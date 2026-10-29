import type { ComponentProps } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardR2V from '../StoryboardR2V';
import type ShotCard from '../storyboard-r2v/ShotCard';
import type CandidatesSection from '../storyboard-r2v/shot-panel/CandidatesSection';
import { ack, deferred, enriched, frame } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
vi.mock('../storyboard-r2v/ShotCard', () => ({
    T2I_HISTORY_LIMIT: 10,
    default: (p: ComponentProps<typeof ShotCard>) => <section>
        <output data-testid="shot">{JSON.stringify(p.shot)}</output>
        <button onClick={p.onToggleExpanded}>expand</button>
        <button onClick={p.onUnpinVideo}>unpin</button>
        <button onClick={p.onCancelVideo}>cancel</button>
    </section>,
}));
vi.mock('../storyboard-r2v/shot-panel/CandidatesSection', () => ({
    default: (p: ComponentProps<typeof CandidatesSection>) => <>
        <button onClick={() => p.onSetActive?.(p.tasks[1])}>pin new</button>
        <button onClick={() => p.onToggleStar?.(p.tasks[0], true)}>star</button>
        <button onClick={() => p.onSetLabel?.(p.tasks[0], 'take note')}>label</button>
    </>,
}));
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/TaskQueuePanel', () => ({ default: () => null }));
const tasks = ['old', 'new'].map((id, index) => ({ id, frame_id: 'f1', status: 'completed', video_url: `video/${id}.mp4`, image_url: '', prompt: '', created_at: index + 1, generation_mode: 'r2v' }));
const frames = [{ ...frame('f1'), selected_video_id: 'old', video_url: 'video/old.mp4', is_video_pinned: true }];
const raw = (selected = 'old', pinned = true, revision = 3) => ({ ...ack([{ ...frames[0], selected_video_id: selected, video_url: `video/${selected}.mp4`, is_video_pinned: pinned }], 'a', revision), video_tasks: tasks });
const shot = () => JSON.parse(screen.getByTestId('shot').textContent || '{}');
function renderCandidates() { const view = renderWithIntl(<StoryboardR2V />); fireEvent.click(screen.getByText('expand')); return view; }
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear();
    useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
    useProjectStore.getState().applyProjectReadSnapshot('a', enriched(raw(), 8), true);
});

describe('R2V candidate committed selection', () => {
    it('keeps the previous hero while a failed pin is pending', async () => {
        const pending = deferred<{ data: unknown }>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        renderCandidates();
        fireEvent.click(screen.getByText('pin new'));
        expect(shot().videoUrl).toBe('video/old.mp4');
        await act(async () => pending.reject(new Error('offline')));
        expect(await screen.findByRole('alert')).toBeInTheDocument();
        expect(shot().videoUrl).toBe('video/old.mp4');
    });
    it('uses the confirmed server selection and rejects duplicate in-flight pins', async () => {
        const pending = deferred<{ data: unknown }>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(raw('old', true, 5), 8) });
        renderCandidates();
        fireEvent.click(screen.getByText('pin new')); fireEvent.click(screen.getByText('pin new'));
        expect(axios.post).toHaveBeenCalledTimes(1);
        await act(async () => pending.resolve({ data: raw('new', true, 4) }));
        await waitFor(() => expect(axios.get).toHaveBeenCalledWith(`${API_URL}/projects/a`));
        expect(shot().videoUrl).toBe('video/old.mp4');
        expect(useProjectStore.getState().currentProject?.revision).toBe(5);
    });
    it('does not unpin when the committed read cannot be confirmed', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: raw('old', false, 4) });
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(raw('old', false, 4), 7) });
        renderCandidates(); fireEvent.click(screen.getByText('unpin'));
        await screen.findByRole('alert');
        expect(shot().isVideoPinned).toBe(true);
        fireEvent.click(screen.getByText('unpin'));
        expect(axios.post).toHaveBeenCalledTimes(1);
    });
    it('drops a late pin from an unmounted session before reading', async () => {
        const pending = deferred<{ data: unknown }>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        const view = renderCandidates(); fireEvent.click(screen.getByText('pin new'));
        view.unmount(); renderCandidates();
        await act(async () => pending.resolve({ data: raw('new', true, 4) }));
        expect(axios.get).not.toHaveBeenCalled();
        expect(shot().videoUrl).toBe('video/old.mp4');
    });
    it.each(['star', 'label', 'cancel'] as const)('accepts a %s read that confirms its task receipt', async (action) => {
        const task = { ...tasks[0], is_starred: true, label: 'take note', status: 'failed', error: 'Canceled by user' };
        vi.mocked(axios.patch).mockResolvedValue({ data: task });
        vi.mocked(axios.post).mockResolvedValue({ data: task });
        vi.mocked(axios.get).mockResolvedValue({ data: enriched({ ...raw('old', true, 4), video_tasks: [task, tasks[1]] }, 8) });
        renderCandidates(); fireEvent.click(screen.getByText(action));
        await waitFor(() => expect(useProjectStore.getState().currentProject?.revision).toBe(4));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
    it.each(['star', 'label'] as const)('rejects a %s read that does not confirm the returned task fields', async (action) => {
        const receipt = { ...tasks[0], is_starred: true, label: 'take note' };
        vi.mocked(axios.patch).mockResolvedValue({ data: receipt });
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(raw('old', true, 4), 8) });
        renderCandidates(); fireEvent.click(screen.getByText(action));
        await screen.findByRole('alert');
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
    });
    it('rejects cancellation readback that still reports a completed task', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: { ...tasks[0], status: 'failed', error: 'Canceled by user' } });
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(raw('old', true, 4), 8) });
        renderCandidates(); fireEvent.click(screen.getByText('cancel'));
        await screen.findByRole('alert');
        expect(shot().videoStatus).toBe('completed');
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
    });
    it('drops a task annotation completed after leaving and returning to the same project', async () => {
        const pending = deferred<{ data: unknown }>();
        vi.mocked(axios.patch).mockReturnValue(pending.promise);
        renderCandidates(); fireEvent.click(screen.getByText('star'));
        act(() => useProjectStore.getState().applyProjectReadSnapshot('b', enriched(ack([], 'b', 3), 8), true));
        act(() => useProjectStore.getState().applyProjectReadSnapshot('a', enriched(raw(), 8), true));
        await act(async () => pending.resolve({ data: { ...tasks[0], is_starred: true } }));
        expect(axios.get).not.toHaveBeenCalled();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
    it('does not invent a failed task when cancellation itself fails', async () => {
        vi.mocked(axios.post).mockRejectedValue(new Error('offline'));
        renderCandidates(); fireEvent.click(screen.getByText('cancel'));
        await screen.findByRole('alert');
        expect(shot().videoStatus).toBe('completed');
        expect(axios.get).not.toHaveBeenCalled();
    });
});
