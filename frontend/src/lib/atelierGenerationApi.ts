import {
    assertAtelierProjectIdentity,
    assertAtelierProjectRevisionNotBefore,
    AtelierProjectApiError,
    isAtelierRecord,
    parseAtelierGenerationAttempt,
    parseAtelierGenerationCandidate,
    parseAtelierGenerationRequestReceipt,
    parseAtelierProject,
    requestAtelierJson,
    requireAtelierProjectIdInput,
    requireAtelierRevisionInput,
} from "@/lib/atelierApi";
import { hasCanonicalAtelierOrderedMediaRoleOrder, isAtelierOrderedMediaRole } from "@/lib/atelierOrderedMedia";
import { parseAtelierNodeCommandAck } from "@/lib/atelierNodeApi";
import { validateWan3GenerationParams } from "@/lib/wan3GenerationParams";
import type {
    AtelierAudioCandidateRequest,
    AtelierGenerationAdmissionAck,
    AtelierGenerationAttemptAck,
    AtelierGenerationAttemptIdentityRequest,
    AtelierGenerationAdmissionMember,
    AtelierGenerationAttempt,
    AtelierGenerationBatchAdmissionAck,
    AtelierGenerationSingletonAdmissionAck,
    AtelierGenerationCandidateProjection,
    AtelierGenerationDraft,
    AtelierGenerationRequest,
    AtelierImageCandidateRequest,
    AtelierOrderedMediaRole,
    AtelierOrderedGenerationAttempt,
    AtelierOrderedMediaSelection,
    AtelierOrderedVideoCandidateRequest,
    AtelierOrderedVideoMode,
    AtelierOrderedVideoModel,
    SetAtelierOrderedGenerationDraftInput,
    AtelierProject,
    AtelierNodeCommandAck,
    SelectAtelierGenerationCandidateInput,
    SetAtelierGenerationDraftInput,
} from "@/lib/atelierTypes";

const REQUEST_ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const HASH_RE = /^[0-9a-f]{64}$/;
const LEGACY_VIDEO_PARAMS = new Set(["duration", "resolution", "seed", "negative_prompt", "prompt_extend", "watermark"]);
const ORDERED_ENTRYPOINT = "atelier.provider_video.v2";
const LEGACY_ENTRYPOINT = "atelier.provider_video.v1";
const IMAGE_ENTRYPOINT = "atelier.provider_image.v1";
const AUDIO_ENTRYPOINT = "atelier.provider_audio.v1";
const ORDERED_MEDIA_CONTRACT = "ordered-media-v1";
const ORDERED_MODELS = new Set(["wan/wan3.0-video#video", "wan/wan3.0-video-prime#video"]);
const REFERENCE_MEDIA_ROLES = new Set(["reference_image", "reference_video", "reference_audio"]);
const ACK_BASE_FIELDS = ["project", "receipt", "replayed", "new_candidate_ids", "reason"] as const;
const ACK_SINGLE_FIELDS = [...ACK_BASE_FIELDS, "attempt", "candidate"] as const;
const ACK_BATCH_FIELDS = [...ACK_BASE_FIELDS, "members"] as const;
const ACK_BATCH_MEMBER_FIELDS = ["attempt", "candidate"] as const;
const IMPORTED_CANDIDATE_AUTHORITY_KEYS = new Set([
    "agent_authorization_ref",
    "attempt_id",
    "attempt_revision",
    "attempt_token",
    "binding",
    "credential_ref",
    "execution_contract",
    "execution_fingerprint",
    "handle",
    "provider_execution",
    "provider_handle",
    "provider_request_id",
    "request_fingerprint",
    "request_id",
    "task_id",
]);

const mismatch = (): never => {
    throw new AtelierProjectApiError("Atelier generation acknowledgement does not match the request", "atelier_generation_ack_mismatch");
};

const idPart = (value: string, code = "invalid_atelier_generation_id"): string => {
    if (typeof value !== "string" || value.length === 0 || value.length > 128 || !/^[A-Za-z0-9._:-]+$/.test(value)) {
        throw new AtelierProjectApiError("Invalid Atelier generation id", code);
    }
    return encodeURIComponent(value);
};

const projectPath = (projectId: string): string => `/atelier/projects/${encodeURIComponent(requireAtelierProjectIdInput(projectId))}`;
const nodePath = (projectId: string, nodeId: string): string => `${projectPath(projectId)}/nodes/${idPart(nodeId, "invalid_atelier_node_id")}`;
const generationPath = (projectId: string): string => `${projectPath(projectId)}/generation`;

const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(item => canonical(item));
    if (!isAtelierRecord(value)) return value;
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
};

const equal = (a: unknown, b: unknown): boolean => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const exactKeys = (value: Record<string, unknown>, expected: readonly string[]): boolean => {
    const keys = Object.keys(value);
    return keys.length === expected.length && keys.every(key => expected.includes(key));
};

const requireRequestId = (value: string): string => {
    if (typeof value !== "string" || !REQUEST_ID_RE.test(value)) throw mismatch();
    return value;
};

const requireFingerprint = (value: string): string => {
    if (typeof value !== "string" || !HASH_RE.test(value)) throw mismatch();
    return value;
};

const requireLegacyVideoParams = (value: unknown): void => {
    if (value === undefined) return;
    if (!isAtelierRecord(value) || Object.keys(value).some(key => !LEGACY_VIDEO_PARAMS.has(key))) throw mismatch();
    for (const [key, item] of Object.entries(value)) {
        if (key === "duration" && (typeof item !== "number" || !Number.isFinite(item))) throw mismatch();
        if (key === "resolution" && (typeof item !== "string" || item.length === 0)) throw mismatch();
        if (key === "seed" && (typeof item !== "number" || !Number.isSafeInteger(item))) throw mismatch();
        if (key === "negative_prompt" && typeof item !== "string") throw mismatch();
        if ((key === "prompt_extend" || key === "watermark") && typeof item !== "boolean") throw mismatch();
    }
};

const requireOrderedVideoParams = (modelId: string, value: unknown): void => {
    if (!validateWan3GenerationParams(modelId, value)) throw mismatch();
};

const jsonBody = (value: Record<string, unknown>): string => JSON.stringify(value, (_key, item: unknown) => {
    if (typeof item === "undefined" || typeof item === "function" || typeof item === "symbol" || typeof item === "bigint"
        || (typeof item === "number" && !Number.isFinite(item))) throw mismatch();
    return item;
});


