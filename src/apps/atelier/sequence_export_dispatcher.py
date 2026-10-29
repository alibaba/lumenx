"""Bounded app-owned scheduling of already-persisted Atelier export jobs."""

from __future__ import annotations

from collections import deque
from contextvars import copy_context
import logging
import threading

from ...utils.json_storage import StorageConflictError

logger = logging.getLogger(__name__)


class SequenceExportDispatcher:
    """Run one render at a time; durable state belongs exclusively to the service."""

    def __init__(self, service, *, max_pending: int = 32) -> None:
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError("max_pending must be a positive integer")
        self._service = service
        self._max_pending = max_pending
        self._condition = threading.Condition()
        self._queue: deque[tuple[str, str]] = deque()
        self._scheduled: set[tuple[str, str]] = set()
        self._stopped = False
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def schedule(self, project_id: str, job_id: str) -> bool:
        key = (project_id, job_id)
        with self._condition:
            if self._stopped:
                raise StorageConflictError("Atelier export dispatcher is stopped")
            if key in self._scheduled:
                return False
            if len(self._queue) >= self._max_pending:
                raise StorageConflictError("Atelier export queue is full; saved job can be resumed")
            self._queue.append(key)
            self._scheduled.add(key)
            self._idle.clear()
            if self._thread is None:
                context = copy_context()
                self._thread = threading.Thread(
                    target=lambda: context.run(self._run),
                    name="lumenx-atelier-export",
                    daemon=True,
                )
                try:
                    self._thread.start()
                except BaseException:
                    self._thread = None
                    self._queue.remove(key)
                    self._scheduled.remove(key)
                    self._idle.set()
                    raise
            self._condition.notify()
            return True

    def request_stop(self) -> None:
        with self._condition:
            if self._stopped:
                return
            self._stopped = True
            for key in self._queue:
                self._scheduled.discard(key)
            self._queue.clear()
            if not self._scheduled:
                self._idle.set()
            self._condition.notify_all()
        # Do not hold the dispatcher mutex while taking service/storage locks.
        self._service.request_stop()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._stopped or bool(self._queue))
                if self._stopped:
                    return
                key = self._queue.popleft()
            try:
                self._service.run_sequence_export_job(*key)
            except Exception as exc:
                # The service owns unknown-outcome recovery; never replay here.
                # Avoid logging arbitrary exception content or media paths.
                logger.warning("Atelier export worker returned %s", type(exc).__name__)
            finally:
                with self._condition:
                    self._scheduled.discard(key)
                    if not self._scheduled:
                        self._idle.set()
