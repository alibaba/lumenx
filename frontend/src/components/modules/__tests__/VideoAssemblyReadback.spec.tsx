import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import VideoAssembly from '../VideoAssembly';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { useProjectStore, type Project } from '@/store/projectStore';
import { api } from '@/lib/api';
const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';
const currentEnvKey = () => getRuntimeEnvironment()?.key ?? activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID }).key;

vi.mock('@/lib/api', () => ({
    API_URL: 'http://api.test',
    api: {
        selectVideo: vi.fn(),
        mergeVideos: vi.fn(),
        getProject: vi.fn(),
        listBgmPresets: vi.fn(),
        updateAudioMix: vi.fn(),
    },
}));

vi.mock('framer-motion', () => ({
    AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    motion: {
        div: ({ children, layoutId, ...props }: React.HTMLAttributes<HTMLDivElement> & { layoutId?: string }) => <div {...props} data-layout-id={layoutId}>{children}</div>,
        section: ({ children, ...props }: React.HTMLAttributes<HTMLElement>) => <section {...props}>{children}</section>,
    },
}));

function deferred<T = unknown>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}

function project(id = 'a', revision = 3, extra: Partial<Project> = {}): Project {
    return {
        id,
        revision,
        readEnvironment: currentEnvKey(),
        title: `Project ${id}`,
        originalText: 'script',
        characters: [],
        scenes: [],
        props: [],
        status: 'draft',
        createdAt: 'old',
        updatedAt: 'old',
        frames: [
            { id: 'f1', scene_id: 's1', image_prompt: 'prompt f1', selected_video_id: 'old', video_url: '/video/old.mp4' },
        ],
        video_tasks: [
            { id: 'old', project_id: id, frame_id: 'f1', status: 'completed', video_url: '/video/old.mp4', image_url: '', prompt: '', duration: 5, created_at: 1, model: 'm' },
            { id: 'new', project_id: id, frame_id: 'f1', status: 'completed', video_url: '/video/new.mp4', image_url: '', prompt: '', duration: 5, created_at: 2, model: 'm' },
        ],
        bgm_url: null,
        mix_settings: { dialogue: 100, bgm: 35, sfx: 60 },
        ...extra,
    };
}

function raw(projectData: Project) {
    return {
        ...projectData,
        original_text: projectData.originalText,
        created_at: 1,
        updated_at: 1,
        frames: projectData.frames,
        video_tasks: projectData.video_tasks ?? [],
        characters: projectData.characters,
        scenes: projectData.scenes,
        props: projectData.props,
    };
}

function read(projectData: Project) {
    return {
        ...raw(projectData),
        read_versions: { project: projectData.revision, series: null, library: 1 },
    };
}

function renderAssembly(initial = project()) {
    useProjectStore.setState({ projects: [initial], currentProject: initial, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
    return renderWithIntl(<VideoAssembly />);
}

function openVariants() {
    fireEvent.click(screen.getByText('prompt f1'));
}

function openMix() {
    fireEvent.click(screen.getByRole('button', { name: /音频混音/ }));
}

function openExport() {
    fireEvent.click(screen.getByRole('button', { name: /导出/ }));
}

beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
    vi.resetAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.stubGlobal('alert', vi.fn());
    vi.mocked(api.listBgmPresets).mockResolvedValue([{ id: 'calm', label: 'Calm', mood: 'soft', url: '/bgm/calm.mp3' }]);
});

