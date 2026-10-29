import type { ComponentProps } from 'react';
import { act, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { api } from '@/lib/api';
import { useProjectStore, type Project } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import VideoGenerator from '../VideoGenerator';
import type VideoCreator from '../VideoCreator';
import type VideoSidebar from '../VideoSidebar';
import { ack, deferred, enriched, frame, project } from './frame-command-fixtures';
const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';
const currentEnvKey = () => getRuntimeEnvironment()?.key ?? activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID }).key;

let creatorProps: ComponentProps<typeof VideoCreator> | null = null;

vi.mock('@/lib/api', async importOriginal => {
    const actual = await importOriginal<typeof import('@/lib/api')>();
    return { ...actual, api: { ...actual.api, getProject: vi.fn() } };
});
vi.mock('../VideoCreator', () => ({
    default: (_props: ComponentProps<typeof VideoCreator>) => {
        creatorProps = _props;
        return <div data-testid="creator" />;
    },
}));
vi.mock('../VideoSidebar', () => ({
    default: (props: ComponentProps<typeof VideoSidebar>) => (
        <output data-testid="sidebar-tasks">{JSON.stringify(props.tasks)}</output>
    ),
}));

function videoTask(id: string, status: 'pending' | 'processing' | 'completed' | 'needs_confirmation') {
    return { id, project_id: 'a', frame_id: 'f1', image_url: '/f1.png', prompt: 'move', status, video_url: status === 'completed' ? '/done.mp4' : undefined, created_at: 1 };
}

function rawProject(value: Project, revision = value.revision) {
    return { ...ack(value.frames as Record<string, unknown>[], value.id, revision), characters: value.characters,
        scenes: value.scenes, props: value.props, video_tasks: value.video_tasks };
}

function read(value: Project, library = 8) {
    return enriched(rawProject(value), library);
}

function projectWithTask(status: 'pending' | 'processing' | 'completed' | 'needs_confirmation') {
    return { ...project('a', 3), frames: [frame('f1')], video_tasks: [videoTask('task-a', status)] } as Project;
}

beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
    vi.resetAllMocks();
    creatorProps = null;
    vi.useFakeTimers();
    useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
});

afterEach(() => { vi.useRealTimers(); });

describe('VideoGenerator guarded project polling', () => {

    it('displays recovered needs-confirmation tasks without project polling', async () => {
        useProjectStore.getState().applyProjectReadSnapshot('a', read(projectWithTask('needs_confirmation')), true);

        renderWithIntl(<VideoGenerator />);
        await act(async () => { await vi.advanceTimersByTimeAsync(9000); });

        expect(api.getProject).not.toHaveBeenCalled();
        expect(screen.getByTestId('sidebar-tasks')).toHaveTextContent('needs_confirmation');
    });
    it('clears a prior project poll error when opening another project', async () => {
        useProjectStore.getState().applyProjectReadSnapshot('a', read(projectWithTask('processing')), true);
        vi.mocked(api.getProject).mockRejectedValue(new Error('offline'));
        renderWithIntl(<VideoGenerator />);
        await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
        expect(screen.getByRole('alert')).toBeInTheDocument();
        act(() => useProjectStore.getState().applyProjectReadSnapshot('b', read({ ...project('b'), video_tasks: [] }), true));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
    it('does not let stale same-project callback data override the confirmed task list', async () => {
        const current = { ...projectWithTask('completed'), revision: 5 };
        useProjectStore.getState().applyProjectReadSnapshot('a', read(current), true);
        renderWithIntl(<VideoGenerator />);
        act(() => creatorProps!.onTaskCreated({ ...projectWithTask('processing'), readEnvironment: currentEnvKey() }));
        expect(screen.getByTestId('sidebar-tasks')).toHaveTextContent('completed');
        expect(screen.getByTestId('sidebar-tasks')).not.toHaveTextContent('processing');
    });
    it('serializes project polls and applies completion through the source-floor read path', async () => {
        const current = projectWithTask('processing');
        useProjectStore.getState().applyProjectReadSnapshot('a', read(current), true);
        const completed = { ...current, revision: 4, video_tasks: [videoTask('task-a', 'completed')] } as Project;
        const firstRead = deferred<unknown>();
        vi.mocked(api.getProject).mockReturnValue(firstRead.promise);

        renderWithIntl(<VideoGenerator />);
        await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
        expect(api.getProject).toHaveBeenCalledTimes(1);

        await act(async () => firstRead.resolve(read(completed)));
        vi.useRealTimers();

        await waitFor(() => expect(useProjectStore.getState().currentProject?.video_tasks?.[0].status).toBe('completed'));
        expect(screen.getByTestId('sidebar-tasks')).toHaveTextContent('completed');
    });

    it('does not start a second project poll when task snapshots change while a read is pending', async () => {
        const current = projectWithTask('processing');
        useProjectStore.getState().applyProjectReadSnapshot('a', read(current), true);
        const firstRead = deferred<unknown>();
        vi.mocked(api.getProject).mockReturnValue(firstRead.promise);

        renderWithIntl(<VideoGenerator />);
        await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
        act(() => {
            const refreshed = { ...current, revision: 4, video_tasks: [{ ...videoTask('task-a', 'processing'), created_at: 2 }] } as Project;
            useProjectStore.getState().applyProjectReadSnapshot('a', read(refreshed), true);
        });
        await act(async () => { await vi.advanceTimersByTimeAsync(9000); });

        expect(api.getProject).toHaveBeenCalledTimes(1);
    });

    it('rejects late poll reads after switching projects before store mutation', async () => {
        const current = projectWithTask('processing');
        useProjectStore.getState().applyProjectReadSnapshot('a', read(current), true);
        const completed = { ...current, revision: 4, video_tasks: [videoTask('task-a', 'completed')] } as Project;
        const firstRead = deferred<unknown>();
        vi.mocked(api.getProject).mockReturnValue(firstRead.promise);

        renderWithIntl(<VideoGenerator />);
        await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
        act(() => useProjectStore.setState({ currentProject: project('b'), projects: [project('b')], readWatermarks: {} }));
        await act(async () => firstRead.resolve(read(completed)));
        vi.useRealTimers();

        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(useProjectStore.getState().projects.find(item => item.id === 'a')).toBeUndefined();
    });

    it('ignores stale child task updates after switching to another project', async () => {
        const current = projectWithTask('processing');
        useProjectStore.getState().applyProjectReadSnapshot('a', read(current), true);
        renderWithIntl(<VideoGenerator />);
        expect(creatorProps).not.toBeNull();

        act(() => useProjectStore.getState().applyProjectReadSnapshot('b', read({ ...project('b', 3), frames: [frame('b1')], video_tasks: [] } as Project), true));
        act(() => creatorProps!.onTaskCreated({ ...current, revision: 4, readEnvironment: currentEnvKey(), video_tasks: [videoTask('task-a', 'completed')] }));

        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(screen.getByTestId('sidebar-tasks')).not.toHaveTextContent('completed');
    });
});
