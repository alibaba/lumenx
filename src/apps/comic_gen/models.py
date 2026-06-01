from typing import List, Optional, Dict, Any
from enum import Enum
import time
import uuid
from pydantic import BaseModel, Field

from ...utils.model_catalog import get_default_model_settings


_DEFAULT_MODEL_SETTINGS = get_default_model_settings()

class AspectRatio(str, Enum):
    SQUARE = "1:1"
    PORTRAIT = "9:16"
    LANDSCAPE = "16:9"
    CINEMA = "21:9"

class GenerationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ProviderBackend(str, Enum):
    DASHSCOPE = "dashscope"
    VENDOR = "vendor"


class ProviderRoutingConfig(BaseModel):
    KLING_PROVIDER_MODE: ProviderBackend = Field(
        ProviderBackend.DASHSCOPE,
        description="Provider backend for kling-* models: dashscope or vendor",
    )
    VIDU_PROVIDER_MODE: ProviderBackend = Field(
        ProviderBackend.DASHSCOPE,
        description="Provider backend for vidu* models: dashscope or vendor",
    )
    PIXVERSE_PROVIDER_MODE: ProviderBackend = Field(
        ProviderBackend.DASHSCOPE,
        description="Provider backend for pixverse-* models: dashscope or vendor",
    )

class ImageVariant(BaseModel):
    id: str = Field(..., description="Unique identifier for the variant")
    url: str = Field(..., description="URL of the image")
    created_at: float = Field(default_factory=time.time, description="Timestamp of creation")
    prompt_used: Optional[str] = Field(None, description="Prompt used for this specific variant")
    is_favorited: bool = Field(False, description="Whether this variant is favorited/pinned (won't be auto-deleted)")
    # NEW: 上传来源标记
    is_uploaded_source: bool = Field(False, description="Whether this is a user-uploaded source file")
    upload_type: Optional[str] = Field(None, description="Upload type if is_uploaded_source: full_body/head_shot/three_views/image")

# Maximum variants to keep per asset (excluding favorited ones)
MAX_VARIANTS_PER_ASSET = 10

class ImageAsset(BaseModel):
    selected_id: Optional[str] = Field(None, description="ID of the currently selected variant")
    variants: List[ImageVariant] = Field(default_factory=list, description="History of generated variants")

class VideoVariant(BaseModel):
    """A video variant for Motion Reference"""
    id: str = Field(..., description="Unique identifier for the video variant")
    url: str = Field(..., description="URL of the video")
    created_at: float = Field(default_factory=time.time, description="Timestamp of creation")
    prompt_used: Optional[str] = Field(None, description="Prompt used for this video generation")
    audio_url: Optional[str] = Field(None, description="URL of the driving audio (for lip-sync)")
    source_image_id: Optional[str] = Field(None, description="ID of the static image used as source")
    is_favorited: bool = Field(False, description="Whether this variant is favorited")

class AssetUnit(BaseModel):
    """A unified asset container holding both static images and motion references"""
    # Static Image
    selected_image_id: Optional[str] = Field(None, description="ID of the currently selected image")
    image_variants: List[ImageVariant] = Field(default_factory=list, description="Pool of static image variants")
    
    # Motion Reference (Video)
    selected_video_id: Optional[str] = Field(None, description="ID of the currently selected motion ref")
    video_variants: List[VideoVariant] = Field(default_factory=list, description="Pool of motion reference variants")
    
    # Prompts
    image_prompt: Optional[str] = Field(None, description="Prompt used for image generation")
    video_prompt: Optional[str] = Field(None, description="Prompt used for motion ref generation")
    
    # Timestamps for consistency tracking
    image_updated_at: float = Field(default_factory=time.time, description="Timestamp of last image update")
    video_updated_at: float = Field(0.0, description="Timestamp of last motion ref update")

