import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from '../series/__tests__/test-utils';
import { LightboxProvider } from '../shared/preview/LightboxProvider';
import { api } from '@/lib/api';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests } from '@/lib/studioAssetImageRequest';
import { projectReadSnapshot } from '@/lib/frameCommandSnapshot';
import { clearLibraryRecoveries, libraryOperationKey, rememberLibraryRecovery } from '@/lib/libraryReadback';
import { useProjectStore } from '@/store/projectStore';
import AssetLibraryPage from './AssetLibraryPage';
import AssetInspector from './AssetInspector';

vi.mock('@/lib/api', () => ({ API_URL: 'http://test.invalid', api: {
    listSeries: vi.fn(), getProjects: vi.fn(), listLibraryAssets: vi.fn(), getProject: vi.fn(), getSeries: vi.fn(), generateAsset: vi.fn(), getTaskStatus: vi.fn(),
    createLibraryAsset: vi.fn(), promoteAssetToLibrary: vi.fn(), toggleSeriesAssetStarred: vi.fn(), toggleAssetStarred: vi.fn(), updateLibraryAsset: vi.fn(),
} }));
vi.mock('@/lib/appAccess', async original => ({ ...await original<typeof import('@/lib/appAccess')>(), appAccessFetch: vi.fn() }));
vi.mock('@/store/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn(), progress: vi.fn(() => 'toast'), update: vi.fn() } }));
const SID = '11111111-1111-4111-8111-111111111111';
const kinds = ['scene', 'prop'] as const;
type Kind = typeof kinds[number];
const models = ['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'];
const owner = (rootId = 'p') => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId });
function deferred<T>() { let resolve!: (v: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function raw({ id = 'p', revision = 4, library = 1, model = 'qwen-image-2.0', negative = '', fresh = false, source = 'episode', seriesId = null as string | null, seriesRevision = 1, inherited = false } = {}) {
    const asset = (kind: Kind) => ({ id: 'shared', name: `${kind} ${id}`, description: 'Saved description', source,
        image_url: '/old.png', status: 'completed', video_prompt: 'keep video', video_assets: [{ id: 'motion', video_url: '/keep.mp4', status: 'completed' }],
        image_asset: { selected_id: 'old', variants: [...(fresh ? [{ id: 'new-2', url: '/new-2.png' }, { id: 'new-1', url: '/new-1.png' }] : []),
            { id: 'old', url: '/old.png', is_favorited: true }, { id: 'other', url: '/other.png' }] } });
    return { id, revision, title: `Project ${id}`, original_text: 'script', series_id: seriesId, status: 'draft', created_at: 1, updated_at: 2,
        read_versions: { project: revision, library, series: seriesId ? { id: seriesId, revision: seriesRevision } : null }, model_settings: { t2i_model: model },
        art_direction: inherited ? null : { style_config: { positive_prompt: 'source light', negative_prompt: negative } },
        characters: [], scenes: [asset('scene')], props: [asset('prop')], frames: [], video_tasks: [] };
}
const parsed = (value: ReturnType<typeof raw>) => projectReadSnapshot(value.id, value, requireActiveRuntimeEnvironment().key);
let server: ReturnType<typeof raw>;
function install(options: Parameters<typeof raw>[0] = {}) { const value = raw(options); server = value; vi.mocked(api.getProjects).mockResolvedValue([parsed(value)]); vi.mocked(api.getProject).mockResolvedValue(value); return value; }
function wire(kind: Kind, key: string, statuses = ['needs_confirmation', 'needs_confirmation', 'needs_confirmation'], root = 'p') {
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: true, reason: null, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: 'studio.asset.scene_prop.v1', request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: root,
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: server?.series_id ?? null,
        owner_kind: 'script', owner_id: root, asset_type: kind, asset_id: 'shared', channel: 'image',
        members: statuses.map((status, i) => ({ variant_id: `${key}-${i}`, attempt_id: `${key}-${i}`, status, reason: null,
            ...(status === 'completed' ? { output_ref: `assets/studio/${key}-${i}.png` } : {}) })),
    } };
}
const tooLong = '本次与风格合并后的负向提示词超过 500 个字符，请缩短负向提示词或关闭风格。';
const body = () => JSON.parse(String(posts()[0]?.[1]?.body));
const generate = () => screen.getByRole('button', { name: '生成更多变体' });
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const seed = (kind: Kind, key: string = kind) => beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'shared', asset_type: kind, generation_type: 'all', batch_size: 2 }, idFactory: () => key });
const element = () => <LightboxProvider><AssetLibraryPage /></LightboxProvider>;
async function page(kind: Kind = 'scene') { const view = renderWithIntl(element()); fireEvent.click(await screen.findByText(`${kind} p`)); return view; }
function inspector(kind: Kind, value = raw(), sourceKind: 'project' | 'series' | 'global' = 'project') {
    const asset = parsed(value)[kind === 'scene' ? 'scenes' : 'props'][0];
    return <LightboxProvider><AssetInspector asset={asset} type={kind === 'scene' ? 'scenes' : 'props'} sourceName="Source" sourceId={value.id} sourceKind={sourceKind} sourceProject={parsed(value)} starred={false} onClose={() => {}} onToggleStar={() => {}} /></LightboxProvider>;
}
function lockQueue() {
    let release!: () => Promise<unknown>;
    const request = vi.fn((_name: string, _options: unknown, callback: (lock: object) => unknown) => new Promise((resolve, reject) => {
        release = async () => { try { resolve(await callback({})); } catch (error) { reject(error); } };
    }));
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request } });
    return { request, release: () => release() };
}
function noSubmission() { expect(posts()).toHaveLength(0); expect(api.generateAsset).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(0); }
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearLibraryRecoveries(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ projects: [], currentProject: parsed(raw({ id: 'b', model: 'wan2.6-t2i' })), readWatermarks: {}, runningOps: {}, currentSeries: null, generatingTasks: [] });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(api.listSeries).mockResolvedValue([]); vi.mocked(api.listLibraryAssets).mockResolvedValue({ revision: 1, characters: [], scenes: [], props: [] }); install();
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => { const input = init?.method === 'POST' ? JSON.parse(String(init.body)) : null;
        const key = input?.client_request_id ?? String(url).split('/').at(-1)!; return response(wire(input?.asset_type ?? key, key)); });
});
afterEach(() => { vi.useRealTimers(); clearLibraryRecoveries(); clearRuntimeEnvironment(); });

