"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslations } from "next-intl";
import { Video } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import VideoCreator from "./VideoCreator";
import VideoSidebar from "./VideoSidebar";
import { resolveLegacyVideoSidebarModelId } from "@/lib/legacyVideoSidebarModels";
import { VideoTask } from "@/lib/api";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { resolveModelId } from "@/lib/modelCatalog";
import StepHeader from "@/components/shared/StepHeader";
import { ProjectReadbackError, readProjectOnce } from "@/lib/projectReadback";

export default function VideoGenerator() {
    const tStep = useTranslations("stepHeader");
    const tFrameCommand = useTranslations("frameCommands");
    const currentProject = useProjectStore((state) => state.currentProject);
    const [tasks, setTasks] = useState<VideoTask[]>([]);
    const pollInFlightRef = useRef(false);
    const [pollBlocked, setPollBlocked] = useState<{ projectId: string; signature: string } | null>(null);
    const [pollReadFailed, setPollReadFailed] = useState(false);

    const currentProjectReadGuard = useCallback((expectedProjectId: string) => {
        const environment = getRuntimeEnvironment();
        return () => {
            const current = useProjectStore.getState().currentProject;
            return !!environment && current?.id === expectedProjectId && current.readEnvironment === environment.key
                && isCurrentRuntimeEnvironment(environment.key, environment.generation);
        };
    }, []);

    // Shared state for Remix functionality
    const [remixData, setRemixData] = useState<Partial<VideoTask> | null>(null);

    // Get default model from project settings
    const defaultI2vModel = resolveLegacyVideoSidebarModelId(resolveModelId(
        'i2v',
        currentProject?.model_settings?.i2v_model,
        'video_sidebar',
    ));

    // Generation Params (Lifted State)
    const [params, setParams] = useState({
        resolution: "720p",
        duration: 5,
        seed: undefined as number | undefined,
        generateAudio: true,  // Default to AI Sound enabled
        audioUrl: "",
        promptExtend: true,
        negativePrompt: "",
        batchSize: 1,
        cameraMovement: "none" as string,
        subjectMotion: "still" as string,
        model: defaultI2vModel,
        shotType: "single" as string,  // 'single' or 'multi' (only for wan2.6-i2v)
        generationMode: "i2v" as string,  // 'i2v' or 'r2v'
        referenceVideoUrls: [] as string[],  // Reference videos for R2V (max 3)
        // Kling params
        mode: "std" as string,
        sound: false,
        cfgScale: 0.5,
        // Vidu params
        viduAudio: true,
        movementAmplitude: "auto" as string,
    });

    // Sync model from project settings when project changes
    useEffect(() => {
        setParams((p) => ({
            ...p,
            model: resolveLegacyVideoSidebarModelId(resolveModelId(
                'i2v',
                currentProject?.model_settings?.i2v_model,
                'video_sidebar',
            )),
        }));
    }, [currentProject?.model_settings?.i2v_model]);

    useEffect(() => {
        setPollReadFailed(false);
        setPollBlocked(null);
    }, [currentProject?.id, currentProject?.readEnvironment]);

    // The sidebar reflects the confirmed store, including an empty project.
    useEffect(() => {
        setTasks((currentProject?.video_tasks ?? []) as VideoTask[]);
    }, [currentProject?.video_tasks]);

    // Poll for updates through the shared readback guard. Keep the
    // in-flight latch at session scope so effect restarts from task
    // snapshots cannot start overlapping project reads.
    useEffect(() => {
        const activeTaskIds = tasks
            .filter(t => t.status === "pending" || t.status === "processing")
            .map(t => t.id)
            .sort();
        if (!activeTaskIds.length || !currentProject?.id || currentProject.readEnvironment !== getRuntimeEnvironment()?.key) return;

        const projectId = currentProject.id;
        const signature = activeTaskIds.join('|');
        if (pollBlocked?.projectId === projectId && pollBlocked.signature === signature) return;

        let cancelled = false;
        const interval = setInterval(async () => {
            if (pollInFlightRef.current) return;
            pollInFlightRef.current = true;
            try {
                const project = await readProjectOnce(projectId, undefined, {
                    isCurrent: () => !cancelled && currentProjectReadGuard(projectId)(),
                });
                if (cancelled) return;
                setTasks((project.video_tasks ?? []) as VideoTask[]);
                setPollReadFailed(false);
                setPollBlocked(prev => prev?.projectId === projectId ? null : prev);
            } catch (error) {
                if (cancelled) return;
                console.error("Failed to poll project status:", error);
                if (error instanceof ProjectReadbackError) {
                    setPollReadFailed(true);
                    setPollBlocked({ projectId, signature });
                }
            } finally {
                pollInFlightRef.current = false;
            }
        }, 3000);

        return () => { cancelled = true; clearInterval(interval); };
    }, [tasks, currentProject?.id, currentProject?.readEnvironment, pollBlocked, currentProjectReadGuard]);

    const handleTaskCreated = (updatedProject: any) => {
        const current = useProjectStore.getState().currentProject;
        const environment = getRuntimeEnvironment();
        if (!environment || !current || !updatedProject?.video_tasks || !updatedProject.id || updatedProject.id !== current.id
            || updatedProject.readEnvironment !== environment.key || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        if (current.readEnvironment === environment.key) setTasks((current.video_tasks ?? []) as VideoTask[]);
    };

    const handleRemix = (task: VideoTask) => {
        setRemixData({
            image_url: task.image_url,
            prompt: task.prompt,
            negative_prompt: task.negative_prompt,
            seed: task.seed,
            duration: task.duration,
            audio_url: task.audio_url,
            prompt_extend: task.prompt_extend
        });

        // Update params state
        setParams(p => ({
            ...p,
            duration: task.duration || 5,
            seed: task.seed,
            resolution: task.resolution || "720p",
            generateAudio: task.generate_audio,
            audioUrl: task.audio_url || "",
            promptExtend: task.prompt_extend ?? true,
            negativePrompt: task.negative_prompt || "",
            // Reset motion params as they are not stored directly in task (they are in prompt)
            cameraMovement: "none",
            subjectMotion: "still"
        }));
    };

    const queueCount = tasks.filter(t => t.status === "pending" || t.status === "processing").length;
    const doneCount = tasks.filter(t => t.status === "completed").length;

    return (
        <div className="flex flex-col h-full w-full overflow-hidden">
            <StepHeader
                stepNumber={5}
                totalSteps={6}
                icon={<Video />}
                englishName="Motion Generator"
                title={tStep("motionTitle")}
                subtitle={tStep("motionSubtitle")}
                trailing={tasks.length > 0 ? (
                    <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-text-muted">
                        <span className="text-foreground font-medium">{tasks.length}</span>
                        <span className="ml-1.5">shots</span>
                        {doneCount > 0 ? (
                            <>
                                <span className="mx-1.5 text-text-muted/40">·</span>
                                <span className="text-primary">{doneCount}</span>
                                <span className="ml-1.5">done</span>
                            </>
                        ) : null}
                        {queueCount > 0 ? (
                            <>
                                <span className="mx-1.5 text-text-muted/40">·</span>
                                <span className="text-foreground">{queueCount}</span>
                                <span className="ml-1.5">in queue</span>
                            </>
                        ) : null}
                    </span>
                ) : undefined}
            />
            {pollReadFailed ? (
                <div role="alert" className="mx-6 mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                    {tFrameCommand("readFailed")}
                </div>
            ) : null}
            <div className="flex flex-1 overflow-hidden">
                {/* Left: Creator (70%) */}
                <div className="w-[70%] h-full border-r border-glass-border">
                    <VideoCreator
                        onTaskCreated={handleTaskCreated}
                        remixData={remixData}
                        onRemixClear={() => setRemixData(null)}
                        params={params}
                        onParamsChange={(newParams) => setParams(p => ({ ...p, ...newParams }))}
                    />
                </div>

                {/* Right: Sidebar (30%) */}
                <div className="w-[30%] h-full">
                    <VideoSidebar
                        tasks={tasks}
                        onRemix={handleRemix}
                        params={params}
                        setParams={setParams}
                    />
                </div>
            </div>
        </div>
    );
}
