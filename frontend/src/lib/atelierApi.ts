import { API_URL } from "@/lib/apiUrl";
import { appAccessFetch } from "@/lib/appAccess";
import { hasCanonicalAtelierOrderedMediaRoleOrder, isAtelierOrderedMediaRole } from "@/lib/atelierOrderedMedia";
import { isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type {
    AtelierAgentPolicy,
    AtelierAudioTrack,
    AtelierCandidateBranchSource,
    AtelierCandidateJudgment,
    AtelierExportDeletionReceipt,
    AtelierExportRecord,
    AtelierImportedExportProof,
    AtelierImportProvenance,
    AtelierImportedAgentHistoryEntry,
    AtelierGenerationAttempt,
    AtelierAgentApprovalRecord,
    AtelierAgentGenerationBinding,
    AtelierAgentCanvasBinding,
    AtelierAgentLoopStartContext,
    AtelierGenerationAuthorizationRef,
    AtelierGenerationAttemptStatus,
    AtelierGenerationCandidateProjection,
    AtelierGenerationFirstFrameSource,
    AtelierGenerationRequestReceipt,
    AtelierGenerationEntrypoint,
    AtelierOrderedMediaRole,
    AtelierOrderedMediaSelection,
    AtelierGenerationRequestReceiptMember,
    AtelierNode,
    AtelierNodeUploadReceipt,
    AtelierUploadedMediaProof,
    AtelierNodeDeleteAudioTracksChange,
    AtelierNodeDeleteDataChangeGroup,
    AtelierNodeDeleteFieldValue,
    AtelierNodeDeleteReceipt,
    AtelierNodeDeleteSequenceChange,
    AtelierNodeDeleteSnapshot,
    AtelierNodeDeleteSourceProof,
    AtelierNodeRestoreSkip,
    AtelierProductSkillSnapshot,
    AtelierProject,
    AtelierSequenceEntry,
    AtelierSequenceExportJob,
    AtelierSequenceExportJobStatus,
    AtelierSequencePublicationProof,
    AtelierSequenceTransition,
    CreateAtelierProjectInput,
    DeleteAtelierProjectInput,
    DeleteAtelierProjectResult,
    JsonRecord,
    JsonValue,
    ProviderExecutionPhase,
    ProviderExecutionRecord,
    UpdateAtelierProjectInput,
} from "@/lib/atelierTypes";

export type {
    AtelierAgentPolicy,
    AtelierAudioTrack,
    AtelierCandidateBranchSource,
    AtelierCandidateJudgment,
    AtelierExportRecord,
    AtelierGenerationAttempt,
    AtelierGenerationAuthorizationRef,
    AtelierGenerationCandidateProjection,
    AtelierGenerationRequestReceipt,
    AtelierGenerationEntrypoint,
    AtelierOrderedMediaRole,
    AtelierOrderedMediaSelection,
    AtelierNode,
    AtelierNodeDeleteAudioTracksChange,
    AtelierNodeDeleteDataChangeGroup,
    AtelierNodeDeleteFieldValue,
    AtelierNodeDeleteReceipt,
    AtelierNodeDeleteSequenceChange,
    AtelierNodeDeleteSnapshot,
    AtelierNodeDeleteSourceProof,
    AtelierNodeRestoreSkip,
    AtelierProductSkillSnapshot,
    AtelierProject,
    AtelierSequenceEntry,
    AtelierSequenceExportJob,
    AtelierSequenceExportJobStatus,
    AtelierSequencePublicationProof,
    AtelierSequenceTransition,
    CreateAtelierProjectInput,
    DeleteAtelierProjectInput,
    DeleteAtelierProjectResult,
    JsonRecord,
    JsonValue,
    ProviderExecutionRecord,
    UpdateAtelierProjectInput,
} from "@/lib/atelierTypes";

export class AtelierProjectApiError extends Error {
    constructor(
        message: string,
        public readonly code: string = "atelier_project_api_error",
        public readonly status?: number,
    ) {
        super(message);
        this.name = "AtelierProjectApiError";
    }
}

const ATELIER_PROJECTS_PATH = "/atelier/projects";

const isRecord = (value: unknown): value is Record<string, unknown> => (
    typeof value === "object" && value !== null && !Array.isArray(value)
);

const hasOwn = (value: object, key: string): boolean => Object.prototype.hasOwnProperty.call(value, key);

const hasExactKeys = (value: Record<string, unknown>, expected: readonly string[]): boolean => {
    const keys = Object.keys(value);
    return keys.length === expected.length && keys.every(key => expected.includes(key));
};

const isPlainRecord = (value: unknown): value is Record<string, unknown> => {
    if (!isRecord(value)) return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
};

const isJsonValue = (value: unknown, seen = new Set<object>()): value is JsonValue => {
    if (value === null) return true;
    const valueType = typeof value;
    if (valueType === "string" || valueType === "boolean") return true;
    if (valueType === "number") return Number.isFinite(value);
    if (valueType !== "object") return false;
    const objectValue = value as object;
    if (seen.has(objectValue)) return false;
    seen.add(objectValue);
    if (Array.isArray(value)) {
        return value.every(item => isJsonValue(item, seen));
    }
    if (!isPlainRecord(value)) return false;
    return Object.values(value).every(item => isJsonValue(item, seen));
};

const requireString = (value: unknown, code: string): string => {
    if (typeof value !== "string") {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return value;
};

const requireNullableString = (value: unknown, code: string): string | null => {
    if (value === null || typeof value === "string") return value;
    throw new AtelierProjectApiError("Invalid Atelier project response", code);
};

const requireFiniteNumber = (value: unknown, code: string): number => {
    if (typeof value !== "number" || !Number.isFinite(value)) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return value;
};

const requirePositiveFiniteNumber = (value: unknown, code: string): number => {
    const number = requireFiniteNumber(value, code);
    if (number <= 0) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return number;
};

const requireNonnegativeFiniteNumber = (value: unknown, code: string): number => {
    const number = requireFiniteNumber(value, code);
    if (number < 0) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return number;
};

const requireBoundedFiniteNumber = (value: unknown, min: number, max: number, code: string): number => {
    const number = requireFiniteNumber(value, code);
    if (number < min || number > max) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return number;
};

const requireNonnegativeInteger = (value: unknown, code: string): number => {
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return value;
};

const requirePositiveInteger = (value: unknown, code: string): number => {
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return value;
};

const requireNonemptyString = (value: unknown, code: string): string => {
    const text = requireString(value, code);
    if (!text) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return text;
};

const requireStringArray = (value: unknown, code: string): string[] => {
    if (!Array.isArray(value) || !value.every(item => typeof item === "string")) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return [...value];
};

const requireBoolean = (value: unknown, code: string): boolean => {
    if (typeof value !== "boolean") {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return value;
};

const requireJsonRecord = (value: unknown, code: string): JsonRecord => {
    if (!isPlainRecord(value) || !isJsonValue(value)) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return { ...value } as JsonRecord;
};

const requireJsonRecordArray = (value: unknown, code: string): JsonRecord[] => {
    if (!Array.isArray(value)) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return value.map(item => requireJsonRecord(item, code));
};

const requireRevisionInput = (value: unknown): number => {
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
        throw new AtelierProjectApiError("Invalid Atelier project revision", "invalid_atelier_project_revision");
    }
    return value;
};

const requireProjectIdInput = (projectId: string): string => {
    if (typeof projectId !== "string" || projectId.length === 0) {
        throw new AtelierProjectApiError("Invalid Atelier project id", "invalid_atelier_project_id");
    }
    return projectId;
};

const parsePolicy = (value: unknown): AtelierAgentPolicy => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier project response", "invalid_atelier_project");
    }
    const approvalMode = value.approval_mode;
    if (approvalMode !== "untrusted" && approvalMode !== "on_failure" && approvalMode !== "on_request" && approvalMode !== "never") {
        throw new AtelierProjectApiError("Invalid Atelier project response", "invalid_atelier_project");
    }
    return {
        approval_mode: approvalMode,
        allowed_tools: requireStringArray(value.allowed_tools, "invalid_atelier_project"),
        max_nodes_per_action: requirePositiveInteger(value.max_nodes_per_action, "invalid_atelier_project"),
        max_tokens_per_turn: requirePositiveInteger(value.max_tokens_per_turn, "invalid_atelier_project"),
        enable_reviewer: requireBoolean(value.enable_reviewer, "invalid_atelier_project"),
        max_generation_cost_per_turn: requireFiniteNumber(value.max_generation_cost_per_turn, "invalid_atelier_project"),
        updated_at: requireFiniteNumber(value.updated_at, "invalid_atelier_project"),
    };
};

const GENERATION_HASH_RE = /^[0-9a-f]{64}$/;
const GENERATION_CLIENT_REQUEST_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const GENERATION_CANDIDATE_CONTRACT = "atelier-provider-video-v1";
const MEDIA_GENERATION_CANDIDATE_CONTRACT = "atelier-provider-media-v2";
const GENERATION_ENTRYPOINT = "atelier.provider_video.v1";
const ORDERED_GENERATION_ENTRYPOINT = "atelier.provider_video.v2";
const IMAGE_GENERATION_ENTRYPOINT = "atelier.provider_image.v1";
const AUDIO_GENERATION_ENTRYPOINT = "atelier.provider_audio.v1";
const GENERATION_ENTRYPOINTS = new Set([GENERATION_ENTRYPOINT, ORDERED_GENERATION_ENTRYPOINT, IMAGE_GENERATION_ENTRYPOINT, AUDIO_GENERATION_ENTRYPOINT]);
const ORDERED_MEDIA_CONTRACT_VERSION = "ordered-media-v1";
const GENERATION_REQUEST_CONTRACT = "request-json-v1";
const AGENT_GENERATION_TOOL_NAME = "generation.createVideoCandidates";
const GENERATION_CANDIDATE_FIELDS = [
    "execution_contract", "id", "attempt_id", "media_kind", "status", "video_url", "error_code", "created_at", "updated_at",
] as const;
const MEDIA_GENERATION_CANDIDATE_FIELDS = [
    "schema_version", "execution_contract", "id", "media_kind", "status", "media_url", "error_code", "authority", "attempt_id", "execution_fingerprint",
] as const;
const PROVIDER_EXECUTION_FIELDS = [
    "schema_version", "attempt_id", "phase", "provider_model_id", "intent", "media", "scope", "credential_ref",
    "fingerprint", "handle", "observation", "prepared_artifact", "artifact",
] as const;
const PROVIDER_SYNC_EXECUTION_FIELDS = [
    "schema_version", "execution_kind", "media_kind", "output_ref", "attempt_id", "phase", "provider_model_id", "intent", "media", "scope", "credential_ref",
    "fingerprint", "handle", "observation", "prepared_artifact", "artifact",
] as const;
const LEGACY_GENERATION_ATTEMPT_FIELDS = [
    "schema_version", "project_id", "node_id", "candidate_id", "attempt_id", "attempt_revision", "status", "error_code",
    "first_frame_source", "output_ref", "provider_execution", "created_at", "updated_at",
] as const;
const AGENT_LEGACY_GENERATION_ATTEMPT_FIELDS = [...LEGACY_GENERATION_ATTEMPT_FIELDS, "agent_authorization_ref"] as const;
const ORDERED_GENERATION_ATTEMPT_FIELDS = [
    "schema_version", "project_id", "node_id", "candidate_id", "attempt_id", "attempt_revision", "status", "error_code",
    "input_contract_version", "input_media", "output_ref", "provider_execution", "created_at", "updated_at",
] as const;
const AGENT_ORDERED_GENERATION_ATTEMPT_FIELDS = [...ORDERED_GENERATION_ATTEMPT_FIELDS, "agent_authorization_ref"] as const;
const MEDIA_GENERATION_ATTEMPT_FIELDS = [
    "schema_version", "execution_contract", "project_id", "node_id", "candidate_id", "attempt_id", "attempt_revision", "media_kind",
    "status", "error_code", "output_ref", "provider_execution", "created_at", "updated_at",
] as const;
const CANDIDATE_BRANCH_SOURCE_FIELDS = [
    "schema_version", "origin_project_id", "origin_node_id", "origin_candidate_id", "media_kind", "media_url", "media_sha256", "media_size_bytes", "recipe_status",
] as const;
const CANDIDATE_JUDGMENT_FIELDS = [
    "schema_version", "id", "project_id", "node_id", "candidate_id", "media_kind", "verdict", "reason", "created_at",
] as const;
const CANDIDATE_JUDGMENT_FIELDS_WITH_SUPERSEDES = [...CANDIDATE_JUDGMENT_FIELDS, "supersedes_judgment_id"] as const;
const GENERATION_AUTHORIZATION_REF_FIELDS = [
    "schema_version", "project_id", "turn_id", "call_id", "approval_id", "tool_name", "tool_schema_version",
    "authorization_hash", "preview_fingerprint", "request_fingerprint", "server_storage_identity", "policy_snapshot_hash",
] as const;
const AGENT_GENERATION_BINDING_FIELDS = [
    "schema_version", "preview_public_record", "provider_execution", "agent_authorization_ref", "created_at",
] as const;
const AGENT_APPROVAL_RECORD_FIELDS = [
    "schema_version", "approval_id", "authorization_hash", "decision", "decided_at",
] as const;
const GENERATION_RECEIPT_V1_FIELDS = [
    "schema_version", "project_id", "entrypoint", "request_contract_version", "client_request_id", "request_fingerprint",
    "node_id", "candidate_id", "attempt_id", "execution_fingerprint", "base_revision", "committed_revision", "created_at",
] as const;
const GENERATION_RECEIPT_V2_FIELDS = [
    "schema_version", "project_id", "entrypoint", "request_contract_version", "client_request_id", "request_fingerprint",
    "node_id", "members", "base_revision", "committed_revision", "created_at",
] as const;
const GENERATION_RECEIPT_MEMBER_FIELDS = ["candidate_id", "attempt_id", "execution_fingerprint"] as const;
const PROVIDER_ARTIFACT_FIELDS = ["schema_version", "attempt_id", "handle", "relative_path", "sha256", "size_bytes"] as const;
const PROVIDER_SYNC_ARTIFACT_FIELDS = ["schema_version", "contract_version", "attempt_id", "media_kind", "relative_path", "output_ref", "sha256", "size_bytes"] as const;
const GENERATION_PENDING_STATUSES = new Set(["pending", "processing", "needs_confirmation"]);
const GENERATION_TERMINAL_STATUSES = new Set(["failed", "cancelled", "completed"]);

