"use client";

import { useState, useEffect } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { Paintbrush, User, Users, MapPin, Box, Lock, Unlock, RefreshCw, Upload, Image as ImageIcon, X, Check, Settings, ChevronRight, Trash2, Plus, Link as LinkIcon } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { api, API_URL, crudApi } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";
import CharacterWorkbench from "./CharacterWorkbench";
import { VariantSelector } from "../common/VariantSelector";
import { VideoVariantSelector } from "../common/VideoVariantSelector";
import UploadAssetModal from "../modals/UploadAssetModal";
import StepHeader from "@/components/shared/StepHeader";
import WorkflowActionButton from "@/components/shared/WorkflowActionButton";
import MethodologyCardSection from "@/components/shared/MethodologyCardSection";

export default function ConsistencyVault() {
    const tv = useTranslations("vault");
    const tStep = useTranslations("stepHeader");
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);



    const [activeTab, setActiveTab] = useState<"character" | "scene" | "prop">("character");

    // Use global state for generation status to persist across navigation
    // Refactored to track { assetId, generationType }
    const generatingTasks = useProjectStore((state) => state.generatingTasks || []); // Fallback to empty array if not defined yet
    const addGeneratingTask = useProjectStore((state) => state.addGeneratingTask);
    const removeGeneratingTask = useProjectStore((state) => state.removeGeneratingTask);

    // Store ID and Type instead of full object to ensure reactivity
    const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
    const [selectedAssetType, setSelectedAssetType] = useState<string | null>(null);

    // Create asset dialog state
    const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);

    // Upload modal state
    const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
    const [uploadTarget, setUploadTarget] = useState<{ id: string; type: string; name: string; description: string } | null>(null);

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
            const stylePrompt = currentProject?.art_direction?.style_config?.positive_prompt || "";

            console.log("[handleGenerate] Starting asset generation...");

            // Call API - now returns immediately with task_id
            const response = await api.generateAsset(
                currentProject.id,
                assetId,
                type,
                "ArtDirection",
                stylePrompt,
                generationType,
                prompt,
                applyStyle,
                negativePrompt,
                batchSize,
                currentProject.model_settings?.t2i_model
            );

            const taskId = response._task_id;
            console.log("[handleGenerate] Got task_id:", taskId);

            // Start polling if we got a task_id
            if (taskId) {
                const pollInterval = setInterval(async () => {
                    try {
                        const status = await api.getTaskStatus(taskId);
                        console.log("[Polling] Task status:", status.status);

                        if (status.status === "completed") {
                            clearInterval(pollInterval);
                            // Refresh project data
                            const updatedProject = await api.getProject(currentProject.id);
                            updateProject(currentProject.id, updatedProject);
                            console.log("Asset generated successfully (async)");

                            if (removeGeneratingTask) {
                                removeGeneratingTask(assetId, generationType);
                            }
                        } else if (status.status === "failed") {
                            clearInterval(pollInterval);
                            console.error("Asset generation failed:", status.error);
                            alert(tv('genFailed', { error: status.error || '' }));

                            // Also refresh project to show updated status
                            try {
                                const updatedProject = await api.getProject(currentProject.id);
                                updateProject(currentProject.id, updatedProject);
                            } catch (refreshError) {
                                console.error("Failed to refresh project:", refreshError);
                            }

                            if (removeGeneratingTask) {
                                removeGeneratingTask(assetId, generationType);
                            }
                        }
                        // If status is "pending" or "processing", continue polling
                    } catch (pollError: any) {
                        console.error("Polling error:", pollError);
                        clearInterval(pollInterval);
                        alert(tv('pollFailed', { error: pollError.message || '' }));
                        if (removeGeneratingTask) {
                            removeGeneratingTask(assetId, generationType);
                        }
                    }
                }, 2000); // Poll every 2 seconds
            } else {
                // Fallback: no task_id means sync response (shouldn't happen, but just in case)
                console.warn("[handleGenerate] No task_id in response, falling back to sync mode");
                updateProject(currentProject.id, response);
                console.log("Asset generated successfully");
                if (removeGeneratingTask) {
                    removeGeneratingTask(assetId, generationType);
                }
            }
        } catch (error: any) {
            console.error("Failed to generate asset:", error);
            alert(tv('startGenFailed', { error: error.response?.data?.detail || error.message }));
            if (removeGeneratingTask) {
                removeGeneratingTask(assetId, generationType);
            }
        }
    };

    // Delete asset handler
    const handleDeleteAsset = async (assetId: string, type: string) => {
        if (!currentProject) return;
        if (!confirm(`Are you sure you want to delete this ${type}?`)) return;

        try {
            if (type === "character") {
                await crudApi.deleteCharacter(currentProject.id, assetId);
            } else if (type === "scene") {
                await crudApi.deleteScene(currentProject.id, assetId);
            } else if (type === "prop") {
                await crudApi.deleteProp(currentProject.id, assetId);
            }
            // Refresh project data
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete asset:", error);
            alert("Failed to delete asset");
        }
    };

    // Create asset handler
    const handleCreateAsset = async (data: { name: string; description: string }) => {
        if (!currentProject) return;

        try {
            if (activeTab === "character") {
                await crudApi.createCharacter(currentProject.id, data);
            } else if (activeTab === "scene") {
                await crudApi.createScene(currentProject.id, data);
            } else if (activeTab === "prop") {
                await crudApi.createProp(currentProject.id, data);
            }
            // Refresh project data
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
            setIsCreateDialogOpen(false);
        } catch (error) {
            console.error("Failed to create asset:", error);
            alert("Failed to create asset");
        }
    };

    // Video Handlers
    const handleGenerateVideo = async (assetId: string, type: string, prompt: string, duration: number, assetSubType: string = "full_body") => {
        if (!currentProject) return;

        // Validate and map the assetSubType to ensure correct values are passed
        let finalAssetType: 'full_body' | 'head_shot' | 'scene' | 'prop' = 'full_body';

        // Different mappings based on the type of asset
        if (type === "scene") {
            finalAssetType = "scene";
        } else if (type === "prop") {
            finalAssetType = "prop";
        } else {
            // For character types, ensure assetSubType is valid
            if (assetSubType === "head_shot") {
                finalAssetType = "head_shot";
            } else {
                finalAssetType = "full_body";  // default to full_body
            }
        }

        // Use a more specific generation type to avoid state pollution
        const generationType = assetSubType === "head_shot" ? "video_head_shot" : "video_full_body";

        if (addGeneratingTask) {
            addGeneratingTask(assetId, generationType, 1);
        }

        try {
            console.log(`[handleGenerateVideo] Starting ${generationType} generation for asset ${type}, type: ${finalAssetType}...`);
            const response = await api.generateMotionRef(
                currentProject.id,
                assetId,
                finalAssetType,
                prompt,
                undefined, // audioUrl
                duration
            );

            const taskId = response._task_id;
            console.log("[handleGenerateVideo] Got task_id:", taskId);

            if (taskId) {
                // Polling mechanism for video task
                const pollInterval = setInterval(async () => {
                    try {
                        const status = await api.getTaskStatus(taskId);
                        console.log(`[Video Polling] Task ${taskId} status:`, status.status);

                        if (status.status === "completed") {
                            clearInterval(pollInterval);
                            // Refresh project data
                            const updatedProject = await api.getProject(currentProject.id);
                            updateProject(currentProject.id, updatedProject);
                            if (removeGeneratingTask) {
                                removeGeneratingTask(assetId, generationType);
                            }
                            console.log(`[Video Polling] ${generationType} generated successfully`);
                        } else if (status.status === "failed") {
                            clearInterval(pollInterval);
                            alert(tv('genFailed', { error: status.error || '' }));
                            if (removeGeneratingTask) {
                                removeGeneratingTask(assetId, generationType);
                            }
                            // Still refresh to show failed status if any
                            const updatedProject = await api.getProject(currentProject.id);
                            updateProject(currentProject.id, updatedProject);
                        }
                    } catch (pollError: any) {
                        console.error("Video polling error:", pollError);
                        clearInterval(pollInterval);
                        alert(tv('pollFailed', { error: pollError.message || '' }));
                        if (removeGeneratingTask) {
                            removeGeneratingTask(assetId, generationType);
                        }
                    }
                }, 3000); // Poll every 3 seconds for video
            } else {
                // Fallback for sync response
                updateProject(currentProject.id, response);
                if (removeGeneratingTask) {
                    removeGeneratingTask(assetId, generationType);
                }
            }
        } catch (error: any) {
            console.error("Failed to generate video:", error);
            alert(tv('startGenFailed', { error: error.response?.data?.detail || error.message }));
            if (removeGeneratingTask) {
                removeGeneratingTask(assetId, generationType);
            }
        }
    };

    const handleDeleteVideo = async (assetId: string, type: string, videoId: string) => {
        if (!currentProject) return;
        if (!confirm("Are you sure you want to delete this video? This action cannot be undone.")) return;

        try {
            await api.deleteAssetVideo(currentProject.id, type, assetId, videoId);
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error: any) {
            console.error("Failed to delete video:", error);
            alert(`Failed to delete video: ${error.message}`);
        }
    };

    // Sync descriptions from Script module to Assets
    const handleSyncDescriptions = async () => {
        if (!currentProject) return;

        const confirmed = confirm(
            tv("syncDescription")
        );

        if (!confirmed) return;

        try {
            const updatedProject = await api.syncDescriptions(currentProject.id);
            updateProject(currentProject.id, updatedProject);
            alert(tv("syncSuccess"));
        } catch (error: any) {
            console.error("Failed to sync descriptions:", error);
            alert(tv('syncFailed', { error: error.message }));
        }
    };

    // Upload handlers
    const handleOpenUploadModal = (asset: any, type: string) => {
        setUploadTarget({
            id: asset.id,
            type: type,
            name: asset.name,
            description: asset.description
        });
        setIsUploadModalOpen(true);
    };

    const handleUploadComplete = async (updatedScript: any) => {
        if (currentProject) {
            updateProject(currentProject.id, updatedScript);
        }
        setIsUploadModalOpen(false);
        setUploadTarget(null);
    };

    const assets = activeTab === "character" ? currentProject?.characters :
        activeTab === "scene" ? currentProject?.scenes :
            activeTab === "prop" ? currentProject?.props : [];

    return (
        <div className="flex flex-col h-full text-foreground">
            <StepHeader
                stepNumber={3}
                totalSteps={6}
                icon={<Users />}
                englishName="Asset Library"
                title={tStep("vaultTitle")}
                subtitle={tStep("vaultSubtitle")}
            />
            {/* Tab bar + sync action */}
            <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-glass-border bg-surface">
                <div className="flex gap-2">
                    <TabButton
                        active={activeTab === "character"}
                        onClick={() => setActiveTab("character")}
                        icon={<User size={14} />}
                        label="Characters"
                        count={currentProject?.characters?.length || 0}
                    />
                    <TabButton
                        active={activeTab === "scene"}
                        onClick={() => setActiveTab("scene")}
                        icon={<MapPin size={14} />}
                        label="Scenes"
                        count={currentProject?.scenes?.length || 0}
                    />
                    <TabButton
                        active={activeTab === "prop"}
                        onClick={() => setActiveTab("prop")}
                        icon={<Box size={14} />}
                        label="Props"
                        count={currentProject?.props?.length || 0}
                    />
                </div>

                <WorkflowActionButton
                    variant="secondary"
                    size="sm"
                    leftIcon={<RefreshCw />}
                    onClick={handleSyncDescriptions}
                    title={tv("syncDescHint")}
                >
                    {tv("syncDesc")}
                </WorkflowActionButton>
            </div>

            {/* Content Grid */}
            {currentProject?.workflow_mode !== "i2v_legacy" && (
                <div className="mx-6 mt-4 px-4 py-3 rounded-lg bg-primary/5 border border-primary/20 flex items-start gap-3">
                    <Paintbrush size={16} className="text-primary mt-0.5 shrink-0" />
                    <div>
                        <p className="text-sm font-medium text-foreground">{tv("r2vModeActive")}</p>
                        <p className="text-xs text-text-secondary mt-0.5">
                            {tv("r2vBannerDesc")}
                        </p>
                    </div>
                </div>
            )}
            <div className="flex-1 overflow-y-auto p-6">
                {!currentProject ? (
                    <div className="flex items-center justify-center h-full text-text-muted">
                        Loading project...
                    </div>
                ) : assets?.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-text-muted gap-4">
                        <div className="w-16 h-16 rounded-full bg-glass flex items-center justify-center">
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
                                onDelete={() => handleDeleteAsset(asset.id, activeTab)}
                                onUpload={() => handleOpenUploadModal(asset, activeTab)}
                            />
                        ))}
                        {/* Create New Asset Button */}
                        <motion.div
                            layout
                            initial={{ opacity: 0, scale: 0.9 }}
                            animate={{ opacity: 1, scale: 1 }}
                            onClick={() => setIsCreateDialogOpen(true)}
                            className="group relative aspect-[3/4] bg-surface rounded-2xl border-2 border-dashed border-glass-border hover:border-primary/50 overflow-hidden transition-all cursor-pointer flex items-center justify-center hover:bg-glass"
                        >
                            <div className="flex flex-col items-center gap-3 text-text-secondary group-hover:text-primary transition-colors">
                                <Plus size={40} />
                                <span className="text-sm font-medium">Add {activeTab}</span>
                            </div>
                        </motion.div>
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
                            stylePrompt={currentProject?.art_direction?.style_config?.positive_prompt || ""}
                            styleNegativePrompt={currentProject?.art_direction?.style_config?.negative_prompt || ""}
                            onGenerateVideo={(prompt: string, duration: number, subType?: string) => handleGenerateVideo(selectedAssetId, selectedAssetType, prompt, duration, subType || "video")}
                            onDeleteVideo={(videoId: string) => handleDeleteVideo(selectedAssetId, selectedAssetType, videoId)}
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
                            stylePrompt={currentProject?.art_direction?.style_config?.positive_prompt || ""}
                            styleNegativePrompt={currentProject?.art_direction?.style_config?.negative_prompt || ""}
                            onGenerateVideo={(prompt: string, duration: number) => handleGenerateVideo(selectedAssetId, selectedAssetType, prompt, duration, "video")}
                            onDeleteVideo={(videoId: string) => handleDeleteVideo(selectedAssetId, selectedAssetType, videoId)}
                            isGeneratingVideo={getAssetGeneratingTypes(selectedAssetId).some((t: any) => t.type.startsWith("video"))}
                        />
                    )
                )}
            </AnimatePresence>



            {/* Create Asset Dialog */}
            <AnimatePresence>
                {isCreateDialogOpen && (
                    <CreateAssetDialog
                        type={activeTab}
                        onClose={() => setIsCreateDialogOpen(false)}
                        onCreate={handleCreateAsset}
                    />
                )}
            </AnimatePresence>

            {/* Upload Asset Modal */}
            {uploadTarget && currentProject && (
                <UploadAssetModal
                    isOpen={isUploadModalOpen}
                    onClose={() => {
                        setIsUploadModalOpen(false);
                        setUploadTarget(null);
                    }}
                    assetId={uploadTarget.id}
                    assetType={uploadTarget.type as "character" | "scene" | "prop"}
                    assetName={uploadTarget.name}
                    defaultDescription={uploadTarget.description}
                    scriptId={currentProject.id}
                    onUploadComplete={handleUploadComplete}
                />
            )}
        </div >
    );
}

