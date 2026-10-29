// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API_URL } from "@/lib/apiUrl";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { atelierAgentApi, parseAtelierAgentTurnAck } from "@/lib/atelierAgentApi";
import backendWireProject from "./fixtures/atelier-agent-authorization-wire.json";
import agentTurnAcks from "./fixtures/atelier-agent-turn-acks.json";

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const HASH = "a".repeat(64);
const AUTH_HASH = "c678edca72a86fa8beb0e62ce98ed59ae2f68d0ffc95a0457c665c7d11e5323d";
const START_HASH = "b".repeat(64);

const response = (value: unknown, status = 200): Response => new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
});

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

const turn = (overrides: Record<string, unknown> = {}) => ({
    id: "turn-1",
    project_id: "project-1",
    user_message: "Generate a slow sunrise shot",
    preview: false,
    session_id: null,
    status: "waiting_approval",
    start_request_fingerprint: START_HASH,
    tool_calls: [
        {
            call_id: "call-1",
            tool_name: "generation.createVideoCandidates",
            arguments: { node_id: "video", prompt: "Slow sunrise" },
            status: "approval_required",
            approval_required: true,
            approval_granted: false,
            error: null,
            result_snapshot: null,
            preview_binding: null,
            approval_record: null,
            created_at: 10,
            completed_at: null,
        },
    ],
    node_reservations: {},
    completion_tokens_used: 0,
    completion_tokens_reserved: 0,
    generation_cost_quotes: {},
    created_at: 10,
    completed_at: null,
    response: null,
    iteration_count: 0,
    messages_history: [],
    max_iterations: 3,
    pending_tool_calls: [],
    skill_name: null,
    iterations: [],
    ...overrides,
});

const project = (overrides: Record<string, unknown> = {}) => ({
    id: "project-1",
    title: "Canvas",
    description: "",
    source_project_id: null,
    nodes: [],
    agent_policy: {
        approval_mode: "untrusted",
        allowed_tools: [],
        max_nodes_per_action: 8,
        max_tokens_per_turn: 2048,
        enable_reviewer: false,
        max_generation_cost_per_turn: 5,
        updated_at: 1,
    },
    agent_turns: [turn()],
    sequence: [],
    audio_tracks: [],
    exports: [],
    node_delete_receipts: [],
    node_upload_receipts: [],
    sequence_export_jobs: [],
    generation_attempts: [],
    generation_request_receipts: [],
    agent_compaction_summary: null,
    compacted_through_turn_id: null,
    revision: 0,
    created_at: 1,
    updated_at: 1,
    ...overrides,
});

const event = (overrides: Record<string, unknown> = {}) => ({
    type: "turn_preview_prepared",
    project_id: "project-1",
    turn_id: "turn-1",
    call_id: "call-1",
    approval_id: null,
    attempt_id: null,
    candidate_id: null,
    message: null,
    ...overrides,
});

const ack = (overrides: Record<string, unknown> = {}) => ({
    project: project(),
    turn: turn(),
    call: null,
    generation: null,
    events: [event()],
    replayed: false,
    scheduled: false,
    schedule_error: null,
    ...overrides,
});

const wireProject = () => clone(backendWireProject) as Record<string, unknown>;



const callIdentity = (raw: Record<string, unknown>) => {
    const call = raw.call as Record<string, unknown>;
    return {
        turnId: (raw.turn as Record<string, unknown>).id as string,
        callId: call.call_id as string,
    };
};

const approvalIdentity = (raw: Record<string, unknown>) => {
    const call = raw.call as Record<string, unknown>;
    const approval = call.approval_record as Record<string, unknown>;
    return {
        turnId: (raw.turn as Record<string, unknown>).id as string,
        callId: call.call_id as string,
        approvalId: approval.approval_id as string,
        authorizationHash: approval.authorization_hash as string,
        approvalDecision: approval.decision as "approved" | "denied" | "revoked",
    };
};

const savedCallInAck = (raw: Record<string, unknown>): Record<string, unknown> => {
    const p = raw.project as Record<string, unknown>;
    const turns = p.agent_turns as Array<Record<string, unknown>>;
    const t = turns[0];
    const calls = t.tool_calls as Array<Record<string, unknown>>;
    return calls[0];
};

const turnCallInAck = (raw: Record<string, unknown>): Record<string, unknown> => {
    const t = raw.turn as Record<string, unknown>;
    const calls = t.tool_calls as Array<Record<string, unknown>>;
    return calls[0];
};

