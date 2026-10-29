import type { RuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { StartAtelierAgentTurnRequest } from "@/lib/atelierAgentApi";
import type { AtelierAgentLoopPlanner, StartAtelierAgentLoopRequest } from "@/lib/atelierAgentLoopApi";

export interface AtelierAgentTurnRequestOwner {
    environment: RuntimeEnvironment;
    projectId: string;
}

export type AtelierAgentTurnRequestEntrypoint = "single_turn" | "agent_loop";

export interface StoredAtelierAgentSingleTurnRequest {
    schema_version: 1;
    entrypoint: "single_turn";
    apiBase: string;
    storageId: string;
    projectId: string;
    clientTurnId: string;
    request: StartAtelierAgentTurnRequest;
}

export interface StoredAtelierAgentLoopRequest {
    schema_version: 2;
    entrypoint: "agent_loop";
    apiBase: string;
    storageId: string;
    projectId: string;
    clientTurnId: string;
    request: StartAtelierAgentLoopRequest;
}

export type StoredAtelierAgentTurnRequest = StoredAtelierAgentSingleTurnRequest | StoredAtelierAgentLoopRequest;

export type AtelierAgentTurnRequestResult = {
    status: "created" | "pending";
    request: StoredAtelierAgentSingleTurnRequest;
};

export type AtelierAgentLoopRequestResult = {
    status: "created" | "pending";
    request: StoredAtelierAgentLoopRequest;
};

export type AtelierAgentTurnRequestErrorCode =
    | "atelier_agent_turn_request_storage_unavailable"
    | "atelier_agent_turn_request_store_corrupt"
    | "atelier_agent_turn_request_owner_invalid"
    | "atelier_agent_turn_request_payload_invalid"
    | "atelier_agent_turn_request_id_invalid";

export class AtelierAgentTurnRequestError extends Error {
    code: AtelierAgentTurnRequestErrorCode;

    constructor(code: AtelierAgentTurnRequestErrorCode) {
        super(code);
        this.name = "AtelierAgentTurnRequestError";
        this.code = code;
    }
}

export interface BeginAtelierAgentTurnRequestOptions extends AtelierAgentTurnRequestOwner {
    expectedRevision: number;
    clientTurnId: string;
    userMessage: string;
    selectedNodeId: string | null;
    storage?: Storage;
}

export interface BeginAtelierAgentLoopRequestOptions extends AtelierAgentTurnRequestOwner {
    expectedRevision: number;
    clientTurnId: string;
    userMessage: string;
    selectedNodeId: string | null;
    planner?: AtelierAgentLoopPlanner;
    maxIterations?: number;
    skillName?: string | null;
    storage?: Storage;
}

const REQUEST_PREFIX = "lumenx.atelierAgentTurn.request.v1:";
const ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;

export function createAtelierAgentTurnRequestId(): string {
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
    return `agent-turn-${suffix}`;
}

export function beginAtelierAgentTurnRequest(options: BeginAtelierAgentTurnRequestOptions): AtelierAgentTurnRequestResult {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const clientTurnId = validateId(options.clientTurnId);
    const request = freezeRequest(validateRequest({
        expected_revision: options.expectedRevision,
        client_turn_id: clientTurnId,
        user_message: options.userMessage,
        selected_node_id: options.selectedNodeId,
    }));
    const existing = listStoredForProject(storage, owner).find(item => item.clientTurnId === clientTurnId);
    if (existing) {
        if (existing.entrypoint !== "single_turn" || canonicalJson(existing.request) !== canonicalJson(request)) {
            throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
        }
        return { status: "pending", request: existing };
    }
    const record = freezeStoredRequest(validateStoredRequest({
        schema_version: 1,
        entrypoint: "single_turn",
        ...owner,
        clientTurnId,
        request,
    }, owner));
    if (record.entrypoint !== "single_turn") throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
    try {
        const key = requestStorageKey(owner, clientTurnId);
        const serialized = canonicalJson(record);
        storage.setItem(key, serialized);
        if (storage.getItem(key) !== serialized) {
            throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_storage_unavailable");
        }
    } catch (error) {
        if (error instanceof AtelierAgentTurnRequestError) throw error;
        throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_storage_unavailable");
    }
    return { status: "created", request: record };
}

export function beginAtelierAgentLoopRequest(options: BeginAtelierAgentLoopRequestOptions): AtelierAgentLoopRequestResult {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const clientTurnId = validateId(options.clientTurnId);
    const request = freezeLoopRequest(validateLoopRequest({
        expected_revision: options.expectedRevision,
        client_turn_id: clientTurnId,
        user_message: options.userMessage,
        selected_node_id: options.selectedNodeId,
        ...(options.planner === undefined ? {} : { planner: options.planner }),
        ...(Object.prototype.hasOwnProperty.call(options, "skillName") ? { skill_name: options.skillName } : {}),
        max_iterations: options.maxIterations ?? 3,
    }));
    const existing = listStoredForProject(storage, owner).find(item => item.clientTurnId === clientTurnId);
    if (existing) {
        if (existing.entrypoint !== "agent_loop" || canonicalJson(existing.request) !== canonicalJson(request)) {
            throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
        }
        return { status: "pending", request: existing };
    }
    const record = freezeStoredRequest(validateStoredRequest({
        schema_version: 2,
        entrypoint: "agent_loop",
        ...owner,
        clientTurnId,
        request,
    }, owner));
    if (record.entrypoint !== "agent_loop") throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
    try {
        const key = requestStorageKey(owner, clientTurnId);
        const serialized = canonicalJson(record);
        storage.setItem(key, serialized);
        if (storage.getItem(key) !== serialized) {
            throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_storage_unavailable");
        }
    } catch (error) {
        if (error instanceof AtelierAgentTurnRequestError) throw error;
        throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_storage_unavailable");
    }
    return { status: "created", request: record };
}

export function listProjectAtelierAgentTurnRequests(options: AtelierAgentTurnRequestOwner & { storage?: Storage }): StoredAtelierAgentTurnRequest[] {
    const storage = resolveStorage(options.storage);
    return listStoredForProject(storage, normalizeOwner(options));
}

export function readAtelierAgentTurnRequest(options: AtelierAgentTurnRequestOwner & { clientTurnId: string; storage?: Storage }): StoredAtelierAgentTurnRequest | null {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateId(options.clientTurnId);
    return listStoredForProject(storage, owner).find(item => item.clientTurnId === id) ?? null;
}

export function resolveAtelierAgentTurnRequest(options: AtelierAgentTurnRequestOwner & {
    clientTurnId: string;
    request: StartAtelierAgentTurnRequest;
    storage?: Storage;
}): boolean {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateId(options.clientTurnId);
    const request = freezeRequest(validateRequest(options.request, id, "atelier_agent_turn_request_store_corrupt"));
    const existing = listStoredForProject(storage, owner).find(item => item.clientTurnId === id);
    if (!existing || existing.entrypoint !== "single_turn" || canonicalJson(existing.request) !== canonicalJson(request)) return false;
    try {
        storage.removeItem(requestStorageKey(owner, id));
    } catch {
        throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_storage_unavailable");
    }
    return true;
}

export function resolveAtelierAgentLoopRequest(options: AtelierAgentTurnRequestOwner & {
    clientTurnId: string;
    request: StartAtelierAgentLoopRequest;
    storage?: Storage;
}): boolean {
    const storage = resolveStorage(options.storage);
    const owner = normalizeOwner(options);
    const id = validateId(options.clientTurnId);
    const request = freezeLoopRequest(validateLoopRequest(options.request, id, "atelier_agent_turn_request_store_corrupt"));
    const existing = listStoredForProject(storage, owner).find(item => item.clientTurnId === id);
    if (!existing || existing.entrypoint !== "agent_loop" || canonicalJson(existing.request) !== canonicalJson(request)) return false;
    try {
        storage.removeItem(requestStorageKey(owner, id));
    } catch {
        throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_storage_unavailable");
    }
    return true;
}

export function atelierAgentTurnProjectPendingKey(owner: AtelierAgentTurnRequestOwner): string {
    return ownerRequestPrefix(normalizeOwner(owner));
}

interface NormalizedOwner {
    apiBase: string;
    storageId: string;
    projectId: string;
}

function normalizeOwner(owner: AtelierAgentTurnRequestOwner): NormalizedOwner {
    const normalized = {
        apiBase: typeof owner.environment.apiUrl === "string" ? owner.environment.apiUrl : "",
        storageId: typeof owner.environment.storageId === "string" ? owner.environment.storageId : "",
        projectId: typeof owner.projectId === "string" ? owner.projectId : "",
    };
    if (!normalized.apiBase || !normalized.storageId || !normalized.projectId) {
        throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_owner_invalid");
    }
    return normalized;
}

function requestStorageKey(owner: NormalizedOwner, clientTurnId: string): string {
    return `${ownerRequestPrefix(owner)}:${encodeURIComponent(validateId(clientTurnId))}`;
}

function ownerRequestPrefix(owner: NormalizedOwner): string {
    return `${REQUEST_PREFIX}${encodeURIComponent(canonicalJson(owner))}`;
}

function listStoredForProject(storage: Storage, owner: NormalizedOwner): StoredAtelierAgentTurnRequest[] {
    const requests: StoredAtelierAgentTurnRequest[] = [];
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
                if (keyBelongsToOwner(key, owner)) throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
                continue;
            }
            if (!matchesOwner(parsed, owner)) {
                if (keyBelongsToOwner(key, owner)) throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
                continue;
            }
            const expectedKey = requestStorageKey(owner, clientTurnIdFromStoredRecord(parsed));
            if (key !== expectedKey) throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
            requests.push(freezeStoredRequest(validateStoredRequest(parsed, owner)));
        }
    } catch (error) {
        if (error instanceof AtelierAgentTurnRequestError) throw error;
        throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_storage_unavailable");
    }
    return requests.sort((left, right) => left.clientTurnId.localeCompare(right.clientTurnId));
}

