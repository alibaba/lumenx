/** Public Studio image receipts carry identity and progress, never execution authority. */
export type StudioAssetImageMemberStatus = 'pending' | 'processing' | 'needs_confirmation' | 'completed' | 'cancelled';
export type StudioAssetImageMember = {
    variant_id: string;
    attempt_id: string;
    reason?: string | null;
} & (
    | { status: 'completed'; output_ref: string }
    | { status: Exclude<StudioAssetImageMemberStatus, 'completed'>; output_ref?: never }
);
type StudioAssetImageTarget =
    | { entrypoint: 'studio.asset.reference_sheet.v1'; asset_type: 'character'; channel: 'reference_sheet' }
    | { entrypoint: 'studio.asset.full_body.v1'; asset_type: 'character'; channel: 'full_body' }
    | { entrypoint: 'studio.asset.three_view.v1'; asset_type: 'character'; channel: 'three_view' }
    | { entrypoint: 'studio.asset.headshot.v1'; asset_type: 'character'; channel: 'headshot' }
    | { entrypoint: 'studio.asset.scene_prop.v1'; asset_type: 'scene' | 'prop'; channel: 'image' }
    | { entrypoint: 'studio.storyboard.image.v1'; asset_type: 'storyboard_frame'; channel: 'image' };
export type StudioAssetImageRequest = StudioAssetImageTarget & {
    id: string;
    request_contract_version: 'request-json-v1';
    client_request_id: string;
    request_fingerprint: string;
    request_root_kind: 'project' | 'series';
    request_root_id: string;
    request_root_scope_id: string;
    request_root_series_id: string | null;
    owner_kind: 'script' | 'series' | 'global';
    owner_id: string;
    asset_id: string;
    members: StudioAssetImageMember[];
};
export interface StudioAssetImageReceipt {
    schema_version: 1;
    contract_version: 'studio-asset-image-request-v1';
    replayed: boolean;
    reason?: string | null;
    request: StudioAssetImageRequest;
}

type Raw = Record<string, unknown>;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CLIENT_KEY = /^[A-Za-z0-9._:-]{1,128}$/;
const ATTEMPT = /^[A-Za-z0-9_-]{1,128}$/;
const HASH = /^[0-9a-f]{64}$/;
const REQUEST_KEYS = ['id', 'entrypoint', 'request_contract_version', 'client_request_id', 'request_fingerprint',
    'request_root_kind', 'request_root_id', 'request_root_scope_id', 'request_root_series_id',
    'owner_kind', 'owner_id', 'asset_type', 'asset_id', 'channel', 'members'];

function invalid(): never { throw new Error('studio_image_receipt_invalid'); }

function object(value: unknown, required: string[], optional: string[] = []): Raw {
    if (typeof value !== 'object' || value === null || Array.isArray(value)
        || Object.getPrototypeOf(value) !== Object.prototype) invalid();
    const raw = value as Raw;
    const allowed = new Set([...required, ...optional]);
    if (required.some(key => !Object.hasOwn(raw, key))
        || Reflect.ownKeys(raw).some(key => typeof key !== 'string' || !allowed.has(key))) invalid();
    return raw;
}

function matching(value: unknown, pattern: RegExp): string {
    // Full-match equality also rejects the newline accepted by JavaScript's $ anchor.
    if (typeof value !== 'string' || pattern.exec(value)?.[0] !== value) invalid();
    return value;
}

function businessId(value: unknown): string {
    // Match Python str.strip/UTF-8 constraints on existing opaque business IDs.
    if (typeof value !== 'string' || !value || /[\u0000-\u001f\u007f]/.test(value)
        || /^[\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]|[\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]$/.test(value)) invalid();
    for (const char of value) {
        const code = char.charCodeAt(0);
        if (char.length === 1 && code >= 0xd800 && code <= 0xdfff) invalid();
    }
    return value;
}

function reason(raw: Raw): { reason?: string | null } {
    if (!Object.hasOwn(raw, 'reason')) return {};
    if (raw.reason !== null && typeof raw.reason !== 'string') invalid();
    return { reason: raw.reason };
}

function member(value: unknown): StudioAssetImageMember {
    const raw = object(value, ['variant_id', 'attempt_id', 'status'], ['reason', 'output_ref']);
    const attempt = matching(raw.attempt_id, ATTEMPT);
    if (raw.variant_id !== attempt) invalid();
    const identity = { variant_id: attempt, attempt_id: attempt, ...reason(raw) };
    if (raw.status === 'completed') {
        if (raw.output_ref !== `assets/studio/${attempt}.png`) invalid();
        return { ...identity, status: raw.status, output_ref: raw.output_ref };
    }
    if (Object.hasOwn(raw, 'output_ref') || !['pending', 'processing', 'needs_confirmation', 'cancelled'].includes(raw.status as string)) invalid();
    return { ...identity, status: raw.status as Exclude<StudioAssetImageMemberStatus, 'completed'> };
}

