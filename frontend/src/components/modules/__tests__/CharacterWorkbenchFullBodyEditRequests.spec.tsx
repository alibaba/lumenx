// Real Vault/Workbench/hooks/store; only transport boundaries are mocked.
// A captured three-view BEFORE project supplies ordinary author state. Uploaded
// source edits below are explicit UI input fixtures, never successful receipts.
// Every Core POST loses its ACK; recovery GET is 404. Actual full-body completion
// and dual-history readback need the separate backend-wire capture gate.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests } from '@/lib/studioAssetImageRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred, project } from './frame-command-fixtures';
import capture from '@/__tests__/fixtures/studio-three-view-wire.json';
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
    const before = (capture as unknown as Capture).cases.find(value => value.case === 'local_single')?.events.find(value => value.label === 'before_project');
    if (!before) throw new Error('Missing captured author-state baseline');
    return structuredClone(before.body);
}
const actor = (value: Obj) => obj((value.characters as unknown[]).find(item => obj(item).id === 'c'));
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p' });
const panel = () => screen.getAllByRole('group', { name: '1. 主体资产（全身图）' }).at(-1)!;
const generate = () => within(panel()).getByRole('button', { name: '生成' });
const prompt = () => within(panel()).getByPlaceholderText('Enter prompt description...');
const editPrompt = (value: string) => fireEvent.change(prompt(), { target: { value } });
const editNegative = (value: string) => fireEvent.change(screen.getByPlaceholderText(negativePlaceholder), { target: { value } });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const legacyPosts = () => vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/assets/generate'));
const motionPosts = () => vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/assets/generate_motion_ref'));
const journals = () => listStudioAssetImageRequests(owner());
const noImageSubmission = () => { expect(posts()).toHaveLength(0); expect(legacyPosts()).toHaveLength(0); };
let server: Obj;
const source = () => (obj(actor(server).three_view_asset).variants as Obj[])[0];
function publish() {
    server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
    useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server));
}
function install(model = 'gpt-image-2') {
    server = beforeProject(); obj(server.model_settings).i2i_model = model;
    obj(obj(server.art_direction).style_config).negative_prompt = '';
    const target = actor(server), three = obj(target.three_view_asset);
    const uploaded = { ...(three.variants as Obj[])[0], is_uploaded_source: true, upload_type: 'three_view',
        media_refs: { url: 'uploads/three-view-old-0.png' }, url: 'https://signed.example.invalid/three-view-old-0.png?expires=synthetic' };
    three.variants = [uploaded, (three.variants as Obj[])[1]];
    obj(target.three_views).image_variants = structuredClone(three.variants);
    // selected_id deliberately points at the other, non-uploaded variant.
    const head = { id: 'uploaded-headshot', url: 'uploads/uploaded-headshot.png', created_at: 9,
        is_uploaded_source: true, upload_type: 'headshot' };
    target.headshot_asset = { selected_id: head.id, variants: [head] };
    Object.assign(obj(target.head_shot), { selected_image_id: head.id, image_variants: [head] });
    target.headshot_image_url = target.avatar_url = head.url;
    target.full_body_prompt = 'Saved full-body author prompt';
    useProjectStore.setState({ currentProject: project('p', 0), projects: [project('p', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    publish();
}
function page() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Alice' })); return view;
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
function originalEdit() {
    return beginStudioAssetImageRequest({ ...owner(), input: {
        asset_id: 'c', asset_type: 'character', generation_type: 'full_body', model_name: 'gpt-image-2',
        reference_variant_id: 'three-view-old-0', reference_image_url: 'uploads/three-view-old-0.png',
        prompt: 'Original full-body request still unknown', apply_style: false, negative_prompt: '', aspect_ratio: '9:16', batch_size: 1,
    }, idFactory: () => 'original-full-body', now: () => 7 }).journal;
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
        if (init?.method === 'POST') throw new Error('Deliberately lost full-body acknowledgement');
        return new Response(JSON.stringify({ detail: 'Original receipt remains unconfirmed' }), { status: 404 });
    });
    install();
});
afterEach(() => { clearRuntimeEnvironment(); vi.restoreAllMocks(); });

describe('reference full-body author entry', () => {
    it('uses the local mother selected raw body in one original full-body request and journal', async () => {
        const child = actor(server), mother = structuredClone(child);
        child.base_character_id = 'mother';
        mother.id = 'mother'; mother.name = 'Mother Alice'; mother.base_character_id = null;
        const take = { id: 'mother-take', url: 'https://signed.example.invalid/mother.png?expires=synthetic',
            media_refs: { url: 'assets/characters/mother_fullbody_take.png' }, created_at: 1, is_favorited: true };
        mother.full_body_asset = { selected_id: take.id, variants: [take] };
        Object.assign(obj(mother.full_body), { selected_image_id: take.id, image_variants: [structuredClone(take)] });
        (server.characters as unknown[]).push(mother);
        const originalMother = structuredClone(mother);
        publish(); page(); editNegative(''); editPrompt('Variant pose from the selected mother'); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        const sent = JSON.parse(String(posts()[0][1]?.body));
        expect(sent).toMatchObject({ asset_id: 'c', asset_type: 'character', generation_type: 'full_body',
            model_name: 'gpt-image-2', reference_variant_id: 'mother-take',
            reference_image_url: 'assets/characters/mother_fullbody_take.png',
            prompt: 'Variant pose from the selected mother', aspect_ratio: '9:16', batch_size: 1,
            negative_prompt: '', apply_style: true });
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(sent);
        expect(legacyPosts()).toHaveLength(0); expect(motionPosts()).toHaveLength(0);
        expect(mother).toEqual(originalMother);
        expect(obj(child.full_body).selected_video_id).toBe('motion-old');
    });
    it.each(['gpt-image-2', 'gpt-image/gpt-image-2#image'])(
        'uses saved %s I2I, first uploaded three-view raw reference and saved portrait in one original request', async model => {
            install(model); page();
            expect((screen.getByPlaceholderText(negativePlaceholder) as HTMLTextAreaElement).value).toContain('bad anatomy');
            expect(generate()).toBeDisabled(); noImageSubmission();
            fireEvent.click(within(panel()).getByRole('button', { name: '清空负向提示词' }));
            editPrompt('My exact full-body author draft'); fireEvent.click(within(panel()).getByRole('button', { name: 'x2' }));
            fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
            const sent = JSON.parse(String(posts()[0][1]?.body));
            expect(sent).toMatchObject({ asset_id: 'c', asset_type: 'character', generation_type: 'full_body', model_name: model,
                reference_variant_id: 'three-view-old-0', reference_image_url: 'uploads/three-view-old-0.png',
                prompt: 'My exact full-body author draft', batch_size: 2, apply_style: true, negative_prompt: '',
                aspect_ratio: '9:16', style_prompt: 'Soft cyan light, graphic ink.' });
            expect(sent).not.toHaveProperty('seed'); expect(sent).not.toHaveProperty('watermark');
            expect(String(posts()[0][0])).toMatch(/\/projects\/p\/asset_image_requests$/);
            expect(legacyPosts()).toHaveLength(0); expect(motionPosts()).toHaveLength(0);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(sent);
            expect(useProjectStore.getState().currentProject?.model_settings?.t2i_model).toBe('wan2.7-image');
            expect(obj(actor(server).three_view_asset).selected_id).toBe('three-view-old-1');
            expect(obj(actor(server).headshot_asset).selected_id).toBe('uploaded-headshot');
            expect(obj(actor(server).full_body).selected_video_id).toBe('motion-old');
        });

    it('uses uploaded headshot only when no uploaded three-view exists', async () => {
        source().is_uploaded_source = false;
        (obj(actor(server).three_views).image_variants as Obj[])[0].is_uploaded_source = false;
        publish(); page(); editNegative(''); editPrompt('Headshot-backed full-body author draft'); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        const sent = JSON.parse(String(posts()[0][1]?.body));
        expect(sent).toMatchObject({ reference_variant_id: 'uploaded-headshot', reference_image_url: 'uploads/uploaded-headshot.png' });
        expect(journals()[0].request).toEqual(sent); expect(legacyPosts()).toHaveLength(0);
    });

    it.each(['1:1', '16:9'])('preserves saved %s without imposing the portrait display ratio on the request', async ratio => {
        obj(server.model_settings).character_aspect_ratio = ratio; publish(); page(); editNegative(''); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body)).aspect_ratio).toBe(ratio); expect(legacyPosts()).toHaveLength(0);
    });

    it('requires explicit removal of both own and effective style negative without mutating saved style', async () => {
        obj(obj(server.art_direction).style_config).negative_prompt = 'Keep saved style negative'; publish(); page();
        expect(generate()).toBeDisabled();
        fireEvent.click(within(panel()).getByRole('button', { name: '清空负向提示词' }));
        expect(generate()).toBeDisabled(); noImageSubmission();
        fireEvent.click(within(panel()).getByRole('button', { name: '本次不使用风格提示词' }));
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ negative_prompt: '', apply_style: false });
        expect(obj(obj(server.art_direction).style_config).negative_prompt).toBe('Keep saved style negative');
        expect(legacyPosts()).toHaveLength(0);
    });

    it.each(['raw-null', 'raw-empty', 'blank', 'blob', 'data', 'duplicate-same', 'duplicate-other'])(
        'does not fall back to a selected or lower-priority source when first uploaded source is %s', async kind => {
            const first = source();
            if (kind.startsWith('duplicate')) (obj(actor(server).three_view_asset).variants as Obj[]).push({
                ...first, url: kind === 'duplicate-same' ? first.url : 'uploads/different.png',
            });
            else obj(first.media_refs).url = kind === 'raw-null' ? null : kind === 'raw-empty' ? '' : kind === 'blank' ? ' '
                : kind === 'blob' ? 'blob:https://example.invalid/blob-id' : 'data:image/png;base64,aGVsbG8=';
            publish(); page(); editNegative('');
            await act(async () => { await Promise.resolve(); });
            expect(generate()).toBeDisabled(); fireEvent.click(generate()); noImageSubmission(); expect(journals()).toHaveLength(0);
        });

    it.each(['draft ABA', 'source ABA', 'environment ABA'])(
        'does not POST an obsolete reference full-body request after %s while the browser lock is queued', async change => {
            const view = page(); editNegative(''); editPrompt('Original full-body author draft'); const lock = queueLock();
            try {
                fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
                if (change === 'draft ABA') { editPrompt('Later author draft'); editPrompt('Original full-body author draft'); }
                else if (change === 'environment ABA') act(() => {
                    clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
                });
                else {
                    // Explicitly published source edits use monotonically increasing revisions.
                    // This checks stale-source ownership, not isolated epoch-only causality.
                    act(() => { obj(source().media_refs).url = 'uploads/source-later.png'; publish(); });
                    act(() => { obj(source().media_refs).url = 'uploads/three-view-old-0.png'; publish(); });
                }
                await act(async () => { lock.gate.resolve(); });
                noImageSubmission(); expect(journals()).toHaveLength(0);
            } finally {
                view.unmount(); clearRuntimeEnvironment(); await act(async () => { lock.gate.resolve(); });
            }
        });

    it('rechecks an unseen original full-body journal before the other-model legacy click', async () => {
        install('wan2.7-image-pro'); const held = deferred<{ data: Obj }>(); vi.mocked(axios.post).mockImplementation(async () => held.promise);
        const view = page();
        try {
            await waitFor(() => expect(generate()).toBeEnabled()); expect(gets()).toHaveLength(0);
            // No render between another session's journal write and dispatch.
            const original = originalEdit(); fireEvent.click(generate()); noImageSubmission();
            await waitFor(() => expect(gets()).toHaveLength(1));
            expect(String(gets()[0][0])).toMatch(/\/asset_image_requests\/original-full-body$/);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
            expect(generate()).toBeDisabled(); noImageSubmission();
        } finally {
            view.unmount(); clearRuntimeEnvironment(); await act(async () => { held.resolve({ data: structuredClone(server) }); });
        }
    });
});

