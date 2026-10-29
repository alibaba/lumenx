export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonRecord = { [key: string]: JsonValue };

export type AtelierApprovalMode = "untrusted" | "on_failure" | "on_request" | "never";
export type AtelierCandidateJudgmentVerdict = "unreviewed" | "keep" | "reject";

export interface AtelierAgentPolicy {
    approval_mode: AtelierApprovalMode;
    allowed_tools: string[];
    max_nodes_per_action: number;
    max_tokens_per_turn: number;
    enable_reviewer: boolean;
    max_generation_cost_per_turn: number;
    updated_at: number;
}

export interface AtelierNode {
    id: string;
    project_id: string;
    type: string;
    title: string;
    prompt: string;
    status: string;
    x: number;
    y: number;
    width: number;
    height: number;
    source_project_id: string | null;
    frame_id: string | null;
    asset_id: string | null;
    video_task_id: string | null;
    media_urls: string[];
    data: JsonRecord;
    created_by: string;
    created_at: number;
    updated_at: number;
}


export type AtelierBranchRecipeStatus = "available" | "unavailable_imported" | "unavailable_legacy" | "unavailable_inputs" | "unsupported";

export interface AtelierCandidateBranchSource {
    schema_version: 1;
    origin_project_id: string;
    origin_node_id: string;
    origin_candidate_id: string;
    media_kind: "image" | "video" | "audio";
    media_url: string;
    media_sha256: string;
    media_size_bytes: number;
    recipe_status: AtelierBranchRecipeStatus;
}

export interface AtelierCandidateJudgment {
    schema_version: 1;
    id: string;
    project_id: string;
    node_id: string;
    candidate_id: string;
    media_kind: "image" | "video" | "audio";
    verdict: AtelierCandidateJudgmentVerdict;
    reason: string;
    created_at: number;
    supersedes_judgment_id?: string;
}

export interface AtelierSequenceTransition {
    kind: "cut" | "fade";
    duration: number;
}

export interface AtelierSequenceEntry {
    parentId: string;
    candidateId: string | null;
    id?: string | null;
    trimStart?: number | null;
    trimEnd?: number | null;
    gain: number;
    transition: AtelierSequenceTransition;
}

export interface AtelierAudioTrack {
    id: string;
    node_id: string;
    start: number;
    trim_start?: number | null;
    trim_end?: number | null;
    gain: number;
    loop: boolean;
}

export interface AtelierSequencePublicationProof {
    schema_version: 1;
    job_id: string;
    project_id: string;
    relative_path: string;
    sha256: string;
    size_bytes: number;
    duration: number;
    width: number;
    height: number;
    fps: number;
    snapshot_digest: string;
    prepared_at: number;
}

export type AtelierSequenceExportJobStatus = "queued" | "running" | "cancelling" | "cancelled" | "failed" | "repair_required" | "completed";
export type AtelierSequenceExportContractVersion = "atelier-sequence-export-v1" | "atelier-sequence-export-redo-v1";

export interface AtelierSequenceExportJob {
    schema_version: 1;
    job_id: string;
    project_id: string;
    status: AtelierSequenceExportJobStatus;
    request_contract_version: AtelierSequenceExportContractVersion;
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
    snapshot: JsonRecord;
    source_entries: AtelierSequenceEntry[];
    source_audio_tracks: AtelierAudioTrack[];
    output_ref: string;
    prepared_proof: AtelierSequencePublicationProof | null;
    export_id: string | null;
    error_code: string | null;
    error_message: string | null;
    cancelled_at: number | null;
}

export type AtelierExportDeletionFileStatus = "retained" | "pending" | "deleted" | "already_missing" | "blocked";

export interface AtelierExportDeletionReceipt {
    client_request_id: string;
    delete_file: boolean;
    deleted_at: number;
    file_status: AtelierExportDeletionFileStatus;
    checked_at: number | null;
    error_code: string | null;
    error_message: string | null;
}

