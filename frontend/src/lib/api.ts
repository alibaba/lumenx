import axios from "axios";
import { DEFAULT_I2V_MODEL_ID } from "@/lib/modelCatalog";
import type { Project, StyleConfig } from "@/store/projectStore";

// Dynamic API URL detection:
// 1. In packaged app (Electron): Frontend is served by backend, use same origin
// 2. In development (port 3008/3009, legacy 3000/3001): Use backend port 17177
const getApiUrl = (): string => {
    // If running in browser
    if (typeof window !== 'undefined') {
        const { protocol, hostname, port } = window.location;

        // In development mode (port 3008/3009, legacy 3000/3001 = Next.js dev server)
        // Backend is on a different port
        if (port === '3008' || port === '3009' || port === '3000' || port === '3001') {
            return `${protocol}//${hostname}:17177`;
        }

        // In production/packaged mode: Frontend is served by backend
        // Use same origin
        return `${protocol}//${hostname}${port ? ':' + port : ''}`;
    }

    // SSR fallback
    return 'http://localhost:17177';
};

export const API_URL = getApiUrl();

export type ProviderMode = "dashscope" | "vendor";

export interface EnvConfigPayload {
    DASHSCOPE_API_KEY?: string;
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
    endpoint_overrides?: Record<string, string>;
    [key: string]: string | Record<string, string> | undefined;
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
}

export type AtelierApprovalMode = "untrusted" | "on_failure" | "on_request" | "never";

export interface AtelierAgentPolicy {
    approval_mode: AtelierApprovalMode;
    allowed_tools: string[];
    max_nodes_per_action: number;
    updated_at: number;
}

export type AtelierAgentToolStatus = "proposed" | "approval_required" | "completed" | "denied" | "failed";

export interface AtelierAgentToolCall {
    call_id: string;
    tool_name: string;
    arguments: Record<string, unknown>;
    status: AtelierAgentToolStatus;
    approval_required: boolean;
    approval_granted: boolean;
    error?: string | null;
    result_snapshot?: Record<string, unknown> | null;
    created_at: number;
    completed_at?: number | null;
}

export interface AtelierAgentPlanContext {
    selected_node_id?: string | null;
    planner_input?: Record<string, unknown>;
    planner_schema_version?: string;
    planner_adapter_name?: string | null;
    tool_schema_version?: string;
    model_trace_id?: string | null;
}

export interface AtelierAgentPlannerPackage {
    project_id: string;
    user_message: string;
    selected_node_id?: string | null;
    skill_name?: string | null;
    planner_schema_version: string;
    tool_schema_version: string;
    output_contract: Record<string, unknown>;
    tool_schemas: AtelierAgentToolSpec[];
    project_snapshot: {
        id: string;
        title: string;
        description: string;
        node_count: number;
        nodes: AtelierNode[];
    };
    selected_node_snapshot?: AtelierNode | null;
    policy_snapshot: AtelierAgentPolicy;
    created_at: number;
}

export interface AtelierAgentPlan {
    project_id: string;
    user_message: string;
    planner: string;
    skill_name?: string | null;
    status: "ready" | "blocked";
    reason: string;
    tool_calls: AtelierAgentToolCallPayload[];
    context: AtelierAgentPlanContext;
    created_at: number;
}

export interface AtelierAgentTurn {
    id: string;
    project_id: string;
    user_message: string;
    preview: boolean;
    status: "pending" | "waiting_approval" | "completed" | "failed";
    tool_calls: AtelierAgentToolCall[];
    created_at: number;
    completed_at?: number | null;
    response?: string | null;
}

export interface AtelierAgentToolSpec {
    name: string;
    description: string;
    input_schema: Record<string, unknown>;
    required_permission: "read" | "canvas_write" | "generation";
    mutates_canvas: boolean;
    max_count_cost: number;
    requires_approval: boolean;
}

export interface AtelierAgentToolCallPayload {
    tool_name: string;
    arguments?: Record<string, unknown>;
}

export interface RunAtelierAgentTurnPayload {
    user_message?: string;
    tool_calls: AtelierAgentToolCallPayload[];
    preview?: boolean;
    approve?: boolean;
    deny?: boolean;
    turn_id?: string;
}

export interface PlanAtelierAgentTurnPayload {
    user_message?: string;
    selected_node_id?: string | null;
    skill_name?: string | null;
    planner?: string | null;
    planner_input?: Record<string, unknown>;
}

export interface BuildAtelierAgentPlannerPackagePayload {
    user_message?: string;
    selected_node_id?: string | null;
    skill_name?: string | null;
}

