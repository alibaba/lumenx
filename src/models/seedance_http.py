"""One-shot explicit HTTP submission and receipt recovery for standard Seedance."""

from __future__ import annotations

from contextlib import contextmanager
import inspect
from pathlib import Path
import re
import time
from typing import Callable, ContextManager, Iterator

import requests

from .provider_result_fetch import fetch_provider_result
from .provider_tasks import (
    ProviderArtifact, ProviderBindingError, ProviderHandle, ProviderObservation,
    ProviderQueryError, ProviderScope, ProviderSubmission, ProviderSubmitError,
    ProviderTaskStatus, QueryCapability, ResolvedCredential, validate_fetch_binding,
    validate_query_binding, validate_result_url,
)
from .seedance_core_contract import (
    SEEDANCE_HTTP_ENDPOINTS, seedance_submission_payload, validate_seedance_scope,
)
from ..utils.json_storage import DirectoryWriterLock


_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}")
_SAFE_DIAGNOSTIC = re.compile(r"[A-Za-z0-9_.:-]{1,256}")
_STATUS = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_STATUSES = {
    "pending": ProviderTaskStatus.PENDING, "queued": ProviderTaskStatus.PENDING,
    "running": ProviderTaskStatus.RUNNING, "processing": ProviderTaskStatus.RUNNING,
    "completed": ProviderTaskStatus.SUCCEEDED, "succeeded": ProviderTaskStatus.SUCCEEDED,
    "failed": ProviderTaskStatus.FAILED,
}


@contextmanager
def _http_session(credential: ResolvedCredential) -> Iterator[requests.Session]:
    with requests.Session() as session:
        session.trust_env = False
        session.auth = None
        session.cookies.clear()
        session.headers.clear()
        session.headers.update({"Authorization": f"Bearer {credential.secret}",
                                "Content-Type": "application/json"})
        # A fresh Requests session has max_retries=0 for both schemes.
        yield session


def _endpoint(scope: ProviderScope, provider_model_id: str) -> str:
    validate_seedance_scope(scope)
    path = SEEDANCE_HTTP_ENDPOINTS.get(provider_model_id)
    if path is None:
        raise ProviderBindingError("Unsupported Seedance receipt model")
    return scope.base_url + path


def _valid_task_id(task_id: object) -> bool:
    return isinstance(task_id, str) and _TASK_ID.fullmatch(task_id) is not None


def _safe_diagnostic(value: object, *, secret: str) -> str | None:
    if isinstance(value, str) and secret not in value and _SAFE_DIAGNOSTIC.fullmatch(value):
        return value
    return None


