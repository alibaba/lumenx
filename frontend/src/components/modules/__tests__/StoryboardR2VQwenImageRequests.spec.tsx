import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import wire from '@/__tests__/fixtures/storyboard-qwen-image-wire.json';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests } from '@/lib/studioAssetImageRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, frame } from './frame-command-fixtures';
import StoryboardR2V from '../StoryboardR2V';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
// Only unrelated modal/queue surfaces are replaced. The card, editor, generation button,
// locale, environment, store, request journal and recovery view are the real production path.
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/TaskQueuePanel', () => ({ default: () => null }));
const SID = '11111111-1111-4111-8111-111111111111';
const models = ['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'];
let server: ReturnType<typeof snapshot>;
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'a' });
function snapshot(model = models[0], revision = 3) {
    return enriched({ ...ack([{ ...frame('f1'), workbench_tab_mode: 't2i_i2v' }], 'a', revision),
        model_settings: { i2i_model: model, t2i_model: 'wan2.7-image', storyboard_aspect_ratio: '16:9' } }, 8);
}
function receipt(key: string, status = 'pending', reason: string | null = null) {
    const base = status === 'completed' ? wire.readback : wire.admission;
    return { ...base, reason, request: { ...base.request, client_request_id: key, request_root_id: 'a', owner_id: 'a', asset_id: 'f1',
        members: base.request.members.map(member => ({ ...member, status })) } };
}
const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const reads = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const generate = () => screen.getByRole('button', { name: '由提示词生成' });
const read = () => screen.findByRole('button', { name: '查看生成结果' });
const prompt = () => screen.getByPlaceholderText('描述此镜头... 使用 @ 引用资产');
function page() { return renderWithIntl(<LightboxProvider><StoryboardR2V /></LightboxProvider>); }
function selectCurrent() { useProjectStore.getState().applyProjectReadSnapshot('a', server, true); }
function completed() {
    server = { ...server, revision: 4, read_versions: { ...server.read_versions, project: 4 }, frames: [{ ...server.frames[0],
        image_request_id: wire.readback.request.id,
        rendered_image_asset: { selected_id: wire.readback.request.members[0].variant_id, variants: [{ id: wire.readback.request.members[0].variant_id, url: wire.readback.request.members[0].output_ref }] },
        image_url: wire.readback.request.members[0].output_ref, rendered_image_url: wire.readback.request.members[0].output_ref }] };
}
function holdLock() {
    const release = deferred<void>(); let entered = false;
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, cb: (lock: object) => unknown) => { entered = true; await release.promise; return cb({}); } } });
    return { release, entered: () => entered };
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    localStorage.setItem('storyboard-r2v-expanded-a', '["f1"]');
    useProjectStore.setState({ currentProject: null, projects: [], currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
    server = snapshot(); selectCurrent();
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, cb: (lock: object) => unknown) => cb({}) } });
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/projects/a') ? server : [] }));
    vi.mocked(axios.post).mockImplementation(async () => ({ data: server }));
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => reply(receipt(init?.method === 'POST' ? JSON.parse(String(init.body)).client_request_id : String(url).split('/').at(-1)!)));
});
afterEach(() => { vi.useRealTimers(); });

