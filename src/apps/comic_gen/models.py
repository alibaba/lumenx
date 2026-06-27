from typing import List, Optional, Dict, Any
from enum import Enum
import time
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


# === Storyboard Schema v2: Enums ===

class ShotSizeEnum(str, Enum):
    EXTREME_CLOSE_UP = "大特写"
    CLOSE_UP = "特写"
    MEDIUM_CLOSE_UP = "近景"
    MEDIUM_SHOT = "中景"
    FULL_SHOT = "全景"
    LONG_SHOT = "远景"
    EXTREME_LONG_SHOT = "大远景"

class CameraAngleEnum(str, Enum):
    EYE_LEVEL = "平视"
    HIGH_ANGLE = "俯视"
    LOW_ANGLE = "仰视"
    BIRDS_EYE = "鸟瞰"
    WORMS_EYE = "蚁视"
    OVER_SHOULDER = "过肩"
    DUTCH_ANGLE = "荷兰角"
    POV = "主观视角"

class CameraMovementType(str, Enum):
    STATIC = "static"
    PUSH_IN = "push_in"
    PULL_OUT = "pull_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    ORBIT = "orbit"
    FOLLOW = "follow"
    CRANE_UP = "crane_up"
    CRANE_DOWN = "crane_down"
    HANDHELD = "handheld"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"

class CameraSpeed(str, Enum):
    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"

# === Storyboard Schema v2: Compound structures ===

class CameraMovementData(BaseModel):
    primary: str = Field(..., description="主运镜类型")
    secondary: Optional[str] = Field(None, description="副运镜（最多一个）")
    speed: str = Field("normal", description="运镜速度: slow/normal/fast")
    description: Optional[str] = Field(None, description="自然语言运镜描述")

class StageSubject(BaseModel):
    ref: str = Field(..., description="角色/道具名称引用")
    zone: str = Field(..., description="屏幕区域: left/center/right")
    depth: str = Field(..., description="纵深: fore/mid/back")
    height: Optional[str] = Field(None, description="垂直: ground/low/surface/eye/high/overhead")
    facing: Optional[str] = Field(None, description="朝向: toward-camera/away/left/right")
    posture: Optional[str] = Field(None, description="体态: standing/sitting/lying/crouching/dynamic")

class Blocking(BaseModel):
    description: str = Field(..., description="自然语言站位描述")
    stage: Optional[List[StageSubject]] = Field(None, description="结构化站位数据")
    camera_relation: Optional[str] = Field(None, description="相机相对场景的空间关系")

class DialogueStructured(BaseModel):
    speaker: str = Field(..., description="说话人")
    line: str = Field(..., description="台词内容")
    emotion: Optional[str] = Field(None, description="情绪标签")
    delivery: Optional[str] = Field(None, description="演绎方式")

class AudioNote(BaseModel):
    sfx: Optional[str] = Field(None, description="音效描述")
    ambience: Optional[str] = Field(None, description="环境音描述")
    bgm_note: Optional[str] = Field(None, description="BGM 变化标注")

