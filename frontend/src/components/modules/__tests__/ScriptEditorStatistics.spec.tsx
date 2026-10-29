import { useEffect } from 'react';
import { EditorContent, type Editor } from '@tiptap/react';
import { act, screen, waitFor } from '@testing-library/react';
import axios, { type AxiosAdapter } from 'axios';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { useEditorSetup } from '../ScriptEditor/hooks/useEditorSetup';
import { useAutoSave } from '../ScriptEditor/hooks/useAutoSave';
import { useL3Completion } from '../ScriptEditor/hooks/useL3Completion';
import { useEditorStore } from '@/store/editorStore';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';

function doc(text: string) {
    return { type: 'doc', content: [{ type: 'action', ...(text ? { content: [{ type: 'text', text }] } : {}) }] };
}

const screenplay = {
    type: 'doc',
    content: [
        { type: 'sceneHeading', attrs: { id: 'room' }, content: [{ type: 'text', text: 'INT. ROOM - DAY' }] },
        { type: 'characterCue', content: [{ type: 'text', text: 'ADA' }] },
        { type: 'dialogue', content: [{ type: 'text', text: 'Hello' }] },
        { type: 'action', content: [{ type: 'text', text: 'Wait' }] },
        { type: 'sceneHeading', attrs: { id: 'park' }, content: [{ type: 'text', text: 'EXT. PARK - NIGHT' }] },
        { type: 'characterCue', content: [{ type: 'text', text: 'ADA' }] },
        { type: 'dialogue', content: [{ type: 'text', text: 'Bye' }] },
    ],
};

function envelope(projectId: string, content: Record<string, unknown>) {
    return { project_id: projectId, revision: 3, updated_at: '2026-10-15T00:00:00Z', content };
}

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(r => { resolve = r; });
    return { promise, resolve };
}

let activeEditor: Editor | null;
let adapterBefore: typeof axios.defaults.adapter;
let load: ReturnType<typeof deferred<ReturnType<typeof envelope>>>;
let writes: string[];
let derives: Array<{ url: string; body: Record<string, unknown>; storageId: string | undefined }>;
let deriveReply: ReturnType<typeof deferred<{ entities: Array<{ type: string; name: string; confidence: number }>; cached: boolean }>> | null;

function Harness({ id = null, initialContent }: { id?: string | null; initialContent?: Record<string, unknown> }) {
    const { editor } = useEditorSetup({ content: initialContent });
    const persistence = useAutoSave(editor, id);
    useL3Completion(editor, id);
    const words = useEditorStore(s => s.wordCount);
    const scenes = useEditorStore(s => s.derivedScenes);
    const characters = useEditorStore(s => s.derivedCharacters);
    const dirty = useEditorStore(s => s.isDirty);
    useEffect(() => { activeEditor = editor; }, [editor]);
    return <>
        <EditorContent editor={editor} />
        <output data-testid="words">{words}</output>
        <output data-testid="scenes">{scenes.length}:{scenes.map(scene => scene.title).join('|')}</output>
        <output data-testid="characters">{characters.map(character => `${character.name}:${character.occurrences}`).join('|')}</output>
        <output data-testid="dirty">{String(dirty)}</output>
        <output data-testid="load">{persistence.loadStatus}</output>
        <output data-testid="revision">{persistence.documentRevision}</output>
    </>;
}

async function open(id: string, content: Record<string, unknown>) {
    load = deferred();
    const view = renderWithIntl(<Harness id={id} />);
    await waitFor(() => expect(screen.getByTestId('load')).toHaveTextContent('loading'));
    await act(async () => { load.resolve(envelope(id, content)); });
    await waitFor(() => expect(activeEditor?.getJSON()).toBeTruthy());
    return view;
}

