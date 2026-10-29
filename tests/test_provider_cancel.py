"""Cancel only provider work that can be proven not to have been submitted."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import ProviderExecutionRecord
from src.models.provider_tasks import ProviderTaskStatus
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_studio_provider_recovery import FakeProvider, _admission_request
from tests.test_studio_provider_recovery_dispatcher import _make_runtime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    item = _make_runtime(tmp_path, monkeypatch, FakeProvider())
    yield item
    item.close()


def _admit(runtime):
    return runtime.admit_provider_video_task_with_receipt(
        "p", _admission_request(), client_request_id="cancel-request",
    ).task_id


def test_cancel_before_submit_is_durable_and_replay_is_read_only(runtime):
    task_id = _admit(runtime)
    runtime.provider_recovery_runtime = None
    task = runtime.cancel_provider_video_task("p", task_id)
    assert task.status == "failed"
    assert task.error == "provider_cancelled_before_submit"
    assert task.provider_execution.phase == "cancelled"
    before = (runtime.data_dir / "projects.json").read_bytes()
    task.prompt = "detached edit"
    again = runtime.cancel_provider_video_task("p", task_id)
    assert again.prompt != "detached edit"
    assert (runtime.data_dir / "projects.json").read_bytes() == before
    replay = runtime.admit_provider_video_task_with_receipt(
        "p", _admission_request(), client_request_id="cancel-request",
    )
    assert replay.task_id == task_id and replay.new_task_ids == ()


def test_cancelled_task_never_submits_queries_or_fetches(runtime):
    task_id = _admit(runtime)
    provider = runtime.provider_recovery_runtime.provider
    runtime.cancel_provider_video_task("p", task_id)
    for operation in (runtime.submit_provider_video_task, runtime.query_provider_video_task, runtime.fetch_provider_video_task):
        result = operation("p", task_id)
        assert result.video_tasks[0].status == "failed"
        assert result.video_tasks[0].provider_execution.phase == "cancelled"
    assert provider.submits == provider.queries == provider.fetches == []


def test_cancelled_task_stays_cancelled_after_restart(runtime):
    task_id = _admit(runtime)
    runtime.cancel_provider_video_task("p", task_id)
    runtime.close()
    restarted = module.ComicGenPipeline(
        data_dir=runtime.data_dir, script_processor=Mock(), recover_orphan_tasks=True,
    )
    try:
        task = restarted.read_committed_script("p").video_tasks[0]
        assert task.status == "failed" and task.provider_execution.phase == "cancelled"
    finally:
        restarted.close()


def test_cancel_during_submit_cannot_discard_paid_receipt(runtime):
    task_id = _admit(runtime)
    provider = runtime.provider_recovery_runtime.provider
    provider.block_submit = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.submit_provider_video_task, "p", task_id)
        try:
            assert provider.network_entered.wait(2)
            before = (runtime.data_dir / "projects.json").read_bytes()
            with pytest.raises(StorageConflictError, match="provider_cancel_unconfirmed"):
                runtime.cancel_provider_video_task("p", task_id)
            assert (runtime.data_dir / "projects.json").read_bytes() == before
        finally:
            provider.network_release.set()
        result = pending.result(timeout=3)
    assert len(provider.submits) == 1
    assert result.video_tasks[0].provider_execution.handle["task_id"] == "remote-task-1"


def test_submitted_task_keeps_handle_when_cancel_cannot_be_confirmed(runtime):
    task_id = _admit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    before = (runtime.data_dir / "projects.json").read_bytes()
    with pytest.raises(StorageConflictError, match="provider_cancel_unconfirmed"):
        runtime.cancel_provider_video_task("p", task_id)
    assert (runtime.data_dir / "projects.json").read_bytes() == before


def test_cancel_save_failure_does_not_publish_success(runtime, monkeypatch):
    task_id = _admit(runtime)
    before = (runtime.data_dir / "projects.json").read_bytes()
    monkeypatch.setattr(runtime._storage, "commit", Mock(side_effect=StorageError("disk unavailable")))
    with pytest.raises(StorageError):
        runtime.cancel_provider_video_task("p", task_id)
    assert (runtime.data_dir / "projects.json").read_bytes() == before
    assert runtime.scripts["p"].video_tasks[0].provider_execution.phase == "accepted"


def test_cancel_endpoint_distinguishes_unsubmitted_and_already_submitted(runtime):
    task_id = _admit(runtime)
    _, paid_task_id = runtime.admit_provider_video_task("p", _admission_request())
    runtime.submit_provider_video_task("p", paid_task_id)
    app = api.create_app(
        data_dir=runtime.data_dir, pipeline_instance=runtime,
        load_config=False, recover_orphan_tasks=False,
    )
    with TestClient(app) as client:
        canceled = client.post(f"/projects/p/video_tasks/{task_id}/cancel")
        assert canceled.status_code == 200
        assert canceled.json()["error"] == "provider_cancelled_before_submit"
        denied = client.post(f"/projects/p/video_tasks/{paid_task_id}/cancel")
        assert denied.status_code == 409
        assert denied.json()["code"] == "provider_cancel_unconfirmed"
        assert "cannot" in denied.json()["detail"]
        assert client.post("/projects/p/video_tasks/missing/cancel").status_code == 404


def test_cancel_cannot_pass_claimed_accepted_task_while_secret_resolution_waits(runtime):
    task_id = _admit(runtime)
    entered, release = Event(), Event()
    original = runtime.provider_recovery_runtime

    def resolve():
        entered.set()
        assert release.wait(3)
        return original.resolve_secret()

    runtime.provider_recovery_runtime = replace(original, secret_resolver=resolve)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.submit_provider_video_task, "p", task_id)
        try:
            assert entered.wait(2)
            assert runtime.scripts["p"].video_tasks[0].provider_execution.phase == "accepted"
            before = (runtime.data_dir / "projects.json").read_bytes()
            with pytest.raises(StorageConflictError, match="provider_cancel_unconfirmed"):
                runtime.cancel_provider_video_task("p", task_id)
            assert (runtime.data_dir / "projects.json").read_bytes() == before
        finally:
            release.set()
        assert pending.result(timeout=3).video_tasks[0].provider_execution.phase == "submitted"


@pytest.mark.parametrize("stage", ["submitting", "observed", "completed"])
def test_cancel_preserves_unknown_or_confirmed_remote_work(runtime, stage):
    task_id = _admit(runtime)
    provider = runtime.provider_recovery_runtime.provider
    if stage == "submitting":
        runtime.scripts["p"].video_tasks[0].provider_execution.phase = stage
        runtime._commit_storage("projects.json")
    else:
        runtime.submit_provider_video_task("p", task_id)
        provider.query_status = ProviderTaskStatus.SUCCEEDED
        provider.query_video_url = "https://example.invalid/video.mp4"
        runtime.query_provider_video_task("p", task_id)
        if stage == "completed":
            runtime.fetch_provider_video_task("p", task_id)
    assert runtime.scripts["p"].video_tasks[0].provider_execution.phase == stage
    before = (runtime.data_dir / "projects.json").read_bytes()
    with pytest.raises(StorageConflictError, match="provider_cancel_unconfirmed"):
        runtime.cancel_provider_video_task("p", task_id)
    assert (runtime.data_dir / "projects.json").read_bytes() == before


def test_cancelled_record_rejects_provider_handle(runtime):
    task_id = _admit(runtime)
    runtime.submit_provider_video_task("p", task_id)
    record = runtime.scripts["p"].video_tasks[0].provider_execution.model_dump()
    record["phase"] = "cancelled"
    with pytest.raises(ValidationError, match="Cancelled provider execution"):
        ProviderExecutionRecord.model_validate(record)


def test_cancelled_operations_are_noops_without_any_current_runtime(runtime):
    task_id = _admit(runtime)
    runtime.cancel_provider_video_task("p", task_id)
    runtime.provider_recovery_runtime = None
    before = (runtime.data_dir / "projects.json").read_bytes()
    for operation in (runtime.submit_provider_video_task, runtime.query_provider_video_task, runtime.fetch_provider_video_task):
        assert operation("p", task_id).video_tasks[0].provider_execution.phase == "cancelled"
    assert (runtime.data_dir / "projects.json").read_bytes() == before
