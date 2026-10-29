// Real Vault/Workbench/hooks/store; network boundaries only.
// The captured three-view BEFORE project supplies ordinary author state only.
// No headshot success receipt is fabricated: POST loses ACK; recovery GET is 404.
// Actual headshot completion/partial/readback capture tests remain a separate gate.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import { listStudioMotionRequests } from '@/lib/studioMotionRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred, project } from './frame-command-fixtures';
import capture from '@/__tests__/fixtures/studio-three-view-wire.json';
import motionCapture from '@/__tests__/fixtures/studio-motion-duration5-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Obj = Record<string, unknown>;
type Capture = { cases: { case: string; events: { label: string; body: Obj }[] }[] };
const SID = '11111111-1111-4111-8111-111111111111';
const negativePlaceholder = 'Enter your negative prompt (avoid unwanted elements)...';
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected author-state object');
    return value as Obj;
}
function beforeProject() {
    const raw = (capture as unknown as Capture).cases.find(value => value.case === 'local_single')?.events.find(value => value.label === 'before_project');
    if (!raw) throw new Error('Missing captured three-view author-state baseline');
    return structuredClone(raw.body);
}
const actor = (value: Obj) => obj((value.characters as unknown[]).find(item => obj(item).id === 'c'));
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p' });
const panel = () => screen.getAllByRole('group', { name: '3. 头像（特写）' }).at(-1)!;
const fullBodyPanel = () => screen.getAllByRole('group', { name: '1. 主体资产（全身图）' }).at(-1)!;
const generate = () => within(panel()).getByRole('button', { name: '生成' });
const prompt = () => within(panel()).getByPlaceholderText('Enter prompt description...');
const editPrompt = (value: string) => fireEvent.change(prompt(), { target: { value } });
const editNegative = (value: string) => fireEvent.change(screen.getByPlaceholderText(negativePlaceholder), { target: { value } });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const legacyPosts = () => vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/assets/generate'));
const motionPosts = () => vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/assets/generate_motion_ref'));
const journals = () => listStudioAssetImageRequests(owner());
let server: Obj;
function publish() {
    server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
    useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
}
function install(model = 'gpt-image-2') {
    server = beforeProject(); obj(server.model_settings).i2i_model = model;
    actor(server).headshot_prompt = 'Saved headshot author prompt';
    useProjectStore.setState({ currentProject: project('p', 0), projects: [project('p', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    publish();
}
function page() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Alice' })); return view;
}
function originalHeadshot() {
    return beginStudioAssetImageRequest({ ...owner(), input: {
        asset_id: 'c', asset_type: 'character', generation_type: 'headshot', model_name: 'gpt-image-2',
        reference_variant_id: 'selected-c', reference_image_url: 'uploads/selected-c.png',
        prompt: 'Original headshot still unknown', apply_style: false, batch_size: 1,
    } as unknown as StudioAssetImageRequestInput, idFactory: () => 'original-headshot', now: () => 7 }).journal;
}
function noImageSubmission() { expect(posts()).toHaveLength(0); expect(legacyPosts()).toHaveLength(0); }
function queueLock() {
    const gate = deferred<void>(); let entered = false;
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => {
            entered = true; await gate.promise; return callback({});
        },
    } });
    return { gate, entered: () => entered };
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}),
    } });
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : structuredClone(server) }));
    vi.mocked(axios.post).mockRejectedValue(new Error('Unexpected legacy or Motion submission'));
    vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => {
        if (init?.method === 'POST') throw new Error('Deliberately lost headshot acknowledgement');
        return new Response(JSON.stringify({ detail: 'Original receipt remains unconfirmed' }), { status: 404 });
    });
    install();
});
afterEach(() => { clearRuntimeEnvironment(); vi.restoreAllMocks(); });

