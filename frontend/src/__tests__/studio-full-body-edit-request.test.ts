// @vitest-environment happy-dom
// Admission/journal tests plus actual full-body HTTP receipts captured from the
// real backend with external transport substituted. UI projection is a separate gate.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, isStudioFullBodyEditRequest, listStudioAssetImageRequests,
    readStudioAssetImageRequestReceipt, submitStudioAssetImageRequest, resolveStudioAssetImageRequest,
    type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import capture from '@/__tests__/fixtures/studio-full-body-edit-wire.json';

vi.mock('@/lib/appAccess', () => ({ appAccessFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
type Obj = Record<string, unknown>;
const SID = '11111111-1111-4111-8111-111111111111';
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p', storage: localStorage });
const input = () => ({ asset_id: 'c', asset_type: 'character', generation_type: 'full_body',
    model_name: 'gpt-image-2', reference_variant_id: 'uploaded-three-view-a', reference_image_url: 'uploads/three-view-a.png',
    prompt: 'My authored full-body design', apply_style: false, negative_prompt: '', batch_size: 1,
    aspect_ratio: '9:16' } satisfies StudioAssetImageRequestInput);
// Deliberately malformed browser data must reach runtime admission, including null field presence.
const invalidInput = (change: Obj) => ({ ...input(), ...change }) as unknown as StudioAssetImageRequestInput;
const unknownResponse = () => new Response(JSON.stringify({ detail: 'Original request remains unconfirmed' }), { status: 404 });
let net: ReturnType<typeof vi.fn>;
beforeEach(() => {
    localStorage.clear(); clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    net = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => unknownResponse()); vi.stubGlobal('fetch', net);
    vi.stubGlobal('navigator', { locks: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
});
afterEach(() => { vi.unstubAllGlobals(); clearRuntimeEnvironment(); });

it.each(['gpt-image-2', 'gpt-image/gpt-image-2#image'])(
    'admits saved-model-shaped %s full-body input with every existing aspect label without changing raw fields', model_name => {
        for (const aspect_ratio of ['9:16', '3:4', '1:1', '4:3', '16:9']) {
            const raw = { ...input(), model_name, aspect_ratio };
            const key = `full-body-${aspect_ratio.replace(':', '-')}`;
            const accepted = beginStudioAssetImageRequest({ ...owner(), input: raw, idFactory: () => key, now: () => 7 });
            expect(accepted.status).toBe('created');
            expect(accepted.journal.request).toEqual({ ...raw, client_request_id: key });
            expect(isStudioFullBodyEditRequest(accepted.journal.request)).toBe(true);
        }
        expect(listStudioAssetImageRequests(owner())).toHaveLength(5); expect(net).not.toHaveBeenCalled();
    });

it.each(['omitted', 'explicit-null'])(
    'retains %s model and aspect fields so the backend can use saved defaults', presence => {
        const raw: StudioAssetImageRequestInput = input();
        if (presence === 'omitted') { delete raw.model_name; delete raw.aspect_ratio; }
        else { raw.model_name = null; raw.aspect_ratio = null; }
        const accepted = beginStudioAssetImageRequest({ ...owner(), input: raw, idFactory: () => 'saved-defaults', now: () => 7 });
        expect(accepted.journal.request).toEqual({ ...raw, client_request_id: 'saved-defaults' });
        expect(Object.hasOwn(accepted.journal.request, 'model_name')).toBe(presence === 'explicit-null');
        expect(Object.hasOwn(accepted.journal.request, 'aspect_ratio')).toBe(presence === 'explicit-null');
        expect(net).not.toHaveBeenCalled();
    });

it.each([
    { reference_variant_id: null }, { reference_variant_id: '' }, { reference_image_url: null }, { reference_image_url: '' },
    { reference_image_url: ' uploads/three-view-a.png' }, { model_name: 'wan2.7-image' }, { negative_prompt: 'blur' },
    { negative_prompt: ' ' }, { seed: 0 }, { seed: null }, { watermark: false }, { watermark: null },
    { aspect_ratio: '2:3' }, { aspect_ratio: '' },
])('rejects an unsupported full-body edit field before journal or transport %j', change => {
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: invalidInput(change), idFactory: () => 'unsupported' })).toThrow();
    expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
});

// Contract-unit root/owner mutation of a project-root capture, not standalone HTTP evidence.
it('stores a standalone-series full-body edit journal and reads its receipt by the original key', async () => {
    const seriesOwner = { ...owner(), rootKind: 'series' as const, rootId: 's' };
    const original = capturedCommand('series_batch');
    const pending = beginStudioAssetImageRequest({ ...seriesOwner, ...original });
    expect(pending.status).toBe('created');
    expect(isStudioFullBodyEditRequest(pending.journal.request)).toBe(true);
    expect(listStudioAssetImageRequests(seriesOwner)).toEqual([pending.journal]);
    expect(pending.journal.request).toEqual(actualEvent('series_batch', 'accepted_post').request_body);
    expect(net).not.toHaveBeenCalled();
    const event = actualEvent('series_batch');
    Object.assign(event.body.request as Obj, { request_root_kind: 'series', request_root_id: 's', request_root_series_id: null,
        owner_kind: 'series', owner_id: 's' });
    net.mockResolvedValue(new Response(JSON.stringify(event.body), { status: event.status_code }));
    const read = await readStudioAssetImageRequestReceipt({ ...seriesOwner, journal: listStudioAssetImageRequests(seriesOwner)[0] });
    expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(event.body);
    expect(listStudioAssetImageRequests(seriesOwner)[0].request).toEqual(pending.journal.request);
    expect(net).toHaveBeenCalledTimes(1); expect(net.mock.calls[0][1]?.method).toBe('GET');
    expect(String(net.mock.calls[0][0])).toBe(`${seriesOwner.environment.apiUrl}/series/s/asset_image_requests/${original.idFactory()}`);
});

it('preserves historical standalone-series raw journal reads', async () => {
    const seriesOwner = { ...owner(), rootKind: 'series' as const, rootId: 's' };
    // The pre-edit client accepted this raw shape even though backend admission was unsupported.
    const oldRaw = { asset_id: 'c', asset_type: 'character' as const, generation_type: 'full_body' as const,
        model_name: 'wan2.7-image', prompt: 'Retained old input' };
    beginStudioAssetImageRequest({ ...seriesOwner, input: oldRaw, idFactory: () => 'historical', now: () => 7 });
    const name = localStorage.key(0)!;
    const retained = JSON.parse(localStorage.getItem(name)!);
    retained.request.reference_image_url = 'uploads/historical.png';
    const bytes = JSON.stringify(retained); localStorage.setItem(name, bytes);
    expect(listStudioAssetImageRequests(seriesOwner)[0].request).toEqual(retained.request);
    expect(beginStudioAssetImageRequest({ ...seriesOwner, input: { ...oldRaw, reference_image_url: 'uploads/historical.png' },
        idFactory: () => 'historical' }).status).toBe('pending');
    expect(localStorage.getItem(name)).toBe(bytes); expect(net).not.toHaveBeenCalled();
    const recovered = await readStudioAssetImageRequestReceipt({
        ...seriesOwner, journal: listStudioAssetImageRequests(seriesOwner)[0],
    });
    expect(recovered).toMatchObject({ status: 'unknown', reason: 'studio_image_http_404' });
    expect(net).toHaveBeenCalledTimes(1);
    expect(String(net.mock.calls[0][0])).toBe(seriesOwner.environment.apiUrl + '/series/s/asset_image_requests/historical');
    expect(net.mock.calls[0][1]).toMatchObject({ method: 'GET' });
    expect(net.mock.calls[0][1]).not.toHaveProperty('body');
    expect(localStorage.getItem(name)).toBe(bytes);
    expect(listStudioAssetImageRequests(seriesOwner)[0].request).toEqual(retained.request);
});

it('binds the author before the exact original POST, then recovers a lost ACK by original-key GET only', async () => {
    const order: string[] = [];
    net.mockImplementationOnce(async (_url, init) => {
        order.push('POST');
        expect(listStudioAssetImageRequests(owner())[0].request).toEqual({ ...input(), client_request_id: 'full-body-one' });
        expect(JSON.parse(String(init.body))).toEqual(listStudioAssetImageRequests(owner())[0].request);
        throw new Error('Deliberately lost full-body acknowledgement');
    });
    const sent = await submitStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'full-body-one',
        onCreated: journal => { order.push('created'); expect(journal.request).toEqual({ ...input(), client_request_id: 'full-body-one' }); } });
    expect(sent.status).toBe('unknown'); expect(order).toEqual(['created', 'POST']);
    const original = sent.journal.request, name = localStorage.key(0)!, bytes = localStorage.getItem(name);
    expect((await submitStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'full-body-one' })).status).toBe('unknown');
    expect(net).toHaveBeenCalledTimes(1); expect(localStorage.getItem(name)).toBe(bytes);
    net.mockClear();
    expect((await readStudioAssetImageRequestReceipt({ ...owner(), journal: sent.journal })).status).toBe('unknown');
    expect(net).toHaveBeenCalledTimes(1);
    expect(String(net.mock.calls[0][0])).toMatch(/\/projects\/p\/asset_image_requests\/full-body-one$/);
    expect(net.mock.calls[0][1].method).toBe('GET');
    expect(listStudioAssetImageRequests(owner())[0].request).toEqual(original);
    expect(localStorage.getItem(name)).toBe(bytes);
});

