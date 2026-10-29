import { StrictMode } from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
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
const models = ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'];
const negativePlaceholder = 'Enter your negative prompt (avoid unwanted elements)...';
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'a' });
function snapshot({ model = 'wan2.7-image', source = 'episode', negative = '', revision = 5, extra = {} as Record<string, unknown>, seriesId = 's' } = {}) {
    return enriched({ ...ack([], 'a', revision), series_id: source === 'series' ? seriesId : null,
        model_settings: { t2i_model: model },
        art_direction: negative ? { style_config: { name: 'Saved style', positive_prompt: 'soft light', negative_prompt: negative } } : null,
        characters: [{ id: 'shared', name: 'Alice', description: 'red coat', source,
            full_body_image_url: '/old.png', full_body_asset: { selected_id: 'old', variants: [{ id: 'old', url: '/old.png' }] }, ...extra }] }, 9, 1);
}
function install(options: Parameters<typeof snapshot>[0] = {}) {
    const value = snapshot({ revision: (useProjectStore.getState().currentProject?.revision ?? 3) + 1, ...options });
    useProjectStore.getState().applyProjectReadSnapshot('a', value);
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? []
        : String(url).includes('/series/') ? { id: value.series_id, revision: 1, art_direction: null } : value }));
    return value;
}
function wire(key: string, statuses = ['needs_confirmation'], channel = 'full_body') {
    const project = useProjectStore.getState().currentProject!;
    const source = project.characters[0].source;
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: `studio.asset.${channel}.v1`, request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: 'a',
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: project.series_id ?? null,
        owner_kind: source === 'global' ? 'global' : source === 'series' ? 'series' : 'script',
        owner_id: source === 'global' ? 'library' : source === 'series' ? project.series_id : 'a',
        asset_type: 'character', asset_id: 'shared', channel,
        members: statuses.map((status, i) => ({ variant_id: `${key}-${i}`, attempt_id: `${key}-${i}`, status,
            ...(status === 'completed' ? { output_ref: `assets/studio/${key}-${i}.png` } : {}) })),
    } };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const projectGets = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'));
const panel = (index = 0) => screen.getAllByRole('group', { name: ['1. 主体资产（全身图）', '2. 三视图', '3. 头像（特写）'][index] }).at(-1)!;
const generate = (index = 0) => within(panel(index)).getByRole('button', { name: '生成' });
const clearNegative = () => fireEvent.click(screen.getByRole('button', { name: '清空负向提示词' }));
function open() { fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Alice' })); }
function page() { const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>); open(); return view; }
function seed(key: string, channel: 'full_body' | 'reference_sheet' = 'full_body') {
    return beginStudioAssetImageRequest({ ...owner(), input: channel === 'full_body'
        ? { asset_id: 'shared', asset_type: 'character', generation_type: 'full_body', batch_size: 2 }
        : { asset_id: 'shared', batch_size: 2 }, idFactory: () => key }).journal;
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); vi.spyOn(window, 'alert').mockImplementation(() => {});
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ currentProject: project(), projects: [project()], currentSeries: null, readWatermarks: {}, generatingTasks: [], runningOps: {} });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        const body = init?.method === 'POST' ? JSON.parse(String(init.body)) : null;
        const key = body?.client_request_id ?? String(url).split('/').at(-1)!;
        return response(wire(key, Array.from({ length: body?.batch_size ?? 2 }, () => 'needs_confirmation')));
    });
    install();
});
afterEach(() => { vi.useRealTimers(); clearRuntimeEnvironment(); });

