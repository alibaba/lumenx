// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";

import backendWireProject from "./fixtures/atelier-agent-authorization-wire.json";
import { parseAtelierProject } from "@/lib/atelierApi";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const HASH_C = "c".repeat(64);
const HASH_D = "d".repeat(64);
const HASH_E = "e".repeat(64);
const TOOL_NAME = "generation.createVideoCandidates";
const TOOL_SCHEMA_VERSION = "atelier.tools.readonly.v1";
const STORAGE_ID = "11111111-1111-4111-8111-111111111111";

const clone = <T>(value: T): T => structuredClone(value);

const providerRecord = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 1,
    attempt_id: "attempt-1",
    phase: "accepted",
    provider_model_id: "wan2.7-i2v",
    intent: { model_id: "wan/wan2.7-video#i2v", mode: "i2v", prompt: "Slow sunrise", params: {} },
    media: [],
    scope: { provider: "dashscope", backend: "dashscope", gateway: "dashscope", base_url: "https://dashscope.aliyuncs.com", region: "cn-beijing", workspace_id: null, account_identity: null },
    credential_ref: "credential-ref",
    fingerprint: HASH_A,
    handle: null,
    observation: null,
    prepared_artifact: null,
    artifact: null,
    ...overrides,
});

const candidate = (overrides: Record<string, unknown> = {}) => ({
    execution_contract: "atelier-provider-video-v1",
    id: "candidate-1",
    attempt_id: "attempt-1",
    media_kind: "video",
    status: "pending",
    video_url: null,
    error_code: null,
    created_at: 10,
    updated_at: 10,
    ...overrides,
});

const authorizationRef = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 1,
    project_id: "project-1",
    turn_id: "turn-1",
    call_id: "call-1",
    approval_id: "approval-1",
    tool_name: TOOL_NAME,
    tool_schema_version: TOOL_SCHEMA_VERSION,
    authorization_hash: HASH_C,
    preview_fingerprint: HASH_B,
    request_fingerprint: HASH_D,
    server_storage_identity: STORAGE_ID,
    policy_snapshot_hash: HASH_E,
    ...overrides,
});

const previewBinding = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 1,
    preview_public_record: {
        schema_version: 1,
        project_id: "project-1",
        node_id: "video",
        server_storage_identity: STORAGE_ID,
        raw_request: { mode: "i2v", model_id: "wan/wan2.7-video#i2v" },
        first_frame: { node_id: "image", media_url: "uploads/frame.png" },
    },
    provider_execution: providerRecord({ attempt_id: "preview-attempt-1", fingerprint: HASH_B }),
    agent_authorization_ref: authorizationRef(),
    created_at: 20,
    ...overrides,
});

const approvalRecord = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 1,
    approval_id: "approval-1",
    authorization_hash: HASH_C,
    decision: "approved",
    decided_at: 21,
    ...overrides,
});

const toolCall = (overrides: Record<string, unknown> = {}) => ({
    call_id: "call-1",
    tool_name: TOOL_NAME,
    arguments: { node_id: "video" },
    status: "completed",
    preview_binding: previewBinding(),
    approval_record: approvalRecord(),
    ...overrides,
});

const toolCallWithoutPreviewBinding = (): Record<string, unknown> => {
    const { preview_binding: previewBindingValue, ...call } = toolCall({ approval_record: approvalRecord() });
    void previewBindingValue;
    return call;
};

type MutableBackendWireProject = {
    nodes: Array<{ data: { candidates?: unknown[] } }>;
    agent_turns: Array<{
        status: string;
        tool_calls: Array<{
            approval_record: { decision?: unknown } | null;
            approval_granted: boolean;
            status: string;
            result_snapshot: unknown;
        }>;
    }>;
    generation_attempts: unknown[];
    generation_request_receipts: unknown[];
};

const mutableBackendWireProject = (): MutableBackendWireProject => clone(backendWireProject) as unknown as MutableBackendWireProject;

type ParsedToolCall = {
    preview_binding?: { agent_authorization_ref: { approval_id: string } };
    approval_record?: { approval_id?: string; decision?: string } | null;
};

const firstParsedToolCall = (parsed: { agent_turns: unknown[] }): ParsedToolCall | undefined => {
    const firstTurn = parsed.agent_turns[0] as { tool_calls?: unknown[] } | undefined;
    return firstTurn?.tool_calls?.[0] as ParsedToolCall | undefined;
};

