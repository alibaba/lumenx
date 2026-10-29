import axios from 'axios';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { API_URL } from '@/lib/api';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore } from '@/store/projectStore';
import { scriptDraftKey, useScriptDraftStore } from '@/store/scriptDraftStore';
import { useToastStore } from '@/store/toastStore';
import ProjectClient from '../ProjectClient';
import zh from '../../../../messages/zh.json';
import en from '../../../../messages/en.json';

// The actual script editor, confirmation modal, API client and both stores stay
// real. Only unrelated project steps and the external HTTP boundary are replaced.
vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('@/components/layout/EpisodeMiniList', () => ({ default: () => null }));
vi.mock('@/components/modules/PreviousEpisodeSummary', () => ({ default: () => null }));
vi.mock('@/components/modules/ReconcileModal', () => ({ default: () => null }));
vi.mock('@/components/modules/Cast', () => ({ default: () => null }));
vi.mock('@/components/modules/VideoGenerator', () => ({ default: () => null }));
vi.mock('@/components/modules/VideoAssembly', () => ({ default: () => null }));
vi.mock('@/components/modules/ConsistencyVault', () => ({ default: () => null }));
vi.mock('@/components/modules/ArtDirection', () => ({ default: () => null }));
vi.mock('@/components/modules/StoryboardComposer', () => ({ default: () => null }));
vi.mock('@/components/modules/StoryboardR2V', () => ({ default: () => null }));
vi.mock('@/components/common/ModelSettingsModal', () => ({ default: () => null }));
vi.mock('@/components/project/EnvConfigDialog', () => ({ default: () => null }));
vi.mock('@/components/project/PromptConfigModal', () => ({ default: () => null }));
vi.mock('axios', () => ({ default: { put: vi.fn(), get: vi.fn(), post: vi.fn() } }));

const STORAGE_ID = '11111111-1111-4111-8111-111111111111';
const preview = { characters: [{ name: 'Extracted character' }], scenes: [], props: [] };
const rawProject = (text = 'Saved text', revision = 7, seriesId?: string) => ({
    id: 'p', title: 'Project', original_text: text, revision, created_at: 1, updated_at: 2,
    characters: [], scenes: [], props: [], frames: [], video_tasks: [], workflow_mode: 'r2v',
    series_id: seriesId ?? null,
    read_versions: { project: revision, library: 0, series: seriesId ? { id: seriesId, revision: 1 } : null },
});
let server = rawProject();

function httpError(status: number) {
    return { response: { status, data: { code: 'storage_conflict', detail: 'Saved version changed', commit_uncertain: false } } };
}

