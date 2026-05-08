from typing import List, Optional, Dict, Any
from enum import Enum
import time
from pydantic import BaseModel, Field

DEFAULT_I2V_MODEL = "doubao-seedance-2-0-260128"
DEFAULT_T2I_MODEL = "openai-image"
DEFAULT_I2I_MODEL = "openai-image-edit"

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
    upload_type: Optional[str] = Field(None, description="Upload type if is_uploaded_source: full_body/head_shot/three_views/expression_sheet/image")

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
    model: str = Field(DEFAULT_I2V_MODEL, description="Model used for generation")
    aspect_ratio: Optional[str] = Field(None, description="Seedance aspect ratio: adaptive/16:9/9:16/1:1")
    watermark: bool = Field(False, description="Whether to keep vendor watermark")
    camera_fixed: Optional[bool] = Field(None, description="Seedance fixed camera mode")
    reference_audio_url: Optional[str] = Field(None, description="Seedance reference audio URL")
    seedance_reference_mode: Optional[str] = Field(None, description="Seedance reference mode: image/video/combo")
    seedance_workflow: Optional[str] = Field(None, description="Seedance workflow: standard/extend/edit")
    seedance_extend_mode: Optional[str] = Field(None, description="Seedance extend mode")
    seedance_edit_mode: Optional[str] = Field(None, description="Seedance edit mode")
    shot_type: str = Field("single", description="Shot type: 'single' or 'multi' (only for wan2.6-i2v)")
    generation_mode: str = Field("i2v", description="Generation mode: 'i2v' (image-to-video) or 'r2v' (reference-to-video)")
    reference_video_urls: List[str] = Field(default_factory=list, description="Reference video URLs for R2V generation (max 3)")
    # Kling params
    mode: Optional[str] = Field(None, description="Kling mode: std/pro")
    sound: Optional[str] = Field(None, description="Kling sound: on/off")
    cfg_scale: Optional[float] = Field(None, description="Kling cfg_scale: 0-1")
    # Vidu params
    vidu_audio: Optional[bool] = Field(None, description="Vidu audio output")
    movement_amplitude: Optional[str] = Field(None, description="Vidu movement amplitude: auto/small/medium/large")
    created_at: float = Field(default_factory=time.time)

class Character(BaseModel):
    id: str = Field(..., description="Unique identifier for the character")
    name: str = Field(..., description="Name of the character")
    aliases: List[str] = Field(default_factory=list, description="Alias or descriptor forms mapped to this character")
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
    expression_sheet: Optional[AssetUnit] = Field(default_factory=AssetUnit, description="Expression sheet asset unit")
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

    # Level 2: Expression Sheet (Derived)
    expression_sheet_image_url: Optional[str] = Field(None, description="[LEGACY] URL of the character expression sheet")
    expression_sheet_prompt: Optional[str] = Field(None, description="[LEGACY] Prompt used for expression sheet generation")
    expression_sheet_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="[LEGACY] Expression sheet asset container")

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
    expression_sheet_updated_at: float = Field(0.0, description="[LEGACY] Timestamp of last expression sheet update")
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
    image_prompt: Optional[str] = Field(None, description="Prompt used for scene image generation")
    
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
    image_prompt: Optional[str] = Field(None, description="Prompt used for prop image generation")
    
    # Video Assets (New for R2V)
    video_assets: List[VideoTask] = Field(default_factory=list, description="Generated reference videos for this prop")
    video_prompt: Optional[str] = Field(None, description="Prompt used for video generation")
    
    locked: bool = Field(False, description="Whether this asset is locked from regeneration")
    status: GenerationStatus = GenerationStatus.PENDING

class StoryboardFrame(BaseModel):
    id: str = Field(..., description="Unique identifier for the frame")
    scene_id: str = Field(..., description="Reference to the Scene ID")
    story_beat_id: Optional[str] = Field(None, description="Structured story beat ID this frame belongs to")
    story_beat_title: Optional[str] = Field(None, description="Structured story beat title this frame belongs to")
    story_beat_order: Optional[int] = Field(None, description="Structured story beat order this frame belongs to")
    chapter_order: Optional[int] = Field(None, description="Structured chapter/section order this frame belongs to")
    chapter_title: Optional[str] = Field(None, description="Structured chapter/section title this frame belongs to")
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
    bgm_url: Optional[str] = Field(None, description="URL of the generated background music")

    # Generation provenance
    generation_source: Optional[str] = Field(None, description="Source of the latest frame generation (llm/mock/fallback)")
    generation_degraded: bool = Field(False, description="Whether the latest frame generation used a fallback or mock path")
    generation_reason: Optional[str] = Field(None, description="Optional note explaining degraded generation")
    
    selected_video_id: Optional[str] = Field(None, description="ID of the selected VideoTask for this frame")
    locked: bool = Field(False, description="Whether this frame is locked from regeneration")
    status: GenerationStatus = GenerationStatus.PENDING
    updated_at: float = Field(default_factory=time.time, description="Timestamp of last update")

