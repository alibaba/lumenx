"""Atelier sequence export job service.

This module owns the app-level lifecycle around durable export jobs. It composes
AtelierStorage, immutable sequence snapshots, and the renderer publication
primitive without wiring HTTP routes or background schedulers.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Callable, Iterator

from pydantic import ValidationError

from .export_jobs import (
    REDO_REQUEST_CONTRACT_VERSION,
    CreateSequenceExportJob,
    RedoSequenceExportJob,
    SequenceExportJobAck,
    fingerprint_sequence_export_redo_request,
    fingerprint_sequence_export_request,
)
from .models import (
    AtelierAudioTrack,
    AtelierProject,
    AtelierSequenceEntry,
    AtelierSequenceExportJob,
    AtelierSequencePublicationProof,
    ExportRecord,
)
from .sequence import SequenceValidationError, normalize_sequence_inputs, resolve_sequence_snapshot, validate_sequence_snapshot
from .sequence_render import (
    MAX_RENDER_BYTES,
    SequencePublicationProof,
    SequenceRenderCancelToken,
    SequenceRenderCancelled,
    SequenceRenderError,
    publish_sequence_render,
    render_sequence_to_private_file,
)
from ...utils.json_storage import StorageConflictError, StorageError
from ...utils.media_versions import MediaVersionError, verify_managed_media_file


class SequenceExportError(RuntimeError):
    """The export job lifecycle cannot safely continue."""


class SequenceExportJobNotFound(ValueError):
    """Raised when a sequence export job or request receipt is missing."""


@dataclass(frozen=True)
class _WorkerKey:
    project_id: str
    job_id: str


class SequenceExportWorkerRegistry:
    """Runtime-only registry for live sequence export cancellation tokens."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._tokens: dict[_WorkerKey, SequenceRenderCancelToken] = {}
        self._stopping = False

    @contextmanager
    def synchronized(self) -> Iterator[None]:
        with self._condition:
            yield

    @contextmanager
    def reserve(self, project_id: str, job_id: str, token: SequenceRenderCancelToken) -> Iterator[None]:
        key = _WorkerKey(project_id, job_id)
        with self._condition:
            if self._stopping:
                raise StorageConflictError("Atelier export workers are stopping")
            if key in self._tokens:
                raise StorageConflictError("Atelier export job is already running")
            self._tokens[key] = token
            self._condition.notify_all()
        try:
            yield
        finally:
            self.unregister(project_id, job_id, token)

    def register(self, project_id: str, job_id: str, token: SequenceRenderCancelToken) -> None:
        key = _WorkerKey(project_id, job_id)
        with self._condition:
            if self._stopping:
                raise StorageConflictError("Atelier export workers are stopping")
            if key in self._tokens:
                raise StorageConflictError("Atelier export job is already running")
            self._tokens[key] = token
            self._condition.notify_all()

    def _token_for_unlocked(self, project_id: str, job_id: str) -> SequenceRenderCancelToken | None:
        return self._tokens.get(_WorkerKey(project_id, job_id))

    def cancel(self, project_id: str, job_id: str) -> bool:
        with self._condition:
            token = self._token_for_unlocked(project_id, job_id)
        if token is None:
            return False
        token.cancel()
        return True

    def cancel_all(self) -> int:
        with self._condition:
            tokens = list(self._tokens.values())
        for token in tokens:
            token.cancel()
        return len(tokens)

    def request_stop(self) -> int:
        with self._condition:
            self._stopping = True
            tokens = list(self._tokens.values())
        for token in tokens:
            token.cancel()
        return len(tokens)

    def join(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._tokens:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def unregister(self, project_id: str, job_id: str, token: SequenceRenderCancelToken) -> None:
        key = _WorkerKey(project_id, job_id)
        with self._condition:
            if self._tokens.get(key) is token:
                self._tokens.pop(key, None)
                self._condition.notify_all()

    def has_live_worker(self, project_id: str, job_id: str) -> bool:
        with self._condition:
            return self._token_for_unlocked(project_id, job_id) is not None


class SequenceExportService:
    """Create, run, cancel, and repair durable Atelier sequence export jobs."""

    def __init__(
        self,
        storage,
        *,
        registry: SequenceExportWorkerRegistry | None = None,
        id_factory: Callable[[], str] | None = None,
        export_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        render_context: Callable[..., Any] | None = None,
    ) -> None:
        self._storage = storage
        self._registry = registry or SequenceExportWorkerRegistry()
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._export_id_factory = export_id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock or time.time
        self._render_context = render_context or render_sequence_to_private_file


    def request_stop(self) -> int:
        """Stop accepting new run claims and cancel all live owned render tokens."""

        return self._registry.request_stop()

    def join(self, timeout: float | None = None) -> bool:
        """Wait until live owned render tokens have unregistered."""

        return self._registry.join(timeout=timeout)

    def create_sequence_export_job(self, project_id: str, command: CreateSequenceExportJob) -> SequenceExportJobAck:
        request_fingerprint = fingerprint_sequence_export_request(command)
        existing_project = self._storage.get_project(project_id)
        existing = _find_job_by_request(existing_project, command.client_request_id)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise StorageConflictError("Atelier export request id already belongs to different content")
            return self._ack(existing_project, existing, replayed=True, new_job_ids=[])
        if existing_project.revision != command.expected_revision:
            raise StorageConflictError("Atelier project changed; reload before exporting")

        raw_entries = list(command.entries) if command.entries is not None else list(existing_project.sequence)
        raw_audio_tracks = list(command.audio_tracks) if command.audio_tracks is not None else list(existing_project.audio_tracks)
        media_scope = self._storage._media_write_scope()
        snapshot = resolve_sequence_snapshot(
            existing_project,
            raw_entries,
            media_scope=media_scope,
            audio_tracks=raw_audio_tracks,
        )
        source_entries, source_audio_tracks = normalize_sequence_inputs(raw_entries, audio_tracks=raw_audio_tracks)
        source_audio_tracks = source_audio_tracks or []
        snapshot_digest = _snapshot_digest(snapshot)
        job_id = self._unique_job_id(existing_project)
        now = self._clock()
        output_ref = f"video/atelier_seq_{job_id}.mp4"

        def append_job(project: AtelierProject) -> None:
            project.sequence_export_jobs.append(
                AtelierSequenceExportJob(
                    job_id=job_id,
                    project_id=project.id,
                    status="queued",
                    client_request_id=command.client_request_id,
                    request_fingerprint=request_fingerprint,
                    base_revision=command.expected_revision,
                    created_at=now,
                    updated_at=now,
                    snapshot_digest=snapshot_digest,
                    snapshot=snapshot,
                    source_entries=source_entries,
                    source_audio_tracks=source_audio_tracks,
                    output_ref=output_ref,
                )
            )
            project.updated_at = now

        project, job, replayed = self._storage.apply_sequence_export_job_creation(
            project_id,
            expected_revision=command.expected_revision,
            client_request_id=command.client_request_id,
            request_fingerprint=request_fingerprint,
            command=append_job,
        )
        return self._ack(project, job, replayed=replayed, new_job_ids=[] if replayed else [job.job_id])

    def redo_sequence_export_job(self, project_id: str, source_export_id: str, command: RedoSequenceExportJob) -> SequenceExportJobAck:
        request_fingerprint = fingerprint_sequence_export_redo_request(command, source_export_id=source_export_id)
        existing_project = self._storage.get_project(project_id)
        existing = _find_job_by_request(existing_project, command.client_request_id)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise StorageConflictError("Atelier export request id already belongs to different content")
            return self._ack(existing_project, existing, replayed=True, new_job_ids=[])
        if existing_project.revision != command.expected_revision:
            raise StorageConflictError("Atelier project changed; reload before exporting")
        source_record = _find_export_record_by_id(existing_project, source_export_id)
        if source_record.deletion is not None:
            raise SequenceExportError("Atelier deleted export cannot be redone")
        if source_record.timeline_snapshot is None:
            raise SequenceExportError("Atelier export cannot be redone without a native timeline snapshot")
        media_scope = self._storage._media_write_scope()
        snapshot = validate_sequence_snapshot(deepcopy(source_record.timeline_snapshot), media_scope=media_scope)
        snapshot_digest = _snapshot_digest(snapshot)
        if source_record.snapshot_digest is not None and source_record.snapshot_digest != snapshot_digest:
            raise SequenceExportError("Atelier redo source snapshot digest does not match export record")
        source_job = _find_job(existing_project, source_record.job_id) if source_record.job_id else None
        if source_job is not None:
            source_entries = list(source_job.source_entries)
            source_audio_tracks = list(source_job.source_audio_tracks)
        else:
            source_entries, source_audio_tracks = _metadata_from_snapshot(snapshot)
        job_id = self._unique_job_id(existing_project)
        now = self._clock()
        output_ref = f"video/atelier_seq_{job_id}.mp4"
        source_payload = source_record.model_dump(mode="json")

        def append_job(project: AtelierProject) -> None:
            latest_source = _find_export_record_by_id(project, source_export_id)
            if latest_source.model_dump(mode="json") != source_payload:
                raise StorageConflictError("Atelier redo source export changed; reload before exporting")
            project.sequence_export_jobs.append(
                AtelierSequenceExportJob(
                    job_id=job_id,
                    project_id=project.id,
                    status="queued",
                    request_contract_version=REDO_REQUEST_CONTRACT_VERSION,
                    client_request_id=command.client_request_id,
                    source_export_id=source_export_id,
                    request_fingerprint=request_fingerprint,
                    base_revision=command.expected_revision,
                    created_at=now,
                    updated_at=now,
                    snapshot_digest=snapshot_digest,
                    snapshot=snapshot,
                    source_entries=source_entries,
                    source_audio_tracks=source_audio_tracks,
                    output_ref=output_ref,
                )
            )
            project.updated_at = now

        project, job, replayed = self._storage.apply_sequence_export_job_creation(
            project_id,
            expected_revision=command.expected_revision,
            client_request_id=command.client_request_id,
            request_fingerprint=request_fingerprint,
            command=append_job,
        )
        return self._ack(project, job, replayed=replayed, new_job_ids=[] if replayed else [job.job_id])

    def get_sequence_export_job(self, project_id: str, job_id: str) -> SequenceExportJobAck:
        project = self._storage.get_project(project_id)
        return self._ack(project, _find_job(project, job_id))

    def get_sequence_export_job_by_request(self, project_id: str, client_request_id: str) -> SequenceExportJobAck:
        project = self._storage.get_project(project_id)
        job = _find_job_by_request(project, client_request_id)
        if job is None:
            raise SequenceExportJobNotFound("Atelier export job not found")
        return self._ack(project, job)

    def resume_sequence_export_job(self, project_id: str, job_id: str) -> SequenceExportJobAck:
        with self._registry.synchronized():
            project = self._storage.get_project(project_id)
            job = _find_job(project, job_id)
            if job.status in {"queued", "repair_required"}:
                return self._ack(project, job)
            if job.status in {"running", "cancelling"} and self._registry._token_for_unlocked(project_id, job_id) is not None:
                return self._ack(project, job)
            if job.status == "completed":
                return self._ack(project, job)
            raise StorageConflictError("Atelier export job cannot be resumed")

    def run_sequence_export_job(self, project_id: str, job_id: str) -> SequenceExportJobAck:
        with self._registry.synchronized():
            project = self._storage.get_project(project_id)
            job = _find_job(project, job_id)
            if job.status == "completed":
                return self._ack(project, job)
            if job.status == "repair_required":
                return self.repair_sequence_export_job(project_id, job_id)
            if job.status in {"running", "cancelling"}:
                if self._registry._token_for_unlocked(project_id, job_id) is not None:
                    return self._ack(project, job)
                if job.prepared_proof is not None:
                    return self._mark_repair_required(
                        project_id,
                        job_id,
                        "orphaned_running_export",
                        "Export worker is not live but proof exists",
                    )
                orphan_project, orphan_job = self._mark_terminal(
                    project_id,
                    job_id,
                    status="failed",
                    code="orphaned_running_export",
                    message="Export worker is not live",
                )
                return self._ack(orphan_project, orphan_job)
            if job.status != "queued":
                return self._ack(project, job)

        token = SequenceRenderCancelToken()
        with self._registry.reserve(project_id, job_id, token):
            claimed_project, claimed_job = self._mark_running(project_id, job_id)
            if claimed_job.status == "cancelled":
                return self._ack(claimed_project, claimed_job)
            media_scope = self._storage._media_write_scope()
            try:
                with self._render_context(claimed_job.snapshot, media_scope=media_scope, cancel_token=token) as result:
                    proof = publish_sequence_render(
                        result,
                        output_ref=claimed_job.output_ref,
                        media_scope=media_scope,
                        on_prepared=lambda prepared: self._persist_prepared_proof(project_id, job_id, prepared),
                    )
                    self._assert_proof_matches_job(project_id, job_id, proof)
                return self._complete_from_prepared(project_id, job_id)
            except SequenceRenderCancelled:
                latest = _find_job(self._storage.get_project(project_id), job_id)
                if latest.prepared_proof is not None:
                    return self._mark_repair_required(
                        project_id,
                        job_id,
                        "cancelled_after_proof",
                        "Export was cancelled after publication proof was recorded",
                    )
                project, job = self._mark_terminal(project_id, job_id, status="cancelled", code="cancelled", message="Export was cancelled")
                return self._ack(project, job)
            except StorageError as exc:
                if exc.commit_uncertain:
                    return self._mark_repair_required(project_id, job_id, "commit_uncertain", str(exc), primary=exc)
                project, job = self._mark_terminal(project_id, job_id, status="failed", code="storage_error", message=str(exc))
                return self._ack(project, job)
            except (SequenceRenderError, SequenceValidationError, MediaVersionError) as exc:
                project, job = self._mark_terminal(project_id, job_id, status="failed", code="render_failed", message=str(exc))
                return self._ack(project, job)

    def cancel_sequence_export_job(self, project_id: str, job_id: str) -> SequenceExportJobAck:
        with self._registry.synchronized():
            project = self._storage.get_project(project_id)
            job = _find_job(project, job_id)
            if job.status == "queued":
                project, job = self._mark_terminal(project_id, job_id, status="cancelled", code="cancelled", message="Export was cancelled before start")
                return self._ack(project, job)
            if job.status in {"running", "cancelling"}:
                token = self._registry._token_for_unlocked(project_id, job_id)
                if token is not None:
                    project, job = self._update_job(
                        project_id,
                        job_id,
                        lambda _project, item: _request_cancelling(item, now=self._clock()),
                    )
                    if job.status == "cancelling":
                        token.cancel()
                    return self._ack(project, job)
                if job.prepared_proof is not None:
                    return self._mark_repair_required(project_id, job_id, "orphaned_running_export", "Export worker is not live but proof exists")
                project, job = self._mark_terminal(project_id, job_id, status="failed", code="orphaned_running_export", message="Export worker is not live")
                return self._ack(project, job)
            return self._ack(project, job)

    def repair_sequence_export_job(self, project_id: str, job_id: str) -> SequenceExportJobAck:
        with self._registry.synchronized():
            project = self._storage.get_project(project_id)
            job = _find_job(project, job_id)
            if job.status == "completed":
                return self._ack(project, job)
            if job.status in {"running", "cancelling"}:
                if self._registry._token_for_unlocked(project_id, job_id) is not None:
                    return self._ack(project, job)
                if job.prepared_proof is None:
                    project, job = self._mark_terminal(
                        project_id,
                        job_id,
                        status="failed",
                        code="orphaned_running_export",
                        message="Export worker is not live",
                    )
                    return self._ack(project, job)
            if job.prepared_proof is None:
                return self._ack(project, job)
        proof = job.prepared_proof
        media_scope = self._storage._media_write_scope()
        try:
            verify_managed_media_file(
                proof.relative_path,
                output_root=media_scope.data_dir,
                writer_lock=media_scope.writer_lock,
                storage_guard=media_scope.storage_guard,
                expected_sha256=proof.sha256,
                expected_size=proof.size_bytes,
                max_bytes=MAX_RENDER_BYTES,
            )
        except (StorageError, MediaVersionError) as exc:
            project, job = self._mark_terminal(project_id, job_id, status="failed", code="export_media_mismatch", message=str(exc))
            return self._ack(project, job)
        return self._complete_from_prepared(project_id, job_id)

    def _unique_job_id(self, project: AtelierProject) -> str:
        seen = {job.job_id for job in project.sequence_export_jobs}
        for _ in range(100):
            raw = str(self._id_factory())
            job_id = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-"})
            if job_id and job_id not in seen:
                return job_id
        raise StorageConflictError("Cannot allocate unique Atelier export job id")

    def _mark_running(self, project_id: str, job_id: str) -> tuple[AtelierProject, AtelierSequenceExportJob]:
        now = self._clock()

        def command(_project: AtelierProject, job: AtelierSequenceExportJob) -> None:
            if job.status == "cancelled":
                return
            if job.status not in {"queued", "running"}:
                raise StorageConflictError("Atelier export job cannot be started")
            _set_job_state(job, status="running", now=now, clear_error=True)
            if job.started_at is None:
                job.started_at = now

        return self._update_job(project_id, job_id, command)

    def _persist_prepared_proof(self, project_id: str, job_id: str, prepared: SequencePublicationProof) -> None:
        now = self._clock()
        model_proof = AtelierSequencePublicationProof(
            job_id=job_id,
            project_id=project_id,
            relative_path=prepared.relative_path,
            sha256=prepared.sha256,
            size_bytes=prepared.size_bytes,
            duration=prepared.duration,
            width=prepared.width,
            height=prepared.height,
            fps=prepared.fps,
            snapshot_digest=prepared.snapshot_sha256,
            prepared_at=now,
        )

        def command(_project: AtelierProject, job: AtelierSequenceExportJob) -> None:
            if job.snapshot_digest != prepared.snapshot_sha256 or job.output_ref != prepared.relative_path:
                raise StorageError("Atelier export prepared proof does not match job")
            job.prepared_proof = model_proof
            job.updated_at = now

        self._update_job(project_id, job_id, command)

    def _assert_proof_matches_job(self, project_id: str, job_id: str, proof: SequencePublicationProof) -> None:
        job = _find_job(self._storage.get_project(project_id), job_id)
        if job.prepared_proof is None:
            raise StorageError("Atelier export prepared proof was not persisted", commit_uncertain=True)
        if (
            job.prepared_proof.relative_path != proof.relative_path
            or job.prepared_proof.sha256 != proof.sha256
            or job.prepared_proof.size_bytes != proof.size_bytes
            or job.prepared_proof.snapshot_digest != proof.snapshot_sha256
        ):
            raise StorageError("Atelier export prepared proof differs from publication", commit_uncertain=True)

    def _complete_from_prepared(self, project_id: str, job_id: str) -> SequenceExportJobAck:
        now = self._clock()
        export_id = self._export_id_factory()

        def command(project: AtelierProject, job: AtelierSequenceExportJob) -> None:
            if job.status == "completed":
                return
            proof = job.prepared_proof
            if proof is None:
                raise StorageError("Atelier export cannot complete without proof")
            job.status = "completed"
            job.export_id = export_id
            job.finished_at = now
            job.updated_at = now
            job.error_code = None
            job.error_message = None
            project.exports.append(
                ExportRecord(
                    id=export_id,
                    project_id=project.id,
                    created_at=now,
                    clip_count=len(job.source_entries),
                    size_mb=proof.size_bytes / 1024 / 1024,
                    video_url=proof.relative_path,
                    filename=Path(proof.relative_path).name,
                    source_entries=list(job.source_entries),
                    timeline_snapshot=job.snapshot,
                    publication_proof=proof,
                    job_id=job.job_id,
                    snapshot_digest=job.snapshot_digest,
                )
            )
            project.updated_at = now

        try:
            project = self._storage.apply_sequence_export_completion(project_id, job_id=job_id, command=command)
        except StorageError as exc:
            if exc.commit_uncertain:
                return self._mark_repair_required(project_id, job_id, "completion_uncertain", str(exc), primary=exc)
            raise
        return self._ack(project, _find_job(project, job_id))

    def _mark_terminal(
        self,
        project_id: str,
        job_id: str,
        *,
        status: str,
        code: str,
        message: str,
    ) -> tuple[AtelierProject, AtelierSequenceExportJob]:
        now = self._clock()

        def command(_project: AtelierProject, job: AtelierSequenceExportJob) -> None:
            _set_job_state(job, status=status, now=now, code=code, message=message)
            if status in {"cancelled", "failed"}:
                job.finished_at = job.finished_at or now
            if status == "cancelled":
                job.cancelled_at = job.cancelled_at or now

        return self._update_job(project_id, job_id, command)

    def _mark_repair_required(
        self,
        project_id: str,
        job_id: str,
        code: str,
        message: str,
        *,
        primary: BaseException | None = None,
    ) -> SequenceExportJobAck:
        try:
            project, job = self._mark_terminal(project_id, job_id, status="repair_required", code=code, message=message)
        except StorageError:
            if primary is not None:
                raise primary
            raise
        return self._ack(project, job)

    def _update_job(
        self,
        project_id: str,
        job_id: str,
        command: Callable[[AtelierProject, AtelierSequenceExportJob], None],
    ) -> tuple[AtelierProject, AtelierSequenceExportJob]:
        return self._storage.apply_sequence_export_job_update(project_id, job_id=job_id, command=command)

    def _ack(
        self,
        project: AtelierProject,
        job: AtelierSequenceExportJob,
        *,
        replayed: bool = False,
        new_job_ids: list[str] | None = None,
    ) -> SequenceExportJobAck:
        return SequenceExportJobAck(
            project=project,
            job=job,
            export_record=_find_export_record(project, job),
            replayed=replayed,
            new_job_ids=[] if new_job_ids is None else new_job_ids,
        )


def _request_cancelling(job: AtelierSequenceExportJob, *, now: float) -> None:
    if job.status == "running":
        _set_job_state(
            job,
            status="cancelling",
            now=now,
            code="cancelling",
            message="Export cancellation requested",
        )


def _set_job_state(
    job: AtelierSequenceExportJob,
    *,
    status: str,
    now: float,
    code: str | None = None,
    message: str | None = None,
    clear_error: bool = False,
) -> None:
    job.status = status  # type: ignore[assignment]
    job.updated_at = now
    if clear_error:
        job.error_code = None
        job.error_message = None
    elif code is not None:
        job.error_code = code
        job.error_message = message


def _find_job(project: AtelierProject, job_id: str) -> AtelierSequenceExportJob:
    for job in project.sequence_export_jobs:
        if job.job_id == job_id:
            return job
    raise SequenceExportJobNotFound("Atelier export job not found")


def _find_job_by_request(project: AtelierProject, client_request_id: str) -> AtelierSequenceExportJob | None:
    return next((job for job in project.sequence_export_jobs if job.client_request_id == client_request_id), None)


def _find_export_record(project: AtelierProject, job: AtelierSequenceExportJob) -> ExportRecord | None:
    if job.export_id is None:
        return None
    for export in project.exports:
        if export.id == job.export_id and export.job_id == job.job_id:
            return export
    return None


def _find_export_record_by_id(project: AtelierProject, export_id: str) -> ExportRecord:
    for export in project.exports:
        if export.id == export_id:
            return export
    raise SequenceExportJobNotFound("Atelier export record not found")


def _metadata_from_snapshot(snapshot: dict[str, Any]) -> tuple[list[AtelierSequenceEntry], list[AtelierAudioTrack]]:
    try:
        entries = [
            AtelierSequenceEntry(
                id=clip["clip_id"],
                parentId=clip["parentId"],
                candidateId=clip.get("candidateId"),
                trimStart=clip["trimStart"],
                trimEnd=clip["trimEnd"],
                gain=clip["gain"],
                transition=clip["transition"],
            )
            for clip in snapshot["clips"]
        ]
        audio_tracks = [
            AtelierAudioTrack(
                id=track["id"],
                node_id=track["node_id"],
                start=track["start"],
                trim_start=track["trim_start"],
                trim_end=track["trim_end"],
                gain=track["gain"],
                loop=track["loop"],
            )
            for track in snapshot["audio_tracks"]
        ]
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise SequenceExportError("Atelier redo source snapshot cannot be mapped to export metadata") from exc
    return entries, audio_tracks


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
