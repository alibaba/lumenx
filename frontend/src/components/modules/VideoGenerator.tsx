"use client";

import { useState, useEffect } from "react";
import { DEFAULT_I2V_MODEL, VideoParams, useProjectStore, type Project, type VideoTask } from "@/store/projectStore";
import VideoCreator from "./VideoCreator";
import VideoSidebar from "./VideoSidebar";
import { api } from "@/lib/api";

export default function VideoGenerator() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const [tasks, setTasks] = useState<VideoTask[]>([]);

    // Shared state for Remix functionality
    const [remixData, setRemixData] = useState<Partial<VideoTask> | null>(null);

    const currentProjectId = currentProject?.id;
    const currentI2vModel = currentProject?.model_settings?.i2v_model;
    const currentVideoTasks = currentProject?.video_tasks;

    // Get default model from project settings
    const defaultI2vModel = currentI2vModel || DEFAULT_I2V_MODEL;

    // Generation Params (Lifted State)
    const [params, setParams] = useState<VideoParams>({
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
        aspectRatio: "adaptive",
        watermark: false,
        cameraFixed: false,
        referenceAudioUrl: "",
        seedanceReferenceMode: "image",
        seedanceWorkflow: "standard",
        seedanceExtendMode: "continue",
        seedanceEditMode: "subject_replace",
        seedancePreviewOnly: false,
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
        if (currentI2vModel) {
            setParams(p => ({ ...p, model: currentI2vModel }));
        }
    }, [currentI2vModel]);

    // Sync tasks from project
    useEffect(() => {
        if (currentVideoTasks) {
            setTasks(currentVideoTasks);
        }
    }, [currentVideoTasks]);

    // Poll for updates
    useEffect(() => {
        const hasActiveTasks = tasks.some(t => t.status === "pending" || t.status === "processing");
        if (!hasActiveTasks || !currentProjectId) return;

        const interval = setInterval(async () => {
            try {
                const project = await api.getProject(currentProjectId);
                if (project.video_tasks) {
                    setTasks(project.video_tasks);
                    updateProject(currentProjectId, { video_tasks: project.video_tasks });
                }
            } catch (error) {
                console.error("Failed to poll project status:", error);
            }
        }, 3000);

        return () => clearInterval(interval);
    }, [tasks, currentProjectId, updateProject]);

    const handleTaskCreated = (updatedProject: Project) => {
        if (updatedProject.video_tasks) {
            setTasks(updatedProject.video_tasks);
            if (currentProjectId) {
                updateProject(currentProjectId, { video_tasks: updatedProject.video_tasks });
            }
        }
    };

    const handleRemix = (task: VideoTask) => {
        setRemixData({
            image_url: task.image_url,
            prompt: task.prompt,
            negative_prompt: task.negative_prompt,
            seed: task.seed,
            duration: task.duration,
            audio_url: task.audio_url,
            prompt_extend: task.prompt_extend,
            aspect_ratio: task.aspect_ratio,
            watermark: task.watermark,
            camera_fixed: task.camera_fixed,
            reference_audio_url: task.reference_audio_url,
            seedance_reference_mode: task.seedance_reference_mode,
            seedance_workflow: task.seedance_workflow,
            seedance_extend_mode: task.seedance_extend_mode,
            seedance_edit_mode: task.seedance_edit_mode,
            model: task.model,
        });

        // Update params state
        setParams(p => ({
            ...p,
            model: task.model || p.model,
            generationMode: task.generation_mode || p.generationMode,
            referenceVideoUrls: task.reference_video_urls || [],
            duration: task.duration || 5,
            seed: task.seed,
            resolution: task.resolution || "720p",
            generateAudio: task.generate_audio,
            audioUrl: task.audio_url || "",
            promptExtend: task.prompt_extend ?? true,
            negativePrompt: task.negative_prompt || "",
            referenceAudioUrl: task.reference_audio_url || "",
            seedanceReferenceMode: task.seedance_reference_mode || "image",
            seedanceWorkflow: task.seedance_workflow || "standard",
            seedanceExtendMode: task.seedance_extend_mode || "continue",
            seedanceEditMode: task.seedance_edit_mode || "subject_replace",
            seedancePreviewOnly: false,
            aspectRatio: task.aspect_ratio || "adaptive",
            watermark: task.watermark ?? false,
            cameraFixed: task.camera_fixed ?? false,
            // Reset motion params as they are not stored directly in task (they are in prompt)
            cameraMovement: "none",
            subjectMotion: "still"
        }));
    };

    return (
        <div className="flex h-full w-full overflow-hidden">
            {/* Left: Creator (70%) */}
            <div className="w-[70%] h-full border-r border-white/10">
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
    );
}
