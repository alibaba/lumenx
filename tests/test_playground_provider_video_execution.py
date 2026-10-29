"""Playground provider video execution advances durable Core records one step at a time."""

from __future__ import annotations

import hashlib
import threading

import pytest

from src.apps.playground.models import PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode, PlaygroundOutput
from src.apps.playground.provider_video_execution import PlaygroundProviderVideoExecutionService
from src.apps.playground.storage import PlaygroundStorage
from src.models.provider_binding import ProviderCredentialStore
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_recovery import build_accepted_provider_submission_and_record, record_with_changes
from src.models.provider_runtime import ProviderRecoveryRuntime
from src.models.provider_tasks import (
    ProviderArtifact,
    ProviderHandle,
    ProviderObservation,
    ProviderQueryError,
    ProviderScope,
    ProviderTaskStatus,
    QueryCapability,
)
from src.utils.generation_intent import GenerationIntent
from src.utils.json_storage import StorageError


SECRET = "fake-secret"
SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)


class FakeProvider:
    def __init__(self) -> None:
        self.submits = []
        self.queries = []
        self.fetches = []
        self.submit_error = None
        self.query_error = None
        self.query_status = ProviderTaskStatus.RUNNING
        self.query_raw_status = "RUNNING"
        self.fetch_bytes = b"playground provider video bytes"
        self.before_submit_network = None
        self.before_submit_callback = None
        self.before_fetch_callback = None
        self.next_task_id = "remote-task-1"
        self.returned_task_id = None

    def submit(self, request, *, credential, on_submitted):
        if self.before_submit_network is not None:
            self.before_submit_network()
        self.submits.append((request, credential))
        if self.submit_error is not None:
            raise self.submit_error
        handle = ProviderHandle(
            provider_model_id=request.provider_model_id,
            task_id=self.next_task_id,
            scope=request.scope,
            credential_ref=request.credential_ref,
            request_id="submit-request-1",
            query_capability=QueryCapability.BY_TASK_ID,
            submitted_at=123.0,
        )
        if self.before_submit_callback is not None:
            self.before_submit_callback()
        on_submitted(handle)
        if self.returned_task_id is not None:
            return ProviderHandle(
                provider_model_id=request.provider_model_id,
                task_id=self.returned_task_id,
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
            task_id=handle.task_id,
            status=self.query_status,
            raw_status=self.query_raw_status,
            request_id="query-request-1",
            video_url="https://provider.example/result.mp4" if self.query_status is ProviderTaskStatus.SUCCEEDED else None,
            source_handle=handle,
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
        if self.before_fetch_callback is not None:
            self.before_fetch_callback()
        if max_bytes < len(self.fetch_bytes):
            raise AssertionError("fetch output budget is too small")
        digest = hashlib.sha256(self.fetch_bytes).hexdigest()
        artifact = ProviderArtifact(handle, output_ref, digest, len(self.fetch_bytes))
        if on_prepared is not None:
            on_prepared(artifact)
        target = output_root / output_ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.fetch_bytes)
        return artifact


@pytest.fixture
def app(tmp_path):
    storage = PlaygroundStorage(tmp_path)
    credentials = ProviderCredentialStore(tmp_path, writer_lock=storage._writer_lock, runtime_lock=storage._lock)
    provider = FakeProvider()
    runtime = ProviderRecoveryRuntime(scope=SCOPE, secret_resolver=lambda: SECRET, provider=provider, max_bytes=1024 * 1024)
    service = PlaygroundProviderVideoExecutionService(
        storage,
        runtime_provider=lambda scope: runtime if scope == SCOPE else None,
        credential_store=credentials,
        clock=lambda: 200.0,
    )
    yield service, storage, credentials, provider, tmp_path
    storage.close()


