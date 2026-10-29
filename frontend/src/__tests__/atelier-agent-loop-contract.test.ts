// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { parseAtelierProject } from "@/lib/atelierApi";
import fixture from "./fixtures/atelier-project.json";
import backendWire from "./fixtures/atelier-agent-loop-wire.json";

function loopProject() {
    return {
        ...structuredClone(fixture),
        agent_turns: [{
            id: "loop", project_id: fixture.id, tool_calls: [],
            user_message: "Plan a sequence", skill_name: null, max_iterations: 3,
            start_request_fingerprint: "a".repeat(64),
            loop_start_context: {
                schema_version: 1, user_message: "Plan a sequence", selected_node_id: null,
                planner: "deterministic_core", skill_name: null, max_iterations: 3,
            },
        }],
    };
}

describe("Agent loop start context wire boundary", () => {
    it("reads actual isolated loop pause, approval and completed history without dropping intent", () => {
        for (const project of [backendWire.paused, backendWire.approved, backendWire.completed]) {
            expect(parseAtelierProject(project).agent_turns).toEqual(project.agent_turns);
        }
        expect(backendWire.completed.agent_turns[0].iteration_count).toBe(3);
        expect(backendWire.completed.agent_turns[0].pending_tool_calls).toEqual([]);
    });

    it("preserves the exact saved intent and accepts legacy turns with no loop context", () => {
        const project = loopProject();
        expect(parseAtelierProject(project).agent_turns).toEqual(project.agent_turns);
        Reflect.deleteProperty(project.agent_turns[0], "loop_start_context");
        expect(parseAtelierProject(project).agent_turns[0]).not.toHaveProperty("loop_start_context");
    });

    it.each<[string, unknown]>([
        ["schema_version", 2], ["schema_version", "1"], ["user_message", 9],
        ["selected_node_id", {}], ["selected_node_id", ""], ["planner", "unregistered"], ["skill_name", false], ["skill_name", ""],
        ["max_iterations", 0], ["max_iterations", 9], ["max_iterations", 1.5],
        ["max_iterations", "3"], ["max_iterations", true], ["unknown", "ignored"],
    ])("rejects malformed %s in durable loop intent", (field, value) => {
        const project = loopProject();
        Object.assign(project.agent_turns[0].loop_start_context, { [field]: value });
        expect(() => parseAtelierProject(project)).toThrow();
    });

    it.each(["schema_version", "user_message", "selected_node_id", "planner", "skill_name", "max_iterations"])("requires the %s field without filling defaults", field => {
        const project = loopProject();
        Reflect.deleteProperty(project.agent_turns[0].loop_start_context, field);
        expect(() => parseAtelierProject(project)).toThrow();
    });

    it.each(["user_message", "skill_name", "max_iterations", "start_request_fingerprint"])("rejects inconsistent top-level %s", field => {
        const project = loopProject();
        Object.assign(project.agent_turns[0], { [field]: field === "max_iterations" ? 2 : "different" });
        expect(() => parseAtelierProject(project)).toThrow();
        Reflect.deleteProperty(project.agent_turns[0], field);
        expect(() => parseAtelierProject(project)).toThrow();
    });

    it("retains historical selected-node identity after the canvas target is deleted", () => {
        const project = loopProject();
        Object.assign(project.agent_turns[0].loop_start_context, { selected_node_id: "deleted-node", planner: "model_adapter" });
        expect(parseAtelierProject(project).agent_turns).toEqual(project.agent_turns);
    });
});
