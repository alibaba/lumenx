// @vitest-environment happy-dom
// Synthetic protocol-unit fixtures, not captured backend responses.
// Actual headshot HTTP/owner/projection captures remain a separate required gate.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { validateStudioAssetImageReceipt } from '@/lib/studioAssetImageContract';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, submitStudioAssetImageRequest,
    readStudioAssetImageRequestReceipt, resolveStudioAssetImageRequest,
    type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';

vi.mock('@/lib/appAccess', () => ({ appAccessFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
type Obj = Record<string, unknown>;
const SID = '11111111-1111-4111-8111-111111111111';
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p', storage: localStorage });
const rawInput = () => ({ asset_id: 'c', asset_type: 'character', generation_type: 'headshot',
    model_name: 'gpt-image-2', reference_variant_id: 'selected-c', reference_image_url: 'uploads/selected-c.png',
    prompt: 'Keep this authored portrait', apply_style: false, negative_prompt: '', batch_size: 1 });
// Deliberate boundary cast: these tests must reach current runtime rejection before the target union is extended.
const input = (change: Obj = {}) => ({ ...rawInput(), ...change }) as unknown as StudioAssetImageRequestInput;
function syntheticReceipt(status = 'needs_confirmation', change: Obj = {}) {
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false, reason: null, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: 'studio.asset.headshot.v1', request_contract_version: 'request-json-v1',
        client_request_id: 'headshot-one', request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: 'p',
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: null,
        owner_kind: 'script', owner_id: 'p', asset_type: 'character', asset_id: 'c', channel: 'headshot',
        members: [{ variant_id: 'headshot-attempt', attempt_id: 'headshot-attempt', status, reason: null,
            ...(status === 'completed' ? { output_ref: 'assets/studio/headshot-attempt.png' } : {}) }], ...change,
    } };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
let net: ReturnType<typeof vi.fn>;
beforeEach(() => {
    localStorage.clear(); clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    net = vi.fn(async () => response(syntheticReceipt())); vi.stubGlobal('fetch', net);
    vi.stubGlobal('navigator', { locks: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
});
afterEach(() => { vi.unstubAllGlobals(); clearRuntimeEnvironment(); });

it.each([
    ['local', 'script', 'p', null], ['shared series', 'series', 's', 's'], ['global', 'global', 'library', null],
])('accepts only the explicit synthetic headshot tuple at a consistent %s owner', (_label, owner_kind, owner_id, request_root_series_id) => {
    const raw = syntheticReceipt('completed', { owner_kind, owner_id, request_root_series_id });
    expect(validateStudioAssetImageReceipt(raw)).toEqual(raw);
});

it.each([
    { entrypoint: 'studio.asset.three_view.v1' }, { asset_type: 'head_shot' }, { channel: 'three_view' },
])('rejects a mixed static-headshot / sibling-or-motion tuple %j', change => {
    expect(() => validateStudioAssetImageReceipt(syntheticReceipt('completed', change))).toThrow('studio_image_receipt_invalid');
});

it('accepts an internally consistent synthetic standalone-series headshot receipt contract', () => {
    const raw = syntheticReceipt('completed', {
        request_root_kind: 'series', request_root_id: 's', owner_kind: 'series', owner_id: 's',
    });
    expect(validateStudioAssetImageReceipt(raw)).toEqual(raw);
});

it('stores a standalone-series headshot journal and reads its synthetic receipt by the original key', async () => {
    const seriesOwner = { ...owner(), rootKind: 'series' as const, rootId: 's' };
    const pending = beginStudioAssetImageRequest({ ...seriesOwner, input: input(), idFactory: () => 'headshot-one' });
    expect(pending.status).toBe('created');
    expect(listStudioAssetImageRequests(seriesOwner)).toEqual([pending.journal]);
    expect(pending.journal.request).toEqual({ ...rawInput(), client_request_id: 'headshot-one' });
    expect(net).not.toHaveBeenCalled();
    const raw = syntheticReceipt('completed', { request_root_kind: 'series', request_root_id: 's', owner_kind: 'series', owner_id: 's' });
    net.mockResolvedValue(response(raw));
    const read = await readStudioAssetImageRequestReceipt({ ...seriesOwner, journal: listStudioAssetImageRequests(seriesOwner)[0] });
    expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(raw);
    expect(listStudioAssetImageRequests(seriesOwner)[0].request).toEqual(pending.journal.request);
    expect(net).toHaveBeenCalledTimes(1); expect(net.mock.calls[0][1].method).toBe('GET');
    expect(String(net.mock.calls[0][0])).toBe(`${seriesOwner.environment.apiUrl}/series/s/asset_image_requests/headshot-one`);
});

it('persists exact raw headshot input, binds its author before POST, and recovers a lost ACK by original-key GET only', async () => {
    const order: string[] = [];
    net.mockImplementationOnce(async (_url, init) => {
        order.push('POST');
        expect(listStudioAssetImageRequests(owner())[0].request).toEqual({ ...rawInput(), client_request_id: 'headshot-one' });
        expect(JSON.parse(String(init.body))).toEqual(listStudioAssetImageRequests(owner())[0].request);
        throw new Error('Deliberately lost synthetic acknowledgement');
    });
    const sent = await submitStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'headshot-one',
        onCreated: journal => { order.push('created'); expect(journal.request).toEqual({ ...rawInput(), client_request_id: 'headshot-one' }); } });
    expect(sent.status).toBe('unknown'); expect(order).toEqual(['created', 'POST']);
    const original = sent.journal.request;
    expect((await submitStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'headshot-one' })).status).toBe('unknown');
    expect(net).toHaveBeenCalledTimes(1);
    net.mockClear(); net.mockResolvedValue(response(syntheticReceipt()));
    const read = await readStudioAssetImageRequestReceipt({ ...owner(), journal: sent.journal });
    expect(read.status).toBe('confirmed'); expect(net).toHaveBeenCalledTimes(1);
    expect(String(net.mock.calls[0][0])).toMatch(/\/projects\/p\/asset_image_requests\/headshot-one$/);
    expect(net.mock.calls[0][1].method).toBe('GET');
    expect(listStudioAssetImageRequests(owner())[0].request).toEqual(original);
});