export interface AtelierExportRecord {
    id: string;
    project_id: string;
    created_at: number;
    clip_count: number;
    size_mb: number;
    video_url: string;
    filename: string;
    source_entries: AtelierSequenceEntry[];
    timeline_snapshot?: JsonRecord | null;
    publication_proof?: AtelierSequencePublicationProof | null;
    job_id?: string | null;
    snapshot_digest?: string | null;
    deletion?: AtelierExportDeletionReceipt | null;
    imported_proof?: AtelierImportedExportProof | null;
}

export interface AtelierImportedExportProof {
    schema_version: 1;
    project_id: string;
    export_id: string;
    relative_path: string;
    sha256: string;
    size_bytes: number;
    source_project_id: string;
    source_export_id: string;
    source_storage_id: string;
    imported_at: number;
}

export interface AtelierImportedAgentHistoryMessage {
    role: "user" | "assistant" | "tool";
    content: string;
    name?: string;
    status?: string;
    error_code?: string;
}

export interface AtelierImportedAgentHistoryEntry {
    schema_version: 1;
    source_turn_id: string;
    status: string;
    user_message: string;
    assistant_response: string | null;
    messages: AtelierImportedAgentHistoryMessage[];
    created_at: number | null;
    completed_at: number | null;
    source_refs: Record<string, string>;
    target_refs: Record<string, string>;
}

export interface AtelierImportProvenance {
    schema_version: 1;
    source_project_id: string;
    source_storage_id: string;
    imported_at: number;
    agent_history: AtelierImportedAgentHistoryEntry[];
}

export type AtelierGenerationAttemptStatus = "pending" | "processing" | "needs_confirmation" | "failed" | "cancelled" | "completed";
export type ProviderExecutionPhase = "accepted" | "cancelled" | "submitting" | "submitted" | "observed" | "completed";

export interface ProviderAsyncArtifactProof {
    schema_version: 1;
    attempt_id: string;
    handle: JsonRecord;
    relative_path: string;
    sha256: string;
    size_bytes: number;
}

export interface ProviderSyncArtifactProof {
    schema_version: 2;
    contract_version: "provider-sync-artifact-v1";
    attempt_id: string;
    media_kind: "image" | "audio";
    relative_path: string;
    output_ref: string;
    sha256: string;
    size_bytes: number;
}

export interface ProviderAsyncExecutionRecord {
    schema_version: 1;
    attempt_id: string;
    phase: ProviderExecutionPhase;
    provider_model_id: string;
    intent: JsonRecord;
    media: JsonRecord[];
    scope: JsonRecord;
    credential_ref: string;
    fingerprint: string;
    handle: JsonRecord | null;
    observation: JsonRecord | null;
    prepared_artifact: JsonRecord | null;
    artifact: JsonRecord | null;
}

export interface ProviderSyncExecutionRecord {
    schema_version: 2;
    execution_kind: "sync_artifact";
    media_kind: "image" | "audio";
    output_ref: string;
    attempt_id: string;
    phase: ProviderExecutionPhase;
    provider_model_id: string;
    intent: JsonRecord;
    media: JsonRecord[];
    scope: JsonRecord;
    credential_ref: string;
    fingerprint: string;
    handle: null;
    observation: null;
    prepared_artifact: JsonRecord | null;
    artifact: JsonRecord | null;
}

export type ProviderExecutionRecord = ProviderAsyncExecutionRecord | ProviderSyncExecutionRecord;

export interface AtelierGenerationFirstFrameSource {
    node_id: string;
    media_url: string;
}

export type AtelierGenerationEntrypoint = "atelier.provider_video.v1" | "atelier.provider_video.v2" | "atelier.provider_image.v1" | "atelier.provider_audio.v1";
export type AtelierOrderedVideoModel = "wan/wan3.0-video#video" | "wan/wan3.0-video-prime#video";
export type AtelierOrderedVideoMode = "t2v" | "i2v" | "r2v";
export type AtelierOrderedMediaRole = "first_frame" | "last_frame" | "reference_image" | "reference_video" | "reference_audio";

