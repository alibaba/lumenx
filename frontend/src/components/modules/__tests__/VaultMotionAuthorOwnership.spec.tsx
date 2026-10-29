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
const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/project'));
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
function seedJournal(name: string, key?: string) {
    const { client_request_id, ...input } = actual(name, 'accepted_post').request_body!;
    const identity = obj(actual(name, 'accepted_post').body.request);
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

const strictPosts = () => posts().filter(([url]) => String(url).endsWith('/motion_ref_requests'));
function holdLock() {
    const acquired = deferred<void>();
    const requested = vi.fn(async (_name: string, _options: unknown, callback: (lock: object) => unknown) => {
        await acquired.promise; return callback({});
    });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: requested } });
    return { ...acquired, requested };
}
function readyEmptyOwner() {
    install('lifecycle_script_duration5', false);
    vi.mocked(appAccessFetch).mockImplementation(async url => new URL(String(url), 'http://localhost').pathname.endsWith('/motion_ref_requests')
        ? response(actual('lifecycle_script_duration5', 'eligibility').body) : response({ detail: 'No known request' }, 404));
}

it('rejects a queued action after a real prompt A-to-B-to-A edit before Web Lock acquisition', async () => {
    readyEmptyOwner(); const lock = holdLock(); page();
    const original = (prompt() as HTMLTextAreaElement).value;
    await waitFor(() => expect(generate()).not.toBeDisabled());
    fireEvent.click(generate()); await waitFor(() => expect(generate()).toBeDisabled());
    await waitFor(() => expect(lock.requested).toHaveBeenCalledTimes(1));
    fireEvent.change(prompt(), { target: { value: 'A different intervening intention' } });
    fireEvent.change(prompt(), { target: { value: original } });
    await act(async () => { lock.resolve(); });
    expect(prompt()).toHaveValue(original); expect(strictPosts()).toHaveLength(0);
    expect(journalBytes()).toHaveLength(0); expect(projectReads()).toHaveLength(0);
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
});

it('rejects a queued action after actual image selection changes away and back', async () => {
    readyEmptyOwner(); const character = obj((server.characters as unknown[])[0]);
    const first = { id: 'selected-image', url: 'assets/first.png', created_at: 1 };
    const second = { id: 'other-image', url: 'assets/second.png', created_at: 2 };
    character.full_body_asset = { selected_id: first.id, variants: [first, second] };
    obj(character.full_body).image_variants = [first, second];
    useProjectStore.getState().applyProjectReadSnapshot('project', server);
    vi.mocked(axios.post).mockImplementation(async (url, body) => {
        expect(String(url)).toMatch(/\/assets\/variant\/select$/);
        const payload = obj(body), selected = String(payload.variant_id);
        obj(character.full_body_asset).selected_id = selected; obj(character.full_body).selected_image_id = selected;
        server.revision = Number(server.revision) + 1; obj(server.read_versions).project = server.revision;
        return { data: structuredClone(server) };
    });
    const lock = holdLock(); page();
    await waitFor(() => expect(generate()).not.toBeDisabled());
    fireEvent.click(generate());
    await waitFor(() => expect(generate()).toBeDisabled());
    await waitFor(() => expect(lock.requested).toHaveBeenCalledTimes(1));
    fireEvent.click(within(panel()).getByRole('button', { name: 'Static' }));
    fireEvent.click(within(panel()).getAllByAltText('Variant')[1]);
    await waitFor(() => expect(useProjectStore.getState().currentProject?.characters[0].full_body?.selected_image_id).toBe('other-image'));
    fireEvent.click(within(panel()).getAllByAltText('Variant')[0]);
    await waitFor(() => expect(useProjectStore.getState().currentProject?.characters[0].full_body?.selected_image_id).toBe('selected-image'));
    await act(async () => { lock.resolve(); });
    expect(strictPosts()).toHaveLength(0); expect(journalBytes()).toHaveLength(0);
    expect(vi.mocked(axios.post)).toHaveBeenCalledTimes(2);
});

it('rejects a queued strict action after sequential A-to-B-to-A audio edits and preserves the final authored audio', async () => {
    readyEmptyOwner(); const lock = holdLock();
    const uploads = [deferred<Response>(), deferred<Response>(), deferred<Response>()]; let count = 0;
    vi.mocked(appAccessFetch).mockImplementation(async url => {
        if (String(url).endsWith('/upload')) return uploads[count++].promise;
        return response(actual('lifecycle_script_duration5', 'eligibility').body);
    });
    vi.mocked(axios.post).mockResolvedValue({ data: server });
    page(); await waitFor(() => expect(generate()).not.toBeDisabled());
    fireEvent.click(generate()); await waitFor(() => expect(generate()).toBeDisabled());
    await waitFor(() => expect(lock.requested).toHaveBeenCalledTimes(1));
    const upload = within(panel()).getByLabelText('Upload Audio File');
    const names = ['a.wav', 'b.wav', 'a.wav'];
    for (const [index, name] of Array.from(names.entries())) {
        expect(upload).not.toBeDisabled();
        fireEvent.change(upload, { target: { files: [new File(['audio'], name, { type: 'audio/wav' })] } });
        await waitFor(() => expect(count).toBe(index + 1));
        expect(upload).toBeDisabled();
        await act(async () => { uploads[index].resolve(response({ url: `/output/uploads/${name}` })); });
        await waitFor(() => expect(upload).not.toBeDisabled());
    }
    await within(panel()).findByText('Audio Uploaded');
    await act(async () => { lock.resolve(); });
    expect(strictPosts()).toHaveLength(0); expect(journalBytes()).toHaveLength(0);
    await waitFor(() => expect(generate()).not.toBeDisabled());
    fireEvent.click(generate()); await waitFor(() => expect(vi.mocked(axios.post)).toHaveBeenCalledTimes(1));
    expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ audio_url: '/output/uploads/a.wav', duration: 5, batch_size: 1 });
});

