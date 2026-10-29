"""Atelier export history deletion service and DTOs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Callable, ContextManager

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import AtelierProject, ExportDeletionReceipt, ExportRecord
from .sequence_render import MAX_RENDER_BYTES
from .storage import AtelierProjectNotFound
from ...utils.json_storage import StorageConflictError, StorageError
from ...utils.media_refs import payload_contains_managed_ref as payload_contains_managed_ref
from ...utils.media_versions import MediaVersionError, delete_verified_managed_media_file


class ExportRecordNotFound(ValueError):
    """Raised when an export history record is absent."""


class ExportReferenceCheckUnavailable(RuntimeError):
    """Raised when cross-domain reference safety cannot be checked."""


class DeleteExportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0, strict=True)
    client_request_id: str = Field(..., min_length=1, max_length=128)
    delete_file: bool = Field(True, strict=True)

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id(cls, value: str) -> str:
        from .export_jobs import _CLIENT_REQUEST_ID_PATTERN

        if _CLIENT_REQUEST_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("Invalid client_request_id")
        return value


class DeleteExportRecordAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: AtelierProject
    export_record: ExportRecord
    replayed: bool = False


class ExportHistoryService:
    """Delete export history with a tombstone before any file operation."""

    def __init__(
        self,
        storage,
        *,
        external_reference_guard: Callable[[AtelierProject, ExportRecord], ContextManager[bool]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._storage = storage
        self._external_reference_guard = external_reference_guard or _unavailable_external_reference_guard
        self._clock = clock or time.time

    def delete_export(self, project_id: str, export_id: str, command: DeleteExportRecord) -> DeleteExportRecordAck:
        now = self._clock()

        def admit(_project: AtelierProject, export: ExportRecord) -> None:
            if export.project_id != project_id:
                raise StorageError("Atelier export owner mismatch")
            export.deletion = ExportDeletionReceipt(
                client_request_id=command.client_request_id,
                delete_file=command.delete_file,
                deleted_at=now,
                file_status="pending" if command.delete_file else "retained",
                checked_at=now if not command.delete_file else None,
            )
            _project.updated_at = now

        try:
            project, export, replayed = self._storage.apply_export_deletion_admission(
                project_id,
                export_id=export_id,
                expected_revision=command.expected_revision,
                client_request_id=command.client_request_id,
                delete_file=command.delete_file,
                command=admit,
            )
        except StorageConflictError as exc:
            if "project no longer exists" in str(exc):
                raise AtelierProjectNotFound("Atelier project not found") from exc
            if "export record not found" in str(exc):
                raise ExportRecordNotFound("Atelier export record not found") from exc
            raise
        if export.deletion is None:
            raise StorageError("Atelier export deletion receipt was not persisted")
        if not command.delete_file:
            return DeleteExportRecordAck(project=project, export_record=export, replayed=replayed)
        if replayed and export.deletion.file_status not in {"pending", "blocked"}:
            return DeleteExportRecordAck(project=project, export_record=export, replayed=True)

        file_status, error_code, error_message = self._delete_file_fact(project_id, export)
        checked_at = None if file_status == "pending" else self._clock()
        project, export = self._storage.apply_export_deletion_file_fact(
            project_id,
            export_id=export_id,
            client_request_id=command.client_request_id,
            file_status=file_status,
            checked_at=checked_at,
            error_code=error_code,
            error_message=error_message,
        )
        return DeleteExportRecordAck(project=project, export_record=export, replayed=replayed)

    def _delete_file_fact(self, project_id: str, export: ExportRecord) -> tuple[str, str | None, str | None]:
        media_scope = self._storage._media_write_scope()
        with media_scope.storage_guard():
            projects = self._storage.list_projects()
            latest_project = _find_project(projects, project_id)
            latest_export = find_export_record(latest_project, export.id)
            if latest_export.deletion is None or latest_export.deletion.client_request_id != export.deletion.client_request_id:
                return "blocked", "deletion_receipt_changed", "Export deletion receipt changed during delete"
            deletion_proof = _deletion_proof(latest_export)
            if deletion_proof is None:
                return "blocked", "missing_publication_proof", "Export has no verifiable publication or import proof"
            if deletion_proof.relative_path != latest_export.video_url:
                return "blocked", "proof_path_mismatch", "Export proof path does not match the export record"
            if _has_atelier_reference(projects, latest_project.id, latest_export, media_scope.data_dir):
                return "blocked", "has_atelier_reference", "Export file is referenced by another Atelier record"
            try:
                with self._external_reference_guard(latest_project, latest_export) as no_external_refs:
                    if not no_external_refs:
                        return "blocked", "has_external_reference", "Export file is referenced outside Atelier"
                    result = delete_verified_managed_media_file(
                        deletion_proof.relative_path,
                        output_root=media_scope.data_dir,
                        writer_lock=media_scope.writer_lock,
                        storage_guard=media_scope.storage_guard,
                        expected_sha256=deletion_proof.sha256,
                        expected_size=deletion_proof.size_bytes,
                        max_bytes=MAX_RENDER_BYTES,
                    )
                    return result, None, None
            except ExportReferenceCheckUnavailable as exc:
                return "blocked", "checker_unavailable", str(exc)
            except MediaVersionError as exc:
                return "blocked", "media_proof_mismatch", str(exc)
            except StorageConflictError as exc:
                return "blocked", "storage_conflict", str(exc)


@dataclass(frozen=True)
class _DeletionProof:
    relative_path: str
    sha256: str
    size_bytes: int


def _deletion_proof(export: ExportRecord) -> _DeletionProof | None:
    if export.imported_proof is not None:
        proof = export.imported_proof
        if proof.project_id != export.project_id or proof.export_id != export.id:
            return None
        return _DeletionProof(
            relative_path=proof.relative_path,
            sha256=proof.sha256,
            size_bytes=proof.size_bytes,
        )
    proof = export.publication_proof
    if proof is None or export.job_id is None or export.snapshot_digest is None:
        return None
    return _DeletionProof(
        relative_path=proof.relative_path,
        sha256=proof.sha256,
        size_bytes=proof.size_bytes,
    )


def _find_project(projects: list[AtelierProject], project_id: str) -> AtelierProject:
    for project in projects:
        if project.id == project_id:
            return project
    raise AtelierProjectNotFound("Atelier project not found")


def find_export_record(project: AtelierProject, export_id: str) -> ExportRecord:
    for export in project.exports:
        if export.id == export_id:
            return export
    raise ExportRecordNotFound("Atelier export record not found")


@contextmanager
def _unavailable_external_reference_guard(_project: AtelierProject, _export: ExportRecord):
    raise ExportReferenceCheckUnavailable("External reference checker is unavailable")
    yield


def _has_atelier_reference(
    projects: list[AtelierProject],
    owner_project_id: str,
    target: ExportRecord,
    output_root: Path,
) -> bool:
    target_ref = target.video_url
    for project in projects:
        for node in project.nodes:
            if payload_contains_managed_ref(node.model_dump(mode="json"), target_ref, output_root):
                return True
        if payload_contains_managed_ref([entry.model_dump(mode="json") for entry in project.sequence], target_ref, output_root):
            return True
        if payload_contains_managed_ref([track.model_dump(mode="json") for track in project.audio_tracks], target_ref, output_root):
            return True
        for receipt in project.node_upload_receipts:
            if payload_contains_managed_ref(receipt.model_dump(mode="json"), target_ref, output_root):
                return True
        for receipt in project.node_delete_receipts:
            if payload_contains_managed_ref(receipt.model_dump(mode="json"), target_ref, output_root):
                return True
        for job in project.sequence_export_jobs:
            if project.id == owner_project_id and job.job_id == target.job_id:
                continue
            if payload_contains_managed_ref(job.model_dump(mode="json"), target_ref, output_root):
                return True
        for export in project.exports:
            if project.id == owner_project_id and export.id == target.id:
                continue
            if export.deletion is not None:
                continue
            if payload_contains_managed_ref(export.model_dump(mode="json"), target_ref, output_root):
                return True
    return False
