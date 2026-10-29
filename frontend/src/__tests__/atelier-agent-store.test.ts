// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAtelierStore } from "@/store/atelierStore";
import { atelierProjectApi, parseAtelierProject } from "@/lib/atelierApi";
import { listProjectAtelierAgentTurnRequests } from "@/lib/atelierAgentTurnRequest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierAgentToolCall, AtelierAgentTurnAck } from "@/lib/atelierAgentApi";
import type { AtelierAgentLoopAck } from "@/lib/atelierAgentLoopApi";
import type { AtelierProject } from "@/lib/atelierTypes";
import agentAcks from "./fixtures/atelier-agent-turn-acks.json";
import loopWire from "./fixtures/atelier-agent-loop-http-wire.json";
import productSkillWire from "./fixtures/atelier-product-skills-wire.json";
import fixture from "./fixtures/atelier-project.json";

const api = vi.hoisted(() => ({ startTurn: vi.fn(), getTurn: vi.fn(), recordApproval: vi.fn(), continueTurn: vi.fn(), cancelTurn: vi.fn() }));
const stream = vi.hoisted(() => ({ startTurnStream: vi.fn(), continueTurnStream: vi.fn() }));
const loopApi = vi.hoisted(() => ({ startLoop: vi.fn(), getLoop: vi.fn(), resumeLoop: vi.fn(), recordCanvasApproval: vi.fn() }));
const loopStream = vi.hoisted(() => ({ startLoopStream: vi.fn(), resumeLoopStream: vi.fn() }));
vi.mock("@/lib/atelierAgentApi", () => ({ atelierAgentApi: api }));
vi.mock("@/lib/atelierAgentStream", () => stream);
vi.mock("@/lib/atelierAgentLoopApi", async importOriginal => ({ ...(await importOriginal()), atelierAgentLoopApi: loopApi, startLoopStream: loopStream.startLoopStream, resumeLoopStream: loopStream.resumeLoopStream }));

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const state = () => useAtelierStore.getState();
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const ack = (name: keyof typeof agentAcks): AtelierAgentTurnAck => clone(agentAcks[name]) as AtelierAgentTurnAck;

function startedAckFor(request: { client_turn_id: string; user_message: string }): AtelierAgentTurnAck {
    const value = ack("started");
    value.turn.id = request.client_turn_id;
    value.turn.user_message = request.user_message;
    value.project.agent_turns[0].id = request.client_turn_id;
    value.project.agent_turns[0].user_message = request.user_message;
    value.events = value.events.map(event => ({ ...event, turn_id: request.client_turn_id }));
    value.call = (value.project.agent_turns[0].tool_calls as AtelierAgentToolCall[])[0] ?? value.call;
    return value;
}

function project(id = "p", revision = 0): AtelierProject {
    return parseAtelierProject({ ...structuredClone(fixture), id, revision, nodes: [], agent_turns: [], sequence: [], audio_tracks: [], exports: [], node_delete_receipts: [], node_upload_receipts: [], sequence_export_jobs: [], generation_attempts: [], generation_request_receipts: [] });
}

function seed(p = project()) {
    useAtelierStore.setState({ projects: [p], currentProject: p, selectedNodeId: null });
}

function pending() {
    return listProjectAtelierAgentTurnRequests({ environment: requireActiveRuntimeEnvironment(), projectId: "p" });
}

const loopAck = <T,>(value: T): T => clone(value);
type MutableLoopAck = { turn: Record<string, unknown>; events: Array<Record<string, unknown>>; project: { agent_turns: Array<Record<string, unknown>> } };

