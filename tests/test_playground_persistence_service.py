"""Offline provider and library faults at Playground's persistence boundary."""

from pathlib import Path
import base64
import hashlib
from types import SimpleNamespace
import os
import stat

import pytest

from src.apps.playground.models import GenerateRequest, PlaygroundGeneration, PlaygroundOutput
from src.apps.playground.service import PlaygroundService
from src.models.provider_media_execution import PrivateSyncArtifact
from tests.test_playground_core_media_execution import _authority_provider, _authority_resolver
from src.apps.playground.storage import PlaygroundStorage
from src.utils.json_storage import StorageConflictError, StorageError, atomic_write_json, read_json


PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=")
# Generic storage faults use the unversioned image contract.
CORE_IMAGE_MODEL = "qwen-image-2.0-pro"


def _is_image_mode(value) -> bool:
    return value == "t2i" or getattr(value, "value", None) == "t2i"


def _find_generation_for_attempt(storage, attempt_id):
    for gen in storage.list_history(limit=500):
        for attempt in gen.attempts:
            if attempt.attempt_id == attempt_id:
                return gen, attempt.ordinal
    raise AssertionError(f"generation for attempt {attempt_id} not found")


class CoreImageFixture:
    def __init__(self, storage, root):
        self.storage = storage
        self.root = root
        self.delegate = self.default_delegate

    def default_delegate(self, _gen, output_path, _ordinal):
        Path(output_path).write_bytes(PNG_1X1)

    def execute(self, record, _authority, *, scratch_dir, **_kwargs):
        gen, ordinal = _find_generation_for_attempt(self.storage, record.attempt_id)
        paid_path = self.root / "provider-paid" / f"{record.attempt_id}.png"
        paid_path.parent.mkdir(parents=True, exist_ok=True)
        self.delegate(gen, str(paid_path), ordinal)
        private_path = Path(scratch_dir) / f"{record.attempt_id}.png"
        private_path.write_bytes(PNG_1X1)
        digest = hashlib.sha256(PNG_1X1).hexdigest()
        return PrivateSyncArtifact(
            path=private_path,
            sha256=digest,
            size_bytes=len(PNG_1X1),
            encoding="image/png",
            media_kind="image",
        )


def _install_core_image_fixture(service, storage, root):
    fixture = CoreImageFixture(storage, root)
    service._core_media_executor = fixture.execute
    service._test_core_image_fixture = fixture


def _set_provider_delegate(monkeypatch, service, delegate, fake_generate):
    if delegate == "_test_core_image_delegate":
        fixture = service._test_core_image_fixture
        monkeypatch.setattr(fixture, "delegate", fake_generate)
        return
    monkeypatch.setattr(service, delegate, fake_generate)


def make_runtime(tmp_path, **kwargs):
    storage = PlaygroundStorage(tmp_path)
    service = PlaygroundService(
        storage,
        data_dir=tmp_path,
        media_authority_provider=_authority_provider,
        media_authority_resolver=_authority_resolver,
        **kwargs,
    )
    _install_core_image_fixture(service, storage, tmp_path)
    return storage, service


def generate_request(request_id="request-1", **values):
    if _is_image_mode(values.get("mode")):
        values["model_id"] = CORE_IMAGE_MODEL
        values.setdefault("parameters", {"size": "1280*1280", "seed": 0, "prompt_extend": False, "watermark": False})
    return GenerateRequest(client_request_id=request_id, **values)


def add_completed(storage, root, gen_id="generation", content=b"first", status="completed"):
    source = root / "playground" / gen_id / "same.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    gen = PlaygroundGeneration(
        id=gen_id, mode="t2i", model_id="fake", prompt="Original", status=status,
        created_at="2026-10-09T00:00:00Z",
        outputs=[PlaygroundOutput(id="output", media_path=source.relative_to(root).as_posix(), media_type="image")],
    )
    storage.add_generation(gen)
    return gen


