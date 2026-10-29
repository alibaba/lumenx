"use client";

import { useState, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, RefreshCw, Check, AlertTriangle, Image as ImageIcon, Lock, Unlock, ChevronRight, Maximize2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { api, API_URL } from "@/lib/api";
import { VariantSelector } from "../common/VariantSelector";
import { useProjectStore } from "@/store/projectStore";
import { confirmProjectAck, confirmRawProjectAck, ProjectReadbackError } from "@/lib/projectReadback";

interface StoryboardFrameEditorProps {
    frame: any;
    onClose: () => void;
}

export default function StoryboardFrameEditor({ frame: initialFrame, onClose }: StoryboardFrameEditorProps) {
    const ts = useTranslations("storyboard");
    const feedback = useTranslations("castWorkbench");
    const currentProject = useProjectStore(state => state.currentProject);

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
            await confirmRawProjectAck(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to generate frame:", error);
            alert(error instanceof ProjectReadbackError ? feedback("toastReadbackFailed") : ts("generateFailed"));
        } finally {
            setIsGenerating(false);
        }
    };

    const handleSelectVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.selectAssetVariant(currentProject.id, frame.id, "storyboard_frame", variantId);
            await confirmProjectAck(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select variant:", error);
            alert(feedback(error instanceof ProjectReadbackError ? "toastReadbackFailed" : "toastSelectErr"));
        }
    };

    const handleDeleteVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.deleteAssetVariant(currentProject.id, frame.id, "storyboard_frame", variantId);
            await confirmProjectAck(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete variant:", error);
            alert(feedback(error instanceof ProjectReadbackError ? "toastReadbackFailed" : "toastSelectErr"));
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
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-md p-4 md:p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-elevated border border-glass-border rounded-2xl w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden shadow-lg"
            >
                {/* Header */}
                <div className="h-16 border-b border-glass-border flex justify-between items-center px-6 bg-surface">
                    <div className="flex items-center gap-4">
                        <h2 className="text-xl font-bold text-foreground">{ts("frameEditor")} <span className="text-text-muted font-normal text-sm ml-2">#{frame.id.substring(0, 8)}</span></h2>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-hover-bg rounded-full text-text-secondary hover:text-foreground transition-colors">
                        <X size={24} />
                    </button>
                </div>

                {/* Content */}
                <div className="flex-1 flex overflow-hidden">
                    {/* Left: Variant Selector */}
                    <div className="flex-1 bg-surface p-4 flex flex-col overflow-hidden relative">
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
                    <div className="w-1/3 min-w-[350px] border-l border-glass-border bg-elevated flex flex-col">
                        <div className="p-4 border-b border-border-subtle">
                            <h3 className="font-bold text-sm uppercase tracking-wider text-text-secondary mb-2">
                                {ts("sceneContext")}
                            </h3>
                            <p className="text-xs text-text-secondary mb-2">
                                <span className="font-bold text-text-muted">{ts("action")}:</span> {frame.action_description}
                            </p>
                            {frame.dialogue && (
                                <p className="text-xs text-text-secondary italic">
                                    <span className="font-bold text-text-muted not-italic">{ts("dialogue")}:</span> "{frame.dialogue}"
                                </p>
                            )}
                        </div>

                        <div className="flex-1 p-4 flex flex-col">
                            <h3 className="font-bold text-sm uppercase tracking-wider text-text-secondary mb-2">
                                {ts("generationPrompt")}
                            </h3>
                            <textarea
                                value={prompt}
                                onChange={(e) => setPrompt(e.target.value)}
                                className="flex-1 w-full bg-surface border border-glass-border rounded-lg p-4 text-sm text-text-secondary resize-none focus:outline-none focus:border-primary/50 font-mono leading-relaxed"
                                placeholder={ts("promptPlaceholder")}
                            />
                            <p className="text-xs text-text-muted mt-2">
                                {ts("promptHint")}
                            </p>
                        </div>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}
