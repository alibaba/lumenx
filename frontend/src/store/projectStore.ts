import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from '@/lib/api';
import { zhCN } from '@/lib/i18n';

export interface ImageVariant {
    id: string;
    url: string;
    created_at: number;
    prompt_used?: string;
}

export interface ImageAsset {
    selected_id: string | null;
    variants: ImageVariant[];
}

interface LegacyVisualAsset {
    selected_image_id?: string;
    image_variants?: ImageVariant[];
    video_variants?: Array<{
        id?: string;
        url: string;
        thumbnail?: string;
        created_at?: number;
    }>;
}

export interface VideoTask {
    id: string;
    project_id: string;
    asset_id?: string;
    frame_id?: string;
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
    aspect_ratio?: string;
    watermark?: boolean;
    camera_fixed?: boolean;
    reference_audio_url?: string;
    seedance_reference_mode?: string;
    seedance_workflow?: string;
    seedance_extend_mode?: string;
    seedance_edit_mode?: string;
    generation_mode?: string;  // 'i2v' or 'r2v'
    reference_video_urls?: string[];  // Reference videos for R2V
}

export interface Character {
    id: string;
    name: string;
    aliases?: string[];
    description?: string;
    age?: string;
    gender?: string;
    clothing?: string;
    visual_weight?: number;

    // Legacy fields
    image_url?: string;
    avatar_url?: string;
    full_body_image_url?: string;
    three_view_image_url?: string;
    headshot_image_url?: string;

    // New Asset Containers
    full_body_asset?: ImageAsset;
    three_view_asset?: ImageAsset;
    headshot_asset?: ImageAsset;
    full_body?: LegacyVisualAsset;
    head_shot?: LegacyVisualAsset;

    // Video Assets
    video_assets?: VideoTask[];
    video_prompt?: string;

    voice_id?: string;
    voice_name?: string;
    locked?: boolean;
    status?: string;
    is_consistent?: boolean;
    full_body_updated_at?: number;
    three_view_updated_at?: number;
    headshot_updated_at?: number;
}

export interface Scene {
    id: string;
    name: string;
    description: string;
    visual_weight?: number;
    image_url?: string;
    image_asset?: ImageAsset;
    video_assets?: VideoTask[];
    video_prompt?: string;
    status?: string;
    locked?: boolean;
    time_of_day?: string;
    lighting_mood?: string;
}

export interface Prop {
    id: string;
    name: string;
    description: string;
    image_url?: string;
    image_asset?: ImageAsset;
    video_assets?: VideoTask[];
    video_prompt?: string;
    status?: string;
    locked?: boolean;
}

export interface StoryBeat {
    id: string;
    order: number;
    title: string;
    chapter_order?: number;
    chapter_title?: string;
    summary: string;
    action_summary?: string;
    dialogue_excerpt?: string;
    storyboard_goal?: string;
    scene_id?: string;
    scene_name?: string;
    location_hint?: string;
    time_hint?: string;
    character_ids: string[];
    character_names: string[];
    prop_ids: string[];
    prop_names: string[];
    source_excerpt?: string;
    storyboard_focus?: string;
    quality_flags?: string[];
}

export interface CharacterPresenceEntry {
    character_id: string;
    character_name: string;
    scene_beat_ids: string[];
    scene_titles: string[];
    mention_count: number;
    highlights: string[];
}

export interface CharacterRelationshipEdge {
    pair_id: string;
    source_character_id: string;
    source_character_name: string;
    target_character_id: string;
    target_character_name: string;
    co_scene_count: number;
    shared_scene_beat_ids: string[];
    shared_scene_titles: string[];
    relationship_hint: string;
}

export interface StoryAnalysis {
    summary: string;
    plot_points: string[];
    scene_beats: StoryBeat[];
    character_presence: CharacterPresenceEntry[];
    character_relationships: CharacterRelationshipEdge[];
}

