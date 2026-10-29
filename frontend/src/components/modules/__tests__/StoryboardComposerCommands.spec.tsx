import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardComposer from '../StoryboardComposer';
import { ack, deferred, frame, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn() } }));
vi.mock('../StoryboardFrameEditor', () => ({ default: () => null }));

const actions = [
    { name: '复制', method: 'post', path: '/frames/copy', result: [frame('f1'), frame('copy'), frame('f2')] },
    { name: '删除', method: 'delete', path: '/frames/f1', result: [frame('f2')] },
    { name: '下移', method: 'put', path: '/frames/reorder', result: [frame('f2'), frame('f1')] },
] as const;
function button(name: string) { return screen.getAllByRole('button', { name })[0]; }

describe('StoryboardComposer committed structural commands', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        vi.spyOn(window, 'alert').mockImplementation(() => {});
        useProjectStore.setState({ projects: [project()], currentProject: project(), runningOps: {}, renderingFrames: new Set() });
    });

    it.each(actions)('$name waits for and applies the actual ACK via CRUD transport', async command => {
        const request = deferred();
        vi.mocked(axios[command.method]).mockReturnValueOnce(request.promise);
        renderWithIntl(<StoryboardComposer />);
        fireEvent.click(button(command.name));
        expect(useProjectStore.getState().currentProject?.frames.map(f => f.id)).toEqual(['f1', 'f2']);
        expect(button(command.name)).toBeDisabled();
        expect(axios[command.method]).toHaveBeenCalledWith(`${API_URL}/projects/a${command.path}`, ...(command.method === 'delete' ? [] : [expect.any(Object)]));
        await act(async () => request.resolve({ data: ack([...command.result]) }));
        expect(useProjectStore.getState().currentProject).toMatchObject({ revision: 4, originalText: 'server text' });
        expect(useProjectStore.getState().currentProject?.frames.map(f => f.id)).toEqual(command.result.map(f => f.id));
        expect(useProjectStore.getState().currentProject?.characters[0].id).toBe('shared');
        expect(axios.get).not.toHaveBeenCalled();
    });

    it.each(actions.flatMap(command => [409, 503, 0].map(status => ({ ...command, status }))))(
        '$name rejection $status is visible without pretending it saved or retrying', async command => {
            vi.mocked(axios[command.method]).mockRejectedValueOnce(command.status ? { response: { status: command.status } } : new Error('network'));
            renderWithIntl(<StoryboardComposer />);
            fireEvent.click(button(command.name));
            expect(await screen.findByRole('alert')).toHaveTextContent(/未确认|冲突|保存|服务/);
            expect(useProjectStore.getState().currentProject?.frames.map(f => f.id)).toEqual(['f1', 'f2']);
            expect(useProjectStore.getState().currentProject?.revision).toBe(3);
            expect(axios[command.method]).toHaveBeenCalledTimes(1);
            expect(axios.get).not.toHaveBeenCalled();
            expect(button(command.name)).not.toBeDisabled();
        },
    );

    it('prevents same-tick duplicate submissions and ignores older ACKs independently', async () => {
        const request = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(request.promise);
        renderWithIntl(<StoryboardComposer />);
        const copy = button('复制');
        act(() => { fireEvent.click(copy); fireEvent.click(copy); });
        expect(axios.post).toHaveBeenCalledTimes(1);
        act(() => useProjectStore.setState({ currentProject: { ...project('a', 5), frames: [frame('newer')] } }));
        await act(async () => request.resolve({ data: ack() }));
        expect(useProjectStore.getState().currentProject?.frames[0].id).toBe('newer');
        expect(useProjectStore.getState().projects[0].revision).toBe(4);
    });

    it('keeps B selected when a pending command on A completes after switching', async () => {
        const request = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(request.promise);
        renderWithIntl(<StoryboardComposer />);
        fireEvent.click(button('复制'));
        act(() => useProjectStore.setState({ currentProject: project('b') }));
        await act(async () => request.resolve({ data: ack() }));
        await waitFor(() => expect(useProjectStore.getState().projects[0].revision).toBe(4));
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
});
