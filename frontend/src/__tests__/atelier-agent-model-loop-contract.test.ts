// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { parseAtelierProject } from "@/lib/atelierApi";
import { atelierAgentLoopApi, parseAtelierAgentLoopAck } from "@/lib/atelierAgentLoopApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import wire from "./fixtures/atelier-agent-model-loop-http-wire.json";

beforeEach(() => {
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
});

describe("actual model-backed loop read contract", () => {
    it("preserves the settled model invocation through project and loop decoding", () => {
        const project = parseAtelierProject(wire.project);
        const turnId = wire.start.turn.id;
        const turn = project.agent_turns.find(item => item.id === turnId);
        expect(turn).toEqual(wire.start.turn);
        expect(turn?.loop_start_context).toMatchObject({ planner: "model_adapter" });
        expect(turn?.planner_invocations).toEqual(wire.start.turn.planner_invocations);
        for (const ack of [wire.start, wire.read]) {
            expect(parseAtelierAgentLoopAck(ack, project.id, { turnId }).turn).toEqual(turn);
        }
    });

    it("refreshes the original model turn with one GET and no new planner request", async () => {
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(wire.read), {
            headers: { "Content-Type": "application/json" },
        }));
        const result = await atelierAgentLoopApi.getLoop(wire.project.id, wire.start.turn.id);
        expect(result.turn.planner_invocations).toEqual(wire.start.turn.planner_invocations);
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(fetch.mock.calls[0][0]).toContain(`/atelier/projects/${wire.project.id}/agent/loops/${wire.start.turn.id}`);
        expect(fetch.mock.calls[0][1]?.method).toBe("GET");
    });

    it("refuses a loop ACK whose invocation differs from the saved project turn", () => {
        const ack = structuredClone(wire.read);
        ack.turn.planner_invocations[0].charged_completion_tokens += 1;
        expect(() => parseAtelierAgentLoopAck(ack, wire.project.id, { turnId: wire.start.turn.id })).toThrow();
    });
});
