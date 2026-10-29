import {
    assertAtelierProjectIdentity,
    AtelierProjectApiError,
    isAtelierRecord,
    parseAtelierProject,
    requestAtelierJson,
    requireAtelierProjectIdInput,
    requireAtelierRevisionInput,
} from "@/lib/atelierApi";
import { parseAtelierGenerationAdmissionAck } from "@/lib/atelierGenerationApi";
import type { AtelierAgentLoopStartContext, AtelierGenerationSingletonAdmissionAck, AtelierProject, JsonRecord } from "@/lib/atelierTypes";

const ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const HASH_RE = /^[0-9a-f]{64}$/;
const EVENT_TYPES = new Set([
    "turn_start_replayed",
    "turn_preview_prepared",
    "turn_pending_saved",
    "approval_recorded",
    "generation_admitted",
    "generation_schedule_requested",
    "generation_schedule_failed",
    "turn_cancelled",
]);

export type AtelierAgentEventType =
    | "turn_start_replayed"
    | "turn_preview_prepared"
    | "turn_pending_saved"
    | "approval_recorded"
    | "generation_admitted"
    | "generation_schedule_requested"
    | "generation_schedule_failed"
    | "turn_cancelled";

export interface AtelierAgentEvent {
    type: AtelierAgentEventType;
    project_id: string;
    turn_id: string;
    call_id: string | null;
    approval_id: string | null;
    attempt_id: string | null;
    candidate_id: string | null;
    message: string | null;
}

export type AtelierAgentToolCall = JsonRecord & {
    call_id: string;
    tool_name: string;
};

export type AtelierAgentTurn = JsonRecord & {
    id: string;
    project_id: string;
    tool_calls?: JsonRecord[];
    start_request_fingerprint?: string;
    loop_start_context?: AtelierAgentLoopStartContext | null;
};

export interface StartAtelierAgentTurnRequest {
    expected_revision: number;
    client_turn_id: string;
    user_message: string;
    selected_node_id: string | null;
}

export interface RecordAtelierAgentApprovalRequest {
    expected_revision: number;
    turn_id: string;
    call_id: string;
    approval_id: string;
    authorization_hash: string;
    decision: "approved" | "denied" | "revoked";
}

export interface ContinueAtelierAgentTurnRequest {
    expected_revision: number;
    turn_id: string;
    call_id: string;
    approval_id: string;
    authorization_hash: string;
    client_request_id: string;
}

export interface CancelAtelierAgentTurnRequest {
    turn_id: string;
}

export interface AtelierAgentTurnAck {
    project: AtelierProject;
    turn: AtelierAgentTurn;
    call: AtelierAgentToolCall | null;
    generation: AtelierGenerationSingletonAdmissionAck | null;
    events: AtelierAgentEvent[];
    replayed: boolean;
    scheduled: boolean;
    schedule_error: string | null;
}

export interface AtelierAgentAckExpectation {
    turnId?: string;
    callId?: string;
    approvalId?: string;
    authorizationHash?: string;
    approvalDecision?: RecordAtelierAgentApprovalRequest["decision"];
    clientRequestId?: string;
}

const mismatch = (): never => {
    throw new AtelierProjectApiError("Atelier Agent acknowledgement does not match the request", "atelier_agent_ack_mismatch");
};

const idPart = (value: string, code = "invalid_atelier_agent_id"): string => {
    if (typeof value !== "string" || !ID_RE.test(value)) {
        throw new AtelierProjectApiError("Invalid Atelier Agent id", code);
    }
    return encodeURIComponent(value);
};

const projectPath = (projectId: string): string => `/atelier/projects/${encodeURIComponent(requireAtelierProjectIdInput(projectId))}`;
const turnPath = (projectId: string, turnId: string): string => `${projectPath(projectId)}/agent/turns/${idPart(turnId, "invalid_atelier_agent_turn_id")}`;

const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(item => canonical(item));
    if (!isAtelierRecord(value)) return value;
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
};

const equal = (a: unknown, b: unknown): boolean => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

const exactKeys = (value: Record<string, unknown>, expected: readonly string[]): boolean => {
    const keys = Object.keys(value);
    return keys.length === expected.length && keys.every(key => expected.includes(key));
};

const requireId = (value: unknown): string => {
    if (typeof value !== "string" || !ID_RE.test(value)) throw mismatch();
    return value;
};

