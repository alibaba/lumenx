export interface ProviderVideoRequestOwner {
    apiBase: string;
    storageId: string;
    projectId: string;
    frameId: string;
}

export type ProviderVideoRequestPayload = Record<string, unknown> & {
    client_request_id: string;
    batch_size: number;
};

export type ProviderVideoRequestStatus = "created" | "pending";

export interface ProviderVideoRequestResult {
    status: ProviderVideoRequestStatus;
    clientRequestId: string;
    payload: ProviderVideoRequestPayload;
    storageKey: string;
}

export interface ProviderVideoRequestOptions {
    owner: ProviderVideoRequestOwner;
    input: Record<string, unknown>;
    batchSize: number;
    storage?: Storage;
    idFactory?: () => string;
}

export interface ResolveProviderVideoRequestOptions {
    owner: ProviderVideoRequestOwner;
    clientRequestId: string;
    storage?: Storage;
}

export interface ReadProviderVideoRequestOptions {
    owner: ProviderVideoRequestOwner;
    storage?: Storage;
}

type WebLockGrant = object | null;
type WebLocksLike = {
    request: <T>(name: string, options: { mode: "exclusive"; ifAvailable: true }, callback: (lock: WebLockGrant) => T | Promise<T>) => Promise<T>;
};

export type ProviderVideoRequestErrorCode =
    | "provider_video_request_storage_unavailable"
    | "provider_video_request_store_corrupt"
    | "provider_video_request_owner_invalid"
    | "provider_video_request_payload_invalid"
    | "provider_video_request_batch_invalid"
    | "provider_video_request_id_invalid"
    | "provider_video_request_secret_field"
    | "provider_video_request_lock_unavailable";

const STORAGE_PREFIX = "lumenx.providerVideoRequest.v1:";
const CLIENT_REQUEST_ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const SECRET_KEY_RE = /(?:^|[_-])(api[_-]?key|access[_-]?key|secret|token|password|credential)(?:$|[_-])/i;

export class ProviderVideoRequestError extends Error {
    code: ProviderVideoRequestErrorCode;

    constructor(code: ProviderVideoRequestErrorCode) {
        super(code);
        this.name = "ProviderVideoRequestError";
        this.code = code;
    }
}

interface StoredProviderVideoRequest {
    schema_version: 1;
    owner: CanonicalOwner;
    client_request_id: string;
    payload: ProviderVideoRequestPayload;
}

type CanonicalOwner = Required<ProviderVideoRequestOwner>;

export function beginProviderVideoRequest(options: ProviderVideoRequestOptions): ProviderVideoRequestResult {
    const storage = resolveStorage(options.storage);
    const owner = canonicalOwner(options.owner);
    const storageKey = ownerStorageKey(owner);
    const existing = readStored(storage, storageKey, owner);
    if (existing) {
        return resultFromStored("pending", storageKey, existing);
    }

    const clientRequestId = validateClientRequestId(options.idFactory ? options.idFactory() : defaultIdFactory());
    const payload = freezeJsonPayload({
        ...validateInput(options.input),
        batch_size: validateBatchSize(options.batchSize),
        client_request_id: clientRequestId,
    });
    const record: StoredProviderVideoRequest = deepFreeze({
        schema_version: 1,
        owner,
        client_request_id: clientRequestId,
        payload,
    });
    writeStored(storage, storageKey, record);
    return resultFromStored("created", storageKey, record);
}

export function readProviderVideoRequest(options: ReadProviderVideoRequestOptions): ProviderVideoRequestResult | null {
    const storage = resolveStorage(options.storage);
    const owner = canonicalOwner(options.owner);
    const storageKey = ownerStorageKey(owner);
    const existing = readStored(storage, storageKey, owner);
    return existing ? resultFromStored("pending", storageKey, existing) : null;
}

export function resolveProviderVideoRequest(options: ResolveProviderVideoRequestOptions): boolean {
    const storage = resolveStorage(options.storage);
    const owner = canonicalOwner(options.owner);
    const storageKey = ownerStorageKey(owner);
    const existing = readStored(storage, storageKey, owner);
    if (!existing) return false;
    if (existing.client_request_id !== options.clientRequestId) return false;
    try {
        storage.removeItem(storageKey);
    } catch {
        throw new ProviderVideoRequestError("provider_video_request_storage_unavailable");
    }
    return true;
}

