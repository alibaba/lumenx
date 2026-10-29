"""Client retries reuse durable admission identity without new paid work."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.models import StoryboardFrame
from src.apps.comic_gen.provider_recovery import StudioProviderRecoveryRuntime
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_studio_provider_recovery import FakeProvider, SECRET, SCOPE, _admission_request
from tests.test_studio_provider_recovery_dispatcher import _make_runtime


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    item = _make_runtime(tmp_path, monkeypatch, FakeProvider())
    yield item
    item.close()


def admit(pipeline, request=None, client_id="request-1"):
    return pipeline.admit_provider_video_task_with_receipt(
        "p", _admission_request() if request is None else request,
        client_request_id=client_id,
    )


def test_replay_returns_original_identity_without_dispatch_or_current_runtime(pipeline, monkeypatch):
    first = admit(pipeline)
    assert not first.replayed and first.new_task_ids == (first.task_id,)
    original_bytes = (pipeline.data_dir / "projects.json").read_bytes()
    pipeline.provider_recovery_runtime = None
    monkeypatch.setattr(module.provider_recovery, "build_accepted_submission_and_record", Mock(side_effect=AssertionError("replay re-normalized")))
    (pipeline.data_dir / "uploads/source.png").unlink()

    again = admit(pipeline)

    assert again.replayed and again.new_task_ids == ()
    assert (again.task_id, again.attempt_id, again.execution_fingerprint) == (
        first.task_id, first.attempt_id, first.execution_fingerprint,
    )
    assert len(again.script.video_tasks) == len(again.script.provider_request_receipts) == 1
    assert (pipeline.data_dir / "projects.json").read_bytes() == original_bytes


@pytest.mark.parametrize("patch", [
    {"prompt": "different shot"}, {"seed": False}, {"seed": None},
    {"frame_id": "other-frame"}, {"model": "wan2.6-i2v"},
])
def test_same_request_id_changed_payload_conflicts_before_runtime(pipeline, patch):
    admit(pipeline)
    pipeline.provider_recovery_runtime = None
    with pytest.raises(StorageConflictError, match="provider_request_conflict"):
        admit(pipeline, _admission_request(**patch))
    assert len(pipeline.scripts["p"].video_tasks) == 1


def test_omitted_value_replays_without_current_catalog_default(pipeline, monkeypatch):
    request = _admission_request()
    request.pop("duration")
    first = admit(pipeline, request)
    monkeypatch.setattr(module.provider_recovery, "normalize_generation_intent", Mock(side_effect=AssertionError("new defaults")))
    again = admit(pipeline, request)
    assert again.task_id == first.task_id and again.new_task_ids == ()
    with pytest.raises(StorageConflictError, match="provider_request_conflict"):
        admit(pipeline, {**request, "duration": first.script.video_tasks[0].duration})


def test_deleted_task_remains_receipt_tombstone_and_new_id_can_admit(pipeline):
    original = admit(pipeline)
    pipeline.scripts["p"].video_tasks.clear()
    pipeline._commit_storage("projects.json")
    replay = admit(pipeline)
    assert replay.task_id == original.task_id and replay.replayed
    assert replay.reason == "provider_request_deleted" and replay.new_task_ids == ()
    assert pipeline.scripts["p"].video_tasks == []
    fresh = admit(pipeline, client_id="request-2")
    assert fresh.task_id != original.task_id and not fresh.replayed
    assert len(pipeline.scripts["p"].provider_request_receipts) == 2


def test_deleted_frame_replays_without_recreating_owner(pipeline):
    original = admit(pipeline)
    pipeline.delete_frame("p", "frame")
    replay = admit(pipeline)
    assert replay.task_id == original.task_id and replay.new_task_ids == ()
    assert replay.reason == "provider_request_owner_deleted"


def test_storage_failure_commits_neither_task_nor_receipt(pipeline, monkeypatch):
    before = (pipeline.data_dir / "projects.json").read_bytes()
    provider = pipeline.provider_recovery_runtime.provider
    monkeypatch.setattr(pipeline._storage, "commit", Mock(side_effect=StorageError("disk unavailable")))
    with pytest.raises(StorageError):
        admit(pipeline)
    assert (pipeline.data_dir / "projects.json").read_bytes() == before
    assert pipeline.scripts["p"].video_tasks == []
    assert pipeline.scripts["p"].provider_request_receipts == []
    assert provider.submits == []


def test_receipt_replays_after_restart_without_secret_or_media(pipeline):
    first = admit(pipeline)
    pipeline.close()
    restarted = module.ComicGenPipeline(data_dir=pipeline.data_dir, script_processor=Mock(), warmup_demucs=False)
    try:
        assert restarted.provider_recovery_runtime is None
        replay = admit(restarted)
        assert replay.replayed and replay.new_task_ids == ()
        assert replay.task_id == first.task_id
    finally:
        restarted.close()


def test_concurrent_same_request_has_one_receipt_and_one_dispatchable_result(pipeline):
    barrier = Barrier(2)
    original_provider = pipeline.provider_recovery_runtime.provider

    def resolve():
        barrier.wait(timeout=3)
        return SECRET

    pipeline.provider_recovery_runtime = StudioProviderRecoveryRuntime(
        scope=SCOPE, secret_resolver=resolve, provider=original_provider,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(admit, pipeline)
        b = pool.submit(admit, pipeline)
        results = [a.result(timeout=5), b.result(timeout=5)]
    assert len({result.task_id for result in results}) == 1
    assert sorted(result.replayed for result in results) == [False, True]
    assert sum(len(result.new_task_ids) for result in results) == 1
    assert len(pipeline.scripts["p"].video_tasks) == 1
    assert len(pipeline.scripts["p"].provider_request_receipts) == 1
    assert original_provider.submits == []


def test_copy_frame_preserves_only_original_request_authority(pipeline):
    first = admit(pipeline)
    task = pipeline.scripts["p"].video_tasks[0]
    task.status = "completed"
    task.video_url = "video/completed.mp4"
    pipeline._commit_storage("projects.json")
    copied = pipeline.copy_frame("p", "frame")
    assert len(copied.provider_request_receipts) == 1
    assert copied.provider_request_receipts[0].task_id == first.task_id
    clone = next(task for task in copied.video_tasks if task.id != first.task_id)
    assert clone.provider_execution is None
    assert clone.frame_id != "frame"


@pytest.mark.parametrize("client_id", [None, "", 1, True, "a" * 129, "request\nspoof"])
def test_invalid_client_id_fails_before_credentials(pipeline, client_id):
    pipeline.provider_recovery_runtime = None
    with pytest.raises(ValueError):
        admit(pipeline, client_id=client_id)
    assert pipeline.scripts["p"].video_tasks == []


def test_same_id_changed_frame_is_conflict_even_when_new_frame_exists(pipeline):
    admit(pipeline)
    pipeline.scripts["p"].frames.append(StoryboardFrame(id="other", scene_id="scene"))
    pipeline._commit_storage("projects.json")
    with pytest.raises(StorageConflictError, match="provider_request_conflict"):
        admit(pipeline, _admission_request(frame_id="other"))


def test_result_is_detached_from_persisted_receipt(pipeline):
    first = admit(pipeline)
    first.script.provider_request_receipts.clear()
    first.script.video_tasks.clear()
    again = admit(pipeline)
    assert again.replayed and len(again.script.video_tasks) == 1