export interface StoryboardFrame {
    id: string;
    scene_id?: string;
    action_description?: string;
    dialogue?: string;
    speaker?: string;
    image_prompt?: string;
    image_prompt_cn?: string;
    image_prompt_en?: string;
    camera_angle?: string;
    camera_movement?: string;
    composition?: string;
    facial_expression?: string;
    atmosphere?: string;
    character_ids?: string[];
    prop_ids?: string[];
    story_beat_id?: string;
    story_beat_title?: string;
    story_beat_order?: number;
    chapter_order?: number;
    chapter_title?: string;
    image_url?: string;
    image_asset?: ImageAsset;
    rendered_image_url?: string;
    rendered_image_asset?: ImageAsset;
    audio_url?: string;
    sfx_url?: string;
    scene_description?: string;
    status?: string;
    locked?: boolean;
    selected_video_id?: string;
    composition_data?: Record<string, unknown> | null;
    updated_at?: number;
    generation_source?: string | null;
    generation_degraded?: boolean;
    generation_reason?: string | null;
}

export interface StylePreset {
    id: string;
    name: string;
    color: string;
    prompt: string;
    negative_prompt?: string;
}

export interface StyleConfig {
    id: string;
    name: string;
    description?: string;
    positive_prompt: string;
    negative_prompt: string;
    thumbnail_url?: string;
    reference_images?: string[];
    moodboard_notes?: string;
    is_custom: boolean;
    reason?: string; // For AI recommendations
    generation_source?: string | null;
    generation_degraded?: boolean;
    generation_reason?: string | null;
}

export interface ArtDirection {
    selected_style_id: string;
    style_config: StyleConfig;
    custom_styles: StyleConfig[];
    ai_recommendations: StyleConfig[];
}

export interface ModelSettings {
    t2i_model: string;  // Text-to-Image model for Assets
    i2i_model: string;  // Image-to-Image model for Storyboard
    i2v_model: string;  // Image-to-Video model for Motion
    character_aspect_ratio: string;  // Aspect ratio for Character generation
    scene_aspect_ratio: string;  // Aspect ratio for Scene generation
    prop_aspect_ratio: string;  // Aspect ratio for Prop generation
    storyboard_aspect_ratio: string;  // Aspect ratio for Storyboard generation
}

// Model options for dropdowns
const modelCopy = zhCN.modelCopy;
const storeCopy = zhCN.projectStore;

export const DEFAULT_T2I_MODEL = "openai-image";
export const DEFAULT_I2I_MODEL = "openai-image-edit";

export const T2I_MODELS = [
    { id: DEFAULT_T2I_MODEL, name: 'OpenAI-Compatible T2I', description: '使用中转 / OpenAI-compatible 图像接口，实际模型在设置页中配置' },
    { id: 'wan2.6-t2i', name: 'Wan 2.6 T2I', description: modelCopy.latestT2i },
    { id: 'wan2.5-t2i-preview', name: 'Wan 2.5 T2I Preview', description: modelCopy.defaultT2i },
    { id: 'wan2.2-t2i-plus', name: 'Wan 2.2 T2I Plus', description: modelCopy.higherQuality },
    { id: 'wan2.2-t2i-flash', name: 'Wan 2.2 T2I Flash', description: modelCopy.fasterGeneration },
];

export const I2I_MODELS = [
    { id: DEFAULT_I2I_MODEL, name: 'OpenAI-Compatible I2I', description: '使用中转 / OpenAI-compatible 图像编辑接口，默认沿用图像主模型' },
    { id: 'wan2.6-image', name: 'Wan 2.6 Image', description: modelCopy.latestI2i },
    { id: 'wan2.5-i2i-preview', name: 'Wan 2.5 I2I Preview', description: modelCopy.defaultI2i },
];

export const DEFAULT_I2V_MODEL = 'doubao-seedance-2-0-260128';
export const isSeedanceI2VModel = (modelId?: string) => (modelId || '').startsWith('doubao-seedance-');

export type DurationConfig =
    | { type: 'slider'; min: number; max: number; step: number; default: number }
    | { type: 'buttons'; options: number[]; default: number }
    | { type: 'fixed'; value: number };

export interface ModelParamSupport {
    resolution?: { options: string[]; default: string };
    seed?: boolean;
    negativePrompt?: boolean;
    promptExtend?: boolean;
    shotType?: boolean;
    audio?: boolean;
    aspectRatio?: { options: string[]; default: string };
    watermark?: boolean;
    cameraFixed?: boolean;
    referenceAudio?: boolean;
    seedanceReferenceMode?: { options: string[]; default: string };
    seedanceWorkflow?: { options: string[]; default: string };
    // Kling
    mode?: { options: string[]; default: string };
    sound?: boolean;
    cfgScale?: { min: number; max: number; step: number; default: number };
    // Vidu
    viduAudio?: boolean;
    movementAmplitude?: { options: string[]; default: string };
}

