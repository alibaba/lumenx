"""Typed execution router for shared Atelier generation endpoints."""

from __future__ import annotations

import time

from .models import AtelierGenerationAttempt, AtelierMediaGenerationAttempt
from .storage import AtelierStorage


class AtelierGenerationExecutionRouter:
    """Dispatch public attempt operations by the persisted attempt model.

    The dispatcher and HTTP routes use one service surface. This router does not
    infer runtime authority; it only reads the already-committed attempt type and
    delegates to the corresponding execution service.
    """

    def __init__(self, storage: AtelierStorage, video_service, media_service) -> None:
        self._storage = storage
        self._video_service = video_service
        self._media_service = media_service

    def request_stop(self) -> None:
        self._video_service.request_stop()
        self._media_service.request_stop()

    def join(self, timeout: float | None = 5.0) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        video_done = self._join_one(self._video_service, timeout)
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        media_done = self._join_one(self._media_service, remaining)
        return bool(video_done and media_done)

    def read_attempt(self, project_id: str, attempt_id: str):
        return self._service_for(project_id, attempt_id).read_attempt(project_id, attempt_id)

    def run_attempt(self, project_id: str, attempt_id: str):
        return self._service_for(project_id, attempt_id).run_attempt(project_id, attempt_id)

    def continue_attempt(self, project_id: str, attempt_id: str, **kwargs):
        return self._service_for(project_id, attempt_id).continue_attempt(project_id, attempt_id, **kwargs)

    def cancel_attempt(self, project_id: str, attempt_id: str, **kwargs):
        return self._service_for(project_id, attempt_id).cancel_attempt(project_id, attempt_id, **kwargs)

    def _service_for(self, project_id: str, attempt_id: str):
        project = self._storage.get_project(project_id)
        attempt = next((item for item in project.generation_attempts if item.attempt_id == attempt_id), None)
        if isinstance(attempt, AtelierMediaGenerationAttempt):
            return self._media_service
        if isinstance(attempt, AtelierGenerationAttempt):
            return self._video_service
        raise ValueError("atelier_generation_attempt_not_found")

    @staticmethod
    def _join_one(service, timeout: float | None) -> bool:
        join = getattr(service, "join", None)
        if not callable(join):
            return True
        result = join(timeout=timeout)
        return True if result is None else bool(result)
