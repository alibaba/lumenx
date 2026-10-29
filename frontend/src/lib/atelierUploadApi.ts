import {
    assertAtelierProjectIdentity, AtelierProjectApiError, isAtelierRecord,
    parseAtelierProject, parseAtelierUploadReceipt, requestAtelierJson,
    requireAtelierProjectIdInput, requireAtelierRevisionInput,
} from "@/lib/atelierApi";
import { canAttachAtelierReference, getAtelierReferenceNodeIds } from "@/lib/atelierCanvas";
import type {
    AtelierMediaKind, AtelierUploadReadback, AtelierUploadAck, AtelierUploadInput,
    AttachAtelierMediaUploadInput, CreateAtelierMediaUploadInput,
} from "@/lib/atelierTypes";

const EXTENSIONS: Record<AtelierMediaKind, readonly string[]> = {
    image: ["png", "jpg", "jpeg", "webp", "gif"],
    audio: ["mp3", "wav", "m4a", "ogg", "flac", "aac"],
    video: ["mp4", "mov", "webm", "mkv"],
};
const LIMITS = { image: 25, audio: 100, video: 250 };
export const ATELIER_UPLOAD_ACCEPT = Object.values(EXTENSIONS).flat().map(extension => `.${extension}`).join(",");
export const ATELIER_IMAGE_UPLOAD_ACCEPT = EXTENSIONS.image.map(extension => `.${extension}`).join(",");
export const ATELIER_UPLOAD_TARGET_ACTIVE_CODE = "atelier_upload_target_active_execution";
const invalid = () => new AtelierProjectApiError("Invalid Atelier upload input", "invalid_atelier_upload_input");
const mismatch = () => new AtelierProjectApiError("Atelier upload acknowledgement does not match the request", "atelier_upload_ack_mismatch");
const requestId = (id: string): string => {
    if (typeof id !== "string" || !/^[A-Za-z0-9._:-]{1,128}$/.test(id)) throw invalid();
    return id;
};
const path = (projectId: string) => `/atelier/projects/${encodeURIComponent(requireAtelierProjectIdInput(projectId))}/uploads`;

export function getAtelierUploadKind(file: File): AtelierMediaKind {
    if (!(file instanceof File) || !file.name.trim() || file.name.length > 255 || file.size === 0) throw invalid();
    const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
    const actualKind = (Object.keys(EXTENSIONS) as AtelierMediaKind[]).find(candidate => EXTENSIONS[candidate].includes(extension));
    if (!actualKind || file.size > LIMITS[actualKind] * 1024 * 1024) throw invalid();
    return actualKind;
}

function uploadQuery(input: AtelierUploadInput, kind?: AtelierMediaKind): { query: URLSearchParams; kind: AtelierMediaKind } {
    const revision = requireAtelierRevisionInput(input.expectedRevision);
    requestId(input.clientRequestId);
    const actualKind = getAtelierUploadKind(input.file);
    if (kind !== undefined && kind !== actualKind) throw invalid();
    const query = new URLSearchParams({ expected_revision: String(revision), client_request_id: input.clientRequestId, filename: input.file.name });
    if (input.title !== undefined) {
        if (typeof input.title !== "string") throw invalid();
        query.set("title", input.title);
    }
    for (const field of ["x", "y", "width", "height"] as const) {
        const value = input[field];
        if (value === undefined) continue;
        const size = field === "width" || field === "height";
        if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > (size ? 100000 : 10000000) || (size && value <= 0)) throw invalid();
        query.set(field, String(value));
    }
    return { query, kind: actualKind };
}

export const validateAtelierUploadInput = (input: AtelierUploadInput, kind?: AtelierMediaKind): AtelierMediaKind => uploadQuery(input, kind).kind;

