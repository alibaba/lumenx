import {
    assertAtelierProjectIdentity, assertAtelierProjectRevisionNotBefore, AtelierProjectApiError,
    isAtelierRecord, parseAtelierProject, parseAtelierSequenceEntry, parseAtelierAudioTrack,
    parseAtelierSequenceExportJob, parseAtelierExportRecord, requestAtelierJson,
    requireAtelierProjectIdInput, requireAtelierRevisionInput,
} from "@/lib/atelierApi";
import type { AtelierAudioTrack, AtelierExportRecord, AtelierProject, AtelierSequenceEntry, AtelierSequenceExportJob } from "@/lib/atelierTypes";
import type { AtelierExportDeletionRequest, StoredAtelierExportDeletionRequest } from "@/lib/atelierExportDeletionRequest";
import type { AtelierExportOriginalRequest, StoredAtelierExportRequest } from "@/lib/atelierExportRequest";

export interface AtelierSequenceExportAck {
    project: AtelierProject;
    job: AtelierSequenceExportJob;
    export_record: AtelierExportRecord | null;
    replayed: boolean;
    new_job_ids: string[];
}
export interface AtelierExportDeletionAck {
    project: AtelierProject;
    export_record: AtelierExportRecord;
    replayed: boolean;
}
export interface AtelierSequenceIssue { code: string; message: string; clip_id?: string; track_id?: string }
export interface AtelierSequencePreflight {
    project_id: string;
    revision: number;
    report: { errors: AtelierSequenceIssue[]; warnings: AtelierSequenceIssue[]; duration: number; width: number; height: number; fps: number };
}
export type RedoAtelierExportRequest = Pick<AtelierExportOriginalRequest, "expected_revision" | "client_request_id">;
type SequenceInput = { entries: AtelierSequenceEntry[]; audioTracks?: AtelierAudioTrack[] };
const mismatch = () => new AtelierProjectApiError("Sequence acknowledgement does not match the requested operation", "atelier_sequence_ack_mismatch");
const path = (id: string) => `/atelier/projects/${encodeURIComponent(requireAtelierProjectIdInput(id))}/sequence`;
const projectPath = (id: string) => `/atelier/projects/${encodeURIComponent(requireAtelierProjectIdInput(id))}`;
const idPart = (id: string) => {
    if (typeof id !== "string" || !/^[A-Za-z0-9._:-]{1,128}$/.test(id)) throw mismatch();
    return encodeURIComponent(id);
};
const equal = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);
const normalizedEntries = (entries: AtelierSequenceEntry[]) => entries.map(item => ({
    ...parseAtelierSequenceEntry(item), id: item.id ?? null, trimStart: item.trimStart ?? null, trimEnd: item.trimEnd ?? null,
}));
const sequencePayload = (input: SequenceInput) => {
    if (!Array.isArray(input.entries)) throw mismatch();
    input.entries.forEach(parseAtelierSequenceEntry);
    if (input.audioTracks !== undefined) {
        if (!Array.isArray(input.audioTracks)) throw mismatch();
        input.audioTracks.forEach(parseAtelierAudioTrack);
    }
    return JSON.parse(JSON.stringify({ entries: input.entries, ...(input.audioTracks !== undefined ? { audio_tracks: input.audioTracks } : {}) }));
};

export function parseAtelierSequenceExportAck(raw: unknown, projectId: string, expectation: {
    jobId?: string; requestId?: string; request?: AtelierExportOriginalRequest | StoredAtelierExportRequest; sourceExportId?: string | null; creating?: boolean;
} = {}): AtelierSequenceExportAck {
    if (!isAtelierRecord(raw) || typeof raw.replayed !== "boolean" || !Array.isArray(raw.new_job_ids)) throw mismatch();
    const project = assertAtelierProjectIdentity(parseAtelierProject(raw.project), projectId);
    const job = parseAtelierSequenceExportJob(raw.job);
    const saved = project.sequence_export_jobs.find(item => item.job_id === job.job_id);
    if (job.project_id !== projectId || !saved || !equal(saved, job)
        || (expectation.jobId !== undefined && job.job_id !== expectation.jobId)
        || (expectation.requestId !== undefined && job.client_request_id !== expectation.requestId)) throw mismatch();
    const expectedRequest = expectation.request && "request" in expectation.request ? expectation.request.request : expectation.request;
    const expectedSourceExportId = expectation.sourceExportId ?? (expectation.request && "request" in expectation.request ? expectation.request.sourceExportId : undefined);
    if (expectedRequest) {
        if (job.client_request_id !== expectedRequest.client_request_id || job.base_revision !== expectedRequest.expected_revision) throw mismatch();
        if (expectedRequest.entries !== undefined && !equal(normalizedEntries(expectedRequest.entries.map(parseAtelierSequenceEntry)), normalizedEntries(job.source_entries))) throw mismatch();
        if (expectedRequest.audio_tracks !== undefined && !equal(expectedRequest.audio_tracks.map(parseAtelierAudioTrack), job.source_audio_tracks)) throw mismatch();
    }
    if (expectedSourceExportId !== undefined) {
        if (job.source_export_id !== expectedSourceExportId) throw mismatch();
    }
    const exportRecord = raw.export_record === null ? null : parseAtelierExportRecord(raw.export_record);
    const savedExport = project.exports.find(item => item.id === job.export_id) ?? null;
    if (!equal(exportRecord, savedExport) || (job.status === "completed") !== (exportRecord !== null)) throw mismatch();
    const expectedIds = expectation.creating && !raw.replayed ? [job.job_id] : [];
    if (!equal(raw.new_job_ids, expectedIds)) throw mismatch();
    return { project, job, export_record: exportRecord, replayed: raw.replayed, new_job_ids: expectedIds };
}


