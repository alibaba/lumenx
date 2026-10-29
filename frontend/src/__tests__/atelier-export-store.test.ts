// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAtelierStore } from "@/store/atelierStore";
import { parseAtelierProject } from "@/lib/atelierApi";
import { readAtelierExportRequest } from "@/lib/atelierExportRequest";
import { readAtelierExportDeletionRequest } from "@/lib/atelierExportDeletionRequest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project.json";
import type { AtelierExportRecord, AtelierProject, AtelierSequenceEntry } from "@/lib/atelierTypes";

const sequenceApi = vi.hoisted(() => ({
    saveSequence: vi.fn(),
    preflight: vi.fn(),
    createExport: vi.fn(),
    redoExport: vi.fn(),
    getExport: vi.fn(),
    getExportByRequest: vi.fn(),
    cancelExport: vi.fn(),
    repairExport: vi.fn(),
    resumeExport: vi.fn(),
    deleteExport: vi.fn(),
}));

vi.mock("@/lib/atelierSequenceApi", () => ({ atelierSequenceApi: sequenceApi }));

const A = "11111111-1111-4111-8111-111111111111";
const state = () => useAtelierStore.getState();
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(r => { resolve = r; });
    return { promise, resolve };
}

function entry(id = "clip-1"): AtelierSequenceEntry {
    return { id, parentId: "video-1", candidateId: null, trimStart: null, trimEnd: null, gain: 1, transition: { kind: "cut", duration: 0 } };
}

function baseProject(id = "p1", revision = 0, patch: Partial<AtelierProject> = {}): AtelierProject {
    return parseAtelierProject({
        ...structuredClone(fixture),
        id,
        revision,
        nodes: [],
        agent_turns: [],
        sequence: [],
        audio_tracks: [],
        exports: [],
        node_delete_receipts: [],
        node_upload_receipts: [],
        source_project_id: null,
        ...patch,
    });
}

type TestJob = {
    schema_version: 1;
    job_id: string;
    project_id: string;
    status: "queued" | "running" | "cancelled" | "failed" | "repair_required" | "completed";
    request_contract_version: "atelier-sequence-export-v1" | "atelier-sequence-export-redo-v1";
    client_request_id: string;
    source_export_id: string | null;
    request_fingerprint: string;
    base_revision: number;
    job_revision: number;
    created_at: number;
    updated_at: number;
    started_at: number | null;
    finished_at: number | null;
    snapshot_digest: string;
    snapshot: Record<string, unknown>;
    source_entries: AtelierSequenceEntry[];
    source_audio_tracks: unknown[];
    output_ref: string;
    prepared_proof: null;
    export_id: string | null;
    error_code: string | null;
    error_message: string | null;
    cancelled_at: number | null;
};

function job(projectId = "p1", requestId = "export-req", overrides: Partial<TestJob> = {}): TestJob {
    return {
        schema_version: 1,
        job_id: "job-1",
        project_id: projectId,
        status: "queued",
        request_contract_version: "atelier-sequence-export-v1",
        client_request_id: requestId,
        source_export_id: null,
        request_fingerprint: "a".repeat(64),
        base_revision: 2,
        job_revision: 0,
        created_at: 10,
        updated_at: 10,
        started_at: null,
        finished_at: null,
        snapshot_digest: "b".repeat(64),
        snapshot: { clips: 1 },
        source_entries: [entry()],
        source_audio_tracks: [],
        output_ref: "video/atelier_seq_job-1.mp4",
        prepared_proof: null,
        export_id: null,
        error_code: null,
        error_message: null,
        cancelled_at: null,
        ...overrides,
    };
}

function exportRecord(projectId = "p1", id = "export-1"): AtelierExportRecord {
    return {
        id,
        project_id: projectId,
        created_at: 20,
        clip_count: 1,
        size_mb: 1.25,
        video_url: "video/atelier_seq_job-1.mp4",
        filename: "atelier_seq_job-1.mp4",
        source_entries: [entry()],
        timeline_snapshot: { schema_version: "atelier.timeline.v1", clips: [] },
        publication_proof: null,
        job_id: null,
        snapshot_digest: null,
    };
}

