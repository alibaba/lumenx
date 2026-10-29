"""Atelier provider generation execution advances only durable attempts."""

import base64
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.apps.atelier.generation_admission import AtelierGenerationAdmissionService
from src.apps.atelier.generation_commands import CreateVideoCandidateCommand
from src.apps.atelier.generation_execution import AtelierGenerationExecutionService
from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import (
    ProviderArtifact,
    ProviderBindingError,
    ProviderHandle,
    ProviderObservation,
    ProviderQueryError,
    ProviderScope,
    ProviderTaskStatus,
    QueryCapability,
)
from src.utils.json_storage import StorageConflictError, StorageError


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=")
SECRET = "fake"
SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)


class FakeProvider:
    def __init__(self):
        self.submits = []
        self.queries = []
        self.fetches = []
        self.next_task_id = "remote-task-1"
        self.submit_error = None
        self.query_error = None
        self.fetch_error = None
        self.query_status = ProviderTaskStatus.RUNNING
        self.query_raw_status = "RUNNING"
        self.query_video_url = None
        self.query_error_code = None
        self.fetch_bytes = b"atelier paid provider video bytes"
        self.skip_fetch_callback = False
        self.skip_fetch_write = False
        self.fetch_output_ref_override = None
        self.handle_task_id_override = None
        self.returned_handle_task_id_override = None
        self.observation_task_id_override = None
        self.observation_source_handle_override = None
        self.block_submit = False
        self.network_entered = threading.Event()
        self.network_release = threading.Event()
        self.before_submit_callback = None
        self.before_fetch_callback = None

    def submit(self, request, *, credential, on_submitted):
        self.submits.append((request, credential))
        if self.block_submit:
            self.network_entered.set()
            assert self.network_release.wait(3), "submit network was not released"
        if self.submit_error is not None:
            raise self.submit_error
        handle = ProviderHandle(
            provider_model_id=request.provider_model_id,
            task_id=self.handle_task_id_override or self.next_task_id,
            scope=request.scope,
            credential_ref=request.credential_ref,
            request_id="submit-request-1",
            query_capability=QueryCapability.BY_TASK_ID,
            submitted_at=123.0,
        )
        if self.before_submit_callback is not None:
            self.before_submit_callback()
        on_submitted(handle)
        if self.returned_handle_task_id_override is not None:
            return ProviderHandle(
                provider_model_id=request.provider_model_id,
                task_id=self.returned_handle_task_id_override,
                scope=request.scope,
                credential_ref=request.credential_ref,
                request_id="submit-request-1",
                query_capability=QueryCapability.BY_TASK_ID,
                submitted_at=123.0,
            )
        return handle

    def query(self, handle, *, credential):
        self.queries.append((handle, credential))
        if self.query_error is not None:
            raise self.query_error
        return ProviderObservation(
            task_id=self.observation_task_id_override or handle.task_id,
            status=self.query_status,
            raw_status=self.query_raw_status,
            request_id="query-request-1",
            video_url=self.query_video_url,
            error_code=self.query_error_code,
            source_handle=self.observation_source_handle_override or handle,
        )

    def fetch(
        self,
        handle,
        observation,
        *,
        output_root,
        output_ref,
        writer_lock,
        storage_guard,
        max_bytes,
        on_prepared=None,
        publish_policy="replace",
    ):
        self.fetches.append((handle, observation, output_ref, publish_policy))
        if self.fetch_error is not None:
            raise self.fetch_error
        if self.before_fetch_callback is not None:
            self.before_fetch_callback()
        if max_bytes < len(self.fetch_bytes):
            raise AssertionError("fetch output budget is too small")
        target_ref = self.fetch_output_ref_override or output_ref
        digest = hashlib.sha256(self.fetch_bytes).hexdigest()
        artifact = ProviderArtifact(handle, target_ref, digest, len(self.fetch_bytes))
        if on_prepared is not None and not self.skip_fetch_callback:
            on_prepared(artifact)
        if not self.skip_fetch_write:
            target = output_root / target_ref
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.fetch_bytes)
        return artifact