describe('real R2V single-frame Qwen image request route', () => {
    it.each(models)('uses actual i2i_model %s, one image and author revision', async model => {
        server = snapshot(model); selectCurrent(); page();
        await waitFor(() => expect(generate()).not.toBeDisabled()); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ asset_type: 'storyboard_frame', generation_type: 'all', asset_id: 'f1', prompt: 'prompt f1', composition_data: {}, expected_revision: 3, batch_size: 1 });
        expect(Object.keys(JSON.parse(String(posts()[0][1]?.body))).sort()).toEqual(['asset_id', 'asset_type', 'batch_size', 'client_request_id', 'composition_data', 'expected_revision', 'generation_type', 'prompt'].sort());
        expect(axios.post).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(generate()).toBeDisabled();
    });
    it('submits one deduplicated local implicit reference and retains the original journal key', async () => {
        server.frames[0].character_ids = ['c']; server.frames[0].scene_id = 's';
        Object.assign(server, {
            characters: [{ id: 'c', name: 'Actor', source: 'episode', full_body_image_url: 'https://remote.invalid/lower-priority.png',
                three_view_asset: { selected_id: 'selected-c', variants: [{ id: 'selected-c',
                    url: 'https://signed.invalid/character.png', media_refs: { url: 'uploads/shared-reference.png' } }] } }],
            scenes: [{ id: 's', name: 'Scene', source: 'episode', image_asset: { selected_id: 'selected-s', variants: [{
                id: 'selected-s', url: 'https://signed.invalid/scene.png', media_refs: { url: 'uploads/shared-reference.png' } }] } }],
        });
        selectCurrent(); page(); await waitFor(() => expect(generate()).not.toBeDisabled()); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        const sent = JSON.parse(String(posts()[0][1]?.body));
        expect(sent).toMatchObject({ asset_type: 'storyboard_frame', generation_type: 'all', asset_id: 'f1',
            prompt: 'prompt f1', composition_data: {}, expected_revision: 3, batch_size: 1 });
        const journals = listStudioAssetImageRequests(owner());
        expect(journals).toHaveLength(1); expect(journals[0].request).toEqual(sent);
        expect(sent.client_request_id).toBeTruthy(); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['other model', 't2i only', 'remote reference source'])('keeps %s on its existing legacy route', async kind => {
        server = snapshot(kind === 'remote reference source' ? models[0] : 'wan2.7-image');
        if (kind === 't2i only') server.model_settings.t2i_model = models[0];
        if (kind === 'remote reference source') {
            server.frames[0].character_ids = ['c'];
            Object.assign(server, { characters: [{ id: 'c', name: 'Actor', source: 'episode', full_body_image_url: 'https://remote.invalid/reference.png' }] });
        }
        selectCurrent(); page(); fireEvent.click(generate());
        await waitFor(() => expect(axios.post).toHaveBeenCalled());
        expect(String(vi.mocked(axios.post).mock.calls[0][0])).toContain('/storyboard/render');
        expect(posts()).toHaveLength(0); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
    });
    it('keeps admission pending until GET and matching committed frame projection', async () => {
        page(); await waitFor(() => expect(generate()).not.toBeDisabled()); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(useProjectStore.getState().currentProject?.frames[0].image_url).toBe('');
        completed(); vi.mocked(appAccessFetch).mockImplementation(async url => reply(receipt(String(url).split('/').at(-1)!, 'completed')));
        fireEvent.click(await read());
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        expect(useProjectStore.getState().currentProject?.frames[0].image_url).toBe(wire.readback.request.members[0].output_ref);
        expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['lost ACK', '503'])('retains %s for original GET only across reopen', async failure => {
        vi.mocked(appAccessFetch).mockImplementationOnce(async () => { if (failure === 'lost ACK') throw Error('ACK lost'); return reply({}, 503); });
        const view = page(); await waitFor(() => expect(generate()).not.toBeDisabled()); fireEvent.click(generate());
        await waitFor(() => expect(posts()).toHaveLength(1));
        await waitFor(() => expect(generate()).toBeDisabled());
        const original = listStudioAssetImageRequests(owner())[0]; expect(original).toBeDefined();
        view.unmount(); page();
        await waitFor(() => expect(reads().length).toBeGreaterThan(0));
        expect(reads().every(([url]) => String(url).endsWith(original.request.client_request_id))).toBe(true);
        expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
    it('does not upgrade a dirty draft revision to a fresh GET revision', async () => {
        page(); fireEvent.change(prompt(), { target: { value: 'author draft' } });
        server = snapshot(models[0], 4);
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ prompt: 'author draft', expected_revision: 3 });
    });
    it.each(['edit', 'edit ABA'])('cancels %s during Web Lock waiting without a journal or paid POST', async kind => {
        const lock = holdLock(); page(); await waitFor(() => expect(generate()).not.toBeDisabled()); fireEvent.click(generate());
        await waitFor(() => expect(lock.entered()).toBe(true));
        fireEvent.change(prompt(), { target: { value: 'new author draft' } });
        if (kind === 'edit ABA') fireEvent.change(prompt(), { target: { value: 'prompt f1' } });
        await act(async () => lock.release.resolve());
        expect(posts()).toHaveLength(0); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
    });
    it('does not project a late completed image over a newer unsaved author draft', async () => {
        page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        fireEvent.change(prompt(), { target: { value: 'new local story' } });
        completed(); vi.mocked(appAccessFetch).mockImplementation(async url => reply(receipt(String(url).split('/').at(-1)!, 'completed')));
        fireEvent.click(await read());
        await waitFor(() => expect(reads()).toHaveLength(1));
        await act(async () => {});
        expect(useProjectStore.getState().currentProject?.frames[0].image_url).toBe('');
        expect(prompt()).toHaveValue('new local story');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
    it.each(['model', 'reference', 'environment ABA', 'project ABA'])('cancels %s while waiting for the browser lock', async kind => {
        const lock = holdLock(); page(); fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
        if (kind === 'environment ABA') act(() => {
            clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
        });
        else if (kind === 'project ABA') {
            act(() => useProjectStore.getState().applyProjectReadSnapshot('b', enriched(ack([{ ...frame('f1'), workbench_tab_mode: 't2i_i2v' }], 'b', 3), 8), true));
            act(() => selectCurrent());
        } else {
            server = snapshot(kind === 'model' ? 'wan2.7-image' : models[0], 4);
            if (kind === 'reference') { server.frames[0].scene_id = 's'; Object.assign(server, { scenes: [{ id: 's', name: 'Scene', image_url: 'missing.png' }] }); }
            act(() => selectCurrent());
        }
        await act(async () => lock.release.resolve());
        expect(posts()).toHaveLength(0); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
    });
    it.each(['read failure', 'wrong pointer', 'changed input'])('keeps %s as a read-only unresolved request', async failure => {
        page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); completed();
        if (failure === 'wrong pointer') Object.assign(server.frames[0], { image_request_id: '22222222-2222-4222-8222-222222222222' });
        if (failure === 'read failure') vi.mocked(axios.get).mockRejectedValueOnce(Error('GET failed'));
        vi.mocked(appAccessFetch).mockImplementation(async url => reply(receipt(String(url).split('/').at(-1)!, 'completed', failure === 'changed input' ? 'studio_image_request_inputs_changed' : null)));
        fireEvent.click(await read()); await waitFor(() => expect(reads()).toHaveLength(1)); await act(async () => {});
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); expect(generate()).toBeDisabled();
        expect(useProjectStore.getState().currentProject?.frames[0].image_url).toBe('');
        expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
    it('keeps structural commands blocked while a Core image remains pending', async () => {
        page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        await read(); await act(async () => {});
        fireEvent.click(screen.getAllByRole('button', { name: '添加镜头' }).at(-1)!);
        expect(axios.post).not.toHaveBeenCalled();
        expect(await screen.findByRole('alert')).toHaveTextContent('生成仍在进行');
    });
    it('accepts only the author queue own ACK as an advance while eligibility GET is pending', async () => {
        const fresh = deferred<{ data: typeof server }>();
        vi.mocked(axios.get).mockImplementationOnce(() => fresh.promise);
        vi.mocked(axios.post).mockImplementation(async (url, body) => {
            expect(String(url)).toContain('/frames/update');
            expect(body).toMatchObject({ expected_revision: 3, visual_description: 'saved author prompt' });
            server = snapshot(models[0], 4); Object.assign(server.frames[0], { visual_description: 'saved author prompt' });
            return { data: server };
        });
        page(); fireEvent.change(prompt(), { target: { value: 'saved author prompt' } }); fireEvent.click(generate());
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 850)); });
        expect(axios.post).toHaveBeenCalledTimes(1); expect(posts()).toHaveLength(0);
        await act(async () => fresh.resolve({ data: server }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ expected_revision: 4, prompt: 'saved author prompt' });
    });
    it.each(['selected character', 'selected scene', 'unselected character'])('matches the existing raw reference priority for %s', async kind => {
        const unit = { selected_id: kind === 'unselected character' ? 'missing-selection' : 'selected', variants: [{ id: 'selected', url: 'missing-reference.png' }] };
        if (kind === 'selected scene') {
            server.frames[0].scene_id = 'scene'; Object.assign(server, { scenes: [{ id: 'scene', name: 'Scene', source: 'series', image_asset: unit }] });
        } else {
            server.frames[0].character_ids = ['c']; Object.assign(server, { characters: [{ id: 'c', name: 'Actor', source: 'global', three_view_asset: unit }] });
        }
        selectCurrent(); page(); fireEvent.click(generate());
        if (kind === 'unselected character') { await waitFor(() => expect(posts()).toHaveLength(1)); expect(axios.post).not.toHaveBeenCalled(); }
        else { await waitFor(() => expect(axios.post).toHaveBeenCalled()); expect(posts()).toHaveLength(0); }
    });
    it('reopens an existing request after changing model and completes by the original GET only', async () => {
        const view = page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); await read();
        const original = listStudioAssetImageRequests(owner())[0]; view.unmount();
        completed(); server.model_settings.i2i_model = 'wan2.7-image';
        vi.mocked(appAccessFetch).mockImplementation(async url => reply(receipt(String(url).split('/').at(-1)!, 'completed')));
        act(() => selectCurrent()); page();
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(0));
        expect(reads()).toHaveLength(1); expect(String(reads()[0][0])).toContain(original.request.client_request_id);
        expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
    it('does not resubmit when a receipt GET fails and an explicit read retries', async () => {
        page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1)); await read();
        const original = listStudioAssetImageRequests(owner())[0];
        vi.mocked(appAccessFetch).mockResolvedValueOnce(reply({}, 503));
        fireEvent.click(await read()); await waitFor(() => expect(reads()).toHaveLength(1));
        await waitFor(() => expect(generate()).toBeDisabled());
        fireEvent.click(await read()); await waitFor(() => expect(reads()).toHaveLength(2));
        expect(reads().every(([url]) => String(url).endsWith(original.request.client_request_id))).toBe(true);
        expect(posts()).toHaveLength(1); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
    it('ignores an old pending response after leaving and returning to the same project', async () => {
        const late = deferred<Response>(); vi.mocked(appAccessFetch).mockImplementationOnce(() => late.promise);
        page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        const original = listStudioAssetImageRequests(owner())[0];
        act(() => useProjectStore.getState().applyProjectReadSnapshot('b', enriched(ack([], 'b', 3), 8), true));
        act(() => selectCurrent());
        vi.mocked(appAccessFetch).mockImplementation(async url => reply(receipt(String(url).split('/').at(-1)!, 'needs_confirmation')));
        await waitFor(() => expect(reads()).toHaveLength(1));
        await act(async () => late.resolve(reply(receipt(original.request.client_request_id, 'completed'))));
        expect(posts()).toHaveLength(1); expect(vi.mocked(axios.get).mock.calls).toHaveLength(1);
        expect(useProjectStore.getState().currentProject?.frames[0].image_url).toBe('');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
});

