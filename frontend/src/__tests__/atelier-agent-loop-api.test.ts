// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { atelierAgentLoopApi, parseAtelierAgentLoopAck, startLoopStream, resumeLoopStream } from "@/lib/atelierAgentLoopApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import wire from "./fixtures/atelier-agent-loop-http-wire.json";
import productSkillWire from "./fixtures/atelier-product-skills-wire.json";

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));
const mutate = (value: unknown, path: (string | number)[], next: unknown, remove = false): void => {
    let target = value as Record<string | number, unknown>;
    for (const key of path.slice(0, -1)) target = target[key] as Record<string | number, unknown>;
    if (remove) delete target[path[path.length - 1]];
    else target[path[path.length - 1]] = next;
};
const last = wire.start_stream.at(-1)!;
const started = last.ack!;
const turnId = wire.start_request.client_turn_id;
const jsonResponse = (data: unknown) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
const sse = (events: unknown[]) => events.map(event => `data: ${JSON.stringify(event)}\n\n`).join("");
const response = (events: unknown[], cancel?: () => void, close = true) => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode(sse(events))); if (close) controller.close(); }, cancel,
}), { headers: { "Content-Type": "text/event-stream" } });

const skillSnapshot = productSkillWire.story_scout_snapshot;
const skillCatalog = productSkillWire.catalog;
const skillAckFor = () => {
    const raw = clone(started);
    const request = { ...wire.start_request, planner: "model_adapter" as const, skill_name: "story-scout", max_iterations: 1 };
    const context = { schema_version: 2, user_message: request.user_message, selected_node_id: request.selected_node_id, planner: "model_adapter", skill_name: "story-scout", max_iterations: 1, skill_snapshot: skillSnapshot };
    mutate(raw, ["turn", "skill_name"], "story-scout");
    mutate(raw, ["turn", "max_iterations"], 1);
    mutate(raw, ["turn", "loop_start_context"], context);
    mutate(raw, ["project", "agent_turns", 0, "skill_name"], "story-scout");
    mutate(raw, ["project", "agent_turns", 0, "max_iterations"], 1);
    mutate(raw, ["project", "agent_turns", 0, "loop_start_context"], context);
    return { request, raw };
};

beforeEach(() => {
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
});

