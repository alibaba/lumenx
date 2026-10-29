import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ModelSettingsModal from '@/components/common/ModelSettingsModal';
import PromptConfigModal from '@/components/project/PromptConfigModal';
import { api } from '@/lib/api';
import { useProjectStore, type Project } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from '../../modules/__tests__/frame-command-fixtures';

vi.mock('@/lib/api', () => ({
    API_URL: 'http://api.test',
    api: {
        updateModelSettings: vi.fn(),
        getPromptConfig: vi.fn(),
        updatePromptConfig: vi.fn(),
        getProject: vi.fn(),
    },
}));

vi.mock('@/components/common/GroupedModelGrid', () => ({
    default: ({ selectedId, onSelect }: { selectedId: string; onSelect: (id: string) => void }) => (
        <button type="button" data-testid={`model-${selectedId}`} onClick={() => onSelect(`${selectedId}-changed`)}>{selectedId}</button>
    ),
}));

function rawProject(id = 'a', revision = 4, patch: Record<string, unknown> = {}) {
    return {
        ...ack([], id, revision),
        title: `Project ${id}`,
        original_text: 'server text',
        model_settings: { t2i_model: 'server-t2i', i2i_model: 'server-i2i', image_model: 'server-image', i2v_model: 'server-i2v', r2v_model: 'server-r2v', character_aspect_ratio: '1:1', scene_aspect_ratio: '16:9', prop_aspect_ratio: '1:1', storyboard_aspect_ratio: '16:9' },
        prompt_config: { storyboard_polish: 'server-story', video_polish: 'server-video', r2v_polish: 'server-r2v-prompt', polish_model: 'server-model', style_analysis: 'server-style' },
        ...patch,
    };
}

function storeProject(id = 'a', revision = 3, patch: Partial<Project> = {}): Project {
    return {
        ...project(id, revision),
        model_settings: { t2i_model: 'old-t2i', i2i_model: 'old-i2i', image_model: 'old-image', i2v_model: 'old-i2v', r2v_model: 'old-r2v', character_aspect_ratio: '1:1', scene_aspect_ratio: '16:9', prop_aspect_ratio: '1:1', storyboard_aspect_ratio: '16:9' },
        prompt_config: { storyboard_polish: 'old-story', video_polish: 'old-video', r2v_polish: 'old-r2v-prompt', polish_model: 'old-model' } as Project['prompt_config'],
        ...patch,
    };
}

function seed(projectValue = storeProject()) {
    useProjectStore.setState({ projects: [projectValue], currentProject: projectValue, currentSeries: null, readWatermarks: {}, runningOps: {} });
}

beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    seed();
});