def request(**changes):
    payload = {
        "expected_revision": 0,
        "client_request_id": "request-1",
        "model_id": "wan/wan2.7-video#i2v",
        "mode": "i2v",
        "prompt": "Slow sunrise",
        "first_frame": {"node_id": "image", "media_url": "uploads/frame.png"},
    }
    payload.update(changes)
    return CreateVideoCandidateCommand(**payload)


@pytest.fixture
def app(tmp_path):
    store = AtelierStorage(tmp_path)
    (tmp_path / "uploads").mkdir(parents=True)
    (tmp_path / "uploads/frame.png").write_bytes(PNG)
    store.create_project(
        AtelierProject(
            id="p",
            title="Canvas",
            created_at=1.0,
            updated_at=1.0,
            nodes=[
                AtelierNode(id="image", project_id="p", type="image", status="completed", media_urls=["uploads/frame.png"]),
                AtelierNode(
                    id="video",
                    project_id="p",
                    type="video",
                    data={"reference_node_ids": ["image"], "reference_image_urls": ["uploads/frame.png"]},
                ),
            ],
        )
    )
    credentials = ProviderCredentialStore(tmp_path, writer_lock=store._writer_lock, runtime_lock=store._lock)
    admission = AtelierGenerationAdmissionService(
        store,
        credential_provider=lambda *, provider_model_id, gateway: credentials.bind(SCOPE, SECRET),
        clock=lambda: 100.0,
        id_factory=iter(["attempt-1", "candidate-1"]).__next__,
    )
    ack = admission.admit("p", "video", request())
    provider = FakeProvider()
    runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=provider, max_bytes=1024 * 1024)
    service = AtelierGenerationExecutionService(
        store,
        runtime_provider=lambda scope: runtime,
        credential_store=credentials,
        clock=lambda: 200.0,
    )
    yield service, store, provider, ack, tmp_path
    store.close()


def _attempt(store, attempt_id="attempt-1"):
    return next(item for item in store.get_project("p").generation_attempts if item.attempt_id == attempt_id)


def _continue(service, ack, **changes):
    kwargs = {
        "candidate_id": ack.attempt.candidate_id,
        "expected_attempt_revision": _attempt(service.storage, ack.attempt.attempt_id).attempt_revision,
        "execution_fingerprint": ack.attempt.provider_execution.fingerprint,
    }
    kwargs.update(changes)
    return service.continue_attempt("p", ack.attempt.attempt_id, **kwargs)


def test_submitting_save_failure_makes_zero_provider_post(app, monkeypatch):
    service, store, provider, ack, _ = app
    original = store.update_generation_attempt

    def fail_submitting(*args, command, **kwargs):
        def wrapped(attempt):
            command(attempt)
            if attempt.provider_execution.phase == "submitting":
                raise StorageError("disk full")
        return original(*args, command=wrapped, **kwargs)

    monkeypatch.setattr(store, "update_generation_attempt", fail_submitting)
    with pytest.raises(StorageError):
        _continue(service, ack)
    assert provider.submits == []
    assert _attempt(store).provider_execution.phase == "accepted"


def test_saved_submitting_without_handle_never_reposts_after_restart(app, monkeypatch):
    service, store, provider, ack, tmp_path = app
    original = store.update_generation_attempt
    calls = {"count": 0}

    def fail_handle_once(*args, command, **kwargs):
        def wrapped(attempt):
            command(attempt)
            if attempt.provider_execution.handle is not None and calls["count"] == 0:
                calls["count"] += 1
                raise StorageError("cannot save handle")
        return original(*args, command=wrapped, **kwargs)

    monkeypatch.setattr(store, "update_generation_attempt", fail_handle_once)
    with pytest.raises(StorageError):
        _continue(service, ack)
    assert len(provider.submits) == 1
    assert _attempt(store).provider_execution.phase == "submitting"
    assert _attempt(store).provider_execution.handle is None

    store.close()
    reopened = AtelierStorage(tmp_path)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=reopened._writer_lock, runtime_lock=reopened._lock)
    restarted = AtelierGenerationExecutionService(
        reopened,
        runtime_provider=lambda scope: ProviderRecoveryRuntime(scope=scope, secret_resolver=lambda: SECRET, provider=provider),
        credential_store=credentials,
    )
    try:
        current = restarted.run_attempt("p", ack.attempt.attempt_id)
        assert current.attempt.status == "needs_confirmation"
        assert current.attempt.error_code == "provider_submit_receipt_missing"
        assert len(provider.submits) == 1
    finally:
        reopened.close()


