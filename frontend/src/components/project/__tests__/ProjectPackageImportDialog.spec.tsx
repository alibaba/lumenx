import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ProjectPackageImportError } from '@/lib/api';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore } from '@/store/projectStore';
import ProjectPackageImportDialog from '../ProjectPackageImportDialog';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred } from '../../modules/__tests__/frame-command-fixtures';

vi.mock('@/lib/api', async importOriginal => {
    const actual = await importOriginal<typeof import('@/lib/api')>();
    return { ...actual, api: { ...actual.api, importProjectPackage: vi.fn(), getProject: vi.fn() } };
});

const STORAGE_A = '11111111-1111-4111-8111-111111111111';
const STORAGE_B = '22222222-2222-4222-8222-222222222222';
type ImportProjectPackageResult = Awaited<ReturnType<typeof api.importProjectPackage>>;
type GetProjectResult = Awaited<ReturnType<typeof api.getProject>>;

function wireProject(id = 'imported', revision = 5) {
    return {
        id,
        revision,
        title: 'Imported Project',
        original_text: 'copied script',
        frames: [],
        characters: [],
        scenes: [],
        props: [],
        video_tasks: [],
        created_at: 1,
        updated_at: 2,
        read_versions: { project: revision, series: null, library: 1 },
    };
}

function file(name = 'project.lumenx.zip', size = 4, type = 'application/zip') {
    return new File(['x'.repeat(size)], name, { type });
}

function importAck(value: unknown): ImportProjectPackageResult {
    return value as ImportProjectPackageResult;
}

function projectRead(value: unknown): GetProjectResult {
    return value as GetProjectResult;
}

function renderDialog(props: Partial<ComponentProps<typeof ProjectPackageImportDialog>> = {}) {
    return renderWithIntl(
        <ProjectPackageImportDialog
            isOpen
            onClose={vi.fn()}
            {...props}
        />,
    );
}