const generationInvalid = (): never => {
    throw new AtelierProjectApiError("Invalid Atelier generation ledger", "invalid_atelier_generation_ledger");
};

const branchSourceInvalid = (): never => {
    throw new AtelierProjectApiError("Invalid Atelier candidate branch source", "invalid_atelier_candidate_branch_source");
};

const judgmentInvalid = (): never => {
    throw new AtelierProjectApiError("Invalid Atelier candidate judgment history", "invalid_atelier_candidate_judgments");
};

const requireGenerationHash = (value: unknown): string => {
    if (typeof value !== "string" || !GENERATION_HASH_RE.test(value)) return generationInvalid();
    return value;
};

const requireGenerationRequestId = (value: unknown): string => {
    if (typeof value !== "string" || !GENERATION_CLIENT_REQUEST_RE.test(value)) return generationInvalid();
    return value;
};

const parseGenerationStatus = (value: unknown): AtelierGenerationAttemptStatus => {
    if (value !== "pending" && value !== "processing" && value !== "needs_confirmation" && value !== "failed"
        && value !== "cancelled" && value !== "completed") return generationInvalid();
    return value;
};

const parseProviderPhase = (value: unknown): ProviderExecutionPhase => {
    if (value !== "accepted" && value !== "cancelled" && value !== "submitting" && value !== "submitted"
        && value !== "observed" && value !== "completed") return generationInvalid();
    return value;
};

const isSafeMediaCandidateUrl = (value: string, mediaKind: "image" | "audio", authority: "provider" | "imported"): boolean => {
    if (value.startsWith("/") || value.includes("\\") || value.split("/").some(part => part === "." || part === ".." || part === "")) return false;
    const suffix = value.split(".").pop()?.toLowerCase();
    if (mediaKind === "image") {
        const root = authority === "provider" ? "assets/atelier/" : "assets/imported/";
        return value.startsWith(root) && suffix === "png";
    }
    const root = authority === "provider" ? "audio/atelier/" : "audio/imported/";
    return value.startsWith(root) && suffix === "mp3";
};

const isCurrentImportedMediaCandidateUrl = (value: string, mediaKind: "image" | "audio", projectId: string): boolean => {
    const root = mediaKind === "image" ? `assets/imported/${projectId}/` : `audio/imported/${projectId}/`;
    return value.startsWith(root) && isSafeMediaCandidateUrl(value, mediaKind, "imported");
};

const parseProviderArtifact = (value: unknown): JsonRecord | null => {
    if (value === null) return null;
    if (!isRecord(value) || !hasExactKeys(value, PROVIDER_ARTIFACT_FIELDS) || value.schema_version !== 1) return generationInvalid();
    const artifact = requireJsonRecord(value, "invalid_atelier_generation_ledger");
    requireNonemptyString(value.attempt_id, "invalid_atelier_generation_ledger");
    requireJsonRecord(value.handle, "invalid_atelier_generation_ledger");
    if (typeof value.relative_path !== "string" || !value.relative_path.startsWith("video/")) return generationInvalid();
    requireGenerationHash(value.sha256);
    requirePositiveInteger(value.size_bytes, "invalid_atelier_generation_ledger");
    return artifact;
};

const parseProviderSyncArtifact = (value: unknown, mediaKind: "image" | "audio", attemptId: string, outputRef: string): JsonRecord | null => {
    if (value === null) return null;
    if (!isRecord(value) || !hasExactKeys(value, PROVIDER_SYNC_ARTIFACT_FIELDS) || value.schema_version !== 2
        || value.contract_version !== "provider-sync-artifact-v1" || value.media_kind !== mediaKind) return generationInvalid();
    const relativePath = requireNonemptyString(value.relative_path, "invalid_atelier_generation_ledger");
    const proofOutput = requireNonemptyString(value.output_ref, "invalid_atelier_generation_ledger");
    if (value.attempt_id !== attemptId || proofOutput !== outputRef || relativePath !== outputRef) return generationInvalid();
    const suffix = relativePath.split(".").pop()?.toLowerCase();
    if (mediaKind === "image" ? (!relativePath.startsWith("assets/atelier/") || suffix !== "png")
        : (!relativePath.startsWith("audio/atelier/") || suffix !== "mp3")) return generationInvalid();
    requireGenerationHash(value.sha256);
    requirePositiveInteger(value.size_bytes, "invalid_atelier_generation_ledger");
    return requireJsonRecord(value, "invalid_atelier_generation_ledger");
};

const parseProviderExecution = (value: unknown): ProviderExecutionRecord => {
    if (!isRecord(value)) return generationInvalid();
    if (value.schema_version === 2) {
        if (!hasExactKeys(value, PROVIDER_SYNC_EXECUTION_FIELDS) || value.execution_kind !== "sync_artifact"
            || (value.media_kind !== "image" && value.media_kind !== "audio") || value.handle !== null || value.observation !== null) return generationInvalid();
        const attemptId = requireNonemptyString(value.attempt_id, "invalid_atelier_generation_ledger");
        const outputRef = requireNonemptyString(value.output_ref, "invalid_atelier_generation_ledger");
        const phase = parseProviderPhase(value.phase);
        if (phase !== "accepted" && phase !== "cancelled" && phase !== "submitting" && phase !== "completed") return generationInvalid();
        const preparedArtifact = parseProviderSyncArtifact(value.prepared_artifact, value.media_kind, attemptId, outputRef);
        const artifact = parseProviderSyncArtifact(value.artifact, value.media_kind, attemptId, outputRef);
        if ((phase === "accepted" || phase === "cancelled") && (preparedArtifact !== null || artifact !== null)) return generationInvalid();
        if (phase === "completed" && (preparedArtifact === null || artifact === null || canonicalJson(preparedArtifact) !== canonicalJson(artifact))) return generationInvalid();
        return {
            schema_version: 2,
            execution_kind: "sync_artifact",
            media_kind: value.media_kind as "image" | "audio",
            output_ref: outputRef,
            attempt_id: attemptId,
            phase,
            provider_model_id: requireNonemptyString(value.provider_model_id, "invalid_atelier_generation_ledger"),
            intent: requireJsonRecord(value.intent, "invalid_atelier_generation_ledger"),
            media: requireJsonRecordArray(value.media, "invalid_atelier_generation_ledger"),
            scope: requireJsonRecord(value.scope, "invalid_atelier_generation_ledger"),
            credential_ref: requireNonemptyString(value.credential_ref, "invalid_atelier_generation_ledger"),
            fingerprint: requireGenerationHash(value.fingerprint),
            handle: null,
            observation: null,
            prepared_artifact: preparedArtifact,
            artifact,
        };
    }
    if (!hasExactKeys(value, PROVIDER_EXECUTION_FIELDS) || value.schema_version !== 1) return generationInvalid();
    return {
        schema_version: 1,
        attempt_id: requireNonemptyString(value.attempt_id, "invalid_atelier_generation_ledger"),
        phase: parseProviderPhase(value.phase),
        provider_model_id: requireNonemptyString(value.provider_model_id, "invalid_atelier_generation_ledger"),
        intent: requireJsonRecord(value.intent, "invalid_atelier_generation_ledger"),
        media: requireJsonRecordArray(value.media, "invalid_atelier_generation_ledger"),
        scope: requireJsonRecord(value.scope, "invalid_atelier_generation_ledger"),
        credential_ref: requireNonemptyString(value.credential_ref, "invalid_atelier_generation_ledger"),
        fingerprint: requireGenerationHash(value.fingerprint),
        handle: value.handle === null ? null : requireJsonRecord(value.handle, "invalid_atelier_generation_ledger"),
        observation: value.observation === null ? null : requireJsonRecord(value.observation, "invalid_atelier_generation_ledger"),
        prepared_artifact: parseProviderArtifact(value.prepared_artifact),
        artifact: parseProviderArtifact(value.artifact),
    };
};

const canonicalJson = (value: unknown): string => JSON.stringify(value, (_key, item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return item;
    const record = item as Record<string, unknown>;
    return Object.fromEntries(Object.keys(record).sort().map(key => [key, record[key]]));
});

const providerExecutionStableIdentity = (record: ProviderExecutionRecord): string => canonicalJson({
    provider_model_id: record.provider_model_id,
    intent: record.intent,
    media: record.media,
    scope: record.scope,
    credential_ref: record.credential_ref,
});

const isAcceptedPreviewProviderExecution = (record: ProviderExecutionRecord): boolean => (
    record.phase === "accepted"
    && record.handle === null
    && record.observation === null
    && record.prepared_artifact === null
    && record.artifact === null
);

const parseGenerationFirstFrame = (value: unknown): AtelierGenerationFirstFrameSource => {
    if (!isRecord(value)) return generationInvalid();
    const nodeId = requireNonemptyString(value.node_id, "invalid_atelier_generation_ledger");
    const mediaUrl = requireNonemptyString(value.media_url, "invalid_atelier_generation_ledger");
    if (!nodeId.trim() || !mediaUrl.trim()) return generationInvalid();
    return { node_id: nodeId, media_url: mediaUrl };
};

const parseOrderedMediaRole = (value: unknown): AtelierOrderedMediaRole => {
    if (!isAtelierOrderedMediaRole(value)) return generationInvalid();
    return value;
};

const parseOrderedMediaSelection = (value: unknown): AtelierOrderedMediaSelection => {
    if (!isRecord(value) || !hasExactKeys(value, ["role", "ordinal", "node_id", "media_url"])) return generationInvalid();
    const nodeId = requireNonemptyString(value.node_id, "invalid_atelier_generation_ledger");
    const mediaUrl = requireNonemptyString(value.media_url, "invalid_atelier_generation_ledger");
    if (!nodeId.trim() || !mediaUrl.trim()) return generationInvalid();
    return {
        role: parseOrderedMediaRole(value.role),
        ordinal: requireNonnegativeInteger(value.ordinal, "invalid_atelier_generation_ledger"),
        node_id: nodeId,
        media_url: mediaUrl,
    };
};

const parseOrderedMediaSelections = (value: unknown): AtelierOrderedMediaSelection[] => {
    if (!Array.isArray(value)) return generationInvalid();
    const media = value.map(parseOrderedMediaSelection);
    const seen = new Set<string>();
    const byRole = new Map<AtelierOrderedMediaRole, number[]>();
    for (const item of media) {
        const key = `${item.role}:${item.ordinal}`;
        if (seen.has(key)) return generationInvalid();
        seen.add(key);
        if ((item.role === "first_frame" || item.role === "last_frame") && item.ordinal !== 0) return generationInvalid();
        byRole.set(item.role, [...(byRole.get(item.role) ?? []), item.ordinal]);
    }
    for (const ordinals of Array.from(byRole.values())) {
        if (ordinals.some((ordinal, index) => ordinal !== index)) return generationInvalid();
    }
    if (!hasCanonicalAtelierOrderedMediaRoleOrder(media)) return generationInvalid();
    return media;
};

const providerMediaMatchesInput = (providerMedia: JsonRecord[], inputMedia: AtelierOrderedMediaSelection[]): boolean => {
    if (providerMedia.length !== inputMedia.length) return false;
    return inputMedia.every((item, index) => {
        const provider = providerMedia[index];
        return provider?.schema_version === 1
            && provider.role === item.role
            && provider.ordinal === item.ordinal
            && typeof provider.declared_media_type === "string"
            && provider.declared_media_type.length > 0
            && typeof provider.sha256 === "string"
            && GENERATION_HASH_RE.test(provider.sha256)
            && typeof provider.size_bytes === "number"
            && Number.isSafeInteger(provider.size_bytes)
            && provider.size_bytes > 0;
    });
};

const parseGenerationAuthorizationRef = (value: unknown): AtelierGenerationAuthorizationRef => {
    if (!isRecord(value) || !hasExactKeys(value, GENERATION_AUTHORIZATION_REF_FIELDS) || value.schema_version !== 1
        || value.tool_name !== AGENT_GENERATION_TOOL_NAME) return generationInvalid();
    return {
        schema_version: 1,
        project_id: requireNonemptyString(value.project_id, "invalid_atelier_generation_ledger"),
        turn_id: requireNonemptyString(value.turn_id, "invalid_atelier_generation_ledger"),
        call_id: requireNonemptyString(value.call_id, "invalid_atelier_generation_ledger"),
        approval_id: requireNonemptyString(value.approval_id, "invalid_atelier_generation_ledger"),
        tool_name: AGENT_GENERATION_TOOL_NAME,
        tool_schema_version: requireNonemptyString(value.tool_schema_version, "invalid_atelier_generation_ledger"),
        authorization_hash: requireGenerationHash(value.authorization_hash),
        preview_fingerprint: requireGenerationHash(value.preview_fingerprint),
        request_fingerprint: requireGenerationHash(value.request_fingerprint),
        server_storage_identity: requireNonemptyString(value.server_storage_identity, "invalid_atelier_generation_ledger"),
        policy_snapshot_hash: requireGenerationHash(value.policy_snapshot_hash),
    };
};

const parseAgentGenerationBinding = (value: unknown): AtelierAgentGenerationBinding => {
    if (!isRecord(value) || !hasExactKeys(value, AGENT_GENERATION_BINDING_FIELDS) || value.schema_version !== 1) return generationInvalid();
    const previewPublicRecord = requireJsonRecord(value.preview_public_record, "invalid_atelier_generation_ledger");
    const ref = parseGenerationAuthorizationRef(value.agent_authorization_ref);
    const providerExecution = parseProviderExecution(value.provider_execution);
    if (!isAcceptedPreviewProviderExecution(providerExecution)) return generationInvalid();
    if (previewPublicRecord.project_id !== undefined && previewPublicRecord.project_id !== ref.project_id) return generationInvalid();
    if (previewPublicRecord.server_storage_identity !== undefined && previewPublicRecord.server_storage_identity !== ref.server_storage_identity) return generationInvalid();
    return {
        schema_version: 1,
        preview_public_record: previewPublicRecord,
        provider_execution: providerExecution,
        agent_authorization_ref: ref,
        created_at: requireFiniteNumber(value.created_at, "invalid_atelier_generation_ledger"),
    };
};

const parseAgentApprovalRecord = (value: unknown): AtelierAgentApprovalRecord => {
    if (!isRecord(value) || !hasExactKeys(value, AGENT_APPROVAL_RECORD_FIELDS) || value.schema_version !== 1) return generationInvalid();
    if (value.decision !== "approved" && value.decision !== "denied" && value.decision !== "revoked") return generationInvalid();
    return {
        schema_version: 1,
        approval_id: requireNonemptyString(value.approval_id, "invalid_atelier_generation_ledger"),
        authorization_hash: requireGenerationHash(value.authorization_hash),
        decision: value.decision,
        decided_at: requireFiniteNumber(value.decided_at, "invalid_atelier_generation_ledger"),
    };
};

