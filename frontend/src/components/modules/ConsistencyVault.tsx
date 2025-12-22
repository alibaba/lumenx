"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Paintbrush, User, MapPin, Box, Lock, Unlock, RefreshCw, Upload, Image as ImageIcon, X, Check, Settings, ChevronRight } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { api, API_URL } from "@/lib/api";
import CharacterWorkbench from "./CharacterWorkbench";
import { VariantSelector } from "../common/VariantSelector";

export default function ConsistencyVault() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    // Global Style State
    const styles = useProjectStore((state) => state.styles);
    const selectedStyleId = useProjectStore((state) => state.selectedStyleId);
    const setSelectedStyleId = useProjectStore((state) => state.setSelectedStyleId);
    const updateStylePrompt = useProjectStore((state) => state.updateStylePrompt);

    const [isEditingStyle, setIsEditingStyle] = useState(false);

    const [activeTab, setActiveTab] = useState<"character" | "scene" | "prop">("character");

    // Use global state for generation status to persist across navigation
    // Refactored to track { assetId, generationType }
    const generatingTasks = useProjectStore((state) => state.generatingTasks || []); // Fallback to empty array if not defined yet
    const addGeneratingTask = useProjectStore((state) => state.addGeneratingTask);
    const removeGeneratingTask = useProjectStore((state) => state.removeGeneratingTask);

    // Store ID and Type instead of full object to ensure reactivity
    const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
    const [selectedAssetType, setSelectedAssetType] = useState<string | null>(null);

    // Derive selected asset from currentProject
    const selectedAsset = currentProject ? (() => {
        if (!selectedAssetId || !selectedAssetType) return null;
        const list = selectedAssetType === "character" ? currentProject.characters :
            selectedAssetType === "scene" ? currentProject.scenes :
                selectedAssetType === "prop" ? currentProject.props : [];
        return list?.find((a: any) => a.id === selectedAssetId) || null;
    })() : null;

    const isAssetGenerating = (assetId: string) => {
        return generatingTasks?.some((t: any) => t.assetId === assetId);
    };

    const getAssetGeneratingTypes = (assetId: string) => {
        return generatingTasks?.filter((t: any) => t.assetId === assetId).map((t: any) => ({
            type: t.generationType,
            batchSize: t.batchSize
        })) || [];
    };

    const handleUpdateDescription = async (assetId: string, type: string, description: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.updateAssetDescription(currentProject.id, assetId, type, description);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to update description:", error);
        }
    };

    const handleGenerate = async (assetId: string, type: string, generationType: string = "all", prompt: string = "", applyStyle: boolean = true, negativePrompt: string = "", batchSize: number = 1) => {
        if (!currentProject) return;

        // Add task with specific generation type and batch size
        if (addGeneratingTask) {
            addGeneratingTask(assetId, generationType, batchSize);
        }

        try {
            const currentStyle = styles.find(s => s.id === selectedStyleId);
            const stylePrompt = currentStyle?.prompt;

            // Call API with new parameters
            const updatedProject = await api.generateAsset(
                currentProject.id,
                assetId,
                type,
                selectedStyleId,
                stylePrompt,
                generationType,
                prompt,
                applyStyle,
                negativePrompt,
                batchSize
            );

            updateProject(currentProject.id, updatedProject);
            console.log("Asset generated successfully");
        } catch (error: any) {
            console.error("Failed to generate asset:", error);
            alert(`Failed to generate asset: ${error.message}`);
        } finally {
            if (removeGeneratingTask) {
                removeGeneratingTask(assetId, generationType);
            }
        }
    };

    const assets = activeTab === "character" ? currentProject?.characters :
        activeTab === "scene" ? currentProject?.scenes :
            activeTab === "prop" ? currentProject?.props : [];

    return (
        <div className="flex flex-col h-full bg-[#111] text-white">
            {/* Header */}
            <div className="flex items-center justify-between p-6 border-b border-white/10 bg-black/20">
                <div className="flex gap-2 bg-black/40 p-1 rounded-xl border border-white/5">
                    <TabButton
                        active={activeTab === "character"}
                        onClick={() => setActiveTab("character")}
                        icon={<User size={18} />}
                        label="Characters"
                        count={currentProject?.characters?.length || 0}
                    />
                    <TabButton
                        active={activeTab === "scene"}
                        onClick={() => setActiveTab("scene")}
                        icon={<MapPin size={18} />}
                        label="Scenes"
                        count={currentProject?.scenes?.length || 0}
                    />
                    <TabButton
                        active={activeTab === "prop"}
                        onClick={() => setActiveTab("prop")}
                        icon={<Box size={18} />}
                        label="Props"
                        count={currentProject?.props?.length || 0}
                    />
                </div>

                <button
                    onClick={() => setIsEditingStyle(true)}
                    className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg transition-colors"
                >
                    <Paintbrush size={16} className="text-primary" />
                    <span className="text-sm font-bold">Global Style</span>
                </button>
            </div>

            {/* Content Grid */}
            <div className="flex-1 overflow-y-auto p-6">
                {!currentProject ? (
                    <div className="flex items-center justify-center h-full text-gray-500">
                        Loading project...
                    </div>
                ) : assets?.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-gray-500 gap-4">
                        <div className="w-16 h-16 rounded-full bg-white/5 flex items-center justify-center">
                            {activeTab === "character" ? <User size={32} /> : activeTab === "scene" ? <MapPin size={32} /> : <Box size={32} />}
                        </div>
                        <p>No {activeTab}s found</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                        {assets?.map((asset: any) => (
                            <AssetCard
                                key={asset.id}
                                asset={asset}
                                type={activeTab}
                                isGenerating={isAssetGenerating(asset.id)}
                                onGenerate={() => handleGenerate(asset.id, activeTab)}
                                onToggleLock={() => api.toggleAssetLock(currentProject.id, asset.id, activeTab).then(updated => updateProject(currentProject.id, updated))}
                                onClick={() => {
                                    setSelectedAssetId(asset.id);
                                    setSelectedAssetType(activeTab);
                                }}
                            />
                        ))}
                    </div>
                )}
            </div>

            {/* Detail Modal / Workbench */}
            <AnimatePresence>
                {selectedAsset && selectedAssetId && selectedAssetType && (
                    selectedAssetType === "character" ? (
                        <CharacterWorkbench
                            asset={selectedAsset}
                            onClose={() => {
                                setSelectedAssetId(null);
                                setSelectedAssetType(null);
                            }}
                            onUpdateDescription={(desc: string) => handleUpdateDescription(selectedAssetId, selectedAssetType, desc)}
                            onGenerate={(type: string, prompt: string, applyStyle: boolean, negativePrompt: string, batchSize: number) => handleGenerate(selectedAssetId, selectedAssetType, type, prompt, applyStyle, negativePrompt, batchSize)}
                            generatingTypes={getAssetGeneratingTypes(selectedAssetId)}
                            stylePrompt={currentProject?.art_direction?.style_config?.positive_prompt || styles.find(s => s.id === selectedStyleId)?.prompt || ""}
                            styleNegativePrompt={currentProject?.art_direction?.style_config?.negative_prompt || styles.find(s => s.id === selectedStyleId)?.negative_prompt || ""}
                        />
                    ) : (
                        <CharacterDetailModal
                            asset={selectedAsset}
                            type={selectedAssetType}
                            onClose={() => {
                                setSelectedAssetId(null);
                                setSelectedAssetType(null);
                            }}
                            onUpdateDescription={(desc: string) => handleUpdateDescription(selectedAssetId, selectedAssetType, desc)}
                            onGenerate={(applyStyle: boolean, negativePrompt: string, batchSize: number) => handleGenerate(selectedAssetId, selectedAssetType, "all", "", applyStyle, negativePrompt, batchSize)}
                            isGenerating={isAssetGenerating(selectedAssetId)}
                            stylePrompt={currentProject?.art_direction?.style_config?.positive_prompt || styles.find(s => s.id === selectedStyleId)?.prompt || ""}
                            styleNegativePrompt={currentProject?.art_direction?.style_config?.negative_prompt || styles.find(s => s.id === selectedStyleId)?.negative_prompt || ""}
                        />
                    )
                )}
            </AnimatePresence>

            {/* Style Editor Modal */}
            <AnimatePresence>
                {
                    isEditingStyle && (
                        <StyleEditorModal
                            styles={styles}
                            selectedStyleId={selectedStyleId}
                            onClose={() => setIsEditingStyle(false)}
                            onUpdate={(styleId: string, prompt: string) => {
                                updateStylePrompt(styleId, prompt);
                                setIsEditingStyle(false);
                            }}
                        />
                    )
                }
            </AnimatePresence>
        </div >
    );
}

