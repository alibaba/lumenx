"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import NextImage from "next/image";
import { Loader2, RefreshCw, Copy, Download, Trash2, AlertCircle } from "lucide-react";

import { VideoTask } from "@/lib/api";
import { messages } from "@/lib/i18n";
import {
    getSeedanceEditModeLabel,
    getSeedanceExtendModeLabel,
    getSeedanceReferenceModeLabel,
    getSeedanceWorkflowLabel,
} from "@/lib/seedance";
import { getAssetUrl } from "@/lib/utils";
import { isSeedanceI2VModel, useProjectStore, type StoryboardFrame } from "@/store/projectStore";
import { getGenerationBadgeText, getGenerationTooltip, isGenerationDegraded } from "@/lib/generation-provenance";

interface VideoQueueProps {
    tasks: VideoTask[];
    onRemix: (task: VideoTask) => void;
}

const copy = messages.modules.videoQueue;

export default function VideoQueue({ tasks, onRemix }: VideoQueueProps) {
    const [filter, setFilter] = useState<"all" | "processing" | "completed" | "failed">("all");

    const filteredTasks = tasks.filter((task) => {
        if (filter === "all") return true;
        if (filter === "processing") return task.status === "pending" || task.status === "processing";
        return task.status === filter;
    }).reverse();

    const processingCount = tasks.filter((task) => task.status === "pending" || task.status === "processing").length;

    return (
        <div className="h-full flex flex-col bg-black/40 backdrop-blur-sm border-l border-white/5">
            <div className="p-4 border-b border-white/5">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-display font-bold text-white">{copy.title}</h3>
                    <div className="text-xs font-mono text-gray-500 flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${processingCount > 0 ? "bg-green-500 animate-pulse" : "bg-gray-600"}`} />
                        GPU: {processingCount > 0 ? copy.gpuRunning : copy.gpuIdle}
                    </div>
                </div>

                <div className="flex bg-white/5 rounded-lg p-1 gap-1">
                    {[
                        { id: "all", label: copy.filters.all },
                        { id: "processing", label: copy.filters.processing },
                        { id: "completed", label: copy.filters.completed },
                    ].map((tab) => (
                        <button
                            key={tab.id}
                            onClick={() => setFilter(tab.id as typeof filter)}
                            className={`flex-1 py-1.5 text-xs rounded-md transition-colors ${filter === tab.id
                                ? "bg-white/10 text-white font-medium shadow-sm"
                                : "text-gray-500 hover:text-gray-300"
                                }`}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
                <AnimatePresence mode="popLayout">
                    {filteredTasks.map((task) => (
                        <TaskCard key={task.id} task={task} onRemix={onRemix} />
                    ))}

                    {filteredTasks.length === 0 && (
                        <div className="text-center py-10 text-gray-600 text-sm">
                            {copy.empty}
                        </div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}

function getSeedanceTaskTags(task: VideoTask) {
    if (!isSeedanceI2VModel(task.model)) {
        return [];
    }

    const tags: string[] = [];
    const workflow = task.seedance_workflow || "standard";
    let workflowLabel = getSeedanceWorkflowLabel(workflow);

    if (workflow === "extend" && task.seedance_extend_mode) {
        workflowLabel = `${workflowLabel} · ${getSeedanceExtendModeLabel(task.seedance_extend_mode)}`;
    }

    if (workflow === "edit" && task.seedance_edit_mode) {
        workflowLabel = `${workflowLabel} · ${getSeedanceEditModeLabel(task.seedance_edit_mode)}`;
    }

    tags.push(workflowLabel);
    tags.push(getSeedanceReferenceModeLabel(task.seedance_reference_mode));

    if (task.aspect_ratio) {
        tags.push(copy.seedance.aspectRatio(task.aspect_ratio));
    }

    if (typeof task.watermark === "boolean") {
        tags.push(task.watermark ? copy.seedance.withWatermark : copy.seedance.withoutWatermark);
    }

    if (typeof task.camera_fixed === "boolean") {
        tags.push(task.camera_fixed ? copy.seedance.cameraFixed : copy.seedance.cameraFlexible);
    }

    if (task.reference_video_urls?.length) {
        tags.push(copy.seedance.referenceVideos(task.reference_video_urls.length));
    }

    if (task.reference_audio_url) {
        tags.push(copy.seedance.referenceAudio);
    }

    return tags;
}

function TaskCard({ task, onRemix }: { task: VideoTask; onRemix: (t: VideoTask) => void }) {
    const sourceFrame = useProjectStore((state) =>
        state.currentProject?.frames?.find((frame: StoryboardFrame) => frame.id === task.frame_id)
    );
    const isCompleted = task.status === "completed";
    const isProcessing = task.status === "processing" || task.status === "pending";
    const isFailed = task.status === "failed";
    const seedanceTags = getSeedanceTaskTags(task);
    const generationBadge = getGenerationBadgeText(sourceFrame);
    const generationBadgeDegraded = isGenerationDegraded(sourceFrame);

    const getDisplayUrl = (url: string) => getAssetUrl(url);

    const handleCopyPrompt = async () => {
        try {
            await navigator.clipboard.writeText(task.prompt || "");
        } catch (error) {
            console.error("Copy prompt failed", error);
        }
    };

    const handleDownloadVideo = () => {
        if (!task.video_url) return;
        window.open(getDisplayUrl(task.video_url), "_blank", "noopener,noreferrer");
    };

    return (
        <motion.div
            layout
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className={`rounded-xl overflow-hidden border transition-all ${isProcessing ? "bg-white/5 border-white/10" :
                isFailed ? "bg-red-500/5 border-red-500/20" :
                    "bg-black/40 border-white/10 hover:border-white/20"
                }`}
        >
            {isProcessing && (
                <div className="p-3 flex gap-3 items-center">
                    <div className="w-12 h-12 rounded bg-black/50 relative overflow-hidden flex-shrink-0">
                        {task.image_url ? (
                            <NextImage
                                src={getDisplayUrl(task.image_url)}
                                alt={copy.inputAlt}
                                fill
                                sizes="48px"
                                unoptimized
                                className="w-full h-full object-cover opacity-60"
                            />
                        ) : (
                            <div className="w-full h-full flex items-center justify-center bg-purple-900/30 text-purple-400 text-[10px] font-bold">
                                {copy.r2vInput}
                            </div>
                        )}
                        <div className="absolute inset-0 flex items-center justify-center">
                            <Loader2 className="animate-spin text-primary" size={16} />
                        </div>
                    </div>
                    <div className="flex-1 min-w-0">
                        <div className="flex justify-between items-center mb-1">
                            <span className="text-xs font-mono text-gray-400">#{task.id.slice(0, 6)}</span>
                            <span className="text-xs text-primary animate-pulse">
                                {task.status === "pending" ? copy.pending : copy.processing}
                            </span>
                        </div>
                        <p className="text-xs text-gray-300 truncate">{task.prompt}</p>
                        <TagRow tags={seedanceTags} />
                    </div>
                </div>
            )}

            {isCompleted && (
                <div>
                    <div className="px-3 py-2 border-b border-white/5 flex justify-between items-center bg-white/5">
                        <span className="text-xs font-mono text-gray-500">#{task.id.slice(0, 6)}</span>
                        <div className="flex gap-2">
                            <button
                                onClick={() => onRemix(task)}
                                className="text-xs flex items-center gap-1 text-gray-400 hover:text-white transition-colors"
                                title={copy.remixTitle}
                            >
                                <RefreshCw size={12} /> {copy.remix}
                            </button>
                        </div>
                    </div>

                    <div className="flex h-32 relative group">
                        <div className="w-1/2 relative border-r border-white/10">
                            {task.image_url ? (
                                <NextImage
                                    src={getDisplayUrl(task.image_url)}
                                    alt={copy.inputAlt}
                                    fill
                                    sizes="(max-width: 768px) 50vw, 16rem"
                                    unoptimized
                                    className="object-cover"
                                />
                            ) : task.reference_video_urls && task.reference_video_urls.length > 0 ? (
                                <div className="w-full h-full grid grid-cols-2 gap-0.5 bg-purple-900/20">
                                    {task.reference_video_urls.slice(0, 4).map((url, idx) => (
                                        <div key={idx} className="relative bg-black/50 overflow-hidden">
                                            <video
                                                src={getAssetUrl(url)}
                                                className="w-full h-full object-cover"
                                                muted
                                                preload="metadata"
                                            />
                                            <div className="absolute bottom-0.5 left-0.5 bg-purple-600/80 px-1 rounded text-[8px] text-white font-bold">
                                                @{String.fromCharCode(65 + idx)}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="w-full h-full flex items-center justify-center bg-purple-900/10 text-purple-400/50 text-xs font-bold">
                                    {copy.r2vInput}
                                </div>
                            )}
                            <div className="absolute top-2 left-2 bg-black/60 px-1.5 py-0.5 rounded text-[10px] text-gray-300">{copy.input}</div>
                        </div>

                        <div className="w-1/2 relative bg-black">
                            {task.video_url ? (
                                <video
                                    src={getAssetUrl(task.video_url)}
                                    controls
                                    className="w-full h-full object-cover"
                                />
                            ) : (
                                <div className="w-full h-full flex items-center justify-center text-red-500 text-xs">
                                    {copy.error}
                                </div>
                            )}
                            <div className="absolute top-2 right-2 flex flex-col items-end gap-1">
                                <div className="bg-primary/80 px-1.5 py-0.5 rounded text-[10px] text-white">{copy.result}</div>
                                {generationBadge && (
                                    <div
                                        className={`rounded border px-1.5 py-0.5 text-[10px] ${generationBadgeDegraded
                                            ? "border-amber-400/30 bg-black/70 text-amber-200"
                                            : "border-emerald-400/30 bg-black/70 text-emerald-200"
                                            }`}
                                        title={getGenerationTooltip(sourceFrame)}
                                    >
                                        {generationBadge}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>

                    <div className="p-3">
                        <TagRow tags={seedanceTags} className="mb-3" />
                        <p className="text-xs text-gray-400 line-clamp-2 mb-3 hover:line-clamp-none transition-all cursor-help">
                            {task.prompt}
                        </p>

                        <div className="flex justify-between items-center">
                            <div className="flex gap-2">
                                <button
                                    onClick={handleCopyPrompt}
                                    className="p-1.5 hover:bg-white/10 rounded text-gray-400 hover:text-white"
                                    title={copy.copyPrompt}
                                >
                                    <Copy size={14} />
                                </button>
                                <button
                                    onClick={handleDownloadVideo}
                                    className="p-1.5 hover:bg-white/10 rounded text-gray-400 hover:text-white"
                                    title={copy.downloadVideo}
                                >
                                    <Download size={14} />
                                </button>
                            </div>
                            <button className="p-1.5 hover:bg-red-500/20 rounded text-gray-500 hover:text-red-400" title={copy.deleteTask}>
                                <Trash2 size={14} />
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {isFailed && (
                <div className="p-3">
                    <div className="flex items-center gap-2 text-red-400 mb-2">
                        <AlertCircle size={16} />
                        <span className="text-sm font-medium">{copy.failed}</span>
                    </div>
                    <TagRow tags={seedanceTags} className="mb-2" />
                    <p className="text-xs text-gray-500 mb-3">{copy.unknownError}</p>
                    <button
                        onClick={() => onRemix(task)}
                        className="w-full py-1.5 bg-white/5 hover:bg-white/10 rounded text-xs text-gray-300 transition-colors"
                    >
                        {copy.retry}
                    </button>
                </div>
            )}
        </motion.div>
    );
}

function TagRow({ tags, className = "" }: { tags: string[]; className?: string }) {
    if (tags.length === 0) {
        return null;
    }

    return (
        <div className={`flex flex-wrap gap-1.5 ${className}`.trim()}>
            {tags.map((tag) => (
                <span
                    key={tag}
                    className="px-2 py-0.5 rounded-full border border-white/10 bg-white/5 text-[10px] text-gray-300"
                >
                    {tag}
                </span>
            ))}
        </div>
    );
}
