// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import wire from './fixtures/storyboard-qwen-image-wire.json';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { validateStudioAssetImageReceipt } from '@/lib/studioAssetImageContract';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, readStudioAssetImageRequestReceipt,
    submitStudioAssetImageRequest, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import { usesStudioQwenImageCore } from '@/lib/useStudioAssetImageRequests';

vi.mock('@/lib/appAccess', () => ({ appAccessFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
const sid = '11111111-1111-4111-8111-111111111111';
const owner = () => ({ environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: sid }), rootKind: 'project' as const, rootId: 'p' });
const input = () => { const body: Partial<typeof wire.body> = { ...wire.body }; delete body.client_request_id; return body as StudioAssetImageRequestInput; };
let net: ReturnType<typeof vi.fn>;
beforeEach(() => {
    localStorage.clear(); clearRuntimeEnvironment();
    net = vi.fn(async () => new Response(JSON.stringify(wire.admission))); vi.stubGlobal('fetch', net);
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, cb: (lock: object) => unknown) => cb({}) } });
});
afterEach(() => vi.unstubAllGlobals());

it.each(['admission', 'readback'] as const)('accepts unchanged real HTTP frame %s wire', phase => {
    expect(validateStudioAssetImageReceipt(wire[phase])).toEqual(wire[phase]);
});
it.each(['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'])('recognizes only the existing Qwen frame image tuple: %s', model => {
    expect(usesStudioQwenImageCore('storyboard_frame', model, 'image')).toBe(true);
    expect(usesStudioQwenImageCore('storyboard_frame', model, 'full_body')).toBe(false);
    expect(usesStudioQwenImageCore('storyboard_frame', 'wan2.7-image', 'image')).toBe(false);
});
it.each([
    { owner_kind: 'global', owner_id: 'library' },
    { owner_kind: 'series', owner_id: 's', request_root_series_id: 's' },
    { request_root_kind: 'series', owner_kind: 'series' },
    { members: [...wire.admission.request.members, { ...wire.admission.request.members[0], variant_id: 'second', attempt_id: 'second' }] },
    { channel: 'full_body' }, { entrypoint: 'studio.asset.scene_prop.v1' },
])('rejects invalid frame target relations %j', changes => {
    expect(() => validateStudioAssetImageReceipt({ ...wire.admission, request: { ...wire.admission.request, ...changes } })).toThrow();
});
it.each([undefined, null, {}])('retains exact composition raw presence %j and author revision before POST', async composition => {
    const raw = { ...input(), ...(composition !== undefined ? { composition_data: composition } : {}) } as Record<string, unknown>;
    if (composition === undefined) delete raw.composition_data;
    net.mockImplementation(async (_url, init) => {
        expect(listStudioAssetImageRequests(owner())[0].request).toEqual({ ...raw, client_request_id: 'frame-request' });
        expect(JSON.parse(init.body)).toEqual({ ...raw, client_request_id: 'frame-request' });
        return new Response(JSON.stringify(wire.admission));
    });
    const sent = await submitStudioAssetImageRequest({ ...owner(), input: raw as StudioAssetImageRequestInput, idFactory: () => 'frame-request' });
    expect(sent.status).toBe('confirmed'); expect(net).toHaveBeenCalledTimes(1);
    net.mockResolvedValue(new Response(JSON.stringify(wire.readback)));
    expect((await readStudioAssetImageRequestReceipt({ ...owner(), journal: sent.journal })).status).toBe('confirmed');
    expect(net.mock.calls[1][1].method).toBe('GET');
    expect((await submitStudioAssetImageRequest({ ...owner(), input: raw as StudioAssetImageRequestInput, idFactory: () => 'frame-request' })).status).toBe('unknown');
    expect(net).toHaveBeenCalledTimes(2);
});
it.each([{ expected_revision: undefined }, { expected_revision: -1 }, { expected_revision: 0.5 }, { expected_revision: true },
    { prompt: null }, { prompt: undefined }, { batch_size: 2 }, { composition_data: [] }, { composition_data: { reference_image_urls: [] } },
    ...['style_preset', 'style_prompt', 'apply_style', 'reference_image_url', 'negative_prompt', 'model_name', 'aspect_ratio', 'seed', 'watermark'].map(name => ({ [name]: null }))])('rejects non-frame inputs before any journal %j', changes => {
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: { ...input(), ...changes } as never, idFactory: () => 'frame-request' })).toThrow();
    expect(localStorage.length).toBe(0); expect(net).not.toHaveBeenCalled();
});
it('requires project root and preserves old asset omissions without frame-only fields', () => {
    expect(() => beginStudioAssetImageRequest({ ...owner(), rootKind: 'series', input: input(), idFactory: () => 'frame-request' })).toThrow();
    const old = { asset_id: 'c', prompt: '', apply_style: false, seed: 0 };
    const stored = beginStudioAssetImageRequest({ ...owner(), input: old, idFactory: () => 'old' }).journal;
    expect(stored.request).toEqual({ ...old, client_request_id: 'old' });
    for (const extra of [{ expected_revision: 0 }, { composition_data: {} }]) {
        expect(() => beginStudioAssetImageRequest({ ...owner(), input: { ...old, ...extra } as never, idFactory: () => 'bad' })).toThrow();
    }
});
