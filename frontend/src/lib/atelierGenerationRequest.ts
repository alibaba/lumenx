import { hasCanonicalAtelierOrderedMediaRoleOrder, isAtelierOrderedMediaRole } from "@/lib/atelierOrderedMedia";
import { validateWan3GenerationParams } from "@/lib/wan3GenerationParams";
import type {
    AtelierAudioCandidateRequest,
    AtelierGenerationRequest,
    AtelierGenerationFirstFrameSource,
    AtelierImageCandidateRequest,
    AtelierImageReferenceSelection,
    AtelierLegacyGenerationParams,
    AtelierOrderedGenerationParams,
    AtelierOrderedMediaRole,
    AtelierOrderedMediaSelection,
    AtelierOrderedVideoCandidateRequest,
    AtelierOrderedVideoMode,
    AtelierOrderedVideoModel,
    AtelierVideoCandidateRequest,
} from "@/lib/atelierTypes";
import type { RuntimeEnvironment } from "@/lib/runtimeEnvironment";

export interface AtelierGenerationRequestOwner {
    environment: RuntimeEnvironment;
    projectId: string;
    nodeId: string;
}

export interface AtelierGenerationProjectRequestOwner {
    environment: RuntimeEnvironment;
    projectId: string;
}

export type AtelierGenerationFirstFrame = AtelierGenerationFirstFrameSource;
export type AtelierGenerationParams = AtelierLegacyGenerationParams;
export type AtelierGenerationOriginalRequest = AtelierGenerationRequest;

interface BeginAtelierLegacyGenerationRequestOptions extends AtelierGenerationRequestOwner {
    expectedRevision: number;
    modelId: string;
    mode: "i2v";
    prompt: string;
    firstFrame: unknown;
    params?: unknown;
    batchSize?: unknown;
    storage?: Storage;
    idFactory?: () => string;
}

interface BeginAtelierImageGenerationRequestOptions extends AtelierGenerationRequestOwner {
    entrypoint: "atelier.provider_image.v1";
    expectedRevision: number;
    modelId: string;
    mode: "t2i" | "i2i";
    prompt: string;
    media?: unknown;
    params?: unknown;
    batchSize?: unknown;
    storage?: Storage;
    idFactory?: () => string;
}

interface BeginAtelierAudioGenerationRequestOptions extends AtelierGenerationRequestOwner {
    entrypoint: "atelier.provider_audio.v1";
    expectedRevision: number;
    voice: string;
    text: string;
    params?: unknown;
    storage?: Storage;
    idFactory?: () => string;
}

interface BeginAtelierOrderedGenerationRequestOptions extends AtelierGenerationRequestOwner {
    expectedRevision: number;
    contractVersion: "ordered-media-v1";
    modelId: AtelierOrderedVideoModel;
    mode: AtelierOrderedVideoMode;
    prompt: string;
    media: unknown;
    params?: unknown;
    batchSize?: unknown;
    storage?: Storage;
    idFactory?: () => string;
}

export type BeginAtelierGenerationRequestOptions = BeginAtelierLegacyGenerationRequestOptions | BeginAtelierOrderedGenerationRequestOptions | BeginAtelierImageGenerationRequestOptions | BeginAtelierAudioGenerationRequestOptions;

export interface StoredAtelierGenerationRequest {
    schema_version: 1;
    apiBase: string;
    storageId: string;
    projectId: string;
    nodeId: string;
    clientRequestId: string;
    request: AtelierGenerationOriginalRequest;
}

export type AtelierGenerationRequestResult = {
    status: "created" | "pending";
    request: StoredAtelierGenerationRequest;
};

export type AtelierGenerationRequestErrorCode =
    | "atelier_generation_request_storage_unavailable"
    | "atelier_generation_request_store_corrupt"
    | "atelier_generation_request_owner_invalid"
    | "atelier_generation_request_payload_invalid"
    | "atelier_generation_request_id_invalid";

const REQUEST_PREFIX = "lumenx.atelierGeneration.request.v1:";
const CLIENT_REQUEST_ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const LEGACY_PARAM_FIELDS = new Set(["duration", "resolution", "seed", "negative_prompt", "prompt_extend", "watermark"]);
const IMAGE_PARAM_FIELDS = new Set(["size", "seed", "negative_prompt", "prompt_extend", "watermark"]);
const AUDIO_PARAM_FIELDS = new Set(["speech_rate", "pitch_rate", "volume", "instructions"]);
const ORDERED_MODELS = new Set(["wan/wan3.0-video#video", "wan/wan3.0-video-prime#video"]);
const REFERENCE_MEDIA_ROLES = new Set(["reference_image", "reference_video", "reference_audio"]);

export class AtelierGenerationRequestError extends Error {
    code: AtelierGenerationRequestErrorCode;

    constructor(code: AtelierGenerationRequestErrorCode) {
        super(code);
        this.name = "AtelierGenerationRequestError";
        this.code = code;
    }
}

