import { StrictMode } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests } from '@/lib/studioAssetImageRequest';
import { usesStudioImageCore } from '@/lib/useStudioAssetImageRequests';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import ToastContainer from '@/components/shared/ToastContainer';
import { useProjectStore } from '@/store/projectStore';
import { useToastStore } from '@/store/toastStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from './frame-command-fixtures';
import Cast from '../Cast';
import { activePolls } from '../cast/CastWorkbenchModal';

// The entire Cast / modal / model grid / strict request stack is real; only HTTP is replaced.
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
const SID = '11111111-1111-4111-8111-111111111111';
const models = ['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'];
const templateNegative = 'text, labels, watermark, UI overlay, panel borders, frames, multiple separate images';
const tooLong = '模板与风格合并后的负向提示词超过 500 个字符，请缩短风格负向提示词或关闭风格。';
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'a' });
function style(negative = '', positive = 'soft light') {
    return { selected_style_id: 'custom', custom_styles: [], ai_recommendations: [],
        style_config: { id: 'custom', is_custom: true, name: 'Saved style', positive_prompt: positive, negative_prompt: negative } };
}
type SnapshotOptions = { model?: string; source?: string; negative?: string; revision?: number; ratio?: string; seriesId?: string | null;
    art?: ReturnType<typeof style> | null; seriesRevision?: number; assetId?: string };
function snapshot({ model = 'qwen-image-2.0', source = 'episode', negative = '', revision = 5, ratio = '9:16', seriesId = null as string | null,
    art = negative ? style(negative) : null as ReturnType<typeof style> | null, seriesRevision = 1, assetId = 'shared' }: SnapshotOptions = {}) {
    return enriched({ ...ack([], 'a', revision), series_id: source === 'series' ? seriesId ?? 's' : seriesId,
        model_settings: { t2i_model: model, character_aspect_ratio: ratio }, art_direction: art,
        characters: [{ id: assetId, name: 'Alice', description: 'red coat', source }] }, 9, seriesRevision);
}
let server: ReturnType<typeof snapshot>;
let series: { id: string; revision: number; art_direction: ReturnType<typeof style> | null };
const cachedSeries = (value: typeof series) => ({ ...value, art_direction: value.art_direction ?? undefined, title: 'Series', description: '',
    characters: [], scenes: [], props: [], episode_ids: [], created_at: 0, updated_at: 0 });
function install(options: Parameters<typeof snapshot>[0] = {}) {
    server = snapshot({ revision: (useProjectStore.getState().currentProject?.revision ?? 3) + 1, ...options });
    useProjectStore.getState().applyProjectReadSnapshot('a', server);
    return server;
}
function wire(key: string, statuses = ['needs_confirmation', 'needs_confirmation']) {
    const root = useProjectStore.getState().currentProject!;
    const source = root.characters[0].source;
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: 'studio.asset.reference_sheet.v1', request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: 'a',
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: root.series_id ?? null,
        owner_kind: source === 'global' ? 'global' : source === 'series' ? 'series' : 'script',
        owner_id: source === 'global' ? 'library' : source === 'series' ? root.series_id : 'a',
        asset_type: 'character', asset_id: 'shared', channel: 'reference_sheet',
        members: statuses.map((status, i) => ({ variant_id: `${key}-${i}`, attempt_id: `${key}-${i}`, status,
            ...(status === 'completed' ? { output_ref: `assets/studio/${key}-${i}.png` } : {}) })),
    } };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const body = () => JSON.parse(String(posts()[0]?.[1]?.body));
