import type { VideoTask } from "@/lib/api";
import { validateWan3GenerationParams } from "@/lib/wan3GenerationParams";

export const LEGACY_PROVIDER_VIDEO_MODELS = Object.freeze(["wan2.6-i2v", "wan2.6-i2v-flash", "wan2.7-i2v"] as const);
export const SUPPORTED_PROVIDER_VIDEO_MODELS = LEGACY_PROVIDER_VIDEO_MODELS;
export const ORDERED_PROVIDER_VIDEO_MODELS = Object.freeze(["wan3.0-video", "wan3.0-video-prime"] as const);
export const ORDERED_MEDIA_CONTRACT_VERSION = "ordered-media-v1" as const;

export type LegacyProviderVideoModel = typeof LEGACY_PROVIDER_VIDEO_MODELS[number];
export type OrderedProviderVideoModel = typeof ORDERED_PROVIDER_VIDEO_MODELS[number];
export type SupportedProviderVideoModel = typeof SUPPORTED_PROVIDER_VIDEO_MODELS[number];
export type ProviderVideoTaskModel = LegacyProviderVideoModel | OrderedProviderVideoModel;
export type ProviderWorkbenchTab = "t2i_i2v" | "direct_r2v";
export type ProviderVideoGenerationMode = "t2v" | "i2v" | "r2v";

const WAN27_ALLOWED_INPUT_KEYS = new Set([
    "batchSize",
    "clientRequestId",
    "duration",
    "frameId",
    "imageUrl",
    "model",
    "negativePrompt",
    "prompt",
    "promptExtend",
    "resolution",
    "seed",
    "shotType",
    "watermark",
    "workbenchTab",
]);

const ORDERED_ALLOWED_INPUT_KEYS = new Set([
    "batchSize",
    "clientRequestId",
    "contractVersion",
    "duration",
    "frameId",
    "generateAudio",
    "generationMode",
    "imageUrl",
    "lastFrameUrl",
    "model",
    "prompt",
    "promptExtend",
    "ratio",
    "referenceAudioUrls",
    "referenceImageUrls",
    "referenceVideoUrls",
    "resolution",
    "seed",
    "watermark",
    "workbenchTab",
]);

const PROVIDER_TOMBSTONE_REASONS = new Set([
    "provider_request_deleted",
    "provider_request_owner_deleted",
]);

const HEX64 = /^[0-9a-f]{64}$/i;
const PROVIDER_EXECUTION_KEYS = new Set([
    "artifact", "attempt_id", "credential_ref", "fingerprint", "handle", "intent", "media",
    "observation", "phase", "prepared_artifact", "provider_model_id", "schema_version", "scope",
]);
const PROVIDER_EXECUTION_PHASES = new Set(["accepted", "cancelled", "submitting", "submitted", "observed", "completed"]);
const PROVIDER_SCOPE_KEYS = new Set([
    "account_identity", "backend", "base_url", "gateway", "provider", "region", "workspace_id",
]);
const MEDIA_PROOF_KEYS = new Set(["declared_media_type", "ordinal", "role", "schema_version", "sha256", "size_bytes"]);
const IMAGE_MIME_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/bmp"]);
const VIDEO_MIME_TYPES = new Set(["video/mp4", "video/quicktime"]);
const AUDIO_MIME_TYPES = new Set(["audio/wav", "audio/mpeg"]);
const ORDERED_PAYLOAD_SCALAR_FIELDS = Object.freeze([
    "duration",
    "resolution",
    "ratio",
    "prompt_extend",
    "watermark",
    "generate_audio",
    "seed",
] as const);


export function isProviderVideoTaskTombstoneReason(reason: string | null): boolean {
    return reason !== null && PROVIDER_TOMBSTONE_REASONS.has(reason);
}

export interface ProviderVideoTaskInput {
    imageUrl?: string;
    lastFrameUrl?: string;
    referenceImageUrls?: string[];
    referenceVideoUrls?: string[];
    referenceAudioUrls?: string[];
    prompt: string;
    model: string;
    frameId: string;
    generationMode?: ProviderVideoGenerationMode;
    contractVersion?: typeof ORDERED_MEDIA_CONTRACT_VERSION;
    duration?: number;
    resolution?: string;
    ratio?: string;
    promptExtend?: boolean;
    negativePrompt?: string;
    seed?: number;
    shotType?: string;
    watermark?: boolean;
    generateAudio?: boolean;
    workbenchTab: ProviderWorkbenchTab;
}

