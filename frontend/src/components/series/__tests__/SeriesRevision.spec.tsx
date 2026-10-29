import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from './test-utils';
import { api } from '@/lib/api';
import SeriesDetailPage from '../SeriesDetailPage';

vi.mock('@/lib/api', () => ({
    API_URL: 'http://test.invalid',
    api: { getSeries: vi.fn(), getSeriesEpisodes: vi.fn(), updateSeries: vi.fn() },
}));

// Exercise the parent refresh callback without loading unrelated settings UI.
vi.mock('next/dynamic', () => ({
    default: () => function Settings({ isOpen, onSaved }: { isOpen?: boolean; onSaved?: () => void }) {
        return isOpen ? <button onClick={onSaved}>完成设置</button> : null;
    },
}));

const series = { id: 's', revision: 4, title: '系列原名', description: '', characters: [], scenes: [], props: [], episode_ids: [], created_at: 0, updated_at: 0 };

async function startEditing() {
    const title = await screen.findByRole('heading', { name: '系列原名', level: 1 });
    fireEvent.doubleClick(title);
    return screen.getByDisplayValue('系列原名');
}

async function refreshFromSettings() {
    fireEvent.click(screen.getByRole('button', { name: '提示词配置' }));
    const button = await screen.findByRole('button', { name: '完成设置' });
    await act(async () => { fireEvent.click(button); });
}

