"""Internal image/audio execution with durable, non-retrying recovery checkpoints."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import threading
import time
from typing import Any, ContextManager, Literal, cast

from pydantic import BaseModel, ConfigDict

from .generation_ledger import MEDIA_GENERATION_CANDIDATE_CONTRACT
from .models import AtelierMediaGenerationAttempt, AtelierProject
from .storage import AtelierStorage
from ...models.provider_execution import ProviderExecutionRecord
from ...models.provider_media_intents import sync_artifact_proof_from_record
from ...models.provider_media_runtime import MediaRuntimeAuthority
from ...models.provider_recovery import record_with_changes
from ...utils import media_versions
from ...utils.json_storage import StorageConflictError, StorageError
from ...utils.private_media_directory import OwnedPrivateMediaDirectory

MediaAttemptStatus = Literal["pending", "processing", "needs_confirmation", "failed", "cancelled", "completed"]


class MediaGenerationAttemptAck(BaseModel):
    """Private service result; public media wire integration is a separate slice."""
    model_config = ConfigDict(extra='forbid')
    project: AtelierProject
    attempt: AtelierMediaGenerationAttempt
    candidate: dict[str, Any] | None


class AtelierMediaGenerationExecutionService:
    """Execute an accepted request once; only repair already-proven final bytes."""
    def __init__(
        self, storage: AtelierStorage, *,
        authority_resolver: Callable[[ProviderExecutionRecord], ContextManager[MediaRuntimeAuthority]],
        executor: Callable | None = None,
        max_bytes: int = 100 * 1024 * 1024,
        clock: Callable[[], float] = time.time,
    ):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError('max_bytes must be a positive integer')
        self.storage = storage
        self._authority_resolver = authority_resolver
        self._executor = executor or _execute_core
        self._max_bytes = max_bytes
        self._clock = clock
        self._condition = threading.Condition()
        self._active: set[tuple[str, str]] = set()
        self._stopping = False

    def request_stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()

    def join(self, timeout: float | None = 5.0) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._active:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
        return True

    def read_attempt(self, project_id: str, attempt_id: str) -> MediaGenerationAttemptAck:
        project = self.storage.get_project(project_id)
        for attempt in project.generation_attempts:
            if attempt.attempt_id == attempt_id:
                if not isinstance(attempt, AtelierMediaGenerationAttempt):
                    raise ValueError('atelier_media_attempt_required')
                return _ack(project, attempt)
        raise ValueError('atelier_generation_attempt_not_found')

    def run_attempt(self, project_id: str, attempt_id: str) -> MediaGenerationAttemptAck:
        with self._claim(project_id, attempt_id):
            return self._run(self.read_attempt(project_id, attempt_id).attempt)

    def continue_attempt(self, project_id: str, attempt_id: str, *, candidate_id: str,
                         expected_attempt_revision: int, execution_fingerprint: str) -> MediaGenerationAttemptAck:
        with self._claim(project_id, attempt_id):
            attempt = self.read_attempt(project_id, attempt_id).attempt
            _require_identity(attempt, candidate_id, expected_attempt_revision, execution_fingerprint)
            return self._run(attempt)

    def cancel_attempt(self, project_id: str, attempt_id: str, *, candidate_id: str,
                       expected_attempt_revision: int, execution_fingerprint: str) -> MediaGenerationAttemptAck:
        with self._claim(project_id, attempt_id):
            attempt = self.read_attempt(project_id, attempt_id).attempt
            _require_identity(attempt, candidate_id, expected_attempt_revision, execution_fingerprint)
            if attempt.provider_execution.phase != 'accepted':
                raise StorageConflictError('atelier_generation_cancel_not_allowed')
            return self._mark(attempt, status='cancelled', error_code=None,
                              record=record_with_changes(attempt.provider_execution, phase='cancelled'))

    def _run(self, attempt: AtelierMediaGenerationAttempt) -> MediaGenerationAttemptAck:
        if attempt.status in {'completed', 'failed', 'cancelled'}:
            return self.read_attempt(attempt.project_id, attempt.attempt_id)
        record = attempt.provider_execution
        if record.phase == 'submitting':
            if record.prepared_artifact is None:
                return self._uncertain(attempt, 'provider_sync_execution_unknown')
            state = self._artifact_state(attempt)
            if state == 'ready':
                return self._complete(attempt)
            return self._uncertain(attempt, f'provider_sync_artifact_{state}')
        if record.phase != 'accepted':
            raise StorageConflictError('atelier_media_execution_phase_invalid')
        output_ref = _require_record_output_ref(record)
        media_kind = _require_record_media_kind(record)
        scope = self.storage._media_write_scope()
        try:
            exists = media_versions.managed_media_file_exists(output_ref, output_root=scope.data_dir,
                writer_lock=scope.writer_lock, storage_guard=scope.storage_guard, media_kind=media_kind)
        except media_versions.MediaVersionError:
            return self._uncertain(attempt, 'provider_sync_artifact_invalid')
        if exists:
            return self._uncertain(attempt, 'provider_sync_artifact_unrecorded')
        # A resolver context owns any private profile snapshot through execution.
        # Resolution failure before submitting does not make a provider call.
        entered = False
        try:
            with self._authority_resolver(record) as authority:
                entered = True
                with self._condition:
                    if self._stopping:
                        raise StorageConflictError('Atelier generation execution service is stopping')
                submitting = self._mark(attempt, status='processing', error_code=None,
                    record=record_with_changes(record, phase='submitting')).attempt
                return self._execute(submitting, authority)
        except StorageError:
            raise
        except Exception:
            latest = self.read_attempt(attempt.project_id, attempt.attempt_id).attempt
            if latest.status == 'completed':
                return self.read_attempt(attempt.project_id, attempt.attempt_id)
            code = 'provider_sync_execution_unknown' if entered else 'provider_credentials_required'
            return self._uncertain(latest, code)

    def _execute(self, attempt: AtelierMediaGenerationAttempt, authority: MediaRuntimeAuthority) -> MediaGenerationAttemptAck:
        record = attempt.provider_execution
        output_ref = _require_record_output_ref(record)
        media_kind = _require_record_media_kind(record)
        scope = self.storage._media_write_scope()
        directory = OwnedPrivateMediaDirectory(prefix='lumenx-sync-media-')
        result = None
        durable = False
        try:
            result = self._executor(record, authority, output_root=scope.data_dir,
                scratch_dir=directory.path, max_bytes=self._max_bytes)
            directory.assert_owned()
            with _verified_private_file(result, directory.path, media_kind, self._max_bytes) as source:
                def prepared(path: str, sha256: str, size: int) -> None:
                    # This comparison also detects mutation between verification
                    # and streaming, before any final file becomes authoritative.
                    if sha256 != result.sha256 or size != result.size_bytes:
                        raise media_versions.MediaVersionError('Private result changed during publication')
                    proof = sync_artifact_proof_from_record(record, relative_path=path, sha256=sha256, size_bytes=size)
                    self._mark(attempt, status='processing', error_code=None,
                        record=record_with_changes(record, prepared_artifact=proof))

                media_versions.store_media_stream(iter(lambda: source.read(64 * 1024), b''),
                    output_ref=output_ref, output_root=scope.data_dir, writer_lock=scope.writer_lock,
                    storage_guard=scope.storage_guard, max_bytes=self._max_bytes, media_kind=media_kind,
                    on_prepared=prepared, publish_policy='no_overwrite')
            latest = self.read_attempt(attempt.project_id, attempt.attempt_id).attempt
            state = self._artifact_state(latest)
            durable = state == 'ready'
            if not durable:
                return self._uncertain(latest, f'provider_sync_artifact_{state}')
            return self._complete(latest)
        finally:
            keep_name = None
            if result is not None and not durable:
                try:
                    directory.assert_owned()
                    with _verified_private_file(result, directory.path, media_kind, self._max_bytes):
                        keep_name = Path(result.path).name
                except Exception:
                    # An invalid or replaced result confers no retention authority.
                    pass
            directory.cleanup(keep_name=keep_name)

    def _artifact_state(self, attempt: AtelierMediaGenerationAttempt) -> str:
        proof = attempt.provider_execution.prepared_artifact
        if proof is None:
            return 'unrecorded'
        media_kind = _require_record_media_kind(attempt.provider_execution)
        scope = self.storage._media_write_scope()
        try:
            if not media_versions.managed_media_file_exists(
                proof['relative_path'],
                output_root=scope.data_dir,
                writer_lock=scope.writer_lock,
                storage_guard=scope.storage_guard,
                media_kind=media_kind,
            ):
                return 'missing'
            media_versions.verify_managed_media_file(
                proof['relative_path'],
                output_root=scope.data_dir,
                writer_lock=scope.writer_lock,
                storage_guard=scope.storage_guard,
                media_kind=media_kind,
                expected_sha256=proof['sha256'],
                expected_size=proof['size_bytes'],
                max_bytes=self._max_bytes,
            )
        except media_versions.MediaVersionError:
            return 'invalid'
        return 'ready'

    def _complete(self, attempt: AtelierMediaGenerationAttempt) -> MediaGenerationAttemptAck:
        proof = attempt.provider_execution.prepared_artifact
        if proof is None:
            raise StorageConflictError('atelier_media_prepared_proof_required')
        return self._mark(attempt, status='completed', error_code=None, output_ref=proof['relative_path'],
            record=record_with_changes(attempt.provider_execution, phase='completed', artifact=proof))

    def _uncertain(self, attempt: AtelierMediaGenerationAttempt, code: str) -> MediaGenerationAttemptAck:
        return self._mark(attempt, status='needs_confirmation', error_code=code, record=attempt.provider_execution)

    def _mark(self, attempt: AtelierMediaGenerationAttempt, *, status: MediaAttemptStatus, error_code: str | None,
              record: ProviderExecutionRecord, output_ref: str | None = None) -> MediaGenerationAttemptAck:
        def apply(current) -> None:
            if not isinstance(current, AtelierMediaGenerationAttempt):
                raise StorageConflictError('atelier_media_attempt_required')
            current.status, current.error_code = status, error_code
            current.provider_execution, current.output_ref = record, output_ref
            current.updated_at = self._clock()
        project, updated = self.storage.update_generation_attempt(attempt.project_id, attempt_id=attempt.attempt_id,
            expected_attempt_revision=attempt.attempt_revision, execution_fingerprint=attempt.provider_execution.fingerprint,
            command=apply)
        if not isinstance(updated, AtelierMediaGenerationAttempt):
            raise StorageConflictError('atelier_media_attempt_required')
        return _ack(project, updated)

    @contextmanager
    def _claim(self, project_id: str, attempt_id: str):
        key = (project_id, attempt_id)
        with self._condition:
            if self._stopping or key in self._active:
                raise StorageConflictError('Atelier media attempt is running or service is stopping')
            self._active.add(key)
        try:
            yield
        finally:
            with self._condition:
                self._active.remove(key)
                self._condition.notify_all()


def _execute_core(*args, **kwargs):
    from ...models.provider_media_execution import execute_sync_artifact_to_private_file
    return execute_sync_artifact_to_private_file(*args, **kwargs)


def _require_record_output_ref(record: ProviderExecutionRecord) -> str:
    if record.schema_version != 2 or record.output_ref is None:
        raise StorageConflictError('provider_sync_output_ref_required')
    return record.output_ref


def _require_record_media_kind(record: ProviderExecutionRecord) -> media_versions.ManagedMediaKind:
    if record.schema_version != 2 or record.media_kind not in {'image', 'audio'}:
        raise StorageConflictError('provider_sync_media_kind_required')
    return cast(media_versions.ManagedMediaKind, record.media_kind)


def _ack(project: AtelierProject, attempt: AtelierMediaGenerationAttempt) -> MediaGenerationAttemptAck:
    candidate = next((item for node in project.nodes if node.id == attempt.node_id
        for field in ('candidates', 'deleted_candidates') for item in node.data.get(field, [])
        if isinstance(item, dict) and item.get('execution_contract') == MEDIA_GENERATION_CANDIDATE_CONTRACT
        and item.get('id') == attempt.candidate_id), None)
    return MediaGenerationAttemptAck(project=project, attempt=attempt.model_copy(deep=True), candidate=deepcopy(candidate))


def _require_identity(attempt, candidate_id, revision, fingerprint):
    if type(revision) is not int or (attempt.candidate_id, attempt.attempt_revision, attempt.provider_execution.fingerprint) != (candidate_id, revision, fingerprint):
        raise StorageConflictError('Atelier generation attempt identity changed')


@contextmanager
def _verified_private_file(result, scratch: Path, media_kind: str, max_bytes: int):
    if result.media_kind != media_kind or Path(result.path).parent != scratch:
        raise media_versions.MediaVersionError('Private result identity is invalid')
    with media_versions.verified_private_media_file(result.path, scratch_root=scratch,
            expected_sha256=result.sha256, expected_size=result.size_bytes, max_bytes=max_bytes) as source:
        yield source
