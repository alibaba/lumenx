import { describe, expect, it } from "vitest";
import {
    parseAtelierAudioTrack,
    parseAtelierProject,
    parseAtelierSequenceEntry,
    parseAtelierSequenceExportJob,
} from "@/lib/atelierApi";
import fixture from "./fixtures/atelier-export-job-contract.json";
import legacyFixture from "./fixtures/atelier-project.json";
import retainedDeletionFixture from "./fixtures/atelier-export-deletion-retained.json";

const clone = <T>(value: T): T => structuredClone(value);

describe("Atelier sequence export job contract parser", () => {
    it("preserves backend-serialized export jobs and completed publication proof bindings", () => {
        const project = parseAtelierProject(fixture);

        expect(project.sequence_export_jobs).toHaveLength(1);
        expect(project.sequence_export_jobs[0].status).toBe("completed");
        expect(project.sequence_export_jobs[0].prepared_proof?.relative_path).toBe(project.exports[0].video_url);
        expect(project.exports[0].job_id).toBe(project.sequence_export_jobs[0].job_id);
    });

    it("defaults legacy projects without sequence export jobs to an empty journal", () => {
        const legacy = clone(legacyFixture) as Record<string, unknown>;
        delete legacy.sequence_export_jobs;

        expect(parseAtelierProject(legacy).sequence_export_jobs).toEqual([]);
    });

    it("rejects invalid entry and audio bounds before accepting export jobs", () => {
        expect(() => parseAtelierSequenceEntry({
            parentId: "",
            candidateId: null,
            trimStart: -0.1,
            trimEnd: 0,
            gain: 2.1,
            transition: { kind: "fade", duration: -1 },
        })).toThrow();

        expect(() => parseAtelierAudioTrack({
            id: "",
            node_id: "audio-node-1",
            start: -1,
            trim_start: -0.1,
            trim_end: 0,
            gain: 2.1,
            loop: false,
        })).toThrow();
    });

    it("rejects malformed job shape, ids, revisions, paths, hashes, and state/proof bindings", () => {
        const job = clone(fixture.sequence_export_jobs[0]);
        const invalidJobs = [
            { extra: true },
            { job_id: "bad id" },
            { project_id: "" },
            { status: "done" },
            { client_request_id: "x".repeat(129) },
            { request_fingerprint: "not-a-sha" },
            { base_revision: -1 },
            { job_revision: 1.5 },
            { started_at: Number.POSITIVE_INFINITY },
            { output_ref: "video/atelier_seq_other.mp4" },
            { prepared_proof: { ...job.prepared_proof, project_id: "other" } },
            { status: "repair_required", prepared_proof: null },
            { status: "completed", export_id: null },
            { status: "queued", export_id: "export-job-1" },
        ];

        for (const patch of invalidJobs) {
            expect(() => parseAtelierSequenceExportJob({ ...job, ...patch })).toThrow();
        }
    });

    it("rejects completed jobs unless the export record matches proof, source, snapshot, output, size, and count", () => {
        const cases = [
            { exports: [] },
            { exports: [{ ...fixture.exports[0], id: "other-export" }] },
            { exports: [{ ...fixture.exports[0], publication_proof: { ...fixture.exports[0].publication_proof, sha256: "3333333333333333333333333333333333333333333333333333333333333333" } }] },
            { exports: [{ ...fixture.exports[0], source_entries: [] }] },
            { exports: [{ ...fixture.exports[0], timeline_snapshot: { clips: [] } }] },
            { exports: [{ ...fixture.exports[0], filename: "wrong.mp4" }] },
            { exports: [{ ...fixture.exports[0], clip_count: 2 }] },
            { exports: [{ ...fixture.exports[0], size_mb: 1 }] },
        ];

        for (const patch of cases) {
            expect(() => parseAtelierProject({ ...fixture, ...patch })).toThrow();
        }
    });

    it("accepts retained deletion receipts with terminal checked_at and rejects inconsistent receipt timing", () => {
        const retained = parseAtelierProject(retainedDeletionFixture);
        expect(retained.exports[0].deletion).toMatchObject({
            delete_file: false,
            file_status: "retained",
            checked_at: 21,
            error_code: null,
            error_message: null,
        });

        const retainedNullChecked = clone(retainedDeletionFixture) as { exports: Array<{ deletion: { checked_at: number | null } }> };
        retainedNullChecked.exports[0].deletion.checked_at = null;
        expect(() => parseAtelierProject(retainedNullChecked)).toThrow();

        const pendingWithChecked = clone(retainedDeletionFixture) as { exports: Array<{ deletion: Record<string, unknown> }> };
        pendingWithChecked.exports[0].deletion = {
            ...pendingWithChecked.exports[0].deletion,
            delete_file: true,
            file_status: "pending",
            checked_at: 21,
        };
        expect(() => parseAtelierProject(pendingWithChecked)).toThrow();
    });

});
