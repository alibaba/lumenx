// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API_URL } from "@/lib/apiUrl";
import { parseAtelierProject } from "@/lib/atelierApi";
import { atelierGenerationApi, parseAtelierGenerationAdmissionAck } from "@/lib/atelierGenerationApi";
import packageFixture from "./fixtures/atelier-project-package-wire.json";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const STORAGE_B = "22222222-2222-4222-8222-222222222222";
const HASH = "a".repeat(64);
const REQUEST_HASH = "b".repeat(64);

const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
});

const providerRecord = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 1,
    attempt_id: "attempt-1",
    phase: "accepted",
    provider_model_id: "wan2.7-i2v",
    intent: {
        rules_version: "generation-parameters-v1",
        scope: "parameters-only",
        model_id: "wan/wan2.7-video#i2v",
        catalog_mode: "i2v",
        mode: "i2v",
        prompt: "Slow sunrise",
        params: { duration: 5, resolution: "1080p" },
        sources: { model_id: "request", mode: "request", prompt: "request" },
        request_fields: ["mode", "model_id", "prompt", "params"],
        preset_id: null,
        preset_version: null,
    },
    media: [],
    scope: {
        provider: "dashscope",
        backend: "dashscope",
        gateway: "dashscope",
        base_url: "https://dashscope.aliyuncs.com",
        region: "cn-beijing",
        workspace_id: null,
        account_identity: null,
    },
    credential_ref: "credential-ref",
    fingerprint: HASH,
    handle: null,
    observation: null,
    prepared_artifact: null,
    artifact: null,
    ...overrides,
});

const generationCandidate = (overrides: Record<string, unknown> = {}) => ({
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

const generationAttempt = (overrides: Record<string, unknown> = {}) => ({
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

const generationReceipt = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 1,
    project_id: "project-1",
    entrypoint: "atelier.provider_video.v1",
    request_contract_version: "request-json-v1",
    client_request_id: "request-1",
    request_fingerprint: REQUEST_HASH,
    node_id: "video",
    candidate_id: "candidate-1",
    attempt_id: "attempt-1",
    execution_fingerprint: HASH,
    base_revision: 0,
    committed_revision: 1,
    created_at: 10,
    ...overrides,
});

const batchAttempt = () => generationAttempt({
    candidate_id: "candidate-2",
    attempt_id: "attempt-2",
    provider_execution: providerRecord({ attempt_id: "attempt-2", fingerprint: "c".repeat(64) }),
});

const batchCandidate = () => generationCandidate({ id: "candidate-2", attempt_id: "attempt-2" });

const generationReceiptMember = (candidateId: string, attemptId: string, fingerprint: string) => ({
    candidate_id: candidateId,
    attempt_id: attemptId,
    execution_fingerprint: fingerprint,
});

const generationBatchReceipt = (overrides: Record<string, unknown> = {}) => ({
    schema_version: 2,
    project_id: "project-1",
    entrypoint: "atelier.provider_video.v1",
    request_contract_version: "request-json-v1",
    client_request_id: "request-1",
    request_fingerprint: REQUEST_HASH,
    node_id: "video",
    members: [
        generationReceiptMember("candidate-1", "attempt-1", HASH),
        generationReceiptMember("candidate-2", "attempt-2", "c".repeat(64)),
    ],
    base_revision: 0,
    committed_revision: 1,
    created_at: 10,
    ...overrides,
});

const reordered = (value: Record<string, unknown>): Record<string, unknown> => Object.fromEntries(Object.entries(value).reverse());

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
    data: { candidates: [generationCandidate()] },
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
    agent_policy: {
        approval_mode: "untrusted",
        allowed_tools: [],
        max_nodes_per_action: 8,
        max_tokens_per_turn: 2048,
        enable_reviewer: false,
        max_generation_cost_per_turn: 5,
        updated_at: 1,
    },
    agent_turns: [],
    sequence: [],
    audio_tracks: [],
    exports: [],
    node_delete_receipts: [],
    sequence_export_jobs: [],
    node_upload_receipts: [],
    generation_attempts: [generationAttempt()],
    generation_request_receipts: [generationReceipt()],
    agent_compaction_summary: null,
    compacted_through_turn_id: null,
    revision: 1,
    created_at: 1,
    updated_at: 10,
    ...overrides,
});

const createRequest = {
    expected_revision: 0,
    client_request_id: "request-1",
    model_id: "wan/wan2.7-video#i2v",
    mode: "i2v" as const,
    prompt: "Slow sunrise",
    first_frame: { node_id: "image", media_url: "uploads/frame.png" },
    params: { duration: 5, resolution: "1080p" },
};

type LegacyWireRequest = {
    expected_revision: number;
    client_request_id: string;
    model_id: string;
    mode: "i2v";
    prompt: string;
    first_frame: { node_id: string; media_url: string };
    params?: Record<string, unknown>;
    batch_size?: number;
};

const importedPackageProject = (): Record<string, unknown> => structuredClone(packageFixture.prepared_project) as Record<string, unknown>;
const selectableImportedPackageProject = (): Record<string, unknown> => {
    const raw = importedPackageProject();
    importedPackageCandidate(raw).media_kind = "video";
    return raw;
};
const importedPackageNode = (raw: Record<string, unknown>): Record<string, unknown> => {
    const nodes = raw.nodes as Array<Record<string, unknown>>;
    return nodes.find(item => item.id === "fixture-node-3") as Record<string, unknown>;
};
const importedPackageCandidate = (raw: Record<string, unknown>): Record<string, unknown> => {
    const node = importedPackageNode(raw);
    const data = node.data as Record<string, unknown>;
    const candidates = data.candidates as Array<Record<string, unknown>>;
    return candidates.find(item => item.id === "fixture-candidate") as Record<string, unknown>;
};


