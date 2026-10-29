import type {
    AtelierGenerationRequest,
    AtelierOrderedGenerationParams,
    AtelierOrderedMediaRole,
    AtelierOrderedMediaSelection,
    AtelierOrderedVideoMode,
    AtelierOrderedVideoModel,
    AtelierVideoCandidateRequest,
    SetAtelierLegacyGenerationDraftInput,
    SetAtelierOrderedGenerationDraftInput,
} from "@/lib/atelierTypes";

export const LEGACY_ATELIER_VIDEO_MODEL = "wan/wan2.7-video#i2v" as const;
export type AtelierComposerModelId = typeof LEGACY_ATELIER_VIDEO_MODEL | AtelierOrderedVideoModel;
export type AtelierComposerMode = "i2v" | "t2v" | "firstlast" | "r2v";

export interface AtelierComposerSource {
    nodeId: string;
    title: string;
    mediaUrl: string;
    kind: "image" | "video" | "audio";
}

export interface AtelierComposerValues {
    modelId: AtelierComposerModelId;
    mode: AtelierComposerMode;
    prompt: string;
    duration: number;
    resolution: string;
    ratio: string;
    seed: string;
    negativePrompt: string;
    promptExtend: boolean;
    watermark: boolean;
    audio: boolean;
    batchSize: number;
    firstSourceKey: string;
    lastSourceKey: string;
    referenceMedia: AtelierOrderedMediaSelection[];
    referenceMediaInitialized: boolean;
}

export interface AtelierComposerModelOption {
    id: AtelierComposerModelId;
    name: string;
    status?: string;
    disabled?: boolean;
    params?: {
        resolution?: { options: string[]; default: string };
        ratio?: { options: string[]; default: string };
        audio?: boolean | { default: boolean };
        promptExtend?: boolean | { default: boolean };
        watermark?: boolean | { default: boolean };
    };
    duration?: { type?: "slider" | "buttons" | "fixed"; default?: number; min?: number; max?: number; step?: number; supports_auto?: boolean; options?: number[]; value?: number };
}


export function composerBooleanDefault(
    model: AtelierComposerModelOption | null | undefined,
    key: "promptExtend" | "watermark" | "audio",
): boolean {
    const value = model?.params?.[key];
    if (value && typeof value === "object") return value.default;
    if (key === "promptExtend") return value === true;
    return false;
}

export interface ResolvedOrderedMediaSelection extends AtelierOrderedMediaSelection {
    available: boolean;
    title?: string;
    kind?: AtelierComposerSource["kind"];
}

const ROLE_ORDER: Record<AtelierOrderedMediaRole, number> = {
    first_frame: 0,
    last_frame: 1,
    reference_image: 2,
    reference_video: 3,
    reference_audio: 4,
};

export function isOrderedComposerModel(modelId: string): modelId is AtelierOrderedVideoModel {
    return modelId === "wan/wan3.0-video#video" || modelId === "wan/wan3.0-video-prime#video";
}

export function sourceKey(source: Pick<AtelierComposerSource, "nodeId" | "mediaUrl">): string {
    return `${source.nodeId}:${source.mediaUrl}`;
}

export function mediaKey(media: Pick<AtelierOrderedMediaSelection, "node_id" | "media_url">): string {
    return `${media.node_id}:${media.media_url}`;
}

export function paramsFromValues(values: AtelierComposerValues): Record<string, string | number | boolean> {
    const params: Record<string, string | number | boolean> = {
        duration: values.duration,
        resolution: values.resolution,
        prompt_extend: values.promptExtend,
        watermark: values.watermark,
    };
    if (values.seed.trim()) params.seed = Number(values.seed);
    if (!isOrderedComposerModel(values.modelId) && values.negativePrompt.trim()) params.negative_prompt = values.negativePrompt;
    if (isOrderedComposerModel(values.modelId)) {
        params.ratio = values.ratio;
        params.audio = values.audio;
    }
    return params;
}

function selectedSource(sources: AtelierComposerSource[], key: string): AtelierComposerSource | null {
    return sources.find(source => sourceKey(source) === key) ?? null;
}

function sourceForMedia(sources: AtelierComposerSource[], media: AtelierOrderedMediaSelection): AtelierComposerSource | null {
    return sources.find(source => source.nodeId === media.node_id && source.mediaUrl === media.media_url) ?? null;
}

function roleForSource(source: AtelierComposerSource): AtelierOrderedMediaRole {
    if (source.kind === "video") return "reference_video";
    if (source.kind === "audio") return "reference_audio";
    return "reference_image";
}

function reordinalize(media: AtelierOrderedMediaSelection[]): AtelierOrderedMediaSelection[] {
    const counts: Record<AtelierOrderedMediaRole, number> = {
        first_frame: 0,
        last_frame: 0,
        reference_image: 0,
        reference_video: 0,
        reference_audio: 0,
    };
    return media.map(item => ({ ...item, ordinal: counts[item.role]++ }));
}

