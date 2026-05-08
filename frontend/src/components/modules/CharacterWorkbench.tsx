"use client";

import { useState, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import NextImage from "next/image";
import { X, RefreshCw, Check, Image as ImageIcon, Lock, ChevronRight, Video } from "lucide-react";
import { api } from "@/lib/api";

import { VariantSelector } from "../common/VariantSelector";
import { VideoVariantSelector } from "../common/VideoVariantSelector";
import { useProjectStore } from "@/store/projectStore";
import type { Character, ImageAsset, ImageVariant, VideoTask } from "@/store/projectStore";
import { Image as PhotoIcon } from "lucide-react";
import { getAssetUrl } from "@/lib/utils";
import { messages } from "@/lib/i18n";
import PromptQualityPanel from "../common/PromptQualityPanel";
import {
    applyImagePromptBlock,
    buildDefaultImagePrompt,
    getImagePromptScaffolds,
    getImagePromptTemplates,
    type ImagePromptBlock,
} from "@/lib/image-prompt-recipes";
import {
    formatPromptIssues,
    hasBlockingPromptIssues,
    inspectImagePrompt,
    type PromptQualityIssue,
} from "@/lib/prompt-quality";

const copy = messages.modules.characterWorkbench;

type CharacterPromptType = "full_body" | "three_view" | "headshot";
type MotionPromptType = "full_body" | "headshot";
type CharacterMotionAssetType = "full_body" | "head_shot";
type WorkbenchMode = "static" | "motion";

interface UploadedImageVariant extends ImageVariant {
    is_uploaded_source?: boolean;
}

interface CharacterImageAsset extends Omit<ImageAsset, "variants"> {
    variants: UploadedImageVariant[];
}

interface MotionReferenceVideo {
    id?: string;
    url: string;
    thumbnail?: string;
    created_at?: number;
}

interface LegacyMotionContainer {
    video_variants?: MotionReferenceVideo[];
}

interface CharacterWorkbenchAsset extends Omit<Character, "full_body_asset" | "three_view_asset" | "headshot_asset" | "full_body" | "head_shot"> {
    full_body_asset?: CharacterImageAsset;
    three_view_asset?: CharacterImageAsset;
    headshot_asset?: CharacterImageAsset;
    full_body?: LegacyMotionContainer;
    head_shot?: LegacyMotionContainer;
    full_body_prompt?: string;
    three_view_prompt?: string;
    headshot_prompt?: string;
}

interface GeneratingType {
    type: string;
    batchSize: number;
}

interface WorkbenchPanelProps {
    title: string;
    isActive: boolean;
    onClick: () => void;
    asset?: ImageAsset;
    currentImageUrl?: string;
    onSelect: (variantId: string) => void;
    onDelete: (variantId: string) => void;
    onFavorite?: (variantId: string, isFavorited: boolean) => void;
    prompt: string;
    setPrompt: (value: string) => void;
    onGenerate: (batchSize: number) => void;
    scaffolds?: ImagePromptBlock[];
    templates?: ImagePromptBlock[];
    promptIssues?: PromptQualityIssue[];
    isGenerating: boolean;
    generatingBatchSize?: number;
    status?: string;
    isLocked?: boolean;
    description: string;
    aspectRatio?: string;
    isVideo?: boolean;
    videos?: VideoTask[];
    onDeleteVideo?: (videoId: string) => void;
    onGenerateVideo?: (duration: number) => void;
    supportsMotion?: boolean;
    mode?: WorkbenchMode;
    onModeChange?: (mode: WorkbenchMode) => void;
    hasStaticImage?: boolean;
    motionRefVideos?: MotionReferenceVideo[];
    onGenerateMotionRef?: (prompt: string, audioUrl?: string) => void;
    isGeneratingMotion?: boolean;
    motionPrompt?: string;
    setMotionPrompt?: (value: string) => void;
    audioUrl?: string;
    onAudioUpload?: (file: File) => void;
    isUploadingAudio?: boolean;
    isVideoLoading?: boolean;
    setIsVideoLoading?: (value: boolean) => void;
    onResetPrompt?: () => void;
    reverseGenerationMode?: boolean;
    reverseReferenceUrl?: string | null;
}

function applyPromptTemplate(
    currentPrompt: string,
    nextPrompt: string,
    setPrompt: (value: string) => void,
    mode: "replace" | "append" = "replace",
) {
    setPrompt(applyImagePromptBlock(currentPrompt, nextPrompt, mode));
}

function hasReferenceLock(prompt: string) {
    const normalized = prompt.toLowerCase();
    return normalized.includes("strictly preserve") || normalized.includes("reference image");
}

function buildMotionReferencePrompt(type: MotionPromptType, description: string, hasAudio: boolean) {
    if (type === "full_body") {
        return hasAudio
            ? `Full-body character reference video.\n${description}.\nStanding pose, shifting weight slightly, natural hand gestures, turning body 30 degrees left and right. The character is speaking naturally matching the audio, with accurate lip-sync and facial expressions.\nHead to toe shot, stable camera, flat lighting.`
            : `Full-body character reference video.\n${description}.\nStanding pose, shifting weight slightly, natural hand gestures while talking, turning body 30 degrees left and right. The character is speaking naturally, counting numbers from one to five in English.\nHead to toe shot, stable camera, flat lighting.`;
    }

    return hasAudio
        ? `High-fidelity portrait video reference.\n${description}.\nFacing camera, speaking naturally matching the audio, with accurate lip-sync and facial expressions, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`
        : `High-fidelity portrait video reference.\n${description}.\nFacing camera, speaking naturally, counting numbers from one to five in English, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`;
}

interface CharacterWorkbenchProps {
    asset: CharacterWorkbenchAsset;
    onClose: () => void;
    onUpdateDescription: (desc: string) => void;
    onGenerate: (type: string, prompt: string, applyStyle: boolean, negativePrompt: string, batchSize: number) => void;
    generatingTypes: GeneratingType[];
    stylePrompt?: string;
    styleNegativePrompt?: string;
    onGenerateVideo?: (prompt: string, duration: number, subType?: string, audioUrl?: string) => void;
    onDeleteVideo?: (videoId: string) => void;
    isGeneratingVideo?: boolean;
}

export default function CharacterWorkbench({ asset, onClose, onGenerate, generatingTypes = [], stylePrompt = "", styleNegativePrompt = "", onGenerateVideo }: CharacterWorkbenchProps) {
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
    const hasUploadedThreeViews = asset.three_view_asset?.variants?.some((variant) => variant.is_uploaded_source) || false;
    const hasUploadedHeadshot = asset.headshot_asset?.variants?.some((variant) => variant.is_uploaded_source) || false;
    const hasUploadedFullBody = asset.full_body_asset?.variants?.some((variant) => variant.is_uploaded_source) || false;
    const hasAnyUpload = hasUploadedThreeViews || hasUploadedHeadshot || hasUploadedFullBody;
    const hasNonFullBodyUpload = hasUploadedThreeViews || hasUploadedHeadshot;
    const hasFullBodyImage = !!(asset.full_body_image_url || ((asset.full_body_asset?.variants?.length ?? 0) > 0));
    const hasThreeViewImage = !!(asset.three_view_image_url || ((asset.three_view_asset?.variants?.length ?? 0) > 0));
    const hasHeadshotImage = !!(asset.headshot_image_url || asset.avatar_url || ((asset.headshot_asset?.variants?.length ?? 0) > 0));
    const generatedCoreCount = [hasFullBodyImage, hasThreeViewImage, hasHeadshotImage].filter(Boolean).length;
    const isGeneratingCoreSet = generatingTypes.some((task) =>
        ["all", "full_body", "three_view", "headshot"].includes(task?.type)
    );

    const characterName = asset.name || "Character";
    const characterDescription = asset.description || "";

    const fullBodyDefaultPrompt = useMemo(() => buildDefaultImagePrompt({
        target: "full_body",
        name: characterName,
        description: characterDescription,
        strictReference: hasNonFullBodyUpload,
        stylePrompt,
    }), [characterName, characterDescription, hasNonFullBodyUpload, stylePrompt]);

    const threeViewDefaultPrompt = useMemo(() => buildDefaultImagePrompt({
        target: "three_view",
        name: characterName,
        description: characterDescription,
        strictReference: Boolean(hasFullBodyImage || hasAnyUpload),
        stylePrompt,
    }), [characterName, characterDescription, hasFullBodyImage, hasAnyUpload, stylePrompt]);

    const headshotDefaultPrompt = useMemo(() => buildDefaultImagePrompt({
        target: "headshot",
        name: characterName,
        description: characterDescription,
        strictReference: Boolean(hasFullBodyImage || hasAnyUpload),
        stylePrompt,
    }), [characterName, characterDescription, hasFullBodyImage, hasAnyUpload, stylePrompt]);

    // Local state for prompts
    const [fullBodyPrompt, setFullBodyPrompt] = useState(asset.full_body_prompt || fullBodyDefaultPrompt);
    const [threeViewPrompt, setThreeViewPrompt] = useState(asset.three_view_prompt || threeViewDefaultPrompt);
    const [headshotPrompt, setHeadshotPrompt] = useState(asset.headshot_prompt || headshotDefaultPrompt);

    // New State for Style Control
    const [applyStyle, setApplyStyle] = useState(true);
    // User's own negative prompt (initially empty or with sensible defaults)
    const [negativePrompt, setNegativePrompt] = useState("low quality, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, jpeg artifacts, signature, watermark, blurry");
    // Art Direction Style expanded state (collapsed by default to save space)
    const [showStyleExpanded, setShowStyleExpanded] = useState(false);
    const fullBodyScaffolds = getImagePromptScaffolds({
        target: "full_body",
        stylePrompt,
        description: asset.description,
    });
    const threeViewScaffolds = getImagePromptScaffolds({
        target: "three_view",
        stylePrompt,
        description: asset.description,
    });
    const headshotScaffolds = getImagePromptScaffolds({
        target: "headshot",
        stylePrompt,
        description: asset.description,
    });
    const fullBodyTemplates = getImagePromptTemplates({
        target: "full_body",
        stylePrompt,
        description: asset.description,
    });
    const threeViewTemplates = getImagePromptTemplates({
        target: "three_view",
        stylePrompt,
        description: asset.description,
    });
    const headshotTemplates = getImagePromptTemplates({
        target: "headshot",
        stylePrompt,
        description: asset.description,
    });
    const fullBodyIssues = inspectImagePrompt({
        prompt: fullBodyPrompt,
        target: "full_body",
        stylePrompt,
    });
    const threeViewIssues = inspectImagePrompt({
        prompt: threeViewPrompt,
        target: "three_view",
        stylePrompt,
    });
    const headshotIssues = inspectImagePrompt({
        prompt: headshotPrompt,
        target: "headshot",
        stylePrompt,
    });

    // Get the uploaded image URL for reverse generation reference
    const getUploadedReferenceUrl = () => {
        if (hasUploadedThreeViews) {
            const uploadedVariant = asset.three_view_asset?.variants?.find((variant) => variant.is_uploaded_source);
            return uploadedVariant?.url || asset.three_view_image_url;
        }
        if (hasUploadedHeadshot) {
            const uploadedVariant = asset.headshot_asset?.variants?.find((variant) => variant.is_uploaded_source);
            return uploadedVariant?.url || asset.headshot_image_url;
        }
        return null;
    };

    // Motion Ref generation handler with validation
    const handleGenerateMotionRef = async (assetType: CharacterMotionAssetType, prompt: string, audioUrl?: string) => {
        if (!onGenerateVideo) return;

        // Check if source image exists
        const hasSourceImage = assetType === 'full_body'
            ? (asset.full_body_image_url || ((asset.full_body_asset?.variants?.length ?? 0) > 0))
            : (asset.headshot_image_url || ((asset.headshot_asset?.variants?.length ?? 0) > 0));

        if (!hasSourceImage) {
            const typeLabel = assetType === "full_body" ? copy.sourceImageTypes.fullBody : copy.sourceImageTypes.headshot;
            alert(copy.missingSourceImage(typeLabel));
            return;
        }

        setIsVideoLoading(true); // Start loading state (will be reset by onCanPlay or if no video)
        onGenerateVideo(prompt, 5, assetType, audioUrl);
    };


    // Audio upload handler for Motion Ref
    const handleAudioUpload = async (file: File, assetType: 'full_body' | 'head_shot') => {
        if (!file) return;

        // Validate file type
        if (!file.type.startsWith('audio/')) {
            alert(copy.audioFileInvalid);
            return;
        }

        // Validate file size (max 10MB)
        if (file.size > 10 * 1024 * 1024) {
            alert(copy.audioFileTooLarge);
            return;
        }

        setIsUploadingAudio(true);

        try {
            const result = await api.uploadFile(file);
            const url = result.url;

            if (assetType === 'full_body') {
                setFullBodyAudioUrl(url);
                // Automatically update prompt if it's the default "counting" one
                const currentDefault = buildMotionReferencePrompt("full_body", characterDescription, false);
                const oldDefault = `Full-body character reference video.\n${characterDescription}.\nStanding pose, shifting weight slightly, natural hand gestures while talking, turning body 30 degrees left and right to show costume details. No walking away.\nHead to toe shot, stable camera, flat lighting.`;

                if (fullBodyMotionPrompt === currentDefault || fullBodyMotionPrompt === oldDefault || !fullBodyMotionPrompt) {
                    setFullBodyMotionPrompt(buildMotionReferencePrompt("full_body", characterDescription, true));
                }
            } else {
                setHeadshotAudioUrl(url);
                // Automatically update prompt if it's the default "counting" one
                const currentDefault = buildMotionReferencePrompt("headshot", characterDescription, false);
                const oldDefault = `High-fidelity portrait video reference.\n${characterDescription}.\nFacing camera, speaking naturally matching the audio, subtle head movements, blinking, rich micro-expressions.\n4k, studio lighting, stable camera.`;

                if (headshotMotionPrompt === currentDefault || headshotMotionPrompt === oldDefault || !headshotMotionPrompt) {
                    setHeadshotMotionPrompt(buildMotionReferencePrompt("headshot", characterDescription, true));
                }
            }
        } catch (error: unknown) {
            console.error('Failed to upload audio:', error);
            const message = error instanceof Error ? error.message : String(error);
            alert(copy.audioUploadFailed(message));
        } finally {
            setIsUploadingAudio(false);
        }
    };

    // Initialize motion prompts if empty (first time load)
    useEffect(() => {
        setFullBodyMotionPrompt((current) => current || buildMotionReferencePrompt("full_body", characterDescription, Boolean(fullBodyAudioUrl)));
        setHeadshotMotionPrompt((current) => current || buildMotionReferencePrompt("headshot", characterDescription, Boolean(headshotAudioUrl)));
    }, [characterDescription, fullBodyAudioUrl, headshotAudioUrl]);

    const handleResetMotionPrompt = (type: MotionPromptType) => {
        const hasAudio = type === 'full_body' ? !!fullBodyAudioUrl : !!headshotAudioUrl;
        const defaultPrompt = buildMotionReferencePrompt(type, characterDescription, hasAudio);
        if (type === 'full_body') {
            setFullBodyMotionPrompt(defaultPrompt);
        } else {
            setHeadshotMotionPrompt(defaultPrompt);
        }
    };


    // Update local state when asset updates (e.g. after generation)
    useEffect(() => {
        setFullBodyPrompt((current) => {
            if (asset.full_body_prompt) return asset.full_body_prompt;
            if (hasNonFullBodyUpload && !hasReferenceLock(current)) return fullBodyDefaultPrompt;
            return current || fullBodyDefaultPrompt;
        });

        setThreeViewPrompt((current) => {
            if (asset.three_view_prompt) return asset.three_view_prompt;
            if (hasAnyUpload && !hasReferenceLock(current)) return threeViewDefaultPrompt;
            return current || threeViewDefaultPrompt;
        });

        setHeadshotPrompt((current) => {
            if (asset.headshot_prompt) return asset.headshot_prompt;
            if (hasAnyUpload && !hasReferenceLock(current)) return headshotDefaultPrompt;
            return current || headshotDefaultPrompt;
        });

    }, [
        asset.full_body_prompt,
        asset.three_view_prompt,
        asset.headshot_prompt,
        fullBodyDefaultPrompt,
        threeViewDefaultPrompt,
        headshotDefaultPrompt,
        hasAnyUpload,
        hasNonFullBodyUpload,
    ]);

    const handleGenerateClick = (type: CharacterPromptType, batchSize: number) => {
        let prompt = "";
        let issues = fullBodyIssues;
        if (type === "full_body") prompt = fullBodyPrompt;
        else if (type === "three_view") {
            prompt = threeViewPrompt;
            issues = threeViewIssues;
        } else if (type === "headshot") {
            prompt = headshotPrompt;
            issues = headshotIssues;
        }

        if (hasBlockingPromptIssues(issues)) {
            alert(`${copy.promptQualityBlocked}\n\n${formatPromptIssues(issues.filter((issue) => issue.severity === "error"))}`);
            return;
        }

        onGenerate(type, prompt, applyStyle, negativePrompt, batchSize);
    };

    const handleGenerateAllClick = () => {
        if (hasBlockingPromptIssues(fullBodyIssues)) {
            alert(`${copy.promptQualityBlocked}\n\n${formatPromptIssues(fullBodyIssues.filter((issue) => issue.severity === "error"))}`);
            return;
        }
        onGenerate("all", fullBodyPrompt, applyStyle, negativePrompt, 1);
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
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-md p-4 md:p-8">
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-[#1a1a1a] border border-white/10 rounded-2xl w-full max-w-7xl h-[90vh] flex flex-col overflow-hidden shadow-2xl"
            >
                <div className="h-16 border-b border-white/10 flex justify-between items-center px-6 bg-black/20">
                    <div className="flex items-center gap-4">
                        <h2 className="text-xl font-bold text-white">{asset.name} <span className="text-gray-500 font-normal text-sm ml-2">{copy.title}</span></h2>
                        <div className="flex items-center gap-2 px-3 py-1 bg-blue-500/10 border border-blue-500/20 rounded-full">
                            <span className="text-xs text-blue-400 font-medium">{copy.consistencyTip}</span>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-full text-gray-400 hover:text-white transition-colors">
                        <X size={24} />
                    </button>
                </div>

                <div className="border-b border-white/5 bg-gradient-to-r from-primary/10 via-blue-500/5 to-transparent px-6 py-3 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <p className="text-xs text-gray-300 leading-relaxed max-w-3xl">
                        {copy.generateAllHint(generatedCoreCount)}
                    </p>
                    <button
                        onClick={handleGenerateAllClick}
                        disabled={isGeneratingCoreSet}
                        className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-bold transition-colors ${isGeneratingCoreSet
                            ? "bg-white/10 text-gray-400 cursor-not-allowed"
                            : "bg-primary hover:bg-primary/90 text-white shadow-lg shadow-primary/20"
                            }`}
                    >
                        <RefreshCw size={16} className={isGeneratingCoreSet ? "animate-spin" : ""} />
                        {isGeneratingCoreSet ? copy.generatingAll : copy.generateAll}
                    </button>
                </div>

                {/* Main Content - 3 Columns */}
                <div className="flex-1 flex overflow-hidden">

                    {/* Panel 1: Full Body (Master) */}
                    <WorkbenchPanel
                        title={copy.panels.fullBody}
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
                        scaffolds={fullBodyScaffolds}
                        templates={fullBodyTemplates}
                        promptIssues={fullBodyIssues}
                        isGenerating={getGeneratingInfo("full_body").isGenerating}
                        generatingBatchSize={getGeneratingInfo("full_body").batchSize}
                        description={copy.descriptions.fullBody}
                        aspectRatio="9:16"

                        // Reverse generation: Show hint if upload detected but no full body
                        reverseGenerationMode={hasNonFullBodyUpload && !hasFullBodyImage}
                        reverseReferenceUrl={getUploadedReferenceUrl()}

                        supportsMotion={true}
                        mode={fullBodyMode}
                        onModeChange={setFullBodyMode}
                        hasStaticImage={!!asset.full_body_image_url || ((asset.full_body_asset?.variants?.length ?? 0) > 0)}
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
                    <div className="w-px bg-white/10 flex items-center justify-center">
                        <ChevronRight size={16} className="text-gray-600" />
                    </div>

                    {/* Panel 2: Three View (Derived) */}
                    <WorkbenchPanel
                        title={copy.panels.threeView}
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
                        scaffolds={threeViewScaffolds}
                        templates={threeViewTemplates}
                        promptIssues={threeViewIssues}
                        isGenerating={getGeneratingInfo("three_view").isGenerating}
                        generatingBatchSize={getGeneratingInfo("three_view").batchSize}
                        isLocked={!asset.full_body_image_url && !hasAnyUpload}
                        description={copy.descriptions.threeView}
                        aspectRatio="16:9"
                    />

                    {/* Divider */}
                    <div className="w-px bg-white/10 flex items-center justify-center">
                        <ChevronRight size={16} className="text-gray-600" />
                    </div>

                    {/* Panel 3: Headshot (Derived) */}
                    <WorkbenchPanel
                        title={copy.panels.headshot}
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
                        scaffolds={headshotScaffolds}
                        templates={headshotTemplates}
                        promptIssues={headshotIssues}
                        isGenerating={getGeneratingInfo("headshot").isGenerating}
                        generatingBatchSize={getGeneratingInfo("headshot").batchSize}
                        isLocked={!asset.full_body_image_url && !hasAnyUpload}
                        description={copy.descriptions.headshot}
                        aspectRatio="1:1"

                        supportsMotion={true}
                        mode={headshotMode}
                        onModeChange={setHeadshotMode}
                        hasStaticImage={!!asset.headshot_image_url || ((asset.headshot_asset?.variants?.length ?? 0) > 0)}
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
                <div className="border-t border-white/10 bg-[#111] flex flex-col">
                    {/* Top Row: User's Negative Prompt + Apply Style Toggle */}
                    <div className="px-6 py-3 flex items-start gap-4">
                        {/* User's Negative Prompt (Editable) */}
                        <div className="flex-1">
                            <label className="text-xs font-bold text-gray-500 uppercase mb-2 block">{copy.yourNegativePrompt}</label>
                            <textarea
                                value={negativePrompt}
                                onChange={(e) => setNegativePrompt(e.target.value)}
                                className="w-full h-16 bg-black/40 border border-white/10 rounded-lg p-3 text-xs text-gray-300 resize-none focus:outline-none focus:border-primary/50 font-mono"
                                placeholder={copy.negativePromptPlaceholder}
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
                                    {copy.applyArtDirectionStyle}
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
                                    <span className="text-xs font-bold text-gray-400 uppercase">{copy.applyArtDirectionStyle}</span>
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
                                                        <span className="text-xs font-bold text-green-400 block mb-1">{copy.stylePromptLabel}</span>
                                                        <p className="text-xs text-gray-400 font-mono bg-black/20 p-2 rounded border border-white/5 leading-relaxed">
                                                            {stylePrompt}
                                                        </p>
                                                    </div>
                                                )}

                                                {styleNegativePrompt && (
                                                    <div>
                                                        <span className="text-xs font-bold text-red-400 block mb-1">{copy.styleNegativePromptLabel}</span>
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
    scaffolds = [],
    templates = [],
    promptIssues = [],
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
}: WorkbenchPanelProps) {

    return (
        <div
            className={`flex-1 flex flex-col min-w-[300px] transition-colors ${isActive ? 'bg-white/5' : 'bg-transparent hover:bg-white/[0.02]'}`}
            onClick={onClick}
        >
            {/* Panel Header */}
            <div className="p-4 border-b border-white/5">
                <div className="flex items-center justify-between mb-1">
                    <h3 className={`font-bold text-sm uppercase tracking-wider ${isActive ? 'text-primary' : 'text-gray-400'}`}>
                        {title}
                    </h3>

                    {/* Mode Switcher (Asset Activation v2) */}
                    {supportsMotion && (
                        <div className="flex items-center gap-1 bg-black/40 p-1 rounded-lg border border-white/10">
                            <button
                                onClick={(e) => { e.stopPropagation(); onModeChange?.('static'); }}
                                className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors ${mode === 'static'
                                    ? 'bg-primary/20 text-primary'
                                    : 'text-gray-400 hover:text-white'
                                    }`}
                            >
                                <PhotoIcon size={12} />
                                {copy.staticMode}
                            </button>
                            <button
                                onClick={(e) => {
                                    e.stopPropagation();
                                    if (!hasStaticImage) {
                                        alert(copy.staticRequired);
                                        return;
                                    }
                                    onModeChange?.('motion');
                                }}
                                className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors ${mode === 'motion'
                                    ? 'bg-purple-500/20 text-purple-400'
                                    : 'text-gray-400 hover:text-white'
                                    }`}
                            >
                                <Video size={12} />
                                {copy.motionMode}
                            </button>
                        </div>
                    )}
                </div>
                <p className="text-xs text-gray-500">{description}</p>
            </div>

            {/* Image Area with Variant Selector */}
            <div className="flex-1 relative bg-black/40 p-4 flex flex-col overflow-y-auto group">

                {/* Locked Overlay */}
                {isLocked && (
                    <div className="absolute inset-0 bg-black/80 z-20 flex items-center justify-center text-center p-6">
                        <div className="text-gray-500 flex flex-col items-center gap-2">
                            <Lock size={32} />
                            <span className="text-sm">{copy.generateMasterFirst}</span>
                        </div>
                    </div>
                )}

                {/* Reverse Generation Hint - shown in Full Body panel when upload detected */}
                {reverseGenerationMode && (
                    <div className="absolute inset-0 bg-gradient-to-b from-primary/10 to-transparent z-10 flex flex-col items-center justify-center text-center p-6 pointer-events-none">
                        <div className="flex flex-col items-center gap-3 bg-black/60 backdrop-blur-md rounded-xl p-6 border border-primary/30 pointer-events-auto">
                            <div className="flex items-center gap-2 text-primary">
                                <RefreshCw size={20} />
                                <span className="text-sm font-bold">{copy.uploadDetected}</span>
                            </div>
                            <p className="text-xs text-gray-300 max-w-[200px]">
                                {copy.uploadDetectedHint}
                            </p>
                            {reverseReferenceUrl && (
                                <NextImage
                                    src={getAssetUrl(reverseReferenceUrl)}
                                    alt={copy.reverseReferenceAlt}
                                    width={64}
                                    height={64}
                                    unoptimized
                                    className="w-16 h-16 rounded-lg object-cover border border-white/20"
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
                                <span className="text-xs font-bold text-purple-300 uppercase tracking-wider">{copy.motionReference}</span>
                            </div>

                            {/* Video Player with glassmorphism */}
                            <div className={`relative w-full ${aspectRatio === '9:16' ? 'aspect-[9/16] max-h-[40vh]' : aspectRatio === '1:1' ? 'aspect-square max-h-[35vh]' : 'aspect-video'} bg-gradient-to-br from-gray-900/80 to-black rounded-xl overflow-hidden border border-white/5 shadow-xl backdrop-blur-sm`}>
                                {isGeneratingMotion ? (
                                    <div className="absolute inset-0 z-10 bg-black/60 backdrop-blur-md flex flex-col items-center justify-center gap-4">
                                        <div className="relative">
                                            <RefreshCw size={48} className="text-purple-400 animate-spin" />
                                            <div className="absolute inset-0 blur-xl bg-purple-500/30 animate-pulse"></div>
                                        </div>
                                        <div className="flex flex-col items-center">
                                            <span className="text-sm font-bold text-white uppercase tracking-widest animate-pulse">{messages.common.messages.generating}</span>
                                            <span className="text-[10px] text-purple-300/60 mt-1">{copy.aiProcessingMotion}</span>
                                        </div>
                                    </div>
                                ) : isVideoLoading && motionRefVideos?.length > 0 ? (
                                    <div className="absolute inset-0 z-10 bg-black/40 backdrop-blur-sm flex flex-col items-center justify-center gap-3">
                                        <RefreshCw size={32} className="text-gray-400 animate-spin" />
                                        <span className="text-xs text-gray-400 font-medium">{copy.loadingVideoFile}</span>
                                    </div>
                                ) : null}

                                {motionRefVideos?.length > 0 ? (
                                    <video
                                        key={motionRefVideos[motionRefVideos.length - 1]?.url}
                                        src={getAssetUrl(motionRefVideos[motionRefVideos.length - 1]?.url)}
                                        onCanPlay={() => setIsVideoLoading?.(false)}
                                        onLoadStart={() => setIsVideoLoading?.(true)}
                                        className="w-full h-full object-contain"
                                        controls
                                        loop
                                        autoPlay
                                        muted
                                    />
                                ) : !isGeneratingMotion && (
                                    <div className="w-full h-full flex flex-col items-center justify-center text-gray-500 gap-2">
                                        <Video size={40} className="opacity-50" />
                                        <span className="text-sm">{copy.noMotionReference}</span>
                                        <span className="text-xs opacity-70">{copy.generateBelow}</span>
                                    </div>
                                )}
                            </div>

                            <div className="bg-black/20 rounded-lg border border-white/10 p-3">
                                <label className="text-xs font-bold text-gray-500 uppercase mb-2 block">{copy.audioInputOptional}</label>
                                <p className="text-xs text-gray-600 mb-3">{copy.audioInputHint}</p>

                                <label className={`flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-dashed cursor-pointer transition-all ${audioUrl
                                    ? 'border-green-500/50 bg-green-500/10 text-green-400'
                                    : 'border-indigo-500/30 hover:border-indigo-400/50 hover:bg-indigo-500/5 text-gray-400'
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
                                            <span className="text-xs">{copy.uploading}</span>
                                        </>
                                    ) : audioUrl ? (
                                        <>
                                            <Check size={14} />
                                            <span className="text-xs font-medium">{copy.audioUploaded}</span>
                                        </>
                                    ) : (
                                        <>
                                            <ImageIcon size={14} />
                                            <span className="text-xs">{copy.uploadAudioFile}</span>
                                        </>
                                    )}
                                </label>
                            </div>

                            {/* Motion Prompt */}
                            <div className="flex flex-col gap-2">
                                <div className="flex items-center justify-between">
                                    <label className="text-xs font-bold text-gray-500 uppercase">{copy.motionPrompt}</label>
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            onResetPrompt?.();
                                        }}
                                        className="text-[10px] text-primary hover:text-primary/80 transition-colors flex items-center gap-1"
                                        title={copy.resetRecommendedPrompt}
                                    >
                                        <RefreshCw size={10} />
                                        {copy.resetRecommendedPrompt}
                                    </button>
                                </div>
                                <textarea
                                    value={motionPrompt}
                                    onChange={(e) => setMotionPrompt?.(e.target.value)}
                                    className="w-full h-24 bg-black/40 border border-white/10 rounded-lg p-3 text-xs text-gray-300 resize-none focus:outline-none focus:border-primary/50 font-mono leading-relaxed"
                                    placeholder={copy.motionPromptPlaceholder}
                                />
                            </div>

                            {/* Generate Button */}
                            <button
                                onClick={() => onGenerateMotionRef?.(motionPrompt, audioUrl)}
                                disabled={isGeneratingMotion}
                                className={`flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${isGeneratingMotion
                                    ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                                    : 'bg-primary hover:bg-primary/90 text-white shadow-lg'
                                    }`}
                            >
                                <Video size={16} />
                                {copy.generateMotionReference}
                            </button>
                        </div>
                    ) : isVideo ? (
                        <VideoVariantSelector
                            videos={videos || []}
                            onDelete={onDeleteVideo || (() => undefined)}
                            onGenerate={onGenerateVideo || (() => undefined)}
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
                            <span className="text-xs font-bold text-yellow-500">{copy.updateRecommended}</span>
                        </div>
                    </div>
                )}
            </div>

            {/* Prompt Editor (Bottom) */}
            <div className="h-1/3 border-t border-white/10 flex flex-col bg-[#111]">
                <div className="p-2 border-b border-white/5 flex justify-between items-center bg-black/20">
                    <span className="text-xs font-bold text-gray-500 uppercase px-2">{copy.prompt}</span>
                </div>
                {!isVideo && (
                    <div className="border-b border-white/5 px-4 py-3 space-y-3 bg-black/10">
                        {scaffolds.length > 0 && (
                            <div className="space-y-2">
                                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500">
                                    {copy.scaffoldTitle}
                                </p>
                                <div className="flex flex-wrap gap-2">
                                    {scaffolds.map((item: ImagePromptBlock) => (
                                        <button
                                            key={item.id}
                                            type="button"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                applyPromptTemplate(prompt, item.prompt, setPrompt, "replace");
                                            }}
                                            className="rounded-full border border-cyan-400/25 bg-cyan-400/10 px-3 py-1 text-[11px] text-cyan-100 transition hover:bg-cyan-400/20"
                                        >
                                            {item.title}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        {templates.length > 0 && (
                            <div className="space-y-2">
                                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500">
                                    {copy.templateTitle}
                                </p>
                                <div className="flex flex-wrap gap-2">
                                    {templates.map((item: ImagePromptBlock) => (
                                        <button
                                            key={item.id}
                                            type="button"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                applyPromptTemplate(prompt, item.prompt, setPrompt, "append");
                                            }}
                                            className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] text-gray-200 transition hover:bg-white/10"
                                        >
                                            {copy.appendTemplate(item.title)}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )}
                <textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    disabled={isLocked}
                    className="flex-1 w-full bg-transparent p-4 text-xs text-gray-300 resize-none focus:outline-none focus:bg-white/5 font-mono leading-relaxed"
                    placeholder={copy.promptPlaceholder}
                />
                {!isVideo && (
                    <div className="border-t border-white/5 px-4 py-3">
                        <PromptQualityPanel
                            issues={promptIssues}
                            title={copy.promptQualityTitle}
                            compact
                        />
                    </div>
                )}
            </div>
        </div>
    );
}