def test_unknown_submit_and_bad_receipts_stop_without_query_or_fetch(app):
    service, store, provider, ack, _ = app
    _continue(service, ack)
    provider.queries.clear()
    provider.fetches.clear()
    current = service.run_attempt("p", ack.attempt.attempt_id)
    assert current.attempt.status == "processing"
    assert len(provider.queries) == 1
    assert provider.fetches == []

    project = store.get_project("p")
    attempt = project.generation_attempts[0]
    bad = attempt.provider_execution.model_copy(deep=True)
    bad.handle["task_id"] = "other-task"
    with pytest.raises((StorageError, ValueError)):
        store.update_generation_attempt(
            "p",
            attempt_id=attempt.attempt_id,
            expected_attempt_revision=attempt.attempt_revision,
            execution_fingerprint=attempt.provider_execution.fingerprint,
            command=lambda item: setattr(item, "provider_execution", bad),
        )


def test_original_credential_change_requires_attention(app):
    service, _, provider, ack, _ = app
    service._runtime_provider = lambda scope: ProviderRecoveryRuntime(scope=scope, secret_resolver=lambda: "changed-secret", provider=provider)
    current = _continue(service, ack)
    assert current.attempt.status == "needs_confirmation"
    assert current.attempt.error_code == "provider_credentials_required"
    assert provider.submits == []


def test_first_frame_is_frozen_before_source_file_changes(app):
    service, _, provider, ack, tmp_path = app
    (tmp_path / "uploads/frame.png").write_bytes(b"changed mutable source")
    _continue(service, ack)
    submission = provider.submits[0][0]
    assert submission.media[0].version.sha256 == ack.attempt.provider_execution.media[0]["sha256"]


def test_query_success_must_fetch_before_completion(app):
    service, store, provider, ack, tmp_path = app
    _continue(service, ack)
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://provider.example/video.mp4"

    observed = service.run_attempt("p", ack.attempt.attempt_id)
    assert observed.attempt.status == "processing"
    assert observed.attempt.provider_execution.phase == "observed"
    assert observed.attempt.output_ref is None
    assert provider.fetches == []

    completed = service.run_attempt("p", ack.attempt.attempt_id)
    assert completed.attempt.status == "completed"
    assert completed.attempt.output_ref == "video/atelier-candidate-1-attempt-1.mp4"
    assert (tmp_path / completed.attempt.output_ref).read_bytes() == provider.fetch_bytes
    assert store.get_project("p").nodes[1].data["candidates"][0]["video_url"] == completed.attempt.output_ref
    assert "selected_candidate_id" not in store.get_project("p").nodes[1].data
    assert provider.fetches[0][3] == "no_overwrite"


def test_prepared_save_failure_does_not_publish(app, monkeypatch, tmp_path):
    service, store, provider, ack, _ = app
    _continue(service, ack)
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://provider.example/video.mp4"
    service.run_attempt("p", ack.attempt.attempt_id)
    original = store.update_generation_attempt

    def fail_prepared(*args, command, **kwargs):
        def wrapped(attempt):
            command(attempt)
            if attempt.provider_execution.prepared_artifact is not None:
                raise StorageError("cannot save prepared proof")
        return original(*args, command=wrapped, **kwargs)

    monkeypatch.setattr(store, "update_generation_attempt", fail_prepared)
    with pytest.raises(StorageError):
        service.run_attempt("p", ack.attempt.attempt_id)
    assert not (tmp_path / "video/atelier-candidate-1-attempt-1.mp4").exists()