const wireTurnAck = () => {
    const p = wireProject();
    const turns = p.agent_turns as Array<Record<string, unknown>>;
    const t = turns[0];
    const calls = t.tool_calls as Array<Record<string, unknown>>;
    const call = calls[0];
    const attempts = p.generation_attempts as Array<Record<string, unknown>>;
    const receipts = p.generation_request_receipts as Array<Record<string, unknown>>;
    const nodes = p.nodes as Array<Record<string, unknown>>;
    const video = nodes.find(node => node.id === "video") as Record<string, unknown>;
    const data = video.data as Record<string, unknown>;
    const candidates = data.candidates as Array<Record<string, unknown>>;
    return {
        project: p,
        turn: t,
        call,
        generation: {
            project: p,
            receipt: receipts[0],
            attempt: attempts[0],
            candidate: candidates[0],
            replayed: false,
            new_candidate_ids: [candidates[0].id],
            reason: null,
        },
        events: [
            {
                type: "generation_admitted",
                project_id: "p",
                turn_id: "turn",
                call_id: "call",
                approval_id: "approval",
                attempt_id: attempts[0].attempt_id,
                candidate_id: candidates[0].id,
                message: null,
            },
        ],
        replayed: false,
        scheduled: true,
        schedule_error: null,
    };
};

beforeEach(() => {
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
    vi.restoreAllMocks();
});

