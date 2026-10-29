import { AtelierProjectApiError, isAtelierRecord } from "@/lib/atelierApi";
import {
    assertAtelierAgentPathTurn,
    atelierAgentProjectPath,
    atelierAgentTurnPath,
    parseAtelierAgentTurnAck,
    requireAtelierAgentContinueRequest,
    requireAtelierAgentStartRequest,
    stringifyAtelierAgentJsonBody,
    type AtelierAgentAckExpectation,
    type AtelierAgentTurnAck,
    type ContinueAtelierAgentTurnRequest,
    type StartAtelierAgentTurnRequest,
} from "@/lib/atelierAgentApi";
import { postAtelierEventStream } from "@/lib/atelierEventStream";

const STREAM_EVENT_TYPES = new Set([
    "turn_start_replayed",
    "turn_planning_started",
    "turn_plan_ready",
    "turn_preview_prepared",
    "turn_pending_saved",
    "approval_recorded",
    "generation_admitted",
    "generation_schedule_requested",
    "generation_schedule_failed",
    "turn_cancelled",
    "turn_terminal",
    "turn_closed",
]);

const STREAM_EVENT_KEYS = [
    "type",
    "project_id",
    "turn_id",
    "call_id",
    "approval_id",
    "attempt_id",
    "candidate_id",
    "message",
    "ack",
] as const;

const STREAM_ERROR_KEYS = ["type", "project_id", "turn_id", "code", "detail"] as const;

export type AtelierAgentStreamEventType =
    | "turn_start_replayed"
    | "turn_planning_started"
    | "turn_plan_ready"
    | "turn_preview_prepared"
    | "turn_pending_saved"
    | "approval_recorded"
    | "generation_admitted"
    | "generation_schedule_requested"
    | "generation_schedule_failed"
    | "turn_cancelled"
    | "turn_terminal"
    | "turn_closed";

export interface AtelierAgentStreamEvent {
    type: AtelierAgentStreamEventType;
    project_id: string;
    turn_id: string | null;
    call_id: string | null;
    approval_id: string | null;
    attempt_id: string | null;
    candidate_id: string | null;
    message: string | null;
    ack: AtelierAgentTurnAck | null;
}

export interface AtelierAgentStreamErrorEvent {
    type: "agent_error";
    project_id: string;
    turn_id: string | null;
    code: string;
    detail: string;
}

export interface AtelierAgentStreamOptions {
    signal?: AbortSignal;
    onEvent?: (event: AtelierAgentStreamEvent | AtelierAgentStreamErrorEvent) => void;
    maxEventBytes?: number;
}

const exactKeys = (value: Record<string, unknown>, expected: readonly string[]): boolean => {
    const keys = Object.keys(value);
    return keys.length === expected.length && keys.every(key => expected.includes(key));
};

const streamError = (message: string, code: string, status?: number): AtelierProjectApiError => new AtelierProjectApiError(message, code, status);

const requireString = (value: unknown, code = "atelier_agent_stream_invalid_event"): string => {
    if (typeof value !== "string") throw streamError("Invalid Atelier Agent stream event", code);
    return value;
};

const requireNullableString = (value: unknown): string | null => {
    if (value === null || typeof value === "string") return value;
    throw streamError("Invalid Atelier Agent stream event", "atelier_agent_stream_invalid_event");
};

const requireStreamEventType = (value: unknown): AtelierAgentStreamEventType => {
    if (typeof value === "string" && STREAM_EVENT_TYPES.has(value)) return value as AtelierAgentStreamEventType;
    throw streamError("Unknown Atelier Agent stream event", "atelier_agent_stream_unknown_event");
};

const expectedTurnId = (expectation: AtelierAgentAckExpectation): string | undefined => expectation.turnId;

const assertStreamIdentity = (
    rawProjectId: unknown,
    rawTurnId: unknown,
    projectId: string,
    expectation: AtelierAgentAckExpectation,
): { projectId: string; turnId: string | null } => {
    const eventProjectId = requireString(rawProjectId);
    const eventTurnId = requireNullableString(rawTurnId);
    const expected = expectedTurnId(expectation);
    if (eventProjectId !== projectId || (expected !== undefined && eventTurnId !== expected)) {
        throw streamError("Atelier Agent stream event does not match the request", "atelier_agent_stream_identity_mismatch");
    }
    return { projectId: eventProjectId, turnId: eventTurnId };
};