class PersistentLibraryWriter:
    """Minimal durable stand-in for the library's source-key contract."""

    def __init__(self, root):
        self.path = root / "fake-library.json"
        self.calls = []

    def __call__(self, asset_type, payload, *, source_key):
        self.calls.append((asset_type, payload, source_key))
        records = read_json(self.path, dict)
        if source_key not in records:
            records[source_key] = {"id": f"asset-{len(records)}", "asset_type": asset_type, **payload}
            atomic_write_json(self.path, records)
        return SimpleNamespace(**records[source_key])


@pytest.mark.parametrize("mode,delegate", [("t2i", "_test_core_image_delegate"), ("t2v", "_generate_video_wanx")])
@pytest.mark.parametrize("failure_point", ["processing", "result", "delegate"])
def test_storage_fault_stops_before_any_next_provider_call(tmp_path, monkeypatch, mode, delegate, failure_point):
    storage, service = make_runtime(tmp_path)
    gen = service.create_generation(generate_request(f"request-{mode}-{failure_point}", mode=mode, model_id="fake", prompt="Test", batch_size=3))
    calls = []
    real_save = storage._save_file

    def fake_generate(gen, output_path, *args):
        calls.append(output_path)
        if failure_point == "delegate":
            raise StorageError("injected provider persistence failure")
        Path(output_path).write_bytes(b"already generated media")

    def fail_save(path, items):
        if failure_point == "processing" or (failure_point == "result" and items[0].outputs):
            raise StorageError("injected history failure")
        real_save(path, items)

    _set_provider_delegate(monkeypatch, service, delegate, fake_generate)
    monkeypatch.setattr(storage, "_save_file", fail_save)
    with pytest.raises(StorageError):
        service.process_generation(gen.id)
    assert len(calls) == (0 if failure_point == "processing" else 1)
    assert storage.get_generation(gen.id).status != "completed"
    if failure_point == "result":
        assert Path(calls[0]).read_bytes() == b"already generated media"
        assert storage.get_generation(gen.id).outputs == []


def test_background_snapshot_preserves_user_flags_new_outputs_and_frozen_request(tmp_path):
    storage, _ = make_runtime(tmp_path)
    add_completed(storage, tmp_path)
    worker = storage.get_generation("generation")
    user = storage.get_generation("generation")
    user.outputs[0].saved_to_library = True
    storage.update_generation(user)
    worker.prompt = "Stale changed request"
    worker.status = "completed"
    worker.outputs.append(PlaygroundOutput(id="second", media_path="playground/second.png", media_type="image"))
    storage.update_generation(worker)
    latest = storage.get_generation("generation")
    assert latest.prompt == "Original"
    assert [item.id for item in latest.outputs] == ["output", "second"]
    assert latest.outputs[0].saved_to_library
    # A second old snapshot must not erase the already committed second output.
    storage.update_generation(user)
    assert [item.id for item in storage.get_generation("generation").outputs] == ["output", "second"]


def test_deleted_generation_cannot_be_resurrected_by_late_worker(tmp_path):
    storage, _ = make_runtime(tmp_path)
    add_completed(storage, tmp_path)
    old = storage.get_generation("generation")
    storage.delete_generation(old.id)
    with pytest.raises(StorageConflictError):
        storage.update_generation(old)
    assert storage.list_history() == []


def test_library_commit_then_history_failure_is_idempotent_across_reload_and_category(tmp_path, monkeypatch):
    writer = PersistentLibraryWriter(tmp_path)
    storage, service = make_runtime(tmp_path, library_writer=writer)
    add_completed(storage, tmp_path)

    def fail_save(*args):
        raise StorageError("injected history save failure")

    monkeypatch.setattr(storage, "_save_file", fail_save)
    with pytest.raises(StorageError, match="history save failure"):
        service.save_to_library("generation", "output", "character")
    records = read_json(writer.path, dict)
    assert list(records) == ["playground:generation:output"]
    assert not storage.get_generation("generation").outputs[0].saved_to_library
    storage.close()
    reloaded_storage, reloaded_service = make_runtime(tmp_path, library_writer=writer)
    assert reloaded_service.save_to_library("generation", "output", "scene")
    assert read_json(writer.path, dict) == records
    assert reloaded_storage.get_generation("generation").outputs[0].saved_to_library
    assert reloaded_service.save_to_library("generation", "output", "prop")
    assert len(writer.calls) == 2


