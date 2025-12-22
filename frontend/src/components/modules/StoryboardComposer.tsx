"use client";

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
    Layout, Image as ImageIcon, Box, Type, Move,
    ZoomIn, ZoomOut, Layers, Settings, Play,
    ChevronRight, ChevronLeft, Trash2, Copy, Wand2, Users, FileText, RefreshCw, Loader2, X, Lock, Unlock
} from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { api, API_URL } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";

import StoryboardFrameEditor from "./StoryboardFrameEditor";

export default function StoryboardComposer() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const selectedFrameId = useProjectStore((state) => state.selectedFrameId);
    const setSelectedFrameId = useProjectStore((state) => state.setSelectedFrameId);
    const updateProject = useProjectStore((state) => state.updateProject);

    // Use global rendering state (persists across module switches)
    const renderingFrames = useProjectStore((state) => state.renderingFrames);
    const addRenderingFrame = useProjectStore((state) => state.addRenderingFrame);
    const removeRenderingFrame = useProjectStore((state) => state.removeRenderingFrame);

    const [isReparsing, setIsReparsing] = useState(false);
    const [editingFrameId, setEditingFrameId] = useState<string | null>(null);

    const handleReparse = async () => {
        if (!currentProject) return;
        if (!confirm("Re-analyzing will overwrite current scenes, characters, and frames based on the text. Continue?")) return;

        setIsReparsing(true);
        try {
            const updatedProject = await api.reparseProject(currentProject.id, currentProject.originalText);
            updateProject(currentProject.id, updatedProject);
            alert("Script re-analyzed successfully!");
        } catch (error) {
            console.error("Reparse failed:", error);
            alert("Failed to re-analyze script.");
        } finally {
            setIsReparsing(false);
        }
    };

    const handleImageClick = (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        setEditingFrameId(frameId);
    };

    const handleRenderFrame = async (frame: any, batchSize: number = 1, e?: React.MouseEvent) => {
        e?.stopPropagation();
        if (!currentProject) return;

        addRenderingFrame(frame.id);
        try {
            // Construct composition data with references
            const compositionData: any = {
                character_ids: frame.character_ids,
                prop_ids: frame.prop_ids,
                scene_id: frame.scene_id,
                reference_image_urls: []
            };

            // Helper to get selected variant URL from an asset
            const getSelectedVariantUrl = (asset: any): string | null => {
                if (!asset || !asset.selected_id || !asset.variants) return null;
                const variant = asset.variants.find((v: any) => v.id === asset.selected_id);
                return variant?.url || null;
            };

            // 1. Add Scene Image - prioritize selected variant
            if (frame.scene_id) {
                const scene = currentProject.scenes?.find((s: any) => s.id === frame.scene_id);
                if (scene) {
                    const sceneUrl = getSelectedVariantUrl(scene.image_asset) || scene.image_url;
                    if (sceneUrl) compositionData.reference_image_urls.push(sceneUrl);
                }
            }

            // 2. Add Character Images - use selected variant from three_view > full_body > headshot
            if (frame.character_ids && frame.character_ids.length > 0) {
                frame.character_ids.forEach((charId: string) => {
                    const char = currentProject.characters?.find((c: any) => c.id === charId);
                    if (char) {
                        // Priority: three_view_asset > full_body_asset > headshot_asset > legacy fields
                        let charUrl = getSelectedVariantUrl(char.three_view_asset)
                            || getSelectedVariantUrl(char.full_body_asset)
                            || getSelectedVariantUrl(char.headshot_asset)
                            || char.three_view_image_url
                            || char.full_body_image_url
                            || char.headshot_image_url
                            || char.avatar_url
                            || char.image_url;
                        if (charUrl) compositionData.reference_image_urls.push(charUrl);
                    }
                });
            }

            // 3. Add Prop Images - prioritize selected variant
            if (frame.prop_ids && frame.prop_ids.length > 0) {
                frame.prop_ids.forEach((propId: string) => {
                    const prop = currentProject.props?.find((p: any) => p.id === propId);
                    if (prop) {
                        const propUrl = getSelectedVariantUrl(prop.image_asset) || prop.image_url;
                        if (propUrl) compositionData.reference_image_urls.push(propUrl);
                    }
                });
            }

            // Construct Enhanced Prompt using Art Direction (or fallback to legacy)
            const artDirection = currentProject?.art_direction;
            let globalStylePrompt = "";

            if (artDirection?.style_config) {
                // Use Art Direction style
                globalStylePrompt = artDirection.style_config.positive_prompt;
            } else {
                // Fallback to legacy style system
                const styles = useProjectStore.getState().styles;
                const selectedStyleId = useProjectStore.getState().selectedStyleId;
                const currentStyle = styles.find(s => s.id === selectedStyleId);
                globalStylePrompt = currentStyle?.prompt || "";
            }

            // Construct final prompt:
            // If image_prompt exists (polished or manually edited), it already contains action/dialogue,
            // so only prepend the style. Otherwise, build from action_description and dialogue.
            let finalPrompt = "";

            if (frame.image_prompt && frame.image_prompt.trim()) {
                // User has a custom/polished prompt - only add style prefix
                finalPrompt = globalStylePrompt
                    ? `${globalStylePrompt} . ${frame.image_prompt}`
                    : frame.image_prompt;
            } else {
                // No custom prompt - build from action_description and dialogue
                const parts = [
                    globalStylePrompt,
                    frame.action_description,
                    frame.dialogue ? `Dialogue context: "${frame.dialogue}"` : ""
                ].filter(Boolean);
                finalPrompt = parts.join(" . ");
            }

            await api.renderFrame(currentProject.id, frame.id, compositionData, finalPrompt, batchSize);

            // Fetch updated project to get new image URL and timestamp
            const updatedProject = await api.getProject(currentProject.id);
            useProjectStore.getState().updateProject(currentProject.id, updatedProject);

        } catch (error) {
            console.error("Render failed:", error);
            alert("Render failed. See console for details.");
        } finally {
            removeRenderingFrame(frame.id);
        }
    };

    return (
        <div className="flex h-full bg-[#0a0a0a] text-white overflow-hidden">
            {/* Left Column: Script Viewer */}
            <div className="w-1/3 min-w-[300px] max-w-[500px] border-r border-white/10 flex flex-col bg-[#111]">
                <div className="p-4 border-b border-white/10 flex items-center justify-between">
                    <h3 className="font-bold text-sm flex items-center gap-2">
                        <FileText size={16} className="text-primary" /> Original Script
                    </h3>
                    <button
                        onClick={handleReparse}
                        disabled={isReparsing}
                        className="flex items-center gap-1 text-[10px] bg-white/10 hover:bg-white/20 px-2 py-1 rounded text-white transition-colors disabled:opacity-50"
                        title="Re-extract entities from script"
                    >
                        <RefreshCw size={12} className={isReparsing ? "animate-spin" : ""} />
                        {isReparsing ? "Analyzing..." : "Re-Analyze"}
                    </button>
                </div>
                <div className="flex-1 p-4 overflow-hidden flex flex-col">
                    <textarea
                        className="flex-1 w-full h-full bg-black/20 border border-white/10 rounded-lg p-4 text-sm text-gray-300 resize-none focus:outline-none focus:border-primary/50 leading-relaxed"
                        value={currentProject?.originalText || ""}
                        onChange={(e) => updateProject(currentProject!.id, { originalText: e.target.value })}
                        placeholder="Paste your script here..."
                    />
                </div>
            </div>

            {/* Center Column: Frame List */}
            <div className="flex-1 flex flex-col bg-[#0a0a0a] relative">
                <div className="p-4 border-b border-white/10 flex items-center justify-between bg-[#111]">
                    <h3 className="font-bold text-sm flex items-center gap-2">
                        <Layout size={16} className="text-primary" /> Storyboard Frames
                    </h3>
                    <div className="text-xs text-gray-500">
                        {currentProject?.frames?.length || 0} Frames
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto p-8">
                    <div className="max-w-4xl mx-auto space-y-6">
                        {currentProject?.frames?.map((frame: any, index: number) => (
                            <motion.div
                                key={frame.id}
                                layoutId={frame.id}
                                onClick={() => setSelectedFrameId(frame.id)}
                                className={`group relative flex gap-6 p-4 rounded-xl border transition-all cursor-pointer ${selectedFrameId === frame.id
                                    ? "bg-white/5 border-primary ring-1 ring-primary"
                                    : "bg-[#161616] border-white/5 hover:border-white/20"
                                    }`}
                            >
                                {/* Frame Number */}
                                <div className="absolute -left-3 -top-3 w-8 h-8 rounded-full bg-[#222] border border-white/10 flex items-center justify-center text-xs font-bold text-gray-400 shadow-lg z-10">
                                    {index + 1}
                                </div>

                                {/* Image Preview */}
                                <div className="w-64 aspect-video bg-black/40 rounded-lg border border-white/5 overflow-hidden flex-shrink-0 relative">
                                    {frame.rendered_image_url || frame.image_url ? (
                                        <ImageWithRetry
                                            key={frame.id + (frame.updated_at || 0)} // Force remount on refresh
                                            src={getAssetUrl(frame.rendered_image_url || frame.image_url) + `?t=${frame.updated_at || 0}`}
                                            alt={`Frame ${index + 1}`}
                                            className="w-full h-full object-cover cursor-zoom-in"
                                            onClick={(e: React.MouseEvent) => handleImageClick(frame.id, e)}
                                        />
                                    ) : (
                                        <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 gap-2">
                                            <ImageIcon size={24} className="opacity-20" />
                                            <span className="text-[10px]">No Image</span>
                                        </div>
                                    )

                                    }

                                    {/* Hover Actions - pointer-events-none to allow image click */}
                                    <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2 pointer-events-none">
                                        {/* Lock Button */}
                                        <button
                                            onClick={async (e) => {
                                                e.stopPropagation();
                                                if (!currentProject) return;
                                                try {
                                                    await api.toggleFrameLock(currentProject.id, frame.id);
                                                    const updated = await api.getProject(currentProject.id);
                                                    updateProject(currentProject.id, updated);
                                                } catch (error) {
                                                    console.error("Toggle lock failed:", error);
                                                }
                                            }}
                                            className="p-2 bg-white/10 hover:bg-white/20 text-white rounded-lg text-xs font-bold flex items-center gap-1 pointer-events-auto"
                                            title={frame.locked ? "解锁" : "锁定"}
                                        >
                                            {frame.locked ? <Unlock size={14} /> : <Lock size={14} />}
                                        </button>

                                        {/* Render Buttons with Batch Size - only show if not locked */}
                                        {!frame.locked && (
                                            <div className="flex items-center gap-1 pointer-events-auto">
                                                {renderingFrames.has(frame.id) ? (
                                                    <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-700 rounded-lg">
                                                        <Loader2 size={14} className="animate-spin text-white" />
                                                        <span className="text-xs text-white">Generating...</span>
                                                    </div>
                                                ) : (
                                                    <>
                                                        {[1, 2, 3, 4].map(size => (
                                                            <button
                                                                key={size}
                                                                onClick={(e) => { e.stopPropagation(); handleRenderFrame(frame, size); }}
                                                                className="px-2 py-1.5 bg-primary/80 hover:bg-primary text-white rounded text-xs font-bold transition-colors"
                                                                title={`Generate ${size} variant${size > 1 ? 's' : ''}`}
                                                            >
                                                                <div className="flex items-center gap-1">
                                                                    <Wand2 size={12} />
                                                                    <span>×{size}</span>
                                                                </div>
                                                            </button>
                                                        ))}
                                                    </>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </div>

                                {/* Content */}
                                <div className="flex-1 flex flex-col gap-3">
                                    <div className="flex items-start justify-between">
                                        <div className="space-y-1">
                                            <div className="flex items-center gap-2">
                                                <span className="text-xs font-bold text-gray-500 uppercase tracking-wider">Action</span>
                                                {frame.camera_movement && (
                                                    <span className="text-[10px] px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded border border-blue-500/30">
                                                        {frame.camera_movement}
                                                    </span>
                                                )}
                                            </div>
                                            <p className="text-sm text-gray-200 leading-relaxed line-clamp-3">
                                                {frame.action_description}
                                            </p>
                                        </div>
                                    </div>

                                    {frame.dialogue && (
                                        <div className="mt-auto pt-3 border-t border-white/5">
                                            <span className="text-xs font-bold text-gray-500 uppercase tracking-wider block mb-1">Dialogue</span>
                                            <p className="text-sm text-gray-400 italic">"{frame.dialogue}"</p>
                                        </div>
                                    )}
                                </div>
                            </motion.div>
                        ))}
                    </div>
                </div>
            </div>
            {/* Storyboard Frame Editor Modal */}
            <AnimatePresence>
                {editingFrameId && currentProject?.frames?.find((f: any) => f.id === editingFrameId) && (
                    <StoryboardFrameEditor
                        frame={currentProject.frames.find((f: any) => f.id === editingFrameId)}
                        onClose={() => setEditingFrameId(null)}
                    />
                )}
            </AnimatePresence>
        </div>
    );
}

function ImageWithRetry({ src, alt, className, onClick }: { src: string, alt: string, className?: string, onClick?: (e: React.MouseEvent) => void }) {
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(false);
    const [retryCount, setRetryCount] = useState(0);
    const imgRef = useRef<HTMLImageElement>(null);

    // Reset state when src changes
    useEffect(() => {
        setIsLoading(true);
        setError(false);
        setRetryCount(0);
    }, [src]);

    useEffect(() => {
        if (imgRef.current && imgRef.current.complete) {
            if (imgRef.current.naturalWidth > 0) {
                setIsLoading(false);
            }
        }
    }, [src]);

    useEffect(() => {
        if (error && retryCount < 10) {
            const timer = setTimeout(() => {
                setRetryCount(prev => prev + 1);
                setError(false);
            }, 1000 * (retryCount + 1)); // Exponential backoff
            return () => clearTimeout(timer);
        }
    }, [error, retryCount]);

    // Construct src with retry param to bypass cache if retrying
    const displaySrc = retryCount > 0 ? `${src}${src.includes('?') ? '&' : '?'}retry=${retryCount}` : src;

    return (
        <div className={`relative ${className}`}>
            {isLoading && (
                <div className="absolute inset-0 flex items-center justify-center bg-white/5 backdrop-blur-sm z-10">
                    <RefreshCw className="animate-spin text-white/50" size={24} />
                </div>
            )}
            <img
                ref={imgRef}
                src={displaySrc}
                alt={alt}
                className={`${className} ${isLoading ? 'opacity-50' : 'opacity-100'} transition-opacity duration-300`}
                onLoad={() => setIsLoading(false)}
                onError={() => {
                    setError(true);
                    setIsLoading(true); // Keep showing loader while retrying
                }}
                onClick={onClick}
            />
            {error && retryCount >= 10 && (
                <div className="absolute inset-0 flex flex-col items-center justify-center bg-red-500/10 backdrop-blur-sm z-20 p-2 text-center">
                    <span className="text-xs text-red-400 font-bold">Failed to load</span>
                    <span className="text-[10px] text-red-400/70 break-all">{src}</span>
                </div>
            )}
        </div>
    );
}