class VideoTask(BaseModel):
    id: str
    project_id: str
    frame_id: Optional[str] = Field(None, description="ID of the storyboard frame this video belongs to")
    asset_id: Optional[str] = Field(None, description="ID of the asset this video belongs to")
    image_url: str
    prompt: str
    status: str = "pending"  # pending, processing, completed, failed
    video_url: Optional[str] = None
    duration: int = Field(5, description="Video duration in seconds (model-specific range)")
    seed: Optional[int] = Field(None, description="Random seed for reproducibility")
    resolution: str = Field("720p", description="Video resolution")
    generate_audio: bool = Field(False, description="Whether to generate audio")
    audio_url: Optional[str] = Field(None, description="URL of generated/uploaded audio")
    prompt_extend: bool = Field(True, description="Whether to use prompt extension")
    negative_prompt: Optional[str] = Field(None, description="Negative prompt")
    model: str = Field("wan2.7-i2v", description="Model used for generation")
    shot_type: str = Field("single", description="Shot type: 'single' or 'multi' (only for wan I2V models)")
    generation_mode: str = Field("i2v", description="Generation mode: 'i2v' (image-to-video) or 'r2v' (reference-to-video)")
    reference_video_urls: List[str] = Field(default_factory=list, description="Reference video URLs for R2V generation (max 3)")
    # Kling params
    mode: Optional[str] = Field(None, description="Kling mode: std/pro")
    sound: Optional[str] = Field(None, description="Kling sound: on/off")
    cfg_scale: Optional[float] = Field(None, description="Kling cfg_scale: 0-1")
    # Vidu params
    vidu_audio: Optional[bool] = Field(None, description="Vidu audio output")
    movement_amplitude: Optional[str] = Field(None, description="Vidu movement amplitude: auto/small/medium/large")
    # HappyHorse params
    reference_image_urls: List[str] = Field(default_factory=list, description="Reference image URLs for HappyHorse R2V (max 9)")
    ratio: Optional[str] = Field(None, description="Aspect ratio for HappyHorse T2V/R2V: 16:9, 9:16, 1:1, 4:3, 3:4")
    audio_setting: Optional[str] = Field(None, description="Audio setting for HappyHorse V2V: auto/origin")
    created_at: float = Field(default_factory=time.time)

class Character(BaseModel):
    id: str = Field(..., description="Unique identifier for the character")
    name: str = Field(..., description="Name of the character")
    description: str = Field(..., description="Physical appearance and personality description")
    
    # New Attributes
    age: Optional[str] = Field(None, description="Age of the character")
    gender: Optional[str] = Field(None, description="Gender of the character")
    clothing: Optional[str] = Field(None, description="Clothing description")
    visual_weight: int = Field(3, description="Visual importance weight (1-5)")
    
    # === NEW: Asset Activation v2 - Unified Asset Units ===
    # Each unit holds both static images and motion references
    full_body: Optional[AssetUnit] = Field(default_factory=AssetUnit, description="Full Body asset unit (Master)")
    three_views: Optional[AssetUnit] = Field(default_factory=AssetUnit, description="Three Views asset unit")
    head_shot: Optional[AssetUnit] = Field(default_factory=AssetUnit, description="Headshot/Avatar asset unit")
    
    # === LEGACY: Kept for backwards compatibility ===
    # Level 1: Full Body (Master)
    full_body_image_url: Optional[str] = Field(None, description="[LEGACY] URL of the full body master image")
    full_body_prompt: Optional[str] = Field(None, description="[LEGACY] Prompt used for full body generation")
    full_body_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="[LEGACY] Full body asset container")

    # Level 2: Three Views (Derived)
    three_view_image_url: Optional[str] = Field(None, description="[LEGACY] URL of the 3-view character sheet")
    three_view_prompt: Optional[str] = Field(None, description="[LEGACY] Prompt used for 3-view generation")
    three_view_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="[LEGACY] Three view asset container")

    # Level 2: Headshot (Derived)
    headshot_image_url: Optional[str] = Field(None, description="[LEGACY] URL of the headshot/avatar")
    headshot_prompt: Optional[str] = Field(None, description="[LEGACY] Prompt used for headshot generation")
    headshot_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="[LEGACY] Headshot asset container")

    # Video Assets (Legacy R2V - will be migrated to AssetUnit.video_variants)
    video_assets: List[VideoTask] = Field(default_factory=list, description="[LEGACY] Generated reference videos")
    video_prompt: Optional[str] = Field(None, description="[LEGACY] Prompt used for video generation")

    # Legacy fields (kept for compatibility, mapped to new fields)
    image_url: Optional[str] = Field(None, description="[LEGACY] mapped to three_view_image_url")
    avatar_url: Optional[str] = Field(None, description="[LEGACY] mapped to headshot_image_url")

    is_consistent: bool = Field(True, description="Whether derived assets match the full body master")
    
    # Timestamps for consistency tracking (Legacy - now in AssetUnit)
    full_body_updated_at: float = Field(default_factory=time.time, description="[LEGACY] Timestamp of last full body update")
    three_view_updated_at: float = Field(0.0, description="[LEGACY] Timestamp of last three view update")
    headshot_updated_at: float = Field(0.0, description="[LEGACY] Timestamp of last headshot update")

    base_character_id: Optional[str] = Field(None, description="ID of the base character if this is a variant")
    voice_id: Optional[str] = Field(None, description="ID of the voice model to use")
    voice_name: Optional[str] = Field(None, description="Human-readable name of the voice")
    voice_speed: float = Field(1.0, description="Default speech rate (0.5-2.0)")
    voice_pitch: float = Field(1.0, description="Default pitch rate (0.5-2.0)")
    voice_volume: int = Field(50, description="Default volume (0-100)")
    locked: bool = Field(False, description="Whether this asset is locked from regeneration")
    status: GenerationStatus = GenerationStatus.PENDING