afterEach(() => {
    clearRuntimeEnvironment();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('VideoAssembly committed write/readback handling', () => {
    it.each(['stale', 'unknown', 'missing', 'wrong-revision'])('keeps %s output available only as a historical export', (state) => {
        const proof = state === 'missing' ? undefined : {
            state: state === 'wrong-revision' ? 'current' : state,
            reason: 'test', project_revision: state === 'wrong-revision' ? 2 : 3,
        };
        renderAssembly(project('a', 3, { merged_video_url: '/video/merged.mp4', merged_video_freshness: proof } as Partial<Project>));
        openExport();
        expect(screen.queryByText('合并视频已就绪')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: '下载历史 MP4' })).toBeEnabled();
        expect(screen.getByText(state === 'stale' ? '合成输入已变化，请重新合成以获得当前版本。' : '尚未确认此成片是否对应当前项目，请重新合成。')).toBeInTheDocument();
    });

    it('shows current output only with a backend proof for the displayed revision', () => {
        renderAssembly(project('a', 3, { merged_video_url: '/video/merged.mp4',
            merged_video_freshness: { state: 'current', reason: 'current', project_revision: 3 } } as Partial<Project>));
        openExport();
        expect(screen.getByText('合并视频已就绪')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '下载 MP4' })).toBeEnabled();
    });

    it('removes current status when a new raw command has no freshness proof', () => {
        const before = project('a', 3, { merged_video_url: '/video/merged.mp4',
            merged_video_freshness: { state: 'current', reason: 'current', project_revision: 3 } } as Partial<Project>);
        renderAssembly(before); openExport();
        const next = raw(project('a', 4, { merged_video_url: '/video/merged.mp4' }));
        act(() => useProjectStore.getState().applyFrameCommandSnapshot('a', next));
        expect(screen.queryByText('合并视频已就绪')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: '下载历史 MP4' })).toBeEnabled();
    });


    it('does not download a protected access error response as the merged video', async () => {
        const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'access_required' }), {
            status: 401,
            headers: { 'Content-Type': 'application/json' },
        }));
        vi.stubGlobal('fetch', fetchMock);
        const createObjectURL = vi.spyOn(URL, 'createObjectURL');
        renderAssembly(project('a', 3, { merged_video_url: '/video/merged.mp4', merged_video_freshness: { state: 'current', reason: 'current', project_revision: 3 } } as Partial<Project>));

        openExport();
        fireEvent.click(screen.getByRole('button', { name: /下载 MP4/ }));

        await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
        expect(createObjectURL).not.toHaveBeenCalled();
        expect(alert).toHaveBeenCalledWith(expect.stringMatching(/下载失败|download/i));
    });

    it.each([undefined, 'http://other.test'])('does not write a project outside the confirmed API environment %s', async (readEnvironment) => {
        renderAssembly(project('a', 3, { readEnvironment }));
        openVariants(); fireEvent.click(screen.getByRole('button', { name: '选择此变体' }));
        openMix(); fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '55' } });
        openExport(); fireEvent.click(screen.getByRole('button', { name: /合并并继续/ }));
        await act(async () => {});
        expect(api.selectVideo).not.toHaveBeenCalled();
        expect(api.updateAudioMix).not.toHaveBeenCalled();
        expect(api.mergeVideos).not.toHaveBeenCalled();
    });

    it('selects a take only after raw ACK and committed readback confirm it', async () => {
        const ackProject = project('a', 4, { frames: [{ id: 'f1', scene_id: 's1', image_prompt: 'prompt f1', selected_video_id: 'new', video_url: '/video/new.mp4' }] });
        vi.mocked(api.selectVideo).mockResolvedValue(raw(ackProject));
        vi.mocked(api.getProject).mockResolvedValue(read(ackProject));
        renderAssembly();
        openVariants();

        fireEvent.click(screen.getByRole('button', { name: '选择此变体' }));

        await waitFor(() => expect(api.getProject).toHaveBeenCalledWith('a'));
        expect(useProjectStore.getState().currentProject?.frames[0].selected_video_id).toBe('new');
        expect(screen.getAllByText('已选择')).toHaveLength(1);
    });

    it('keeps a same-project refresh from canceling an in-flight take selection', async () => {
        const request = deferred<Project>();
        const ackProject = project('a', 4, { frames: [{ id: 'f1', scene_id: 's1', image_prompt: 'prompt f1', selected_video_id: 'new', video_url: '/video/new.mp4' }] });
        vi.mocked(api.selectVideo).mockReturnValue(request.promise);
        vi.mocked(api.getProject).mockResolvedValue(read(ackProject));
        renderAssembly();
        openVariants();
        fireEvent.click(screen.getByRole('button', { name: '选择此变体' }));
        act(() => useProjectStore.setState({ currentProject: { ...project('a', 3), title: 'Refreshed same project' } }));

        await act(async () => request.resolve(raw(ackProject) as Project));

        await waitFor(() => expect(api.getProject).toHaveBeenCalledWith('a'));
        expect(useProjectStore.getState().currentProject?.frames[0].selected_video_id).toBe('new');
    });

    it('drops a selected take ACK after unmount without readback or mutation', async () => {
        const request = deferred<Project>();
        vi.mocked(api.selectVideo).mockReturnValue(request.promise);
        const view = renderAssembly();
        openVariants();
        fireEvent.click(screen.getByRole('button', { name: '选择此变体' }));
        view.unmount();

        await act(async () => request.resolve(raw(project('a', 4, { frames: [{ id: 'f1', scene_id: 's1', selected_video_id: 'new', image_prompt: 'prompt f1' }] })) as Project));

        expect(api.getProject).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.frames[0].selected_video_id).toBe('old');
    });

    it('does not apply or resubmit a selected take when committed readback fails', async () => {
        const ackProject = project('a', 4, { frames: [{ id: 'f1', scene_id: 's1', image_prompt: 'prompt f1', selected_video_id: 'new', video_url: '/video/new.mp4' }] });
        vi.mocked(api.selectVideo).mockResolvedValue(raw(ackProject));
        vi.mocked(api.getProject).mockRejectedValue(new Error('offline'));
        renderAssembly();
        openVariants();

        fireEvent.click(screen.getByRole('button', { name: '选择此变体' }));

        await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/)));
        expect(useProjectStore.getState().currentProject?.frames[0].selected_video_id).toBe('old');
        expect(api.selectVideo).toHaveBeenCalledTimes(1);
    });

    it('drops late select readback after a project switch without mutating the new project', async () => {
        const request = deferred<Project>();
        vi.mocked(api.selectVideo).mockReturnValue(request.promise);
        renderAssembly();
        openVariants();
        fireEvent.click(screen.getByRole('button', { name: '选择此变体' }));
        act(() => useProjectStore.setState({ projects: [project('b')], currentProject: project('b') }));
        act(() => useProjectStore.setState({ projects: [project('a', 6)], currentProject: project('a', 6) }));

        await act(async () => request.resolve(raw(project('a', 4, { frames: [{ id: 'f1', scene_id: 's1', selected_video_id: 'new', image_prompt: 'prompt f1' }] })) as Project));

        expect(useProjectStore.getState().currentProject?.id).toBe('a');
        expect(useProjectStore.getState().currentProject?.revision).toBe(6);
        expect(useProjectStore.getState().currentProject?.frames[0].selected_video_id).toBe('old');
        expect(api.getProject).not.toHaveBeenCalled();
        expect(window.alert).not.toHaveBeenCalled();
    });

    it('drops a selected take ACK when the same project id now belongs to a different read environment', async () => {
        const request = deferred<Project>();
        vi.mocked(api.selectVideo).mockReturnValue(request.promise);
        renderAssembly(project('a', 3, { readEnvironment: currentEnvKey() } as Partial<Project>));
        openVariants();
        fireEvent.click(screen.getByRole('button', { name: '选择此变体' }));
        act(() => useProjectStore.setState({ currentProject: project('a', 8, { readEnvironment: 'http://other.test' } as Partial<Project>) }));

        await act(async () => request.resolve(raw(project('a', 4, { frames: [{ id: 'f1', scene_id: 's1', selected_video_id: 'new', image_prompt: 'prompt f1' }] })) as Project));

        expect(api.getProject).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.readEnvironment).toBe('http://other.test');
        expect(useProjectStore.getState().currentProject?.frames[0].selected_video_id).toBe('old');
    });

    it('does not let an old same-key selection completion clear a newer in-flight selection', async () => {
        const oldRequest = deferred<Project>();
        const newRequest = deferred<Project>();
        vi.mocked(api.selectVideo).mockReturnValueOnce(oldRequest.promise).mockReturnValueOnce(newRequest.promise);
        renderAssembly(project('a', 3, { readEnvironment: currentEnvKey() } as Partial<Project>));
        openVariants();
        fireEvent.click(screen.getByRole('button', { name: '选择此变体' }));
        act(() => useProjectStore.setState({ projects: [project('b')], currentProject: project('b') }));
        act(() => useProjectStore.setState({
            projects: [project('a', 6, { readEnvironment: currentEnvKey() } as Partial<Project>)],
            currentProject: project('a', 6, { readEnvironment: currentEnvKey() } as Partial<Project>),
        }));
        openVariants();
        fireEvent.click(screen.getByRole('button', { name: '选择此变体' }));

        await act(async () => oldRequest.resolve(raw(project('a', 4, { frames: [{ id: 'f1', scene_id: 's1', selected_video_id: 'new', image_prompt: 'prompt f1' }] })) as Project));

        expect(screen.getByRole('button', { name: '' })).toBeDisabled();
        expect(api.selectVideo).toHaveBeenCalledTimes(2);
        expect(api.getProject).not.toHaveBeenCalled();
    });

    it('confirms merge through readback and separates write failure from readback failure', async () => {
        const merged = project('a', 4, { merged_video_url: '/merged.mp4' });
        vi.mocked(api.mergeVideos).mockResolvedValue(raw(merged));
        vi.mocked(api.getProject).mockRejectedValue(new Error('offline'));
        renderAssembly();
        openExport();

        fireEvent.click(screen.getByRole('button', { name: /合并并继续/ }));

        await waitFor(() => expect(screen.getByText(/已保存.*读取失败/)).toBeInTheDocument());
        expect(useProjectStore.getState().currentProject?.merged_video_url).toBeUndefined();
        expect(api.mergeVideos).toHaveBeenCalledTimes(1);
        expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/));
    });

    it('keeps a historical download when merge fails after a previous output', async () => {
        vi.mocked(api.mergeVideos).mockRejectedValueOnce(new Error('merge failed'));
        renderAssembly(project('a', 3, { merged_video_url: '/video/old-merged.mp4',
            merged_video_freshness: { state: 'stale', reason: 'inputs_changed', project_revision: 3 } } as Partial<Project>));
        openExport();

        fireEvent.click(screen.getByRole('button', { name: /合并并继续/ }));

        await waitFor(() => expect(screen.getByText('merge failed')).toBeInTheDocument());
        expect(screen.queryByText('合并视频已就绪')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: '下载历史 MP4' })).toBeEnabled();
        expect(useProjectStore.getState().currentProject?.merged_video_url).toBe('/video/old-merged.mp4');
        expect(api.getProject).not.toHaveBeenCalled();
    });

    it('shows a current merged output only after the merge readback confirms the freshness proof', async () => {
        const ack = project('a', 4, { merged_video_url: '/video/new-merged.mp4' });
        const confirmed = project('a', 5, { merged_video_url: '/video/new-merged.mp4',
            merged_video_freshness: { state: 'current', reason: 'current', project_revision: 5 } } as Partial<Project>);
        vi.mocked(api.mergeVideos).mockResolvedValueOnce(raw(ack));
        vi.mocked(api.getProject).mockResolvedValueOnce(read(confirmed));
        renderAssembly();
        openExport();

        fireEvent.click(screen.getByRole('button', { name: /合并并继续/ }));

        await waitFor(() => expect(screen.getByText('合并视频已就绪')).toBeInTheDocument());
        expect(screen.getByRole('button', { name: '下载 MP4' })).toBeEnabled();
        expect(useProjectStore.getState().currentProject?.merged_video_url).toBe('/video/new-merged.mp4');
        expect(useProjectStore.getState().currentProject?.merged_video_freshness).toEqual({
            state: 'current', reason: 'current', project_revision: 5,
        });
        expect(api.mergeVideos).toHaveBeenCalledTimes(1);
        expect(api.getProject).toHaveBeenCalledWith('a');
    });

    it('does not let an old merge completion clear a newer project merge state', async () => {
        const oldRequest = deferred<Project>();
        vi.mocked(api.mergeVideos).mockReturnValueOnce(oldRequest.promise).mockReturnValueOnce(deferred<Project>().promise);
        renderAssembly();
        openExport();
        fireEvent.click(screen.getByRole('button', { name: /合并并继续/ }));
        act(() => useProjectStore.setState({ projects: [project('b')], currentProject: project('b') }));
        fireEvent.click(screen.getByRole('button', { name: /合并并继续/ }));

        await act(async () => oldRequest.resolve(raw(project('a', 4, { merged_video_url: '/old.mp4' })) as Project));

        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(screen.getByRole('button', { name: /合并并继续/ })).toBeDisabled();
        expect(window.alert).not.toHaveBeenCalled();
    });

    it('offers only supported mix controls and explains the selected clip audio', async () => {
        renderAssembly();
        openMix();
        await waitFor(() => expect(api.listBgmPresets).toHaveBeenCalledTimes(1));
        expect(screen.getAllByRole('slider')).toHaveLength(2);
        expect(screen.getByRole('slider', { name: '视频音轨' })).toHaveValue('100');
        expect(screen.getByRole('slider', { name: 'BGM' })).toHaveValue('35');
        expect(screen.queryByText('音效', { exact: true })).not.toBeInTheDocument();
        expect(screen.getByText(/视频音轨包括当前片段中的原声或已应用配音/)).toBeInTheDocument();
        expect(api.updateAudioMix).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.mix_settings?.sfx).toBe(60);
    });

    it('stores audio mix only after full raw ACK and committed readback confirm it', async () => {
        const confirmed = project('a', 4, { mix_settings: { dialogue: 77, bgm: 35, sfx: 60 } });
        vi.mocked(api.updateAudioMix).mockResolvedValueOnce(raw(confirmed));
        vi.mocked(api.getProject).mockResolvedValueOnce(read(confirmed));
        renderAssembly();
        openMix();
        const dialogue = screen.getAllByRole('slider')[0];

        fireEvent.change(dialogue, { target: { value: '80' } });

        await waitFor(() => expect(api.getProject).toHaveBeenCalledWith('a'));
        const store = useProjectStore.getState();
        expect(store.currentProject?.revision).toBe(4);
        expect(store.projects[0].revision).toBe(4);
        expect(store.currentProject?.read_versions).toEqual({ project: 4, series: null, library: 1 });
        expect(store.projects[0].read_versions).toEqual({ project: 4, series: null, library: 1 });
        expect(store.currentProject?.mix_settings?.dialogue).toBe(77);
        expect(dialogue).toHaveValue('77');
    });

    it('shows a visible mix write failure and preserves the failed slider draft locally', async () => {
        vi.mocked(api.updateAudioMix).mockRejectedValueOnce(new Error('storage unavailable'));
        renderAssembly();
        openMix();
        const dialogue = screen.getAllByRole('slider')[0];

        fireEvent.change(dialogue, { target: { value: '55' } });

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/storage unavailable/));
        expect(useProjectStore.getState().currentProject?.mix_settings?.dialogue).toBe(100);
        expect(dialogue).toHaveValue('55');
        expect(api.getProject).not.toHaveBeenCalled();
    });

    it('stops queued mix writes after a failed draft so later success cannot clear the unsaved values', async () => {
        const failed = deferred<Project>();
        vi.mocked(api.updateAudioMix).mockReturnValueOnce(failed.promise);
        renderAssembly();
        openMix();
        const [dialogue, bgm] = screen.getAllByRole('slider');

        fireEvent.change(dialogue, { target: { value: '55' } });
        await waitFor(() => expect(api.updateAudioMix).toHaveBeenCalledTimes(1));
        fireEvent.change(bgm, { target: { value: '20' } });
        await act(async () => failed.reject(new Error('dialogue write failed')));

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/dialogue write failed/));
        expect(api.updateAudioMix).toHaveBeenCalledTimes(1);
        expect(api.getProject).not.toHaveBeenCalled();
        expect(dialogue).toHaveValue('55');
        expect(bgm).toHaveValue('20');
        expect(useProjectStore.getState().currentProject?.mix_settings).toEqual({ dialogue: 100, bgm: 35, sfx: 60 });
    });

    it('keeps edits after a failed mix local until the user explicitly saves the whole draft', async () => {
        vi.mocked(api.updateAudioMix).mockRejectedValueOnce(new Error('dialogue write failed'));
        const confirmed = project('a', 4, { mix_settings: { dialogue: 55, bgm: 20, sfx: 60 } });
        vi.mocked(api.updateAudioMix).mockResolvedValueOnce(raw(confirmed));
        vi.mocked(api.getProject).mockResolvedValueOnce(read(confirmed));
        renderAssembly();
        openMix();
        const [dialogue, bgm] = screen.getAllByRole('slider');
        fireEvent.change(dialogue, { target: { value: '55' } });
        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('dialogue write failed'));
        fireEvent.change(bgm, { target: { value: '20' } });
        await act(async () => {});
        expect(api.updateAudioMix).toHaveBeenCalledTimes(1);
        expect(dialogue).toHaveValue('55');
        expect(bgm).toHaveValue('20');
        fireEvent.click(screen.getByRole('button', { name: '保存' }));
        await waitFor(() => expect(api.updateAudioMix).toHaveBeenCalledTimes(2));
        expect(api.updateAudioMix).toHaveBeenLastCalledWith('a', {
            bgm_url: null, dialogue_volume: 55, bgm_volume: 20, sfx_volume: 60,
        });
        await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
        expect(dialogue).toHaveValue('55');
        expect(bgm).toHaveValue('20');
    });

    it('clears a failed mix draft when the same id changes API environment', async () => {
        vi.mocked(api.updateAudioMix).mockRejectedValueOnce(new Error('dialogue write failed'));
        renderAssembly(project('a', 3, { readEnvironment: currentEnvKey() }));
        openMix();
        fireEvent.change(screen.getAllByRole('slider')[0], { target: { value: '55' } });
        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('dialogue write failed'));
        act(() => useProjectStore.setState({ currentProject: project('a', 8, {
            readEnvironment: 'http://other.test', mix_settings: { dialogue: 90, bgm: 10, sfx: 40 },
        }) }));
        expect(screen.getAllByRole('slider')[0]).toHaveValue('90');
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('cancels not-yet-started queued mix writes after unmount', async () => {
        const first = deferred<Project>();
        vi.mocked(api.updateAudioMix).mockReturnValueOnce(first.promise).mockResolvedValueOnce(raw(project('a', 5)) as Project);
        const view = renderAssembly();
        openMix();
        const [dialogue, bgm] = screen.getAllByRole('slider');

        fireEvent.change(dialogue, { target: { value: '55' } });
        await waitFor(() => expect(api.updateAudioMix).toHaveBeenCalledTimes(1));
        fireEvent.change(bgm, { target: { value: '20' } });
        view.unmount();
        await act(async () => first.resolve(raw(project('a', 4, { mix_settings: { dialogue: 55, bgm: 35, sfx: 60 } })) as Project));

        await new Promise(resolve => setTimeout(resolve, 0));
        expect(api.updateAudioMix).toHaveBeenCalledTimes(1);
        expect(api.getProject).not.toHaveBeenCalled();
    });

    it('shows a visible mix readback failure without applying the raw ACK', async () => {
        const ackProject = project('a', 4, { mix_settings: { dialogue: 72, bgm: 35, sfx: 60 } });
        vi.mocked(api.updateAudioMix).mockResolvedValueOnce(raw(ackProject));
        vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));
        renderAssembly();
        openMix();
        const dialogue = screen.getAllByRole('slider')[0];

        fireEvent.change(dialogue, { target: { value: '72' } });

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/已保存.*读取失败/));
        expect(useProjectStore.getState().currentProject?.mix_settings?.dialogue).toBe(100);
        expect(dialogue).toHaveValue('72');
    });

    it('serializes slider patches so the latest user input wins after committed reads', async () => {
        const first = deferred<Project>();
        const second = deferred<Project>();
        const firstRead = project('a', 4, { mix_settings: { dialogue: 60, bgm: 35, sfx: 60 } });
        const secondRead = project('a', 5, { mix_settings: { dialogue: 40, bgm: 35, sfx: 60 } });
        vi.mocked(api.updateAudioMix).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
        vi.mocked(api.getProject).mockResolvedValueOnce(read(firstRead)).mockResolvedValueOnce(read(secondRead));
        renderAssembly();
        openMix();
        const dialogue = screen.getAllByRole('slider')[0];

        fireEvent.change(dialogue, { target: { value: '60' } });
        await waitFor(() => expect(api.updateAudioMix).toHaveBeenCalledTimes(1));
        fireEvent.change(dialogue, { target: { value: '40' } });
        expect(api.updateAudioMix).toHaveBeenCalledTimes(1);
        await act(async () => first.resolve(raw(firstRead) as Project));
        await waitFor(() => expect(api.updateAudioMix).toHaveBeenCalledTimes(2));
        expect(dialogue).toHaveValue('40');
        await act(async () => second.resolve(raw(secondRead) as Project));

        await waitFor(() => expect(useProjectStore.getState().currentProject?.mix_settings?.dialogue).toBe(40));
        expect(useProjectStore.getState().currentProject?.revision).toBe(5);
    });

    it('uses acknowledged BGM selection from committed readback instead of the clicked preset', async () => {
        const ackProject = project('a', 4, { bgm_url: '/bgm/server.mp3', mix_settings: { dialogue: 100, bgm: 35, sfx: 60 } });
        vi.mocked(api.updateAudioMix).mockResolvedValueOnce(raw(ackProject));
        vi.mocked(api.getProject).mockResolvedValueOnce(read(ackProject));
        renderAssembly();
        openMix();
        await screen.findByText('Calm');

        fireEvent.click(screen.getByText('Calm'));

        await waitFor(() => expect(useProjectStore.getState().currentProject?.bgm_url).toBe('/bgm/server.mp3'));
        expect(api.updateAudioMix).toHaveBeenCalledWith('a', { bgm_url: '/bgm/calm.mp3' });
        expect(api.getProject).toHaveBeenCalledWith('a');
    });
});
