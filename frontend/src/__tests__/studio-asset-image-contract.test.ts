import { describe, expect, it } from 'vitest';
import { validateStudioAssetImageReceipt, type StudioAssetImageReceipt } from '@/lib/studioAssetImageContract';

type Raw = Record<string, unknown>;
const REQUEST_ID = '11111111-1111-1111-1111-111111111111';
const SCOPE_ID = '00000000-0000-0000-0000-000000000000';
const TOP_KEYS = ['schema_version', 'contract_version', 'replayed', 'request'];
const REQUEST_KEYS = ['id', 'entrypoint', 'request_contract_version', 'client_request_id', 'request_fingerprint',
    'request_root_kind', 'request_root_id', 'request_root_scope_id', 'request_root_series_id',
    'owner_kind', 'owner_id', 'asset_type', 'asset_id', 'channel', 'members'];
const MEMBER_KEYS = ['variant_id', 'attempt_id', 'status'];
const STATUSES = ['pending', 'processing', 'needs_confirmation', 'completed', 'cancelled'] as const;

function member(attempt = 'attempt_1-A', status: typeof STATUSES[number] = 'pending'): Raw {
    return { variant_id: attempt, attempt_id: attempt, status, reason: null,
        ...(status === 'completed' ? { output_ref: `assets/studio/${attempt}.png` } : {}) };
}
function receipt(): Raw {
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false, reason: null,
        request: { id: REQUEST_ID, entrypoint: 'studio.asset.reference_sheet.v1', request_contract_version: 'request-json-v1',
            client_request_id: 'caller.request:1', request_fingerprint: 'a'.repeat(64), request_root_kind: 'project',
            request_root_id: 'project legacy', request_root_scope_id: SCOPE_ID, request_root_series_id: null,
            owner_kind: 'script', owner_id: 'project legacy', asset_type: 'character', asset_id: '角色/legacy',
            channel: 'reference_sheet', members: [member()] } };
}
function request(value: Raw): Raw { return value.request as Raw; }
function members(value: Raw): Raw[] { return request(value).members as Raw[]; }

function invalid(value: unknown): void {
    expect(() => validateStudioAssetImageReceipt(value)).toThrow('studio_image_receipt_invalid');
}

