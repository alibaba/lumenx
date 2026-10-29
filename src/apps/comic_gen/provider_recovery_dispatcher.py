"""Background dispatcher for Studio provider recovery tasks.

The dispatcher is intentionally lifecycle-only. Constructing it never starts a
thread, and each scan handles at most one committed task snapshot. It delegates
all durable validation and writes to ``ComicGenPipeline`` explicit recovery
methods, so it never submits paid work and never owns a persisted queue.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from contextvars import copy_context
import time
from typing import Callable, Optional

from ...models.provider_tasks import ProviderQueryError, ProviderTaskStatus
from ...utils.json_storage import StorageConflictError, StorageError
from . import provider_recovery
from .asset_image_execution import AssetImageRequestNotFound

logger = logging.getLogger(__name__)

_NONTRANSIENT_PREPARED_REPAIR_ERRORS = {
    "provider_fetch_artifact_invalid",
    "provider_fetch_artifact_missing",
    "provider_fetch_artifact_target_invalid",
}


@dataclass(frozen=True)
class _Candidate:
    action: str
    script_id: str
    task_id: str
    backoff_key: tuple[str, ...]


@dataclass(frozen=True)
class _ImageCandidate:
    action: str
    request_id: str
    attempt_id: str
    execution: dict[str, object]
    backoff_key: tuple[str, ...]


class StudioProviderRecoveryDispatcher:
    """Small app-owned worker for provider query/fetch recovery.

    API/app lifecycle should create this only after an explicit runtime has been
    injected into the pipeline, call ``start()`` when recovery is enabled, then
    shutdown with ``request_stop()`` before closing the pipeline and ``join()``
    after close. ``run_once()`` exists for deterministic tests and admin hooks.
    """

    def __init__(
        self,
        pipeline,
        *,
        interval_seconds: float = 30.0,
        transient_backoff_seconds: float = 60.0,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pipeline = pipeline
        self._interval_seconds = max(0.001, float(interval_seconds))
        self._transient_backoff_seconds = max(0.0, float(transient_backoff_seconds))
        self._time = time_fn
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._backoff_until: dict[tuple[str, ...], float] = {}
        self._last_attempt_at: dict[tuple[str, ...], float] = {}
        self._last_media: str | None = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            self._stop.clear()
            self._wake.clear()
            self._idle.clear()
            context = copy_context()
            thread = threading.Thread(
                target=lambda: context.run(self._run),
                name="lumenx-provider-recovery-dispatcher",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except BaseException:
                self._thread = None
                self._idle.set()
                raise

    def wake(self) -> None:
        self._idle.clear()
        self._wake.set()

    def request_stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def join(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def close(self, timeout: Optional[float] = 5.0) -> None:
        self.request_stop()
        self.join(timeout)

    def wait_for_idle(self, timeout: Optional[float] = None) -> bool:
        return self._idle.wait(timeout)

    def run_once(self):
        """Process at most one eligible committed task snapshot."""
        if self._stop.is_set():
            return None
        self._prune_backoff()
        candidate = self._next_candidate()
        if candidate is None or self._stop.is_set():
            return None
        if isinstance(candidate, _ImageCandidate):
            self._last_media = "image"
            return self._run_image_candidate(candidate)
        self._last_media = "video"
        try:
            if candidate.action in {"repair", "fetch"}:
                before = self._snapshot_task(candidate.script_id, candidate.task_id)
                self._pipeline.fetch_provider_video_task(candidate.script_id, candidate.task_id)
                after = self._snapshot_task(candidate.script_id, candidate.task_id)
                self._record_attempt(candidate.backoff_key)
                if self._is_transient_fetch_result(before, after):
                    self._set_backoff(candidate.backoff_key)
                    return ("transient", candidate.script_id, candidate.task_id)
                if self._is_nontransient_fetch_confirmation(after):
                    self._clear_backoff(candidate.backoff_key)
                    return ("needs_confirmation", candidate.script_id, candidate.task_id)
                self._clear_backoff(candidate.backoff_key)
                result = "repaired" if candidate.action == "repair" else "fetched"
                return (result, candidate.script_id, candidate.task_id)
            if candidate.action == "query":
                before = self._snapshot_task(candidate.script_id, candidate.task_id)
                self._pipeline.query_provider_video_task(candidate.script_id, candidate.task_id)
                after = self._snapshot_task(candidate.script_id, candidate.task_id)
                self._record_attempt(candidate.backoff_key)
                self._set_backoff(candidate.backoff_key)
                if self._is_transient_query_result(before, after):
                    return ("transient", candidate.script_id, candidate.task_id)
                return ("queried", candidate.script_id, candidate.task_id)
        except StorageConflictError:
            self._clear_backoff(candidate.backoff_key)
            return ("stale", candidate.script_id, candidate.task_id)
        except StorageError:
            raise
        except Exception:
            logger.exception(
                "Provider recovery dispatcher failed for %s/%s",
                candidate.script_id,
                candidate.task_id,
            )
            self._record_attempt(candidate.backoff_key)
            self._set_backoff(candidate.backoff_key)
            return ("error", candidate.script_id, candidate.task_id)

    def _run(self) -> None:
        first = True
        while not self._stop.is_set():
            if first:
                first = False
            else:
                self._wake.wait(self._interval_seconds)
                self._wake.clear()
            if self._stop.is_set():
                break
            self._idle.clear()
            try:
                self.run_once()
            except StorageError:
                self._stop.set()
            except Exception:
                logger.exception("Provider recovery dispatcher scan failed")
            finally:
                self._idle.set()

    def _run_image_candidate(self, candidate: _ImageCandidate):
        service = self._pipeline.asset_image_service
        identity = (candidate.request_id, candidate.attempt_id)
        try:
            state = service.recovery_member_state(*identity)
            if state != candidate.action:
                self._record_attempt(candidate.backoff_key)
                self._set_backoff(candidate.backoff_key)
                return ("image_busy" if state == "busy" else "image_stale", *identity)
            if self._stop.is_set():
                return None
            result = service.recover_member_once(*identity, action=candidate.action)
            self._record_attempt(candidate.backoff_key)
            after = service.recovery_member_state(*identity)
            execution = next(
                member.execution for member in result.request.members
                if member.execution.attempt_id == candidate.attempt_id
            )
            if after == "busy":
                self._set_backoff(candidate.backoff_key)
                return ("image_busy", *identity)
            if after in {"blocked", "unknown"}:
                self._clear_backoff(candidate.backoff_key)
                return ("image_needs_confirmation", *identity)
            if execution.model_dump(mode="json") == candidate.execution:
                self._set_backoff(candidate.backoff_key)
                return ("image_transient", *identity)
            if candidate.action == "query":
                # A successful running observation still needs a polling delay;
                # the distinct fetch key permits the next step immediately.
                self._set_backoff(candidate.backoff_key)
                return ("image_queried", *identity)
            if execution.phase == "completed":
                self._clear_backoff(candidate.backoff_key)
                result_name = "image_repaired" if candidate.action == "repair" else "image_fetched"
                return (result_name, *identity)
            self._set_backoff(candidate.backoff_key)
            return ("image_transient", *identity)
        except (StorageConflictError, AssetImageRequestNotFound):
            self._record_attempt(candidate.backoff_key)
            self._set_backoff(candidate.backoff_key)
            return ("image_stale", *identity)
        except StorageError:
            raise
        except ProviderQueryError:
            self._record_attempt(candidate.backoff_key)
            self._set_backoff(candidate.backoff_key)
            return ("image_transient", *identity)
        except Exception:
            logger.exception("Image recovery dispatcher failed for %s/%s", *identity)
            self._record_attempt(candidate.backoff_key)
            self._set_backoff(candidate.backoff_key)
            return ("image_error", *identity)

    def _next_candidate(self) -> _Candidate | _ImageCandidate | None:
        now = self._time()
        video_available = (
            getattr(self._pipeline, "provider_recovery_runtime", None) is not None
            or getattr(self._pipeline, "provider_runtime_selector", None) is not None
        )
        projects = self._pipeline.read_committed_projects() if video_available else []
        repair_candidates: list[_Candidate] = []
        query_candidates: list[_Candidate] = []
        fetch_candidates: list[_Candidate] = []
        live_bases: set[tuple[str, ...]] = set()
        for script in projects:
            for task in getattr(script, "video_tasks", []) or []:
                record = getattr(task, "provider_execution", None)
                task_id = getattr(task, "id", None)
                if record is not None and task_id and getattr(record, "attempt_id", None):
                    live_bases.add(("video", script.id, task_id, record.attempt_id))
                candidate = self._candidate_for_task(script.id, task, now)
                if candidate is None:
                    continue
                if candidate.action == "repair":
                    repair_candidates.append(candidate)
                if candidate.action == "fetch":
                    fetch_candidates.append(candidate)
                elif candidate.action == "query":
                    query_candidates.append(candidate)
        image_candidates = self._image_candidates(now, live_bases)
        # Finish both owner scans before pruning; an early video repair must not
        # erase fairness history for tasks not visited yet.
        self._prune_attempts(live_bases)
        video = (repair_candidates[0] if repair_candidates else
                 self._least_recent_candidate(fetch_candidates) if fetch_candidates else
                 self._least_recent_candidate(query_candidates) if query_candidates else None)
        image = min(image_candidates, key=lambda item: (
            self._last_attempt_at.get(item.backoff_key, -1.0),
            {"repair": 0, "fetch": 1, "query": 2}[item.action],
        )) if image_candidates else None
        if video is not None and image is not None:
            return image if self._last_media == "video" else video
        return image or video

    def _image_candidates(self, now: float, live_bases: set[tuple[str, ...]]) -> list[_ImageCandidate]:
        service = self._pipeline.asset_image_service
        candidates: list[_ImageCandidate] = []
        for request in service.read_committed_requests():
            for member in request.members:
                record = member.execution
                base = ("image", request.owner_kind, request.owner_id, request.id, record.attempt_id)
                live_bases.add(base)
                # Completed history cannot yield work; public reads still
                # verify its file proof when explicitly requested.
                if record.schema_version != 3 or record.handle is None or record.phase == "completed":
                    continue
                try:
                    action = service.recovery_member_state(request.id, record.attempt_id)
                except (StorageConflictError, AssetImageRequestNotFound):
                    continue
                if action not in {"query", "fetch", "repair"}:
                    continue
                key = (*base, action)
                if self._backoff_until.get(key, 0.0) > now:
                    continue
                candidates.append(_ImageCandidate(action, request.id, record.attempt_id,
                                                  record.model_dump(mode="json"), key))
        return candidates

    def _candidate_for_task(self, script_id: str, task, now: float) -> Optional[_Candidate]:
        record = getattr(task, "provider_execution", None)
        if record is None:
            return None
        task_id = getattr(task, "id", None)
        if not task_id:
            return None
        if record.phase == "completed" or record.artifact is not None:
            return None
        status = getattr(task, "status", None)
        error = getattr(task, "error", None)
        base_key = ("video", script_id, task_id, record.attempt_id)
        if record.prepared_artifact is not None:
            if status == "needs_confirmation" and error in _NONTRANSIENT_PREPARED_REPAIR_ERRORS:
                return None
            key = (*base_key, "repair")
            if self._backoff_until.get(key, 0.0) > now:
                return None
            return _Candidate("repair", script_id, task_id, key)
        if status in {"completed", "failed"}:
            return None
        if record.phase == "observed" and self._observed_succeeded(record):
            if status == "needs_confirmation" and error not in {"provider_fetch_unavailable"}:
                return None
            key = (*base_key, "fetch")
            if self._backoff_until.get(key, 0.0) > now:
                return None
            return _Candidate("fetch", script_id, task_id, key)
        if status == "needs_confirmation" and error != "provider_query_unavailable":
            return None
        if record.phase == "observed":
            key = (*base_key, "query")
            if self._backoff_until.get(key, 0.0) > now:
                return None
            return _Candidate("query", script_id, task_id, key)
        if record.phase in {"submitted", "submitting"} and record.handle is not None:
            key = (*base_key, "query")
            if self._backoff_until.get(key, 0.0) > now:
                return None
            return _Candidate("query", script_id, task_id, key)
        return None

    def _observed_succeeded(self, record) -> bool:
        try:
            return provider_recovery.observation_from_record(record).status is ProviderTaskStatus.SUCCEEDED
        except provider_recovery.StudioProviderRecoveryError:
            return False

    def _clear_backoff(self, key: tuple[str, ...]) -> None:
        self._backoff_until.pop(key, None)

    def _snapshot_task(self, script_id: str, task_id: str):
        try:
            script = self._pipeline.read_committed_script(script_id)
        except (StorageError, ValueError):
            return None
        return next((task for task in (getattr(script, "video_tasks", []) or []) if task.id == task_id), None)

    def _is_transient_query_result(self, before, after) -> bool:
        return (
            before is not None
            and after is not None
            and getattr(after, "status", None) == "needs_confirmation"
            and getattr(after, "error", None) == "provider_query_unavailable"
        )

    def _is_transient_fetch_result(self, before, after) -> bool:
        return (
            before is not None
            and after is not None
            and getattr(after, "status", None) == "needs_confirmation"
            and getattr(after, "error", None) == "provider_fetch_unavailable"
        )

    def _is_nontransient_fetch_confirmation(self, after) -> bool:
        return (
            after is not None
            and getattr(after, "status", None) == "needs_confirmation"
            and getattr(after, "error", None) in _NONTRANSIENT_PREPARED_REPAIR_ERRORS
        )

    def _record_attempt(self, key: tuple[str, ...]) -> None:
        self._last_attempt_at[key] = self._time()

    def _least_recent_candidate(self, candidates: list[_Candidate]) -> _Candidate:
        return min(candidates, key=lambda candidate: self._last_attempt_at.get(candidate.backoff_key, -1.0))

    def _set_backoff(self, key: tuple[str, ...]) -> None:
        self._backoff_until[key] = self._time() + self._transient_backoff_seconds

    def _prune_backoff(self) -> None:
        now = self._time()
        expired = [key for key, until in self._backoff_until.items() if until <= now]
        for key in expired:
            self._backoff_until.pop(key, None)

    def _prune_attempts(self, live_bases: set[tuple[str, ...]]) -> None:
        stale = [key for key in self._last_attempt_at if key[:-1] not in live_bases]
        for key in stale:
            self._last_attempt_at.pop(key, None)
