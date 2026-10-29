// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { parseAtelierProject } from "@/lib/atelierApi";
import fixture from "./fixtures/atelier-project.json";
import backendWire from "./fixtures/atelier-agent-canvas-wire.json";

const hash = "a".repeat(64);
function canvasProject() {
    const binding = {
        schema_version: 1,
        normalized_arguments: { type: "video", title: "Take", prompt: "Rain", x: 0, y: 0, width: 320, height: 180 },
        target_state: { kind: "project", project_id: fixture.id, node_count: 0, node_ids: [] },
        canvas_authorization_ref: {
            schema_version: 1, project_id: fixture.id, turn_id: "turn", call_id: "call", approval_id: "approval",
            tool_name: "canvas.createVideoNode", tool_schema_version: "atelier.canvas.tools.v1",
            authorization_hash: hash, arguments_fingerprint: hash, target_fingerprint: hash,
            server_storage_identity: "storage", policy_snapshot_hash: hash,
        },
        created_at: 1,
    };
    const call = {
        call_id: "call", tool_name: "canvas.createVideoNode", arguments: { title: "Take", prompt: "Rain" },
        status: "approval_required", canvas_approval_binding: binding,
        canvas_approval_record: { schema_version: 1, approval_id: "approval", authorization_hash: hash, decision: "approved", decided_at: 2 },
    };
    return { ...structuredClone(fixture), agent_turns: [{ id: "turn", project_id: fixture.id, tool_calls: [call] }] };
}

type CanvasCall = ReturnType<typeof canvasProject>["agent_turns"][number]["tool_calls"][number];

describe("Agent canvas authorization wire boundary", () => {
    it("reads actual isolated storage results for create and update commands", () => {
        for (const project of [backendWire.create_approved_applied, backendWire.update_approved_applied]) {
            const parsed = parseAtelierProject(project);
            expect(parsed.agent_turns).toEqual(project.agent_turns);
            expect(parsed.agent_turns[0].pending_tool_calls).toEqual([]);
        }
    });

    it("loads canvas approvals separately from generation and retains denied history", () => {
        const project = canvasProject();
        expect(parseAtelierProject(project).agent_turns).toEqual(project.agent_turns);
        project.agent_turns[0].tool_calls[0].canvas_approval_record.decision = "revoked";
        expect(parseAtelierProject(project).agent_turns).toEqual(project.agent_turns);
    });

    it.each<[string, (call: CanvasCall) => void]>([
        ["project", (c: CanvasCall) => { c.canvas_approval_binding.canvas_authorization_ref.project_id = "other"; }],
        ["turn", (c: CanvasCall) => { c.canvas_approval_binding.canvas_authorization_ref.turn_id = "other"; }],
        ["call", (c: CanvasCall) => { c.canvas_approval_binding.canvas_authorization_ref.call_id = "other"; }],
        ["tool", (c: CanvasCall) => { c.tool_name = "generation.createVideoCandidates"; }],
        ["approval", (c: CanvasCall) => { c.canvas_approval_record.approval_id = "other"; }],
        ["hash", (c: CanvasCall) => { c.canvas_approval_record.authorization_hash = "b".repeat(64); }],
        ["unknown field", (c: CanvasCall) => { Object.assign(c.canvas_approval_binding, { provider_execution: {} }); }],
        ["unbound approval", (c: CanvasCall) => { Reflect.deleteProperty(c, "canvas_approval_binding"); }],
        ["target", (c: CanvasCall) => { c.canvas_approval_binding.target_state.project_id = "other"; }],
        ["target count", (c: CanvasCall) => { c.canvas_approval_binding.target_state.node_count = 1; }],
        ["node type", (c: CanvasCall) => { c.canvas_approval_binding.normalized_arguments.type = "image"; }],
        ["size", (c: CanvasCall) => { c.canvas_approval_binding.normalized_arguments.width = -1; }],
    ])("rejects a mismatched %s before enabling canvas actions", (_name, mutate) => {
        const project = canvasProject();
        mutate(project.agent_turns[0].tool_calls[0]);
        expect(() => parseAtelierProject(project)).toThrow();
    });

    it("rejects duplicate typed call identities while keeping unbound legacy calls readable", () => {
        const project = canvasProject();
        project.agent_turns[0].tool_calls.push(structuredClone(project.agent_turns[0].tool_calls[0]));
        expect(() => parseAtelierProject(project)).toThrow();
        const legacy = { ...project, agent_turns: [{ ...project.agent_turns[0], tool_calls: [{ call_id: "old", tool_name: "canvas.createVideoNode", status: "completed", result_snapshot: { node_ids: ["old"] } }] }] };
        expect(parseAtelierProject(legacy).agent_turns).toEqual(legacy.agent_turns);
    });
});