def test_published_after_final_save_failure_can_repair_from_proof_without_refetch(app, monkeypatch):
    service, store, provider, ack, _ = app
    _continue(service, ack)
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://provider.example/video.mp4"
    service.run_attempt("p", ack.attempt.attempt_id)
    original = store.update_generation_attempt
    failed = {"done": False}

    def fail_completion_once(*args, command, **kwargs):
        def wrapped(attempt):
            command(attempt)
            if attempt.provider_execution.artifact is not None and not failed["done"]:
                failed["done"] = True
                raise StorageError("cannot save completion")
        return original(*args, command=wrapped, **kwargs)

    monkeypatch.setattr(store, "update_generation_attempt", fail_completion_once)
    with pytest.raises(StorageError):
        service.run_attempt("p", ack.attempt.attempt_id)
    assert len(provider.fetches) == 1
    monkeypatch.setattr(store, "update_generation_attempt", original)
    provider.fetch_error = AssertionError("repair must not refetch")
    repaired = service.run_attempt("p", ack.attempt.attempt_id)
    assert repaired.attempt.status == "completed"
    assert len(provider.fetches) == 1


def test_tampered_existing_output_is_invalid_and_never_refetched(app):
    service, _, provider, ack, tmp_path = app
    _continue(service, ack)
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://provider.example/video.mp4"
    service.run_attempt("p", ack.attempt.attempt_id)
    provider.skip_fetch_write = True
    prepared = service.run_attempt("p", ack.attempt.attempt_id)
    assert prepared.attempt.provider_execution.prepared_artifact is not None
    target = tmp_path / "video/atelier-candidate-1-attempt-1.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"tampered existing bytes")

    provider.fetch_error = AssertionError("existing mismatched output must not refetch")
    failed = service.run_attempt("p", ack.attempt.attempt_id)
    assert failed.attempt.status == "needs_confirmation"
    assert failed.attempt.error_code == "provider_fetch_artifact_invalid"
    assert target.read_bytes() == b"tampered existing bytes"
    assert len(provider.fetches) == 1


def test_fetch_callback_uses_operation_revision_and_rejects_late_same_attempt_update(app):
    service, store, provider, ack, _ = app
    _continue(service, ack)
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://provider.example/video.mp4"
    service.run_attempt("p", ack.attempt.attempt_id)

    def advance_attempt_before_prepared():
        current = _attempt(store)
        store.update_generation_attempt(
            "p",
            attempt_id=current.attempt_id,
            expected_attempt_revision=current.attempt_revision,
            execution_fingerprint=current.provider_execution.fingerprint,
            command=lambda attempt: setattr(attempt, "error_code", "transient-note"),
        )

    provider.before_fetch_callback = advance_attempt_before_prepared
    with pytest.raises(StorageConflictError):
        service.run_attempt("p", ack.attempt.attempt_id)
    assert _attempt(store).provider_execution.prepared_artifact is None
    assert _attempt(store).error_code == "transient-note"


def test_bad_returned_handle_and_bad_observation_are_needs_confirmation(app):
    service, store, provider, ack, _ = app
    provider.returned_handle_task_id_override = "wrong-task"
    bad_handle = _continue(service, ack)
    assert bad_handle.attempt.status == "needs_confirmation"
    assert bad_handle.attempt.error_code == "provider_submit_receipt_mismatch"

    fresh = AtelierGenerationAdmissionService(
        store,
        credential_provider=lambda *, provider_model_id, gateway: service._credential_store.bind(SCOPE, SECRET),
        id_factory=iter(["attempt-2", "candidate-2"]).__next__,
    ).admit("p", "video", request(expected_revision=1, client_request_id="request-2"))
    provider.returned_handle_task_id_override = None
    _continue(service, fresh)
    provider.observation_task_id_override = "wrong-observation"
    bad_observation = service.run_attempt("p", fresh.attempt.attempt_id)
    assert bad_observation.attempt.status == "needs_confirmation"
    assert bad_observation.attempt.error_code == "provider_query_invalid"


def test_closed_storage_rejects_late_submit_callback(app):
    service, store, provider, ack, _ = app

    def close_before_handle():
        store.close()

    provider.before_submit_callback = close_before_handle
    with pytest.raises(StorageConflictError):
        _continue(service, ack)
    assert len(provider.submits) == 1


def test_runtime_is_captured_once_and_secret_switch_does_not_cross_credentials(app):
    service, _, provider, ack, _ = app
    calls = {"count": 0}

    def runtime_provider(scope):
        calls["count"] += 1
        secret = SECRET if calls["count"] == 1 else "changed-secret"
        return ProviderRecoveryRuntime(scope=scope, secret_resolver=lambda: secret, provider=provider, max_bytes=1024 * 1024)

    service._runtime_provider = runtime_provider
    _continue(service, ack)
    assert calls["count"] == 1
    assert len(provider.submits) == 1