function ack(project: AtelierProject, requestId: string, overrides: Partial<TestJob> = {}, record: AtelierExportRecord | null = null) {
    return { project, job: job(project.id, requestId, { base_revision: project.revision, ...overrides }), export_record: record, replayed: false, new_job_ids: [overrides.job_id ?? "job-1"] };
}

function deletedRecord(record: AtelierExportRecord, status: "pending" | "deleted" | "already_missing" | "blocked" | "retained", requestId = "delete-req-1"): AtelierExportRecord {
    return {
        ...record,
        deletion: {
            client_request_id: requestId,
            delete_file: status !== "retained",
            deleted_at: 30,
            file_status: status,
            checked_at: status === "pending" ? null : 31,
            error_code: status === "blocked" ? "export_file_still_referenced" : null,
            error_message: status === "blocked" ? "The file is still referenced." : null,
        },
    };
}

function deleteAck(project: AtelierProject, record: AtelierExportRecord, replayed = false) {
    return { project, export_record: record, replayed };
}

async function setup(project = baseProject("p1", 2, { sequence: [entry()] })) {
    const fetch = vi.fn().mockResolvedValueOnce(response([project]));
    vi.stubGlobal("fetch", fetch);
    await state().loadProjects();
    return fetch;
}

beforeEach(() => {
    vi.restoreAllMocks();
    Object.values(sequenceApi).forEach(mock => mock.mockReset());
    localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
});