describe('Studio asset image public receipt', () => {
    it.each([
        ['local', 'project', 'p', null, 'script', 'p'],
        ['project series', 'project', 'p', 's', 'series', 's'],
        ['global', 'project', 'p', null, 'global', 'library'],
        ['standalone series', 'series', 's', null, 'series', 's'],
        ['global from series episode', 'project', 'p', 's', 'global', 'library'],
        ['local from series episode', 'project', 'p', 's', 'script', 'p'],
    ])('preserves the internally consistent %s owner', (_name, kind, id, series, ownerKind, ownerId) => {
        const value = receipt();
        Object.assign(request(value), { request_root_kind: kind, request_root_id: id, request_root_series_id: series, owner_kind: ownerKind, owner_id: ownerId });
        expect(validateStudioAssetImageReceipt(value)).toEqual(value);
    });

    it('returns independent data and preserves ordered mixed statuses without inferring success', () => {
        const value = receipt();
        request(value).members = STATUSES.map((status, i) => member(`attempt-${i}`, status));
        const parsed: StudioAssetImageReceipt = validateStudioAssetImageReceipt(value);
        expect(parsed).toEqual(value);
        expect(parsed).not.toBe(value);
        expect(parsed.request).not.toBe(request(value));
        expect(parsed.request.members).not.toBe(members(value));
        members(value)[0].status = 'completed';
        request(value).asset_id = 'changed';
        expect(parsed.request.asset_id).toBe('角色/legacy');
        expect(parsed.request.members.map(m => m.status)).toEqual(STATUSES);
        expect(parsed.request.members[0]).not.toHaveProperty('output_ref');
    });

    it.each([undefined, null, '', 'future_reason_code'])('preserves optional reasons: %s', reason => {
        const value = receipt();
        if (reason === undefined) { delete value.reason; delete members(value)[0].reason; }
        else { value.reason = reason; members(value)[0].reason = reason; }
        value.replayed = true;
        expect(validateStudioAssetImageReceipt(value)).toEqual(value);
    });

    it.each(['角色', 'legacy/id', 'emoji-😀', '\ufeffvalid', 'valid\ufeff'])('preserves valid opaque business ID %s', id => {
        const value = receipt(); request(value).asset_id = id;
        expect(validateStudioAssetImageReceipt(value).request.asset_id).toBe(id);
    });

    it.each([null, undefined, [], true, 1, 'receipt', new Date()])('rejects a non-object envelope %s', invalid);
    it.each(['top', 'request', 'member'])('rejects unexpected authority at %s', level => {
        const value = receipt();
        const target = level === 'top' ? value : level === 'request' ? request(value) : members(value)[0];
        target.credential_ref = 'must-not-be-accepted';
        invalid(value);
    });
    it.each(TOP_KEYS)('requires envelope field %s', key => { const value = receipt(); delete value[key]; invalid(value); });
    it.each(REQUEST_KEYS)('requires request field %s', key => { const value = receipt(); delete request(value)[key]; invalid(value); });
    it.each(MEMBER_KEYS)('requires member field %s', key => { const value = receipt(); delete members(value)[0][key]; invalid(value); });

    it.each([
        ['schema_version', '1'], ['schema_version', true], ['schema_version', 2], ['schema_version', NaN],
        ['contract_version', 'studio-asset-image-request-v2'], ['replayed', 'false'], ['replayed', 0],
        ['reason', undefined], ['reason', 1], ['reason', {}], ['request', null], ['request', []],
    ])('rejects malformed envelope %s = %s', (key, raw) => { const value = receipt(); value[key as string] = raw; invalid(value); });

    it.each([
        ['id', REQUEST_ID.toUpperCase().replace('11111111', 'ABCDEFAB')], ['id', REQUEST_ID.replaceAll('-', '')],
        ['id', `{${REQUEST_ID}}`], ['id', ` ${REQUEST_ID}`], ['id', 1], ['id', `${REQUEST_ID}\n`],
        ['request_root_scope_id', 'not-a-uuid'], ['request_root_scope_id', null],
        ['entrypoint', 'studio.asset.other.v1'], ['request_contract_version', 'request-json-v2'],
        ['client_request_id', ''], ['client_request_id', 'a'.repeat(129)], ['client_request_id', 'bad key'],
        ['client_request_id', 'bad/key'], ['client_request_id', 'invalid\n'], ['client_request_id', 1],
        ['request_fingerprint', 'A'.repeat(64)], ['request_fingerprint', 'a'.repeat(63)],
        ['request_fingerprint', 'a'.repeat(64) + '\n'], ['request_fingerprint', 1],
        ['request_root_kind', 'script'], ['request_root_id', ' p'], ['request_root_id', 'p\u0085'],
        ['request_root_series_id', ''], ['request_root_series_id', false], ['request_root_series_id', undefined],
        ['owner_kind', 'project'], ['owner_id', ''], ['owner_id', ' p'],
        ['asset_type', 'scene'], ['asset_id', '\ud800'], ['asset_id', 'bad\u007f'],
        ['asset_id', '\u3000asset'], ['asset_id', 'asset\u0085'], ['asset_id', ''], ['asset_id', false],
        ['channel', 'full_body'], ['members', null], ['members', {}], ['members', []],
    ])('rejects malformed request %s = %s', (key, raw) => { const value = receipt(); request(value)[key as string] = raw; invalid(value); });

    it.each([
        { owner_kind: 'global', owner_id: 'wrong' },
        { owner_kind: 'script', owner_id: 'different-project' },
        { owner_kind: 'series', owner_id: 's', request_root_series_id: null },
        { owner_kind: 'series', owner_id: 's', request_root_series_id: 'another' },
        { request_root_kind: 'series', owner_kind: 'script' },
        { request_root_kind: 'series', owner_kind: 'global', owner_id: 'library' },
        { request_root_kind: 'series', owner_kind: 'series', owner_id: 'different' },
        { request_root_kind: 'series', owner_kind: 'series', owner_id: 'project legacy', request_root_series_id: 's' },
    ])('rejects inconsistent original owner identity %j', changes => {
        const value = receipt(); Object.assign(request(value), changes); invalid(value);
    });

    it.each([
        ['variant_id', 'other'], ['variant_id', undefined], ['attempt_id', ''], ['attempt_id', '../escape'],
        ['attempt_id', 'a'.repeat(129)], ['attempt_id', 'attempt\n'], ['attempt_id', true],
        ['status', 'failed'], ['status', true], ['reason', undefined], ['reason', 0], ['reason', {}],
    ])('rejects malformed member %s = %s', (key, raw) => { const value = receipt(); members(value)[0][key as string] = raw; invalid(value); });

    it.each(['', '../escape', 'a'.repeat(129), 'attempt\n', 'space id', 'attempt.png', '中文'])('rejects equal unsafe variant/attempt %s', attempt => {
        const value = receipt(); request(value).members = [member(attempt)]; invalid(value);
    });
    it('accepts maximum-length keys/attempts without imposing UUID format on a Core attempt', () => {
        const value = receipt(); request(value).client_request_id = 'a'.repeat(128);
        request(value).members = [member('A'.repeat(128), 'completed')];
        expect(validateStudioAssetImageReceipt(value)).toEqual(value);
    });
    it.each([null, [], true, 'member'])('rejects non-object member %s', raw => { const value = receipt(); request(value).members = [raw]; invalid(value); });
    it('rejects duplicate member identities', () => { const value = receipt(); members(value).push(member()); invalid(value); });
    it('rejects sparse members', () => { const value = receipt(); request(value).members = new Array(2); invalid(value); });

    it.each(STATUSES.filter(s => s !== 'completed'))('forbids any output_ref on %s', status => {
        for (const output of [undefined, null, '', 'assets/studio/attempt_1-A.png']) {
            const value = receipt(); request(value).members = [{ ...member('attempt_1-A', status), output_ref: output }]; invalid(value);
        }
    });
    it.each([undefined, null, '', 'https://example.invalid/image.png', '/files/assets/studio/attempt_1-A.png',
        'output/assets/studio/attempt_1-A.png', '/assets/studio/attempt_1-A.png', 'assets/studio/other.png',
        'assets/studio/attempt_1-A.PNG', 'assets/studio/attempt_1-A.png?token=fake',
        'assets/studio/attempt_1-A.png#image', 'assets/studio/../attempt_1-A.png',
        'assets/studio/%61ttempt_1-A.png', 'assets/studio/attempt_1-A.png\n', 1])('rejects completed output %s', output => {
        const value = receipt(); request(value).members = [{ ...member('attempt_1-A', 'completed'), output_ref: output }]; invalid(value);
    });
    it('requires completed output even when absent', () => {
        const value = receipt(); request(value).members = [member('attempt_1-A', 'completed')]; delete members(value)[0].output_ref; invalid(value);
    });
    it('rejects inherited fields and non-enumerable unknown keys', () => {
        const value = receipt(); invalid(Object.create(value));
        Object.defineProperty(request(value), 'secret', { value: 'not-json' }); invalid(value);
    });
    it('never includes the rejected payload in its error', () => {
        const value = receipt(); value.raw_provider_result = 'private-canary';
        try { validateStudioAssetImageReceipt(value); throw new Error('expected rejection'); }
        catch (error) { expect(String(error)).toBe('Error: studio_image_receipt_invalid'); }
    });
});
