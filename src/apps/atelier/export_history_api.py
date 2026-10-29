"""FastAPI routes for Atelier export history deletion."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from .export_history import DeleteExportRecord, DeleteExportRecordAck, ExportHistoryService, ExportRecordNotFound
from .storage import AtelierProjectNotFound
from ...utils.json_storage import StorageError

router = APIRouter(tags=["atelier"])


@router.delete("/projects/{project_id}/exports/{export_id}", response_model=DeleteExportRecordAck)
def delete_export_record(project_id: str, export_id: str, payload: DeleteExportRecord, request: Request) -> DeleteExportRecordAck:
    return _call_service(lambda: _service(request).delete_export(project_id, export_id, payload))


def _service(request: Request) -> ExportHistoryService:
    service = getattr(request.app.state, "atelier_export_history_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "atelier_export_history_unavailable", "detail": "Atelier export history service is unavailable"},
        )
    return service


def _call_service(call):
    try:
        return call()
    except ExportRecordNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "atelier_export_record_not_found", "detail": str(exc)}) from exc
    except AtelierProjectNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "atelier_project_not_found", "detail": str(exc)}) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StorageError:
        raise