interface SetAtelierGenerationCandidateVisibilityInput {
    expected_revision: number;
    visible: boolean;
}

type CandidateBucket = "visible" | "hidden";

const candidateBucketFromProject = (project: AtelierGenerationAdmissionAck["project"], nodeId: string, candidateId: string): { bucket: CandidateBucket; record: Record<string, unknown> } | null => {
    const node = project.nodes.find(item => item.id === nodeId);
    if (!node) return null;
    for (const [field, bucket] of [["candidates", "visible"], ["deleted_candidates", "hidden"]] as const) {
        const values = node.data[field];
        if (!Array.isArray(values)) continue;
        const found = values.find(item => isAtelierRecord(item) && item.id === candidateId);
        if (isAtelierRecord(found)) return { bucket, record: found };
    }
    return null;
};

const candidateFromProject = (project: AtelierGenerationAdmissionAck["project"], nodeId: string, candidateId: string): AtelierGenerationCandidateProjection | null => {
    const node = project.nodes.find(item => item.id === nodeId);
    if (!node) return null;
    for (const field of ["candidates", "deleted_candidates"] as const) {
        const values = node.data[field];
        if (!Array.isArray(values)) continue;
        const found = values.find(item => isAtelierRecord(item)
            && (item.execution_contract === "atelier-provider-video-v1" || item.execution_contract === "atelier-provider-media-v2")
            && item.id === candidateId);
        if (isAtelierRecord(found)) return parseAtelierGenerationCandidate(found);
    }
    return null;
};

const isSafeImportedVideoPath = (value: unknown, projectId: string): value is string => {
    if (typeof value !== "string" || value.includes("\\") || value.includes("?") || value.includes("#")) return false;
    const parts = value.split("/");
    if (parts.length < 4 || parts[0] !== "video" || parts[1] !== "imported" || parts[2] !== projectId) return false;
    return parts.every(part => part.length > 0 && part !== "." && part !== ".." && !/[\u0000-\u001f\u007f]/.test(part));
};

export const isSelectableImportedAtelierVideoCandidate = (
    project: Pick<AtelierProject, "id" | "import_provenance">,
    record: Record<string, unknown>,
    candidateId: string,
): record is Record<string, unknown> & { video_url: string } => {
    if (!project.import_provenance) return false;
    if (Object.keys(record).some(key => IMPORTED_CANDIDATE_AUTHORITY_KEYS.has(key))) return false;
    if (record.id !== candidateId || record.status !== "completed" || record.media_kind !== "video") return false;
    return isSafeImportedVideoPath(record.video_url, project.id);
};

const importedCandidateFromProject = (project: AtelierGenerationAdmissionAck["project"], nodeId: string, candidateId: string): { video_url: string } | null => {
    const found = candidateBucketFromProject(project, nodeId, candidateId);
    if (!found || found.bucket !== "visible") return null;
    if (!isSelectableImportedAtelierVideoCandidate(project, found.record, candidateId)) return null;
    return { video_url: found.record.video_url };
};

const receiptMembers = (receipt: AtelierGenerationAdmissionAck["receipt"]): Array<{ candidate_id: string; attempt_id: string; execution_fingerprint: string }> => {
    if (receipt.schema_version === 1) {
        if (!receipt.candidate_id || !receipt.attempt_id || !receipt.execution_fingerprint) throw mismatch();
        return [{ candidate_id: receipt.candidate_id, attempt_id: receipt.attempt_id, execution_fingerprint: receipt.execution_fingerprint }];
    }
    if (!receipt.members) throw mismatch();
    return receipt.members;
};

const orderedAttempt = (attempt: AtelierGenerationAdmissionMember["attempt"]): attempt is AtelierOrderedGenerationAttempt => "input_contract_version" in attempt;
const mediaAttempt = (attempt: AtelierGenerationAdmissionMember["attempt"]): attempt is Extract<AtelierGenerationAttempt, { execution_contract: "atelier-provider-media-v2" }> => "execution_contract" in attempt && attempt.execution_contract === "atelier-provider-media-v2";
const orderedRequest = (request: AtelierGenerationRequest): request is AtelierOrderedVideoCandidateRequest => "contract_version" in request;
const imageRequest = (request: AtelierGenerationRequest): request is AtelierImageCandidateRequest => "entrypoint" in request && request.entrypoint === IMAGE_ENTRYPOINT;
const audioRequest = (request: AtelierGenerationRequest): request is AtelierAudioCandidateRequest => "entrypoint" in request && request.entrypoint === AUDIO_ENTRYPOINT;
const entrypointForRequest = (request: AtelierGenerationRequest): string => orderedRequest(request) ? ORDERED_ENTRYPOINT
    : imageRequest(request) ? IMAGE_ENTRYPOINT : audioRequest(request) ? AUDIO_ENTRYPOINT : LEGACY_ENTRYPOINT;

const requireOrderedMediaRole = (value: unknown): AtelierOrderedMediaRole => {
    if (!isAtelierOrderedMediaRole(value)) throw mismatch();
    return value;
};

const requireOrderedMedia = (value: unknown, mode: AtelierOrderedVideoCandidateRequest["mode"]): AtelierOrderedMediaSelection[] => {
    if (!Array.isArray(value)) throw mismatch();
    const media = value.map(item => {
        if (!isAtelierRecord(item) || !exactKeys(item, ["role", "ordinal", "node_id", "media_url"])) throw mismatch();
        if (!Number.isSafeInteger(item.ordinal) || (item.ordinal as number) < 0
            || typeof item.node_id !== "string" || !item.node_id.trim()
            || typeof item.media_url !== "string" || !item.media_url.trim()) throw mismatch();
        return { role: requireOrderedMediaRole(item.role), ordinal: item.ordinal as number, node_id: item.node_id, media_url: item.media_url };
    });
    const seen = new Set<string>();
    const byRole = new Map<AtelierOrderedMediaRole, number[]>();
    for (const item of media) {
        const key = `${item.role}:${item.ordinal}`;
        if (seen.has(key)) throw mismatch();
        seen.add(key);
        if ((item.role === "first_frame" || item.role === "last_frame") && item.ordinal !== 0) throw mismatch();
        byRole.set(item.role, [...(byRole.get(item.role) ?? []), item.ordinal]);
    }
    for (const ordinals of Array.from(byRole.values())) {
        if (ordinals.some((ordinal, index) => ordinal !== index)) throw mismatch();
    }
    if (!hasCanonicalAtelierOrderedMediaRoleOrder(media)) throw mismatch();
    const roles = media.map(item => item.role);
    if (mode === "t2v" && media.length !== 0) throw mismatch();
    if (mode === "i2v") {
        const first = roles.length === 1 && roles[0] === "first_frame";
        const firstLast = roles.length === 2 && roles[0] === "first_frame" && roles[1] === "last_frame";
        if (!first && !firstLast) throw mismatch();
    }
    if (mode === "r2v" && (media.length === 0 || roles.some(role => !REFERENCE_MEDIA_ROLES.has(role)))) throw mismatch();
    return media;
};

