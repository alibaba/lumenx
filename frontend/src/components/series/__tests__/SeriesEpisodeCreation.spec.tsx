import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithIntl } from './test-utils';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { deferred } from '../../modules/__tests__/frame-command-fixtures';
import SeriesDetailPage from '../SeriesDetailPage';

vi.mock('axios');
vi.mock('@/lib/apiUrl', () => ({ API_URL: 'http://episode-backend.invalid' }));

const ENV_A = '11111111-1111-4111-8111-111111111111';
const ENV_B = '22222222-2222-4222-8222-222222222222';
const base = 'http://episode-backend.invalid';
const savedSeries = (id = 'series-1') => ({
    id, revision: 4, title: id === 'series-1' ? '系列一' : '系列二',
    description: '', workflow_mode: 'r2v', default_generation_mode: 'i2v',
    characters: [], scenes: [], props: [], episode_ids: [], created_at: 1, updated_at: 1,
});
const savedEpisode = (seriesId = 'series-1') => ({
    id: 'episode-created', series_id: seriesId, title: '新的一集', episode_number: 1,
    revision: 0, frames: [], characters: [], scenes: [], props: [], original_text: '',
    workflow_mode: 'r2v', default_generation_mode: 'i2v', created_at: 2, updated_at: 2,
});
function episodeRequest(value: unknown) {
    if (!value || typeof value !== 'object'
        || !('client_request_id' in value) || typeof value.client_request_id !== 'string'
        || !('title' in value) || typeof value.title !== 'string') {
        throw new Error('Expected an episode creation request with its original identity');
    }
    return { client_request_id: value.client_request_id, title: value.title };
}
const savedReceipt = (request: unknown, seriesId = 'series-1') => {
    const input = episodeRequest(request);
    return {
        schema_version: 1, series_id: seriesId, client_request_id: input.client_request_id,
        request_fingerprint: 'a'.repeat(64), episode_id: 'episode-created',
        episode_number: 1, title: input.title, created_at: 2,
    };
};
const notFound = () => ({ response: { status: 404 } });

let receipt: ReturnType<typeof savedReceipt> | null;
let episodes: ReturnType<typeof savedEpisode>[];

async function fillNewEpisode(title = '新的一集') {
    fireEvent.click(await screen.findByRole('button', { name: '添加集数' }));
    fireEvent.change(await screen.findByPlaceholderText('集数标题...'), { target: { value: title } });
}

