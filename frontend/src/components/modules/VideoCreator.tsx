"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import NextImage from "next/image";
import {
    Upload, X, Wand2, Plus, Loader2, Layout,
    Video,
    Eraser,
    Check,
    Image as ImageIcon,
    Film
} from "lucide-react";





import { useProjectStore } from "@/store/projectStore";
import type { Project, StoryboardFrame } from "@/store/projectStore";
import { api, type CreateVideoTaskPayload, type VideoTask } from "@/lib/api";
import { extractErrorDetail, getAssetUrl, getAssetUrlWithTimestamp, stripAssetApiPrefix } from "@/lib/utils";
import PromptBuilder, { PromptSegment, PromptBuilderRef } from "./PromptBuilder";
import { isSeedanceI2VModel, type VideoParams } from "@/store/projectStore";
import { cameraTerms, getReferenceVideoTypeLabel, messages, seedanceTerms, subjectMotionTerms } from "@/lib/i18n";
import {
    buildSeedancePayloadPreviews,
    getSeedanceEffectiveMedia,
    getSeedanceEditModeLabel,
    getSeedanceExtendModeLabel,
    getSeedancePayloadWarnings,
    getSeedanceWorkflowLabel,
    getSeedanceSubmissionState,
} from "@/lib/seedance";
import {
    applySeedancePromptBlock,
    getSeedancePromptScaffolds,
    getSeedancePromptTemplates,
    SEEDANCE_PROMPT_TEMPLATE_CATEGORIES,
    type SeedancePromptTemplateCategory,
} from "@/lib/seedance-prompts";
import PromptQualityPanel from "@/components/common/PromptQualityPanel";
import {
    formatPromptIssues,
    hasBlockingPromptIssues,
    inspectVideoPrompt,
} from "@/lib/prompt-quality";
import { getGenerationBadgeText, getGenerationTooltip, isGenerationDegraded } from "@/lib/generation-provenance";

interface VideoCreatorProps {
    onTaskCreated: (project: Project) => void;
    remixData: Partial<VideoTask> | null;
    onRemixClear: () => void;
    params: VideoParams;
    onParamsChange: (params: Partial<VideoParams>) => void;
}

const copy = messages.modules.videoCreator;
const commonActions = messages.common.actions;
const commonMessages = messages.common.messages;
const seedanceSummaryCopy = seedanceTerms.summary;

type AssetCardItem = { url: string; title: string };
type ReferenceVideoItem = { url: string; thumbnail?: string; title: string; assetName: string; type: string };

