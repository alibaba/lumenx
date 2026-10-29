"""Playground video recovery dispatcher advances only safe durable attempts."""

from __future__ import annotations

import logging
import threading
from typing import Callable

import pytest

from src.apps.playground.models import PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode
from src.apps.playground.provider_video_execution import PlaygroundVideoAttemptAck
from src.apps.playground.recovery_dispatcher import PlaygroundVideoRecoveryDispatcher
from src.models.provider_execution import ProviderExecutionRecord
from src.models.provider_tasks import ProviderHandle, ProviderObservation, ProviderScope, ProviderTaskStatus, QueryCapability
from src.utils.json_storage import StorageConflictError, StorageError


HASH = "a" * 64
SCOPE = ProviderScope(
    provider="dashscope",
    backend="dashscope",
    gateway="dashscope",
    base_url="https://dashscope.aliyuncs.com",
    region="cn-beijing",
)


def _handle(task_id: str) -> ProviderHandle:
    return ProviderHandle(
        provider_model_id="wan2.7-t2v",
        task_id=task_id,
        scope=SCOPE,
        credential_ref="credential-ref",
        request_id=f"request-{task_id}",
        query_capability=QueryCapability.BY_TASK_ID,
        submitted_at=10.0,
    )


def _observation(handle: ProviderHandle, status: ProviderTaskStatus = ProviderTaskStatus.SUCCEEDED) -> ProviderObservation:
    return ProviderObservation(
        task_id=handle.task_id,
        status=status,
        raw_status=status.value,
        request_id=f"query-{handle.task_id}",
        video_url="https://example.invalid/result.mp4" if status is ProviderTaskStatus.SUCCEEDED else None,
        source_handle=handle,
    )


def _record(
    attempt_id: str,
    *,
    phase: str = "accepted",
    handle: ProviderHandle | None = None,
    observation: ProviderObservation | None = None,
    prepared_artifact: dict | None = None,
) -> ProviderExecutionRecord:
    return ProviderExecutionRecord(
        schema_version=1,
        execution_kind=None,
        media_kind=None,
        output_ref=None,
        attempt_id=attempt_id,
        phase=phase,
        provider_model_id="wan2.7-t2v",
        intent={},
        media=[],
        scope=SCOPE.to_dict(),
        credential_ref="credential-ref",
        fingerprint=HASH,
        handle=handle.to_dict() if handle is not None else None,
        observation=observation.to_dict() if observation is not None else None,
        prepared_artifact=prepared_artifact,
        artifact=None,
    )


def _attempt(
    attempt_id: str,
    *,
    status: str = "pending",
    record: ProviderExecutionRecord | None = None,
    error: str | None = None,
) -> PlaygroundAttempt:
    return PlaygroundAttempt(
        attempt_id=attempt_id,
        ordinal=int(attempt_id.rsplit("-", 1)[-1]) if attempt_id.rsplit("-", 1)[-1].isdigit() else 0,
        status=status,
        error=error,
        provider_execution=(record or _record(attempt_id)).model_dump(mode="json"),
    )


def _generation(*attempts: PlaygroundAttempt, generation_id: str = "generation-1", status: str = "pending") -> PlaygroundGeneration:
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
        batch_size=max(1, len(attempts)),
        status=status,
        attempts=list(attempts),
        error=None,
        created_at="2026-10-13T00:00:00+00:00",
    )


class StorageFake:
    def __init__(self, *generations: PlaygroundGeneration):
        self.generations = list(generations)

    def list_history(self, limit=50, offset=0):
        return [gen.model_copy(deep=True) for gen in self.generations[offset : offset + limit]]


class ServiceFake:
    def __init__(self, storage: StorageFake):
        self.storage = storage
        self.calls: list[tuple[str, str]] = []
        self.stop_count = 0
        self.join_timeouts = []
        self.before_run: Callable[[str, str], object] | None = None

    def run_attempt(self, generation_id: str, attempt_id: str, **_kwargs):
        if self.before_run is not None:
            self.before_run(generation_id, attempt_id)
        self.calls.append((generation_id, attempt_id))
        gen = next(gen for gen in self.storage.generations if gen.id == generation_id)
        attempt = next(attempt for attempt in gen.attempts if attempt.attempt_id == attempt_id)
        return PlaygroundVideoAttemptAck(gen, attempt)

    def request_stop(self):
        self.stop_count += 1

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)


def test_recovery_submits_pending_accepted_and_marks_restart_ambiguity_visible():
    pending = _generation(_attempt("attempt-1"), generation_id="pending", status="pending")
    processing_accepted = _generation(_attempt("attempt-1", status="processing"), generation_id="processing", status="processing")
    submitting = _generation(_attempt("attempt-1", status="processing", record=_record("attempt-1", phase="submitting")), generation_id="submitting", status="processing")
    storage = StorageFake(pending, processing_accepted, submitting)
    service = ServiceFake(storage)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)

    assert dispatcher.run_once() == ("submit", "pending", "attempt-1")
    pending.status = "completed"
    pending.attempts[0].status = "completed"
    assert dispatcher.run_once() == ("confirm", "processing", "attempt-1")
    assert dispatcher.run_once() == ("confirm", "submitting", "attempt-1")
    assert service.calls == [("pending", "attempt-1"), ("processing", "attempt-1"), ("submitting", "attempt-1")]