function loopStartAckFor(request: { client_turn_id: string; user_message: string; selected_node_id: string | null; planner?: "deterministic_core" | "model_adapter"; skill_name?: string | null; max_iterations?: number }): AtelierAgentLoopAck {
    const source = loopWire.start_stream.at(-1)?.ack;
    if (!source) throw new Error("loop fixture missing terminal ACK");
    const value = loopAck(source) as unknown as MutableLoopAck;
    const skillName = request.skill_name ?? null;
    const context = skillName === null
        ? { schema_version: 1, user_message: request.user_message, selected_node_id: request.selected_node_id, planner: request.planner ?? "deterministic_core", skill_name: null, max_iterations: request.max_iterations ?? 3 }
        : {
            schema_version: 2, user_message: request.user_message, selected_node_id: request.selected_node_id, planner: "model_adapter",
            skill_name: skillName, max_iterations: request.max_iterations ?? 3,
            skill_snapshot: { ...productSkillWire.story_scout_snapshot, name: skillName },
        };
    value.turn.id = request.client_turn_id;
    value.turn.user_message = request.user_message;
    value.turn.skill_name = skillName;
    value.turn.loop_start_context = context;
    value.events = value.events.map(event => ({ ...event, turn_id: request.client_turn_id }));
    value.project.agent_turns = value.project.agent_turns.map(item => item.id === "loop-http-1" ? value.turn : item);
    return value as unknown as AtelierAgentLoopAck;
}

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }

beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
    seed();
});