it('rechecks all keys after async preparation instead of bypassing an arriving unknown journal', async () => {
    readyEmptyOwner(); const held = deferred<{ data: Obj }>();
    vi.mocked(axios.get).mockImplementation(async url => String(url).endsWith('/presets') ? { data: [] } : held.promise);
    page(); await waitFor(() => expect(generate()).not.toBeDisabled());
    fireEvent.click(generate()); await waitFor(() => expect(projectReads()).toHaveLength(1));
    seedJournal('lifecycle_script_duration5', 'arrived-during-prepare');
    const original = journalBytes();
    await act(async () => { held.resolve({ data: server }); });
    await waitFor(() => expect(generate()).toBeDisabled()); await act(async () => {});
    expect(strictPosts()).toHaveLength(0); expect(journalBytes()).toEqual(original);
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
});


function newerProject() {
    const fresh = structuredClone(server);
    fresh.revision = Number(fresh.revision) + 1;
    obj(fresh.read_versions).project = fresh.revision;
    return fresh;
}

it('does not refresh or admit when the explicit preflight project GET returns 503', async () => {
    readyEmptyOwner(); const baseline = useProjectStore.getState().currentProject;
    vi.mocked(axios.get).mockImplementation(async url => {
        if (String(url).endsWith('/presets')) return { data: [] };
        throw Object.assign(new Error('Project unavailable'), { response: { status: 503 } });
    });
    page(); await waitFor(() => expect(generate()).not.toBeDisabled());
    fireEvent.change(prompt(), { target: { value: 'Keep this authored draft after the failed read' } });
    fireEvent.click(generate()); await waitFor(() => expect(projectReads()).toHaveLength(1));
    await waitFor(() => expect(generate()).not.toBeDisabled());
    expect(useProjectStore.getState().currentProject).toBe(baseline);
    expect(prompt()).toHaveValue('Keep this authored draft after the failed read');
    expect(vi.mocked(appAccessFetch)).not.toHaveBeenCalled();
    expect(strictPosts()).toHaveLength(0); expect(journalBytes()).toHaveLength(0);
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
});

it.each(['asset owner', 'project series'] as const)(
    'does not adopt a newer project response with a changed %s', async changed => {
        readyEmptyOwner(); const baseline = useProjectStore.getState().currentProject, fresh = newerProject();
        if (changed === 'asset owner') obj((fresh.characters as unknown[])[0]).source = 'global';
        else fresh.series_id = 'different-series';
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : fresh }));
        page(); await waitFor(() => expect(generate()).not.toBeDisabled());
        fireEvent.click(generate()); await waitFor(() => expect(projectReads()).toHaveLength(1));
        await waitFor(() => expect(generate()).not.toBeDisabled());
        expect(useProjectStore.getState().currentProject).toBe(baseline);
        expect(vi.mocked(appAccessFetch)).not.toHaveBeenCalled();
        expect(strictPosts()).toHaveLength(0); expect(journalBytes()).toHaveLength(0);
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });

it.each(['prompt edit', 'prompt A-to-B-to-A', 'environment A-to-B-to-A', 'owner switch', 'newer store read'] as const)(
    'does not adopt the held preflight after an intervening %s', async changed => {
        readyEmptyOwner(); const held = deferred<{ data: Obj }>(), fresh = newerProject();
        vi.mocked(axios.get).mockImplementation(async url => String(url).endsWith('/presets') ? { data: [] } : held.promise);
        page(); await waitFor(() => expect(generate()).not.toBeDisabled());
        const original = (prompt() as HTMLTextAreaElement).value;
        fireEvent.click(generate()); await waitFor(() => expect(projectReads()).toHaveLength(1));
        if (changed === 'prompt edit' || changed === 'prompt A-to-B-to-A') {
            fireEvent.change(prompt(), { target: { value: 'An edit made while the project read was held' } });
            if (changed === 'prompt A-to-B-to-A') fireEvent.change(prompt(), { target: { value: original } });
        } else if (changed === 'environment A-to-B-to-A') {
            act(() => {
                activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' });
                activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
            });
        } else {
            const replacement = newerProject();
            replacement.revision = Number(replacement.revision) + 1;
            obj(replacement.read_versions).project = replacement.revision;
            if (changed === 'owner switch') obj((replacement.characters as unknown[])[0]).source = 'global';
            act(() => { useProjectStore.getState().applyProjectReadSnapshot('project', replacement); });
        }
        const currentAfterChange = useProjectStore.getState().currentProject;
        await act(async () => { held.resolve({ data: fresh }); });
        await waitFor(() => expect(generate()).not.toBeDisabled());
        expect(useProjectStore.getState().currentProject).toBe(currentAfterChange);
        expect(prompt()).toHaveValue(changed === 'prompt edit' ? 'An edit made while the project read was held' : original);
        expect(vi.mocked(appAccessFetch)).not.toHaveBeenCalled();
        expect(strictPosts()).toHaveLength(0); expect(journalBytes()).toHaveLength(0);
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });
