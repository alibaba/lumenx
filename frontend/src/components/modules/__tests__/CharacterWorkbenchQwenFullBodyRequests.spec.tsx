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
const models = ['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'];
const negativePlaceholder = 'Enter your negative prompt (avoid unwanted elements)...';
const tooLong = '负向提示词与风格合并后超过 500 个字符，请缩短负向提示词或关闭风格。';
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'a' });
const style = (negative: string, positive = 'soft light') => ({
    style_config: { id: 'saved', name: 'Saved style', positive_prompt: positive, negative_prompt: negative },
});
function snapshot({ model = models[0], source = 'episode', negative, revision = 5, extra = {} as Record<string, unknown>, seriesId = null as string | null } = {} as {
    model?: string; source?: string; negative?: string; revision?: number; extra?: Record<string, unknown>; seriesId?: string | null;
}) {
    return enriched({ ...ack([], 'a', revision), series_id: seriesId, model_settings: { t2i_model: model },
        art_direction: negative === undefined ? null : style(negative),
        characters: [{ id: 'shared', name: 'Alice', description: 'red coat', source,
            full_body_image_url: '/old.png', full_body_asset: { selected_id: 'old', variants: [{ id: 'old', url: '/old.png', is_favorited: true }] }, ...extra }] }, 9, 1);
}
let server: ReturnType<typeof snapshot>;
let series: unknown;
function install(options: Parameters<typeof snapshot>[0] = {}) {
    server = snapshot({ revision: (useProjectStore.getState().currentProject?.revision ?? 3) + 1, ...options });
    useProjectStore.getState().applyProjectReadSnapshot('a', server);
}
function wire(key: string, statuses = ['needs_confirmation'], channel = 'full_body') {
    const root = useProjectStore.getState().currentProject!;
    const source = root.characters[0].source;
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: `studio.asset.${channel}.v1`, request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: 'a',
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: root.series_id ?? null,
        owner_kind: source === 'global' ? 'global' : source === 'series' ? 'series' : 'script',
        owner_id: source === 'global' ? 'library' : source === 'series' ? root.series_id : 'a', asset_type: 'character', asset_id: 'shared', channel,
        members: statuses.map((status, i) => ({ variant_id: `${key}-${i}`, attempt_id: `${key}-${i}`, status,
            ...(status === 'completed' ? { output_ref: `assets/studio/${key}-${i}.png` } : {}) })),
    } };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const projectGets = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'));
const seriesGets = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).includes('/series/'));
const panel = (index = 0) => screen.getAllByRole('group', { name: ['1. 主体资产（全身图）', '2. 三视图', '3. 头像（特写）'][index] }).at(-1)!;
const generate = (index = 0) => within(panel(index)).getByRole('button', { name: '生成' });
const editNegative = (value: string) => fireEvent.change(screen.getByPlaceholderText(negativePlaceholder), { target: { value } });
const editPrompt = (value: string) => fireEvent.change(within(panel()).getByPlaceholderText('Enter prompt description...'), { target: { value } });
function open() { fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Alice' })); }
function page(strict = false) {
    const content = <LightboxProvider><ConsistencyVault /></LightboxProvider>;
    const view = renderWithIntl(strict ? <StrictMode>{content}</StrictMode> : content); open(); return view;
}
function seed(key = 'saved', model = models[0], channel: 'full_body' | 'reference_sheet' = 'full_body') {
    const original = { asset_id: 'shared', batch_size: 2, model_name: model };
    return beginStudioAssetImageRequest({ ...owner(), input: channel === 'full_body'
        ? { ...original, asset_type: 'character', generation_type: 'full_body' } : original, idFactory: () => key }).journal;
}
function queueLock() {
    const gate = deferred<void>(); let entered = false;
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => {
        entered = true; await gate.promise; return callback({});
    } } });
    return { gate, entered: () => entered };
}
function noSubmission() {
    expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); vi.spyOn(window, 'alert').mockImplementation(() => {});
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ currentProject: project(), projects: [project()], currentSeries: null, readWatermarks: {}, generatingTasks: [], runningOps: {} });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    series = { id: 's', revision: 1, art_direction: null };
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : String(url).includes('/series/') ? series : server }));
    vi.mocked(axios.post).mockImplementation(async () => ({ data: ack([], 'a', 6) }));
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        const body = init?.method === 'POST' ? JSON.parse(String(init.body)) : null;
        const key = body?.client_request_id ?? String(url).split('/').at(-1)!;
        return response(wire(key, Array.from({ length: body?.batch_size ?? 2 }, () => 'needs_confirmation')));
    });
    install();
});
afterEach(() => { vi.useRealTimers(); clearRuntimeEnvironment(); });