class Scene(BaseModel):
    id: str = Field(..., description="Unique identifier for the scene")
    name: str = Field(..., description="Name of the location/scene")
    description: str = Field(..., description="Visual description of the environment")
    visual_weight: int = Field(3, description="Visual importance weight (1-5)")
    time_of_day: Optional[str] = Field(None, description="Time of day (e.g. Night, Day)")
    lighting_mood: Optional[str] = Field(None, description="Lighting atmosphere")
    image_url: Optional[str] = Field(None, description="URL of the generated scene reference image (Legacy)")
    image_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="Scene image asset container")
    
    # Video Assets (New for R2V)
    video_assets: List[VideoTask] = Field(default_factory=list, description="Generated reference videos for this scene")
    video_prompt: Optional[str] = Field(None, description="Prompt used for video generation")
    
    locked: bool = Field(False, description="Whether this asset is locked from regeneration")
    status: GenerationStatus = GenerationStatus.PENDING

class Prop(BaseModel):
    id: str = Field(..., description="Unique identifier for the prop")
    name: str = Field(..., description="Name of the object")
    description: str = Field(..., description="Visual description of the object")
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    sfx_url: Optional[str] = None
    bgm_url: Optional[str] = None
    image_url: Optional[str] = Field(None, description="URL of the generated prop image (Legacy)")
    image_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="Prop image asset container")
    
    # Video Assets (New for R2V)
    video_assets: List[VideoTask] = Field(default_factory=list, description="Generated reference videos for this prop")
    video_prompt: Optional[str] = Field(None, description="Prompt used for video generation")
    
    locked: bool = Field(False, description="Whether this asset is locked from regeneration")
    status: GenerationStatus = GenerationStatus.PENDING