it('checks author validity again after onCreated and keeps the unsent original journal if it changed', async () => {
    let current = true;
    await expect(submitStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'headshot-one',
        isCurrent: () => current, onCreated: () => { current = false; } })).rejects.toThrow('studio_image_request_cancelled');
    expect(net).not.toHaveBeenCalled();
    expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    expect(listStudioAssetImageRequests(owner())[0].receiptIdentity).toBeNull();
});

it.each([
    { reference_variant_id: null }, { negative_prompt: 'blur' }, { aspect_ratio: null }, { seed: 0 }, { watermark: false },
])('refuses an unsupported authored headshot input without storing or POSTing %j', change => {
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: input(change), idFactory: () => 'headshot-one' })).toThrow();
    expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
});

it('does not normalize omitted and explicit null fields under the original headshot key', () => {
    beginStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'headshot-one', now: () => 7 });
    const bytes = localStorage.getItem(localStorage.key(0)!);
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: input({ style_prompt: null }), idFactory: () => 'headshot-one', now: () => 7 })).toThrow('studio_image_request_conflict');
    expect(localStorage.getItem(localStorage.key(0)!)).toBe(bytes); expect(net).not.toHaveBeenCalled();
});

it('resolves only the original headshot journal while preserving its independent three-view sibling bytes', async () => {
    beginStudioAssetImageRequest({ ...owner(), input: { ...rawInput(), generation_type: 'three_view' } as unknown as StudioAssetImageRequestInput,
        idFactory: () => 'three-view-one', now: () => 7 });
    const siblingKey = localStorage.key(0)!, siblingBytes = localStorage.getItem(siblingKey);
    net.mockResolvedValue(response(syntheticReceipt('completed')));
    const done = await submitStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'headshot-one' });
    expect(done.status).toBe('confirmed'); expect(done.receipt).toBeDefined();
    expect(resolveStudioAssetImageRequest({ ...owner(), journal: done.journal, receipt: done.receipt! })).toBe(true);
    expect(listStudioAssetImageRequests(owner()).map(journal => journal.request.client_request_id)).toEqual(['three-view-one']);
    expect(localStorage.getItem(siblingKey)).toBe(siblingBytes);
});


it.each(['association-reason', 'member-reason', 'all-cancelled'])(
    'keeps a headshot journal when synthetic terminal evidence has %s', async reason => {
        const raw: Obj = syntheticReceipt(reason === 'all-cancelled' ? 'cancelled' : 'completed');
        if (reason === 'association-reason') raw.reason = 'studio_image_request_projection_changed';
        if (reason === 'member-reason') ((raw.request as Obj).members as Obj[])[0].reason = 'member_unconfirmed';
        net.mockResolvedValue(response(raw));
        const result = await submitStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'headshot-one' });
        expect(result.status).toBe('confirmed'); expect(result.receipt).toBeDefined();
        const bytes = localStorage.getItem(localStorage.key(0)!);
        expect(resolveStudioAssetImageRequest({ ...owner(), journal: result.journal, receipt: result.receipt! })).toBe(false);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(localStorage.getItem(localStorage.key(0)!)).toBe(bytes);
    });
