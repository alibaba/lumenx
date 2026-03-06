"use client";

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
    Layout, Image as ImageIcon, Box, Type, Move,
    ZoomIn, ZoomOut, Layers, Settings, Play,
    ChevronRight, ChevronLeft, Trash2, Copy, Wand2, Users, FileText, RefreshCw, Loader2, X, Lock, Unlock,
    Plus, ArrowUp, ArrowDown, Zap
} from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { api, API_URL, crudApi } from "@/lib/api";
import { getAssetUrl, getAssetUrlWithTimestamp } from "@/lib/utils";

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

    // Use global storyboard analysis state (persists across tab switches)
    const isAnalyzing = useProjectStore((state) => state.isAnalyzingStoryboard);
    const setIsAnalyzing = useProjectStore((state) => state.setIsAnalyzingStoryboard);

    const [editingFrameId, setEditingFrameId] = useState<string | null>(null);
    const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
    const [insertIndex, setInsertIndex] = useState<number | null>(null);



    // NEW: Analyze script text to generate storyboard frames
    const handleAnalyzeToStoryboard = async () => {
        if (!currentProject) return;

        const text = currentProject.originalText;
        if (!text || !text.trim()) {
            alert("请先输入剧本文本");
            return;
        }

        if (currentProject.frames?.length > 0) {
            if (!confirm("这将覆盖当前的所有分镜帧。是否继续？")) return;
        }

        setIsAnalyzing(true);
        try {
            const updatedProject = await api.analyzeToStoryboard(currentProject.id, text);
            updateProject(currentProject.id, updatedProject);
            alert(`成功生成 ${updatedProject.frames?.length || 0} 个分镜帧！`);
        } catch (error) {
            console.error("Analyze to storyboard failed:", error);
            alert("分镜生成失败，请查看控制台了解详情。");
        } finally {
            setIsAnalyzing(false);
        }
    };

    const handleImageClick = (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        setEditingFrameId(frameId);
    };

    const handleDeleteFrame = async (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!currentProject) return;
        if (!confirm("Are you sure you want to delete this frame?")) return;

        try {
            await crudApi.deleteFrame(currentProject.id, frameId);
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete frame:", error);
            alert("Failed to delete frame");
        }
    };

    const handleCopyFrame = async (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!currentProject) return;

        try {
            await crudApi.copyFrame(currentProject.id, frameId);
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to copy frame:", error);
            alert("Failed to copy frame");
        }
    };

    const handleCreateFrame = async (data: any) => {
        if (!currentProject) return;

        try {
            await crudApi.createFrame(currentProject.id, {
                ...data,
                insert_at: insertIndex !== null ? insertIndex : undefined
            });
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
            setIsCreateDialogOpen(false);
            setInsertIndex(null);
        } catch (error) {
            console.error("Failed to create frame:", error);
            alert("Failed to create frame");
        }
    };

    const handleMoveFrame = async (index: number, direction: 'up' | 'down', e: React.MouseEvent) => {
        e.stopPropagation();
        if (!currentProject || !currentProject.frames) return;

        const newIndex = direction === 'up' ? index - 1 : index + 1;
        if (newIndex < 0 || newIndex >= currentProject.frames.length) return;

        // Create new order
        const newFrames = [...currentProject.frames];
        const [movedFrame] = newFrames.splice(index, 1);
        newFrames.splice(newIndex, 0, movedFrame);

        const newOrderIds = newFrames.map((f: any) => f.id);

        try {
            // Optimistic update
            updateProject(currentProject.id, { ...currentProject, frames: newFrames });

            await crudApi.reorderFrames(currentProject.id, newOrderIds);
            // No need to fetch again if optimistic update was correct, but good for safety
        } catch (error) {
            console.error("Failed to reorder frames:", error);
            alert("Failed to reorder frames");
            // Revert on error would be ideal here by fetching project again
            const project = await api.getProject(currentProject.id);
            updateProject(currentProject.id, project);
        }
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
                if (!asset || !asset.variants || asset.variants.length === 0) return null;

                // Try to get selected variant first
                if (asset.selected_id) {
                    const selectedVariant = asset.variants.find((v: any) => v.id === asset.selected_id);
                    if (selectedVariant?.url) return selectedVariant.url;
                }

                // Fallback: auto-select first variant if no selection exists
                // This handles the case where selected_id is null/undefined
                return asset.variants[0]?.url || null;
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
                        const charUrl = getSelectedVariantUrl(char.three_view_asset)
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
                    <div className="flex items-center gap-2">
                        <button
                            onClick={handleAnalyzeToStoryboard}
                            disabled={isAnalyzing}
                            className="flex items-center gap-1 text-[10px] bg-primary/80 hover:bg-primary px-2 py-1 rounded text-white transition-colors disabled:opacity-50"
                            title="从剧本生成分镜帧"
                        >
                            {isAnalyzing ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
                            {isAnalyzing ? "生成中..." : "⚡ 生成分镜"}
                        </button>

                    </div>
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
                        {/* Add Frame Button (Top) */}
                        <div className="flex justify-center">
                            <button
                                onClick={() => { setInsertIndex(0); setIsCreateDialogOpen(true); }}
                                className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white rounded-lg transition-colors border border-dashed border-white/10 hover:border-white/30"
                            >
                                <Plus size={16} />
                                <span className="text-sm font-medium">Insert Frame at Start</span>
                            </button>
                        </div>

                        {currentProject?.frames?.map((frame: any, index: number) => (
                            <>
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
                                                src={getAssetUrlWithTimestamp(frame.rendered_image_url || frame.image_url, frame.updated_at)}
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

                                        {/* Frame Actions */}
                                        <div className="flex justify-end gap-2 mt-2 pt-2 border-t border-white/5 opacity-0 group-hover:opacity-100 transition-opacity">
                                            <div className="flex items-center gap-1 mr-auto">
                                                <button
                                                    onClick={(e) => handleMoveFrame(index, 'up', e)}
                                                    disabled={index === 0}
                                                    className="p-2 hover:bg-white/10 text-gray-400 hover:text-white rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                                                    title="Move Up"
                                                >
                                                    <ArrowUp size={14} />
                                                </button>
                                                <button
                                                    onClick={(e) => handleMoveFrame(index, 'down', e)}
                                                    disabled={index === (currentProject.frames?.length || 0) - 1}
                                                    className="p-2 hover:bg-white/10 text-gray-400 hover:text-white rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                                                    title="Move Down"
                                                >
                                                    <ArrowDown size={14} />
                                                </button>
                                            </div>

                                            <button
                                                onClick={(e) => handleCopyFrame(frame.id, e)}
                                                className="p-2 hover:bg-white/10 text-gray-400 hover:text-white rounded-lg transition-colors"
                                                title="Duplicate Frame"
                                            >
                                                <Copy size={14} />
                                            </button>
                                            <button
                                                onClick={(e) => handleDeleteFrame(frame.id, e)}
                                                className="p-2 hover:bg-red-500/20 text-gray-400 hover:text-red-400 rounded-lg transition-colors"
                                                title="Delete Frame"
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    </div>
                                </motion.div>

                                {/* Add Button Between Frames */}
                                < div className="flex justify-center opacity-0 hover:opacity-100 transition-opacity -my-3 z-10 relative" >
                                    <button
                                        onClick={() => { setInsertIndex(index + 1); setIsCreateDialogOpen(true); }}
                                        className="p-1 bg-[#222] border border-white/20 rounded-full text-gray-400 hover:text-white hover:border-primary hover:bg-primary/20 transition-all transform hover:scale-110"
                                        title="Insert Frame Here"
                                    >
                                        <Plus size={16} />
                                    </button>
                                </div>
                            </>
                        ))}
                    </div >
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

            {/* Create Frame Dialog */}
            <AnimatePresence>
                {isCreateDialogOpen && (
                    <CreateFrameDialog
                        onClose={() => { setIsCreateDialogOpen(false); setInsertIndex(null); }}
                        onCreate={handleCreateFrame}
                        scenes={currentProject?.scenes || []}
                    />
                )}
            </AnimatePresence>
        </div >
    );
}