export function parseAtelierExportDeletionAck(raw: unknown, projectId: string, exportId: string, expectation: {
    request?: AtelierExportDeletionRequest | StoredAtelierExportDeletionRequest;
} = {}): AtelierExportDeletionAck {
    if (!isAtelierRecord(raw) || typeof raw.replayed !== "boolean") throw mismatch();
    const project = assertAtelierProjectIdentity(parseAtelierProject(raw.project), projectId);
    const exportRecord = parseAtelierExportRecord(raw.export_record);
    const saved = project.exports.find(item => item.id === exportId) ?? null;
    if (exportRecord.project_id !== projectId || exportRecord.id !== exportId || !saved || !equal(saved, exportRecord) || exportRecord.deletion == null) {
        throw mismatch();
    }
    const expectedRequest = expectation.request && "request" in expectation.request ? expectation.request.request : expectation.request;
    if (expectedRequest) {
        if (exportRecord.deletion.client_request_id !== expectedRequest.client_request_id
            || exportRecord.deletion.delete_file !== expectedRequest.delete_file) {
            throw mismatch();
        }
        assertAtelierProjectRevisionNotBefore(project, expectedRequest.expected_revision);
    }
    return { project, export_record: exportRecord, replayed: raw.replayed };
}

function parsePreflight(raw: unknown, projectId: string): AtelierSequencePreflight {
    if (!isAtelierRecord(raw) || raw.project_id !== projectId || !isAtelierRecord(raw.report)) throw mismatch();
    const revision = requireAtelierRevisionInput(raw.revision as number);
    const issues = (value: unknown): AtelierSequenceIssue[] => {
        if (!Array.isArray(value)) throw mismatch();
        return value.map(item => {
            if (!isAtelierRecord(item) || typeof item.code !== "string" || typeof item.message !== "string") throw mismatch();
            const issue: AtelierSequenceIssue = { code: item.code, message: item.message };
            for (const key of ["clip_id", "track_id"] as const) {
                if (item[key] !== undefined) { if (typeof item[key] !== "string") throw mismatch(); issue[key] = item[key]; }
            }
            return issue;
        });
    };
    const number = (key: string) => {
        const value = raw.report as Record<string, unknown>;
        const n = value[key];
        if (typeof n !== "number" || !Number.isFinite(n) || n < 0) throw mismatch();
        return n;
    };
    return { project_id: projectId, revision, report: { errors: issues(raw.report.errors), warnings: issues(raw.report.warnings), duration: number("duration"), width: number("width"), height: number("height"), fps: number("fps") } };
}

async function jobAction(projectId: string, jobId: string, action: "cancel" | "repair" | "resume"): Promise<AtelierSequenceExportAck> {
    return parseAtelierSequenceExportAck(await requestAtelierJson(`${path(projectId)}/export_jobs/${idPart(jobId)}/${action}`, { method: "POST" }), projectId, { jobId });
}