describe('static headshot author entry', () => {
    it.each(['gpt-image-2', 'gpt-image/gpt-image-2#image'])(
        'uses saved %s I2I and exact raw selected C only after explicit negative clearing', async model => {
            install(model);
            const chosen = (obj(actor(server).full_body_asset).variants as Obj[]).find(value => value.id === 'selected-c')!;
            chosen.media_refs = { url: 'uploads/selected-c.png' };
            chosen.url = 'https://signed.example.invalid/selected-c.png?expires=synthetic'; publish();
            page();
            expect((screen.getByPlaceholderText(negativePlaceholder) as HTMLTextAreaElement).value).toContain('bad anatomy');
            expect(generate()).toBeDisabled(); noImageSubmission();
            fireEvent.click(within(panel()).getByRole('button', { name: '清空负向提示词' }));
            editPrompt('My exact static headshot draft');
            fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
            fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
            const sent = JSON.parse(String(posts()[0][1]?.body));
            expect(sent).toMatchObject({ asset_id: 'c', asset_type: 'character', generation_type: 'headshot',
                model_name: model, reference_variant_id: 'selected-c', reference_image_url: 'uploads/selected-c.png',
                prompt: 'My exact static headshot draft', batch_size: 2, apply_style: true, negative_prompt: '',
                style_prompt: 'Soft cyan light, graphic ink.' });
            expect(sent).not.toHaveProperty('aspect_ratio'); expect(sent).not.toHaveProperty('seed'); expect(sent).not.toHaveProperty('watermark');
            expect(String(posts()[0][0])).toMatch(/\/projects\/p\/asset_image_requests$/);
            expect(legacyPosts()).toHaveLength(0); expect(motionPosts()).toHaveLength(0);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(sent);
            expect(useProjectStore.getState().currentProject?.model_settings?.t2i_model).toBe('wan2.7-image');
            expect(useProjectStore.getState().currentProject?.model_settings?.i2i_model).toBe(model);
            expect(obj(obj(server.art_direction).style_config).negative_prompt).toBe('Saved inherited negative remains unchanged.');
        });

    it.each(['draft ABA', 'source ABA', 'environment ABA'])(
        'does not POST an obsolete headshot after %s while the actual browser lock is queued', async change => {
            const view = page(); editNegative(''); editPrompt('Original headshot author draft');
            const lock = queueLock();
            try {
                fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
                if (change === 'draft ABA') { editPrompt('Later portrait draft'); editPrompt('Original headshot author draft'); }
                else if (change === 'environment ABA') act(() => {
                    clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
                });
                else {
                    vi.mocked(axios.post).mockImplementation(async (_url, raw) => {
                        const id = obj(raw).variant_id;
                        const next = structuredClone(server);
                        next.revision = Number(next.revision) + 1; obj(next.read_versions).project = next.revision;
                        obj(actor(next).full_body_asset).selected_id = id;
                        obj(actor(next).full_body).selected_image_id = id;
                        actor(next).full_body_image_url = id === 'first-b' ? 'uploads/first-b.png' : 'uploads/selected-c.png';
                        server = next; return { data: next };
                    });
                    fireEvent.click(within(fullBodyPanel()).getAllByAltText('Variant')[0]);
                    await waitFor(() => expect(useProjectStore.getState().currentProject?.characters[0].full_body_asset?.selected_id).toBe('first-b'));
                    fireEvent.click(within(fullBodyPanel()).getAllByAltText('Variant')[1]);
                    await waitFor(() => expect(useProjectStore.getState().currentProject?.characters[0].full_body_asset?.selected_id).toBe('selected-c'));
                    const selections = vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/assets/variant/select'));
                    expect(selections.map(([, body]) => obj(body).variant_id)).toEqual(['first-b', 'selected-c']);
                }
                await act(async () => { lock.gate.resolve(); });
                noImageSubmission(); expect(journals()).toHaveLength(0);
            } finally {
                view.unmount(); clearRuntimeEnvironment(); await act(async () => { lock.gate.resolve(); });
            }
        });

    it.each(['dangling', 'duplicate-same', 'duplicate-different'])(
        'does not fall back from a %s saved selection even with a legacy URL', async kind => {
            const target = actor(server), full = obj(target.full_body_asset), modern = obj(target.full_body);
            if (kind === 'dangling') { full.selected_id = 'missing'; modern.selected_image_id = 'missing'; }
            else {
                const variants = full.variants as Obj[], selected = variants.find(value => value.id === 'selected-c')!;
                variants.push({ ...selected, url: kind === 'duplicate-same' ? selected.url : 'uploads/first-b.png' });
            }
            target.full_body_image_url = 'uploads/legacy-a.png'; publish(); page(); editNegative('');
            await act(async () => { await Promise.resolve(); });
            expect(generate()).toBeDisabled(); fireEvent.click(generate()); noImageSubmission();
            expect(journals()).toHaveLength(0);
        });

    it('rechecks an unseen original headshot journal before dispatching the available other-model legacy path', async () => {
        install('wan2.7-image-pro'); const held = deferred<{ data: Obj }>();
        vi.mocked(axios.post).mockImplementation(async () => held.promise);
        const view = page();
        try {
            await act(async () => { await Promise.resolve(); });
            expect(generate()).toBeEnabled(); expect(gets()).toHaveLength(0);
            // No render between another observed session's write and the real click.
            const original = originalHeadshot(); fireEvent.click(generate()); noImageSubmission();
            await waitFor(() => expect(gets()).toHaveLength(1));
            expect(String(gets()[0][0])).toMatch(/\/asset_image_requests\/original-headshot$/);
            expect(generate()).toBeDisabled(); expect(journals()).toHaveLength(1);
            expect(journals()[0].request).toEqual(original.request); noImageSubmission();
        } finally {
            view.unmount(); clearRuntimeEnvironment(); await act(async () => { held.resolve({ data: structuredClone(server) }); });
        }
    });
});

