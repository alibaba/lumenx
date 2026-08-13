"""Playground API routes — generation, history, and template management."""

import os
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

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
    gen = _service.create_generation(request)
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
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}
_ALLOWED_EXTENSIONS = _IMAGE_EXTENSIONS | _VIDEO_EXTENSIONS
_MODE_MEDIA_KINDS: dict[str, set[str]] = {
    "t2i": {"image"},
    "i2i": {"image"},
    "i2v": {"image"},
    "r2v": {"image", "video"},
    "v2v": {"video"},
}


def _media_kind_from_signature(header: bytes) -> Optional[Literal["image", "video"]]:
    """Identify supported media from trusted binary signatures, not client MIME."""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if header.startswith(b"\xff\xd8\xff"):
        return "image"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "video"
    if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
        return "video"
    return None


def _validate_upload_name(filename: Optional[str]) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file extension. Upload PNG, JPEG, GIF, WebP, MP4, MOV, WebM, AVI, or MKV.",
        )
    return ext


def _validate_upload_kind(mode: str, extension: str, header: bytes) -> None:
    if mode not in _MODE_MEDIA_KINDS:
        raise HTTPException(status_code=400, detail="This generation mode does not accept uploaded media.")

    kind = _media_kind_from_signature(header)
    if kind is None:
        raise HTTPException(status_code=400, detail="File content is not a supported image or video format.")

    extension_kind = "image" if extension in _IMAGE_EXTENSIONS else "video"
    if kind != extension_kind:
        raise HTTPException(status_code=400, detail="File extension does not match the uploaded file content.")
    if kind not in _MODE_MEDIA_KINDS[mode]:
        raise HTTPException(status_code=400, detail=f"Mode '{mode}' does not accept {kind} uploads.")


async def upload_media(file: UploadFile = File(...), mode: str = Form(...)):
    """Upload a validated image/video source for the selected Playground mode.

    The client-supplied MIME type is intentionally ignored: extension allow-lists
    and binary signatures determine whether the file is accepted.
    """
    extension = _validate_upload_name(file.filename)
    initial_chunk = await file.read(UPLOAD_CHUNK_BYTES)
    if len(initial_chunk) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 100 MiB upload limit.")
    _validate_upload_kind(mode, extension, initial_chunk)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4()}{extension}"
    dest = os.path.join(UPLOAD_DIR, filename)
    written = 0

    try:
        with open(dest, "wb") as output:
            output.write(initial_chunk)
            written += len(initial_chunk)
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds the 100 MiB upload limit.")
                output.write(chunk)
    except Exception:
        if os.path.exists(dest):
            os.remove(dest)
        raise
    finally:
        await file.close()

    return {"path": dest}


router.add_api_route("/upload", upload_media, methods=["POST"])
