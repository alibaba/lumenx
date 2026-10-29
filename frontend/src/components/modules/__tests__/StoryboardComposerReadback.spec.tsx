import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { useProjectStore, type Project } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardComposer from '../StoryboardComposer';
import { ack, deferred, enriched, frame, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn() } }));
vi.mock('../StoryboardFrameEditor', () => ({ default: () => null }));

function rawProject(value: Project, revision = value.revision) {
    return { ...ack(value.frames as Record<string, unknown>[], value.id, revision), characters: value.characters,
        scenes: value.scenes, props: value.props, video_tasks: value.video_tasks };
}
function read(value: Project, library = 8) { return enriched(rawProject(value), library); }
function task(id = 'task-a') {
    return { id, project_id: 'a', frame_id: 'f1', status: 'completed', video_url: '/done.mp4', image_url: '/f1.png', prompt: 'move', created_at: 1 };
}
function composerProject(id = 'a', revision = 3): Project {
    return { ...project(id, revision), originalText: 'script text', frames: [
        { ...frame('f1'), image_url: '/f1.png', selected_video_id: 'task-a', action_description: 'first' },
        { ...frame('f2'), image_url: '/f2.png', action_description: 'second' },
    ], video_tasks: id === 'a' ? [task()] : [] } as Project;
}
function renderFromProject(value = composerProject()) {
    useProjectStore.getState().applyProjectReadSnapshot(value.id, read(value), true);
    return renderWithIntl(<StoryboardComposer />);
}

beforeEach(() => {
    vi.resetAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.spyOn(window, 'alert').mockImplementation(() => undefined);
    useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });

});