export function beginAtelierGenerationRequest(options: BeginAtelierGenerationRequestOptions): AtelierGenerationRequestResult {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const clientRequestId = validateRequestId(options.idFactory ? options.idFactory() : createAtelierGenerationRequestId());
    const request = buildOriginalRequest(options, clientRequestId);
    const existing = listStoredForOwner(storage, owner).find(item => item.clientRequestId === clientRequestId);
    if (existing) {
        if (canonicalJson(existing.request) !== canonicalJson(request)) {
            throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
        }
        return { status: "pending", request: existing };
    }
    const projectCollision = listStoredForProject(storage, owner)
        .find(item => item.clientRequestId === clientRequestId && item.nodeId !== owner.nodeId);
    if (projectCollision) {
        throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
    }

    const record = freezeStoredRequest(validateStoredRequest({
        schema_version: 1,
        ...owner,
        clientRequestId,
        request,
    }, owner));
    try {
        const key = requestStorageKey(owner, clientRequestId);
        const serialized = canonicalJson(record);
        storage.setItem(key, serialized);
        if (storage.getItem(key) !== serialized) {
            throw new AtelierGenerationRequestError("atelier_generation_request_storage_unavailable");
        }
    } catch {
        throw new AtelierGenerationRequestError("atelier_generation_request_storage_unavailable");
    }
    return { status: "created", request: record };
}

export function createAtelierGenerationRequestId(): string {
    const cryptoRef = globalThis.crypto;
    let suffix: string;
    if (cryptoRef && typeof cryptoRef.randomUUID === "function") {
        suffix = cryptoRef.randomUUID();
    } else if (cryptoRef && typeof cryptoRef.getRandomValues === "function") {
        const bytes = cryptoRef.getRandomValues(new Uint8Array(16));
        suffix = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
    } else {
        suffix = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    }
    return `generation-${suffix}`;
}

export function readAtelierGenerationRequest(
    options: AtelierGenerationRequestOwner & { clientRequestId: string; storage?: Storage },
): StoredAtelierGenerationRequest | null {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateRequestId(options.clientRequestId);
    return listStoredForOwner(storage, owner).find(item => item.clientRequestId === id) ?? null;
}

export function listAtelierGenerationRequests(
    options: AtelierGenerationRequestOwner & { storage?: Storage },
): StoredAtelierGenerationRequest[] {
    const storage = resolveStorage(options.storage);
    return listStoredForOwner(storage, normalizeOwner(options));
}

export function listProjectAtelierGenerationRequests(
    options: AtelierGenerationProjectRequestOwner & { storage?: Storage },
): StoredAtelierGenerationRequest[] {
    const storage = resolveStorage(options.storage);
    return listStoredForProject(storage, normalizeProjectOwner(options));
}

export function resolveAtelierGenerationRequest(
    options: AtelierGenerationRequestOwner & {
        clientRequestId: string;
        request: AtelierGenerationOriginalRequest;
        storage?: Storage;
    },
): boolean {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateRequestId(options.clientRequestId);
    const requestId = requestClientRequestId(options.request);
    const expectedRequest = freezeOriginalRequest(validateOriginalRequest(options.request, requestId));
    const existing = listStoredForOwner(storage, owner).find(item => item.clientRequestId === id);
    if (!existing || requestId !== id) return false;
    if (canonicalJson(existing.request) !== canonicalJson(expectedRequest)) return false;
    try {
        storage.removeItem(requestStorageKey(owner, id));
    } catch {
        throw new AtelierGenerationRequestError("atelier_generation_request_storage_unavailable");
    }
    return true;
}

export function atelierGenerationNodePendingKey(owner: AtelierGenerationRequestOwner): string {
    return ownerRequestPrefix(normalizeOwner(owner));
}

interface NormalizedOwner {
    apiBase: string;
    storageId: string;
    projectId: string;
    nodeId: string;
}

interface NormalizedProjectOwner {
    apiBase: string;
    storageId: string;
    projectId: string;
}

function normalizeOwner(owner: AtelierGenerationRequestOwner): NormalizedOwner {
    const normalized = {
        apiBase: typeof owner.environment.apiUrl === "string" ? owner.environment.apiUrl : "",
        storageId: typeof owner.environment.storageId === "string" ? owner.environment.storageId : "",
        projectId: typeof owner.projectId === "string" ? owner.projectId : "",
        nodeId: typeof owner.nodeId === "string" ? owner.nodeId : "",
    };
    if (!normalized.apiBase || !normalized.storageId || !normalized.projectId || !normalized.nodeId) {
        throw new AtelierGenerationRequestError("atelier_generation_request_owner_invalid");
    }
    return normalized;
}