beforeEach(() => {
    activeEditor = null;
    writes = [];
    derives = [];
    deriveReply = null;
    load = deferred();
    useEditorStore.getState().reset();
    adapterBefore = axios.defaults.adapter;
    const adapter: AxiosAdapter = async config => {
        if (config.url?.endsWith('/derive_gaps')) {
            derives.push({ url: config.url, body: JSON.parse(config.data), storageId: getRuntimeEnvironment()?.storageId });
            const data = deriveReply ? await deriveReply.promise : { entities: [{ type: 'prop', name: 'Current prop', confidence: 0.8 }], cached: false };
            return { data, status: 200, statusText: 'OK', headers: {}, config };
        }
        if (config.method !== 'get') {
            writes.push(config.method ?? 'unknown');
            throw new Error('Statistics must not write a document');
        }
        return { data: await load.promise, status: 200, statusText: 'OK', headers: {}, config };
    };
    axios.defaults.adapter = adapter;
});

afterEach(() => { axios.defaults.adapter = adapterBefore; });

describe('real Tiptap statistics follow the current document without creating edits', () => {
    it('derives initial screenplay content once per text, including scenes and characters', async () => {
        renderWithIntl(<Harness initialContent={screenplay} />);
        await waitFor(() => expect(activeEditor?.state.doc.textContent).toHaveLength(50));
        expect(screen.getByTestId('words')).toHaveTextContent(/^50$/);
        expect(screen.getByTestId('scenes')).toHaveTextContent('2:INT. ROOM - DAY|EXT. PARK - NIGHT');
        expect(screen.getByTestId('characters')).toHaveTextContent('ADA:2');
        expect(useEditorStore.getState().estimatedDuration).toBe(120);
        expect(useEditorStore.getState().confidenceScore).toBeCloseTo(46 / 50);
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        expect(writes).toEqual([]);
    });

    it('counts a saved document adopted with emitUpdate false on a fresh mount', async () => {
        await open('statistics-saved', doc('甲'.repeat(42)));
        expect(activeEditor?.getText()).toBe('甲'.repeat(42));
        expect(screen.getByTestId('load')).toHaveTextContent('ready');
        expect(screen.getByTestId('words')).toHaveTextContent(/^42$/);
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        expect(screen.getByTestId('revision')).toHaveTextContent('3');
        expect(writes).toEqual([]);
    });

    it.each(['empty-B', 'restored-A'] as const)('does not show another environment count for %s', async stage => {
        const id = `statistics-${stage}`;
        const first = await open(id, doc('A saved'));
        act(() => { activeEditor!.commands.setContent(doc('甲'.repeat(42))); });
        clearRuntimeEnvironment();
        first.unmount();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' });
        const second = await open(id, doc(''));
        expect(activeEditor?.getText()).toBe('');
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        if (stage === 'empty-B') {
            expect(screen.getByTestId('words')).toHaveTextContent(/^0$/);
        } else {
            act(() => { activeEditor!.commands.setContent(doc('乙'.repeat(28))); });
            second.unmount();
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
            await open(id, doc('A saved'));
            expect(activeEditor?.getText()).toBe('甲'.repeat(42));
            expect(screen.getByTestId('load')).toHaveTextContent('recovered-draft');
            expect(screen.getByTestId('dirty')).toHaveTextContent('true');
            expect(screen.getByTestId('words')).toHaveTextContent(/^42$/);
            expect(screen.getByTestId('revision')).toHaveTextContent('3');
        }
        expect(writes).toEqual([]);
    });

    it('refreshes silent replacements and clears previous scene and character derivations', async () => {
        await open('statistics-scene-load', screenplay);
        expect(screen.getByTestId('scenes')).toHaveTextContent(/^2:/);
        act(() => { activeEditor!.commands.setContent(doc('B'), { emitUpdate: false }); });
        expect(screen.getByTestId('words')).toHaveTextContent(/^1$/);
        expect(screen.getByTestId('scenes')).toHaveTextContent(/^0:$/);
        expect(screen.getByTestId('characters')).toBeEmptyDOMElement();
        expect(useEditorStore.getState().estimatedDuration).toBe(0);
        expect(useEditorStore.getState().confidenceScore).toBe(0);
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        expect(writes).toEqual([]);
    });

    it('keeps real editing dirty and derives each text once after the existing debounce', async () => {
        await open('statistics-edit', doc(''));
        act(() => { activeEditor!.commands.setContent(screenplay); });
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
        await waitFor(() => expect(screen.getByTestId('scenes')).toHaveTextContent(/^2:/));
        expect(screen.getByTestId('words')).toHaveTextContent(/^50$/);
        expect(screen.getByTestId('characters')).toHaveTextContent('ADA:2');
        expect(useEditorStore.getState().confidenceScore).toBeCloseTo(46 / 50);
        expect(writes).toEqual([]);
    });

    it('does not mark readonly or selection-only events dirty or change saved metadata', async () => {
        await open('statistics-selection', doc('Clean saved text'));
        const savedAt = useEditorStore.getState().lastSavedAt;
        const before = activeEditor!.getJSON();
        act(() => {
            activeEditor!.setEditable(false, false);
            activeEditor!.commands.setTextSelection(3);
            activeEditor!.setEditable(true, false);
        });
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        expect(activeEditor!.getJSON()).toEqual(before);
        expect(useEditorStore.getState().lastSavedAt).toEqual(savedAt);
        expect(screen.getByTestId('revision')).toHaveTextContent('3');
        expect(writes).toEqual([]);
    });

    it('does not let a destroyed editor debounce replace the next document statistics', async () => {
        const first = await open('statistics-timer-A', doc(''));
        act(() => { activeEditor!.commands.setContent(screenplay); });
        first.unmount();
        await open('statistics-timer-B', doc(''));
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 550)); });
        expect(activeEditor!.getText()).toBe('');
        expect(screen.getByTestId('words')).toHaveTextContent(/^0$/);
        expect(screen.getByTestId('scenes')).toHaveTextContent(/^0:$/);
        expect(screen.getByTestId('characters')).toBeEmptyDOMElement();
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        expect(writes).toEqual([]);
    });
});


