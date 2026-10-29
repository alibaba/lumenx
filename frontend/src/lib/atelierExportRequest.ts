import type { RuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { JsonValue } from "@/lib/atelierTypes";

export interface AtelierExportRequestOwner {
    environment: RuntimeEnvironment;
    projectId: string;
}

export interface AtelierExportOriginalRequest {
    expected_revision: number;
    client_request_id: string;
    entries?: JsonValue[];
    audio_tracks?: JsonValue[];
}
export type AtelierExportRequestOperation = "create" | "redo";

export interface BeginAtelierExportRequestOptions extends AtelierExportRequestOwner {
    expectedRevision: number;
    entries?: readonly unknown[];
    audioTracks?: readonly unknown[];
    sourceExportId?: string | null;
    storage?: Storage;
    idFactory?: () => string;
}

export interface StoredAtelierExportRequest {
    schema_version: 1;
    apiBase: string;
    storageId: string;
    projectId: string;
    clientRequestId: string;
    operation: AtelierExportRequestOperation;
    sourceExportId: string | null;
    request: AtelierExportOriginalRequest;
}

export type AtelierExportRequestResult = {
    status: "created" | "pending";
    request: StoredAtelierExportRequest;
};

export type AtelierExportRequestErrorCode =
    | "atelier_export_request_storage_unavailable"
    | "atelier_export_request_store_corrupt"
    | "atelier_export_request_owner_invalid"
    | "atelier_export_request_payload_invalid"
    | "atelier_export_request_id_invalid";

const REQUEST_PREFIX = "lumenx.atelierExport.request.v1:";
const CLIENT_REQUEST_ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;

export class AtelierExportRequestError extends Error {
    code: AtelierExportRequestErrorCode;

    constructor(code: AtelierExportRequestErrorCode) {
        super(code);
        this.name = "AtelierExportRequestError";
        this.code = code;
    }
}

export function beginAtelierExportRequest(options: BeginAtelierExportRequestOptions): AtelierExportRequestResult {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const clientRequestId = validateRequestId(options.idFactory ? options.idFactory() : createAtelierExportRequestId());
    const request = buildOriginalRequest(options, clientRequestId);
    const existing = listStoredForOwner(storage, owner).find(item => item.clientRequestId === clientRequestId);
    if (existing) {
        const operation = options.sourceExportId === undefined || options.sourceExportId === null ? "create" : "redo";
        const sourceExportId = operation === "redo" ? validateSourceExportId(options.sourceExportId) : null;
        if (existing.operation !== operation || existing.sourceExportId !== sourceExportId || canonicalJson(existing.request) !== canonicalJson(request)) {
            throw new AtelierExportRequestError("atelier_export_request_store_corrupt");
        }
        return { status: "pending", request: existing };
    }

    const record = freezeStoredRequest(validateStoredRequest({
        schema_version: 1,
        ...owner,
        clientRequestId,
        operation: options.sourceExportId === undefined || options.sourceExportId === null ? "create" : "redo",
        sourceExportId: options.sourceExportId ?? null,
        request,
    }, owner));
    try {
        storage.setItem(requestStorageKey(owner, clientRequestId), canonicalJson(record));
    } catch {
        throw new AtelierExportRequestError("atelier_export_request_storage_unavailable");
    }
    return { status: "created", request: record };
}

export function createAtelierExportRequestId(): string {
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
    return `export-${suffix}`;
}

export function readAtelierExportRequest(
    options: AtelierExportRequestOwner & { clientRequestId: string; storage?: Storage },
): StoredAtelierExportRequest | null {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateRequestId(options.clientRequestId);
    return listStoredForOwner(storage, owner).find(item => item.clientRequestId === id) ?? null;
}

export function listAtelierExportRequests(options: AtelierExportRequestOwner & { storage?: Storage }): StoredAtelierExportRequest[] {
    const storage = resolveStorage(options.storage);
    return listStoredForOwner(storage, normalizeOwner(options));
}

export function resolveAtelierExportRequest(
    options: AtelierExportRequestOwner & {
        clientRequestId: string;
        request: AtelierExportOriginalRequest;
        operation?: AtelierExportRequestOperation;
        sourceExportId?: string | null;
        storage?: Storage;
    },
): boolean {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateRequestId(options.clientRequestId);
    const requestId = requestClientRequestId(options.request);
    const expectedRequest = freezeOriginalRequest(validateOriginalRequest(options.request, requestId));
    const operation = options.operation ?? "create";
    const sourceExportId = operation === "redo" ? validateSourceExportId(options.sourceExportId) : null;
    const existing = listStoredForOwner(storage, owner).find(item => item.clientRequestId === id);
    if (!existing || requestId !== id) return false;
    if (existing.operation !== operation || existing.sourceExportId !== sourceExportId) return false;
    if (canonicalJson(existing.request) !== canonicalJson(expectedRequest)) return false;
    try {
        storage.removeItem(requestStorageKey(owner, id));
    } catch {
        throw new AtelierExportRequestError("atelier_export_request_storage_unavailable");
    }
    return true;
}

export function atelierExportProjectPendingKey(owner: AtelierExportRequestOwner): string {
    return ownerRequestPrefix(normalizeOwner(owner));
}

interface NormalizedOwner {
    apiBase: string;
    storageId: string;
    projectId: string;
}

function normalizeOwner(owner: AtelierExportRequestOwner): NormalizedOwner {
    const normalized = {
        apiBase: typeof owner.environment.apiUrl === "string" ? owner.environment.apiUrl : "",
        storageId: typeof owner.environment.storageId === "string" ? owner.environment.storageId : "",
        projectId: typeof owner.projectId === "string" ? owner.projectId : "",
    };
    if (!normalized.apiBase || !normalized.storageId || !normalized.projectId) {
        throw new AtelierExportRequestError("atelier_export_request_owner_invalid");
    }
    return normalized;
}

function buildOriginalRequest(options: BeginAtelierExportRequestOptions, clientRequestId: string): AtelierExportOriginalRequest {
    if (!Number.isSafeInteger(options.expectedRevision) || options.expectedRevision < 0) {
        throw new AtelierExportRequestError("atelier_export_request_payload_invalid");
    }
    const request: AtelierExportOriginalRequest = {
        expected_revision: options.expectedRevision,
        client_request_id: clientRequestId,
    };
    if (options.sourceExportId !== undefined && options.sourceExportId !== null) {
        if (Object.prototype.hasOwnProperty.call(options, "entries") || Object.prototype.hasOwnProperty.call(options, "audioTracks")) {
            throw new AtelierExportRequestError("atelier_export_request_payload_invalid");
        }
    }
    if (Object.prototype.hasOwnProperty.call(options, "entries")) {
        request.entries = validateJsonArray(options.entries);
    }
    if (Object.prototype.hasOwnProperty.call(options, "audioTracks")) {
        request.audio_tracks = validateJsonArray(options.audioTracks);
    }
    return freezeOriginalRequest(request);
}

function requestStorageKey(owner: NormalizedOwner, clientRequestId: string): string {
    return `${ownerRequestPrefix(owner)}:${encodeURIComponent(validateRequestId(clientRequestId))}`;
}

function ownerRequestPrefix(owner: NormalizedOwner): string {
    return `${REQUEST_PREFIX}${encodeURIComponent(canonicalJson(owner))}`;
}

function listStoredForOwner(storage: Storage, owner: NormalizedOwner): StoredAtelierExportRequest[] {
    const requests: StoredAtelierExportRequest[] = [];
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
                throw new AtelierExportRequestError("atelier_export_request_store_corrupt");
            }
            if (!matchesOwner(parsed, owner)) {
                throw new AtelierExportRequestError("atelier_export_request_store_corrupt");
            }
            const request = freezeStoredRequest(validateStoredRequest(parsed, owner));
            if (request.clientRequestId !== keyRequestId) {
                throw new AtelierExportRequestError("atelier_export_request_store_corrupt");
            }
            requests.push(request);
        }
    } catch (error) {
        if (error instanceof AtelierExportRequestError) throw error;
        throw new AtelierExportRequestError("atelier_export_request_storage_unavailable");
    }
    return requests.sort((left, right) => left.clientRequestId.localeCompare(right.clientRequestId));
}

