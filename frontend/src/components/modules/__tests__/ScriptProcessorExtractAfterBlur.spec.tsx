import axios from 'axios';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { API_URL } from '@/lib/api';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore } from '@/store/projectStore';
import { scriptDraftKey, useScriptDraftStore } from '@/store/scriptDraftStore';
import { useToastStore } from '@/store/toastStore';
import ScriptProcessor from '../ScriptProcessor';
import zh from '../../../../messages/zh.json';
import en from '../../../../messages/en.json';

// The editor, revisioned stores and API client remain real. Only the external
// transport and unrelated sibling panels are replaced.
vi.mock('axios', () => ({ default: { put: vi.fn(), post: vi.fn(), get: vi.fn() } }));
vi.mock('@/components/modules/PreviousEpisodeSummary', () => ({ default: () => null }));
vi.mock('@/components/modules/ReconcileModal', () => ({ default: () => null }));

const storageA = '11111111-1111-4111-8111-111111111111';
const storageB = '22222222-2222-4222-8222-222222222222';
const preview = { characters: [{ name: 'Extracted character' }], scenes: [], props: [] };
const rawProject = (text = 'Saved text', revision = 7, id = 'p') => ({
    id, title: id, original_text: text, revision, created_at: 1, updated_at: 2,
    characters: [], scenes: [], props: [], frames: [], video_tasks: [], workflow_mode: 'r2v',
    series_id: null, read_versions: { project: revision, library: 0, series: null },
});

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
    return { promise, resolve, reject };
}

function select(text = 'Saved text', revision = 7, id = 'p') {
    useProjectStore.getState().applyProjectReadSnapshot(id, rawProject(text, revision, id), true);
}

function mount(locale: 'zh' | 'en' = 'en') {
    const messages = locale === 'zh' ? zh : en;
    const view = render(<NextIntlClientProvider locale={locale} messages={messages} timeZone="Asia/Shanghai">
        <ScriptProcessor />
    </NextIntlClientProvider>);
    return {
        ...view, messages,
        editor: () => screen.getByRole('textbox', { name: messages.script.scriptEditor }),
        extract: () => screen.getByRole('button', { name: messages.script.extractEntities }),
    };
}

function clickAfterBlur(editor: HTMLElement, button: HTMLElement) {
    // A browser pointer activation blurs the editor before dispatching click.
    fireEvent.mouseDown(button);
    fireEvent.blur(editor, { relatedTarget: button });
    fireEvent.focus(button, { relatedTarget: editor });
    fireEvent.mouseUp(button);
    fireEvent.click(button);
}

function begin(text = 'Clicked draft', locale: 'zh' | 'en' = 'en') {
    const save = deferred<{ data: ReturnType<typeof rawProject> }>();
    vi.mocked(axios.put).mockReturnValueOnce(save.promise);
    const view = mount(locale);
    fireEvent.focus(view.editor());
    fireEvent.change(view.editor(), { target: { value: text } });
    clickAfterBlur(view.editor(), view.extract());
    expect(axios.put).toHaveBeenCalledWith(`${API_URL}/projects/p/text`, { text, expected_revision: 7 });
    expect(axios.post).not.toHaveBeenCalled();
    return { ...view, save };
}

beforeEach(() => {
    vi.clearAllMocks();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA });
    localStorage.clear();
    useScriptDraftStore.setState({ drafts: {} });
    useToastStore.setState({ toasts: [] });
    useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null,
        readWatermarks: {}, runningOps: {}, isAnalyzing: false,
        pendingExtraction: null, pendingExtractionScript: null, pendingExtractionOwner: null });
    select();
    vi.mocked(axios.post).mockResolvedValue({ data: preview });
    vi.mocked(axios.put).mockImplementation(async (_url, body) => {
        const command = body as { text: string; expected_revision: number };
        return { data: rawProject(command.text, command.expected_revision + 1) };
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
    clearRuntimeEnvironment();
    vi.restoreAllMocks();
});

