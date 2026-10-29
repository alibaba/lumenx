// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAtelierStore } from "@/store/atelierStore";
import { atelierProjectApi, parseAtelierProject } from "@/lib/atelierApi";
import { beginAtelierGenerationRequest, listProjectAtelierGenerationRequests } from "@/lib/atelierGenerationRequest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierGenerationAdmissionAck, AtelierGenerationRequest, AtelierGenerationSingletonAdmissionAck, AtelierOrderedVideoCandidateRequest, AtelierProject, AtelierVideoCandidateRequest, JsonValue } from "@/lib/atelierTypes";
import fixture from "./fixtures/atelier-project.json";
import orderedBatchReplayedFixture from "./fixtures/atelier-ordered-generation-batch-replayed.json";
import orderedRequestsFixture from "./fixtures/atelier-ordered-generation-requests.json";

const api = vi.hoisted(() => ({ createVideoCandidate: vi.fn(), getRequest: vi.fn(), getAttempt: vi.fn(),
    continueAttempt: vi.fn(), cancelAttempt: vi.fn(), selectCandidate: vi.fn(), setCandidateVisibility: vi.fn(), setGenerationDraft: vi.fn() }));
vi.mock("@/lib/atelierGenerationApi", async importOriginal => ({ ...(await importOriginal<typeof import("@/lib/atelierGenerationApi")>()), atelierGenerationApi: api }));
const state = () => useAtelierStore.getState();
const input = { model_id: "wan/wan2.7-video#i2v", mode: "i2v" as const, prompt: "sunrise",
    first_frame: { node_id: "image", media_url: "uploads/image.png" } };
function project(id = "p", revision = 0): AtelierProject {
    return parseAtelierProject({ ...structuredClone(fixture), id, revision, nodes: [], agent_turns: [],
        sequence: [], audio_tracks: [], exports: [], node_delete_receipts: [], node_upload_receipts: [],
        sequence_export_jobs: [], generation_attempts: [], generation_request_receipts: [] });
}
function legacyRequest(request: AtelierGenerationRequest): AtelierVideoCandidateRequest {
    if ("contract_version" in request || "entrypoint" in request || !("first_frame" in request)) throw new Error("expected legacy request");
    return request;
}

function intentForRequest(rawRequest: AtelierGenerationRequest): Record<string, JsonValue> {
    const request = legacyRequest(rawRequest);
    return {
        rules_version: "generation-parameters-v1",
        scope: "parameters-only",
        model_id: request.model_id,
        catalog_mode: request.mode,
        mode: request.mode,
        prompt: request.prompt,
        ...(request.params === undefined ? {} : { params: structuredClone(request.params) }),
        sources: {},
        request_fields: [],
        preset_id: null,
        preset_version: null,
    };
}

function ack(rawRequest: AtelierGenerationRequest): AtelierGenerationSingletonAdmissionAck {
    const request = legacyRequest(rawRequest);
    const p = project("p", request.expected_revision + 1);
    const candidate = { execution_contract: "atelier-provider-video-v1", id: "candidate", attempt_id: "attempt",
        media_kind: "video", status: "pending", video_url: null, error_code: null, created_at: 1, updated_at: 1 };
    const attempt = { schema_version: 1, project_id: "p", node_id: "video", candidate_id: "candidate", attempt_id: "attempt",
        attempt_revision: 0, status: "pending", error_code: null, output_ref: null, created_at: 1, updated_at: 1,
        first_frame_source: request.first_frame,
        provider_execution: { schema_version: 1, attempt_id: "attempt", fingerprint: "a".repeat(64), phase: "accepted",
            provider_model_id: "wan2.7-i2v", intent: intentForRequest(request), media: [], scope: {}, credential_ref: "ref" } };
    const receipt = { schema_version: 1, project_id: "p", node_id: "video", candidate_id: "candidate", attempt_id: "attempt",
        client_request_id: request.client_request_id, base_revision: request.expected_revision, committed_revision: p.revision,
        execution_fingerprint: "a".repeat(64), entrypoint: "atelier.provider_video.v1", request_contract_version: "request-json-v1",
        request_fingerprint: "b".repeat(64), created_at: 1 };
    p.nodes = [{ ...structuredClone(fixture.nodes[0]), id: "video", project_id: "p", type: "video", data: { candidates: [candidate] }, media_urls: [] }];
    return { project: { ...p, generation_attempts: [attempt], generation_request_receipts: [receipt] },
        members: [{ attempt, candidate }], attempt, receipt, candidate, replayed: false, new_candidate_ids: ["candidate"], reason: null } as unknown as AtelierGenerationSingletonAdmissionAck;
}

