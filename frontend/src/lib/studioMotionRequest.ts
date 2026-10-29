import { appAccessFetch } from '@/lib/appAccess';
import type { Project } from '@/store/projectStore';
import type { StudioMotionAssetOwner, StudioMotionEligibility, StudioMotionReceipt, StudioMotionRequestBody } from '@/lib/api';
import { isCurrentRuntimeEnvironment, runtimeEnvironmentKey, type RuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { assertJournalDataFields, isPlainJournalData as plain, listJournalRecords, resolveJournalStorage,
    serializeJournalData as canonical, writeJournalBytes } from '@/lib/studioRequestJournal';

export type StudioMotionInput = Omit<StudioMotionRequestBody, 'client_request_id'>;
export interface StudioMotionRequestOwner {
    environment: RuntimeEnvironment;
    projectId: string;
    assetId: string;
    assetKind?: 'character' | 'scene' | 'prop';
    assetOwner: StudioMotionAssetOwner;
    storage?: Storage;
}
export interface StoredStudioMotionRequest {
    schema_version: 1;
    apiBase: string;
    storageId: string;
    projectId: string;
    assetId: string;
    ownerKind: StudioMotionAssetOwner['kind'];
    ownerId: string;
    request: StudioMotionRequestBody;
    selectedImageId: string;
    createdAt: number;
    receiptIdentity: string | null;
}
export type StudioMotionReadResult = {
    status: 'confirmed' | 'unknown' | 'not_accepted';
    journal: StoredStudioMotionRequest;
    receipt?: StudioMotionReceipt;
    reason?: string;
};
type BeginOptions = StudioMotionRequestOwner & { input: StudioMotionInput; selectedImageId: string; idFactory?: () => string; now?: () => number };
export type PrepareStudioMotionRequest = () => Promise<{ input: StudioMotionInput; selectedImageId: string } | 'legacy' | null>;
type ReadOptions = StudioMotionRequestOwner & { journal: StoredStudioMotionRequest; isCurrent?: () => boolean };
const PREFIX = 'lumenx.studioMotionRequest.v1:';
const REJECTION_PREFIX = 'lumenx.studioMotionRejection.v1:';
const ID = /^[A-Za-z0-9._:-]{1,128}$/;
const HEX = /^[0-9a-f]{64}$/;
const fail = (code: string): never => { throw new Error(`studio_motion_${code}`); };
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
    && value === value.trim() && !/[\x00-\x1f\x7f]/.test(value);
const digest = (value: unknown): value is string => typeof value === 'string' && HEX.test(value);
const fields = (value: unknown) => assertJournalDataFields(value, () => fail('request_invalid'));
const reasons = new Set(['asset_type_unsupported', 'batch_unsupported', 'request_audio_present', 'model_unsupported',
    'configured_audio_present', 'selected_image_required', 'selected_image_mismatch', 'source_kind_unsupported']);
const positiveInteger = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
const nullableText = (value: unknown) => value === null || text(value);
const canonicalLocalPath = (value: unknown) => typeof value === 'string' && !/[\\?#\x00-\x1f\x7f]/.test(value)
    && value.split('/').every(component => component !== '' && component !== '.' && component !== '..');

function validateScope(value: unknown) {
    if (!plain(value) || !['provider', 'backend', 'gateway', 'base_url'].every(name => text(value[name]))
        || !['region', 'workspace_id', 'account_identity'].every(name => nullableText(value[name]))) fail('receipt_invalid');
}
function validateHandle(value: unknown, execution: Record<string, unknown>) {
    if (!plain(value) || value.schema_version !== 1 || value.provider_model_id !== 'wan2.7-i2v' || !text(value.task_id)
        || !['by_task_id', 'unknown', 'unsupported'].includes(String(value.query_capability))
        || !nullableText(value.request_id)
        || value.submitted_at !== null && (typeof value.submitted_at !== 'number' || !Number.isFinite(value.submitted_at))
        || canonical(value.scope) !== canonical(execution.scope)
        || value.credential_ref !== execution.credential_ref) return fail('receipt_invalid');
    validateScope(value.scope);
}
function validateProof(value: unknown, execution: Record<string, unknown>, taskId: string) {
    if (!plain(value) || value.schema_version !== 1 || value.attempt_id !== execution.attempt_id
        || !canonicalLocalPath(value.relative_path)
        || value.relative_path !== `video/provider-${taskId}-${String(execution.attempt_id)}.mp4`
        || !digest(value.sha256) || !positiveInteger(value.size_bytes)
        || canonical(value.handle) !== canonical(execution.handle)) fail('receipt_invalid');
}
function validateExecution(execution: Record<string, unknown>, task: Record<string, unknown>, journal: StoredStudioMotionRequest) {
    validateScope(execution.scope);
    if (Object.hasOwn(execution, 'credential_ref') && !text(execution.credential_ref)) fail('receipt_invalid');
    const intent = execution.intent, media = execution.media;
    if (!plain(intent) || intent.mode !== 'i2v' || intent.catalog_mode !== 'i2v' || intent.model_id !== 'wan/wan2.7-video#i2v'
        || typeof intent.prompt !== 'string' || intent.prompt !== task.prompt || !plain(intent.params)
        || intent.params.duration !== (journal.request.duration ?? 5)
        || !Array.isArray(media) || media.length !== 1 || !plain(media[0]) || media[0].schema_version !== 1
        || media[0].role !== 'first_frame' || media[0].ordinal !== 0 || !digest(media[0].sha256)
        || !positiveInteger(media[0].size_bytes) || typeof media[0].declared_media_type !== 'string'
        || !media[0].declared_media_type.startsWith('image/')) fail('receipt_invalid');
    const { phase, handle, observation, prepared_artifact: prepared, artifact } = execution;
    if (['accepted', 'cancelled', 'submitting'].includes(String(phase))) {
        if ([handle, observation, prepared, artifact].some(value => value !== null)) fail('receipt_invalid');
    } else {
        validateHandle(handle, execution);
        if (phase === 'submitted' && [observation, prepared, artifact].some(value => value !== null)) fail('receipt_invalid');
        if (phase === 'observed' || phase === 'completed') {
            if (!plain(observation) || !plain(handle) || observation.task_id !== handle.task_id
                || canonical(observation.source_handle) !== canonical(handle)
                || !['pending', 'running', 'succeeded', 'failed', 'canceled', 'unknown'].includes(String(observation.status))) fail('receipt_invalid');
        }
        if (prepared !== null) validateProof(prepared, execution, String(task.id));
        if (artifact !== null) validateProof(artifact, execution, String(task.id));
        if (prepared !== null || artifact !== null) {
            if (!plain(observation) || observation.status !== 'succeeded') fail('receipt_invalid');
        }
        if (phase === 'completed') {
            if (prepared === null || artifact === null || canonical(prepared) !== canonical(artifact)) fail('receipt_invalid');
        } else if (artifact !== null) fail('receipt_invalid');
    }
    if (task.status === 'completed' && phase !== 'completed') fail('receipt_invalid');
}

function owner(options: StudioMotionRequestOwner) {
    const { environment, projectId, assetId, assetOwner } = options;
    if (!text(environment.apiUrl) || !text(environment.storageId) || !text(projectId) || !text(assetId)
        || !['character', 'scene', 'prop'].includes(options.assetKind ?? 'character')
        || !assetOwner || !['script', 'series', 'global'].includes(assetOwner.kind) || !text(assetOwner.id)
        || assetOwner.kind === 'script' && assetOwner.id !== projectId
        || assetOwner.kind === 'global' && assetOwner.id !== 'library') return fail('owner_invalid');
    return { apiBase: environment.apiUrl, storageId: environment.storageId, projectId, assetId,
        ownerKind: assetOwner.kind, ownerId: assetOwner.id };
}
const storageFor = (options: StudioMotionRequestOwner) => resolveJournalStorage(options, () => fail('storage_unavailable'));
// Character keys stay byte-compatible. Other kinds precede the owner prefix so
// listing or locking a character never includes a same-ID scene or prop.
const prefix = (options: StudioMotionRequestOwner) =>
    `${PREFIX}${options.assetKind && options.assetKind !== 'character' ? `${options.assetKind}:` : ''}${encodeURIComponent(canonical(owner(options)))}:`;
function key(options: StudioMotionRequestOwner, id: string) {
    if (!ID.test(id)) fail('request_invalid');
    return `${prefix(options)}${encodeURIComponent(id)}`;
}
function current(options: StudioMotionRequestOwner & { isCurrent?: () => boolean }) {
    const env = options.environment;
    if (env.key !== runtimeEnvironmentKey(env.apiUrl, env.storageId) || !isCurrentRuntimeEnvironment(env.key, env.generation)) fail('environment_changed');
    if (options.isCurrent && !options.isCurrent()) fail('request_cancelled');
}
function validateBody(value: unknown): StudioMotionRequestBody {
    fields(value);
    if (!plain(value) || !text(value.client_request_id) || !ID.test(value.client_request_id) || !text(value.asset_id)
        || typeof value.asset_type !== 'string' || !['full_body', 'head_shot', 'scene', 'prop'].includes(value.asset_type) || !digest(value.expected_target)
        || Object.keys(value).some(name => !['asset_id', 'asset_type', 'client_request_id', 'expected_target', 'prompt', 'audio_url', 'duration', 'batch_size'].includes(name))
        || Object.hasOwn(value, 'prompt') && value.prompt !== null && typeof value.prompt !== 'string'
        || Object.hasOwn(value, 'audio_url') && value.audio_url !== null
        || Object.hasOwn(value, 'duration') && (typeof value.duration !== 'number' || !Number.isSafeInteger(value.duration) || value.duration <= 0)
        || Object.hasOwn(value, 'batch_size') && value.batch_size !== 1) return fail('request_invalid');
    return Object.freeze({ ...value }) as unknown as StudioMotionRequestBody;
}
function validateJournal(value: unknown, options: StudioMotionRequestOwner): StoredStudioMotionRequest {
    if (!plain(value) || Object.keys(value).sort().join(',') !== 'apiBase,assetId,createdAt,ownerId,ownerKind,projectId,receiptIdentity,request,schema_version,selectedImageId,storageId'
        || value.schema_version !== 1 || !text(value.selectedImageId) || typeof value.createdAt !== 'number' || !Number.isFinite(value.createdAt)
        || value.receiptIdentity !== null && typeof value.receiptIdentity !== 'string') return fail('journal_corrupt');
    for (const [name, expected] of Object.entries(owner(options))) if (value[name] !== expected) fail('journal_corrupt');
    const request = validateBody(value.request);
    const kind = request.asset_type === 'scene' || request.asset_type === 'prop' ? request.asset_type : 'character';
    if (request.asset_id !== options.assetId || kind !== (options.assetKind ?? 'character')) fail('journal_corrupt');
    return Object.freeze({ ...value, request }) as unknown as StoredStudioMotionRequest;
}
export function listStudioMotionRequests(options: StudioMotionRequestOwner): StoredStudioMotionRequest[] {
    const storage = storageFor(options), start = prefix(options);
    try {
        return listJournalRecords(storage, start, (raw, name) => {
            if (raw === null) fail('journal_corrupt');
            const journal = validateJournal(JSON.parse(raw!), options);
            if (name !== key(options, journal.request.client_request_id)) fail('journal_corrupt');
            return journal;
        }).sort((a, b) => a.createdAt - b.createdAt || a.request.client_request_id.localeCompare(b.request.client_request_id));
    } catch { return fail('journal_unavailable'); }
}
function save(options: StudioMotionRequestOwner, value: StoredStudioMotionRequest) {
    const journal = validateJournal(value, options), storage = storageFor(options);
    writeJournalBytes(storage, key(options, journal.request.client_request_id), canonical(journal), () => fail('storage_unavailable'));
    return journal;
}
export function beginStudioMotionRequest(options: BeginOptions): { status: 'created' | 'pending'; journal: StoredStudioMotionRequest } {
    fields(options.input);
    if (Object.hasOwn(options.input, 'client_request_id') || !text(options.selectedImageId)) fail('request_invalid');
    const request = validateBody({ ...options.input, client_request_id: options.idFactory ? options.idFactory() : crypto.randomUUID() });
    const previous = listStudioMotionRequests(options).find(item => item.request.client_request_id === request.client_request_id);
    if (previous) {
        if (canonical(previous.request) !== canonical(request) || previous.selectedImageId !== options.selectedImageId) fail('request_conflict');
        return { status: 'pending', journal: previous };
    }
    return { status: 'created', journal: save(options, { schema_version: 1, ...owner(options), request,
        selectedImageId: options.selectedImageId, createdAt: options.now ? options.now() : Date.now(), receiptIdentity: null }) };
}
function stored(options: ReadOptions) {
    const given = validateJournal(options.journal, options);
    const latest = listStudioMotionRequests(options).find(item => item.request.client_request_id === given.request.client_request_id);
    if (!latest || canonical({ ...latest, receiptIdentity: null }) !== canonical({ ...given, receiptIdentity: null })) return fail('journal_changed');
    if (given.receiptIdentity !== null && given.receiptIdentity !== latest.receiptIdentity) fail('receipt_changed');
    return latest;
}
const url = (options: StudioMotionRequestOwner) => `${owner(options).apiBase}/projects/${encodeURIComponent(options.projectId)}/motion_ref_requests`;
const headers = (options: StudioMotionRequestOwner) => ({ Accept: 'application/json', 'Content-Type': 'application/json', 'X-LumenX-Environment': options.environment.storageId });

export async function readStudioMotionEligibility(options: StudioMotionRequestOwner & { assetType?: StudioMotionRequestBody['asset_type']; isCurrent?: () => boolean }): Promise<StudioMotionEligibility> {
    current(options);
    const assetType = options.assetType ?? 'full_body';
    const query = new URLSearchParams({ asset_id: options.assetId, asset_type: assetType, batch_size: '1', has_audio: 'false' });
    const response = await appAccessFetch(`${url(options)}?${query}`, { method: 'GET', credentials: 'include', cache: 'no-store', redirect: 'error', headers: headers(options) });
    if (!response.ok) fail(`http_${response.status}`);
    const value: unknown = await response.json(); current(options);
    if (!plain(value) || value.project_id !== options.projectId || value.asset_id !== options.assetId || value.asset_type !== assetType
        || typeof value.eligible !== 'boolean' || !text(value.model_id) || !digest(value.expected_target)
        || !plain(value.owner) || value.owner.kind !== options.assetOwner.kind || value.owner.id !== options.assetOwner.id
        || value.selected_image_id !== null && !text(value.selected_image_id)
        || !['local_path', 'data_uri', 'remote_url', 'object_key', 'blob_url', 'unknown'].includes(String(value.source_kind))
        || (value.eligible ? value.reason !== null || value.model_id !== 'wan2.7-i2v' || !text(value.selected_image_id)
            || !['local_path', 'data_uri'].includes(String(value.source_kind)) : !reasons.has(String(value.reason)))) fail('eligibility_invalid');
    return value as unknown as StudioMotionEligibility;
}

/** Validate this namespace independently of frame-video and image receipts. Unknown
 * extra provider binding fields are allowed on the wire, never persisted wholesale. */
export function validateStudioMotionReceipt(value: unknown, journal: StoredStudioMotionRequest): StudioMotionReceipt {
    if (!plain(value) || !plain(value.request) || !text(value.task_id) || value.reason !== null && typeof value.reason !== 'string'
        || Object.hasOwn(value, 'replayed') && typeof value.replayed !== 'boolean') return fail('receipt_invalid');
    const request = value.request;
    if (request.schema_version !== 3 || request.entrypoint !== 'studio.motion_reference.v1' || request.request_contract_version !== 'request-json-v1'
        || request.client_request_id !== journal.request.client_request_id || request.asset_id !== journal.assetId || request.asset_type !== journal.request.asset_type
        || request.owner_kind !== journal.ownerKind || request.owner_id !== journal.ownerId || request.task_id !== value.task_id
        || !text(request.attempt_id) || !digest(request.request_fingerprint) || !digest(request.execution_fingerprint)
        || typeof request.created_at !== 'number' || !Number.isFinite(request.created_at)) fail('receipt_invalid');
    if (value.task === null) {
        if (value.reason !== 'task_deleted') fail('receipt_invalid');
    } else {
        if (!plain(value.task)) return fail('receipt_invalid');
        const task = value.task, binding = task.motion_reference, execution = task.provider_execution;
        if ((task.error ?? null) !== value.reason || task.id !== value.task_id || task.project_id !== journal.projectId || task.asset_id !== journal.assetId || task.frame_id != null
            || task.model !== 'wan2.7-i2v' || task.generation_mode !== 'i2v' || task.duration !== (journal.request.duration ?? 5)
            || !['pending', 'processing', 'needs_confirmation', 'completed', 'failed'].includes(String(task.status))
            || !plain(binding) || binding.channel !== request.asset_type || binding.client_request_id !== request.client_request_id
            || binding.owner_kind !== request.owner_kind || binding.owner_id !== request.owner_id
            || binding.source_image_id !== journal.selectedImageId || !digest(binding.target_fingerprint)
            || !plain(execution) || execution.schema_version !== 1 || execution.attempt_id !== request.attempt_id
            || execution.fingerprint !== request.execution_fingerprint || execution.provider_model_id !== 'wan2.7-i2v'
            || !['accepted', 'cancelled', 'submitting', 'submitted', 'observed', 'completed'].includes(String(execution.phase))) return fail('receipt_invalid');
        validateExecution(execution, task, journal);
    }
    const receipt = value as unknown as StudioMotionReceipt;
    if (journal.receiptIdentity !== null && journal.receiptIdentity !== receiptIdentity(receipt)) fail('receipt_changed');
    return receipt;
}
function receiptIdentity(receipt: StudioMotionReceipt) {
    // Only this public immutable identity is stored. No handle, credential, task,
    // prompt expansion, signed URL or whole provider execution enters localStorage.
    const value = receipt.request;
    return canonical({ schema_version: value.schema_version, entrypoint: value.entrypoint, request_contract_version: value.request_contract_version,
        client_request_id: value.client_request_id, request_fingerprint: value.request_fingerprint, execution_fingerprint: value.execution_fingerprint,
        task_id: value.task_id, attempt_id: value.attempt_id, owner_kind: value.owner_kind, owner_id: value.owner_id,
        asset_id: value.asset_id, asset_type: value.asset_type, created_at: value.created_at });
}
function rejectionMarker(value: unknown, status: number, journal: StoredStudioMotionRequest) {
    if (!plain(value) || value.accepted !== false || value.client_request_id !== journal.request.client_request_id) return null;
    if (status === 409 && value.code === 'motion_reference_target_changed' && !Object.hasOwn(value, 'reason')) {
        return { code: value.code, accepted: false, client_request_id: value.client_request_id };
    }
    if (status === 422 && value.code === 'motion_reference_not_eligible' && reasons.has(String(value.reason))) {
        return { code: value.code, accepted: false, client_request_id: value.client_request_id, reason: value.reason };
    }
    return null;
}
const rejectionKey = (options: StudioMotionRequestOwner, journal: StoredStudioMotionRequest) =>
    `${REJECTION_PREFIX}${key(options, journal.request.client_request_id).slice(PREFIX.length)}`;
function recordRejection(options: ReadOptions, journal: StoredStudioMotionRequest, status: number, marker: NonNullable<ReturnType<typeof rejectionMarker>>) {
    current(options); stored({ ...options, journal });
    const storage = storageFor(options), original = storage.getItem(key(options, journal.request.client_request_id));
    if (original === null) return fail('journal_changed');
    writeJournalBytes(storage, rejectionKey(options, journal), canonical({ schema_version: 1, journalBytes: original,
        status_code: status, response: marker }), () => fail('storage_unavailable'));
}
function recordedRejection(options: StudioMotionRequestOwner, journal: StoredStudioMotionRequest) {
    const storage = storageFor(options), raw = storage.getItem(rejectionKey(options, journal));
    if (!raw) return null;
    try {
        const value: unknown = JSON.parse(raw);
        if (!plain(value) || value.schema_version !== 1
            || Object.keys(value).sort().join(',') !== 'journalBytes,response,schema_version,status_code'
            || typeof value.journalBytes !== 'string'
            || value.journalBytes !== storage.getItem(key(options, journal.request.client_request_id))
            || typeof value.status_code !== 'number') return null;
        return rejectionMarker(value.response, value.status_code, journal);
    } catch { return null; }
}
async function exchange(options: ReadOptions, method: 'GET' | 'POST'): Promise<StudioMotionReadResult> {
    let journal = options.journal;
    try {
        current(options); journal = stored(options);
        const response = await appAccessFetch(method === 'POST' ? url(options) : `${url(options)}/${encodeURIComponent(journal.request.client_request_id)}`, {
            method, credentials: 'include', cache: 'no-store', redirect: 'error', headers: headers(options),
            ...(method === 'POST' ? { body: JSON.stringify(journal.request) } : {}),
        });
        if (!response.ok) {
            current(options); journal = stored({ ...options, journal });
            if (method === 'POST' && (response.status === 409 || response.status === 422)) {
                const marker = rejectionMarker(await response.json(), response.status, journal);
                if (marker) {
                    recordRejection(options, journal, response.status, marker);
                    return { status: 'not_accepted', journal, reason: marker.code };
                }
            }
            if (method === 'GET' && response.status === 404) {
                const marker = recordedRejection(options, journal);
                if (marker) return { status: 'not_accepted', journal, reason: marker.code };
            }
            fail(`http_${response.status}`);
        }
        const receipt = validateStudioMotionReceipt(await response.json(), journal);
        current(options); journal = stored({ ...options, journal }); validateStudioMotionReceipt(receipt, journal);
        journal = save(options, { ...journal, receiptIdentity: receiptIdentity(receipt) });
        return { status: 'confirmed', journal, receipt };
    } catch (error) {
        return { status: 'unknown', journal, reason: error instanceof Error ? error.message : 'studio_motion_unknown' };
    }
}
export async function submitStudioMotionRequest(options: StudioMotionRequestOwner & { prepare: PrepareStudioMotionRequest;
    isCurrent?: () => boolean; idFactory?: () => string; now?: () => number;
    maySkipJournal?: (journal: StoredStudioMotionRequest) => boolean;
    onLegacy?: () => void;
    onCreated?: (journal: StoredStudioMotionRequest) => void }): Promise<StudioMotionReadResult | null> {
    current(options);
    const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
    if (!locks?.request) return fail('lock_unavailable');
    return locks.request(`${prefix(options)}submit`, { mode: 'exclusive', ifAvailable: true }, async lock => {
        if (!lock) return fail('request_busy');
        current(options);
        const blocker = () => listStudioMotionRequests(options).find(journal => !options.maySkipJournal?.(journal));
        const previous = blocker();
        if (previous) return { status: 'unknown', journal: previous, reason: 'studio_motion_request_already_recorded' };
        // Both the author reads and the final journal recheck belong to this lock.
        const prepared = await options.prepare();
        current(options);
        if (!prepared) return null;
        const arrived = blocker();
        if (arrived) return { status: 'unknown', journal: arrived, reason: 'studio_motion_request_already_recorded' };
        if (prepared === 'legacy') {
            if (!options.onLegacy) return fail('legacy_unavailable');
            options.onLegacy(); return null;
        }
        const created = beginStudioMotionRequest({ ...options, ...prepared });
        if (created.status !== 'created') return { status: 'unknown', journal: created.journal, reason: 'studio_motion_request_already_recorded' };
        options.onCreated?.(created.journal);
        current(options);
        return exchange({ ...options, journal: created.journal }, 'POST');
    });
}
export function readStudioMotionRequestReceipt(options: ReadOptions): Promise<StudioMotionReadResult> { return exchange(options, 'GET'); }

/** Execution completion and its current projection are separate facts. */
export function studioMotionCompletionProof(result: StudioMotionReadResult): string | null {
    if (result.status !== 'confirmed' || !result.receipt?.task) return null;
    try { validateStudioMotionReceipt(result.receipt, result.journal); } catch { return null; }
    const { task, reason } = result.receipt, execution = task.provider_execution;
    if (execution?.phase !== 'completed' || !['completed', 'needs_confirmation'].includes(task.status)
        || ![null, 'motion_reference_inputs_changed', 'motion_reference_target_changed'].includes(reason)) return null;
    return canonical({ identity: receiptIdentity(result.receipt), artifact: execution.artifact, prepared: execution.prepared_artifact });
}
export function studioMotionRetentionProof(options: StudioMotionRequestOwner, result: StudioMotionReadResult): string | null {
    const completion = studioMotionCompletionProof(result);
    return completion && studioMotionJournalCurrent(options, result.journal)
        ? canonical({ journal: result.journal, completion }) : null;
}
export function studioMotionDefiniteFailure(result: StudioMotionReadResult): boolean {
    if (result.status !== 'confirmed' || !result.receipt?.task) return false;
    try { validateStudioMotionReceipt(result.receipt, result.journal); } catch { return false; }
    const { task, reason } = result.receipt, execution = task.provider_execution;
    const observation = execution?.observation;
    return task.status === 'failed' && execution?.phase === 'observed' && !!execution.handle
        && execution.artifact === null && execution.prepared_artifact === null
        && (observation?.status === 'failed' && reason === 'provider_remote_failed'
            || observation?.status === 'canceled' && reason === 'provider_remote_canceled');
}
export function studioMotionJournalCurrent(options: StudioMotionRequestOwner, journal: StoredStudioMotionRequest): boolean {
    try { current(options); return canonical(stored({ ...options, journal })) === canonical(journal); }
    catch { return false; }
}
export function studioMotionKnownRejection(options: StudioMotionRequestOwner, result: StudioMotionReadResult): boolean {
    try {
        return result.status === 'not_accepted' && studioMotionJournalCurrent(options, result.journal)
            && !!recordedRejection(options, result.journal);
    } catch { return false; }
}
export function studioMotionProjectionMatches(result: StudioMotionReadResult, project: Project): boolean {
    if (!result.receipt?.task || !studioMotionCompletionProof(result) || result.receipt.reason
        || project.id !== result.journal.projectId) return false;
    const journal = result.journal, task = result.receipt.task;
    const channel = journal.request.asset_type;
    const asset = (channel === 'scene' ? project.scenes : channel === 'prop' ? project.props : project.characters)
        .find(item => item.id === journal.assetId);
    if (!asset || (journal.ownerKind === 'series' ? asset.source !== 'series' || project.series_id !== journal.ownerId
        : journal.ownerKind === 'global' ? asset.source !== 'global' : asset.source !== undefined && asset.source !== 'episode')) return false;
    if (channel === 'scene' || channel === 'prop') {
        const target = (channel === 'scene' ? project.scenes : project.props).find(item => item.id === journal.assetId);
        if (!target || target.motion_request_id !== task.id || target.image_asset?.selected_id !== journal.selectedImageId) return false;
        const candidate = target.video_assets?.find(item => item.id === task.id);
        if (!candidate || candidate.status !== 'completed' || candidate.asset_id !== journal.assetId) return false;
        const raw = candidate.media_refs && Object.hasOwn(candidate.media_refs, 'video_url') ? candidate.media_refs.video_url : candidate.video_url;
        return raw === task.provider_execution?.artifact?.relative_path;
    }
    const character = project.characters.find(item => item.id === journal.assetId), unit = character?.[channel];
    if (!unit || character?.[`${channel}_motion_request_id`] !== task.id || unit.selected_image_id !== journal.selectedImageId) return false;
    const candidate = unit.video_variants?.find(item => item.id === task.id);
    if (!candidate || candidate.source_image_id !== journal.selectedImageId) return false;
    // Signed display URLs cannot establish ownership; an explicit raw sidecar wins,
    // including when it is null or otherwise invalid.
    const raw = candidate.media_refs && Object.hasOwn(candidate.media_refs, 'url') ? candidate.media_refs.url : candidate.url;
    return raw === task.provider_execution?.artifact?.relative_path;
}
export function resolveStudioMotionRequest(options: ReadOptions & { result: StudioMotionReadResult; project?: Project }): boolean {
    const { result } = options;
    if (!studioMotionDefiniteFailure(result) && (!options.project || !studioMotionProjectionMatches(result, options.project))) return false;
    try {
        current(options);
        if (!studioMotionJournalCurrent(options, result.journal)) return false;
        const storage = storageFor(options), name = key(options, result.journal.request.client_request_id);
        storage.removeItem(name);
        if (storage.getItem(name) !== null) return false;
        return true;
    } catch { return false; }
}

/** A UI action on an existing execution, never permission to create a new key. */
export function studioMotionRecoveryAction(result: StudioMotionReadResult): 'continue' | 'recover' | null {
    if (result.status !== 'confirmed' || !result.receipt?.task) return null;
    try { validateStudioMotionReceipt(result.receipt, result.journal); } catch { return null; }
    if (studioMotionDefiniteFailure(result)) return null;
    const record = result.receipt.task.provider_execution;
    if (record?.phase === 'accepted') return 'continue';
    if (record && ['submitted', 'observed', 'completed'].includes(record.phase) && record.handle) return 'recover';
    return null;
}
export async function runStudioMotionRecovery(options: ReadOptions & { action: 'continue' | 'recover' }): Promise<StudioMotionReadResult> {
    current(options);
    const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
    if (!locks?.request) return fail('lock_unavailable');
    return locks.request(`${prefix(options)}submit`, { mode: 'exclusive', ifAvailable: true }, async lock => {
        if (!lock) return fail('request_busy');
        const fresh = await readStudioMotionRequestReceipt(options);
        current(options);
        if (studioMotionRecoveryAction(fresh) !== options.action || !fresh.receipt) return fresh;
        const journal = stored({ ...options, journal: fresh.journal }), receipt = fresh.receipt;
        const endpoint = options.action === 'continue'
            ? `${owner(options).apiBase}/projects/${encodeURIComponent(options.projectId)}/provider_video_tasks/${encodeURIComponent(receipt.task_id)}/continue`
            : `${url(options)}/${encodeURIComponent(journal.request.client_request_id)}/recover`;
        try {
            await appAccessFetch(endpoint, { method: 'POST', credentials: 'include', cache: 'no-store', redirect: 'error', headers: headers(options),
                body: JSON.stringify({ expected_attempt_id: receipt.request.attempt_id, expected_fingerprint: receipt.request.execution_fingerprint }) });
        } catch { /* A lost action ACK is resolved only by the original key GET below. */ }
        current(options);
        return readStudioMotionRequestReceipt({ ...options, journal });
    });
}