describe("Atelier Agent store", () => {
    it("persists stable start intent before POST and clears only a matching saved turn", async () => {
        api.startTurn.mockImplementation(async (_projectId, request) => {
            expect(pending()).toHaveLength(1);
            expect(pending()[0].request).toEqual(request);
            return startedAckFor(request);
        });

        const result = await state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null });

        const submitted = api.startTurn.mock.calls[0][1];
        expect(result.turn.id).toBe(submitted.client_turn_id);
        expect(api.startTurn).toHaveBeenCalledWith("p", {
            expected_revision: 0,
            client_turn_id: submitted.client_turn_id,
            user_message: "生成视频",
            selected_node_id: null,
        });
        expect(pending()).toEqual([]);
        expect(state().pendingAgentTurns).toEqual([]);
        expect(state().currentProject?.agent_turns).toHaveLength(1);
    });

    it("uses stream transport without changing start journal semantics", async () => {
        stream.startTurnStream.mockImplementation(async (_projectId, request) => {
            expect(pending()).toHaveLength(1);
            expect(pending()[0].request).toEqual(request);
            return startedAckFor(request);
        });

        const event = vi.fn();
        const result = await state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null }, { transport: "stream", onEvent: event });

        const submitted = stream.startTurnStream.mock.calls[0][1];
        expect(result.turn.id).toBe(submitted.client_turn_id);
        expect(api.startTurn).not.toHaveBeenCalled();
        expect(stream.startTurnStream).toHaveBeenCalledWith("p", submitted, expect.objectContaining({ onEvent: event }));
        expect(pending()).toEqual([]);
        expect(state().pendingAgentTurns).toEqual([]);
        expect(state().currentProject?.agent_turns).toHaveLength(1);
    });

    it("keeps a stream start journal when the stream ends without a terminal ACK", async () => {
        stream.startTurnStream.mockRejectedValueOnce(Object.assign(new Error("missing terminal"), { code: "atelier_agent_stream_missing_terminal" }));

        await expect(state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null }, { transport: "stream" })).rejects.toThrow("missing terminal");

        expect(pending()).toHaveLength(1);
        expect(state().currentProject?.agent_turns).toHaveLength(0);
    });

    it("clears exact stream start rejection even when the stream error has no HTTP status", async () => {
        stream.startTurnStream
            .mockRejectedValueOnce(Object.assign(new Error("planner rejected"), { code: "atelier_agent_start_rejected" }))
            .mockImplementationOnce(async (_projectId, request) => startedAckFor(request));

        await expect(state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null }, { transport: "stream" })).rejects.toThrow("planner rejected");
        expect(pending()).toEqual([]);
        expect(state().pendingAgentTurns).toEqual([]);

        await state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: "video" }, { transport: "stream" });
        expect(stream.startTurnStream).toHaveBeenCalledTimes(2);
    });


    it("does not clear a pending start when the ACK belongs to a different selected node intent", async () => {
        api.startTurn.mockImplementation(async (_projectId, request) => {
            const mismatched = startedAckFor(request);
            const call = mismatched.call as Record<string, unknown>;
            call.arguments = { ...(call.arguments as Record<string, unknown>), node_id: "video" };
            const binding = call.preview_binding as Record<string, unknown>;
            const publicRecord = binding.preview_public_record as Record<string, unknown>;
            publicRecord.node_id = "video";
            mismatched.turn.start_request_fingerprint = "b".repeat(64);
            mismatched.project.agent_turns[0] = mismatched.turn;
            return mismatched;
        });

        await expect(state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: "image" })).rejects.toThrow(/Atelier Agent start acknowledgement/);

        expect(pending()).toHaveLength(1);
        expect(state().currentProject?.agent_turns).toHaveLength(0);
    });

    it("keeps unknown start outcomes for GET readback and explicit original-payload retry", async () => {
        api.startTurn.mockRejectedValueOnce(Object.assign(new Error("lost"), { status: 503 }));
        await expect(state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null })).rejects.toThrow("lost");
        const original = pending()[0];
        expect(original.clientTurnId).toMatch(/^agent-turn-/);

        api.getTurn.mockRejectedValueOnce(Object.assign(new Error("missing"), { status: 404 }));
        await expect(state().readbackPendingAgentTurn(original.clientTurnId)).rejects.toThrow("missing");
        expect(pending()).toHaveLength(1);
        expect(api.startTurn).toHaveBeenCalledTimes(1);

        api.startTurn.mockImplementationOnce(async (_projectId, request) => startedAckFor(request));
        await state().retryPendingAgentTurn(original.clientTurnId);
        expect(api.startTurn).toHaveBeenLastCalledWith("p", original.request);
        expect(pending()).toEqual([]);
        expect(state().pendingAgentTurns).toEqual([]);
    });


    it("clears a definitive no-write start rejection so a null-selection planner error does not block future turns", async () => {
        api.startTurn
            .mockRejectedValueOnce(Object.assign(new Error("planner needs a selected video node"), { status: 422, code: "atelier_agent_start_rejected" }))
            .mockImplementationOnce(async (_projectId, request) => startedAckFor(request));

        await expect(state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null })).rejects.toThrow("planner needs a selected video node");
        expect(pending()).toEqual([]);
        expect(state().pendingAgentTurns).toEqual([]);

        const result = await state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: "video" });

        expect(result.turn.id).toBe(api.startTurn.mock.calls[1][1].client_turn_id);
        expect(api.startTurn).toHaveBeenCalledTimes(2);
        expect(api.startTurn.mock.calls[1][1]).toMatchObject({ selected_node_id: "video" });
    });


    it("keeps unknown 422 start outcomes pending because they may have written", async () => {
        api.startTurn.mockRejectedValueOnce(Object.assign(new Error("ambiguous validation failure"), { status: 422, code: "atelier_project_request_failed" }));

        await expect(state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null })).rejects.toThrow("ambiguous validation failure");

        expect(pending()).toHaveLength(1);
        await expect(state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: "video" })).rejects.toThrow();
    });

    it("blocks start when journal storage fails before POST", async () => {
        vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("quota"); });
        await expect(state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null })).rejects.toThrow();
        expect(api.startTurn).not.toHaveBeenCalled();
    });

    it("retains the old project's journal when a rejection arrives after navigation", async () => {
        let reject!: (error: Error) => void;
        api.startTurn.mockReturnValueOnce(new Promise((_resolve, rejectRequest) => { reject = rejectRequest; }));
        const start = state().startAgentTurn({ userMessage: "Generate", selectedNodeId: null });
        const rejected = expect(start).rejects.toThrow("Not prepared");
        expect(pending()).toHaveLength(1);
        vi.spyOn(atelierProjectApi, "getProject").mockResolvedValueOnce(project("other"));
        await state().switchProject("other");
        reject(Object.assign(new Error("Not prepared"), { status: 422, code: "atelier_agent_start_rejected" }));
        await rejected;
        expect(state().currentProject?.id).toBe("other");
        expect(pending()).toHaveLength(1);
    });

    it("reuses the same in-flight single-turn start and rejects different concurrent input", async () => {
        const result = deferred<AtelierAgentTurnAck>();
        api.startTurn.mockReturnValueOnce(result.promise);

        const first = state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null });
        const second = state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null });
        const different = state().startAgentTurn({ userMessage: "换个请求", selectedNodeId: null });
        await Promise.resolve();

        expect(second).toBe(first);
        await expect(different).rejects.toThrow("A different Atelier Agent turn is already pending");
        expect(api.startTurn).toHaveBeenCalledTimes(1);
        const submitted = api.startTurn.mock.calls[0][1];
        expect(pending()).toHaveLength(1);
        expect(pending()[0].request).toEqual(submitted);

        result.resolve(startedAckFor(submitted));
        await expect(first).resolves.toMatchObject({ turn: { id: submitted.client_turn_id } });
        expect(pending()).toEqual([]);
    });

    it("reuses the same in-flight guided loop start and rejects different concurrent input", async () => {
        const result = deferred<AtelierAgentLoopAck>();
        loopApi.startLoop.mockReturnValueOnce(result.promise);

        const first = state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video" });
        const second = state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video" });
        const different = state().startAgentLoop({ userMessage: "换个请求", selectedNodeId: "video" });
        const differentPlanner = state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video", planner: "model_adapter" });
        await Promise.resolve();

        expect(second).toBe(first);
        await expect(different).rejects.toThrow("A different Atelier Agent loop is already pending");
        await expect(differentPlanner).rejects.toThrow("A different Atelier Agent loop is already pending");
        expect(loopApi.startLoop).toHaveBeenCalledTimes(1);
        const submitted = loopApi.startLoop.mock.calls[0][1];
        expect(pending()).toHaveLength(1);
        expect(pending()[0].entrypoint).toBe("agent_loop");
        expect(pending()[0].request).toEqual(submitted);

        result.resolve(loopStartAckFor(submitted));
        await expect(first).resolves.toMatchObject({ turn: { id: submitted.client_turn_id } });
        expect(pending()).toEqual([]);
    });

    it("does not apply late start ACKs from another environment", async () => {
        const result = deferred<AtelierAgentTurnAck>();
        api.startTurn.mockReturnValue(result.promise);
        const first = state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null });
        await Promise.resolve();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        seed(project("other"));
        result.resolve(ack("started"));
        await expect(first).rejects.toThrow();
        expect(state().currentProject?.id).toBe("other");
    });


    it("uses skillName in guided loop singleflight and retries only the name-only original request", async () => {
        const result = deferred<AtelierAgentLoopAck>();
        loopApi.startLoop.mockReturnValueOnce(result.promise);

        const first = state().startAgentLoop({ userMessage: "Scout", selectedNodeId: null, planner: "model_adapter", skillName: "story-scout", maxIterations: 1 });
        const second = state().startAgentLoop({ userMessage: "Scout", selectedNodeId: null, planner: "model_adapter", skillName: "story-scout", maxIterations: 1 });
        const differentSkill = state().startAgentLoop({ userMessage: "Scout", selectedNodeId: null, planner: "model_adapter", skillName: "shot-review", maxIterations: 1 });
        await Promise.resolve();

        expect(second).toBe(first);
        await expect(differentSkill).rejects.toThrow("A different Atelier Agent loop is already pending");
        const submitted = loopApi.startLoop.mock.calls[0][1];
        expect(submitted).toMatchObject({ planner: "model_adapter", skill_name: "story-scout", max_iterations: 1 });
        expect(JSON.stringify(submitted)).not.toContain("skill_snapshot");
        result.resolve(loopStartAckFor(submitted));
        await expect(first).resolves.toMatchObject({ turn: { id: submitted.client_turn_id, skill_name: "story-scout" } });
        expect(pending()).toEqual([]);

        loopStream.startLoopStream.mockRejectedValueOnce(Object.assign(new Error("missing terminal"), { code: "atelier_agent_stream_missing_terminal" }));
        await expect(state().startAgentLoop({ userMessage: "Scout", selectedNodeId: null, planner: "model_adapter", skillName: "story-scout", maxIterations: 1 }, { transport: "stream" })).rejects.toThrow("missing terminal");
        const original = pending()[0];
        expect(original.request).toMatchObject({ skill_name: "story-scout" });
        expect(JSON.stringify(original.request)).not.toContain("skill_snapshot");
        loopApi.startLoop.mockImplementationOnce(async (_projectId, request) => loopStartAckFor(request));
        await state().retryPendingAgentTurn(original.clientTurnId);
        expect(loopApi.startLoop).toHaveBeenLastCalledWith("p", original.request);
    });

    it("starts model-planner guided loops with the exact saved planner", async () => {
        loopApi.startLoop.mockImplementationOnce(async (_projectId, request) => loopStartAckFor(request));

        const result = await state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video", planner: "model_adapter" });

        const submitted = loopApi.startLoop.mock.calls[0][1];
        expect(submitted).toMatchObject({ planner: "model_adapter", max_iterations: 3 });
        expect(result.turn.loop_start_context?.planner).toBe("model_adapter");
        expect(pending()).toEqual([]);
    });

    it("starts guided loops through the loop journal and clears only a matching loop ACK", async () => {
        loopStream.startLoopStream.mockImplementation(async (_projectId, request) => {
            expect(pending()).toHaveLength(1);
            expect(pending()[0].entrypoint).toBe("agent_loop");
            expect(pending()[0].request).toEqual(request);
            return loopStartAckFor(request);
        });

        const result = await state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video" }, { transport: "stream" });

        const submitted = loopStream.startLoopStream.mock.calls[0][1];
        expect(result.turn.id).toBe(submitted.client_turn_id);
        expect(loopStream.startLoopStream).toHaveBeenCalledWith("p", expect.objectContaining({
            expected_revision: 0, user_message: "生成视频", selected_node_id: "video", max_iterations: 3,
        }), expect.objectContaining({ onEvent: undefined }));
        expect(api.startTurn).not.toHaveBeenCalled();
        expect(pending()).toEqual([]);
        expect(state().currentProject?.agent_turns.some(turn => turn.id === submitted.client_turn_id)).toBe(true);
    });

    it("keeps lost guided loop starts for readback and same-payload retry", async () => {
        loopStream.startLoopStream.mockRejectedValueOnce(Object.assign(new Error("missing terminal"), { code: "atelier_agent_stream_missing_terminal" }));

        await expect(state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video" }, { transport: "stream" })).rejects.toThrow("missing terminal");
        const original = pending()[0];
        expect(original.entrypoint).toBe("agent_loop");

        loopApi.getLoop.mockRejectedValueOnce(Object.assign(new Error("missing"), { status: 404 }));
        await expect(state().readbackPendingAgentTurn(original.clientTurnId)).rejects.toThrow("missing");
        expect(pending()).toHaveLength(1);

        loopApi.startLoop.mockImplementationOnce(async (_projectId, request) => loopStartAckFor(request));
        await state().retryPendingAgentTurn(original.clientTurnId);
        expect(loopApi.startLoop).toHaveBeenCalledWith("p", original.request);
        expect(pending()).toEqual([]);
    });



    it("clears definitive no-write guided loop start rejection for sync and stream starts", async () => {
        loopApi.startLoop.mockRejectedValueOnce(Object.assign(new Error("loop rejected"), { status: 422, code: "atelier_agent_start_rejected" }));
        await expect(state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video" })).rejects.toThrow("loop rejected");
        expect(pending()).toEqual([]);
        expect(state().pendingAgentTurns).toEqual([]);

        loopStream.startLoopStream.mockRejectedValueOnce(Object.assign(new Error("stream loop rejected"), { code: "atelier_agent_start_rejected" }));
        await expect(state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video" }, { transport: "stream" })).rejects.toThrow("stream loop rejected");
        expect(pending()).toEqual([]);
        expect(state().pendingAgentTurns).toEqual([]);
    });

    it("blocks guided loop start when journal storage fails before POST", async () => {
        vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("quota"); });
        await expect(state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video" })).rejects.toThrow();
        expect(loopApi.startLoop).not.toHaveBeenCalled();
        expect(loopStream.startLoopStream).not.toHaveBeenCalled();
    });

    it("does not apply late guided loop start ACKs from another environment", async () => {
        const result = deferred<AtelierAgentLoopAck>();
        loopApi.startLoop.mockReturnValue(result.promise);
        const first = state().startAgentLoop({ userMessage: "生成视频", selectedNodeId: "video" });
        await Promise.resolve();
        const submitted = loopApi.startLoop.mock.calls[0][1];
        activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        seed(project("other"));
        result.resolve(loopStartAckFor(submitted));
        await expect(first).rejects.toThrow();
        expect(state().currentProject?.id).toBe("other");
    });

    it("resumes guided loops explicitly and records canvas approval from the canvas binding tuple", async () => {
        seed(parseAtelierProject(loopWire.canvas_approval_ack.project));
        loopApi.recordCanvasApproval.mockResolvedValueOnce(loopAck(loopWire.canvas_approval_ack));
        loopStream.resumeLoopStream.mockResolvedValueOnce(loopAck(loopWire.canvas_resume_ack));

        await state().recordAgentLoopCanvasApproval("canvas-loop-1", "0d1d265587294c78b101c2c5d9652476", "approved");
        expect(loopApi.recordCanvasApproval).toHaveBeenCalledWith("p", "canvas-loop-1", expect.objectContaining({
            turn_id: "canvas-loop-1", call_id: "0d1d265587294c78b101c2c5d9652476", decision: "approved",
            approval_id: "d4f9c7844fa446e391b724bd8ff1337b", authorization_hash: "830c57b22b7fc4c87d27063e0737074ff021d194cd4ed057199a23e6adf1fcf0",
        }));

        await state().resumeAgentLoop("canvas-loop-1", { transport: "stream" });
        expect(loopStream.resumeLoopStream).toHaveBeenCalledWith("p", "canvas-loop-1", { turn_id: "canvas-loop-1" }, expect.any(Object));
    });

    it("records approve and deny decisions from the persisted call tuple without continuing", async () => {
        const started = ack("started");
        seed(parseAtelierProject(started.project));
        api.recordApproval.mockResolvedValueOnce(ack("approved")).mockResolvedValueOnce(ack("denied"));

        await state().approveAgentCall("turn-http-1", "840a7ca5162e4ba0af14937cb229632e");
        expect(api.recordApproval).toHaveBeenCalledWith("p", "turn-http-1", expect.objectContaining({ decision: "approved" }));
        expect(api.continueTurn).not.toHaveBeenCalled();

        seed(parseAtelierProject(started.project));
        await state().denyAgentCall("turn-http-1", "840a7ca5162e4ba0af14937cb229632e");
        expect(api.recordApproval).toHaveBeenLastCalledWith("p", "turn-http-1", expect.objectContaining({ decision: "denied" }));
    });

    it("continues with the original generation request id and does not select candidates", async () => {
        const approved = ack("approved");
        seed(parseAtelierProject(approved.project));
        api.continueTurn.mockResolvedValueOnce(ack("admitted"));

        await state().continueAgentCall("turn-http-1", "840a7ca5162e4ba0af14937cb229632e");

        expect(api.continueTurn).toHaveBeenCalledWith("p", "turn-http-1", expect.objectContaining({ client_request_id: "322dd25f896b49028894ff3820cd9ab0" }));
        expect(state().currentProject?.generation_attempts).toHaveLength(1);
        expect(state().currentProject?.nodes.find(node => node.id === "video")?.media_urls).toEqual([]);
    });

    it("streams continue with the original generation request id and only adopts terminal ACKs", async () => {
        const approved = ack("approved");
        seed(parseAtelierProject(approved.project));
        const event = vi.fn();
        stream.continueTurnStream.mockResolvedValueOnce(ack("admitted"));

        await state().continueAgentCall("turn-http-1", "840a7ca5162e4ba0af14937cb229632e", { transport: "stream", onEvent: event });

        expect(api.continueTurn).not.toHaveBeenCalled();
        expect(stream.continueTurnStream).toHaveBeenCalledWith("p", "turn-http-1", expect.objectContaining({ client_request_id: "322dd25f896b49028894ff3820cd9ab0" }), expect.objectContaining({ onEvent: event }));
        expect(state().currentProject?.generation_attempts).toHaveLength(1);
    });

    it("unblocks writes after reading back the original admitted turn following stream loss", async () => {
        seed(parseAtelierProject(ack("approved").project));
        stream.continueTurnStream.mockRejectedValueOnce(new Error("missing terminal"));
        await expect(state().continueAgentCall("turn-http-1", "840a7ca5162e4ba0af14937cb229632e", { transport: "stream" })).rejects.toThrow();
        expect(state().needsRefresh).toBe(true);
        api.getTurn.mockResolvedValueOnce(ack("admitted"));
        await state().refreshAgentTurn("turn-http-1");
        expect(state().needsRefresh).toBe(false);
        expect(state().error).toBeNull();
        expect(state().currentProject?.generation_attempts).toHaveLength(1);
        expect(stream.continueTurnStream).toHaveBeenCalledTimes(1);
    });

    it("unblocks writes after lost-start readback verifies and clears its saved intent", async () => {
        let savedAck: AtelierAgentTurnAck | undefined;
        stream.startTurnStream.mockImplementationOnce(async (_projectId, request) => {
            savedAck = startedAckFor(request);
            throw new Error("missing terminal");
        });
        await expect(state().startAgentTurn({ userMessage: "生成视频", selectedNodeId: null }, { transport: "stream" })).rejects.toThrow();
        expect(state().needsRefresh).toBe(true);
        api.getTurn.mockResolvedValueOnce(savedAck);
        await state().readbackPendingAgentTurn();
        expect(state().needsRefresh).toBe(false);
        expect(pending()).toHaveLength(0);
        expect(state().error).toBeNull();
    });


    it("continues from the frozen preview request id even when a historical result snapshot is present", async () => {
        const approved = ack("approved");
        const call = approved.call as Record<string, unknown>;
        call.result_snapshot = { client_request_id: "historical-request", request_fingerprint: "f".repeat(64), attempt_id: "old-attempt", candidate_id: "old-candidate", committed_revision: 99 };
        const projectWithSnapshot = parseAtelierProject(approved.project);
        const toolCalls = projectWithSnapshot.agent_turns[0]?.tool_calls as AtelierAgentToolCall[] | undefined;
        if (!toolCalls?.[0]) throw new Error("fixture missing agent tool call");
        toolCalls[0] = call as AtelierAgentToolCall;
        seed(projectWithSnapshot);
        api.continueTurn.mockResolvedValueOnce(ack("admitted"));

        await state().continueAgentCall("turn-http-1", "840a7ca5162e4ba0af14937cb229632e");

        expect(api.continueTurn).toHaveBeenCalledWith("p", "turn-http-1", expect.objectContaining({ client_request_id: "322dd25f896b49028894ff3820cd9ab0" }));
    });

    it("cancels an agent turn explicitly", async () => {
        const started = ack("started");
        seed(parseAtelierProject(started.project));
        api.cancelTurn.mockResolvedValueOnce(ack("canceled"));
        await state().cancelAgentTurn("turn-http-1");
        expect(api.cancelTurn).toHaveBeenCalledWith("p", "turn-http-1", { turn_id: "turn-http-1" });
    });
});
