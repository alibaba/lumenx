import { assertAtelierProjectIdentity, AtelierProjectApiError, isAtelierRecord, parseAtelierProject, requestAtelierJson, requireAtelierRevisionInput } from "@/lib/atelierApi";
import { assertAtelierAgentPathTurn, atelierAgentProjectPath, equalAtelierAgentJson, findSavedAtelierAgentTurn, requireAtelierAgentApprovalRequest, requireAtelierAgentId, stringifyAtelierAgentJsonBody, type AtelierAgentTurn, type RecordAtelierAgentApprovalRequest } from "@/lib/atelierAgentApi";
import { parseAtelierAgentStreamError, type AtelierAgentStreamErrorEvent } from "@/lib/atelierAgentStream";
import { postAtelierEventStream, type AtelierEventStreamOptions } from "@/lib/atelierEventStream";
import { parseAtelierGenerationAdmissionAck } from "@/lib/atelierGenerationApi";
import type { AtelierGenerationSingletonAdmissionAck, AtelierProductSkillCatalogItem, AtelierProductSkillSnapshot, AtelierProject } from "@/lib/atelierTypes";

const EVENT_TYPES = ["loop_started", "loop_start_replayed", "loop_resumed", "iteration_started", "iteration_saved", "tool_started", "tool_done", "approval_paused", "canvas_approval_recorded", "generation_admitted", "generation_schedule_requested", "generation_schedule_failed", "generation_waiting", "loop_terminal", "loop_closed"] as const;
type LoopEventType = typeof EVENT_TYPES[number];
export type AtelierAgentLoopPlanner = "deterministic_core" | "model_adapter";
const ACK_EVENTS = new Set<LoopEventType>(["approval_paused", "generation_waiting", "loop_terminal", "loop_closed"]);
const TRACE_KEYS = ["type", "turn_id", "iteration", "call_id", "tool_name", "approval_id", "attempt_id", "candidate_id", "status", "decision", "message", "replayed"];
const STREAM_KEYS = ["type", "project_id", "turn_id", "iteration", "call_id", "tool_name", "approval_id", "attempt_id", "candidate_id", "message", "replayed", "ack"];
const SKILL_NAME_RE = /^[a-z][a-z0-9-]{2,63}$/;
const SKILL_VERSION_RE = /^\d+\.\d+\.\d+$/;
const LOWER_HASH_RE = /^[0-9a-f]{64}$/;
const PRODUCT_SKILL_CATEGORIES = ["story", "shot", "review", "sequence"] as const;
const PRODUCT_SKILL_CATALOG_KEYS = ["schema_version", "name", "version", "title", "description", "category", "expected_tools", "default_iteration_cap", "content_hash", "enabled", "disabled_reason"];
const PRODUCT_SKILL_SNAPSHOT_KEYS = ["schema_version", "name", "version", "title", "description", "category", "prompt_template", "expected_tools", "content_hash"];

