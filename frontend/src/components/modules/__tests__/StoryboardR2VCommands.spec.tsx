import type { ComponentProps } from 'react';
import type ShotCard from '../storyboard-r2v/ShotCard';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { activateProjectEnvironment, deactivateProjectEnvironment, useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardR2V from '../StoryboardR2V';
import { ack, deferred, enriched, frame as baseFrame, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
// Keep the actual parent, store, frame mapper, and API transport. The card
// shell exposes its callbacks/data without generating media or mounting editors.
vi.mock('../storyboard-r2v/ShotCard', () => ({
    T2I_HISTORY_LIMIT: 10,
    default: (p: ComponentProps<typeof ShotCard>) => <section data-testid="shot" aria-label={p.shot.id}>
        <output data-testid="shot-data">{JSON.stringify(p.shot)}</output>
        <textarea aria-label="prompt" value={p.shot.prompt} onChange={event => p.onUpdatePrompt(event.target.value)} />
        <button onClick={() => p.onUpdateField('shotSize', 'close_up')}>edit field</button>
        <button onClick={() => p.onUpdateField('cameraAngle', 'high_angle')}>edit angle</button>
        <button onClick={() => p.onUpdateField('duration', 6)}>edit duration</button>
        <button onClick={() => p.onUpdateField('shotSize', null)}>clear field</button>
        <button onClick={() => p.onSetTabMode('t2i_i2v')}>edit workbench</button>
        <button onClick={() => p.onSetTabMode('direct_r2v')}>reset workbench</button>
        <button onClick={() => p.onSetGenerateCount?.(3)}>edit count</button>
        <button disabled={p.structureDisabled} onClick={p.onDuplicate}>copy</button>
        <button disabled={p.structureDisabled} onClick={p.onDelete}>delete</button>
        <button disabled={p.structureDisabled || p.index === p.totalShots - 1} onClick={p.onMoveDown}>down</button>
    </section>,
}));
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/TaskQueuePanel', () => ({ default: () => null }));

const frame = (id: string) => ({ ...baseFrame(id), updated_at: 100 });
const STORAGE_A = '11111111-1111-4111-8111-111111111111';
const STORAGE_B = '22222222-2222-4222-8222-222222222222';
function activateStorage(storageId: string) {
    return activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
}
function firstButton(name: string) { return within(screen.getAllByTestId('shot')[0]).getByRole('button', { name }); }
function shots() { return screen.queryAllByTestId('shot').map(node => node.getAttribute('aria-label')); }
beforeEach(() => useProjectStore.setState({ readWatermarks: {} }));
const actions = [
    { name: 'copy', method: 'post', result: [frame('f1'), frame('copy'), frame('f2')] },
    { name: 'delete', method: 'delete', result: [frame('f2')] },
    { name: 'down', method: 'put', result: [frame('f2'), frame('f1')] },
] as const;

describe('StoryboardR2V committed structural commands', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        localStorage.clear();
        useProjectStore.setState({ projects: [project()], currentProject: project(), runningOps: {}, renderingFrames: new Set() });
    });
    afterEach(() => vi.useRealTimers());

    it.each(actions)('$name waits for ACK and updates both the rendered shots and full store snapshot', async command => {
        const request = deferred();
        vi.mocked(axios[command.method]).mockReturnValueOnce(request.promise);
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton(command.name));
        expect(shots()).toEqual(['f1', 'f2']);
        expect(firstButton(command.name)).toBeDisabled();
        await act(async () => request.resolve({ data: ack([...command.result]) }));
        expect(shots()).toEqual(command.result.map(f => f.id));
        expect(useProjectStore.getState().currentProject).toMatchObject({ revision: 4, originalText: 'server text' });
        expect(useProjectStore.getState().currentProject?.characters[0].id).toBe('shared');
        expect(axios.get).not.toHaveBeenCalled();
    });

    it.each(actions.flatMap(command => [409, 503, 0].map(status => ({ ...command, status }))))(
        '$name rejection $status keeps confirmed shots and provides visible feedback', async command => {
            vi.mocked(axios[command.method]).mockRejectedValueOnce(command.status ? { response: { status: command.status } } : new Error('network'));
            renderWithIntl(<StoryboardR2V />);
            fireEvent.click(firstButton(command.name));
            expect(await screen.findByRole('alert')).toHaveTextContent(/未确认|冲突|保存|服务/);
            expect(shots()).toEqual(['f1', 'f2']);
            expect(useProjectStore.getState().currentProject?.revision).toBe(3);
            expect(axios[command.method]).toHaveBeenCalledTimes(1);
            expect(axios.get).not.toHaveBeenCalled();
            expect(firstButton(command.name)).not.toBeDisabled();
        },
    );

    it('uses server copy content including media, history and pin state identically after remount', async () => {
        const original = { ...frame('f1'), image_url: '/first.png', video_url: '/movie.mp4', selected_video_id: 'v1',
            is_video_pinned: true, final_take_id: 'v1', t2i_image_urls: ['/one.png', '/two.png'], t2i_selected_index: 1 };
        const copied = { ...original, id: 'copy', locked: false, selected_video_id: 'v1-copy', final_take_id: 'v1-copy' };
        const task = { id: 'v1', frame_id: 'f1', status: 'completed', video_url: '/movie.mp4', image_url: '', prompt: '', created_at: 1 };
        vi.mocked(axios.post).mockResolvedValueOnce({ data: { ...ack([original, copied, frame('f2')]),
            video_tasks: [task, { ...task, id: 'v1-copy', frame_id: 'copy' }] } });
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('copy'));
        await waitFor(() => expect(shots()).toContain('copy'));
        const rendered = screen.getAllByTestId('shot-data').map(el => el.textContent);
        expect(JSON.parse(rendered[1]!)).toMatchObject({ imageUrl: '/first.png', videoUrl: '/movie.mp4', isVideoPinned: true,
            finalTakeId: 'v1-copy', videoTaskId: 'v1-copy', t2iImageUrls: ['/one.png', '/two.png'], t2iSelectedIndex: 1 });
        view.unmount(); renderWithIntl(<StoryboardR2V />);
        expect(screen.getAllByTestId('shot-data').map(el => el.textContent)).toEqual(rendered);
    });

    it('blocks same-tick duplicates and keeps newer local shots when an older ACK arrives', async () => {
        const request = deferred(); vi.mocked(axios.post).mockReturnValueOnce(request.promise);
        renderWithIntl(<StoryboardR2V />);
        const copy = firstButton('copy');
        act(() => { fireEvent.click(copy); fireEvent.click(copy); });
        expect(axios.post).toHaveBeenCalledTimes(1);
        act(() => useProjectStore.setState({ currentProject: { ...project('a', 5), frames: [frame('newer')] } }));
        await act(async () => request.resolve({ data: ack() }));
        expect(shots()).toEqual(['newer']);
        expect(useProjectStore.getState().projects[0].revision).toBe(4);
    });

    it.each([false, true])('isolates project switches and old mount callbacks (return to A: %s)', async backToA => {
        const request = deferred(); vi.mocked(axios.post).mockReturnValueOnce(request.promise);
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('copy'));
        act(() => useProjectStore.setState({ currentProject: { ...project('b'), frames: [frame('B')] } }));
        expect(shots()).toEqual(['B']);
        if (backToA) act(() => useProjectStore.setState({ currentProject: { ...project('a', 6), frames: [frame('freshA')] } }));
        await act(async () => request.resolve({ data: ack() }));
        expect(shots()).toEqual(backToA ? ['freshA'] : ['B']);
        expect(useProjectStore.getState().currentProject?.id).toBe(backToA ? 'a' : 'b');
    });

    it.each(['copy', 'delete', 'down'])('does not pretend a synthetic %s was saved', async action => {
        useProjectStore.setState({ currentProject: { ...project(), frames: [frame('shot_pending'), frame('f2')] } });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton(action));
        expect(await screen.findByRole('alert')).toHaveTextContent(/未落库|生成/);
        expect(shots()).toEqual(['shot_pending', 'f2']);
        expect(axios.post).not.toHaveBeenCalled(); expect(axios.put).not.toHaveBeenCalled(); expect(axios.delete).not.toHaveBeenCalled();
    });

    it.each(['prompt', 'field', 'workbench'])('protects pending, in-flight and failed %s edits from a structural command', async edit => {
        vi.useFakeTimers();
        const save = deferred();
        vi.mocked(axios.post).mockReturnValue(save.promise);
        vi.mocked(axios.patch).mockReturnValue(save.promise);
        renderWithIntl(<StoryboardR2V />);
        if (edit === 'prompt') fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'unsaved draft' } });
        else fireEvent.click(firstButton(`edit ${edit}`));
        fireEvent.click(firstButton('copy'));
        expect(screen.getByRole('alert')).toHaveTextContent(/待保存|正在保存/);
        expect(axios.post).not.toHaveBeenCalled();
        await act(async () => vi.advanceTimersByTime(3100));
        fireEvent.click(firstButton('copy'));
        expect(screen.getByRole('alert')).toHaveTextContent(/待保存|正在保存/);
        await act(async () => save.reject({ response: { status: 503 } }));
        fireEvent.click(firstButton('copy'));
        expect(screen.getByRole('alert')).toHaveTextContent(/编辑保存失败/);
        expect(shots()).toEqual(['f1', 'f2']);
        expect(vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/frames/copy'))).toHaveLength(0);
        if (edit === 'prompt') expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('unsaved draft');
    });

    it('sends the real reorder IDs through the API transport', async () => {
        vi.mocked(axios.put).mockResolvedValueOnce({ data: ack([frame('f2'), frame('f1')]) });
        renderWithIntl(<StoryboardR2V />); fireEvent.click(firstButton('down'));
        await waitFor(() => expect(axios.put).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/reorder`, { frame_ids: ['f2', 'f1'] }));
    });

    it('keeps the typed prompt after its edit save and copies only after that save completes', async () => {
        vi.useFakeTimers();
        const save = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(save.promise);
        useProjectStore.setState({ currentProject: { ...project(), frames: [{ ...frame('f1'), action_description: 'legacy summary', visual_description: 'previous visual' }, frame('f2')] } });
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'new draft' } });
        fireEvent.click(firstButton('copy'));
        await act(async () => vi.advanceTimersByTime(800));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/update`, { frame_id: 'f1', expected_revision: 3, visual_description: 'new draft' });
        const edited = { ...frame('f1'), action_description: 'legacy summary', visual_description: 'new draft' };
        await act(async () => save.resolve({ data: ack([edited, frame('f2')]) }));
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('new draft');
        vi.mocked(axios.post).mockResolvedValueOnce({ data: ack([edited, { ...edited, id: 'copy' }], 'a', 5) });
        await act(async () => fireEvent.click(firstButton('copy')));
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/copy`, { frame_id: 'f1', insert_at: 1 });
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[1]).toHaveValue('new draft');
        expect(useProjectStore.getState().currentProject?.frames[1].action_description).toBe('legacy summary');
        view.unmount(); renderWithIntl(<StoryboardR2V />);
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('new draft');
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[1]).toHaveValue('new draft');
    });

    it('tracks edits flushed by an unmounted instance until ACK and maps the edited visual on remount', async () => {
        const save = deferred(); vi.mocked(axios.post).mockReturnValueOnce(save.promise);
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'flushed draft' } });
        view.unmount();
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('copy'));
        expect(screen.getByRole('alert')).toHaveTextContent(/待保存|正在保存/);
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/update`, { frame_id: 'f1', expected_revision: 3, visual_description: 'flushed draft' });
        await act(async () => save.resolve({ data: ack([{ ...frame('f1'), visual_description: 'flushed draft' }, frame('f2')]) }));
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('flushed draft');
        expect(useProjectStore.getState().currentProject?.revision).toBe(4);
    });

    it('keeps structure blocked after an old mount edit fails instead of mistaking remount for a save', async () => {
        const save = deferred(); vi.mocked(axios.post).mockReturnValueOnce(save.promise);
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'draft' } });
        view.unmount(); renderWithIntl(<StoryboardR2V />);
        await act(async () => save.reject({ response: { status: 503 } }));
        fireEvent.click(firstButton('copy'));
        expect(screen.getByRole('alert')).toHaveTextContent(/编辑保存失败/);
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('keeps the project command gate across unmount and refreshes the new mount from its ACK', async () => {
        const request = deferred(); vi.mocked(axios.post).mockReturnValueOnce(request.promise);
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('copy'));
        view.unmount(); renderWithIntl(<StoryboardR2V />);
        expect(firstButton('copy')).toBeDisabled();
        await act(async () => request.resolve({ data: ack() }));
        expect(shots()).toEqual(['f1', 'copy', 'f2']);
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(firstButton('copy')).toBeEnabled();
    });

    it.each(['success', '503'] as const)('keeps a reconnected structural command owned by the new session after old %s', async outcome => {
        const oldRequest = deferred();
        const newRequest = deferred();
        // A higher old revision must not mask a missing runtime owner check.
        const oldAck = ack([frame('f1'), frame('old-copy'), frame('f2')], 'a', 11);
        const newAck = ack([frame('f1'), frame('new-copy'), frame('f2')], 'a', 8);
        vi.mocked(axios.post).mockReset().mockReturnValueOnce(oldRequest.promise).mockReturnValueOnce(newRequest.promise);
        const firstEnvironment = activateStorage(STORAGE_A);
        await activateProjectEnvironment(firstEnvironment.key, firstEnvironment.generation);
        const first = project();
        useProjectStore.setState({ projects: [first], currentProject: first, readWatermarks: {} });
        renderWithIntl(<StoryboardR2V />);
        const operation = `frame-command:${encodeURIComponent(firstEnvironment.key)}:a`;
        const errorPrefix = `frame-command-error:${encodeURIComponent(firstEnvironment.key)}:a:`;
        try {
            fireEvent.click(firstButton('copy'));
            await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
            expect(useProjectStore.getState().runningOps[operation]).toBe(true);
            expect(firstButton('copy')).toBeDisabled();

            act(() => { clearRuntimeEnvironment(); deactivateProjectEnvironment(); });
            expect(shots()).toEqual([]);
            await act(async () => {
                const nextEnvironment = activateStorage(STORAGE_A);
                await activateProjectEnvironment(nextEnvironment.key, nextEnvironment.generation);
                const next = { ...project('a', 7), originalText: 'current connection text' };
                useProjectStore.setState({ projects: [next], currentProject: next, readWatermarks: {} });
            });
            expect(getRuntimeEnvironment()?.key).toBe(firstEnvironment.key);
            expect(getRuntimeEnvironment()?.generation).not.toBe(firstEnvironment.generation);
            expect(useProjectStore.getState().runningOps).toEqual({});
            fireEvent.click(firstButton('copy'));
            await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
            expect(useProjectStore.getState().runningOps[operation]).toBe(true);
            expect(firstButton('copy')).toBeDisabled();

            await act(async () => {
                if (outcome === '503') oldRequest.reject({ response: { status: 503 } });
                else oldRequest.resolve({ data: oldAck });
            });

            expect(useProjectStore.getState().currentProject?.revision).toBe(7);
            expect(useProjectStore.getState().projects.find(item => item.id === 'a')?.revision).toBe(7);
            expect(useProjectStore.getState().currentProject?.originalText).toBe('current connection text');
            expect(shots()).toEqual(['f1', 'f2']);
            expect(useProjectStore.getState().runningOps[operation]).toBe(true);
            expect(Object.keys(useProjectStore.getState().runningOps).filter(key => key.startsWith(errorPrefix))).toEqual([]);
            expect(firstButton('copy')).toBeDisabled();
            expect(screen.queryByRole('alert')).not.toBeInTheDocument();
            expect(axios.get).not.toHaveBeenCalled();
            fireEvent.click(firstButton('copy'));
            expect(axios.post).toHaveBeenCalledTimes(2);
            for (const [url, body] of vi.mocked(axios.post).mock.calls) {
                expect(url).toBe(`${API_URL}/projects/a/frames/copy`);
                expect(body).toEqual({ frame_id: 'f1', insert_at: 1 });
            }
        } finally {
            await act(async () => {
                oldRequest.resolve({ data: oldAck });
                newRequest.resolve({ data: newAck });
            });
        }
        await waitFor(() => expect(useProjectStore.getState().runningOps[operation]).toBeUndefined());
        expect(useProjectStore.getState().currentProject?.revision).toBe(8);
        expect(shots()).toEqual(['f1', 'new-copy', 'f2']);
        expect(firstButton('copy')).toBeEnabled();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('blocks editing during a structural command and does not erase newly entered text silently', async () => {
        const request = deferred(); vi.mocked(axios.post).mockReturnValueOnce(request.promise);
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('copy'));
        const editor = screen.getAllByRole('textbox', { name: 'prompt' })[0];
        expect(editor).toBeDisabled();
        fireEvent.change(editor, { target: { value: 'cannot enter while saving' } });
        expect(editor).toHaveValue('prompt f1');
        await act(async () => request.resolve({ data: ack() }));
    });

    it('blocks structural operations while a video task is in flight', async () => {
        useProjectStore.setState({ currentProject: { ...project(), video_tasks: [{ id: 'v1', frame_id: 'f1', status: 'pending' }] } });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('delete'));
        expect(await screen.findByRole('alert')).toHaveTextContent(/生成/);
        expect(shots()).toEqual(['f1', 'f2']);
        expect(axios.delete).not.toHaveBeenCalled();
    });

    it('does not mistake an unsupported field clear for a saved edit or erase it during copy', async () => {
        vi.useFakeTimers();
        const original = { ...frame('f1'), shot_size: 'medium_shot' };
        useProjectStore.setState({ currentProject: { ...project(), frames: [original, frame('f2')] } });
        vi.mocked(axios.post).mockResolvedValue({ data: ack([original, frame('f2')]) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('clear field'));
        await act(async () => vi.advanceTimersByTime(3100));
        fireEvent.click(firstButton('copy'));
        expect(screen.getByRole('alert')).toHaveTextContent(/编辑保存失败/);
        expect(JSON.parse(screen.getAllByTestId('shot-data')[0].textContent!).shotSize).toBeNull();
        expect(axios.post).not.toHaveBeenCalled();
    });
});