it('rechecks author validity after onCreated and retains an unsent original journal when it changed', async () => {
    let current = true;
    await expect(submitStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'not-sent', isCurrent: () => current,
        onCreated: () => { current = false; } })).rejects.toThrow('studio_image_request_cancelled');
    expect(net).not.toHaveBeenCalled();
    const retained = listStudioAssetImageRequests(owner()); expect(retained).toHaveLength(1);
    expect(retained[0].request).toEqual({ ...input(), client_request_id: 'not-sent' });
    expect(retained[0].receiptIdentity).toBeNull();
});

it('keeps omitted and explicit-null author fields distinct under the original key', () => {
    beginStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'one', now: () => 7 });
    const name = localStorage.key(0)!, bytes = localStorage.getItem(name);
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: { ...input(), style_prompt: null }, idFactory: () => 'one', now: () => 7 }))
        .toThrow('studio_image_request_conflict');
    expect(localStorage.getItem(name)).toBe(bytes); expect(net).not.toHaveBeenCalled();
});

it('keeps ordinary full-body T2I and its independently stored three-view sibling unchanged', async () => {
    const t2i = { asset_id: 'c', asset_type: 'character' as const, generation_type: 'full_body' as const,
        model_name: 'wan2.7-image', prompt: 'Ordinary full-body T2I', apply_style: false, batch_size: 1 };
    const old = beginStudioAssetImageRequest({ ...owner(), input: t2i, idFactory: () => 't2i', now: () => 7 });
    expect(isStudioFullBodyEditRequest(old.journal.request)).toBe(false);
    beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'c', asset_type: 'character', generation_type: 'three_view',
        model_name: 'gpt-image-2', reference_variant_id: 'selected-c', reference_image_url: 'uploads/selected-c.png',
        prompt: 'Separate three-view author input', negative_prompt: '' }, idFactory: () => 'three-view', now: () => 8 });
    const before = Array.from({ length: localStorage.length }, (_, index) => {
        const key = localStorage.key(index)!; return [key, localStorage.getItem(key)] as const;
    });
    expect((await submitStudioAssetImageRequest({ ...owner(), input: input(), idFactory: () => 'edit' })).status).toBe('unknown');
    for (const [key, bytes] of before) expect(localStorage.getItem(key)).toBe(bytes);
    expect(listStudioAssetImageRequests(owner()).map(value => value.request.client_request_id).sort()).toEqual(['edit', 't2i', 'three-view']);
    expect(net).toHaveBeenCalledTimes(1);
});


