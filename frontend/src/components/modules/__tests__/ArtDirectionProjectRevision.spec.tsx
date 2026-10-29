import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { api } from '@/lib/api';
import { useProjectStore, type Project } from '@/store/projectStore';
import ArtDirection from '../ArtDirection';

vi.mock('@/lib/api', () => ({ API_URL: 'http://test.invalid', api: {
    getSeries: vi.fn(), getStylePresets: vi.fn(), updateSeries: vi.fn(),
    analyzeScriptForStyles: vi.fn(), saveArtDirection: vi.fn(), clearProjectArtDirection: vi.fn(), getProject: vi.fn(),
} }));

const storageA = '11111111-1111-4111-8111-111111111111';
const storageB = '22222222-2222-4222-8222-222222222222';
const style = { id: 'saved', name: 'Saved style', positive_prompt: 'saved ink', negative_prompt: '', is_custom: false };
const draftStyle = { id: 'draft', name: 'Local draft', name_zh: 'Local draft', category: 'all', positive_prompt: 'draft ink', negative_prompt: 'draft negative', is_custom: false };
const newerStyle = { ...style, id: 'newer', name: 'Newer saved style', positive_prompt: 'newer ink' };
const baseline = { ...style, id: 'baseline', name: 'Series baseline' };
const aiStyle = { ...draftStyle, id: 'ai-draft', name: 'AI modal draft' };

function project(revision = 2, id = 'p', chosen = style): Project {
    return {
        id, revision, series_id: 's', title: id, originalText: 'Script', readEnvironment: getRuntimeEnvironment()!.key,
        read_versions: { project: revision, series: { id: 's', revision: 4 }, library: 1 },
        characters: [], scenes: [], props: [], frames: [], status: 'draft', createdAt: '', updatedAt: '',
        art_direction: { selected_style_id: chosen.id, style_config: chosen, custom_styles: [], ai_recommendations: [] },
    };
}

function raw(item: Project) {
    return { ...item, original_text: item.originalText, created_at: 1, updated_at: 1, video_tasks: [] };
}

function show(item: Project) {
    useProjectStore.setState({ currentProject: item, projects: [item] });
}

function deferred<T = unknown>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}

async function chooseDraft() {
    fireEvent.click(await screen.findByText('Local draft'));
    fireEvent.click(await screen.findByRole('button', { name: '使用此风格' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '使用此风格' })).not.toBeInTheDocument());
}

const apply = () => screen.getByRole('button', { name: /应用并继续|保存保留的草稿/ });

