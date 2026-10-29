import { act, fireEvent, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from '../series/__tests__/test-utils';
import { LightboxProvider } from '../shared/preview/LightboxProvider';
import { api } from '@/lib/api';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests } from '@/lib/studioAssetImageRequest';
import { useStudioAssetImageRequests } from '@/lib/useStudioAssetImageRequests';
import { projectReadSnapshot } from '@/lib/frameCommandSnapshot';
import { clearLibraryRecoveries, libraryOperationKey, rememberLibraryRecovery } from '@/lib/libraryReadback';
import { useProjectStore } from '@/store/projectStore';
import AssetLibraryPage from './AssetLibraryPage';
import AssetInspector from './AssetInspector';

vi.mock('@/lib/api', () => ({ API_URL: 'http://test.invalid', api: {
    listSeries: vi.fn(), getProjects: vi.fn(), listLibraryAssets: vi.fn(), getProject: vi.fn(), generateAsset: vi.fn(), getTaskStatus: vi.fn(),
    createLibraryAsset: vi.fn(), promoteAssetToLibrary: vi.fn(), toggleSeriesAssetStarred: vi.fn(), toggleAssetStarred: vi.fn(), updateLibraryAsset: vi.fn(),
} }));
vi.mock('@/lib/appAccess', async original => ({ ...await original<typeof import('@/lib/appAccess')>(), appAccessFetch: vi.fn() }));
vi.mock('@/store/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn(), progress: vi.fn(() => 'toast'), update: vi.fn() } }));
const SID = '11111111-1111-4111-8111-111111111111';
const kinds = ['scene', 'prop'] as const;
type Kind = typeof kinds[number];
const models = ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'];
const owner = (rootId = 'p') => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId });
function deferred<T>() { let resolve!: (v: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function raw({ id = 'p', revision = 4, library = 1, model = 'wan2.7-image', negative = '', fresh = false, source = 'episode' } = {}) {
    const asset = (kind: Kind) => ({ id: 'shared', name: `${kind} ${id}`, description: 'Saved description', source,
        image_url: '/old.png', status: 'completed', video_prompt: 'keep video', video_assets: [{ id: 'motion', video_url: '/keep.mp4', status: 'completed' }],
        image_asset: { selected_id: 'old', variants: [...(fresh ? [{ id: 'new-2', url: '/new-2.png' }, { id: 'new-1', url: '/new-1.png' }] : []),
            { id: 'old', url: '/old.png', is_favorited: true }, { id: 'other', url: '/other.png' }] } });
    return { id, revision, title: `Project ${id}`, original_text: 'script', series_id: null, status: 'draft', created_at: 1, updated_at: 2,
        read_versions: { project: revision, library, series: null }, model_settings: { t2i_model: model },
        art_direction: { style_config: { positive_prompt: 'source light', negative_prompt: negative } },
        characters: [], scenes: [asset('scene')], props: [asset('prop')], frames: [], video_tasks: [] };
}
const parsed = (value: ReturnType<typeof raw>) => projectReadSnapshot(value.id, value, requireActiveRuntimeEnvironment().key);
function install(options: Parameters<typeof raw>[0] = {}) { const value = raw(options); vi.mocked(api.getProjects).mockResolvedValue([parsed(value)]); vi.mocked(api.getProject).mockResolvedValue(value); return value; }
function wire(kind: Kind, key: string, statuses = ['needs_confirmation', 'needs_confirmation', 'needs_confirmation'], root = 'p') {
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: true, reason: null, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: 'studio.asset.scene_prop.v1', request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: root,
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: null,
        owner_kind: 'script', owner_id: root, asset_type: kind, asset_id: 'shared', channel: 'image',
        members: statuses.map((status, i) => ({ variant_id: `${key}-${i}`, attempt_id: `${key}-${i}`, status, reason: null,
            ...(status === 'completed' ? { output_ref: `assets/studio/${key}-${i}.png` } : {}) })),
    } };
}
// Positive completed fixtures carry the same durable member IDs and raw paths as their projection.
function projectedWire(kind: Kind, key: string, statuses = ['completed', 'completed']) {
    const receipt = wire(kind, key, statuses);
    receipt.request.members = receipt.request.members.map((member, i) => ({ ...member,
        variant_id: `new-${i + 1}`, attempt_id: `new-${i + 1}`,
        ...(member.status === 'completed' ? { output_ref: `assets/studio/new-${i + 1}.png` } : {}),
    }));
    return receipt;
}
function projectedRaw(kind: Kind, options: Parameters<typeof raw>[0] = {}) {
    const value = raw({ ...options, fresh: true });
    const asset = value[kind === 'scene' ? 'scenes' : 'props'][0];
    Object.assign(asset, { image_request_id: projectedWire(kind, kind).request.id });
    for (const variant of asset.image_asset.variants) {
        if (variant.id === 'new-1' || variant.id === 'new-2') variant.url = `assets/studio/${variant.id}.png`;
    }
    return value;
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const seed = (kind: Kind, key: string = kind) => beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'shared', asset_type: kind, generation_type: 'all', batch_size: 2 }, idFactory: () => key });
const element = () => <LightboxProvider><AssetLibraryPage /></LightboxProvider>;
async function page(kind: Kind = 'scene') { const view = renderWithIntl(element()); fireEvent.click(await screen.findByText(`${kind} p`)); return view; }
function inspector(kind: Kind, value = raw(), sourceKind: 'project' | 'series' | 'global' = 'project') {
    const asset = parsed(value)[kind === 'scene' ? 'scenes' : 'props'][0];
    return <LightboxProvider><AssetInspector asset={asset} type={kind === 'scene' ? 'scenes' : 'props'} sourceName="Source" sourceId={value.id} sourceKind={sourceKind} starred={false} onClose={() => {}} onToggleStar={() => {}} /></LightboxProvider>;
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearLibraryRecoveries(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ projects: [], currentProject: parsed(raw({ id: 'b', model: 'wan2.6-t2i' })), readWatermarks: {}, runningOps: {}, currentSeries: null, generatingTasks: [] });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(api.listSeries).mockResolvedValue([]); vi.mocked(api.listLibraryAssets).mockResolvedValue({ revision: 1, characters: [], scenes: [], props: [] }); install();
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => { const input = init?.method === 'POST' ? JSON.parse(String(init.body)) : null;
        const key = input?.client_request_id ?? String(url).split('/').at(-1)!; return response(wire(input?.asset_type ?? key, key)); });
});
afterEach(() => { vi.useRealTimers(); clearLibraryRecoveries(); clearRuntimeEnvironment(); });

