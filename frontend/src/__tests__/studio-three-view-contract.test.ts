// @vitest-environment happy-dom
// UNRUN CANDIDATE. Install only after the six real HTTP samples are captured.
// Mutations below are intentional negative fixtures, never claimed as server output.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { validateStudioAssetImageReceipt } from '@/lib/studioAssetImageContract';
import { usesStudioImageCore } from '@/lib/useStudioAssetImageRequests';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, submitStudioAssetImageRequest,
    readStudioAssetImageRequestReceipt, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import capture from '@/__tests__/fixtures/studio-three-view-wire.json';

vi.mock('@/lib/appAccess', () => ({ appAccessFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
type Obj = Record<string, unknown>;
type Event = { label: string; body: Obj; request_body?: Obj; status_code: number };
type Capture = { cases: { case: string; events: Event[] }[] };
function actual(name: string, label: string): Event {
    const found = (capture as unknown as Capture).cases.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing real three-view capture: ${name}/${label}`);
    return structuredClone(found);
}
function command(name = 'local_single') {
    const { client_request_id, ...input } = actual(name, 'accepted_post').request_body!;
    if (typeof client_request_id !== 'string') throw new Error('Actual author key missing');
    return { input: input as StudioAssetImageRequestInput, idFactory: () => client_request_id };
}
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const options = () => ({ environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' }),
    rootKind: 'project' as const, rootId: 'p', storage: localStorage });
let net: ReturnType<typeof vi.fn>;
beforeEach(() => {
    localStorage.clear(); clearRuntimeEnvironment();
    net = vi.fn(async () => response(actual('local_single', 'accepted_post').body));
    vi.stubGlobal('fetch', net);
    vi.stubGlobal('navigator', { locks: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
});
afterEach(() => { vi.unstubAllGlobals(); clearRuntimeEnvironment(); });

it.each(['local_single', 'series_batch', 'global_single'])('accepts the actual project-root %s receipt without rewriting its identity', name => {
    for (const label of ['accepted_post', 'result_get']) {
        const raw = actual(name, label).body;
        expect(validateStudioAssetImageReceipt(raw)).toEqual(raw);
    }
});

it.each([
    { entrypoint: 'studio.asset.full_body.v1' }, { asset_type: 'scene' }, { channel: 'reference_sheet' },
])('rejects a fabricated inconsistent three-view tuple %j', change => {
    const raw = actual('local_single', 'result_get').body;
    Object.assign(raw.request as Obj, change);
    expect(() => validateStudioAssetImageReceipt(raw)).toThrow('studio_image_receipt_invalid');
});

// Contract-unit mutations of a project-root capture; not standalone-series HTTP evidence.
it('accepts an internally consistent standalone-series three-view receipt contract', () => {
    const raw = actual('series_batch', 'result_get').body;
    Object.assign(raw.request as Obj, { request_root_kind: 'series', request_root_id: 's', request_root_series_id: null,
        owner_kind: 'series', owner_id: 's' });
    expect(validateStudioAssetImageReceipt(raw)).toEqual(raw);
});

it('rejects a standalone-series three-view receipt with a different owner', () => {
    const raw = actual('series_batch', 'result_get').body;
    Object.assign(raw.request as Obj, { request_root_kind: 'series', request_root_id: 's', request_root_series_id: null,
        owner_kind: 'series', owner_id: 'another-series' });
    expect(() => validateStudioAssetImageReceipt(raw)).toThrow('studio_image_receipt_invalid');
});

it('stores the actual raw reference ID and path before a single POST; reuse of the same key does not resubmit', async () => {
    const original = command(), raw = actual('local_single', 'accepted_post').request_body!;
    net.mockImplementation(async (_url, init) => {
        expect(listStudioAssetImageRequests(options())).toHaveLength(1);
        expect(listStudioAssetImageRequests(options())[0].request).toEqual(raw);
        expect(JSON.parse(String(init.body))).toEqual(raw);
        return response(actual('local_single', 'accepted_post').body);
    });
    expect((await submitStudioAssetImageRequest({ ...options(), ...original })).status).toBe('confirmed');
    expect((await submitStudioAssetImageRequest({ ...options(), ...original })).status).toBe('unknown');
    expect(net).toHaveBeenCalledTimes(1);
    expect(listStudioAssetImageRequests(options())[0].request).toEqual(raw);
});

it('retains raw field presence and conflicts on null instead of normalizing an original three-view command', () => {
    const original = command();
    beginStudioAssetImageRequest({ ...options(), ...original });
    const before = localStorage.getItem(localStorage.key(0)!);
    expect(() => beginStudioAssetImageRequest({ ...options(), ...original,
        input: { ...original.input, reference_variant_id: null } as StudioAssetImageRequestInput })).toThrow();
    expect(localStorage.getItem(localStorage.key(0)!)).toBe(before);
});

it.each(['reference_sheet', 'full_body'])('rejects a reference_variant_id field on sibling %s even when null', generation_type => {
    expect(() => beginStudioAssetImageRequest({ ...options(), input: {
        asset_id: 'c', asset_type: 'character', generation_type, reference_variant_id: null,
    } as unknown as StudioAssetImageRequestInput, idFactory: () => 'sibling' })).toThrow();
    expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
});

it('stores a standalone-series three-view journal and reads its contract-unit receipt by the original key', async () => {
    const seriesOwner = { ...options(), rootKind: 'series' as const, rootId: 's' };
    const original = command('series_batch');
    const pending = beginStudioAssetImageRequest({ ...seriesOwner, ...original });
    expect(pending.status).toBe('created');
    expect(listStudioAssetImageRequests(seriesOwner)).toEqual([pending.journal]);
    expect(pending.journal.request).toEqual(actual('series_batch', 'accepted_post').request_body);
    expect(net).not.toHaveBeenCalled();
    const raw = actual('series_batch', 'result_get').body;
    Object.assign(raw.request as Obj, { request_root_kind: 'series', request_root_id: 's', request_root_series_id: null,
        owner_kind: 'series', owner_id: 's' });
    net.mockResolvedValue(response(raw));
    const read = await readStudioAssetImageRequestReceipt({ ...seriesOwner, journal: listStudioAssetImageRequests(seriesOwner)[0] });
    expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(raw);
    expect(listStudioAssetImageRequests(seriesOwner)[0].request).toEqual(pending.journal.request);
    expect(net).toHaveBeenCalledTimes(1); expect(net.mock.calls[0][1].method).toBe('GET');
    expect(String(net.mock.calls[0][0])).toBe(`${seriesOwner.environment.apiUrl}/series/s/asset_image_requests/${original.idFactory()}`);
});

it('keeps a lost ACK under its original key and recovers the actual receipt by GET only', async () => {
    net.mockRejectedValueOnce(new Error('Lost acknowledgement'));
    const unknown = await submitStudioAssetImageRequest({ ...options(), ...command() });
    expect(unknown.status).toBe('unknown'); expect(listStudioAssetImageRequests(options())).toHaveLength(1);
    const first = net.mock.calls[0];
    net.mockClear(); net.mockResolvedValue(response(actual('local_single', 'result_get').body));
    const read = await readStudioAssetImageRequestReceipt({ ...options(), journal: unknown.journal });
    expect(read.status).toBe('confirmed'); expect(net).toHaveBeenCalledTimes(1);
    expect(net.mock.calls[0][1].method).toBe('GET');
    expect(String(net.mock.calls[0][0])).toBe(`${first[0]}/${unknown.journal.request.client_request_id}`);
    expect(listStudioAssetImageRequests(options())[0].request).toEqual(actual('local_single', 'accepted_post').request_body);
});


// Existing GPT command fields reused as Wan admission-unit inputs; not Wan HTTP captures.
it.each(['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'])(
    'admits exact Wan three-view %s while preserving selected raw identity, zero seed and false watermark', model_name => {
        const original = command();
        const input = { ...original.input, model_name, apply_style: false, negative_prompt: '', seed: 0, watermark: false } as StudioAssetImageRequestInput;
        expect(usesStudioImageCore('character', model_name, 'three_view')).toBe(true);
        const saved = beginStudioAssetImageRequest({ ...options(), input, idFactory: original.idFactory });
        expect(saved.status).toBe('created');
        expect(saved.journal.request).toEqual({ ...input, client_request_id: original.idFactory() });
        expect(listStudioAssetImageRequests(options())[0].request).toEqual(saved.journal.request);
        expect(net).not.toHaveBeenCalled();
    });

it.each([{ reference_source: 'headshot_upload' }, { aspect_ratio: null }, { negative_prompt: 'blur' }])(
    'rejects unsupported strict Wan three-view fields before journaling %j', extra => {
        const original = command();
        expect(() => beginStudioAssetImageRequest({ ...options(), ...original,
            input: { ...original.input, model_name: 'wan2.7-image', ...extra } as StudioAssetImageRequestInput })).toThrow();
        expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
    });

it('keeps Wan three-view project-only and does not widen the headshot or unknown-model capability', () => {
    const original = command();
    expect(() => beginStudioAssetImageRequest({ ...options(), rootKind: 'series', rootId: 's', ...original,
        input: { ...original.input, model_name: 'wan2.7-image' } as StudioAssetImageRequestInput })).toThrow('studio_image_owner_invalid');
    expect(usesStudioImageCore('character', 'wan2.7-image', 'headshot')).toBe(false);
    expect(usesStudioImageCore('character', 'wan2.7-image-extra', 'three_view')).toBe(false);
    expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
});


// Existing command shape reused for Qwen admission units; no Qwen HTTP receipt is fabricated.
it.each(['qwen-image-2.0', 'qwen/qwen-image-2.0-pro#image'])(
    'admits selected-reference Qwen three-view %s without discarding authored negative, zero seed or false watermark', model_name => {
        const original = command();
        const input = { ...original.input, model_name, negative_prompt: 'Keep this authored negative',
            seed: 0, watermark: false } as StudioAssetImageRequestInput;
        expect(usesStudioImageCore('character', model_name, 'three_view')).toBe(true);
        const saved = beginStudioAssetImageRequest({ ...options(), input, idFactory: original.idFactory });
        expect(saved.status).toBe('created');
        expect(saved.journal.request).toEqual({ ...input, client_request_id: original.idFactory() });
        expect(listStudioAssetImageRequests(options())[0].request).toEqual(saved.journal.request);
        expect(net).not.toHaveBeenCalled();
    });

it.each([{ aspect_ratio: null }, { reference_source: 'headshot_upload' }])(
    'rejects unsupported Qwen three-view fields before journaling %j', extra => {
        const original = command();
        expect(() => beginStudioAssetImageRequest({ ...options(), ...original,
            input: { ...original.input, model_name: 'qwen-image-2.0', ...extra } as StudioAssetImageRequestInput })).toThrow();
        expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
    });

it('keeps selected-reference Qwen three-view project-only without widening headshot', () => {
    const original = command();
    expect(() => beginStudioAssetImageRequest({ ...options(), rootKind: 'series', rootId: 's', ...original,
        input: { ...original.input, model_name: 'qwen-image-2.0' } as StudioAssetImageRequestInput })).toThrow('studio_image_owner_invalid');
    expect(usesStudioImageCore('character', 'qwen-image-2.0', 'headshot')).toBe(false);
    expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
});
