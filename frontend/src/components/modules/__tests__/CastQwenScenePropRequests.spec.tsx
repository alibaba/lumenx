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
const kinds = ['scene', 'prop'] as const;
type Kind = typeof kinds[number];
let activeKind: Kind = 'scene';
const SID = '11111111-1111-4111-8111-111111111111';
const models = ['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'];
const templateNegative = 'text, labels, watermark, UI overlay, panel borders, frames';
const tooLong = '模板与风格合并后的负向提示词超过 500 个字符，请缩短风格负向提示词或关闭风格。';
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'a' });
function style(negative = '', positive = 'soft light') {
    return { selected_style_id: 'custom', custom_styles: [], ai_recommendations: [],
        style_config: { id: 'custom', is_custom: true, name: 'Saved style', positive_prompt: positive, negative_prompt: negative } };
}
type SnapshotOptions = { kind?: Kind; model?: string; source?: string; negative?: string; revision?: number; ratio?: string; seriesId?: string | null;
    art?: ReturnType<typeof style> | null; seriesRevision?: number; assetId?: string };
function snapshot({ kind = activeKind, model = 'qwen-image-2.0', source = 'episode', negative = '', revision = 5, ratio = activeKind === 'scene' ? '16:9' : '1:1', seriesId = null as string | null,
    art = negative ? style(negative) : null as ReturnType<typeof style> | null, seriesRevision = 1, assetId = 'shared' }: SnapshotOptions = {}) {
    return enriched({ ...ack([], 'a', revision), series_id: source === 'series' ? seriesId ?? 's' : seriesId,
        model_settings: { t2i_model: model, scene_aspect_ratio: ratio, prop_aspect_ratio: ratio }, art_direction: art,
        characters: [], scenes: kind === 'scene' ? [{ id: assetId, name: 'Place', description: 'red field', source }] : [], props: kind === 'prop' ? [{ id: assetId, name: 'Object', description: 'red box', source }] : [] }, 9, seriesRevision);
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
    const source = (activeKind === 'scene' ? root.scenes : root.props)[0].source;
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: false, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: 'studio.asset.scene_prop.v1', request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: 'a',
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: root.series_id ?? null,
        owner_kind: source === 'global' ? 'global' : source === 'series' ? 'series' : 'script',
        owner_id: source === 'global' ? 'library' : source === 'series' ? root.series_id : 'a',
        asset_type: activeKind, asset_id: 'shared', channel: 'image',
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
const open = () => fireEvent.click(screen.getByRole('button', { name: activeKind === 'scene' ? '生成场景' : '生成道具' }));
function page(strict = false) {
    const content = <LightboxProvider><Cast /><ToastContainer /></LightboxProvider>;
    const view = renderWithIntl(strict ? <StrictMode>{content}</StrictMode> : content); open(); return view;
}
function seed(key = 'saved-qwen', model = models[0]) {
    return beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'shared', asset_type: activeKind, generation_type: 'all',
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
    activeKind = 'scene'; vi.resetAllMocks(); localStorage.clear(); useToastStore.getState().clear();
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

describe('real Cast Qwen scene/prop Core requests', () => {
    it.each(kinds.flatMap(kind => models.map(model => [kind, model] as const)))('submits %s with %s via the strict scene/prop tuple', async (kind, model) => {
        activeKind = kind; install({ kind, model }); page();
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'authored scene prop prompt' } });
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ asset_id: 'shared', asset_type: kind, generation_type: 'all', model_name: model,
            prompt: 'authored scene prop prompt', negative_prompt: templateNegative, apply_style: true, batch_size: 2 });
        expect(body()).not.toHaveProperty('reference_image_url'); expect(body()).not.toHaveProperty('aspect_ratio');
        expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/); expect(axios.post).not.toHaveBeenCalled();
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
    });

    it.each(kinds)('selects a public Qwen Pro card for %s using the real grid', async kind => {
        activeKind = kind; install({ kind, model: 'wan2.7-image', negative: 'style negative' }); page(); expect(generate()).toBeDisabled();
        select('Qwen Image 2.0 Pro'); expect(generate()).toBeEnabled(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ model_name: models[1], negative_prompt: templateNegative });
    });
    it.each(kinds.flatMap(kind => [500, 501].map(count => [kind, count] as const)))('checks %s combined negative at %i Unicode codepoints', async (kind, count) => {
        activeKind = kind; install({ kind, negative: '😀'.repeat(count - Array.from(templateNegative).length - 2) }); page();
        if (count === 501) { expect(generate()).toBeDisabled(); expect(screen.getByText(tooLong)).toBeInTheDocument(); noSubmission(); }
        else { fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); expect(body().negative_prompt).toBe(templateNegative); }
    });
    it('explicitly disables style while preserving the template and selected ratio', async () => {
        install({ negative: 'x'.repeat(501) }); page(); fireEvent.click(screen.getByRole('button', { name: '本次不使用风格提示词' }));
        fireEvent.click(screen.getByRole('button', { name: '3:4' })); noSubmission(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ apply_style: false, style_prompt: '', negative_prompt: templateNegative, aspect_ratio: '3:4' });
    });
    it('uses the fresh root style once for a global scene', async () => {
        install({ source: 'global', negative: 'old' }); page(); server = snapshot({ revision: 6, source: 'global', art: style('fresh negative', 'fresh positive') });
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); expect(body()).toMatchObject({ style_prompt: 'fresh positive', negative_prompt: templateNegative });
    });
    it('reads root series for a global scene without trusting another cached series', async () => {
        install({ source: 'global', seriesId: 's' }); series.art_direction = style('inherited negative', 'inherited positive');
        useProjectStore.setState({ currentSeries: cachedSeries({ id: 'other', revision: 99, art_direction: style('x'.repeat(501)) }) });
        page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(vi.mocked(axios.get).mock.calls.some(([url]) => String(url).endsWith('/series/s'))).toBe(true);
        expect(body()).toMatchObject({ style_prompt: 'inherited positive', negative_prompt: templateNegative });
    });
    it.each(['mismatch', 'failure', 'long'])('blocks %s inherited series read with no journal', async change => {
        install({ seriesId: 's' }); page();
        if (change === 'mismatch') series.revision = 2;
        else if (change === 'long') series.art_direction = style('x'.repeat(501));
        else vi.mocked(axios.get).mockImplementation(async url => { if (String(url).includes('/series/')) throw Error('offline'); return { data: server }; });
        fireEvent.click(generate()); await act(async () => {}); noSubmission();
    });
    it.each(['ratio', 'owner', 'series', 'model'])('rejects changed fresh %s without a strict or legacy POST', async change => {
        page(); server = snapshot({ revision: 6, ...(change === 'ratio' ? { ratio: '1:1' } : change === 'owner' ? { source: 'global' }
            : change === 'series' ? { seriesId: 'other' } : { model: models[1] }) }); fireEvent.click(generate()); await act(async () => {}); noSubmission();
    });
    it.each(['style', 'owner', 'series', 'model', 'draft ABA', 'environment ABA', 'close'])('checks %s after waiting for the Web Lock', async change => {
        install({ art: style('original') }); page(); const lock = lockQueue(); fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
        act(() => {
            if (change === 'close') fireEvent.click(screen.getByRole('button', { name: '关闭' }));
            else if (change === 'environment ABA') { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); }
            else if (change === 'draft ABA') { select('Qwen Image 2.0 Pro'); select('Qwen Image 2.0'); }
            else install({ revision: 8, art: style(change === 'style' ? 'new' : 'original'), ...(change === 'owner' ? { source: 'global' } : change === 'model' ? { model: models[1] } : change === 'series' ? { seriesId: 's' } : {}) });
        });
        await act(async () => { await lock.release(); }); noSubmission();
    });
    it.each(['lost ACK', '503'])('recovers %s by original-ID GET on reopen without fallback', async failure => {
        if (failure === '503') vi.mocked(appAccessFetch).mockResolvedValueOnce(response({ detail: 'unavailable' }, 503)); else vi.mocked(appAccessFetch).mockRejectedValueOnce(Error('lost ACK'));
        page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); const key = body().client_request_id;
        fireEvent.click(screen.getByRole('button', { name: '关闭' })); open(); await waitFor(() => expect(gets().length).toBeGreaterThan(0));
        expect(gets().every(([url]) => String(url).endsWith(key))).toBe(true); expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
    it('recovers old Wan identity under current Qwen with GET only', async () => {
        seed('saved-wan', 'wan2.7-image'); page(true); await waitFor(() => expect(gets().length).toBeGreaterThan(0));
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2); noSubmissionExceptSaved();
    });
    it('keeps unsupported tuple and non-exact Qwen IDs out of the gate', () => {
        for (const model of models) { expect(usesStudioImageCore('scene', model, 'full_body')).toBe(false); expect(usesStudioImageCore('prop', model, 'reference_sheet')).toBe(false); }
        expect(usesStudioImageCore('scene', 'qwen-image-edit', 'image')).toBe(false);
    });
    it('cancels inherited-series preflight on an environment ABA', async () => {
        install({ seriesId: 's' }); page(); const read = deferred<{ data: typeof series }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).includes('/series/') ? read.promise : Promise.resolve({ data: server }));
        fireEvent.click(generate()); await waitFor(() => expect(vi.mocked(axios.get).mock.calls.some(([url]) => String(url).includes('/series/'))).toBe(true));
        act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        await act(async () => read.resolve({ data: series })); noSubmission(); expect(useProjectStore.getState().currentSeries).toBeNull();
    });
});
function noSubmissionExceptSaved() { expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(1); }

