import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests } from '@/lib/studioAssetImageRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from './frame-command-fixtures';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
const SID = '11111111-1111-4111-8111-111111111111';
const kinds = ['scene', 'prop'] as const;
type Kind = typeof kinds[number];
const models = ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'];
const owner = (id = 'a') => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: id });
function snapshot({ id = 'a', revision = 5, source = 'global', seriesId = 'series-a', model = 'wan2.7-image', negative = '', library = 9 } = {}) {
    const asset = (kind: string) => ({ id: 'shared', name: `${kind} ${id}`, description: `${kind} description`, source,
        image_url: '/old.png', image_asset: { selected_id: 'old', variants: [{ id: 'old', url: '/old.png', is_favorited: true }, { id: 'other', url: '/other.png' }] },
        video_prompt: 'keep video', video_assets: [{ id: 'video', video_url: '/keep.mp4', status: 'completed' }] });
    return enriched({ ...ack([], id, revision), series_id: source === 'series' ? seriesId : null,
        model_settings: { t2i_model: model },
        art_direction: negative ? { style_config: { name: 'Saved', positive_prompt: 'soft light', negative_prompt: negative } } : null,
        characters: [], scenes: [asset('scene')], props: [asset('prop')] }, library, 1);
}
function install(options: Parameters<typeof snapshot>[0] = {}) {
    const value = snapshot({ revision: (useProjectStore.getState().currentProject?.revision ?? 3) + 1, ...options });
    useProjectStore.getState().applyProjectReadSnapshot(value.id, value);
    vi.mocked(axios.get).mockResolvedValue({ data: value });
    return value;
}
function wire(kind: Kind, key: string, statuses = ['needs_confirmation'], root = 'a') {
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: true, reason: null, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: 'studio.asset.scene_prop.v1', request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: root,
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: null,
        owner_kind: 'global', owner_id: 'library', asset_type: kind, asset_id: 'shared', channel: 'image',
        members: statuses.map((status, i) => ({ variant_id: `${key}-${i}`, attempt_id: `${key}-${i}`, status, reason: null,
            ...(status === 'completed' ? { output_ref: `assets/studio/${key}-${i}.png` } : {}) })),
    } };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const element = () => <LightboxProvider><ConsistencyVault /></LightboxProvider>;
function page(kind: Kind = 'scene') { const view = renderWithIntl(element()); tab(kind); return view; }
const tab = (kind: Kind) => fireEvent.click(screen.getByRole('button', { name: new RegExp(kind === 'scene' ? 'Scenes' : 'Props') }));
const open = (kind: Kind = 'scene', id = 'a') => fireEvent.click(screen.getByRole('heading', { level: 3, name: `${kind} ${id}` }));
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const seed = (kind: Kind, key: string = kind, id = 'a') => beginStudioAssetImageRequest({ ...owner(id), input: { asset_id: 'shared', asset_type: kind, generation_type: 'all', batch_size: 2 }, idFactory: () => key });

beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); vi.spyOn(window, 'alert').mockImplementation(() => {});
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ projects: [project(), project('b')], currentProject: project(), readWatermarks: {}, currentSeries: null, generatingTasks: [] });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        const request = init?.method === 'POST' ? JSON.parse(String(init.body)) : null;
        const key = request?.client_request_id ?? String(url).split('/').at(-1)!;
        return response(wire(request?.asset_type ?? key, key));
    });
    install();
});
afterEach(() => { vi.useRealTimers(); clearRuntimeEnvironment(); });