describe('independent reopened receipt projection ownership', () => {
    it('retains workbench candidate selection made while the reopened receipt GET is pending', async () => {
        const journal = beginStudioAssetImageRequest({ ...owner(), idFactory: () => 'reopened-frame', input: { asset_type: 'storyboard_frame', generation_type: 'all', asset_id: 'f1', expected_revision: 3, prompt: 'prompt f1', composition_data: {}, batch_size: 1 } }).journal;
        completed();
        Object.assign(server.frames[0], { t2i_image_urls: ['uploads/first.png', 'uploads/second.png'], t2i_selected_index: 0 });
        selectCurrent();
        const pendingReceipt = deferred<Response>();
        vi.mocked(appAccessFetch).mockImplementationOnce(() => pendingReceipt.promise);
        page();
        await waitFor(() => expect(reads()).toHaveLength(1));
        fireEvent.click(screen.getByRole('button', { name: 'T2I candidate 2' }));
        expect(screen.getByRole('button', { name: 'T2I candidate 2 (active)' })).toHaveAttribute('aria-pressed', 'true');
        await act(async () => pendingReceipt.resolve(reply(receipt(journal.request.client_request_id, 'completed'))));
        expect(axios.patch).not.toHaveBeenCalled();
        expect(posts()).toHaveLength(0);
        expect(screen.getByRole('button', { name: 'T2I candidate 2 (active)' })).toHaveAttribute('aria-pressed', 'true');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
    it('does not resolve an old receipt using a superseding project whose current pointer no longer matches', async () => {
        const journal = beginStudioAssetImageRequest({ ...owner(), idFactory: () => 'reopened-frame', input: { asset_type: 'storyboard_frame', generation_type: 'all', asset_id: 'f1', expected_revision: 3, prompt: 'prompt f1', composition_data: {}, batch_size: 1 } }).journal;
        completed(); selectCurrent();
        const oldCompletedSnapshot = structuredClone(server);
        const pendingProject = deferred<{ data: typeof server }>();
        vi.mocked(axios.get).mockImplementationOnce(() => pendingProject.promise);
        vi.mocked(appAccessFetch).mockResolvedValue(reply(receipt(journal.request.client_request_id, 'completed')));
        page();
        await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
        server = { ...server, revision: 5, read_versions: { ...server.read_versions, project: 5 }, frames: [{ ...server.frames[0], image_request_id: '22222222-2222-4222-8222-222222222222', image_url: 'uploads/newer.png', rendered_image_url: 'uploads/newer.png', rendered_image_asset: { selected_id: 'manual', variants: [{ id: 'manual', url: 'uploads/newer.png' }] } }] };
        act(() => selectCurrent());
        await act(async () => pendingProject.resolve({ data: oldCompletedSnapshot }));
        expect(useProjectStore.getState().currentProject?.revision).toBe(5);
        expect(useProjectStore.getState().currentProject?.frames[0].image_request_id).toBe('22222222-2222-4222-8222-222222222222');
        expect(posts()).toHaveLength(0);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
});

it('cancels shared reference ABA with unchanged project revision while the browser lock is pending', async () => {
    server.frames[0].character_ids = ['c'];
    Object.assign(server, { characters: [{ id: 'c', name: 'Actor', source: 'global', image_url: '' }] });
    selectCurrent();
    const lock = holdLock(); page();
    await waitFor(() => expect(generate()).not.toBeDisabled());
    fireEvent.click(generate()); await waitFor(() => expect(lock.entered()).toBe(true));
    server = { ...server, read_versions: { ...server.read_versions, library: 9 } };
    Object.assign(server, { characters: [{ id: 'c', name: 'Actor', source: 'global', image_url: 'uploads/new-reference.png' }] });
    act(() => selectCurrent());
    server = { ...server, read_versions: { ...server.read_versions, library: 10 } };
    Object.assign(server, { characters: [{ id: 'c', name: 'Actor', source: 'global', image_url: '' }] });
    act(() => selectCurrent());
    expect(useProjectStore.getState().currentProject?.revision).toBe(3);
    expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(10);
    await act(async () => lock.release.resolve());
    expect(posts()).toHaveLength(0);
    expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
});


describe('explicit retention of known storyboard results', () => {
    const generate = () => screen.getByRole('button', { name: '再抽一张' });
    const keep = () => screen.getByRole('button', { name: '保留当前编辑，继续分镜' });
    async function blocked(reason: string | null = null, candidate = false) {
        const journal = beginStudioAssetImageRequest({ ...owner(), idFactory: () => 'retained-frame', input: { asset_type: 'storyboard_frame', generation_type: 'all', asset_id: 'f1', expected_revision: 3, prompt: 'prompt f1', composition_data: {}, batch_size: 1 } }).journal;
        completed();
        if (candidate) Object.assign(server.frames[0], { t2i_image_urls: ['uploads/first.png', 'uploads/second.png'], t2i_selected_index: 0 });
        selectCurrent();
        const response = deferred<Response>();
        vi.mocked(appAccessFetch).mockImplementation(async url => reply(receipt(String(url).split('/').at(-1)!, 'completed', reason)));
        vi.mocked(appAccessFetch).mockImplementationOnce(() => response.promise);
        const view = page();
        await waitFor(() => expect(reads()).toHaveLength(1));
        if (candidate) fireEvent.click(screen.getByRole('button', { name: 'T2I candidate 2' }));
        else fireEvent.change(prompt(), { target: { value: 'keep current authored draft' } });
        await act(async () => response.resolve(reply(receipt(journal.request.client_request_id, 'completed', reason))));
        expect(generate()).toBeDisabled();
        return { journal, view };
    }
    it.each([null, 'studio_image_request_inputs_changed', 'studio_image_request_projection_changed', 'studio_image_request_target_changed'])('keeps %s by an explicit original GET without saving or deleting the journal', async reason => {
        const { journal } = await blocked(reason);
        const stored = JSON.stringify(listStudioAssetImageRequests(owner()));
        const projectReads = vi.mocked(axios.get).mock.calls.length;
        const saves = vi.mocked(axios.post).mock.calls.length;
        fireEvent.click(keep());
        await waitFor(() => expect(reads()).toHaveLength(2));
        await waitFor(() => expect(generate()).not.toBeDisabled());
        expect(reads()[1][0]).toContain(journal.request.client_request_id);
        expect(posts()).toHaveLength(0); expect(axios.patch).not.toHaveBeenCalled();
        expect(axios.post).toHaveBeenCalledTimes(saves); expect(axios.get).toHaveBeenCalledTimes(projectReads);
        expect(prompt()).toHaveValue('keep current authored draft');
        expect(JSON.stringify(listStudioAssetImageRequests(owner()))).toBe(stored);
        expect(screen.queryByRole('button', { name: '保留当前编辑，继续分镜' })).not.toBeInTheDocument();
        expect(screen.getByText('已保留当前编辑；旧结果仍可查看。再次生成需另行操作。')).toBeInTheDocument();
        expect(screen.getAllByAltText('结果 1').length).toBeGreaterThan(0);
    });
    it.each(['lost GET', '503', 'identity', 'proof', 'pending', 'reason'])('does not acknowledge %s on the explicit verification read', async change => {
        await blocked('studio_image_request_inputs_changed');
        vi.mocked(appAccessFetch).mockImplementationOnce(async url => {
            if (change === 'lost GET') throw Error('synthetic read lost');
            if (change === '503') return reply({}, 503);
            const value = receipt(String(url).split('/').at(-1)!, change === 'pending' ? 'pending' : 'completed', change === 'reason' ? 'studio_image_request_owner_changed' : 'studio_image_request_inputs_changed');
            if (change === 'identity') value.request.id = '22222222-2222-4222-8222-222222222222';
            if (change === 'proof') Object.assign(value.request.members[0], { variant_id: 'other-proof', attempt_id: 'other-proof', output_ref: 'assets/studio/other-proof.png' });
            return reply(value);
        });
        fireEvent.click(keep()); await waitFor(() => expect(reads()).toHaveLength(2));
        await act(async () => {});
        expect(generate()).toBeDisabled(); expect(posts()).toHaveLength(0);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(prompt()).toHaveValue('keep current authored draft');
        expect(screen.queryByText('已保留当前编辑；旧结果仍可查看。再次生成需另行操作。')).not.toBeInTheDocument();
    });
    it.each(['studio_image_request_root_changed', 'studio_image_request_owner_changed', 'provider_unknown'])('never offers retention for %s', async reason => {
        await blocked(reason);
        expect(screen.queryByRole('button', { name: '保留当前编辑，继续分镜' })).not.toBeInTheDocument();
        expect(generate()).toBeDisabled(); expect(posts()).toHaveLength(0);
    });
    it('does not release another unknown request when one completed result is retained', async () => {
        await blocked('studio_image_request_inputs_changed');
        beginStudioAssetImageRequest({ ...owner(), idFactory: () => 'other-unconfirmed', input: { asset_type: 'storyboard_frame', generation_type: 'all', asset_id: 'f1', expected_revision: 3, prompt: 'other request', composition_data: {}, batch_size: 1 } });
        vi.mocked(appAccessFetch).mockImplementation(async url => String(url).endsWith('other-unconfirmed') ? reply({}, 503) : reply(receipt(String(url).split('/').at(-1)!, 'completed', 'studio_image_request_inputs_changed')));
        fireEvent.click(await read()); await waitFor(() => expect(reads()).toHaveLength(3));
        fireEvent.click(keep()); await waitFor(() => expect(reads()).toHaveLength(4));
        await act(async () => {});
        expect(generate()).toBeDisabled(); expect(posts()).toHaveLength(0);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(2);
    });
    it.each(['project ABA', 'environment ABA', 'remount'])('does not apply a delayed retain decision across %s', async change => {
        const { view } = await blocked('studio_image_request_inputs_changed');
        const late = deferred<Response>(); vi.mocked(appAccessFetch).mockImplementationOnce(() => late.promise);
        fireEvent.click(keep()); await waitFor(() => expect(reads()).toHaveLength(2));
        if (change === 'project ABA') {
            act(() => useProjectStore.getState().applyProjectReadSnapshot('b', enriched(ack([{ ...frame('f1'), workbench_tab_mode: 't2i_i2v' }], 'b', 3), 8), true));
            act(() => selectCurrent());
        } else if (change === 'environment ABA') act(() => {
            clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
        });
        else { view.unmount(); page(); }
        await waitFor(() => expect(reads().length).toBeGreaterThanOrEqual(3));
        await act(async () => late.resolve(reply(receipt('retained-frame', 'completed', 'studio_image_request_inputs_changed'))));
        expect(generate()).toBeDisabled(); expect(posts()).toHaveLength(0);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(screen.queryByText('已保留当前编辑；旧结果仍可查看。再次生成需另行操作。')).not.toBeInTheDocument();
    });

    it('retains the actual chosen candidate and exact journal bytes', async () => {
        await blocked('studio_image_request_projection_changed', true);
        const journalKey = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)!).find(key => key.startsWith('lumenx.studioAssetImageRequest.v1:'))!;
        const bytes = localStorage.getItem(journalKey);
        fireEvent.click(keep()); await waitFor(() => expect(generate()).not.toBeDisabled());
        expect(screen.getByRole('button', { name: 'T2I candidate 2 (active)' })).toHaveAttribute('aria-pressed', 'true');
        expect(localStorage.getItem(journalKey)).toBe(bytes);
        expect(axios.patch).not.toHaveBeenCalled(); expect(posts()).toHaveLength(0);
    });
    it.each(['503', 'identity', 'proof', 'pending', 'root', 'owner', 'unknown reason'])('revokes a completed decision when a later explicit GET returns %s', async change => {
        await blocked('studio_image_request_inputs_changed');
        fireEvent.click(keep()); await waitFor(() => expect(generate()).not.toBeDisabled());
        vi.mocked(appAccessFetch).mockImplementationOnce(async url => {
            if (change === '503') return reply({}, 503);
            const value = receipt(String(url).split('/').at(-1)!, change === 'pending' ? 'pending' : 'completed',
                change === 'root' ? 'studio_image_request_root_changed' : change === 'owner' ? 'studio_image_request_owner_changed' : change === 'unknown reason' ? 'new_unknown_reason' : 'studio_image_request_inputs_changed');
            if (change === 'identity') value.request.id = '22222222-2222-4222-8222-222222222222';
            if (change === 'proof') Object.assign(value.request.members[0], { output_ref: 'uploads/not-canonical.png' });
            return reply(value);
        });
        fireEvent.click(await read()); await waitFor(() => expect(reads()).toHaveLength(3));
        await act(async () => {});
        expect(generate()).toBeDisabled(); expect(posts()).toHaveLength(0);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(screen.queryByText('已保留当前编辑；旧结果仍可查看。再次生成需另行操作。')).not.toBeInTheDocument();
    });
    it('does not release a second completed result whose project projection is still unconfirmed', async () => {
        await blocked('studio_image_request_inputs_changed');
        beginStudioAssetImageRequest({ ...owner(), idFactory: () => 'other-completed', input: { asset_type: 'storyboard_frame', generation_type: 'all', asset_id: 'f1', expected_revision: 3, prompt: 'other request', composition_data: {}, batch_size: 1 } });
        vi.mocked(appAccessFetch).mockImplementation(async url => {
            const key = String(url).split('/').at(-1)!;
            const value = receipt(key, 'completed', 'studio_image_request_projection_changed');
            if (key === 'other-completed') {
                value.request.id = '22222222-2222-4222-8222-222222222222';
                Object.assign(value.request.members[0], { variant_id: 'other-proof', attempt_id: 'other-proof', output_ref: 'assets/studio/other-proof.png' });
            }
            return reply(value);
        });
        fireEvent.click(await read()); await waitFor(() => expect(reads()).toHaveLength(3));
        fireEvent.click(screen.getAllByRole('button', { name: '保留当前编辑，继续分镜' })[0]);
        await waitFor(() => expect(reads()).toHaveLength(4)); await act(async () => {});
        expect(generate()).toBeDisabled(); expect(posts()).toHaveLength(0);
        expect(screen.getAllByRole('button', { name: '保留当前编辑，继续分镜' })).toHaveLength(1);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(2);
    });
    it('rejects a new generation when the retained local journal was replaced', async () => {
        await blocked('studio_image_request_inputs_changed');
        fireEvent.click(keep()); await waitFor(() => expect(generate()).not.toBeDisabled());
        const journalKey = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)!).find(key => key.startsWith('lumenx.studioAssetImageRequest.v1:'))!;
        const replaced = JSON.parse(localStorage.getItem(journalKey)!); replaced.request.prompt = 'different local request';
        localStorage.setItem(journalKey, JSON.stringify(replaced));
        fireEvent.click(generate()); await act(async () => {});
        expect(posts()).toHaveLength(0); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(generate()).toBeDisabled();
    });
    it('allows history retention after project read failure but a new generation still requires a readable project', async () => {
        const journal = beginStudioAssetImageRequest({ ...owner(), idFactory: () => 'retained-frame', input: { asset_type: 'storyboard_frame', generation_type: 'all', asset_id: 'f1', expected_revision: 3, prompt: 'prompt f1', composition_data: {}, batch_size: 1 } }).journal;
        completed(); selectCurrent();
        vi.mocked(axios.get).mockRejectedValue(Error('project offline'));
        vi.mocked(appAccessFetch).mockImplementation(async () => reply(receipt(journal.request.client_request_id, 'completed')));
        page(); await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
        fireEvent.click(keep()); await waitFor(() => expect(generate()).not.toBeDisabled());
        expect(axios.get).toHaveBeenCalledTimes(1); expect(posts()).toHaveLength(0);
        fireEvent.click(generate()); await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
    it('retains old history through a separately started new request, automatic polling and reopening', async () => {
        const { view } = await blocked('studio_image_request_inputs_changed');
        vi.mocked(axios.post).mockImplementation(async (_url, body) => {
            expect(body).toMatchObject({ visual_description: 'keep current authored draft', expected_revision: 4 });
            server = { ...server, revision: 5, read_versions: { ...server.read_versions, project: 5 }, frames: [{ ...server.frames[0], visual_description: 'keep current authored draft' }] };
            return { data: server };
        });
        fireEvent.click(keep()); await waitFor(() => expect(generate()).not.toBeDisabled());
        const original = JSON.stringify(listStudioAssetImageRequests(owner()));
        expect(posts()).toHaveLength(0);
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 850)); });
        let nextKey = '';
        const nextReceipt = (status: string) => {
            const value = receipt(nextKey, status); value.request.id = '22222222-2222-4222-8222-222222222222';
            Object.assign(value.request.members[0], { variant_id: 'next-proof', attempt_id: 'next-proof', ...(status === 'completed' ? { output_ref: 'assets/studio/next-proof.png' } : {}) });
            return value;
        };
        vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
            if (init?.method === 'POST') {
                const body = JSON.parse(String(init.body)); nextKey = body.client_request_id;
                expect(body).toMatchObject({ expected_revision: 5, prompt: 'keep current authored draft' });
                server = { ...server, revision: 6, read_versions: { ...server.read_versions, project: 6 }, frames: [{ ...server.frames[0],
                    image_request_id: '22222222-2222-4222-8222-222222222222', image_url: 'assets/studio/next-proof.png', rendered_image_url: 'assets/studio/next-proof.png',
                    rendered_image_asset: { selected_id: 'next-proof', variants: [{ id: wire.readback.request.members[0].variant_id, url: wire.readback.request.members[0].output_ref }, { id: 'next-proof', url: 'assets/studio/next-proof.png' }] } }] };
                return reply(nextReceipt('pending'));
            }
            return String(url).endsWith('retained-frame') ? reply(receipt('retained-frame', 'completed', 'studio_image_request_target_changed')) : reply(nextReceipt('completed'));
        });
        vi.useFakeTimers(); fireEvent.click(generate());
        await act(async () => vi.advanceTimersByTimeAsync(0));
        expect(posts()).toHaveLength(1); expect(nextKey).not.toBe('retained-frame');
        await act(async () => vi.advanceTimersByTimeAsync(2500)); vi.useRealTimers();
        await waitFor(() => expect(listStudioAssetImageRequests(owner())).toHaveLength(1));
        expect(reads().filter(([url]) => String(url).endsWith('retained-frame'))).toHaveLength(2);
        expect(reads().filter(([url]) => String(url).endsWith(nextKey))).toHaveLength(1);
        expect(JSON.stringify(listStudioAssetImageRequests(owner()))).toBe(original);
        expect(generate()).not.toBeDisabled();
        fireEvent.click(await read()); await waitFor(() => expect(reads()).toHaveLength(4));
        expect(generate()).not.toBeDisabled(); expect(posts()).toHaveLength(1);
        view.unmount(); page(); await waitFor(() => expect(reads()).toHaveLength(5));
        await waitFor(() => expect(keep()).toBeInTheDocument());
        expect(generate()).toBeDisabled();
        fireEvent.click(keep()); await waitFor(() => expect(reads()).toHaveLength(6));
        await waitFor(() => expect(generate()).not.toBeDisabled());
        expect(posts()).toHaveLength(1); expect(JSON.stringify(listStudioAssetImageRequests(owner()))).toBe(original);
    });
});