export interface StartAtelierAgentLoopRequest {
    expected_revision: number;
    client_turn_id: string;
    user_message: string;
    selected_node_id: string | null;
    planner?: AtelierAgentLoopPlanner;
    skill_name?: string | null;
    max_iterations?: number;
}
export interface ResumeAtelierAgentLoopRequest { turn_id: string }
export interface AtelierAgentLoopTrace {
    type: LoopEventType;
    turn_id: string;
    iteration: number | null;
    call_id: string | null;
    tool_name: string | null;
    approval_id: string | null;
    attempt_id: string | null;
    candidate_id: string | null;
    status: string | null;
    decision: string | null;
    message: string | null;
    replayed: boolean;
}
export interface AtelierAgentLoopAck {
    project: AtelierProject;
    turn: AtelierAgentTurn;
    replayed: boolean;
    events: AtelierAgentLoopTrace[];
    generation: AtelierGenerationSingletonAdmissionAck | null;
    scheduled: boolean;
    schedule_error: string | null;
}
export interface AtelierAgentLoopEvent extends Omit<AtelierAgentLoopTrace, "status" | "decision"> {
    project_id: string;
    ack: AtelierAgentLoopAck | null;
}
export type AtelierAgentLoopStreamOptions = AtelierEventStreamOptions<AtelierAgentLoopEvent>;
interface Expectation {
    turnId: string;
    start?: StartAtelierAgentLoopRequest;
    approval?: RecordAtelierAgentApprovalRequest;
}
function fail(): never { throw new AtelierProjectApiError("Atelier Agent loop acknowledgement does not match the request", "atelier_agent_loop_ack_mismatch"); }
const exact = (raw: Record<string, unknown>, keys: string[]): boolean => Object.keys(raw).length === keys.length && Object.keys(raw).every(key => keys.includes(key));
const nullableString = (raw: unknown): string | null => raw === null || typeof raw === "string" ? raw : fail();
const boolean = (raw: unknown): boolean => typeof raw === "boolean" ? raw : fail();
const skillName = (raw: unknown): string => typeof raw === "string" && SKILL_NAME_RE.test(raw) ? raw : fail();
const semver = (raw: unknown): string => typeof raw === "string" && SKILL_VERSION_RE.test(raw) ? raw : fail();
const lowerHash = (raw: unknown): string => typeof raw === "string" && LOWER_HASH_RE.test(raw) ? raw : fail();
const nonemptyString = (raw: unknown): string => typeof raw === "string" && raw.length > 0 ? raw : fail();
const stringArray = (raw: unknown): string[] => Array.isArray(raw) && raw.every(item => typeof item === "string" && item.length > 0) ? [...raw] : fail();
const skillSnapshot = (raw: unknown): AtelierProductSkillSnapshot => {
    if (!isAtelierRecord(raw) || !exact(raw, PRODUCT_SKILL_SNAPSHOT_KEYS)) fail();
    return {
        schema_version: raw.schema_version === 1 ? 1 : fail(),
        name: skillName(raw.name),
        version: semver(raw.version),
        title: nonemptyString(raw.title),
        description: nonemptyString(raw.description),
        category: nonemptyString(raw.category),
        prompt_template: nonemptyString(raw.prompt_template),
        expected_tools: stringArray(raw.expected_tools),
        content_hash: lowerHash(raw.content_hash),
    };
};
const productSkillCatalogItem = (raw: unknown): AtelierProductSkillCatalogItem => {
    if (!isAtelierRecord(raw) || !exact(raw, PRODUCT_SKILL_CATALOG_KEYS)) fail();
    const defaultCap = typeof raw.default_iteration_cap === "number" && Number.isSafeInteger(raw.default_iteration_cap) && raw.default_iteration_cap >= 1 && raw.default_iteration_cap <= 3 ? raw.default_iteration_cap : fail();
    const category = typeof raw.category === "string" && PRODUCT_SKILL_CATEGORIES.includes(raw.category as AtelierProductSkillCatalogItem["category"]) ? raw.category as AtelierProductSkillCatalogItem["category"] : fail();
    return {
        schema_version: raw.schema_version === 1 ? 1 : fail(),
        name: skillName(raw.name), version: semver(raw.version), title: nonemptyString(raw.title), description: nonemptyString(raw.description),
        category, expected_tools: stringArray(raw.expected_tools), default_iteration_cap: defaultCap, content_hash: lowerHash(raw.content_hash),
        enabled: boolean(raw.enabled), disabled_reason: nullableString(raw.disabled_reason),
    };
};
const iteration = (raw: unknown): number | null => raw === null || (typeof raw === "number" && Number.isSafeInteger(raw) && raw >= 1 && raw <= 8) ? raw : fail();
const eventType = (raw: unknown): LoopEventType => typeof raw === "string" && EVENT_TYPES.includes(raw as LoopEventType) ? raw as LoopEventType : fail();
const loopPath = (projectId: string, turnId?: string) => `${atelierAgentProjectPath(projectId)}/agent/loops${turnId === undefined ? "" : `/${encodeURIComponent(requireAtelierAgentId(turnId))}`}`;
const body = (raw: object) => stringifyAtelierAgentJsonBody(raw as Record<string, unknown>);

