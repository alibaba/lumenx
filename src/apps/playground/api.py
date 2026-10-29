"""Playground API routes — generation, history, and template management."""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File, Request

from .seedance_video_admission import SeedanceCapabilityError
from .models import (
    CreateTemplateRequest,
    GenerateRequest,
    PlaygroundMode,
    PlaygroundRequestReceipt,
    PlaygroundTemplate,
    SaveToLibraryRequest,
    UpdateTemplateRequest,
)
from ...models.provider_media_runtime import MediaRuntimeAuthorityError
from ...models.provider_recovery import ProviderRecoveryError
from ...models.provider_tasks import ProviderBindingError
from ...utils.generation_intent import IntentValidationError
from ...utils.media_probe import MediaProbeError
from ...utils.media_versions import MediaVersionError
from ...utils import get_logger
from ...utils.json_storage import StorageConflictError
from ...utils.request_identity import validate_client_request_id

logger = get_logger(__name__)

router = APIRouter(tags=["playground"])


def _storage(request: Request):
    return request.app.state.playground_storage


def _service(request: Request):
    return request.app.state.playground_service


def _is_core_video_generation(gen) -> bool:
    return gen.mode in (PlaygroundMode.T2V, PlaygroundMode.I2V, PlaygroundMode.R2V, PlaygroundMode.V2V) and any(
        attempt.provider_execution is not None for attempt in gen.attempts
    )


def _schedule_generation(http_request: Request, background_tasks: BackgroundTasks, gen) -> None:
    dispatcher = getattr(http_request.app.state, "playground_video_recovery_dispatcher", None)
    if dispatcher is not None and _is_core_video_generation(gen):
        for attempt in gen.attempts:
            if attempt.provider_execution is None:
                continue
            try:
                dispatcher.schedule(gen.id, attempt.attempt_id)
            except StorageConflictError:
                logger.info(
                    "Playground video attempt %s/%s saved but not queued; dispatcher recovery will resume it",
                    gen.id,
                    attempt.attempt_id,
                )
        return
    background_tasks.add_task(_service(http_request).process_generation, gen.id)

# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate(request: GenerateRequest, background_tasks: BackgroundTasks, http_request: Request) -> PlaygroundRequestReceipt:
    """Create a generation record and kick off processing in the background."""
    service = _service(http_request)
    try:
        validate_client_request_id(request.client_request_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_playground_request_id", "detail": str(exc)}) from exc
    try:
        receipt = service.admit_generation(request)
    except SeedanceCapabilityError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "playground_generation_not_admitted", "reason": exc.reason},
        ) from exc
    except (MediaRuntimeAuthorityError, ProviderBindingError, ProviderRecoveryError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "playground_generation_unavailable", "detail": "Generation is unavailable with the current provider configuration"},
        ) from exc
    except (IntentValidationError, MediaVersionError, MediaProbeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_playground_generation_request", "detail": "Invalid model, parameters or input media"},
        ) from exc
    except StorageConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "playground_request_conflict", "detail": str(exc)},
        ) from exc
    if not receipt.replayed and receipt.generation is not None:
        _schedule_generation(http_request, background_tasks, receipt.generation)
    return receipt


router.add_api_route("/generate", generate, methods=["POST"])


def get_capabilities(request: Request):
    """Discover bounded admission capabilities without provider or CLI work."""
    return _service(request).get_capabilities()


router.add_api_route("/capabilities", get_capabilities, methods=["GET"])