export interface AtelierOrderedMediaSelection {
    role: AtelierOrderedMediaRole;
    ordinal: number;
    node_id: string;
    media_url: string;
}

export type AtelierOrderedGenerationParams = Partial<{
    duration: number;
    resolution: string;
    ratio: string;
    seed: number;
    prompt_extend: boolean;
    watermark: boolean;
    audio: boolean;
}>;

export interface AtelierGenerationAuthorizationRef {
    schema_version: 1;
    project_id: string;
    turn_id: string;
    call_id: string;
    approval_id: string;
    tool_name: "generation.createVideoCandidates";
    tool_schema_version: string;
    authorization_hash: string;
    preview_fingerprint: string;
    request_fingerprint: string;
    server_storage_identity: string;
    policy_snapshot_hash: string;
}

export interface AtelierAgentApprovalRecord {
    schema_version: 1;
    approval_id: string;
    authorization_hash: string;
    decision: "approved" | "denied" | "revoked";
    decided_at: number;
}

export interface AtelierAgentGenerationBinding {
    schema_version: 1;
    preview_public_record: JsonRecord;
    provider_execution: ProviderExecutionRecord;
    agent_authorization_ref: AtelierGenerationAuthorizationRef;
    created_at: number;
}

export interface AtelierAgentCanvasAuthorizationRef {
    schema_version: 1;
    project_id: string;
    turn_id: string;
    call_id: string;
    approval_id: string;
    tool_name: "canvas.createVideoNode" | "canvas.updateNodePrompt";
    tool_schema_version: string;
    authorization_hash: string;
    arguments_fingerprint: string;
    target_fingerprint: string;
    server_storage_identity: string;
    policy_snapshot_hash: string;
}

export interface AtelierAgentCanvasBinding {
    schema_version: 1;
    normalized_arguments: JsonRecord;
    target_state: JsonRecord;
    canvas_authorization_ref: AtelierAgentCanvasAuthorizationRef;
    created_at: number;
}

export type AtelierAgentCanvasApprovalRecord = AtelierAgentApprovalRecord;

export interface AtelierProductSkillCatalogItem {
    schema_version: 1;
    name: string;
    version: string;
    title: string;
    description: string;
    category: "story" | "shot" | "review" | "sequence";
    expected_tools: string[];
    default_iteration_cap: number;
    content_hash: string;
    enabled: boolean;
    disabled_reason: string | null;
}

export interface AtelierProductSkillSnapshot extends JsonRecord {
    schema_version: 1;
    name: string;
    version: string;
    title: string;
    description: string;
    category: string;
    prompt_template: string;
    expected_tools: string[];
    content_hash: string;
}

export type AtelierAgentLoopStartContext = JsonRecord & {
    schema_version: 1 | 2;
    user_message: string;
    selected_node_id: string | null;
    planner: "deterministic_core" | "model_adapter";
    skill_name: string | null;
    max_iterations: number;
    skill_snapshot?: AtelierProductSkillSnapshot | null;
};

interface AtelierGenerationAttemptBase {
    schema_version: 1 | 2;
    project_id: string;
    node_id: string;
    candidate_id: string;
    attempt_id: string;
    attempt_revision: number;
    status: AtelierGenerationAttemptStatus;
    error_code: string | null;
    output_ref: string | null;
    provider_execution: ProviderExecutionRecord;
    agent_authorization_ref?: AtelierGenerationAuthorizationRef;
    created_at: number;
    updated_at: number;
}

export interface AtelierLegacyGenerationAttempt extends AtelierGenerationAttemptBase {
    first_frame_source: AtelierGenerationFirstFrameSource;
}

export interface AtelierOrderedGenerationAttempt extends AtelierGenerationAttemptBase {
    input_contract_version: "ordered-media-v1";
    input_media: AtelierOrderedMediaSelection[];
}