describe('project settings modals read back committed project state', () => {
    it('does not apply a raw model-settings ACK or close when the confirming read fails', async () => {
        const onClose = vi.fn();
        vi.mocked(api.updateModelSettings).mockResolvedValue(rawProject('a', 4, { model_settings: { t2i_model: 'raw-new' } }));
        vi.mocked(api.getProject)
            .mockRejectedValueOnce(new Error('read failed'))
            .mockResolvedValueOnce(enriched(rawProject('a', 3, { model_settings: { t2i_model: 'stale-read' } }), 8))
            .mockResolvedValueOnce(enriched(rawProject('a', 4, { model_settings: { t2i_model: 'confirmed-after-reread', i2i_model: 'confirmed-i2i', image_model: 'confirmed-image', i2v_model: 'confirmed-i2v', r2v_model: 'confirmed-r2v', character_aspect_ratio: '1:1', scene_aspect_ratio: '16:9', prop_aspect_ratio: '1:1', storyboard_aspect_ratio: '16:9' } }), 8));

        renderWithIntl(<ModelSettingsModal isOpen onClose={onClose} />);
        fireEvent.click(screen.getByRole('button', { name: /保存设置/ }));

        await waitFor(() => expect(screen.getByText(/编辑已保存，但读取失败/)).toBeInTheDocument());
        expect(onClose).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.model_settings?.t2i_model).toBe('old-t2i');
        expect(api.getProject).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByRole('button', { name: /重新读取/ }));
        await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(2));
        expect(onClose).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.model_settings?.t2i_model).toBe('old-t2i');

        fireEvent.click(screen.getByRole('button', { name: /重新读取/ }));
        await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
        expect(useProjectStore.getState().currentProject?.model_settings?.t2i_model).toBe('confirmed-after-reread');
    });

    it('blocks duplicate model-settings saves while one save is in flight', async () => {
        const pending = deferred<unknown>();
        vi.mocked(api.updateModelSettings).mockReturnValue(pending.promise);
        renderWithIntl(<ModelSettingsModal isOpen onClose={vi.fn()} />);

        const save = screen.getByRole('button', { name: /保存设置/ });
        fireEvent.click(save);
        fireEvent.click(save);

        expect(api.updateModelSettings).toHaveBeenCalledTimes(1);
    });

    it('saves model settings through raw ACK revision and applies only the confirmed read', async () => {
        const onClose = vi.fn();
        vi.mocked(api.updateModelSettings).mockResolvedValue(rawProject('a', 4, { model_settings: { t2i_model: 'raw-only' } }));
        vi.mocked(api.getProject).mockResolvedValue(enriched(rawProject('a', 5, { model_settings: { t2i_model: 'confirmed-t2i', i2i_model: 'confirmed-i2i', image_model: 'confirmed-image', i2v_model: 'confirmed-i2v', r2v_model: 'confirmed-r2v', character_aspect_ratio: '1:1', scene_aspect_ratio: '16:9', prop_aspect_ratio: '1:1', storyboard_aspect_ratio: '16:9' } }), 8));

        renderWithIntl(<ModelSettingsModal isOpen onClose={onClose} />);
        fireEvent.click(screen.getByRole('button', { name: /保存设置/ }));

        await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
        expect(api.getProject).toHaveBeenCalledTimes(1);
        expect(useProjectStore.getState().currentProject?.model_settings?.t2i_model).toBe('confirmed-t2i');
        expect(useProjectStore.getState().currentProject?.model_settings?.t2i_model).not.toBe('raw-only');
    });

    it('keeps model drafts and duplicate-save lock across same-project refreshes', async () => {
        const pending = deferred<unknown>();
        vi.mocked(api.updateModelSettings).mockReturnValue(pending.promise);
        renderWithIntl(<ModelSettingsModal isOpen onClose={vi.fn()} />);

        fireEvent.click(screen.getAllByTestId('model-wan2.7-image-pro')[0]);
        expect(screen.getByTestId('model-wan2.7-image-pro-changed')).toBeInTheDocument();

        act(() => useProjectStore.setState({
            currentProject: storeProject('a', 4, { model_settings: { ...storeProject('a').model_settings!, t2i_model: 'polled-t2i' } }),
        }));
        expect(screen.getByTestId('model-wan2.7-image-pro-changed')).toBeInTheDocument();

        const save = screen.getByRole('button', { name: /保存设置/ });
        fireEvent.click(save);
        act(() => useProjectStore.setState({
            currentProject: storeProject('a', 5, { model_settings: { ...storeProject('a').model_settings!, t2i_model: 'second-polled-t2i' } }),
        }));
        fireEvent.click(save);

        expect(api.updateModelSettings).toHaveBeenCalledTimes(1);
    });

    it('does not read or close after an obsolete model ACK from an unmounted or reopened session', async () => {
        const onClose = vi.fn();
        const save = deferred<unknown>();
        vi.mocked(api.updateModelSettings).mockReturnValue(save.promise);
        const { rerender, unmount } = renderWithIntl(<ModelSettingsModal isOpen onClose={onClose} />);

        fireEvent.click(screen.getByRole('button', { name: /保存设置/ }));
        act(() => useProjectStore.setState({ projects: [storeProject('b')], currentProject: storeProject('b'), readWatermarks: {}, runningOps: {} }));
        rerender(<ModelSettingsModal isOpen onClose={onClose} />);
        act(() => useProjectStore.setState({ projects: [storeProject('a')], currentProject: storeProject('a'), readWatermarks: {}, runningOps: {} }));
        rerender(<ModelSettingsModal isOpen onClose={onClose} />);
        unmount();

        await act(async () => save.resolve(rawProject('a', 4)));

        expect(api.getProject).not.toHaveBeenCalled();
        expect(onClose).not.toHaveBeenCalled();
    });

    it('saves prompt config through one enriched project read and preserves explicit empty strings', async () => {
        const onClose = vi.fn();
        vi.mocked(api.getPromptConfig).mockResolvedValue({
            revision: 3,
            prompt_config: { storyboard_polish: 'loaded-story', video_polish: 'loaded-video', r2v_polish: 'loaded-r2v', polish_model: 'qwen3.7-plus' },
            defaults: { storyboard_polish: 'default story', video_polish: 'default video', r2v_polish: 'default r2v' },
        });
        vi.mocked(api.updatePromptConfig).mockResolvedValue({ revision: 4, prompt_config: { storyboard_polish: 'partial-stale' } });
        vi.mocked(api.getProject).mockResolvedValue(enriched(rawProject('a', 4, { prompt_config: { storyboard_polish: '', video_polish: 'confirmed-video', r2v_polish: 'confirmed-r2v', polish_model: 'confirmed-model' } }), 8));

        renderWithIntl(<PromptConfigModal isOpen onClose={onClose} />);
        const textareas = await screen.findAllByRole('textbox');
        fireEvent.change(textareas[0], { target: { value: '' } });
        fireEvent.click(screen.getByRole('button', { name: /^保存$/ }));

        await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
        expect(api.updatePromptConfig).toHaveBeenCalledWith('a', expect.objectContaining({ storyboard_polish: '' }));
        expect(api.getProject).toHaveBeenCalledTimes(1);
        expect(useProjectStore.getState().currentProject?.prompt_config?.storyboard_polish).toBe('');
        expect(useProjectStore.getState().currentProject?.prompt_config?.video_polish).toBe('confirmed-video');
    });

    it('does not let a late prompt save from a previous modal session close the reopened modal', async () => {
        const onClose = vi.fn();
        const save = deferred<unknown>();
        vi.mocked(api.getPromptConfig).mockResolvedValue({
            revision: 3,
            prompt_config: { storyboard_polish: 'loaded-story', video_polish: '', r2v_polish: '', polish_model: '' },
            defaults: { storyboard_polish: 'default story', video_polish: 'default video', r2v_polish: 'default r2v' },
        });
        vi.mocked(api.updatePromptConfig).mockReturnValue(save.promise);
        vi.mocked(api.getProject).mockResolvedValue(enriched(rawProject('a', 4), 8));

        const { rerender } = renderWithIntl(<PromptConfigModal isOpen onClose={onClose} />);
        await screen.findAllByRole('textbox');
        fireEvent.click(screen.getByRole('button', { name: /^保存$/ }));

        const next = storeProject('b');
        act(() => useProjectStore.setState({ projects: [next], currentProject: next, readWatermarks: {}, runningOps: {} }));
        rerender(<PromptConfigModal isOpen onClose={onClose} />);
        await act(async () => save.resolve({ revision: 4, prompt_config: { storyboard_polish: 'saved-a' } }));

        expect(onClose).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
    });

    it('does not read or close after an obsolete prompt ACK from an unmounted session', async () => {
        const onClose = vi.fn();
        const save = deferred<unknown>();
        vi.mocked(api.getPromptConfig).mockResolvedValue({
            revision: 3,
            prompt_config: { storyboard_polish: 'loaded-story', video_polish: '', r2v_polish: '', polish_model: '' },
            defaults: { storyboard_polish: 'default story', video_polish: 'default video', r2v_polish: 'default r2v' },
        });
        vi.mocked(api.updatePromptConfig).mockReturnValue(save.promise);

        const view = renderWithIntl(<PromptConfigModal isOpen onClose={onClose} />);
        await screen.findAllByRole('textbox');
        fireEvent.click(screen.getByRole('button', { name: /^保存$/ }));
        view.unmount();
        await act(async () => save.resolve({ revision: 4, prompt_config: { storyboard_polish: 'saved-after-unmount' } }));

        expect(api.getProject).not.toHaveBeenCalled();
        expect(onClose).not.toHaveBeenCalled();
    });

    it('fails prompt save closed when the ACK has no committed revision', async () => {
        const onClose = vi.fn();
        vi.mocked(api.getPromptConfig).mockResolvedValue({
            revision: 3,
            prompt_config: { storyboard_polish: 'loaded-story', video_polish: '', r2v_polish: '', polish_model: '' },
            defaults: { storyboard_polish: 'default story', video_polish: 'default video', r2v_polish: 'default r2v' },
        });
        vi.mocked(api.updatePromptConfig).mockResolvedValue({ prompt_config: { storyboard_polish: 'missing-revision' } });

        renderWithIntl(<PromptConfigModal isOpen onClose={onClose} />);
        await screen.findAllByRole('textbox');
        fireEvent.click(screen.getByRole('button', { name: /^保存$/ }));

        await waitFor(() => expect(screen.getByText(/保存提示词.*失败|保存失败/)).toBeInTheDocument());
        expect(api.getProject).not.toHaveBeenCalled();
        expect(onClose).not.toHaveBeenCalled();
    });

    it('keeps prompt drafts after a saved-write readback failure and rereads only on explicit request', async () => {
        const onClose = vi.fn();
        vi.mocked(api.getPromptConfig).mockResolvedValue({
            revision: 3,
            prompt_config: { storyboard_polish: 'draft', video_polish: '', r2v_polish: '', polish_model: '' },
            defaults: { storyboard_polish: 'default story', video_polish: 'default video', r2v_polish: 'default r2v' },
        });
        vi.mocked(api.updatePromptConfig).mockResolvedValue({ revision: 4, prompt_config: { storyboard_polish: 'partial' } });
        vi.mocked(api.getProject)
            .mockRejectedValueOnce(new Error('read failed'))
            .mockResolvedValueOnce(enriched(rawProject('a', 3, { prompt_config: { storyboard_polish: 'stale-reread', video_polish: '', r2v_polish: '', polish_model: '' } }), 8))
            .mockResolvedValueOnce(enriched(rawProject('a', 4, { prompt_config: { storyboard_polish: 'confirmed-after-reread', video_polish: '', r2v_polish: '', polish_model: '' } }), 8));

        renderWithIntl(<PromptConfigModal isOpen onClose={onClose} />);
        const [storyboard] = await screen.findAllByRole('textbox');
        fireEvent.change(storyboard, { target: { value: 'kept draft' } });
        fireEvent.click(screen.getByRole('button', { name: /^保存$/ }));

        await waitFor(() => expect(screen.getByText(/编辑已保存，但读取失败/)).toBeInTheDocument());
        expect((storyboard as HTMLTextAreaElement).value).toBe('kept draft');
        expect(api.updatePromptConfig).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole('button', { name: /重新读取/ }));
        await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(2));
        expect(onClose).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.prompt_config?.storyboard_polish).toBe('old-story');

        fireEvent.click(screen.getByRole('button', { name: /重新读取/ }));
        await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
        expect(api.updatePromptConfig).toHaveBeenCalledTimes(1);
        expect(api.getProject).toHaveBeenCalledTimes(3);
    });
});


