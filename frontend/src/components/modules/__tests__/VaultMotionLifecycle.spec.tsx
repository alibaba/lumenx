import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import type { StudioMotionAssetOwner, StudioMotionRequestBody } from '@/lib/api';
import { beginStudioMotionRequest } from '@/lib/studioMotionRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, project } from './frame-command-fixtures';
import capture from '@/__tests__/fixtures/studio-motion-request-wire.json';
import duration5 from '@/__tests__/fixtures/studio-motion-duration5-wire.json';
import terminal from '@/__tests__/fixtures/studio-motion-terminal-followup-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Obj = Record<string, unknown>;
type Event = { label: string; body: Obj; request_body?: StudioMotionRequestBody; status_code: number };
type Capture = { cases: Array<{ case: string; events: Event[] }> };
function actual(name: string, label: string): Event {
    const all = [...(capture as unknown as Capture).cases, ...(duration5 as unknown as Capture).cases,
        ...(terminal as unknown as Capture).cases];
    const found = all.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual HTTP evidence: ${name}/${label}`);
    return structuredClone(found);
}
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected an object in the actual fixture');
    return value as Obj;
}
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(done => { resolve = done; });
    return { promise, resolve };
}
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const panel = () => screen.getAllByRole('group', { name: '1. 主体资产（全身图）' }).at(-1)!;
const generate = () => within(panel()).getByRole('button', { name: 'Generate Motion Reference' });
const journalBytes = () => Object.keys(localStorage).filter(key => key.startsWith('lumenx.studioMotionRequest.v1:'))
    .sort().map(key => ({ key, raw: localStorage.getItem(key) }));
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const mainVideo = () => panel().querySelector('video')!;
const prompt = () => within(panel()).getByPlaceholderText('Describe the motion you want...');
let server: Obj;
function install(name = 'lifecycle_script_duration5', seed = true, projection = 'completed_enriched_project_projection') {
    const source = actual(name, projection).body;
    server = { ...ack([], 'project', source.revision as number), ...source,
        model_settings: { t2i_model: 'wan2.6-t2i', i2v_model: 'kling-v1' } };
    useProjectStore.setState({ currentProject: project('project', 0), projects: [project('project', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('project', server);
    if (seed) seedJournal(name);
}
function seedJournal(name: string, key?: string, admission = 'accepted_post') {
    const { client_request_id, ...input } = actual(name, admission).request_body!;
    const identity = obj(actual(name, admission).body.request);
    return beginStudioMotionRequest({
        environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' }),
        projectId: 'project', assetId: 'hero', assetOwner: { kind: identity.owner_kind, id: identity.owner_id } as StudioMotionAssetOwner,
        input, selectedImageId: 'selected-image', idFactory: () => key ?? client_request_id, now: () => key ? 8 : 7,
    });
}
function page() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Hero' }));
    fireEvent.click(within(panel()).getByRole('button', { name: '动态' }));
    return view;
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}),
    } });
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : server }));
    vi.mocked(axios.post).mockRejectedValue(new Error('Legacy generation is outside this completion flow'));
});
afterEach(() => { clearRuntimeEnvironment(); vi.restoreAllMocks(); });

const sequence = 'completed_history_then_explicit_followup';
function installFirstHistory() {
    install(sequence, false, 'first_unprojected_project');
    seedJournal(sequence, undefined, 'first_accepted_post');
    return actual(sequence, 'first_completed_unprojected_get').body;
}
const originalKeyReads = () => vi.mocked(appAccessFetch).mock.calls.filter(([url, init]) => init?.method === 'GET'
    && String(url).endsWith('/motion_ref_requests/motion-request'));
const followupReads = () => vi.mocked(appAccessFetch).mock.calls.filter(([url, init]) => init?.method === 'GET'
    && String(url).endsWith('/motion_ref_requests/explicit-followup'));

it('keeps proven history through a separately clicked new key, active polling and exact second completion', async () => {
    const original = installFirstHistory(); let submitted = false, complete = false;
    // The client identity is deterministic for this actual HTTP capture; no receipt fields are rewritten.
    vi.spyOn(globalThis.crypto, 'randomUUID').mockImplementation(() => 'explicit-followup' as ReturnType<Crypto['randomUUID']>);
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        const path = new URL(String(url), 'http://localhost').pathname;
        if (init?.method === 'POST') {
            expect(path).toBe('/projects/project/motion_ref_requests');
            expect(JSON.parse(String(init.body))).toEqual(actual(sequence, 'second_accepted_post').request_body);
            submitted = true;
            server = { ...server, ...actual(sequence, 'second_active_project').body };
            return response(actual(sequence, 'second_accepted_post').body);
        }
        if (path.endsWith('/motion_ref_requests')) return response(actual(sequence, 'second_eligibility').body);
        if (path.endsWith('/explicit-followup')) return response(actual(sequence, complete
            ? 'second_completed_get' : 'second_observed_running_get').body);
        expect(path).toBe('/projects/project/motion_ref_requests/motion-request');
        return response(submitted ? actual(sequence, complete ? 'old_after_second_complete_get' : 'old_after_followup_get').body : original);
    });
    page(); fireEvent.click(await screen.findByRole('button', { name: '保留为历史' }));
    await waitFor(() => expect(generate()).not.toBeDisabled());
    const originalBytes = journalBytes()[0], oldReadsAfterRetention = originalKeyReads().length;
    expect(posts()).toHaveLength(0);
    fireEvent.change(prompt(), { target: { value: 'A later explicit slow rotation' } });
    fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
    await waitFor(() => expect(followupReads().length).toBeGreaterThanOrEqual(1), { timeout: 4000 });
    expect(originalKeyReads()).toHaveLength(oldReadsAfterRetention);
    expect(journalBytes()).toHaveLength(2); expect(generate()).toBeDisabled();
    complete = true; server = { ...server, ...actual(sequence, 'second_completed_project').body };
    fireEvent.click(screen.getByRole('button', { name: '查看原任务' }));
    await waitFor(() => expect(journalBytes()).toEqual([originalBytes]));
    expect(generate()).not.toBeDisabled(); expect(posts()).toHaveLength(1);
    expect(mainVideo()).toHaveAttribute('src', expect.stringContaining('video/history.mp4'));
    expect(screen.getByText('已保留为历史；当前编辑不变。')).toBeInTheDocument();
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
}, 10000);

it('requires another original-key read and explicit history decision after reopening', async () => {
    const original = installFirstHistory();
    vi.mocked(appAccessFetch).mockImplementation(async () => response(original));
    const first = page(); fireEvent.click(await screen.findByRole('button', { name: '保留为历史' }));
    await waitFor(() => expect(generate()).not.toBeDisabled());
    const bytes = journalBytes(), reads = originalKeyReads().length;
    first.unmount(); page();
    await waitFor(() => expect(originalKeyReads().length).toBeGreaterThan(reads));
    const retain = await screen.findByRole('button', { name: '保留为历史' });
    expect(generate()).toBeDisabled(); expect(journalBytes()).toEqual(bytes);
    fireEvent.click(retain); await waitFor(() => expect(generate()).not.toBeDisabled());
    expect(journalBytes()).toEqual(bytes); expect(posts()).toHaveLength(0);
});

it('does not let an old A-to-B-to-A confirmation or its finally clear a newer active operation', async () => {
    const original = installFirstHistory(), oldHeld = deferred<Response>(), newHeld = deferred<Response>(); let reads = 0;
    vi.mocked(appAccessFetch).mockImplementation(async () => {
        reads += 1;
        if (reads === 2) return oldHeld.promise;
        if (reads === 4) return newHeld.promise;
        return response(original);
    });
    page(); fireEvent.click(await screen.findByRole('button', { name: '保留为历史' }));
    await waitFor(() => expect(reads).toBe(2)); const bytes = journalBytes();
    act(() => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
    });
    await waitFor(() => expect(reads).toBe(3));
    const laterRetain = await screen.findByRole('button', { name: '保留为历史' });
    await waitFor(() => expect(laterRetain).not.toBeDisabled());
    fireEvent.click(laterRetain); await waitFor(() => expect(reads).toBe(4));
    expect(laterRetain).toBeDisabled();
    await act(async () => { oldHeld.resolve(response(original)); });
    expect(generate()).toBeDisabled(); expect(laterRetain).toBeDisabled();
    expect(screen.queryByText('已保留为历史；当前编辑不变。')).not.toBeInTheDocument();
    expect(journalBytes()).toEqual(bytes);
    await act(async () => { newHeld.resolve(response(original)); });
    await waitFor(() => expect(generate()).not.toBeDisabled());
    expect(screen.getByText('已保留为历史；当前编辑不变。')).toBeInTheDocument();
    expect(journalBytes()).toEqual(bytes); expect(posts()).toHaveLength(0);
});


const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/project'));
const eligibilityReads = () => vi.mocked(appAccessFetch).mock.calls.filter(([url, init]) => init?.method === 'GET'
    && new URL(String(url), 'http://localhost').pathname.endsWith('/motion_ref_requests'));
function advanceServerProject() {
    // Ordinary project drift only. Captured request/eligibility/receipt proof is unchanged.
    server = structuredClone(server);
    server.revision = Number(server.revision) + 1;
    obj(server.read_versions).project = server.revision;
    return obj((server.characters as unknown[])[0]);
}

it.each(['selected source', 'background revision'] as const)(
    'refreshes a newer %s on the first click and admits only a separate second click', async drift => {
        const original = installFirstHistory();
        server = structuredClone(server);
        const character = obj((server.characters as unknown[])[0]), unit = obj(character.full_body);
        const selected = obj((unit.image_variants as unknown[])[0]);
        const previous = { id: 'previous-visible-source', url: 'assets/previous-visible-source.png', created_at: 1 };
        unit.image_variants = drift === 'selected source' ? [previous, selected] : [selected];
        unit.selected_image_id = drift === 'selected source' ? previous.id : selected.id;
        character.full_body_asset = { selected_id: unit.selected_image_id, variants: unit.image_variants };
        character.full_body_image_url = drift === 'selected source' ? previous.url : selected.url;
        useProjectStore.getState().applyProjectReadSnapshot('project', server);
        vi.spyOn(globalThis.crypto, 'randomUUID').mockImplementation(() => 'explicit-followup' as ReturnType<Crypto['randomUUID']>);
        vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
            const path = new URL(String(url), 'http://localhost').pathname;
            if (init?.method === 'POST') {
                expect(path).toBe('/projects/project/motion_ref_requests');
                expect(JSON.parse(String(init.body))).toEqual(actual(sequence, 'second_accepted_post').request_body);
                return response(actual(sequence, 'second_accepted_post').body);
            }
            if (path.endsWith('/motion_ref_requests')) return response(actual(sequence, 'second_eligibility').body);
            if (path.endsWith('/explicit-followup')) return response(actual(sequence, 'second_observed_running_get').body);
            expect(path).toBe('/projects/project/motion_ref_requests/motion-request');
            return response(original);
        });
        page(); fireEvent.click(await screen.findByRole('button', { name: '保留为历史' }));
        await waitFor(() => expect(generate()).not.toBeDisabled());
        fireEvent.change(prompt(), { target: { value: 'A later explicit slow rotation' } });
        const bytes = journalBytes(), retainedReads = originalKeyReads().length;
        const fresh = advanceServerProject();
        obj(fresh.full_body).selected_image_id = selected.id;
        obj(fresh.full_body_asset).selected_id = selected.id;
        fresh.full_body_image_url = selected.url;
        fresh.status = 'completed';

        fireEvent.click(generate());
        await waitFor(() => expect(projectReads()).toHaveLength(1));
        await waitFor(() => expect(generate()).not.toBeDisabled());
        expect(useProjectStore.getState().currentProject?.revision).toBe(server.revision);
        expect(useProjectStore.getState().currentProject?.characters[0].full_body?.selected_image_id).toBe('selected-image');
        expect(prompt()).toHaveValue('A later explicit slow rotation');
        expect(journalBytes()).toEqual(bytes); expect(originalKeyReads()).toHaveLength(retainedReads);
        expect(eligibilityReads()).toHaveLength(0); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
        expect(screen.getByText('已保留为历史；当前编辑不变。')).toBeInTheDocument();
        expect(screen.getByText('素材或编辑内容已变化，尚未开始生成。请确认当前内容后重新点击。')).toBeInTheDocument();
        expect(mainVideo()).toHaveAttribute('src', expect.stringContaining('video/history.mp4'));
        fireEvent.click(within(panel()).getByRole('button', { name: 'Static' }));
        expect(within(panel()).getByAltText('Selected Variant')).toHaveAttribute('src', String(selected.url));
        fireEvent.click(within(panel()).getByRole('button', { name: '动态' }));
        expect(prompt()).toHaveValue('A later explicit slow rotation');

        fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        await waitFor(() => expect(journalBytes().some(item => {
            const journal = JSON.parse(item.raw!);
            return journal.request.client_request_id === 'explicit-followup' && typeof journal.receiptIdentity === 'string';
        })).toBe(true));
        expect(projectReads()).toHaveLength(2); expect(eligibilityReads()).toHaveLength(1);
        expect(journalBytes()).toHaveLength(2);
        expect(journalBytes().find(item => item.key === bytes[0].key)).toEqual(bytes[0]);
        const next = JSON.parse(journalBytes().find(item => item.key !== bytes[0].key)!.raw!);
        expect(next.request).toEqual(actual(sequence, 'second_accepted_post').request_body);
        expect(next.selectedImageId).toBe('selected-image');
        expect(originalKeyReads()).toHaveLength(retainedReads);
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });

it.each(['full_body', 'headshot'] as const)(
    'preserves an explicitly empty %s motion draft when a newer project description is refreshed', async channel => {
        const original = installFirstHistory();
        if (channel === 'headshot') {
            // A real static source opens the normal Motion mode gate; do not bypass it.
            server = structuredClone(server);
            const character = obj((server.characters as unknown[])[0]);
            const headshot = { id: 'visible-headshot', url: 'assets/visible-headshot.png', created_at: 1 };
            character.headshot_image_url = headshot.url;
            character.headshot_asset = { selected_id: headshot.id, variants: [headshot] };
            useProjectStore.getState().applyProjectReadSnapshot('project', server);
        }
        vi.mocked(appAccessFetch).mockImplementation(async () => response(original));
        page(); fireEvent.click(await screen.findByRole('button', { name: '保留为历史' }));
        await waitFor(() => expect(generate()).not.toBeDisabled());
        const target = channel === 'full_body' ? panel()
            : screen.getAllByRole('group', { name: '3. 头像（特写）' }).at(-1)!;
        if (channel === 'headshot') fireEvent.click(within(target).getByRole('button', { name: '动态' }));
        const emptyDraft = () => within(target).getByPlaceholderText('Describe the motion you want...');
        fireEvent.change(emptyDraft(), { target: { value: '' } });
        expect(emptyDraft()).toHaveValue('');
        const bytes = journalBytes(), retainedReads = originalKeyReads().length;
        advanceServerProject().description = 'A refreshed description from the project source';
        fireEvent.click(generate());
        await waitFor(() => expect(projectReads()).toHaveLength(1));
        await waitFor(() => expect(generate()).not.toBeDisabled());
        expect(useProjectStore.getState().currentProject?.revision).toBe(server.revision);
        expect(emptyDraft()).toHaveValue('');
        expect(journalBytes()).toEqual(bytes); expect(originalKeyReads()).toHaveLength(retainedReads);
        expect(screen.getByText('已保留为历史；当前编辑不变。')).toBeInTheDocument();
        expect(eligibilityReads()).toHaveLength(0); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });


const artworkGroups = {
    full_body: '1. 主体资产（全身图）', three_view: '2. 三视图', headshot: '3. 头像（特写）',
} as const;
type ArtworkChannel = keyof typeof artworkGroups;
const artworkPanel = (channel: ArtworkChannel) => screen.getAllByRole('group', { name: artworkGroups[channel] }).at(-1)!;
const artworkPrompt = (channel: ArtworkChannel) => within(artworkPanel(channel)).getByPlaceholderText('Enter prompt description...');

it.each([
    ['full_body', 'A deliberately authored full-body draft'], ['full_body', ''],
    ['three_view', 'A deliberately authored three-view draft'], ['three_view', ''],
    ['headshot', 'A deliberately authored headshot draft'], ['headshot', ''],
] as const)('preserves the pre-existing %s artwork draft %j through a motion preflight source refresh', async (channel, draft) => {
    const original = installFirstHistory();
    server = structuredClone(server);
    const character = obj((server.characters as unknown[])[0]);
    for (const name of Object.keys(artworkGroups) as ArtworkChannel[]) character[`${name}_prompt`] = `Saved ${name} prompt`;
    useProjectStore.getState().applyProjectReadSnapshot('project', server);
    vi.mocked(appAccessFetch).mockImplementation(async () => response(original));
    page(); fireEvent.click(await screen.findByRole('button', { name: '保留为历史' }));
    await waitFor(() => expect(generate()).not.toBeDisabled());
    if (channel === 'full_body') fireEvent.click(within(panel()).getByRole('button', { name: 'Static' }));
    expect(artworkPrompt(channel)).toHaveValue(`Saved ${channel} prompt`);
    fireEvent.change(artworkPrompt(channel), { target: { value: draft } });
    expect(artworkPrompt(channel)).toHaveValue(draft);
    if (channel === 'full_body') fireEvent.click(within(panel()).getByRole('button', { name: '动态' }));
    const bytes = journalBytes(), retainedReads = originalKeyReads().length;
    const fresh = advanceServerProject();
    fresh.description = 'A new server description that also retriggers prompt initialization';
    fresh[`${channel}_prompt`] = `Newly saved ${channel} prompt on the server`;

    fireEvent.click(generate());
    await waitFor(() => expect(projectReads()).toHaveLength(1));
    await waitFor(() => expect(generate()).not.toBeDisabled());
    expect(useProjectStore.getState().currentProject?.revision).toBe(server.revision);
    if (channel === 'full_body') fireEvent.click(within(panel()).getByRole('button', { name: 'Static' }));
    expect(artworkPrompt(channel)).toHaveValue(draft);
    if (channel === 'full_body') fireEvent.click(within(panel()).getByRole('button', { name: '动态' }));
    expect(journalBytes()).toEqual(bytes); expect(originalKeyReads()).toHaveLength(retainedReads);
    expect(screen.getByText('已保留为历史；当前编辑不变。')).toBeInTheDocument();
    expect(eligibilityReads()).toHaveLength(0); expect(posts()).toHaveLength(0);
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
});


const savedMotionChannels = [['full_body', 'full_body'], ['headshot', 'head_shot']] as const;
const savedMotionDraft = (channel: 'full_body' | 'headshot') => within(artworkPanel(channel))
    .getByPlaceholderText('Describe the motion you want...');
function installSavedMotionPrompts(missingUnit?: 'full_body' | 'head_shot') {
    install('lifecycle_script_duration5', false);
    server = structuredClone(server);
    const character = obj((server.characters as unknown[])[0]);
    obj(character.full_body).video_prompt = 'Current author draft must survive the old result.';
    // Reuse the real headshot Motion gate's static-source setup; no permission/hook mock.
    const headshot = { id: 'visible-headshot', url: 'assets/visible-headshot.png', created_at: 1 };
    character.headshot_image_url = headshot.url;
    character.headshot_asset = { selected_id: headshot.id, variants: [headshot] };
    character.head_shot = { selected_image_id: headshot.id, image_variants: [headshot],
        selected_video_id: null, video_variants: [], video_prompt: 'Saved close-up speaking with a slight smile.' };
    character.video_prompt = 'Independent legacy R2V camera move; do not borrow it.';
    if (missingUnit) delete obj(character[missingUnit]).video_prompt;
    useProjectStore.getState().applyProjectReadSnapshot('project', server);
    vi.mocked(appAccessFetch).mockResolvedValue(response({ detail: 'No known request' }, 404));
    return character;
}

it.each(savedMotionChannels)(
    'restores the saved %s motion unit prompt after actual Vault close and reopen', async (channel, unit) => {
        const character = installSavedMotionPrompts(), saved = String(obj(character[unit]).video_prompt);
        const confirmed = useProjectStore.getState().currentProject;
        page();
        if (channel === 'headshot') fireEvent.click(within(artworkPanel(channel)).getByRole('button', { name: '动态' }));
        // This draft is intentionally not saved. Reopen must restore the confirmed value.
        fireEvent.change(savedMotionDraft(channel), { target: { value: 'Disposable local draft before closing' } });
        expect(savedMotionDraft(channel)).toHaveValue('Disposable local draft before closing');
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        await waitFor(() => expect(screen.queryAllByRole('group', { name: artworkGroups[channel] })).toHaveLength(0));
        fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Hero' }));
        fireEvent.click(within(artworkPanel(channel)).getByRole('button', { name: '动态' }));
        await waitFor(() => expect(savedMotionDraft(channel)).toHaveValue(saved));
        expect(useProjectStore.getState().currentProject).toBe(confirmed);
        expect(projectReads()).toHaveLength(0); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });

it.each(savedMotionChannels)(
    'keeps the template for legacy %s snapshots and reads a later saved unit prompt without borrowing R2V text', async (channel, unit) => {
        const character = installSavedMotionPrompts(unit);
        page();
        if (channel === 'headshot') fireEvent.click(within(artworkPanel(channel)).getByRole('button', { name: '动态' }));
        const template = (savedMotionDraft(channel) as HTMLTextAreaElement).value;
        expect(template).toContain(channel === 'full_body' ? 'Full-body character reference video.' : 'High-fidelity portrait video reference.');
        expect(template).toContain(String(character.description));
        expect(template).not.toContain(String(character.video_prompt));
        const sibling = unit === 'full_body' ? 'head_shot' : 'full_body';
        expect(template).not.toContain(String(obj(character[sibling]).video_prompt));

        const fresh = advanceServerProject(), saved = `New saved ${channel} motion prompt from a confirmed project read`;
        obj(fresh[unit]).video_prompt = saved;
        await act(async () => { useProjectStore.getState().applyProjectReadSnapshot('project', server); });
        expect(useProjectStore.getState().currentProject?.revision).toBe(server.revision);
        await waitFor(() => expect(savedMotionDraft(channel)).toHaveValue(saved));
        expect(projectReads()).toHaveLength(0); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });

it.each(savedMotionChannels)(
    'keeps authored %s motion text including an explicit empty draft across saved-prompt refreshes', async (channel, unit) => {
        installSavedMotionPrompts(); page();
        if (channel === 'headshot') fireEvent.click(within(artworkPanel(channel)).getByRole('button', { name: '动态' }));
        const bytes = journalBytes();
        for (const draft of ['A newer unsaved motion intention', '']) {
            fireEvent.change(savedMotionDraft(channel), { target: { value: draft } });
            expect(savedMotionDraft(channel)).toHaveValue(draft);
            const fresh = advanceServerProject(), saved = `Different saved motion prompt at revision ${server.revision}`;
            fresh.description = `Updated project description at revision ${server.revision}`;
            obj(fresh[unit]).video_prompt = saved;
            await act(async () => { useProjectStore.getState().applyProjectReadSnapshot('project', server); });
            expect(useProjectStore.getState().currentProject?.revision).toBe(server.revision);
            const confirmed = obj(useProjectStore.getState().currentProject!.characters[0]);
            expect(obj(confirmed[unit]).video_prompt).toBe(saved);
            expect(savedMotionDraft(channel)).toHaveValue(draft);
        }
        expect(journalBytes()).toEqual(bytes); expect(projectReads()).toHaveLength(0);
        expect(posts()).toHaveLength(0); expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });
