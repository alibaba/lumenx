"""FastAPI router for product-owned Atelier project CRUD."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .media_upload import DEFAULT_MAX_BYTES
from .models import AtelierNodeUploadReceipt, AtelierProject
from .node_history import DeleteNodesAck, RestoreNodesAck
from .reference_commands import AttachPendingReferenceCommand, AttachReferenceCommand, DetachReferenceCommand
from .nodes import (
    BatchLayoutCommand,
    CreateNodeCommand,
    CreateRegionWrapCommand,
    LayoutPatch,
    MoveNodeCommand,
    NodeBodyPatch,
    NodeCommandAck,
    NodeTextPatch,
    RegionAppearancePatch,
    RegionMembersCommand,
)
from .service import AtelierService, AtelierUploadReceiptNotFound, SourceProjectNotFound
from .storage import AtelierProjectNotFound
from .sequence import SequenceValidationError
from .sequence_commands import AUDIO_TRACKS_OMITTED, ReplaceSequenceCommand
from .upload_commands import (
    UPLOAD_TARGET_ACTIVE_CODE,
    AtelierUploadTargetActiveError,
    CreateMediaNodeUpload,
    UploadCommandAck,
    UploadReferenceAttach,
)

router = APIRouter(tags=["atelier"])


class CreateAtelierProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(...)
    description: str = Field("")
    source_project_id: str | None = Field(default=None)


class UpdateAtelierProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0, strict=True)
    title: str | None = None
    description: str | None = None
    source_project_id: str | None = None


class DeleteAtelierProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0, strict=True)


class DeleteAtelierNodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0, strict=True)
    node_ids: list[str] = Field(..., min_length=1, max_length=100)


class RestoreAtelierNodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0, strict=True)
    receipt_id: str = Field(..., min_length=1, max_length=128)


_CREATE_UPLOAD_QUERY = {"expected_revision", "client_request_id", "filename", "node_type", "title", "x", "y", "width", "height"}
_REFERENCE_UPLOAD_QUERY = {"expected_revision", "client_request_id", "filename", "source_node_type", "title", "x", "y", "width", "height"}
_RAW_UPLOAD_MAX_BYTES_BY_KIND = dict(DEFAULT_MAX_BYTES)
_RAW_UPLOAD_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"},
    "video": {".mp4", ".mov", ".webm", ".mkv"},
}


class ReplaceAtelierSequenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0, strict=True)
    entries: list[dict[str, Any]] = Field(...)
    audio_tracks: list[dict[str, Any]] | None = None


class PreflightAtelierSequenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[dict[str, Any]] = Field(...)
    audio_tracks: list[dict[str, Any]] | None = None


class PreflightAtelierSequenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    revision: int
    report: dict[str, Any]


class UploadReceiptReadback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "found"
    project: AtelierProject
    receipt: AtelierNodeUploadReceipt


@router.get("/projects", response_model=list[AtelierProject])
def list_projects(request: Request) -> list[AtelierProject]:
    return _call_service(lambda: _service(request).list_projects())


@router.post("/projects", response_model=AtelierProject)
def create_project(
    payload: CreateAtelierProjectRequest, request: Request
) -> AtelierProject:
    return _call_service(
        lambda: _service(request).create_project(
            **payload.model_dump(exclude_unset=True)
        )
    )


@router.get("/projects/{project_id}", response_model=AtelierProject)
def get_project(project_id: str, request: Request) -> AtelierProject:
    return _call_service(lambda: _service(request).get_project(project_id))


@router.get("/projects/{project_id}/uploads/receipts/{client_request_id}", response_model=UploadReceiptReadback)
def get_upload_receipt(project_id: str, client_request_id: str, request: Request) -> UploadReceiptReadback:
    def readback() -> UploadReceiptReadback:
        project, receipt = _service(request).get_upload_receipt_readback(project_id, client_request_id)
        return UploadReceiptReadback(project=project, receipt=receipt)

    return _call_service(readback)


@router.post("/projects/{project_id}/uploads/nodes", response_model=UploadCommandAck)
async def create_media_node_upload(project_id: str, request: Request) -> UploadCommandAck:
    payload = _validate_upload_query(CreateMediaNodeUpload, request, _CREATE_UPLOAD_QUERY)
    spool = await _spool_upload_body(request, filename=payload.filename)
    try:
        return await run_in_threadpool(
            lambda: _call_service(
                lambda: _service(request).create_media_node_upload(
                    project_id,
                    payload,
                    _file_chunks(spool),
                    content_type=request.headers.get("content-type"),
                )
            )
        )
    finally:
        _unlink_spool(spool)


@router.post("/projects/{project_id}/uploads/references/{target_node_id}", response_model=UploadCommandAck)
async def upload_reference_attach(project_id: str, target_node_id: str, request: Request) -> UploadCommandAck:
    payload = _validate_upload_query(UploadReferenceAttach, request, _REFERENCE_UPLOAD_QUERY)
    spool = await _spool_upload_body(request, filename=payload.filename)
    try:
        return await run_in_threadpool(
            lambda: _call_service(
                lambda: _service(request).upload_reference_attach(
                    project_id,
                    target_node_id,
                    payload,
                    _file_chunks(spool),
                    content_type=request.headers.get("content-type"),
                )
            )
        )
    finally:
        _unlink_spool(spool)


@router.put("/projects/{project_id}/sequence", response_model=AtelierProject)
def replace_sequence(
    project_id: str, payload: ReplaceAtelierSequenceRequest, request: Request
) -> AtelierProject:
    if "audio_tracks" in payload.model_fields_set and payload.audio_tracks is None:
        raise HTTPException(status_code=422, detail="audio_tracks must be omitted or a list")
    audio_tracks = (
        AUDIO_TRACKS_OMITTED
        if "audio_tracks" not in payload.model_fields_set
        else payload.audio_tracks
    )
    command = ReplaceSequenceCommand(
        expected_revision=payload.expected_revision,
        entries=payload.entries,
        audio_tracks=audio_tracks,
    )
    return _call_service(lambda: _service(request).replace_sequence(project_id, command))


@router.post(
    "/projects/{project_id}/sequence/preflight",
    response_model=PreflightAtelierSequenceResponse,
)
def preflight_sequence_route(
    project_id: str, payload: PreflightAtelierSequenceRequest, request: Request
) -> PreflightAtelierSequenceResponse:
    if "audio_tracks" in payload.model_fields_set and payload.audio_tracks is None:
        raise HTTPException(status_code=422, detail="audio_tracks must be omitted or a list")
    audio_tracks = (
        AUDIO_TRACKS_OMITTED
        if "audio_tracks" not in payload.model_fields_set
        else payload.audio_tracks
    )
    report = _call_service(
        lambda: _service(request).preflight_sequence(
            project_id,
            entries=payload.entries,
            audio_tracks=audio_tracks,
        )
    )
    return PreflightAtelierSequenceResponse(
        project_id=report.project_id, revision=report.revision, report=report.report
    )

@router.put("/projects/{project_id}", response_model=AtelierProject)
def update_project(
    project_id: str, payload: UpdateAtelierProjectRequest, request: Request
) -> AtelierProject:
    values = payload.model_dump(exclude_unset=True)
    expected_revision = values.pop("expected_revision")
    return _call_service(
        lambda: _service(request).update_project(
            project_id,
            expected_revision=expected_revision,
            **values,
        )
    )


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str, payload: DeleteAtelierProjectRequest, request: Request
) -> dict[str, bool]:
    _call_service(
        lambda: _service(request).delete_project(
            project_id,
            expected_revision=payload.expected_revision,
        )
    )
    return {"ok": True}




@router.post("/projects/{project_id}/nodes/{node_id}/references/attach_pending", response_model=NodeCommandAck)
def attach_pending_reference(
    project_id: str, node_id: str, payload: AttachPendingReferenceCommand, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).attach_pending_reference(project_id, node_id, payload))


@router.post("/projects/{project_id}/nodes/{node_id}/references/attach", response_model=NodeCommandAck)
def attach_reference(
    project_id: str, node_id: str, payload: AttachReferenceCommand, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).attach_reference(project_id, node_id, payload))


@router.post("/projects/{project_id}/nodes/{node_id}/references/detach", response_model=NodeCommandAck)
def detach_reference(
    project_id: str, node_id: str, payload: DetachReferenceCommand, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).detach_reference(project_id, node_id, payload))


@router.post("/projects/{project_id}/nodes/delete", response_model=DeleteNodesAck)
def delete_nodes(project_id: str, payload: DeleteAtelierNodesRequest, request: Request) -> DeleteNodesAck:
    return _call_service(
        lambda: _service(request).delete_nodes(
            project_id,
            expected_revision=payload.expected_revision,
            node_ids=payload.node_ids,
        )
    )


@router.post("/projects/{project_id}/nodes/restore", response_model=RestoreNodesAck)
def restore_nodes(project_id: str, payload: RestoreAtelierNodesRequest, request: Request) -> RestoreNodesAck:
    return _call_service(
        lambda: _service(request).restore_nodes(
            project_id,
            expected_revision=payload.expected_revision,
            receipt_id=payload.receipt_id,
        )
    )


@router.post("/projects/{project_id}/nodes", response_model=NodeCommandAck)
def create_node(project_id: str, payload: CreateNodeCommand, request: Request) -> NodeCommandAck:
    return _call_service(lambda: _service(request).create_node(project_id, payload))


@router.put("/projects/{project_id}/nodes/{node_id}/text", response_model=NodeCommandAck)
def update_node_text(
    project_id: str, node_id: str, payload: NodeTextPatch, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).update_node_text(project_id, node_id, payload))


@router.put("/projects/{project_id}/nodes/{node_id}/body", response_model=NodeCommandAck)
def update_node_body(
    project_id: str, node_id: str, payload: NodeBodyPatch, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).update_node_body(project_id, node_id, payload))


@router.put("/projects/{project_id}/nodes/{node_id}/layout", response_model=NodeCommandAck)
def update_node_layout(
    project_id: str, node_id: str, payload: LayoutPatch, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).update_node_layout(project_id, node_id, payload))


@router.put("/projects/{project_id}/nodes/layout", response_model=NodeCommandAck)
def update_nodes_layout(
    project_id: str, payload: BatchLayoutCommand, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).update_nodes_layout(project_id, payload))


@router.post("/projects/{project_id}/nodes/move", response_model=NodeCommandAck)
def move_nodes(project_id: str, payload: MoveNodeCommand, request: Request) -> NodeCommandAck:
    return _call_service(lambda: _service(request).move_nodes(project_id, payload))


@router.post("/projects/{project_id}/regions/wrap", response_model=NodeCommandAck)
def wrap_region(
    project_id: str, payload: CreateRegionWrapCommand, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).wrap_region(project_id, payload))


@router.post("/projects/{project_id}/regions/{region_id}/members", response_model=NodeCommandAck)
def set_region_members(
    project_id: str, region_id: str, payload: RegionMembersCommand, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).set_region_members(project_id, region_id, payload))

@router.put("/projects/{project_id}/regions/{region_id}/appearance", response_model=NodeCommandAck)
def update_region_appearance(
    project_id: str, region_id: str, payload: RegionAppearancePatch, request: Request
) -> NodeCommandAck:
    return _call_service(lambda: _service(request).update_region_appearance(project_id, region_id, payload))


def _service(request: Request) -> AtelierService:
    service = getattr(request.app.state, "atelier_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "atelier_service_unavailable",
                "detail": "Atelier service is unavailable",
            },
        )
    return service


def _call_service(call):
    try:
        return call()
    except AtelierUploadReceiptNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SourceProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AtelierProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SequenceValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_sequence", "report": exc.report},
        ) from exc
    except AtelierUploadTargetActiveError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": UPLOAD_TARGET_ACTIVE_CODE,
                "detail": str(exc),
                "commit_uncertain": False,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _strict_query(request: Request, allowed: set[str]) -> dict:
    seen: set[str] = set()
    values: dict[str, str | int | float] = {}
    for key, value in request.query_params.multi_items():
        if key not in allowed:
            raise HTTPException(status_code=422, detail=f"Unsupported query parameter: {key}")
        if key in seen:
            raise HTTPException(status_code=422, detail=f"Duplicate query parameter: {key}")
        seen.add(key)
        values[key] = value
    if "expected_revision" in values:
        raw = values["expected_revision"]
        if not isinstance(raw, str) or not raw.isdigit():
            raise HTTPException(status_code=422, detail="Invalid expected_revision")
        values["expected_revision"] = int(raw)
    for key in ("x", "y", "width", "height"):
        if key in values:
            raw = values[key]
            try:
                values[key] = float(raw)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"Invalid {key}") from exc
    return values


def _validate_upload_query(model, request: Request, allowed: set[str]):
    try:
        return model.model_validate(_strict_query(request, allowed))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _spool_upload_body(request: Request, *, filename: str) -> Path:
    max_bytes = _max_upload_bytes_for_filename(filename)
    descriptor, name = tempfile.mkstemp(prefix="lumenx-atelier-api-upload-")
    path = Path(name)
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail="Upload exceeds size limit")
                await run_in_threadpool(target.write, chunk)
            await run_in_threadpool(_flush_and_fsync, target)
        return path
    except BaseException:
        _unlink_spool(path)
        raise


def _flush_and_fsync(target) -> None:
    target.flush()
    os.fsync(target.fileno())


def _file_chunks(path: Path):
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            yield chunk


def _unlink_spool(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _max_upload_bytes_for_filename(filename: str) -> int:
    suffix = Path(filename or "").suffix.lower()
    for kind, extensions in _RAW_UPLOAD_EXTENSIONS.items():
        if suffix in extensions:
            return _RAW_UPLOAD_MAX_BYTES_BY_KIND[kind]
    raise HTTPException(status_code=422, detail="Unsupported media file type")