describe("Atelier export store integration", () => {
    it("saves sequence through the project write queue and adopts the returned project", async () => {
        await setup(baseProject("p1", 2));
        const saved = baseProject("p1", 3, { sequence: [entry("saved")] });
        sequenceApi.saveSequence.mockResolvedValueOnce(saved);

        const result = await state().saveSequence([entry("saved")], 2);

        expect(sequenceApi.saveSequence).toHaveBeenCalledWith("p1", { expectedRevision: 2, entries: [entry("saved")] });
        expect(result.revision).toBe(3);
        expect(state().currentProject?.sequence[0].id).toBe("saved");
    });

    it("journals export before POST, sends committed-sequence request, clears matching ACK, and stores job state", async () => {
        await setup(baseProject("p1", 2, { sequence: [entry()] }));
        sequenceApi.createExport.mockImplementationOnce(async (_projectId: string, request: { client_request_id: string }) => {
            expect(readAtelierExportRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1", clientRequestId: request.client_request_id })).not.toBeNull();
            expect(request).not.toHaveProperty("entries");
            expect(request).not.toHaveProperty("audio_tracks");
            return ack(baseProject("p1", 2, { sequence: [entry()] }), request.client_request_id);
        });

        const result = await state().createSequenceExport();

        expect(sequenceApi.createExport).toHaveBeenCalledTimes(1);
        expect(result.job.client_request_id).toMatch(/^export-/);
        expect(state().pendingExports).toEqual([]);
        expect(state().sequenceExportJobs.map(item => item.job_id)).toEqual(["job-1"]);
    });

    it("keeps pending export journals for unknown create failures and 404 readback", async () => {
        await setup(baseProject("p1", 2, { sequence: [entry()] }));
        sequenceApi.createExport.mockRejectedValueOnce(Object.assign(new Error("dispatch uncertain"), { status: 503 }));
        await expect(state().createSequenceExport()).rejects.toThrow("dispatch uncertain");
        const pendingId = state().pendingExports[0].clientRequestId;
        expect(pendingId).toMatch(/^export-/);

        sequenceApi.getExportByRequest.mockRejectedValueOnce(Object.assign(new Error("missing"), { status: 404 }));
        await expect(state().readbackPendingExport(pendingId)).resolves.toBeNull();
        expect(sequenceApi.getExportByRequest.mock.calls[0][2]).toMatchObject({
            operation: "create",
            sourceExportId: null,
            request: { client_request_id: pendingId },
        });
        expect(state().pendingExports.map(item => item.clientRequestId)).toEqual([pendingId]);
        expect(sequenceApi.createExport).toHaveBeenCalledTimes(1);
    });

    it("blocks same-project synchronous export create while one request is already pending", async () => {
        const pending = deferred<ReturnType<typeof ack>>();
        await setup(baseProject("p1", 2, { sequence: [entry()] }));
        sequenceApi.createExport.mockReturnValueOnce(pending.promise);

        const first = state().createSequenceExport();
        await expect(state().createSequenceExport()).rejects.toThrow("already pending");
        expect(sequenceApi.createExport).toHaveBeenCalledTimes(1);
        const requestId = state().pendingExports[0].clientRequestId;

        pending.resolve(ack(baseProject("p1", 2, { sequence: [entry()] }), requestId));
        await first;
        expect(state().pendingExports).toEqual([]);
    });

    it("blocks new export creates while an unknown previous create is pending but allows one after success clears it", async () => {
        await setup(baseProject("p1", 2, { sequence: [entry()] }));
        sequenceApi.createExport.mockRejectedValueOnce(Object.assign(new Error("dispatch uncertain"), { status: 503 }));
        await expect(state().createSequenceExport()).rejects.toThrow("dispatch uncertain");
        const pendingId = state().pendingExports[0].clientRequestId;
        await expect(state().createSequenceExport()).rejects.toThrow("already pending");
        expect(state().pendingExports.map(item => item.clientRequestId)).toEqual([pendingId]);
        expect(sequenceApi.createExport).toHaveBeenCalledTimes(1);

        sequenceApi.getExportByRequest.mockResolvedValueOnce(ack(baseProject("p1", 2, { sequence: [entry()] }), pendingId, {
            status: "failed",
            job_revision: 1,
            error_message: "dispatch failed",
        }));
        await state().readbackPendingExport(pendingId);
        sequenceApi.createExport.mockImplementationOnce(async (_projectId: string, request: { client_request_id: string }) =>
            ack(baseProject("p1", 2, { sequence: [entry()] }), request.client_request_id, { job_id: "job-2", output_ref: "video/atelier_seq_job-2.mp4" }));
        await expect(state().createSequenceExport()).resolves.toMatchObject({ job: { job_id: "job-2" } });
        expect(sequenceApi.createExport).toHaveBeenCalledTimes(2);
    });

    it("does not poison the normal write gate when job-only refresh fails", async () => {
        await setup(baseProject("p1", 2));
        sequenceApi.getExport.mockRejectedValueOnce(new Error("poll failed"));
        await expect(state().refreshSequenceExport("job-1")).rejects.toThrow("poll failed");
        expect(state().needsRefresh).toBe(false);

        const saved = baseProject("p1", 3, { sequence: [entry("after-poll-error")] });
        sequenceApi.saveSequence.mockResolvedValueOnce(saved);
        await expect(state().saveSequence([entry("after-poll-error")])).resolves.toMatchObject({ revision: 3 });
    });

    it("rejects same job revision with different content and keeps the latest known job", async () => {
        await setup(baseProject("p1", 2));
        sequenceApi.getExport.mockResolvedValueOnce(ack(baseProject("p1", 2), "req-1", { job_revision: 1, status: "running" }));
        await state().refreshSequenceExport("job-1");
        sequenceApi.getExport.mockResolvedValueOnce(ack(baseProject("p1", 2), "req-1", { job_revision: 1, status: "failed", error_message: "changed" }));

        await expect(state().refreshSequenceExport("job-1")).rejects.toThrow("same revision");
        expect(state().sequenceExportJobs[0]).toMatchObject({ job_revision: 1, status: "running" });
    });

    it("rejects completed job ACKs that are not atomically bound to the returned project export", async () => {
        await setup(baseProject("p1", 2));
        const record = exportRecord("p1", "export-1");
        sequenceApi.getExport.mockResolvedValueOnce(ack(baseProject("p1", 2, { exports: [] }), "req-1", {
            status: "completed",
            job_revision: 1,
            export_id: record.id,
        }, record));

        await expect(state().refreshSequenceExport("job-1")).rejects.toThrow("atomically bound");
        expect(state().sequenceExportJobs).toEqual([]);
    });

    it("does not mutate the newly selected project when a late export ACK returns after navigation ABA", async () => {
        const pending = deferred<ReturnType<typeof ack>>();
        await setup(baseProject("p1", 2, { sequence: [entry("old-current")] }));
        state().projects.push(baseProject("p2", 0));
        sequenceApi.createExport.mockReturnValueOnce(pending.promise);
        const saving = state().createSequenceExport();
        const requestId = state().pendingExports[0].clientRequestId;

        vi.stubGlobal("fetch", vi.fn()
            .mockResolvedValueOnce(response(baseProject("p2", 0)))
            .mockResolvedValueOnce(response(baseProject("p1", 2, { sequence: [entry("new-current")] }))));
        await state().switchProject("p2");
        await state().switchProject("p1");

        pending.resolve(ack(baseProject("p1", 2, { sequence: [entry("late-ack")] }), requestId));
        await saving;
        expect(state().currentProject?.sequence[0].id).toBe("new-current");
    });

    it("journals redo by source export, does not save the current draft path, and clears only matching ACKs", async () => {
        const source = exportRecord("p1", "export-source-1");
        await setup(baseProject("p1", 6, { sequence: [entry("current-draft-source")], exports: [source] }));
        sequenceApi.redoExport.mockImplementationOnce(async (_projectId: string, exportId: string, request: { client_request_id: string }) => {
            expect(exportId).toBe(source.id);
            expect(readAtelierExportRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1", clientRequestId: request.client_request_id }))
                .toMatchObject({ operation: "redo", sourceExportId: source.id });
            return ack(baseProject("p1", 6, { sequence: [entry("server-current")], exports: [source] }), request.client_request_id, {
                request_contract_version: "atelier-sequence-export-redo-v1",
                source_export_id: source.id,
            });
        });

        await expect(state().redoSequenceExport(source)).resolves.toMatchObject({
            job: { request_contract_version: "atelier-sequence-export-redo-v1", source_export_id: source.id },
        });

        expect(sequenceApi.saveSequence).not.toHaveBeenCalled();
        expect(sequenceApi.createExport).not.toHaveBeenCalled();
        expect(sequenceApi.redoExport).toHaveBeenCalledTimes(1);
        expect(state().pendingExports).toEqual([]);
        expect(state().sequenceExportJobs[0]).toMatchObject({ source_export_id: source.id });
    });

    it("reads back a lost redo ACK without posting a second redo", async () => {
        const source = exportRecord("p1", "export-source-1");
        await setup(baseProject("p1", 6, { exports: [source] }));
        sequenceApi.redoExport.mockRejectedValueOnce(Object.assign(new Error("dispatch uncertain"), { status: 503 }));

        await expect(state().redoSequenceExport(source)).rejects.toThrow("dispatch uncertain");
        const pending = state().pendingExports[0];
        expect(pending).toMatchObject({ operation: "redo", sourceExportId: source.id });
        await expect(state().redoSequenceExport(source)).rejects.toThrow("already pending");

        sequenceApi.getExportByRequest.mockResolvedValueOnce(ack(baseProject("p1", 6, { exports: [source] }), pending.clientRequestId, {
            request_contract_version: "atelier-sequence-export-redo-v1",
            source_export_id: source.id,
        }));
        await state().readbackPendingExport(pending.clientRequestId);

        expect(sequenceApi.redoExport).toHaveBeenCalledTimes(1);
        expect(state().pendingExports).toEqual([]);
    });

    it("accepts redo readback for a durable job after the source history record disappears", async () => {
        const source = exportRecord("p1", "export-source-1");
        await setup(baseProject("p1", 6, { exports: [source] }));
        sequenceApi.redoExport.mockRejectedValueOnce(Object.assign(new Error("dispatch uncertain"), { status: 503 }));

        await expect(state().redoSequenceExport(source)).rejects.toThrow("dispatch uncertain");
        const pending = state().pendingExports[0];
        sequenceApi.getExportByRequest.mockResolvedValueOnce(ack(baseProject("p1", 6, { exports: [] }), pending.clientRequestId, {
            request_contract_version: "atelier-sequence-export-redo-v1",
            source_export_id: source.id,
        }));

        await expect(state().readbackPendingExport(pending.clientRequestId)).resolves.toMatchObject({
            job: { source_export_id: source.id },
        });
        expect(sequenceApi.redoExport).toHaveBeenCalledTimes(1);
        expect(state().pendingExports).toEqual([]);
    });

    it("rejects redo readback when the backend returns the same request id for a different source export", async () => {
        const actual = await vi.importActual<typeof import("@/lib/atelierSequenceApi")>("@/lib/atelierSequenceApi");
        const source = exportRecord("p1", "export-source-a");
        await setup(baseProject("p1", 6, { exports: [source] }));
        sequenceApi.redoExport.mockRejectedValueOnce(Object.assign(new Error("dispatch uncertain"), { status: 503 }));

        await expect(state().redoSequenceExport(source)).rejects.toThrow("dispatch uncertain");
        const pending = state().pendingExports[0];
        const mismatchedJob = job("p1", pending.clientRequestId, {
            base_revision: 6,
            request_contract_version: "atelier-sequence-export-redo-v1",
            source_export_id: "export-source-b",
        });
        const mismatchedAck = {
            project: baseProject("p1", 6, { exports: [], sequence_export_jobs: [mismatchedJob] as never }),
            job: mismatchedJob,
            export_record: null,
            replayed: false,
            new_job_ids: [],
        };
        sequenceApi.getExportByRequest.mockImplementationOnce(actual.atelierSequenceApi.getExportByRequest);
        vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(mismatchedAck)));

        await expect(state().readbackPendingExport(pending.clientRequestId)).rejects.toThrow();
        expect(sequenceApi.redoExport).toHaveBeenCalledTimes(1);
        expect(readAtelierExportRequest({
            environment: requireActiveRuntimeEnvironment(),
            projectId: "p1",
            clientRequestId: pending.clientRequestId,
        })).toMatchObject({ operation: "redo", sourceExportId: source.id });
    });

    it("rejects redo readback when the backend replays a create export contract for the same request id", async () => {
        const actual = await vi.importActual<typeof import("@/lib/atelierSequenceApi")>("@/lib/atelierSequenceApi");
        const source = exportRecord("p1", "export-source-a");
        await setup(baseProject("p1", 6, { exports: [source] }));
        sequenceApi.redoExport.mockRejectedValueOnce(Object.assign(new Error("dispatch uncertain"), { status: 503 }));

        await expect(state().redoSequenceExport(source)).rejects.toThrow("dispatch uncertain");
        const pending = state().pendingExports[0];
        const createReplayJob = job("p1", pending.clientRequestId, {
            base_revision: 6,
            request_contract_version: "atelier-sequence-export-v1",
            source_export_id: null,
        });
        const createReplay = {
            project: baseProject("p1", 6, { exports: [], sequence_export_jobs: [createReplayJob] as never }),
            job: createReplayJob,
            export_record: null,
            replayed: false,
            new_job_ids: [],
        };
        sequenceApi.getExportByRequest.mockImplementationOnce(actual.atelierSequenceApi.getExportByRequest);
        vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(createReplay)));

        await expect(state().readbackPendingExport(pending.clientRequestId)).rejects.toThrow();
        expect(sequenceApi.redoExport).toHaveBeenCalledTimes(1);
        expect(readAtelierExportRequest({
            environment: requireActiveRuntimeEnvironment(),
            projectId: "p1",
            clientRequestId: pending.clientRequestId,
        })).toMatchObject({ operation: "redo", sourceExportId: source.id });
    });



    it("rejects late export create ACKs from an old environment and keeps the export journal", async () => {
        const pending = deferred<ReturnType<typeof ack>>();
        await setup(baseProject("p1", 2, { sequence: [entry()] }));
        sequenceApi.createExport.mockReturnValueOnce(pending.promise);
        const exporting = state().createSequenceExport();
        const requestId = state().pendingExports[0].clientRequestId;
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        pending.resolve(ack(baseProject("p1", 2, { sequence: [entry()] }), requestId));

        await expect(exporting).rejects.toThrow("changed");
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
        expect(readAtelierExportRequest({
            environment: requireActiveRuntimeEnvironment(),
            projectId: "p1",
            clientRequestId: requestId,
        })).toMatchObject({ operation: "create", sourceExportId: null });
    });

    it("journals export deletion before DELETE, does not locally splice history, and clears only the matching ACK", async () => {
        const record = exportRecord("p1", "export-delete-1");
        await setup(baseProject("p1", 7, { exports: [record] }));
        sequenceApi.deleteExport.mockImplementationOnce(async (_projectId: string, exportId: string, request: { client_request_id: string }) => {
            expect(exportId).toBe(record.id);
            expect(readAtelierExportDeletionRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1", clientRequestId: request.client_request_id }))
                .toMatchObject({ exportId: record.id, request: { delete_file: true } });
            expect(state().currentProject?.exports.map(item => item.id)).toEqual([record.id]);
            const deleted = deletedRecord(record, "deleted", request.client_request_id);
            return deleteAck(baseProject("p1", 8, { exports: [deleted] }), deleted);
        });

        await expect(state().deleteSequenceExport(record)).resolves.toMatchObject({ export_record: { deletion: { file_status: "deleted" } } });

        expect(sequenceApi.deleteExport).toHaveBeenCalledTimes(1);
        expect(state().pendingExportDeletions).toEqual([]);
        expect(state().currentProject?.exports[0].deletion?.file_status).toBe("deleted");
    });

    it("keeps a lost deletion ACK pending, reads it back from the project tombstone, and never mints a new id", async () => {
        const record = exportRecord("p1", "export-delete-1");
        await setup(baseProject("p1", 7, { exports: [record] }));
        sequenceApi.deleteExport.mockRejectedValueOnce(Object.assign(new Error("delete uncertain"), { status: 503 }));

        await expect(state().deleteSequenceExport(record)).rejects.toThrow("delete uncertain");
        const pending = state().pendingExportDeletions[0];
        expect(pending).toMatchObject({ exportId: record.id, request: { delete_file: true } });
        await expect(state().deleteSequenceExport(record)).rejects.toThrow("already pending");

        const tombstone = deletedRecord(record, "blocked", pending.clientRequestId);
        vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(baseProject("p1", 8, { exports: [tombstone] }))));
        await expect(state().readbackPendingExportDeletion(pending.clientRequestId)).resolves.toMatchObject({ export_record: { deletion: { file_status: "blocked" } } });

        expect(sequenceApi.deleteExport).toHaveBeenCalledTimes(1);
        expect(state().pendingExportDeletions).toEqual([]);
        expect(state().currentProject?.exports[0].deletion?.error_code).toBe("export_file_still_referenced");
    });

    it("rejects late deletion ACKs from an old environment and keeps the journal", async () => {
        const record = exportRecord("p1", "export-delete-1");
        const pending = deferred<ReturnType<typeof deleteAck>>();
        await setup(baseProject("p1", 7, { exports: [record] }));
        sequenceApi.deleteExport.mockReturnValueOnce(pending.promise);
        const deleting = state().deleteSequenceExport(record);
        const requestId = state().pendingExportDeletions[0].clientRequestId;
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        const deleted = deletedRecord(record, "deleted", requestId);
        pending.resolve(deleteAck(baseProject("p1", 8, { exports: [deleted] }), deleted));

        await expect(deleting).rejects.toThrow("changed");
        expect(state().currentProject?.exports[0].deletion).toBeUndefined();
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
        expect(readAtelierExportDeletionRequest({
            environment: requireActiveRuntimeEnvironment(),
            projectId: "p1",
            clientRequestId: requestId,
        })).toMatchObject({ exportId: record.id });
    });

});