def _intent() -> GenerationIntent:
    return GenerationIntent(
        model_id="wan/wan2.7-video#t2v",
        catalog_mode="t2v",
        mode="t2v",
        prompt="A calm ocean at sunrise",
        params={"duration": 5, "resolution": "720P", "ratio": "16:9", "prompt_extend": False, "watermark": False, "seed": 0},
        sources={
            "model_id": "request",
            "mode": "request",
            "prompt": "request",
            "params.duration": "request",
            "params.resolution": "request",
            "params.ratio": "request",
            "params.prompt_extend": "request",
            "params.watermark": "request",
            "params.seed": "request",
        },
        request_fields=("mode", "model_id", "prompt", "params.duration", "params.resolution", "params.ratio"),
    )


def _record(credentials: ProviderCredentialStore, attempt_id: str, *, phase: str = "accepted") -> ProviderExecutionRecord:
    credential = credentials.bind(SCOPE, SECRET)
    _, record = build_accepted_provider_submission_and_record(
        _intent(),
        provider_model_id="wan2.7-t2v",
        media=(),
        scope=SCOPE,
        credential_ref=credential.credential_ref,
        attempt_id=attempt_id,
    )
    if phase != "accepted":
        record = record_with_changes(record, phase=phase)
    return record


def _handle(record: ProviderExecutionRecord, task_id: str = "remote-task-1") -> ProviderHandle:
    return ProviderHandle(
        provider_model_id=record.provider_model_id,
        task_id=task_id,
        scope=ProviderScope.from_dict(record.scope),
        credential_ref=record.credential_ref,
        request_id=f"request-{task_id}",
        query_capability=QueryCapability.BY_TASK_ID,
        submitted_at=123.0,
    )


def _observation(handle: ProviderHandle, status: ProviderTaskStatus = ProviderTaskStatus.SUCCEEDED) -> ProviderObservation:
    return ProviderObservation(
        task_id=handle.task_id,
        status=status,
        raw_status=status.value,
        request_id=f"query-{handle.task_id}",
        video_url="https://provider.example/result.mp4" if status is ProviderTaskStatus.SUCCEEDED else None,
        source_handle=handle,
    )


def _generation(record: ProviderExecutionRecord, *, generation_id: str = "generation-1", status: str = "pending", attempt_status: str = "pending") -> PlaygroundGeneration:
    return PlaygroundGeneration(
        schema_version=1,
        id=generation_id,
        client_request_id=f"client-{generation_id}",
        request_contract_version=None,
        request_fingerprint=f"fingerprint-{generation_id}",
        mode=PlaygroundMode.T2V,
        model_id="wan/wan2.7-video#t2v",
        prompt="A calm ocean at sunrise",
        negative_prompt=None,
        parameters={"duration": 5, "resolution": "720P", "ratio": "16:9"},
        batch_size=1,
        attempts=[PlaygroundAttempt(attempt_id=record.attempt_id, ordinal=0, status=attempt_status, error=None, provider_execution=record.model_dump(mode="json"))],
        status=status,
        error=None,
        created_at="2026-10-13T00:00:00+00:00",
    )


def _attempt(storage: PlaygroundStorage, generation_id: str = "generation-1", attempt_id: str = "attempt-1"):
    current = storage.get_generation(generation_id)
    assert current is not None
    return next(attempt for attempt in current.attempts if attempt.attempt_id == attempt_id)


def test_submitting_is_persisted_before_provider_post(app):
    service, storage, credentials, provider, _ = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))

    def assert_submitting_saved() -> None:
        saved = ProviderExecutionRecord.model_validate(_attempt(storage).provider_execution)
        assert saved.phase == "submitting"
        assert saved.handle is None

    provider.before_submit_network = assert_submitting_saved
    ack = service.run_attempt("generation-1", "attempt-1")

    saved = ProviderExecutionRecord.model_validate(ack.attempt.provider_execution)
    assert len(provider.submits) == 1
    assert saved.phase == "submitted"
    assert saved.handle is not None
    assert saved.handle["task_id"] == "remote-task-1"
    assert provider.queries == []