const canvasInvalid = (): never => {
    throw new AtelierProjectApiError("Invalid Atelier Agent canvas authorization", "invalid_atelier_agent_canvas_authorization");
};

const parseAgentCanvasBinding = (value: unknown): AtelierAgentCanvasBinding => {
    if (!isRecord(value) || !hasExactKeys(value, ["schema_version", "normalized_arguments", "target_state", "canvas_authorization_ref", "created_at"])
        || value.schema_version !== 1) return canvasInvalid();
    const ref = value.canvas_authorization_ref;
    if (!isRecord(ref) || !hasExactKeys(ref, ["schema_version", "project_id", "turn_id", "call_id", "approval_id", "tool_name", "tool_schema_version", "authorization_hash", "arguments_fingerprint", "target_fingerprint", "server_storage_identity", "policy_snapshot_hash"])
        || ref.schema_version !== 1 || (ref.tool_name !== "canvas.createVideoNode" && ref.tool_name !== "canvas.updateNodePrompt")) return canvasInvalid();
    for (const key of ["project_id", "turn_id", "call_id", "approval_id", "tool_schema_version", "server_storage_identity"]) {
        requireNonemptyString(ref[key], "invalid_atelier_agent_canvas_authorization");
    }
    for (const key of ["authorization_hash", "arguments_fingerprint", "target_fingerprint", "policy_snapshot_hash"]) {
        if (typeof ref[key] !== "string" || !/^[a-f0-9]{64}$/.test(ref[key] as string)) return canvasInvalid();
    }
    const args = requireJsonRecord(value.normalized_arguments, "invalid_atelier_agent_canvas_authorization");
    const target = requireJsonRecord(value.target_state, "invalid_atelier_agent_canvas_authorization");
    if (target.project_id !== ref.project_id) return canvasInvalid();
    if (ref.tool_name === "canvas.createVideoNode") {
        if (!hasExactKeys(args, ["type", "title", "prompt", "x", "y", "width", "height"]) || args.type !== "video"
            || typeof args.title !== "string" || typeof args.prompt !== "string") return canvasInvalid();
        for (const key of ["x", "y", "width", "height"]) requireFiniteNumber(args[key], "invalid_atelier_agent_canvas_authorization");
        if ((args.width as number) <= 0 || (args.height as number) <= 0) return canvasInvalid();
        if (!hasExactKeys(target, ["kind", "project_id", "node_count", "node_ids"]) || target.kind !== "project") return canvasInvalid();
        const ids = requireStringArray(target.node_ids, "invalid_atelier_agent_canvas_authorization");
        if (target.node_count !== ids.length || ids.some(id => !id) || new Set(ids).size !== ids.length) return canvasInvalid();
    } else {
        if (!hasExactKeys(args, hasOwn(args, "title") ? ["node_id", "prompt", "title"] : ["node_id", "prompt"])
            || typeof args.prompt !== "string" || (hasOwn(args, "title") && typeof args.title !== "string")) return canvasInvalid();
        requireNonemptyString(args.node_id, "invalid_atelier_agent_canvas_authorization");
        if (!hasExactKeys(target, ["kind", "project_id", "node"]) || target.kind !== "node" || !isRecord(target.node)) return canvasInvalid();
        const node = target.node;
        if (!hasExactKeys(node, ["id", "project_id", "type", "title", "prompt", "status", "updated_at"])
            || node.project_id !== ref.project_id || node.id !== args.node_id) return canvasInvalid();
        for (const key of ["type", "title", "prompt", "status"]) requireString(node[key], "invalid_atelier_agent_canvas_authorization");
        requireFiniteNumber(node.updated_at, "invalid_atelier_agent_canvas_authorization");
    }
    requireFiniteNumber(value.created_at, "invalid_atelier_agent_canvas_authorization");
    return value as unknown as AtelierAgentCanvasBinding;
};

const parseGenerationAttempt = (value: unknown): AtelierGenerationAttempt => {
    if (!isRecord(value)) return generationInvalid();
    const schemaVersion = value.schema_version;
    if (schemaVersion !== 1 && schemaVersion !== 2) return generationInvalid();
    if (value.execution_contract === MEDIA_GENERATION_CANDIDATE_CONTRACT) {
        if (!hasExactKeys(value, MEDIA_GENERATION_ATTEMPT_FIELDS) || schemaVersion !== 1
            || (value.media_kind !== "image" && value.media_kind !== "audio")) return generationInvalid();
        const providerExecution = parseProviderExecution(value.provider_execution);
        if (providerExecution.schema_version !== 2 || providerExecution.media_kind !== value.media_kind) return generationInvalid();
        const status = parseGenerationStatus(value.status);
        const errorCode = requireNullableString(value.error_code, "invalid_atelier_generation_ledger");
        if (errorCode !== null && status !== "needs_confirmation" && status !== "failed") return generationInvalid();
        const attempt = {
            schema_version: 1 as const,
            execution_contract: MEDIA_GENERATION_CANDIDATE_CONTRACT as typeof MEDIA_GENERATION_CANDIDATE_CONTRACT,
            project_id: requireNonemptyString(value.project_id, "invalid_atelier_generation_ledger"),
            node_id: requireNonemptyString(value.node_id, "invalid_atelier_generation_ledger"),
            candidate_id: requireNonemptyString(value.candidate_id, "invalid_atelier_generation_ledger"),
            attempt_id: requireNonemptyString(value.attempt_id, "invalid_atelier_generation_ledger"),
            attempt_revision: requireNonnegativeInteger(value.attempt_revision, "invalid_atelier_generation_ledger"),
            media_kind: value.media_kind as "image" | "audio",
            status,
            error_code: errorCode,
            output_ref: requireNullableString(value.output_ref, "invalid_atelier_generation_ledger"),
            provider_execution: providerExecution,
            created_at: requireFiniteNumber(value.created_at, "invalid_atelier_generation_ledger"),
            updated_at: requireFiniteNumber(value.updated_at, "invalid_atelier_generation_ledger"),
        };
        if (attempt.provider_execution.attempt_id !== attempt.attempt_id) return generationInvalid();
        if (attempt.status === "completed") {
            if (attempt.output_ref === null || attempt.provider_execution.output_ref !== attempt.output_ref) return generationInvalid();
        } else if (attempt.output_ref !== null) return generationInvalid();
        return attempt;
    }
    const isOrdered = hasOwn(value, "input_contract_version") || hasOwn(value, "input_media");
    const expectedFields = isOrdered
        ? schemaVersion === 1 ? ORDERED_GENERATION_ATTEMPT_FIELDS : AGENT_ORDERED_GENERATION_ATTEMPT_FIELDS
        : schemaVersion === 1 ? LEGACY_GENERATION_ATTEMPT_FIELDS : AGENT_LEGACY_GENERATION_ATTEMPT_FIELDS;
    if (!hasExactKeys(value, expectedFields)) return generationInvalid();
    const common = {
        project_id: requireNonemptyString(value.project_id, "invalid_atelier_generation_ledger"),
        node_id: requireNonemptyString(value.node_id, "invalid_atelier_generation_ledger"),
        candidate_id: requireNonemptyString(value.candidate_id, "invalid_atelier_generation_ledger"),
        attempt_id: requireNonemptyString(value.attempt_id, "invalid_atelier_generation_ledger"),
        attempt_revision: requireNonnegativeInteger(value.attempt_revision, "invalid_atelier_generation_ledger"),
        status: parseGenerationStatus(value.status),
        error_code: requireNullableString(value.error_code, "invalid_atelier_generation_ledger"),
        output_ref: requireNullableString(value.output_ref, "invalid_atelier_generation_ledger"),
        provider_execution: parseProviderExecution(value.provider_execution),
        created_at: requireFiniteNumber(value.created_at, "invalid_atelier_generation_ledger"),
        updated_at: requireFiniteNumber(value.updated_at, "invalid_atelier_generation_ledger"),
    };
    if (common.provider_execution.schema_version !== 1) return generationInvalid();
    const body = isOrdered ? (() => {
        if (value.input_contract_version !== ORDERED_MEDIA_CONTRACT_VERSION) return generationInvalid();
        const inputMedia = parseOrderedMediaSelections(value.input_media);
        if (!providerMediaMatchesInput(common.provider_execution.media, inputMedia)) return generationInvalid();
        return { input_contract_version: ORDERED_MEDIA_CONTRACT_VERSION as typeof ORDERED_MEDIA_CONTRACT_VERSION, input_media: inputMedia };
    })() : { first_frame_source: parseGenerationFirstFrame(value.first_frame_source) };
    const attempt: AtelierGenerationAttempt = schemaVersion === 1
        ? { schema_version: 1, ...common, ...body }
        : { schema_version: 2, ...common, ...body, agent_authorization_ref: parseGenerationAuthorizationRef(value.agent_authorization_ref) };
    if (attempt.provider_execution.attempt_id !== attempt.attempt_id) return generationInvalid();
    if (attempt.schema_version === 2) {
        if (!attempt.agent_authorization_ref || attempt.agent_authorization_ref.project_id !== attempt.project_id) return generationInvalid();
    }
    return attempt;
};

const parseGenerationReceiptMember = (value: unknown): AtelierGenerationRequestReceiptMember => {
    if (!isRecord(value) || !hasExactKeys(value, GENERATION_RECEIPT_MEMBER_FIELDS)) return generationInvalid();
    return {
        candidate_id: requireNonemptyString(value.candidate_id, "invalid_atelier_generation_ledger"),
        attempt_id: requireNonemptyString(value.attempt_id, "invalid_atelier_generation_ledger"),
        execution_fingerprint: requireGenerationHash(value.execution_fingerprint),
    };
};

const generationReceiptMembers = (receipt: AtelierGenerationRequestReceipt): AtelierGenerationRequestReceiptMember[] => {
    if (receipt.schema_version === 1) {
        if (!receipt.candidate_id || !receipt.attempt_id || !receipt.execution_fingerprint) return generationInvalid();
        return [{ candidate_id: receipt.candidate_id, attempt_id: receipt.attempt_id, execution_fingerprint: receipt.execution_fingerprint }];
    }
    if (!receipt.members) return generationInvalid();
    return receipt.members;
};

const parseGenerationReceipt = (value: unknown): AtelierGenerationRequestReceipt => {
    if (!isRecord(value) || typeof value.entrypoint !== "string" || !GENERATION_ENTRYPOINTS.has(value.entrypoint)
        || value.request_contract_version !== GENERATION_REQUEST_CONTRACT) return generationInvalid();
    const entrypoint = value.entrypoint as AtelierGenerationEntrypoint;
    const common = {
        project_id: requireNonemptyString(value.project_id, "invalid_atelier_generation_ledger"),
        entrypoint,
        request_contract_version: GENERATION_REQUEST_CONTRACT as typeof GENERATION_REQUEST_CONTRACT,
        client_request_id: requireGenerationRequestId(value.client_request_id),
        request_fingerprint: requireGenerationHash(value.request_fingerprint),
        node_id: requireNonemptyString(value.node_id, "invalid_atelier_generation_ledger"),
        base_revision: requireNonnegativeInteger(value.base_revision, "invalid_atelier_generation_ledger"),
        committed_revision: requireNonnegativeInteger(value.committed_revision, "invalid_atelier_generation_ledger"),
        created_at: requireFiniteNumber(value.created_at, "invalid_atelier_generation_ledger"),
    };
    if (common.committed_revision !== common.base_revision + 1) return generationInvalid();
    if (value.schema_version === 1) {
        if (!hasExactKeys(value, GENERATION_RECEIPT_V1_FIELDS)) return generationInvalid();
        return {
            schema_version: 1, ...common,
            candidate_id: requireNonemptyString(value.candidate_id, "invalid_atelier_generation_ledger"),
            attempt_id: requireNonemptyString(value.attempt_id, "invalid_atelier_generation_ledger"),
            execution_fingerprint: requireGenerationHash(value.execution_fingerprint),
        };
    }
    if (value.schema_version === 2) {
        if (!hasExactKeys(value, GENERATION_RECEIPT_V2_FIELDS) || !Array.isArray(value.members) || value.members.length < 2 || value.members.length > 6) return generationInvalid();
        const members = value.members.map(parseGenerationReceiptMember);
        const candidateIds = new Set<string>();
        const attemptIds = new Set<string>();
        for (const member of members) {
            if (candidateIds.has(member.candidate_id) || attemptIds.has(member.attempt_id)) return generationInvalid();
            candidateIds.add(member.candidate_id);
            attemptIds.add(member.attempt_id);
        }
        return { schema_version: 2, ...common, members };
    }
    return generationInvalid();
};

const parseGenerationCandidate = (value: Record<string, unknown>): AtelierGenerationCandidateProjection => {
    if (value.execution_contract === MEDIA_GENERATION_CANDIDATE_CONTRACT) {
        if (!hasExactKeys(value, MEDIA_GENERATION_CANDIDATE_FIELDS) || value.schema_version !== 2
            || (value.media_kind !== "image" && value.media_kind !== "audio")
            || (value.authority !== "provider" && value.authority !== "imported")) return generationInvalid();
        const status = parseGenerationStatus(value.status);
        const mediaUrl = requireNullableString(value.media_url, "invalid_atelier_generation_ledger");
        const attemptId = value.attempt_id === null ? null : requireNonemptyString(value.attempt_id, "invalid_atelier_generation_ledger");
        const executionFingerprint = value.execution_fingerprint === null ? null : requireGenerationHash(value.execution_fingerprint);
        if (status === "completed" ? mediaUrl === null : mediaUrl !== null) return generationInvalid();
        if (mediaUrl !== null && !isSafeMediaCandidateUrl(mediaUrl, value.media_kind, value.authority)) return generationInvalid();
        const errorCode = requireNullableString(value.error_code, "invalid_atelier_generation_ledger");
        if (errorCode !== null && status !== "needs_confirmation" && status !== "failed") return generationInvalid();
        if (value.authority === "provider" ? (attemptId === null || executionFingerprint === null) : (attemptId !== null || executionFingerprint !== null || status !== "completed" || errorCode !== null)) return generationInvalid();
        return {
            schema_version: 2,
            execution_contract: MEDIA_GENERATION_CANDIDATE_CONTRACT,
            id: requireNonemptyString(value.id, "invalid_atelier_generation_ledger"),
            attempt_id: attemptId,
            media_kind: value.media_kind as "image" | "audio",
            status,
            media_url: mediaUrl,
            error_code: requireNullableString(value.error_code, "invalid_atelier_generation_ledger"),
            authority: value.authority,
            execution_fingerprint: executionFingerprint,
        };
    }
    if (!hasExactKeys(value, GENERATION_CANDIDATE_FIELDS) || value.execution_contract !== GENERATION_CANDIDATE_CONTRACT || value.media_kind !== "video") {
        return generationInvalid();
    }
    const candidate: AtelierGenerationCandidateProjection = {
        execution_contract: GENERATION_CANDIDATE_CONTRACT,
        id: requireNonemptyString(value.id, "invalid_atelier_generation_ledger"),
        attempt_id: requireNonemptyString(value.attempt_id, "invalid_atelier_generation_ledger"),
        media_kind: "video",
        status: parseGenerationStatus(value.status),
        video_url: requireNullableString(value.video_url, "invalid_atelier_generation_ledger"),
        error_code: requireNullableString(value.error_code, "invalid_atelier_generation_ledger"),
        created_at: requireFiniteNumber(value.created_at, "invalid_atelier_generation_ledger"),
        updated_at: requireFiniteNumber(value.updated_at, "invalid_atelier_generation_ledger"),
    };
    if (candidate.status === "completed" ? candidate.video_url === null : candidate.video_url !== null) return generationInvalid();
    return candidate;
};

