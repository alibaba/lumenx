import { describe, expect, it } from "vitest";
import { mergeAtelierGenerationState } from "@/lib/atelierGenerationMerge";
import type { AtelierProject } from "@/lib/atelierTypes";
import fixture from "./fixtures/atelier-project.json";

function project(revision: number, attemptRevision: number, status = "processing"): AtelierProject {
    const output = status === "completed" ? "video/result.mp4" : null;
    const attempt = {
        schema_version: 1, project_id: "p", node_id: "n", candidate_id: "c", attempt_id: "a",
        attempt_revision: attemptRevision, status, error_code: null, output_ref: output,
        first_frame_source: { node_id: "image", media_url: "uploads/image.png" }, created_at: 1, updated_at: attemptRevision + 1,
        provider_execution: { schema_version: 1, attempt_id: "a", fingerprint: "f", provider_model_id: "wan2.7-i2v",
            intent: {}, media: [], scope: {}, credential_ref: "ref", phase: status === "completed" ? "completed" : "submitted" },
    };
    return {
        ...structuredClone(fixture), id: "p", revision, title: `canvas-${revision}`,
        nodes: [{ ...structuredClone(fixture.nodes[0]), id: "n", project_id: "p", type: "video", prompt: `draft-${revision}`,
            media_urls: [], data: { candidates: [{ execution_contract: "atelier-provider-video-v1", id: "c", attempt_id: "a",
                media_kind: "video", status, video_url: output, error_code: null, created_at: 1, updated_at: attemptRevision + 1 }] } }],
        generation_attempts: [attempt], generation_request_receipts: [{ attempt_id: "a", client_request_id: "request", committed_revision: 1 }],
    } as unknown as AtelierProject;
}

describe("Atelier independent generation revision merge", () => {
    it("keeps newer canvas edits while carrying newer execution state from an older project", () => {
        const canvas = project(4, 1);
        const completed = project(3, 5, "completed");
        const merged = mergeAtelierGenerationState(canvas, completed);
        expect(merged.revision).toBe(4);
        expect(merged.nodes[0].prompt).toBe("draft-4");
        expect(merged.generation_attempts[0].attempt_revision).toBe(5);
        expect(merged.nodes[0].data.candidates).toEqual(completed.nodes[0].data.candidates);
        expect(merged.nodes[0].media_urls).toEqual([]);
        expect(merged.nodes[0].data.selected_candidate_id).toBeUndefined();
        expect(canvas.generation_attempts[0].attempt_revision).toBe(1);
    });
    it("does not regress execution when a later GET contains stale progress", () => {
        const current = project(3, 5, "completed");
        const fetched = project(3, 1);
        expect(mergeAtelierGenerationState(fetched, current).generation_attempts[0].status).toBe("completed");
    });
    it("rejects different content at the same attempt revision", () => {
        expect(() => mergeAtelierGenerationState(project(3, 2), project(3, 2, "completed"))).toThrow(/revision/i);
    });
    it("rejects changed execution identity even with an advanced revision", () => {
        const other = project(3, 5);
        other.generation_attempts[0].provider_execution.credential_ref = "another-account";
        expect(() => mergeAtelierGenerationState(project(4, 1), other)).toThrow(/identity/i);
    });
    it("preserves a deleted owner without resurrecting a node", () => {
        const removed = project(5, 5, "completed");
        removed.nodes = [];
        expect(mergeAtelierGenerationState(removed, project(4, 4, "completed")).nodes).toEqual([]);
    });
    it("merges hidden candidate projections without moving them into visible candidates", () => {
        const hidden = project(4, 1);
        hidden.nodes[0].data.deleted_candidates = hidden.nodes[0].data.candidates;
        hidden.nodes[0].data.candidates = [];
        const result = mergeAtelierGenerationState(hidden, project(3, 5, "completed"));
        expect(result.nodes[0].data.candidates).toEqual([]);
        expect(result.nodes[0].data.deleted_candidates).toEqual(project(3, 5, "completed").nodes[0].data.candidates);
    });
    it("rejects missing known attempts or altered receipts in a newer project", () => {
        const missing = project(4, 1);
        missing.generation_attempts = [];
        expect(() => mergeAtelierGenerationState(missing, project(3, 1))).toThrow(/missing/i);
        const changed = project(4, 1);
        changed.generation_request_receipts[0].client_request_id = "other";
        expect(() => mergeAtelierGenerationState(changed, project(3, 1))).toThrow(/receipt/i);
    });
    it("keeps multiple batch receipts distinct by request identity", () => {
        const current = project(4, 1);
        const incoming = project(3, 5);
        const secondReceipt = { ...current.generation_request_receipts[0], client_request_id: "request-2", attempt_id: undefined, members: [{ attempt_id: "a", candidate_id: "c", execution_fingerprint: "f" }] };
        current.generation_request_receipts = [
            { ...current.generation_request_receipts[0], schema_version: 2, attempt_id: undefined, members: [{ attempt_id: "a", candidate_id: "c", execution_fingerprint: "f" }] },
            secondReceipt,
        ] as never;
        incoming.generation_request_receipts = structuredClone(current.generation_request_receipts);

        expect(() => mergeAtelierGenerationState(current, incoming)).not.toThrow();
    });

    it("rejects cross-project snapshots", () => {
        const other = project(3, 1); other.id = "other";
        expect(() => mergeAtelierGenerationState(project(4, 1), other)).toThrow(/project/i);
    });
});

describe("Atelier ordered generation merge identity", () => {
    it("uses ordered input media rather than legacy first_frame_source for execution identity", () => {
        const current = project(4, 1);
        const incoming = project(3, 5);
        const media = [{ role: "first_frame", ordinal: 0, node_id: "first", media_url: "uploads/first.png" }];
        for (const canvas of [current, incoming]) {
            const attempt = canvas.generation_attempts[0] as unknown as Record<string, unknown>;
            delete attempt.first_frame_source;
            attempt.input_contract_version = "ordered-media-v1";
            attempt.input_media = structuredClone(media);
            (attempt.provider_execution as Record<string, unknown>).media = structuredClone(media);
            canvas.generation_request_receipts[0].entrypoint = "atelier.provider_video.v2";
        }

        expect(mergeAtelierGenerationState(current, incoming).generation_attempts[0].attempt_revision).toBe(5);

        const drifted = structuredClone(incoming) as AtelierProject;
        const attempt = drifted.generation_attempts[0] as unknown as Record<string, unknown>;
        attempt.input_media = [{ role: "last_frame", ordinal: 0, node_id: "last", media_url: "uploads/last.png" }];
        expect(() => mergeAtelierGenerationState(current, drifted)).toThrow(/identity/i);
    });
});
