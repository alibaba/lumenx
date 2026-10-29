import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from './test-utils';
import { api } from '@/lib/api';
import { useProjectStore, type Series } from '@/store/projectStore';
import SeriesArtDirectionPanel from '../SeriesArtDirectionPanel';

vi.mock('@/lib/api', () => ({ API_URL: 'http://test.invalid', api: {
    getSeries: vi.fn(), getStylePresets: vi.fn(), updateSeries: vi.fn(),
} }));

const initial: Series = { id: 's', revision: 4, title: 'Series', description: '', created_at: 1, updated_at: 1, characters: [], scenes: [], props: [], episode_ids: [] };
const preset = { id: 'p', name: 'New style', name_zh: '新风格', category: 'test', positive_prompt: 'new prompt', negative_prompt: '', thumbnail: null };

async function chooseStyle() {
    fireEvent.click(await screen.findByText('新风格'));
    fireEvent.click(screen.getByRole('button', { name: '使用此风格' }));
}

describe('Series art direction revision contract', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        useProjectStore.setState({ currentSeries: null, seriesList: [] });
        vi.mocked(api.getSeries).mockResolvedValue(initial);
        vi.mocked(api.getStylePresets).mockResolvedValue({ presets: [preset], categories: [] });
        vi.mocked(api.updateSeries).mockImplementation(async (id, data) => ({ ...initial, ...data, revision: data.expected_revision + 1 }));
    });

    it('saves and clears using each acknowledged revision without fetching a replacement version', async () => {
        renderWithIntl(<SeriesArtDirectionPanel seriesId="s" onSaved={vi.fn()} />);
        await chooseStyle();
        fireEvent.click(screen.getByRole('button', { name: '保存为系列基线' }));
        await waitFor(() => expect(api.updateSeries).toHaveBeenCalledWith('s', expect.objectContaining({ expected_revision: 4 })));
        fireEvent.click(await screen.findByRole('button', { name: '清除基线' }));
        await waitFor(() => expect(api.updateSeries).toHaveBeenLastCalledWith('s', { expected_revision: 5, art_direction: null }));
        expect(api.getSeries).toHaveBeenCalledTimes(1);
    });

    it.each([409, 503])('preserves the selected style and reports HTTP %s without refetch or automatic retry', async status => {
        vi.mocked(api.updateSeries).mockRejectedValue({ response: { status } });
        renderWithIntl(<SeriesArtDirectionPanel seriesId="s" onSaved={vi.fn()} />);
        await chooseStyle();
        fireEvent.click(screen.getByRole('button', { name: '保存为系列基线' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/草稿|未保存/);
        expect(screen.getByText('New style')).toBeInTheDocument();
        expect(api.getSeries).toHaveBeenCalledTimes(1);
        expect(api.updateSeries).toHaveBeenCalledTimes(1);
    });

    it('does not replace a reopened baseline with a delayed save from the previous visit', async () => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateSeries).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        const onSaved = vi.fn();
        const view = renderWithIntl(<SeriesArtDirectionPanel seriesId="s" onSaved={onSaved} />);
        await chooseStyle();
        fireEvent.click(screen.getByRole('button', { name: '保存为系列基线' }));
        vi.mocked(api.getSeries).mockResolvedValueOnce({ ...initial, id: 'other' });
        view.rerender(<SeriesArtDirectionPanel seriesId="other" onSaved={onSaved} />);
        await screen.findByText('新风格');
        vi.mocked(api.getSeries).mockResolvedValueOnce({ ...initial, revision: 6, art_direction: { style_config: { ...preset, name: '更新的风格' } } });
        view.rerender(<SeriesArtDirectionPanel seriesId="s" onSaved={onSaved} />);
        await screen.findByText('当前基线：更新的风格');
        await act(async () => resolve({ ...initial, revision: 5, art_direction: { style_config: preset } }));
        expect(screen.getByText('当前基线：更新的风格')).toBeInTheDocument();
        expect(onSaved).not.toHaveBeenCalled();
    });

    it('does not restore the previous active series when its save finishes after unmount', async () => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateSeries).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        const onSaved = vi.fn();
        const view = renderWithIntl(<SeriesArtDirectionPanel seriesId="s" onSaved={onSaved} />);
        await chooseStyle();
        fireEvent.click(screen.getByRole('button', { name: '保存为系列基线' }));
        view.unmount();
        const nextSeries = { ...initial, id: 'other', revision: 9 };
        useProjectStore.setState({ currentSeries: nextSeries, seriesList: [nextSeries] });

        await act(async () => resolve({ ...initial, revision: 5, art_direction: { style_config: preset } }));

        expect(useProjectStore.getState().currentSeries).toEqual(nextSeries);
        expect(onSaved).not.toHaveBeenCalled();
    });

    it.each(['currentSeries', 'seriesList', 'both'] as const)('does not roll back a newer %s snapshot on save completion', async target => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateSeries).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        useProjectStore.setState({ currentSeries: initial, seriesList: [initial] });
        renderWithIntl(<SeriesArtDirectionPanel seriesId="s" onSaved={vi.fn()} />);
        await chooseStyle();
        fireEvent.click(screen.getByRole('button', { name: '保存为系列基线' }));
        const newer = { ...initial, revision: 6, title: '较新的系列数据' };
        useProjectStore.setState({
            ...(target !== 'seriesList' ? { currentSeries: newer } : {}),
            ...(target !== 'currentSeries' ? { seriesList: [newer] } : {}),
        });

        await act(async () => resolve({ ...initial, revision: 5, art_direction: { style_config: preset } }));

        if (target !== 'seriesList') expect(useProjectStore.getState().currentSeries).toEqual(newer);
        if (target !== 'currentSeries') expect(useProjectStore.getState().seriesList[0]).toEqual(newer);
    });
});
