// @vitest-environment happy-dom
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, submitStudioAssetImageRequest,
    readStudioAssetImageRequestReceipt, recoverStudioAssetImageRequests, resolveStudioAssetImageRequest } from '@/lib/studioAssetImageRequest';
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
const sid = '11111111-1111-4111-8111-111111111111';
const rid = '22222222-2222-4222-8222-222222222222';
const scope = '33333333-3333-4333-8333-333333333333';
let net: ReturnType<typeof vi.fn>;
const environment = () => activateRuntimeEnvironment({ schema_version: 1, storage_id: sid });
const options = () => ({ environment: environment(), rootKind: 'project' as const, rootId: 'p', storage: localStorage });
const input = { asset_id: 'c', apply_style: false, seed: 0, watermark: false };
const receipt = (status = 'pending', changes = {}) => ({ schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false, reason: null, request: {
    id: rid, entrypoint: 'studio.asset.reference_sheet.v1', request_contract_version: 'request-json-v1',
    client_request_id: 'request-1', request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: 'p',
    request_root_scope_id: scope, request_root_series_id: null, owner_kind: 'script', owner_id: 'p',
    asset_type: 'character', asset_id: 'c', channel: 'reference_sheet',
    members: [{ variant_id: 'attempt-1', attempt_id: 'attempt-1', status, reason: null,
        ...(status === 'completed' ? { output_ref: 'assets/studio/attempt-1.png' } : {}) }], ...changes,
} });
const response = (value: unknown = receipt(), status = 200) => new Response(JSON.stringify(value), { status });
const create = () => submitStudioAssetImageRequest({ ...options(), input, idFactory: () => 'request-1' });
beforeEach(() => {
    localStorage.clear(); clearRuntimeEnvironment();
    net = vi.fn(async () => response()); vi.stubGlobal('fetch', net);
    const held = new Set<string>();
    vi.stubGlobal('navigator', { locks: { request: async (name: string, _options: unknown, callback: (lock: object | null) => unknown) => {
        if (held.has(name)) return callback(null);
        held.add(name); try { return await callback({}); } finally { held.delete(name); }
    } } });
});
afterEach(() => vi.unstubAllGlobals());