const firstMutableToolCall = (projectValue: MutableBackendWireProject): Record<string, unknown> => (
    projectValue.agent_turns[0].tool_calls[0] as unknown as Record<string, unknown>
);

const firstMutableBindingProviderExecution = (projectValue: MutableBackendWireProject): Record<string, unknown> => {
    const binding = firstMutableToolCall(projectValue).preview_binding as Record<string, unknown>;
    return binding.provider_execution as Record<string, unknown>;
};

const firstMutableAttemptProviderExecution = (projectValue: MutableBackendWireProject): Record<string, unknown> => {
    const attemptRecord = projectValue.generation_attempts[0] as Record<string, unknown>;
    return attemptRecord.provider_execution as Record<string, unknown>;
};

const agentTurn = (overrides: Record<string, unknown> = {}) => ({
    id: "turn-1",
    project_id: "project-1",
    user_message: "generate",
    status: "completed",
    tool_calls: [toolCall()],
    ...overrides,
});

const attempt = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 1,
    project_id: "project-1",
    node_id: "video",
    candidate_id: "candidate-1",
    attempt_id: "attempt-1",
    attempt_revision: 0,
    status: "pending",
    error_code: null,
    first_frame_source: { node_id: "image", media_url: "uploads/frame.png" },
    output_ref: null,
    provider_execution: providerRecord(),
    created_at: 10,
    updated_at: 10,
    ...overrides,
});

const receipt = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 1,
    project_id: "project-1",
    entrypoint: "atelier.provider_video.v1",
    request_contract_version: "request-json-v1",
    client_request_id: "request-1",
    request_fingerprint: HASH_D,
    node_id: "video",
    candidate_id: "candidate-1",
    attempt_id: "attempt-1",
    execution_fingerprint: HASH_A,
    base_revision: 0,
    committed_revision: 1,
    created_at: 10,
    ...overrides,
});

const node = (overrides: Record<string, unknown> = {}) => ({
    id: "video",
    project_id: "project-1",
    type: "video",
    title: "",
    prompt: "",
    status: "draft",
    x: 0,
    y: 0,
    width: 320,
    height: 180,
    source_project_id: null,
    frame_id: null,
    asset_id: null,
    video_task_id: null,
    media_urls: [],
    data: { candidates: [candidate()] },
    created_by: "user",
    created_at: 1,
    updated_at: 10,
    ...overrides,
});

const project = (overrides: Record<string, unknown> = {}) => ({
    id: "project-1",
    title: "Canvas",
    description: "",
    source_project_id: null,
    nodes: [node()],
    agent_policy: { approval_mode: "untrusted", allowed_tools: [], max_nodes_per_action: 8, max_tokens_per_turn: 2048, enable_reviewer: false, max_generation_cost_per_turn: 5, updated_at: 1 },
    agent_turns: [],
    sequence: [],
    audio_tracks: [],
    exports: [],
    node_delete_receipts: [],
    sequence_export_jobs: [],
    node_upload_receipts: [],
    generation_attempts: [attempt()],
    generation_request_receipts: [receipt()],
    agent_compaction_summary: null,
    compacted_through_turn_id: null,
    revision: 1,
    created_at: 1,
    updated_at: 10,
    ...overrides,
});