const parseCandidateBranchSource = (value: unknown): AtelierCandidateBranchSource => {
    if (!isRecord(value) || !hasExactKeys(value, CANDIDATE_BRANCH_SOURCE_FIELDS) || value.schema_version !== 1
        || (value.media_kind !== "image" && value.media_kind !== "video" && value.media_kind !== "audio")
        || (value.recipe_status !== "available" && value.recipe_status !== "unavailable_imported" && value.recipe_status !== "unavailable_legacy"
            && value.recipe_status !== "unavailable_inputs" && value.recipe_status !== "unsupported")) return branchSourceInvalid();
    const mediaUrl = requireNonemptyString(value.media_url, "invalid_atelier_candidate_branch_source");
    if (!/^uploads\/(?:imported\/[A-Za-z0-9_-]+\/)?[A-Za-z0-9_-]+\.[A-Za-z0-9]+$/.test(mediaUrl)) return branchSourceInvalid();
    return {
        schema_version: 1,
        origin_project_id: requireNonemptyString(value.origin_project_id, "invalid_atelier_candidate_branch_source"),
        origin_node_id: requireNonemptyString(value.origin_node_id, "invalid_atelier_candidate_branch_source"),
        origin_candidate_id: requireNonemptyString(value.origin_candidate_id, "invalid_atelier_candidate_branch_source"),
        media_kind: value.media_kind,
        media_url: mediaUrl,
        media_sha256: typeof value.media_sha256 === "string" && /^[a-f0-9]{64}$/.test(value.media_sha256) ? value.media_sha256 : branchSourceInvalid(),
        media_size_bytes: requirePositiveInteger(value.media_size_bytes, "invalid_atelier_candidate_branch_source"),
        recipe_status: value.recipe_status,
    };
};

const parseCandidateJudgment = (value: unknown): AtelierCandidateJudgment => {
    if (!isRecord(value) || (!hasExactKeys(value, CANDIDATE_JUDGMENT_FIELDS) && !hasExactKeys(value, CANDIDATE_JUDGMENT_FIELDS_WITH_SUPERSEDES))
        || value.schema_version !== 1 || (value.media_kind !== "image" && value.media_kind !== "video" && value.media_kind !== "audio")
        || (value.verdict !== "unreviewed" && value.verdict !== "keep" && value.verdict !== "reject")) return judgmentInvalid();
    const reason = requireString(value.reason, "invalid_atelier_candidate_judgments");
    if (Array.from(reason).length > 2000) return judgmentInvalid();
    const judgment: AtelierCandidateJudgment = {
        schema_version: 1,
        id: requireNonemptyString(value.id, "invalid_atelier_candidate_judgments"),
        project_id: requireNonemptyString(value.project_id, "invalid_atelier_candidate_judgments"),
        node_id: requireNonemptyString(value.node_id, "invalid_atelier_candidate_judgments"),
        candidate_id: requireNonemptyString(value.candidate_id, "invalid_atelier_candidate_judgments"),
        media_kind: value.media_kind,
        verdict: value.verdict,
        reason,
        created_at: requireNonnegativeFiniteNumber(value.created_at, "invalid_atelier_candidate_judgments"),
    };
    if (value.supersedes_judgment_id !== undefined) {
        judgment.supersedes_judgment_id = requireNonemptyString(value.supersedes_judgment_id, "invalid_atelier_candidate_judgments");
    }
    return judgment;
};

const normalizeGenerationCandidateList = (value: JsonValue | undefined): JsonValue | undefined => {
    if (!Array.isArray(value)) return value;
    return value.map(item => {
        if (!isRecord(item) || !hasOwn(item, "execution_contract")) return item as JsonValue;
        if (item.execution_contract !== GENERATION_CANDIDATE_CONTRACT && item.execution_contract !== MEDIA_GENERATION_CANDIDATE_CONTRACT) return generationInvalid();
        return parseGenerationCandidate(item) as unknown as JsonValue;
    });
};

const parseNode = (value: unknown): AtelierNode => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier project response", "invalid_atelier_project");
    }
    const data = requireJsonRecord(value.data, "invalid_atelier_project");
    const normalizedCandidates = normalizeGenerationCandidateList(data.candidates);
    const normalizedDeletedCandidates = normalizeGenerationCandidateList(data.deleted_candidates);
    if (normalizedCandidates !== undefined) data.candidates = normalizedCandidates;
    if (normalizedDeletedCandidates !== undefined) data.deleted_candidates = normalizedDeletedCandidates;
    if (data.branch_source !== undefined) data.branch_source = parseCandidateBranchSource(data.branch_source) as unknown as JsonValue;
    return {
        id: requireString(value.id, "invalid_atelier_project"),
        project_id: requireString(value.project_id, "invalid_atelier_project"),
        type: requireString(value.type, "invalid_atelier_project"),
        title: requireString(value.title, "invalid_atelier_project"),
        prompt: requireString(value.prompt, "invalid_atelier_project"),
        status: requireString(value.status, "invalid_atelier_project"),
        x: requireFiniteNumber(value.x, "invalid_atelier_project"),
        y: requireFiniteNumber(value.y, "invalid_atelier_project"),
        width: requireFiniteNumber(value.width, "invalid_atelier_project"),
        height: requireFiniteNumber(value.height, "invalid_atelier_project"),
        source_project_id: requireNullableString(value.source_project_id, "invalid_atelier_project"),
        frame_id: requireNullableString(value.frame_id, "invalid_atelier_project"),
        asset_id: requireNullableString(value.asset_id, "invalid_atelier_project"),
        video_task_id: requireNullableString(value.video_task_id, "invalid_atelier_project"),
        media_urls: requireStringArray(value.media_urls, "invalid_atelier_project"),
        data,
        created_by: requireString(value.created_by, "invalid_atelier_project"),
        created_at: requireFiniteNumber(value.created_at, "invalid_atelier_project"),
        updated_at: requireFiniteNumber(value.updated_at, "invalid_atelier_project"),
    };
};

const parseTransition = (value: unknown): AtelierSequenceTransition => {
    if (!isRecord(value) || (value.kind !== "cut" && value.kind !== "fade")) {
        throw new AtelierProjectApiError("Invalid Atelier project response", "invalid_atelier_project");
    }
    return {
        kind: value.kind,
        duration: requireNonnegativeFiniteNumber(value.duration, "invalid_atelier_project"),
    };
};

const parseSequenceEntry = (value: unknown): AtelierSequenceEntry => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier project response", "invalid_atelier_project");
    }
    const entry: AtelierSequenceEntry = {
        parentId: requireNonemptyString(value.parentId, "invalid_atelier_project"),
        candidateId: value.candidateId === null ? null : requireNonemptyString(value.candidateId, "invalid_atelier_project"),
        gain: requireBoundedFiniteNumber(value.gain, 0, 2, "invalid_atelier_project"),
        transition: parseTransition(value.transition),
    };
    if (value.id !== undefined) entry.id = value.id === null ? null : requireNonemptyString(value.id, "invalid_atelier_project");
    if (value.trimStart !== undefined) entry.trimStart = value.trimStart === null ? null : requireNonnegativeFiniteNumber(value.trimStart, "invalid_atelier_project");
    if (value.trimEnd !== undefined) entry.trimEnd = value.trimEnd === null ? null : requirePositiveFiniteNumber(value.trimEnd, "invalid_atelier_project");
    return entry;
};

const parseAudioTrack = (value: unknown): AtelierAudioTrack => {
    if (!isRecord(value) || typeof value.loop !== "boolean") {
        throw new AtelierProjectApiError("Invalid Atelier project response", "invalid_atelier_project");
    }
    const track: AtelierAudioTrack = {
        id: requireNonemptyString(value.id, "invalid_atelier_project"),
        node_id: requireNonemptyString(value.node_id, "invalid_atelier_project"),
        start: requireNonnegativeFiniteNumber(value.start, "invalid_atelier_project"),
        gain: requireBoundedFiniteNumber(value.gain, 0, 2, "invalid_atelier_project"),
        loop: value.loop,
    };
    if (value.trim_start !== undefined) track.trim_start = value.trim_start === null ? null : requireNonnegativeFiniteNumber(value.trim_start, "invalid_atelier_project");
    if (value.trim_end !== undefined) track.trim_end = value.trim_end === null ? null : requirePositiveFiniteNumber(value.trim_end, "invalid_atelier_project");
    return track;
};

const EXPORT_HASH_RE = /^[0-9a-f]{64}$/;
const EXPORT_CLIENT_REQUEST_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const EXPORT_OUTPUT_RE = /^video\/atelier_seq_[A-Za-z0-9_-]+\.mp4$/;
const EXPORT_JOB_ID_RE = /^[A-Za-z0-9_-]+$/;
const EXPORT_JOB_FIELDS = [
    "schema_version", "job_id", "project_id", "status", "request_contract_version", "client_request_id",
    "request_fingerprint", "base_revision", "job_revision", "created_at", "updated_at", "started_at",
    "finished_at", "snapshot_digest", "snapshot", "source_entries", "source_audio_tracks", "output_ref",
    "prepared_proof", "export_id", "error_code", "error_message", "cancelled_at",
] as const;
const EXPORT_JOB_FIELDS_WITH_SOURCE = [...EXPORT_JOB_FIELDS, "source_export_id"] as const;
const EXPORT_PROOF_FIELDS = [
    "schema_version", "job_id", "project_id", "relative_path", "sha256", "size_bytes", "duration",
    "width", "height", "fps", "snapshot_digest", "prepared_at",
] as const;

