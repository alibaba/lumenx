import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore } from '@/store/projectStore';
import ProjectCard from '@/components/project/ProjectCard';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred, project } from '../../modules/__tests__/frame-command-fixtures';

vi.mock('@/lib/api', async importOriginal => {
    const actual = await importOriginal<typeof import('@/lib/api')>();
    return { ...actual, api: { ...actual.api, deleteProject: vi.fn(), toggleProjectStarred: vi.fn(), exportProjectPackage: vi.fn() } };
});

function seed(projectId = 'a') {
    const current = project(projectId);
    useProjectStore.setState({
        projects: [current],
        currentProject: current,
        currentSeries: null,
        readWatermarks: {},
        runningOps: {},
    });
    return current;
}

beforeEach(() => {
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
    vi.resetAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    localStorage.clear();
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('project deletion persistence boundary', () => {
    it('preserves cached and current project when backend deletion fails', async () => {
        const current = seed('a');
        vi.mocked(api.deleteProject).mockRejectedValue(new Error('offline'));

        await expect(useProjectStore.getState().deleteProject('a')).rejects.toThrow('offline');

        expect(useProjectStore.getState().projects).toEqual([current]);
        expect(useProjectStore.getState().currentProject?.id).toBe('a');
    });

    it('coalesces duplicate in-flight store deletes for the same project', async () => {
        seed('a');
        const pending = deferred<void>();
        vi.mocked(api.deleteProject).mockReturnValue(pending.promise);

        const first = useProjectStore.getState().deleteProject('a');
        const second = useProjectStore.getState().deleteProject('a');
        expect(api.deleteProject).toHaveBeenCalledTimes(1);

        await act(async () => pending.resolve(undefined));
        await expect(first).resolves.toBeUndefined();
        await expect(second).resolves.toBeUndefined();
        expect(useProjectStore.getState().projects).toEqual([]);
    });

    it('preserves a different current project when a late delete completes', async () => {
        const doomed = project('a');
        const survivor = project('b');
        useProjectStore.setState({ projects: [doomed, survivor], currentProject: doomed, currentSeries: null, readWatermarks: {}, runningOps: {} });
        const pending = deferred<void>();
        vi.mocked(api.deleteProject).mockReturnValue(pending.promise);

        const deletion = useProjectStore.getState().deleteProject('a');
        act(() => useProjectStore.setState({ currentProject: survivor }));
        await act(async () => pending.resolve(undefined));
        await deletion;

        expect(useProjectStore.getState().projects.map(item => item.id)).toEqual(['b']);
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
    });

    it('keeps the project card menu open and shows persistence failure when delete rejects', async () => {
        const onDelete = vi.fn().mockRejectedValue(new Error('offline'));
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        renderWithIntl(<ProjectCard project={project('a')} onDelete={onDelete} />);

        fireEvent.click(screen.getByLabelText('更多操作'));
        fireEvent.click(screen.getByRole('menuitem', { name: /删除/ }));

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/保存失败/));
        expect(onDelete).toHaveBeenCalledTimes(1);
        expect(screen.getByRole('menu')).toBeInTheDocument();
        expect(screen.getByText('Project a')).toBeInTheDocument();
    });

    it('awaits card deletion and blocks duplicate delete clicks while pending', async () => {
        const pending = deferred<void>();
        const onDelete = vi.fn().mockReturnValue(pending.promise);
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        renderWithIntl(<ProjectCard project={project('a')} onDelete={onDelete} />);

        fireEvent.click(screen.getByLabelText('更多操作'));
        const deleteButton = screen.getByRole('menuitem', { name: /删除/ });
        fireEvent.click(deleteButton);
        fireEvent.click(deleteButton);

        expect(onDelete).toHaveBeenCalledTimes(1);
        expect(deleteButton).toBeDisabled();
        await act(async () => pending.resolve(undefined));
        await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
    });

    it('downloads a project package once and releases the object URL', async () => {
        const blob = new Blob(['zip'], { type: 'application/zip' });
        vi.mocked(api.exportProjectPackage).mockResolvedValue({ blob, filename: 'server-name.zip', cacheControl: 'no-store' });
        const createObjectURL = vi.fn().mockReturnValue('blob:package');
        const revokeObjectURL = vi.fn();
        vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
        renderWithIntl(<ProjectCard project={{ ...project('a'), title: 'My Project: One' }} onDelete={vi.fn()} />);

        fireEvent.click(screen.getByLabelText('更多操作'));
        const exportButton = screen.getByRole('menuitem', { name: /导出工程包/ });
        fireEvent.click(exportButton);
        fireEvent.click(exportButton);

        await waitFor(() => expect(api.exportProjectPackage).toHaveBeenCalledExactlyOnceWith('a'));
        expect(createObjectURL).toHaveBeenCalledWith(blob);
        expect(click).toHaveBeenCalledTimes(1);
        expect(revokeObjectURL).toHaveBeenCalledWith('blob:package');
    });

    it('does not download a package after the card unmounts before export returns', async () => {
        const pending = deferred<{ blob: Blob; filename: string; cacheControl: string }>();
        vi.mocked(api.exportProjectPackage).mockReturnValue(pending.promise);
        const createObjectURL = vi.fn().mockReturnValue('blob:package');
        const revokeObjectURL = vi.fn();
        vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
        const view = renderWithIntl(<ProjectCard project={project('a')} onDelete={vi.fn()} />);

        fireEvent.click(screen.getByLabelText('更多操作'));
        fireEvent.click(screen.getByRole('menuitem', { name: /导出工程包/ }));
        view.unmount();
        await act(async () => pending.resolve({ blob: new Blob(['zip']), filename: 'a.zip', cacheControl: 'no-store' }));

        expect(createObjectURL).not.toHaveBeenCalled();
        expect(click).not.toHaveBeenCalled();
        expect(revokeObjectURL).not.toHaveBeenCalled();
    });

    it('does not let a late export from a replaced project download on the new card', async () => {
        const pending = deferred<{ blob: Blob; filename: string; cacheControl: string }>();
        vi.mocked(api.exportProjectPackage).mockReturnValue(pending.promise);
        const createObjectURL = vi.fn().mockReturnValue('blob:package');
        vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL: vi.fn() });
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
        const view = renderWithIntl(<ProjectCard project={project('a')} onDelete={vi.fn()} />);

        fireEvent.click(screen.getByLabelText('更多操作'));
        fireEvent.click(screen.getByRole('menuitem', { name: /导出工程包/ }));
        view.rerender(<ProjectCard project={project('b')} onDelete={vi.fn()} />);
        await act(async () => pending.resolve({ blob: new Blob(['zip']), filename: 'a.zip', cacheControl: 'no-store' }));

        expect(createObjectURL).not.toHaveBeenCalled();
        expect(click).not.toHaveBeenCalled();
    });



    it('clears export pending when the card is reused for another project before export returns', async () => {
        const pending = deferred<{ blob: Blob; filename: string; cacheControl: string }>();
        vi.mocked(api.exportProjectPackage).mockReturnValue(pending.promise);
        const view = renderWithIntl(<ProjectCard project={project('a')} onDelete={vi.fn()} />);

        fireEvent.click(screen.getByLabelText('更多操作'));
        fireEvent.click(screen.getByRole('menuitem', { name: /导出工程包/ }));
        view.rerender(<ProjectCard project={project('b')} onDelete={vi.fn()} />);

        await waitFor(() => expect(screen.getByRole('menuitem', { name: /导出工程包/ })).not.toBeDisabled());
        await act(async () => pending.resolve({ blob: new Blob(['zip']), filename: 'a.zip', cacheControl: 'no-store' }));
    });

    it('does not let an old A export finally unlock a newer A export after A to B to A reuse', async () => {
        const pendingOldA = deferred<{ blob: Blob; filename: string; cacheControl: string }>();
        const pendingNewA = deferred<{ blob: Blob; filename: string; cacheControl: string }>();
        vi.mocked(api.exportProjectPackage)
            .mockReturnValueOnce(pendingOldA.promise)
            .mockReturnValueOnce(pendingNewA.promise);
        const view = renderWithIntl(<ProjectCard project={project('a')} onDelete={vi.fn()} />);

        fireEvent.click(screen.getByLabelText('更多操作'));
        fireEvent.click(screen.getByRole('menuitem', { name: /导出工程包/ }));
        view.rerender(<ProjectCard project={project('b')} onDelete={vi.fn()} />);
        view.rerender(<ProjectCard project={project('a')} onDelete={vi.fn()} />);
        await waitFor(() => expect(screen.getByRole('menuitem', { name: /导出工程包/ })).not.toBeDisabled());
        fireEvent.click(screen.getByRole('menuitem', { name: /导出工程包/ }));

        await act(async () => pendingOldA.resolve({ blob: new Blob(['old-a']), filename: 'old-a.zip', cacheControl: 'no-store' }));
        const stillPendingExport = await screen.findByRole('menuitem', { name: /导出中|Exporting/ });
        expect(stillPendingExport).toBeDisabled();
        expect(stillPendingExport).toHaveAttribute('aria-busy', 'true');
        fireEvent.click(stillPendingExport);

        expect(api.exportProjectPackage).toHaveBeenCalledTimes(2);
        await act(async () => pendingNewA.resolve({ blob: new Blob(['new-a']), filename: 'new-a.zip', cacheControl: 'no-store' }));
    });

    it('rejects an old A export result after A to B to A returns to the same project id', async () => {
        const pendingOldA = deferred<{ blob: Blob; filename: string; cacheControl: string }>();
        vi.mocked(api.exportProjectPackage).mockReturnValueOnce(pendingOldA.promise);
        const createObjectURL = vi.fn().mockReturnValue('blob:old-a');
        vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL: vi.fn() });
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
        const view = renderWithIntl(<ProjectCard project={project('a')} onDelete={vi.fn()} />);

        fireEvent.click(screen.getByLabelText('更多操作'));
        fireEvent.click(screen.getByRole('menuitem', { name: /导出工程包/ }));
        view.rerender(<ProjectCard project={project('b')} onDelete={vi.fn()} />);
        view.rerender(<ProjectCard project={project('a')} onDelete={vi.fn()} />);

        await act(async () => pendingOldA.resolve({ blob: new Blob(['old-a']), filename: 'old-a.zip', cacheControl: 'no-store' }));

        expect(createObjectURL).not.toHaveBeenCalled();
        expect(click).not.toHaveBeenCalled();
    });

    it('keeps the card menu open when package export fails', async () => {
        vi.mocked(api.exportProjectPackage).mockRejectedValue(new Error('offline'));
        renderWithIntl(<ProjectCard project={project('a')} onDelete={vi.fn()} />);

        fireEvent.click(screen.getByLabelText('更多操作'));
        fireEvent.click(screen.getByRole('menuitem', { name: /导出工程包/ }));

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/offline/));
        expect(screen.getByRole('menu')).toBeInTheDocument();
    });
});
