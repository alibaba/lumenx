"use client";

import { useState, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, RefreshCw, Check, AlertTriangle, Image as ImageIcon, Lock, Unlock, ChevronRight, Maximize2 } from "lucide-react";
import { api, API_URL } from "@/lib/api";
import { VariantSelector } from "../common/VariantSelector";
import { useProjectStore } from "@/store/projectStore";

interface StoryboardFrameEditorProps {
    frame: any;
    onClose: () => void;
}

export default function StoryboardFrameEditor({ frame: initialFrame, onClose }: StoryboardFrameEditorProps) {
    const currentProject = useProjectStore(state => state.currentProject);
    const updateProject = useProjectStore(state => state.updateProject);

    // Get the latest frame data from the store (instead of using stale prop)
    const frame = useMemo(() => {
        if (!currentProject?.frames) return initialFrame;
        return currentProject.frames.find((f: any) => f.id === initialFrame.id) || initialFrame;
    }, [currentProject?.frames, initialFrame.id, initialFrame]);

    const [prompt, setPrompt] = useState(frame.image_prompt || frame.action_description || "");
    const [isGenerating, setIsGenerating] = useState(false);

    // Sync prompt when frame changes
    useEffect(() => {
        setPrompt(frame.image_prompt || frame.action_description || "");
    }, [frame.id, frame.image_prompt, frame.action_description]);

    const handleGenerate = async (batchSize: number) => {
        if (!currentProject) return;

        setIsGenerating(true);
        try {
            // Construct composition data (simplified for now, ideally passed from parent or re-calculated)
            // For re-rendering, we might want to reuse existing composition data or just rely on prompt/I2I
            // The api.renderFrame expects compositionData.
            // If we don't pass it, pipeline uses existing.

            const updatedProject = await api.renderFrame(
                currentProject.id,
                frame.id,
                null, // Use existing composition data
                prompt,
                batchSize
            );
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to generate frame:", error);
            alert("Failed to generate frame");
        } finally {
            setIsGenerating(false);
        }
    };

    const handleSelectVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.selectAssetVariant(currentProject.id, frame.id, "storyboard_frame", variantId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select variant:", error);
        }
    };

    const handleDeleteVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.deleteAssetVariant(currentProject.id, frame.id, "storyboard_frame", variantId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete variant:", error);
        }
    };

    const handleSavePrompt = async () => {
        if (!currentProject) return;
        // We can update the prompt without generating
        // But currently we don't have a specific endpoint for just updating frame prompt without render?
        // We can use updateAssetAttributes?
        // But frame is not exactly an asset in the same way.
        // Let's assume prompt is saved on generation for now.
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-md p-4 md:p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-[#1a1a1a] border border-white/10 rounded-2xl w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden shadow-2xl"
            >
                {/* Header */}
                <div className="h-16 border-b border-white/10 flex justify-between items-center px-6 bg-black/20">
                    <div className="flex items-center gap-4">
                        <h2 className="text-xl font-bold text-white">Frame Editor <span className="text-gray-500 font-normal text-sm ml-2">#{frame.id.substring(0, 8)}</span></h2>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-full text-gray-400 hover:text-white transition-colors">
                        <X size={24} />
                    </button>
                </div>

                {/* Content */}
                <div className="flex-1 flex overflow-hidden">
                    {/* Left: Variant Selector */}
                    <div className="flex-1 bg-black/40 p-4 flex flex-col overflow-hidden relative">
                        <VariantSelector
                            asset={frame.rendered_image_asset}
                            currentImageUrl={frame.rendered_image_url || frame.image_url}
                            onSelect={handleSelectVariant}
                            onDelete={handleDeleteVariant}
                            onGenerate={handleGenerate}
                            isGenerating={isGenerating}
                            aspectRatio="16:9"
                            className="h-full"
                        />
                    </div>

                    {/* Right: Controls & Prompt */}
                    <div className="w-1/3 min-w-[350px] border-l border-white/10 bg-[#111] flex flex-col">
                        <div className="p-4 border-b border-white/5">
                            <h3 className="font-bold text-sm uppercase tracking-wider text-gray-400 mb-2">
                                Scene Context
                            </h3>
                            <p className="text-xs text-gray-300 mb-2">
                                <span className="font-bold text-gray-500">Action:</span> {frame.action_description}
                            </p>
                            {frame.dialogue && (
                                <p className="text-xs text-gray-300 italic">
                                    <span className="font-bold text-gray-500 not-italic">Dialogue:</span> "{frame.dialogue}"
                                </p>
                            )}
                        </div>

                        <div className="flex-1 p-4 flex flex-col">
                            <h3 className="font-bold text-sm uppercase tracking-wider text-gray-400 mb-2">
                                Generation Prompt
                            </h3>
                            <textarea
                                value={prompt}
                                onChange={(e) => setPrompt(e.target.value)}
                                className="flex-1 w-full bg-black/20 border border-white/10 rounded-lg p-4 text-sm text-gray-300 resize-none focus:outline-none focus:border-primary/50 font-mono leading-relaxed"
                                placeholder="Enter prompt description..."
                            />
                            <p className="text-xs text-gray-500 mt-2">
                                Modify the prompt to refine the generated image.
                            </p>
                        </div>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}
