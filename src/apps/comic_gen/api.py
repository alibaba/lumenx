# ─────────────────────────────────────────────────────────────────────────────
# IMPORTANT — handler async/sync convention (audited 2026-05-21):
#
# DEFAULT every new endpoint to `def`, NOT `async def`.
#
# FastAPI's contract:
#   - `async def` handlers run on the asyncio event loop. If the body does any
#     blocking I/O (sync HTTP call, json.dump, requests.post, openai sync
#     client, ffmpeg via subprocess.run, etc.) it FREEZES every other request
#     until the blocking call returns — including unrelated GETs the modal
#     uses to load config. Symptom: "modal stuck on 加载配置中...".
#   - `def` handlers get auto-dispatched to anyio's threadpool, so blocking
#     I/O doesn't touch the event loop. Concurrent requests stay responsive.
#
# Use `async def` ONLY when the body has `await` (streaming uploads, real
# asyncio primitives, awaiting `call_next` in middleware, asyncio.to_thread).
#
# The 7 legitimate async endpoints (verified to use await): middleware
# add_cache_control_header, create_project, reparse_project,
# import_file_preview, import_file_confirm, upload_t2i_frame,
# analyze_script_for_styles. All others are `def` for a reason.
# ─────────────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any, Tuple
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import concurrent.futures
import hashlib
import json
import os
import shutil
import uuid
import logging
import traceback
from .pipeline import ComicGenPipeline, LibraryAssetInUseError
from .models import (
    ArtDirection,
    PromptConfig,
    ProviderBackend,
    ProviderRoutingConfig,
    Script,
    Series,
    StoryboardFrame,
    VideoTask,
)
from .llm import ScriptProcessor, DEFAULT_STORYBOARD_POLISH_PROMPT, DEFAULT_VIDEO_POLISH_PROMPT, DEFAULT_R2V_POLISH_PROMPT, DEFAULT_ENTITY_EXTRACTION_PROMPT, DEFAULT_STYLE_ANALYSIS_PROMPT, DEFAULT_STORYBOARD_EXTRACTION_PROMPT
from ...utils.oss_utils import OSSImageUploader, sign_oss_urls_in_data
from ...utils import setup_logging
from fastapi.responses import JSONResponse
from dotenv import load_dotenv, set_key

app = FastAPI(title="AI Comic Gen API")
logger = logging.getLogger(__name__)

# Setup logging to user directory
setup_logging()

# Use absolute path for .env file (api.py is in src/apps/comic_gen/)
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
env_path = os.path.join(_project_root, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path, override=True)

# Mount playground router AFTER .env is loaded (adapters read API keys from env)
from ..playground.api import router as playground_router
app.include_router(playground_router, prefix="/playground")

# Debug: Print OSS configuration at startup
logger.info(f"STARTUP: OSS_ENDPOINT={os.getenv('OSS_ENDPOINT')}, OSS_BUCKET_NAME={os.getenv('OSS_BUCKET_NAME')}, OSS_BASE_PATH={os.getenv('OSS_BASE_PATH')}")



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify the frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],  # Allow browsers to access Content-Disposition for downloads
)

# Middleware to add cache headers to static files
@app.middleware("http")
async def add_cache_control_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/files/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response

# Create output directory if it doesn't exist
os.makedirs("output", exist_ok=True)
os.makedirs("output/uploads", exist_ok=True)
os.makedirs("output/video", exist_ok=True)
os.makedirs("output/assets", exist_ok=True)

# Mount static files with multiple aliases to handle plural/singular inconsistencies
# Legacy paths in projects.json often use 'outputs/videos' or 'outputs/assets'
app.mount("/files/outputs/videos", StaticFiles(directory="output/video"), name="files_outputs_videos")
app.mount("/files/outputs/assets", StaticFiles(directory="output/assets"), name="files_outputs_assets")
app.mount("/files/outputs", StaticFiles(directory="output"), name="files_outputs")
app.mount("/files/videos", StaticFiles(directory="output/video"), name="files_videos")
app.mount("/files/assets", StaticFiles(directory="output/assets"), name="files_assets")
app.mount("/files", StaticFiles(directory="output"), name="files")

# Ensure playground output directories exist
os.makedirs("output/playground/images", exist_ok=True)
os.makedirs("output/playground/videos", exist_ok=True)
app.mount("/files/playground", StaticFiles(directory="output/playground"), name="files_playground")


# Initialize pipeline
pipeline = ComicGenPipeline()

# Allow-list map for uploaded file extensions: keys come from the (untrusted)
# client filename, values are trusted literals safe to embed in server paths.
_UPLOAD_EXT_MAP = {
    ".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpeg", ".webp": ".webp",
    ".gif": ".gif", ".bmp": ".bmp", ".svg": ".svg", ".ico": ".ico",
    ".mp4": ".mp4", ".mov": ".mov", ".webm": ".webm", ".avi": ".avi",
    ".mkv": ".mkv", ".mp3": ".mp3", ".wav": ".wav", ".m4a": ".m4a",
    ".aac": ".aac", ".ogg": ".ogg", ".flac": ".flac",
    ".txt": ".txt", ".md": ".md", ".pdf": ".pdf", ".json": ".json",
}


def _safe_upload_ext(filename: Optional[str]) -> str:
    """Return a trusted file extension literal for an uploaded filename.

    Unknown/missing extensions fall back to empty string so the stored file
    is still saved under its random UUID name.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    return _UPLOAD_EXT_MAP.get(ext, "")


@app.get("/debug/config")
def debug_config():
    """Diagnostic endpoint to check OSS and path configuration."""
    uploader = OSSImageUploader()
    return {
        "oss_configured": uploader.is_configured,
        "oss_bucket_initialized": uploader.bucket is not None,
        "oss_base_path": os.getenv("OSS_BASE_PATH", "lumenx"),
        "output_dir_exists": os.path.exists("output"),
        "output_contents": os.listdir("output") if os.path.exists("output") else [],
        "cwd": os.getcwd(),
        "env_vars_present": {
            "OSS_ENDPOINT": bool(os.getenv("OSS_ENDPOINT")),
            "OSS_BUCKET_NAME": bool(os.getenv("OSS_BUCKET_NAME")),
            "ALIBABA_CLOUD_ACCESS_KEY_ID": bool(os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")),
        }
    }

def signed_response(data):
    """Helper to sign OSS URLs in data before returning to frontend.
    
    Handles Pydantic models, lists of models, and dicts.
    Returns a JSONResponse with signed URLs.
    """
    if data is None:
        return JSONResponse(content=None)
    
    # Convert Pydantic models to dict
    if hasattr(data, "model_dump"):
        processed_data = data.model_dump()
    elif isinstance(data, list):
        processed_data = [item.model_dump() if hasattr(item, "model_dump") else item for item in data]
    else:
        processed_data = data
    
    # Check if OSS is configured
    uploader = OSSImageUploader()
    if uploader.is_configured:
        # OSS mode: sign URLs in the data
        processed_data = sign_oss_urls_in_data(processed_data, uploader)
    
    # Return JSONResponse directly to avoid Pydantic re-validation stripping fields
    return JSONResponse(content=processed_data)


# ============================================================
# Shared Request Models (used by both Project and Series endpoints)
# ============================================================

class GenerateAssetRequest(BaseModel):
    asset_id: str
    asset_type: str
    style_preset: str = "Cinematic"
    reference_image_url: Optional[str] = None
    style_prompt: Optional[str] = None
    generation_type: str = "all"  # 'full_body', 'three_view', 'headshot', 'all', 'reference_sheet'
    prompt: Optional[str] = None
    apply_style: bool = True
    negative_prompt: Optional[str] = None
    batch_size: int = 1
    model_name: Optional[str] = None
    aspect_ratio: Optional[str] = None

class ToggleLockRequest(BaseModel):
    asset_id: str
    asset_type: str

class UpdateAssetImageRequest(BaseModel):
    asset_id: str
    asset_type: str
    image_url: str

class UpdateAssetAttributesRequest(BaseModel):
    asset_id: str
    asset_type: str
    attributes: Dict[str, Any]


@app.get("/health")
def health_check():
    """Lightweight liveness probe used by the Diagnose UI on stuck
    tasks and by external uptime checks. Intentionally cheap: no DB
    hit, no provider call, just a 200 + a few facts the frontend can
    show next to the spinner ("backend reachable, log file at X")."""
    from ...utils import get_log_dir
    log_dir = get_log_dir()
    log_file = os.path.join(log_dir, "app.log")
    return {
        "ok": True,
        "time": time.time(),
        "log_file": log_file,
        "log_dir": log_dir,
        "studio_projects": len(getattr(pipeline, "scripts", {})),
    }


@app.get("/diagnose/log_tail")
def diagnose_log_tail(lines: int = 200):
    """Return the last N lines of the app log + an ERROR/Exception
    summary so the Diagnose UI on stuck tasks can show actual log
    content instead of just a path. Read-only, capped to 1000 lines so
    a runaway client can't hose the process."""
    from ...utils import get_log_dir
    capped = max(1, min(int(lines or 200), 1000))
    log_path = os.path.join(get_log_dir(), "app.log")
    if not os.path.exists(log_path):
        return {"path": log_path, "lines": [], "errors": [], "missing": True}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # Read all, then tail. App.log is bounded by the rotating
            # handler (≤ 5 MB active) so this is safe.
            all_lines = f.readlines()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read log: {exc}")
    tail = all_lines[-capped:] if len(all_lines) > capped else all_lines
    error_keywords = ("ERROR", "Exception", "Failed", "Unauthorized", "Traceback")
    errors = [
        ln.rstrip("\n")
        for ln in tail
        if any(k in ln for k in error_keywords)
    ][-30:]
    return {
        "path": log_path,
        "total_lines": len(all_lines),
        "returned_lines": len(tail),
        "lines": [ln.rstrip("\n") for ln in tail],
        "errors": errors,
        "missing": False,
    }


@app.get("/system/check")
def check_system():
    """Check system dependencies (ffmpeg, etc.) and configuration."""
    from ...utils.system_check import run_system_checks
    return run_system_checks()