function validateStoredRequest(value: unknown, owner: NormalizedOwner): StoredAtelierAgentTurnRequest {
    if (!isPlainRecord(value)) throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
    const record = value as Partial<StoredAtelierAgentTurnRequest> & { entrypoint?: unknown };
    if ((record.schema_version !== 1 && record.schema_version !== 2) || record.apiBase !== owner.apiBase || record.storageId !== owner.storageId
        || record.projectId !== owner.projectId || typeof record.clientTurnId !== "string") {
        throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
    }
    const clientTurnId = validateId(record.clientTurnId);
    if (record.schema_version === 2) {
        if (record.entrypoint !== "agent_loop") throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
        return {
            schema_version: 2,
            entrypoint: "agent_loop",
            apiBase: record.apiBase,
            storageId: record.storageId,
            projectId: record.projectId,
            clientTurnId,
            request: freezeLoopRequest(validateLoopRequest(record.request, clientTurnId, "atelier_agent_turn_request_store_corrupt")),
        };
    }
    if (record.entrypoint !== undefined && record.entrypoint !== "single_turn") throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
    return {
        schema_version: 1,
        entrypoint: "single_turn",
        apiBase: record.apiBase,
        storageId: record.storageId,
        projectId: record.projectId,
        clientTurnId,
        request: freezeRequest(validateRequest(record.request, clientTurnId, "atelier_agent_turn_request_store_corrupt")),
    };
}