def test_recovery_queries_existing_handle_then_fetches_success_observation():
    handle = _handle("remote-1")
    attempt = _attempt("attempt-1", status="processing", record=_record("attempt-1", phase="submitted", handle=handle))
    storage = StorageFake(_generation(attempt, status="processing"))
    service = ServiceFake(storage)

    def advance(_generation_id, _attempt_id):
        if len(service.calls) == 0:
            attempt.provider_execution = _record("attempt-1", phase="observed", handle=handle, observation=_observation(handle)).model_dump(mode="json")

    service.before_run = advance
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service, transient_backoff_seconds=0)

    assert dispatcher.run_once() == ("query", "generation-1", "attempt-1")
    assert dispatcher.run_once() == ("fetch", "generation-1", "attempt-1")
    assert service.calls == [("generation-1", "attempt-1"), ("generation-1", "attempt-1")]


def test_prepared_proof_repairs_before_query_or_submit():
    handle = _handle("remote-1")
    prepared = {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "handle": handle.to_dict(),
        "relative_path": "playground/videos/result.mp4",
        "sha256": "b" * 64,
        "size_bytes": 12,
    }
    storage = StorageFake(_generation(_attempt(
        "attempt-1",
        status="processing",
        record=_record("attempt-1", phase="observed", handle=handle, observation=_observation(handle), prepared_artifact=prepared),
    ), status="processing"))
    service = ServiceFake(storage)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)

    assert dispatcher.run_once() == ("repair", "generation-1", "attempt-1")
    assert service.calls == [("generation-1", "attempt-1")]


def test_non_transient_needs_confirmation_is_not_auto_retried_even_with_prepared_proof():
    handle = _handle("remote-1")
    prepared = {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "handle": handle.to_dict(),
        "relative_path": "playground/videos/result.mp4",
        "sha256": "b" * 64,
        "size_bytes": 12,
    }
    storage = StorageFake(_generation(_attempt(
        "attempt-1",
        status="needs_confirmation",
        error="provider_credentials_required",
        record=_record("attempt-1", phase="observed", handle=handle, observation=_observation(handle), prepared_artifact=prepared),
    ), status="needs_confirmation"))
    service = ServiceFake(storage)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)

    assert dispatcher.run_once() is None
    assert service.calls == []


def test_transient_backoff_does_not_starve_other_attempts():
    now = {"value": 1.0}
    first = _attempt(
        "attempt-1",
        status="needs_confirmation",
        error="provider_query_unavailable",
        record=_record("attempt-1", phase="submitted", handle=_handle("remote-1")),
    )
    second = _attempt("attempt-2", status="processing", record=_record("attempt-2", phase="submitted", handle=_handle("remote-2")))
    storage = StorageFake(_generation(first, second, status="processing"))
    service = ServiceFake(storage)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service, transient_backoff_seconds=10, time_fn=lambda: now["value"])

    assert dispatcher.run_once() == ("query", "generation-1", "attempt-1")
    assert dispatcher.run_once() == ("query", "generation-1", "attempt-2")
    assert service.calls == [("generation-1", "attempt-1"), ("generation-1", "attempt-2")]


def test_explicit_schedule_advances_once_and_deduplicates():
    entered = threading.Event()
    release = threading.Event()
    storage = StorageFake(_generation(_attempt("attempt-1")))
    service = ServiceFake(storage)

    def block_once(_generation_id, _attempt_id):
        entered.set()
        assert release.wait(3)

    service.before_run = block_once
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)
    try:
        assert dispatcher.schedule("generation-1", "attempt-1")
        assert entered.wait(3)
        assert not dispatcher.schedule("generation-1", "attempt-1")
        release.set()
        assert dispatcher.wait_for_idle(3)
        assert service.calls == [("generation-1", "attempt-1")]
    finally:
        dispatcher.request_stop()
        dispatcher.join(3)


def test_stop_discards_queue_and_stops_service():
    entered = threading.Event()
    release = threading.Event()
    storage = StorageFake(_generation(_attempt("attempt-1"), _attempt("attempt-2")))
    service = ServiceFake(storage)
    service.before_run = lambda *_args: (entered.set(), release.wait(3))
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)

    assert dispatcher.schedule("generation-1", "attempt-1")
    assert entered.wait(3)
    assert dispatcher.schedule("generation-1", "attempt-2")
    dispatcher.request_stop()
    release.set()
    dispatcher.join(3)
    assert service.calls == [("generation-1", "attempt-1")]
    assert service.stop_count == 1
    with pytest.raises(StorageConflictError, match="stopped"):
        dispatcher.schedule("generation-1", "attempt-3")


