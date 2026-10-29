import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { api } from '@/lib/api';
import { getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore, type Project } from '@/store/projectStore';
import { scriptDraftKey, useScriptDraftStore } from '@/store/scriptDraftStore';
import ScriptProcessor from '../ScriptProcessor';

vi.mock('@/lib/api', () => ({
    API_URL: 'http://test.invalid',
    crudApi: {},
    api: {
        updateScriptText: vi.fn(),
        getProject: vi.fn(),
        getNextEpisodeHook: vi.fn(async () => ({ has_text: false, hook: null, stale: false })),
        getPreviousEpisodeSummary: vi.fn(async () => ({ has_previous: false })),
    },
}));

const envKey = () => getRuntimeEnvironment()!.key;

function project(id = 'a', text = '已保存原文', revision = 7): Project & { original_text: string; created_at: number; updated_at: number } {
    return {
        id, title: id, originalText: text, original_text: text, revision, readEnvironment: envKey(),
        created_at: 1, updated_at: 2, video_tasks: [],
        characters: [], scenes: [], props: [], frames: [], status: 'draft',
        createdAt: '', updatedAt: '',
    };
}

function selectProject(value: Project) {
    act(() => useProjectStore.setState({ currentProject: value }));
}

function editor() {
    return screen.getByRole('textbox');
}