function validateRequest(value: unknown, expectedTurnId?: string, code: AtelierAgentTurnRequestErrorCode = "atelier_agent_turn_request_payload_invalid"): StartAtelierAgentTurnRequest {
    const fail = (): never => { throw new AtelierAgentTurnRequestError(code); };
    if (!isPlainRecord(value)) fail();
    const keys = Object.keys(value as Record<string, unknown>);
    const allowed = new Set(["expected_revision", "client_turn_id", "user_message", "selected_node_id"]);
    if (keys.length !== allowed.size || keys.some(key => !allowed.has(key))) fail();
    const record = value as Partial<StartAtelierAgentTurnRequest>;
    if (!Number.isSafeInteger(record.expected_revision) || (record.expected_revision as number) < 0
        || typeof record.client_turn_id !== "string" || (expectedTurnId !== undefined && record.client_turn_id !== expectedTurnId)
        || typeof record.user_message !== "string" || (record.selected_node_id !== null && typeof record.selected_node_id !== "string")) {
        fail();
    }
    return {
        expected_revision: record.expected_revision as number,
        client_turn_id: validateId(record.client_turn_id as string),
        user_message: record.user_message as string,
        selected_node_id: record.selected_node_id === null ? null : validateId(record.selected_node_id as string),
    };
}

function validateLoopRequest(value: unknown, expectedTurnId?: string, code: AtelierAgentTurnRequestErrorCode = "atelier_agent_turn_request_payload_invalid"): StartAtelierAgentLoopRequest {
    const fail = (): never => { throw new AtelierAgentTurnRequestError(code); };
    if (!isPlainRecord(value)) fail();
    const keys = Object.keys(value as Record<string, unknown>);
    const required = ["expected_revision", "client_turn_id", "user_message", "selected_node_id"];
    const allowed = new Set([...required, "planner", "skill_name", "max_iterations"]);
    if (required.some(key => !Object.prototype.hasOwnProperty.call(value, key)) || keys.some(key => !allowed.has(key))) fail();
    const record = value as Partial<StartAtelierAgentLoopRequest>;
    if (!Number.isSafeInteger(record.expected_revision) || (record.expected_revision as number) < 0
        || typeof record.client_turn_id !== "string" || (expectedTurnId !== undefined && record.client_turn_id !== expectedTurnId)
        || typeof record.user_message !== "string" || (record.selected_node_id !== null && typeof record.selected_node_id !== "string")) {
        fail();
    }
    if (Object.prototype.hasOwnProperty.call(record, "planner") && record.planner !== "deterministic_core" && record.planner !== "model_adapter") fail();
    if (Object.prototype.hasOwnProperty.call(record, "skill_name")
        && record.skill_name !== null
        && (typeof record.skill_name !== "string" || !/^[a-z][a-z0-9-]{2,63}$/.test(record.skill_name))) fail();
    if (typeof record.skill_name === "string" && (record.planner ?? "deterministic_core") !== "model_adapter") fail();
    if (Object.prototype.hasOwnProperty.call(record, "max_iterations")
        && (!Number.isSafeInteger(record.max_iterations) || (record.max_iterations as number) < 1 || (record.max_iterations as number) > 8)) {
        fail();
    }
    return {
        expected_revision: record.expected_revision as number,
        client_turn_id: validateId(record.client_turn_id as string),
        user_message: record.user_message as string,
        selected_node_id: record.selected_node_id === null ? null : validateId(record.selected_node_id as string),
        ...(Object.prototype.hasOwnProperty.call(record, "planner") ? { planner: record.planner } : {}),
        ...(Object.prototype.hasOwnProperty.call(record, "skill_name") ? { skill_name: record.skill_name as string | null } : {}),
        ...(Object.prototype.hasOwnProperty.call(record, "max_iterations") ? { max_iterations: record.max_iterations as number } : {}),
    };
}

function clientTurnIdFromStoredRecord(value: unknown): string {
    if (!isPlainRecord(value) || typeof (value as Partial<StoredAtelierAgentTurnRequest>).clientTurnId !== "string") {
        throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_store_corrupt");
    }
    return validateId((value as Partial<StoredAtelierAgentTurnRequest>).clientTurnId as string);
}