describe('real Vault to CharacterWorkbench full-body generation', () => {
    it('shows the captured batch size through preflight, POST and a partially finished receipt', async () => {
        page(); clearNegative();
        const read = deferred<{ data: ReturnType<typeof snapshot> }>();
        const submitted = deferred<Response>();
        vi.mocked(axios.get).mockReturnValueOnce(read.promise);
        vi.mocked(appAccessFetch).mockReturnValueOnce(submitted.promise);
        fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
        fireEvent.click(generate());
        expect(within(panel()).getByText('正在生成 2 个变体...')).toBeInTheDocument();
        await act(async () => { read.resolve({ data: snapshot({ revision: 6 }) }); });
        await waitFor(() => expect(posts()).toHaveLength(1));
        fireEvent.click(within(panel()).getByRole('button', { name: 'x4' }));
        expect(within(panel()).getByText('正在生成 2 个变体...')).toBeInTheDocument();
        const key = JSON.parse(String(posts()[0][1]?.body)).client_request_id;
        await act(async () => { submitted.resolve(response(wire(key, ['completed', 'processing']))); });
        expect(within(panel()).getByText('正在生成 2 个变体...')).toBeInTheDocument();
        expect(within(panel()).queryByText('正在生成 1 个变体...')).toBeNull();
        expect(posts()).toHaveLength(1);
    });
    it('restores the original pending batch size from its journal on reopen without another POST', async () => {
        seed('pending-full');
        vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('pending-full', ['completed', 'processing'])));
        page();
        expect(await within(panel()).findByText('正在生成 2 个变体...')).toBeInTheDocument();
        expect(posts()).toHaveLength(0);
        fireEvent.click(screen.getByRole('button', { name: '关闭' })); open();
        await waitFor(() => expect(gets()).toHaveLength(2));
        expect(within(panel()).getByText('正在生成 2 个变体...')).toBeInTheDocument();
        expect(posts()).toHaveLength(0);
    });
    it('does not retain a closed preflight batch count in the next workbench session', async () => {
        page(); clearNegative();
        const read = deferred<{ data: ReturnType<typeof snapshot> }>();
        vi.mocked(axios.get).mockReturnValueOnce(read.promise);
        fireEvent.click(within(panel()).getByRole('button', { name: 'x3' })); fireEvent.click(generate());
        expect(within(panel()).getByText('正在生成 3 个变体...')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '关闭' })); open();
        expect(within(panel()).queryByText(/正在生成/)).toBeNull();
        await act(async () => { read.resolve({ data: snapshot({ revision: 6 }) }); });
        expect(within(panel()).queryByText(/正在生成/)).toBeNull(); expect(posts()).toHaveLength(0);
    });
    it.each(models)('requires explicit negative clearing, then submits one strict full-body request for %s', async model => {
        install({ model }); page();
        expect((screen.getByPlaceholderText(negativePlaceholder) as HTMLTextAreaElement).value).toContain('bad anatomy');
        expect(generate()).toBeDisabled();
        expect(screen.getByText('此模型不支持负向提示词，请清空后再生成。')).toBeInTheDocument();
        clearNegative(); expect(screen.getByPlaceholderText(negativePlaceholder)).toHaveValue('');
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        fireEvent.change(screen.getAllByPlaceholderText('Enter prompt description...')[0], { target: { value: 'my captured full body' } });
        fireEvent.click(screen.getAllByRole('button', { name: 'x2' })[0]); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(projectGets()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
        expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/);
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ asset_id: 'shared', asset_type: 'character', generation_type: 'full_body',
            model_name: model, prompt: 'my captured full body', batch_size: 2, negative_prompt: '', apply_style: true });
        expect(await screen.findAllByText('待确认')).toHaveLength(2);
        expect(useProjectStore.getState().currentProject?.characters[0].full_body_asset?.selected_id).toBe('old');
    });
    it.each(['episode', 'series', 'global'])('keeps the project request root and fresh %s ownership', async source => {
        install({ source }); page(); clearNegative(); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/);
        expect(listStudioAssetImageRequests(owner())[0].request.generation_type).toBe('full_body');
    });
    it('does not exclude an uploaded full-body image', async () => {
        install({ extra: { full_body_asset: { selected_id: 'old', variants: [{ id: 'old', url: '/old.png', is_uploaded_source: true }] } } });
        page(); clearNegative(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(axios.post).not.toHaveBeenCalled();
    });
    it.each([{ base_character_id: 'base' }, { three_view_asset: { variants: [{ id: 'uploaded', url: '/upload.png', is_uploaded_source: true }] } },
        { headshot_asset: { variants: [{ id: 'uploaded', url: '/upload.png', is_uploaded_source: true }] } }])('preserves reference-dependent legacy behavior %j', async extra => {
        install({ extra }); vi.mocked(axios.post).mockResolvedValue({ data: ack([], 'a', 5) }); page();
        expect(screen.queryByRole('button', { name: '清空负向提示词' })).toBeNull(); fireEvent.click(generate());
        await waitFor(() => expect(axios.post).toHaveBeenCalledOnce()); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ generation_type: 'full_body', negative_prompt: expect.stringContaining('bad anatomy') });
    });
    it.each(['wan2.6-t2i'])('preserves the existing legacy path for unmigrated %s', async model => {
        install({ model }); vi.mocked(axios.post).mockResolvedValue({ data: ack([], 'a', 5) }); page(); fireEvent.click(generate());
        await waitFor(() => expect(axios.post).toHaveBeenCalledOnce()); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ model_name: model, generation_type: 'full_body', negative_prompt: expect.stringContaining('bad anatomy') });
    });
    it.each([[1, 'three_view'], [2, 'headshot']] as const)('preserves sibling channel %s as %s', async (index, channel) => {
        vi.mocked(axios.post).mockResolvedValue({ data: ack([], 'a', 5) }); page(); fireEvent.click(generate(index));
        await waitFor(() => expect(axios.post).toHaveBeenCalledOnce()); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ generation_type: channel, negative_prompt: expect.stringContaining('bad anatomy') });
    });
    it('keeps an unsupported style until explicit disable and never silently drops its negative', async () => {
        install({ negative: 'no text' }); page(); clearNegative(); expect(generate()).toBeDisabled();
        expect(screen.getByText('此模型不支持负向提示词，请关闭风格提示词或更换模型。')).toBeInTheDocument();
        expect(screen.getByRole('checkbox')).toBeChecked();
        fireEvent.click(screen.getByRole('button', { name: '本次不使用风格提示词' }));
        expect(screen.getByRole('checkbox')).not.toBeChecked(); expect(posts()).toHaveLength(0);
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ apply_style: false, negative_prompt: '' });
    });
    it('rechecks fresh inherited negatives before submitting while retaining the user clear', async () => {
        page(); clearNegative(); vi.mocked(axios.get).mockResolvedValue({ data: snapshot({ negative: 'new unsupported negative', revision: 6 }) });
        fireEvent.click(generate());
        await screen.findByText('此模型不支持负向提示词，请关闭风格提示词或更换模型。');
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        expect(screen.getByPlaceholderText(negativePlaceholder)).toHaveValue(''); expect(screen.getByRole('checkbox')).toBeChecked();
    });
    it('reads the captured series baseline before submission and requires explicit disabling of a fresh inherited negative', async () => {
        install({ source: 'series' }); page(); clearNegative();
        const fresh = snapshot({ source: 'series', revision: 6 });
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).includes('/series/')
            ? { id: 's', revision: 1, art_direction: { style_config: { negative_prompt: 'series negative', positive_prompt: 'series positive' } } } : fresh }));
        fireEvent.click(generate());
        await screen.findByText('此模型不支持负向提示词，请关闭风格提示词或更换模型。');
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: '本次不使用风格提示词' })); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body)).apply_style).toBe(false);
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).includes('/series/'))).toHaveLength(1);
    });
    it.each([{ id: 'other', revision: 1 }, { id: 's', revision: 2 }, { id: 's' }])('does not guess a negative-free style from invalid series evidence %j', async series => {
        install({ source: 'series' }); page(); clearNegative();
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).includes('/series/') ? series : snapshot({ source: 'series', revision: 6 }) }));
        fireEvent.click(generate()); await screen.findByText('无法确认最新素材，请重新打开项目后重试');
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
    });
    it.each([{ source: 'global' }, { model: 'qwen-image-2.0' }, { extra: { base_character_id: 'new-base' } },
        { extra: { headshot_asset: { variants: [{ is_uploaded_source: true }] } } }])('rejects a fresh changed owner/model/eligibility without fallback %j', async change => {
        page(); clearNegative(); vi.mocked(axios.get).mockResolvedValue({ data: snapshot({ revision: 6, ...change }) });
        fireEvent.click(generate()); await waitFor(() => expect(projectGets()).toHaveLength(1)); await act(async () => {});
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
        expect(screen.getByText('无法确认最新素材，请重新打开项目后重试')).toBeInTheDocument();
    });
    it('allows only one in-flight preflight and submits the captured prompt after the fresh read', async () => {
        page(); clearNegative(); const read = deferred<{ data: ReturnType<typeof snapshot> }>(); vi.mocked(axios.get).mockReturnValue(read.promise);
        fireEvent.change(screen.getAllByPlaceholderText('Enter prompt description...')[0], { target: { value: 'clicked draft' } });
        fireEvent.click(generate()); fireEvent.click(generate()); await waitFor(() => expect(projectGets()).toHaveLength(1));
        await act(async () => { read.resolve({ data: snapshot({ revision: 6, extra: { full_body_prompt: 'later server prompt' } }) }); });
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body)).prompt).toBe('clicked draft');
    });
    it.each(['close', 'environment ABA'])('does not submit a preflight finishing after %s', async action => {
        page(); clearNegative(); const read = deferred<{ data: ReturnType<typeof snapshot> }>(); vi.mocked(axios.get).mockReturnValue(read.promise);
        fireEvent.click(generate()); await waitFor(() => expect(projectGets()).toHaveLength(1));
        if (action === 'close') fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        else act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        await act(async () => { read.resolve({ data: snapshot({ revision: 6 }) }); });
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
    });
    it('gives a rapid close and reopen a new session while the old preflight is unresolved', async () => {
        page(); clearNegative(); const old = deferred<{ data: ReturnType<typeof snapshot> }>();
        vi.mocked(axios.get).mockReturnValueOnce(old.promise); fireEvent.click(generate());
        await waitFor(() => expect(projectGets()).toHaveLength(1));
        fireEvent.click(screen.getByRole('button', { name: '关闭' })); open();
        await act(async () => { old.resolve({ data: snapshot({ revision: 6 }) }); });
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(within(panel()).getByRole('button', { name: '清空负向提示词' }));
        fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
    });
    it('invalidates a pending series read on close before it can change store state or submit', async () => {
        install({ source: 'series' }); page(); clearNegative(); const old = deferred<{ data: unknown }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).includes('/series/') ? old.promise : Promise.resolve({ data: snapshot({ source: 'series', revision: 6 }) }));
        fireEvent.click(generate());
        await waitFor(() => expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).includes('/series/'))).toHaveLength(1));
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        await act(async () => { old.resolve({ data: { id: 's', revision: 1, art_direction: { style_config: { negative_prompt: 'late' } } } }); });
        expect(useProjectStore.getState().currentSeries).toBeNull(); expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
    });
    it('keeps one submitted identity across React StrictMode effect replay', async () => {
        renderWithIntl(<StrictMode><LightboxProvider><ConsistencyVault /></LightboxProvider></StrictMode>); open(); clearNegative(); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1)); await screen.findByText('待确认');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
    it('does not POST when the workbench closes while its browser lock callback is still queued', async () => {
        const lock = deferred<void>();
        let enter!: (lock: object) => Promise<unknown>;
        Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: typeof enter) => {
            enter = callback; await lock.promise; return callback({});
        } } });
        page(); clearNegative(); fireEvent.click(generate()); await waitFor(() => expect(enter).toBeDefined());
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        await act(async () => { lock.resolve(); });
        expect(posts()).toHaveLength(0); expect(listStudioAssetImageRequests(owner())).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
    });
    it('rechecks newly inherited negatives immediately before a queued lock can POST', async () => {
        const lock = deferred<void>(); let entered = false;
        Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => {
            entered = true; await lock.promise; return callback({});
        } } });
        page(); clearNegative(); fireEvent.click(generate()); await waitFor(() => expect(entered).toBe(true));
        act(() => { useProjectStore.getState().applyProjectReadSnapshot('a', snapshot({ negative: 'changed during browser scheduling', revision: 7 })); });
        await act(async () => { lock.resolve(); });
        expect(posts()).toHaveLength(0); expect(listStudioAssetImageRequests(owner())).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        expect(screen.getByText('此模型不支持负向提示词，请关闭风格提示词或更换模型。')).toBeInTheDocument();
    });
    it.each(['lost ACK', 'rejected'])('retains an unknown journal after %s and only GETs on reopen', async failure => {
        vi.mocked(appAccessFetch).mockImplementationOnce(async () => { if (failure === 'rejected') return response({}, 409); throw new Error('lost ACK'); });
        const view = page(); clearNegative(); fireEvent.click(screen.getAllByRole('button', { name: 'x2' })[0]); fireEvent.click(generate());
        await screen.findByRole('region', { name: '生成请求结果' }); expect(screen.getAllByText('待确认')).toHaveLength(2);
        const key = listStudioAssetImageRequests(owner())[0].request.client_request_id;
        view.unmount(); page(); await waitFor(() => expect(gets()).toHaveLength(1));
        expect(String(gets()[0][0])).toContain(key); expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(await screen.findByRole('button', { name: '查看生成结果' })); await waitFor(() => expect(gets()).toHaveLength(2));
        expect(posts()).toHaveLength(1);
    });
    it('shows only full-body history, keeps partial members, and preserves current selection until readback', async () => {
        seed('full'); seed('reference', 'reference_sheet');
        vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('full', ['completed', 'needs_confirmation'])));
        page(); const results = await screen.findByRole('region', { name: '生成请求结果' });
        await waitFor(() => expect(projectGets()).toHaveLength(1));
        expect(within(results).getByText('已完成')).toBeInTheDocument(); expect(within(results).getByText('待确认')).toBeInTheDocument();
        expect(gets()).toHaveLength(1); expect(String(gets()[0][0])).toMatch(/\/full$/);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(2); expect(posts()).toHaveLength(0);
        expect(useProjectStore.getState().currentProject?.characters[0].full_body_asset?.selected_id).toBe('old');
    });
    it.each([false, true])('resolves terminal full-body history only after a valid owner readback; failure=%s', async failure => {
        seed('full'); vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('full', ['completed', 'cancelled'])));
        if (failure) vi.mocked(axios.get).mockRejectedValue(new Error('offline'));
        else {
            const variants = [{ id: 'old', url: '/old.png' }, { id: 'full-0', url: 'assets/studio/full-0.png' }];
            const confirmed = snapshot({ revision: 6, extra: {
                full_body_request_id: wire('full').request.id,
                full_body_asset: { selected_id: 'old', variants },
                full_body: { selected_image_id: 'old', image_variants: variants },
            } });
            vi.mocked(axios.get).mockResolvedValue({ data: confirmed });
        }
        page(); await screen.findByRole('region', { name: '生成请求结果' }); await act(async () => {});
        expect(listStudioAssetImageRequests(owner())).toHaveLength(failure ? 1 : 0); expect(posts()).toHaveLength(0);
        if (failure) expect(screen.getByText('结果已保存，但最新素材读取失败，请再次查看。')).toBeInTheDocument();
    });
});