const requireFingerprint = (value: unknown): string => {
    if (typeof value !== "string" || !HASH_RE.test(value)) throw mismatch();
    return value;
};

const requireNullableString = (value: unknown): string | null => {
    if (value === null || typeof value === "string") return value;
    throw mismatch();
};

const requireBoolean = (value: unknown): boolean => {
    if (typeof value !== "boolean") throw mismatch();
    return value;
};

const requireDecision = (value: unknown): RecordAtelierAgentApprovalRequest["decision"] => {
    if (value === "approved" || value === "denied" || value === "revoked") return value;
    throw mismatch();
};

const requireEventType = (value: unknown): AtelierAgentEventType => {
    if (typeof value === "string" && EVENT_TYPES.has(value)) return value as AtelierAgentEventType;
    throw mismatch();
};

const jsonBody = (value: Record<string, unknown>): string => JSON.stringify(value, (_key, item: unknown) => {
    if (typeof item === "undefined" || typeof item === "function" || typeof item === "symbol" || typeof item === "bigint"
        || (typeof item === "number" && !Number.isFinite(item))) throw mismatch();
    return item;
});

const requireStartRequest = (request: StartAtelierAgentTurnRequest): StartAtelierAgentTurnRequest => {
    if (!isAtelierRecord(request) || !exactKeys(request, ["expected_revision", "client_turn_id", "user_message", "selected_node_id"])) throw mismatch();
    const expectedRevision = requireAtelierRevisionInput(request.expected_revision);
    const clientTurnId = requireId(request.client_turn_id);
    if (typeof request.user_message !== "string") throw mismatch();
    const selectedNodeId = request.selected_node_id === null ? null : requireId(request.selected_node_id);
    return {
        expected_revision: expectedRevision,
        client_turn_id: clientTurnId,
        user_message: request.user_message,
        selected_node_id: selectedNodeId,
    };
};

const requireApprovalRequest = (request: RecordAtelierAgentApprovalRequest): RecordAtelierAgentApprovalRequest => {
    if (!isAtelierRecord(request) || !exactKeys(request, ["expected_revision", "turn_id", "call_id", "approval_id", "authorization_hash", "decision"])) throw mismatch();
    return {
        expected_revision: requireAtelierRevisionInput(request.expected_revision),
        turn_id: requireId(request.turn_id),
        call_id: requireId(request.call_id),
        approval_id: requireId(request.approval_id),
        authorization_hash: requireFingerprint(request.authorization_hash),
        decision: requireDecision(request.decision),
    };
};

const requireContinueRequest = (request: ContinueAtelierAgentTurnRequest): ContinueAtelierAgentTurnRequest => {
    if (!isAtelierRecord(request) || !exactKeys(request, ["expected_revision", "turn_id", "call_id", "approval_id", "authorization_hash", "client_request_id"])) throw mismatch();
    return {
        expected_revision: requireAtelierRevisionInput(request.expected_revision),
        turn_id: requireId(request.turn_id),
        call_id: requireId(request.call_id),
        approval_id: requireId(request.approval_id),
        authorization_hash: requireFingerprint(request.authorization_hash),
        client_request_id: requireId(request.client_request_id),
    };
};


const requireCancelRequest = (request: CancelAtelierAgentTurnRequest): CancelAtelierAgentTurnRequest => {
    if (!isAtelierRecord(request) || !exactKeys(request, ["turn_id"])) throw mismatch();
    return { turn_id: requireId(request.turn_id) };
};

const assertPathTurn = (pathTurnId: string, bodyTurnId: string): void => {
    if (pathTurnId !== bodyTurnId) mismatch();
};

const parseAgentEvent = (raw: unknown, projectId: string, turnId: string): AtelierAgentEvent => {
    if (!isAtelierRecord(raw) || !exactKeys(raw, ["type", "project_id", "turn_id", "call_id", "approval_id", "attempt_id", "candidate_id", "message"])) throw mismatch();
    const event: AtelierAgentEvent = {
        type: requireEventType(raw.type),
        project_id: requireId(raw.project_id),
        turn_id: requireId(raw.turn_id),
        call_id: requireNullableString(raw.call_id),
        approval_id: requireNullableString(raw.approval_id),
        attempt_id: requireNullableString(raw.attempt_id),
        candidate_id: requireNullableString(raw.candidate_id),
        message: requireNullableString(raw.message),
    };
    if (event.project_id !== projectId || event.turn_id !== turnId) throw mismatch();
    return event;
};

