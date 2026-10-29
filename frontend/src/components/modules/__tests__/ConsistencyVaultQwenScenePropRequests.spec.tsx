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
const models = ['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'];
const owner = (id = 'a') => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: id });
function snapshot({ id = 'a', revision = 5, source = 'global', seriesId = null as string | null, seriesRevision = 1, model = 'qwen-image-2.0', negative = '', library = 9 } = {}) {
    const asset = (kind: string) => ({ id: 'shared', name: `${kind} ${id}`, description: `${kind} description`, source,
        image_url: '/old.png', image_asset: { selected_id: 'old', variants: [{ id: 'old', url: '/old.png', is_favorited: true }, { id: 'other', url: '/other.png' }] },
        video_prompt: 'keep video', video_assets: [{ id: 'video', video_url: '/keep.mp4', status: 'completed' }] });
    return enriched({ ...ack([], id, revision), series_id: source === 'series' ? seriesId ?? 'series-a' : seriesId,
        model_settings: { t2i_model: model },
        art_direction: negative ? { style_config: { name: 'Saved', positive_prompt: 'soft light', negative_prompt: negative } } : null,
        characters: [], scenes: [asset('scene')], props: [asset('prop')] }, library, seriesRevision);
}
let server: ReturnType<typeof snapshot>;
let series: { id: string; revision: number; art_direction: ReturnType<typeof snapshot>['art_direction'] };
function install(options: Parameters<typeof snapshot>[0] = {}) {
    const value = snapshot({ revision: (useProjectStore.getState().currentProject?.revision ?? 3) + 1, ...options });
    useProjectStore.getState().applyProjectReadSnapshot(value.id, value);
    server = value;
    return value;
}
function wire(kind: Kind, key: string, statuses = ['needs_confirmation'], root = 'a') {
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: true, reason: null, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: 'studio.asset.scene_prop.v1', request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: root,
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: server?.series_id ?? null,
        owner_kind: 'global', owner_id: 'library', asset_type: kind, asset_id: 'shared', channel: 'image',
        members: statuses.map((status, i) => ({ variant_id: `${key}-${i}`, attempt_id: `${key}-${i}`, status, reason: null,
            ...(status === 'completed' ? { output_ref: `assets/studio/${key}-${i}.png` } : {}) })),
    } };
}
const tooLong = '本次与风格合并后的负向提示词超过 500 个字符，请缩短负向提示词或关闭风格。';
const body = () => JSON.parse(String(posts()[0]?.[1]?.body));
const generate = () => screen.getByRole('button', { name: '生成' });
const editNegative = (value: string) => { if (!screen.queryByPlaceholderText('Enter negative prompt...')) fireEvent.click(screen.getByRole('button', { name: /Advanced Settings/ })); fireEvent.change(screen.getByPlaceholderText('Enter negative prompt...'), { target: { value } }); };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const element = () => <LightboxProvider><ConsistencyVault /></LightboxProvider>;
function page(kind: Kind = 'scene') { const view = renderWithIntl(element()); tab(kind); return view; }
const tab = (kind: Kind) => fireEvent.click(screen.getByRole('button', { name: new RegExp(kind === 'scene' ? 'Scenes' : 'Props') }));
const open = (kind: Kind = 'scene', id = 'a') => fireEvent.click(screen.getByRole('heading', { level: 3, name: `${kind} ${id}` }));
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const seed = (kind: Kind, key: string = kind, id = 'a') => beginStudioAssetImageRequest({ ...owner(id), input: { asset_id: 'shared', asset_type: kind, generation_type: 'all', batch_size: 2 }, idFactory: () => key });

