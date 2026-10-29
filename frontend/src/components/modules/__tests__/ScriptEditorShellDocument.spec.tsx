import React from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { scriptEditorApi } from '@/lib/scriptEditorApi';
import { useEditorStore } from '@/store/editorStore';
import ScriptEditorShell from '../ScriptEditor/ScriptEditorShell';

const shellEditorRef = vi.hoisted(() => ({ current: null as unknown as ReturnType<typeof fakeEditor> }));

vi.mock('@tiptap/react', () => ({
    EditorContent: ({ editor }: { editor: { getJSON: () => unknown } | null }) => <div data-testid="editor-content">{JSON.stringify(editor?.getJSON())}</div>,
}));

vi.mock('../ScriptEditor/hooks/useEditorSetup', () => ({
    useEditorSetup: () => ({ editor: shellEditorRef.current, isReady: true }),
}));
vi.mock('../ScriptEditor/toolbar/FormatToolbar', () => ({ default: () => null }));
vi.mock('../ScriptEditor/hooks/usePasteHandler', () => ({ usePasteHandler: () => ({ showHint: false, analysis: null, applyFormatting: vi.fn(), dismissHint: vi.fn() }) }));
vi.mock('../ScriptEditor/hooks/useKeyboardShortcuts', () => ({ useKeyboardShortcuts: () => ({ showShortcutHelp: false, closeShortcutHelp: vi.fn() }) }));
vi.mock('../ScriptEditor/hooks/useContinuityCheck', () => ({ useContinuityCheck: () => ({}) }));
vi.mock('../ScriptEditor/hooks/useSceneFolding', () => ({ useSceneFolding: () => ({ enabled: false, isAllExpanded: true, totalScenes: 0 }) }));
vi.mock('../ScriptEditor/hooks/useViewMode', () => ({ useViewMode: () => ({ mode: 'edit', setMode: vi.fn(), isReadOnly: false, showToolbar: true, showSidebars: true }) }));
vi.mock('../ScriptEditor/hooks/useL3Completion', () => ({ useL3Completion: vi.fn() }));
vi.mock('../ScriptEditor/panels', () => ({ default: () => null }));
vi.mock('../ScriptEditor/sidebar', () => ({ default: () => null }));
vi.mock('../ScriptEditor/views/StoryboardView', () => ({ default: () => null }));
vi.mock('../ScriptEditor/components/ContinuityIndicator', () => ({ ContinuityIndicator: () => null }));

vi.mock('@/lib/scriptEditorApi', () => ({
    scriptEditorApi: {
        loadDocument: vi.fn(),
        saveDocument: vi.fn(),
        listSnapshots: vi.fn(),
        restoreSnapshot: vi.fn(),
        deriveGaps: vi.fn(),
    },
}));

function doc(text: string) {
    return { type: 'doc', content: [{ type: 'action', content: [{ type: 'text', text }] }] };
}

function envelope(projectId: string, revision: number, text: string) {
    return { project_id: projectId, revision, updated_at: `2026-10-10T00:00:0${revision}.000000Z`, content: doc(text) };
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
    };
}