class StoryboardFrame(BaseModel):
    id: str = Field(..., description="Unique identifier for the frame")
    scene_id: str = Field(..., description="Reference to the Scene ID")
    character_ids: List[str] = Field(default_factory=list, description="List of Character IDs present in the frame")
    prop_ids: List[str] = Field(default_factory=list, description="List of Prop IDs present in the frame")
    
    # Legacy fields (kept for compatibility)
    action_description: str = Field("", description="What is happening in this frame (Legacy, use character_acting)")
    facial_expression: Optional[str] = Field(None, description="Specific facial expression")
    dialogue: Optional[str] = Field(None, description="Dialogue text content")
    speaker: Optional[str] = Field(None, description="Name of the speaker")
    
    # === NEW: Visual Atoms (Storyboard Dramatization v2) ===
    visual_atmosphere: Optional[str] = Field(None, description="Environment atmosphere: lighting, mood, volumetric effects")
    character_acting: Optional[str] = Field(None, description="Character performance: expression, body language, micro-details")
    key_action_physics: Optional[str] = Field(None, description="Key action with physics: deformation, texture, motion details")
    
    # === Camera Parameters ===
    shot_size: Optional[str] = Field(None, description="Shot size: 特写/近景/中景/全景/远景")
    camera_angle: str = Field("Medium Shot", description="Camera angle/shot type (Legacy)")
    camera_movement: Optional[str] = Field(None, description="Camera movement")
    composition: Optional[str] = Field(None, description="Visual composition guide")
    atmosphere: Optional[str] = Field(None, description="Mood of this specific shot (Legacy, use visual_atmosphere)")
    
    # Composition Data (JSON structure for canvas)
    composition_data: Optional[Dict[str, Any]] = Field(None, description="JSON data representing the canvas composition")
    
    # === Prompts ===
    image_prompt: Optional[str] = Field(None, description="Optimized prompt for T2I/I2I (Legacy)")
    image_prompt_cn: Optional[str] = Field(None, description="Polished Chinese prompt for user confirmation")
    image_prompt_en: Optional[str] = Field(None, description="Polished English prompt for Wan model generation")
    
    image_url: Optional[str] = Field(None, description="URL of the generated storyboard image (Legacy)")
    image_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="Storyboard image asset container")
    rendered_image_url: Optional[str] = Field(None, description="URL of the high-fidelity rendered image (Legacy)")
    rendered_image_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="Rendered image asset container")
    
    video_prompt: Optional[str] = Field(None, description="Optimized prompt for I2V")
    video_url: Optional[str] = Field(None, description="URL of the generated video clip")
    
    audio_url: Optional[str] = Field(None, description="URL of the generated dialogue audio")
    audio_error: Optional[str] = Field(None, description="Audio generation error message")
    sfx_url: Optional[str] = Field(None, description="URL of the generated sound effect")
    
    selected_video_id: Optional[str] = Field(None, description="ID of the selected VideoTask for this frame")
    locked: bool = Field(False, description="Whether this frame is locked from regeneration")
    status: GenerationStatus = GenerationStatus.PENDING
    updated_at: float = Field(default_factory=time.time, description="Timestamp of last update")

class ModelSettings(BaseModel):
    """Model selection settings for different generation stages"""
    t2i_model: str = Field(_DEFAULT_MODEL_SETTINGS.t2i_model, description="Text-to-Image model for Assets")
    i2i_model: str = Field(_DEFAULT_MODEL_SETTINGS.i2i_model, description="Image-to-Image model for Storyboard")
    image_model: str = Field(_DEFAULT_MODEL_SETTINGS.image_model, description="Image generation model (T2I+I2I unified)")
    i2v_model: str = Field(_DEFAULT_MODEL_SETTINGS.i2v_model, description="Image-to-Video model for Motion")
    character_aspect_ratio: str = Field("9:16", description="Aspect ratio for Characters (9:16, 16:9, 1:1)")
    scene_aspect_ratio: str = Field("16:9", description="Aspect ratio for Scenes (9:16, 16:9, 1:1)")
    prop_aspect_ratio: str = Field("1:1", description="Aspect ratio for Props (9:16, 16:9, 1:1)")
    storyboard_aspect_ratio: str = Field("16:9", description="Aspect ratio for Storyboard (9:16, 16:9, 1:1)")


class ArtDirection(BaseModel):
    """Art Direction configuration for global visual style"""
    selected_style_id: str = Field(..., description="ID of the selected style")
    style_config: Dict[str, Any] = Field(..., description="Complete style configuration")
    custom_styles: List[Dict[str, Any]] = Field(default_factory=list, description="User-created custom styles")
    ai_recommendations: List[Dict[str, Any]] = Field(default_factory=list, description="AI recommended styles")

class PromptConfig(BaseModel):
    """Custom system prompts for polish/refine stages. Empty string = use system default."""
    storyboard_polish: str = Field("", description="Custom system prompt for storyboard polish (Prompt C)")
    video_polish: str = Field("", description="Custom system prompt for video I2V polish (Prompt D)")
    r2v_polish: str = Field("", description="Custom system prompt for video R2V polish (Prompt E)")

