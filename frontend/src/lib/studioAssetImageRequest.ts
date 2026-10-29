import { appAccessFetch } from '@/lib/appAccess';
import { isCurrentRuntimeEnvironment, runtimeEnvironmentKey, type RuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { validateStudioAssetImageReceipt, type StudioAssetImageReceipt } from '@/lib/studioAssetImageContract';
import { assertJournalDataFields, isPlainJournalData as plain, listJournalRecords,
    resolveJournalStorage, serializeJournalData as canonical, writeJournalBytes } from '@/lib/studioRequestJournal';

export type StudioDerivedImageReferenceSource = 'full_body_image_url' | 'three_view_upload' | 'headshot_upload';

type StudioAssetImageCommandTarget =
    | { asset_type?: 'character'; generation_type?: 'reference_sheet'; reference_variant_id?: never; reference_source?: never }
    | { asset_type: 'character'; generation_type: 'full_body'; reference_variant_id?: string | null; reference_source?: never }
    | { asset_type: 'character'; generation_type: 'three_view' | 'headshot'; reference_variant_id?: string | null; reference_source?: StudioDerivedImageReferenceSource }
    | { asset_type: 'scene' | 'prop'; generation_type: 'all'; reference_variant_id?: never; reference_source?: never };
type AssetImageFields = {
    style_preset?: string | null;
    reference_image_url?: string | null;
    style_prompt?: string | null;
    prompt?: string | null;
    apply_style?: boolean;
    negative_prompt?: string | null;
    batch_size?: number;
    model_name?: string | null;
    aspect_ratio?: string | null;
    seed?: number;
    watermark?: boolean;
};
type FrameImageFields = { asset_type: 'storyboard_frame'; generation_type: 'all'; expected_revision: number;
    prompt: string; composition_data?: Record<string, never> | null; batch_size?: 1; reference_variant_id?: never; reference_source?: never }
    & { [K in Exclude<keyof AssetImageFields, 'prompt' | 'batch_size'>]?: never };
export type StudioAssetImageRequestInput = { asset_id: string } & (
    | StudioAssetImageCommandTarget & AssetImageFields & { expected_revision?: never; composition_data?: never }
    | FrameImageFields
);
export type StudioAssetImageRequestBody = StudioAssetImageRequestInput & { client_request_id: string };
export interface StudioAssetImageRequestOwner {
    environment: RuntimeEnvironment;
    rootKind: 'project' | 'series';
    rootId: string;
    storage?: Storage;
}
export interface StoredStudioAssetImageRequest {
    schema_version: 1;
    apiBase: string;
    storageId: string;
    rootKind: 'project' | 'series';
    rootId: string;
    request: StudioAssetImageRequestBody;
    createdAt: number;
    receiptIdentity: string | null;
}
type BeginOptions = StudioAssetImageRequestOwner & {
    input: StudioAssetImageRequestInput;
    idFactory?: () => string;
    now?: () => number;
};
type ReadOptions = StudioAssetImageRequestOwner & { journal: StoredStudioAssetImageRequest };
export type StudioAssetImageReadResult = {
    status: 'confirmed' | 'unknown';
    journal: StoredStudioAssetImageRequest;
    receipt?: StudioAssetImageReceipt;
    reason?: string;
};
const PREFIX = 'lumenx.studioAssetImageRequest.v1:';
const ID = /^[A-Za-z0-9._:-]{1,128}$/;
const STRINGS = new Set(['style_preset', 'reference_image_url', 'reference_variant_id', 'reference_source', 'style_prompt', 'prompt', 'negative_prompt', 'model_name', 'aspect_ratio']);
const ALLOWED = new Set(['client_request_id', 'asset_id', 'asset_type', 'generation_type', 'apply_style', 'batch_size', 'seed', 'watermark', ...Array.from(STRINGS)]);
const FRAME_FIELDS = new Set(['client_request_id', 'asset_id', 'asset_type', 'generation_type', 'expected_revision', 'prompt', 'composition_data', 'batch_size']);
export const isStudioGptImageModel = (model: string | null | undefined): boolean =>
    model === 'gpt-image-2' || model === 'gpt-image/gpt-image-2#image';
export const isStudioWanImageModel = (model: string | null | undefined): boolean =>
    ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'].includes(model ?? '');
export const isStudioQwenImageModel = (model: string | null | undefined): boolean =>
    ['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'].includes(model ?? '');
/** Classify original raw journals independently of today's saved model or source. */
export const isStudioFullBodyEditRequest = (request: StudioAssetImageRequestInput): boolean =>
    request.asset_type === 'character' && request.generation_type === 'full_body'
        && (request.reference_variant_id != null || request.reference_image_url != null);
const fail = (code: string): never => { throw new Error(`studio_image_${code}`); };
const text = (v: unknown): v is string => typeof v === 'string' && !!v.trim() && v.trim() === v && !/[\x00-\x1f\x7f]/.test(v);

function strictFields(value: unknown): void {
    assertJournalDataFields(value, () => fail('request_invalid'));
}
function validateBody(value: unknown): StudioAssetImageRequestBody {
    strictFields(value);
    if (!plain(value) || !text(value.client_request_id) || !ID.test(value.client_request_id) || !text(value.asset_id)) return fail('request_invalid');
    if (value.asset_type === 'storyboard_frame') {
        if (Object.keys(value).some(key => !FRAME_FIELDS.has(key)) || value.generation_type !== 'all'
            || typeof value.expected_revision !== 'number' || !Number.isSafeInteger(value.expected_revision) || value.expected_revision < 0
            || typeof value.prompt !== 'string' || Object.hasOwn(value, 'batch_size') && value.batch_size !== 1) fail('request_invalid');
        if (Object.hasOwn(value, 'composition_data') && value.composition_data !== null) {
            strictFields(value.composition_data);
            if (!plain(value.composition_data) || Reflect.ownKeys(value.composition_data).length) fail('request_invalid');
            return Object.freeze({ ...value, composition_data: Object.freeze({}) }) as unknown as StudioAssetImageRequestBody;
        }
        return Object.freeze({ ...value }) as unknown as StudioAssetImageRequestBody;
    }
    for (const [key, item] of Object.entries(value)) {
        if (!ALLOWED.has(key)) fail('request_invalid');
        if (STRINGS.has(key) && item !== null && typeof item !== 'string') fail('request_invalid');
        if (['apply_style', 'watermark'].includes(key) && typeof item !== 'boolean') fail('request_invalid');
        if (key === 'batch_size' && (typeof item !== 'number' || !Number.isSafeInteger(item) || item < 1)) fail('request_invalid');
        if (key === 'seed' && (typeof item !== 'number' || !Number.isInteger(item) || item < 0 || item > 2147483647)) fail('request_invalid');
    }
    const referenceSheet = (!Object.hasOwn(value, 'asset_type') || value.asset_type === 'character')
        && (!Object.hasOwn(value, 'generation_type') || value.generation_type === 'reference_sheet');
    const image = Object.hasOwn(value, 'asset_type') && Object.hasOwn(value, 'generation_type')
        && (value.asset_type === 'scene' || value.asset_type === 'prop') && value.generation_type === 'all';
    const fullBody = value.asset_type === 'character' && value.generation_type === 'full_body';
    const characterEdit = value.asset_type === 'character' && (value.generation_type === 'three_view' || value.generation_type === 'headshot');
    if (!referenceSheet && !image && !fullBody && !characterEdit
        || !characterEdit && !fullBody && Object.hasOwn(value, 'reference_variant_id')) fail('request_invalid');
    if (Object.hasOwn(value, 'reference_source') && (!characterEdit
        || !['full_body_image_url', 'three_view_upload', 'headshot_upload'].includes(String(value.reference_source)))) fail('request_invalid');
    return Object.freeze({ ...value }) as unknown as StudioAssetImageRequestBody;
}
function owner(options: StudioAssetImageRequestOwner) {
    const { environment, rootKind, rootId } = options;
    if (!text(environment.apiUrl) || !text(environment.storageId) || !text(rootId)
        || !['project', 'series'].includes(rootKind)) return fail('owner_invalid');
    return { apiBase: environment.apiUrl, storageId: environment.storageId, rootKind, rootId };
}
function storageFor(options: StudioAssetImageRequestOwner): Storage {
    return resolveJournalStorage(options, () => fail('storage_unavailable'));
}
function prefix(options: StudioAssetImageRequestOwner) { return `${PREFIX}${encodeURIComponent(canonical(owner(options)))}:`; }
function key(options: StudioAssetImageRequestOwner, id: string) {
    if (!ID.test(id)) fail('request_invalid');
    return `${prefix(options)}${encodeURIComponent(id)}`;
}
function validateJournal(value: unknown, options: StudioAssetImageRequestOwner): StoredStudioAssetImageRequest {
    if (!plain(value) || Object.keys(value).sort().join(',') !== 'apiBase,createdAt,receiptIdentity,request,rootId,rootKind,schema_version,storageId'
        || value.schema_version !== 1 || typeof value.createdAt !== 'number' || !Number.isFinite(value.createdAt)
        || (value.receiptIdentity !== null && typeof value.receiptIdentity !== 'string')) return fail('journal_corrupt');
    for (const [name, expected] of Object.entries(owner(options))) if (value[name] !== expected) fail('journal_corrupt');
    const request = validateBody(value.request);
    if (request.asset_type === 'storyboard_frame' && options.rootKind !== 'project') fail('owner_invalid');
    return Object.freeze({ ...value, request }) as unknown as StoredStudioAssetImageRequest;
}
export function listStudioAssetImageRequests(options: StudioAssetImageRequestOwner): StoredStudioAssetImageRequest[] {
    const storage = storageFor(options), start = prefix(options);
    let result: StoredStudioAssetImageRequest[];
    try {
        result = listJournalRecords(storage, start, (raw, name) => {
            if (raw === null) fail('journal_corrupt');
            const record = validateJournal(JSON.parse(raw!), options);
            if (name !== key(options, record.request.client_request_id)) fail('journal_corrupt');
            return record;
        });
    } catch { return fail('journal_unavailable'); }
    return result.sort((a, b) => a.createdAt - b.createdAt || a.request.client_request_id.localeCompare(b.request.client_request_id));
}
function save(options: StudioAssetImageRequestOwner, journal: StoredStudioAssetImageRequest) {
    const record = validateJournal(journal, options), storage = storageFor(options);
    const name = key(options, record.request.client_request_id), raw = canonical(record);
    writeJournalBytes(storage, name, raw, () => fail('storage_unavailable'));
    return record;
}
export function beginStudioAssetImageRequest(options: BeginOptions): { status: 'created' | 'pending'; journal: StoredStudioAssetImageRequest } {
    strictFields(options.input);
    if (Object.hasOwn(options.input, 'client_request_id')) fail('request_invalid');
    const request = validateBody({ ...options.input, client_request_id: options.idFactory ? options.idFactory() : `studio-${crypto.randomUUID()}` });
    if (request.asset_type === 'storyboard_frame' && options.rootKind !== 'project') fail('owner_invalid');
    const previous = listStudioAssetImageRequests(options).find(r => r.request.client_request_id === request.client_request_id);
    if (previous) {
        if (canonical(previous.request) !== canonical(request)) fail('request_conflict');
        return { status: 'pending', journal: previous };
    }
    // Admission only: historical raw requests remain readable under their original identity.
    const fullBodyEdit = isStudioFullBodyEditRequest(request);
    if (request.generation_type === 'full_body' && Object.hasOwn(request, 'reference_variant_id') && !fullBodyEdit) fail('request_invalid');
    if (request.generation_type === 'three_view' || request.generation_type === 'headshot' || fullBodyEdit) {
        const wanThreeView = request.generation_type === 'three_view' && isStudioWanImageModel(request.model_name);
        const qwenThreeView = request.generation_type === 'three_view' && isStudioQwenImageModel(request.model_name);
        if ((wanThreeView || qwenThreeView) && options.rootKind !== 'project') fail('owner_invalid');
        if ((wanThreeView || qwenThreeView) && Object.hasOwn(request, 'reference_source')) fail('request_invalid');
        const legacyUrl = request.reference_source === 'full_body_image_url';
        if (!text(request.reference_image_url)
            || (legacyUrl ? request.reference_variant_id != null : !text(request.reference_variant_id))) fail('request_invalid');
        if (qwenThreeView) {
            if (Object.hasOwn(request, 'aspect_ratio')) fail('qwen_parameter_not_supported');
        } else if (wanThreeView) {
            if (request.negative_prompt || Object.hasOwn(request, 'aspect_ratio')) fail('wan_parameter_not_supported');
        } else if (request.model_name != null && !isStudioGptImageModel(request.model_name) || request.negative_prompt
            || (fullBodyEdit ? request.aspect_ratio != null && !['9:16', '3:4', '1:1', '4:3', '16:9'].includes(request.aspect_ratio)
                : Object.hasOwn(request, 'aspect_ratio'))
            || Object.hasOwn(request, 'seed') || Object.hasOwn(request, 'watermark')) fail('gpt_parameter_not_supported');
    } else if (isStudioGptImageModel(request.model_name) && (
        !((request.asset_type ?? 'character') === 'character' && ['reference_sheet', 'full_body'].includes(request.generation_type ?? 'reference_sheet')
            || (request.asset_type === 'scene' || request.asset_type === 'prop') && request.generation_type === 'all')
        || request.negative_prompt || request.reference_image_url != null
        || Object.hasOwn(request, 'seed') || Object.hasOwn(request, 'watermark')
    )) fail('gpt_parameter_not_supported');
    return { status: 'created', journal: save(options, {
        schema_version: 1, ...owner(options), request, createdAt: options.now ? options.now() : Date.now(), receiptIdentity: null,
    }) };
}
function current(options: StudioAssetImageRequestOwner) {
    const env = options.environment;
    if (env.key !== runtimeEnvironmentKey(env.apiUrl, env.storageId)
        || !isCurrentRuntimeEnvironment(env.key, env.generation)) fail('environment_changed');
}
function stored(options: ReadOptions) {
    const given = validateJournal(options.journal, options);
    const latest = listStudioAssetImageRequests(options).find(r => r.request.client_request_id === given.request.client_request_id);
    if (!latest || canonical({ ...latest, receiptIdentity: null }) !== canonical({ ...given, receiptIdentity: null })) return fail('journal_changed');
    if (given.receiptIdentity !== null && given.receiptIdentity !== latest.receiptIdentity) fail('receipt_changed');
    return latest;
}
function receiptIdentity(receipt: StudioAssetImageReceipt) {
    return canonical({ ...receipt.request, members: receipt.request.members.map(m => ({ variant_id: m.variant_id, attempt_id: m.attempt_id })) });
}
function match(journal: StoredStudioAssetImageRequest, receipt: StudioAssetImageReceipt) {
    const request = receipt.request;
    // Interpret legacy omissions only for matching; never add defaults to journal raw.
    const kind = journal.request.asset_type ?? 'character';
    const channel = kind === 'character' ? journal.request.generation_type ?? 'reference_sheet' : 'image';
    const entrypoint = kind === 'character' ? `studio.asset.${channel}.v1`
        : kind === 'storyboard_frame' ? 'studio.storyboard.image.v1' : 'studio.asset.scene_prop.v1';
    if (request.asset_type !== kind || request.entrypoint !== entrypoint || request.channel !== channel
        || request.client_request_id !== journal.request.client_request_id || request.request_root_kind !== journal.rootKind
        || request.request_root_id !== journal.rootId || request.asset_id !== journal.request.asset_id
        || request.members.length !== (journal.request.batch_size ?? 1)) fail('receipt_mismatch');
    if (journal.receiptIdentity !== null && journal.receiptIdentity !== receiptIdentity(receipt)) fail('receipt_changed');
}
function url(options: StudioAssetImageRequestOwner) {
    const target = owner(options);
    return `${target.apiBase}/${target.rootKind === 'project' ? 'projects' : 'series'}/${encodeURIComponent(target.rootId)}/asset_image_requests`;
}
async function exchange(options: ReadOptions, method: 'POST' | 'GET'): Promise<StudioAssetImageReadResult> {
    let journal = options.journal;
    try {
        current(options); journal = stored(options);
        const response = await appAccessFetch(method === 'POST' ? url(options) : `${url(options)}/${encodeURIComponent(journal.request.client_request_id)}`, {
            method, credentials: 'include', cache: 'no-store', redirect: 'error',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-LumenX-Environment': options.environment.storageId },
            ...(method === 'POST' ? { body: JSON.stringify(journal.request) } : {}),
        });
        if (!response.ok) throw new Error(`studio_image_http_${response.status}`);
        const receipt = validateStudioAssetImageReceipt(await response.json());
        current(options); journal = stored({ ...options, journal }); match(journal, receipt);
        journal = save(options, { ...journal, receiptIdentity: receiptIdentity(receipt) });
        return { status: 'confirmed', journal, receipt };
    } catch (error) {
        return { status: 'unknown', journal, reason: error instanceof Error ? error.message : 'studio_image_unknown' };
    }
}
export async function submitStudioAssetImageRequest(options: BeginOptions & { isCurrent?: () => boolean;
    /** Bind a new derived-image journal to the author draft before its first POST. */
    onCreated?: (journal: StoredStudioAssetImageRequest) => void;
}): Promise<StudioAssetImageReadResult> {
    const currentSubmission = () => {
        current(options);
        if (options.isCurrent && !options.isCurrent()) fail('request_cancelled');
    };
    currentSubmission();
    const id = options.idFactory ? options.idFactory() : `studio-${crypto.randomUUID()}`;
    const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
    if (!locks?.request) return fail('lock_unavailable');
    return locks.request(`${key(options, id)}:submit`, { mode: 'exclusive', ifAvailable: true }, async lock => {
        if (!lock) return fail('request_busy');
        // The lock callback is asynchronous even with ifAvailable; the owning UI may have closed.
        currentSubmission();
        const accepted = beginStudioAssetImageRequest({ ...options, idFactory: () => id });
        if (accepted.status === 'pending') return { status: 'unknown', journal: accepted.journal, reason: 'studio_image_request_already_recorded' };
        if (accepted.journal.request.generation_type === 'three_view' || accepted.journal.request.generation_type === 'headshot'
            || isStudioFullBodyEditRequest(accepted.journal.request)) {
            options.onCreated?.(accepted.journal);
            currentSubmission();
        }
        return exchange({ ...options, journal: accepted.journal }, 'POST');
    });
}
export function readStudioAssetImageRequestReceipt(options: ReadOptions): Promise<StudioAssetImageReadResult> {
    return exchange(options, 'GET');
}
export async function recoverStudioAssetImageRequests(options: StudioAssetImageRequestOwner): Promise<StudioAssetImageReadResult[]> {
    return Promise.all(listStudioAssetImageRequests(options).map(journal => readStudioAssetImageRequestReceipt({ ...options, journal })));
}
/** Session-only history decisions bind the unchanged journal and full public completion
 * proof. Association reasons can change after another request becomes current. */
export function storyboardImageRetentionProof(options: ReadOptions & { receipt?: StudioAssetImageReceipt }): string | null {
    try {
        current(options);
        const journal = stored(options), receipt = validateStudioAssetImageReceipt(options.receipt);
        match(journal, receipt);
        const request = receipt.request;
        const reasons = [undefined, null, '', 'studio_image_request_inputs_changed', 'studio_image_request_projection_changed', 'studio_image_request_target_changed'];
        if (journal.request.asset_type !== 'storyboard_frame' || !journal.receiptIdentity
            || request.asset_type !== 'storyboard_frame' || request.owner_kind !== 'script'
            || request.owner_id !== options.rootId || request.request_root_kind !== 'project'
            || request.members.length !== 1 || request.members[0].status !== 'completed'
            || !reasons.includes(receipt.reason) || !reasons.includes(request.members[0].reason)) return null;
        const member = request.members[0];
        return canonical({ journal, request: { ...request, members: [{ variant_id: member.variant_id,
            attempt_id: member.attempt_id, status: member.status, output_ref: member.output_ref }] } });
    } catch { return null; }
}
export function resolveStudioAssetImageRequest(options: ReadOptions & { receipt: StudioAssetImageReceipt }): boolean {
    current(options);
    const journal = stored(options), receipt = validateStudioAssetImageReceipt(options.receipt);
    match(journal, receipt);
    if (receipt.request.asset_type === 'storyboard_frame' && (receipt.reason || receipt.request.members.some(m => m.status !== 'completed' || m.reason))) return false;
    if ((receipt.request.channel === 'three_view' || receipt.request.channel === 'headshot'
        || isStudioFullBodyEditRequest(journal.request)) && (receipt.reason
        || !receipt.request.members.some(m => m.status === 'completed')
        || receipt.request.members.some(m => m.status === 'completed' && m.reason))) return false;
    if (!journal.receiptIdentity || !receipt.request.members.every(m => m.status === 'completed' || m.status === 'cancelled')) return false;
    const storage = storageFor(options), name = key(options, journal.request.client_request_id);
    try { storage.removeItem(name); if (storage.getItem(name) !== null) fail('storage_unavailable'); }
    catch { fail('storage_unavailable'); }
    return true;
}
