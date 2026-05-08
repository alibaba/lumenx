"use client";

import { useState, useEffect, type ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import NextImage from "next/image";
import { User, MapPin, Box, Lock, Unlock, RefreshCw, Upload, Image as ImageIcon, X, Check, ChevronRight, Trash2, Plus } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import type { Character, Project, Prop, Scene } from "@/store/projectStore";
import { api, crudApi, type GenerateAssetPayload, type GenerateMotionRefPayload } from "@/lib/api";
import { appendAssetQueryParam, extractErrorDetail, getAssetUrl } from "@/lib/utils";
import CharacterWorkbench from "./CharacterWorkbench";
import { VariantSelector } from "../common/VariantSelector";
import { VideoVariantSelector } from "../common/VideoVariantSelector";
import UploadAssetModal from "../modals/UploadAssetModal";
import PromptQualityPanel from "../common/PromptQualityPanel";
import { assetTypeTerms, getAssetTypeTerm, messages } from "@/lib/i18n";
import {
    applyImagePromptBlock,
    buildDefaultImagePrompt,
    getImagePromptScaffolds,
    getImagePromptTemplates,
    type ImagePromptTarget,
} from "@/lib/image-prompt-recipes";
import {
    formatPromptIssues,
    hasBlockingPromptIssues,
    inspectImagePrompt,
} from "@/lib/prompt-quality";

const copy = messages.modules.consistencyVault;
const commonActions = messages.common.actions;

type AssetKind = "character" | "scene" | "prop";
type AssetEntity = Character | Scene | Prop;
type GeneratingTaskSummary = { type: string; batchSize: number };
type UploadTargetData = { id: string; type: AssetKind; name: string; description: string };
type DetailAsset = (Scene | Prop) & { image_prompt?: string };

interface CharacterDetailModalProps {
    asset: DetailAsset;
    type: "scene" | "prop";
    onClose: () => void;
    onUpdateDescription: (description: string) => void;
    onGenerate: (prompt: string, applyStyle: boolean, negativePrompt: string, batchSize: number) => void;
    isGenerating: boolean;
    stylePrompt?: string;
    styleNegativePrompt?: string;
    onGenerateVideo: (prompt: string, duration: number, assetSubType?: string) => void;
    onDeleteVideo: (videoId: string) => void;
    isGeneratingVideo: boolean;
}

interface TabButtonProps {
    active: boolean;
    onClick: () => void;
    icon: ReactNode;
    label: string;
    count: number;
}

interface AssetCardProps {
    asset: AssetEntity;
    type: AssetKind;
    isGenerating: boolean;
    onGenerate: () => void;
    onToggleLock: () => void;
    onClick: () => void;
    onDelete: () => void;
    onUpload?: () => void;
}

function getDetailPromptTarget(type: string): ImagePromptTarget {
    return type === "scene" ? "scene" : "prop";
}

export default function ConsistencyVault() {
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
    const [uploadTarget, setUploadTarget] = useState<UploadTargetData | null>(null);

    // Derive selected asset from currentProject
    const selectedAsset: AssetEntity | null = currentProject ? (() => {
        if (!selectedAssetId || !selectedAssetType) return null;
        const list = selectedAssetType === "character" ? currentProject.characters :
            selectedAssetType === "scene" ? currentProject.scenes :
                selectedAssetType === "prop" ? currentProject.props : [];
        return list.find((asset) => asset.id === selectedAssetId) || null;
    })() : null;

    const isAssetGenerating = (assetId: string) => {
        return generatingTasks?.some((task) => task.assetId === assetId);
    };

    const getAssetGeneratingTypes = (assetId: string) => {
        return generatingTasks?.filter((task) => task.assetId === assetId).map((task): GeneratingTaskSummary => ({
            type: task.generationType,
            batchSize: task.batchSize,
        })) || [];
    };

    const handleUpdateDescription = async (assetId: string, type: string, description: string) => {
        if (!currentProject) return;
        try {
            const updatedProject = await api.updateAssetDescription(currentProject.id, assetId, type, description);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error(copy.failedToUpdateDescription, error);
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

            const assetPayload: GenerateAssetPayload = {
                assetId,
                assetType: type,
                stylePreset: "ArtDirection",
                stylePrompt,
                generationType,
                prompt,
                applyStyle,
                negativePrompt,
                batchSize,
                modelName: currentProject.model_settings?.t2i_model,
            };

            // Call API - now returns immediately with task_id
            const response = await api.generateAsset(currentProject.id, assetPayload);

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
                            alert(copy.generationFailed(status.error));

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
                    } catch (pollError) {
                        console.error("Polling error:", pollError);
                        clearInterval(pollInterval);
                        alert(copy.taskPollingFailed(extractErrorDetail(pollError, pollError instanceof Error ? pollError.message : "")));
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
        } catch (error) {
            console.error("Failed to generate asset:", error);
            alert(copy.startGenerationFailed(extractErrorDetail(error, "")));
            if (removeGeneratingTask) {
                removeGeneratingTask(assetId, generationType);
            }
        }
    };

    // Delete asset handler
    const handleDeleteAsset = async (assetId: string, type: string) => {
        if (!currentProject) return;
        if (!confirm(copy.deleteAssetConfirm(getAssetTypeTerm(type)?.label || type))) return;

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
            alert(copy.failedToDeleteAsset);
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
            alert(copy.failedToCreateAsset);
        }
    };

    // Video Handlers
    const handleGenerateVideo = async (assetId: string, type: string, prompt: string, duration: number, assetSubType: string = "full_body", audioUrl?: string) => {
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
            const motionPayload: GenerateMotionRefPayload = {
                assetId,
                assetType: finalAssetType,
                prompt,
                audioUrl,
                duration,
            };

            const response = await api.generateMotionRef(currentProject.id, motionPayload);

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
                            alert(copy.videoGenerationFailed(status.error));
                            if (removeGeneratingTask) {
                                removeGeneratingTask(assetId, generationType);
                            }
                            // Still refresh to show failed status if any
                            const updatedProject = await api.getProject(currentProject.id);
                            updateProject(currentProject.id, updatedProject);
                        }
                    } catch (pollError) {
                        console.error("Video polling error:", pollError);
                        clearInterval(pollInterval);
                        alert(copy.videoPollingFailed(extractErrorDetail(pollError, pollError instanceof Error ? pollError.message : "")));
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
        } catch (error) {
            console.error("Failed to generate video:", error);
            alert(copy.startVideoGenerationFailed(extractErrorDetail(error, "")));
            if (removeGeneratingTask) {
                removeGeneratingTask(assetId, generationType);
            }
        }
    };

    const handleDeleteVideo = async (assetId: string, type: string, videoId: string) => {
        if (!currentProject) return;
        if (!confirm(copy.deleteVideoConfirm)) return;

        try {
            await api.deleteAssetVideo(currentProject.id, type, assetId, videoId);
            const updatedProject = await api.getProject(currentProject.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to delete video:", error);
            alert(copy.failedToDeleteVideo(extractErrorDetail(error, "")));
        }
    };

    // Sync descriptions from Script module to Assets
    const handleSyncDescriptions = async () => {
        if (!currentProject) return;

        const confirmed = confirm(
            copy.syncDescriptionsConfirm
        );

        if (!confirmed) return;

        try {
            const updatedProject = await api.syncDescriptions(currentProject.id);
            updateProject(currentProject.id, updatedProject);
            alert(copy.syncDescriptionsSuccess);
        } catch (error) {
            console.error(copy.failedToSyncDescriptions, error);
            alert(`${copy.failedToSyncDescriptions}${extractErrorDetail(error, "")}`);
        }
    };

    // Upload handlers
    const handleOpenUploadModal = (asset: AssetEntity, type: AssetKind) => {
        setUploadTarget({
            id: asset.id,
            type,
            name: asset.name,
            description: asset.description || ""
        });
        setIsUploadModalOpen(true);
    };

    const handleUploadComplete = async (updatedScript: Project) => {
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
        <div className="flex flex-col h-full text-white">
            {/* Header */}
            <div className="flex items-center justify-between p-6 border-b border-white/10 bg-black/20">
                <div className="flex gap-2 bg-black/40 p-1 rounded-xl border border-white/5">
                    <TabButton
                        active={activeTab === "character"}
                        onClick={() => setActiveTab("character")}
                        icon={<User size={18} />}
                        label={assetTypeTerms.character.pluralLabel}
                        count={currentProject?.characters?.length || 0}
                    />
                    <TabButton
                        active={activeTab === "scene"}
                        onClick={() => setActiveTab("scene")}
                        icon={<MapPin size={18} />}
                        label={assetTypeTerms.scene.pluralLabel}
                        count={currentProject?.scenes?.length || 0}
                    />
                    <TabButton
                        active={activeTab === "prop"}
                        onClick={() => setActiveTab("prop")}
                        icon={<Box size={18} />}
                        label={assetTypeTerms.prop.pluralLabel}
                        count={currentProject?.props?.length || 0}
                    />
                </div>

                <div className="flex gap-2">
                    <button
                        onClick={handleSyncDescriptions}
                        className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg transition-colors"
                        title={copy.syncDescriptionsTitle}
                    >
                        <RefreshCw size={16} className="text-blue-400" />
                        <span className="text-sm font-bold">{copy.syncDescriptions}</span>
                    </button>

                </div>
            </div>

            {/* Content Grid */}
            <div className="flex-1 overflow-y-auto p-6">
                {!currentProject ? (
                    <div className="flex items-center justify-center h-full text-gray-500">
                        {copy.loadingProject}
                    </div>
                ) : assets?.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-gray-500 gap-4">
                        <div className="w-16 h-16 rounded-full bg-white/5 flex items-center justify-center">
                            {activeTab === "character" ? <User size={32} /> : activeTab === "scene" ? <MapPin size={32} /> : <Box size={32} />}
                        </div>
                        <p>{copy.noAssetsFound(getAssetTypeTerm(activeTab)?.pluralLabel || activeTab)}</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                        {assets?.map((asset) => (
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
                            className="group relative aspect-[3/4] bg-black/20 rounded-2xl border-2 border-dashed border-white/20 hover:border-primary/50 overflow-hidden transition-all cursor-pointer flex items-center justify-center hover:bg-white/5"
                        >
                            <div className="flex flex-col items-center gap-3 text-gray-400 group-hover:text-primary transition-colors">
                                <Plus size={40} />
                                <span className="text-sm font-medium">{copy.addAsset(getAssetTypeTerm(activeTab)?.label || activeTab)}</span>
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
                            onGenerateVideo={(prompt: string, duration: number, subType?: string, audioUrl?: string) => handleGenerateVideo(selectedAssetId, selectedAssetType, prompt, duration, subType || "video", audioUrl)}
                            onDeleteVideo={(videoId: string) => handleDeleteVideo(selectedAssetId, selectedAssetType, videoId)}
                        />
                    ) : (
                        <CharacterDetailModal
                            asset={selectedAsset as DetailAsset}
                            type={selectedAssetType as "scene" | "prop"}
                            onClose={() => {
                                setSelectedAssetId(null);
                                setSelectedAssetType(null);
                            }}
                            onUpdateDescription={(desc: string) => handleUpdateDescription(selectedAssetId, selectedAssetType, desc)}
                            onGenerate={(prompt: string, applyStyle: boolean, negativePrompt: string, batchSize: number) =>
                                handleGenerate(selectedAssetId, selectedAssetType, "all", prompt, applyStyle, negativePrompt, batchSize)
                            }
                            isGenerating={isAssetGenerating(selectedAssetId)}
                            stylePrompt={currentProject?.art_direction?.style_config?.positive_prompt || ""}
                            styleNegativePrompt={currentProject?.art_direction?.style_config?.negative_prompt || ""}
                            onGenerateVideo={(prompt: string, duration: number) => handleGenerateVideo(selectedAssetId, selectedAssetType, prompt, duration, "video")}
                            onDeleteVideo={(videoId: string) => handleDeleteVideo(selectedAssetId, selectedAssetType, videoId)}
                            isGeneratingVideo={getAssetGeneratingTypes(selectedAssetId).some((task) => task.type.startsWith("video"))}
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

function CharacterDetailModal({ asset, type, onClose, onUpdateDescription, onGenerate, isGenerating, stylePrompt = "", styleNegativePrompt = "", onGenerateVideo, onDeleteVideo, isGeneratingVideo }: CharacterDetailModalProps) {
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
    const [imagePrompt, setImagePrompt] = useState(
        asset.image_prompt || buildDefaultImagePrompt({
            target: getDetailPromptTarget(type),
            name: asset.name,
            description: asset.description,
            stylePrompt,
        }),
    );
    const [videoPrompt, setVideoPrompt] = useState(asset.video_prompt || "");
    const imageScaffolds = getImagePromptScaffolds({
        target: getDetailPromptTarget(type),
        stylePrompt,
        description: asset.description,
    });
    const imageTemplates = getImagePromptTemplates({
        target: getDetailPromptTarget(type),
        stylePrompt,
        description: asset.description,
    });
    const imagePromptIssues = inspectImagePrompt({
        prompt: imagePrompt,
        target: getDetailPromptTarget(type),
        stylePrompt,
    });

    // Sync local state if asset changes
    useEffect(() => {
        setDescription(asset.description);
        if (asset.image_prompt) {
            setImagePrompt(asset.image_prompt);
        } else {
            setImagePrompt(buildDefaultImagePrompt({
                target: getDetailPromptTarget(type),
                name: asset.name,
                description: asset.description,
                stylePrompt,
            }));
        }
        if (asset.video_prompt) setVideoPrompt(asset.video_prompt);
        else if (!videoPrompt) {
            setVideoPrompt(`Cinematic shot of ${asset.name}, ${asset.description}, looking around, breathing, slight movement, high quality, 4k`);
        }
    }, [asset, type, stylePrompt, videoPrompt]);

    // Sync negative prompt if style changes
    useEffect(() => {
        if (styleNegativePrompt && (!negativePrompt || negativePrompt.includes("low quality"))) {
            setNegativePrompt(styleNegativePrompt);
        }
    }, [negativePrompt, styleNegativePrompt]);

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
        if (hasBlockingPromptIssues(imagePromptIssues)) {
            alert(`${copy.detailModal.promptQualityBlocked}\n\n${formatPromptIssues(imagePromptIssues.filter((issue) => issue.severity === "error"))}`);
            return;
        }

        onGenerate(imagePrompt, applyStyle, negativePrompt, batchSize);
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
                <div className="w-1/2 bg-black/40 relative border-r border-white/10 flex flex-col overflow-hidden">
                    {/* Tab Switcher */}
                    <div className="flex border-b border-white/10 bg-black/20">
                        <button
                            onClick={() => setActiveTab("image")}
                            className={`flex-1 p-3 text-sm font-bold transition-colors ${activeTab === "image" ? "text-white border-b-2 border-primary bg-white/5" : "text-gray-500 hover:text-gray-300"}`}
                        >
                            {copy.detailModal.imageReference}
                        </button>
                        <button
                            onClick={() => setActiveTab("video")}
                            className={`flex-1 p-3 text-sm font-bold transition-colors ${activeTab === "video" ? "text-white border-b-2 border-primary bg-white/5" : "text-gray-500 hover:text-gray-300"}`}
                        >
                            {copy.detailModal.videoReference}
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
                                <label className="text-sm font-bold text-gray-400 uppercase">{copy.detailModal.description}</label>
                                {!isEditing && (
                                    <button onClick={() => setIsEditing(true)} className="text-xs text-primary hover:underline">
                                        {commonActions.edit}
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
                                        <button onClick={() => { setIsEditing(false); setDescription(asset.description); }} className="px-3 py-1.5 text-xs text-gray-400 hover:text-white">{commonActions.cancel}</button>
                                        <button onClick={handleSave} className="px-3 py-1.5 bg-primary text-white text-xs rounded hover:bg-primary/90">{copy.detailModal.saveDescription}</button>
                                    </div>
                                </div>
                            ) : (
                                <p className="text-sm text-gray-300 leading-relaxed bg-white/5 p-3 rounded-lg border border-transparent hover:border-white/10 transition-colors">
                                    {asset.description}
                                </p>
                            )}
                        </div>

                        {/* Video Prompt (Only visible in Video Tab) */}
                        {activeTab === "video" && (
                            <div className="space-y-2">
                                <label className="text-sm font-bold text-gray-400 uppercase">{copy.detailModal.videoPrompt}</label>
                                <textarea
                                    value={videoPrompt}
                                    onChange={(e) => setVideoPrompt(e.target.value)}
                                    className="w-full h-24 bg-black/20 border border-white/10 rounded-lg p-3 text-sm text-gray-300 resize-none focus:border-primary focus:outline-none"
                                    placeholder={copy.detailModal.videoPromptPlaceholder}
                                />
                            </div>
                        )}

                        {activeTab === "image" && (
                            <div className="space-y-4">
                                <div className="space-y-2">
                                    <label className="text-sm font-bold text-gray-400 uppercase">{copy.detailModal.imagePrompt}</label>
                                    <textarea
                                        value={imagePrompt}
                                        onChange={(e) => setImagePrompt(e.target.value)}
                                        className="w-full h-32 bg-black/20 border border-white/10 rounded-lg p-3 text-sm text-gray-300 resize-none focus:border-primary focus:outline-none font-mono"
                                        placeholder={copy.detailModal.imagePromptPlaceholder}
                                    />
                                </div>

                                {imageScaffolds.length > 0 && (
                                    <div className="space-y-2">
                                        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500">
                                            {copy.detailModal.promptScaffold}
                                        </p>
                                        <div className="flex flex-wrap gap-2">
                                            {imageScaffolds.map((item) => (
                                                <button
                                                    key={item.id}
                                                    type="button"
                                                    onClick={() => setImagePrompt(applyImagePromptBlock(imagePrompt, item.prompt, "replace"))}
                                                    className="rounded-full border border-cyan-400/25 bg-cyan-400/10 px-3 py-1 text-[11px] text-cyan-100 transition hover:bg-cyan-400/20"
                                                >
                                                    {item.title}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {imageTemplates.length > 0 && (
                                    <div className="space-y-2">
                                        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500">
                                            {copy.detailModal.promptTemplates}
                                        </p>
                                        <div className="flex flex-wrap gap-2">
                                            {imageTemplates.map((item) => (
                                                <button
                                                    key={item.id}
                                                    type="button"
                                                    onClick={() => setImagePrompt(applyImagePromptBlock(imagePrompt, item.prompt, "append"))}
                                                    className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] text-gray-200 transition hover:bg-white/10"
                                                >
                                                    {copy.detailModal.appendTemplate(item.title)}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                <PromptQualityPanel
                                    issues={imagePromptIssues}
                                    title={copy.detailModal.promptQualityTitle}
                                    compact
                                />
                            </div>
                        )}

                        {/* Style Control (Only visible in Image Tab) */}
                        {activeTab === "image" && (
                            <div className="space-y-2">
                                <label className="text-sm font-bold text-gray-400 uppercase">{copy.detailModal.styleSettings}</label>
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
                                            {copy.detailModal.applyArtDirectionStyle}
                                        </label>
                                    </div>

                                    {stylePrompt && (
                                        <div className="text-xs text-gray-500 font-mono bg-black/20 p-2 rounded border border-white/5">
                                            <span className="text-primary font-bold">{copy.detailModal.styleLabel}</span> {stylePrompt}
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
                                    className="flex items-center gap-2 text-xs font-bold text-gray-500 hover:text-white transition-colors uppercase"
                                >
                                    <span>{copy.detailModal.advancedNegativePrompt}</span>
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
                                                placeholder={copy.detailModal.negativePromptPlaceholder}
                                            />
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        )}
                    </div>

                    {/* Footer Actions */}
                    <div className="p-6 border-t border-white/10 bg-black/20 flex gap-4">
                        <button
                            onClick={onClose}
                            className="flex-1 py-3 bg-green-600 hover:bg-green-500 text-white rounded-xl font-bold flex items-center justify-center gap-2 shadow-lg shadow-green-900/20"
                        >
                            <Check size={18} />
                            {commonActions.done}
                        </button>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}

function TabButton({ active, onClick, icon, label, count }: TabButtonProps) {
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

    const displaySrc = retryCount > 0 ? appendAssetQueryParam(src, "retry", retryCount) : src;

    return (
        <div className={`relative ${className}`}>
            {isLoading && (
                <div className="absolute inset-0 flex items-center justify-center bg-white/5 backdrop-blur-sm z-10">
                    <RefreshCw className="animate-spin text-white/50" size={24} />
                </div>
            )}
            <NextImage
                key={`${displaySrc}-${retryCount}`}
                src={displaySrc}
                alt={alt}
                fill
                sizes="(max-width: 768px) 100vw, 50vw"
                unoptimized
                className={`${isLoading ? 'opacity-0' : 'opacity-100'} transition-opacity duration-300 object-cover`}
                onLoad={() => {
                    setError(false);
                    setIsLoading(false);
                }}
                onError={() => {
                    setError(true);
                    setIsLoading(true); // Keep showing loader while retrying
                }}
            />
            {error && retryCount >= 10 && (
                <div className="absolute inset-0 flex items-center justify-center bg-red-500/10 backdrop-blur-sm z-20">
                    <span className="text-xs text-red-400 font-bold">{copy.detailModal.imageLoadFailed}</span>
                </div>
            )}
        </div>
    );
}

function AssetCard({ asset, type, isGenerating, onGenerate, onToggleLock, onClick, onDelete, onUpload }: AssetCardProps) {
    const isLocked = asset.locked || false;
    const characterAsset = type === "character" ? asset as Character : null;
    const imageUrl = characterAsset ? (characterAsset.avatar_url || characterAsset.image_url) : asset.image_url;
    const fullImageUrl = getAssetUrl(imageUrl);
    const primaryActionLabel = type === "character" ? copy.assetCard.generateSet : copy.assetCard.generate;
    const uploadActionTitle = type === "character" ? copy.assetCard.uploadReferenceTitle : copy.assetCard.uploadTitle;
    const characterVariantStatus = type === "character"
        ? [
            {
                key: "full_body",
                label: copy.assetCard.variantLabels.fullBody,
                ready: !!(characterAsset?.full_body_image_url || (characterAsset?.full_body_asset?.variants?.length || 0) > 0),
            },
            {
                key: "three_view",
                label: copy.assetCard.variantLabels.threeView,
                ready: !!(characterAsset?.three_view_image_url || (characterAsset?.three_view_asset?.variants?.length || 0) > 0),
            },
            {
                key: "headshot",
                label: copy.assetCard.variantLabels.headshot,
                ready: !!(characterAsset?.headshot_image_url || characterAsset?.avatar_url || (characterAsset?.headshot_asset?.variants?.length || 0) > 0),
            },
        ]
        : [];

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
                    <span className="text-xs font-mono text-primary">{copy.assetCard.generating}</span>
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
                    title={commonActions.delete}
                >
                    <Trash2 size={14} />
                </button>
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
                    {asset.description || copy.assetCard.noDescription}
                </p>

                {characterVariantStatus.length > 0 && (
                    <div className="mb-3 flex flex-wrap gap-2">
                        {characterVariantStatus.map((item) => (
                            <span
                                key={item.key}
                                className={`rounded-full border px-2 py-1 text-[10px] font-bold tracking-wide ${item.ready
                                    ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-300"
                                    : "border-white/10 bg-black/30 text-gray-400"
                                    }`}
                            >
                                {item.label}
                            </span>
                        ))}
                    </div>
                )}

                <div className="flex gap-2">
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
                        {isGenerating ? copy.assetCard.generating : primaryActionLabel}
                    </button>
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            onUpload?.();
                        }}
                        className="p-2 bg-white/10 hover:bg-white/20 rounded-lg text-white cursor-pointer transition-colors"
                        title={uploadActionTitle}
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
            alert(messages.common.messages.nameRequired);
            return;
        }
        setIsSubmitting(true);
        try {
            await onCreate({ name: name.trim(), description: description.trim() });
        } finally {
            setIsSubmitting(false);
        }
    };

    const typeLabel = getAssetTypeTerm(type)?.label || type;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-[#1a1a1a] border border-white/10 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl"
            >
                <div className="p-6 border-b border-white/10 flex justify-between items-center bg-black/20">
                    <div className="flex items-center gap-3">
                        <Plus className="text-primary" size={20} />
                        <h2 className="text-lg font-bold text-white">{copy.createDialog.createNew(typeLabel)}</h2>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-lg transition-colors">
                        <X size={20} className="text-gray-400" />
                    </button>
                </div>

                <div className="p-6 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-400 mb-2">{copy.createDialog.name}</label>
                        <input
                            type="text"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder={getAssetTypeTerm(type)?.namePlaceholder || typeLabel}
                            className="w-full px-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white placeholder-gray-500 focus:border-primary/50 focus:outline-none"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-400 mb-2">{copy.createDialog.description}</label>
                        <textarea
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder={getAssetTypeTerm(type)?.descriptionPlaceholder || typeLabel}
                            rows={4}
                            className="w-full px-4 py-3 bg-black/40 border border-white/10 rounded-lg text-white placeholder-gray-500 focus:border-primary/50 focus:outline-none resize-none"
                        />
                    </div>
                </div>

                <div className="p-6 border-t border-white/10 flex justify-end gap-3">
                    <button
                        onClick={onClose}
                        className="px-6 py-2 bg-white/5 hover:bg-white/10 text-white rounded-lg transition-colors"
                    >
                        {commonActions.cancel}
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={isSubmitting || !name.trim()}
                        className="px-6 py-2 bg-primary hover:bg-primary/90 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                        {isSubmitting && <RefreshCw size={16} className="animate-spin" />}
                        {copy.createDialog.create(typeLabel)}
                    </button>
                </div>
            </motion.div>
        </div>
    );
}
