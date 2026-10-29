"""Atelier generation dispatcher only advances authorized persisted attempts."""

from __future__ import annotations

import logging
import threading

import pytest

from src.apps.atelier.generation_dispatcher import AtelierGenerationDispatcher
from src.apps.atelier.generation_execution import GenerationAttemptAck
from src.apps.atelier.models import AtelierGenerationAttempt, AtelierGenerationFirstFrameSource, AtelierProject
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
        provider_model_id="wan2.7-i2v",
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
        error_code=None,
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
        attempt_id=attempt_id,
        phase=phase,
        provider_model_id="wan2.7-i2v",
        intent={},
        media=[],
        scope=SCOPE.to_dict(),
        credential_ref="credential-ref",
        fingerprint=HASH,
        handle=handle.to_dict() if handle is not None else None,
        observation=observation.to_dict() if observation is not None else None,
        prepared_artifact=prepared_artifact,
    )


def _attempt(
    attempt_id: str,
    *,
    status: str = "pending",
    record: ProviderExecutionRecord | None = None,
    error_code: str | None = None,
) -> AtelierGenerationAttempt:
    return AtelierGenerationAttempt(
        project_id="p",
        node_id="video",
        candidate_id=f"candidate-{attempt_id}",
        attempt_id=attempt_id,
        attempt_revision=0,
        status=status,
        error_code=error_code,
        first_frame_source=AtelierGenerationFirstFrameSource(node_id="image", media_url="uploads/frame.png"),
        provider_execution=record or _record(attempt_id),
        created_at=1.0,
        updated_at=1.0,
    )


def _project(*attempts: AtelierGenerationAttempt) -> AtelierProject:
    return AtelierProject(id="p", title="Canvas", created_at=1.0, updated_at=1.0, generation_attempts=list(attempts))


class StorageFake:
    def __init__(self, project: AtelierProject):
        self.project = project

    def list_projects(self):
        return [self.project]

    def get_project(self, project_id: str):
        if project_id != self.project.id:
            raise ValueError("missing project")
        return self.project


class ServiceFake:
    def __init__(self, storage: StorageFake):
        self.storage = storage
        self.calls: list[tuple[str, str]] = []
        self.stop_count = 0
        self.before_run = None
        self.join_timeouts = []

    def read_attempt(self, project_id: str, attempt_id: str):
        attempt = self._attempt(attempt_id)
        return GenerationAttemptAck(project=self.storage.project, attempt=attempt, candidate=None)

    def run_attempt(self, project_id: str, attempt_id: str):
        if self.before_run is not None:
            self.before_run(project_id, attempt_id)
        self.calls.append((project_id, attempt_id))
        attempt = self._attempt(attempt_id)
        return GenerationAttemptAck(project=self.storage.project, attempt=attempt, candidate=None)

    def request_stop(self):
        self.stop_count += 1

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)

    def _attempt(self, attempt_id: str):
        return next(attempt for attempt in self.storage.project.generation_attempts if attempt.attempt_id == attempt_id)


def test_start_and_recovery_scan_do_not_submit_plain_accepted_attempts():
    storage = StorageFake(_project(_attempt("attempt-1")))
    service = ServiceFake(storage)
    dispatcher = AtelierGenerationDispatcher(storage, service, interval_seconds=0.01)
    try:
        dispatcher.start()
        assert dispatcher.wait_for_idle(3)
        assert dispatcher.run_once() is None
        assert service.calls == []
    finally:
        dispatcher.request_stop()
        dispatcher.join(3)


def test_unknown_submitting_without_handle_is_not_replayed():
    storage = StorageFake(_project(_attempt("attempt-1", status="processing", record=_record("attempt-1", phase="submitting"))))
    service = ServiceFake(storage)
    dispatcher = AtelierGenerationDispatcher(storage, service)

    assert dispatcher.run_once() is None
    assert service.calls == []


def test_incoherent_submitting_with_handle_is_not_auto_recovered():
    storage = StorageFake(_project(_attempt(
        "attempt-1",
        status="processing",
        record=_record("attempt-1", phase="submitting", handle=_handle("remote-1")),
    )))
    service = ServiceFake(storage)
    dispatcher = AtelierGenerationDispatcher(storage, service)

    assert dispatcher.run_once() is None
    assert service.calls == []


