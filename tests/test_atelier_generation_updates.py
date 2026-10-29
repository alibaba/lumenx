"""Execution progress is a CAS on one attempt, independent of canvas edits."""

from unittest.mock import Mock

import pytest

from src.models.provider_recovery import record_with_changes
from src.models.provider_tasks import ProviderHandle, ProviderObservation, ProviderTaskStatus
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_atelier_generation_ledger_storage import _project, _store_with_project, _handle


@pytest.fixture
def store(tmp_path):
    storage = _store_with_project(tmp_path, _project())
    yield storage
    storage.close()


def update(store, callback, *, revision=0, fingerprint=None, project_guard=None):
    original = store.get_project("p1").generation_attempts[0]
    guard_kwargs = {"project_guard": project_guard} if project_guard is not None else {}
    return store.update_generation_attempt(
        "p1", attempt_id="attempt-1", expected_attempt_revision=revision,
        execution_fingerprint=fingerprint or original.provider_execution.fingerprint,
        command=callback, **guard_kwargs,
    )


def submitting(attempt):
    attempt.status = "processing"
    attempt.provider_execution = record_with_changes(attempt.provider_execution, phase="submitting")
    attempt.updated_at = 20.0


def test_progress_preserves_concurrent_canvas_edit_and_receipts(store):
    store.apply_project_command("p1", expected_revision=1, command=lambda p: setattr(p.nodes[0], "prompt", "User revised text"))
    before = store.get_project("p1")
    project, attempt = update(store, submitting)
    assert project.revision == 2 and project.updated_at == before.updated_at
    assert project.nodes[0].prompt == "User revised text"
    assert project.generation_request_receipts == before.generation_request_receipts
    assert attempt.attempt_revision == 1
    assert project.nodes[0].data["candidates"][0]["status"] == "processing"
    assert project.nodes[0].data["candidates"][0]["updated_at"] == 20.0
    attempt.status = "failed"
    assert store.get_project("p1").generation_attempts[0].status == "processing"


def test_stale_attempt_or_fingerprint_rejects_callback(store):
    callback = Mock()
    for kwargs in [{"revision": 2}, {"fingerprint": "a" * 64}]:
        with pytest.raises(StorageConflictError):
            update(store, callback, **kwargs)
    callback.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "different"), ("node_id", "different"),
    ("created_at", 99.0), ("attempt_revision", 5),
])
def test_callback_cannot_rewrite_attempt_identity(store, field, value):
    before = store.path.read_bytes()
    with pytest.raises(StorageError):
        update(store, lambda a: setattr(a, field, value))
    assert store.path.read_bytes() == before


def test_provider_execution_cannot_roll_back_to_accepted(store):
    update(store, submitting)
    before = store.path.read_bytes()
    def rollback(attempt):
        attempt.provider_execution = record_with_changes(attempt.provider_execution, phase="accepted")
        attempt.status = "pending"
    with pytest.raises(StorageError):
        update(store, rollback, revision=1)
    assert store.path.read_bytes() == before


def test_saved_handle_cannot_be_replaced(store):
    update(store, submitting)
    def submitted(attempt):
        attempt.provider_execution = record_with_changes(attempt.provider_execution, phase="submitted", handle=_handle())
    update(store, submitted, revision=1)
    def replace(attempt):
        attempt.provider_execution = record_with_changes(attempt.provider_execution, handle={**_handle(), "task_id": "replacement"})
    with pytest.raises(StorageError):
        update(store, replace, revision=2)


def test_cancelled_terminal_attempt_is_immutable_but_noop_is_safe(store):
    def cancel(attempt):
        attempt.status = "cancelled"
        attempt.provider_execution = record_with_changes(attempt.provider_execution, phase="cancelled")
    _, cancelled = update(store, cancel)
    _, unchanged = update(store, lambda a: None, revision=1)
    assert unchanged == cancelled
    with pytest.raises(StorageError):
        update(store, lambda a: setattr(a, "error_code", "changed"), revision=1)


def test_failed_write_keeps_original_projection_and_revision(store, monkeypatch):
    original = store.path.read_bytes()
    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", Mock(side_effect=StorageError("disk full")))
    with pytest.raises(StorageError):
        update(store, submitting)
    assert store.path.read_bytes() == original
    assert store.get_project("p1").generation_attempts[0].attempt_revision == 0


def test_closed_storage_rejects_late_execution_callback(store):
    identity = store.get_project("p1").generation_attempts[0].provider_execution.fingerprint
    store.close()
    callback = Mock()
    with pytest.raises(StorageConflictError):
        store.update_generation_attempt("p1", attempt_id="attempt-1", expected_attempt_revision=0, execution_fingerprint=identity, command=callback)
    callback.assert_not_called()