export interface ProviderVideoTaskPayloadInput extends ProviderVideoTaskInput {
    clientRequestId: string;
    batchSize: number;
}

export type ProviderVideoTaskPayload = Record<string, unknown> & {
    client_request_id: string;
    prompt: string;
    model: ProviderVideoTaskModel;
    frame_id: string;
    generation_mode: ProviderVideoGenerationMode;
    batch_size: number;
    workbench_tab: ProviderWorkbenchTab;
};

export interface ProviderVideoTaskReceipt {
    clientRequestId: string;
    replayed: boolean;
    taskIds: string[];
    tasks: VideoTask[];
    reason: string | null;
}

export interface ProviderVideoTaskReceiptExpectation {
    clientRequestId: string;
    projectId: string;
    frameId: string;
    generationMode: ProviderVideoGenerationMode;
    workbenchTab: ProviderWorkbenchTab;
    batchSize: number;
    contractVersion?: typeof ORDERED_MEDIA_CONTRACT_VERSION;
    model?: string;
    imageUrl?: string;
    lastFrameUrl?: string;
    referenceImageUrls?: string[];
    referenceVideoUrls?: string[];
    referenceAudioUrls?: string[];
}

export interface ProviderVideoTaskExpectationContext {
    clientRequestId: string;
    projectId: string;
    frameId: string;
}

export type ProviderVideoTaskEntryErrorCode =
    | "provider_model_unsupported"
    | "provider_workbench_unsupported"
    | "provider_batch_invalid"
    | "provider_payload_invalid"
    | "provider_watermark_unsupported"
    | "provider_shot_type_incompatible"
    | "provider_receipt_invalid";

export class ProviderVideoTaskEntryError extends Error {
    code: ProviderVideoTaskEntryErrorCode;

    constructor(code: ProviderVideoTaskEntryErrorCode) {
        super(code);
        this.name = "ProviderVideoTaskEntryError";
        this.code = code;
    }
}

export function isProviderVideoModel(model: string | undefined | null): model is SupportedProviderVideoModel {
    return LEGACY_PROVIDER_VIDEO_MODELS.includes(model as LegacyProviderVideoModel);
}

export function buildProviderVideoTaskInput(input: ProviderVideoTaskInput): Record<string, unknown> {
    if (Object.prototype.hasOwnProperty.call(input as unknown as Record<string, unknown>, "contractVersion")) {
        return buildOrderedProviderVideoTaskInput(input);
    }
    return buildLegacyProviderVideoTaskInput(input);
}