export interface AtelierMediaGenerationAttempt extends AtelierGenerationAttemptBase {
    schema_version: 1;
    execution_contract: "atelier-provider-media-v2";
    media_kind: "image" | "audio";
    provider_execution: ProviderSyncExecutionRecord;
}

export type AtelierGenerationAttempt = AtelierLegacyGenerationAttempt | AtelierOrderedGenerationAttempt | AtelierMediaGenerationAttempt;

export interface AtelierGenerationRequestReceiptMember {
    candidate_id: string;
    attempt_id: string;
    execution_fingerprint: string;
}

export interface AtelierGenerationRequestReceipt {
    schema_version: 1 | 2;
    project_id: string;
    entrypoint: AtelierGenerationEntrypoint;
    request_contract_version: "request-json-v1";
    client_request_id: string;
    request_fingerprint: string;
    node_id: string;
    candidate_id?: string;
    attempt_id?: string;
    execution_fingerprint?: string;
    members?: AtelierGenerationRequestReceiptMember[];
    base_revision: number;
    committed_revision: number;
    created_at: number;
}

export interface AtelierVideoGenerationCandidateProjection {
    execution_contract: "atelier-provider-video-v1";
    id: string;
    attempt_id: string;
    media_kind: "video";
    status: AtelierGenerationAttemptStatus;
    video_url: string | null;
    error_code: string | null;
    created_at: number;
    updated_at: number;
    media_url?: never;
}

export interface AtelierMediaGenerationCandidateProjection {
    schema_version: 2;
    execution_contract: "atelier-provider-media-v2";
    id: string;
    attempt_id: string | null;
    media_kind: "image" | "audio";
    status: AtelierGenerationAttemptStatus;
    media_url: string | null;
    error_code: string | null;
    authority: "provider" | "imported";
    execution_fingerprint: string | null;
    video_url?: never;
    created_at?: never;
    updated_at?: never;
}

export type AtelierGenerationCandidateProjection = AtelierVideoGenerationCandidateProjection | AtelierMediaGenerationCandidateProjection;

export type AtelierLegacyGenerationParams = Partial<{
    duration: number;
    resolution: string;
    seed: number;
    negative_prompt: string;
    prompt_extend: boolean;
    watermark: boolean;
}>;

export interface AtelierVideoCandidateRequest {
    expected_revision: number;
    client_request_id: string;
    model_id: string;
    mode: "i2v";
    prompt: string;
    first_frame: AtelierGenerationFirstFrameSource;
    params?: AtelierLegacyGenerationParams;
    batch_size?: number;
}

export interface AtelierOrderedVideoCandidateRequest {
    expected_revision: number;
    client_request_id: string;
    contract_version: "ordered-media-v1";
    model_id: AtelierOrderedVideoModel;
    mode: AtelierOrderedVideoMode;
    prompt: string;
    media: AtelierOrderedMediaSelection[];
    params?: AtelierOrderedGenerationParams;
    batch_size?: number;
}

export type AtelierImageGenerationParams = Partial<{
    size: string;
    seed: number;
    negative_prompt: string;
    prompt_extend: boolean;
    watermark: boolean;
}>;

export interface AtelierImageReferenceSelection {
    role: "reference_image";
    ordinal: number;
    node_id: string;
    media_url: string;
}

export interface AtelierImageCandidateRequest {
    entrypoint: "atelier.provider_image.v1";
    expected_revision: number;
    client_request_id: string;
    model_id: string;
    mode: "t2i" | "i2i";
    prompt: string;
    media: AtelierImageReferenceSelection[];
    params?: AtelierImageGenerationParams;
    batch_size?: number;
}

export type AtelierTTSGenerationParams = Partial<{
    speech_rate: number;
    pitch_rate: number;
    volume: number;
    instructions: string;
}>;

export interface AtelierAudioCandidateRequest {
    entrypoint: "atelier.provider_audio.v1";
    expected_revision: number;
    client_request_id: string;
    voice: string;
    text: string;
    params?: AtelierTTSGenerationParams;
}

