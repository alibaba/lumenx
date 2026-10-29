// @vitest-environment happy-dom
import { createHash } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, readStudioAssetImageRequestReceipt,
    recoverStudioAssetImageRequests, resolveStudioAssetImageRequest, submitStudioAssetImageRequest } from '@/lib/studioAssetImageRequest';

vi.mock('@/lib/appAccess', () => ({ appAccessFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
type Kind = 'character' | 'scene' | 'prop';
const sid = '11111111-1111-4111-8111-111111111111';
const activate = () => activateRuntimeEnvironment({ schema_version: 1, storage_id: sid });
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'project-root', storage: localStorage });
const input = (kind: 'scene' | 'prop') => ({ asset_id: 'shared', asset_type: kind, generation_type: 'all' as const,
    seed: 0, watermark: false, apply_style: false, prompt: null, style_prompt: '', batch_size: 2 });
// Synthetic wire exercises parser + actual journal; no backend provenance is claimed.
const wire = (kind: Kind, key = 'original-key', statuses = ['pending', 'pending']) => ({
    schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false,
    request: {
        id: '22222222-2222-4222-8222-222222222222',
        entrypoint: kind === 'character' ? 'studio.asset.reference_sheet.v1' : 'studio.asset.scene_prop.v1',
        request_contract_version: 'request-json-v1', client_request_id: key, request_fingerprint: 'a'.repeat(64),
        request_root_kind: 'project', request_root_id: 'project-root', request_root_scope_id: '33333333-3333-4333-8333-333333333333',
        request_root_series_id: null, owner_kind: 'script', owner_id: 'project-root', asset_type: kind,
        asset_id: 'shared', channel: kind === 'character' ? 'reference_sheet' : 'image',
        members: statuses.map((status, i) => ({ variant_id: `attempt-${i}`, attempt_id: `attempt-${i}`, status,
            ...(status === 'completed' ? { output_ref: `assets/studio/attempt-${i}.png` } : {}) })),
    },
});
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
let net: ReturnType<typeof vi.fn>;
beforeEach(() => {
    localStorage.clear(); clearRuntimeEnvironment(); activate(); net = vi.fn(); vi.stubGlobal('fetch', net);
    vi.stubGlobal('navigator', { locks: { request: async (_name: string, _opts: unknown, callback: (lock: object) => unknown) => callback({}) } });
});
afterEach(() => { clearRuntimeEnvironment(); vi.unstubAllGlobals(); });

it('preserves the command input union while allowing the legacy omitted tuple', () => {
    type Input = Parameters<typeof submitStudioAssetImageRequest>[0]['input'];
    const accept = (value: Input) => value;
    expect(accept({ asset_id: 'shared' })).toEqual({ asset_id: 'shared' });
    expect(accept({ asset_id: 'shared', asset_type: 'character', generation_type: 'reference_sheet' }).asset_type).toBe('character');
    expect(accept(input('scene')).generation_type).toBe('all');
    expect(accept(input('prop')).generation_type).toBe('all');
    // @ts-expect-error A scene command must explicitly provide generation_type all.
    accept({ asset_id: 'shared', asset_type: 'scene' });
    // @ts-expect-error The reference_sheet generation type belongs only to character commands.
    accept({ asset_id: 'shared', asset_type: 'scene', generation_type: 'reference_sheet' });
    expect(net).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
});

it.each(['scene', 'prop'] as const)('stores exact %s raw before its only POST and recovers by original GET', async kind => {
    net.mockImplementation(async (_url, init) => {
        const stored = listStudioAssetImageRequests(owner());
        expect(stored).toHaveLength(1);
        expect(stored[0].request).toEqual({ ...input(kind), client_request_id: 'original-key' });
        if (init.method === 'POST') expect(JSON.parse(init.body)).toEqual(stored[0].request);
        return response(wire(kind));
    });
    const opts = { ...owner(), input: input(kind), idFactory: () => 'original-key' };
    const sent = await submitStudioAssetImageRequest(opts);
    expect(sent.status).toBe('confirmed');
    expect((await submitStudioAssetImageRequest(opts)).status).toBe('unknown');
    expect(net).toHaveBeenCalledTimes(1);
    expect((await recoverStudioAssetImageRequests(owner()))[0].status).toBe('confirmed');
    expect(net.mock.calls.map(([, init]) => init.method)).toEqual(['POST', 'GET']);
    expect(String(net.mock.calls[1][0])).toMatch(/\/projects\/project-root\/asset_image_requests\/original-key$/);
});

it.each([
    { asset_type: 'scene' }, { asset_type: 'prop' }, { generation_type: 'all' },
    { asset_type: 'scene', generation_type: 'reference_sheet' },
    { asset_type: 'prop', generation_type: 'image' }, { asset_type: 'character', generation_type: 'all' },
    { asset_type: 'scene', generation_type: undefined }, { asset_type: null }, { generation_type: null },
])('rejects a missing or invalid explicit command tuple before storage %j', tuple => {
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'shared', ...tuple } as never })).toThrow('studio_image_request_invalid');
    expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
});

