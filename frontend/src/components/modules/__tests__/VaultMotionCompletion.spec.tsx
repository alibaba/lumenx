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
function installUnprojected() {
    install('completed_unprojected', true, 'unprojected_enriched_project_projection');
    return actual('completed_unprojected', 'completed_unprojected_get').body;
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
afterEach(() => { clearRuntimeEnvironment(); });

it.each(['lifecycle_script_duration5', 'lifecycle_series', 'lifecycle_global'])(
    '%s confirms its exact projection before resolving the journal and keeps the selected video', async name => {
        install(name);
        vi.mocked(appAccessFetch).mockImplementation(async () => response(actual(name, 'completed_get').body));
        page();
        await waitFor(() => expect(projectReads().length).toBeGreaterThan(0));
        await waitFor(() => expect(journalBytes()).toHaveLength(0));
        expect(mainVideo()).toHaveAttribute('src', expect.stringContaining('video/history.mp4'));
        expect(useProjectStore.getState().currentProject?.characters[0].full_body?.selected_video_id).toBe('selected-video');
        expect(generate()).not.toBeDisabled(); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });

it('shows the actual selected old video instead of silently presenting the last appended candidate as current', () => {
    install('lifecycle_script_duration5', false); page();
    expect(mainVideo()).toHaveAttribute('src', expect.stringContaining('video/history.mp4'));
});
it('retains the legacy last-video preview when no selected-video field exists', () => {
    install('lifecycle_script_duration5', false);
    const character = obj((server.characters as unknown[])[0]);
    delete obj(character.full_body).selected_video_id;
    useProjectStore.getState().applyProjectReadSnapshot('project', server);
    page();
    expect(mainVideo()).toHaveAttribute('src', expect.stringContaining('video/provider-'));
});

it('does not refresh over author edits made while a reopened receipt is still waiting', async () => {
    install(); const held = deferred<Response>();
    vi.mocked(appAccessFetch).mockImplementation(() => held.promise);
    page(); await waitFor(() => expect(vi.mocked(appAccessFetch)).toHaveBeenCalledTimes(1));
    fireEvent.change(prompt(), { target: { value: 'Keep my new draft' } });
    await act(async () => { held.resolve(response(actual('lifecycle_script_duration5', 'completed_get').body)); });
    await screen.findByRole('button', { name: '保留为历史' });
    expect(prompt()).toHaveValue('Keep my new draft'); expect(projectReads()).toHaveLength(0);
    expect(journalBytes()).toHaveLength(1); expect(generate()).toBeDisabled();
});

it('can retain an already completed execution after project readback fails, but a new action still needs fresh project access', async () => {
    install();
    vi.mocked(appAccessFetch).mockImplementation(async () => response(actual('lifecycle_script_duration5', 'completed_get').body));
    vi.mocked(axios.get).mockImplementation(async url => {
        if (String(url).endsWith('/presets')) return { data: [] };
        throw new Error('Readback unavailable');
    });
    page(); const retain = await screen.findByRole('button', { name: '保留为历史' });
    const original = journalBytes(), text = (prompt() as HTMLTextAreaElement).value;
    fireEvent.click(retain); await waitFor(() => expect(generate()).not.toBeDisabled());
    expect(journalBytes()).toEqual(original); expect(prompt()).toHaveValue(text);
    const previousReads = projectReads().length;
    fireEvent.click(generate()); await waitFor(() => expect(projectReads().length).toBeGreaterThan(previousReads));
    await act(async () => {});
    expect(posts()).toHaveLength(0); expect(journalBytes()).toEqual(original);
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
});

it('retains the actual completed-unprojected result by original-key GET without saving or changing the selected video', async () => {
    const completed = installUnprojected();
    vi.mocked(appAccessFetch).mockImplementation(async () => response(completed));
    page(); const retain = await screen.findByRole('button', { name: '保留为历史' });
    fireEvent.change(prompt(), { target: { value: 'A later local motion choice' } });
    const original = journalBytes(), previousReads = vi.mocked(appAccessFetch).mock.calls.length;
    fireEvent.click(retain); await waitFor(() => expect(generate()).not.toBeDisabled());
    expect(vi.mocked(appAccessFetch).mock.calls.length).toBeGreaterThan(previousReads);
    expect(vi.mocked(appAccessFetch).mock.calls.every(([url, init]) => init?.method === 'GET'
        && String(url).endsWith('/motion_ref_requests/motion-request'))).toBe(true);
    expect(journalBytes()).toEqual(original); expect(prompt()).toHaveValue('A later local motion choice');
    expect(mainVideo()).toHaveAttribute('src', expect.stringContaining('video/history.mp4'));
    expect(projectReads()).toHaveLength(0); expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
});

it('does not release an unrelated unknown request when one completed history result is retained', async () => {
    const completed = installUnprojected(); seedJournal('completed_unprojected', 'uncertain-second');
    vi.mocked(appAccessFetch).mockImplementation(async url => String(url).endsWith('/uncertain-second')
        ? response({ detail: 'Request not found' }, 404) : response(completed));
    page(); fireEvent.click(await screen.findByRole('button', { name: '保留为历史' }));
    await screen.findByText('已保留为历史；当前编辑不变。');
    expect(generate()).toBeDisabled(); expect(journalBytes()).toHaveLength(2); expect(posts()).toHaveLength(0);
});

it('revokes a retained result when an explicit original-key read becomes unknown', async () => {
    const completed = installUnprojected(); let unavailable = false;
    vi.mocked(appAccessFetch).mockImplementation(async () => unavailable
        ? response({ detail: 'Unavailable' }, 503) : response(completed));
    page(); fireEvent.click(await screen.findByRole('button', { name: '保留为历史' }));
    await waitFor(() => expect(generate()).not.toBeDisabled()); const original = journalBytes();
    unavailable = true; fireEvent.click(screen.getByRole('button', { name: '查看原任务' }));
    await waitFor(() => expect(generate()).toBeDisabled());
    expect(screen.queryByText('已保留为历史；当前编辑不变。')).not.toBeInTheDocument();
    expect(journalBytes()).toEqual(original); expect(posts()).toHaveLength(0);
});

it('does not retain a different completion proof returned by the confirmation GET', async () => {
    const completed = installUnprojected(); let reads = 0;
    const changed = structuredClone(completed), record = obj(obj(changed.task).provider_execution);
    obj(record.artifact).sha256 = 'a'.repeat(64); obj(record.prepared_artifact).sha256 = 'a'.repeat(64);
    vi.mocked(appAccessFetch).mockImplementation(async () => response(++reads === 1 ? completed : changed));
    page(); fireEvent.click(await screen.findByRole('button', { name: '保留为历史' }));
    await waitFor(() => expect(reads).toBe(2)); await act(async () => {});
    expect(generate()).toBeDisabled(); expect(journalBytes()).toHaveLength(1);
    expect(screen.queryByText('已保留为历史；当前编辑不变。')).not.toBeInTheDocument();
});

it('does not resolve a journal when readProjectOnce returns a newer snapshot with a different pointer', async () => {
    install(); const old = structuredClone(server), held = deferred<{ data: Obj }>();
    vi.mocked(appAccessFetch).mockImplementation(async () => response(actual('lifecycle_script_duration5', 'completed_get').body));
    vi.mocked(axios.get).mockImplementation(async url => String(url).endsWith('/presets') ? { data: [] } : held.promise);
    page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
    const later = structuredClone(server), nextRevision = Number(later.revision) + 1;
    later.revision = nextRevision; obj(later.read_versions).project = nextRevision;
    obj((later.characters as unknown[])[0]).full_body_motion_request_id = 'newer-independent-task';
    act(() => { useProjectStore.getState().applyProjectReadSnapshot('project', later); });
    await act(async () => { held.resolve({ data: old }); });
    await screen.findByRole('button', { name: '保留为历史' });
    expect(journalBytes()).toHaveLength(1); expect(generate()).toBeDisabled();
    expect(useProjectStore.getState().currentProject?.characters[0].full_body_motion_request_id).toBe('newer-independent-task');
});

it.each(['raw-url', 'source-image', 'explicit-raw-sidecar'])(
    'does not confirm a completed projection with mismatching %s', async mismatch => {
        install(); const altered = structuredClone(server), character = obj((altered.characters as unknown[])[0]);
        const videos = obj(character.full_body).video_variants as Obj[], candidate = videos.at(-1)!;
        if (mismatch === 'raw-url') candidate.url = 'video/different-result.mp4';
        if (mismatch === 'source-image') candidate.source_image_id = 'different-image';
        if (mismatch === 'explicit-raw-sidecar') candidate.media_refs = { url: 'video/different-result.mp4' };
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : altered }));
        vi.mocked(appAccessFetch).mockImplementation(async () => response(actual('lifecycle_script_duration5', 'completed_get').body));
        page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
        await screen.findByRole('button', { name: '保留为历史' });
        expect(journalBytes()).toHaveLength(1); expect(generate()).toBeDisabled(); expect(posts()).toHaveLength(0);
    });


it.each(['terminal_failed', 'terminal_canceled'])(
    '%s releases only its definite terminal request without offering another recovery or starting generation', async name => {
        install(name, true, 'terminal_project');
        vi.mocked(appAccessFetch).mockImplementation(async () => response(actual(name, 'terminal_get').body));
        page();
        await screen.findByText('原任务已结束，未生成可用视频。');
        await waitFor(() => expect(generate()).not.toBeDisabled());
        expect(screen.queryByRole('button', { name: '继续原任务' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: '恢复原任务' })).not.toBeInTheDocument();
        expect(mainVideo()).toHaveAttribute('src', expect.stringContaining('video/history.mp4'));
        expect(posts()).toHaveLength(0); expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });
