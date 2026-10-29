import type { PlaygroundGenerationResponse, PlaygroundRequestReceiptResponse, PlaygroundRequestTombstoneResponse } from "@/lib/api";
import type { PlaygroundGenerateRequestBody, StoredPlaygroundGenerationRequest } from "@/lib/playgroundGenerationRequest";

export const PLAYGROUND_REQUEST_CONTRACT_VERSION = "request-json-v1";

export type ParsedPlaygroundRequestReceipt =
    | { requestStatus: "accepted"; generation: PlaygroundGenerationResponse; serverRequestFingerprint: string }
    | { requestStatus: "deleted"; tombstone: PlaygroundRequestTombstoneResponse; serverRequestFingerprint: string };

export class PlaygroundRequestReceiptError extends Error {
    code = "playground_request_receipt_invalid" as const;

    constructor() {
        super("playground_request_receipt_invalid");
        this.name = "PlaygroundRequestReceiptError";
    }
}

const HEX64 = /^[0-9a-f]{64}$/;

export function parsePlaygroundRequestReceipt(
    receipt: PlaygroundRequestReceiptResponse,
    journal: StoredPlaygroundGenerationRequest,
    previousServerRequestFingerprint?: string,
): ParsedPlaygroundRequestReceipt {
    if (!receipt || receipt.schema_version !== 1 || typeof receipt.replayed !== "boolean") invalid();
    if (receipt.request_status === "accepted") {
        if (!receipt.generation || receipt.tombstone !== null) invalid();
        const generation = receipt.generation;
        const serverRequestFingerprint = validateIdentity(
            generation.client_request_id,
            generation.request_contract_version,
            generation.request_fingerprint,
            journal,
            previousServerRequestFingerprint,
        );
        if (!generationMatchesRequest(generation, journal.request)) invalid();
        return { requestStatus: "accepted", generation, serverRequestFingerprint };
    }
    if (receipt.request_status === "deleted") {
        if (receipt.generation !== null || !receipt.tombstone) invalid();
        const tombstone = receipt.tombstone;
        if (tombstone.record_type !== "tombstone" || tombstone.schema_version !== 1) invalid();
        const serverRequestFingerprint = validateIdentity(
            tombstone.client_request_id,
            tombstone.request_contract_version,
            tombstone.request_fingerprint,
            journal,
            previousServerRequestFingerprint,
        );
        if (typeof tombstone.generation_id !== "string" || !tombstone.generation_id
            || typeof tombstone.deleted_at !== "string" || !tombstone.deleted_at
            || typeof tombstone.deleted_status !== "string" || !tombstone.deleted_status) invalid();
        return { requestStatus: "deleted", tombstone, serverRequestFingerprint };
    }
    invalid();
}

function validateIdentity(
    clientRequestId: unknown,
    requestContractVersion: unknown,
    requestFingerprint: unknown,
    journal: StoredPlaygroundGenerationRequest,
    previousServerRequestFingerprint?: string,
): string {
    if (clientRequestId !== journal.clientRequestId
        || requestContractVersion !== PLAYGROUND_REQUEST_CONTRACT_VERSION
        || typeof requestFingerprint !== "string"
        || !HEX64.test(requestFingerprint)
        || (previousServerRequestFingerprint !== undefined && requestFingerprint !== previousServerRequestFingerprint)) {
        invalid();
    }
    return requestFingerprint;
}

function generationMatchesRequest(generation: PlaygroundGenerationResponse, request: PlaygroundGenerateRequestBody): boolean {
    return generation.mode === request.mode
        && generation.model_id === request.model_id
        && generation.prompt === request.prompt
        && optionalString(generation.negative_prompt) === optionalString(request.negative_prompt)
        && semanticEqual(generation.input_media ?? [], request.input_media ?? [])
        && semanticEqual(generation.parameters ?? {}, request.parameters ?? {})
        && generation.batch_size === (request.batch_size ?? 1);
}

function optionalString(value: unknown): string | undefined {
    return typeof value === "string" ? value : undefined;
}

function semanticEqual(left: unknown, right: unknown): boolean {
    if (left === right) return true;
    if (Array.isArray(left) || Array.isArray(right)) {
        if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
        return left.every((item, index) => semanticEqual(item, right[index]));
    }
    if (isPlainRecord(left) || isPlainRecord(right)) {
        if (!isPlainRecord(left) || !isPlainRecord(right)) return false;
        const leftKeys = Object.keys(left).sort();
        const rightKeys = Object.keys(right).sort();
        if (leftKeys.length !== rightKeys.length) return false;
        return leftKeys.every((key, index) => key === rightKeys[index] && semanticEqual(left[key], right[key]));
    }
    return false;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value)
        && Object.getPrototypeOf(value) === Object.prototype;
}

function invalid(): never {
    throw new PlaygroundRequestReceiptError();
}
