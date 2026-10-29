import type { ReactNode } from 'react';
import { act, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { useProjectStore, type Series } from '@/store/projectStore';
import Home from '@/app/page';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred, project } from '../../modules/__tests__/frame-command-fixtures';

vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('@/components/layout/AppShell', () => ({ default: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock('@/components/canvas/CreativeCanvas', () => ({ default: () => null }));
vi.mock('@/components/layout/TauriMenuListener', () => ({ default: () => null }));
vi.mock('@/components/project/EnvConfigDialog', () => ({ default: () => null }));
vi.mock('@/components/project/ProjectCard', () => ({ default: ({ project }: { project: { title: string } }) => <div>{project.title}</div>, deriveStatus: () => 'pending', deriveCover: () => null }));
vi.mock('@/lib/useOnline', () => ({ useOnline: () => true }));
vi.mock('@/lib/api', async importOriginal => { const actual = await importOriginal<typeof import('@/lib/api')>(); return { ...actual, api: { ...actual.api, listSeries: vi.fn(), getProjects: vi.fn(), getSeriesEpisodes: vi.fn() } }; });
const series = (revision: number): Series => ({ id: 's', revision, title: 'Series', description: '', characters: [], scenes: [], props: [], episode_ids: ['episode'], created_at: 1, updated_at: revision });

beforeEach(() => {
    vi.clearAllMocks(); window.location.hash = ''; localStorage.clear();
    useProjectStore.setState({ projects: [], seriesList: [], currentProject: null, readWatermarks: {}, runningOps: {} });
    vi.mocked(api.getProjects).mockResolvedValue([]);
    vi.mocked(api.listSeries).mockResolvedValue([series(1)]);
});

describe('workspace episode read ownership', () => {
    it('does not apply an old episode response after a newer series refresh', async () => {
        const old = deferred<ReturnType<typeof project>[]>();
        vi.mocked(api.getSeriesEpisodes).mockReturnValueOnce(old.promise).mockResolvedValueOnce([{ ...project('episode'), title: 'New episode' }]);
        renderWithIntl(<Home />);
        await waitFor(() => expect(api.getSeriesEpisodes).toHaveBeenCalledTimes(1));
        act(() => useProjectStore.setState({ seriesList: [series(2)] }));
        await screen.findByText('New episode');
        await act(async () => old.resolve([{ ...project('episode'), title: 'Old episode' }]));
        expect(screen.getByText('New episode')).toBeInTheDocument();
        expect(screen.queryByText('Old episode')).not.toBeInTheDocument();
    });
    it('clears episode counts when the series list becomes empty', async () => {
        vi.mocked(api.getSeriesEpisodes).mockResolvedValue([{ ...project('episode'), title: 'Visible episode' }]);
        renderWithIntl(<Home />);
        await screen.findByText('Visible episode');
        act(() => useProjectStore.setState({ seriesList: [] }));
        const all = screen.getByRole('tab', { name: /全部/ });
        await waitFor(() => expect(all.textContent).toMatch(/0/));
    });
});