class ModelSettings(BaseModel):
    """Model selection settings for different generation stages"""
    t2i_model: str = Field(DEFAULT_T2I_MODEL, description="Text-to-Image model for Assets")
    i2i_model: str = Field(DEFAULT_I2I_MODEL, description="Image-to-Image model for Storyboard")
    i2v_model: str = Field(DEFAULT_I2V_MODEL, description="Image-to-Video model for Motion")
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


class StoryBeat(BaseModel):
    id: str = Field(..., description="Unique identifier for the structured story beat")
    order: int = Field(..., description="1-based order in the source narrative")
    title: str = Field(..., description="Readable title for the beat/scene")
    chapter_order: Optional[int] = Field(None, description="1-based chapter/section order if available")
    chapter_title: Optional[str] = Field(None, description="Readable chapter/section anchor for tree grouping")
    summary: str = Field("", description="Short summary of what happens in this beat")
    action_summary: str = Field("", description="Editable action summary for this beat")
    dialogue_excerpt: str = Field("", description="Editable dialogue excerpt for this beat")
    storyboard_goal: str = Field("", description="Editable storyboard goal for this beat")
    scene_id: Optional[str] = Field(None, description="Resolved primary scene ID if available")
    scene_name: Optional[str] = Field(None, description="Resolved primary scene name if available")
    location_hint: Optional[str] = Field(None, description="Location hint inferred from text")
    time_hint: Optional[str] = Field(None, description="Time hint inferred from text")
    character_ids: List[str] = Field(default_factory=list, description="Characters appearing in this beat")
    character_names: List[str] = Field(default_factory=list, description="Character names appearing in this beat")
    prop_ids: List[str] = Field(default_factory=list, description="Props appearing in this beat")
    prop_names: List[str] = Field(default_factory=list, description="Prop names appearing in this beat")
    source_excerpt: Optional[str] = Field(None, description="Short excerpt from the source text")
    storyboard_focus: Optional[str] = Field(None, description="Suggested storyboard focus for this beat")
    quality_flags: List[str] = Field(default_factory=list, description="Heuristic diagnostics for this beat")


class CharacterPresenceEntry(BaseModel):
    character_id: str = Field(..., description="Character ID")
    character_name: str = Field(..., description="Character name")
    scene_beat_ids: List[str] = Field(default_factory=list, description="Structured beat IDs where the character appears")
    scene_titles: List[str] = Field(default_factory=list, description="Structured beat titles where the character appears")
    mention_count: int = Field(0, description="Rough mention count across the source text")
    highlights: List[str] = Field(default_factory=list, description="Important summaries involving this character")


class CharacterRelationshipEdge(BaseModel):
    pair_id: str = Field(..., description="Stable identifier for this co-presence pair")
    source_character_id: str = Field(..., description="Source character ID")
    source_character_name: str = Field(..., description="Source character name")
    target_character_id: str = Field(..., description="Target character ID")
    target_character_name: str = Field(..., description="Target character name")
    co_scene_count: int = Field(0, description="How many structured beats the pair appears in together")
    shared_scene_beat_ids: List[str] = Field(default_factory=list, description="Beat IDs where the pair co-appears")
    shared_scene_titles: List[str] = Field(default_factory=list, description="Beat titles where the pair co-appears")
    relationship_hint: str = Field("", description="Heuristic description of the relationship/co-presence pattern")


class StoryAnalysis(BaseModel):
    summary: str = Field("", description="Overall plot summary for the source text")
    plot_points: List[str] = Field(default_factory=list, description="Ordered plot highlights")
    scene_beats: List[StoryBeat] = Field(default_factory=list, description="Structured beats/scenes extracted from the source text")
    character_presence: List[CharacterPresenceEntry] = Field(default_factory=list, description="Character appearance table across beats")
    character_relationships: List[CharacterRelationshipEdge] = Field(default_factory=list, description="Character co-presence and relationship statistics across beats")

class Script(BaseModel):
    id: str = Field(..., description="Unique identifier for the script project")
    title: str = Field(..., description="Title of the comic/video")
    original_text: str = Field(..., description="The original novel text")
    fixture_slug: Optional[str] = Field(None, description="Slug of the bundled fixture used to create this project")
    fixture_name: Optional[str] = Field(None, description="Display name of the bundled fixture")
    fixture_project_type: Optional[str] = Field(None, description="Project type declared by the bundled fixture")
    
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

    # Structured text analysis
    story_analysis: StoryAnalysis = Field(default_factory=StoryAnalysis, description="Structured analysis for plot summary, beats, and character presence")

    # Generation provenance for parse/analyze steps
    generation_metadata: Dict[str, Any] = Field(default_factory=dict, description="Provenance and degradation flags for generation steps")

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

    # Episode references
    episode_ids: List[str] = Field(default_factory=list, description="Ordered list of Episode/Script IDs")

    created_at: float
    updated_at: float
