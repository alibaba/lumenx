import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api, API_URL } from '@/lib/api';
import { appAccessFetch } from '@/lib/appAccess';
import { environmentLocalStorageKey, getRuntimeEnvironment, isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { acceptsProjectRevision, frameCommandSnapshot, projectReadSnapshot, observeReadVersions, observeProjectRevision,
    markSharedReadStale, canApplyRead, readDominates, sameReadVersion, isReadConfirmed,
    type ReadVersions, type ReadWatermarks, type SnapshotApplyResult, type FrameCommandApplyResult } from '@/lib/frameCommandSnapshot';
import type { FrontendModelSettings } from '@/lib/modelCatalog';
import type { DubSourceProof, MediaRefsLike } from '@/lib/dubSource';
import { withoutMergeFreshness, type MergeVideoFreshness } from '@/lib/mergeFreshness';
export {
    I2I_MODELS,
    I2V_MODELS,
    IMAGE_MODELS,
    T2I_MODELS,
} from '@/lib/modelCatalog';
export type {
    DurationConfig,
    I2VModelConfig,
    ModelParamSupport,
    ModelOption,
} from '@/lib/modelCatalog';

export interface ImageVariant {
    id: string;
    url: string;
    created_at: number;
    prompt_used?: string;
    is_uploaded_source?: boolean;
}

export interface ImageAsset {
    selected_id: string | null;
    variants: ImageVariant[];
}

/** Character reference sheet container (new schema). Mirrors backend
 *  AssetUnit: variants live under `image_variants` / selection under
 *  `selected_image_id` (note: different field names from ImageAsset). */
export interface AssetUnit {
    selected_image_id: string | null;
    image_variants: ImageVariant[];
    selected_video_id?: string | null;
    video_variants?: { id: string; url: string; source_image_id?: string | null; prompt_used?: string; media_refs?: MediaRefsLike | null }[];
    video_prompt?: string;
}

export interface VideoTask {
    id: string;
    project_id: string;
    asset_id?: string;
    frame_id?: string;
    image_url: string;
    prompt: string;
    status: string;
    video_url?: string;
    media_refs?: MediaRefsLike | null;
    duration?: number;
    created_at: number;
    model?: string;
    generation_mode?: string;  // 'i2v' or 'r2v'
    reference_video_urls?: string[];  // Reference videos for R2V
}

export interface Character {
    id: string;
    name: string;
    description?: string;
    base_character_id?: string | null;
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
    // Reference sheets and full-body artwork retain independent selections.
    // The full-body channel keeps its persisted legacy container name.
    reference_sheet?: AssetUnit;
    full_body?: AssetUnit;
    full_body_request_id?: string | null;
    full_body_motion_request_id?: string | null;
    head_shot_motion_request_id?: string | null;
    full_body_asset?: ImageAsset;
    three_view_asset?: ImageAsset;
    headshot_asset?: ImageAsset;
    head_shot?: AssetUnit;
    headshot_request_id?: string | null;

    // Video Assets
    video_assets?: VideoTask[];
    video_prompt?: string;

    voice_id?: string;
    voice_name?: string;
    voice_speed?: number;
    voice_pitch?: number;
    voice_volume?: number;
    locked?: boolean;
    starred?: boolean;
    status?: string;
    is_consistent?: boolean;
    full_body_updated_at?: number;
    three_view_updated_at?: number;
    headshot_updated_at?: number;
    /** Backend-derived: where this asset actually lives.
     *  "episode" — in the script's own characters[] (episode-local
     *  override or standalone-project local)
     *  "series" — in the parent Series.characters[] (shared across
     *  all episodes in the series)
     *  Drives UI badges + the "high-cost action" confirm modal
     *  (A2 design decision). Not persisted; set fresh on every
     *  GET /projects/{id} response. */
    source?: "episode" | "series" | "global";
}

export interface Scene {
    id: string;
    name: string;
    description: string;
    image_url?: string;
    image_asset?: ImageAsset;
    motion_request_id?: string | null;
    video_assets?: VideoTask[];
    video_prompt?: string;
    status?: string;
    locked?: boolean;
    starred?: boolean;
    time_of_day?: string;
    lighting_mood?: string;
    source?: "episode" | "series" | "global";
}

export interface Prop {
    id: string;
    name: string;
    description: string;
    image_url?: string;
    image_asset?: ImageAsset;
    motion_request_id?: string | null;
    video_assets?: VideoTask[];
    video_prompt?: string;
    status?: string;
    locked?: boolean;
    starred?: boolean;
    source?: "episode" | "series" | "global";
}

export interface StoryboardFrame {
    id: string;
    scene_id: string;
    image_url?: string;
    image_asset?: ImageAsset;
    rendered_image_url?: string;
    rendered_image_asset?: ImageAsset;
    last_frame_url?: string | null;
    status?: string;
    locked?: boolean;
    audio_url?: string | null;
    media_refs?: MediaRefsLike | null;
    preview_video_url?: string | null;
    dubbed_video_url?: string | null;
    selected_video_id?: string | null;
    dubbed_video_task_id?: string | null;
    dub_offset_ms?: number | null;
    dub_preview_source?: DubSourceProof | null;
    dub_applied_source?: DubSourceProof | null;
    // ... other fields
}

export interface StylePresetCategory {
    id: string;
    name: string;
    name_zh: string;
    sort_order: number;
}

export interface StylePreset {
    id: string;
    category: string;
    name: string;
    name_zh: string;
    subtitle_zh?: string;
    description?: string;
    best_for?: string[];
    avoid_for?: string[];
    positive_prompt: string;
    negative_prompt: string;
    sample_prompt?: string;
    thumbnail: string | null;
    object_position?: string;
}

export interface StyleConfig {
    id: string;
    name: string;
    description?: string;
    positive_prompt: string;
    negative_prompt: string;
    thumbnail_url?: string;
    is_custom: boolean;
    reason?: string; // For AI recommendations
}

export interface ArtDirection {
    selected_style_id: string;
    style_config: StyleConfig;
    custom_styles: StyleConfig[];
    ai_recommendations: StyleConfig[];
}

export type ModelSettings = FrontendModelSettings;

export const ASPECT_RATIOS = [
    { id: '9:16', name: '9:16', description: 'Portrait (576×1024)' },
    { id: '3:4', name: '3:4', description: 'Portrait mild (768×1024)' },
    { id: '1:1', name: '1:1', description: 'Square (1024×1024)' },
    { id: '4:3', name: '4:3', description: 'Landscape mild (1024×768)' },
    { id: '16:9', name: '16:9', description: 'Landscape (1024×576)' },
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
    storyboard_extraction?: string;
}

export interface Series {
    id: string;
    revision: number;
    title: string;
    description: string;
    characters: Character[];
    scenes: Scene[];
    props: Prop[];
    art_direction?: ArtDirection;
    prompt_config?: PromptConfig;
    model_settings?: ModelSettings;
    workflow_mode?: "r2v" | "i2v_legacy";
    /** PR-3e — Visual control preference. 'r2v' = 节奏优先 (new shots default
     *  direct_r2v); 'i2v' = 画面优先 (new shots default t2i_i2v). */
    default_generation_mode?: "r2v" | "i2v";
    episode_ids: string[];
    created_at: number;
    updated_at: number;
}

export interface Project {
    id: string;
    revision: number;
    title: string;
    originalText: string;
    characters: Character[];
    scenes: Scene[];
    props: Prop[];
    frames: any[]; // Keeping as any for now to avoid breaking too much, but ideally StoryboardFrame[]
    video_tasks?: any[];
    status: string;
    createdAt: string;
    updatedAt: string;
    aspectRatio?: string;
    style_preset?: string;
    art_direction?: ArtDirection;
    model_settings?: ModelSettings;
    prompt_config?: PromptConfig;
    workflow_mode?: "i2v_legacy" | "r2v";
    /** PR-3e — Inherited from series; used by StoryboardR2V addShot to
     *  pick default tabMode for new shots. */
    default_generation_mode?: "r2v" | "i2v";
    merged_video_url?: string;
    merged_video_freshness?: MergeVideoFreshness;
    /** PR-3k · Assembly Mix phase fields */
    bgm_url?: string | null;
    mix_settings?: Record<string, number>;
    series_id?: string | null;
    sharedAssetsNeedRefresh?: boolean;
    read_versions?: ReadVersions;
    readEnvironment?: string;
    episode_number?: number;
    /** T13 — user-starred (featured) flag; drives the amber-halation card. */
    starred?: boolean;
}

export interface PendingExtractionOwner {
    projectId: string;
    environment: string;
    generation: number;
    revision: number;
    text: string;
    sessionId: string;
}

interface ProjectListSyncCapture {
    projects: Record<string, { project: Project; revision: number }>;
}

interface ProjectEnvironmentSlice {
    projects: Project[];
    currentProject: Project | null;
    readWatermarks: ReadWatermarks;
    generatingTasks: { assetId: string; generationType: string; batchSize: number }[];
}

interface ProjectStore {
    activeEnvironmentKey: string | null;
    environments: Record<string, ProjectEnvironmentSlice>;
    projects: Project[];
    currentProject: Project | null;
    isLoading: boolean;
    isAnalyzing: boolean;

    // Entity extraction confirmation (persists across step switches)
    pendingExtraction: { characters: any[]; scenes: any[]; props: any[] } | null;
    pendingExtractionScript: string | null;
    pendingExtractionOwner: PendingExtractionOwner | null;
    confirmExtraction: () => Promise<Project | null>;
    discardExtraction: () => void;

    // Global Selection State
    selectedFrameId: string | null;

    // Actions
    setProjects: (projects: Project[]) => void;  // For syncing from backend
    captureProjectListSync: () => ProjectListSyncCapture;
    syncProjectList: (projects: unknown[], capture?: ProjectListSyncCapture) => void;
    createProject: (title: string, text: string, skipAnalysis?: boolean, workflowMode?: string, seriesId?: string) => Promise<Project>;
    loadProjects: () => void;
    selectProject: (id: string) => Promise<void>;
    updateProject: (id: string, data: Partial<Project>) => void;
    readWatermarks: ReadWatermarks;
    applyFrameCommandSnapshot: (id: string, data: unknown) => FrameCommandApplyResult;
    applyProjectReadSnapshot: (id: string, data: unknown, select?: boolean) => SnapshotApplyResult;
    applySeriesReadSnapshot: (projectId: string, seriesId: string, data: unknown) => boolean;
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

    // Global async operation tracker — persists across step/tab switches
    runningOps: Record<string, boolean>;
    setRunningOp: (key: string, running: boolean) => void;

    // Series State
    seriesList: Series[];
    currentSeries: Series | null;
    fetchSeriesList: () => Promise<boolean>;
}

// localStorage keys mirrored from SettingsPage. These hold the user's
// global default model settings / prompt config. Kept here so newly
// created projects can be backfilled with those defaults.
const LS_KEY_DEFAULT_MODEL = 'lumenx_default_model_settings';
const LS_KEY_DEFAULT_PROMPT = 'lumenx_default_prompt_config';
let projectSelectionRequest = 0;
let seriesListRequest = 0;
let projectStoreHydrated = false;
let projectEnvironmentActivationRequest = 0;
const projectDeleteRequests = new Map<string, Promise<void>>();
const extractionConfirmRequests = new Map<string, Promise<Project | null>>();
export const activeProjectEnvironmentKey = () => requireActiveRuntimeEnvironment().key;
export const projectReadFailureKey = (id: string, environment = activeProjectEnvironmentKey()) => `project-read-failed:${encodeURIComponent(environment)}:${encodeURIComponent(id)}`;
const projectDeleteOperationKey = (id: string, environment = activeProjectEnvironmentKey()) => `project-delete:${encodeURIComponent(environment)}:${encodeURIComponent(id)}`;
export const extractionPreviewOperationKey = (id: string, environment = activeProjectEnvironmentKey()) => `script-extraction-preview:${encodeURIComponent(environment)}:${encodeURIComponent(id)}`;
const extractionConfirmOperationKey = (id: string, environment = activeProjectEnvironmentKey()) => `script-extraction-confirm:${encodeURIComponent(environment)}:${encodeURIComponent(id)}`;
const frameEditPrefix = (id: string, environment: string) => `frame-edit:${encodeURIComponent(environment)}:${encodeURIComponent(id)}:`;
const frameEditFailurePrefix = (id: string, environment: string) => `frame-edit-failed:${encodeURIComponent(environment)}:${encodeURIComponent(id)}:`;
const frameCommandErrorPrefix = (id: string, environment: string) => `frame-command-error:${encodeURIComponent(environment)}:${encodeURIComponent(id)}:`;
class InvalidDefaultWriteAckError extends Error {}
function currentMatchesExtractionOwner(project: Project | null | undefined, owner: PendingExtractionOwner): boolean {
    return isCurrentRuntimeEnvironment(owner.environment, owner.generation) && !!project && project.id === owner.projectId && project.revision === owner.revision
        && project.readEnvironment === owner.environment;
}
function hasExtractionOperation(runningOps: Record<string, boolean>): boolean {
    return Object.keys(runningOps).some(key => key.startsWith('script-extraction-preview:')
        || key.startsWith('script-extraction-confirm:'));
}
function projectTimestamp(value: unknown): string {
    return typeof value === 'number' && Number.isFinite(value)
        ? new Date(value * 1000).toISOString() : typeof value === 'string' ? value : '';
}
function projectSummary(value: unknown): Project {
    const raw = value as Partial<Project> & { original_text?: unknown; created_at?: unknown; updated_at?: unknown } | null;
    if (!raw || typeof raw.id !== 'string' || !raw.id.trim()
        || typeof raw.title !== 'string' || typeof raw.revision !== 'number'
        || !Number.isSafeInteger(raw.revision) || raw.revision < 0) {
        throw new Error('Invalid project summary');
    }
    const originalText = typeof raw.originalText === 'string' ? raw.originalText
        : typeof raw.original_text === 'string' ? raw.original_text : '';
    return {
        ...raw,
        id: raw.id,
        title: raw.title,
        revision: raw.revision,
        originalText,
        frames: Array.isArray(raw.frames) ? raw.frames : [],
        characters: Array.isArray(raw.characters) ? raw.characters : [],
        scenes: Array.isArray(raw.scenes) ? raw.scenes : [],
        props: Array.isArray(raw.props) ? raw.props : [],
        video_tasks: Array.isArray(raw.video_tasks) ? raw.video_tasks : [],
        status: raw.status ?? 'draft',
        createdAt: raw.createdAt ?? projectTimestamp(raw.created_at),
        updatedAt: raw.updatedAt ?? projectTimestamp(raw.updated_at),
        readEnvironment: undefined,
        read_versions: undefined,
        sharedAssetsNeedRefresh: undefined,
    } as Project;
}
function captureProjectList(projects: Project[]): ProjectListSyncCapture {
    return { projects: Object.fromEntries(projects.map(project => [project.id, { project, revision: project.revision }])) };
}
function changedSinceCapture(project: Project, capture?: ProjectListSyncCapture): boolean {
    const entry = capture?.projects[project.id];
    return !entry || entry.project !== project || entry.revision !== project.revision;
}
function mergeProjectSummary(existing: Project | undefined, summary: Project, capture?: ProjectListSyncCapture): Project {
    if (!existing) return summary;
    if (summary.revision < existing.revision) return existing;
    if (changedSinceCapture(existing, capture) && summary.revision <= existing.revision) return existing;
    const environment = getRuntimeEnvironment()?.key;
    if (environment && existing.readEnvironment === environment && !summary.read_versions && summary.revision <= existing.revision) return existing;
    return summary;
}
function matchesSeriesCapture(project: Project | null, series: Series | null): boolean {
    const captured = project?.read_versions?.series;
    const environment = getRuntimeEnvironment()?.key;
    return !!environment && !!project && project.readEnvironment === environment && !project.sharedAssetsNeedRefresh
        && !!series && series.id === project.series_id && captured?.id === series.id
        && typeof captured.revision === 'number' && captured.revision === series.revision;
}

function environmentSlice(state: ProjectStore): ProjectEnvironmentSlice {
    return {
        projects: state.projects.map(withoutMergeFreshness),
        currentProject: state.currentProject ? withoutMergeFreshness(state.currentProject) : null,
        readWatermarks: state.readWatermarks,
        generatingTasks: state.generatingTasks,
    };
}

function emptyEnvironmentSlice(): ProjectEnvironmentSlice {
    return { projects: [], currentProject: null, readWatermarks: {}, generatingTasks: [] };
}

function persistableEnvironments(state: ProjectStore): Record<string, ProjectEnvironmentSlice> {
    return state.activeEnvironmentKey
        ? { ...state.environments, [state.activeEnvironmentKey]: environmentSlice(state) }
        : state.environments;
}

function readLS<T>(key: string): T | null {
    if (typeof window === 'undefined') return null;
    try {
        const raw = localStorage.getItem(key);
        return raw ? (JSON.parse(raw) as T) : null;
    } catch {
        return null;
    }
}

// Backfill the SettingsPage defaults onto a freshly created project.
// Each successful default write immediately advances the persisted project
// floor so a later default failure cannot allow an older readback to apply.
async function injectDefaultsIntoProject(projectId: string, observeRevision: (revision: number) => void): Promise<void> {
    const environment = requireActiveRuntimeEnvironment();
    const ms = readLS<Partial<FrontendModelSettings>>(environmentLocalStorageKey(LS_KEY_DEFAULT_MODEL, environment.key));
    const pc = readLS<{
        storyboard_polish?: string;
        video_polish?: string;
        r2v_polish?: string;
        entity_extraction?: string;
        style_analysis?: string;
        storyboard_extraction?: string;
    }>(environmentLocalStorageKey(LS_KEY_DEFAULT_PROMPT, environment.key));

    const observeMinimalAck = (value: unknown) => {
        const rawRevision = (value as { revision?: unknown } | null)?.revision;
        if (typeof rawRevision !== 'number' || !Number.isSafeInteger(rawRevision) || rawRevision < 0) {
            throw new InvalidDefaultWriteAckError('Invalid project default write revision');
        }
        observeRevision(rawRevision);
    };
    const observeRawScriptAck = (value: unknown) => {
        try {
            observeRevision(frameCommandSnapshot(projectId, value).revision);
        } catch {
            throw new InvalidDefaultWriteAckError('Invalid project default write script acknowledgement');
        }
    };

    if (ms) {
        if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        observeRawScriptAck(await api.updateModelSettings(
            projectId,
            ms.t2i_model,
            ms.i2i_model,
            ms.i2v_model,
            ms.character_aspect_ratio,
            ms.scene_aspect_ratio,
            ms.prop_aspect_ratio,
            ms.storyboard_aspect_ratio,
            ms.image_model,
            ms.r2v_model,
        ));
    }

    if (pc) {
        const hasAny = Object.values(pc).some((v) => typeof v === 'string' && v.trim());
        if (hasAny) {
            if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
            observeMinimalAck(await api.updatePromptConfig(projectId, pc));
        }
    }
}

export const useProjectStore = create<ProjectStore>()(
    persist(
        (set, get) => ({
            activeEnvironmentKey: null,
            environments: {},
            projects: [],
            currentProject: null,
            readWatermarks: {},
            isLoading: false,
            isAnalyzing: false,
            selectedFrameId: null,

            // Entity extraction confirmation
            pendingExtraction: null,
            pendingExtractionScript: null,
            pendingExtractionOwner: null,
            confirmExtraction: async () => {
                const environment = requireActiveRuntimeEnvironment();
                const { currentProject, pendingExtractionScript, pendingExtractionOwner } = get();
                if (!pendingExtractionScript || !pendingExtractionOwner) return null;
                if (pendingExtractionScript !== pendingExtractionOwner.text) {
                    throw new Error('Stale extraction script text');
                }
                if (!currentMatchesExtractionOwner(currentProject, pendingExtractionOwner)) {
                    throw new Error('Stale extraction project');
                }
                const key = extractionConfirmOperationKey(pendingExtractionOwner.projectId, environment.key);
                const requestKey = `${key}:${environment.generation}`;
                const existing = extractionConfirmRequests.get(requestKey);
                if (existing) return existing;
                if (get().runningOps[key]) return null;
                const confirmation = (async () => {
                    get().setRunningOp(key, true);
                    set({ isAnalyzing: true });
                    try {
                        const project = await api.reparseProject(pendingExtractionOwner.projectId, pendingExtractionOwner.text, pendingExtractionOwner.revision);
                        if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) return null;
                        const snapshot = frameCommandSnapshot(pendingExtractionOwner.projectId, project);
                        get().applyFrameCommandSnapshot(pendingExtractionOwner.projectId, project);
                        set((state) => state.pendingExtractionOwner?.sessionId === pendingExtractionOwner.sessionId
                            ? {
                                pendingExtraction: null,
                                pendingExtractionScript: null,
                                pendingExtractionOwner: null,
                            }
                            : {});
                        return snapshot;
                    } catch (error) {
                        console.error("Failed to apply extraction:", error);
                        throw error;
                    } finally {
                        extractionConfirmRequests.delete(requestKey);
                        if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                            get().setRunningOp(key, false);
                            set({ isAnalyzing: hasExtractionOperation(get().runningOps) });
                        }
                    }
                })();
                extractionConfirmRequests.set(requestKey, confirmation);
                return confirmation;
            },
            discardExtraction: () => {
                set({ pendingExtraction: null, pendingExtractionScript: null, pendingExtractionOwner: null });
            },

            // Sync projects from backend
            setProjects: (projects: Project[]) => set({ projects }),
            captureProjectListSync: () => captureProjectList(get().projects),
            syncProjectList: (incoming: unknown[], capture?: ProjectListSyncCapture) => {
                const environment = requireActiveRuntimeEnvironment();
                const summaries = new Map<string, Project>();
                for (const value of incoming) {
                    const summary = projectSummary(value);
                    summaries.set(summary.id, summary);
                }
                set((state) => {
                    const nextById = new Map<string, Project>();
                    for (const existing of state.projects) {
                        const summary = summaries.get(existing.id);
                        if (summary) {
                            nextById.set(existing.id, mergeProjectSummary(existing, summary, capture));
                            summaries.delete(existing.id);
                        } else if (changedSinceCapture(existing, capture)) {
                            nextById.set(existing.id, existing);
                        }
                    }
                    Array.from(summaries.values()).forEach((summary) => nextById.set(summary.id, summary));
                    for (const project of Array.from(nextById.values())) project.readEnvironment = environment.key;
                    const projects = Array.from(nextById.values());
                    let currentProject = state.currentProject;
                    if (currentProject) {
                        const summary = nextById.get(currentProject.id);
                        if (summary) currentProject = mergeProjectSummary(currentProject, summary, capture);
                        else if (!changedSinceCapture(currentProject, capture)) currentProject = null;
                    }
                    return { projects, currentProject };
                });
            },

            createProject: async (title: string, text: string, skipAnalysis: boolean = false, workflowMode: string = "r2v", seriesId?: string) => {
                const environment = requireActiveRuntimeEnvironment();
                set({ isLoading: true });
                const createRequest = ++projectSelectionRequest;
                let projectId: string | undefined;
                let created: Project | undefined;
                try {
                    const rawProject = await api.createProject(title, text, skipAnalysis, workflowMode, seriesId);
                    if (typeof rawProject.id !== 'string' || !rawProject.id.trim()) throw new Error('Invalid created project id');
                    const createdId: string = rawProject.id;
                    projectId = createdId;
                    if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) throw new Error('Runtime environment changed during project creation');
                    created = frameCommandSnapshot(createdId, rawProject, undefined, environment.key);
                    const selectCreated = projectSelectionRequest === createRequest;
                    set((state) => ({
                        readWatermarks: observeProjectRevision(state.readWatermarks, environment.key, created!.id, created!.revision),
                        projects: [...state.projects.filter((project) => project.id !== created!.id), created!],
                        currentProject: selectCreated ? created! : state.currentProject,
                        currentSeries: selectCreated ? null : state.currentSeries,
                    }));

                    let defaultAckUnconfirmed = false;
                    const observeCreatedRevision = (revision: number) => {
                        set((state) => ({
                            readWatermarks: observeProjectRevision(state.readWatermarks, environment.key, createdId, revision),
                        }));
                    };
                    try {
                        await injectDefaultsIntoProject(createdId, observeCreatedRevision);
                    } catch (backfillError) {
                        // Non-fatal: a failed backfill must not block creation, but a
                        // resolved write without a trustworthy revision cannot validate
                        // a following readback as current.
                        if (backfillError instanceof InvalidDefaultWriteAckError) defaultAckUnconfirmed = true;
                        console.warn('Failed to inject default settings into new project:', backfillError);
                    }
                    try {
                        const enriched = await api.getProject(createdId);
                        if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) throw new Error('Runtime environment changed during project readback');
                        if (defaultAckUnconfirmed) {
                            throw new Error('Created project readback cannot confirm an unversioned default acknowledgement');
                        }
                        const select = projectSelectionRequest === createRequest && get().currentProject?.id === createdId;
                        const result = get().applyProjectReadSnapshot(createdId, enriched, select);
                        get().setRunningOp(projectReadFailureKey(createdId, environment.key), !isReadConfirmed(result));
                    } catch (readError) {
                        if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
                            get().setRunningOp(projectReadFailureKey(createdId, environment.key), true);
                        }
                        console.error('Failed to confirm created project:', readError);
                    }
                    set({ isLoading: false });
                    return get().projects.find((project) => project.id === createdId) ?? created;
                } catch (error) {
                    console.error('Failed to create project:', error);
                    set({ isLoading: false });
                    if (projectId && isCurrentRuntimeEnvironment(environment.key, environment.generation)) get().setRunningOp(projectReadFailureKey(projectId, environment.key), true);
                    throw error;
                }
            },


            loadProjects: () => {
                // Projects are already loaded from localStorage via persist middleware
                // This is mainly for future API sync if needed
            },

            selectProject: async (id: string) => {
                const environment = requireActiveRuntimeEnvironment();
                const request = ++projectSelectionRequest;
                const editsAtStart = Object.keys(get().runningOps).some(key => key.startsWith(frameEditPrefix(id, environment.key)));
                const failuresAtStart = new Set(Object.keys(get().runningOps)
                    .filter(key => key.startsWith(frameEditFailurePrefix(id, environment.key))
                        || key.startsWith(frameCommandErrorPrefix(id, environment.key))));
                // First, try to set from local cache for immediate feedback
                const cachedProject = get().projects.find((p) => p.id === id);
                const current = get().currentProject;
                const pendingPatch = get().pendingExtractionOwner?.projectId === id ? {} : {
                    pendingExtraction: null,
                    pendingExtractionScript: null,
                    pendingExtractionOwner: null,
                };
                set({ currentProject: current?.id === id && !acceptsProjectRevision(current, cachedProject?.revision ?? -1)
                    ? current : cachedProject ?? null, currentSeries: null, ...pendingPatch });

                // Then fetch latest data from backend
                try {
                    const response = await appAccessFetch(`${API_URL}/projects/${id}`);
                    if (response.ok) {
                        const rawData = await response.json();
                        if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
                        const result = get().applyProjectReadSnapshot(id, rawData, request === projectSelectionRequest);
                        get().setRunningOp(projectReadFailureKey(id, environment.key), !isReadConfirmed(result));
                        set((state) => ({
                            // A deliberate reopen confirms server state after an edit
                            // failure. Require a read accepted by both projections,
                            // with no outstanding edit, before releasing the guard.
                            runningOps: editsAtStart
                                || !isReadConfirmed(result)
                                || Object.keys(state.runningOps).some(key => key.startsWith(frameEditPrefix(id, environment.key)))
                                ? state.runningOps : Object.fromEntries(Object.entries(state.runningOps)
                                    .filter(([key]) => !failuresAtStart.has(key))),
                        }));

                        // Auto-load parent series for style inheritance (always fetch fresh for up-to-date art_direction)
                        if (!isReadConfirmed(result) || request !== projectSelectionRequest) return;
                        const seriesId = get().currentProject?.series_id;
                        if (seriesId) {
                            const cached = get().seriesList.find((s) => s.id === seriesId);
                            if (cached && matchesSeriesCapture(get().currentProject, cached)) {
                                set({ currentSeries: cached });
                            }
                            const series = await api.getSeries(seriesId);
                            if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
                            get().applySeriesReadSnapshot(id, seriesId, series);
                        } else {
                            set({ currentSeries: null });
                        }
                    } else throw new Error(`Project read failed: HTTP ${response.status}`);
                } catch (error) {
                    if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) get().setRunningOp(projectReadFailureKey(id, environment.key), true);
                    console.error('Failed to fetch latest project data:', error);
                    // Keep using cached version if fetch fails
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

            applyFrameCommandSnapshot: (id, data) => {
                const environment = requireActiveRuntimeEnvironment();
                // Validate before set so malformed success responses never partially apply.
                const snapshot = frameCommandSnapshot(id, data);
                let current = false; let cached = false;
                set(state => {
                    const readWatermarks = observeProjectRevision(observeReadVersions(state.readWatermarks,
                        [...state.projects, ...(state.currentProject ? [state.currentProject] : [])], environment.key), environment.key, id, snapshot.revision);
                    const accepts = (project: Project) => project.readEnvironment !== environment.key || acceptsProjectRevision(project, snapshot.revision);
                    const apply = (project: Project) => markSharedReadStale(project.id === id && accepts(project)
                        ? frameCommandSnapshot(id, data, project, environment.key) : project, readWatermarks[environment.key], environment.key);
                    current = state.currentProject?.id === id && accepts(state.currentProject);
                    cached = state.projects.some(project => project.id === id && accepts(project));
                    const currentProject = state.currentProject ? apply(state.currentProject) : null;
                    return {
                        readWatermarks,
                        projects: state.projects.map(apply),
                        currentSeries: matchesSeriesCapture(currentProject, state.currentSeries) ? state.currentSeries : null,
                        currentProject,
                    };
                });
                return { scope: 'project', status: current || cached ? 'applied' : 'superseded', current, cached };
            },

            applyProjectReadSnapshot: (id, data, select = false) => {
                const environment = requireActiveRuntimeEnvironment();
                const snapshot = projectReadSnapshot(id, data, environment.key);
                const result: SnapshotApplyResult = { scope: 'read', status: 'rejected', reason: 'incomparable', current: false, cached: false };
                set(state => {
                    const readWatermarks = observeReadVersions(state.readWatermarks,
                        [...state.projects, ...(state.currentProject ? [state.currentProject] : []), snapshot], environment.key);
                    const floor = readWatermarks[environment.key];
                    const outcomes: { snapshot: Project; confirmed: boolean }[] = [];
                    const apply = (previous: Project, projection: 'current' | 'cached') => {
                        const next = markSharedReadStale(previous, floor, environment.key);
                        if (previous.id !== id) return next;
                        const accepted = canApplyRead(previous, snapshot, floor, environment.key);
                        result[projection] ||= accepted;
                        outcomes.push({ snapshot: accepted ? snapshot : next, confirmed: accepted && !!snapshot.read_versions
                            || readDominates(next, snapshot, floor, environment.key) });
                        return accepted ? snapshot : next;
                    };
                    const projects = state.projects.map(project => apply(project, 'cached'));
                    if (select && !projects.some(project => project.id === id) && canApplyRead(undefined, snapshot, floor, environment.key)) {
                        projects.push(snapshot); result.cached = true;
                        outcomes.push({ snapshot, confirmed: !!snapshot.read_versions });
                    }
                    let currentProject = state.currentProject ? apply(state.currentProject, 'current') : null;
                    if (select && state.currentProject?.id !== id) {
                        const target = state.projects.find(project => project.id === id);
                        if (target) currentProject = apply(target, 'current');
                        else if (canApplyRead(undefined, snapshot, floor, environment.key)) {
                            currentProject = snapshot; result.current = true;
                            outcomes.push({ snapshot, confirmed: !!snapshot.read_versions });
                        }
                    }
                    const consistent = outcomes.every(outcome => sameReadVersion(outcome.snapshot, outcomes[0].snapshot));
                    if (outcomes.length && outcomes.every(outcome => outcome.confirmed) && consistent) {
                        result.status = result.current || result.cached ? 'applied' : 'superseded'; delete result.reason;
                    } else if (!snapshot.read_versions && (result.current || result.cached)) {
                        result.status = 'unconfirmed'; result.reason = 'unknown_sources';
                    } else result.reason = !snapshot.read_versions ? 'unknown_sources' : !consistent ? 'projection_mismatch' : 'incomparable';
                    return { projects, currentProject, readWatermarks,
                        currentSeries: matchesSeriesCapture(currentProject, state.currentSeries) ? state.currentSeries : null };
                });
                return result;
            },

            applySeriesReadSnapshot: (projectId, seriesId, data) => {
                const environment = requireActiveRuntimeEnvironment();
                const series = data as Series | null;
                if (!series || series.id !== seriesId || !seriesId.trim()
                    || !Number.isSafeInteger(series.revision) || series.revision < 0) {
                    get().setRunningOp(projectReadFailureKey(projectId, environment.key), true);
                    return false;
                }
                let accepted = false;
                set(state => {
                    const readWatermarks = observeReadVersions(state.readWatermarks,
                        [...state.projects, ...(state.currentProject ? [state.currentProject] : [])], environment.key);
                    const floor = readWatermarks[environment.key];
                    floor.series[seriesId] = Math.max(floor.series[seriesId] ?? 0, series.revision);
                    const currentProject = state.currentProject ? markSharedReadStale(state.currentProject, floor, environment.key) : null;
                    accepted = currentProject?.id === projectId && matchesSeriesCapture(currentProject, series);
                    return {
                        readWatermarks, currentProject,
                        projects: state.projects.map(project => markSharedReadStale(project, floor, environment.key)),
                        currentSeries: accepted ? series : matchesSeriesCapture(currentProject, state.currentSeries) ? state.currentSeries : null,
                        runningOps: !accepted && currentProject?.id === projectId
                            ? { ...state.runningOps, [projectReadFailureKey(projectId, environment.key)]: true } : state.runningOps,
                    };
                });
                return accepted;
            },

            deleteProject: async (id: string) => {
                const environment = requireActiveRuntimeEnvironment();
                const key = projectDeleteOperationKey(id, environment.key);
                const existing = projectDeleteRequests.get(key);
                if (existing) return existing;

                const deletion = (async () => {
                    get().setRunningOp(key, true);
                    try {
                        await api.deleteProject(id);
                        if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
                        set((state) => ({
                            projects: state.projects.filter((p) => p.id !== id),
                            currentProject: state.currentProject?.id === id ? null : state.currentProject,
                            currentSeries: state.currentProject?.id === id ? null : state.currentSeries,
                        }));
                    } catch (error) {
                        console.error('Failed to delete project from backend:', error);
                        throw error;
                    } finally {
                        projectDeleteRequests.delete(key);
                        get().setRunningOp(key, false);
                    }
                })();
                projectDeleteRequests.set(key, deletion);
                return deletion;
            },

            clearCurrentProject: () => {
                ++projectSelectionRequest;
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

            // Global async operation tracker
            runningOps: {},
            setRunningOp: (key: string, running: boolean) => set((state) => {
                const next = { ...state.runningOps };
                if (running) next[key] = true;
                else delete next[key];
                return { runningOps: next };
            }),

            // Series State
            seriesList: [],
            currentSeries: null,

            fetchSeriesList: async () => {
                const environment = requireActiveRuntimeEnvironment();
                const request = ++seriesListRequest;
                const captured = new Map(get().seriesList.map(series => [series.id, series]));
                const capturedFloor = { ...get().readWatermarks[environment.key]?.series };
                try {
                    const incoming = await api.listSeries();
                    if (request !== seriesListRequest || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return true;
                    if (!Array.isArray(incoming) || !incoming.every(series => series
                        && typeof series.id === 'string' && series.id.trim()
                        && typeof series.title === 'string'
                        && Number.isSafeInteger(series.revision) && series.revision >= 0)
                        || new Set(incoming.map(series => series.id)).size !== incoming.length) {
                        throw new Error('Invalid series list response');
                    }
                    const sources = observeReadVersions(get().readWatermarks,
                        [...get().projects, ...(get().currentProject ? [get().currentProject!] : [])], environment.key);
                    if (incoming.some(series => series.revision < (sources[environment.key].series[series.id] ?? 0))) {
                        throw new Error('Series list predates a confirmed source revision');
                    }
                    set(state => {
                        const readWatermarks = observeReadVersions(state.readWatermarks,
                            [...state.projects, ...(state.currentProject ? [state.currentProject] : [])], environment.key);
                        const floor = readWatermarks[environment.key];
                        for (const series of incoming) floor.series[series.id] = Math.max(floor.series[series.id] ?? 0, series.revision);
                        const currentProject = state.currentProject ? markSharedReadStale(state.currentProject, floor, environment.key) : null;
                        const known = new Map(state.seriesList.map(series => [series.id, series]));
                        const seriesList = incoming.map(series => {
                            const previous = known.get(series.id);
                            known.delete(series.id);
                            return previous && previous.revision > series.revision ? previous : series;
                        });
                        for (const previous of Array.from(known.values())) {
                            const observedDuringRead = (floor.series[previous.id] ?? 0) > Math.max(
                                capturedFloor[previous.id] ?? 0, captured.get(previous.id)?.revision ?? 0);
                            if (captured.get(previous.id) !== previous || observedDuringRead) seriesList.push(previous);
                        }
                        return { seriesList, readWatermarks, currentProject,
                            projects: state.projects.map(project => markSharedReadStale(project, floor, environment.key)),
                            currentSeries: matchesSeriesCapture(currentProject, state.currentSeries) ? state.currentSeries : null };
                    });
                    return true;
                } catch (error) {
                    if (request !== seriesListRequest) return true;
                    console.error('Failed to fetch series list:', error);
                    return false;
                }
            },
        }),
        {
            name: 'project-storage',
            partialize: (state) => ({
                activeEnvironmentKey: state.activeEnvironmentKey,
                environments: persistableEnvironments(state),
            }),
            skipHydration: true,
        }
    )
);

