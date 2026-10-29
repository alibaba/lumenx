// Same-storage reconnect; real Vault/Workbench/hooks/readback/store.
// The successful HTTP bodies are unchanged actual headshot captures.
// The only author change is a real textarea edit; both held GETs settle in finally.
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

describe('headshot author continuity across same-storage reconnect', () => {
    it.each([
        { name: 'keeps an edited portrait draft and original journal', dirty: true },
        { name: 'recovers the untouched portrait and resolves its original journal', dirty: false },
    ])('$name', async ({ dirty }) => {
        install('local_single', 'before_project', true);
        const original = journals()[0];
        const visibleBefore = structuredClone(currentActor());
        const originalPrompt = visibleBefore.headshot_prompt;
        const completedPrompt = actor(actual('local_single', 'after_project').body).headshot_prompt;
        if (typeof originalPrompt !== 'string' || typeof completedPrompt !== 'string') throw new Error('Missing captured portrait prompts');
        const beforeEnvironment = requireActiveRuntimeEnvironment();
        const oldGet = deferred<Response>(), currentGet = deferred<Response>();
        vi.mocked(appAccessFetch)
            .mockImplementationOnce(async (_url, init) => { expect(init?.method).toBe('GET'); return oldGet.promise; })
            .mockImplementationOnce(async (_url, init) => { expect(init?.method).toBe('GET'); return currentGet.promise; });
        let view: ReturnType<typeof page> | undefined;
        try {
            view = page();
            await waitFor(() => expect(gets()).toHaveLength(1));
            const editor = within(panel()).getByPlaceholderText('Enter prompt description...');
            const draft = 'Live author portrait draft after the original request.';
            if (dirty) fireEvent.change(editor, { target: { value: draft } });
            expect(editor).toHaveValue(dirty ? draft : originalPrompt);
            server = actual('local_single', 'after_project').body;
            await act(async () => {
                clearRuntimeEnvironment();
                activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
            });
            expect(requireActiveRuntimeEnvironment().key).toBe(beforeEnvironment.key);
            expect(requireActiveRuntimeEnvironment().generation).not.toBe(beforeEnvironment.generation);
            await waitFor(() => expect(gets()).toHaveLength(2));
            expect(within(panel()).getByPlaceholderText('Enter prompt description...')).toBe(editor);
            expect(editor).toHaveValue(dirty ? draft : originalPrompt);
            await act(async () => { currentGet.resolve(response(actual('local_single', 'result_get').body)); });
            await within(panel()).findByText('已完成');
            await waitFor(() => expect(within(panel()).getByRole('button', { name: '查看生成结果' })).toBeEnabled());
            expect(within(panel()).getByPlaceholderText('Enter prompt description...')).toBe(editor);
            if (dirty) {
                expect(editor).toHaveValue(draft);
                expect(projectReads()).toHaveLength(0);
                expect(journals()).toHaveLength(1);
                expect(journals()[0].request).toEqual(original.request);
                expect(currentActor()).toEqual(visibleBefore);
                expect(within(panel()).getByText('结果已保存，但最新素材读取失败，请再次查看。')).toBeVisible();
            } else {
                expect(editor).toHaveValue(completedPrompt);
                expect(projectReads()).toHaveLength(1);
                expect(journals()).toHaveLength(0);
                expect(currentActor().headshot_asset).toEqual(actor(server).headshot_asset);
                expect(currentActor().head_shot).toEqual(actor(server).head_shot);
                expect(currentActor().headshot_request_id).toBe(actor(server).headshot_request_id);
            }
            expect(gets()).toHaveLength(2);
            noPaidSubmission();
        } finally {
            view?.unmount();
            await act(async () => {
                oldGet.resolve(response(actual('local_single', 'result_get').body));
                currentGet.resolve(response(actual('local_single', 'result_get').body));
                await Promise.all([oldGet.promise, currentGet.promise]);
            });
        }
    });
});

