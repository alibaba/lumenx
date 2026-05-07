import { seedanceTerms } from "@/lib/i18n";

export type SeedanceReferenceMode = "image" | "video" | "combo";
export type SeedanceWorkflow = "standard" | "extend" | "edit";
export type SeedanceExtendMode = "continue" | "prepend" | "trajectory";
export type SeedanceEditMode = "subject_replace" | "object_edit" | "inpaint";
export type SeedancePreviewReason = "ready" | "manual_preview" | "workflow_missing_video";

export interface SeedancePayloadPreviewInput {
    prompt: string;
    model: string;
    duration: number;
    resolution: string;
    aspectRatio: string;
    watermark: boolean;
    cameraFixed: boolean;
    generateAudio: boolean;
    seed?: number;
    referenceMode: SeedanceReferenceMode;
    workflow: SeedanceWorkflow;
    extendMode: SeedanceExtendMode;
    editMode: SeedanceEditMode;
    imageUrls: string[];
    referenceVideoUrls: string[];
    referenceAudioUrl?: string;
}

export interface SeedancePayloadPreviewEntry {
    key: string;
    label: string;
    imageUrl?: string;
    payload: Record<string, unknown>;
}

export interface SeedanceEffectiveMediaInput {
    referenceMode: SeedanceReferenceMode;
    imageUrls: string[];
    referenceVideoUrls: string[];
    referenceAudioUrl?: string;
}

export interface SeedanceSubmissionStateInput extends SeedanceEffectiveMediaInput {
    previewOnly: boolean;
    workflow: SeedanceWorkflow;
}

export interface SeedanceSubmissionState {
    mode: "preview" | "submit";
    reason: SeedancePreviewReason;
}

const referenceModeTerms = seedanceTerms.referenceModes;
const workflowTerms = seedanceTerms.workflows;
const extendModeTerms = seedanceTerms.extendModes;
const editModeTerms = seedanceTerms.editModes;
const payloadCopy = seedanceTerms.payloads;
const warningCopy = seedanceTerms.warnings;

export const SEEDANCE_REFERENCE_MODE_OPTIONS: { value: SeedanceReferenceMode; label: string }[] = [
    { value: referenceModeTerms.image.value, label: referenceModeTerms.image.label },
    { value: referenceModeTerms.video.value, label: referenceModeTerms.video.label },
    { value: referenceModeTerms.combo.value, label: referenceModeTerms.combo.label },
];

export const SEEDANCE_WORKFLOW_OPTIONS: { value: SeedanceWorkflow; label: string }[] = [
    { value: workflowTerms.standard.value, label: workflowTerms.standard.label },
    { value: workflowTerms.extend.value, label: workflowTerms.extend.label },
    { value: workflowTerms.edit.value, label: workflowTerms.edit.label },
];

export const SEEDANCE_EXTEND_MODE_OPTIONS: { value: SeedanceExtendMode; label: string }[] = [
    { value: extendModeTerms.continue.value, label: extendModeTerms.continue.label },
    { value: extendModeTerms.prepend.value, label: extendModeTerms.prepend.label },
    { value: extendModeTerms.trajectory.value, label: extendModeTerms.trajectory.label },
];

export const SEEDANCE_EDIT_MODE_OPTIONS: { value: SeedanceEditMode; label: string }[] = [
    { value: editModeTerms.subjectReplace.value, label: editModeTerms.subjectReplace.label },
    { value: editModeTerms.objectEdit.value, label: editModeTerms.objectEdit.label },
    { value: editModeTerms.inpaint.value, label: editModeTerms.inpaint.label },
];

const SEEDANCE_IMAGE_REFERENCE_MODES: SeedanceReferenceMode[] = ["image", "combo"];
const SEEDANCE_VIDEO_REFERENCE_MODES: SeedanceReferenceMode[] = ["video", "combo"];

function appendMediaContent(
    content: Array<Record<string, unknown>>,
    urls: string[],
    type: "image_url" | "video_url",
) {
    urls
        .filter((url) => !!url)
        .forEach((url) => {
            content.push({
                type,
                [type]: {
                    url,
                },
            });
        });
}

export function getSeedanceReferenceModeLabel(mode?: string) {
    return SEEDANCE_REFERENCE_MODE_OPTIONS.find((item) => item.value === mode)?.label || seedanceTerms.defaults.referenceMode;
}

export function getSeedanceWorkflowLabel(workflow?: string) {
    return SEEDANCE_WORKFLOW_OPTIONS.find((item) => item.value === workflow)?.label || seedanceTerms.defaults.workflow;
}

export function getSeedanceExtendModeLabel(mode?: string) {
    return SEEDANCE_EXTEND_MODE_OPTIONS.find((item) => item.value === mode)?.label || seedanceTerms.defaults.extendMode;
}

export function getSeedanceEditModeLabel(mode?: string) {
    return SEEDANCE_EDIT_MODE_OPTIONS.find((item) => item.value === mode)?.label || seedanceTerms.defaults.editMode;
}

export function getSeedanceWorkflowMode(
    workflow: SeedanceWorkflow,
    extendMode: SeedanceExtendMode,
    editMode: SeedanceEditMode,
) {
    if (workflow === "extend") {
        return extendMode;
    }

    if (workflow === "edit") {
        return editMode;
    }

    return undefined;
}

