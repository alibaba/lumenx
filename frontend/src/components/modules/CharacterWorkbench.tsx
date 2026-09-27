"use client";

import { useState, useEffect } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { X, RefreshCw, Check, AlertTriangle, Image as ImageIcon, Lock, Unlock, ChevronRight, Maximize2, Video } from "lucide-react";
import { api, API_URL } from "@/lib/api";

import { VariantSelector } from "../common/VariantSelector";
import { VideoVariantSelector } from "../common/VideoVariantSelector";
import { useProjectStore } from "@/store/projectStore";
import { Image as PhotoIcon } from "lucide-react";
import { getAssetUrl } from "@/lib/utils";
import MethodologyCardSection from "@/components/shared/MethodologyCardSection";


interface CharacterWorkbenchProps {
    asset: any;
    onClose: () => void;
    onUpdateDescription: (desc: string) => void;
    onGenerate: (type: string, prompt: string, applyStyle: boolean, negativePrompt: string, batchSize: number) => void;
    generatingTypes: { type: string; batchSize: number }[];
    stylePrompt?: string;
    styleNegativePrompt?: string;
    onGenerateVideo?: (prompt: string, duration: number, subType?: string) => void;
    onDeleteVideo?: (videoId: string) => void;
    isGeneratingVideo?: boolean;
}