function normalizeProjectOwner(owner: AtelierGenerationProjectRequestOwner): NormalizedProjectOwner {
    const normalized = {
        apiBase: typeof owner.environment.apiUrl === "string" ? owner.environment.apiUrl : "",
        storageId: typeof owner.environment.storageId === "string" ? owner.environment.storageId : "",
        projectId: typeof owner.projectId === "string" ? owner.projectId : "",
    };
    if (!normalized.apiBase || !normalized.storageId || !normalized.projectId) {
        throw new AtelierGenerationRequestError("atelier_generation_request_owner_invalid");
    }
    return normalized;
}

function buildOriginalRequest(options: BeginAtelierGenerationRequestOptions, clientRequestId: string): AtelierGenerationOriginalRequest {
    if (isImageOptions(options)) {
        if (options.entrypoint !== "atelier.provider_image.v1" || !Number.isSafeInteger(options.expectedRevision) || options.expectedRevision < 0
            || typeof options.modelId !== "string" || !options.modelId || (options.mode !== "t2i" && options.mode !== "i2i")
            || typeof options.prompt !== "string") {
            throw new AtelierGenerationRequestError("atelier_generation_request_payload_invalid");
        }
        const request: AtelierImageCandidateRequest = {
            entrypoint: "atelier.provider_image.v1",
            expected_revision: options.expectedRevision,
            client_request_id: clientRequestId,
            model_id: options.modelId,
            mode: options.mode,
            prompt: options.prompt,
            media: validateImageMedia(options.media ?? [], options.mode),
        };
        if (Object.prototype.hasOwnProperty.call(options, "params")) request.params = validateImageParams(options.params);
        if (Object.prototype.hasOwnProperty.call(options, "batchSize")) request.batch_size = validateBatchSize(options.batchSize);
        return freezeOriginalRequest(request);
    }
    if (isAudioOptions(options)) {
        if (options.entrypoint !== "atelier.provider_audio.v1" || !Number.isSafeInteger(options.expectedRevision) || options.expectedRevision < 0
            || typeof options.voice !== "string" || !options.voice.trim() || typeof options.text !== "string" || !options.text.trim()) {
            throw new AtelierGenerationRequestError("atelier_generation_request_payload_invalid");
        }
        const request: AtelierAudioCandidateRequest = {
            entrypoint: "atelier.provider_audio.v1",
            expected_revision: options.expectedRevision,
            client_request_id: clientRequestId,
            voice: options.voice,
            text: options.text,
        };
        if (Object.prototype.hasOwnProperty.call(options, "params")) request.params = validateAudioParams(options.params);
        return freezeOriginalRequest(request);
    }
    if (isOrderedOptions(options)) {
        if (options.contractVersion !== "ordered-media-v1"
            || !Number.isSafeInteger(options.expectedRevision) || options.expectedRevision < 0
            || !ORDERED_MODELS.has(options.modelId) || typeof options.prompt !== "string") {
            throw new AtelierGenerationRequestError("atelier_generation_request_payload_invalid");
        }
        const request: AtelierOrderedVideoCandidateRequest = {
            expected_revision: options.expectedRevision,
            client_request_id: clientRequestId,
            contract_version: "ordered-media-v1",
            model_id: options.modelId,
            mode: validateOrderedMode(options.mode),
            prompt: options.prompt,
            media: validateOrderedMedia(options.media, options.mode),
        };
        if (Object.prototype.hasOwnProperty.call(options, "params")) {
            request.params = validateOrderedParams(options.modelId, options.params);
        }
        if (Object.prototype.hasOwnProperty.call(options, "batchSize")) {
            request.batch_size = validateBatchSize(options.batchSize);
        }
        return freezeOriginalRequest(request);
    }
    if (!Number.isSafeInteger(options.expectedRevision) || options.expectedRevision < 0
        || typeof options.modelId !== "string" || !options.modelId
        || options.mode !== "i2v" || typeof options.prompt !== "string") {
        throw new AtelierGenerationRequestError("atelier_generation_request_payload_invalid");
    }
    const request: AtelierVideoCandidateRequest = {
        expected_revision: options.expectedRevision,
        client_request_id: clientRequestId,
        model_id: options.modelId,
        mode: options.mode,
        prompt: options.prompt,
        first_frame: validateFirstFrame(options.firstFrame),
    };
    if (Object.prototype.hasOwnProperty.call(options, "params")) {
        request.params = validateLegacyParams(options.params);
    }
    if (Object.prototype.hasOwnProperty.call(options, "batchSize")) {
        request.batch_size = validateBatchSize(options.batchSize);
    }
    return freezeOriginalRequest(request);
}

function requestStorageKey(owner: NormalizedOwner, clientRequestId: string): string {
    return `${ownerRequestPrefix(owner)}:${encodeURIComponent(validateRequestId(clientRequestId))}`;
}

function ownerRequestPrefix(owner: NormalizedOwner): string {
    return `${REQUEST_PREFIX}${encodeURIComponent(canonicalJson(owner))}`;
}

