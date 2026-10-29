import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest } from '@/lib/studioAssetImageRequest';
import { isStudioFullBodyEligible, usesStudioImageCore, useStudioAssetImageRequests } from '@/lib/useStudioAssetImageRequests';
import { deferred } from './frame-command-fixtures';

vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p' });
const input = { asset_id: 'c', asset_type: 'character' as const, generation_type: 'full_body' as const };
const options = { kind: 'character' as const, open: true, projectId: 'p', assetId: 'c', source: 'episode' };
function response(channel: 'full_body' | 'reference_sheet', key: string) {
    return new Response(JSON.stringify({ schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false,
        request: { id: '11111111-1111-4111-8111-111111111111', entrypoint: `studio.asset.${channel}.v1`,
            request_contract_version: 'request-json-v1', client_request_id: key, request_fingerprint: 'a'.repeat(64),
            request_root_kind: 'project', request_root_id: 'p', request_root_scope_id: '22222222-2222-4222-8222-222222222222',
            request_root_series_id: null, owner_kind: 'script', owner_id: 'p', asset_type: 'character', asset_id: 'c', channel,
            members: [{ variant_id: 'attempt', attempt_id: 'attempt', status: 'needs_confirmation' }] } }));
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: '33333333-3333-4333-8333-333333333333' });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
});
afterEach(() => clearRuntimeEnvironment());

describe('full-body channel isolation', () => {
    it.each(['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'])(
        'routes only the supported character channels for %s', model => {
            expect(usesStudioImageCore('character', model, 'full_body')).toBe(true);
            expect(usesStudioImageCore('character', model, 'reference_sheet')).toBe(true);
            expect(usesStudioImageCore('character', model, 'three_view')).toBe(true);
            expect(usesStudioImageCore('scene', model, 'full_body')).toBe(false);
        });
    it('limits full-body eligibility to assets without costume-base or uploaded non-full-body references', () => {
        expect(isStudioFullBodyEligible({})).toBe(true);
        expect(isStudioFullBodyEligible({ full_body_asset: { variants: [{ is_uploaded_source: true }] } })).toBe(true);
        expect(isStudioFullBodyEligible({ base_character_id: 'base' })).toBe(false);
        expect(isStudioFullBodyEligible({ three_view_asset: { variants: [{ is_uploaded_source: true }] } })).toBe(false);
        expect(isStudioFullBodyEligible({ headshot_asset: { variants: [{ is_uploaded_source: true }] } })).toBe(false);
        expect(isStudioFullBodyEligible({ three_view_asset: { variants: [{ is_uploaded_source: false }] } })).toBe(true);
    });
    it('reads only the matching channel and treats an omitted old journal as reference sheet', async () => {
        beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'c' }, idFactory: () => 'reference' });
        beginStudioAssetImageRequest({ ...owner(), input, idFactory: () => 'full' });
        vi.mocked(appAccessFetch).mockImplementation(async url => String(url).endsWith('/full') ? response('full_body', 'full') : response('reference_sheet', 'reference'));
        const hook = renderHook(({ channel }: { channel?: 'full_body' }) => useStudioAssetImageRequests({ ...options, channel }), { initialProps: { channel: 'full_body' } as { channel?: 'full_body' } });
        await waitFor(() => expect(hook.result.current.results).toHaveLength(1));
        expect(hook.result.current.results[0].journal.request.client_request_id).toBe('full');
        expect(appAccessFetch).toHaveBeenCalledOnce();
        hook.rerender({ channel: undefined });
        await waitFor(() => expect(hook.result.current.results[0]?.journal.request.client_request_id).toBe('reference'));
        expect(vi.mocked(appAccessFetch).mock.calls.map(([, init]) => init?.method)).toEqual(['GET', 'GET']);
    });
    it('invalidates an old channel lease before a delayed full-body receipt can enter the reference-sheet view', async () => {
        beginStudioAssetImageRequest({ ...owner(), input, idFactory: () => 'full' });
        const old = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(old.promise);
        const hook = renderHook(({ channel }: { channel?: 'full_body' }) => useStudioAssetImageRequests({ ...options, channel }), { initialProps: { channel: 'full_body' } as { channel?: 'full_body' } });
        await waitFor(() => expect(appAccessFetch).toHaveBeenCalledOnce());
        hook.rerender({ channel: undefined });
        await act(async () => { old.resolve(response('full_body', 'full')); });
        expect(hook.result.current.results).toEqual([]); expect(hook.result.current.unknown).toBe(false);
        expect(appAccessFetch).toHaveBeenCalledOnce();
    });
    it('refuses a cross-channel submission without creating a journal or posting', async () => {
        const hook = renderHook(() => useStudioAssetImageRequests({ ...options, channel: 'full_body' }));
        await act(async () => { await hook.result.current.submit({ asset_id: 'c' }); });
        expect(hook.result.current.submitFailed).toBe(true); expect(localStorage.length).toBe(0); expect(appAccessFetch).not.toHaveBeenCalled();
    });
    it('invalidates a queued browser lock callback when the same asset changes character channel', async () => {
        const lock = deferred<void>(); let entered = false;
        Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => {
            entered = true; await lock.promise; return callback({});
        } } });
        const hook = renderHook(({ channel }: { channel?: 'full_body' }) => useStudioAssetImageRequests({ ...options, channel }), { initialProps: { channel: 'full_body' } as { channel?: 'full_body' } });
        let submitted!: Promise<void>;
        await act(async () => { submitted = hook.result.current.submit(input); });
        expect(entered).toBe(true); hook.rerender({ channel: undefined });
        await act(async () => { lock.resolve(); await submitted; });
        expect(appAccessFetch).not.toHaveBeenCalled(); expect(localStorage.length).toBe(0); expect(hook.result.current.results).toEqual([]);
    });
});
