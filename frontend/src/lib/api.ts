import axios from "axios";
import { zhCN } from "@/lib/i18n";
import type {
    AddCharacterRequest as ApiAddCharacterRequest,
    AddEpisodeRequest as ApiAddEpisodeRequest,
    AddFrameRequest as ApiAddFrameRequest,
    AddSceneRequest as ApiAddSceneRequest,
    AnalyzeBeatStoryboardRequest as ApiAnalyzeBeatStoryboardRequest,
    AnalyzeToStoryboardRequest as ApiAnalyzeToStoryboardRequest,
    ArtDirection as ApiArtDirection,
    BindVoiceRequest as ApiBindVoiceRequest,
    Character as ApiCharacter,
    CopyFrameRequest as ApiCopyFrameRequest,
    CreatePropRequest as ApiCreatePropRequest,
    CreateSeriesRequest as ApiCreateSeriesRequest,
    CreateProjectRequest as ApiCreateProjectRequest,
    CreateVideoTaskRequest as ApiCreateVideoTaskRequest,
    DeleteResponse as ApiDeleteResponse,
    DeleteVariantRequest as ApiDeleteVariantRequest,
    ExportRequest as ApiExportRequest,
    ExportResponse as ApiExportResponse,
    ExtractLastFrameRequest as ApiExtractLastFrameRequest,
    FavoriteVariantRequest as ApiFavoriteVariantRequest,
    GenerateAssetRequest as ApiGenerateAssetRequest,
    GenerateAssetVideoRequest as ApiGenerateAssetVideoRequest,
    GenerateLineAudioRequest as ApiGenerateLineAudioRequest,
    GenerateMotionRefRequest as ApiGenerateMotionRefRequest,
    ImportAssetsRequest as ApiImportAssetsRequest,
    ImageAsset as ApiImageAsset,
    ImageVariant as ApiImageVariant,
    ModelSettings as ApiModelSettings,
    PromptConfig as ApiPromptConfig,
    PromptConfigResponse as ApiPromptConfigResponse,
    Prop as ApiProp,
    RenderFrameRequest as ApiRenderFrameRequest,
    ReparseProjectRequest as ApiReparseProjectRequest,
    ReorderFramesRequest as ApiReorderFramesRequest,
    Script as ApiScript,
    Scene as ApiScene,
    SelectVideoRequest as ApiSelectVideoRequest,
    SelectVariantRequest as ApiSelectVariantRequest,
    Series as ApiSeries,
    SeriesAssetsResponse as ApiSeriesAssetsResponse,
    SeriesDetailResponse as ApiSeriesDetailResponse,
    SeriesEpisodeSummary as ApiSeriesEpisodeSummary,
    StoryAnalysis as ApiStoryAnalysis,
    StoryboardFrame as ApiStoryboardFrame,
    ToggleFrameLockRequest as ApiToggleFrameLockRequest,
    ToggleLockRequest as ApiToggleLockRequest,
    UpdateAssetAttributesRequest as ApiUpdateAssetAttributesRequest,
    UpdateAssetDescriptionRequest as ApiUpdateAssetDescriptionRequest,
    UpdateAssetImageRequest as ApiUpdateAssetImageRequest,
    UpdateFrameRequest as ApiUpdateFrameRequest,
    UpdateModelSettingsRequest as ApiUpdateModelSettingsRequest,
    UpdatePromptConfigRequest as ApiUpdatePromptConfigRequest,
    UpdateSeriesRequest as ApiUpdateSeriesRequest,
    UpdateStoryBeatRequest as ApiUpdateStoryBeatRequest,
    UpdateStyleRequest as ApiUpdateStyleRequest,
    UpdateVoiceParamsRequest as ApiUpdateVoiceParamsRequest,
    VideoTask as ApiVideoTask,
} from "@/lib/generated/openapi-types";
import type {
    ArtDirection,
    Character,
    ImageAsset,
    ImageVariant,
    ModelSettings,
    Project,
    PromptConfig,
    Prop,
    Scene,
    Series,
    StoryAnalysis,
    StoryboardFrame,
    StyleConfig,
    StylePreset,
} from "@/store/projectStore";

// Dynamic API URL detection:
// 1. In packaged app (Electron): Frontend is served by backend, use same origin
// 2. In local development: Use the backend port published by the dev launcher (defaults to 18177)
const DEFAULT_BACKEND_PORT = process.env.NEXT_PUBLIC_LUMENX_API_PORT || '18177';

const getApiUrl = (): string => {
    // If running in browser
    if (typeof window !== 'undefined') {
        const { protocol, hostname, port } = window.location;

        // In local development, Next.js can auto-increment its port.
        // Keep API calls pinned to the FastAPI backend port.
        if ((hostname === 'localhost' || hostname === '127.0.0.1') && port !== DEFAULT_BACKEND_PORT) {
            return `${protocol}//127.0.0.1:${DEFAULT_BACKEND_PORT}`;
        }

        // In production/packaged mode: Frontend is served by backend
        // Use same origin
        return `${protocol}//${hostname}${port ? ':' + port : ''}`;
    }

    // SSR fallback
    return `http://127.0.0.1:${DEFAULT_BACKEND_PORT}`;
};

export const API_URL = getApiUrl();
const apiErrors = zhCN.apiErrors;

export type ProviderMode = "dashscope" | "vendor";
export type StorageProvider = "" | "tos" | "oss";

export interface ImageModelStartupCheck {
    status: "ok" | "drift";
    expected_image_model: string;
    expected_image_edit_model: string;
    image_model: string;
    image_edit_model: string;
    message: string;
}

export interface EnvConfigPayload {
    IMAGE_PROVIDER?: "openai" | "dashscope";
    IMAGE_EDIT_PROVIDER?: "openai" | "dashscope";
    TTS_PROVIDER?: "openai" | "dashscope";
    LLM_PROVIDER?: string;
    OPENAI_API_KEY?: string;
    OPENAI_BASE_URL?: string;
    OPENAI_MODEL?: string;
    OPENAI_IMAGE_API_KEY?: string;
    OPENAI_IMAGE_EDIT_API_KEY?: string;
    OPENAI_IMAGE_BASE_URL?: string;
    OPENAI_IMAGE_EDIT_BASE_URL?: string;
    OPENAI_IMAGE_MODEL?: string;
    OPENAI_IMAGE_EDIT_MODEL?: string;
    OPENAI_TTS_API_KEY?: string;
    OPENAI_TTS_BASE_URL?: string;
    OPENAI_TTS_MODEL?: string;
    OPENAI_MULTIMODAL_API_KEY?: string;
    OPENAI_MULTIMODAL_BASE_URL?: string;
    OPENAI_MULTIMODAL_MODEL?: string;
    ARK_API_KEY?: string;
    DASHSCOPE_API_KEY?: string;
    OBJECT_STORAGE_PROVIDER?: StorageProvider;
    OBJECT_STORAGE_BUCKET_NAME?: string;
    OBJECT_STORAGE_ENDPOINT?: string;
    OBJECT_STORAGE_REGION?: string;
    OBJECT_STORAGE_BASE_PATH?: string;
    TOS_ACCESS_KEY_ID?: string;
    TOS_SECRET_ACCESS_KEY?: string;
    ALIBABA_CLOUD_ACCESS_KEY_ID?: string;
    ALIBABA_CLOUD_ACCESS_KEY_SECRET?: string;
    OSS_BUCKET_NAME?: string;
    OSS_ENDPOINT?: string;
    OSS_BASE_PATH?: string;
    KLING_PROVIDER_MODE?: ProviderMode;
    VIDU_PROVIDER_MODE?: ProviderMode;
    PIXVERSE_PROVIDER_MODE?: ProviderMode;
    KLING_ACCESS_KEY?: string;
    KLING_SECRET_KEY?: string;
    VIDU_API_KEY?: string;
    image_model_startup_check?: ImageModelStartupCheck;
    endpoint_overrides?: Record<string, string>;
    [key: string]: string | Record<string, string> | ImageModelStartupCheck | undefined;
}

type ApiTaskStatus = "pending" | "processing" | "completed" | "failed";

export interface VideoTask extends Omit<
    ApiVideoTask,
    | "asset_id"
    | "frame_id"
    | "status"
    | "video_url"
    | "duration"
    | "seed"
    | "resolution"
    | "generate_audio"
    | "audio_url"
    | "prompt_extend"
    | "negative_prompt"
    | "created_at"
    | "model"
    | "aspect_ratio"
    | "watermark"
    | "camera_fixed"
    | "reference_audio_url"
    | "seedance_reference_mode"
    | "seedance_workflow"
    | "seedance_extend_mode"
    | "seedance_edit_mode"
    | "generation_mode"
    | "reference_video_urls"