const requestIntent = (request: Pick<LegacyWireRequest, "model_id" | "mode" | "prompt"> | Record<string, unknown>, params: Record<string, unknown> | undefined = (request as { params?: Record<string, unknown> }).params) => ({
    rules_version: "generation-parameters-v1",
    scope: "parameters-only",
    model_id: request.model_id,
    catalog_mode: request.mode,
    mode: request.mode,
    prompt: request.prompt,
    ...(params === undefined ? {} : { params }),
    sources: { model_id: "request", mode: "request", prompt: "request" },
    request_fields: params === undefined ? ["mode", "model_id", "prompt"] : ["mode", "model_id", "prompt", "params"],
    preset_id: null,
    preset_version: null,
});

const legacyWireForRequest = (request: LegacyWireRequest, options: { batch?: boolean } = {}) => {
    const batch = options.batch === true;
    const baseReceipt = batch ? generationBatchReceipt({
        client_request_id: request.client_request_id,
        base_revision: request.expected_revision,
    }) : generationReceipt({
        client_request_id: request.client_request_id,
        base_revision: request.expected_revision,
    });
    const first = generationAttempt({
        first_frame_source: request.first_frame,
        provider_execution: providerRecord({ intent: requestIntent(request) }),
    });
    if (!batch) {
        return admissionAck({
            receipt: baseReceipt,
            attempt: first,
            project: project({ generation_attempts: [first], generation_request_receipts: [baseReceipt], nodes: [node({ data: { candidates: [generationCandidate()] } })] }),
        });
    }
    const second = batchAttempt();
    second.first_frame_source = request.first_frame;
    second.provider_execution = providerRecord({ attempt_id: "attempt-2", fingerprint: "c".repeat(64), intent: requestIntent(request) });
    return batchAdmissionAck({
        receipt: baseReceipt,
        members: [{ attempt: first, candidate: generationCandidate() }, { attempt: second, candidate: batchCandidate() }],
        project: batchProject({ generation_attempts: [first, second], generation_request_receipts: [baseReceipt] }),
    });
};

const driftAdmissionIntent = (wire: Record<string, unknown>, mutate: (intent: Record<string, unknown>) => void): Record<string, unknown> => {
    const copy = structuredClone(wire);
    const attempts: Array<Record<string, unknown>> = "members" in copy
        ? (copy.members as Array<Record<string, unknown>>).map(member => member.attempt as Record<string, unknown>)
        : [copy.attempt as Record<string, unknown>];
    const projectAttempts = ((copy.project as Record<string, unknown>).generation_attempts as Array<Record<string, unknown>>);
    for (const attempt of attempts) {
        const execution = attempt.provider_execution as Record<string, unknown>;
        const intent = structuredClone(execution.intent as Record<string, unknown>);
        mutate(intent);
        execution.intent = intent;
        const saved = projectAttempts.find(item => item.attempt_id === attempt.attempt_id);
        if (saved) (saved.provider_execution as Record<string, unknown>).intent = structuredClone(intent);
    }
    return copy;
};

const admissionAck = (overrides: Record<string, unknown> = {}) => ({
    project: project(),
    receipt: generationReceipt(),
    attempt: generationAttempt(),
    candidate: generationCandidate(),
    replayed: false,
    new_candidate_ids: ["candidate-1"],
    reason: null,
    ...overrides,
});

const batchProject = (overrides: Record<string, unknown> = {}) => project({
    nodes: [node({ data: { candidates: [generationCandidate(), batchCandidate()] } })],
    generation_attempts: [generationAttempt(), batchAttempt()],
    generation_request_receipts: [generationBatchReceipt()],
    ...overrides,
});

const batchAdmissionAck = (overrides: Record<string, unknown> = {}) => ({
    project: batchProject(),
    receipt: generationBatchReceipt(),
    members: [
        { attempt: generationAttempt(), candidate: generationCandidate() },
        { attempt: batchAttempt(), candidate: batchCandidate() },
    ],
    replayed: false,
    new_candidate_ids: ["candidate-1", "candidate-2"],
    reason: null,
    ...overrides,
});

beforeEach(() => {
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
});

