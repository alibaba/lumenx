"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, RefreshCw, Check, AlertTriangle, Image as ImageIcon, Lock, Unlock, ChevronRight, Maximize2 } from "lucide-react";
import { api, API_URL } from "@/lib/api";

import { VariantSelector } from "../common/VariantSelector";
import { useProjectStore } from "@/store/projectStore";

interface CharacterWorkbenchProps {
    asset: any;
    onClose: () => void;
    onUpdateDescription: (desc: string) => void;
    onGenerate: (type: string, prompt: string, applyStyle: boolean, negativePrompt: string, batchSize: number) => void;
    generatingTypes: { type: string; batchSize: number }[];
    stylePrompt?: string;
    styleNegativePrompt?: string;
}

export default function CharacterWorkbench({ asset, onClose, onUpdateDescription, onGenerate, generatingTypes = [], stylePrompt = "", styleNegativePrompt = "" }: CharacterWorkbenchProps) {
    const [activePanel, setActivePanel] = useState<"full_body" | "three_view" | "headshot">("full_body");
    const updateProject = useProjectStore(state => state.updateProject);
    const currentProject = useProjectStore(state => state.currentProject);

    // Local state for prompts
    const [fullBodyPrompt, setFullBodyPrompt] = useState(asset.full_body_prompt || "");
    const [threeViewPrompt, setThreeViewPrompt] = useState(asset.three_view_prompt || "");
    const [headshotPrompt, setHeadshotPrompt] = useState(asset.headshot_prompt || "");

    // New State for Style Control
    const [applyStyle, setApplyStyle] = useState(true);
    // User's own negative prompt (initially empty or with sensible defaults)
    const [negativePrompt, setNegativePrompt] = useState("low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, jpeg artifacts, signature, watermark, blurry");
    // Art Direction Style expanded state (collapsed by default to save space)
    const [showStyleExpanded, setShowStyleExpanded] = useState(false);

    // Initialize prompts if empty (first time load)
    useEffect(() => {
        if (!fullBodyPrompt) {
            setFullBodyPrompt(`Full body character design of ${asset.name}, concept art. ${asset.description}. Standing pose, neutral expression, no emotion, looking at viewer. Clean white background, isolated, no other objects, no scenery, simple background, high quality, masterpiece.`);
        }
        if (!threeViewPrompt) {
            setThreeViewPrompt(`Character Reference Sheet for ${asset.name}. ${asset.description}. Three-view character design: Front view, Side view, and Back view. Full body, standing pose, neutral expression. Consistent clothing and details across all views. Simple white background.`);
        }
        if (!headshotPrompt) {
            setHeadshotPrompt(`Close-up portrait of the SAME character ${asset.name}. ${asset.description}. Zoom in on face and shoulders, detailed facial features, neutral expression, looking at viewer, high quality, masterpiece.`);
        }
    }, [asset.name, asset.description]);

    // Update local state when asset updates (e.g. after generation)
    useEffect(() => {
        if (asset.full_body_prompt) setFullBodyPrompt(asset.full_body_prompt);
        if (asset.three_view_prompt) setThreeViewPrompt(asset.three_view_prompt);
        if (asset.headshot_prompt) setHeadshotPrompt(asset.headshot_prompt);
    }, [asset]);

    const handleGenerateClick = (type: "full_body" | "three_view" | "headshot", batchSize: number) => {
        let prompt = "";
        if (type === "full_body") prompt = fullBodyPrompt;
        else if (type === "three_view") prompt = threeViewPrompt;
        else if (type === "headshot") prompt = headshotPrompt;

        onGenerate(type, prompt, applyStyle, negativePrompt, batchSize);
    };

    // Helper to get image URL
    const getImageUrl = (url: string) => {
        if (!url) return null;
        return url.startsWith("http") ? url : `${API_URL}/files/${url}`;
    };

    // Helper to check if a specific type is generating
    const getGeneratingInfo = (type: string) => {
        console.log("[DEBUG] generatingTypes:", generatingTypes);
        // Safety check: ensure generatingTypes is an array
        if (!Array.isArray(generatingTypes) || generatingTypes.length === 0) {
            return { isGenerating: false, batchSize: 1 };
        }
        const task = generatingTypes.find(t => t?.type === type || t?.type === "all");
        console.log("[DEBUG] found task for type", type, ":", task);
        return task ? { isGenerating: true, batchSize: task.batchSize || 1 } : { isGenerating: false, batchSize: 1 };
    };

    // Handlers for Variant Selection/Deletion
    // Note: In a real app, these would call API endpoints. 
    // For this prototype, we'll update the local store state directly if possible, or just mock it.
    // Since we don't have API endpoints for select/delete yet, we'll assume the parent component or store handles it,
    // OR we implement local state manipulation here.
    // Ideally, we should add select/delete actions to the store.

    const handleSelectVariant = async (type: "full_body" | "three_view" | "headshot", variantId: string) => {
        if (!currentProject) return;

        try {
            const updatedProject = await api.selectAssetVariant(currentProject.id, asset.id, "character", variantId, type);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select variant:", error);
        }
    };

    const handleDeleteVariant = async (type: "full_body" | "three_view" | "headshot", variantId: string) => {
        if (!currentProject) return;

        try {
            const updatedProject = await api.deleteAssetVariant(currentProject.id, asset.id, "character", variantId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete variant:", error);
        }
    };

    const handleFavoriteVariant = async (type: "full_body" | "three_view" | "headshot", variantId: string, isFavorited: boolean) => {
        if (!currentProject) return;

        try {
            const updatedProject = await api.favoriteAssetVariant(currentProject.id, asset.id, "character", variantId, isFavorited, type);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to favorite variant:", error);
        }
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-md p-4 md:p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-[#1a1a1a] border border-white/10 rounded-2xl w-full max-w-7xl h-[90vh] flex flex-col overflow-hidden shadow-2xl"
            >
                <div className="h-16 border-b border-white/10 flex justify-between items-center px-6 bg-black/20">
                    <div className="flex items-center gap-4">
                        <h2 className="text-xl font-bold text-white">{asset.name} <span className="text-gray-500 font-normal text-sm ml-2">Character Workbench</span></h2>
                        <div className="flex items-center gap-2 px-3 py-1 bg-blue-500/10 border border-blue-500/20 rounded-full">
                            <span className="text-xs text-blue-400 font-medium">💡 Tip: Keep the three images consistent for best results</span>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-full text-gray-400 hover:text-white transition-colors">
                        <X size={24} />
                    </button>
                </div>

                {/* Main Content - 3 Columns */}
                <div className="flex-1 flex overflow-hidden">

                    {/* Panel 1: Full Body (Master) */}
                    <WorkbenchPanel
                        title="1. Master Asset (Full Body)"
                        isActive={activePanel === "full_body"}
                        onClick={() => setActivePanel("full_body")}

                        // Variant Props - use backend field names
                        asset={asset.full_body_asset}
                        currentImageUrl={asset.full_body_image_url}
                        onSelect={(id: string) => handleSelectVariant("full_body", id)}
                        onDelete={(id: string) => handleDeleteVariant("full_body", id)}
                        onFavorite={(id: string, isFav: boolean) => handleFavoriteVariant("full_body", id, isFav)}

                        prompt={fullBodyPrompt}
                        setPrompt={setFullBodyPrompt}
                        onGenerate={(batchSize: number) => handleGenerateClick("full_body", batchSize)}
                        isGenerating={getGeneratingInfo("full_body").isGenerating}
                        generatingBatchSize={getGeneratingInfo("full_body").batchSize}
                        description="The primary reference for character consistency."
                        aspectRatio="9:16"
                    />

                    {/* Divider */}
                    <div className="w-px bg-white/10 flex items-center justify-center">
                        <ChevronRight size={16} className="text-gray-600" />
                    </div>

                    {/* Panel 2: Three View (Derived) */}
                    <WorkbenchPanel
                        title="2. Three-Views"
                        isActive={activePanel === "three_view"}
                        onClick={() => setActivePanel("three_view")}

                        // Variant Props - use backend field names
                        asset={asset.three_view_asset}
                        currentImageUrl={asset.three_view_image_url}
                        onSelect={(id: string) => handleSelectVariant("three_view", id)}
                        onDelete={(id: string) => handleDeleteVariant("three_view", id)}
                        onFavorite={(id: string, isFav: boolean) => handleFavoriteVariant("three_view", id, isFav)}

                        prompt={threeViewPrompt}
                        setPrompt={setThreeViewPrompt}
                        onGenerate={(batchSize: number) => handleGenerateClick("three_view", batchSize)}
                        isGenerating={getGeneratingInfo("three_view").isGenerating}
                        generatingBatchSize={getGeneratingInfo("three_view").batchSize}
                        isLocked={!asset.full_body_image_url}
                        description="Front, side, and back views for 3D-like consistency."
                        aspectRatio="16:9"
                    />

                    {/* Divider */}
                    <div className="w-px bg-white/10 flex items-center justify-center">
                        <ChevronRight size={16} className="text-gray-600" />
                    </div>

                    {/* Panel 3: Headshot (Derived) */}
                    <WorkbenchPanel
                        title="3. Avatar (Headshot)"
                        isActive={activePanel === "headshot"}
                        onClick={() => setActivePanel("headshot")}

                        // Variant Props - use backend field names
                        asset={asset.headshot_asset}
                        currentImageUrl={asset.headshot_image_url || asset.avatar_url}
                        onSelect={(id: string) => handleSelectVariant("headshot", id)}
                        onDelete={(id: string) => handleDeleteVariant("headshot", id)}
                        onFavorite={(id: string, isFav: boolean) => handleFavoriteVariant("headshot", id, isFav)}

                        prompt={headshotPrompt}
                        setPrompt={setHeadshotPrompt}
                        onGenerate={(batchSize: number) => handleGenerateClick("headshot", batchSize)}
                        isGenerating={getGeneratingInfo("headshot").isGenerating}
                        generatingBatchSize={getGeneratingInfo("headshot").batchSize}
                        isLocked={!asset.full_body_image_url}
                        description="Close-up facial details and expressions."
                        aspectRatio="1:1"
                    />

                </div>

                {/* Footer: Negative Prompt & Art Direction Settings */}
                <div className="border-t border-white/10 bg-[#111] flex flex-col">
                    {/* Top Row: User's Negative Prompt + Apply Style Toggle */}
                    <div className="px-6 py-3 flex items-start gap-4">
                        {/* User's Negative Prompt (Editable) */}
                        <div className="flex-1">
                            <label className="text-xs font-bold text-gray-500 uppercase mb-2 block">Your Negative Prompt</label>
                            <textarea
                                value={negativePrompt}
                                onChange={(e) => setNegativePrompt(e.target.value)}
                                className="w-full h-16 bg-black/40 border border-white/10 rounded-lg p-3 text-xs text-gray-300 resize-none focus:outline-none focus:border-primary/50 font-mono"
                                placeholder="Enter your negative prompt (avoid unwanted elements)..."
                            />
                        </div>

                        {/* Apply Style Toggle */}
                        <div className="pt-6">
                            <div className="flex items-center gap-2 bg-black/40 px-4 py-2 rounded-lg border border-white/10">
                                <input
                                    type="checkbox"
                                    id="applyStyleFooter"
                                    checked={applyStyle}
                                    onChange={(e) => setApplyStyle(e.target.checked)}
                                    className="rounded border-gray-600 bg-gray-700 text-primary focus:ring-primary w-4 h-4"
                                />
                                <label htmlFor="applyStyleFooter" className="text-xs font-bold text-gray-300 cursor-pointer select-none whitespace-nowrap">
                                    Apply Art Direction Style
                                </label>
                            </div>
                        </div>
                    </div>

                    {/* Art Direction Style Display (Collapsible) - Only show toggle when style exists */}
                    {applyStyle && (stylePrompt || styleNegativePrompt) && (
                        <div className="border-t border-white/5">
                            <button
                                onClick={() => setShowStyleExpanded(!showStyleExpanded)}
                                className="w-full px-6 py-2 flex items-center justify-between hover:bg-white/5 transition-colors"
                            >
                                <div className="flex items-center gap-2">
                                    <div className="w-2 h-2 rounded-full bg-gradient-to-r from-purple-500 to-blue-500" />
                                    <span className="text-xs font-bold text-gray-400 uppercase">Art Direction Style (Will Be Appended)</span>
                                </div>
                                <ChevronRight size={14} className={`text-gray-500 transform transition-transform ${showStyleExpanded ? 'rotate-90' : ''}`} />
                            </button>

                            <AnimatePresence>
                                {showStyleExpanded && (
                                    <motion.div
                                        initial={{ height: 0, opacity: 0 }}
                                        animate={{ height: "auto", opacity: 1 }}
                                        exit={{ height: 0, opacity: 0 }}
                                        className="overflow-hidden"
                                    >
                                        <div className="px-6 pb-4">
                                            <div className="bg-gradient-to-r from-purple-500/10 to-blue-500/10 border border-white/10 rounded-lg p-4">
                                                {stylePrompt && (
                                                    <div className="mb-3">
                                                        <span className="text-xs font-bold text-green-400 block mb-1">+ Style Prompt:</span>
                                                        <p className="text-xs text-gray-400 font-mono bg-black/20 p-2 rounded border border-white/5 leading-relaxed">
                                                            {stylePrompt}
                                                        </p>
                                                    </div>
                                                )}

                                                {styleNegativePrompt && (
                                                    <div>
                                                        <span className="text-xs font-bold text-red-400 block mb-1">+ Negative Prompt:</span>
                                                        <p className="text-xs text-gray-400 font-mono bg-black/20 p-2 rounded border border-white/5 leading-relaxed">
                                                            {styleNegativePrompt}
                                                        </p>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>
                    )}
                </div>
            </motion.div>
        </div>
    );
}

function WorkbenchPanel({
    title,
    isActive,
    onClick,

    // Variant Props
    asset,
    currentImageUrl,
    onSelect,
    onDelete,
    onFavorite,

    prompt,
    setPrompt,
    onGenerate,
    isGenerating,
    generatingBatchSize,
    status,
    isLocked,
    description,
    aspectRatio = "9:16"
}: any) {
    return (
        <div
            className={`flex-1 flex flex-col min-w-[300px] transition-colors ${isActive ? 'bg-white/5' : 'bg-transparent hover:bg-white/[0.02]'}`}
            onClick={onClick}
        >
            {/* Panel Header */}
            <div className="p-4 border-b border-white/5">
                <h3 className={`font-bold text-sm uppercase tracking-wider mb-1 ${isActive ? 'text-primary' : 'text-gray-400'}`}>
                    {title}
                </h3>
                <p className="text-xs text-gray-500">{description}</p>
            </div>

            {/* Image Area with Variant Selector */}
            <div className="flex-1 relative bg-black/40 p-4 flex flex-col overflow-hidden group">

                {/* Locked Overlay */}
                {isLocked && (
                    <div className="absolute inset-0 bg-black/80 z-20 flex items-center justify-center text-center p-6">
                        <div className="text-gray-500 flex flex-col items-center gap-2">
                            <Lock size={32} />
                            <span className="text-sm">Generate Master Asset first</span>
                        </div>
                    </div>
                )}

                {/* Variant Selector */}
                <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-700">
                    <VariantSelector
                        asset={asset}
                        currentImageUrl={currentImageUrl}
                        onSelect={onSelect}
                        onDelete={onDelete}
                        onFavorite={onFavorite}
                        onGenerate={onGenerate}
                        isGenerating={isGenerating}
                        generatingBatchSize={generatingBatchSize}
                        aspectRatio={aspectRatio}
                        className="h-full"
                    />
                </div>

                {/* Status Overlay (if outdated) */}
                {status === "outdated" && !isGenerating && (
                    <div className="absolute top-4 right-4 z-10">
                        <div className="bg-yellow-500/20 border border-yellow-500/50 px-3 py-1 rounded-lg flex items-center gap-2 backdrop-blur-sm">
                            <RefreshCw size={12} className="text-yellow-500" />
                            <span className="text-xs font-bold text-yellow-500">Update Recommended</span>
                        </div>
                    </div>
                )}
            </div>

            {/* Prompt Editor (Bottom) */}
            <div className="h-1/3 border-t border-white/10 flex flex-col bg-[#111]">
                <div className="p-2 border-b border-white/5 flex justify-between items-center bg-black/20">
                    <span className="text-xs font-bold text-gray-500 uppercase px-2">Prompt</span>
                </div>
                <textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    disabled={isLocked}
                    className="flex-1 w-full bg-transparent p-4 text-xs text-gray-300 resize-none focus:outline-none focus:bg-white/5 font-mono leading-relaxed"
                    placeholder="Enter prompt description..."
                />
            </div>
        </div>
    );
}
