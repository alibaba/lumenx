import { useEffect } from 'react';
import { EditorContent, type Editor } from '@tiptap/react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import axios, { type AxiosAdapter } from 'axios';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { useEditorSetup } from '../ScriptEditor/hooks/useEditorSetup';
import { useAutoSave } from '../ScriptEditor/hooks/useAutoSave';
import { useEditorStore } from '@/store/editorStore';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';

function doc(text: string) {
    return { type: 'doc', content: [{ type: 'action', content: [{ type: 'text', text }] }] };
}
function envelope(projectId: string, revision: number, text: string) {
    return { project_id: projectId, revision, updated_at: '2026-10-15T00:00:00Z', content: doc(text) };
}
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(r => { resolve = r; });
    return { promise, resolve };
}
let activeEditor: Editor | null;
let adapterBefore: typeof axios.defaults.adapter;
let load: ReturnType<typeof deferred<ReturnType<typeof envelope>>>;
let writes: Array<{ url: string | undefined; body: Record<string, unknown> }>;
let heldSave: ReturnType<typeof deferred<ReturnType<typeof envelope>>> | undefined;
let saveFromEditor: ReturnType<typeof useAutoSave>['save'];

function Harness({ id }: { id: string }) {
    const { editor } = useEditorSetup();
    const state = useAutoSave(editor, id);
    const dirty = useEditorStore(s => s.isDirty);
    useEffect(() => { activeEditor = editor; }, [editor]);
    useEffect(() => { saveFromEditor = state.save; }, [state.save]);
    return <>
        <EditorContent editor={editor} />
        <output data-testid="load">{state.loadStatus}</output>
        <output data-testid="dirty">{String(dirty)}</output>
        <output data-testid="error">{state.errorKey}</output>
        <output data-testid="revision">{state.documentRevision}</output>
        <button onClick={() => void state.save()}>Save</button>
        <button onClick={() => void state.save(false, { force: false })}>Autosave</button>
    </>;
}