function matchesOwner(value: unknown, owner: NormalizedOwner): boolean {
    return !!value && typeof value === "object" && !Array.isArray(value)
        && (value as Partial<StoredAtelierExportRequest>).apiBase === owner.apiBase
        && (value as Partial<StoredAtelierExportRequest>).projectId === owner.projectId
        && (value as Partial<StoredAtelierExportRequest>).storageId === owner.storageId;
}

function validateStoredRequest(value: unknown, owner: NormalizedOwner): StoredAtelierExportRequest {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new AtelierExportRequestError("atelier_export_request_store_corrupt");
    const record = value as Partial<StoredAtelierExportRequest>;
    if (record.schema_version !== 1 || record.apiBase !== owner.apiBase || record.storageId !== owner.storageId
        || record.projectId !== owner.projectId || typeof record.clientRequestId !== "string") {
        throw new AtelierExportRequestError("atelier_export_request_store_corrupt");
    }
    const clientRequestId = validateRequestId(record.clientRequestId);
    const operation = record.operation === undefined ? "create" : validateOperation(record.operation);
    const sourceExportId = operation === "redo" ? validateSourceExportId(record.sourceExportId) : null;
    if (operation === "create" && record.sourceExportId !== undefined && record.sourceExportId !== null) {
        throw new AtelierExportRequestError("atelier_export_request_store_corrupt");
    }
    const request = freezeOriginalRequest(validateOriginalRequest(record.request, clientRequestId, "atelier_export_request_store_corrupt"));
    return {
        schema_version: 1,
        apiBase: record.apiBase,
        storageId: record.storageId,
        projectId: record.projectId,
        clientRequestId,
        operation,
        sourceExportId,
        request,
    };
}