function lockQueue() {
    let release!: () => Promise<unknown>;
    const request = vi.fn((_name: string, _options: unknown, callback: (lock: object) => unknown) => new Promise((resolve, reject) => {
        release = async () => { try { resolve(await callback({})); } catch (error) { reject(error); } };
    }));
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request } });
    return { request, release: () => release() };
}
function noSubmission() { expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(0); }
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); series = { id: 's', revision: 1, art_direction: null };
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).includes('/series/') ? series : server })); vi.spyOn(window, 'alert').mockImplementation(() => {});
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ projects: [project(), project('b')], currentProject: project(), readWatermarks: {}, currentSeries: null, generatingTasks: [] });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        const request = init?.method === 'POST' ? JSON.parse(String(init.body)) : null;
        const key = request?.client_request_id ?? String(url).split('/').at(-1)!;
        return response(wire(request?.asset_type ?? key, key, Array.from({ length: request?.batch_size ?? 2 }, () => 'needs_confirmation')));
    });
    install();
});
afterEach(() => { vi.useRealTimers(); clearRuntimeEnvironment(); });

describe('Vault Qwen batch draft ownership', () => {
    it.each(kinds.flatMap(kind => ['change', 'ABA'].map(change => [kind, change] as const)))('cancels %s batch %s while waiting for the Web Lock', async (kind, change) => {
        page(kind); open(kind); editNegative('draft');
        fireEvent.click(screen.getByRole('button', { name: 'x2' }));
        const lock = lockQueue(); fireEvent.click(generate());
        await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        const four = screen.getByRole('button', { name: 'x4' });
        expect(four).toBeEnabled(); fireEvent.click(four); expect(four).toHaveClass('bg-blue-600');
        if (change === 'ABA') fireEvent.click(screen.getByRole('button', { name: 'x2' }));
        await act(async () => { await lock.release(); }); noSubmission();
        expect(generate()).toBeEnabled();
        fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledTimes(2));
        await act(async () => { await lock.release(); });
        expect(posts()).toHaveLength(1); expect(body().batch_size).toBe(change === 'ABA' ? 2 : 4);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
    it('keeps the pending batch when the already-selected button is clicked', async () => {
        page(); open(); editNegative('draft'); fireEvent.click(screen.getByRole('button', { name: 'x2' }));
        const lock = lockQueue(); fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        fireEvent.click(screen.getByRole('button', { name: 'x2' }));
        await act(async () => { await lock.release(); }); expect(posts()).toHaveLength(1); expect(body().batch_size).toBe(2);
    });
    it('rejects a batch edit from the exiting detail DOM before the same detail reopens', async () => {
        page(); open(); editNegative('draft'); fireEvent.click(screen.getByRole('button', { name: 'x2' }));
        const exitingFour = screen.getByRole('button', { name: 'x4' });
        fireEvent.click(screen.getByRole('button', { name: 'Done' }));
        expect(document.body.contains(exitingFour)).toBe(true); fireEvent.click(exitingFour); open();
        expect(screen.getByRole('button', { name: 'x2' })).toHaveClass('bg-blue-600');
        const lock = lockQueue(); fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        await act(async () => { await lock.release(); }); expect(posts()).toHaveLength(1); expect(body().batch_size).toBe(2);
    });
    it('does not change the submitted journal when selecting the next batch', async () => {
        page(); open(); editNegative('draft'); fireEvent.click(screen.getByRole('button', { name: 'x2' })); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1)); await screen.findAllByText('待确认');
        const original = structuredClone(listStudioAssetImageRequests(owner())[0].request);
        fireEvent.click(screen.getByRole('button', { name: 'x4' }));
        expect(listStudioAssetImageRequests(owner())[0].request).toEqual(original);
        expect(original.batch_size).toBe(2); expect(posts()).toHaveLength(1); expect(screen.getAllByText('待确认')).toHaveLength(2);
    });
});