export interface AtelierNode {
    id: string;
    project_id: string;
    type: string;
    title: string;
    prompt: string;
    status: string;
    x: number;
    y: number;
    width: number;
    height: number;
    source_project_id?: string | null;
    frame_id?: string | null;
    asset_id?: string | null;
    video_task_id?: string | null;
    media_urls: string[];
    data: Record<string, unknown>;
    created_by: string;
    created_at: number;
    updated_at: number;
}

export interface AtelierVideoCandidate {
    id: string;
    status: "pending" | "processing" | "completed" | "failed";
    video_url?: string | null;
    prompt: string;
    model: string;
    reference_image_urls: string[];
    params: Record<string, unknown>;
    error?: string | null;
    created_at: number;
    completed_at?: number;
    failed_at?: number | null;
    attempt_count?: number;
    retry_count?: number;
    attempt_started_at?: number;
    retry_requested_at?: number;
    attempts?: Array<Record<string, unknown>>;
    generation_snapshot?: Record<string, unknown>;
    runtime_snapshot?: Record<string, unknown>;
    label?: string;
}

export interface AtelierGenerationConfig {
    prompt: string;
    model: string;
    reference_image_urls: string[];
    batch_size: number;
    params: Record<string, unknown>;
}

export interface AtelierSequenceEntry {
    parentId: string;
    candidateId: string;
    trimStart?: number | null;
    trimEnd?: number | null;
}

export interface AtelierProject {
    id: string;
    title: string;
    description: string;
    source_project_id?: string | null;
    nodes: AtelierNode[];
    agent_policy: AtelierAgentPolicy;
    agent_turns?: AtelierAgentTurn[];
    sequence?: AtelierSequenceEntry[];
    created_at: number;
    updated_at: number;
}

export interface AtelierNodePayload {
    type?: string;
    title?: string;
    prompt?: string;
    status?: string;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    source_project_id?: string | null;
    frame_id?: string | null;
    asset_id?: string | null;
    video_task_id?: string | null;
    media_urls?: string[];
    data?: Record<string, unknown>;
    created_by?: string;
}

type JsonRecord = Record<string, unknown>;

type ApiProjectResponse = Project & {
    original_text?: string;
};

const normalizeProject = (project: ApiProjectResponse): Project => ({
    ...project,
    originalText: project.original_text ?? project.originalText ?? "",
});

