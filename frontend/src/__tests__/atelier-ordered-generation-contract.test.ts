import { describe, expect, it } from "vitest";
import { parseAtelierGenerationAdmissionAck } from "@/lib/atelierGenerationApi";
import type { AtelierGenerationAttempt, AtelierOrderedGenerationAttempt, AtelierOrderedVideoCandidateRequest } from "@/lib/atelierTypes";
import t2vFixture from "./fixtures/atelier-ordered-generation-t2v.json";
import batchFixture from "./fixtures/atelier-ordered-generation-batch.json";
import batchReplayedFixture from "./fixtures/atelier-ordered-generation-batch-replayed.json";
import requestsFixture from "./fixtures/atelier-ordered-generation-requests.json";

const PROJECT_ID = "p";
const NODE_ID = "video";
const requests = requestsFixture as { t2v: AtelierOrderedVideoCandidateRequest; batch: AtelierOrderedVideoCandidateRequest };
const orderedAttempt = (attempt: AtelierGenerationAttempt): AtelierOrderedGenerationAttempt => {
    expect(attempt).not.toHaveProperty("first_frame_source");
    if (!("input_contract_version" in attempt)) throw new Error("expected ordered attempt");
    return attempt;
};

describe("Atelier ordered generation backend wire fixtures", () => {
    it("parses a Wan3 ordered T2V singleton without legacy first_frame fields", () => {
        const ack = parseAtelierGenerationAdmissionAck(t2vFixture, PROJECT_ID, {
            nodeId: NODE_ID,
            request: requests.t2v,
            creating: true,
        });

        expect(ack.receipt).toMatchObject({
            schema_version: 1,
            entrypoint: "atelier.provider_video.v2",
            request_contract_version: "request-json-v1",
            client_request_id: requests.t2v.client_request_id,
            node_id: NODE_ID,
        });
        const attempt = orderedAttempt(ack.attempt);
        expect(attempt.input_contract_version).toBe("ordered-media-v1");
        expect(attempt.input_media).toEqual([]);
        expect(attempt.provider_execution.media).toEqual([]);
        expect(attempt.provider_execution.intent).toMatchObject({
            model_id: "wan/wan3.0-video#video",
            mode: "t2v",
            params: expect.objectContaining({ audio: false, duration: -1, seed: 0 }),
        });
        expect(JSON.stringify(t2vFixture)).not.toContain("fixture-secret");
    });

    it("parses a two-member ordered first/last frame batch and replay", () => {
        const ack = parseAtelierGenerationAdmissionAck(batchFixture, PROJECT_ID, {
            nodeId: NODE_ID,
            request: requests.batch,
            creating: true,
            allowBatch: true,
        });

        expect(ack.receipt).toMatchObject({
            schema_version: 2,
            entrypoint: "atelier.provider_video.v2",
            client_request_id: requests.batch.client_request_id,
            node_id: NODE_ID,
        });
        expect(ack.members).toHaveLength(2);
        expect(ack.new_candidate_ids).toEqual(["candidate-2", "candidate-3"]);
        for (const member of ack.members) {
            const attempt = orderedAttempt(member.attempt);
            expect(attempt.input_contract_version).toBe("ordered-media-v1");
            expect(attempt.input_media).toEqual(requests.batch.media);
            expect(attempt.provider_execution.media).toHaveLength(requests.batch.media.length);
            attempt.provider_execution.media.forEach((providerMedia, index) => {
                expect(providerMedia).toMatchObject({
                    schema_version: 1,
                    role: requests.batch.media[index].role,
                    ordinal: requests.batch.media[index].ordinal,
                    declared_media_type: expect.any(String),
                    size_bytes: expect.any(Number),
                });
                expect(providerMedia.sha256).toMatch(/^[0-9a-f]{64}$/);
            });
            expect(attempt.provider_execution.intent).toMatchObject({
                model_id: requests.batch.model_id,
                mode: "i2v",
                params: expect.objectContaining({ audio: false, prompt_extend: false, seed: -1, watermark: false }),
            });
        }

        const replayed = parseAtelierGenerationAdmissionAck(batchReplayedFixture, PROJECT_ID, {
            nodeId: NODE_ID,
            request: requests.batch,
            allowBatch: true,
        });
        expect(replayed.replayed).toBe(true);
        expect(replayed.new_candidate_ids).toEqual([]);
        expect(replayed.members).toEqual(ack.members);
    });

    it("rejects ordered ACKs whose source and provider media share the same cross-role disorder", () => {
        const sourceMedia = [
            { role: "reference_audio" as const, ordinal: 0, node_id: "audio", media_url: "uploads/audio.wav" },
            { role: "reference_image" as const, ordinal: 0, node_id: "image", media_url: "uploads/image.png" },
        ];
        const providerMedia = [
            { schema_version: 1, role: "reference_audio", ordinal: 0, declared_media_type: "audio/wav", sha256: "2".repeat(64), size_bytes: 12 },
            { schema_version: 1, role: "reference_image", ordinal: 0, declared_media_type: "image/png", sha256: "3".repeat(64), size_bytes: 34 },
        ];
        const outOfOrderAck = structuredClone(batchFixture);
        const outOfOrderRequest: AtelierOrderedVideoCandidateRequest = {
            ...requests.batch,
            mode: "r2v",
            media: sourceMedia,
        };
        for (const attempt of [
            ...outOfOrderAck.members.map(member => member.attempt),
            ...outOfOrderAck.project.generation_attempts.filter(attempt => "input_media" in attempt),
        ]) {
            attempt.input_media = sourceMedia;
            attempt.provider_execution.media = providerMedia;
            attempt.provider_execution.intent = { ...attempt.provider_execution.intent, mode: "r2v" };
        }

        expect(() => parseAtelierGenerationAdmissionAck(outOfOrderAck, PROJECT_ID, {
            nodeId: NODE_ID,
            request: outOfOrderRequest,
            creating: true,
            allowBatch: true,
        })).toThrow();
    });

    it("rejects legacy request expectations for ordered entrypoints", () => {
        expect(() => parseAtelierGenerationAdmissionAck(t2vFixture, PROJECT_ID, {
            nodeId: NODE_ID,
            request: {
                expected_revision: requests.t2v.expected_revision,
                client_request_id: requests.t2v.client_request_id,
                model_id: "wan/wan2.7-video#i2v",
                mode: "i2v",
                prompt: requests.t2v.prompt,
                first_frame: { node_id: "image", media_url: "uploads/image.png" },
            },
        })).toThrow();
    });
});
