from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import os
import shutil
import uuid
import logging
import traceback
from .pipeline import ComicGenPipeline
from .models import (
    AtelierAgentTurn,
    AtelierAgentPlan,
    AtelierAgentPlannerPackage,
    AtelierNode,
    AtelierProject,
    AtelierSequenceEntry,
    PromptConfig,
    ProviderBackend,
    ProviderRoutingConfig,
    Script,
    Series,
    VideoTask,
)
from .llm import ScriptProcessor, DEFAULT_STORYBOARD_POLISH_PROMPT, DEFAULT_VIDEO_POLISH_PROMPT, DEFAULT_R2V_POLISH_PROMPT
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


# Initialize pipeline
pipeline = ComicGenPipeline()

@app.get("/debug/config")
async def debug_config():
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
    generation_type: str = "all"  # 'full_body', 'three_view', 'headshot', 'all'
    prompt: Optional[str] = None
    apply_style: bool = True
    negative_prompt: Optional[str] = None
    batch_size: int = 1
    model_name: Optional[str] = None

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


@app.get("/system/check")
async def check_system():
    """Check system dependencies (ffmpeg, etc.) and configuration."""
    from utils.system_check import run_system_checks
    return run_system_checks()





@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Uploads a file and returns its URL (OSS if configured, else local)."""
    try:
        file_ext = os.path.splitext(file.filename)[1]
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
async def upload_asset(
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
        file_ext = os.path.splitext(file.filename)[1]
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


@app.post("/projects", response_model=Script)
async def create_project(request: CreateProjectRequest, skip_analysis: bool = False):
    """Creates a new project from a novel text."""
    # Run in thread pool to avoid blocking event loop during LLM analysis (Python 3.8 compatible)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,  # Use default executor
        partial(pipeline.create_project, request.title, request.text, skip_analysis, request.workflow_mode)
    )
    return signed_response(result)



class ReparseProjectRequest(BaseModel):
    text: str


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


# ============================================================
# Atelier Canvas Core
# ============================================================

class CreateAtelierProjectRequest(BaseModel):
    title: str
    description: str = ""
    source_project_id: Optional[str] = None


class UpdateAtelierProjectRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    source_project_id: Optional[str] = None


class UpdateAtelierAgentPolicyRequest(BaseModel):
    approval_mode: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    max_nodes_per_action: Optional[int] = None


class CreateAtelierNodeRequest(BaseModel):
    type: str = "idea"
    title: str = ""
    prompt: str = ""
    status: str = "draft"
    x: float = 0.0
    y: float = 0.0
    width: float = 320.0
    height: float = 180.0
    source_project_id: Optional[str] = None
    frame_id: Optional[str] = None
    asset_id: Optional[str] = None
    video_task_id: Optional[str] = None
    media_urls: List[str] = Field(default_factory=list)
    data: Dict[str, Any] = Field(default_factory=dict)
    created_by: str = "user"


class UpdateAtelierNodeRequest(BaseModel):
    type: Optional[str] = None
    title: Optional[str] = None
    prompt: Optional[str] = None
    status: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    source_project_id: Optional[str] = None
    frame_id: Optional[str] = None
    asset_id: Optional[str] = None
    video_task_id: Optional[str] = None
    media_urls: Optional[List[str]] = None
    data: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = None


class CreateAtelierVideoCandidatesRequest(BaseModel):
    prompt: str
    model: str = "wan2.7-i2v"
    reference_image_urls: List[str] = Field(default_factory=list)
    batch_size: int = Field(3, ge=1, le=6)
    params: Dict[str, Any] = Field(default_factory=dict)


class RegenerateAtelierVideoCandidatesRequest(BaseModel):
    prompt: Optional[str] = None
    model: Optional[str] = None
    reference_image_urls: Optional[List[str]] = None
    batch_size: Optional[int] = Field(None, ge=1, le=6)
    params: Optional[Dict[str, Any]] = None


class SelectAtelierVideoCandidateRequest(BaseModel):
    candidate_id: str


class AtelierAgentToolCallRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class RunAtelierAgentTurnRequest(BaseModel):
    user_message: str = ""
    tool_calls: List[AtelierAgentToolCallRequest] = Field(default_factory=list)
    preview: bool = False
    approve: bool = False
    deny: bool = False
    turn_id: Optional[str] = None


class PlanAtelierAgentTurnRequest(BaseModel):
    user_message: str = ""
    selected_node_id: Optional[str] = None
    skill_name: Optional[str] = None
    planner: Optional[str] = None
    planner_input: Dict[str, Any] = Field(default_factory=dict)


class BuildAtelierAgentPlannerPackageRequest(BaseModel):
    user_message: str = ""
    selected_node_id: Optional[str] = None
    skill_name: Optional[str] = None


class ExportAtelierSequenceRequest(BaseModel):
    entries: List[AtelierSequenceEntry] = Field(default_factory=list)


class ExportAtelierSequenceResponse(BaseModel):
    video_url: str
    filename: str
    size_mb: float
    clip_count: int


@app.post("/atelier/projects", response_model=AtelierProject)
async def create_atelier_project(request: CreateAtelierProjectRequest):
    try:
        project = pipeline.create_atelier_project(
            title=request.title,
            description=request.description,
            source_project_id=request.source_project_id,
        )
        return signed_response(project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/atelier/projects", response_model=List[AtelierProject])
async def list_atelier_projects():
    return signed_response(pipeline.list_atelier_projects())


@app.get("/atelier/projects/{project_id}", response_model=AtelierProject)
async def get_atelier_project(project_id: str):
    project = pipeline.get_atelier_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Atelier project not found")
    return signed_response(project)


@app.put("/atelier/projects/{project_id}", response_model=AtelierProject)
async def update_atelier_project(project_id: str, request: UpdateAtelierProjectRequest):
    try:
        updates = {k: v for k, v in request.model_dump().items() if v is not None}
        project = pipeline.update_atelier_project(project_id, updates)
        return signed_response(project)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))


@app.delete("/atelier/projects/{project_id}")
async def delete_atelier_project(project_id: str):
    try:
        pipeline.delete_atelier_project(project_id)
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.put("/atelier/projects/{project_id}/agent_policy", response_model=AtelierProject)
async def update_atelier_agent_policy(project_id: str, request: UpdateAtelierAgentPolicyRequest):
    try:
        updates = {k: v for k, v in request.model_dump().items() if v is not None}
        project = pipeline.update_atelier_agent_policy(project_id, updates)
        return signed_response(project)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))


@app.get("/atelier/projects/{project_id}/agent/tools")
async def list_atelier_agent_tools(project_id: str):
    if not pipeline.get_atelier_project(project_id):
        raise HTTPException(status_code=404, detail="Atelier project not found")
    return pipeline.list_atelier_agent_tools()


@app.post("/atelier/projects/{project_id}/agent/planner_package", response_model=AtelierAgentPlannerPackage)
async def build_atelier_agent_planner_package(project_id: str, request: BuildAtelierAgentPlannerPackageRequest):
    try:
        package = pipeline.build_atelier_agent_planner_package(
            project_id=project_id,
            user_message=request.user_message,
            selected_node_id=request.selected_node_id,
            skill_name=request.skill_name,
        )
        return signed_response(package)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))


@app.post("/atelier/projects/{project_id}/agent/plan", response_model=AtelierAgentPlan)
async def plan_atelier_agent_turn(project_id: str, request: PlanAtelierAgentTurnRequest):
    try:
        plan = pipeline.plan_atelier_agent_turn(
            project_id=project_id,
            user_message=request.user_message,
            selected_node_id=request.selected_node_id,
            skill_name=request.skill_name,
            planner=request.planner,
            planner_input=request.planner_input,
        )
        return signed_response(plan)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))


class ReplaceAtelierSequenceRequest(BaseModel):
    entries: List[AtelierSequenceEntry] = Field(default_factory=list)


@app.put(
    "/atelier/projects/{project_id}/sequence",
    response_model=AtelierProject,
)
async def replace_atelier_sequence(project_id: str, request: ReplaceAtelierSequenceRequest):
    try:
        project = pipeline.replace_atelier_sequence(
            project_id,
            [entry.model_dump() for entry in request.entries],
        )
        return signed_response(project)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))


@app.post(
    "/atelier/projects/{project_id}/sequence/export",
    response_model=ExportAtelierSequenceResponse,
)
async def export_atelier_sequence(project_id: str, request: ExportAtelierSequenceRequest):
    try:
        result = pipeline.export_atelier_sequence(
            project_id,
            [entry.model_dump() for entry in request.entries],
        )
        return signed_response(result)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/atelier/projects/{project_id}/agent/turns", response_model=AtelierAgentTurn)
async def run_atelier_agent_turn(
    project_id: str,
    request: RunAtelierAgentTurnRequest,
    background_tasks: BackgroundTasks,
):
    try:
        turn = pipeline.run_atelier_agent_turn(
            project_id=project_id,
            tool_calls=[call.model_dump() for call in request.tool_calls],
            user_message=request.user_message,
            preview=request.preview,
            approve=request.approve,
            deny=request.deny,
            turn_id=request.turn_id,
        )
        if not request.preview:
            for call in turn.tool_calls:
                if call.tool_name != "generation.createVideoCandidates" or call.status != "completed":
                    continue
                node_id = call.arguments.get("node_id")
                candidate_ids = (call.result_snapshot or {}).get("candidate_ids") or []
                for candidate_id in candidate_ids:
                    background_tasks.add_task(
                        pipeline.process_atelier_video_candidate,
                        project_id,
                        node_id,
                        candidate_id,
                    )
        return signed_response(turn)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))


@app.post("/atelier/projects/{project_id}/nodes", response_model=AtelierNode)
async def create_atelier_node(project_id: str, request: CreateAtelierNodeRequest):
    try:
        node = pipeline.create_atelier_node(project_id, request.model_dump())
        return signed_response(node)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))


@app.put("/atelier/projects/{project_id}/nodes/{node_id}", response_model=AtelierNode)
async def update_atelier_node(project_id: str, node_id: str, request: UpdateAtelierNodeRequest):
    try:
        updates = {k: v for k, v in request.model_dump().items() if v is not None}
        node = pipeline.update_atelier_node(project_id, node_id, updates)
        return signed_response(node)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))


@app.delete("/atelier/projects/{project_id}/nodes/{node_id}")
async def delete_atelier_node(project_id: str, node_id: str):
    try:
        pipeline.delete_atelier_node(project_id, node_id)
        return {"status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/atelier/projects/{project_id}/nodes/{node_id}/video_candidates", response_model=AtelierNode)
async def create_atelier_video_candidates(
    project_id: str,
    node_id: str,
    request: CreateAtelierVideoCandidatesRequest,
    background_tasks: BackgroundTasks,
):
    try:
        node = pipeline.create_atelier_video_candidates(
            project_id=project_id,
            node_id=node_id,
            prompt=request.prompt,
            model=request.model,
            reference_image_urls=request.reference_image_urls,
            batch_size=request.batch_size,
            params=request.params,
        )
        candidates = (node.data or {}).get("candidates") or []
        for candidate in candidates[-request.batch_size:]:
            background_tasks.add_task(
                pipeline.process_atelier_video_candidate,
                project_id,
                node_id,
                candidate["id"],
            )
        return signed_response(node)
    except ValueError as e:
        raise HTTPException(status_code=400 if "required" in str(e).lower() else 404, detail=str(e))


@app.post("/atelier/projects/{project_id}/nodes/{node_id}/video_candidates/regenerate", response_model=AtelierNode)
async def regenerate_atelier_video_candidates(
    project_id: str,
    node_id: str,
    request: RegenerateAtelierVideoCandidatesRequest,
    background_tasks: BackgroundTasks,
):
    try:
        node = pipeline.regenerate_atelier_video_candidates(
            project_id=project_id,
            node_id=node_id,
            prompt=request.prompt,
            model=request.model,
            reference_image_urls=request.reference_image_urls,
            batch_size=request.batch_size,
            params=request.params,
        )
        candidates = (node.data or {}).get("candidates") or []
        for candidate in candidates:
            background_tasks.add_task(
                pipeline.process_atelier_video_candidate,
                project_id,
                node_id,
                candidate["id"],
            )
        return signed_response(node)
    except ValueError as e:
        raise HTTPException(status_code=400 if "required" in str(e).lower() else 404, detail=str(e))


@app.post("/atelier/projects/{project_id}/nodes/{node_id}/video_candidates/{candidate_id}/retry", response_model=AtelierNode)
async def retry_atelier_video_candidate(
    project_id: str,
    node_id: str,
    candidate_id: str,
    background_tasks: BackgroundTasks,
):
    try:
        node = pipeline.retry_atelier_video_candidate(project_id, node_id, candidate_id)
        background_tasks.add_task(
            pipeline.process_atelier_video_candidate,
            project_id,
            node_id,
            candidate_id,
        )
        return signed_response(node)
    except ValueError as e:
        raise HTTPException(status_code=400 if "running" in str(e).lower() else 404, detail=str(e))


@app.post("/atelier/projects/{project_id}/nodes/{node_id}/video_candidates/select", response_model=AtelierNode)
async def select_atelier_video_candidate(
    project_id: str,
    node_id: str,
    request: SelectAtelierVideoCandidateRequest,
):
    try:
        node = pipeline.select_atelier_video_candidate(project_id, node_id, request.candidate_id)
        return signed_response(node)
    except ValueError as e:
        raise HTTPException(status_code=400 if "completed" in str(e).lower() else 404, detail=str(e))


@app.delete("/atelier/projects/{project_id}/nodes/{node_id}/video_candidates/{candidate_id}", response_model=AtelierNode)
async def delete_atelier_video_candidate(project_id: str, node_id: str, candidate_id: str):
    try:
        node = pipeline.delete_atelier_video_candidate(project_id, node_id, candidate_id)
        return signed_response(node)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))



@app.get("/projects/", response_model=List[dict])
async def list_projects():
    """Lists all projects from backend storage."""
    scripts = list(pipeline.scripts.values())
    return signed_response(scripts)


# ============================================================
# Series CRUD
# ============================================================

class CreateSeriesRequest(BaseModel):
    title: str
    description: str = ""
    workflow_mode: str = "r2v"


class UpdateSeriesRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    workflow_mode: Optional[str] = None


@app.post("/series")
async def create_series(request: CreateSeriesRequest):
    """Create a new Series."""
    series = pipeline.create_series(request.title, request.description, request.workflow_mode)
    return signed_response(series)


@app.get("/series")
async def list_series():
    """List all Series."""
    series_list = pipeline.list_series()
    return signed_response(series_list)


@app.get("/series/{series_id}")
async def get_series(series_id: str):
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
async def update_series(series_id: str, request: UpdateSeriesRequest):
    """Update Series title/description."""
    try:
        updates = {k: v for k, v in request.model_dump().items() if v is not None}
        series = pipeline.update_series(series_id, updates)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/series/{series_id}")
async def delete_series(series_id: str):
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
async def add_episode_to_series(series_id: str, request: AddEpisodeRequest):
    """Add an existing project as an episode to a Series."""
    try:
        series = pipeline.add_episode_to_series(series_id, request.script_id, request.episode_number)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/series/{series_id}/episodes/{script_id}")
async def remove_episode_from_series(series_id: str, script_id: str):
    """Remove an episode from a Series (does not delete the project)."""
    try:
        series = pipeline.remove_episode_from_series(series_id, script_id)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/series/{series_id}/episodes")
async def get_series_episodes(series_id: str):
    """Get all episodes in a Series."""
    try:
        episodes = pipeline.get_series_episodes(series_id)
        return signed_response(episodes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/series/{series_id}/prompt_config")
async def get_series_prompt_config(series_id: str):
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
        },
    }


@app.put("/series/{series_id}/prompt_config")
async def update_series_prompt_config(series_id: str, config: PromptConfig):
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
    character_aspect_ratio: Optional[str] = None
    scene_aspect_ratio: Optional[str] = None
    prop_aspect_ratio: Optional[str] = None
    storyboard_aspect_ratio: Optional[str] = None

@app.get("/series/{series_id}/model_settings")
async def get_series_model_settings(series_id: str):
    """Get Series model settings."""
    series = pipeline.get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return series.model_settings.model_dump()


@app.put("/series/{series_id}/model_settings")
async def update_series_model_settings(series_id: str, settings: UpdateModelSettingsRequest):
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
async def get_series_assets(series_id: str):
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
async def generate_series_asset(series_id: str, request: GenerateAssetRequest, background_tasks: BackgroundTasks):
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
async def toggle_series_asset_lock(series_id: str, request: ToggleLockRequest):
    """Toggle the locked status of a Series asset."""
    try:
        series = pipeline.toggle_series_asset_lock(series_id, request.asset_id, request.asset_type)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/update_image")
async def update_series_asset_image(series_id: str, request: UpdateAssetImageRequest):
    """Update a Series asset's image URL."""
    try:
        series = pipeline.update_series_asset_image(series_id, request.asset_id, request.asset_type, request.image_url)
        return signed_response(series)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/series/{series_id}/assets/update_attributes")