def test_hard_storage_error_stops_background_worker():
    storage = StorageFake(_generation(_attempt("attempt-1", status="processing", record=_record("attempt-1", phase="submitted", handle=_handle("remote-1"))), status="processing"))
    service = ServiceFake(storage)
    service.before_run = lambda *_args: (_ for _ in ()).throw(StorageError("disk unavailable"))
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service, interval_seconds=0.01)

    dispatcher.start()
    assert dispatcher.wait_for_idle(3)
    dispatcher.join(0.2)
    assert not dispatcher.is_running
    assert service.stop_count == 1


def test_exception_logging_uses_class_name_only(caplog):
    storage = StorageFake(_generation(_attempt("attempt-1")))
    service = ServiceFake(storage)
    service.before_run = lambda *_args: (_ for _ in ()).throw(RuntimeError("provider secret path"))
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)

    with caplog.at_level(logging.WARNING):
        assert dispatcher.schedule("generation-1", "attempt-1")
        assert dispatcher.wait_for_idle(3)

    assert "RuntimeError" in caplog.text
    assert "provider secret path" not in caplog.text


def test_join_passes_remaining_timeout_budget_to_execution_service():
    release = threading.Event()
    storage = StorageFake(_generation(_attempt("attempt-1")))
    service = ServiceFake(storage)
    service.before_run = lambda *_args: release.wait(3)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)
    try:
        assert dispatcher.schedule("generation-1", "attempt-1")
        dispatcher.request_stop()
        dispatcher.join(0.2)
        assert len(service.join_timeouts) == 1
        assert service.join_timeouts[0] is not None
        assert 0 <= service.join_timeouts[0] <= 0.2
    finally:
        release.set()
        dispatcher.join(3)

def test_recovery_confirms_core_video_missing_or_invalid_records_but_not_image_like_rows():
    missing = _generation(_attempt("attempt-1", record=None), generation_id="missing", status="processing")
    missing.attempts[0].provider_execution = None
    invalid = _generation(_attempt("attempt-1", record=None), generation_id="invalid", status="processing")
    invalid.attempts[0].provider_execution = {"not": "a provider record"}
    image_like = _generation(_attempt("attempt-1", record=None), generation_id="image-like", status="processing")
    image_like.attempts[0].provider_execution = None
    image_like.mode = PlaygroundMode.T2I
    image_like.model_id = "wan/wan2.7-image#t2i"
    storage = StorageFake(missing, invalid, image_like)
    service = ServiceFake(storage)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)

    assert dispatcher.run_once() == ("confirm", "missing", "attempt-1")
    missing.attempts[0].status = "completed"
    assert dispatcher.run_once() == ("confirm", "invalid", "attempt-1")
    invalid.attempts[0].status = "completed"
    assert dispatcher.run_once() is None
    assert service.calls == [("missing", "attempt-1"), ("invalid", "attempt-1")]


def test_recovery_repairs_completed_record_with_local_proof():
    handle = _handle("remote-completed")
    proof = {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "handle": handle.to_dict(),
        "relative_path": "playground/videos/result.mp4",
        "sha256": "c" * 64,
        "size_bytes": 12,
    }
    completed = _record("attempt-1", phase="completed", handle=handle)
    completed = completed.model_copy(update={"artifact": proof})
    storage = StorageFake(_generation(_attempt("attempt-1", status="processing", record=completed), status="processing"))
    service = ServiceFake(storage)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)

    assert dispatcher.run_once() == ("repair", "generation-1", "attempt-1")
    assert service.calls == [("generation-1", "attempt-1")]



@pytest.mark.parametrize("model_id", ["wan2.7-t2v", "wan2.7-i2v", "wan2.7-r2v"])
def test_recovery_finds_missing_records_for_actual_playground_model_ids(model_id):
    gen = _generation(_attempt("attempt-1", status="processing"), status="processing")
    gen.model_id = model_id
    gen.mode = PlaygroundMode(model_id.rsplit("-", 1)[-1])
    gen.attempts[0].provider_execution = None
    storage = StorageFake(gen)
    service = ServiceFake(storage)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)
    assert dispatcher.run_once() == ("confirm", gen.id, "attempt-1")


@pytest.mark.parametrize("record_kind", ["missing", "invalid", "completed"])
def test_hard_confirmation_is_not_repeatedly_written_or_repaired(record_kind):
    gen = _generation(_attempt("attempt-1", status="needs_confirmation"), status="needs_confirmation")
    gen.attempts[0].error = "provider_fetch_artifact_missing"
    if record_kind == "missing":
        gen.attempts[0].provider_execution = None
    elif record_kind == "invalid":
        gen.attempts[0].provider_execution = {"invalid": True}
    else:
        gen.attempts[0].provider_execution = _record("attempt-1", phase="completed").model_dump(mode="json")
    storage = StorageFake(gen)
    service = ServiceFake(storage)
    dispatcher = PlaygroundVideoRecoveryDispatcher(storage, service)
    assert dispatcher.run_once() is None
    assert service.calls == []