class LightingData(BaseModel):
    direction: Optional[str] = Field(None, description="光源方向")
    azimuth: Optional[float] = Field(None, description="方位角(度)")
    elevation: Optional[float] = Field(None, description="仰角(度)")
    quality: Optional[str] = Field(None, description="光质: soft/hard")
    color_temp: Optional[str] = Field(None, description="色温: warm/neutral/cool")
    description: Optional[str] = Field(None, description="自然语言光影描述")


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
    error: Optional[str] = Field(None, description="Failure reason, if any (set by pipeline / cancel / orphan recovery)")
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
    # Watermark toggle — supported by wan/kling/vidu/pixverse/happyhorse video models.
    # None = use provider default (most providers leave it off); True/False = explicit user choice.
    watermark: Optional[bool] = Field(None, description="Whether to embed a provider watermark in the rendered clip")
    # Provider-side identifiers (Issue 17). Persisted from the model's API response
    # so the user can paste them into the provider's console (e.g. Bailian / 百炼)
    # to diagnose failures without re-running. Different providers use different
    # naming — the canonical fields here normalize to "task_id" + "request_id":
    #   - DashScope (wan / qwen / happyhorse): task_id (output.task_id) + request_id
    #   - Kling: task_id (kling/vendor mode) + request_id (header X-Kling-Request-Id)
    #   - Vidu: task_id only
    #   - PixVerse: task_id only
    # provider_name labels the platform so the UI can render "dashscope: 1ce3..."
    # rather than guessing from model_name.
    provider_name: Optional[str] = Field(None, description="Which provider handled this task (dashscope / kling / vidu / pixverse / etc.)")
    provider_task_id: Optional[str] = Field(None, description="Provider-side task ID; pasteable into the provider's console for diagnosis")
    provider_request_id: Optional[str] = Field(None, description="Provider-side request ID for support tickets (optional — not all providers return one)")
    # User annotations on this take (抽卡 review). Storyboard's candidates
    # panel lets the user star multiple takes as a shortlist and attach a
    # short free-text label (≤20 chars). 🎬 final-take selection happens
    # in Assembly stage, not here.
    is_starred: bool = Field(False, description="User-starred shortlist flag (multi-select per shot)")
    label: Optional[str] = Field(None, description="User-attached short free-text note (≤20 chars)")
    # Source tab in the Storyboard R2V workbench. Distinct from
    # generation_mode (which the backend dispatcher uses to pick the
    # provider); workbench_tab reflects the UI tab the user clicked
    # Generate from, so candidates can be grouped per tab on refresh.
    # Optional: pre-Phase-2-persistence tasks parse with None.
    workbench_tab: Optional[str] = Field(
        None,
        description="Storyboard R2V workbench tab the user generated from: 't2i_i2v' | 'direct_r2v'",
    )
    created_at: float = Field(default_factory=time.time)