export function providerVideoRequestStorageKey(owner: ProviderVideoRequestOwner): string {
    return ownerStorageKey(canonicalOwner(owner));
}

export async function withProviderVideoRequestLock<T>(
    owner: ProviderVideoRequestOwner,
    callback: () => T | Promise<T>,
): Promise<T> {
    const locks = resolveWebLocks();
    const lockName = providerVideoRequestLockName(owner);
    return locks.request(lockName, { mode: "exclusive", ifAvailable: true }, lock => {
        if (!lock) throw new ProviderVideoRequestError("provider_video_request_lock_unavailable");
        return callback();
    });
}

export function providerVideoRequestLockName(owner: ProviderVideoRequestOwner): string {
    return `${STORAGE_PREFIX}lock:${encodeURIComponent(providerVideoRequestStorageKey(owner))}`;
}

function resultFromStored(
    status: ProviderVideoRequestStatus,
    storageKey: string,
    record: StoredProviderVideoRequest,
): ProviderVideoRequestResult {
    return {
        status,
        clientRequestId: record.client_request_id,
        payload: deepFreeze(jsonClone(record.payload)) as ProviderVideoRequestPayload,
        storageKey,
    };
}

function canonicalOwner(owner: ProviderVideoRequestOwner): CanonicalOwner {
    const normalized = {
        apiBase: normalizeString(owner.apiBase),
        frameId: normalizeString(owner.frameId),
        projectId: normalizeString(owner.projectId),
        storageId: normalizeString(owner.storageId),
    };
    if (!normalized.apiBase || !normalized.storageId || !normalized.projectId || !normalized.frameId) {
        throw new ProviderVideoRequestError("provider_video_request_owner_invalid");
    }
    return normalized;
}

function ownerStorageKey(owner: CanonicalOwner): string {
    return `${STORAGE_PREFIX}${encodeURIComponent(canonicalJson(owner))}`;
}

function validateInput(input: Record<string, unknown>): Record<string, unknown> {
    if (!input || typeof input !== "object" || Array.isArray(input)) {
        throw new ProviderVideoRequestError("provider_video_request_payload_invalid");
    }
    const cloned = jsonClone(input);
    if (!cloned || typeof cloned !== "object" || Array.isArray(cloned)) {
        throw new ProviderVideoRequestError("provider_video_request_payload_invalid");
    }
    rejectSecretKeys(cloned, "payload");
    return cloned as Record<string, unknown>;
}

function validateBatchSize(value: number): number {
    if (!Number.isInteger(value) || value < 1 || value > 6) {
        throw new ProviderVideoRequestError("provider_video_request_batch_invalid");
    }
    return value;
}

function validateClientRequestId(value: string): string {
    if (typeof value !== "string" || !CLIENT_REQUEST_ID_RE.test(value)) {
        throw new ProviderVideoRequestError("provider_video_request_id_invalid");
    }
    return value;
}


function resolveWebLocks(): WebLocksLike {
    const maybeNavigator = typeof navigator !== "undefined" ? navigator as Navigator & { locks?: WebLocksLike } : null;
    if (maybeNavigator?.locks && typeof maybeNavigator.locks.request === "function") {
        return maybeNavigator.locks;
    }
    throw new ProviderVideoRequestError("provider_video_request_lock_unavailable");
}

function resolveStorage(storage?: Storage): Storage {
    if (storage) return storage;
    try {
        if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
    } catch {
        throw new ProviderVideoRequestError("provider_video_request_storage_unavailable");
    }
    throw new ProviderVideoRequestError("provider_video_request_storage_unavailable");
}

function readStored(storage: Storage, storageKey: string, owner: CanonicalOwner): StoredProviderVideoRequest | null {
    let raw: string | null;
    try {
        raw = storage.getItem(storageKey);
    } catch {
        throw new ProviderVideoRequestError("provider_video_request_storage_unavailable");
    }
    if (raw === null) return null;
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {
        throw new ProviderVideoRequestError("provider_video_request_store_corrupt");
    }
    try {
        if (!isStoredRecord(parsed) || canonicalJson(parsed.owner) !== canonicalJson(owner)) {
            throw new ProviderVideoRequestError("provider_video_request_store_corrupt");
        }
        rejectSecretKeys(parsed.payload, "payload");
        return deepFreeze(jsonClone(parsed)) as StoredProviderVideoRequest;
    } catch (error) {
        if (error instanceof ProviderVideoRequestError && error.code === "provider_video_request_store_corrupt") {
            throw error;
        }
        throw new ProviderVideoRequestError("provider_video_request_store_corrupt");
    }
}