describe('Qwen full-body batch draft ownership', () => {
    it.each(['change', 'ABA'])('cancels batch %s while waiting for the Web Lock', async change => {
        page(); editNegative('draft'); fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
        const lock = queueLock(); fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
        const four = within(panel()).getByRole('button', { name: 'x4' });
        expect(four).toBeEnabled(); fireEvent.click(four); expect(four).toHaveClass('bg-blue-600');
        if (change === 'ABA') fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
        await act(async () => { lock.gate.resolve(); }); noSubmission(); expect(generate()).toBeEnabled();
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body)).batch_size).toBe(change === 'ABA' ? 2 : 4);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
    it('keeps the pending batch when the already-selected button is clicked', async () => {
        page(); editNegative('draft'); fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
        const lock = queueLock(); fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
        fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
        await act(async () => { lock.gate.resolve(); }); expect(posts()).toHaveLength(1);
        expect(JSON.parse(String(posts()[0][1]?.body)).batch_size).toBe(2);
    });
    it('rejects an exiting full-body batch edit without cancelling the new session', async () => {
        page(); fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
        const exitingPanel = panel(), exitingFour = within(exitingPanel).getByRole('button', { name: 'x4' });
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        expect(document.body.contains(exitingFour)).toBe(true); fireEvent.click(exitingFour);
        expect(exitingFour).not.toHaveClass('bg-blue-600'); open();
        const currentPanel = panel(); expect(currentPanel).not.toBe(exitingPanel);
        fireEvent.click(within(currentPanel).getByRole('button', { name: 'x2' }));
        const lock = queueLock(); fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
        await act(async () => { lock.gate.resolve(); }); expect(posts()).toHaveLength(1);
        expect(JSON.parse(String(posts()[0][1]?.body)).batch_size).toBe(2);
    });
    it('keeps the submitted batch and journal when selecting a later batch', async () => {
        page(); fireEvent.click(within(panel()).getByRole('button', { name: 'x2' })); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1)); await within(panel()).findAllByText('待确认');
        const original = structuredClone(listStudioAssetImageRequests(owner())[0].request);
        fireEvent.click(within(panel()).getByRole('button', { name: 'x4' }));
        expect(listStudioAssetImageRequests(owner())[0].request).toEqual(original); expect(original.batch_size).toBe(2);
        expect(posts()).toHaveLength(1); expect(within(panel()).getAllByText('待确认')).toHaveLength(2);
    });
});