> {
    id: string;
    project_id: string;
    asset_id?: string;
    frame_id?: string;
    image_url: string;
    prompt: string;
    status: ApiTaskStatus;
    video_url?: string;
    duration: number;
    seed?: number;
    resolution: string;
    generate_audio: boolean;
    audio_url?: string;
    prompt_extend: boolean;
    negative_prompt?: string;
    created_at: number;
    model?: string;
    aspect_ratio?: string;
    watermark?: boolean;
    camera_fixed?: boolean;
    reference_audio_url?: string;
    seedance_reference_mode?: string;
    seedance_workflow?: string;
    seedance_extend_mode?: string;
    seedance_edit_mode?: string;
    generation_mode?: string;
    reference_video_urls?: string[];
}

export interface VoiceOption {
    id: string;
    name: string;
    gender?: string;
    model?: string;
}

export interface FixtureProjectSummary {
    slug: string;
    name: string;
    project_type: string;
    description: string;
    parser: string;
    source_count: number;
    reference_count: number;
    frame_count: number;
    model_settings?: Record<string, string>;
    is_imported: boolean;
    project_id?: string | null;
}

type ApiRecord = Record<string, unknown>;

export interface ApiMessageResponse extends ApiRecord {
    status?: string;
    message?: string;
    detail?: string;
}

export interface UploadFileResponse extends ApiRecord {
    url: string;
    storage_path?: string;
    filename?: string;
}

export interface ProjectTaskResponse extends Project {
    _task_id?: string;
}

export interface TaskStatusResponse extends ApiRecord {
    status: "pending" | "processing" | "completed" | "failed";
    error?: string;
    script_id?: string;
    script?: string | ProjectApiPayload;
}

export interface PromptConfigResponse extends Omit<ApiPromptConfigResponse, "prompt_config" | "defaults"> {
    prompt_config: PromptConfig;
    defaults?: PromptConfig;
}

export interface ArtStyleAnalysisResponse extends ApiRecord {
    recommendations: StyleConfig[];
    analysis_source?: string;
    analysis_degraded?: boolean;
    analysis_reason?: string | null;
}

export interface StylePresetsResponse extends ApiRecord {
    presets: StylePreset[];
}

export interface PolishPromptResponse extends ApiRecord {
    prompt_cn: string;
    prompt_en: string;
    generation_source?: string;
    generation_degraded?: boolean;
    generation_reason?: string | null;
}

export interface EnvConfigSaveResponse extends ApiMessageResponse {
    status: "success" | string;
    message: string;
}

export interface SeriesEpisodeSummary extends Omit<ApiSeriesEpisodeSummary, "episode_number"> {
    episode_number?: number;
}

export interface SeriesDetailResponse extends Series {
    episodes?: SeriesEpisodeSummary[];
}

export interface SeriesAssetsResponse extends Omit<ApiSeriesAssetsResponse, "characters" | "scenes" | "props"> {
    characters: Character[];
    scenes: Scene[];
    props: Prop[];
}

export interface CreateVideoTaskPayload {
    imageUrl: ApiCreateVideoTaskRequest["image_url"];
    prompt: ApiCreateVideoTaskRequest["prompt"];
    duration?: ApiCreateVideoTaskRequest["duration"];
    seed?: NonNullable<ApiCreateVideoTaskRequest["seed"]>;
    resolution?: ApiCreateVideoTaskRequest["resolution"];
    generateAudio?: ApiCreateVideoTaskRequest["generate_audio"];
    audioUrl?: NonNullable<ApiCreateVideoTaskRequest["audio_url"]>;
    promptExtend?: ApiCreateVideoTaskRequest["prompt_extend"];
    negativePrompt?: NonNullable<ApiCreateVideoTaskRequest["negative_prompt"]>;
    batchSize?: ApiCreateVideoTaskRequest["batch_size"];
    model?: ApiCreateVideoTaskRequest["model"];
    frameId?: NonNullable<ApiCreateVideoTaskRequest["frame_id"]>;
    shotType?: ApiCreateVideoTaskRequest["shot_type"];
    generationMode?: ApiCreateVideoTaskRequest["generation_mode"];
    referenceVideoUrls?: ApiCreateVideoTaskRequest["reference_video_urls"];
    aspectRatio?: NonNullable<ApiCreateVideoTaskRequest["aspect_ratio"]>;
    watermark?: ApiCreateVideoTaskRequest["watermark"];
    cameraFixed?: NonNullable<ApiCreateVideoTaskRequest["camera_fixed"]>;
    mode?: NonNullable<ApiCreateVideoTaskRequest["mode"]>;
    sound?: boolean;
    cfgScale?: NonNullable<ApiCreateVideoTaskRequest["cfg_scale"]>;
    viduAudio?: NonNullable<ApiCreateVideoTaskRequest["vidu_audio"]>;
    movementAmplitude?: NonNullable<ApiCreateVideoTaskRequest["movement_amplitude"]>;
    referenceAudioUrl?: NonNullable<ApiCreateVideoTaskRequest["reference_audio_url"]>;
    seedanceReferenceMode?: NonNullable<ApiCreateVideoTaskRequest["seedance_reference_mode"]>;
    seedanceWorkflow?: NonNullable<ApiCreateVideoTaskRequest["seedance_workflow"]>;
    seedanceExtendMode?: NonNullable<ApiCreateVideoTaskRequest["seedance_extend_mode"]>;
    seedanceEditMode?: NonNullable<ApiCreateVideoTaskRequest["seedance_edit_mode"]>;
}

export interface GenerateAssetPayload {
    assetId: string;
    assetType: string;
    stylePreset: string;
    referenceImageUrl?: string;
    stylePrompt?: string;
    generationType?: string;
    prompt?: string;
    applyStyle?: boolean;
    negativePrompt?: string;
    batchSize?: number;
    modelName?: string;
}

export interface GenerateMotionRefPayload {
    assetId: string;
    assetType: "full_body" | "head_shot" | "scene" | "prop";
    prompt?: string;
    audioUrl?: string;
    duration?: number;
    batchSize?: number;
}

export interface GenerateAssetVideoPayload {
    prompt?: string;
    duration?: number;
    aspectRatio?: string;
}

export interface UpdateModelSettingsPayload {
    t2iModel?: string;
    i2iModel?: string;
    i2vModel?: string;
    characterAspectRatio?: string;
    sceneAspectRatio?: string;
    propAspectRatio?: string;
    storyboardAspectRatio?: string;
}

export interface UpdateFramePayload {
    imagePrompt?: string;
    actionDescription?: string;
    dialogue?: string;
    cameraAngle?: string;
    sceneId?: string;
    characterIds?: string[];
}

export interface UpdateStoryBeatPayload {
    actionSummary?: string;
    dialogueExcerpt?: string;
    storyboardGoal?: string;
}

type ProjectStatus = ApiTaskStatus;
type ApiTimestamp = number | string | null | undefined;
type ProjectApiScriptBase = Omit<
    ApiScript,
    | "art_direction"
    | "characters"
    | "created_at"
    | "frames"
    | "model_settings"
    | "prompt_config"
    | "props"
    | "scenes"
    | "story_analysis"
    | "updated_at"
    | "video_tasks"
>;

interface ProjectApiPayload extends Partial<ProjectApiScriptBase> {
    id: string;
    title: string;
    originalText?: string | null;
    characters?: ApiCharacter[];
    scenes?: ApiScene[];
    props?: ApiProp[];
    frames?: ApiStoryboardFrame[];
    video_tasks?: ApiVideoTask[];
    status?: ProjectStatus | string | null;
    createdAt?: ApiTimestamp;
    created_at?: ApiTimestamp;
    updatedAt?: ApiTimestamp;
    updated_at?: ApiTimestamp;
    aspect_ratio?: string | null;
    aspectRatio?: string | null;
    art_direction?: ApiArtDirection | null;
    model_settings?: ApiModelSettings | null;
    prompt_config?: ApiPromptConfig | null;
    story_analysis?: ApiStoryAnalysis | null;
}

export type AssetAttributesPayload = ApiUpdateAssetAttributesRequest["attributes"];
export type StoryboardCompositionPayload = ApiRenderFrameRequest["composition_data"];

export interface RefineFrameAssetPayload {
    type: "Scene" | "Character" | "Prop";
    name: string;
    description?: string;
}

export interface RefineFramePromptResponse extends ApiRecord {
    prompt_cn?: string;
    prompt_en?: string;
    frame_updated?: boolean;
}

export interface MotionReferenceResponse extends Project {
    _task_id?: string;
}

export type ProjectExportOptions = ApiExportRequest;
export type ProjectExportResponse = ApiExportResponse;

export interface SeriesImportEpisodePayload {
    episode_number?: number;
    title?: string;
    summary?: string;
    text?: string;
}

export interface SeriesImportPreviewEpisode extends SeriesImportEpisodePayload {
    episode_number: number;
    title: string;
    summary: string;
    estimated_duration?: string;
}