describe('GPT scene and prop keep their own Cast author entry', () => {
    it.each([
        ['scene', 'gpt-image-2', 'wide establishing shot'],
        ['prop', 'gpt-image/gpt-image-2#image', 'product photography style'],
    ] as const)('submits %s through %s without character templates or inherited negative', async (kind, model, composition) => {
        activeKind = kind; install({ kind, model, source: 'global', seriesId: 's' });
        series.art_direction = style('x'.repeat(501), 'fresh series positive'); page();
        expect(generate()).toBeEnabled(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ asset_type: kind, generation_type: 'all', model_name: model,
            apply_style: true, batch_size: 2, style_prompt: 'fresh series positive' });
        expect(body().prompt).toContain(composition); expect(body().prompt).not.toContain('character reference sheet');
        expect(body().prompt).not.toContain('professional character design sheet');
        for (const field of ['negative_prompt', 'reference_image_url', 'seed', 'watermark', 'aspect_ratio']) expect(body()).not.toHaveProperty(field);
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/series/s'))).toHaveLength(1);
        expect(await screen.findByText('负向提示词（本次不应用）')).toBeInTheDocument();
        expect(series.art_direction.style_config.negative_prompt).toHaveLength(501);
        expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['draft ABA', 'shared read floor'])('cancels GPT scene %s while the browser lock is held', async change => {
        install({ model: 'gpt-image-2', source: 'global' }); page(); const lock = lockQueue();
        try {
            fireEvent.click(generate()); await waitFor(() => expect(lock.request).toHaveBeenCalledOnce());
            act(() => {
                if (change === 'draft ABA') {
                    const original = (screen.getByRole('textbox') as HTMLTextAreaElement).value;
                    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'temporary draft' } });
                    fireEvent.change(screen.getByRole('textbox'), { target: { value: original } });
                } else useProjectStore.getState().applyProjectReadSnapshot('a', enriched(server, 10, 1));
            });
        } finally { if (lock.request.mock.calls.length) await act(async () => { await lock.release(); }); }
        noSubmission();
    });
});