describe('ScriptEditorShell document integration', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.spyOn(window, 'confirm').mockReturnValue(true);
        useEditorStore.setState({ isDirty: false, lastSavedAt: null });
        shellEditorRef.current = fakeEditor();
        vi.mocked(scriptEditorApi.loadDocument).mockResolvedValue(envelope('p', 5, 'base'));
        vi.mocked(scriptEditorApi.listSnapshots).mockResolvedValue([
            { project_id: 'p', snapshot_id: 'snap-1', timestamp: 'snap-1', created_at: '2026-10-10T00:00:00Z', revision: 4, size_bytes: 12 },
        ]);
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('opens snapshots from the shell and adopts the returned envelope', async () => {
        vi.mocked(scriptEditorApi.restoreSnapshot).mockResolvedValueOnce(envelope('p', 6, 'restored'));

        renderWithIntl(<ScriptEditorShell mode="embedded" projectId="p" />);
        fireEvent.click(await screen.findByRole('button', { name: '版本历史' }));
        fireEvent.click(await screen.findByRole('button', { name: '恢复' }));
        fireEvent.click(await screen.findByRole('button', { name: '确认恢复' }));

        await waitFor(() => expect(scriptEditorApi.restoreSnapshot).toHaveBeenCalledWith('p', 'snap-1', { expectedRevision: 5 }));
        expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('restored'), expect.objectContaining({ emitUpdate: false }));
        expect(useEditorStore.getState().isDirty).toBe(false);
    });

    it('does not call restore while the current editor has an unsaved draft', async () => {
        renderWithIntl(<ScriptEditorShell mode="embedded" projectId="p" />);
        await screen.findByRole('button', { name: '版本历史' });
        act(() => useEditorStore.setState({ isDirty: true }));
        fireEvent.click(screen.getByRole('button', { name: '版本历史' }));
        const restore = await screen.findByRole('button', { name: '恢复' });

        expect(restore).toBeDisabled();
        expect(scriptEditorApi.restoreSnapshot).not.toHaveBeenCalled();
    });

    it('does not reload the latest document when discard confirmation is cancelled', async () => {
        vi.mocked(window.confirm).mockReturnValueOnce(false);
        vi.mocked(scriptEditorApi.loadDocument)
            .mockResolvedValueOnce(envelope('p-cancel', 1, 'base'))
            .mockResolvedValueOnce(envelope('other-cancel', 3, 'other'))
            .mockResolvedValueOnce(envelope('p-cancel', 2, 'remote changed'));
        const view = renderWithIntl(<ScriptEditorShell mode="embedded" projectId="p-cancel" />);
        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('base'), expect.anything()));
        act(() => {
            shellEditorRef.current.setContentForTest(doc('local draft'));
            useEditorStore.setState({ isDirty: true });
        });
        view.rerender(<ScriptEditorShell mode="embedded" projectId="other-cancel" />);
        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('other'), expect.anything()));
        view.rerender(<ScriptEditorShell mode="embedded" projectId="p-cancel" />);
        await screen.findByRole('button', { name: '舍弃本地草稿并读取最新版本' });

        fireEvent.click(screen.getByRole('button', { name: '舍弃本地草稿并读取最新版本' }));

        expect(window.confirm).toHaveBeenCalledTimes(1);
        expect(scriptEditorApi.loadDocument).toHaveBeenCalledTimes(3);
        expect(scriptEditorApi.saveDocument).not.toHaveBeenCalled();
        expect(shellEditorRef.current.getJSON()).toEqual(doc('local draft'));
    });

    it('adopts the latest document after confirmed discard and reload', async () => {
        vi.mocked(scriptEditorApi.loadDocument)
            .mockResolvedValueOnce(envelope('p-reload', 1, 'base'))
            .mockResolvedValueOnce(envelope('other-reload', 3, 'other'))
            .mockResolvedValueOnce(envelope('p-reload', 2, 'remote changed'))
            .mockResolvedValueOnce(envelope('p-reload', 3, 'latest'));
        vi.mocked(scriptEditorApi.restoreSnapshot).mockResolvedValueOnce(envelope('p-reload', 4, 'restored'));
        const view = renderWithIntl(<ScriptEditorShell mode="embedded" projectId="p-reload" />);
        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('base'), expect.anything()));
        act(() => {
            shellEditorRef.current.setContentForTest(doc('local draft'));
            useEditorStore.setState({ isDirty: true });
        });
        view.rerender(<ScriptEditorShell mode="embedded" projectId="other-reload" />);
        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('other'), expect.anything()));
        view.rerender(<ScriptEditorShell mode="embedded" projectId="p-reload" />);
        await screen.findByRole('button', { name: '舍弃本地草稿并读取最新版本' });

        fireEvent.click(screen.getByRole('button', { name: '舍弃本地草稿并读取最新版本' }));

        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('latest'), expect.objectContaining({ emitUpdate: false })));
        expect(useEditorStore.getState().isDirty).toBe(false);
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '版本历史' }));
        fireEvent.click(await screen.findByRole('button', { name: '恢复' }));
        fireEvent.click(await screen.findByRole('button', { name: '确认恢复' }));
        await waitFor(() => expect(scriptEditorApi.restoreSnapshot).toHaveBeenCalledWith('p-reload', 'snap-1', { expectedRevision: 3 }));
    });

    it('keeps the local draft when confirmed discard reload fails', async () => {
        vi.mocked(scriptEditorApi.loadDocument)
            .mockResolvedValueOnce(envelope('p-fail', 1, 'base'))
            .mockResolvedValueOnce(envelope('other-fail', 3, 'other'))
            .mockResolvedValueOnce(envelope('p-fail', 2, 'remote changed'))
            .mockRejectedValueOnce(new Error('storage down'));
        const view = renderWithIntl(<ScriptEditorShell mode="embedded" projectId="p-fail" />);
        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('base'), expect.anything()));
        act(() => {
            shellEditorRef.current.setContentForTest(doc('local draft'));
            useEditorStore.setState({ isDirty: true });
        });
        view.rerender(<ScriptEditorShell mode="embedded" projectId="other-fail" />);
        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('other'), expect.anything()));
        view.rerender(<ScriptEditorShell mode="embedded" projectId="p-fail" />);
        await screen.findByRole('button', { name: '舍弃本地草稿并读取最新版本' });

        fireEvent.click(screen.getByRole('button', { name: '舍弃本地草稿并读取最新版本' }));

        await screen.findByText('读取已保存文档失败，本地编辑已保留。');
        expect(shellEditorRef.current.getJSON()).toEqual(doc('local draft'));
        expect(useEditorStore.getState().isDirty).toBe(true);
    });

    it('ignores a discard reload result after switching projects', async () => {
        const pendingReload = deferred<ReturnType<typeof envelope>>();
        vi.mocked(scriptEditorApi.loadDocument)
            .mockResolvedValueOnce(envelope('p-late', 1, 'base'))
            .mockResolvedValueOnce(envelope('other-late', 3, 'other'))
            .mockResolvedValueOnce(envelope('p-late', 2, 'remote changed'))
            .mockReturnValueOnce(pendingReload.promise)
            .mockResolvedValueOnce(envelope('next', 8, 'next project'));
        const view = renderWithIntl(<ScriptEditorShell mode="embedded" projectId="p-late" />);
        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('base'), expect.anything()));
        act(() => {
            shellEditorRef.current.setContentForTest(doc('local draft'));
            useEditorStore.setState({ isDirty: true });
        });
        view.rerender(<ScriptEditorShell mode="embedded" projectId="other-late" />);
        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('other'), expect.anything()));
        view.rerender(<ScriptEditorShell mode="embedded" projectId="p-late" />);
        await screen.findByRole('button', { name: '舍弃本地草稿并读取最新版本' });
        fireEvent.click(screen.getByRole('button', { name: '舍弃本地草稿并读取最新版本' }));

        view.rerender(<ScriptEditorShell mode="embedded" projectId="next" />);
        await waitFor(() => expect(shellEditorRef.current.commands.setContent).toHaveBeenLastCalledWith(doc('next project'), expect.anything()));
        await act(async () => pendingReload.resolve(envelope('p-late', 3, 'late latest')));

        expect(shellEditorRef.current.getJSON()).toEqual(doc('next project'));
    });
});
