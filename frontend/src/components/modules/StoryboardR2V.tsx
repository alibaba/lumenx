"use client";

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { motion } from "framer-motion";
import { Plus, Settings2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useProjectStore } from "@/store/projectStore";
import { api, type VideoTask } from "@/lib/api";
import { getR2vReferenceInputConfig, getR2vRouteModelId, VIDEO_I2V_MODELS, DEFAULT_I2V_MODEL_ID } from "@/lib/modelCatalog";
import {
    insertStoryboardR2VTag,
    resolveStoryboardR2VRefs,
    stripStoryboardR2VTags,
    validateStoryboardR2VRefs,
} from "@/lib/storyboardR2VAssets";
import ShotCard, { type ShotNode } from "./storyboard-r2v/ShotCard";
import AssetDrawer from "./storyboard-r2v/AssetDrawer";
import VideoConfigModal, { type VideoConfig, DEFAULT_VIDEO_CONFIG } from "./storyboard-r2v/VideoConfigModal";

type ProjectFrame = {
    id: string;
    action_description?: string;
    video_url?: string;
    rendered_image_url?: string;
    image_url?: string;
};

type StoryboardR2VProjectLike = {
    id?: string;
    frames?: ProjectFrame[];
};

const getDraftStorageKey = (projectId: string) => `storyboard-r2v-draft:${projectId}`;

const createShotFromFrame = (frame: ProjectFrame): ShotNode => ({
    id: frame.id,
    prompt: frame.action_description || "",
    tabMode: "direct_r2v",
    videoUrl: frame.video_url || undefined,
    videoStatus: frame.video_url ? "completed" : undefined,
    imageUrl: frame.rendered_image_url || frame.image_url || undefined,
});

const createEmptyShot = (): ShotNode => ({
    id: `shot_${Date.now()}`,
    prompt: "",
    tabMode: "direct_r2v",
});

const isDraftShot = (value: unknown): value is ShotNode => {
    const candidate = value as Partial<ShotNode>;
    return (
        !!candidate &&
        typeof candidate.id === "string" &&
        typeof candidate.prompt === "string" &&
        (candidate.tabMode === "direct_r2v" || candidate.tabMode === "t2i_i2v")
    );
};

const readDraftShots = (projectId?: string): ShotNode[] | null => {
    if (!projectId || typeof window === "undefined") return null;
    try {
        const rawDraft = window.localStorage.getItem(getDraftStorageKey(projectId));
        if (!rawDraft) return null;
        const parsed = JSON.parse(rawDraft) as unknown;
        if (!Array.isArray(parsed)) return null;
        const shots = parsed.filter(isDraftShot);
        return shots.length > 0 ? shots : null;
    } catch {
        return null;
    }
};

const toDraftShot = (shot: ShotNode): ShotNode => ({
    id: shot.id,
    prompt: shot.prompt,
    tabMode: shot.tabMode,
    t2iImageUrl: shot.t2iImageUrl,
    t2iStatus: shot.t2iStatus === "completed" ? "completed" : undefined,
    videoUrl: shot.videoUrl,
    videoStatus: shot.videoStatus === "completed" ? "completed" : undefined,
    imageUrl: shot.imageUrl,
});

const buildInitialShots = (project?: StoryboardR2VProjectLike | null): ShotNode[] => {
    const draftShots = readDraftShots(project?.id);
    if (draftShots) return draftShots;
    if (project?.frames?.length) {
        return project.frames.map(createShotFromFrame);
    }
    return [createEmptyShot()];
};