const providerMediaMatches = (providerMedia: unknown, inputMedia: AtelierOrderedMediaSelection[]): boolean => {
    return Array.isArray(providerMedia) && providerMedia.length === inputMedia.length
        && inputMedia.every((item, index) => {
            const provider = providerMedia[index];
            return isAtelierRecord(provider)
                && provider.schema_version === 1
                && provider.role === item.role
                && provider.ordinal === item.ordinal
                && typeof provider.declared_media_type === "string"
                && provider.declared_media_type.length > 0
                && typeof provider.sha256 === "string"
                && HASH_RE.test(provider.sha256)
                && typeof provider.size_bytes === "number"
                && Number.isSafeInteger(provider.size_bytes)
                && provider.size_bytes > 0;
        });
};


const requireImageRequestMedia = (value: unknown, mode: "t2i" | "i2i") => {
    if (!Array.isArray(value)) throw mismatch();
    const media = value.map(item => {
        if (!isAtelierRecord(item) || !exactKeys(item, ["role", "ordinal", "node_id", "media_url"]) || item.role !== "reference_image") throw mismatch();
        if (!Number.isSafeInteger(item.ordinal) || (item.ordinal as number) < 0
            || typeof item.node_id !== "string" || !item.node_id.trim()
            || typeof item.media_url !== "string" || !item.media_url.trim()) throw mismatch();
        return { role: "reference_image" as const, ordinal: item.ordinal as number, node_id: item.node_id as string, media_url: item.media_url as string };
    });
    if (mode === "t2i" && media.length !== 0) throw mismatch();
    if (mode === "i2i") {
        if (media.length === 0 || media.some((item, index) => item.ordinal !== index)) throw mismatch();
    }
    return media;
};

const requireImageParams = (value: unknown): void => {
    if (value === undefined) return;
    if (!isAtelierRecord(value)) throw mismatch();
    for (const [key, item] of Object.entries(value)) {
        if (!["size", "seed", "negative_prompt", "prompt_extend", "watermark"].includes(key)) throw mismatch();
        if ((key === "size" || key === "negative_prompt") && typeof item !== "string") throw mismatch();
        if (key === "seed" && (typeof item !== "number" || !Number.isSafeInteger(item))) throw mismatch();
        if ((key === "prompt_extend" || key === "watermark") && typeof item !== "boolean") throw mismatch();
    }
};

const requireAudioParams = (value: unknown): void => {
    if (value === undefined) return;
    if (!isAtelierRecord(value)) throw mismatch();
    for (const [key, item] of Object.entries(value)) {
        if (!["speech_rate", "pitch_rate", "volume", "instructions"].includes(key)) throw mismatch();
        if (key === "instructions" ? typeof item !== "string" : typeof item !== "number" || !Number.isFinite(item)) throw mismatch();
        if (typeof item === "number" && (item < (key === "volume" ? 0 : 0.5) || item > (key === "volume" ? 100 : 2))) throw mismatch();
    }
};

const explicitParamsMatchIntent = (requestParams: unknown, intentParams: unknown): boolean => {
    if (requestParams === undefined) return true;
    if (!isAtelierRecord(requestParams) || !isAtelierRecord(intentParams)) return false;
    return Object.entries(requestParams).every(([key, value]) => equal(intentParams[key], value));
};

const parseAdmissionMember = (raw: unknown): AtelierGenerationAdmissionMember => {
    if (!isAtelierRecord(raw) || !exactKeys(raw, ACK_BATCH_MEMBER_FIELDS)) throw mismatch();
    return {
        attempt: parseAtelierGenerationAttempt(raw.attempt),
        candidate: raw.candidate === null ? null : isAtelierRecord(raw.candidate) ? parseAtelierGenerationCandidate(raw.candidate) : mismatch(),
    };
};

const isVideoCandidateKind = (record: Record<string, unknown>): boolean => record.media_kind === undefined || record.media_kind === "video";

interface ParseAdmissionExpectation {
    nodeId?: string;
    request?: AtelierGenerationRequest;
    requestId?: string;
    creating?: boolean;
    allowBatch?: boolean;
}


