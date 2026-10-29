import type { RuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierMediaKind } from "@/lib/atelierTypes";

export type AtelierUploadOperation = "create_media_node" | "upload_reference_attach";

export interface AtelierUploadRequestOwner {
    environment: RuntimeEnvironment;
    projectId: string;
}

export interface BeginAtelierUploadRequestOptions extends AtelierUploadRequestOwner {
    operation: AtelierUploadOperation;
    targetNodeId: string | null;
    mediaKind: AtelierMediaKind;
    fileName: string;
    fileSize: number;
    baseRevision: number;
    title?: string;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    storage?: Storage;
    idFactory?: () => string;
}

export interface StoredAtelierUploadRequest {
    schema_version: 1;
    apiBase: string;
    storageId: string;
    projectId: string;
    clientRequestId: string;
    operation: AtelierUploadOperation;
    targetNodeId: string | null;
    mediaKind: AtelierMediaKind;
    fileName: string;
    fileSize: number;
    baseRevision: number;
    title?: string;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
}

export type AtelierUploadRequestResult = {
    status: "created" | "pending";
    request: StoredAtelierUploadRequest;
};

export type AtelierUploadRequestErrorCode =
    | "atelier_upload_request_storage_unavailable"
    | "atelier_upload_request_store_corrupt"
    | "atelier_upload_request_owner_invalid"
    | "atelier_upload_request_payload_invalid"
    | "atelier_upload_request_id_invalid";

const REQUEST_PREFIX = "lumenx.atelierUpload.request.v1:";
const CLIENT_REQUEST_ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const MEDIA_KINDS = new Set<AtelierMediaKind>(["image", "audio", "video"]);
const OPERATIONS = new Set<AtelierUploadOperation>(["create_media_node", "upload_reference_attach"]);

export class AtelierUploadRequestError extends Error {
    code: AtelierUploadRequestErrorCode;

    constructor(code: AtelierUploadRequestErrorCode) {
        super(code);
        this.name = "AtelierUploadRequestError";
        this.code = code;
    }
}

export function beginAtelierUploadRequest(options: BeginAtelierUploadRequestOptions): AtelierUploadRequestResult {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const existing = listStoredForOwner(storage, owner)[0];
    if (existing) return { status: "pending", request: existing };
    const request: StoredAtelierUploadRequest = {
        schema_version: 1,
        ...owner,
        clientRequestId: validateRequestId(options.idFactory ? options.idFactory() : createAtelierUploadRequestId()),
        operation: options.operation,
        targetNodeId: options.targetNodeId,
        mediaKind: options.mediaKind,
        fileName: options.fileName,
        fileSize: options.fileSize,
        baseRevision: options.baseRevision,
    };
    if (options.title !== undefined) request.title = options.title;
    if (options.x !== undefined) request.x = options.x;
    if (options.y !== undefined) request.y = options.y;
    if (options.width !== undefined) request.width = options.width;
    if (options.height !== undefined) request.height = options.height;
    const frozenRequest = freezeRequest(validateStoredRequest(request, owner));
    try {
        storage.setItem(requestStorageKey(owner, frozenRequest.clientRequestId), canonicalJson(frozenRequest));
    } catch {
        throw new AtelierUploadRequestError("atelier_upload_request_storage_unavailable");
    }
    return { status: "created", request: frozenRequest };
}

export function createAtelierUploadRequestId(): string {
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
    return `upload-${suffix}`;
}

export function readAtelierUploadRequest(options: AtelierUploadRequestOwner & { storage?: Storage }): StoredAtelierUploadRequest | null {
    const storage = resolveStorage(options.storage);
    return listStoredForOwner(storage, normalizeOwner(options))[0] ?? null;
}

export function listAtelierUploadRequests(options: AtelierUploadRequestOwner & { storage?: Storage }): StoredAtelierUploadRequest[] {
    const storage = resolveStorage(options.storage);
    return listStoredForOwner(storage, normalizeOwner(options));
}

export function resolveAtelierUploadRequest(options: AtelierUploadRequestOwner & { clientRequestId: string; storage?: Storage }): boolean {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateRequestId(options.clientRequestId);
    const existing = listStoredForOwner(storage, owner).find(item => item.clientRequestId === id);
    if (!existing) return false;
    try {
        storage.removeItem(requestStorageKey(owner, id));
    } catch {
        throw new AtelierUploadRequestError("atelier_upload_request_storage_unavailable");
    }
    return true;
}

export function atelierUploadProjectPendingKey(owner: AtelierUploadRequestOwner): string {
    return ownerRequestPrefix(normalizeOwner(owner));
}

interface NormalizedOwner {
    apiBase: string;
    storageId: string;
    projectId: string;
}

function normalizeOwner(owner: AtelierUploadRequestOwner): NormalizedOwner {
    const normalized = {
        apiBase: typeof owner.environment.apiUrl === "string" ? owner.environment.apiUrl : "",
        storageId: typeof owner.environment.storageId === "string" ? owner.environment.storageId : "",
        projectId: typeof owner.projectId === "string" ? owner.projectId : "",
    };
    if (!normalized.apiBase || !normalized.storageId || !normalized.projectId) {
        throw new AtelierUploadRequestError("atelier_upload_request_owner_invalid");
    }
    return normalized;
}

function requestStorageKey(owner: NormalizedOwner, clientRequestId: string): string {
    return `${ownerRequestPrefix(owner)}:${encodeURIComponent(validateRequestId(clientRequestId))}`;
}