beforeEach(() => {
    activeEditor = null; writes = []; load = deferred(); heldSave = undefined;
    useEditorStore.getState().reset();
    adapterBefore = axios.defaults.adapter;
    const adapter: AxiosAdapter = async config => {
        let data;
        if (config.method === 'get') data = await load.promise;
        else if (config.method === 'post' && config.url?.endsWith('/document')) {
            const body = JSON.parse(config.data);
            writes.push({ url: config.url, body });
            data = heldSave ? await heldSave.promise : { ...envelope('real-edit', 4, ''), content: body.content };
        } else throw new Error(`Unexpected method ${config.method}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    axios.defaults.adapter = adapter;
});

describe('real editor draft ownership across destroyed and recreated editors', () => {
    const A = '11111111-1111-4111-8111-111111111111';
    const B = '22222222-2222-4222-8222-222222222222';
    async function open(id: string, text: string, revision = 3) {
        load = deferred();
        const view = renderWithIntl(<Harness id={id} />);
        await waitFor(() => expect(screen.getByTestId('load')).toHaveTextContent('loading'));
        await act(async () => { load.resolve(envelope(id, revision, text)); });
        return view;
    }
    it.each(['same-project', 'other-project', 'other-environment'] as const)('preserves A without lending its dirty flag to %s', async scenario => {
        const id = `remount-${scenario}`;
        const first = await open(id, 'A remote');
        expect(screen.getByTestId('load')).toHaveTextContent('ready');
        act(() => { activeEditor!.commands.setContent(doc('A unsaved real content')); });
        // The gate clears identity before unmounting its old children.
        if (scenario === 'other-environment') clearRuntimeEnvironment();
        first.unmount();
        if (scenario === 'other-environment') activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        const nextId = scenario === 'other-project' ? id + '-B' : id;
        const second = await open(nextId, scenario === 'same-project' ? 'A remote' : 'B remote', scenario === 'same-project' ? 3 : 7);
        if (scenario !== 'same-project') {
            expect(screen.getByTestId('load')).toHaveTextContent('ready');
            expect(screen.getByTestId('dirty')).toHaveTextContent('false');
            expect(activeEditor!.getText()).toBe('B remote');
            act(() => { activeEditor!.commands.setContent(doc('B separate draft')); });
            second.unmount();
            if (scenario === 'other-environment') activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
            await open(id, 'A remote');
        }
        expect(screen.getByTestId('load')).toHaveTextContent('recovered-draft');
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
        expect(activeEditor!.getText()).toBe('A unsaved real content');
        expect(screen.getByTestId('revision')).toHaveTextContent('3');
        expect(writes).toEqual([]);
    });

    it('keeps a recovered draft at its old revision when the remote changed while closed', async () => {
        const id = 'remount-revision';
        const first = await open(id, 'Remote before close');
        act(() => { activeEditor!.commands.setContent(doc('Preserved older revision draft')); });
        first.unmount();
        await open(id, 'Newer remote must not be overwritten', 4);
        expect(activeEditor!.getText()).toBe('Preserved older revision draft');
        expect(screen.getByTestId('revision')).toHaveTextContent('3');
        expect(screen.getByTestId('error')).toHaveTextContent('saveConflict');
        fireEvent.click(screen.getByRole('button', { name: 'Autosave' }));
        await act(async () => {});
        expect(writes).toEqual([]);
    });

    it('does not let an old save acknowledgement clear a remounted A draft after visiting B', async () => {
        const id = 'remount-late-save';
        const first = await open(id, 'A saved');
        act(() => { activeEditor!.commands.setContent(doc('A pending save')); });
        heldSave = deferred();
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(writes).toHaveLength(1));
        clearRuntimeEnvironment(); first.unmount();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        const second = await open(id, 'B saved', 7);
        expect(activeEditor!.getText()).toBe('B saved');
        second.unmount();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
        await open(id, 'A saved');
        await act(async () => { heldSave!.resolve(envelope(id, 4, 'A pending save')); });
        expect(activeEditor!.getText()).toBe('A pending save');
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
        expect(screen.getByTestId('revision')).toHaveTextContent('3');
        expect(writes).toHaveLength(1);
    });

    it.each(['cleared', 'replaced'] as const)('does not dispatch an old editor save after runtime identity is %s', async transition => {
        const id = `remount-stale-save-${transition}`;
        await open(id, 'A saved');
        act(() => { activeEditor!.commands.setContent(doc('A draft must remain local')); });
        if (transition === 'cleared') clearRuntimeEnvironment();
        else activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        await act(async () => { await expect(saveFromEditor(false, { force: false })).resolves.toBeUndefined(); });
        expect(writes).toEqual([]);
        expect(activeEditor!.getText()).toBe('A draft must remain local');
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
    });
});
afterEach(() => { axios.defaults.adapter = adapterBefore; });

describe('real Tiptap lifecycle with production editor setup and persistence', () => {
    it('loads an untouched remote document without inventing a local edit', async () => {
        renderWithIntl(<Harness id="real-clean" />);
        await waitFor(() => expect(screen.getByTestId('load')).toHaveTextContent('loading'));
        expect(activeEditor?.isEditable).toBe(false);
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        await act(async () => { load.resolve(envelope('real-clean', 3, 'Remote saved document')); });
        await waitFor(() => expect(screen.getByTestId('load')).toHaveTextContent('ready'));
        expect(activeEditor?.getText()).toBe('Remote saved document');
        expect(activeEditor?.isEditable).toBe(true);
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        expect(screen.getByTestId('error')).toBeEmptyDOMElement();
        expect(writes).toEqual([]);
    });

    it('still marks an actual document edit dirty and clears it only after save acknowledgement', async () => {
        renderWithIntl(<Harness id="real-edit" />);
        await waitFor(() => expect(activeEditor).not.toBeNull());
        await act(async () => { load.resolve(envelope('real-edit', 3, 'Saved text')); });
        await waitFor(() => expect(screen.getByTestId('load')).toHaveTextContent('ready'));
        act(() => { activeEditor!.commands.setContent(doc('Actual edited text')); });
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(screen.getByTestId('dirty')).toHaveTextContent('false'));
        expect(writes).toHaveLength(1);
        expect(writes[0].body).toMatchObject({ expected_revision: 3, content: doc('Actual edited text') });
    });

    it('retains a real late edit instead of replacing it with an arriving remote document', async () => {
        renderWithIntl(<Harness id="real-late" />);
        await waitFor(() => expect(screen.getByTestId('load')).toHaveTextContent('loading'));
        // A programmatic edit is still a content mutation even while readonly.
        act(() => { activeEditor!.commands.setContent(doc('Local draft during read')); });
        await act(async () => { load.resolve(envelope('real-late', 3, 'Remote must not overwrite')); });
        await waitFor(() => expect(screen.getByTestId('load')).toHaveTextContent('blocked-dirty'));
        expect(activeEditor?.getText()).toBe('Local draft during read');
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
        expect(screen.getByTestId('error')).toHaveTextContent('dirtyRemote');
        expect(writes).toEqual([]);
    });
});