async def update_series_asset_attributes(series_id: str, request: UpdateAssetAttributesRequest):
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


@app.post("/series/{series_id}/assets/import")
async def import_series_assets(series_id: str, request: ImportAssetsRequest):
    """Deep-copy assets from another Series into this one."""
    try:
        series, imported_ids, skipped_ids = pipeline.import_assets_from_series(series_id, request.source_series_id, request.asset_ids)
        return signed_response(series)
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
    KLING_ACCESS_KEY: Optional[str] = None
    KLING_SECRET_KEY: Optional[str] = None
    VIDU_API_KEY: Optional[str] = None
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
async def get_config_info():
    """Returns information about the current config storage mode."""
    config_path = get_user_config_path()
    is_packaged = os.getenv("LUMEN_X_PACKAGED", "false").lower() == "true" or getattr(sys, 'frozen', False)
    return {
        "mode": "packaged" if is_packaged else "development",
        "config_path": config_path,
        "config_exists": os.path.exists(config_path)
    }


@app.post("/config/env")
async def update_env_config(config: EnvConfig):
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
            if isinstance(value, ProviderBackend):
                config_dict[key] = value.value
            else:
                config_dict[key] = value

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



@app.get("/projects/{script_id}", response_model=Script)
async def get_project(script_id: str):
    """Retrieves a project by ID."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    return signed_response(script)



@app.delete("/projects/{script_id}")
async def delete_project(script_id: str):
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


@app.post("/projects/{script_id}/sync_descriptions", response_model=Script)
async def sync_descriptions(script_id: str):
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
async def add_character(script_id: str, request: AddCharacterRequest):
    """Adds a new character."""
    try:
        updated_script = pipeline.add_character(script_id, request.name, request.description)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/projects/{script_id}/characters/{char_id}", response_model=Script)
async def delete_character(script_id: str, char_id: str):
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
async def add_scene(script_id: str, request: AddSceneRequest):
    """Adds a new scene."""
    try:
        updated_script = pipeline.add_scene(script_id, request.name, request.description)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/projects/{script_id}/scenes/{scene_id}", response_model=Script)
async def delete_scene(script_id: str, scene_id: str):
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
async def update_project_style(script_id: str, request: UpdateStyleRequest):
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
async def generate_assets(script_id: str, background_tasks: BackgroundTasks):
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
async def generate_motion_ref(script_id: str, request: GenerateMotionRefRequest, background_tasks: BackgroundTasks):
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
async def analyze_to_storyboard(script_id: str, request: AnalyzeToStoryboardRequest):
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
async def refine_storyboard_prompt(script_id: str, request: RefinePromptRequest):
    """
    Refines a raw prompt into bilingual (CN/EN) prompts using AI (Prompt C).
    Returns the refined prompts and optionally updates the frame.
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