function CharacterDetailModal({ asset, type, onClose, onUpdateDescription, onGenerate, isGenerating, stylePrompt = "", styleNegativePrompt = "" }: any) {
    const [description, setDescription] = useState(asset.description);
    const [isEditing, setIsEditing] = useState(false);
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    // Style Controls
    const [applyStyle, setApplyStyle] = useState(true);
    const [negativePrompt, setNegativePrompt] = useState(styleNegativePrompt || "low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry");
    const [showAdvanced, setShowAdvanced] = useState(false);

    // Sync local state if asset changes
    useEffect(() => {
        setDescription(asset.description);
    }, [asset.description]);

    // Sync negative prompt if style changes
    useEffect(() => {
        if (styleNegativePrompt && (!negativePrompt || negativePrompt.includes("low quality"))) {
            setNegativePrompt(styleNegativePrompt);
        }
    }, [styleNegativePrompt]);

    const handleSave = () => {
        onUpdateDescription(description);
        setIsEditing(false);
    };

    const handleSelectVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.selectAssetVariant(currentProject.id, asset.id, type, variantId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to select variant:", error);
        }
    };

    const handleDeleteVariant = async (variantId: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.deleteAssetVariant(currentProject.id, asset.id, type, variantId);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete variant:", error);
        }
    };

    const handleGenerateClick = (batchSize: number) => {
        onGenerate(applyStyle, negativePrompt, batchSize);
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-[#1a1a1a] border border-white/10 rounded-2xl w-full max-w-5xl h-[85vh] flex overflow-hidden shadow-2xl"
            >
                {/* Left: Variant Selector */}
                <div className="w-1/2 bg-black/40 relative border-r border-white/10 p-4 flex flex-col overflow-hidden">
                    <VariantSelector
                        asset={asset.image_asset}
                        currentImageUrl={asset.image_url}
                        onSelect={handleSelectVariant}
                        onDelete={handleDeleteVariant}
                        onGenerate={handleGenerateClick}
                        isGenerating={isGenerating}
                        aspectRatio="16:9"
                        className="h-full"
                    />
                </div>

                {/* Right: Details */}
                <div className="w-1/2 flex flex-col">
                    {/* Header */}
                    <div className="p-6 border-b border-white/10 flex justify-between items-center bg-black/20">
                        <h2 className="text-2xl font-bold text-white">{asset.name}</h2>
                        <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-full text-gray-400 hover:text-white">
                            <X size={24} />
                        </button>
                    </div>

                    {/* Content */}
                    <div className="flex-1 p-6 overflow-y-auto space-y-6">
                        {/* Description */}
                        <div className="space-y-2">
                            <div className="flex justify-between items-center">
                                <label className="text-sm font-bold text-gray-400 uppercase">Description</label>
                                {!isEditing && (
                                    <button onClick={() => setIsEditing(true)} className="text-xs text-primary hover:underline">
                                        Edit
                                    </button>
                                )}
                            </div>
                            {isEditing ? (
                                <div className="space-y-2">
                                    <textarea
                                        value={description}
                                        onChange={(e) => setDescription(e.target.value)}
                                        className="w-full h-32 bg-black/20 border border-white/10 rounded-lg p-3 text-sm text-gray-300 resize-none focus:border-primary focus:outline-none"
                                    />
                                    <div className="flex justify-end gap-2">
                                        <button onClick={() => { setIsEditing(false); setDescription(asset.description); }} className="px-3 py-1.5 text-xs text-gray-400 hover:text-white">Cancel</button>
                                        <button onClick={handleSave} className="px-3 py-1.5 bg-primary text-white text-xs rounded hover:bg-primary/90">Save Description</button>
                                    </div>
                                </div>
                            ) : (
                                <p className="text-sm text-gray-300 leading-relaxed bg-white/5 p-3 rounded-lg border border-transparent hover:border-white/10 transition-colors">
                                    {asset.description}
                                </p>
                            )}
                        </div>

                        {/* Style Control */}
                        <div className="space-y-2">
                            <label className="text-sm font-bold text-gray-400 uppercase">Style Settings</label>
                            <div className="bg-white/5 rounded-lg p-3 border border-white/5">
                                <div className="flex items-center gap-2 mb-2">
                                    <input
                                        type="checkbox"
                                        id="applyStyleModal"
                                        checked={applyStyle}
                                        onChange={(e) => setApplyStyle(e.target.checked)}
                                        className="rounded border-gray-600 bg-gray-700 text-primary focus:ring-primary"
                                    />
                                    <label htmlFor="applyStyleModal" className="text-sm font-bold text-gray-300 cursor-pointer select-none">
                                        Apply Global Style
                                    </label>
                                </div>

                                {stylePrompt && (
                                    <div className="text-xs text-gray-500 font-mono bg-black/20 p-2 rounded border border-white/5">
                                        <span className="text-primary font-bold">Style:</span> {stylePrompt}
                                    </div>
                                )}
                            </div>
                        </div>

                        {/* Advanced Settings (Negative Prompt) */}
                        <div className="space-y-2">
                            <button
                                onClick={() => setShowAdvanced(!showAdvanced)}
                                className="flex items-center gap-2 text-xs font-bold text-gray-500 hover:text-white transition-colors uppercase"
                            >
                                <span>Advanced Settings (Negative Prompt)</span>
                                <ChevronRight size={12} className={`transform transition-transform ${showAdvanced ? 'rotate-90' : ''}`} />
                            </button>

                            <AnimatePresence>
                                {showAdvanced && (
                                    <motion.div
                                        initial={{ height: 0, opacity: 0 }}
                                        animate={{ height: "auto", opacity: 1 }}
                                        exit={{ height: 0, opacity: 0 }}
                                        className="overflow-hidden"
                                    >
                                        <textarea
                                            value={negativePrompt}
                                            onChange={(e) => setNegativePrompt(e.target.value)}
                                            className="w-full h-24 bg-black/20 border border-white/10 rounded-lg p-3 text-xs text-gray-400 resize-none focus:outline-none focus:border-primary/50 font-mono"
                                            placeholder="Enter negative prompt..."
                                        />
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>
                    </div>

                    {/* Footer Actions */}
                    <div className="p-6 border-t border-white/10 bg-black/20 flex gap-4">
                        <button
                            onClick={onClose}
                            className="flex-1 py-3 bg-green-600 hover:bg-green-500 text-white rounded-xl font-bold flex items-center justify-center gap-2 shadow-lg shadow-green-900/20"
                        >
                            <Check size={18} />
                            Done
                        </button>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}

function TabButton({ active, onClick, icon, label, count }: any) {
    return (
        <button
            onClick={onClick}
            className={`flex items-center justify-between w-full p-3 rounded-xl transition-all ${active
                ? "bg-white/10 text-white border border-white/10 shadow-sm"
                : "text-gray-500 hover:bg-white/5 hover:text-gray-300"
                }`}
        >
            <div className="flex items-center gap-3">
                {icon}
                <span className="font-bold text-sm">{label}</span>
            </div>
            <span className="text-xs bg-black/30 px-2 py-0.5 rounded-full">{count}</span>
        </button>
    );
}

function ImageWithRetry({ src, alt, className }: { src: string, alt: string, className?: string }) {
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(false);
    const [retryCount, setRetryCount] = useState(0);

    // Reset state when src changes
    useEffect(() => {
        setIsLoading(true);
        setError(false);
        setRetryCount(0);
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
                src={displaySrc}
                alt={alt}
                className={`${className} ${isLoading ? 'opacity-0' : 'opacity-100'} transition-opacity duration-300`}
                onLoad={() => setIsLoading(false)}
                onError={() => {
                    setError(true);
                    setIsLoading(true); // Keep showing loader while retrying
                }}
            />
            {error && retryCount >= 10 && (
                <div className="absolute inset-0 flex items-center justify-center bg-red-500/10 backdrop-blur-sm z-20">
                    <span className="text-xs text-red-400 font-bold">Failed to load</span>
                </div>
            )}
        </div>
    );
}

function AssetCard({ asset, type, isGenerating, onGenerate, onToggleLock, onClick }: any) {
    const isLocked = asset.locked || false;
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file || !currentProject) return;

        try {
            // 1. Upload file
            const { url } = await api.uploadFile(file);

            // 2. Update asset image
            const updatedProject = await api.updateAssetImage(currentProject.id, asset.id, type, url);

            // 3. Update local state
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to upload asset image:", error);
            alert("Failed to upload image");
        }
    };

    const imageUrl = (type === 'character' ? (asset.avatar_url || asset.image_url) : asset.image_url);
    const fullImageUrl = imageUrl?.startsWith("http") ? imageUrl : `${API_URL}/files/${imageUrl}`;

    return (
        <motion.div
            layout
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            onClick={onClick}
            className={`group relative aspect-[3/4] bg-black/40 rounded-2xl border overflow-hidden transition-colors cursor-pointer ${isLocked ? 'border-yellow-500/50' : 'border-white/10 hover:border-primary/50'
                }`}
        >
            {/* Image Area */}
            <div className="absolute inset-0 bg-gradient-to-b from-transparent to-black/80 z-10" />

            {imageUrl ? (
                <ImageWithRetry
                    src={fullImageUrl}
                    alt={asset.name}
                    className="w-full h-full object-cover"
                />
            ) : (
                <div className="w-full h-full flex items-center justify-center bg-white/5">
                    <ImageIcon className="text-white/10" size={48} />
                </div>
            )}

            {/* Loading Overlay */}
            {isGenerating && (
                <div className="absolute inset-0 z-20 bg-black/60 backdrop-blur-sm flex items-center justify-center flex-col gap-2">
                    <RefreshCw className="animate-spin text-primary" size={32} />
                    <span className="text-xs font-mono text-primary">Generating...</span>
                </div>
            )}

            {/* Top Actions Overlay */}
            <div className="absolute top-2 right-2 z-30 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        onToggleLock();
                    }}
                    className={`p-2 rounded-full backdrop-blur-md transition-colors ${isLocked
                        ? "bg-yellow-500/20 text-yellow-500 hover:bg-yellow-500/30"
                        : "bg-black/40 text-white hover:bg-white/20"
                        }`}
                >
                    {isLocked ? <Lock size={14} /> : <Unlock size={14} />}
                </button>
            </div>

            {/* Bottom Info */}
            <div className="absolute bottom-0 left-0 right-0 p-4 z-30">
                <h3 className="text-lg font-bold text-white mb-1 truncate">{asset.name}</h3>
                <p className="text-xs text-gray-400 line-clamp-2 mb-3 h-8">
                    {asset.description || "No description"}
                </p>

                <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity transform translate-y-2 group-hover:translate-y-0">
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            onGenerate();
                        }}
                        disabled={isLocked || isGenerating}
                        className={`flex-1 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-2 transition-colors ${isLocked
                            ? 'bg-gray-600 text-gray-400 cursor-not-allowed'
                            : 'bg-primary hover:bg-primary/90 text-white shadow-lg shadow-primary/20'
                            }`}
                    >
                        <RefreshCw size={14} className={isGenerating ? "animate-spin" : ""} />
                        {isGenerating ? "Generating..." : "Generate"}
                    </button>
                    <label
                        onClick={(e) => e.stopPropagation()}
                        className="p-2 bg-white/10 hover:bg-white/20 rounded-lg text-white cursor-pointer transition-colors"
                    >
                        <Upload size={14} />
                        <input type="file" className="hidden" accept="image/*" onChange={handleUpload} />
                    </label>
                </div>
            </div>
        </motion.div>
    );
}