function matchesOwner(value: unknown, owner: NormalizedOwner): boolean {
    return isPlainRecord(value)
        && (value as Partial<StoredAtelierAgentTurnRequest>).apiBase === owner.apiBase
        && (value as Partial<StoredAtelierAgentTurnRequest>).storageId === owner.storageId
        && (value as Partial<StoredAtelierAgentTurnRequest>).projectId === owner.projectId;
}

function keyBelongsToOwner(key: string, owner: NormalizedOwner): boolean {
    const parsedOwner = ownerFromStorageKey(key);
    return Boolean(parsedOwner && parsedOwner.apiBase === owner.apiBase && parsedOwner.storageId === owner.storageId && parsedOwner.projectId === owner.projectId);
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
            || typeof rawOwner.projectId !== "string" || !rawOwner.projectId) {
            return null;
        }
        return { apiBase: rawOwner.apiBase, storageId: rawOwner.storageId, projectId: rawOwner.projectId };
    } catch {
        return null;
    }
}

function validateId(value: string): string {
    if (typeof value !== "string" || !ID_RE.test(value)) throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_id_invalid");
    return value;
}

function resolveStorage(storage?: Storage): Storage {
    if (storage) return storage;
    try {
        if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
    } catch {
        throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_storage_unavailable");
    }
    throw new AtelierAgentTurnRequestError("atelier_agent_turn_request_storage_unavailable");
}

function freezeRequest(request: StartAtelierAgentTurnRequest): StartAtelierAgentTurnRequest {
    return Object.freeze({ ...request });
}

function freezeLoopRequest(request: StartAtelierAgentLoopRequest): StartAtelierAgentLoopRequest {
    return Object.freeze({ ...request });
}

function freezeStoredRequest(request: StoredAtelierAgentTurnRequest): StoredAtelierAgentTurnRequest {
    return request.entrypoint === "agent_loop"
        ? Object.freeze({ ...request, request: freezeLoopRequest(request.request) })
        : Object.freeze({ ...request, request: freezeRequest(request.request) });
}

function canonicalJson(value: unknown): string {
    return JSON.stringify(sortJson(strictJsonValue(value, new WeakSet())));
}

function sortJson(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(sortJson);
    if (value && typeof value === "object") {
        const output: Record<string, unknown> = {};
        for (const key of Object.keys(value as Record<string, unknown>).sort()) output[key] = sortJson((value as Record<string, unknown>)[key]);
        return output;
    }
    return value;
}

function strictJsonValue(value: unknown, seen: WeakSet<object>, code: AtelierAgentTurnRequestErrorCode = "atelier_agent_turn_request_payload_invalid"): unknown {
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new AtelierAgentTurnRequestError(code);
        return value;
    }
    if (Array.isArray(value)) {
        rejectCycle(value, seen, code);
        const output: unknown[] = [];
        for (let index = 0; index < value.length; index += 1) {
            if (!Object.prototype.hasOwnProperty.call(value, index)) throw new AtelierAgentTurnRequestError(code);
            output.push(strictJsonValue(value[index], seen, code));
        }
        seen.delete(value);
        return output;
    }
    if (isPlainRecord(value)) {
        rejectCycle(value, seen, code);
        const output: Record<string, unknown> = {};
        for (const [key, item] of Object.entries(value)) {
            if (typeof item === "undefined" || typeof item === "function" || typeof item === "symbol" || typeof item === "bigint") {
                throw new AtelierAgentTurnRequestError(code);
            }
            output[key] = strictJsonValue(item, seen, code);
        }
        seen.delete(value);
        return output;
    }
    throw new AtelierAgentTurnRequestError(code);
}

function rejectCycle(value: object, seen: WeakSet<object>, code: AtelierAgentTurnRequestErrorCode): void {
    if (seen.has(value)) throw new AtelierAgentTurnRequestError(code);
    seen.add(value);
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
}
