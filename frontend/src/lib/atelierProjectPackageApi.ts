import { API_URL } from "@/lib/apiUrl";
import { appAccessFetch } from "@/lib/appAccess";
import { AtelierProjectApiError, isAtelierRecord, parseAtelierProject, requireAtelierProjectIdInput } from "@/lib/atelierApi";
import type { AtelierProject } from "@/lib/atelierTypes";
import { filenameFromContentDisposition, PROJECT_PACKAGE_MAX_BYTES } from "@/lib/projectPackage";
import { isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";

export class AtelierPackageImportError extends AtelierProjectApiError {
    constructor(message: string, public readonly outcomeUnknown: boolean, code = "atelier_package_import_failed", status?: number) {
        super(message, code, status);
        this.name = "AtelierPackageImportError";
    }
}

function assertEnvironment(environment: { key: string; generation: number }, importing?: boolean): void {
    if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
    const message = "Runtime environment changed during project package transfer";
    if (importing !== undefined) throw new AtelierPackageImportError(message, importing, "environment_changed");
    throw new AtelierProjectApiError(message, "environment_changed");
}

function knownImportRejection(response: Response, raw: unknown): boolean {
    if (!isAtelierRecord(raw)) return false;
    const error = isAtelierRecord(raw.detail) ? raw.detail : raw;
    const code = error.code;
    if (response.status === 503 && code === "atelier_storage_unavailable") return error.commit_uncertain === false;
    const expected: Record<number, readonly string[]> = {
        401: ["access_required"],
        403: ["access_required"],
        409: ["atelier_project_package_conflict", "environment_changed"],
        413: ["atelier_project_package_too_large"],
        422: ["atelier_project_package_invalid", "atelier_project_package_invalid_request"],
    };
    return typeof code === "string" && !!expected[response.status]?.includes(code);
}

function parseImportAck(raw: unknown): AtelierProject {
    const project = parseAtelierProject(raw);
    if (!project.import_provenance || project.id === project.import_provenance.source_project_id
        || project.revision !== 0 || project.agent_turns.length !== 0 || project.generation_attempts.length !== 0
        || project.generation_request_receipts.length !== 0 || project.sequence_export_jobs.length !== 0
        || project.node_upload_receipts.length !== 0) {
        throw new AtelierProjectApiError("Invalid independent project package acknowledgement", "invalid_atelier_package_ack");
    }
    return project;
}

export const atelierProjectPackageApi = {
    async importProjectPackage(file: Blob): Promise<AtelierProject> {
        const environment = requireActiveRuntimeEnvironment();
        if (!(file instanceof Blob) || file.size <= 0 || file.size > PROJECT_PACKAGE_MAX_BYTES) {
            throw new AtelierPackageImportError("Choose a nonempty project package within the size limit", false, "invalid_atelier_package_input");
        }
        let bytes: ArrayBuffer;
        try {
            bytes = await file.arrayBuffer();
        } catch {
            throw new AtelierPackageImportError("Project package could not be read", false, "atelier_package_read_failed");
        }
        assertEnvironment(environment, false);
        if (bytes.byteLength !== file.size || bytes.byteLength > PROJECT_PACKAGE_MAX_BYTES) {
            throw new AtelierPackageImportError("Project package content changed before submission", false, "invalid_atelier_package_input");
        }
        let response: Response;
        try {
            response = await appAccessFetch(`${API_URL}/atelier/projects/package/import`, {
                method: "POST", cache: "no-store",
                headers: { "Content-Type": "application/zip", "Accept": "application/json" }, body: bytes,
            });
        } catch {
            assertEnvironment(environment, true);
            throw new AtelierPackageImportError("Project package import outcome is unknown; check the project list before importing again", true);
        }
        assertEnvironment(environment, true);
        let raw: unknown;
        try {
            raw = await response.json();
        } catch {
            assertEnvironment(environment, true);
            throw new AtelierPackageImportError("Project package import acknowledgement could not be read; check the project list", true);
        }
        assertEnvironment(environment, true);
        if (!response.ok) {
            const knownRejected = knownImportRejection(response, raw);
            throw new AtelierPackageImportError(knownRejected ? "Project package import was rejected"
                : "Project package import outcome is unknown; check the project list before importing again", !knownRejected, "atelier_package_import_failed", response.status);
        }
        try {
            return parseImportAck(raw);
        } catch {
            throw new AtelierPackageImportError("Project package import acknowledgement is invalid; check the project list", true, "invalid_atelier_package_ack");
        }
    },

    async exportProjectPackage(projectId: string): Promise<{ blob: Blob; filename?: string }> {
        const id = requireAtelierProjectIdInput(projectId);
        const environment = requireActiveRuntimeEnvironment();
        const response = await appAccessFetch(`${API_URL}/atelier/projects/${encodeURIComponent(id)}/package/export`, {
            method: "GET", cache: "no-store", headers: { "Accept": "application/zip" },
        });
        assertEnvironment(environment);
        if (!response.ok) throw new AtelierProjectApiError("Project package export failed", "atelier_package_export_failed", response.status);
        const mediaType = response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
        const length = response.headers.get("Content-Length");
        if (mediaType !== "application/zip" || (length !== null && (!/^\d+$/.test(length) || Number(length) > PROJECT_PACKAGE_MAX_BYTES))) {
            throw new AtelierProjectApiError("Invalid project package download", "invalid_atelier_package_download");
        }
        const blob = await response.blob();
        assertEnvironment(environment);
        if (blob.size <= 0 || blob.size > PROJECT_PACKAGE_MAX_BYTES) {
            throw new AtelierProjectApiError("Invalid project package download", "invalid_atelier_package_download");
        }
        return { blob, filename: filenameFromContentDisposition(response.headers.get("Content-Disposition")) };
    },
};