type WireEvent = { label: string; method: string; path: string; status_code: number; body: Obj; request_body?: Obj };
type WireCapture = { cases: { case: string; events: WireEvent[] }[] };
function actualEvent(name: string, label = 'result_get'): WireEvent {
    const event = (capture as unknown as WireCapture).cases.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!event) throw new Error(`Missing actual full-body HTTP event: ${name}/${label}`);
    return structuredClone(event);
}
function capturedCommand(name: string) {
    const raw = actualEvent(name, 'accepted_post').request_body;
    if (!raw || typeof raw.client_request_id !== 'string') throw new Error('Missing actual full-body command');
    const { client_request_id, ...input } = raw;
    return { input: input as StudioAssetImageRequestInput, idFactory: () => client_request_id, now: () => 7 };
}
function queueGet(event: WireEvent) {
    net.mockImplementationOnce(async (url, init) => {
        expect(event.method).toBe('GET');
        expect(String(url)).toBe(owner().environment.apiUrl + event.path);
        expect(init).toMatchObject({ method: 'GET' }); expect(init).not.toHaveProperty('body');
        return new Response(JSON.stringify(event.body), { status: event.status_code });
    });
}

it.each(['local_single', 'series_batch'])(
    '%s binds the actual completion through original-key GET and removes only that resolved journal', async name => {
        const original = capturedCommand(name), raw = actualEvent(name, 'accepted_post').request_body;
        const pending = beginStudioAssetImageRequest({ ...owner(), ...original });
        const key = localStorage.key(0)!;
        beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'c', asset_type: 'character', generation_type: 'full_body',
            model_name: 'wan2.7-image', prompt: 'Separate retained T2I draft' }, idFactory: () => 'separate-t2i', now: () => 8 });
        const siblingKey = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index)!).find(value => value !== key)!;
        const siblingBytes = localStorage.getItem(siblingKey);
        expect(pending.journal.request).toEqual(raw); expect(pending.journal.receiptIdentity).toBeNull();
        const event = actualEvent(name); queueGet(event);
        const read = await readStudioAssetImageRequestReceipt({ ...owner(), journal: pending.journal });
        expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(event.body);
        expect(read.receipt?.request.members.every(member => member.status === 'completed')).toBe(true);
        expect(read.journal.receiptIdentity).not.toBeNull();
        expect(localStorage.getItem(key)).not.toBeNull();
        expect(resolveStudioAssetImageRequest({ ...owner(), journal: read.journal, receipt: read.receipt! })).toBe(true);
        expect(localStorage.getItem(key)).toBeNull(); expect(localStorage.getItem(siblingKey)).toBe(siblingBytes);
        expect(listStudioAssetImageRequests(owner()).map(journal => journal.request.client_request_id)).toEqual(['separate-t2i']);
        expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['GET']);
        expect(actualEvent(name)).toEqual(event);
    });