export default function CharacterWorkbench({ asset, onClose, onUpdateDescription, onGenerate, generatingTypes = [], stylePrompt = "", styleNegativePrompt = "", onGenerateVideo, onDeleteVideo, isGeneratingVideo }: CharacterWorkbenchProps) {
    const tc = useTranslations("character");
    const [activePanel, setActivePanel] = useState<"full_body" | "three_view" | "headshot" | "video">("full_body");
    const updateProject = useProjectStore(state => state.updateProject);
    const currentProject = useProjectStore(state => state.currentProject);

    // Mode state for Asset Activation v2 (Static/Motion)
    const [fullBodyMode, setFullBodyMode] = useState<'static' | 'motion'>('static');
    const [headshotMode, setHeadshotMode] = useState<'static' | 'motion'>('static');

    // Motion Ref prompts (initialized with PRD templates)
    const [fullBodyMotionPrompt, setFullBodyMotionPrompt] = useState('');
    const [headshotMotionPrompt, setHeadshotMotionPrompt] = useState('');

    // Motion Ref audio URLs
    const [fullBodyAudioUrl, setFullBodyAudioUrl] = useState('');
    const [headshotAudioUrl, setHeadshotAudioUrl] = useState('');
    const [isUploadingAudio, setIsUploadingAudio] = useState(false);

    // Motion Ref generation state
    const [isVideoLoading, setIsVideoLoading] = useState(false);


    // === Reverse Generation: Detect uploaded images ===
    const hasUploadedThreeViews = asset.three_view_asset?.variants?.some((v: any) => v.is_uploaded_source) || false;
    const hasUploadedHeadshot = asset.headshot_asset?.variants?.some((v: any) => v.is_uploaded_source) || false;
    const hasUploadedFullBody = asset.full_body_asset?.variants?.some((v: any) => v.is_uploaded_source) || false;
    const hasAnyUpload = hasUploadedThreeViews || hasUploadedHeadshot || hasUploadedFullBody;
    const hasNonFullBodyUpload = hasUploadedThreeViews || hasUploadedHeadshot;
    const hasFullBodyImage = !!(asset.full_body_image_url || (asset.full_body_asset?.variants?.length > 0));

    // Local state for prompts
    const getInitialPrompt = (type: string, existingPrompt: string) => {
        if (existingPrompt) return existingPrompt;

        const baseDesc = asset.description || "";
        const name = asset.name || "Character";

        if (type === "full_body") {
            const prefix = hasNonFullBodyUpload ? "STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, skin tone, and clothing as the reference image. " : "";
            return `${prefix}Full body character design of ${name}, concept art. ${baseDesc}. Standing pose, neutral expression, no emotion, looking at viewer. Clean white background, isolated, no other objects, no scenery, simple background, high quality, masterpiece.`;
        }
        if (type === "three_view") {
            const prefix = (hasFullBodyImage || hasAnyUpload) ? "STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, and clothing as the reference image. " : "";
            return `${prefix}Character Reference Sheet for ${name}. ${baseDesc}. Three-view character design: Front view, Side view, and Back view. Full body, standing pose, neutral expression. Consistent clothing and details across all views. Simple white background, clean lines, studio lighting, high quality.`;
        }
        if (type === "headshot") {
            const prefix = (hasFullBodyImage || hasAnyUpload) ? "STRICTLY MAINTAIN the SAME face, hairstyle, skin tone, and facial features as the reference image. " : "";
            return `${prefix}Close-up portrait of the SAME character ${name}. ${baseDesc}. Zoom in on face and shoulders, detailed facial features, neutral expression, looking at viewer, high quality, masterpiece.`;
        }
        return "";
    };

    const [fullBodyPrompt, setFullBodyPrompt] = useState(getInitialPrompt("full_body", asset.full_body_prompt));
    const [threeViewPrompt, setThreeViewPrompt] = useState(getInitialPrompt("three_view", asset.three_view_prompt));
    const [headshotPrompt, setHeadshotPrompt] = useState(getInitialPrompt("headshot", asset.headshot_prompt));
    const [videoPrompt, setVideoPrompt] = useState(asset.video_prompt || "");

    // New State for Style Control
    const [applyStyle, setApplyStyle] = useState(true);
    // User's own negative prompt (initially empty or with sensible defaults)
    const [negativePrompt, setNegativePrompt] = useState("low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, jpeg artifacts, signature, watermark, blurry");
    // Art Direction Style expanded state (collapsed by default to save space)
    const [showStyleExpanded, setShowStyleExpanded] = useState(false);

    // Get the uploaded image URL for reverse generation reference
    const getUploadedReferenceUrl = () => {
        if (hasUploadedThreeViews) {
            const uploadedVariant = asset.three_view_asset?.variants?.find((v: any) => v.is_uploaded_source);
            return uploadedVariant?.url || asset.three_view_image_url;
        }
        if (hasUploadedHeadshot) {
            const uploadedVariant = asset.headshot_asset?.variants?.find((v: any) => v.is_uploaded_source);
            return uploadedVariant?.url || asset.headshot_image_url;
        }
        return null;
    };

    // Motion Ref generation handler with validation
    const handleGenerateMotionRef = async (assetType: 'full_body' | 'head_shot', prompt: string, audioUrl?: string) => {
        if (!onGenerateVideo) return;

        // Check if source image exists
        const hasSourceImage = assetType === 'full_body'
            ? (asset.full_body_image_url || asset.full_body_asset?.variants?.length > 0)
            : (asset.headshot_image_url || asset.headshot_asset?.variants?.length > 0);

        if (!hasSourceImage) {
            alert(tc('generateFirstStatic', { type: assetType === 'full_body' ? tc('fullBodyType') : tc('avatarType') }));
            return;
        }

        setIsVideoLoading(true); // Start loading state (will be reset by onCanPlay or if no video)
        onGenerateVideo(prompt, 5, assetType);
    };


    // Audio upload handler for Motion Ref
    const handleAudioUpload = async (file: File, assetType: 'full_body' | 'head_shot') => {
        if (!file) return;

        // Validate file type
        if (!file.type.startsWith('audio/')) {
            alert(tc('invalidAudioFile'));
            return;
        }

        // Validate file size (max 10MB)
        if (file.size > 10 * 1024 * 1024) {
            alert(tc('audioTooLarge'));
            return;
        }

        setIsUploadingAudio(true);

        try {
            const result = await api.uploadFile(file);
            const url = result.url;

            if (assetType === 'full_body') {
                setFullBodyAudioUrl(url);
                // Automatically update prompt if it's the default "counting" one
                const currentDefault = `Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures while talking, turning body 30 degrees left and right. The character is speaking naturally, counting numbers from one to five in English.\nHead to toe shot, stable camera, flat lighting.`;
                const oldDefault = `Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures while talking, turning body 30 degrees left and right to show costume details. No walking away.\nHead to toe shot, stable camera, flat lighting.`;

                if (fullBodyMotionPrompt === currentDefault || fullBodyMotionPrompt === oldDefault || !fullBodyMotionPrompt) {
                    setFullBodyMotionPrompt(`Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures, turning body 30 degrees left and right. The character is speaking naturally matching the audio, with accurate lip-sync and facial expressions.\nHead to toe shot, stable camera, flat lighting.`);
                }
            } else {
                setHeadshotAudioUrl(url);
                // Automatically update prompt if it's the default "counting" one
                const currentDefault = `High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally, counting numbers from one to five in English, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`;
                const oldDefault = `High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally matching the audio, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`;

                if (headshotMotionPrompt === currentDefault || headshotMotionPrompt === oldDefault || !headshotMotionPrompt) {
                    setHeadshotMotionPrompt(`High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally matching the audio, with accurate lip-sync and facial expressions, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`);
                }
            }
        } catch (error: any) {
            console.error('Failed to upload audio:', error);
            alert(tc('audioUploadFailed', { error: error.message }));
        } finally {
            setIsUploadingAudio(false);
        }
    };

    // PRD Motion Prompt Templates
    const getMotionDefault = (type: 'full_body' | 'headshot', hasAudio: boolean) => {
        if (type === 'full_body') {
            return hasAudio
                ? `Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures, turning body 30 degrees left and right. The character is speaking naturally matching the audio, with accurate lip-sync and facial expressions.\nHead to toe shot, stable camera, flat lighting.`
                : `Full-body character reference video.\n${asset.description}.\nStanding pose, shifting weight slightly, natural hand gestures while talking, turning body 30 degrees left and right. The character is speaking naturally, counting numbers from one to five in English.\nHead to toe shot, stable camera, flat lighting.`;
        } else {
            return hasAudio
                ? `High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally matching the audio, with accurate lip-sync and facial expressions, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`
                : `High-fidelity portrait video reference.\n${asset.description}.\nFacing camera, speaking naturally, counting numbers from one to five in English, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`;
        }
    };

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
        if (!videoPrompt) {
            setVideoPrompt(`Cinematic shot of ${asset.name}, ${asset.description}, looking around, breathing, slight movement, high quality, 4k`);
        }

        if (!fullBodyMotionPrompt) {
            setFullBodyMotionPrompt(getMotionDefault('full_body', !!fullBodyAudioUrl));
        }
        if (!headshotMotionPrompt) {
            setHeadshotMotionPrompt(getMotionDefault('headshot', !!headshotAudioUrl));
        }
    }, [asset.name, asset.description]);

    const handleResetMotionPrompt = (type: 'full_body' | 'headshot') => {
        const hasAudio = type === 'full_body' ? !!fullBodyAudioUrl : !!headshotAudioUrl;
        const defaultPrompt = getMotionDefault(type, hasAudio);
        if (type === 'full_body') {
            setFullBodyMotionPrompt(defaultPrompt);
        } else {
            setHeadshotMotionPrompt(defaultPrompt);
        }
    };


    // Update local state when asset updates (e.g. after generation)
    useEffect(() => {
        if (asset.full_body_prompt) setFullBodyPrompt(asset.full_body_prompt);
        else if (hasNonFullBodyUpload && !fullBodyPrompt.includes("STRICTLY MAINTAIN")) {
            setFullBodyPrompt(getInitialPrompt("full_body", ""));
        }

        if (asset.three_view_prompt) setThreeViewPrompt(asset.three_view_prompt);
        else if (hasAnyUpload && !threeViewPrompt.includes("STRICTLY MAINTAIN")) {
            setThreeViewPrompt(getInitialPrompt("three_view", ""));
        }

        if (asset.headshot_prompt) setHeadshotPrompt(asset.headshot_prompt);
        else if (hasAnyUpload && !headshotPrompt.includes("STRICTLY MAINTAIN")) {
            setHeadshotPrompt(getInitialPrompt("headshot", ""));
        }

        if (asset.video_prompt) setVideoPrompt(asset.video_prompt);
    }, [asset, hasAnyUpload, hasNonFullBodyUpload]);

    const handleGenerateClick = (type: "full_body" | "three_view" | "headshot", batchSize: number) => {
        let prompt = "";
        if (type === "full_body") prompt = fullBodyPrompt;
        else if (type === "three_view") prompt = threeViewPrompt;
        else if (type === "headshot") prompt = headshotPrompt;

        onGenerate(type, prompt, applyStyle, negativePrompt, batchSize);
    };

    // Helper to check if a specific type is generating
    const getGeneratingInfo = (type: string) => {
        if (!Array.isArray(generatingTypes) || generatingTypes.length === 0) {
            return { isGenerating: false, batchSize: 1 };
        }
        const task = generatingTypes.find(t => t?.type === type || t?.type === "all");
        return task ? { isGenerating: true, batchSize: task.batchSize || 1 } : { isGenerating: false, batchSize: 1 };
    };

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
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-md p-4 md:p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-surface border border-glass-border rounded-2xl w-full max-w-7xl h-[90vh] flex flex-col overflow-hidden shadow-lg"
            >
                <div className="h-16 border-b border-glass-border flex justify-between items-center px-6 bg-surface">
                    <div className="flex items-center gap-4">
                        <h2 className="text-xl font-bold text-foreground">{asset.name} <span className="text-text-muted font-normal text-sm ml-2">{tc("workbench")}</span></h2>
                        <div className="flex items-center gap-2 px-3 py-1 bg-blue-500/10 border border-blue-500/20 rounded-full">
                            <span className="text-xs text-blue-400 font-medium">{tc("tipConsistency")}</span>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-hover-bg rounded-full text-text-secondary hover:text-foreground transition-colors">
                        <X size={24} />
                    </button>
                </div>

                {/* T-B6 方法论三分离角色卡（可折叠全宽条，收起时不占空间） */}
                <div className="px-6 py-3 border-b border-glass-border bg-surface">
                    <MethodologyCardSection asset={asset} type="character" />
                </div>

                {/* Main Content - 3 Columns */}
                <div className="flex-1 flex overflow-hidden">

                    {/* Panel 1: Full Body (Master) */}
                    <WorkbenchPanel
                        title={tc("masterAsset")}
                        isActive={activePanel === "full_body"}
                        onClick={() => setActivePanel("full_body")}

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

                        // Reverse generation: Show hint if upload detected but no full body
                        reverseGenerationMode={hasNonFullBodyUpload && !hasFullBodyImage}
                        reverseReferenceUrl={getUploadedReferenceUrl()}

                        supportsMotion={true}
                        mode={fullBodyMode}
                        onModeChange={setFullBodyMode}
                        hasStaticImage={!!asset.full_body_image_url || (asset.full_body_asset?.variants?.length > 0)}
                        motionRefVideos={asset.full_body?.video_variants || []}
                        onGenerateMotionRef={(prompt: string, audioUrl?: string) => handleGenerateMotionRef('full_body', prompt, audioUrl)}
                        isGeneratingMotion={generatingTypes.some(t => t.type === "video_full_body")}
                        motionPrompt={fullBodyMotionPrompt}
                        setMotionPrompt={setFullBodyMotionPrompt}
                        audioUrl={fullBodyAudioUrl}
                        onAudioUpload={(file: File) => handleAudioUpload(file, 'full_body')}
                        isUploadingAudio={isUploadingAudio}
                        isVideoLoading={isVideoLoading}
                        setIsVideoLoading={setIsVideoLoading}
                        onResetPrompt={() => handleResetMotionPrompt('full_body')}
                    />


                    {/* Divider */}
                    <div className="w-px bg-glass flex items-center justify-center">
                        <ChevronRight size={16} className="text-text-muted" />
                    </div>

                    {/* Panel 2: Three View (Derived) */}
                    <WorkbenchPanel
                        title={tc("threeViews")}
                        isActive={activePanel === "three_view"}
                        onClick={() => setActivePanel("three_view")}

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
                        isLocked={!asset.full_body_image_url && !hasAnyUpload}
                        description="Front, side, and back views for 3D-like consistency."
                        aspectRatio="16:9"
                    />

                    {/* Divider */}
                    <div className="w-px bg-glass flex items-center justify-center">
                        <ChevronRight size={16} className="text-text-muted" />
                    </div>

                    {/* Panel 3: Headshot (Derived) */}
                    <WorkbenchPanel
                        title={tc("avatar")}
                        isActive={activePanel === "headshot"}
                        onClick={() => setActivePanel("headshot")}

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
                        isLocked={!asset.full_body_image_url && !hasAnyUpload}
                        description="Close-up facial details and expressions."
                        aspectRatio="1:1"

                        supportsMotion={true}
                        mode={headshotMode}
                        onModeChange={setHeadshotMode}
                        hasStaticImage={!!asset.headshot_image_url || (asset.headshot_asset?.variants?.length > 0)}
                        motionRefVideos={asset.head_shot?.video_variants || []}
                        onGenerateMotionRef={(prompt: string, audioUrl?: string) => handleGenerateMotionRef('head_shot', prompt, audioUrl)}
                        isGeneratingMotion={generatingTypes.some(t => t.type === "video_head_shot")}
                        motionPrompt={headshotMotionPrompt}
                        setMotionPrompt={setHeadshotMotionPrompt}
                        audioUrl={headshotAudioUrl}
                        onAudioUpload={(file: File) => handleAudioUpload(file, 'head_shot')}
                        isUploadingAudio={isUploadingAudio}
                        isVideoLoading={isVideoLoading}
                        setIsVideoLoading={setIsVideoLoading}
                        onResetPrompt={() => handleResetMotionPrompt('headshot')}
                    />


                </div>

                {/* Footer: Negative Prompt & Art Direction Settings */}
                <div className="border-t border-glass-border bg-surface flex flex-col">
                    {/* Top Row: User's Negative Prompt + Apply Style Toggle */}
                    <div className="px-6 py-3 flex items-start gap-4">
                        {/* User's Negative Prompt (Editable) */}
                        <div className="flex-1">
                            <label className="text-xs font-bold text-text-muted uppercase mb-2 block">{tc("workbench")}</label>
                            <textarea
                                value={negativePrompt}
                                onChange={(e) => setNegativePrompt(e.target.value)}
                                className="w-full h-16 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-text-secondary resize-none focus:outline-none focus:border-primary/50 font-mono"
                                placeholder="Enter your negative prompt (avoid unwanted elements)..."
                            />
                        </div>

                        {/* Apply Style Toggle */}
                        <div className="pt-6">
                            <div className="flex items-center gap-2 bg-surface px-4 py-2 rounded-lg border border-glass-border">
                                <input
                                    type="checkbox"
                                    id="applyStyleFooter"
                                    checked={applyStyle}
                                    onChange={(e) => setApplyStyle(e.target.checked)}
                                    className="rounded border-gray-600 bg-gray-700 text-primary focus:ring-primary w-4 h-4"
                                />
                                <label htmlFor="applyStyleFooter" className="text-xs font-bold text-text-secondary cursor-pointer select-none whitespace-nowrap">
                                    {tc("workbench")}
                                </label>
                            </div>
                        </div>
                    </div>

                    {/* Art Direction Style Display (Collapsible) - Only show toggle when style exists */}
                    {applyStyle && (stylePrompt || styleNegativePrompt) && (
                        <div className="border-t border-border-subtle">
                            <button
                                onClick={() => setShowStyleExpanded(!showStyleExpanded)}
                                className="w-full px-6 py-2 flex items-center justify-between hover:bg-glass transition-colors"
                            >
                                <div className="flex items-center gap-2">
                                    <div className="w-2 h-2 rounded-full bg-gradient-to-r from-purple-500 to-blue-500" />
                                    <span className="text-xs font-bold text-text-secondary uppercase">Art Direction Style (Will Be Appended)</span>
                                </div>
                                <ChevronRight size={14} className={`text-text-muted transform transition-transform ${showStyleExpanded ? 'rotate-90' : ''}`} />
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
                                            <div className="bg-gradient-to-r from-purple-500/10 to-blue-500/10 border border-glass-border rounded-lg p-4">
                                                {stylePrompt && (
                                                    <div className="mb-3">
                                                        <span className="text-xs font-bold text-green-400 block mb-1">+ Style Prompt:</span>
                                                        <p className="text-xs text-text-secondary font-mono bg-surface p-2 rounded border border-border-subtle leading-relaxed">
                                                            {stylePrompt}
                                                        </p>
                                                    </div>
                                                )}

                                                {styleNegativePrompt && (
                                                    <div>
                                                        <span className="text-xs font-bold text-red-400 block mb-1">+ Negative Prompt:</span>
                                                        <p className="text-xs text-text-secondary font-mono bg-surface p-2 rounded border border-border-subtle leading-relaxed">
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
    aspectRatio = "9:16",
    // Video specific
    isVideo = false,
    videos,
    onDeleteVideo,
    onGenerateVideo,

    // Motion Ref Mode (Asset Activation v2)
    supportsMotion = false,
    mode = 'static',  // 'static' | 'motion'
    onModeChange,
    hasStaticImage = false,
    motionRefVideos = [],
    onGenerateMotionRef,
    isGeneratingMotion = false,
    motionPrompt = '',
    setMotionPrompt,
    audioUrl = '',
    onAudioUpload,
    isUploadingAudio = false,
    isVideoLoading = false,
    setIsVideoLoading,
    onResetPrompt,
    // Reverse Generation Props
    reverseGenerationMode = false,
    reverseReferenceUrl = null
}: any) {
    const tc = useTranslations("character");

    return (
        <div
            className={`flex-1 flex flex-col min-w-[300px] transition-colors ${isActive ? 'bg-glass' : 'bg-transparent hover:bg-hover-bg'}`}
            onClick={onClick}
        >
            {/* Panel Header */}
            <div className="p-4 border-b border-border-subtle">
                <div className="flex items-center justify-between mb-1">
                    <h3 className={`font-bold text-sm uppercase tracking-wider ${isActive ? 'text-primary' : 'text-text-secondary'}`}>
                        {title}
                    </h3>

                    {/* Mode Switcher (Asset Activation v2) */}
                    {supportsMotion && (
                        <div className="flex items-center gap-1 bg-surface p-1 rounded-lg border border-glass-border">
                            <button
                                onClick={(e) => { e.stopPropagation(); onModeChange?.('static'); }}
                                className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors ${mode === 'static'
                                    ? 'bg-primary/20 text-primary'
                                    : 'text-text-secondary hover:text-foreground'
                                    }`}
                            >
                                <PhotoIcon size={12} />
                                Static
                            </button>
                            <button
                                onClick={(e) => {
                                    e.stopPropagation();
                                    if (!hasStaticImage) {
                                        alert(tc('generateFirstStatic', { type: tc('fullBodyType') }));
                                        return;
                                    }
                                    onModeChange?.('motion');
                                }}
                                className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors ${mode === 'motion'
                                    ? 'bg-purple-500/20 text-purple-400'
                                    : 'text-text-secondary hover:text-foreground'
                                    }`}
                            >
                                <Video size={12} />
                                {tc("motionMode")}
                            </button>
                        </div>
                    )}
                </div>
                <p className="text-xs text-text-muted">{description}</p>
            </div>

            {/* Image Area with Variant Selector */}
            <div className="flex-1 relative bg-surface p-4 flex flex-col overflow-y-auto group">

                {/* Locked Overlay */}
                {isLocked && (
                    <div className="absolute inset-0 bg-overlay z-20 flex items-center justify-center text-center p-6">
                        <div className="text-text-muted flex flex-col items-center gap-2">
                            <Lock size={32} />
                            <span className="text-sm">Generate Master Asset first</span>
                        </div>
                    </div>
                )}

                {/* Reverse Generation Hint - shown in Full Body panel when upload detected */}
                {reverseGenerationMode && (
                    <div className="absolute inset-0 bg-gradient-to-b from-primary/10 to-transparent z-10 flex flex-col items-center justify-center text-center p-6 pointer-events-none">
                        <div className="flex flex-col items-center gap-3 bg-overlay backdrop-blur-md rounded-xl p-6 border border-primary/30 pointer-events-auto">
                            <div className="flex items-center gap-2 text-primary">
                                <RefreshCw size={20} />
                                <span className="text-sm font-bold">Upload Detected</span>
                            </div>
                            <p className="text-xs text-text-secondary max-w-[200px]">
                                Generate Full Body from your uploaded reference image
                            </p>
                            {reverseReferenceUrl && (
                                <img
                                    src={typeof reverseReferenceUrl === 'string' && reverseReferenceUrl.startsWith('http')
                                        ? reverseReferenceUrl
                                        : `${window.location.origin}/${reverseReferenceUrl}`}
                                    alt="Reference"
                                    className="w-16 h-16 rounded-lg object-cover border border-glass-border"
                                />
                            )}
                        </div>
                    </div>
                )}

                {/* Variant Selector / Motion Ref Content */}
                <div className="flex-1 overflow-y-auto overflow-x-hidden scrollbar-thin scrollbar-thumb-gray-700">
                    {mode === 'motion' && supportsMotion ? (
                        /* Motion Ref Mode Content - Matching Static Style */
                        <div className="flex flex-col gap-4 p-4">
                            {/* Header with gradient accent */}
                            <div className="flex items-center gap-2 pb-2 border-b border-purple-500/20">
                                <div className="w-1 h-4 bg-gradient-to-b from-purple-400 to-pink-500 rounded-full"></div>
                                <span className="text-xs font-bold text-purple-300 uppercase tracking-wider">{tc("motionRef")}</span>
                            </div>

                            {/* Video Player with glassmorphism */}
                            <div className={`relative w-full ${aspectRatio === '9:16' ? 'aspect-[9/16] max-h-[40vh]' : aspectRatio === '1:1' ? 'aspect-square max-h-[35vh]' : 'aspect-video'} bg-gradient-to-br from-overlay to-black/60 rounded-xl overflow-hidden border border-border-subtle shadow-xl backdrop-blur-sm`}>
                                {isGeneratingMotion ? (
                                    <div className="absolute inset-0 z-10 bg-overlay backdrop-blur-md flex flex-col items-center justify-center gap-4">
                                        <div className="relative">
                                            <RefreshCw size={48} className="text-purple-400 animate-spin" />
                                            <div className="absolute inset-0 blur-xl bg-purple-500/30 animate-pulse"></div>
                                        </div>
                                        <div className="flex flex-col items-center">
                                            <span className="text-sm font-bold text-foreground uppercase tracking-widest animate-pulse">Generating Video</span>
                                            <span className="text-[0.625rem] text-purple-300/60 mt-1">AI is processing motion...</span>
                                        </div>
                                    </div>
                                ) : isVideoLoading && motionRefVideos?.length > 0 ? (
                                    <div className="absolute inset-0 z-10 bg-overlay backdrop-blur-sm flex flex-col items-center justify-center gap-3">
                                        <RefreshCw size={32} className="text-text-secondary animate-spin" />
                                        <span className="text-xs text-text-secondary font-medium">Loading Video File...</span>
                                    </div>
                                ) : null}

                                {motionRefVideos?.length > 0 ? (
                                    <video
                                        key={motionRefVideos[motionRefVideos.length - 1]?.url}
                                        src={getAssetUrl(motionRefVideos[motionRefVideos.length - 1]?.url)}
                                        onCanPlay={() => setIsVideoLoading(false)}
                                        onLoadStart={() => setIsVideoLoading(true)}
                                        className="w-full h-full object-contain"
                                        controls
                                        loop
                                        autoPlay
                                        muted
                                    />
                                ) : !isGeneratingMotion && (
                                    <div className="w-full h-full flex flex-col items-center justify-center text-text-muted gap-2">
                                        <Video size={40} className="opacity-50" />
                                        <span className="text-sm">No motion reference yet</span>
                                        <span className="text-xs opacity-70">Generate one below</span>
                                    </div>
                                )}
                            </div>

                            <div className="bg-surface rounded-lg border border-glass-border p-3">
                                <label className="text-xs font-bold text-text-muted uppercase mb-2 block">Audio Input (Optional)</label>
                                <p className="text-xs text-text-muted mb-3">Upload audio to drive lip-sync or body rhythm</p>

                                <label className={`flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-dashed cursor-pointer transition-all ${audioUrl
                                    ? 'border-green-500/50 bg-green-500/10 text-green-400'
                                    : 'border-indigo-500/30 hover:border-indigo-400/50 hover:bg-indigo-500/5 text-text-secondary'
                                    }`}>
                                    <input
                                        type="file"
                                        accept="audio/*"
                                        className="hidden"
                                        onChange={(e) => {
                                            const file = e.target.files?.[0];
                                            if (file) onAudioUpload?.(file);
                                        }}
                                        disabled={isUploadingAudio}
                                    />
                                    {isUploadingAudio ? (
                                        <>
                                            <div className="animate-spin rounded-full h-4 w-4 border-2 border-primary/30 border-t-primary"></div>
                                            <span className="text-xs">Uploading...</span>
                                        </>
                                    ) : audioUrl ? (
                                        <>
                                            <Check size={14} />
                                            <span className="text-xs font-medium">Audio Uploaded</span>
                                        </>
                                    ) : (
                                        <>
                                            <ImageIcon size={14} />
                                            <span className="text-xs">Upload Audio File</span>
                                        </>
                                    )}
                                </label>
                            </div>

                            {/* Motion Prompt */}
                            <div className="flex flex-col gap-2">
                                <div className="flex items-center justify-between">
                                    <label className="text-xs font-bold text-text-muted uppercase">Motion Prompt</label>
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            onResetPrompt?.();
                                        }}
                                        className="text-[0.625rem] text-primary hover:text-primary/80 transition-colors flex items-center gap-1"
                                        title="Reset to recommended prompt"
                                    >
                                        <RefreshCw size={10} />
                                        Reset
                                    </button>
                                </div>
                                <textarea
                                    value={motionPrompt}
                                    onChange={(e) => setMotionPrompt?.(e.target.value)}
                                    className="w-full h-24 bg-input-bg border border-glass-border rounded-lg p-3 text-xs text-text-secondary resize-none focus:outline-none focus:border-primary/50 font-mono leading-relaxed"
                                    placeholder="Describe the motion you want..."
                                />
                            </div>

                            {/* Generate Button */}
                            <button
                                onClick={() => onGenerateMotionRef?.(motionPrompt, audioUrl)}
                                disabled={isGeneratingMotion}
                                className={`flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${isGeneratingMotion
                                    ? 'bg-gray-700 text-text-muted cursor-not-allowed'
                                    : 'bg-primary hover:bg-primary/90 text-foreground shadow-lg'
                                    }`}
                            >
                                <Video size={16} />
                                Generate Motion Reference
                            </button>
                        </div>
                    ) : isVideo ? (
                        <VideoVariantSelector
                            videos={videos}
                            onDelete={onDeleteVideo}
                            onGenerate={onGenerateVideo}
                            isGenerating={isGenerating}
                            aspectRatio={aspectRatio}
                            className="h-full"
                        />
                    ) : (
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
                    )}
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
            <div className="h-1/3 border-t border-glass-border flex flex-col bg-surface">
                <div className="p-2 border-b border-border-subtle flex justify-between items-center bg-surface">
                    <span className="text-xs font-bold text-text-muted uppercase px-2">Prompt</span>
                </div>
                <textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    disabled={isLocked}
                    className="flex-1 w-full bg-transparent p-4 text-xs text-text-secondary resize-none focus:outline-none focus:bg-glass font-mono leading-relaxed"
                    placeholder="Enter prompt description..."
                />
            </div>
        </div>
    );
}
