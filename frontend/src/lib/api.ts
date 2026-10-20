import axios from "axios";
import { DEFAULT_I2V_MODEL_ID } from "@/lib/modelCatalog";

// Dynamic API URL detection (no port enumeration):
// 1. Explicit override: NEXT_PUBLIC_API_URL (any env / proxy setup).
// 2. Dev mode (`next dev`, NODE_ENV==='development'): backend runs on a separate
//    port, so target the same host on the backend port — works for ANY dev port.
// 3. Production / packaged (Electron): frontend is served by the backend, so use
//    the same origin.
const BACKEND_PORT = process.env.NEXT_PUBLIC_BACKEND_PORT || "17177";

const getApiUrl = (): string => {
    // Explicit override always wins (strip any trailing slash).
    const override = process.env.NEXT_PUBLIC_API_URL;
    if (override && override.trim()) {
        return override.trim().replace(/\/+$/, "");
    }

    if (typeof window !== 'undefined') {
        const { protocol, hostname, port } = window.location;

        // Tauri desktop: frontend served via tauri:// protocol, backend on localhost.
        if (protocol === 'tauri:' || protocol === 'https:' && hostname === 'tauri.localhost') {
            return `http://127.0.0.1:${BACKEND_PORT}`;
        }

        // Dev server: backend lives on a different port regardless of which
        // dev port Next.js picked (3008/3009/3018/...).
        if (process.env.NODE_ENV === 'development') {
            return `${protocol}//${hostname}:${BACKEND_PORT}`;
        }

        // Production / packaged: frontend is served by the backend → same origin.
        return `${protocol}//${hostname}${port ? ':' + port : ''}`;
    }

    // SSR fallback
    return `http://localhost:${BACKEND_PORT}`;
};

export const API_URL = getApiUrl();

export type ProviderMode = "dashscope" | "vendor";

/**
 * PR-3g #3 · TTS voice metadata returned by GET /voices.
 * Family-aware fields (family/dialect/lang_primary/supports_instruction)
 * power the Voice picker modal's tabbed UI (Q15.5 B):
 *   Tab 1 系统音色 → origin === "system"
 *   Tab 2 我的复刻 → origin === "clone"   (PR-3h)
 *   Tab 3 我的设计 → origin === "design"  (PR-3i)
 * Inside Tab 1, group by family (cosyvoice / qwen3) + dialect markers.
 */
export interface VoiceMeta {
    id: string;
    name: string;
    gender: "Male" | "Female" | "Neutral" | "Unknown";
    model: string;                                            // backend model id (cosyvoice-v3-flash / qwen3-tts-flash / ...)
    family: "cosyvoice" | "qwen3";
    supports_instruction: boolean;
    dialect?: string | null;                                  // 'shanghai' | 'beijing' | 'sichuan' | 'cantonese' | etc.
    lang_primary?: string | null;                             // 'es' | 'ru' | 'it' | 'ko' | 'ja' | 'de' | 'fr' for international
    origin: "system" | "clone" | "design";
}

/**
 * PR-3h · Custom voice entry from series.custom_voices[].
 * Returned by GET /series/{id}/custom_voices and POST /voice/clone (single).
 * Picker tabs 2/3 render these alongside the system catalog.
 */
export interface CustomVoice {
    id: string;                            // dashscope voice_id
    label: string;                         // user-given display name
    origin: "clone" | "design";
    target_model: string;                  // e.g. "cosyvoice-v3.5-plus"
    family: "cosyvoice" | "qwen3";
    created_at: number;
    source_audio_url?: string | null;      // clone-specific
    voice_prompt?: string | null;          // design-specific (PR-3i)
}

export interface EnvConfigPayload {
    DASHSCOPE_API_KEY?: string;
    ALIBABA_CLOUD_ACCESS_KEY_ID?: string;
    ALIBABA_CLOUD_ACCESS_KEY_SECRET?: string;
    OSS_BUCKET_NAME?: string;
    OSS_ENDPOINT?: string;
    OSS_BASE_PATH?: string;
    OSS_ENABLE?: boolean;
    KLING_PROVIDER_MODE?: ProviderMode;
    VIDU_PROVIDER_MODE?: ProviderMode;
    PIXVERSE_PROVIDER_MODE?: ProviderMode;
    KLING_ACCESS_KEY?: string;
    KLING_SECRET_KEY?: string;
    VIDU_API_KEY?: string;
    endpoint_overrides?: Record<string, string>;
    // Secrets from GET are masked (bullets + last 4 chars). This map reports
    // which credential fields are actually configured on the backend.
    secrets_configured?: Record<string, boolean>;
    [key: string]: string | Record<string, string> | Record<string, boolean> | boolean | undefined;
}

// R2V v2 Phase 4 — Cross-episode reconcile types
export interface ReconcileSuggestion {
    local_id: string;
    local_name: string;
    suggested_series_id: string | null;
    suggested_series_name: string | null;
    confidence: number;
}

export interface BgmPreset {
    id: string;
    label: string;
    mood: string;
    url: string;
}

export interface ReconcileAction {
    local_id: string;
    action: "merge_into_series" | "create_new_in_series" | "skip";
    target_series_id?: string;
}

export interface VideoTask {
    id: string;
    project_id: string;
    image_url: string;
    prompt: string;
    status: "pending" | "processing" | "completed" | "failed";
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
    frame_id?: string;
    generation_mode?: string;
    reference_video_urls?: string[];
    reference_image_urls?: string[];
    ratio?: string;
    /** Failure reason set by pipeline / cancel / orphan recovery. */
    error?: string | null;
    /** User-starred shortlist flag (multi-select per shot) — set via
     *  PATCH /annotate. Optional on the wire so older task records
     *  parse unchanged. */
    is_starred?: boolean;
    /** User-attached short free-text note (≤20 chars, server-truncated). */
    label?: string | null;
    /** Source tab in the Storyboard R2V workbench. Pre-Phase-2 records
     *  parse with null/undefined; CandidatesSection falls back to
     *  generation_mode to bucket them in that case. */
    workbench_tab?: "t2i_i2v" | "direct_r2v" | null;
    /** Provider-side identifiers (Issue 17). Used by TaskQueuePanel to let
     *  users copy IDs into the provider's console (Bailian / 百炼 etc.) for
     *  diagnosis. Different platforms use different naming — these are
     *  normalized canonical fields. provider_request_id may be absent for
     *  platforms that don't expose one (Vidu / PixVerse). */
    provider_name?: string | null;
    provider_task_id?: string | null;
    provider_request_id?: string | null;
}