beforeEach(() => {
    vi.clearAllMocks();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_ID });
    localStorage.clear();
    server = rawProject();
    useScriptDraftStore.setState({ drafts: {} });
    useToastStore.setState({ toasts: [] });
    useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null, seriesList: [],
        pendingExtraction: null, pendingExtractionScript: null, pendingExtractionOwner: null,
        readWatermarks: {}, runningOps: {}, isAnalyzing: false, isLoading: false });
    useProjectStore.getState().applyProjectReadSnapshot('p', server, true);
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(server), { status: 200 })));
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/series/s')
        ? { id: 's', title: 'Series', revision: 1, content_mode: 'scripted', episode_ids: ['p'], characters: [], scenes: [], props: [] }
        : { ...server } }));
    vi.mocked(axios.post).mockResolvedValue({ data: preview });
    vi.mocked(axios.put).mockImplementation(async (_url, body) => {
        const request = body as { expected_revision?: number; text: string };
        if (request.expected_revision !== server.revision) throw httpError(409);
        server = rawProject(request.text, server.revision + 1, server.series_id ?? undefined);
        return { data: { ...server } };
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
    clearRuntimeEnvironment();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

async function mount(locale: 'zh' | 'en', expectedText = 'Saved text') {
    const messages = locale === 'zh' ? zh : en;
    render(<NextIntlClientProvider locale={locale} messages={messages} timeZone="Asia/Shanghai">
        <ProjectClient id="p" />
    </NextIntlClientProvider>);
    await waitFor(() => expect(screen.getByRole('textbox', { name: messages.script.scriptEditor })).toHaveValue(expectedText));
    return messages;
}

describe.each(['zh', 'en'] as const)('real ProjectClient reparse baseline (%s)', locale => {
    it('sends the preview owner revision after another tab saves', async () => {
        const messages = await mount(locale);
        fireEvent.click(screen.getByRole('button', { name: messages.script.extractEntities }));
        await screen.findByRole('button', { name: messages.script.extractApply });
        expect(useProjectStore.getState().pendingExtractionOwner?.revision).toBe(7);
        server = rawProject('New content from another tab', 8);

        fireEvent.click(screen.getByRole('button', { name: messages.script.extractApply }));

        await waitFor(() => expect(axios.put).toHaveBeenCalledWith(`${API_URL}/projects/p/reparse`, {
            text: 'Saved text', expected_revision: 7,
        }));
        expect(axios.put).toHaveBeenCalledTimes(1);
        expect(axios.get).not.toHaveBeenCalled();
        expect(server.original_text).toBe('New content from another tab');
    });

    it('shows a version conflict while retaining text and preview without retry', async () => {
        const messages = await mount(locale);
        fireEvent.click(screen.getByRole('button', { name: messages.script.extractEntities }));
        await screen.findByRole('button', { name: messages.script.extractApply });
        server = rawProject('New content from another tab', 8);
        fireEvent.click(screen.getByRole('button', { name: messages.script.extractApply }));

        expect((await screen.findAllByText(messages.persistence.conflict))[0]).toBeVisible();
        expect(screen.getByRole('textbox', { name: messages.script.scriptEditor })).toHaveValue('Saved text');
        expect(useProjectStore.getState().pendingExtraction).toEqual(preview);
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')].revision).toBe(7);
        expect(axios.put).toHaveBeenCalledTimes(1);
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('does not lend a newly read project revision to an older dirty draft', async () => {
        const messages = await mount(locale);
        fireEvent.change(screen.getByRole('textbox', { name: messages.script.scriptEditor }), { target: { value: 'Draft based on revision seven' } });
        server = rawProject('Another editor saved revision eight', 8);
        act(() => useProjectStore.getState().applyProjectReadSnapshot('p', server, true));
        expect(useProjectStore.getState().currentProject?.revision).toBe(8);
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')].revision).toBe(7);

        fireEvent.click(screen.getByRole('button', { name: messages.script.extractEntities }));
        await act(async () => {});

        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.put).not.toHaveBeenCalled();
        expect(screen.getByText(messages.persistence.conflict)).toBeVisible();
        expect(screen.getByRole('textbox', { name: messages.script.scriptEditor })).toHaveValue('Draft based on revision seven');
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')].revision).toBe(7);
        expect(getRuntimeEnvironment()?.key).toBe(useProjectStore.getState().currentProject?.readEnvironment);
    });

    it('reads without writing, then requires a fresh preview and explicit confirmation of the retained draft', async () => {
        const messages = await mount(locale);
        fireEvent.click(screen.getByRole('button', { name: messages.script.extractEntities }));
        await screen.findByRole('button', { name: messages.script.extractApply });
        server = rawProject('New content from another tab', 8);
        fireEvent.click(screen.getByRole('button', { name: messages.script.extractApply }));
        await screen.findAllByText(messages.persistence.conflict);

        fireEvent.click(screen.getByRole('button', { name: messages.script.extractionReviewDraft }));
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: messages.script.readLatest }));

        expect(await screen.findByText('New content from another tab')).toBeVisible();
        expect(axios.get).toHaveBeenCalledTimes(1);
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.put).toHaveBeenCalledTimes(1);
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')]).toMatchObject({ text: 'Saved text', revision: 7, error: 'conflict' });
        fireEvent.blur(screen.getByRole('textbox', { name: messages.script.scriptEditor }));
        expect(axios.put).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByRole('button', { name: messages.script.reextractRetainedDraft }));
        await screen.findByRole('button', { name: messages.script.extractApply });
        expect(axios.post).toHaveBeenLastCalledWith(`${API_URL}/projects/p/extract_preview`, { text: 'Saved text' });
        expect(axios.post).toHaveBeenCalledTimes(2);
        expect(axios.put).toHaveBeenCalledTimes(1);
        expect(useProjectStore.getState().pendingExtractionOwner?.revision).toBe(8);

        fireEvent.click(screen.getByRole('button', { name: messages.script.extractApply }));
        await waitFor(() => expect(useProjectStore.getState().pendingExtraction).toBeNull());
        expect(axios.put).toHaveBeenLastCalledWith(`${API_URL}/projects/p/reparse`, { text: 'Saved text', expected_revision: 8 });
        expect(server.original_text).toBe('Saved text');
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')]).toMatchObject({
            text: 'Saved text', savedText: 'Saved text', revision: 9, error: null,
        });
        fireEvent.blur(screen.getByRole('textbox', { name: messages.script.scriptEditor }));
        expect(axios.put).toHaveBeenCalledTimes(2);
    });

    it('keeps a failed read from granting the retained draft a new write version', async () => {
        const messages = await mount(locale);
        fireEvent.change(screen.getByRole('textbox', { name: messages.script.scriptEditor }), { target: { value: 'Retained draft' } });
        server = rawProject('Server content selected by the author', 8);
        act(() => useProjectStore.getState().applyProjectReadSnapshot('p', server, true));
        fireEvent.click(screen.getByRole('button', { name: messages.script.extractEntities }));
        vi.mocked(axios.get).mockRejectedValueOnce(httpError(503));

        fireEvent.click(screen.getByRole('button', { name: messages.script.readLatest }));

        expect(await screen.findByText(messages.script.readLatestFailed)).toBeVisible();
        expect(screen.queryByRole('button', { name: messages.script.reextractRetainedDraft })).not.toBeInTheDocument();
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')]).toMatchObject({ text: 'Retained draft', revision: 7, error: 'conflict' });
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.put).not.toHaveBeenCalled();
    });

    it('adopts saved content only after an explicit choice and without another extraction', async () => {
        const messages = await mount(locale);
        fireEvent.change(screen.getByRole('textbox', { name: messages.script.scriptEditor }), { target: { value: 'Retained draft' } });
        server = rawProject('Server content selected by the author', 8);
        act(() => useProjectStore.getState().applyProjectReadSnapshot('p', server, true));
        fireEvent.click(screen.getByRole('button', { name: messages.script.extractEntities }));
        fireEvent.click(screen.getByRole('button', { name: messages.script.readLatest }));
        await screen.findByText('Server content selected by the author');

        fireEvent.click(screen.getByRole('button', { name: messages.script.useSavedText }));

        expect(screen.getByRole('textbox', { name: messages.script.scriptEditor })).toHaveValue('Server content selected by the author');
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')]).toMatchObject({ text: 'Server content selected by the author', savedText: 'Server content selected by the author', revision: 8, error: null });
        fireEvent.blur(screen.getByRole('textbox', { name: messages.script.scriptEditor }));
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.put).not.toHaveBeenCalled();
    });

    it('waits for its own blur acknowledgement before allowing an extraction', async () => {
        const messages = await mount(locale);
        let finish!: (value: { data: ReturnType<typeof rawProject> }) => void;
        vi.mocked(axios.put).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
        const editor = screen.getByRole('textbox', { name: messages.script.scriptEditor });
        fireEvent.change(editor, { target: { value: 'Edited text' } });
        fireEvent.blur(editor);

        fireEvent.click(screen.getByRole('button', { name: messages.script.extractEntities }));

        expect(axios.post).not.toHaveBeenCalled();
        await act(async () => finish({ data: rawProject('Edited text', 8) }));
        fireEvent.click(screen.getByRole('button', { name: messages.script.extractEntities }));
        await screen.findByRole('button', { name: messages.script.extractApply });
        expect(useProjectStore.getState().pendingExtractionOwner).toMatchObject({ text: 'Edited text', revision: 8 });
    });
});

