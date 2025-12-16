from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, List, Any
import os
import shutil
import uuid
from .pipeline import ComicGenPipeline
from .models import Script, VideoTask
from .llm import ScriptProcessor
from ...utils.oss_utils import OSSImageUploader
from dotenv import load_dotenv, set_key


env_path = ".env"
if os.path.exists(env_path):
    load_dotenv(env_path, override=True)

app = FastAPI(title="AI Comic Gen API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify the frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create output directory if it doesn't exist
os.makedirs("output", exist_ok=True)
os.makedirs("output/uploads", exist_ok=True)

# Mount static files
app.mount("/files", StaticFiles(directory="output"), name="files")

# Initialize pipeline
pipeline = ComicGenPipeline()



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
            return {"url": oss_url}

        # Fallback to local URL
        return {"url": f"http://localhost:8000/files/uploads/{filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CreateProjectRequest(BaseModel):
    title: str
    text: str


@app.post("/projects", response_model=Script)
async def create_project(request: CreateProjectRequest, skip_analysis: bool = False):
    """Creates a new project from a novel text."""
    return pipeline.create_project(request.title, request.text, skip_analysis=skip_analysis)


class ReparseProjectRequest(BaseModel):
    text: str


@app.put("/projects/{script_id}/reparse", response_model=Script)
async def reparse_project(script_id: str, request: ReparseProjectRequest):
    """Re-parses the text for an existing project, replacing all entities."""
    try:
        return pipeline.reparse_project(script_id, request.text)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/projects/{script_id}", response_model=Script)
async def get_project(script_id: str):
    """Retrieves a project by ID."""
    script = pipeline.get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Project not found")
    return script


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
        return updated_script
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
        return updated_script
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/generate_storyboard", response_model=Script)
async def generate_storyboard(script_id: str):
    """Triggers storyboard generation."""
    try:
        updated_script = pipeline.generate_storyboard(script_id)
        return updated_script
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/generate_video", response_model=Script)
async def generate_video(script_id: str):
    """Triggers video generation."""
    try:
        updated_script = pipeline.generate_video(script_id)
        return updated_script
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/generate_audio", response_model=Script)
async def generate_audio(script_id: str):
    """Triggers audio generation."""
    try:
        updated_script = pipeline.generate_audio(script_id)
        return updated_script
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
    model: str = "wan2.5-i2v-preview"


async def process_video_task(script_id: str, task_id: str):
    """Background task to generate video."""
    try:
        pipeline.process_video_task(script_id, task_id)
    except Exception as e:
        print(f"Error processing video task {task_id}: {e}")


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
                model=request.model
            )

            # Find the created task object
            created_task = next((t for t in script.video_tasks if t.id == task_id), None)
            if created_task:
                tasks.append(created_task)

            # Add background processing
            background_tasks.add_task(pipeline.process_video_task, script_id, task_id)

        return tasks
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


class GenerateAssetRequest(BaseModel):
    asset_id: str
    asset_type: str
    style_preset: str = "Cinematic"
    reference_image_url: Optional[str] = None
    style_prompt: Optional[str] = None
    generation_type: str = "all"  # 'full_body', 'three_view', 'headshot', 'all'
    prompt: Optional[str] = None  # Specific prompt for this generation step
    apply_style: bool = True
    negative_prompt: Optional[str] = None


@app.post("/projects/{script_id}/assets/generate", response_model=Script)
async def generate_single_asset(script_id: str, request: GenerateAssetRequest):
    """Generates a single asset with specific options."""
    try:
        updated_script = pipeline.generate_asset(
            script_id,
            request.asset_id,
            request.asset_type,
            request.style_preset,
            request.reference_image_url,
            request.style_prompt,
            request.generation_type,
            request.prompt,
            request.apply_style,
            request.negative_prompt
        )
        return updated_script
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ToggleLockRequest(BaseModel):
    asset_id: str
    asset_type: str


@app.post("/projects/{script_id}/assets/toggle_lock", response_model=Script)
async def toggle_asset_lock(script_id: str, request: ToggleLockRequest):
    """Toggles the locked status of an asset."""
    try:
        updated_script = pipeline.toggle_asset_lock(
            script_id,
            request.asset_id,
            request.asset_type
        )
        return updated_script
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdateAssetImageRequest(BaseModel):
    asset_id: str
    asset_type: str
    image_url: str


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
        return updated_script
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdateAssetAttributesRequest(BaseModel):
    asset_id: str
    asset_type: str
    attributes: Dict[str, Any]


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
        return updated_script
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
        return updated_script
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
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
        return updated_script
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/voices")
async def get_voices():
    """Returns list of available voices."""
    return pipeline.audio_generator.get_available_voices()


class GenerateLineAudioRequest(BaseModel):
    speed: float = 1.0
    pitch: float = 1.0


@app.post("/projects/{script_id}/frames/{frame_id}/audio", response_model=Script)
async def generate_line_audio(script_id: str, frame_id: str, request: GenerateLineAudioRequest):
    """Generates audio for a specific frame with parameters."""
    try:
        updated_script = pipeline.generate_dialogue_line(script_id, frame_id, request.speed, request.pitch)
        return updated_script
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
        return updated_script
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/mix/generate_bgm", response_model=Script)
async def generate_mix_bgm(script_id: str):
    """Triggers BGM generation."""
    try:
        updated_script = pipeline.generate_audio(script_id)
        return updated_script
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
        return updated_script
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RenderFrameRequest(BaseModel):
    frame_id: str
    composition_data: Optional[Dict[str, Any]] = None
    prompt: str


@app.post("/projects/{script_id}/storyboard/render", response_model=Script)
async def render_frame(script_id: str, request: RenderFrameRequest):
    """Renders a specific frame using composition data (I2I)."""
    try:
        updated_script = pipeline.generate_storyboard_render(
            script_id,
            request.frame_id,
            request.composition_data,
            request.prompt
        )
        return updated_script
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SelectVideoRequest(BaseModel):
    video_id: str


@app.post("/projects/{script_id}/frames/{frame_id}/select_video", response_model=Script)
async def select_video(script_id: str, frame_id: str, request: SelectVideoRequest):
    """Selects a video variant for a specific frame."""
    try:
        updated_script = pipeline.select_video_for_frame(script_id, frame_id, request.video_id)
        return updated_script
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/projects/{script_id}/merge", response_model=Script)
async def merge_videos(script_id: str):
    """Merge all selected frame videos into final output"""
    try:
        merged_script = pipeline.merge_videos(script_id)
        return merged_script
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

        # Use LLM to analyze and recommend styles
        recommendations = pipeline.script_processor.analyze_script_for_styles(request.script_text)

        return {"recommendations": recommendations}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
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
        return updated_script
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/art_direction/presets")
async def get_style_presets():
    """Get built-in style presets"""
    try:
        import json
        import os
        preset_file = os.path.join(os.path.dirname(__file__), "style_presets.json")
        print(f"DEBUG: Loading presets from {preset_file}")
        print(f"DEBUG: File exists: {os.path.exists(preset_file)}")

        if not os.path.exists(preset_file):
            print("DEBUG: Preset file not found!")
            return {"presets": []}

        with open(preset_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {"presets": data}

        return {"presets": presets}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


class PolishPromptRequest(BaseModel):
    draft_prompt: str
    assets: List[Dict[str, Any]]


@app.post("/storyboard/polish_prompt")
async def polish_prompt(request: PolishPromptRequest):
    """Polishes a storyboard prompt using LLM."""
    try:
        processor = ScriptProcessor()
        polished_prompt = processor.polish_storyboard_prompt(request.draft_prompt, request.assets)
        return {"polished_prompt": polished_prompt}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


class PolishVideoPromptRequest(BaseModel):
    draft_prompt: str


@app.post("/video/polish_prompt")
async def polish_video_prompt(request: PolishVideoPromptRequest):
    """Polishes a video generation prompt using LLM."""
    try:
        processor = ScriptProcessor()
        polished_prompt = processor.polish_video_prompt(request.draft_prompt)
        return {"polished_prompt": polished_prompt}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ===== Environment Configuration Endpoints =====

class EnvConfig(BaseModel):
    DASHSCOPE_API_KEY: Optional[str] = None
    ALIBABA_CLOUD_ACCESS_KEY_ID: Optional[str] = None
    ALIBABA_CLOUD_ACCESS_KEY_SECRET: Optional[str] = None
    OSS_BUCKET_NAME: Optional[str] = None
    OSS_ENDPOINT: Optional[str] = None
    OSS_BASE_PATH: Optional[str] = None


@app.get("/config/env")
async def get_env_config():
    """Get current environment configuration."""
    try:
        return {
            "DASHSCOPE_API_KEY": os.getenv("DASHSCOPE_API_KEY", ""),
            "ALIBABA_CLOUD_ACCESS_KEY_ID": os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", ""),
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", ""),
            "OSS_BUCKET_NAME": os.getenv("OSS_BUCKET_NAME", ""),
            "OSS_ENDPOINT": os.getenv("OSS_ENDPOINT", ""),
            "OSS_BASE_PATH": os.getenv("OSS_BASE_PATH", "")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/config/env")
async def save_env_config(config: EnvConfig):
    """Save environment configuration to .env file and current environment."""
    try:
        # Get the .env file path (in project root)
        env_path = ".env"

        # Create .env file if it doesn't exist
        if not os.path.exists(env_path):
            with open(env_path, "w") as f:
                f.write("# Auto-generated environment configuration\n")

        # Update both file and environment
        config_dict = config.dict(exclude_unset=True)
        for key, value in config_dict.items():
            if value is not None:
                # Update environment variable
                os.environ[key] = value
                # Update .env file
                set_key(env_path, key, value)

        # Reload environment variables
        load_dotenv(env_path, override=True)

        return {"message": "Configuration saved successfully"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
