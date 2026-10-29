"""Batch provider request receipts are one atomic product admission."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from src.apps.comic_gen import pipeline as module
from src.apps.comic_gen.provider_recovery import StudioProviderRecoveryRuntime
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_studio_provider_recovery import FakeProvider, SECRET, SCOPE, _admission_request
from tests.test_studio_provider_recovery_dispatcher import _make_runtime


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    item = _make_runtime(tmp_path, monkeypatch, FakeProvider())
    yield item
    item.close()


def admit_batch(pipeline, request=None, client_id="batch-1", batch_size=3):
    return pipeline.admit_provider_video_tasks_with_receipt(
        "p",
        _admission_request() if request is None else request,
        client_request_id=client_id,
        batch_size=batch_size,
    )


def test_batch_admission_commits_all_tasks_and_one_typed_receipt(pipeline):
    result = admit_batch(pipeline, batch_size=3)

    assert not result.replayed
    assert result.task_ids == result.new_task_ids
    assert len(result.task_ids) == 3
    assert tuple(member.task_id for member in result.members) == result.task_ids
    script = pipeline.scripts["p"]
    assert len(script.video_tasks) == 3
    assert len(script.provider_request_receipts) == 1
    receipt = script.provider_request_receipts[0]
    assert receipt.schema_version == 2
    assert not hasattr(receipt, "task_id")
    assert len(receipt.members) == 3
    assert [member.task_id for member in receipt.members] == list(result.task_ids)
    assert len({task.provider_execution.attempt_id for task in script.video_tasks}) == 3


def test_batch_replay_returns_original_members_without_runtime_or_media(pipeline, monkeypatch):
    first = admit_batch(pipeline, batch_size=2)
    original_bytes = (pipeline.data_dir / "projects.json").read_bytes()
    pipeline.provider_recovery_runtime = None
    monkeypatch.setattr(module.provider_recovery, "build_accepted_submission_and_record", Mock(side_effect=AssertionError("replay rebuilt")))
    (pipeline.data_dir / "uploads/source.png").unlink()

    again = admit_batch(pipeline, batch_size=2)

    assert again.replayed
    assert again.task_ids == first.task_ids
    assert again.members == first.members
    assert again.new_task_ids == ()
    assert (pipeline.data_dir / "projects.json").read_bytes() == original_bytes


@pytest.mark.parametrize("patch", [
    {"prompt": "changed"}, {"frame_id": "other-frame"}, {"model": "wan2.6-i2v"}, {"image_url": "uploads/other.png"},
])
def test_batch_same_id_changed_payload_conflicts_before_runtime(pipeline, patch):
    admit_batch(pipeline, batch_size=2)
    pipeline.provider_recovery_runtime = None
    with pytest.raises(StorageConflictError, match="provider_request_conflict"):
        admit_batch(pipeline, _admission_request(**patch), batch_size=2)
    assert len(pipeline.scripts["p"].video_tasks) == 2


def test_batch_size_participates_in_fingerprint(pipeline):
    admit_batch(pipeline, batch_size=2)
    pipeline.provider_recovery_runtime = None
    with pytest.raises(StorageConflictError, match="provider_request_conflict"):
        admit_batch(pipeline, batch_size=3)


def test_single_and_batch_same_id_conflict(pipeline):
    single = pipeline.admit_provider_video_task_with_receipt(
        "p", _admission_request(), client_request_id="shared-id",
    )
    assert single.new_task_ids == (single.task_id,)
    pipeline.provider_recovery_runtime = None
    with pytest.raises(StorageConflictError, match="provider_request_conflict"):
        admit_batch(pipeline, client_id="shared-id", batch_size=2)


def test_batch_commit_failure_publishes_neither_task_nor_receipt(pipeline, monkeypatch):
    before = (pipeline.data_dir / "projects.json").read_bytes()
    monkeypatch.setattr(pipeline._storage, "commit", Mock(side_effect=StorageError("disk unavailable")))

    with pytest.raises(StorageError):
        admit_batch(pipeline, batch_size=3)

    assert (pipeline.data_dir / "projects.json").read_bytes() == before
    assert pipeline.scripts["p"].video_tasks == []
    assert pipeline.scripts["p"].provider_request_receipts == []


@pytest.mark.parametrize("failed_member", [2, 3])
def test_batch_build_failure_on_nth_member_commits_zero_tasks(pipeline, monkeypatch, failed_member):
    # The first member freezes the input. Later members derive their own records
    # from that one snapshot; inject there instead of expecting source rereads.
    member = 1
    original = module._build_accepted_provider_record
    before = (pipeline.data_dir / "projects.json").read_bytes()

    def fail_member(*args, **kwargs):
        nonlocal member
        member += 1
        if member == failed_member:
            raise module.provider_recovery.StudioProviderRecoveryError("bad_member")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_build_accepted_provider_record", fail_member)

    with pytest.raises(module.provider_recovery.StudioProviderRecoveryError, match="bad_member"):
        admit_batch(pipeline, batch_size=3)

    assert member == failed_member
    assert (pipeline.data_dir / "projects.json").read_bytes() == before
    assert pipeline.scripts["p"].video_tasks == []
    assert pipeline.scripts["p"].provider_request_receipts == []


def test_concurrent_same_batch_id_has_one_dispatchable_result(pipeline):
    barrier = Barrier(2)
    provider = pipeline.provider_recovery_runtime.provider

    def resolve():
        barrier.wait(timeout=3)
        return SECRET

    pipeline.provider_recovery_runtime = StudioProviderRecoveryRuntime(scope=SCOPE, secret_resolver=resolve, provider=provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(admit_batch, pipeline)
        b = pool.submit(admit_batch, pipeline)
        results = [a.result(timeout=5), b.result(timeout=5)]

    assert len({result.task_ids for result in results}) == 1
    assert sorted(result.replayed for result in results) == [False, True]
    assert sorted(len(result.new_task_ids) for result in results) == [0, 3]
    assert len(pipeline.scripts["p"].video_tasks) == 3
    assert len(pipeline.scripts["p"].provider_request_receipts) == 1


def test_concurrent_same_batch_id_different_payload_conflicts(pipeline):
    barrier = Barrier(2)
    provider = pipeline.provider_recovery_runtime.provider

    def resolve():
        barrier.wait(timeout=3)
        return SECRET

    pipeline.provider_recovery_runtime = StudioProviderRecoveryRuntime(scope=SCOPE, secret_resolver=resolve, provider=provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(admit_batch, pipeline, _admission_request(prompt="a"))
        b = pool.submit(admit_batch, pipeline, _admission_request(prompt="b"))
        outcomes = []
        for item in (a, b):
            try:
                outcomes.append(item.result(timeout=5))
            except StorageConflictError as exc:
                outcomes.append(exc)

    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, StorageConflictError) for item in outcomes) == 1
    assert len(pipeline.scripts["p"].provider_request_receipts) == 1


def test_batch_receipt_replays_after_restart_without_runtime(pipeline):
    first = admit_batch(pipeline, batch_size=2)
    pipeline.close()
    restarted = module.ComicGenPipeline(data_dir=pipeline.data_dir, script_processor=Mock(), warmup_demucs=False)
    try:
        assert restarted.provider_recovery_runtime is None
        replay = admit_batch(restarted, batch_size=2)
        assert replay.replayed
        assert replay.task_ids == first.task_ids
        assert replay.new_task_ids == ()
    finally:
        restarted.close()


def test_batch_deleted_task_and_frame_replay_as_tombstone(pipeline):
    first = admit_batch(pipeline, batch_size=2)
    pipeline.scripts["p"].video_tasks = [pipeline.scripts["p"].video_tasks[0]]
    pipeline.scripts["p"].frames.clear()
    pipeline._commit_storage("projects.json")

    replay = admit_batch(pipeline, batch_size=2)

    assert replay.replayed
    assert replay.task_ids == first.task_ids
    assert replay.reason == "provider_request_deleted"
    assert len(pipeline.scripts["p"].video_tasks) == 1


def test_schema1_single_receipt_still_replays_with_batch_union_validator(pipeline):
    single = pipeline.admit_provider_video_task_with_receipt(
        "p", _admission_request(), client_request_id="single-1",
    )
    assert pipeline.scripts["p"].provider_request_receipts[0].schema_version == 1
    pipeline.provider_recovery_runtime = None
    replay = pipeline.admit_provider_video_task_with_receipt(
        "p", _admission_request(), client_request_id="single-1",
    )
    assert replay.replayed
    assert replay.task_id == single.task_id


def test_copy_frame_preserves_original_batch_receipt_only(pipeline):
    batch = admit_batch(pipeline, batch_size=2)
    for task in pipeline.scripts["p"].video_tasks:
        task.status = "completed"
        task.video_url = f"video/{task.id}.mp4"
    pipeline._commit_storage("projects.json")

    copied = pipeline.copy_frame("p", "frame")

    assert len(copied.provider_request_receipts) == 1
    assert [member.task_id for member in copied.provider_request_receipts[0].members] == list(batch.task_ids)
    cloned = [task for task in copied.video_tasks if task.id not in batch.task_ids]
    assert cloned
    assert all(task.provider_execution is None for task in cloned)


def test_batch_result_is_detached_from_persisted_ledger(pipeline):
    first = admit_batch(pipeline, batch_size=2)
    first.script.provider_request_receipts.clear()
    first.script.video_tasks.clear()
    again = admit_batch(pipeline, batch_size=2)
    assert again.replayed
    assert len(again.script.video_tasks) == 2


@pytest.mark.parametrize("batch_size", [0, 7, True, "2"])
def test_invalid_batch_size_fails_before_credentials(pipeline, batch_size):
    pipeline.provider_recovery_runtime = None
    with pytest.raises(ValueError):
        admit_batch(pipeline, batch_size=batch_size)
    assert pipeline.scripts["p"].video_tasks == []


def test_raw_batch_size_transport_field_is_rejected_before_credentials(pipeline):
    pipeline.provider_recovery_runtime = None
    with pytest.raises(ValueError, match="provider_request_batch_size_transport_field"):
        admit_batch(pipeline, _admission_request(batch_size=2), batch_size=2)
    assert pipeline.scripts["p"].video_tasks == []