function listStoredForOwner(storage: Storage, owner: NormalizedOwner): StoredAtelierGenerationRequest[] {
    const requests: StoredAtelierGenerationRequest[] = [];
    const prefix = `${ownerRequestPrefix(owner)}:`;
    try {
        for (let index = 0; index < storage.length; index += 1) {
            const key = storage.key(index);
            if (!key?.startsWith(prefix)) continue;
            const keyRequestId = validateRequestId(decodeURIComponent(key.slice(prefix.length)));
            const raw = storage.getItem(key);
            if (raw === null) continue;
            let parsed: unknown;
            try {
                parsed = JSON.parse(raw);
            } catch {
                throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
            }
            if (!matchesOwner(parsed, owner)) {
                throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
            }
            const request = freezeStoredRequest(validateStoredRequest(parsed, owner));
            if (request.clientRequestId !== keyRequestId) {
                throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
            }
            requests.push(request);
        }
    } catch (error) {
        if (error instanceof AtelierGenerationRequestError) throw error;
        throw new AtelierGenerationRequestError("atelier_generation_request_storage_unavailable");
    }
    return requests.sort((left, right) => left.clientRequestId.localeCompare(right.clientRequestId));
}

function listStoredForProject(storage: Storage, owner: NormalizedProjectOwner): StoredAtelierGenerationRequest[] {
    const requests: StoredAtelierGenerationRequest[] = [];
    try {
        for (let index = 0; index < storage.length; index += 1) {
            const key = storage.key(index);
            if (!key?.startsWith(REQUEST_PREFIX)) continue;
            const raw = storage.getItem(key);
            if (raw === null) continue;
            let parsed: unknown;
            try {
                parsed = JSON.parse(raw);
            } catch {
                if (keyBelongsToProject(key, owner)) {
                    throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
                }
                continue;
            }
            if (!matchesProjectOwner(parsed, owner)) {
                if (keyBelongsToProject(key, owner)) {
                    throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
                }
                continue;
            }
            const nodeOwner = ownerFromStoredRecord(parsed);
            const expectedKey = requestStorageKey(nodeOwner, requestClientRequestIdFromStoredRecord(parsed));
            if (key !== expectedKey) {
                throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
            }
            requests.push(freezeStoredRequest(validateStoredRequest(parsed, nodeOwner)));
        }
    } catch (error) {
        if (error instanceof AtelierGenerationRequestError) throw error;
        throw new AtelierGenerationRequestError("atelier_generation_request_storage_unavailable");
    }
    return requests.sort((left, right) => {
        const byRequestId = left.clientRequestId.localeCompare(right.clientRequestId);
        return byRequestId || left.nodeId.localeCompare(right.nodeId);
    });
}

function matchesOwner(value: unknown, owner: NormalizedOwner): boolean {
    return isPlainRecord(value)
        && (value as Partial<StoredAtelierGenerationRequest>).apiBase === owner.apiBase
        && (value as Partial<StoredAtelierGenerationRequest>).projectId === owner.projectId
        && (value as Partial<StoredAtelierGenerationRequest>).storageId === owner.storageId
        && (value as Partial<StoredAtelierGenerationRequest>).nodeId === owner.nodeId;
}

function matchesProjectOwner(value: unknown, owner: NormalizedProjectOwner): boolean {
    return isPlainRecord(value)
        && (value as Partial<StoredAtelierGenerationRequest>).apiBase === owner.apiBase
        && (value as Partial<StoredAtelierGenerationRequest>).projectId === owner.projectId
        && (value as Partial<StoredAtelierGenerationRequest>).storageId === owner.storageId
        && typeof (value as Partial<StoredAtelierGenerationRequest>).nodeId === "string"
        && Boolean((value as Partial<StoredAtelierGenerationRequest>).nodeId);
}

function ownerFromStoredRecord(value: unknown): NormalizedOwner {
    if (!isPlainRecord(value)) throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
    const record = value as Partial<StoredAtelierGenerationRequest>;
    if (typeof record.apiBase !== "string" || !record.apiBase
        || typeof record.storageId !== "string" || !record.storageId
        || typeof record.projectId !== "string" || !record.projectId
        || typeof record.nodeId !== "string" || !record.nodeId) {
        throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
    }
    return {
        apiBase: record.apiBase,
        storageId: record.storageId,
        projectId: record.projectId,
        nodeId: record.nodeId,
    };
}

function requestClientRequestIdFromStoredRecord(value: unknown): string {
    if (!isPlainRecord(value) || typeof (value as Partial<StoredAtelierGenerationRequest>).clientRequestId !== "string") {
        throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
    }
    return validateRequestId((value as Partial<StoredAtelierGenerationRequest>).clientRequestId as string);
}

function keyBelongsToProject(key: string, owner: NormalizedProjectOwner): boolean {
    const parsedOwner = ownerFromStorageKey(key);
    return Boolean(parsedOwner
        && parsedOwner.apiBase === owner.apiBase
        && parsedOwner.storageId === owner.storageId
        && parsedOwner.projectId === owner.projectId);
}