def test_provider_errors_are_stable_codes_without_provider_text(app):
    service, _, provider, ack, _ = app
    provider.submit_error = ProviderBindingError("provider said SECRET backend stack")
    current = _continue(service, ack)
    assert current.attempt.error_code == "provider_submit_receipt_missing"
    assert "SECRET" not in _attempt(service.storage).model_dump_json()

    provider.submit_error = None
    provider.query_error = ProviderQueryError("provider said SECRET query failed")
    # Use a fresh admitted attempt for query-path diagnostics.
    fresh = AtelierGenerationAdmissionService(
        service.storage,
        credential_provider=lambda *, provider_model_id, gateway: service._credential_store.bind(SCOPE, SECRET),
        id_factory=iter(["attempt-2", "candidate-2"]).__next__,
    ).admit("p", "video", request(expected_revision=1, client_request_id="request-2"))
    _continue(service, fresh)
    current = service.run_attempt("p", ack.attempt.attempt_id)
    current = service.run_attempt("p", fresh.attempt.attempt_id)
    assert current.attempt.error_code == "provider_query_unavailable"
    assert "SECRET" not in current.attempt.model_dump_json()


def test_cancel_only_unsubmitted_accepted_attempt(app):
    service, store, provider, ack, _ = app
    cancelled = service.cancel_attempt(
        "p",
        ack.attempt.attempt_id,
        candidate_id=ack.attempt.candidate_id,
        expected_attempt_revision=0,
        execution_fingerprint=ack.attempt.provider_execution.fingerprint,
    )
    assert cancelled.attempt.status == "cancelled"
    assert cancelled.attempt.provider_execution.phase == "cancelled"
    assert provider.submits == provider.queries == provider.fetches == []
    assert store.get_project("p").nodes[1].data["candidates"][0]["status"] == "cancelled"


def test_concurrent_execution_has_single_submit_and_edit_during_network_survives(app):
    service, store, provider, ack, _ = app
    provider.block_submit = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lambda: _continue(service, ack))
        assert provider.network_entered.wait(2), "submit did not enter network"
        store.apply_project_command("p", expected_revision=1, command=lambda project: setattr(project.nodes[1], "x", 42))
        second = pool.submit(lambda: service.run_attempt("p", ack.attempt.attempt_id))
        provider.network_release.set()
        first.result()
        with pytest.raises(StorageConflictError):
            second.result()
    assert len(provider.submits) == 1
    assert store.get_project("p").nodes[1].x == 42


def test_observed_running_attempt_queries_again(app):
    service, _, provider, ack, _ = app
    _continue(service, ack)
    first = service.run_attempt("p", ack.attempt.attempt_id)
    assert first.attempt.provider_execution.phase == "observed"
    assert first.attempt.status == "processing"
    second = service.run_attempt("p", ack.attempt.attempt_id)
    assert second.attempt.status == "processing"
    assert len(provider.queries) == 2


def test_manual_continue_can_requery_unknown_observation_without_resubmit(app):
    service, _, provider, ack, _ = app
    _continue(service, ack)
    provider.query_status = ProviderTaskStatus.UNKNOWN
    provider.query_raw_status = "UNKNOWN"
    unknown = service.run_attempt("p", ack.attempt.attempt_id)
    assert unknown.attempt.status == "needs_confirmation"
    assert unknown.attempt.error_code == "provider_query_unknown"
    assert len(provider.submits) == 1
    assert len(provider.queries) == 1

    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    provider.query_video_url = "https://provider.example/video.mp4"
    retried = _continue(
        service,
        ack,
        expected_attempt_revision=unknown.attempt.attempt_revision,
    )
    assert retried.attempt.status == "processing"
    assert retried.attempt.provider_execution.phase == "observed"
    assert len(provider.submits) == 1
    assert len(provider.queries) == 2


def test_request_stop_rejects_new_execution_claims(app):
    service, _, provider, ack, _ = app
    service.request_stop()
    service.join()
    with pytest.raises(StorageConflictError):
        _continue(service, ack)
    assert provider.submits == []