export type AtelierGenerationRequest = AtelierVideoCandidateRequest | AtelierOrderedVideoCandidateRequest | AtelierImageCandidateRequest | AtelierAudioCandidateRequest;

export interface AtelierGenerationAttemptIdentityRequest {
    candidate_id: string;
    expected_attempt_revision: number;
    execution_fingerprint: string;
}

export interface AtelierGenerationAdmissionMember {
    attempt: AtelierGenerationAttempt;
    candidate: AtelierGenerationCandidateProjection | null;
}

interface AtelierGenerationAdmissionAckBase {
    project: AtelierProject;
    receipt: AtelierGenerationRequestReceipt;
    members: AtelierGenerationAdmissionMember[];
    replayed: boolean;
    new_candidate_ids: string[];
    reason: string | null;
}

export interface AtelierGenerationSingletonAdmissionAck extends AtelierGenerationAdmissionAckBase {
    receipt: AtelierGenerationRequestReceipt & { schema_version: 1 };
    members: [AtelierGenerationAdmissionMember];
    attempt: AtelierGenerationAttempt;
    candidate: AtelierGenerationCandidateProjection | null;
}

export interface AtelierGenerationBatchAdmissionAck extends AtelierGenerationAdmissionAckBase {
    receipt: AtelierGenerationRequestReceipt & { schema_version: 2 };
    members: AtelierGenerationAdmissionMember[];
    attempt?: never;
    candidate?: never;
}

export type AtelierGenerationAdmissionAck = AtelierGenerationSingletonAdmissionAck | AtelierGenerationBatchAdmissionAck;

export interface AtelierGenerationAttemptAck {
    project: AtelierProject;
    attempt: AtelierGenerationAttempt;
    candidate: AtelierGenerationCandidateProjection | null;
}

export interface SelectAtelierGenerationCandidateInput {
    expected_revision: number;
}

export interface AtelierGenerationDraftParams {
    duration?: number;
    resolution?: string;
    seed?: number;
    negative_prompt?: string;
    prompt_extend?: boolean;
    watermark?: boolean;
}

export interface AtelierLegacyGenerationDraft {
    schema_version: 1;
    model_id: "wan/wan2.7-video#i2v";
    mode: "i2v";
    params: AtelierGenerationDraftParams;
}

export interface AtelierOrderedGenerationDraft {
    schema_version: 2;
    input_contract_version: "ordered-media-v1";
    model_id: AtelierOrderedVideoModel;
    mode: AtelierOrderedVideoMode;
    params: AtelierOrderedGenerationParams;
    media: AtelierOrderedMediaSelection[];
    batch_size: number;
}

export type AtelierGenerationDraft = AtelierLegacyGenerationDraft | AtelierOrderedGenerationDraft;

export interface SetAtelierLegacyGenerationDraftInput {
    expected_revision: number;
    prompt: string;
    model_id: "wan/wan2.7-video#i2v";
    mode: "i2v";
    params: AtelierGenerationDraftParams;
}

export interface SetAtelierOrderedGenerationDraftInput {
    expected_revision: number;
    contract_version: "ordered-media-v1";
    prompt: string;
    model_id: AtelierOrderedVideoModel;
    mode: AtelierOrderedVideoMode;
    params: AtelierOrderedGenerationParams;
    media: AtelierOrderedMediaSelection[];
    batch_size?: number;
}

export type SetAtelierGenerationDraftInput = SetAtelierLegacyGenerationDraftInput | SetAtelierOrderedGenerationDraftInput;