class Script(BaseModel):
    id: str = Field(..., description="Unique identifier for the script project")
    title: str = Field(..., description="Title of the comic/video")
    original_text: str = Field(..., description="The original novel text")
    
    characters: List[Character] = Field(default_factory=list)
    scenes: List[Scene] = Field(default_factory=list)
    props: List[Prop] = Field(default_factory=list)
    frames: List[StoryboardFrame] = Field(default_factory=list)
    video_tasks: List[VideoTask] = Field(default_factory=list)
    
    # Global style settings (legacy, will be replaced by art_direction)
    style_preset: str = Field("realistic", description="Global style preset for all image generations")
    style_prompt: Optional[str] = Field(None, description="Custom style prompt to append to all generations")
    
    # Art Direction configuration (new approach)
    art_direction: Optional[ArtDirection] = Field(None, description="Global visual style configuration")
    
    # Model Settings for each generation stage
    model_settings: ModelSettings = Field(default_factory=ModelSettings, description="Model selection for T2I/I2I/I2V")

    # Custom prompt configuration for polish stages
    prompt_config: PromptConfig = Field(default_factory=PromptConfig, description="Custom system prompts for polish stages")

    # Workflow mode
    workflow_mode: str = Field("i2v_legacy", description="Workflow mode: 'r2v' (reference-to-video) or 'i2v_legacy' (first-frame I2V, default for old projects)")

    # Merged video URL
    merged_video_url: Optional[str] = Field(None, description="URL of the merged final video")

    # Series association
    series_id: Optional[str] = Field(None, description="ID of the parent Series, None for standalone projects")
    episode_number: Optional[int] = Field(None, description="Episode number within the Series")

    created_at: float
    updated_at: float


class Series(BaseModel):
    """A Series groups multiple Episodes with shared assets and configuration."""
    id: str = Field(..., description="Unique identifier for the series")
    title: str = Field(..., description="Title of the series")
    description: str = Field("", description="Series description/synopsis")

    # Shared asset library
    characters: List[Character] = Field(default_factory=list, description="Shared character assets")
    scenes: List[Scene] = Field(default_factory=list, description="Shared scene assets")
    props: List[Prop] = Field(default_factory=list, description="Shared prop assets")

    # Unified visual style
    art_direction: Optional[ArtDirection] = Field(None, description="Series-level art direction")

    # Series-level prompt configuration
    prompt_config: PromptConfig = Field(default_factory=PromptConfig, description="Series-level custom prompts")

    # Model settings
    model_settings: ModelSettings = Field(default_factory=ModelSettings, description="Series-level model settings")

    # Workflow mode for all episodes in this series
    workflow_mode: str = Field("i2v_legacy", description="Workflow mode: 'r2v' or 'i2v_legacy'")

    # Episode references
    episode_ids: List[str] = Field(default_factory=list, description="Ordered list of Episode/Script IDs")

    created_at: float
    updated_at: float


class AtelierApprovalMode(str, Enum):
    UNTRUSTED = "untrusted"
    ON_FAILURE = "on_failure"
    ON_REQUEST = "on_request"
    NEVER = "never"


class AtelierAgentPolicy(BaseModel):
    approval_mode: AtelierApprovalMode = Field(
        AtelierApprovalMode.UNTRUSTED,
        description="Approval mode for agent actions on the canvas",
    )
    allowed_tools: List[str] = Field(
        default_factory=list,
        description="Canvas tool names the agent may call; empty means no project-level restriction",
    )
    max_nodes_per_action: int = Field(
        8,
        ge=1,
        le=50,
        description="Upper bound for nodes an agent may create in one action",
    )
    updated_at: float = Field(default_factory=time.time)


class AtelierAgentToolStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVAL_REQUIRED = "approval_required"
    COMPLETED = "completed"
    DENIED = "denied"
    FAILED = "failed"


class AtelierAgentToolCall(BaseModel):
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = Field(..., description="Namespaced Atelier agent tool name")
    arguments: Dict[str, Any] = Field(default_factory=dict)
    status: AtelierAgentToolStatus = AtelierAgentToolStatus.PROPOSED
    approval_required: bool = False
    approval_granted: bool = False
    error: Optional[str] = None
    result_snapshot: Optional[Dict[str, Any]] = None
    created_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None


class AtelierAgentPlanContext(BaseModel):
    selected_node_id: Optional[str] = None
    planner_input: Dict[str, Any] = Field(default_factory=dict)
    planner_schema_version: str = Field("atelier.agent.planner.v1", description="Planner input/output envelope version")
    planner_adapter_name: Optional[str] = None
    tool_schema_version: str = Field("atelier.tools.v1", description="Tool schema version visible to the planner")
    model_trace_id: Optional[str] = None


