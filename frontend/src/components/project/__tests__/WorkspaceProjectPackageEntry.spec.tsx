import type { ReactNode } from 'react';
import { fireEvent, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Home from '@/app/page';
import { api } from '@/lib/api';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';

function MockPackageImportDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
    return isOpen ? (
        <div role="dialog" aria-label="project package import dialog">
            <button type="button" onClick={onClose}>close package import</button>
        </div>
    ) : null;
}

function MockFileImportDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
    return isOpen ? (
        <div role="dialog" aria-label="script file import dialog">
            <button type="button" onClick={onClose}>close file import</button>
        </div>
    ) : null;
}

function MockDynamicNull() {
    return null;
}

vi.mock('next/dynamic', () => ({
    default: (loader: () => Promise<unknown>) => {
        const source = loader.toString();
        if (source.includes('ProjectPackageImportDialog')) return MockPackageImportDialog;
        if (source.includes('ImportFileDialog')) return MockFileImportDialog;
        return MockDynamicNull;
    },
}));
vi.mock('@/components/layout/AppShell', () => ({ default: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock('@/components/canvas/CreativeCanvas', () => ({ default: () => null }));
vi.mock('@/components/layout/TauriMenuListener', () => ({ default: () => null }));
vi.mock('@/components/project/CreateProjectDialog', () => ({ default: () => null }));
vi.mock('@/components/project/ProjectCard', () => ({ default: ({ project }: { project: { title: string } }) => <div>{project.title}</div>, deriveStatus: () => 'pending', deriveCover: () => null }));
vi.mock('@/lib/useOnline', () => ({ useOnline: () => true }));
vi.mock('@/lib/api', async importOriginal => {
    const actual = await importOriginal<typeof import('@/lib/api')>();
    return { ...actual, api: { ...actual.api, getProjects: vi.fn(), listSeries: vi.fn(), getSeriesEpisodes: vi.fn() } };
});

beforeEach(() => {
    vi.clearAllMocks();
    useProjectStore.setState({ projects: [], seriesList: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {} });
    vi.mocked(api.getProjects).mockResolvedValue([]);
    vi.mocked(api.listSeries).mockResolvedValue([]);
    vi.mocked(api.getSeriesEpisodes).mockResolvedValue([]);
});

describe('workspace project package import entry', () => {
    it('opens the package import dialog from its own workspace button without reusing file import', () => {
        renderWithIntl(<Home />);

        fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ }));

        expect(screen.getByRole('dialog', { name: 'project package import dialog' })).toBeInTheDocument();
        expect(screen.queryByRole('dialog', { name: 'script file import dialog' })).not.toBeInTheDocument();
    });
});
