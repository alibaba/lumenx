"""Local JSON storage for product-owned Atelier projects."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import UnionType
from typing import Any, Callable, ContextManager, get_args, get_origin

from pydantic import BaseModel, ValidationError

from .agent_commands import AdmitApprovedAgentGenerationCommand, RecordAgentApprovalCommand
from .agent_canvas_commands import ApplyApprovedAgentCanvasCommand, RecordAgentCanvasApprovalCommand
from .models import (
    AtelierAgentPlannerInvocation,
    AtelierAgentTurn,
    AtelierGenerationAttempt,
    AtelierMediaGenerationAttempt,
    AtelierProject,
    AtelierSequenceExportJob,
    ExportDeletionReceipt,
)
from .generation_ledger import (
    GENERATION_CANDIDATE_CONTRACT,
    GENERATION_TERMINAL_STATUSES,
    MEDIA_GENERATION_CANDIDATE_CONTRACT,
    assert_generation_unchanged_for_product_command,
    has_pending_generation,
    validate_generation_ledger,
    validate_generation_transition,
)
from ...utils.json_storage import (
    DirectoryWriterLock,
    StorageConflictError,
    StorageError,
    atomic_write_json,
    read_json,
    serialize_json,
)

_DB_FILENAME = "atelier_projects.json"
_UPLOAD_CLIENT_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_UPLOAD_REF_RE = re.compile(r"^uploads/[A-Za-z0-9_-]+\.[a-z0-9]+$")
_UPLOAD_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"},
    "video": {".mp4", ".mov", ".webm", ".mkv"},
}


@dataclass(frozen=True)
class _AtelierMediaWriteScope:
    data_dir: Path
    writer_lock: DirectoryWriterLock
    storage_guard: Callable[[], ContextManager[None]]


class AtelierProjectNotFound(ValueError):
    """Raised when an operation requires an existing Atelier project."""


class AtelierProjectExists(StorageConflictError):
    """Raised when creating a duplicate Atelier project."""


def _is_any_annotation(annotation: Any) -> bool:
    return annotation is Any or annotation is object


def _annotation_allows_opaque_dict(annotation: Any) -> bool:
    if _is_any_annotation(annotation):
        return True
    origin = get_origin(annotation)
    if origin is dict:
        args = get_args(annotation)
        return len(args) == 2 and _is_any_annotation(args[1])
    if origin in (list, tuple, set):
        return any(_annotation_allows_opaque_dict(arg) for arg in get_args(annotation))
    if origin in (UnionType, getattr(__import__("typing"), "Union")):
        return any(_annotation_allows_opaque_dict(arg) for arg in get_args(annotation))
    return False


def _model_types(annotation: Any) -> tuple[type[BaseModel], ...]:
    origin = get_origin(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return (annotation,)
    if origin in (list, tuple, set):
        found: list[type[BaseModel]] = []
        for arg in get_args(annotation):
            found.extend(_model_types(arg))
        return tuple(found)
    if origin in (UnionType, getattr(__import__("typing"), "Union")):
        found = []
        for arg in get_args(annotation):
            found.extend(_model_types(arg))
        return tuple(found)
    return ()


def _reject_unknown_model_fields(value: Any, model_type: type[BaseModel], path: str) -> None:
    if not isinstance(value, dict):
        return
    allowed = set(model_type.model_fields)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise StorageError(f"Unsupported Atelier field at {path}: {unknown[0]}")
    for name, field in model_type.model_fields.items():
        if name not in value:
            continue
        item = value[name]
        annotation = field.annotation
        if _annotation_allows_opaque_dict(annotation):
            continue
        nested_models = _model_types(annotation)
        if not nested_models:
            continue
        if isinstance(item, dict):
            _reject_unknown_for_any_model(item, nested_models, f"{path}.{name}")
        elif isinstance(item, list):
            for index, element in enumerate(item):
                if isinstance(element, dict):
                    _reject_unknown_for_any_model(element, nested_models, f"{path}.{name}[{index}]")


def _reject_unknown_for_any_model(value: dict[str, Any], model_types: tuple[type[BaseModel], ...], path: str) -> None:
    last_error: StorageError | None = None
    for model_type in model_types:
        try:
            _reject_unknown_model_fields(value, model_type, path)
            return
        except StorageError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error


def _project_from_payload(project_id: str, payload: Any) -> AtelierProject:
    if not isinstance(payload, dict):
        raise StorageError("Invalid Atelier project payload")
    _reject_unknown_model_fields(payload, AtelierProject, f"projects.{project_id}")
    try:
        project = AtelierProject.model_validate(payload)
    except ValidationError as exc:
        raise StorageError(f"Invalid Atelier project {project_id}: {exc}") from exc
    _validate_project(project, expected_id=project_id)
    return project


def _canonical_project(project: AtelierProject, *, expected_id: str | None = None) -> AtelierProject:
    key = expected_id if expected_id is not None else project.id
    return _project_from_payload(key, project.model_dump(mode="json"))


def _validate_project(project: AtelierProject, *, expected_id: str | None = None) -> None:
    from .agent_authorization import validate_agent_authorization_ledger

    if expected_id is not None and project.id != expected_id:
        raise StorageError("Atelier project id does not match storage key")
    node_ids: set[str] = set()
    for node in project.nodes:
        if node.project_id != project.id:
            raise StorageError("Atelier node project_id does not match owner")
        if node.id in node_ids:
            raise StorageError("Duplicate Atelier node id")
        node_ids.add(node.id)
        _validate_branch_source(node)
    turn_ids: set[str] = set()
    for turn in project.agent_turns:
        if turn.project_id != project.id:
            raise StorageError("Atelier turn project_id does not match owner")
        if turn.id in turn_ids:
            raise StorageError("Duplicate Atelier agent turn id")
        turn_ids.add(turn.id)
        _validate_agent_planner_invocations(project, turn)
    validate_agent_authorization_ledger(project, StorageError)
    for export in project.exports:
        if export.project_id != project.id:
            raise StorageError("Atelier export project_id does not match owner")
    receipt_ids: set[str] = set()
    live_node_ids = set(node_ids)
    allowed_groups = {
        "references": (
            {"reference_node_ids", "reference_image_urls", "reference_video_urls", "reference_audio_urls"},
            {"reference_node_ids", "pending_reference_nodes", "reference_image_urls", "reference_video_urls", "reference_audio_urls"},
        ),
        "parent": ({"parent_node_id", "reference_role"},),
        "region": ({"region_id"},),
    }
    for receipt in project.node_delete_receipts:
        if receipt.project_id != project.id:
            raise StorageError("Atelier delete receipt project_id does not match owner")
        if receipt.receipt_id in receipt_ids:
            raise StorageError("Duplicate Atelier delete receipt id")
        receipt_ids.add(receipt.receipt_id)
        if receipt.base_revision > project.revision:
            raise StorageError("Atelier delete receipt base revision is in the future")
        if (receipt.restored_at is None) != (receipt.restore_base_revision is None):
            raise StorageError("Atelier delete receipt restore fields are inconsistent")
        if receipt.restore_base_revision is not None and (
            receipt.restore_base_revision < receipt.base_revision
            or receipt.restore_base_revision > project.revision
        ):
            raise StorageError("Atelier delete receipt restore revision is invalid")
        if len(receipt.node_ids) != len(set(receipt.node_ids)):
            raise StorageError("Duplicate Atelier delete receipt node id")
        snapshot_ids = [snapshot.node.id for snapshot in receipt.node_snapshots]
        if snapshot_ids != receipt.node_ids:
            raise StorageError("Atelier delete receipt snapshot ids do not match node_ids")
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise StorageError("Duplicate Atelier delete receipt snapshot id")
        snapshot_indices = [snapshot.original_index for snapshot in receipt.node_snapshots]
        if len(snapshot_indices) != len(set(snapshot_indices)):
            raise StorageError("Duplicate Atelier delete receipt snapshot index")
        for snapshot in receipt.node_snapshots:
            if snapshot.node.project_id != project.id:
                raise StorageError("Atelier delete receipt node project_id does not match owner")
            for proof in snapshot.source_proofs:
                if proof.target_node_id != snapshot.node.id:
                    raise StorageError("Atelier delete receipt source proof owner mismatch")
        seen_groups: set[tuple[str, str]] = set()
        for group in receipt.data_change_groups:
            key = (group.node_id, group.group)
            if key in seen_groups:
                raise StorageError("Duplicate Atelier delete receipt data group")
            seen_groups.add(key)
            allowed = allowed_groups.get(group.group)
            before_fields = set(group.before)
            after_fields = set(group.after)
            if allowed is None or before_fields != after_fields or before_fields not in allowed:
                raise StorageError("Atelier delete receipt data group fields are invalid")
            for proof in group.source_proofs:
                if proof.target_node_id != group.node_id:
                    raise StorageError("Atelier delete receipt source proof owner mismatch")
        if receipt.restored_at is None and set(snapshot_ids) & live_node_ids:
            raise StorageError("Unconsumed Atelier delete receipt overlaps live node ids")
    upload_receipt_ids: set[str] = set()
    upload_client_ids: set[str] = set()
    for receipt in project.node_upload_receipts:
        if receipt.project_id != project.id:
            raise StorageError("Atelier upload receipt project_id does not match owner")
        if receipt.receipt_id in upload_receipt_ids:
            raise StorageError("Duplicate Atelier upload receipt id")
        upload_receipt_ids.add(receipt.receipt_id)
        if receipt.client_request_id in upload_client_ids:
            raise StorageError("Duplicate Atelier upload request id")
        upload_client_ids.add(receipt.client_request_id)
        if _UPLOAD_CLIENT_REQUEST_ID_RE.fullmatch(receipt.client_request_id) is None:
            raise StorageError("Atelier upload receipt client request id is invalid")
        if receipt.committed_revision != receipt.base_revision + 1 or receipt.committed_revision > project.revision:
            raise StorageError("Atelier upload receipt revision is invalid")
        _validate_upload_media_proof(receipt.media)
        if receipt.previous_media is not None or receipt.replaced_node_id is not None:
            raise StorageError("Atelier upload replacement receipt fields are not supported")
        if len(receipt.node_ids) != len(set(receipt.node_ids)) or any(not item for item in receipt.node_ids):
            raise StorageError("Duplicate Atelier upload receipt node id")
        if receipt.operation == "create_media_node" and (
            len(receipt.node_ids) != 1
            or receipt.target_node_id is not None
            or receipt.source_node_id != receipt.node_ids[0]
            or receipt.replaced_node_id is not None
        ):
            raise StorageError("Atelier upload create receipt fields are invalid")
        if receipt.operation == "upload_reference_attach" and (
            len(receipt.node_ids) != 2
            or receipt.target_node_id != receipt.node_ids[0]
            or receipt.source_node_id != receipt.node_ids[1]
            or receipt.replaced_node_id is not None
        ):
            raise StorageError("Atelier upload reference receipt fields are invalid")
    validate_generation_ledger(project, StorageError)
    _validate_candidate_judgments(project)
    _validate_sequence_export_jobs(project)
    # Validate strict JSON compatibility and duplicate-key-safe serialization.
    serialize_json(project.model_dump(mode="json"))


def _validate_candidate_judgments(project: AtelierProject) -> None:
    if not project.candidate_judgments:
        return
    ids: set[str] = set()
    latest_by_candidate: dict[tuple[str, str], str] = {}
    kind_by_candidate: dict[tuple[str, str], str] = {}
    referenced_candidates = {(judgment.node_id, judgment.candidate_id) for judgment in project.candidate_judgments}
    live_candidates = _live_candidate_index(project, referenced_candidates=referenced_candidates)
    live_node_ids = {node.id for node in project.nodes}
    for judgment in project.candidate_judgments:
        if judgment.project_id != project.id:
            raise StorageError("Atelier candidate judgment project_id does not match owner")
        if judgment.id in ids:
            raise StorageError("Duplicate Atelier candidate judgment id")
        ids.add(judgment.id)
        key = (judgment.node_id, judgment.candidate_id)
        previous_id = latest_by_candidate.get(key)
        if judgment.supersedes_judgment_id != previous_id:
            raise StorageError("Atelier candidate judgment chain is invalid")
        previous_kind = kind_by_candidate.get(key)
        if previous_kind is not None and judgment.media_kind != previous_kind:
            raise StorageError("Atelier candidate judgment media_kind is inconsistent")
        latest_by_candidate[key] = judgment.id
        kind_by_candidate[key] = judgment.media_kind
        candidate_kind = live_candidates.get(key)
        if candidate_kind is not None:
            if judgment.media_kind != candidate_kind:
                raise StorageError("Atelier candidate judgment media_kind does not match candidate")
        elif judgment.node_id in live_node_ids:
            raise StorageError("Atelier candidate judgment candidate is missing from live node")


def _live_candidate_index(
    project: AtelierProject,
    *,
    referenced_candidates: set[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], str] = {}
    referenced_nodes = {node_id for node_id, _candidate_id in referenced_candidates}
    for node in project.nodes:
        if node.id not in referenced_nodes:
            continue
        if node.type not in {"image", "video", "audio"}:
            continue
        data = node.data if isinstance(node.data, dict) else {}
        for bucket in ("candidates", "deleted_candidates"):
            candidates = data.get(bucket, [])
            if candidates is None:
                continue
            if not isinstance(candidates, list):
                raise StorageError("Atelier candidate collection is invalid")
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    raise StorageError("Atelier candidate collection is invalid")
                candidate_id = candidate.get("id")
                if not isinstance(candidate_id, str) or not candidate_id:
                    continue
                if (node.id, candidate_id) not in referenced_candidates:
                    continue
                media_kind = _candidate_media_kind(node.type, candidate)
                key = (node.id, candidate_id)
                if key in values and values[key] != media_kind:
                    raise StorageError("Atelier candidate media_kind is inconsistent")
                values[key] = media_kind
    return values


def _candidate_media_kind(node_type: str, candidate: dict[str, Any]) -> str:
    if candidate.get("execution_contract") == MEDIA_GENERATION_CANDIDATE_CONTRACT:
        media_kind = candidate.get("media_kind")
        if media_kind not in {"image", "audio"}:
            raise StorageError("Atelier media candidate kind is invalid")
        return media_kind
    media_kind = candidate.get("media_kind", "video")
    if candidate.get("execution_contract") == GENERATION_CANDIDATE_CONTRACT or media_kind == "video":
        return "video"
    if media_kind in {"image", "audio"}:
        return media_kind
    if node_type in {"image", "video", "audio"}:
        return node_type
    raise StorageError("Atelier candidate media_kind is invalid")


def _validate_branch_source(node) -> None:
    from .candidate_branch_types import validate_branch_source

    data = node.data if isinstance(node.data, dict) else {}
    if "branch_source" not in data:
        return
    try:
        source = validate_branch_source(data["branch_source"])
    except ValueError as exc:
        raise StorageError("Atelier branch source is invalid") from exc
    if source.media_kind != node.type:
        raise StorageError("Atelier branch source media kind does not match node")


def _validate_upload_media_proof(media) -> None:
    if _UPLOAD_REF_RE.fullmatch(media.url) is None:
        raise StorageError("Atelier upload receipt media reference is invalid")
    poster_fields = (media.poster_url, media.poster_size_bytes, media.poster_sha256)
    if any(value is not None for value in poster_fields) and media.poster_url == media.url:
        raise StorageError("Atelier upload receipt poster proof is invalid")
    suffix = Path(media.url).suffix.lower()
    if suffix not in _UPLOAD_EXTENSIONS.get(media.media_kind, set()):
        raise StorageError("Atelier upload receipt media extension is invalid")
    if media.media_kind == "image":
        if media.width is None or media.height is None:
            raise StorageError("Atelier upload receipt image dimensions are invalid")
        if media.duration is not None:
            raise StorageError("Atelier upload receipt image duration is invalid")
    elif media.media_kind == "audio":
        if media.width is not None or media.height is not None:
            raise StorageError("Atelier upload receipt audio dimensions are invalid")
        if media.duration is None:
            raise StorageError("Atelier upload receipt audio duration is invalid")
    elif media.media_kind == "video":
        if media.width is None or media.height is None:
            raise StorageError("Atelier upload receipt video dimensions are invalid")
        if media.duration is None:
            raise StorageError("Atelier upload receipt video duration is invalid")
    if any(value is not None for value in poster_fields):
        if media.media_kind != "video" or not all(value is not None for value in poster_fields):
            raise StorageError("Atelier upload receipt poster proof is invalid")
        if _UPLOAD_REF_RE.fullmatch(media.poster_url or "") is None:
            raise StorageError("Atelier upload receipt poster proof is invalid")
        if Path(media.poster_url or "").suffix.lower() != ".jpg" or media.poster_url == media.url:
            raise StorageError("Atelier upload receipt poster proof is invalid")


_EXPORT_CLIENT_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_EXPORT_OUTPUT_RE = re.compile(r"^video/atelier_seq_[A-Za-z0-9_-]+\.mp4$")
_IMPORTED_EXPORT_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")
_IMMUTABLE_EXPORT_JOB_FIELDS = {
    "schema_version",
    "job_id",
    "project_id",
    "request_contract_version",
    "client_request_id",
    "source_export_id",
    "request_fingerprint",
    "base_revision",
    "created_at",
    "snapshot_digest",
    "snapshot",
    "source_entries",
    "source_audio_tracks",
    "output_ref",
}
_ALLOWED_EXPORT_STATUS_TRANSITIONS = {
    "queued": {"queued", "running", "cancelling", "cancelled", "failed", "repair_required", "completed"},
    "running": {"running", "cancelling", "cancelled", "failed", "repair_required", "completed"},
    "cancelling": {"cancelling", "cancelled", "failed", "repair_required", "completed"},
    "repair_required": {"repair_required", "failed", "completed"},
    "cancelled": {"cancelled"},
    "failed": {"failed"},
    "completed": {"completed"},
}
_PENDING_EXPORT_JOB_STATUSES = {"queued", "running", "cancelling", "repair_required"}
_PENDING_EXPORT_DELETION_STATUSES = {"pending"}


def _validate_sequence_export_jobs(project: AtelierProject) -> None:
    job_ids: set[str] = set()
    client_ids: set[str] = set()
    for job in project.sequence_export_jobs:
        if job.project_id != project.id:
            raise StorageError("Atelier export job project_id does not match owner")
        if job.job_id in job_ids:
            raise StorageError("Duplicate Atelier export job id")
        job_ids.add(job.job_id)
        if job.client_request_id in client_ids:
            raise StorageError("Duplicate Atelier export client request id")
        client_ids.add(job.client_request_id)
        if job.request_contract_version == "atelier-sequence-export-v1" and job.source_export_id is not None:
            raise StorageError("Atelier export v1 job cannot reference a source export")
        if job.request_contract_version == "atelier-sequence-export-redo-v1" and not job.source_export_id:
            raise StorageError("Atelier redo export job requires a source export")
        if _EXPORT_CLIENT_REQUEST_ID_RE.fullmatch(job.client_request_id) is None:
            raise StorageError("Atelier export job client request id is invalid")
        if job.output_ref != f"video/atelier_seq_{job.job_id}.mp4" or _EXPORT_OUTPUT_RE.fullmatch(job.output_ref) is None:
            raise StorageError("Atelier export job output ref is invalid")
        if job.snapshot_digest != _canonical_sequence_snapshot_digest(job.snapshot):
            raise StorageError("Atelier export job snapshot digest mismatch")
        if job.base_revision > project.revision:
            raise StorageError("Atelier export job base revision is in the future")
        if job.finished_at is not None and job.started_at is not None and job.finished_at < job.started_at:
            raise StorageError("Atelier export job timestamps are invalid")
        _validate_sequence_publication_proof(job.prepared_proof, project_id=project.id, job_id=job.job_id, output_ref=job.output_ref, snapshot_digest=job.snapshot_digest)
        if job.status == "repair_required" and job.prepared_proof is None:
            raise StorageError("Atelier export repair proof is required")
        if job.status == "completed" and (job.prepared_proof is None or not job.export_id or job.finished_at is None):
            raise StorageError("Atelier export completed job is missing proof")
        if job.status != "completed" and job.export_id is not None:
            raise StorageError("Atelier export job export_id is invalid")
    exports_by_job_id: dict[str, Any] = {}
    deletion_client_ids: set[str] = set()
    for export in project.exports:
        _validate_imported_export_proof(export, project_id=project.id)
        _validate_export_deletion_receipt(export.deletion)
        if export.deletion is not None:
            if export.deletion.client_request_id in deletion_client_ids:
                raise StorageError("Duplicate Atelier export deletion request id")
            deletion_client_ids.add(export.deletion.client_request_id)
        if export.publication_proof is not None:
            if export.job_id is None or export.snapshot_digest is None:
                raise StorageError("Atelier export proof fields are incomplete")
            _validate_sequence_publication_proof(
                export.publication_proof,
                project_id=project.id,
                job_id=export.job_id,
                output_ref=export.video_url,
                snapshot_digest=export.snapshot_digest,
            )
            if export.publication_proof.relative_path != export.video_url:
                raise StorageError("Atelier export proof path does not match export")
            if export.job_id in exports_by_job_id:
                raise StorageError("Duplicate Atelier export job export")
            exports_by_job_id[export.job_id] = export
        elif export.job_id is not None or export.snapshot_digest is not None:
            raise StorageError("Atelier export proof fields are incomplete")
    for job in project.sequence_export_jobs:
        if job.status == "completed":
            export = exports_by_job_id.get(job.job_id)
            if export is None or export.id != job.export_id:
                raise StorageError("Atelier completed export job has no matching completed export")
            if export.publication_proof != job.prepared_proof:
                raise StorageError("Atelier completed export proof does not match job")
            _validate_completed_sequence_export_record(export, job)


def _validate_imported_export_proof(export, *, project_id: str) -> None:
    proof = export.imported_proof
    if proof is None:
        if isinstance(export.video_url, str) and export.video_url.startswith(f"video/imported/{project_id}/"):
            raise StorageError("Atelier imported export proof is required")
        return
    if export.publication_proof is not None or export.job_id is not None or export.snapshot_digest is not None:
        raise StorageError("Atelier imported export proof cannot be mixed with render authority")
    if proof.project_id != project_id or proof.export_id != export.id:
        raise StorageError("Atelier imported export proof owner mismatch")
    if proof.relative_path != export.video_url:
        raise StorageError("Atelier imported export proof path does not match export")
    if not _is_imported_export_path(proof.relative_path, project_id=project_id):
        raise StorageError("Atelier imported export proof path is invalid")
    if Path(proof.relative_path).name != export.filename:
        raise StorageError("Atelier imported export proof filename mismatch")


def _is_imported_export_path(value: str, *, project_id: str) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split("/")
    if len(parts) < 4 or parts[0] != "video" or parts[1] != "imported" or parts[2] != project_id:
        return False
    if _IMPORTED_EXPORT_COMPONENT_RE.fullmatch(project_id) is None:
        return False
    if Path(parts[-1]).suffix.lower() != ".mp4":
        return False
    return all(_IMPORTED_EXPORT_COMPONENT_RE.fullmatch(part) is not None for part in parts[3:])


def _validate_export_deletion_receipt(receipt: ExportDeletionReceipt | None) -> None:
    if receipt is None:
        return
    if receipt.file_status == "retained":
        if receipt.delete_file or receipt.checked_at is None or receipt.error_code is not None or receipt.error_message is not None:
            raise StorageError("Atelier export deletion retained receipt is invalid")
        return
    if not receipt.delete_file:
        raise StorageError("Atelier export deletion file status is invalid")
    if receipt.file_status == "pending":
        if receipt.checked_at is not None or receipt.error_code is not None or receipt.error_message is not None:
            raise StorageError("Atelier export deletion pending receipt is invalid")
        return
    if receipt.checked_at is None:
        raise StorageError("Atelier export deletion checked_at is required")
    if receipt.file_status == "blocked":
        if not receipt.error_code or not receipt.error_message:
            raise StorageError("Atelier export deletion blocked receipt is invalid")
        return
    if receipt.file_status in {"deleted", "already_missing"}:
        if receipt.error_code is not None or receipt.error_message is not None:
            raise StorageError("Atelier export deletion final receipt is invalid")
        return
    raise StorageError("Atelier export deletion file status is invalid")


def _validate_completed_sequence_export_record(export, job: AtelierSequenceExportJob) -> None:
    proof = job.prepared_proof
    if proof is None:
        raise StorageError("Atelier completed export proof does not match job")
    if export.timeline_snapshot != job.snapshot:
        raise StorageError("Atelier completed export snapshot does not match job")
    if export.source_entries != job.source_entries:
        raise StorageError("Atelier completed export source entries do not match job")
    if export.filename != Path(job.output_ref).name:
        raise StorageError("Atelier completed export filename does not match job")
    if export.clip_count != len(job.source_entries):
        raise StorageError("Atelier completed export clip count does not match job")
    if export.size_mb != proof.size_bytes / 1024 / 1024:
        raise StorageError("Atelier completed export size does not match proof")


def _canonical_sequence_snapshot_digest(snapshot: Any) -> str:
    try:
        encoded = json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise StorageError(f"Atelier export snapshot digest cannot be computed: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _validate_sequence_publication_proof(proof, *, project_id: str, job_id: str, output_ref: str, snapshot_digest: str) -> None:
    if proof is None:
        return
    if proof.project_id != project_id or proof.job_id != job_id:
        raise StorageError("Atelier export proof owner mismatch")
    if proof.relative_path != output_ref or _EXPORT_OUTPUT_RE.fullmatch(proof.relative_path) is None:
        raise StorageError("Atelier export proof path is invalid")
    if proof.snapshot_digest != snapshot_digest:
        raise StorageError("Atelier export proof snapshot digest mismatch")


def _project_without_export_jobs(project: AtelierProject) -> dict[str, Any]:
    payload = project.model_dump(mode="json")
    payload.pop("updated_at", None)
    payload.pop("sequence_export_jobs", None)
    return payload


def _project_without_jobs_and_exports(project: AtelierProject) -> dict[str, Any]:
    payload = _project_without_export_jobs(project)
    payload.pop("exports", None)
    return payload


def _find_export_job(project: AtelierProject, job_id: str) -> AtelierSequenceExportJob:
    for job in project.sequence_export_jobs:
        if job.job_id == job_id:
            return job
    raise StorageConflictError("Atelier export job not found")


def _find_export_job_by_request(project: AtelierProject, client_request_id: str) -> AtelierSequenceExportJob | None:
    return next((job for job in project.sequence_export_jobs if job.client_request_id == client_request_id), None)


def _find_export_record(project: AtelierProject, export_id: str):
    for export in project.exports:
        if export.id == export_id:
            return export
    raise StorageConflictError("Atelier export record not found")


def _find_export_deletion_by_request(project: AtelierProject, client_request_id: str):
    return next((export for export in project.exports if export.deletion is not None and export.deletion.client_request_id == client_request_id), None)


def _replace_export_job(project: AtelierProject, replacement: AtelierSequenceExportJob) -> None:
    for index, job in enumerate(project.sequence_export_jobs):
        if job.job_id == replacement.job_id:
            project.sequence_export_jobs[index] = replacement
            return
    raise StorageConflictError("Atelier export job not found")


def _assert_export_status_transition(before: str, after: str) -> None:
    if after not in _ALLOWED_EXPORT_STATUS_TRANSITIONS.get(before, {before}):
        raise StorageError("Atelier export job status transition is invalid")


def _assert_export_job_immutable_fields(before: AtelierSequenceExportJob, after: AtelierSequenceExportJob) -> None:
    before_payload = before.model_dump(mode="json")
    after_payload = after.model_dump(mode="json")
    for field in _IMMUTABLE_EXPORT_JOB_FIELDS:
        if before_payload.get(field) != after_payload.get(field):
            raise StorageError("Atelier export job immutable fields changed")
    if before.prepared_proof is not None and before.prepared_proof != after.prepared_proof:
        raise StorageError("Atelier export prepared proof cannot change once recorded")


def _validate_database(value: Any) -> dict[str, AtelierProject]:
    if not isinstance(value, dict):
        raise StorageError("Invalid Atelier storage database")
    if "schema_version" in value or "projects" in value:
        raise StorageError("Unsupported Atelier storage envelope")
    projects: dict[str, AtelierProject] = {}
    for project_id, payload in value.items():
        if type(project_id) is not str or not project_id:
            raise StorageError("Invalid Atelier project id")
        if project_id in projects:
            raise StorageError("Duplicate Atelier project id")
        projects[project_id] = _project_from_payload(project_id, payload)
    return projects


def _find_agent_canvas_call(project: AtelierProject, turn_id: str, call_id: str):
    for turn in project.agent_turns:
        if turn.id == turn_id:
            for call in turn.tool_calls:
                if call.call_id == call_id:
                    return call
    return None


def _restore_agent_canvas_command_for_comparison(
    before: dict[str, Any],
    after: dict[str, Any],
    command: ApplyApprovedAgentCanvasCommand,
    *,
    now: float,
    final_turn_status: str = "completed",
    final_completed_at: float | None = None,
) -> None:
    old_turn = next((turn for turn in before["agent_turns"] if turn["id"] == command.turn_id), None)
    new_turn = next((turn for turn in after["agent_turns"] if turn["id"] == command.turn_id), None)
    if old_turn is None or new_turn is None:
        raise StorageError("Agent canvas command requires its existing turn")
    old_call = next((call for call in old_turn["tool_calls"] if call["call_id"] == command.call_id), None)
    new_call = next((call for call in new_turn["tool_calls"] if call["call_id"] == command.call_id), None)
    if old_call is None or new_call is None:
        raise StorageError("Agent canvas command requires its existing call")
    binding = old_call.get("canvas_approval_binding")
    if not isinstance(binding, dict):
        raise StorageError("Agent canvas command requires its binding")
    ref = binding.get("canvas_authorization_ref")
    if not isinstance(ref, dict):
        raise StorageError("Agent canvas command requires its authorization ref")
    tool_name = ref.get("tool_name")
    result = new_call.get("result_snapshot")
    if not isinstance(result, dict) or result.get("committed_revision") != before["revision"] + 1:
        raise StorageError("Agent canvas command result is invalid")
    node_ids = result.get("node_ids")
    if not isinstance(node_ids, list) or not all(isinstance(item, str) for item in node_ids):
        raise StorageError("Agent canvas command result node ids are invalid")
    if tool_name == "canvas.createVideoNode":
        if len(node_ids) != 1 or len(after["nodes"]) != len(before["nodes"]) + 1 or after["nodes"][:-1] != before["nodes"] or after["nodes"][-1]["id"] != node_ids[0]:
            raise StorageError("Agent canvas create must append exactly one node")
        after["nodes"].pop()
    elif tool_name == "canvas.updateNodePrompt":
        if len(node_ids) != 1:
            raise StorageError("Agent canvas update must affect exactly one node")
        old_node = next((node for node in before["nodes"] if node["id"] == node_ids[0]), None)
        new_node = next((node for node in after["nodes"] if node["id"] == node_ids[0]), None)
        if old_node is None or new_node is None:
            raise StorageError("Agent canvas update requires an existing node")
        for field in ("title", "prompt", "data", "updated_at"):
            new_node[field] = deepcopy(old_node[field])
    else:
        raise StorageError("Agent canvas command tool is invalid")
    if new_call.get("status") != "completed" or new_call.get("completed_at") != now:
        raise StorageError("Agent canvas call completion is invalid")
    for field in ("status", "completed_at", "result_snapshot"):
        new_call[field] = deepcopy(old_call[field])
    old_pending = old_turn.get("pending_tool_calls", [])
    expected_pending = [item for item in old_pending if not (isinstance(item, dict) and item.get("call_id") == command.call_id)]
    if new_turn.get("pending_tool_calls") != expected_pending:
        raise StorageError("Agent canvas pending calls were not consumed exactly")
    new_turn["pending_tool_calls"] = deepcopy(old_pending)
    old_messages = old_turn.get("messages_history", [])
    new_messages = new_turn.get("messages_history", [])
    if len(new_messages) != len(old_messages) + 1 or new_messages[:-1] != old_messages:
        raise StorageError("Agent canvas tool message was not appended exactly")
    message = new_messages[-1]
    if not isinstance(message, dict) or message.get("role") != "tool" or message.get("tool_call_id") != command.call_id or message.get("name") != tool_name or message.get("content") != result:
        raise StorageError("Agent canvas tool message is invalid")
    new_turn["messages_history"] = deepcopy(old_messages)
    expected_complete = all(call["status"] in {"completed", "denied"} or call["call_id"] == command.call_id for call in new_turn["tool_calls"])
    if expected_complete:
        expected_completed_at = now if final_turn_status == "completed" and final_completed_at is None else final_completed_at
        if new_turn.get("status") != final_turn_status or new_turn.get("completed_at") != expected_completed_at:
            raise StorageError("Agent canvas turn completion is invalid")
        new_turn["status"] = deepcopy(old_turn["status"])
        new_turn["completed_at"] = deepcopy(old_turn["completed_at"])


def _loop_context_payload(turn: dict[str, Any]) -> dict[str, Any] | None:
    value = turn.get("loop_start_context")
    return value if isinstance(value, dict) else None


def _assert_loop_contexts_immutable(before: dict[str, Any], after: dict[str, Any]) -> None:
    before_by_id = {turn.get("id"): turn for turn in before.get("agent_turns", []) if isinstance(turn, dict)}
    for new_turn in after.get("agent_turns", []):
        if not isinstance(new_turn, dict):
            continue
        old_turn = before_by_id.get(new_turn.get("id"))
        if old_turn is not None and _loop_context_payload(old_turn) != _loop_context_payload(new_turn):
            raise StorageError("Atelier Agent loop start context is immutable")
        context = _loop_context_payload(new_turn)
        if context is None:
            continue
        if new_turn.get("start_request_fingerprint") is None:
            raise StorageError("Atelier Agent loop requires its start fingerprint")
        if new_turn.get("user_message") != context.get("user_message"):
            raise StorageError("Atelier Agent loop user message changed")
        if new_turn.get("skill_name") != context.get("skill_name"):
            raise StorageError("Atelier Agent loop skill name changed")
        if new_turn.get("max_iterations") != context.get("max_iterations"):
            raise StorageError("Atelier Agent loop max iterations changed")



def _agent_loop_canvas_replay_matches(call: Any, command: ApplyApprovedAgentCanvasCommand) -> bool:
    binding = getattr(call, "canvas_approval_binding", None)
    ref = getattr(binding, "canvas_authorization_ref", None) if binding is not None else None
    return (
        ref is not None
        and ref.turn_id == command.turn_id
        and ref.call_id == command.call_id
        and ref.approval_id == command.approval_id
        and ref.authorization_hash == command.authorization_hash
    )


def _validate_agent_loop_iteration_calls(project: AtelierProject, turn: AtelierAgentTurn, calls: tuple) -> None:
    from .agent_authorization import TOOL_GENERATION_CREATE_VIDEO_CANDIDATES, policy_snapshot_hash, validate_agent_generation_binding
    from .agent_canvas_authorization import (
        TOOL_CANVAS_CREATE_VIDEO_NODE,
        TOOL_CANVAS_UPDATE_NODE_PROMPT,
        validate_agent_canvas_binding,
    )
    from .models import AtelierAgentToolStatus

    if len(calls) > 8:
        raise StorageConflictError("atelier_agent_loop_too_many_tool_calls")
    skill_snapshot = turn.loop_start_context.skill_snapshot if turn.loop_start_context is not None else None
    skill_tools = set(skill_snapshot.expected_tools) if skill_snapshot is not None and skill_snapshot.expected_tools else None
    seen_call_ids = {existing.call_id for existing in turn.tool_calls}
    mutation_targets: set[str] = set()
    generation_count = 0
    for call in calls:
        if call.call_id in seen_call_ids:
            raise StorageConflictError("atelier_agent_loop_call_id_conflict")
        seen_call_ids.add(call.call_id)
        if skill_tools is not None and call.tool_name not in skill_tools:
            raise StorageConflictError("atelier_product_skill_tool_not_allowed")
        if project.agent_policy.allowed_tools and call.tool_name not in project.agent_policy.allowed_tools:
            raise StorageConflictError("atelier_agent_loop_tool_not_allowed")
        if call.tool_name == "canvas.readProject":
            if (
                call.status != AtelierAgentToolStatus.PROPOSED
                or call.approval_required
                or call.approval_granted
                or call.canvas_approval_binding is not None
                or call.canvas_approval_record is not None
                or call.preview_binding is not None
                or call.approval_record is not None
                or call.result_snapshot is not None
                or call.completed_at is not None
            ):
                raise StorageConflictError("atelier_agent_loop_invalid_read_call")
            continue
        if call.tool_name in {TOOL_CANVAS_CREATE_VIDEO_NODE, TOOL_CANVAS_UPDATE_NODE_PROMPT}:
            if (
                call.status != AtelierAgentToolStatus.APPROVAL_REQUIRED
                or not call.approval_required
                or call.approval_granted
                or call.canvas_approval_binding is None
                or call.canvas_approval_record is not None
                or call.preview_binding is not None
                or call.approval_record is not None
                or call.result_snapshot is not None
                or call.completed_at is not None
            ):
                raise StorageConflictError("atelier_agent_loop_invalid_canvas_call")
            binding = validate_agent_canvas_binding(call.canvas_approval_binding, error=StorageConflictError)
            ref = binding.canvas_authorization_ref
            if ref.project_id != project.id or ref.turn_id != turn.id or ref.call_id != call.call_id or ref.tool_name != call.tool_name:
                raise StorageConflictError("atelier_agent_loop_canvas_binding_mismatch")
            if ref.policy_snapshot_hash != policy_snapshot_hash(project.agent_policy):
                raise StorageConflictError("atelier_agent_loop_policy_changed")
            if ref.target_fingerprint in mutation_targets:
                raise StorageConflictError("atelier_agent_loop_conflicting_canvas_batch")
            mutation_targets.add(ref.target_fingerprint)
            continue
        if call.tool_name == TOOL_GENERATION_CREATE_VIDEO_CANDIDATES:
            generation_count += 1
            if generation_count > 1:
                raise StorageConflictError("atelier_agent_loop_too_many_generation_calls")
            if (
                call.status != AtelierAgentToolStatus.APPROVAL_REQUIRED
                or not call.approval_required
                or call.approval_granted
                or call.preview_binding is None
                or call.approval_record is not None
                or call.result_snapshot is not None
                or call.completed_at is not None
                or call.canvas_approval_binding is not None
                or call.canvas_approval_record is not None
            ):
                raise StorageConflictError("atelier_agent_loop_invalid_generation_call")
            binding = validate_agent_generation_binding(call.preview_binding, error=StorageConflictError)
            ref = binding.agent_authorization_ref
            if ref.project_id != project.id or ref.turn_id != turn.id or ref.call_id != call.call_id or ref.tool_name != call.tool_name:
                raise StorageConflictError("atelier_agent_loop_generation_binding_mismatch")
            if ref.policy_snapshot_hash != policy_snapshot_hash(project.agent_policy):
                raise StorageConflictError("atelier_agent_loop_policy_changed")
            continue
        raise StorageConflictError("atelier_agent_loop_tool_not_implemented")



def _loop_generation_attempt_for_call(project: AtelierProject, call: Any) -> AtelierGenerationAttempt | None:
    if not isinstance(getattr(call, "result_snapshot", None), dict):
        return None
    client_request_id = call.result_snapshot.get("client_request_id")
    if not isinstance(client_request_id, str):
        return None
    receipt = next((item for item in project.generation_request_receipts if item.client_request_id == client_request_id), None)
    if receipt is None:
        raise StorageConflictError("atelier_agent_loop_generation_receipt_missing")
    attempt = next((item for item in project.generation_attempts if item.attempt_id == receipt.attempt_id), None)
    if attempt is None:
        raise StorageConflictError("atelier_agent_loop_generation_attempt_missing")
    return attempt


def _assert_no_blocking_loop_generation(project: AtelierProject, turn: AtelierAgentTurn) -> None:
    for call in turn.tool_calls:
        attempt = _loop_generation_attempt_for_call(project, call)
        if attempt is not None and attempt.status not in GENERATION_TERMINAL_STATUSES:
            raise StorageConflictError("atelier_agent_loop_generation_not_terminal")


def _assert_import_authority_unchanged(before: AtelierProject, after: AtelierProject) -> None:
    if before.import_provenance != after.import_provenance:
        raise StorageError("Atelier product command cannot change import provenance")
    after_exports = {export.id: export for export in after.exports}
    for export in before.exports:
        if export.imported_proof is None:
            continue
        replacement = after_exports.get(export.id)
        if replacement is None or replacement.model_dump(mode="json") != export.model_dump(mode="json"):
            raise StorageError("Atelier product command cannot change imported export proof")
    before_exports = {export.id: export for export in before.exports}
    for export in after.exports:
        if export.imported_proof is not None and (
            export.id not in before_exports or before_exports[export.id].imported_proof is None
        ):
            raise StorageError("Atelier product command cannot create imported export proof")


def _validate_agent_planner_invocations(project: AtelierProject, turn: AtelierAgentTurn) -> None:
    from .agent_planner_invocations import (
        ClaimAgentPlannerInvocationCommand,
        SettleAgentPlannerInvocationCommand,
        claim_config_fingerprint,
        claim_fingerprint,
        claim_input_fingerprint,
        settlement_fingerprint,
    )

    seen_iterations: set[int] = set()
    seen_ids: set[str] = set()
    claimed_reservations = 0
    charged_tokens = 0
    for invocation in turn.planner_invocations:
        if invocation.project_id != project.id or invocation.turn_id != turn.id:
            raise StorageError("Atelier planner invocation owner mismatch")
        if invocation.invocation_id != f"{turn.id}:iteration:{invocation.iteration_index}":
            raise StorageError("Atelier planner invocation id is invalid")
        if invocation.invocation_id in seen_ids:
            raise StorageError("Duplicate Atelier planner invocation id")
        seen_ids.add(invocation.invocation_id)
        if invocation.iteration_index in seen_iterations:
            raise StorageError("Duplicate Atelier planner invocation iteration")
        seen_iterations.add(invocation.iteration_index)
        if invocation.reserved_completion_tokens != invocation.max_completion_tokens:
            raise StorageError("Atelier planner invocation reservation mismatch")
        if invocation.max_completion_tokens > invocation.budget_completion_tokens:
            raise StorageError("Atelier planner invocation exceeds budget snapshot")
        try:
            claim_command = ClaimAgentPlannerInvocationCommand(
                expected_revision=0,
                turn_id=invocation.turn_id,
                iteration_index=invocation.iteration_index,
                claim_request_id=invocation.claim_request_id,
                planner=invocation.planner,
                provider_id=invocation.provider_id,
                model_id=invocation.model_id,
                config_identity=invocation.config_identity,
                input_messages=tuple(invocation.input_messages),
                max_completion_tokens=invocation.max_completion_tokens,
                timeout_seconds=invocation.timeout_seconds,
            )
        except ValueError as exc:
            raise StorageError("Atelier planner invocation claim payload is invalid") from exc
        if invocation.input_fingerprint != claim_input_fingerprint(claim_command):
            raise StorageError("Atelier planner invocation input fingerprint mismatch")
        if invocation.config_fingerprint != claim_config_fingerprint(claim_command):
            raise StorageError("Atelier planner invocation config fingerprint mismatch")
        if invocation.claim_fingerprint != claim_fingerprint(claim_command):
            raise StorageError("Atelier planner invocation claim fingerprint mismatch")
        if invocation.status == "claimed":
            if (
                invocation.settled_at is not None
                or invocation.settlement_fingerprint is not None
                or invocation.outcome is not None
                or invocation.result_content is not None
                or invocation.provider_model is not None
                or invocation.usage is not None
                or invocation.charged_completion_tokens is not None
                or invocation.error_code is not None
                or invocation.error_message is not None
            ):
                raise StorageError("Atelier claimed planner invocation has settlement fields")
            claimed_reservations += invocation.reserved_completion_tokens
        elif invocation.status == "settled":
            if (
                invocation.settled_at is None
                or invocation.settlement_fingerprint is None
                or invocation.outcome is None
                or invocation.charged_completion_tokens is None
            ):
                raise StorageError("Atelier settled planner invocation is incomplete")
            expected_charge = (
                invocation.usage.completion_tokens
                if invocation.usage is not None and invocation.usage.completion_tokens is not None
                else invocation.reserved_completion_tokens
            )
            if invocation.charged_completion_tokens != expected_charge:
                raise StorageError("Atelier planner invocation charged usage mismatch")
            try:
                settle_command = SettleAgentPlannerInvocationCommand(
                    invocation_id=invocation.invocation_id,
                    outcome=invocation.outcome,
                    result_content=invocation.result_content,
                    provider_model=invocation.provider_model,
                    usage=invocation.usage,
                    error_code=invocation.error_code,
                    error_message=invocation.error_message,
                )
            except ValueError as exc:
                raise StorageError("Atelier planner invocation settlement payload is invalid") from exc
            if invocation.settlement_fingerprint != settlement_fingerprint(settle_command):
                raise StorageError("Atelier planner invocation settlement fingerprint mismatch")
            charged_tokens += invocation.charged_completion_tokens
        else:
            raise StorageError("Atelier planner invocation status is invalid")
    if turn.completion_tokens_reserved != claimed_reservations:
        raise StorageError("Atelier planner invocation reservation total mismatch")
    if turn.planner_invocations and turn.completion_tokens_used < charged_tokens:
        raise StorageError("Atelier planner invocation usage total mismatch")


def _find_planner_invocation(
    project: AtelierProject,
    invocation_id: str,
) -> tuple[AtelierAgentTurn, AtelierAgentPlannerInvocation]:
    for turn in project.agent_turns:
        for invocation in turn.planner_invocations:
            if invocation.invocation_id == invocation_id:
                return turn, invocation
    raise StorageConflictError("atelier_agent_planner_invocation_missing")


def _database_payload(projects: dict[str, AtelierProject]) -> dict[str, Any]:
    return {project_id: project.model_dump(mode="json") for project_id, project in sorted(projects.items())}


class AtelierStorage:
    """Product-owned Atelier project store backed by one strict JSON file."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        writer_lock: DirectoryWriterLock | None = None,
        lock: threading.RLock | None = None,
        runtime_check: Callable[[], None] | None = None,
        mark_storage_failed: Callable[[StorageError], None] | None = None,
    ):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.path = self.data_dir / _DB_FILENAME
        self._lock = lock if lock is not None else threading.RLock()
        self._runtime_check = runtime_check or (lambda: None)
        self._mark_storage_failed = mark_storage_failed
        self._owns_writer_lock = writer_lock is None
        if writer_lock is not None and writer_lock.data_dir != self.data_dir:
            writer_lock.assert_owned_path()
            raise StorageConflictError("Borrowed writer lock does not own Atelier data directory")
        self._writer_lock = writer_lock or DirectoryWriterLock(self.data_dir).acquire()
        self._closed = False
        self._failed: StorageError | None = None
        self._in_command = False
        try:
            self._writer_lock.assert_owned_path()
            self._projects = _validate_database(read_json(self.path, dict))
        except Exception:
            if self._owns_writer_lock:
                self._writer_lock.close()
            self._closed = True
            raise

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._owns_writer_lock:
                self._writer_lock.close()

    def _check_open(self) -> None:
        if self._failed is not None:
            raise StorageError("Atelier storage failed; restart before continuing") from self._failed
        if self._closed:
            raise StorageConflictError("Atelier storage is closed")
        self._runtime_check()
        self._writer_lock.assert_owned_path()

    def _record_storage_failure(self, error: StorageError) -> None:
        if error.commit_uncertain:
            self._failed = error
        if self._mark_storage_failed is not None:
            try:
                self._mark_storage_failed(error)
            except Exception:
                # Preserve the primary storage error; the store is already latched
                # fail-closed when the commit is uncertain.
                pass

    @staticmethod
    def _copy(project: AtelierProject) -> AtelierProject:
        return project.model_copy(deep=True)

    def list_projects(self) -> list[AtelierProject]:
        with self._lock:
            self._check_open()
            return [self._copy(project) for project in self._projects.values()]

    def get_project(self, project_id: str) -> AtelierProject:
        with self._lock:
            self._check_open()
            try:
                return self._copy(self._projects[project_id])
            except KeyError as exc:
                raise AtelierProjectNotFound("Atelier project not found") from exc

    def save_managed_upload(self, chunks, *, filename: str, content_type: str | None):
        from .media_upload import save_managed_upload

        scope = self._media_write_scope()
        return save_managed_upload(
            chunks,
            filename=filename,
            content_type=content_type,
            data_dir=scope.data_dir,
            writer_lock=scope.writer_lock,
            storage_guard=scope.storage_guard,
        )

    def discard_managed_upload(self, managed) -> None:
        from .media_upload import discard_managed_upload

        scope = self._media_write_scope()
        discard_managed_upload(
            managed,
            data_dir=scope.data_dir,
            writer_lock=scope.writer_lock,
            storage_guard=scope.storage_guard,
        )

    def _media_write_scope(self) -> _AtelierMediaWriteScope:
        @contextmanager
        def guard():
            with self._lock:
                self._check_open()
                yield

        with self._lock:
            self._check_open()
            return _AtelierMediaWriteScope(
                data_dir=self.data_dir,
                writer_lock=self._writer_lock,
                storage_guard=guard,
            )

    def create_project(
        self,
        project: AtelierProject,
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> AtelierProject:
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            if project.id in self._projects:
                raise AtelierProjectExists("Atelier project already exists")
            candidate = _canonical_project(project.model_copy(deep=True, update={"revision": 0}))
            if before_commit is not None:
                self._in_command = True
                try:
                    before_commit()
                finally:
                    self._in_command = False
                self._check_open()
            next_projects = dict(self._projects)
            next_projects[candidate.id] = candidate
            committed = self._commit(next_projects)
            return self._copy(committed[candidate.id])

    def apply_project_command(
        self,
        project_id: str,
        *,
        expected_revision: int,
        command: Callable[[AtelierProject], Any],
    ) -> AtelierProject:
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            if current.revision != expected_revision:
                raise StorageConflictError("Atelier project changed; reload before saving")
            candidate = self._copy(current)
            self._in_command = True
            try:
                command(candidate)
            finally:
                self._in_command = False
            self._check_open()
            if candidate.id != project_id:
                raise StorageError("Atelier command cannot change project id")
            if candidate.revision != expected_revision:
                raise StorageError("Atelier command cannot change project revision")
            removed_node_ids = {node.id for node in current.nodes} - {node.id for node in candidate.nodes}
            if any(
                attempt.node_id in removed_node_ids
                and attempt.status in {"pending", "processing", "needs_confirmation"}
                for attempt in current.generation_attempts
            ):
                raise StorageError("Cannot delete node with active generation")
            assert_generation_unchanged_for_product_command(current, candidate, StorageError)
            if candidate.agent_turns != current.agent_turns:
                raise StorageError("Atelier product command cannot change Agent execution state")
            candidate = _canonical_project(candidate, expected_id=project_id)
            _assert_import_authority_unchanged(current, candidate)
            before = current.model_dump(mode="json")
            after = candidate.model_dump(mode="json")
            if before == after:
                return self._copy(current)
            candidate = _canonical_project(candidate.model_copy(deep=True, update={"revision": expected_revision + 1}), expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)
            return self._copy(committed[project_id])

    def apply_upload_command(
        self,
        project_id: str,
        *,
        expected_revision: int,
        client_request_id: str,
        request_fingerprint: str,
        command: Callable[[AtelierProject], Any],
    ) -> tuple[AtelierProject, bool]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            existing = next(
                (
                    receipt
                    for receipt in current.node_upload_receipts
                    if receipt.client_request_id == client_request_id
                ),
                None,
            )
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise StorageConflictError("Atelier upload request id already belongs to different content")
                return self._copy(current), True
            if current.revision != expected_revision:
                raise StorageConflictError("Atelier project changed; reload before saving")
            candidate = self._copy(current)
            self._in_command = True
            try:
                command(candidate)
            finally:
                self._in_command = False
            self._check_open()
            if candidate.id != project_id:
                raise StorageError("Atelier command cannot change project id")
            if candidate.revision != expected_revision:
                raise StorageError("Atelier command cannot change project revision")
            assert_generation_unchanged_for_product_command(current, candidate, StorageError)
            if candidate.agent_turns != current.agent_turns:
                raise StorageError("Atelier upload command cannot change Agent execution state")
            before = current.model_dump(mode="json")
            after = candidate.model_dump(mode="json")
            if before == after:
                return self._copy(current), False
            candidate = _canonical_project(candidate.model_copy(deep=True, update={"revision": expected_revision + 1}), expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)
            return self._copy(committed[project_id]), False

    def apply_generation_admission(
        self,
        project_id: str,
        *,
        expected_revision: int,
        client_request_id: str,
        request_fingerprint: str,
        command: Callable[[AtelierProject], None],
        expected_count: int = 1,
    ) -> tuple[AtelierProject, bool]:
        """Commit accepted attempts, projections and one original receipt."""
        return self._apply_generation_admission(
            project_id, expected_revision=expected_revision,
            client_request_id=client_request_id, request_fingerprint=request_fingerprint,
            command=command, expected_count=expected_count,
        )


    def read_agent_loop_start_replay(
        self, project_id: str, command,
    ) -> AtelierProject | None:
        from .agent_loop_commands import fingerprint_agent_loop_start

        with self._lock:
            self._check_open()
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            existing = next((turn for turn in current.agent_turns if turn.id == command.client_turn_id), None)
            if existing is None:
                return None
            snapshot = existing.loop_start_context.skill_snapshot if existing.loop_start_context is not None else None
            try:
                fingerprint = fingerprint_agent_loop_start(command, resolved_skill_snapshot=snapshot)
            except ValueError as exc:
                raise StorageConflictError("Atelier Agent loop turn id already belongs to different content") from exc
            if existing.start_request_fingerprint != fingerprint:
                raise StorageConflictError("Atelier Agent loop turn id already belongs to different content")
            return self._copy(current)

    def start_agent_loop_turn(
        self, project_id: str, command, *, now: float, resolved_skill_snapshot=None,
    ) -> tuple[AtelierProject, bool]:
        from .agent_loop_commands import fingerprint_agent_loop_start, loop_start_context
        from .product_skills import CALLABLE_LOOP_TOOL_NAMES

        if command.planner not in {"deterministic_core", "model_adapter"}:
            raise StorageConflictError("atelier_agent_loop_planner_not_supported")
        with self._lock:
            self._check_open()
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            existing = next((turn for turn in current.agent_turns if turn.id == command.client_turn_id), None)
            if existing is not None:
                stored_snapshot = existing.loop_start_context.skill_snapshot if existing.loop_start_context is not None else None
                if resolved_skill_snapshot is not None and stored_snapshot != resolved_skill_snapshot:
                    raise StorageConflictError("Atelier Agent loop turn id already belongs to different content")
                try:
                    fingerprint = fingerprint_agent_loop_start(command, resolved_skill_snapshot=stored_snapshot)
                except ValueError as exc:
                    raise StorageConflictError("Atelier Agent loop turn id already belongs to different content") from exc
                if existing.start_request_fingerprint != fingerprint:
                    raise StorageConflictError("Atelier Agent loop turn id already belongs to different content")
                return self._copy(current), True
            if command.skill_name is not None and resolved_skill_snapshot is None:
                raise StorageConflictError("atelier_product_skill_snapshot_required")
            if command.skill_name is None and resolved_skill_snapshot is not None:
                raise StorageConflictError("atelier_product_skill_unexpected_snapshot")
            if resolved_skill_snapshot is not None:
                if command.planner != "model_adapter":
                    raise StorageConflictError("atelier_agent_loop_skill_requires_model_adapter")
                if set(resolved_skill_snapshot.expected_tools) - CALLABLE_LOOP_TOOL_NAMES:
                    raise StorageConflictError("atelier_product_skill_tools_unavailable")
                if current.agent_policy.allowed_tools:
                    available = set(resolved_skill_snapshot.expected_tools).intersection(current.agent_policy.allowed_tools)
                else:
                    available = set(resolved_skill_snapshot.expected_tools)
                if resolved_skill_snapshot.expected_tools and not available:
                    raise StorageConflictError("atelier_product_skill_tools_unavailable")
            fingerprint = fingerprint_agent_loop_start(command, resolved_skill_snapshot=resolved_skill_snapshot)
            context = loop_start_context(command, resolved_skill_snapshot=resolved_skill_snapshot)
            if current.revision != command.expected_revision:
                raise StorageConflictError("Atelier project changed; reload before Agent loop start")
            turn = AtelierAgentTurn(
                id=command.client_turn_id,
                project_id=project_id,
                user_message=context.user_message,
                status="pending",
                start_request_fingerprint=fingerprint,
                loop_start_context=context,
                max_iterations=context.max_iterations,
                skill_name=context.skill_name,
                created_at=now,
            )
            candidate = self._copy(current)
            candidate.agent_turns.append(turn)
            candidate.updated_at = now
            before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
            _assert_loop_contexts_immutable(before, after)
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            return self._copy(self._commit(next_projects)[project_id]), False

    def save_agent_loop_iteration(
        self, project_id: str, command, *, now: float,
    ) -> tuple[AtelierProject, bool]:
        from .agent_loop_commands import iteration_fingerprint
        from .models import AtelierAgentIterationRecord

        payload = iteration_fingerprint(command)
        with self._lock:
            self._check_open()
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            existing_turn = next((item for item in current.agent_turns if item.id == command.turn_id), None)
            if existing_turn is None or existing_turn.loop_start_context is None:
                raise StorageConflictError("atelier_agent_loop_turn_missing")
            if command.iteration_index > existing_turn.max_iterations:
                raise StorageConflictError("atelier_agent_loop_iteration_limit")
            existing = next((item for item in existing_turn.iterations if item.idx == command.iteration_index), None)
            if existing is not None:
                existing_payload = next((
                    message.get("iteration_fingerprint")
                    for message in existing_turn.messages_history
                    if isinstance(message, dict)
                    and isinstance(message.get("iteration_fingerprint"), dict)
                    and message["iteration_fingerprint"].get("iteration_index") == command.iteration_index
                ), None)
                if existing_payload == payload:
                    return self._copy(current), True
                raise StorageConflictError("atelier_agent_loop_iteration_already_belongs_to_different_content")
            if current.revision != command.expected_revision:
                raise StorageConflictError("Atelier project changed; reload before Agent loop iteration")
            candidate = self._copy(current)
            turn = next((item for item in candidate.agent_turns if item.id == command.turn_id), None)
            if turn is None or turn.loop_start_context is None:
                raise StorageConflictError("atelier_agent_loop_turn_missing")
            if command.planner != turn.loop_start_context.planner:
                raise StorageConflictError("atelier_agent_loop_planner_mismatch")
            if turn.status != "pending" or turn.pending_tool_calls:
                raise StorageConflictError("atelier_agent_loop_pending_work_must_be_consumed_first")
            _assert_no_blocking_loop_generation(candidate, turn)
            if command.iteration_index != turn.iteration_count + 1:
                raise StorageConflictError("atelier_agent_loop_iteration_not_consecutive")
            tool_calls = [call.model_copy(deep=True) for call in command.tool_calls]
            _validate_agent_loop_iteration_calls(candidate, turn, tuple(tool_calls))
            assistant_message = {"role": "assistant", "content": command.assistant_message, "iteration_fingerprint": payload}
            turn.messages_history.append(assistant_message)
            turn.tool_calls.extend(tool_calls)
            pending: list[dict[str, Any]] = []
            for call in tool_calls:
                if call.status == "completed":
                    continue
                item = {"call_id": call.call_id, "tool_name": call.tool_name, "requires_approval": bool(call.approval_required)}
                if call.canvas_approval_binding is not None:
                    ref = call.canvas_approval_binding.canvas_authorization_ref
                    item.update({"approval_id": ref.approval_id, "authorization_hash": ref.authorization_hash})
                if call.preview_binding is not None:
                    ref = call.preview_binding.agent_authorization_ref
                    item.update({"approval_id": ref.approval_id, "authorization_hash": ref.authorization_hash})
                pending.append(item)
            turn.pending_tool_calls = pending
            turn.iterations.append(AtelierAgentIterationRecord(
                idx=command.iteration_index,
                provider="local",
                model_id=command.planner,
                tool_call_ids=[call.call_id for call in tool_calls],
                started_at=now,
                completed_at=now,
            ))
            turn.iteration_count = command.iteration_index
            if pending:
                turn.status = "waiting_approval" if any(item["requires_approval"] for item in pending) else "pending"
            else:
                turn.status = "completed"
                turn.completed_at = now
                turn.response = command.stop_reason or command.assistant_message or "completed"
            candidate.updated_at = now
            before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
            _assert_loop_contexts_immutable(before, after)
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            return self._copy(self._commit(next_projects)[project_id]), False

    def claim_agent_planner_invocation(
        self,
        project_id: str,
        command,
        *,
        now: float,
    ):
        from .agent_planner_invocations import (
            PlannerInvocationClaimAck,
            claim_config_fingerprint,
            claim_fingerprint,
            claim_input_fingerprint,
        )

        fingerprint = claim_fingerprint(command)
        input_fingerprint = claim_input_fingerprint(command)
        config_fingerprint = claim_config_fingerprint(command)
        with self._lock:
            self._check_open()
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            existing_turn = next((item for item in current.agent_turns if item.id == command.turn_id), None)
            if existing_turn is None or existing_turn.loop_start_context is None:
                raise StorageConflictError("atelier_agent_planner_turn_missing")
            existing = next(
                (item for item in existing_turn.planner_invocations if item.iteration_index == command.iteration_index),
                None,
            )
            if existing is not None:
                if existing.claim_fingerprint != fingerprint:
                    raise StorageConflictError("atelier_agent_planner_invocation_already_belongs_to_different_content")
                return self._copy(current), PlannerInvocationClaimAck(invocation=existing.model_copy(deep=True), claimed=False)
            if current.revision != command.expected_revision:
                raise StorageConflictError("Atelier project changed; reload before planner invocation claim")
            if existing_turn.loop_start_context.planner != command.planner or command.planner != "model_adapter":
                raise StorageConflictError("atelier_agent_planner_kind_mismatch")
            if existing_turn.status in {"completed", "failed", "canceled", "cancelled"}:
                raise StorageConflictError("atelier_agent_planner_turn_terminal")
            if existing_turn.status != "pending" or existing_turn.pending_tool_calls:
                raise StorageConflictError("atelier_agent_planner_pending_work_must_be_consumed_first")
            _assert_no_blocking_loop_generation(current, existing_turn)
            if command.iteration_index != existing_turn.iteration_count + 1:
                raise StorageConflictError("atelier_agent_planner_iteration_not_consecutive")
            if command.iteration_index > existing_turn.max_iterations:
                raise StorageConflictError("atelier_agent_planner_iteration_limit")
            if any(item.status == "claimed" for item in existing_turn.planner_invocations):
                raise StorageConflictError("atelier_agent_planner_prior_invocation_unsettled")
            budget = current.agent_policy.max_tokens_per_turn
            if type(budget) is not int or budget < 0:
                raise StorageError("Atelier planner budget is invalid")
            remaining = budget - existing_turn.completion_tokens_used - existing_turn.completion_tokens_reserved
            if remaining < command.max_completion_tokens:
                raise StorageConflictError("atelier_agent_planner_budget_exceeded")
            candidate = self._copy(current)
            turn = next((item for item in candidate.agent_turns if item.id == command.turn_id), None)
            if turn is None or turn.loop_start_context is None:
                raise StorageConflictError("atelier_agent_planner_turn_missing")
            invocation = AtelierAgentPlannerInvocation(
                invocation_id=f"{turn.id}:iteration:{command.iteration_index}",
                project_id=project_id,
                turn_id=turn.id,
                iteration_index=command.iteration_index,
                planner=command.planner,
                provider_id=command.provider_id,
                model_id=command.model_id,
                config_fingerprint=config_fingerprint,
                config_identity=command.config_identity.model_copy(deep=True),
                input_fingerprint=input_fingerprint,
                input_messages=[message.model_copy(deep=True) for message in command.input_messages],
                max_completion_tokens=command.max_completion_tokens,
                timeout_seconds=command.timeout_seconds,
                budget_completion_tokens=budget,
                reserved_completion_tokens=command.max_completion_tokens,
                status="claimed",
                claim_request_id=command.claim_request_id,
                claim_fingerprint=fingerprint,
                claimed_at=now,
            )
            turn.planner_invocations.append(invocation)
            turn.completion_tokens_reserved += invocation.reserved_completion_tokens
            candidate.updated_at = now
            before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
            _assert_loop_contexts_immutable(before, after)
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)[project_id]
            committed_turn = next(item for item in committed.agent_turns if item.id == command.turn_id)
            committed_invocation = next(
                item for item in committed_turn.planner_invocations if item.iteration_index == command.iteration_index
            )
            return self._copy(committed), PlannerInvocationClaimAck(invocation=committed_invocation.model_copy(deep=True), claimed=True)

    def settle_agent_planner_invocation(
        self,
        project_id: str,
        command,
        *,
        now: float,
    ):
        from .agent_planner_invocations import PlannerInvocationSettleAck, settlement_fingerprint

        fingerprint = settlement_fingerprint(command)
        with self._lock:
            self._check_open()
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            _current_turn, current_invocation = _find_planner_invocation(current, command.invocation_id)
            if current_invocation.status == "settled":
                if current_invocation.settlement_fingerprint != fingerprint:
                    raise StorageConflictError("atelier_agent_planner_invocation_settlement_conflict")
                return self._copy(current), PlannerInvocationSettleAck(
                    invocation=current_invocation.model_copy(deep=True),
                    settled=False,
                )
            candidate = self._copy(current)
            turn, invocation = _find_planner_invocation(candidate, command.invocation_id)
            if turn.completion_tokens_reserved < invocation.reserved_completion_tokens:
                raise StorageError("Atelier planner reservation ledger is invalid")
            completion_tokens = command.usage.completion_tokens if command.usage is not None else None
            charged = completion_tokens if completion_tokens is not None else invocation.reserved_completion_tokens
            invocation.status = "settled"
            invocation.settled_at = now
            invocation.settlement_fingerprint = fingerprint
            invocation.outcome = command.outcome
            invocation.result_content = command.result_content
            invocation.provider_model = command.provider_model
            invocation.usage = command.usage
            invocation.charged_completion_tokens = charged
            invocation.error_code = command.error_code
            invocation.error_message = command.error_message
            turn.completion_tokens_reserved -= invocation.reserved_completion_tokens
            turn.completion_tokens_used += charged
            candidate.updated_at = now
            before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
            _assert_loop_contexts_immutable(before, after)
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)[project_id]
            _, committed_invocation = _find_planner_invocation(committed, command.invocation_id)
            return self._copy(committed), PlannerInvocationSettleAck(
                invocation=committed_invocation.model_copy(deep=True),
                settled=True,
            )

    def apply_agent_loop_read_call(
        self, project_id: str, turn_id: str, call_id: str, *, now: float,
    ) -> tuple[AtelierProject, bool]:
        with self._lock:
            self._check_open()
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            call = _find_agent_canvas_call(current, turn_id, call_id)
            if call is not None and call.status == "completed" and call.result_snapshot is not None:
                return self._copy(current), True
            candidate = self._copy(current)
            turn = next((item for item in candidate.agent_turns if item.id == turn_id), None)
            if turn is None or turn.loop_start_context is None or turn.status not in {"pending", "waiting_approval"}:
                raise StorageConflictError("atelier_agent_loop_turn_not_active")
            call = next((item for item in turn.tool_calls if item.call_id == call_id), None)
            if call is None or call.tool_name != "canvas.readProject" or call.approval_required:
                raise StorageConflictError("atelier_agent_loop_read_call_missing")
            result = {
                "tool_name": "canvas.readProject",
                "project_id": candidate.id,
                "revision": candidate.revision,
                "node_count": len(candidate.nodes),
                "node_ids": [node.id for node in candidate.nodes],
            }
            from .models import AtelierAgentToolStatus

            call.status = AtelierAgentToolStatus.COMPLETED
            call.completed_at = now
            call.result_snapshot = result
            turn.pending_tool_calls = [item for item in turn.pending_tool_calls if not (isinstance(item, dict) and item.get("call_id") == call_id)]
            if not any(isinstance(item, dict) and item.get("tool_call_id") == call_id for item in turn.messages_history):
                turn.messages_history.append({"role": "tool", "tool_call_id": call_id, "name": call.tool_name, "content": result})
            if not turn.pending_tool_calls:
                turn.status = "pending"
                turn.completed_at = None
            candidate.updated_at = now
            before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
            _assert_loop_contexts_immutable(before, after)
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            return self._copy(self._commit(next_projects)[project_id]), False

    def apply_agent_loop_canvas_command(
        self,
        project_id: str,
        command: ApplyApprovedAgentCanvasCommand,
        *,
        now: float,
        server_storage_identity: str,
        node_id_factory: Callable[[], str] | None = None,
    ) -> tuple[AtelierProject, bool]:
        from .agent_canvas_commands import apply_approved_canvas_call

        make_node_id = node_id_factory or (lambda: uuid.uuid4().hex)
        with self._lock:
            self._check_open()
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            existing = _find_agent_canvas_call(current, command.turn_id, command.call_id)
            if existing is not None and existing.status == "completed" and existing.result_snapshot is not None:
                if not _agent_loop_canvas_replay_matches(existing, command):
                    raise StorageConflictError("atelier_agent_loop_canvas_replay_mismatch")
                return self._copy(current), True
            if current.revision != command.expected_revision:
                raise StorageConflictError("Atelier project changed; reload before Agent loop canvas command")
            before = current.model_dump(mode="json")
            candidate = self._copy(current)
            turn = next((item for item in candidate.agent_turns if item.id == command.turn_id), None)
            if turn is None or turn.loop_start_context is None or turn.status not in {"waiting_approval", "pending"}:
                raise StorageConflictError("atelier_agent_loop_turn_not_active")
            self._in_command = True
            try:
                apply_approved_canvas_call(
                    candidate,
                    command,
                    now=now,
                    server_storage_identity=server_storage_identity,
                    node_id_factory=make_node_id,
                    finalizer="loop",
                )
            finally:
                self._in_command = False
            self._check_open()
            if candidate.id != project_id or candidate.revision != command.expected_revision:
                raise StorageError("Atelier Agent loop canvas command cannot change project identity or revision")
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            after = candidate.model_dump(mode="json")
            _assert_loop_contexts_immutable(before, after)
            _restore_agent_canvas_command_for_comparison(before, after, command, now=now, final_turn_status="pending", final_completed_at=None)
            after["revision"] = before["revision"]
            after["updated_at"] = before["updated_at"]
            if after != before:
                raise StorageError("Atelier Agent loop canvas command cannot modify unrelated project fields")
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            return self._copy(self._commit(next_projects)[project_id]), False



    def apply_agent_loop_generation_admission(
        self,
        project_id: str,
        command: AdmitApprovedAgentGenerationCommand,
        *,
        now: float,
    ) -> tuple[AtelierProject, bool]:
        """Admit one approved loop generation call and consume its pending entry."""
        from .agent_admission import append_approved_generation
        from .agent_authorization import fingerprint_agent_generation_request

        if not isinstance(command, AdmitApprovedAgentGenerationCommand):
            raise TypeError("Agent loop admission requires a typed command")
        fingerprint = fingerprint_agent_generation_request(
            turn_id=command.turn_id, call_id=command.call_id,
            approval_id=command.approval_id, authorization_hash=command.authorization_hash,
        )

        def apply(project):
            turn = next((item for item in project.agent_turns if item.id == command.turn_id), None)
            if turn is None or turn.loop_start_context is None:
                raise StorageConflictError("atelier_agent_loop_turn_missing")
            append_approved_generation(
                project, command, media_scope=self._media_write_scope(), now=now,
                attempt_id=uuid.uuid4().hex, candidate_id=uuid.uuid4().hex, finalizer="loop",
            )

        return self._apply_generation_admission(
            project_id, expected_revision=command.expected_revision,
            client_request_id=command.client_request_id, request_fingerprint=fingerprint,
            command=apply, agent_command=command, agent_finalizer="loop", expected_count=1,
        )


    def record_agent_loop_generation_schedule_failure(
        self,
        project_id: str,
        *,
        turn_id: str,
        call_id: str,
        client_request_id: str,
        attempt_id: str,
        error_code: str,
        now: float,
    ) -> tuple[AtelierProject, bool]:
        allowed_errors = {
            "generation_schedule_unavailable",
            "generation_schedule_failed",
            "generation_schedule_not_accepted",
        }
        if error_code not in allowed_errors:
            raise StorageConflictError("atelier_agent_loop_generation_schedule_error_invalid")
        with self._lock:
            self._check_open()
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            turn_current = next((item for item in current.agent_turns if item.id == turn_id), None)
            call_current = next((item for item in turn_current.tool_calls if item.call_id == call_id), None) if turn_current else None
            result_current = call_current.result_snapshot if call_current is not None else None
            attempt_current = next((item for item in current.generation_attempts if item.attempt_id == attempt_id), None)
            if (
                turn_current is None
                or turn_current.loop_start_context is None
                or call_current is None
                or call_current.tool_name != "generation.createVideoCandidates"
                or not isinstance(result_current, dict)
                or result_current.get("client_request_id") != client_request_id
                or result_current.get("attempt_id") != attempt_id
                or attempt_current is None
                or attempt_current.agent_authorization_ref is None
                or attempt_current.agent_authorization_ref.turn_id != turn_id
                or attempt_current.agent_authorization_ref.call_id != call_id
            ):
                raise StorageConflictError("atelier_agent_loop_generation_schedule_identity_mismatch")
            if result_current.get("schedule_error") == error_code:
                return self._copy(current), True
            candidate = self._copy(current)
            turn = next(item for item in candidate.agent_turns if item.id == turn_id)
            call = next(item for item in turn.tool_calls if item.call_id == call_id)
            result = dict(call.result_snapshot or {})
            result.update({"scheduled": False, "schedule_error": error_code})
            call.result_snapshot = result
            for message in turn.messages_history:
                if isinstance(message, dict) and message.get("role") == "tool" and message.get("tool_call_id") == call_id:
                    message["content"] = dict(result)
            candidate.updated_at = now
            before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
            _assert_loop_contexts_immutable(before, after)
            for field in ("agent_turns", "updated_at"):
                after[field] = before[field]
            if after != before:
                raise StorageError("Atelier Agent loop schedule diagnostic cannot modify unrelated project fields")
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            return self._copy(self._commit(next_projects)[project_id]), False

    def finish_agent_loop_turn(
        self, project_id: str, *, expected_revision: int, turn_id: str, status: str, response: str | None, now: float,
    ) -> AtelierProject:
        if status not in {"completed", "failed"}:
            raise StorageConflictError("atelier_agent_loop_finish_status_invalid")
        with self._lock:
            self._check_open()
            current = self._projects.get(project_id)
            if current is None or current.revision != expected_revision:
                raise StorageConflictError("Atelier project changed; reload before Agent loop finish")
            candidate = self._copy(current)
            turn = next((item for item in candidate.agent_turns if item.id == turn_id), None)
            if turn is None or turn.loop_start_context is None:
                raise StorageConflictError("atelier_agent_loop_turn_missing")
            if turn.status in {"completed", "failed", "canceled", "cancelled"}:
                raise StorageConflictError("atelier_agent_loop_turn_terminal")
            if turn.pending_tool_calls:
                raise StorageConflictError("atelier_agent_loop_pending_work_must_be_consumed_first")
            turn.status = status
            turn.completed_at = now
            turn.response = response or status
            candidate.updated_at = now
            before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
            _assert_loop_contexts_immutable(before, after)
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            return self._copy(self._commit(next_projects)[project_id])

    def create_agent_turn(
        self, project_id: str, turn: AtelierAgentTurn, *, expected_revision: int,
    ) -> AtelierProject:
        from .agent_admission import append_pending_turn

        if not isinstance(turn, AtelierAgentTurn):
            raise TypeError("Agent turn creation requires a typed turn")
        if turn.loop_start_context is not None:
            raise StorageConflictError("atelier_agent_turn_authority_must_use_dedicated_command")
        return self._apply_agent_turn_command(
            project_id, expected_revision=expected_revision,
            command=lambda project: append_pending_turn(project, turn.model_copy(deep=True)),
        )

    def record_agent_approval(
        self, project_id: str, command: RecordAgentApprovalCommand, *, decided_at: float,
    ) -> AtelierProject:
        from .agent_admission import record_approval

        if not isinstance(command, RecordAgentApprovalCommand):
            raise TypeError("Agent approval requires a typed command")
        return self._apply_agent_turn_command(
            project_id, expected_revision=command.expected_revision if command.decision == "approved" else None,
            command=lambda project: record_approval(project, command, decided_at=decided_at),
        )

    def cancel_agent_turn(
        self, project_id: str, turn_id: str, *, cancelled_at: float,
    ) -> AtelierProject:
        from .agent_admission import cancel_turn

        return self._apply_agent_turn_command(
            project_id, expected_revision=None,
            command=lambda project: cancel_turn(project, turn_id, cancelled_at=cancelled_at),
        )

    def _apply_agent_turn_command(
        self, project_id: str, *, expected_revision: int | None,
        command: Callable[[AtelierProject], None],
    ) -> AtelierProject:
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise StorageConflictError("Invalid Atelier project revision")
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None or (expected_revision is not None and current.revision != expected_revision):
                raise StorageConflictError("Atelier project changed; reload before Agent command")
            candidate = self._copy(current)
            self._in_command = True
            try:
                command(candidate)
            finally:
                self._in_command = False
            self._check_open()
            before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
            _assert_loop_contexts_immutable(before, after)
            for field in ("agent_turns", "updated_at"):
                after[field] = before[field]
            if before != after:
                raise StorageError("Agent command cannot modify unrelated project fields")
            if candidate == current:
                return self._copy(current)
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            return self._copy(self._commit(next_projects)[project_id])

    def record_agent_canvas_approval(
        self, project_id: str, command: RecordAgentCanvasApprovalCommand, *, decided_at: float,
    ) -> AtelierProject:
        from .agent_canvas_commands import record_canvas_approval

        if not isinstance(command, RecordAgentCanvasApprovalCommand):
            raise TypeError("Agent canvas approval requires a typed command")
        return self._apply_agent_turn_command(
            project_id, expected_revision=command.expected_revision if command.decision == "approved" else None,
            command=lambda project: record_canvas_approval(project, command, decided_at=decided_at),
        )

    def apply_agent_canvas_command(
        self,
        project_id: str,
        command: ApplyApprovedAgentCanvasCommand,
        *,
        now: float,
        server_storage_identity: str,
        node_id_factory: Callable[[], str] | None = None,
    ) -> tuple[AtelierProject, bool]:
        """Commit one approved local Agent canvas mutation and its tool result."""
        from .agent_canvas_commands import apply_approved_canvas_call

        if not isinstance(command, ApplyApprovedAgentCanvasCommand):
            raise TypeError("Agent canvas command requires a typed command")
        if type(command.expected_revision) is not int or command.expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        make_node_id = node_id_factory or (lambda: uuid.uuid4().hex)
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            turn = next((item for item in current.agent_turns if item.id == command.turn_id), None)
            if turn is not None and turn.loop_start_context is not None:
                raise StorageConflictError("atelier_agent_canvas_loop_finalizer_required")
            existing = _find_agent_canvas_call(current, command.turn_id, command.call_id)
            if existing is not None and existing.result_snapshot is not None and existing.status == "completed":
                ref = existing.canvas_approval_binding.canvas_authorization_ref if existing.canvas_approval_binding else None
                approval = existing.canvas_approval_record
                if ref is None or approval is None or ref.approval_id != command.approval_id or ref.authorization_hash != command.authorization_hash:
                    raise StorageConflictError("atelier_agent_canvas_authorization_mismatch")
                return self._copy(current), True
            if current.revision != command.expected_revision:
                raise StorageConflictError("Atelier project changed; reload before Agent canvas command")
            before = current.model_dump(mode="json")
            candidate = self._copy(current)
            self._in_command = True
            try:
                apply_approved_canvas_call(
                    candidate,
                    command,
                    now=now,
                    server_storage_identity=server_storage_identity,
                    node_id_factory=make_node_id,
                )
            finally:
                self._in_command = False
            self._check_open()
            if candidate.id != project_id or candidate.revision != command.expected_revision:
                raise StorageError("Atelier Agent canvas command cannot change project identity or revision")
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            after = candidate.model_dump(mode="json")
            _restore_agent_canvas_command_for_comparison(before, after, command, now=now)
            after["revision"] = before["revision"]
            after["updated_at"] = before["updated_at"]
            if after != before:
                raise StorageError("Atelier Agent canvas command cannot modify unrelated project fields")
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            return self._copy(self._commit(next_projects)[project_id]), False

    def apply_agent_generation_admission(
        self,
        project_id: str,
        command: AdmitApprovedAgentGenerationCommand,
        *,
        now: float,
    ) -> tuple[AtelierProject, bool]:
        """Admit a saved approved call and its generation ledger in one commit."""
        from .agent_admission import append_approved_generation
        from .agent_authorization import fingerprint_agent_generation_request

        if not isinstance(command, AdmitApprovedAgentGenerationCommand):
            raise TypeError("Agent admission requires a typed command")
        fingerprint = fingerprint_agent_generation_request(
            turn_id=command.turn_id, call_id=command.call_id,
            approval_id=command.approval_id, authorization_hash=command.authorization_hash,
        )

        def apply(project):
            append_approved_generation(
                project, command, media_scope=self._media_write_scope(),
                now=now, attempt_id=uuid.uuid4().hex, candidate_id=uuid.uuid4().hex,
            )

        return self._apply_generation_admission(
            project_id, expected_revision=command.expected_revision,
            client_request_id=command.client_request_id, request_fingerprint=fingerprint,
            command=apply, agent_command=command,
        )

    def _apply_generation_admission(
        self,
        project_id: str,
        *,
        expected_revision: int,
        client_request_id: str,
        request_fingerprint: str,
        command: Callable[[AtelierProject], None],
        agent_command: AdmitApprovedAgentGenerationCommand | None = None,
        agent_finalizer: str = "standalone",
        expected_count: int = 1,
    ) -> tuple[AtelierProject, bool]:
        from ...utils.request_identity import validate_client_request_id

        validate_client_request_id(client_request_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        if not isinstance(request_fingerprint, str) or re.fullmatch(r"[a-f0-9]{64}", request_fingerprint) is None:
            raise StorageError("Invalid Atelier generation request fingerprint")
        if type(expected_count) is not int or expected_count < 1 or expected_count > 6:
            raise StorageConflictError("Invalid Atelier generation admission count")
        if agent_command is not None and expected_count != 1:
            raise StorageConflictError("Agent generation admission must stay singleton")
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            existing = next((r for r in current.generation_request_receipts if r.client_request_id == client_request_id), None)
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise StorageConflictError("Atelier generation request id already belongs to different content")
                return self._copy(current), True
            if current.revision != expected_revision:
                raise StorageConflictError("Atelier project changed; reload before generating")
            candidate = self._copy(current)
            self._in_command = True
            try:
                command(candidate)
            finally:
                self._in_command = False
            self._check_open()
            if candidate.id != project_id or candidate.revision != expected_revision:
                raise StorageError("Atelier generation admission cannot change project identity or revision")
            candidate.revision += 1
            candidate = _canonical_project(candidate, expected_id=project_id)
            before, after = current.model_dump(mode="json"), candidate.model_dump(mode="json")
            if len(after["generation_attempts"]) != len(before["generation_attempts"]) + expected_count or after["generation_attempts"][:-expected_count] != before["generation_attempts"]:
                raise StorageError("Atelier generation admission must append expected attempts")
            if len(after["generation_request_receipts"]) != len(before["generation_request_receipts"]) + 1 or after["generation_request_receipts"][:-1] != before["generation_request_receipts"]:
                raise StorageError("Atelier generation admission must append exactly one receipt")
            attempts = candidate.generation_attempts[-expected_count:]
            receipt = candidate.generation_request_receipts[-1]
            members = receipt.receipt_members
            if len(members) != expected_count:
                raise StorageError("Atelier generation admission receipt member count is invalid")
            if (
                receipt.client_request_id != client_request_id
                or receipt.request_fingerprint != request_fingerprint
                or receipt.base_revision != expected_revision
                or receipt.committed_revision != expected_revision + 1
                or receipt.node_id != attempts[0].node_id
            ):
                raise StorageError("Atelier generation admission fields are invalid")
            owner_node_id = attempts[0].node_id
            for attempt, member in zip(attempts, members, strict=True):
                if (
                    attempt.node_id != owner_node_id
                    or member.attempt_id != attempt.attempt_id
                    or member.candidate_id != attempt.candidate_id
                    or member.execution_fingerprint != attempt.provider_execution.fingerprint
                    or attempt.status != "pending"
                    or attempt.attempt_revision != 0
                    or attempt.provider_execution.phase != "accepted"
                    or attempt.schema_version != (2 if agent_command is not None else 1)
                ):
                    raise StorageError("Atelier generation admission fields are invalid")
            old_node = next((n for n in before["nodes"] if n["id"] == owner_node_id), None)
            new_node = next((n for n in after["nodes"] if n["id"] == owner_node_id), None)
            if old_node is None or new_node is None:
                raise StorageError("Atelier generation admission requires an existing owner")
            old_candidates = old_node["data"].get("candidates", [])
            new_candidates = new_node["data"].get("candidates", [])
            admitted_candidates = new_candidates[-expected_count:] if isinstance(new_candidates, list) else []
            if (
                not isinstance(old_candidates, list) or not isinstance(new_candidates, list)
                or len(new_candidates) != len(old_candidates) + expected_count
                or new_candidates[:-expected_count] != old_candidates
                or any(not isinstance(item, dict) for item in admitted_candidates)
                or [item.get("id") for item in admitted_candidates] != [attempt.candidate_id for attempt in attempts]
            ):
                raise StorageError("Atelier generation admission must append expected candidates to its owner")
            # Remove precisely the admitted fields to prove that the callback
            # did not change selection, layout, previous work or another node.
            del after["generation_attempts"][-expected_count:]
            after["generation_request_receipts"].pop()
            if agent_command is not None:
                from .agent_admission import restore_admitted_call_for_comparison

                restore_admitted_call_for_comparison(before, after, agent_command, attempts[0], now=attempts[0].created_at, finalizer=agent_finalizer)
            if "candidates" in old_node["data"]:
                new_node["data"]["candidates"] = old_candidates
            else:
                del new_node["data"]["candidates"]
            new_node["updated_at"] = old_node["updated_at"]
            after["revision"] = before["revision"]
            after["updated_at"] = before["updated_at"]
            if after != before:
                raise StorageError("Atelier generation admission cannot modify unrelated project fields")
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)
            return self._copy(committed[project_id]), False

    def update_generation_attempt(
        self,
        project_id: str,
        *,
        attempt_id: str,
        expected_attempt_revision: int,
        execution_fingerprint: str,
        command: Callable[[AtelierGenerationAttempt | AtelierMediaGenerationAttempt], None],
        project_guard: Callable[[AtelierProject, AtelierGenerationAttempt | AtelierMediaGenerationAttempt], None] | None = None,
    ) -> tuple[AtelierProject, AtelierGenerationAttempt | AtelierMediaGenerationAttempt]:
        """Persist one execution CAS without changing the canvas edit revision.

        An optional authorization guard reads the current project under this
        same lock, before the transition. Its detached inputs confer no write
        authority. Callers must keep guard work local and bounded.
        """
        if type(expected_attempt_revision) is not int or expected_attempt_revision < 0:
            raise StorageConflictError("Invalid Atelier generation attempt revision")
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            index = next((i for i, a in enumerate(current.generation_attempts) if a.attempt_id == attempt_id), None)
            if index is None:
                raise StorageConflictError("Atelier generation attempt no longer exists")
            original = current.generation_attempts[index]
            if original.attempt_revision != expected_attempt_revision or original.provider_execution.fingerprint != execution_fingerprint:
                raise StorageConflictError("Atelier generation attempt changed; reload before continuing")
            updated = original.model_copy(deep=True)
            self._in_command = True
            try:
                if project_guard is not None:
                    project_guard(self._copy(current), original.model_copy(deep=True))
                command(updated)
            finally:
                self._in_command = False
            self._check_open()
            try:
                attempt_model = AtelierMediaGenerationAttempt if isinstance(original, AtelierMediaGenerationAttempt) else AtelierGenerationAttempt
                updated = attempt_model.model_validate(updated.model_dump(mode="json"))
            except ValidationError as exc:
                raise StorageError("Invalid Atelier generation update") from exc
            validate_generation_transition(original, updated, StorageError)
            if updated == original:
                return self._copy(current), original.model_copy(deep=True)
            updated.attempt_revision += 1
            candidate = self._copy(current)
            candidate.generation_attempts[index] = updated
            owner = next((node for node in candidate.nodes if node.id == updated.node_id), None)
            if owner is None:
                raise StorageConflictError("Atelier generation owner no longer exists")
            projection = next(
                (item for field in ("candidates", "deleted_candidates")
                 for item in (owner.data[field] if isinstance(owner.data.get(field), list) else [])
                 if isinstance(item, dict) and item.get("id") == updated.candidate_id),
                None,
            )
            if projection is None:
                raise StorageConflictError("Atelier generation candidate no longer exists")
            if isinstance(updated, AtelierMediaGenerationAttempt):
                projection.update(status=updated.status, error_code=updated.error_code, media_url=updated.output_ref)
            else:
                projection.update(status=updated.status, error_code=updated.error_code,
                                  video_url=updated.output_ref, updated_at=updated.updated_at)
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)
            result = self._copy(committed[project_id])
            return result, result.generation_attempts[index].model_copy(deep=True)

    def apply_sequence_export_job_creation(
        self,
        project_id: str,
        *,
        expected_revision: int,
        client_request_id: str,
        request_fingerprint: str,
        command: Callable[[AtelierProject], Any],
    ) -> tuple[AtelierProject, AtelierSequenceExportJob, bool]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            existing = _find_export_job_by_request(current, client_request_id)
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise StorageConflictError("Atelier export request id already belongs to different content")
                return self._copy(current), existing.model_copy(deep=True), True
            if current.revision != expected_revision:
                raise StorageConflictError("Atelier project changed; reload before exporting")
            before_job_ids = {job.job_id for job in current.sequence_export_jobs}
            candidate = self._copy(current)
            self._in_command = True
            try:
                command(candidate)
            finally:
                self._in_command = False
            self._check_open()
            if candidate.id != project_id:
                raise StorageError("Atelier export command cannot change project id")
            if candidate.revision != current.revision:
                raise StorageError("Atelier export job creation cannot change project revision")
            assert_generation_unchanged_for_product_command(current, candidate, StorageError)
            candidate = _canonical_project(candidate, expected_id=project_id)
            if _project_without_export_jobs(candidate) != _project_without_export_jobs(current):
                raise StorageError("Atelier export job creation can only add the export job")
            current_jobs = [job.model_dump(mode="json") for job in current.sequence_export_jobs]
            candidate_jobs = [job.model_dump(mode="json") for job in candidate.sequence_export_jobs]
            if len(candidate_jobs) != len(current_jobs) + 1 or candidate_jobs[: len(current_jobs)] != current_jobs:
                raise StorageError("Atelier export job creation can only append the export job")
            created = [job for job in candidate.sequence_export_jobs if job.job_id not in before_job_ids]
            if len(created) != 1 or created[0] != candidate.sequence_export_jobs[-1]:
                raise StorageError("Atelier export job creation must add exactly one job")
            job = created[0]
            if job.project_id != project_id or job.client_request_id != client_request_id or job.request_fingerprint != request_fingerprint:
                raise StorageError("Atelier export job request fields are invalid")
            if job.base_revision != expected_revision or job.job_revision != 0 or job.status != "queued":
                raise StorageError("Atelier export job creation fields are invalid")
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)
            committed_project = self._copy(committed[project_id])
            return committed_project, _find_export_job(committed_project, job.job_id).model_copy(deep=True), False

    def apply_sequence_export_job_update(
        self,
        project_id: str,
        *,
        job_id: str,
        command: Callable[[AtelierProject, AtelierSequenceExportJob], None],
    ) -> tuple[AtelierProject, AtelierSequenceExportJob]:
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            current_job = _find_export_job(current, job_id)
            candidate = self._copy(current)
            candidate_job = _find_export_job(candidate, job_id)
            self._in_command = True
            try:
                command(candidate, candidate_job)
            finally:
                self._in_command = False
            self._check_open()
            if candidate.id != project_id or candidate.revision != current.revision:
                raise StorageError("Atelier export job update cannot change project identity or revision")
            assert_generation_unchanged_for_product_command(current, candidate, StorageError)
            candidate = _canonical_project(candidate, expected_id=project_id)
            if _project_without_export_jobs(candidate) != _project_without_export_jobs(current):
                raise StorageError("Atelier export job update can only change the matching export job")
            if [job.job_id for job in candidate.sequence_export_jobs] != [job.job_id for job in current.sequence_export_jobs]:
                raise StorageError("Atelier export job update can only change the matching export job")
            candidate_job = _find_export_job(candidate, job_id)
            for before_job, after_job in zip(current.sequence_export_jobs, candidate.sequence_export_jobs, strict=True):
                if before_job.job_id != after_job.job_id:
                    raise StorageError("Atelier export job update cannot reorder jobs")
                if before_job.job_id != job_id and before_job.model_dump(mode="json") != after_job.model_dump(mode="json"):
                    raise StorageError("Atelier export job update can only change the matching export job")
            if candidate_job.job_revision != current_job.job_revision:
                raise StorageError("Atelier export job update cannot set job_revision")
            _assert_export_job_immutable_fields(current_job, candidate_job)
            _assert_export_status_transition(current_job.status, candidate_job.status)
            before = current.model_dump(mode="json")
            after_without_revision = candidate.model_dump(mode="json")
            if before == after_without_revision:
                return self._copy(current), current_job.model_copy(deep=True)
            replacement = candidate_job.model_copy(deep=True, update={"job_revision": current_job.job_revision + 1})
            _replace_export_job(candidate, replacement)
            candidate = _canonical_project(candidate, expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)
            committed_project = self._copy(committed[project_id])
            return committed_project, _find_export_job(committed_project, job_id).model_copy(deep=True)

    def apply_sequence_export_completion(
        self,
        project_id: str,
        *,
        job_id: str,
        command: Callable[[AtelierProject, AtelierSequenceExportJob], None],
    ) -> AtelierProject:
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            current_job = _find_export_job(current, job_id)
            candidate = self._copy(current)
            candidate_job = _find_export_job(candidate, job_id)
            self._in_command = True
            try:
                command(candidate, candidate_job)
            finally:
                self._in_command = False
            self._check_open()
            if candidate.id != project_id or candidate.revision != current.revision:
                raise StorageError("Atelier export completion cannot change project identity or revision")
            assert_generation_unchanged_for_product_command(current, candidate, StorageError)
            candidate = _canonical_project(candidate, expected_id=project_id)
            if _project_without_jobs_and_exports(candidate) != _project_without_jobs_and_exports(current):
                raise StorageError("Atelier export completion can only change the job and exports")
            if [job.job_id for job in candidate.sequence_export_jobs] != [job.job_id for job in current.sequence_export_jobs]:
                raise StorageError("Atelier export completion can only change the matching export job")
            candidate_job = _find_export_job(candidate, job_id)
            for before_job, after_job in zip(current.sequence_export_jobs, candidate.sequence_export_jobs, strict=True):
                if before_job.job_id != after_job.job_id:
                    raise StorageError("Atelier export completion cannot reorder jobs")
                if before_job.job_id != job_id and before_job.model_dump(mode="json") != after_job.model_dump(mode="json"):
                    raise StorageError("Atelier export completion can only change the matching export job")
            if candidate_job.job_revision != current_job.job_revision:
                raise StorageError("Atelier export completion cannot set job_revision")
            _assert_export_job_immutable_fields(current_job, candidate_job)
            _assert_export_status_transition(current_job.status, candidate_job.status)
            if candidate_job.status != "completed":
                raise StorageError("Atelier export completion must mark the job completed")
            before = current.model_dump(mode="json")
            after = candidate.model_dump(mode="json")
            if before == after:
                return self._copy(current)
            current_exports = [export.model_dump(mode="json") for export in current.exports]
            candidate_exports = [export.model_dump(mode="json") for export in candidate.exports]
            matching_exports = [export for export in candidate.exports if export.job_id == job_id]
            if (
                len(candidate_exports) != len(current_exports) + 1
                or candidate_exports[: len(current_exports)] != current_exports
                or len(matching_exports) != 1
                or matching_exports[0] != candidate.exports[-1]
                or matching_exports[0].id != candidate_job.export_id
            ):
                raise StorageError("Atelier export completion must append exactly one matching export record")
            replacement = candidate_job.model_copy(deep=True, update={"job_revision": current_job.job_revision + 1})
            _replace_export_job(candidate, replacement)
            candidate = _canonical_project(candidate.model_copy(deep=True, update={"revision": current.revision + 1}), expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)
            return self._copy(committed[project_id])

    def apply_export_deletion_admission(
        self,
        project_id: str,
        *,
        export_id: str,
        expected_revision: int,
        client_request_id: str,
        delete_file: bool,
        command: Callable[[AtelierProject, Any], None],
    ) -> tuple[AtelierProject, Any, bool]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            existing_request = _find_export_deletion_by_request(current, client_request_id)
            if existing_request is not None:
                if existing_request.id != export_id or existing_request.deletion is None or existing_request.deletion.delete_file != delete_file:
                    raise StorageConflictError("Atelier export deletion request id already belongs to different content")
                return self._copy(current), existing_request.model_copy(deep=True), True
            current_export = _find_export_record(current, export_id)
            if current_export.deletion is not None:
                raise StorageConflictError("Atelier export is already deleted")
            if current.revision != expected_revision:
                raise StorageConflictError("Atelier project changed; reload before deleting export")
            candidate = self._copy(current)
            candidate_export = _find_export_record(candidate, export_id)
            self._in_command = True
            try:
                command(candidate, candidate_export)
            finally:
                self._in_command = False
            self._check_open()
            if candidate.id != project_id or candidate.revision != current.revision:
                raise StorageError("Atelier export deletion cannot change project identity or revision")
            assert_generation_unchanged_for_product_command(current, candidate, StorageError)
            candidate = _canonical_project(candidate, expected_id=project_id)
            current_payload = current.model_dump(mode="json")
            candidate_payload = candidate.model_dump(mode="json")
            current_payload.pop("updated_at", None)
            candidate_payload.pop("updated_at", None)
            current_exports = current_payload.pop("exports")
            candidate_exports = candidate_payload.pop("exports")
            if current_payload != candidate_payload:
                raise StorageError("Atelier export deletion can only change the matching export record")
            if [export["id"] for export in current_exports] != [export["id"] for export in candidate_exports]:
                raise StorageError("Atelier export deletion cannot add, remove, or reorder exports")
            changed = 0
            for before, after in zip(current_exports, candidate_exports, strict=True):
                if before["id"] != export_id:
                    if before != after:
                        raise StorageError("Atelier export deletion can only change the matching export record")
                    continue
                before_without_deletion = dict(before)
                after_without_deletion = dict(after)
                before_without_deletion.pop("deletion", None)
                after_deletion = after_without_deletion.pop("deletion", None)
                if before_without_deletion != after_without_deletion or before.get("deletion") is not None:
                    raise StorageError("Atelier export deletion can only add the deletion receipt")
                if after_deletion is None:
                    raise StorageError("Atelier export deletion receipt is required")
                if after_deletion.get("client_request_id") != client_request_id or after_deletion.get("delete_file") is not delete_file:
                    raise StorageError("Atelier export deletion request fields are invalid")
                changed += 1
            if changed != 1:
                raise StorageError("Atelier export deletion must change exactly one export")
            candidate = _canonical_project(candidate.model_copy(deep=True, update={"revision": current.revision + 1}), expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)
            committed_project = self._copy(committed[project_id])
            return committed_project, _find_export_record(committed_project, export_id).model_copy(deep=True), False

    def apply_export_deletion_file_fact(
        self,
        project_id: str,
        *,
        export_id: str,
        client_request_id: str,
        file_status: str,
        checked_at: float | None,
        error_code: str | None,
        error_message: str | None,
    ) -> tuple[AtelierProject, Any]:
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise StorageConflictError("Atelier project no longer exists")
            current_export = _find_export_record(current, export_id)
            if current_export.deletion is None or current_export.deletion.client_request_id != client_request_id:
                raise StorageConflictError("Atelier export deletion receipt not found")
            candidate = self._copy(current)
            candidate_export = _find_export_record(candidate, export_id)
            if candidate_export.deletion is None:
                raise StorageConflictError("Atelier export deletion receipt not found")
            candidate_export.deletion.file_status = file_status  # type: ignore[assignment]
            candidate_export.deletion.checked_at = checked_at
            candidate_export.deletion.error_code = error_code
            candidate_export.deletion.error_message = error_message
            assert_generation_unchanged_for_product_command(current, candidate, StorageError)
            candidate = _canonical_project(candidate, expected_id=project_id)
            current_payload = current.model_dump(mode="json")
            candidate_payload = candidate.model_dump(mode="json")
            current_payload.pop("updated_at", None)
            candidate_payload.pop("updated_at", None)
            current_exports = current_payload.pop("exports")
            candidate_exports = candidate_payload.pop("exports")
            if current_payload != candidate_payload:
                raise StorageError("Atelier export deletion fact can only change the matching export record")
            if [export["id"] for export in current_exports] != [export["id"] for export in candidate_exports]:
                raise StorageError("Atelier export deletion fact cannot add, remove, or reorder exports")
            for before, after in zip(current_exports, candidate_exports, strict=True):
                if before["id"] != export_id and before != after:
                    raise StorageError("Atelier export deletion fact can only change the matching export record")
            before = current.model_dump(mode="json")
            after = candidate.model_dump(mode="json")
            if before == after:
                return self._copy(current), current_export.model_copy(deep=True)
            candidate = _canonical_project(candidate.model_copy(deep=True, update={"revision": current.revision + 1}), expected_id=project_id)
            next_projects = dict(self._projects)
            next_projects[project_id] = candidate
            committed = self._commit(next_projects)
            committed_project = self._copy(committed[project_id])
            return committed_project, _find_export_record(committed_project, export_id).model_copy(deep=True)

    def delete_project(self, project_id: str, *, expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise StorageConflictError("Invalid Atelier project revision")
        with self._lock:
            self._check_open()
            if self._in_command:
                raise StorageConflictError("Nested Atelier storage commands are not allowed")
            current = self._projects.get(project_id)
            if current is None:
                raise AtelierProjectNotFound("Atelier project not found")
            if current.revision != expected_revision:
                raise StorageConflictError("Atelier project changed; reload before deleting")
            if any(job.status in _PENDING_EXPORT_JOB_STATUSES for job in current.sequence_export_jobs) or any(
                export.deletion is not None and export.deletion.file_status in _PENDING_EXPORT_DELETION_STATUSES
                for export in current.exports
            ):
                raise StorageConflictError("Cannot delete project with pending Atelier export operation")
            if has_pending_generation(current):
                raise StorageConflictError("Cannot delete project with pending Atelier generation")
            next_projects = dict(self._projects)
            del next_projects[project_id]
            self._commit(next_projects)

    def _commit(self, next_projects: dict[str, AtelierProject]) -> dict[str, AtelierProject]:
        self._writer_lock.assert_owned_path()
        payload = _database_payload(next_projects)
        committed = _validate_database(payload)
        write_payload = _database_payload(committed)
        try:
            atomic_write_json(self.path, write_payload)
        except StorageError as exc:
            self._record_storage_failure(exc)
            raise
        try:
            self._writer_lock.assert_owned_path()
        except StorageError as exc:
            uncertain = StorageError(
                f"Atelier storage commit visible but writer lease could not be verified: {exc}",
                commit_uncertain=True,
            )
            self._record_storage_failure(uncertain)
            raise uncertain from exc
        self._projects = {project_id: self._copy(project) for project_id, project in committed.items()}
        return self._projects