describe('Series episode creation through the actual client', () => {
    beforeEach(() => {
        vi.resetAllMocks();
        localStorage.clear();
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: ENV_A });
        receipt = null;
        episodes = [];
        vi.mocked(axios.get).mockImplementation(async (url) => {
            const path = String(url).slice(base.length);
            const seriesId = path.split('/')[2];
            if (path.includes('/episode_requests/')) {
                if (!receipt) throw notFound();
                return { data: receipt };
            }
            if (path.endsWith('/episodes')) return { data: episodes.filter(ep => ep.series_id === seriesId) };
            if (path.startsWith('/series/')) return { data: { ...savedSeries(seriesId), episode_ids: episodes.filter(ep => ep.series_id === seriesId).map(ep => ep.id) } };
            throw new Error(`Unexpected GET ${path}`);
        });
    });

    afterEach(() => {
        cleanup();
        vi.restoreAllMocks();
        clearRuntimeEnvironment();
    });

    it('saves one frozen identity before a single create-and-associate POST', async () => {
        vi.mocked(axios.post).mockImplementation(async (url, body) => {
            expect(url).toBe(`${base}/series/series-1/episode_requests`);
            expect(body).toEqual({ client_request_id: expect.any(String), title: '新的一集', expected_revision: 4 });
            const journal = Array.from({ length: localStorage.length }, (_, index) => localStorage.getItem(localStorage.key(index)!)).join(' ');
            expect(journal).toContain(episodeRequest(body).client_request_id);
            receipt = savedReceipt(body);
            episodes = [savedEpisode()];
            return { data: receipt };
        });
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        await waitFor(() => expect(screen.queryByPlaceholderText('集数标题...')).not.toBeInTheDocument());
        expect(await screen.findByText('新的一集')).toBeInTheDocument();
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('keeps the title and shows save uncertainty without minting another request', async () => {
        vi.mocked(axios.post).mockRejectedValue({ response: { status: 503 } });
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByRole('alert')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('集数标题...')).toHaveValue('新的一集');
        expect(axios.post).toHaveBeenCalledTimes(1);
        const request = vi.mocked(axios.post).mock.calls[0][1];
        expect(episodeRequest(request).client_request_id).toEqual(expect.any(String));
    });

    it('recovers a lost ACK through a read without a second create', async () => {
        vi.mocked(axios.post).mockImplementation(async (_url, body) => {
            receipt = savedReceipt(body);
            episodes = [savedEpisode()];
            throw new Error('Lost response');
        });
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByText('新的一集')).toBeInTheDocument();
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.get).toHaveBeenCalledWith(expect.stringContaining('/episode_requests/'));
    });

    it('fails visibly before POST when the durable journal cannot be written', async () => {
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        const failedWrite = vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => { throw new Error('Quota exceeded'); });
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByRole('alert')).toBeInTheDocument();
        expect(failedWrite).toHaveBeenCalled();
        expect(axios.post).not.toHaveBeenCalled();
        expect(screen.getByPlaceholderText('集数标题...')).toHaveValue('新的一集');
    });

    it('blocks double submission while the save response is pending', async () => {
        const pending = deferred<{ data: ReturnType<typeof savedReceipt> }>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        const input = screen.getByPlaceholderText('集数标题...');
        fireEvent.keyDown(input, { key: 'Enter' });
        fireEvent.keyDown(input, { key: 'Enter' });
        expect(axios.post).toHaveBeenCalledTimes(1);
        const request = vi.mocked(axios.post).mock.calls[0][1];
        receipt = savedReceipt(request);
        episodes = [savedEpisode()];
        await act(async () => pending.resolve({ data: receipt! }));
        expect(await screen.findByText('新的一集')).toBeInTheDocument();
    });

    it('does not let an old series ACK close the current series draft', async () => {
        const pending = deferred<{ data: ReturnType<typeof savedReceipt> }>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        const rendered = renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        const request = vi.mocked(axios.post).mock.calls[0][1];
        rendered.rerender(<SeriesDetailPage seriesId="series-2" />);
        await screen.findByRole('heading', { name: '系列二' });
        await fillNewEpisode('系列二草稿');
        await act(async () => pending.resolve({ data: savedReceipt(request) }));
        expect(screen.getByPlaceholderText('集数标题...')).toHaveValue('系列二草稿');
        expect(screen.queryByText('新的一集')).not.toBeInTheDocument();
    });

    it('isolates an A to B to A environment return from the first A request', async () => {
        const pending = deferred<{ data: ReturnType<typeof savedReceipt> }>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        const request = vi.mocked(axios.post).mock.calls[0][1];
        act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: ENV_B }); });
        await screen.findByRole('button', { name: '添加集数' });
        act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: ENV_A }); });
        await waitFor(() => expect(axios.get).toHaveBeenCalledWith(expect.stringContaining('/episode_requests/')));
        const readsBefore = vi.mocked(axios.get).mock.calls.length;
        await act(async () => pending.resolve({ data: savedReceipt(request) }));
        expect(vi.mocked(axios.get).mock.calls.length).toBe(readsBefore);
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(screen.queryByText('新的一集')).not.toBeInTheDocument();
    });

    it('restores an unresolved identity after unmount and reads the saved result', async () => {
        const originalGet = vi.mocked(axios.get).getMockImplementation()!;
        let canReadReceipt = false;
        vi.mocked(axios.get).mockImplementation(async (...args) => {
            if (String(args[0]).includes('/episode_requests/') && !canReadReceipt) throw new Error('Read unavailable');
            return originalGet(...args);
        });
        vi.mocked(axios.post).mockImplementation(async (_url, body) => {
            receipt = savedReceipt(body);
            throw new Error('Lost response');
        });
        const first = renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('创建结果尚未确认');
        expect(screen.getByPlaceholderText('集数标题...')).toBeDisabled();
        first.unmount();
        canReadReceipt = true;
        episodes = [savedEpisode()];
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        expect(await screen.findByText('新的一集')).toBeInTheDocument();
        await waitFor(() => expect(screen.queryByPlaceholderText('集数标题...')).not.toBeInTheDocument());
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('retries a confirmed result refresh without creating another episode', async () => {
        const originalGet = vi.mocked(axios.get).getMockImplementation()!;
        let failRefresh = true;
        vi.mocked(axios.get).mockImplementation(async (...args) => {
            if (receipt && String(args[0]).endsWith('/episodes') && failRefresh) throw new Error('List unavailable');
            return originalGet(...args);
        });
        vi.mocked(axios.post).mockImplementation(async (_url, body) => {
            receipt = savedReceipt(body);
            episodes = [savedEpisode()];
            return { data: receipt };
        });
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('分集已创建');
        failRefresh = false;
        fireEvent.click(screen.getByRole('button', { name: '刷新结果' }));
        expect(await screen.findByText('新的一集')).toBeInTheDocument();
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('retries the exact frozen request after a missing receipt instead of changing identity', async () => {
        vi.mocked(axios.post).mockRejectedValueOnce({ response: { status: 503 } }).mockImplementationOnce(async (_url, body) => {
            receipt = savedReceipt(body);
            episodes = [savedEpisode()];
            return { data: receipt };
        });
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        await screen.findByRole('alert');
        const originalRequest = vi.mocked(axios.post).mock.calls[0][1];
        fireEvent.click(screen.getByRole('button', { name: '重试创建' }));
        expect(await screen.findByText('新的一集')).toBeInTheDocument();
        expect(axios.post).toHaveBeenCalledTimes(2);
        expect(vi.mocked(axios.post).mock.calls[1][1]).toEqual(originalRequest);
    });

    it('requires fresh confirmation after a rejected stale revision and a missing receipt', async () => {
        let revision = 4;
        const originalGet = vi.mocked(axios.get).getMockImplementation()!;
        vi.mocked(axios.get).mockImplementation(async (...args) => {
            if (args[0] === `${base}/series/series-1`) return { data: { ...savedSeries(), revision } };
            return originalGet(...args);
        });
        vi.mocked(axios.post).mockImplementationOnce(async () => {
            revision = 5;
            throw { response: { status: 409 } };
        }).mockImplementationOnce(async (_url, body) => {
            receipt = savedReceipt(body);
            episodes = [savedEpisode()];
            return { data: receipt };
        });
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('系列内容已变化');
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(screen.getByPlaceholderText('集数标题...')).toHaveValue('新的一集');
        expect(screen.getByPlaceholderText('集数标题...')).toBeEnabled();
        const originalRequest = episodeRequest(vi.mocked(axios.post).mock.calls[0][1]);
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByText('新的一集')).toBeInTheDocument();
        const retried = vi.mocked(axios.post).mock.calls[1][1];
        expect(retried).toEqual({ client_request_id: expect.any(String), title: '新的一集', expected_revision: 5 });
        expect(episodeRequest(retried).client_request_id).not.toBe(originalRequest.client_request_id);
    });

    it('rejects a receipt from another series without clearing the original identity', async () => {
        vi.mocked(axios.post).mockImplementation(async (_url, body) => {
            receipt = savedReceipt(body, 'another-series');
            return { data: receipt };
        });
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('创建结果尚未确认');
        expect(screen.getByPlaceholderText('集数标题...')).toHaveValue('新的一集');
        fireEvent.click(screen.getByRole('button', { name: '检查结果' }));
        await waitFor(() => expect(screen.getByRole('button', { name: '检查结果' })).toBeEnabled());
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('cannot submit when browser storage silently drops the journal write', async () => {
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {});
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('无法在浏览器中保存创建记录');
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('shows an actionable error when the series response has no saved revision', async () => {
        const originalGet = vi.mocked(axios.get).getMockImplementation()!;
        vi.mocked(axios.get).mockImplementation(async (...args) => {
            if (args[0] === `${base}/series/series-1`) return { data: { ...savedSeries(), revision: undefined } };
            return originalGet(...args);
        });
        renderWithIntl(<SeriesDetailPage seriesId="series-1" />);
        await fillNewEpisode();
        fireEvent.click(screen.getByRole('button', { name: '确定' }));
        expect(await screen.findByRole('alert')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('集数标题...')).toHaveValue('新的一集');
        expect(axios.post).not.toHaveBeenCalled();
    });
});