function switchRuntime(storageId: string, text: string, revision: number) {
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    server = rawProject(text, revision);
    useProjectStore.setState({ projects: [], currentProject: null, currentSeries: null, readWatermarks: {}, runningOps: {},
        isAnalyzing: false, pendingExtraction: null, pendingExtractionScript: null, pendingExtractionOwner: null });
    useProjectStore.getState().applyProjectReadSnapshot('p', server, true);
}

describe('script draft operations across environment activations', () => {
    it('does not apply an old recovery read after changing environments and returning to the same project', async () => {
        await mount('en');
        fireEvent.change(screen.getByRole('textbox', { name: en.script.scriptEditor }), { target: { value: 'Retained A draft' } });
        server = rawProject('A updated elsewhere', 8);
        act(() => useProjectStore.getState().applyProjectReadSnapshot('p', server, true));
        fireEvent.click(screen.getByRole('button', { name: en.script.extractEntities }));
        let resolveOld!: (value: { data: ReturnType<typeof rawProject> }) => void;
        vi.mocked(axios.get).mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
        fireEvent.click(screen.getByRole('button', { name: en.script.readLatest }));
        cleanup();
        act(() => switchRuntime('22222222-2222-4222-8222-222222222222', 'B saved text', 2));
        await mount('en', 'B saved text');
        cleanup();
        act(() => switchRuntime(STORAGE_ID, 'Latest A content', 9));
        await mount('en', 'Retained A draft');

        await act(async () => resolveOld({ data: rawProject('Old read with higher revision', 42) }));

        expect(useProjectStore.getState().currentProject?.revision).toBe(9);
        expect(screen.queryByRole('button', { name: en.script.reextractRetainedDraft })).not.toBeInTheDocument();
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')]).toMatchObject({ text: 'Retained A draft', revision: 7, error: 'conflict' });
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.put).not.toHaveBeenCalled();
    });

    it('keeps the new preview busy when an old activation finishes late', async () => {
        await mount('en');
        let resolveOld!: (value: { data: typeof preview }) => void;
        let resolveNew!: (value: { data: typeof preview }) => void;
        vi.mocked(axios.post).mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
        fireEvent.click(screen.getByRole('button', { name: en.script.extractEntities }));
        cleanup();
        act(() => switchRuntime('22222222-2222-4222-8222-222222222222', 'B saved text', 2));
        await mount('en', 'B saved text');
        cleanup();
        act(() => switchRuntime(STORAGE_ID, 'New A saved text', 9));
        await mount('en', 'New A saved text');
        vi.mocked(axios.post).mockReturnValueOnce(new Promise(resolve => { resolveNew = resolve; }));
        fireEvent.click(screen.getByRole('button', { name: en.script.extractEntities }));
        expect(axios.post).toHaveBeenCalledTimes(2);
        const operationKey = `script-extraction-preview:${encodeURIComponent(getRuntimeEnvironment()!.key)}:p`;

        await act(async () => resolveOld({ data: preview }));

        expect(useProjectStore.getState().runningOps[operationKey]).toBe(true);
        expect(useProjectStore.getState().isAnalyzing).toBe(true);
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
        await act(async () => resolveNew({ data: preview }));
        expect(useProjectStore.getState().pendingExtractionOwner?.revision).toBe(9);
    });

    it.each(['resolve', 'reject'] as const)('ignores an old blur %s without clearing the new save or its queued text', async outcome => {
        await mount('en');
        let resolveOld!: (value: { data: ReturnType<typeof rawProject> }) => void;
        let rejectOld!: (reason: unknown) => void;
        let resolveNew!: (value: { data: ReturnType<typeof rawProject> }) => void;
        vi.mocked(axios.put).mockReturnValueOnce(new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; }));
        const oldEditor = screen.getByRole('textbox', { name: en.script.scriptEditor });
        fireEvent.change(oldEditor, { target: { value: 'Unconfirmed A draft' } });
        fireEvent.blur(oldEditor);
        expect(axios.put).toHaveBeenCalledTimes(1);

        cleanup();
        act(() => switchRuntime('22222222-2222-4222-8222-222222222222', 'B saved text', 2));
        await mount('en', 'B saved text');
        cleanup();
        act(() => switchRuntime(STORAGE_ID, 'Latest A saved text', 8));
        await mount('en', 'Unconfirmed A draft');
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')]).toMatchObject({ saving: false, error: 'conflict' });
        fireEvent.click(screen.getByRole('button', { name: en.script.readLatest }));
        await screen.findByText('Latest A saved text');
        fireEvent.click(screen.getByRole('button', { name: en.script.useSavedText }));

        vi.mocked(axios.put).mockReturnValueOnce(new Promise(resolve => { resolveNew = resolve; }));
        const editor = screen.getByRole('textbox', { name: en.script.scriptEditor });
        fireEvent.change(editor, { target: { value: 'New A draft' } });
        fireEvent.blur(editor);
        fireEvent.change(editor, { target: { value: 'Queued A draft' } });
        fireEvent.blur(editor);
        expect(axios.put).toHaveBeenCalledTimes(2);
        await act(async () => {
            if (outcome === 'resolve') resolveOld({ data: rawProject('Unconfirmed A draft', 8) });
            else rejectOld(httpError(503));
        });
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')]).toMatchObject({ saving: true, error: null, text: 'Queued A draft', revision: 8 });
        expect(useProjectStore.getState().currentProject?.originalText).toBe('Latest A saved text');
        server = rawProject('New A draft', 9);
        await act(async () => resolveNew({ data: { ...server } }));
        await waitFor(() => expect(axios.put).toHaveBeenCalledTimes(3));
        expect(axios.put).toHaveBeenLastCalledWith(`${API_URL}/projects/p/text`, { text: 'Queued A draft', expected_revision: 9 });
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')]).toMatchObject({ saving: false, error: null, savedText: 'Queued A draft', revision: 10 });
    });

    it('does not share an old confirmation promise or clear the new confirmation lock after A to B to A', async () => {
        await mount('en');
        fireEvent.click(screen.getByRole('button', { name: en.script.extractEntities }));
        await screen.findByRole('button', { name: en.script.extractApply });
        let resolveOld!: (value: { data: ReturnType<typeof rawProject> }) => void;
        let resolveNew!: (value: { data: ReturnType<typeof rawProject> }) => void;
        vi.mocked(axios.put).mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
        fireEvent.click(screen.getByRole('button', { name: en.script.extractApply }));
        expect(axios.put).toHaveBeenCalledTimes(1);

        cleanup();
        act(() => switchRuntime('22222222-2222-4222-8222-222222222222', 'B saved text', 2));
        await mount('en', 'B saved text');
        cleanup();
        act(() => switchRuntime(STORAGE_ID, 'New A saved text', 9));
        await mount('en', 'New A saved text');
        fireEvent.click(screen.getByRole('button', { name: en.script.extractEntities }));
        await screen.findByRole('button', { name: en.script.extractApply });
        vi.mocked(axios.put).mockReturnValueOnce(new Promise(resolve => { resolveNew = resolve; }));
        fireEvent.click(screen.getByRole('button', { name: en.script.extractApply }));
        expect(axios.put).toHaveBeenCalledTimes(2);
        const newOwner = useProjectStore.getState().pendingExtractionOwner;
        const operationKey = `script-extraction-confirm:${encodeURIComponent(getRuntimeEnvironment()!.key)}:p`;

        await act(async () => resolveOld({ data: rawProject('Saved text', 8) }));

        expect(useProjectStore.getState().pendingExtractionOwner).toBe(newOwner);
        expect(useProjectStore.getState().runningOps[operationKey]).toBe(true);
        expect(useProjectStore.getState().isAnalyzing).toBe(true);
        expect(useProjectStore.getState().currentProject?.revision).toBe(9);
        await act(async () => resolveNew({ data: rawProject('New A saved text', 10) }));
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
        expect(useProjectStore.getState().currentProject?.revision).toBe(10);
    });
});