function CreateFrameDialog({ onClose, onCreate, scenes }: { onClose: () => void; onCreate: (data: any) => void; scenes: any[] }) {
    const [action, setAction] = useState("");
    const [dialogue, setDialogue] = useState("");
    const [sceneId, setSceneId] = useState(scenes[0]?.id || "");
    const [isSubmitting, setIsSubmitting] = useState(false);

    const handleSubmit = async () => {
        if (!action.trim()) {
            alert("Action description is required");
            return;
        }
        if (!sceneId && scenes.length > 0) {
            alert("Please select a scene");
            return;
        }

        setIsSubmitting(true);
        try {
            await onCreate({
                action_description: action.trim(),
                dialogue: dialogue.trim(),
                scene_id: sceneId,
                camera_angle: "Medium Shot"
            });
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-[#1a1a1a] border border-white/10 rounded-2xl w-full max-w-lg overflow-hidden shadow-2xl"
            >
                <div className="p-6 border-b border-white/10 flex justify-between items-center bg-black/20">
                    <div className="flex items-center gap-3">
                        <Plus className="text-primary" size={20} />
                        <h2 className="text-lg font-bold text-white">Add New Frame</h2>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-lg transition-colors">
                        <X size={20} className="text-gray-400" />
                    </button>
                </div>

                <div className="p-6 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-400 mb-2">Scene</label>
                        <select
                            value={sceneId}
                            onChange={(e) => setSceneId(e.target.value)}
                            className="w-full px-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white focus:border-primary/50 focus:outline-none appearance-none"
                        >
                            <option value="" disabled>Select a scene</option>
                            {scenes.map((s: any) => (
                                <option key={s.id} value={s.id}>{s.name}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-400 mb-2">Action Description *</label>
                        <textarea
                            value={action}
                            onChange={(e) => setAction(e.target.value)}
                            placeholder="What is happening in this frame?"
                            rows={3}
                            className="w-full px-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white placeholder-gray-500 focus:border-primary/50 focus:outline-none resize-none"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-400 mb-2">Dialogue (Optional)</label>
                        <textarea
                            value={dialogue}
                            onChange={(e) => setDialogue(e.target.value)}
                            placeholder="Character dialogue..."
                            rows={2}
                            className="w-full px-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white placeholder-gray-500 focus:border-primary/50 focus:outline-none resize-none"
                        />
                    </div>
                </div>

                <div className="p-6 border-t border-white/10 flex justify-end gap-3">
                    <button
                        onClick={onClose}
                        className="px-6 py-2 bg-white/5 hover:bg-white/10 text-white rounded-lg transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={isSubmitting || !action.trim()}
                        className="px-6 py-2 bg-primary hover:bg-primary/90 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                        {isSubmitting && <RefreshCw size={16} className="animate-spin" />}
                        Create Frame
                    </button>
                </div>
            </motion.div>
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
