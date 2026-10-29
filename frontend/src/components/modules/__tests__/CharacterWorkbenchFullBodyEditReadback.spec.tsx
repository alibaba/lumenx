// Actual full-body HTTP captures; real Vault/Workbench/hooks/readback/store.
// Only transport responses are supplied. Explicit corruptions below are negative
// HTTP cases, never new successful receipts. Captured r2 saves the author ratio.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred } from './frame-command-fixtures';
import capture from '@/__tests__/fixtures/studio-full-body-edit-wire.json';
import emptySelectionCapture from '@/__tests__/fixtures/studio-full-body-empty-selection-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Obj = Record<string, unknown>;
type Event = { label: string; path: string; body: Obj; request_body?: Obj; status_code: number };
type Capture = { cases: { case: string; events: Event[] }[] };
const SID = '11111111-1111-4111-8111-111111111111';
const warning = '结果已保存，但最新素材读取失败，请再次查看。';
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid captured object');
    return value as Obj;
}
function actual(name: string, label: string): Event {
    const cases = [...(capture as unknown as Capture).cases, ...(emptySelectionCapture as unknown as Capture).cases];
    const found = cases.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual full-body HTTP sample: ${name}/${label}`);
    return structuredClone(found);
}
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p' });
const panel = () => screen.getAllByRole('group', { name: '1. 主体资产（全身图）' }).at(-1)!;
const prompt = () => within(panel()).getByPlaceholderText('Enter prompt description...');
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/p'));
const journals = () => listStudioAssetImageRequests(owner());
const actor = (snapshot: Obj) => obj((snapshot.characters as unknown[]).find(value => obj(value).id === 'c'));
const currentActor = () => obj(useProjectStore.getState().currentProject?.characters.find(item => item.id === 'c'));
const savedChoice = () => obj(currentActor().full_body_asset).selected_id;
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let server: Obj;
let activeCase: string;
function seed(name = activeCase) {
    const { client_request_id, ...input } = actual(name, 'accepted_post').request_body!;
    return beginStudioAssetImageRequest({ ...owner(), input: input as StudioAssetImageRequestInput,
        idFactory: () => String(client_request_id), now: () => 7 }).journal;
}
function install(name = 'local_single', seeded = false) {
    activeCase = name; server = actual(name, 'before_project').body;
    // Actual API overrides are not substitutes for the saved-ratio Workbench path.
    expect(obj(server.model_settings).character_aspect_ratio)
        .toBe(actual(name, 'accepted_post').request_body!.aspect_ratio ?? '9:16');
    useProjectStore.setState({ currentProject: null, projects: [], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    expect(useProjectStore.getState().applyProjectReadSnapshot('p', structuredClone(server), true).status).toBe('applied');
    if (seeded) seed();
}
function page() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Alice' })); return view;
}
function noPaidSubmission() { expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); }
function expectVisibleBody(before: Obj) {
    for (const field of ['full_body_asset', 'full_body', 'full_body_image_url', 'full_body_request_id']) {
        expect(currentActor()[field]).toEqual(before[field]);
    }
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
    vi.mocked(axios.post).mockRejectedValue(new Error('Unexpected mutation during full-body receipt recovery'));
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        if (init?.method !== 'GET') throw new Error('Recovery must not create another request');
        const event = actual(activeCase, 'result_get');
        expect(String(url)).toBe(owner().environment.apiUrl + event.path);
        return response(event.body, event.status_code);
    });
    install();
});
afterEach(() => { clearRuntimeEnvironment(); vi.restoreAllMocks(); });

describe('full-body actual receipt and project recovery', () => {
    it.each(['series_empty_complete', 'series_empty_partial'])(
        '%s adopts the actual final selection without inventing a choice or resubmitting', async name => {
            install(name, true); const original = journals()[0];
            expect(savedChoice()).toBeNull();
            expect(obj(currentActor().full_body).selected_image_id).toBeNull();
            server = actual(name, 'after_project').body; const expected = actor(server);
            const request = obj(actual(name, 'result_get').body.request);
            const members = request.members as Obj[];
            page();
            await waitFor(() => expect(currentActor().full_body_request_id).toBe(request.id));
            expectVisibleBody(expected);
            expect(gets()).toHaveLength(1); expect(projectReads()).toHaveLength(1);
            if (name === 'series_empty_complete') {
                expect(members.map(member => member.status)).toEqual(['completed', 'completed']);
                expect(savedChoice()).toBe(members[0].variant_id);
                expect(obj(currentActor().full_body).selected_image_id).toBe(members[0].variant_id);
                expect(journals()).toHaveLength(0);
            } else {
                expect(members.map(member => member.status)).toEqual(['completed', 'needs_confirmation', 'cancelled']);
                expect(savedChoice()).toBeNull();
                expect(obj(currentActor().full_body).selected_image_id).toBeNull();
                expect(currentActor().full_body_image_url).toBeNull();
                const region = await within(panel()).findByRole('region', { name: '生成请求结果' });
                expect(within(region).getByText('已完成')).toBeInTheDocument();
                expect(within(region).getByText('待确认')).toBeInTheDocument();
                expect(within(region).getByAltText('结果 1')).toBeInTheDocument();
                expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
            }
            noPaidSubmission();
        });

    it.each(['local_single', 'series_batch'])(
        '%s resolves only after the actual owner pointer and both complete histories are read', async name => {
            install(name, true); const original = journals()[0], old = savedChoice();
            const before = structuredClone(currentActor());
            server = actual(name, 'after_project').body; const expected = actor(server);
            page(); await waitFor(() => expect(journals()).toHaveLength(0));
            expect(gets()).toHaveLength(1);
            expect(String(gets()[0][0])).toBe(owner().environment.apiUrl + actual(name, 'result_get').path);
            expect(projectReads()).toHaveLength(1); expect(currentActor().source).toBe(expected.source);
            for (const field of ['full_body_asset', 'full_body', 'full_body_request_id', 'full_body_image_url', 'full_body_prompt']) {
                expect(currentActor()[field]).toEqual(expected[field]);
            }
            for (const field of ['three_view_asset', 'three_views', 'headshot_asset', 'head_shot']) {
                expect(currentActor()[field]).toEqual(before[field]);
            }
            const completed = obj((obj(actual(name, 'result_get').body.request).members as unknown[])[0]);
            expect(savedChoice()).toBe(name === 'local_single' ? completed.variant_id : old);
            expect(obj(currentActor().full_body).video_variants).toEqual(obj(before.full_body).video_variants);
            expect(obj(currentActor().full_body).video_prompt).toBe(obj(before.full_body).video_prompt);
            expect(original.request).toEqual(actual(name, 'accepted_post').request_body); noPaidSubmission();
        });

    it('reopens the actual global unknown original key and explicit reads remain GET only', async () => {
        install('global_unknown', true); const original = journals()[0], old = savedChoice();
        const first = page();
        await within(panel()).findByText('待确认'); await waitFor(() => expect(gets()).toHaveLength(1));
        first.unmount(); server = actual('global_unknown', 'reopen_project').body;
        vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
            if (init?.method !== 'GET') throw new Error('Unknown recovery must not submit');
            const event = actual('global_unknown', 'reopen_get');
            expect(String(url)).toBe(owner().environment.apiUrl + event.path);
            return response(event.body, event.status_code);
        });
        page(); await waitFor(() => expect(gets()).toHaveLength(2));
        fireEvent.click(within(panel()).getByRole('button', { name: '查看生成结果' }));
        await waitFor(() => expect(gets()).toHaveLength(3));
        expect(gets().every(([url]) => String(url).endsWith('/' + original.request.client_request_id))).toBe(true);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expect(projectReads()).toHaveLength(0); expect(savedChoice()).toBe(old); noPaidSubmission();
    });

    it('shows actual partial output in both histories while retaining the old choice and original journal', async () => {
        install('local_partial', true); const original = journals()[0], old = savedChoice();
        server = actual('local_partial', 'after_project').body; const expected = actor(server);
        page();
        const region = await within(panel()).findByRole('region', { name: '生成请求结果' });
        await within(region).findByText('已完成'); expect(within(region).getByText('待确认')).toBeInTheDocument();
        expect(within(region).getByAltText('结果 1')).toBeInTheDocument();
        await waitFor(() => expect(currentActor().full_body_request_id).toBe(expected.full_body_request_id));
        expectVisibleBody(expected);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expect(savedChoice()).toBe(old); noPaidSubmission();
    });
});

describe('full-body readback rejects explicitly corrupted projections', () => {
    it.each(['pointer', 'missing legacy member', 'missing modern member', 'duplicate legacy member',
        'duplicate modern member', 'legacy raw URL', 'null modern raw sidecar', 'full-body alias'])(
        'retains the original journal and visible state for wrong %s', async change => {
            install('local_single', true); const original = journals()[0], before = structuredClone(currentActor());
            server = actual('local_single', 'after_project').body;
            const target = actor(server), legacy = obj(target.full_body_asset), modern = obj(target.full_body);
            const member = obj((obj(actual('local_single', 'result_get').body.request).members as unknown[])[0]);
            const old = (legacy.variants as Obj[]).find(value => value.id === member.variant_id)!;
            const current = (modern.image_variants as Obj[]).find(value => value.id === member.variant_id)!;
            if (change === 'pointer') target.full_body_request_id = '44444444-4444-4444-8444-444444444444';
            else if (change === 'missing legacy member') legacy.variants = (legacy.variants as Obj[]).filter(value => value.id !== member.variant_id);
            else if (change === 'missing modern member') modern.image_variants = (modern.image_variants as Obj[]).filter(value => value.id !== member.variant_id);
            else if (change === 'duplicate legacy member') (legacy.variants as Obj[]).push(structuredClone(old));
            else if (change === 'duplicate modern member') (modern.image_variants as Obj[]).push(structuredClone(current));
            else if (change === 'legacy raw URL') old.url = 'assets/studio/unrelated.png';
            else if (change === 'null modern raw sidecar') current.media_refs = { url: null };
            else target.full_body_image_url = 'uploads/legacy-a.png';
            page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
            await within(panel()).findByText(warning);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
            expectVisibleBody(before); noPaidSubmission();
        });

    it('does not read the project for a completed receipt corrupted to another actual owner', async () => {
        install('local_single', true); const original = journals()[0], before = structuredClone(currentActor());
        const corrupted = actual('local_single', 'result_get').body;
        Object.assign(obj(corrupted.request), { owner_kind: 'global', owner_id: 'library' });
        vi.mocked(appAccessFetch).mockImplementation(async () => response(corrupted));
        page(); await within(panel()).findByText(warning);
        expect(projectReads()).toHaveLength(0); expectVisibleBody(before);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request); noPaidSubmission();
    });

    it('retains an all-completed batch whose null selection disagrees with its nonempty alias', async () => {
        install('series_batch', true); const original = journals()[0], before = structuredClone(currentActor());
        server = actual('series_batch', 'after_project').body; const target = actor(server);
        obj(target.full_body_asset).selected_id = obj(target.full_body).selected_image_id = null;
        // Null itself is valid after history-only recovery; this response instead
        // contradicts its unchanged selected-image alias from the actual capture.
        expect(target.full_body_image_url).toBe('uploads/selected-c.png');
        page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
        await within(panel()).findByText(warning);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expectVisibleBody(before); noPaidSubmission();
    });

    it('does not treat missing selected raw URLs and alias as a matching batch selection', async () => {
        install('series_batch', true); const original = journals()[0], old = savedChoice(), before = structuredClone(currentActor());
        server = actual('series_batch', 'after_project').body; const target = actor(server);
        for (const values of [obj(target.full_body_asset).variants as Obj[], obj(target.full_body).image_variants as Obj[]]) {
            const selected = values.find(value => value.id === old)!; delete selected.url; delete selected.media_refs;
        }
        delete target.full_body_image_url; delete target.media_refs;
        page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
        await within(panel()).findByText(warning);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expectVisibleBody(before); noPaidSubmission();
    });
});

describe('full-body completion preserves current author and store state', () => {
    it('does not begin project readback after a new full-body draft while its original receipt GET is held', async () => {
        install('local_single', true); const original = journals()[0], before = structuredClone(currentActor());
        const held = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(held.promise);
        const view = page(), draft = 'Keep my new full-body author draft';
        try {
            await waitFor(() => expect(gets()).toHaveLength(1));
            fireEvent.change(prompt(), { target: { value: draft } });
            server = actual('local_single', 'after_project').body;
            await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
            expect(prompt()).toHaveValue(draft); expect(projectReads()).toHaveLength(0);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
            expectVisibleBody(before); noPaidSubmission();
        } finally {
            view.unmount(); clearRuntimeEnvironment();
            await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
        }
    });

    it('rechecks the author epoch after a full-body draft ABA while project GET is held', async () => {
        install('local_single', true); const original = journals()[0], before = structuredClone(currentActor());
        const held = deferred<{ data: Obj }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : held.promise);
        const view = page();
        try {
            await waitFor(() => expect(projectReads()).toHaveLength(1));
            const text = (prompt() as HTMLTextAreaElement).value;
            fireEvent.change(prompt(), { target: { value: 'Intervening full-body draft' } });
            fireEvent.change(prompt(), { target: { value: text } });
            await act(async () => { held.resolve({ data: actual('local_single', 'after_project').body }); });
            expect(prompt()).toHaveValue(text); expectVisibleBody(before);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request); noPaidSubmission();
        } finally {
            view.unmount(); clearRuntimeEnvironment();
            await act(async () => { held.resolve({ data: actual('local_single', 'after_project').body }); });
        }
    });

    it('does not resolve an older valid response after a newer stored full-body pointer wins', async () => {
        install('local_single', true); const original = journals()[0], held = deferred<{ data: Obj }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : held.promise);
        const view = page();
        try {
            await waitFor(() => expect(projectReads()).toHaveLength(1));
            const received = actual('local_single', 'after_project').body, newer = structuredClone(received);
            newer.revision = Number(newer.revision) + 1; obj(newer.read_versions).project = newer.revision;
            actor(newer).full_body_request_id = '44444444-4444-4444-8444-444444444444';
            act(() => { useProjectStore.getState().applyProjectReadSnapshot('p', newer); });
            await act(async () => { held.resolve({ data: received }); });
            expect(currentActor().full_body_request_id).toBe('44444444-4444-4444-8444-444444444444');
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request); noPaidSubmission();
        } finally {
            view.unmount(); clearRuntimeEnvironment();
            await act(async () => { held.resolve({ data: actual('local_single', 'after_project').body }); });
        }
    });
});