def test_explicit_schedule_advances_accepted_attempt_once_and_deduplicates():
    entered = threading.Event()
    release = threading.Event()
    storage = StorageFake(_project(_attempt("attempt-1")))
    service = ServiceFake(storage)

    def block_once(_project_id, _attempt_id):
        entered.set()
        assert release.wait(3)

    service.before_run = block_once
    dispatcher = AtelierGenerationDispatcher(storage, service)
    try:
        assert dispatcher.schedule("p", "attempt-1")
        assert entered.wait(3)
        assert not dispatcher.schedule("p", "attempt-1")
        release.set()
        assert dispatcher.wait_for_idle(3)
        assert service.calls == [("p", "attempt-1")]
    finally:
        dispatcher.request_stop()
        dispatcher.join(3)


def test_schedule_wakes_worker_that_is_waiting_for_recovery_interval():
    storage = StorageFake(_project(_attempt("attempt-1")))
    service = ServiceFake(storage)
    dispatcher = AtelierGenerationDispatcher(storage, service, interval_seconds=30)
    try:
        dispatcher.start()
        assert dispatcher.wait_for_idle(3)
        assert dispatcher.schedule("p", "attempt-1")
        assert dispatcher.wait_for_idle(0.5)
        assert service.calls == [("p", "attempt-1")]
    finally:
        dispatcher.request_stop()
        dispatcher.join(3)


def test_recovery_queries_existing_handle_then_fetches_success_observation():
    handle = _handle("remote-1")
    attempt = _attempt("attempt-1", status="processing", record=_record("attempt-1", phase="submitted", handle=handle))
    storage = StorageFake(_project(attempt))
    service = ServiceFake(storage)

    def advance(_project_id, _attempt_id):
        if len(service.calls) == 0:
            attempt.provider_execution = _record("attempt-1", phase="observed", handle=handle, observation=_observation(handle))

    service.before_run = advance
    dispatcher = AtelierGenerationDispatcher(storage, service, transient_backoff_seconds=0)

    assert dispatcher.run_once() == ("query", "p", "attempt-1")
    assert dispatcher.run_once() == ("fetch", "p", "attempt-1")
    assert service.calls == [("p", "attempt-1"), ("p", "attempt-1")]


def test_three_recovery_tasks_rotate_fairly_before_repeating():
    attempts = []
    for index in range(3):
        attempt_id = f"attempt-{index}"
        handle = _handle(f"remote-{index}")
        attempts.append(_attempt(attempt_id, status="processing", record=_record(attempt_id, phase="submitted", handle=handle)))
    storage = StorageFake(_project(*attempts))
    service = ServiceFake(storage)
    dispatcher = AtelierGenerationDispatcher(storage, service, transient_backoff_seconds=0, time_fn=lambda: 1.0)

    assert [dispatcher.run_once() for _ in range(3)] == [
        ("query", "p", "attempt-0"),
        ("query", "p", "attempt-1"),
        ("query", "p", "attempt-2"),
    ]


def test_transient_backoff_does_not_starve_other_attempts():
    now = {"value": 1.0}
    first = _attempt(
        "attempt-1",
        status="needs_confirmation",
        error_code="provider_query_unavailable",
        record=_record("attempt-1", phase="submitted", handle=_handle("remote-1")),
    )
    second = _attempt("attempt-2", status="processing", record=_record("attempt-2", phase="submitted", handle=_handle("remote-2")))
    storage = StorageFake(_project(first, second))
    service = ServiceFake(storage)
    dispatcher = AtelierGenerationDispatcher(
        storage,
        service,
        transient_backoff_seconds=10,
        time_fn=lambda: now["value"],
    )

    assert dispatcher.run_once() == ("query", "p", "attempt-1")
    assert dispatcher.run_once() == ("query", "p", "attempt-2")
    assert service.calls == [("p", "attempt-1"), ("p", "attempt-2")]