const generate = () => screen.getByRole('button', { name: /生成第一组|再生成|发起新生成/ });
const select = (name: string) => fireEvent.click(screen.getByText(name, { selector: 'span' }).closest('button')!);
const open = () => fireEvent.click(screen.getByRole('button', { name: '生成形象' }));
function page(strict = false) {
    const content = <LightboxProvider><Cast /><ToastContainer /></LightboxProvider>;
    const view = renderWithIntl(strict ? <StrictMode>{content}</StrictMode> : content); open(); return view;
}
function seed(key = 'saved-qwen', model = models[0]) {
    return beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'shared', asset_type: 'character', generation_type: 'reference_sheet',
        model_name: model, ...(model.includes('qwen') ? { negative_prompt: templateNegative } : {}), batch_size: 2 }, idFactory: () => key }).journal;
}
function lockQueue() {
    let release!: () => Promise<unknown>;
    const request = vi.fn((_name: string, _options: unknown, callback: (lock: object) => unknown) => new Promise((resolve, reject) => {
        release = async () => { try { resolve(await callback({})); } catch (error) { reject(error); } };
    }));
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request } });
    return { request, release: () => release() };
}
function noSubmission() {
    expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
    expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); useToastStore.getState().clear();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ currentProject: project(), projects: [project()], currentSeries: null, readWatermarks: {}, generatingTasks: [], runningOps: {} });
    series = { id: 's', revision: 1, art_direction: null };
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : String(url).includes('/series/') ? series : server }));
    vi.mocked(axios.post).mockResolvedValue({ data: { ...ack([], 'a', 6), _task_id: 'legacy' } });
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        const input = init?.method === 'POST' ? JSON.parse(String(init.body)) : null;
        const key = input?.client_request_id ?? String(url).split('/').at(-1)!;
        return response(wire(key, Array.from({ length: input?.batch_size ?? 2 }, () => 'needs_confirmation')));
    });
    install();
});
afterEach(() => { activePolls.forEach(clearInterval); activePolls.clear(); vi.useRealTimers(); clearRuntimeEnvironment(); });