export interface I2VModelConfig {
    id: string;
    name: string;
    description: string;
    duration: DurationConfig;
    params: ModelParamSupport;
}

const WAN26_PARAMS: ModelParamSupport = {
    resolution: { options: ['480p', '720p', '1080p'], default: '720p' },
    seed: true, negativePrompt: true, promptExtend: true, shotType: true, audio: true,
};

const SEEDANCE_PARAMS: ModelParamSupport = {
    resolution: { options: ['480p', '720p'], default: '720p' },
    seed: true,
    audio: true,
    aspectRatio: { options: ['adaptive', '16:9', '9:16', '1:1'], default: 'adaptive' },
    watermark: true,
    cameraFixed: true,
    referenceAudio: true,
    seedanceReferenceMode: { options: ['image', 'video', 'combo'], default: 'image' },
    seedanceWorkflow: { options: ['standard', 'extend', 'edit'], default: 'standard' },
};

const WAN25_PARAMS: ModelParamSupport = {
    resolution: { options: ['480p', '720p', '1080p'], default: '720p' },
    seed: true, negativePrompt: true, audio: true,
};

const WAN22_PARAMS: ModelParamSupport = {
    resolution: { options: ['480p', '720p', '1080p'], default: '720p' },
    seed: true, negativePrompt: true,
};

const KLING_PARAMS: ModelParamSupport = {
    negativePrompt: true,
    mode: { options: ['std', 'pro'], default: 'std' },
    sound: true,
    cfgScale: { min: 0, max: 1, step: 0.1, default: 0.5 },
};

const VIDU_PARAMS: ModelParamSupport = {
    resolution: { options: ['540p', '720p', '1080p'], default: '720p' },
    seed: true, viduAudio: true,
    movementAmplitude: { options: ['auto', 'small', 'medium', 'large'], default: 'auto' },
};

export const I2V_MODELS: I2VModelConfig[] = [
    { id: DEFAULT_I2V_MODEL, name: 'Seedance 2.0', description: modelCopy.preferredAudioModel,
      duration: { type: 'buttons', options: [5, 10], default: 5 }, params: SEEDANCE_PARAMS },
    { id: 'wan2.6-i2v', name: 'Wan 2.6 I2V / R2V', description: modelCopy.latestR2v,
      duration: { type: 'slider', min: 2, max: 15, step: 1, default: 5 }, params: WAN26_PARAMS },
    { id: 'wan2.6-i2v-flash', name: 'Wan 2.6 I2V Flash', description: modelCopy.fasterGeneration,
      duration: { type: 'slider', min: 2, max: 15, step: 1, default: 5 }, params: WAN26_PARAMS },
    { id: 'wan2.5-i2v-preview', name: 'Wan 2.5 I2V Preview', description: modelCopy.defaultI2v,
      duration: { type: 'buttons', options: [5, 10], default: 5 }, params: WAN25_PARAMS },
    { id: 'wan2.2-i2v-plus', name: 'Wan 2.2 I2V Plus', description: modelCopy.higherQuality,
      duration: { type: 'fixed', value: 5 }, params: WAN22_PARAMS },
    { id: 'wan2.2-i2v-flash', name: 'Wan 2.2 I2V Flash', description: modelCopy.fasterGeneration,
      duration: { type: 'fixed', value: 5 }, params: WAN22_PARAMS },
    { id: 'kling-v3', name: 'Kling v3', description: modelCopy.klingLatest,
      duration: { type: 'slider', min: 3, max: 15, step: 1, default: 5 }, params: KLING_PARAMS },
    { id: 'viduq3-pro', name: 'Vidu Q3 Pro', description: modelCopy.viduLatest,
      duration: { type: 'slider', min: 1, max: 16, step: 1, default: 5 }, params: VIDU_PARAMS },
    { id: 'viduq3-turbo', name: 'Vidu Q3 Turbo', description: modelCopy.viduFast,
      duration: { type: 'slider', min: 1, max: 16, step: 1, default: 5 }, params: VIDU_PARAMS },
];

export const ASPECT_RATIOS = [
    { id: '9:16', name: '9:16', description: modelCopy.portrait },
    { id: '16:9', name: '16:9', description: modelCopy.landscape },
    { id: '1:1', name: '1:1', description: modelCopy.square },
];