function ownerFromStorageKey(key: string): NormalizedOwner | null {
    if (!key.startsWith(REQUEST_PREFIX)) return null;
    const separatorIndex = key.indexOf(":", REQUEST_PREFIX.length);
    if (separatorIndex < 0) return null;
    try {
        const rawOwner = JSON.parse(decodeURIComponent(key.slice(REQUEST_PREFIX.length, separatorIndex)));
        if (!isPlainRecord(rawOwner)
            || typeof rawOwner.apiBase !== "string" || !rawOwner.apiBase
            || typeof rawOwner.storageId !== "string" || !rawOwner.storageId
            || typeof rawOwner.projectId !== "string" || !rawOwner.projectId
            || typeof rawOwner.nodeId !== "string" || !rawOwner.nodeId) {
            return null;
        }
        return {
            apiBase: rawOwner.apiBase,
            storageId: rawOwner.storageId,
            projectId: rawOwner.projectId,
            nodeId: rawOwner.nodeId,
        };
    } catch {
        return null;
    }
}

function validateStoredRequest(value: unknown, owner: NormalizedOwner): StoredAtelierGenerationRequest {
    if (!isPlainRecord(value)) throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
    const record = value as Partial<StoredAtelierGenerationRequest>;
    if (record.schema_version !== 1 || record.apiBase !== owner.apiBase || record.storageId !== owner.storageId
        || record.projectId !== owner.projectId || record.nodeId !== owner.nodeId || typeof record.clientRequestId !== "string") {
        throw new AtelierGenerationRequestError("atelier_generation_request_store_corrupt");
    }
    const clientRequestId = validateRequestId(record.clientRequestId);
    const request = freezeOriginalRequest(validateOriginalRequest(record.request, clientRequestId, "atelier_generation_request_store_corrupt"));
    return {
        schema_version: 1,
        apiBase: record.apiBase,
        storageId: record.storageId,
        projectId: record.projectId,
        nodeId: record.nodeId,
        clientRequestId,
        request,
    };
}

function requestClientRequestId(value: unknown): string {
    if (!isPlainRecord(value) || typeof (value as { client_request_id?: unknown }).client_request_id !== "string") {
        throw new AtelierGenerationRequestError("atelier_generation_request_payload_invalid");
    }
    return validateRequestId((value as { client_request_id: string }).client_request_id);
}

function validateOriginalRequest(
    value: unknown,
    clientRequestId: string,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): AtelierGenerationOriginalRequest {
    const fail = (): never => { throw new AtelierGenerationRequestError(errorCode); };
    if (!isPlainRecord(value)) fail();
    const record = value as Record<string, unknown>;
    const isImage = record.entrypoint === "atelier.provider_image.v1";
    const isAudio = record.entrypoint === "atelier.provider_audio.v1";
    const isOrdered = record.contract_version === "ordered-media-v1";
    const allowed = isImage ? new Set(["entrypoint", "expected_revision", "client_request_id", "model_id", "mode", "prompt", "media", "params", "batch_size"])
        : isAudio ? new Set(["entrypoint", "expected_revision", "client_request_id", "voice", "text", "params"])
            : isOrdered ? new Set(["expected_revision", "client_request_id", "contract_version", "model_id", "mode", "prompt", "media", "params", "batch_size"])
                : new Set(["expected_revision", "client_request_id", "model_id", "mode", "prompt", "first_frame", "params", "batch_size"]);
    if (Object.keys(record).some(key => !allowed.has(key))) fail();
    if (!Number.isSafeInteger(record.expected_revision) || (record.expected_revision as number) < 0
        || record.client_request_id !== clientRequestId) fail();
    if (isImage) {
        if (typeof record.model_id !== "string" || !record.model_id || (record.mode !== "t2i" && record.mode !== "i2i") || typeof record.prompt !== "string") fail();
        const request: AtelierImageCandidateRequest = {
            entrypoint: "atelier.provider_image.v1",
            expected_revision: record.expected_revision as number,
            client_request_id: clientRequestId,
            model_id: record.model_id as string,
            mode: record.mode as "t2i" | "i2i",
            prompt: record.prompt as string,
            media: validateImageMedia(record.media, record.mode as "t2i" | "i2i", errorCode),
        };
        if (Object.prototype.hasOwnProperty.call(record, "params")) request.params = validateImageParams(record.params, errorCode);
        if (Object.prototype.hasOwnProperty.call(record, "batch_size")) request.batch_size = validateBatchSize(record.batch_size, errorCode);
        return request;
    }
    if (isAudio) {
        if (typeof record.voice !== "string" || !record.voice.trim() || typeof record.text !== "string" || !record.text.trim()) fail();
        const request: AtelierAudioCandidateRequest = {
            entrypoint: "atelier.provider_audio.v1",
            expected_revision: record.expected_revision as number,
            client_request_id: clientRequestId,
            voice: record.voice as string,
            text: record.text as string,
        };
        if (Object.prototype.hasOwnProperty.call(record, "params")) request.params = validateAudioParams(record.params, errorCode);
        return request;
    }
    if (typeof record.model_id !== "string" || !record.model_id || typeof record.prompt !== "string") fail();
    if (isOrdered) {
        if (!ORDERED_MODELS.has(record.model_id as string)) fail();
        const mode = validateOrderedMode(record.mode, errorCode);
        const request: AtelierOrderedVideoCandidateRequest = {
            expected_revision: record.expected_revision as number,
            client_request_id: clientRequestId,
            contract_version: "ordered-media-v1",
            model_id: record.model_id as AtelierOrderedVideoModel,
            mode,
            prompt: record.prompt as string,
            media: validateOrderedMedia(record.media, mode, errorCode),
        };
        if (Object.prototype.hasOwnProperty.call(record, "params")) request.params = validateOrderedParams(record.model_id as string, record.params, errorCode);
        if (Object.prototype.hasOwnProperty.call(record, "batch_size")) request.batch_size = validateBatchSize(record.batch_size, errorCode);
        return request;
    }
    if (record.mode !== "i2v") fail();
    const request: AtelierVideoCandidateRequest = {
        expected_revision: record.expected_revision as number,
        client_request_id: clientRequestId,
        model_id: record.model_id as string,
        mode: "i2v",
        prompt: record.prompt as string,
        first_frame: validateFirstFrame(record.first_frame, errorCode),
    };
    if (Object.prototype.hasOwnProperty.call(record, "params")) request.params = validateLegacyParams(record.params, errorCode);
    if (Object.prototype.hasOwnProperty.call(record, "batch_size")) request.batch_size = validateBatchSize(record.batch_size, errorCode);
    return request;
}

