// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";
import {
    AtelierGenerationRequestError,
    atelierGenerationNodePendingKey,
    beginAtelierGenerationRequest,
    createAtelierGenerationRequestId,
    listAtelierGenerationRequests,
    listProjectAtelierGenerationRequests,
    readAtelierGenerationRequest,
    resolveAtelierGenerationRequest,
} from "@/lib/atelierGenerationRequest";
import type { RuntimeEnvironment } from "@/lib/runtimeEnvironment";
import requestsFixture from "./fixtures/atelier-ordered-generation-requests.json";
import type { AtelierOrderedVideoCandidateRequest } from "@/lib/atelierTypes";

const env = (storageId: string): RuntimeEnvironment => ({
    apiUrl: "http://atelier.local/api",
    storageId,
    key: JSON.stringify(["http://atelier.local/api", storageId]),
    generation: 1,
});

const ownerA = { environment: env("storage-a"), projectId: "project-1", nodeId: "node-video" };
const ownerOtherNode = { environment: env("storage-a"), projectId: "project-1", nodeId: "node-other" };
const ownerOtherStorage = { environment: env("storage-b"), projectId: "project-1", nodeId: "node-video" };

const firstFrame = { node_id: "node-image", media_url: "uploads/frame.png" };

beforeEach(() => {
    localStorage.clear();
});