export interface VideoParams {
    resolution: string;
    duration: number;
    seed: number | undefined;
    generateAudio: boolean;
    audioUrl: string;
    promptExtend: boolean;
    negativePrompt: string;
    batchSize: number;
    cameraMovement: string;
    subjectMotion: string;
    model: string;
    shotType: string;
    generationMode: string;
    referenceVideoUrls: string[];
    aspectRatio: string;
    watermark: boolean;
    cameraFixed: boolean;
    referenceAudioUrl: string;
    seedanceReferenceMode: string;
    seedanceWorkflow: string;
    seedanceExtendMode: string;
    seedanceEditMode: string;
    seedancePreviewOnly: boolean;
    // Kling
    mode: string;
    sound: boolean;
    cfgScale: number;
    // Vidu
    viduAudio: boolean;
    movementAmplitude: string;
}

/** 将动态列数映射为完整的 Tailwind class（避免 JIT 扫描不到动态拼接） */
export const GRID_COLS_CLASS: Record<number, string> = {
    2: 'grid-cols-2',
    3: 'grid-cols-3',
    4: 'grid-cols-4',
    5: 'grid-cols-5',
};

export interface PromptConfig {
    storyboard_polish: string;
    video_polish: string;
    r2v_polish: string;
}

export type CodexImagegenMode = "safe_refs_only" | "two_stage_high_consistency";

export interface CodexImagegenPolicy {
    enabled: boolean;
    mode: CodexImagegenMode | string;
    max_total_bytes: number;
    max_side: number;
    min_side: number;
    jpeg_quality: number;
    min_jpeg_quality: number;
    never_attach_raw_references: boolean;
    recommendation?: CodexImagegenRecommendationPolicy;
}

export interface CodexImagegenRecommendationPolicy {
    enabled: boolean;
    auto_apply: boolean;
    safe_direct_max_ready_refs: number;
    two_stage_min_ready_refs: number;
    two_stage_min_identity_refs: number;
    two_stage_min_character_refs: number;
    two_stage_min_prop_refs: number;
    two_stage_min_scene_refs: number;
    direct_when_required_refs_missing: boolean;
    shot_type_overrides: Record<string, Record<string, number>>;
    genre_overrides: Record<string, Record<string, number>>;
}

export interface Series {
    id: string;
    title: string;
    description: string;
    characters: Character[];
    scenes: Scene[];
    props: Prop[];
    art_direction?: ArtDirection;
    prompt_config?: PromptConfig;
    model_settings?: ModelSettings;
    episode_ids: string[];
    created_at: number;
    updated_at: number;
}

export interface Project {
    id: string;
    title: string;
    originalText: string;
    characters: Character[];
    scenes: Scene[];
    props: Prop[];
    frames: StoryboardFrame[];
    video_tasks?: VideoTask[];
    status: string;
    createdAt: string;
    updatedAt: string;
    aspectRatio?: string;
    style_preset?: string;
    style_prompt?: string;
    art_direction?: ArtDirection;
    model_settings?: ModelSettings;
    prompt_config?: PromptConfig;
    codex_imagegen_policy?: CodexImagegenPolicy;
    merged_video_url?: string;
    series_id?: string;
    episode_number?: number;
    story_analysis?: StoryAnalysis;
    generation_metadata?: Record<string, unknown>;
}

interface ProjectStore {
    projects: Project[];
    currentProject: Project | null;
    isLoading: boolean;
    isAnalyzing: boolean;
    isAnalyzingArtStyle: boolean;



    // Global Selection State
    selectedFrameId: string | null;

    // Actions
    setProjects: (projects: Project[]) => void;  // For syncing from backend
    createProject: (title: string, text: string, skipAnalysis?: boolean) => Promise<void>;
    analyzeProject: (script: string) => Promise<void>;
    analyzeArtStyle: (scriptId: string, text: string) => Promise<void>;
    loadProjects: () => void;
    selectProject: (id: string) => Promise<void>;
    updateProject: (id: string, data: Partial<Project>) => void;
    deleteProject: (id: string) => Promise<void>;
    clearCurrentProject: () => void;



    // Selection Actions
    // Selection Actions
    setSelectedFrameId: (id: string | null) => void;

    // Asset Generation State
    generatingTasks: { assetId: string; generationType: string; batchSize: number }[];
    addGeneratingTask: (assetId: string, generationType: string, batchSize: number) => void;
    removeGeneratingTask: (assetId: string, generationType: string) => void;