function isImageOptions(options: BeginAtelierGenerationRequestOptions): options is BeginAtelierImageGenerationRequestOptions {
    return "entrypoint" in options && options.entrypoint === "atelier.provider_image.v1";
}

function isAudioOptions(options: BeginAtelierGenerationRequestOptions): options is BeginAtelierAudioGenerationRequestOptions {
    return "entrypoint" in options && options.entrypoint === "atelier.provider_audio.v1";
}

function isOrderedOptions(options: BeginAtelierGenerationRequestOptions): options is BeginAtelierOrderedGenerationRequestOptions {
    return "contractVersion" in options;
}

function validateOrderedMode(
    value: unknown,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): AtelierOrderedVideoMode {
    if (value !== "t2v" && value !== "i2v" && value !== "r2v") throw new AtelierGenerationRequestError(errorCode);
    return value;
}

function validateOrderedRole(
    value: unknown,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): AtelierOrderedMediaRole {
    if (!isAtelierOrderedMediaRole(value)) throw new AtelierGenerationRequestError(errorCode);
    return value;
}

function validateOrderedMediaItem(
    value: unknown,
    errorCode: AtelierGenerationRequestErrorCode,
): AtelierOrderedMediaSelection {
    const fail = (): never => { throw new AtelierGenerationRequestError(errorCode); };
    if (!isPlainRecord(value)) fail();
    const record = value as Record<string, unknown>;
    if (Object.keys(record).some(key => !["role", "ordinal", "node_id", "media_url"].includes(key))) fail();
    const role = validateOrderedRole(record.role, errorCode);
    if (!Number.isSafeInteger(record.ordinal) || (record.ordinal as number) < 0
        || typeof record.node_id !== "string" || !record.node_id.trim()
        || typeof record.media_url !== "string" || !record.media_url.trim()) fail();
    return { role, ordinal: record.ordinal as number, node_id: record.node_id as string, media_url: record.media_url as string };
}

function validateOrderedMedia(
    value: unknown,
    mode: AtelierOrderedVideoMode,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): AtelierOrderedMediaSelection[] {
    const fail = (): never => { throw new AtelierGenerationRequestError(errorCode); };
    if (!Array.isArray(value)) fail();
    const media = (value as unknown[]).map((item: unknown) => validateOrderedMediaItem(item, errorCode));
    const seen = new Set<string>();
    const byRole = new Map<AtelierOrderedMediaRole, number[]>();
    for (const item of media) {
        const key = `${item.role}:${item.ordinal}`;
        if (seen.has(key)) fail();
        seen.add(key);
        if ((item.role === "first_frame" || item.role === "last_frame") && item.ordinal !== 0) fail();
        byRole.set(item.role, [...(byRole.get(item.role) ?? []), item.ordinal]);
    }
    for (const ordinals of Array.from(byRole.values())) {
        if (ordinals.some((ordinal, index) => ordinal !== index)) fail();
    }
    if (!hasCanonicalAtelierOrderedMediaRoleOrder(media)) fail();
    const roles = media.map((item: AtelierOrderedMediaSelection) => item.role);
    if (mode === "t2v") {
        if (media.length !== 0) fail();
    } else if (mode === "i2v") {
        const exactFirst = roles.length === 1 && roles[0] === "first_frame";
        const firstLast = roles.length === 2 && roles[0] === "first_frame" && roles[1] === "last_frame";
        if (!exactFirst && !firstLast) fail();
    } else if (mode === "r2v") {
        if (media.length === 0 || roles.some((role: AtelierOrderedMediaRole) => !REFERENCE_MEDIA_ROLES.has(role))) fail();
    }
    const imageCount = roles.filter((role: AtelierOrderedMediaRole) => role === "first_frame" || role === "last_frame" || role === "reference_image").length;
    const videoCount = roles.filter((role: AtelierOrderedMediaRole) => role === "reference_video").length;
    const audioCount = roles.filter((role: AtelierOrderedMediaRole) => role === "reference_audio").length;
    if (imageCount > 10 || videoCount > 5 || audioCount > 5 || media.length > 20) fail();
    return media;
}

