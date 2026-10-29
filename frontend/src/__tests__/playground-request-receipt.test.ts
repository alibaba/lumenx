import { describe, expect, it } from "vitest";
import { parsePlaygroundRequestReceipt } from "@/lib/playgroundRequestReceipt";
import wireFixture from "./fixtures/playground-admission-wire.json";
import type { PlaygroundRequestReceiptResponse } from "@/lib/api";
import type { PlaygroundGenerateRequestBody, StoredPlaygroundGenerationRequest } from "@/lib/playgroundGenerationRequest";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

const request: PlaygroundGenerateRequestBody = {
    client_request_id: "playground-receipt",
    mode: "t2i",
    model_id: "wanx2.1-t2i",
    prompt: "glass city",
};

const journal: StoredPlaygroundGenerationRequest = {
    schema_version: 1,
    apiBase: "http://localhost:17177",
    storageId: "00000000-0000-4000-8000-000000000001",
    clientRequestId: request.client_request_id,
    requestFingerprint: JSON.stringify(request),
    request,
    createdAt: 1,
};

function accepted(overrides: Record<string, unknown> = {}): PlaygroundRequestReceiptResponse {
    return {
        schema_version: 1,
        replayed: false,
        request_status: "accepted",
        generation: {
            id: "gen-1",
            mode: request.mode,
            model_id: request.model_id,
            prompt: request.prompt,
            input_media: [],
            parameters: {},
            batch_size: 1,
            outputs: [],
            status: "completed",
            created_at: "2026-10-13T00:00:00.000Z",
            client_request_id: request.client_request_id,
            request_contract_version: "request-json-v1",
            request_fingerprint: HASH_A,
            ...overrides,
        },
        tombstone: null,
    } as PlaygroundRequestReceiptResponse;
}

function deleted(overrides: Record<string, unknown> = {}, receiptOverrides: Record<string, unknown> = {}): PlaygroundRequestReceiptResponse {
    return {
        schema_version: 1,
        replayed: true,
        request_status: "deleted",
        generation: null,
        tombstone: {
            record_type: "tombstone",
            schema_version: 1,
            client_request_id: request.client_request_id,
            request_contract_version: "request-json-v1",
            request_fingerprint: HASH_A,
            generation_id: "gen-deleted",
            deleted_at: "2026-10-13T00:00:00.000Z",
            deleted_status: "deleted",
            ...overrides,
        },
        ...receiptOverrides,
    } as PlaygroundRequestReceiptResponse;
}

