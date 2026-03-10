"use client";

import { useState, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Play, Check, ChevronRight, Loader2, Film, AlertTriangle, Layout, Clock, FileText, Download } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { api, API_URL } from "@/lib/api";
import { getAssetUrl, extractErrorDetail } from "@/lib/utils";

export default function VideoAssembly() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    const [selectedFrameId, setSelectedFrameId] = useState<string | null>(null);
    const [isMerging, setIsMerging] = useState(false);
    const [mergeError, setMergeError] = useState<string | null>(null);
    const [isDownloading, setIsDownloading] = useState(false);

    // Group videos by frame
    const videosByFrame = useMemo(() => {
        if (!currentProject?.video_tasks) return {};

        const grouped: Record<string, any[]> = {};
        currentProject.video_tasks.forEach((task: any) => {
            if (task.status === "completed" && task.video_url) {
                if (task.frame_id) {
                    if (!grouped[task.frame_id]) grouped[task.frame_id] = [];
                    grouped[task.frame_id].push(task);
                }
            }
        });
        return grouped;
    }, [currentProject?.video_tasks]);

    const handleSelectVideo = async (frameId: string, videoId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.selectVideo(currentProject.id, frameId, videoId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select video:", error);
        }
    };

    const handleMerge = async () => {
        if (!currentProject) return;
        setIsMerging(true);
        setMergeError(null);  // Clear previous errors

        try {
            const updatedProject = await api.mergeVideos(currentProject.id);
            updateProject(currentProject.id, updatedProject);
            // Success - error will be null, merged video will show below
        } catch (error: any) {
            console.error("Failed to merge videos:", error);

            // Extract detailed error message from backend
            const errorDetail = extractErrorDetail(error, "Unknown error occurred during video merge");

            setMergeError(errorDetail);

            // Also show alert for immediate feedback
            alert(`Failed to merge videos:\n\n${errorDetail}`);
        } finally {
            setIsMerging(false);
        }
    };


    const handleDownload = async () => {
        if (!currentProject?.merged_video_url) return;
        setIsDownloading(true);
        try {
            // Build download URL - use proxy in dev to avoid CORS, direct in production
            const rawPath = currentProject.merged_video_url;
            const cleanPath = rawPath.startsWith("/") ? rawPath.slice(1) : rawPath;
            const isDev = process.env.NODE_ENV === "development";
            const url = isDev
                ? `/api-proxy/files/${cleanPath}`
                : getAssetUrl(rawPath);

            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const blob = await response.blob();
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = blobUrl;
            a.download = `${currentProject.title || "merged"}_${currentProject.id}.mp4`;
            a.click();
            setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
        } catch (error) {
            console.error("Failed to download video:", error);
            alert("Failed to download video. Please try again.");
        } finally {
            setIsDownloading(false);
        }
    };

    const selectedFrame = useMemo(() => {
        return currentProject?.frames?.find((f: any) => f.id === selectedFrameId);
    }, [currentProject?.frames, selectedFrameId]);

    const variants = selectedFrameId ? videosByFrame[selectedFrameId] || [] : [];

    return (
        <div className="h-full flex flex-col bg-black/20 overflow-hidden">
            {/* Top Section: Split View (Timeline + Variants) */}
            <div className="flex-1 flex flex-row min-h-0">
                {/* Left Column: Vertical List + Action Bar */}
                <div className="flex-1 flex flex-col min-w-0 border-r border-white/10">
                    {/* Header */}
                    <div className="h-14 border-b border-white/10 flex items-center justify-between px-6 bg-black/20">
                        <h2 className="font-bold text-lg flex items-center gap-2">
                            <Film className="text-primary" size={20} />
                            Assembly Timeline
                        </h2>
                        <div className="text-sm text-gray-400">
                            <span className="text-white font-bold">{currentProject?.frames?.filter((f: any) => f.selected_video_id).length}</span>
                            /{currentProject?.frames?.length} frames ready
                        </div>
                    </div>

                    {/* Vertical List */}
                    <div className="flex-1 overflow-y-auto custom-scrollbar p-6 space-y-4">
                        {currentProject?.frames?.map((frame: any, index: number) => {
                            const hasVideos = videosByFrame[frame.id]?.length > 0;
                            const isSelected = frame.id === selectedFrameId;
                            const selectedVideoId = frame.selected_video_id;
                            const selectedVideo = currentProject.video_tasks?.find((v: any) => v.id === selectedVideoId);

                            return (
                                <motion.div
                                    key={frame.id}
                                    layoutId={`frame-${frame.id}`}
                                    onClick={() => setSelectedFrameId(frame.id)}
                                    className={`group relative flex rounded-xl overflow-hidden cursor-pointer border transition-all bg-white/5 hover:bg-white/10 ${isSelected ? "border-primary ring-1 ring-primary/50" :
                                        selectedVideoId ? "border-green-500/30" : "border-white/10"
                                        }`}
                                >
                                    {/* Left: Preview */}
                                    <div className="w-48 aspect-video relative flex-shrink-0 border-r border-white/10 bg-black">
                                        {selectedVideo ? (
                                            <video
                                                src={getAssetUrl(selectedVideo.video_url)}
                                                className="w-full h-full object-cover"
                                                muted
                                                onMouseOver={(e) => e.currentTarget.play()}
                                                onMouseOut={(e) => {
                                                    e.currentTarget.pause();
                                                    e.currentTarget.currentTime = 0;
                                                }}
                                            />
                                        ) : (
                                            <div className="w-full h-full relative">
                                                {frame.image_url ? (
                                                    <img
                                                        src={getAssetUrl(frame.image_url)}
                                                        className="w-full h-full object-cover opacity-50 grayscale"
                                                    />
                                                ) : (
                                                    <div className="w-full h-full bg-white/5" />
                                                )}
                                                <div className="absolute inset-0 flex items-center justify-center">
                                                    {hasVideos ? (
                                                        <div className="bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded text-xs font-bold border border-yellow-500/50">
                                                            Select Video
                                                        </div>
                                                    ) : (
                                                        <div className="bg-red-500/20 text-red-500 px-2 py-1 rounded text-xs font-bold border border-red-500/50">
                                                            No Videos
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        )}
                                        <div className="absolute top-2 left-2 bg-black/60 backdrop-blur px-2 py-0.5 rounded text-[10px] font-mono text-white">
                                            #{index + 1}
                                        </div>
                                    </div>

                                    {/* Right: Details */}
                                    <div className="flex-1 p-4 flex flex-col justify-between min-w-0">
                                        <div className="space-y-2">
                                            <div className="flex items-start gap-2">
                                                <FileText size={14} className="text-gray-500 mt-0.5 flex-shrink-0" />
                                                <p className="text-sm text-gray-300 line-clamp-2 leading-relaxed">
                                                    {frame.image_prompt || frame.action_description || "No prompt available"}
                                                </p>
                                            </div>
                                            {frame.dialogue && (
                                                <div className="flex items-start gap-2 pl-6 border-l-2 border-white/10 ml-1">
                                                    <p className="text-xs text-gray-400 italic">"{frame.dialogue}"</p>
                                                </div>
                                            )}
                                        </div>

                                        <div className="flex items-center justify-between mt-2 pt-2 border-t border-white/5">
                                            <div className="flex items-center gap-4 text-xs text-gray-500">
                                                <span className="flex items-center gap-1">
                                                    <Clock size={12} /> {selectedVideo ? `${selectedVideo.duration}s` : "--"}
                                                </span>
                                                <span className="flex items-center gap-1">
                                                    <Film size={12} /> {videosByFrame[frame.id]?.length || 0} variants
                                                </span>
                                            </div>

                                            {selectedVideoId && (
                                                <div className="flex items-center gap-1 text-green-500 text-xs font-bold">
                                                    <Check size={12} /> Ready
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </motion.div>
                            );
                        })}
                    </div>

                    {/* Bottom Action Bar */}
                    <div className="h-20 border-t border-white/10 bg-black/40 backdrop-blur flex items-center justify-end px-8">
                        <button
                            onClick={handleMerge}
                            disabled={isMerging}
                            className="bg-primary hover:bg-primary/90 text-white px-8 py-3 rounded-xl font-bold flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-primary/20"
                        >
                            {isMerging ? <Loader2 className="animate-spin" /> : <Film />}
                            Merge & Proceed
                        </button>
                    </div>

                    {/* Error Display */}
                    {mergeError && (
                        <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            className="px-8 pb-4"
                        >
                            <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                                <div className="flex items-start gap-3">
                                    <AlertTriangle className="text-red-500 flex-shrink-0 mt-0.5" size={20} />
                                    <div className="flex-1">
                                        <h4 className="text-sm font-bold text-red-400 mb-1">Merge Failed</h4>
                                        <p className="text-xs text-red-300/90 whitespace-pre-wrap leading-relaxed font-mono">
                                            {mergeError}
                                        </p>
                                        {mergeError.toLowerCase().includes("ffmpeg") && (
                                            <a
                                                href="https://ffmpeg.org/download.html"
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="text-xs text-blue-400 hover:text-blue-300 underline mt-2 inline-block"
                                            >
                                                Download FFmpeg →
                                            </a>
                                        )}
                                        <button
                                            onClick={() => setMergeError(null)}
                                            className="mt-3 text-xs text-gray-400 hover:text-white underline"
                                        >
                                            Dismiss
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </motion.div>
                    )}
                </div>

                {/* Right Sidebar - Variants */}
                <div className="w-96 bg-[#0f0f0f] flex flex-col shadow-2xl z-10 border-l border-white/10">
                    <div className="h-14 border-b border-white/10 flex items-center justify-between px-4 bg-black/20">
                        <h3 className="font-bold text-sm">Variants</h3>
                        {selectedFrameId && (
                            <span className="text-xs text-gray-500">Frame #{(currentProject?.frames?.findIndex((f: any) => f.id === selectedFrameId) ?? -1) + 1}</span>
                        )}
                    </div>

                    <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
                        {selectedFrameId ? (
                            <div className="space-y-4">
                                {variants.length > 0 ? (
                                    variants.map((video: any, idx: number) => {
                                        const isSelected = selectedFrame?.selected_video_id === video.id;
                                        return (
                                            <div
                                                key={video.id}
                                                className={`rounded-xl overflow-hidden border transition-all group ${isSelected ? "border-green-500 ring-1 ring-green-500/50 bg-green-500/5" : "border-white/10 bg-white/5 hover:border-white/30"
                                                    }`}
                                            >
                                                <div className="aspect-video relative bg-black">
                                                    <video
                                                        src={getAssetUrl(video.video_url)}
                                                        className="w-full h-full object-contain"
                                                        controls
                                                    />
                                                    {/* Overlay Info */}
                                                    <div className="absolute top-2 left-2 bg-black/60 backdrop-blur px-1.5 rounded text-[10px] text-white opacity-0 group-hover:opacity-100 transition-opacity">
                                                        {video.duration}s
                                                    </div>
                                                </div>
                                                <div className="p-3">
                                                    <div className="flex items-center justify-between mb-2">
                                                        <div className="text-xs text-gray-400">
                                                            Variant #{idx + 1}
                                                        </div>
                                                        <div className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-gray-400">
                                                            {video.model}
                                                        </div>
                                                    </div>

                                                    {isSelected ? (
                                                        <div className="w-full py-2 bg-green-500/10 text-green-500 rounded-lg text-xs font-bold flex items-center justify-center gap-2 border border-green-500/20">
                                                            <Check size={14} /> Selected
                                                        </div>
                                                    ) : (
                                                        <button
                                                            onClick={() => handleSelectVideo(selectedFrameId, video.id)}
                                                            className="w-full py-2 bg-white/10 hover:bg-white/20 rounded-lg text-xs font-medium transition-colors text-white"
                                                        >
                                                            Select This Variant
                                                        </button>
                                                    )}
                                                </div>
                                            </div>
                                        );
                                    })
                                ) : (
                                    <div className="text-center py-12 text-gray-500 flex flex-col items-center">
                                        <AlertTriangle className="mb-3 opacity-50" size={32} />
                                        <p className="text-sm font-medium">No videos generated</p>
                                        <p className="text-xs mt-1 max-w-[200px]">Go back to the Motion step to generate videos for this frame.</p>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div className="h-full flex flex-col items-center justify-center text-gray-500 gap-3">
                                <Layout size={48} className="opacity-10" />
                                <p className="text-sm">Select a frame from the timeline<br />to view video variants</p>
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* Bottom Section: Merged Video Preview */}
            <AnimatePresence>
                {currentProject?.merged_video_url && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="border-t border-white/10 bg-[#050505] shadow-2xl z-20"
                    >
                        <div className="max-w-7xl mx-auto p-6 flex gap-8">
                            {/* Video Player */}
                            <div className="w-1/3 aspect-video bg-black rounded-xl overflow-hidden border border-white/10 shadow-lg relative group">
                                <video
                                    src={getAssetUrl(currentProject.merged_video_url)}
                                    className="w-full h-full object-contain"
                                    controls
                                    autoPlay
                                />
                            </div>

                            {/* Info & Actions */}
                            <div className="flex-1 flex flex-col justify-center space-y-4">
                                <div>
                                    <h3 className="text-xl font-bold text-white flex items-center gap-2">
                                        <Check className="text-green-500" /> Merged Video Ready
                                    </h3>
                                    <p className="text-gray-400 mt-1">
                                        All selected clips have been stitched together. You can now proceed to add voiceovers and sound effects.
                                    </p>
                                </div>

                                <div className="flex gap-4">
                                    <button
                                        onClick={handleDownload}
                                        disabled={isDownloading}
                                        className="px-6 py-3 bg-white/10 hover:bg-white/20 text-white rounded-lg font-bold flex items-center gap-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                    >
                                        <Download size={18} />
                                        {isDownloading ? "Downloading..." : "Download MP4"}
                                    </button>
                                    {/* Optional: Proceed Button if needed, or user uses sidebar */}
                                </div>
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
