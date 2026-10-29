// Uses six captured three-view HTTP samples; provider execution is outside this suite.
// Real Vault, Workbench, request hook, readback and Zustand; network boundaries only.
import { StrictMode } from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred, project } from './frame-command-fixtures';
import capture from '@/__tests__/fixtures/studio-three-view-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Obj = Record<string, unknown>;
type Event = { label: string; body: Obj; request_body?: Obj; status_code: number };
type Capture = { cases: { case: string; events: Event[] }[] };
const SID = '11111111-1111-4111-8111-111111111111';
const negativePlaceholder = 'Enter your negative prompt (avoid unwanted elements)...';
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid captured object');
    return value as Obj;
}
function actual(name: string, label: string): Event {
    const found = (capture as unknown as Capture).cases.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual three-view HTTP sample: ${name}/${label}`);
    return structuredClone(found);
}
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p' });
const panel = () => screen.getAllByRole('group', { name: '2. 三视图' }).at(-1)!;
const fullBodyPanel = () => screen.getAllByRole('group', { name: '1. 主体资产（全身图）' }).at(-1)!;
const generate = () => within(panel()).getByRole('button', { name: '生成' });
const prompt = () => within(panel()).getByPlaceholderText('Enter prompt description...');
const editPrompt = (value: string) => fireEvent.change(prompt(), { target: { value } });
const editNegative = (value: string) => fireEvent.change(screen.getByPlaceholderText(negativePlaceholder), { target: { value } });
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/p'));
const journals = () => listStudioAssetImageRequests(owner());
const actor = (snapshot: Obj) => obj((snapshot.characters as unknown[]).find(value => obj(value).id === 'c'));
const savedChoice = () => useProjectStore.getState().currentProject?.characters.find(item => item.id === 'c')?.three_view_asset?.selected_id;
let server: Obj;
let activeCase: string;
function seed(name = activeCase) {
    const { client_request_id, ...input } = actual(name, 'accepted_post').request_body!;
    return beginStudioAssetImageRequest({ ...owner(), input: input as StudioAssetImageRequestInput,
        idFactory: () => String(client_request_id), now: () => 7 }).journal;
}
function install(name = 'local_single', label = 'before_project', seeded = false) {
    activeCase = name; server = actual(name, label).body;
    useProjectStore.setState({ currentProject: project('p', 0), projects: [project('p', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
    if (seeded) seed();
}
function open() { fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Alice' })); }
function page() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>); open(); return view;
}
function queueLock() {
    const gate = deferred<void>(); let entered = false;
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => {
            entered = true; await gate.promise; return callback({});
        },
    } });
    return { gate, entered: () => entered };
}
function noPaidSubmission() {
    expect(posts()).toHaveLength(0);
    expect(vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/assets/generate'))).toHaveLength(0);
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}),
    } });
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? []
        : String(url).includes('/series/') ? actual(activeCase, 'before_series').body : structuredClone(server) }));
    vi.mocked(axios.post).mockRejectedValue(new Error('Unexpected legacy generation'));
    vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => {
        // New author commands have newly generated keys. A deliberate lost ACK
        // tests their raw POST without rewriting an unrelated captured receipt.
        if (init?.method === 'POST') throw new Error('Synthetic acknowledgement lost');
        return response(actual(activeCase, 'result_get').body);
    });
    install();
});
afterEach(() => { clearRuntimeEnvironment(); });

describe('three-view real Vault entry', () => {
    it.each(['local_single', 'series_batch', 'global_single'])(
        '%s uses saved I2I and selected raw C, preserves default negative until explicit clearing, and never takes legacy', async name => {
            install(name); page();
            expect((screen.getByPlaceholderText(negativePlaceholder) as HTMLTextAreaElement).value).toContain('bad anatomy');
            expect(generate()).toBeDisabled(); noPaidSubmission();
            fireEvent.click(within(panel()).getByRole('button', { name: '清空负向提示词' }));
            expect(screen.getByPlaceholderText(negativePlaceholder)).toHaveValue('');
            expect(generate()).toBeEnabled();
            const expected = actual(name, 'accepted_post').request_body!;
            editPrompt(String(expected.prompt));
            fireEvent.click(within(panel()).getByRole('button', { name: `x${expected.batch_size}` }));
            fireEvent.click(generate());
            await waitFor(() => expect(posts()).toHaveLength(1));
            const sent = JSON.parse(String(posts()[0][1]?.body));
            expect(sent).toMatchObject({ asset_id: 'c', asset_type: 'character', generation_type: 'three_view',
                model_name: expected.model_name, reference_variant_id: 'selected-c', reference_image_url: 'uploads/selected-c.png',
                prompt: expected.prompt, style_prompt: expected.style_prompt, apply_style: true, negative_prompt: '', batch_size: expected.batch_size });
            expect(sent).not.toHaveProperty('aspect_ratio'); expect(sent).not.toHaveProperty('seed'); expect(sent).not.toHaveProperty('watermark');
            expect(String(posts()[0][0])).toMatch(/\/projects\/p\/asset_image_requests$/);
            expect(axios.post).not.toHaveBeenCalled();
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(sent);
            expect(useProjectStore.getState().currentProject?.model_settings?.t2i_model).toBe('wan2.7-image');
            expect(useProjectStore.getState().currentProject?.model_settings?.i2i_model).toBe(expected.model_name);
            const styleOwner = name === 'series_batch' ? useProjectStore.getState().currentSeries : useProjectStore.getState().currentProject;
            expect(styleOwner?.art_direction?.style_config.negative_prompt).toBe('Saved inherited negative remains unchanged.');
        });

    // Unit author-state mutations of the existing GPT project; POST deliberately loses its ACK.
    // These prove the real Workbench request path, not Wan provider execution or HTTP capture.
    it.each(['wan2.7-image', 'wan/wan2.7-image-pro#image'])(
        '%s submits only the selected local full-body raw reference through the existing Core journal', async model_name => {
            obj(server.model_settings).i2i_model = model_name;
            const source = obj((obj(actor(server).full_body_asset).variants as Obj[]).find(value => value.id === 'selected-c'));
            source.media_refs = { url: 'uploads/selected-c.png' }; source.url = 'https://signed.example.invalid/display.png';
            server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
            useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
            page(); expect(generate()).toBeDisabled(); editNegative('');
            expect(generate()).toBeDisabled(); // Saved style negative must not silently become GPT's ignored field.
            expect(within(panel()).getByText('此模型不支持负向提示词，请关闭风格提示词或更换模型。')).toBeInTheDocument();
            fireEvent.click(screen.getByRole('checkbox'));
            expect(generate()).toBeEnabled(); editPrompt('Authored Wan three-view'); fireEvent.click(generate());
            await waitFor(() => expect(posts()).toHaveLength(1));
            const sent = JSON.parse(String(posts()[0][1]?.body));
            expect(sent).toMatchObject({ asset_id: 'c', asset_type: 'character', generation_type: 'three_view', model_name,
                reference_variant_id: 'selected-c', reference_image_url: 'uploads/selected-c.png',
                prompt: 'Authored Wan three-view', apply_style: false, style_prompt: '', negative_prompt: '' });
            for (const field of ['reference_source', 'aspect_ratio', 'seed', 'watermark']) expect(sent).not.toHaveProperty(field);
            expect(String(posts()[0][0])).toMatch(/\/projects\/p\/asset_image_requests$/);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(sent);
            expect(axios.post).not.toHaveBeenCalled();
        });

    // Unit project/model mutation, real Workbench and journal; no Qwen result wire is invented.
    it.each(['qwen-image-2.0', 'qwen/qwen-image-2.0-pro#image'])(
        '%s retains user and saved style negatives while submitting the selected local reference through Core', async model_name => {
            obj(server.model_settings).i2i_model = model_name;
            server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
            useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
            page();
            expect((screen.getByPlaceholderText(negativePlaceholder) as HTMLTextAreaElement).value).toContain('bad anatomy');
            expect(generate()).toBeEnabled();
            expect(within(panel()).queryByRole('button', { name: '清空负向提示词' })).toBeNull();
            expect(within(panel()).queryByText('三视图保留正向风格；继承的负向风格本次不应用，已保存的风格设置不变。')).toBeNull();
            editNegative('保留手写负向, no text'); editPrompt('Authored Qwen three-view');
            fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
            const sent = JSON.parse(String(posts()[0][1]?.body));
            expect(sent).toMatchObject({ asset_id: 'c', asset_type: 'character', generation_type: 'three_view', model_name,
                reference_variant_id: 'selected-c', reference_image_url: 'uploads/selected-c.png',
                prompt: 'Authored Qwen three-view', apply_style: true, negative_prompt: '保留手写负向, no text',
                style_prompt: obj(obj(server.art_direction).style_config).positive_prompt });
            for (const field of ['reference_source', 'aspect_ratio', 'seed', 'watermark']) expect(sent).not.toHaveProperty(field);
            expect(String(posts()[0][0])).toMatch(/\/projects\/p\/asset_image_requests$/);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(sent);
            expect(screen.getByPlaceholderText(negativePlaceholder)).toHaveValue('保留手写负向, no text');
            expect(useProjectStore.getState().currentProject?.art_direction?.style_config.negative_prompt)
                .toBe('Saved inherited negative remains unchanged.');
            expect(axios.post).not.toHaveBeenCalled();
        });

    it('uses an explicit raw source sidecar instead of the signed display URL', async () => {
        const source = obj((obj(actor(server).full_body_asset).variants as Obj[]).find(value => value.id === 'selected-c'));
        source.media_refs = { url: 'uploads/selected-c.png' }; source.url = 'https://signed.example.invalid/display.png?expires=synthetic';
        server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
        useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
        page(); editNegative(''); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body)).reference_image_url).toBe('uploads/selected-c.png');
        expect(axios.post).not.toHaveBeenCalled();
    });

    it.each(['draft ABA', 'batch ABA', 'close', 'environment ABA'])(
        'does not submit the captured three-view intent after %s while its browser lock is queued', async change => {
            page(); editNegative(''); editPrompt('first author draft');
            fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
            const lock = queueLock(); fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
            if (change === 'draft ABA') { editPrompt('second draft'); editPrompt('first author draft'); }
            else if (change === 'batch ABA') {
                fireEvent.click(within(panel()).getByRole('button', { name: 'x4' }));
                fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
            } else if (change === 'close') fireEvent.click(screen.getByRole('button', { name: '关闭' }));
            else act(() => {
                activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' });
                activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
            });
            await act(async () => { lock.gate.resolve(); });
            noPaidSubmission(); expect(journals()).toHaveLength(0);
        });

    it.each(['gpt-image-2', 'wan2.7-image', 'qwen-image-2.0'])(
        'invalidates a queued %s three-view command after actual full-body selection A to B to A', async model_name => {
        obj(server.model_settings).i2i_model = model_name;
        if (model_name === 'wan2.7-image') obj(obj(server.art_direction).style_config).negative_prompt = '';
        server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
        useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
        page(); editNegative('');
        const lock = queueLock(); fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
        // These are intentional, independently versioned mutation ACK fixtures.
        // Each real VariantSelector click still passes through api/select/confirmed store readback.
        vi.mocked(axios.post).mockImplementation(async (_url, raw) => {
            const id = obj(raw).variant_id;
            const next = structuredClone(server);
            next.revision = Number(next.revision) + 1; obj(next.read_versions).project = next.revision;
            obj(actor(next).full_body_asset).selected_id = id;
            actor(next).full_body_image_url = id === 'first-b' ? 'uploads/first-b.png' : 'uploads/selected-c.png';
            server = next; return { data: next };
        });
        fireEvent.click(within(fullBodyPanel()).getAllByAltText('Variant')[0]);
        await waitFor(() => expect(useProjectStore.getState().currentProject?.characters[0].full_body_asset?.selected_id).toBe('first-b'));
        fireEvent.click(within(fullBodyPanel()).getAllByAltText('Variant')[1]);
        await waitFor(() => expect(useProjectStore.getState().currentProject?.characters[0].full_body_asset?.selected_id).toBe('selected-c'));
        await act(async () => { lock.gate.resolve(); });
        noPaidSubmission(); expect(journals()).toHaveLength(0);
        expect(vi.mocked(axios.post).mock.calls).toHaveLength(2);
    });

    it('keeps an actual unknown original key on close/reopen and explicit receipt viewing; all recovery traffic is GET', async () => {
        install('local_unknown', 'before_project', true); const key = journals()[0].request.client_request_id;
        const view = page(); await waitFor(() => expect(gets()).toHaveLength(1));
        await within(panel()).findByRole('region', { name: '生成请求结果' });
        const raw = journals()[0].request;
        view.unmount(); page(); await waitFor(() => expect(gets()).toHaveLength(2));
        fireEvent.click(within(panel()).getByRole('button', { name: '查看生成结果' }));
        await waitFor(() => expect(gets()).toHaveLength(3));
        expect(gets().every(([url]) => String(url).endsWith('/' + key))).toBe(true);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(raw); noPaidSubmission();
    });

    it('shows actual completed partial history and unknown remaining members without clearing its original journal or changing the old selection', async () => {
        install('global_partial', 'before_project', true); const old = savedChoice();
        server = actual('global_partial', 'after_project').body; page();
        const region = await within(panel()).findByRole('region', { name: '生成请求结果' });
        await within(region).findByText('已完成'); expect(within(region).getByText('待确认')).toBeInTheDocument();
        expect(within(region).getByAltText('结果 1')).toBeInTheDocument();
        expect(journals()).toHaveLength(1); expect(savedChoice()).toBe(old); noPaidSubmission();
    });
});

describe('three-view completion belongs to the observed request and author session', () => {
    it('recovers an original completion after StrictMode reopen without treating repeated effect setup as a draft edit', async () => {
        install('local_single', 'before_project', true);
        const key = journals()[0].request.client_request_id, held = deferred<Response>();
        vi.mocked(appAccessFetch).mockReturnValue(held.promise);
        const first = page(); await waitFor(() => expect(gets()).toHaveLength(1)); first.unmount();
        server = actual('local_single', 'after_project').body;
        const expected = obj(actor(server).three_view_asset);
        vi.mocked(appAccessFetch).mockImplementation(async () => response(actual('local_single', 'result_get').body));
        renderWithIntl(<StrictMode><LightboxProvider><ConsistencyVault /></LightboxProvider></StrictMode>); open();
        await waitFor(() => expect(journals()).toHaveLength(0));
        expect(gets().length).toBeGreaterThanOrEqual(3); // Original opening plus both StrictMode setups.
        expect(gets().every(([url]) => String(url).endsWith('/' + key))).toBe(true);
        expect(savedChoice()).toBe(expected.selected_id);
        expect(useProjectStore.getState().currentProject?.characters[0].three_view_asset?.variants).toEqual(expected.variants);
        await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
        expect(journals()).toHaveLength(0); expect(savedChoice()).toBe(expected.selected_id); noPaidSubmission();
    });

    it.each(['local_single', 'series_batch', 'global_single'])(
        '%s resolves only after its actual pointer and every completed raw image are read back; batch choice remains server-owned', async name => {
            install(name, 'before_project', true); server = actual(name, 'after_project').body;
            const expected = obj(actor(server).three_view_asset);
            page(); await waitFor(() => expect(projectReads().length).toBeGreaterThan(0));
            await waitFor(() => expect(journals()).toHaveLength(0));
            expect(savedChoice()).toBe(expected.selected_id);
            expect(useProjectStore.getState().currentProject?.characters[0].three_view_asset?.variants).toEqual(expected.variants);
            noPaidSubmission(); expect(axios.post).not.toHaveBeenCalled();
        });

    it.each(['pointer', 'raw URL', 'null sidecar', 'missing candidate'])(
        'retains the original journal and visible old selection when a completion readback has a wrong %s', async change => {
            install('local_single', 'before_project', true); const old = savedChoice();
            server = actual('local_single', 'after_project').body;
            const target = actor(server), unit = obj(target.three_view_asset);
            const receipt = obj(actual('local_single', 'result_get').body.request);
            const member = obj((receipt.members as unknown[])[0]);
            const candidates = unit.variants as Obj[], candidate = candidates.find(value => value.id === member.variant_id)!;
            if (change === 'pointer') target.three_view_request_id = '44444444-4444-4444-8444-444444444444';
            else if (change === 'raw URL') candidate.url = 'assets/studio/unrelated.png';
            else if (change === 'null sidecar') candidate.media_refs = { url: null };
            else unit.variants = candidates.filter(value => value.id !== member.variant_id);
            page(); await waitFor(() => expect(projectReads().length).toBeGreaterThan(0));
            await within(panel()).findByText('结果已保存，但最新素材读取失败，请再次查看。');
            expect(journals()).toHaveLength(1); expect(savedChoice()).toBe(old); noPaidSubmission();
        });

    it('accepts a valid completed candidate raw sidecar while its signed display URL differs', async () => {
        install('local_single', 'before_project', true); server = actual('local_single', 'after_project').body;
        const receipt = obj(actual('local_single', 'result_get').body.request), member = obj((receipt.members as unknown[])[0]);
        const candidate = (obj(actor(server).three_view_asset).variants as Obj[]).find(value => value.id === member.variant_id)!;
        candidate.media_refs = { url: member.output_ref }; candidate.url = 'https://signed.example.invalid/result.png?expires=synthetic';
        page(); await waitFor(() => expect(journals()).toHaveLength(0)); noPaidSubmission();
    });

    it('keeps an actual changed-input completion as history without reading over the current author state', async () => {
        install('local_changed', 'changed_project', true);
        vi.mocked(appAccessFetch).mockImplementation(async () => response(actual('local_changed', 'changed_get').body));
        page(); const region = await within(panel()).findByRole('region', { name: '生成请求结果' });
        await within(region).findByText('已完成');
        expect(journals()).toHaveLength(1); expect(projectReads()).toHaveLength(0); noPaidSubmission();
        expect(useProjectStore.getState().currentProject?.characters[0].description).toBe('A newer author description');
    });

    it('does not start result readback after an author edits the three-view draft while a reopened receipt GET is held', async () => {
        install('local_single', 'before_project', true); const old = savedChoice(), held = deferred<Response>();
        vi.mocked(appAccessFetch).mockReturnValue(held.promise); page();
        await waitFor(() => expect(gets()).toHaveLength(1)); editPrompt('Keep my newer three-view draft');
        server = actual('local_single', 'after_project').body;
        await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
        expect(prompt()).toHaveValue('Keep my newer three-view draft'); expect(projectReads()).toHaveLength(0);
        expect(journals()).toHaveLength(1); expect(savedChoice()).toBe(old); noPaidSubmission();
    });

    it('rechecks the draft epoch before a held project read can replace the visible author draft', async () => {
        install('local_single', 'before_project', true); const old = savedChoice(), held = deferred<{ data: Obj }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : held.promise);
        page(); await waitFor(() => expect(projectReads()).toHaveLength(1)); editPrompt('Later draft during readback');
        await act(async () => { held.resolve({ data: actual('local_single', 'after_project').body }); });
        expect(prompt()).toHaveValue('Later draft during readback'); expect(journals()).toHaveLength(1);
        expect(savedChoice()).toBe(old); noPaidSubmission();
    });

    it('checks the current snapshot returned by readback when a newer project supersedes the valid response', async () => {
        install('local_single', 'before_project', true); const held = deferred<{ data: Obj }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : held.promise);
        page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
        const responseProject = actual('local_single', 'after_project').body;
        const newer = structuredClone(responseProject);
        newer.revision = Number(newer.revision) + 1; obj(newer.read_versions).project = newer.revision;
        actor(newer).three_view_request_id = '44444444-4444-4444-8444-444444444444';
        act(() => { useProjectStore.getState().applyProjectReadSnapshot('p', newer); });
        await act(async () => { held.resolve({ data: responseProject }); });
        expect(obj(useProjectStore.getState().currentProject?.characters[0]).three_view_request_id)
            .toBe('44444444-4444-4444-8444-444444444444');
        expect(journals()).toHaveLength(1); noPaidSubmission();
    });
});


// Follow-up regression: synthetic saved artwork text, unchanged captured three-view receipt.
// This block is separate from the original 24 UI cases.
describe('three-view completion preserves other artwork drafts', () => {
    it.each(['full_body', 'headshot'] as const)(
        'preserves a new %s artwork draft while the original three-view completion GET is held', async channel => {
            const saved = `Saved ${channel} artwork prompt`, draft = `New unsaved ${channel} artwork prompt`;
            server = actual('local_single', 'before_project').body;
            actor(server)[`${channel}_prompt`] = saved;
            // Reinstall the original revision with an ordinary author-state field.
            // The real pointer, request, selected reference, candidates and receipt stay unchanged.
            useProjectStore.setState({ currentProject: project('p', 0), projects: [project('p', 0)], currentSeries: null,
                readWatermarks: {}, generatingTasks: [], runningOps: {} });
            useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
            const original = seed('local_single'), old = savedChoice(), held = deferred<Response>();
            vi.mocked(appAccessFetch).mockReturnValue(held.promise); page();
            await waitFor(() => expect(gets()).toHaveLength(1));
            const area = () => within(screen.getAllByRole('group', {
                name: channel === 'full_body' ? '1. 主体资产（全身图）' : '3. 头像（特写）',
            }).at(-1)!).getByPlaceholderText('Enter prompt description...');
            expect(area()).toHaveValue(saved);
            fireEvent.change(area(), { target: { value: draft } });
            expect(area()).toHaveValue(draft);
            server = actual('local_single', 'after_project').body;
            await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
            expect(area()).toHaveValue(draft); expect(projectReads()).toHaveLength(0);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
            expect(savedChoice()).toBe(old); noPaidSubmission();
        });
});


// Follow-up compatibility regressions: author-state projections below are synthetic,
// not new HTTP captures. The real Vault / Workbench / journal remain unmocked.
// Existing source cases now assert the migrated request contract; original selected cases stay intact.
describe('three-view retained legacy source boundaries', () => {
    function installLegacySource(source: 'legacy full-body URL' | 'uploaded headshot' | 'none') {
        install('local_single');
        const target = actor(server);
        target.full_body = null; target.full_body_asset = null;
        target.full_body_image_url = source === 'legacy full-body URL' ? 'uploads/legacy-a.png' : '';
        target.headshot_asset = source === 'uploaded headshot' ? { selected_id: 'uploaded-face', variants: [
            { id: 'uploaded-face', url: 'uploads/author-headshot.png', created_at: 7, is_uploaded_source: true },
        ] } : null;
        target.head_shot = null; target.headshot_image_url = ''; target.avatar_url = '';
        target.three_view_asset = null; target.three_views = null; target.three_view_image_url = '';
        server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
        useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
    }
    const legacyPosts = () => vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/assets/generate'));
    const holdLegacy = () => {
        const held = deferred<{ data: Obj }>();
        vi.mocked(axios.post).mockImplementation(async url => {
            if (!String(url).endsWith('/assets/generate')) throw new Error(`Unexpected legacy mutation: ${String(url)}`);
            return held.promise;
        });
        return held;
    };
    it.each(['legacy full-body URL', 'uploaded headshot'] as const)(
        'migrates legitimate local %s through the original-key Core journal', async source => {
            installLegacySource(source); page();
            expect(generate()).toBeDisabled(); editNegative('');
            editPrompt('Keep this explicit legacy three-view draft');
            fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
            fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
            const sent = JSON.parse(String(posts()[0][1]?.body));
            expect(sent).toMatchObject({ generation_type: 'three_view', model_name: 'gpt-image-2',
                prompt: 'Keep this explicit legacy three-view draft', negative_prompt: '', batch_size: 2,
                reference_source: source === 'legacy full-body URL' ? 'full_body_image_url' : 'headshot_upload',
                reference_image_url: source === 'legacy full-body URL' ? 'uploads/legacy-a.png' : 'uploads/author-headshot.png' });
            if (source === 'legacy full-body URL') expect(sent).not.toHaveProperty('reference_variant_id');
            else expect(sent.reference_variant_id).toBe('uploaded-face');
            expect(legacyPosts()).toHaveLength(0); expect(journals()).toHaveLength(1);
            expect(journals()[0].request).toEqual(sent);
        });

    it('rejects an ambiguous selected ID even when a local legacy URL and uploaded fallback exist', async () => {
        installLegacySource('uploaded headshot');
        const target = actor(server);
        target.full_body_image_url = 'uploads/legacy-a.png';
        target.full_body_asset = { selected_id: 'selected-c', variants: [
            { id: 'selected-c', url: 'uploads/selected-c.png', created_at: 7 },
            { id: 'selected-c', url: 'uploads/selected-c.png', created_at: 8 },
        ] };
        server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
        useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
        page(); editNegative('');
        await act(async () => { await Promise.resolve(); });
        expect(generate()).toBeDisabled(); fireEvent.click(generate()); noPaidSubmission();
        expect(journals()).toHaveLength(0);
    });

    it('keeps a genuinely absent source disabled instead of treating every null reference as legacy', async () => {
        installLegacySource('none'); page(); editNegative('');
        await act(async () => { await Promise.resolve(); });
        expect(generate()).toBeDisabled(); fireEvent.click(generate()); noPaidSubmission();
        expect(journals()).toHaveLength(0);
    });

    it('rechecks an original unknown Core journal before dispatching an already available legacy route', async () => {
        installLegacySource('legacy full-body URL');
        // This existing non-GPT route is available even before the source-compatibility fix.
        // It makes the original-journal guard independently reproducible, rather than
        // counting the current GPT missing-reference setup failure as this race's RED.
        obj(server.model_settings).i2i_model = 'wan2.7-image-pro';
        server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
        useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
        vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => {
            if (init?.method !== 'GET') throw new Error('No strict POST belongs to this legacy handoff');
            return response(actual('local_unknown', 'result_get').body);
        });
        const held = holdLegacy(), view = page();
        try {
            await act(async () => { await Promise.resolve(); });
            expect(generate()).toBeEnabled(); expect(gets()).toHaveLength(0);
            // A second observed session adds its original journal after the empty recovery read.
            // Do not trigger a render between this write and the actual Generate click.
            const original = seed('local_unknown');
            fireEvent.click(generate());
            noPaidSubmission();
            await waitFor(() => expect(gets()).toHaveLength(1));
            await within(panel()).findByText('待确认');
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
            expect(gets().every(([url]) => String(url).endsWith('/' + original.request.client_request_id))).toBe(true);
            expect(generate()).toBeDisabled(); expect(projectReads()).toHaveLength(0);
            noPaidSubmission();
        } finally {
            view.unmount(); clearRuntimeEnvironment();
            if (legacyPosts().length) await act(async () => { held.reject(new Error('Only legacy dispatch was observed')); });
        }
    });
});

// Synthetic author-state changes only; no completed backend receipt is manufactured.
describe('local GPT derived fallback identity', () => {
    const channel = 'three_view' as 'three_view' | 'headshot';
    const other = channel === 'three_view' ? 'headshot' : 'three_view';
    function installFallback(kind: 'legacy-url' | 'other-upload' | 'own-upload') {
        install();
        const target = actor(server);
        target.full_body_asset = null; target.full_body = null;
        target.full_body_image_url = kind === 'legacy-url' ? 'https://signed.example.invalid/full.png' : '';
        target.media_refs = { full_body_image_url: kind === 'legacy-url' ? 'uploads/legacy-a.png' : null };
        for (const name of ['three_view', 'headshot']) {
            target[name + '_asset'] = { selected_id: name + '-second', variants: [
                { id: name + '-first', url: 'https://signed.example.invalid/' + name + '-first.png',
                    media_refs: { url: 'uploads/' + name + '-first.png' }, created_at: 1,
                    is_uploaded_source: kind !== 'own-upload' || name === channel },
                { id: name + '-second', url: 'uploads/' + name + '-second.png', created_at: 2,
                    is_uploaded_source: kind !== 'own-upload' || name === channel },
            ] };
        }
        server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
        useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
    }
    it('sends a base character own local fallback through Core without a synthetic variant ID', async () => {
        installFallback('legacy-url'); actor(server).base_character_id = 'parent';
        server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
        useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
        page(); editNegative(''); editPrompt('Use this character own local image');
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        const sent = JSON.parse(String(posts()[0][1]?.body));
        expect(sent).toMatchObject({ generation_type: channel, model_name: 'gpt-image-2',
            prompt: 'Use this character own local image', reference_source: 'full_body_image_url',
            reference_image_url: 'uploads/legacy-a.png' });
        expect(sent).not.toHaveProperty('reference_variant_id');
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(sent);
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });
    it.each(['legacy-url', 'other-upload', 'own-upload'] as const)(
        '%s binds original raw identity and keeps its single journal after lost ACK and reopen', async kind => {
            installFallback(kind); const view = page(); editNegative(''); editPrompt('Keep my fallback draft');
            fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
            const sent = JSON.parse(String(posts()[0][1]?.body));
            const from = kind === 'other-upload' ? other : channel;
            expect(sent).toMatchObject({ generation_type: channel, model_name: 'gpt-image-2', prompt: 'Keep my fallback draft',
                reference_source: kind === 'legacy-url' ? 'full_body_image_url' : from + '_upload',
                reference_image_url: kind === 'legacy-url' ? 'uploads/legacy-a.png' : 'uploads/' + from + '-first.png' });
            if (kind === 'legacy-url') expect(sent).not.toHaveProperty('reference_variant_id');
            else expect(sent.reference_variant_id).toBe(from + '-first');
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(sent);
            expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
            view.unmount(); page(); await waitFor(() => expect(gets().length).toBeGreaterThan(0));
            expect(gets().every(([url]) => String(url).endsWith('/' + sent.client_request_id))).toBe(true);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(sent);
            expect(posts()).toHaveLength(1); expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
        });
    it('invalidates the pending browser lock when source-channel identity changes but raw ID and URL stay the same', async () => {
        installFallback('other-upload'); const view = page(); editNegative('');
        const lock = queueLock();
        try {
            fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
            const target = actor(server), source = obj(target[other + '_asset']);
            target[channel + '_asset'] = structuredClone(source);
            for (const variant of source.variants as Obj[]) variant.is_uploaded_source = false;
            server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
            act(() => { useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server)); });
            await act(async () => { lock.gate.resolve(); });
            expect(posts()).toHaveLength(0); expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
            expect(journals()).toHaveLength(0);
        } finally { view.unmount(); await act(async () => { lock.gate.resolve(); }); }
    });
    it.each(['null-raw', 'duplicate-first-id'])(
        'cannot use a signed display URL or a later uploaded fallback after %s', async kind => {
            installFallback('other-upload'); const images = obj(actor(server)[other + '_asset']);
            const source = (images.variants as Obj[])[0];
            if (kind === 'null-raw') source.media_refs = { url: null };
            else (images.variants as Obj[]).push({ ...source });
            server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
            useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
            page(); editNegative(''); expect(generate()).toBeDisabled(); fireEvent.click(generate());
            expect(posts()).toHaveLength(0); expect(vi.mocked(axios.post)).not.toHaveBeenCalled(); expect(journals()).toHaveLength(0);
        });
});