export const atelierSequenceApi = {
    async saveSequence(projectId: string, input: SequenceInput & { expectedRevision: number }): Promise<AtelierProject> {
        const payload = { expected_revision: requireAtelierRevisionInput(input.expectedRevision), ...sequencePayload(input) };
        const project = assertAtelierProjectRevisionNotBefore(assertAtelierProjectIdentity(parseAtelierProject(await requestAtelierJson(path(projectId), {
            method: "PUT", body: JSON.stringify(payload),
        })), projectId), payload.expected_revision);
        if (!equal(normalizedEntries(project.sequence), normalizedEntries(payload.entries))) throw mismatch();
        if (payload.audio_tracks !== undefined && !equal(project.audio_tracks.map(parseAtelierAudioTrack), payload.audio_tracks.map(parseAtelierAudioTrack))) throw mismatch();
        return project;
    },
    async preflight(projectId: string, input: SequenceInput): Promise<AtelierSequencePreflight> {
        return parsePreflight(await requestAtelierJson(`${path(projectId)}/preflight`, { method: "POST", body: JSON.stringify(sequencePayload(input)) }), projectId);
    },
    async createExport(projectId: string, request: AtelierExportOriginalRequest): Promise<AtelierSequenceExportAck> {
        requireAtelierRevisionInput(request.expected_revision); idPart(request.client_request_id);
        if (Object.keys(request).some(key => !["expected_revision", "client_request_id", "entries", "audio_tracks"].includes(key))) throw mismatch();
        for (const key of ["entries", "audio_tracks"] as const) {
            if (Object.prototype.hasOwnProperty.call(request, key) && !Array.isArray(request[key])) throw mismatch();
        }
        const body = JSON.stringify(request, (_key, value: unknown) => {
            if (typeof value === "undefined" || typeof value === "function" || typeof value === "symbol" || typeof value === "bigint"
                || (typeof value === "number" && !Number.isFinite(value))) throw mismatch();
            return value;
        });
        const frozen = JSON.parse(body) as AtelierExportOriginalRequest;
        return parseAtelierSequenceExportAck(await requestAtelierJson(`${path(projectId)}/export_jobs`, { method: "POST", body }), projectId, { request: frozen, sourceExportId: null, creating: true });
    },
    async redoExport(projectId: string, exportId: string, request: RedoAtelierExportRequest): Promise<AtelierSequenceExportAck> {
        requireAtelierRevisionInput(request.expected_revision); idPart(request.client_request_id);
        if (Object.keys(request).some(key => !["expected_revision", "client_request_id"].includes(key))) throw mismatch();
        const sourceExportId = idPart(exportId);
        const body = JSON.stringify(request, (_key, value: unknown) => {
            if (typeof value === "undefined" || typeof value === "function" || typeof value === "symbol" || typeof value === "bigint"
                || (typeof value === "number" && !Number.isFinite(value))) throw mismatch();
            return value;
        });
        const frozen = JSON.parse(body) as RedoAtelierExportRequest;
        return parseAtelierSequenceExportAck(await requestAtelierJson(`${projectPath(projectId)}/exports/${sourceExportId}/redo`, { method: "POST", body }),
            projectId, { request: frozen, sourceExportId: exportId, creating: true });
    },
    async deleteExport(projectId: string, exportId: string, request: AtelierExportDeletionRequest): Promise<AtelierExportDeletionAck> {
        requireAtelierRevisionInput(request.expected_revision); idPart(request.client_request_id);
        if (typeof request.delete_file !== "boolean" || Object.keys(request).some(key => !["expected_revision", "client_request_id", "delete_file"].includes(key))) throw mismatch();
        const targetExportId = idPart(exportId);
        const body = JSON.stringify(request, (_key, value: unknown) => {
            if (typeof value === "undefined" || typeof value === "function" || typeof value === "symbol" || typeof value === "bigint"
                || (typeof value === "number" && !Number.isFinite(value))) throw mismatch();
            return value;
        });
        const frozen = JSON.parse(body) as AtelierExportDeletionRequest;
        return parseAtelierExportDeletionAck(await requestAtelierJson(`${projectPath(projectId)}/exports/${targetExportId}`, { method: "DELETE", body }),
            projectId, exportId, { request: frozen });
    },
    async getExport(projectId: string, jobId: string): Promise<AtelierSequenceExportAck> {
        return parseAtelierSequenceExportAck(await requestAtelierJson(`${path(projectId)}/export_jobs/${idPart(jobId)}`), projectId, { jobId });
    },
    async getExportByRequest(projectId: string, requestId: string, originalRequest?: AtelierExportOriginalRequest | StoredAtelierExportRequest, sourceExportId?: string | null): Promise<AtelierSequenceExportAck> {
        return parseAtelierSequenceExportAck(await requestAtelierJson(`${path(projectId)}/export_jobs/by_request/${idPart(requestId)}`), projectId, { requestId, request: originalRequest, sourceExportId });
    },
    cancelExport: (projectId: string, jobId: string) => jobAction(projectId, jobId, "cancel"),
    repairExport: (projectId: string, jobId: string) => jobAction(projectId, jobId, "repair"),
    resumeExport: (projectId: string, jobId: string) => jobAction(projectId, jobId, "resume"),
};