describe('one explicit extraction click after editor blur', () => {
    it.each(['zh', 'en'] as const)('waits for its own save and extracts once without a second click (%s)', async locale => {
        const { save, messages } = begin('Clicked draft', locale);
        expect(screen.getByRole('button', { name: messages.persistence.saving })).toBeDisabled();
        await act(async () => save.resolve({ data: rawProject('Clicked draft', 8) }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/p/extract_preview`, { text: 'Clicked draft' });
        expect(axios.put).toHaveBeenCalledTimes(1);
        expect(axios.get).not.toHaveBeenCalled();
        expect(useProjectStore.getState().pendingExtractionOwner).toMatchObject({
            projectId: 'p', revision: 8, text: 'Clicked draft',
            environment: getRuntimeEnvironment()!.key, generation: getRuntimeEnvironment()!.generation,
        });
    });

    it('does not start duplicate previews for repeated clicks while the same save is pending', async () => {
        const { save } = begin();
        const button = screen.getByRole('button', { name: en.persistence.saving });
        fireEvent.click(button);
        fireEvent.click(button);
        await act(async () => save.resolve({ data: rawProject('Clicked draft', 8) }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.put).toHaveBeenCalledTimes(1);
    });

    it.each([409, 503])('cancels the click when the save fails with HTTP %s without retry or read', async status => {
        const { save, editor } = begin();
        await act(async () => save.reject({ response: { status } }));
        expect(await screen.findByRole('alert')).toBeVisible();
        expect(editor()).toHaveValue('Clicked draft');
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.get).not.toHaveBeenCalled();
        expect(axios.put).toHaveBeenCalledTimes(1);
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')].revision).toBe(7);
    });

    it('does not extract a different text returned by the save acknowledgement', async () => {
        const { save } = begin();
        await act(async () => save.resolve({ data: rawProject('Unexpected saved text', 8) }));
        expect(axios.post).not.toHaveBeenCalled();
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
    });

    it('does not extract when the save acknowledgement belongs to another owner', async () => {
        const { save } = begin();
        await act(async () => save.resolve({ data: rawProject('Clicked draft', 8, 'other') }));
        expect(await screen.findByRole('alert')).toBeVisible();
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')].revision).toBe(7);
        expect(useProjectStore.getState().currentProject?.id).toBe('p');
        expect(axios.post).not.toHaveBeenCalled();
    });

    it.each([false, true])('cancels after editing while saving even if text returns to its earlier value: %s', async returnToOriginal => {
        const { save, editor } = begin();
        fireEvent.change(editor(), { target: { value: 'Newer unsaved draft' } });
        if (returnToOriginal) fireEvent.change(editor(), { target: { value: 'Clicked draft' } });
        await act(async () => save.resolve({ data: rawProject('Clicked draft', 8) }));
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.put).toHaveBeenCalledTimes(1);
        expect(editor()).toHaveValue(returnToOriginal ? 'Clicked draft' : 'Newer unsaved draft');
    });

    it('cancels after a project A to B to A change even when React batches both changes', async () => {
        const { save } = begin();
        act(() => { select('Other project', 2, 'other'); select(); });
        await act(async () => save.resolve({ data: rawProject('Clicked draft', 8) }));
        expect(useProjectStore.getState().currentProject?.id).toBe('p');
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('cancels after leaving and reactivating the same environment', async () => {
        const { save } = begin();
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: storageB });
            activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA });
            select();
        });
        await act(async () => save.resolve({ data: rawProject('Clicked draft', 8) }));
        expect(axios.post).not.toHaveBeenCalled();
        expect(useProjectStore.getState().pendingExtractionOwner).toBeNull();
    });

    it('does not resurrect the click after unmount and remount', async () => {
        const { save, unmount } = begin();
        unmount();
        const reopened = mount();
        await act(async () => save.resolve({ data: rawProject('Clicked draft', 8) }));
        expect(reopened.editor()).toHaveValue('Clicked draft');
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('does not borrow a newer project version when the own save acknowledgement is older', async () => {
        const { save } = begin();
        act(() => select('Another editor saved', 9));
        await act(async () => save.resolve({ data: rawProject('Clicked draft', 8) }));
        expect(useProjectStore.getState().currentProject?.revision).toBe(9);
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')].revision).toBe(8);
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('waits for the existing blur queue to acknowledge the exact clicked draft', async () => {
        const first = deferred<{ data: ReturnType<typeof rawProject> }>();
        const second = deferred<{ data: ReturnType<typeof rawProject> }>();
        vi.mocked(axios.put).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
        const view = mount();
        fireEvent.change(view.editor(), { target: { value: 'Earlier blurred text' } });
        fireEvent.blur(view.editor());
        fireEvent.change(view.editor(), { target: { value: 'Clicked draft' } });
        clickAfterBlur(view.editor(), view.extract());
        await act(async () => first.resolve({ data: rawProject('Earlier blurred text', 8) }));
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.put).toHaveBeenLastCalledWith(`${API_URL}/projects/p/text`, { text: 'Clicked draft', expected_revision: 8 });
        await act(async () => second.resolve({ data: rawProject('Clicked draft', 9) }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(useProjectStore.getState().pendingExtractionOwner).toMatchObject({ text: 'Clicked draft', revision: 9 });
        expect(axios.put).toHaveBeenCalledTimes(2);
    });

    it('rejects a late preview after the draft changes and returns following its own save', async () => {
        const response = deferred<{ data: typeof preview }>();
        vi.mocked(axios.post).mockReturnValueOnce(response.promise);
        const { save, editor } = begin();
        await act(async () => save.resolve({ data: rawProject('Clicked draft', 8) }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        fireEvent.change(editor(), { target: { value: 'Changed during extraction' } });
        fireEvent.change(editor(), { target: { value: 'Clicked draft' } });
        await act(async () => response.resolve({ data: preview }));
        expect(useProjectStore.getState().pendingExtractionOwner).toBeNull();
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
    });

    it('preserves direct extraction of unchanged text without writing or reading a version', async () => {
        const view = mount();
        fireEvent.click(view.extract());
        await waitFor(() => expect(useProjectStore.getState().pendingExtractionOwner?.revision).toBe(7));
        expect(axios.put).not.toHaveBeenCalled();
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('does not introduce a save when extraction was activated without editor blur', async () => {
        const view = mount();
        fireEvent.change(view.editor(), { target: { value: 'Explicit retained draft' } });
        fireEvent.click(view.extract());
        await waitFor(() => expect(useProjectStore.getState().pendingExtractionOwner).toMatchObject({ text: 'Explicit retained draft', revision: 7 }));
        expect(axios.put).not.toHaveBeenCalled();
        expect(axios.get).not.toHaveBeenCalled();
    });
});