describe('Series title revision contract', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.mocked(api.getSeries).mockResolvedValue(series);
        vi.mocked(api.getSeriesEpisodes).mockResolvedValue([]);
        vi.mocked(api.updateSeries).mockImplementation(async (id, data) => ({ ...series, ...data, revision: 5 }));
    });

    it('submits the displayed revision and uses the successful response for the next edit', async () => {
        renderWithIntl(<SeriesDetailPage seriesId="s" />);
        const input = await startEditing();
        fireEvent.change(input, { target: { value: '新系列名' } });
        fireEvent.blur(input);
        await screen.findByRole('heading', { name: '新系列名', level: 1 });
        expect(api.updateSeries).toHaveBeenCalledWith('s', { title: '新系列名', expected_revision: 4 });
        fireEvent.doubleClick(screen.getByRole('heading', { name: '新系列名', level: 1 }));
        const second = screen.getByDisplayValue('新系列名');
        fireEvent.change(second, { target: { value: '第三版名称' } });
        fireEvent.blur(second);
        await waitFor(() => expect(api.updateSeries).toHaveBeenLastCalledWith('s', { title: '第三版名称', expected_revision: 5 }));
        expect(api.getSeries).toHaveBeenCalledTimes(1);
    });

    it.each([409, 503])('keeps the draft and exposes HTTP %s without fetching a newer revision or retrying', async status => {
        vi.mocked(api.updateSeries).mockRejectedValue({ response: { status } });
        renderWithIntl(<SeriesDetailPage seriesId="s" />);
        const input = await startEditing();
        fireEvent.change(input, { target: { value: '保留这个名字' } });
        fireEvent.blur(input);
        expect(await screen.findByRole('alert')).toHaveTextContent(/草稿|未保存/);
        expect(screen.getByDisplayValue('保留这个名字')).toBeInTheDocument();
        fireEvent.blur(input);
        expect(api.updateSeries).toHaveBeenCalledTimes(1);
        expect(api.getSeries).toHaveBeenCalledTimes(1);
    });

    it('does not display the previous series as an editable fallback when the next series cannot load', async () => {
        const view = renderWithIntl(<SeriesDetailPage seriesId="s" />);
        await screen.findByRole('heading', { name: '系列原名', level: 1 });
        vi.mocked(api.getSeries).mockRejectedValueOnce({ response: { status: 503 } });
        view.rerender(<SeriesDetailPage seriesId="other" />);
        expect(await screen.findByText('系列未找到')).toBeInTheDocument();
        expect(screen.queryByRole('heading', { name: '系列原名', level: 1 })).not.toBeInTheDocument();
        expect(api.updateSeries).not.toHaveBeenCalled();
    });

    it('does not apply a delayed save over the newer snapshot after leaving and reopening the same series', async () => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateSeries).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        const view = renderWithIntl(<SeriesDetailPage seriesId="s" />);
        const input = await startEditing();
        fireEvent.change(input, { target: { value: '较早提交的名字' } });
        fireEvent.blur(input);
        vi.mocked(api.getSeries).mockResolvedValueOnce({ ...series, id: 'other', title: '另一个系列' });
        view.rerender(<SeriesDetailPage seriesId="other" />);
        await screen.findByRole('heading', { name: '另一个系列', level: 1 });
        vi.mocked(api.getSeries).mockResolvedValueOnce({ ...series, revision: 6, title: '已读到更新的名字' });
        view.rerender(<SeriesDetailPage seriesId="s" />);
        await screen.findByRole('heading', { name: '已读到更新的名字', level: 1 });
        await act(async () => resolve({ ...series, revision: 5, title: '较早提交的名字' }));
        expect(screen.getByRole('heading', { name: '已读到更新的名字', level: 1 })).toBeInTheDocument();
    });

    it('ignores an old settings refresh after navigating to another series', async () => {
        let resolve!: (value: unknown) => void;
        const view = renderWithIntl(<SeriesDetailPage seriesId="s" />);
        await screen.findByRole('heading', { name: '系列原名', level: 1 });
        vi.mocked(api.getSeries).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        await refreshFromSettings();
        vi.mocked(api.getSeries).mockResolvedValueOnce({ ...series, id: 'other', revision: 2, title: '另一个系列' });
        view.rerender(<SeriesDetailPage seriesId="other" />);
        await screen.findByRole('heading', { name: '另一个系列', level: 1 });

        await act(async () => resolve({ ...series, revision: 5, title: '旧页面的刷新结果' }));

        expect(screen.getByRole('heading', { name: '另一个系列', level: 1 })).toBeInTheDocument();
    });

    it('does not let an older refresh undo a newly acknowledged title and revision', async () => {
        let resolve!: (value: unknown) => void;
        renderWithIntl(<SeriesDetailPage seriesId="s" />);
        await screen.findByRole('heading', { name: '系列原名', level: 1 });
        vi.mocked(api.getSeries).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        await refreshFromSettings();
        const input = await startEditing();
        fireEvent.change(input, { target: { value: '保存后的新名称' } });
        fireEvent.blur(input);
        await screen.findByRole('heading', { name: '保存后的新名称', level: 1 });

        await act(async () => resolve(series));

        const heading = screen.getByRole('heading', { name: '保存后的新名称', level: 1 });
        fireEvent.doubleClick(heading);
        fireEvent.change(screen.getByDisplayValue('保存后的新名称'), { target: { value: '再次编辑' } });
        fireEvent.blur(screen.getByDisplayValue('再次编辑'));
        await waitFor(() => expect(api.updateSeries).toHaveBeenLastCalledWith('s', { title: '再次编辑', expected_revision: 5 }));
    });

    it('does not let a delayed title acknowledgement replace a newer settings refresh', async () => {
        let resolve!: (value: unknown) => void;
        vi.mocked(api.updateSeries).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        renderWithIntl(<SeriesDetailPage seriesId="s" />);
        const input = await startEditing();
        fireEvent.change(input, { target: { value: '较早提交的名称' } });
        fireEvent.blur(input);
        vi.mocked(api.getSeries).mockResolvedValueOnce({ ...series, revision: 6, title: '刷新后更新的名称' });
        await refreshFromSettings();

        await act(async () => resolve({ ...series, revision: 5, title: '较早提交的名称' }));

        expect(screen.getByRole('heading', { name: '刷新后更新的名称', level: 1 })).toBeInTheDocument();
    });
});