describe("Atelier Agent generation authorization contract", () => {
    it("accepts the real backend wire fixture for binding, approval record, and schema 2 attempt refs", () => {
        const parsed = parseAtelierProject(clone(backendWireProject));
        const attemptRecord = parsed.generation_attempts[0];
        const call = firstParsedToolCall(parsed);

        expect(attemptRecord.schema_version).toBe(2);
        expect(attemptRecord.agent_authorization_ref?.approval_id).toBe("approval");
        expect(call?.preview_binding?.agent_authorization_ref.approval_id).toBe("approval");
        expect(call?.approval_record).toMatchObject({ approval_id: "approval", decision: "approved" });
    });

    it("keeps pending preview bindings readable before admission creates attempts or receipts", () => {
        const pendingPreview = mutableBackendWireProject();
        pendingPreview.agent_turns[0].status = "waiting_approval";
        pendingPreview.agent_turns[0].tool_calls[0].status = "approval_required";
        pendingPreview.agent_turns[0].tool_calls[0].approval_record = null;
        pendingPreview.agent_turns[0].tool_calls[0].approval_granted = false;
        pendingPreview.agent_turns[0].tool_calls[0].result_snapshot = null;
        pendingPreview.nodes[1].data.candidates = [];
        pendingPreview.generation_attempts = [];
        pendingPreview.generation_request_receipts = [];
        const parsed = parseAtelierProject(pendingPreview);

        expect(firstParsedToolCall(parsed)?.preview_binding?.agent_authorization_ref.approval_id).toBe("approval");
        expect(firstParsedToolCall(parsed)?.approval_record).toBeNull();
        expect(parsed.generation_attempts).toHaveLength(0);
    });

    it("rejects admitted schema 2 attempts when the linked approval record is missing", () => {
        const missingApproval = mutableBackendWireProject();
        missingApproval.agent_turns[0].tool_calls[0].approval_record = null;

        expect(() => parseAtelierProject(missingApproval)).toThrow();
    });

    it("rejects multiple schema 2 attempts bound to the same agent authorization", () => {
        const duplicate = mutableBackendWireProject();
        const originalAttempt = clone(duplicate.generation_attempts[0]);
        const originalReceipt = clone(duplicate.generation_request_receipts[0]) as Record<string, unknown>;
        const originalCandidate = clone(duplicate.nodes[1].data.candidates?.[0]);
        duplicate.generation_attempts.push({ ...(originalAttempt as Record<string, unknown>), attempt_id: "duplicate-attempt", candidate_id: "duplicate-candidate", provider_execution: { ...((originalAttempt as Record<string, unknown>).provider_execution as Record<string, unknown>), attempt_id: "duplicate-attempt", fingerprint: "f".repeat(64) } });
        duplicate.generation_request_receipts.push({ ...originalReceipt, attempt_id: "duplicate-attempt", candidate_id: "duplicate-candidate", client_request_id: "request-duplicate", execution_fingerprint: "f".repeat(64) });
        duplicate.nodes[1].data.candidates = [originalCandidate, { ...(originalCandidate as Record<string, unknown>), id: "duplicate-candidate", attempt_id: "duplicate-attempt" }];

        expect(() => parseAtelierProject(duplicate)).toThrow();
    });

    it("rejects bindings whose preview provider execution is no longer an accepted local preview", () => {
        const submitted = mutableBackendWireProject();
        firstMutableBindingProviderExecution(submitted).phase = "submitted";
        expect(() => parseAtelierProject(submitted)).toThrow();

        const withHandle = mutableBackendWireProject();
        firstMutableBindingProviderExecution(withHandle).handle = { task_id: "provider-task" };
        expect(() => parseAtelierProject(withHandle)).toThrow();
    });

    it("compares schema 2 attempt and binding provider identity without depending on object key order", () => {
        const reordered = mutableBackendWireProject();
        const attemptExecution = firstMutableAttemptProviderExecution(reordered);
        const intent = attemptExecution.intent as Record<string, unknown>;
        attemptExecution.intent = Object.fromEntries(Object.entries(intent).reverse());
        const scope = attemptExecution.scope as Record<string, unknown>;
        attemptExecution.scope = Object.fromEntries(Object.entries(scope).reverse());

        expect(() => parseAtelierProject(reordered)).not.toThrow();
    });

    it("rejects schema 2 attempts whose stable provider identity diverges from the binding preview", () => {
        const changedCredential = mutableBackendWireProject();
        firstMutableAttemptProviderExecution(changedCredential).credential_ref = "different-bound-key";
        expect(() => parseAtelierProject(changedCredential)).toThrow();

        const changedIntent = mutableBackendWireProject();
        const intent = firstMutableAttemptProviderExecution(changedIntent).intent as Record<string, unknown>;
        changedIntent.generation_attempts[0] = {
            ...(changedIntent.generation_attempts[0] as Record<string, unknown>),
            provider_execution: {
                ...firstMutableAttemptProviderExecution(changedIntent),
                intent: { ...intent, prompt: "Different prompt" },
            },
        };
        expect(() => parseAtelierProject(changedIntent)).toThrow();
    });

    it("keeps revoked approval records for already admitted history readable", () => {
        const revoked = mutableBackendWireProject();
        const approval = revoked.agent_turns[0].tool_calls[0].approval_record;
        if (approval === null) throw new Error("fixture approval record missing");
        approval.decision = "revoked";
        const parsed = parseAtelierProject(revoked);

        expect(firstParsedToolCall(parsed)?.approval_record?.decision).toBe("revoked");
        expect(parsed.generation_attempts[0].schema_version).toBe(2);
    });

    it("keeps manual schema 1 attempts accepted without an agent authorization ref", () => {
        const parsed = parseAtelierProject(project());
        expect(parsed.generation_attempts[0].schema_version).toBe(1);
        expect("agent_authorization_ref" in parsed.generation_attempts[0]).toBe(false);
    });

    it("accepts schema 2 Agent attempts when backed by a preview binding, approval record, and matching receipt", () => {
        const agentAttempt = attempt({ schema_version: 2, agent_authorization_ref: authorizationRef() });
        const parsed = parseAtelierProject(project({
            agent_turns: [agentTurn()],
            generation_attempts: [agentAttempt],
        }));

        expect(parsed.generation_attempts[0].schema_version).toBe(2);
        expect(parsed.generation_attempts[0].agent_authorization_ref?.approval_id).toBe("approval-1");
    });

    it("keeps denied and revoked historical approval decisions readable", () => {
        for (const decision of ["denied", "revoked"] as const) {
            const parsed = parseAtelierProject(project({
                agent_turns: [agentTurn({ tool_calls: [toolCall({ approval_record: approvalRecord({ decision }) })] })],
                generation_attempts: [attempt({ schema_version: 2, agent_authorization_ref: authorizationRef() })],
            }));
            expect(firstParsedToolCall(parsed)?.approval_record?.decision).toBe(decision);
        }
    });

    it("rejects schema/ref mismatches and malformed agent authorization refs", () => {
        const cases = [
            attempt({ agent_authorization_ref: authorizationRef() }),
            attempt({ schema_version: 2 }),
            attempt({ schema_version: 2, agent_authorization_ref: null }),
            attempt({ schema_version: 2, agent_authorization_ref: { ...authorizationRef(), extra: true } }),
            attempt({ schema_version: 2, agent_authorization_ref: authorizationRef({ authorization_hash: "not-a-hash" }) }),
            attempt({ schema_version: 2, agent_authorization_ref: authorizationRef({ project_id: "other" }) }),
        ];

        for (const item of cases) {
            expect(() => parseAtelierProject(project({
                agent_turns: [agentTurn()],
                generation_attempts: [item],
            }))).toThrow();
        }
    });

    it("rejects Agent attempts whose authorization does not match receipt or turn binding records", () => {
        const agentAttempt = attempt({ schema_version: 2, agent_authorization_ref: authorizationRef() });
        const cases = [
            { generation_request_receipts: [receipt({ request_fingerprint: "f".repeat(64) })] },
            { agent_turns: [agentTurn({ id: "turn-other" })] },
            { agent_turns: [agentTurn({ tool_calls: [toolCall({ call_id: "call-other" })] })] },
            { agent_turns: [agentTurn({ tool_calls: [toolCall({ approval_record: approvalRecord({ authorization_hash: "f".repeat(64) }) })] })] },
            { agent_turns: [agentTurn({ tool_calls: [toolCall({ preview_binding: previewBinding({ agent_authorization_ref: authorizationRef({ request_fingerprint: "f".repeat(64) }) }) })] })] },
        ];

        for (const item of cases) {
            expect(() => parseAtelierProject(project({
                agent_turns: [agentTurn()],
                generation_attempts: [agentAttempt],
                ...item,
            }))).toThrow();
        }
    });

    it("validates authority-bearing turn fields while keeping old turns opaque", () => {
        expect(() => parseAtelierProject(project({
            agent_turns: [{ id: "legacy-turn", project_id: "project-1", tool_calls: [{ call_id: "legacy-call", tool_name: TOOL_NAME }] }],
        }))).not.toThrow();

        expect(() => parseAtelierProject(project({
            agent_turns: [agentTurn({ tool_calls: [toolCall({ approval_record: approvalRecord({ extra: true }) })] })],
        }))).toThrow();
        expect(() => parseAtelierProject(project({
            agent_turns: [agentTurn({ tool_calls: [toolCallWithoutPreviewBinding()] })],
        }))).toThrow();
        expect(() => parseAtelierProject(project({
            agent_turns: [agentTurn({ tool_calls: [toolCall({ preview_binding: previewBinding({ extra: true }) })] })],
        }))).toThrow();
        expect(() => parseAtelierProject(project({
            agent_turns: [agentTurn({ tool_calls: [toolCall({ preview_binding: previewBinding({ agent_authorization_ref: authorizationRef({ project_id: "other" }) }) })] })],
        }))).toThrow();
    });
});
