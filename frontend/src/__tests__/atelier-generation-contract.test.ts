import { describe, expect, it } from "vitest";

import { parseAtelierProject } from "@/lib/atelierApi";
import {
    parseAtelierGenerationAdmissionAck,
    parseAtelierGenerationAttemptAck,
} from "@/lib/atelierGenerationApi";
import { parseAtelierNodeCommandAck } from "@/lib/atelierNodeApi";
import type { AtelierVideoCandidateRequest } from "@/lib/atelierTypes";
import acceptedFixture from "./fixtures/atelier-generation-accepted.json";
import completedFixture from "./fixtures/atelier-generation-completed.json";
import selectedFixture from "./fixtures/atelier-generation-selected.json";
import batchFixture from "./fixtures/atelier-generation-batch.json";
import batchReplayedFixture from "./fixtures/atelier-generation-batch-replayed.json";

const PROJECT_ID = "project-1";
const NODE_ID = "video";
const CANDIDATE_ID = "candidate-1";
const ATTEMPT_ID = "attempt-1";
const VIDEO_URL = "video/atelier-candidate-1-attempt-1.mp4";

const request: AtelierVideoCandidateRequest = {
    expected_revision: 0,
    client_request_id: "request-1",
    model_id: "wan/wan2.7-video#i2v",
    mode: "i2v",
    prompt: "Slow sunrise",
    first_frame: { node_id: "image", media_url: "uploads/frame.png" },
    params: { duration: 5, resolution: "1080p" },
};

const videoNode = (project: ReturnType<typeof parseAtelierProject>) => {
    const node = project.nodes.find(item => item.id === NODE_ID);
    expect(node).toBeDefined();
    return node!;
};

describe("Atelier generation backend wire contract fixtures", () => {
    it("parses backend batch and readback alongside the existing singleton receipt", () => {
        const batchRequest = { ...request, expected_revision: 2, client_request_id: "request-batch", batch_size: 2 };
        const ack = parseAtelierGenerationAdmissionAck(batchFixture, PROJECT_ID, {
            nodeId: NODE_ID, request: batchRequest, creating: true, allowBatch: true,
        });
        expect(ack).not.toHaveProperty("attempt");
        expect(ack).not.toHaveProperty("candidate");
        expect(ack.members.map(member => member.attempt.attempt_id)).toEqual(["attempt-2", "attempt-3"]);
        expect(ack.new_candidate_ids).toEqual(["candidate-2", "candidate-3"]);
        expect(ack.project.generation_request_receipts.map(receipt => receipt.schema_version)).toEqual([1, 2]);
        expect(videoNode(ack.project).data.selected_candidate_id).toBe(CANDIDATE_ID);
        const replayed = parseAtelierGenerationAdmissionAck(batchReplayedFixture, PROJECT_ID, {
            nodeId: NODE_ID, request: batchRequest, allowBatch: true,
        });
        expect(replayed.replayed).toBe(true);
        expect(replayed.members).toEqual(ack.members);
        expect(replayed.new_candidate_ids).toEqual([]);
        expect(() => parseAtelierGenerationAdmissionAck(batchFixture, PROJECT_ID)).toThrow();
    });

    it("parses backend-generated accepted admission ACK without selecting media", () => {
        const parsedProject = parseAtelierProject(acceptedFixture.project);
        expect(parsedProject.id).toBe(PROJECT_ID);

        const ack = parseAtelierGenerationAdmissionAck(acceptedFixture, PROJECT_ID, {
            nodeId: NODE_ID,
            request,
            creating: true,
        });

        expect(ack.replayed).toBe(false);
        expect(ack.reason).toBeNull();
        expect(ack.new_candidate_ids).toEqual([CANDIDATE_ID]);
        expect(ack.receipt).toMatchObject({
            project_id: PROJECT_ID,
            client_request_id: request.client_request_id,
            node_id: NODE_ID,
            candidate_id: CANDIDATE_ID,
            attempt_id: ATTEMPT_ID,
            base_revision: 0,
            committed_revision: 1,
        });
        expect(ack.attempt.status).toBe("pending");
        expect(ack.attempt.attempt_revision).toBe(0);
        expect(ack.attempt.provider_execution.phase).toBe("accepted");
        expect(ack.candidate).toMatchObject({ id: CANDIDATE_ID, status: "pending", video_url: null });
        expect(videoNode(ack.project).media_urls).toEqual([]);
        expect(videoNode(ack.project).data.selected_candidate_id).toBeUndefined();
        expect(JSON.stringify(acceptedFixture)).not.toContain("fixture-secret");
    });

    it("parses backend-generated completed attempt ACK before user selection", () => {
        const accepted = parseAtelierGenerationAdmissionAck(acceptedFixture, PROJECT_ID, {
            nodeId: NODE_ID,
            request,
            creating: true,
        });
        const completed = parseAtelierGenerationAttemptAck(completedFixture, PROJECT_ID, {
            attemptId: ATTEMPT_ID,
            candidateId: CANDIDATE_ID,
            expectedAttemptRevision: 0,
            executionFingerprint: accepted.attempt.provider_execution.fingerprint,
        });

        expect(completed.attempt.status).toBe("completed");
        expect(completed.attempt.attempt_revision).toBe(5);
        expect(completed.attempt.output_ref).toBe(VIDEO_URL);
        expect(completed.attempt.provider_execution.phase).toBe("completed");
        expect(completed.attempt.provider_execution.artifact).toMatchObject({
            relative_path: VIDEO_URL,
            size_bytes: 33,
        });
        expect(completed.candidate).toMatchObject({
            id: CANDIDATE_ID,
            attempt_id: ATTEMPT_ID,
            status: "completed",
            video_url: VIDEO_URL,
        });
        expect(videoNode(completed.project).media_urls).toEqual([]);
        expect(videoNode(completed.project).data.selected_candidate_id).toBeUndefined();
        expect(JSON.stringify(completedFixture)).not.toContain("fixture-secret");
    });

    it("parses backend-generated selected node ACK and binds media to the chosen candidate", () => {
        const selected = parseAtelierNodeCommandAck(selectedFixture, {
            projectId: PROJECT_ID,
            expectedRevision: 1,
            expectedIds: [NODE_ID],
        });
        const selectedProject = parseAtelierProject(selectedFixture.project);
        const node = videoNode(selected.project);
        const candidate = (node.data.candidates as Array<Record<string, unknown>>).find(item => item.id === CANDIDATE_ID);

        expect(selected.project).toEqual(selectedProject);
        expect(selected.node_ids).toEqual([NODE_ID]);
        expect(selected.project.revision).toBe(2);
        expect(node.media_urls).toEqual([VIDEO_URL]);
        expect(node.data.selected_candidate_id).toBe(CANDIDATE_ID);
        expect(candidate).toMatchObject({
            execution_contract: "atelier-provider-video-v1",
            id: CANDIDATE_ID,
            status: "completed",
            video_url: VIDEO_URL,
        });
        expect(JSON.stringify(selectedFixture)).not.toContain("fixture-secret");
    });
});