function batchAck(request: AtelierGenerationRequest): AtelierGenerationAdmissionAck {
    const first = ack(request);
    const secondAttempt = structuredClone(first.attempt);
    secondAttempt.attempt_id = "attempt-2";
    secondAttempt.candidate_id = "candidate-2";
    secondAttempt.provider_execution = { ...secondAttempt.provider_execution, attempt_id: "attempt-2", fingerprint: "c".repeat(64) };
    const secondCandidate = { ...first.candidate!, id: "candidate-2", attempt_id: "attempt-2" };
    const receipt = { schema_version: 2, project_id: "p", node_id: "video",
        client_request_id: request.client_request_id, base_revision: request.expected_revision, committed_revision: first.project.revision,
        entrypoint: "atelier.provider_video.v1", request_contract_version: "request-json-v1", request_fingerprint: "b".repeat(64), created_at: 1,
        members: [
            { candidate_id: "candidate", attempt_id: "attempt", execution_fingerprint: "a".repeat(64) },
            { candidate_id: "candidate-2", attempt_id: "attempt-2", execution_fingerprint: "c".repeat(64) },
        ] };
    first.project.generation_attempts = [first.attempt, secondAttempt];
    first.project.generation_request_receipts = [receipt as never];
    first.project.nodes[0].data = { candidates: [first.candidate! as unknown as JsonValue, secondCandidate as unknown as JsonValue] };
    return { project: first.project, receipt: receipt as never, members: [{ attempt: first.attempt, candidate: first.candidate }, { attempt: secondAttempt, candidate: secondCandidate }],
        replayed: false, new_candidate_ids: ["candidate", "candidate-2"], reason: null } as unknown as AtelierGenerationAdmissionAck;
}
function seed(p = project()) { useAtelierStore.setState({ projects: [p], currentProject: p }); }

function videoProject(revision = 0): AtelierProject {
    const p = project("p", revision);
    p.nodes = [{ ...structuredClone(fixture.nodes[0]), id: "video", project_id: "p", type: "video", prompt: "", data: {}, media_urls: [] }];
    return p;
}
function draftAck(request: { expected_revision: number; prompt: string; model_id: "wan/wan2.7-video#i2v"; mode: "i2v"; params: { [key: string]: JsonValue } }) {
    const p = videoProject(request.expected_revision + 1);
    p.nodes[0].prompt = request.prompt;
    p.nodes[0].data = { generation_draft: { schema_version: 1, model_id: request.model_id, mode: request.mode, params: structuredClone(request.params) } };
    return { project: p, node_ids: ["video"] };
}
function orderedDraftAck(request: { expected_revision: number; prompt: string; model_id: "wan/wan3.0-video#video" | "wan/wan3.0-video-prime#video"; mode: "t2v" | "i2v" | "r2v"; params: { [key: string]: JsonValue }; media: JsonValue[]; batch_size?: number }) {
    const p = videoProject(request.expected_revision + 1);
    p.nodes[0].prompt = request.prompt;
    p.nodes[0].data = { generation_draft: {
        schema_version: 2, input_contract_version: "ordered-media-v1", model_id: request.model_id, mode: request.mode,
        params: structuredClone(request.params), media: structuredClone(request.media), batch_size: request.batch_size ?? 1,
    } };
    return { project: p, node_ids: ["video"] };
}
function pending() { return listProjectAtelierGenerationRequests({ environment: requireActiveRuntimeEnvironment(), projectId: "p" }); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }

beforeEach(() => {
    vi.restoreAllMocks(); vi.clearAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" }); seed();
});