export default function StoryboardR2V() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const t = useTranslations("storyboardR2V");
    const currentProjectId = currentProject?.id;
    const currentProjectFrames = currentProject?.frames;

    // Derive shots from project frames or initialize empty
    const [shots, setShots] = useState<ShotNode[]>(() => buildInitialShots(currentProject));

    useEffect(() => {
        setShots(buildInitialShots({ id: currentProjectId, frames: currentProjectFrames }));
    }, [currentProjectId, currentProjectFrames]);

    useEffect(() => {
        if (!currentProjectId || typeof window === "undefined") return;
        window.localStorage.setItem(
            getDraftStorageKey(currentProjectId),
            JSON.stringify(shots.map(toDraftShot))
        );
    }, [currentProjectId, shots]);

    // Global video config (with localStorage persistence for model selection)
    const [videoConfig, setVideoConfig] = useState<VideoConfig>(() => {
        const savedModel = typeof window !== 'undefined' ? localStorage.getItem('storyboard-r2v-model') : null;
        const projectModel = currentProject?.model_settings?.i2v_model || DEFAULT_I2V_MODEL_ID;
        const modelId = savedModel || projectModel;
        const modelConfig = VIDEO_I2V_MODELS.find(m => m.id === modelId);
        const dc = modelConfig?.duration;
        const defaultDuration = dc ? (dc.type === 'fixed' ? dc.value : dc.default) : 5;
        return { ...DEFAULT_VIDEO_CONFIG, model: modelId, duration: defaultDuration };
    });

    const handleConfigChange = useCallback((config: VideoConfig) => {
        if (typeof window !== 'undefined') {
            localStorage.setItem('storyboard-r2v-model', config.model);
        }
        setVideoConfig(config);
    }, []);

    // Modal & drawer state
    const [configModalOpen, setConfigModalOpen] = useState(false);
    const [drawerState, setDrawerState] = useState<{ isOpen: boolean; targetShotIndex: number | null }>({
        isOpen: false,
        targetShotIndex: null,
    });

    // Refs map for textareas (for asset insertion from drawer)
    const textareaRefs = useRef<Map<number, HTMLTextAreaElement>>(new Map());

    const registerTextarea = useCallback((index: number, element: HTMLTextAreaElement | null) => {
        if (element) {
            textareaRefs.current.set(index, element);
        } else {
            textareaRefs.current.delete(index);
        }
    }, []);

    const characters = useMemo(() => currentProject?.characters || [], [currentProject?.characters]);
    const scenes = useMemo(() => currentProject?.scenes || [], [currentProject?.scenes]);
    const props = useMemo(() => currentProject?.props || [], [currentProject?.props]);

    // Add a new shot after the given index
    const addShot = useCallback((afterIndex: number) => {
        const newShot: ShotNode = {
            id: `shot_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
            prompt: "",
            tabMode: "direct_r2v",
        };
        setShots(prev => {
            const updated = [...prev];
            updated.splice(afterIndex + 1, 0, newShot);
            return updated;
        });
    }, []);

    // Delete a shot
    const deleteShot = useCallback((index: number) => {
        setShots(prev => prev.filter((_, i) => i !== index));
    }, []);

    // Move shot up/down
    const moveShot = useCallback((index: number, direction: "up" | "down") => {
        setShots(prev => {
            const updated = [...prev];
            const targetIndex = direction === "up" ? index - 1 : index + 1;
            if (targetIndex < 0 || targetIndex >= updated.length) return prev;
            [updated[index], updated[targetIndex]] = [updated[targetIndex], updated[index]];
            return updated;
        });
    }, []);

    // Duplicate a shot
    const duplicateShot = useCallback((index: number) => {
        setShots(prev => {
            const source = prev[index];
            const newShot: ShotNode = {
                ...source,
                id: `shot_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
                videoUrl: undefined,
                videoTaskId: undefined,
                videoStatus: undefined,
                t2iImageUrl: undefined,
                t2iTaskId: undefined,
                t2iStatus: undefined,
            };
            const updated = [...prev];
            updated.splice(index + 1, 0, newShot);
            return updated;
        });
    }, []);

    // Update shot prompt
    const updatePrompt = useCallback((index: number, prompt: string) => {
        setShots(prev => prev.map((s, i) => i === index ? { ...s, prompt } : s));
    }, []);

    // Set shot tab mode
    const setTabMode = useCallback((index: number, mode: "t2i_i2v" | "direct_r2v") => {
        setShots(prev => prev.map((s, i) => i === index ? { ...s, tabMode: mode } : s));
    }, []);

    const resolveAssetRefs = useCallback(
        (prompt: string) => resolveStoryboardR2VRefs(prompt, characters, scenes, props),
        [characters, scenes, props]
    );

    const r2vRouteModelId = useMemo(() => getR2vRouteModelId(videoConfig.model), [videoConfig.model]);
    const r2vReferenceConfig = useMemo(() => getR2vReferenceInputConfig(r2vRouteModelId), [r2vRouteModelId]);
    const r2vReferenceMode = r2vReferenceConfig.type;

    const getReferenceValidation = useCallback(
        (prompt: string) => validateStoryboardR2VRefs(resolveAssetRefs(prompt), r2vReferenceMode, r2vReferenceConfig.max),
        [resolveAssetRefs, r2vReferenceMode, r2vReferenceConfig.max]
    );

    const syncCreatedTasks = useCallback(
        (createdTasks: VideoTask[]) => {
            const project = useProjectStore.getState().currentProject;
            if (!project?.id || createdTasks.length === 0) return;
            updateProject(project.id, {
                video_tasks: [...(project.video_tasks || []), ...createdTasks],
            });
        },
        [updateProject]
    );

    const syncTaskPatch = useCallback(
        (taskId: string, patch: Partial<VideoTask>) => {
            const project = useProjectStore.getState().currentProject;
            if (!project?.id) return;
            updateProject(project.id, {
                video_tasks: (project.video_tasks || []).map((task: VideoTask) =>
                    task.id === taskId ? { ...task, ...patch } : task
                ),
            });
        },
        [updateProject]
    );

    // Generate T2I image for a shot (t2i_i2v mode stage 1)
    const generateT2I = useCallback(async (index: number) => {
        const shot = shots[index];
        if (!currentProject || !shot.prompt.trim()) return;

        setShots(prev => prev.map((s, i) =>
            i === index ? { ...s, t2iStatus: "pending" } : s
        ));

        try {
            const result = await api.renderFrame(
                currentProject.id,
                shot.id,
                {},  // compositionData (empty for now)
                stripStoryboardR2VTags(shot.prompt),
                1    // batchSize
            );

            if (result?.task_id || result?.id) {
                const taskId = result.task_id || result.id;
                setShots(prev => prev.map((s, i) =>
                    i === index ? { ...s, t2iTaskId: taskId, t2iStatus: "processing" } : s
                ));
            } else if (result?.image_url || result?.rendered_image_url) {
                // Immediate result (synchronous render)
                const imageUrl = result.image_url || result.rendered_image_url;
                setShots(prev => prev.map((s, i) =>
                    i === index ? { ...s, t2iImageUrl: imageUrl, t2iStatus: "completed" } : s
                ));
            }
        } catch (error) {
            console.error("Failed to generate T2I for shot:", error);
            setShots(prev => prev.map((s, i) =>
                i === index ? { ...s, t2iStatus: "failed" } : s
            ));
        }
    }, [shots, currentProject]);

    // Generate video for a shot
    const generateVideo = useCallback(async (index: number) => {
        const shot = shots[index];
        if (!currentProject || !shot.prompt.trim()) return;

        const promptText = stripStoryboardR2VTags(shot.prompt);

        try {
            if (shot.tabMode === "direct_r2v") {
                // R2V mode: use reference assets
                const referenceRefs = resolveAssetRefs(shot.prompt);
                const referenceValidation = validateStoryboardR2VRefs(
                    referenceRefs,
                    r2vReferenceMode,
                    r2vReferenceConfig.max
                );
                const imageBased = r2vReferenceMode === "image";

                if (!referenceValidation.canGenerate) {
                    return;
                }

                setShots(prev => prev.map((s, i) =>
                    i === index ? { ...s, videoStatus: "pending" } : s
                ));

                const created = await api.createVideoTask(
                    currentProject.id,
                    "",  // no image_url for R2V
                    promptText,
                    videoConfig.duration,
                    undefined, // seed
                    videoConfig.resolution,
                    false, // generateAudio
                    "", // audioUrl
                    videoConfig.promptExtend,
                    videoConfig.negativePrompt,
                    1, // batchSize
                    r2vRouteModelId,  // use routed R2V model
                    shot.id, // frameId
                    "multi", // shotType
                    "r2v", // generationMode
                    !imageBased ? referenceRefs.videoUrls : undefined, // referenceVideoUrls (Wan 2.6 legacy)
                    undefined, undefined, undefined, // kling params
                    undefined, undefined, // vidu params
                    imageBased ? referenceRefs.imageUrls : undefined, // referenceImageUrls
                );

                const createdTasks = Array.isArray(created) ? created : [created];
                const task = createdTasks[0];
                syncCreatedTasks(createdTasks);

                if (task?.id) {
                    setShots(prev => prev.map((s, i) =>
                        i === index ? { ...s, videoTaskId: task.id, videoStatus: "processing" } : s
                    ));
                }
            } else {
                // I2V mode: use T2I image as first frame
                const imageUrl = shot.t2iImageUrl || shot.imageUrl || "";

                setShots(prev => prev.map((s, i) =>
                    i === index ? { ...s, videoStatus: "pending" } : s
                ));

                const created = await api.createVideoTask(
                    currentProject.id,
                    imageUrl,
                    promptText,
                    videoConfig.duration,
                    undefined, // seed
                    videoConfig.resolution,
                    false, // generateAudio
                    "", // audioUrl
                    videoConfig.promptExtend,
                    videoConfig.negativePrompt,
                    1, // batchSize
                    videoConfig.model, // direct I2V model
                    shot.id, // frameId
                    "multi", // shotType
                    "i2v", // generationMode
                    undefined, // referenceVideoUrls
                    // Kling params
                    videoConfig.mode,
                    videoConfig.sound,
                    videoConfig.cfgScale,
                    // Vidu params
                    videoConfig.viduAudio,
                    videoConfig.movementAmplitude,
                    // HappyHorse
                    undefined,
                );

                const createdTasks = Array.isArray(created) ? created : [created];
                const task = createdTasks[0];
                syncCreatedTasks(createdTasks);

                if (task?.id) {
                    setShots(prev => prev.map((s, i) =>
                        i === index ? { ...s, videoTaskId: task.id, videoStatus: "processing" } : s
                    ));
                }
            }
        } catch (error) {
            console.error("Failed to generate video for shot:", error);
            setShots(prev => prev.map((s, i) =>
                i === index ? { ...s, videoStatus: "failed" } : s
            ));
        }
    }, [shots, currentProject, videoConfig, resolveAssetRefs, r2vReferenceMode, r2vReferenceConfig.max, r2vRouteModelId, syncCreatedTasks]);

    // Poll for task completion (both T2I and video)
    useEffect(() => {
        const processingShots = shots.filter(s =>
            (s.videoTaskId && (s.videoStatus === "processing" || s.videoStatus === "pending")) ||
            (s.t2iTaskId && (s.t2iStatus === "processing" || s.t2iStatus === "pending"))
        );
        if (processingShots.length === 0) return;

        const interval = setInterval(async () => {
            for (const shot of processingShots) {
                // Poll video task
                if (shot.videoTaskId && (shot.videoStatus === "processing" || shot.videoStatus === "pending")) {
                    try {
                        const status = await api.getTaskStatus(shot.videoTaskId);
                        if (status.status === "completed" && status.video_url) {
                            setShots(prev => prev.map(s =>
                                s.id === shot.id ? { ...s, videoStatus: "completed", videoUrl: status.video_url } : s
                            ));
                            syncTaskPatch(shot.videoTaskId, {
                                status: "completed",
                                video_url: status.video_url,
                            });
                        } else if (status.status === "failed") {
                            setShots(prev => prev.map(s =>
                                s.id === shot.id ? { ...s, videoStatus: "failed" } : s
                            ));
                            syncTaskPatch(shot.videoTaskId, { status: "failed" });
                        }
                    } catch (error) {
                        console.error("Video poll failed for shot:", shot.id, error);
                    }
                }
                // Poll T2I task
                if (shot.t2iTaskId && (shot.t2iStatus === "processing" || shot.t2iStatus === "pending")) {
                    try {
                        const status = await api.getTaskStatus(shot.t2iTaskId);
                        if (status.status === "completed") {
                            const imageUrl = status.image_url || status.video_url || status.result_url;
                            if (imageUrl) {
                                setShots(prev => prev.map(s =>
                                    s.id === shot.id ? { ...s, t2iStatus: "completed", t2iImageUrl: imageUrl } : s
                                ));
                            }
                        } else if (status.status === "failed") {
                            setShots(prev => prev.map(s =>
                                s.id === shot.id ? { ...s, t2iStatus: "failed" } : s
                            ));
                        }
                    } catch (error) {
                        console.error("T2I poll failed for shot:", shot.id, error);
                    }
                }
            }
        }, 5000);

        return () => clearInterval(interval);
    }, [shots, syncTaskPatch]);

    // Insert asset tag from drawer into target shot
    const insertAssetFromDrawer = useCallback((type: string, name: string) => {
        const shotIndex = drawerState.targetShotIndex;
        if (shotIndex === null || shotIndex === undefined) return;

        const tag = `[${type}:${name}]`;
        const textarea = textareaRefs.current.get(shotIndex) ?? null;
        if (textarea) {
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const currentPrompt = shots[shotIndex].prompt;
            const insertion = insertStoryboardR2VTag(currentPrompt, tag, start, end);
            updatePrompt(shotIndex, insertion.prompt);
            setTimeout(() => {
                textarea.selectionStart = textarea.selectionEnd = insertion.cursor;
                textarea.focus();
            }, 0);
        } else {
            updatePrompt(shotIndex, shots[shotIndex].prompt + " " + tag);
        }
    }, [drawerState.targetShotIndex, shots, updatePrompt]);

    // Get model display name for toolbar
    const currentModelName = VIDEO_I2V_MODELS.find(m => m.id === videoConfig.model)?.name ?? videoConfig.model;

    return (
        <div className="h-full flex flex-col overflow-hidden relative">
            {/* Top Toolbar */}
            <div className="flex items-center justify-between px-6 py-3 border-b border-white/[0.06] bg-white/[0.02] backdrop-blur-xl shrink-0 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-foreground">
                        {shots.length} {shots.length === 1 ? "Shot" : "Shots"}
                    </span>
                    <motion.button
                        whileHover={{ scale: 1.04 }}
                        whileTap={{ scale: 0.96 }}
                        onClick={() => addShot(shots.length - 1)}
                        className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium text-primary hover:text-primary/80 transition-colors"
                    >
                        <Plus size={14} strokeWidth={2} />
                        {t("addShot")}
                    </motion.button>
                </div>
                <div className="flex items-center gap-3">
                    <span className="text-[11px] text-text-secondary tracking-wide">{t("currentModel")}: <span className="text-foreground font-medium">{currentModelName}</span></span>
                    <motion.button
                        whileHover={{ scale: 1.08 }}
                        whileTap={{ scale: 0.92 }}
                        onClick={() => setConfigModalOpen(true)}
                        className="flex items-center justify-center w-7 h-7 rounded-lg bg-white/[0.04] border border-white/[0.06] hover:bg-white/[0.08] hover:border-white/[0.10] text-text-secondary hover:text-foreground transition-all"
                        title={t("videoSettings")}
                    >
                        <Settings2 size={13} />
                    </motion.button>
                </div>
            </div>

            {/* Shot List (Timeline) */}
            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
                {shots.map((shot, index) => (
                    <motion.div
                        key={shot.id}
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{
                            type: "spring",
                            stiffness: 100,
                            damping: 20,
                            delay: Math.min(index * 0.03, 0.3),
                        }}
                    >
                        <ShotCard
                            shot={shot}
                            index={index}
                            totalShots={shots.length}
                            characters={characters}
                            scenes={scenes}
                            props={props}
                            referenceValidation={
                                shot.tabMode === "direct_r2v" && shot.prompt.trim()
                                    ? getReferenceValidation(shot.prompt)
                                    : undefined
                            }
                            onUpdatePrompt={(prompt) => updatePrompt(index, prompt)}
                            onGenerateT2I={() => generateT2I(index)}
                            onGenerateVideo={() => generateVideo(index)}
                            onDelete={() => deleteShot(index)}
                            onMoveUp={() => moveShot(index, "up")}
                            onMoveDown={() => moveShot(index, "down")}
                            onDuplicate={() => duplicateShot(index)}
                            onSetTabMode={(mode) => setTabMode(index, mode)}
                            onOpenDrawer={() => setDrawerState({ isOpen: true, targetShotIndex: index })}
                            onRegisterTextarea={(element) => registerTextarea(index, element)}
                            onCancelVideo={
                                shot.videoTaskId && currentProjectId
                                    ? async () => {
                                        try {
                                            await api.cancelVideoTask(currentProjectId, shot.videoTaskId!);
                                        } finally {
                                            // Optimistically flip local state so the spinner clears
                                            // even before the next refetch lands. Failed status
                                            // shows the existing Retry affordance.
                                            setShots((prev) =>
                                                prev.map((s, i) =>
                                                    i === index ? { ...s, videoStatus: "failed" as const } : s,
                                                ),
                                            );
                                        }
                                    }
                                    : undefined
                            }
                        />
                    </motion.div>
                ))}

                {/* Add shot at end */}
                <motion.button
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: Math.min(shots.length * 0.03, 0.3) }}
                    whileHover={{ scale: 1.005 }}
                    whileTap={{ scale: 0.995 }}
                    onClick={() => addShot(shots.length - 1)}
                    className="w-full py-3.5 border border-dashed border-white/[0.08] hover:border-primary/40 rounded-xl text-text-secondary hover:text-primary text-sm font-medium transition-all duration-300 flex items-center justify-center gap-2 bg-white/[0.01] hover:bg-white/[0.03]"
                >
                    <Plus size={16} strokeWidth={1.5} />
                    {t("addShot")}
                </motion.button>
            </div>

            {/* Asset Drawer (fixed overlay) */}
            <AssetDrawer
                isOpen={drawerState.isOpen}
                onClose={() => setDrawerState({ isOpen: false, targetShotIndex: null })}
                characters={characters}
                scenes={scenes}
                props={props}
                onSelectAsset={insertAssetFromDrawer}
            />

            {/* Video Config Modal */}
            <VideoConfigModal
                isOpen={configModalOpen}
                onClose={() => setConfigModalOpen(false)}
                config={videoConfig}
                onConfigChange={handleConfigChange}
            />
        </div>
    );
}