describe("parsePlaygroundRequestReceipt", () => {
    it("accepts a strict accepted receipt and returns the server fingerprint", () => {
        expect(parsePlaygroundRequestReceipt(accepted(), journal)).toEqual({
            requestStatus: "accepted",
            generation: accepted().generation,
            serverRequestFingerprint: HASH_A,
        });
    });


    it("accepts the real FastAPI wire fixture for accepted, readback, and deleted receipts", () => {
        const wire = wireFixture as {
            request: PlaygroundGenerateRequestBody;
            accepted: PlaygroundRequestReceiptResponse;
            readback: PlaygroundRequestReceiptResponse;
            deleted: PlaygroundRequestReceiptResponse;
        };
        const wireJournal: StoredPlaygroundGenerationRequest = {
            schema_version: 1,
            apiBase: "http://localhost:17177",
            storageId: "00000000-0000-4000-8000-000000000001",
            clientRequestId: wire.request.client_request_id,
            requestFingerprint: JSON.stringify(wire.request),
            request: wire.request,
            createdAt: 1,
        };

        const acceptedResult = parsePlaygroundRequestReceipt(wire.accepted, wireJournal);
        expect(acceptedResult.requestStatus).toBe("accepted");
        expect(acceptedResult.serverRequestFingerprint).toBe(wire.accepted.generation?.request_fingerprint);

        const readbackResult = parsePlaygroundRequestReceipt(
            wire.readback,
            wireJournal,
            acceptedResult.serverRequestFingerprint,
        );
        expect(readbackResult.requestStatus).toBe("accepted");
        expect(readbackResult.serverRequestFingerprint).toBe(acceptedResult.serverRequestFingerprint);

        const deletedResult = parsePlaygroundRequestReceipt(
            wire.deleted,
            wireJournal,
            acceptedResult.serverRequestFingerprint,
        );
        expect(deletedResult.requestStatus).toBe("deleted");
        expect(deletedResult.serverRequestFingerprint).toBe(acceptedResult.serverRequestFingerprint);
    });


    it("matches parameters semantically when plain object keys are reordered", () => {
        const requestWithParams: PlaygroundGenerateRequestBody = {
            client_request_id: "playground-param-order",
            mode: "t2i",
            model_id: "wanx2.1-t2i",
            prompt: "glass city",
            parameters: {
                zeta: { beta: 2, alpha: [1, { y: false, x: "yes" }] },
                alpha: "first",
            },
        };
        const journalWithParams: StoredPlaygroundGenerationRequest = {
            ...journal,
            clientRequestId: requestWithParams.client_request_id,
            request: requestWithParams,
        };

        const result = parsePlaygroundRequestReceipt(accepted({
            client_request_id: requestWithParams.client_request_id,
            parameters: {
                alpha: "first",
                zeta: { alpha: [1, { x: "yes", y: false }], beta: 2 },
            },
        }), journalWithParams);

        expect(result.requestStatus).toBe("accepted");
    });

    it("rejects parameter value changes even when object keys otherwise match", () => {
        const requestWithParams: PlaygroundGenerateRequestBody = {
            client_request_id: "playground-param-value",
            mode: "t2i",
            model_id: "wanx2.1-t2i",
            prompt: "glass city",
            parameters: { nested: { value: 1 } },
        };
        const journalWithParams: StoredPlaygroundGenerationRequest = {
            ...journal,
            clientRequestId: requestWithParams.client_request_id,
            request: requestWithParams,
        };

        expect(() => parsePlaygroundRequestReceipt(accepted({
            client_request_id: requestWithParams.client_request_id,
            parameters: { nested: { value: 2 } },
        }), journalWithParams)).toThrow("playground_request_receipt_invalid");
    });

    it("rejects reordered arrays inside parameters", () => {
        const requestWithParams: PlaygroundGenerateRequestBody = {
            client_request_id: "playground-param-array",
            mode: "t2i",
            model_id: "wanx2.1-t2i",
            prompt: "glass city",
            parameters: { sequence: ["first", "second"] },
        };
        const journalWithParams: StoredPlaygroundGenerationRequest = {
            ...journal,
            clientRequestId: requestWithParams.client_request_id,
            request: requestWithParams,
        };

        expect(() => parsePlaygroundRequestReceipt(accepted({
            client_request_id: requestWithParams.client_request_id,
            parameters: { sequence: ["second", "first"] },
        }), journalWithParams)).toThrow("playground_request_receipt_invalid");
    });

    it("rejects accepted receipts with the same client id but different intent", () => {
        expect(() => parsePlaygroundRequestReceipt(accepted({ prompt: "different prompt" }), journal))
            .toThrow("playground_request_receipt_invalid");
    });

    it("rejects malformed server fingerprints", () => {
        expect(() => parsePlaygroundRequestReceipt(accepted({ request_fingerprint: "{}" }), journal))
            .toThrow("playground_request_receipt_invalid");
    });

    it("rejects contradictory deleted ACKs", () => {
        expect(() => parsePlaygroundRequestReceipt(deleted({}, { generation: accepted().generation }), journal))
            .toThrow("playground_request_receipt_invalid");
    });

    it("rejects later receipts whose server fingerprint changed", () => {
        expect(() => parsePlaygroundRequestReceipt(accepted({ request_fingerprint: HASH_B }), journal, HASH_A))
            .toThrow("playground_request_receipt_invalid");
    });

    it("rejects tombstones for a different client request id", () => {
        expect(() => parsePlaygroundRequestReceipt(deleted({ client_request_id: "playground-other" }), journal))
            .toThrow("playground_request_receipt_invalid");
    });
});