export interface SeriesImportPreviewResponse extends ApiRecord {
    filename?: string | null;
    text_length: number;
    suggested_episodes: number;
    episodes: SeriesImportPreviewEpisode[];
    import_id: string;
    text?: string;
}

export interface SeriesImportConfirmPayload {
    title: string;
    description?: string;
    import_id?: string;
    text?: string;
    episodes: SeriesImportEpisodePayload[];
}

export interface SeriesImportConfirmResponse extends ApiRecord {
    series_id: string;
    episodes?: Array<ApiRecord>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
    typeof value === "object" && value !== null && !Array.isArray(value);

const requireStringField = (payload: Record<string, unknown>, fieldName: string): string => {
    const value = payload[fieldName];
    if (typeof value === "string" && value.trim()) return value;
    throw new Error(`Project API payload missing required string field: ${fieldName}`);
};

const optionalString = (value: unknown): string | undefined =>
    typeof value === "string" ? value : undefined;

const optionalNumber = (value: unknown): number | undefined =>
    typeof value === "number" && Number.isFinite(value) ? value : undefined;

const optionalBoolean = (value: unknown): boolean | undefined =>
    typeof value === "boolean" ? value : undefined;

const optionalArray = <T>(value: unknown): T[] =>
    Array.isArray(value) ? (value as T[]) : [];

const optionalRecord = (value: unknown): Record<string, unknown> | undefined =>
    isRecord(value) ? value : undefined;

const optionalStringArray = (value: unknown): string[] =>
    optionalArray<unknown>(value).filter((item): item is string => typeof item === "string");

const requireNumberField = (payload: Record<string, unknown>, fieldName: string): number => {
    const value = payload[fieldName];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    throw new Error(`Project API payload missing required numeric field: ${fieldName}`);
};

const normalizeTaskStatus = (value: unknown): ApiTaskStatus => {
    const status = optionalString(value);
    if (status === "pending" || status === "processing" || status === "completed" || status === "failed") {
        return status;
    }
    return "pending";
};

const normalizeImageVariant = (value: ApiImageVariant | unknown): ImageVariant | undefined => {
    if (!isRecord(value)) return undefined;

    const id = optionalString(value.id);
    const url = optionalString(value.url);
    if (!id || !url) return undefined;

    return {
        ...value,
        id,
        url,
        created_at: optionalNumber(value.created_at) ?? 0,
        prompt_used: optionalString(value.prompt_used),
    } as ImageVariant;
};

const normalizeImageVariants = (value: unknown): ImageVariant[] =>
    optionalArray<unknown>(value)
        .map(normalizeImageVariant)
        .filter((variant): variant is ImageVariant => Boolean(variant));

const normalizeImageAsset = (value: ApiImageAsset | unknown): ImageAsset | undefined => {
    if (!isRecord(value)) return undefined;

    return {
        ...value,
        selected_id: optionalString(value.selected_id) ?? null,
        variants: normalizeImageVariants(value.variants),
    } as ImageAsset;
};

type LegacyVisualAsset = NonNullable<Character["full_body"]>;
type LegacyVideoVariant = NonNullable<LegacyVisualAsset["video_variants"]>[number];

const normalizeLegacyVideoVariant = (value: unknown): LegacyVideoVariant | undefined => {
    if (!isRecord(value)) return undefined;
    const url = optionalString(value.url);
    if (!url) return undefined;

    return {
        ...value,
        id: optionalString(value.id),
        url,
        thumbnail: optionalString(value.thumbnail),
        created_at: optionalNumber(value.created_at),
    } as LegacyVideoVariant;
};

const normalizeLegacyVideoVariants = (value: unknown): LegacyVideoVariant[] =>
    optionalArray<unknown>(value)
        .map(normalizeLegacyVideoVariant)
        .filter((variant): variant is LegacyVideoVariant => Boolean(variant));

const normalizeLegacyVisualAsset = (value: unknown): Character["full_body"] => {
    if (!isRecord(value)) return undefined;

    return {
        ...value,
        selected_image_id: optionalString(value.selected_image_id),
        image_variants: normalizeImageVariants(value.image_variants),
        video_variants: normalizeLegacyVideoVariants(value.video_variants),
    } as LegacyVisualAsset;
};

const normalizeVideoTask = (value: ApiVideoTask | unknown): VideoTask | undefined => {
    if (!isRecord(value)) return undefined;

    const id = optionalString(value.id);
    const projectId = optionalString(value.project_id);
    const imageUrl = optionalString(value.image_url);
    const prompt = optionalString(value.prompt);
    if (!id || !projectId || !imageUrl || !prompt) return undefined;

    return {
        ...value,
        id,
        project_id: projectId,
        asset_id: optionalString(value.asset_id),
        frame_id: optionalString(value.frame_id),
        image_url: imageUrl,
        prompt,
        status: normalizeTaskStatus(value.status),
        video_url: optionalString(value.video_url),
        duration: optionalNumber(value.duration) ?? 5,
        seed: optionalNumber(value.seed),
        resolution: optionalString(value.resolution) ?? "720p",
        generate_audio: optionalBoolean(value.generate_audio) ?? false,
        audio_url: optionalString(value.audio_url),
        prompt_extend: optionalBoolean(value.prompt_extend) ?? false,
        negative_prompt: optionalString(value.negative_prompt),
        created_at: optionalNumber(value.created_at) ?? 0,
        model: optionalString(value.model),
        aspect_ratio: optionalString(value.aspect_ratio),
        watermark: optionalBoolean(value.watermark),
        camera_fixed: optionalBoolean(value.camera_fixed),
        reference_audio_url: optionalString(value.reference_audio_url),
        seedance_reference_mode: optionalString(value.seedance_reference_mode),
        seedance_workflow: optionalString(value.seedance_workflow),
        seedance_extend_mode: optionalString(value.seedance_extend_mode),
        seedance_edit_mode: optionalString(value.seedance_edit_mode),
        generation_mode: optionalString(value.generation_mode),
        reference_video_urls: optionalStringArray(value.reference_video_urls),
    } as VideoTask;
};

const normalizeVideoTasks = (value: unknown): VideoTask[] =>
    optionalArray<unknown>(value)
        .map(normalizeVideoTask)
        .filter((task): task is VideoTask => Boolean(task));

const normalizeCharacters = (value: ApiCharacter[] | unknown): Character[] =>
    optionalArray<unknown>(value)
        .map((item): Character | undefined => {
            if (!isRecord(item)) return undefined;
            const id = optionalString(item.id);
            const name = optionalString(item.name);
            if (!id || !name) return undefined;

            return {
                ...item,
                id,
                name,
                description: optionalString(item.description) ?? "",
                age: optionalString(item.age),
                gender: optionalString(item.gender),
                clothing: optionalString(item.clothing),
                full_body: normalizeLegacyVisualAsset(item.full_body),
                head_shot: normalizeLegacyVisualAsset(item.head_shot),
                full_body_image_url: optionalString(item.full_body_image_url),
                full_body_prompt: optionalString(item.full_body_prompt),
                full_body_asset: normalizeImageAsset(item.full_body_asset),
                three_view_image_url: optionalString(item.three_view_image_url),
                three_view_prompt: optionalString(item.three_view_prompt),
                three_view_asset: normalizeImageAsset(item.three_view_asset),
                headshot_image_url: optionalString(item.headshot_image_url),
                headshot_prompt: optionalString(item.headshot_prompt),
                headshot_asset: normalizeImageAsset(item.headshot_asset),
                video_assets: normalizeVideoTasks(item.video_assets),
                video_prompt: optionalString(item.video_prompt),
                image_url: optionalString(item.image_url),
                avatar_url: optionalString(item.avatar_url),
                voice_id: optionalString(item.voice_id),
                voice_name: optionalString(item.voice_name),
                status: optionalString(item.status),
            } as Character;
        })
        .filter((character): character is Character => Boolean(character));

const normalizeScenes = (value: ApiScene[] | unknown): Scene[] =>
    optionalArray<unknown>(value)
        .map((item): Scene | undefined => {
            if (!isRecord(item)) return undefined;
            const id = optionalString(item.id);
            const name = optionalString(item.name);
            if (!id || !name) return undefined;

            return {
                ...item,
                id,
                name,
                description: optionalString(item.description) ?? "",
                time_of_day: optionalString(item.time_of_day),
                lighting_mood: optionalString(item.lighting_mood),
                image_url: optionalString(item.image_url),
                image_asset: normalizeImageAsset(item.image_asset),
                video_assets: normalizeVideoTasks(item.video_assets),
                video_prompt: optionalString(item.video_prompt),
                status: optionalString(item.status),
            } as Scene;
        })
        .filter((scene): scene is Scene => Boolean(scene));

const normalizeProps = (value: ApiProp[] | unknown): Prop[] =>
    optionalArray<unknown>(value)
        .map((item): Prop | undefined => {
            if (!isRecord(item)) return undefined;
            const id = optionalString(item.id);
            const name = optionalString(item.name);
            if (!id || !name) return undefined;

            return {
                ...item,
                id,
                name,
                description: optionalString(item.description) ?? "",
                image_url: optionalString(item.image_url),
                image_asset: normalizeImageAsset(item.image_asset),
                video_assets: normalizeVideoTasks(item.video_assets),
                video_prompt: optionalString(item.video_prompt),
                status: optionalString(item.status),
            } as Prop;
        })
        .filter((prop): prop is Prop => Boolean(prop));

const normalizeStoryboardFrames = (value: ApiStoryboardFrame[] | unknown): StoryboardFrame[] =>
    optionalArray<unknown>(value)
        .map((item): StoryboardFrame | undefined => {
            if (!isRecord(item)) return undefined;
            const id = optionalString(item.id);
            if (!id) return undefined;

            return {
                ...item,
                id,
                scene_id: optionalString(item.scene_id),
                story_beat_id: optionalString(item.story_beat_id),
                story_beat_title: optionalString(item.story_beat_title),
                story_beat_order: optionalNumber(item.story_beat_order),
                chapter_order: optionalNumber(item.chapter_order),
                chapter_title: optionalString(item.chapter_title),
                character_ids: optionalStringArray(item.character_ids),
                prop_ids: optionalStringArray(item.prop_ids),
                action_description: optionalString(item.action_description),
                facial_expression: optionalString(item.facial_expression),
                dialogue: optionalString(item.dialogue),
                speaker: optionalString(item.speaker),
                camera_angle: optionalString(item.camera_angle),
                camera_movement: optionalString(item.camera_movement),
                composition: optionalString(item.composition),
                atmosphere: optionalString(item.atmosphere),
                image_prompt: optionalString(item.image_prompt),
                image_prompt_cn: optionalString(item.image_prompt_cn),
                image_prompt_en: optionalString(item.image_prompt_en),
                image_url: optionalString(item.image_url),
                image_asset: normalizeImageAsset(item.image_asset),
                rendered_image_url: optionalString(item.rendered_image_url),
                rendered_image_asset: normalizeImageAsset(item.rendered_image_asset),
                audio_url: optionalString(item.audio_url),
                sfx_url: optionalString(item.sfx_url),
                selected_video_id: optionalString(item.selected_video_id),
                status: optionalString(item.status),
                audio_error: optionalString(item.audio_error),
                video_url: optionalString(item.video_url),
                bgm_url: optionalString(item.bgm_url),
            } as StoryboardFrame;
        })
        .filter((frame): frame is StoryboardFrame => Boolean(frame));

const normalizeStoryAnalysis = (value: ApiStoryAnalysis | unknown): StoryAnalysis | undefined => {
    if (!isRecord(value)) return undefined;

    return {
        summary: optionalString(value.summary) ?? "",
        plot_points: optionalStringArray(value.plot_points),
        scene_beats: optionalArray<unknown>(value.scene_beats)
            .map((item): StoryAnalysis["scene_beats"][number] | undefined => {
                if (!isRecord(item)) return undefined;
                const id = optionalString(item.id);
                if (!id) return undefined;
                return {
                    ...item,
                    id,
                    order: optionalNumber(item.order) ?? 0,
                    title: optionalString(item.title) ?? "",
                    chapter_order: optionalNumber(item.chapter_order),
                    chapter_title: optionalString(item.chapter_title),
                    summary: optionalString(item.summary) ?? "",
                    action_summary: optionalString(item.action_summary),
                    dialogue_excerpt: optionalString(item.dialogue_excerpt),
                    storyboard_goal: optionalString(item.storyboard_goal),
                    scene_id: optionalString(item.scene_id),
                    scene_name: optionalString(item.scene_name),
                    location_hint: optionalString(item.location_hint),
                    time_hint: optionalString(item.time_hint),
                    character_ids: optionalStringArray(item.character_ids),
                    character_names: optionalStringArray(item.character_names),
                    prop_ids: optionalStringArray(item.prop_ids),
                    prop_names: optionalStringArray(item.prop_names),
                    source_excerpt: optionalString(item.source_excerpt),
                    storyboard_focus: optionalString(item.storyboard_focus),
                    quality_flags: optionalStringArray(item.quality_flags),
                };
            })
            .filter((beat): beat is StoryAnalysis["scene_beats"][number] => Boolean(beat)),
        character_presence: optionalArray<unknown>(value.character_presence)
            .map((item): StoryAnalysis["character_presence"][number] | undefined => {
                if (!isRecord(item)) return undefined;
                const characterId = optionalString(item.character_id);
                const characterName = optionalString(item.character_name);
                if (!characterId || !characterName) return undefined;
                return {
                    ...item,
                    character_id: characterId,
                    character_name: characterName,
                    scene_beat_ids: optionalStringArray(item.scene_beat_ids),
                    scene_titles: optionalStringArray(item.scene_titles),
                    mention_count: optionalNumber(item.mention_count) ?? 0,
                    highlights: optionalStringArray(item.highlights),
                };
            })
            .filter((entry): entry is StoryAnalysis["character_presence"][number] => Boolean(entry)),
        character_relationships: optionalArray<unknown>(value.character_relationships)
            .map((item): StoryAnalysis["character_relationships"][number] | undefined => {
                if (!isRecord(item)) return undefined;
                const pairId = optionalString(item.pair_id);
                const sourceId = optionalString(item.source_character_id);
                const sourceName = optionalString(item.source_character_name);
                const targetId = optionalString(item.target_character_id);
                const targetName = optionalString(item.target_character_name);
                if (!pairId || !sourceId || !sourceName || !targetId || !targetName) return undefined;
                return {
                    ...item,
                    pair_id: pairId,
                    source_character_id: sourceId,
                    source_character_name: sourceName,
                    target_character_id: targetId,
                    target_character_name: targetName,
                    co_scene_count: optionalNumber(item.co_scene_count) ?? 0,
                    shared_scene_beat_ids: optionalStringArray(item.shared_scene_beat_ids),
                    shared_scene_titles: optionalStringArray(item.shared_scene_titles),
                    relationship_hint: optionalString(item.relationship_hint) ?? "",
                };
            })
            .filter((edge): edge is StoryAnalysis["character_relationships"][number] => Boolean(edge)),
    };
};

const normalizeArtDirection = (value: ApiArtDirection | unknown): ArtDirection | undefined => {
    if (!isRecord(value)) return undefined;
    const styleConfig = optionalRecord(value.style_config);
    if (!styleConfig) return undefined;

    return {
        selected_style_id: optionalString(value.selected_style_id) ?? "",
        style_config: styleConfig as unknown as StyleConfig,
        custom_styles: optionalArray<StyleConfig>(value.custom_styles),
        ai_recommendations: optionalArray<StyleConfig>(value.ai_recommendations),
    };
};

const normalizeModelSettings = (value: ApiModelSettings | unknown): ModelSettings | undefined => {
    if (!isRecord(value)) return undefined;

    return {
        t2i_model: optionalString(value.t2i_model) ?? "openai-image",
        i2i_model: optionalString(value.i2i_model) ?? "openai-image-edit",
        i2v_model: optionalString(value.i2v_model) ?? "doubao-seedance-2-0-260128",
        character_aspect_ratio: optionalString(value.character_aspect_ratio) ?? "9:16",
        scene_aspect_ratio: optionalString(value.scene_aspect_ratio) ?? "16:9",
        prop_aspect_ratio: optionalString(value.prop_aspect_ratio) ?? "1:1",
        storyboard_aspect_ratio: optionalString(value.storyboard_aspect_ratio) ?? "16:9",
    };
};

export const normalizePromptConfig = (value: unknown): PromptConfig => {
    if (!isRecord(value)) {
        return {
            storyboard_polish: "",
            video_polish: "",
            r2v_polish: "",
        };
    }

    return {
        storyboard_polish: optionalString(value.storyboard_polish) ?? "",
        video_polish: optionalString(value.video_polish) ?? "",
        r2v_polish: optionalString(value.r2v_polish) ?? "",
    };
};

export const normalizePromptConfigResponse = (data: ApiPromptConfigResponse): PromptConfigResponse => ({
    ...data,
    prompt_config: normalizePromptConfig(data.prompt_config),
    defaults: data.defaults ? normalizePromptConfig(data.defaults) : undefined,
});

const normalizeSeriesEpisodeSummary = (value: unknown): SeriesEpisodeSummary | undefined => {
    if (!isRecord(value)) return undefined;

    return {
        id: requireStringField(value, "id"),
        title: requireStringField(value, "title"),
        episode_number: optionalNumber(value.episode_number),
        created_at: requireNumberField(value, "created_at"),
        updated_at: requireNumberField(value, "updated_at"),
    };
};

const normalizeSeriesEpisodes = (value: unknown): SeriesEpisodeSummary[] =>
    optionalArray<unknown>(value)
        .map(normalizeSeriesEpisodeSummary)
        .filter((episode): episode is SeriesEpisodeSummary => Boolean(episode));

export const normalizeSeriesPayload = (data: unknown): Series => {
    if (!isRecord(data)) {
        throw new Error("Series API payload must be a JSON object");
    }

    return {
        id: requireStringField(data, "id"),
        title: requireStringField(data, "title"),
        description: optionalString(data.description) ?? "",
        characters: normalizeCharacters(data.characters),
        scenes: normalizeScenes(data.scenes),
        props: normalizeProps(data.props),
        art_direction: normalizeArtDirection(data.art_direction),
        prompt_config: isRecord(data.prompt_config) ? normalizePromptConfig(data.prompt_config) : undefined,
        model_settings: normalizeModelSettings(data.model_settings),
        episode_ids: optionalStringArray(data.episode_ids),
        created_at: requireNumberField(data, "created_at"),
        updated_at: requireNumberField(data, "updated_at"),
    };
};

export const normalizeSeriesDetailPayload = (data: unknown): SeriesDetailResponse => {
    if (!isRecord(data)) {
        throw new Error("Series detail payload must be a JSON object");
    }

    return {
        ...normalizeSeriesPayload(data),
        episodes: normalizeSeriesEpisodes(data.episodes),
    };
};

export const normalizeSeriesAssetsPayload = (data: unknown): SeriesAssetsResponse => {
    if (!isRecord(data)) {
        throw new Error("Series assets payload must be a JSON object");
    }

    return {
        characters: normalizeCharacters(data.characters),
        scenes: normalizeScenes(data.scenes),
        props: normalizeProps(data.props),
    };
};

const parseProjectPayload = (data: unknown): ProjectApiPayload => {
    if (!isRecord(data)) {
        throw new Error("Project API payload must be a JSON object");
    }

    return {
        id: requireStringField(data, "id"),
        title: requireStringField(data, "title"),
        original_text: optionalString(data.original_text),
        originalText: optionalString(data.originalText),
        characters: optionalArray<ApiCharacter>(data.characters),
        scenes: optionalArray<ApiScene>(data.scenes),
        props: optionalArray<ApiProp>(data.props),
        frames: optionalArray<ApiStoryboardFrame>(data.frames),
        video_tasks: optionalArray<ApiVideoTask>(data.video_tasks),
        status: optionalString(data.status) || "pending",
        created_at: data.created_at as ApiTimestamp,
        updated_at: data.updated_at as ApiTimestamp,
        createdAt: data.createdAt as ApiTimestamp,
        updatedAt: data.updatedAt as ApiTimestamp,
        aspect_ratio: optionalString(data.aspect_ratio),
        aspectRatio: optionalString(data.aspectRatio),
        style_preset: optionalString(data.style_preset),
        style_prompt: optionalString(data.style_prompt),
        art_direction: isRecord(data.art_direction) ? (data.art_direction as unknown as ApiArtDirection) : undefined,
        model_settings: isRecord(data.model_settings) ? (data.model_settings as unknown as ApiModelSettings) : undefined,
        prompt_config: isRecord(data.prompt_config) ? (data.prompt_config as unknown as ApiPromptConfig) : undefined,
        merged_video_url: optionalString(data.merged_video_url),
        series_id: optionalString(data.series_id),
        episode_number: optionalNumber(data.episode_number),
        story_analysis: isRecord(data.story_analysis) ? (data.story_analysis as unknown as ApiStoryAnalysis) : undefined,
        generation_metadata: optionalRecord(data.generation_metadata),
        fixture_slug: optionalString(data.fixture_slug),
        fixture_name: optionalString(data.fixture_name),
        fixture_project_type: optionalString(data.fixture_project_type),
    };
};

const normalizeTimestamp = (value: unknown): string => {
    if (typeof value === "number" && Number.isFinite(value)) {
        return new Date(value < 1e12 ? value * 1000 : value).toISOString();
    }

    if (typeof value === "string" && value.trim()) {
        const numericValue = Number(value);
        if (Number.isFinite(numericValue)) {
            return new Date(numericValue < 1e12 ? numericValue * 1000 : numericValue).toISOString();
        }

        const parsedDate = new Date(value);
        if (!Number.isNaN(parsedDate.getTime())) {
            return parsedDate.toISOString();
        }
    }

    return new Date(0).toISOString();
};

export const normalizeProjectPayload = (data: unknown): Project => {
    const project = parseProjectPayload(data);
    const createdAt = normalizeTimestamp(project.createdAt ?? project.created_at);
    const updatedAt = normalizeTimestamp(project.updatedAt ?? project.updated_at);
    const aspectRatio = project.aspectRatio ?? project.aspect_ratio ?? undefined;

    return {
        id: project.id,
        title: project.title,
        originalText: project.originalText ?? project.original_text ?? "",
        characters: normalizeCharacters(project.characters),
        scenes: normalizeScenes(project.scenes),
        props: normalizeProps(project.props),
        frames: normalizeStoryboardFrames(project.frames),
        video_tasks: normalizeVideoTasks(project.video_tasks),
        status: project.status || "pending",
        createdAt,
        updatedAt,
        aspectRatio,
        style_preset: project.style_preset ?? undefined,
        style_prompt: project.style_prompt ?? undefined,
        art_direction: normalizeArtDirection(project.art_direction),
        model_settings: normalizeModelSettings(project.model_settings),
        prompt_config: project.prompt_config ? normalizePromptConfig(project.prompt_config) : undefined,
        merged_video_url: project.merged_video_url ?? undefined,
        series_id: project.series_id ?? undefined,
        episode_number: project.episode_number ?? undefined,
        story_analysis: normalizeStoryAnalysis(project.story_analysis),
        generation_metadata: project.generation_metadata ?? {},
    };
};

export const api = {
    createProject: async (title: string, text: string, skipAnalysis: boolean = false): Promise<Project> => {
        const request = { title, text } satisfies ApiCreateProjectRequest;
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects`, request, {
            params: { skip_analysis: skipAnalysis }
        });
        return normalizeProjectPayload(res.data);
    },

    listFixtureProjects: async (): Promise<FixtureProjectSummary[]> => {
        const res = await axios.get<FixtureProjectSummary[]>(`${API_URL}/projects/fixtures`);
        return res.data;
    },

    importFixtureProject: async (fixtureSlug: string): Promise<Project> => {
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/fixtures/${fixtureSlug}/import`);
        return normalizeProjectPayload(res.data);
    },