describe('Library project-origin scene/prop Core image controls', () => {
    it.each(kinds.flatMap(kind => models.map(model => [kind, model] as const)))('uses actual uncached %s source model %s', async (kind, model) => {
        install({ model }); const current = useProjectStore.getState().currentProject; await page(kind);
        fireEvent.click(screen.getByRole('button', { name: '生成更多变体' })); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ asset_type: kind, asset_id: 'shared', generation_type: 'all', batch_size: 3, model_name: model });
        expect(api.generateAsset).not.toHaveBeenCalled(); expect(useProjectStore.getState().currentProject).toBe(current); expect(useProjectStore.getState().projects).toEqual([]);
        expect(await screen.findAllByText('待确认')).toHaveLength(3);
    });
    it('characterizes why the default hook cannot confirm an uncached Library root', async () => {
        seed('scene'); vi.mocked(appAccessFetch).mockResolvedValue(response(wire('scene', 'scene', ['completed', 'completed'])));
        const { result } = renderHook(() => useStudioAssetImageRequests({ kind: 'scene', open: true, projectId: 'p', assetId: 'shared', source: 'episode', seriesId: null }));
        await waitFor(() => expect(result.current.readbackFailed).toBe(true)); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(useProjectStore.getState().projects).toEqual([]); expect(useProjectStore.getState().currentProject?.id).toBe('b');
    });
    it.each(kinds)('confirms full %s prepend snapshot without changing preview or current Studio project', async kind => {
        seed(kind); const done = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(done.promise);
        const current = useProjectStore.getState().currentProject; await page(kind);
        const other = screen.getAllByAltText('变体').find(image => image.getAttribute('src')?.endsWith('/other.png'))!; fireEvent.click(other.closest('button')!);
        vi.mocked(api.getProject).mockResolvedValue(projectedRaw(kind, { revision: 6 })); await act(async () => done.resolve(response(projectedWire(kind, kind, ['completed', 'cancelled']))));
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        const panel = screen.getByRole('complementary');
        expect(within(panel).getAllByAltText('变体').map(image => image.getAttribute('src')?.split('/').at(-1))).toEqual(['new-2.png', 'new-1.png', 'old.png', 'other.png']);
        expect(within(panel).getByAltText(`${kind} p`)).toHaveAttribute('src', expect.stringContaining('/other.png'));
        expect(useProjectStore.getState().currentProject).toBe(current); expect(useProjectStore.getState().projects).toEqual([]); expect(posts()).toHaveLength(0);
    });
    it('keeps double clicks under one source preflight and one original journal', async () => {
        const pending = deferred<ReturnType<typeof raw>>(); vi.mocked(api.getProject).mockReturnValue(pending.promise); await page();
        const button = screen.getByRole('button', { name: '生成更多变体' }); fireEvent.click(button); fireEvent.click(button);
        expect(api.getProject).toHaveBeenCalledTimes(1); expect(posts()).toHaveLength(0); await act(async () => pending.resolve(raw()));
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
    it('makes inherited negative correction explicit', async () => {
        install({ negative: 'saved negative' }); await page(); fireEvent.click(screen.getByRole('button', { name: '生成更多变体' }));
        const clear = await screen.findByRole('button', { name: '本次不使用风格提示词' }); expect(posts()).toHaveLength(0); expect(api.generateAsset).not.toHaveBeenCalled();
        fireEvent.click(clear); expect(posts()).toHaveLength(0); fireEvent.click(screen.getByRole('button', { name: '生成更多变体' }));
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ apply_style: false, negative_prompt: '' });
    });
    it.each(['failure', 'older', 'missing', 'source'])('rejects %s source read without either paid route', async failure => {
        await page();
        if (failure === 'failure') vi.mocked(api.getProject).mockRejectedValue(Error('offline'));
        else vi.mocked(api.getProject).mockResolvedValue(failure === 'older' ? raw({ revision: 3 }) : failure === 'source' ? raw({ source: 'global' }) : { ...raw(), scenes: [] });
        fireEvent.click(screen.getByRole('button', { name: '生成更多变体' })); await act(async () => {});
        expect(posts()).toHaveLength(0); expect(api.generateAsset).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
    });
    it.each(['kind', 'root', 'owner', 'environment', 'close'])('ignores a pending preflight after %s changes', async change => {
        const pending = deferred<ReturnType<typeof raw>>(); vi.mocked(api.getProject).mockReturnValue(pending.promise);
        const view = renderWithIntl(inspector('scene')); fireEvent.click(screen.getByRole('button', { name: '生成更多变体' }));
        if (change === 'close') view.unmount(); else if (change === 'environment') act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        else view.rerender(inspector(change === 'kind' ? 'prop' : 'scene', raw(change === 'root' ? { id: 'q' } : change === 'owner' ? { source: 'global' } : {})));
        await act(async () => pending.resolve(raw())); expect(posts()).toHaveLength(0); expect(api.generateAsset).not.toHaveBeenCalled();
    });
    it('retains a lost ACK across drawer close and reads only the original ID on reopen', async () => {
        const pending = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(pending.promise); const view = await page();
        fireEvent.click(screen.getByRole('button', { name: '生成更多变体' })); await waitFor(() => expect(posts()).toHaveLength(1)); const key = listStudioAssetImageRequests(owner())[0].request.client_request_id;
        view.unmount(); await act(async () => pending.reject(Error('lost ACK')));
        vi.mocked(appAccessFetch).mockResolvedValue(response(wire('scene', key))); await page(); await waitFor(() => expect(gets()).toHaveLength(1));
        expect(String(gets()[0][0])).toContain(key); expect(posts()).toHaveLength(1);
    });
    it('gives an old task recovery priority over a new Core submit', async () => {
        const key = libraryOperationKey('generate', requireActiveRuntimeEnvironment().key, ['p', 'shared', 'scene']);
        rememberLibraryRecovery({ kind: 'variant', key, projectId: 'p', assetId: 'shared', assetType: 'scene', taskId: 'legacy-task', beforeVariantIds: ['old', 'other'] });
        vi.mocked(api.getTaskStatus).mockRejectedValue(Error('404')); await page(); fireEvent.click(await screen.findByRole('button', { name: '重新读取' })); await act(async () => {});
        expect(api.getTaskStatus).toHaveBeenCalledWith('legacy-task'); expect(posts()).toHaveLength(0); expect(api.generateAsset).not.toHaveBeenCalled();
    });
    it.each(['series', 'global'] as const)('keeps %s Library sources disabled', source => {
        renderWithIntl(inspector('scene', raw(), source)); expect(screen.getByRole('button', { name: /生成更多变体/ })).toBeDisabled(); expect(posts()).toHaveLength(0); expect(api.generateAsset).not.toHaveBeenCalled();
    });
    it.each(['revision', 'library'])('rejects a terminal read older than the last partial %s snapshot', async lower => {
        seed('scene'); vi.mocked(appAccessFetch).mockResolvedValue(response(projectedWire('scene', 'scene', ['completed', 'needs_confirmation'])));
        vi.mocked(api.getProject).mockResolvedValue(projectedRaw('scene', { revision: 8, library: 3 })); await page();
        await waitFor(() => expect(screen.getAllByAltText('变体')).toHaveLength(4));
        vi.mocked(appAccessFetch).mockResolvedValue(response(projectedWire('scene', 'scene', ['completed', 'completed'])));
        vi.mocked(api.getProject).mockResolvedValue(raw({ revision: lower === 'revision' ? 7 : 9, library: lower === 'library' ? 2 : 3 }));
        fireEvent.click(screen.getByRole('button', { name: '查看生成结果' })); await act(async () => {});
        expect(screen.getAllByAltText('变体')).toHaveLength(4); expect(listStudioAssetImageRequests(owner())).toHaveLength(1); expect(posts()).toHaveLength(0);
    });
    it.each(['kind', 'root', 'owner', 'environment', 'close'])('rejects a terminal project read after %s changes', async change => {
        seed('scene'); vi.mocked(appAccessFetch).mockResolvedValue(response(wire('scene', 'scene', ['completed', 'completed'])));
        const pending = deferred<ReturnType<typeof raw>>(); vi.mocked(api.getProject).mockReturnValue(pending.promise);
        const view = renderWithIntl(inspector('scene')); await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
        if (change === 'close') view.unmount(); else if (change === 'environment') act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        else view.rerender(inspector(change === 'kind' ? 'prop' : 'scene', raw(change === 'root' ? { id: 'q' } : change === 'owner' ? { source: 'global' } : {})));
        await act(async () => pending.resolve(raw({ revision: 8, fresh: true })));
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); expect(useProjectStore.getState().currentProject?.id).toBe('b');
        if (change !== 'close') expect(screen.getAllByAltText('变体')).toHaveLength(2); expect(posts()).toHaveLength(0);
    });
    it('rejects a completed original owner receipt after cached same-ID asset changes owner', async () => {
        seed('scene'); vi.mocked(appAccessFetch).mockResolvedValue(response(wire('scene', 'scene', ['completed', 'completed'])));
        const pending = deferred<ReturnType<typeof raw>>(); vi.mocked(api.getProject).mockReturnValue(pending.promise);
        await page(); await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
        act(() => useProjectStore.setState({ projects: [parsed(raw({ revision: 6, source: 'global' }))] }));
        await act(async () => pending.resolve(raw({ revision: 8, fresh: true })));
        expect(useProjectStore.getState().projects[0].scenes[0].source).toBe('global'); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(screen.getAllByAltText('变体')).toHaveLength(2); expect(posts()).toHaveLength(0);
    });
    it('uses a verified source row shared-version floor for uncached readback', async () => {
        install({ library: 5 }); seed('scene');
        vi.mocked(appAccessFetch).mockResolvedValue(response(wire('scene', 'scene', ['completed', 'completed'])));
        vi.mocked(api.getProject).mockResolvedValue(raw({ revision: 4, library: 4, fresh: true })); await page();
        await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1)); await act(async () => {});
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); expect(screen.getAllByAltText('变体')).toHaveLength(2);
    });
    it('accepts the actual raw project-list shape without inventing shared metadata', async () => {
        const listed = parsed(raw()); delete listed.read_versions; delete listed.readEnvironment;
        vi.mocked(api.getProjects).mockResolvedValue([listed]); await page(); fireEvent.click(screen.getByRole('button', { name: '生成更多变体' }));
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(useProjectStore.getState().projects).toEqual([]);
    });
    it('keeps explicit standalone source identity across obsolete cached series changes', async () => {
        const cached = (id: string, revision: number) => ({ ...parsed(raw({ revision })), series_id: id, read_versions: { project: revision, library: 1, series: { id, revision: 1 } } });
        useProjectStore.setState({ projects: [cached('series-a', 2)] });
        const pending = deferred<ReturnType<typeof raw>>(); vi.mocked(api.getProject).mockReturnValue(pending.promise); await page();
        fireEvent.click(screen.getByRole('button', { name: '生成更多变体' })); act(() => useProjectStore.setState({ projects: [cached('series-b', 3)] }));
        await act(async () => pending.resolve(raw({ revision: 5 }))); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(useProjectStore.getState().projects[0].series_id).toBeNull(); expect(useProjectStore.getState().currentProject?.id).toBe('b');
    });
    it('keeps one receipt lease through a cached project snapshot replacement', async () => {
        useProjectStore.setState({ projects: [parsed(raw())] }); seed('scene');
        vi.mocked(appAccessFetch).mockResolvedValue(response(projectedWire('scene', 'scene', ['completed', 'completed'])));
        vi.mocked(api.getProject).mockResolvedValue(projectedRaw('scene', { revision: 6 })); await page();
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0)); await act(async () => {});
        expect(gets()).toHaveLength(1); expect(api.getProject).toHaveBeenCalledTimes(1);
        const saved = useProjectStore.getState().projects[0].scenes[0];
        expect(saved.image_asset?.selected_id).toBe('old'); expect(saved.image_asset?.variants.find(v => v.id === 'old')).toMatchObject({ is_favorited: true });
        expect(saved.video_prompt).toBe('keep video'); expect(saved.video_assets?.[0].video_url).toBe('/keep.mp4');
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
    });
    it('uses the server selection when the previously previewed variant no longer exists', async () => {
        seed('scene'); const done = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(done.promise); await page();
        fireEvent.click(screen.getAllByAltText('变体').find(image => image.getAttribute('src')?.endsWith('/other.png'))!.closest('button')!);
        const fresh = projectedRaw('scene', { revision: 6 }); fresh.scenes[0].image_asset.variants = fresh.scenes[0].image_asset.variants.filter(v => v.id !== 'other');
        vi.mocked(api.getProject).mockResolvedValue(fresh); await act(async () => done.resolve(response(projectedWire('scene', 'scene', ['completed', 'completed']))));
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        const panel = screen.getByRole('complementary'); expect(within(panel).getAllByAltText('变体')).toHaveLength(3);
        expect(within(panel).getByAltText('scene p')).toHaveAttribute('src', expect.stringContaining('/old.png'));
    });
    it('retains confirmed variants after closing and reopening the actual Library drawer', async () => {
        seed('scene'); vi.mocked(appAccessFetch).mockResolvedValue(response(projectedWire('scene', 'scene', ['completed', 'completed'])));
        vi.mocked(api.getProject).mockResolvedValue(projectedRaw('scene', { revision: 6 })); await page();
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0)); expect(screen.getAllByAltText('变体')).toHaveLength(4);
        fireEvent.click(screen.getByRole('button', { name: '关闭详情' })); fireEvent.click(screen.getByText('scene p'));
        expect(screen.getAllByAltText('变体')).toHaveLength(4); expect(gets()).toHaveLength(1); expect(posts()).toHaveLength(0);
        expect(useProjectStore.getState().projects).toEqual([]); expect(useProjectStore.getState().currentProject?.id).toBe('b');
    });
    it('does not let an older pending Library refresh overwrite a confirmed projection', async () => {
        seed('scene'); const receipt = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(receipt.promise); await page();
        const listed = deferred<ReturnType<typeof parsed>[]>(); vi.mocked(api.getProjects).mockReturnValueOnce(listed.promise);
        vi.mocked(api.promoteAssetToLibrary).mockResolvedValue({ id: 'global-copy' });
        vi.mocked(api.listLibraryAssets).mockResolvedValue({ revision: 2, characters: [], scenes: [{ ...raw().scenes[0], id: 'global-copy', name: 'Copied scene' }], props: [] });
        fireEvent.click(screen.getByRole('button', { name: '提升到全局' })); await waitFor(() => expect(api.getProjects).toHaveBeenCalledTimes(2));
        vi.mocked(api.getProject).mockResolvedValue(projectedRaw('scene', { revision: 6 })); await act(async () => receipt.resolve(response(projectedWire('scene', 'scene', ['completed', 'completed']))));
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        await act(async () => listed.resolve([parsed(raw())])); fireEvent.click(screen.getByRole('button', { name: '关闭详情' })); fireEvent.click(screen.getByText('scene p'));
        expect(screen.getAllByAltText('变体')).toHaveLength(4); expect(posts()).toHaveLength(0);
    });
    it('keeps the verified project floor through a later successful Library refresh', async () => {
        seed('scene'); vi.mocked(appAccessFetch).mockResolvedValue(response(projectedWire('scene', 'scene', ['completed', 'completed'])));
        vi.mocked(api.getProject).mockResolvedValue(projectedRaw('scene', { revision: 6 })); await page();
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        vi.mocked(api.promoteAssetToLibrary).mockResolvedValue({ id: 'global-copy' });
        vi.mocked(api.listLibraryAssets).mockResolvedValue({ revision: 2, characters: [], scenes: [{ ...raw().scenes[0], id: 'global-copy', name: 'Copied scene' }], props: [] });
        fireEvent.click(screen.getByRole('button', { name: '提升到全局' })); await screen.findByText('Copied scene');
        fireEvent.click(screen.getByRole('button', { name: '关闭详情' })); fireEvent.click(screen.getByText('scene p'));
        expect(screen.getAllByAltText('变体')).toHaveLength(4); expect(posts()).toHaveLength(0);
    });
    it('rejects a late terminal snapshot after the original project moves to a series', async () => {
        seed('scene'); vi.mocked(appAccessFetch).mockResolvedValue(response(wire('scene', 'scene', ['completed', 'completed'])));
        const pending = deferred<ReturnType<typeof raw>>(); vi.mocked(api.getProject).mockReturnValue(pending.promise); await page();
        await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
        const moved = { ...parsed(raw({ revision: 6 })), series_id: 'series-b', read_versions: { project: 6, library: 1, series: { id: 'series-b', revision: 1 } } };
        act(() => useProjectStore.setState({ projects: [moved] })); await act(async () => pending.resolve(raw({ revision: 8, fresh: true })));
        expect(useProjectStore.getState().projects[0]).toBe(moved); expect(listStudioAssetImageRequests(owner())).toHaveLength(1); expect(screen.getAllByAltText('变体')).toHaveLength(2);
    });
    it('accepts a real raw local list item without source against an enriched episode read', async () => {
        const listed = parsed(raw()); delete listed.read_versions; delete listed.readEnvironment;
        listed.scenes.forEach(asset => { delete asset.source; }); listed.props.forEach(asset => { delete asset.source; });
        vi.mocked(api.getProjects).mockResolvedValue([listed]);
        vi.mocked(api.getProject).mockResolvedValueOnce(raw()).mockResolvedValue(projectedRaw('scene', { revision: 6 }));
        vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => response(projectedWire('scene', JSON.parse(String(init?.body)).client_request_id, ['completed', 'completed', 'cancelled'])));
        await page(); fireEvent.click(screen.getByRole('button', { name: '生成更多变体' }));
        await waitFor(() => expect(posts()).toHaveLength(1)); await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        expect(api.generateAsset).not.toHaveBeenCalled(); expect(screen.getAllByAltText('变体')).toHaveLength(4);
        expect(api.getProject).toHaveBeenCalledTimes(2); expect(gets()).toHaveLength(0);
    });
    it.each([false, true])('preserves original owner groups while reading enriched globals (explicit reference: %s)', async explicit => {
        const shared = { ...raw({ id: 'g', source: 'global' }).scenes[0], id: 'global-scene', name: 'Global scene' };
        const listed = parsed(raw()); if (explicit) listed.scenes.push(shared as typeof listed.scenes[number]);
        vi.mocked(api.getProjects).mockResolvedValue([listed]);
        vi.mocked(api.listLibraryAssets).mockResolvedValue({ revision: 1, characters: [], scenes: [shared], props: [] });
        const fresh = projectedRaw('scene', { revision: 6 }); fresh.scenes.push(shared);
        vi.mocked(api.getProject).mockResolvedValue(fresh); seed('scene');
        vi.mocked(appAccessFetch).mockResolvedValue(response(projectedWire('scene', 'scene', ['completed', 'completed'])));
        await page(); await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        expect(screen.getAllByText('Global scene')).toHaveLength(explicit ? 2 : 1);
        fireEvent.click(screen.getByRole('button', { name: '关闭详情' })); fireEvent.click(screen.getByText('scene p'));
        expect(screen.getAllByText('Global scene')).toHaveLength(explicit ? 2 : 1); expect(screen.getAllByAltText('变体')).toHaveLength(4);
    });
    it.each(kinds.flatMap(kind => ['missing', 'obsolete-owner'].map(state => [kind, state] as const)))('accepts newer %s Library source over %s in an older cached project', async (kind, state) => {
        const listed = parsed(raw({ revision: 6 }));
        const old = parsed(raw({ revision: 4, source: state === 'obsolete-owner' ? 'global' : 'episode' }));
        if (state === 'missing') old[kind === 'scene' ? 'scenes' : 'props'] = [];
        useProjectStore.setState({ projects: [old] });
        const current = useProjectStore.getState().currentProject;
        vi.mocked(api.getProjects).mockResolvedValue([listed]);
        vi.mocked(api.getProject).mockResolvedValue(raw({ revision: 6 }));
        await page(kind); fireEvent.click(screen.getByRole('button', { name: '生成更多变体' }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ asset_type: kind, asset_id: 'shared', generation_type: 'all' });
        expect(api.generateAsset).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject).toBe(current);
        const confirmed = useProjectStore.getState().projects[0];
        expect(confirmed.revision).toBe(6);
        expect(confirmed[kind === 'scene' ? 'scenes' : 'props'][0]).toMatchObject({ id: 'shared', source: 'episode' });
    });
    it.each(kinds.flatMap(kind => [6, 7].flatMap(revision => ['missing', 'changed-owner'].map(state => [kind, revision, state] as const))))('rejects %s Library source when cached revision %s records %s', async (kind, revision, state) => {
        const cached = parsed(raw({ revision, source: state === 'changed-owner' ? 'global' : 'episode' }));
        if (state === 'missing') cached[kind === 'scene' ? 'scenes' : 'props'] = [];
        useProjectStore.setState({ projects: [cached] });
        vi.mocked(api.getProjects).mockResolvedValue([parsed(raw({ revision: 6 }))]);
        vi.mocked(api.getProject).mockResolvedValue(raw({ revision: 8 }));
        await page(kind); fireEvent.click(screen.getByRole('button', { name: '生成更多变体' }));
        await screen.findByText('本次生成尚未提交，请检查连接或本机存储后重试。');
        expect(posts()).toHaveLength(0); expect(api.generateAsset).not.toHaveBeenCalled();
        expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
        expect(useProjectStore.getState().projects[0]).toBe(cached);
    });
    it('keeps the original legacy model route for the actual source project', async () => {
        install({ model: 'wan2.6-t2i' }); vi.mocked(api.generateAsset).mockRejectedValue(Error('unconfirmed'));
        await page(); fireEvent.click(screen.getByRole('button', { name: '生成更多变体' })); await waitFor(() => expect(api.generateAsset).toHaveBeenCalledTimes(1)); expect(posts()).toHaveLength(0);
    });
});
