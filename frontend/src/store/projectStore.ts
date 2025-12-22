import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from '@/lib/api';

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

export interface Character {
    id: string;
    name: string;
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
    image_url?: string;
    image_asset?: ImageAsset;
    status?: string;
    locked?: boolean;
}

export interface Prop {
    id: string;
    name: string;
    description: string;
    image_url?: string;
    image_asset?: ImageAsset;
    status?: string;
    locked?: boolean;
}

export interface StoryboardFrame {
    id: string;
    scene_id: string;
    image_url?: string;
    image_asset?: ImageAsset;
    rendered_image_url?: string;
    rendered_image_asset?: ImageAsset;
    status?: string;
    locked?: boolean;
    // ... other fields
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
    is_custom: boolean;
    reason?: string; // For AI recommendations
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
export const T2I_MODELS = [
    { id: 'wan2.6-t2i', name: 'Wan 2.6 T2I', description: 'Latest T2I model' },
    { id: 'wan2.5-t2i-preview', name: 'Wan 2.5 T2I Preview', description: 'Default T2I' },
    { id: 'wan2.2-t2i-plus', name: 'Wan 2.2 T2I Plus', description: 'Higher quality' },
    { id: 'wan2.2-t2i-flash', name: 'Wan 2.2 T2I Flash', description: 'Faster generation' },
];

export const I2I_MODELS = [
    { id: 'wan2.6-image', name: 'Wan 2.6 Image', description: 'Latest I2I model (HTTP)' },
    { id: 'wan2.5-i2i-preview', name: 'Wan 2.5 I2I Preview', description: 'Default I2I' },
];

export const I2V_MODELS = [
    { id: 'wan2.6-i2v', name: 'Wan 2.6 I2V', description: 'Latest I2V model (HTTP)' },
    { id: 'wan2.5-i2v-preview', name: 'Wan 2.5 I2V Preview', description: 'Default I2V' },
    { id: 'wan2.2-i2v-plus', name: 'Wan 2.2 I2V Plus', description: 'Higher quality' },
    { id: 'wan2.2-i2v-flash', name: 'Wan 2.2 I2V Flash', description: 'Faster generation' },
];

export const ASPECT_RATIOS = [
    { id: '9:16', name: '9:16', description: 'Portrait (576*1024)' },
    { id: '16:9', name: '16:9', description: 'Landscape (1024*576)' },
    { id: '1:1', name: '1:1', description: 'Square (1024*1024)' },
];

export const DEFAULT_STYLES: StylePreset[] = [
    {
        id: "Cinematic",
        name: "Cinematic Realism",
        color: "from-blue-500 to-purple-500",
        prompt: "cinematic lighting, movie still, 8k, highly detailed, realistic",
        negative_prompt: "cartoon, anime, illustration, painting, drawing, low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry"
    },
    {
        id: "Cyberpunk",
        name: "Cyberpunk Neon",
        color: "from-pink-500 to-cyan-500",
        prompt: "cyberpunk style, neon lights, futuristic, high tech, dark atmosphere",
        negative_prompt: "natural, rustic, vintage, low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry"
    },
    {
        id: "Anime",
        name: "Japanese Anime",
        color: "from-orange-400 to-red-500",
        prompt: "anime style, cel shaded, vibrant colors, studio ghibli style",
        negative_prompt: "photorealistic, 3d, realistic, low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry"
    },
    {
        id: "Watercolor",
        name: "Soft Watercolor",
        color: "from-green-400 to-teal-500",
        prompt: "watercolor painting, soft edges, artistic, pastel colors",
        negative_prompt: "sharp lines, photorealistic, 3d, low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry"
    },
    {
        id: "B&W Manga",
        name: "B&W Manga",
        color: "from-gray-700 to-gray-900",
        prompt: "black and white manga style, ink lines, screen tones, comic book",
        negative_prompt: "color, 3d, photorealistic, low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry"
    },
];

export interface Project {
    id: string;
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
}

interface ProjectStore {
    projects: Project[];
    currentProject: Project | null;
    isLoading: boolean;
    isAnalyzing: boolean;
    isAnalyzingArtStyle: boolean;