const parseAgentTurn = (raw: unknown, projectId: string, expectation: AtelierAgentAckExpectation = {}): AtelierAgentTurn => {
    if (!isAtelierRecord(raw)) throw mismatch();
    const id = requireId(raw.id);
    const rawProjectId = requireId(raw.project_id);
    if (rawProjectId !== projectId || (expectation.turnId !== undefined && id !== expectation.turnId)) throw mismatch();
    if (expectation.approvalDecision === "approved" && (raw.status === "canceled" || raw.status === "cancelled" || raw.status === "failed")) throw mismatch();
    if (raw.start_request_fingerprint !== undefined) requireFingerprint(raw.start_request_fingerprint);
    if (raw.tool_calls !== undefined) {
        if (!Array.isArray(raw.tool_calls) || raw.tool_calls.some(item => !isAtelierRecord(item))) throw mismatch();
    }
    return JSON.parse(JSON.stringify(raw)) as AtelierAgentTurn;
};

const findSavedTurn = (project: AtelierProject, rawTurn: unknown, expectation: AtelierAgentAckExpectation): AtelierAgentTurn => {
    const turn = parseAgentTurn(rawTurn, project.id, expectation);
    const saved = project.agent_turns.find(item => isAtelierRecord(item) && item.id === turn.id);
    if (!saved || !equal(saved, turn)) throw mismatch();
    return turn;
};

const assertApprovalTuple = (call: Record<string, unknown>, projectId: string, turnId: string, expectation: AtelierAgentAckExpectation): void => {
    const binding = call.preview_binding;
    if (isAtelierRecord(binding) && isAtelierRecord(binding.agent_authorization_ref)) {
        const ref = binding.agent_authorization_ref;
        if (ref.project_id !== projectId || ref.turn_id !== turnId || ref.call_id !== call.call_id) throw mismatch();
    }
    if (expectation.approvalId === undefined && expectation.authorizationHash === undefined && expectation.approvalDecision === undefined) return;
    const approval = call.approval_record;
    if (!isAtelierRecord(approval)) throw mismatch();
    if (expectation.approvalId !== undefined && approval.approval_id !== expectation.approvalId) throw mismatch();
    if (expectation.authorizationHash !== undefined && approval.authorization_hash !== expectation.authorizationHash) throw mismatch();
    if (expectation.approvalDecision !== undefined && approval.decision !== expectation.approvalDecision) throw mismatch();
    if (!isAtelierRecord(binding) || !isAtelierRecord(binding.agent_authorization_ref)) throw mismatch();
    const ref = binding.agent_authorization_ref;
    if (expectation.approvalId !== undefined && ref.approval_id !== expectation.approvalId) throw mismatch();
    if (expectation.authorizationHash !== undefined && ref.authorization_hash !== expectation.authorizationHash) throw mismatch();
};

const parseAgentCall = (raw: unknown, projectId: string, turn: AtelierAgentTurn, expectation: AtelierAgentAckExpectation = {}): AtelierAgentToolCall | null => {
    if (raw === null) return null;
    if (!isAtelierRecord(raw)) throw mismatch();
    const callId = requireId(raw.call_id);
    if (expectation.callId !== undefined && callId !== expectation.callId) throw mismatch();
    if (typeof raw.tool_name !== "string" || raw.tool_name.length === 0) throw mismatch();
    const savedCalls = Array.isArray(turn.tool_calls) ? turn.tool_calls : [];
    const saved = savedCalls.find(item => isAtelierRecord(item) && item.call_id === callId);
    if (!saved || !equal(saved, raw)) throw mismatch();
    assertApprovalTuple(raw, projectId, turn.id, expectation);
    return JSON.parse(JSON.stringify(raw)) as AtelierAgentToolCall;
};

