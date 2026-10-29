import type { RuntimeEnvironment } from "@/lib/runtimeEnvironment";

export type PlaygroundMode = "t2i" | "i2i" | "t2v" | "i2v" | "r2v" | "v2v";

export interface PlaygroundGenerateRequestBody {
    client_request_id: string;
    mode: PlaygroundMode;
    model_id: string;
    prompt: string;
    negative_prompt?: string;
    input_media?: string[];
    parameters?: Record<string, unknown>;
    batch_size?: number;
}

export interface PlaygroundRequestOwner {
    environment: RuntimeEnvironment;
}

export interface StoredPlaygroundGenerationRequest {
    schema_version: 1;
    apiBase: string;
    storageId: string;
    clientRequestId: string;
    requestFingerprint: string;
    request: PlaygroundGenerateRequestBody;
    createdAt: number;
}

export type PlaygroundGenerationRequestErrorCode =
    | "playground_generation_request_storage_unavailable"
    | "playground_generation_request_store_corrupt"
    | "playground_generation_request_owner_invalid"
    | "playground_generation_request_payload_invalid"
    | "playground_generation_request_id_invalid";

export class PlaygroundGenerationRequestError extends Error {
    code: PlaygroundGenerationRequestErrorCode;

    constructor(code: PlaygroundGenerationRequestErrorCode) {
        super(code);
        this.name = "PlaygroundGenerationRequestError";
        this.code = code;
    }
}

interface BeginPlaygroundGenerationRequestOptions extends PlaygroundRequestOwner {
    mode: PlaygroundMode;
    modelId: string;
    prompt: string;
    negativePrompt?: string;
    inputMedia?: string[];
    parameters?: Record<string, unknown>;
    batchSize?: number;
    storage?: Storage;
    idFactory?: () => string;
    now?: () => number;
}

const REQUEST_PREFIX = "lumenx.playgroundGeneration.request.v1:";
const CLIENT_REQUEST_ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const MODES = new Set<PlaygroundMode>(["t2i", "i2i", "t2v", "i2v", "r2v", "v2v"]);

export function beginPlaygroundGenerationRequest(options: BeginPlaygroundGenerationRequestOptions): StoredPlaygroundGenerationRequest {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const clientRequestId = validateRequestId(options.idFactory ? options.idFactory() : createPlaygroundGenerationRequestId());
    const request = buildRequest(options, clientRequestId);
    const requestFingerprint = playgroundRequestFingerprint(request);
    const existing = listStoredForOwner(storage, owner).find(item => item.clientRequestId === clientRequestId);
    if (existing) {
        if (existing.requestFingerprint !== requestFingerprint || canonicalJson(existing.request) !== canonicalJson(request)) {
            throw new PlaygroundGenerationRequestError("playground_generation_request_store_corrupt");
        }
        return existing;
    }

    const record = freezeStoredRequest(validateStoredRequest({
        schema_version: 1,
        ...owner,
        clientRequestId,
        requestFingerprint,
        request,
        createdAt: options.now ? options.now() : Date.now(),
    }, owner));
    try {
        const key = requestStorageKey(owner, clientRequestId);
        const serialized = canonicalJson(record);
        storage.setItem(key, serialized);
        if (storage.getItem(key) !== serialized) {
            throw new PlaygroundGenerationRequestError("playground_generation_request_storage_unavailable");
        }
    } catch (error) {
        if (error instanceof PlaygroundGenerationRequestError) throw error;
        throw new PlaygroundGenerationRequestError("playground_generation_request_storage_unavailable");
    }
    return record;
}

export function createPlaygroundGenerationRequestId(): string {
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
    return `playground-${suffix}`;
}

export function listPlaygroundGenerationRequests(
    options: PlaygroundRequestOwner & { storage?: Storage },
): StoredPlaygroundGenerationRequest[] {
    const storage = resolveStorage(options.storage);
    return listStoredForOwner(storage, normalizeOwner(options));
}