    // Global Style State
    styles: StylePreset[];
    selectedStyleId: string;

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

    // Style Actions
    setStyles: (styles: StylePreset[]) => void;
    updateStylePrompt: (id: string, prompt: string) => void;
    setSelectedStyleId: (id: string) => void;

    // Selection Actions
    // Selection Actions
    setSelectedFrameId: (id: string | null) => void;

    // Asset Generation State
    // Asset Generation State
    generatingTasks: { assetId: string; generationType: string; batchSize: number }[];
    addGeneratingTask: (assetId: string, generationType: string, batchSize: number) => void;
    removeGeneratingTask: (assetId: string, generationType: string) => void;

    // Storyboard Frame Rendering State
    renderingFrames: Set<string>;  // Set of frame IDs currently being rendered
    addRenderingFrame: (frameId: string) => void;
    removeRenderingFrame: (frameId: string) => void;
}

export const useProjectStore = create<ProjectStore>()(
    persist(
        (set, get) => ({
            projects: [],
            currentProject: null,
            isLoading: false,
            isAnalyzing: false,
            styles: DEFAULT_STYLES,
            selectedStyleId: "Cinematic",
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
                    console.error('Failed to create project:', error);
                    set({ isLoading: false });
                    throw error;
                }
            },

            analyzeProject: async (script: string) => {
                const { currentProject, updateProject, createProject } = get();
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
                        await createProject(currentProject?.title || "New Project", script);
                    }
                } catch (error) {
                    console.error("Failed to analyze script:", error);
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
                }

                // Then fetch latest data from backend
                try {
                    const API_URL = typeof window !== 'undefined'
                        ? (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:17177')
                        : 'http://localhost:17177';
                    const response = await fetch(`${API_URL}/projects/${id}`);
                    if (response.ok) {
                        const rawData = await response.json();
                        // Transform data to match frontend model (snake_case -> camelCase for specific fields)
                        const latestProject = {
                            ...rawData,
                            originalText: rawData.original_text
                        };

                        // Update both currentProject and projects array with latest data
                        set((state) => ({
                            currentProject: latestProject,
                            projects: state.projects.map((p) =>
                                p.id === id ? latestProject : p
                            ),
                        }));
                    }
                } catch (error) {
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
                    console.error('Failed to delete project from backend:', error);
                    // Still remove from local state for UX, but warn user
                    set((state) => ({
                        projects: state.projects.filter((p) => p.id !== id),
                        currentProject: state.currentProject?.id === id ? null : state.currentProject
                    }));
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
                    console.error("Failed to analyze art style:", error);
                    // We could add an error state here if needed
                } finally {
                    set({ isAnalyzingArtStyle: false });
                }
            },

            clearCurrentProject: () => {
                set({ currentProject: null });
            },

            setStyles: (styles) => set({ styles }),

            updateStylePrompt: (id, prompt) => set((state) => ({
                styles: state.styles.map(s => s.id === id ? { ...s, prompt } : s)
            })),

            setSelectedStyleId: (id) => set({ selectedStyleId: id }),

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
        }),
        {
            name: 'project-storage',
            // 明确使用 localStorage
            storage: {
                getItem: (name) => {
                    const str = localStorage.getItem(name);
                    return str ? JSON.parse(str) : null;
                },
                setItem: (name, value) => {
                    localStorage.setItem(name, JSON.stringify(value));
                },
                removeItem: (name) => {
                    localStorage.removeItem(name);
                },
            },
            // 持久化更多状态，包括 currentProject
            partialize: (state) => ({
                projects: state.projects,
                currentProject: state.currentProject,
                styles: state.styles,
                selectedStyleId: state.selectedStyleId,
                selectedFrameId: state.selectedFrameId,
                // generatingTasks 不持久化，避免刷新后卡住
            }),
        }
    )
);
