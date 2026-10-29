import type { ReactNode } from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { useProjectStore, type Series } from '@/store/projectStore';
import { toast } from '@/store/toastStore';
import Home from '@/app/page';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { currentReadEnvironment, deferred } from '../../modules/__tests__/frame-command-fixtures';

vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('@/components/layout/AppShell', () => ({ default: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock('@/components/canvas/CreativeCanvas', () => ({ default: () => null }));
vi.mock('@/components/layout/TauriMenuListener', () => ({ default: () => null }));
vi.mock('@/components/project/EnvConfigDialog', () => ({ default: () => null }));
vi.mock('@/components/project/CreateProjectDialog', () => ({ default: () => null }));
vi.mock('@/components/project/ProjectCard', () => ({ default: ({ project }: { project: { title: string } }) => <div>{project.title}</div>, deriveStatus: () => 'pending', deriveCover: () => null }));
vi.mock('@/lib/useOnline', () => ({ useOnline: () => true }));
vi.mock('@/store/toastStore', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
vi.mock('@/lib/api', async importOriginal => {
    const actual = await importOriginal<typeof import('@/lib/api')>();
    return { ...actual, api: { ...actual.api, createSeriesV2: vi.fn(), listSeries: vi.fn(), getProjects: vi.fn(), getSeriesEpisodes: vi.fn() } };
});

const series = (id = 's', revision = 1, title = 'New Series'): Series => ({
    id,
    revision,
    title,
    description: '',
    characters: [],
    scenes: [],
    props: [],
    episode_ids: [],
    created_at: 1,
    updated_at: revision,
});

function renderWorkspace() {
    useProjectStore.setState({ projects: [], seriesList: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {} });
    vi.mocked(api.getProjects).mockResolvedValue([]);
    vi.mocked(api.listSeries).mockResolvedValue([]);
    vi.mocked(api.getSeriesEpisodes).mockResolvedValue([]);
    return renderWithIntl(<Home />);
}

function openCreateSeries() {
    fireEvent.click(screen.getByRole('button', { name: /新建|New/ }));
    fireEvent.click(screen.getByRole('button', { name: /新建系列|New Series/ }));
}

function fillTitle(value = 'Created Series') {
    fireEvent.change(screen.getByPlaceholderText(/我的漫剧系列|My Comic Series/), { target: { value } });
}

function createButton() {
    return within(screen.getByRole('dialog')).getByRole('button', { name: '创建系列' });
}

beforeEach(() => {
    vi.clearAllMocks();
    window.location.hash = '';
    localStorage.clear();
});

describe('workspace create series readback', () => {
    it('closes and navigates only after the series list confirms the created id and revision', async () => {
        const created = series('created', 7, 'Created Series');
        vi.mocked(api.createSeriesV2).mockResolvedValueOnce(created);
        renderWorkspace();
        vi.mocked(api.listSeries).mockResolvedValue([created]);
        openCreateSeries();
        fillTitle();

        await act(async () => fireEvent.click(createButton()));

        await waitFor(() => expect(window.location.hash).toBe('#/series/created'));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(useProjectStore.getState().seriesList).toEqual([created]);
    });

    it('keeps the dialog open and never navigates when the create acknowledgement is malformed', async () => {
        vi.mocked(api.createSeriesV2).mockResolvedValueOnce({ id: '', revision: 1, title: 'Created Series' } as Series);
        renderWorkspace();
        openCreateSeries();
        fillTitle();

        await act(async () => fireEvent.click(createButton()));

        expect(window.location.hash).toBe('');
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(toast.error).toHaveBeenCalled();
        expect(api.listSeries).toHaveBeenCalledTimes(1);
    });


    it('does not show readback failure while the acknowledged create is still reading', async () => {
        const created = series('created', 7, 'Created Series');
        const readback = deferred<Series[]>();
        vi.mocked(api.createSeriesV2).mockResolvedValueOnce(created);
        renderWorkspace();
        vi.mocked(api.listSeries).mockReturnValue(readback.promise);
        openCreateSeries();
        fillTitle();

        await act(async () => fireEvent.click(createButton()));

        await waitFor(() => expect(api.createSeriesV2).toHaveBeenCalledTimes(1));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(window.location.hash).toBe('');

        await act(async () => readback.resolve([created]));
        await waitFor(() => expect(window.location.hash).toBe('#/series/created'));
    });

    it('keeps a read-only recovery receipt after list readback fails and retry does not POST again', async () => {
        const created = series('created', 7, 'Created Series');
        vi.mocked(api.createSeriesV2).mockResolvedValueOnce(created);
        renderWorkspace();
        vi.mocked(api.listSeries).mockResolvedValue([]);
        openCreateSeries();
        fillTitle();

        await act(async () => fireEvent.click(createButton()));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent(/同步|sync|读取|read/i);
        expect(window.location.hash).toBe('');
        vi.mocked(api.listSeries).mockResolvedValue([created]);

        await act(async () => fireEvent.click(within(alert).getByRole('button', { name: /同步|Sync|重试|Retry/ })));

        await waitFor(() => expect(window.location.hash).toBe('#/series/created'));
        expect(api.createSeriesV2).toHaveBeenCalledTimes(1);
    });


    it('records the acknowledged revision floor before rejecting an older series readback', async () => {
        const acknowledged = series('created', 7, 'Created Series');
        const stale = series('created', 6, 'Created Series');
        vi.mocked(api.createSeriesV2).mockResolvedValueOnce(acknowledged);
        renderWorkspace();
        vi.mocked(api.listSeries).mockResolvedValue([stale]);
        openCreateSeries();
        fillTitle();

        await act(async () => fireEvent.click(createButton()));

        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent(/同步|sync|读取|read/i);
        expect(window.location.hash).toBe('');
        expect(useProjectStore.getState().readWatermarks[currentReadEnvironment()]?.series.created).toBe(7);
        expect(useProjectStore.getState().seriesList).toEqual([]);

        vi.mocked(api.listSeries).mockResolvedValue([acknowledged]);
        await act(async () => fireEvent.click(within(alert).getByRole('button', { name: /同步|Sync|重试|Retry/ })));
        await waitFor(() => expect(window.location.hash).toBe('#/series/created'));
    });

    it('keeps a pending create POST locked across close and reopen', async () => {
        const pending = deferred<Series>();
        const created = series('created', 7, 'Created Series');
        vi.mocked(api.createSeriesV2).mockReturnValueOnce(pending.promise);
        renderWorkspace();
        vi.mocked(api.listSeries).mockResolvedValue([created]);
        openCreateSeries();
        fillTitle();
        fireEvent.click(createButton());
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        openCreateSeries();

        fireEvent.click(createButton());

        await waitFor(() => expect(api.createSeriesV2).toHaveBeenCalledTimes(1));
        await act(async () => pending.resolve(created));
        expect(window.location.hash).toBe('');
    });

    it('keeps an acknowledged receipt across close and reopen and retries only the list read', async () => {
        const created = series('created', 7, 'Created Series');
        vi.mocked(api.createSeriesV2).mockResolvedValueOnce(created);
        renderWorkspace();
        vi.mocked(api.listSeries).mockResolvedValue([]);
        openCreateSeries();
        fillTitle();

        await act(async () => fireEvent.click(createButton()));
        expect(await screen.findByRole('alert')).toHaveTextContent(/同步|sync|读取|read/i);
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        openCreateSeries();
        const alert = await screen.findByRole('alert');
        vi.mocked(api.listSeries).mockResolvedValue([created]);

        await act(async () => fireEvent.click(within(alert).getByRole('button', { name: /同步|Sync|重试|Retry/ })));

        await waitFor(() => expect(window.location.hash).toBe('#/series/created'));
        expect(api.createSeriesV2).toHaveBeenCalledTimes(1);
    });

    it('does not close or navigate a reopened dialog from an obsolete create completion', async () => {
        const pending = deferred<Series>();
        const created = series('created', 7, 'Created Series');
        vi.mocked(api.createSeriesV2).mockReturnValueOnce(pending.promise);
        renderWorkspace();
        vi.mocked(api.listSeries).mockResolvedValue([created]);
        openCreateSeries();
        fillTitle();
        fireEvent.click(createButton());
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        openCreateSeries();

        await act(async () => pending.resolve(created));

        expect(window.location.hash).toBe('');
        expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('does not navigate after the dialog unmounts while create is in flight', async () => {
        const pending = deferred<Series>();
        const created = series('created', 7, 'Created Series');
        vi.mocked(api.createSeriesV2).mockReturnValueOnce(pending.promise);
        const rendered = renderWorkspace();
        vi.mocked(api.listSeries).mockResolvedValue([created]);
        openCreateSeries();
        fillTitle();
        fireEvent.click(createButton());
        rendered.unmount();

        await act(async () => pending.resolve(created));

        expect(window.location.hash).toBe('');
    });

    it('blocks duplicate create submissions while the first create is in flight', async () => {
        const pending = deferred<Series>();
        const created = series('created', 7, 'Created Series');
        vi.mocked(api.createSeriesV2).mockReturnValueOnce(pending.promise);
        renderWorkspace();
        vi.mocked(api.listSeries).mockResolvedValue([created]);
        openCreateSeries();
        fillTitle();
        const create = createButton();

        fireEvent.click(create);
        fireEvent.click(create);

        await waitFor(() => expect(api.createSeriesV2).toHaveBeenCalledTimes(1));
        await act(async () => pending.resolve(created));
    });
});