def get_request_receipt(client_request_id: str, request: Request):
    """Read the admission receipt for a client request id without resubmitting."""
    try:
        request_id = validate_client_request_id(client_request_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_playground_request_id", "detail": str(exc)}) from exc
    receipt = _storage(request).read_request(request_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail={"code": "playground_request_not_found", "detail": "Playground request not found"})
    return receipt


router.add_api_route("/requests/{client_request_id}", get_request_receipt, methods=["GET"])

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def list_history(request: Request, limit: int = 50, offset: int = 0):
    """Return paginated generation history, newest first."""
    return _storage(request).list_history(limit=limit, offset=offset)


def get_generation(generation_id: str, request: Request):
    """Return full details for a single generation."""
    gen = _storage(request).get_generation(generation_id)
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")
    return gen


def get_generation_status(generation_id: str, request: Request):
    """Return lightweight status payload for polling."""
    gen = _storage(request).get_generation(generation_id)
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")
    return {
        "id": gen.id,
        "status": gen.status,
        "outputs": gen.outputs,
        "attempts": gen.attempts,
        "error": gen.error,
    }


def delete_generation(generation_id: str, request: Request):
    """Delete a generation record and its outputs."""
    if not _storage(request).delete_generation(generation_id):
        raise HTTPException(status_code=404, detail="Generation not found")
    return {"ok": True}


def save_to_library(
    generation_id: str,
    output_id: str,
    http_request: Request,
    request: Optional[SaveToLibraryRequest] = None,
):
    """Save a specific generation output to the project library."""
    category = request.category if request else "general"
    if not _service(http_request).save_to_library(generation_id, output_id, category):
        raise HTTPException(status_code=404, detail="Generation or output not found")
    return {"ok": True}


router.add_api_route("/history", list_history, methods=["GET"])
router.add_api_route("/history/{generation_id}", get_generation, methods=["GET"])
router.add_api_route(
    "/history/{generation_id}/status", get_generation_status, methods=["GET"]
)
router.add_api_route(
    "/history/{generation_id}", delete_generation, methods=["DELETE"]
)
router.add_api_route(
    "/history/{generation_id}/outputs/{output_id}/save-to-library",
    save_to_library,
    methods=["POST"],
)

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def list_templates(request: Request):
    """Return all saved prompt templates."""
    return _storage(request).list_templates()


def create_template(request: CreateTemplateRequest, http_request: Request):
    """Create a new prompt template."""
    now = datetime.now(timezone.utc).isoformat()
    template = PlaygroundTemplate(
        id=str(uuid.uuid4()),
        name=request.name,
        category=request.category or "general",
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        default_mode=request.default_mode,
        default_model_id=request.default_model_id,
        default_parameters=request.default_parameters or {},
        created_at=now,
        updated_at=now,
    )
    _storage(http_request).add_template(template)
    return template


def update_template(template_id: str, request: UpdateTemplateRequest, http_request: Request):
    """Update an existing prompt template (partial update)."""
    storage = _storage(http_request)
    template = storage.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    update_data = request.model_dump(exclude_none=True)
    for key, value in update_data.items():
        setattr(template, key, value)
    template.updated_at = datetime.now(timezone.utc).isoformat()
    storage.update_template(template)
    return template


def delete_template(template_id: str, request: Request):
    """Delete a prompt template."""
    if not _storage(request).delete_template(template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


router.add_api_route("/templates", list_templates, methods=["GET"])
router.add_api_route("/templates", create_template, methods=["POST"])
router.add_api_route("/templates/{template_id}", update_template, methods=["PUT"])
router.add_api_route("/templates/{template_id}", delete_template, methods=["DELETE"])

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def _upload_dir(request: Request) -> Path:
    return Path(request.app.state.data_dir) / "playground" / "uploads"


async def upload_media(request: Request, file: UploadFile = File(...)):
    """Upload a media file for use as playground input (reference image, first frame, etc.)."""
    upload_dir = _upload_dir(request)
    os.makedirs(upload_dir, exist_ok=True)
    ext = os.path.splitext(file.filename or "file")[1] or ".bin"
    filename = f"{uuid.uuid4()}{ext}"
    dest = upload_dir / filename
    contents = await file.read()
    with open(dest, "wb") as f:
        f.write(contents)
    return {"path": dest.relative_to(request.app.state.data_dir).as_posix()}


router.add_api_route("/upload", upload_media, methods=["POST"])