@app.post("/upload")
def upload_file(file: UploadFile = File(...)):
    """Uploads a file and returns its URL (OSS if configured, else local)."""
    try:
        file_ext = _safe_upload_ext(file.filename)
        filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join("output/uploads", filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Try uploading to OSS
        oss_url = OSSImageUploader().upload_image(file_path)
        if oss_url:
            return signed_response({"url": oss_url})

        # Fallback to local URL (relative path for frontend getAssetUrl)
        return {"url": f"uploads/{filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UploadAssetRequest(BaseModel):
    upload_type: str  # "full_body" | "head_shot" | "three_views" | "image"
    description: Optional[str] = None  # User-modified description for reverse generation


@app.post("/projects/{script_id}/assets/{asset_type}/{asset_id}/upload")
def upload_asset(
    script_id: str,
    asset_type: str,
    asset_id: str,
    upload_type: str,
    description: Optional[str] = None,
    file: UploadFile = File(...)
):
    """
    Uploads an image as a new variant for an asset.
    The uploaded image is marked as the 'upload source' for reverse generation.
    
    - asset_type: "character", "scene", or "prop"
    - upload_type: "full_body", "head_shot", "three_views", or "image" (for scene/prop)
    - description: Optional modified description for the asset
    """
    try:
        # 1. Save file locally first
        file_ext = _safe_upload_ext(file.filename)
        filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join("output/uploads", filename)
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # 2. Upload to OSS
        uploader = OSSImageUploader()
        oss_url = uploader.upload_image(file_path)
        if not oss_url:
            oss_url = f"uploads/{filename}"  # Fallback to local path
        
        # 3. Update asset with new variant
        updated_script = pipeline.add_uploaded_asset_variant(
            script_id=script_id,
            asset_type=asset_type,
            asset_id=asset_id,
            upload_type=upload_type,
            image_url=oss_url,
            description=description
        )
        
        if not updated_script:
            raise HTTPException(status_code=404, detail="Script or asset not found")
        
        return signed_response(updated_script)
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error uploading asset: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CreateProjectRequest(BaseModel):
    title: str
    text: str
    workflow_mode: str = "r2v"  # "r2v" (default) or "i2v_legacy"
    # Optional series binding (T9). When set, the new project is created as
    # the next episode of this series (episode_number = current max + 1).
    # Omit for a standalone project — behavior unchanged.
    series_id: Optional[str] = None


@app.post("/projects", response_model=Script)
async def create_project(request: CreateProjectRequest, skip_analysis: bool = False):
    """Creates a new project from a novel text.

    When `series_id` is provided the project is bound as the next episode
    of that series; omitting it keeps the standalone-project behavior
    unchanged.
    """
    # Run in thread pool to avoid blocking event loop during LLM analysis (Python 3.8 compatible)
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,  # Use default executor
            partial(pipeline.create_project, request.title, request.text, skip_analysis, request.workflow_mode, request.series_id)
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return signed_response(result)



class ReparseProjectRequest(BaseModel):
    text: str


class UpdateScriptTextRequest(BaseModel):
    text: str


@app.put("/projects/{script_id}/text", response_model=Script)
def update_script_text(script_id: str, request: UpdateScriptTextRequest):
    """Persist `original_text` without re-parsing entities.

    Used by ScriptProcessor's onBlur so typing survives reload/navigation
    without triggering an LLM round-trip. Heavy reparse stays bound to the
    explicit "提取实体" CTA.
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    script.original_text = request.text or ""
    script.updated_at = time.time()
    pipeline._save_data()
    return signed_response(script)


@app.put("/projects/{script_id}/reparse", response_model=Script)
async def reparse_project(script_id: str, request: ReparseProjectRequest):
    """Re-parses the text for an existing project, replacing all entities."""
    try:
        # Run the blocking LLM call in a thread pool to avoid blocking the event loop (Python 3.8 compatible)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,  # Use default executor
            partial(pipeline.reparse_project, script_id, request.text)
        )
        return signed_response(result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/projects/{script_id}/extract_preview")
async def extract_preview(script_id: str, request: ReparseProjectRequest):
    """Dry-run entity extraction — returns entities without saving."""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(pipeline.extract_preview, script_id, request.text)
        )
        return {
            "characters": [c.dict() for c in result.characters],
            "scenes": [s.dict() for s in result.scenes],
            "props": [p.dict() for p in result.props],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/projects/", response_model=List[dict])
def list_projects():
    """Lists all projects from backend storage."""
    scripts = list(pipeline.scripts.values())
    return signed_response(scripts)


@app.post("/projects/{script_id}/toggle_starred")
def toggle_project_starred(script_id: str):
    """Toggle the user-starred (featured shortlist) flag on a project."""
    try:
        script = pipeline.toggle_project_starred(script_id)
        return signed_response(script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Series CRUD
# ============================================================

class CreateSeriesRequest(BaseModel):
    title: str
    description: str = ""
    workflow_mode: str = "r2v"
    # R2V v2 Phase 6 — content mode (scripted | freeform), defaults
    # scripted (traditional script-first flow).
    content_mode: str = "scripted"
    # PR-3e (r2v-workflow-v3) — Visual control preference.
    # 'r2v' (节奏优先, default) | 'i2v' (画面优先).
    default_generation_mode: str = "r2v"


class UpdateSeriesRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    workflow_mode: Optional[str] = None
    content_mode: Optional[str] = None
    default_generation_mode: Optional[str] = None
    # R2V v2: series-level art_direction baseline (Phase 2). Inherits +
    # override flows at the episode level read this as the source of truth.
    art_direction: Optional[ArtDirection] = None


@app.post("/series")
def create_series(request: CreateSeriesRequest):
    """Create a new Series."""
    series = pipeline.create_series(
        request.title,
        request.description,
        request.workflow_mode,
        request.content_mode,
        request.default_generation_mode,
    )
    return signed_response(series)


@app.get("/series")
def list_series():
    """List all Series."""
    series_list = pipeline.list_series()
    return signed_response(series_list)


@app.get("/series/{series_id}")
def get_series(series_id: str):
    """Get Series details including assets and episode list."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    # Include episode summaries
    episodes = pipeline.get_series_episodes(series_id)
    result = series.model_dump()
    result["episodes"] = [
        {
            "id": ep.id,
            "title": ep.title,
            "episode_number": ep.episode_number,
            "created_at": ep.created_at,
            "updated_at": ep.updated_at,
        }
        for ep in episodes
    ]
    return signed_response(result)


@app.put("/series/{series_id}")
def update_series(series_id: str, request: UpdateSeriesRequest):
    """Update Series fields. Uses `exclude_unset=True` so explicitly-null
    values (e.g. `{"art_direction": null}` to clear baseline) are honored,
    while fields the client didn't send remain untouched."""
    try:
        updates = request.model_dump(exclude_unset=True)
        series = pipeline.update_series(series_id, updates)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/series/{series_id}")
def delete_series(series_id: str):
    """Delete a Series and disassociate its episodes."""
    try:
        pipeline.delete_series(series_id)
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AddEpisodeRequest(BaseModel):
    script_id: str
    episode_number: Optional[int] = None


@app.post("/series/{series_id}/episodes")
def add_episode_to_series(series_id: str, request: AddEpisodeRequest):
    """Add an existing project as an episode to a Series."""
    try:
        series = pipeline.add_episode_to_series(series_id, request.script_id, request.episode_number)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/series/{series_id}/episodes/{script_id}")
def remove_episode_from_series(series_id: str, script_id: str):
    """Remove an episode from a Series (does not delete the project)."""
    try:
        series = pipeline.remove_episode_from_series(series_id, script_id)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/series/{series_id}/episodes")
def get_series_episodes(series_id: str):
    """Get all episodes in a Series."""
    try:
        episodes = pipeline.get_series_episodes(series_id)
        return signed_response(episodes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/series/{series_id}/prompt_config")
def get_series_prompt_config(series_id: str):
    """Get Series prompt config with system defaults."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return {
        "prompt_config": series.prompt_config.model_dump(),
        "defaults": {
            "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
            "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
            "r2v_polish": DEFAULT_R2V_POLISH_PROMPT,
            "storyboard_extraction": DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
        },
    }


@app.put("/series/{series_id}/prompt_config")
def update_series_prompt_config(series_id: str, config: PromptConfig):
    """Update Series-level prompt config."""
    try:
        series = pipeline.update_series(series_id, {"prompt_config": config})
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# Series Model Settings
# ============================================================

class UpdateModelSettingsRequest(BaseModel):
    t2i_model: Optional[str] = None
    i2i_model: Optional[str] = None
    image_model: Optional[str] = None
    i2v_model: Optional[str] = None
    r2v_model: Optional[str] = None
    character_aspect_ratio: Optional[str] = None
    scene_aspect_ratio: Optional[str] = None
    prop_aspect_ratio: Optional[str] = None
    storyboard_aspect_ratio: Optional[str] = None

@app.get("/series/{series_id}/model_settings")
def get_series_model_settings(series_id: str):
    """Get Series model settings."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return series.model_settings.model_dump()


@app.put("/series/{series_id}/model_settings")
def update_series_model_settings(series_id: str, settings: UpdateModelSettingsRequest):
    """Update Series-level model settings."""
    updates = {k: v for k, v in settings.model_dump().items() if v is not None}
    if not updates:
        series = pipeline.get_series(series_id)
        if not series:
            raise HTTPException(status_code=404, detail="Series not found")
        return signed_response(series)
    try:
        current_series = pipeline.get_series(series_id)
        if not current_series:
            raise HTTPException(status_code=404, detail="Series not found")
        ms = current_series.model_settings.model_copy(update=updates)
        series = pipeline.update_series(series_id, {"model_settings": ms})
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================
# Series Asset Operations
# ============================================================

@app.get("/series/{series_id}/assets")
def get_series_assets(series_id: str):
    """Get all shared assets from a Series."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return signed_response({
        "characters": [c.model_dump() for c in series.characters],
        "scenes": [s.model_dump() for s in series.scenes],
        "props": [p.model_dump() for p in series.props],
    })


@app.post("/series/{series_id}/assets/generate")
def generate_series_asset(series_id: str, request: GenerateAssetRequest, background_tasks: BackgroundTasks):
    """Generate a single asset for a Series (async)."""
    try:
        series, task_id = pipeline.generate_series_asset(
            series_id,
            request.asset_id,
            request.asset_type,
            request.style_preset,
            request.reference_image_url,
            request.style_prompt,
            request.generation_type,
            request.prompt,
            request.apply_style,
            request.negative_prompt,
            request.batch_size,
            request.model_name
        )
        background_tasks.add_task(pipeline.process_asset_generation_task, task_id)
        response_data = series.dict()
        response_data["_task_id"] = task_id
        return signed_response(response_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/toggle_lock")
def toggle_series_asset_lock(series_id: str, request: ToggleLockRequest):
    """Toggle the locked status of a Series asset."""
    try:
        series = pipeline.toggle_series_asset_lock(series_id, request.asset_id, request.asset_type)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/toggle_starred")
def toggle_series_asset_starred(series_id: str, request: ToggleLockRequest):
    """Toggle the starred (library shortlist) status of a Series asset."""
    try:
        series = pipeline.toggle_series_asset_starred(series_id, request.asset_id, request.asset_type)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/update_image")
def update_series_asset_image(series_id: str, request: UpdateAssetImageRequest):
    """Update a Series asset's image URL."""
    try:
        series = pipeline.update_series_asset_image(series_id, request.asset_id, request.asset_type, request.image_url)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/update_attributes")
def update_series_asset_attributes(series_id: str, request: UpdateAssetAttributesRequest):
    """Update arbitrary attributes of a Series asset."""
    try:
        series = pipeline.update_series_asset_attributes(
            series_id, request.asset_id, request.asset_type, request.attributes
        )
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ImportAssetsRequest(BaseModel):
    source_series_id: str
    asset_ids: List[str]


# R2V v2 Phase 5 — quick-create CRUD for series-shared assets.
# Used by Cast step's "+ 新角色 / 新场景 / 新道具" modal. Optional
# `image_url` lets the user attach a pre-uploaded master sheet so the
# new asset isn't blank.
class CreateSeriesAssetRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    persona: Optional[str] = ""        # characters only — grouping label
    image_url: Optional[str] = None    # optional uploaded master sheet
    voice_id: Optional[str] = None     # characters only — TTS voice binding


def _new_id(prefix: str) -> str:
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@app.post("/series/{series_id}/characters")
def create_series_character(series_id: str, request: CreateSeriesAssetRequest):
    """Create a new Character at series scope."""
    from .models import Character, AssetUnit, ImageVariant
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    char_id = _new_id("char")
    ref_sheet = AssetUnit()
    if request.image_url:
        variant = ImageVariant(id=_new_id("img"), url=request.image_url)
        ref_sheet.image_variants.append(variant)
        ref_sheet.selected_image_id = variant.id
    char = Character(
        id=char_id,
        name=request.name,
        description=request.description or "",
        persona=request.persona or "",
        voice_id=request.voice_id,
        reference_sheet=ref_sheet,
    )
    series.characters.append(char)
    series.updated_at = time.time()
    pipeline.series_store[series_id] = series
    pipeline._save_series_data()
    return signed_response(char.model_dump())


@app.post("/series/{series_id}/scenes")
def create_series_scene(series_id: str, request: CreateSeriesAssetRequest):
    from .models import Scene
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    sid = _new_id("scene")
    scene = Scene(
        id=sid,
        name=request.name,
        description=request.description or "",
        image_url=request.image_url,
    )
    series.scenes.append(scene)
    series.updated_at = time.time()
    pipeline.series_store[series_id] = series
    pipeline._save_series_data()
    return signed_response(scene.model_dump())


@app.post("/series/{series_id}/props")
def create_series_prop(series_id: str, request: CreateSeriesAssetRequest):
    from .models import Prop
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    pid = _new_id("prop")
    prop = Prop(
        id=pid,
        name=request.name,
        description=request.description or "",
        image_url=request.image_url,
    )
    series.props.append(prop)
    series.updated_at = time.time()
    pipeline.series_store[series_id] = series
    pipeline._save_series_data()
    return signed_response(prop.model_dump())


@app.post("/series/{series_id}/assets/import")
def import_series_assets(series_id: str, request: ImportAssetsRequest):
    """Deep-copy assets from another Series into this one."""
    try:
        series, imported_ids, skipped_ids = pipeline.import_assets_from_series(series_id, request.source_series_id, request.asset_ids)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Global Asset Library (project-independent shared pool) — CRUD + promote
# ============================================================
# Mirrors the /series/{id}/assets endpoints, one level up: a curated,
# project-independent pool any project can reference. All mutations route
# through the ComicGenPipeline.*_library_asset methods so the Playground
# "录入资产库" flow stays consistent with these endpoints.

class CreateLibraryAssetRequest(BaseModel):
    asset_type: str                    # "character" | "scene" | "prop"
    name: str
    description: Optional[str] = ""
    persona: Optional[str] = ""        # characters only — grouping label
    image_url: Optional[str] = None    # optional pre-uploaded master image
    voice_id: Optional[str] = None     # characters only — TTS voice binding


class UpdateLibraryAssetRequest(BaseModel):
    # Generic patch — only the fields the client actually sends are applied
    # (PATCH semantics; PUT here is lenient/partial).
    name: Optional[str] = None
    description: Optional[str] = None
    persona: Optional[str] = None
    image_url: Optional[str] = None
    voice_id: Optional[str] = None
    starred: Optional[bool] = None
    locked: Optional[bool] = None
    visual_weight: Optional[int] = None


class PromoteAssetRequest(BaseModel):
    source_kind: str   # "project" | "series"
    source_id: str
    asset_type: str    # "character" | "scene" | "prop"
    asset_id: str


class ForkFromLibraryRequest(BaseModel):
    asset_type: str          # "character" | "scene" | "prop"
    library_asset_id: str    # id of the source asset in the global library


@app.get("/library/assets")
def get_library_assets():
    """List all assets in the global shared pool."""
    lib = pipeline.list_library_assets()
    return signed_response({
        "characters": [c.model_dump() for c in lib.characters],
        "scenes": [s.model_dump() for s in lib.scenes],
        "props": [p.model_dump() for p in lib.props],
    })


@app.post("/library/assets")
def create_library_asset(request: CreateLibraryAssetRequest):
    """Create a new asset in the global shared pool."""
    try:
        payload = request.model_dump(exclude={"asset_type"})
        asset = pipeline.create_library_asset(request.asset_type, payload)
        return signed_response(asset.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/library/assets/upload")
def upload_library_asset_image(file: UploadFile = File(...)):
    """Upload an image to use as a global library asset's master image.

    Saves the file under output/uploads/ (served via the /files static mount)
    and returns {"image_url": <path-or-URL the frontend can load>}. When OSS
    is configured the returned URL is the (signed) OSS URL; otherwise a local
    relative path "uploads/<name>" resolvable through the frontend's
    getAssetUrl helper. The caller then passes this image_url to
    POST /library/assets (image_url=...) or PATCH /library/assets/{type}/{id}
    to attach it to a library asset. Mirrors the generic /upload endpoint but
    returns the {image_url} contract the library UI expects.
    """
    try:
        file_ext = _safe_upload_ext(file.filename)
        filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join("output/uploads", filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        # Prefer OSS when configured (signed), else fall back to local path.
        oss_url = OSSImageUploader().upload_image(file_path)
        if oss_url:
            return signed_response({"image_url": oss_url})
        return {"image_url": f"uploads/{filename}"}
    except Exception as e:
        logger.exception("upload_library_asset_image failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.api_route("/library/assets/{asset_type}/{asset_id}", methods=["PUT", "PATCH"])
def update_library_asset(asset_type: str, asset_id: str, request: UpdateLibraryAssetRequest):
    """Patch a global library asset (only the provided fields are applied)."""
    try:
        patch = request.model_dump(exclude_unset=True)
        asset = pipeline.update_library_asset(asset_type, asset_id, patch)
        return signed_response(asset.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/library/assets/{asset_type}/{asset_id}")
def delete_library_asset(asset_type: str, asset_id: str, force: bool = False):
    """Delete an asset from the global shared pool.

    Reference-integrity (design Q2): if any storyboard frame in any project or
    series still references this asset (via scene_id / character_ids /
    prop_ids), the delete is refused with HTTP 409 and the referrers are
    listed — unless ``force=true`` is passed, which deletes anyway and leaves
    those references dangling (the asset resolver simply drops the unknown id).
    """
    try:
        pipeline.delete_library_asset(asset_type, asset_id, force=force)
        return {"status": "deleted", "asset_type": asset_type, "id": asset_id}
    except LibraryAssetInUseError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "library_asset_in_use",
                "message": str(e),
                "asset_type": e.asset_type,
                "asset_id": e.asset_id,
                "references": e.references,
                "hint": "Pass ?force=true to delete anyway.",
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/library/assets/promote")
def promote_asset_to_library(request: PromoteAssetRequest):
    """Deep-copy an asset from a project or series into the global pool."""
    try:
        asset = pipeline.promote_asset_to_library(
            request.source_kind, request.source_id, request.asset_type, request.asset_id
        )
        return signed_response(asset.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/fork_from_library")
def fork_asset_from_library(script_id: str, request: ForkFromLibraryRequest):
    """Fork (deep-copy) a global library asset into this project as an
    independent, editable local copy with a fresh id (design Q3, 按需 fork).

    Under live-reference (D1 活引用) semantics a project references shared
    library assets directly; this endpoint materializes a project-owned copy
    so subsequent edits no longer affect the shared original. Returns the new
    project-local asset.
    """
    try:
        asset = pipeline.fork_library_asset_to_project(
            script_id, request.asset_type, request.library_asset_id
        )
        return signed_response(asset.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# File Import & Episode Splitting
# ============================================================

@app.post("/series/import/preview")
async def import_file_preview(
    file: UploadFile = File(...),
    suggested_episodes: int = 3,
):
    """Upload a txt/md file and get LLM episode split preview."""
    if suggested_episodes < 1 or suggested_episodes > 50:
        raise HTTPException(status_code=400, detail="建议集数应在 1-50 之间")
    try:
        content_bytes = await file.read()
        text = content_bytes.decode("utf-8")
        if not text.strip():
            raise HTTPException(status_code=400, detail="文件内容为空")

        loop = asyncio.get_event_loop()
        episodes = await loop.run_in_executor(
            None,
            partial(pipeline.import_file_and_split, text, suggested_episodes)
        )
        # Store text in pipeline cache, return import_id instead of full text
        import_id = str(uuid.uuid4())
        pipeline._import_cache[import_id] = text
        return {
            "filename": file.filename,
            "text_length": len(text),
            "suggested_episodes": suggested_episodes,
            "episodes": episodes,
            "import_id": import_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("File import preview failed")
        raise HTTPException(status_code=500, detail=str(e))


class ConfirmImportRequest(BaseModel):
    title: str
    description: str = ""
    import_id: str = ""
    text: Optional[str] = None
    episodes: List[Dict[str, Any]]  # episode_number, title, start_marker, end_marker, ...


@app.post("/series/import/confirm")
async def import_file_confirm(request: ConfirmImportRequest):
    """Confirm the episode split and create Series + Episodes."""
    try:
        # Prefer import_id from cache, fallback to request.text
        text = None
        if request.import_id:
            text = pipeline._import_cache.pop(request.import_id, None)
        if not text:
            text = request.text
        if not text:
            raise ValueError("No text available. Provide import_id or text.")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(
                pipeline.create_series_from_import,
                request.title,
                text,
                request.episodes,
                request.description,
            )
        )
        return signed_response(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Import confirm failed")
        raise HTTPException(status_code=500, detail=str(e))


class EnvConfig(ProviderRoutingConfig):
    DASHSCOPE_API_KEY: Optional[str] = None
    ALIBABA_CLOUD_ACCESS_KEY_ID: Optional[str] = None
    ALIBABA_CLOUD_ACCESS_KEY_SECRET: Optional[str] = None
    OSS_BUCKET_NAME: Optional[str] = None
    OSS_ENDPOINT: Optional[str] = None
    OSS_BASE_PATH: Optional[str] = None
    OSS_ENABLE: bool = True
    KLING_ACCESS_KEY: Optional[str] = None
    KLING_SECRET_KEY: Optional[str] = None
    VIDU_API_KEY: Optional[str] = None
    MULEROUTER_API_KEY: Optional[str] = None
    endpoint_overrides: Dict[str, str] = Field(default_factory=dict)


def _normalize_provider_mode(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized in (ProviderBackend.DASHSCOPE.value, ProviderBackend.VENDOR.value):
        return normalized
    return ProviderBackend.DASHSCOPE.value


def get_user_config_path() -> str:
    """
    Returns the path to the user config file.
    - Development mode: Uses .env in project root
    - Packaged app mode: Uses ~/.lumen-x/config.json
    """
    from ...utils import get_user_data_dir
    
    # Check if running in packaged mode (e.g., via environment variable or frozen check)
    is_packaged = os.getenv("LUMEN_X_PACKAGED", "false").lower() == "true" or getattr(sys, 'frozen', False)
    
    if is_packaged:
        # Use user home directory for packaged app
        config_dir = get_user_data_dir()
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, "config.json")
    else:
        # Use .env in project root for development
        # Get absolute path to project root (api.py is in src/apps/comic_gen/)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return os.path.join(project_root, ".env")



def load_user_config():
    """Loads user config from file and applies to environment."""
    config_path = get_user_config_path()
    
    if config_path.endswith(".json"):
        # JSON config for packaged app
        if os.path.exists(config_path):
            try:
                import json
                with open(config_path, "r") as f:
                    config = json.load(f)
                for key, value in config.items():
                    if value:
                        os.environ[key] = value
            except Exception as e:
                logger.warning(f"Failed to load config from {config_path}: {e}")
    # .env is already loaded at startup via dotenv


def save_user_config(config_dict: dict):
    """Saves user config to file."""
    config_path = get_user_config_path()

    if config_path.endswith(".json"):
        # JSON config for packaged app
        import json
        existing_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    existing_config = json.load(f)
            except:
                pass
        existing_config.update(config_dict)
        with open(config_path, "w") as f:
            json.dump(existing_config, f, indent=2)
    else:
        # .env for development
        for key, value in config_dict.items():
            if value is not None:
                set_key(config_path, key, value)


def remove_user_config_keys(keys: list):
    """Removes keys from the persisted config file."""
    if not keys:
        return
    config_path = get_user_config_path()

    if config_path.endswith(".json"):
        import json
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    existing_config = json.load(f)
                for key in keys:
                    existing_config.pop(key, None)
                with open(config_path, "w") as f:
                    json.dump(existing_config, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to remove keys from config: {e}")
    else:
        from dotenv import unset_key
        for key in keys:
            try:
                unset_key(config_path, key)
            except Exception as e:
                logger.warning(f"Failed to unset key {key} from .env: {e}")


# Load user config on startup
import sys
load_user_config()



@app.get("/config/info")
def get_config_info():
    """Returns information about the current config storage mode."""
    config_path = get_user_config_path()
    is_packaged = os.getenv("LUMEN_X_PACKAGED", "false").lower() == "true" or getattr(sys, 'frozen', False)
    return {
        "mode": "packaged" if is_packaged else "development",
        "config_path": config_path,
        "config_exists": os.path.exists(config_path)
    }


@app.post("/config/env")
def update_env_config(config: EnvConfig):
    """Updates environment configuration and saves to config file."""
    try:
        raw_config = config.dict(exclude_unset=True)

        # Extract endpoint_overrides and flatten into config_dict
        endpoint_overrides = raw_config.pop("endpoint_overrides", {})

        # Filter out None values and serialize enum values as plain strings.
        config_dict: Dict[str, str] = {}
        for key, value in raw_config.items():
            if value is None:
                continue
            if isinstance(value, bool):
                # Booleans (e.g. OSS_ENABLE) persist as "true"/"false" strings so
                # they round-trip through os.environ and the .env/config.json store.
                config_dict[key] = "true" if value else "false"
            elif isinstance(value, ProviderBackend):
                config_dict[key] = value.value
            else:
                config_dict[key] = value

        # Secret masking guard: GET /config/env returns secrets masked with the
        # bullet sentinel. If the frontend re-submits an unchanged secret it will
        # still contain bullets — skip it so we don't overwrite the real stored
        # key with the mask. Only genuinely edited (bullet-free) values persist.
        for field in list(config_dict.keys()):
            if field in SECRET_FIELDS and _MASK_CHAR in str(config_dict[field]):
                config_dict.pop(field, None)

        # Process endpoint overrides: validate keys against known providers
        from ...utils.endpoints import PROVIDER_DEFAULTS
        allowed_keys = {f"{p}_BASE_URL" for p in PROVIDER_DEFAULTS}
        keys_to_remove = []
        for env_key, value in endpoint_overrides.items():
            if env_key not in allowed_keys:
                logger.warning(f"Ignoring unknown endpoint key: {env_key}")
                continue
            if value and value.strip():
                config_dict[env_key] = value.strip()
            else:
                # Clear override: remove from env and config file
                os.environ.pop(env_key, None)
                keys_to_remove.append(env_key)

        # Update current process env
        for key, value in config_dict.items():
            os.environ[key] = value

        # Save to file
        save_user_config(config_dict)
        remove_user_config_keys(keys_to_remove)

        # Reset OSS singleton to pick up new config (non-blocking)
        try:
            OSSImageUploader.reset_instance()
            logger.info("OSS instance reset successfully")
        except Exception as oss_e:
            # OSS reset failure should not block config saving
            logger.warning(f"OSS reset failed (non-critical): {oss_e}")

        config_path = get_user_config_path()
        return {"status": "success", "message": f"Configuration saved to {config_path}"}
    except Exception as e:
        logger.exception("Failed to save environment configuration")
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/projects/{script_id}")
def get_project(script_id: str):
    """Retrieves a project by ID. When the project belongs to a
    Series, the response merges series-shared characters / scenes /
    props on top of the episode-local lists. Each item carries a
    `source` field ("episode" | "series" | "global") so the frontend can
    visually distinguish where the asset lives and route writes
    appropriately (per A2 design decision — shared writes default to
    the series side; local writes stay episode-side; the helper
    `_find_asset_with_source` in pipeline routes mutations correctly).

    Response model dropped from `Script` because the `source` field
    is a presentation-layer concern (never persisted, derived from
    container membership at read time)."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")

    payload = script.model_dump()

    # Episode-local entries always carry source="episode".
    for asset_list in (payload.get("characters", []),
                      payload.get("scenes", []),
                      payload.get("props", [])):
        for item in asset_list:
            item["source"] = "episode"

    # Merge series-shared assets on top (any id not already present
    # locally — episode-local overrides series). Without this step
    # the user "loses" characters when switching between episodes of
    # the same series, because the series shared pool isn't
    # reflected on each episode's response.
    if script.series_id:
        series = pipeline.get_series(script.series_id)
        if series:
            ep_char_ids = {c.id for c in script.characters}
            ep_scene_ids = {s.id for s in script.scenes}
            ep_prop_ids = {p.id for p in script.props}
            for ch in series.characters:
                if ch.id not in ep_char_ids:
                    d = ch.model_dump()
                    d["source"] = "series"
                    payload["characters"].append(d)
            for sc in series.scenes:
                if sc.id not in ep_scene_ids:
                    d = sc.model_dump()
                    d["source"] = "series"
                    payload["scenes"].append(d)
            for pr in series.props:
                if pr.id not in ep_prop_ids:
                    d = pr.model_dump()
                    d["source"] = "series"
                    payload["props"].append(d)

    # Merge the project-independent global asset library underneath as
    # the lowest layer. Any id not already present from the episode or
    # series layers is appended with source="global" (read-time only —
    # never written back to projects.json). When the library is empty
    # this is a no-op and the response is byte-identical to before.
    lib = pipeline.library_store
    if lib.characters or lib.scenes or lib.props:
        seen_char_ids = {c["id"] for c in payload["characters"]}
        seen_scene_ids = {s["id"] for s in payload["scenes"]}
        seen_prop_ids = {p["id"] for p in payload["props"]}
        for ch in lib.characters:
            if ch.id not in seen_char_ids:
                d = ch.model_dump()
                d["source"] = "global"
                payload["characters"].append(d)
        for sc in lib.scenes:
            if sc.id not in seen_scene_ids:
                d = sc.model_dump()
                d["source"] = "global"
                payload["scenes"].append(d)
        for pr in lib.props:
            if pr.id not in seen_prop_ids:
                d = pr.model_dump()
                d["source"] = "global"
                payload["props"].append(d)
    return signed_response(payload)



@app.delete("/projects/{script_id}")
def delete_project(script_id: str):
    """Deletes a project by ID. WARNING: This permanently removes the project from backend storage."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    
    try:
        # If project belongs to a Series, remove from episode_ids
        if script.series_id:
            series = pipeline.get_series(script.series_id)
            if series and script_id in series.episode_ids:
                series.episode_ids.remove(script_id)
                pipeline._save_series_data()

        # Remove from pipeline scripts
        del pipeline.scripts[script_id]
        pipeline._save_data()
        return {"status": "deleted", "id": script_id, "title": script.title}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────
# R2V v2 Phase 4 — Cross-episode asset reconcile
# ─────────────────────────────────────────────────────────────────────
def _name_match_confidence(local_name: str, series_name: str) -> int:
    """0-100 simple confidence score.
    Phase 4 v1: exact match = 100, substring match = 75, none = 0.
    Future: embedding-based semantic similarity on `description`."""
    a, b = (local_name or "").strip().lower(), (series_name or "").strip().lower()
    if not a or not b:
        return 0
    if a == b:
        return 100
    if a in b or b in a:
        return 75
    return 0


@app.get("/projects/{script_id}/reconcile/suggestions")
def reconcile_suggestions(script_id: str):
    """Compute match suggestions for the current episode's just-extracted
    entities vs the parent series's shared asset library.

    Returned shape:
    {
      "characters": [
        { local_id, local_name, suggested_series_id|null, suggested_series_name|null, confidence }
      ],
      "scenes": [...],
      "props": [...],
    }
    Frontend uses this to render the ReconcileModal after script parse.
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        # Standalone projects have no series library to reconcile against
        return {"characters": [], "scenes": [], "props": []}
    series = pipeline.get_series(script.series_id)
    if not series:
        return {"characters": [], "scenes": [], "props": []}

    def best_match(local_name: str, pool: list, key: str = "name"):
        best_id, best_name, best_conf = None, None, 0
        for item in pool:
            sname = getattr(item, key, "") if hasattr(item, key) else item.get(key, "")
            conf = _name_match_confidence(local_name, sname)
            if conf > best_conf:
                best_id, best_name, best_conf = item.id if hasattr(item, "id") else item.get("id"), sname, conf
        return best_id, best_name, best_conf

    def build(local_pool, series_pool):
        result = []
        for local in local_pool:
            sid, sname, conf = best_match(local.name, series_pool)
            result.append({
                "local_id": local.id,
                "local_name": local.name,
                "suggested_series_id": sid if conf > 0 else None,
                "suggested_series_name": sname if conf > 0 else None,
                "confidence": conf,
            })
        return result

    return {
        "characters": build(script.characters, series.characters),
        "scenes": build(script.scenes, series.scenes),
        "props": build(script.props, series.props),
    }


class ReconcileAction(BaseModel):
    local_id: str
    action: str  # "merge_into_series" | "create_new_in_series" | "skip"
    target_series_id: Optional[str] = None


class ApplyReconcileRequest(BaseModel):
    characters: List[ReconcileAction] = Field(default_factory=list)
    scenes: List[ReconcileAction] = Field(default_factory=list)
    props: List[ReconcileAction] = Field(default_factory=list)


@app.post("/projects/{script_id}/reconcile/apply")
def reconcile_apply(script_id: str, request: ApplyReconcileRequest):
    """Apply user-confirmed reconcile decisions.
    - merge_into_series: drop the local episode entity, replace all
      frame references with target_series_id (the series-shared asset).
    - create_new_in_series: promote the local entity to series scope.
    - skip: no-op (keep local-only).
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        raise HTTPException(status_code=400, detail="Project not in a series")
    series = pipeline.get_series(script.series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")

    def apply_list(local_pool_attr: str, series_pool_attr: str, actions: list, frame_ref_attr: Optional[str] = None):
        local_pool = getattr(script, local_pool_attr)
        series_pool = getattr(series, series_pool_attr)
        for act in actions:
            local_item = next((x for x in local_pool if x.id == act.local_id), None)
            if not local_item:
                continue
            if act.action == "create_new_in_series":
                # Promote: copy to series pool, drop from local
                series_pool.append(local_item)
                local_pool.remove(local_item)
            elif act.action == "merge_into_series" and act.target_series_id:
                # Rewire frame references then drop local
                if frame_ref_attr:
                    for frame in script.frames:
                        if frame_ref_attr == "scene_id":
                            if frame.scene_id == local_item.id:
                                frame.scene_id = act.target_series_id
                        elif frame_ref_attr == "character_ids":
                            frame.character_ids = [
                                act.target_series_id if cid == local_item.id else cid
                                for cid in frame.character_ids
                            ]
                        elif frame_ref_attr == "prop_ids":
                            frame.prop_ids = [
                                act.target_series_id if pid == local_item.id else pid
                                for pid in frame.prop_ids
                            ]
                local_pool.remove(local_item)
            # skip: do nothing
        setattr(script, local_pool_attr, local_pool)
        setattr(series, series_pool_attr, series_pool)

    apply_list("characters", "characters", request.characters, "character_ids")
    apply_list("scenes", "scenes", request.scenes, "scene_id")
    apply_list("props", "props", request.props, "prop_ids")

    script.updated_at = time.time()
    series.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline.series_store[series.id] = series
    pipeline._save_data()
    pipeline._save_series_data()
    return signed_response(script)


# ─────────────────────────────────────────────────────────────────────
# R2V v2 Phase 3 — "Previously on..." (上回书说到)
# ─────────────────────────────────────────────────────────────────────
def _prev_text_revision(prev_text: str) -> str:
    """Cheap revision marker for cache invalidation.
    Hash + length is sufficient — collisions don't matter, we only
    detect "did the script change since cache was built"."""
    import hashlib
    h = hashlib.md5(prev_text.encode("utf-8")).hexdigest()[:12]
    return f"{len(prev_text)}-{h}"


@app.get("/projects/{script_id}/previous_episode")
def get_previous_episode_summary(script_id: str):
    """Return previous-episode raw snippet + AI summary cache state for
    the "Previously on..." right rail in the Script step.

    Response shape:
        {
          "has_previous": bool,
          "previous_episode_id": str | null,
          "previous_episode_title": str | null,
          "raw_snippet": str  (last ~600 chars of previous original_text),
          "ai_summary": str | null  (cached summary if fresh),
          "ai_summary_stale": bool  (true when prev text changed since cache),
        }
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        return {
            "has_previous": False,
            "previous_episode_id": None,
            "previous_episode_title": None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }
    series = pipeline.get_series(script.series_id)
    if not series:
        return {
            "has_previous": False,
            "previous_episode_id": None,
            "previous_episode_title": None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }
    # Find previous episode by ordered episode_ids — fall back to
    # episode_number ordering when episode_ids is malformed.
    try:
        idx = series.episode_ids.index(script_id)
    except ValueError:
        idx = -1
    if idx <= 0:
        # No previous episode (this IS the first or the id is missing)
        return {
            "has_previous": False,
            "previous_episode_id": None,
            "previous_episode_title": None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }
    prev_id = series.episode_ids[idx - 1]
    prev = pipeline.get_script(prev_id)
    if not prev or not (prev.original_text or "").strip():
        return {
            "has_previous": False,
            "previous_episode_id": prev_id,
            "previous_episode_title": prev.title if prev else None,
            "raw_snippet": "",
            "ai_summary": None,
            "ai_summary_stale": False,
        }
    raw_snippet = (prev.original_text or "")[-800:]
    current_rev = _prev_text_revision(prev.original_text or "")
    is_stale = (
        script.last_episode_summary_cache is not None
        and script.last_episode_summary_revision != current_rev
    )
    # R2V v2 P2-a — last frames of previous episode for Storyboard
    # cross-step reference. We surface the *last 4 frames* with their
    # selected video / T2I image so authors composing this episode's
    # opening shots can see what the previous episode ended on.
    last_frames = []
    for f in (prev.frames or [])[-4:]:
        thumb_url = None
        # Prefer the selected video's poster/url, fall back to T2I image
        if getattr(f, "video_url", None):
            thumb_url = f.video_url
        elif getattr(f, "rendered_image_url", None):
            thumb_url = f.rendered_image_url
        elif getattr(f, "image_url", None):
            thumb_url = f.image_url
        elif getattr(f, "t2i_image_urls", None):
            urls = f.t2i_image_urls
            if urls:
                idx = getattr(f, "t2i_selected_index", 0) or 0
                idx = max(0, min(idx, len(urls) - 1))
                thumb_url = urls[idx]
        last_frames.append({
            "id": f.id,
            "action_description": (f.action_description or "")[:120],
            "thumbnail_url": thumb_url,
            "video_url": getattr(f, "video_url", None),
        })
    return {
        "has_previous": True,
        "previous_episode_id": prev_id,
        "previous_episode_title": prev.title,
        "raw_snippet": raw_snippet,
        "ai_summary": script.last_episode_summary_cache,
        "ai_summary_stale": is_stale,
        "last_frames": last_frames,
    }


@app.post("/projects/{script_id}/previous_episode/summary")
def generate_previous_episode_summary(script_id: str):
    """On-demand AI summary of the previous episode (qwen3.6-plus).
    Per Q7-followup design: this is *user-triggered*, not auto, to
    respect LLM quota and user intent. Result is cached on the current
    episode's Script record with a revision marker for invalidation.
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    if not script.series_id:
        raise HTTPException(status_code=400, detail="Episode not in a series")
    series = pipeline.get_series(script.series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    try:
        idx = series.episode_ids.index(script_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Episode not registered in series order")
    if idx <= 0:
        raise HTTPException(status_code=400, detail="No previous episode")
    prev = pipeline.get_script(series.episode_ids[idx - 1])
    if not prev or not (prev.original_text or "").strip():
        raise HTTPException(status_code=400, detail="Previous episode has no script")

    # Call LLM — keep prompt simple and bilingual-friendly
    from .llm_adapter import LLMAdapter
    prompt = (
        "请用 150-220 字概括下面这段剧本的核心情节、关键角色出场与结尾留下的悬念/钩子。"
        "不要列点，写成一段连贯的中文叙述，便于作者快速回顾上一集走到了哪里。\n\n"
        f"剧本：\n{prev.original_text}"
    )
    try:
        adapter = LLMAdapter()
        summary = adapter.chat(
            messages=[{"role": "user", "content": prompt}],
            model=None,  # use adapter default (qwen3.6-plus with fallback chain)
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")
    summary = (summary or "").strip()
    if not summary:
        raise HTTPException(status_code=502, detail="Empty summary from LLM")

    # Persist cache + revision marker on current script
    script.last_episode_summary_cache = summary
    script.last_episode_summary_revision = _prev_text_revision(prev.original_text or "")
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return {
        "ai_summary": summary,
        "ai_summary_stale": False,
        "previous_episode_id": prev.id,
        "previous_episode_title": prev.title,
    }


@app.get("/series/{series_id}/characters/{character_id}/appearances")
def get_character_appearances(series_id: str, character_id: str):
    """R2V v2 P1-c — Cross-episode character appearance summary.
    Used by Script step's @mention helper popover. Returns chronological
    list of episodes where the character appears + per-episode frame
    count + aggregated 'last seen' info."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    char = next((c for c in series.characters if c.id == character_id), None)
    if not char:
        # Try as episode-local character — maybe id is from any episode
        for ep_id in series.episode_ids:
            ep = pipeline.get_script(ep_id)
            if ep:
                local = next((c for c in ep.characters if c.id == character_id), None)
                if local:
                    char = local
                    break
    if not char:
        raise HTTPException(status_code=404, detail="Character not found in series scope")
    appearances = []
    total_frames = 0
    for ep_id in series.episode_ids:
        ep = pipeline.get_script(ep_id)
        if not ep:
            continue
        count = sum(1 for f in ep.frames if character_id in (f.character_ids or []))
        if count > 0:
            appearances.append({
                "episode_id": ep_id,
                "episode_number": ep.episode_number,
                "episode_title": ep.title,
                "frame_count": count,
            })
            total_frames += count
    return {
        "character": {
            "id": char.id,
            "name": char.name,
            "persona": char.persona,
            "description": char.description,
        },
        "appearances": appearances,
        "total_frames": total_frames,
    }


# R2V v2 P2-b — "Next episode hook" prediction (forward-looking).
@app.get("/projects/{script_id}/next_hook")
def get_next_episode_hook(script_id: str):
    """Return cached hook prediction state for the Script step's
    'Hook for next' panel. Always returns the current revision marker
    so the frontend can decide if a refresh is warranted."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    current_text = script.original_text or ""
    current_rev = _prev_text_revision(current_text)
    is_stale = (
        script.next_hook_cache is not None
        and script.next_hook_revision != current_rev
    )
    return {
        "has_text": bool(current_text.strip()),
        "hook": script.next_hook_cache,
        "stale": is_stale,
    }


@app.post("/projects/{script_id}/next_hook")
def generate_next_episode_hook(script_id: str):
    """On-demand AI prediction of the next-episode opening hook based
    on THIS episode's ending. User-triggered (no auto-generate to
    respect LLM quota)."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    text = script.original_text or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Episode has no script text yet")

    from .llm_adapter import LLMAdapter
    # Use last ~1500 chars as context (the ending matters most for hooks)
    ending = text[-1500:]
    prompt = (
        "下面是一集剧本的结尾。请你站在编剧的视角，预测下一集开头可能的 hook，"
        "用 120-180 字给出 2-3 个具体方向（包括场景、角色出场、悬念点）。"
        "不要列点编号，写成连贯的中文段落，便于作者参考。\n\n"
        f"本集结尾：\n{ending}"
    )
    try:
        adapter = LLMAdapter()
        hook = adapter.chat(
            messages=[{"role": "user", "content": prompt}],
            model=None,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")
    hook = (hook or "").strip()
    if not hook:
        raise HTTPException(status_code=502, detail="Empty hook from LLM")

    script.next_hook_cache = hook
    script.next_hook_revision = _prev_text_revision(text)
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return {"hook": hook, "stale": False}


@app.put("/projects/{script_id}/next_hook")
def update_next_episode_hook(script_id: str, payload: dict):
    """Manually edit / clear the next-hook cache."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    hook = payload.get("hook")
    if hook is None:
        script.next_hook_cache = None
        script.next_hook_revision = None
    else:
        script.next_hook_cache = str(hook).strip()
        script.next_hook_revision = _prev_text_revision(script.original_text or "")
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return {"hook": script.next_hook_cache, "stale": False}


@app.post("/projects/{script_id}/sync_descriptions", response_model=Script)
def sync_descriptions(script_id: str):
    """
    Syncs entity descriptions from Script module to Assets module.
    
    This endpoint forces a refresh of the project data, ensuring that any
    description changes made in the Script module are reflected in Assets.
    
    Note: This only syncs descriptions; generated images/videos are preserved.
    """
    try:
        updated_script = pipeline.sync_descriptions_from_script_entities(script_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AddCharacterRequest(BaseModel):
    name: str
    description: str

@app.post("/projects/{script_id}/characters", response_model=Script)
def add_character(script_id: str, request: AddCharacterRequest):
    """Adds a new character."""
    try:
        updated_script = pipeline.add_character(script_id, request.name, request.description)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/projects/{script_id}/characters/{char_id}", response_model=Script)
def delete_character(script_id: str, char_id: str):
    """Deletes a character."""
    try:
        updated_script = pipeline.delete_character(script_id, char_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class AddSceneRequest(BaseModel):
    name: str
    description: str

@app.post("/projects/{script_id}/scenes", response_model=Script)
def add_scene(script_id: str, request: AddSceneRequest):
    """Adds a new scene."""
    try:
        updated_script = pipeline.add_scene(script_id, request.name, request.description)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/projects/{script_id}/scenes/{scene_id}", response_model=Script)
def delete_scene(script_id: str, scene_id: str):
    """Deletes a scene."""
    try:
        updated_script = pipeline.delete_scene(script_id, scene_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class UpdateStyleRequest(BaseModel):
    style_preset: str
    style_prompt: Optional[str] = None


@app.patch("/projects/{script_id}/style", response_model=Script)
def update_project_style(script_id: str, request: UpdateStyleRequest):
    """Updates the global style settings for a project."""
    try:
        updated_script = pipeline.update_project_style(
            script_id,
            request.style_preset,
            request.style_prompt
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/generate_assets", response_model=Script)
def generate_assets(script_id: str, background_tasks: BackgroundTasks):
    """Triggers asset generation."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")

    # Run in background to avoid blocking
    # For simplicity in this demo, we run synchronously or use background tasks
    # pipeline.generate_assets(script_id) 
    # But since we want to return the updated status, we might want to run it and return.
    # Given the mock nature, it's fast.

    try:
        updated_script = pipeline.generate_assets(script_id)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



class GenerateMotionRefRequest(BaseModel):
    """Request model for generating Motion Reference videos."""
    asset_id: str
    asset_type: str  # 'full_body' | 'head_shot' for characters; 'scene' | 'prop' for scenes and props
    prompt: Optional[str] = None
    audio_url: Optional[str] = None  # Driving audio for lip-sync
    duration: int = 5
    batch_size: int = 1


@app.post("/projects/{script_id}/assets/generate_motion_ref")
def generate_motion_ref(script_id: str, request: GenerateMotionRefRequest, background_tasks: BackgroundTasks):
    """Generates a Motion Reference video for an asset (Character Full Body/Headshot, Scene, or Prop)."""
    try:
        script, task_id = pipeline.create_motion_ref_task(
            script_id=script_id,
            asset_id=request.asset_id,
            asset_type=request.asset_type,
            prompt=request.prompt,
            audio_url=request.audio_url,
            duration=request.duration,
            batch_size=request.batch_size
        )
        
        # Add background processing
        background_tasks.add_task(pipeline.process_motion_ref_task, script_id, task_id)
        
        # Return script with task_id for frontend polling
        response_data = script.model_dump() if hasattr(script, 'model_dump') else script.dict()
        response_data["_task_id"] = task_id
        return signed_response(response_data)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# === STORYBOARD DRAMATIZATION v2 ===

class AnalyzeToStoryboardRequest(BaseModel):
    """Request to analyze script text into storyboard frames."""
    text: str


@app.post("/projects/{script_id}/storyboard/analyze")
def analyze_to_storyboard(script_id: str, request: AnalyzeToStoryboardRequest):
    """
    Analyzes script text and generates storyboard frames using AI (Prompt B).
    Replaces existing frames with newly generated ones.
    """
    try:
        updated_script = pipeline.analyze_text_to_frames(script_id, request.text)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error in analyze_to_storyboard: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class RefinePromptRequest(BaseModel):
    """Request to refine a frame's prompt using AI."""
    frame_id: str
    raw_prompt: str
    assets: list = []  # List of asset references
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement


@app.post("/projects/{script_id}/storyboard/refine_prompt")
def refine_storyboard_prompt(script_id: str, request: RefinePromptRequest):
    """
    Refines a raw prompt into bilingual (CN/EN) prompts using AI (Prompt C).
    Returns the refined prompts and optionally updates the frame.

    SYNC handler — pipeline.refine_frame_prompt() does a blocking LLM call.
    Declared `def` (not `async def`) so FastAPI runs it on the threadpool
    and the event loop stays free for concurrent GETs.
    """
    try:
        result = pipeline.refine_frame_prompt(
            script_id,
            request.frame_id,
            request.raw_prompt,
            request.assets,
            request.feedback,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error in refine_storyboard_prompt: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/refine")
def refine_single_frame(script_id: str, frame_id: str):
    """Phase 2: Refine a single coarse frame into a rich frame with structured fields."""
    try:
        frame = pipeline.refine_frame(script_id, frame_id)
        if not frame:
            raise HTTPException(status_code=500, detail="Refine returned no result")
        return frame.model_dump() if hasattr(frame, 'model_dump') else frame.dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error in refine_single_frame: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/storyboard/refine_batch")
def refine_storyboard_batch(script_id: str):
    """Phase 2: Batch refine all coarse frames. Streams SSE events."""
    from fastapi.responses import StreamingResponse

    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    def event_stream():
        for event_type, data in pipeline.refine_batch_generator(script_id):
            yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/projects/{script_id}/generate_storyboard", response_model=Script)
def generate_storyboard(script_id: str):
    """Triggers storyboard generation."""
    try:
        updated_script = pipeline.generate_storyboard(script_id)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/projects/{script_id}/generate_video", response_model=Script)
def generate_video(script_id: str):
    """Triggers video generation."""
    try:
        updated_script = pipeline.generate_video(script_id)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/projects/{script_id}/generate_audio", response_model=Script)
def generate_audio(script_id: str):
    """Triggers audio generation."""
    try:
        updated_script = pipeline.generate_audio(script_id)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



class CreateVideoTaskRequest(BaseModel):
    image_url: str
    prompt: str
    frame_id: Optional[str] = None
    duration: int = 5
    seed: Optional[int] = None
    resolution: str = "720p"
    generate_audio: bool = False
    audio_url: Optional[str] = None
    prompt_extend: bool = True
    negative_prompt: Optional[str] = None
    batch_size: int = 1
    model: str = "wan2.6-i2v"
    shot_type: str = "single"  # 'single' or 'multi' (only for wan2.6-i2v)
    generation_mode: str = "i2v"  # 'i2v' (image-to-video) or 'r2v' (reference-to-video)
    reference_video_urls: List[str] = []  # Reference video URLs for R2V (max 3)
    # Kling params
    mode: Optional[str] = None
    sound: Optional[str] = None
    cfg_scale: Optional[float] = None
    # Vidu params
    vidu_audio: Optional[bool] = None
    movement_amplitude: Optional[str] = None
    # HappyHorse params
    reference_image_urls: List[str] = []  # Reference image URLs for HH R2V (max 9)
    ratio: Optional[str] = None  # Aspect ratio for HH T2V/R2V
    # Watermark toggle (wan / kling / vidu / pixverse / happyhorse video).
    # None = leave to provider default; True/False = explicit user choice.
    watermark: Optional[bool] = None
    # Source tab in the Storyboard R2V workbench. Distinct from
    # generation_mode (backend dispatcher hint) — used by the candidates
    # panel to group takes per UI tab on refresh.
    workbench_tab: Optional[str] = None  # 't2i_i2v' | 'direct_r2v'


def process_video_task(script_id: str, task_id: str):
    """Background task to generate video.

    The pipeline method has its own try/except that flips status to
    "failed" on errors during generation. This outer wrapper is a
    belt-and-suspenders writeback for exceptions that escape *before*
    that inner handler armed (e.g. `get_script` raising, persistence
    layer crashing). Without it the task would stay forever-`pending`
    and the UI would show an eternal spinner.
    """
    try:
        pipeline.process_video_task(script_id, task_id)
    except Exception as e:
        logger.exception(f"Error processing video task {task_id}")
        try:
            pipeline.mark_video_task_failed(
                script_id, task_id, f"Background error: {e}"
            )
        except Exception:
            logger.exception(
                f"Could not mark video task {task_id} as failed after wrapper exception"
            )


class AnnotateVideoTaskRequest(BaseModel):
    """User-review annotations on a video task ("抽卡 review").
    Both fields optional so the same endpoint covers star-only,
    label-only, or both. `clear_label=True` explicitly removes
    the label (None on its own means "don't change")."""
    is_starred: Optional[bool] = None
    label: Optional[str] = None
    clear_label: bool = False


@app.patch("/projects/{script_id}/video_tasks/{task_id}/annotate", response_model=VideoTask)
def annotate_video_task(script_id: str, task_id: str, request: AnnotateVideoTaskRequest):
    """Set the user's star + label on a video task. Used by Storyboard's
    candidates panel for shortlist marking (multi-select) and short
    free-text notes (≤20 chars, truncated server-side)."""
    task = pipeline.annotate_video_task(
        script_id,
        task_id,
        is_starred=request.is_starred,
        label=request.label,
        clear_label=request.clear_label,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Video task not found")
    return signed_response(task)


class UpdateFrameWorkbenchRequest(BaseModel):
    """Storyboard R2V workbench state writeback. Every field optional;
    only what the caller passes gets updated. The server clamps lists
    and indices to safe ranges; rejects unknown enum values."""
    workbench_tab_mode: Optional[str] = None  # 't2i_i2v' | 'direct_r2v'
    t2i_image_urls: Optional[List[str]] = None  # full ordered history, server caps at 10 FIFO
    t2i_selected_index: Optional[int] = None  # active首帧 index, clamped to range
    workbench_generate_count: Optional[int] = None  # batch size, clamped to [1, 6]


@app.patch("/projects/{script_id}/frames/{frame_id}/workbench", response_model=StoryboardFrame)
def update_frame_workbench(
    script_id: str, frame_id: str, request: UpdateFrameWorkbenchRequest
):
    """Persist Storyboard R2V workbench state onto a frame so it
    survives refresh and cross-device opens. Previously this state
    lived only in React component state and got lost on reload."""
    try:
        frame = pipeline.update_frame_workbench(
            script_id,
            frame_id,
            workbench_tab_mode=request.workbench_tab_mode,
            t2i_image_urls=request.t2i_image_urls,
            t2i_selected_index=request.t2i_selected_index,
            workbench_generate_count=request.workbench_generate_count,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if frame is None:
        raise HTTPException(status_code=404, detail="Script or frame not found")
    return signed_response(frame)


@app.post("/projects/{script_id}/video_tasks/{task_id}/cancel", response_model=VideoTask)
def cancel_video_task(script_id: str, task_id: str):
    """Mark a video task as failed-by-cancel. We can't actually yank a
    running provider call mid-flight (the provider keeps rendering on
    its side), but flipping the local status to "failed" unblocks the
    UI and puts the user back in control. Treats already-completed
    tasks as a no-op."""
    ok = pipeline.mark_video_task_failed(script_id, task_id, "Canceled by user")
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Video task not found or already completed",
        )
    script = pipeline.get_script(script_id)
    task = next(
        (t for t in (script.video_tasks if script else []) if t.id == task_id),
        None,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Video task not found")
    return signed_response(task)


@app.post("/projects/{script_id}/video_tasks", response_model=List[VideoTask])
def create_video_task(script_id: str, request: CreateVideoTaskRequest, background_tasks: BackgroundTasks):
    """Creates new video generation tasks."""
    try:
        tasks = []
        for _ in range(request.batch_size):
            script, task_id = pipeline.create_video_task(
                script_id=script_id,
                image_url=request.image_url,
                prompt=request.prompt,
                frame_id=request.frame_id,
                duration=request.duration,
                seed=request.seed,
                resolution=request.resolution,
                generate_audio=request.generate_audio,
                audio_url=request.audio_url,
                prompt_extend=request.prompt_extend,
                negative_prompt=request.negative_prompt,
                model=request.model,
                shot_type=request.shot_type,
                generation_mode=request.generation_mode,
                reference_video_urls=request.reference_video_urls,
                reference_image_urls=request.reference_image_urls,
                ratio=request.ratio,
                watermark=request.watermark,
                mode=request.mode,
                sound=request.sound,
                cfg_scale=request.cfg_scale,
                vidu_audio=request.vidu_audio,
                movement_amplitude=request.movement_amplitude,
                workbench_tab=request.workbench_tab,
            )

            # Find the created task object
            created_task = next((t for t in script.video_tasks if t.id == task_id), None)
            if created_task:
                tasks.append(created_task)

            # Add background processing
            background_tasks.add_task(pipeline.process_video_task, script_id, task_id)

        return signed_response(tasks)

    except ValueError as e:
        # Validation failures from pipeline.create_video_task (model⇄
        # mode⇄refs mismatch, missing references for R2V, etc.) are
        # the user's responsibility to fix — surface as 400 so the
        # frontend can show an inline error instead of treating it
        # as a server error.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/generate")
def generate_single_asset(script_id: str, request: GenerateAssetRequest, background_tasks: BackgroundTasks):
    """Generates a single asset with specific options (async).
    Returns immediately with task_id for polling progress."""
    try:
        script, task_id = pipeline.create_asset_generation_task(
            script_id,
            request.asset_id,
            request.asset_type,
            request.style_preset,
            request.reference_image_url,
            request.style_prompt,
            request.generation_type,
            request.prompt,
            request.apply_style,
            request.negative_prompt,
            request.batch_size,
            request.model_name,
            request.aspect_ratio,
        )
        
        # Add background processing
        background_tasks.add_task(pipeline.process_asset_generation_task, task_id)
        
        # Return script with task_id for frontend polling
        response_data = script.model_dump() if hasattr(script, 'model_dump') else script.dict()
        response_data["_task_id"] = task_id
        return signed_response(response_data)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tasks/{task_id}")
def get_task_status(task_id: str):
    """Returns the status of an asset generation task for polling."""
    status = pipeline.get_asset_generation_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # If completed, return the updated script as well
    if status["status"] == "completed":
        script = pipeline.get_script(status["script_id"])
        if script:
            status["script"] = signed_response(script).body.decode('utf-8')
    
    return status


class GenerateAssetVideoRequest(BaseModel):
    prompt: Optional[str] = None
    duration: int = 5
    aspect_ratio: Optional[str] = None


@app.post("/projects/{script_id}/assets/{asset_type}/{asset_id}/generate_video", response_model=Script)
def generate_asset_video(script_id: str, asset_type: str, asset_id: str, request: GenerateAssetVideoRequest, background_tasks: BackgroundTasks):
    """Generates a video for a specific asset (I2V)."""
    try:
        script, task_id = pipeline.create_asset_video_task(
            script_id,
            asset_id,
            asset_type,
            request.prompt,
            request.duration,
            request.aspect_ratio
        )
        
        # Add background processing
        background_tasks.add_task(pipeline.process_video_task, script_id, task_id)
        
        return signed_response(script)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/projects/{script_id}/assets/{asset_type}/{asset_id}/videos/{video_id}", response_model=Script)
def delete_asset_video(script_id: str, asset_type: str, asset_id: str, video_id: str):
    """Deletes a video from an asset."""
    try:
        updated_script = pipeline.delete_asset_video(
            script_id,
            asset_id,
            asset_type,
            video_id
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/projects/{script_id}/assets/toggle_lock", response_model=Script)
def toggle_asset_lock(script_id: str, request: ToggleLockRequest):
    """Toggles the locked status of an asset."""
    try:
        updated_script = pipeline.toggle_asset_lock(
            script_id,
            request.asset_id,
            request.asset_type
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/toggle_starred", response_model=Script)
def toggle_asset_starred(script_id: str, request: ToggleLockRequest):
    """Toggles the starred (library shortlist) status of an asset."""
    try:
        updated_script = pipeline.toggle_asset_starred(
            script_id,
            request.asset_id,
            request.asset_type
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/projects/{script_id}/assets/update_image", response_model=Script)
def update_asset_image(script_id: str, request: UpdateAssetImageRequest):
    """Updates an asset's image URL manually."""
    try:
        updated_script = pipeline.update_asset_image(
            script_id,
            request.asset_id,
            request.asset_type,
            request.image_url
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/projects/{script_id}/assets/update_attributes", response_model=Script)
def update_asset_attributes(script_id: str, request: UpdateAssetAttributesRequest):
    """Updates arbitrary attributes of an asset."""
    try:
        updated_script = pipeline.update_asset_attributes(
            script_id,
            request.asset_id,
            request.asset_type,
            request.attributes
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



class UpdateAssetDescriptionRequest(BaseModel):
    asset_id: str
    asset_type: str
    description: str


@app.post("/projects/{script_id}/assets/update_description", response_model=Script)
def update_asset_description(script_id: str, request: UpdateAssetDescriptionRequest):
    """Updates an asset's description."""
    try:
        updated_script = pipeline.update_asset_description(
            script_id,
            request.asset_id,
            request.asset_type,
            request.description
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



class SelectVariantRequest(BaseModel):
    asset_id: str
    asset_type: str
    variant_id: str
    generation_type: str = None  # For character: "full_body", "three_view", "headshot"

@app.post("/projects/{script_id}/assets/variant/select", response_model=Script)
def select_asset_variant(script_id: str, request: SelectVariantRequest):
    """Selects a specific variant for an asset."""
    try:
        updated_script = pipeline.select_asset_variant(
            script_id,
            request.asset_id,
            request.asset_type,
            request.variant_id,
            request.generation_type
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DeleteVariantRequest(BaseModel):
    asset_id: str
    asset_type: str
    variant_id: str

@app.post("/projects/{script_id}/assets/variant/delete", response_model=Script)
def delete_asset_variant(script_id: str, request: DeleteVariantRequest):
    """Deletes a specific variant from an asset."""
    try:
        updated_script = pipeline.delete_asset_variant(
            script_id,
            request.asset_id,
            request.asset_type,
            request.variant_id
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FavoriteVariantRequest(BaseModel):
    asset_id: str
    asset_type: str
    variant_id: str
    generation_type: Optional[str] = None  # For character: 'full_body', 'three_view', 'headshot'
    is_favorited: bool

@app.post("/projects/{script_id}/assets/variant/favorite", response_model=Script)
def toggle_variant_favorite(script_id: str, request: FavoriteVariantRequest):
    """Toggles the favorite status of a variant. Favorited variants won't be auto-deleted when limit is reached."""
    try:
        updated_script = pipeline.toggle_variant_favorite(
            script_id,
            request.asset_id,
            request.asset_type,
            request.variant_id,
            request.is_favorited,
            request.generation_type
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/projects/{script_id}/model_settings", response_model=Script)
def update_model_settings(script_id: str, request: UpdateModelSettingsRequest):
    """Updates project's model settings for T2I/I2I/I2V and aspect ratios."""
    try:
        updated_script = pipeline.update_model_settings(
            script_id,
            request.t2i_model,
            request.i2i_model,
            request.i2v_model,
            r2v_model=request.r2v_model,
            character_aspect_ratio=request.character_aspect_ratio,
            scene_aspect_ratio=request.scene_aspect_ratio,
            prop_aspect_ratio=request.prop_aspect_ratio,
            storyboard_aspect_ratio=request.storyboard_aspect_ratio,
            image_model=request.image_model,
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdatePromptConfigRequest(BaseModel):
    storyboard_polish: str = ""
    video_polish: str = ""
    r2v_polish: str = ""
    entity_extraction: str = ""
    style_analysis: str = ""
    storyboard_extraction: str = ""


@app.get("/projects/{script_id}/prompt_config")
def get_prompt_config(script_id: str):
    """Returns project prompt_config and system default prompts for reference."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Project not found")
        config = script.prompt_config if hasattr(script, 'prompt_config') else PromptConfig()
        return {
            "prompt_config": config.model_dump(),
            "defaults": {
                "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
                "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
                "r2v_polish": DEFAULT_R2V_POLISH_PROMPT,
                "storyboard_extraction": DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/projects/{script_id}/prompt_config")
def update_prompt_config(script_id: str, request: UpdatePromptConfigRequest):
    """Updates project custom prompt configuration. Empty string = use system default."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Project not found")
        existing = getattr(script, "prompt_config", None)
        preserved_polish_model = getattr(existing, "polish_model", "") if existing else ""
        script.prompt_config = PromptConfig(
            storyboard_polish=request.storyboard_polish,
            video_polish=request.video_polish,
            r2v_polish=request.r2v_polish,
            entity_extraction=request.entity_extraction,
            style_analysis=request.style_analysis,
            storyboard_extraction=request.storyboard_extraction,
            polish_model=preserved_polish_model,
        )
        pipeline._save_data()
        return {"prompt_config": script.prompt_config.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/prompt_defaults")
def get_prompt_defaults():
    """Return the built-in default system prompts for all configurable prompt keys.

    The frontend uses this to pre-fill the prompt fields and to compute the
    "delta" on save (a field equal to its default is stored as "" so the
    built-in default is used instead of pinning a snapshot).
    """
    return {
        "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
        "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
        "r2v_polish": DEFAULT_R2V_POLISH_PROMPT,
        "entity_extraction": DEFAULT_ENTITY_EXTRACTION_PROMPT,
        "style_analysis": DEFAULT_STYLE_ANALYSIS_PROMPT,
        "storyboard_extraction": DEFAULT_STORYBOARD_EXTRACTION_PROMPT,
    }


class BindVoiceRequest(BaseModel):
    voice_id: str
    voice_name: str


@app.post("/projects/{script_id}/characters/{char_id}/voice", response_model=Script)
def bind_voice(script_id: str, char_id: str, request: BindVoiceRequest):
    """Binds a voice to a character."""
    try:
        updated_script = pipeline.bind_voice(script_id, char_id, request.voice_id, request.voice_name)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdateVoiceParamsRequest(BaseModel):
    speed: float = 1.0
    pitch: float = 1.0
    volume: int = 50


@app.put("/projects/{script_id}/characters/{char_id}/voice_params", response_model=Script)
def update_voice_params(script_id: str, char_id: str, request: UpdateVoiceParamsRequest):
    """Updates voice parameters for a character."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    char = next((c for c in script.characters if c.id == char_id), None)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    char.voice_speed = request.speed
    char.voice_pitch = request.pitch
    char.voice_volume = request.volume
    pipeline._save_data()
    return signed_response(script)


@app.get("/voices")
def get_voices():
    """Returns list of available voices."""
    return pipeline.audio_generator.get_available_voices()


class VoicePreviewRequest(BaseModel):
    """PR-3g #3 · request shape for /voice/preview endpoint.

    Backs the Voice picker modal's inline ▶ button. Frontend hits this
    when user previews a voice card; backend either returns a cached
    URL or generates fresh audio via TTSProcessor.
    """
    voice_id: str
    text: str
    speed: float = 1.0
    pitch: float = 1.0
    volume: int = 50
    instructions: Optional[str] = None


@app.post("/voice/preview")
def voice_preview(request: VoicePreviewRequest):
    """Generate or fetch cached preview audio for a voice.

    Cache key = md5(voice_id|text|speed|pitch|volume|instructions). First
    call triggers TTSProcessor.synthesize() and writes to
    output/cache/voice_preview/{key}.mp3. Subsequent identical calls
    return the cached URL instantly.

    PR-3h #2: handles CUSTOM voices (clones/designs) by looking up
    series.custom_voices[] for target_model + family overrides — required
    because cloned voice_ids aren't in static TTS_VOICE_REGISTRY.

    Spec: r2v-workflow-v3-unified.md §4.2.3 (cache strategy) + Q5 b/c.
    """
    import hashlib
    if not pipeline.audio_generator.tts:
        raise HTTPException(
            status_code=503,
            detail="TTS service unavailable. Check DASHSCOPE_API_KEY configuration.",
        )

    # PR-3h #2: resolve custom voice → target_model/family override
    custom = pipeline.find_custom_voice(request.voice_id)
    model_override = custom.target_model if custom else None
    family_override = custom.family if custom else None

    cache_dir = "output/cache/voice_preview"
    os.makedirs(cache_dir, exist_ok=True)
    cache_key = hashlib.md5(
        f"{request.voice_id}|{request.text}|{request.speed}|{request.pitch}|{request.volume}|{request.instructions or ''}".encode("utf-8")
    ).hexdigest()
    cache_path = os.path.join(cache_dir, f"{cache_key}.mp3")
    cached = os.path.exists(cache_path)

    if not cached:
        try:
            pipeline.audio_generator.tts.synthesize(
                text=request.text,
                output_path=cache_path,
                voice=request.voice_id,
                speech_rate=request.speed,
                pitch_rate=request.pitch,
                volume=request.volume,
                instructions=request.instructions,
                model_override=model_override,
                family_override=family_override,
            )
        except Exception as e:
            logger.error(f"[/voice/preview] TTS error voice={request.voice_id}: {e}")
            raise HTTPException(status_code=500, detail=f"TTS generation failed: {e}")

    # Static mount /files maps to output/, so the relative path under output/
    # becomes the URL path frontend can hit. signed_response wraps for OSS
    # signing when configured, no-op otherwise.
    url = f"cache/voice_preview/{cache_key}.mp3"
    return signed_response({"url": url, "cached": cached})


# ─────────────────────────────────────────────────────────────
# PR-3h · Voice clone endpoints
# Per Q16: series-level scope for custom voices. Frontend uploads audio
# via existing /upload (gets URL), then calls /voice/clone with that URL.
# ─────────────────────────────────────────────────────────────

class VoiceCloneRequest(BaseModel):
    """PR-3h request: clone a voice from a reference audio URL.

    Frontend pre-validates: ≤10MB, MP3/WAV/M4A, ≥16kHz, 10-20s recommended.
    """
    series_id: str
    audio_url: str
    label: str
    target_model: str = "cosyvoice-v3.5-plus"


@app.post("/voice/clone")
def voice_clone(request: VoiceCloneRequest):
    """Create a custom voice by cloning a reference audio sample.

    Per Q15.2: stored at series level so any character in the series can
    pick from the clone via the VoicePickerModal '我的复刻' tab.
    """
    try:
        custom = pipeline.create_voice_clone(
            series_id=request.series_id,
            audio_url=request.audio_url,
            label=request.label,
            target_model=request.target_model,
        )
        return signed_response(custom)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/series/{series_id}/custom_voices")
def list_series_custom_voices(series_id: str):
    """Return all custom voices (clones + designs) in a series."""
    voices = pipeline.list_custom_voices(series_id)
    return signed_response(voices)


@app.delete("/series/{series_id}/custom_voices/{voice_id}")
def delete_series_custom_voice(series_id: str, voice_id: str):
    """Remove a custom voice from a series.

    Note: does NOT delete on dashscope side (24h retention is best-effort).
    If a character has this voice_id bound, the binding becomes orphaned
    (frontend should warn before delete in v2).
    """
    removed = pipeline.delete_custom_voice(series_id, voice_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Custom voice not found")
    return signed_response({"removed": True})


# ─────────────────────────────────────────────────────────────
# PR-3i · Voice design endpoints
# Iterative pattern: preview → (tweak prompt) → preview → accept.
# Each preview mints a NEW voice on dashscope; only accept persists.
# ─────────────────────────────────────────────────────────────

class VoiceDesignPreviewRequest(BaseModel):
    voice_prompt: str
    preview_text: str = "你好，这是一段音色测试。请仔细听一听是否符合预期。"
    target_model: str = "cosyvoice-v3.5-plus"


@app.post("/voice/design/preview")
def voice_design_preview(request: VoiceDesignPreviewRequest):
    """Mint a fresh design voice and return a preview audio URL.

    The user re-calls this with a tweaked voice_prompt to iterate.
    Returns: {voice_id, preview_url, target_model}.
    """
    try:
        result = pipeline.voice_design_preview(
            voice_prompt=request.voice_prompt,
            preview_text=request.preview_text,
            target_model=request.target_model,
        )
        return signed_response(result)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


class VoiceDesignSaveRequest(BaseModel):
    series_id: str
    voice_id: str
    voice_prompt: str
    label: str
    target_model: str = "cosyvoice-v3.5-plus"


@app.post("/voice/design/accept")
def voice_design_accept(request: VoiceDesignSaveRequest):
    """Persist a previewed design voice into series.custom_voices[]."""
    try:
        custom = pipeline.voice_design_save(
            series_id=request.series_id,
            voice_id=request.voice_id,
            voice_prompt=request.voice_prompt,
            label=request.label,
            target_model=request.target_model,
        )
        return signed_response(custom)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


class VoiceDesignTranslateRequest(BaseModel):
    description: str


@app.post("/voice/design/translate")
def voice_design_translate(request: VoiceDesignTranslateRequest):
    """LLM helper: character.description → CosyVoice voice_prompt."""
    if not request.description.strip():
        raise HTTPException(status_code=400, detail="description is empty")
    try:
        voice_prompt = pipeline.translate_character_to_voice_prompt(request.description)
        return {"voice_prompt": voice_prompt}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


class GenerateLineAudioRequest(BaseModel):
    speed: float = 1.0
    pitch: float = 1.0
    volume: int = 50
    instructions: Optional[str] = None  # PR-3j · chip emotion + free text


@app.post("/projects/{script_id}/frames/{frame_id}/audio", response_model=Script)
def generate_line_audio(script_id: str, frame_id: str, request: GenerateLineAudioRequest):
    """Generates audio for a specific frame with parameters."""
    try:
        updated_script = pipeline.generate_dialogue_line(
            script_id, frame_id,
            request.speed, request.pitch, request.volume,
            instructions=request.instructions,
        )
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────
# PR-3k · Audio mix endpoints (BGM presets + per-script mix settings)
# Backs the Assembly Mix phase.
# ─────────────────────────────────────────────────────────────

@app.get("/bgm/presets")
def list_bgm_presets():
    """Return the BGM preset catalog. UI populates the Mix phase picker."""
    from .audio import get_bgm_presets
    return get_bgm_presets()


class AudioMixRequest(BaseModel):
    bgm_url: Optional[str] = None  # null clears bgm
    dialogue_volume: Optional[int] = None  # 0-100
    bgm_volume: Optional[int] = None
    sfx_volume: Optional[int] = None


@app.put("/projects/{script_id}/audio_mix", response_model=Script)
def update_audio_mix(script_id: str, request: AudioMixRequest):
    """Set BGM + per-track mix levels for the final merge.

    PATCH-style: only fields explicitly present in the request body are
    applied. Using `model_fields_set` (Pydantic v2) lets us distinguish
    "omitted" from "explicit null" — clients pass `bgm_url: null` to clear
    the BGM, which the old `is not None` check silently swallowed.
    """
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    fields_set = request.model_fields_set
    if "bgm_url" in fields_set:
        script.bgm_url = request.bgm_url or None  # null and "" both clear
    mix = dict(script.mix_settings or {"dialogue": 100, "bgm": 35, "sfx": 60})
    if "dialogue_volume" in fields_set and request.dialogue_volume is not None:
        mix["dialogue"] = max(0, min(100, request.dialogue_volume))
    if "bgm_volume" in fields_set and request.bgm_volume is not None:
        mix["bgm"] = max(0, min(100, request.bgm_volume))
    if "sfx_volume" in fields_set and request.sfx_volume is not None:
        mix["sfx"] = max(0, min(100, request.sfx_volume))
    script.mix_settings = mix
    pipeline._save_data()
    return signed_response(script)


class DubPreviewRequest(BaseModel):
    video_task_id: str
    offset_ms: int = 0


@app.post("/projects/{script_id}/frames/{frame_id}/dub/preview")
def preview_dub(script_id: str, frame_id: str, request: DubPreviewRequest):
    """Generate a preview dub (cached Demucs + fast adelay+amix+mux)."""
    try:
        updated_script = pipeline.preview_dub(
            script_id, frame_id,
            video_task_id=request.video_task_id,
            offset_ms=request.offset_ms,
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/dub/apply")
def apply_dub(script_id: str, frame_id: str):
    """Promote current preview to official dubbed video."""
    try:
        updated_script = pipeline.apply_dub(script_id, frame_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/projects/{script_id}/frames/{frame_id}/dub")
def revert_frame_dub(script_id: str, frame_id: str):
    """Revert dubbing — remove dubbed+preview, keep bg cache."""
    try:
        updated_script = pipeline.revert_dub(script_id, frame_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/projects/{script_id}/dialogue_audio/batch")
def generate_dialogue_audio_batch(script_id: str):
    """PR-3j · Generate audio for every frame that has dialogue.

    Idempotent re-use: frames whose audio is already up-to-date (matching
    text/voice/instructions hash) are skipped. Stale + missing frames get
    regenerated using each character's bound voice.

    Returns the updated script plus _batch_stats with generated/skipped/failed counts.
    """
    from .audio import dialogue_audio_is_stale
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Script not found")
        char_lookup = {c.id: c for c in script.characters}
        char_name_lookup = {c.name.strip().lower(): c for c in script.characters}
        generated = 0
        skipped = 0
        failed = 0
        no_voice = 0
        for frame in script.frames:
            dialogue_text = (
                (frame.dialogue_structured.line if hasattr(frame, 'dialogue_structured') and frame.dialogue_structured else None)
                or frame.dialogue
            )
            if not dialogue_text:
                continue
            speaker = None
            if frame.character_ids:
                speaker = char_lookup.get(frame.character_ids[0])
            speaker_name = frame.speaker or (
                frame.dialogue_structured.speaker if frame.dialogue_structured else None
            )
            if not speaker and speaker_name:
                key = speaker_name.strip().lower()
                speaker = char_name_lookup.get(key)
                if not speaker:
                    for name, char in char_name_lookup.items():
                        if key in name or name in key:
                            speaker = char
                            break
            if not speaker or not speaker.voice_id:
                no_voice += 1
                continue
            if frame.audio_url and not dialogue_audio_is_stale(frame, speaker):
                skipped += 1
                continue
            try:
                pipeline.generate_dialogue_line(script_id, frame.id)
                generated += 1
            except Exception as exc:
                logger.error(f"[batch_dialogue_audio] frame={frame.id} error={exc}")
                failed += 1
        logger.info(f"[batch_dialogue_audio] script={script_id} generated={generated} skipped={skipped} failed={failed} no_voice={no_voice}")
        script = pipeline.get_script(script_id)
        response_data = script.model_dump() if hasattr(script, 'model_dump') else script.dict()
        response_data["_batch_stats"] = {"generated": generated, "skipped": skipped, "failed": failed, "no_voice": no_voice}
        return signed_response(response_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/mix/generate_sfx", response_model=Script)
def generate_mix_sfx(script_id: str):
    """Triggers Video-to-Audio SFX generation for all frames."""
    # Re-using generate_audio for now as it covers everything, 
    # but ideally we'd have granular methods in pipeline.
    # Let's just call generate_audio again, it's idempotent-ish.
    try:
        updated_script = pipeline.generate_audio(script_id)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/mix/generate_bgm", response_model=Script)
def generate_mix_bgm(script_id: str):
    """Triggers BGM generation."""
    try:
        updated_script = pipeline.generate_audio(script_id)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ToggleFrameLockRequest(BaseModel):
    frame_id: str


@app.post("/projects/{script_id}/frames/toggle_lock", response_model=Script)
def toggle_frame_lock(script_id: str, request: ToggleFrameLockRequest):
    """Toggles the locked status of a frame."""
    try:
        updated_script = pipeline.toggle_frame_lock(
            script_id,
            request.frame_id
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdateFrameRequest(BaseModel):
    frame_id: str
    image_prompt: Optional[str] = None
    action_description: Optional[str] = None
    dialogue: Optional[str] = None
    camera_angle: Optional[str] = None
    scene_id: Optional[str] = None
    character_ids: Optional[List[str]] = None
    duration: Optional[int] = None
    shot_size: Optional[str] = None
    camera_movement_description: Optional[str] = None
    transition_hint: Optional[str] = None

@app.post("/projects/{script_id}/frames/update", response_model=Script)
def update_frame(script_id: str, request: UpdateFrameRequest):
    """Updates frame data (prompt, scene, characters, etc.)."""
    try:
        updated_script = pipeline.update_frame(
            script_id,
            request.frame_id,
            image_prompt=request.image_prompt,
            action_description=request.action_description,
            dialogue=request.dialogue,
            camera_angle=request.camera_angle,
            scene_id=request.scene_id,
            character_ids=request.character_ids,
            duration=request.duration,
            shot_size=request.shot_size,
            camera_movement_description=request.camera_movement_description,
            transition_hint=request.transition_hint,
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class AddFrameRequest(BaseModel):
    scene_id: Optional[str] = None
    action_description: str = ""
    camera_angle: str = "medium_shot"
    insert_at: Optional[int] = None

@app.post("/projects/{script_id}/frames", response_model=Script)
def add_frame(script_id: str, request: AddFrameRequest):
    """Adds a new storyboard frame."""
    try:
        updated_script = pipeline.add_frame(
            script_id, 
            request.scene_id, 
            request.action_description, 
            request.camera_angle,
            request.insert_at
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/projects/{script_id}/frames/{frame_id}", response_model=Script)
def delete_frame(script_id: str, frame_id: str):
    """Deletes a storyboard frame."""
    try:
        updated_script = pipeline.delete_frame(script_id, frame_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CopyFrameRequest(BaseModel):
    frame_id: str
    insert_at: Optional[int] = None

@app.post("/projects/{script_id}/frames/copy", response_model=Script)
def copy_frame(script_id: str, request: CopyFrameRequest):
    """Copies a storyboard frame."""
    try:
        updated_script = pipeline.copy_frame(script_id, request.frame_id, request.insert_at)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ReorderFramesRequest(BaseModel):
    frame_ids: List[str]

@app.put("/projects/{script_id}/frames/reorder", response_model=Script)
def reorder_frames(script_id: str, request: ReorderFramesRequest):
    """Reorders storyboard frames."""
    try:
        updated_script = pipeline.reorder_frames(script_id, request.frame_ids)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class RenderFrameRequest(BaseModel):
    frame_id: str
    composition_data: Optional[Dict[str, Any]] = None
    prompt: str
    batch_size: int = 1


@app.post("/projects/{script_id}/storyboard/render", response_model=Script)
def render_frame(script_id: str, request: RenderFrameRequest):
    """Renders a specific frame using composition data (I2I)."""
    try:
        logger.info(f"Rendering frame {request.frame_id}")
        
        updated_script = pipeline.generate_storyboard_render(
            script_id,
            request.frame_id,
            request.composition_data,
            request.prompt,
            request.batch_size
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error rendering frame {request.frame_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SelectVideoRequest(BaseModel):
    video_id: str


@app.post("/projects/{script_id}/frames/{frame_id}/select_video", response_model=Script)
def select_video(script_id: str, frame_id: str, request: SelectVideoRequest):
    """Selects a video variant for a specific frame."""
    try:
        updated_script = pipeline.select_video_for_frame(script_id, frame_id, request.video_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/auto_select_latest_video", response_model=Script)
def auto_select_latest_video(script_id: str, frame_id: str):
    """Auto-pick the latest completed video as this frame's active take.

    Idempotent; skipped when the frame is pinned (is_video_pinned=True).
    Frontend calls this on every task-completion poll so the freshly
    generated take surfaces on the hero — unless the user has explicitly
    pinned a different take.
    """
    try:
        updated_script = pipeline.auto_select_latest_video(script_id, frame_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/unpin_video", response_model=Script)
def unpin_video(script_id: str, frame_id: str):
    """Clear the manual pin; auto_select_latest_video resumes on next poll.

    Leaves selected_video_id / video_url untouched — the user keeps seeing
    the current take until a new generation produces a newer completed
    task.
    """
    try:
        updated_script = pipeline.unpin_video(script_id, frame_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExtractLastFrameRequest(BaseModel):
    video_task_id: str


@app.post("/projects/{script_id}/frames/{frame_id}/extract_last_frame")
def extract_last_frame(script_id: str, frame_id: str, request: ExtractLastFrameRequest):
    """Extract the last frame from a completed video and add it as a variant to the frame's rendered_image_asset."""
    try:
        updated_script = pipeline.extract_last_frame(script_id, frame_id, request.video_task_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception(f"Error extracting last frame: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/frames/{frame_id}/upload_image")
def upload_frame_image(script_id: str, frame_id: str, file: UploadFile = File(...)):
    """Upload an image as a variant for a frame's rendered_image_asset."""
    try:
        # Save file locally first
        file_ext = _safe_upload_ext(file.filename)
        filename = f"{uuid.uuid4()}{file_ext}"
        file_path = os.path.join("output/uploads", filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        updated_script = pipeline.upload_frame_image(script_id, frame_id, file_path)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Error uploading frame image: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Issue 10: T2I 首帧 upload — uploaded image is appended to the frame's
# t2i_image_urls history and becomes the active首帧 immediately, unlocking
# Step 2 (I2V generation). Whitelisted formats + 8 MB cap reflect that this
# is a user-facing creator upload, not an arbitrary file store.
_T2I_UPLOAD_MAX_BYTES = 8 * 1024 * 1024
_T2I_UPLOAD_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@app.post("/projects/{script_id}/frames/{frame_id}/upload_t2i")
async def upload_t2i_frame(script_id: str, frame_id: str, file: UploadFile = File(...)):
    """Upload an external image as a T2I首帧 candidate for an I2V flow.

    Validation:
      - ≤ 8 MB (rejected with 413 to differentiate from validation 400)
      - jpg/jpeg/png/webp only (rejected with 415)

    On success, returns the updated StoryboardFrame so the client can read
    back `t2i_image_urls` and `t2i_selected_index` without an extra round-
    trip. The uploaded image becomes the active首帧 (auto-selected).
    """
    try:
        original_name = (file.filename or "").strip()
        if not original_name:
            raise HTTPException(status_code=400, detail="No filename provided")
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in _T2I_UPLOAD_ALLOWED_EXTS:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type {ext!r}. Allowed: {sorted(_T2I_UPLOAD_ALLOWED_EXTS)}",
            )

        # Stream-to-disk with explicit byte cap so we never load >8 MB
        # into memory if a client lies about Content-Length.
        filename = f"t2i_{uuid.uuid4().hex}{ext}"
        rel_path = os.path.join("uploads", filename)
        abs_path = os.path.join("output", rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        size = 0
        try:
            with open(abs_path, "wb") as buffer:
                while chunk := await file.read(64 * 1024):
                    size += len(chunk)
                    if size > _T2I_UPLOAD_MAX_BYTES:
                        buffer.close()
                        os.unlink(abs_path)
                        raise HTTPException(
                            status_code=413,
                            detail=f"File exceeds {_T2I_UPLOAD_MAX_BYTES // (1024 * 1024)} MB limit",
                        )
                    buffer.write(chunk)
        except HTTPException:
            raise
        except Exception as e:
            # Clean up partial file on any unexpected error
            if os.path.exists(abs_path):
                try:
                    os.unlink(abs_path)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail=f"Upload write failed: {e}")

        # `pipeline.upload_t2i_frame` synchronously serializes + writes the
        # whole projects.json (~50-500KB) — wrap in to_thread so we don't
        # block the event loop for the ~50ms write. Matches the pattern
        # used by create_project / reparse_project / analyze_script_for_styles.
        loop = asyncio.get_event_loop()
        frame = await loop.run_in_executor(
            None,
            partial(pipeline.upload_t2i_frame, script_id, frame_id, rel_path),
        )
        if frame is None:
            # Roll back the file — frame/script gone, no reference will exist
            if os.path.exists(abs_path):
                try:
                    os.unlink(abs_path)
                except OSError:
                    pass
            raise HTTPException(status_code=404, detail="Script or frame not found")
        return signed_response(frame)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("upload_t2i_frame unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/merge", response_model=Script)
def merge_videos(script_id: str):
    """Merge all selected frame videos into final output"""
    import traceback
    try:
        merged_script = pipeline.merge_videos(script_id)
        return signed_response(merged_script)
    except ValueError as e:
        # Known validation errors (no videos, etc.)
        logger.error(f"[MERGE ERROR] Validation failed: {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # FFmpeg or processing errors
        logger.error(f"[MERGE ERROR] Runtime error: {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"[MERGE ERROR] Unexpected error: {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=f"Merge failed: {str(e)}")


# ===== Export Endpoint =====

class ExportRequest(BaseModel):
    resolution: str = "1080p"
    format: str = "mp4"
    subtitles: str = "none"

@app.post("/projects/{script_id}/export")
def export_project(script_id: str, request: ExportRequest):
    """Export project video by merging all selected frame videos.

    Currently delegates to the existing merge_videos pipeline.
    resolution/format/subtitles parameters are accepted but not yet applied
    (requires FFmpeg pipeline iteration).
    """
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Project not found")

        # If already merged, return existing URL directly
        if script.merged_video_url:
            return signed_response({"url": script.merged_video_url})

        # Otherwise, run merge pipeline
        merged_script = pipeline.merge_videos(script_id)
        return signed_response({"url": merged_script.merged_video_url})
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"[EXPORT ERROR] {e}")
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


# ===== Art Direction Endpoints =====

class AnalyzeStyleRequest(BaseModel):
    script_text: str


class SaveArtDirectionRequest(BaseModel):
    selected_style_id: str
    style_config: Dict[str, Any]
    custom_styles: List[Dict[str, Any]] = []
    ai_recommendations: List[Dict[str, Any]] = []


@app.post("/projects/{script_id}/art_direction/analyze")
async def analyze_script_for_styles(script_id: str, request: AnalyzeStyleRequest):
    """Analyze script content and recommend visual styles using LLM"""
    try:
        # Get the script to ensure it exists
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Script not found")

        # Use LLM to analyze and recommend styles (run in thread pool to avoid blocking, Python 3.8 compatible)
        custom_style = getattr(getattr(script, "prompt_config", None), "style_analysis", "")
        loop = asyncio.get_event_loop()
        recommendations = await loop.run_in_executor(
            None,  # Use default executor
            partial(pipeline.script_processor.analyze_script_for_styles, request.script_text, custom_style)
        )

        return {"recommendations": recommendations}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/art_direction/clear")
def clear_project_art_direction(script_id: str):
    """R2V v2 Phase 2 — clear project-level art_direction so the
    episode falls back to series baseline (inherit). Used by the
    Style step '重置为系列' button."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    script.art_direction = None
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return signed_response(script)


@app.put("/projects/{script_id}/last_episode_summary")
def update_last_episode_summary(script_id: str, payload: dict):
    """R2V v2 Phase P1-b — manually edit the cached AI summary.
    Body: {"ai_summary": "user-edited text"} or null to clear."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    summary = payload.get("ai_summary")
    if summary is None:
        script.last_episode_summary_cache = None
        script.last_episode_summary_revision = None
    else:
        script.last_episode_summary_cache = str(summary).strip()
        # When user manually edits, mark the revision as the current
        # previous-episode revision so it doesn't show stale.
        if script.series_id:
            series = pipeline.get_series(script.series_id)
            if series:
                try:
                    idx = series.episode_ids.index(script_id)
                    if idx > 0:
                        prev = pipeline.get_script(series.episode_ids[idx - 1])
                        if prev:
                            script.last_episode_summary_revision = _prev_text_revision(prev.original_text or "")
                except ValueError:
                    pass
    script.updated_at = time.time()
    pipeline.scripts[script_id] = script
    pipeline._save_data()
    return signed_response(script)


@app.post("/projects/{script_id}/art_direction/save", response_model=Script)
def save_art_direction(script_id: str, request: SaveArtDirectionRequest):
    """Save Art Direction configuration to the project"""
    try:
        updated_script = pipeline.save_art_direction(
            script_id,
            request.selected_style_id,
            request.style_config,
            request.custom_styles,
            request.ai_recommendations
        )
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/art_direction/presets")
def get_style_presets():
    """Get built-in style presets (v2: categories + presets)"""
    try:
        import json
        import os
        preset_file = os.path.join(os.path.dirname(__file__), "style_presets.json")

        if not os.path.exists(preset_file):
            return {"version": 2, "categories": [], "presets": []}

        with open(preset_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict) and data.get("version") == 2:
                return data
            # Legacy fallback: plain array
            return {"version": 2, "categories": [], "presets": data if isinstance(data, list) else []}
    except Exception as e:
        logger.exception("Failed to load style presets")
        raise HTTPException(status_code=500, detail=str(e))


# NOTE: /storyboard/polish_prompt removed - use /storyboard/refine_prompt instead


def _get_custom_prompt(script_id: str, field: str) -> str:
    """Read a custom prompt with 3-level fallback: Episode → Series → system default.
    Returns empty string if result equals system default (so LLM method uses its built-in)."""
    if not script_id:
        return ""
    script = pipeline.get_script(script_id)
    if not script:
        return ""
    series = pipeline.get_series(script.series_id) if script.series_id else None
    effective = pipeline.get_effective_prompt(field, script, series)
    # If it's the system default, return empty so the LLM method uses its built-in default
    from .llm import DEFAULT_STORYBOARD_POLISH_PROMPT, DEFAULT_VIDEO_POLISH_PROMPT, DEFAULT_R2V_POLISH_PROMPT
    defaults = {
        "storyboard_polish": DEFAULT_STORYBOARD_POLISH_PROMPT,
        "video_polish": DEFAULT_VIDEO_POLISH_PROMPT,
        "r2v_polish": DEFAULT_R2V_POLISH_PROMPT,
    }
    if effective == defaults.get(field, ""):
        return ""
    return effective


def _get_polish_model_for_project(script_id: str) -> str:
    """Read polish_model with 3-level fallback: Episode.prompt_config → Series.prompt_config → "".
    Empty = LLMAdapter uses its default (qwen3.6-plus). The frontend's polish-model dropdown
    in PromptConfig modal writes here; backend just reads."""
    if not script_id:
        return ""
    script = pipeline.get_script(script_id)
    if not script:
        return ""
    # Episode first
    ep_pc = getattr(script, "prompt_config", None)
    if ep_pc and getattr(ep_pc, "polish_model", ""):
        return ep_pc.polish_model
    # Series fallback
    if getattr(script, "series_id", None):
        series = pipeline.get_series(script.series_id)
        if series:
            s_pc = getattr(series, "prompt_config", None)
            if s_pc and getattr(s_pc, "polish_model", ""):
                return s_pc.polish_model
    return ""


class PolishVideoPromptRequest(BaseModel):
    draft_prompt: str
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement
    script_id: str = ""  # Optional: project ID to load custom prompt config
    # 迭代时传入上一次的 CN 作为双语锚点，让模型同步修改双语；首次留空。
    prev_cn: str = ""
    # I2V 模式：首帧图 URL(s)。Vision-capable polish 模型会真正看见图像并
    # 用其指导润色（光影、构图、被摄主体等）。空列表 = 纯文本润色。
    image_urls: List[str] = Field(default_factory=list, max_length=4)
    # 显式覆盖 polish 用的 LLM 模型；空 = 用 project / series PromptConfig
    # 的 polish_model（再 fallback 到 system default）。
    polish_model: str = ""


def _polish_error_response(err) -> Dict[str, Any]:
    """把 PolishError 转成统一的 502 响应体。
    model_echo 时附带原文双语供前端做 warning 渲染。"""
    body: Dict[str, Any] = {
        "reason": err.reason,
        "message_zh": err.message_zh,
        "message_en": err.message_en,
    }
    if err.prompt_cn:
        body["prompt_cn"] = err.prompt_cn
    if err.prompt_en:
        body["prompt_en"] = err.prompt_en
    return body


@app.post("/video/polish_prompt")
def polish_video_prompt(request: PolishVideoPromptRequest):
    """Polishes a video generation prompt using LLM. Returns bilingual prompts.

    NOTE: Defined as a SYNC handler on purpose. The body calls
    `processor.polish_video_prompt()` which makes a blocking HTTP call to
    DashScope (openai sync client). If declared `async def`, FastAPI would
    run it on the event loop itself — blocking ALL other endpoints for the
    10-30s LLM call (e.g. concurrent `GET /prompt_config` from the modal
    looks "stuck loading"). Sync handlers get auto-dispatched to anyio's
    threadpool, freeing the event loop for other requests.

    成功：200 + {prompt_cn, prompt_en}
    失败：502 + {reason, message_zh, message_en, prompt_cn?, prompt_en?}
      其中 reason ∈ {is_configured_false, api_error, json_parse_error,
                     missing_keys, model_echo}。
      model_echo 是 warning 性质（带原文双语），其余是 hard error。
    """
    from .llm import PolishError
    try:
        custom_prompt = _get_custom_prompt(request.script_id, "video_polish")
        # Polish model: request override → project/series PromptConfig → ""
        polish_model = request.polish_model or _get_polish_model_for_project(request.script_id)
        processor = ScriptProcessor()
        result = processor.polish_video_prompt(
            request.draft_prompt,
            request.feedback,
            custom_prompt,
            request.prev_cn,
            image_urls=request.image_urls or None,
            polish_model=polish_model,
        )
        return {
            "prompt_cn": result.get("prompt_cn", ""),
            "prompt_en": result.get("prompt_en", "")
        }
    except PolishError as e:
        logger.warning("polish_video_prompt failed: %s", e)
        raise HTTPException(status_code=502, detail=_polish_error_response(e))
    except Exception as e:
        logger.exception("polish_video_prompt unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


class RefSlot(BaseModel):
    description: str  # Character name, e.g., "雷震", "白兔"


class PolishR2VPromptRequest(BaseModel):
    draft_prompt: str
    slots: List[RefSlot]
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement
    script_id: str = ""  # Optional: project ID to load custom prompt config
    prev_cn: str = ""  # 双语锚点迭代用，首次留空
    # R2V 模式：用户挂载的 character1/2/3 参考图 URL(s)，让 vision 模型
    # 看清各角色实际形象。空列表 = 纯文本润色（兼容旧调用方）。
    image_urls: List[str] = Field(default_factory=list, max_length=9)
    polish_model: str = ""


@app.post("/video/polish_r2v_prompt")
def polish_r2v_prompt(request: PolishR2VPromptRequest):
    """Polishes a R2V (Reference-to-Video) prompt using LLM. Returns bilingual prompts.
    错误约定同 /video/polish_prompt。
    SYNC handler on purpose — see polish_video_prompt for rationale."""
    from .llm import PolishError
    try:
        custom_prompt = _get_custom_prompt(request.script_id, "r2v_polish")
        polish_model = request.polish_model or _get_polish_model_for_project(request.script_id)
        processor = ScriptProcessor()
        slot_info = [{"description": s.description} for s in request.slots]
        result = processor.polish_r2v_prompt(
            request.draft_prompt,
            slot_info,
            request.feedback,
            custom_prompt,
            request.prev_cn,
            image_urls=request.image_urls or None,
            polish_model=polish_model,
        )
        return {
            "prompt_cn": result.get("prompt_cn", ""),
            "prompt_en": result.get("prompt_en", "")
        }
    except PolishError as e:
        logger.warning("polish_r2v_prompt failed: %s", e)
        raise HTTPException(status_code=502, detail=_polish_error_response(e))
    except Exception as e:
        logger.exception("polish_r2v_prompt unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


# ===== Environment Configuration Endpoints =====

def _check_mulerun_cli_status() -> bool:
    """Check if MuleRun CLI is installed and logged in."""
    try:
        import shutil, subprocess
        if shutil.which("mulerun") is None:
            return False
        result = subprocess.run(
            ["mulerun", "login", "status"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


@app.post("/config/mulerun-login")
def trigger_mulerun_login():
    """Trigger mulerun login — opens browser for OAuth."""
    import shutil, subprocess
    if shutil.which("mulerun") is None:
        raise HTTPException(status_code=400, detail="MuleRun CLI 未安装。请先运行: npm i -g @mulerunai/cli")
    try:
        subprocess.Popen(
            ["mulerun", "login"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"status": "ok", "message": "浏览器已打开，请完成登录"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动登录失败: {e}")


# Credential-like env fields that must never be returned in plaintext.
SECRET_FIELDS = {
    "DASHSCOPE_API_KEY",
    "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "KLING_ACCESS_KEY",
    "KLING_SECRET_KEY",
    "VIDU_API_KEY",
    "MULEROUTER_API_KEY",
}

# Bullet sentinel: never appears in a real key, so the save path can detect an
# unchanged (still-masked) field and avoid overwriting the stored secret.
_MASK_CHAR = "\u2022"


def _mask_secret(value: Optional[str]) -> str:
    """Return a masked representation (bullets + last 4 chars) for a configured
    secret, or an empty string when it is unset. Never reveals the full value."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 4:
        return _MASK_CHAR * len(v)
    return _MASK_CHAR * 8 + v[-4:]


@app.get("/config/env")
def get_env_config():
    """Get current environment configuration.

    Secrets are masked (bullets + last 4 chars) and never returned in
    plaintext. `secrets_configured` reports which credential fields are set so
    the frontend can drive required-field / validation logic without the raw
    value. Non-secret config (OSS bucket/endpoint/base path, provider modes,
    endpoint overrides) is returned as-is."""
    try:
        from ...utils.endpoints import PROVIDER_DEFAULTS
        from ...utils.oss_utils import is_oss_enabled
        endpoint_overrides = {}
        for provider in PROVIDER_DEFAULTS:
            env_key = f"{provider}_BASE_URL"
            value = os.getenv(env_key)
            if value:
                endpoint_overrides[env_key] = value

        secrets_configured = {
            field: bool((os.getenv(field, "") or "").strip())
            for field in SECRET_FIELDS
        }

        return {
            # Masked secrets — never plaintext.
            "DASHSCOPE_API_KEY": _mask_secret(os.getenv("DASHSCOPE_API_KEY")),
            "ALIBABA_CLOUD_ACCESS_KEY_ID": _mask_secret(os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")),
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": _mask_secret(os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")),
            "KLING_ACCESS_KEY": _mask_secret(os.getenv("KLING_ACCESS_KEY")),
            "KLING_SECRET_KEY": _mask_secret(os.getenv("KLING_SECRET_KEY")),
            "VIDU_API_KEY": _mask_secret(os.getenv("VIDU_API_KEY")),
            "MULEROUTER_API_KEY": _mask_secret(os.getenv("MULEROUTER_API_KEY")),
            # Non-secret config.
            "OSS_BUCKET_NAME": os.getenv("OSS_BUCKET_NAME", ""),
            "OSS_ENDPOINT": os.getenv("OSS_ENDPOINT", ""),
            "OSS_BASE_PATH": os.getenv("OSS_BASE_PATH", ""),
            "OSS_ENABLE": is_oss_enabled(),
            "MULERUN_CLI_LOGGED_IN": _check_mulerun_cli_status(),
            "KLING_PROVIDER_MODE": _normalize_provider_mode(os.getenv("KLING_PROVIDER_MODE")),
            "VIDU_PROVIDER_MODE": _normalize_provider_mode(os.getenv("VIDU_PROVIDER_MODE")),
            "PIXVERSE_PROVIDER_MODE": _normalize_provider_mode(os.getenv("PIXVERSE_PROVIDER_MODE")),
            "endpoint_overrides": endpoint_overrides,
            "secrets_configured": secrets_configured,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))





# ============================================
# Prop CRUD Endpoints
# ============================================

class CreatePropRequest(BaseModel):
    name: str
    description: str = ""

@app.post("/projects/{script_id}/props")
def create_prop(script_id: str, request: CreatePropRequest):
    """Creates a new prop in the project."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")

    import uuid
    from .models import Prop, GenerationStatus

    new_prop = Prop(
        id=f"prop_{uuid.uuid4().hex[:8]}",
        name=request.name,
        description=request.description,
        status=GenerationStatus.PENDING
    )

    script.props.append(new_prop)
    script.updated_at = time.time()
    pipeline._save_data()

    return signed_response(script)


@app.delete("/projects/{script_id}/props/{prop_id}")
def delete_prop(script_id: str, prop_id: str):
    """Deletes a prop from the project."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")

    original_count = len(script.props)
    script.props = [p for p in script.props if p.id != prop_id]

    if len(script.props) == original_count:
        raise HTTPException(status_code=404, detail="Prop not found")

    # Remove prop references from frames
    for frame in script.frames:
        if prop_id in frame.prop_ids:
            frame.prop_ids.remove(prop_id)

    script.updated_at = time.time()
    pipeline._save_data()

    return signed_response(script)


# ─────────────────────────────────────────────────────────────────────────────
# Document CRUD — Script Editor document persistence
# ─────────────────────────────────────────────────────────────────────────────

from pathlib import Path
from datetime import datetime, timezone

_TRON_PROJECTS_DIR = Path.home() / ".tron" / "comic" / "projects"


class SaveDocumentRequest(BaseModel):
    content: dict  # Tiptap JSON document
    create_snapshot: bool = False


class DocumentSnapshotInfo(BaseModel):
    timestamp: str  # ISO format
    size_bytes: int


def _get_project_dir(project_id: str) -> Path:
    """Return the project directory, raising 404 if it doesn't exist."""
    project_dir = _TRON_PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return project_dir


def _ensure_history_dir(project_dir: Path) -> Path:
    """Ensure history/ subdirectory exists and return its path."""
    history_dir = project_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir


def _create_snapshot(project_dir: Path) -> None:
    """Create a timestamped snapshot of the current document.json."""
    doc_path = project_dir / "document.json"
    if not doc_path.exists():
        return
    history_dir = _ensure_history_dir(project_dir)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    snapshot_path = history_dir / f"{ts}.json"
    shutil.copy2(str(doc_path), str(snapshot_path))


@app.post("/projects/{project_id}/document")
def save_document(project_id: str, request: SaveDocumentRequest):
    """Save the Tiptap JSON document for a project."""
    project_dir = _TRON_PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    doc_path = project_dir / "document.json"

    # Optionally create a snapshot before overwriting
    if request.create_snapshot and doc_path.exists():
        _create_snapshot(project_dir)

    try:
        with open(doc_path, "w", encoding="utf-8") as f:
            json.dump(request.content, f, ensure_ascii=False, indent=2)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save document: {e}")

    return {"status": "ok", "size_bytes": doc_path.stat().st_size}


@app.get("/projects/{project_id}/document")
def load_document(project_id: str):
    """Load the Tiptap JSON document for a project."""
    project_dir = _TRON_PROJECTS_DIR / project_id
    doc_path = project_dir / "document.json"

    if not doc_path.exists():
        return {"type": "doc", "content": []}

    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            content = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to load document: {e}")

    return content


@app.get("/projects/{project_id}/document/snapshots")
def list_document_snapshots(project_id: str):
    """List all document snapshots for a project, ordered by time descending."""
    project_dir = _get_project_dir(project_id)
    history_dir = project_dir / "history"

    if not history_dir.exists():
        return []

    snapshots = []
    for f in history_dir.iterdir():
        if f.suffix == ".json" and f.is_file():
            snapshots.append(
                DocumentSnapshotInfo(
                    timestamp=f.stem,
                    size_bytes=f.stat().st_size,
                )
            )

    # Sort by timestamp descending
    snapshots.sort(key=lambda s: s.timestamp, reverse=True)
    return snapshots


@app.post("/projects/{project_id}/document/snapshots")
def create_document_snapshot(project_id: str):
    """Create a snapshot of the current document."""
    project_dir = _get_project_dir(project_id)
    doc_path = project_dir / "document.json"

    if not doc_path.exists():
        raise HTTPException(status_code=404, detail="No document to snapshot")

    _create_snapshot(project_dir)
    return {"status": "ok"}


@app.post("/projects/{project_id}/document/snapshots/{timestamp}/restore")
def restore_document_snapshot(project_id: str, timestamp: str):
    """Restore a document from a snapshot."""
    project_dir = _get_project_dir(project_id)
    history_dir = project_dir / "history"
    snapshot_path = history_dir / f"{timestamp}.json"

    if not snapshot_path.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")

    doc_path = project_dir / "document.json"

    try:
        shutil.copy2(str(snapshot_path), str(doc_path))
        with open(doc_path, "r", encoding="utf-8") as f:
            content = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to restore snapshot: {e}")

    return content


# ═══════════════════════════════════════════════════════════════
# Document Import/Export
# ═══════════════════════════════════════════════════════════════


class ImportDocRequest(BaseModel):
    filename: str
    content: str  # base64 encoded file content
    file_type: str  # "fdx" | "fountain" | "txt"


class ExportDocRequest(BaseModel):
    content: dict  # Tiptap JSON document
    format: str  # "pdf" | "docx"
    options: dict = {}


def _parse_fdx(xml_text: str) -> dict:
    """Parse FDX (Final Draft XML) into Tiptap JSON."""
    import re
    from xml.etree.ElementTree import ParseError

    # defusedxml guards against entity-expansion attacks (XML bomb).
    # Hard dependency (declared in requirements.txt) so the parse path is
    # always the hardened one; no stdlib fallback.
    from defusedxml.ElementTree import fromstring as _xml_fromstring

    # Reject DTD/entity declarations outright (defense-in-depth;
    # FDX files never legitimately contain them)
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise HTTPException(status_code=400, detail="FDX XML with DTD/entity declarations is not allowed")

    FDX_TO_NODE: dict = {
        "Scene Heading": "sceneHeading",
        "Action": "action",
        "Character": "characterCue",
        "Dialogue": "dialogue",
        "Parenthetical": "parenthetical",
        "Transition": "transition",
    }

    try:
        root = _xml_fromstring(xml_text)
    except (ParseError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid FDX XML: {e}")

    content_nodes = []
    # Find all Paragraph elements (support both with and without Content wrapper)
    paragraphs = root.findall(".//Paragraph")

    for para in paragraphs:
        para_type = para.get("Type", "Action")
        node_type = FDX_TO_NODE.get(para_type, "action")

        # Extract text from Text elements
        texts = []
        for text_el in para.findall(".//Text"):
            text_content = text_el.text or ""
            style = text_el.get("Style", "")
            marks = []
            if "Bold" in style:
                marks.append({"type": "bold"})
            if "Italic" in style:
                marks.append({"type": "italic"})
            if "Underline" in style:
                marks.append({"type": "underline"})

            text_node: dict = {"type": "text", "text": text_content}
            if marks:
                text_node["marks"] = marks
            texts.append(text_node)

        if not texts:
            texts = [{"type": "text", "text": ""}]

        node: dict = {"type": node_type, "content": texts}

        # Add attributes for scene headings
        if node_type == "sceneHeading":
            full_text = "".join(t.get("text", "") for t in texts)
            match = re.match(r"^(INT|EXT|INT/EXT|I/E)[\./\s]", full_text, re.IGNORECASE)
            attrs: dict = {"id": str(uuid.uuid4())}
            if match:
                attrs["intExt"] = match.group(1).upper()
            node["attrs"] = attrs

        content_nodes.append(node)

    return {"type": "doc", "content": content_nodes}


def _parse_fountain(text: str) -> dict:
    """Parse Fountain format text into Tiptap JSON."""
    import re

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content_nodes = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Empty lines - skip
        if not stripped:
            i += 1
            continue

        # Forced scene heading (starts with .)
        if stripped.startswith(".") and len(stripped) > 1 and not stripped.startswith(".."):
            heading_text = stripped[1:]
            match = re.match(r"^(INT|EXT|INT/EXT|I/E)[\./\s]", heading_text, re.IGNORECASE)
            attrs: dict = {"id": str(uuid.uuid4())}
            if match:
                attrs["intExt"] = match.group(1).upper()
            content_nodes.append({
                "type": "sceneHeading",
                "attrs": attrs,
                "content": [{"type": "text", "text": heading_text}],
            })
            i += 1
            continue

        # Scene heading: INT./EXT. pattern
        if re.match(r"^(INT|EXT|INT/EXT|I/E)[\./\s]", stripped, re.IGNORECASE):
            match = re.match(r"^(INT|EXT|INT/EXT|I/E)", stripped, re.IGNORECASE)
            attrs = {"id": str(uuid.uuid4())}
            if match:
                attrs["intExt"] = match.group(1).upper()
            content_nodes.append({
                "type": "sceneHeading",
                "attrs": attrs,
                "content": [{"type": "text", "text": stripped}],
            })
            i += 1
            continue

        # Transition: all caps ending with colon, or starts with >
        if stripped.startswith(">"):
            t_text = stripped[1:].strip()
            content_nodes.append({
                "type": "transition",
                "content": [{"type": "text", "text": t_text}],
            })
            i += 1
            continue

        if (
            stripped.isupper()
            and stripped.endswith(":")
            and len(stripped) > 2
            and not stripped.startswith("(")
        ):
            content_nodes.append({
                "type": "transition",
                "content": [{"type": "text", "text": stripped}],
            })
            i += 1
            continue

        # Character cue: all uppercase, preceded by empty line (check)
        # In Fountain spec: a line of all-uppercase text
        if (
            stripped.isupper()
            and len(stripped) > 1
            and not stripped.endswith(":")
            and (i == 0 or not lines[i - 1].strip())
        ):
            content_nodes.append({
                "type": "characterCue",
                "content": [{"type": "text", "text": stripped}],
            })
            i += 1

            # Following lines until empty line are dialogue/parenthetical
            while i < len(lines) and lines[i].strip():
                dline = lines[i].strip()
                if dline.startswith("(") and dline.endswith(")"):
                    content_nodes.append({
                        "type": "parenthetical",
                        "content": [{"type": "text", "text": dline}],
                    })
                else:
                    content_nodes.append({
                        "type": "dialogue",
                        "content": [{"type": "text", "text": dline}],
                    })
                i += 1
            continue

        # Default: Action
        content_nodes.append({
            "type": "action",
            "content": [{"type": "text", "text": stripped}],
        })
        i += 1

    return {"type": "doc", "content": content_nodes}


def _parse_txt(text: str) -> dict:
    """Parse plain text into Tiptap JSON (all as action nodes)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content_nodes = []

    for line in lines:
        if line.strip():
            content_nodes.append({
                "type": "action",
                "content": [{"type": "text", "text": line.strip()}],
            })

    if not content_nodes:
        content_nodes = [{"type": "action", "content": [{"type": "text", "text": ""}]}]

    return {"type": "doc", "content": content_nodes}


@app.post("/projects/{project_id}/document/import")
def import_document(project_id: str, req: ImportDocRequest):
    """Import FDX/Fountain/TXT file and convert to Tiptap JSON."""
    import base64

    try:
        raw_bytes = base64.b64decode(req.content)
        text = raw_bytes.decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to decode file content: {e}")

    file_type = req.file_type.lower()

    if file_type == "fdx":
        doc = _parse_fdx(text)
    elif file_type == "fountain":
        doc = _parse_fountain(text)
    elif file_type == "txt":
        doc = _parse_txt(text)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_type}")

    return {"content": doc, "filename": req.filename, "file_type": file_type}


@app.post("/projects/{project_id}/document/export")
def export_document(project_id: str, req: ExportDocRequest):
    """Export Tiptap JSON to PDF/DOCX. Returns HTML fallback if libraries unavailable."""
    from fastapi.responses import Response

    doc = req.content
    fmt = req.format.lower()

    # Helper to extract plain text from Tiptap doc
    def extract_text(node: dict) -> str:
        if node.get("text"):
            return node["text"]
        children = node.get("content", [])
        return "".join(extract_text(c) for c in children)

    def doc_to_lines(doc: dict) -> list:
        lines = []
        for node in doc.get("content", []):
            node_type = node.get("type", "action")
            text = extract_text(node)
            lines.append((node_type, text))
        return lines

    if fmt == "pdf":
        # Try reportlab first, fallback to HTML
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.units import inch
            from reportlab.platypus import SimpleDocTemplate, Paragraph as RLParagraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.enums import TA_CENTER, TA_RIGHT
            import io

            buffer = io.BytesIO()
            doc_pdf = SimpleDocTemplate(buffer, pagesize=letter,
                                        leftMargin=1.5 * inch, rightMargin=1 * inch,
                                        topMargin=1 * inch, bottomMargin=1 * inch)

            styles = getSampleStyleSheet()
            styles.add(ParagraphStyle("SceneHeading", parent=styles["Heading3"],
                                      fontName="Helvetica-Bold", fontSize=12,
                                      spaceAfter=6, spaceBefore=18))
            styles.add(ParagraphStyle("CharacterCue", parent=styles["Normal"],
                                      fontName="Helvetica-Bold", fontSize=10,
                                      alignment=TA_CENTER, spaceBefore=12))
            styles.add(ParagraphStyle("DialogueStyle", parent=styles["Normal"],
                                      fontName="Helvetica", fontSize=10,
                                      leftIndent=1.5 * inch, rightIndent=1.5 * inch))
            styles.add(ParagraphStyle("TransitionStyle", parent=styles["Normal"],
                                      fontName="Helvetica-Bold", fontSize=10,
                                      alignment=TA_RIGHT, spaceBefore=12))

            story = []
            lines = doc_to_lines(doc)
            style_map = {
                "sceneHeading": "SceneHeading",
                "characterCue": "CharacterCue",
                "dialogue": "DialogueStyle",
                "transition": "TransitionStyle",
            }

            for node_type, text in lines:
                style_name = style_map.get(node_type, "Normal")
                story.append(RLParagraph(text or " ", styles[style_name]))

            doc_pdf.build(story)
            pdf_bytes = buffer.getvalue()
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="script.pdf"'},
            )

        except ImportError:
            # Fallback: generate printable HTML
            from html import escape as _esc

            lines = doc_to_lines(doc)
            html_parts = [
                "<!DOCTYPE html><html><head><meta charset='utf-8'>",
                "<title>Script Export</title>",
                "<style>body{font-family:Courier,monospace;max-width:7in;margin:1in auto;}",
                ".scene-heading{font-weight:bold;text-transform:uppercase;margin-top:1.5em;}",
                ".character-cue{text-align:center;font-weight:bold;margin-top:1em;text-transform:uppercase;}",
                ".dialogue{margin-left:1.5in;margin-right:1.5in;}",
                ".parenthetical{margin-left:2in;margin-right:1.5in;font-style:italic;}",
                ".transition{text-align:right;font-weight:bold;margin-top:1em;}",
                "</style></head><body>",
            ]
            for node_type, text in lines:
                css_class = {
                    "sceneHeading": "scene-heading",
                    "characterCue": "character-cue",
                    "dialogue": "dialogue",
                    "parenthetical": "parenthetical",
                    "transition": "transition",
                    "action": "action",
                }.get(node_type, "action")
                html_parts.append(f'<p class="{css_class}">{_esc(text)}</p>')
            html_parts.append("</body></html>")
            html_content = "\n".join(html_parts)

            return Response(
                content=html_content.encode("utf-8"),
                media_type="text/html",
                headers={
                    "Content-Disposition": 'attachment; filename="script.html"',
                    "X-Export-Fallback": "true",
                },
            )

    elif fmt == "docx":
        # Try python-docx, fallback to plain text
        try:
            from docx import Document as DocxDocument
            from docx.shared import Pt, Inches
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            import io

            document = DocxDocument()
            lines = doc_to_lines(doc)

            for node_type, text in lines:
                if node_type == "sceneHeading":
                    p = document.add_paragraph()
                    p.style = document.styles["Heading 3"]
                    run = p.add_run(text.upper())
                    run.bold = True
                elif node_type == "characterCue":
                    p = document.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = p.add_run(text.upper())
                    run.bold = True
                elif node_type == "dialogue":
                    p = document.add_paragraph()
                    p.paragraph_format.left_indent = Inches(1.5)
                    p.paragraph_format.right_indent = Inches(1.5)
                    p.add_run(text)
                elif node_type == "transition":
                    p = document.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    run = p.add_run(text.upper())
                    run.bold = True
                else:
                    document.add_paragraph(text)

            buffer = io.BytesIO()
            document.save(buffer)
            docx_bytes = buffer.getvalue()

            return Response(
                content=docx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": 'attachment; filename="script.docx"'},
            )

        except ImportError:
            # Fallback: plain text
            lines = doc_to_lines(doc)
            text_parts = []
            for node_type, text in lines:
                if node_type == "sceneHeading":
                    text_parts.append(f"\n{text.upper()}\n")
                elif node_type == "characterCue":
                    text_parts.append(f"\n    {text.upper()}")
                elif node_type == "dialogue":
                    text_parts.append(f"        {text}")
                elif node_type == "transition":
                    text_parts.append(f"\n{'':>60}{text.upper()}")
                else:
                    text_parts.append(text)
            plain = "\n".join(text_parts)

            return Response(
                content=plain.encode("utf-8"),
                media_type="text/plain",
                headers={
                    "Content-Disposition": 'attachment; filename="script.txt"',
                    "X-Export-Fallback": "true",
                },
            )

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}")


# ═══════════════════════════════════════════════════════════════
# Script Editor - Derivation Sync & Pipeline Integration
# ═══════════════════════════════════════════════════════════════


class SyncDerivationRequest(BaseModel):
    scenes: List[Dict[str, Any]] = Field(default_factory=list)
    characters: List[Dict[str, Any]] = Field(default_factory=list)
    locations: List[str] = Field(default_factory=list)
    estimated_duration: float = 0
    word_count: int = 0
    confidence_score: float = 0


class DeriveGapEntity(BaseModel):
    type: str  # 'character' | 'prop' | 'beat' | 'location'
    name: str
    description: Optional[str] = None
    confidence: float  # 0.0-1.0
    scene_index: Optional[int] = None  # 关联场景


class DeriveGapsResponse(BaseModel):
    entities: List[DeriveGapEntity]
    cached: bool = False  # 是否来自缓存


class DeriveGapsRequest(BaseModel):
    already_extracted: Optional[Dict[str, List[str]]] = None  # {"scenes": [...], "characters": [...]}
    gaps: List[str] = Field(default_factory=lambda: ["characters", "props", "beats", "locations"])
    raw_text_blocks: Optional[List[str]] = None  # 原始文本块（可选，否则从项目文档读取）


class ConfirmShotBlockRequest(BaseModel):
    shot_type: Optional[str] = None
    camera_movement: Optional[str] = None
    duration: Optional[float] = None
    description: Optional[str] = None
    characters: List[str] = Field(default_factory=list)


@app.post("/projects/{project_id}/sync_derivation")
def sync_derivation(project_id: str, req: SyncDerivationRequest):
    """前端推送 L1 派生结果到后端存储"""
    project_dir = _get_project_dir(project_id)
    derivation_path = project_dir / "derivation.json"

    data = {
        "scenes": req.scenes,
        "characters": req.characters,
        "locations": req.locations,
        "estimated_duration": req.estimated_duration,
        "word_count": req.word_count,
        "confidence_score": req.confidence_score,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }

    derivation_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"status": "ok", "synced_at": data["synced_at"]}


# ── derive_gaps cache & helpers ──────────────────────────────────────────
_derive_cache: Dict[str, Tuple[float, List[dict]]] = {}
_DERIVE_CACHE_TTL = 6 * 3600  # 6 hours


def _derive_cache_key(text_blocks: List[str], gaps: List[str]) -> str:
    content = "".join(text_blocks) + "|" + "|".join(sorted(gaps))
    return hashlib.md5(content.encode()).hexdigest()


def _derive_cache_get(key: str) -> Optional[List[dict]]:
    if key in _derive_cache:
        timestamp, result = _derive_cache[key]
        if time.time() - timestamp < _DERIVE_CACHE_TTL:
            return result
        del _derive_cache[key]
    return None


def _build_derive_gaps_prompt(
    text_blocks: List[str],
    gaps: List[str],
    already_extracted: Dict[str, List[str]],
) -> str:
    """Construct the LLM prompt for incremental gap extraction."""
    text_content = "\n---\n".join(text_blocks)

    already_section = ""
    if already_extracted:
        parts = []
        for k, v in already_extracted.items():
            parts.append(f"  - {k}: {', '.join(v)}")
        already_section = (
            "\n\n以下内容已被提取，请勿重复输出：\n" + "\n".join(parts)
        )

    gap_descriptions = {
        "characters": "角色（人物名称、简短描述）",
        "props": "道具（从动作描述中识别关键道具）",
        "beats": "节拍（场景中的情感转折点或关键节奏）",
        "locations": "地点（从场景描述中提取具体地点）",
    }
    requested = ", ".join(gap_descriptions.get(g, g) for g in gaps)

    return f"""你是一名专业的剧本分析助手。请从以下文本中提取缺失的结构化信息。

需要提取的类型：{requested}
{already_section}

请以 JSON 数组格式返回结果，每个元素包含：
- type: 类型（"character" | "prop" | "beat" | "location"）
- name: 名称
- description: 简短描述（可选）
- confidence: 置信度 0.0-1.0
- scene_index: 关联场景序号（从 0 开始，可选）

仅返回 JSON 数组，不要包含其他文字。

# 文本内容
{text_content}"""


def _call_llm_for_gaps(
    text_blocks: List[str],
    gaps: List[str],
    already_extracted: Dict[str, List[str]],
) -> List[DeriveGapEntity]:
    """Call LLM (Qwen) for incremental gap extraction with 2s timeout."""
    from .llm_adapter import LLMAdapter
    from .llm import _strip_markdown_json

    adapter = LLMAdapter()
    if not adapter.is_configured:
        logger.warning("derive_gaps: LLM not configured, returning empty")
        return []

    prompt = _build_derive_gaps_prompt(text_blocks, gaps, already_extracted)
    messages = [{"role": "user", "content": prompt}]

    def _do_call():
        return adapter.chat(
            messages=messages,
            response_format={"type": "json_object"},
        )

    # Timeout control: 2 seconds
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_call)
            raw = future.result(timeout=2)
    except concurrent.futures.TimeoutError:
        logger.warning("derive_gaps: LLM call timed out (2s), returning empty")
        return []
    except Exception as e:
        logger.error(f"derive_gaps: LLM call failed: {e}")
        return []

    # Parse response
    try:
        raw = _strip_markdown_json(raw)
        data = json.loads(raw)
        # Handle both top-level array and {"entities": [...]}
        if isinstance(data, dict):
            data = data.get("entities", data.get("items", []))
        if not isinstance(data, list):
            data = []
    except (json.JSONDecodeError, TypeError):
        logger.warning("derive_gaps: failed to parse LLM JSON response")
        return []

    entities = []
    for item in data:
        if not isinstance(item, dict):
            continue
        entity_type = item.get("type", "")
        name = item.get("name", "")
        if not entity_type or not name:
            continue
        entities.append(DeriveGapEntity(
            type=entity_type,
            name=name,
            description=item.get("description"),
            confidence=max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
            scene_index=item.get("scene_index"),
        ))
    return entities


@app.post("/projects/{project_id}/derive_gaps")
def derive_gaps(project_id: str, req: DeriveGapsRequest):
    """L3 LLM 增量补全：支持缓存、超时降级、增量去重。"""
    project_dir = _get_project_dir(project_id)

    # 1. Resolve text blocks
    text_blocks = req.raw_text_blocks
    if not text_blocks:
        # Fallback: try to read project script file
        script_path = project_dir / "script.json"
        if script_path.exists():
            try:
                script_data = json.loads(script_path.read_text(encoding="utf-8"))
                scenes = script_data.get("scenes", [])
                text_blocks = [s.get("description", "") or s.get("name", "") for s in scenes if isinstance(s, dict)]
            except Exception:
                text_blocks = []
        if not text_blocks:
            return DeriveGapsResponse(entities=[], cached=False)

    # 2. Check cache
    cache_key = _derive_cache_key(text_blocks, req.gaps)
    cached_result = _derive_cache_get(cache_key)
    if cached_result is not None:
        entities = [DeriveGapEntity(**e) for e in cached_result]
        # Filter against already_extracted even for cached results
        if req.already_extracted:
            entities = _filter_already_extracted(entities, req.already_extracted)
        return DeriveGapsResponse(entities=entities, cached=True)

    # 3. Call LLM
    already = req.already_extracted or {}
    entities = _call_llm_for_gaps(text_blocks, req.gaps, already)

    # 4. Filter duplicates against already_extracted
    if req.already_extracted:
        entities = _filter_already_extracted(entities, req.already_extracted)

    # 5. Store in cache
    _derive_cache[cache_key] = (time.time(), [e.dict() for e in entities])

    return DeriveGapsResponse(entities=entities, cached=False)


def _filter_already_extracted(
    entities: List[DeriveGapEntity],
    already_extracted: Dict[str, List[str]],
) -> List[DeriveGapEntity]:
    """Remove entities whose name already appears in already_extracted."""
    # Build a set of known names (normalized)
    known_names: set = set()
    for names_list in already_extracted.values():
        for n in names_list:
            known_names.add(n.strip().lower())
    return [e for e in entities if e.name.strip().lower() not in known_names]


@app.post("/projects/{project_id}/shot_blocks/{shot_id}/confirm")
def confirm_shot_block(project_id: str, shot_id: str, req: ConfirmShotBlockRequest):
    """确认 ShotBlock，更新状态为 confirmed"""
    project_dir = _get_project_dir(project_id)
    shot_blocks_path = project_dir / "shot_blocks.json"

    # Load existing shot_blocks or create empty dict
    if shot_blocks_path.exists():
        shot_blocks = json.loads(shot_blocks_path.read_text(encoding="utf-8"))
    else:
        shot_blocks = {}

    # Build the confirmed shot block entry
    confirmed = {
        "shot_id": shot_id,
        "status": "confirmed",
        "shot_type": req.shot_type,
        "camera_movement": req.camera_movement,
        "duration": req.duration,
        "description": req.description,
        "characters": req.characters,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }

    shot_blocks[shot_id] = confirmed

    shot_blocks_path.write_text(
        json.dumps(shot_blocks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return confirmed
