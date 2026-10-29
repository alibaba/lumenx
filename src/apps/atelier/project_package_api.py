"""FastAPI routes for Atelier project package export/import."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from ...utils.json_storage import StorageConflictError, StorageError
from .models import AtelierProject
from .project_package import DEFAULT_MAX_PACKAGE_BYTES, AtelierProjectPackageError
from .project_package_service import AtelierProjectPackageService
from .storage import AtelierProjectNotFound

router = APIRouter(tags=["atelier-project-package"])

_ALLOWED_PACKAGE_MEDIA_TYPES = {"application/zip", "application/octet-stream"}


class AtelierProjectPackageTooLarge(Exception):
    """Raised when the request body exceeds the package size budget."""


def _no_store_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _package_error_response(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "detail": detail},
        headers=_no_store_headers(),
    )


def _max_package_bytes(request: Request) -> int:
    value = getattr(
        request.app.state,
        "atelier_project_package_max_bytes",
        DEFAULT_MAX_PACKAGE_BYTES,
    )
    if type(value) is not int or value <= 0:
        return DEFAULT_MAX_PACKAGE_BYTES
    return value


async def _read_raw_atelier_project_package(request: Request) -> bytes:
    media_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media_type not in _ALLOWED_PACKAGE_MEDIA_TYPES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "atelier_project_package_invalid_request",
                "detail": "Package body must be application/zip or application/octet-stream",
            },
            headers=_no_store_headers(),
        )
    max_bytes = _max_package_bytes(request)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "atelier_project_package_invalid_request",
                    "detail": "Invalid package request",
                },
                headers=_no_store_headers(),
            ) from exc
        if declared < 0:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "atelier_project_package_invalid_request",
                    "detail": "Invalid package request",
                },
                headers=_no_store_headers(),
            )
        if declared > max_bytes:
            raise AtelierProjectPackageTooLarge("Atelier project package is too large")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise AtelierProjectPackageTooLarge("Atelier project package is too large")
        body.extend(chunk)
    if not body:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "atelier_project_package_invalid_request",
                "detail": "Package body is required",
            },
            headers=_no_store_headers(),
        )
    return bytes(body)


@router.get("/projects/{project_id}/package/export")
def export_project_package(project_id: str, request: Request):
    try:
        artifact = _service(request).export_project_package(project_id)
    except AtelierProjectNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "atelier_project_not_found", "detail": "Atelier project not found"},
            headers=_no_store_headers(),
        ) from exc
    except AtelierProjectPackageError:
        return _package_error_response(
            422,
            "atelier_project_package_invalid",
            "Atelier project package is invalid",
        )
    except StorageConflictError:
        return _package_error_response(
            409,
            "atelier_storage_conflict",
            "Atelier storage changed; reload and retry",
        )
    except StorageError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "code": "atelier_storage_unavailable",
                "detail": "Atelier storage is unavailable; restart before continuing",
                "commit_uncertain": exc.commit_uncertain,
            },
            headers=_no_store_headers(),
        )
    filename = f"lumenx-atelier-project-{project_id}.lumenx.zip"
    return Response(
        content=artifact.data,
        media_type="application/zip",
        headers={
            **_no_store_headers(),
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.post("/projects/package/import", response_model=AtelierProject)
async def import_project_package(request: Request):
    try:
        package_bytes = await _read_raw_atelier_project_package(request)
        return await asyncio.to_thread(_service(request).import_project_package, package_bytes)
    except AtelierProjectPackageTooLarge:
        return _package_error_response(
            413,
            "atelier_project_package_too_large",
            "Atelier project package is too large",
        )
    except AtelierProjectPackageError:
        return _package_error_response(
            422,
            "atelier_project_package_invalid",
            "Atelier project package is invalid",
        )
    except ValidationError:
        return _package_error_response(
            422,
            "atelier_project_package_invalid",
            "Atelier project package is invalid",
        )
    except StorageConflictError:
        return _package_error_response(
            409,
            "atelier_storage_conflict",
            "Atelier storage changed; reload and retry",
        )
    except StorageError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "code": "atelier_storage_unavailable",
                "detail": "Atelier storage is unavailable; restart before continuing",
                "commit_uncertain": exc.commit_uncertain,
            },
            headers=_no_store_headers(),
        )


def _service(request: Request) -> AtelierProjectPackageService:
    service = getattr(request.app.state, "atelier_project_package_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "atelier_project_package_unavailable",
                "detail": "Atelier project package service is unavailable",
            },
            headers=_no_store_headers(),
        )
    return service
