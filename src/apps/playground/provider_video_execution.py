"""Single-step Playground provider video execution with durable recovery checkpoints."""

from __future__ import annotations

import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

from .models import PlaygroundAttempt, PlaygroundGeneration, PlaygroundMode, PlaygroundOutput
from .storage import PlaygroundStorage
from .happyhorse_video_admission import admitted_happyhorse_video_generation
from .kling_video_admission import admitted_kling_video_generation
from .vidu_video_admission import admitted_vidu_video_generation
from .pixverse_video_admission import admitted_pixverse_video_generation
from .seedance_video_admission import admitted_seedance_video_generation
from .video_admission import admitted_wan27_video_generation
from ...models import provider_recovery
from ...models.provider_binding import ProviderCredentialResolutionError, ProviderCredentialStore
from ...models.provider_execution import ProviderExecutionRecord
from ...models.provider_runtime import ProviderRecoveryRuntime
from ...models.provider_tasks import (
    ProviderBindingError,
    ProviderFetchError,
    ProviderHandle,
    ProviderQueryError,
    ProviderScope,
    ProviderSubmitError,
    ProviderTaskStatus,
    validate_fetch_binding,
    validate_query_binding,
)
from ...utils import media_versions
from ...utils.json_storage import StorageConflictError, StorageError


_TERMINAL_ATTEMPTS = {"completed", "failed", "cancelled"}
_TRANSIENT_ERRORS = {"provider_query_unavailable", "provider_fetch_unavailable"}
_SAFE_OUTPUT_ID = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class PlaygroundVideoAttemptAck:
    generation: PlaygroundGeneration
    attempt: PlaygroundAttempt


