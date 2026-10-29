import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import Cast from '../Cast';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import ToastContainer from '@/components/shared/ToastContainer';
import { useProjectStore, type Project } from '@/store/projectStore';
import { useToastStore } from '@/store/toastStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, currentReadEnvironment, deferred, project } from './frame-command-fixtures';

vi.mock('../cast/CastWorkbenchModal', () => ({ default: () => null, activePolls: new Map() }));
vi.mock('axios', () => ({ default: { get: vi.fn() } }));

const voices = ['old', 'new', 'other'].map(id => ({ id, name: `${id} voice`, gender: 'Neutral',
    model: 'cosyvoice-v3-flash', family: 'cosyvoice', origin: 'system', supports_instruction: false }));
const character = { id: 'shared', name: 'Alice', source: 'series' as const, voice_id: 'old', voice_name: 'old voice' };
function voiceProject(id = 'a', revision = 3): Project {
    return { ...project(id, revision), series_id: 'series-1', characters: [character], readEnvironment: currentReadEnvironment(),
        read_versions: { project: revision, series: { id: 'series-1', revision: 0 }, library: 0 } };
}
function voiceAck(id = 'a', revision = 4) {
    return { ...ack([], id, revision), series_id: 'series-1', read_versions: { project: revision, series: { id: 'series-1', revision: 1 }, library: 0 },
        characters: [{ ...character, voice_id: 'new', voice_name: 'new voice' }],
        scenes: [{ id: 'global-scene', name: 'Global scene', source: 'global' }] };
}
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
let write: ReturnType<typeof deferred<Response>>;
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;
function writes() { return fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/characters/shared/voice')); }
function renderPage() { return renderWithIntl(<LightboxProvider><Cast /><ToastContainer /></LightboxProvider>); }
async function openPicker() {
    fireEvent.click(screen.getByTitle('更换音色'));
    await screen.findByText('new voice');
}
async function selectNew() { await openPicker(); fireEvent.click(screen.getByText('new voice')); }
function apply() { fireEvent.click(screen.getByRole('button', { name: '应用' })); }

beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); useToastStore.getState().clear();
    useProjectStore.setState({ projects: [voiceProject(), voiceProject('b')], currentProject: voiceProject(),
        runningOps: {}, readWatermarks: {}, generatingTasks: [], currentSeries: null });
    write = deferred<Response>();
    vi.mocked(axios.get).mockRejectedValue(new Error('Read unavailable'));
    fetchMock = vi.fn<typeof fetch>(async url => {
        const path = String(url);
        if (path.endsWith('/voices')) return response(voices);
        if (path.endsWith('/custom_voices')) return response([]);
        if (/\/characters\/[^/]+\/voice$/.test(path)) return write.promise;
        throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe('Cast voice binding through the actual picker, API and project store', () => {

    it('does not delete another live asset marker when Cast mounts', () => {
        const marker = { assetId: 'shared', generationType: 'full_body', batchSize: 1 };
        useProjectStore.setState({ generatingTasks: [marker] }); renderPage();
        expect(useProjectStore.getState().generatingTasks).toEqual([marker]);
    });

    it('keeps the existing image usable while marking its interrupted generation separately', () => {
        const current = voiceProject();
        current.characters = [{ ...character, status: 'needs_confirmation', image_url: '/chosen.png' }];
        useProjectStore.setState({ currentProject: current, projects: [current], generatingTasks: [{ assetId: 'shared', generationType: 'all', batchSize: 1 }] });
        renderPage();
        expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
        expect(screen.queryByText('生成中...')).toBeNull();
        expect(useProjectStore.getState().currentProject?.characters[0].image_url).toBe('/chosen.png');
        expect(writes()).toHaveLength(0);
    });

    it('keeps the picker open when a shared-source ACK and its one readback are incomparable', async () => {
        const latest = voiceAck('a', 3);
        latest.characters = [character];
        latest.read_versions.series.revision = 5;
        latest.read_versions.library = 8;
        useProjectStore.getState().applyProjectReadSnapshot('a', latest);
        const reply = voiceAck('a', 4);
        reply.read_versions.series.revision = 4; reply.read_versions.library = 9;
        vi.mocked(axios.get).mockResolvedValue({ data: reply });
        renderPage(); await selectNew(); apply();
        await act(async () => write.resolve(response(reply)));
        expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent(/已保存.*读取失败/);
        expect(useProjectStore.getState().currentProject?.characters?.[0].voice_id).toBe('old');
        expect(axios.get).toHaveBeenCalledOnce(); expect(writes()).toHaveLength(1);
    });

    it('awaits one write, then applies the complete enriched ACK before closing', async () => {
        renderPage(); await selectNew();
        const button = screen.getByRole('button', { name: '应用' });
        act(() => { fireEvent.click(button); fireEvent.click(button); });
        expect(writes()).toHaveLength(1);
        expect(screen.getByRole('heading', { name: /选择音色/ })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled();
        await act(async () => write.resolve(response(voiceAck())));
        await waitFor(() => expect(screen.queryByRole('heading', { name: /选择音色/ })).not.toBeInTheDocument());
        const state = useProjectStore.getState();
        for (const saved of [state.currentProject, state.projects.find(p => p.id === 'a')]) {
            expect(saved).toMatchObject({ revision: 4, originalText: 'server text', updatedAt: new Date(2000_000).toISOString(),
                characters: [{ id: 'shared', source: 'series', voice_id: 'new' }], scenes: [{ source: 'global' }] });
        }
        expect(writes()[0][1]).toMatchObject({ method: 'POST', body: JSON.stringify({ voice_id: 'new', voice_name: 'new voice' }) });
    });

    it.each([503, 409, 0])('keeps the selected voice and shows a %s failure without auto retry', async status => {
        renderPage(); await selectNew(); apply();
        await act(async () => status ? write.resolve(response({ detail: 'storage rejected' }, status)) : write.reject(new Error('offline')));
        const dialog = screen.getByRole('dialog', { name: /选择音色/ });
        expect(within(dialog).getByRole('alert')).toHaveTextContent(status === 503 ? /存储不可用/ : status === 409 ? /已变化/ : /未能确认/);
        expect(within(dialog).getByRole('button', { name: '应用' })).toBeEnabled();
        expect(within(dialog).getByRole('radio', { name: 'new voice' })).toHaveAttribute('aria-checked', 'true');
        expect(writes()).toHaveLength(1);
        expect(useProjectStore.getState().currentProject?.characters?.[0].voice_id).toBe('old');
    });

    it('does not replace an open selection when an external ACK changes the current binding', async () => {
        renderPage(); await selectNew();
        act(() => useProjectStore.getState().applyProjectReadSnapshot('a', {
            ...voiceAck(), characters: [{ ...character, voice_id: 'other', voice_name: 'other voice' }],
        }));
        apply();
        expect(JSON.parse(String(writes()[0][1]?.body)).voice_id).toBe('new');
        await act(async () => write.resolve(response(voiceAck('a', 5))));
    });

    it('discards a cancelled selection when the same project opens a fresh picker', async () => {
        renderPage(); await selectNew(); fireEvent.click(screen.getByRole('button', { name: '取消' }));
        await openPicker();
        expect(screen.getByRole('button', { name: '应用' })).toBeDisabled();
        expect(writes()).toHaveLength(0);
    });

    it('does not treat the nested preview button keyboard event as selecting a voice', async () => {
        renderPage(); await openPicker();
        const card = screen.getByRole('radio', { name: 'new voice' });
        fireEvent.keyDown(within(card).getByRole('button', { name: 'Play preview' }), { key: 'Enter' });
        expect(card).toHaveAttribute('aria-checked', 'false');
        fireEvent.keyDown(card, { key: 'Enter' });
        expect(card).toHaveAttribute('aria-checked', 'true');
        expect(writes()).toHaveLength(0);
    });

    it('keeps a newer selection open after the previously submitted voice is confirmed', async () => {
        renderPage(); await selectNew(); apply();
        fireEvent.click(screen.getByText('other voice'));
        await act(async () => write.resolve(response(voiceAck())));
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'other voice' })).toHaveAttribute('aria-checked', 'true');
        expect(screen.getByRole('button', { name: '应用' })).toBeEnabled();
        expect(useProjectStore.getState().currentProject?.characters?.[0].voice_id).toBe('new');
        expect(writes()).toHaveLength(1);
        write = deferred<Response>(); apply();
        expect(JSON.parse(String(writes()[1][1]?.body)).voice_id).toBe('other');
        await act(async () => write.resolve(response({ ...voiceAck('a', 5),
            characters: [{ ...character, voice_id: 'other', voice_name: 'other voice' }],
        })));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('allows an explicit close but never lets the old ACK close a newly opened picker', async () => {
        renderPage(); await selectNew(); apply();
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        await openPicker(); fireEvent.click(screen.getByText('other voice'));
        expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled();
        await act(async () => write.resolve(response(voiceAck())));
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'other voice' })).toHaveAttribute('aria-checked', 'true');
        expect(writes()).toHaveLength(1);
    });

    it('isolates a selection from a different project that references the same shared character', async () => {
        renderPage(); await selectNew();
        act(() => useProjectStore.setState({ currentProject: voiceProject('b') }));
        expect(screen.queryByRole('heading', { name: /选择音色/ })).not.toBeInTheDocument();
        await openPicker();
        expect(screen.getByRole('button', { name: '应用' })).toBeDisabled();
        expect(writes()).toHaveLength(0);
    });

    it('does not return to project A when its binding ACK arrives while B is active', async () => {
        renderPage(); await selectNew(); apply();
        act(() => useProjectStore.setState({ currentProject: voiceProject('b') }));
        await openPicker(); fireEvent.click(screen.getByText('other voice'));
        await act(async () => write.resolve(response(voiceAck())));
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(useProjectStore.getState().currentProject?.characters?.[0].voice_id).toBe('old');
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'other voice' })).toHaveAttribute('aria-checked', 'true');
        expect(useProjectStore.getState().projects.find(p => p.id === 'a')?.characters?.[0].voice_id).toBe('new');
    });

    it('blocks a second write across A -> B -> A and does not close the fresh picker on the old ACK', async () => {
        renderPage(); await selectNew(); apply();
        act(() => useProjectStore.setState({ currentProject: voiceProject('b') }));
        act(() => useProjectStore.setState({ currentProject: voiceProject() }));
        await openPicker(); fireEvent.click(screen.getByText('other voice'));
        expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled();
        expect(writes()).toHaveLength(1);
        await act(async () => write.resolve(response(voiceAck())));
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'other voice' })).toHaveAttribute('aria-checked', 'true');
        expect(screen.getByRole('button', { name: '应用' })).toBeEnabled();
        expect(writes()).toHaveLength(1);
    });

    it('keeps the pending gate across unmount and makes an old request failure visible after remount', async () => {
        const view = renderPage(); await selectNew(); apply(); view.unmount();
        renderPage(); await selectNew();
        expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled();
        expect(writes()).toHaveLength(1);
        await act(async () => write.resolve(response({ detail: 'unavailable' }, 503)));
        expect(await screen.findByText(/存储不可用/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '应用' })).toBeEnabled();
        expect(useToastStore.getState().toasts.at(-1)).toMatchObject({ kind: 'error', projectId: 'a' });
        expect(writes()).toHaveLength(1);
    });

    it('serializes two shared characters whose binding ACKs keep the same project revision', async () => {
        const bob = { ...character, id: 'bob', name: 'Bob' };
        const initial = { ...voiceProject(), characters: [character, bob] };
        useProjectStore.setState({ currentProject: initial, projects: [initial] });
        renderPage();
        fireEvent.click(screen.getAllByTitle('更换音色')[0]);
        fireEvent.click(await screen.findByText('new voice')); apply();
        fireEvent.click(screen.getByRole('button', { name: '关闭' }));
        fireEvent.click(screen.getAllByTitle('更换音色')[1]);
        fireEvent.click(await screen.findByText('other voice'));
        expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled();
        expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/voice'))).toHaveLength(1);
        const first = { ...voiceAck('a', 3), characters: [{ ...character, voice_id: 'new', voice_name: 'new voice' }, bob] };
        await act(async () => write.resolve(response(first)));
        write = deferred<Response>(); apply();
        expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/voice'))).toHaveLength(2);
        const second = { ...first, characters: [first.characters[0], { ...bob, voice_id: 'other', voice_name: 'other voice' }] };
        second.read_versions = { ...first.read_versions, series: { id: 'series-1', revision: 2 } };
        await act(async () => write.resolve(response(second)));
        const state = useProjectStore.getState();
        for (const saved of [state.currentProject, state.projects[0]]) {
            expect(saved?.revision).toBe(3);
            expect(saved?.characters?.map(c => c.voice_id)).toEqual(['new', 'other']);
        }
        expect(axios.get).not.toHaveBeenCalled();
    });

    it.each([[4, 3], [3, 4]])('reads once when a shared ACK is older than current %s or cached %s', async (currentRevision, cachedRevision) => {
        useProjectStore.setState({ currentProject: voiceProject('a', currentRevision), projects: [voiceProject('a', cachedRevision)] });
        const readback = deferred<{ data: ReturnType<typeof voiceAck> }>();
        vi.mocked(axios.get).mockReturnValue(readback.promise);
        renderPage(); await selectNew(); apply();
        await act(async () => write.resolve(response(voiceAck('a', 3))));
        expect(axios.get).toHaveBeenCalledOnce();
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled();
        await act(async () => readback.resolve({ data: voiceAck('a', 4) }));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        for (const saved of [useProjectStore.getState().currentProject, useProjectStore.getState().projects[0]]) {
            expect(saved).toMatchObject({ revision: 4, originalText: 'server text', characters: [{ voice_id: 'new', source: 'series' }] });
        }
        expect(writes()).toHaveLength(1);
    });

    it.each(['failed', 'stale', 'malformed'])('separates a saved binding from %s readback and never resubmits it automatically', async readOutcome => {
        useProjectStore.setState({ currentProject: voiceProject('a', 4), projects: [voiceProject('a', 4)] });
        if (readOutcome === 'stale') vi.mocked(axios.get).mockResolvedValue({ data: voiceAck('a', 3) });
        if (readOutcome === 'malformed') vi.mocked(axios.get).mockResolvedValue({ data: { id: 'a' } });
        renderPage(); await selectNew(); apply();
        await act(async () => write.resolve(response(voiceAck('a', 3))));
        expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent(/已保存.*读取失败/);
        expect(screen.getByRole('radio', { name: 'new voice' })).toHaveAttribute('aria-checked', 'true');
        expect(useProjectStore.getState().currentProject?.revision).toBe(4);
        expect(useProjectStore.getState().currentProject?.characters?.[0].voice_id).toBe('old');
        expect(axios.get).toHaveBeenCalledOnce(); expect(writes()).toHaveLength(1);
    });

    it('does not accept a readback that became stale while awaiting the GET', async () => {
        useProjectStore.setState({ currentProject: voiceProject('a', 4), projects: [voiceProject('a', 4)] });
        const readback = deferred<{ data: ReturnType<typeof voiceAck> }>();
        vi.mocked(axios.get).mockReturnValue(readback.promise);
        renderPage(); await selectNew(); apply();
        await act(async () => write.resolve(response(voiceAck('a', 3))));
        act(() => useProjectStore.getState().applyProjectReadSnapshot('a', { ...voiceAck('a', 5), original_text: 'newest text' }));
        const incomparable = voiceAck('a', 4);
        incomparable.read_versions.series.revision = 2;
        await act(async () => readback.resolve({ data: incomparable }));
        expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent(/已保存.*读取失败/);
        expect(useProjectStore.getState().currentProject).toMatchObject({ revision: 5, originalText: 'newest text' });
        expect(axios.get).toHaveBeenCalledOnce(); expect(writes()).toHaveLength(1);
    });

    it('keeps a late fallback GET on its original project without closing the new project picker', async () => {
        useProjectStore.setState({ currentProject: voiceProject('a', 4), projects: [voiceProject('a', 4), voiceProject('b')] });
        const readback = deferred<{ data: ReturnType<typeof voiceAck> }>();
        vi.mocked(axios.get).mockReturnValue(readback.promise);
        renderPage(); await selectNew(); apply();
        await act(async () => write.resolve(response(voiceAck('a', 3))));
        act(() => useProjectStore.setState({ currentProject: voiceProject('b') }));
        await openPicker(); fireEvent.click(screen.getByText('other voice'));
        await act(async () => readback.resolve({ data: voiceAck('a', 4) }));
        expect(useProjectStore.getState().currentProject).toMatchObject({ id: 'b', revision: 3, characters: [{ voice_id: 'old' }] });
        expect(useProjectStore.getState().projects.find(p => p.id === 'a')).toMatchObject({ revision: 4, characters: [{ voice_id: 'new' }] });
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'other voice' })).toHaveAttribute('aria-checked', 'true');
        expect(axios.get).toHaveBeenCalledOnce(); expect(writes()).toHaveLength(1);
    });

    it.each([[6, 3], [3, 6], [6, 7]])('guards current revision %s and cached revision %s independently', async (currentRevision, cachedRevision) => {
        useProjectStore.setState({ currentProject: voiceProject('a', currentRevision), projects: [voiceProject('a', cachedRevision)] });
        renderPage(); await selectNew(); apply();
        await act(async () => write.resolve(response(voiceAck())));
        const state = useProjectStore.getState();
        expect(state.currentProject?.revision).toBe(Math.max(4, currentRevision));
        expect(state.projects[0].revision).toBe(Math.max(4, cachedRevision));
        expect(state.currentProject?.originalText).toBe(currentRevision > 4 ? 'original text' : 'server text');
        expect(state.projects[0].originalText).toBe(cachedRevision > 4 ? 'original text' : 'server text');
    });

    it.each([
        { id: 'a', characters: [] },
        { ...voiceAck(), id: 'another-project' },
        'invalid response',
    ])('does not claim a saved binding from an invalid initial ACK: %j', async invalidAck => {
        renderPage(); await selectNew(); apply();
        await act(async () => write.resolve(response(invalidAck)));
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent(/未能确认/);
        expect(screen.queryByText(/已保存/)).not.toBeInTheDocument();
        expect(screen.getByRole('radio', { name: 'new voice' })).toHaveAttribute('aria-checked', 'true');
        expect(useProjectStore.getState().currentProject?.characters).toEqual([character]);
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
        expect(writes()).toHaveLength(1);
        expect(axios.get).not.toHaveBeenCalled();
    });
});