class Character(BaseModel):
    id: str = Field(..., description="Unique identifier for the character")
    name: str = Field(..., description="Name of the character")
    description: str = Field(..., description="Physical appearance and personality description")

    # R2V v2 Phase 4 — persona grouping. The character.id is the *visual
    # unit* (e.g. "young Zhang San" vs "adult Zhang San" are two ids).
    # persona is a free-text label grouping multiple visual variants of
    # the same "person". v1 schema only; v2 surfaces grouping in UI.
    persona: str = Field("", description="Persona group label (multiple visual variants of the same person share a persona)")

    # New Attributes
    age: Optional[str] = Field(None, description="Age of the character")
    gender: Optional[str] = Field(None, description="Gender of the character")
    clothing: Optional[str] = Field(None, description="Clothing description")
    visual_weight: int = Field(3, description="Visual importance weight (1-5)")
    
    # === R2V v2 Phase 5: Unified reference sheet ===
    # Single master sheet (multi-view or single portrait both OK) replaces
    # the 3-AssetUnit split. Legacy `full_body / three_views / head_shot`
    # kept readable for backward compat but new code writes here.
    reference_sheet: Optional[AssetUnit] = Field(
        default_factory=AssetUnit,
        description="Single master reference sheet (R2V v2). Multi-view or single portrait both supported.",
    )

    # === LEGACY (pre R2V v2): Asset Activation v2 — three separate units ===
    # Frontend now collapses to reference_sheet; legacy fields are read
    # with fallback during transition. Will be deprecated in a future release.
    full_body: Optional[AssetUnit] = Field(default_factory=AssetUnit, description="[LEGACY → reference_sheet] Full Body asset unit")
    three_views: Optional[AssetUnit] = Field(default_factory=AssetUnit, description="[LEGACY] Three Views asset unit")
    head_shot: Optional[AssetUnit] = Field(default_factory=AssetUnit, description="[LEGACY] Headshot/Avatar asset unit")
    
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
    # PR-3g (r2v-workflow-v3) — Voice source tracking. 'system' = built-in
    # voice from TTS_VOICE_REGISTRY; 'clone' = user-uploaded reference
    # audio (PR-3h); 'design' = voice generated from text prompt (PR-3i).
    # Picker modal Tabs filter by this field (Q15.5 B).
    voice_origin: str = Field("system", description="Voice source: 'system' | 'clone' | 'design'")
    locked: bool = Field(False, description="Whether this asset is locked from regeneration")
    starred: bool = Field(False, description="User-starred flag for the asset library shortlist")
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
    starred: bool = Field(False, description="User-starred flag for the asset library shortlist")
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
    starred: bool = Field(False, description="User-starred flag for the asset library shortlist")
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

    # === Storyboard Schema v2: Rich frame fields ===
    duration: Optional[int] = Field(None, description="建议时长（秒）")
    visual_description: Optional[str] = Field(None, description="画面描述：环境氛围 + 角色表演 + 物理动作的综合自然语言描述")
    dialogue_structured: Optional[DialogueStructured] = Field(None, description="结构化对白（speaker + line + emotion + delivery）")
    camera_movement_structured: Optional[CameraMovementData] = Field(None, description="结构化运镜（primary + secondary + speed + description）")
    blocking: Optional[Blocking] = Field(None, description="空间站位")
    audio_note: Optional[AudioNote] = Field(None, description="音效/环境音标注")
    lighting: Optional[LightingData] = Field(None, description="光影方向与质感")
    transition_hint: Optional[str] = Field(None, description="与下一帧的转场方式")
    assembled_prompt: Optional[str] = Field(None, description="由 visual_description + 结构化字段自动拼装的最终 prompt（只读）")

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
    # Optional TwelveLabs Pegasus scene description of the generated clip.
    # Populated only when TWELVELABS_API_KEY is configured (opt-in); None otherwise.
    scene_analysis: Optional[str] = Field(None, description="Pegasus scene description of the generated clip (opt-in, TwelveLabs)")
    
    audio_url: Optional[str] = Field(None, description="URL of the generated dialogue audio")
    audio_error: Optional[str] = Field(None, description="Audio generation error message")
    sfx_url: Optional[str] = Field(None, description="URL of the generated sound effect")
    # PR-3j · Stale detection for dialogue audio. text_hash combines
    # dialogue text + voice_id + instructions; UI flags audio as STALE
    # when current state hashes differently than the snapshot.
    dialogue_text_hash: Optional[str] = Field(None, description="MD5 of (dialogue|voice_id|instructions) at audio generation time")
    dialogue_voice_id: Optional[str] = Field(None, description="Voice id used to generate the current audio")
    dialogue_instructions: Optional[str] = Field(None, description="Emotion/style instructions used for the current audio")
    
    dubbed_video_url: Optional[str] = Field(None, description="URL of the video with TTS audio dubbed over original track")
    dubbed_video_task_id: Optional[str] = Field(None, description="ID of the VideoTask that was dubbed (for UI display)")
    dub_offset_ms: int = Field(0, description="Audio offset in ms for dubbing (positive = audio starts later)")
    bg_audio_url: Optional[str] = Field(None, description="Cached background audio (Demucs no_vocals) path")
    bg_audio_source_video: Optional[str] = Field(None, description="Video URL that bg_audio_url was separated from (cache key)")
    preview_video_url: Optional[str] = Field(None, description="Current preview dubbed video (temporary, not committed)")

    selected_video_id: Optional[str] = Field(None, description="ID of the selected VideoTask for this frame")
    is_video_pinned: bool = Field(False, description="True when the user has manually pinned an active video take; auto_select_latest_video skips pinned frames so newly generated takes don't overwrite a hand-picked selection")
    locked: bool = Field(False, description="Whether this frame is locked from regeneration")
    status: GenerationStatus = GenerationStatus.PENDING
    updated_at: float = Field(default_factory=time.time, description="Timestamp of last update")

    # === Storyboard R2V Workbench Persistence ===
    # These fields back the per-shot workbench panel state so it
    # survives refreshes and cross-device opens. Previously this state
    # lived only in React component state and was lost on reload.
    # All Optional with sane defaults to keep older frame records
    # round-tripping unchanged.
    workbench_tab_mode: Optional[str] = Field(
        None,
        description="Last-active R2V workbench tab: 't2i_i2v' | 'direct_r2v'",
    )
    t2i_image_urls: List[str] = Field(
        default_factory=list,
        description=(
            "Ordered history of T2I first-frame URLs for this shot "
            "(bounded FIFO, max 10). Active one indexed by t2i_selected_index."
        ),
    )
    t2i_selected_index: int = Field(
        0,
        description="Index into t2i_image_urls; the active首帧 fed into I2V.",
    )
    workbench_generate_count: int = Field(
        1,
        description="Last-chosen Generate ×N batch size for this shot (1-6).",
    )
    # Issue 16 — final take selection. Set in Assembly (per the chosen take
    # from this frame's video_tasks), read by Storyboard's ShotCard top
    # preview to display the canonical "this is the version that ships"
    # output. None = no explicit pick yet → ShotCard preview falls back to
    # latest starred / latest completed / first frame.
    final_take_id: Optional[str] = Field(
        None,
        description="Task ID of the chosen final take for this frame (singular). Set in Assembly stage; read by Storyboard.",
    )