class PlaygroundProviderVideoExecutionService:
    """Advance one Playground Core video attempt by one bounded step."""

    def __init__(
        self,
        storage: PlaygroundStorage,
        *,
        runtime_provider: Callable[[ProviderScope], ProviderRecoveryRuntime | None],
        credential_store: ProviderCredentialStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.storage = storage
        self._runtime_provider = runtime_provider
        self._credential_store = credential_store
        self._clock = clock
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

    def read_attempt(self, generation_id: str, attempt_id: str) -> PlaygroundVideoAttemptAck:
        gen = self.storage.get_generation(generation_id)
        if gen is None:
            raise ValueError("playground_generation_not_found")
        return PlaygroundVideoAttemptAck(gen, _find_attempt(gen, attempt_id))

    def run_attempt(
        self,
        generation_id: str,
        attempt_id: str,
    ) -> PlaygroundVideoAttemptAck:
        existing = self.storage.get_generation(generation_id)
        if existing is None:
            raise ValueError("playground_generation_not_found")
        existing_attempt = _find_attempt(existing, attempt_id)
        if not is_core_video_generation(existing):
            return PlaygroundVideoAttemptAck(existing, existing_attempt)
        existing_record = _valid_provider_record_or_none(existing_attempt)
        if existing_record is not None and not is_async_video_record(existing_record):
            return PlaygroundVideoAttemptAck(existing, existing_attempt)
        with self._claim(generation_id, attempt_id):
            gen, claimed_by_this_call = self._read_or_claim_generation(generation_id, attempt_id)
            attempt = _find_attempt(gen, attempt_id)
            if attempt.status in _TERMINAL_ATTEMPTS:
                return PlaygroundVideoAttemptAck(gen, attempt)
            if attempt.provider_execution is None:
                return self._mark(gen.id, attempt, status="needs_confirmation", error="playground_core_video_record_missing", record=None)
            try:
                record = ProviderExecutionRecord.model_validate(attempt.provider_execution)
            except (TypeError, ValueError):
                return self._mark(gen.id, attempt, status="needs_confirmation", error="provider_receipt_invalid", record=None)
            if record.attempt_id != attempt.attempt_id:
                return self._mark(gen.id, attempt, status="needs_confirmation", error="provider_receipt_invalid", record=attempt.provider_execution)
            if record.phase == "accepted":
                if not claimed_by_this_call:
                    return self._mark(gen.id, attempt, status="needs_confirmation", error="provider_accepted_after_restart", record=record)
                return self._submit(gen.id, attempt, record)
            if record.phase == "submitting":
                if record.handle is None:
                    return self._mark(gen.id, attempt, status="needs_confirmation", error="provider_submit_receipt_missing", record=record)
                return self._mark(gen.id, attempt, status="needs_confirmation", error="provider_submit_receipt_invalid", record=record)
            local_repair = self._repair_ready_local_proof(gen.id, attempt, record)
            if local_repair is not None:
                return local_repair
            if record.phase == "completed":
                return self._mark(gen.id, attempt, status="needs_confirmation", error="provider_fetch_artifact_invalid", record=record)
            runtime = self._runtime_or_confirmation(gen.id, attempt, record)
            if runtime is None:
                return self.read_attempt(gen.id, attempt.attempt_id)
            if record.phase == "submitted":
                return self._query(gen.id, attempt, record, runtime)
            if record.phase == "observed":
                observation = _observation_or_none(record)
                if observation is None:
                    return self._mark(gen.id, attempt, status="needs_confirmation", error="provider_query_invalid", record=record)
                if observation.status is ProviderTaskStatus.SUCCEEDED:
                    return self._fetch(gen.id, attempt, record, runtime)
                if observation.status in {ProviderTaskStatus.PENDING, ProviderTaskStatus.RUNNING, ProviderTaskStatus.UNKNOWN}:
                    return self._query(gen.id, attempt, record, runtime)
                return PlaygroundVideoAttemptAck(gen, attempt)
            return PlaygroundVideoAttemptAck(gen, attempt)

    def _repair_ready_local_proof(
        self,
        generation_id: str,
        attempt: PlaygroundAttempt,
        record: ProviderExecutionRecord,
    ) -> PlaygroundVideoAttemptAck | None:
        proof = record.artifact if record.phase == "completed" else record.prepared_artifact
        if proof is None:
            return None
        output_ref = _output_ref(generation_id, attempt)
        if proof["relative_path"] != output_ref:
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_fetch_artifact_target_invalid", record=record)
        state = self._final_artifact_state(proof, None)
        if state == "ready":
            return self._complete_from_proof(generation_id, attempt, record, proof)
        if state == "invalid":
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_fetch_artifact_invalid", record=record)
        if record.phase == "completed":
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_fetch_artifact_missing", record=record)
        return None

    def _read_or_claim_generation(self, generation_id: str, attempt_id: str) -> tuple[PlaygroundGeneration, bool]:
        claimed = self.storage.claim_generation_attempt_for_processing(generation_id, attempt_id)
        if claimed is not None:
            return claimed, True
        gen = self.storage.get_generation(generation_id)
        if gen is None:
            raise ValueError("playground_generation_not_found")
        return gen, False

    def _submit(self, generation_id: str, attempt: PlaygroundAttempt, record: ProviderExecutionRecord) -> PlaygroundVideoAttemptAck:
        runtime = self._runtime_or_confirmation(generation_id, attempt, record)
        if runtime is None:
            return self.read_attempt(generation_id, attempt.attempt_id)
        if not callable(getattr(runtime.provider, "submit", None)):
            raise StorageConflictError("Playground provider runtime cannot submit")
        secret = self._resolve_secret_or_mark(generation_id, attempt, record, runtime)
        if secret is None:
            return self.read_attempt(generation_id, attempt.attempt_id)
        try:
            scope = ProviderScope.from_dict(record.scope)
            credential = self._credential_store.resolve_binding(
                scope, record.credential_ref, secret,
                source_key=self._credential_store._source_key_for_scope(scope),
            )
            submission = provider_recovery.reconstruct_submission_from_record(
                record, output_root=self.storage.data_dir, max_bytes=runtime.max_bytes,
            )
            submission = provider_recovery.prepare_submission_transport(submission, credential)
        except (ProviderCredentialResolutionError, provider_recovery.ProviderRecoveryError, ProviderBindingError) as exc:
            reason = getattr(exc, "reason", "provider_credentials_required")
            return self._mark(generation_id, attempt, status="needs_confirmation", error=reason, record=record)
        with self._active_condition:
            if self._stopping:
                raise StorageConflictError("Playground video execution service is stopping")
        self._mark(
            generation_id,
            attempt,
            status="processing",
            error=None,
            record=provider_recovery.record_with_changes(record, phase="submitting"),
            require_processing_generation=True,
        )

        def save_handle(handle: ProviderHandle) -> None:
            latest = self.read_attempt(generation_id, attempt.attempt_id).attempt
            latest_record = ProviderExecutionRecord.model_validate(latest.provider_execution)
            if latest_record.phase != "submitting" or latest_record.handle is not None:
                raise StorageConflictError("Playground provider handle was already saved or superseded")
            accepted = provider_recovery.require_matching_handle(latest_record, handle)
            self._mark(
                generation_id,
                latest,
                status="processing",
                error=None,
                record=provider_recovery.record_with_changes(latest_record, phase="submitted", handle=accepted.to_dict()),
            )

        try:
            returned = runtime.provider.submit(submission, credential=credential, on_submitted=save_handle)
        except StorageError:
            raise
        except (ProviderSubmitError, ProviderBindingError):
            latest = self.read_attempt(generation_id, attempt.attempt_id).attempt
            latest_record = ProviderExecutionRecord.model_validate(latest.provider_execution)
            if latest_record.handle is None:
                return self._mark(generation_id, latest, status="needs_confirmation", error="provider_submit_receipt_missing", record=latest_record)
            return self.read_attempt(generation_id, attempt.attempt_id)
        latest = self.read_attempt(generation_id, attempt.attempt_id).attempt
        try:
            returned = provider_recovery.require_matching_handle(ProviderExecutionRecord.model_validate(latest.provider_execution), returned)
            saved = provider_recovery.handle_from_record(ProviderExecutionRecord.model_validate(latest.provider_execution))
            if saved != returned:
                raise provider_recovery.ProviderRecoveryError("provider_handle_return_mismatch")
        except provider_recovery.ProviderRecoveryError:
            return self._mark(generation_id, latest, status="needs_confirmation", error="provider_submit_receipt_mismatch", record=latest.provider_execution)
        return self.read_attempt(generation_id, attempt.attempt_id)

    def _query(self, generation_id: str, attempt: PlaygroundAttempt, record: ProviderExecutionRecord, runtime: ProviderRecoveryRuntime) -> PlaygroundVideoAttemptAck:
        if not callable(getattr(runtime.provider, "query", None)):
            raise StorageConflictError("Playground provider runtime cannot query")
        try:
            handle = provider_recovery.handle_from_record(record)
        except provider_recovery.ProviderRecoveryError:
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_receipt_invalid", record=record)
        secret = self._resolve_secret_or_mark(generation_id, attempt, record, runtime)
        if secret is None:
            return self.read_attempt(generation_id, attempt.attempt_id)
        try:
            credential = self._credential_store.resolve_handle(handle, secret)
            validate_query_binding(handle, credential)
        except (ProviderCredentialResolutionError, ProviderBindingError):
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_credentials_required", record=record)
        try:
            observation = runtime.provider.query(handle, credential=credential)
        except ProviderBindingError:
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_receipt_invalid", record=record)
        except ProviderQueryError:
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_query_unavailable", record=record)
        try:
            observation = provider_recovery.validate_observation_for_handle(observation, handle)
        except provider_recovery.ProviderRecoveryError:
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_query_invalid", record=record)
        status, error = _status_after_observation(observation)
        return self._mark(
            generation_id,
            attempt,
            status=status,
            error=error,
            record=provider_recovery.record_with_changes(record, phase="observed", observation=provider_recovery.observation_to_dict(observation)),
        )

    def _fetch(self, generation_id: str, attempt: PlaygroundAttempt, record: ProviderExecutionRecord, runtime: ProviderRecoveryRuntime) -> PlaygroundVideoAttemptAck:
        if not callable(getattr(runtime.provider, "fetch", None)):
            raise StorageConflictError("Playground provider runtime cannot fetch")
        output_ref = _output_ref(generation_id, attempt)
        prepared = record.prepared_artifact
        if prepared is not None:
            if prepared["relative_path"] != output_ref:
                return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_fetch_artifact_target_invalid", record=record)
            state = self._final_artifact_state(prepared, runtime)
            if state == "ready":
                return self._complete_from_proof(generation_id, attempt, record, prepared)
            if state == "invalid":
                return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_fetch_artifact_invalid", record=record)
        elif media_versions.managed_media_file_exists(
            output_ref,
            output_root=self.storage.data_dir,
            writer_lock=self.storage._writer_lock,
            storage_guard=self._storage_guard,
        ):
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_fetch_artifact_unrecorded", record=record)
        try:
            handle = provider_recovery.handle_from_record(record)
            observation = provider_recovery.observation_from_record(record)
            validate_fetch_binding(handle, observation)
        except (provider_recovery.ProviderRecoveryError, ProviderBindingError):
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_fetch_receipt_invalid", record=record)
        secret = self._resolve_secret_or_mark(generation_id, attempt, record, runtime)
        if secret is None:
            return self.read_attempt(generation_id, attempt.attempt_id)
        try:
            credential = self._credential_store.resolve_handle(handle, secret)
            validate_query_binding(handle, credential)
        except (ProviderCredentialResolutionError, ProviderBindingError):
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_credentials_required", record=record)
        prepared_holder: dict[str, PlaygroundAttempt] = {}

        def save_prepared(artifact) -> None:
            latest = self.read_attempt(generation_id, attempt.attempt_id).attempt
            latest_record = ProviderExecutionRecord.model_validate(latest.provider_execution)
            proof = provider_recovery.require_matching_artifact_proof(latest_record, artifact, latest_record.prepared_artifact)
            if proof["relative_path"] != output_ref:
                raise provider_recovery.ProviderRecoveryError("provider_fetch_artifact_target_invalid")
            if latest_record.prepared_artifact is None:
                prepared_holder["attempt"] = self._mark(
                    generation_id,
                    latest,
                    status="processing",
                    error=None,
                    record=provider_recovery.record_with_changes(latest_record, prepared_artifact=proof),
                ).attempt
            else:
                prepared_holder["attempt"] = latest

        try:
            artifact = runtime.provider.fetch(
                handle,
                observation,
                output_root=self.storage.data_dir,
                output_ref=output_ref,
                writer_lock=self.storage._writer_lock,
                storage_guard=self._storage_guard,
                max_bytes=getattr(runtime, "output_max_bytes", 200 * 1024 * 1024) if runtime is not None else 200 * 1024 * 1024,
                on_prepared=save_prepared,
                publish_policy="no_overwrite",
            )
        except provider_recovery.ProviderRecoveryError as exc:
            code = "provider_fetch_artifact_target_invalid" if exc.reason == "provider_fetch_artifact_target_invalid" else "provider_fetch_artifact_invalid"
            latest = self.read_attempt(generation_id, attempt.attempt_id).attempt
            return self._mark(generation_id, latest, status="needs_confirmation", error=code, record=latest.provider_execution)
        except (ProviderBindingError, ProviderFetchError):
            latest = self.read_attempt(generation_id, attempt.attempt_id).attempt
            return self._mark(generation_id, latest, status="needs_confirmation", error="provider_fetch_unavailable", record=latest.provider_execution)
        latest = prepared_holder.get("attempt", attempt)
        latest_record = ProviderExecutionRecord.model_validate(latest.provider_execution)
        prepared = latest_record.prepared_artifact
        if prepared is None:
            return self._mark(generation_id, latest, status="needs_confirmation", error="provider_fetch_prepare_missing", record=latest_record)
        try:
            proof = provider_recovery.require_matching_artifact_proof(latest_record, artifact, prepared)
        except provider_recovery.ProviderRecoveryError:
            return self._mark(generation_id, latest, status="needs_confirmation", error="provider_fetch_artifact_invalid", record=latest_record)
        if proof["relative_path"] != output_ref:
            return self._mark(generation_id, latest, status="needs_confirmation", error="provider_fetch_artifact_target_invalid", record=latest_record)
        state = self._final_artifact_state(proof, runtime)
        if state == "missing":
            return self._mark(generation_id, latest, status="needs_confirmation", error="provider_fetch_artifact_missing", record=latest_record)
        if state == "invalid":
            return self._mark(generation_id, latest, status="needs_confirmation", error="provider_fetch_artifact_invalid", record=latest_record)
        return self._complete_from_proof(generation_id, latest, latest_record, proof)

    def _complete_from_proof(self, generation_id: str, attempt: PlaygroundAttempt, record: ProviderExecutionRecord, proof: dict | None) -> PlaygroundVideoAttemptAck:
        if proof is None:
            return self._mark(generation_id, attempt, status="needs_confirmation", error="provider_fetch_artifact_invalid", record=record)
        output_ref = proof["relative_path"]
        return self._mark(
            generation_id,
            attempt,
            status="completed",
            error=None,
            record=provider_recovery.record_with_changes(record, phase="completed", artifact=proof),
            output_ref=output_ref,
        )

    def _final_artifact_state(self, proof: dict, runtime: ProviderRecoveryRuntime | None) -> str:
        if not media_versions.managed_media_file_exists(
            proof["relative_path"],
            output_root=self.storage.data_dir,
            writer_lock=self.storage._writer_lock,
            storage_guard=self._storage_guard,
        ):
            return "missing"
        try:
            media_versions.verify_managed_media_file(
                proof["relative_path"],
                output_root=self.storage.data_dir,
                writer_lock=self.storage._writer_lock,
                storage_guard=self._storage_guard,
                expected_sha256=proof["sha256"],
                expected_size=proof["size_bytes"],
                max_bytes=getattr(runtime, "output_max_bytes", 200 * 1024 * 1024) if runtime is not None else 200 * 1024 * 1024,
                media_kind="video",
            )
            return "ready"
        except media_versions.MediaVersionError:
            return "invalid"

    def _resolve_secret_or_mark(
        self,
        generation_id: str,
        attempt: PlaygroundAttempt,
        record: ProviderExecutionRecord,
        runtime: ProviderRecoveryRuntime,
    ) -> str | None:
        try:
            return runtime.resolve_secret()
        except provider_recovery.ProviderRecoveryError:
            self._mark(generation_id, attempt, status="needs_confirmation", error="provider_credentials_required", record=record)
            return None

    def _runtime_or_confirmation(
        self,
        generation_id: str,
        attempt: PlaygroundAttempt,
        record: ProviderExecutionRecord,
    ) -> ProviderRecoveryRuntime | None:
        try:
            return self._runtime_or_error(ProviderScope.from_dict(record.scope))
        except (TypeError, ValueError, provider_recovery.ProviderRecoveryError, StorageConflictError):
            self._mark(generation_id, attempt, status="needs_confirmation", error="provider_recovery_runtime_missing", record=record)
            return None

    def _runtime_or_error(self, scope: ProviderScope) -> ProviderRecoveryRuntime:
        runtime = self._runtime_provider(scope)
        if not isinstance(runtime, ProviderRecoveryRuntime):
            raise ValueError("playground_provider_recovery_runtime_missing")
        if runtime.scope != scope:
            raise StorageConflictError("Playground provider runtime scope mismatch")
        return runtime

    def _mark(
        self,
        generation_id: str,
        attempt: PlaygroundAttempt,
        *,
        status: str,
        error: str | None,
        record,
        output_ref: str | None = None,
        require_processing_generation: bool = False,
    ) -> PlaygroundVideoAttemptAck:
        def mutate(gen: PlaygroundGeneration, target: PlaygroundAttempt) -> None:
            _apply_attempt_to_record(target, status=status, error=error, record=record)
            if output_ref is not None:
                _append_output(gen, attempt.attempt_id, output_ref)
            _project_generation_status(gen)

        saved = self.storage.update_generation_attempt(
            generation_id,
            attempt.attempt_id,
            mutate,
            require_processing=require_processing_generation,
            expected_status=attempt.status,
            expected_provider_execution=attempt.provider_execution,
        )
        return PlaygroundVideoAttemptAck(saved, _find_attempt(saved, attempt.attempt_id))

    @contextmanager
    def _claim(self, generation_id: str, attempt_id: str):
        key = (generation_id, attempt_id)
        token = object()
        with self._active_condition:
            if self._stopping:
                raise StorageConflictError("Playground video execution service is stopping")
            self.storage.claim_video_attempt_operation(generation_id, attempt_id, token)
            self._active[key] = token
        try:
            yield
        finally:
            self.storage.release_video_attempt_operation(generation_id, attempt_id, token)
            with self._active_condition:
                if self._active.get(key) is token:
                    del self._active[key]
                    self._active_condition.notify_all()

    @contextmanager
    def _storage_guard(self):
        with self.storage._lock:
            yield


def _valid_provider_record_or_none(attempt: PlaygroundAttempt) -> ProviderExecutionRecord | None:
    if attempt.provider_execution is None:
        return None
    try:
        return ProviderExecutionRecord.model_validate(attempt.provider_execution)
    except (TypeError, ValueError):
        return None


def is_async_video_record(record: ProviderExecutionRecord) -> bool:
    return record.schema_version == 1 and record.execution_kind is None and record.media_kind is None and record.output_ref is None


def is_core_video_generation(gen: PlaygroundGeneration) -> bool:
    try:
        mode = PlaygroundMode(getattr(gen, "mode", None))
    except (TypeError, ValueError):
        return False
    model_id = str(getattr(gen, "model_id", ""))
    return (
        admitted_wan27_video_generation(mode, model_id)
        or admitted_happyhorse_video_generation(mode, model_id)
        or admitted_kling_video_generation(mode, model_id)
        or admitted_vidu_video_generation(mode, model_id)
        or admitted_pixverse_video_generation(mode, model_id)
        or admitted_seedance_video_generation(mode, model_id)
    )


def _find_attempt(gen: PlaygroundGeneration, attempt_id: str) -> PlaygroundAttempt:
    for attempt in gen.attempts:
        if attempt.attempt_id == attempt_id:
            return attempt.model_copy(deep=True)
    raise ValueError("playground_video_attempt_not_found")


def _apply_attempt(gen: PlaygroundGeneration, attempt_id: str, *, status: str, error: str | None, record) -> None:
    for attempt in gen.attempts:
        if attempt.attempt_id != attempt_id:
            continue
        _apply_attempt_to_record(attempt, status=status, error=error, record=record)
        return
    raise ValueError("playground_video_attempt_not_found")


def _apply_attempt_to_record(attempt: PlaygroundAttempt, *, status: str, error: str | None, record) -> None:
    attempt.status = status
    attempt.error = error
    if record is not None:
        if isinstance(record, ProviderExecutionRecord):
            attempt.provider_execution = record.model_dump(mode="json")
        elif isinstance(record, dict):
            attempt.provider_execution = dict(record)


def _append_output(gen: PlaygroundGeneration, attempt_id: str, output_ref: str) -> None:
    for output in gen.outputs:
        if output.attempt_id == attempt_id or output.media_path == output_ref:
            return
    gen.outputs.append(
        PlaygroundOutput(
            id=str(uuid.uuid4()),
            media_path=output_ref,
            media_type="video",
            thumbnail_path=None,
            saved_to_library=False,
            attempt_id=attempt_id,
        )
    )


def _project_generation_status(gen: PlaygroundGeneration) -> None:
    statuses = [attempt.status for attempt in gen.attempts]
    if any(status in {"pending", "processing"} for status in statuses):
        gen.status = "processing"
        gen.error = None
    elif statuses and all(status == "completed" for status in statuses):
        gen.status = "completed"
        gen.error = None
    elif any(attempt.status == "needs_confirmation" and attempt.error in _TRANSIENT_ERRORS for attempt in gen.attempts):
        gen.status = "processing"
        gen.error = None
    elif any(status == "needs_confirmation" for status in statuses):
        gen.status = "needs_confirmation"
        gen.error = None
    elif gen.outputs:
        gen.status = "completed"
        gen.error = None
    else:
        gen.status = "failed"
        gen.error = next((attempt.error for attempt in gen.attempts if attempt.error), "All video attempts failed")


def _status_after_observation(observation) -> tuple[str, str | None]:
    if observation.status is ProviderTaskStatus.FAILED:
        return "failed", "provider_remote_failed"
    if observation.status is ProviderTaskStatus.CANCELED:
        return "failed", "provider_remote_cancelled"
    if observation.status is ProviderTaskStatus.UNKNOWN:
        return "needs_confirmation", "provider_query_unknown"
    return "processing", None


def _observation_or_none(record: ProviderExecutionRecord):
    try:
        return provider_recovery.observation_from_record(record)
    except provider_recovery.ProviderRecoveryError:
        return None


def _output_ref(generation_id: str, attempt: PlaygroundAttempt) -> str:
    base = _SAFE_OUTPUT_ID.sub("-", f"playground-{generation_id}-{attempt.ordinal}").strip("-")
    if not base:
        base = uuid.uuid4().hex
    return f"playground/videos/{base}.mp4"


def is_transient_video_result(ack: PlaygroundVideoAttemptAck | None) -> bool:
    if ack is None:
        return False
    return ack.attempt.status == "needs_confirmation" and ack.attempt.error in _TRANSIENT_ERRORS