    getProjects: async (): Promise<Project[]> => {
        const res = await axios.get<ProjectApiPayload[]>(`${API_URL}/projects/`);
        return res.data.map((project) => normalizeProjectPayload(project));
    },

    getProject: async (scriptId: string): Promise<Project> => {
        const res = await axios.get<ProjectApiPayload>(`${API_URL}/projects/${scriptId}`);
        return normalizeProjectPayload(res.data);
    },

    deleteProject: async (scriptId: string): Promise<ApiMessageResponse> => {
        const res = await axios.delete<ApiMessageResponse>(`${API_URL}/projects/${scriptId}`);
        return res.data;
    },

    reparseProject: async (scriptId: string, text: string): Promise<Project> => {
        const request: ApiReparseProjectRequest = { text };
        const res = await axios.put<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/reparse`, request);
        return normalizeProjectPayload(res.data);
    },

    syncDescriptions: async (scriptId: string): Promise<Project> => {
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/sync_descriptions`);
        return normalizeProjectPayload(res.data);
    },

    generateAssets: async (scriptId: string): Promise<Project> => {
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/generate_assets`);
        return normalizeProjectPayload(res.data);
    },

    createVideoTask: async (id: string, payload: CreateVideoTaskPayload): Promise<VideoTask[]> => {
        const request = {
            image_url: payload.imageUrl,
            prompt: payload.prompt,
            duration: payload.duration ?? 5,
            seed: payload.seed,
            resolution: payload.resolution ?? "720p",
            generate_audio: payload.generateAudio ?? false,
            audio_url: payload.audioUrl,
            prompt_extend: payload.promptExtend ?? true,
            negative_prompt: payload.negativePrompt,
            batch_size: payload.batchSize ?? 1,
            model: payload.model ?? "doubao-seedance-2-0-260128",
            frame_id: payload.frameId,
            shot_type: payload.shotType ?? "single",  // 'single' or 'multi' (only for wan2.6-i2v)
            generation_mode: payload.generationMode ?? "i2v",  // 'i2v' or 'r2v'
            reference_video_urls: payload.referenceVideoUrls ?? [],  // Reference videos for R2V (max 3)
            aspect_ratio: payload.aspectRatio ?? "adaptive",
            watermark: payload.watermark ?? false,
            camera_fixed: payload.cameraFixed,
            reference_audio_url: payload.referenceAudioUrl,
            seedance_reference_mode: payload.seedanceReferenceMode,
            seedance_workflow: payload.seedanceWorkflow,
            seedance_extend_mode: payload.seedanceExtendMode,
            seedance_edit_mode: payload.seedanceEditMode,
            // Kling
            mode: payload.mode,
            sound: payload.sound != null ? (payload.sound ? "on" : "off") : undefined,
            cfg_scale: payload.cfgScale,
            // Vidu
            vidu_audio: payload.viduAudio,
            movement_amplitude: payload.movementAmplitude
        } satisfies ApiCreateVideoTaskRequest;
        const res = await axios.post<VideoTask[]>(`${API_URL}/projects/${id}/video_tasks`, request);
        return res.data;
    },


    uploadFile: async (file: File): Promise<UploadFileResponse> => {
        const formData = new FormData();
        formData.append("file", file);
        const response = await fetch(`${API_URL}/upload`, {
            method: "POST",
            body: formData,
        });
        if (!response.ok) throw new Error(apiErrors.uploadFile);
        return response.json() as Promise<UploadFileResponse>;
    },

    /**
     * Upload an asset image as a new variant.
     * The uploaded image will be marked as the 'upload source' for reverse generation.
     */
    uploadAsset: async (
        scriptId: string,
        assetType: string,
        assetId: string,
        file: File,
        uploadType: string,
        description?: string
    ): Promise<Project> => {
        const formData = new FormData();
        formData.append("file", file);

        const params = new URLSearchParams({
            upload_type: uploadType,
        });
        if (description) {
            params.append("description", description);
        }

        const response = await fetch(
            `${API_URL}/projects/${scriptId}/assets/${assetType}/${assetId}/upload?${params.toString()}`,
            {
                method: "POST",
                body: formData,
            }
        );

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || apiErrors.uploadAsset);
        }

        const data = await response.json() as ProjectApiPayload;
        return normalizeProjectPayload(data);
    },

    generateAsset: async (scriptId: string, payload: GenerateAssetPayload): Promise<ProjectTaskResponse> => {
        const request: ApiGenerateAssetRequest = {
            asset_id: payload.assetId,
            asset_type: payload.assetType,
            style_preset: payload.stylePreset,
            reference_image_url: payload.referenceImageUrl,
            style_prompt: payload.stylePrompt,
            generation_type: payload.generationType ?? "all",
            prompt: payload.prompt ?? "",
            apply_style: payload.applyStyle ?? true,
            negative_prompt: payload.negativePrompt ?? "",
            batch_size: payload.batchSize ?? 1,
            model_name: payload.modelName
        };
        const res = await axios.post<ProjectApiPayload & { _task_id?: string }>(`${API_URL}/projects/${scriptId}/assets/generate`, request);
        const normalized = normalizeProjectPayload(res.data);
        return { ...normalized, _task_id: res.data._task_id };
    },

    getTaskStatus: async (taskId: string): Promise<TaskStatusResponse> => {
        const res = await axios.get<TaskStatusResponse>(`${API_URL}/tasks/${taskId}`);
        return res.data;
    },

    generateAssetVideo: async (scriptId: string, assetType: string, assetId: string, payload: GenerateAssetVideoPayload): Promise<Project> => {
        const request: ApiGenerateAssetVideoRequest = {
            prompt: payload.prompt,
            duration: payload.duration,
            aspect_ratio: payload.aspectRatio,
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/assets/${assetType}/${assetId}/generate_video`, request);
        return normalizeProjectPayload(res.data);
    },

    /**
     * Generate Motion Reference video for an asset (Character Full Body/Headshot, Scene, or Prop).
     * This is part of Asset Activation v2.
     */
    generateMotionRef: async (scriptId: string, payload: GenerateMotionRefPayload): Promise<MotionReferenceResponse> => {
        const request: ApiGenerateMotionRefRequest = {
            asset_id: payload.assetId,
            asset_type: payload.assetType,
            prompt: payload.prompt,
            audio_url: payload.audioUrl,
            duration: payload.duration ?? 5,
            batch_size: payload.batchSize ?? 1
        };
        const res = await axios.post<ProjectApiPayload & { _task_id?: string }>(`${API_URL}/projects/${scriptId}/assets/generate_motion_ref`, request);
        const normalized = normalizeProjectPayload(res.data);
        return { ...normalized, _task_id: res.data._task_id };
    },

    deleteAssetVideo: async (scriptId: string, assetType: string, assetId: string, videoId: string): Promise<Project> => {
        const res = await axios.delete<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/assets/${assetType}/${assetId}/videos/${videoId}`);
        return normalizeProjectPayload(res.data);
    },

    toggleAssetLock: async (scriptId: string, assetId: string, assetType: string): Promise<Project> => {
        const request: ApiToggleLockRequest = {
            asset_id: assetId,
            asset_type: assetType
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/assets/toggle_lock`, request);
        return normalizeProjectPayload(res.data);
    },

    updateAssetImage: async (scriptId: string, assetId: string, assetType: string, imageUrl: string): Promise<Project> => {
        const request: ApiUpdateAssetImageRequest = {
            asset_id: assetId,
            asset_type: assetType,
            image_url: imageUrl
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/assets/update_image`, request);
        return normalizeProjectPayload(res.data);
    },

    selectAssetVariant: async (scriptId: string, assetId: string, assetType: string, variantId: string, generationType?: string): Promise<Project> => {
        const request: ApiSelectVariantRequest = {
            asset_id: assetId,
            asset_type: assetType,
            variant_id: variantId,
            generation_type: generationType
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/assets/variant/select`, request);
        return normalizeProjectPayload(res.data);
    },

    deleteAssetVariant: async (scriptId: string, assetId: string, assetType: string, variantId: string): Promise<Project> => {
        const request: ApiDeleteVariantRequest = {
            asset_id: assetId,
            asset_type: assetType,
            variant_id: variantId
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/assets/variant/delete`, request);
        return normalizeProjectPayload(res.data);
    },

    favoriteAssetVariant: async (scriptId: string, assetId: string, assetType: string, variantId: string, isFavorited: boolean, generationType?: string): Promise<Project> => {
        const request: ApiFavoriteVariantRequest = {
            asset_id: assetId,
            asset_type: assetType,
            variant_id: variantId,
            is_favorited: isFavorited,
            generation_type: generationType
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/assets/variant/favorite`, request);
        return normalizeProjectPayload(res.data);
    },

    updateModelSettings: async (scriptId: string, payload: UpdateModelSettingsPayload): Promise<Project> => {
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/model_settings`, {
            t2i_model: payload.t2iModel,
            i2i_model: payload.i2iModel,
            i2v_model: payload.i2vModel,
            character_aspect_ratio: payload.characterAspectRatio,
            scene_aspect_ratio: payload.sceneAspectRatio,
            prop_aspect_ratio: payload.propAspectRatio,
            storyboard_aspect_ratio: payload.storyboardAspectRatio
        });
        return normalizeProjectPayload(res.data);
    },

    getPromptConfig: async (scriptId: string): Promise<PromptConfigResponse> => {
        const res = await axios.get<ApiPromptConfigResponse>(`${API_URL}/projects/${scriptId}/prompt_config`);
        return normalizePromptConfigResponse(res.data);
    },

    updatePromptConfig: async (scriptId: string, config: ApiPromptConfig): Promise<PromptConfigResponse> => {
        const request: ApiUpdatePromptConfigRequest = {
            storyboard_polish: config.storyboard_polish,
            video_polish: config.video_polish,
            r2v_polish: config.r2v_polish,
        };
        const res = await axios.put<ApiPromptConfigResponse>(`${API_URL}/projects/${scriptId}/prompt_config`, request);
        return normalizePromptConfigResponse(res.data);
    },

    selectVideo: async (scriptId: string, frameId: string, videoId: string): Promise<Project> => {
        const request: ApiSelectVideoRequest = { video_id: videoId };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/frames/${frameId}/select_video`, request);
        return normalizeProjectPayload(res.data);
    },

    mergeVideos: async (scriptId: string): Promise<Project> => {
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/merge`);
        return normalizeProjectPayload(res.data);
    },

    // Art Direction APIs
    analyzeScriptForStyles: async (scriptId: string, scriptText: string): Promise<ArtStyleAnalysisResponse> => {
        const res = await axios.post<ArtStyleAnalysisResponse>(`${API_URL}/projects/${scriptId}/art_direction/analyze`, {
            script_text: scriptText
        });
        return res.data;
    },

    saveArtDirection: async (
        scriptId: string,
        selectedStyleId: string,
        styleConfig: StyleConfig,
        customStyles: StyleConfig[] = [],
        aiRecommendations: StyleConfig[] = []
    ): Promise<Project> => {
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/art_direction/save`, {
            selected_style_id: selectedStyleId,
            style_config: styleConfig,
            custom_styles: customStyles,
            ai_recommendations: aiRecommendations
        });
        return normalizeProjectPayload(res.data);
    },

    getStylePresets: async (): Promise<StylePresetsResponse> => {
        const res = await axios.get<StylePresetsResponse>(`${API_URL}/art_direction/presets`);
        return res.data;
    },

    // NOTE: polishPrompt removed - use refineFramePrompt for storyboard prompts
    polishVideoPrompt: async (draftPrompt: string, feedback: string = "", scriptId: string = ""): Promise<PolishPromptResponse> => {
        const res = await axios.post<PolishPromptResponse>(`${API_URL}/video/polish_prompt`, {
            draft_prompt: draftPrompt,
            feedback: feedback,
            script_id: scriptId,
        });
        return res.data;
    },
    polishR2VPrompt: async (draftPrompt: string, slots: { description: string }[], feedback: string = "", scriptId: string = ""): Promise<PolishPromptResponse> => {
        const res = await axios.post<PolishPromptResponse>(`${API_URL}/video/polish_r2v_prompt`, {
            draft_prompt: draftPrompt,
            slots: slots,
            feedback: feedback,
            script_id: scriptId,
        });
        return res.data;
    },
    updateAssetDescription: async (scriptId: string, assetId: string, assetType: string, description: string): Promise<Project> => {
        const request: ApiUpdateAssetDescriptionRequest = {
            asset_id: assetId,
            asset_type: assetType,
            description: description
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/assets/update_description`, request);
        return normalizeProjectPayload(res.data);
    },

    updateAssetAttributes: async (scriptId: string, assetId: string, assetType: string, attributes: AssetAttributesPayload): Promise<Project> => {
        const request: ApiUpdateAssetAttributesRequest = {
            asset_id: assetId,
            asset_type: assetType,
            attributes: attributes
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/assets/update_attributes`, request);
        return normalizeProjectPayload(res.data);
    },

    toggleFrameLock: async (scriptId: string, frameId: string): Promise<Project> => {
        const request: ApiToggleFrameLockRequest = {
            frame_id: frameId
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/frames/toggle_lock`, request);
        return normalizeProjectPayload(res.data);
    },

    updateFrame: async (scriptId: string, frameId: string, payload: UpdateFramePayload): Promise<Project> => {
        const request: ApiUpdateFrameRequest = {
            frame_id: frameId,
            image_prompt: payload.imagePrompt,
            action_description: payload.actionDescription,
            dialogue: payload.dialogue,
            camera_angle: payload.cameraAngle,
            scene_id: payload.sceneId,
            character_ids: payload.characterIds
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/frames/update`, request);
        return normalizeProjectPayload(res.data);
    },

    updateProjectStyle: async (scriptId: string, stylePreset: string, stylePrompt?: string): Promise<Project> => {
        const request: ApiUpdateStyleRequest = {
            style_preset: stylePreset,
            style_prompt: stylePrompt
        };
        const res = await axios.patch<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/style`, request);
        return normalizeProjectPayload(res.data);
    },

    renderFrame: async (scriptId: string, frameId: string, compositionData: StoryboardCompositionPayload, prompt: string, batchSize: number = 1): Promise<Project> => {
        const request: ApiRenderFrameRequest = {
            frame_id: frameId,
            composition_data: compositionData,
            prompt: prompt,
            batch_size: batchSize
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/storyboard/render`, request);
        return normalizeProjectPayload(res.data);
    },

    // === STORYBOARD DRAMATIZATION v2 ===

    /**
     * Analyzes script text and generates storyboard frames using AI.
     * Replaces existing frames with newly generated ones.
     */
    analyzeToStoryboard: async (scriptId: string, text: string): Promise<Project> => {
        const request: ApiAnalyzeToStoryboardRequest = {
            text: text
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/storyboard/analyze`, request);
        return normalizeProjectPayload(res.data);
    },

    updateStoryBeat: async (
        scriptId: string,
        beatId: string,
        payload: UpdateStoryBeatPayload
    ): Promise<Project> => {
        const request: ApiUpdateStoryBeatRequest = {
            action_summary: payload.actionSummary,
            dialogue_excerpt: payload.dialogueExcerpt,
            storyboard_goal: payload.storyboardGoal
        };
        const res = await axios.put<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/story_analysis/beats/${beatId}`, request);
        return normalizeProjectPayload(res.data);
    },

    analyzeStoryboardBeat: async (scriptId: string, beatId: string): Promise<Project> => {
        const request: ApiAnalyzeBeatStoryboardRequest = {
            beat_id: beatId,
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/storyboard/analyze_beat`, request);
        return normalizeProjectPayload(res.data);
    },

    /**
     * Refines a raw prompt into bilingual (CN/EN) prompts using AI.
     * Returns { prompt_cn, prompt_en, frame_updated }.
     */
    refineFramePrompt: async (
        scriptId: string,
        frameId: string,
        rawPrompt: string,
        assets: RefineFrameAssetPayload[] = [],
        feedback: string = ""
    ): Promise<RefineFramePromptResponse> => {
        const res = await axios.post<RefineFramePromptResponse>(`${API_URL}/projects/${scriptId}/storyboard/refine_prompt`, {
            frame_id: frameId,
            raw_prompt: rawPrompt,
            assets: assets,
            feedback: feedback
        });
        return res.data;
    },

    generateStoryboard: async (scriptId: string): Promise<Project> => {
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/generate_storyboard`);
        return normalizeProjectPayload(res.data);
    },

    getVoices: async (): Promise<VoiceOption[]> => {
        const response = await fetch(`${API_URL}/voices`);
        if (!response.ok) throw new Error(apiErrors.fetchVoices);
        return response.json() as Promise<VoiceOption[]>;
    },

    bindVoice: async (scriptId: string, charId: string, voiceId: string, voiceName: string): Promise<Project> => {
        const request: ApiBindVoiceRequest = { voice_id: voiceId, voice_name: voiceName };
        const response = await fetch(`${API_URL}/projects/${scriptId}/characters/${charId}/voice`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(request),
        });
        if (!response.ok) throw new Error(apiErrors.bindVoice);
        const data = await response.json() as ProjectApiPayload;
        return normalizeProjectPayload(data);
    },

    generateAudio: async (scriptId: string): Promise<Project> => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/generate_audio`, {
            method: "POST",
        });
        if (!response.ok) throw new Error(apiErrors.generateAudio);
        const data = await response.json() as ProjectApiPayload;
        return normalizeProjectPayload(data);
    },

    generateLineAudio: async (scriptId: string, frameId: string, speed: number, pitch: number, volume: number = 50): Promise<Project> => {
        const request: ApiGenerateLineAudioRequest = { speed, pitch, volume };
        const response = await fetch(`${API_URL}/projects/${scriptId}/frames/${frameId}/audio`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(request),
        });
        if (!response.ok) throw new Error(apiErrors.generateLineAudio);
        const data = await response.json() as ProjectApiPayload;
        return normalizeProjectPayload(data);
    },

    updateVoiceParams: async (scriptId: string, charId: string, speed: number, pitch: number, volume: number): Promise<Project> => {
        const request: ApiUpdateVoiceParamsRequest = { speed, pitch, volume };
        const response = await fetch(`${API_URL}/projects/${scriptId}/characters/${charId}/voice_params`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(request),
        });
        if (!response.ok) throw new Error(apiErrors.updateVoiceParams);
        const data = await response.json() as ProjectApiPayload;
        return normalizeProjectPayload(data);
    },

    exportProject: async (scriptId: string, options: ProjectExportOptions): Promise<ProjectExportResponse> => {
        const request: ProjectExportOptions = options;
        const response = await fetch(`${API_URL}/projects/${scriptId}/export`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(request),
        });
        if (!response.ok) throw new Error(apiErrors.exportProject);
        return response.json() as Promise<ProjectExportResponse>;
    },

    generateVideo: async (scriptId: string): Promise<Project> => {
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/generate_video`);
        return normalizeProjectPayload(res.data);
    },

    getEnvConfig: async (): Promise<EnvConfigPayload> => {
        const res = await axios.get<EnvConfigPayload>(`${API_URL}/config/env`);
        return res.data;
    },

    saveEnvConfig: async (config: EnvConfigPayload): Promise<EnvConfigSaveResponse> => {
        const persistableConfig: EnvConfigPayload = { ...config };
        delete persistableConfig.image_model_startup_check;
        const res = await axios.post<EnvConfigSaveResponse>(`${API_URL}/config/env`, persistableConfig, {
            timeout: 60000, // 60 seconds timeout
        });
        return res.data;
    },

    extractLastFrame: async (scriptId: string, frameId: string, videoTaskId: string): Promise<Project> => {
        const request: ApiExtractLastFrameRequest = { video_task_id: videoTaskId };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/frames/${frameId}/extract_last_frame`, request);
        return normalizeProjectPayload(res.data);
    },

    uploadFrameImage: async (scriptId: string, frameId: string, file: File): Promise<Project> => {
        const formData = new FormData();
        formData.append("file", file);
        const response = await fetch(
            `${API_URL}/projects/${scriptId}/frames/${frameId}/upload_image`,
            { method: "POST", body: formData }
        );
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || apiErrors.uploadFrameImage);
        }
        const data = await response.json() as ProjectApiPayload;
        return normalizeProjectPayload(data);
    },

    // ============================================
    // Series APIs
    // ============================================

    // Series CRUD
    createSeries: async (title: string, description?: string): Promise<Series> => {
        const request: ApiCreateSeriesRequest = { title, description };
        const response = await axios.post<ApiSeries>(`${API_URL}/series`, request);
        return normalizeSeriesPayload(response.data);
    },
    listSeries: async (): Promise<Series[]> => {
        const response = await axios.get<ApiSeries[]>(`${API_URL}/series`);
        return response.data.map((item) => normalizeSeriesPayload(item));
    },
    getSeries: async (seriesId: string): Promise<SeriesDetailResponse> => {
        const response = await axios.get<ApiSeriesDetailResponse>(`${API_URL}/series/${seriesId}`);
        return normalizeSeriesDetailPayload(response.data);
    },
    updateSeries: async (seriesId: string, data: { title?: string; description?: string }): Promise<Series> => {
        const request: ApiUpdateSeriesRequest = data;
        const response = await axios.put<ApiSeries>(`${API_URL}/series/${seriesId}`, request);
        return normalizeSeriesPayload(response.data);
    },
    deleteSeries: async (seriesId: string): Promise<ApiDeleteResponse> => {
        const response = await axios.delete<ApiDeleteResponse>(`${API_URL}/series/${seriesId}`);
        return response.data;
    },

    // Series Episodes
    getSeriesEpisodes: async (seriesId: string): Promise<Project[]> => {
        const response = await axios.get<ProjectApiPayload[]>(`${API_URL}/series/${seriesId}/episodes`);
        return response.data.map((project) => normalizeProjectPayload(project));
    },
    addEpisodeToSeries: async (seriesId: string, scriptId: string, episodeNumber?: number): Promise<Series> => {
        const request: ApiAddEpisodeRequest = { script_id: scriptId, episode_number: episodeNumber };
        const response = await axios.post<ApiSeries>(`${API_URL}/series/${seriesId}/episodes`, request);
        return normalizeSeriesPayload(response.data);
    },
    removeEpisodeFromSeries: async (seriesId: string, scriptId: string): Promise<Series> => {
        const response = await axios.delete<ApiSeries>(`${API_URL}/series/${seriesId}/episodes/${scriptId}`);
        return normalizeSeriesPayload(response.data);
    },

    // Series Assets
    getSeriesAssets: async (seriesId: string): Promise<SeriesAssetsResponse> => {
        const response = await axios.get<ApiSeriesAssetsResponse>(`${API_URL}/series/${seriesId}/assets`);
        return normalizeSeriesAssetsPayload(response.data);
    },
    importSeriesAssets: async (seriesId: string, sourceSeriesId: string, assetIds: string[]): Promise<Series> => {
        const request: ApiImportAssetsRequest = { source_series_id: sourceSeriesId, asset_ids: assetIds };
        const response = await axios.post<ApiSeries>(`${API_URL}/series/${seriesId}/assets/import`, request);
        return normalizeSeriesPayload(response.data);
    },

    // Series Prompt Config
    getSeriesPromptConfig: async (seriesId: string): Promise<PromptConfigResponse> => {
        const response = await axios.get<ApiPromptConfigResponse>(`${API_URL}/series/${seriesId}/prompt_config`);
        return normalizePromptConfigResponse(response.data);
    },
    updateSeriesPromptConfig: async (seriesId: string, config: ApiPromptConfig): Promise<Series> => {
        const response = await axios.put<ApiSeries>(`${API_URL}/series/${seriesId}/prompt_config`, config);
        return normalizeSeriesPayload(response.data);
    },
    getSeriesModelSettings: async (seriesId: string): Promise<ApiModelSettings> => {
        const response = await axios.get<ApiModelSettings>(`${API_URL}/series/${seriesId}/model_settings`);
        return response.data;
    },
    updateSeriesModelSettings: async (seriesId: string, settings: {
        t2i_model?: string;
        i2i_model?: string;
        i2v_model?: string;
        character_aspect_ratio?: string;
        scene_aspect_ratio?: string;
        prop_aspect_ratio?: string;
        storyboard_aspect_ratio?: string;
    }): Promise<Series> => {
        const request: ApiUpdateModelSettingsRequest = settings;
        const response = await axios.put<ApiSeries>(`${API_URL}/series/${seriesId}/model_settings`, request);
        return normalizeSeriesPayload(response.data);
    },

    // Helper: create a project and add it as an episode to a series
    createEpisodeForSeries: async (seriesId: string, title: string, episodeNumber: number): Promise<Project> => {
        const project = await api.createProject(title, "", true);
        await api.addEpisodeToSeries(seriesId, project.id, episodeNumber);
        return project;
    },

    // File Import
    importFilePreview: async (file: File, suggestedEpisodes: number = 3): Promise<SeriesImportPreviewResponse> => {
        const formData = new FormData();
        formData.append('file', file);
        const response = await axios.post<SeriesImportPreviewResponse>(`${API_URL}/series/import/preview?suggested_episodes=${suggestedEpisodes}`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },
    importFileConfirm: async (data: SeriesImportConfirmPayload): Promise<SeriesImportConfirmResponse> => {
        const response = await axios.post<SeriesImportConfirmResponse>(`${API_URL}/series/import/confirm`, data);
        return response.data;
    },
};

// ============================================
// CRUD APIs for Assets and Frames
// ============================================

export const crudApi = {
    // Character CRUD
    createCharacter: async (scriptId: string, data: {
        name: string;
        description?: string;
        age?: string;
        gender?: string;
        clothing?: string;
    }): Promise<Project> => {
        const request: ApiAddCharacterRequest = {
            name: data.name,
            description: data.description ?? "",
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/characters`, request);
        return normalizeProjectPayload(res.data);
    },

    deleteCharacter: async (scriptId: string, characterId: string): Promise<Project> => {
        const res = await axios.delete<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/characters/${characterId}`);
        return normalizeProjectPayload(res.data);
    },

    // Scene CRUD
    createScene: async (scriptId: string, data: {
        name: string;
        description?: string;
        time_of_day?: string;
        lighting_mood?: string;
    }): Promise<Project> => {
        const request: ApiAddSceneRequest = {
            name: data.name,
            description: data.description ?? "",
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/scenes`, request);
        return normalizeProjectPayload(res.data);
    },

    deleteScene: async (scriptId: string, sceneId: string): Promise<Project> => {
        const res = await axios.delete<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/scenes/${sceneId}`);
        return normalizeProjectPayload(res.data);
    },

    // Prop CRUD
    createProp: async (scriptId: string, data: {
        name: string;
        description?: string;
    }): Promise<Project> => {
        const request: ApiCreatePropRequest = {
            name: data.name,
            description: data.description ?? "",
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/props`, request);
        return normalizeProjectPayload(res.data);
    },

    deleteProp: async (scriptId: string, propId: string): Promise<Project> => {
        const res = await axios.delete<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/props/${propId}`);
        return normalizeProjectPayload(res.data);
    },

    // Frame CRUD
    createFrame: async (scriptId: string, data: {
        scene_id: string;
        action_description: string;
        character_ids?: string[];
        prop_ids?: string[];
        dialogue?: string;
        speaker?: string;
        camera_angle?: string;
        insert_at?: number;
    }): Promise<Project> => {
        const request = {
            scene_id: data.scene_id,
            action_description: data.action_description,
            camera_angle: data.camera_angle,
            insert_at: data.insert_at,
        } satisfies ApiAddFrameRequest;
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/frames`, request);
        return normalizeProjectPayload(res.data);
    },

    deleteFrame: async (scriptId: string, frameId: string): Promise<Project> => {
        const res = await axios.delete<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/frames/${frameId}`);
        return normalizeProjectPayload(res.data);
    },

    copyFrame: async (scriptId: string, frameId: string, insertAt?: number): Promise<Project> => {
        const request: ApiCopyFrameRequest = {
            frame_id: frameId,
            insert_at: insertAt
        };
        const res = await axios.post<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/frames/copy`, request);
        return normalizeProjectPayload(res.data);
    },

    reorderFrames: async (scriptId: string, frameIds: string[]): Promise<Project> => {
        const request: ApiReorderFramesRequest = {
            frame_ids: frameIds
        };
        const res = await axios.put<ProjectApiPayload>(`${API_URL}/projects/${scriptId}/frames/reorder`, request);
        return normalizeProjectPayload(res.data);
    }
};