describe('ScriptProcessor revisioned saving', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        localStorage.clear();
        useScriptDraftStore.setState({ drafts: {} });
        const initial = project();
        useProjectStore.setState({ projects: [initial], currentProject: initial, isAnalyzing: false });
        vi.mocked(api.updateScriptText).mockImplementation(async (id, text, revision) => ({
            ...project(id, text, revision + 1), original_text: text,
        }));
    });

    it('saves a changed draft on blur and shows the committed text after remount', async () => {
        const view = renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: '新的原文' } });
        expect(useProjectStore.getState().currentProject?.originalText).toBe('已保存原文');
        fireEvent.blur(editor());
        await waitFor(() => expect(api.updateScriptText).toHaveBeenCalledWith('a', '新的原文', 7));
        await waitFor(() => expect(useProjectStore.getState().currentProject?.revision).toBe(8));
        view.unmount();
        renderWithIntl(<ScriptProcessor />);
        expect(editor()).toHaveValue('新的原文');
        fireEvent.blur(editor());
        expect(api.updateScriptText).toHaveBeenCalledTimes(1);
    });

    it.each([409, 503])('preserves a failed draft and visibly reports HTTP %s without automatic retry', async status => {
        vi.mocked(api.updateScriptText).mockRejectedValue({ response: { status } });
        renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: '不可丢失的草稿' } });
        fireEvent.blur(editor());
        expect(await screen.findByRole('alert')).toHaveTextContent(/草稿|未保存/);
        expect(editor()).toHaveValue('不可丢失的草稿');
        fireEvent.blur(editor());
        expect(api.updateScriptText).toHaveBeenCalledTimes(1);
        expect(api.getProject).not.toHaveBeenCalled();
    });

    it('keeps typing performed during a save and uses the acknowledged revision for the next save', async () => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateScriptText).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: '第一版草稿' } });
        fireEvent.blur(editor());
        fireEvent.change(editor(), { target: { value: '继续输入的第二版' } });
        await act(async () => resolve({ ...project('a', '第一版草稿', 8), original_text: '第一版草稿' }));
        expect(editor()).toHaveValue('继续输入的第二版');
        fireEvent.blur(editor());
        await waitFor(() => expect(api.updateScriptText).toHaveBeenLastCalledWith('a', '继续输入的第二版', 8));
    });

    it('keeps project drafts separate when switching projects', async () => {
        renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: 'A的未保存草稿' } });
        selectProject(project('b', 'B原文', 3));
        expect(editor()).toHaveValue('B原文');
        fireEvent.change(editor(), { target: { value: 'B的新原文' } });
        fireEvent.blur(editor());
        await waitFor(() => expect(api.updateScriptText).toHaveBeenCalledWith('b', 'B的新原文', 3));
        selectProject(project());
        expect(editor()).toHaveValue('A的未保存草稿');
        fireEvent.blur(editor());
        await waitFor(() => expect(api.updateScriptText).toHaveBeenLastCalledWith('a', 'A的未保存草稿', 7));
    });

    it('does not borrow a newer store revision for a draft based on the old snapshot', async () => {
        renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: '旧版本上的草稿' } });
        selectProject(project('a', '其他入口已修改', 8));
        fireEvent.blur(editor());
        await waitFor(() => expect(api.updateScriptText).toHaveBeenCalledWith('a', '旧版本上的草稿', 7));
    });

    it.each(['currentProject', 'projects', 'both'] as const)('does not overwrite a newer %s snapshot with a delayed save response', async target => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateScriptText).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: '本次提交' } });
        fireEvent.blur(editor());
        fireEvent.change(editor(), { target: { value: '仍在输入的草稿' } });
        const newer = project('a', '其他入口的新原文', 9);
        act(() => useProjectStore.setState({
            ...(target !== 'projects' ? { currentProject: newer } : {}),
            ...(target !== 'currentProject' ? { projects: [newer] } : {}),
        }));

        await act(async () => resolve({ ...project('a', '本次提交', 8), original_text: '本次提交' }));

        if (target !== 'projects') expect(useProjectStore.getState().currentProject).toMatchObject(newer);
        if (target !== 'currentProject') expect(useProjectStore.getState().projects[0]).toMatchObject(newer);
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('a')]).toMatchObject({
            text: '仍在输入的草稿', savedText: '本次提交', revision: 8, saving: false,
        });
        expect(api.updateScriptText).toHaveBeenCalledTimes(1);
    });

    it('keeps the queued blur on its acknowledged revision when a newer snapshot arrives before the response', async () => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateScriptText)
            .mockImplementationOnce(() => new Promise(done => { resolve = done; }))
            .mockRejectedValueOnce({ response: { status: 409 } });
        const view = renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: '本次提交' } });
        fireEvent.blur(editor());
        fireEvent.change(editor(), { target: { value: '排队的草稿' } });
        fireEvent.blur(editor());
        const newer = project('a', '其他入口的新原文', 9);
        act(() => useProjectStore.setState({ currentProject: newer, projects: [newer] }));

        await act(async () => resolve({ ...project('a', '本次提交', 8), original_text: '本次提交' }));

        expect(api.updateScriptText).toHaveBeenLastCalledWith('a', '排队的草稿', 8);
        expect(useProjectStore.getState().currentProject).toMatchObject(newer);
        expect(useProjectStore.getState().projects[0]).toMatchObject(newer);
        expect(useScriptDraftStore.getState().drafts[scriptDraftKey('a')]).toMatchObject({
            text: '排队的草稿', savedText: '本次提交', revision: 8, error: 'conflict', saving: false,
        });
        view.unmount();
        renderWithIntl(<ScriptProcessor />);
        expect(editor()).toHaveValue('排队的草稿');
        expect(screen.getByRole('alert')).toHaveTextContent(/草稿|未保存/);
        fireEvent.blur(editor());
        expect(api.updateScriptText).toHaveBeenCalledTimes(2);
    });

    it('serializes the latest blurred draft after an in-flight save using its acknowledged revision', async () => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateScriptText).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: '首个提交' } });
        fireEvent.blur(editor());
        fireEvent.change(editor(), { target: { value: '过渡提交' } });
        fireEvent.blur(editor());
        fireEvent.change(editor(), { target: { value: '最后一次失焦提交' } });
        fireEvent.blur(editor());
        fireEvent.change(editor(), { target: { value: '仍在输入未失焦' } });
        expect(api.updateScriptText).toHaveBeenCalledTimes(1);
        await act(async () => resolve(project('a', '首个提交', 8)));
        await waitFor(() => expect(api.updateScriptText).toHaveBeenLastCalledWith('a', '最后一次失焦提交', 8));
        expect(api.updateScriptText).toHaveBeenCalledTimes(2);
        expect(editor()).toHaveValue('仍在输入未失焦');
        expect(screen.getByRole('status')).toHaveTextContent('未保存');
    });

    it.each([409, 503])('stops the blur queue on HTTP %s and keeps the draft across unmount and remount', async status => {
        let reject!: (reason: unknown) => void;
        vi.mocked(api.updateScriptText).mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
        const view = renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: '首个提交' } });
        fireEvent.blur(editor());
        fireEvent.change(editor(), { target: { value: '排队但不能继续提交' } });
        fireEvent.blur(editor());
        view.unmount();
        await act(async () => reject({ response: { status } }));
        renderWithIntl(<ScriptProcessor />);
        expect(editor()).toHaveValue('排队但不能继续提交');
        expect(await screen.findByRole('alert')).toHaveTextContent(/草稿|未保存/);
        fireEvent.blur(editor());
        expect(api.updateScriptText).toHaveBeenCalledTimes(1);
        expect(api.getProject).not.toHaveBeenCalled();
    });

    it('finishes a queued blur after unmount and shows the committed response on remount', async () => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateScriptText).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        const view = renderWithIntl(<ScriptProcessor />);
        fireEvent.change(editor(), { target: { value: '首个提交' } });
        fireEvent.blur(editor());
        fireEvent.change(editor(), { target: { value: '离开页面前的提交' } });
        fireEvent.blur(editor());
        view.unmount();
        await act(async () => resolve(project('a', '首个提交', 8)));
        await waitFor(() => expect(api.updateScriptText).toHaveBeenLastCalledWith('a', '离开页面前的提交', 8));
        renderWithIntl(<ScriptProcessor />);
        expect(editor()).toHaveValue('离开页面前的提交');
        expect(screen.getByRole('status')).toHaveTextContent('已保存');
    });
});