function validateFirstFrame(
    value: unknown,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): AtelierGenerationFirstFrame {
    const fail = (): never => { throw new AtelierGenerationRequestError(errorCode); };
    if (!isPlainRecord(value)) fail();
    const record = value as Partial<AtelierGenerationFirstFrame>;
    const nodeId = record.node_id;
    const mediaUrl = record.media_url;
    if (Object.keys(record).some(key => key !== "node_id" && key !== "media_url")
        || typeof nodeId !== "string" || !nodeId.trim()
        || typeof mediaUrl !== "string" || !mediaUrl.trim()) {
        fail();
    }
    return { node_id: nodeId as string, media_url: mediaUrl as string };
}

function validateImageMedia(
    value: unknown,
    mode: "t2i" | "i2i",
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): AtelierImageReferenceSelection[] {
    const fail = (): never => { throw new AtelierGenerationRequestError(errorCode); };
    if (!Array.isArray(value)) fail();
    const items = value as unknown[];
    const media = items.map((item: unknown) => {
        if (!isPlainRecord(item)) fail();
        const record = item as Record<string, unknown>;
        if (Object.keys(record).some(key => !["role", "ordinal", "node_id", "media_url"].includes(key)) || record.role !== "reference_image"
            || !Number.isSafeInteger(record.ordinal) || (record.ordinal as number) < 0
            || typeof record.node_id !== "string" || !record.node_id.trim()
            || typeof record.media_url !== "string" || !record.media_url.trim()) fail();
        return { role: "reference_image" as const, ordinal: record.ordinal as number, node_id: record.node_id as string, media_url: record.media_url as string };
    });
    if (mode === "t2i" && media.length !== 0) fail();
    if (mode === "i2i" && (media.length === 0 || media.some((item: AtelierImageReferenceSelection, index: number) => item.ordinal !== index))) fail();
    return media;
}

function validateImageParams(
    value: unknown,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): NonNullable<AtelierImageCandidateRequest["params"]> {
    const fail = (): never => { throw new AtelierGenerationRequestError(errorCode); };
    if (!isPlainRecord(value)) fail();
    const record = value as Record<string, unknown>;
    const params: NonNullable<AtelierImageCandidateRequest["params"]> = {};
    for (const [key, item] of Object.entries(record)) {
        if (!IMAGE_PARAM_FIELDS.has(key)) fail();
        if ((key === "size" || key === "negative_prompt") && typeof item === "string") params[key] = item;
        else if (key === "seed" && typeof item === "number" && Number.isSafeInteger(item)) params.seed = item;
        else if ((key === "prompt_extend" || key === "watermark") && typeof item === "boolean") params[key] = item;
        else fail();
    }
    return params;
}

function validateAudioParams(
    value: unknown,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): NonNullable<AtelierAudioCandidateRequest["params"]> {
    const fail = (): never => { throw new AtelierGenerationRequestError(errorCode); };
    if (!isPlainRecord(value)) fail();
    const record = value as Record<string, unknown>;
    const params: NonNullable<AtelierAudioCandidateRequest["params"]> = {};
    for (const [key, item] of Object.entries(record)) {
        if (!AUDIO_PARAM_FIELDS.has(key)) fail();
        if (key === "instructions" && typeof item === "string") params.instructions = item;
        else if ((key === "speech_rate" || key === "pitch_rate" || key === "volume") && typeof item === "number" && Number.isFinite(item)
            && item >= (key === "volume" ? 0 : 0.5) && item <= (key === "volume" ? 100 : 2)) params[key] = item;
        else fail();
    }
    return params;
}

function validateBatchSize(
    value: unknown,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): number {
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1 || value > 6) {
        throw new AtelierGenerationRequestError(errorCode);
    }
    return value;
}