export function parseAtelierAgentTurnAck(raw: unknown, projectId: string, expectation: AtelierAgentAckExpectation = {}): AtelierAgentTurnAck {
    if (!isAtelierRecord(raw) || !exactKeys(raw, ["project", "turn", "call", "generation", "events", "replayed", "scheduled", "schedule_error"])) throw mismatch();
    const project = assertAtelierProjectIdentity(parseAtelierProject(raw.project), projectId);
    const turn = findSavedTurn(project, raw.turn, expectation);
    const call = parseAgentCall(raw.call, project.id, turn, expectation);
    if (expectation.callId !== undefined && call === null) throw mismatch();
    const generation = raw.generation === null
        ? null
        : parseAtelierGenerationAdmissionAck(raw.generation, projectId, {
            requestId: expectation.clientRequestId,
            creating: !requireBoolean(raw.replayed),
        });
    if (expectation.clientRequestId !== undefined && generation === null) throw mismatch();
    if (generation !== null && !equal(generation.project, project)) throw mismatch();
    if (expectation.callId !== undefined && call !== null && call.call_id !== expectation.callId) throw mismatch();
    if (!Array.isArray(raw.events)) throw mismatch();
    const events = raw.events.map(item => parseAgentEvent(item, project.id, turn.id));
    const replayed = requireBoolean(raw.replayed);
    const scheduled = requireBoolean(raw.scheduled);
    const scheduleError = requireNullableString(raw.schedule_error);
    return { project, turn, call, generation, events, replayed, scheduled, schedule_error: scheduleError };
}

export const findSavedAtelierAgentTurn = findSavedTurn;
export const equalAtelierAgentJson = equal;
export const requireAtelierAgentApprovalRequest = requireApprovalRequest;
export const requireAtelierAgentId = requireId;
export const requireAtelierAgentStartRequest = requireStartRequest;
export const requireAtelierAgentContinueRequest = requireContinueRequest;
export const stringifyAtelierAgentJsonBody = jsonBody;
export const atelierAgentProjectPath = projectPath;
export const atelierAgentTurnPath = turnPath;
export const assertAtelierAgentPathTurn = assertPathTurn;

export const atelierAgentApi = {
    async startTurn(projectId: string, request: StartAtelierAgentTurnRequest): Promise<AtelierAgentTurnAck> {
        const payload = requireStartRequest(request);
        return parseAtelierAgentTurnAck(await requestAtelierJson(`${projectPath(projectId)}/agent/turns`, {
            method: "POST",
            body: jsonBody(payload as unknown as Record<string, unknown>),
        }, ["atelier_agent_start_rejected"]), projectId, { turnId: payload.client_turn_id });
    },

    async getTurn(projectId: string, turnId: string): Promise<AtelierAgentTurnAck> {
        const cleanTurnId = requireId(turnId);
        return parseAtelierAgentTurnAck(await requestAtelierJson(turnPath(projectId, cleanTurnId), { method: "GET" }), projectId, { turnId: cleanTurnId });
    },

    async recordApproval(projectId: string, turnId: string, request: RecordAtelierAgentApprovalRequest): Promise<AtelierAgentTurnAck> {
        const cleanTurnId = requireId(turnId);
        const payload = requireApprovalRequest(request);
        assertPathTurn(cleanTurnId, payload.turn_id);
        return parseAtelierAgentTurnAck(await requestAtelierJson(`${turnPath(projectId, cleanTurnId)}/approval`, {
            method: "POST",
            body: jsonBody(payload as unknown as Record<string, unknown>),
        }), projectId, {
            turnId: payload.turn_id,
            callId: payload.call_id,
            approvalId: payload.approval_id,
            authorizationHash: payload.authorization_hash,
            approvalDecision: payload.decision,
        });
    },

    async continueTurn(projectId: string, turnId: string, request: ContinueAtelierAgentTurnRequest): Promise<AtelierAgentTurnAck> {
        const cleanTurnId = requireId(turnId);
        const payload = requireContinueRequest(request);
        assertPathTurn(cleanTurnId, payload.turn_id);
        return parseAtelierAgentTurnAck(await requestAtelierJson(`${turnPath(projectId, cleanTurnId)}/continue`, {
            method: "POST",
            body: jsonBody(payload as unknown as Record<string, unknown>),
        }), projectId, {
            turnId: payload.turn_id,
            callId: payload.call_id,
            approvalId: payload.approval_id,
            authorizationHash: payload.authorization_hash,
            clientRequestId: payload.client_request_id,
        });
    },

    async cancelTurn(projectId: string, turnId: string, request: CancelAtelierAgentTurnRequest): Promise<AtelierAgentTurnAck> {
        const cleanTurnId = requireId(turnId);
        const payload = requireCancelRequest(request);
        assertPathTurn(cleanTurnId, payload.turn_id);
        return parseAtelierAgentTurnAck(await requestAtelierJson(`${turnPath(projectId, cleanTurnId)}/cancel`, {
            method: "POST",
            body: jsonBody(payload as unknown as Record<string, unknown>),
        }), projectId, { turnId: payload.turn_id });
    },
};
