import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { api } from '@/lib/api';
import { useProjectStore, type Project } from '@/store/projectStore';
import ArtDirection from '../ArtDirection';
const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';
const currentEnvKey = () => getRuntimeEnvironment()?.key ?? activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID }).key;

vi.mock('@/lib/api', () => ({ API_URL: 'http://test.invalid', api: {
    getSeries: vi.fn(), getStylePresets: vi.fn(), updateSeries: vi.fn(),
    analyzeScriptForStyles: vi.fn(), saveArtDirection: vi.fn(), clearProjectArtDirection: vi.fn(), getProject: vi.fn(),
} }));

const style = { id: 'p', name: 'Project style', positive_prompt: 'ink', negative_prompt: '', is_custom: false };
const baselineStyle = { id: 'b', name: 'Series baseline', positive_prompt: 'series ink', negative_prompt: '', is_custom: false };
const project: Project = {
    id: 'p', revision: 2, series_id: 's', title: 'Episode', originalText: 'Script', readEnvironment: currentEnvKey(),
    read_versions: { project: 2, series: { id: 's', revision: 4 }, library: 1 },
    characters: [], scenes: [], props: [], frames: [], status: 'draft', createdAt: '', updatedAt: '',
    art_direction: { selected_style_id: 'p', style_config: style, custom_styles: [], ai_recommendations: [] },
};
const series = { id: 's', revision: 4, title: 'Series', art_direction: null };
const withSeriesRevision = (projectData: Project, revision: number): Project => ({
    ...projectData,
    readEnvironment: currentEnvKey(),
    read_versions: { project: projectData.revision, series: projectData.series_id ? { id: projectData.series_id, revision } : null, library: 1 },
});

function deferred<T = unknown>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
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
        read_versions: projectData.read_versions ?? { project: projectData.revision, series: projectData.series_id ? { id: projectData.series_id, revision: 4 } : null, library: 1 },
    };
}