export function readPlaygroundGenerationRequest(
    options: PlaygroundRequestOwner & { clientRequestId: string; storage?: Storage },
): StoredPlaygroundGenerationRequest | null {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateRequestId(options.clientRequestId);
    return listStoredForOwner(storage, owner).find(item => item.clientRequestId === id) ?? null;
}

export function resolvePlaygroundGenerationRequest(
    options: PlaygroundRequestOwner & {
        clientRequestId: string;
        request: PlaygroundGenerateRequestBody;
        storage?: Storage;
    },
): boolean {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateRequestId(options.clientRequestId);
    const request = freezeRequest(validateRequest(options.request, id));
    const existing = listStoredForOwner(storage, owner).find(item => item.clientRequestId === id);
    if (!existing || existing.requestFingerprint !== playgroundRequestFingerprint(request)
        || canonicalJson(existing.request) !== canonicalJson(request)) {
        return false;
    }
    try {
        storage.removeItem(requestStorageKey(owner, id));
    } catch {
        throw new PlaygroundGenerationRequestError("playground_generation_request_storage_unavailable");
    }
    return true;
}

export function resolveStoredPlaygroundGenerationRequest(
    journal: StoredPlaygroundGenerationRequest,
    storage?: Storage,
): boolean {
    return resolvePlaygroundGenerationRequest({
        environment: {
            apiUrl: journal.apiBase,
            storageId: journal.storageId,
            key: JSON.stringify([journal.apiBase, journal.storageId]),
            generation: -1,
        },
        clientRequestId: journal.clientRequestId,
        request: journal.request,
        storage,
    });
}

export function playgroundRequestStoragePrefix(owner: PlaygroundRequestOwner): string {
    return ownerRequestPrefix(normalizeOwner(owner));
}

export function playgroundRequestFingerprint(request: PlaygroundGenerateRequestBody): string {
    return canonicalJson(request);
}

function buildRequest(options: BeginPlaygroundGenerationRequestOptions, clientRequestId: string): PlaygroundGenerateRequestBody {
    const request: PlaygroundGenerateRequestBody = {
        client_request_id: clientRequestId,
        mode: validateMode(options.mode),
        model_id: validateNonEmptyString(options.modelId),
        prompt: validatePrompt(options.prompt),
    };
    if (options.negativePrompt !== undefined) request.negative_prompt = validateString(options.negativePrompt);
    if (options.inputMedia !== undefined && options.inputMedia.length > 0) request.input_media = validateStringArray(options.inputMedia);
    if (options.parameters !== undefined && Object.keys(options.parameters).length > 0) request.parameters = validateParameters(options.parameters);
    if (options.batchSize !== undefined && options.batchSize > 1) request.batch_size = validateBatchSize(options.batchSize);
    return freezeRequest(validateRequest(request, clientRequestId));
}

interface NormalizedOwner {
    apiBase: string;
    storageId: string;
}

function normalizeOwner(owner: PlaygroundRequestOwner): NormalizedOwner {
    const normalized = {
        apiBase: typeof owner.environment.apiUrl === "string" ? owner.environment.apiUrl : "",
        storageId: typeof owner.environment.storageId === "string" ? owner.environment.storageId : "",
    };
    if (!normalized.apiBase || !normalized.storageId) {
        throw new PlaygroundGenerationRequestError("playground_generation_request_owner_invalid");
    }
    return normalized;
}

function requestStorageKey(owner: NormalizedOwner, clientRequestId: string): string {
    return `${ownerRequestPrefix(owner)}:${encodeURIComponent(validateRequestId(clientRequestId))}`;
}

function ownerRequestPrefix(owner: NormalizedOwner): string {
    return `${REQUEST_PREFIX}${encodeURIComponent(canonicalJson(owner))}`;
}

