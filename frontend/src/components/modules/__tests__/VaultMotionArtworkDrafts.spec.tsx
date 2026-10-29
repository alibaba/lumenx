import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import type { StudioMotionRequestBody } from '@/lib/api';
import { beginStudioMotionRequest } from '@/lib/studioMotionRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, project } from './frame-command-fixtures';
import duration5 from '@/__tests__/fixtures/studio-motion-duration5-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Obj = Record<string, unknown>;
type Event = { label: string; body: Obj; request_body?: StudioMotionRequestBody };
function actual(label: string): Event {
    const found = (duration5 as unknown as { cases: Array<{ case: string; events: Event[] }> }).cases
        .find(item => item.case === 'lifecycle_script_duration5')?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual HTTP evidence: ${label}`);
    return structuredClone(found);
}
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected fixture object');
    return value as Obj;
}
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(done => { resolve = done; });
    return { promise, resolve };
}
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const journalBytes = () => Object.keys(localStorage).filter(key => key.startsWith('lumenx.studioMotionRequest.v1:'))
    .sort().map(key => ({ key, raw: localStorage.getItem(key) }));
const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/project'));
const groups = {
    full_body: '1. 主体资产（全身图）',
    three_view: '2. 三视图',
    headshot: '3. 头像（特写）',
} as const;
type Channel = keyof typeof groups;
const saved = (channel: Channel) => `Saved ${channel} artwork prompt`;
const draft = (channel: Channel) => `New unsaved ${channel} artwork prompt`;
const textarea = (channel: Channel) => within(screen.getAllByRole('group', { name: groups[channel] }).at(-1)!)
    .getByPlaceholderText('Enter prompt description...');
let server: Obj;
function install() {
    const source = actual('completed_enriched_project_projection').body;
    server = { ...ack([], 'project', source.revision as number), ...source,
        model_settings: { t2i_model: 'wan2.6-t2i', i2v_model: 'kling-v1' } };
    const character = obj((server.characters as unknown[])[0]);
    // Ordinary author-state fixtures only. The completion receipt, pointer,
    // selected source image, selected video and provider proof stay unchanged.
    for (const channel of Object.keys(groups) as Channel[]) character[`${channel}_prompt`] = saved(channel);
    useProjectStore.setState({ currentProject: project('project', 0), projects: [project('project', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('project', server);
    const { client_request_id, ...input } = actual('accepted_post').request_body!;
    return beginStudioMotionRequest({
        environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' }),
        projectId: 'project', assetId: 'hero', assetOwner: { kind: 'script', id: 'project' },
        input, selectedImageId: 'selected-image', idFactory: () => client_request_id, now: () => 7,
    }).journal;
}
function page() {
    renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Hero' }));
    // Static is the real default. Motion recovery still starts in the same
    // Workbench; do not disable its normal background read to hide the race.
}
function expectNoPaidAction() {
    expect(vi.mocked(appAccessFetch).mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true);
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}),
    } });
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : structuredClone(server) }));
    vi.mocked(axios.post).mockRejectedValue(new Error('No write belongs to a completion readback'));
});
afterEach(() => { clearRuntimeEnvironment(); vi.restoreAllMocks(); });

it.each(['full_body', 'three_view', 'headshot'] as const)(
    'preserves a new %s artwork draft authored while the original motion receipt is held', async channel => {
        const journal = install(), held = deferred<Response>();
        vi.mocked(appAccessFetch).mockImplementation(() => held.promise);
        page(); await waitFor(() => expect(vi.mocked(appAccessFetch)).toHaveBeenCalledTimes(1));
        expect(textarea(channel)).toHaveValue(saved(channel));
        fireEvent.change(textarea(channel), { target: { value: draft(channel) } });
        expect(textarea(channel)).toHaveValue(draft(channel));
        await act(async () => { held.resolve(response(actual('completed_get').body)); });
        expect(textarea(channel)).toHaveValue(draft(channel));
        expect(projectReads()).toHaveLength(0);
        const records = journalBytes(); expect(records).toHaveLength(1);
        // First validation may add immutable receiptIdentity, but cannot remove
        // or rebind this original author command merely because it completed.
        expect(JSON.parse(records[0].raw!)).toEqual({ ...journal, receiptIdentity: expect.any(String) });
        expectNoPaidAction();
    });

it('rechecks the artwork draft before applying a motion project GET that was already in flight', async () => {
    install(); const held = deferred<{ data: Obj }>();
    vi.mocked(appAccessFetch).mockImplementation(async () => response(actual('completed_get').body));
    vi.mocked(axios.get).mockImplementation(async url => String(url).endsWith('/presets') ? { data: [] } : held.promise);
    page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
    const original = journalBytes(); expect(original).toHaveLength(1);
    fireEvent.change(textarea('full_body'), { target: { value: draft('full_body') } });
    expect(textarea('full_body')).toHaveValue(draft('full_body'));
    await act(async () => { held.resolve({ data: structuredClone(server) }); });
    expect(textarea('full_body')).toHaveValue(draft('full_body'));
    expect(journalBytes()).toEqual(original); expect(projectReads()).toHaveLength(1);
    expect(useProjectStore.getState().currentProject?.characters[0].full_body?.selected_video_id).toBe('selected-video');
    expectNoPaidAction();
});
