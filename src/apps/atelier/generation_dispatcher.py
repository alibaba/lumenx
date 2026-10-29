"""Bounded app-owned scheduling for persisted Atelier generation attempts."""

from __future__ import annotations

from collections import deque
from contextvars import copy_context
from dataclasses import dataclass
import logging
import threading
import time
from typing import Callable

from ...models import provider_recovery
from ...models.provider_tasks import ProviderTaskStatus
from ...utils.json_storage import StorageConflictError, StorageError

logger = logging.getLogger(__name__)

_TRANSIENT_ERRORS = {"provider_query_unavailable", "provider_fetch_unavailable"}


@dataclass(frozen=True)
class _RecoveryCandidate:
    action: str
    project_id: str
    attempt_id: str
    backoff_key: tuple[str, str, str]


class AtelierGenerationDispatcher:
    """Run one authorized generation attempt at a time.

    ``schedule`` is the only submit-authorizing path for newly admitted
    ``accepted`` attempts. ``start`` and recovery scans only resume attempts
    that already contain durable provider evidence, such as a handle,
    observation, or prepared artifact proof.
    """

    def __init__(
        self,
        storage,
        execution_service,
        *,
        interval_seconds: float = 30.0,
        transient_backoff_seconds: float = 60.0,
        time_fn: Callable[[], float] = time.monotonic,
        max_pending: int = 32,
    ) -> None:
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError("max_pending must be a positive integer")
        self._storage = storage
        self._execution_service = execution_service
        self._interval_seconds = max(0.001, float(interval_seconds))
        self._transient_backoff_seconds = max(0.0, float(transient_backoff_seconds))
        self._time = time_fn
        self._max_pending = max_pending
        self._condition = threading.Condition()
        self._queue: deque[tuple[str, str]] = deque()
        self._scheduled: set[tuple[str, str]] = set()
        self._stopped = False
        self._wake_requested = False
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()
        self._backoff_until: dict[tuple[str, str, str], float] = {}
        self._last_attempt_at: dict[tuple[str, str, str], float] = {}

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        """Start recovery scanning without granting submit rights to accepted attempts."""
        with self._condition:
            if self.is_running:
                return
            if self._stopped:
                raise StorageConflictError("Atelier generation dispatcher is stopped")
            self._idle.clear()
            self._wake_requested = True
            self._start_thread_locked()
            self._condition.notify_all()

    def schedule(self, project_id: str, attempt_id: str) -> bool:
        """Queue an explicitly admitted attempt for one execution step."""
        key = (project_id, attempt_id)
        with self._condition:
            if self._stopped:
                raise StorageConflictError("Atelier generation dispatcher is stopped")
            if key in self._scheduled:
                return False
            if len(self._queue) >= self._max_pending:
                raise StorageConflictError("Atelier generation queue is full; saved attempt can be resumed")
            self._queue.append(key)
            self._scheduled.add(key)
            self._idle.clear()
            self._wake_requested = True
            try:
                self._start_thread_locked()
            except BaseException:
                self._queue.remove(key)
                self._scheduled.remove(key)
                if not self._scheduled:
                    self._idle.set()
                raise
            self._condition.notify_all()
            return True

    def wake(self) -> None:
        with self._condition:
            if not self._stopped:
                self._idle.clear()
                self._wake_requested = True
                self._condition.notify_all()

    def request_stop(self) -> None:
        should_stop_service = False
        with self._condition:
            if not self._stopped:
                self._stopped = True
                should_stop_service = True
            for key in self._queue:
                self._scheduled.discard(key)
            self._queue.clear()
            self._wake_requested = True
            if not self._scheduled:
                self._idle.set()
            self._condition.notify_all()
        if should_stop_service:
            self._execution_service.request_stop()

    def join(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        join = getattr(self._execution_service, "join", None)
        if callable(join):
            if deadline is None:
                join()
            else:
                remaining = max(0.0, deadline - time.monotonic())
                join(timeout=remaining)

    def close(self, timeout: float | None = 5.0) -> None:
        self.request_stop()
        self.join(timeout)

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def run_once(self):
        """Advance one queued attempt or one recovery-eligible attempt."""
        key = self._pop_queued()
        if key is not None:
            return self._run_scheduled(key)
        return self._run_recovery_once()

    def _start_thread_locked(self) -> None:
        if self.is_running:
            return
        context = copy_context()
        thread = threading.Thread(
            target=lambda: context.run(self._run),
            name="lumenx-atelier-generation",
            daemon=True,
        )
        self._thread = thread
        try:
            thread.start()
        except BaseException:
            self._thread = None
            self._idle.set()
            raise

    def _run(self) -> None:
        first = True
        while True:
            with self._condition:
                if self._stopped:
                    return
                if not first:
                    self._condition.wait_for(
                        lambda: self._stopped or bool(self._queue) or self._wake_requested,
                        timeout=self._interval_seconds,
                    )
                    if self._stopped:
                        return
                self._wake_requested = False
            try:
                first = False
                self._idle.clear()
                result = self.run_once()
            except StorageError:
                with self._condition:
                    self._stopped = True
                    self._queue.clear()
                    self._scheduled.clear()
                    self._idle.set()
                    self._condition.notify_all()
                self._execution_service.request_stop()
                return
            except Exception as exc:
                logger.warning("Atelier generation dispatcher scan returned %s", type(exc).__name__)
                result = None
            finally:
                with self._condition:
                    if not self._queue:
                        self._idle.set()
            if result is None:
                continue

    def _pop_queued(self) -> tuple[str, str] | None:
        with self._condition:
            if self._stopped or not self._queue:
                return None
            return self._queue.popleft()

    def _run_scheduled(self, key: tuple[str, str]):
        project_id, attempt_id = key
        try:
            self._execution_service.run_attempt(project_id, attempt_id)
            return ("scheduled", project_id, attempt_id)
        except StorageConflictError:
            return ("stale", project_id, attempt_id)
        except StorageError:
            raise
        except Exception as exc:
            logger.warning("Atelier generation scheduled attempt returned %s", type(exc).__name__)
            return ("error", project_id, attempt_id)
        finally:
            with self._condition:
                self._scheduled.discard(key)
                if not self._scheduled:
                    self._idle.set()

    def _run_recovery_once(self):
        if self._is_stopped():
            return None
        self._prune_backoff()
        candidate = self._next_recovery_candidate()
        if candidate is None:
            return None
        try:
            ack = self._execution_service.run_attempt(candidate.project_id, candidate.attempt_id)
            self._record_attempt(candidate.backoff_key)
            if self._is_transient_result(ack):
                self._set_backoff(candidate.backoff_key)
            else:
                self._clear_backoff(candidate.backoff_key)
            return (candidate.action, candidate.project_id, candidate.attempt_id)
        except StorageConflictError:
            self._clear_backoff(candidate.backoff_key)
            return ("stale", candidate.project_id, candidate.attempt_id)
        except StorageError:
            raise
        except Exception as exc:
            logger.warning("Atelier generation recovery attempt returned %s", type(exc).__name__)
            self._record_attempt(candidate.backoff_key)
            self._set_backoff(candidate.backoff_key)
            return ("error", candidate.project_id, candidate.attempt_id)

    def _next_recovery_candidate(self) -> _RecoveryCandidate | None:
        now = self._time()
        query_candidates: list[_RecoveryCandidate] = []
        fetch_candidates: list[_RecoveryCandidate] = []
        repair_candidates: list[_RecoveryCandidate] = []
        live_bases: set[tuple[str, str]] = set()
        for project in self._storage.list_projects():
            for attempt in getattr(project, "generation_attempts", []) or []:
                live_bases.add((project.id, attempt.attempt_id))
                candidate = self._candidate_for_attempt(project.id, attempt, now)
                if candidate is None:
                    continue
                if candidate.action == "repair":
                    repair_candidates.append(candidate)
                elif candidate.action == "fetch":
                    fetch_candidates.append(candidate)
                elif candidate.action == "query":
                    query_candidates.append(candidate)
        self._prune_attempts(live_bases)
        if repair_candidates:
            return self._least_recent_candidate(repair_candidates)
        if fetch_candidates:
            return self._least_recent_candidate(fetch_candidates)
        if query_candidates:
            return self._least_recent_candidate(query_candidates)
        return None

    def _candidate_for_attempt(self, project_id: str, attempt, now: float) -> _RecoveryCandidate | None:
        if getattr(attempt, "status", None) in {"completed", "failed", "cancelled"}:
            return None
        record = getattr(attempt, "provider_execution", None)
        if record is None:
            return None
        attempt_id = getattr(attempt, "attempt_id", None)
        if not attempt_id or record.phase in {"accepted", "cancelled", "completed"}:
            return None
        if getattr(attempt, "status", None) == "needs_confirmation" and getattr(attempt, "error_code", None) not in _TRANSIENT_ERRORS:
            return None
        if record.prepared_artifact is not None:
            return self._candidate_if_ready("repair", project_id, attempt_id, now)
        if record.phase == "submitting":
            return None
        if record.phase == "observed" and self._observed_succeeded(record):
            return self._candidate_if_ready("fetch", project_id, attempt_id, now)
        if record.phase in {"submitted", "observed"} and record.handle is not None:
            return self._candidate_if_ready("query", project_id, attempt_id, now)
        return None

    def _candidate_if_ready(self, action: str, project_id: str, attempt_id: str, now: float) -> _RecoveryCandidate | None:
        key = (project_id, attempt_id, action)
        if self._backoff_until.get(key, 0.0) > now:
            return None
        return _RecoveryCandidate(action, project_id, attempt_id, key)

    def _observed_succeeded(self, record) -> bool:
        try:
            return provider_recovery.observation_from_record(record).status is ProviderTaskStatus.SUCCEEDED
        except provider_recovery.ProviderRecoveryError:
            return False

    def _is_transient_result(self, ack) -> bool:
        attempt = getattr(ack, "attempt", None)
        return (
            getattr(attempt, "status", None) == "needs_confirmation"
            and getattr(attempt, "error_code", None) in _TRANSIENT_ERRORS
        )

    def _record_attempt(self, key: tuple[str, str, str]) -> None:
        self._last_attempt_at[key] = self._time()

    def _least_recent_candidate(self, candidates: list[_RecoveryCandidate]) -> _RecoveryCandidate:
        return min(candidates, key=lambda candidate: self._last_attempt_at.get(candidate.backoff_key, -1.0))

    def _set_backoff(self, key: tuple[str, str, str]) -> None:
        self._backoff_until[key] = self._time() + self._transient_backoff_seconds

    def _clear_backoff(self, key: tuple[str, str, str]) -> None:
        self._backoff_until.pop(key, None)

    def _prune_backoff(self) -> None:
        now = self._time()
        expired = [key for key, until in self._backoff_until.items() if until <= now]
        for key in expired:
            self._backoff_until.pop(key, None)

    def _prune_attempts(self, live_bases: set[tuple[str, str]]) -> None:
        stale = [key for key in self._last_attempt_at if key[:2] not in live_bases]
        for key in stale:
            self._last_attempt_at.pop(key, None)

    def _is_stopped(self) -> bool:
        with self._condition:
            return self._stopped