it('verifies exact body in storage before one POST and never resends a stored key', async () => {
    net.mockImplementation(async (_url, init) => {
        const stored = listStudioAssetImageRequests(options());
        expect(stored).toHaveLength(1);
        expect(stored[0].request).toEqual({ ...input, client_request_id: 'request-1' });
        expect(JSON.parse(init.body)).toEqual(stored[0].request);
        expect(new Headers(init.headers).get('X-LumenX-Environment')).toBe(sid);
        return response();
    });
    expect((await create()).status).toBe('confirmed');
    expect((await create()).status).toBe('unknown');
    expect(net).toHaveBeenCalledTimes(1);
});
it('retains separate unknown requests and distinguishes omitted/null fields', () => {
    expect(beginStudioAssetImageRequest({ ...options(), input, idFactory: () => 'request-1' }).status).toBe('created');
    expect(beginStudioAssetImageRequest({ ...options(), input, idFactory: () => 'request-1' }).status).toBe('pending');
    expect(() => beginStudioAssetImageRequest({ ...options(), input: { ...input, prompt: null }, idFactory: () => 'request-1' })).toThrow();
    beginStudioAssetImageRequest({ ...options(), input, idFactory: () => 'request-2' });
    expect(listStudioAssetImageRequests(options())).toHaveLength(2);
});
it.each(['write', 'readback', 'lock'])('does not POST if %s is unavailable', async kind => {
    if (kind === 'lock') vi.stubGlobal('navigator', {});
    const storage = kind === 'lock' ? localStorage : {
        get length() { return localStorage.length; }, key: (i: number) => localStorage.key(i),
        getItem: (key: string) => kind === 'readback' ? null : localStorage.getItem(key),
        setItem: (key: string, value: string) => { if (kind === 'write') throw Error('full'); localStorage.setItem(key, value); },
        removeItem: (key: string) => localStorage.removeItem(key), clear: () => localStorage.clear(),
    } as Storage;
    await expect(submitStudioAssetImageRequest({ ...options(), input, storage, idFactory: () => 'request-1' })).rejects.toThrow();
    expect(net).not.toHaveBeenCalled();
});
it('serializes concurrent submissions', async () => {
    let done!: () => void;
    net.mockImplementation(async () => { await new Promise<void>(resolve => { done = resolve; }); return response(); });
    const first = create(); await vi.waitFor(() => expect(net).toHaveBeenCalledTimes(1));
    await expect(create()).rejects.toThrow(); done(); await first;
    expect(net).toHaveBeenCalledTimes(1);
});
it.each([404, 500, 'lost', 'bad'])('retains evidence after %s and recovers by GET only', async failure => {
    if (failure === 'lost') net.mockRejectedValueOnce(Error('ACK lost'));
    else net.mockResolvedValueOnce(failure === 'bad' ? response({ unexpected: true }) : response({}, typeof failure === 'number' ? failure : 500));
    expect((await create()).status).toBe('unknown');
    expect(listStudioAssetImageRequests(options())).toHaveLength(1);
    net.mockClear(); net.mockResolvedValue(response());
    expect((await recoverStudioAssetImageRequests(options()))[0].status).toBe('confirmed');
    expect(net.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
    expect(listStudioAssetImageRequests(options())).toHaveLength(1);
});
it.each([{ request_root_id: 'wrong' }, { asset_id: 'wrong' }, { client_request_id: 'wrong' }, { members: [] }, { request_root_kind: 'series' }])('rejects mismatched initial identity %j', async changes => {
    net.mockResolvedValue(response(receipt('pending', changes)));
    expect((await create()).status).toBe('unknown');
    expect(listStudioAssetImageRequests(options())[0].receiptIdentity).toBeNull();
});
it.each([{ request_fingerprint: 'b'.repeat(64) }, { request_root_scope_id: rid }, { owner_kind: 'global', owner_id: 'library' }, { id: scope }])('pins initial identity against later changes %j', async changes => {
    const sent = await create(); net.mockResolvedValue(response(receipt('completed', changes)));
    expect((await readStudioAssetImageRequestReceipt({ ...options(), journal: sent.journal })).status).toBe('unknown');
    expect(listStudioAssetImageRequests(options())).toHaveLength(1);
});
it('only removes same journal with all members confirmed terminal', async () => {
    const sent = await create();
    expect(resolveStudioAssetImageRequest({ ...options(), journal: sent.journal, receipt: sent.receipt! })).toBe(false);
    net.mockResolvedValue(response(receipt('completed')));
    const read = await readStudioAssetImageRequestReceipt({ ...options(), journal: sent.journal });
    expect(resolveStudioAssetImageRequest({ ...options(), journal: read.journal, receipt: read.receipt! })).toBe(true);
    expect(listStudioAssetImageRequests(options())).toEqual([]);
});
it('rejects A-B-A responses and stale-environment reads', async () => {
    let done!: (value: Response) => void;
    net.mockImplementationOnce(() => new Promise<Response>(resolve => { done = resolve; }));
    const original = options(); const pending = submitStudioAssetImageRequest({ ...original, input, idFactory: () => 'request-1' });
    await vi.waitFor(() => expect(net).toHaveBeenCalledTimes(1));
    clearRuntimeEnvironment(); environment(); done(response());
    const sent = await pending; expect(sent.status).toBe('unknown');
    expect(listStudioAssetImageRequests(options())[0].receiptIdentity).toBeNull(); net.mockClear();
    expect((await readStudioAssetImageRequestReceipt({ ...original, journal: sent.journal })).status).toBe('unknown');
    expect(net).not.toHaveBeenCalled();
});
it('isolates roots and runtimes and rejects corrupt storage', () => {
    beginStudioAssetImageRequest({ ...options(), input, idFactory: () => 'request-1' });
    expect(listStudioAssetImageRequests({ ...options(), rootId: 'else' })).toEqual([]);
    expect(listStudioAssetImageRequests({ ...options(), environment: { ...environment(), storageId: rid } })).toEqual([]);
    localStorage.setItem(localStorage.key(0)!, '{');
    expect(() => listStudioAssetImageRequests(options())).toThrow();
});
it.each([{ seed: null }, { seed: -1 }, { batch_size: NaN }, { prompt: undefined }, { extra: 'field' }, { batch_size: true }])('rejects invalid raw input %j', changes => {
    expect(() => beginStudioAssetImageRequest({ ...options(), input: { ...input, ...changes } as never, idFactory: () => 'request-1' })).toThrow();
    expect(localStorage.length).toBe(0);
});

it('rejects a URL or storageId paired with another live environment key', async () => {
    for (const changed of [{ apiUrl: 'https://not-current.invalid/api' }, { storageId: rid }]) {
        const base = options();
        await expect(submitStudioAssetImageRequest({ ...base, environment: { ...base.environment, ...changed }, input, idFactory: () => 'request-1' })).rejects.toThrow();
    }
    expect(net).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
});
it('does not downgrade a known receipt binding when stored identity disappears', async () => {
    const sent = await create();
    const key = localStorage.key(0)!;
    localStorage.setItem(key, JSON.stringify({ ...JSON.parse(localStorage.getItem(key)!), receiptIdentity: null }));
    net.mockClear(); net.mockResolvedValue(response(receipt('pending', { request_fingerprint: 'b'.repeat(64) })));
    expect((await readStudioAssetImageRequestReceipt({ ...options(), journal: sent.journal })).status).toBe('unknown');
    expect(net).not.toHaveBeenCalled();
    expect(listStudioAssetImageRequests(options())[0].receiptIdentity).toBeNull();
});
it.each(['symbol', 'hidden', 'getter'])('rejects %s raw fields before a spread can discard them', kind => {
    const raw = { ...input };
    if (kind === 'symbol') Object.defineProperty(raw, Symbol('private'), { value: 'lost', enumerable: true });
    if (kind === 'hidden') Object.defineProperty(raw, 'prompt', { value: 'lost', enumerable: false });
    if (kind === 'getter') Object.defineProperty(raw, 'prompt', { get: () => 'unstable', enumerable: true });
    expect(() => beginStudioAssetImageRequest({ ...options(), input: raw, idFactory: () => 'request-1' })).toThrow();
    expect(localStorage.length).toBe(0);
});
it('forbids redirects on both POST and original receipt GET', async () => {
    const sent = await create();
    await readStudioAssetImageRequestReceipt({ ...options(), journal: sent.journal });
    expect(net.mock.calls).toHaveLength(2);
    expect(net.mock.calls.every(([, init]) => init.redirect === 'error')).toBe(true);
});

// Fixed schema-1 bytes protect persisted requests when storage primitives move.
// Do not obtain these expectations from the journal's serializer or key helper.
function legacyJournalFixture(rootKind: 'project' | 'series') {
    const owner = { ...options(), rootKind };
    const apiBase = JSON.stringify(owner.environment.apiUrl);
    const prefix = 'lumenx.studioAssetImageRequest.v1:';
    const identity = `{"apiBase":${apiBase},"rootId":"p","rootKind":"${rootKind}","storageId":"11111111-1111-4111-8111-111111111111"}`;
    const name = `${prefix}${encodeURIComponent(identity)}:legacy-1`;
    const raw = `{"apiBase":${apiBase},"createdAt":7,"receiptIdentity":null,"request":{"apply_style":false,"asset_id":"c","client_request_id":"legacy-1","seed":0,"watermark":false},"rootId":"p","rootKind":"${rootKind}","schema_version":1,"storageId":"11111111-1111-4111-8111-111111111111"}`;
    return { owner, name, raw };
}

it.each(['project', 'series'] as const)('keeps the exact existing %s journal namespace and serialized bytes', rootKind => {
    const { owner, name, raw } = legacyJournalFixture(rootKind);
    const begun = beginStudioAssetImageRequest({ ...owner, input, idFactory: () => 'legacy-1', now: () => 7 });
    expect(begun.status).toBe('created');
    expect(localStorage.length).toBe(1);
    expect(localStorage.key(0)).toBe(name);
    expect(localStorage.getItem(name)).toBe(raw);
    expect(net).not.toHaveBeenCalled();
});

it.each(['project', 'series'] as const)('reads and reuses an existing %s journal without rewriting its raw bytes', rootKind => {
    const { owner, name, raw } = legacyJournalFixture(rootKind);
    const historical = ` \n${raw}\n`;
    localStorage.setItem(name, historical);
    localStorage.setItem('unrelated-setting', 'keep exactly');
    const records = listStudioAssetImageRequests(owner);
    expect(records).toHaveLength(1);
    expect(records[0].request).toEqual({ ...input, client_request_id: 'legacy-1' });
    expect(beginStudioAssetImageRequest({ ...owner, input, idFactory: () => 'legacy-1', now: () => 999 })).toEqual({
        status: 'pending', journal: records[0],
    });
    expect(localStorage.getItem(name)).toBe(historical);
    expect(localStorage.getItem('unrelated-setting')).toBe('keep exactly');
    expect(localStorage.length).toBe(2);
    expect(net).not.toHaveBeenCalled();
});
