import type { ComponentProps } from 'react';
import { act, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { useProjectStore, type Project } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardR2V from '../StoryboardR2V';
import type ShotCard from '../storyboard-r2v/ShotCard';
import { ack, deferred, enriched, frame, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
vi.mock('../storyboard-r2v/ShotCard', () => ({
    T2I_HISTORY_LIMIT: 10,
    default: (p: ComponentProps<typeof ShotCard>) => <section data-testid="shot" aria-label={p.shot.id}>
        <output data-testid={`shot-data-${p.shot.id}`}>{JSON.stringify(p.shot)}</output>
    </section>,
}));
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/TaskQueuePanel', () => ({ default: () => null }));

function task(id: string, frameId: string, status: 'pending' | 'processing' | 'completed' | 'needs_confirmation', videoUrl?: string) {
    return { id, project_id: 'a', frame_id: frameId, status, video_url: videoUrl, image_url: '', prompt: '', created_at: id === 't1' ? 1 : 2 };
}

function videoProject({ revision = 3, status = 'processing', pinned = false }: { revision?: number; status?: 'processing' | 'completed' | 'needs_confirmation'; pinned?: boolean } = {}): Project {
    const f1 = { ...frame('f1'), selected_video_id: pinned ? 'manual' : null, video_url: pinned ? '/manual.mp4' : '', is_video_pinned: pinned };
    const f2 = { ...frame('f2'), selected_video_id: null, video_url: '', is_video_pinned: false };
    const tasks = [
        ...(pinned ? [task('manual', 'f1', 'completed', '/manual.mp4')] : []),
        task('t1', 'f1', status, status === 'completed' ? '/new-f1.mp4' : undefined),
        task('t2', 'f2', status, status === 'completed' ? '/new-f2.mp4' : undefined),
    ];
    return { ...project('a', revision), frames: [f1, f2], video_tasks: tasks };
}

function rawScriptFrom(current: Project, revision: number) {
    return { ...ack(current.frames as Record<string, unknown>[], current.id, revision), characters: current.characters,
        scenes: current.scenes, props: current.props, video_tasks: current.video_tasks };
}

function enrichedScriptFrom(current: Project, library = 8) {
    return enriched(rawScriptFrom(current, current.revision), library);
}

function shotData(id: string) {
    return JSON.parse(screen.getByTestId(`shot-data-${id}`).textContent || '{}');
}

beforeEach(() => {
    vi.resetAllMocks(); vi.useFakeTimers(); localStorage.clear();
    useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
});

afterEach(() => { vi.useRealTimers(); });

describe('StoryboardR2V project-level video polling', () => {

    it('shows recovered needs-confirmation tasks without polling or marking failed', async () => {
        const recovered = videoProject({ status: 'needs_confirmation' });
        useProjectStore.getState().applyProjectReadSnapshot('a', enrichedScriptFrom(recovered, 8), true);
        vi.mocked(axios.get).mockImplementation(async (url: string) => {
            throw new Error(`unexpected GET ${url}`);
        });

        renderWithIntl(<StoryboardR2V />);
        await act(async () => { await vi.advanceTimersByTimeAsync(15000); });

        expect(axios.get).not.toHaveBeenCalled();
        expect(shotData('f1').videoStatus).toBe('needs_confirmation');
        expect(shotData('f1').videoTaskId).toBe('t1');
        expect(shotData('f1')).not.toHaveProperty('videoUrl');
    });
    it('rejects stale enriched project reads without auto-selecting or looping forever', async () => {
        const current = videoProject();
        useProjectStore.getState().applyProjectReadSnapshot('a', enrichedScriptFrom(current, 8), true);
        const staleCompleted = videoProject({ revision: 4, status: 'completed' });
        let projectReads = 0;
        vi.mocked(axios.get).mockImplementation(async (url: string) => {
            if (url === `${API_URL}/projects/a`) {
                projectReads += 1;
                return { data: enrichedScriptFrom(staleCompleted, 7) };
            }
            if (url.startsWith(`${API_URL}/tasks/`)) return { data: { status: 'processing' } };
            throw new Error(`unexpected GET ${url}`);
        });

        renderWithIntl(<StoryboardR2V />);
        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });

        expect(useProjectStore.getState().currentProject?.video_tasks?.[0].status).toBe('processing');
        expect(axios.post).not.toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/auto_select_latest_video`);
        expect(screen.getByRole('alert')).toHaveTextContent(/读取失败|已保存|未确认|保存/);

        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(projectReads).toBe(1);
    });

    it('keeps a single poll in flight across effect restarts and discards its superseded session', async () => {
        const current = videoProject();
        useProjectStore.getState().applyProjectReadSnapshot('a', enrichedScriptFrom(current), true);
        const read = deferred<{ data: unknown }>();
        let reads = 0;
        vi.mocked(axios.get).mockImplementation(async (url: string) => {
            if (url === `${API_URL}/projects/a`) { reads += 1; return read.promise; }
            if (url.startsWith(`${API_URL}/tasks/`)) return { data: { status: 'processing' } };
            throw new Error(`unexpected GET ${url}`);
        });
        renderWithIntl(<StoryboardR2V />);
        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
        act(() => useProjectStore.getState().applyProjectReadSnapshot('a', enrichedScriptFrom(videoProject({ revision: 4 })), true));
        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
        expect(reads).toBe(1);
        await act(async () => read.resolve({ data: enrichedScriptFrom(videoProject({ revision: 10, status: 'completed' })) }));
        expect(useProjectStore.getState().currentProject?.revision).toBe(4);
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('confirms auto-select ACKs through readback instead of applying the Promise-array tail', async () => {
        const current = videoProject({ pinned: true });
        useProjectStore.getState().applyProjectReadSnapshot('a', enrichedScriptFrom(current, 8), true);
        const completed = videoProject({ revision: 4, status: 'completed', pinned: true });
        const confirmed = {
            ...completed,
            revision: 6,
            frames: [
                { ...completed.frames[0], selected_video_id: 'manual', video_url: '/manual.mp4', is_video_pinned: true },
                { ...completed.frames[1], selected_video_id: null, video_url: '', is_video_pinned: false },
            ],
        } as Project;
        const staleTail = {
            ...completed,
            revision: 5,
            frames: [
                { ...completed.frames[0], selected_video_id: 't1', video_url: '/stale-tail-f1.mp4', is_video_pinned: false },
                { ...completed.frames[1], selected_video_id: 't2', video_url: '/new-f2.mp4', is_video_pinned: false },
            ],
        } as Project;

        const projectReads = [completed, confirmed, staleTail];
        vi.mocked(axios.get).mockImplementation(async (url: string) => {
            if (url === `${API_URL}/projects/a`) {
                const next = projectReads.shift();
                if (!next) throw new Error('unexpected project read');
                return { data: enrichedScriptFrom(next, 8) };
            }
            if (url.startsWith(`${API_URL}/tasks/`)) return { data: { status: 'processing' } };
            throw new Error(`unexpected GET ${url}`);
        });
        vi.mocked(axios.post)
            .mockResolvedValueOnce({ data: rawScriptFrom(confirmed, 6) })
            .mockResolvedValueOnce({ data: rawScriptFrom(staleTail, 5) });

        renderWithIntl(<StoryboardR2V />);
        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
        vi.useRealTimers();

        await waitFor(() => expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/auto_select_latest_video`));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f2/auto_select_latest_video`));
        await waitFor(() => expect(useProjectStore.getState().currentProject?.revision).toBe(6));

        const frameOne = useProjectStore.getState().currentProject?.frames.find(item => item.id === 'f1');
        expect(frameOne?.selected_video_id).toBe('manual');
        expect(frameOne?.video_url).toBe('/manual.mp4');
        expect(frameOne?.is_video_pinned).toBe(true);
        expect(shotData('f1').videoUrl).toBe('/manual.mp4');
    });

    it('ignores late auto-select confirmations after switching projects', async () => {
        const current = videoProject();
        useProjectStore.getState().applyProjectReadSnapshot('a', enrichedScriptFrom(current, 8), true);
        const completed = videoProject({ revision: 4, status: 'completed' });
        let projectReads = 0;
        vi.mocked(axios.get).mockImplementation(async (url: string) => {
            if (url === `${API_URL}/projects/a`) {
                projectReads += 1;
                return { data: enrichedScriptFrom(completed, 8) };
            }
            if (url.startsWith(`${API_URL}/tasks/`)) return { data: { status: 'processing' } };
            throw new Error(`unexpected GET ${url}`);
        });
        const autoSelect = deferred<{ data: unknown }>();
        vi.mocked(axios.post).mockReturnValue(autoSelect.promise);

        renderWithIntl(<StoryboardR2V />);
        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
        vi.useRealTimers();
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/auto_select_latest_video`));

        act(() => useProjectStore.setState({ currentProject: project('b'), projects: [project('b')], readWatermarks: {} }));
        await act(async () => autoSelect.resolve({ data: rawScriptFrom({ ...completed, revision: 5 } as Project, 5) }));
        expect(projectReads).toBe(1);

        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(screen.getAllByTestId('shot')[0]).toHaveAttribute('aria-label', 'f1');
        expect(useProjectStore.getState().currentProject?.frames).toEqual(project('b').frames);
    });
});
