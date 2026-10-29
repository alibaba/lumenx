import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { API_URL, api } from '@/lib/api';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore, type Project } from '@/store/projectStore';
import { useScriptDraftStore } from '@/store/scriptDraftStore';
import { useToastStore } from '@/store/toastStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import ScriptProcessor from '../ScriptProcessor';
import { deferred } from './frame-command-fixtures';

vi.mock('@/lib/api', () => ({
    API_URL: 'http://test.invalid',
    crudApi: {},
    api: {
        extractPreview: vi.fn(),
        reparseProject: vi.fn(),
        updateScriptText: vi.fn(),
        getProject: vi.fn(),
        getNextEpisodeHook: vi.fn(async () => ({ has_text: false, hook: null, stale: false })),
        getPreviousEpisodeSummary: vi.fn(async () => ({ has_previous: false })),
    },
}));

const preview = { characters: [{ id: 'c1' }], scenes: [{ id: 's1' }], props: [{ id: 'p1' }] };
const STORAGE_ID = '11111111-1111-4111-8111-111111111111';
const currentEnvKey = () => getRuntimeEnvironment()?.key ?? API_URL;
const currentEnvGeneration = () => getRuntimeEnvironment()?.generation ?? 0;

function project(id = 'a', text = '已保存原文', revision = 7): Project & { original_text: string; created_at: number; updated_at: number } {
    return {
        id, title: `Project ${id}`, originalText: text, original_text: text, revision, readEnvironment: currentEnvKey(),
        created_at: 1, updated_at: 2, video_tasks: [],
        characters: [{ id: 'shared', name: 'shared', source: 'series' }], scenes: [], props: [], frames: [], status: 'draft',
        createdAt: '', updatedAt: '',
    };
}

function rawProject(id = 'a', text = 'reparsed', revision = 8) {
    return { ...project(id, text, revision), original_text: text };
}

function seed(current: Project = project()) {
    useScriptDraftStore.setState({ drafts: {} });
    useProjectStore.setState({
        projects: [current],
        currentProject: current,
        currentSeries: null,
        pendingExtraction: null,
        pendingExtractionScript: null,
        pendingExtractionOwner: null,
        readWatermarks: {},
        runningOps: {},
        isAnalyzing: false,
    } as Partial<ReturnType<typeof useProjectStore.getState>>);
}

function extractButton() {
    return screen.getByRole('button', { name: /提取实体|智能分析中/ });
}

beforeEach(() => {
    vi.clearAllMocks();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_ID });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    localStorage.clear();
    useToastStore.setState({ toasts: [] });
    seed();
});

afterEach(() => {
    clearRuntimeEnvironment();
    vi.restoreAllMocks();
});

