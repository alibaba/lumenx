"""Single-step execution for persisted Atelier provider generation attempts."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from .agent_authorization import require_agent_authorization
from .generation_ledger import GENERATION_CANDIDATE_CONTRACT
from .models import AtelierGenerationAttempt, AtelierProject
from .storage import AtelierStorage
from ...models import provider_recovery
from ...models.provider_binding import ProviderCredentialResolutionError, ProviderCredentialStore
from ...models.provider_runtime import ProviderRecoveryRuntime
from ...models.provider_tasks import (
    ProviderBindingError,
    ProviderFetchError,
    ProviderHandle,
    ProviderObservation,
    ProviderQueryError,
    ProviderScope,
    ProviderSubmitError,
    ProviderTaskStatus,
    validate_fetch_binding,
    validate_query_binding,
)
from ...utils import media_versions
from ...utils.json_storage import StorageConflictError, StorageError
from ...utils.storage_identity import initialize_storage_identity


class GenerationAttemptAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    attempt: AtelierGenerationAttempt
    candidate: dict[str, Any] | None


class AtelierGenerationExecutionService:
    """Advance one already-admitted Atelier provider attempt by one safe step."""

    def __init__(
        self,
        storage: AtelierStorage,
        *,
        runtime_provider: Callable[[ProviderScope], ProviderRecoveryRuntime | None],
        credential_store: ProviderCredentialStore,
        clock: Callable[[], float] = time.time,
        server_storage_identity: Callable[[], str] | str | None = None,
    ):
        self.storage = storage
        self._runtime_provider = runtime_provider
        self._credential_store = credential_store
        self._clock = clock
        self._server_storage_identity = (
            (lambda: _default_storage_identity(storage))
            if server_storage_identity is None
            else server_storage_identity
        )
        self._active: dict[tuple[str, str], object] = {}
        self._active_lock = threading.Lock()
        self._active_condition = threading.Condition(self._active_lock)
        self._stopping = False

    def request_stop(self) -> None:
        with self._active_condition:
            self._stopping = True
            self._active_condition.notify_all()

    def join(self, timeout: float | None = 5.0) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._active_condition:
            while self._active:
                if deadline is None:
                    self._active_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._active_condition.wait(remaining)
            return True

    def read_attempt(self, project_id: str, attempt_id: str) -> GenerationAttemptAck:
        project = self.storage.get_project(project_id)
        return _ack(project, _find_attempt(project.generation_attempts, attempt_id))

    def continue_attempt(
        self,
        project_id: str,
        attempt_id: str,
        *,
        candidate_id: str,
        expected_attempt_revision: int,
        execution_fingerprint: str,
    ) -> GenerationAttemptAck:
        attempt = self.read_attempt(project_id, attempt_id).attempt
        _require_identity(
            attempt,
            candidate_id=candidate_id,
            expected_attempt_revision=expected_attempt_revision,
            execution_fingerprint=execution_fingerprint,
        )
        return self._run_step(project_id, attempt_id, expected_attempt_revision, execution_fingerprint)

    def run_attempt(self, project_id: str, attempt_id: str) -> GenerationAttemptAck:
        attempt = self.read_attempt(project_id, attempt_id).attempt
        return self._run_step(
            project_id,
            attempt_id,
            attempt.attempt_revision,
            attempt.provider_execution.fingerprint,
        )

    def cancel_attempt(
        self,
        project_id: str,
        attempt_id: str,
        *,
        candidate_id: str,
        expected_attempt_revision: int,
        execution_fingerprint: str,
    ) -> GenerationAttemptAck:
        attempt = self.read_attempt(project_id, attempt_id).attempt
        _require_identity(
            attempt,
            candidate_id=candidate_id,
            expected_attempt_revision=expected_attempt_revision,
            execution_fingerprint=execution_fingerprint,
        )

        def cancel(current: AtelierGenerationAttempt) -> None:
            record = current.provider_execution
            if (
                record.phase != "accepted"
                or any(value is not None for value in (record.handle, record.observation, record.prepared_artifact, record.artifact))
            ):
                raise StorageConflictError("atelier_generation_cancel_not_allowed")
            _set_attempt(
                current,
                status="cancelled",
                error_code=None,
                record=provider_recovery.record_with_changes(record, phase="cancelled"),
                now=self._clock(),
            )

        with self._claim(project_id, attempt_id):
            return self._update(project_id, attempt_id, expected_attempt_revision, execution_fingerprint, cancel)

    def _run_step(
        self,
        project_id: str,
        attempt_id: str,
        expected_attempt_revision: int,
        execution_fingerprint: str,
    ) -> GenerationAttemptAck:
        with self._claim(project_id, attempt_id):
            attempt = self.read_attempt(project_id, attempt_id).attempt
            if attempt.attempt_revision != expected_attempt_revision:
                raise StorageConflictError("Atelier generation attempt changed; reload before continuing")
            if attempt.provider_execution.fingerprint != execution_fingerprint:
                raise StorageConflictError("Atelier generation execution identity changed")
            if attempt.status in {"completed", "failed", "cancelled"}:
                return _ack(self.storage.get_project(project_id), attempt)
            record = attempt.provider_execution
            if record.phase == "submitting" and record.handle is None:
                return self._mark(
                    project_id,
                    attempt,
                    status="needs_confirmation",
                    error_code="provider_submit_receipt_missing",
                    record=record,
                )
            runtime = self._runtime_or_error(ProviderScope.from_dict(record.scope))
            if record.phase == "accepted":
                return self._submit(project_id, attempt, runtime)
            if record.phase in {"submitted", "observed"}:
                if record.observation is None:
                    return self._query(project_id, attempt, runtime)
                observation = _observation_or_none(record)
                if observation is None:
                    return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_query_invalid", record=record)
                if observation.status is ProviderTaskStatus.SUCCEEDED:
                    return self._fetch(project_id, attempt, runtime)
                if observation.status in {ProviderTaskStatus.PENDING, ProviderTaskStatus.RUNNING, ProviderTaskStatus.UNKNOWN}:
                    return self._query(project_id, attempt, runtime)
                return _ack(self.storage.get_project(project_id), attempt)
            return _ack(self.storage.get_project(project_id), attempt)

    def _submit(
        self,
        project_id: str,
        attempt: AtelierGenerationAttempt,
        runtime: ProviderRecoveryRuntime,
    ) -> GenerationAttemptAck:
        if not callable(getattr(runtime.provider, "submit", None)):
            raise StorageConflictError("Atelier provider runtime cannot submit")
        record = attempt.provider_execution
        secret = self._resolve_secret_or_mark(project_id, attempt, runtime)
        if secret is None:
            return self.read_attempt(project_id, attempt.attempt_id)
        try:
            credential = self._credential_store.resolve_binding(
                ProviderScope.from_dict(record.scope),
                record.credential_ref,
                secret,
            )
            submission = provider_recovery.reconstruct_submission_from_record(
                record,
                output_root=self.storage.data_dir,
                max_bytes=runtime.max_bytes,
            )
        except (ProviderCredentialResolutionError, provider_recovery.ProviderRecoveryError, ProviderBindingError):
            return self._mark(
                project_id,
                attempt,
                status="needs_confirmation",
                error_code="provider_credentials_required",
                record=record,
            )

        def guard(project: AtelierProject, current: AtelierGenerationAttempt) -> None:
            if current.schema_version == 1:
                return
            require_agent_authorization(
                project,
                current,
                server_storage_identity=self._resolve_server_storage_identity(),
            )

        if attempt.schema_version != 1:
            project = self.storage.get_project(project_id)
            guard(project, _find_attempt(project.generation_attempts, attempt.attempt_id))

        recheck_runtime_after_transport = any(
            item.transport_binding == "frozen_av_sha256_v1" for item in submission.media
        )
        try:
            submission = provider_recovery.prepare_submission_transport(
                submission, credential,
            )
        except provider_recovery.ProviderRecoveryError as exc:
            return self._mark(
                project_id,
                attempt,
                status="needs_confirmation",
                error_code=exc.reason,
                record=record,
            )
        if (
            recheck_runtime_after_transport
            and self._runtime_or_error(ProviderScope.from_dict(record.scope)) is not runtime
        ):
            raise StorageConflictError("Atelier provider runtime changed before submit")
        with self._active_condition:
            if self._stopping:
                raise StorageConflictError("Atelier generation execution service is stopping")

        submitted = self._mark(
            project_id,
            attempt,
            status="processing",
            error_code=None,
            record=provider_recovery.record_with_changes(record, phase="submitting"),
            project_guard=guard,
        )
        expected_revision = submitted.attempt.attempt_revision

        def save_handle(handle: ProviderHandle) -> None:
            def apply(current: AtelierGenerationAttempt) -> None:
                current_record = current.provider_execution
                if current_record.phase != "submitting" or current_record.handle is not None:
                    raise StorageConflictError("Atelier provider handle was already saved or superseded")
                accepted = provider_recovery.require_matching_handle(current_record, handle)
                _set_attempt(
                    current,
                    status="processing",
                    error_code=None,
                    record=provider_recovery.record_with_changes(
                        current_record,
                        phase="submitted",
                        handle=accepted.to_dict(),
                    ),
                    now=self._clock(),
                )

            self._update(project_id, attempt.attempt_id, expected_revision, record.fingerprint, apply)

        try:
            returned = runtime.provider.submit(submission, credential=credential, on_submitted=save_handle)
        except StorageError:
            raise
        except (ProviderSubmitError, ProviderBindingError):
            latest = self.read_attempt(project_id, attempt.attempt_id).attempt
            if latest.provider_execution.handle is None:
                return self._mark(
                    project_id,
                    latest,
                    status="needs_confirmation",
                    error_code="provider_submit_receipt_missing",
                    record=latest.provider_execution,
                )
            return _ack(self.storage.get_project(project_id), latest)
        latest = self.read_attempt(project_id, attempt.attempt_id).attempt
        try:
            returned = provider_recovery.require_matching_handle(latest.provider_execution, returned)
            saved = provider_recovery.handle_from_record(latest.provider_execution)
            if saved != returned:
                raise provider_recovery.ProviderRecoveryError("provider_handle_return_mismatch")
        except provider_recovery.ProviderRecoveryError:
            return self._mark(
                project_id,
                latest,
                status="needs_confirmation",
                error_code="provider_submit_receipt_mismatch",
                record=latest.provider_execution,
            )
        return _ack(self.storage.get_project(project_id), latest)

    def _query(
        self,
        project_id: str,
        attempt: AtelierGenerationAttempt,
        runtime: ProviderRecoveryRuntime,
    ) -> GenerationAttemptAck:
        if not callable(getattr(runtime.provider, "query", None)):
            raise StorageConflictError("Atelier provider runtime cannot query")
        try:
            handle = provider_recovery.handle_from_record(attempt.provider_execution)
        except provider_recovery.ProviderRecoveryError:
            return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_receipt_invalid", record=attempt.provider_execution)
        secret = self._resolve_secret_or_mark(project_id, attempt, runtime)
        if secret is None:
            return self.read_attempt(project_id, attempt.attempt_id)
        try:
            credential = self._credential_store.resolve_handle(handle, secret)
            validate_query_binding(handle, credential)
        except (ProviderCredentialResolutionError, ProviderBindingError):
            return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_credentials_required", record=attempt.provider_execution)
        try:
            observation = runtime.provider.query(handle, credential=credential)
        except ProviderBindingError:
            return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_receipt_invalid", record=attempt.provider_execution)
        except ProviderQueryError:
            return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_query_unavailable", record=attempt.provider_execution)
        try:
            observation = provider_recovery.validate_observation_for_handle(observation, handle)
        except provider_recovery.ProviderRecoveryError:
            return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_query_invalid", record=attempt.provider_execution)
        status, error_code = _status_after_observation(observation)
        return self._mark(
            project_id,
            attempt,
            status=status,
            error_code=error_code,
            record=provider_recovery.record_with_changes(
                attempt.provider_execution,
                phase="observed",
                observation=provider_recovery.observation_to_dict(observation),
            ),
        )

    def _fetch(
        self,
        project_id: str,
        attempt: AtelierGenerationAttempt,
        runtime: ProviderRecoveryRuntime,
    ) -> GenerationAttemptAck:
        if not callable(getattr(runtime.provider, "fetch", None)):
            raise StorageConflictError("Atelier provider runtime cannot fetch")
        record = attempt.provider_execution
        output_ref = _output_ref(attempt)
        prepared = record.prepared_artifact
        if prepared is not None:
            if prepared["relative_path"] != output_ref:
                return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_fetch_artifact_target_invalid", record=record)
            state = self._final_artifact_state(prepared, runtime)
            if state == "ready":
                return self._complete_from_proof(project_id, attempt, prepared)
            if state == "invalid":
                return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_fetch_artifact_invalid", record=record)
        elif media_versions.managed_media_file_exists(
            output_ref,
            output_root=self.storage.data_dir,
            writer_lock=self.storage._writer_lock,
            storage_guard=self.storage._media_write_scope().storage_guard,
        ):
            return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_fetch_artifact_unrecorded", record=record)
        try:
            handle = provider_recovery.handle_from_record(record)
            observation = provider_recovery.observation_from_record(record)
            validate_fetch_binding(handle, observation)
        except (provider_recovery.ProviderRecoveryError, ProviderBindingError):
            return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_fetch_receipt_invalid", record=record)
        secret = self._resolve_secret_or_mark(project_id, attempt, runtime)
        if secret is None:
            return self.read_attempt(project_id, attempt.attempt_id)
        try:
            credential = self._credential_store.resolve_handle(handle, secret)
            validate_query_binding(handle, credential)
        except (ProviderCredentialResolutionError, ProviderBindingError):
            return self._mark(project_id, attempt, status="needs_confirmation", error_code="provider_credentials_required", record=record)

        media_scope = self.storage._media_write_scope()
        fetch_revision = attempt.attempt_revision
        fetch_fingerprint = attempt.provider_execution.fingerprint
        prepared_holder: dict[str, AtelierGenerationAttempt] = {}

        def save_prepared(artifact) -> None:
            def apply(current: AtelierGenerationAttempt) -> None:
                current_record = current.provider_execution
                proof = provider_recovery.require_matching_artifact_proof(current_record, artifact, current_record.prepared_artifact)
                if proof["relative_path"] != _output_ref(current):
                    raise provider_recovery.ProviderRecoveryError("provider_fetch_artifact_target_invalid")
                if current_record.prepared_artifact is None:
                    _set_attempt(
                        current,
                        status="processing",
                        error_code=None,
                        record=provider_recovery.record_with_changes(current_record, prepared_artifact=proof),
                        now=self._clock(),
                    )

            prepared_holder["attempt"] = self._update(
                project_id,
                attempt.attempt_id,
                fetch_revision,
                fetch_fingerprint,
                apply,
            ).attempt

        try:
            artifact = runtime.provider.fetch(
                handle,
                observation,
                output_root=media_scope.data_dir,
                output_ref=output_ref,
                writer_lock=media_scope.writer_lock,
                storage_guard=media_scope.storage_guard,
                max_bytes=getattr(runtime, "output_max_bytes", 200 * 1024 * 1024),
                on_prepared=save_prepared,
                publish_policy="no_overwrite",
            )
        except provider_recovery.ProviderRecoveryError as exc:
            code = "provider_fetch_artifact_target_invalid" if exc.reason == "provider_fetch_artifact_target_invalid" else "provider_fetch_artifact_invalid"
            latest = self.read_attempt(project_id, attempt.attempt_id).attempt
            return self._mark(project_id, latest, status="needs_confirmation", error_code=code, record=latest.provider_execution)
        except (ProviderBindingError, ProviderFetchError):
            latest = self.read_attempt(project_id, attempt.attempt_id).attempt
            return self._mark(project_id, latest, status="needs_confirmation", error_code="provider_fetch_unavailable", record=latest.provider_execution)
        latest = prepared_holder.get("attempt", attempt)
        prepared = latest.provider_execution.prepared_artifact
        if prepared is None:
            return self._mark(project_id, latest, status="needs_confirmation", error_code="provider_fetch_prepare_missing", record=latest.provider_execution)
        try:
            proof = provider_recovery.require_matching_artifact_proof(latest.provider_execution, artifact, prepared)
        except provider_recovery.ProviderRecoveryError:
            return self._mark(project_id, latest, status="needs_confirmation", error_code="provider_fetch_artifact_invalid", record=latest.provider_execution)
        if proof["relative_path"] != _output_ref(latest):
            return self._mark(project_id, latest, status="needs_confirmation", error_code="provider_fetch_artifact_target_invalid", record=latest.provider_execution)
        state = self._final_artifact_state(proof, runtime)
        if state == "missing":
            return self._mark(project_id, latest, status="needs_confirmation", error_code="provider_fetch_artifact_missing", record=latest.provider_execution)
        if state == "invalid":
            return self._mark(project_id, latest, status="needs_confirmation", error_code="provider_fetch_artifact_invalid", record=latest.provider_execution)
        return self._complete_from_proof(project_id, latest, proof)

    def _complete_from_proof(
        self,
        project_id: str,
        attempt: AtelierGenerationAttempt,
        proof: dict,
    ) -> GenerationAttemptAck:
        return self._mark(
            project_id,
            attempt,
            status="completed",
            error_code=None,
            output_ref=proof["relative_path"],
            record=provider_recovery.record_with_changes(attempt.provider_execution, phase="completed", artifact=proof),
        )

    def _final_artifact_state(self, proof: dict, runtime: ProviderRecoveryRuntime) -> str:
        media_scope = self.storage._media_write_scope()
        if not media_versions.managed_media_file_exists(
            proof["relative_path"],
            output_root=media_scope.data_dir,
            writer_lock=media_scope.writer_lock,
            storage_guard=media_scope.storage_guard,
        ):
            return "missing"
        try:
            media_versions.verify_managed_media_file(
                proof["relative_path"],
                output_root=media_scope.data_dir,
                writer_lock=media_scope.writer_lock,
                storage_guard=media_scope.storage_guard,
                expected_sha256=proof["sha256"],
                expected_size=proof["size_bytes"],
                max_bytes=getattr(runtime, "output_max_bytes", 200 * 1024 * 1024),
            )
            return "ready"
        except media_versions.MediaVersionError:
            return "invalid"

    def _resolve_secret_or_mark(
        self,
        project_id: str,
        attempt: AtelierGenerationAttempt,
        runtime: ProviderRecoveryRuntime,
    ) -> str | None:
        try:
            return runtime.resolve_secret()
        except provider_recovery.ProviderRecoveryError:
            self._mark(
                project_id,
                attempt,
                status="needs_confirmation",
                error_code="provider_credentials_required",
                record=attempt.provider_execution,
            )
            return None

    def _runtime_or_error(self, scope: ProviderScope) -> ProviderRecoveryRuntime:
        runtime = self._runtime_provider(scope)
        if not isinstance(runtime, ProviderRecoveryRuntime):
            raise ValueError("atelier_provider_recovery_runtime_missing")
        if runtime.scope != scope:
            raise StorageConflictError("Atelier provider runtime scope mismatch")
        return runtime

    def _resolve_server_storage_identity(self) -> str | None:
        value = self._server_storage_identity
        return value() if callable(value) else value

    @contextmanager
    def _claim(self, project_id: str, attempt_id: str):
        key = (project_id, attempt_id)
        token = object()
        with self._active_condition:
            if self._stopping:
                raise StorageConflictError("Atelier generation execution service is stopping")
            if key in self._active:
                raise StorageConflictError("Atelier generation attempt operation already running")
            self._active[key] = token
        try:
            yield
        finally:
            with self._active_condition:
                if self._active.get(key) is token:
                    del self._active[key]
                    self._active_condition.notify_all()

    def _mark(
        self,
        project_id: str,
        attempt: AtelierGenerationAttempt,
        *,
        status: str,
        error_code: str | None,
        record,
        output_ref: str | None = None,
        project_guard: Callable[[AtelierProject, AtelierGenerationAttempt], None] | None = None,
    ) -> GenerationAttemptAck:
        def apply(current: AtelierGenerationAttempt) -> None:
            _set_attempt(
                current,
                status=status,
                error_code=error_code,
                output_ref=output_ref,
                record=record,
                now=self._clock(),
            )

        return self._update(
            project_id,
            attempt.attempt_id,
            attempt.attempt_revision,
            attempt.provider_execution.fingerprint,
            apply,
            project_guard=project_guard,
        )

    def _update(
        self,
        project_id: str,
        attempt_id: str,
        expected_attempt_revision: int,
        execution_fingerprint: str,
        command: Callable[[AtelierGenerationAttempt], None],
        project_guard: Callable[[AtelierProject, AtelierGenerationAttempt], None] | None = None,
    ) -> GenerationAttemptAck:
        project, attempt = self.storage.update_generation_attempt(
            project_id,
            attempt_id=attempt_id,
            expected_attempt_revision=expected_attempt_revision,
            execution_fingerprint=execution_fingerprint,
            command=command,
            project_guard=project_guard,
        )
        return _ack(project, attempt)


def _find_attempt(attempts: list[AtelierGenerationAttempt], attempt_id: str) -> AtelierGenerationAttempt:
    for attempt in attempts:
        if attempt.attempt_id == attempt_id:
            return attempt.model_copy(deep=True)
    raise ValueError("atelier_generation_attempt_not_found")


def _ack(project: AtelierProject, attempt: AtelierGenerationAttempt) -> GenerationAttemptAck:
    candidate = None
    for node in project.nodes:
        if node.id != attempt.node_id:
            continue
        for field in ("candidates", "deleted_candidates"):
            values = node.data.get(field, [])
            if not isinstance(values, list):
                continue
            candidate = next(
                (
                    item
                    for item in values
                    if isinstance(item, dict)
                    and item.get("execution_contract") == GENERATION_CANDIDATE_CONTRACT
                    and item.get("id") == attempt.candidate_id
                ),
                None,
            )
            if candidate is not None:
                break
    return GenerationAttemptAck(project=project, attempt=attempt.model_copy(deep=True), candidate=deepcopy(candidate))


def _require_identity(
    attempt: AtelierGenerationAttempt,
    *,
    candidate_id: str,
    expected_attempt_revision: int,
    execution_fingerprint: str,
) -> None:
    if (
        attempt.candidate_id != candidate_id
        or attempt.attempt_revision != expected_attempt_revision
        or attempt.provider_execution.fingerprint != execution_fingerprint
    ):
        raise StorageConflictError("Atelier generation attempt identity changed")


def _set_attempt(
    attempt: AtelierGenerationAttempt,
    *,
    status: str,
    error_code: str | None,
    record,
    now: float,
    output_ref: str | None = None,
) -> None:
    attempt.status = status
    attempt.error_code = error_code
    attempt.provider_execution = record
    attempt.output_ref = output_ref
    attempt.updated_at = now


def _status_after_observation(observation: ProviderObservation) -> tuple[str, str | None]:
    if observation.status is ProviderTaskStatus.FAILED:
        return "failed", "provider_remote_failed"
    if observation.status is ProviderTaskStatus.CANCELED:
        return "failed", "provider_remote_cancelled"
    if observation.status is ProviderTaskStatus.UNKNOWN:
        return "needs_confirmation", "provider_query_unknown"
    return "processing", None


def _observation_or_none(record) -> ProviderObservation | None:
    try:
        return provider_recovery.observation_from_record(record)
    except provider_recovery.ProviderRecoveryError:
        return None


def _output_ref(attempt: AtelierGenerationAttempt) -> str:
    return f"video/atelier-{attempt.candidate_id}-{attempt.attempt_id}.mp4"


def _default_storage_identity(storage: AtelierStorage) -> str:
    identity = initialize_storage_identity(storage.data_dir, writer_lock=storage._writer_lock)
    storage_id = identity.get("storage_id")
    if not isinstance(storage_id, str) or not storage_id:
        raise ValueError("Atelier generation execution requires a server storage identity")
    return storage_id