describe('real Library Qwen scene/prop Core requests', () => {
    it.each(kinds.flatMap(kind => models.map(model => [kind, model] as const)))('submits uncached %s source with %s and empty authored negative', async (kind, model) => {
        install({ model, negative: 'source negative' }); const current = useProjectStore.getState().currentProject; await page(kind);
        fireEvent.click(screen.getByRole('button', { name: '生成更多变体' })); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ asset_type: kind, asset_id: 'shared', generation_type: 'all', batch_size: 3,
            model_name: model, apply_style: true, negative_prompt: '', prompt: '' });
        expect(api.generateAsset).not.toHaveBeenCalled(); expect(useProjectStore.getState().currentProject).toBe(current); expect(useProjectStore.getState().projects).toEqual([]);
        expect(await screen.findAllByText('待确认')).toHaveLength(3);
    });

    it.each(kinds.flatMap(kind => [500, 501].map(count => [kind, count] as const)))('validates %s inherited negative at %i Unicode codepoints', async (kind, count) => {
        install({ negative: '😀'.repeat(count) }); await page(kind);
        if (count === 501) { expect(generate()).toBeDisabled(); expect(screen.getByText(tooLong)).toBeInTheDocument(); noSubmission(); }
        else { fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); expect(body().negative_prompt).toBe(''); }
    });
    it('style-off is explicit, local, and leaves saved style unchanged', async () => {
        install({ negative: 'x'.repeat(501) }); await page(); expect(generate()).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: '本次不使用风格提示词' })); noSubmission(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ apply_style: false, negative_prompt: '' }); expect(server.art_direction?.style_config.negative_prompt).toHaveLength(501);
    });
    it('validates fresh project style before submission without borrowing current Studio style', async () => {
        install(); await page(); vi.mocked(api.getProject).mockResolvedValue(raw({ revision: 6, negative: 'x'.repeat(501) }));
        fireEvent.click(generate()); await act(async () => {}); noSubmission(); expect(screen.getByText(tooLong)).toBeInTheDocument();
    });
    it('uses uncached source series proof locally and sends no inherited negative', async () => {
        install({ seriesId: 's', inherited: true }); vi.mocked(api.getSeries).mockResolvedValue({ id: 's', revision: 1, art_direction: { style_config: { negative_prompt: 'series negative', positive_prompt: 'series positive' } } });
        const current = useProjectStore.getState().currentProject; renderWithIntl(inspector('scene', server)); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(api.getSeries).toHaveBeenCalledWith('s'); expect(body().negative_prompt).toBe(''); expect(useProjectStore.getState().currentProject).toBe(current);
        expect(useProjectStore.getState().projects).toEqual([]); expect(useProjectStore.getState().currentSeries).toBeNull();
    });
    it.each(['id', 'revision', 'missing revision', 'failure', 'long'])('rejects %s series proof for the uncached root without generation', async change => {
        install({ seriesId: 's', inherited: true });
        if (change === 'failure') vi.mocked(api.getSeries).mockRejectedValue(Error('offline'));
        else vi.mocked(api.getSeries).mockResolvedValue({ id: change === 'id' ? 'other' : 's', ...(change === 'missing revision' ? {} : { revision: change === 'revision' ? 2 : 1 }), art_direction: { style_config: { negative_prompt: change === 'long' ? 'x'.repeat(501) : '' } } });
        renderWithIntl(inspector('scene', server)); fireEvent.click(generate()); await act(async () => {}); noSubmission();
    });
    it.each(['root revision', 'owner', 'model', 'root series', 'environment ABA', 'close'])('cancels %s changes during the Web Lock wait', async change => {
        const initial = raw(); const view = renderWithIntl(inspector('scene', initial)); const lock = lockQueue(); fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        act(() => {
            if (change === 'environment ABA') { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); }
            else if (change === 'close') view.unmount();
            else view.rerender(inspector('scene', raw({ revision: 8, ...(change === 'owner' ? { source: 'global' } : change === 'model' ? { model: models[1] } : change === 'root series' ? { seriesId: 's' } : {}) })));
        });
        await act(async () => { await lock.release(); }); noSubmission();
    });
    it('does not supersede an observed cached source update while the lock is pending', async () => {
        await page(); const lock = lockQueue(); fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        act(() => useProjectStore.setState({ projects: [parsed(raw({ revision: 8, negative: 'changed' }))] }));
        await act(async () => { await lock.release(); }); noSubmission();
    });
    it.each(['lost ACK', '503'])('recovers %s via original ID after drawer close without fallback', async failure => {
        if (failure === '503') vi.mocked(appAccessFetch).mockResolvedValueOnce(response({ detail: 'unavailable' }, 503)); else vi.mocked(appAccessFetch).mockRejectedValueOnce(Error('lost ACK'));
        const view = await page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); const key = body().client_request_id;
        view.unmount(); vi.mocked(appAccessFetch).mockResolvedValue(response(wire('scene', key))); await page(); await waitFor(() => expect(gets().length).toBeGreaterThan(0));
        expect(gets().every(([url]) => String(url).endsWith(key))).toBe(true); expect(posts()).toHaveLength(1); expect(api.generateAsset).not.toHaveBeenCalled();
    });
    it('gives old legacy task recovery priority under Qwen', async () => {
        const key = libraryOperationKey('generate', requireActiveRuntimeEnvironment().key, ['p', 'shared', 'scene']);
        rememberLibraryRecovery({ kind: 'variant', key, projectId: 'p', assetId: 'shared', assetType: 'scene', taskId: 'legacy-task', beforeVariantIds: ['old', 'other'] });
        vi.mocked(api.getTaskStatus).mockRejectedValue(Error('404')); await page(); fireEvent.click(await screen.findByRole('button', { name: '重新读取' })); await act(async () => {});
        expect(api.getTaskStatus).toHaveBeenCalledWith('legacy-task'); noSubmission();
    });
    it.each(kinds)('replays old %s receipt and preserves selected/favorited/motion media on full readback', async kind => {
        seed(kind); const done = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(done.promise); const current = useProjectStore.getState().currentProject; await page(kind);
        fireEvent.click(screen.getAllByAltText('变体').find(image => image.getAttribute('src')?.endsWith('/other.png'))!.closest('button')!);
        const receipt = wire(kind, kind, ['completed', 'completed']), fresh = raw({ revision: 6, fresh: true });
        const target = fresh[kind === 'scene' ? 'scenes' : 'props'][0];
        Object.assign(target, { image_request_id: receipt.request.id });
        target.image_asset.variants.splice(0, 2, ...receipt.request.members.slice().reverse().map(member => ({
            id: member.variant_id, url: member.output_ref!, is_favorited: false,
        })));
        vi.mocked(api.getProject).mockResolvedValue(fresh); await act(async () => done.resolve(response(receipt)));
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        const panel = screen.getByRole('complementary'); expect(within(panel).getAllByAltText('变体')).toHaveLength(4);
        expect(within(panel).getByAltText(`${kind} p`)).toHaveAttribute('src', expect.stringContaining('/other.png'));
        expect(useProjectStore.getState().currentProject).toBe(current); expect(posts()).toHaveLength(0);
    });

    it('cancels environment ABA during its private inherited-series read', async () => {
        install({ seriesId: 's', inherited: true }); const read = deferred<unknown>(); vi.mocked(api.getSeries).mockReturnValue(read.promise);
        renderWithIntl(inspector('scene', server)); fireEvent.click(generate()); await waitFor(() => expect(api.getSeries).toHaveBeenCalledOnce());
        act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        await act(async () => read.resolve({ id: 's', revision: 1, art_direction: null })); noSubmission();
    });
});