export interface AtelierProject {
    id: string;
    title: string;
    description: string;
    source_project_id: string | null;
    nodes: AtelierNode[];
    candidate_judgments?: AtelierCandidateJudgment[];
    agent_policy: AtelierAgentPolicy;
    agent_turns: JsonRecord[];
    sequence: AtelierSequenceEntry[];
    audio_tracks: AtelierAudioTrack[];
    exports: AtelierExportRecord[];
    node_delete_receipts: AtelierNodeDeleteReceipt[];
    sequence_export_jobs: AtelierSequenceExportJob[];
    node_upload_receipts: AtelierNodeUploadReceipt[];
    generation_attempts: AtelierGenerationAttempt[];
    generation_request_receipts: AtelierGenerationRequestReceipt[];
    agent_compaction_summary: string | null;
    compacted_through_turn_id: string | null;
    import_provenance?: AtelierImportProvenance | null;
    revision: number;
    created_at: number;
    updated_at: number;
}

export interface CreateAtelierProjectInput {
    title: string;
    description?: string;
    sourceProjectId?: string | null;
}

export interface UpdateAtelierProjectInput {
    expectedRevision: number;
    title?: string;
    description?: string;
    sourceProjectId?: string | null;
}

export interface DeleteAtelierProjectInput {
    expectedRevision: number;
}

export interface DeleteAtelierProjectResult {
    ok: true;
}

export interface SetAtelierCandidateJudgmentInput {
    expected_revision: number;
    verdict: AtelierCandidateJudgmentVerdict;
    reason: string;
}

export type AtelierNodeType =
    | "idea"
    | "comment"
    | "region"
    | "image"
    | "video"
    | "audio"
    | "draft"
    | "seed"
    | "plan"
    | "workflow"
    | "sequence";

export interface AtelierNodeCommandAck {
    project: AtelierProject;
    node_ids: string[];
}

export type AtelierMediaKind = "image" | "audio" | "video";
export interface AtelierPendingReference {
    schema_version: 1;
    source_node_id: string;
    source_kind: AtelierMediaKind;
    status: "pending_media";
}
export interface AtelierUploadedMediaProof {
    url: string;
    media_kind: AtelierMediaKind;
    size_bytes: number;
    sha256: string;
    width: number | null;
    height: number | null;
    duration: number | null;
    poster_url: string | null;
    poster_size_bytes: number | null;
    poster_sha256: string | null;
}

export interface AtelierNodeUploadReceipt {
    schema_version: 1;
    receipt_id: string;
    project_id: string;
    client_request_id: string;
    operation: "create_media_node" | "upload_reference_attach";
    request_contract_version: "atelier-upload-request-v1";
    request_fingerprint: string;
    base_revision: number;
    committed_revision: number;
    created_at: number;
    node_ids: string[];
    media: AtelierUploadedMediaProof;
    target_node_id: string | null;
    source_node_id: string;
    replaced_node_id: null;
    previous_media: null;
}

export interface AtelierUploadAck extends AtelierNodeCommandAck {
    receipt_id: string;
    replayed: boolean;
}

export interface AtelierUploadReadback {
    status: "found";
    project: AtelierProject;
    receipt: AtelierNodeUploadReceipt;
}

export interface AtelierUploadInput {
    expectedRevision: number;
    clientRequestId: string;
    file: File;
    title?: string;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
}
export interface CreateAtelierMediaUploadInput extends AtelierUploadInput { nodeType: AtelierMediaKind }
export interface AttachAtelierMediaUploadInput extends AtelierUploadInput { sourceNodeType?: AtelierMediaKind }

export interface CreateAtelierNodeInput {
    expectedRevision: number;
    type?: AtelierNodeType;
    title?: string;
    prompt?: string;
    body?: string | null;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    color?: string;
    collapsed?: boolean;
}

export interface UpdateAtelierNodeTextInput {
    expectedRevision: number;
    title?: string;
    prompt?: string;
}

export interface UpdateAtelierNodeBodyInput {
    expectedRevision: number;
    body: string;
}

export interface UpdateAtelierNodeLayoutInput {
    expectedRevision: number;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    regionId?: string | null;
}

export interface AtelierNodeLayoutUpdate {
    nodeId: string;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    regionId?: string | null;
}