describe("Atelier generation request journal", () => {
    it.each([null, {}, { ...ownerA, projectId: "another-project" }])("does not hide a malformed record under the current project key: %j", value => {
        localStorage.setItem(`${atelierGenerationNodePendingKey(ownerA)}:original-request`, JSON.stringify(value));
        expect(() => listProjectAtelierGenerationRequests(ownerA))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_store_corrupt"));
    });
    it("isolates owners by runtime storage id, project id, and node id", () => {
        beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 7,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "slow sunrise",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-a",
        });
        beginAtelierGenerationRequest({
            ...ownerOtherNode,
            expectedRevision: 7,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "same node text",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-b",
        });
        beginAtelierGenerationRequest({
            ...ownerOtherStorage,
            expectedRevision: 7,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "same storage text",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-c",
        });

        expect(atelierGenerationNodePendingKey(ownerA)).not.toBe(atelierGenerationNodePendingKey(ownerOtherNode));
        expect(atelierGenerationNodePendingKey(ownerA)).not.toBe(atelierGenerationNodePendingKey(ownerOtherStorage));
        expect(listAtelierGenerationRequests({ ...ownerA, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["generation-a"]);
        expect(listAtelierGenerationRequests({ ...ownerOtherNode, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["generation-b"]);
        expect(listAtelierGenerationRequests({ ...ownerOtherStorage, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["generation-c"]);
    });


    it("lists all pending generation requests for a project across nodes", () => {
        beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "node still visible",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-project-a",
        });
        beginAtelierGenerationRequest({
            ...ownerOtherNode,
            expectedRevision: 2,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "node removed from canvas but request remains",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-project-b",
        });
        beginAtelierGenerationRequest({
            ...ownerOtherStorage,
            expectedRevision: 3,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "other storage",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-project-c",
        });

        const projectRequests = listProjectAtelierGenerationRequests({
            environment: ownerA.environment,
            projectId: ownerA.projectId,
            storage: localStorage,
        });

        expect(projectRequests.map(item => [item.nodeId, item.clientRequestId])).toEqual([
            ["node-video", "generation-project-a"],
            ["node-other", "generation-project-b"],
        ]);
        expect(listAtelierGenerationRequests({ ...ownerA, storage: localStorage }).map(item => item.clientRequestId))
            .toEqual(["generation-project-a"]);
    });

    it("rejects project-level client_request_id reuse across nodes", () => {
        beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "first node",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-shared-id",
        });

        expect(() => beginAtelierGenerationRequest({
            ...ownerOtherNode,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "second node same id",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-shared-id",
        })).toThrowError(new AtelierGenerationRequestError("atelier_generation_request_store_corrupt"));

        expect(beginAtelierGenerationRequest({
            ...ownerOtherNode,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "second node different id",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-different-id",
        }).status).toBe("created");
    });

    it("fails closed when storage accepts setItem but readback cannot verify the record", () => {
        const memory = new Map<string, string>();
        const noReadbackStorage = {
            get length() { return memory.size; },
            key: (index: number) => Array.from(memory.keys())[index] ?? null,
            getItem: () => null,
            setItem: (key: string, value: string) => { memory.set(key, value); },
            removeItem: (key: string) => { memory.delete(key); },
            clear: () => { memory.clear(); },
        } as unknown as Storage;

        expect(() => beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "write without readback",
            firstFrame,
            storage: noReadbackStorage,
            idFactory: () => "generation-no-readback",
        })).toThrowError(new AtelierGenerationRequestError("atelier_generation_request_storage_unavailable"));
        expect(memory.size).toBe(1);
    });

    it("freezes the exact backend request while preserving params omission", () => {
        const params = { duration: 5, resolution: "1080p" };
        const result = beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 3,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "wide shot",
            firstFrame,
            params,
            storage: localStorage,
            idFactory: () => "generation-freeze",
        });
        params.duration = 9;

        const stored = readAtelierGenerationRequest({ ...ownerA, clientRequestId: "generation-freeze", storage: localStorage })!;
        expect(stored.request).toEqual({
            expected_revision: 3,
            client_request_id: "generation-freeze",
            model_id: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "wide shot",
            first_frame: firstFrame,
            params: { duration: 5, resolution: "1080p" },
        });
        expect(result.request.request.params).toEqual({ duration: 5, resolution: "1080p" });
        expect(Object.isFrozen(result.request.request.params)).toBe(true);

        const omitted = beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 4,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "no params",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-omitted",
        });
        const empty = beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 5,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "empty params",
            firstFrame,
            params: {},
            storage: localStorage,
            idFactory: () => "generation-empty",
        });
        expect(omitted.request.request).not.toHaveProperty("params");
        expect(empty.request.request).toHaveProperty("params", {});
    });

    it("preserves optional manual batch size in the one original request", () => {
        const result = beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 6,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "three variations",
            firstFrame,
            batchSize: 3,
            storage: localStorage,
            idFactory: () => "generation-batch",
        });

        expect(result.request.request).toMatchObject({ client_request_id: "generation-batch", batch_size: 3 });
        expect(listAtelierGenerationRequests({ ...ownerA, storage: localStorage })).toHaveLength(1);
        expect(resolveAtelierGenerationRequest({
            ...ownerA,
            clientRequestId: "generation-batch",
            request: result.request.request,
            storage: localStorage,
        })).toBe(true);

        expect(() => beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 6,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "bad batch",
            firstFrame,
            batchSize: 7,
            storage: localStorage,
            idFactory: () => "generation-bad-batch",
        })).toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
    });

    it("fails closed on storage write failure so callers cannot POST afterward", () => {
        const brokenStorage = {
            get length() { return 0; },
            key: () => null,
            getItem: () => null,
            setItem: () => { throw new Error("quota"); },
            removeItem: () => undefined,
            clear: () => undefined,
        } as unknown as Storage;

        expect(() => beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "fail",
            firstFrame,
            storage: brokenStorage,
            idFactory: () => "generation-fail",
        })).toThrowError(new AtelierGenerationRequestError("atelier_generation_request_storage_unavailable"));
        expect(listAtelierGenerationRequests({ ...ownerA, storage: localStorage })).toEqual([]);
    });

    it("retains pending requests until the exact request is resolved", () => {
        const stored = beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "retry after unknown",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-readback",
        }).request;

        expect(resolveAtelierGenerationRequest({
            ...ownerA,
            clientRequestId: "generation-readback",
            request: { ...stored.request, prompt: "changed" },
            storage: localStorage,
        })).toBe(false);
        expect(readAtelierGenerationRequest({ ...ownerA, clientRequestId: "generation-readback", storage: localStorage })).toEqual(stored);
        expect(resolveAtelierGenerationRequest({
            ...ownerA,
            clientRequestId: "generation-readback",
            request: stored.request,
            storage: localStorage,
        })).toBe(true);
        expect(readAtelierGenerationRequest({ ...ownerA, clientRequestId: "generation-readback", storage: localStorage })).toBeNull();
    });

    it("rejects request-id collisions, corrupt current-owner records, unknown fields, and invalid params", () => {
        beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "original",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-collision",
        });

        expect(beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "original",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-collision",
        }).status).toBe("pending");
        expect(() => beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "different",
            firstFrame,
            storage: localStorage,
            idFactory: () => "generation-collision",
        })).toThrowError(new AtelierGenerationRequestError("atelier_generation_request_store_corrupt"));

        expect(() => beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "bad param",
            firstFrame,
            params: { duration: 5, ratio: "16:9" },
            storage: localStorage,
            idFactory: () => "generation-bad-param",
        })).toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));

        localStorage.setItem(`${atelierGenerationNodePendingKey(ownerOtherNode)}:generation-bad`, "{broken");
        expect(listAtelierGenerationRequests({ ...ownerA, storage: localStorage }).map(item => item.clientRequestId)).toEqual(["generation-collision"]);
        localStorage.setItem(`${atelierGenerationNodePendingKey(ownerA)}:generation-bad`, "{broken");
        expect(() => listAtelierGenerationRequests({ ...ownerA, storage: localStorage }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_store_corrupt"));
    });

    it("validates stored request shape strictly and creates valid ids", () => {
        expect(createAtelierGenerationRequestId()).toMatch(/^generation-[A-Za-z0-9._:-]{1,119}$/);
        expect(() => beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 1,
            modelId: "wan/wan2.7-video#i2v",
            mode: "i2v",
            prompt: "bad first frame",
            firstFrame: { node_id: "node-image", media_url: "uploads/frame.png", extra: true },
            storage: localStorage,
            idFactory: () => "generation-extra",
        })).toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => resolveAtelierGenerationRequest({
            ...ownerA,
            clientRequestId: "generation-extra",
            request: {
                expected_revision: 1,
                client_request_id: "generation-extra",
                model_id: "wan/wan2.7-video#i2v",
                mode: "i2v",
                prompt: "bad top field",
                first_frame: firstFrame,
                extra: true,
            } as never,
            storage: localStorage,
        })).toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
    });
});