class AtelierAgentPlannerPackage(BaseModel):
    project_id: str
    user_message: str = ""
    selected_node_id: Optional[str] = None
    skill_name: Optional[str] = None
    planner_schema_version: str = "atelier.agent.planner.v1"
    tool_schema_version: str = "atelier.tools.v1"
    output_contract: Dict[str, Any] = Field(default_factory=dict)
    tool_schemas: List[Dict[str, Any]] = Field(default_factory=list)
    project_snapshot: Dict[str, Any] = Field(default_factory=dict)
    selected_node_snapshot: Optional[Dict[str, Any]] = None
    policy_snapshot: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class AtelierAgentPlan(BaseModel):
    project_id: str
    user_message: str = ""
    planner: str = Field("deterministic_core", description="Planner implementation that produced this plan")
    skill_name: Optional[str] = None
    status: str = Field("ready", description="ready/blocked")
    reason: str = ""
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    context: AtelierAgentPlanContext = Field(default_factory=AtelierAgentPlanContext)
    created_at: float = Field(default_factory=time.time)


class AtelierAgentTurn(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    user_message: str = ""
    preview: bool = False
    status: str = Field("pending", description="pending/waiting_approval/completed/failed")
    tool_calls: List[AtelierAgentToolCall] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None
    response: Optional[str] = Field(
        default=None,
        description=(
            "Derived human-readable English summary of the turn's outcome. "
            "Populated by AtelierAgentHarness when the turn reaches a terminal "
            "status (completed/failed/waiting_approval). Optional so historical "
            "persisted turns load cleanly."
        ),
    )


class AtelierNode(BaseModel):
    id: str = Field(..., description="Unique node identifier")
    project_id: str = Field(..., description="Parent Atelier project ID")
    type: str = Field("idea", description="Node type, for example idea/image/video/audio/workflow")
    title: str = Field("", description="Short visible node title")
    prompt: str = Field("", description="Generation or planning prompt attached to this node")
    status: str = Field("draft", description="draft/pending/processing/completed/failed")
    x: float = Field(0.0, description="Canvas X position")
    y: float = Field(0.0, description="Canvas Y position")
    width: float = Field(320.0, description="Canvas node width")
    height: float = Field(180.0, description="Canvas node height")
    source_project_id: Optional[str] = Field(None, description="Studio project ID this node references")
    frame_id: Optional[str] = Field(None, description="Storyboard frame ID this node references")
    asset_id: Optional[str] = Field(None, description="Studio asset ID this node references")
    video_task_id: Optional[str] = Field(None, description="Shared VideoTask ID this node references")
    media_urls: List[str] = Field(default_factory=list, description="Generated or attached media URLs")
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Node-type specific payload. For draft / video nodes this includes "
            "`candidates`, `generation`, `selected_candidate_id`, and the single "
            "`reference_image_urls` reference bucket — bucket name is image-historical "
            "but as of v0.7 it also holds video URLs sourced from sibling drafts' "
            "completed takes (attach_atelier_reference). The shell infers per-URL kind "
            "at render time from the owning node's `type`."
        ),
    )
    created_by: str = Field("user", description="user or agent")
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class AtelierSequenceEntry(BaseModel):
    """One clip in the project's exported sequence (Sequence Strip).

    Coordinates a parent video node + a candidate id; trim points are
    optional seconds (used by ffmpeg -ss / -to during export). Stored
    on the project so the cut survives device / browser changes.
    """
    parentId: str = Field(..., description="Parent video node id holding the candidate")
    candidateId: str = Field(..., description="Candidate id within the parent's data.candidates")
    trimStart: Optional[float] = Field(None, description="Optional in-point in seconds")
    trimEnd: Optional[float] = Field(None, description="Optional out-point in seconds")


class AtelierProject(BaseModel):
    id: str = Field(..., description="Unique Atelier project ID")
    title: str = Field(..., description="Canvas project title")
    description: str = Field("", description="Canvas project description")
    source_project_id: Optional[str] = Field(None, description="Optional Studio project ID used as a seed")
    nodes: List[AtelierNode] = Field(default_factory=list)
    agent_policy: AtelierAgentPolicy = Field(default_factory=AtelierAgentPolicy)
    agent_turns: List[AtelierAgentTurn] = Field(default_factory=list)
    sequence: List[AtelierSequenceEntry] = Field(
        default_factory=list,
        description="Ordered list of clips assembled in the Sequence Strip; persisted server-side so the cut survives across devices",
    )
    created_at: float
    updated_at: float