class SeedanceHttpProvider:
    @staticmethod
    def validate_submission(request: ProviderSubmission) -> None:
        seedance_submission_payload(request)

    def submit(
        self, request: ProviderSubmission, *, credential: ResolvedCredential,
        on_submitted: Callable[[ProviderHandle], None],
    ) -> ProviderHandle:
        if not callable(on_submitted) or any(
            check(candidate)
            for candidate in (on_submitted, getattr(on_submitted, "__call__", None))
            for check in (inspect.iscoroutinefunction, inspect.isgeneratorfunction, inspect.isasyncgenfunction)
        ):
            raise ProviderBindingError("Submission requires a synchronous receipt writer")
        if (
            not isinstance(request, ProviderSubmission) or not isinstance(credential, ResolvedCredential)
            or credential.scope != request.scope or credential.credential_ref != request.credential_ref
        ):
            raise ProviderBindingError("Credential binding does not match the submission")
        payload = seedance_submission_payload(request)
        endpoint = _endpoint(request.scope, request.provider_model_id)
        submitted_at = time.time()
        try:
            with _http_session(credential) as session:
                with session.post(endpoint, json=payload, timeout=120, allow_redirects=False) as response:
                    status_code = response.status_code
                    try:
                        result = response.json()
                    except ValueError:
                        raise ProviderSubmitError("protocol", status_code=status_code) from None
        except requests.RequestException:
            raise ProviderSubmitError("transport") from None
        info = result.get("task_info") if isinstance(result, dict) else None
        task_id = info.get("id") if isinstance(info, dict) else None
        if not _valid_task_id(task_id):
            raise ProviderSubmitError("missing_receipt", status_code=status_code)
        # A usable receipt must survive even a non-200 response; never resubmit.
        assert isinstance(task_id, str)
        handle = ProviderHandle(
            request.provider_model_id, task_id, request.scope, request.credential_ref,
            request_id=_safe_diagnostic(result.get("request_id"), secret=credential.secret),
            query_capability=QueryCapability.BY_TASK_ID, submitted_at=submitted_at,
        )
        acknowledgement = on_submitted(handle)
        if acknowledgement is not None:
            if inspect.iscoroutine(acknowledgement) or inspect.isgenerator(acknowledgement):
                acknowledgement.close()
            raise TypeError("Receipt writer must return None synchronously")
        return handle

    def query(self, handle: ProviderHandle, *, credential: ResolvedCredential) -> ProviderObservation:
        scope = validate_query_binding(handle, credential)
        endpoint = _endpoint(scope, handle.provider_model_id)
        if not _valid_task_id(handle.task_id):
            raise ProviderBindingError("Invalid Seedance task ID")
        try:
            with _http_session(credential) as session:
                with session.get(f"{endpoint}/{handle.task_id}", timeout=30, allow_redirects=False) as response:
                    if response.status_code != 200:
                        raise ProviderQueryError("http", status_code=response.status_code)
                    try:
                        payload = response.json()
                    except ValueError:
                        raise ProviderQueryError("protocol") from None
        except requests.RequestException:
            raise ProviderQueryError("transport") from None
        info = payload.get("task_info") if isinstance(payload, dict) else None
        if not isinstance(info, dict) or info.get("id") != handle.task_id:
            raise ProviderQueryError("protocol")
        raw_status = info.get("status")
        if not isinstance(raw_status, str) or not _STATUS.fullmatch(raw_status):
            raise ProviderQueryError("protocol")
        status = _STATUSES.get(raw_status, ProviderTaskStatus.UNKNOWN)
        video_url = None
        if status is ProviderTaskStatus.SUCCEEDED:
            videos = payload.get("videos")
            if isinstance(videos, list):
                if len(videos) != 1:
                    raise ProviderQueryError("protocol")
                videos = videos[0]
            try:
                validate_result_url(videos)
            except ProviderBindingError:
                raise ProviderQueryError("protocol") from None
            video_url = videos
        error = info.get("error")
        error_code = _safe_diagnostic(error.get("code"), secret=credential.secret) if isinstance(error, dict) else None
        if status is ProviderTaskStatus.FAILED and error_code is None:
            error_code = "provider_failed"
        return ProviderObservation(
            handle.task_id, status, raw_status,
            request_id=_safe_diagnostic(payload.get("request_id"), secret=credential.secret), video_url=video_url,
            error_code=error_code, source_handle=handle,
        )

    def fetch(
        self, handle: ProviderHandle, observation: ProviderObservation, *,
        output_root: Path, output_ref: str, writer_lock: DirectoryWriterLock,
        storage_guard: Callable[[], ContextManager[None]], max_bytes: int,
        on_prepared: Callable[[ProviderArtifact], None] | None = None,
        publish_policy: str = "replace",
    ) -> ProviderArtifact:
        validate_fetch_binding(handle, observation)
        assert handle.scope is not None
        _endpoint(handle.scope, handle.provider_model_id)
        if not _valid_task_id(handle.task_id):
            raise ProviderBindingError("Invalid Seedance task ID")
        return fetch_provider_result(
            handle, observation, output_root=output_root, output_ref=output_ref,
            writer_lock=writer_lock, storage_guard=storage_guard, max_bytes=max_bytes,
            on_prepared=on_prepared, publish_policy=publish_policy,
        )