const requireExportHash = (value: unknown): string => {
    if (typeof value !== "string" || !EXPORT_HASH_RE.test(value)) {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    return value;
};

const requireExportOutputRef = (value: unknown): string => {
    if (typeof value !== "string" || !EXPORT_OUTPUT_RE.test(value)) {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    return value;
};

const requireNullableFiniteNumber = (value: unknown): number | null => (
    value === null ? null : requireFiniteNumber(value, "invalid_atelier_export_job")
);

const requireNullableNonemptyString = (value: unknown): string | null => (
    value === null ? null : requireNonemptyString(value, "invalid_atelier_export_job")
);

const parseSequencePublicationProof = (value: unknown): AtelierSequencePublicationProof => {
    if (!isRecord(value) || !hasExactKeys(value, EXPORT_PROOF_FIELDS) || value.schema_version !== 1) {
        throw new AtelierProjectApiError("Invalid Atelier export proof", "invalid_atelier_export_job");
    }
    return {
        schema_version: 1,
        job_id: requireNonemptyString(value.job_id, "invalid_atelier_export_job"),
        project_id: requireNonemptyString(value.project_id, "invalid_atelier_export_job"),
        relative_path: requireExportOutputRef(value.relative_path),
        sha256: requireExportHash(value.sha256),
        size_bytes: requirePositiveInteger(value.size_bytes, "invalid_atelier_export_job"),
        duration: requirePositiveFiniteNumber(value.duration, "invalid_atelier_export_job"),
        width: requirePositiveInteger(value.width, "invalid_atelier_export_job"),
        height: requirePositiveInteger(value.height, "invalid_atelier_export_job"),
        fps: requirePositiveInteger(value.fps, "invalid_atelier_export_job"),
        snapshot_digest: requireExportHash(value.snapshot_digest),
        prepared_at: requireFiniteNumber(value.prepared_at, "invalid_atelier_export_job"),
    };
};

const parseSequenceExportJob = (value: unknown): AtelierSequenceExportJob => {
    if (!isRecord(value) || (!hasExactKeys(value, EXPORT_JOB_FIELDS) && !hasExactKeys(value, EXPORT_JOB_FIELDS_WITH_SOURCE))
        || value.schema_version !== 1
        || (value.request_contract_version !== "atelier-sequence-export-v1" && value.request_contract_version !== "atelier-sequence-export-redo-v1")) {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    const status = value.status;
    if (status !== "queued" && status !== "running" && status !== "cancelling" && status !== "cancelled"
        && status !== "failed" && status !== "repair_required" && status !== "completed") {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    const sourceExportId = value.source_export_id === undefined ? null : requireNullableNonemptyString(value.source_export_id);
    if ((value.request_contract_version === "atelier-sequence-export-v1" && sourceExportId !== null)
        || (value.request_contract_version === "atelier-sequence-export-redo-v1" && sourceExportId === null)) {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    const job: AtelierSequenceExportJob = {
        schema_version: 1,
        job_id: requireNonemptyString(value.job_id, "invalid_atelier_export_job"),
        project_id: requireNonemptyString(value.project_id, "invalid_atelier_export_job"),
        status: status as AtelierSequenceExportJobStatus,
        request_contract_version: value.request_contract_version,
        client_request_id: requireNonemptyString(value.client_request_id, "invalid_atelier_export_job"),
        source_export_id: sourceExportId,
        request_fingerprint: requireExportHash(value.request_fingerprint),
        base_revision: requireNonnegativeInteger(value.base_revision, "invalid_atelier_export_job"),
        job_revision: requireNonnegativeInteger(value.job_revision, "invalid_atelier_export_job"),
        created_at: requireFiniteNumber(value.created_at, "invalid_atelier_export_job"),
        updated_at: requireFiniteNumber(value.updated_at, "invalid_atelier_export_job"),
        started_at: requireNullableFiniteNumber(value.started_at),
        finished_at: requireNullableFiniteNumber(value.finished_at),
        snapshot_digest: requireExportHash(value.snapshot_digest),
        snapshot: requireJsonRecord(value.snapshot, "invalid_atelier_export_job"),
        source_entries: parseArray(value.source_entries, parseSequenceEntry, "invalid_atelier_export_job"),
        source_audio_tracks: parseArray(value.source_audio_tracks, parseAudioTrack, "invalid_atelier_export_job"),
        output_ref: requireExportOutputRef(value.output_ref),
        prepared_proof: value.prepared_proof === null ? null : parseSequencePublicationProof(value.prepared_proof),
        export_id: requireNullableNonemptyString(value.export_id),
        error_code: requireNullableNonemptyString(value.error_code),
        error_message: requireNullableNonemptyString(value.error_message),
        cancelled_at: requireNullableFiniteNumber(value.cancelled_at),
    };
    if (!EXPORT_JOB_ID_RE.test(job.job_id) || job.output_ref !== `video/atelier_seq_${job.job_id}.mp4` || !EXPORT_CLIENT_REQUEST_RE.test(job.client_request_id)) {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    if (job.prepared_proof !== null && (job.prepared_proof.project_id !== job.project_id || job.prepared_proof.job_id !== job.job_id
        || job.prepared_proof.relative_path !== job.output_ref || job.prepared_proof.snapshot_digest !== job.snapshot_digest)) {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    if (job.status === "repair_required" && job.prepared_proof === null) {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    if (job.status === "completed" && (job.prepared_proof === null || job.export_id === null || job.finished_at === null)) {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    if (job.status !== "completed" && job.export_id !== null) {
        throw new AtelierProjectApiError("Invalid Atelier export job", "invalid_atelier_export_job");
    }
    return job;
};


const parseExportDeletionReceipt = (value: unknown): AtelierExportDeletionReceipt => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier export deletion receipt", "invalid_atelier_export_deletion_receipt");
    }
    const fileStatus = value.file_status;
    if (fileStatus !== "retained" && fileStatus !== "pending" && fileStatus !== "deleted" && fileStatus !== "already_missing" && fileStatus !== "blocked") {
        throw new AtelierProjectApiError("Invalid Atelier export deletion receipt", "invalid_atelier_export_deletion_receipt");
    }
    const receipt: AtelierExportDeletionReceipt = {
        client_request_id: requireNonemptyString(value.client_request_id, "invalid_atelier_export_deletion_receipt"),
        delete_file: requireBoolean(value.delete_file, "invalid_atelier_export_deletion_receipt"),
        deleted_at: requireFiniteNumber(value.deleted_at, "invalid_atelier_export_deletion_receipt"),
        file_status: fileStatus,
        checked_at: value.checked_at === null ? null : requireFiniteNumber(value.checked_at, "invalid_atelier_export_deletion_receipt"),
        error_code: requireNullableString(value.error_code, "invalid_atelier_export_deletion_receipt"),
        error_message: requireNullableString(value.error_message, "invalid_atelier_export_deletion_receipt"),
    };
    if (!EXPORT_CLIENT_REQUEST_RE.test(receipt.client_request_id)) {
        throw new AtelierProjectApiError("Invalid Atelier export deletion receipt", "invalid_atelier_export_deletion_receipt");
    }
    if ((receipt.file_status === "pending") !== (receipt.checked_at === null)) {
        throw new AtelierProjectApiError("Invalid Atelier export deletion receipt", "invalid_atelier_export_deletion_receipt");
    }
    if ((receipt.delete_file === false) !== (receipt.file_status === "retained")) {
        throw new AtelierProjectApiError("Invalid Atelier export deletion receipt", "invalid_atelier_export_deletion_receipt");
    }
    if (receipt.file_status === "blocked") {
        if (!receipt.error_code || !receipt.error_message) {
            throw new AtelierProjectApiError("Invalid Atelier export deletion receipt", "invalid_atelier_export_deletion_receipt");
        }
    } else if (receipt.error_code !== null || receipt.error_message !== null) {
        throw new AtelierProjectApiError("Invalid Atelier export deletion receipt", "invalid_atelier_export_deletion_receipt");
    }
    return receipt;
};

const importInvalid = (): never => {
    throw new AtelierProjectApiError("Invalid Atelier import provenance", "invalid_atelier_import_provenance");
};

const parseImportedExportProof = (value: unknown): AtelierImportedExportProof => {
    if (!isRecord(value) || value.schema_version !== 1 || !hasExactKeys(value, [
        "schema_version", "project_id", "export_id", "relative_path", "sha256", "size_bytes",
        "source_project_id", "source_export_id", "source_storage_id", "imported_at",
    ])) return importInvalid();
    const code = "invalid_atelier_import_provenance";
    const projectId = requireNonemptyString(value.project_id, code);
    const path = requireNonemptyString(value.relative_path, code);
    const segments = path.split("/");
    if (!["assets", "audio", "export", "storyboard", "uploads", "video", "video_inputs"].includes(segments[0])
        || segments[1] !== "imported" || segments[2] !== projectId || segments.length < 4
        || segments.some(part => !part || part === "." || part === "..") || /[\\\u0000]/.test(path)) return importInvalid();
    return {
        schema_version: 1, project_id: projectId,
        export_id: requireNonemptyString(value.export_id, code), relative_path: path,
        sha256: requireExportHash(value.sha256), size_bytes: requirePositiveInteger(value.size_bytes, code),
        source_project_id: requireNonemptyString(value.source_project_id, code),
        source_export_id: requireNonemptyString(value.source_export_id, code),
        source_storage_id: requireNonemptyString(value.source_storage_id, code),
        imported_at: requireFiniteNumber(value.imported_at, code),
    };
};

const parseHistoryStrings = (value: unknown): Record<string, string> => {
    if (!isRecord(value)) return importInvalid();
    return Object.fromEntries(Object.entries(value).map(([key, text]) => [key, requireString(text, "invalid_atelier_import_provenance")]));
};

const parseImportedAgentHistory = (value: unknown): AtelierImportedAgentHistoryEntry => {
    if (!isRecord(value) || value.schema_version !== 1 || !hasExactKeys(value, [
        "schema_version", "source_turn_id", "status", "user_message", "assistant_response", "messages",
        "created_at", "completed_at", "source_refs", "target_refs",
    ])) return importInvalid();
    const code = "invalid_atelier_import_provenance";
    return {
        schema_version: 1, source_turn_id: requireNonemptyString(value.source_turn_id, code),
        status: requireString(value.status, code), user_message: requireString(value.user_message, code),
        assistant_response: requireNullableString(value.assistant_response, code),
        messages: parseArray(value.messages, item => {
            const message = parseHistoryStrings(item);
            const role = message.role;
            if ((role !== "user" && role !== "assistant" && role !== "tool") || typeof message.content !== "string"
                || Object.keys(message).some(key => !["role", "content", "name", "status", "error_code"].includes(key))) return importInvalid();
            return {
                role, content: message.content,
                ...(message.name === undefined ? {} : { name: message.name }),
                ...(message.status === undefined ? {} : { status: message.status }),
                ...(message.error_code === undefined ? {} : { error_code: message.error_code }),
            };
        }, code),
        created_at: value.created_at === null ? null : requireFiniteNumber(value.created_at, code),
        completed_at: value.completed_at === null ? null : requireFiniteNumber(value.completed_at, code),
        source_refs: parseHistoryStrings(value.source_refs), target_refs: parseHistoryStrings(value.target_refs),
    };
};

const parseImportProvenance = (value: unknown): AtelierImportProvenance => {
    if (!isRecord(value) || value.schema_version !== 1 || !hasExactKeys(value, [
        "schema_version", "source_project_id", "source_storage_id", "imported_at", "agent_history",
    ])) return importInvalid();
    const code = "invalid_atelier_import_provenance";
    return {
        schema_version: 1, source_project_id: requireNonemptyString(value.source_project_id, code),
        source_storage_id: requireNonemptyString(value.source_storage_id, code),
        imported_at: requireFiniteNumber(value.imported_at, code),
        agent_history: parseArray(value.agent_history, parseImportedAgentHistory, code),
    };
};

const parseExportRecord = (value: unknown): AtelierExportRecord => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier project response", "invalid_atelier_project");
    }
    const timeline = value.timeline_snapshot;
    const publicationProof = value.publication_proof === undefined || value.publication_proof === null
        ? null
        : parseSequencePublicationProof(value.publication_proof);
    const jobId = value.job_id === undefined ? null : requireNullableNonemptyString(value.job_id);
    const snapshotDigest = value.snapshot_digest === undefined || value.snapshot_digest === null ? null : requireExportHash(value.snapshot_digest);
    const deletion = value.deletion === undefined || value.deletion === null ? null : parseExportDeletionReceipt(value.deletion);
    return {
        id: requireString(value.id, "invalid_atelier_project"),
        project_id: requireNonemptyString(value.project_id, "invalid_atelier_project"),
        created_at: requireFiniteNumber(value.created_at, "invalid_atelier_project"),
        clip_count: requireNonnegativeInteger(value.clip_count, "invalid_atelier_project"),
        size_mb: requireFiniteNumber(value.size_mb, "invalid_atelier_project"),
        video_url: requireString(value.video_url, "invalid_atelier_project"),
        filename: requireString(value.filename, "invalid_atelier_project"),
        source_entries: parseArray(value.source_entries, parseSequenceEntry, "invalid_atelier_project"),
        ...(timeline === undefined ? {} : { timeline_snapshot: timeline === null ? null : requireJsonRecord(timeline, "invalid_atelier_project") }),
        publication_proof: publicationProof,
        job_id: jobId,
        snapshot_digest: snapshotDigest,
        deletion,
        ...(value.imported_proof === undefined ? {} : { imported_proof: value.imported_proof === null ? null : parseImportedExportProof(value.imported_proof) }),
    };
};

const parseDeleteFieldValue = (value: unknown): AtelierNodeDeleteFieldValue => {
    if (!isRecord(value) || typeof value.exists !== "boolean" || !isJsonValue(value.value)) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt", "invalid_atelier_delete_receipt");
    }
    return { exists: value.exists, value: value.value };
};

const parseDeleteFieldValueMap = (value: unknown): Record<string, AtelierNodeDeleteFieldValue> => {
    if (!isPlainRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt", "invalid_atelier_delete_receipt");
    }
    const parsed: Record<string, AtelierNodeDeleteFieldValue> = {};
    for (const [key, item] of Object.entries(value)) {
        parsed[key] = parseDeleteFieldValue(item);
    }
    return parsed;
};

const parseDeleteSourceProof = (value: unknown): AtelierNodeDeleteSourceProof => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt", "invalid_atelier_delete_receipt");
    }
    return {
        target_node_id: requireNonemptyString(value.target_node_id, "invalid_atelier_delete_receipt"),
        source_node_id: requireNonemptyString(value.source_node_id, "invalid_atelier_delete_receipt"),
        source_type: requireNonemptyString(value.source_type, "invalid_atelier_delete_receipt"),
        urls: requireStringArray(value.urls, "invalid_atelier_delete_receipt"),
    };
};

const parseDeleteSnapshot = (value: unknown): AtelierNodeDeleteSnapshot => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt", "invalid_atelier_delete_receipt");
    }
    return {
        node: parseNode(value.node),
        original_index: requireNonnegativeInteger(value.original_index, "invalid_atelier_delete_receipt"),
        reference_media_urls: requireStringArray(value.reference_media_urls, "invalid_atelier_delete_receipt"),
        referenced_source_ids: requireStringArray(value.referenced_source_ids, "invalid_atelier_delete_receipt"),
        source_proofs: parseArray(value.source_proofs, parseDeleteSourceProof, "invalid_atelier_delete_receipt"),
    };
};

const parseDeleteDataChangeGroup = (value: unknown): AtelierNodeDeleteDataChangeGroup => {
    if (!isRecord(value) || (value.group !== "references" && value.group !== "parent" && value.group !== "region")) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt", "invalid_atelier_delete_receipt");
    }
    return {
        node_id: requireNonemptyString(value.node_id, "invalid_atelier_delete_receipt"),
        group: value.group,
        before: parseDeleteFieldValueMap(value.before),
        after: parseDeleteFieldValueMap(value.after),
        source_proofs: parseArray(value.source_proofs, parseDeleteSourceProof, "invalid_atelier_delete_receipt"),
    };
};

const parseDeleteSequenceChange = (value: unknown): AtelierNodeDeleteSequenceChange => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt", "invalid_atelier_delete_receipt");
    }
    return {
        before: parseArray(value.before, parseSequenceEntry, "invalid_atelier_delete_receipt"),
        after: parseArray(value.after, parseSequenceEntry, "invalid_atelier_delete_receipt"),
        removed_node_ids: requireStringArray(value.removed_node_ids, "invalid_atelier_delete_receipt"),
    };
};

const parseDeleteAudioTracksChange = (value: unknown): AtelierNodeDeleteAudioTracksChange => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt", "invalid_atelier_delete_receipt");
    }
    return {
        before: parseArray(value.before, parseAudioTrack, "invalid_atelier_delete_receipt"),
        after: parseArray(value.after, parseAudioTrack, "invalid_atelier_delete_receipt"),
        removed_node_ids: requireStringArray(value.removed_node_ids, "invalid_atelier_delete_receipt"),
    };
};

const parseRestoreSkip = (value: unknown): AtelierNodeRestoreSkip => {
    if (!isRecord(value) || (value.scope !== "node" && value.scope !== "data_group" && value.scope !== "sequence" && value.scope !== "audio_tracks")) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt", "invalid_atelier_restore_skip");
    }
    return {
        scope: value.scope,
        reason: requireNonemptyString(value.reason, "invalid_atelier_restore_skip"),
        node_id: value.node_id === undefined ? null : requireNullableString(value.node_id, "invalid_atelier_restore_skip"),
        group: value.group === undefined ? null : requireNullableString(value.group, "invalid_atelier_restore_skip"),
    };
};