describe('unmigrated full-body reference compatibility', () => {
    it.each(['other saved I2I', 'base character', 'remote first upload', 'object-key first upload'])(
        'retains the explicit %s legacy transport boundary without minting an image journal', async kind => {
            install(kind === 'other saved I2I' ? 'wan2.7-image-pro' : 'gpt-image-2');
            if (kind === 'base character') actor(server).base_character_id = 'base-character';
            if (kind === 'remote first upload') obj(source().media_refs).url = 'https://example.invalid/original.png';
            if (kind === 'object-key first upload') obj(source().media_refs).url = 'lumenx/characters/original.png';
            publish(); const held = deferred<{ data: Obj }>(); vi.mocked(axios.post).mockImplementation(async () => held.promise);
            const view = page();
            try {
                await waitFor(() => expect(generate()).toBeEnabled()); editPrompt('Explicit retained legacy full-body intent'); fireEvent.click(generate());
                await waitFor(() => expect(legacyPosts()).toHaveLength(1));
                expect(legacyPosts()[0][1]).toMatchObject({ asset_id: 'c', asset_type: 'character', generation_type: 'full_body',
                    prompt: 'Explicit retained legacy full-body intent', model_name: 'wan2.7-image', batch_size: 1 });
                expect(posts()).toHaveLength(0); expect(motionPosts()).toHaveLength(0); expect(journals()).toHaveLength(0);
            } finally {
                view.unmount(); clearRuntimeEnvironment(); await act(async () => { held.resolve({ data: structuredClone(server) }); });
            }
        });
});