describe("Atelier agent turn API", () => {
    it("preserves only the explicit unsaved-start error code through the shared transport", async () => {
        const fetchMock = vi.spyOn(globalThis, "fetch")
            .mockResolvedValueOnce(response({ detail: { code: "atelier_agent_start_rejected", detail: "Not prepared" } }, 422))
            .mockResolvedValueOnce(response({ detail: { code: "atelier_agent_request_invalid", detail: "Unknown outcome" } }, 422))
            .mockResolvedValueOnce(response({ detail: { code: "atelier_agent_start_rejected", detail: "Wrong operation" } }, 422));
        const request = { expected_revision: 0, client_turn_id: "turn-1", user_message: "Generate", selected_node_id: "video" };
        await expect(atelierAgentApi.startTurn("project-1", request)).rejects.toMatchObject({ code: "atelier_agent_start_rejected", status: 422 });
        await expect(atelierAgentApi.startTurn("project-1", request)).rejects.toMatchObject({ code: "atelier_project_request_failed", status: 422 });
        await expect(atelierAgentApi.getTurn("project-1", "turn-1")).rejects.toMatchObject({ code: "atelier_project_request_failed", status: 422 });
        expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    it("starts a turn with the exact stable retry identity body", async () => {
        const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(ack()));

        const result = await atelierAgentApi.startTurn("project-1", {
            expected_revision: 0,
            client_turn_id: "turn-1",
            user_message: "Generate a slow sunrise shot",
            selected_node_id: "video",
        });

        expect(result.turn.id).toBe("turn-1");
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock.mock.calls[0][0]).toBe(`${API_URL}/atelier/projects/project-1/agent/turns`);
        expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });
        expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
            expected_revision: 0,
            client_turn_id: "turn-1",
            user_message: "Generate a slow sunrise shot",
            selected_node_id: "video",
        });
    });

    it("rejects path/body turn identity mismatch before approval, continue, or cancel requests", async () => {
        const fetchMock = vi.spyOn(globalThis, "fetch");

        await expect(atelierAgentApi.recordApproval("project-1", "turn-path", {
            expected_revision: 1,
            turn_id: "turn-body",
            call_id: "call-1",
            approval_id: "approval-1",
            authorization_hash: HASH,
            decision: "approved",
        })).rejects.toMatchObject({ code: "atelier_agent_ack_mismatch" });
        await expect(atelierAgentApi.continueTurn("project-1", "turn-path", {
            expected_revision: 1,
            turn_id: "turn-body",
            call_id: "call-1",
            approval_id: "approval-1",
            authorization_hash: HASH,
            client_request_id: "request-1",
        })).rejects.toMatchObject({ code: "atelier_agent_ack_mismatch" });
        await expect(atelierAgentApi.cancelTurn("project-1", "turn-path", { turn_id: "turn-body" })).rejects.toMatchObject({ code: "atelier_agent_ack_mismatch" });
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("parses continue ACKs with nested generation consistency and exact tuple matching", async () => {
        const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(wireTurnAck()));

        const result = await atelierAgentApi.continueTurn("p", "turn", {
            expected_revision: 2,
            turn_id: "turn",
            call_id: "call",
            approval_id: "approval",
            authorization_hash: AUTH_HASH,
            client_request_id: "request-1",
        });

        expect(fetchMock.mock.calls[0][0]).toBe(`${API_URL}/atelier/projects/p/agent/turns/turn/continue`);
        expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
            expected_revision: 2,
            turn_id: "turn",
            call_id: "call",
            approval_id: "approval",
            authorization_hash: AUTH_HASH,
            client_request_id: "request-1",
        });
        expect(result.call?.call_id).toBe("call");
        expect(result.generation?.attempt.schema_version).toBe(2);
        expect(result.generation?.receipt.client_request_id).toBe("request-1");
        expect(result.scheduled).toBe(true);
    });

    it("rejects nested generation or returned call mismatches", () => {
        const nestedProjectMismatch = wireTurnAck();
        nestedProjectMismatch.generation.project = { ...wireProject(), id: "other" };
        expect(() => parseAtelierAgentTurnAck(nestedProjectMismatch, "p", {
            turnId: "turn",
            callId: "call",
            clientRequestId: "request-1",
        })).toThrow();

        const callMismatch = wireTurnAck();
        callMismatch.call = { ...(callMismatch.call as Record<string, unknown>), call_id: "other-call" };
        expect(() => parseAtelierAgentTurnAck(callMismatch, "p", { turnId: "turn" })).toThrow(/Atelier Agent acknowledgement/);

        const missingContinuePieces = wireTurnAck() as Record<string, unknown>;
        missingContinuePieces.call = null;
        missingContinuePieces.generation = null;
        expect(() => parseAtelierAgentTurnAck(missingContinuePieces, "p", {
            turnId: "turn",
            callId: "call",
            clientRequestId: "request-1",
        })).toThrow(/Atelier Agent acknowledgement/);
    });

    it("rejects malformed event envelopes and unsupported ACK fields", () => {
        expect(() => parseAtelierAgentTurnAck({ ...ack(), events: [event({ type: "future_event" })] }, "project-1", { turnId: "turn-1" })).toThrow(/Atelier Agent acknowledgement/);
        expect(() => parseAtelierAgentTurnAck({ ...ack(), schedule_error: 404 }, "project-1", { turnId: "turn-1" })).toThrow(/Atelier Agent acknowledgement/);
        expect(() => parseAtelierAgentTurnAck({ ...ack(), extra: true }, "project-1", { turnId: "turn-1" })).toThrow(/Atelier Agent acknowledgement/);
    });


    it("parses real backend agent turn ACK wire snapshots", () => {
        const fixture = clone(agentTurnAcks) as Record<string, Record<string, unknown>>;

        const started = parseAtelierAgentTurnAck(fixture.started, "p", callIdentity(fixture.started));
        expect(started.turn.start_request_fingerprint).toMatch(/^[0-9a-f]{64}$/);
        expect(started.events.map(item => item.type)).toEqual(["turn_preview_prepared", "turn_pending_saved"]);

        const approvedIdentity = approvalIdentity(fixture.approved);
        const approved = parseAtelierAgentTurnAck(fixture.approved, "p", approvedIdentity);
        expect((approved.call?.approval_record as Record<string, unknown> | undefined)?.decision).toBe("approved");

        const admitted = parseAtelierAgentTurnAck(fixture.admitted, "p", {
            ...approvalIdentity(fixture.admitted),
            clientRequestId: ((fixture.admitted.generation as Record<string, unknown>).receipt as Record<string, unknown>).client_request_id as string,
        });
        expect(admitted.generation?.attempt.schema_version).toBe(2);
        expect(admitted.scheduled).toBe(true);

        const denied = parseAtelierAgentTurnAck(fixture.denied, "p", approvalIdentity(fixture.denied));
        expect(denied.turn.status).toBe("failed");
        expect((denied.call?.approval_record as Record<string, unknown> | undefined)?.decision).toBe("denied");

        const revoked = parseAtelierAgentTurnAck(fixture.revoked, "p", approvalIdentity(fixture.revoked));
        expect(revoked.turn.status).toBe("canceled");
        expect((revoked.call?.approval_record as Record<string, unknown> | undefined)?.decision).toBe("revoked");

        const canceled = parseAtelierAgentTurnAck(fixture.canceled, "p", {
            turnId: (fixture.canceled.turn as Record<string, unknown>).id as string,
            callId: ((fixture.canceled.call as Record<string, unknown>).call_id as string),
        });
        expect(canceled.events.map(item => item.type)).toEqual(["turn_cancelled"]);
    });

    it("rejects operation ACKs with wrong approval decision, terminal turn, or cross-turn ref", () => {
        const wrongDecision = clone(agentTurnAcks.approved) as Record<string, unknown>;
        const wrongDecisionCall = wrongDecision.call as Record<string, unknown>;
        wrongDecisionCall.approval_record = { ...(wrongDecisionCall.approval_record as Record<string, unknown>), decision: "denied" };
        savedCallInAck(wrongDecision).approval_record = wrongDecisionCall.approval_record;
        turnCallInAck(wrongDecision).approval_record = wrongDecisionCall.approval_record;
        expect(() => parseAtelierAgentTurnAck(wrongDecision, "p", {
            ...approvalIdentity(wrongDecision),
            approvalDecision: "approved",
        })).toThrow(/Atelier Agent acknowledgement/);

        const canceledAck = agentTurnAcks.canceled as Record<string, unknown>;
        expect(() => parseAtelierAgentTurnAck(canceledAck, "p", {
            turnId: (canceledAck.turn as Record<string, unknown>).id as string,
            callId: (canceledAck.call as Record<string, unknown>).call_id as string,
            approvalDecision: "approved",
        })).toThrow(/Atelier Agent acknowledgement/);

        const refMismatch = clone(agentTurnAcks.approved) as Record<string, unknown>;
        const call = refMismatch.call as Record<string, unknown>;
        const binding = call.preview_binding as Record<string, unknown>;
        binding.agent_authorization_ref = { ...(binding.agent_authorization_ref as Record<string, unknown>), turn_id: "other-turn" };
        const savedBinding = savedCallInAck(refMismatch).preview_binding as Record<string, unknown>;
        savedBinding.agent_authorization_ref = binding.agent_authorization_ref;
        const turnBinding = turnCallInAck(refMismatch).preview_binding as Record<string, unknown>;
        turnBinding.agent_authorization_ref = binding.agent_authorization_ref;
        expect(() => parseAtelierAgentTurnAck(refMismatch, "p", callIdentity(refMismatch))).toThrow();

        const revokedReadback = clone(agentTurnAcks.approved) as Record<string, unknown>;
        const revokedCall = revokedReadback.call as Record<string, unknown>;
        revokedCall.approval_record = { ...(revokedCall.approval_record as Record<string, unknown>), decision: "revoked" };
        savedCallInAck(revokedReadback).approval_record = revokedCall.approval_record;
        turnCallInAck(revokedReadback).approval_record = revokedCall.approval_record;
        const revokedParsed = parseAtelierAgentTurnAck(revokedReadback, "p", callIdentity(revokedReadback));
        expect((revokedParsed.call?.approval_record as Record<string, unknown> | undefined)?.decision).toBe("revoked");
    });

    it("gets a turn and records approval using exact route shapes", async () => {
        const fetchMock = vi.spyOn(globalThis, "fetch")
            .mockResolvedValueOnce(response(ack()))
            .mockResolvedValueOnce(response(wireTurnAck()));

        const getResult = await atelierAgentApi.getTurn("project-1", "turn-1");
        expect(getResult.turn.id).toBe("turn-1");

        const approvalResult = await atelierAgentApi.recordApproval("p", "turn", {
            expected_revision: 2,
            turn_id: "turn",
            call_id: "call",
            approval_id: "approval",
            authorization_hash: AUTH_HASH,
            decision: "approved",
        });
        expect(approvalResult.call?.call_id).toBe("call");

        expect(fetchMock.mock.calls[0][0]).toBe(`${API_URL}/atelier/projects/project-1/agent/turns/turn-1`);
        expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "GET" });
        expect(fetchMock.mock.calls[1][0]).toBe(`${API_URL}/atelier/projects/p/agent/turns/turn/approval`);
        expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({
            expected_revision: 2,
            turn_id: "turn",
            call_id: "call",
            approval_id: "approval",
            authorization_hash: AUTH_HASH,
            decision: "approved",
        });
    });
});