const parseDeleteReceipt = (value: unknown): AtelierNodeDeleteReceipt => {
    if (!isRecord(value) || value.schema_version !== 1) {
        throw new AtelierProjectApiError("Invalid Atelier delete receipt", "invalid_atelier_delete_receipt");
    }
    return {
        schema_version: 1,
        receipt_id: requireNonemptyString(value.receipt_id, "invalid_atelier_delete_receipt"),
        project_id: requireNonemptyString(value.project_id, "invalid_atelier_delete_receipt"),
        base_revision: requireNonnegativeInteger(value.base_revision, "invalid_atelier_delete_receipt"),
        restore_base_revision: value.restore_base_revision === null ? null : requireNonnegativeInteger(value.restore_base_revision, "invalid_atelier_delete_receipt"),
        deleted_at: requireFiniteNumber(value.deleted_at, "invalid_atelier_delete_receipt"),
        restored_at: value.restored_at === null ? null : requireFiniteNumber(value.restored_at, "invalid_atelier_delete_receipt"),
        node_ids: requireStringArray(value.node_ids, "invalid_atelier_delete_receipt"),
        node_snapshots: parseArray(value.node_snapshots, parseDeleteSnapshot, "invalid_atelier_delete_receipt"),
        data_change_groups: parseArray(value.data_change_groups, parseDeleteDataChangeGroup, "invalid_atelier_delete_receipt"),
        sequence_change: value.sequence_change === null ? null : parseDeleteSequenceChange(value.sequence_change),
        audio_tracks_change: value.audio_tracks_change === null ? null : parseDeleteAudioTracksChange(value.audio_tracks_change),
        last_restore_skips: parseArray(value.last_restore_skips, parseRestoreSkip, "invalid_atelier_delete_receipt"),
    };
};

function parseArray<T>(value: unknown, parser: (item: unknown) => T, code: string): T[] {
    if (!Array.isArray(value)) {
        throw new AtelierProjectApiError("Invalid Atelier project response", code);
    }
    return value.map(parser);
}

const UPLOAD_EXTENSIONS = {
    image: ["png", "jpg", "jpeg", "webp", "gif"],
    audio: ["mp3", "wav", "m4a", "ogg", "flac", "aac"],
    video: ["mp4", "mov", "webm", "mkv"],
};
const uploadInvalid = (): never => { throw new AtelierProjectApiError("Invalid Atelier upload receipt", "invalid_atelier_upload_receipt"); };
const uploadHash = (value: unknown): string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value) ? value : uploadInvalid();
const uploadPath = (value: unknown): string => typeof value === "string" && /^uploads\/[A-Za-z0-9_-]+\.[a-z0-9]+$/.test(value) ? value : uploadInvalid();
const uploadOptionalInteger = (value: unknown): number | null => value === null ? null : requirePositiveInteger(value, "invalid_atelier_upload_receipt");

const parseUploadMedia = (value: unknown): AtelierUploadedMediaProof => {
    if (!isRecord(value) || (value.media_kind !== "image" && value.media_kind !== "audio" && value.media_kind !== "video")) return uploadInvalid();
    const media: AtelierUploadedMediaProof = {
        url: uploadPath(value.url), media_kind: value.media_kind,
        size_bytes: requirePositiveInteger(value.size_bytes, "invalid_atelier_upload_receipt"), sha256: uploadHash(value.sha256),
        width: uploadOptionalInteger(value.width), height: uploadOptionalInteger(value.height),
        duration: value.duration === null ? null : requireFiniteNumber(value.duration, "invalid_atelier_upload_receipt"),
        poster_url: value.poster_url === null ? null : uploadPath(value.poster_url),
        poster_size_bytes: uploadOptionalInteger(value.poster_size_bytes),
        poster_sha256: value.poster_sha256 === null ? null : uploadHash(value.poster_sha256),
    };
    if (!UPLOAD_EXTENSIONS[media.media_kind].includes(media.url.split(".").pop()!)) return uploadInvalid();
    if (media.media_kind === "audio" ? (media.width !== null || media.height !== null) : (media.width === null || media.height === null)) return uploadInvalid();
    if (media.media_kind === "image" ? media.duration !== null : (media.duration === null || media.duration <= 0)) return uploadInvalid();
    const posterFields = [media.poster_url, media.poster_size_bytes, media.poster_sha256];
    if (posterFields.some(field => field !== null) && (media.media_kind !== "video" || posterFields.some(field => field === null)
        || !media.poster_url?.endsWith(".jpg") || media.poster_url === media.url)) return uploadInvalid();
    return media;
};

export const parseAtelierUploadReceipt = (value: unknown): AtelierNodeUploadReceipt => {
    if (!isRecord(value) || value.schema_version !== 1 || value.request_contract_version !== "atelier-upload-request-v1"
        || (value.operation !== "create_media_node" && value.operation !== "upload_reference_attach")
        || value.replaced_node_id !== null || value.previous_media !== null) return uploadInvalid();
    const receipt: AtelierNodeUploadReceipt = {
        schema_version: 1, request_contract_version: "atelier-upload-request-v1", operation: value.operation,
        receipt_id: requireNonemptyString(value.receipt_id, "invalid_atelier_upload_receipt"),
        project_id: requireNonemptyString(value.project_id, "invalid_atelier_upload_receipt"),
        client_request_id: requireNonemptyString(value.client_request_id, "invalid_atelier_upload_receipt"),
        request_fingerprint: uploadHash(value.request_fingerprint),
        base_revision: requireNonnegativeInteger(value.base_revision, "invalid_atelier_upload_receipt"),
        committed_revision: requirePositiveInteger(value.committed_revision, "invalid_atelier_upload_receipt"),
        created_at: requireFiniteNumber(value.created_at, "invalid_atelier_upload_receipt"),
        node_ids: requireStringArray(value.node_ids, "invalid_atelier_upload_receipt"),
        media: parseUploadMedia(value.media),
        target_node_id: requireNullableString(value.target_node_id, "invalid_atelier_upload_receipt"),
        source_node_id: requireNonemptyString(value.source_node_id, "invalid_atelier_upload_receipt"),
        replaced_node_id: null, previous_media: null,
    };
    if (!/^[A-Za-z0-9._:-]{1,128}$/.test(receipt.client_request_id) || receipt.committed_revision !== receipt.base_revision + 1
        || receipt.node_ids.some(id => !id) || new Set(receipt.node_ids).size !== receipt.node_ids.length) return uploadInvalid();
    if (receipt.operation === "create_media_node" ? (receipt.node_ids.length !== 1 || receipt.target_node_id !== null || receipt.node_ids[0] !== receipt.source_node_id)
        : (receipt.node_ids.length !== 2 || !receipt.target_node_id || receipt.node_ids[0] !== receipt.target_node_id || receipt.node_ids[1] !== receipt.source_node_id)) return uploadInvalid();
    return receipt;
};

const parseProject = (value: unknown): AtelierProject => {
    if (!isRecord(value)) {
        throw new AtelierProjectApiError("Invalid Atelier project response", "invalid_atelier_project");
    }
    return assertProjectGraphIntegrity({
        id: requireNonemptyString(value.id, "invalid_atelier_project"),
        title: requireString(value.title, "invalid_atelier_project"),
        description: requireString(value.description, "invalid_atelier_project"),
        source_project_id: requireNullableString(value.source_project_id, "invalid_atelier_project"),
        nodes: parseArray(value.nodes, parseNode, "invalid_atelier_project"),
        candidate_judgments: value.candidate_judgments === undefined ? [] : parseArray(value.candidate_judgments, parseCandidateJudgment, "invalid_atelier_candidate_judgments"),
        agent_policy: parsePolicy(value.agent_policy),
        agent_turns: parseArray(value.agent_turns, item => requireJsonRecord(item, "invalid_atelier_project"), "invalid_atelier_project"),
        sequence: parseArray(value.sequence, parseSequenceEntry, "invalid_atelier_project"),
        audio_tracks: parseArray(value.audio_tracks, parseAudioTrack, "invalid_atelier_project"),
        exports: parseArray(value.exports, parseExportRecord, "invalid_atelier_project"),
        node_delete_receipts: value.node_delete_receipts === undefined ? [] : parseArray(value.node_delete_receipts, parseDeleteReceipt, "invalid_atelier_project"),
        node_upload_receipts: value.node_upload_receipts === undefined ? [] : parseArray(value.node_upload_receipts, parseAtelierUploadReceipt, "invalid_atelier_project"),
        sequence_export_jobs: value.sequence_export_jobs === undefined ? [] : parseArray(value.sequence_export_jobs, parseSequenceExportJob, "invalid_atelier_project"),
        generation_attempts: value.generation_attempts === undefined ? [] : parseArray(value.generation_attempts, parseGenerationAttempt, "invalid_atelier_generation_ledger"),
        generation_request_receipts: value.generation_request_receipts === undefined ? [] : parseArray(value.generation_request_receipts, parseGenerationReceipt, "invalid_atelier_generation_ledger"),
        agent_compaction_summary: requireNullableString(value.agent_compaction_summary, "invalid_atelier_project"),
        compacted_through_turn_id: requireNullableString(value.compacted_through_turn_id, "invalid_atelier_project"),
        ...(value.import_provenance === undefined ? {} : { import_provenance: value.import_provenance === null ? null : parseImportProvenance(value.import_provenance) }),
        revision: requireNonnegativeInteger(value.revision, "invalid_atelier_project"),
        created_at: requireFiniteNumber(value.created_at, "invalid_atelier_project"),
        updated_at: requireFiniteNumber(value.updated_at, "invalid_atelier_project"),
    });
};

const assertProjectIdentity = (project: AtelierProject, projectId: string): AtelierProject => {
    if (project.id !== projectId) {
        throw new AtelierProjectApiError("Atelier project response did not match request", "atelier_project_identity_mismatch");
    }
    return project;
};

const assertRevisionNotBefore = (project: AtelierProject, expectedRevision: number): AtelierProject => {
    if (project.revision < expectedRevision) {
        throw new AtelierProjectApiError("Atelier project revision moved backwards", "atelier_project_revision_rollback");
    }
    return project;
};

const DELETE_RECEIPT_GROUP_FIELDS: Record<string, Set<string>> = {
    references: new Set(["reference_node_ids", "reference_image_urls", "reference_video_urls", "reference_audio_urls"]),
    parent: new Set(["parent_node_id", "reference_role"]),
    region: new Set(["region_id"]),
};

const hasExactFieldSet = (value: Record<string, unknown>, expected: Set<string>): boolean => {
    const keys = Object.keys(value);
    return keys.length === expected.size && keys.every(key => expected.has(key));
};

const generationCallKey = (turnId: string, callId: string): string => `${turnId}:${callId}`;
const generationAuthorityKey = (ref: AtelierGenerationAuthorizationRef): string => `${ref.turn_id}:${ref.call_id}:${ref.approval_id}:${ref.authorization_hash}`;

type AgentAuthorizationBindings = {
    approvals: Map<string, AtelierAgentApprovalRecord>;
    bindings: Map<string, AtelierAgentGenerationBinding>;
};

const sameAuthorizationRef = (a: AtelierGenerationAuthorizationRef, b: AtelierGenerationAuthorizationRef): boolean => (
    a.schema_version === b.schema_version
    && a.project_id === b.project_id
    && a.turn_id === b.turn_id
    && a.call_id === b.call_id
    && a.approval_id === b.approval_id
    && a.tool_name === b.tool_name
    && a.tool_schema_version === b.tool_schema_version
    && a.authorization_hash === b.authorization_hash
    && a.preview_fingerprint === b.preview_fingerprint
    && a.request_fingerprint === b.request_fingerprint
    && a.server_storage_identity === b.server_storage_identity
    && a.policy_snapshot_hash === b.policy_snapshot_hash
);

const approvalMatchesAuthorizationRef = (approval: AtelierAgentApprovalRecord, ref: AtelierGenerationAuthorizationRef): boolean => (
    approval.approval_id === ref.approval_id
    && approval.authorization_hash === ref.authorization_hash
);

const bindingMatchesAuthorizationRef = (binding: AtelierAgentGenerationBinding, ref: AtelierGenerationAuthorizationRef): boolean => (
    sameAuthorizationRef(binding.agent_authorization_ref, ref)
);

const SKILL_NAME_RE = /^[a-z][a-z0-9-]{2,63}$/;
const SKILL_VERSION_RE = /^\d+\.\d+\.\d+$/;
const LOWER_HASH_RE = /^[0-9a-f]{64}$/;

const parseProductSkillSnapshot = (value: unknown): AtelierProductSkillSnapshot => {
    const invalid = (): never => {
        throw new AtelierProjectApiError("Atelier Agent product Skill snapshot is invalid", "invalid_atelier_product_skill_snapshot");
    };
    if (!isRecord(value) || !hasExactKeys(value, [
        "schema_version", "name", "version", "title", "description", "category", "prompt_template", "expected_tools", "content_hash",
    ])) return invalid();
    if (value.schema_version !== 1
        || typeof value.name !== "string" || !SKILL_NAME_RE.test(value.name)
        || typeof value.version !== "string" || !SKILL_VERSION_RE.test(value.version)
        || typeof value.title !== "string" || !value.title || value.title.length > 80
        || typeof value.description !== "string" || !value.description || value.description.length > 280
        || typeof value.category !== "string" || !value.category
        || typeof value.prompt_template !== "string" || !value.prompt_template || value.prompt_template.length > 4000
        || !Array.isArray(value.expected_tools) || !value.expected_tools.every(item => typeof item === "string" && item.length > 0)
        || typeof value.content_hash !== "string" || !LOWER_HASH_RE.test(value.content_hash)) return invalid();
    return {
        schema_version: 1, name: value.name, version: value.version, title: value.title, description: value.description,
        category: value.category, prompt_template: value.prompt_template, expected_tools: [...value.expected_tools], content_hash: value.content_hash,
    };
};

const assertAgentLoopStartContext = (turn: JsonRecord): void => {
    const context = turn.loop_start_context;
    if (context === undefined || context === null) return;
    const invalid = (): never => {
        throw new AtelierProjectApiError("Atelier Agent loop intent is invalid", "invalid_atelier_agent_loop_context");
    };
    if (!isRecord(context)) return invalid();
    const baseValid = typeof context.user_message === "string"
        && (context.selected_node_id === null || (typeof context.selected_node_id === "string" && context.selected_node_id.length > 0))
        && (context.planner === "deterministic_core" || context.planner === "model_adapter")
        && typeof context.max_iterations === "number" && Number.isInteger(context.max_iterations)
        && context.max_iterations >= 1 && context.max_iterations <= 8;
    if (!baseValid) return invalid();
    let intent: AtelierAgentLoopStartContext;
    if (context.schema_version === 1) {
        if (!hasExactKeys(context, ["schema_version", "user_message", "selected_node_id", "planner", "skill_name", "max_iterations"]) || context.skill_name !== null) return invalid();
        intent = context as AtelierAgentLoopStartContext;
    } else if (context.schema_version === 2) {
        if (!hasExactKeys(context, ["schema_version", "user_message", "selected_node_id", "planner", "skill_name", "max_iterations", "skill_snapshot"])
            || typeof context.skill_name !== "string" || !SKILL_NAME_RE.test(context.skill_name)) return invalid();
        const snapshot = parseProductSkillSnapshot(context.skill_snapshot);
        if (snapshot.name !== context.skill_name) return invalid();
        intent = { ...context, skill_snapshot: snapshot } as AtelierAgentLoopStartContext;
    } else {
        return invalid();
    }
    if (turn.user_message !== intent.user_message || turn.skill_name !== intent.skill_name || turn.max_iterations !== intent.max_iterations
        || typeof turn.start_request_fingerprint !== "string" || !LOWER_HASH_RE.test(turn.start_request_fingerprint)) return invalid();
};