def test_submitting_save_failure_makes_zero_provider_post(app, monkeypatch):
    service, storage, credentials, provider, _ = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))
    original = storage.update_generation_attempt

    def fail_submitting(generation_id, attempt_id, command, **kwargs):
        def wrapped(gen, attempt):
            command(gen, attempt)
            incoming = ProviderExecutionRecord.model_validate(attempt.provider_execution)
            if incoming.phase == "submitting":
                raise StorageError("disk full")
        return original(generation_id, attempt_id, wrapped, **kwargs)

    monkeypatch.setattr(storage, "update_generation_attempt", fail_submitting)
    with pytest.raises(StorageError):
        service.run_attempt("generation-1", "attempt-1")

    assert provider.submits == []
    assert ProviderExecutionRecord.model_validate(_attempt(storage).provider_execution).phase == "accepted"


def test_saved_submitting_without_handle_never_reposts_after_restart(app, monkeypatch, tmp_path):
    service, storage, credentials, provider, _ = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))
    original = storage.update_generation_attempt
    failed = {"done": False}

    def fail_handle_once(generation_id, attempt_id, command, **kwargs):
        def wrapped(gen, attempt):
            command(gen, attempt)
            incoming = ProviderExecutionRecord.model_validate(attempt.provider_execution)
            if incoming.handle is not None and not failed["done"]:
                failed["done"] = True
                raise StorageError("cannot save handle")
        return original(generation_id, attempt_id, wrapped, **kwargs)

    monkeypatch.setattr(storage, "update_generation_attempt", fail_handle_once)
    with pytest.raises(StorageError):
        service.run_attempt("generation-1", "attempt-1")
    assert len(provider.submits) == 1
    saved = ProviderExecutionRecord.model_validate(_attempt(storage).provider_execution)
    assert saved.phase == "submitting"
    assert saved.handle is None

    monkeypatch.setattr(storage, "update_generation_attempt", original)
    restarted = PlaygroundProviderVideoExecutionService(
        storage,
        runtime_provider=lambda scope: ProviderRecoveryRuntime(scope=scope, secret_resolver=lambda: SECRET, provider=provider),
        credential_store=credentials,
    )
    current = restarted.run_attempt("generation-1", "attempt-1")
    assert current.attempt.status == "needs_confirmation"
    assert current.attempt.error == "provider_submit_receipt_missing"
    assert len(provider.submits) == 1


def test_query_failure_preserves_original_handle_and_marks_transient(app):
    service, storage, credentials, provider, _ = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))
    service.run_attempt("generation-1", "attempt-1")
    provider.query_error = ProviderQueryError("timeout")

    current = service.run_attempt("generation-1", "attempt-1")

    saved = ProviderExecutionRecord.model_validate(current.attempt.provider_execution)
    assert current.attempt.status == "needs_confirmation"
    assert current.attempt.error == "provider_query_unavailable"
    assert saved.phase == "submitted"
    assert saved.handle is not None
    assert saved.handle["task_id"] == "remote-task-1"
    assert len(provider.submits) == 1
    assert len(provider.queries) == 1


def test_fetch_persists_proof_before_publish_and_repairs_completion_without_refetch(app, monkeypatch):
    service, storage, credentials, provider, tmp_path = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))
    service.run_attempt("generation-1", "attempt-1")
    provider.query_status = ProviderTaskStatus.SUCCEEDED
    provider.query_raw_status = "SUCCEEDED"
    service.run_attempt("generation-1", "attempt-1")

    original_update_generation_attempt = storage.update_generation_attempt
    failed = {"done": False}

    def fail_completion_once(generation_id, attempt_id, command, **kwargs):
        def wrapped(gen, attempt):
            command(gen, attempt)
            incoming = ProviderExecutionRecord.model_validate(attempt.provider_execution)
            if incoming.artifact is not None and not failed["done"]:
                failed["done"] = True
                raise StorageError("cannot save completion")
        return original_update_generation_attempt(generation_id, attempt_id, wrapped, **kwargs)

    monkeypatch.setattr(storage, "update_generation_attempt", fail_completion_once)
    with pytest.raises(StorageError):
        service.run_attempt("generation-1", "attempt-1")
    assert len(provider.fetches) == 1
    persisted = ProviderExecutionRecord.model_validate(_attempt(storage).provider_execution)
    assert persisted.prepared_artifact is not None
    assert persisted.artifact is None
    assert persisted.prepared_artifact is not None
    assert (tmp_path / persisted.prepared_artifact["relative_path"]).read_bytes() == provider.fetch_bytes

    monkeypatch.setattr(storage, "update_generation_attempt", original_update_generation_attempt)
    provider.before_fetch_callback = lambda: (_ for _ in ()).throw(AssertionError("repair must not refetch"))
    repaired = service.run_attempt("generation-1", "attempt-1")

    assert repaired.attempt.status == "completed"
    assert len(provider.fetches) == 1
    assert repaired.generation.outputs[0].attempt_id == "attempt-1"
    assert repaired.generation.outputs[0].media_path == persisted.prepared_artifact["relative_path"]