describe('project ArtDirection draft revision contract', () => {
    beforeEach(() => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA });
        vi.clearAllMocks();
        vi.spyOn(console, 'error').mockImplementation(() => {});
        useProjectStore.setState({ currentProject: project(), projects: [project()], readWatermarks: {}, runningOps: {} });
        vi.mocked(api.getSeries).mockResolvedValue({ id: 's', revision: 4, art_direction: { style_config: baseline } });
        vi.mocked(api.getStylePresets).mockResolvedValue({ presets: [draftStyle], categories: [] });
        vi.mocked(api.analyzeScriptForStyles).mockResolvedValue({ recommendations: [] });
        vi.mocked(api.saveArtDirection).mockResolvedValue(raw(project(3)));
        vi.mocked(api.clearProjectArtDirection).mockResolvedValue(raw({ ...project(3), art_direction: undefined }));
        vi.mocked(api.getProject).mockResolvedValue(raw(project(3)));
    });

    afterEach(() => {
        clearRuntimeEnvironment();
        vi.restoreAllMocks();
    });

    it('sends the revision that hydrated a dirty draft after a newer ordinary project read', async () => {
        vi.mocked(api.saveArtDirection).mockRejectedValueOnce({ response: { status: 409 } });
        renderWithIntl(<ArtDirection />);
        await chooseDraft();
        act(() => show(project(3, 'p', newerStyle)));
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenCalledWith(
            'p', 'draft', expect.objectContaining({ positive_prompt: 'draft ink' }), [], [], 2,
        ));
        expect(screen.getAllByText('Local draft').length).toBeGreaterThan(0);
    });

    it('uses a freshly hydrated revision when the editor has no retained draft', async () => {
        renderWithIntl(<ArtDirection />);
        await screen.findByRole('button', { name: '重置为系列' });
        act(() => show(project(3, 'p', newerStyle)));
        fireEvent.click(screen.getByRole('button', { name: '重置为系列' }));
        await waitFor(() => expect(api.clearProjectArtDirection).toHaveBeenCalledWith('p', 3));
    });

    it.each(['save', 'clear'] as const)('retains a %s conflict until an explicit read and explicit new command', async operation => {
        const mutation = operation === 'save' ? vi.mocked(api.saveArtDirection) : vi.mocked(api.clearProjectArtDirection);
        mutation.mockRejectedValue({ response: { status: 409 } });
        vi.mocked(api.getProject).mockResolvedValue(raw(project(3, 'p', newerStyle)));
        renderWithIntl(<ArtDirection />);
        await chooseDraft();
        const submit = () => fireEvent.click(operation === 'save' ? apply() : screen.getByRole('button', { name: '重置为系列' }));
        submit();
        expect(await screen.findByRole('alert')).toHaveTextContent('内容已被其他入口修改');
        expect(api.getProject).not.toHaveBeenCalled();
        submit();
        expect(mutation).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole('button', { name: '读取最新状态' }));
        await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(1));
        const latest = await screen.findByRole('region', { name: '最新已保存风格' });
        expect(within(latest).getByText('Newer saved style')).toBeInTheDocument();
        expect(screen.getAllByText('Local draft').length).toBeGreaterThan(0);
        expect(mutation).toHaveBeenCalledTimes(1);
        submit();
        await waitFor(() => expect(mutation).toHaveBeenCalledTimes(2));
        if (operation === 'save') expect(api.saveArtDirection).toHaveBeenLastCalledWith(
            'p', 'draft', expect.objectContaining({ negative_prompt: 'draft negative' }), [], [], 3,
        );
        else expect(api.clearProjectArtDirection).toHaveBeenLastCalledWith('p', 3);
        expect(await screen.findByRole('alert')).toHaveTextContent('内容已被其他入口修改');
        expect(api.getProject).toHaveBeenCalledTimes(1);
    });

    it('retains a conflict and its revision when reading the latest version fails', async () => {
        vi.mocked(api.saveArtDirection).mockRejectedValue({ response: { status: 409 } });
        vi.mocked(api.getProject).mockRejectedValue(new Error('offline'));
        renderWithIntl(<ArtDirection />);
        await chooseDraft();
        fireEvent.click(apply());
        fireEvent.click(await screen.findByRole('button', { name: '读取最新状态' }));
        expect(await screen.findByText('读取失败，草稿仍保留，尚未重新提交。')).toBeInTheDocument();
        fireEvent.click(apply());
        expect(api.saveArtDirection).toHaveBeenCalledTimes(1);
        expect(screen.getAllByText('Local draft').length).toBeGreaterThan(0);
    });

    it('does not submit when the draft has no valid project revision', async () => {
        show({ ...project(), series_id: undefined, read_versions: undefined, revision: undefined as unknown as number });
        renderWithIntl(<ArtDirection />);
        await chooseDraft();
        fireEvent.click(apply());
        expect(api.saveArtDirection).not.toHaveBeenCalled();
        expect(await screen.findByRole('alert')).toHaveTextContent('尚未读取到有效版本');
    });

    it('advances an in-flight new draft only to its own ACK, not a newer readback revision', async () => {
        const saving = deferred<ReturnType<typeof raw>>();
        vi.mocked(api.saveArtDirection).mockReturnValueOnce(saving.promise);
        vi.mocked(api.getProject).mockResolvedValue(raw(project(5, 'p', newerStyle)));
        renderWithIntl(<ArtDirection />);
        fireEvent.click(apply());
        await chooseDraft();
        await act(async () => saving.resolve(raw(project(3))));
        await waitFor(() => expect(apply()).toBeEnabled());
        expect(useProjectStore.getState().currentProject?.revision).toBe(5);
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenNthCalledWith(2,
            'p', 'draft', expect.objectContaining({ positive_prompt: 'draft ink' }), [], [], 3,
        ));
    });

    it.each(['project', 'environment'] as const)('keeps the new %s A-B-A save busy when the old save settles', async owner => {
        const oldSave = deferred<ReturnType<typeof raw>>();
        const newSave = deferred<ReturnType<typeof raw>>();
        vi.mocked(api.saveArtDirection).mockReturnValueOnce(oldSave.promise).mockReturnValueOnce(newSave.promise);
        renderWithIntl(<ArtDirection />);
        fireEvent.click(apply());
        act(() => {
            if (owner === 'environment') activateRuntimeEnvironment({ schema_version: 1, storage_id: storageB });
            show(project(2, owner === 'project' ? 'other' : 'p'));
        });
        await screen.findByRole('button', { name: '重置为系列' });
        act(() => {
            if (owner === 'environment') activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA });
            show(project());
        });
        await waitFor(() => expect(apply()).toBeEnabled());
        fireEvent.click(apply());
        expect(api.saveArtDirection).toHaveBeenCalledTimes(2);
        await act(async () => oldSave.resolve(raw(project(3))));
        expect(screen.getByRole('button', { name: '保存中...' })).toBeDisabled();
        expect(api.getProject).not.toHaveBeenCalled();
        await act(async () => newSave.resolve(raw(project(3))));
        await waitFor(() => expect(apply()).toBeEnabled());
        expect(api.getProject).toHaveBeenCalledTimes(1);
    });

    it('ignores an old explicit read after environment A-B-A', async () => {
        const reading = deferred<ReturnType<typeof raw>>();
        vi.mocked(api.saveArtDirection).mockRejectedValueOnce({ response: { status: 409 } });
        vi.mocked(api.getProject).mockReturnValueOnce(reading.promise);
        renderWithIntl(<ArtDirection />);
        await chooseDraft();
        fireEvent.click(apply());
        fireEvent.click(await screen.findByRole('button', { name: '读取最新状态' }));
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: storageB });
            show(project());
        });
        await screen.findByRole('button', { name: '重置为系列' });
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA });
            show(project());
        });
        await chooseDraft();
        await act(async () => reading.resolve(raw(project(99, 'p', newerStyle))));
        expect(useProjectStore.getState().currentProject?.revision).toBe(2);
        expect(screen.queryByText('Newer saved style')).not.toBeInTheDocument();
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenLastCalledWith(
            'p', 'draft', expect.any(Object), [], [], 2,
        ));
    });

    it('does not overlap save and reset commands for the same project style', async () => {
        vi.mocked(api.saveArtDirection).mockReturnValueOnce(deferred<ReturnType<typeof raw>>().promise);
        renderWithIntl(<ArtDirection />);
        const reset = await screen.findByRole('button', { name: '重置为系列' });
        const save = apply();
        act(() => { save.click(); reset.click(); });
        expect(api.saveArtDirection).toHaveBeenCalledTimes(1);
        expect(api.clearProjectArtDirection).not.toHaveBeenCalled();
    });

    it('shows the latest saved prompt without replacing the retained style selection', async () => {
        vi.mocked(api.saveArtDirection).mockRejectedValueOnce({ response: { status: 409 } });
        vi.mocked(api.getProject).mockResolvedValue(raw(project(3, 'p', newerStyle)));
        renderWithIntl(<ArtDirection />);
        await chooseDraft();
        fireEvent.click(apply());
        fireEvent.click(await screen.findByRole('button', { name: '读取最新状态' }));
        const latest = await screen.findByRole('region', { name: '最新已保存风格' });
        expect(within(latest).getByText('newer ink')).toBeInTheDocument();
        expect(screen.getAllByText('Local draft').length).toBeGreaterThan(1);
        expect(api.saveArtDirection).toHaveBeenCalledTimes(1);
    });

    it.each(['preset', 'ai'] as const)('keeps the %s modal authoring version across an ordinary project refresh', async kind => {
        const initial = project();
        show({ ...initial, art_direction: { ...initial.art_direction!, ai_recommendations: [aiStyle] } });
        vi.mocked(api.saveArtDirection).mockRejectedValueOnce({ response: { status: 409 } });
        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByText(kind === 'preset' ? 'Local draft' : 'AI modal draft'));
        fireEvent.click(await screen.findByRole('button', { name: '自定义' }));
        const field = kind === 'preset' ? 0 : 1;
        fireEvent.change(screen.getAllByRole('textbox')[field], { target: { value: 'Authored before the background refresh' } });
        act(() => show(project(3, 'p', newerStyle)));
        expect(screen.getAllByRole('textbox')[field]).toHaveValue('Authored before the background refresh');
        fireEvent.click(screen.getByRole('button', { name: kind === 'preset' ? '应用自定义风格' : '使用该风格' }));
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenCalledWith(
            'p', kind === 'preset' ? 'draft' : 'ai-draft', expect.objectContaining({
                [kind === 'preset' ? 'positive_prompt' : 'negative_prompt']: 'Authored before the background refresh',
            }), [], [aiStyle], 2,
        ));
    });

    it.each(['preset', 'ai'] as const)('lets a clean project refresh after cancelling %s modal edits', async kind => {
        const initial = project();
        show({ ...initial, art_direction: { ...initial.art_direction!, ai_recommendations: [aiStyle] } });
        vi.mocked(api.saveArtDirection).mockRejectedValueOnce({ response: { status: 409 } });
        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByText(kind === 'preset' ? 'Local draft' : 'AI modal draft'));
        fireEvent.click(await screen.findByRole('button', { name: '自定义' }));
        fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Discard this modal draft' } });
        act(() => show(project(3, 'p', newerStyle)));
        fireEvent.click(screen.getByRole('button', { name: '取消' }));
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenCalledWith(
            'p', 'newer', expect.objectContaining({ positive_prompt: 'newer ink' }), [], [aiStyle], 3,
        ));
    });

    it.each(['project', 'environment'] as const)('ignores the exiting modal Apply when the %s owner changes and returns', async owner => {
        vi.mocked(api.saveArtDirection).mockRejectedValue({ response: { status: 409 } });
        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByText('Local draft'));
        fireEvent.click(await screen.findByRole('button', { name: '自定义' }));
        fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Old owner draft' } });
        const oldApply = screen.getByRole('button', { name: '应用自定义风格' });
        act(() => {
            if (owner === 'environment') activateRuntimeEnvironment({ schema_version: 1, storage_id: storageB });
            show(project(3, owner === 'project' ? 'other' : 'p', newerStyle));
        });
        expect(oldApply).toBeInTheDocument();
        fireEvent.click(oldApply);
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenNthCalledWith(1,
            owner === 'project' ? 'other' : 'p', 'newer', expect.objectContaining({ positive_prompt: 'newer ink' }), [], [], 3,
        ));
        act(() => {
            if (owner === 'environment') activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA });
            show(project(4, 'p', newerStyle));
        });
        if (oldApply.isConnected) fireEvent.click(oldApply);
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenNthCalledWith(2,
            'p', 'newer', expect.objectContaining({ positive_prompt: 'newer ink' }), [], [], 4,
        ));
    });

    it.each(['preset', 'ai'] as const)('does not let an exiting %s Customize callback acquire the new project', async kind => {
        const initial = project();
        show({ ...initial, art_direction: { ...initial.art_direction!, ai_recommendations: [aiStyle] } });
        vi.mocked(api.saveArtDirection).mockRejectedValueOnce({ response: { status: 409 } });
        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByText(kind === 'preset' ? 'Local draft' : 'AI modal draft'));
        const oldCustomize = await screen.findByRole('button', { name: '自定义' });
        const oldApply = screen.getByRole('button', { name: kind === 'preset' ? '使用此风格' : '使用该风格' });
        act(() => show(project(4, 'other', newerStyle)));
        expect(oldCustomize).toBeInTheDocument();
        fireEvent.click(oldCustomize);
        fireEvent.click(oldApply);
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenCalledWith(
            'other', 'newer', expect.objectContaining({ positive_prompt: 'newer ink' }), [], [], 4,
        ));
    });

    it('does not move an open authored modal to an in-flight save ACK or its newer readback', async () => {
        const saving = deferred<ReturnType<typeof raw>>();
        vi.mocked(api.saveArtDirection).mockReturnValueOnce(saving.promise).mockRejectedValueOnce({ response: { status: 409 } });
        vi.mocked(api.getProject).mockResolvedValue(raw(project(5, 'p', newerStyle)));
        renderWithIntl(<ArtDirection />);
        fireEvent.click(apply());
        fireEvent.click(await screen.findByText('Local draft'));
        fireEvent.click(await screen.findByRole('button', { name: '自定义' }));
        fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Authored while saving' } });
        await act(async () => saving.resolve(raw(project(3))));
        await waitFor(() => expect(useProjectStore.getState().currentProject?.revision).toBe(5));
        fireEvent.click(screen.getByRole('button', { name: '应用自定义风格' }));
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenNthCalledWith(2,
            'p', 'draft', expect.objectContaining({ positive_prompt: 'Authored while saving' }), [], [], 2,
        ));
    });

    it('carries a modal authoring version through the series override confirmation', async () => {
        show({ ...project(), art_direction: undefined });
        vi.mocked(api.saveArtDirection).mockRejectedValueOnce({ response: { status: 409 } });
        renderWithIntl(<ArtDirection />);
        fireEvent.click(await screen.findByText('Local draft'));
        fireEvent.click(await screen.findByRole('button', { name: '自定义' }));
        fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Series override draft' } });
        fireEvent.click(screen.getByRole('button', { name: '应用自定义风格' }));
        act(() => show({ ...project(3), art_direction: undefined }));
        fireEvent.click(await screen.findByRole('button', { name: '进入预览' }));
        fireEvent.click(apply());
        await waitFor(() => expect(api.saveArtDirection).toHaveBeenCalledWith(
            'p', 'draft', expect.objectContaining({ positive_prompt: 'Series override draft' }), [], [], 2,
        ));
    });
});