const collectAgentAuthorizationBindings = (project: AtelierProject): AgentAuthorizationBindings => {
    const turnIds = new Set<string>();
    const approvals = new Map<string, AtelierAgentApprovalRecord>();
    const bindings = new Map<string, AtelierAgentGenerationBinding>();
    const callBindings = new Map<string, AtelierAgentGenerationBinding>();
    for (const turn of project.agent_turns) {
        const turnId = turn.id;
        const turnProjectId = turn.project_id;
        if (typeof turnId !== "string" || turnId.length === 0) {
            throw new AtelierProjectApiError("Atelier project contains an empty agent turn id", "atelier_project_empty_agent_turn_id");
        }
        if (typeof turnProjectId !== "string" || turnProjectId !== project.id) {
            throw new AtelierProjectApiError("Atelier project agent turn owner mismatch", "atelier_project_agent_turn_owner_mismatch");
        }
        if (turnIds.has(turnId)) {
            throw new AtelierProjectApiError("Atelier project contains duplicate agent turn ids", "atelier_project_duplicate_agent_turn_id");
        }
        turnIds.add(turnId);
        assertAgentLoopStartContext(turn);
        const toolCalls = turn.tool_calls;
        if (!Array.isArray(toolCalls)) continue;
        const typedCallIds = new Set<string>();
        for (const call of toolCalls) {
            if (!isRecord(call)) continue;
            const hasBindingRecord = hasOwn(call, "preview_binding") && call.preview_binding !== null && call.preview_binding !== undefined;
            const hasApprovalRecord = hasOwn(call, "approval_record") && call.approval_record !== null && call.approval_record !== undefined;
            const hasCanvasBinding = call.canvas_approval_binding !== null && call.canvas_approval_binding !== undefined;
            const hasCanvasApproval = call.canvas_approval_record !== null && call.canvas_approval_record !== undefined;
            if (hasBindingRecord || hasApprovalRecord || hasCanvasBinding || hasCanvasApproval) {
                const id = requireNonemptyString(call.call_id, "invalid_atelier_agent_canvas_authorization");
                if (typedCallIds.has(id)) return canvasInvalid();
                typedCallIds.add(id);
            }
            if (hasCanvasBinding || hasCanvasApproval) {
                if (hasBindingRecord || hasApprovalRecord || !hasCanvasBinding) return canvasInvalid();
                const canvas = parseAgentCanvasBinding(call.canvas_approval_binding);
                const ref = canvas.canvas_authorization_ref;
                if (ref.project_id !== project.id || ref.turn_id !== turnId || ref.call_id !== call.call_id || ref.tool_name !== call.tool_name) return canvasInvalid();
                if (hasCanvasApproval) {
                    const approval = parseAgentApprovalRecord(call.canvas_approval_record);
                    if (approval.approval_id !== ref.approval_id || approval.authorization_hash !== ref.authorization_hash) return canvasInvalid();
                }
            }
            if (!hasBindingRecord && !hasApprovalRecord) continue;
            const callId = requireNonemptyString(call.call_id, "invalid_atelier_generation_ledger");
            const toolName = requireNonemptyString(call.tool_name, "invalid_atelier_generation_ledger");
            if (toolName !== AGENT_GENERATION_TOOL_NAME) return generationInvalid();
            const callKey = generationCallKey(turnId, callId);
            let binding: AtelierAgentGenerationBinding | null = null;
            if (hasBindingRecord) {
                binding = parseAgentGenerationBinding(call.preview_binding);
                const ref = binding.agent_authorization_ref;
                if (ref.project_id !== project.id || ref.turn_id !== turnId || ref.call_id !== callId) return generationInvalid();
                const authKey = generationAuthorityKey(ref);
                if (bindings.has(authKey) || callBindings.has(callKey)) return generationInvalid();
                bindings.set(authKey, binding);
                callBindings.set(callKey, binding);
            }
            if (hasApprovalRecord) {
                if (binding === null) return generationInvalid();
                const approval = parseAgentApprovalRecord(call.approval_record);
                const ref = binding.agent_authorization_ref;
                if (!approvalMatchesAuthorizationRef(approval, ref)) return generationInvalid();
                const authKey = generationAuthorityKey(ref);
                if (approvals.has(authKey)) return generationInvalid();
                approvals.set(authKey, approval);
            }
        }
    }
    return { approvals, bindings };
};


const isMediaGenerationAttempt = (attempt: AtelierGenerationAttempt): attempt is Extract<AtelierGenerationAttempt, { execution_contract: "atelier-provider-media-v2" }> => (
    "execution_contract" in attempt && attempt.execution_contract === MEDIA_GENERATION_CANDIDATE_CONTRACT
);

const receiptEntrypointMatchesAttempt = (entrypoint: AtelierGenerationEntrypoint, attempt: AtelierGenerationAttempt): boolean => {
    if (isMediaGenerationAttempt(attempt)) {
        return attempt.media_kind === "image" ? entrypoint === IMAGE_GENERATION_ENTRYPOINT : entrypoint === AUDIO_GENERATION_ENTRYPOINT;
    }
    return (entrypoint === ORDERED_GENERATION_ENTRYPOINT) === ("input_contract_version" in attempt);
};

const assertProjectGraphIntegrity = (project: AtelierProject): AtelierProject => {
    const uploadIds = new Set<string>();
    const uploadRequestIds = new Set<string>();
    for (const receipt of project.node_upload_receipts) {
        if (receipt.project_id !== project.id || receipt.committed_revision > project.revision
            || uploadIds.has(receipt.receipt_id) || uploadRequestIds.has(receipt.client_request_id)) return uploadInvalid();
        uploadIds.add(receipt.receipt_id);
        uploadRequestIds.add(receipt.client_request_id);
    }
    const nodeIds = new Set<string>();
    for (const node of project.nodes) {
        if (!node.id) {
            throw new AtelierProjectApiError("Atelier project contains an empty node id", "atelier_project_empty_node_id");
        }
        if (node.project_id !== project.id) {
            throw new AtelierProjectApiError("Atelier project graph owner mismatch", "atelier_project_graph_owner_mismatch");
        }
        if (nodeIds.has(node.id)) {
            throw new AtelierProjectApiError("Atelier project contains duplicate node ids", "atelier_project_duplicate_node_id");
        }
        nodeIds.add(node.id);
    }
    for (const exportRecord of project.exports) {
        if (exportRecord.project_id !== project.id) {
            throw new AtelierProjectApiError("Atelier project graph owner mismatch", "atelier_project_graph_owner_mismatch");
        }
    }
    const agentAuthorizationBindings = collectAgentAuthorizationBindings(project);
    const candidateKey = (nodeId: string, candidateId: string): string => JSON.stringify([nodeId, candidateId]);
    const generationCandidates = new Map<string, { nodeId: string; candidate: AtelierGenerationCandidateProjection }>();
    const reviewableCandidateKinds = new Map<string, "image" | "video" | "audio">();
    for (const node of project.nodes) {
        for (const field of ["candidates", "deleted_candidates"] as const) {
            const values = node.data[field];
            if (!Array.isArray(values)) continue;
            for (const item of values) {
                if (!isRecord(item)) continue;
                if (typeof item.id === "string" && item.id.length > 0) {
                    const mediaKind = item.execution_contract === MEDIA_GENERATION_CANDIDATE_CONTRACT && (item.media_kind === "image" || item.media_kind === "audio")
                        ? item.media_kind
                        : node.type === "image" || node.type === "audio" ? node.type : item.media_kind === undefined || item.media_kind === "video" ? "video" : null;
                    if (mediaKind !== null) reviewableCandidateKinds.set(candidateKey(node.id, item.id), mediaKind);
                }
                if (item.execution_contract !== GENERATION_CANDIDATE_CONTRACT && item.execution_contract !== MEDIA_GENERATION_CANDIDATE_CONTRACT) continue;
                const candidate = item as unknown as AtelierGenerationCandidateProjection;
                const key = candidateKey(node.id, candidate.id);
                if (generationCandidates.has(key)) return generationInvalid();
                if (candidate.execution_contract === MEDIA_GENERATION_CANDIDATE_CONTRACT) {
                    if (candidate.media_kind !== node.type) return generationInvalid();
                    if (candidate.authority === "imported") {
                        if (!project.import_provenance || candidate.attempt_id !== null || candidate.execution_fingerprint !== null
                            || candidate.status !== "completed" || candidate.error_code !== null || candidate.media_url === null
                            || !isCurrentImportedMediaCandidateUrl(candidate.media_url, candidate.media_kind, project.id)) return generationInvalid();
                    }
                }
                generationCandidates.set(key, { nodeId: node.id, candidate });
            }
        }
    }
    const generationAttempts = new Map<string, AtelierGenerationAttempt>();
    const generationCandidateIds = new Set<string>();
    const generationAuthorizationKeys = new Set<string>();
    for (const attempt of project.generation_attempts) {
        if (attempt.project_id !== project.id || attempt.provider_execution.attempt_id !== attempt.attempt_id) return generationInvalid();
        if (!isMediaGenerationAttempt(attempt) && attempt.schema_version === 2) {
            const ref = attempt.agent_authorization_ref;
            if (!ref) return generationInvalid();
            const authKey = generationAuthorityKey(ref);
            const approval = agentAuthorizationBindings.approvals.get(authKey);
            const binding = agentAuthorizationBindings.bindings.get(authKey);
            if (generationAuthorizationKeys.has(authKey) || !approval || !binding || !approvalMatchesAuthorizationRef(approval, ref) || !bindingMatchesAuthorizationRef(binding, ref)
                || providerExecutionStableIdentity(attempt.provider_execution) !== providerExecutionStableIdentity(binding.provider_execution)) return generationInvalid();
            generationAuthorizationKeys.add(authKey);
        }
        if (generationAttempts.has(attempt.attempt_id) || generationCandidateIds.has(attempt.candidate_id)) return generationInvalid();
        generationAttempts.set(attempt.attempt_id, attempt);
        generationCandidateIds.add(attempt.candidate_id);
        const owner = nodeIds.has(attempt.node_id);
        if (GENERATION_PENDING_STATUSES.has(attempt.status) && !owner) return generationInvalid();
        const record = attempt.provider_execution;
        if (attempt.status === "pending" && record.phase !== "accepted") return generationInvalid();
        if (record.phase === "accepted" && (record.handle !== null || record.observation !== null || record.prepared_artifact !== null || record.artifact !== null)) return generationInvalid();
        if (record.phase === "cancelled" && attempt.status !== "cancelled") return generationInvalid();
        if (record.phase === "completed" && attempt.status !== "completed") return generationInvalid();
        if (attempt.status !== "completed" && (attempt.output_ref !== null || record.artifact !== null)) return generationInvalid();
        if (attempt.status === "completed") {
            if (record.phase !== "completed" || record.artifact === null || attempt.output_ref === null || record.artifact.relative_path !== attempt.output_ref) return generationInvalid();
        }
        if (attempt.status === "cancelled" && (record.phase !== "cancelled" || attempt.output_ref !== null)) return generationInvalid();
        if (attempt.status === "failed" && record.phase === "submitting") return generationInvalid();
        if ((record.phase === "submitted" || record.phase === "observed") && record.handle === null) return generationInvalid();
    }
    const generationReceiptAttempts = new Set<string>();
    const generationReceiptKeys = new Set<string>();
    for (const receipt of project.generation_request_receipts) {
        const key = `${receipt.entrypoint}:${receipt.client_request_id}`;
        if (receipt.project_id !== project.id || receipt.committed_revision > project.revision || generationReceiptKeys.has(key)) return generationInvalid();
        generationReceiptKeys.add(key);
        const members = generationReceiptMembers(receipt);
        for (const member of members) {
            if (generationReceiptAttempts.has(member.attempt_id)) return generationInvalid();
            generationReceiptAttempts.add(member.attempt_id);
            const attempt = generationAttempts.get(member.attempt_id);
            if (!attempt || receipt.node_id !== attempt.node_id || member.candidate_id !== attempt.candidate_id
                || member.execution_fingerprint !== attempt.provider_execution.fingerprint) return generationInvalid();
            if (!receiptEntrypointMatchesAttempt(receipt.entrypoint, attempt)) return generationInvalid();
            if (!isMediaGenerationAttempt(attempt) && attempt.schema_version === 2) {
                if (!attempt.agent_authorization_ref || receipt.request_fingerprint !== attempt.agent_authorization_ref.request_fingerprint) return generationInvalid();
            }
        }
    }
    for (const attempt of project.generation_attempts) {
        if (!generationReceiptAttempts.has(attempt.attempt_id)) return generationInvalid();
        const projected = generationCandidates.get(candidateKey(attempt.node_id, attempt.candidate_id))?.candidate ?? null;
        if (projected === null) {
            if (!nodeIds.has(attempt.node_id) && GENERATION_TERMINAL_STATUSES.has(attempt.status)) continue;
            return generationInvalid();
        }
        if (isMediaGenerationAttempt(attempt)) {
            if (projected.execution_contract !== MEDIA_GENERATION_CANDIDATE_CONTRACT || projected.media_kind !== attempt.media_kind
                || projected.attempt_id !== attempt.attempt_id || projected.execution_fingerprint !== attempt.provider_execution.fingerprint
                || projected.status !== attempt.status || projected.error_code !== attempt.error_code
                || (attempt.status === "completed" && projected.media_url !== attempt.output_ref)) return generationInvalid();
        } else if (projected.execution_contract !== GENERATION_CANDIDATE_CONTRACT || projected.attempt_id !== attempt.attempt_id
            || projected.status !== attempt.status || projected.error_code !== attempt.error_code
            || ("created_at" in projected && projected.created_at !== attempt.created_at) || ("updated_at" in projected && projected.updated_at !== attempt.updated_at)
            || (attempt.status === "completed" && projected.video_url !== attempt.output_ref)) return generationInvalid();
    }
    for (const { nodeId, candidate } of Array.from(generationCandidates.values())) {
        if (candidate.attempt_id === null) continue;
        const attempt = generationAttempts.get(candidate.attempt_id);
        if (!attempt || attempt.node_id !== nodeId || attempt.candidate_id !== candidate.id || !generationReceiptAttempts.has(attempt.attempt_id)) return generationInvalid();
    }
    const judgmentIds = new Set<string>();
    const latestByCandidate = new Map<string, string>();
    for (const judgment of project.candidate_judgments ?? []) {
        if (judgment.project_id !== project.id || judgmentIds.has(judgment.id)) return judgmentInvalid();
        judgmentIds.add(judgment.id);
        const key = candidateKey(judgment.node_id, judgment.candidate_id);
        const previousId = latestByCandidate.get(key);
        if ((previousId ?? null) !== (judgment.supersedes_judgment_id ?? null)) return judgmentInvalid();
        if (judgment.supersedes_judgment_id !== undefined && !judgmentIds.has(judgment.supersedes_judgment_id)) return judgmentInvalid();
        latestByCandidate.set(key, judgment.id);
        const candidateKind = reviewableCandidateKinds.get(key) ?? null;
        if (candidateKind) {
            if (candidateKind !== judgment.media_kind) return judgmentInvalid();
        } else if (nodeIds.has(judgment.node_id)) {
            return judgmentInvalid();
        }
    }
    const receiptIds = new Set<string>();
    for (const receipt of project.node_delete_receipts) {
        if (receipt.project_id !== project.id) {
            throw new AtelierProjectApiError("Atelier delete receipt owner mismatch", "atelier_delete_receipt_owner_mismatch");
        }
        if (receiptIds.has(receipt.receipt_id)) {
            throw new AtelierProjectApiError("Atelier project contains duplicate delete receipt ids", "atelier_delete_receipt_duplicate");
        }
        receiptIds.add(receipt.receipt_id);
        if (receipt.base_revision > project.revision) {
            throw new AtelierProjectApiError("Atelier delete receipt base revision is in the future", "atelier_delete_receipt_revision_invalid");
        }
        if ((receipt.restored_at === null) !== (receipt.restore_base_revision === null)) {
            throw new AtelierProjectApiError("Atelier delete receipt restore fields are inconsistent", "atelier_delete_receipt_restore_fields_inconsistent");
        }
        if (receipt.restore_base_revision !== null && (receipt.restore_base_revision < receipt.base_revision || receipt.restore_base_revision > project.revision)) {
            throw new AtelierProjectApiError("Atelier delete receipt restore revision is invalid", "atelier_delete_receipt_revision_invalid");
        }
        if (receipt.node_ids.length === 0 || receipt.node_ids.length !== new Set(receipt.node_ids).size) {
            throw new AtelierProjectApiError("Atelier delete receipt node ids are invalid", "atelier_delete_receipt_node_ids_invalid");
        }
        const snapshotIds = receipt.node_snapshots.map(snapshot => snapshot.node.id);
        if (snapshotIds.length !== receipt.node_ids.length || snapshotIds.some((nodeId, index) => nodeId !== receipt.node_ids[index])) {
            throw new AtelierProjectApiError("Atelier delete receipt snapshot ids do not match node ids", "atelier_delete_receipt_snapshot_mismatch");
        }
        const snapshotIndices = receipt.node_snapshots.map(snapshot => snapshot.original_index);
        if (snapshotIndices.length !== new Set(snapshotIndices).size) {
            throw new AtelierProjectApiError("Atelier delete receipt contains duplicate snapshot indexes", "atelier_delete_receipt_duplicate_snapshot_index");
        }
        const groupKeys = new Set<string>();
        for (const snapshot of receipt.node_snapshots) {
            if (snapshot.node.project_id !== project.id) {
                throw new AtelierProjectApiError("Atelier delete receipt owner mismatch", "atelier_delete_receipt_owner_mismatch");
            }
            for (const proof of snapshot.source_proofs) {
                if (proof.target_node_id !== snapshot.node.id) {
                    throw new AtelierProjectApiError("Atelier delete receipt source proof owner mismatch", "atelier_delete_receipt_source_proof_owner_mismatch");
                }
            }
        }
        for (const group of receipt.data_change_groups) {
            const groupKey = `${group.node_id}:${group.group}`;
            if (groupKeys.has(groupKey)) {
                throw new AtelierProjectApiError("Atelier delete receipt contains duplicate data groups", "atelier_delete_receipt_duplicate_group");
            }
            groupKeys.add(groupKey);
            const allowed = DELETE_RECEIPT_GROUP_FIELDS[group.group];
            if (!allowed || !hasExactFieldSet(group.before, allowed) || !hasExactFieldSet(group.after, allowed)) {
                throw new AtelierProjectApiError("Atelier delete receipt data group fields are invalid", "atelier_delete_receipt_group_fields_invalid");
            }
            for (const proof of group.source_proofs) {
                if (proof.target_node_id !== group.node_id) {
                    throw new AtelierProjectApiError("Atelier delete receipt source proof owner mismatch", "atelier_delete_receipt_source_proof_owner_mismatch");
                }
            }
        }
        if (receipt.restored_at === null) {
            for (const snapshotId of snapshotIds) {
                if (nodeIds.has(snapshotId)) {
                    throw new AtelierProjectApiError("Unconsumed Atelier delete receipt overlaps live node ids", "atelier_delete_receipt_live_overlap");
                }
            }
        }
    }
    const exportJobIds = new Set<string>();
    const exportJobRequestIds = new Set<string>();
    const exportsByJobId = new Map<string, AtelierExportRecord>();
    for (const exportRecord of project.exports) {
        const publicationProof = exportRecord.publication_proof ?? null;
        const importedProof = exportRecord.imported_proof ?? null;
        const jobId = exportRecord.job_id ?? null;
        const snapshotDigest = exportRecord.snapshot_digest ?? null;
        if (importedProof !== null && (publicationProof !== null || jobId !== null || snapshotDigest !== null
            || importedProof.project_id !== project.id || importedProof.export_id !== exportRecord.id
            || importedProof.relative_path !== exportRecord.video_url)) return importInvalid();
        if (publicationProof !== null) {
            if (jobId === null || snapshotDigest === null) {
                throw new AtelierProjectApiError("Atelier export proof fields are incomplete", "atelier_export_proof_incomplete");
            }
            if (publicationProof.project_id !== project.id || publicationProof.job_id !== jobId
                || publicationProof.relative_path !== exportRecord.video_url || publicationProof.snapshot_digest !== snapshotDigest) {
                throw new AtelierProjectApiError("Atelier export proof does not match export", "atelier_export_proof_mismatch");
            }
            if (exportsByJobId.has(jobId)) {
                throw new AtelierProjectApiError("Atelier project contains duplicate job-backed exports", "atelier_export_duplicate_job");
            }
            exportsByJobId.set(jobId, exportRecord);
        } else if (jobId !== null || snapshotDigest !== null) {
            throw new AtelierProjectApiError("Atelier export proof fields are incomplete", "atelier_export_proof_incomplete");
        }
    }
    for (const job of project.sequence_export_jobs) {
        if (job.project_id !== project.id || job.base_revision > project.revision) {
            throw new AtelierProjectApiError("Atelier export job owner or revision mismatch", "atelier_export_job_owner_mismatch");
        }
        if (exportJobIds.has(job.job_id) || exportJobRequestIds.has(job.client_request_id)) {
            throw new AtelierProjectApiError("Atelier project contains duplicate export jobs", "atelier_export_job_duplicate");
        }
        exportJobIds.add(job.job_id);
        exportJobRequestIds.add(job.client_request_id);
        if (job.status === "completed") {
            const exportRecord = exportsByJobId.get(job.job_id);
            const proof = job.prepared_proof;
            if (!exportRecord || exportRecord.id !== job.export_id || proof === null || JSON.stringify(exportRecord.publication_proof) !== JSON.stringify(proof)
                || exportRecord.timeline_snapshot === undefined || JSON.stringify(exportRecord.timeline_snapshot) !== JSON.stringify(job.snapshot)
                || JSON.stringify(exportRecord.source_entries) !== JSON.stringify(job.source_entries)
                || exportRecord.filename !== `${job.output_ref.split("/").pop()}`
                || exportRecord.clip_count !== job.source_entries.length
                || exportRecord.size_mb !== proof.size_bytes / 1024 / 1024) {
                throw new AtelierProjectApiError("Atelier completed export does not match job", "atelier_export_job_binding_mismatch");
            }
        }
    }

    return project;
};