function parseAck(raw: unknown, projectId: string, input: AtelierUploadInput, kind: AtelierMediaKind, targetId: string | null): AtelierUploadAck {
    if (!isAtelierRecord(raw) || typeof raw.replayed !== "boolean" || typeof raw.receipt_id !== "string" || !Array.isArray(raw.node_ids)) throw mismatch();
    const project = assertAtelierProjectIdentity(parseAtelierProject(raw.project), projectId);
    const receipt = project.node_upload_receipts.find(item => item.receipt_id === raw.receipt_id);
    if (!receipt || receipt.client_request_id !== input.clientRequestId
        || receipt.operation !== (targetId === null ? "create_media_node" : "upload_reference_attach")
        || receipt.target_node_id !== targetId || receipt.media.media_kind !== kind || receipt.media.size_bytes !== input.file.size
        || raw.node_ids.length !== receipt.node_ids.length || raw.node_ids.some((id, index) => id !== receipt.node_ids[index])) throw mismatch();
    // A replay reports the current project. Historical nodes may have been edited or deleted.
    if (!raw.replayed) {
        if (receipt.base_revision !== input.expectedRevision || project.revision !== receipt.committed_revision) throw mismatch();
        const source = project.nodes.find(node => node.id === receipt.source_node_id);
        const media = receipt.media;
        if (!source || source.type !== kind || source.status !== "completed" || source.media_urls.length !== 1 || source.media_urls[0] !== media.url
            || source.data.sha256 !== media.sha256 || source.data.size_bytes !== media.size_bytes || source.data.filename !== input.file.name) throw mismatch();
        for (const field of ["width", "height", "duration", "poster_url"] as const) {
            if (media[field] !== null && source.data[field] !== media[field]) throw mismatch();
        }
        for (const field of ["x", "y", "width", "height"] as const) {
            if (input[field] !== undefined && source[field] !== input[field]) throw mismatch();
        }
        if (input.title && source.title !== input.title) throw mismatch();
        if (targetId !== null) {
            const target = project.nodes.find(node => node.id === targetId);
            const urls = target?.data[`reference_${kind}_urls`];
            if (!target || !canAttachAtelierReference(target, source) || source.data.parent_node_id !== targetId
                || !getAtelierReferenceNodeIds(target).includes(source.id) || !Array.isArray(urls) || !urls.includes(media.url)) throw mismatch();
        }
    }
    return { project, node_ids: receipt.node_ids, receipt_id: receipt.receipt_id, replayed: raw.replayed };
}

async function upload(url: string, projectId: string, input: AtelierUploadInput, kind: AtelierMediaKind, targetId: string | null): Promise<AtelierUploadAck> {
    const raw = await requestAtelierJson(url, {
        method: "POST", headers: { "Content-Type": input.file.type || "application/octet-stream" }, body: input.file,
    }, [ATELIER_UPLOAD_TARGET_ACTIVE_CODE]);
    return parseAck(raw, projectId, input, kind, targetId);
}

export const atelierUploadApi = {
    async createMediaNode(projectId: string, input: CreateAtelierMediaUploadInput): Promise<AtelierUploadAck> {
        input = { ...input };
        const base = path(projectId);
        const { query, kind } = uploadQuery(input, input.nodeType);
        if (input.nodeType !== kind) throw invalid();
        query.set("node_type", kind);
        return upload(`${base}/nodes?${query}`, projectId, input, kind, null);
    },
    async uploadReference(projectId: string, targetId: string, input: AttachAtelierMediaUploadInput): Promise<AtelierUploadAck> {
        input = { ...input };
        const base = path(projectId);
        if (typeof targetId !== "string" || !targetId.trim()) throw invalid();
        const { query, kind } = uploadQuery(input, input.sourceNodeType);
        if (input.sourceNodeType !== undefined) query.set("source_node_type", input.sourceNodeType);
        return upload(`${base}/references/${encodeURIComponent(targetId)}?${query}`, projectId, input, kind, targetId);
    },
    async getReceipt(projectId: string, clientRequestId: string): Promise<AtelierUploadReadback> {
        const raw = await requestAtelierJson(`${path(projectId)}/receipts/${encodeURIComponent(requestId(clientRequestId))}`);
        if (!isAtelierRecord(raw) || raw.status !== "found") throw mismatch();
        const project = assertAtelierProjectIdentity(parseAtelierProject(raw.project), projectId);
        const receipt = parseAtelierUploadReceipt(raw.receipt);
        if (receipt.project_id !== projectId || receipt.client_request_id !== clientRequestId) throw mismatch();
        const saved = project.node_upload_receipts.find(item => item.receipt_id === receipt.receipt_id);
        if (!saved || JSON.stringify(saved) !== JSON.stringify(receipt)) throw mismatch();
        return { status: "found", project, receipt };
    },
};