export function requireAtelierAgentLoopStart(request: StartAtelierAgentLoopRequest): StartAtelierAgentLoopRequest {
    const required = ["expected_revision", "client_turn_id", "user_message", "selected_node_id"];
    if (!isAtelierRecord(request) || required.some(key => !(key in request)) || Object.keys(request).some(key => ![...required, "planner", "skill_name", "max_iterations"].includes(key))) fail();
    requireAtelierRevisionInput(request.expected_revision);
    requireAtelierAgentId(request.client_turn_id);
    if (typeof request.user_message !== "string") fail();
    if (request.selected_node_id !== null) requireAtelierAgentId(request.selected_node_id);
    if ("planner" in request && request.planner !== "deterministic_core" && request.planner !== "model_adapter") fail();
    if ("skill_name" in request && request.skill_name !== null && (typeof request.skill_name !== "string" || !SKILL_NAME_RE.test(request.skill_name))) fail();
    if (typeof request.skill_name === "string" && (request.planner ?? "deterministic_core") !== "model_adapter") fail();
    if ("max_iterations" in request && iteration(request.max_iterations) === null) fail();
    return { ...request };
}
function requireResume(turnId: string, request: ResumeAtelierAgentLoopRequest): ResumeAtelierAgentLoopRequest {
    requireAtelierAgentId(turnId);
    if (!isAtelierRecord(request) || !exact(request, ["turn_id"])) fail();
    requireAtelierAgentId(request.turn_id);
    assertAtelierAgentPathTurn(turnId, request.turn_id);
    return { turn_id: request.turn_id };
}
function parseTrace(raw: unknown, turnId: string): AtelierAgentLoopTrace {
    if (!isAtelierRecord(raw) || !exact(raw, TRACE_KEYS) || raw.turn_id !== turnId) fail();
    return {
        type: eventType(raw.type), turn_id: turnId, iteration: iteration(raw.iteration),
        call_id: nullableString(raw.call_id), tool_name: nullableString(raw.tool_name), approval_id: nullableString(raw.approval_id),
        attempt_id: nullableString(raw.attempt_id), candidate_id: nullableString(raw.candidate_id), status: nullableString(raw.status),
        decision: nullableString(raw.decision), message: nullableString(raw.message), replayed: boolean(raw.replayed),
    };
}
export function parseAtelierAgentLoopAck(raw: unknown, projectId: string, expected: Expectation): AtelierAgentLoopAck {
    if (!isAtelierRecord(raw) || !exact(raw, ["project", "turn", "replayed", "events", "generation", "scheduled", "schedule_error"])) fail();
    const project = assertAtelierProjectIdentity(parseAtelierProject(raw.project), projectId);
    const turn = findSavedAtelierAgentTurn(project, raw.turn, { turnId: expected.turnId });
    if (!turn.loop_start_context) fail();
    if (expected.start) {
        const request = expected.start;
        const context = turn.loop_start_context;
        if (!isAtelierRecord(context)) fail();
        if (typeof request.skill_name === "string") {
            if (!exact(context, ["schema_version", "user_message", "selected_node_id", "planner", "skill_name", "max_iterations", "skill_snapshot"])) fail();
            const snapshot = skillSnapshot(context.skill_snapshot);
            if (context.schema_version !== 2 || context.user_message !== request.user_message || context.selected_node_id !== request.selected_node_id
                || context.planner !== "model_adapter" || context.skill_name !== request.skill_name || context.max_iterations !== (request.max_iterations ?? 3)
                || snapshot.name !== request.skill_name || turn.skill_name !== request.skill_name) fail();
        } else if (!equalAtelierAgentJson(context, {
            schema_version: 1, user_message: request.user_message, selected_node_id: request.selected_node_id,
            planner: request.planner ?? "deterministic_core", skill_name: null, max_iterations: request.max_iterations ?? 3,
        }) || turn.skill_name !== null) fail();
    }
    const replayed = boolean(raw.replayed);
    const generation = raw.generation === null ? null : parseAtelierGenerationAdmissionAck(raw.generation, projectId, { creating: !replayed });
    if (generation) {
        if (!equalAtelierAgentJson(generation.project, project)) fail();
        const ref = generation.attempt.agent_authorization_ref;
        const call = turn.tool_calls?.find(item => item.call_id === ref?.call_id);
        if (!ref || ref.turn_id !== turn.id || ref.project_id !== projectId || !call || !isAtelierRecord(call.preview_binding)
            || !equalAtelierAgentJson(call.preview_binding.agent_authorization_ref, ref)) fail();
    }
    if (expected.approval) {
        const approval = expected.approval;
        const call = turn.tool_calls?.find(item => item.call_id === approval.call_id);
        const record = call?.canvas_approval_record;
        const binding = call?.canvas_approval_binding;
        if (!isAtelierRecord(record) || !isAtelierRecord(binding) || !isAtelierRecord(binding.canvas_authorization_ref)) fail();
        const ref = binding.canvas_authorization_ref;
        if (record.approval_id !== approval.approval_id || record.authorization_hash !== approval.authorization_hash || record.decision !== approval.decision
            || ref.project_id !== projectId || ref.turn_id !== turn.id || ref.call_id !== approval.call_id
            || ref.approval_id !== approval.approval_id || ref.authorization_hash !== approval.authorization_hash) fail();
    }
    if (!Array.isArray(raw.events)) fail();
    return { project, turn, generation, replayed, events: raw.events.map(event => parseTrace(event, turn.id)), scheduled: boolean(raw.scheduled), schedule_error: nullableString(raw.schedule_error) };
}
function parseLoopEvent(raw: unknown, projectId: string, expected: Expectation): AtelierAgentLoopEvent | AtelierAgentStreamErrorEvent {
    if (!isAtelierRecord(raw)) fail();
    if (raw.type === "agent_error") return parseAtelierAgentStreamError(raw, projectId, expected);
    if (!exact(raw, STREAM_KEYS) || raw.project_id !== projectId || raw.turn_id !== expected.turnId) fail();
    const type = eventType(raw.type);
    const ack = raw.ack === null ? null : parseAtelierAgentLoopAck(raw.ack, projectId, expected);
    if (type !== "generation_schedule_failed" && ACK_EVENTS.has(type) !== (ack !== null)) fail();
    if (type === "generation_schedule_failed" && ack && (!ack.generation || ack.scheduled || ack.schedule_error === null)) fail();
    const replayed = boolean(raw.replayed);
    if (ack && ack.replayed !== replayed) fail();
    if (type === "loop_terminal" && ack && !["completed", "failed", "canceled", "cancelled"].includes(String(ack.turn.status))) fail();
    if (type === "generation_waiting" && ack && (!ack.generation || ["completed", "failed", "cancelled"].includes(ack.generation.attempt.status))) fail();
    return { type, project_id: projectId, turn_id: expected.turnId, iteration: iteration(raw.iteration),
        call_id: nullableString(raw.call_id), tool_name: nullableString(raw.tool_name), approval_id: nullableString(raw.approval_id),
        attempt_id: nullableString(raw.attempt_id), candidate_id: nullableString(raw.candidate_id), message: nullableString(raw.message), replayed, ack };
}
export const atelierAgentLoopApi = {
    async listProductSkills(): Promise<AtelierProductSkillCatalogItem[]> {
        const raw = await requestAtelierJson("/atelier/agent/product_skills", { method: "GET" });
        if (!Array.isArray(raw)) fail();
        return raw.map(productSkillCatalogItem);
    },
    async startLoop(projectId: string, request: StartAtelierAgentLoopRequest): Promise<AtelierAgentLoopAck> {
        const payload = requireAtelierAgentLoopStart(request);
        return parseAtelierAgentLoopAck(await requestAtelierJson(loopPath(projectId), { method: "POST", body: body(payload) }), projectId, { turnId: payload.client_turn_id, start: payload });
    },
    async getLoop(projectId: string, turnId: string): Promise<AtelierAgentLoopAck> {
        return parseAtelierAgentLoopAck(await requestAtelierJson(loopPath(projectId, turnId), { method: "GET" }), projectId, { turnId });
    },
    async resumeLoop(projectId: string, turnId: string, request: ResumeAtelierAgentLoopRequest): Promise<AtelierAgentLoopAck> {
        const payload = requireResume(turnId, request);
        return parseAtelierAgentLoopAck(await requestAtelierJson(`${loopPath(projectId, turnId)}/resume`, { method: "POST", body: body(payload) }), projectId, { turnId });
    },
    async recordCanvasApproval(projectId: string, turnId: string, request: RecordAtelierAgentApprovalRequest): Promise<AtelierAgentLoopAck> {
        const payload = requireAtelierAgentApprovalRequest(request);
        assertAtelierAgentPathTurn(turnId, payload.turn_id);
        return parseAtelierAgentLoopAck(await requestAtelierJson(`${loopPath(projectId, turnId)}/canvas_approval`, { method: "POST", body: body(payload) }), projectId, { turnId, approval: payload });
    },
};
export function startLoopStream(projectId: string, request: StartAtelierAgentLoopRequest, options: AtelierAgentLoopStreamOptions = {}): Promise<AtelierAgentLoopAck> {
    const payload = requireAtelierAgentLoopStart(request);
    return postAtelierEventStream<AtelierAgentLoopAck, AtelierAgentLoopEvent>(`${loopPath(projectId)}/stream`, body(payload), raw => parseLoopEvent(raw, projectId, { turnId: payload.client_turn_id, start: payload }), options);
}
export function resumeLoopStream(projectId: string, turnId: string, request: ResumeAtelierAgentLoopRequest, options: AtelierAgentLoopStreamOptions = {}): Promise<AtelierAgentLoopAck> {
    const payload = requireResume(turnId, request);
    return postAtelierEventStream<AtelierAgentLoopAck, AtelierAgentLoopEvent>(`${loopPath(projectId, turnId)}/resume/stream`, body(payload), raw => parseLoopEvent(raw, projectId, { turnId }), options);
}
