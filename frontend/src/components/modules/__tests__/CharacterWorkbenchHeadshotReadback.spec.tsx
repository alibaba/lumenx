// Actual headshot HTTP captures; real Vault/Workbench/hooks/readback/store.
// Only transport responses are supplied. Explicit corruptions below are negative
// HTTP cases, never new successful receipts.
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
import capture from '@/__tests__/fixtures/studio-headshot-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Obj = Record<string, unknown>;
type Event = { label: string; body: Obj; request_body?: Obj; status_code: number };
type Capture = { cases: { case: string; events: Event[] }[] };
const SID = '11111111-1111-4111-8111-111111111111';
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid captured object');
    return value as Obj;
}
function actual(name: string, label: string): Event {
    const found = (capture as unknown as Capture).cases.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual headshot HTTP sample: ${name}/${label}`);
    return structuredClone(found);
}
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p' });
const panel = () => screen.getAllByRole('group', { name: '3. 头像（特写）' }).at(-1)!;
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/p'));
const journals = () => listStudioAssetImageRequests(owner());
const actor = (snapshot: Obj) => obj((snapshot.characters as unknown[]).find(value => obj(value).id === 'c'));
const currentActor = () => obj(useProjectStore.getState().currentProject?.characters.find(item => item.id === 'c'));
const savedChoice = () => obj(currentActor().headshot_asset).selected_id;
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
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
function noPaidSubmission() { expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); }
function artworkArea(channel: 'headshot' | 'three_view' | 'full_body') {
    const name = { headshot: '3. 头像（特写）', three_view: '2. 三视图', full_body: '1. 主体资产（全身图）' }[channel];
    return within(screen.getAllByRole('group', { name }).at(-1)!).getByPlaceholderText('Enter prompt description...');
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
    vi.mocked(axios.post).mockRejectedValue(new Error('Unexpected mutation during receipt recovery'));
    vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => {
        if (init?.method !== 'GET') throw new Error('Recovery must not create another request');
        return response(actual(activeCase, 'result_get').body);
    });
    install();
});
afterEach(() => { clearRuntimeEnvironment(); vi.restoreAllMocks(); });

describe('headshot selected raw URL evidence', () => {
    it('accepts the unchanged actual series batch with an existing valid selected portrait', async () => {
        install('series_batch', 'before_project', true); const old = savedChoice();
        server = actual('series_batch', 'after_project').body;
        const expected = actor(server);
        page(); await waitFor(() => expect(journals()).toHaveLength(0));
        expect(savedChoice()).toBe(old);
        expect(currentActor().headshot_asset).toEqual(expected.headshot_asset);
        expect(currentActor().head_shot).toEqual(expected.head_shot);
        expect(currentActor().headshot_image_url).toBe(expected.headshot_image_url);
        expect(currentActor().avatar_url).toBe(expected.avatar_url);
        expect(gets()).toHaveLength(1); noPaidSubmission();
    });

    it('retains the original journal when both selected old URLs and both portrait aliases are missing', async () => {
        install('series_batch', 'before_project', true); const original = journals()[0], old = savedChoice();
        const visibleBefore = structuredClone(currentActor());
        server = actual('series_batch', 'after_project').body;
        const target = actor(server), legacy = obj(target.headshot_asset), modern = obj(target.head_shot);
        for (const values of [legacy.variants as Obj[], modern.image_variants as Obj[]]) {
            const chosen = values.find(value => value.id === old)!;
            // Negative response corruption only. Generated members/receipt stay exact.
            delete chosen.url; delete chosen.media_refs;
        }
        delete target.headshot_image_url; delete target.avatar_url; delete target.media_refs;
        page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
        await within(panel()).findByText('结果已保存，但最新素材读取失败，请再次查看。');
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expect(savedChoice()).toBe(old);
        expect(currentActor().headshot_asset).toEqual(visibleBefore.headshot_asset);
        expect(currentActor().head_shot).toEqual(visibleBefore.head_shot);
        noPaidSubmission();
    });
});

describe('headshot actual receipt and project recovery', () => {
    // The unchanged series_batch positive control is above. These three rows add
    // the other two owners and the actual prepared-proof restart capture.
    it.each(['local_single', 'global_single', 'restart_read_only'])(
        '%s resolves only after the actual owner pointer and both image histories are read', async name => {
            install(name, 'before_project', true); const key = journals()[0].request.client_request_id;
            server = actual(name, 'after_project').body; const expected = actor(server);
            page(); await waitFor(() => expect(journals()).toHaveLength(0));
            expect(gets()).toHaveLength(1); expect(String(gets()[0][0])).toMatch(new RegExp('/' + key + '$'));
            expect(projectReads()).toHaveLength(1);
            expect(currentActor().source).toBe(expected.source);
            for (const field of ['headshot_asset', 'head_shot', 'headshot_request_id', 'headshot_image_url', 'avatar_url']) {
                expect(currentActor()[field]).toEqual(expected[field]);
            }
            noPaidSubmission();
        });

    it('reopens the actual unknown original key and explicit reads remain GET only', async () => {
        install('local_unknown', 'before_project', true);
        const original = journals()[0], key = original.request.client_request_id;
        const first = page();
        await within(panel()).findByText('待确认'); await waitFor(() => expect(gets()).toHaveLength(1));
        first.unmount(); server = actual('local_unknown', 'reopen_project').body;
        vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => {
            if (init?.method !== 'GET') throw new Error('Unknown recovery must not submit');
            return response(actual('local_unknown', 'reopen_get').body);
        });
        page(); await waitFor(() => expect(gets()).toHaveLength(2));
        fireEvent.click(within(panel()).getByRole('button', { name: '查看生成结果' }));
        await waitFor(() => expect(gets()).toHaveLength(3));
        expect(gets().every(([url]) => String(url).endsWith('/' + key))).toBe(true);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expect(projectReads()).toHaveLength(0); expect(savedChoice()).toBe('portrait-old'); noPaidSubmission();
    });

    it('shows the actual partial output in both histories while retaining the old selection and original journal', async () => {
        install('global_partial', 'before_project', true); const original = journals()[0], old = savedChoice();
        server = actual('global_partial', 'after_project').body; const expected = actor(server);
        page();
        const region = await within(panel()).findByRole('region', { name: '生成请求结果' });
        await within(region).findByText('已完成'); expect(within(region).getByText('待确认')).toBeInTheDocument();
        expect(within(region).getByAltText('结果 1')).toBeInTheDocument();
        await waitFor(() => expect(currentActor().headshot_request_id).toBe(expected.headshot_request_id));
        expect(currentActor().headshot_asset).toEqual(expected.headshot_asset);
        expect(currentActor().head_shot).toEqual(expected.head_shot);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expect(savedChoice()).toBe(old); noPaidSubmission();
    });

    it('recovers an actual completion through ordinary StrictMode reopen without treating setup as an edit', async () => {
        install('local_single', 'before_project', true); const key = journals()[0].request.client_request_id;
        const held = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(held.promise);
        const first = page(); let second: ReturnType<typeof renderWithIntl> | undefined;
        try {
            await waitFor(() => expect(gets()).toHaveLength(1)); first.unmount();
            server = actual('local_single', 'after_project').body;
            vi.mocked(appAccessFetch).mockImplementation(async () => response(actual('local_single', 'result_get').body));
            second = renderWithIntl(<StrictMode><LightboxProvider><ConsistencyVault /></LightboxProvider></StrictMode>); open();
            await waitFor(() => expect(journals()).toHaveLength(0));
            expect(gets().length).toBeGreaterThanOrEqual(3);
            expect(gets().every(([url]) => String(url).endsWith('/' + key))).toBe(true);
            expect(currentActor().headshot_asset).toEqual(actor(server).headshot_asset);
            await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
            expect(journals()).toHaveLength(0); noPaidSubmission();
        } finally {
            first.unmount(); second?.unmount(); clearRuntimeEnvironment();
            await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
        }
    });
});

describe('headshot readback rejects explicitly corrupted projections', () => {
    it.each(['pointer', 'missing legacy member', 'missing modern member', 'duplicate modern member',
        'legacy raw URL', 'modern raw URL', 'null raw sidecar', 'headshot alias', 'source'])(
        'retains the original journal and visible state for wrong %s', async change => {
            install('local_single', 'before_project', true); const original = journals()[0];
            const visibleBefore = structuredClone(currentActor()); server = actual('local_single', 'after_project').body;
            const target = actor(server), legacy = obj(target.headshot_asset), modern = obj(target.head_shot);
            const member = obj((obj(actual('local_single', 'result_get').body.request).members as unknown[])[0]);
            const old = (legacy.variants as Obj[]).find(value => value.id === member.variant_id)!;
            const current = (modern.image_variants as Obj[]).find(value => value.id === member.variant_id)!;
            if (change === 'pointer') target.headshot_request_id = '44444444-4444-4444-8444-444444444444';
            else if (change === 'missing legacy member') legacy.variants = (legacy.variants as Obj[]).filter(value => value.id !== member.variant_id);
            else if (change === 'missing modern member') modern.image_variants = (modern.image_variants as Obj[]).filter(value => value.id !== member.variant_id);
            else if (change === 'duplicate modern member') (modern.image_variants as Obj[]).push(structuredClone(current));
            else if (change === 'legacy raw URL') old.url = 'assets/studio/unrelated.png';
            else if (change === 'modern raw URL') current.url = 'assets/studio/unrelated.png';
            else if (change === 'null raw sidecar') current.media_refs = { url: null };
            else if (change === 'headshot alias') target.headshot_image_url = 'uploads/portrait-old.png';
            else target.source = 'global';
            page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
            await within(panel()).findByText('结果已保存，但最新素材读取失败，请再次查看。');
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
            expect(currentActor().headshot_asset).toEqual(visibleBefore.headshot_asset);
            expect(currentActor().head_shot).toEqual(visibleBefore.head_shot); noPaidSubmission();
        });

    it('does not read a project for a completed receipt corrupted to a different actual owner', async () => {
        install('local_single', 'before_project', true); const original = journals()[0], old = savedChoice();
        const corrupted = actual('local_single', 'result_get').body;
        Object.assign(obj(corrupted.request), { owner_kind: 'global', owner_id: 'library' });
        vi.mocked(appAccessFetch).mockImplementation(async () => response(corrupted));
        page(); await within(panel()).findByText('结果已保存，但最新素材读取失败，请再次查看。');
        expect(projectReads()).toHaveLength(0); expect(savedChoice()).toBe(old);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request); noPaidSubmission();
    });

    it('keeps the original journal when the read project moved to another series', async () => {
        install('local_single', 'before_project', true); const old = savedChoice();
        server = actual('local_single', 'after_project').body;
        server.series_id = 'other-series'; obj(server.read_versions).series = { id: 'other-series', revision: 0 };
        page(); await waitFor(() => expect(projectReads()).toHaveLength(1));
        await within(panel()).findByText('结果已保存，但最新素材读取失败，请再次查看。');
        expect(journals()).toHaveLength(1); expect(savedChoice()).toBe(old); noPaidSubmission();
    });

    it('uses original raw sidecars when an explicit display URL variation covers both containers and aliases', async () => {
        install('local_single', 'before_project', true); server = actual('local_single', 'after_project').body;
        const target = actor(server), member = obj((obj(actual('local_single', 'result_get').body.request).members as unknown[])[0]);
        const display = 'https://signed.example.invalid/headshot.png?expires=synthetic';
        for (const values of [obj(target.headshot_asset).variants as Obj[], obj(target.head_shot).image_variants as Obj[]]) {
            const value = values.find(item => item.id === member.variant_id)!;
            value.media_refs = { url: member.output_ref }; value.url = display;
        }
        target.media_refs = { headshot_image_url: member.output_ref, avatar_url: member.output_ref };
        target.headshot_image_url = target.avatar_url = display;
        // Only display/sidecar representation is varied. The captured receipt,
        // canonical reference, IDs and completed raw output remain unchanged.
        page(); await waitFor(() => expect(journals()).toHaveLength(0));
        expect(savedChoice()).toBe(member.variant_id); expect(currentActor().avatar_url).toBe(display); noPaidSubmission();
    });
});

describe('headshot completion preserves current author state', () => {
    it.each(['headshot', 'three_view', 'full_body'] as const)(
        'does not start project readback after a new %s draft while the original receipt GET is held', async channel => {
            install('local_single', 'before_project', true); const original = journals()[0], old = savedChoice();
            const held = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(held.promise);
            const view = page(), draft = `Keep my new ${channel} author draft`;
            try {
                await waitFor(() => expect(gets()).toHaveLength(1));
                fireEvent.change(artworkArea(channel), { target: { value: draft } });
                server = actual('local_single', 'after_project').body;
                await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
                expect(artworkArea(channel)).toHaveValue(draft); expect(projectReads()).toHaveLength(0);
                expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
                expect(savedChoice()).toBe(old); noPaidSubmission();
            } finally {
                view.unmount(); clearRuntimeEnvironment();
                await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
            }
        });

    it('keeps the existing head_shot Motion author text while a static completion is read', async () => {
        install('local_single', 'before_project', true); const held = deferred<Response>();
        vi.mocked(appAccessFetch).mockReturnValue(held.promise); const view = page();
        const draft = 'Keep the independently authored speaking motion';
        try {
            await waitFor(() => expect(gets()).toHaveLength(1));
            fireEvent.click(within(panel()).getByRole('button', { name: '动态' }));
            fireEvent.change(within(panel()).getByPlaceholderText('Describe the motion you want...'), { target: { value: draft } });
            server = actual('local_single', 'after_project').body;
            await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
            expect(within(panel()).getByPlaceholderText('Describe the motion you want...')).toHaveValue(draft);
            noPaidSubmission();
            expect(Object.keys(localStorage).some(key => key.startsWith('lumenx.studioMotionRequest.v1:'))).toBe(false);
        } finally {
            view.unmount(); clearRuntimeEnvironment();
            await act(async () => { held.resolve(response(actual('local_single', 'result_get').body)); });
        }
    });

    it('rechecks the author epoch after a headshot draft ABA while its project GET is held', async () => {
        install('local_single', 'before_project', true); const old = savedChoice(), held = deferred<{ data: Obj }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : held.promise);
        const view = page();
        try {
            await waitFor(() => expect(projectReads()).toHaveLength(1));
            const originalText = (artworkArea('headshot') as HTMLTextAreaElement).value;
            fireEvent.change(artworkArea('headshot'), { target: { value: 'Intervening portrait draft' } });
            fireEvent.change(artworkArea('headshot'), { target: { value: originalText } });
            await act(async () => { held.resolve({ data: actual('local_single', 'after_project').body }); });
            expect(artworkArea('headshot')).toHaveValue(originalText);
            expect(journals()).toHaveLength(1); expect(savedChoice()).toBe(old); noPaidSubmission();
        } finally {
            view.unmount(); clearRuntimeEnvironment();
            await act(async () => { held.resolve({ data: actual('local_single', 'after_project').body }); });
        }
    });

    it('does not resolve against an older valid response after a newer stored headshot pointer wins', async () => {
        install('local_single', 'before_project', true); const held = deferred<{ data: Obj }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : held.promise);
        const view = page();
        try {
            await waitFor(() => expect(projectReads()).toHaveLength(1));
            const received = actual('local_single', 'after_project').body, newer = structuredClone(received);
            newer.revision = Number(newer.revision) + 1; obj(newer.read_versions).project = newer.revision;
            actor(newer).headshot_request_id = '44444444-4444-4444-8444-444444444444';
            act(() => { useProjectStore.getState().applyProjectReadSnapshot('p', newer); });
            await act(async () => { held.resolve({ data: received }); });
            expect(currentActor().headshot_request_id).toBe('44444444-4444-4444-8444-444444444444');
            expect(journals()).toHaveLength(1); noPaidSubmission();
        } finally {
            view.unmount(); clearRuntimeEnvironment();
            await act(async () => { held.resolve({ data: actual('local_single', 'after_project').body }); });
        }
    });
});