describe("durable loop client", () => {
    it("accepts actual backend pause, resume, readback and replay ACKs", () => {
        for (const ack of [started, wire.resume_stream.at(-1)!.ack, wire.readback, wire.start_replay]) {
            expect(parseAtelierAgentLoopAck(ack, "p", { turnId }).turn.id).toBe(turnId);
        }
    });
    it("accepts model-planner loop starts and rejects planner-mismatched ACKs", async () => {
        const request = { ...wire.start_request, planner: "model_adapter" as const };
        const raw = clone(started);
        mutate(raw, ["turn", "loop_start_context", "planner"], "model_adapter");
        mutate(raw, ["project", "agent_turns", 0, "loop_start_context", "planner"], "model_adapter");
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse(raw)).mockResolvedValueOnce(jsonResponse(started));
        await expect(atelierAgentLoopApi.startLoop("p", request)).resolves.toMatchObject({ turn: { id: turnId } });
        expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual(request);
        await expect(atelierAgentLoopApi.startLoop("p", request)).rejects.toThrow();
    });

    it("accepts name-only product Skill starts and validates server-owned v2 snapshots", async () => {
        const { request, raw } = skillAckFor();
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse(raw)).mockResolvedValueOnce(jsonResponse(skillCatalog));

        await expect(atelierAgentLoopApi.startLoop("p", request)).resolves.toMatchObject({ turn: { id: turnId, skill_name: "story-scout" } });
        expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual(request);
        await expect(atelierAgentLoopApi.listProductSkills()).resolves.toEqual(skillCatalog);

        const mismatched = clone(raw);
        mutate(mismatched, ["turn", "loop_start_context", "skill_snapshot", "name"], "other-skill");
        mutate(mismatched, ["project", "agent_turns", 0, "loop_start_context", "skill_snapshot", "name"], "other-skill");
        expect(() => parseAtelierAgentLoopAck(mismatched, "p", { turnId, start: request })).toThrow();
    });

    it("binds start ACK to its saved start intent and uses a read-only recovery route", async () => {
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse(started)).mockResolvedValueOnce(jsonResponse(wire.readback));
        await atelierAgentLoopApi.startLoop("p", wire.start_request);
        await atelierAgentLoopApi.getLoop("p", turnId);
        expect(fetch.mock.calls[0][0]).toContain("/agent/loops");
        expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual(wire.start_request);
        expect(fetch.mock.calls[1][1]!.method).toBe("GET");
        expect(fetch.mock.calls[1][0]).toContain(`/agent/loops/${turnId}`);
        expect(fetch).toHaveBeenCalledTimes(2);
    });
    it("binds a canvas approval ACK to the exact saved approval tuple", async () => {
        const request = { ...wire.canvas_approval_request, decision: "approved" as const };
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(wire.canvas_approval_ack));
        const result = await atelierAgentLoopApi.recordCanvasApproval("p", request.turn_id, request);
        expect(result.turn.id).toBe(request.turn_id);
        expect(fetch.mock.calls[0][0]).toContain(`/agent/loops/${request.turn_id}/canvas_approval`);
        expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual(request);
        await expect(atelierAgentLoopApi.recordCanvasApproval("p", request.turn_id, { ...request, approval_id: "wrong" })).rejects.toThrow();
        await expect(atelierAgentLoopApi.recordCanvasApproval("p", request.turn_id, { ...request, decision: "denied" })).rejects.toThrow();
    });
    it("accepts the real canvas loop start and explicit resume", async () => {
        const request = wire.canvas_start_request;
        vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(wire.canvas_start_stream)).mockResolvedValueOnce(jsonResponse(wire.canvas_resume_ack));
        const start = await startLoopStream("p", request);
        const resumed = await atelierAgentLoopApi.resumeLoop("p", request.client_turn_id, wire.canvas_resume_request);
        expect(resumed.project.nodes.length).toBeGreaterThan(start.project.nodes.length);
    });
    it("sends only the saved turn identity for explicit resume", async () => {
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(wire.readback));
        await atelierAgentLoopApi.resumeLoop("p", turnId, wire.resume_request);
        expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({ turn_id: turnId });
        expect(fetch.mock.calls[0][0]).toContain(`/agent/loops/${turnId}/resume`);
    });
    it.each([
        { max_iterations: 0 }, { max_iterations: 9 }, { max_iterations: 1.5 },
        { planner: "unknown" }, { skill_name: "unavailable" }, { skill_name: "" }, { skill_name: {} }, { skill_snapshot: skillSnapshot }, { prompt_template: "do it" }, { expected_tools: ["canvas.readProject"] }, { unexpected: true },
        { expected_revision: true }, { selected_node_id: "" }, { client_turn_id: "../invalid" },
    ])("rejects unsupported/invalid start before fetch: %s", async patch => {
        const fetch = vi.spyOn(globalThis, "fetch");
        await expect(atelierAgentLoopApi.startLoop("p", { ...wire.start_request, ...patch } as never)).rejects.toThrow();
        expect(fetch).not.toHaveBeenCalled();
    });
    it("rejects mismatched/extended resume request before fetch", async () => {
        const fetch = vi.spyOn(globalThis, "fetch");
        await expect(atelierAgentLoopApi.resumeLoop("p", "other", wire.resume_request)).rejects.toThrow();
        await expect(atelierAgentLoopApi.resumeLoop("p", turnId, { ...wire.resume_request, prompt: "new" } as never)).rejects.toThrow();
        expect(fetch).not.toHaveBeenCalled();
    });
    it.each(["foreign_turn", "standalone", "extra_ack", "invalid_trace", "foreign_trace", "bad_iteration", "mismatched_saved_turn"])('rejects malformed ACK: %s', variant => {
        const raw = clone(started);
        if (variant === "foreign_turn") raw.turn.id = "other";
        if (variant === "standalone") { mutate(raw, ["turn", "loop_start_context"], undefined, true); mutate(raw, ["project", "agent_turns", 0, "loop_start_context"], undefined, true); }
        if (variant === "extra_ack") mutate(raw, ["unexpected"], true);
        if (variant === "invalid_trace") raw.events[0].type = "guess";
        if (variant === "foreign_trace") raw.events[0].turn_id = "other";
        if (variant === "bad_iteration") raw.events[0].iteration = -1;
        if (variant === "mismatched_saved_turn") mutate(raw, ["turn", "response"], "unsaved");
        expect(() => parseAtelierAgentLoopAck(raw, "p", { turnId })).toThrow();
    });
    it("rejects a start ACK for another saved intent", async () => {
        const raw = clone(started);
        mutate(raw, ["turn", "loop_start_context", "user_message"], "changed");
        mutate(raw, ["project", "agent_turns", 0, "loop_start_context", "user_message"], "changed");
        vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(raw));
        await expect(atelierAgentLoopApi.startLoop("p", wire.start_request)).rejects.toThrow();
    });
    it("rejects nested generation from another snapshot or another Agent call", () => {
        const raw = clone(wire.readback);
        mutate(raw, ["generation", "project", "revision"], 999);
        expect(() => parseAtelierAgentLoopAck(raw, "p", { turnId })).toThrow();
        const foreign = clone(wire.readback);
        mutate(foreign, ["generation", "attempt", "agent_authorization_ref", "turn_id"], "other");
        expect(() => parseAtelierAgentLoopAck(foreign, "p", { turnId })).toThrow();
    });
});