def test_needs_confirmation_non_transient_error_is_not_auto_retried_even_with_prepared_proof():
    handle = _handle("remote-1")
    prepared = {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "handle": handle.to_dict(),
        "relative_path": "video/atelier-candidate-attempt.mp4",
        "sha256": "b" * 64,
        "size_bytes": 12,
    }
    storage = StorageFake(_project(_attempt(
        "attempt-1",
        status="needs_confirmation",
        error_code="provider_credentials_required",
        record=_record("attempt-1", phase="observed", handle=handle, observation=_observation(handle), prepared_artifact=prepared),
    )))
    service = ServiceFake(storage)
    dispatcher = AtelierGenerationDispatcher(storage, service)

    assert dispatcher.run_once() is None
    assert service.calls == []


def test_hard_storage_error_stops_background_worker():
    storage = StorageFake(_project(_attempt("attempt-1", status="processing", record=_record("attempt-1", phase="submitted", handle=_handle("remote-1")))))
    service = ServiceFake(storage)
    service.before_run = lambda *_args: (_ for _ in ()).throw(StorageError("disk unavailable"))
    dispatcher = AtelierGenerationDispatcher(storage, service, interval_seconds=0.01)

    dispatcher.start()
    assert dispatcher.wait_for_idle(3)
    dispatcher.join(0.2)
    assert not dispatcher.is_running
    assert service.stop_count == 1


def test_stop_discards_memory_queue_and_stops_service():
    entered = threading.Event()
    release = threading.Event()
    storage = StorageFake(_project(_attempt("attempt-1"), _attempt("attempt-2")))
    service = ServiceFake(storage)
    service.before_run = lambda *_args: (entered.set(), release.wait(3))
    dispatcher = AtelierGenerationDispatcher(storage, service)

    assert dispatcher.schedule("p", "attempt-1")
    assert entered.wait(3)
    assert dispatcher.schedule("p", "attempt-2")
    dispatcher.request_stop()
    release.set()
    dispatcher.join(3)
    assert service.calls == [("p", "attempt-1")]
    assert service.stop_count == 1
    with pytest.raises(StorageConflictError, match="stopped"):
        dispatcher.schedule("p", "attempt-3")


def test_thread_start_failure_leaves_no_phantom_dispatch(monkeypatch):
    storage = StorageFake(_project(_attempt("attempt-1")))
    service = ServiceFake(storage)
    dispatcher = AtelierGenerationDispatcher(storage, service)
    real_start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("no threads")))
    with pytest.raises(RuntimeError, match="no threads"):
        dispatcher.schedule("p", "attempt-1")
    assert not dispatcher.is_running
    assert dispatcher.wait_for_idle(0)
    monkeypatch.setattr(threading.Thread, "start", real_start)
    try:
        assert dispatcher.schedule("p", "attempt-1")
        assert dispatcher.wait_for_idle(3)
    finally:
        dispatcher.request_stop()
        dispatcher.join(3)


def test_join_passes_remaining_timeout_budget_to_service_join():
    release = threading.Event()
    storage = StorageFake(_project(_attempt("attempt-1")))
    service = ServiceFake(storage)
    service.before_run = lambda *_args: release.wait(3)
    dispatcher = AtelierGenerationDispatcher(storage, service)
    try:
        assert dispatcher.schedule("p", "attempt-1")
        dispatcher.request_stop()
        dispatcher.join(0.2)
        assert len(service.join_timeouts) == 1
        assert service.join_timeouts[0] is not None
        assert 0 <= service.join_timeouts[0] <= 0.2
    finally:
        release.set()
        dispatcher.join(3)


def test_exception_logging_uses_class_name_only(caplog):
    storage = StorageFake(_project(_attempt("attempt-1")))
    service = ServiceFake(storage)
    service.before_run = lambda *_args: (_ for _ in ()).throw(RuntimeError("provider secret path"))
    dispatcher = AtelierGenerationDispatcher(storage, service)

    with caplog.at_level(logging.WARNING):
        assert dispatcher.schedule("p", "attempt-1")
        assert dispatcher.wait_for_idle(3)

    assert "RuntimeError" in caplog.text
    assert "provider secret path" not in caplog.text
