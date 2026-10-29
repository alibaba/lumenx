import React from 'react';
import { act, render } from '@testing-library/react';
import type { Editor, EditorEvents } from '@tiptap/react';
import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { scriptEditorApi } from '@/lib/scriptEditorApi';
import { useEditorStore } from '@/store/editorStore';
import { useL3Completion } from '../ScriptEditor/hooks/useL3Completion';

vi.mock('@/lib/apiUrl', () => ({ API_URL: 'http://api.test' }));
vi.mock('axios', () => ({
    default: {
        get: vi.fn(),
        post: vi.fn(),
    },
}));

type FakeEditor = Editor & {
    emitUpdate: () => void;
    setText: (text: string) => void;
    setEditableForTest: (editable: boolean) => void;
};

function fakeEditor(initialText = 'CURRENT EDITOR TEXT'): FakeEditor {
    let text = initialText;
    let editable = true;
    const handlers = new Set<(event: EditorEvents['update']) => void>();
    return {
        get isEditable() {
            return editable;
        },
        isDestroyed: false,
        getText: vi.fn(() => text),
        on: vi.fn((event: string, handler: (event: EditorEvents['update']) => void) => {
            if (event === 'update') handlers.add(handler);
        }),
        off: vi.fn((event: string, handler: (event: EditorEvents['update']) => void) => {
            if (event === 'update') handlers.delete(handler);
        }),
        emitUpdate: () => {
            for (const handler of Array.from(handlers)) handler({ transaction: { docChanged: true } } as EditorEvents['update']);
        },
        setText: (next: string) => {
            text = next;
        },
        setEditableForTest: (next: boolean) => {
            editable = next;
        },
    } as unknown as FakeEditor;
}

function L3Harness({ editor, projectId }: { editor: Editor | null; projectId: string | null }) {
    useL3Completion(editor, projectId);
    return null;
}

async function flushAsyncWork() {
    await Promise.resolve();
    await Promise.resolve();
}

function primeLowConfidence() {
    useEditorStore.setState({
        confidenceScore: 0.2,
        derivedScenes: [{ id: 's1', number: 1, intExt: 'INT', location: 'Lab', timeOfDay: 'Day', title: 'INT. LAB - DAY' }],
        derivedCharacters: [{ id: 'ada', name: 'ADA', occurrences: 1, firstAppearance: 1 }],
        l3Status: 'idle',
        l3Results: null,
        l3LastFetchTime: null,
    });
}