export async function activateProjectEnvironment(environmentKey: string, generation = getRuntimeEnvironment()?.generation): Promise<void> {
    const request = ++projectEnvironmentActivationRequest;
    const alreadyHydrated = projectStoreHydrated;
    if (!projectStoreHydrated) {
        await useProjectStore.persist.rehydrate();
        if (request !== projectEnvironmentActivationRequest || !isCurrentRuntimeEnvironment(environmentKey, generation)) return;
        projectStoreHydrated = true;
    }
    if (request !== projectEnvironmentActivationRequest || !isCurrentRuntimeEnvironment(environmentKey, generation)) return;
    projectSelectionRequest += 1;
    seriesListRequest += 1;
    useProjectStore.setState(state => {
        const environments = alreadyHydrated ? persistableEnvironments(state) : state.environments;
        const next = environments[environmentKey] ?? emptyEnvironmentSlice();
        return {
            activeEnvironmentKey: environmentKey,
            environments,
            projects: next.projects,
            currentProject: next.currentProject,
            readWatermarks: next.readWatermarks,
            generatingTasks: next.generatingTasks,
            runningOps: {},
            currentSeries: null,
            seriesList: [],
            pendingExtraction: null,
            pendingExtractionScript: null,
            pendingExtractionOwner: null,
            isAnalyzing: false,
            isLoading: false,
        };
    });
}

export function deactivateProjectEnvironment(): void {
    projectEnvironmentActivationRequest += 1;
    projectSelectionRequest += 1;
    seriesListRequest += 1;
    useProjectStore.setState(state => ({
        activeEnvironmentKey: null,
        environments: persistableEnvironments(state),
        projects: [],
        currentProject: null,
        readWatermarks: {},
        generatingTasks: [],
        runningOps: {},
        currentSeries: null,
        seriesList: [],
        pendingExtraction: null,
        pendingExtractionScript: null,
        pendingExtractionOwner: null,
        isAnalyzing: false,
        isLoading: false,
    }));
}

export function resetProjectStoreHydrationForTests(): void {
    if (process.env.NODE_ENV === 'test') {
        projectStoreHydrated = false;
        projectEnvironmentActivationRequest = 0;
    }
}