describe("Atelier ordered media generation request journal", () => {
    const orderedMedia = [
        { role: "first_frame" as const, ordinal: 0, node_id: "first-node", media_url: "uploads/first.png" },
        { role: "last_frame" as const, ordinal: 0, node_id: "last-node", media_url: "uploads/last.png" },
    ];

    it("freezes the exact ordered-media-v1 request and preserves false, zero, negative one, and omission", () => {
        const result = beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 8,
            contractVersion: "ordered-media-v1",
            modelId: "wan/wan3.0-video#video",
            mode: "i2v",
            prompt: "animate endpoints",
            media: orderedMedia,
            params: { duration: -1, seed: 0, resolution: "480P", ratio: "16:9", prompt_extend: false, watermark: false, audio: false },
            batchSize: 6,
            storage: localStorage,
            idFactory: () => "generation-ordered",
        });

        orderedMedia[0].media_url = "mutated.png";
        const stored = readAtelierGenerationRequest({ ...ownerA, clientRequestId: "generation-ordered", storage: localStorage })!;
        expect(stored.request).toEqual({
            expected_revision: 8,
            client_request_id: "generation-ordered",
            contract_version: "ordered-media-v1",
            model_id: "wan/wan3.0-video#video",
            mode: "i2v",
            prompt: "animate endpoints",
            media: [
                { role: "first_frame", ordinal: 0, node_id: "first-node", media_url: "uploads/first.png" },
                { role: "last_frame", ordinal: 0, node_id: "last-node", media_url: "uploads/last.png" },
            ],
            params: { duration: -1, seed: 0, resolution: "480P", ratio: "16:9", prompt_extend: false, watermark: false, audio: false },
            batch_size: 6,
        });
        expect(result.request.request).toEqual(stored.request);
        expect(Object.isFrozen(result.request.request)).toBe(true);
        if (!("contract_version" in result.request.request)) throw new Error("expected ordered request");
        expect(Object.isFrozen(result.request.request.media[0])).toBe(true);
        expect(resolveAtelierGenerationRequest({ ...ownerA, clientRequestId: "generation-ordered", request: stored.request, storage: localStorage })).toBe(true);

        const omitted = beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: 9,
            contractVersion: "ordered-media-v1",
            modelId: "wan/wan3.0-video-prime#video",
            mode: "t2v",
            prompt: "text only",
            media: [],
            storage: localStorage,
            idFactory: () => "generation-ordered-omitted",
        });
        expect(omitted.request.request).not.toHaveProperty("params");
        expect(omitted.request.request).not.toHaveProperty("batch_size");
    });


    it("accepts backend fixture ordered requests while preserving strict scalar values", () => {
        const requests = requestsFixture as { t2v: AtelierOrderedVideoCandidateRequest; batch: AtelierOrderedVideoCandidateRequest };
        const result = beginAtelierGenerationRequest({
            ...ownerA,
            expectedRevision: requests.t2v.expected_revision,
            contractVersion: requests.t2v.contract_version,
            modelId: requests.t2v.model_id,
            mode: requests.t2v.mode,
            prompt: requests.t2v.prompt,
            media: requests.t2v.media,
            params: requests.t2v.params,
            storage: localStorage,
            idFactory: () => requests.t2v.client_request_id,
        });
        expect(result.request.request).toEqual(requests.t2v);
        expect(result.request.request).not.toHaveProperty("first_frame");
        if (!("contract_version" in result.request.request)) throw new Error("expected ordered request");
        expect(result.request.request.params).toMatchObject({ audio: false, duration: -1, seed: 0 });

        const batch = beginAtelierGenerationRequest({
            ...ownerOtherNode,
            expectedRevision: requests.batch.expected_revision,
            contractVersion: requests.batch.contract_version,
            modelId: requests.batch.model_id,
            mode: requests.batch.mode,
            prompt: requests.batch.prompt,
            media: requests.batch.media,
            params: requests.batch.params,
            batchSize: requests.batch.batch_size,
            storage: localStorage,
            idFactory: () => requests.batch.client_request_id,
        });
        expect(batch.request.request).toEqual(requests.batch);
        expect(batch.request.request).not.toHaveProperty("first_frame");
    });

    it("rejects ordered-media contract drift before storage or POST", () => {
        const base = {
            ...ownerA,
            expectedRevision: 1,
            contractVersion: "ordered-media-v1" as const,
            modelId: "wan/wan3.0-video#video" as const,
            mode: "i2v" as const,
            prompt: "strict",
            media: orderedMedia,
            storage: localStorage,
        };
        expect(() => beginAtelierGenerationRequest({ ...base, modelId: "wan/wan2.7-video#i2v" as never, idFactory: () => "bad-model" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => beginAtelierGenerationRequest({ ...base, media: [{ ...orderedMedia[0], ordinal: 1 }], idFactory: () => "bad-ordinal" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => beginAtelierGenerationRequest({ ...base, mode: "r2v", media: orderedMedia, idFactory: () => "bad-r2v" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => beginAtelierGenerationRequest({ ...base, batchSize: 0, idFactory: () => "bad-batch" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => beginAtelierGenerationRequest({ ...base, params: { negative_prompt: "legacy" }, idFactory: () => "bad-param" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        for (const [params, id] of [
            [{ duration: 0 }, "bad-duration-low"],
            [{ duration: 31 }, "bad-duration-high"],
            [{ duration: 2.5 }, "bad-duration-step"],
            [{ seed: -2 }, "bad-seed"],
            [{ seed: 2147483648 }, "bad-seed-high"],
            [{ resolution: "4K" }, "bad-resolution"],
            [{ ratio: "2:1" }, "bad-ratio"],
        ] as const) {
            expect(() => beginAtelierGenerationRequest({ ...base, mode: "t2v", media: [], params, idFactory: () => id }))
                .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        }
        expect(() => beginAtelierGenerationRequest({ ...base, contractVersion: null as never, idFactory: () => "bad-contract-null" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => beginAtelierGenerationRequest({ ...base, contractVersion: "ordered-media-v2" as never, idFactory: () => "bad-contract-version" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => beginAtelierGenerationRequest({ ...base, mode: "r2v", media: [
            { role: "reference_image", ordinal: 1, node_id: "image-2", media_url: "uploads/2.png" },
            { role: "reference_image", ordinal: 0, node_id: "image-1", media_url: "uploads/1.png" },
        ], idFactory: () => "bad-role-order" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => beginAtelierGenerationRequest({ ...base, mode: "r2v", media: [
            { role: "reference_audio", ordinal: 0, node_id: "audio", media_url: "uploads/audio.wav" },
            { role: "reference_image", ordinal: 0, node_id: "image", media_url: "uploads/image.png" },
        ], idFactory: () => "bad-cross-role-order" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => beginAtelierGenerationRequest({ ...base, media: [{ ...orderedMedia[0], node_id: "   " }], idFactory: () => "bad-node-blank" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
        expect(() => beginAtelierGenerationRequest({ ...base, media: [{ ...orderedMedia[0], media_url: "   " }], idFactory: () => "bad-url-blank" }))
            .toThrowError(new AtelierGenerationRequestError("atelier_generation_request_payload_invalid"));
    });
});
