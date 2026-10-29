// @vitest-environment happy-dom
// Request/journal-only tests; no provider result fixtures.
import { afterEach, beforeEach, expect, it } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p', storage: localStorage });
const input = (channel: 'three_view' | 'headshot', extra: Record<string, unknown> = {}) => ({
    asset_id: 'c', asset_type: 'character', generation_type: channel, model_name: 'gpt-image-2',
    reference_source: 'full_body_image_url', reference_image_url: 'uploads/legacy-a.png',
    prompt: 'Original fallback author draft', ...extra,
}) as StudioAssetImageRequestInput;
beforeEach(() => { localStorage.clear(); clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' }); });
afterEach(() => { clearRuntimeEnvironment(); });
it.each(['three_view', 'headshot'] as const)('%s preserves absent-vs-null original variant identity and never manufactures an ID', channel => {
    const first = beginStudioAssetImageRequest({ ...owner(), input: input(channel), idFactory: () => 'original-key' });
    expect(first.journal.request).not.toHaveProperty('reference_variant_id');
    expect(listStudioAssetImageRequests(owner())[0].request).toEqual(first.journal.request);
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: input(channel, { reference_variant_id: null }), idFactory: () => 'original-key' })).toThrow('conflict');
    expect(beginStudioAssetImageRequest({ ...owner(), input: input(channel), idFactory: () => 'original-key' }).status).toBe('pending');
    expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
});
it.each(['three_view_upload', 'headshot_upload'] as const)('%s requires a real uploaded variant ID', reference_source => {
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: input('headshot', { reference_source }), idFactory: () => 'missing-id' })).toThrow('request_invalid');
    const request = beginStudioAssetImageRequest({ ...owner(), input: input('headshot', { reference_source, reference_variant_id: 'first-upload' }), idFactory: () => 'uploaded' });
    expect(request.journal.request.reference_variant_id).toBe('first-upload');
});
it.each([
    { reference_source: 'selected_full_body' }, { reference_source: null },
    { reference_variant_id: 'invented-old-url-id' }, { generation_type: 'full_body' },
    { asset_type: 'scene', generation_type: 'all' },
])('rejects unsupported fallback shape %j before journaling', extra => {
    expect(() => beginStudioAssetImageRequest({ ...owner(), input: input('headshot', extra), idFactory: () => 'bad-shape' })).toThrow();
    expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
});
