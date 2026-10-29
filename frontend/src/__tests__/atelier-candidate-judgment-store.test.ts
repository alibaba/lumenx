// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAtelierStore } from "@/store/atelierStore";
import { atelierProjectApi, parseAtelierProject } from "@/lib/atelierApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierNodeCommandAck, AtelierProject } from "@/lib/atelierTypes";
import wire from "./fixtures/atelier-candidate-judgment-wire.json";

const api = vi.hoisted(() => ({ setCandidateJudgment: vi.fn() }));
vi.mock("@/lib/atelierCandidateJudgmentApi", () => ({ atelierCandidateJudgmentApi: api }));
const state = () => useAtelierStore.getState();
const input = { verdict: "keep" as const, reason: "Keep this performance" };
const project = () => parseAtelierProject(structuredClone(wire.before));
const ack = (): AtelierNodeCommandAck => ({ project: parseAtelierProject(structuredClone(wire.ack.project)), node_ids: ["node-1"] });
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(done => { resolve = done; });
    return { promise, resolve };
}
beforeEach(() => {
    vi.restoreAllMocks(); vi.clearAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
    const p = project();
    useAtelierStore.setState({ projects: [p], currentProject: p, selectedNodeId: "node-1" });
});

describe("candidate judgment store ownership and recovery", () => {
    it("queues a snapshotted judgment behind a project edit using the new revision", async () => {
        const pending = deferred<AtelierProject>();
        vi.spyOn(atelierProjectApi, "updateProject").mockReturnValue(pending.promise);
        const editing = state().updateProject({ title: "Edited" });
        const requested = { ...input };
        api.setCandidateJudgment.mockResolvedValue({ ...ack(), project: { ...ack().project, revision: 5 } });
        const judging = state().setCandidateJudgment("node-1", "candidate-1", requested);
        requested.reason = "Changed after click";
        pending.resolve({ ...project(), title: "Edited", revision: 4 });
        await editing; await judging;
        expect(api.setCandidateJudgment).toHaveBeenCalledWith("p1", "node-1", "candidate-1", { ...input, expected_revision: 4 });
    });

    it("keeps a lost acknowledgement unresolved until GET refresh without automatic resubmission", async () => {
        api.setCandidateJudgment.mockRejectedValueOnce(new TypeError("Network disconnected"));
        await expect(state().setCandidateJudgment("node-1", "candidate-1", input)).rejects.toThrow("Network disconnected");
        expect(state().needsRefresh).toBe(true);
        expect(state().currentProject?.candidate_judgments).toEqual([]);
        await expect(state().setCandidateJudgment("node-1", "candidate-1", input)).rejects.toThrow("Refresh");
        const read = vi.spyOn(atelierProjectApi, "getProject").mockResolvedValue(ack().project);
        await state().refreshProject();
        expect(read).toHaveBeenCalledTimes(1);
        expect(api.setCandidateJudgment).toHaveBeenCalledTimes(1);
        expect(state().needsRefresh).toBe(false);
        expect(state().currentProject?.candidate_judgments).toEqual(ack().project.candidate_judgments);
    });

    it("does not navigate back when a judgment resolves after switching projects", async () => {
        const pending = deferred<AtelierNodeCommandAck>(); api.setCandidateJudgment.mockReturnValue(pending.promise);
        const saving = state().setCandidateJudgment("node-1", "candidate-1", input);
        await Promise.resolve();
        const other = { ...project(), id: "other", nodes: [], generation_attempts: [], generation_request_receipts: [] };
        vi.spyOn(atelierProjectApi, "getProject").mockResolvedValue(other);
        await state().switchProject("other"); pending.resolve(ack()); await saving;
        expect(state().currentProject?.id).toBe("other");
        expect(state().currentProject?.candidate_judgments).toEqual([]);
    });

    it("preserves the fresher project after navigating away and back before a delayed acknowledgement", async () => {
        const pending = deferred<AtelierNodeCommandAck>(); api.setCandidateJudgment.mockReturnValue(pending.promise);
        const saving = state().setCandidateJudgment("node-1", "candidate-1", input);
        await Promise.resolve();
        vi.spyOn(atelierProjectApi, "getProject")
            .mockResolvedValueOnce({ ...project(), id: "other", nodes: [], generation_attempts: [], generation_request_receipts: [] })
            .mockResolvedValueOnce({ ...ack().project, title: "Newer title", revision: 3 });
        await state().switchProject("other"); await state().switchProject("p1");
        pending.resolve(ack()); await saving;
        expect(state().currentProject?.title).toBe("Newer title");
        expect(state().currentProject?.revision).toBe(3);
    });

    it("rejects delayed judgment acknowledgement after environment identity changes", async () => {
        const pending = deferred<AtelierNodeCommandAck>(); api.setCandidateJudgment.mockReturnValue(pending.promise);
        const saving = state().setCandidateJudgment("node-1", "candidate-1", input);
        await Promise.resolve();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        pending.resolve(ack());
        await expect(saving).rejects.toThrow("Atelier environment or project changed");
        expect(state().currentProject).toBeNull();
    });
});
