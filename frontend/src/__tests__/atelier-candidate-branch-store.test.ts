// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAtelierStore } from "@/store/atelierStore";
import { atelierProjectApi, parseAtelierProject } from "@/lib/atelierApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import wire from "./fixtures/atelier-generation-selected.json";
import type { AtelierNode, AtelierNodeCommandAck, AtelierProject } from "@/lib/atelierTypes";

const api = vi.hoisted(() => ({ branchCandidate: vi.fn() }));
vi.mock("@/lib/atelierCandidateBranchApi", () => ({ atelierCandidateBranchApi: api }));

const state = () => useAtelierStore.getState();
const HASH = "a".repeat(64);
const source = () => parseAtelierProject(structuredClone(wire.project));
const branchNode = (): AtelierNode => ({
    id: "branch-1", project_id: "project-1", type: "video", title: "Branch take", prompt: "Frozen prompt", status: "draft",
    x: 420, y: 0, width: 320, height: 180, source_project_id: null, frame_id: null, asset_id: null, video_task_id: null,
    media_urls: [], data: { branch_source: { schema_version: 1, origin_project_id: "project-1", origin_node_id: "video", origin_candidate_id: "candidate-1", media_kind: "video", media_url: "uploads/branch-candidate-1.mp4", media_sha256: HASH, media_size_bytes: 1234, recipe_status: "available" } },
    created_by: "user", created_at: 30, updated_at: 30,
});
const ack = (revision = 3): AtelierNodeCommandAck => {
    const p = source();
    return { project: { ...p, revision, nodes: [...p.nodes, branchNode()] }, node_ids: ["branch-1"] };
};
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(done => { resolve = done; });
    return { promise, resolve };
}

beforeEach(() => {
    vi.restoreAllMocks(); vi.clearAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
    const p = source();
    useAtelierStore.setState({ projects: [p], currentProject: p, selectedNodeId: "video", needsRefresh: false, isSaving: false, error: null });
});

describe("candidate branch store ownership and recovery", () => {
    it("queues behind project edits, snapshots the candidate id, and selects the ACK draft", async () => {
        const pending = deferred<AtelierProject>();
        vi.spyOn(atelierProjectApi, "updateProject").mockReturnValue(pending.promise);
        const editing = state().updateProject({ title: "Edited" });
        api.branchCandidate.mockResolvedValue(ack(5));
        const branching = state().branchFromCandidate("video", "candidate-1");
        pending.resolve({ ...source(), title: "Edited", revision: 4 });
        await editing; await branching;
        expect(api.branchCandidate).toHaveBeenCalledWith("project-1", "video", "candidate-1", { expected_revision: 4 }, expect.objectContaining({ revision: 4 }));
        expect(state().selectedNodeId).toBe("branch-1");
    });

    it("keeps lost branch ACK unresolved until GET refresh and does not replay POST", async () => {
        api.branchCandidate.mockRejectedValueOnce(new TypeError("Network disconnected"));
        await expect(state().branchFromCandidate("video", "candidate-1")).rejects.toThrow("Network disconnected");
        expect(state().needsRefresh).toBe(true);
        await expect(state().branchFromCandidate("video", "candidate-1")).rejects.toThrow("Refresh");
        vi.spyOn(atelierProjectApi, "getProject").mockResolvedValue(ack().project);
        await state().refreshProject();
        expect(api.branchCandidate).toHaveBeenCalledTimes(1);
        expect(state().needsRefresh).toBe(false);
    });

    it("does not select a delayed branch after navigating away and back", async () => {
        const pending = deferred<AtelierNodeCommandAck>();
        api.branchCandidate.mockReturnValue(pending.promise);
        const branching = state().branchFromCandidate("video", "candidate-1");
        await Promise.resolve();
        vi.spyOn(atelierProjectApi, "getProject")
            .mockResolvedValueOnce({ ...source(), id: "other", nodes: [], generation_attempts: [], generation_request_receipts: [] })
            .mockResolvedValueOnce({ ...source(), revision: 4 });
        await state().switchProject("other");
        await state().switchProject("project-1");
        pending.resolve(ack());
        await branching;
        expect(state().currentProject?.id).toBe("project-1");
        expect(state().selectedNodeId).not.toBe("branch-1");
    });
});