    // Storyboard Frame Rendering State
    renderingFrames: Set<string>;  // Set of frame IDs currently being rendered
    addRenderingFrame: (frameId: string) => void;
    removeRenderingFrame: (frameId: string) => void;

    // Storyboard Analysis State (persists across tab switches)
    isAnalyzingStoryboard: boolean;
    setIsAnalyzingStoryboard: (value: boolean) => void;

    // Series State
    seriesList: Series[];
    currentSeries: Series | null;
    fetchSeriesList: () => Promise<void>;
    fetchSeries: (id: string) => Promise<void>;
    createSeries: (title: string, description?: string) => Promise<Series>;
    deleteSeries: (id: string) => Promise<void>;
    setCurrentSeries: (series: Series | null) => void;
}

export const useProjectStore = create<ProjectStore>()(
    persist(
        (set, get) => ({
            projects: [],
            currentProject: null,
            isLoading: false,
            isAnalyzing: false,
            selectedFrameId: null,

            // Sync projects from backend
            setProjects: (projects: Project[]) => set({ projects }),

            createProject: async (title: string, text: string, skipAnalysis: boolean = false) => {
                set({ isLoading: true });
                try {
                    const project = await api.createProject(title, text, skipAnalysis);
                    set((state) => ({
                        projects: [...state.projects, project],
                        currentProject: project,
                        isLoading: false,
                    }));
                } catch (error) {
                    console.error(storeCopy.console.failedToCreateProject, error);
                    set({ isLoading: false });
                    throw error;
                }
            },

            analyzeProject: async (script: string) => {
                const { currentProject, createProject } = get();
                set({ isAnalyzing: true });

                try {
                    let project: Project;
                    if (currentProject && currentProject.id) {
                        project = await api.reparseProject(currentProject.id, script);
                        // Update the store with the new/updated project
                        set((state) => ({
                            projects: state.projects.map((p) =>
                                p.id === project.id ? { ...project, updatedAt: new Date().toISOString() } : p
                            ),
                            currentProject: { ...project, updatedAt: new Date().toISOString() }
                        }));
                    } else {
                        // If no current project, create one (assuming title is available or default)
                        // This case might be rare if we always create project first, but handling it just in case
                        await createProject(currentProject?.title || storeCopy.newProjectTitle, script);
                    }
                } catch (error) {
                    console.error(storeCopy.console.failedToAnalyzeScript, error);
                    throw error;
                } finally {
                    set({ isAnalyzing: false });
                }
            },

            loadProjects: () => {
                // Projects are already loaded from localStorage via persist middleware
                // This is mainly for future API sync if needed
            },

            selectProject: async (id: string) => {
                // First, try to set from local cache for immediate feedback
                const cachedProject = get().projects.find((p) => p.id === id);
                if (cachedProject) {
                    set({ currentProject: cachedProject });
                } else {
                    set({ currentProject: null });
                }

                // Then fetch latest data from backend
                try {
                    const latestProject = await api.getProject(id);

                    // Update both currentProject and projects array with latest data.
                    // If this episode/project was not yet cached locally (for example created via series import),
                    // append it so subsequent route changes can resolve immediately from store.
                    set((state) => {
                        const alreadyExists = state.projects.some((p) => p.id === id);
                        return {
                            currentProject: latestProject,
                            projects: alreadyExists
                                ? state.projects.map((p) => (p.id === id ? latestProject : p))
                                : [...state.projects, latestProject],
                        };
                    });
                } catch (error) {
                    console.error(storeCopy.console.failedToFetchLatestProject, error);
                    // Keep using cached version if fetch fails; otherwise leave currentProject null.
                }
            },

            updateProject: (id: string, data: Partial<Project>) => {
                set((state) => ({
                    projects: state.projects.map((p) =>
                        p.id === id ? { ...p, ...data, updatedAt: new Date().toISOString() } : p
                    ),
                    currentProject:
                        state.currentProject?.id === id
                            ? { ...state.currentProject, ...data, updatedAt: new Date().toISOString() }
                            : state.currentProject,
                }));
            },

            deleteProject: async (id: string) => {
                try {
                    // Delete from backend first
                    await api.deleteProject(id);
                    // Then remove from local state
                    set((state) => ({
                        projects: state.projects.filter((p) => p.id !== id),
                        currentProject: state.currentProject?.id === id ? null : state.currentProject
                    }));
                } catch (error) {
                    console.error(storeCopy.console.failedToDeleteProject, error);
                    throw error;
                }
            },

            isAnalyzingArtStyle: false,

            analyzeArtStyle: async (scriptId: string, text: string) => {
                set({ isAnalyzingArtStyle: true });
                try {
                    const data = await api.analyzeScriptForStyles(scriptId, text);

                    // Update the project with new recommendations
                    // We need to fetch the latest project state to ensure we don't overwrite other changes
                    // But for now, let's assume we just want to update the recommendations

                    // Actually, analyzeScriptForStyles just returns recommendations, it doesn't save them to the project yet
                    // The user needs to select one.
                    // BUT, to persist them, we should probably save them to the project immediately if possible?
                    // Or just return them?
                    // The issue is: if we navigate away, we lose the return value.
                    // So we MUST save them to the project or store them in the store.

                    // Let's store them in the current project in the store
                    const current = get().currentProject;
                    if (current) {
                        const updatedArtDirection = {
                            ...current.art_direction,
                            ai_recommendations: data.recommendations
                        } as ArtDirection;

                        // Update local state
                        set((state) => ({
                            currentProject: state.currentProject ? {
                                ...state.currentProject,
                                art_direction: updatedArtDirection
                            } : null
                        }));

                        // Also try to save to backend if we have an active art direction
                        // If not, we just keep it in memory until user saves
                    }

                } catch (error) {
                    console.error(storeCopy.console.failedToAnalyzeArtStyle, error);
                    // We could add an error state here if needed
                } finally {
                    set({ isAnalyzingArtStyle: false });
                }
            },

            clearCurrentProject: () => {
                set({ currentProject: null });
            },



            setSelectedFrameId: (id) => set({ selectedFrameId: id }),

            // Asset Generation State
            generatingTasks: [],
            addGeneratingTask: (assetId: string, generationType: string, batchSize: number) => set((state) => ({
                generatingTasks: [...state.generatingTasks, { assetId, generationType, batchSize }]
            })),
            removeGeneratingTask: (assetId: string, generationType: string) => set((state) => ({
                generatingTasks: state.generatingTasks.filter((t) => !(t.assetId === assetId && t.generationType === generationType))
            })),

            // Storyboard Frame Rendering State
            renderingFrames: new Set<string>(),
            addRenderingFrame: (frameId: string) => set((state) => {
                const newSet = new Set(state.renderingFrames);
                newSet.add(frameId);
                return { renderingFrames: newSet };
            }),
            removeRenderingFrame: (frameId: string) => set((state) => {
                const newSet = new Set(state.renderingFrames);
                newSet.delete(frameId);
                return { renderingFrames: newSet };
            }),

            // Storyboard Analysis State
            isAnalyzingStoryboard: false,
            setIsAnalyzingStoryboard: (value: boolean) => set({ isAnalyzingStoryboard: value }),

            // Series State
            seriesList: [],
            currentSeries: null,

            fetchSeriesList: async () => {
                try {
                    const seriesList = await api.listSeries();
                    set({ seriesList });
                } catch (error) {
                    console.error(storeCopy.console.failedToFetchSeriesList, error);
                }
            },

            fetchSeries: async (id: string) => {
                try {
                    const series = await api.getSeries(id);
                    set({ currentSeries: series });
                } catch (error) {
                    console.error(storeCopy.console.failedToFetchSeries, error);
                }
            },

            createSeries: async (title: string, description?: string) => {
                try {
                    const series = await api.createSeries(title, description);
                    set((state) => ({
                        seriesList: [...state.seriesList, series],
                    }));
                    return series;
                } catch (error) {
                    console.error(storeCopy.console.failedToCreateSeries, error);
                    throw error;
                }
            },

            deleteSeries: async (id: string) => {
                try {
                    await api.deleteSeries(id);
                    set((state) => ({
                        seriesList: state.seriesList.filter((s) => s.id !== id),
                        currentSeries: state.currentSeries?.id === id ? null : state.currentSeries,
                    }));
                } catch (error) {
                    console.error(storeCopy.console.failedToDeleteSeries, error);
                    throw error;
                }
            },

            setCurrentSeries: (series: Series | null) => set({ currentSeries: series }),
        }),
        {
            name: 'project-storage',
            partialize: (state) => ({
                projects: state.projects,

                generatingTasks: state.generatingTasks // Now persisting this to maintain state across refreshes
            }),
        }
    )
);