@app.post("/projects/{script_id}/generate_storyboard", response_model=Script)
async def generate_storyboard(script_id: str):
    """Triggers storyboard generation."""
    try:
        updated_script = pipeline.generate_storyboard(script_id)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/projects/{script_id}/generate_video", response_model=Script)
async def generate_video(script_id: str):
    """Triggers video generation."""
    try:
        updated_script = pipeline.generate_video(script_id)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/projects/{script_id}/generate_audio", response_model=Script)
async def generate_audio(script_id: str):
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


async def process_video_task(script_id: str, task_id: str):
    """Background task to generate video."""
    try:
        pipeline.process_video_task(script_id, task_id)
    except Exception as e:
        logger.error(f"Error processing video task {task_id}: {e}")


@app.post("/projects/{script_id}/video_tasks", response_model=List[VideoTask])
async def create_video_task(script_id: str, request: CreateVideoTaskRequest, background_tasks: BackgroundTasks):
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
                mode=request.mode,
                sound=request.sound,
                cfg_scale=request.cfg_scale,
                vidu_audio=request.vidu_audio,
                movement_amplitude=request.movement_amplitude,
            )

            # Find the created task object
            created_task = next((t for t in script.video_tasks if t.id == task_id), None)
            if created_task:
                tasks.append(created_task)

            # Add background processing
            background_tasks.add_task(pipeline.process_video_task, script_id, task_id)

        return signed_response(tasks)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/assets/generate")
