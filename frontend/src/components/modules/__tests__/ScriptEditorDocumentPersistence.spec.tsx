import { useEffect } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import type { Editor } from '@tiptap/react';
import { scriptEditorApi } from '@/lib/scriptEditorApi';
import { useEditorStore } from '@/store/editorStore';
import { useAutoSave } from '../ScriptEditor/hooks/useAutoSave';
import SnapshotListDialog from '../ScriptEditor/dialogs/SnapshotListDialog';

vi.mock('@/lib/scriptEditorApi', () => ({
    scriptEditorApi: {
        loadDocument: vi.fn(),
        saveDocument: vi.fn(),
        listSnapshots: vi.fn(),
        restoreSnapshot: vi.fn(),
    },
}));

function doc(text: string) {
    return { type: 'doc', content: [{ type: 'action', content: [{ type: 'text', text }] }] };
}

function envelope(projectId: string, revision: number, text: string) {
    return { project_id: projectId, revision, updated_at: `2026-10-10T00:00:0${revision}.000000Z`, content: doc(text) };
}

function snapshot(projectId: string, timestamp: string, revision: number, createdAt = '2026-10-10T00:00:00Z') {
    return { project_id: projectId, snapshot_id: timestamp, timestamp, created_at: createdAt, revision, size_bytes: 12 };
}

async function expectRevision(value: string | number) {
    await waitFor(() => expect(screen.getByTestId('revision')).toHaveTextContent(String(value)));
}

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}

function fakeEditor(initial = doc('initial')) {
    let content = initial;
    return {
        isDestroyed: false,
        getJSON: vi.fn(() => content),
        getText: vi.fn(() => JSON.stringify(content)),
        setEditable: vi.fn(),
        setContentForTest(next: ReturnType<typeof doc>) { content = next; },
        commands: {
            setContent: vi.fn((next: ReturnType<typeof doc>) => {
                content = next;
                return true;
            }),
        },
    } as unknown as Editor & { setContentForTest(next: ReturnType<typeof doc>): void; commands: { setContent: ReturnType<typeof vi.fn> } };
}

function AutoSaveHarness({ editor, projectId }: { editor: Editor; projectId: string | null }) {
    const state = useAutoSave(editor, projectId);
    return (
        <div>
            <button type="button" onClick={() => state.save(false)}>save</button>
            <button type="button" onClick={() => state.save(true)}>snapshot</button>
            <button type="button" onClick={() => state.save(false, { force: false })}>autosave</button>
            <span data-testid="revision">{state.documentRevision ?? 'none'}</span>
            <span data-testid="load-status">{state.loadStatus}</span>
            <span data-testid="save-status">{state.saveStatus}</span>
            {state.errorMessage && <div role="alert">{state.errorMessage}</div>}
        </div>
    );
}

function RestoreCallbackHarness({
    editor,
    projectId,
    onReady,
}: {
    editor: Editor;
    projectId: string;
    onReady: (projectId: string, restore: (raw: ReturnType<typeof envelope>) => boolean | void) => void;
}) {
    const state = useAutoSave(editor, projectId);
    useEffect(() => {
        if (state.documentRevision !== null) {
            onReady(projectId, state.adoptRestoredEnvelope);
        }
    }, [projectId, state.documentRevision, state.adoptRestoredEnvelope, onReady]);
    return (
        <div>
            <span data-testid="revision">{state.documentRevision ?? 'none'}</span>
            <span data-testid="save-status">{state.saveStatus}</span>
            {state.errorMessage && <div role="alert">{state.errorMessage}</div>}
        </div>
    );
}