export function assertAtelierGenerationAdmissionMatchesRequest(
    ack: AtelierGenerationAdmissionAck,
    request: AtelierGenerationRequest,
    expectation: { projectId?: string; nodeId?: string } = {},
): void {
    const expectedEntrypoint = entrypointForRequest(request);
    const expectedBatchSize = ("batch_size" in request ? request.batch_size : undefined) ?? 1;
    if (expectation.projectId !== undefined && ack.project.id !== expectation.projectId) throw mismatch();
    if (ack.receipt.project_id !== ack.project.id || ack.receipt.entrypoint !== expectedEntrypoint
        || ack.receipt.client_request_id !== request.client_request_id || ack.receipt.base_revision !== request.expected_revision
        || ack.members.length !== expectedBatchSize) throw mismatch();
    if (expectation.nodeId !== undefined && ack.receipt.node_id !== expectation.nodeId) throw mismatch();

    for (const member of ack.members) {
        const attempt = member.attempt;
        if (attempt.project_id !== ack.project.id || attempt.node_id !== ack.receipt.node_id) throw mismatch();
        const intent = attempt.provider_execution.intent;
        if (imageRequest(request)) {
            if (!mediaAttempt(attempt) || attempt.media_kind !== "image" || !isAtelierRecord(intent)
                || intent.model_id !== request.model_id || intent.mode !== request.mode || intent.prompt !== request.prompt
                || !explicitParamsMatchIntent(request.params, intent.params)) throw mismatch();
        } else if (audioRequest(request)) {
            if (!mediaAttempt(attempt) || attempt.media_kind !== "audio" || !isAtelierRecord(intent)
                || intent.intent_contract_version !== "provider-prepared-tts-v1" || !isAtelierRecord(intent.prepared_tts)) throw mismatch();
            const prepared = intent.prepared_tts;
            if (!isAtelierRecord(prepared.parameters) || prepared.parameters.voice !== request.voice || prepared.parameters.text !== request.text
                || !explicitParamsMatchIntent(request.params, prepared.parameters)) throw mismatch();
        } else {
            if (!isAtelierRecord(intent) || intent.model_id !== request.model_id || intent.mode !== request.mode || intent.prompt !== request.prompt) throw mismatch();
            if (!explicitParamsMatchIntent(request.params, intent.params)) throw mismatch();
            if (orderedRequest(request)) {
                if (!orderedAttempt(attempt) || attempt.input_contract_version !== request.contract_version
                    || !equal(attempt.input_media, request.media) || !providerMediaMatches(attempt.provider_execution.media, request.media)) throw mismatch();
            } else {
                if (orderedAttempt(attempt) || mediaAttempt(attempt) || !("first_frame" in request) || !("first_frame_source" in attempt)
                    || !equal(attempt.first_frame_source, request.first_frame)) throw mismatch();
            }
        }
    }
}

export function parseAtelierGenerationAdmissionAck(raw: unknown, projectId: string, expectation?: ParseAdmissionExpectation & { allowBatch?: false }): AtelierGenerationSingletonAdmissionAck;
export function parseAtelierGenerationAdmissionAck(raw: unknown, projectId: string, expectation: ParseAdmissionExpectation & { allowBatch: true }): AtelierGenerationAdmissionAck;
export function parseAtelierGenerationAdmissionAck(raw: unknown, projectId: string, expectation: ParseAdmissionExpectation = {}): AtelierGenerationAdmissionAck {
    if (!isAtelierRecord(raw) || typeof raw.replayed !== "boolean" || !Array.isArray(raw.new_candidate_ids)) throw mismatch();
    const project = assertAtelierProjectIdentity(parseAtelierProject(raw.project), projectId);
    const receipt = parseAtelierGenerationRequestReceipt(raw.receipt);
    const rawReason = raw.reason === undefined || raw.reason === null ? null : (() => { if (typeof raw.reason !== "string") throw mismatch(); return raw.reason; })();
    const newCandidateIds = raw.new_candidate_ids.map(item => {
        if (typeof item !== "string" || item.length === 0) throw mismatch();
        return item;
    });
    if (receipt.project_id !== projectId) throw mismatch();
    if (expectation.nodeId !== undefined && receipt.node_id !== expectation.nodeId) throw mismatch();
    if (expectation.requestId !== undefined && receipt.client_request_id !== expectation.requestId) throw mismatch();
    if (expectation.request) {
        if (receipt.client_request_id !== expectation.request.client_request_id || receipt.base_revision !== expectation.request.expected_revision) throw mismatch();
        assertAtelierProjectRevisionNotBefore(project, expectation.request.expected_revision);
    }
    if (expectation.request) {
        const expectedEntrypoint = entrypointForRequest(expectation.request);
        if (receipt.entrypoint !== expectedEntrypoint) throw mismatch();
    }
    const savedReceipt = project.generation_request_receipts.find(item => item.entrypoint === receipt.entrypoint && item.client_request_id === receipt.client_request_id);
    if (!savedReceipt || !equal(savedReceipt, receipt)) throw mismatch();

    const batchWire = receipt.schema_version === 2;
    if (batchWire) {
        if (!expectation.allowBatch || !exactKeys(raw, ACK_BATCH_FIELDS) || !Array.isArray(raw.members)) throw mismatch();
        const receiptMemberRecords = receiptMembers(receipt);
        const members = raw.members.map(parseAdmissionMember);
        if (members.length !== receiptMemberRecords.length) throw mismatch();
        members.forEach((member, index) => {
            const receiptMember = receiptMemberRecords[index];
            const savedAttempt = project.generation_attempts.find(item => item.attempt_id === member.attempt.attempt_id);
            const savedCandidate = candidateFromProject(project, member.attempt.node_id, member.attempt.candidate_id);
            if (!savedAttempt || !equal(savedAttempt, member.attempt) || !equal(savedCandidate, member.candidate)) throw mismatch();
            if (member.attempt.project_id !== projectId || member.attempt.node_id !== receipt.node_id
                || receiptMember.attempt_id !== member.attempt.attempt_id || receiptMember.candidate_id !== member.attempt.candidate_id
                || receiptMember.execution_fingerprint !== member.attempt.provider_execution.fingerprint) throw mismatch();
            if (entrypointForAttempt(member.attempt) !== receipt.entrypoint) throw mismatch();
            if (orderedAttempt(member.attempt) && !providerMediaMatches(member.attempt.provider_execution.media, member.attempt.input_media)) throw mismatch();
        });
        if (expectation.request && ("batch_size" in expectation.request ? expectation.request.batch_size : undefined) !== members.length) throw mismatch();
        const expectedNewIds = expectation.creating && !raw.replayed ? receiptMemberRecords.map(member => member.candidate_id) : [];
        if (!equal(newCandidateIds, expectedNewIds)) throw mismatch();
        if (expectedNewIds.length > 0 && members.some(member => member.candidate === null)) throw mismatch();
        const ack = { project, receipt: receipt as AtelierGenerationBatchAdmissionAck["receipt"], members, replayed: raw.replayed, new_candidate_ids: newCandidateIds, reason: rawReason };
        if (expectation.request) assertAtelierGenerationAdmissionMatchesRequest(ack, expectation.request, { projectId, nodeId: expectation.nodeId });
        return ack;
    }

    if (exactKeys(raw, ACK_BATCH_FIELDS) && Array.isArray(raw.members)) {
        const receiptMemberRecords = receiptMembers(receipt);
        const members = raw.members.map(parseAdmissionMember);
        if (members.length !== 1 || receiptMemberRecords.length !== 1) throw mismatch();
        const member = members[0];
        const receiptMember = receiptMemberRecords[0];
        const savedAttempt = project.generation_attempts.find(item => item.attempt_id === member.attempt.attempt_id);
        const savedCandidate = candidateFromProject(project, member.attempt.node_id, member.attempt.candidate_id);
        if (!savedAttempt || !equal(savedAttempt, member.attempt) || !equal(savedCandidate, member.candidate)) throw mismatch();
        if (member.attempt.project_id !== projectId || receipt.node_id !== member.attempt.node_id
            || receiptMember.attempt_id !== member.attempt.attempt_id || receiptMember.candidate_id !== member.attempt.candidate_id
            || receiptMember.execution_fingerprint !== member.attempt.provider_execution.fingerprint
            || entrypointForAttempt(member.attempt) !== receipt.entrypoint) throw mismatch();
        if (!mediaAttempt(member.attempt)) throw mismatch();
        if (expectation.request && ("batch_size" in expectation.request ? expectation.request.batch_size : undefined) !== undefined
            && (expectation.request as { batch_size?: number }).batch_size !== 1) throw mismatch();
        const expectedNewIds = expectation.creating && !raw.replayed ? [member.attempt.candidate_id] : [];
        if (!equal(newCandidateIds, expectedNewIds)) throw mismatch();
        if (expectedNewIds.length > 0 && member.candidate === null) throw mismatch();
        const ack = { project, receipt: receipt as AtelierGenerationSingletonAdmissionAck["receipt"], members: members as [AtelierGenerationAdmissionMember], attempt: member.attempt, candidate: member.candidate, replayed: raw.replayed, new_candidate_ids: newCandidateIds, reason: rawReason };
        if (expectation.request) assertAtelierGenerationAdmissionMatchesRequest(ack, expectation.request, { projectId, nodeId: expectation.nodeId });
        return ack;
    }

    if (!exactKeys(raw, ACK_SINGLE_FIELDS)) throw mismatch();
    const attempt = parseAtelierGenerationAttempt(raw.attempt);
    const candidate = raw.candidate === null ? null : isAtelierRecord(raw.candidate) ? parseAtelierGenerationCandidate(raw.candidate) : mismatch();
    const receiptMember = receiptMembers(receipt)[0];
    const savedAttempt = project.generation_attempts.find(item => item.attempt_id === attempt.attempt_id);
    const savedCandidate = candidateFromProject(project, attempt.node_id, attempt.candidate_id);
    if (!savedAttempt || !equal(savedAttempt, attempt) || !equal(savedCandidate, candidate)) throw mismatch();
    if (attempt.project_id !== projectId || receipt.node_id !== attempt.node_id || receiptMember.attempt_id !== attempt.attempt_id
        || receiptMember.candidate_id !== attempt.candidate_id || receiptMember.execution_fingerprint !== attempt.provider_execution.fingerprint) throw mismatch();
    if (entrypointForAttempt(attempt) !== receipt.entrypoint) throw mismatch();
    if (orderedAttempt(attempt) && !providerMediaMatches(attempt.provider_execution.media, attempt.input_media)) throw mismatch();
    if (expectation.request && ("batch_size" in expectation.request ? expectation.request.batch_size : undefined) !== undefined
        && (expectation.request as { batch_size?: number }).batch_size !== 1) throw mismatch();
    const expectedNewIds = expectation.creating && !raw.replayed ? [attempt.candidate_id] : [];
    if (!equal(newCandidateIds, expectedNewIds)) throw mismatch();
    if (expectedNewIds.length > 0 && candidate === null) throw mismatch();
    const ack = { project, receipt: receipt as AtelierGenerationSingletonAdmissionAck["receipt"], members: [{ attempt, candidate }] as [AtelierGenerationAdmissionMember], attempt, candidate, replayed: raw.replayed, new_candidate_ids: newCandidateIds, reason: rawReason };
    if (expectation.request) assertAtelierGenerationAdmissionMatchesRequest(ack, expectation.request, { projectId, nodeId: expectation.nodeId });
    return ack;
}