describe('real Vault Qwen scene/prop Core requests', () => {
    it.each(kinds.flatMap(kind => models.map(model => [kind, model] as const)))('submits %s detail with %s and preserves authored negative', async (kind, model) => {
        install({ model, negative: 'inherited negative' }); page(kind); open(kind);
        fireEvent.click(screen.getByRole('button', { name: /Advanced Settings/ }));
        const input = screen.getByPlaceholderText('Enter negative prompt...');
        expect(input).not.toHaveValue('inherited negative');
        fireEvent.change(input, { target: { value: 'authored negative' } });
        fireEvent.click(screen.getByRole('button', { name: 'x2' })); fireEvent.click(screen.getByRole('button', { name: '生成' }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ asset_type: kind, asset_id: 'shared', generation_type: 'all', model_name: model,
            batch_size: 2, negative_prompt: 'authored negative', apply_style: true, prompt: '' });
        expect(axios.post).not.toHaveBeenCalled(); expect(await screen.findAllByText('待确认')).toHaveLength(2);
    });

    it.each(kinds)('preserves %s quick-card batch1 with no authored negative', async kind => {
        install({ negative: 'style negative' }); page(kind); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(body()).toMatchObject({ batch_size: 1, negative_prompt: '', apply_style: true, prompt: '' }); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(kinds.flatMap(kind => [500, 501].map(count => [kind, count] as const)))('validates %s effective negative at %i Unicode codepoints', async (kind, count) => {
        install({ negative: 's' }); page(kind); open(kind); editNegative('😀'.repeat(count - 3));
        if (count === 501) { expect(generate()).toBeDisabled(); expect(screen.getByText(tooLong)).toBeInTheDocument(); noSubmission(); }
        else { fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); expect(body().negative_prompt).toBe('😀'.repeat(497)); }
    });
    it('keeps explicit authored negative even when it equals the inherited negative', async () => {
        install({ negative: 'same text' }); page(); open(); editNegative('same text'); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body().negative_prompt).toBe('same text');
    });
    it('allows style-off as an explicit local edit with no request and keeps the authored input', async () => {
        install({ negative: 'x'.repeat(501) }); page(); open(); editNegative('authored'); expect(generate()).toBeDisabled();
        fireEvent.click(screen.getByLabelText('Apply Art Direction Style')); expect(generate()).toBeEnabled(); noSubmission(); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(body()).toMatchObject({ apply_style: false, negative_prompt: 'authored', style_prompt: '' });
    });
    it('uses fresh root style instead of copying its negative into authored input', async () => {
        install({ negative: 'old' }); page(); open(); editNegative('authored'); server = snapshot({ revision: 7, negative: 'fresh' });
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); expect(body()).toMatchObject({ negative_prompt: 'authored', style_prompt: 'soft light' });
    });
    it('reads inherited style from the global asset root series exactly once', async () => {
        install({ seriesId: 's' }); series.art_direction = snapshot({ negative: 'series negative' }).art_direction;
        page(); open(); editNegative('authored'); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/series/s'))).toHaveLength(1);
        expect(body()).toMatchObject({ negative_prompt: 'authored', style_prompt: 'soft light' });
    });
    it.each(['mismatch', 'failure', 'long'])('blocks %s series style before a journal exists', async change => {
        install({ seriesId: 's' }); if (change === 'mismatch') series.revision = 2;
        else if (change === 'long') series.art_direction = snapshot({ negative: 'x'.repeat(501) }).art_direction;
        else vi.mocked(axios.get).mockImplementation(async url => { if (String(url).includes('/series/')) throw Error('offline'); return { data: server }; });
        page(); open(); editNegative(''); fireEvent.click(generate()); await act(async () => {}); noSubmission();
    });
    it.each(['draft ABA', 'style ABA', 'owner', 'root series', 'model', 'environment ABA', 'close'])('cancels %s changes during the Web Lock wait', async change => {
        page(); open(); editNegative('draft'); const lock = lockQueue(); fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        act(() => {
            if (change === 'draft ABA') { editNegative('other'); editNegative('draft'); }
            else if (change === 'style ABA') { fireEvent.click(screen.getByLabelText('Apply Art Direction Style')); fireEvent.click(screen.getByLabelText('Apply Art Direction Style')); }
            else if (change === 'environment ABA') { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); }
            else if (change === 'close') fireEvent.click(screen.getByRole('button', { name: 'Done' }));
            else install({ revision: 8, ...(change === 'owner' ? { source: 'episode' } : change === 'model' ? { model: models[1] } : { seriesId: 'other' }) });
        });
        await act(async () => { await lock.release(); }); noSubmission();
    });
    it.each(['lost ACK', '503'])('recovers %s via the original receipt ID without a legacy request', async failure => {
        if (failure === '503') vi.mocked(appAccessFetch).mockResolvedValueOnce(response({ detail: 'unavailable' }, 503)); else vi.mocked(appAccessFetch).mockRejectedValueOnce(Error('lost ACK'));
        const view = page(); fireEvent.click(screen.getByRole('button', { name: 'Generate' })); await waitFor(() => expect(posts()).toHaveLength(1)); const key = body().client_request_id;
        view.unmount(); vi.mocked(appAccessFetch).mockResolvedValue(response(wire('scene', key))); page(); await waitFor(() => expect(gets().length).toBeGreaterThan(0));
        expect(gets().every(([url]) => String(url).endsWith(key))).toBe(true); expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });

    it('recovers a pre-existing scene journal using GET only', async () => {
        seed('scene'); page(); await waitFor(() => expect(gets().length).toBeGreaterThan(0));
        expect(await screen.findAllByText('待确认')).toHaveLength(2); expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
    });
    it('cancels an environment ABA before inherited series read resolves', async () => {
        install({ seriesId: 's' }); const read = deferred<{ data: typeof series }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).includes('/series/') ? read.promise : Promise.resolve({ data: server }));
        page(); open(); editNegative(''); fireEvent.click(generate()); await waitFor(() => expect(vi.mocked(axios.get).mock.calls.some(([url]) => String(url).includes('/series/'))).toBe(true));
        act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        await act(async () => { read.resolve({ data: series }); }); noSubmission(); expect(useProjectStore.getState().currentSeries).toBeNull();
    });
});