def test_library_failure_is_not_reported_as_missing_output(tmp_path):
    def fail_writer(*args, **kwargs):
        raise StorageError("injected library failure")

    storage, service = make_runtime(tmp_path, library_writer=fail_writer)
    add_completed(storage, tmp_path)
    with pytest.raises(StorageError, match="library failure"):
        service.save_to_library("generation", "output")
    assert not storage.get_generation("generation").outputs[0].saved_to_library


def test_distinct_outputs_with_same_basename_keep_distinct_library_media(tmp_path):
    writer = PersistentLibraryWriter(tmp_path)
    storage, service = make_runtime(tmp_path, library_writer=writer)
    add_completed(storage, tmp_path, "first", b"first")
    add_completed(storage, tmp_path, "second", b"second")
    assert service.save_to_library("first", "output", "props")
    assert service.save_to_library("second", "output", "props")
    first, second = read_json(writer.path, dict).values()
    assert first["image_url"] != second["image_url"]
    assert (tmp_path / first["image_url"]).read_bytes() == b"first"
    assert (tmp_path / second["image_url"]).read_bytes() == b"second"


@pytest.mark.parametrize("mode,delegate", [("t2i", "_test_core_image_delegate"), ("t2v", "_generate_video_wanx")])
def test_provider_preflight_checks_storage_health(tmp_path, monkeypatch, mode, delegate):
    storage, service = make_runtime(tmp_path)
    gen = service.create_generation(generate_request(f"request-preflight-{mode}", mode=mode, model_id="fake", prompt="Test"))
    calls = []
    _set_provider_delegate(monkeypatch, service, delegate, lambda *args: calls.append(args))

    def unavailable():
        raise StorageError("storage became unavailable")

    monkeypatch.setattr(storage, "require_writable", unavailable)
    with pytest.raises(StorageError, match="became unavailable"):
        service.process_generation(gen.id)
    assert calls == []


@pytest.mark.parametrize("mode,delegate", [("t2i", "_test_core_image_delegate"), ("t2v", "_generate_video_wanx")])
def test_shutdown_during_generation_prevents_next_batch_call(tmp_path, monkeypatch, mode, delegate):
    storage, service = make_runtime(tmp_path)
    gen = service.create_generation(generate_request(f"request-shutdown-{mode}", mode=mode, model_id="fake", prompt="Test", batch_size=3))
    calls = []

    def finish_during_shutdown(gen, output_path, *args):
        calls.append(output_path)
        Path(output_path).write_bytes(b"completed before shutdown")
        storage.close()

    _set_provider_delegate(monkeypatch, service, delegate, finish_during_shutdown)
    with pytest.raises(StorageError, match="closed"):
        service.process_generation(gen.id)
    assert len(calls) == 1
    assert Path(calls[0]).read_bytes() == b"completed before shutdown"
    assert storage.get_generation(gen.id).status == "processing"


def test_library_flag_does_not_overwrite_progress_committed_during_library_write(tmp_path):
    storage, service = make_runtime(tmp_path)
    add_completed(storage, tmp_path, status="processing")

    def writer(asset_type, payload, *, source_key):
        progress = storage.get_generation("generation")
        progress.status = "processing"
        progress.outputs.append(PlaygroundOutput(id="new", media_path="playground/new.png", media_type="image"))
        storage.update_generation(progress)
        return SimpleNamespace(id="library-asset")

    service.library_writer = writer
    assert service.save_to_library("generation", "output")
    latest = storage.get_generation("generation")
    assert latest.status == "processing"
    assert [item.id for item in latest.outputs] == ["output", "new"]
    assert latest.outputs[0].saved_to_library


