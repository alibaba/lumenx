"""Persistence faults must not become successful Playground edits or submissions."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from src.apps.playground.models import PlaygroundGeneration, PlaygroundTemplate
from src.apps.playground import storage as storage_module
from src.apps.playground.storage import PlaygroundStorage
from src.utils import json_storage
from src.utils.json_storage import CorruptStorageError, DirectoryWriterLock, StorageConflictError, StorageError


def generation(gen_id="first", **values):
    return PlaygroundGeneration(
        id=gen_id, mode="t2i", model_id="offline", prompt="Initial prompt",
        created_at="2026-10-09T00:00:00Z", **values,
    )


def template(template_id="first"):
    return PlaygroundTemplate(
        id=template_id, name="Initial name", prompt="Initial prompt",
        created_at="2026-10-09T00:00:00Z", updated_at="2026-10-09T00:00:00Z",
        default_parameters={"nested": {"seed": 1}},
    )


@pytest.mark.parametrize("filename", ["playground_history.json", "playground_templates.json"])
@pytest.mark.parametrize("content", ["{broken", "{}", '[{"id":"invalid"}]'])
def test_corrupt_storage_stops_loading_without_overwriting(tmp_path, filename, content):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    with pytest.raises(CorruptStorageError):
        PlaygroundStorage(tmp_path)
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("kind", ["generation", "template"])
@pytest.mark.parametrize("operation", ["add", "update", "delete"])
def test_failed_mutation_keeps_last_committed_models(tmp_path, monkeypatch, kind, operation):
    storage = PlaygroundStorage(tmp_path)
    original = generation(parameters={"nested": {"seed": 1}}) if kind == "generation" else template()
    getattr(storage, f"add_{kind}")(original)
    path = storage.history_path if kind == "generation" else storage.templates_path
    before = path.read_bytes()

    def fail_save(*args):
        raise RuntimeError("injected storage failure")

    monkeypatch.setattr(storage, "_save_file", fail_save)
    with pytest.raises(RuntimeError, match="injected storage failure"):
        if operation == "add":
            item = generation("second") if kind == "generation" else template("second")
            getattr(storage, f"add_{kind}")(item)
        elif operation == "update":
            item = getattr(storage, f"get_{kind}")("first")
            item.prompt = "Uncommitted change"
            getattr(storage, f"update_{kind}")(item)
        else:
            getattr(storage, f"delete_{kind}")("first")

    assert path.read_bytes() == before
    assert getattr(storage, f"get_{kind}")("first").prompt == "Initial prompt"
    assert getattr(storage, f"get_{kind}")("second") is None


@pytest.mark.parametrize("kind", ["generation", "template"])
def test_models_are_detached_on_input_and_read(tmp_path, kind):
    storage = PlaygroundStorage(tmp_path)
    original = generation(parameters={"nested": {"seed": 1}}) if kind == "generation" else template()
    getattr(storage, f"add_{kind}")(original)
    original.prompt = "Changed by caller after saving"
    loaded = getattr(storage, f"get_{kind}")("first")
    assert loaded.prompt == "Initial prompt"
    params = loaded.parameters if kind == "generation" else loaded.default_parameters
    params["nested"]["seed"] = 99
    loaded.prompt = "Changed without saving"
    listing = storage.list_history() if kind == "generation" else storage.list_templates()
    assert listing[0].prompt == "Initial prompt"
    listed_params = listing[0].parameters if kind == "generation" else listing[0].default_parameters
    assert listed_params["nested"]["seed"] == 1
    listed_params["nested"]["seed"] = 42
    again = getattr(storage, f"get_{kind}")("first")
    assert (again.parameters if kind == "generation" else again.default_parameters)["nested"]["seed"] == 1


def test_serialization_failure_preserves_history_and_valid_file(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    storage.add_generation(generation())
    before = storage.history_path.read_bytes()
    with pytest.raises(RuntimeError):
        storage.add_generation(generation("invalid", parameters={"not_json": object()}))
    assert storage.history_path.read_bytes() == before
    assert storage.get_generation("invalid") is None
    assert [item["id"] for item in json.loads(before)] == ["first"]


def test_missing_files_and_normal_mutations_round_trip(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    assert storage.list_history() == []
    assert storage.list_templates() == []
    assert not storage.history_path.exists()
    storage.add_generation(generation("first"))
    storage.add_generation(generation("second"))
    storage.add_template(template())
    updated = storage.get_generation("first")
    updated.status = "completed"
    storage.update_generation(updated)
    assert [item.id for item in storage.list_history(limit=1)] == ["second"]
    assert [item.id for item in storage.list_history(limit=1, offset=1)] == ["first"]
    assert storage.delete_generation("second")
    assert not storage.delete_generation("missing")
    storage.close()
    reloaded = PlaygroundStorage(tmp_path)
    assert reloaded.get_generation("first").status == "completed"
    assert len(reloaded.list_templates()) == 1


@pytest.mark.parametrize("kind", ["generation", "template"])
def test_replacement_failure_preserves_disk_and_memory_then_allows_retry(tmp_path, monkeypatch, kind):
    storage = PlaygroundStorage(tmp_path)
    getattr(storage, f"add_{kind}")(generation() if kind == "generation" else template())
    path = storage.history_path if kind == "generation" else storage.templates_path
    before = path.read_bytes()
    real_replace = os.replace

    def fail_replace(*args, **kwargs):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(json_storage.os, "replace", fail_replace)
    item = generation("second") if kind == "generation" else template("second")
    with pytest.raises(StorageError, match="injected replacement failure") as error:
        getattr(storage, f"add_{kind}")(item)
    assert not error.value.commit_uncertain
    assert path.read_bytes() == before
    assert getattr(storage, f"get_{kind}")("second") is None
    monkeypatch.setattr(json_storage.os, "replace", real_replace)
    getattr(storage, f"add_{kind}")(item)
    storage.close()
    assert getattr(PlaygroundStorage(tmp_path), f"get_{kind}")("second") is not None


def test_uncertain_commit_blocks_both_stores_until_successful_reload(tmp_path, monkeypatch):
    storage = PlaygroundStorage(tmp_path)
    storage.add_generation(generation())
    real_write = storage_module.atomic_write_json

    def fail_after_replacement(path, payload):
        real_write(path, payload)
        # The primitive's real fsync fault is covered by test_json_storage.
        # Exercise the caller's uncertain outcome on every desktop platform.
        raise StorageError("injected uncertain commit", commit_uncertain=True)

    monkeypatch.setattr(storage_module, "atomic_write_json", fail_after_replacement)
    with pytest.raises(StorageError) as error:
        storage.add_generation(generation("uncertain"))
    assert error.value.commit_uncertain
    assert storage.get_generation("uncertain") is None
    # Replacement succeeded, but durability was not confirmed. No later write
    # may overwrite this file from the previous in-memory snapshot.
    assert [item["id"] for item in json.loads(storage.history_path.read_text())] == ["first", "uncertain"]
    monkeypatch.setattr(storage_module, "atomic_write_json", real_write)
    with pytest.raises(StorageError, match="reload storage or restart"):
        storage.add_generation(generation("third"))
    with pytest.raises(StorageError, match="reload storage or restart"):
        storage.add_template(template())
    assert not storage.templates_path.exists()
    storage.templates_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(CorruptStorageError):
        storage._load()
    with pytest.raises(StorageError, match="reload storage or restart"):
        storage.delete_generation("first")
    storage.templates_path.unlink()
    storage._load()
    assert storage.get_generation("uncertain") is not None
    storage.add_template(template())
    storage.close()
    assert len(PlaygroundStorage(tmp_path).list_templates()) == 1


def test_reload_failure_does_not_replace_part_of_in_memory_state(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    storage.add_generation(generation())
    storage.add_template(template())
    storage.history_path.write_text("[]", encoding="utf-8")
    storage.templates_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(StorageError):
        storage._load()
    assert storage.get_generation("first") is not None
    assert storage.get_template("first") is not None


def test_concurrent_edits_preserve_every_committed_record(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda index: storage.add_generation(generation(str(index))), range(20)))
    assert {item.id for item in storage.list_history()} == {str(index) for index in range(20)}
    storage.close()
    assert {item.id for item in PlaygroundStorage(tmp_path).list_history()} == {str(index) for index in range(20)}


def test_second_standalone_writer_is_refused_before_loading(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    storage.history_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(StorageConflictError):
        PlaygroundStorage(tmp_path)


def test_close_is_idempotent_blocks_writes_and_releases_owned_lock(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    storage.add_generation(generation())
    storage.close()
    storage.close()
    with pytest.raises(StorageError, match="closed"):
        storage.add_generation(generation("late"))
    with pytest.raises(StorageError, match="closed"):
        storage.require_writable()
    reloaded = PlaygroundStorage(tmp_path)
    assert reloaded.get_generation("first") is not None
    assert reloaded.get_generation("late") is None
    reloaded.close()


def test_constructor_failure_releases_owned_lock(tmp_path):
    path = tmp_path / "playground_history.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(CorruptStorageError):
        PlaygroundStorage(tmp_path)
    path.write_text("[]", encoding="utf-8")
    with DirectoryWriterLock(tmp_path):
        pass


def test_shared_lock_remains_owned_after_storage_close_and_load_failure(tmp_path):
    owner = DirectoryWriterLock(tmp_path).acquire()
    try:
        storage = PlaygroundStorage(tmp_path, writer_lock=owner)
        storage.add_generation(generation())
        storage.close()
        with pytest.raises(StorageConflictError):
            DirectoryWriterLock(tmp_path).acquire()
        storage.history_path.write_text("{broken", encoding="utf-8")
        with pytest.raises(CorruptStorageError):
            PlaygroundStorage(tmp_path, writer_lock=owner)
        with pytest.raises(StorageConflictError):
            DirectoryWriterLock(tmp_path).acquire()
    finally:
        owner.close()


def test_shared_lock_requires_matching_directory_and_active_ownership(tmp_path):
    owner = DirectoryWriterLock(tmp_path / "owner").acquire()
    try:
        with pytest.raises(StorageConflictError):
            PlaygroundStorage(tmp_path / "other", writer_lock=owner)
        assert not (tmp_path / "other").exists()
        storage = PlaygroundStorage(tmp_path / "owner", writer_lock=owner)
        owner.close()
        with pytest.raises(StorageConflictError):
            storage.add_generation(generation())
        with pytest.raises(StorageConflictError):
            PlaygroundStorage(tmp_path / "owner", writer_lock=owner)
    finally:
        owner.close()


def test_runtime_failure_blocks_playground_writes(tmp_path):
    failed = False

    def check_runtime():
        if failed:
            raise StorageError("runtime requires restart")

    storage = PlaygroundStorage(tmp_path, runtime_check=check_runtime)
    storage.add_generation(generation())
    failed = True
    with pytest.raises(StorageError, match="runtime requires restart"):
        storage.add_template(template())
    with pytest.raises(StorageError, match="runtime requires restart"):
        storage.require_writable()
    assert not storage.templates_path.exists()
    storage.close()


def test_close_waits_for_in_flight_storage_write_before_releasing_lease(tmp_path, monkeypatch):
    storage = PlaygroundStorage(tmp_path)
    writing, finish_write, closing, closed = Event(), Event(), Event(), Event()
    real_write = storage_module.atomic_write_json

    def slow_write(path, payload):
        writing.set()
        assert finish_write.wait(5)
        real_write(path, payload)

    def close_storage():
        closing.set()
        storage.close()
        closed.set()

    monkeypatch.setattr(storage_module, "atomic_write_json", slow_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        save = executor.submit(storage.add_generation, generation())
        assert writing.wait(5)
        close = executor.submit(close_storage)
        assert closing.wait(5)
        try:
            assert not closed.wait(0.05)
            with pytest.raises(StorageConflictError):
                DirectoryWriterLock(tmp_path).acquire()
        finally:
            finish_write.set()
        save.result(timeout=5)
        close.result(timeout=5)
    with DirectoryWriterLock(tmp_path):
        pass


def test_template_update_missing_after_read_is_not_successful_ack(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    storage.add_template(template())
    stale = storage.get_template("first")
    assert storage.delete_template("first")
    stale.prompt = "stale write"

    with pytest.raises(StorageConflictError):
        storage.update_template(stale)

    assert storage.get_template("first") is None
    storage.add_template(template("second"))
    assert storage.get_template("second") is not None


def test_template_http_update_deleted_during_write_returns_conflict(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.apps.comic_gen import api

    app = api.create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post('/playground/templates', json={'name': 'draft', 'prompt': 'old'})
        assert created.status_code == 200, created.text
        template_id = created.json()['id']
        storage = app.state.playground_storage
        real_update = storage.update_template

        def delete_before_update(template):
            assert storage.delete_template(template.id)
            real_update(template)

        monkeypatch.setattr(storage, 'update_template', delete_before_update)
        response = client.put(f'/playground/templates/{template_id}', json={'prompt': 'new'})

        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'storage_conflict'
        assert all(item['id'] != template_id for item in client.get('/playground/templates').json())