export function parseAtelierGenerationAttemptAck(raw: unknown, projectId: string, expectation: {
    attemptId?: string;
    candidateId?: string;
    expectedAttemptRevision?: number;
    executionFingerprint?: string;
} = {}): AtelierGenerationAttemptAck {
    if (!isAtelierRecord(raw)) throw mismatch();
    const project = assertAtelierProjectIdentity(parseAtelierProject(raw.project), projectId);
    const attempt = parseAtelierGenerationAttempt(raw.attempt);
    const candidate = raw.candidate === null ? null : isAtelierRecord(raw.candidate) ? parseAtelierGenerationCandidate(raw.candidate) : mismatch();
    const savedAttempt = project.generation_attempts.find(item => item.attempt_id === attempt.attempt_id);
    const savedCandidate = candidateFromProject(project, attempt.node_id, attempt.candidate_id);
    if (!savedAttempt || !equal(savedAttempt, attempt) || !equal(savedCandidate, candidate)) throw mismatch();
    if (expectation.attemptId !== undefined && attempt.attempt_id !== expectation.attemptId) throw mismatch();
    if (expectation.candidateId !== undefined && attempt.candidate_id !== expectation.candidateId) throw mismatch();
    if (expectation.expectedAttemptRevision !== undefined && attempt.attempt_revision < expectation.expectedAttemptRevision) throw mismatch();
    if (expectation.executionFingerprint !== undefined && attempt.provider_execution.fingerprint !== expectation.executionFingerprint) throw mismatch();
    return { project, attempt, candidate };
}

const entrypointForAttempt = (attempt: AtelierGenerationAdmissionMember["attempt"]): string => mediaAttempt(attempt)
    ? attempt.media_kind === "image" ? IMAGE_ENTRYPOINT : AUDIO_ENTRYPOINT
    : orderedAttempt(attempt) ? ORDERED_ENTRYPOINT : LEGACY_ENTRYPOINT;