def test_partial_batch_retains_completed_output_when_another_attempt_needs_confirmation(app):
    service, storage, credentials, provider, _ = app
    base_done_record = _record(credentials, "attempt-1")
    done_handle = _handle(base_done_record, "already-done")
    done_record = record_with_changes(base_done_record, phase="completed", handle=done_handle.to_dict(), artifact={
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "handle": done_handle.to_dict(),
        "relative_path": "playground/videos/already-done.mp4",
        "sha256": "a" * 64,
        "size_bytes": 12,
    })
    base_pending_record = _record(credentials, "attempt-2")
    pending_record = record_with_changes(base_pending_record, phase="submitted", handle=_handle(base_pending_record, "remote-2").to_dict())
    gen = PlaygroundGeneration(
        schema_version=1,
        id="generation-1",
        client_request_id="client-generation-1",
        request_contract_version=None,
        request_fingerprint="fingerprint-generation-1",
        mode=PlaygroundMode.T2V,
        model_id="wan/wan2.7-video#t2v",
        prompt="A calm ocean at sunrise",
        negative_prompt=None,
        parameters={"duration": 5, "resolution": "720P", "ratio": "16:9"},
        batch_size=2,
        status="processing",
        attempts=[
            PlaygroundAttempt(attempt_id="attempt-1", ordinal=0, status="completed", error=None, provider_execution=done_record.model_dump(mode="json")),
            PlaygroundAttempt(attempt_id="attempt-2", ordinal=1, status="processing", error=None, provider_execution=pending_record.model_dump(mode="json")),
        ],
        outputs=[PlaygroundOutput(
            id="out-1",
            media_path="playground/videos/already-done.mp4",
            media_type="video",
            thumbnail_path=None,
            saved_to_library=False,
            attempt_id="attempt-1",
        )],
        error=None,
        created_at="2026-10-13T00:00:00+00:00",
    )
    storage.add_generation(gen)
    provider.query_error = ProviderQueryError("timeout")

    current = service.run_attempt("generation-1", "attempt-2")

    assert current.generation.status == "processing"
    assert current.generation.outputs[0].media_path == "playground/videos/already-done.mp4"
    assert _attempt(storage, attempt_id="attempt-2").error == "provider_query_unavailable"


def test_concurrent_same_attempt_is_rejected_while_submit_is_active(app):
    service, storage, credentials, provider, _ = app
    record = _record(credentials, "attempt-1")
    storage.add_generation(_generation(record))
    entered = threading.Event()
    release = threading.Event()
    errors = []

    def block_submit() -> None:
        entered.set()
        assert release.wait(3)

    provider.before_submit_network = block_submit
    thread = threading.Thread(target=lambda: service.run_attempt("generation-1", "attempt-1"))
    thread.start()
    assert entered.wait(3)
    with pytest.raises(Exception) as excinfo:
        service.run_attempt("generation-1", "attempt-1")
    errors.append(str(excinfo.value))
    release.set()
    thread.join(3)
    assert not thread.is_alive()
    assert len(provider.submits) == 1
    assert any("already running" in error for error in errors)
