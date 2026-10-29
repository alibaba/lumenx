import type { RuntimeEnvironment } from "@/lib/runtimeEnvironment";

export interface AtelierExportDeletionRequestOwner {
    environment: RuntimeEnvironment;
    projectId: string;
}

export interface AtelierExportDeletionRequest {
    expected_revision: number;
    client_request_id: string;
    delete_file: boolean;
}

export interface BeginAtelierExportDeletionRequestOptions extends AtelierExportDeletionRequestOwner {
    exportId: string;
    expectedRevision: number;
    deleteFile?: boolean;
    storage?: Storage;
    idFactory?: () => string;
}

export interface StoredAtelierExportDeletionRequest {
    schema_version: 1;
    apiBase: string;
    storageId: string;
    projectId: string;
    exportId: string;
    clientRequestId: string;
    request: AtelierExportDeletionRequest;
}

export type AtelierExportDeletionRequestResult = {
    status: "created" | "pending";
    request: StoredAtelierExportDeletionRequest;
};

export type AtelierExportDeletionRequestErrorCode =
    | "atelier_export_deletion_request_storage_unavailable"
    | "atelier_export_deletion_request_store_corrupt"
    | "atelier_export_deletion_request_owner_invalid"
    | "atelier_export_deletion_request_payload_invalid"
    | "atelier_export_deletion_request_id_invalid";

const REQUEST_PREFIX = "lumenx.atelierExportDeletion.request.v1:";
const ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;

export class AtelierExportDeletionRequestError extends Error {
    code: AtelierExportDeletionRequestErrorCode;

    constructor(code: AtelierExportDeletionRequestErrorCode) {
        super(code);
        this.name = "AtelierExportDeletionRequestError";
        this.code = code;
    }
}

export function beginAtelierExportDeletionRequest(options: BeginAtelierExportDeletionRequestOptions): AtelierExportDeletionRequestResult {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const exportId = validateId(options.exportId, "atelier_export_deletion_request_payload_invalid");
    const clientRequestId = validateId(options.idFactory ? options.idFactory() : createAtelierExportDeletionRequestId(), "atelier_export_deletion_request_id_invalid");
    const request = buildRequest(options, clientRequestId);
    const existing = listStoredForOwner(storage, owner).find(item => item.clientRequestId === clientRequestId);
    if (existing) {
        if (existing.exportId !== exportId || canonicalJson(existing.request) !== canonicalJson(request)) {
            throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_store_corrupt");
        }
        return { status: "pending", request: existing };
    }

    const record = freezeStoredRequest(validateStoredRequest({
        schema_version: 1,
        ...owner,
        exportId,
        clientRequestId,
        request,
    }, owner));
    try {
        storage.setItem(requestStorageKey(owner, clientRequestId), canonicalJson(record));
    } catch {
        throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_storage_unavailable");
    }
    return { status: "created", request: record };
}

export function createAtelierExportDeletionRequestId(): string {
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
    return `export-delete-${suffix}`;
}

export function readAtelierExportDeletionRequest(
    options: AtelierExportDeletionRequestOwner & { clientRequestId: string; storage?: Storage },
): StoredAtelierExportDeletionRequest | null {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateId(options.clientRequestId, "atelier_export_deletion_request_id_invalid");
    return listStoredForOwner(storage, owner).find(item => item.clientRequestId === id) ?? null;
}

export function listAtelierExportDeletionRequests(options: AtelierExportDeletionRequestOwner & { storage?: Storage }): StoredAtelierExportDeletionRequest[] {
    const storage = resolveStorage(options.storage);
    return listStoredForOwner(storage, normalizeOwner(options));
}

export function resolveAtelierExportDeletionRequest(
    options: AtelierExportDeletionRequestOwner & {
        exportId: string;
        clientRequestId: string;
        request: AtelierExportDeletionRequest;
        storage?: Storage;
    },
): boolean {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const clientRequestId = validateId(options.clientRequestId, "atelier_export_deletion_request_id_invalid");
    const exportId = validateId(options.exportId, "atelier_export_deletion_request_payload_invalid");
    const request = freezeRequest(validateRequest(options.request, clientRequestId));
    const existing = listStoredForOwner(storage, owner).find(item => item.clientRequestId === clientRequestId);
    if (!existing || existing.exportId !== exportId || canonicalJson(existing.request) !== canonicalJson(request)) return false;
    try {
        storage.removeItem(requestStorageKey(owner, clientRequestId));
    } catch {
        throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_storage_unavailable");
    }
    return true;
}

interface NormalizedOwner {
    apiBase: string;
    storageId: string;
    projectId: string;
}

function normalizeOwner(owner: AtelierExportDeletionRequestOwner): NormalizedOwner {
    const normalized = {
        apiBase: typeof owner.environment.apiUrl === "string" ? owner.environment.apiUrl : "",
        storageId: typeof owner.environment.storageId === "string" ? owner.environment.storageId : "",
        projectId: typeof owner.projectId === "string" ? owner.projectId : "",
    };
    if (!normalized.apiBase || !normalized.storageId || !normalized.projectId) {
        throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_owner_invalid");
    }
    return normalized;
}