const requireVideoCandidateRequest = (request: AtelierGenerationRequest): AtelierGenerationRequest => {
    requireAtelierRevisionInput(request.expected_revision);
    requireRequestId(request.client_request_id);
    if (imageRequest(request) || audioRequest(request)) throw mismatch();
    if (orderedRequest(request)) {
        if (request.contract_version !== ORDERED_MEDIA_CONTRACT || !ORDERED_MODELS.has(request.model_id)
            || (request.mode !== "t2v" && request.mode !== "i2v" && request.mode !== "r2v") || typeof request.prompt !== "string") throw mismatch();
        requireOrderedMedia(request.media, request.mode);
        requireOrderedVideoParams(request.model_id, request.params);
        if (Object.prototype.hasOwnProperty.call(request, "batch_size")) {
            const batchSize = (request as { batch_size?: unknown }).batch_size;
            if (!Number.isSafeInteger(batchSize) || (batchSize as number) < 1 || (batchSize as number) > 6) throw mismatch();
        }
        const keys = Object.keys(request);
        if (keys.some(key => !["expected_revision", "client_request_id", "contract_version", "model_id", "mode", "prompt", "media", "params", "batch_size"].includes(key))) throw mismatch();
        return request;
    }
    if (!("first_frame" in request) || request.model_id !== "wan/wan2.7-video#i2v" || request.mode !== "i2v" || typeof request.prompt !== "string") throw mismatch();
    if (!isAtelierRecord(request.first_frame) || !exactKeys(request.first_frame, ["node_id", "media_url"])
        || typeof request.first_frame.node_id !== "string" || !request.first_frame.node_id.trim()
        || typeof request.first_frame.media_url !== "string" || !request.first_frame.media_url.trim()) throw mismatch();
    requireLegacyVideoParams(request.params);
    if (Object.prototype.hasOwnProperty.call(request, "batch_size")) {
        const batchSize = (request as { batch_size?: unknown }).batch_size;
        if (!Number.isSafeInteger(batchSize) || (batchSize as number) < 1 || (batchSize as number) > 6) throw mismatch();
    }
    const keys = Object.keys(request);
    if (keys.some(key => !["expected_revision", "client_request_id", "model_id", "mode", "prompt", "first_frame", "params", "batch_size"].includes(key))) throw mismatch();
    return request;
};

const requireImageCandidateRequest = (request: AtelierImageCandidateRequest): AtelierImageCandidateRequest => {
    requireAtelierRevisionInput(request.expected_revision);
    requireRequestId(request.client_request_id);
    if (request.entrypoint !== IMAGE_ENTRYPOINT || typeof request.model_id !== "string" || !request.model_id
        || (request.mode !== "t2i" && request.mode !== "i2i") || typeof request.prompt !== "string") throw mismatch();
    const media = requireImageRequestMedia(request.media, request.mode);
    requireImageParams(request.params);
    if (Object.prototype.hasOwnProperty.call(request, "batch_size")) {
        const batchSize = (request as { batch_size?: unknown }).batch_size;
        if (!Number.isSafeInteger(batchSize) || (batchSize as number) < 1 || (batchSize as number) > 6) throw mismatch();
    }
    const keys = Object.keys(request);
    if (keys.some(key => !["entrypoint", "expected_revision", "client_request_id", "model_id", "mode", "prompt", "media", "params", "batch_size"].includes(key))) throw mismatch();
    return { ...request, media };
};

const requireAudioCandidateRequest = (request: AtelierAudioCandidateRequest): AtelierAudioCandidateRequest => {
    requireAtelierRevisionInput(request.expected_revision);
    requireRequestId(request.client_request_id);
    if (request.entrypoint !== AUDIO_ENTRYPOINT || typeof request.voice !== "string" || !request.voice.trim()
        || typeof request.text !== "string" || !request.text.trim()) throw mismatch();
    requireAudioParams(request.params);
    const keys = Object.keys(request);
    if (keys.some(key => !["entrypoint", "expected_revision", "client_request_id", "voice", "text", "params"].includes(key))) throw mismatch();
    return request;
};

const stripFrontendEntrypoint = <T extends AtelierImageCandidateRequest | AtelierAudioCandidateRequest>(request: T): Omit<T, "entrypoint"> => {
    const payload = { ...request };
    delete (payload as { entrypoint?: unknown }).entrypoint;
    return payload;
};

const requireVisibilityInput = (input: SetAtelierGenerationCandidateVisibilityInput): SetAtelierGenerationCandidateVisibilityInput => {
    const expectedRevision = requireAtelierRevisionInput(input.expected_revision);
    if (typeof input.visible !== "boolean") throw mismatch();
    const keys = Object.keys(input as unknown as Record<string, unknown>);
    if (keys.some(key => !["expected_revision", "visible"].includes(key))) throw mismatch();
    return { expected_revision: expectedRevision, visible: input.visible };
};

const requireOrderedDraftMedia = (value: unknown): AtelierOrderedMediaSelection[] => {
    if (!Array.isArray(value)) throw mismatch();
    const media = value.map(item => {
        if (!isAtelierRecord(item) || !exactKeys(item, ["role", "ordinal", "node_id", "media_url"])) throw mismatch();
        if (!Number.isSafeInteger(item.ordinal) || (item.ordinal as number) < 0
            || typeof item.node_id !== "string" || !item.node_id.trim()
            || typeof item.media_url !== "string" || !item.media_url.trim()) throw mismatch();
        return { role: requireOrderedMediaRole(item.role), ordinal: item.ordinal as number, node_id: item.node_id, media_url: item.media_url };
    });
    const seen = new Set<string>();
    const byRole = new Map<AtelierOrderedMediaRole, number[]>();
    for (const item of media) {
        const key = `${item.role}:${item.ordinal}`;
        if (seen.has(key)) throw mismatch();
        seen.add(key);
        if ((item.role === "first_frame" || item.role === "last_frame") && item.ordinal !== 0) throw mismatch();
        byRole.set(item.role, [...(byRole.get(item.role) ?? []), item.ordinal]);
    }
    for (const ordinals of Array.from(byRole.values())) {
        if (ordinals.some((ordinal, index) => ordinal !== index)) throw mismatch();
    }
    if (!hasCanonicalAtelierOrderedMediaRoleOrder(media)) throw mismatch();
    const roles = media.map(item => item.role);
    const imageCount = roles.filter(role => role === "first_frame" || role === "last_frame" || role === "reference_image").length;
    const videoCount = roles.filter(role => role === "reference_video").length;
    const audioCount = roles.filter(role => role === "reference_audio").length;
    if (imageCount > 10 || videoCount > 5 || audioCount > 5 || media.length > 20) throw mismatch();
    return media;
};