function writeStored(storage: Storage, storageKey: string, record: StoredProviderVideoRequest): void {
    try {
        storage.setItem(storageKey, canonicalJson(record));
    } catch {
        throw new ProviderVideoRequestError("provider_video_request_storage_unavailable");
    }
}

function isStoredRecord(value: unknown): value is StoredProviderVideoRequest {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const record = value as Partial<StoredProviderVideoRequest>;
    if (record.schema_version !== 1) return false;
    if (!record.owner || typeof record.owner !== "object" || Array.isArray(record.owner)) return false;
    const candidateOwner = record.owner as Partial<CanonicalOwner>;
    if (![candidateOwner.apiBase, candidateOwner.frameId, candidateOwner.projectId, candidateOwner.storageId].every(item => typeof item === "string")) return false;
    if (!record.payload || typeof record.payload !== "object" || Array.isArray(record.payload)) return false;
    if (typeof record.client_request_id !== "string" || !CLIENT_REQUEST_ID_RE.test(record.client_request_id)) return false;
    const payload = record.payload as Partial<ProviderVideoRequestPayload>;
    if (payload.client_request_id !== record.client_request_id) return false;
    if (!Number.isInteger(payload.batch_size) || (payload.batch_size as number) < 1 || (payload.batch_size as number) > 6) return false;
    return true;
}

function rejectSecretKeys(value: unknown, path: string): void {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) {
        value.forEach((item, index) => rejectSecretKeys(item, `${path}[${index}]`));
        return;
    }
    for (const [key, item] of Object.entries(value)) {
        if (SECRET_KEY_RE.test(key)) {
            throw new ProviderVideoRequestError("provider_video_request_secret_field");
        }
        rejectSecretKeys(item, `${path}.${key}`);
    }
}

function defaultIdFactory(): string {
    const cryptoRef = globalThis.crypto;
    if (cryptoRef && typeof cryptoRef.randomUUID === "function") return cryptoRef.randomUUID();
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function normalizeString(value: string): string {
    return typeof value === "string" ? value : "";
}

function freezeJsonPayload(value: Record<string, unknown>): ProviderVideoRequestPayload {
    return deepFreeze(jsonClone(value)) as ProviderVideoRequestPayload;
}

function jsonClone<T>(value: T): T {
    return strictJsonValue(value, new WeakSet()) as T;
}

function canonicalJson(value: unknown): string {
    return JSON.stringify(sortJson(strictJsonValue(value, new WeakSet())));
}

function strictJsonValue(value: unknown, seen: WeakSet<object>): unknown {
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new ProviderVideoRequestError("provider_video_request_payload_invalid");
        return value;
    }
    if (Array.isArray(value)) {
        rejectCycle(value, seen);
        const output: unknown[] = [];
        for (let index = 0; index < value.length; index += 1) {
            if (!Object.prototype.hasOwnProperty.call(value, index)) {
                throw new ProviderVideoRequestError("provider_video_request_payload_invalid");
            }
            output.push(strictJsonValue(value[index], seen));
        }
        seen.delete(value);
        return output;
    }
    if (value && typeof value === "object") {
        const proto = Object.getPrototypeOf(value);
        if (proto !== Object.prototype && proto !== null) {
            throw new ProviderVideoRequestError("provider_video_request_payload_invalid");
        }
        rejectCycle(value, seen);
        const output: Record<string, unknown> = Object.create(null);
        for (const [key, item] of Object.entries(value)) {
            Object.defineProperty(output, key, {
                value: strictJsonValue(item, seen),
                enumerable: true,
                configurable: true,
                writable: true,
            });
        }
        seen.delete(value);
        return output;
    }
    throw new ProviderVideoRequestError("provider_video_request_payload_invalid");
}

function rejectCycle(value: object, seen: WeakSet<object>): void {
    if (seen.has(value)) {
        throw new ProviderVideoRequestError("provider_video_request_payload_invalid");
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

function deepFreeze<T>(value: T): T {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
        Object.freeze(value);
        for (const item of Object.values(value as Record<string, unknown>)) {
            deepFreeze(item);
        }
    }
    return value;
}