it.each([['scene', 'prop'], ['prop', 'scene'], ['character', 'scene'], ['scene', 'character']] as const)(
    'rejects initial and terminal %s→%s receipt confusion for the same ID', async (expected, received) => {
        const body = expected === 'character' ? { asset_id: 'shared', batch_size: 2 } : input(expected);
        net.mockResolvedValue(response(wire(received)));
        const result = await submitStudioAssetImageRequest({ ...owner(), input: body, idFactory: () => 'original-key' });
        expect(result).toMatchObject({ status: 'unknown', reason: 'studio_image_receipt_mismatch' });
        expect(listStudioAssetImageRequests(owner())[0].receiptIdentity).toBeNull();
        net.mockResolvedValue(response(wire(expected)));
        const bound = await readStudioAssetImageRequestReceipt({ ...owner(), journal: result.journal });
        expect(bound.status).toBe('confirmed');
        net.mockResolvedValue(response(wire(received, 'original-key', ['completed', 'cancelled'])));
        const wrong = await readStudioAssetImageRequestReceipt({ ...owner(), journal: bound.journal });
        expect(wrong).toMatchObject({ status: 'unknown', reason: 'studio_image_receipt_mismatch' });
        expect(() => resolveStudioAssetImageRequest({ ...owner(), journal: bound.journal, receipt: wire(received, 'original-key', ['completed', 'cancelled']) as never })).toThrow();
        expect(listStudioAssetImageRequests(owner())[0].receiptIdentity).toBe(bound.journal.receiptIdentity);
    },
);

it('keeps one root key domain across kinds without replacing its old raw', () => {
    const first = beginStudioAssetImageRequest({ ...owner(), input: input('scene'), idFactory: () => 'same-key' });
    const stored = localStorage.getItem(localStorage.key(0)!);
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: input('prop'), idFactory: () => 'same-key' })).toThrow('studio_image_request_conflict');
    expect(localStorage.getItem(localStorage.key(0)!)).toBe(stored);
    expect(first.journal.request.asset_type).toBe('scene');
});

it.each([404, 503, 'lost'] as const)('retains both kinds after %s and only GETs original requests', async failure => {
    if (failure === 'lost') net.mockRejectedValue(Error('lost acknowledgement'));
    else net.mockResolvedValue(response({}, failure));
    for (const kind of ['scene', 'prop'] as const) {
        expect((await submitStudioAssetImageRequest({ ...owner(), input: input(kind), idFactory: () => kind })).status).toBe('unknown');
    }
    net.mockClear(); net.mockImplementation(async url => {
        const kind = String(url).split('/').at(-1) as 'scene' | 'prop'; return response(wire(kind, kind));
    });
    expect((await recoverStudioAssetImageRequests(owner())).map(result => result.status)).toEqual(['confirmed', 'confirmed']);
    expect(net.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
    expect(listStudioAssetImageRequests(owner())).toHaveLength(2);
});

it('resolves only the bound terminal scene journal, preserving an unknown prop request', async () => {
    net.mockResolvedValue(response(wire('scene', 'scene', ['completed', 'cancelled'])));
    const scene = await submitStudioAssetImageRequest({ ...owner(), input: input('scene'), idFactory: () => 'scene' });
    net.mockRejectedValue(Error('lost'));
    await submitStudioAssetImageRequest({ ...owner(), input: input('prop'), idFactory: () => 'prop' });
    expect(resolveStudioAssetImageRequest({ ...owner(), journal: scene.journal, receipt: scene.receipt! })).toBe(true);
    expect(listStudioAssetImageRequests(owner()).map(item => item.request.asset_type)).toEqual(['prop']);
});

it('does not bind a late new-kind POST after environment A→B→A', async () => {
    let done!: (response: Response) => void;
    net.mockImplementation(() => new Promise<Response>(resolve => { done = resolve; }));
    const pending = submitStudioAssetImageRequest({ ...owner(), input: input('scene'), idFactory: () => 'original-key' })
        .catch(error => ({ status: 'rejected', reason: String(error) }));
    await vi.waitFor(() => expect(net).toHaveBeenCalledTimes(1));
    clearRuntimeEnvironment(); activate(); done(response(wire('scene')));
    expect((await pending).status).toBe('unknown');
    expect(listStudioAssetImageRequests(owner())[0].receiptIdentity).toBeNull();
});

it.each([
    [{}, '42a4c161f29d76c2cf766e0dbe6adcef9993bb57fefc40b454073b5e935088e5'],
    [{ seed: 0, watermark: false }, 'c4aba8b690207674731139898036c88e043471e87bc0dd45d80ee0591207dc66'],
    [{ prompt: null }, 'b2858d04a3d813620500996c058d4047700f7f0237a548238c18273e86e98520'],
] as const)('preserves old omitted tuple raw and its existing backend hash %j', async (extra, expected) => {
    const body = { asset_id: 'legacy-character', ...extra };
    const journal = beginStudioAssetImageRequest({ ...owner(), input: body, idFactory: () => 'old-key' }).journal;
    const original = localStorage.getItem(localStorage.key(0)!);
    expect(localStorage.key(0)).toMatch(/^lumenx\.studioAssetImageRequest\.v1:/);
    expect(listStudioAssetImageRequests(owner())[0].request).toEqual({ ...body, client_request_id: 'old-key' });
    expect(journal.request).not.toHaveProperty('asset_type'); expect(journal.request).not.toHaveProperty('generation_type');
    expect(localStorage.getItem(localStorage.key(0)!)).toBe(original);
    const value = wire('character', 'old-key', ['pending']);
    value.request.asset_id = 'legacy-character'; value.request.request_fingerprint = expected;
    net.mockResolvedValue(response(value));
    const bound = await readStudioAssetImageRequestReceipt({ ...owner(), journal });
    expect(bound.status).toBe('confirmed');
    const { client_request_id: key, ...raw } = listStudioAssetImageRequests(owner())[0].request;
    expect(key).toBe('old-key'); expect(raw).toEqual(body);
    expect(raw).not.toHaveProperty('asset_type'); expect(raw).not.toHaveProperty('generation_type');
    const canonical = JSON.stringify({ entrypoint: 'studio.asset.reference_sheet.v1', request_contract_version: 'request-json-v1',
        request: { request_root_kind: 'project', request_root_id: 'project-root', body: raw } }, (_key, value) =>
        value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value);
    expect(createHash('sha256').update(canonical).digest('hex')).toBe(expected);
});