function CharacterDetailModal({ asset, type, onClose, onUpdateDescription, onGenerate, isGenerating, stylePrompt = "", styleNegativePrompt = "", onGenerateVideo, onDeleteVideo, isGeneratingVideo }: any) {
    const [description, setDescription] = useState(asset.description);
    const [isEditing, setIsEditing] = useState(false);
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    // Style Controls
    const [applyStyle, setApplyStyle] = useState(true);
    const [negativePrompt, setNegativePrompt] = useState(styleNegativePrompt || "low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry");
    const [showAdvanced, setShowAdvanced] = useState(false);

    // Video Controls
    const [activeTab, setActiveTab] = useState<"image" | "video">("image");
    const [videoPrompt, setVideoPrompt] = useState(asset.video_prompt || "");

    // Sync local state if asset changes
    useEffect(() => {
        setDescription(asset.description);
        if (asset.video_prompt) setVideoPrompt(asset.video_prompt);
        else if (!videoPrompt) {
            setVideoPrompt(`Cinematic shot of ${asset.name}, ${asset.description}, looking around, breathing, slight movement, high quality, 4k`);
        }
    }, [asset]);

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
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-surface border border-glass-border rounded-2xl w-full max-w-5xl h-[85vh] flex overflow-hidden shadow-lg"
            >
                {/* Left: Variant Selector */}
                <div className="w-1/2 bg-surface relative border-r border-glass-border flex flex-col overflow-hidden">
                    {/* Tab Switcher */}
                    <div className="flex border-b border-glass-border bg-surface">
                        <button
                            onClick={() => setActiveTab("image")}
                            className={`flex-1 p-3 text-sm font-bold transition-colors ${activeTab === "image" ? "text-foreground border-b-2 border-primary bg-glass" : "text-text-muted hover:text-text-secondary"}`}
                        >
                            Image Reference
                        </button>
                        <button
                            onClick={() => setActiveTab("video")}
                            className={`flex-1 p-3 text-sm font-bold transition-colors ${activeTab === "video" ? "text-foreground border-b-2 border-primary bg-glass" : "text-text-muted hover:text-text-secondary"}`}
                        >
                            Video Reference
                        </button>
                    </div>

                    <div className="flex-1 p-4 overflow-hidden">
                        {activeTab === "image" ? (
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
                        ) : (
                            <VideoVariantSelector
                                videos={asset.video_assets || []}
                                onDelete={onDeleteVideo}
                                onGenerate={(duration) => onGenerateVideo(videoPrompt, duration)}
                                isGenerating={isGeneratingVideo}
                                aspectRatio="16:9"
                                className="h-full"
                            />
                        )}
                    </div>
                </div>

                {/* Right: Details */}
                <div className="w-1/2 flex flex-col">
                    {/* Header */}
                    <div className="p-6 border-b border-glass-border flex justify-between items-center bg-surface">
                        <h2 className="text-2xl font-bold text-foreground">{asset.name}</h2>
                        <button onClick={onClose} className="p-2 hover:bg-hover-bg rounded-full text-text-secondary hover:text-foreground">
                            <X size={24} />
                        </button>
                    </div>

                    {/* Content */}
                    <div className="flex-1 p-6 overflow-y-auto space-y-6">
                        {/* Description */}
                        <div className="space-y-2">
                            <div className="flex justify-between items-center">
                                <label className="text-sm font-bold text-text-secondary uppercase">Description</label>
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
                                        className="w-full h-32 bg-input-bg border border-glass-border rounded-lg p-3 text-sm text-text-secondary resize-none focus:border-primary focus:outline-none"
                                    />
                                    <div className="flex justify-end gap-2">
                                        <button onClick={() => { setIsEditing(false); setDescription(asset.description); }} className="px-3 py-1.5 text-xs text-text-secondary hover:text-foreground">Cancel</button>
                                        <button onClick={handleSave} className="px-3 py-1.5 bg-primary text-white text-xs rounded hover:bg-primary/90">Save Description</button>
                                    </div>
                                </div>
                            ) : (
                                <p className="text-sm text-text-secondary leading-relaxed bg-glass p-3 rounded-lg border border-transparent hover:border-glass-border transition-colors">
                                    {asset.description}
                                </p>
                            )}
                        </div>

                        {/* Methodology Card (T-B6 三分离卡：scene 露出 image 卡+视觉锚点，prop 自动隐藏) */}
                        <MethodologyCardSection asset={asset} type={type} />

                        {/* Video Prompt (Only visible in Video Tab) */}
                        {activeTab === "video" && (
                            <div className="space-y-2">
                                <label className="text-sm font-bold text-text-secondary uppercase">Video Prompt</label>
                                <textarea
                                    value={videoPrompt}
                                    onChange={(e) => setVideoPrompt(e.target.value)}
                                    className="w-full h-24 bg-input-bg border border-glass-border rounded-lg p-3 text-sm text-text-secondary resize-none focus:border-primary focus:outline-none"
                                    placeholder="Describe the motion..."
                                />
                            </div>
                        )}

                        {/* Style Control (Only visible in Image Tab) */}
                        {activeTab === "image" && (
                            <div className="space-y-2">
                                <label className="text-sm font-bold text-text-secondary uppercase">Style Settings</label>
                                <div className="bg-glass rounded-lg p-3 border border-border-subtle">
                                    <div className="flex items-center gap-2 mb-2">
                                        <input
                                            type="checkbox"
                                            id="applyStyleModal"
                                            checked={applyStyle}
                                            onChange={(e) => setApplyStyle(e.target.checked)}
                                            className="rounded border-gray-600 bg-gray-700 text-primary focus:ring-primary"
                                        />
                                        <label htmlFor="applyStyleModal" className="text-sm font-bold text-text-secondary cursor-pointer select-none">
                                            Apply Art Direction Style
                                        </label>
                                    </div>

                                    {stylePrompt && (
                                        <div className="text-xs text-text-muted font-mono bg-surface p-2 rounded border border-border-subtle">
                                            <span className="text-primary font-bold">Style:</span> {stylePrompt}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Advanced Settings (Negative Prompt) - Only visible in Image Tab */}
                        {activeTab === "image" && (
                            <div className="space-y-2">
                                <button
                                    onClick={() => setShowAdvanced(!showAdvanced)}
                                    className="flex items-center gap-2 text-xs font-bold text-text-muted hover:text-foreground transition-colors uppercase"
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
                                                className="w-full h-24 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-text-secondary resize-none focus:outline-none focus:border-primary/50 font-mono"
                                                placeholder="Enter negative prompt..."
                                            />
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        )}
                    </div>

                    {/* Footer Actions */}
                    <div className="p-6 border-t border-glass-border bg-surface flex gap-4">
                        <button
                            onClick={onClose}
                            className="flex-1 py-3 bg-green-600 hover:bg-green-500 text-foreground rounded-xl font-bold flex items-center justify-center gap-2 shadow-lg shadow-green-500/20"
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
            className={`inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 border transition-colors ${active
                ? "bg-[rgba(100,108,255,0.12)] text-foreground border-primary"
                : "bg-glass text-text-secondary hover:text-foreground border-glass-border hover:border-glass-border-strong"
                }`}
        >
            <span className={active ? "text-primary" : ""}>{icon}</span>
            <span className="font-mono text-[0.65625rem] font-semibold uppercase tracking-[0.14em]">{label}</span>
            <span className={`font-mono text-[0.5625rem] px-1.5 py-0.5 rounded-full border ${
                active
                    ? "text-primary border-primary/40 bg-[rgba(100,108,255,0.08)]"
                    : "text-text-muted border-glass-border bg-black/30"
            }`}>{count}</span>
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
                <div className="absolute inset-0 flex items-center justify-center bg-surface z-10">
                    <RefreshCw className="animate-spin text-text-secondary" size={24} />
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

function AssetCard({ asset, type, isGenerating, onGenerate, onToggleLock, onClick, onDelete, onUpload }: any) {
    const tv = useTranslations("vault");
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
    const fullImageUrl = getAssetUrl(imageUrl);

    return (
        <motion.div
            layout
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            onClick={onClick}
            className={`group relative aspect-[3/4] bg-surface rounded-2xl border overflow-hidden transition-colors cursor-pointer ${isLocked ? 'border-primary/60 border-dashed' : 'border-glass-border hover:border-primary/50'
                }`}
        >
            {/* Image Area */}
            <div className="absolute inset-0 bg-gradient-to-b from-transparent to-black/60 z-10" />

            {imageUrl ? (
                <ImageWithRetry
                    src={fullImageUrl}
                    alt={asset.name}
                    className="w-full h-full object-cover"
                />
            ) : (
                <div className="w-full h-full flex items-center justify-center bg-glass">
                    <ImageIcon className="text-text-muted" size={48} />
                </div>
            )}

            {/* Loading Overlay */}
            {isGenerating && (
                <div className="absolute inset-0 z-20 bg-overlay backdrop-blur-sm flex items-center justify-center flex-col gap-2">
                    <RefreshCw className="animate-spin text-primary" size={32} />
                    <span className="text-xs font-mono text-primary">Generating...</span>
                </div>
            )}

            {/* Top Actions Overlay */}
            <div className="absolute top-2 right-2 z-30 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        onDelete();
                    }}
                    className="p-2 rounded-full backdrop-blur-md bg-red-500/20 text-red-400 hover:bg-red-500/40 transition-colors"
                    title="Delete"
                >
                    <Trash2 size={14} />
                </button>
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        onToggleLock();
                    }}
                    className={`p-2 rounded-full backdrop-blur-md transition-colors ${isLocked
                        ? "bg-primary/20 text-primary hover:bg-primary/30"
                        : "bg-surface text-foreground hover:bg-hover-bg"
                        }`}
                >
                    {isLocked ? <Lock size={14} /> : <Unlock size={14} />}
                </button>
            </div>

            {/* Bottom Info */}
            <div className="absolute bottom-0 left-0 right-0 p-4 z-30">
                <h3 className="text-lg font-bold text-foreground mb-1 truncate">{asset.name}</h3>
                <p className="text-xs text-foreground/80 line-clamp-2 mb-3 h-8">
                    {asset.description || "No description"}
                </p>

                <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity transform translate-y-2 group-hover:translate-y-0">
                    <WorkflowActionButton
                        onClick={(e) => {
                            e.stopPropagation();
                            onGenerate();
                        }}
                        disabled={isLocked || isGenerating}
                        loading={isGenerating}
                        leftIcon={!isGenerating ? <RefreshCw /> : undefined}
                        variant="primary"
                        size="sm"
                        className="flex-1"
                    >
                        {isGenerating ? "Generating..." : "Generate"}
                    </WorkflowActionButton>
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            onUpload?.();
                        }}
                        className="px-2.5 rounded-full bg-glass hover:bg-hover-bg border border-glass-border text-foreground cursor-pointer transition-colors"
                        title={tv("uploadAsset")}
                    >
                        <Upload size={14} />
                    </button>
                </div>
            </div>
        </motion.div>
    );
}