describe('real Cast Qwen reference-sheet requests', () => {
    it.each(models)('submits %s once with template negative and the original project root', async model => {
        install({ model }); page(); fireEvent.change(screen.getByRole('textbox'), { target: { value: 'my reference sheet' } });
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ asset_id: 'shared', asset_type: 'character', generation_type: 'reference_sheet', model_name: model,
            prompt: 'my reference sheet', negative_prompt: templateNegative, apply_style: true, batch_size: 2 });
        expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/); expect(axios.post).not.toHaveBeenCalled();
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
    });
    it.each(['Qwen Image 2.0', 'Qwen Image 2.0 Pro'])('selects the public %s card from the real model grid', async name => {
        install({ model: 'wan2.7-image', negative: 'avoid clutter' }); page(); expect(generate()).toBeDisabled();
        select(name); expect(generate()).toBeEnabled(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ model_name: name.endsWith('Pro') ? models[1] : models[0], negative_prompt: templateNegative });
        expect(body().negative_prompt).not.toContain('avoid clutter'); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['episode', 'series', 'global'])('retains %s ownership and two original batch members', async source => {
        install({ source }); page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body().batch_size).toBe(2); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['9:16', '3:4', '1:1', '4:3', '16:9'])('preserves the existing %s ratio control', async ratio => {
        page(); fireEvent.click(screen.getByRole('button', { name: ratio })); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        if (ratio === '9:16') expect(body()).not.toHaveProperty('aspect_ratio'); else expect(body().aspect_ratio).toBe(ratio);
    });
    it.each([500, 501])('validates %i combined Unicode codepoints before making a paid request', async count => {
        const negative = '😀'.repeat(count - Array.from(templateNegative).length - 2);
        install({ negative }); page();
        if (count === 501) { expect(generate()).toBeDisabled(); expect(screen.getByText(tooLong)).toBeInTheDocument(); noSubmission(); }
        else { expect(generate()).toBeEnabled(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); expect(body().negative_prompt).toBe(templateNegative); }
    });
    it('allows explicit style-off while retaining the template negative', async () => {
        install({ negative: 'x'.repeat(501) }); page(); expect(generate()).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: '本次不使用风格提示词' })); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(body()).toMatchObject({ apply_style: false, negative_prompt: templateNegative, style_prompt: '' });
    });
    it('uses the freshly read root style once without copying its negative into the request', async () => {
        install({ negative: 'old negative' }); page(); server = snapshot({ revision: 6, negative: 'fresh negative', art: style('fresh negative', 'fresh positive') });
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ negative_prompt: templateNegative, style_prompt: 'fresh positive' });
    });
    it('blocks an over-limit style first discovered by the project read', async () => {
        page(); server = snapshot({ revision: 6, negative: 'x'.repeat(501) }); fireEvent.click(generate()); await act(async () => {});
        noSubmission(); expect(screen.getAllByText(tooLong).length).toBeGreaterThan(0);
    });
    it('reads the captured series style for a global asset without trusting a foreign cached series', async () => {
        install({ source: 'global', seriesId: 's' });
        series = { id: 's', revision: 1, art_direction: style('series negative', 'series positive') };
        useProjectStore.setState({ currentSeries: cachedSeries({ id: 'other', revision: 99, art_direction: style('x'.repeat(501), 'wrong') }) });
        page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(vi.mocked(axios.get).mock.calls.some(([url]) => String(url).endsWith('/series/s'))).toBe(true);
        expect(body()).toMatchObject({ negative_prompt: templateNegative, style_prompt: 'series positive' });
    });
    it.each(['mismatch', 'failure'])('does not submit when inherited series style read is %s', async change => {
        install({ seriesId: 's' }); page();
        if (change === 'mismatch') series = { id: 's', revision: 2, art_direction: style('different') };
        else vi.mocked(axios.get).mockImplementation(async url => { if (String(url).includes('/series/')) throw new Error('offline'); return { data: server }; });
        fireEvent.click(generate()); await act(async () => {}); noSubmission();
    });
    it('validates a too-long inherited style only after its captured series read', async () => {
        install({ seriesId: 's' }); series.art_direction = style('x'.repeat(501)); page(); expect(generate()).toBeEnabled();
        fireEvent.click(generate()); await act(async () => {}); noSubmission(); expect(screen.getAllByText(tooLong).length).toBeGreaterThan(0);
    });
    it.each(['close', 'environment', 'owner', 'draft'])('rejects %s changes while the inherited series read is pending', async change => {
        install({ seriesId: 's' }); page(); const read = deferred<{ data: typeof series }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).includes('/series/') ? read.promise : Promise.resolve({ data: server }));
        fireEvent.click(generate()); await waitFor(() => expect(vi.mocked(axios.get).mock.calls.some(([url]) => String(url).includes('/series/'))).toBe(true));
        act(() => {
            if (change === 'close') { fireEvent.click(screen.getByRole('button', { name: '关闭' })); open(); }
            else if (change === 'environment') { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); }
            else if (change === 'owner') install({ source: 'global', seriesId: 's', revision: 6 });
            else fireEvent.change(screen.getByRole('textbox'), { target: { value: 'a new idea' } });
        });
        await act(async () => { read.resolve({ data: series }); }); noSubmission(); expect(useProjectStore.getState().currentSeries).toBeNull();
    });
    it('does not read or validate disabled inherited style', async () => {
        install({ seriesId: 's', art: style('old') }); page(); fireEvent.click(screen.getByRole('button', { name: '风格基线' }));
        server = snapshot({ revision: 6, seriesId: 's' }); series = { id: 's', revision: 99, art_direction: style('x'.repeat(501)) };
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body().apply_style).toBe(false); expect(vi.mocked(axios.get).mock.calls.some(([url]) => String(url).includes('/series/'))).toBe(false);
    });
    it.each(['model', 'ratio', 'owner', 'series'])('rejects a changed %s in fresh project state', async change => {
        page(); server = snapshot({ revision: 6, ...(change === 'model' ? { model: models[1] } : change === 'ratio' ? { ratio: '1:1' }
            : change === 'owner' ? { source: 'global' } : { seriesId: 'other' }) });
        fireEvent.click(generate()); await act(async () => {}); noSubmission();
    });
    it('keeps explicit model and ratio choices when unrelated project defaults change', async () => {
        install({ model: 'wan2.7-image' }); page(); select('Qwen Image 2.0 Pro'); fireEvent.click(screen.getByRole('button', { name: '1:1' }));
        server = snapshot({ revision: 6, model: 'gpt-image-2', ratio: '16:9' }); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(body()).toMatchObject({ model_name: models[1], aspect_ratio: '1:1' });
    });
    it.each(['model', 'prompt', 'ratio', 'batch', 'template', 'style'])('invalidates the preflight when the user changes %s', async change => {
        install({ art: style('avoid blur') }); page(); const read = deferred<{ data: typeof server }>(); vi.mocked(axios.get).mockReturnValueOnce(read.promise);
        fireEvent.click(generate());
        if (change === 'model') select('Qwen Image 2.0 Pro');
        else if (change === 'prompt') fireEvent.change(screen.getByRole('textbox'), { target: { value: 'changed draft' } });
        else if (change === 'ratio') fireEvent.click(screen.getByRole('button', { name: '1:1' }));
        else if (change === 'batch') fireEvent.click(screen.getByRole('button', { name: '×4' }));
        else if (change === 'template') fireEvent.click(screen.getByRole('button', { name: /详细参考表/ }));
        else fireEvent.click(screen.getByRole('button', { name: '风格基线' }));
        await act(async () => { read.resolve({ data: server }); }); noSubmission();
    });
    it.each(['environment', 'close', 'model'])('invalidates A→B→A during preflight for %s', async change => {
        page(); const read = deferred<{ data: typeof server }>(); vi.mocked(axios.get).mockReturnValueOnce(read.promise); fireEvent.click(generate());
        act(() => {
            if (change === 'environment') { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); }
            else if (change === 'close') { fireEvent.click(screen.getByRole('button', { name: '关闭' })); open(); }
            else { select('Qwen Image 2.0 Pro'); select('Qwen Image 2.0'); }
        });
        await act(async () => { read.resolve({ data: server }); }); noSubmission();
    });
    it.each(['style', 'owner', 'model', 'series', 'close', 'environment'])('rechecks %s inside the cross-tab lock before journal creation', async change => {
        install({ art: style('original') }); page(); const lock = lockQueue(); fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        act(() => {
            if (change === 'close') fireEvent.click(screen.getByRole('button', { name: '关闭' }));
            else if (change === 'environment') { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); }
            else install({ revision: 8, art: style(change === 'style' ? 'new negative' : 'original'), ...(change === 'owner' ? { source: 'global' }
                : change === 'model' ? { model: models[1] } : change === 'series' ? { seriesId: 'other' } : {}) });
        });
        await act(async () => { await lock.release(); }); noSubmission();
    });
    it('rejects a changed inherited series revision while waiting for the lock', async () => {
        install({ seriesId: 's' }); series.art_direction = style('series original'); page(); const lock = lockQueue();
        fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        act(() => { useProjectStore.setState({ currentSeries: cachedSeries({ ...series, revision: 2, art_direction: style('new') }) }); });
        await act(async () => { await lock.release(); }); noSubmission();
    });
    it('rejects a model-card A→B→A inside the cross-tab lock', async () => {
        page(); const lock = lockQueue(); fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        act(() => { select('Qwen Image 2.0 Pro'); select('Qwen Image 2.0'); });
        await act(async () => { await lock.release(); }); noSubmission();
    });
    it('does not fall back after a lost strict POST and only reads the same request on reopen', async () => {
        vi.mocked(appAccessFetch).mockRejectedValueOnce(new Error('lost ACK')); page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
        const key = body().client_request_id; fireEvent.click(screen.getByRole('button', { name: '关闭' })); open();
        await waitFor(() => expect(gets()).toHaveLength(1)); expect(String(gets()[0][0])).toContain(key); expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
    it('restores existing Wan history under the Qwen model with reads only', async () => {
        seed('saved-wan', 'wan2.7-image'); page(true); await waitFor(() => expect(gets().length).toBeGreaterThan(0));
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2); expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
    });
    it('ignores a late receipt after closing and reopening onto another entity', async () => {
        seed(); const receipt = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValueOnce(receipt.promise); page();
        await waitFor(() => expect(gets()).toHaveLength(1)); fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        act(() => { install({ assetId: 'other', revision: 6 }); }); open();
        await act(async () => { receipt.resolve(response(wire('saved-qwen', ['completed', 'completed']))); });
        expect(screen.queryByTestId('studio-image-member')).toBeNull(); expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(models)('keeps Qwen %s strict routing limited to supported character and scene/prop tuples', model => {
        expect(usesStudioImageCore('character', model, 'reference_sheet')).toBe(true);
        expect(usesStudioImageCore('character', model, 'full_body')).toBe(true);
        expect(usesStudioImageCore('scene', model, 'image')).toBe(true); expect(usesStudioImageCore('prop', model, 'image')).toBe(true);
    });
});