it('keeps the acknowledged text and its source version metadata consistent', async () => {
    const initial = { ...project(), read_versions: { project: 7, series: null, library: 4 } };
    useScriptDraftStore.setState({ drafts: {} });
    useProjectStore.setState({ projects: [initial], currentProject: initial, readWatermarks: {}, runningOps: {} });
    vi.mocked(api.updateScriptText).mockResolvedValue({ ...project('a', 'saved', 8), original_text: 'saved' });
    const key = scriptDraftKey('a');
    useScriptDraftStore.getState().initialize(key, initial.originalText, 7);
    useScriptDraftStore.getState().changeText(key, 'saved');
    await useScriptDraftStore.getState().save(key, 'a');
    expect(useProjectStore.getState().currentProject?.revision).toBe(8);
    expect(useProjectStore.getState().currentProject?.read_versions?.project).toBe(8);
    expect(useProjectStore.getState().projects[0].read_versions?.project).toBe(8);
    expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(4);
});

it('does not mark a malformed text acknowledgement as saved', async () => {
    useScriptDraftStore.setState({ drafts: {} });
    useProjectStore.setState({ projects: [project()], currentProject: project(), readWatermarks: {}, runningOps: {} });
    vi.mocked(api.updateScriptText).mockResolvedValue({ revision: 8, original_text: 'saved' });
    const key = scriptDraftKey('a');
    useScriptDraftStore.getState().initialize(key, 'original', 7);
    useScriptDraftStore.getState().changeText(key, 'saved');
    await useScriptDraftStore.getState().save(key, 'a');
    expect(useScriptDraftStore.getState().drafts[key]).toMatchObject({ savedText: 'original', revision: 7, error: 'failed', text: 'saved' });
    expect(useProjectStore.getState().currentProject?.revision).toBe(7);
});
