"""Typed Atelier managed-upload commands and receipt helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .media_upload import ManagedUpload
from .models import (
    AtelierNode,
    AtelierNodeUploadReceipt,
    AtelierProject,
    AtelierUploadedMediaProof,
)
from .node_history import _assert_delete_safe
from .nodes import MAX_COORDINATE_ABS, MAX_SIZE, NodeCommandAck
from .references import can_attach_atelier_reference, canonical_atelier_reference_data
from ...utils.json_storage import StorageConflictError

REQUEST_CONTRACT_VERSION = "atelier-upload-request-v1"
CLIENT_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
UPLOAD_NODE_TYPES = {"image", "audio", "video"}
UPLOAD_TARGET_ACTIVE_CODE = "atelier_upload_target_active_execution"


class AtelierUploadTargetActiveError(Exception):
    """Raised when reference upload is definitely rejected before storage commit."""

    code = UPLOAD_TARGET_ACTIVE_CODE


class UploadCommandAck(NodeCommandAck):
    receipt_id: str
    replayed: bool = False


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _UploadBase(_StrictModel):
    expected_revision: int = Field(..., ge=0, strict=True)
    client_request_id: str = Field(..., min_length=1, max_length=128)
    filename: str = Field(..., min_length=1, max_length=255)
    title: str | None = None
    x: float | None = None
    y: float | None = None
    width: float | None = Field(None, gt=0)
    height: float | None = Field(None, gt=0)

    @field_validator("client_request_id")
    @classmethod
    def _client_request_id(cls, value: str) -> str:
        if CLIENT_REQUEST_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("Invalid client_request_id")
        return value

    @field_validator("filename")
    @classmethod
    def _non_empty_filename(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("filename must be non-empty")
        return value

    @field_validator("x", "y", mode="before")
    @classmethod
    def _coordinate(cls, value: Any) -> float | None:
        if value is None:
            return None
        return _finite_number(value, limit=MAX_COORDINATE_ABS, name="coordinate")

    @field_validator("width", "height", mode="before")
    @classmethod
    def _size(cls, value: Any) -> float | None:
        if value is None:
            return None
        return _finite_number(value, limit=MAX_SIZE, name="size", positive=True)


class CreateMediaNodeUpload(_UploadBase):
    node_type: Literal["image", "audio", "video"]


class UploadReferenceAttach(_UploadBase):
    source_node_type: Literal["image", "audio", "video"] | None = None

    @model_validator(mode="after")
    def _source_node_type_matches_default(self) -> "UploadReferenceAttach":
        return self


def fingerprint_upload_request(
    command: CreateMediaNodeUpload | UploadReferenceAttach,
    *,
    operation: Literal["create_media_node", "upload_reference_attach"],
    media: ManagedUpload,
    target_node_id: str | None = None,
) -> str:
    fields: dict[str, Any] = {}
    for name in command.__class__.model_fields:
        if name in {"expected_revision", "client_request_id"}:
            continue
        fields[name] = {
            "present": name in command.model_fields_set,
            "value": getattr(command, name) if name in command.model_fields_set else None,
        }
    payload = {
        "request_contract_version": REQUEST_CONTRACT_VERSION,
        "operation": operation,
        "target_node_id": {"present": target_node_id is not None, "value": target_node_id},
        "fields": fields,
        "media": {
            "media_kind": media.media_kind,
            "size_bytes": media.size_bytes,
            "sha256": media.sha256,
            "width": media.width,
            "height": media.height,
            "duration": media.duration,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_media_node(
    project: AtelierProject,
    command: CreateMediaNodeUpload,
    *,
    managed: ManagedUpload,
    receipt_id: str,
    node_id: str,
    now: float,
    request_fingerprint: str,
) -> UploadCommandAck:
    if managed.media_kind != command.node_type:
        raise ValueError("Uploaded media kind does not match requested node type")
    node = _media_node(
        project,
        command,
        managed=managed,
        node_id=node_id,
        now=now,
    )
    project.nodes.append(node)
    receipt = AtelierNodeUploadReceipt(
        receipt_id=receipt_id,
        project_id=project.id,
        client_request_id=command.client_request_id,
        operation="create_media_node",
        request_contract_version=REQUEST_CONTRACT_VERSION,
        request_fingerprint=request_fingerprint,
        base_revision=command.expected_revision,
        committed_revision=command.expected_revision + 1,
        created_at=now,
        node_ids=[node.id],
        media=_media_proof(managed),
        source_node_id=node.id,
    )
    project.node_upload_receipts.append(receipt)
    project.updated_at = now
    return UploadCommandAck(project=project, node_ids=[node.id], receipt_id=receipt_id)


def upload_reference_attach(
    project: AtelierProject,
    target_node_id: str,
    command: UploadReferenceAttach,
    *,
    managed: ManagedUpload,
    receipt_id: str,
    node_id: str,
    now: float,
    request_fingerprint: str,
) -> UploadCommandAck:
    requested_kind = command.source_node_type or managed.media_kind
    if requested_kind != managed.media_kind:
        raise ValueError("Uploaded media kind does not match requested source node type")
    target = _find_node(project, target_node_id, "Target node not found")
    _assert_reference_target_idle(target)
    source_command = CreateMediaNodeUpload(
        expected_revision=command.expected_revision,
        client_request_id=command.client_request_id,
        filename=command.filename,
        node_type=managed.media_kind,
        title=command.title,
        x=command.x,
        y=command.y,
        width=command.width,
        height=command.height,
    )
    source = _media_node(project, source_command, managed=managed, node_id=node_id, now=now)
    if not can_attach_atelier_reference(target, source):
        raise ValueError("Unsupported reference source kind or self reference")

    proposed_nodes = [*project.nodes, source]
    target_data = dict(target.data or {})
    ids = [item for item in target_data.get("reference_node_ids", []) if isinstance(item, str)]
    ids.append(source.id)
    target_data["reference_node_ids"] = list(dict.fromkeys(ids))
    target_data.update(canonical_atelier_reference_data(target, proposed_nodes, target_data))
    field = f"reference_{source.type}_urls"
    urls = [item for item in target_data.get(field, []) if isinstance(item, str)]
    target_data[field] = list(dict.fromkeys([*urls, managed.url]))

    source_data = {
        **source.data,
        "parent_node_id": target.id,
        "reference_role": f"{target.type}_reference_{source.type}",
    }
    source.data = source_data
    target.data = target_data
    target.updated_at = now
    source.updated_at = now
    project.nodes.append(source)
    receipt = AtelierNodeUploadReceipt(
        receipt_id=receipt_id,
        project_id=project.id,
        client_request_id=command.client_request_id,
        operation="upload_reference_attach",
        request_contract_version=REQUEST_CONTRACT_VERSION,
        request_fingerprint=request_fingerprint,
        base_revision=command.expected_revision,
        committed_revision=command.expected_revision + 1,
        created_at=now,
        node_ids=[target.id, source.id],
        media=_media_proof(managed),
        target_node_id=target.id,
        source_node_id=source.id,
    )
    project.node_upload_receipts.append(receipt)
    project.updated_at = now
    return UploadCommandAck(project=project, node_ids=[target.id, source.id], receipt_id=receipt_id)


def replay_upload_receipt(project: AtelierProject, receipt: AtelierNodeUploadReceipt) -> UploadCommandAck:
    return UploadCommandAck(project=project, node_ids=list(receipt.node_ids), receipt_id=receipt.receipt_id, replayed=True)


def find_upload_receipt(project: AtelierProject, client_request_id: str) -> AtelierNodeUploadReceipt | None:
    return next((receipt for receipt in project.node_upload_receipts if receipt.client_request_id == client_request_id), None)


def _media_node(
    project: AtelierProject,
    command: CreateMediaNodeUpload,
    *,
    managed: ManagedUpload,
    node_id: str,
    now: float,
) -> AtelierNode:
    width, height = _node_size(command, managed.media_kind)
    return AtelierNode(
        id=node_id,
        project_id=project.id,
        type=managed.media_kind,
        title=command.title or _default_title(managed.media_kind, command.filename),
        prompt="",
        status="completed",
        x=command.x if command.x is not None else 0.0,
        y=command.y if command.y is not None else 0.0,
        width=width,
        height=height,
        media_urls=[managed.url],
        data=_media_data(command.filename, managed),
        created_by="user",
        created_at=now,
        updated_at=now,
    )


def _node_size(command: CreateMediaNodeUpload, kind: str) -> tuple[float, float]:
    default_width, default_height = {"image": (240.0, 240.0), "video": (320.0, 240.0), "audio": (260.0, 80.0)}[kind]
    return command.width if command.width is not None else default_width, command.height if command.height is not None else default_height


def _media_data(filename: str, managed: ManagedUpload) -> dict[str, Any]:
    data: dict[str, Any] = {
        "filename": filename,
        "size_bytes": managed.size_bytes,
        "sha256": managed.sha256,
    }
    for key in ("width", "height", "duration", "poster_url"):
        value = getattr(managed, key)
        if value is not None:
            data[key] = value
    return data


def _media_proof(managed: ManagedUpload) -> AtelierUploadedMediaProof:
    return AtelierUploadedMediaProof(
        url=managed.url,
        media_kind=managed.media_kind,
        size_bytes=managed.size_bytes,
        sha256=managed.sha256,
        width=managed.width,
        height=managed.height,
        duration=managed.duration,
        poster_url=managed.poster_url,
        poster_size_bytes=managed.poster_size_bytes,
        poster_sha256=managed.poster_sha256,
    )


def _default_title(kind: str, filename: str) -> str:
    return f"Uploaded {kind}: {filename}"


def _find_node(project: AtelierProject, node_id: str, message: str) -> AtelierNode:
    for node in project.nodes:
        if node.id == node_id:
            return node
    raise ValueError(message)


def _assert_reference_target_idle(node: AtelierNode) -> None:
    try:
        _assert_delete_safe(node)
    except StorageConflictError as exc:
        if str(exc) == "Atelier node has active execution":
            raise AtelierUploadTargetActiveError("Target node has active execution; wait for it to finish before uploading a reference") from exc
        raise


def _finite_number(value: Any, *, limit: float, name: str, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number) or abs(number) > limit or (positive and number <= 0):
        raise ValueError(f"{name} is out of range")
    return number