const requireOrderedDraftInput = (input: SetAtelierGenerationDraftInput, expectedRevision: number): SetAtelierOrderedGenerationDraftInput => {
    if (!("contract_version" in input) || input.contract_version !== "ordered-media-v1"
        || !ORDERED_MODELS.has(input.model_id) || (input.mode !== "t2v" && input.mode !== "i2v" && input.mode !== "r2v")
        || typeof input.prompt !== "string" || !isAtelierRecord(input.params)) throw mismatch();
    requireOrderedVideoParams(input.model_id, input.params);
    const media = requireOrderedDraftMedia(input.media);
    const keys = Object.keys(input as unknown as Record<string, unknown>);
    if (keys.some(key => !["expected_revision", "contract_version", "prompt", "model_id", "mode", "params", "media", "batch_size"].includes(key))) throw mismatch();
    if (Object.prototype.hasOwnProperty.call(input, "batch_size")
        && (!Number.isSafeInteger(input.batch_size) || (input.batch_size as number) < 1 || (input.batch_size as number) > 6)) throw mismatch();
    return {
        expected_revision: expectedRevision,
        contract_version: "ordered-media-v1",
        prompt: input.prompt,
        model_id: input.model_id as AtelierOrderedVideoModel,
        mode: input.mode as AtelierOrderedVideoMode,
        params: input.params,
        media,
        ...(Object.prototype.hasOwnProperty.call(input, "batch_size") ? { batch_size: input.batch_size } : {}),
    };
};

const requireGenerationDraftInput = (input: SetAtelierGenerationDraftInput): SetAtelierGenerationDraftInput => {
    const expectedRevision = requireAtelierRevisionInput(input.expected_revision);
    if ("contract_version" in input) return requireOrderedDraftInput(input, expectedRevision);
    if (typeof input.prompt !== "string" || input.model_id !== "wan/wan2.7-video#i2v" || input.mode !== "i2v") throw mismatch();
    requireLegacyVideoParams(input.params);
    if (!isAtelierRecord(input.params)) throw mismatch();
    const keys = Object.keys(input as unknown as Record<string, unknown>);
    if (keys.some(key => !["expected_revision", "prompt", "model_id", "mode", "params"].includes(key))) throw mismatch();
    return { expected_revision: expectedRevision, prompt: input.prompt, model_id: input.model_id, mode: input.mode, params: input.params };
};

export const parseAtelierGenerationDraft = (raw: unknown): AtelierGenerationDraft => {
    if (!isAtelierRecord(raw)) throw mismatch();
    if (raw.schema_version === 1) {
        if (!exactKeys(raw, ["schema_version", "model_id", "mode", "params"]) || raw.model_id !== "wan/wan2.7-video#i2v" || raw.mode !== "i2v") throw mismatch();
        requireLegacyVideoParams(raw.params);
        if (!isAtelierRecord(raw.params)) throw mismatch();
        return { schema_version: 1, model_id: raw.model_id, mode: raw.mode, params: raw.params };
    }
    if (raw.schema_version === 2) {
        if (!exactKeys(raw, ["schema_version", "input_contract_version", "model_id", "mode", "params", "media", "batch_size"])
            || raw.input_contract_version !== "ordered-media-v1" || typeof raw.model_id !== "string" || !ORDERED_MODELS.has(raw.model_id)
            || (raw.mode !== "t2v" && raw.mode !== "i2v" && raw.mode !== "r2v") || !isAtelierRecord(raw.params)) throw mismatch();
        requireOrderedVideoParams(raw.model_id, raw.params);
        const media = requireOrderedDraftMedia(raw.media);
        if (!Number.isSafeInteger(raw.batch_size) || (raw.batch_size as number) < 1 || (raw.batch_size as number) > 6) throw mismatch();
        return {
            schema_version: 2,
            input_contract_version: "ordered-media-v1",
            model_id: raw.model_id as AtelierOrderedVideoModel,
            mode: raw.mode as AtelierOrderedVideoMode,
            params: raw.params,
            media,
            batch_size: raw.batch_size as number,
        };
    }
    throw mismatch();
};

const expectedDraftFromPayload = (payload: SetAtelierGenerationDraftInput): AtelierGenerationDraft => "contract_version" in payload ? {
    schema_version: 2,
    input_contract_version: "ordered-media-v1",
    model_id: payload.model_id,
    mode: payload.mode,
    params: payload.params,
    media: payload.media,
    batch_size: payload.batch_size ?? 1,
} : {
    schema_version: 1,
    model_id: payload.model_id,
    mode: payload.mode,
    params: payload.params,
};

const requireAttemptIdentity = (request: AtelierGenerationAttemptIdentityRequest): AtelierGenerationAttemptIdentityRequest => {
    idPart(request.candidate_id);
    requireAtelierRevisionInput(request.expected_attempt_revision);
    requireFingerprint(request.execution_fingerprint);
    const keys = Object.keys(request);
    if (keys.some(key => !["candidate_id", "expected_attempt_revision", "execution_fingerprint"].includes(key))) throw mismatch();
    return request;
};