const assertUniqueProjectList = (projects: AtelierProject[]): AtelierProject[] => {
    const projectIds = new Set<string>();
    for (const project of projects) {
        if (projectIds.has(project.id)) {
            throw new AtelierProjectApiError("Atelier project list contains duplicate project ids", "atelier_project_duplicate_project_id");
        }
        projectIds.add(project.id);
    }
    return projects;
};

const parseErrorResponse = async (response: Response): Promise<{ message: string; code?: string }> => {
    const fallback = { message: `Atelier project request failed with status ${response.status}` };
    try {
        const raw = await response.text();
        if (!raw) return fallback;
        const parsed = JSON.parse(raw) as unknown;
        if (typeof parsed === "string") return { message: parsed };
        if (isRecord(parsed)) {
            const detail = parsed.detail;
            if (typeof detail === "string") return { message: detail };
            if (isRecord(detail) && typeof detail.detail === "string") return {
                message: detail.detail,
                code: typeof detail.code === "string" ? detail.code : undefined,
            };
            if (typeof parsed.message === "string") return { message: parsed.message };
        }
    } catch {
        return fallback;
    }
    return fallback;
};

async function readJson(response: Response): Promise<unknown> {
    try {
        return await response.json();
    } catch (error) {
        if (error instanceof AtelierProjectApiError) throw error;
        throw new AtelierProjectApiError("Invalid Atelier project JSON response", "invalid_atelier_project_response");
    }
}

async function requestJson(path: string, init: RequestInit = {}, allowedErrorCodes: readonly string[] = []): Promise<unknown> {
    const environment = requireActiveRuntimeEnvironment();
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
    }
    const response = await appAccessFetch(`${API_URL}${path}`, {
        cache: "no-store",
        ...init,
        headers,
    });
    const assertCurrentEnvironment = () => {
        if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
            throw new AtelierProjectApiError("Runtime environment changed while Atelier project request was in flight", "environment_changed");
        }
    };
    if (!response.ok) {
        const failure = await parseErrorResponse(response);
        assertCurrentEnvironment();
        const code = failure.code && allowedErrorCodes.includes(failure.code) ? failure.code : "atelier_project_request_failed";
        throw new AtelierProjectApiError(failure.message, code, response.status);
    }
    try {
        const body = await readJson(response);
        assertCurrentEnvironment();
        return body;
    } catch (error) {
        assertCurrentEnvironment();
        throw error;
    }
}

const projectPath = (projectId: string): string => `${ATELIER_PROJECTS_PATH}/${encodeURIComponent(requireProjectIdInput(projectId))}`;

const body = (value: Record<string, unknown>): string => JSON.stringify(value);

export const atelierProjectApi = {
    async listProjects(): Promise<AtelierProject[]> {
        return assertUniqueProjectList(parseArray(await requestJson(ATELIER_PROJECTS_PATH, { method: "GET" }), parseProject, "invalid_atelier_project_list"));
    },

    async getProject(projectId: string): Promise<AtelierProject> {
        return assertProjectIdentity(parseProject(await requestJson(projectPath(projectId), { method: "GET" })), projectId);
    },

    async createProject(input: CreateAtelierProjectInput): Promise<AtelierProject> {
        const payload: Record<string, unknown> = { title: input.title };
        if (input.description !== undefined) payload.description = input.description;
        if (hasOwn(input, "sourceProjectId") && input.sourceProjectId !== undefined) {
            payload.source_project_id = input.sourceProjectId;
        }
        const project = parseProject(await requestJson(ATELIER_PROJECTS_PATH, {
            method: "POST",
            body: body(payload),
        }));
        return assertRevisionNotBefore(project, 0);
    },

    async updateProject(projectId: string, input: UpdateAtelierProjectInput): Promise<AtelierProject> {
        const expectedRevision = requireRevisionInput(input.expectedRevision);
        const payload: Record<string, unknown> = { expected_revision: expectedRevision };
        if (input.title !== undefined) payload.title = input.title;
        if (input.description !== undefined) payload.description = input.description;
        if (hasOwn(input, "sourceProjectId") && input.sourceProjectId !== undefined) {
            payload.source_project_id = input.sourceProjectId;
        }
        const project = assertProjectIdentity(parseProject(await requestJson(projectPath(projectId), {
            method: "PUT",
            body: body(payload),
        })), projectId);
        return assertRevisionNotBefore(project, expectedRevision);
    },

    async deleteProject(projectId: string, input: DeleteAtelierProjectInput): Promise<DeleteAtelierProjectResult> {
        const expectedRevision = requireRevisionInput(input.expectedRevision);
        const value = await requestJson(projectPath(projectId), {
            method: "DELETE",
            body: body({ expected_revision: expectedRevision }),
        });
        if (!isRecord(value) || value.ok !== true) {
            throw new AtelierProjectApiError("Invalid Atelier project delete confirmation", "invalid_atelier_project_delete_ack");
        }
        return { ok: true };
    },
};

export {
    assertProjectIdentity as assertAtelierProjectIdentity,
    assertRevisionNotBefore as assertAtelierProjectRevisionNotBefore,
    hasOwn as hasOwnAtelierField,
    isRecord as isAtelierRecord,
    parseProject as parseAtelierProject,
    parseSequenceEntry as parseAtelierSequenceEntry,
    parseAudioTrack as parseAtelierAudioTrack,
    parseExportRecord as parseAtelierExportRecord,
    parseCandidateBranchSource as parseAtelierCandidateBranchSource,
    parseCandidateJudgment as parseAtelierCandidateJudgment,
    parseGenerationAttempt as parseAtelierGenerationAttempt,
    parseGenerationCandidate as parseAtelierGenerationCandidate,
    parseGenerationReceipt as parseAtelierGenerationRequestReceipt,
    parseSequenceExportJob as parseAtelierSequenceExportJob,
    parseSequencePublicationProof as parseAtelierSequencePublicationProof,
    parseRestoreSkip as parseAtelierNodeRestoreSkip,
    requestJson as requestAtelierJson,
    requireProjectIdInput as requireAtelierProjectIdInput,
    requireRevisionInput as requireAtelierRevisionInput,
};