describe('real Vault to Qwen full-body strict requests', () => {
    it.each(models)('retains the default negative and submits one strict batch2 for %s', async model => {
        install({ model }); page();
        const negative = (screen.getByPlaceholderText(negativePlaceholder) as HTMLTextAreaElement).value;
        expect(negative).toContain('bad anatomy'); expect(generate()).toBeEnabled();
        expect(screen.queryByText('此模型不支持负向提示词，请清空后再生成。')).toBeNull();
        editPrompt('my captured full body'); fireEvent.click(within(panel()).getByRole('button', { name: 'x2' })); fireEvent.click(generate());
        await waitFor(() => expect(posts().length + vi.mocked(axios.post).mock.calls.length).toBeGreaterThan(0));
        expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled(); expect(projectGets()).toHaveLength(1);
        expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/);
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ asset_id: 'shared', asset_type: 'character', generation_type: 'full_body',
            model_name: model, prompt: 'my captured full body', batch_size: 2, negative_prompt: negative, apply_style: true });
        expect(await screen.findAllByText('待确认')).toHaveLength(2);
        expect(useProjectStore.getState().currentProject?.characters[0].full_body_asset?.selected_id).toBe('old');
    });
    it.each([500, 501])('validates %s effective negative code points without trimming or truncation', async count => {
        const negative = '😀'.repeat(count - 3); install({ negative: '界' }); page(); editNegative(negative);
        expect(screen.getByPlaceholderText(negativePlaceholder)).toHaveValue(negative);
        expect(generate().hasAttribute('disabled')).toBe(count > 500);
        if (count > 500) { expect(screen.getByText(tooLong)).toBeInTheDocument(); noSubmission(); return; }
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body)).negative_prompt).toBe(negative);
        expect(JSON.parse(String(posts()[0][1]?.body)).style_prompt).toBe('soft light');
    });
    it('keeps explicit empty negative while applying a valid style once', async () => {
        install({ negative: 'no blur' }); page(); editNegative(''); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ negative_prompt: '', apply_style: true, style_prompt: 'soft light' });
        expect(screen.queryByText('此模型不支持负向提示词，请关闭风格提示词或更换模型。')).toBeNull();
    });
    it('requires an explicit style-off choice and preserves the authored negative', async () => {
        install({ negative: 'x'.repeat(501) }); page(); editNegative('keep my negative');
        expect(generate()).toBeDisabled(); expect(screen.getByText(tooLong)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('checkbox')); expect(generate()).toBeEnabled(); noSubmission();
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ negative_prompt: 'keep my negative', apply_style: false, style_prompt: '' });
    });
    it.each(['episode', 'series', 'global'])('retains the project root and fresh %s owner', async source => {
        install({ source, seriesId: source === 'series' ? 's' : null }); page(); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1)); await screen.findByText('待确认');
        expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/);
        expect(listStudioAssetImageRequests(owner())[0].receiptIdentity).not.toBeNull(); expect(axios.post).not.toHaveBeenCalled();
    });
    it('does not treat uploaded full-body output as a T2I reference input', async () => {
        install({ extra: { full_body_asset: { selected_id: 'old', variants: [{ id: 'old', url: '/old.png', is_uploaded_source: true }] } } });
        page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); expect(axios.post).not.toHaveBeenCalled();
        expect(JSON.parse(String(posts()[0][1]?.body))).not.toHaveProperty('reference_image_url');
    });
    it.each([{ base_character_id: 'base' }, { three_view_asset: { variants: [{ id: 'u', url: '/upload.png', is_uploaded_source: true }] } },
        { headshot_asset: { variants: [{ id: 'u', url: '/upload.png', is_uploaded_source: true }] } }])('keeps reference-dependent generation outside strict T2I %j', async extra => {
        install({ extra }); page(); fireEvent.click(generate());
        await waitFor(() => expect(axios.post).toHaveBeenCalledOnce()); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ generation_type: 'full_body', negative_prompt: expect.stringContaining('bad anatomy') });
    });
    it.each([[1, 'three_view'], [2, 'headshot']] as const)('preserves sibling panel %s as legacy %s', async (index, channel) => {
        page(); fireEvent.click(generate(index)); await waitFor(() => expect(axios.post).toHaveBeenCalledOnce()); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ generation_type: channel });
    });
    it('uses freshly read project style and sends only the authored negative', async () => {
        page(); editNegative('my negative'); server = snapshot({ negative: 'fresh negative', revision: 6 });
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ style_prompt: 'soft light', negative_prompt: 'my negative' });
    });
    it('blocks a fresh project negative that makes the effective value exceed 500', async () => {
        page(); editNegative('keep'); server = snapshot({ negative: 'x'.repeat(500), revision: 6 }); fireEvent.click(generate());
        await screen.findByText(tooLong); noSubmission(); expect(screen.getByPlaceholderText(negativePlaceholder)).toHaveValue('keep');
    });
    it('reads the root series style even for a global asset and ignores stale cached style', async () => {
        install({ source: 'global', seriesId: 's' });
        useProjectStore.setState({ currentSeries: { id: 's', revision: 0, art_direction: style('x'.repeat(501)) } as unknown as NonNullable<ReturnType<typeof useProjectStore.getState>['currentSeries']> });
        series = { id: 's', revision: 1, art_direction: style('current series negative', 'current series positive') };
        page(); expect(generate()).toBeEnabled(); editNegative('my negative'); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(seriesGets()).toHaveLength(1);
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ style_prompt: 'current series positive', negative_prompt: 'my negative' });
    });
    it('blocks a newly read inherited series negative and allows explicit style-off', async () => {
        install({ source: 'global', seriesId: 's' }); page(); editNegative('keep');
        series = { id: 's', revision: 1, art_direction: style('x'.repeat(500)) }; fireEvent.click(generate());
        await screen.findByText(tooLong); noSubmission();
        fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ apply_style: false, negative_prompt: 'keep', style_prompt: '' });
        expect(seriesGets()).toHaveLength(1);
    });
    it.each([{ id: 'other', revision: 1 }, { id: 's', revision: 2 }, { id: 's' }])('does not infer a negative-free series from invalid evidence %j', async invalid => {
        install({ seriesId: 's' }); page(); series = invalid; fireEvent.click(generate());
        await screen.findByText('无法确认最新素材，请重新打开项目后重试'); noSubmission();
    });
    it.each([{ source: 'global' }, { model: 'qwen-image-2.0-pro' }, { extra: { base_character_id: 'new' } },
        { extra: { headshot_asset: { variants: [{ is_uploaded_source: true }] } } }])('rejects fresh owner/model/reference changes without fallback %j', async change => {
        page(); server = snapshot({ revision: 6, ...change }); fireEvent.click(generate());
        await waitFor(() => expect(projectGets()).toHaveLength(1)); await act(async () => {}); noSubmission();
        expect(screen.getByText('无法确认最新素材，请重新打开项目后重试')).toBeInTheDocument();
    });
    it.each(['close', 'environment ABA', 'owner ABA'])('invalidates an old preflight after %s', async action => {
        page(); const read = deferred<{ data: ReturnType<typeof snapshot> }>(); vi.mocked(axios.get).mockReturnValueOnce(read.promise);
        fireEvent.click(generate()); await waitFor(() => expect(projectGets()).toHaveLength(1));
        if (action === 'close') fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        else if (action === 'environment ABA') act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        else {
            const original = useProjectStore.getState().currentProject!;
            act(() => { useProjectStore.setState({ currentProject: { ...original, id: 'other' } }); });
            act(() => { useProjectStore.setState({ currentProject: original }); }); open();
        }
        await act(async () => { read.resolve({ data: snapshot({ revision: 6 }) }); }); noSubmission();
    });
    it.each(['negative', 'prompt', 'style', 'style ABA'])('invalidates authored %s edits while a strict lock is queued', async change => {
        const lock = queueLock(); page(); editNegative('original'); editPrompt('original'); fireEvent.click(generate());
        await waitFor(() => expect(lock.entered()).toBe(true));
        if (change === 'negative') editNegative('new negative');
        else if (change === 'prompt') editPrompt('new prompt');
        else { fireEvent.click(screen.getByRole('checkbox')); if (change === 'style ABA') fireEvent.click(screen.getByRole('checkbox')); }
        await act(async () => { lock.gate.resolve(); }); noSubmission();
        expect(generate()).toBeEnabled();
    });
    it.each(['over limit', 'valid changed style'])('rechecks %s after the browser lock wait', async change => {
        const lock = queueLock(); page(); editNegative('keep'); fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
        act(() => { install({ revision: 6, negative: change === 'over limit' ? 'x'.repeat(500) : 'new legal style' }); });
        await act(async () => { lock.gate.resolve(); }); noSubmission();
        if (change === 'over limit') expect(screen.getByText(tooLong)).toBeInTheDocument();
    });
    it.each(['lost ACK', '503'])('keeps the original journal after %s and only GETs on reopen', async failure => {
        vi.mocked(appAccessFetch).mockImplementationOnce(async () => { if (failure === '503') return response({ detail: 'unavailable' }, 503); throw new Error('lost ACK'); });
        const view = page(); fireEvent.click(within(panel()).getByRole('button', { name: 'x2' })); fireEvent.click(generate());
        await screen.findByRole('region', { name: '生成请求结果' }); const journal = listStudioAssetImageRequests(owner())[0];
        const request = structuredClone(journal.request); expect(posts()).toHaveLength(1); expect(screen.getAllByText('待确认')).toHaveLength(2);
        view.unmount(); page(); await waitFor(() => expect(gets()).toHaveLength(1));
        expect(String(gets()[0][0])).toContain(request.client_request_id); expect(listStudioAssetImageRequests(owner())[0].request).toEqual(request);
        fireEvent.click(await screen.findByRole('button', { name: '查看生成结果' })); await waitFor(() => expect(gets()).toHaveLength(2));
        expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['wan2.7-image', models[1]])('recovers saved %s full-body identity without reading a sibling journal or rebinding the model', async savedModel => {
        const saved = seed('full', savedModel); seed('reference', models[1], 'reference_sheet'); page(true);
        await waitFor(() => expect(gets().length).toBeGreaterThan(0)); expect(gets().every(([url]) => String(url).endsWith('/full'))).toBe(true);
        expect(listStudioAssetImageRequests(owner()).find(item => item.request.client_request_id === 'full')?.request).toEqual(saved.request);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(2); expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['environment', 'owner'])('ignores a late receipt after %s ABA and only displays the new read', async change => {
        seed(); const old = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValueOnce(old.promise); page();
        await waitFor(() => expect(gets()).toHaveLength(1));
        if (change === 'environment') act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        else {
            const original = useProjectStore.getState().currentProject!;
            act(() => { useProjectStore.setState({ currentProject: { ...original, id: 'other' } }); });
            act(() => { useProjectStore.setState({ currentProject: original }); }); open();
        }
        // An exiting workbench may also recover its own journal; every read must keep the original identity.
        await waitFor(() => expect(gets().length).toBeGreaterThanOrEqual(2));
        expect(gets().every(([url]) => String(url).endsWith('/projects/a/asset_image_requests/saved'))).toBe(true);
        await within(panel()).findAllByText('待确认');
        await act(async () => { old.resolve(response(wire('saved', ['completed', 'completed']))); });
        expect(within(panel()).queryByText('已完成')).toBeNull(); expect(within(panel()).getAllByText('待确认')).toHaveLength(2);
        expect(projectGets()).toHaveLength(0); expect(posts()).toHaveLength(0);
    });
});

