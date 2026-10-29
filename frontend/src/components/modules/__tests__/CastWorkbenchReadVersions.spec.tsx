import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import axios from 'axios';
import CastWorkbenchModal, { activePolls } from '../cast/CastWorkbenchModal';
import ConsistencyVault from '../ConsistencyVault';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import ToastContainer from '@/components/shared/ToastContainer';
import { useProjectStore } from '@/store/projectStore';
import { useToastStore } from '@/store/toastStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from './frame-command-fixtures';
const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/components/common/GroupedModelGrid', () => ({ default: () => null }));
const originalAppBasePath = process.env.NEXT_PUBLIC_APP_BASE_PATH;
function read(library: number, selected = 'v1', revision = 3) {
    return enriched({ ...ack([], 'a', revision), characters: [{ id: 'shared', name: 'Alice', source: 'global',
        reference_sheet: { selected_image_id: selected, image_variants: [{ id: 'v1', url: '/v1.png' }, { id: 'v2', url: '/v2.png' }] } }] }, library);
}
function page() { return renderWithIntl(<LightboxProvider><CastWorkbenchModal isOpen kind="character" entityId="shared" onClose={() => {}} /><ToastContainer /></LightboxProvider>); }
afterEach(() => {
    activePolls.forEach(clearInterval); activePolls.clear(); vi.useRealTimers();
    clearRuntimeEnvironment();
    if (originalAppBasePath === undefined) delete process.env.NEXT_PUBLIC_APP_BASE_PATH;
    else process.env.NEXT_PUBLIC_APP_BASE_PATH = originalAppBasePath;
});

beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
    vi.resetAllMocks(); localStorage.clear(); useToastStore.getState().clear();
    useProjectStore.setState({ projects: [project()], currentProject: project(), readWatermarks: {}, currentSeries: null, generatingTasks: [] });
    useProjectStore.getState().applyProjectReadSnapshot('a', read(8));
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : read(7, 'v2', 4) }));
});
describe('actual Cast workbench read-version acknowledgement', () => {

    it.each(['cast-to-vault', 'vault-to-cast'])('preserves a live pre-ACK lock between entry points: %s', async direction => {
        const snapshot = read(9); Object.assign(snapshot.characters[0], { status: 'needs_confirmation' });
        useProjectStore.getState().applyProjectReadSnapshot('a', snapshot);
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : snapshot }));
        const accepted = deferred<{ data: unknown }>(); vi.mocked(axios.post).mockReturnValue(accepted.promise);
        const first = direction === 'cast-to-vault' ? page() : renderWithIntl(<ConsistencyVault />);
        fireEvent.click(screen.getByRole('button', { name: '发起新生成' })); await act(async () => {});
        expect(axios.post).toHaveBeenCalledOnce(); first.unmount();
        if (direction === 'cast-to-vault') renderWithIntl(<ConsistencyVault />); else page();
        expect(screen.queryByRole('button', { name: '发起新生成' })).toBeNull();
        expect(axios.post).toHaveBeenCalledOnce();
        await act(async () => accepted.resolve({ data: { ...ack([], 'a', 3), _task_id: 'accepted' } }));
    });

    it.each(['needs_confirmation', 'completed', 'pending'])('allows an explicit request after reload but keeps live submissions busy: %s', async status => {
        const snapshot = read(9); Object.assign(snapshot.characters[0], { status });
        useProjectStore.getState().applyProjectReadSnapshot('a', snapshot);
        useProjectStore.getState().addGeneratingTask('shared', 'reference_sheet', 1);
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : snapshot }));
        const accepted = deferred<{ data: unknown }>(); vi.mocked(axios.post).mockReturnValue(accepted.promise);
        const view = page();
        expect(screen.getByRole('button', { name: status === 'needs_confirmation' ? '发起新生成' : /再生成/ })).toBeEnabled();
        fireEvent.click(screen.getByRole('button', { name: status === 'needs_confirmation' ? '发起新生成' : /再生成/ })); await act(async () => {});
        expect(axios.post).toHaveBeenCalledOnce();
        view.unmount(); page();
        expect(screen.getByRole('button', { name: /生成中/ })).toBeDisabled();
        await act(async () => accepted.resolve({ data: { ...ack([], 'a', 3), _task_id: 'accepted' } }));
        expect(axios.post).toHaveBeenCalledOnce();
    });

    it('rejects a pending recovery read after the same project changes series owner', async () => {
        const base = read(9);
        const owned = (owner: string, revision: number) => enriched({ ...base, revision, series_id: owner,
            characters: [{ ...base.characters[0], source: 'series', status: 'needs_confirmation' }] }, 9, 1);
        useProjectStore.getState().applyProjectReadSnapshot('a', owned('series-a', 4));
        const pending = deferred<{ data: ReturnType<typeof owned> }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : pending.promise);
        page();
        fireEvent.click(screen.getByRole('button', { name: '重新读取资产' }));
        act(() => { useProjectStore.getState().applyProjectReadSnapshot('a', owned('series-b', 5)); });
        expect(screen.getByRole('button', { name: '重新读取资产' })).toBeEnabled();
        await act(async () => { pending.resolve({ data: owned('series-a', 6) }); });
        expect(useProjectStore.getState().currentProject?.series_id).toBe('series-b');
        expect(axios.post).not.toHaveBeenCalled();
    });


    it('shows neutral confirmation while preserving the selected image and uses a read-only refresh', async () => {
        const snapshot = read(9); Object.assign(snapshot.characters[0], { status: 'needs_confirmation' });
        useProjectStore.getState().applyProjectReadSnapshot('a', snapshot);
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : snapshot }));
        page();
        expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
        expect(screen.getByText(/再次生成会发起新的请求/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '重新读取资产' }));
        await act(async () => {});
        expect(axios.post).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.characters[0].reference_sheet?.selected_image_id).toBe('v1');
    });
    it('reads the authoritative asset once after an old task returns 404 without a success claim', async () => {
        vi.useFakeTimers();
        const snapshot = read(9); Object.assign(snapshot.characters[0], { status: 'needs_confirmation' });
        let reads = 0;
        vi.mocked(axios.get).mockImplementation(async url => {
            if (String(url).endsWith('/presets')) return { data: [] };
            if (String(url).includes('/tasks/')) throw { response: { status: 404 } };
            return { data: ++reads === 1 ? read(8) : snapshot };
        });
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack([], 'a', 3), _task_id: 'lost-task' } });
        page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await act(async () => {});
        await act(async () => { await vi.advanceTimersByTimeAsync(7500); });
        expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
        expect(reads).toBe(2); expect(axios.post).toHaveBeenCalledOnce();
        expect(useToastStore.getState().toasts.some(t => t.kind === 'success')).toBe(false);
        expect(useProjectStore.getState().generatingTasks).toHaveLength(0);
    });

    it.each(['snapshot-with-old-image', 'read-failed'])('keeps an ordinary 404 unknown without completion or failure claims: %s', async scenario => {
        vi.useFakeTimers(); let reads = 0;
        vi.mocked(axios.get).mockImplementation(async url => {
            if (String(url).endsWith('/presets')) return { data: [] };
            if (String(url).includes('/tasks/')) throw { response: { status: 404 } };
            if (++reads > 1 && scenario === 'read-failed') throw new Error('offline');
            return { data: read(9, 'v1') };
        });
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack([], 'a', 3), _task_id: 'lost' } });
        page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await act(async () => {}); await act(async () => { await vi.advanceTimersByTimeAsync(7500); });
        expect(reads).toBe(2); expect(axios.post).toHaveBeenCalledOnce();
        expect(screen.queryByText('资产生成结果待确认')).toBeNull();
        expect(useToastStore.getState().toasts.some(t => t.kind === 'success')).toBe(false);
        expect(screen.getByText('资产状态读取失败，结果仍未知。')).toBeInTheDocument();
    });

    it('does not clear a new same-ID task or read assets after an old poll crosses A to B to A', async () => {
        vi.useFakeTimers(); const pending = deferred<{ data: { status: string } }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : String(url).includes('/tasks/') ? pending.promise : Promise.resolve({ data: read(8) }));
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack([], 'a', 3), _task_id: 'old' } });
        page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await act(async () => {}); await act(async () => { await vi.advanceTimersByTimeAsync(7500); });
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).includes('/tasks/'))).toHaveLength(1);
        const marker = { assetId: 'shared', generationType: 'reference_sheet', batchSize: 1 };
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' });
            activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
            useProjectStore.setState({ generatingTasks: [marker] });
        });
        await act(async () => { pending.resolve({ data: { status: 'completed' } }); });
        expect(useProjectStore.getState().generatingTasks).toEqual([marker]);
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'))).toHaveLength(1);
        expect(useToastStore.getState().toasts.some(t => t.kind === 'success')).toBe(false);
    });

    it('renders shipped character template thumbnails under the configured app base path', () => {
        process.env.NEXT_PUBLIC_APP_BASE_PATH = '/static';
        page();

        const srcs = Array.from(document.querySelectorAll('img')).map((img) => img.getAttribute('src'));
        expect(srcs).toEqual(expect.arrayContaining([
            '/static/assets/templates/simple-triview.png',
            '/static/assets/templates/detailed-reference.png',
            '/static/assets/templates/design-sheet.png',
            'http://localhost:3000/files/v1.png',
            'http://localhost:3000/files/v2.png',
        ]));
    });

    it('uses the raw command helper for synchronous generation and keeps saved/read-failed feedback', async () => {
        let reads = 0;
        vi.mocked(axios.get).mockImplementation(async url => {
            if (String(url).endsWith('/presets')) return { data: [] };
            if (++reads === 1) return { data: read(8) };
            throw new Error('readback offline');
        });
        vi.mocked(axios.post).mockResolvedValue({ data: ack([], 'a', 4) });
        page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        expect(await screen.findByText(/已保存.*读取失败/)).toBeInTheDocument();
        expect(axios.post).toHaveBeenCalledOnce(); expect(reads).toBe(2);
        expect(useProjectStore.getState().currentProject?.characters[0].source).toBe('global');
        expect(useProjectStore.getState().generatingTasks).toHaveLength(0);
    });
    it('does not claim selection success when its ACK and one GET are below a shared source watermark', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: read(7, 'v2', 4) }); page();
        fireEvent.click(screen.getByText('点击设为参考'));
        await waitFor(() => expect(axios.post).toHaveBeenCalledOnce());
        await act(async () => {});
        expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(8);
        expect(useProjectStore.getState().currentProject?.characters[0].reference_sheet?.selected_image_id).toBe('v1');
        expect(useToastStore.getState().toasts.some(t => t.kind === 'success')).toBe(false);
        expect(await screen.findByText(/已保存.*读取失败/)).toBeInTheDocument();
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'))).toHaveLength(1);
        expect(axios.post).toHaveBeenCalledOnce();
    });

    it('applies a valid newer shared ACK with unchanged project revision and no additional GET', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: read(9, 'v2') }); page();
        fireEvent.click(screen.getByText('点击设为参考'));
        await waitFor(() => expect(useProjectStore.getState().currentProject?.characters[0].reference_sheet?.selected_image_id).toBe('v2'));
        expect(useProjectStore.getState().currentProject).toMatchObject({ revision: 3, read_versions: { library: 9 } });
        expect(useToastStore.getState().toasts.some(t => t.kind === 'success')).toBe(true);
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'))).toHaveLength(0);
    });

    it('does not read back or claim saved state for a malformed initial ACK', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: { id: 'a', revision: 4 } }); page();
        fireEvent.click(screen.getByText('点击设为参考'));
        expect(await screen.findByText('切换失败')).toBeInTheDocument();
        expect(screen.queryByText(/已保存/)).not.toBeInTheDocument();
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'))).toHaveLength(0);
        expect(axios.post).toHaveBeenCalledOnce();
    });

    it('does not submit generation after its required fresh read is incomparable', async () => {
        page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        expect(await screen.findByText(/无法确认最新素材/)).toBeInTheDocument();
        expect(axios.post).not.toHaveBeenCalled();
        expect(useProjectStore.getState().generatingTasks).toHaveLength(0);
        expect(useProjectStore.getState().currentProject?.characters[0].reference_sheet?.selected_image_id).toBe('v1');
    });
});
