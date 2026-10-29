import type { ComponentProps } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardR2V from '../StoryboardR2V';
import type ShotCard from '../storyboard-r2v/ShotCard';
import { ack, deferred, enriched, frame } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
vi.mock('../storyboard-r2v/ShotCard', () => ({
    T2I_HISTORY_LIMIT: 10,
    default: (p: ComponentProps<typeof ShotCard>) => <section>
        <output data-testid="shot">{JSON.stringify(p.shot)}</output>
        <button onClick={p.onGenerateT2I}>render image</button>
    </section>,
}));
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/TaskQueuePanel', () => ({ default: () => null }));
const frames = [{ ...frame('f1'), workbench_tab_mode: 't2i_i2v' }];
const result = () => ack([{ ...frames[0], rendered_image_url: 'storyboard/new.png', image_url: 'storyboard/new.png' }], 'a', 4);
const shot = () => JSON.parse(screen.getByTestId('shot').textContent || '{}');
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear();
    useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
    useProjectStore.getState().applyProjectReadSnapshot('a', enriched(ack(frames, 'a', 3), 8), true);
});
afterEach(() => vi.useRealTimers());

describe('R2V synchronous image render contract', () => {
    it('reads a committed Script response without polling its project ID as a task', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: result() });
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(result(), 8) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByText('render image'));
        await waitFor(() => expect(shot().t2iStatus).toBe('completed'));
        expect(shot()).toMatchObject({ imageUrl: 'storyboard/new.png', t2iImageUrl: 'storyboard/new.png' });
        expect(shot().t2iTaskId).toBeUndefined();
        expect(axios.get).toHaveBeenCalledExactlyOnceWith(`${API_URL}/projects/a`);
        expect(axios.patch).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.revision).toBe(4);
    });

    it('does not resubmit a successful render when its readback is stale', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: result() });
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(result(), 7) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByText('render image'));
        expect(await screen.findByRole('alert')).toHaveTextContent(/读取|保存|未确认/);
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
        expect(shot().imageUrl).toBeUndefined();
        fireEvent.click(screen.getByText('render image'));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.get).toHaveBeenCalledTimes(1);
    });

    it('blocks duplicate renders across a remount while the first is unresolved', async () => {
        const pending = deferred<{ data: unknown }>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        const view = renderWithIntl(<StoryboardR2V />);
        act(() => { fireEvent.click(screen.getByText('render image')); fireEvent.click(screen.getByText('render image')); });
        view.unmount(); renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByText('render image'));
        expect(axios.post).toHaveBeenCalledTimes(1);
        await act(async () => pending.resolve({ data: result() }));
        expect(axios.get).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
        fireEvent.click(screen.getByText('render image'));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(screen.getByRole('alert')).toHaveTextContent(/已保存.*读取失败/);
    });

    it('rejects rendering from cached data belonging to another environment', async () => {
        useProjectStore.setState(state => ({ currentProject: { ...state.currentProject!, readEnvironment: 'https://another.invalid' } }));
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByText('render image'));
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('keeps uncertain image submissions blocked across remounts', async () => {
        vi.mocked(axios.post).mockRejectedValueOnce(new Error('connection lost after send'));
        const view = renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByText('render image'));
        await screen.findByRole('alert');
        view.unmount(); renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByText('render image'));
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('keeps a different project untouched by a late render', async () => {
        const pending = deferred<{ data: unknown }>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getByText('render image'));
        act(() => useProjectStore.getState().applyProjectReadSnapshot('b', enriched(ack(frames, 'b', 5), 8), true));
        await act(async () => pending.resolve({ data: result() }));
        expect(axios.get).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(shot().imageUrl).toBeUndefined();
    });
});