describe('GPT Library requests stay with the uncached source project', () => {
    it.each([['scene', 'gpt-image-2'], ['prop', 'gpt-image/gpt-image-2#image']] as const)(
        'submits uncached %s with %s while preserving inherited negative and current Studio', async (kind, model) => {
            install({ model, seriesId: 's', inherited: true });
            const inherited = { id: 's', revision: 1, art_direction: { style_config: {
                positive_prompt: 'source series positive', negative_prompt: 'x'.repeat(501),
            } } };
            vi.mocked(api.getSeries).mockResolvedValue(inherited);
            const current = useProjectStore.getState().currentProject;
            renderWithIntl(inspector(kind, server)); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
            expect(body()).toMatchObject({ asset_id: 'shared', asset_type: kind, generation_type: 'all', model_name: model,
                prompt: '', apply_style: true, negative_prompt: '', batch_size: 3 });
            expect(String(posts()[0][0])).toMatch(/\/projects\/p\/asset_image_requests$/);
            expect(api.getSeries).toHaveBeenCalledWith('s');
            expect(await screen.findByText('负向提示词（本次不应用）')).toBeInTheDocument();
            expect(inherited.art_direction.style_config.negative_prompt).toHaveLength(501);
            expect(useProjectStore.getState().currentProject).toBe(current);
            expect(useProjectStore.getState().projects).toEqual([]); expect(useProjectStore.getState().currentSeries).toBeNull();
            expect(api.generateAsset).not.toHaveBeenCalled();
        },
    );
    it('does not treat mismatched GPT source-series proof as permission to submit', async () => {
        install({ model: 'gpt-image-2', seriesId: 's', inherited: true });
        vi.mocked(api.getSeries).mockResolvedValue({ id: 's', revision: 2, art_direction: null });
        renderWithIntl(inspector('scene', server)); fireEvent.click(generate());
        await waitFor(() => expect(api.getSeries).toHaveBeenCalledOnce()); await act(async () => {}); noSubmission();
    });
    it.each(['owner', 'environment ABA'])('cancels GPT Library %s while its source lock is held', async change => {
        const initial = install({ model: 'gpt-image-2' }); const view = renderWithIntl(inspector('scene', initial)); const lock = lockQueue();
        try {
            fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
            act(() => {
                if (change === 'owner') view.rerender(inspector('scene', raw({ model: 'gpt-image-2', source: 'global', revision: 8 })));
                else {
                    activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' });
                    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
                }
            });
        } finally { if (lock.request.mock.calls.length) await act(async () => { await lock.release(); }); }
        noSubmission();
    });
    it('gives original legacy-task recovery priority under the new GPT gate', async () => {
        install({ model: 'gpt-image-2' });
        const key = libraryOperationKey('generate', requireActiveRuntimeEnvironment().key, ['p', 'shared', 'scene']);
        rememberLibraryRecovery({ kind: 'variant', key, projectId: 'p', assetId: 'shared', assetType: 'scene', taskId: 'legacy-task', beforeVariantIds: ['old', 'other'] });
        vi.mocked(api.getTaskStatus).mockRejectedValue(Error('404')); await page();
        fireEvent.click(await screen.findByRole('button', { name: '重新读取' })); await act(async () => {});
        expect(api.getTaskStatus).toHaveBeenCalledWith('legacy-task'); noSubmission();
    });
});