export const parseAtelierAgentStreamError = (
    raw: Record<string, unknown>,
    projectId: string,
    expectation: AtelierAgentAckExpectation,
): AtelierAgentStreamErrorEvent => {
    if (!exactKeys(raw, STREAM_ERROR_KEYS)) throw streamError("Invalid Atelier Agent stream error event", "atelier_agent_stream_invalid_event");
    const identity = assertStreamIdentity(raw.project_id, raw.turn_id, projectId, expectation);
    const code = requireString(raw.code, "atelier_agent_stream_invalid_event");
    const detail = requireString(raw.detail, "atelier_agent_stream_invalid_event");
    return { type: "agent_error", project_id: identity.projectId, turn_id: identity.turnId, code, detail };
};

const parseStreamEvent = (
    raw: unknown,
    projectId: string,
    expectation: AtelierAgentAckExpectation,
): AtelierAgentStreamEvent | AtelierAgentStreamErrorEvent => {
    if (!isAtelierRecord(raw)) throw streamError("Invalid Atelier Agent stream event", "atelier_agent_stream_invalid_event");
    if (raw.type === "agent_error") return parseAtelierAgentStreamError(raw, projectId, expectation);
    if (!exactKeys(raw, STREAM_EVENT_KEYS)) throw streamError("Invalid Atelier Agent stream event", "atelier_agent_stream_invalid_event");
    const type = requireStreamEventType(raw.type);
    const identity = assertStreamIdentity(raw.project_id, raw.turn_id, projectId, expectation);
    const callId = requireNullableString(raw.call_id);
    const approvalId = requireNullableString(raw.approval_id);
    if (type !== "turn_terminal") {
        if (expectation.callId !== undefined && callId !== expectation.callId) {
            throw streamError("Atelier Agent stream event does not match the request", "atelier_agent_stream_identity_mismatch");
        }
        if (expectation.approvalId !== undefined && approvalId !== expectation.approvalId) {
            throw streamError("Atelier Agent stream event does not match the request", "atelier_agent_stream_identity_mismatch");
        }
    }
    const ack = raw.ack === null
        ? null
        : parseAtelierAgentTurnAck(raw.ack, projectId, expectation);
    if (type === "turn_terminal") {
        if (ack === null) throw streamError("Atelier Agent terminal stream event is missing an ACK", "atelier_agent_stream_invalid_event");
    } else if (ack !== null) {
        throw streamError("Atelier Agent non-terminal stream event unexpectedly included an ACK", "atelier_agent_stream_invalid_event");
    }
    return {
        type,
        project_id: identity.projectId,
        turn_id: identity.turnId,
        call_id: callId,
        approval_id: approvalId,
        attempt_id: requireNullableString(raw.attempt_id),
        candidate_id: requireNullableString(raw.candidate_id),
        message: requireNullableString(raw.message),
        ack,
    };
};

export async function startTurnStream(
    projectId: string,
    request: StartAtelierAgentTurnRequest,
    options: AtelierAgentStreamOptions = {},
): Promise<AtelierAgentTurnAck> {
    const payload = requireAtelierAgentStartRequest(request);
    return postAtelierEventStream(`${atelierAgentProjectPath(projectId)}/agent/turns/stream`, stringifyAtelierAgentJsonBody(payload as unknown as Record<string, unknown>), raw => parseStreamEvent(raw, projectId, { turnId: payload.client_turn_id }), options);
}

export async function continueTurnStream(
    projectId: string,
    turnId: string,
    request: ContinueAtelierAgentTurnRequest,
    options: AtelierAgentStreamOptions = {},
): Promise<AtelierAgentTurnAck> {
    const payload = requireAtelierAgentContinueRequest(request);
    assertAtelierAgentPathTurn(turnId, payload.turn_id);
    return postAtelierEventStream(`${atelierAgentTurnPath(projectId, payload.turn_id)}/continue/stream`, stringifyAtelierAgentJsonBody(payload as unknown as Record<string, unknown>), raw => parseStreamEvent(raw, projectId, {
        turnId: payload.turn_id,
        callId: payload.call_id,
        approvalId: payload.approval_id,
        authorizationHash: payload.authorization_hash,
        clientRequestId: payload.client_request_id,
    }), options);
}
