"""FastAPI routes for durable Atelier sequence export jobs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from .export_jobs import CreateSequenceExportJob, RedoSequenceExportJob, SequenceExportJobAck
from .sequence import SequenceValidationError
from .sequence_export_service import SequenceExportError, SequenceExportJobNotFound, SequenceExportService
from .storage import AtelierProjectNotFound
from ...utils.json_storage import StorageError

router = APIRouter(tags=["atelier"])


@router.post("/projects/{project_id}/sequence/export_jobs", response_model=SequenceExportJobAck)
def create_sequence_export_job(project_id: str, payload: CreateSequenceExportJob, request: Request) -> SequenceExportJobAck:
    ack = _call_service(lambda: _service(request).create_sequence_export_job(project_id, payload))
    for job_id in ack.new_job_ids:
        _schedule_or_503(request, project_id, job_id, ack)
    return ack


@router.post("/projects/{project_id}/exports/{export_id}/redo", response_model=SequenceExportJobAck)
def redo_sequence_export_job(project_id: str, export_id: str, payload: RedoSequenceExportJob, request: Request) -> SequenceExportJobAck:
    ack = _call_service(lambda: _service(request).redo_sequence_export_job(project_id, export_id, payload))
    for job_id in ack.new_job_ids:
        _schedule_or_503(request, project_id, job_id, ack)
    return ack


@router.get("/projects/{project_id}/sequence/export_jobs/by_request/{client_request_id}", response_model=SequenceExportJobAck)
def get_sequence_export_job_by_request(project_id: str, client_request_id: str, request: Request) -> SequenceExportJobAck:
    return _call_service(lambda: _service(request).get_sequence_export_job_by_request(project_id, client_request_id))


@router.get("/projects/{project_id}/sequence/export_jobs/{job_id}", response_model=SequenceExportJobAck)
def get_sequence_export_job(project_id: str, job_id: str, request: Request) -> SequenceExportJobAck:
    return _call_service(lambda: _service(request).get_sequence_export_job(project_id, job_id))


@router.post("/projects/{project_id}/sequence/export_jobs/{job_id}/cancel", response_model=SequenceExportJobAck)
def cancel_sequence_export_job(project_id: str, job_id: str, request: Request) -> SequenceExportJobAck:
    return _call_service(lambda: _service(request).cancel_sequence_export_job(project_id, job_id))


@router.post("/projects/{project_id}/sequence/export_jobs/{job_id}/repair", response_model=SequenceExportJobAck)
def repair_sequence_export_job(project_id: str, job_id: str, request: Request) -> SequenceExportJobAck:
    return _call_service(lambda: _service(request).repair_sequence_export_job(project_id, job_id))


@router.post("/projects/{project_id}/sequence/export_jobs/{job_id}/resume", response_model=SequenceExportJobAck)
def resume_sequence_export_job(project_id: str, job_id: str, request: Request) -> SequenceExportJobAck:
    ack = _call_service(lambda: _service(request).resume_sequence_export_job(project_id, job_id))
    if ack.job.status in {"queued", "repair_required"}:
        _schedule_or_503(request, project_id, job_id, ack)
    return ack


def _schedule_or_503(request: Request, project_id: str, job_id: str, ack: SequenceExportJobAck) -> None:
    try:
        _dispatcher(request).schedule(project_id, job_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "sequence_export_dispatch_unavailable",
                "detail": "Export job was saved but could not be scheduled; read it back by request id or resume later",
                "project_id": project_id,
                "job_id": job_id,
                "client_request_id": ack.job.client_request_id,
                "replayed": ack.replayed,
            },
        ) from exc


def _service(request: Request) -> SequenceExportService:
    service = getattr(request.app.state, "atelier_sequence_export_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_sequence_export_unavailable", "detail": "Atelier export service is unavailable"},
        )
    return service


def _dispatcher(request: Request):
    dispatcher = getattr(request.app.state, "atelier_sequence_export_dispatcher", None)
    if dispatcher is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_sequence_export_unavailable", "detail": "Atelier export dispatcher is unavailable"},
        )
    return dispatcher


def _call_service(call):
    try:
        return call()
    except SequenceExportJobNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "sequence_export_job_not_found", "detail": str(exc)}) from exc
    except AtelierProjectNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "atelier_project_not_found", "detail": str(exc)}) from exc
    except SequenceValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_sequence", "report": exc.report}) from exc
    except SequenceExportError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_sequence_export", "detail": str(exc)}) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StorageError:
        raise
