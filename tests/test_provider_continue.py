"""Explicit continuation preserves the authority of proven unsubmitted work."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_studio_provider_recovery import (
    FakeProvider,
    _admission_request,
    _install_provider,
)
from tests.test_studio_provider_recovery_dispatcher import _make_runtime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    item = _make_runtime(tmp_path, monkeypatch, FakeProvider())
    yield item
    item.close()


def admit(runtime):
    result = runtime.admit_provider_video_task_with_receipt(
        "p", _admission_request(), client_request_id="continue-original"
    )
    task = result.script.video_tasks[0]
    return task.id, {
        "expected_attempt_id": task.provider_execution.attempt_id,
        "expected_fingerprint": task.provider_execution.fingerprint,
    }


def test_restart_then_explicit_continue_preserves_original_attempt_and_receipt(runtime):
    task_id, identity = admit(runtime)
    receipt = (
        runtime.read_committed_script("p").provider_request_receipts[0].model_dump()
    )
    runtime.close()
    provider = FakeProvider()
    restarted = module.ComicGenPipeline(
        data_dir=runtime.data_dir, script_processor=Mock(), recover_orphan_tasks=True
    )
    try:
        _install_provider(restarted, provider)
        assert (
            restarted.read_committed_script("p").video_tasks[0].status
            == "needs_confirmation"
        )
        result = restarted.continue_accepted_provider_video_task(
            "p", task_id, **identity
        )
        assert (
            result.id == task_id
            and result.provider_execution.attempt_id == identity["expected_attempt_id"]
        )
        assert (
            result.status == "processing"
            and result.provider_execution.phase == "submitted"
        )
        assert len(provider.submits) == 1
        assert (
            restarted.read_committed_script("p")
            .provider_request_receipts[0]
            .model_dump()
            == receipt
        )
        replay = restarted.admit_provider_video_task_with_receipt(
            "p", _admission_request(), client_request_id="continue-original"
        )
        assert replay.task_id == task_id and replay.new_task_ids == ()
        with pytest.raises(StorageConflictError, match="provider_continue_not_allowed"):
            restarted.continue_accepted_provider_video_task("p", task_id, **identity)
        assert len(provider.submits) == 1
    finally:
        restarted.close()


@pytest.mark.parametrize("phase", ["submitting", "submitted", "cancelled"])
def test_unknown_or_terminal_phase_is_never_resubmitted(runtime, phase):
    task_id, identity = admit(runtime)
    provider = runtime.provider_recovery_runtime.provider
    if phase == "submitted":
        runtime.submit_provider_video_task("p", task_id)
    elif phase == "cancelled":
        runtime.cancel_provider_video_task("p", task_id)
    else:
        runtime.scripts["p"].video_tasks[0].provider_execution.phase = phase
        runtime.scripts["p"].video_tasks[0].status = "needs_confirmation"
        runtime._commit_storage("projects.json")
    count = len(provider.submits)
    before = (runtime.data_dir / "projects.json").read_bytes()
    with pytest.raises(StorageConflictError, match="provider_continue_not_allowed"):
        runtime.continue_accepted_provider_video_task("p", task_id, **identity)
    assert len(provider.submits) == count
    assert (runtime.data_dir / "projects.json").read_bytes() == before


@pytest.mark.parametrize(
    "bad", [{"expected_attempt_id": "stale"}, {"expected_fingerprint": "0" * 64}]
)
def test_stale_click_does_not_authorize_current_attempt(runtime, bad):
    task_id, identity = admit(runtime)
    with pytest.raises(StorageConflictError, match="provider_continue_not_allowed"):
        runtime.continue_accepted_provider_video_task(
            "p", task_id, **{**identity, **bad}
        )
    assert runtime.provider_recovery_runtime.provider.submits == []


@pytest.mark.parametrize(
    "corruption", ["missing", "duplicate", "wrong_frame", "wrong_member"]
)
def test_missing_or_changed_receipt_cannot_authorize_continue(runtime, corruption):
    task_id, identity = admit(runtime)
    script = runtime.scripts["p"]
    if corruption == "missing":
        script.provider_request_receipts.clear()
    elif corruption == "duplicate":
        script.provider_request_receipts.append(
            script.provider_request_receipts[0].model_copy(deep=True)
        )
    elif corruption == "wrong_frame":
        script.provider_request_receipts[0].frame_id = "other"
    else:
        script.provider_request_receipts[0].execution_fingerprint = "0" * 64
    with pytest.raises(StorageConflictError, match="provider_continue_not_allowed"):
        runtime.continue_accepted_provider_video_task("p", task_id, **identity)
    assert runtime.provider_recovery_runtime.provider.submits == []


def test_continue_storage_failure_is_closed_before_post(runtime, monkeypatch):
    task_id, identity = admit(runtime)
    provider = runtime.provider_recovery_runtime.provider
    monkeypatch.setattr(
        runtime._storage, "commit", Mock(side_effect=StorageError("disk failed"))
    )
    with pytest.raises(StorageError):
        runtime.continue_accepted_provider_video_task("p", task_id, **identity)
    assert provider.submits == []


def test_missing_credentials_keeps_same_task_for_later_explicit_continue(runtime):
    task_id, identity = admit(runtime)
    original = runtime.provider_recovery_runtime
    runtime.provider_recovery_runtime = replace(original, secret_resolver=lambda: "")
    result = runtime.continue_accepted_provider_video_task("p", task_id, **identity)
    assert (
        result.status == "needs_confirmation"
        and result.provider_execution.phase == "accepted"
    )
    assert original.provider.submits == []
    runtime.provider_recovery_runtime = original
    result = runtime.continue_accepted_provider_video_task("p", task_id, **identity)
    assert result.status == "processing" and len(original.provider.submits) == 1


def test_double_continue_and_cancel_while_submitting_never_duplicate_post(runtime):
    task_id, identity = admit(runtime)
    provider = runtime.provider_recovery_runtime.provider
    provider.block_submit = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            runtime.continue_accepted_provider_video_task, "p", task_id, **identity
        )
        try:
            assert provider.network_entered.wait(2)
            with pytest.raises(
                StorageConflictError, match="provider_operation_running"
            ):
                runtime.continue_accepted_provider_video_task("p", task_id, **identity)
            with pytest.raises(
                StorageConflictError, match="provider_cancel_unconfirmed"
            ):
                runtime.cancel_provider_video_task("p", task_id)
        finally:
            provider.network_release.set()
        assert pending.result(timeout=3).provider_execution.phase == "submitted"
    assert len(provider.submits) == 1


def test_continue_api_rejects_generation_payload_and_returns_original_committed_task(
    runtime,
):
    task_id, identity = admit(runtime)
    app = api.create_app(
        data_dir=runtime.data_dir,
        pipeline_instance=runtime,
        load_config=False,
        recover_orphan_tasks=False,
    )
    with TestClient(app) as client:
        url = f"/projects/p/provider_video_tasks/{task_id}/continue"
        assert (
            client.post(url, json={**identity, "prompt": "changed"}).status_code == 422
        )
        assert client.post(url, json={}).status_code == 422
        result = client.post(url, json=identity)
        assert result.status_code == 200, result.text
        assert (
            result.json()["id"] == task_id
            and result.json()["provider_execution"]["phase"] == "submitted"
        )
        denied = client.post(url, json=identity)
        assert (
            denied.status_code == 409
            and denied.json()["code"] == "provider_continue_not_allowed"
        )
        assert len(runtime.provider_recovery_runtime.provider.submits) == 1


def test_continue_api_reports_unavailable_runtime_and_missing_owner(runtime):
    task_id, identity = admit(runtime)
    runtime.provider_recovery_runtime = None
    app = api.create_app(
        data_dir=runtime.data_dir,
        pipeline_instance=runtime,
        load_config=False,
        recover_orphan_tasks=False,
    )
    with TestClient(app) as client:
        # create_app manages a missing runtime; force an unavailable snapshot for this error boundary.
        runtime.provider_recovery_runtime = None
        runtime.provider_runtime_selector = None
        app.state.provider_recovery_runtime = None
        app.state.provider_runtime_selector = None
        result = client.post(
            f"/projects/p/provider_video_tasks/{task_id}/continue", json=identity
        )
        assert (
            result.status_code == 409
            and result.json()["code"] == "provider_recovery_unavailable"
        )
        assert (
            client.post(
                "/projects/p/provider_video_tasks/missing/continue", json=identity
            ).status_code
            == 404
        )


def test_receipt_removed_during_credentials_lookup_cannot_authorize_post(runtime):
    from threading import Event

    task_id, identity = admit(runtime)
    entered, release = Event(), Event()
    original = runtime.provider_recovery_runtime

    def resolve():
        entered.set()
        assert release.wait(3)
        return original.resolve_secret()

    runtime.provider_recovery_runtime = replace(original, secret_resolver=resolve)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            runtime.continue_accepted_provider_video_task, "p", task_id, **identity
        )
        try:
            assert entered.wait(2)
            with runtime._save_lock:
                runtime.scripts["p"].provider_request_receipts.clear()
                runtime._commit_storage("projects.json")
        finally:
            release.set()
        with pytest.raises(StorageConflictError, match="provider_continue_not_allowed"):
            pending.result(timeout=3)
    assert original.provider.submits == []


def test_continuing_one_batch_member_keeps_other_original_members_unsubmitted(runtime):
    result = runtime.admit_provider_video_tasks_with_receipt(
        "p",
        _admission_request(),
        client_request_id="batch-original",
        batch_size=3,
    )
    task = result.script.video_tasks[1]
    continued = runtime.continue_accepted_provider_video_task(
        "p",
        task.id,
        expected_attempt_id=task.provider_execution.attempt_id,
        expected_fingerprint=task.provider_execution.fingerprint,
    )
    assert continued.id == task.id
    project = runtime.read_committed_script("p")
    assert [item.provider_execution.phase for item in project.video_tasks] == [
        "accepted",
        "submitted",
        "accepted",
    ]
    assert len(project.provider_request_receipts) == 1
    assert len(runtime.provider_recovery_runtime.provider.submits) == 1