describe('Vault scene/prop actual card and detail Core requests', () => {
    it.each(kinds.flatMap(kind => models.map(model => [kind, model] as const)))('routes %s card through strict request for %s', async (kind, model) => {
        install({ model }); page(kind);
        fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ asset_type: kind, asset_id: 'shared', generation_type: 'all', model_name: model, batch_size: 1 });
        expect(axios.post).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(await screen.findByText('待确认')).toBeInTheDocument();
    });
    it.each(kinds)('preserves the %s detail negative draft until explicit clear, which does not submit', async kind => {
        page(kind); open(kind);
        expect(screen.getByRole('button', { name: '生成' })).toBeDisabled();
        expect(screen.getByText('此模型不支持负向提示词，请清空后再生成。')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Advanced Settings/ }));
        expect(screen.getByPlaceholderText('Enter negative prompt...')).not.toHaveValue('');
        fireEvent.click(screen.getByRole('button', { name: '清空负向提示词' }));
        expect(screen.getByPlaceholderText('Enter negative prompt...')).toHaveValue('');
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'x2' })); fireEvent.click(screen.getByRole('button', { name: '生成' }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ batch_size: 2, negative_prompt: '' });
        expect(screen.getByPlaceholderText('Enter negative prompt...')).toHaveValue('');
    });
    it.each(kinds)('shares one %s preflight lock between card and detail', async kind => {
        const pending = deferred<{ data: ReturnType<typeof snapshot> }>(); vi.mocked(axios.get).mockReturnValue(pending.promise);
        page(kind); fireEvent.click(screen.getByRole('button', { name: 'Generate' })); open(kind);
        const clear = screen.queryByRole('button', { name: '清空负向提示词' }); if (clear) fireEvent.click(clear);
        fireEvent.click(screen.getByRole('button', { name: '生成' }));
        expect(axios.get).toHaveBeenCalledTimes(1); expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        await act(async () => pending.resolve({ data: snapshot() }));
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
    it('requires explicit correction of both editable and inherited negative text', async () => {
        install({ negative: 'no signs' }); page(); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(screen.getByRole('button', { name: '清空负向提示词' })).toBeInTheDocument());
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: '清空负向提示词' }));
        expect(screen.getByRole('button', { name: '生成' })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: '本次不使用风格提示词' }));
        expect(screen.getByRole('button', { name: '生成' })).toBeEnabled(); expect(posts()).toHaveLength(0);
        fireEvent.click(screen.getByRole('button', { name: '生成' })); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ apply_style: false, negative_prompt: '' });
        expect(useProjectStore.getState().currentProject?.art_direction?.style_config?.negative_prompt).toBe('no signs');
    });
    it.each(['kind', 'root', 'owner', 'environment', 'model'])('rejects a delayed preflight after %s changes', async change => {
        const pending = deferred<{ data: ReturnType<typeof snapshot> }>(); vi.mocked(axios.get).mockReturnValue(pending.promise);
        const view = page(); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        if (change === 'kind') tab('prop');
        else if (change === 'root') act(() => useProjectStore.setState({ currentProject: project('b') }));
        else if (change === 'owner') act(() => install({ source: 'series', seriesId: 'series-b', revision: 7 }));
        else if (change === 'model') act(() => install({ model: 'wan2.6-t2i', revision: 7 }));
        else act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        await act(async () => pending.resolve({ data: snapshot({ revision: 8 }) }));
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); view.unmount();
    });
    it.each(kinds)('keeps lost %s ACK journal and reopens with original GET only', async kind => {
        vi.mocked(appAccessFetch).mockImplementation(async () => { throw Error('lost ACK'); });
        const view = page(kind); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(posts()).toHaveLength(1)); await act(async () => {});
        const key = listStudioAssetImageRequests(owner())[0].request.client_request_id; view.unmount();
        vi.mocked(appAccessFetch).mockImplementation(async () => response(wire(kind, key)));
        page(kind); await waitFor(() => expect(gets()).toHaveLength(1));
        expect(posts()).toHaveLength(1); expect(String(gets()[0][0])).toContain(key); expect(await screen.findByText('待确认')).toBeInTheDocument();
    });
    it('reads only the active kind journal when IDs match and preserves partial/cancelled states', async () => {
        seed('scene'); seed('prop');
        const fresh = snapshot({ revision: 8, library: 10 });
        for (const kind of kinds) {
            const receipt = wire(kind, kind, ['completed', 'cancelled']);
            const target = fresh[kind === 'scene' ? 'scenes' : 'props'][0];
            Object.assign(target, { image_request_id: receipt.request.id });
            target.image_asset.variants.unshift({ id: receipt.request.members[0].variant_id,
                url: receipt.request.members[0].output_ref!, is_favorited: false });
        }
        vi.mocked(axios.get).mockResolvedValue({ data: fresh });
        vi.mocked(appAccessFetch).mockImplementation(async url => { const kind = String(url).endsWith('/scene') ? 'scene' : 'prop'; return response(wire(kind, kind, ['completed', 'cancelled'])); });
        page(); await waitFor(() => expect(screen.getByText('本次未生成')).toBeInTheDocument());
        expect(gets()).toHaveLength(1); expect(String(gets()[0][0])).toMatch(/scene$/); expect(posts()).toHaveLength(0);
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(1));
        expect(useProjectStore.getState().currentProject?.scenes[0].image_asset?.selected_id).toBe('old');
        tab('prop'); await waitFor(() => expect(gets()).toHaveLength(2));
        expect(String(gets()[1][0])).toMatch(/prop$/);
    });
    it('keeps a cleared detail draft through close/reopen and later same-owner reads', async () => {
        page(); open(); fireEvent.click(screen.getByRole('button', { name: '清空负向提示词' }));
        fireEvent.click(screen.getByRole('button', { name: 'Done' })); open();
        fireEvent.click(screen.getByRole('button', { name: /Advanced Settings/ }));
        expect(screen.getByPlaceholderText('Enter negative prompt...')).toHaveValue('');
        act(() => { install({ revision: 8, library: 10 }); });
        expect(screen.getByPlaceholderText('Enter negative prompt...')).toHaveValue('');
        expect(posts()).toHaveLength(0);
    });
    it('blocks a newly discovered inherited negative during preflight without submitting', async () => {
        const value = snapshot({ revision: 7, negative: 'newly saved negative' });
        vi.mocked(axios.get).mockResolvedValue({ data: value });
        page(); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(screen.getByRole('button', { name: '本次不使用风格提示词' })).toBeInTheDocument());
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        expect(screen.getByRole('button', { name: '生成' })).toBeDisabled();
    });
    it('uses series style only for the matching strict project root', async () => {
        install({ source: 'series' });
        useProjectStore.setState({ currentSeries: { id: 'series-a', revision: 1, title: 'Series', description: '',
            characters: [], scenes: [], props: [], episode_ids: ['a'], created_at: 1, updated_at: 1,
            art_direction: { selected_style_id: 'saved', custom_styles: [], ai_recommendations: [], style_config: { id: 'saved', is_custom: true, name: 'Saved', positive_prompt: 'series light', negative_prompt: 'series negative' } } } });
        page(); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        expect(screen.getByRole('button', { name: '本次不使用风格提示词' })).toBeInTheDocument();
        expect(posts()).toHaveLength(0);
        fireEvent.click(screen.getByRole('button', { name: '清空负向提示词' }));
        fireEvent.click(screen.getByRole('button', { name: '本次不使用风格提示词' }));
        fireEvent.click(screen.getByRole('button', { name: '生成' }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ apply_style: false, style_prompt: 'series light' });
    });
    it('keeps the card and detail busy through the original POST acknowledgement', async () => {
        const pending = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(pending.promise);
        page(); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(posts()).toHaveLength(1)); open();
        fireEvent.click(screen.getByRole('button', { name: '清空负向提示词' }));
        expect(screen.getByRole('button', { name: '生成' })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: '生成' }));
        fireEvent.click(screen.getByRole('button', { name: 'Done' }));
        expect(screen.getByRole('button', { name: 'Generating...' })).toBeDisabled();
        expect(posts()).toHaveLength(1); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        const key = JSON.parse(String(posts()[0][1]?.body)).client_request_id;
        await act(async () => pending.resolve(response(wire('scene', key))));
        expect(await screen.findByText('待确认')).toBeInTheDocument();
    });
    it.each(['404', '503'])('keeps an original journal after receipt GET %s without resubmission', async code => {
        seed('scene'); vi.mocked(appAccessFetch).mockImplementation(async () => response({ detail: 'unavailable' }, Number(code)));
        page(); expect(await screen.findAllByText('待确认')).toHaveLength(2);
        fireEvent.click(screen.getByRole('button', { name: '查看生成结果' }));
        await waitFor(() => expect(gets()).toHaveLength(2));
        expect(posts()).toHaveLength(0); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(axios.get).not.toHaveBeenCalled();
    });
    it('does not use an original global terminal receipt to confirm a new local owner', async () => {
        seed('scene'); install({ source: 'episode' });
        vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('scene', 'scene', ['completed', 'cancelled'])));
        page(); expect(await screen.findByText('结果已保存，但最新素材读取失败，请再次查看。')).toBeInTheDocument();
        expect(axios.get).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(useProjectStore.getState().currentProject?.scenes[0].source).toBe('episode');
    });
    it('rejects an older terminal readback after a newer choice arrives', async () => {
        seed('scene'); const pending = deferred<{ data: ReturnType<typeof snapshot> }>();
        vi.mocked(axios.get).mockReturnValue(pending.promise);
        vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('scene', 'scene', ['completed', 'cancelled'])));
        page(); await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        const newer = snapshot({ revision: 6, library: 10 }); newer.scenes[0].image_asset.selected_id = 'other';
        act(() => { useProjectStore.getState().applyProjectReadSnapshot('a', newer); });
        await act(async () => pending.resolve({ data: snapshot({ revision: 7, library: 9 }) }));
        expect(useProjectStore.getState().currentProject?.scenes[0].image_asset?.selected_id).toBe('other');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); expect(posts()).toHaveLength(0);
    });
    it('applies the authoritative completed snapshot preserving order, choice, favorites and motion', async () => {
        seed('scene'); const fresh = snapshot({ revision: 8, library: 10 });
        const receipt = wire('scene', 'scene', ['completed', 'completed']);
        Object.assign(fresh.scenes[0], { image_request_id: receipt.request.id });
        fresh.scenes[0].image_asset.variants.unshift(...receipt.request.members.slice().reverse().map(member => ({
            id: member.variant_id, url: member.output_ref!, is_favorited: false,
        })));
        vi.mocked(axios.get).mockResolvedValue({ data: fresh });
        vi.mocked(appAccessFetch).mockImplementation(async () => response(receipt));
        page(); await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        expect(useProjectStore.getState().currentProject?.scenes[0]).toMatchObject(fresh.scenes[0]);
        expect(posts()).toHaveLength(0);
    });
    it('ignores a late scene GET after scene to prop to scene', async () => {
        seed('scene'); seed('prop'); const pending = deferred<Response>();
        vi.mocked(appAccessFetch).mockReturnValueOnce(pending.promise).mockImplementation(async url => {
            const kind = String(url).endsWith('/scene') ? 'scene' : 'prop'; return response(wire(kind, kind, ['needs_confirmation', 'needs_confirmation']));
        });
        page(); await waitFor(() => expect(gets()).toHaveLength(1)); tab('prop');
        await waitFor(() => expect(gets()).toHaveLength(2)); tab('scene');
        await waitFor(() => expect(gets()).toHaveLength(3));
        await act(async () => pending.resolve(response(wire('scene', 'scene', ['completed', 'completed']))));
        expect(screen.queryByText('已完成')).toBeNull(); expect(axios.get).not.toHaveBeenCalled();
        expect(listStudioAssetImageRequests(owner())).toHaveLength(2); expect(posts()).toHaveLength(0);
    });
    it('retains a late ACK for the original project and only reads it when returning', async () => {
        const pending = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(pending.promise);
        page(); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        const key = JSON.parse(String(posts()[0][1]?.body)).client_request_id;
        const original = useProjectStore.getState().currentProject!;
        act(() => { useProjectStore.setState({ currentProject: project('b') }); });
        await act(async () => pending.resolve(response(wire('scene', key, ['completed']))));
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('scene', key)));
        act(() => { useProjectStore.setState({ currentProject: original }); });
        await waitFor(() => expect(gets()).toHaveLength(1)); expect(posts()).toHaveLength(1);
        expect(String(gets()[0][0])).toContain(key);
    });
    it('leaves the old detail and card route unchanged for wan2.6', async () => {
        install({ model: 'wan2.6-t2i' }); vi.mocked(axios.post).mockResolvedValue({ data: snapshot() });
        page(); open(); fireEvent.click(screen.getByRole('button', { name: '生成' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledOnce()); expect(posts()).toHaveLength(0);
        expect(JSON.stringify(vi.mocked(axios.post).mock.calls[0])).toContain('low quality');
    });
});
