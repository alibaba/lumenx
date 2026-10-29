import { StrictMode } from 'react';
import { act, fireEvent, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CreateProjectDialog from '@/components/project/CreateProjectDialog';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred, project } from '../../modules/__tests__/frame-command-fixtures';

const originalCreateProject = useProjectStore.getState().createProject;

beforeEach(() => {
    vi.restoreAllMocks();
    window.location.hash = '';
    useProjectStore.setState({ createProject: originalCreateProject } as Partial<ReturnType<typeof useProjectStore.getState>>);
});

describe('CreateProjectDialog creation session boundary', () => {
    it('opens the returned project after StrictMode effect replay', async () => {
        const createProject = vi.fn().mockResolvedValue(project('created'));
        const onClose = vi.fn();
        useProjectStore.setState({ createProject });
        renderWithIntl(<StrictMode><CreateProjectDialog isOpen onClose={onClose} /></StrictMode>);
        fireEvent.change(screen.getByPlaceholderText('输入项目标题...'), { target: { value: 'Created' } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: '创建项目' })));
        expect(window.location.hash).toBe('#/project/created');
        expect(onClose).toHaveBeenCalledExactlyOnceWith();
    });

    it('does not navigate or close a reopened dialog from an obsolete create completion', async () => {
        const pending = deferred<ReturnType<typeof project>>();
        const createProject = vi.fn().mockReturnValue(pending.promise);
        const onClose = vi.fn();
        useProjectStore.setState({ createProject } as Partial<ReturnType<typeof useProjectStore.getState>>);

        const { rerender } = renderWithIntl(<CreateProjectDialog isOpen onClose={onClose} />);
        fireEvent.change(screen.getByPlaceholderText('输入项目标题...'), { target: { value: 'Created' } });
        fireEvent.click(screen.getByRole('button', { name: '创建项目' }));

        expect(createProject).toHaveBeenCalledTimes(1);
        rerender(<CreateProjectDialog isOpen={false} onClose={onClose} />);
        rerender(<CreateProjectDialog isOpen onClose={onClose} />);
        await act(async () => pending.resolve(project('created')));

        expect(window.location.hash).toBe('');
        expect(onClose).not.toHaveBeenCalled();
    });

    it('blocks duplicate create submissions while the first create is in flight', () => {
        const pending = deferred<ReturnType<typeof project>>();
        const createProject = vi.fn().mockReturnValue(pending.promise);
        useProjectStore.setState({ createProject } as Partial<ReturnType<typeof useProjectStore.getState>>);

        renderWithIntl(<CreateProjectDialog isOpen onClose={vi.fn()} />);
        fireEvent.change(screen.getByPlaceholderText('输入项目标题...'), { target: { value: 'Created' } });
        const create = screen.getByRole('button', { name: '创建项目' });
        fireEvent.click(create);
        fireEvent.click(create);

        expect(createProject).toHaveBeenCalledTimes(1);
    });
});