function buildRequest(options: BeginAtelierExportDeletionRequestOptions, clientRequestId: string): AtelierExportDeletionRequest {
    return freezeRequest(validateRequest({
        expected_revision: options.expectedRevision,
        client_request_id: clientRequestId,
        delete_file: options.deleteFile ?? true,
    }, clientRequestId));
}

function validateRequest(value: unknown, clientRequestId: string): AtelierExportDeletionRequest {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_payload_invalid");
    const record = value as Partial<AtelierExportDeletionRequest>;
    if (!Number.isSafeInteger(record.expected_revision) || (record.expected_revision as number) < 0
        || record.client_request_id !== clientRequestId || typeof record.delete_file !== "boolean") {
        throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_payload_invalid");
    }
    return {
        expected_revision: record.expected_revision as number,
        client_request_id: clientRequestId,
        delete_file: record.delete_file,
    };
}

function validateStoredRequest(value: unknown, owner: NormalizedOwner): StoredAtelierExportDeletionRequest {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_store_corrupt");
    const record = value as Partial<StoredAtelierExportDeletionRequest>;
    if (record.schema_version !== 1 || record.apiBase !== owner.apiBase || record.storageId !== owner.storageId
        || record.projectId !== owner.projectId || typeof record.clientRequestId !== "string") {
        throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_store_corrupt");
    }
    const clientRequestId = validateId(record.clientRequestId, "atelier_export_deletion_request_store_corrupt");
    return {
        schema_version: 1,
        apiBase: record.apiBase,
        storageId: record.storageId,
        projectId: record.projectId,
        exportId: validateId(record.exportId, "atelier_export_deletion_request_store_corrupt"),
        clientRequestId,
        request: freezeRequest(validateRequest(record.request, clientRequestId)),
    };
}

function requestStorageKey(owner: NormalizedOwner, clientRequestId: string): string {
    return `${ownerRequestPrefix(owner)}:${encodeURIComponent(validateId(clientRequestId, "atelier_export_deletion_request_id_invalid"))}`;
}

function ownerRequestPrefix(owner: NormalizedOwner): string {
    return `${REQUEST_PREFIX}${encodeURIComponent(canonicalJson(owner))}`;
}

function listStoredForOwner(storage: Storage, owner: NormalizedOwner): StoredAtelierExportDeletionRequest[] {
    const requests: StoredAtelierExportDeletionRequest[] = [];
    const prefix = `${ownerRequestPrefix(owner)}:`;
    try {
        for (let index = 0; index < storage.length; index += 1) {
            const key = storage.key(index);
            if (!key?.startsWith(prefix)) continue;
            const keyRequestId = validateId(decodeURIComponent(key.slice(prefix.length)), "atelier_export_deletion_request_store_corrupt");
            const raw = storage.getItem(key);
            if (raw === null) continue;
            let parsed: unknown;
            try {
                parsed = JSON.parse(raw);
            } catch {
                throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_store_corrupt");
            }
            if (!matchesOwner(parsed, owner)) {
                throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_store_corrupt");
            }
            const request = freezeStoredRequest(validateStoredRequest(parsed, owner));
            if (request.clientRequestId !== keyRequestId) {
                throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_store_corrupt");
            }
            requests.push(request);
        }
    } catch (error) {
        if (error instanceof AtelierExportDeletionRequestError) throw error;
        throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_storage_unavailable");
    }
    return requests.sort((left, right) => left.clientRequestId.localeCompare(right.clientRequestId));
}

function matchesOwner(value: unknown, owner: NormalizedOwner): boolean {
    return !!value && typeof value === "object" && !Array.isArray(value)
        && (value as Partial<StoredAtelierExportDeletionRequest>).apiBase === owner.apiBase
        && (value as Partial<StoredAtelierExportDeletionRequest>).projectId === owner.projectId
        && (value as Partial<StoredAtelierExportDeletionRequest>).storageId === owner.storageId;
}

function validateId(value: unknown, code: AtelierExportDeletionRequestErrorCode): string {
    if (typeof value !== "string" || !ID_RE.test(value)) {
        throw new AtelierExportDeletionRequestError(code);
    }
    return value;
}

function resolveStorage(storage?: Storage): Storage {
    if (storage) return storage;
    try {
        if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
    } catch {
        throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_storage_unavailable");
    }
    throw new AtelierExportDeletionRequestError("atelier_export_deletion_request_storage_unavailable");
}

function canonicalJson(value: unknown): string {
    return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(sortJson);
    if (value && typeof value === "object") {
        const output: Record<string, unknown> = Object.create(null);
        for (const key of Object.keys(value as Record<string, unknown>).sort()) {
            Object.defineProperty(output, key, { value: sortJson((value as Record<string, unknown>)[key]), enumerable: true });
        }
        return output;
    }
    return value;
}

function freezeRequest(request: AtelierExportDeletionRequest): AtelierExportDeletionRequest {
    return Object.freeze({ ...request });
}

function freezeStoredRequest(request: StoredAtelierExportDeletionRequest): StoredAtelierExportDeletionRequest {
    return Object.freeze({ ...request, request: freezeRequest(request.request) });
}
