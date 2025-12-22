from typing import List, Optional, Dict, Any
from enum import Enum
import time
from pydantic import BaseModel, Field

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

class ImageVariant(BaseModel):
    id: str = Field(..., description="Unique identifier for the variant")
    url: str = Field(..., description="URL of the image")
    created_at: float = Field(default_factory=time.time, description="Timestamp of creation")
    prompt_used: Optional[str] = Field(None, description="Prompt used for this specific variant")
    is_favorited: bool = Field(False, description="Whether this variant is favorited/pinned (won't be auto-deleted)")

# Maximum variants to keep per asset (excluding favorited ones)
MAX_VARIANTS_PER_ASSET = 10

class ImageAsset(BaseModel):
    selected_id: Optional[str] = Field(None, description="ID of the currently selected variant")
    variants: List[ImageVariant] = Field(default_factory=list, description="History of generated variants")

class Character(BaseModel):
    id: str = Field(..., description="Unique identifier for the character")
    name: str = Field(..., description="Name of the character")
    description: str = Field(..., description="Physical appearance and personality description")
    
    # New Attributes
    age: Optional[str] = Field(None, description="Age of the character")
    gender: Optional[str] = Field(None, description="Gender of the character")
    clothing: Optional[str] = Field(None, description="Clothing description")
    visual_weight: int = Field(3, description="Visual importance weight (1-5)")
    
    # Level 1: Full Body (Master)
    full_body_image_url: Optional[str] = Field(None, description="URL of the full body master image (Legacy)")
    full_body_prompt: Optional[str] = Field(None, description="Prompt used for full body generation")
    full_body_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="Full body asset container")

    # Level 2: Three Views (Derived)
    three_view_image_url: Optional[str] = Field(None, description="URL of the 3-view character sheet (Legacy)")
    three_view_prompt: Optional[str] = Field(None, description="Prompt used for 3-view generation")
    three_view_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="Three view asset container")

    # Level 2: Headshot (Derived)
    headshot_image_url: Optional[str] = Field(None, description="URL of the headshot/avatar (Legacy)")
    headshot_prompt: Optional[str] = Field(None, description="Prompt used for headshot generation")
    headshot_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="Headshot asset container")

    # Legacy fields (kept for compatibility, mapped to new fields)
    image_url: Optional[str] = Field(None, description="Legacy: mapped to three_view_image_url")
    avatar_url: Optional[str] = Field(None, description="Legacy: mapped to headshot_image_url")

    is_consistent: bool = Field(True, description="Whether derived assets match the full body master")
    
    # Timestamps for consistency tracking
    full_body_updated_at: float = Field(default_factory=time.time, description="Timestamp of last full body update")
    three_view_updated_at: float = Field(0.0, description="Timestamp of last three view update")
    headshot_updated_at: float = Field(0.0, description="Timestamp of last headshot update")

    base_character_id: Optional[str] = Field(None, description="ID of the base character if this is a variant")
    voice_id: Optional[str] = Field(None, description="ID of the voice model to use")
    voice_name: Optional[str] = Field(None, description="Human-readable name of the voice")
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
    locked: bool = Field(False, description="Whether this asset is locked from regeneration")
    status: GenerationStatus = GenerationStatus.PENDING

class StoryboardFrame(BaseModel):
    id: str = Field(..., description="Unique identifier for the frame")
    scene_id: str = Field(..., description="Reference to the Scene ID")
    character_ids: List[str] = Field(default_factory=list, description="List of Character IDs present in the frame")
    prop_ids: List[str] = Field(default_factory=list, description="List of Prop IDs present in the frame")
    
    action_description: str = Field(..., description="What is happening in this frame")
    facial_expression: Optional[str] = Field(None, description="Specific facial expression")
    dialogue: Optional[str] = Field(None, description="Dialogue text content")
    speaker: Optional[str] = Field(None, description="Name of the speaker")
    
    camera_angle: str = Field("Medium Shot", description="Camera angle/shot type")
    camera_movement: Optional[str] = Field(None, description="Camera movement")
    composition: Optional[str] = Field(None, description="Visual composition guide")
    atmosphere: Optional[str] = Field(None, description="Mood of this specific shot")
    
    # Composition Data (JSON structure for canvas)
    composition_data: Optional[Dict[str, Any]] = Field(None, description="JSON data representing the canvas composition")
    
    image_prompt: Optional[str] = Field(None, description="Optimized prompt for T2I/I2I")
    image_url: Optional[str] = Field(None, description="URL of the generated storyboard image (Legacy)")
    image_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="Storyboard image asset container")
    rendered_image_url: Optional[str] = Field(None, description="URL of the high-fidelity rendered image (Legacy)")
    rendered_image_asset: Optional[ImageAsset] = Field(default_factory=ImageAsset, description="Rendered image asset container")
    
    video_prompt: Optional[str] = Field(None, description="Optimized prompt for I2V")
    video_url: Optional[str] = Field(None, description="URL of the generated video clip")
    
    audio_url: Optional[str] = Field(None, description="URL of the generated dialogue audio")
    sfx_url: Optional[str] = Field(None, description="URL of the generated sound effect")
    
    selected_video_id: Optional[str] = Field(None, description="ID of the selected VideoTask for this frame")
    locked: bool = Field(False, description="Whether this frame is locked from regeneration")
    status: GenerationStatus = GenerationStatus.PENDING
    updated_at: float = Field(default_factory=time.time, description="Timestamp of last update")

class VideoTask(BaseModel):
    id: str
    project_id: str
    frame_id: Optional[str] = Field(None, description="ID of the storyboard frame this video belongs to")
    image_url: str
    prompt: str
    status: str = "pending"  # pending, processing, completed, failed
    video_url: Optional[str] = None
    duration: int = Field(5, description="Video duration in seconds (5 or 10)")
    seed: Optional[int] = Field(None, description="Random seed for reproducibility")
    resolution: str = Field("720p", description="Video resolution")
    generate_audio: bool = Field(False, description="Whether to generate audio")
    audio_url: Optional[str] = Field(None, description="URL of generated/uploaded audio")
    prompt_extend: bool = Field(True, description="Whether to use prompt extension")
    negative_prompt: Optional[str] = Field(None, description="Negative prompt")
    model: str = Field("wan2.6-i2v", description="Model used for generation")
    shot_type: str = Field("single", description="Shot type: 'single' or 'multi' (only for wan2.6-i2v)")
    created_at: float = Field(default_factory=time.time)


class ModelSettings(BaseModel):
    """Model selection settings for different generation stages"""
    t2i_model: str = Field("wan2.6-t2i", description="Text-to-Image model for Assets")
    i2i_model: str = Field("wan2.6-image", description="Image-to-Image model for Storyboard")
    i2v_model: str = Field("wan2.6-i2v", description="Image-to-Video model for Motion")
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
    
    # Merged video URL
    merged_video_url: Optional[str] = Field(None, description="URL of the merged final video")
    
    created_at: float
    updated_at: float