describe('script editor L3 bridge', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.clearAllMocks();
        primeLowConfidence();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
        useEditorStore.getState().reset();
    });

    it('maps backend entities to L3 results and passes AbortSignal through axios', async () => {
        const signal = new AbortController().signal;
        vi.mocked(axios.post).mockResolvedValueOnce({
            data: {
                entities: [{ type: 'prop', name: 'Scanner', description: 'Hand prop', confidence: 0.8, scene_index: 2 }],
                cached: false,
            },
        });

        const result = await scriptEditorApi.deriveGaps(
            'p',
            { raw_text_blocks: ['CURRENT'], gaps: ['props'] },
            { signal }
        );

        expect(axios.post).toHaveBeenCalledWith(
            'http://api.test/projects/p/derive_gaps',
            { raw_text_blocks: ['CURRENT'], gaps: ['props'] },
            { signal }
        );
        expect(result).toEqual({
            results: [{ type: 'prop', name: 'Scanner', description: 'Hand prop', confidence: 0.8, sceneIndex: 2 }],
            cached: false,
        });
    });


    it('accepts backend null optionals as omitted and rejects unsafe scene indexes', async () => {
        vi.mocked(axios.post).mockResolvedValueOnce({
            data: {
                entities: [{ type: 'location', name: '  Lab  ', description: null, confidence: 0.6, scene_index: null }],
                cached: false,
            },
        });

        await expect(scriptEditorApi.deriveGaps('p', { raw_text_blocks: ['TEXT'] })).resolves.toEqual({
            results: [{ type: 'location', name: 'Lab', confidence: 0.6 }],
            cached: false,
        });

        vi.mocked(axios.post).mockResolvedValueOnce({
            data: {
                entities: [{ type: 'prop', name: 'Scanner', confidence: 0.8, description: { nested: true } }],
                cached: false,
            },
        });

        await expect(scriptEditorApi.deriveGaps('p', { raw_text_blocks: ['TEXT'] })).rejects.toThrow(/description/i);

        vi.mocked(axios.post).mockResolvedValueOnce({
            data: {
                entities: [{ type: 'prop', name: 'Scanner', confidence: 0.8, scene_index: Number.MAX_SAFE_INTEGER + 1 }],
                cached: false,
            },
        });

        await expect(scriptEditorApi.deriveGaps('p', { raw_text_blocks: ['TEXT'] })).rejects.toThrow(/scene_index/i);
    });

    it('rejects malformed derive_gaps responses instead of treating them as empty success', async () => {
        vi.mocked(axios.post).mockResolvedValueOnce({ data: { results: [{ type: 'prop', name: 'Wrong shape' }] } });

        await expect(scriptEditorApi.deriveGaps('p', { raw_text_blocks: ['TEXT'] })).rejects.toThrow(/derive_gaps/i);
    });

    it('sends current editor text blocks with an abort signal and stores returned results', async () => {
        const editor = fakeEditor('CURRENT EDITOR TEXT');
        const derive = vi.spyOn(scriptEditorApi, 'deriveGaps').mockResolvedValueOnce({
            results: [{ type: 'prop', name: 'Scanner', confidence: 0.7, sceneIndex: 0 }],
            cached: false,
        });

        render(<L3Harness editor={editor} projectId="p" />);
        await act(async () => {
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });

        expect(derive).toHaveBeenCalledTimes(1);
        expect(derive).toHaveBeenCalledWith(
            'p',
            expect.objectContaining({
                raw_text_blocks: ['CURRENT EDITOR TEXT'],
                already_extracted: { scenes: ['INT. LAB - DAY'], characters: ['ADA'] },
                gaps: ['props', 'beats', 'locations'],
            }),
            expect.objectContaining({ signal: expect.any(AbortSignal) })
        );
        await act(async () => {
            await flushAsyncWork();
        });
        expect(useEditorStore.getState().l3Results?.[0]?.name).toBe('Scanner');
        expect(useEditorStore.getState().l3Status).toBe('success');
    });

    it('discards late results after the editor input changes and fetches the new key', async () => {
        const editor = fakeEditor('OLD TEXT');
        let resolveFirst!: (value: Awaited<ReturnType<typeof scriptEditorApi.deriveGaps>>) => void;
        const first = new Promise<Awaited<ReturnType<typeof scriptEditorApi.deriveGaps>>>((resolve) => { resolveFirst = resolve; });
        const derive = vi.spyOn(scriptEditorApi, 'deriveGaps')
            .mockReturnValueOnce(first)
            .mockResolvedValueOnce({ results: [{ type: 'location', name: 'New Lab', confidence: 0.9 }], cached: false });

        render(<L3Harness editor={editor} projectId="p" />);
        await act(async () => {
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });
        expect(derive).toHaveBeenCalledTimes(1);

        await act(async () => {
            editor.setText('NEW TEXT');
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });
        expect(derive).toHaveBeenCalledTimes(2);
        await act(async () => {
            resolveFirst({ results: [{ type: 'prop', name: 'Old Scanner', confidence: 0.5 }], cached: false });
            await flushAsyncWork();
        });

        expect(useEditorStore.getState().l3Results?.[0]?.name).toBe('New Lab');
    });


    it('keeps the in-flight signal alive when loading state rerenders the hook', async () => {
        const editor = fakeEditor('CURRENT TEXT');
        let capturedSignal: AbortSignal | undefined;
        let resolveRequest!: (value: Awaited<ReturnType<typeof scriptEditorApi.deriveGaps>>) => void;
        const request = new Promise<Awaited<ReturnType<typeof scriptEditorApi.deriveGaps>>>((resolve) => { resolveRequest = resolve; });
        const derive = vi.spyOn(scriptEditorApi, 'deriveGaps').mockImplementation((_project, _params, options) => {
            capturedSignal = options?.signal;
            return request;
        });

        render(<L3Harness editor={editor} projectId="p" />);
        await act(async () => {
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });

        expect(derive).toHaveBeenCalledTimes(1);
        expect(capturedSignal?.aborted).toBe(false);

        await act(async () => {
            resolveRequest({ results: [{ type: 'prop', name: 'Scanner', confidence: 0.7 }], cached: false });
            await flushAsyncWork();
        });
        expect(useEditorStore.getState().l3Status).toBe('success');
    });

    it('clears stale L3 state when text becomes empty before an in-flight request settles', async () => {
        const editor = fakeEditor('OLD TEXT');
        useEditorStore.setState({
            l3Status: 'success',
            l3Results: [{ type: 'prop', name: 'Old Scanner', confidence: 0.7 }],
            l3LastFetchTime: Date.now(),
        });
        let resolveRequest!: (value: Awaited<ReturnType<typeof scriptEditorApi.deriveGaps>>) => void;
        const request = new Promise<Awaited<ReturnType<typeof scriptEditorApi.deriveGaps>>>((resolve) => { resolveRequest = resolve; });
        vi.spyOn(scriptEditorApi, 'deriveGaps').mockReturnValueOnce(request);

        render(<L3Harness editor={editor} projectId="p" />);
        await act(async () => {
            editor.setText('NEW TEXT');
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });
        expect(useEditorStore.getState().l3Status).toBe('loading');

        await act(async () => {
            editor.setText('   ');
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });

        expect(useEditorStore.getState().l3Status).toBe('idle');
        expect(useEditorStore.getState().l3Results).toBeNull();

        await act(async () => {
            resolveRequest({ results: [{ type: 'prop', name: 'Late Scanner', confidence: 0.8 }], cached: false });
            await flushAsyncWork();
        });
        expect(useEditorStore.getState().l3Results).toBeNull();
        expect(useEditorStore.getState().l3Status).toBe('idle');
    });

    it('clears previous project L3 state when mounted without a project', () => {
        useEditorStore.setState({
            l3Status: 'success',
            l3Results: [{ type: 'beat', name: 'Old Beat', confidence: 0.8 }],
            l3LastFetchTime: Date.now(),
        });

        render(<L3Harness editor={fakeEditor('TEXT')} projectId={null} />);

        expect(useEditorStore.getState().l3Status).toBe('idle');
        expect(useEditorStore.getState().l3Results).toBeNull();
        expect(useEditorStore.getState().l3LastFetchTime).toBeNull();
    });

    it('uses the same-key cache but fetches again for a new project or non-editable editor state', async () => {
        const editor = fakeEditor('SAME TEXT');
        const derive = vi.spyOn(scriptEditorApi, 'deriveGaps').mockResolvedValue({
            results: [{ type: 'beat', name: 'Reveal', confidence: 0.75 }],
            cached: false,
        });

        const view = render(<L3Harness editor={editor} projectId="p" />);
        await act(async () => {
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });
        expect(derive).toHaveBeenCalledTimes(1);

        await act(async () => {
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });
        expect(derive).toHaveBeenCalledTimes(1);

        view.rerender(<L3Harness editor={editor} projectId="next" />);
        await act(async () => {
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });
        expect(derive).toHaveBeenCalledTimes(2);

        editor.setEditableForTest(false);
        await act(async () => {
            editor.emitUpdate();
            vi.advanceTimersByTime(2000);
            await flushAsyncWork();
        });
        expect(derive).toHaveBeenCalledTimes(2);
    });
});