describe('GPT scene and prop keep the existing Vault controls', () => {
    it('submits a GPT scene card once with fresh inherited positive and empty authored negative', async () => {
        install({ model: 'gpt-image-2', seriesId: 's' });
        series.art_direction = snapshot({ negative: 'x'.repeat(501) }).art_direction;
        page(); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ asset_type: 'scene', generation_type: 'all', model_name: 'gpt-image-2',
            prompt: '', batch_size: 1, apply_style: true, negative_prompt: '', style_prompt: 'soft light' });
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/series/s'))).toHaveLength(1);
        expect(useProjectStore.getState().currentSeries?.art_direction?.style_config.negative_prompt).toHaveLength(501);
        expect(axios.post).not.toHaveBeenCalled();
    });
    it('keeps GPT prop authored negative separate from inherited negative until explicit clear', async () => {
        const model = 'gpt-image/gpt-image-2#image'; install({ model, negative: 'inherited negative' }); page('prop'); open('prop');
        editNegative('inherited negative'); expect(generate()).toBeDisabled(); noSubmission();
        fireEvent.click(screen.getByRole('button', { name: '清空负向提示词' }));
        expect(screen.getByPlaceholderText('Enter negative prompt...')).toHaveValue('');
        expect(screen.getByLabelText('Apply Art Direction Style')).toBeChecked(); expect(generate()).toBeEnabled();
        expect(screen.queryByRole('button', { name: '本次不使用风格提示词' })).toBeNull();
        expect(screen.getByText('负向提示词（本次不应用）')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'x2' })); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ asset_type: 'prop', generation_type: 'all', model_name: model,
            prompt: '', batch_size: 2, negative_prompt: '', apply_style: true, style_prompt: 'soft light' });
        expect(server.art_direction?.style_config.negative_prompt).toBe('inherited negative'); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['draft ABA', 'environment ABA'])('cancels GPT Vault %s while its shared card/detail lock is held', async change => {
        install({ model: 'gpt-image-2' }); page(); open(); editNegative(''); const lock = lockQueue();
        try {
            fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
            act(() => {
                if (change === 'draft ABA') { editNegative('temporary'); editNegative(''); }
                else {
                    activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' });
                    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
                }
            });
        } finally { if (lock.request.mock.calls.length) await act(async () => { await lock.release(); }); }
        noSubmission();
    });
});