@pytest.mark.parametrize("failure_point", ["mkdir", "copy"])
def test_copy_failure_preserves_source_and_leaves_no_library_record(tmp_path, monkeypatch, failure_point):
    writer = PersistentLibraryWriter(tmp_path)
    storage, service = make_runtime(tmp_path, library_writer=writer)
    gen = add_completed(storage, tmp_path)

    def partial_copy(source, destination):
        Path(destination).write_bytes(b"partial")
        raise OSError("injected copy failure")

    if failure_point == "copy":
        monkeypatch.setattr("src.apps.playground.service.shutil.copy2", partial_copy)
    else:
        def no_directory(*args, **kwargs):
            raise OSError("injected copy failure")
        monkeypatch.setattr("src.apps.playground.service.os.makedirs", no_directory)
    with pytest.raises(StorageError, match="injected copy failure"):
        service.save_to_library("generation", "output")
    assert writer.calls == []
    assert (tmp_path / gen.outputs[0].media_path).read_bytes() == b"first"
    destination = tmp_path / "assets" / "general"
    assert not destination.exists() or list(destination.iterdir()) == []
    assert not storage.get_generation("generation").outputs[0].saved_to_library


def observe_directory_sync(monkeypatch, *, fail_path=None):
    opened, synced = {}, []
    real_open, real_fsync = os.open, os.fsync

    def record_open(path, *args, **kwargs):
        descriptor = real_open(path, *args, **kwargs)
        opened[descriptor] = Path(path).resolve()
        return descriptor

    def record_sync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            path = opened[descriptor]
            synced.append(path)
            if path == fail_path:
                raise OSError("injected directory sync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "open", record_open)
    monkeypatch.setattr(os, "fsync", record_sync)
    return synced


@pytest.mark.skipif(os.name == "nt", reason="Windows has no stdlib directory fsync")
def test_library_media_syncs_new_ancestors_before_registering(tmp_path, monkeypatch):
    storage, service = make_runtime(tmp_path)
    add_completed(storage, tmp_path)
    synced = observe_directory_sync(monkeypatch)
    before_registration = []

    def writer(*args, **kwargs):
        before_registration.extend(synced)
        return SimpleNamespace(id="asset")

    service.library_writer = writer
    assert service.save_to_library("generation", "output", "nested/category")
    assert before_registration == [
        tmp_path / "assets/nested/category",
        tmp_path / "assets/nested",
        tmp_path / "assets",
        tmp_path,
    ]


@pytest.mark.skipif(os.name == "nt", reason="Windows has no stdlib directory fsync")
@pytest.mark.parametrize("relative_directory", ["assets/nested/category", "assets/nested", "assets", "."])
def test_directory_sync_failure_prevents_library_and_flag_commit(tmp_path, monkeypatch, relative_directory):
    writer = PersistentLibraryWriter(tmp_path)
    storage, service = make_runtime(tmp_path, library_writer=writer)
    gen = add_completed(storage, tmp_path)
    flag_calls = []
    monkeypatch.setattr(storage, "mark_output_saved", lambda *args: flag_calls.append(args))
    observe_directory_sync(monkeypatch, fail_path=(tmp_path / relative_directory).resolve())
    with pytest.raises(StorageError, match="directory sync failure"):
        service.save_to_library("generation", "output", "nested/category")
    assert writer.calls == []
    assert flag_calls == []
    assert not writer.path.exists()
    assert not storage.get_generation("generation").outputs[0].saved_to_library
    assert (tmp_path / gen.outputs[0].media_path).read_bytes() == b"first"


def test_duplicate_processing_claim_does_not_repeat_paid_provider(tmp_path, monkeypatch):
    storage, service = make_runtime(tmp_path)
    gen = service.create_generation(generate_request("request-duplicate", mode="t2i", model_id="fake", prompt="Test"))
    calls = []

    def fake_generate(gen, output_path, *args):
        calls.append(output_path)
        Path(output_path).write_bytes(b"paid-result")

    _set_provider_delegate(monkeypatch, service, "_test_core_image_delegate", fake_generate)

    service.process_generation(gen.id)
    service.process_generation(gen.id)

    saved = storage.get_generation(gen.id)
    assert saved.status == "completed"
    assert len(calls) == 1
    assert len(saved.outputs) == 1
    assert Path(calls[0]).read_bytes() == b"paid-result"


def test_late_playground_worker_cannot_finish_deleted_generation(tmp_path, monkeypatch):
    storage, service = make_runtime(tmp_path)
    gen = service.create_generation(generate_request("request-delete-late", mode="t2i", model_id="fake", prompt="Test"))
    calls = []

    def delete_during_provider(gen, output_path, *args):
        calls.append(output_path)
        Path(output_path).write_bytes(b"paid-result")
        assert storage.delete_generation(gen.id)

    _set_provider_delegate(monkeypatch, service, "_test_core_image_delegate", delete_during_provider)

    with pytest.raises(StorageConflictError):
        service.process_generation(gen.id)

    assert len(calls) == 1
    assert storage.get_generation(gen.id) is None
    service.process_generation(gen.id)
    assert len(calls) == 1


def test_late_playground_failure_does_not_overwrite_terminal_generation(tmp_path, monkeypatch):
    storage, service = make_runtime(tmp_path)
    gen = service.create_generation(generate_request("request-terminal-late", mode="t2i", model_id="fake", prompt="Test"))

    def complete_elsewhere(gen, output_path, *args):
        current = storage.get_generation(gen.id)
        current.status = "completed"
        current.outputs.append(PlaygroundOutput(id="winner", media_path="playground/winner.png", media_type="image"))
        storage.update_generation(current)
        raise RuntimeError("late worker failed")

    _set_provider_delegate(monkeypatch, service, "_test_core_image_delegate", complete_elsewhere)

    service.process_generation(gen.id)

    saved = storage.get_generation(gen.id)
    assert saved.status == "completed"
    assert saved.error is None
    assert [output.id for output in saved.outputs] == ["winner"]


@pytest.mark.parametrize("mode,delegate,extension", [("t2i", "_test_core_image_delegate", "png"), ("t2v", "_generate_video_wanx", "mp4")])
@pytest.mark.parametrize("mutation", ["delete", "terminal"])
def test_batch_rechecks_claim_before_each_paid_item(tmp_path, monkeypatch, mode, delegate, extension, mutation):
    storage, service = make_runtime(tmp_path)
    gen = service.create_generation(generate_request(f"request-batch-{mode}-{mutation}", mode=mode, model_id="fake", prompt="Test", batch_size=2))
    calls = []

    def mutate_then_fail(gen, output_path, *args):
        calls.append(Path(output_path).name)
        if len(calls) == 1:
            if mutation == "delete":
                assert storage.delete_generation(gen.id)
            else:
                current = storage.get_generation(gen.id)
                current.status = "completed"
                current.outputs.append(PlaygroundOutput(id="winner", media_path=f"playground/winner.{extension}", media_type="image" if mode == "t2i" else "video"))
                storage.update_generation(current)
            raise RuntimeError("first item failed after claim changed")
        Path(output_path).write_bytes(b"second-paid")

    _set_provider_delegate(monkeypatch, service, delegate, mutate_then_fail)

    if mutation == "delete" or (mode == "t2i" and mutation == "terminal"):
        with pytest.raises(StorageConflictError):
            service.process_generation(gen.id)
    else:
        service.process_generation(gen.id)

    expected_name = f"{mode}_{gen.id}_0.{extension}"
    if mode == "t2i":
        expected_name = f"request-batch-{mode}-{mutation}-0.{extension}"
    assert calls == [expected_name]
    saved = storage.get_generation(gen.id)
    if mutation == "delete":
        assert saved is None
    else:
        assert saved.status == "completed"
        assert [output.id for output in saved.outputs] == ["winner"]