function CreateAssetDialog({ type, onClose, onCreate }: { type: string; onClose: () => void; onCreate: (data: { name: string; description: string }) => void }) {
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [isSubmitting, setIsSubmitting] = useState(false);

    const handleSubmit = async () => {
        if (!name.trim()) {
            alert("Name is required");
            return;
        }
        setIsSubmitting(true);
        try {
            await onCreate({ name: name.trim(), description: description.trim() });
        } finally {
            setIsSubmitting(false);
        }
    };

    const typeLabel = type === "character" ? "Character" : type === "scene" ? "Scene" : "Prop";

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-surface border border-glass-border rounded-2xl w-full max-w-md overflow-hidden shadow-lg"
            >
                <div className="p-6 border-b border-glass-border flex justify-between items-center bg-surface">
                    <div className="flex items-center gap-3">
                        <Plus className="text-primary" size={20} />
                        <h2 className="text-lg font-bold text-foreground">Create New {typeLabel}</h2>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-hover-bg rounded-lg transition-colors">
                        <X size={20} className="text-text-secondary" />
                    </button>
                </div>

                <div className="p-6 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-2">Name *</label>
                        <input
                            type="text"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder={`Enter ${type} name`}
                            className="w-full px-4 py-3 bg-input-bg border border-glass-border rounded-lg text-foreground placeholder-text-muted focus:border-primary/50 focus:outline-none"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-2">Description</label>
                        <textarea
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder={`Describe the ${type}...`}
                            rows={4}
                            className="w-full px-4 py-3 bg-input-bg border border-glass-border rounded-lg text-foreground placeholder-text-muted focus:border-primary/50 focus:outline-none resize-none"
                        />
                    </div>
                </div>

                <div className="p-6 border-t border-glass-border flex justify-end gap-3">
                    <button
                        onClick={onClose}
                        className="px-6 py-2 bg-glass hover:bg-hover-bg text-foreground rounded-lg transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={isSubmitting || !name.trim()}
                        className="px-6 py-2 bg-primary hover:bg-primary/90 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                        {isSubmitting && <RefreshCw size={16} className="animate-spin" />}
                        Create {typeLabel}
                    </button>
                </div>
            </motion.div>
        </div>
    );
}