it.each(['model', 'prompt'] as const)('keeps the new %s session read floor when an obsolete ACK arrives', async kind => {
    const old = deferred<unknown>();
    const onClose = vi.fn();
    vi.mocked(api.getPromptConfig).mockResolvedValue({ revision: 3, prompt_config: { storyboard_polish: 'draft', video_polish: '', r2v_polish: '' }, defaults: { storyboard_polish: 'default story', video_polish: 'default video', r2v_polish: 'default r2v' } });
    const update = kind === 'model' ? vi.mocked(api.updateModelSettings) : vi.mocked(api.updatePromptConfig);
    update.mockReturnValueOnce(old.promise).mockResolvedValueOnce(kind === 'model' ? rawProject('a', 8) : { revision: 8, prompt_config: {} });
    vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(enriched(rawProject('a', 5), 8));
    const node = (open: boolean) => kind === 'model' ? <ModelSettingsModal isOpen={open} onClose={onClose} /> : <PromptConfigModal isOpen={open} onClose={onClose} />;
    const view = renderWithIntl(node(true));
    const clickSave = async () => {
        if (kind === 'prompt') await screen.findAllByRole('textbox');
        fireEvent.click(screen.getByRole('button', { name: kind === 'model' ? /保存设置/ : /^保存$/ }));
    };
    await clickSave();
    view.rerender(node(false));
    view.rerender(node(true));
    await clickSave();
    await screen.findByRole('alert');
    await act(async () => old.resolve(kind === 'model' ? rawProject('a', 4) : { revision: 4, prompt_config: {} }));
    fireEvent.click(screen.getByRole('button', { name: /重新读取/ }));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(2));
    expect(onClose).not.toHaveBeenCalled();
    expect(useProjectStore.getState().currentProject?.revision).toBe(3);
});

it('refuses a prompt editor load older than the confirmed project', async () => {
    vi.mocked(api.getPromptConfig).mockResolvedValue({ revision: 2, prompt_config: { storyboard_polish: 'stale', video_polish: '', r2v_polish: '' }, defaults: { storyboard_polish: 'default story', video_polish: 'default video', r2v_polish: 'default r2v' } });
    renderWithIntl(<PromptConfigModal isOpen onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: /^保存$/ })).toBeDisabled());
    expect(await screen.findByText(/加载.*失败|无法加载/)).toBeInTheDocument();
    expect(api.updatePromptConfig).not.toHaveBeenCalled();
});