describe('ArtDirection promotion revision contract', () => {
    beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
        vi.clearAllMocks();
        vi.spyOn(console, 'error').mockImplementation(() => {});
        useProjectStore.setState({ currentProject: project, projects: [project], readWatermarks: {}, runningOps: {} });
        vi.mocked(api.getSeries).mockResolvedValue(series);
        vi.mocked(api.getStylePresets).mockResolvedValue({ presets: [], categories: [] });
        vi.mocked(api.updateSeries).mockImplementation(async (id, data) => {
            const revision = data.expected_revision + 1;
            const current = useProjectStore.getState().currentProject ?? project;
            vi.mocked(api.getProject).mockResolvedValueOnce(read(withSeriesRevision({ ...current, revision: current.revision + 1 }, revision)));
            return { ...series, id, ...data, revision };
        });
        vi.mocked(api.analyzeScriptForStyles).mockResolvedValue({ recommendations: [] });
        vi.mocked(api.saveArtDirection).mockResolvedValue(raw({ ...project, revision: 3 }));
        vi.mocked(api.clearProjectArtDirection).mockResolvedValue(raw({ ...project, revision: 3, art_direction: undefined }));
        vi.mocked(api.getProject).mockResolvedValue(read({ ...project, revision: 3 }));
    });

    afterEach(() => {
    clearRuntimeEnvironment();
        vi.restoreAllMocks();
    });

    it('promotes using the displayed series revision and applies the response without refetch', async () => {
        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '推广为系列基线' }));
        await waitFor(() => expect(api.updateSeries).toHaveBeenCalledWith('s', {
            expected_revision: 4, art_direction: project.art_direction,
        }));
        expect(await screen.findByRole('button', { name: '重置为系列' })).toBeInTheDocument();
        expect(api.getSeries).toHaveBeenCalledTimes(1);
    });

    it.each([409, 503])('keeps the project style and shows HTTP %s without refreshing the version', async status => {
        vi.mocked(api.updateSeries).mockRejectedValue({ response: { status } });
        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '推广为系列基线' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/草稿|未保存/);
        expect(useProjectStore.getState().currentProject?.art_direction?.style_config).toEqual(style);
        expect(api.getSeries).toHaveBeenCalledTimes(1);
        expect(api.updateSeries).toHaveBeenCalledTimes(1);
    });

    it('does not reuse the previous series version while the next project is loading', async () => {
        renderWithIntl(<ArtDirection />);
        await screen.findByRole('button', { name: '推广为系列基线' });
        let resolve!: (value: unknown) => void;
        vi.mocked(api.getSeries).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        act(() => useProjectStore.setState({ currentProject: withSeriesRevision({ ...project, id: 'other', series_id: 'other-series' }, 9) }));
        expect(screen.queryByRole('button', { name: '推广为系列基线' })).not.toBeInTheDocument();
        await act(async () => resolve({ ...series, id: 'other-series', revision: 9 }));
        fireEvent.click(await screen.findByRole('button', { name: '推广为系列基线' }));
        await waitFor(() => expect(api.updateSeries).toHaveBeenCalledWith('other-series', expect.objectContaining({ expected_revision: 9 })));
    });

    it('does not apply a delayed promotion over the baseline read after reopening a project', async () => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateSeries).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '推广为系列基线' }));
        act(() => useProjectStore.setState({ currentProject: withSeriesRevision({ ...project, id: 'other', series_id: 'other-series' }, 9) }));
        await screen.findByRole('button', { name: '推广为系列基线' });
        vi.mocked(api.getSeries).mockResolvedValueOnce({ ...series, revision: 6, art_direction: { style_config: { ...style, name: 'Newer baseline' } } });
        vi.mocked(api.getProject).mockResolvedValueOnce(read(withSeriesRevision({ ...project, revision: 3 }, 6)));
        act(() => useProjectStore.setState({ currentProject: project }));
        await screen.findByText(/Newer baseline/);
        await act(async () => resolve({ ...series, revision: 5, art_direction: project.art_direction }));
        expect(screen.getByText(/Newer baseline/)).toBeInTheDocument();
    });

    it('keeps analyzed recommendations local and does not mutate the project store before apply', async () => {
        const recommendation = { id: 'ai-1', name: 'AI Noir', positive_prompt: 'noir', negative_prompt: '', is_custom: false };
        vi.mocked(api.analyzeScriptForStyles).mockResolvedValueOnce({ recommendations: [recommendation] });

        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '分析剧本' }));

        expect(await screen.findByText('AI Noir')).toBeInTheDocument();
        expect(useProjectStore.getState().currentProject?.art_direction?.ai_recommendations).toEqual([]);
        expect(api.saveArtDirection).not.toHaveBeenCalled();
    });

    it('ignores stale analysis when the same project id now belongs to another read environment', async () => {
        const request = deferred<{ recommendations: typeof style[] }>();
        vi.mocked(api.analyzeScriptForStyles).mockReturnValueOnce(request.promise);
        useProjectStore.setState({
            currentProject: { ...project, readEnvironment: currentEnvKey() },
            projects: [{ ...project, readEnvironment: currentEnvKey() }],
        });

        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '分析剧本' }));
        act(() => useProjectStore.setState({
            currentProject: { ...project, readEnvironment: 'http://other.invalid' },
            projects: [{ ...project, readEnvironment: 'http://other.invalid' }],
        }));
        await act(async () => request.resolve({ recommendations: [{ ...style, id: 'late', name: 'Late style' }] }));

        expect(screen.queryByText('Late style')).not.toBeInTheDocument();
        expect(useProjectStore.getState().currentProject?.art_direction?.ai_recommendations).toEqual([]);
    });

    it('does not fake persistence when save ACK succeeds but committed project readback fails', async () => {
        vi.mocked(api.saveArtDirection).mockResolvedValueOnce(raw({ ...project, revision: 3, title: 'raw ack only' }));
        vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));

        renderWithIntl(<ArtDirection />);
        fireEvent.click(screen.getByRole('button', { name: '应用并继续' }));

        await waitFor(() => expect(api.getProject).toHaveBeenCalledWith('p'));
        expect(await screen.findByRole('alert')).toHaveTextContent(/编辑已保存，但读取失败/);
        expect(useProjectStore.getState().currentProject?.revision).toBe(2);
        expect(useProjectStore.getState().currentProject?.title).toBe('Episode');
    });

    it('prevents duplicate save submissions while a save is in flight', async () => {
        const request = deferred<Project>();
        vi.mocked(api.saveArtDirection).mockReturnValueOnce(request.promise);

        renderWithIntl(<ArtDirection />);
        const apply = screen.getByRole('button', { name: '应用并继续' });
        act(() => {
            apply.click();
            apply.click();
        });

        expect(api.saveArtDirection).toHaveBeenCalledTimes(1);
    });

    it('does not fake reset-to-series persistence when clear ACK cannot be read back', async () => {
        vi.mocked(api.getSeries).mockResolvedValueOnce({ ...series, art_direction: { style_config: baselineStyle } });
        vi.mocked(api.clearProjectArtDirection).mockResolvedValueOnce(raw({ ...project, revision: 3, art_direction: undefined }));
        vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));

        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '重置为系列' }));

        await waitFor(() => expect(api.getProject).toHaveBeenCalledWith('p'));
        expect(await screen.findByRole('alert')).toHaveTextContent(/编辑已保存，但读取失败/);
        expect(useProjectStore.getState().currentProject?.art_direction?.style_config).toEqual(style);
    });

    it('rejects a mismatched series baseline instead of displaying it for promotion decisions', async () => {
        vi.mocked(api.getSeries).mockResolvedValueOnce({
            id: 'wrong-series',
            revision: 12,
            title: 'Wrong',
            art_direction: { style_config: { ...baselineStyle, name: 'Wrong baseline' } },
        });
        useProjectStore.setState({
            currentProject: { ...project, art_direction: undefined },
            projects: [{ ...project, art_direction: undefined }],
        });

        renderWithIntl(<ArtDirection />);

        await waitFor(() => expect(api.getSeries).toHaveBeenCalledWith('s'));
        expect(screen.queryByText(/Wrong baseline/)).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: '推广为系列基线' })).not.toBeInTheDocument();
    });

    it('rejects an older series baseline already superseded by observed source versions', async () => {
        const newerProject = {
            ...project,
            readEnvironment: currentEnvKey(),
            read_versions: { project: 2, series: { id: 's', revision: 10 }, library: 1 },
            art_direction: undefined,
        };
        vi.mocked(api.getSeries).mockResolvedValueOnce({
            id: 's',
            revision: 4,
            title: 'Series',
            art_direction: { style_config: { ...baselineStyle, name: 'Old baseline' } },
        });
        vi.mocked(api.getProject).mockResolvedValueOnce(read(newerProject as Project));
        useProjectStore.setState({ currentProject: newerProject as Project, projects: [newerProject as Project] });

        renderWithIntl(<ArtDirection />);

        await waitFor(() => expect(api.getSeries).toHaveBeenCalledWith('s'));
        expect(api.getProject).toHaveBeenCalledWith('p');
        expect(screen.queryByText(/Old baseline/)).not.toBeInTheDocument();
        expect(await screen.findByRole('alert')).toHaveTextContent(/尚未读取到有效版本/);
    });

    it('preserves a local style draft and AI proposal across ordinary same-project refresh', async () => {
        const recommendation = { id: 'ai-1', name: 'AI Noir', positive_prompt: 'noir', negative_prompt: '', is_custom: false };
        vi.mocked(api.analyzeScriptForStyles).mockResolvedValueOnce({ recommendations: [recommendation] });

        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '分析剧本' }));
        fireEvent.click(await screen.findByText('AI Noir'));
        fireEvent.click(await screen.findByRole('button', { name: '使用该风格' }));
        expect(screen.getAllByText('AI Noir').length).toBeGreaterThan(0);

        act(() => useProjectStore.setState({
            currentProject: { ...project, title: 'same project refresh', art_direction: { ...project.art_direction!, ai_recommendations: [] } },
            projects: [{ ...project, title: 'same project refresh', art_direction: { ...project.art_direction!, ai_recommendations: [] } }],
        }));

        expect(screen.getAllByText('AI Noir').length).toBeGreaterThan(0);
        expect(screen.getAllByText('noir').length).toBeGreaterThan(0);
    });


    it('clears a hydrated project style when switching to a blank project', async () => {
        renderWithIntl(<ArtDirection />);
        expect((await screen.findAllByText('Project style')).length).toBeGreaterThan(0);

        const blankProject: Project = {
            ...project,
            id: 'blank',
            series_id: undefined,
            art_direction: undefined,
            read_versions: { project: 1, series: null, library: 1 },
        };
        act(() => useProjectStore.setState({ currentProject: blankProject, projects: [blankProject] }));

        await waitFor(() => expect(screen.queryAllByText('Project style')).toHaveLength(0));
        expect(screen.getByRole('button', { name: '应用并继续' })).toBeDisabled();
    });

    it('keeps fresh AI recommendations when a same-project read contains older persisted proposals', async () => {
        const oldRecommendation = { id: 'old-ai', name: 'Old AI', positive_prompt: 'old ink', negative_prompt: '', is_custom: false };
        const freshRecommendation = { id: 'fresh-ai', name: 'Fresh AI', positive_prompt: 'fresh ink', negative_prompt: '', is_custom: false };
        const initialProject = {
            ...project,
            art_direction: { ...project.art_direction!, ai_recommendations: [oldRecommendation] },
        };
        useProjectStore.setState({ currentProject: initialProject, projects: [initialProject], readWatermarks: {}, runningOps: {} });
        vi.mocked(api.analyzeScriptForStyles).mockResolvedValueOnce({ recommendations: [freshRecommendation] });

        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '分析剧本' }));
        expect(await screen.findByText('Fresh AI')).toBeInTheDocument();
        expect(screen.queryByText('Old AI')).not.toBeInTheDocument();

        act(() => useProjectStore.setState({
            currentProject: { ...initialProject, title: 'same project readback' },
            projects: [{ ...initialProject, title: 'same project readback' }],
        }));

        expect(screen.getByText('Fresh AI')).toBeInTheDocument();
        expect(screen.queryByText('Old AI')).not.toBeInTheDocument();
    });

    it('does not let a completed save clear a newer local style draft', async () => {
        const alternate = { id: 'alt', name: 'Alt Style', name_zh: 'Alt Style', category: 'all', positive_prompt: 'alt ink', negative_prompt: '', is_custom: false };
        const save = deferred<Project>();
        vi.mocked(api.getStylePresets).mockResolvedValueOnce({ presets: [alternate], categories: [] });
        vi.mocked(api.saveArtDirection).mockReturnValueOnce(save.promise);
        vi.mocked(api.getProject).mockResolvedValueOnce(read({ ...project, revision: 3 }));

        renderWithIntl(<ArtDirection />);
        fireEvent.click(screen.getByRole('button', { name: '应用并继续' }));
        fireEvent.click(await screen.findByText('Alt Style'));
        fireEvent.click(await screen.findByRole('button', { name: '使用此风格' }));
        await waitFor(() => expect(screen.queryByRole('button', { name: '使用此风格' })).not.toBeInTheDocument());
        expect(screen.getAllByText('Alt Style').length).toBeGreaterThan(1);

        await act(async () => save.resolve(raw({ ...project, revision: 3 })));
        await waitFor(() => expect(api.getProject).toHaveBeenCalledWith('p'));

        await waitFor(() => expect(screen.getAllByText('Alt Style').length).toBeGreaterThan(1));
    });

    it('does not let a completed clear reset a newer local style draft', async () => {
        const alternate = { id: 'alt', name: 'Alt Style', name_zh: 'Alt Style', category: 'all', positive_prompt: 'alt ink', negative_prompt: '', is_custom: false };
        const clear = deferred<Project>();
        vi.mocked(api.getStylePresets).mockResolvedValueOnce({ presets: [alternate], categories: [] });
        vi.mocked(api.getSeries).mockResolvedValueOnce({ ...series, art_direction: { style_config: baselineStyle } });
        vi.mocked(api.clearProjectArtDirection).mockReturnValueOnce(clear.promise);
        vi.mocked(api.getProject).mockResolvedValueOnce(read({ ...project, revision: 3, art_direction: undefined }));

        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '重置为系列' }));
        fireEvent.click(await screen.findByText('Alt Style'));
        fireEvent.click(await screen.findByRole('button', { name: '使用此风格' }));
        await waitFor(() => expect(screen.queryByRole('button', { name: '使用此风格' })).not.toBeInTheDocument());
        expect(screen.getAllByText('Alt Style').length).toBeGreaterThan(1);

        await act(async () => clear.resolve(raw({ ...project, revision: 3, art_direction: undefined })));
        await waitFor(() => expect(api.getProject).toHaveBeenCalledWith('p'));

        await waitFor(() => expect(screen.getAllByText('Alt Style').length).toBeGreaterThan(1));
    });

    it('does not apply a delayed series read after the project environment changes', async () => {
        const pendingSeries = deferred<unknown>();
        vi.mocked(api.getSeries).mockReturnValueOnce(pendingSeries.promise);
        const applySeries = vi.spyOn(useProjectStore.getState(), 'applySeriesReadSnapshot');
        const blankProject: Project = { ...project, art_direction: undefined };
        useProjectStore.setState({ currentProject: blankProject, projects: [blankProject], readWatermarks: {}, runningOps: {} });

        renderWithIntl(<ArtDirection />);
        act(() => useProjectStore.setState({
            currentProject: { ...blankProject, readEnvironment: 'http://other.invalid' },
            projects: [{ ...blankProject, readEnvironment: 'http://other.invalid' }],
        }));
        await act(async () => pendingSeries.resolve({ ...series, art_direction: { style_config: baselineStyle } }));

        expect(applySeries).not.toHaveBeenCalled();
        expect(screen.queryByText('Series baseline')).not.toBeInTheDocument();
    });


    it('reports a plain read failure when initial series fallback read fails without a write', async () => {
        const newerProject = {
            ...project,
            read_versions: { project: 2, series: { id: 's', revision: 10 }, library: 1 },
            art_direction: undefined,
        };
        vi.mocked(api.getSeries).mockResolvedValueOnce({
            id: 's',
            revision: 4,
            title: 'Series',
            art_direction: { style_config: baselineStyle },
        });
        vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));
        useProjectStore.setState({ currentProject: newerProject as Project, projects: [newerProject as Project] });

        renderWithIntl(<ArtDirection />);

        await waitFor(() => expect(api.getProject).toHaveBeenCalledWith('p'));
        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent(/未能读取最新项目状态/);
        expect(alert).not.toHaveTextContent(/编辑已保存/);
        expect(screen.queryByText('Series baseline')).not.toBeInTheDocument();
    });

    it('ignores a delayed series load failure after the project environment changes', async () => {
        const pendingSeries = deferred<unknown>();
        vi.mocked(api.getSeries).mockReturnValueOnce(pendingSeries.promise);
        const blankProject: Project = { ...project, art_direction: undefined };
        useProjectStore.setState({ currentProject: blankProject, projects: [blankProject], readWatermarks: {}, runningOps: {} });

        renderWithIntl(<ArtDirection />);
        act(() => useProjectStore.setState({
            currentProject: { ...blankProject, readEnvironment: 'http://other.invalid' },
            projects: [{ ...blankProject, readEnvironment: 'http://other.invalid' }],
        }));
        await act(async () => pendingSeries.reject(new Error('offline')));

        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('prevents duplicate promote submissions across remount while promotion is in flight', async () => {
        const request = deferred<unknown>();
        vi.mocked(api.updateSeries).mockReturnValueOnce(request.promise);

        const view = renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '推广为系列基线' }));
        expect(api.updateSeries).toHaveBeenCalledTimes(1);

        view.unmount();
        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByRole('button', { name: '推广为系列基线' }));
        expect(api.updateSeries).toHaveBeenCalledTimes(1);

        await act(async () => request.resolve({ ...series, revision: 5, art_direction: project.art_direction }));
    });
});