export interface UpdateAtelierNodesLayoutInput {
    expectedRevision: number;
    updates: AtelierNodeLayoutUpdate[];
}

export interface MoveAtelierNodesInput {
    expectedRevision: number;
    nodeIds: string[];
    dx?: number;
    dy?: number;
}

export interface WrapAtelierRegionInput {
    expectedRevision: number;
    title?: string;
    color?: string;
    collapsed?: boolean;
    memberNodeIds?: string[];
    x?: number;
    y?: number;
    width?: number;
    height?: number;
}

export interface SetAtelierRegionMembersInput {
    expectedRevision: number;
    memberNodeIds: string[];
}

export interface UpdateAtelierRegionAppearanceInput {
    expectedRevision: number;
    color?: string;
    collapsed?: boolean;
}

export interface AtelierNodeDeleteFieldValue {
    exists: boolean;
    value: JsonValue;
}

export interface AtelierNodeDeleteSourceProof {
    target_node_id: string;
    source_node_id: string;
    source_type: string;
    urls: string[];
}

export type AtelierNodeDeleteDataChangeGroupName = "references" | "parent" | "region";

export interface AtelierNodeDeleteDataChangeGroup {
    node_id: string;
    group: AtelierNodeDeleteDataChangeGroupName;
    before: Record<string, AtelierNodeDeleteFieldValue>;
    after: Record<string, AtelierNodeDeleteFieldValue>;
    source_proofs: AtelierNodeDeleteSourceProof[];
}

export interface AtelierNodeDeleteSnapshot {
    node: AtelierNode;
    original_index: number;
    reference_media_urls: string[];
    referenced_source_ids: string[];
    source_proofs: AtelierNodeDeleteSourceProof[];
}

export interface AtelierNodeDeleteSequenceChange {
    before: AtelierSequenceEntry[];
    after: AtelierSequenceEntry[];
    removed_node_ids: string[];
}

export interface AtelierNodeDeleteAudioTracksChange {
    before: AtelierAudioTrack[];
    after: AtelierAudioTrack[];
    removed_node_ids: string[];
}

export type AtelierNodeRestoreSkipScope = "node" | "data_group" | "sequence" | "audio_tracks";

export interface AtelierNodeRestoreSkip {
    scope: AtelierNodeRestoreSkipScope;
    reason: string;
    node_id: string | null;
    group: string | null;
}

export interface AtelierNodeDeleteReceipt {
    schema_version: 1;
    receipt_id: string;
    project_id: string;
    base_revision: number;
    restore_base_revision: number | null;
    deleted_at: number;
    restored_at: number | null;
    node_ids: string[];
    node_snapshots: AtelierNodeDeleteSnapshot[];
    data_change_groups: AtelierNodeDeleteDataChangeGroup[];
    sequence_change: AtelierNodeDeleteSequenceChange | null;
    audio_tracks_change: AtelierNodeDeleteAudioTracksChange | null;
    last_restore_skips: AtelierNodeRestoreSkip[];
}

export interface DeleteAtelierNodesInput {
    expectedRevision: number;
    nodeIds: string[];
}

export interface DeleteAtelierNodesResult {
    project: AtelierProject;
    receipt_id: string;
    node_ids: string[];
}

export interface RestoreAtelierNodesInput {
    expectedRevision: number;
    receiptId: string;
}

export interface RestoreAtelierNodesResult {
    project: AtelierProject;
    receipt_id: string;
    restored_node_ids: string[];
    skips: AtelierNodeRestoreSkip[];
}

export interface AttachAtelierReferenceInput {
    expectedRevision: number;
    sourceNodeId: string;
    sourceCandidateId?: string;
}

export interface AttachAtelierPendingReferenceInput {
    expectedRevision: number;
    sourceNodeId: string;
}

export interface DetachAtelierReferenceInput {
    expectedRevision: number;
    referenceUrl?: string;
    sourceNodeId?: string;
}
