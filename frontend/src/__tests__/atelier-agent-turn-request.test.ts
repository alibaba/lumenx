// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
    beginAtelierAgentLoopRequest,
    beginAtelierAgentTurnRequest,
    createAtelierAgentTurnRequestId,
    listProjectAtelierAgentTurnRequests,
    resolveAtelierAgentLoopRequest,
    resolveAtelierAgentTurnRequest,
} from "@/lib/atelierAgentTurnRequest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";

const owner = () => ({ environment: requireActiveRuntimeEnvironment(), projectId: "p" });

beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
});

describe("Atelier Agent turn start journal", () => {
    it("persists the exact start request and reuses the same id for identical retry", () => {
        const first = beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 4, clientTurnId: "turn-1", userMessage: "Make a take", selectedNodeId: "video" });
        const second = beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 4, clientTurnId: "turn-1", userMessage: "Make a take", selectedNodeId: "video" });

        expect(first.status).toBe("created");
        expect(second.status).toBe("pending");
        expect(second.request).toEqual(first.request);
        expect(listProjectAtelierAgentTurnRequests(owner()).map(item => item.request)).toEqual([{ expected_revision: 4, client_turn_id: "turn-1", user_message: "Make a take", selected_node_id: "video" }]);
    });

    it("rejects the same stable id with different payload instead of silently replacing it", () => {
        beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 4, clientTurnId: "turn-1", userMessage: "Make a take", selectedNodeId: "video" });

        expect(() => beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 4, clientTurnId: "turn-1", userMessage: "Different", selectedNodeId: "video" })).toThrow();
        expect(listProjectAtelierAgentTurnRequests(owner())).toHaveLength(1);
    });

    it("keeps project-level requests visible without a selected node", () => {
        beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 0, clientTurnId: "turn-none", userMessage: "Plan", selectedNodeId: null });
        const pending = listProjectAtelierAgentTurnRequests(owner());
        expect(pending).toHaveLength(1);
        expect(pending[0].request.selected_node_id).toBeNull();
    });

    it("clears only the exact owner and request tuple", () => {
        const stored = beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 1, clientTurnId: "turn-1", userMessage: "Make", selectedNodeId: null }).request;
        expect(resolveAtelierAgentTurnRequest({ ...owner(), clientTurnId: "turn-1", request: { ...stored.request, user_message: "other" } })).toBe(false);
        expect(listProjectAtelierAgentTurnRequests(owner())).toHaveLength(1);
        expect(resolveAtelierAgentTurnRequest({ ...owner(), clientTurnId: "turn-1", request: stored.request })).toBe(true);
        expect(listProjectAtelierAgentTurnRequests(owner())).toEqual([]);
    });

    it("fails closed on corrupt same-owner storage but ignores a different environment", () => {
        const current = owner();
        const good = beginAtelierAgentTurnRequest({ ...current, expectedRevision: 1, clientTurnId: "turn-1", userMessage: "Make", selectedNodeId: null }).request;
        const key = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index)).find(item => item?.includes("turn-1"));
        expect(key).toBeTruthy();
        localStorage.setItem(key!, "{broken");
        expect(() => listProjectAtelierAgentTurnRequests(current)).toThrow();

        localStorage.clear();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 1, clientTurnId: "foreign", userMessage: "Other", selectedNodeId: null });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
        expect(listProjectAtelierAgentTurnRequests(current)).toEqual([]);
        expect(good.clientTurnId).toBe("turn-1");
    });


    it("stores loop start requests beside legacy single-turn records without replacing them", () => {
        beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 1, clientTurnId: "turn-1", userMessage: "One step", selectedNodeId: null });
        const loop = beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "loop-1", userMessage: "Guided", selectedNodeId: "video", maxIterations: 3 });

        const pending = listProjectAtelierAgentTurnRequests(owner());
        expect(pending.map(item => item.entrypoint)).toEqual(["agent_loop", "single_turn"]);
        expect(loop.request.entrypoint).toBe("agent_loop");
        expect(loop.request.request).toEqual({ expected_revision: 1, client_turn_id: "loop-1", user_message: "Guided", selected_node_id: "video", max_iterations: 3 });
        expect(resolveAtelierAgentLoopRequest({ ...owner(), clientTurnId: "loop-1", request: loop.request.request })).toBe(true);
        expect(listProjectAtelierAgentTurnRequests(owner()).map(item => item.clientTurnId)).toEqual(["turn-1"]);
    });

    it("round-trips name-only product Skill loop requests without catalog snapshots", () => {
        const loop = beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "loop-skill", userMessage: "Scout", selectedNodeId: null, planner: "model_adapter", skillName: "story-scout", maxIterations: 1 });

        expect(loop.request.request).toEqual({ expected_revision: 1, client_turn_id: "loop-skill", user_message: "Scout", selected_node_id: null, planner: "model_adapter", skill_name: "story-scout", max_iterations: 1 });
        expect(JSON.stringify(loop.request)).not.toContain("prompt_template");
        expect(JSON.stringify(loop.request)).not.toContain("skill_snapshot");
        expect(listProjectAtelierAgentTurnRequests(owner())[0].request).toEqual(loop.request.request);
        expect(beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "loop-skill", userMessage: "Scout", selectedNodeId: null, planner: "model_adapter", skillName: "story-scout", maxIterations: 1 }).status).toBe("pending");
        expect(resolveAtelierAgentLoopRequest({ ...owner(), clientTurnId: "loop-skill", request: loop.request.request })).toBe(true);
    });

    it("keeps old null and absent Skill loop journals compatible", () => {
        const absent = beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "loop-absent", userMessage: "Guided", selectedNodeId: null, planner: "model_adapter", maxIterations: 3 });
        const explicitNull = beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "loop-null", userMessage: "Guided", selectedNodeId: null, planner: "model_adapter", skillName: null, maxIterations: 3 });

        expect(absent.request.request).not.toHaveProperty("skill_name");
        expect(explicitNull.request.request).toMatchObject({ skill_name: null });
        expect(listProjectAtelierAgentTurnRequests(owner()).map(item => item.clientTurnId)).toEqual(["loop-absent", "loop-null"]);
    });

    it("rejects product Skill loop journals that are not name-only model_adapter requests", () => {
        expect(() => beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "loop-bad", userMessage: "Scout", selectedNodeId: null, skillName: "story-scout" })).toThrow();
        expect(() => beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "loop-empty", userMessage: "Scout", selectedNodeId: null, planner: "model_adapter", skillName: "" })).toThrow();
        localStorage.clear();
        beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "forged", userMessage: "Scout", selectedNodeId: null, planner: "model_adapter", skillName: "story-scout", maxIterations: 1 });
        const key = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index)).find(item => item?.includes("forged"));
        expect(key).toBeTruthy();
        const stored = JSON.parse(localStorage.getItem(key!) ?? "{}");
        stored.request.skill_snapshot = { name: "story-scout" };
        localStorage.setItem(key!, JSON.stringify(stored));
        expect(() => listProjectAtelierAgentTurnRequests(owner())).toThrow();
    });

    it("persists model-planner loop requests and treats planner as part of the stable payload", () => {
        const loop = beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "loop-model", userMessage: "Guided", selectedNodeId: "video", planner: "model_adapter", maxIterations: 3 });

        expect(loop.request.request).toEqual({ expected_revision: 1, client_turn_id: "loop-model", user_message: "Guided", selected_node_id: "video", planner: "model_adapter", max_iterations: 3 });
        expect(() => beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "loop-model", userMessage: "Guided", selectedNodeId: "video", maxIterations: 3 })).toThrow();
        expect(resolveAtelierAgentLoopRequest({ ...owner(), clientTurnId: "loop-model", request: { ...loop.request.request, planner: "deterministic_core" } })).toBe(false);
        expect(listProjectAtelierAgentTurnRequests(owner())).toHaveLength(1);
    });

    it("rejects same-id conflicts across single-turn and loop entrypoints", () => {
        beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 1, clientTurnId: "same-id", userMessage: "One step", selectedNodeId: null });

        expect(() => beginAtelierAgentLoopRequest({ ...owner(), expectedRevision: 1, clientTurnId: "same-id", userMessage: "Guided", selectedNodeId: null, maxIterations: 3 })).toThrow();
        expect(listProjectAtelierAgentTurnRequests(owner())).toHaveLength(1);
    });

    it("blocks start when storage cannot persist the journal", () => {
        vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("quota"); });
        expect(() => beginAtelierAgentTurnRequest({ ...owner(), expectedRevision: 1, clientTurnId: "turn-1", userMessage: "Make", selectedNodeId: null })).toThrow();
    });

    it("creates stable agent turn ids within the shared request id alphabet", () => {
        expect(createAtelierAgentTurnRequestId()).toMatch(/^agent-turn-[A-Za-z0-9._:-]+$/);
    });
});