describe('confirmation outcomes belong to the actual project command', () => {
    it('does not open reconciliation for a new environment after an old confirmation resolves', async () => {
        await mount('en');
        fireEvent.click(screen.getByRole('button', { name: en.script.extractEntities }));
        await screen.findByRole('button', { name: en.script.extractApply });
        let resolveOld!: (value: { data: ReturnType<typeof rawProject> }) => void;
        vi.mocked(axios.put).mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
        fireEvent.click(screen.getByRole('button', { name: en.script.extractApply }));
        cleanup();
        act(() => {
            switchRuntime('22222222-2222-4222-8222-222222222222', 'Other environment episode', 2);
            server = rawProject('Other environment episode', 2, 's');
            useProjectStore.getState().applyProjectReadSnapshot('p', server, true);
        });
        await mount('en', 'Other environment episode');
        const dispatch = vi.spyOn(document, 'dispatchEvent');

        await act(async () => resolveOld({ data: rawProject('Saved text', 8) }));

        expect(dispatch).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'lumenx:openReconcile' }));
        expect(useProjectStore.getState().currentProject).toMatchObject({ originalText: 'Other environment episode', revision: 2 });
        expect(axios.put).toHaveBeenCalledTimes(1);
    });

    it('requires another read when the reviewed version changes before the explicit choice', async () => {
        await mount('en');
        fireEvent.change(screen.getByRole('textbox', { name: en.script.scriptEditor }), { target: { value: 'Retained draft' } });
        server = rawProject('Saved elsewhere', 8);
        act(() => useProjectStore.getState().applyProjectReadSnapshot('p', server, true));
        fireEvent.click(screen.getByRole('button', { name: en.script.extractEntities }));
        fireEvent.click(screen.getByRole('button', { name: en.script.readLatest }));
        await screen.findByRole('button', { name: en.script.reextractRetainedDraft });
        server = rawProject('Changed again', 9);
        act(() => useProjectStore.getState().applyProjectReadSnapshot('p', server, true));

        fireEvent.click(screen.getByRole('button', { name: en.script.reextractRetainedDraft }));

        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('p')]).toMatchObject({ text: 'Retained draft', revision: 7, error: 'conflict' });
        expect(screen.getByRole('button', { name: en.script.readLatest })).toBeVisible();
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.put).not.toHaveBeenCalled();
    });

    it.each([502, 503])('retains the pending preview after HTTP %s and does not retry', async status => {
        await mount('en');
        fireEvent.click(screen.getByRole('button', { name: en.script.extractEntities }));
        await screen.findByRole('button', { name: en.script.extractApply });
        vi.mocked(axios.put).mockRejectedValueOnce(httpError(status));

        fireEvent.click(screen.getByRole('button', { name: en.script.extractApply }));

        expect(await screen.findByRole('alert')).toHaveTextContent(status === 503 ? en.persistence.unavailable : en.persistence.failed);
        expect(useProjectStore.getState().pendingExtraction).toEqual(preview);
        expect(axios.put).toHaveBeenCalledTimes(1);
        expect(axios.get).not.toHaveBeenCalled();
    });

    it.each(['blocked', 'committed'] as const)('opens series reconciliation only for a verified committed result: %s', async outcome => {
        server = rawProject('Saved text', 7, 's');
        act(() => useProjectStore.getState().applyProjectReadSnapshot('p', server, true));
        await mount('en');
        fireEvent.click(screen.getByRole('button', { name: en.script.extractEntities }));
        await screen.findByRole('button', { name: en.script.extractApply });
        const dispatch = vi.spyOn(document, 'dispatchEvent');
        if (outcome === 'blocked') {
            const key = `script-extraction-confirm:${encodeURIComponent(getRuntimeEnvironment()!.key)}:p`;
            act(() => useProjectStore.getState().setRunningOp(key, true));
        }

        fireEvent.click(screen.getByRole('button', { name: en.script.extractApply }));
        await act(async () => {});

        if (outcome === 'committed') {
            expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ type: 'lumenx:openReconcile' }));
            expect(useProjectStore.getState().currentProject?.revision).toBe(8);
        } else {
            expect(dispatch).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'lumenx:openReconcile' }));
            expect(axios.put).not.toHaveBeenCalled();
            expect(useProjectStore.getState().pendingExtraction).toEqual(preview);
        }
    });
});