function StyleEditorModal({ styles, selectedStyleId, onClose, onUpdate }: any) {
    const style = styles.find((s: any) => s.id === selectedStyleId);
    const [prompt, setPrompt] = useState(style?.prompt || "");

    useEffect(() => {
        setPrompt(style?.prompt || "");
    }, [style]);

    const handleSave = () => {
        onUpdate(selectedStyleId, prompt);
        onClose();
    };

    return (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-[#1a1a1a] border border-white/10 rounded-2xl w-full max-w-2xl overflow-hidden shadow-2xl flex flex-col"
            >
                <div className="p-6 border-b border-white/10 flex justify-between items-center bg-black/20">
                    <div className="flex items-center gap-3">
                        <Paintbrush className="text-primary" size={20} />
                        <div>
                            <h2 className="text-xl font-bold text-white">Global Style Settings</h2>
                            <p className="text-xs text-gray-400">Define the visual style for all assets</p>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-full text-gray-400 hover:text-white">
                        <X size={20} />
                    </button>
                </div>

                <div className="p-6 space-y-6">
                    <div>
                        <label className="text-xs font-bold text-gray-500 uppercase mb-2 block">Selected Style</label>
                        <div className={`text-sm font-bold text-white bg-gradient-to-r ${style?.color} p-3 rounded-lg border border-white/10 shadow-lg inline-block`}>
                            {style?.name}
                        </div>
                    </div>

                    <div className="space-y-2">
                        <label className="text-xs font-bold text-gray-500 uppercase block">Style Prompt</label>
                        <textarea
                            value={prompt}
                            onChange={(e) => setPrompt(e.target.value)}
                            className="w-full h-48 bg-black/40 border border-white/10 rounded-xl p-4 text-sm text-gray-300 resize-none focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/50 transition-all"
                            placeholder="Describe the global style (e.g., lighting, art style, atmosphere)..."
                        />
                        <p className="text-xs text-gray-500">
                            This prompt will be appended to all asset generation prompts to ensure consistency.
                        </p>
                    </div>
                </div>

                <div className="p-6 border-t border-white/10 bg-black/20 flex justify-end gap-3">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-sm font-medium text-gray-400 hover:text-white transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSave}
                        className="px-6 py-2 bg-primary hover:bg-primary/90 text-white text-sm font-bold rounded-lg shadow-lg shadow-primary/20 transition-all"
                    >
                        Save Changes
                    </button>
                </div>
            </motion.div>
        </div>
    );
}