function target(raw: Raw): StudioAssetImageTarget {
    if (raw.entrypoint === 'studio.asset.headshot.v1' && raw.asset_type === 'character' && raw.channel === 'headshot') {
        return { entrypoint: raw.entrypoint, asset_type: raw.asset_type, channel: raw.channel };
    }
    if (raw.entrypoint === 'studio.asset.three_view.v1' && raw.asset_type === 'character' && raw.channel === 'three_view') {
        return { entrypoint: raw.entrypoint, asset_type: raw.asset_type, channel: raw.channel };
    }
    if (raw.entrypoint === 'studio.storyboard.image.v1' && raw.asset_type === 'storyboard_frame' && raw.channel === 'image') {
        return { entrypoint: raw.entrypoint, asset_type: raw.asset_type, channel: raw.channel };
    }
    if (raw.entrypoint === 'studio.asset.full_body.v1' && raw.asset_type === 'character' && raw.channel === 'full_body') {
        return { entrypoint: raw.entrypoint, asset_type: raw.asset_type, channel: raw.channel };
    }
    if (raw.entrypoint === 'studio.asset.reference_sheet.v1' && raw.asset_type === 'character' && raw.channel === 'reference_sheet') {
        return { entrypoint: raw.entrypoint, asset_type: raw.asset_type, channel: raw.channel };
    }
    if (raw.entrypoint === 'studio.asset.scene_prop.v1' && (raw.asset_type === 'scene' || raw.asset_type === 'prop') && raw.channel === 'image') {
        return { entrypoint: raw.entrypoint, asset_type: raw.asset_type, channel: raw.channel };
    }
    return invalid();
}

/** Validate shape and internal identity only; the journal binds the expected environment/request. */
export function validateStudioAssetImageReceipt(value: unknown): StudioAssetImageReceipt {
    const raw = object(value, ['schema_version', 'contract_version', 'replayed', 'request'], ['reason']);
    if (raw.schema_version !== 1 || raw.contract_version !== 'studio-asset-image-request-v1' || typeof raw.replayed !== 'boolean') invalid();
    const r = object(raw.request, REQUEST_KEYS);
    const identity = target(r);
    if (r.request_contract_version !== 'request-json-v1'
        || !['project', 'series'].includes(r.request_root_kind as string)
        || !['script', 'series', 'global'].includes(r.owner_kind as string)
        || !Array.isArray(r.members) || r.members.length === 0) invalid();
    const rootId = businessId(r.request_root_id);
    const ownerId = businessId(r.owner_id);
    const seriesId = r.request_root_series_id === null ? null : businessId(r.request_root_series_id);
    if (identity.asset_type === 'storyboard_frame'
        && (r.request_root_kind !== 'project' || r.owner_kind !== 'script' || ownerId !== rootId || r.members.length !== 1)) invalid();
    if (r.owner_kind === 'global' && ownerId !== 'library') invalid();
    if (r.request_root_kind === 'series') {
        if (r.owner_kind !== 'series' || ownerId !== rootId || seriesId !== null) invalid();
    } else if (r.owner_kind === 'script' && ownerId !== rootId || r.owner_kind === 'series' && ownerId !== seriesId) invalid();
    const members: StudioAssetImageMember[] = [];
    const attempts = new Set<string>();
    for (const candidate of r.members) {
        const parsed = member(candidate);
        if (attempts.has(parsed.attempt_id)) invalid();
        attempts.add(parsed.attempt_id);
        members.push(parsed);
    }
    return {
        schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: raw.replayed, ...reason(raw),
        request: {
            ...identity, id: matching(r.id, UUID), request_contract_version: r.request_contract_version,
            client_request_id: matching(r.client_request_id, CLIENT_KEY), request_fingerprint: matching(r.request_fingerprint, HASH),
            request_root_kind: r.request_root_kind as 'project' | 'series', request_root_id: rootId,
            request_root_scope_id: matching(r.request_root_scope_id, UUID), request_root_series_id: seriesId,
            owner_kind: r.owner_kind as 'script' | 'series' | 'global', owner_id: ownerId,
            asset_id: businessId(r.asset_id), members,
        },
    };
}