export default function VideoCreator({ onTaskCreated, remixData, onRemixClear, params, onParamsChange }: VideoCreatorProps) {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const isSeedanceModel = isSeedanceI2VModel(params.model);

    // Helper function to generate motion description text
    const getMotionDescription = () => {
        const parts: string[] = [];

        if (params.cameraMovement && params.cameraMovement !== 'none') {
            parts.push(cameraTerms[params.cameraMovement as keyof typeof cameraTerms]?.prompt || '');
        }

        if (params.subjectMotion && params.subjectMotion !== 'still') {
            parts.push(subjectMotionTerms[params.subjectMotion as keyof typeof subjectMotionTerms]?.prompt || '');
        }

        return parts.filter(p => p).join(', ');
    };

    const [selectedImages, setSelectedImages] = useState<string[]>([]);
    const [selectedReferenceVideos, setSelectedReferenceVideos] = useState<string[]>(params.referenceVideoUrls || []);
    const [uploadingPaths, setUploadingPaths] = useState<Record<string, string>>({}); // Map blobUrl -> serverUrl
    const [activeTab, setActiveTab] = useState<"storyboard" | "upload">("storyboard");

    // R2V Cast Slots: 3 slots for reference videos
    const [castSlots, setCastSlots] = useState<{ url: string; name: string }[]>([]);
    const [selectedFrameId, setSelectedFrameId] = useState<string | null>(null); // Selected frame for R2V
    const [generationMode, setGenerationMode] = useState<"i2v" | "r2v">("i2v"); // Local mode state
    const [extractingFrameId, setExtractingFrameId] = useState<string | null>(null);

    // Sync from parent params
    useEffect(() => {
        if (params.generationMode) {
            setGenerationMode(params.generationMode as "i2v" | "r2v");
        }
    }, [params.generationMode]);

    useEffect(() => {
        setSelectedReferenceVideos(params.referenceVideoUrls || []);
    }, [params.referenceVideoUrls]);

    useEffect(() => {
        if (!isSeedanceModel || generationMode !== "i2v") {
            setShowSeedancePayloadPreview(false);
        }
    }, [generationMode, isSeedanceModel]);

    const handleExtractLastFrame = async (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!currentProject?.frames) return;

        const frameIndex = currentProject.frames.findIndex((frame) => frame.id === frameId);
        if (frameIndex <= 0) return;

        const prevFrame = currentProject.frames[frameIndex - 1];
        if (!prevFrame.selected_video_id) return;

        const prevVideo = currentProject.video_tasks?.find(
            (task) => task.id === prevFrame.selected_video_id && task.status === "completed"
        );
        if (!prevVideo) return;

        setExtractingFrameId(frameId);
        try {
            const updatedProject = await api.extractLastFrame(currentProject.id, frameId, prevVideo.id);
            updateProject(currentProject.id, updatedProject);
        } catch (error) {
            console.error("Failed to extract last frame:", error);
            alert(extractErrorDetail(error, "") || copy.extractLastFrameFailed);
        } finally {
            setExtractingFrameId(null);
        }
    };

    const handleFrameSelect = (frame: StoryboardFrame) => {
        // Prefer rendered_image_url (from extracted last frame / uploaded image), fallback to image_url
        const url = frame.rendered_image_url || frame.image_url;
        if (!url) return;

        // If already selected, deselect
        if (selectedImages.includes(url)) {
            setSelectedImages([]);
            return;
        }

        // Select new image (replace existing)
        setSelectedImages([url]);

        // Auto-fill prompt (Replace existing prompt)
        let newPrompt = frame.image_prompt || frame.action_description || "";
        if (frame.dialogue) {
            newPrompt += ` . Dialogue: ${frame.dialogue}`;
        }
        setSegments([{ type: "text", value: newPrompt, id: "init" }]);
    };
    const [segments, setSegments] = useState<PromptSegment[]>([{ type: "text", value: "", id: "init" }]);
    const promptBuilderRef = useRef<PromptBuilderRef>(null);

    // Computed prompt for API
    const prompt = segments.map(s => s.value).join(" ");

    // negativePrompt moved to params
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [submitSuccess, setSubmitSuccess] = useState(false);
    const [polishedPrompt, setPolishedPrompt] = useState<{ cn: string; en: string } | null>(null);
    const [isPolishing, setIsPolishing] = useState(false);
    const [feedbackText, setFeedbackText] = useState("");
    const [showSeedancePayloadPreview, setShowSeedancePayloadPreview] = useState(false);
    const [isUploadingReferenceAudio, setIsUploadingReferenceAudio] = useState(false);
    const [activePromptTemplateCategory, setActivePromptTemplateCategory] =
        useState<SeedancePromptTemplateCategory>("all");
    const referenceAudioInputRef = useRef<HTMLInputElement>(null);
    const isR2VMode = generationMode === "r2v";
    const shouldShowPromptLibrary = isSeedanceModel || isR2VMode;
    const promptLibraryGenerationMode = isR2VMode ? "r2v" : "i2v";
    const promptLibraryWorkflow = isR2VMode
        ? "standard"
        : (params.seedanceWorkflow as "standard" | "extend" | "edit");
    const promptLibraryWorkflowMode = isR2VMode
        ? undefined
        : promptLibraryWorkflow === "extend"
            ? params.seedanceExtendMode
            : promptLibraryWorkflow === "edit"
                ? params.seedanceEditMode
                : undefined;
    const availablePromptScaffolds = shouldShowPromptLibrary
        ? getSeedancePromptScaffolds({
            generationMode: promptLibraryGenerationMode,
            workflow: promptLibraryWorkflow,
            workflowMode: promptLibraryWorkflowMode,
        })
        : [];
    const availablePromptTemplates = shouldShowPromptLibrary
        ? getSeedancePromptTemplates({
            generationMode: promptLibraryGenerationMode,
            workflow: promptLibraryWorkflow,
            workflowMode: promptLibraryWorkflowMode,
        }).filter((item) =>
            activePromptTemplateCategory === "all" || item.category === activePromptTemplateCategory,
        )
        : [];
    const promptLibraryContextLabel = isR2VMode
        ? copy.promptLibraryR2VContext
        : promptLibraryWorkflow === "extend" && promptLibraryWorkflowMode
            ? copy.promptLibraryWorkflowContext(`${getSeedanceWorkflowLabel(promptLibraryWorkflow)} · ${getSeedanceExtendModeLabel(promptLibraryWorkflowMode)}`)
            : promptLibraryWorkflow === "edit" && promptLibraryWorkflowMode
                ? copy.promptLibraryWorkflowContext(`${getSeedanceWorkflowLabel(promptLibraryWorkflow)} · ${getSeedanceEditModeLabel(promptLibraryWorkflowMode)}`)
                : copy.promptLibraryWorkflowContext(getSeedanceWorkflowLabel(promptLibraryWorkflow));

    const handleApplyPromptBlock = (blockPrompt: string, mode: "replace" | "append") => {
        const hasStructuredContent = segments.some((segment) => segment.value.trim().length > 0);

        if (mode === "append" && hasStructuredContent) {
            const nextSegments = [...segments];
            const lastSegment = nextSegments[nextSegments.length - 1];

            if (lastSegment?.type === "text") {
                nextSegments[nextSegments.length - 1] = {
                    ...lastSegment,
                    value: `${lastSegment.value}\n\n${blockPrompt}`,
                };
            } else {
                nextSegments.push({
                    type: "text",
                    value: `\n\n${blockPrompt}`,
                    id: `prompt-block-${Date.now()}`,
                });
            }

            setSegments(nextSegments);
            setPolishedPrompt(null);
            return;
        }

        const nextPrompt = applySeedancePromptBlock("", blockPrompt, "replace");
        setSegments([{ type: "text", value: nextPrompt, id: `prompt-block-${Date.now()}` }]);
        setPolishedPrompt(null);
    };

    const handlePolish = async (feedback: string = "") => {
        const draftPrompt = feedback ? (polishedPrompt?.en || prompt) : prompt;
        if (!draftPrompt) return;
        setIsPolishing(true);
        try {
            let res;
            const scriptId = currentProject?.id || "";
            if (generationMode === 'r2v') {
                // R2V mode: use R2V-specific polish with slot info
                const slotInfo = castSlots
                    .filter(slot => slot.url)
                    .map((slot, index) => ({
                        description: slot.name || `@Ref_${String.fromCharCode(65 + index)}`
                    }));
                res = await api.polishR2VPrompt(draftPrompt, slotInfo, feedback, scriptId);
            } else {
                // I2V mode: use video polish
                res = await api.polishVideoPrompt(draftPrompt, feedback, scriptId);
            }
            if (res.prompt_cn && res.prompt_en) {
                setPolishedPrompt({ cn: res.prompt_cn, en: res.prompt_en });
                setFeedbackText("");
            }
        } catch (error) {
            console.error("Polish failed", error);
            alert(copy.polishFailed);
        } finally {
            setIsPolishing(false);
        }
    };


    // Handle Remix Data
    useEffect(() => {
        if (remixData) {
            if (remixData.image_url) setSelectedImages([remixData.image_url]);
            if (remixData.prompt) setSegments([{ type: "text", value: remixData.prompt, id: "remix" }]);
            // negativePrompt handled by parent

            // Clear remix data after applying to avoid re-applying on every render
            onRemixClear();
        }
    }, [remixData, onRemixClear]);

    const handleImageSelect = (files: FileList | null) => {
        if (!files) return;

        const newImages: string[] = [];

        Array.from(files).forEach(async (file) => {
            const blobUrl = URL.createObjectURL(file);
            newImages.push(blobUrl);

            // Background Upload
            try {
                const res = await api.uploadFile(file);
                setUploadingPaths(prev => ({ ...prev, [blobUrl]: res.url }));
            } catch (error) {
                console.error("Upload failed", error);
                // Could remove from selectedImages or show error state on the specific image
            }
        });

        setSelectedImages(prev => [...prev, ...newImages]);
    };

    const handleAssetSelect = (url: string) => {
        if (!selectedImages.includes(url)) {
            setSelectedImages(prev => [...prev, url]);
        }
    };

    const removeImage = (index: number) => {
        setSelectedImages(prev => prev.filter((_, i) => i !== index));
    };

    // R2V: Handle Reference Video Selection
    const handleReferenceVideoSelect = (videoUrl: string) => {
        if (selectedReferenceVideos.includes(videoUrl)) {
            const next = selectedReferenceVideos.filter((v) => v !== videoUrl);
            setSelectedReferenceVideos(next);
            onParamsChange({ referenceVideoUrls: next });
        } else {
            if (selectedReferenceVideos.length >= 3) {
                alert(copy.referenceVideoLimit);
                return;
            }
            const next = [...selectedReferenceVideos, videoUrl];
            setSelectedReferenceVideos(next);
            onParamsChange({ referenceVideoUrls: next });
        }
    };

    // R2V: Handle Cast Slot Selection
    const handleCastSlotSelect = (slotIndex: number, video: { url: string; name: string }) => {
        setCastSlots(prev => {
            const newSlots = [...prev];
            // Ensure array is long enough
            while (newSlots.length <= slotIndex) {
                newSlots.push({ url: '', name: '' });
            }
            newSlots[slotIndex] = video;
            return newSlots;
        });
    };

    // R2V: Clear Cast Slot
    const handleClearCastSlot = (slotIndex: number) => {
        setCastSlots(prev => {
            const newSlots = [...prev];
            if (newSlots[slotIndex]) {
                newSlots[slotIndex] = { url: '', name: '' };
            }
            return newSlots;
        });
    };

    // R2V: Handle Frame Selection (for description)
    const handleR2VFrameSelect = (frame: StoryboardFrame) => {
        setSelectedFrameId(frame.id);
        // Auto-fill prompt with frame description
        let newPrompt = frame.action_description || frame.image_prompt || "";
        if (frame.dialogue) {
            newPrompt += ` Dialogue: ${frame.dialogue}`;
        }
        setSegments([{ type: "text", value: newPrompt, id: `frame-${frame.id}` }]);
    };

    // Insert character into prompt at cursor position
    const insertCharacter = (slotIndex: number) => {
        const slot = castSlots[slotIndex];
        if (!slot?.url) return;

        // Find video to get thumbnail
        const video = availableReferenceVideos.find(v => v.url === slot.url);
        const thumbnail = video?.thumbnail ? getAssetUrl(video.thumbnail) : undefined;

        promptBuilderRef.current?.insertCharacter(slotIndex, slot.name, thumbnail);
    };

    const handleSeedanceReferenceAudioUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        setIsUploadingReferenceAudio(true);
        try {
            const res = await api.uploadFile(file);
            onParamsChange({ referenceAudioUrl: res.url });
        } catch (error) {
            console.error("Seedance reference audio upload failed", error);
            alert(copy.submitFailed);
        } finally {
            setIsUploadingReferenceAudio(false);
            if (referenceAudioInputRef.current) {
                referenceAudioInputRef.current.value = "";
            }
        }
    };

    const resolveMediaUrl = (mediaUrl?: string) => {
        if (!mediaUrl) return "";
        if (mediaUrl.startsWith("blob:")) {
            return uploadingPaths[mediaUrl] || "";
        }
        return stripAssetApiPrefix(mediaUrl);
    };

    const finalPrompt = (() => {
        const motionDesc = getMotionDescription();
        return motionDesc ? `${prompt}, ${motionDesc}` : prompt;
    })();
    const promptQualityIssues = inspectVideoPrompt({
        prompt: finalPrompt,
        workflow: params.seedanceWorkflow,
        workflowMode: promptLibraryWorkflowMode,
        generationMode,
    });

    const allResolvedSeedanceImages = selectedImages.map(resolveMediaUrl).filter(Boolean);
    const allResolvedReferenceVideoUrls = selectedReferenceVideos.map(resolveMediaUrl).filter(Boolean);
    const allResolvedReferenceAudioUrl = resolveMediaUrl(params.referenceAudioUrl);
    const seedanceEffectiveMedia = getSeedanceEffectiveMedia({
        referenceMode: params.seedanceReferenceMode as "image" | "video" | "combo",
        imageUrls: allResolvedSeedanceImages,
        referenceVideoUrls: allResolvedReferenceVideoUrls,
        referenceAudioUrl: allResolvedReferenceAudioUrl,
    });
    const resolvedSeedanceImages = seedanceEffectiveMedia.imageUrls;
    const resolvedReferenceVideoUrls = seedanceEffectiveMedia.referenceVideoUrls;
    const resolvedReferenceAudioUrl = seedanceEffectiveMedia.referenceAudioUrl || "";
    const usesSeedanceImageInput = params.seedanceReferenceMode === "image" || params.seedanceReferenceMode === "combo";
    const seedanceImageInputs = useMemo(
        () => (usesSeedanceImageInput ? selectedImages : []),
        [selectedImages, usesSeedanceImageInput],
    );
    const selectedCastCount = castSlots.filter((slot) => slot.url).length;
    const seedanceSubmissionState = getSeedanceSubmissionState({
        previewOnly: params.seedancePreviewOnly,
        workflow: params.seedanceWorkflow as "standard" | "extend" | "edit",
        referenceMode: params.seedanceReferenceMode as "image" | "video" | "combo",
        imageUrls: allResolvedSeedanceImages,
        referenceVideoUrls: allResolvedReferenceVideoUrls,
        referenceAudioUrl: allResolvedReferenceAudioUrl,
    });
    const isSeedancePreviewSubmit = generationMode === "i2v"
        && isSeedanceModel
        && seedanceSubmissionState.mode === "preview";
    const canOpenSeedancePreview = generationMode === "i2v"
        && isSeedanceModel
        && (
            finalPrompt.trim().length > 0
            || resolvedSeedanceImages.length > 0
            || resolvedReferenceVideoUrls.length > 0
            || !!resolvedReferenceAudioUrl
        );
    const seedancePreviewPayloads = buildSeedancePayloadPreviews({
        prompt: finalPrompt,
        model: params.model,
        duration: params.duration,
        resolution: params.resolution,
        aspectRatio: params.aspectRatio,
        watermark: params.watermark,
        cameraFixed: params.cameraFixed,
        generateAudio: params.generateAudio,
        seed: params.seed,
        referenceMode: params.seedanceReferenceMode as "image" | "video" | "combo",
        workflow: params.seedanceWorkflow as "standard" | "extend" | "edit",
        extendMode: params.seedanceExtendMode as "continue" | "prepend" | "trajectory",
        editMode: params.seedanceEditMode as "subject_replace" | "object_edit" | "inpaint",
        imageUrls: allResolvedSeedanceImages,
        referenceVideoUrls: allResolvedReferenceVideoUrls,
        referenceAudioUrl: allResolvedReferenceAudioUrl,
    });
    const seedancePayloadWarnings = [
        ...getSeedancePayloadWarnings({
            prompt: finalPrompt,
            model: params.model,
            duration: params.duration,
            resolution: params.resolution,
            aspectRatio: params.aspectRatio,
            watermark: params.watermark,
            cameraFixed: params.cameraFixed,
            generateAudio: params.generateAudio,
            seed: params.seed,
            referenceMode: params.seedanceReferenceMode as "image" | "video" | "combo",
            workflow: params.seedanceWorkflow as "standard" | "extend" | "edit",
            extendMode: params.seedanceExtendMode as "continue" | "prepend" | "trajectory",
            editMode: params.seedanceEditMode as "subject_replace" | "object_edit" | "inpaint",
            imageUrls: allResolvedSeedanceImages,
            referenceVideoUrls: allResolvedReferenceVideoUrls,
            referenceAudioUrl: allResolvedReferenceAudioUrl,
        }),
    ];

    const handleOpenSeedancePreview = () => {
        setShowSeedancePayloadPreview(true);
    };

    const copySeedancePayloadJson = async (payload: unknown, successMessage: string) => {
        try {
            await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
            alert(successMessage);
        } catch (error) {
            console.error("Failed to copy Seedance payload preview", error);
        }
    };

    const handleCopySeedancePayloads = async () => {
        const payloadToCopy = seedancePreviewPayloads.length > 1
            ? seedancePreviewPayloads.map((item) => item.payload)
            : seedancePreviewPayloads[0]?.payload;
        await copySeedancePayloadJson(
            payloadToCopy,
            seedancePreviewPayloads.length > 1 ? copy.payloadsCopied : copy.payloadCopied,
        );
    };

    const handleCopySingleSeedancePayload = async (payload: Record<string, unknown>) => {
        await copySeedancePayloadJson(payload, copy.payloadCopied);
    };

    const canRunPrimaryAction = (() => {
        if (isSubmitting || !currentProject || !finalPrompt.trim()) {
            return false;
        }

        if (generationMode === "r2v") {
            return selectedCastCount > 0;
        }

        if (isSeedanceModel) {
            if (isSeedancePreviewSubmit) {
                return true;
            }
            return resolvedSeedanceImages.length > 0 || resolvedReferenceVideoUrls.length > 0;
        }

        return selectedImages.length > 0;
    })();

    const handleSubmit = useCallback(async () => {
        if (!currentProject) return;

        if (generationMode === "r2v") {
            if (selectedCastCount === 0) {
                alert(copy.r2vCastSlotRequired);
                return;
            }
            if (!finalPrompt.trim()) return;
        } else if (isSeedanceModel) {
            if (isSeedancePreviewSubmit) {
                setShowSeedancePayloadPreview(true);
                if (seedanceSubmissionState.reason === "workflow_missing_video") {
                    alert(copy.workflowNeedsReferenceVideo);
                }
                return;
            }

            if (!finalPrompt.trim()) return;

            if (resolvedSeedanceImages.length === 0 && resolvedReferenceVideoUrls.length === 0) {
                setShowSeedancePayloadPreview(true);
                alert(copy.seedanceMissingInput);
                return;
            }
        } else {
            if (selectedImages.length === 0 || !finalPrompt.trim()) return;
        }

        if (hasBlockingPromptIssues(promptQualityIssues)) {
            alert(`${copy.promptQualityBlocked}\n\n${formatPromptIssues(promptQualityIssues.filter((issue) => issue.severity === "error"))}`);
            return;
        }

        setIsSubmitting(true);
        try {
            const optimisticTasks: VideoTask[] = [];
            let itemsToProcess = generationMode === "i2v" && isSeedanceModel ? seedanceImageInputs : selectedImages;

            if (generationMode === "r2v" && selectedImages.length === 0) {
                itemsToProcess = [""];
            }

            if (generationMode === "i2v" && isSeedanceModel && resolvedSeedanceImages.length === 0) {
                itemsToProcess = [""];
            }

            itemsToProcess.forEach((img, idx) => {
                let displayUrl = img;
                if (img && img.startsWith("blob:")) {
                    displayUrl = uploadingPaths[img] || img;
                }

                const actualModel = generationMode === "r2v" ? "wan2.6-r2v" : params.model;
                const referenceVideos = generationMode === "r2v"
                    ? castSlots.filter((slot) => slot.url).map((slot) => slot.url)
                    : resolvedReferenceVideoUrls;

                for (let i = 0; i < params.batchSize; i++) {
                    optimisticTasks.push({
                        id: `temp-${Date.now()}-${idx}-${i}`,
                        project_id: currentProject.id,
                        image_url: displayUrl,
                        prompt: finalPrompt,
                        status: "pending",
                        video_url: undefined,
                        duration: params.duration,
                        seed: params.seed,
                        resolution: params.resolution,
                        generate_audio: params.generateAudio,
                        audio_url: params.audioUrl,
                        prompt_extend: params.promptExtend,
                        negative_prompt: params.negativePrompt,
                        aspect_ratio: params.aspectRatio,
                        watermark: params.watermark,
                        camera_fixed: params.cameraFixed,
                        reference_audio_url: resolvedReferenceAudioUrl || undefined,
                        seedance_reference_mode: params.seedanceReferenceMode,
                        seedance_workflow: params.seedanceWorkflow,
                        seedance_extend_mode: params.seedanceExtendMode,
                        seedance_edit_mode: params.seedanceEditMode,
                        model: actualModel,
                        created_at: Date.now() / 1000,
                        generation_mode: generationMode,
                        reference_video_urls: referenceVideos,
                    });
                }
            });

            const optimisticProject = {
                ...currentProject,
                video_tasks: [...(currentProject.video_tasks || []), ...optimisticTasks],
            };
            onTaskCreated(optimisticProject);

            for (const img of itemsToProcess) {
                let finalImageUrl = img;
                if (img && img.startsWith("blob:")) {
                    if (uploadingPaths[img]) {
                        finalImageUrl = uploadingPaths[img];
                    } else {
                        console.warn("Image upload pending for", img);
                        continue;
                    }
                } else if (img) {
                    finalImageUrl = stripAssetApiPrefix(img);
                }

                let frameId: string | undefined;
                if (generationMode === "r2v") {
                    frameId = selectedFrameId || undefined;
                } else {
                    const frame = currentProject?.frames?.find((candidate) =>
                        (candidate.rendered_image_url || candidate.image_url) === img ||
                        getAssetUrl(candidate.rendered_image_url || candidate.image_url || "") === img
                    );
                    frameId = frame ? frame.id : undefined;
                }

                const actualModel = generationMode === "r2v" ? "wan2.6-r2v" : params.model;
                const referenceVideos = generationMode === "r2v"
                    ? castSlots.filter((slot) => slot.url).map((slot) => slot.url)
                    : resolvedReferenceVideoUrls;

                const videoTaskPayload: CreateVideoTaskPayload = {
                    imageUrl: finalImageUrl,
                    prompt: finalPrompt,
                    duration: params.duration,
                    seed: params.seed,
                    resolution: params.resolution,
                    generateAudio: params.generateAudio,
                    audioUrl: params.audioUrl,
                    promptExtend: params.promptExtend,
                    negativePrompt: params.negativePrompt,
                    batchSize: params.batchSize,
                    model: actualModel,
                    frameId,
                    shotType: params.shotType,
                    generationMode,
                    referenceVideoUrls: referenceVideos,
                    aspectRatio: params.aspectRatio,
                    watermark: params.watermark,
                    cameraFixed: params.cameraFixed,
                    mode: params.mode,
                    sound: params.sound,
                    cfgScale: params.cfgScale,
                    viduAudio: params.viduAudio,
                    movementAmplitude: params.movementAmplitude,
                    referenceAudioUrl: resolvedReferenceAudioUrl || undefined,
                    seedanceReferenceMode: params.seedanceReferenceMode,
                    seedanceWorkflow: params.seedanceWorkflow,
                    seedanceExtendMode: params.seedanceExtendMode,
                    seedanceEditMode: params.seedanceEditMode,
                };

                await api.createVideoTask(currentProject.id, videoTaskPayload);
            }

            const updatedProject = await api.getProject(currentProject.id);
            onTaskCreated(updatedProject);

            setSubmitSuccess(true);
            setTimeout(() => setSubmitSuccess(false), 1500);
        } catch (error) {
            console.error("Failed to submit task:", error);
            alert(copy.submitFailed);
            const updatedProject = await api.getProject(currentProject.id);
            onTaskCreated(updatedProject);
        } finally {
            setIsSubmitting(false);
        }
    }, [
        castSlots,
        currentProject,
        finalPrompt,
        generationMode,
        isSeedanceModel,
        isSeedancePreviewSubmit,
        onTaskCreated,
        params,
        promptQualityIssues,
        resolvedReferenceAudioUrl,
        resolvedReferenceVideoUrls,
        resolvedSeedanceImages,
        seedanceImageInputs,
        seedanceSubmissionState,
        selectedCastCount,
        selectedFrameId,
        selectedImages,
        uploadingPaths,
    ]);

    // Keyboard shortcut
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.ctrlKey && e.key === "Enter") {
                handleSubmit();
            }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [selectedImages, prompt, currentProject, params, selectedReferenceVideos, handleSubmit]);

    // Available assets for drag/drop or selection
    const availableAssets: AssetCardItem[] = currentProject ? [
        ...currentProject.characters.map((character) => ({
            url: getAssetUrl(character.image_url),
            title: character.name,
        })),
        ...currentProject.scenes.map((scene) => ({
            url: getAssetUrl(scene.image_url),
            title: scene.name,
        })),
    ].filter((asset): asset is AssetCardItem => Boolean(asset.url)) : [];

    // Available Reference Videos (for R2V)
    const availableReferenceVideos: ReferenceVideoItem[] = currentProject ? [
        // Character asset video variants (full_body and headshot)
        ...currentProject.characters.flatMap((character) => {
            const variants: ReferenceVideoItem[] = [];
            const fullBody = character.full_body;
            const headShot = character.head_shot;
            // Full body video variants
            if (fullBody?.video_variants?.length) {
                variants.push(...fullBody.video_variants.map((variant) => ({
                    url: variant.url,
                    thumbnail: fullBody.selected_image_id
                        ? (fullBody.image_variants?.find((image) => image.id === fullBody.selected_image_id)?.url || character.full_body_image_url)
                        : character.full_body_image_url,
                    title: `${character.name} - Full Body Motion Reference`,
                    assetName: character.name,
                    type: "character_full_body",
                })));
            }
            // Headshot video variants
            if (headShot?.video_variants?.length) {
                variants.push(...headShot.video_variants.map((variant) => ({
                    url: variant.url,
                    thumbnail: headShot.selected_image_id
                        ? (headShot.image_variants?.find((image) => image.id === headShot.selected_image_id)?.url || character.headshot_image_url)
                        : character.headshot_image_url,
                    title: `${character.name} - Headshot Motion Reference`,
                    assetName: character.name,
                    type: "character_headshot",
                })));
            }
            return variants;
        }),
        // Character legacy video assets
        ...currentProject.characters.flatMap((character) =>
            (character.video_assets || []).map((video) => ({
                url: video.video_url,
                thumbnail: video.image_url,
                title: `${character.name} - Video`,
                assetName: character.name,
                type: "character_legacy",
            }))
        ),
        // Scene video assets
        ...currentProject.scenes.flatMap((scene) =>
            (scene.video_assets || []).map((video) => ({
                url: video.video_url,
                thumbnail: video.image_url,
                title: `${scene.name} - Video`,
                assetName: scene.name,
                type: "scene",
            }))
        ),
        // Prop video assets
        ...currentProject.props.flatMap((prop) =>
            (prop.video_assets || []).map((video) => ({
                url: video.video_url,
                thumbnail: video.image_url,
                title: `${prop.name} - Video`,
                assetName: prop.name,
                type: "prop",
            }))
        )
    ].filter((video): video is ReferenceVideoItem => Boolean(video.url && video.url !== "null" && video.url !== "undefined")) : [];

    return (
        <div className="h-full flex flex-col relative min-h-0" data-testid="video-creator">
            {/* Scrollable Content Area */}
            <div className="flex-1 overflow-y-auto p-8 custom-scrollbar min-h-0">
                <h2 className="text-2xl font-display font-bold text-white mb-6 flex items-center gap-3">
                    <div className="w-2 h-8 bg-primary rounded-full" />
                    {copy.title}
                    <span className="text-xs font-mono text-gray-500 bg-white/5 px-2 py-1 rounded">{copy.titleBadge}</span>
                </h2>

                <div className="flex flex-col gap-6 max-w-4xl mx-auto w-full pb-8">
                    {/* Generation Mode Switcher */}
                    <div className="flex items-center justify-center">
                        <div className="flex bg-black/40 rounded-xl p-1.5 gap-1 border border-white/10">
                            <button
                                onClick={() => {
                                    setGenerationMode("i2v");
                                    onParamsChange({ generationMode: "i2v" });
                                }}
                                className={`px-5 py-2.5 text-sm rounded-lg flex items-center gap-2 transition-all font-medium ${generationMode === "i2v"
                                    ? "bg-primary text-white shadow-lg"
                                    : "text-gray-400 hover:text-white hover:bg-white/5"
                                    }`}
                            >
                                <ImageIcon size={16} />
                                {copy.generationModes.i2v}
                            </button>
                            <button
                                onClick={() => {
                                    setGenerationMode("r2v");
                                    onParamsChange({
                                        generationMode: "r2v",
                                        model: "wan2.6-i2v" // Force Wan 2.6 when switching to R2V
                                    });
                                }}
                                className={`px-5 py-2.5 text-sm rounded-lg flex items-center gap-2 transition-all font-medium ${generationMode === "r2v"
                                    ? "bg-purple-600 text-white shadow-lg"
                                    : "text-gray-400 hover:text-white hover:bg-white/5"
                                    }`}
                            >
                                <Film size={16} />
                                {copy.generationModes.r2v}
                            </button>
                        </div>
                    </div>
                    {/* === I2V MODE: Source Selector === */}
                    {generationMode === 'i2v' && (
                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <label className="text-sm font-medium text-gray-300">{copy.firstFrame}</label>
                                <div className="flex bg-white/5 rounded-lg p-1 gap-1">
                                    <button
                                        onClick={() => setActiveTab("storyboard")}
                                        className={`px-3 py-1.5 text-xs rounded-md flex items-center gap-2 transition-all ${activeTab === "storyboard"
                                            ? "bg-primary text-white shadow-sm"
                                            : "text-gray-400 hover:text-white hover:bg-white/5"
                                            }`}
                                    >
                                        <Layout size={14} /> {copy.tabs.storyboard}
                                    </button>
                                    <button
                                        onClick={() => setActiveTab("upload")}
                                        className={`px-3 py-1.5 text-xs rounded-md flex items-center gap-2 transition-all ${activeTab === "upload"
                                            ? "bg-primary text-white shadow-sm"
                                            : "text-gray-400 hover:text-white hover:bg-white/5"
                                            }`}
                                    >
                                        <Upload size={14} /> {copy.tabs.upload}
                                    </button>
                                </div>
                            </div>

                            {/* Tab Content */}
                            <div className="bg-black/20 border border-white/10 rounded-xl p-4 min-h-[200px]">
                                {activeTab === "storyboard" ? (
                                    <div className="space-y-4">
                                        {currentProject?.frames && currentProject.frames.length > 0 ? (() => {
                                            const completedVideoIds = new Set(
                                                currentProject.video_tasks
                                                    ?.filter((task) => task.status === "completed")
                                                    .map((task) => task.id) ?? []
                                            );
                                            return (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 max-h-[500px] overflow-y-auto custom-scrollbar pr-2 p-2">
                                                {currentProject.frames.map((frame, index) => {
                                                    const prevFrame = index > 0 ? currentProject.frames![index - 1] : null;
                                                    const prevVideoCompleted = prevFrame?.selected_video_id && completedVideoIds.has(prevFrame.selected_video_id);
                                                    const isExtracting = extractingFrameId === frame.id;
                                                    const hasExtracted = !!frame.rendered_image_url;
                                                    const generationBadge = getGenerationBadgeText(frame);
                                                    const generationBadgeDegraded = isGenerationDegraded(frame);

                                                    return (
                                                    <div
                                                        key={frame.id}
                                                        onClick={() => handleFrameSelect(frame)}
                                                        data-testid="video-storyboard-frame-card"
                                                        data-frame-id={frame.id}
                                                        className={`group relative aspect-video rounded-lg overflow-hidden border cursor-pointer transition-all ${selectedImages.includes(frame.rendered_image_url || frame.image_url || "")
                                                            ? "border-primary ring-2 ring-primary/50"
                                                            : "border-white/10 hover:border-white/30"
                                                            }`}
                                                    >
                                                        {(frame.rendered_image_url || frame.image_url) ? (
                                                                <NextImage
                                                                    src={getAssetUrlWithTimestamp(frame.rendered_image_url || frame.image_url || "", frame.updated_at)}
                                                                    alt={copy.frameAlt(frame.id)}
                                                                    fill
                                                                    sizes="(max-width: 640px) 100vw, 33vw"
                                                                    unoptimized
                                                                    className="object-cover"
                                                                />
                                                        ) : (
                                                            <div className="w-full h-full bg-white/5 flex items-center justify-center text-xs text-gray-500">
                                                                {copy.noImage}
                                                            </div>
                                                        )}
                                                        <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                                                            <span className="text-xs text-white font-bold">{copy.select}</span>
                                                        </div>
                                                        {/* Frame Number Badge */}
                                                        <div className="absolute top-1 left-1 bg-black/60 px-1.5 rounded text-[10px] text-gray-300 backdrop-blur-sm">
                                                            #{frame.id.slice(0, 4)}
                                                        </div>
                                                        {generationBadge && (
                                                            <div
                                                                className={`absolute top-1 right-1 rounded border px-1.5 text-[10px] backdrop-blur-sm ${generationBadgeDegraded
                                                                    ? "border-amber-400/30 bg-black/70 text-amber-200"
                                                                    : "border-emerald-400/30 bg-black/70 text-emerald-200"
                                                                    }`}
                                                                title={getGenerationTooltip(frame)}
                                                            >
                                                                {generationBadge}
                                                            </div>
                                                        )}
                                                        {/* Extract Last Frame Button */}
                                                        {prevVideoCompleted && (
                                                            <button
                                                                onClick={(e) => handleExtractLastFrame(frame.id, e)}
                                                                disabled={isExtracting}
                                                                className={`absolute bottom-1 right-1 flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium backdrop-blur-sm transition-colors ${
                                                                    hasExtracted
                                                                        ? "bg-green-500/20 text-green-400 border border-green-500/30 hover:bg-purple-500/20 hover:text-purple-300 hover:border-purple-500/30"
                                                                        : "bg-purple-500/20 text-purple-300 border border-purple-500/30 hover:bg-purple-500/40"
                                                                } disabled:opacity-50`}
                                                                title={hasExtracted ? copy.reExtractPrevEndFrame : copy.usePrevEndFrame}
                                                            >
                                                                {isExtracting ? (
                                                                    <Loader2 size={10} className="animate-spin" />
                                                                ) : hasExtracted ? (
                                                                    <><Check size={10} /> {copy.applied}</>
                                                                ) : (
                                                                    <><Film size={10} /> {copy.prevEndFrame}</>
                                                                )}
                                                            </button>
                                                        )}
                                                    </div>
                                                    );
                                                })}
                                            </div>
                                            );
                                        })() : (
                                            <div className="flex flex-col items-center justify-center h-[200px] text-gray-500 gap-2">
                                                <Layout size={32} className="opacity-20" />
                                                <p className="text-xs">{copy.noStoryboardFrames}</p>
                                            </div>
                                        )}

                                        {/* Selected Preview (Storyboard Mode) */}
                                        {selectedImages.length > 0 && (
                                            <div className="pt-4 border-t border-white/10">
                                                <p className="text-xs text-gray-500 mb-2">{copy.selectedForGeneration}</p>
                                                <div className="flex gap-2 flex-wrap">
                                                    {selectedImages.map((img, idx) => {
                                                        // Find frame to get updated_at for cache busting
                                                        const frame = currentProject?.frames?.find((candidate) => (candidate.rendered_image_url || candidate.image_url) === img);
                                                        const timestamp = frame?.updated_at || 0;
                                                        return (
                                                            <div key={idx} className="relative w-24 aspect-video rounded-lg overflow-hidden border border-white/20">
                                                                <NextImage
                                                                    src={timestamp ? getAssetUrlWithTimestamp(img, timestamp) : getAssetUrl(img)}
                                                                    alt={copy.selectedImageAlt}
                                                                    fill
                                                                    sizes="96px"
                                                                    unoptimized
                                                                    className="object-cover"
                                                                />
                                                                <button
                                                                    onClick={() => removeImage(idx)}
                                                                    className="absolute top-1 right-1 p-0.5 bg-black/60 rounded-full text-white hover:bg-red-500"
                                                                >
                                                                    <X size={10} />
                                                                </button>
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                ) : (
                                    /* Upload Mode Content */
                                    <div className="space-y-4">
                                        <div className="grid grid-cols-3 gap-4">
                                            {selectedImages.map((img, idx) => (
                                                <div key={idx} className="relative aspect-video bg-black/40 rounded-xl overflow-hidden border border-white/10 group">
                                                    <NextImage
                                                        src={getAssetUrl(img)}
                                                        alt={copy.inputImageAlt(idx)}
                                                        fill
                                                        sizes="(max-width: 768px) 100vw, 33vw"
                                                        unoptimized
                                                        className="object-contain"
                                                    />
                                                    <button
                                                        onClick={() => removeImage(idx)}
                                                        className="absolute top-2 right-2 p-1 bg-black/60 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500"
                                                    >
                                                        <X size={12} />
                                                    </button>
                                                    {img.startsWith("blob:") && !uploadingPaths[img] && (
                                                        <div className="absolute inset-0 flex items-center justify-center bg-black/50">
                                                            <Loader2 className="animate-spin text-white" size={20} />
                                                        </div>
                                                    )}
                                                </div>
                                            ))}

                                            {/* Add Button */}
                                            <div
                                                onClick={() => document.getElementById('image-upload')?.click()}
                                                className="aspect-video border-2 border-dashed border-white/10 rounded-xl flex flex-col items-center justify-center bg-white/5 hover:bg-white/10 transition-colors cursor-pointer relative min-h-[100px]"
                                            >
                                                <input
                                                    id="image-upload"
                                                    type="file"
                                                    accept="image/*"
                                                    multiple
                                                    className="hidden"
                                                    onChange={(e) => handleImageSelect(e.target.files)}
                                                />
                                                <Plus className="text-gray-400 mb-2" size={24} />
                                                <p className="text-gray-400 text-xs font-medium">{copy.addImage}</p>
                                            </div>
                                        </div>

                                        {/* Quick Select from Assets (Only in Upload Mode) */}
                                        {availableAssets.length > 0 && (
                                            <div className="mt-4 pt-4 border-t border-white/10">
                                                <p className="text-xs text-gray-500 mb-2">{copy.quickSelectFromAssets}</p>
                                                <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-hide">
                                                    {availableAssets.slice(0, 10).map((asset, i) => (
                                                        <div
                                                            key={i}
                                                            onClick={() => handleAssetSelect(asset.url)}
                                                            className="w-16 h-16 relative rounded-lg overflow-hidden flex-shrink-0 border border-white/10 hover:border-primary cursor-pointer"
                                                        >
                                                            <NextImage
                                                                src={getAssetUrl(asset.url)}
                                                                alt={asset.title}
                                                                fill
                                                                sizes="64px"
                                                                unoptimized
                                                                className="object-cover"
                                                            />
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {/* === R2V MODE: Cast Slots + Frame Description === */}
                    {generationMode === 'r2v' && (
                        <div className="space-y-6">
                            {/* Frame Description Cards */}
                            <div className="space-y-3">
                                <label className="text-sm font-medium text-gray-300">{copy.selectFrame}</label>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[200px] overflow-y-auto custom-scrollbar pr-2">
                                    {currentProject?.frames && currentProject.frames.length > 0 ? (
                                        currentProject.frames.map((frame) => {
                                            const generationBadge = getGenerationBadgeText(frame);
                                            const generationBadgeDegraded = isGenerationDegraded(frame);

                                            return (
                                                <div
                                                    key={frame.id}
                                                    onClick={() => handleR2VFrameSelect(frame)}
                                                    className={`p-3 rounded-lg border cursor-pointer transition-all ${selectedFrameId === frame.id
                                                        ? "border-purple-500 bg-purple-500/10 ring-2 ring-purple-500/30"
                                                        : "border-white/10 bg-black/20 hover:border-white/30"
                                                        }`}
                                                >
                                                    <div className="flex items-start gap-3">
                                                        {/* Frame thumbnail */}
                                                        <div className="relative w-16 h-10 rounded overflow-hidden flex-shrink-0 bg-black/40">
                                                            {frame.image_url ? (
                                                                <NextImage
                                                                    src={getAssetUrlWithTimestamp(frame.image_url, frame.updated_at)}
                                                                    alt={copy.frameAlt(frame.id)}
                                                                    fill
                                                                    sizes="64px"
                                                                    unoptimized
                                                                    className="object-cover"
                                                                />
                                                            ) : (
                                                                <div className="w-full h-full flex items-center justify-center text-gray-600">
                                                                    <Layout size={14} />
                                                                </div>
                                                            )}
                                                        </div>
                                                        {/* Frame description */}
                                                        <div className="flex-1 min-w-0">
                                                            <div className="mb-1 flex flex-wrap items-center gap-2">
                                                                <p className="text-xs text-gray-400">#{frame.id.slice(0, 6)}</p>
                                                                {generationBadge && (
                                                                    <span
                                                                        className={`rounded-full border px-1.5 py-0.5 text-[10px] ${generationBadgeDegraded
                                                                            ? "border-amber-400/30 bg-amber-400/10 text-amber-200"
                                                                            : "border-emerald-400/20 bg-emerald-400/10 text-emerald-200"
                                                                            }`}
                                                                        title={getGenerationTooltip(frame)}
                                                                    >
                                                                        {generationBadge}
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <p className="text-xs text-gray-300 line-clamp-2">
                                                                {frame.action_description || frame.image_prompt || commonMessages.noDescription}
                                                            </p>
                                                            {frame.dialogue && (
                                                                <p className="text-[10px] text-purple-400 mt-1 italic line-clamp-1">
                                                                    “{frame.dialogue}”
                                                                </p>
                                                            )}
                                                        </div>
                                                        {/* Selected indicator */}
                                                        {selectedFrameId === frame.id && (
                                                            <div className="w-5 h-5 rounded-full bg-purple-500 flex items-center justify-center flex-shrink-0">
                                                                <Check size={12} className="text-white" />
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                            );
                                        })
                                    ) : (
                                        <div className="col-span-2 flex flex-col items-center justify-center h-[100px] text-gray-500 gap-2">
                                            <Layout size={24} className="opacity-20" />
                                            <p className="text-xs">{copy.noStoryboardData}</p>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Cast Slots (卡司槽位) */}
                            <div className="space-y-3">
                                <label className="text-sm font-medium text-gray-300">{copy.castSlots}</label>
                                <div className="grid grid-cols-3 gap-4">
                                    {[0, 1, 2].map((slotIndex) => {
                                        const slot = castSlots[slotIndex];
                                        const slotTitle = slotIndex === 0 ? copy.leadRole : copy.supportingRole;
                                        const video = slot?.url ? availableReferenceVideos.find(v => v.url === slot.url) : null;

                                        return (
                                            <div
                                                key={slotIndex}
                                                className={`relative rounded-xl border-2 border-dashed transition-all ${slot?.url
                                                    ? "border-purple-500 bg-purple-500/10"
                                                    : "border-white/20 bg-black/20 hover:border-white/40"
                                                    }`}
                                            >
                                                {/* Slot Header */}
                                                <div className="absolute top-2 left-2 z-10">
                                                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-600 text-white font-bold">
                                                        {copy.castSlotLabel(slotIndex)}
                                                    </span>
                                                </div>

                                                {slot?.url ? (
                                                    /* Filled Slot */
                                                    <div className="aspect-video relative">
                                                        <NextImage
                                                            src={getAssetUrl(video?.thumbnail || "")}
                                                            alt={slot.name}
                                                            fill
                                                            sizes="(max-width: 768px) 100vw, 16rem"
                                                            unoptimized
                                                            className="object-cover rounded-xl"
                                                        />
                                                        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-2 rounded-b-xl">
                                                            <p className="text-xs text-white font-medium truncate">{slot.name}</p>
                                                        </div>
                                                        <button
                                                            onClick={() => handleClearCastSlot(slotIndex)}
                                                            className="absolute top-2 right-2 p-1 bg-black/60 rounded-full text-white hover:bg-red-500 transition-colors"
                                                        >
                                                            <X size={12} />
                                                        </button>
                                                    </div>
                                                ) : (
                                                    /* Empty Slot */
                                                    <div className="aspect-video flex flex-col items-center justify-center p-4">
                                                        <p className="text-xs text-gray-400 mb-2">{slotTitle}</p>
                                                        <select
                                                            className="w-full text-xs bg-black/40 border border-white/20 rounded-lg px-2 py-1.5 text-gray-300 focus:border-purple-500 focus:outline-none"
                                                            value=""
                                                            onChange={(e) => {
                                                                const selectedVideo = availableReferenceVideos.find(v => v.url === e.target.value);
                                                                if (selectedVideo) {
                                                                    handleCastSlotSelect(slotIndex, { url: selectedVideo.url, name: selectedVideo.assetName });
                                                                }
                                                            }}
                                                        >
                                                            <option value="">{copy.selectReferenceVideo}</option>
                                                            {availableReferenceVideos.map((v, i) => (
                                                                <option key={i} value={v.url}>{v.assetName} - {getReferenceVideoTypeLabel(v.type)}</option>
                                                            ))}
                                                        </select>
                                                        {slotIndex === 0 && (
                                                            <p className="text-[10px] text-amber-400 mt-2">{copy.required}</p>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                                {availableReferenceVideos.length === 0 && (
                                    <p className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
                                        {copy.noReferenceVideos}
                                    </p>
                                )}
                            </div>
                        </div>
                    )}


                    {generationMode === "i2v" && isSeedanceModel && (
                        <div className="space-y-4 rounded-2xl border border-emerald-500/20 bg-emerald-500/[0.06] p-4">
                            <div className="space-y-1">
                                <h3 className="text-sm font-semibold text-white">{copy.seedanceReferencePanel}</h3>
                                <p className="text-xs text-gray-400 leading-relaxed">{copy.seedanceReferenceHint}</p>
                            </div>

                            {isSeedancePreviewSubmit && (
                                <div className="grid gap-3 md:grid-cols-2">
                                    {seedanceSubmissionState.reason === "manual_preview" && (
                                        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                                            {copy.payloadWillNotSubmit}
                                        </div>
                                    )}
                                    {seedanceSubmissionState.reason === "workflow_missing_video" && (
                                        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                                            {copy.workflowNeedsReferenceVideo}
                                        </div>
                                    )}
                                </div>
                            )}

                            {seedanceSubmissionState.reason === "ready" && params.seedanceWorkflow !== "standard" && (
                                <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
                                    {copy.workflowReadyToSubmit}
                                </div>
                            )}

                            {seedancePreviewPayloads.length > 1 && (
                                <div className="rounded-xl border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-100">
                                    {copy.payloadPreviewExpanded(seedancePreviewPayloads.length)}
                                </div>
                            )}

                            <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(260px,1fr)]">
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                        <label className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
                                            {copy.seedanceReferenceVideos}
                                        </label>
                                        <span className="text-[11px] text-gray-500">
                                            {copy.seedanceReferenceVideosHint}
                                        </span>
                                    </div>

                                    {availableReferenceVideos.length > 0 ? (
                                        <div className="grid gap-3 sm:grid-cols-2 max-h-[320px] overflow-y-auto custom-scrollbar pr-1">
                                            {availableReferenceVideos.map((video, index) => {
                                                const isSelected = selectedReferenceVideos.includes(video.url);
                                                return (
                                                    <button
                                                        key={`${video.url}-${index}`}
                                                        type="button"
                                                        onClick={() => handleReferenceVideoSelect(video.url)}
                                                        className={`overflow-hidden rounded-xl border text-left transition-all ${isSelected
                                                            ? "border-emerald-400/60 bg-emerald-500/10 shadow-[0_0_0_1px_rgba(16,185,129,0.18)]"
                                                            : "border-white/10 bg-black/20 hover:border-white/25 hover:bg-white/[0.04]"
                                                            }`}
                                                    >
                                                        <div className="relative aspect-video overflow-hidden bg-black/40">
                                                            {video.thumbnail ? (
                                                                <NextImage
                                                                    src={getAssetUrl(video.thumbnail)}
                                                                    alt={video.assetName}
                                                                    fill
                                                                    sizes="(max-width: 640px) 100vw, 50vw"
                                                                    unoptimized
                                                                    className="object-cover"
                                                                />
                                                            ) : (
                                                                <div className="w-full h-full flex items-center justify-center text-xs text-gray-500">
                                                                    {copy.seedanceReferenceVideos}
                                                                </div>
                                                            )}
                                                            <div className="absolute left-2 top-2 rounded-full bg-black/65 px-2 py-0.5 text-[10px] text-gray-200">
                                                                {getReferenceVideoTypeLabel(video.type)}
                                                            </div>
                                                            {isSelected && (
                                                                <div className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-emerald-400 text-black">
                                                                    <Check size={14} />
                                                                </div>
                                                            )}
                                                        </div>
                                                        <div className="space-y-1 p-3">
                                                            <p className="truncate text-sm font-medium text-white">{video.assetName}</p>
                                                            <p className="truncate text-[11px] text-gray-500">{video.title}</p>
                                                        </div>
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    ) : (
                                        <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
                                            {copy.noSeedanceReferenceVideos}
                                        </p>
                                    )}
                                </div>

                                <div className="space-y-3">
                                    <div className="rounded-xl border border-white/10 bg-black/25 p-4 space-y-3">
                                        <div className="space-y-1">
                                            <label className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
                                                {copy.seedanceReferenceAudio}
                                            </label>
                                            <p className="text-[11px] text-gray-500 leading-relaxed">
                                                {copy.seedanceReferenceAudioHint}
                                            </p>
                                        </div>

                                        <input
                                            ref={referenceAudioInputRef}
                                            type="file"
                                            accept="audio/*"
                                            className="hidden"
                                            onChange={handleSeedanceReferenceAudioUpload}
                                        />

                                        {params.referenceAudioUrl ? (
                                            <div className="space-y-3">
                                                <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/10 p-3">
                                                    <p className="text-xs font-medium text-emerald-200">{copy.referenceAudioReady}</p>
                                                    <p className="mt-1 break-all text-[11px] text-gray-400">
                                                        {allResolvedReferenceAudioUrl}
                                                    </p>
                                                </div>
                                                <div className="flex gap-2">
                                                    <button
                                                        type="button"
                                                        onClick={() => referenceAudioInputRef.current?.click()}
                                                        disabled={isUploadingReferenceAudio}
                                                        className="flex-1 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-gray-200 transition-colors hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
                                                    >
                                                        {isUploadingReferenceAudio ? copy.uploadReferenceAudio : copy.uploadReferenceAudio}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={() => onParamsChange({ referenceAudioUrl: "" })}
                                                        className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200 transition-colors hover:bg-red-500/20"
                                                    >
                                                        {copy.removeReferenceAudio}
                                                    </button>
                                                </div>
                                            </div>
                                        ) : (
                                            <button
                                                type="button"
                                                onClick={() => referenceAudioInputRef.current?.click()}
                                                disabled={isUploadingReferenceAudio}
                                                className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-white/15 bg-white/5 px-3 py-3 text-xs text-gray-200 transition-colors hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
                                            >
                                                {isUploadingReferenceAudio ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                                                {isUploadingReferenceAudio ? copy.uploadReferenceAudio : copy.uploadReferenceAudio}
                                            </button>
                                        )}
                                    </div>

                                    <div className="rounded-xl border border-white/10 bg-black/25 p-4">
                                        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">{copy.currentCombo}</p>
                                        <div className="mt-3 flex flex-wrap gap-2">
                                            <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-gray-300">
                                                {seedanceSummaryCopy.image} {resolvedSeedanceImages.length}
                                            </span>
                                            <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-gray-300">
                                                {seedanceSummaryCopy.video} {resolvedReferenceVideoUrls.length}
                                            </span>
                                            <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-gray-300">
                                                {seedanceSummaryCopy.audio} {resolvedReferenceAudioUrl ? 1 : 0}
                                            </span>
                                            <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-gray-300">
                                                {seedanceSummaryCopy.payload} {seedancePreviewPayloads.length}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <AnimatePresence>
                                {showSeedancePayloadPreview && (
                                    <motion.div
                                        initial={{ opacity: 0, y: -8 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0, y: -8 }}
                                        className="space-y-4 rounded-2xl border border-white/10 bg-black/35 p-4"
                                    >
                                        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                                            <div className="space-y-1">
                                                <h4 className="text-sm font-semibold text-white">{copy.payloadPreviewTitle}</h4>
                                                <p className="text-xs text-gray-400">{copy.payloadPreviewHint}</p>
                                            </div>
                                            <div className="flex gap-2">
                                                <button
                                                    type="button"
                                                    onClick={handleCopySeedancePayloads}
                                                    className="rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-gray-200 transition-colors hover:bg-white/10"
                                                >
                                                    {seedancePreviewPayloads.length > 1 ? copy.copyAllPayloads : commonActions.copy}
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setShowSeedancePayloadPreview(false)}
                                                    className="rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-gray-200 transition-colors hover:bg-white/10"
                                                >
                                                    {commonActions.close}
                                                </button>
                                            </div>
                                        </div>

                                        {seedancePayloadWarnings.length > 0 && (
                                            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
                                                <p className="mb-2 text-xs font-medium text-amber-200">{copy.payloadWarnings}</p>
                                                <ul className="space-y-1 pl-4 text-xs text-amber-100/90 list-disc">
                                                    {seedancePayloadWarnings.map((warning) => (
                                                        <li key={warning}>{warning}</li>
                                                    ))}
                                                </ul>
                                            </div>
                                        )}

                                        <div className="space-y-4">
                                            {seedancePreviewPayloads.map((preview) => (
                                                <div
                                                    key={preview.key}
                                                    className="overflow-hidden rounded-xl border border-white/10 bg-black/30"
                                                >
                                                    <div className="flex flex-col gap-2 border-b border-white/10 px-4 py-3 md:flex-row md:items-center md:justify-between">
                                                        <div className="space-y-1">
                                                            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-300">
                                                                {preview.label}
                                                            </p>
                                                            {preview.imageUrl && (
                                                                <p className="text-[11px] text-gray-500">
                                                                    {copy.payloadPreviewImage(preview.imageUrl)}
                                                                </p>
                                                            )}
                                                        </div>
                                                        <button
                                                            type="button"
                                                            onClick={() => handleCopySingleSeedancePayload(preview.payload)}
                                                            className="rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-xs text-gray-200 transition-colors hover:bg-white/10"
                                                        >
                                                            {commonActions.copy}
                                                        </button>
                                                    </div>
                                                    <pre className="max-h-[320px] overflow-auto p-4 text-xs leading-6 text-emerald-100 custom-scrollbar">
                                                        {JSON.stringify(preview.payload, null, 2)}
                                                    </pre>
                                                </div>
                                            ))}
                                        </div>
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>
                    )}

                    {/* 2. Prompt Input */}
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium text-gray-300">{copy.prompt}</label>
                            <div className="flex items-center gap-2">
                                {generationMode === 'i2v' && (
                                    <div className="relative">
                                        <button
                                            onClick={() => promptBuilderRef.current?.insertCamera()}
                                            className="text-xs flex items-center gap-1 px-2 py-1 rounded transition-colors text-gray-400 hover:text-white hover:bg-white/5"
                                        >
                                            <Video size={12} /> {copy.camera}
                                        </button>
                                    </div>
                                )}
                                <button
                                    onClick={() => handlePolish()}
                                    disabled={isPolishing || !prompt}
                                    className="text-xs text-primary hover:text-primary/80 flex items-center gap-1 disabled:opacity-50"
                                >
                                    {isPolishing ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
                                    {copy.aiPolish}
                                </button>
                                <button
                                    onClick={() => setSegments([{ type: "text", value: "", id: "init" }])}
                                    className="text-xs text-gray-400 hover:text-white flex items-center gap-1 px-2 py-1 rounded hover:bg-white/5 transition-colors"
                                    title={copy.clearPrompt}
                                >
                                    <Eraser size={12} /> {commonActions.clear}
                                </button>
                            </div>
                        </div>

                        {/* Character Insert Shortcuts (R2V Mode Only) */}
                        {generationMode === 'r2v' && (
                            <div className="flex gap-2 flex-wrap">
                                {[0, 1, 2].map((idx) => {
                                    const slot = castSlots[idx];
                                    const isActive = slot?.url;
                                    const video = isActive ? availableReferenceVideos.find(v => v.url === slot.url) : null;
                                    return (
                                        <button
                                            key={idx}
                                            onClick={() => insertCharacter(idx)}
                                            disabled={!isActive}
                                            className={`text-xs px-2 py-1 rounded-lg border transition-all flex items-center gap-1.5 ${isActive
                                                ? "border-purple-500/50 bg-purple-500/10 text-purple-400 hover:bg-purple-500/20"
                                                : "border-white/10 bg-white/5 text-gray-500 cursor-not-allowed"
                                                }`}
                                        >
                                            {video?.thumbnail ? (
                                                <NextImage
                                                    src={getAssetUrl(video.thumbnail)}
                                                    alt={video.title}
                                                    width={16}
                                                    height={16}
                                                    unoptimized
                                                    className="w-4 h-4 rounded-full object-cover"
                                                />
                                            ) : (
                                                <span className="w-4 h-4 rounded-full bg-purple-500/30 flex items-center justify-center text-[10px]">+</span>
                                            )}
                                            <span>{copy.insertCharacter(slot?.name || copy.castSlotLabel(idx))}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        )}

                        {shouldShowPromptLibrary && (
                            <div className="rounded-2xl border border-cyan-500/20 bg-cyan-500/5 p-4 space-y-4">
                                <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                                    <div>
                                        <p className="text-sm font-semibold text-white">{copy.promptLibraryTitle}</p>
                                        <p className="mt-1 text-xs leading-relaxed text-cyan-100/80">
                                            {copy.promptLibraryHint}
                                        </p>
                                    </div>
                                    <span className="rounded-full border border-cyan-400/20 bg-black/20 px-3 py-1 text-[10px] text-cyan-100">
                                        {promptLibraryContextLabel}
                                    </span>
                                </div>

                                <div className="space-y-2">
                                    <div className="flex items-center justify-between">
                                        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-200/80">
                                            {copy.promptScaffolds}
                                        </p>
                                    </div>
                                    <div className="grid gap-3 md:grid-cols-2">
                                        {availablePromptScaffolds.map((item) => (
                                            <div
                                                key={item.id}
                                                className="rounded-xl border border-white/10 bg-black/20 p-3"
                                            >
                                                <div className="flex items-start justify-between gap-3">
                                                    <div>
                                                        <p className="text-sm font-medium text-white">{item.title}</p>
                                                        <p className="mt-1 text-xs leading-relaxed text-gray-300">
                                                            {item.summary}
                                                        </p>
                                                    </div>
                                                </div>
                                                <div className="mt-2 flex flex-wrap gap-2">
                                                    {item.tags.map((tag) => (
                                                        <span
                                                            key={`${item.id}-${tag}`}
                                                            className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[10px] text-gray-300"
                                                        >
                                                            {tag}
                                                        </span>
                                                    ))}
                                                </div>
                                                <pre className="mt-3 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-3 text-[11px] leading-5 text-cyan-50/90 custom-scrollbar">
                                                    {item.prompt}
                                                </pre>
                                                <div className="mt-3 flex gap-2">
                                                    <button
                                                        type="button"
                                                        data-testid={`prompt-block-replace-${item.id}`}
                                                        onClick={() => handleApplyPromptBlock(item.prompt, "replace")}
                                                        className="rounded-lg bg-cyan-500 px-3 py-1.5 text-xs font-medium text-black transition hover:bg-cyan-400"
                                                    >
                                                        {copy.replaceTemplate}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        data-testid={`prompt-block-append-${item.id}`}
                                                        onClick={() => handleApplyPromptBlock(item.prompt, "append")}
                                                        className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-white transition hover:bg-white/10"
                                                    >
                                                        {copy.appendTemplate}
                                                    </button>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                <div className="space-y-3">
                                    <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                                        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-200/80">
                                            {copy.promptTemplates}
                                        </p>
                                        <div className="flex flex-wrap gap-2">
                                            {SEEDANCE_PROMPT_TEMPLATE_CATEGORIES.map((item) => (
                                                <button
                                                    key={item.value}
                                                    type="button"
                                                    onClick={() => setActivePromptTemplateCategory(item.value)}
                                                    className={`rounded-full border px-3 py-1 text-[11px] transition ${activePromptTemplateCategory === item.value
                                                        ? "border-cyan-400/60 bg-cyan-400/15 text-cyan-50"
                                                        : "border-white/10 bg-white/5 text-gray-300 hover:bg-white/10"
                                                        }`}
                                                >
                                                    {item.label}
                                                </button>
                                            ))}
                                        </div>
                                    </div>

                                    {availablePromptTemplates.length > 0 ? (
                                        <div className="grid gap-3 md:grid-cols-2">
                                            {availablePromptTemplates.map((item) => (
                                                <div
                                                    key={item.id}
                                                    className="rounded-xl border border-white/10 bg-black/20 p-3"
                                                >
                                                    <div className="flex items-start justify-between gap-3">
                                                        <div>
                                                            <p className="text-sm font-medium text-white">{item.title}</p>
                                                            <p className="mt-1 text-xs leading-relaxed text-gray-300">
                                                                {item.summary}
                                                            </p>
                                                        </div>
                                                    </div>
                                                    <div className="mt-2 flex flex-wrap gap-2">
                                                        {item.tags.map((tag) => (
                                                            <span
                                                                key={`${item.id}-${tag}`}
                                                                className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[10px] text-gray-300"
                                                            >
                                                                {tag}
                                                            </span>
                                                        ))}
                                                    </div>
                                                    <pre className="mt-3 max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-3 text-[11px] leading-5 text-gray-100 custom-scrollbar">
                                                        {item.prompt}
                                                    </pre>
                                                    <div className="mt-3 flex gap-2">
                                                        <button
                                                            type="button"
                                                            data-testid={`prompt-template-replace-${item.id}`}
                                                            onClick={() => handleApplyPromptBlock(item.prompt, "replace")}
                                                            className="rounded-lg bg-cyan-500 px-3 py-1.5 text-xs font-medium text-black transition hover:bg-cyan-400"
                                                        >
                                                            {copy.replaceTemplate}
                                                        </button>
                                                        <button
                                                            type="button"
                                                            data-testid={`prompt-template-append-${item.id}`}
                                                            onClick={() => handleApplyPromptBlock(item.prompt, "append")}
                                                            className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-white transition hover:bg-white/10"
                                                        >
                                                            {copy.appendTemplate}
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="rounded-xl border border-dashed border-white/10 bg-black/20 px-4 py-5 text-sm text-gray-400">
                                            {copy.noPromptTemplates}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}

                        <div className="relative">
                            <PromptBuilder
                                ref={promptBuilderRef}
                                segments={segments}
                                onChange={setSegments}
                                placeholder={isR2VMode
                                    ? copy.r2vPromptPlaceholder
                                    : copy.i2vPromptPlaceholder
                                }
                            />
                        </div>

                        <PromptQualityPanel
                            issues={promptQualityIssues}
                            title={copy.promptQualityTitle}
                        />

                        {/* Polished Result Display - Bilingual */}
                        <AnimatePresence>
                            {polishedPrompt && (
                                <motion.div
                                    initial={{ opacity: 0, y: -10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={{ opacity: 0, y: -10 }}
                                    className="bg-purple-900/20 border border-purple-500/30 rounded-lg p-3 mt-2 space-y-3"
                                >
                                    <div className="flex justify-between items-start">
                                        <span className="text-xs font-bold text-purple-400 flex items-center gap-1">
                                            <Wand2 size={12} /> {copy.bilingualPolish}
                                        </span>
                                        <button
                                            onClick={() => { setPolishedPrompt(null); setFeedbackText(""); }}
                                            className="text-[10px] text-gray-400 hover:text-white"
                                        >
                                            ✕
                                        </button>
                                    </div>

                                    {/* Chinese Prompt */}
                                    <div className="space-y-1">
                                        <div className="flex justify-between items-center">
                                            <span className="text-[10px] font-bold text-gray-500 uppercase">{copy.chinesePreview}</span>
                                            <button
                                                onClick={() => {
                                                    navigator.clipboard.writeText(polishedPrompt.cn);
                                                    alert(copy.copyChineseSuccess);
                                                }}
                                                className="text-[10px] text-gray-400 hover:text-white bg-black/20 px-2 py-0.5 rounded"
                                            >
                                                {commonActions.copy}
                                            </button>
                                        </div>
                                        <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap bg-black/20 p-2 rounded">
                                            {polishedPrompt.cn}
                                        </p>
                                    </div>

                                    {/* English Prompt */}
                                    <div className="space-y-1">
                                        <div className="flex justify-between items-center">
                                            <span className="text-[10px] font-bold text-gray-500 uppercase">{copy.englishForGeneration}</span>
                                            <div className="flex gap-1">
                                                <button
                                                    onClick={() => {
                                                        navigator.clipboard.writeText(polishedPrompt.en);
                                                        alert(copy.copyEnglishSuccess);
                                                    }}
                                                    className="text-[10px] text-gray-400 hover:text-white bg-black/20 px-2 py-0.5 rounded"
                                                >
                                                    {commonActions.copy}
                                                </button>
                                                <button
                                                    onClick={() => {
                                                        setSegments([{ type: "text", value: polishedPrompt.en, id: `polished-${Date.now()}` }]);
                                                        setPolishedPrompt(null);
                                                    }}
                                                    className="text-[10px] text-white bg-purple-600 hover:bg-purple-500 px-2 py-0.5 rounded font-bold"
                                                >
                                                    {commonActions.apply}
                                                </button>
                                            </div>
                                        </div>
                                        <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap bg-black/20 p-2 rounded font-mono">
                                            {polishedPrompt.en}
                                        </p>
                                    </div>

                                    {/* Feedback for iterative refinement */}
                                    <div className="space-y-2 pt-2 border-t border-purple-500/20">
                                        <div className="flex gap-2">
                                            <input
                                                type="text"
                                                value={feedbackText}
                                                onChange={(e) => setFeedbackText(e.target.value)}
                                                onKeyDown={(e) => {
                                                    if (e.key === "Enter" && feedbackText.trim() && !isPolishing) {
                                                        handlePolish(feedbackText.trim());
                                                    }
                                                }}
                                                placeholder={copy.feedbackPlaceholder}
                                                className="flex-1 text-xs bg-black/30 border border-purple-500/20 rounded px-2 py-1.5 text-white placeholder-gray-500 focus:outline-none focus:border-purple-500/50"
                                            />
                                            <button
                                                onClick={() => handlePolish(feedbackText.trim())}
                                                disabled={isPolishing || !feedbackText.trim()}
                                                className="text-xs text-white bg-purple-600 hover:bg-purple-500 px-3 py-1.5 rounded font-medium flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                                            >
                                                {isPolishing ? <Loader2 size={10} className="animate-spin" /> : <Wand2 size={10} />}
                                                {copy.repolish}
                                            </button>
                                        </div>
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </div>
                </div>
            </div>

            {/* 4. Fixed Action Bar */}
            <div className="p-6 border-t border-white/10 bg-black/40 backdrop-blur-md z-10">
                <div className="max-w-4xl mx-auto w-full">
                    <div className="flex flex-col gap-3 sm:flex-row">
                        {generationMode === "i2v" && isSeedanceModel && (
                            <button
                                type="button"
                                onClick={handleOpenSeedancePreview}
                                disabled={!canOpenSeedancePreview}
                                className="sm:w-auto rounded-xl border border-white/15 bg-white/5 px-4 py-4 text-sm font-medium text-gray-200 transition-all hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                {copy.previewPayload}
                            </button>
                        )}

                        <button
                            onClick={handleSubmit}
                            disabled={!canRunPrimaryAction}
                            data-testid="video-submit"
                            className={`flex-1 py-4 rounded-xl font-bold text-lg flex items-center justify-center gap-2 transition-all transform active:scale-[0.99] ${submitSuccess
                                ? "bg-green-500 text-white"
                                : isSeedancePreviewSubmit
                                    ? "bg-amber-400 hover:bg-amber-300 text-black"
                                    : "bg-primary hover:bg-primary/90 text-white"
                                } disabled:opacity-50 disabled:cursor-not-allowed`}
                        >
                            {isSubmitting ? (
                                <>
                                    <Loader2 className="animate-spin" /> {copy.submitting}
                                </>
                            ) : submitSuccess ? (
                                <>
                                    <Plus /> {copy.queued}
                                </>
                            ) : isSeedancePreviewSubmit ? (
                                <>
                                    <Wand2 /> {copy.previewPayload}
                                </>
                            ) : (
                                <>
                                    <Plus /> {copy.submit}
                                </>
                            )}
                        </button>
                    </div>

                    <div className="flex justify-center mt-3">
                        <label className="flex items-center gap-2 text-xs text-gray-500 cursor-pointer hover:text-gray-400">
                            <input type="checkbox" className="rounded bg-white/10 border-white/20" />
                            {copy.clearAfterSubmit}
                        </label>
                    </div>
                </div>
            </div>
        </div>
    );
}