export function canonicalReferenceMediaFromSources(sources: AtelierComposerSource[]): AtelierOrderedMediaSelection[] {
    const decorated = sources.map((source, index) => ({ source, index, role: roleForSource(source) }));
    decorated.sort((left, right) => ROLE_ORDER[left.role] - ROLE_ORDER[right.role] || left.index - right.index);
    return reordinalize(decorated.map(({ source, role }) => ({ role, ordinal: 0, node_id: source.nodeId, media_url: source.mediaUrl })));
}

export function resolveOrderedMediaSelections(media: AtelierOrderedMediaSelection[], sources: AtelierComposerSource[]): ResolvedOrderedMediaSelection[] {
    return media.map(item => {
        const source = sourceForMedia(sources, item);
        const expectedKind = item.role === "reference_audio" ? "audio" : item.role === "reference_video" ? "video" : "image";
        return { ...item, available: source?.kind === expectedKind, title: source?.title, kind: source?.kind };
    });
}

export function orderedMediaHasMissing(media: AtelierOrderedMediaSelection[], sources: AtelierComposerSource[]): boolean {
    return resolveOrderedMediaSelections(media, sources).some(item => !item.available);
}

export function orderedMediaFromValues(values: AtelierComposerValues, sources: AtelierComposerSource[]): AtelierOrderedMediaSelection[] {
    if (!isOrderedComposerModel(values.modelId) || values.mode === "t2v") return [];
    if (values.mode === "firstlast") {
        const media: AtelierOrderedMediaSelection[] = [];
        const first = selectedSource(sources, values.firstSourceKey);
        const last = selectedSource(sources, values.lastSourceKey);
        const savedFirst = values.referenceMedia.find(item => item.role === "first_frame");
        const savedLast = values.referenceMedia.find(item => item.role === "last_frame");
        if (first?.kind === "image") media.push({ role: "first_frame", ordinal: 0, node_id: first.nodeId, media_url: first.mediaUrl });
        else if (values.firstSourceKey && savedFirst) media.push({ ...savedFirst, ordinal: 0 });
        if (last?.kind === "image") media.push({ role: "last_frame", ordinal: 0, node_id: last.nodeId, media_url: last.mediaUrl });
        else if (values.lastSourceKey && savedLast) media.push({ ...savedLast, ordinal: 0 });
        return media;
    }
    return values.referenceMediaInitialized ? reordinalize(values.referenceMedia) : canonicalReferenceMediaFromSources(sources);
}

export function hasMissingFirstLastSelection(values: AtelierComposerValues, sources: AtelierComposerSource[]): boolean {
    if (!isOrderedComposerModel(values.modelId) || values.mode !== "firstlast") return false;
    const first = selectedSource(sources, values.firstSourceKey);
    const last = values.lastSourceKey ? selectedSource(sources, values.lastSourceKey) : null;
    return !first || (!!values.lastSourceKey && !last);
}

type ComposerDraftInput = Omit<SetAtelierLegacyGenerationDraftInput, "expected_revision"> | Omit<SetAtelierOrderedGenerationDraftInput, "expected_revision">;
type ComposerGenerationRequest = Omit<AtelierVideoCandidateRequest, "expected_revision" | "client_request_id">
    | Omit<Extract<AtelierGenerationRequest, { contract_version: "ordered-media-v1" }>, "expected_revision" | "client_request_id">;

export function buildAtelierGenerationDraft(values: AtelierComposerValues, sources: AtelierComposerSource[]): ComposerDraftInput {
    if (isOrderedComposerModel(values.modelId)) {
        return {
            contract_version: "ordered-media-v1",
            prompt: values.prompt,
            model_id: values.modelId,
            mode: values.mode === "firstlast" ? "i2v" : values.mode as AtelierOrderedVideoMode,
            params: paramsFromValues(values) as AtelierOrderedGenerationParams,
            media: orderedMediaFromValues(values, sources),
            batch_size: values.batchSize,
        };
    }
    return {
        prompt: values.prompt,
        model_id: LEGACY_ATELIER_VIDEO_MODEL,
        mode: "i2v",
        params: paramsFromValues(values),
    };
}

export function buildAtelierGenerationRequest(values: AtelierComposerValues, sources: AtelierComposerSource[]): ComposerGenerationRequest | null {
    const params = paramsFromValues(values);
    if (isOrderedComposerModel(values.modelId)) {
        const media = orderedMediaFromValues(values, sources);
        if (values.mode === "firstlast" && (!media.some(item => item.role === "first_frame") || hasMissingFirstLastSelection(values, sources))) return null;
        if (values.mode === "r2v" && (media.length === 0 || orderedMediaHasMissing(media, sources))) return null;
        return {
            contract_version: "ordered-media-v1",
            model_id: values.modelId,
            mode: values.mode === "firstlast" ? "i2v" : values.mode as AtelierOrderedVideoMode,
            prompt: values.prompt,
            media,
            params: params as AtelierOrderedGenerationParams,
            batch_size: values.batchSize,
        };
    }
    const first = selectedSource(sources, values.firstSourceKey);
    if (!first || first.kind !== "image") return null;
    return {
        model_id: LEGACY_ATELIER_VIDEO_MODEL,
        mode: "i2v",
        prompt: values.prompt,
        first_frame: { node_id: first.nodeId, media_url: first.mediaUrl },
        params: params as AtelierVideoCandidateRequest["params"],
    };
}