describe("loop streams reuse the bounded transport", () => {
    it("publishes real stage events and accepts approval pause / generation wait ACKs", async () => {
        const seen: string[] = [];
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(wire.start_stream)).mockResolvedValueOnce(response(wire.resume_stream));
        const start = await startLoopStream("p", wire.start_request, { onEvent: e => seen.push(e.type) });
        expect(start.turn.status).toBe("waiting_approval");
        expect(seen).toEqual(wire.start_stream.map(e => e.type));
        const resumed = await resumeLoopStream("p", turnId, wire.resume_request);
        expect(resumed.generation?.attempt.attempt_id).toBe(wire.readback.generation!.attempt.attempt_id);
        expect(fetch).toHaveBeenCalledTimes(2);
    });
    it.each(["missing_ack", "premature_ack", "foreign_identity", "unknown_event", "bad_replayed", "extra_field"])('rejects malformed SSE protocol: %s', variant => {
        const events = clone(wire.start_stream);
        if (variant === "missing_ack") events[events.length - 1].ack = null;
        if (variant === "premature_ack") mutate(events, [0, "ack"], clone(started));
        if (variant === "foreign_identity") events[0].turn_id = "other";
        if (variant === "unknown_event") events[0].type = "made_up";
        if (variant === "bad_replayed") mutate(events, [0, "replayed"], "false");
        if (variant === "extra_field") mutate(events, [0, "extra"], "no");
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(events));
        return expect(startLoopStream("p", wire.start_request)).rejects.toThrow();
    });
    it("returns the actual sanitized server error without retry or state adoption", async () => {
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(wire.error_stream));
        await expect(startLoopStream("p", wire.error_start_request)).rejects.toMatchObject({ code: "atelier_agent_request_invalid" });
        expect(fetch).toHaveBeenCalledTimes(1);
    });
    it("rejects a terminal label carrying a pending turn", async () => {
        const events = clone(wire.start_stream);
        events[events.length - 1].type = "loop_terminal";
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(events));
        await expect(startLoopStream("p", wire.start_request)).rejects.toThrow();
    });
    it("accepts live then terminal schedule-failure events and keeps the durable admission", async () => {
        const seen: string[] = [];
        const request = wire.schedule_failure_resume_request;
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(wire.schedule_failure_resume_stream));
        const ack = await resumeLoopStream("p", request.turn_id, request, { onEvent: e => seen.push(e.type) });
        expect(seen.slice(-2)).toEqual(["generation_schedule_failed", "generation_schedule_failed"]);
        expect(ack.schedule_error).toBe("generation_schedule_not_accepted");
        expect(ack.generation?.attempt.attempt_id).toBe(wire.schedule_failure_readback.generation!.attempt.attempt_id);
        expect(ack.scheduled).toBe(false);
        expect(fetch).toHaveBeenCalledTimes(1);
    });
    it("connection loss never retries a POST", async () => {
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(wire.start_stream.slice(0, -1)));
        await expect(startLoopStream("p", wire.start_request)).rejects.toMatchObject({ code: "atelier_agent_stream_missing_terminal" });
        expect(fetch).toHaveBeenCalledTimes(1);
    });
    it("aborts the reader after a stage callback and delivers no later ACK", async () => {
        const cancel = vi.fn(); const controller = new AbortController(); const seen: string[] = [];
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(wire.start_stream, cancel, false));
        await expect(startLoopStream("p", wire.start_request, { signal: controller.signal, onEvent: e => { seen.push(e.type); controller.abort(); } })).rejects.toThrow();
        expect(seen).toEqual(["loop_started"]); expect(cancel).toHaveBeenCalled();
    });
    it("rejects a runtime switch before the response is adopted", async () => {
        vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
            return response(wire.start_stream);
        });
        await expect(startLoopStream("p", wire.start_request)).rejects.toMatchObject({ code: "environment_changed" });
    });
});