function ownerRequestPrefix(owner: NormalizedOwner): string {
    return `${REQUEST_PREFIX}${encodeURIComponent(canonicalJson(owner))}`;
}

function listStoredForOwner(storage: Storage, owner: NormalizedOwner): StoredAtelierUploadRequest[] {
    const requests: StoredAtelierUploadRequest[] = [];
    const expected = { apiBase: owner.apiBase, projectId: owner.projectId, storageId: owner.storageId };
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
                throw new AtelierUploadRequestError("atelier_upload_request_store_corrupt");
            }
            if (!matchesOwner(parsed, expected)) {
                throw new AtelierUploadRequestError("atelier_upload_request_store_corrupt");
            }
            const request = freezeRequest(validateStoredRequest(parsed, owner));
            if (request.clientRequestId !== keyRequestId) {
                throw new AtelierUploadRequestError("atelier_upload_request_store_corrupt");
            }
            requests.push(request);
        }
    } catch (error) {
        if (error instanceof AtelierUploadRequestError) throw error;
        throw new AtelierUploadRequestError("atelier_upload_request_storage_unavailable");
    }
    return requests.sort((left, right) => left.clientRequestId.localeCompare(right.clientRequestId));
}

function matchesOwner(value: unknown, owner: NormalizedOwner): boolean {
    return !!value && typeof value === "object" && !Array.isArray(value)
        && (value as Partial<StoredAtelierUploadRequest>).apiBase === owner.apiBase
        && (value as Partial<StoredAtelierUploadRequest>).projectId === owner.projectId
        && (value as Partial<StoredAtelierUploadRequest>).storageId === owner.storageId;
}

function validateStoredRequest(value: unknown, owner: NormalizedOwner): StoredAtelierUploadRequest {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new AtelierUploadRequestError("atelier_upload_request_store_corrupt");
    const record = value as Partial<StoredAtelierUploadRequest>;
    if (record.schema_version !== 1 || record.apiBase !== owner.apiBase || record.storageId !== owner.storageId
        || record.projectId !== owner.projectId || !validateRequestId(record.clientRequestId ?? "")
        || !OPERATIONS.has(record.operation as AtelierUploadOperation)
        || (record.targetNodeId !== null && typeof record.targetNodeId !== "string")
        || !MEDIA_KINDS.has(record.mediaKind as AtelierMediaKind)
        || typeof record.fileName !== "string" || !record.fileName.trim()
        || typeof record.fileSize !== "number" || !Number.isSafeInteger(record.fileSize) || record.fileSize <= 0
        || typeof record.baseRevision !== "number" || !Number.isSafeInteger(record.baseRevision) || record.baseRevision < 0) {
        throw new AtelierUploadRequestError("atelier_upload_request_store_corrupt");
    }
    for (const field of ["x", "y", "width", "height"] as const) {
        const item = record[field];
        if (item !== undefined && (typeof item !== "number" || !Number.isFinite(item))) {
            throw new AtelierUploadRequestError("atelier_upload_request_store_corrupt");
        }
    }
    if (record.title !== undefined && typeof record.title !== "string") {
        throw new AtelierUploadRequestError("atelier_upload_request_store_corrupt");
    }
    const clientRequestId = validateRequestId(record.clientRequestId ?? "");
    const operation = record.operation as AtelierUploadOperation;
    const mediaKind = record.mediaKind as AtelierMediaKind;
    const targetNodeId = record.targetNodeId;
    const fileName = record.fileName;
    const fileSize = record.fileSize;
    const baseRevision = record.baseRevision;
    if (typeof fileName !== "string" || typeof fileSize !== "number" || typeof baseRevision !== "number"
        || (targetNodeId !== null && typeof targetNodeId !== "string")) {
        throw new AtelierUploadRequestError("atelier_upload_request_store_corrupt");
    }
    const output: StoredAtelierUploadRequest = {
        schema_version: 1,
        apiBase: record.apiBase,
        storageId: record.storageId,
        projectId: record.projectId,
        clientRequestId,
        operation,
        targetNodeId,
        mediaKind,
        fileName,
        fileSize,
        baseRevision,
    };
    if (record.title !== undefined) output.title = record.title;
    if (record.x !== undefined) output.x = record.x;
    if (record.y !== undefined) output.y = record.y;
    if (record.width !== undefined) output.width = record.width;
    if (record.height !== undefined) output.height = record.height;
    return output;
}

function validateRequestId(value: string): string {
    if (typeof value !== "string" || !CLIENT_REQUEST_ID_RE.test(value)) {
        throw new AtelierUploadRequestError("atelier_upload_request_id_invalid");
    }
    return value;
}

function freezeRequest(request: StoredAtelierUploadRequest): StoredAtelierUploadRequest {
    return Object.freeze({ ...request });
}

function resolveStorage(storage?: Storage): Storage {
    if (storage) return storage;
    try {
        if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
    } catch {
        throw new AtelierUploadRequestError("atelier_upload_request_storage_unavailable");
    }
    throw new AtelierUploadRequestError("atelier_upload_request_storage_unavailable");
}

function canonicalJson(value: unknown): string {
    return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(sortJson);
    if (!value || typeof value !== "object") return value;
    const output: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
        output[key] = sortJson((value as Record<string, unknown>)[key]);
    }
    return output;
}