describe("Atelier durable generation store", () => {
    it("persists an exact original request before POST and clears only its accepted receipt", async () => {
        api.createVideoCandidate.mockImplementation(async (_p, _n, request) => {
            expect(pending()).toHaveLength(1);
            expect(pending()[0].request).toEqual(request);
            expect(request).not.toHaveProperty("params");
            return ack(request);
        });
        await state().createVideoCandidate("video", input);
        expect(pending()).toEqual([]);
        expect(state().currentProject?.revision).toBe(1);
    });
    it("blocks submission when the original request cannot be persisted", async () => {
        vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("quota"); });
        await expect(state().createVideoCandidate("video", input)).rejects.toThrow();
        expect(api.createVideoCandidate).not.toHaveBeenCalled();
    });
    it("deduplicates a double click while admission is unresolved", async () => {
        const result = deferred<AtelierGenerationAdmissionAck>();
        api.createVideoCandidate.mockReturnValue(result.promise);
        const first = state().createVideoCandidate("video", input);
        const second = state().createVideoCandidate("video", input);
        await Promise.resolve();
        expect(api.createVideoCandidate).toHaveBeenCalledTimes(1);
        result.resolve(ack(api.createVideoCandidate.mock.calls[0][2]));
        await Promise.all([first, second]);
    });
    it("does not silently replace a different intent with the in-flight request", async () => {
        const result = deferred<AtelierGenerationAdmissionAck>(); api.createVideoCandidate.mockReturnValue(result.promise);
        const first = state().createVideoCandidate("video", input);
        await expect(state().createVideoCandidate("video", { ...input, prompt: "different scene" })).rejects.toThrow();
        result.resolve(ack(api.createVideoCandidate.mock.calls[0][2])); await first;
        expect(api.createVideoCandidate).toHaveBeenCalledTimes(1);
    });
    it("uses the committed revision after a queued edit and snapshots the original input", async () => {
        const updated = deferred<AtelierProject>(); vi.spyOn(atelierProjectApi, "updateProject").mockReturnValue(updated.promise);
        const edit = state().updateProject({ title: "new title" });
        const mutable = structuredClone(input);
        api.createVideoCandidate.mockImplementation(async (_p, _n, request) => ack(request));
        const generation = state().createVideoCandidate("video", mutable);
        mutable.prompt = "late mutation";
        updated.resolve(project("p", 4)); await edit; await generation;
        expect(api.createVideoCandidate.mock.calls[0][2]).toMatchObject({ expected_revision: 4, prompt: "sunrise" });
    });

    it("rejects self-consistent admission ACKs whose provider intent drifts from the saved request", async () => {
        api.createVideoCandidate.mockImplementation(async (_p, _n, request) => {
            const bad = ack(request);
            bad.attempt.provider_execution.intent = { ...bad.attempt.provider_execution.intent, prompt: "drifted prompt" };
            bad.project.generation_attempts[0].provider_execution.intent = structuredClone(bad.attempt.provider_execution.intent);
            return bad;
        });

        await expect(state().createVideoCandidate("video", input)).rejects.toThrow("Atelier generation acknowledgement does not match the request");
        expect(pending()).toHaveLength(1);
    });

    it("keeps one pending journal entry for an entire batch request", async () => {
        const batchInput = { ...input, batch_size: 2 };
        api.createVideoCandidate.mockImplementation(async (_p, _n, request) => {
            expect(pending()).toHaveLength(1);
            expect(pending()[0].request).toMatchObject({ client_request_id: request.client_request_id, batch_size: 2 });
            return batchAck(request);
        });

        const result = await state().createVideoCandidate("video", batchInput);

        expect(result.members).toHaveLength(2);
        expect(api.createVideoCandidate).toHaveBeenCalledTimes(1);
        expect(api.createVideoCandidate.mock.calls[0][2]).toMatchObject({ batch_size: 2 });
        expect(pending()).toEqual([]);
    });

    it("reads back an uncertain batch request with the original payload and no replacement POST", async () => {
        api.createVideoCandidate.mockRejectedValue(Object.assign(new Error("unknown"), { status: 503 }));
        await expect(state().createVideoCandidate("video", { ...input, batch_size: 2 })).rejects.toThrow("unknown");
        const original = pending()[0];
        api.getRequest.mockResolvedValue({ ...batchAck(original.request), replayed: true, new_candidate_ids: [] });

        await state().readbackPendingGeneration(original.clientRequestId);

        expect(api.getRequest).toHaveBeenCalledWith("p", original.clientRequestId, original.request);
        expect(api.createVideoCandidate).toHaveBeenCalledTimes(1);
        expect(pending()).toEqual([]);
    });


    it("reads back a lost ordered batch response with the full saved request and no replacement POST", async () => {
        const requests = orderedRequestsFixture as { batch: AtelierOrderedVideoCandidateRequest };
        seed(project("p", requests.batch.expected_revision));
        const stored = beginAtelierGenerationRequest({
            environment: requireActiveRuntimeEnvironment(),
            projectId: "p",
            nodeId: "video",
            expectedRevision: requests.batch.expected_revision,
            contractVersion: requests.batch.contract_version,
            modelId: requests.batch.model_id,
            mode: requests.batch.mode,
            prompt: requests.batch.prompt,
            media: requests.batch.media,
            params: requests.batch.params,
            batchSize: requests.batch.batch_size,
            storage: localStorage,
            idFactory: () => requests.batch.client_request_id,
        }).request;
        expect(pending()).toHaveLength(1);
        expect(stored.request).toEqual(requests.batch);
        api.getRequest.mockResolvedValue(orderedBatchReplayedFixture);

        const ack = await state().readbackPendingGeneration(requests.batch.client_request_id);

        expect(ack?.receipt.entrypoint).toBe("atelier.provider_video.v2");
        expect(api.getRequest).toHaveBeenCalledWith("p", requests.batch.client_request_id, requests.batch);
        expect(api.createVideoCandidate).not.toHaveBeenCalled();
        expect(pending()).toEqual([]);
        for (const member of ack?.members ?? []) {
            const attempt = state().currentProject?.generation_attempts.find(item => item.attempt_id === member.attempt.attempt_id);
            expect(attempt).toBeTruthy();
            expect(attempt).not.toHaveProperty("first_frame_source");
            expect(attempt).toHaveProperty("input_contract_version", "ordered-media-v1");
        }
    });

    it("uses the write queue and snapshots generation draft input", async () => {
        seed(videoProject());
        const updated = deferred<AtelierProject>();
        vi.spyOn(atelierProjectApi, "updateProject").mockReturnValue(updated.promise);
        const edit = state().updateProject({ title: "new title" });
        const mutable = { prompt: "draft prompt", model_id: "wan/wan2.7-video#i2v" as const, mode: "i2v" as const, params: { seed: -1, prompt_extend: false, watermark: false } };
        api.setGenerationDraft.mockImplementation(async (_p, _n, request) => draftAck(request));
        const saving = state().saveGenerationDraft("video", mutable);
        mutable.prompt = "late mutation";
        mutable.params.seed = 99;
        updated.resolve(videoProject(4));
        await edit;
        await saving;

        expect(api.setGenerationDraft).toHaveBeenCalledWith("p", "video", {
            expected_revision: 4,
            prompt: "draft prompt",
            model_id: "wan/wan2.7-video#i2v",
            mode: "i2v",
            params: { seed: -1, prompt_extend: false, watermark: false },
        });
        expect(state().currentProject?.nodes[0].data.generation_draft).toEqual({
            schema_version: 1, model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: { seed: -1, prompt_extend: false, watermark: false },
        });
    });

    it("saves ordered generation drafts through the same node draft slot", async () => {
        seed(videoProject());
        const mutable = {
            contract_version: "ordered-media-v1" as const,
            prompt: "",
            model_id: "wan/wan3.0-video-prime#video" as const,
            mode: "r2v" as const,
            params: { duration: -1, seed: 0, prompt_extend: false, watermark: false, audio: false },
            media: [
                { role: "reference_image" as const, ordinal: 0, node_id: "image", media_url: "uploads/image.png" },
                { role: "reference_audio" as const, ordinal: 0, node_id: "audio", media_url: "uploads/audio.wav" },
            ],
            batch_size: 6,
        };
        api.setGenerationDraft.mockImplementation(async (_p, _n, request) => orderedDraftAck(request as never));

        await state().saveGenerationDraft("video", mutable as never);

        expect(api.setGenerationDraft).toHaveBeenCalledWith("p", "video", {
            expected_revision: 0,
            contract_version: "ordered-media-v1",
            prompt: "",
            model_id: "wan/wan3.0-video-prime#video",
            mode: "r2v",
            params: { duration: -1, seed: 0, prompt_extend: false, watermark: false, audio: false },
            media: [
                { role: "reference_image", ordinal: 0, node_id: "image", media_url: "uploads/image.png" },
                { role: "reference_audio", ordinal: 0, node_id: "audio", media_url: "uploads/audio.wav" },
            ],
            batch_size: 6,
        });
        expect(state().currentProject?.nodes[0].data.generation_draft).toEqual({
            schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video-prime#video", mode: "r2v",
            params: { duration: -1, seed: 0, prompt_extend: false, watermark: false, audio: false },
            media: [
                { role: "reference_image", ordinal: 0, node_id: "image", media_url: "uploads/image.png" },
                { role: "reference_audio", ordinal: 0, node_id: "audio", media_url: "uploads/audio.wav" },
            ],
            batch_size: 6,
        });
    });

    it("rejects late generation draft acknowledgements from an earlier environment", async () => {
        seed(videoProject(3));
        const result = deferred<{ project: AtelierProject; node_ids: string[] }>();
        api.setGenerationDraft.mockReturnValue(result.promise);
        const saving = state().saveGenerationDraft("video", { prompt: "draft", model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: {} });
        await Promise.resolve();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        seed(project("other"));
        result.resolve(draftAck({ expected_revision: 3, prompt: "draft", model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: {} }));

        await expect(saving).rejects.toThrow();
        expect(api.setGenerationDraft).toHaveBeenCalledTimes(1);
        expect(state().currentProject?.id).toBe("other");
    });
    it("keeps the newly selected project while an earlier project admission finishes", async () => {
        const result = deferred<AtelierGenerationAdmissionAck>(); api.createVideoCandidate.mockReturnValue(result.promise);
        const first = state().createVideoCandidate("video", input); await Promise.resolve();
        const other = project("other"); useAtelierStore.setState(s => ({ projects: [...s.projects, other], currentProject: other }));
        result.resolve(ack(api.createVideoCandidate.mock.calls[0][2])); await first;
        expect(state().currentProject?.id).toBe("other");
        expect(state().projects.find(item => item.id === "p")?.generation_attempts).toHaveLength(1);
    });
    it("retains a 503 request and restores by GET without another POST", async () => {
        api.createVideoCandidate.mockRejectedValue(Object.assign(new Error("unknown"), { status: 503 }));
        await expect(state().createVideoCandidate("video", input)).rejects.toThrow("unknown");
        const original = pending()[0];
        api.getRequest.mockResolvedValue({ ...ack(original.request), replayed: true, new_candidate_ids: [] });
        await state().readbackPendingGeneration(original.clientRequestId);
        expect(api.getRequest).toHaveBeenCalledWith("p", original.clientRequestId, original.request);
        expect(api.createVideoCandidate).toHaveBeenCalledTimes(1);
        expect(pending()).toEqual([]);
        expect(state().needsRefresh).toBe(false);
    });
    it("keeps the original request after a readback 404 or a mismatched owner", async () => {
        api.createVideoCandidate.mockRejectedValue(new Error("unknown"));
        await expect(state().createVideoCandidate("video", input)).rejects.toThrow();
        const original = pending()[0];
        api.getRequest.mockRejectedValue(Object.assign(new Error("missing"), { status: 404 }));
        await expect(state().readbackPendingGeneration()).rejects.toThrow("missing");
        expect(pending()).toHaveLength(1);
        const bad = ack(original.request); bad.receipt.node_id = "another";
        api.getRequest.mockResolvedValue(bad);
        await expect(state().readbackPendingGeneration()).rejects.toThrow();
        expect(pending()).toHaveLength(1);
    });
    it("drops a response from an earlier environment generation", async () => {
        const result = deferred<AtelierGenerationAdmissionAck>(); api.createVideoCandidate.mockReturnValue(result.promise);
        const first = state().createVideoCandidate("video", input); await Promise.resolve();
        const request = api.createVideoCandidate.mock.calls[0][2];
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" }); seed(project("other"));
        result.resolve(ack(request)); await expect(first).rejects.toThrow();
        expect(state().currentProject?.id).toBe("other");
    });

    it("uses the current revision to hide or restore a candidate without durable retry", async () => {
        const p = project("p", 7);
        p.nodes = [{ ...structuredClone(fixture.nodes[0]), id: "video", project_id: "p", type: "video", data: { candidates: [{ id: "candidate", status: "completed", video_url: "video/out.mp4" }] }, media_urls: ["video/out.mp4"] }];
        seed(p);
        const hidden = structuredClone(p);
        hidden.revision = 8;
        hidden.nodes[0].data = { candidates: [], deleted_candidates: [{ id: "candidate", status: "completed", video_url: "video/out.mp4" }], selected_candidate_id: null };
        hidden.nodes[0].media_urls = [];
        api.setCandidateVisibility.mockResolvedValue({ project: hidden, node_ids: ["video"] });

        await state().setGenerationCandidateVisibility("video", "candidate", false);

        expect(api.setCandidateVisibility).toHaveBeenCalledWith("p", "video", "candidate", { expected_revision: 7, visible: false });
        expect(api.createVideoCandidate).not.toHaveBeenCalled();
        expect(state().currentProject?.revision).toBe(8);
        expect(state().currentProject?.nodes[0].data.deleted_candidates).toHaveLength(1);
    });

    it("rejects late visibility acknowledgements from an earlier environment without retrying", async () => {
        const p = project("p", 3);
        p.nodes = [{ ...structuredClone(fixture.nodes[0]), id: "video", project_id: "p", type: "video", data: { candidates: [{ id: "candidate", status: "completed", video_url: "video/out.mp4" }] }, media_urls: [] }];
        seed(p);
        const result = deferred<{ project: AtelierProject; node_ids: string[] }>();
        api.setCandidateVisibility.mockReturnValue(result.promise);
        const first = state().setGenerationCandidateVisibility("video", "candidate", false);
        await Promise.resolve();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        seed(project("other"));
        result.resolve({ project: { ...p, revision: 4 }, node_ids: ["video"] });

        await expect(first).rejects.toThrow();
        expect(api.setCandidateVisibility).toHaveBeenCalledTimes(1);
        expect(api.getRequest).not.toHaveBeenCalled();
        expect(state().currentProject?.id).toBe("other");
    });

    it("keeps uncertain visibility failures as ordinary refresh-required edits without replaying a mutation", async () => {
        const p = project("p", 2);
        p.nodes = [{ ...structuredClone(fixture.nodes[0]), id: "video", project_id: "p", type: "video", data: { candidates: [{ id: "candidate", status: "completed", video_url: "video/out.mp4" }] }, media_urls: [] }];
        seed(p);
        api.setCandidateVisibility.mockRejectedValue(Object.assign(new Error("unknown"), { status: 503 }));

        await expect(state().setGenerationCandidateVisibility("video", "candidate", false)).rejects.toThrow("unknown");

        expect(api.setCandidateVisibility).toHaveBeenCalledTimes(1);
        expect(api.getRequest).not.toHaveBeenCalled();
        expect(state().needsRefresh).toBe(true);
    });

    it("uses original attempt identity for continue and leaves editing usable on progress errors", async () => {
        const admitted = ack({ ...input, expected_revision: 0, client_request_id: "request" }); seed(admitted.project);
        api.continueAttempt.mockRejectedValue(new Error("provider temporarily unavailable"));
        await expect(state().continueGenerationAttempt("attempt")).rejects.toThrow();
        expect(api.continueAttempt).toHaveBeenCalledWith("p", "attempt", {
            candidate_id: "candidate", expected_attempt_revision: 0, execution_fingerprint: "a".repeat(64),
        });
        expect(state().needsRefresh).toBe(false);
        expect(state().currentProject?.generation_attempts[0].status).toBe("pending");
    });
    it("merges attempt progress without selecting or overwriting current canvas edits", async () => {
        const admitted = ack({ ...input, expected_revision: 0, client_request_id: "request" });
        const edited = structuredClone(admitted.project); edited.revision = 3; edited.nodes[0].prompt = "unsent new idea"; seed(edited);
        const finished = structuredClone(admitted); finished.attempt.attempt_revision = 3; finished.attempt.status = "completed";
        finished.attempt.output_ref = "video/out.mp4"; finished.attempt.updated_at = 4;
        finished.project.generation_attempts = [finished.attempt];
        api.getAttempt.mockResolvedValue(finished);
        await state().refreshGenerationAttempt("attempt");
        expect(state().currentProject?.revision).toBe(3);
        expect(state().currentProject?.nodes[0].prompt).toBe("unsent new idea");
        expect(state().currentProject?.generation_attempts[0].status).toBe("completed");
        expect(state().currentProject?.nodes[0].media_urls).toEqual([]);
    });
});