describe('Script extraction preview and reparse boundaries', () => {
    it('discards a late extraction preview after switching projects', async () => {
        const pending = deferred<typeof preview>();
        vi.mocked(api.extractPreview).mockReturnValueOnce(pending.promise);
        renderWithIntl(<ScriptProcessor />);

        fireEvent.click(extractButton());
        expect(api.extractPreview).toHaveBeenCalledWith('a', '已保存原文');
        act(() => useProjectStore.setState({ currentProject: project('b', 'B text', 3), projects: [project('b', 'B text', 3)] }));
        await act(async () => pending.resolve(preview));

        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
        expect(useProjectStore.getState().pendingExtractionScript).toBeNull();
        expect(useProjectStore.getState().isAnalyzing).toBe(false);
        expect(api.reparseProject).not.toHaveBeenCalled();
    });

    it('coalesces duplicate extraction clicks across remount without accepting the unmounted preview', async () => {
        const pending = deferred<typeof preview>();
        vi.mocked(api.extractPreview).mockReturnValue(pending.promise);
        const view = renderWithIntl(<ScriptProcessor />);

        fireEvent.click(extractButton());
        view.unmount();
        renderWithIntl(<ScriptProcessor />);
        fireEvent.click(extractButton());

        expect(api.extractPreview).toHaveBeenCalledTimes(1);
        await act(async () => pending.resolve(preview));
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
        expect(useProjectStore.getState().isAnalyzing).toBe(false);
    });

    it('does not confirm an extraction against a newly selected project', async () => {
        const a = project('a', 'A text', 7);
        const b = project('b', 'B text', 4);
        useProjectStore.setState({
            projects: [a, b],
            currentProject: b,
            pendingExtraction: preview,
            pendingExtractionScript: 'A edited text',
            pendingExtractionOwner: { projectId: 'a', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 7, text: 'A edited text', sessionId: 'test-session' },
            isAnalyzing: false,
            readWatermarks: {},
            runningOps: {},
        } as Partial<ReturnType<typeof useProjectStore.getState>>);

        await expect(useProjectStore.getState().confirmExtraction()).rejects.toThrow(/current|stale|project/i);

        expect(api.reparseProject).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(useProjectStore.getState().projects.find(item => item.id === 'a')?.revision).toBe(7);
    });

    it('applies a confirmed extraction through the raw project ACK gate', async () => {
        const initial = { ...project('a', 'A text', 7), read_versions: { project: 7, series: null, library: 4 } };
        useProjectStore.setState({
            projects: [initial], currentProject: initial,
            pendingExtraction: preview, pendingExtractionScript: 'A edited text',
            pendingExtractionOwner: { projectId: 'a', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 7, text: 'A edited text', sessionId: 'test-session' },
            isAnalyzing: false, readWatermarks: {}, runningOps: {},
        } as Partial<ReturnType<typeof useProjectStore.getState>>);
        vi.mocked(api.reparseProject).mockResolvedValueOnce(rawProject('a', 'A edited text', 8));

        await useProjectStore.getState().confirmExtraction();

        expect(api.reparseProject).toHaveBeenCalledWith('a', 'A edited text', 7);
        expect(useProjectStore.getState().currentProject?.revision).toBe(8);
        expect(useProjectStore.getState().currentProject?.read_versions?.project).toBe(8);
        expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(4);
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
        expect(useProjectStore.getState().pendingExtractionOwner).toBeNull();
    });

    it('keeps pending extraction and project state when reparse ACK is malformed', async () => {
        const initial = project('a', 'A text', 7);
        useProjectStore.setState({
            projects: [initial], currentProject: initial,
            pendingExtraction: preview, pendingExtractionScript: 'A edited text',
            pendingExtractionOwner: { projectId: 'a', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 7, text: 'A edited text', sessionId: 'test-session' },
            isAnalyzing: false, readWatermarks: {}, runningOps: {},
        } as Partial<ReturnType<typeof useProjectStore.getState>>);
        vi.mocked(api.reparseProject).mockResolvedValueOnce({ revision: 8, original_text: 'A edited text' });

        await expect(useProjectStore.getState().confirmExtraction()).rejects.toThrow();

        expect(useProjectStore.getState().currentProject).toMatchObject(initial);
        expect(useProjectStore.getState().pendingExtraction).toEqual(preview);
        expect(useProjectStore.getState().pendingExtractionScript).toBe('A edited text');
        expect(useProjectStore.getState().isAnalyzing).toBe(false);
    });

    it('does not send extraction for a project without a committed API environment', () => {
        seed({ ...project('a', 'A text', 7), readEnvironment: undefined });
        renderWithIntl(<ScriptProcessor />);

        fireEvent.click(extractButton());

        expect(api.extractPreview).not.toHaveBeenCalled();
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
        expect(useProjectStore.getState().isAnalyzing).toBe(false);
    });

    it('does not accept an old A extraction after navigating A to B and back to the same revision', async () => {
        const pending = deferred<typeof preview>();
        vi.mocked(api.extractPreview).mockReturnValueOnce(pending.promise);
        const view = renderWithIntl(<ScriptProcessor />);

        fireEvent.click(extractButton());
        view.unmount();
        act(() => useProjectStore.setState({ currentProject: project('b', 'B text', 4), projects: [project('a', '已保存原文', 7), project('b', 'B text', 4)] }));
        renderWithIntl(<ScriptProcessor />).unmount();
        act(() => useProjectStore.setState({ currentProject: project('a', '已保存原文', 7), projects: [project('a', '已保存原文', 7), project('b', 'B text', 4)] }));
        renderWithIntl(<ScriptProcessor />);

        await act(async () => pending.resolve(preview));

        expect(useProjectStore.getState().currentProject?.id).toBe('a');
        expect(useProjectStore.getState().pendingExtraction).toBeNull();
    });

    it('does not let an old failed extraction clear a newer project operation', async () => {
        const pending = deferred<typeof preview>();
        vi.mocked(api.extractPreview).mockReturnValueOnce(pending.promise);
        renderWithIntl(<ScriptProcessor />);

        fireEvent.click(extractButton());
        const bPreviewKey = `script-extraction-preview:${encodeURIComponent(currentEnvKey())}:b`;
        act(() => useProjectStore.getState().setRunningOp(bPreviewKey, true));
        await act(async () => pending.reject(new Error('old failed')));

        expect(useProjectStore.getState().runningOps[bPreviewKey]).toBe(true);
        expect(useProjectStore.getState().isAnalyzing).toBe(true);
    });

    it('does not let a stale toast retry resubmit the old project and script', async () => {
        const pending = deferred<typeof preview>();
        vi.mocked(api.extractPreview).mockReturnValueOnce(pending.promise);
        renderWithIntl(<ScriptProcessor />);

        fireEvent.click(extractButton());
        await act(async () => pending.reject(new Error('old failed')));
        const retry = useToastStore.getState().toasts.find(item => item.kind === 'error')?.action?.onClick;
        expect(retry).toBeTruthy();

        act(() => useProjectStore.setState({ currentProject: project('b', 'B text', 4), projects: [project('a', '已保存原文', 7), project('b', 'B text', 4)] }));
        act(() => retry?.());

        expect(api.extractPreview).toHaveBeenCalledTimes(1);
    });

    it('rejects confirmation when the pending script no longer matches the owner text', async () => {
        const initial = project('a', 'A text', 7);
        useProjectStore.setState({
            projects: [initial], currentProject: initial,
            pendingExtraction: preview, pendingExtractionScript: 'A changed again',
            pendingExtractionOwner: { projectId: 'a', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 7, text: 'A edited text', sessionId: 'test-session' },
            isAnalyzing: false, readWatermarks: {}, runningOps: {},
        } as Partial<ReturnType<typeof useProjectStore.getState>>);

        await expect(useProjectStore.getState().confirmExtraction()).rejects.toThrow(/stale|script|text/i);

        expect(api.reparseProject).not.toHaveBeenCalled();
        expect(useProjectStore.getState().pendingExtraction).toEqual(preview);
    });

    it('clears pending extraction ownership when selecting another project', async () => {
        const a = project('a', 'A text', 7);
        const b = project('b', 'B text', 4);
        vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => rawProject('b', 'B text', 4) })));
        useProjectStore.setState({
            projects: [a, b], currentProject: a,
            pendingExtraction: preview, pendingExtractionScript: 'A edited text',
            pendingExtractionOwner: { projectId: 'a', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 7, text: 'A edited text', sessionId: 'test-session' },
            isAnalyzing: false, readWatermarks: {}, runningOps: {},
        } as Partial<ReturnType<typeof useProjectStore.getState>>);

        const selecting = useProjectStore.getState().selectProject('b');

        expect(useProjectStore.getState().pendingExtraction).toBeNull();
        await selecting;
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
    });

    it('clears a same-project stale preview when the committed revision changes without clearing another project preview', async () => {
        const initial = project('a', 'A text', 7);
        const bPreview = { characters: [{ id: 'b-char' }], scenes: [], props: [] };
        useProjectStore.setState({
            projects: [initial, project('b', 'B text', 4)], currentProject: initial,
            pendingExtraction: preview, pendingExtractionScript: 'A text',
            pendingExtractionOwner: { projectId: 'a', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 7, text: 'A text', sessionId: 'a-session' },
            isAnalyzing: false, readWatermarks: {}, runningOps: {},
        } as Partial<ReturnType<typeof useProjectStore.getState>>);
        renderWithIntl(<ScriptProcessor />);

        act(() => useProjectStore.setState({ currentProject: project('a', 'A text', 8), projects: [project('a', 'A text', 8), project('b', 'B text', 4)] }));

        await waitFor(() => expect(useProjectStore.getState().pendingExtraction).toBeNull());
        expect(useProjectStore.getState().pendingExtractionOwner).toBeNull();
        expect(api.reparseProject).not.toHaveBeenCalled();

        act(() => useProjectStore.setState({
            currentProject: project('a', 'A text', 8),
            pendingExtraction: bPreview, pendingExtractionScript: 'B text',
            pendingExtractionOwner: { projectId: 'b', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 4, text: 'B text', sessionId: 'b-session' },
        } as Partial<ReturnType<typeof useProjectStore.getState>>));

        expect(useProjectStore.getState().pendingExtraction).toEqual(bPreview);
        expect(useProjectStore.getState().pendingExtractionOwner?.projectId).toBe('b');
    });

    it('keeps a newer extraction operation busy when an old confirmation completes late', async () => {
        const initial = project('a', 'A text', 7);
        const bPreview = { characters: [{ id: 'b-char' }], scenes: [], props: [] };
        const pending = deferred<unknown>();
        vi.mocked(api.reparseProject).mockReturnValueOnce(pending.promise);
        useProjectStore.setState({
            projects: [initial, project('b', 'B text', 4)], currentProject: initial,
            pendingExtraction: preview, pendingExtractionScript: 'A edited text',
            pendingExtractionOwner: { projectId: 'a', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 7, text: 'A edited text', sessionId: 'a-session' },
            isAnalyzing: false, readWatermarks: {}, runningOps: {},
        } as Partial<ReturnType<typeof useProjectStore.getState>>);

        const confirming = useProjectStore.getState().confirmExtraction();
        const aConfirmKey = `script-extraction-confirm:${encodeURIComponent(currentEnvKey())}:a`;
        const bPreviewKey = `script-extraction-preview:${encodeURIComponent(currentEnvKey())}:b`;
        expect(useProjectStore.getState().runningOps[aConfirmKey]).toBe(true);

        act(() => useProjectStore.setState({
            currentProject: project('b', 'B text', 4),
            pendingExtraction: bPreview, pendingExtractionScript: 'B text',
            pendingExtractionOwner: { projectId: 'b', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 4, text: 'B text', sessionId: 'b-session' },
            runningOps: {
                ...useProjectStore.getState().runningOps,
                [bPreviewKey]: true,
            },
        } as Partial<ReturnType<typeof useProjectStore.getState>>));

        await act(async () => pending.resolve(rawProject('a', 'A edited text', 8)));
        await confirming;

        expect(useProjectStore.getState().isAnalyzing).toBe(true);
        expect(useProjectStore.getState().runningOps[bPreviewKey]).toBe(true);
        expect(useProjectStore.getState().pendingExtraction).toEqual(bPreview);
        expect(useProjectStore.getState().pendingExtractionOwner?.projectId).toBe('b');
    });

    it('rejects confirmation when a foreign owner and foreign current project match each other', async () => {
        const foreign = 'http://foreign.invalid';
        const initial = { ...project('a', 'A text', 7), readEnvironment: foreign };
        useProjectStore.setState({
            projects: [initial], currentProject: initial,
            pendingExtraction: preview, pendingExtractionScript: 'A edited text',
            pendingExtractionOwner: { projectId: 'a', environment: foreign, generation: currentEnvGeneration(), revision: 7, text: 'A edited text', sessionId: 'foreign-session' },
            isAnalyzing: false, readWatermarks: {}, runningOps: {},
        } as Partial<ReturnType<typeof useProjectStore.getState>>);

        await expect(useProjectStore.getState().confirmExtraction()).rejects.toThrow(/stale|project/i);

        expect(api.reparseProject).not.toHaveBeenCalled();
        expect(useProjectStore.getState().pendingExtraction).toEqual(preview);
    });

    it('does not duplicate confirmation when the shared reparse operation is already running', async () => {
        const initial = project('a', 'A text', 7);
        useProjectStore.setState({
            projects: [initial], currentProject: initial,
            pendingExtraction: preview, pendingExtractionScript: 'A edited text',
            pendingExtractionOwner: { projectId: 'a', environment: currentEnvKey(), generation: currentEnvGeneration(), revision: 7, text: 'A edited text', sessionId: 'test-session' },
            isAnalyzing: true, readWatermarks: {},
            runningOps: { [`script-extraction-confirm:${encodeURIComponent(currentEnvKey())}:a`]: true },
        } as Partial<ReturnType<typeof useProjectStore.getState>>);

        await useProjectStore.getState().confirmExtraction();

        expect(api.reparseProject).not.toHaveBeenCalled();
        expect(useProjectStore.getState().pendingExtraction).toEqual(preview);
    });
});