export const api = {
    createProject: async (title: string, text: string, skipAnalysis: boolean = false, workflowMode: string = "r2v") => {
        const res = await axios.post<ApiProjectResponse>(`${API_URL}/projects`, { title, text, workflow_mode: workflowMode }, {
            params: { skip_analysis: skipAnalysis }
        });
        return normalizeProject(res.data);
    },

    getProjects: async () => {
        const res = await axios.get<ApiProjectResponse[]>(`${API_URL}/projects/`);
        return res.data.map(normalizeProject);
    },

    getProject: async (scriptId: string) => {
        const res = await axios.get<ApiProjectResponse>(`${API_URL}/projects/${scriptId}`);
        return normalizeProject(res.data);
    },

    deleteProject: async (scriptId: string) => {
        const res = await axios.delete(`${API_URL}/projects/${scriptId}`);
        return res.data;
    },

    reparseProject: async (scriptId: string, text: string) => {
        const res = await axios.put<ApiProjectResponse>(`${API_URL}/projects/${scriptId}/reparse`, { text });
        return normalizeProject(res.data);
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
        ratio?: string  // Aspect ratio: 16:9, 9:16, 1:1, 4:3, 3:4
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
            ratio
        });
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

    healthCheck: async (): Promise<{
        ok: boolean;
        time: number;
        log_file: string;
        log_dir: string;
        studio_projects: number;
        atelier_projects: number;
    }> => {
        const res = await axios.get(`${API_URL}/health`, { timeout: 5000 });
        return res.data;
    },

    cancelVideoTask: async (scriptId: string, taskId: string) => {
        const res = await axios.post(
            `${API_URL}/projects/${scriptId}/video_tasks/${taskId}/cancel`,
        );
        return res.data;
    },

    cancelAtelierCandidate: async (
        projectId: string,
        nodeId: string,
        candidateId: string,
    ) => {
        const res = await axios.post(
            `${API_URL}/atelier/projects/${projectId}/nodes/${nodeId}/video_candidates/${candidateId}/cancel`,
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

    generateAsset: async (scriptId: string, assetId: string, assetType: string, stylePreset: string, stylePrompt?: string, generationType: string = "all", prompt: string = "", applyStyle: boolean = true, negativePrompt: string = "", batchSize: number = 1, modelName?: string) => {
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
            model_name: modelName
        });
        // Now returns { ...script, _task_id: string }
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
    ): Promise<Partial<Project> & { _task_id?: string }> => {
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
        imageModel?: string
    ) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/model_settings`, {
            t2i_model: t2iModel,
            i2i_model: i2iModel,
            i2v_model: i2vModel,
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

    updatePromptConfig: async (scriptId: string, config: { storyboard_polish?: string; video_polish?: string; r2v_polish?: string }) => {
        const res = await axios.put(`${API_URL}/projects/${scriptId}/prompt_config`, config);
        return res.data;
    },

    selectVideo: async (scriptId: string, frameId: string, videoId: string) => {
        const res = await axios.post(`${API_URL}/projects/${scriptId}/frames/${frameId}/select_video`, {
            video_id: videoId
        });
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

    saveArtDirection: async (
        scriptId: string,
        selectedStyleId: string,
        styleConfig: StyleConfig,
        customStyles: StyleConfig[] = [],
        aiRecommendations: StyleConfig[] = []
    ) => {
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
    polishVideoPrompt: async (draftPrompt: string, feedback: string = "", scriptId: string = "") => {
        const res = await axios.post(`${API_URL}/video/polish_prompt`, {
            draft_prompt: draftPrompt,
            feedback: feedback,
            script_id: scriptId,
        });
        return res.data;
    },
    polishR2VPrompt: async (draftPrompt: string, slots: { description: string }[], feedback: string = "", scriptId: string = "") => {
        const res = await axios.post(`${API_URL}/video/polish_r2v_prompt`, {
            draft_prompt: draftPrompt,
            slots: slots,
            feedback: feedback,
            script_id: scriptId,
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

    updateAssetAttributes: async (scriptId: string, assetId: string, assetType: string, attributes: unknown) => {
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

    renderFrame: async (scriptId: string, frameId: string, compositionData: unknown, prompt: string, batchSize: number = 1) => {
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
    refineFramePrompt: async (scriptId: string, frameId: string, rawPrompt: string, assets: JsonRecord[] = [], feedback: string = "") => {
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

    getVoices: async () => {
        const response = await fetch(`${API_URL}/voices`);
        if (!response.ok) throw new Error("Failed to fetch voices");
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

    generateLineAudio: async (scriptId: string, frameId: string, speed: number, pitch: number, volume: number = 50) => {
        const response = await fetch(`${API_URL}/projects/${scriptId}/frames/${frameId}/audio`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ speed, pitch, volume }),
        });
        if (!response.ok) throw new Error("Failed to generate line audio");
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

    exportProject: async (scriptId: string, options: unknown) => {
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
    createSeries: async (title: string, description?: string, workflowMode?: string) => {
        const response = await axios.post(`${API_URL}/series`, { title, description, workflow_mode: workflowMode || "r2v" });
        return response.data;
    },
    listSeries: async () => {
        const response = await axios.get(`${API_URL}/series`);
        return response.data;
    },
    getSeries: async (seriesId: string) => {
        const response = await axios.get(`${API_URL}/series/${seriesId}`);
        return response.data;
    },
    updateSeries: async (seriesId: string, data: { title?: string; description?: string }) => {
        const response = await axios.put(`${API_URL}/series/${seriesId}`, data);
        return response.data;
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
    updateSeriesPromptConfig: async (seriesId: string, config: { storyboard_polish?: string; video_polish?: string; r2v_polish?: string }) => {
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
        return project;
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
    importFileConfirm: async (data: { title: string; description?: string; text: string; episodes: unknown[] }) => {
        const response = await axios.post(`${API_URL}/series/import/confirm`, data);
        return response.data;
    },

    // ============================================
    // Atelier Canvas Core APIs
    // ============================================
    createAtelierProject: async (
        title: string,
        description: string = "",
        sourceProjectId?: string
    ): Promise<AtelierProject> => {
        const response = await axios.post(`${API_URL}/atelier/projects`, {
            title,
            description,
            source_project_id: sourceProjectId,
        });
        return response.data;
    },
    listAtelierProjects: async (): Promise<AtelierProject[]> => {
        const response = await axios.get(`${API_URL}/atelier/projects`);
        return response.data;
    },
    getAtelierProject: async (projectId: string): Promise<AtelierProject> => {
        const response = await axios.get(`${API_URL}/atelier/projects/${projectId}`);
        return response.data;
    },
    updateAtelierProject: async (
        projectId: string,
        data: { title?: string; description?: string; source_project_id?: string | null }
    ): Promise<AtelierProject> => {
        const response = await axios.put(`${API_URL}/atelier/projects/${projectId}`, data);
        return response.data;
    },
    deleteAtelierProject: async (projectId: string) => {
        const response = await axios.delete(`${API_URL}/atelier/projects/${projectId}`);
        return response.data;
    },
    updateAtelierAgentPolicy: async (
        projectId: string,
        policy: Partial<Pick<AtelierAgentPolicy, "approval_mode" | "allowed_tools" | "max_nodes_per_action">>
    ): Promise<AtelierProject> => {
        const response = await axios.put(`${API_URL}/atelier/projects/${projectId}/agent_policy`, policy);
        return response.data;
    },
    listAtelierAgentTools: async (projectId: string): Promise<AtelierAgentToolSpec[]> => {
        const response = await axios.get(`${API_URL}/atelier/projects/${projectId}/agent/tools`);
        return response.data;
    },
    buildAtelierAgentPlannerPackage: async (
        projectId: string,
        payload: BuildAtelierAgentPlannerPackagePayload
    ): Promise<AtelierAgentPlannerPackage> => {
        const response = await axios.post(`${API_URL}/atelier/projects/${projectId}/agent/planner_package`, payload);
        return response.data;
    },
    planAtelierAgentTurn: async (
        projectId: string,
        payload: PlanAtelierAgentTurnPayload
    ): Promise<AtelierAgentPlan> => {
        const response = await axios.post(`${API_URL}/atelier/projects/${projectId}/agent/plan`, payload);
        return response.data;
    },
    runAtelierAgentTurn: async (
        projectId: string,
        payload: RunAtelierAgentTurnPayload
    ): Promise<AtelierAgentTurn> => {
        const response = await axios.post(`${API_URL}/atelier/projects/${projectId}/agent/turns`, payload);
        return response.data;
    },
    exportAtelierSequence: async (
        projectId: string,
        entries: Array<{ parentId: string; candidateId: string; trimStart?: number; trimEnd?: number }>,
    ): Promise<{ video_url: string; filename: string; size_mb: number; clip_count: number }> => {
        const response = await axios.post(
            `${API_URL}/atelier/projects/${projectId}/sequence/export`,
            { entries },
        );
        return response.data;
    },
    replaceAtelierSequence: async (
        projectId: string,
        entries: Array<{ parentId: string; candidateId: string; trimStart?: number; trimEnd?: number }>,
    ): Promise<AtelierProject> => {
        const response = await axios.put(
            `${API_URL}/atelier/projects/${projectId}/sequence`,
            { entries },
        );
        return response.data;
    },
    createAtelierNode: async (
        projectId: string,
        payload: AtelierNodePayload
    ): Promise<AtelierNode> => {
        const response = await axios.post(`${API_URL}/atelier/projects/${projectId}/nodes`, payload);
        return response.data;
    },
    updateAtelierNode: async (
        projectId: string,
        nodeId: string,
        payload: AtelierNodePayload
    ): Promise<AtelierNode> => {
        const response = await axios.put(`${API_URL}/atelier/projects/${projectId}/nodes/${nodeId}`, payload);
        return response.data;
    },
    deleteAtelierNode: async (projectId: string, nodeId: string) => {
        const response = await axios.delete(`${API_URL}/atelier/projects/${projectId}/nodes/${nodeId}`);
        return response.data;
    },
    createAtelierVideoCandidates: async (
        projectId: string,
        nodeId: string,
        payload: AtelierGenerationConfig
    ): Promise<AtelierNode> => {
        const response = await axios.post(
            `${API_URL}/atelier/projects/${projectId}/nodes/${nodeId}/video_candidates`,
            payload
        );
        return response.data;
    },
    regenerateAtelierVideoCandidates: async (
        projectId: string,
        nodeId: string,
        payload?: Partial<AtelierGenerationConfig>
    ): Promise<AtelierNode> => {
        const response = await axios.post(
            `${API_URL}/atelier/projects/${projectId}/nodes/${nodeId}/video_candidates/regenerate`,
            payload ?? {}
        );
        return response.data;
    },
    retryAtelierVideoCandidate: async (
        projectId: string,
        nodeId: string,
        candidateId: string
    ): Promise<AtelierNode> => {
        const response = await axios.post(
            `${API_URL}/atelier/projects/${projectId}/nodes/${nodeId}/video_candidates/${candidateId}/retry`
        );
        return response.data;
    },
    selectAtelierVideoCandidate: async (
        projectId: string,
        nodeId: string,
        candidateId: string
    ): Promise<AtelierNode> => {
        const response = await axios.post(
            `${API_URL}/atelier/projects/${projectId}/nodes/${nodeId}/video_candidates/select`,
            { candidate_id: candidateId }
        );
        return response.data;
    },
    deleteAtelierVideoCandidate: async (
        projectId: string,
        nodeId: string,
        candidateId: string
    ): Promise<AtelierNode> => {
        const response = await axios.delete(
            `${API_URL}/atelier/projects/${projectId}/nodes/${nodeId}/video_candidates/${candidateId}`
        );
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
