"""Playground API routes — generation, history, and template management."""

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File

from .models import (
    CreateTemplateRequest,
    GenerateRequest,
    PlaygroundTemplate,
    SaveToLibraryRequest,
    UpdateTemplateRequest,
)
from .service import PlaygroundService
from .storage import PlaygroundStorage
from ...utils import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["playground"])

# Module-level singletons — initialised when the router is first imported.
_storage = PlaygroundStorage()
_service = PlaygroundService(_storage)

# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate(request: GenerateRequest, background_tasks: BackgroundTasks):
    """Create a generation record and kick off processing in the background."""
    try:
        gen = _service.create_generation(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(_service.process_generation, gen.id)
    return gen


router.add_api_route("/generate", generate, methods=["POST"])

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def list_history(limit: int = 50, offset: int = 0):
    """Return paginated generation history, newest first."""
    return _storage.list_history(limit=limit, offset=offset)


def get_generation(generation_id: str):
    """Return full details for a single generation."""
    gen = _storage.get_generation(generation_id)
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")
    return gen


def get_generation_status(generation_id: str):
    """Return lightweight status payload for polling."""
    gen = _storage.get_generation(generation_id)
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")
    return {
        "id": gen.id,
        "status": gen.status,
        "outputs": gen.outputs,
        "error": gen.error,
    }


def delete_generation(generation_id: str):
    """Delete a generation record and its outputs."""
    if not _storage.delete_generation(generation_id):
        raise HTTPException(status_code=404, detail="Generation not found")
    return {"ok": True}


def save_to_library(
    generation_id: str,
    output_id: str,
    request: Optional[SaveToLibraryRequest] = None,
):
    """Save a specific generation output to the project library."""
    category = request.category if request else "general"
    if not _service.save_to_library(generation_id, output_id, category):
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


def list_templates():
    """Return all saved prompt templates."""
    return _storage.list_templates()


def create_template(request: CreateTemplateRequest):
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
    _storage.add_template(template)
    return template


def update_template(template_id: str, request: UpdateTemplateRequest):
    """Update an existing prompt template (partial update)."""
    template = _storage.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    update_data = request.model_dump(exclude_none=True)
    for key, value in update_data.items():
        setattr(template, key, value)
    template.updated_at = datetime.now(timezone.utc).isoformat()
    _storage.update_template(template)
    return template


def delete_template(template_id: str):
    """Delete a prompt template."""
    if not _storage.delete_template(template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


router.add_api_route("/templates", list_templates, methods=["GET"])
router.add_api_route("/templates", create_template, methods=["POST"])
router.add_api_route("/templates/{template_id}", update_template, methods=["PUT"])
router.add_api_route("/templates/{template_id}", delete_template, methods=["DELETE"])

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

UPLOAD_DIR = os.path.join("output", "playground", "uploads")


async def upload_media(file: UploadFile = File(...)):
    """Upload a media file for use as playground input (reference image, first frame, etc.)."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename or "file")[1] or ".bin"
    filename = f"{uuid.uuid4()}{ext}"
    dest = os.path.join(UPLOAD_DIR, filename)
    contents = await file.read()
    with open(dest, "wb") as f:
        f.write(contents)
    return {"path": dest}


router.add_api_route("/upload", upload_media, methods=["POST"])
