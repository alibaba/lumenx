"""Bounded app-owned dispatcher for durable Playground provider video attempts."""

from __future__ import annotations

from collections import deque
from contextvars import copy_context
from dataclasses import dataclass
import logging
import threading
import time
from typing import Any, Callable, cast

from .provider_video_execution import is_async_video_record, is_core_video_generation, is_transient_video_result
from ...models import provider_recovery
from ...models.provider_execution import ProviderExecutionRecord
from ...models.provider_tasks import ProviderTaskStatus
from ...utils.json_storage import StorageConflictError, StorageError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Candidate:
    action: str
    generation_id: str
    attempt_id: str
    backoff_key: tuple[str, str, str]


class PlaygroundVideoRecoveryDispatcher:
    """Run one Playground provider video step at a time."""

    def __init__(
        self,
        storage,
        execution_service,
        *,
        interval_seconds: float = 30.0,
        transient_backoff_seconds: float = 60.0,
        time_fn: Callable[[], float] = time.monotonic,
        max_pending: int = 32,
        scan_limit: int = 10000,
    ) -> None:
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError("max_pending must be a positive integer")
        self._storage = storage
        self._execution_service = execution_service
        self._interval_seconds = max(0.001, float(interval_seconds))
        self._transient_backoff_seconds = max(0.0, float(transient_backoff_seconds))
        self._time = time_fn
        self._max_pending = max_pending
        self._scan_limit = scan_limit
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
        with self._condition:
            if self.is_running:
                return
            if self._stopped:
                raise StorageConflictError("Playground video dispatcher is stopped")
            self._idle.clear()
            self._wake_requested = True
            self._start_thread_locked()
            self._condition.notify_all()

    def schedule(self, generation_id: str, attempt_id: str) -> bool:
        key = (generation_id, attempt_id)
        with self._condition:
            if self._stopped:
                raise StorageConflictError("Playground video dispatcher is stopped")
            if key in self._scheduled:
                return False
            if len(self._queue) >= self._max_pending:
                raise StorageConflictError("Playground video queue is full; saved attempt can be resumed")
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

    def join(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        thread = self._thread
        thread_stopped = True
        if thread is not None:
            thread.join(timeout)
            thread_stopped = not thread.is_alive()
        service_stopped = True
        join = getattr(self._execution_service, "join", None)
        if callable(join):
            if deadline is None:
                service_stopped = bool(join())
            else:
                service_stopped = bool(join(timeout=max(0.0, deadline - time.monotonic())))
        return thread_stopped and service_stopped

    def close(self, timeout: float | None = 5.0) -> bool:
        self.request_stop()
        return self.join(timeout)

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def run_once(self):
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
            name="lumenx-playground-video",
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
                logger.warning("Playground video dispatcher scan returned %s", type(exc).__name__)
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
        generation_id, attempt_id = key
        try:
            self._execution_service.run_attempt(generation_id, attempt_id)
            return ("scheduled", generation_id, attempt_id)
        except StorageConflictError:
            return ("stale", generation_id, attempt_id)
        except StorageError:
            raise
        except Exception as exc:
            logger.warning("Playground video scheduled attempt returned %s", type(exc).__name__)
            return ("error", generation_id, attempt_id)
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
            if candidate.action == "confirm_generation":
                self._mark_generation_confirmation(candidate.generation_id)
                self._record_attempt(candidate.backoff_key)
                self._clear_backoff(candidate.backoff_key)
                return (candidate.action, candidate.generation_id, candidate.attempt_id)
            ack = self._execution_service.run_attempt(candidate.generation_id, candidate.attempt_id)
            self._record_attempt(candidate.backoff_key)
            if is_transient_video_result(ack):
                self._set_backoff(candidate.backoff_key)
            else:
                self._clear_backoff(candidate.backoff_key)
            return (candidate.action, candidate.generation_id, candidate.attempt_id)
        except StorageConflictError:
            self._clear_backoff(candidate.backoff_key)
            return ("stale", candidate.generation_id, candidate.attempt_id)
        except StorageError:
            raise
        except Exception as exc:
            logger.warning("Playground video recovery attempt returned %s", type(exc).__name__)
            self._record_attempt(candidate.backoff_key)
            self._set_backoff(candidate.backoff_key)
            return ("error", candidate.generation_id, candidate.attempt_id)

    def _next_recovery_candidate(self) -> _Candidate | None:
        now = self._time()
        submit_candidates: list[_Candidate] = []
        confirm_candidates: list[_Candidate] = []
        query_candidates: list[_Candidate] = []
        fetch_candidates: list[_Candidate] = []
        repair_candidates: list[_Candidate] = []
        live_bases: set[tuple[str, str]] = set()
        for gen in self._list_generations_for_scan():
            attempts = list(getattr(gen, "attempts", []))
            if not attempts and is_core_video_generation(gen) and getattr(gen, "status", None) not in {"completed", "failed", "cancelled", "needs_confirmation"}:
                candidate = self._candidate_if_ready("confirm_generation", gen.id, "", now)
                if candidate is not None:
                    confirm_candidates.append(candidate)
            for attempt in attempts:
                live_bases.add((gen.id, attempt.attempt_id))
                candidate = self._candidate_for_attempt(gen, attempt, now)
                if candidate is None:
                    continue
                if candidate.action == "repair":
                    repair_candidates.append(candidate)
                elif candidate.action == "fetch":
                    fetch_candidates.append(candidate)
                elif candidate.action == "query":
                    query_candidates.append(candidate)
                elif candidate.action == "confirm":
                    confirm_candidates.append(candidate)
                elif candidate.action == "submit":
                    submit_candidates.append(candidate)
        self._prune_attempts(live_bases)
        for candidates in (repair_candidates, fetch_candidates, query_candidates, submit_candidates, confirm_candidates):
            if candidates:
                return self._least_recent_candidate(candidates)
        return None

    def _mark_generation_confirmation(self, generation_id: str) -> None:
        get_generation = getattr(self._storage, "get_generation", None)
        update_generation = getattr(self._storage, "update_generation", None)
        if not callable(get_generation) or not callable(update_generation):
            return
        gen = get_generation(generation_id)
        if gen is None or getattr(gen, "status", None) in {"completed", "failed", "cancelled", "needs_confirmation"}:
            return
        gen = cast(Any, gen)
        updated = gen.model_copy(deep=True)
        updated.status = "needs_confirmation"
        updated.error = "playground_core_video_record_missing"
        update_generation(updated)

    def _list_generations_for_scan(self) -> list[Any]:
        list_all = getattr(self._storage, "list_all_generations", None)
        if callable(list_all):
            return list(cast(Any, list_all()))
        return list(self._storage.list_history(limit=self._scan_limit, offset=0))

    def _candidate_for_attempt(self, gen, attempt, now: float) -> _Candidate | None:
        if not is_core_video_generation(gen):
            return None
        if getattr(attempt, "status", None) in {"completed", "failed", "cancelled"}:
            return None
        if getattr(attempt, "status", None) == "needs_confirmation" and getattr(attempt, "error", None) not in {"provider_query_unavailable", "provider_fetch_unavailable"}:
            return None
        raw_record = getattr(attempt, "provider_execution", None)
        attempt_id = getattr(attempt, "attempt_id", None)
        if not attempt_id:
            return None
        if raw_record is None:
            return self._candidate_if_ready("confirm", gen.id, attempt_id, now)
        try:
            record = ProviderExecutionRecord.model_validate(raw_record)
        except (TypeError, ValueError):
            return self._candidate_if_ready("confirm", gen.id, attempt_id, now)
        if not is_async_video_record(record):
            return None
        if record.phase == "cancelled":
            return None
        if record.phase == "completed":
            if record.artifact is not None or record.prepared_artifact is not None:
                return self._candidate_if_ready("repair", gen.id, attempt_id, now)
            return self._candidate_if_ready("confirm", gen.id, attempt_id, now)
        if record.prepared_artifact is not None:
            return self._candidate_if_ready("repair", gen.id, attempt_id, now)
        if record.phase == "accepted":
            if attempt.status == "pending":
                return self._candidate_if_ready("submit", gen.id, attempt_id, now)
            return self._candidate_if_ready("confirm", gen.id, attempt_id, now)
        if record.phase == "submitting":
            return self._candidate_if_ready("confirm", gen.id, attempt_id, now)
        if record.phase == "observed" and self._observed_succeeded(record):
            return self._candidate_if_ready("fetch", gen.id, attempt_id, now)
        if record.phase in {"submitted", "observed"} and record.handle is not None:
            return self._candidate_if_ready("query", gen.id, attempt_id, now)
        return None

    def _candidate_if_ready(self, action: str, generation_id: str, attempt_id: str, now: float) -> _Candidate | None:
        key = (generation_id, attempt_id, action)
        if self._backoff_until.get(key, 0.0) > now:
            return None
        return _Candidate(action, generation_id, attempt_id, key)

    def _observed_succeeded(self, record) -> bool:
        try:
            return provider_recovery.observation_from_record(record).status is ProviderTaskStatus.SUCCEEDED
        except provider_recovery.ProviderRecoveryError:
            return False

    def _record_attempt(self, key: tuple[str, str, str]) -> None:
        self._last_attempt_at[key] = self._time()

    def _least_recent_candidate(self, candidates: list[_Candidate]) -> _Candidate:
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