// ─── Storyboard Schema v2 types ─────────────────────────────────────────────

export interface DialogueStructured {
    speaker: string;
    line: string;
    emotion?: string | null;
    delivery?: string | null;
}

export interface CameraMovementStructured {
    primary: string;
    secondary?: string | null;
    speed: string;
    description?: string | null;
}

export interface BlockingData {
    description?: string | null;
    stage?: Array<{
        ref: string;
        zone?: string | null;
        depth?: string | null;
        height?: string | null;
        facing?: string | null;
        posture?: string | null;
    }> | null;
    camera_relation?: string | null;
}

export interface AudioNoteData {
    sfx?: string | null;
    ambience?: string | null;
    bgm_note?: string | null;
}

export interface LightingData {
    direction?: string | null;
    quality?: string | null;
    color_temp?: string | null;
    description?: string | null;
}

export interface RefineSSEEvent {
    type: "frame_refine_start" | "frame_refine_complete" | "frame_refine_error" | "batch_complete";
    frame_id?: string;
    frame_index?: number;
    total?: number;
    error?: string;
}

export const api = {
    createProject: async (title: string, text: string, skipAnalysis: boolean = false, workflowMode: string = "r2v", seriesId?: string) => {
        const res = await axios.post(`${API_URL}/projects`, { title, text, workflow_mode: workflowMode, series_id: seriesId }, {
            params: { skip_analysis: skipAnalysis }
        });
        return { ...res.data, originalText: res.data.original_text };
    },

    getProjects: async () => {
        const res = await axios.get(`${API_URL}/projects/`);
        return res.data.map((p: any) => ({ ...p, originalText: p.original_text }));
    },

    getProject: async (scriptId: string) => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}`);
        return { ...res.data, originalText: res.data.original_text };
    },

    deleteProject: async (scriptId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}`);
        return res.data;
    },

    /** Toggle the user-starred (featured) flag on a project. Returns the
     *  updated Script. No request body — the backend flips the current flag. */
    toggleProjectStarred: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/toggle_starred`);
        return res.data;
    },

    reparseProject: async (scriptId: string, text: string) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/reparse`, { text });
        return { ...res.data, originalText: res.data.original_text };
    },

    extractPreview: async (scriptId: string, text: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/extract_preview`, { text });
        return res.data as { characters: any[]; scenes: any[]; props: any[] };
    },

    /** Persist `original_text` without LLM reparse. Used for textarea
     *  blur-saves so navigation/reload doesn't drop in-progress drafts. */
    updateScriptText: async (scriptId: string, text: string) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/text`, { text });
        return { ...res.data, originalText: res.data.original_text };
    },

    syncDescriptions: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/sync_descriptions`);
        return res.data;
    },

    generateAssets: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/generate_assets`);
        return res.data;
    },

    createVideoTask: async (
        id: string,
        image_url: string,
        prompt: string,
        duration: number = 5,
        seed?: number,
        resolution: string = "720p",
        generateAudio: boolean = false,
        audioUrl?: string,
        promptExtend: boolean = true,
        negativePrompt?: string,
        batchSize: number = 1,
        model: string = DEFAULT_I2V_MODEL_ID,
        frameId?: string,
        shotType: string = "single",  // 'single' or 'multi' (only for wan2.6-i2v)
        generationMode: string = "i2v",  // 'i2v' or 'r2v'
        referenceVideoUrls: string[] = [],  // Reference videos for R2V (max 3)
        // Kling params
        mode?: string,
        sound?: boolean,
        cfgScale?: number,
        // Vidu params
        viduAudio?: boolean,
        movementAmplitude?: string,
        // HappyHorse params
        referenceImageUrls: string[] = [],  // Reference images for HappyHorse R2V (1-9)
        ratio?: string,  // Aspect ratio: 16:9, 9:16, 1:1, 4:3, 3:4
        // Storyboard R2V workbench tab the user clicked Generate from.
        // Distinct from generationMode (backend dispatcher); workbench_tab
        // lets the candidates panel group takes per UI tab on refresh.
        workbenchTab?: "t2i_i2v" | "direct_r2v",
        // Watermark toggle — supported across wan / kling / vidu / pixverse /
        // happyhorse video. undefined = leave to provider default (typically
        // off); explicit boolean is user's Advanced-section choice.
        watermark?: boolean
    ) => {
        const res = await axios.post(`${API_URL}/projects/${id}/video_tasks`, {
            image_url,
            prompt,
            duration,
            seed,
            resolution,
            generate_audio: generateAudio,
            audio_url: audioUrl,
            prompt_extend: promptExtend,
            negative_prompt: negativePrompt,
            batch_size: batchSize,
            model,
            frame_id: frameId,
            shot_type: shotType,
            generation_mode: generationMode,
            reference_video_urls: referenceVideoUrls,
            // Kling
            mode,
            sound: sound != null ? (sound ? "on" : "off") : undefined,
            cfg_scale: cfgScale,
            // Vidu
            vidu_audio: viduAudio,
            movement_amplitude: movementAmplitude,
            // HappyHorse
            reference_image_urls: referenceImageUrls,
            ratio,
            watermark,
            workbench_tab: workbenchTab,
        });
        return res.data;
    },

    /** Upload an external image as a T2I首帧 candidate for an I2V flow.
     *  Backend appends to the frame's t2i_image_urls history and auto-
     *  selects the new image (it becomes the active首帧, unlocking
     *  Step 2). Returns the updated frame.
     *
     *  Validation lives on the backend:
     *   - ≤ 8 MB (413 if exceeded)
     *   - jpg/jpeg/png/webp only (415 if not)
     *  The caller does cheap front-side checks first to avoid a
     *  round-trip on obvious rejects (file type / size from the File
     *  object) and surfaces backend errors verbatim otherwise. */
    uploadT2IFrame: async (scriptId: string, frameId: string, file: File) => {
        const formData = new FormData();
        formData.append("file", file);
        const res = await axios.post(
            `${API_URL}/projects/${scriptId}/frames/${frameId}/upload_t2i`,
            formData,
            { headers: { "Content-Type": "multipart/form-data" } },
        );
        return res.data;
    },

    /** Persist Storyboard R2V workbench state onto a frame.
     *  Used by StoryboardR2V to write tab/T2I history/active index/
     *  batch-count whenever the user changes them. Server clamps:
     *    t2i_image_urls ≤ 10 FIFO,
     *    t2i_selected_index ∈ [0, len-1],
     *    workbench_generate_count ∈ [1, 6].
     *  Unknown tab_mode returns 400. */
    updateFrameWorkbench: async (
        scriptId: string,
        frameId: string,
        patch: {
            workbench_tab_mode?: "t2i_i2v" | "direct_r2v";
            t2i_image_urls?: string[];
            t2i_selected_index?: number;
            workbench_generate_count?: number;
        },
    ) => {
        const res = await axios.patch(
            `${API_URL}/projects/${scriptId}/frames/${frameId}/workbench`,
            patch,
        );
        return res.data;
    },


    uploadFile: async (file: File) => {
        const formData = new FormData();
        formData.append("file", file);
        const response = await fetch(`${API_URL}/upload`, {
            method: "POST",
            body: formData,
        });
        if (!response.ok) throw new Error("Failed to upload file");
        return response.json();
    },

    /** Lightweight liveness probe + log path. Used by the Diagnose UI
     *  on stuck tasks. 5s timeout because it's only meant to confirm
     *  the backend is alive, not to wait through a slow request. */
    healthCheck: async (): Promise<{
        ok: boolean;
        time: number;
        log_file: string;
        log_dir: string;
        studio_projects: number;
    }> => {
        const res = await axios.get(`${API_URL}/health`, { timeout: 5000 });
        return res.data;
    },

    /** System dependency report (ffmpeg detection + version) used by
     *  Settings → About. ffmpeg -version can be slightly slow, so a
     *  more generous timeout than healthCheck. */
    checkSystem: async (): Promise<{
        system_info?: Record<string, unknown>;
        dependencies?: {
            ffmpeg?: { available: boolean; message: string; path: string | null };
        };
        status?: string;
    }> => {
        const res = await axios.get(`${API_URL}/system/check`, { timeout: 10000 });
        return res.data;
    },

    /** Return last N lines of the backend log + any ERROR-flavored
     *  lines, for the Diagnose UI on stuck tasks. Backend caps at
     *  1000 lines so a runaway client can't drag the server. */
    diagnoseLogTail: async (lines: number = 200): Promise<{
        path: string;
        total_lines?: number;
        returned_lines?: number;
        lines: string[];
        errors: string[];
        missing: boolean;
    }> => {
        const res = await axios.get(`${API_URL}/diagnose/log_tail`, {
            params: { lines },
            timeout: 8000,
        });
        return res.data;
    },

    /** Set the user's star + label annotations on a video task. Used
     *  by Storyboard's candidates panel (shortlist + free-text note).
     *  All payload fields optional; pass clear_label=true to remove
     *  the label explicitly (label=null on its own = "don't change"). */
    annotateVideoTask: async (
        scriptId: string,
        taskId: string,
        payload: { is_starred?: boolean; label?: string | null; clear_label?: boolean },
    ) => {
        const res = await axios.patch(
            `${API_URL}/projects/${scriptId}/video_tasks/${taskId}/annotate`,
            payload,
        );
        return res.data;
    },

    /** Mark a video task as failed-by-cancel. Provider-side render
     *  keeps going; this just unblocks the local UI. Already-completed
     *  tasks are a 404 no-op. */
    cancelVideoTask: async (scriptId: string, taskId: string) => {
        const res = await axios.post(
            `${API_URL}/projects/${scriptId}/video_tasks/${taskId}/cancel`,
        );
        return res.data;
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
    ) => {
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
            throw new Error(errorData.detail || "Failed to upload asset");
        }

        return response.json();
    },

    generateAsset: async (scriptId: string, assetId: string, assetType: string, stylePreset: string, stylePrompt?: string, generationType: string = "all", prompt: string = "", applyStyle: boolean = true, negativePrompt: string = "", batchSize: number = 1, modelName?: string, aspectRatio?: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/generate`, {
            asset_id: assetId,
            asset_type: assetType,
            style_preset: stylePreset,
            style_prompt: stylePrompt,
            generation_type: generationType,
            prompt: prompt,
            apply_style: applyStyle,
            negative_prompt: negativePrompt,
            batch_size: batchSize,
            model_name: modelName,
            aspect_ratio: aspectRatio,
        });
        return res.data;
    },

    getTaskStatus: async (taskId: string) => {
        const res = await axios.get(`${API_URL}/tasks/${taskId}`);
        return res.data;
    },

    generateAssetVideo: async (scriptId: string, assetType: string, assetId: string, data: { prompt?: string, duration?: number, aspect_ratio?: string }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/${assetType}/${assetId}/generate_video`, data);
        return res.data;
    },

    /**
     * Generate Motion Reference video for an asset (Character Full Body/Headshot, Scene, or Prop).
     * This is part of Asset Activation v2.
     */
    generateMotionRef: async (
        scriptId: string,
        assetId: string,
        assetType: 'full_body' | 'head_shot' | 'scene' | 'prop',
        prompt?: string,
        audioUrl?: string,
        duration: number = 5,
        batchSize: number = 1
    ): Promise<any & { _task_id?: string }> => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/generate_motion_ref`, {
            asset_id: assetId,
            asset_type: assetType,
            prompt,
            audio_url: audioUrl,
            duration,
            batch_size: batchSize
        });
        return res.data;
    },

    deleteAssetVideo: async (scriptId: string, assetType: string, assetId: string, videoId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/assets/${assetType}/${assetId}/videos/${videoId}`);
        return res.data;
    },

    toggleAssetLock: async (scriptId: string, assetId: string, assetType: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/toggle_lock`, {
            asset_id: assetId,
            asset_type: assetType
        });
        return res.data;
    },

    toggleAssetStarred: async (scriptId: string, assetId: string, assetType: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/toggle_starred`, {
            asset_id: assetId,
            asset_type: assetType
        });
        return res.data;
    },

    toggleSeriesAssetStarred: async (seriesId: string, assetId: string, assetType: string) => {
        const res = await axios.post(`${API_URL}/series/${seriesId}/assets/toggle_starred`, {
            asset_id: assetId,
            asset_type: assetType
        });
        return res.data;
    },

    updateAssetImage: async (scriptId: string, assetId: string, assetType: string, imageUrl: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/update_image`, {
            asset_id: assetId,
            asset_type: assetType,
            image_url: imageUrl
        });
        return res.data;
    },

    selectAssetVariant: async (scriptId: string, assetId: string, assetType: string, variantId: string, generationType?: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/variant/select`, {
            asset_id: assetId,
            asset_type: assetType,
            variant_id: variantId,
            generation_type: generationType
        });
        return res.data;
    },

    deleteAssetVariant: async (scriptId: string, assetId: string, assetType: string, variantId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/variant/delete`, {
            asset_id: assetId,
            asset_type: assetType,
            variant_id: variantId
        });
        return res.data;
    },

    favoriteAssetVariant: async (scriptId: string, assetId: string, assetType: string, variantId: string, isFavorited: boolean, generationType?: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/variant/favorite`, {
            asset_id: assetId,
            asset_type: assetType,
            variant_id: variantId,
            is_favorited: isFavorited,
            generation_type: generationType
        });
        return res.data;
    },

    updateModelSettings: async (
        scriptId: string,
        t2iModel?: string,
        i2iModel?: string,
        i2vModel?: string,
        characterAspectRatio?: string,
        sceneAspectRatio?: string,
        propAspectRatio?: string,
        storyboardAspectRatio?: string,
        imageModel?: string,
        r2vModel?: string,
    ) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/model_settings`, {
            t2i_model: t2iModel,
            i2i_model: i2iModel,
            i2v_model: i2vModel,
            r2v_model: r2vModel,
            image_model: imageModel,
            character_aspect_ratio: characterAspectRatio,
            scene_aspect_ratio: sceneAspectRatio,
            prop_aspect_ratio: propAspectRatio,
            storyboard_aspect_ratio: storyboardAspectRatio
        });
        return res.data;
    },

    getPromptConfig: async (scriptId: string) => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}/prompt_config`);
        return res.data;
    },

    updatePromptConfig: async (scriptId: string, config: { storyboard_polish?: string; video_polish?: string; r2v_polish?: string; entity_extraction?: string; style_analysis?: string; storyboard_extraction?: string }) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/prompt_config`, config);
        return res.data;
    },

    /** Phase-2 默认 Prompt — built-in DEFAULT text for all six prompt keys
     *  (storyboard_polish / video_polish / r2v_polish / entity_extraction /
     *  style_analysis / storyboard_extraction). Settings pre-fills fields from
     *  this; on save it stores "" for any field still equal to its default
     *  (delta semantics → backend uses the built-in). */
    fetchPromptDefaults: async (): Promise<Record<string, string>> => {
        const res = await axios.get<Record<string, string>>(`${API_URL}/prompt_defaults`);
        return res.data;
    },

    selectVideo: async (scriptId: string, frameId: string, videoId: string) => {
        // Manual pick — sets frame.is_video_pinned=true so future
        // auto_select_latest_video calls (fired by R2V poll completion)
        // skip this frame.
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/select_video`, {
            video_id: videoId
        });
        return res.data;
    },

    autoSelectLatestVideo: async (scriptId: string, frameId: string) => {
        // Fire-and-forget on every R2V poll completion. Backend picks the
        // latest completed task for this frame and updates frame.video_url
        // unless the user has pinned a different take.
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/auto_select_latest_video`);
        return res.data;
    },

    unpinVideo: async (scriptId: string, frameId: string) => {
        // Clear the pin; selected_video_id and video_url stay put until
        // the next auto-select picks a newer completed task.
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/unpin_video`);
        return res.data;
    },

    mergeVideos: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/merge`);
        return res.data;
    },

    // Art Direction APIs
    analyzeScriptForStyles: async (scriptId: string, scriptText: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/art_direction/analyze`, {
            script_text: scriptText
        });
        return res.data;
    },

    saveArtDirection: async (scriptId: string, selectedStyleId: string, styleConfig: any, customStyles: any[] = [], aiRecommendations: any[] = []) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/art_direction/save`, {
            selected_style_id: selectedStyleId,
            style_config: styleConfig,
            custom_styles: customStyles,
            ai_recommendations: aiRecommendations
        });
        return res.data;
    },

    getStylePresets: async () => {
        const res = await axios.get(`${API_URL}/art_direction/presets`);
        return res.data;
    },

    // NOTE: polishPrompt removed - use refineFramePrompt for storyboard prompts
    //
    // 后端契约（#117）：
    //   成功 → 200 + { prompt_cn, prompt_en }
    //   失败 → 502 + { detail: { reason, message_zh, message_en, prompt_cn?, prompt_en? } }
    //     reason ∈ is_configured_false | api_error | json_parse_error | missing_keys | model_echo
    //     model_echo 是 warning（带原文），其余是 hard error。
    //
    // prevCn（#119）：迭代时传入上一次 CN 实现双语锚点；首次留空。
    // Issue 13: image_urls + polish_model added.
    //   image_urls: I2V — pass active first frame URL (T2I selection or
    //     storyboard frame); R2V — pass reference image URLs. Empty/omit
    //     for T2I-only / no-frame shots ⇒ backend falls back to text-only.
    //   polishModel: explicit override; "" lets backend resolve from
    //     project/series PromptConfig.polish_model, then default.
    polishVideoPrompt: async (
        draftPrompt: string,
        feedback: string = "",
        scriptId: string = "",
        prevCn: string = "",
        imageUrls: string[] = [],
        polishModel: string = "",
    ) => {
        const res = await axios.post(`${API_URL}/video/polish_prompt`, {
            draft_prompt: draftPrompt,
            feedback: feedback,
            script_id: scriptId,
            prev_cn: prevCn,
            image_urls: imageUrls,
            polish_model: polishModel,
        });
        return res.data;
    },
    polishR2VPrompt: async (
        draftPrompt: string,
        slots: { description: string }[],
        feedback: string = "",
        scriptId: string = "",
        prevCn: string = "",
        imageUrls: string[] = [],
        polishModel: string = "",
    ) => {
        const res = await axios.post(`${API_URL}/video/polish_r2v_prompt`, {
            draft_prompt: draftPrompt,
            slots: slots,
            feedback: feedback,
            script_id: scriptId,
            prev_cn: prevCn,
            image_urls: imageUrls,
            polish_model: polishModel,
        });
        return res.data;
    },
    updateAssetDescription: async (scriptId: string, assetId: string, assetType: string, description: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/update_description`, {
            asset_id: assetId,
            asset_type: assetType,
            description: description
        });
        return res.data;
    },

    updateAssetAttributes: async (scriptId: string, assetId: string, assetType: string, attributes: any) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/assets/update_attributes`, {
            asset_id: assetId,
            asset_type: assetType,
            attributes: attributes
        });
        return res.data;
    },

    toggleFrameLock: async (scriptId: string, frameId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/toggle_lock`, {
            frame_id: frameId
        });
        return res.data;
    },

    updateFrame: async (scriptId: string, frameId: string, data: {
        image_prompt?: string;
        action_description?: string;
        dialogue?: string;
        camera_angle?: string;
        scene_id?: string;
        character_ids?: string[];
        duration?: number;
        shot_size?: string;
        camera_movement_description?: string;
        transition_hint?: string;
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/update`, {
            frame_id: frameId,
            ...data
        });
        return res.data;
    },

    updateProjectStyle: async (scriptId: string, stylePreset: string, stylePrompt?: string) => {
        const res = await axios.patch(`${API_URL}/projects/${scriptId}/style`, {
            style_preset: stylePreset,
            style_prompt: stylePrompt
        });
        return res.data;
    },

    renderFrame: async (scriptId: string, frameId: string, compositionData: any, prompt: string, batchSize: number = 1) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/storyboard/render`, {
            frame_id: frameId,
            composition_data: compositionData,
            prompt: prompt,
            batch_size: batchSize
        });
        return res.data;
    },

    // === STORYBOARD DRAMATIZATION v2 ===

    /**
     * Analyzes script text and generates storyboard frames using AI.
     * Replaces existing frames with newly generated ones.
     */
    analyzeToStoryboard: async (scriptId: string, text: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/storyboard/analyze`, {
            text: text
        });
        return res.data;
    },

    /**
     * Refines a raw prompt into bilingual (CN/EN) prompts using AI.
     * Returns { prompt_cn, prompt_en, frame_updated }.
     */
    refineFramePrompt: async (scriptId: string, frameId: string, rawPrompt: string, assets: any[] = [], feedback: string = "") => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/storyboard/refine_prompt`, {
            frame_id: frameId,
            raw_prompt: rawPrompt,
            assets: assets,
            feedback: feedback
        });
        return res.data;
    },

    generateStoryboard: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/generate_storyboard`);
        return res.data;
    },

    getVoices: async (): Promise<VoiceMeta[]> => {
        const response = await fetch(`${API_URL}/voices`);
        if (!response.ok) throw new Error("Failed to fetch voices");
        return response.json();
    },

    /**
     * PR-3g #3 · Voice picker modal inline ▶ preview.
     * Backend caches by md5(voice_id|text|speed|pitch|volume|instructions);
     * first call generates, subsequent calls return cached URL instantly.
     * Returns relative URL under /files (e.g. "cache/voice_preview/abc.mp3").
     */
    previewVoice: async (params: {
        voice_id: string;
        text: string;
        speed?: number;
        pitch?: number;
        volume?: number;
        instructions?: string;
    }): Promise<{ url: string; cached: boolean }> => {
        const response = await fetch(`${API_URL}/voice/preview`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                voice_id: params.voice_id,
                text: params.text,
                speed: params.speed ?? 1.0,
                pitch: params.pitch ?? 1.0,
                volume: params.volume ?? 50,
                instructions: params.instructions ?? null,
            }),
        });
        if (!response.ok) {
            const detail = await response.text();
            throw new Error(`Voice preview failed: ${response.status} ${detail}`);
        }
        return response.json();
    },

    /**
     * PR-3h · Clone a voice from a reference audio URL.
     * Frontend flow:
     *   1. Upload audio file via /upload → receive URL
     *   2. Call cloneVoice({series_id, audio_url, label}) → CustomVoice
     *   3. Picker modal 我的复刻 tab refreshes via listCustomVoices()
     * Audio requirements: ≤10MB, MP3/WAV/M4A, ≥16kHz, 10-20s recommended.
     */
    cloneVoice: async (params: {
        series_id: string;
        audio_url: string;
        label: string;
        target_model?: string;
    }): Promise<CustomVoice> => {
        const response = await fetch(`${API_URL}/voice/clone`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                series_id: params.series_id,
                audio_url: params.audio_url,
                label: params.label,
                target_model: params.target_model ?? "cosyvoice-v3.5-plus",
            }),
        });
        if (!response.ok) {
            const detail = await response.text();
            throw new Error(`Voice clone failed: ${response.status} ${detail}`);
        }
        return response.json();
    },

    /** PR-3h · List custom voices (clones + designs) on a series. */
    listCustomVoices: async (seriesId: string): Promise<CustomVoice[]> => {
        const response = await fetch(`${API_URL}/series/${seriesId}/custom_voices`);
        if (!response.ok) throw new Error("Failed to list custom voices");
        return response.json();
    },

    /** PR-3h · Remove a custom voice. Does NOT delete on dashscope side. */
    deleteCustomVoice: async (seriesId: string, voiceId: string): Promise<{ removed: boolean }> => {
        const response = await fetch(`${API_URL}/series/${seriesId}/custom_voices/${voiceId}`, {
            method: "DELETE",
        });
        if (!response.ok) throw new Error("Failed to delete custom voice");
        return response.json();
    },

    /**
     * PR-3i · Voice design — mint a new voice from a text prompt + return preview.
     * Iterative: re-call with tweaked voice_prompt; only persist via designVoiceAccept.
     */
    designVoicePreview: async (params: {
        voice_prompt: string;
        preview_text?: string;
        target_model?: string;
    }): Promise<{ voice_id: string; preview_url: string; target_model: string }> => {
        const response = await fetch(`${API_URL}/voice/design/preview`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                voice_prompt: params.voice_prompt,
                preview_text: params.preview_text ?? "你好，这是一段音色测试。请仔细听一听是否符合预期。",
                target_model: params.target_model ?? "cosyvoice-v3.5-plus",
            }),
        });
        if (!response.ok) {
            const detail = await response.text();
            throw new Error(`Voice design preview failed: ${response.status} ${detail}`);
        }
        return response.json();
    },

    /** PR-3i · Commit a previewed design voice to series.custom_voices[]. */
    designVoiceAccept: async (params: {
        series_id: string;
        voice_id: string;
        voice_prompt: string;
        label: string;
        target_model?: string;
    }): Promise<CustomVoice> => {
        const response = await fetch(`${API_URL}/voice/design/accept`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                series_id: params.series_id,
                voice_id: params.voice_id,
                voice_prompt: params.voice_prompt,
                label: params.label,
                target_model: params.target_model ?? "cosyvoice-v3.5-plus",
            }),
        });
        if (!response.ok) {
            const detail = await response.text();
            throw new Error(`Voice design accept failed: ${response.status} ${detail}`);
        }
        return response.json();
    },

    /** PR-3i · LLM helper — translate character.description → CosyVoice voice_prompt. */
    translateVoicePrompt: async (description: string): Promise<{ voice_prompt: string }> => {
        const response = await fetch(`${API_URL}/voice/design/translate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ description }),
        });
        if (!response.ok) {
            const detail = await response.text();
            throw new Error(`Voice prompt translate failed: ${response.status} ${detail}`);
        }
        return response.json();
    },

    bindVoice: async (scriptId: string, charId: string, voiceId: string, voiceName: string) => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/characters/${charId}/voice`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ voice_id: voiceId, voice_name: voiceName }),
        });
        if (!response.ok) throw new Error("Failed to bind voice");
        return response.json();
    },

    generateAudio: async (scriptId: string) => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/generate_audio`, {
            method: "POST",
        });
        if (!response.ok) throw new Error("Failed to generate audio");
        return response.json();
    },

    generateLineAudio: async (
        scriptId: string,
        frameId: string,
        speed: number,
        pitch: number,
        volume: number = 50,
        instructions?: string,
    ) => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/frames/${frameId}/audio`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ speed, pitch, volume, instructions: instructions || null }),
        });
        if (!response.ok) throw new Error("Failed to generate line audio");
        return response.json();
    },

    /** PR-3j · Generate dialogue audio for every frame with dialogue.
     *  Skips frames whose snapshot hash still matches. */
    generateDialogueAudioBatch: async (scriptId: string): Promise<{ _batch_stats: { generated: number; skipped: number; failed: number; no_voice: number } }> => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/dialogue_audio/batch`, {
            method: "POST",
        });
        if (!response.ok) throw new Error("Failed to generate dialogue audio batch");
        return response.json();
    },

    previewDub: async (scriptId: string, frameId: string, videoTaskId: string, offsetMs: number = 0) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/dub/preview`, {
            video_task_id: videoTaskId,
            offset_ms: offsetMs,
        }, { timeout: 120000 });
        return res.data;
    },

    applyDub: async (scriptId: string, frameId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/dub/apply`);
        return res.data;
    },

    revertDub: async (scriptId: string, frameId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/frames/${frameId}/dub`);
        return res.data;
    },

    /** Schema v2 · Refine a single frame (Phase 2 rich fields). */
    refineSingleFrame: async (scriptId: string, frameId: string) => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/frames/${frameId}/refine`, {
            method: "POST",
        });
        if (!response.ok) throw new Error("Failed to refine frame");
        return response.json();
    },

    /** Schema v2 · Batch refine all frames via SSE stream. */
    refineBatchFrames: async (
        scriptId: string,
        onEvent: (event: RefineSSEEvent) => void,
    ): Promise<void> => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/storyboard/refine_batch`, {
            method: "POST",
        });
        if (!response.ok) throw new Error("Failed to start batch refine");
        const reader = response.body?.getReader();
        if (!reader) return;
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";
            let currentEventType = "";
            for (const line of lines) {
                if (line.startsWith("event: ")) {
                    currentEventType = line.slice(7).trim();
                } else if (line.startsWith("data: ")) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        onEvent({ type: currentEventType as RefineSSEEvent["type"], ...data });
                    } catch { /* skip malformed lines */ }
                }
            }
        }
    },

    /** PR-3k · BGM preset catalog for Assembly Mix phase. */
    listBgmPresets: async (): Promise<BgmPreset[]> => {
        const response = await fetch(`${API_URL}/bgm/presets`);
        if (!response.ok) throw new Error("Failed to list bgm presets");
        return response.json();
    },

    /** PR-3k · Update audio mix (BGM url + per-track volumes). */
    updateAudioMix: async (scriptId: string, payload: {
        bgm_url?: string | null;
        dialogue_volume?: number;
        bgm_volume?: number;
        sfx_volume?: number;
    }) => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/audio_mix`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error("Failed to update audio mix");
        return response.json();
    },

    updateVoiceParams: async (scriptId: string, charId: string, speed: number, pitch: number, volume: number) => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/characters/${charId}/voice_params`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ speed, pitch, volume }),
        });
        if (!response.ok) throw new Error("Failed to update voice params");
        return response.json();
    },

    exportProject: async (scriptId: string, options: any) => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/export`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(options),
        });
        if (!response.ok) throw new Error("Failed to export project");
        return response.json();
    },

    generateVideo: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/generate_video`);
        return res.data;
    },

    getEnvConfig: async (): Promise<EnvConfigPayload> => {
        const res = await axios.get<EnvConfigPayload>(`${API_URL}/config/env`);
        return res.data;
    },

    saveEnvConfig: async (config: EnvConfigPayload) => {
        const res = await axios.post(`${API_URL}/config/env`, config, {
            timeout: 60000, // 60 seconds timeout
        });
        return res.data;
    },

    triggerMulerunLogin: async () => {
        const res = await axios.post(`${API_URL}/config/mulerun-login`);
        return res.data;
    },

    extractLastFrame: async (scriptId: string, frameId: string, videoTaskId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/extract_last_frame`, {
            video_task_id: videoTaskId,
        });
        return res.data;
    },

    uploadFrameImage: async (scriptId: string, frameId: string, file: File) => {
        const formData = new FormData();
        formData.append("file", file);
        const response = await fetch(
            `${API_URL}/projects/${scriptId}/frames/${frameId}/upload_image`,
            { method: "POST", body: formData }
        );
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || "Failed to upload frame image");
        }
        return response.json();
    },

    // ============================================
    // Series APIs
    // ============================================

    // Series CRUD
    createSeriesV2: async (
        title: string,
        opts: { description?: string; workflow_mode?: string; content_mode?: "scripted" | "freeform"; default_generation_mode?: "r2v" | "i2v" } = {},
    ) => {
        const response = await axios.post(`${API_URL}/series`, {
            title,
            description: opts.description ?? "",
            workflow_mode: opts.workflow_mode ?? "r2v",
            content_mode: opts.content_mode ?? "scripted",
            default_generation_mode: opts.default_generation_mode ?? "r2v",
        });
        return response.data;
    },

    createSeries: async (title: string, description?: string, workflowMode?: string) => {
        const response = await axios.post(`${API_URL}/series`, { title, description, workflow_mode: workflowMode || "r2v" });
        return response.data;
    },
    listSeries: async () => {
        const response = await axios.get(`${API_URL}/series`);
        return response.data;
    },
    /** Core 全局/共享资产池（跨系列/项目聚合）。后端：GET /library/assets → {characters, scenes, props}。 */
    listLibraryAssets: async () => {
        const res = await axios.get(`${API_URL}/library/assets`);
        return res.data;
    },
    /** 新建一条全局/共享资产。后端：POST /library/assets。
     *  assetType 为单数（"character"|"scene"|"prop"）。data 可含 name/description/persona/image_url/voice_id。 */
    createLibraryAsset: async (
        assetType: string,
        data: { name: string; description?: string; persona?: string; image_url?: string; voice_id?: string },
    ) => {
        const res = await axios.post(`${API_URL}/library/assets`, { asset_type: assetType, ...data });
        return res.data;
    },
    /** 上传一张本地图片到全局资产库，返回可被前端加载的 image_url。
     *  后端契约：POST /library/assets/upload，multipart 字段名 "file" → { image_url }。
     *  调用方拿到 image_url 后传给 createLibraryAsset。 */
    uploadLibraryImage: async (file: File): Promise<{ image_url: string }> => {
        const formData = new FormData();
        formData.append("file", file);
        const res = await axios.post<{ image_url: string }>(`${API_URL}/library/assets/upload`, formData, {
            headers: { "Content-Type": "multipart/form-data" },
        });
        return res.data;
    },
    /** 补丁更新全局资产（仅发送的字段生效，PATCH 语义）。后端：PUT /library/assets/{type}/{id}。assetType 单数。 */
    updateLibraryAsset: async (
        assetType: string,
        assetId: string,
        patch: {
            name?: string;
            description?: string;
            persona?: string;
            image_url?: string;
            voice_id?: string;
            starred?: boolean;
            locked?: boolean;
            visual_weight?: number;
        },
    ) => {
        const res = await axios.put(`${API_URL}/library/assets/${assetType}/${assetId}`, patch);
        return res.data;
    },
    /** 把项目/系列来源资产 deep-copy 提升进全局共享池。后端：POST /library/assets/promote。
     *  sourceKind: "project"|"series"；assetType 单数。 */
    promoteAssetToLibrary: async (
        sourceKind: "project" | "series",
        sourceId: string,
        assetType: string,
        assetId: string,
    ) => {
        const res = await axios.post(`${API_URL}/library/assets/promote`, {
            source_kind: sourceKind,
            source_id: sourceId,
            asset_type: assetType,
            asset_id: assetId,
        });
        return res.data;
    },
    getSeries: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}`);
        return response.data;
    },
    updateSeries: async (
        seriesId: string,
        data: { title?: string; description?: string; art_direction?: any },
    ) => {
        const response = await axios.put(`${API_URL}/series/${seriesId}`, data);
        return response.data;
    },

    /** R2V v2 Phase 3 — fetch previous episode raw snippet + AI summary cache state.
     *  P2-a extended response with last_frames for Storyboard cross-step rail. */
    getPreviousEpisodeSummary: async (scriptId: string): Promise<{
        has_previous: boolean;
        previous_episode_id: string | null;
        previous_episode_title: string | null;
        raw_snippet: string;
        ai_summary: string | null;
        ai_summary_stale: boolean;
        last_frames?: Array<{
            id: string;
            action_description: string;
            thumbnail_url: string | null;
            video_url: string | null;
        }>;
    }> => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}/previous_episode`);
        return res.data;
    },

    /** On-demand generate AI summary of previous episode (user-triggered). */
    generatePreviousEpisodeSummary: async (scriptId: string): Promise<{
        ai_summary: string;
        ai_summary_stale: boolean;
        previous_episode_id: string;
        previous_episode_title: string;
    }> => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/previous_episode/summary`);
        return res.data;
    },

    /** R2V v2 Phase 4 — fetch reconcile suggestions for this episode's
     *  extracted entities vs the parent series's shared library. */
    getReconcileSuggestions: async (scriptId: string): Promise<{
        characters: ReconcileSuggestion[];
        scenes: ReconcileSuggestion[];
        props: ReconcileSuggestion[];
    }> => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}/reconcile/suggestions`);
        return res.data;
    },

    /** Apply user-confirmed reconcile decisions. */
    applyReconcile: async (
        scriptId: string,
        decisions: {
            characters?: ReconcileAction[];
            scenes?: ReconcileAction[];
            props?: ReconcileAction[];
        },
    ) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/reconcile/apply`, decisions);
        return res.data;
    },

    /** R2V v2 Phase 5 — series-scope quick-create CRUD for Cast modal. */
    createSeriesAsset: async (
        seriesId: string,
        kind: "characters" | "scenes" | "props",
        data: { name: string; description?: string; persona?: string; image_url?: string; voice_id?: string },
    ) => {
        const res = await axios.post(`${API_URL}/series/${seriesId}/${kind}`, data);
        return res.data;
    },

    /** R2V v2 Phase 2 — clear project-level art_direction (return to series inherit). */
    clearProjectArtDirection: async (scriptId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/art_direction/clear`);
        return res.data;
    },

    /** R2V v2 P2-b — next-episode hook prediction state. */
    getNextEpisodeHook: async (scriptId: string): Promise<{
        has_text: boolean;
        hook: string | null;
        stale: boolean;
    }> => {
        const res = await axios.get(`${API_URL}/projects/${scriptId}/next_hook`);
        return res.data;
    },

    /** Generate hook prediction (user-triggered). */
    generateNextEpisodeHook: async (scriptId: string): Promise<{
        hook: string;
        stale: boolean;
    }> => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/next_hook`);
        return res.data;
    },

    /** Manually edit / clear hook cache. */
    updateNextEpisodeHook: async (scriptId: string, hook: string | null) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/next_hook`, { hook });
        return res.data;
    },

    /** R2V v2 P1-c — cross-episode character appearances (for @ helper). */
    getCharacterAppearances: async (seriesId: string, characterId: string): Promise<{
        character: { id: string; name: string; persona: string; description: string };
        appearances: Array<{ episode_id: string; episode_number: number | null; episode_title: string; frame_count: number }>;
        total_frames: number;
    }> => {
        const res = await axios.get(`${API_URL}/series/${seriesId}/characters/${characterId}/appearances`);
        return res.data;
    },

    /** R2V v2 P1-b — manually edit / clear last_episode_summary cache. */
    updateLastEpisodeSummary: async (scriptId: string, aiSummary: string | null) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/last_episode_summary`, {
            ai_summary: aiSummary,
        });
        return res.data;
    },
    deleteSeries: async (seriesId: string) => {
        const response = await axios.delete(`${API_URL}/series/${seriesId}`);
        return response.data;
    },

    // Series Episodes
    getSeriesEpisodes: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}/episodes`);
        return response.data;
    },
    addEpisodeToSeries: async (seriesId: string, scriptId: string, episodeNumber?: number) => {
        const response = await axios.post(`${API_URL}/series/${seriesId}/episodes`, { script_id: scriptId, episode_number: episodeNumber });
        return response.data;
    },
    removeEpisodeFromSeries: async (seriesId: string, scriptId: string) => {
        const response = await axios.delete(`${API_URL}/series/${seriesId}/episodes/${scriptId}`);
        return response.data;
    },

    // Series Assets
    getSeriesAssets: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}/assets`);
        return response.data;
    },
    importSeriesAssets: async (seriesId: string, sourceSeriesId: string, assetIds: string[]) => {
        const response = await axios.post(`${API_URL}/series/${seriesId}/assets/import`, { source_series_id: sourceSeriesId, asset_ids: assetIds });
        return response.data;
    },

    // Series Prompt Config
    getSeriesPromptConfig: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}/prompt_config`);
        return response.data;
    },
    updateSeriesPromptConfig: async (seriesId: string, config: { storyboard_polish?: string; video_polish?: string; r2v_polish?: string; storyboard_extraction?: string }) => {
        const response = await axios.put(`${API_URL}/series/${seriesId}/prompt_config`, config);
        return response.data;
    },
    getSeriesModelSettings: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}/model_settings`);
        return response.data;
    },
    updateSeriesModelSettings: async (seriesId: string, settings: {
        t2i_model?: string;
        i2i_model?: string;
        image_model?: string;
        i2v_model?: string;
        character_aspect_ratio?: string;
        scene_aspect_ratio?: string;
        prop_aspect_ratio?: string;
        storyboard_aspect_ratio?: string;
    }) => {
        const response = await axios.put(`${API_URL}/series/${seriesId}/model_settings`, settings);
        return response.data;
    },

    // Helper: create a project and add it as an episode to a series
    createEpisodeForSeries: async (seriesId: string, title: string, episodeNumber: number, workflowMode: string = "r2v") => {
        const project = await api.createProject(title, "", true, workflowMode);
        await api.addEpisodeToSeries(seriesId, project.id, episodeNumber);
        const refreshed = await api.getProject(project.id);
        return refreshed;
    },

    // File Import
    importFilePreview: async (file: File, suggestedEpisodes: number = 3) => {
        const formData = new FormData();
        formData.append('file', file);
        const response = await axios.post(`${API_URL}/series/import/preview?suggested_episodes=${suggestedEpisodes}`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
        return response.data;
    },
    importFileConfirm: async (data: { title: string; description?: string; text: string; episodes: any[] }) => {
        const response = await axios.post(`${API_URL}/series/import/confirm`, data);
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
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/characters`, data);
        return res.data;
    },

    deleteCharacter: async (scriptId: string, characterId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/characters/${characterId}`);
        return res.data;
    },

    // Scene CRUD
    createScene: async (scriptId: string, data: {
        name: string;
        description?: string;
        time_of_day?: string;
        lighting_mood?: string;
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/scenes`, data);
        return res.data;
    },

    deleteScene: async (scriptId: string, sceneId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/scenes/${sceneId}`);
        return res.data;
    },

    // Prop CRUD
    createProp: async (scriptId: string, data: {
        name: string;
        description?: string;
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/props`, data);
        return res.data;
    },

    deleteProp: async (scriptId: string, propId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/props/${propId}`);
        return res.data;
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
    }) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames`, data);
        return res.data;
    },

    deleteFrame: async (scriptId: string, frameId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}/frames/${frameId}`);
        return res.data;
    },

    copyFrame: async (scriptId: string, frameId: string, insertAt?: number) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/copy`, {
            frame_id: frameId,
            insert_at: insertAt
        });
        return res.data;
    },

    reorderFrames: async (scriptId: string, frameIds: string[]) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/frames/reorder`, {
            frame_ids: frameIds
        });
        return res.data;
    }
};

// ─── Playground API ─────────────────────────────────────────────────────────

export interface PlaygroundGenerateRequest {
  mode: string;
  model_id: string;
  prompt: string;
  negative_prompt?: string;
  input_media?: string[];
  parameters?: Record<string, any>;
  batch_size?: number;
}

export interface PlaygroundGenerationResponse {
  id: string;
  mode: string;
  model_id: string;
  prompt: string;
  negative_prompt?: string;
  input_media: string[];
  parameters: Record<string, any>;
  batch_size: number;
  outputs: Array<{
    id: string;
    media_path: string;
    media_type: string;
    thumbnail_path?: string;
    saved_to_library: boolean;
  }>;
  status: string;
  error?: string;
  created_at: string;
}

export interface PlaygroundTemplateResponse {
  id: string;
  name: string;
  category: string;
  prompt: string;
  negative_prompt?: string;
  default_mode?: string;
  default_model_id?: string;
  default_parameters: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export const playgroundApi = {
  generate: (data: PlaygroundGenerateRequest) =>
    axios.post<PlaygroundGenerationResponse>(API_URL + "/playground/generate", data).then(r => r.data),

  getHistory: (limit = 50, offset = 0) =>
    axios.get<PlaygroundGenerationResponse[]>(API_URL + "/playground/history", { params: { limit, offset } }).then(r => r.data),

  getGeneration: (id: string) =>
    axios.get<PlaygroundGenerationResponse>(API_URL + "/playground/history/" + id).then(r => r.data),

  getGenerationStatus: (id: string) =>
    axios.get<{ id: string; status: string; outputs: any[]; error?: string }>(API_URL + "/playground/history/" + id + "/status").then(r => r.data),

  deleteGeneration: (id: string) =>
    axios.delete(API_URL + "/playground/history/" + id).then(r => r.data),

  saveToLibrary: (generationId: string, outputId: string, category?: string) =>
    axios.post(API_URL + "/playground/history/" + generationId + "/outputs/" + outputId + "/save-to-library", { category: category || "general" }).then(r => r.data),

  getTemplates: () =>
    axios.get<PlaygroundTemplateResponse[]>(API_URL + "/playground/templates").then(r => r.data),

  createTemplate: (data: { name: string; category?: string; prompt: string; negative_prompt?: string; default_mode?: string; default_model_id?: string; default_parameters?: Record<string, any> }) =>
    axios.post<PlaygroundTemplateResponse>(API_URL + "/playground/templates", data).then(r => r.data),

  updateTemplate: (id: string, data: Partial<{ name: string; category: string; prompt: string; negative_prompt: string; default_mode: string; default_model_id: string; default_parameters: Record<string, any> }>) =>
    axios.put<PlaygroundTemplateResponse>(API_URL + "/playground/templates/" + id, data).then(r => r.data),

  deleteTemplate: (id: string) =>
    axios.delete(API_URL + "/playground/templates/" + id).then(r => r.data),

  // Upload media file for playground input (returns file path)
  uploadMedia: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return axios.post<{ path: string }>(API_URL + "/playground/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then(r => r.data);
  },
};