export function providerVideoTaskExpectationFromPayload(
    payloadRaw: unknown,
    context: ProviderVideoTaskExpectationContext,
): ProviderVideoTaskReceiptExpectation {
    if (!isPlainRecord(payloadRaw) || payloadRaw.client_request_id !== context.clientRequestId) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const generationMode = payloadString(payloadRaw, "generation_mode");
    const workbenchTab = payloadString(payloadRaw, "workbench_tab");
    if ((generationMode !== "t2v" && generationMode !== "i2v" && generationMode !== "r2v") || (workbenchTab !== "t2i_i2v" && workbenchTab !== "direct_r2v")) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const base: ProviderVideoTaskReceiptExpectation = {
        clientRequestId: context.clientRequestId,
        projectId: context.projectId,
        frameId: context.frameId,
        generationMode,
        workbenchTab,
        batchSize: payloadBatchSize(payloadRaw),
    };
    if (payloadRaw.contract_version !== ORDERED_MEDIA_CONTRACT_VERSION) {
        return base;
    }
    const model = payloadString(payloadRaw, "model");
    if (!isOrderedProviderVideoModel(model)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    return {
        ...base,
        contractVersion: ORDERED_MEDIA_CONTRACT_VERSION,
        model,
        imageUrl: payloadString(payloadRaw, "image_url"),
        lastFrameUrl: payloadString(payloadRaw, "last_frame_url"),
        referenceImageUrls: payloadStringArray(payloadRaw, "reference_image_urls"),
        referenceVideoUrls: payloadStringArray(payloadRaw, "reference_video_urls"),
        referenceAudioUrls: payloadStringArray(payloadRaw, "reference_audio_urls"),
    };
}

export function providerVideoTaskMatchesExpectation(
    task: VideoTask,
    expected: ProviderVideoTaskReceiptExpectation,
    payloadRaw?: unknown,
): boolean {
    if (
        task.project_id !== expected.projectId ||
        task.frame_id !== expected.frameId ||
        task.generation_mode !== expected.generationMode ||
        task.workbench_tab !== expected.workbenchTab
    ) {
        return false;
    }
    if (expected.contractVersion === ORDERED_MEDIA_CONTRACT_VERSION) {
        if (!orderedTaskShapeMatches(task, expected)) return false;
        if (payloadRaw !== undefined && !orderedTaskPayloadMatches(task, payloadRaw)) return false;
    }
    return true;
}

export function assertProviderVideoTasksMatchPayload(
    tasks: VideoTask[],
    expected: ProviderVideoTaskReceiptExpectation,
    payloadRaw: unknown,
): void {
    if (expected.contractVersion !== ORDERED_MEDIA_CONTRACT_VERSION) return;
    if (!tasks.every(task => providerVideoTaskMatchesExpectation(task, expected, payloadRaw))) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
}

function buildLegacyProviderVideoTaskInput(input: ProviderVideoTaskInput): Record<string, unknown> {
    const model = validateLegacyModel(input.model);
    if (input.workbenchTab !== "t2i_i2v") {
        throw new ProviderVideoTaskEntryError("provider_workbench_unsupported");
    }
    const imageUrl = requireString(input.imageUrl);
    const prompt = requireString(input.prompt);
    const frameId = requireString(input.frameId);
    if (!imageUrl || !prompt || !frameId) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    if (input.generationMode !== undefined && input.generationMode !== "i2v") {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    if (model === "wan2.7-i2v") {
        validateWan27Input(input);
    } else {
        if (typeof input.watermark === "boolean") {
            throw new ProviderVideoTaskEntryError("provider_watermark_unsupported");
        }
        if (input.promptExtend === false && input.shotType !== undefined) {
            throw new ProviderVideoTaskEntryError("provider_shot_type_incompatible");
        }
    }

    const payload: Record<string, unknown> = {
        image_url: imageUrl,
        prompt,
        model,
        frame_id: frameId,
        generation_mode: "i2v",
        workbench_tab: input.workbenchTab,
    };
    setIfDefined(payload, "duration", validateOptionalFiniteNumber(input.duration));
    setIfDefined(payload, "resolution", input.resolution);
    setIfDefined(payload, "prompt_extend", input.promptExtend);
    setIfDefined(payload, "negative_prompt", input.negativePrompt);
    setIfDefined(payload, "seed", validateOptionalFiniteNumber(input.seed));
    if (model !== "wan2.7-i2v") {
        setIfDefined(payload, "shot_type", input.shotType);
    }
    if (model === "wan2.7-i2v") {
        setIfDefined(payload, "watermark", input.watermark);
    }
    if (model === "wan2.6-i2v-flash") {
        payload.generate_audio = false;
    }
    return payload;
}

function buildOrderedProviderVideoTaskInput(input: ProviderVideoTaskInput): Record<string, unknown> {
    validateOrderedInputKeys(input);
    if (input.contractVersion !== ORDERED_MEDIA_CONTRACT_VERSION) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    const model = validateOrderedModel(input.model);
    const prompt = requireString(input.prompt);
    const frameId = requireString(input.frameId);
    const mode = input.generationMode;
    if (!frameId || !mode || !["t2v", "i2v", "r2v"].includes(mode)) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    if (input.workbenchTab !== "t2i_i2v" && input.workbenchTab !== "direct_r2v") {
        throw new ProviderVideoTaskEntryError("provider_workbench_unsupported");
    }
    if (unicodeCodePointLength(prompt) > 20000 || (mode === "t2v" && !prompt.trim())) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }

    const imageRefs = validateOptionalStringArray(input.referenceImageUrls);
    const videoRefs = validateOptionalStringArray(input.referenceVideoUrls);
    const audioRefs = validateOptionalStringArray(input.referenceAudioUrls);
    const imageUrl = input.imageUrl === undefined ? undefined : requireNonEmptyString(input.imageUrl);
    const lastFrameUrl = input.lastFrameUrl === undefined ? undefined : requireNonEmptyString(input.lastFrameUrl);

    const payload: Record<string, unknown> = {
        contract_version: ORDERED_MEDIA_CONTRACT_VERSION,
        prompt,
        model,
        frame_id: frameId,
        generation_mode: mode,
        workbench_tab: input.workbenchTab,
    };

    if (mode === "t2v") {
        if (imageUrl !== undefined || lastFrameUrl !== undefined || imageRefs.length || videoRefs.length || audioRefs.length) {
            throw new ProviderVideoTaskEntryError("provider_payload_invalid");
        }
    } else if (mode === "i2v") {
        if (imageUrl === undefined || imageRefs.length || videoRefs.length || audioRefs.length) {
            throw new ProviderVideoTaskEntryError("provider_payload_invalid");
        }
        payload.image_url = imageUrl;
        setIfDefined(payload, "last_frame_url", lastFrameUrl);
    } else {
        if (imageUrl !== undefined || lastFrameUrl !== undefined) {
            throw new ProviderVideoTaskEntryError("provider_payload_invalid");
        }
        if (!imageRefs.length && !videoRefs.length && !audioRefs.length) {
            throw new ProviderVideoTaskEntryError("provider_payload_invalid");
        }
        if (imageRefs.length > 10 || videoRefs.length > 5 || audioRefs.length > 5 || imageRefs.length + videoRefs.length + audioRefs.length > 20) {
            throw new ProviderVideoTaskEntryError("provider_payload_invalid");
        }
        if (imageRefs.length) payload.reference_image_urls = imageRefs;
        if (videoRefs.length) payload.reference_video_urls = videoRefs;
        if (audioRefs.length) payload.reference_audio_urls = audioRefs;
    }

    const params = orderedParams(input);
    if (!validateWan3GenerationParams(`wan/${model}#video`, params)) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    for (const [key, value] of Object.entries(params)) {
        payload[key === "audio" ? "generate_audio" : key] = value;
    }
    return payload;
}

export function buildProviderVideoTaskPayload(input: ProviderVideoTaskPayloadInput): ProviderVideoTaskPayload {
    const batchSize = validateBatchSize(input.batchSize);
    const clientRequestId = requireString(input.clientRequestId);
    if (!clientRequestId) throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    return {
        client_request_id: clientRequestId,
        ...buildProviderVideoTaskInput(input),
        batch_size: batchSize,
    } as ProviderVideoTaskPayload;
}

export function parseProviderVideoTaskReceipt(raw: unknown, expected: ProviderVideoTaskReceiptExpectation): ProviderVideoTaskReceipt {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const value = raw as Record<string, unknown>;
    const clientRequestId = value.client_request_id;
    const replayed = value.replayed;
    const taskIds = value.task_ids;
    const tasks = value.tasks;
    const reason = value.reason;
    if (clientRequestId !== expected.clientRequestId || typeof replayed !== "boolean") {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (!Array.isArray(taskIds) || !taskIds.every(id => typeof id === "string" && id.length > 0)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (!Array.isArray(tasks)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (reason !== null && typeof reason !== "string") {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (typeof reason === "string" && (!replayed || !isProviderVideoTaskTombstoneReason(reason))) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const taskIdSet = new Set(taskIds);
    if (taskIdSet.size !== taskIds.length) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (!Number.isInteger(expected.batchSize) || expected.batchSize < 1 || expected.batchSize > 6 || taskIds.length !== expected.batchSize) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const parsedTasks = tasks.map(task => validateReceiptTask(task, expected, taskIdSet));
    const parsedTaskIds = new Set(parsedTasks.map(task => task.id));
    if (parsedTaskIds.size !== parsedTasks.length) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (reason === null && parsedTasks.length !== taskIds.length) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (reason !== null && parsedTasks.length > taskIds.length) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (reason === null && parsedTasks.length !== expected.batchSize) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    return {
        clientRequestId,
        replayed,
        taskIds: [...taskIds],
        tasks: parsedTasks,
        reason,
    };
}

function validateReceiptTask(raw: unknown, expected: ProviderVideoTaskReceiptExpectation, taskIdSet: Set<string>): VideoTask {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const task = raw as Partial<VideoTask>;
    if (
        typeof task.id !== "string" ||
        !taskIdSet.has(task.id) ||
        task.project_id !== expected.projectId ||
        task.frame_id !== expected.frameId ||
        task.generation_mode !== expected.generationMode ||
        task.workbench_tab !== expected.workbenchTab
    ) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (expected.contractVersion === ORDERED_MEDIA_CONTRACT_VERSION) {
        validateOrderedReceiptTask(task, expected);
    }
    return task as VideoTask;
}

function validateOrderedReceiptTask(task: Partial<VideoTask>, expected: ProviderVideoTaskReceiptExpectation): void {
    if (!expected.model || !isOrderedProviderVideoModel(expected.model) || task.model !== expected.model) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (!orderedTaskShapeMatches(task, expected)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    validateOrderedProviderExecution(task.provider_execution, expected);
}

function orderedTaskShapeMatches(task: Partial<VideoTask>, expected: ProviderVideoTaskReceiptExpectation): boolean {
    if (!expected.model || !isOrderedProviderVideoModel(expected.model) || task.model !== expected.model) {
        return false;
    }
    if (expected.generationMode === "t2v") {
        if (task.image_url !== "") return false;
        if (!isAbsent(task.last_frame_url)) return false;
        if (!arrayEquals(task.reference_image_urls, [])) return false;
        if (!arrayEquals(task.reference_video_urls, [])) return false;
        if (!arrayEquals(task.reference_audio_urls, [])) return false;
    } else if (expected.generationMode === "i2v") {
        if (task.image_url !== expected.imageUrl) return false;
        if (!optionalValueEquals(task.last_frame_url, expected.lastFrameUrl)) return false;
        if (!arrayEquals(task.reference_image_urls, [])) return false;
        if (!arrayEquals(task.reference_video_urls, [])) return false;
        if (!arrayEquals(task.reference_audio_urls, [])) return false;
    } else {
        if (task.image_url !== "") return false;
        if (!isAbsent(task.last_frame_url)) return false;
        if (!arrayEquals(task.reference_image_urls, expected.referenceImageUrls ?? [])) return false;
        if (!arrayEquals(task.reference_video_urls, expected.referenceVideoUrls ?? [])) return false;
        if (!arrayEquals(task.reference_audio_urls, expected.referenceAudioUrls ?? [])) return false;
    }
    return true;
}

function orderedTaskPayloadMatches(task: Partial<VideoTask>, payloadRaw: unknown): boolean {
    if (!isPlainRecord(payloadRaw)) return false;
    const execution = task.provider_execution;
    if (!isPlainRecord(execution) || !isPlainRecord(execution.intent)) return false;
    const intent = execution.intent;
    if (intent.prompt !== payloadRaw.prompt) return false;
    if (task.prompt !== payloadRaw.prompt) return false;
    const params = intent.params;
    if (!isPlainRecord(params)) return false;
    for (const key of ORDERED_PAYLOAD_SCALAR_FIELDS) {
        if (!Object.prototype.hasOwnProperty.call(payloadRaw, key)) continue;
        const expectedValue = payloadRaw[key];
        if ((task as Partial<Record<string, unknown>>)[key] !== expectedValue) return false;
        const paramKey = key === "generate_audio" ? "audio" : key;
        if (params[paramKey] !== expectedValue) return false;
    }
    return true;
}

function validateOrderedProviderExecution(raw: unknown, expected: ProviderVideoTaskReceiptExpectation): void {
    if (!isPlainRecord(raw) || hasUnknownKeys(raw, PROVIDER_EXECUTION_KEYS)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const record = raw;
    if (
        record.schema_version !== 1 ||
        !nonEmptyString(record.attempt_id) ||
        record.provider_model_id !== expected.model ||
        !PROVIDER_EXECUTION_PHASES.has(record.phase as string) ||
        !nonEmptyString(record.credential_ref) ||
        !hex64(record.fingerprint)
    ) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    validateOrderedProviderScope(record.scope);
    if (!nullableRecord(record.handle) || !nullableRecord(record.observation) || !nullableRecord(record.prepared_artifact) || !nullableRecord(record.artifact)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const intent = record.intent;
    if (!isPlainRecord(intent)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    if (intent.mode !== expected.generationMode || intent.model_id !== `wan/${expected.model}#video` || intent.catalog_mode !== "video") {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const media = record.media;
    if (!Array.isArray(media)) throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    const expectedMedia = expectedOrderedMedia(expected);
    if (media.length !== expectedMedia.length) throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    media.forEach((item, index) => {
        if (!isPlainRecord(item) || hasUnknownKeys(item, MEDIA_PROOF_KEYS)) {
            throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
        }
        const expectation = expectedMedia[index];
        if (
            item.schema_version !== 1 ||
            item.role !== expectation.role ||
            item.ordinal !== expectation.ordinal ||
            !hex64(item.sha256) ||
            !positiveSafeInteger(item.size_bytes) ||
            !declaredMediaTypeMatchesRole(item.declared_media_type, expectation.role)
        ) {
            throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
        }
    });
}

function validateOrderedProviderScope(raw: unknown): void {
    if (!isPlainRecord(raw) || hasUnknownKeys(raw, PROVIDER_SCOPE_KEYS)) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const workspaceId = raw.workspace_id;
    const region = raw.region;
    const baseUrl = raw.base_url;
    if (
        raw.provider !== "dashscope" ||
        raw.backend !== "dashscope" ||
        raw.gateway !== "dashscope_workspace" ||
        !nonEmptyString(workspaceId) ||
        workspaceId === "trial" ||
        !nonEmptyString(region) ||
        !nonEmptyString(baseUrl) ||
        (raw.account_identity !== null && raw.account_identity !== undefined && typeof raw.account_identity !== "string")
    ) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    const expectedUrl = `https://${workspaceId}.${region}.maas.aliyuncs.com`;
    if (baseUrl !== expectedUrl && baseUrl !== `${expectedUrl}/`) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
}

function declaredMediaTypeMatchesRole(value: unknown, role: string): boolean {
    if (typeof value !== "string") return false;
    if (["first_frame", "last_frame", "reference_image"].includes(role)) return IMAGE_MIME_TYPES.has(value);
    if (role === "reference_video") return VIDEO_MIME_TYPES.has(value);
    if (role === "reference_audio") return AUDIO_MIME_TYPES.has(value);
    return false;
}

function expectedOrderedMedia(expected: ProviderVideoTaskReceiptExpectation): Array<{ role: string; ordinal: number }> {
    if (expected.generationMode === "t2v") return [];
    if (expected.generationMode === "i2v") {
        const media = [{ role: "first_frame", ordinal: 0 }];
        if (expected.lastFrameUrl !== undefined) media.push({ role: "last_frame", ordinal: 0 });
        return media;
    }
    return [
        ...(expected.referenceImageUrls ?? []).map((_value, ordinal) => ({ role: "reference_image", ordinal })),
        ...(expected.referenceVideoUrls ?? []).map((_value, ordinal) => ({ role: "reference_video", ordinal })),
        ...(expected.referenceAudioUrls ?? []).map((_value, ordinal) => ({ role: "reference_audio", ordinal })),
    ];
}

function validateWan27Input(input: ProviderVideoTaskInput): void {
    if (Object.keys(input as unknown as Record<string, unknown>).some(key => !WAN27_ALLOWED_INPUT_KEYS.has(key))) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    if (unicodeCodePointLength(input.prompt) > 5000 || unicodeCodePointLength(input.negativePrompt ?? "") > 500) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    if (input.duration !== undefined && (!Number.isInteger(input.duration) || input.duration < 2 || input.duration > 15)) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    if (input.resolution !== undefined && input.resolution !== "720p" && input.resolution !== "1080p") {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    if (input.seed !== undefined && (!Number.isInteger(input.seed) || input.seed < 0 || input.seed > 2147483647)) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    if (input.shotType !== undefined) {
        throw new ProviderVideoTaskEntryError("provider_shot_type_incompatible");
    }
    if (input.watermark !== undefined && typeof input.watermark !== "boolean") {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    if (input.promptExtend !== undefined && typeof input.promptExtend !== "boolean") {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
}

function unicodeCodePointLength(value: string): number {
    return Array.from(value).length;
}

function validateLegacyModel(model: string): LegacyProviderVideoModel {
    if (!LEGACY_PROVIDER_VIDEO_MODELS.includes(model as LegacyProviderVideoModel)) {
        throw new ProviderVideoTaskEntryError("provider_model_unsupported");
    }
    return model as LegacyProviderVideoModel;
}

function validateOrderedModel(model: string): OrderedProviderVideoModel {
    if (!isOrderedProviderVideoModel(model)) {
        throw new ProviderVideoTaskEntryError("provider_model_unsupported");
    }
    return model;
}

function isOrderedProviderVideoModel(model: string | undefined): model is OrderedProviderVideoModel {
    return ORDERED_PROVIDER_VIDEO_MODELS.includes(model as OrderedProviderVideoModel);
}

function validateBatchSize(value: number): number {
    if (!Number.isInteger(value) || value < 1 || value > 6) {
        throw new ProviderVideoTaskEntryError("provider_batch_invalid");
    }
    return value;
}

function validateOptionalFiniteNumber(value: number | undefined): number | undefined {
    if (value === undefined) return undefined;
    if (!Number.isFinite(value)) throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    return value;
}

function validateOrderedInputKeys(input: ProviderVideoTaskInput): void {
    if (Object.keys(input as unknown as Record<string, unknown>).some(key => !ORDERED_ALLOWED_INPUT_KEYS.has(key))) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
}

function orderedParams(input: ProviderVideoTaskInput): Record<string, unknown> {
    const params: Record<string, unknown> = {};
    if (input.duration !== undefined) params.duration = input.duration;
    if (input.resolution !== undefined) params.resolution = input.resolution;
    if (input.ratio !== undefined) params.ratio = input.ratio;
    if (input.seed !== undefined) params.seed = input.seed;
    if (input.promptExtend !== undefined) params.prompt_extend = input.promptExtend;
    if (input.watermark !== undefined) params.watermark = input.watermark;
    if (input.generateAudio !== undefined) params.audio = input.generateAudio;
    return params;
}

function validateOptionalStringArray(value: string[] | undefined): string[] {
    if (value === undefined) return [];
    if (!Array.isArray(value) || value.some(item => typeof item !== "string" || !item.trim())) {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    return [...value];
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
    return !!value && typeof value === "object" && !Array.isArray(value);
}

function hasUnknownKeys(value: Record<string, unknown>, allowed: Set<string>): boolean {
    return Object.keys(value).some(key => !allowed.has(key));
}

function nullableRecord(value: unknown): boolean {
    return value === null || isPlainRecord(value);
}

function nonEmptyString(value: unknown): value is string {
    return typeof value === "string" && value.length > 0;
}

function hex64(value: unknown): value is string {
    return typeof value === "string" && HEX64.test(value);
}

function positiveSafeInteger(value: unknown): value is number {
    return Number.isSafeInteger(value) && (value as number) > 0;
}

function requireString(value: unknown): string {
    if (typeof value !== "string") {
        throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    }
    return value;
}

function requireNonEmptyString(value: unknown): string {
    const text = requireString(value);
    if (!text.trim()) throw new ProviderVideoTaskEntryError("provider_payload_invalid");
    return text;
}

function setIfDefined(target: Record<string, unknown>, key: string, value: unknown): void {
    if (value !== undefined) target[key] = value;
}

function payloadString(payload: Record<string, unknown>, key: string): string | undefined {
    const value = payload[key];
    return typeof value === "string" ? value : undefined;
}

function payloadStringArray(payload: Record<string, unknown>, key: string): string[] | undefined {
    const value = payload[key];
    if (value === undefined) return undefined;
    return Array.isArray(value) && value.every(item => typeof item === "string") ? [...value] : undefined;
}

function payloadBatchSize(payload: Record<string, unknown>): number {
    const value = payload.batch_size;
    if (!Number.isInteger(value) || (value as number) < 1 || (value as number) > 6) {
        throw new ProviderVideoTaskEntryError("provider_receipt_invalid");
    }
    return value as number;
}

function isAbsent(value: unknown): boolean {
    return value === undefined || value === null;
}

function optionalValueEquals(value: unknown, expected: string | undefined): boolean {
    return expected === undefined ? isAbsent(value) : value === expected;
}

function arrayEquals(value: unknown, expected: string[]): boolean {
    const actual = value === undefined ? [] : value;
    return Array.isArray(actual) && actual.length === expected.length && actual.every((item, index) => item === expected[index]);
}