describe('StoryboardComposer guarded readbacks', () => {
    it('releases busy state after a render completes while its editor was unmounted', async () => {
        const request = deferred<unknown>();
        vi.mocked(axios.post).mockReturnValue(request.promise);
        const view = renderFromProject();
        fireEvent.click(screen.getAllByTitle('Generate 1 variant')[0]);
        expect(useProjectStore.getState().renderingFrames.has('f1')).toBe(true);
        view.unmount();
        await act(async () => request.resolve({ data: rawProject(composerProject('a', 4)) }));
        expect(useProjectStore.getState().renderingFrames.has('f1')).toBe(false);
        expect(axios.get).not.toHaveBeenCalled();
    });
    it('does not submit paid commands from a different API environment', async () => {
        renderFromProject();
        act(() => useProjectStore.setState({ currentProject: { ...composerProject(), readEnvironment: 'http://other.test' } }));
        fireEvent.click(screen.getAllByTitle('Generate 1 variant')[0]);
        fireEvent.click(screen.getByRole('button', { name: /生成分镜|Generate/i }));
        await act(async () => {});
        expect(axios.post).not.toHaveBeenCalled();
    });
    it('confirms analyze raw ACK with one guarded read and rejects stale source reads without paid retry', async () => {
        const current = composerProject();
        const generated = { ...current, revision: 4, frames: [frame('new-1'), frame('new-2')] } as Project;
        renderFromProject(current);
        vi.mocked(axios.post).mockResolvedValue({ data: rawProject(generated, 4) });
        vi.mocked(axios.get).mockResolvedValue({ data: read(generated, 7) });

        fireEvent.click(screen.getByRole('button', { name: /生成分镜|Generate/i }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/storyboard/analyze`, { text: 'server text' }));
        await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
        expect(useProjectStore.getState().currentProject?.frames.map(item => item.id)).toEqual(['f1', 'f2']);
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(screen.getByRole('alert')).toHaveTextContent(/read|读取|保存|saved/i);
    });


    it('keeps create dialog open and reports readback failure without a write-failure alert', async () => {
        const current = composerProject();
        const created = { ...current, revision: 4, frames: [...current.frames, frame('new-frame')] } as Project;
        renderFromProject(current);
        vi.mocked(axios.post).mockResolvedValue({ data: rawProject(created, 4) });
        vi.mocked(axios.get).mockResolvedValue({ data: read(created, 7) });

        fireEvent.click(screen.getAllByTitle('在开头插入帧')[0]);
        fireEvent.change(screen.getByPlaceholderText('What is happening in this frame?'), { target: { value: 'new action' } });
        fireEvent.click(screen.getByRole('button', { name: /Create Frame/i }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames`, expect.objectContaining({ action_description: 'new action' })));
        await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
        expect(screen.getByText('Add New Frame')).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent(/read|读取|保存|saved/i);
        expect(window.alert).not.toHaveBeenCalled();
    });

    it('extracts previous end frame through raw ACK readback and ignores late A response after A→B→A remount', async () => {
        const current = composerProject();
        const extracted = { ...current, revision: 4, frames: current.frames.map(item => item.id === 'f2' ? { ...item, rendered_image_url: '/confirmed.png' } : item) } as Project;
        renderFromProject(current);
        const request = deferred<unknown>();
        vi.mocked(axios.post).mockReturnValue(request.promise);
        vi.mocked(axios.get).mockResolvedValue({ data: read(extracted) });

        fireEvent.click(screen.getByTitle('使用上一帧尾帧'));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f2/extract_last_frame`, { video_task_id: 'task-a' });

        act(() => useProjectStore.getState().applyProjectReadSnapshot('b', read(composerProject('b')), true));
        act(() => useProjectStore.getState().applyProjectReadSnapshot('a', read(composerProject('a', 5)), true));
        await act(async () => request.resolve({ data: rawProject(extracted, 4) }));

        expect(useProjectStore.getState().currentProject?.id).toBe('a');
        expect(axios.get).not.toHaveBeenCalled();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(useProjectStore.getState().currentProject?.revision).toBe(5);
        expect(useProjectStore.getState().currentProject?.frames.find(item => item.id === 'f2')?.rendered_image_url).toBeUndefined();
    });

    it('renders frame with one guarded read and blocks same-frame double clicks while pending', async () => {
        const current = composerProject();
        const rendered = { ...current, revision: 4, frames: current.frames.map(item => item.id === 'f1' ? { ...item, rendered_image_url: '/rendered.png' } : item) } as Project;
        renderFromProject(current);
        const request = deferred<unknown>();
        vi.mocked(axios.post).mockReturnValue(request.promise);
        vi.mocked(axios.get).mockResolvedValue({ data: read(rendered) });

        const renderButton = screen.getAllByTitle('Generate 1 variant')[0];
        act(() => { fireEvent.click(renderButton); fireEvent.click(renderButton); });
        expect(axios.post).toHaveBeenCalledTimes(1);
        await act(async () => request.resolve({ data: rawProject(rendered, 4) }));

        await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
        expect(useProjectStore.getState().currentProject?.frames.find(item => item.id === 'f1')?.rendered_image_url).toBe('/rendered.png');
    });

    it('toggle lock uses raw ACK readback without an extra manual GET', async () => {
        const current = composerProject();
        const locked = { ...current, revision: 4, frames: current.frames.map(item => item.id === 'f1' ? { ...item, locked: true } : item) } as Project;
        renderFromProject(current);
        vi.mocked(axios.post).mockResolvedValue({ data: rawProject(locked, 4) });
        vi.mocked(axios.get).mockResolvedValue({ data: read(locked) });

        fireEvent.click(screen.getAllByTitle('锁定')[0]);

        await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
        expect(useProjectStore.getState().currentProject?.frames[0].locked).toBe(true);
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('keeps create locked across remounts until the first request resolves', async () => {
        const current = composerProject();
        const request = deferred<unknown>();
        const first = renderFromProject(current);
        vi.mocked(axios.post).mockReturnValue(request.promise);

        fireEvent.click(screen.getAllByTitle('在开头插入帧')[0]);
        fireEvent.change(screen.getByPlaceholderText('What is happening in this frame?'), { target: { value: 'new action' } });
        fireEvent.click(screen.getByRole('button', { name: /Create Frame/i }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));

        first.unmount();
        renderFromProject(current);
        fireEvent.click(screen.getAllByTitle('在开头插入帧')[0]);
        fireEvent.change(screen.getByPlaceholderText('What is happening in this frame?'), { target: { value: 'new action again' } });
        fireEvent.click(screen.getByRole('button', { name: /Create Frame/i }));
        expect(axios.post).toHaveBeenCalledTimes(1);

        await act(async () => request.resolve({ data: rawProject({ ...current, revision: 4 } as Project, 4) }));
    });

    it('blocks another render after a saved readback failure', async () => {
        const current = composerProject();
        const rendered = { ...current, revision: 4, frames: current.frames.map(item => item.id === 'f1' ? { ...item, rendered_image_url: '/rendered.png' } : item) } as Project;
        renderFromProject(current);
        vi.mocked(axios.post).mockResolvedValue({ data: rawProject(rendered, 4) });
        vi.mocked(axios.get).mockResolvedValue({ data: read(rendered, 7) });

        const renderButton = screen.getAllByTitle('Generate 1 variant')[0];
        fireEvent.click(renderButton);
        await screen.findByRole('alert');
        fireEvent.click(renderButton);

        expect(axios.post).toHaveBeenCalledTimes(1);
    });

});
