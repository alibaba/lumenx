// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { atelierSequenceApi } from "@/lib/atelierSequenceApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project.json";
import exportFixture from "./fixtures/atelier-export-job-contract.json";
import type { AtelierSequenceEntry } from "@/lib/atelierTypes";

const entry: AtelierSequenceEntry = { id: "clip", parentId: "video", candidateId: null, trimStart: null, trimEnd: 0.4, gain: 1, transition: { kind: "cut", duration: 0 } };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
beforeEach(() => { vi.restoreAllMocks(); clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" }); });

describe("sequence command client", () => {
    it("reads a backend-compatible completed job only with its original request and atomic export", async () => {
        const ack = { project: exportFixture, job: exportFixture.sequence_export_jobs[0], export_record: exportFixture.exports[0], replayed: false, new_job_ids: [] };
        const original = { expected_revision: 2, client_request_id: "client-request-1" };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(ack)));
        const result = await atelierSequenceApi.getExportByRequest(exportFixture.id, original.client_request_id, original);
        expect(result.job.status).toBe("completed");
        expect(result.export_record?.job_id).toBe(result.job.job_id);
        for (const patch of [{ job: { ...ack.job, client_request_id: "other" } }, { export_record: null }, { new_job_ids: [ack.job.job_id] }]) {
            vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...ack, ...patch })));
            await expect(atelierSequenceApi.getExportByRequest(exportFixture.id, original.client_request_id, original)).rejects.toThrow();
        }
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(ack)));
        await expect(atelierSequenceApi.getExportByRequest(exportFixture.id, original.client_request_id, { ...original, expected_revision: 1 })).rejects.toThrow();
    });
    it("saves exact entries with the captured revision and preserves omitted audio", async () => {
        const project = { ...structuredClone(fixture), sequence: [entry], revision: 8 };
        const fetch = vi.fn().mockResolvedValue(response(project)); vi.stubGlobal("fetch", fetch);
        const result = await atelierSequenceApi.saveSequence(fixture.id, { expectedRevision: 7, entries: [entry] });
        expect(result.sequence).toEqual([entry]);
        expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ expected_revision: 7, entries: [entry] });
    });

    it("rejects acknowledgements for other content or revision", async () => {
        for (const changes of [{ sequence: [] }, { revision: 6 }, { id: "other" }]) {
            vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...fixture, sequence: [entry], revision: 8, ...changes })));
            await expect(atelierSequenceApi.saveSequence(fixture.id, { expectedRevision: 7, entries: [entry] })).rejects.toThrow();
        }
    });

    it("retains every preflight issue and binds its project and revision", async () => {
        const report = { errors: [{ code: "missing_node", message: "Missing source", clip_id: "clip" }], warnings: [], duration: 0, width: 0, height: 0, fps: 25 };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ project_id: fixture.id, revision: 7, report })));
        expect(await atelierSequenceApi.preflight(fixture.id, { entries: [entry] })).toEqual({ project_id: fixture.id, revision: 7, report });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ project_id: "other", revision: 7, report })));
        await expect(atelierSequenceApi.preflight(fixture.id, { entries: [entry] })).rejects.toThrow();
    });

    it("keeps a dispatch-unavailable response as an error and does not retry", async () => {
        const fetch = vi.fn().mockResolvedValue(response({ detail: { code: "sequence_export_dispatch_unavailable", detail: "Job saved; read back original request" } }, 503));
        vi.stubGlobal("fetch", fetch);
        await expect(atelierSequenceApi.createExport(fixture.id, { expected_revision: 7, client_request_id: "req-1" })).rejects.toMatchObject({ status: 503 });
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("never serializes a nonfinite trim into a null override", async () => {
        const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
        await expect(atelierSequenceApi.createExport(fixture.id, { expected_revision: 7, client_request_id: "req-invalid", entries: [{ ...entry, trimEnd: Number.NaN }] as never })).rejects.toThrow();
        expect(fetch).not.toHaveBeenCalled();
    });

    it("posts redo requests through the export history route and binds ACKs to their source export", async () => {
        const project = structuredClone(exportFixture) as unknown as Record<string, unknown> & {
            exports: Array<{ id: string }>;
            sequence_export_jobs: Array<Record<string, unknown>>;
        };
        project.sequence_export_jobs[0] = {
            ...project.sequence_export_jobs[0],
            status: "queued",
            request_contract_version: "atelier-sequence-export-redo-v1",
            client_request_id: "redo-request-1",
            source_export_id: project.exports[0].id,
            base_revision: exportFixture.revision,
            started_at: null,
            finished_at: null,
            prepared_proof: null,
            export_id: null,
        };
        const ack = { project, job: project.sequence_export_jobs[0], export_record: null, replayed: false, new_job_ids: [project.sequence_export_jobs[0].job_id] };
        const fetch = vi.fn().mockResolvedValue(response(ack));
        vi.stubGlobal("fetch", fetch);

        await expect(atelierSequenceApi.redoExport(exportFixture.id, project.exports[0].id, {
            expected_revision: exportFixture.revision,
            client_request_id: "redo-request-1",
        })).resolves.toMatchObject({
            job: {
                request_contract_version: "atelier-sequence-export-redo-v1",
                source_export_id: project.exports[0].id,
            },
        });

        expect(fetch).toHaveBeenCalledTimes(1);
        expect(fetch.mock.calls[0][0]).toContain(`/atelier/projects/${exportFixture.id}/exports/${project.exports[0].id}/redo`);
        expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
            expected_revision: exportFixture.revision,
            client_request_id: "redo-request-1",
        });

        const wrongSource = structuredClone(project);
        wrongSource.sequence_export_jobs[0].source_export_id = "other-export";
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ ...ack, project: wrongSource, job: wrongSource.sequence_export_jobs[0] })));
        await expect(atelierSequenceApi.redoExport(exportFixture.id, project.exports[0].id, {
            expected_revision: exportFixture.revision,
            client_request_id: "redo-request-1",
        })).rejects.toThrow();
    });


    it("deletes export history through the export route and binds the deletion receipt", async () => {
        const project = structuredClone(exportFixture) as unknown as Record<string, unknown> & { exports: Array<Record<string, unknown>> };
        project.revision = exportFixture.revision + 1;
        project.exports[0] = {
            ...project.exports[0],
            deletion: {
                client_request_id: "delete-request-1",
                delete_file: true,
                deleted_at: 10,
                file_status: "deleted",
                checked_at: 11,
                error_code: null,
                error_message: null,
            },
        };
        const ack = { project, export_record: project.exports[0], replayed: false };
        const fetch = vi.fn().mockResolvedValue(response(ack));
        vi.stubGlobal("fetch", fetch);

        await expect(atelierSequenceApi.deleteExport(exportFixture.id, String(project.exports[0].id), {
            expected_revision: exportFixture.revision,
            client_request_id: "delete-request-1",
            delete_file: true,
        })).resolves.toMatchObject({
            export_record: { deletion: { client_request_id: "delete-request-1", file_status: "deleted" } },
            replayed: false,
        });

        expect(fetch).toHaveBeenCalledTimes(1);
        expect(fetch.mock.calls[0][0]).toContain(`/atelier/projects/${exportFixture.id}/exports/${project.exports[0].id}`);
        expect(fetch.mock.calls[0][1].method).toBe("DELETE");
        expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
            expected_revision: exportFixture.revision,
            client_request_id: "delete-request-1",
            delete_file: true,
        });

        const mismatched = structuredClone(project);
        mismatched.exports[0] = { ...mismatched.exports[0], deletion: { ...(mismatched.exports[0].deletion as Record<string, unknown>), client_request_id: "other" } };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ project: mismatched, export_record: mismatched.exports[0], replayed: false })));
        await expect(atelierSequenceApi.deleteExport(exportFixture.id, String(project.exports[0].id), {
            expected_revision: exportFixture.revision,
            client_request_id: "delete-request-1",
            delete_file: true,
        })).rejects.toThrow();
    });
});