describe('ScriptEditor document persistence', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.mocked(scriptEditorApi.loadDocument).mockReset();
        vi.mocked(scriptEditorApi.saveDocument).mockReset();
        vi.mocked(scriptEditorApi.listSnapshots).mockReset();
        vi.mocked(scriptEditorApi.restoreSnapshot).mockReset();
        useEditorStore.setState({ isDirty: false, lastSavedAt: null });
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('loads the embedded project envelope and saves with the accepted revision', async () => {
        const editor = fakeEditor();
        vi.mocked(scriptEditorApi.loadDocument).mockResolvedValueOnce(envelope('p', 3, 'server'));
        vi.mocked(scriptEditorApi.saveDocument).mockResolvedValueOnce(envelope('p', 4, 'server'));

        renderWithIntl(<AutoSaveHarness editor={editor} projectId="p" />);

        await expectRevision('3');
        expect(editor.commands.setContent).toHaveBeenCalledWith(doc('server'), expect.objectContaining({ emitUpdate: false }));
        fireEvent.click(screen.getByRole('button', { name: 'save' }));

        await waitFor(() => expect(scriptEditorApi.saveDocument).toHaveBeenCalledWith('p', doc('server'), {
            expectedRevision: 3,
            createSnapshot: false,
        }));
        await expectRevision('4');
        expect(useEditorStore.getState().isDirty).toBe(false);
    });

    it('never calls project document APIs for the full local editor without a project id', async () => {
        const editor = fakeEditor();
        renderWithIntl(<AutoSaveHarness editor={editor} projectId={null} />);
        fireEvent.click(screen.getByRole('button', { name: 'save' }));
        await Promise.resolve();
        expect(scriptEditorApi.loadDocument).not.toHaveBeenCalled();
        expect(scriptEditorApi.saveDocument).not.toHaveBeenCalled();
        expect(screen.getByTestId('revision')).toHaveTextContent('none');
    });

    it('does not overwrite a dirty local draft when the initial read resolves late', async () => {
        const editor = fakeEditor();
        const read = deferred<ReturnType<typeof envelope>>();
        vi.mocked(scriptEditorApi.loadDocument).mockReturnValueOnce(read.promise);

        renderWithIntl(<AutoSaveHarness editor={editor} projectId="p" />);
        act(() => {
            editor.setContentForTest(doc('local draft'));
            useEditorStore.setState({ isDirty: true });
        });
        await act(async () => read.resolve(envelope('p', 1, 'remote')));

        expect(editor.commands.setContent).not.toHaveBeenCalled();
        expect(editor.getJSON()).toEqual(doc('local draft'));
        expect(useEditorStore.getState().isDirty).toBe(true);
        vi.mocked(scriptEditorApi.saveDocument).mockResolvedValueOnce(envelope('p', 2, 'local draft'));
        fireEvent.click(screen.getByRole('button', { name: 'save' }));
        await waitFor(() => expect(scriptEditorApi.saveDocument).toHaveBeenCalledWith('p', doc('local draft'), {
            expectedRevision: 1,
            createSnapshot: false,
        }));
    });

    it('ignores a stale load after switching to another project', async () => {
        const editor = fakeEditor();
        const first = deferred<ReturnType<typeof envelope>>();
        vi.mocked(scriptEditorApi.loadDocument)
            .mockReturnValueOnce(first.promise)
            .mockResolvedValueOnce(envelope('b', 2, 'project b'));

        const view = renderWithIntl(<AutoSaveHarness editor={editor} projectId="a" />);
        view.rerender(<AutoSaveHarness editor={editor} projectId="b" />);
        await expectRevision('2');
        await act(async () => first.resolve(envelope('a', 1, 'project a late')));

        expect(editor.getJSON()).toEqual(doc('project b'));
        expect(editor.commands.setContent).toHaveBeenCalledTimes(1);
    });

    it('keeps a newer edit dirty and queues it after an older save ACK returns', async () => {
        const editor = fakeEditor();
        const firstSave = deferred<ReturnType<typeof envelope>>();
        vi.mocked(scriptEditorApi.loadDocument).mockResolvedValueOnce(envelope('p', 5, 'base'));
        vi.mocked(scriptEditorApi.saveDocument)
            .mockReturnValueOnce(firstSave.promise)
            .mockResolvedValueOnce(envelope('p', 7, 'newer'));

        renderWithIntl(<AutoSaveHarness editor={editor} projectId="p" />);
        await expectRevision('5');
        act(() => {
            editor.setContentForTest(doc('first'));
            useEditorStore.setState({ isDirty: true });
        });
        fireEvent.click(screen.getByRole('button', { name: 'save' }));
        act(() => {
            editor.setContentForTest(doc('newer'));
            useEditorStore.setState({ isDirty: true });
        });
        await act(async () => firstSave.resolve(envelope('p', 6, 'first')));

        await waitFor(() => expect(scriptEditorApi.saveDocument).toHaveBeenCalledTimes(2));
        expect(scriptEditorApi.saveDocument).toHaveBeenLastCalledWith('p', doc('newer'), {
            expectedRevision: 6,
            createSnapshot: false,
        });
        await waitFor(() => expect(useEditorStore.getState().isDirty).toBe(false));
    });

    it('preserves the draft and reports conflict on stale save', async () => {
        const editor = fakeEditor();
        vi.mocked(scriptEditorApi.loadDocument).mockResolvedValueOnce(envelope('p', 5, 'base'));
        vi.mocked(scriptEditorApi.saveDocument).mockRejectedValueOnce({ response: { status: 409 } });

        renderWithIntl(<AutoSaveHarness editor={editor} projectId="p" />);
        await expectRevision('5');
        act(() => {
            editor.setContentForTest(doc('draft'));
            useEditorStore.setState({ isDirty: true });
        });
        fireEvent.click(screen.getByRole('button', { name: 'save' }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/conflict|冲突|changed|已变更/i);
        expect(editor.getJSON()).toEqual(doc('draft'));
        expect(useEditorStore.getState().isDirty).toBe(true);
    });


    it('does not repeat autosave after a conflict until the user acts', async () => {
        const editor = fakeEditor();
        vi.mocked(scriptEditorApi.loadDocument).mockResolvedValueOnce(envelope('p', 5, 'base'));
        vi.mocked(scriptEditorApi.saveDocument).mockRejectedValueOnce({ response: { status: 409 } });

        renderWithIntl(<AutoSaveHarness editor={editor} projectId="p" />);
        await screen.findByTestId('revision');
        act(() => {
            editor.setContentForTest(doc('draft'));
            useEditorStore.setState({ isDirty: true });
        });
        fireEvent.click(screen.getByRole('button', { name: 'save' }));
        await screen.findByRole('alert');
        fireEvent.click(screen.getByRole('button', { name: 'autosave' }));
        await Promise.resolve();
        expect(scriptEditorApi.saveDocument).toHaveBeenCalledTimes(1);
    });

    it('rejects ambiguous save ACKs without marking the draft saved', async () => {
        const editor = fakeEditor();
        vi.mocked(scriptEditorApi.loadDocument).mockResolvedValueOnce(envelope('ack', 2, 'base'));
        vi.mocked(scriptEditorApi.saveDocument).mockResolvedValueOnce(envelope('ack', 1, 'different'));

        renderWithIntl(<AutoSaveHarness editor={editor} projectId="ack" />);
        await expectRevision('2');
        act(() => {
            editor.setContentForTest(doc('draft'));
            useEditorStore.setState({ isDirty: true });
        });
        fireEvent.click(screen.getByRole('button', { name: 'save' }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/invalid|failed|无效|失败/i);
        expect(useEditorStore.getState().isDirty).toBe(true);
    });

    it('does not let a save ACK mutate global dirty state after unmount', async () => {
        const editor = fakeEditor();
        const save = deferred<ReturnType<typeof envelope>>();
        vi.mocked(scriptEditorApi.loadDocument).mockResolvedValueOnce(envelope('p-unmount', 1, 'base'));
        vi.mocked(scriptEditorApi.saveDocument).mockReturnValueOnce(save.promise);

        const view = renderWithIntl(<AutoSaveHarness editor={editor} projectId="p-unmount" />);
        await expectRevision('1');
        act(() => {
            editor.setContentForTest(doc('draft'));
            useEditorStore.setState({ isDirty: true });
        });
        fireEvent.click(screen.getByRole('button', { name: 'save' }));
        view.unmount();
        act(() => useEditorStore.setState({ isDirty: true }));
        await act(async () => save.resolve(envelope('p-unmount', 2, 'draft')));
        expect(useEditorStore.getState().isDirty).toBe(true);
    });


    it('drops an in-flight save when switching projects and allows the new project to save', async () => {
        const editor = fakeEditor();
        const oldSave = deferred<ReturnType<typeof envelope>>();
        oldSave.promise.catch(() => undefined);
        vi.mocked(scriptEditorApi.loadDocument)
            .mockResolvedValueOnce(envelope('a', 1, 'project a'))
            .mockResolvedValueOnce(envelope('b', 4, 'project b'));
        vi.mocked(scriptEditorApi.saveDocument)
            .mockReturnValueOnce(oldSave.promise)
            .mockResolvedValueOnce(envelope('b', 5, 'project b edit'));

        const view = renderWithIntl(<AutoSaveHarness editor={editor} projectId="a" />);
        await expectRevision('1');
        act(() => {
            editor.setContentForTest(doc('project a edit'));
            useEditorStore.setState({ isDirty: true });
        });
        fireEvent.click(screen.getByRole('button', { name: 'save' }));

        view.rerender(<AutoSaveHarness editor={editor} projectId="b" />);
        await expectRevision('4');
        await act(async () => oldSave.resolve(envelope('a', 2, 'project a edit')));
        act(() => {
            editor.setContentForTest(doc('project b edit'));
            useEditorStore.setState({ isDirty: true });
        });
        fireEvent.click(screen.getByRole('button', { name: 'save' }));

        await waitFor(() => expect(scriptEditorApi.saveDocument).toHaveBeenCalledTimes(2));
        expect(scriptEditorApi.saveDocument).toHaveBeenLastCalledWith('b', doc('project b edit'), {
            expectedRevision: 4,
            createSnapshot: false,
        });
    });



    it('does not store project A draft under project B during an A-to-B transition', async () => {
        const editor = fakeEditor();
        const oldSave = deferred<ReturnType<typeof envelope>>();
        oldSave.promise.catch(() => undefined);
        vi.mocked(scriptEditorApi.loadDocument)
            .mockResolvedValueOnce(envelope('a-contam', 1, 'project a server'))
            .mockResolvedValueOnce(envelope('b-contam', 4, 'project b server'))
            .mockResolvedValueOnce(envelope('a-contam', 1, 'project a same revision server'));
        vi.mocked(scriptEditorApi.saveDocument).mockReturnValueOnce(oldSave.promise);

        const view = renderWithIntl(<AutoSaveHarness editor={editor} projectId="a-contam" />);
        await expectRevision('1');
        act(() => {
            editor.setContentForTest(doc('draft belongs to a'));
            useEditorStore.setState({ isDirty: true });
        });
        fireEvent.click(screen.getByRole('button', { name: 'save' }));

        view.rerender(<AutoSaveHarness editor={editor} projectId="b-contam" />);
        await expectRevision('4');
        expect(editor.getJSON()).toEqual(doc('project b server'));
        expect(useEditorStore.getState().isDirty).toBe(false);
        await act(async () => oldSave.reject(new Error('a save failed late')));
        expect(editor.getJSON()).toEqual(doc('project b server'));

        view.rerender(<AutoSaveHarness editor={editor} projectId="a-contam" />);
        await expectRevision('1');
        expect(editor.getJSON()).toEqual(doc('draft belongs to a'));
        expect(useEditorStore.getState().isDirty).toBe(true);
    });

    it('keeps a failed old project draft recoverable after switching away and back', async () => {
        const editor = fakeEditor();
        const oldSave = deferred<ReturnType<typeof envelope>>();
        oldSave.promise.catch(() => undefined);
        vi.mocked(scriptEditorApi.loadDocument)
            .mockResolvedValueOnce(envelope('a-recover', 1, 'project a'))
            .mockResolvedValueOnce(envelope('b-recover', 4, 'project b'))
            .mockResolvedValueOnce(envelope('a-recover', 1, 'project a remote'));
        vi.mocked(scriptEditorApi.saveDocument).mockReturnValueOnce(oldSave.promise);

        const view = renderWithIntl(<AutoSaveHarness editor={editor} projectId="a-recover" />);
        await expectRevision('1');
        act(() => {
            editor.setContentForTest(doc('unsaved a'));
            useEditorStore.setState({ isDirty: true });
        });
        fireEvent.click(screen.getByRole('button', { name: 'save' }));
        view.rerender(<AutoSaveHarness editor={editor} projectId="b-recover" />);
        await expectRevision('4');
        await act(async () => oldSave.reject(new Error('disk failed')));
        view.rerender(<AutoSaveHarness editor={editor} projectId="a-recover" />);
        await expectRevision('1');
        expect(editor.getJSON()).toEqual(doc('unsaved a'));
        expect(useEditorStore.getState().isDirty).toBe(true);
    });

    it('does not autosave a recovered draft over a newer remote revision', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        const editor = fakeEditor();
        vi.mocked(scriptEditorApi.loadDocument)
            .mockResolvedValueOnce(envelope('a-drift', 1, 'project a base'))
            .mockResolvedValueOnce(envelope('b-drift', 4, 'project b base'))
            .mockResolvedValueOnce(envelope('a-drift', 2, 'project a changed remotely'));
        vi.mocked(scriptEditorApi.saveDocument).mockRejectedValueOnce({ response: { status: 409 } });

        const view = renderWithIntl(<AutoSaveHarness editor={editor} projectId="a-drift" />);
        await expectRevision('1');
        act(() => {
            editor.setContentForTest(doc('project a local draft'));
            useEditorStore.setState({ isDirty: true });
        });

        view.rerender(<AutoSaveHarness editor={editor} projectId="b-drift" />);
        await expectRevision('4');
        expect(editor.getJSON()).toEqual(doc('project b base'));

        view.rerender(<AutoSaveHarness editor={editor} projectId="a-drift" />);
        expect(await screen.findByTestId('load-status')).toHaveTextContent('recovered-draft');
        expect(screen.getByTestId('revision')).toHaveTextContent('1');
        expect(screen.getByTestId('save-status')).toHaveTextContent('conflict');
        expect(editor.getJSON()).toEqual(doc('project a local draft'));
        expect(await screen.findByRole('alert')).toHaveTextContent(/conflict|冲突|changed|已变更/i);

        await act(async () => {
            vi.advanceTimersByTime(30_000);
        });
        expect(scriptEditorApi.saveDocument).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole('button', { name: 'save' }));
        await waitFor(() => expect(scriptEditorApi.saveDocument).toHaveBeenCalledWith('a-drift', doc('project a local draft'), {
            expectedRevision: 1,
            createSnapshot: false,
        }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/conflict|冲突|changed|已变更/i);
        expect(useEditorStore.getState().isDirty).toBe(true);
    });


    it('ignores a stale snapshot restore callback after switching projects', async () => {
        const editor = fakeEditor();
        const restoreByProject = new Map<string, (raw: ReturnType<typeof envelope>) => boolean | void>();
        const captureRestore = vi.fn((id: string, restore: (raw: ReturnType<typeof envelope>) => boolean | void) => {
            restoreByProject.set(id, restore);
        });
        vi.mocked(scriptEditorApi.loadDocument)
            .mockResolvedValueOnce(envelope('restore-a', 1, 'project a'))
            .mockResolvedValueOnce(envelope('restore-b', 4, 'project b'));

        const view = renderWithIntl(<RestoreCallbackHarness editor={editor} projectId="restore-a" onReady={captureRestore} />);
        await expectRevision('1');
        const staleRestoreA = restoreByProject.get('restore-a');
        expect(staleRestoreA).toBeDefined();

        view.rerender(<RestoreCallbackHarness editor={editor} projectId="restore-b" onReady={captureRestore} />);
        await expectRevision('4');
        act(() => {
            editor.setContentForTest(doc('project b draft'));
            useEditorStore.setState({ isDirty: true });
        });

        let accepted: boolean | void = undefined;
        act(() => {
            accepted = staleRestoreA?.(envelope('restore-a', 2, 'restored a'));
        });

        expect(accepted).toBe(false);
        expect(editor.getJSON()).toEqual(doc('project b draft'));
        expect(useEditorStore.getState().isDirty).toBe(true);
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.getByTestId('save-status')).toHaveTextContent('idle');
    });


    it('ignores a stale snapshot list after switching dialog projects', async () => {
        const staleList = deferred<ReturnType<typeof snapshot>[]>();
        vi.mocked(scriptEditorApi.listSnapshots)
            .mockReturnValueOnce(staleList.promise)
            .mockResolvedValueOnce([snapshot('snap-b', 'snap-b', 5, '2026-10-10T00:00:09Z')]);
        vi.mocked(scriptEditorApi.restoreSnapshot).mockResolvedValueOnce(envelope('snap-b', 6, 'restored b'));
        const onRestore = vi.fn(() => true);

        const view = renderWithIntl(
            <SnapshotListDialog open projectId="snap-a" currentRevision={1} onRestore={onRestore} onClose={vi.fn()} />,
        );
        view.rerender(
            <SnapshotListDialog open projectId="snap-b" currentRevision={5} onRestore={onRestore} onClose={vi.fn()} />,
        );
        await screen.findByRole('button', { name: /恢复|Restore/i });
        await act(async () => staleList.resolve([snapshot('snap-a', 'snap-a', 2, '2026-10-10T00:00:08Z')]));

        fireEvent.click(screen.getByRole('button', { name: /恢复|Restore/i }));
        fireEvent.click(await screen.findByRole('button', { name: /确认|Confirm/i }));

        await waitFor(() => expect(scriptEditorApi.restoreSnapshot).toHaveBeenCalledWith('snap-b', 'snap-b', { expectedRevision: 5 }));
    });

    it('resets pending restore state when the dialog changes project and reopens', async () => {
        const pending = deferred<ReturnType<typeof envelope>>();
        vi.mocked(scriptEditorApi.listSnapshots)
            .mockResolvedValue([snapshot('scope', 'scope-snapshot', 1)]);
        vi.mocked(scriptEditorApi.restoreSnapshot).mockReturnValueOnce(pending.promise);
        const onRestore = vi.fn();
        const onClose = vi.fn();
        const view = renderWithIntl(
            <SnapshotListDialog open projectId="scope-a" currentRevision={1} onRestore={onRestore} onClose={onClose} />,
        );
        fireEvent.click(await screen.findByRole('button', { name: /恢复|Restore/i }));
        fireEvent.click(await screen.findByRole('button', { name: /确认|Confirm/i }));
        await waitFor(() => expect(scriptEditorApi.restoreSnapshot).toHaveBeenCalledTimes(1));

        view.rerender(<SnapshotListDialog open projectId="scope-b" currentRevision={1} onRestore={onRestore} onClose={onClose} />);
        fireEvent.click(await screen.findByRole('button', { name: /^恢复$|^Restore$/i }));
        expect(await screen.findByRole('button', { name: /确认|Confirm/i })).toBeEnabled();
        view.rerender(<SnapshotListDialog open={false} projectId="scope-a" currentRevision={1} onRestore={onRestore} onClose={onClose} />);
        view.rerender(<SnapshotListDialog open projectId="scope-a" currentRevision={1} onRestore={onRestore} onClose={onClose} />);
        await screen.findByRole('button', { name: /^恢复$|^Restore$/i });
        await act(async () => pending.resolve(envelope('scope-a', 2, 'stale restore')));
        expect(onRestore).not.toHaveBeenCalled();
        expect(onClose).not.toHaveBeenCalled();
    });

    it('does not show old project snapshots after the new project list fails', async () => {
        vi.mocked(scriptEditorApi.listSnapshots)
            .mockResolvedValueOnce([snapshot('old', 'old-snapshot', 1)])
            .mockRejectedValueOnce(new Error('new project list failed'));
        const view = renderWithIntl(<SnapshotListDialog open projectId="old" currentRevision={1} onRestore={vi.fn()} onClose={vi.fn()} />);
        await screen.findByRole('button', { name: /^恢复$|^Restore$/i });
        view.rerender(<SnapshotListDialog open projectId="new" currentRevision={1} onRestore={vi.fn()} onClose={vi.fn()} />);
        await screen.findByRole('alert');
        expect(screen.queryByRole('button', { name: /^恢复$|^Restore$/i })).not.toBeInTheDocument();
    });

    it('shows snapshot list and restore failures instead of silently logging them', async () => {
        vi.mocked(scriptEditorApi.listSnapshots).mockRejectedValueOnce(new Error('list failed'));
        const view = renderWithIntl(
            <SnapshotListDialog open projectId="snap-error" currentRevision={1} onRestore={vi.fn()} onClose={vi.fn()} />,
        );
        expect(await screen.findByRole('alert')).toHaveTextContent(/读取已保存文档失败|Failed to load/i);

        vi.mocked(scriptEditorApi.listSnapshots).mockResolvedValueOnce([
            snapshot('snap-error', 'snap-error-1', 1, '2026-10-10T00:00:09Z'),
        ]);
        vi.mocked(scriptEditorApi.restoreSnapshot).mockRejectedValueOnce(new Error('restore failed'));
        view.rerender(
            <SnapshotListDialog open projectId="snap-error-2" currentRevision={1} onRestore={vi.fn()} onClose={vi.fn()} />,
        );
        fireEvent.click(await screen.findByRole('button', { name: /恢复|Restore/i }));
        fireEvent.click(await screen.findByRole('button', { name: /确认|Confirm/i }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/恢复版本回执无效|Invalid snapshot/i);
    });

    it('restores a snapshot with expected revision and adopts the returned envelope', async () => {
        vi.mocked(scriptEditorApi.listSnapshots).mockResolvedValueOnce([
            { project_id: 'p', snapshot_id: 'snap-1', timestamp: 'snap-1', created_at: '2026-10-10T00:00:00Z', revision: 4, size_bytes: 12 },
        ]);
        vi.mocked(scriptEditorApi.restoreSnapshot).mockResolvedValueOnce(envelope('p', 6, 'restored'));
        const onRestore = vi.fn();

        renderWithIntl(
            <SnapshotListDialog open projectId="p" currentRevision={5} onRestore={onRestore} onClose={vi.fn()} />,
        );

        fireEvent.click(await screen.findByRole('button', { name: /恢复|Restore/i }));
        fireEvent.click(await screen.findByRole('button', { name: /确认|Confirm/i }));

        await waitFor(() => expect(scriptEditorApi.restoreSnapshot).toHaveBeenCalledWith('p', 'snap-1', { expectedRevision: 5 }));
        expect(onRestore).toHaveBeenCalledWith(envelope('p', 6, 'restored'));
    });
});