it.each(['local_partial', 'global_unknown'])(
    '%s retains the actual incomplete journal across repeated original-key GET without another POST', async name => {
        const original = capturedCommand(name), pending = beginStudioAssetImageRequest({ ...owner(), ...original });
        const key = localStorage.key(0)!;
        const event = actualEvent(name); queueGet(event);
        const read = await readStudioAssetImageRequestReceipt({ ...owner(), journal: pending.journal });
        expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(event.body);
        expect(read.receipt?.request.members.map(member => member.status)).toEqual(name === 'local_partial'
            ? ['completed', 'needs_confirmation', 'cancelled'] : ['needs_confirmation', 'cancelled']);
        expect(read.journal.receiptIdentity).not.toBeNull();
        const bytes = localStorage.getItem(key);
        expect(resolveStudioAssetImageRequest({ ...owner(), journal: read.journal, receipt: read.receipt! })).toBe(false);
        expect(localStorage.getItem(key)).toBe(bytes);
        const previousEnvironment = owner().environment;
        if (name === 'global_unknown') {
            clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
            expect(owner().environment.key).toBe(previousEnvironment.key);
            expect(owner().environment.generation).not.toBe(previousEnvironment.generation);
        }
        const repeated = actualEvent(name, name === 'global_unknown' ? 'reopen_get' : 'result_get'); queueGet(repeated);
        const recovered = await readStudioAssetImageRequestReceipt({ ...owner(), journal: listStudioAssetImageRequests(owner())[0] });
        expect(recovered.status).toBe('confirmed'); expect(recovered.receipt).toEqual(repeated.body);
        expect(recovered.journal.receiptIdentity).toBe(read.journal.receiptIdentity);
        expect(resolveStudioAssetImageRequest({ ...owner(), journal: recovered.journal, receipt: recovered.receipt! })).toBe(false);
        expect((await submitStudioAssetImageRequest({ ...owner(), ...original })).reason).toBe('studio_image_request_already_recorded');
        expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['GET', 'GET']);
        expect(localStorage.getItem(key)).toBe(bytes);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(listStudioAssetImageRequests(owner())[0].request).toEqual(pending.journal.request);
    });

it.each(['all-cancelled', 'receipt-reason', 'completed-member-reason'])(
    'retains the original full-body journal for explicit negative %s mutation of an actual response', async mutation => {
        const pending = beginStudioAssetImageRequest({ ...owner(), ...capturedCommand('local_single') });
        const key = localStorage.key(0)!, actual = actualEvent('local_single'), event = structuredClone(actual);
        const members = (event.body.request as Obj).members as Obj[];
        // Negative-only fault seams. No successful status or output is manufactured.
        if (mutation === 'all-cancelled') {
            for (const member of members) { member.status = 'cancelled'; delete member.output_ref; }
        } else if (mutation === 'receipt-reason') event.body.reason = 'studio_image_request_projection_changed';
        else members[0].reason = 'studio_image_request_inputs_changed';
        queueGet(event);
        const read = await readStudioAssetImageRequestReceipt({ ...owner(), journal: pending.journal });
        expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(event.body);
        expect(read.journal.receiptIdentity).not.toBeNull();
        const bytes = localStorage.getItem(key);
        expect(resolveStudioAssetImageRequest({ ...owner(), journal: read.journal, receipt: read.receipt! })).toBe(false);
        expect(localStorage.getItem(key)).toBe(bytes); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(listStudioAssetImageRequests(owner())[0].request).toEqual(pending.journal.request);
        expect((await submitStudioAssetImageRequest({ ...owner(), ...capturedCommand('local_single') })).reason)
            .toBe('studio_image_request_already_recorded');
        expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['GET']);
        expect(actualEvent('local_single')).toEqual(actual);
    });