function listStoredForOwner(storage: Storage, owner: NormalizedOwner): StoredPlaygroundGenerationRequest[] {
    const requests: StoredPlaygroundGenerationRequest[] = [];
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
                throw new PlaygroundGenerationRequestError("playground_generation_request_store_corrupt");
            }
            const request = freezeStoredRequest(validateStoredRequest(parsed, owner));
            if (request.clientRequestId !== keyRequestId) {
                throw new PlaygroundGenerationRequestError("playground_generation_request_store_corrupt");
            }
            requests.push(request);
        }
    } catch (error) {
        if (error instanceof PlaygroundGenerationRequestError) throw error;
        throw new PlaygroundGenerationRequestError("playground_generation_request_storage_unavailable");
    }
    return requests.sort((left, right) => left.createdAt - right.createdAt || left.clientRequestId.localeCompare(right.clientRequestId));
}

function validateStoredRequest(value: unknown, owner: NormalizedOwner): StoredPlaygroundGenerationRequest {
    if (!isPlainRecord(value)) throw new PlaygroundGenerationRequestError("playground_generation_request_store_corrupt");
    const record = value as Partial<StoredPlaygroundGenerationRequest>;
    if (record.schema_version !== 1 || record.apiBase !== owner.apiBase || record.storageId !== owner.storageId
        || typeof record.clientRequestId !== "string" || typeof record.requestFingerprint !== "string"
        || typeof record.createdAt !== "number" || !Number.isFinite(record.createdAt)) {
        throw new PlaygroundGenerationRequestError("playground_generation_request_store_corrupt");
    }
    const clientRequestId = validateRequestId(record.clientRequestId);
    const request = freezeRequest(validateRequest(record.request, clientRequestId, "playground_generation_request_store_corrupt"));
    const requestFingerprint = playgroundRequestFingerprint(request);
    if (record.requestFingerprint !== requestFingerprint) {
        throw new PlaygroundGenerationRequestError("playground_generation_request_store_corrupt");
    }
    return {
        schema_version: 1,
        apiBase: record.apiBase,
        storageId: record.storageId,
        clientRequestId,
        requestFingerprint,
        request,
        createdAt: record.createdAt,
    };
}

function validateRequest(
    value: unknown,
    clientRequestId: string,
    errorCode: PlaygroundGenerationRequestErrorCode = "playground_generation_request_payload_invalid",
): PlaygroundGenerateRequestBody {
    const fail = (): never => { throw new PlaygroundGenerationRequestError(errorCode); };
    if (!isPlainRecord(value)) fail();
    const record = value as Record<string, unknown>;
    const allowed = new Set(["client_request_id", "mode", "model_id", "prompt", "negative_prompt", "input_media", "parameters", "batch_size"]);
    if (Object.keys(record).some(key => !allowed.has(key)) || record.client_request_id !== clientRequestId) fail();
    const request: PlaygroundGenerateRequestBody = {
        client_request_id: clientRequestId,
        mode: validateMode(record.mode, errorCode),
        model_id: validateNonEmptyString(record.model_id, errorCode),
        prompt: validatePrompt(record.prompt, errorCode),
    };
    if (Object.prototype.hasOwnProperty.call(record, "negative_prompt")) request.negative_prompt = validateString(record.negative_prompt, errorCode);
    if (Object.prototype.hasOwnProperty.call(record, "input_media")) request.input_media = validateStringArray(record.input_media, errorCode);
    if (Object.prototype.hasOwnProperty.call(record, "parameters")) request.parameters = validateParameters(record.parameters, errorCode);
    if (Object.prototype.hasOwnProperty.call(record, "batch_size")) request.batch_size = validateBatchSize(record.batch_size, errorCode);
    return request;
}

function validateMode(value: unknown, errorCode: PlaygroundGenerationRequestErrorCode = "playground_generation_request_payload_invalid"): PlaygroundMode {
    if (!MODES.has(value as PlaygroundMode)) throw new PlaygroundGenerationRequestError(errorCode);
    return value as PlaygroundMode;
}