describe('reviewed edit acknowledgement boundaries', () => {
    it('keeps structure blocked when a workbench read is rejected by a shared-source floor', async () => {
        vi.useFakeTimers();
        useProjectStore.getState().applyProjectReadSnapshot('a', enriched(ack([frame('f1'), frame('f2')], 'a', 3), 8));
        vi.mocked(axios.patch).mockResolvedValueOnce({ data: { ...frame('f1'), workbench_tab_mode: 't2i_i2v' } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack([frame('f1'), frame('f2')]), 7) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('edit workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        expect(screen.getByRole('alert')).toHaveTextContent(/已保存.*读取失败/);
        fireEvent.click(firstButton('copy'));
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.patch).toHaveBeenCalledOnce(); expect(axios.get).toHaveBeenCalledOnce();
    });

    beforeEach(() => {
        vi.resetAllMocks(); localStorage.clear();
        activateStorage(STORAGE_A);
        useProjectStore.setState({ projects: [project()], currentProject: project(), runningOps: {}, renderingFrames: new Set() });
    });
    afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

    it('rejects a workbench read that has the same frame ID but predates its saved settings', async () => {
        vi.useFakeTimers();
        vi.mocked(axios.patch).mockResolvedValueOnce({ data: { ...frame('f1'), updated_at: 100, workbench_tab_mode: 't2i_i2v' } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack([{ ...frame('f1'), updated_at: 99 }, frame('f2')])) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('edit workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        expect(screen.getByRole('alert')).toHaveTextContent(/已保存.*读取失败/);
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
    });

    it('does not accept a workbench ACK with missing saved version evidence', async () => {
        vi.useFakeTimers();
        vi.mocked(axios.patch).mockResolvedValueOnce({ data: { id: 'f1' } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack()) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('edit workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        expect(screen.getByRole('alert')).toBeInTheDocument();
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('does not apply a workbench GET after its component unmounts', async () => {
        vi.useFakeTimers(); const read = deferred();
        const saved = { ...frame('f1'), updated_at: 100, workbench_tab_mode: 't2i_i2v' };
        vi.mocked(axios.patch).mockResolvedValueOnce({ data: saved });
        vi.mocked(axios.get).mockReturnValueOnce(read.promise);
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('edit workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        view.unmount();
        await act(async () => read.resolve({ data: enriched(ack([saved, frame('f2')])) }));
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
        renderWithIntl(<StoryboardR2V />);
        expect(screen.getByRole('alert')).toHaveTextContent(/已保存.*读取失败/);
    });

    it('reads an enriched project after the real workbench Frame ACK and retains shared sources', async () => {
        vi.useFakeTimers();
        const saved = { ...frame('f1'), workbench_tab_mode: 't2i_i2v' };
        vi.mocked(axios.patch).mockResolvedValueOnce({ data: saved });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched({ ...ack([saved, frame('f2')]), characters: project().characters }) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('edit workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        expect(axios.patch).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/workbench`, { workbench_tab_mode: 't2i_i2v' });
        expect(axios.get).toHaveBeenCalledWith(`${API_URL}/projects/a`);
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(useProjectStore.getState().currentProject?.revision).toBe(4);
        expect(useProjectStore.getState().currentProject?.characters[0].source).toBe('series');
    });

    it('labels a failed read after a successful PATCH, never retries the PATCH, and recovers after reopening', async () => {
        vi.useFakeTimers();
        const saved = { ...frame('f1'), workbench_tab_mode: 't2i_i2v' };
        vi.mocked(axios.patch).mockResolvedValue({ data: saved });
        vi.mocked(axios.get).mockRejectedValueOnce(new Error('read unavailable'));
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('edit workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        expect(screen.getByRole('alert')).toHaveTextContent(/已保存.*读取失败/);
        fireEvent.click(firstButton('copy'));
        fireEvent.click(firstButton('edit workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.patch).toHaveBeenCalledTimes(1);
        view.unmount(); renderWithIntl(<StoryboardR2V />);
        expect(screen.getByRole('alert')).toHaveTextContent(/已保存.*读取失败/);
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => enriched(ack([saved, frame('f2')])) }));
        await act(async () => useProjectStore.getState().selectProject('a'));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        vi.mocked(axios.post).mockResolvedValueOnce({ data: ack(undefined, 'a', 5) });
        await act(async () => fireEvent.click(firstButton('copy')));
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('does not regress a newer committed snapshot with a late workbench read', async () => {
        vi.useFakeTimers(); const read = deferred();
        useProjectStore.getState().applyProjectReadSnapshot('a', enriched(ack([frame('f1'), frame('f2')], 'a', 3)));
        vi.mocked(axios.patch).mockResolvedValueOnce({ data: { ...frame('f1'), workbench_tab_mode: 't2i_i2v' } });
        vi.mocked(axios.get).mockReturnValueOnce(read.promise);
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('edit workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        act(() => useProjectStore.getState().applyFrameCommandSnapshot('a', ack([{ ...frame('f1'), visual_description: 'newer', workbench_tab_mode: 't2i_i2v' }, frame('f2')], 'a', 6)));
        await act(async () => read.resolve({ data: enriched(ack([{ ...frame('f1'), workbench_tab_mode: 't2i_i2v' }, frame('f2')], 'a', 4)) }));
        expect(useProjectStore.getState().currentProject?.revision).toBe(6);
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('newer');
    });

    it('serializes and coalesces prompt writes without erasing a newer unacknowledged draft', async () => {
        vi.useFakeTimers(); const first = deferred(); const last = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(first.promise).mockReturnValueOnce(last.promise);
        renderWithIntl(<StoryboardR2V />);
        const editor = screen.getAllByRole('textbox', { name: 'prompt' })[0];
        fireEvent.change(editor, { target: { value: 'first' } });
        await act(async () => vi.advanceTimersByTime(800));
        fireEvent.change(editor, { target: { value: 'middle' } });
        await act(async () => vi.advanceTimersByTime(800));
        fireEvent.change(editor, { target: { value: 'last' } });
        await act(async () => vi.advanceTimersByTime(800));
        expect(axios.post).toHaveBeenCalledTimes(1);
        await act(async () => first.resolve({ data: ack([{ ...frame('f1'), visual_description: 'first' }, frame('f2')]) }));
        expect(axios.post).toHaveBeenCalledTimes(2);
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/update`, { frame_id: 'f1', expected_revision: 4, visual_description: 'last' });
        expect(editor).toHaveValue('last');
        await act(async () => last.resolve({ data: ack([{ ...frame('f1'), visual_description: 'last' }, frame('f2')], 'a', 5) }));
        expect(editor).toHaveValue('last');
    });

    it('freezes dialogue revision when typing, before blur and a later project read', async () => {
        const initial = { ...project(), frames: [{ ...frame('f1'), dialogue: 'old line' }] };
        useProjectStore.setState({ currentProject: initial, projects: [initial] });
        vi.mocked(axios.get).mockRejectedValue(new Error('audio state unavailable'));
        vi.mocked(axios.post).mockRejectedValue({ response: { status: 409 } });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
        const text = screen.getByPlaceholderText(/输入该镜头/);
        fireEvent.change(text, { target: { value: 'my dialogue' } });
        act(() => useProjectStore.setState({ currentProject: { ...initial, revision: 7 } }));
        fireEvent.blur(text);
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ expected_revision: 3, dialogue: 'my dialogue' });
        expect(text).toHaveValue('my dialogue');
        await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
    });

    it('guards leaving with an unblurred dialogue draft without submitting it', () => {
        const initial = { ...project(), frames: [{ ...frame('f1'), dialogue: 'old line' }] };
        useProjectStore.setState({ currentProject: initial, projects: [initial] });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
        fireEvent.change(screen.getByPlaceholderText(/输入该镜头/), { target: { value: 'unblurred dialogue' } });
        const leaving = new Event('beforeunload', { cancelable: true });
        window.dispatchEvent(leaving);
        expect(leaving.defaultPrevented).toBe(true);
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('keeps a rejected dialogue draft when its workbench closes and reopens', async () => {
        const initial = { ...project(), frames: [{ ...frame('f1'), dialogue: 'old line' }] };
        useProjectStore.setState({ currentProject: initial, projects: [initial] });
        vi.mocked(axios.post).mockRejectedValue({ response: { status: 409 } });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
        const text = screen.getByPlaceholderText(/输入该镜头/);
        fireEvent.change(text, { target: { value: 'retained dialogue' } });
        fireEvent.blur(text);
        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/版本冲突/));
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
        expect(screen.getByPlaceholderText(/输入该镜头/)).toHaveValue('retained dialogue');
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('freezes the first draft revision even when a newer read arrives before debounce', async () => {
        vi.useFakeTimers(); vi.mocked(axios.post).mockRejectedValueOnce({ response: { status: 409 } });
        renderWithIntl(<StoryboardR2V />);
        const editor = screen.getAllByRole('textbox', { name: 'prompt' })[0];
        fireEvent.change(editor, { target: { value: 'old-window draft' } });
        act(() => useProjectStore.getState().applyFrameCommandSnapshot('a', ack([
            { ...frame('f1'), visual_description: 'other-window saved' }, frame('f2'),
        ], 'a', 7)));
        await act(async () => vi.advanceTimersByTime(800));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/update`, {
            frame_id: 'f1', expected_revision: 3, visual_description: 'old-window draft',
        });
        expect(editor).toHaveValue('old-window draft');
    });

    it('serializes different shot edits and advances only its own acknowledged revision', async () => {
        vi.useFakeTimers(); const first = deferred(); const second = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
        renderWithIntl(<StoryboardR2V />);
        const editors = screen.getAllByRole('textbox', { name: 'prompt' });
        try {
        fireEvent.change(editors[0], { target: { value: 'first shot draft' } });
        fireEvent.change(editors[1], { target: { value: 'second shot draft' } });
        await act(async () => vi.advanceTimersByTime(800));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/update`, {
            frame_id: 'f1', expected_revision: 3, visual_description: 'first shot draft',
        });
        await act(async () => first.resolve({ data: ack([
            { ...frame('f1'), visual_description: 'first shot draft' }, frame('f2'),
        ], 'a', 4) }));
        expect(axios.post).toHaveBeenCalledTimes(2);
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/update`, {
            frame_id: 'f2', expected_revision: 4, visual_description: 'second shot draft',
        });
        await act(async () => second.resolve({ data: ack([
            { ...frame('f1'), visual_description: 'first shot draft' },
            { ...frame('f2'), visual_description: 'second shot draft' },
        ], 'a', 5) }));
        } finally {
            await act(async () => {
                first.resolve({ data: ack([frame('f1'), frame('f2')], 'a', 4) });
                second.resolve({ data: ack([frame('f1'), frame('f2')], 'a', 5) });
            });
        }
    });

    it('retains a conflicting draft through explicit readback and saves only after a separate action', async () => {
        vi.useFakeTimers(); const first = deferred(); const retry = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(first.promise).mockReturnValueOnce(retry.promise);
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack([
            { ...frame('f1'), visual_description: 'other-window saved' }, frame('f2'),
        ], 'a', 5)) });
        renderWithIntl(<StoryboardR2V />);
        const editor = screen.getAllByRole('textbox', { name: 'prompt' })[0];
        fireEvent.change(editor, { target: { value: 'retained local draft' } });
        await act(async () => vi.advanceTimersByTime(800));
        await act(async () => first.reject({ response: { status: 409 } }));
        expect(editor).toHaveValue('retained local draft');
        expect(axios.post).toHaveBeenCalledTimes(1);
        await act(async () => fireEvent.click(screen.getByRole('button', { name: '读取最新状态' })));
        expect(axios.get).toHaveBeenCalledTimes(1);
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(editor).toHaveValue('retained local draft');
        expect(useProjectStore.getState().currentProject?.frames[0].visual_description).toBe('other-window saved');
        fireEvent.click(screen.getByRole('button', { name: '保存保留的草稿' }));
        await act(async () => {});
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/update`, {
            frame_id: 'f1', expected_revision: 5, visual_description: 'retained local draft',
        });
        await act(async () => retry.resolve({ data: ack([
            { ...frame('f1'), visual_description: 'retained local draft' }, frame('f2'),
        ], 'a', 6) }));
        expect(screen.queryByRole('button', { name: '保存保留的草稿' })).not.toBeInTheDocument();
        expect(editor).toHaveValue('retained local draft');
    });

    it.each(['offline', 'older', 'removed'])('retains conflicts after a %s read without retrying on navigation', async mode => {
        vi.useFakeTimers();
        vi.mocked(axios.post).mockRejectedValueOnce({ response: { status: 409 } });
        const view = renderWithIntl(<StoryboardR2V />);
        const editor = screen.getAllByRole('textbox', { name: 'prompt' })[0];
        fireEvent.change(editor, { target: { value: 'keep this draft' } });
        await act(async () => vi.advanceTimersByTime(800));
        if (mode === 'offline') vi.mocked(axios.get).mockRejectedValueOnce(new Error('offline'));
        else vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack(mode === 'removed' ? [frame('f2')] : [frame('f1'), frame('f2')], 'a', mode === 'older' ? 2 : 5), 1) });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: '读取最新状态' })));
        expect(screen.getByText(/读取失败，草稿仍保留/)).toBeInTheDocument();
        expect(editor).toHaveValue('keep this draft');
        expect(screen.queryByRole('button', { name: '保存保留的草稿' })).not.toBeInTheDocument();
        expect(axios.post).toHaveBeenCalledTimes(1);
        view.unmount();
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('uses the explicitly read conflict version even if a newer external snapshot arrives before save', async () => {
        vi.useFakeTimers();
        vi.mocked(axios.post).mockRejectedValue({ response: { status: 409 } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack([frame('f1'), frame('f2')], 'a', 5), 1) });
        renderWithIntl(<StoryboardR2V />);
        const editor = screen.getAllByRole('textbox', { name: 'prompt' })[0];
        fireEvent.change(editor, { target: { value: 'conflicting again' } });
        await act(async () => vi.advanceTimersByTime(800));
        await act(async () => fireEvent.click(screen.getByRole('button', { name: '读取最新状态' })));
        act(() => useProjectStore.setState({ currentProject: project('a', 9) }));
        await act(async () => fireEvent.click(screen.getByRole('button', { name: '保存保留的草稿' })));
        expect(axios.post).toHaveBeenCalledTimes(2);
        expect(vi.mocked(axios.post).mock.calls[1][1]).toMatchObject({ expected_revision: 5, visual_description: 'conflicting again' });
        expect(editor).toHaveValue('conflicting again');
        expect(useProjectStore.getState().currentProject?.revision).toBe(9);
        expect(screen.queryByRole('button', { name: '保存保留的草稿' })).not.toBeInTheDocument();
        await act(async () => vi.advanceTimersByTime(4000));
        expect(axios.post).toHaveBeenCalledTimes(2);
    });

    it('ignores a late conflict read after switching environment with the same project id', async () => {
        vi.useFakeTimers();
        activateStorage(STORAGE_A);
        useProjectStore.setState({ currentProject: project(), projects: [project()] });
        const read = deferred();
        vi.mocked(axios.post).mockRejectedValueOnce({ response: { status: 409 } });
        vi.mocked(axios.get).mockReturnValueOnce(read.promise);
        renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'A draft' } });
        await act(async () => vi.advanceTimersByTime(800));
        fireEvent.click(screen.getByRole('button', { name: '读取最新状态' }));
        act(() => {
            activateStorage(STORAGE_B);
            useProjectStore.setState({ currentProject: project('a', 7), projects: [project('a', 7)], runningOps: {} });
        });
        await act(async () => read.resolve({ data: enriched(ack([{ ...frame('f1'), visual_description: 'old A read' }, frame('f2')], 'a', 10), 2) }));
        expect(useProjectStore.getState().currentProject?.revision).toBe(7);
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('prompt f1');
        expect(screen.queryByRole('button', { name: '保存保留的草稿' })).not.toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('does not mask an unsupported field clear as saved during conflict recovery', async () => {
        vi.useFakeTimers();
        vi.mocked(axios.post).mockRejectedValueOnce({ response: { status: 409 } });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'retained' } });
        await act(async () => vi.advanceTimersByTime(800));
        fireEvent.click(firstButton('clear field'));
        fireEvent.click(firstButton('copy'));
        expect(screen.queryByRole('button', { name: '读取最新状态' })).not.toBeInTheDocument();
        expect(screen.getAllByTestId('shot-data')[0]).toHaveTextContent('"shotSize":null');
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('does not advance other pending drafts from an impossible CAS acknowledgement', async () => {
        vi.useFakeTimers();
        const first = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(first.promise);
        renderWithIntl(<StoryboardR2V />);
        const editors = screen.getAllByRole('textbox', { name: 'prompt' });
        fireEvent.change(editors[0], { target: { value: 'first' } });
        fireEvent.change(editors[1], { target: { value: 'second' } });
        await act(async () => vi.advanceTimersByTime(800));
        await act(async () => first.resolve({ data: ack([frame('f1'), frame('f2')], 'a', 50) }));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
        expect(editors[0]).toHaveValue('first'); expect(editors[1]).toHaveValue('second');
        expect(screen.queryByRole('button', { name: '读取最新状态' })).not.toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent(/保存失败/);
    });

    it.each([409, 503])('stops queued prompt writes after %s and retains the last input', async status => {
        vi.useFakeTimers(); const first = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(first.promise);
        renderWithIntl(<StoryboardR2V />);
        const editor = screen.getAllByRole('textbox', { name: 'prompt' })[0];
        fireEvent.change(editor, { target: { value: 'first' } });
        await act(async () => vi.advanceTimersByTime(800));
        fireEvent.change(editor, { target: { value: 'last' } });
        await act(async () => vi.advanceTimersByTime(800));
        await act(async () => first.reject({ response: { status } }));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(editor).toHaveValue('last');
        expect(screen.getByRole('alert')).toHaveTextContent(status === 409 ? /版本冲突.*草稿已保留/ : /编辑保存失败/);
    });

    it('delivers a structural 503 to a remounted same-project view', async () => {
        const save = deferred(); vi.mocked(axios.post).mockReturnValueOnce(save.promise);
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('copy')); view.unmount(); renderWithIntl(<StoryboardR2V />);
        await act(async () => save.reject({ response: { status: 503 } }));
        expect(screen.getByRole('alert')).toHaveTextContent(/保存|服务/);
        expect(firstButton('copy')).toBeEnabled();
    });

    it('keeps an explicitly empty visual prompt through ACK, copy and remount', async () => {
        vi.useFakeTimers();
        const saved = { ...frame('f1'), visual_description: '', action_description: 'legacy summary' };
        useProjectStore.setState({ currentProject: { ...project(), frames: [{ ...saved, visual_description: 'before' }] } });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: ack([saved]) });
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getByRole('textbox', { name: 'prompt' }), { target: { value: '' } });
        await act(async () => vi.advanceTimersByTime(800));
        expect(screen.getByRole('textbox', { name: 'prompt' })).toHaveValue('');
        vi.mocked(axios.post).mockResolvedValueOnce({ data: ack([saved, { ...saved, id: 'copy' }], 'a', 5) });
        await act(async () => fireEvent.click(firstButton('copy')));
        view.unmount(); renderWithIntl(<StoryboardR2V />);
        for (const editor of screen.getAllByRole('textbox', { name: 'prompt' })) expect(editor).toHaveValue('');
    });
});

describe('edit lane lifecycle and coalescing', () => {
    beforeEach(() => {
        vi.resetAllMocks(); localStorage.clear();
        useProjectStore.setState({ projects: [project()], currentProject: project(), runningOps: {}, renderingFrames: new Set() });
    });
    afterEach(() => vi.useRealTimers());

    it('serializes a new mount prompt behind the old mount in-flight prompt', async () => {
        vi.useFakeTimers(); const first = deferred(); const last = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(first.promise).mockReturnValueOnce(last.promise);
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'old mount' } });
        await act(async () => vi.advanceTimersByTime(800));
        view.unmount(); renderWithIntl(<StoryboardR2V />);
        const editor = screen.getAllByRole('textbox', { name: 'prompt' })[0];
        fireEvent.change(editor, { target: { value: 'new mount' } });
        await act(async () => vi.advanceTimersByTime(800));
        expect(axios.post).toHaveBeenCalledTimes(1);
        await act(async () => first.resolve({ data: ack([{ ...frame('f1'), visual_description: 'old mount' }, frame('f2')]) }));
        expect(editor).toHaveValue('new mount');
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/update`, { frame_id: 'f1', expected_revision: 4, visual_description: 'new mount' });
        await act(async () => last.resolve({ data: ack([{ ...frame('f1'), visual_description: 'new mount' }, frame('f2')], 'a', 5) }));
        expect(editor).toHaveValue('new mount');
    });

    it('keeps unacknowledged keystrokes when the first ACK arrives before their debounce timer', async () => {
        vi.useFakeTimers(); const first = deferred(); const last = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(first.promise).mockReturnValueOnce(last.promise);
        renderWithIntl(<StoryboardR2V />);
        const editor = screen.getAllByRole('textbox', { name: 'prompt' })[0];
        fireEvent.change(editor, { target: { value: 'first' } });
        await act(async () => vi.advanceTimersByTime(800));
        fireEvent.change(editor, { target: { value: 'not yet due' } });
        await act(async () => first.resolve({ data: ack([{ ...frame('f1'), visual_description: 'first' }, frame('f2')]) }));
        expect(editor).toHaveValue('not yet due');
        fireEvent.click(firstButton('copy'));
        expect(axios.post).toHaveBeenCalledTimes(1);
        await act(async () => vi.advanceTimersByTime(800));
        await act(async () => last.resolve({ data: ack([{ ...frame('f1'), visual_description: 'not yet due' }, frame('f2')], 'a', 5) }));
        expect(editor).toHaveValue('not yet due');
    });

    it('serializes workbench PATCHes and merges queued settings before the next PATCH', async () => {
        vi.useFakeTimers(); const first = deferred();
        vi.mocked(axios.patch).mockReturnValueOnce(first.promise).mockResolvedValueOnce({ data: { ...frame('f1'), workbench_generate_count: 3 } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack([{ ...frame('f1'), workbench_tab_mode: 't2i_i2v' }, frame('f2')])) })
            .mockResolvedValueOnce({ data: enriched(ack([{ ...frame('f1'), workbench_generate_count: 3 }, frame('f2')], 'a', 5)) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('edit workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        fireEvent.click(firstButton('reset workbench'));
        await act(async () => vi.advanceTimersByTime(1000));
        fireEvent.click(firstButton('edit count'));
        await act(async () => vi.advanceTimersByTime(1000));
        expect(axios.patch).toHaveBeenCalledTimes(1);
        await act(async () => first.resolve({ data: { ...frame('f1'), workbench_tab_mode: 't2i_i2v' } }));
        expect(axios.patch).toHaveBeenCalledTimes(2);
        expect(axios.patch).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/f1/workbench`, { workbench_tab_mode: 'direct_r2v', workbench_generate_count: 3 });
        expect(axios.get).toHaveBeenCalledTimes(2);
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('serializes and merges ready structured field patches', async () => {
        vi.useFakeTimers(); const first = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(first.promise).mockResolvedValueOnce({ data: ack([frame('f1'), frame('f2')], 'a', 5) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(firstButton('edit field'));
        await act(async () => vi.advanceTimersByTime(3000));
        fireEvent.click(firstButton('edit angle'));
        await act(async () => vi.advanceTimersByTime(3000));
        fireEvent.click(firstButton('edit duration'));
        await act(async () => vi.advanceTimersByTime(3000));
        expect(axios.post).toHaveBeenCalledTimes(1);
        await act(async () => first.resolve({ data: ack([frame('f1'), frame('f2')]) }));
        expect(axios.post).toHaveBeenCalledTimes(2);
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/update`, { frame_id: 'f1', expected_revision: 4, camera_angle: 'high_angle', duration: 6 });
    });

    it('keeps the old project failure out of B but exposes it when returning to A', async () => {
        const request = deferred(); vi.mocked(axios.post).mockReturnValueOnce(request.promise);
        renderWithIntl(<StoryboardR2V />); fireEvent.click(firstButton('copy'));
        act(() => useProjectStore.setState({ currentProject: project('b') }));
        await act(async () => request.reject({ response: { status: 503 } }));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        act(() => useProjectStore.setState({ currentProject: project('a') }));
        expect(screen.getByRole('alert')).toHaveTextContent(/保存|服务/);
    });

    it.each([
        { name: 'prompt', act: () => fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'old prompt' } }) },
        { name: 'workbench', act: () => fireEvent.click(firstButton('edit workbench')) },
        { name: 'field', act: () => fireEvent.click(firstButton('edit field')) },
    ])('does not dispatch a scheduled $name edit after switching from environment A to B', async ({ act: edit }) => {
        vi.useFakeTimers();
        renderWithIntl(<StoryboardR2V />);
        edit();
        activateStorage(STORAGE_B);
        useProjectStore.setState({ projects: [project()], currentProject: project(), runningOps: {}, renderingFrames: new Set() });

        await act(async () => vi.advanceTimersByTime(3100));

        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.patch).not.toHaveBeenCalled();
    });

    it('does not dispatch an old A prompt when the app switches A to B and back to A with a new generation', async () => {
        vi.useFakeTimers();
        renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'stale A' } });
        activateStorage(STORAGE_B);
        activateStorage(STORAGE_A);
        useProjectStore.setState({ projects: [project()], currentProject: project(), runningOps: {}, renderingFrames: new Set() });

        await act(async () => vi.advanceTimersByTime(900));

        expect(axios.post).not.toHaveBeenCalled();
    });

    it('starts a fresh draft from the new environment when its project id is identical', async () => {
        vi.useFakeTimers();
        activateStorage(STORAGE_A);
        useProjectStore.setState({ currentProject: project(), projects: [project()] });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'unsent A' } });
        act(() => {
            activateStorage(STORAGE_B);
            useProjectStore.setState({ currentProject: project('a', 7), projects: [project('a', 7)], runningOps: {} });
        });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: ack([{ ...frame('f1'), visual_description: 'new B' }, frame('f2')], 'a', 8) });
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'new B' } });
        await act(async () => vi.advanceTimersByTime(900));
        expect(axios.post).toHaveBeenCalledExactlyOnceWith(`${API_URL}/projects/a/frames/update`, {
            frame_id: 'f1', visual_description: 'new B', expected_revision: 7,
        });
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('does not poison a reconnected environment with the old connection edit result', async () => {
        vi.useFakeTimers();
        activateStorage(STORAGE_A);
        useProjectStore.setState({ currentProject: project(), projects: [project()] });
        const old = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(old.promise);
        renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'old connection' } });
        await act(async () => vi.advanceTimersByTime(800));
        act(() => {
            activateStorage(STORAGE_B); activateStorage(STORAGE_A);
            useProjectStore.setState({ currentProject: project('a', 7), projects: [project('a', 7)], runningOps: {} });
        });
        await act(async () => old.resolve({ data: ack([{ ...frame('f1'), visual_description: 'old connection' }, frame('f2')], 'a', 4) }));
        expect(useProjectStore.getState().currentProject?.revision).toBe(7);
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        vi.mocked(axios.post).mockResolvedValueOnce({ data: ack([{ ...frame('f1'), visual_description: 'new connection' }, frame('f2')], 'a', 8) });
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'new connection' } });
        await act(async () => vi.advanceTimersByTime(800));
        expect(axios.post).toHaveBeenCalledTimes(2);
        expect(vi.mocked(axios.post).mock.calls[1][1]).toMatchObject({ expected_revision: 7 });
    });

    it('removes the old editor when the runtime disconnects without flushing its draft', async () => {
        vi.useFakeTimers();
        renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'unsent at disconnect' } });
        expect(() => act(() => clearRuntimeEnvironment())).not.toThrow();
        expect(screen.queryAllByRole('textbox', { name: 'prompt' })).toHaveLength(0);
        await act(async () => vi.advanceTimersByTime(900));
        expect(axios.post).not.toHaveBeenCalled();
        act(() => activateStorage(STORAGE_A));
    });

    it('does not flush an old prompt from unmount cleanup after switching environment', () => {
        vi.useFakeTimers();
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'cleanup stale' } });
        activateStorage(STORAGE_B);

        view.unmount();

        expect(axios.post).not.toHaveBeenCalled();
    });
});