describe('unmigrated headshot boundaries', () => {
    it.each(['unique remote selected', 'other saved I2I'])(
        'preserves explicit %s legacy dispatch without creating an image journal', async source => {
            install(source === 'other saved I2I' ? 'wan2.7-image-pro' : 'gpt-image-2');
            const target = actor(server), full = obj(target.full_body_asset);
            if (source === 'unique remote selected') {
                const selected = (full.variants as Obj[]).find(value => value.id === 'selected-c')!;
                selected.url = 'https://example.invalid/author-selected-c.png'; delete selected.media_refs;
            }
            publish(); const held = deferred<{ data: Obj }>();
            vi.mocked(axios.post).mockImplementation(async () => held.promise);
            const view = page();
            try {
                await waitFor(() => expect(generate()).toBeEnabled());
                editPrompt('Explicit legacy portrait author intent'); fireEvent.click(generate());
                await waitFor(() => expect(legacyPosts()).toHaveLength(1));
                expect(legacyPosts()[0][1]).toMatchObject({ asset_id: 'c', asset_type: 'character', generation_type: 'headshot',
                    prompt: 'Explicit legacy portrait author intent', model_name: 'wan2.7-image', batch_size: 1 });
                expect(posts()).toHaveLength(0); expect(motionPosts()).toHaveLength(0); expect(journals()).toHaveLength(0);
            } finally {
                view.unmount(); clearRuntimeEnvironment(); await act(async () => { held.resolve({ data: structuredClone(server) }); });
            }
        });

    it('keeps head_shot Motion on Core video requests without creating a static headshot journal', async () => {
        const target = actor(server), old = { id: 'author-face-old', url: 'uploads/author-face-old.png', created_at: 7 };
        target.headshot_asset = { selected_id: old.id, variants: [old] };
        Object.assign(obj(target.head_shot), { selected_image_id: old.id, image_variants: [old] });
        target.headshot_image_url = target.avatar_url = old.url; publish();
        // Headshot/local-author-state unit variant of the existing Motion capture,
        // as in VaultMotionRequests; no new captured wire or completion is claimed.
        const captured = (motionCapture as unknown as Capture).cases[0].events.find(event => event.label === 'eligibility');
        if (!captured) throw new Error('Missing five-second Motion eligibility');
        const qualification: Obj = { ...structuredClone(captured.body), project_id: 'p', asset_id: 'c', asset_type: 'head_shot',
            owner: { kind: 'script', id: 'p' }, selected_image_id: old.id, source_kind: 'local_path' };
        const held = deferred<Response>();
        vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
            if (new URL(String(url), 'http://localhost').pathname === '/projects/p/motion_ref_requests') {
                if (init?.method === 'POST') return held.promise;
                return new Response(JSON.stringify(qualification), { status: 200 });
            }
            return new Response(JSON.stringify({ detail: 'Original receipt remains unconfirmed' }), { status: 404 });
        });
        const view = page();
        try {
            fireEvent.click(within(panel()).getByRole('button', { name: '动态' }));
            fireEvent.change(within(panel()).getByPlaceholderText('Describe the motion you want...'), { target: { value: 'Keep my existing portrait motion intent' } });
            await waitFor(() => expect(within(panel()).getByRole('button', { name: 'Generate Motion Reference' })).toBeEnabled());
            fireEvent.click(within(panel()).getByRole('button', { name: 'Generate Motion Reference' }));
            await waitFor(() => expect(posts()).toHaveLength(1));
            expect(new URL(String(posts()[0][0]), 'http://localhost').pathname).toBe('/projects/p/motion_ref_requests');
            const sent = JSON.parse(String(posts()[0][1]?.body));
            expect(sent).toEqual({ asset_id: 'c', asset_type: 'head_shot',
                prompt: 'Keep my existing portrait motion intent', duration: 5, batch_size: 1,
                expected_target: qualification.expected_target, client_request_id: expect.any(String) });
            expect(gets().some(([url]) => new URL(String(url), 'http://localhost').searchParams.get('asset_type') === 'head_shot')).toBe(true);
            const motionJournals = listStudioMotionRequests({ environment: requireActiveRuntimeEnvironment(),
                projectId: 'p', assetId: 'c', assetOwner: { kind: 'script', id: 'p' } });
            expect(motionJournals).toHaveLength(1);
            expect(motionJournals[0].request).toEqual(sent);
            expect(motionJournals[0].selectedImageId).toBe(old.id);
            expect(legacyPosts()).toHaveLength(0); expect(motionPosts()).toHaveLength(0); expect(journals()).toHaveLength(0);
        } finally {
            view.unmount(); clearRuntimeEnvironment();
            await act(async () => { held.reject(new Error('Deliberately lost Motion acknowledgement')); });
        }
    });
});

// Synthetic author-state changes only; no completed backend receipt is manufactured.
describe('local GPT derived fallback identity', () => {
    const channel = 'headshot' as 'three_view' | 'headshot';
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