const mixedDocument = {
    type: 'doc',
    content: [
        { type: 'sceneHeading', content: [{ type: 'text', text: 'INT. LAB - DAY' }] },
        { type: 'action', content: [{ type: 'text', text: 'A long unstructured action waiting for a genuine user edit before completion.' }] },
    ],
};

async function settle(milliseconds: number) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, milliseconds)); });
}

describe('actual L1 refresh does not authorize L3 work', () => {
    it('does not request completion for silent saved content, selection or readonly changes', async () => {
        await open('statistics-l3-silent', mixedDocument);
        expect(useEditorStore.getState().confidenceScore).toBeGreaterThan(0);
        expect(useEditorStore.getState().confidenceScore).toBeLessThan(0.7);
        act(() => {
            activeEditor!.commands.setTextSelection(3);
            activeEditor!.setEditable(false, false);
            activeEditor!.setEditable(true, false);
        });
        await settle(2700);
        expect(derives).toEqual([]);
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        expect(writes).toEqual([]);
    });

    it('does not treat a recovered dirty draft as a new user edit', async () => {
        const first = await open('statistics-l3-recovered', doc(''));
        act(() => { activeEditor!.commands.setContent(mixedDocument); });
        first.unmount();
        await open('statistics-l3-recovered', doc(''));
        expect(screen.getByTestId('load')).toHaveTextContent('recovered-draft');
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
        expect(screen.getByTestId('scenes')).toHaveTextContent(/^1:/);
        await settle(2700);
        expect(derives).toEqual([]);
        expect(writes).toEqual([]);
    });

    it('cancels an edit timer when the mounted editor switches project', async () => {
        const view = await open('statistics-l3-project-A', doc(''));
        act(() => { activeEditor!.commands.setContent(mixedDocument); });
        await settle(600);
        load = deferred();
        view.rerender(<Harness id="statistics-l3-project-B" />);
        await act(async () => { load.resolve(envelope('statistics-l3-project-B', doc(''))); });
        await settle(2700);
        expect(activeEditor!.getText()).toBe('');
        expect(screen.getByTestId('words')).toHaveTextContent(/^0$/);
        expect(screen.getByTestId('scenes')).toHaveTextContent(/^0:$/);
        expect(screen.getByTestId('dirty')).toHaveTextContent('false');
        expect(derives).toEqual([]);
    });

    it('retains one user edit debounce across the L1 refresh and uses current extracted fields', async () => {
        await open('statistics-l3-edit', doc(''));
        act(() => { activeEditor!.commands.setContent(mixedDocument); });
        await settle(600);
        expect(useEditorStore.getState().derivedScenes).toHaveLength(1);
        await settle(1600);
        expect(derives).toHaveLength(1);
        expect(derives[0].body.already_extracted).toEqual({ scenes: ['INT. LAB - DAY'], characters: [] });
        expect(useEditorStore.getState().l3Results?.[0].name).toBe('Current prop');
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
        expect(writes).toEqual([]);
    });

    it('cancels an edit timer when a silent replacement becomes the current document', async () => {
        await open('statistics-l3-replace', doc(''));
        act(() => { activeEditor!.commands.setContent(doc('user edit')); });
        await settle(600);
        act(() => { activeEditor!.commands.setContent(doc('loaded replacement'), { emitUpdate: false }); });
        await settle(2100);
        expect(derives).toEqual([]);
        expect(activeEditor!.getText()).toBe('loaded replacement');
    });

    it('cancels an old same-ID environment timer without submitting the old document to B', async () => {
        await open('statistics-l3-env-timer', doc(''));
        act(() => { activeEditor!.commands.setContent(doc('A edit')); });
        await settle(600);
        act(() => {
            clearRuntimeEnvironment();
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' });
        });
        await settle(2100);
        expect(derives).toEqual([]);
        expect(useEditorStore.getState().l3Status).toBe('idle');
    });

    it('clears same-ID environment results/cache and allows a new edit after reactivation', async () => {
        await open('statistics-l3-env-cache', doc(''));
        act(() => { activeEditor!.commands.setContent(doc('same text')); });
        await settle(2200);
        expect(derives).toHaveLength(1);
        expect(useEditorStore.getState().l3Status).toBe('success');
        act(() => {
            clearRuntimeEnvironment();
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' });
        });
        expect(useEditorStore.getState().l3Status).toBe('idle');
        expect(useEditorStore.getState().l3Results).toBeNull();
        expect(useEditorStore.getState().l3LastFetchTime).toBeNull();
        act(() => {
            activeEditor!.commands.setContent(doc(''), { emitUpdate: false });
            activeEditor!.commands.setContent(doc('same text'));
        });
        await settle(2200);
        expect(derives.map(item => item.storageId)).toEqual([
            '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222',
        ]);
        act(() => {
            clearRuntimeEnvironment();
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
            activeEditor!.commands.setContent(doc(''), { emitUpdate: false });
            activeEditor!.commands.setContent(doc('same text'));
        });
        await settle(2200);
        expect(derives).toHaveLength(3);
    }, 10000);

    it('clears a successful cache when silent replacement discards its visible results', async () => {
        await open('statistics-l3-silent-cache', doc(''));
        act(() => { activeEditor!.commands.setContent(doc('same eligible text')); });
        await settle(2200);
        expect(derives).toHaveLength(1);
        expect(useEditorStore.getState().l3Status).toBe('success');
        expect(useEditorStore.getState().l3Results?.[0].name).toBe('Current prop');
        act(() => { activeEditor!.commands.setContent(doc(''), { emitUpdate: false }); });
        expect(useEditorStore.getState().l3Status).toBe('idle');
        expect(useEditorStore.getState().l3Results).toBeNull();
        await settle(2200);
        expect(derives).toHaveLength(1);
        act(() => { activeEditor!.commands.setContent(doc('same eligible text')); });
        await settle(2200);
        expect(activeEditor!.getText()).toBe('same eligible text');
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
        expect(derives).toHaveLength(2);
        expect(derives[1].body).toEqual(derives[0].body);
        expect(useEditorStore.getState().l3Status).toBe('success');
        expect(useEditorStore.getState().l3Results?.[0].name).toBe('Current prop');
        expect(writes).toEqual([]);
    }, 10000);

    it('cancels an in-flight completion when a silent document replaces its input', async () => {
        await open('statistics-l3-replace-ack', doc(''));
        deriveReply = deferred();
        act(() => { activeEditor!.commands.setContent(doc('original text')); });
        await settle(2200);
        expect(derives).toHaveLength(1);
        act(() => { activeEditor!.commands.setContent(doc('loaded text'), { emitUpdate: false }); });
        await act(async () => {
            deriveReply!.resolve({ entities: [{ type: 'prop', name: 'Old document prop', confidence: 0.8 }], cached: false });
        });
        expect(useEditorStore.getState().l3Status).toBe('idle');
        expect(useEditorStore.getState().l3Results).toBeNull();
        expect(derives).toHaveLength(1);
    });

    it.each(['pending', 'success'] as const)('clears obsolete %s completion when a user edit no longer needs L3', async phase => {
        await open(`statistics-l3-structured-${phase}`, doc(''));
        if (phase === 'pending') deriveReply = deferred();
        act(() => { activeEditor!.commands.setContent(doc('old unstructured request')); });
        await settle(2200);
        expect(derives).toHaveLength(1);
        expect(useEditorStore.getState().l3Status).toBe(phase === 'pending' ? 'loading' : 'success');

        act(() => { activeEditor!.commands.setContent(screenplay); });
        await settle(600);
        await settle(1600);
        expect(useEditorStore.getState().confidenceScore).toBeGreaterThan(0.7);
        expect(useEditorStore.getState().l3Status).toBe('idle');
        expect(useEditorStore.getState().l3Results).toBeNull();
        expect(useEditorStore.getState().l3LastFetchTime).toBeNull();
        if (deriveReply) {
            await act(async () => {
                deriveReply!.resolve({ entities: [{ type: 'prop', name: 'Old result', confidence: 0.8 }], cached: false });
            });
        }
        expect(derives).toHaveLength(1);
        expect(useEditorStore.getState().l3Status).toBe('idle');
        expect(useEditorStore.getState().l3Results).toBeNull();

        deriveReply = null;
        act(() => { activeEditor!.commands.setContent(doc('old unstructured request')); });
        await settle(600);
        await settle(1600);
        expect(derives).toHaveLength(2);
        expect(useEditorStore.getState().l3Results?.[0].name).toBe('Current prop');
        expect(screen.getByTestId('dirty')).toHaveTextContent('true');
        expect(writes).toEqual([]);
    }, 10000);

    it('ignores an in-flight A result after same-ID A to B to A reactivation', async () => {
        await open('statistics-l3-env-ack', doc(''));
        deriveReply = deferred();
        act(() => { activeEditor!.commands.setContent(doc('A text')); });
        await settle(2200);
        expect(derives).toHaveLength(1);
        expect(useEditorStore.getState().l3Status).toBe('loading');
        act(() => {
            clearRuntimeEnvironment();
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' });
            clearRuntimeEnvironment();
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
        });
        await act(async () => {
            deriveReply!.resolve({ entities: [{ type: 'prop', name: 'Late A', confidence: 0.8 }], cached: false });
        });
        expect(useEditorStore.getState().l3Status).toBe('idle');
        expect(useEditorStore.getState().l3Results).toBeNull();
        expect(derives).toHaveLength(1);
    });
});
