"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, RefreshCw, Copy, Download, Trash2, AlertCircle } from "lucide-react";
import { useTranslations } from "next-intl";

import type { VideoTask } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";

type VideoQueueFilter = "all" | "processing" | "completed" | "needs_confirmation" | "failed";

interface VideoQueueProps {
    tasks: VideoTask[];
    onRemix: (task: VideoTask) => void;
}

export default function VideoQueue({ tasks, onRemix }: VideoQueueProps) {
    const tv = useTranslations("video");
    const [filter, setFilter] = useState<VideoQueueFilter>("all");

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
                    {([
                        { id: "all", label: tv("all") },
                        { id: "processing", label: tv("processing") },
                        { id: "completed", label: tv("completed") },
                        { id: "needs_confirmation", label: tv("needsConfirmationTab") },
                    ] satisfies Array<{ id: VideoQueueFilter; label: string }>).map((tab) => (
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
    const isNeedsConfirmation = task.status === "needs_confirmation";

    const getDisplayUrl = (url: string) => {
        return getAssetUrl(url);
    };

    return (
        <motion.div
            layout
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className={`rounded-xl overflow-hidden border transition-all ${isProcessing ? "bg-glass border-glass-border" :
                isFailed ? "bg-red-500/5 border-red-500/20" :
                    isNeedsConfirmation ? "bg-amber-500/5 border-amber-500/25" :
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
                            <div className="w-full h-full flex items-center justify-center bg-purple-900/30 text-purple-400 text-[0.625rem] font-bold">
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
                            ) : task.reference_video_urls && task.reference_video_urls.length > 0 ? (
                                /* R2V: Show reference video thumbnails */
                                <div className="w-full h-full grid grid-cols-2 gap-0.5 bg-purple-900/20">
                                    {task.reference_video_urls.slice(0, 4).map((url, idx) => (
                                        <div key={idx} className="relative bg-surface overflow-hidden">
                                            <video
                                                src={getAssetUrl(url)}
                                                className="w-full h-full object-cover"
                                                muted
                                                preload="metadata"
                                            />
                                            <div className="absolute bottom-0.5 left-0.5 bg-purple-600/80 px-1 rounded text-[0.5rem] text-foreground font-bold">
                                                @{String.fromCharCode(65 + idx)}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="w-full h-full flex items-center justify-center bg-purple-900/10 text-purple-400/50 text-xs font-bold">
                                    R2V Input
                                </div>
                            )}
                            <div className="absolute top-2 left-2 bg-surface px-1.5 py-0.5 rounded text-[0.625rem] text-text-secondary">Input</div>
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
                            <div className="absolute top-2 right-2 bg-primary/80 px-1.5 py-0.5 rounded text-[0.625rem] text-foreground">Result</div>
                        </div>
                    </div>

                    {/* Prompt & Actions */}
                    <div className="p-3">
                        <p className="text-xs text-text-secondary line-clamp-2 mb-3 hover:line-clamp-none transition-all cursor-help">
                            {task.prompt}
                        </p>

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

            {/* Needs-confirmation State */}
            {isNeedsConfirmation && (
                <div className="p-3">
                    <div className="flex items-center gap-2 text-amber-300 mb-2">
                        <AlertCircle size={16} />
                        <span className="text-sm font-medium">{tv("needsConfirmation")}</span>
                    </div>
                    <p className="text-xs text-text-muted mb-3">{tv("needsConfirmationDetail")}</p>
                    {task.video_url ? (
                        <div className="mb-3 overflow-hidden rounded border border-amber-500/20 bg-black/40">
                            <video src={getAssetUrl(task.video_url)} controls className="h-28 w-full object-cover" />
                            <div className="px-2 py-1 text-[0.625rem] text-amber-200/80">{tv("historicalUnconfirmed")}</div>
                        </div>
                    ) : null}
                    <div className="space-y-1 rounded border border-glass-border/60 bg-black/30 px-2 py-1.5 font-mono text-[0.6875rem] text-text-secondary">
                        {task.provider_task_id ? (
                            <div className="flex gap-1.5">
                                <span className="text-text-muted">{tv("providerTaskId")}:</span>
                                <code className="min-w-0 flex-1 truncate" title={task.provider_task_id}>{task.provider_task_id}</code>
                            </div>
                        ) : null}
                        {task.provider_request_id ? (
                            <div className="flex gap-1.5">
                                <span className="text-text-muted">{tv("providerRequestId")}:</span>
                                <code className="min-w-0 flex-1 truncate" title={task.provider_request_id}>{task.provider_request_id}</code>
                            </div>
                        ) : null}
                        <div className="flex gap-1.5">
                            <span className="text-text-muted">local:</span>
                            <code className="min-w-0 flex-1 truncate" title={task.id}>{task.id}</code>
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