function validatePrompt(value: unknown, errorCode: PlaygroundGenerationRequestErrorCode = "playground_generation_request_payload_invalid"): string {
    if (typeof value !== "string" || !value.trim()) throw new PlaygroundGenerationRequestError(errorCode);
    return value;
}

function validateNonEmptyString(value: unknown, errorCode: PlaygroundGenerationRequestErrorCode = "playground_generation_request_payload_invalid"): string {
    if (typeof value !== "string" || !value.trim()) throw new PlaygroundGenerationRequestError(errorCode);
    return value;
}

function validateString(value: unknown, errorCode: PlaygroundGenerationRequestErrorCode = "playground_generation_request_payload_invalid"): string {
    if (typeof value !== "string") throw new PlaygroundGenerationRequestError(errorCode);
    return value;
}

function validateStringArray(value: unknown, errorCode: PlaygroundGenerationRequestErrorCode = "playground_generation_request_payload_invalid"): string[] {
    if (!Array.isArray(value) || value.some(item => typeof item !== "string" || !item.trim())) {
        throw new PlaygroundGenerationRequestError(errorCode);
    }
    return value.map(item => item as string);
}

function validateBatchSize(value: unknown, errorCode: PlaygroundGenerationRequestErrorCode = "playground_generation_request_payload_invalid"): number {
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1 || value > 8) {
        throw new PlaygroundGenerationRequestError(errorCode);
    }
    return value;
}

function validateParameters(value: unknown, errorCode: PlaygroundGenerationRequestErrorCode = "playground_generation_request_payload_invalid"): Record<string, unknown> {
    if (!isPlainRecord(value)) throw new PlaygroundGenerationRequestError(errorCode);
    return strictJsonValue(value, new WeakSet(), errorCode) as Record<string, unknown>;
}

function validateRequestId(value: unknown): string {
    if (typeof value !== "string" || !CLIENT_REQUEST_ID_RE.test(value)) {
        throw new PlaygroundGenerationRequestError("playground_generation_request_id_invalid");
    }
    return value;
}

function resolveStorage(storage?: Storage): Storage {
    if (storage) return storage;
    try {
        if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
    } catch {
        throw new PlaygroundGenerationRequestError("playground_generation_request_storage_unavailable");
    }
    throw new PlaygroundGenerationRequestError("playground_generation_request_storage_unavailable");
}

function canonicalJson(value: unknown): string {
    return JSON.stringify(sortJson(strictJsonValue(value, new WeakSet())));
}

function strictJsonValue(
    value: unknown,
    seen: WeakSet<object>,
    errorCode: PlaygroundGenerationRequestErrorCode = "playground_generation_request_payload_invalid",
): unknown {
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new PlaygroundGenerationRequestError(errorCode);
        return value;
    }
    if (Array.isArray(value)) {
        rejectCycle(value, seen, errorCode);
        const output: unknown[] = [];
        for (let index = 0; index < value.length; index += 1) {
            if (!Object.prototype.hasOwnProperty.call(value, index)) {
                throw new PlaygroundGenerationRequestError(errorCode);
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
    throw new PlaygroundGenerationRequestError(errorCode);
}

function rejectCycle(value: object, seen: WeakSet<object>, errorCode: PlaygroundGenerationRequestErrorCode): void {
    if (seen.has(value)) throw new PlaygroundGenerationRequestError(errorCode);
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

function freezeRequest(request: PlaygroundGenerateRequestBody): PlaygroundGenerateRequestBody {
    return deepFreeze({
        ...request,
        ...(request.input_media ? { input_media: [...request.input_media] } : {}),
        ...(request.parameters ? { parameters: { ...request.parameters } } : {}),
    });
}

function freezeStoredRequest(request: StoredPlaygroundGenerationRequest): StoredPlaygroundGenerationRequest {
    return deepFreeze({ ...request, request: freezeRequest(request.request) });
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
