"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, RefreshCw, Copy, Download, Trash2, AlertCircle } from "lucide-react";
import { useTranslations } from "next-intl";

import { VideoTask } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";

interface VideoQueueProps {
    tasks: VideoTask[];
    onRemix: (task: VideoTask) => void;
}

export default function VideoQueue({ tasks, onRemix }: VideoQueueProps) {
    const tv = useTranslations("video");
    const [filter, setFilter] = useState<"all" | "processing" | "completed" | "failed">("all");
    const tabs: Array<{ id: typeof filter; label: string }> = [
        { id: "all", label: tv("all") },
        { id: "processing", label: tv("processing") },
        { id: "completed", label: tv("completed") },
    ];

    const filteredTasks = tasks.filter(t => {
        if (filter === "all") return true;
        if (filter === "processing") return t.status === "pending" || t.status === "processing";
        return t.status === filter;
    }).reverse(); // Newest first

    const processingCount = tasks.filter(t => t.status === "pending" || t.status === "processing").length;

    return (
        <div className="h-full flex flex-col bg-surface border-l border-border-subtle">
            {/* Header & Tabs */}
            <div className="p-4 border-b border-border-subtle">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="font-display font-bold text-foreground">{tv("taskQueue")}</h3>
                    <div className="text-xs font-mono text-text-muted flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${processingCount > 0 ? "bg-green-500 animate-pulse" : "bg-gray-600"}`} />
                        GPU: {processingCount > 0 ? "Running" : "Idle"}
                    </div>
                </div>

                <div className="flex bg-glass rounded-lg p-1 gap-1">
                    {tabs.map((tab) => (
                        <button
                            key={tab.id}
                            onClick={() => setFilter(tab.id)}
                            className={`flex-1 py-1.5 text-xs rounded-md transition-colors ${filter === tab.id
                                ? "bg-hover-bg text-foreground font-medium shadow-sm"
                                : "text-text-muted hover:text-text-secondary"
                                }`}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>
            </div>

            {/* Task List */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
                <AnimatePresence mode="popLayout">
                    {filteredTasks.map((task) => (
                        <TaskCard key={task.id} task={task} onRemix={onRemix} />
                    ))}

                    {filteredTasks.length === 0 && (
                        <div className="text-center py-10 text-text-muted text-sm">
                            {tv("noTasks")}
                        </div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}

function TaskCard({ task, onRemix }: { task: VideoTask; onRemix: (t: VideoTask) => void }) {
    const tv = useTranslations("video");
    const isCompleted = task.status === "completed";
    const isProcessing = task.status === "processing" || task.status === "pending";
    const isFailed = task.status === "failed";
    const imageRefCount = task.reference_image_urls?.length ?? 0;
    const videoRefCount = task.reference_video_urls?.length ?? 0;
    const isR2V = task.generation_mode === "r2v";


    const getDisplayUrl = (url: string) => {
        return getAssetUrl(url);
    };

    const renderMetaChips = () => (
        <div className="flex flex-wrap gap-1.5 text-[10px] font-mono text-text-muted">
            <span className="rounded bg-white/[0.04] px-1.5 py-0.5 uppercase">{task.generation_mode || "i2v"}</span>
            {task.model && <span className="rounded bg-white/[0.04] px-1.5 py-0.5">{task.model}</span>}
            {task.frame_id && <span className="rounded bg-white/[0.04] px-1.5 py-0.5">frame {task.frame_id.slice(0, 6)}</span>}
            {isR2V && imageRefCount > 0 && (
                <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-emerald-300">{imageRefCount} img</span>
            )}
            {isR2V && videoRefCount > 0 && (
                <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-sky-300">{videoRefCount} vid</span>
            )}
        </div>
    );

    const renderReferencePreview = () => {
        if (task.reference_image_urls && task.reference_image_urls.length > 0) {
            return (
                <div className="w-full h-full grid grid-cols-2 gap-0.5 bg-emerald-900/20">
                    {task.reference_image_urls.slice(0, 4).map((url, idx) => (
                        <div key={idx} className="relative bg-surface overflow-hidden">
                            <img src={getAssetUrl(url)} alt={`Reference ${idx + 1}`} className="w-full h-full object-cover" />
                            <div className="absolute bottom-0.5 left-0.5 bg-emerald-600/80 px-1 rounded text-[8px] text-foreground font-bold">
                                I{idx + 1}
                            </div>
                        </div>
                    ))}
                </div>
            );
        }

        if (task.reference_video_urls && task.reference_video_urls.length > 0) {
            return (
                <div className="w-full h-full grid grid-cols-2 gap-0.5 bg-sky-900/20">
                    {task.reference_video_urls.slice(0, 4).map((url, idx) => (
                        <div key={idx} className="relative bg-surface overflow-hidden">
                            <video
                                src={getAssetUrl(url)}
                                className="w-full h-full object-cover"
                                muted
                                preload="metadata"
                            />
                            <div className="absolute bottom-0.5 left-0.5 bg-sky-600/80 px-1 rounded text-[8px] text-foreground font-bold">
                                V{idx + 1}
                            </div>
                        </div>
                    ))}
                </div>
            );
        }

        return (
            <div className="w-full h-full flex items-center justify-center bg-purple-900/10 text-purple-400/50 text-xs font-bold">
                R2V Input
            </div>
        );
    };

    return (
        <motion.div
            layout
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className={`rounded-xl overflow-hidden border transition-all ${isProcessing ? "bg-glass border-glass-border" :
                isFailed ? "bg-red-500/5 border-red-500/20" :
                    "bg-surface border-glass-border hover:border-glass-border"
                }`}
        >
            {/* Processing State (Compact) */}
            {isProcessing && (
                <div className="p-3 flex gap-3 items-center">
                    <div className="w-12 h-12 rounded bg-surface/50 relative overflow-hidden flex-shrink-0">
                        {task.image_url ? (
                            <img
                                src={getDisplayUrl(task.image_url)}
                                alt="Input"
                                className="w-full h-full object-cover opacity-60"
                            />
                        ) : (
                            <div className="w-full h-full flex items-center justify-center bg-purple-900/30 text-purple-400 text-[10px] font-bold">
                                R2V
                            </div>
                        )}
                        <div className="absolute inset-0 flex items-center justify-center">
                            <Loader2 className="animate-spin text-primary" size={16} />
                        </div>
                    </div>
                    <div className="flex-1 min-w-0">
                        <div className="flex justify-between items-center mb-1">
                            <span className="text-xs font-mono text-text-secondary">#{task.id.slice(0, 6)}</span>
                            <span className="text-xs text-primary animate-pulse">
                                {task.status === "pending" ? tv("queued") : tv("generating")}
                            </span>
                        </div>
                        <p className="text-xs text-text-secondary truncate">{task.prompt}</p>
                        <div className="mt-1">{renderMetaChips()}</div>
                    </div>
                </div>
            )}

            {/* Completed State (Detailed) */}
            {isCompleted && (
                <div>
                    {/* Header */}
                    <div className="px-3 py-2 border-b border-border-subtle flex justify-between items-center bg-glass">
                        <span className="text-xs font-mono text-text-muted">#{task.id.slice(0, 6)}</span>
                        <div className="flex gap-2">
                            <button
                                onClick={() => onRemix(task)}
                                className="text-xs flex items-center gap-1 text-text-secondary hover:text-foreground transition-colors"
                                title={tv("remixTitle")}
                            >
                                <RefreshCw size={12} /> Remix
                            </button>
                        </div>
                    </div>

                    {/* Visual Comparison */}
                    <div className="flex h-32 relative group">
                        {/* Input Image/Videos (Left) */}
                        <div className="w-1/2 relative border-r border-glass-border">
                            {task.image_url ? (
                                <img src={getDisplayUrl(task.image_url)} alt="Input" className="w-full h-full object-cover" />
                            ) : isR2V ? (
                                renderReferencePreview()
                            ) : (
                                <div className="w-full h-full flex items-center justify-center bg-purple-900/10 text-purple-400/50 text-xs font-bold">
                                    Input
                                </div>
                            )}
                            <div className="absolute top-2 left-2 bg-surface px-1.5 py-0.5 rounded text-[10px] text-text-secondary">Input</div>
                        </div>

                        {/* Output Video (Right) */}
                        <div className="w-1/2 relative bg-black">
                            {task.video_url ? (
                                <video
                                    src={getAssetUrl(task.video_url)}
                                    controls
                                    className="w-full h-full object-cover"
                                />
                            ) : (
                                <div className="w-full h-full flex items-center justify-center text-red-500 text-xs">
                                    Error
                                </div>
                            )}
                            <div className="absolute top-2 right-2 bg-primary/80 px-1.5 py-0.5 rounded text-[10px] text-foreground">Result</div>
                        </div>
                    </div>

                    {/* Prompt & Actions */}
                    <div className="p-3">
                        <p className="text-xs text-text-secondary line-clamp-2 mb-3 hover:line-clamp-none transition-all cursor-help">
                            {task.prompt}
                        </p>
                        <div className="mb-3">{renderMetaChips()}</div>

                        <div className="flex justify-between items-center">
                            <div className="flex gap-2">
                                <button className="p-1.5 hover:bg-hover-bg rounded text-text-secondary hover:text-foreground">
                                    <Copy size={14} />
                                </button>
                                <button className="p-1.5 hover:bg-hover-bg rounded text-text-secondary hover:text-foreground">
                                    <Download size={14} />
                                </button>
                            </div>
                            <button className="p-1.5 hover:bg-red-500/20 rounded text-text-muted hover:text-red-400">
                                <Trash2 size={14} />
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Failed State */}
            {isFailed && (
                <div className="p-3">
                    <div className="flex items-center gap-2 text-red-400 mb-2">
                        <AlertCircle size={16} />
                        <span className="text-sm font-medium">{tv("genFailed")}</span>
                    </div>
                    <p className="text-xs text-text-muted mb-3">{tv("unknownError")}</p>
                    <button
                        onClick={() => onRemix(task)}
                        className="w-full py-1.5 bg-glass hover:bg-hover-bg rounded text-xs text-text-secondary transition-colors"
                    >
                        {tv("retryTask")}
                    </button>
                </div>
            )}
        </motion.div>
    );
}
