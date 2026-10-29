import type { ReactNode } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Home from '@/app/page';
import { api } from '@/lib/api';
import { useProjectStore, type Project } from '@/store/projectStore';
import { clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, enriched, project } from '../../modules/__tests__/frame-command-fixtures';

vi.mock('next/dynamic', async () => {
    const { lazy, Suspense } = await import('react');
    return { default: (loader: () => Promise<{ default: React.ComponentType<Record<string, unknown>> }>) => {
        if (loader.toString().includes('ProjectClient')) {
            const Client = lazy(loader);
            return function LoadedProject(props: Record<string, unknown>) {
                return <Suspense fallback={<p>Loading project shell</p>}><Client {...props} /></Suspense>;
            };
        }
        if (loader.toString().includes('ScriptEditorShell')) return function Editor({ projectId }: { projectId?: string }) {
            return <p>editor:{projectId}</p>;
        };
        if (loader.toString().includes('SeriesDetailPage')) return function Series({ seriesId }: { seriesId: string }) {
            return <p>series:{seriesId}</p>;
        };
        return () => null;
    } };
});
vi.mock('@/components/layout/AppShell', () => ({ default: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock('@/components/canvas/CreativeCanvas', () => ({ default: () => null }));
vi.mock('@/components/layout/TauriMenuListener', () => ({ default: () => null }));
vi.mock('@/components/project/CreateProjectDialog', () => ({ default: () => null }));
vi.mock('@/components/common/ModelSettingsModal', () => ({ default: () => null }));
vi.mock('@/components/project/EnvConfigDialog', () => ({ default: () => null }));
vi.mock('@/components/project/PromptConfigModal', () => ({ default: () => null }));
vi.mock('@/components/modules/EntityConfirmModal', () => ({ default: () => null }));
vi.mock('@/components/modules/ScriptProcessor', () => ({ default: () => <p>script:{useProjectStore.getState().currentProject?.id}</p> }));
vi.mock('@/components/modules/Cast', () => ({ default: () => <p>cast:{useProjectStore.getState().currentProject?.id}</p> }));
vi.mock('@/components/modules/ArtDirection', () => ({ default: () => <p>art_direction:{useProjectStore.getState().currentProject?.id}</p> }));
vi.mock('@/components/modules/ConsistencyVault', () => ({ default: () => <p>assets:{useProjectStore.getState().currentProject?.id}</p> }));
vi.mock('@/components/modules/StoryboardComposer', () => ({ default: () => <p>storyboard:{useProjectStore.getState().currentProject?.id}</p> }));
vi.mock('@/components/modules/StoryboardR2V', () => ({ default: () => <p>storyboard_r2v:{useProjectStore.getState().currentProject?.id}</p> }));
vi.mock('@/components/modules/VideoAssembly', () => ({ default: () => <p>assembly:{useProjectStore.getState().currentProject?.id}</p> }));
vi.mock('@/components/modules/VideoGenerator', () => ({ default: () => <p>motion:{useProjectStore.getState().currentProject?.id}</p> }));
vi.mock('@/lib/useOnline', () => ({ useOnline: () => true }));
vi.mock('@/lib/api', async importOriginal => {
    const actual = await importOriginal<typeof import('@/lib/api')>();
    return { ...actual, api: { ...actual.api, getProjects: vi.fn(), listSeries: vi.fn(), getSeries: vi.fn(), getSeriesEpisodes: vi.fn() } };
});

let projects: Project[];
let mode: 'r2v' | 'i2v_legacy';
let contentMode: 'scripted' | 'freeform';
let fetchMock: ReturnType<typeof vi.fn>;

async function navigate(hash: string) {
    await act(async () => {
        window.location.hash = hash;
        window.dispatchEvent(new Event('hashchange'));
    });
}

beforeEach(() => {
    vi.clearAllMocks();
    window.location.hash = '';
    localStorage.clear();
    mode = 'r2v'; contentMode = 'scripted';
    projects = ['a', 'b'].map((id, i) => ({ ...project(id), workflow_mode: mode, title: `Episode ${id}`, series_id: 's', episode_number: i + 1,
        read_versions: { project: 3, series: { id: 's', revision: 0 }, library: 0 }, characters: [] }));
    useProjectStore.setState({ projects, currentProject: null, currentSeries: null, seriesList: [], readWatermarks: {}, runningOps: {} });
    vi.mocked(api.getProjects).mockResolvedValue(projects);
    vi.mocked(api.listSeries).mockResolvedValue([]);
    vi.mocked(api.getSeriesEpisodes).mockResolvedValue(projects);
    vi.mocked(api.getSeries).mockImplementation(async () => ({ id: 's', revision: 0, title: 'Series S', description: '',
        characters: [], scenes: [], props: [], episode_ids: ['a', 'b'], content_mode: contentMode, created_at: 1, updated_at: 1 }));
    fetchMock = vi.fn(async (input: string | URL | Request) => {
        const id = String(input).split('/projects/')[1];
        if (!['a', 'b'].includes(id)) return { ok: false, status: 404 };
        return { ok: true, json: async () => enriched({ ...ack([], id, 3), title: `Episode ${id}`, series_id: 's', workflow_mode: mode }, 0, 0) };
    });
    vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { vi.unstubAllGlobals(); clearRuntimeEnvironment(); });

describe('actual episode mini-list through workspace route and project shell', () => {
    it.each([['3. Cast', 'cast'], ['4. Storyboard', 'storyboard_r2v']])('keeps %s while selecting only the new episode ID', async (label, step) => {
        window.location.hash = '#/series/s/episode/a';
        renderWithIntl(<Home />);
        await screen.findByText('script:a');
        fireEvent.click(screen.getByRole('button', { name: new RegExp(label.replace('.', '\\.')) }));
        await screen.findByText(`${step}:a`);
        fireEvent.click(await screen.findByRole('button', { name: /Episode b/ }));
        await waitFor(() => expect(fetchMock.mock.calls.map(([url]) => String(url).split('/projects/')[1])).toContain('b'));
        expect(fetchMock.mock.calls.every(([url]) => !String(url).includes('#'))).toBe(true);
        await screen.findByText(`${step}:b`);
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        fireEvent.click(screen.getByRole('button', { name: /Episode a/ }));
        await screen.findByText(`${step}:a`);
    });

    it.each(['cast', 'storyboard_r2v', 'assembly'])('restores a directly opened %s link', async step => {
        window.location.hash = `#/project/b#${step}`;
        renderWithIntl(<Home />);
        await screen.findByText(`${step}:b`);
        expect(fetchMock.mock.calls.every(([url]) => String(url).endsWith('/projects/b'))).toBe(true);
    });

    it('handles same-project route step changes without refetching or overriding later sidebar choices', async () => {
        window.location.hash = '#/project/a';
        renderWithIntl(<Home />);
        await screen.findByText('script:a');
        const reads = fetchMock.mock.calls.length;
        await navigate('#/project/a#cast');
        await screen.findByText('cast:a');
        expect(fetchMock).toHaveBeenCalledTimes(reads);
        fireEvent.click(screen.getByRole('button', { name: /4\. Storyboard/ }));
        await screen.findByText('storyboard_r2v:a');
        act(() => useProjectStore.setState(state => ({ currentProject: { ...state.currentProject!, revision: 4 } })));
        expect(screen.getByText('storyboard_r2v:a')).toBeInTheDocument();
    });

    it.each(['', 'unknown', 'assets'])('falls back safely for an unavailable unified step %s', async step => {
        window.location.hash = `#/project/b#${step}`;
        renderWithIntl(<Home />);
        await screen.findByText('script:b');
        expect(fetchMock.mock.calls.every(([url]) => String(url).endsWith('/projects/b'))).toBe(true);
    });

    it('preserves legacy storyboard while rejecting unified-only Cast', async () => {
        mode = 'i2v_legacy';
        useProjectStore.setState({ projects: projects.map(p => ({ ...p, workflow_mode: mode })) });
        window.location.hash = '#/project/a#storyboard';
        renderWithIntl(<Home />);
        await screen.findByText('storyboard:a');
        await navigate('#/project/b#cast');
        await screen.findByText('script:b');
    });

    it('falls back to the first actual freeform step after series readback', async () => {
        contentMode = 'freeform';
        window.location.hash = '#/project/a#script';
        renderWithIntl(<Home />);
        await screen.findByText('art_direction:a');
        expect(screen.queryByRole('button', { name: /1\. Script/ })).not.toBeInTheDocument();
    });

    it.each([['#/series/s', 'series:s'], ['#/project/b/editor', 'editor:b']])('keeps existing route %s', async (hash, text) => {
        window.location.hash = hash;
        renderWithIntl(<Home />);
        await screen.findByText(text);
        expect(fetchMock).not.toHaveBeenCalled();
    });
});