describe('GPT reference-free full-body uses the existing author request path', () => {
    it.each(['gpt-image-2', 'gpt-image/gpt-image-2#image'])(
        'requires clearing own negative but keeps inherited style for %s', async model => {
            install({ model, negative: 'saved inherited negative' }); page();
            expect((screen.getByPlaceholderText(negativePlaceholder) as HTMLTextAreaElement).value).toContain('bad anatomy');
            expect(generate()).toBeDisabled(); noSubmission();
            fireEvent.click(screen.getByRole('button', { name: '清空负向提示词' }));
            expect(screen.getByPlaceholderText(negativePlaceholder)).toHaveValue('');
            expect(screen.getByRole('checkbox')).toBeChecked(); expect(generate()).toBeEnabled();
            expect(screen.queryByRole('button', { name: '本次不使用风格提示词' })).toBeNull();
            editPrompt('my GPT full body'); fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
            server = snapshot({ model, negative: 'fresh inherited negative', revision: 7 });
            server.art_direction!.style_config.positive_prompt = 'fresh positive';
            fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
            const raw = JSON.parse(String(posts()[0][1]?.body));
            expect(raw).toMatchObject({ asset_type: 'character', generation_type: 'full_body', model_name: model,
                prompt: 'my GPT full body', batch_size: 2, negative_prompt: '', apply_style: true, style_prompt: 'fresh positive' });
            for (const field of ['reference_variant_id', 'reference_image_url', 'aspect_ratio', 'seed', 'watermark']) expect(raw).not.toHaveProperty(field);
            expect(axios.post).not.toHaveBeenCalled();
            expect(useProjectStore.getState().currentProject?.art_direction?.style_config.negative_prompt).toBe('fresh inherited negative');
            expect(useProjectStore.getState().currentProject?.characters[0].full_body_asset?.selected_id).toBe('old');
        },
    );
    it.each(['draft ABA', 'owner', 'saved ratio', 'environment ABA'])(
        'cancels GPT full-body %s while the original browser lock is held', async change => {
            install({ model: 'gpt-image-2' }); page(); editNegative(''); editPrompt('original draft');
            const lock = queueLock();
            try {
                fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
                act(() => {
                    if (change === 'draft ABA') { editPrompt('temporary draft'); editPrompt('original draft'); }
                    else if (change === 'environment ABA') {
                        activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' });
                        activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
                    } else if (change === 'owner') install({ model: 'gpt-image-2', source: 'global', revision: 8 });
                    else {
                        const current = useProjectStore.getState().currentProject!;
                        useProjectStore.setState({ currentProject: { ...current,
                            model_settings: { ...current.model_settings!, character_aspect_ratio: '1:1' } } });
                    }
                });
            } finally { await act(async () => { lock.gate.resolve(); }); }
            noSubmission();
        },
    );
    it('recovers the original unknown GPT full-body raw under Qwen with GET only', async () => {
        install({ model: 'gpt-image-2' });
        vi.mocked(appAccessFetch).mockRejectedValueOnce(Error('lost ACK'));
        const view = page(); editNegative(''); fireEvent.click(within(panel()).getByRole('button', { name: 'x2' })); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        const original = structuredClone(listStudioAssetImageRequests(owner())[0].request);
        view.unmount(); install({ model: models[0] }); page();
        await waitFor(() => expect(gets().length).toBeGreaterThan(0));
        expect(gets().every(([url]) => String(url).endsWith(`/${original.client_request_id}`))).toBe(true);
        expect(listStudioAssetImageRequests(owner())[0].request).toEqual(original);
        expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
});