def test_completion_requires_previously_committed_proof_and_never_selects_candidate(store):
    update(store, submitting)
    def submitted(attempt):
        attempt.provider_execution = record_with_changes(attempt.provider_execution, phase="submitted", handle=_handle())
    update(store, submitted, revision=1)
    observation = ProviderObservation(
        task_id="remote-task-1", status=ProviderTaskStatus.SUCCEEDED,
        raw_status="SUCCEEDED", video_url="https://example.test/result.mp4",
        source_handle=ProviderHandle.from_dict(_handle()),
    ).to_dict()
    def observed(attempt):
        attempt.provider_execution = record_with_changes(attempt.provider_execution, phase="observed", observation=observation)
    update(store, observed, revision=2)
    proof = _project(status="completed").generation_attempts[0].provider_execution.artifact
    def complete(attempt):
        attempt.status = "completed"
        attempt.output_ref = proof["relative_path"]
        attempt.provider_execution = record_with_changes(attempt.provider_execution, phase="completed", prepared_artifact=proof, artifact=proof)
    with pytest.raises(StorageError, match="saved publication proof"):
        update(store, complete, revision=3)
    update(store, lambda a: setattr(a, "provider_execution", record_with_changes(a.provider_execution, prepared_artifact=proof)), revision=3)
    project, completed = update(store, complete, revision=4)
    assert completed.attempt_revision == 5 and completed.status == "completed"
    assert project.revision == 1
    assert project.nodes[0].media_urls == []
    assert "selected_candidate_id" not in project.nodes[0].data
    assert project.nodes[0].data["candidates"][0]["video_url"] == proof["relative_path"]


def test_progress_handles_legacy_absent_candidate_bucket(store):
    # Legacy snapshots may have a null unused bucket; only the matching typed
    # candidate participates in this update.
    store.apply_project_command("p1", expected_revision=1, command=lambda p: p.nodes[0].data.update(
        deleted_candidates=p.nodes[0].data["candidates"], candidates=None,
    ))
    project, attempt = update(store, submitting)
    assert project.nodes[0].data["deleted_candidates"][0]["status"] == attempt.status


def test_submit_guard_observes_latest_policy_and_denial_preserves_disk(store):
    store.apply_project_command("p1", expected_revision=1, command=lambda p: setattr(
        p.agent_policy, "allowed_tools", ["canvas.readProject"],
    ))
    before = store.path.read_bytes()
    transition = Mock(side_effect=submitting)

    def require_generation_permission(project, attempt):
        assert project.revision == 2
        assert attempt.provider_execution.phase == "accepted"
        if "generation.createVideoCandidates" not in project.agent_policy.allowed_tools:
            raise StorageConflictError("agent_authorization_revoked")

    with pytest.raises(StorageConflictError, match="agent_authorization_revoked"):
        update(store, transition, project_guard=require_generation_permission)
    transition.assert_not_called()
    assert store.path.read_bytes() == before
    assert store.get_project("p1").generation_attempts[0].provider_execution.phase == "accepted"


def test_submit_guard_arguments_cannot_mutate_saved_state(store):
    def mutate_detached_arguments(project, attempt):
        project.title = "not authorized to edit"
        project.agent_policy.allowed_tools = ["unexpected"]
        attempt.candidate_id = "not authorized to change"

    before = store.get_project("p1")
    project, attempt = update(store, submitting, project_guard=mutate_detached_arguments)
    assert project.title == before.title
    assert project.agent_policy == before.agent_policy
    assert attempt.candidate_id == before.generation_attempts[0].candidate_id


def test_stale_attempt_skips_project_guard(store):
    guard = Mock(side_effect=AssertionError("stale input must fail before authorization"))
    with pytest.raises(StorageConflictError):
        update(store, submitting, revision=9, project_guard=guard)
    guard.assert_not_called()


def test_project_guard_cannot_run_nested_storage_commands(store):
    before = store.path.read_bytes()

    def nested(project, _attempt):
        store.apply_project_command("p1", expected_revision=project.revision, command=lambda p: setattr(p, "title", "nested"))

    with pytest.raises(StorageConflictError, match="Nested"):
        update(store, submitting, project_guard=nested)
    assert store.path.read_bytes() == before


def test_guard_and_submit_transition_share_lock_against_policy_edit(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    guard_entered = Event()
    release_guard = Event()
    edit_started = Event()
    edit_applied = Event()
    phases_seen = []

    def guard(_project, _attempt):
        guard_entered.set()
        assert release_guard.wait(5)

    def edit_policy():
        edit_started.set()
        def apply(project):
            phases_seen.append(project.generation_attempts[0].provider_execution.phase)
            project.agent_policy.allowed_tools = ["canvas.readProject"]
            edit_applied.set()
        store.apply_project_command("p1", expected_revision=1, command=apply)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claim = executor.submit(update, store, submitting, project_guard=guard)
        assert guard_entered.wait(5)
        edit = executor.submit(edit_policy)
        assert edit_started.wait(5)
        try:
            assert not edit_applied.wait(0.05)
        finally:
            release_guard.set()
        claim.result(timeout=5)
        edit.result(timeout=5)
    assert phases_seen == ["submitting"]
    assert store.get_project("p1").agent_policy.allowed_tools == ["canvas.readProject"]