function requestClientRequestId(value: unknown): string {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new AtelierExportRequestError("atelier_export_request_payload_invalid");
    }
    const requestId = (value as Partial<AtelierExportOriginalRequest>).client_request_id;
    if (typeof requestId !== "string") {
        throw new AtelierExportRequestError("atelier_export_request_payload_invalid");
    }
    return validateRequestId(requestId);
}

function validateOriginalRequest(
    value: unknown,
    clientRequestId: string,
    errorCode: AtelierExportRequestErrorCode = "atelier_export_request_payload_invalid",
): AtelierExportOriginalRequest {
    const fail = () => { throw new AtelierExportRequestError(errorCode); };
    if (!value || typeof value !== "object" || Array.isArray(value)) fail();
    const record = value as Partial<AtelierExportOriginalRequest>;
    if (!Number.isSafeInteger(record.expected_revision) || (record.expected_revision as number) < 0
        || record.client_request_id !== clientRequestId) {
        fail();
    }
    const request: AtelierExportOriginalRequest = {
        expected_revision: record.expected_revision as number,
        client_request_id: clientRequestId,
    };
    if (Object.prototype.hasOwnProperty.call(record, "entries")) {
        request.entries = validateJsonArray(record.entries, errorCode);
    }
    if (Object.prototype.hasOwnProperty.call(record, "audio_tracks")) {
        request.audio_tracks = validateJsonArray(record.audio_tracks, errorCode);
    }
    return request;
}

function validateJsonArray(value: unknown, errorCode: AtelierExportRequestErrorCode = "atelier_export_request_payload_invalid"): JsonValue[] {
    if (!Array.isArray(value)) throw new AtelierExportRequestError(errorCode);
    return strictJsonValue(value, new WeakSet(), errorCode) as JsonValue[];
}

function validateRequestId(value: string): string {
    if (typeof value !== "string" || !CLIENT_REQUEST_ID_RE.test(value)) {
        throw new AtelierExportRequestError("atelier_export_request_id_invalid");
    }
    return value;
}

function validateOperation(value: unknown): AtelierExportRequestOperation {
    if (value !== "create" && value !== "redo") {
        throw new AtelierExportRequestError("atelier_export_request_store_corrupt");
    }
    return value;
}

function validateSourceExportId(value: unknown): string {
    if (typeof value !== "string" || !CLIENT_REQUEST_ID_RE.test(value)) {
        throw new AtelierExportRequestError("atelier_export_request_payload_invalid");
    }
    return value;
}

function resolveStorage(storage?: Storage): Storage {
    if (storage) return storage;
    try {
        if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
    } catch {
        throw new AtelierExportRequestError("atelier_export_request_storage_unavailable");
    }
    throw new AtelierExportRequestError("atelier_export_request_storage_unavailable");
}

function canonicalJson(value: unknown): string {
    return JSON.stringify(sortJson(strictJsonValue(value, new WeakSet())));
}

function strictJsonValue(
    value: unknown,
    seen: WeakSet<object>,
    errorCode: AtelierExportRequestErrorCode = "atelier_export_request_payload_invalid",
): unknown {
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new AtelierExportRequestError(errorCode);
        return value;
    }
    if (Array.isArray(value)) {
        rejectCycle(value, seen, errorCode);
        const output: unknown[] = [];
        for (let index = 0; index < value.length; index += 1) {
            if (!Object.prototype.hasOwnProperty.call(value, index)) {
                throw new AtelierExportRequestError(errorCode);
            }
            output.push(strictJsonValue(value[index], seen, errorCode));
        }
        seen.delete(value);
        return output;
    }
    if (value && typeof value === "object") {
        const proto = Object.getPrototypeOf(value);
        if (proto !== Object.prototype && proto !== null) {
            throw new AtelierExportRequestError(errorCode);
        }
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
    throw new AtelierExportRequestError(errorCode);
}

function rejectCycle(value: object, seen: WeakSet<object>, errorCode: AtelierExportRequestErrorCode): void {
    if (seen.has(value)) {
        throw new AtelierExportRequestError(errorCode);
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

function freezeOriginalRequest(request: AtelierExportOriginalRequest): AtelierExportOriginalRequest {
    return deepFreeze({ ...request });
}

function freezeStoredRequest(request: StoredAtelierExportRequest): StoredAtelierExportRequest {
    return deepFreeze({ ...request, request: { ...request.request } });
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
