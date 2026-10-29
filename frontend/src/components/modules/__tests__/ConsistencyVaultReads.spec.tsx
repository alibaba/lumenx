import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { activateRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import ConsistencyVault from '../ConsistencyVault';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
function read(library = 8, revision = 3, name = 'Alice', id = 'a') {
    return enriched({ ...ack([], id, revision), characters: [{ id: 'shared', name, description: 'red coat', source: 'global' }],
        scenes: [{ id: 'room', name: 'Room', description: 'room description', source: 'episode', image_url: '/v1.png',
            image_asset: { selected_id: 'v1', variants: [{ id: 'v1', url: '/v1.png' }, { id: 'v2', url: '/v2.png' }] } }] }, library);
}
const projectGets = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).includes('/projects/'));
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); vi.spyOn(window, 'alert').mockImplementation(() => {});
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    useProjectStore.setState({ currentProject: project(), projects: [project()], currentSeries: null, readWatermarks: {}, generatingTasks: [], runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('a', read());
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
describe('ConsistencyVault command read confirmation', () => {

    it.each(['needs_confirmation', 'completed', 'pending'])('allows an explicit request after reload but keeps live submissions busy: %s', async status => {
        const snapshot = read(9); Object.assign(snapshot.characters[0], { status });
        useProjectStore.getState().applyProjectReadSnapshot('a', snapshot);
        useProjectStore.getState().addGeneratingTask('shared', 'all', 1);
        const accepted = deferred<{ data: unknown }>(); vi.mocked(axios.post).mockReturnValue(accepted.promise);
        const view = renderWithIntl(<ConsistencyVault />);
        fireEvent.click(screen.getByRole('button', { name: status === 'needs_confirmation' ? '发起新生成' : 'Generate' })); await act(async () => {});
        expect(axios.post).toHaveBeenCalledOnce();
        view.unmount(); renderWithIntl(<ConsistencyVault />);
        expect(screen.queryByRole('button', { name: status === 'needs_confirmation' ? '发起新生成' : 'Generate' })).toBeNull();
        await act(async () => accepted.resolve({ data: { ...ack(), _task_id: 'accepted' } }));
    });

    it('rejects a pending recovery read after the same project changes series owner', async () => {
        const base = read(9);
        const owned = (owner: string, revision: number) => enriched({ ...base, revision, series_id: owner,
            characters: [{ ...base.characters[0], source: 'series', status: 'needs_confirmation' }] }, 9, 1);
        useProjectStore.getState().applyProjectReadSnapshot('a', owned('series-a', 4));
        const pending = deferred<{ data: ReturnType<typeof owned> }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : pending.promise);
        renderWithIntl(<ConsistencyVault />);
        fireEvent.click(screen.getByRole('button', { name: '重新读取资产' }));
        act(() => { useProjectStore.getState().applyProjectReadSnapshot('a', owned('series-b', 5)); });
        expect(screen.getByRole('button', { name: '重新读取资产' })).toBeEnabled();
        await act(async () => { pending.resolve({ data: owned('series-a', 6) }); });
        expect(useProjectStore.getState().currentProject?.series_id).toBe('series-b');
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('shows a neutral recovery notice from the asset snapshot without generating again', async () => {
        const snapshot = read(9); Object.assign(snapshot.characters[0], { status: 'needs_confirmation' });
        useProjectStore.getState().applyProjectReadSnapshot('a', snapshot);
        vi.mocked(axios.get).mockResolvedValue({ data: snapshot });
        renderWithIntl(<ConsistencyVault />);
        expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '重新读取资产' }));
        await act(async () => {});
        expect(projectGets()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
    });
    it('reads back a pending task that needs confirmation without a false failure or another generation', async () => {
        vi.useFakeTimers();
        const snapshot = read(9, 4);
        Object.assign(snapshot.characters[0], { status: 'needs_confirmation', full_body: {
            image_variants: [{ id: 'saved-candidate', url: 'assets/studio/first-result.png' }], selected_image_id: 'saved-candidate',
        } });
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: 'partial-result' } });
        let polls = 0;
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).includes('/tasks/')
            ? { status: ++polls === 1 ? 'pending' : 'needs_confirmation' } : snapshot }));
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await act(async () => {}); await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
        expect(polls).toBe(1); expect(projectGets()).toHaveLength(0);
        expect(useProjectStore.getState().generatingTasks).toHaveLength(1);
        await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
        expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
        expect(useProjectStore.getState().currentProject?.characters[0].full_body?.image_variants).toEqual([
            expect.objectContaining({ id: 'saved-candidate', url: 'assets/studio/first-result.png' }),
        ]);
        expect(useProjectStore.getState().generatingTasks).toHaveLength(0);
        expect(window.alert).not.toHaveBeenCalled();
        await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
        expect(polls).toBe(2); expect(projectGets()).toHaveLength(1); expect(axios.post).toHaveBeenCalledOnce();
    });
    it('performs one read-only refresh on task 404 and keeps unknown outcome neutral', async () => {
        vi.useFakeTimers();
        const snapshot = read(9, 4); Object.assign(snapshot.characters[0], { status: 'needs_confirmation' });
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: 'lost-task' } });
        vi.mocked(axios.get).mockImplementation(url => String(url).includes('/tasks/') ? Promise.reject({ response: { status: 404 } }) : Promise.resolve({ data: snapshot }));
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await act(async () => {}); await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
        expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
        expect(projectGets()).toHaveLength(1); expect(axios.post).toHaveBeenCalledOnce();
        expect(window.alert).not.toHaveBeenCalledWith(expect.stringMatching(/生成失败|已保存/));
    });

    it.each(['old-image', 'offline'])('does not call an unconfirmed 404 saved or failed when readback is %s', async scenario => {
        vi.useFakeTimers();
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: 'lost' } });
        vi.mocked(axios.get).mockImplementation(url => String(url).includes('/tasks/') || scenario === 'offline'
            ? Promise.reject({ response: { status: 404 } }) : Promise.resolve({ data: read(9, 4) }));
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await act(async () => {}); await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
        expect(projectGets()).toHaveLength(1); expect(axios.post).toHaveBeenCalledOnce();
        expect(window.alert).not.toHaveBeenCalledWith(expect.stringMatching(/生成失败|已保存/));
        expect(screen.queryByText('资产生成结果待确认')).toBeNull();
    });

    it('does not let an old runtime poll clear the new same-ID task', async () => {
        vi.useFakeTimers(); const pending = deferred<{ data: { status: string } }>();
        const owner = requireActiveRuntimeEnvironment();
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: 'old' } });
        vi.mocked(axios.get).mockReturnValue(pending.promise);
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await act(async () => {}); await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
        const marker = { assetId: 'shared', generationType: 'all', batchSize: 1 };
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' });
            activateRuntimeEnvironment({ schema_version: 1, storage_id: owner.storageId });
            useProjectStore.setState({ generatingTasks: [marker] });
        });
        await act(async () => { pending.resolve({ data: { status: 'completed' } }); });
        expect(useProjectStore.getState().generatingTasks).toEqual([marker]);
        expect(projectGets()).toHaveLength(0);
    });

    it('does not label successful create as failed or repeat it after its readback fails', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: ack() }); vi.mocked(axios.get).mockRejectedValue(new Error('offline'));
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByText('Add character'));
        fireEvent.change(screen.getByPlaceholderText('Enter character name'), { target: { value: 'New' } });
        fireEvent.click(screen.getByRole('button', { name: 'Create Character' }));
        await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/)));
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Create Character' })).not.toBeInTheDocument());
        expect(axios.post).toHaveBeenCalledOnce(); expect(projectGets()).toHaveLength(1);
        expect(useProjectStore.getState().currentProject?.characters[0].source).toBe('global');
    });
    it('validates raw delete ACK before reading, and does not claim saved for garbage', async () => {
        vi.mocked(axios.delete).mockResolvedValue({ data: { ok: true } }); vi.mocked(axios.get).mockResolvedValue({ data: read(9, 4) });
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByTitle('Delete'));
        await waitFor(() => expect(window.alert).toHaveBeenCalled());
        expect(axios.get).not.toHaveBeenCalled(); expect(window.alert).not.toHaveBeenCalledWith(expect.stringMatching(/已保存/));
    });
    it('confirms raw synchronous generation once without erasing shared provenance', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: ack() }); vi.mocked(axios.get).mockResolvedValue({ data: read(9, 4, 'Generated') });
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(screen.getByText('Generated')).toBeInTheDocument());
        expect(useProjectStore.getState().currentProject?.characters[0].source).toBe('global');
        expect(projectGets()).toHaveLength(1); expect(axios.post).toHaveBeenCalledOnce();
        expect(useProjectStore.getState().generatingTasks).toHaveLength(0);
    });
    it('rejects a scene variant ACK and its one stale GET', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: read(7, 4) }); vi.mocked(axios.get).mockResolvedValue({ data: read(7, 4) });
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: /Scenes/ })); fireEvent.click(screen.getByText('Room'));
        fireEvent.click(screen.getAllByAltText('Variant')[1]);
        await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/)));
        expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(8);
        expect(useProjectStore.getState().projects[0].read_versions?.library).toBe(8);
        expect(projectGets()).toHaveLength(1); expect(axios.post).toHaveBeenCalledOnce();
    });
    it('keeps the description draft visible after a failed write', async () => {
        vi.mocked(axios.post).mockRejectedValue(new Error('unavailable'));
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: /Scenes/ })); fireEvent.click(screen.getByText('Room'));
        fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
        fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'my draft' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save Description' }));
        await waitFor(() => expect(window.alert).toHaveBeenCalled());
        expect(screen.getByDisplayValue('my draft')).toBeInTheDocument(); expect(axios.get).not.toHaveBeenCalled();
    });
    it('serializes status polls and stops after a completed task has an incomparable read', async () => {
        vi.useFakeTimers(); const status = deferred<{ data: { status: string } }>();
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: 'task' } });
        vi.mocked(axios.get).mockImplementation(url => String(url).includes('/tasks/') ? status.promise : Promise.resolve({ data: read(7, 4) }));
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await act(async () => {});
        await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
        expect(axios.get).toHaveBeenCalledOnce();
        await act(async () => { status.resolve({ data: { status: 'completed' } }); });
        expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/));
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(axios.get).toHaveBeenCalledTimes(2); expect(projectGets()).toHaveLength(1);
        expect(axios.post).toHaveBeenCalledOnce(); expect(useProjectStore.getState().generatingTasks).toHaveLength(0);
        expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(8);
    });
    it('keeps terminal task reads on the original project after navigation and remount', async () => {
        vi.useFakeTimers(); vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: 'task' } });
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).includes('/tasks/') ? { status: 'completed' } : read(9, 4, 'Generated') }));
        const view = renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await act(async () => {}); view.unmount();
        act(() => { useProjectStore.setState({ currentProject: project('b'), projects: [...useProjectStore.getState().projects, project('b')] }); });
        renderWithIntl(<ConsistencyVault />);
        await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(useProjectStore.getState().projects.find(p => p.id === 'a')?.read_versions?.library).toBe(9);
        expect(projectGets()[0][0]).toEqual(expect.stringContaining('/projects/a')); expect(axios.post).toHaveBeenCalledOnce();
    });
    it.each([42, ''])('does not query after a malformed task acknowledgement with task id %j', async taskId => {
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: taskId } });
        vi.mocked(axios.get).mockResolvedValue({ data: read(9, 4) });
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await waitFor(() => expect(useProjectStore.getState().generatingTasks).toHaveLength(0));
        expect(window.alert).toHaveBeenCalled(); expect(axios.get).not.toHaveBeenCalled(); expect(axios.post).toHaveBeenCalledOnce();
    });
    it('does not confirm a completed task with a read older than the submission ACK', async () => {
        vi.useFakeTimers(); vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: 'task' } });
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).includes('/tasks/') ? { status: 'completed' } : read(8, 3) }));
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await act(async () => {}); await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
        expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/));
        expect(axios.get).toHaveBeenCalledTimes(2); expect(axios.post).toHaveBeenCalledOnce();
    });
    it.each(['sync', 'lock', 'description'])('confirms the raw %s response with one enriched GET', async operation => {
        vi.mocked(axios.post).mockResolvedValue({ data: ack() }); vi.mocked(axios.get).mockResolvedValue({ data: read(9, 4) });
        renderWithIntl(<ConsistencyVault />);
        if (operation === 'sync') fireEvent.click(screen.getByRole('button', { name: /同步描述/ }));
        else if (operation === 'lock') fireEvent.click(screen.getByTitle('Lock'));
        else {
            fireEvent.click(screen.getByRole('button', { name: /Scenes/ })); fireEvent.click(screen.getByText('Room'));
            fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
            fireEvent.click(screen.getByRole('button', { name: 'Save Description' }));
        }
        await waitFor(() => expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(9));
        expect(useProjectStore.getState().currentProject?.characters[0].source).toBe('global');
        expect(axios.post).toHaveBeenCalledOnce(); expect(projectGets()).toHaveLength(1);
    });
    it('ends motion polling on read rejection and does not send another generation', async () => {
        vi.useFakeTimers();
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: 'motion' } });
        let sourceReads = 0;
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).includes('/tasks/') ? { status: 'completed' }
            : ++sourceReads === 1 ? read() : read(7, 4) }));
        // The existing legacy poll is reached through the same-owner preflight.
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ project_id: 'a', asset_id: 'room',
            asset_type: 'scene', eligible: false, model_id: 'kling-v1', reason: 'model_unsupported',
            owner: { kind: 'script', id: 'a' }, selected_image_id: 'v1', source_kind: 'local_path', expected_target: 'a'.repeat(64) }), { status: 200 })));
        Object.defineProperty(navigator, 'locks', { configurable: true, value: {
            request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: /Scenes/ })); fireEvent.click(screen.getByText('Room'));
        fireEvent.click(screen.getByRole('button', { name: 'Video Reference' }));
        await act(async () => {});
        fireEvent.click(screen.getByRole('button', { name: /生成视频/ }));
        await act(async () => {}); await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
        expect(axios.post).toHaveBeenCalledOnce(); expect(projectGets()).toHaveLength(2);
        expect(axios.get).toHaveBeenCalledTimes(3);
        expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/));
        expect(useProjectStore.getState().generatingTasks).toHaveLength(0);
    });
    it('keeps failed-task and read-failure feedback separate and ends polling', async () => {
        vi.useFakeTimers();
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack(), _task_id: 'failed-task' } });
        vi.mocked(axios.get).mockImplementation(url => String(url).includes('/tasks/')
            ? Promise.resolve({ data: { status: 'failed', error: 'provider failed' } }) : Promise.reject(new Error('offline')));
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
        await act(async () => {}); await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
        expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/生成失败/));
        expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/无法确认最新素材/));
        expect(window.alert).not.toHaveBeenCalledWith(expect.stringMatching(/已保存/));
        expect(axios.get).toHaveBeenCalledTimes(2); expect(useProjectStore.getState().generatingTasks).toHaveLength(0);
    });
    it('shows saved read failure for rejected enriched upload ACK without uploading again', async () => {
        const upload = vi.fn().mockResolvedValue({ ok: true, json: async () => read(7, 4) });
        vi.stubGlobal('fetch', upload); vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview');
        vi.mocked(axios.get).mockRejectedValue(new Error('offline'));
        const view = renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByTitle('上传资产'));
        fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['image'], 'image.png', { type: 'image/png' })] } });
        fireEvent.click(screen.getByRole('button', { name: /确认上传/ }));
        await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/)));
        expect(upload).toHaveBeenCalledOnce(); expect(projectGets()).toHaveLength(1);
        expect(useProjectStore.getState().currentProject?.characters[0].source).toBe('global');
    });
    it('applies an enriched upload ACK without an unnecessary GET', async () => {
        const upload = vi.fn().mockResolvedValue({ ok: true, json: async () => read(9, 4, 'Uploaded') });
        vi.stubGlobal('fetch', upload); vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview');
        vi.mocked(axios.get).mockRejectedValue(new Error('unexpected read'));
        const view = renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByTitle('上传资产'));
        fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['image'], 'image.png', { type: 'image/png' })] } });
        fireEvent.click(screen.getByRole('button', { name: /确认上传/ }));
        await waitFor(() => expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(9));
        expect(upload).toHaveBeenCalledOnce(); expect(axios.get).not.toHaveBeenCalled();
        expect(window.alert).not.toHaveBeenCalled();
    });
    it.each(['project switch', 'same-project reopen'])('does not let an old upload close a newer picker after %s', async scenario => {
        const pending = deferred<{ ok: boolean; json: () => Promise<ReturnType<typeof read>> }>();
        const upload = vi.fn().mockReturnValue(pending.promise); vi.stubGlobal('fetch', upload);
        vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview'); vi.mocked(axios.get).mockResolvedValue({ data: read(9, 4) });
        const view = renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByTitle('上传资产'));
        fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['image'], 'image.png', { type: 'image/png' })] } });
        fireEvent.click(screen.getByRole('button', { name: /确认上传/ }));
        await waitFor(() => expect(upload).toHaveBeenCalledOnce());
        if (scenario === 'project switch') act(() => { useProjectStore.getState().applyProjectReadSnapshot('b', read(8, 3, 'Bob', 'b'), true); });
        else fireEvent.click(screen.getByRole('button', { name: '取消' }));
        fireEvent.click(screen.getByTitle('上传资产'));
        await act(async () => { pending.resolve({ ok: true, json: async () => read(7, 4) }); });
        expect(useProjectStore.getState().currentProject?.id).toBe(scenario === 'project switch' ? 'b' : 'a');
        expect(screen.getByRole('button', { name: /确认上传/ })).toBeInTheDocument();
        expect(screen.getAllByText(scenario === 'project switch' ? /Bob/ : /Alice/).length).toBeGreaterThan(0);
        expect(projectGets()[0][0]).toEqual(expect.stringContaining('/projects/a')); expect(upload).toHaveBeenCalledOnce();
    });
    it('does not close a newly opened create dialog when an old create read completes', async () => {
        const pending = deferred<{ data: ReturnType<typeof read> }>();
        vi.mocked(axios.post).mockResolvedValue({ data: ack() }); vi.mocked(axios.get).mockReturnValue(pending.promise);
        renderWithIntl(<ConsistencyVault />); fireEvent.click(screen.getByText('Add character'));
        fireEvent.change(screen.getByPlaceholderText('Enter character name'), { target: { value: 'First' } });
        fireEvent.click(screen.getByRole('button', { name: 'Create Character' }));
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        await waitFor(() => expect(screen.queryByPlaceholderText('Enter character name')).not.toBeInTheDocument());
        fireEvent.click(screen.getByText('Add character'));
        fireEvent.change(screen.getByPlaceholderText('Enter character name'), { target: { value: 'Second draft' } });
        await act(async () => { pending.resolve({ data: read(9, 4) }); });
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 350)); });
        expect(screen.getByDisplayValue('Second draft')).toBeInTheDocument();
        expect(axios.post).toHaveBeenCalledOnce();
    });
});