describe('headshot stable author scope controls', () => {
    it('shows the new global owner saved prompt when a dirty local editor remains mounted', async () => {
        install('local_single', 'before_project', true);
        const original = journals()[0];
        const globalBefore = actual('global_single', 'before_project').body;
        const savedGlobalPrompt = actor(globalBefore).headshot_prompt;
        if (typeof savedGlobalPrompt !== 'string' || !savedGlobalPrompt) throw new Error('Missing captured global owner prompt');
        const oldReads = deferred<void>();
        vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => {
            expect(init?.method).toBe('GET');
            await oldReads.promise;
            return response(actual('local_single', 'result_get').body);
        });
        let view: ReturnType<typeof page> | undefined;
        try {
            view = page();
            await waitFor(() => expect(gets()).toHaveLength(1));
            const editor = within(panel()).getByPlaceholderText('Enter prompt description...');
            const localDraft = 'Unsaved local portrait draft must not become the global owner prompt.';
            fireEvent.change(editor, { target: { value: localDraft } });
            expect(editor).toHaveValue(localDraft);
            // Independent captures have project revisions 3 and 2. Reset only the
            // fixture cache via the existing install seam; keep both raw snapshots
            // unchanged. This is not a backend migration/revision-ordering claim.
            await act(async () => { install('global_single', 'before_project'); });
            expect(useProjectStore.getState().currentProject?.id).toBe('p');
            expect(currentActor().source).toBe('global');
            expect(currentActor().headshot_prompt).toBe(savedGlobalPrompt);
            expect(within(panel()).getByPlaceholderText('Enter prompt description...')).toBe(editor);
            expect(editor).toHaveValue(savedGlobalPrompt);
            expect(projectReads()).toHaveLength(0);
            expect(journals()).toHaveLength(1);
            expect(journals()[0].request).toEqual(original.request);
            noPaidSubmission();
        } finally {
            view?.unmount();
            await act(async () => { oldReads.resolve(); await oldReads.promise; });
        }
    });

    it('keeps the same-owner dirty draft across a separately flushed offline render', async () => {
        install('local_single', 'before_project', true);
        const original = journals()[0], visibleBefore = structuredClone(currentActor());
        const beforeEnvironment = requireActiveRuntimeEnvironment();
        const oldGet = deferred<Response>(), currentGet = deferred<Response>();
        vi.mocked(appAccessFetch)
            .mockImplementationOnce(async (_url, init) => { expect(init?.method).toBe('GET'); return oldGet.promise; })
            .mockImplementationOnce(async (_url, init) => { expect(init?.method).toBe('GET'); return currentGet.promise; });
        let view: ReturnType<typeof page> | undefined;
        try {
            view = page();
            await waitFor(() => expect(gets()).toHaveLength(1));
            const editor = within(panel()).getByPlaceholderText('Enter prompt description...');
            const draft = 'My current portrait draft survives a separately rendered offline state.';
            fireEvent.change(editor, { target: { value: draft } });
            expect(editor).toHaveValue(draft);
            await act(async () => { clearRuntimeEnvironment(); });
            expect(() => requireActiveRuntimeEnvironment()).toThrow('Runtime environment is not active');
            expect(within(panel()).getByPlaceholderText('Enter prompt description...')).toBe(editor);
            expect(editor).toHaveValue(draft);
            server = actual('local_single', 'after_project').body;
            await act(async () => { activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
            expect(requireActiveRuntimeEnvironment().key).toBe(beforeEnvironment.key);
            expect(requireActiveRuntimeEnvironment().generation).not.toBe(beforeEnvironment.generation);
            await waitFor(() => expect(gets()).toHaveLength(2));
            expect(within(panel()).getByPlaceholderText('Enter prompt description...')).toBe(editor);
            expect(editor).toHaveValue(draft);
            await act(async () => { currentGet.resolve(response(actual('local_single', 'result_get').body)); });
            await within(panel()).findByText('已完成');
            await waitFor(() => expect(within(panel()).getByRole('button', { name: '查看生成结果' })).toBeEnabled());
            expect(editor).toHaveValue(draft);
            expect(projectReads()).toHaveLength(0);
            expect(journals()).toHaveLength(1);
            expect(journals()[0].request).toEqual(original.request);
            expect(currentActor()).toEqual(visibleBefore);
            expect(gets()).toHaveLength(2);
            noPaidSubmission();
        } finally {
            view?.unmount();
            await act(async () => {
                oldGet.resolve(response(actual('local_single', 'result_get').body));
                currentGet.resolve(response(actual('local_single', 'result_get').body));
                await Promise.all([oldGet.promise, currentGet.promise]);
            });
        }
    });
});