describe("atelier generation API client", () => {
    it("preserves generation ledgers and defaults missing legacy ledgers to empty arrays", () => {
        const parsed = parseAtelierProject(project());
        expect(parsed.generation_attempts).toHaveLength(1);
        expect(parsed.generation_request_receipts).toHaveLength(1);
        expect(parsed.nodes[0].data.candidates).toEqual([generationCandidate()]);

        const legacy: Record<string, unknown> = project({
            generation_attempts: undefined,
            generation_request_receipts: undefined,
            nodes: [node({ data: { candidates: [{ id: "legacy-candidate", video_url: "video/legacy.mp4" }] } })],
        });
        delete legacy.generation_attempts;
        delete legacy.generation_request_receipts;

        const parsedLegacy = parseAtelierProject(legacy);
        expect(parsedLegacy.generation_attempts).toEqual([]);
        expect(parsedLegacy.generation_request_receipts).toEqual([]);
    });

    it("rejects unknown generation contracts and mismatched candidate output binding", () => {
        expect(() => parseAtelierProject(project({
            nodes: [node({ data: { candidates: [generationCandidate({ execution_contract: "future-contract" })] } })],
        }))).toThrow();

        const completedAttempt = generationAttempt({
            status: "completed",
            output_ref: "video/atelier-candidate-1-attempt-1.mp4",
            provider_execution: providerRecord({
                phase: "completed",
                handle: {
                    provider_model_id: "wan2.7-i2v",
                    task_id: "remote-1",
                    scope: providerRecord().scope,
                    credential_ref: "credential-ref",
                    request_id: "submit-1",
                    query_capability: "by_task_id",
                    submitted_at: 12,
                },
                artifact: {
                    schema_version: 1,
                    attempt_id: "attempt-1",
                    handle: {
                        provider_model_id: "wan2.7-i2v",
                        task_id: "remote-1",
                        scope: providerRecord().scope,
                        credential_ref: "credential-ref",
                        request_id: "submit-1",
                        query_capability: "by_task_id",
                        submitted_at: 12,
                    },
                    relative_path: "video/atelier-candidate-1-attempt-1.mp4",
                    sha256: "c".repeat(64),
                    size_bytes: 12,
                },
            }),
        });
        expect(() => parseAtelierProject(project({
            nodes: [node({ data: { candidates: [generationCandidate({ status: "completed", video_url: "video/other.mp4" })] } })],
            generation_attempts: [completedAttempt],
        }))).toThrow();
    });

    it("creates a video candidate with the strict request body and binds the admission ACK", async () => {
        const fetch = vi.fn().mockResolvedValue(response(admissionAck()));
        vi.stubGlobal("fetch", fetch);

        const ack = await atelierGenerationApi.createVideoCandidate("project-1", "video", createRequest);

        expect(ack.new_candidate_ids).toEqual(["candidate-1"]);
        expect(ack.members[0].attempt.provider_execution.credential_ref).toBe("credential-ref");
        expect(fetch).toHaveBeenCalledWith(`${API_URL}/atelier/projects/project-1/nodes/video/video_candidates`, expect.objectContaining({
            method: "POST",
            credentials: "include",
        }));
        expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toEqual(createRequest);
    });

    it("parses batch generation receipts and plural ACK members strictly", async () => {
        const parsed = parseAtelierProject(batchProject());
        expect(parsed.generation_request_receipts[0]).toMatchObject({ schema_version: 2, client_request_id: "request-1" });
        expect(parsed.generation_request_receipts[0].members?.map(member => member.candidate_id)).toEqual(["candidate-1", "candidate-2"]);

        const fetch = vi.fn().mockResolvedValue(response(batchAdmissionAck()));
        vi.stubGlobal("fetch", fetch);

        const ack = await atelierGenerationApi.createVideoCandidate("project-1", "video", { ...createRequest, batch_size: 2 } as never);

        expect(ack.members.map(member => member.attempt.attempt_id)).toEqual(["attempt-1", "attempt-2"]);
        expect(ack.members.map(member => member.candidate?.id)).toEqual(["candidate-1", "candidate-2"]);
        expect(Object.prototype.hasOwnProperty.call(ack, "attempt")).toBe(false);
        expect(Object.prototype.hasOwnProperty.call(ack, "candidate")).toBe(false);
        expect(ack.new_candidate_ids).toEqual(["candidate-1", "candidate-2"]);
        expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toEqual({ ...createRequest, batch_size: 2 });
    });

    it("rejects malformed batch ACK aliases, order, fingerprint, and new ids", () => {
        const cases = [
            batchAdmissionAck({ attempt: generationAttempt() }),
            batchAdmissionAck({ members: [
                { attempt: batchAttempt(), candidate: batchCandidate() },
                { attempt: generationAttempt(), candidate: generationCandidate() },
            ] }),
            batchAdmissionAck({ receipt: generationBatchReceipt({ members: [
                generationReceiptMember("candidate-1", "attempt-1", "d".repeat(64)),
                generationReceiptMember("candidate-2", "attempt-2", "c".repeat(64)),
            ] }) }),
            batchAdmissionAck({ new_candidate_ids: ["candidate-2", "candidate-1"] }),
        ];

        for (const wire of cases) {
            expect(() => parseAtelierGenerationAdmissionAck(wire, "project-1", { nodeId: "video", request: { ...createRequest, batch_size: 2 } as never, creating: true, allowBatch: true })).toThrow();
        }
    });

    it("keeps singleton agent admission parsing from accepting batch wire", () => {
        expect(() => parseAtelierGenerationAdmissionAck(batchAdmissionAck(), "project-1", { creating: true })).toThrow();
    });

    it("rejects invalid create payloads before fetch", async () => {
        const fetch = vi.fn();
        vi.stubGlobal("fetch", fetch);

        await expect(atelierGenerationApi.createVideoCandidate("project-1", "video", { ...createRequest, params: { duration: null } } as never)).rejects.toThrow();
        await expect(atelierGenerationApi.createVideoCandidate("project-1", "video", { ...createRequest, params: { unknown: true } } as never)).rejects.toThrow();
        await expect(atelierGenerationApi.createVideoCandidate("project-1", "video", {
            ...createRequest,
            first_frame: { node_id: "image", media_url: "uploads/frame.png", extra: "x" },
        } as never)).rejects.toThrow();
        await expect(atelierGenerationApi.createVideoCandidate("project-1", "video", { ...createRequest, client_request_id: "bad id" } as never)).rejects.toThrow();

        expect(fetch).not.toHaveBeenCalled();
    });

    it("saves generation draft settings through strict node ACK binding", async () => {
        const draft = { schema_version: 1, model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: { duration: 0, seed: -1, prompt_extend: false, watermark: false } };
        const savedProject = project({
            revision: 3,
            nodes: [node({ prompt: "", data: { generation_draft: draft } })],
            generation_attempts: [],
            generation_request_receipts: [],
        });
        const fetch = vi.fn().mockResolvedValue(response({ project: savedProject, node_ids: ["video"] }));
        vi.stubGlobal("fetch", fetch);

        const ack = await atelierGenerationApi.setGenerationDraft("project-1", "video", {
            expected_revision: 2,
            prompt: "",
            model_id: "wan/wan2.7-video#i2v",
            mode: "i2v",
            params: { duration: 0, seed: -1, prompt_extend: false, watermark: false },
        });

        expect(ack.project.nodes[0].data.generation_draft).toEqual(draft);
        expect(fetch).toHaveBeenCalledWith(`${API_URL}/atelier/projects/project-1/nodes/video/generation_draft`, expect.objectContaining({
            method: "PUT",
            credentials: "include",
        }));
        expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toEqual({
            expected_revision: 2,
            prompt: "",
            model_id: "wan/wan2.7-video#i2v",
            mode: "i2v",
            params: { duration: 0, seed: -1, prompt_extend: false, watermark: false },
        });
    });

    it("saves ordered generation draft settings through the same strict node ACK binding", async () => {
        const media = [
            { role: "last_frame" as const, ordinal: 0, node_id: "last", media_url: "uploads/last.png" },
        ];
        const draft = {
            schema_version: 2,
            input_contract_version: "ordered-media-v1",
            model_id: "wan/wan3.0-video#video",
            mode: "i2v",
            params: { duration: -1, seed: 2147483647, prompt_extend: false, watermark: false, audio: false },
            media,
            batch_size: 1,
        };
        const savedProject = project({
            revision: 3,
            nodes: [node({ prompt: "", data: { generation_draft: draft } })],
            generation_attempts: [],
            generation_request_receipts: [],
        });
        const fetch = vi.fn().mockResolvedValue(response({ project: savedProject, node_ids: ["video"] }));
        vi.stubGlobal("fetch", fetch);

        const ack = await atelierGenerationApi.setGenerationDraft("project-1", "video", {
            expected_revision: 2,
            contract_version: "ordered-media-v1",
            prompt: "",
            model_id: "wan/wan3.0-video#video",
            mode: "i2v",
            params: { duration: -1, seed: 2147483647, prompt_extend: false, watermark: false, audio: false },
            media,
        } as never);

        expect(ack.project.nodes[0].data.generation_draft).toEqual(draft);
        expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toEqual({
            expected_revision: 2,
            contract_version: "ordered-media-v1",
            prompt: "",
            model_id: "wan/wan3.0-video#video",
            mode: "i2v",
            params: { duration: -1, seed: 2147483647, prompt_extend: false, watermark: false, audio: false },
            media,
        });
    });

    it("rejects malformed generation draft requests and acknowledgements", async () => {
        const fetch = vi.fn();
        vi.stubGlobal("fetch", fetch);
        await expect(atelierGenerationApi.setGenerationDraft("project-1", "video", {
            expected_revision: 2, prompt: "p", model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: { unknown: true },
        } as never)).rejects.toThrow();
        await expect(atelierGenerationApi.setGenerationDraft("project-1", "video", {
            expected_revision: 2, prompt: "p", model_id: "other", mode: "i2v", params: {},
        } as never)).rejects.toThrow();
        expect(fetch).not.toHaveBeenCalled();

        await expect(atelierGenerationApi.setGenerationDraft("project-1", "video", {
            expected_revision: 2, contract_version: "ordered-media-v1", prompt: "p", model_id: "wan/wan3.0-video#video", mode: "r2v", params: {},
            media: [
                { role: "reference_audio", ordinal: 0, node_id: "audio", media_url: "uploads/audio.wav" },
                { role: "reference_image", ordinal: 0, node_id: "image", media_url: "uploads/image.png" },
            ],
        } as never)).rejects.toThrow();
        await expect(atelierGenerationApi.setGenerationDraft("project-1", "video", {
            expected_revision: 2, contract_version: "ordered-media-v1", prompt: "p", model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: {}, media: [],
        } as never)).rejects.toThrow();
        expect(fetch).not.toHaveBeenCalled();

        for (const params of [
            { duration: 0 },
            { duration: 31 },
            { duration: 2.5 },
            { seed: -2 },
            { seed: 2147483648 },
            { resolution: "4K" },
            { ratio: "2:1" },
        ]) {
            await expect(atelierGenerationApi.setGenerationDraft("project-1", "video", {
                expected_revision: 2, contract_version: "ordered-media-v1", prompt: "p", model_id: "wan/wan3.0-video#video", mode: "t2v", params, media: [],
            } as never)).rejects.toThrow();
        }
        expect(fetch).not.toHaveBeenCalled();

        const badDraftProject = project({
            revision: 3,
            nodes: [node({ prompt: "p", data: { generation_draft: { schema_version: 2, model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: {} } } })],
            generation_attempts: [],
            generation_request_receipts: [],
        });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ project: badDraftProject, node_ids: ["video"] })));
        await expect(atelierGenerationApi.setGenerationDraft("project-1", "video", {
            expected_revision: 2, prompt: "p", model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: {},
        })).rejects.toMatchObject({ code: "atelier_generation_ack_mismatch" });

        const orderedBadAck = project({
            revision: 3,
            nodes: [node({ prompt: "p", data: { generation_draft: {
                schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video#video", mode: "t2v", params: {}, media: [], batch_size: 2,
            } } })],
            generation_attempts: [],
            generation_request_receipts: [],
        });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ project: orderedBadAck, node_ids: ["video"] })));
        await expect(atelierGenerationApi.setGenerationDraft("project-1", "video", {
            expected_revision: 2, contract_version: "ordered-media-v1", prompt: "p", model_id: "wan/wan3.0-video#video", mode: "t2v", params: {}, media: [],
        } as never)).rejects.toMatchObject({ code: "atelier_generation_ack_mismatch" });
    });

    it.each([
        ["model", (intent: Record<string, unknown>) => { intent.model_id = "wan/other#i2v"; }],
        ["prompt", (intent: Record<string, unknown>) => { intent.prompt = "Different prompt"; }],
        ["explicit duration", (intent: Record<string, unknown>) => { (intent.params as Record<string, unknown>).duration = 6; }],
        ["explicit seed zero", (intent: Record<string, unknown>) => { (intent.params as Record<string, unknown>).seed = 1; }],
        ["explicit false", (intent: Record<string, unknown>) => { (intent.params as Record<string, unknown>).prompt_extend = true; }],
    ])("rejects self-consistent legacy singleton ACK whose intent changes %s", (_label, mutate) => {
        const request = { ...createRequest, params: { duration: 5, resolution: "1080p", seed: 0, prompt_extend: false, watermark: false } };
        const wire = driftAdmissionIntent(legacyWireForRequest(request), mutate);

        expect(() => parseAtelierGenerationAdmissionAck(wire, "project-1", { nodeId: "video", request, creating: true })).toThrow();
    });

    it("rejects self-consistent legacy batch ACKs whose provider intent drifts from the original request", () => {
        const request = { ...createRequest, batch_size: 2, params: { duration: 5, seed: 0, prompt_extend: false, watermark: false } };
        const wire = driftAdmissionIntent(legacyWireForRequest(request, { batch: true }), intent => { intent.prompt = "batch drift"; });

        expect(() => parseAtelierGenerationAdmissionAck(wire, "project-1", { nodeId: "video", request, creating: true, allowBatch: true })).toThrow();
    });

    it("rejects ordered ACKs whose provider intent drifts from the original ordered request", () => {
        const request = {
            expected_revision: 0,
            client_request_id: "request-1",
            contract_version: "ordered-media-v1" as const,
            model_id: "wan/wan3.0-video#video" as const,
            mode: "t2v" as const,
            prompt: "Slow sunrise",
            media: [],
            params: { duration: -1, seed: 0, prompt_extend: false, watermark: false, audio: false },
        };
        const attempt = {
            ...generationAttempt({ provider_execution: providerRecord({ intent: requestIntent(request, request.params) }) }),
            input_contract_version: "ordered-media-v1",
            input_media: [],
        };
        delete (attempt as Record<string, unknown>).first_frame_source;
        const receipt = generationReceipt({ entrypoint: "atelier.provider_video.v2" });
        const wire = driftAdmissionIntent(admissionAck({
            receipt,
            attempt,
            project: project({ generation_attempts: [attempt], generation_request_receipts: [receipt], nodes: [node({ data: { candidates: [generationCandidate()] } })] }),
        }), intent => { (intent.params as Record<string, unknown>).seed = 1; });

        expect(() => parseAtelierGenerationAdmissionAck(wire, "project-1", { nodeId: "video", request, creating: true })).toThrow();
    });

    it("binds GET readback to the original request but allows omitted params without catalog defaults", async () => {
        const omittedParamsRequest = { ...createRequest };
        delete (omittedParamsRequest as Record<string, unknown>).params;
        const replay = { ...legacyWireForRequest(omittedParamsRequest, { batch: false }), replayed: true, new_candidate_ids: [] };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(replay)));
        await expect(atelierGenerationApi.getRequest("project-1", "request-1", omittedParamsRequest)).resolves.toMatchObject({ replayed: true });

        const drifted = driftAdmissionIntent({ ...legacyWireForRequest(omittedParamsRequest, { batch: false }), replayed: true, new_candidate_ids: [] }, intent => { intent.prompt = "drift on readback"; });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(drifted)));
        await expect(atelierGenerationApi.getRequest("project-1", "request-1", omittedParamsRequest)).rejects.toMatchObject({ code: "atelier_generation_ack_mismatch" });
    });

    it("rejects dispatch-unavailable create responses without retrying", async () => {
        const fetch = vi.fn().mockResolvedValue(response({
            detail: {
                code: "atelier_generation_dispatch_unavailable",
                detail: "saved but not scheduled",
                project_id: "project-1",
                attempt_id: "attempt-1",
                client_request_id: "request-1",
                replayed: false,
            },
        }, 503));
        vi.stubGlobal("fetch", fetch);

        await expect(atelierGenerationApi.createVideoCandidate("project-1", "video", createRequest)).rejects.toMatchObject({ status: 503 });
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("reads replayed tombstone requests without requiring a live candidate or dispatch right", async () => {
        const terminalAttempt = generationAttempt({
            status: "cancelled",
            provider_execution: providerRecord({ phase: "cancelled" }),
        });
        const tombstoneProject = project({
            nodes: [],
            generation_attempts: [terminalAttempt],
            generation_request_receipts: [generationReceipt()],
            revision: 2,
        });
        const fetch = vi.fn().mockResolvedValue(response(admissionAck({
            project: tombstoneProject,
            attempt: terminalAttempt,
            candidate: null,
            replayed: true,
            new_candidate_ids: [],
            reason: "owner_missing",
        })));
        vi.stubGlobal("fetch", fetch);

        const ack = await atelierGenerationApi.getRequest("project-1", "request-1", createRequest);

        expect(ack.replayed).toBe(true);
        expect(ack.candidate).toBeNull();
        expect(ack.reason).toBe("owner_missing");
        expect(fetch).toHaveBeenCalledWith(`${API_URL}/atelier/projects/project-1/generation/requests/request-1`, expect.objectContaining({ method: "GET" }));
    });

    it("rejects cross-owner and malformed generation acknowledgements", async () => {
        const cases = [
            admissionAck({ receipt: generationReceipt({ node_id: "other-node" }) }),
            admissionAck({ attempt: generationAttempt({ attempt_id: "other-attempt" }) }),
            admissionAck({ attempt: generationAttempt({ provider_execution: providerRecord({ credential_ref: "other-credential" }) }) }),
            admissionAck({ candidate: null }),
            admissionAck({ project: project({ nodes: [node({ data: { candidates: [generationCandidate({ status: "processing" })] } })] }) }),
            admissionAck({ project: project({ generation_request_receipts: [generationReceipt({ execution_fingerprint: "c".repeat(64) })] }) }),
        ];

        for (const ack of cases) {
            vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(ack)));
            await expect(atelierGenerationApi.createVideoCandidate("project-1", "video", createRequest)).rejects.toThrow();
        }
    });

    it("accepts equivalent reordered JSON in ACKs", async () => {
        const savedAttempt = generationAttempt();
        const wireAttempt = generationAttempt({
            provider_execution: providerRecord({ intent: reordered(providerRecord().intent as Record<string, unknown>) }),
        });
        const candidate = generationCandidate();
        const receipt = generationReceipt();
        const wire = {
            project: project({ generation_attempts: [savedAttempt], generation_request_receipts: [receipt], nodes: [node({ data: { candidates: [candidate] } })] }),
            receipt: reordered(receipt),
            attempt: reordered({ ...wireAttempt, provider_execution: reordered(wireAttempt.provider_execution as Record<string, unknown>) }),
            candidate: reordered(candidate),
            replayed: false,
            new_candidate_ids: ["candidate-1"],
            reason: null,
        };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(wire)));

        await expect(atelierGenerationApi.createVideoCandidate("project-1", "video", createRequest)).resolves.toMatchObject({
            attempt: { attempt_id: "attempt-1" },
            candidate: { id: "candidate-1" },
        });
    });

    it("rejects delayed generation responses when the runtime environment changes", async () => {
        const fetch = vi.fn().mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => {
                activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
                return admissionAck();
            },
        } as Response);
        vi.stubGlobal("fetch", fetch);

        await expect(atelierGenerationApi.createVideoCandidate("project-1", "video", createRequest)).rejects.toMatchObject({ code: "environment_changed" });
        expect(fetch).toHaveBeenCalledTimes(1);
    });


    it("hides and restores candidates through strict visibility acknowledgements", async () => {
        const completedCandidate = generationCandidate({ status: "completed", video_url: "video/atelier-candidate-1-attempt-1.mp4" });
        const completedAttempt = generationAttempt({
            status: "completed",
            output_ref: "video/atelier-candidate-1-attempt-1.mp4",
            provider_execution: providerRecord({
                phase: "completed",
                artifact: {
                    schema_version: 1,
                    attempt_id: "attempt-1",
                    handle: {
                        provider_model_id: "wan2.7-i2v",
                        task_id: "remote-1",
                        scope: providerRecord().scope,
                        credential_ref: "credential-ref",
                        request_id: "submit-1",
                        query_capability: "by_task_id",
                        submitted_at: 12,
                    },
                    relative_path: "video/atelier-candidate-1-attempt-1.mp4",
                    sha256: "c".repeat(64),
                    size_bytes: 12,
                },
            }),
        });
        const hiddenProject = project({
            revision: 2,
            nodes: [node({ data: { candidates: [], deleted_candidates: [completedCandidate] }, media_urls: [] })],
            generation_attempts: [completedAttempt],
        });
        const restoredProject = project({
            revision: 3,
            nodes: [node({ data: { candidates: [completedCandidate], deleted_candidates: [], selected_candidate_id: null }, media_urls: [] })],
            generation_attempts: [completedAttempt],
        });
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ project: hiddenProject, node_ids: ["video"] }))
            .mockResolvedValueOnce(response({ project: restoredProject, node_ids: ["video"] }));
        vi.stubGlobal("fetch", fetch);

        const hidden = await atelierGenerationApi.setCandidateVisibility("project-1", "video", "candidate-1", { expected_revision: 1, visible: false });
        const restored = await atelierGenerationApi.setCandidateVisibility("project-1", "video", "candidate-1", { expected_revision: 2, visible: true });

        expect(hidden.project.nodes[0].data.deleted_candidates).toEqual([completedCandidate]);
        expect(restored.project.nodes[0].data.candidates).toEqual([completedCandidate]);
        expect(restored.project.nodes[0].data.selected_candidate_id).toBeNull();
        expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toEqual({ expected_revision: 1, visible: false });
        expect(JSON.parse(fetch.mock.calls[1][1].body as string)).toEqual({ expected_revision: 2, visible: true });
        expect(fetch.mock.calls.map(call => call[0])).toEqual([
            `${API_URL}/atelier/projects/project-1/nodes/video/video_candidates/candidate-1/visibility`,
            `${API_URL}/atelier/projects/project-1/nodes/video/video_candidates/candidate-1/visibility`,
        ]);
    });

    it("rejects visibility ACKs that do not move the requested candidate into the requested bucket", async () => {
        const completedCandidate = generationCandidate({ status: "completed", video_url: "video/atelier-candidate-1-attempt-1.mp4" });
        const completedAttempt = generationAttempt({ status: "completed", output_ref: "video/atelier-candidate-1-attempt-1.mp4", provider_execution: providerRecord({ phase: "completed" }) });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
            project: project({
                revision: 2,
                nodes: [node({ data: { candidates: [completedCandidate], deleted_candidates: [], selected_candidate_id: "candidate-1" }, media_urls: ["video/atelier-candidate-1-attempt-1.mp4"] })],
                generation_attempts: [completedAttempt],
            }),
            node_ids: ["video"],
        })));

        await expect(atelierGenerationApi.setCandidateVisibility("project-1", "video", "candidate-1", { expected_revision: 1, visible: false })).rejects.toThrow();
    });

    it("accepts legacy terminal candidate visibility movement without generation authority", async () => {
        const legacy = { id: "legacy-candidate", status: "completed", video_url: "video/legacy.mp4", title: "Imported take" };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
            project: project({ revision: 2, generation_attempts: [], generation_request_receipts: [], nodes: [node({ data: { candidates: [], deleted_candidates: [legacy] }, media_urls: [] })] }),
            node_ids: ["video"],
        })));

        const ack = await atelierGenerationApi.setCandidateVisibility("project-1", "video", "legacy-candidate", { expected_revision: 1, visible: false });

        expect(ack.project.nodes[0].data.deleted_candidates).toEqual([legacy]);
    });

    it("rejects visibility ACKs for explicit non-video legacy candidates", async () => {
        const legacy = { id: "legacy-image", status: "completed", media_kind: "image", video_url: "video/imported/project-1/take.mp4" };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
            project: project({ revision: 2, generation_attempts: [], generation_request_receipts: [], nodes: [node({ data: { candidates: [], deleted_candidates: [legacy] }, media_urls: [] })] }),
            node_ids: ["video"],
        })));

        await expect(atelierGenerationApi.setCandidateVisibility("project-1", "video", "legacy-image", { expected_revision: 1, visible: false }))
            .rejects.toMatchObject({ code: "atelier_generation_ack_mismatch" });
    });

    it("rejects a restore ACK that silently adopts the restored media", async () => {
        const candidate = { id: "legacy-candidate", status: "completed", video_url: "video/legacy.mp4" };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
            project: project({ revision: 2, generation_attempts: [], generation_request_receipts: [],
                nodes: [node({ data: { candidates: [candidate], deleted_candidates: [] }, media_urls: [candidate.video_url] })] }),
            node_ids: ["video"],
        })));
        await expect(atelierGenerationApi.setCandidateVisibility("project-1", "video", "legacy-candidate", {
            expected_revision: 1, visible: true,
        })).rejects.toMatchObject({ code: "atelier_generation_ack_mismatch" });
    });

    it("preserves a different selected imported candidate that shares the copied media", async () => {
        const candidate = { id: "restored", status: "completed", video_url: "video/imported/project-1/take.mp4" };
        const selected = { ...candidate, id: "selected" };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
            project: project({ revision: 2, generation_attempts: [], generation_request_receipts: [],
                nodes: [node({ data: { candidates: [selected, candidate], deleted_candidates: [], selected_candidate_id: selected.id },
                    media_urls: [selected.video_url] })] }),
            node_ids: ["video"],
        })));
        const ack = await atelierGenerationApi.setCandidateVisibility("project-1", "video", candidate.id, {
            expected_revision: 1, visible: true,
        });
        expect(ack.project.nodes[0].data.selected_candidate_id).toBe(selected.id);
    });

    it("selects a safe imported package candidate through the existing candidate select route", async () => {
        const imported = selectableImportedPackageProject();
        const fetch = vi.fn().mockResolvedValue(response({ project: imported, node_ids: ["fixture-node-3"] }));
        vi.stubGlobal("fetch", fetch);

        const ack = await atelierGenerationApi.selectCandidate("fixture-project", "fixture-node-3", "fixture-candidate", { expected_revision: 0 });

        expect(ack.project.nodes.find(item => item.id === "fixture-node-3")?.media_urls).toEqual(["video/imported/fixture-project/take.mp4"]);
        expect(fetch).toHaveBeenCalledWith(`${API_URL}/atelier/projects/fixture-project/nodes/fixture-node-3/video_candidates/fixture-candidate/select`, expect.objectContaining({
            method: "POST",
            credentials: "include",
        }));
        expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toEqual({ expected_revision: 0 });
    });

    it.each([
        ["missing provenance", (raw: Record<string, unknown>) => { delete raw.import_provenance; }],
        ["authority-bearing candidate", (raw: Record<string, unknown>) => { importedPackageCandidate(raw).attempt_id = "attempt-1"; }],
        ["foreign imported media path", (raw: Record<string, unknown>) => {
            const candidate = importedPackageCandidate(raw);
            candidate.video_url = "video/imported/other-project/take.mp4";
            importedPackageNode(raw).media_urls = [candidate.video_url];
        }],
        ["hidden candidate", (raw: Record<string, unknown>) => {
            const node = importedPackageNode(raw);
            const data = node.data as Record<string, unknown>;
            data.deleted_candidates = data.candidates;
            data.candidates = [];
        }],
        ["non-terminal candidate", (raw: Record<string, unknown>) => { importedPackageCandidate(raw).status = "pending"; }],
        ["image media kind", (raw: Record<string, unknown>) => { importedPackageCandidate(raw).media_kind = "image"; }],
        ["audio media kind", (raw: Record<string, unknown>) => { importedPackageCandidate(raw).media_kind = "audio"; }],
    ])("rejects imported package candidate select ACK with %s", async (_label, mutate) => {
        const imported = selectableImportedPackageProject();
        mutate(imported);
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ project: imported, node_ids: ["fixture-node-3"] })));

        await expect(atelierGenerationApi.selectCandidate("fixture-project", "fixture-node-3", "fixture-candidate", { expected_revision: 0 }))
            .rejects.toMatchObject({ code: "atelier_generation_ack_mismatch" });
    });

    it("continues, cancels, reads, and selects through bound generation routes", async () => {
        const continuedAttempt = generationAttempt({ attempt_revision: 1 });
        const cancelledAttempt = generationAttempt({ status: "cancelled", provider_execution: providerRecord({ phase: "cancelled" }) });
        const cancelledCandidate = generationCandidate({ status: "cancelled" });
        const completedCandidate = generationCandidate({ status: "completed", video_url: "video/atelier-candidate-1-attempt-1.mp4" });
        const completedAttempt = generationAttempt({
            status: "completed",
            output_ref: "video/atelier-candidate-1-attempt-1.mp4",
            provider_execution: providerRecord({
                phase: "completed",
                handle: {
                    provider_model_id: "wan2.7-i2v",
                    task_id: "remote-1",
                    scope: providerRecord().scope,
                    credential_ref: "credential-ref",
                    request_id: "submit-1",
                    query_capability: "by_task_id",
                    submitted_at: 12,
                },
                artifact: {
                    schema_version: 1,
                    attempt_id: "attempt-1",
                    handle: {
                        provider_model_id: "wan2.7-i2v",
                        task_id: "remote-1",
                        scope: providerRecord().scope,
                        credential_ref: "credential-ref",
                        request_id: "submit-1",
                        query_capability: "by_task_id",
                        submitted_at: 12,
                    },
                    relative_path: "video/atelier-candidate-1-attempt-1.mp4",
                    sha256: "c".repeat(64),
                    size_bytes: 12,
                },
            }),
        });
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ project: project(), attempt: generationAttempt(), candidate: generationCandidate() }))
            .mockResolvedValueOnce(response(admissionAck({ replayed: true, new_candidate_ids: [] })))
            .mockResolvedValueOnce(response({ project: project({ generation_attempts: [continuedAttempt] }), attempt: continuedAttempt, candidate: generationCandidate() }))
            .mockResolvedValueOnce(response({
                project: project({ nodes: [node({ data: { candidates: [cancelledCandidate] } })], generation_attempts: [cancelledAttempt] }),
                attempt: cancelledAttempt,
                candidate: cancelledCandidate,
            }))
            .mockResolvedValueOnce(response({
                project: project({
                    nodes: [node({ data: { candidates: [completedCandidate], selected_candidate_id: "candidate-1" }, media_urls: ["video/atelier-candidate-1-attempt-1.mp4"] })],
                    generation_attempts: [completedAttempt],
                }),
                node_ids: ["video"],
            }));
        vi.stubGlobal("fetch", fetch);

        await atelierGenerationApi.getAttempt("project-1", "attempt-1");
        await atelierGenerationApi.getRequest("project-1", "request-1");
        await atelierGenerationApi.continueAttempt("project-1", "attempt-1", {
            candidate_id: "candidate-1",
            expected_attempt_revision: 0,
            execution_fingerprint: HASH,
        });
        await atelierGenerationApi.cancelAttempt("project-1", "attempt-1", {
            candidate_id: "candidate-1",
            expected_attempt_revision: 0,
            execution_fingerprint: HASH,
        });
        const selected = await atelierGenerationApi.selectCandidate("project-1", "video", "candidate-1", { expected_revision: 1 });

        expect(selected.node_ids).toEqual(["video"]);
        expect(fetch.mock.calls[0][1]).toEqual(expect.objectContaining({ method: "GET" }));
        expect(fetch.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "GET" }));
        expect(JSON.parse(fetch.mock.calls[2][1].body as string)).toEqual({
            candidate_id: "candidate-1",
            expected_attempt_revision: 0,
            execution_fingerprint: HASH,
        });
        expect(JSON.parse(fetch.mock.calls[3][1].body as string)).toEqual({
            candidate_id: "candidate-1",
            expected_attempt_revision: 0,
            execution_fingerprint: HASH,
        });
        expect(JSON.parse(fetch.mock.calls[4][1].body as string)).toEqual({ expected_revision: 1 });
        expect(fetch.mock.calls.map(call => call[0])).toEqual([
            `${API_URL}/atelier/projects/project-1/generation/attempts/attempt-1`,
            `${API_URL}/atelier/projects/project-1/generation/requests/request-1`,
            `${API_URL}/atelier/projects/project-1/generation/attempts/attempt-1/continue`,
            `${API_URL}/atelier/projects/project-1/generation/attempts/attempt-1/cancel`,
            `${API_URL}/atelier/projects/project-1/nodes/video/video_candidates/candidate-1/select`,
        ]);
        fetch.mockResolvedValueOnce(response({
            project: project({
                nodes: [node({ data: { candidates: [completedCandidate], selected_candidate_id: "candidate-1" },
                    media_urls: ["video/stale.mp4", "video/atelier-candidate-1-attempt-1.mp4"] })],
                generation_attempts: [completedAttempt],
            }),
            node_ids: ["video"],
        }));
        await expect(atelierGenerationApi.selectCandidate("project-1", "video", "candidate-1", { expected_revision: 1 }))
            .rejects.toThrow();
        fetch.mockResolvedValueOnce(response({
            project: project({
                nodes: [node({ data: { candidates: [], deleted_candidates: [completedCandidate], selected_candidate_id: "candidate-1" },
                    media_urls: [completedCandidate.video_url] })],
                generation_attempts: [completedAttempt],
            }),
            node_ids: ["video"],
        }));
        await expect(atelierGenerationApi.selectCandidate("project-1", "video", "candidate-1", { expected_revision: 1 }))
            .rejects.toMatchObject({ code: "atelier_generation_ack_mismatch" });
    });
});