async def generate_single_asset(script_id: str, request: GenerateAssetRequest, background_tasks: BackgroundTasks):
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
            request.model_name
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
async def get_task_status(task_id: str):
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
async def generate_asset_video(script_id: str, asset_type: str, asset_id: str, request: GenerateAssetVideoRequest, background_tasks: BackgroundTasks):
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
async def delete_asset_video(script_id: str, asset_type: str, asset_id: str, video_id: str):
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
async def toggle_asset_lock(script_id: str, request: ToggleLockRequest):
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



@app.post("/projects/{script_id}/assets/update_image", response_model=Script)
async def update_asset_image(script_id: str, request: UpdateAssetImageRequest):
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
async def update_asset_attributes(script_id: str, request: UpdateAssetAttributesRequest):
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
async def update_asset_description(script_id: str, request: UpdateAssetDescriptionRequest):
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
async def select_asset_variant(script_id: str, request: SelectVariantRequest):
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
async def delete_asset_variant(script_id: str, request: DeleteVariantRequest):
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
async def toggle_variant_favorite(script_id: str, request: FavoriteVariantRequest):
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
async def update_model_settings(script_id: str, request: UpdateModelSettingsRequest):
    """Updates project's model settings for T2I/I2I/I2V and aspect ratios."""
    try:
        updated_script = pipeline.update_model_settings(
            script_id,
            request.t2i_model,
            request.i2i_model,
            request.i2v_model,
            request.character_aspect_ratio,
            request.scene_aspect_ratio,
            request.prop_aspect_ratio,
            request.storyboard_aspect_ratio,
            request.image_model
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


@app.get("/projects/{script_id}/prompt_config")
async def get_prompt_config(script_id: str):
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
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/projects/{script_id}/prompt_config")
async def update_prompt_config(script_id: str, request: UpdatePromptConfigRequest):
    """Updates project custom prompt configuration. Empty string = use system default."""
    try:
        script = pipeline.get_script(script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Project not found")
        script.prompt_config = PromptConfig(
            storyboard_polish=request.storyboard_polish,
            video_polish=request.video_polish,
            r2v_polish=request.r2v_polish,
        )
        pipeline._save_data()
        return {"prompt_config": script.prompt_config.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class BindVoiceRequest(BaseModel):
    voice_id: str
    voice_name: str


@app.post("/projects/{script_id}/characters/{char_id}/voice", response_model=Script)
async def bind_voice(script_id: str, char_id: str, request: BindVoiceRequest):
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
async def update_voice_params(script_id: str, char_id: str, request: UpdateVoiceParamsRequest):
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
async def get_voices():
    """Returns list of available voices."""
    return pipeline.audio_generator.get_available_voices()


class GenerateLineAudioRequest(BaseModel):
    speed: float = 1.0
    pitch: float = 1.0
    volume: int = 50


@app.post("/projects/{script_id}/frames/{frame_id}/audio", response_model=Script)
async def generate_line_audio(script_id: str, frame_id: str, request: GenerateLineAudioRequest):
    """Generates audio for a specific frame with parameters."""
    try:
        updated_script = pipeline.generate_dialogue_line(script_id, frame_id, request.speed, request.pitch, request.volume)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/mix/generate_sfx", response_model=Script)
async def generate_mix_sfx(script_id: str):
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
async def generate_mix_bgm(script_id: str):
    """Triggers BGM generation."""
    try:
        updated_script = pipeline.generate_audio(script_id)
        return signed_response(updated_script)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ToggleFrameLockRequest(BaseModel):
    frame_id: str


@app.post("/projects/{script_id}/frames/toggle_lock", response_model=Script)
async def toggle_frame_lock(script_id: str, request: ToggleFrameLockRequest):
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

@app.post("/projects/{script_id}/frames/update", response_model=Script)
async def update_frame(script_id: str, request: UpdateFrameRequest):
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
            character_ids=request.character_ids
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
async def add_frame(script_id: str, request: AddFrameRequest):
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
async def delete_frame(script_id: str, frame_id: str):
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
async def copy_frame(script_id: str, request: CopyFrameRequest):
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
async def reorder_frames(script_id: str, request: ReorderFramesRequest):
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
async def render_frame(script_id: str, request: RenderFrameRequest):
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
async def select_video(script_id: str, frame_id: str, request: SelectVideoRequest):
    """Selects a video variant for a specific frame."""
    try:
        updated_script = pipeline.select_video_for_frame(script_id, frame_id, request.video_id)
        return signed_response(updated_script)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExtractLastFrameRequest(BaseModel):
    video_task_id: str


@app.post("/projects/{script_id}/frames/{frame_id}/extract_last_frame")
async def extract_last_frame(script_id: str, frame_id: str, request: ExtractLastFrameRequest):
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
async def upload_frame_image(script_id: str, frame_id: str, file: UploadFile = File(...)):
    """Upload an image as a variant for a frame's rendered_image_asset."""
    try:
        # Save file locally first
        file_ext = os.path.splitext(file.filename)[1]
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


@app.post("/projects/{script_id}/merge", response_model=Script)
async def merge_videos(script_id: str):
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
async def export_project(script_id: str, request: ExportRequest):
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
        loop = asyncio.get_event_loop()
        recommendations = await loop.run_in_executor(
            None,  # Use default executor
            partial(pipeline.script_processor.analyze_script_for_styles, request.script_text)
        )

        return {"recommendations": recommendations}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/art_direction/save", response_model=Script)
async def save_art_direction(script_id: str, request: SaveArtDirectionRequest):
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
async def get_style_presets():
    """Get built-in style presets"""
    try:
        import json
        import os
        preset_file = os.path.join(os.path.dirname(__file__), "style_presets.json")
        logger.debug(f"Loading presets from {preset_file}")
        logger.debug(f"File exists: {os.path.exists(preset_file)}")

        if not os.path.exists(preset_file):
            logger.debug("DEBUG: Preset file not found!")
            return {"presets": []}

        with open(preset_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {"presets": data}
    except Exception as e:
        import traceback
        logger.exception("An error occurred")
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


class PolishVideoPromptRequest(BaseModel):
    draft_prompt: str
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement
    script_id: str = ""  # Optional: project ID to load custom prompt config


@app.post("/video/polish_prompt")
async def polish_video_prompt(request: PolishVideoPromptRequest):
    """Polishes a video generation prompt using LLM. Returns bilingual prompts."""
    try:
        custom_prompt = _get_custom_prompt(request.script_id, "video_polish")
        processor = ScriptProcessor()
        result = processor.polish_video_prompt(request.draft_prompt, request.feedback, custom_prompt)
        return {
            "prompt_cn": result.get("prompt_cn", ""),
            "prompt_en": result.get("prompt_en", "")
        }
    except Exception as e:
        import traceback
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


class RefSlot(BaseModel):
    description: str  # Character name, e.g., "雷震", "白兔"


class PolishR2VPromptRequest(BaseModel):
    draft_prompt: str
    slots: List[RefSlot]
    feedback: str = Field("", max_length=2000)  # User feedback for iterative refinement
    script_id: str = ""  # Optional: project ID to load custom prompt config


@app.post("/video/polish_r2v_prompt")
async def polish_r2v_prompt(request: PolishR2VPromptRequest):
    """Polishes a R2V (Reference-to-Video) prompt using LLM. Returns bilingual prompts."""
    try:
        custom_prompt = _get_custom_prompt(request.script_id, "r2v_polish")
        processor = ScriptProcessor()
        slot_info = [{"description": s.description} for s in request.slots]
        result = processor.polish_r2v_prompt(request.draft_prompt, slot_info, request.feedback, custom_prompt)
        return {
            "prompt_cn": result.get("prompt_cn", ""),
            "prompt_en": result.get("prompt_en", "")
        }
    except Exception as e:
        import traceback
        logger.exception("An error occurred")
        raise HTTPException(status_code=500, detail=str(e))


# ===== Environment Configuration Endpoints =====

@app.get("/config/env")
async def get_env_config():
    """Get current environment configuration."""
    try:
        from ...utils.endpoints import PROVIDER_DEFAULTS
        endpoint_overrides = {}
        for provider in PROVIDER_DEFAULTS:
            env_key = f"{provider}_BASE_URL"
            value = os.getenv(env_key)
            if value:
                endpoint_overrides[env_key] = value

        return {
            "DASHSCOPE_API_KEY": os.getenv("DASHSCOPE_API_KEY", ""),
            "ALIBABA_CLOUD_ACCESS_KEY_ID": os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", ""),
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", ""),
            "OSS_BUCKET_NAME": os.getenv("OSS_BUCKET_NAME", ""),
            "OSS_ENDPOINT": os.getenv("OSS_ENDPOINT", ""),
            "OSS_BASE_PATH": os.getenv("OSS_BASE_PATH", ""),
            "KLING_ACCESS_KEY": os.getenv("KLING_ACCESS_KEY", ""),
            "KLING_SECRET_KEY": os.getenv("KLING_SECRET_KEY", ""),
            "VIDU_API_KEY": os.getenv("VIDU_API_KEY", ""),
            "KLING_PROVIDER_MODE": _normalize_provider_mode(os.getenv("KLING_PROVIDER_MODE")),
            "VIDU_PROVIDER_MODE": _normalize_provider_mode(os.getenv("VIDU_PROVIDER_MODE")),
            "PIXVERSE_PROVIDER_MODE": _normalize_provider_mode(os.getenv("PIXVERSE_PROVIDER_MODE")),
            "endpoint_overrides": endpoint_overrides,
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
async def create_prop(script_id: str, request: CreatePropRequest):
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
async def delete_prop(script_id: str, prop_id: str):
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
