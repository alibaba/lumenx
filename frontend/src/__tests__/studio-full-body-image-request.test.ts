// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { validateStudioAssetImageReceipt } from '@/lib/studioAssetImageContract';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, readStudioAssetImageRequestReceipt,
    recoverStudioAssetImageRequests, submitStudioAssetImageRequest } from '@/lib/studioAssetImageRequest';

vi.mock('@/lib/appAccess', () => ({ appAccessFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p', storage: localStorage });
const fullBody = { asset_id: 'c', asset_type: 'character' as const, generation_type: 'full_body' as const,
    batch_size: 2, negative_prompt: '', apply_style: false, prompt: 'Full body', model_name: 'wan2.7-image' };
const wire = (channel: 'full_body' | 'reference_sheet', key = 'original', statuses = ['pending', 'pending']) => ({
    schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false,
    request: { id: '11111111-1111-4111-8111-111111111111', entrypoint: `studio.asset.${channel}.v1`,
        request_contract_version: 'request-json-v1', client_request_id: key, request_fingerprint: 'a'.repeat(64),
        request_root_kind: 'project', request_root_id: 'p', request_root_scope_id: '22222222-2222-4222-8222-222222222222',
        request_root_series_id: null, owner_kind: 'script', owner_id: 'p', asset_type: 'character', asset_id: 'c', channel,
        members: statuses.map((status, index) => ({ variant_id: `attempt-${index}`, attempt_id: `attempt-${index}`, status,
            ...(status === 'completed' ? { output_ref: `assets/studio/attempt-${index}.png` } : {}) })) },
});
const response = (value: unknown) => new Response(JSON.stringify(value));
let net: ReturnType<typeof vi.fn>;
beforeEach(() => {
    localStorage.clear(); activateRuntimeEnvironment({ schema_version: 1, storage_id: '33333333-3333-4333-8333-333333333333' });
    net = vi.fn(); vi.stubGlobal('fetch', net);
    vi.stubGlobal('navigator', { locks: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
});
afterEach(() => { clearRuntimeEnvironment(); vi.unstubAllGlobals(); });

describe('full-body Studio request identity', () => {
    it('accepts exactly the full-body receipt tuple with the existing member proof', () => {
        const value = wire('full_body', 'original', ['completed', 'needs_confirmation']);
        expect(validateStudioAssetImageReceipt(value)).toEqual(value);
    });
    it.each([
        { entrypoint: 'studio.asset.reference_sheet.v1' }, { channel: 'reference_sheet' },
        { asset_type: 'scene' }, { asset_type: 'prop' }, { channel: 'three_view' },
    ])('rejects mixed full-body tuple %j', change => {
        const value = wire('full_body'); Object.assign(value.request, change);
        expect(() => validateStudioAssetImageReceipt(value)).toThrow('studio_image_receipt_invalid');
    });
    it('retains full-body raw before one POST and recovers the original identity by GET', async () => {
        net.mockImplementation(async (_url, init) => {
            const stored = listStudioAssetImageRequests(owner());
            expect(stored).toHaveLength(1);
            expect(stored[0].request).toEqual({ ...fullBody, client_request_id: 'original' });
            if (init.method === 'POST') expect(JSON.parse(init.body)).toEqual(stored[0].request);
            return response(wire('full_body'));
        });
        const options = { ...owner(), input: fullBody, idFactory: () => 'original' };
        expect((await submitStudioAssetImageRequest(options)).status).toBe('confirmed');
        expect((await submitStudioAssetImageRequest(options)).status).toBe('unknown');
        expect(net).toHaveBeenCalledOnce();
        expect((await recoverStudioAssetImageRequests(owner()))[0].status).toBe('confirmed');
        expect(net.mock.calls.map(([, init]) => init.method)).toEqual(['POST', 'GET']);
    });
    it.each(['full_body', 'reference_sheet'] as const)('rejects a receipt from the other character channel for %s', async channel => {
        const other = channel === 'full_body' ? 'reference_sheet' : 'full_body';
        const input = channel === 'full_body' ? fullBody : { asset_id: 'c', batch_size: 2 };
        net.mockResolvedValue(response(wire(other)));
        const submitted = await submitStudioAssetImageRequest({ ...owner(), input, idFactory: () => 'original' });
        expect(submitted).toMatchObject({ status: 'unknown', reason: 'studio_image_receipt_mismatch' });
        expect(listStudioAssetImageRequests(owner())[0].receiptIdentity).toBeNull();
        net.mockResolvedValue(response(wire(channel)));
        const bound = await readStudioAssetImageRequestReceipt({ ...owner(), journal: submitted.journal });
        expect(bound.status).toBe('confirmed');
        net.mockResolvedValue(response(wire(other, 'original', ['completed', 'completed'])));
        expect(await readStudioAssetImageRequestReceipt({ ...owner(), journal: bound.journal }))
            .toMatchObject({ status: 'unknown', reason: 'studio_image_receipt_mismatch' });
        expect(listStudioAssetImageRequests(owner())[0].receiptIdentity).toBe(bound.journal.receiptIdentity);
    });
    it('keeps old omitted journals reference-sheet-only and refuses reusing their root key', async () => {
        const legacy = beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'c', batch_size: 2 }, idFactory: () => 'original' }).journal;
        const originalRaw = localStorage.getItem(localStorage.key(0)!);
        expect(() => beginStudioAssetImageRequest({ ...owner(), input: fullBody, idFactory: () => 'original' })).toThrow('studio_image_request_conflict');
        expect(localStorage.getItem(localStorage.key(0)!)).toBe(originalRaw);
        net.mockResolvedValue(response(wire('reference_sheet')));
        expect((await readStudioAssetImageRequestReceipt({ ...owner(), journal: legacy })).status).toBe('confirmed');
        const raw = listStudioAssetImageRequests(owner())[0].request;
        expect(raw).not.toHaveProperty('asset_type'); expect(raw).not.toHaveProperty('generation_type');
    });
    it.each([{ generation_type: 'full_body' }, { asset_type: 'scene', generation_type: 'full_body' },
        { asset_type: 'character', generation_type: 'three_view' }, { asset_type: 'character', generation_type: 'all' }])(
        'rejects unsupported or incomplete explicit tuples before persistence %j', tuple => {
            expect(() => beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'c', ...tuple } as never })).toThrow('studio_image_request_invalid');
            expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
        });
});