beforeEach(() => {
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
    useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {} });
    vi.clearAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    window.location.hash = '';
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('ProjectPackageImportDialog', () => {
    it('posts the zip once, waits for confirmed readback, then publishes and navigates', async () => {
        const ack = { ...wireProject('imported', 5), read_versions: undefined };
        vi.mocked(api.importProjectPackage).mockResolvedValueOnce(importAck(ack));
        vi.mocked(api.getProject).mockResolvedValueOnce(projectRead(wireProject('imported', 5)));
        const onClose = vi.fn();
        renderDialog({ onClose });

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file()] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));

        await waitFor(() => expect(window.location.hash).toBe('#/project/imported'));
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
        expect(api.getProject).toHaveBeenCalledExactlyOnceWith('imported');
        expect(useProjectStore.getState().projects.map(project => project.id)).toEqual(['imported']);
        expect(useProjectStore.getState().currentProject?.readEnvironment).toBeDefined();
        expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('keeps a read-only receipt when readback fails and retry does not POST again', async () => {
        const ack = { ...wireProject('imported', 5), read_versions: undefined };
        vi.mocked(api.importProjectPackage).mockResolvedValueOnce(importAck(ack));
        vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file()] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));

        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent(/同步|confirm|读取|read/i);
        expect(window.location.hash).toBe('');
        vi.mocked(api.getProject).mockResolvedValueOnce(projectRead(wireProject('imported', 5)));

        await act(async () => fireEvent.click(within(alert).getByRole('button', { name: /重试|Retry|重新读取/ })));

        await waitFor(() => expect(window.location.hash).toBe('#/project/imported'));
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
        expect(api.getProject).toHaveBeenCalledTimes(2);
    });


    it('keeps the acknowledged receipt after repeated readback failures and the third retry only reads', async () => {
        const ack = { ...wireProject('imported', 5), read_versions: undefined };
        vi.mocked(api.importProjectPackage).mockResolvedValueOnce(importAck(ack));
        vi.mocked(api.getProject)
            .mockRejectedValueOnce(new Error('offline first'))
            .mockRejectedValueOnce(new Error('offline second'))
            .mockResolvedValueOnce(projectRead(wireProject('imported', 5)));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('first.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        let alert = await screen.findByRole('alert');

        await act(async () => fireEvent.click(within(alert).getByRole('button', { name: /重试|Retry|重新读取/ })));
        alert = await screen.findByRole('alert');
        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('second.lumenx.zip')] } });
        expect(screen.getByRole('button', { name: /导入工程包|Import Package/ })).toBeDisabled();

        await act(async () => fireEvent.click(within(alert).getByRole('button', { name: /重试|Retry|重新读取/ })));

        await waitFor(() => expect(window.location.hash).toBe('#/project/imported'));
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
        expect(api.getProject).toHaveBeenCalledTimes(3);
    });

    it('coalesces same-render readback retry double clicks into one GET', async () => {
        const ack = { ...wireProject('imported', 5), read_versions: undefined };
        const retry = deferred<GetProjectResult>();
        vi.mocked(api.importProjectPackage).mockResolvedValueOnce(importAck(ack));
        vi.mocked(api.getProject)
            .mockRejectedValueOnce(new Error('offline'))
            .mockReturnValueOnce(retry.promise);
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file()] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        const retryButton = within(await screen.findByRole('alert')).getByRole('button', { name: /重试|Retry|重新读取/ });

        fireEvent.click(retryButton);
        fireEvent.click(retryButton);

        expect(api.getProject).toHaveBeenCalledTimes(2);
        await act(async () => retry.resolve(projectRead(wireProject('imported', 5))));
        await waitFor(() => expect(window.location.hash).toBe('#/project/imported'));
    });

    it('keeps an acknowledged receipt locked when selecting another file after readback fails', async () => {
        const ack = { ...wireProject('imported', 5), read_versions: undefined };
        vi.mocked(api.importProjectPackage).mockResolvedValueOnce(importAck(ack));
        vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('first.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        const alert = await screen.findByRole('alert');

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('second.lumenx.zip')] } });

        expect(screen.getByRole('alert')).toBe(alert);
        expect(screen.getByRole('button', { name: /导入工程包|Import Package/ })).toBeDisabled();
        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
    });

    it('treats an invalid acknowledgement after POST as unknown and blocks another POST in the same environment', async () => {
        vi.mocked(api.importProjectPackage).mockResolvedValueOnce(importAck({ id: '', revision: 1, title: 'Broken' }));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('first.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent(/核对|check|project list|项目列表/i);

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('second.lumenx.zip')] } });
        fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ }));

        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
        expect(api.getProject).not.toHaveBeenCalled();
    });

    it('does not navigate from an old session after the parent closes the dialog', async () => {
        const ack = { ...wireProject('imported', 5), read_versions: undefined };
        const readback = deferred<GetProjectResult>();
        vi.mocked(api.importProjectPackage).mockResolvedValueOnce(importAck(ack));
        vi.mocked(api.getProject).mockReturnValueOnce(readback.promise);
        const onClose = vi.fn();
        const { rerender } = renderDialog({ onClose });

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file()] } });
        fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ }));
        await waitFor(() => expect(api.getProject).toHaveBeenCalledExactlyOnceWith('imported'));
        rerender(<ProjectPackageImportDialog isOpen={false} onClose={onClose} />);

        await act(async () => readback.resolve(projectRead(wireProject('imported', 5))));

        expect(window.location.hash).toBe('');
        expect(onClose).not.toHaveBeenCalled();
        expect(useProjectStore.getState().projects).toEqual([]);
    });

    it('clears reopened busy UI after the old in-flight POST resolves without navigating', async () => {
        const pending = deferred<ImportProjectPackageResult>();
        const ack = { ...wireProject('imported', 5), read_versions: undefined };
        vi.mocked(api.importProjectPackage).mockReturnValueOnce(pending.promise);
        vi.mocked(api.getProject).mockResolvedValueOnce(projectRead(wireProject('imported', 5)));
        const { rerender } = renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file()] } });
        fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ }));
        rerender(<ProjectPackageImportDialog isOpen={false} onClose={vi.fn()} />);
        rerender(<ProjectPackageImportDialog isOpen onClose={vi.fn()} />);
        expect(screen.getByRole('button', { name: /Confirming project|确认新项目|Import Package|导入工程包/ })).toBeDisabled();

        await act(async () => pending.resolve(importAck(ack)));

        await waitFor(() => expect(screen.getByRole('button', { name: /导入工程包|Import Package/ })).toBeDisabled());
        expect(window.location.hash).toBe('');
    });

    it('blocks duplicate import submissions synchronously in the same render', async () => {
        const pending = deferred<ImportProjectPackageResult>();
        vi.mocked(api.importProjectPackage).mockReturnValueOnce(pending.promise);
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file()] } });
        const submit = screen.getByRole('button', { name: /导入工程包|Import Package/ });
        fireEvent.click(submit);
        fireEvent.click(submit);

        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
        await act(async () => pending.reject(new Error('network lost')));
    });


    it('keeps explicit server rejection correctable without locking the current environment', async () => {
        vi.mocked(api.importProjectPackage)
            .mockRejectedValueOnce(new Error('bad zip'))
            .mockResolvedValueOnce(importAck({ ...wireProject('imported', 5), read_versions: undefined }));
        vi.mocked(api.getProject).mockResolvedValueOnce(projectRead(wireProject('imported', 5)));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('bad.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        expect(await screen.findByRole('alert')).toHaveTextContent('bad zip');

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('fixed.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));

        await waitFor(() => expect(window.location.hash).toBe('#/project/imported'));
        expect(api.importProjectPackage).toHaveBeenCalledTimes(2);
    });

    it('locks duplicate import after a typed unknown network outcome', async () => {
        vi.mocked(api.importProjectPackage).mockRejectedValueOnce(new ProjectPackageImportError('unknown', true));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('unknown.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        expect(await screen.findByRole('alert')).toHaveTextContent(/核对|check|project list|项目列表/i);

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('second.lumenx.zip')] } });
        fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ }));

        expect(api.importProjectPackage).toHaveBeenCalledTimes(1);
    });

    it('rejects files over the local 256 MiB guard without posting', () => {
        renderDialog();
        const large = new File(['x'], 'huge.lumenx.zip', { type: 'application/zip' });
        Object.defineProperty(large, 'size', { value: 256 * 1024 * 1024 + 1 });

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [large] } });

        expect(screen.getByRole('alert')).toHaveTextContent(/256 MiB/);
        expect(screen.getByRole('button', { name: /导入工程包|Import Package/ })).toBeDisabled();
        expect(api.importProjectPackage).not.toHaveBeenCalled();
    });

    it('does not publish or navigate a late receipt after switching runtime environments', async () => {
        const pendingImport = deferred<ImportProjectPackageResult>();
        vi.mocked(api.importProjectPackage).mockReturnValueOnce(pendingImport.promise);
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file()] } });
        fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ }));
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
        });
        await act(async () => pendingImport.resolve(importAck({ ...wireProject('imported', 5), read_versions: undefined })));

        expect(api.getProject).not.toHaveBeenCalled();
        expect(window.location.hash).toBe('');
        expect(useProjectStore.getState().projects).toEqual([]);
    });

    it('rejects a readback older than the import acknowledgement before mutating the store', async () => {
        const ack = { ...wireProject('imported', 5), read_versions: undefined };
        vi.mocked(api.importProjectPackage).mockResolvedValueOnce(importAck(ack));
        vi.mocked(api.getProject).mockResolvedValueOnce(projectRead(wireProject('imported', 4)));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file()] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));

        await screen.findByRole('alert');
        expect(window.location.hash).toBe('');
        expect(useProjectStore.getState().projects).toEqual([]);
    });

    it('does not retry an old receipt after A to B to A runtime generation changes', async () => {
        const ack = { ...wireProject('imported', 5), read_versions: undefined };
        vi.mocked(api.importProjectPackage).mockResolvedValueOnce(importAck(ack));
        vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file()] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        const alert = await screen.findByRole('alert');
        expect(api.getProject).toHaveBeenCalledTimes(1);

        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
            activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
        });
        await act(async () => fireEvent.click(within(alert).getByRole('button', { name: /重试|Retry|重新读取/ })));

        expect(api.getProject).toHaveBeenCalledTimes(1);
        expect(window.location.hash).toBe('');
    });


    it('does not reassign an environment A receipt to environment B when B import is rejected', async () => {
        const ackA = { ...wireProject('imported-a', 5), read_versions: undefined };
        vi.mocked(api.importProjectPackage)
            .mockResolvedValueOnce(importAck(ackA))
            .mockRejectedValueOnce(new Error('bad zip in b'));
        vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline a'));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('a.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        expect(await screen.findByRole('alert')).toHaveTextContent(/同步|confirm|读取|read/i);

        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
        });
        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('b-bad.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));

        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent('bad zip in b');
        expect(within(alert).queryByRole('button', { name: /重试|Retry|重新读取/ })).not.toBeInTheDocument();
        expect(api.importProjectPackage).toHaveBeenCalledTimes(2);
    });

    it('does not let an environment A unknown submission block a valid environment B import', async () => {
        const ackB = { ...wireProject('imported-b', 6), read_versions: undefined };
        vi.mocked(api.importProjectPackage)
            .mockRejectedValueOnce(new ProjectPackageImportError('unknown a', true))
            .mockResolvedValueOnce(importAck(ackB));
        vi.mocked(api.getProject).mockResolvedValueOnce(projectRead(wireProject('imported-b', 6)));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('a.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        expect(await screen.findByRole('alert')).toHaveTextContent(/核对|check|项目列表|project list/i);

        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
        });
        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('b.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));

        await waitFor(() => expect(window.location.hash).toBe('#/project/imported-b'));
        expect(api.importProjectPackage).toHaveBeenCalledTimes(2);
        expect(useProjectStore.getState().projects.map(project => project.id)).toEqual(['imported-b']);
    });

    it('resets the local session on runtime switch so a new environment can import before the old response returns', async () => {
        const pendingA = deferred<ImportProjectPackageResult>();
        const ackB = { ...wireProject('imported-b', 6), read_versions: undefined };
        vi.mocked(api.importProjectPackage)
            .mockReturnValueOnce(pendingA.promise)
            .mockResolvedValueOnce(importAck(ackB));
        vi.mocked(api.getProject).mockResolvedValueOnce(projectRead(wireProject('imported-b', 6)));
        renderDialog();

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('a.lumenx.zip')] } });
        fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ }));
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
        });

        fireEvent.change(screen.getByLabelText(/选择工程包|Choose project package/), { target: { files: [file('b.lumenx.zip')] } });
        await act(async () => fireEvent.click(screen.getByRole('button', { name: /导入工程包|Import Package/ })));
        await waitFor(() => expect(window.location.hash).toBe('#/project/imported-b'));

        await act(async () => pendingA.resolve(importAck({ ...wireProject('imported-a', 5), read_versions: undefined })));

        expect(api.importProjectPackage).toHaveBeenCalledTimes(2);
        expect(api.getProject).toHaveBeenCalledExactlyOnceWith('imported-b');
        expect(useProjectStore.getState().projects.map(project => project.id)).toEqual(['imported-b']);
    });
});