export const atelierGenerationApi = {
    async createVideoCandidate(projectId: string, nodeId: string, request: AtelierGenerationRequest): Promise<AtelierGenerationAdmissionAck> {
        const payload = requireVideoCandidateRequest(request);
        return parseAtelierGenerationAdmissionAck(await requestAtelierJson(`${nodePath(projectId, nodeId)}/video_candidates`, {
            method: "POST",
            body: jsonBody(payload as unknown as Record<string, unknown>),
        }), projectId, { nodeId, request: payload, creating: true, allowBatch: true });
    },

    async createImageCandidate(projectId: string, nodeId: string, request: AtelierImageCandidateRequest): Promise<AtelierGenerationAdmissionAck> {
        const payload = requireImageCandidateRequest(request);
        return parseAtelierGenerationAdmissionAck(await requestAtelierJson(`${nodePath(projectId, nodeId)}/image_candidates`, {
            method: "POST",
            body: jsonBody(stripFrontendEntrypoint(payload) as unknown as Record<string, unknown>),
        }), projectId, { nodeId, request: payload, creating: true, allowBatch: true });
    },

    async createAudioCandidate(projectId: string, nodeId: string, request: AtelierAudioCandidateRequest): Promise<AtelierGenerationAdmissionAck> {
        const payload = requireAudioCandidateRequest(request);
        return parseAtelierGenerationAdmissionAck(await requestAtelierJson(`${nodePath(projectId, nodeId)}/audio_candidates`, {
            method: "POST",
            body: jsonBody(stripFrontendEntrypoint(payload) as unknown as Record<string, unknown>),
        }), projectId, { nodeId, request: payload, creating: true, allowBatch: true });
    },

    async getRequest(projectId: string, clientRequestId: string, originalRequest?: AtelierGenerationRequest): Promise<AtelierGenerationAdmissionAck> {
        requireRequestId(clientRequestId);
        return parseAtelierGenerationAdmissionAck(
            await requestAtelierJson(`${generationPath(projectId)}/requests/${encodeURIComponent(clientRequestId)}`, { method: "GET" }),
            projectId,
            { requestId: clientRequestId, request: originalRequest, allowBatch: true },
        );
    },

    async getAttempt(projectId: string, attemptId: string): Promise<AtelierGenerationAttemptAck> {
        return parseAtelierGenerationAttemptAck(await requestAtelierJson(`${generationPath(projectId)}/attempts/${idPart(attemptId)}`, { method: "GET" }), projectId, { attemptId });
    },

    async setGenerationDraft(projectId: string, nodeId: string, input: SetAtelierGenerationDraftInput): Promise<AtelierNodeCommandAck> {
        const payload = requireGenerationDraftInput(input);
        const ack = parseAtelierNodeCommandAck(await requestAtelierJson(`${nodePath(projectId, nodeId)}/generation_draft`, {
            method: "PUT",
            body: jsonBody(payload as unknown as Record<string, unknown>),
        }), { projectId, expectedRevision: payload.expected_revision, expectedIds: [nodeId] });
        const node = ack.project.nodes.find(item => item.id === nodeId);
        const draft = parseAtelierGenerationDraft(node?.data.generation_draft);
        if (!node || node.prompt !== payload.prompt || !equal(draft, expectedDraftFromPayload(payload))) throw mismatch();
        return ack;
    },

    async continueAttempt(projectId: string, attemptId: string, request: AtelierGenerationAttemptIdentityRequest): Promise<AtelierGenerationAttemptAck> {
        const payload = requireAttemptIdentity(request);
        return parseAtelierGenerationAttemptAck(await requestAtelierJson(`${generationPath(projectId)}/attempts/${idPart(attemptId)}/continue`, {
            method: "POST",
            body: jsonBody(payload as unknown as Record<string, unknown>),
        }), projectId, {
            attemptId,
            candidateId: payload.candidate_id,
            expectedAttemptRevision: payload.expected_attempt_revision,
            executionFingerprint: payload.execution_fingerprint,
        });
    },

    async cancelAttempt(projectId: string, attemptId: string, request: AtelierGenerationAttemptIdentityRequest): Promise<AtelierGenerationAttemptAck> {
        const payload = requireAttemptIdentity(request);
        return parseAtelierGenerationAttemptAck(await requestAtelierJson(`${generationPath(projectId)}/attempts/${idPart(attemptId)}/cancel`, {
            method: "POST",
            body: jsonBody(payload as unknown as Record<string, unknown>),
        }), projectId, {
            attemptId,
            candidateId: payload.candidate_id,
            expectedAttemptRevision: payload.expected_attempt_revision,
            executionFingerprint: payload.execution_fingerprint,
        });
    },


    async setCandidateVisibility(
        projectId: string,
        nodeId: string,
        candidateId: string,
        input: SetAtelierGenerationCandidateVisibilityInput,
    ): Promise<AtelierNodeCommandAck> {
        const payload = requireVisibilityInput(input);
        const ack = parseAtelierNodeCommandAck(await requestAtelierJson(`${nodePath(projectId, nodeId)}/video_candidates/${idPart(candidateId)}/visibility`, {
            method: "POST",
            body: jsonBody({ expected_revision: payload.expected_revision, visible: payload.visible }),
        }), { projectId, expectedRevision: payload.expected_revision, expectedIds: [nodeId] });
        const node = ack.project.nodes.find(item => item.id === nodeId);
        const found = candidateBucketFromProject(ack.project, nodeId, candidateId);
        if (!node || !found || found.bucket !== (payload.visible ? "visible" : "hidden")) throw mismatch();
        if (!isVideoCandidateKind(found.record)) throw mismatch();
        if (node.data.selected_candidate_id === candidateId) throw mismatch();
        if (typeof found.record.video_url === "string" && node.media_urls.includes(found.record.video_url)) {
            const selected = typeof node.data.selected_candidate_id === "string"
                ? candidateBucketFromProject(ack.project, nodeId, node.data.selected_candidate_id) : null;
            if (!selected || selected.bucket !== "visible" || selected.record.status !== "completed"
                || selected.record.video_url !== found.record.video_url || node.media_urls.length !== 1) throw mismatch();
        }
        return ack;
    },
    async selectCandidate(
        projectId: string,
        nodeId: string,
        candidateId: string,
        input: SelectAtelierGenerationCandidateInput,
    ): Promise<AtelierNodeCommandAck> {
        const expectedRevision = requireAtelierRevisionInput(input.expected_revision);
        const ack = parseAtelierNodeCommandAck(await requestAtelierJson(`${nodePath(projectId, nodeId)}/video_candidates/${idPart(candidateId)}/select`, {
            method: "POST",
            body: jsonBody({ expected_revision: expectedRevision }),
        }), { projectId, expectedRevision, expectedIds: [nodeId] });
        const node = ack.project.nodes.find(item => item.id === nodeId);
        const selected = node?.data.selected_candidate_id;
        if (selected !== candidateId) throw mismatch();
        if (candidateBucketFromProject(ack.project, nodeId, candidateId)?.bucket !== "visible") throw mismatch();
        const candidate = candidateFromProject(ack.project, nodeId, candidateId);
        const importedCandidate = candidate ? null : importedCandidateFromProject(ack.project, nodeId, candidateId);
        if (candidate && candidate.media_kind !== "video") throw mismatch();
        const videoUrl = candidate?.video_url ?? importedCandidate?.video_url ?? null;
        if (!node || candidate?.status === "completed" && candidate.video_url === null) throw mismatch();
        if (!videoUrl || (candidate && candidate.status !== "completed")
            || node.media_urls.length !== 1 || node.media_urls[0] !== videoUrl) throw mismatch();
        return ack;
    },
};