export function getSeedanceEffectiveMedia(input: SeedanceEffectiveMediaInput) {
    const imageUrls = SEEDANCE_IMAGE_REFERENCE_MODES.includes(input.referenceMode)
        ? input.imageUrls.filter(Boolean)
        : [];
    const referenceVideoUrls = SEEDANCE_VIDEO_REFERENCE_MODES.includes(input.referenceMode)
        ? input.referenceVideoUrls.filter(Boolean)
        : [];
    const referenceAudioUrl = input.referenceMode === "combo" && input.referenceAudioUrl
        ? input.referenceAudioUrl
        : undefined;

    return {
        imageUrls,
        referenceVideoUrls,
        referenceAudioUrl,
    };
}

export function getSeedanceSubmissionState(input: SeedanceSubmissionStateInput): SeedanceSubmissionState {
    if (input.previewOnly) {
        return {
            mode: "preview",
            reason: "manual_preview",
        };
    }

    const effectiveMedia = getSeedanceEffectiveMedia(input);
    const needsReferenceVideo = input.workflow !== "standard";

    if (needsReferenceVideo && effectiveMedia.referenceVideoUrls.length === 0) {
        return {
            mode: "preview",
            reason: "workflow_missing_video",
        };
    }

    return {
        mode: "submit",
        reason: "ready",
    };
}

export function getSeedanceLowCostPreset() {
    return {
        duration: 5,
        resolution: "480p",
        batchSize: 1,
        generateAudio: false,
        aspectRatio: "adaptive",
        watermark: true,
        cameraFixed: false,
        seedanceWorkflow: "standard" as SeedanceWorkflow,
        seedanceReferenceMode: "image" as SeedanceReferenceMode,
    };
}

export function buildSeedancePayloadPreview(input: SeedancePayloadPreviewInput) {
    const effectiveMedia = getSeedanceEffectiveMedia(input);
    const content: Array<Record<string, unknown>> = [
        { type: "text", text: input.prompt || "" },
    ];

    appendMediaContent(content, effectiveMedia.imageUrls, "image_url");
    appendMediaContent(content, effectiveMedia.referenceVideoUrls, "video_url");

    if (effectiveMedia.referenceAudioUrl) {
        content.push({
            type: "audio_url",
            audio_url: {
                url: effectiveMedia.referenceAudioUrl,
            },
        });
    }

    const previewPayload: Record<string, unknown> = {
        model: input.model,
        content,
        duration: input.duration,
        resolution: input.resolution.toLowerCase(),
        ratio: input.aspectRatio,
        seed: input.seed,
        generate_audio: input.generateAudio,
        watermark: input.watermark,
        camera_fixed: input.cameraFixed,
        reference_mode: input.referenceMode,
        workflow: input.workflow,
    };

    const workflowMode = getSeedanceWorkflowMode(input.workflow, input.extendMode, input.editMode);
    if (workflowMode) {
        previewPayload.workflow_mode = workflowMode;
    }

    return previewPayload;
}

export function buildSeedancePayloadPreviews(input: SeedancePayloadPreviewInput): SeedancePayloadPreviewEntry[] {
    const effectiveMedia = getSeedanceEffectiveMedia(input);
    const previewImages = effectiveMedia.imageUrls;

    if (previewImages.length === 0) {
        return [
            {
                key: "seedance-preview-1",
                label: payloadCopy.singleTaskLabel,
                payload: buildSeedancePayloadPreview({
                    ...input,
                    imageUrls: [],
                }),
            },
        ];
    }

    return previewImages.map((imageUrl, index) => ({
        key: `${index}-${imageUrl}`,
        label: payloadCopy.taskLabel(index + 1, previewImages.length),
        imageUrl,
        payload: buildSeedancePayloadPreview({
            ...input,
            imageUrls: [imageUrl],
        }),
    }));
}

export function getSeedancePayloadWarnings(input: SeedancePayloadPreviewInput) {
    const warnings: string[] = [];
    const hasImage = input.imageUrls.length > 0;
    const hasVideo = input.referenceVideoUrls.length > 0;
    const hasAudio = !!input.referenceAudioUrl;
    const effectiveMedia = getSeedanceEffectiveMedia(input);

    if (!input.prompt.trim()) {
        warnings.push(warningCopy.emptyPrompt);
    }

    if (input.referenceMode === "image" && !hasImage) {
        warnings.push(warningCopy.imageModeMissingImage);
    }

    if (input.referenceMode === "video" && !hasVideo) {
        warnings.push(warningCopy.videoModeMissingVideo);
    }

    if (input.referenceMode === "combo" && !hasImage && !hasVideo && !hasAudio) {
        warnings.push(warningCopy.comboModeMissingMedia);
    }

    if (input.referenceMode === "image" && (hasVideo || hasAudio)) {
        warnings.push(warningCopy.imageModeIgnoresVideoOrAudio);
    }

    if (input.referenceMode === "video" && (hasImage || hasAudio)) {
        warnings.push(warningCopy.videoModeIgnoresImageOrAudio);
    }

    if (input.workflow === "extend" && effectiveMedia.referenceVideoUrls.length === 0) {
        warnings.push(warningCopy.extendNeedsReferenceVideo);
    }

    if (input.workflow === "edit" && effectiveMedia.referenceVideoUrls.length === 0) {
        warnings.push(warningCopy.editNeedsReferenceVideo);
    }

    if (effectiveMedia.imageUrls.length > 1) {
        warnings.push(warningCopy.splitRequests(effectiveMedia.imageUrls.length));
    }

    return warnings;
}