function validateLegacyParams(
    value: unknown,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): AtelierLegacyGenerationParams {
    const fail = (): never => { throw new AtelierGenerationRequestError(errorCode); };
    if (!isPlainRecord(value)) fail();
    const record = value as Record<string, unknown>;
    const params: AtelierLegacyGenerationParams = {};
    for (const [key, item] of Object.entries(record)) {
        if (!LEGACY_PARAM_FIELDS.has(key)) fail();
        if (key === "duration") {
            if (typeof item !== "number" || !Number.isFinite(item)) fail();
            params.duration = item as number;
        } else if (key === "seed") {
            if (typeof item !== "number" || !Number.isSafeInteger(item)) fail();
            params.seed = item as number;
        } else if (key === "resolution") {
            if (typeof item !== "string") fail();
            params.resolution = item as string;
        } else if (key === "negative_prompt") {
            if (typeof item !== "string") fail();
            params.negative_prompt = item as string;
        } else if (key === "prompt_extend") {
            if (typeof item !== "boolean") fail();
            params.prompt_extend = item as boolean;
        } else if (key === "watermark") {
            if (typeof item !== "boolean") fail();
            params.watermark = item as boolean;
        }
    }
    return params;
}

function validateOrderedParams(
    modelId: string,
    value: unknown,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): AtelierOrderedGenerationParams {
    if (!validateWan3GenerationParams(modelId, value) || !isPlainRecord(value)) {
        throw new AtelierGenerationRequestError(errorCode);
    }
    return { ...(value as AtelierOrderedGenerationParams) };
}

function validateRequestId(value: string): string {
    if (typeof value !== "string" || !CLIENT_REQUEST_ID_RE.test(value)) {
        throw new AtelierGenerationRequestError("atelier_generation_request_id_invalid");
    }
    return value;
}

function resolveStorage(storage?: Storage): Storage {
    if (storage) return storage;
    try {
        if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
    } catch {
        throw new AtelierGenerationRequestError("atelier_generation_request_storage_unavailable");
    }
    throw new AtelierGenerationRequestError("atelier_generation_request_storage_unavailable");
}

function canonicalJson(value: unknown): string {
    return JSON.stringify(sortJson(strictJsonValue(value, new WeakSet())));
}

function strictJsonValue(
    value: unknown,
    seen: WeakSet<object>,
    errorCode: AtelierGenerationRequestErrorCode = "atelier_generation_request_payload_invalid",
): unknown {
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new AtelierGenerationRequestError(errorCode);
        return value;
    }
    if (Array.isArray(value)) {
        rejectCycle(value, seen, errorCode);
        const output: unknown[] = [];
        for (let index = 0; index < value.length; index += 1) {
            if (!Object.prototype.hasOwnProperty.call(value, index)) {
                throw new AtelierGenerationRequestError(errorCode);
            }
            output.push(strictJsonValue(value[index], seen, errorCode));
        }
        seen.delete(value);
        return output;
    }
    if (isPlainRecord(value)) {
        rejectCycle(value, seen, errorCode);
        const output: Record<string, unknown> = Object.create(null);
        for (const [key, item] of Object.entries(value)) {
            Object.defineProperty(output, key, {
                value: strictJsonValue(item, seen, errorCode),
                enumerable: true,
                configurable: true,
                writable: true,
            });
        }
        seen.delete(value);
        return output;
    }
    throw new AtelierGenerationRequestError(errorCode);
}

function rejectCycle(value: object, seen: WeakSet<object>, errorCode: AtelierGenerationRequestErrorCode): void {
    if (seen.has(value)) {
        throw new AtelierGenerationRequestError(errorCode);
    }
    seen.add(value);
}

function sortJson(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(sortJson);
    if (value && typeof value === "object") {
        const output: Record<string, unknown> = Object.create(null);
        for (const key of Object.keys(value).sort()) {
            Object.defineProperty(output, key, {
                value: sortJson((value as Record<string, unknown>)[key]),
                enumerable: true,
                configurable: true,
                writable: true,
            });
        }
        return output;
    }
    return value;
}

function freezeOriginalRequest(request: AtelierGenerationOriginalRequest): AtelierGenerationOriginalRequest {
    const frozen: AtelierGenerationOriginalRequest = "entrypoint" in request && request.entrypoint === "atelier.provider_image.v1"
        ? { ...request, media: request.media.map(item => ({ ...item })) }
        : "entrypoint" in request && request.entrypoint === "atelier.provider_audio.v1"
            ? { ...request }
            : "contract_version" in request
                ? { ...request, media: request.media.map(item => ({ ...item })) }
                : { ...request, first_frame: { ...request.first_frame } };
    if (request.params !== undefined) {
        frozen.params = { ...request.params } as never;
    }
    return deepFreeze(frozen);
}

function freezeStoredRequest(request: StoredAtelierGenerationRequest): StoredAtelierGenerationRequest {
    return deepFreeze({ ...request, request: freezeOriginalRequest(request.request) });
}

function deepFreeze<T>(value: T): T {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
        Object.freeze(value);
        for (const item of Object.values(value as Record<string, unknown>)) {
            deepFreeze(item);
        }
    }
    return value;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const proto = Object.getPrototypeOf(value);
    return proto === Object.prototype || proto === null;
}
