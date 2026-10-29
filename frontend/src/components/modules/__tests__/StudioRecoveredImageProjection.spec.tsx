// Contract regression at the real hook/journal/project-store boundary. Only
// transport responses are synthetic; this is not a backend HTTP capture.
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import { useStudioAssetImageRequests } from '@/lib/useStudioAssetImageRequests';
import { useProjectStore } from '@/store/projectStore';
import { ack, deferred, enriched, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));

type Mode = 'reference_sheet' | 'full_body' | 'full_body_edit' | 'scene' | 'prop' | 'headshot';
type Selection = 'old' | null;
type Variant = { id: string; url: string; created_at: number; prompt_used: string; media_refs: { url: string | null } };
type Legacy = { selected_id: string | null; variants: Variant[] };
type Modern = { selected_image_id: string | null; image_variants: Variant[]; image_prompt: string; video_prompt: string };
type Target = {
    id: string; name: string; description: string; source: 'episode'; status: string;
    image_prompt: string; headshot_prompt: string; full_body_prompt: string;
    image_url: string | null; full_body_image_url: string | null; headshot_image_url: string | null; avatar_url: string | null;
    media_refs: Record<string, string | null>;
    reference_sheet_request_id: string; full_body_request_id: string; headshot_request_id: string; image_request_id: string;
    reference_sheet: Modern; full_body_asset: Legacy; full_body: Modern;
    headshot_asset: Legacy; head_shot: Modern; image_asset: Legacy;
};
const MODES: Mode[] = ['reference_sheet', 'full_body', 'full_body_edit', 'scene', 'prop', 'headshot'];
const REQUEST = '22222222-2222-4222-8222-222222222222';
const KEY = 'original-recovered-request';
const OLD = 'uploads/original-choice.png';
const AVATAR = 'uploads/independent-avatar.png';
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p' });
const channel = (mode: Mode) => mode === 'scene' || mode === 'prop' ? 'image' as const : mode === 'full_body_edit' ? 'full_body' as const : mode;
const kind = (mode: Mode) => mode === 'scene' || mode === 'prop' ? mode : 'character' as const;
const alias = (mode: Mode) => channel(mode) === 'full_body' ? 'full_body_image_url' : mode === 'headshot' ? 'headshot_image_url' : 'image_url';
const response = (value: unknown) => new Response(JSON.stringify(value));
function input(mode: Mode, count: number): StudioAssetImageRequestInput {
    const common = { asset_id: 'asset', batch_size: count, model_name: 'gpt-image-2', prompt: 'Original request prompt' };
    if (mode === 'scene' || mode === 'prop') return { ...common, asset_type: mode, generation_type: 'all' };
    if (mode === 'full_body_edit' || mode === 'headshot') return { ...common, asset_type: 'character',
        generation_type: mode === 'headshot' ? 'headshot' : 'full_body', reference_variant_id: 'reference', reference_image_url: 'uploads/reference.png' };
    return { ...common, asset_type: 'character', generation_type: mode };
}
function wire(mode: Mode, statuses: string[]) {
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: true, reason: null as string | null,
        request: { id: REQUEST, entrypoint: kind(mode) === 'character' ? `studio.asset.${channel(mode)}.v1` : 'studio.asset.scene_prop.v1',
            request_contract_version: 'request-json-v1', client_request_id: KEY, request_fingerprint: 'a'.repeat(64),
            request_root_kind: 'project', request_root_id: 'p', request_root_scope_id: '33333333-3333-4333-8333-333333333333',
            request_root_series_id: null, owner_kind: 'script', owner_id: 'p', asset_type: kind(mode), asset_id: 'asset', channel: channel(mode),
            members: statuses.map((status, index) => ({ variant_id: `recovered-${index}`, attempt_id: `recovered-${index}`, status,
                reason: null as string | null, ...(status === 'completed' ? { output_ref: `assets/studio/recovered-${index}.png` } : {}) })) } };
}
function variant(id: string, raw: string): Variant {
    return { id, url: `https://signed.example.invalid/${id}.png?display=only`, media_refs: { url: raw }, created_at: 7, prompt_used: 'Saved prompt' };
}
function target(mode: Mode, selection: Selection, completed: number): Target {
    const variants = [variant('old', OLD), ...Array.from({ length: completed }, (_, index) => variant(`recovered-${index}`, `assets/studio/recovered-${index}.png`))];
    const legacy = (): Legacy => ({ selected_id: selection, variants: structuredClone(variants) });
    const modern = (): Modern => ({ selected_image_id: selection, image_variants: structuredClone(variants), image_prompt: 'Current image draft', video_prompt: 'Current video draft' });
    const selectedUrl = selection ? OLD : null;
    const media = { image_url: selectedUrl, full_body_image_url: selectedUrl, headshot_image_url: selectedUrl,
        avatar_url: mode === 'headshot' && selection ? OLD : AVATAR };
    return { id: 'asset', name: 'Author asset', description: 'Keep author description', source: 'episode', status: completed ? 'completed' : 'needs_confirmation',
        image_prompt: 'Current image draft', headshot_prompt: 'Current headshot draft', full_body_prompt: 'Current full-body draft',
        ...media, media_refs: media, reference_sheet_request_id: REQUEST, full_body_request_id: REQUEST, headshot_request_id: REQUEST, image_request_id: REQUEST,
        reference_sheet: modern(), full_body_asset: legacy(), full_body: modern(), headshot_asset: legacy(), head_shot: modern(), image_asset: legacy() };
}
function snapshot(mode: Mode, selection: Selection, completed: number, revision = 2) {
    const asset = target(mode, selection, completed);
    return enriched({ ...ack([], 'p', revision), series_id: null,
        characters: kind(mode) === 'character' ? [asset] : [], scenes: mode === 'scene' ? [asset] : [], props: mode === 'prop' ? [asset] : [] });
}
type Snapshot = ReturnType<typeof snapshot>;
function asset(value: Snapshot, mode: Mode): Target {
    return (mode === 'scene' ? value.scenes : mode === 'prop' ? value.props : value.characters)[0];
}
function histories(value: Target, mode: Mode): Variant[][] {
    if (mode === 'reference_sheet') return [value.reference_sheet.image_variants];
    if (mode === 'headshot') return [value.headshot_asset.variants, value.head_shot.image_variants];
    if (channel(mode) === 'full_body') return [value.full_body_asset.variants, value.full_body.image_variants];
    return [value.image_asset.variants];
}
function selected(value: Target, mode: Mode) {
    return mode === 'reference_sheet' ? value.reference_sheet.selected_image_id : mode === 'headshot' ? value.headshot_asset.selected_id
        : channel(mode) === 'full_body' ? value.full_body_asset.selected_id : value.image_asset.selected_id;
}
function current(mode: Mode): Target {
    const value = useProjectStore.getState().currentProject!;
    return (mode === 'scene' ? value.scenes : mode === 'prop' ? value.props : value.characters)[0] as unknown as Target;
}
function setup(mode: Mode, selection: Selection, statuses: string[]) {
    const initial = snapshot(mode, selection, 0, 1);
    useProjectStore.getState().applyProjectReadSnapshot('p', initial);
    beginStudioAssetImageRequest({ ...owner(), input: input(mode, statuses.length), idFactory: () => KEY });
    const receipt = wire(mode, statuses), fresh = snapshot(mode, selection, statuses.filter(s => s === 'completed').length);
    asset(fresh, mode).status = statuses.every(status => status === 'completed') ? 'completed' : 'needs_confirmation';
    vi.mocked(appAccessFetch).mockImplementation(async () => response(receipt));
    vi.mocked(axios.get).mockImplementation(async () => ({ data: structuredClone(fresh) }));
    return { initial, fresh, receipt };
}
function hookOptions(mode: Mode) {
    return { kind: kind(mode), channel: channel(mode), open: true, projectId: 'p', assetId: 'asset', source: 'episode' };
}
function noWrites() {
    expect(vi.mocked(appAccessFetch).mock.calls.map(([, init]) => init?.method)).toEqual(['GET']);
    expect(axios.post).not.toHaveBeenCalled();
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
    useProjectStore.setState({ currentProject: project('p', 0), projects: [project('p', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
});
afterEach(() => { cleanup(); clearRuntimeEnvironment(); });

describe('recovered image projection acknowledges history without choosing for the author', () => {
    const completeCases = MODES.flatMap(mode => ([null, 'old'] as const).flatMap(selection => [1, 2].map(count => ({ mode, selection, count }))));
    it.each(completeCases)('$mode keeps selection=$selection for $count completed members', async ({ mode, selection, count }) => {
        const { fresh } = setup(mode, selection, Array(count).fill('completed'));
        const hook = renderHook(() => useStudioAssetImageRequests(hookOptions(mode)));
        await waitFor(() => expect(hook.result.current.results).toHaveLength(1));
        await waitFor(() => expect(hook.result.current.reading).toBe(false));
        expect(hook.result.current.readbackFailed).toBe(false);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
        const saved = current(mode);
        expect(selected(saved, mode)).toBe(selection);
        expect(histories(saved, mode)).toEqual(histories(asset(fresh, mode), mode));
        expect(saved).toMatchObject({ description: 'Keep author description', image_prompt: 'Current image draft',
            headshot_prompt: 'Current headshot draft', full_body_prompt: 'Current full-body draft', avatar_url: asset(fresh, mode).avatar_url });
        expect(saved.media_refs).toEqual(asset(fresh, mode).media_refs);
        expect(hook.result.current.busy).toBe(false); noWrites();
    });

    it.each(MODES.flatMap(mode => ([null, 'old'] as const).map(selection => ({ mode, selection }))))(
        '$mode reads partial history with selection=$selection but retains the original journal', async ({ mode, selection }) => {
            setup(mode, selection, ['completed', 'needs_confirmation']);
            const hook = renderHook(() => useStudioAssetImageRequests(hookOptions(mode)));
            await waitFor(() => expect(hook.result.current.results).toHaveLength(1));
            await waitFor(() => expect(hook.result.current.reading).toBe(false));
            expect(hook.result.current.readbackFailed).toBe(false);
            expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
            expect(listStudioAssetImageRequests(owner())[0].request).toEqual({ ...input(mode, 2), client_request_id: KEY });
            expect(selected(current(mode), mode)).toBe(selection);
            expect(current(mode).status).toBe('needs_confirmation');
            expect(histories(current(mode), mode).every(values => values.some(v => v.id === 'recovered-0'))).toBe(true);
            expect(hook.result.current.unknown).toBe(true); noWrites();
        });

    it('keeps an independent avatar when recovered headshot history has no selection', async () => {
        setup('headshot', null, ['completed']);
        const hook = renderHook(() => useStudioAssetImageRequests(hookOptions('headshot')));
        await waitFor(() => expect(hook.result.current.results).toHaveLength(1));
        await waitFor(() => expect(hook.result.current.reading).toBe(false));
        expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
        expect(current('headshot')).toMatchObject({ avatar_url: AVATAR, headshot_image_url: null,
            headshot_asset: { selected_id: null }, head_shot: { selected_image_id: null },
            media_refs: { avatar_url: AVATAR, headshot_image_url: null } });
        noWrites();
    });
    const independentAvatarCases = [
        { label: 'single completion', statuses: ['completed'] },
        { label: 'batch completion', statuses: ['completed', 'completed'] },
        { label: 'partial recovery', statuses: ['completed', 'needs_confirmation'] },
    ].flatMap(item => (['raw', 'display-sidecar'] as const).map(representation => ({ ...item, representation })));
    it.each(independentAvatarCases)('$label keeps the old headshot selection and independent $representation avatar', async ({ statuses, representation }) => {
        const { fresh, receipt } = setup('headshot', 'old', statuses);
        const original = listStudioAssetImageRequests(owner())[0];
        const originalReceipt = structuredClone(receipt), value = asset(fresh, 'headshot');
        const display = representation === 'raw' ? AVATAR : 'https://signed.example.invalid/avatar.png?display=independent';
        // Independent update_asset_image changes image_url/avatar_url, never the
        // selected headshot channel. This is a public projection variation only.
        value.avatar_url = value.image_url = display;
        value.media_refs.avatar_url = value.media_refs.image_url = AVATAR;
        const expected = structuredClone(value);
        const hook = renderHook(() => useStudioAssetImageRequests(hookOptions('headshot')));
        await waitFor(() => expect(hook.result.current.results).toHaveLength(1));
        await waitFor(() => expect(hook.result.current.reading).toBe(false));
        expect(hook.result.current.readbackFailed).toBe(false);
        const saved = current('headshot'), complete = statuses.every(status => status === 'completed');
        expect(saved).toMatchObject(expected);
        expect(histories(saved, 'headshot')).toEqual(histories(expected, 'headshot'));
        expect(saved.media_refs).toEqual(expected.media_refs);
        expect(saved).toMatchObject({ avatar_url: display, image_url: display, headshot_image_url: OLD,
            headshot_asset: { selected_id: 'old' }, head_shot: { selected_image_id: 'old' },
            status: complete ? 'completed' : 'needs_confirmation' });
        expect(receipt).toEqual(originalReceipt);
        expect(hook.result.current.results[0].receipt?.request).toEqual(originalReceipt.request);
        if (complete) expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
        else {
            expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
            expect(listStudioAssetImageRequests(owner())[0].request).toEqual(original.request);
            expect(hook.result.current.unknown).toBe(true);
        }
        expect(hook.result.current.busy).toBe(false);
        expect(axios.get).toHaveBeenCalledOnce(); noWrites();
    });

});

describe('recovery retains strict projection and freshness proof', () => {
    const invalid = ['pointer', 'duplicate target', 'source', 'missing member', 'duplicate member', 'raw URL',
        'dangling selection', 'empty selection', 'selected alias', 'null alias', 'owner', 'receipt reason', 'member reason'] as const;
    it.each(invalid)('rejects %s before adopting scene history or resolving its journal', async fault => {
        const { fresh, receipt } = setup('scene', fault === 'null alias' ? null : 'old', ['completed']);
        const value = asset(fresh, 'scene'), variants = value.image_asset.variants;
        if (fault === 'pointer') value.image_request_id = '44444444-4444-4444-8444-444444444444';
        else if (fault === 'duplicate target') fresh.scenes.push(structuredClone(value));
        else if (fault === 'source') Object.assign(value, { source: 'global' });
        else if (fault === 'missing member') variants.splice(1, 1);
        else if (fault === 'duplicate member') variants.push(structuredClone(variants[1]));
        else if (fault === 'raw URL') variants[1].media_refs.url = 'assets/studio/wrong.png';
        else if (fault === 'dangling selection') value.image_asset.selected_id = 'missing';
        else if (fault === 'empty selection') value.image_asset.selected_id = '';
        else if (fault === 'selected alias' || fault === 'null alias') value.media_refs.image_url = 'uploads/wrong-alias.png';
        else if (fault === 'owner') Object.assign(receipt.request, { owner_kind: 'global', owner_id: 'library' });
        else if (fault === 'receipt reason') receipt.reason = 'studio_image_request_inputs_changed';
        else receipt.request.members[0].reason = 'studio_image_projection_pending';
        const hook = renderHook(() => useStudioAssetImageRequests(hookOptions('scene')));
        await waitFor(() => expect(hook.result.current.readbackFailed).toBe(true));
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(histories(current('scene'), 'scene')[0].map(v => v.id)).toEqual(['old']); noWrites();
    });

    it.each((['full_body', 'full_body_edit', 'headshot'] as const).flatMap(mode =>
        (['modern selection', 'modern member', 'duplicate selected', 'null alias'] as const).map(fault => ({ mode, fault }))))(
        '$mode rejects $fault disagreement across modern/legacy histories', async ({ mode, fault }) => {
            const { fresh } = setup(mode, fault === 'null alias' ? null : 'old', ['completed']);
            const value = asset(fresh, mode), modern = mode === 'headshot' ? value.head_shot : value.full_body;
            if (fault === 'modern selection') modern.selected_image_id = null;
            else if (fault === 'modern member') modern.image_variants.splice(1, 1);
            else if (fault === 'duplicate selected') modern.image_variants.push(structuredClone(modern.image_variants[0]));
            else value.media_refs[alias(mode)] = OLD;
            const hook = renderHook(() => useStudioAssetImageRequests(hookOptions(mode)));
            await waitFor(() => expect(hook.result.current.readbackFailed).toBe(true));
            expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
            expect(histories(current(mode), mode).every(values => values.length === 1)).toBe(true); noWrites();
        });

    it('rejects a selected headshot whose own channel raw alias disagrees', async () => {
        const { fresh } = setup('headshot', 'old', ['completed']);
        const original = listStudioAssetImageRequests(owner())[0];
        asset(fresh, 'headshot').media_refs.headshot_image_url = AVATAR;
        const hook = renderHook(() => useStudioAssetImageRequests(hookOptions('headshot')));
        await waitFor(() => expect(hook.result.current.readbackFailed).toBe(true));
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(listStudioAssetImageRequests(owner())[0].request).toEqual(original.request);
        expect(histories(current('headshot'), 'headshot').every(values => values.map(v => v.id).join() === 'old')).toBe(true);
        expect(current('headshot')).toMatchObject({ headshot_image_url: OLD, avatar_url: OLD,
            headshot_asset: { selected_id: 'old' }, head_shot: { selected_image_id: 'old' } });
        noWrites();
    });

    it('retains the exact journal identity when a later GET changes the request receipt', async () => {
        const { receipt } = setup('scene', 'old', ['needs_confirmation']);
        const hook = renderHook(() => useStudioAssetImageRequests(hookOptions('scene')));
        await waitFor(() => expect(hook.result.current.results).toHaveLength(1));
        await waitFor(() => expect(hook.result.current.reading).toBe(false));
        const original = listStudioAssetImageRequests(owner())[0];
        expect(original.receiptIdentity).not.toBeNull();
        receipt.request.id = '44444444-4444-4444-8444-444444444444';
        await act(async () => { await hook.result.current.refresh(); });
        expect(hook.result.current.results[0].status).toBe('unknown');
        expect(listStudioAssetImageRequests(owner())).toEqual([original]);
        expect(histories(current('scene'), 'scene')[0].map(v => v.id)).toEqual(['old']);
        expect(axios.get).not.toHaveBeenCalled(); expect(axios.post).not.toHaveBeenCalled();
        expect(vi.mocked(appAccessFetch).mock.calls.map(([, init]) => init?.method)).toEqual(['GET', 'GET']);
    });

    it.each(['full_body_edit', 'headshot'] as const)('keeps %s author ABA invalidation while readback is in flight', async mode => {
        const { fresh } = setup(mode, null, ['completed']);
        const read = deferred<{ data: Snapshot }>(); let epoch = 0;
        vi.mocked(axios.get).mockReturnValue(read.promise);
        const hook = renderHook(() => useStudioAssetImageRequests({ ...hookOptions(mode), isDerivedImageReadbackCurrent: () => epoch === 0 }));
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        // A draft can return to its original text without restoring its author epoch.
        epoch += 2;
        await act(async () => { read.resolve({ data: fresh }); });
        await waitFor(() => expect(hook.result.current.readbackFailed).toBe(true));
        expect(histories(current(mode), mode).every(values => values.length === 1)).toBe(true);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); noWrites();
    });

    it('does not adopt a delayed recovered project after its owning panel closes', async () => {
        const { fresh } = setup('scene', null, ['completed']);
        const read = deferred<{ data: Snapshot }>(); vi.mocked(axios.get).mockReturnValue(read.promise);
        const hook = renderHook(({ open }) => useStudioAssetImageRequests({ ...hookOptions('scene'), open }), { initialProps: { open: true } });
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        hook.rerender({ open: false });
        await act(async () => { read.resolve({ data: fresh }); });
        expect(histories(current('scene'), 'scene')[0].map(v => v.id)).toEqual(['old']);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); noWrites();
    });

    it('rechecks a newer store request pointer after a delayed otherwise valid recovered snapshot', async () => {
        const { fresh } = setup('scene', null, ['completed']);
        const read = deferred<{ data: Snapshot }>(); vi.mocked(axios.get).mockReturnValue(read.promise);
        const hook = renderHook(() => useStudioAssetImageRequests(hookOptions('scene')));
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        const newer = snapshot('scene', null, 0, 3);
        asset(newer, 'scene').image_request_id = '44444444-4444-4444-8444-444444444444';
        act(() => { useProjectStore.getState().applyProjectReadSnapshot('p', newer); });
        await act(async () => { read.resolve({ data: fresh }); });
        await waitFor(() => expect(hook.result.current.readbackFailed).toBe(true));
        expect(current('scene').image_request_id).toBe(asset(newer, 'scene').image_request_id);
        expect(histories(current('scene'), 'scene')[0].map(v => v.id)).toEqual(['old']);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); noWrites();
    });
});