class CustomVoice(BaseModel):
    """PR-3h/i — User-created custom voice (clone or design).

    Lives on Series.custom_voices[] (Q16.1 推荐: per-series 共享池).
    The picker modal's 我的复刻/我的设计 tabs read from this list.

    For clones: source_audio_url retains the original upload reference for
    later re-clone or audit; voice_prompt is None.
    For designs (PR-3i): voice_prompt retains the description for iteration;
    source_audio_url is None.
    """
    id: str = Field(..., description="voice_id returned by dashscope customization API")
    label: str = Field(..., description="User-given display name (e.g. '林墨真人声')")
    origin: str = Field(..., description="'clone' (PR-3h) | 'design' (PR-3i)")
    target_model: str = Field(
        "cosyvoice-v3.5-plus",
        description="Speech-synth model the voice was bound to at creation time. Required for /voice/preview model override because custom voice_id is NOT in static VOICES registry.",
    )
    family: str = Field("cosyvoice", description="'cosyvoice' | 'qwen3' — for picker UI filtering")
    created_at: float = Field(default_factory=time.time)
    source_audio_url: Optional[str] = Field(None, description="Clone only: original upload URL")
    voice_prompt: Optional[str] = Field(None, description="Design only: prompt used to generate (≤500 chars)")


class ModelSettings(BaseModel):
    """Model selection settings for different generation stages"""
    t2i_model: str = Field(_DEFAULT_MODEL_SETTINGS.t2i_model, description="Text-to-Image model for Assets")
    i2i_model: str = Field(_DEFAULT_MODEL_SETTINGS.i2i_model, description="Image-to-Image model for Storyboard")
    image_model: str = Field(_DEFAULT_MODEL_SETTINGS.image_model, description="Image generation model (T2I+I2I unified)")
    i2v_model: str = Field(_DEFAULT_MODEL_SETTINGS.i2v_model, description="Image-to-Video model for Motion")
    r2v_model: str = Field(
        "wan2.7-r2v",
        description="Reference-to-Video default for the project. Used by Storyboard's R2V tab as the initial picker value; per-storyboard override still wins.",
    )
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
    entity_extraction: str = Field("", description="Custom system prompt for novel→character/scene/prop extraction (Prompt A)")
    style_analysis: str = Field("", description="Custom system prompt for novel→visual style recommendations")
    storyboard_extraction: str = Field("", description="Custom system prompt for script→storyboard extraction (Prompt B)")
    # Polish 调用使用的 LLM 模型。空 = 用 LLMAdapter 默认（qwen3.6-plus）。
    # 显式覆盖时用于切到 vision-capable 或更便宜的模型（qwen3.6-flash、kimi-k2.6 等）。
    polish_model: str = Field("", description="Override LLM model id used for polish calls; empty = use system default")

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

    # PR-3e (r2v-workflow-v3) — Visual control preference. Determines the
    # default tabMode for newly added shots in the R2V workbench:
    #   'r2v' (default, 节奏优先) → new shots default to direct_r2v
    #   'i2v' (画面优先)         → new shots default to t2i_i2v (生首帧 → 生视频)
    # Per-shot tabMode still overridable. Series-level field; episodes
    # inherit from parent series.
    default_generation_mode: str = Field("r2v", description="Default per-shot generation_mode: 'r2v' (节奏优先) or 'i2v' (画面优先)")

    # Merged video URL
    merged_video_url: Optional[str] = Field(None, description="URL of the merged final video")

    # PR-3k · Assembly audio mix. bgm_url points at a preset library entry
    # (e.g. "presets/bgm/calm_warm.mp3") or a user-uploaded URL. mix_settings
    # holds per-track gain (0-100) used during ffmpeg mux in merge_videos.
    bgm_url: Optional[str] = Field(None, description="Background music URL for the merged video")
    mix_settings: Dict[str, int] = Field(
        default_factory=lambda: {"dialogue": 100, "bgm": 35, "sfx": 60},
        description="Per-track gain 0-100: dialogue / bgm / sfx",
    )

    # Series association
    series_id: Optional[str] = Field(None, description="ID of the parent Series, None for standalone projects")
    episode_number: Optional[int] = Field(None, description="Episode number within the Series")

    # R2V v2 Phase 3 — "Previously on..." panel cache.
    # Generated AI summary of the PREVIOUS episode's script (qwen3.6-plus),
    # invalidated when the previous episode's original_text changes
    # (revision-tracked). User opts in to AI generation; raw text snippet
    # is always available regardless of cache.
    last_episode_summary_cache: Optional[str] = Field(
        None,
        description="Cached AI summary of the previous episode's script.",
    )
    last_episode_summary_revision: Optional[str] = Field(
        None,
        description="Hash/marker of previous episode original_text when the cache was built.",
    )

    # R2V v2 P2-b — "Hook for next episode" prediction cache.
    # AI-generated based on THIS episode's ending; helps the author
    # think about what kicks off next episode while still composing.
    next_hook_cache: Optional[str] = Field(
        None,
        description="Cached AI prediction of next-episode opening hook.",
    )
    next_hook_revision: Optional[str] = Field(
        None,
        description="Hash/marker of THIS episode's original_text when the hook cache was built.",
    )

    # User-starred (featured shortlist) flag. Starred projects get the
    # amber-halation "featured" treatment in the gallery. Optional + default
    # False keeps existing projects and the create path unchanged.
    starred: bool = Field(False, description="User-starred flag for the project gallery featured shortlist")

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

    # PR-3e (r2v-workflow-v3) — Visual control preference at series level.
    # Episodes inherit this when created; user can override per-shot via
    # the shot card tab toggle. See Project.default_generation_mode for
    # full semantics.
    default_generation_mode: str = Field("r2v", description="Default per-shot generation_mode for new episodes: 'r2v' (节奏优先) or 'i2v' (画面优先)")

    # PR-3h/i (r2v-workflow-v3) — Custom voice pool (clones + designs).
    # Per Q16.1: series-level scope. Any character in this series can pick
    # from this pool via VoicePickerModal's 我的复刻 / 我的设计 tabs.
    custom_voices: List["CustomVoice"] = Field(default_factory=list, description="User-created custom voices (clones + designs)")

    # R2V v2 Phase 6 — content source mode. Orthogonal to workflow_mode.
    # 'scripted'  = traditional flow (Script step parses entities first)
    # 'freeform'  = user creates shots/cast directly (no script parse step)
    content_mode: str = Field("scripted", description="Content mode: 'scripted' or 'freeform' — affects R2V step sequence")

    # Episode references
    episode_ids: List[str] = Field(default_factory=list, description="Ordered list of Episode/Script IDs")

    created_at: float
    updated_at: float


class GlobalAssetLibrary(BaseModel):
    """Project-independent global asset pool (LumenX Core shared library).

    A single top-level curated container of reusable assets that any
    project may reference by id. It is the *lowest* layer in the
    resolver (Episode > Series > Global) — assets here never override a
    project's or series' own assets, they only add ids that aren't
    already present locally. Reuses the existing Character/Scene/Prop
    schema verbatim. Persisted to output/library_assets.json alongside
    projects.json / series.json."""

    characters: List[Character] = Field(default_factory=list, description="Shared global character assets")
    scenes: List[Scene] = Field(default_factory=list, description="Shared global scene assets")
    props: List[Prop] = Field(default_factory=list, description="Shared global prop assets")
