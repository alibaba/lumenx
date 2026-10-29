import type { ComponentProps } from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { activateProjectEnvironment, deactivateProjectEnvironment, useProjectStore } from '@/store/projectStore';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { dialogueAudioOperationKey } from '@/lib/dialogueAudioState';
import { useToastStore } from '@/store/toastStore';
import ToastContainer from '@/components/shared/ToastContainer';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardR2V from '../StoryboardR2V';
import type ShotCard from '../storyboard-r2v/ShotCard';
import { ack, deferred, enriched, frame, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
vi.mock('../storyboard-r2v/ShotCard', () => ({
    T2I_HISTORY_LIMIT: 10,
    default: (p: ComponentProps<typeof ShotCard>) => <section data-testid="shot" aria-label={p.shot.id}>
        <textarea aria-label="prompt" value={p.shot.prompt} disabled={p.structureDisabled} onChange={event => p.onUpdatePrompt(event.target.value)} />
        <button disabled={p.structureDisabled} onClick={() => p.onUpdateField('duration', 6)}>edit field</button>
    </section>,
}));
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/TaskQueuePanel', () => ({ default: () => null }));

function json(data: unknown, ok = true, status = ok ? 200 : 500) {
    return { ok, status, json: async () => data };
}

function audioProject(id = 'a', revision = 3) {
    const f1 = { ...frame('f1'), dialogue: 'line one', character_ids: ['c1'], audio_url: null, audio_error: null };
    const f2 = { ...frame('f2'), dialogue: 'line two', character_ids: ['c1'], audio_url: null, audio_error: null };
    return { ...project(id, revision), frames: [f1, f2], characters: [{ id: 'c1', name: 'Hero', source: 'episode' as const, voice_id: 'voice-ui' }] };
}

function audioAck(id = 'a', revision = 4, extra: Record<string, unknown> = {}) {
    const base = audioProject(id, revision);
    return { ...ack(base.frames, id, revision), characters: base.characters, _batch_stats: { generated: 2, skipped: 0, failed: 0, no_voice: 0 }, ...extra };
}

const state = {
    project_id: 'a', frame_id: 'f1', read_versions: { project: 3, series: null, library: 1 },
    audio_url: null, audio_error: null, historical_instructions: null, dialogue: 'line one', voice_id: 'voice-backend',
    options: { speed: null, pitch: null, volume: null, instructions: null }, resolved_request: null, sources: null, prepared: null,
    freshness: 'stale', reason: 'missing_audio',
};

function renderPage() { return renderWithIntl(<><StoryboardR2V /><ToastContainer /></>); }
function synthButton() { return screen.getByRole('button', { name: /合成对白语音/ }); }
function synthOrBusyButton() { return screen.getByRole('button', { name: /合成对白语音|合成中/ }); }
function shot(id = 'f1') {
    const node = screen.getAllByTestId('shot').find(item => item.getAttribute('aria-label') === id) ?? screen.getAllByTestId('shot')[0];
    return within(node).getByRole('textbox', { name: 'prompt' });
}

beforeEach(() => {
    vi.resetAllMocks(); vi.useRealTimers(); localStorage.clear(); useToastStore.getState().clear();
    const current = audioProject();
    useProjectStore.setState({ projects: [current], currentProject: current, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
    vi.mocked(axios.get).mockResolvedValue({ data: enriched(audioAck('a', 4), 1) });
    vi.mocked(axios.post).mockResolvedValue({ data: audioAck('a', 4) });
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
        const text = String(url);
        if (text.endsWith('/dialogue_audio/batch')) return json(audioAck('a', 4));
        if (text.endsWith('/frames/f1/audio/state')) return json({ ...state, read_versions: { ...state.read_versions, project: useProjectStore.getState().currentProject?.revision ?? 3 } });
        if (text.endsWith('/frames/f1/audio') && init?.method === 'POST') return json(audioAck('a', 4));
        throw new Error(`unexpected fetch ${text}`);
    }));
});

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('StoryboardR2V dialogue audio integration', () => {
    it('flushes and awaits pending prompt saves before the paid batch dialogue call', async () => {
        const save = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(save.promise);
        renderPage();
        fireEvent.change(shot(), { target: { value: 'queued prompt edit' } });
        fireEvent.click(synthButton());
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/update`, { frame_id: 'f1', expected_revision: 3, visual_description: 'queued prompt edit' }));
        expect(fetch).not.toHaveBeenCalled();
        await act(async () => save.resolve({ data: audioAck('a', 4) }));
        await waitFor(() => expect(fetch).toHaveBeenCalledWith(`${API_URL}/projects/a/dialogue_audio/batch`, expect.objectContaining({ method: 'POST', credentials: 'include' })));
    });

    it('blocks batch dialogue generation when a pending edit save fails', async () => {
        vi.mocked(axios.post).mockRejectedValueOnce({ response: { status: 503 } });
        renderPage();
        fireEvent.change(shot(), { target: { value: 'queued prompt edit' } });
        fireEvent.click(synthButton());
        expect(await screen.findByRole('alert')).toHaveTextContent(/保存失败|存储|失败/);
        expect(fetch).not.toHaveBeenCalled();
    });

    it('does not show success when the batch raw ACK cannot be confirmed by one GET', async () => {
        vi.mocked(axios.get).mockRejectedValueOnce(new Error('offline'));
        renderPage();
        fireEvent.click(synthButton());
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        expect(useToastStore.getState().toasts.some(toast => toast.kind === 'success')).toBe(false);
        expect(useToastStore.getState().toasts.at(-1)?.kind).toBe('error');
        expect(fetch).toHaveBeenCalledOnce();
    });

    it('keeps the same-project remount gated until its batch request settles', async () => {
        const request = deferred();
        vi.stubGlobal('fetch', vi.fn(async (url: string) => {
            if (String(url).endsWith('/dialogue_audio/batch')) return request.promise;
            throw new Error(`unexpected fetch ${url}`);
        }));
        const view = renderPage();
        const environment = getRuntimeEnvironment();
        expect(environment).not.toBeNull();
        const operation = dialogueAudioOperationKey('a');
        try {
            fireEvent.click(synthButton());
            await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
            expect(useProjectStore.getState().runningOps[operation]).toBe(true);
            view.unmount(); renderPage();
            expect(getRuntimeEnvironment()).toBe(environment);
            expect(useProjectStore.getState().runningOps[operation]).toBe(true);
            expect(synthOrBusyButton()).toBeDisabled();
            fireEvent.click(synthOrBusyButton());
            expect(fetch).toHaveBeenCalledOnce();

            await act(async () => request.resolve(json(audioAck('a', 4))));

            await waitFor(() => {
                expect(useProjectStore.getState().runningOps[operation]).toBeUndefined();
                expect(synthButton()).toBeEnabled();
            });
            expect(getRuntimeEnvironment()).toBe(environment);
            expect(fetch).toHaveBeenCalledOnce();
            expect(axios.get).not.toHaveBeenCalled();
            expect(useToastStore.getState().toasts).toHaveLength(0);
        } finally {
            await act(async () => request.resolve(json(audioAck('a', 4))));
        }
    });

    it('drops a late batch result after switching projects without new-project UI or toast', async () => {
        const request = deferred();
        vi.stubGlobal('fetch', vi.fn(async (url: string) => {
            if (String(url).endsWith('/dialogue_audio/batch')) return request.promise;
            throw new Error(`unexpected fetch ${url}`);
        }));
        renderPage();
        fireEvent.click(synthButton());
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        const next = audioProject('b');
        act(() => useProjectStore.setState({ projects: [next], currentProject: next }));
        await act(async () => request.resolve(json(audioAck('a', 4))));
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('prompt f1');
        expect(axios.get).not.toHaveBeenCalled();
        expect(useToastStore.getState().toasts).toHaveLength(0);
    });

    it('uses backend voice fallback and flushes queued page edits plus dialogue text before single-line generation', async () => {
        const saves = [deferred(), deferred()];
        vi.mocked(axios.post).mockReturnValueOnce(saves[0].promise).mockReturnValueOnce(saves[1].promise);
        renderPage();
        fireEvent.change(shot(), { target: { value: 'queued prompt edit' } });
        fireEvent.click(screen.getAllByRole('button', { name: /对白音频/ })[0]);
        await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
        fireEvent.change(screen.getByPlaceholderText(/输入该镜头/), { target: { value: 'queued dialogue edit' } });
        fireEvent.click(screen.getByRole('button', { name: '生成对白' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalled());
        expect(fetch).not.toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/audio`, expect.anything());
        await act(async () => saves[0].resolve({ data: audioAck('a', 4) }));
        await act(async () => saves[1].resolve({ data: audioAck('a', 5) }));
        await waitFor(() => expect(fetch).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/audio`, expect.objectContaining({ method: 'POST' })));
        const updateCalls = vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/frames/update'));
        expect(updateCalls.map(([, body]) => body)).toEqual(expect.arrayContaining([
            { frame_id: 'f1', expected_revision: 4, visual_description: 'queued prompt edit' },
            { frame_id: 'f1', expected_revision: 3, dialogue: 'queued dialogue edit' },
        ]));
    });
});


it('keeps a reconnected audio operation busy when the old session request finishes', async () => {
    const storageId = '11111111-1111-4111-8111-111111111111';
    const oldRequest = deferred();
    const newRequest = deferred();
    let audioPosts = 0;
    vi.mocked(axios.get).mockResolvedValue({ data: enriched(audioAck('a', 8), 1) });
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
        const text = String(url);
        if (text.endsWith('/frames/f1/audio/state')) {
            return json({ ...state, read_versions: { ...state.read_versions,
                project: useProjectStore.getState().currentProject?.revision ?? 3 } });
        }
        if (text.endsWith('/frames/f1/audio') && init?.method === 'POST') {
            audioPosts += 1;
            if (audioPosts === 1) return oldRequest.promise;
            if (audioPosts === 2) return newRequest.promise;
        }
        throw new Error(`unexpected fetch ${text}`);
    }));
    const firstEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    await activateProjectEnvironment(firstEnvironment.key, firstEnvironment.generation);
    const first = audioProject();
    useProjectStore.setState({ projects: [first], currentProject: first, readWatermarks: {}, runningOps: {} });
    renderPage();
    try {
        fireEvent.click(screen.getAllByRole('button', { name: /对白音频/ })[0]);
        await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', { name: '生成对白' }));
        await waitFor(() => expect(audioPosts).toBe(1));
        expect(synthOrBusyButton()).toBeDisabled();

        act(() => {
            clearRuntimeEnvironment();
            deactivateProjectEnvironment();
        });
        expect(screen.queryByPlaceholderText(/输入该镜头/)).not.toBeInTheDocument();
        expect(screen.queryAllByTestId('shot')).toHaveLength(0);
        await act(async () => {
            const nextEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
            await activateProjectEnvironment(nextEnvironment.key, nextEnvironment.generation);
            const next = audioProject('a', 7);
            useProjectStore.setState({ projects: [next], currentProject: next, readWatermarks: {}, runningOps: {} });
        });
        expect(getRuntimeEnvironment()?.key).toBe(firstEnvironment.key);
        expect(getRuntimeEnvironment()?.generation).not.toBe(firstEnvironment.generation);
        fireEvent.click(screen.getAllByRole('button', { name: /对白音频/ })[0]);
        await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', { name: '生成对白' }));
        await waitFor(() => expect(audioPosts).toBe(2));
        const newOperationKey = dialogueAudioOperationKey('a');
        expect(useProjectStore.getState().runningOps[newOperationKey]).toBe(true);
        expect(synthOrBusyButton()).toBeDisabled();

        await act(async () => oldRequest.resolve(json(audioAck('a', 4))));

        expect(axios.get).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.revision).toBe(7);
        expect(useProjectStore.getState().runningOps[newOperationKey]).toBe(true);
        expect(synthOrBusyButton()).toBeDisabled();
        expect(audioPosts).toBe(2);
    } finally {
        await act(async () => {
            oldRequest.resolve(json(audioAck('a', 4)));
            newRequest.resolve(json(audioAck('a', 8)));
        });
    }
});


it.each([
    { oldKind: 'single-line', newKind: 'batch' },
    { oldKind: 'batch', newKind: 'single-line' },
    { oldKind: 'dub', newKind: 'single-line' },
] as const)('keeps a reconnected $newKind request busy when the old $oldKind request finishes', async ({ oldKind, newKind }) => {
    const storageId = '11111111-1111-4111-8111-111111111111';
    const oldRequest = deferred();
    const newRequest = deferred();
    const requestKinds: Array<'single-line' | 'batch' | 'dub'> = [];
    vi.mocked(axios.get).mockResolvedValue({ data: enriched(audioAck('a', 8), 1) });
    if (oldKind === 'dub') {
        vi.mocked(axios.post).mockImplementationOnce(() => {
            requestKinds.push('dub');
            return oldRequest.promise;
        });
    }
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
        const text = String(url);
        if (text.endsWith('/frames/f1/audio/state')) {
            const current = useProjectStore.getState().currentProject;
            const audioUrl = current?.frames.find(value => value.id === 'f1')?.audio_url ?? null;
            return json({ ...state, audio_url: audioUrl,
                freshness: audioUrl ? 'current' : 'stale', reason: audioUrl ? 'parameters_match' : 'missing_audio',
                read_versions: { ...state.read_versions, project: current?.revision ?? 3 } });
        }
        if (init?.method === 'POST') {
            const kind = text.endsWith('/frames/f1/audio') ? 'single-line'
                : text.endsWith('/dialogue_audio/batch') ? 'batch' : null;
            if (kind) {
                requestKinds.push(kind);
                if (requestKinds.length === 1) return oldRequest.promise;
                if (requestKinds.length === 2) return newRequest.promise;
            }
        }
        throw new Error(`unexpected fetch ${text}`);
    }));
    const startOperation = async (kind: 'single-line' | 'batch' | 'dub') => {
        if (kind === 'batch') fireEvent.click(synthButton());
        else {
            fireEvent.click(screen.getAllByRole('button', { name: /对白音频/ })[0]);
            await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: kind === 'dub' ? '预听' : '生成对白' }));
        }
    };
    const expectPendingOperation = async (kind: 'single-line' | 'batch' | 'dub') => {
        if (kind === 'batch') {
            // The batch branch replaces its CTA after the summary exit animation.
            expect(await screen.findByText('生成对白 0/2…')).toBeInTheDocument();
            expect(screen.queryByRole('button', { name: /合成对白语音|合成中/ })).not.toBeInTheDocument();
            if (!screen.queryByPlaceholderText(/输入该镜头/)) {
                fireEvent.click(screen.getAllByRole('button', { name: /对白音频/ })[0]);
            }
            await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
            const singleLineButton = screen.getByRole('button', { name: '生成对白' });
            expect(singleLineButton).toBeDisabled();
            return singleLineButton;
        }
        const batchButton = synthOrBusyButton();
        expect(batchButton).toBeDisabled();
        return batchButton;
    };
    const firstEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    await activateProjectEnvironment(firstEnvironment.key, firstEnvironment.generation);
    const base = audioProject();
    const first = oldKind === 'dub' ? {
        ...base,
        frames: base.frames.map(value => value.id === 'f1'
            ? { ...value, audio_url: '/audio/f1.mp3', selected_video_id: 'task-f1' } : value),
        video_tasks: [{ id: 'task-f1', project_id: 'a', frame_id: 'f1', status: 'completed' as const,
            video_url: '/video/f1.mp4', image_url: '', prompt: '', created_at: 1 }],
    } : base;
    useProjectStore.setState({ projects: [first], currentProject: first, readWatermarks: {}, runningOps: {} });
    renderPage();
    try {
        await startOperation(oldKind);
        await waitFor(() => expect(requestKinds).toEqual([oldKind]));
        expect(useProjectStore.getState().runningOps[dialogueAudioOperationKey('a')]).toBe(true);
        await expectPendingOperation(oldKind);
        if (oldKind === 'dub') {
            expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/dub/preview`, {
                video_task_id: 'task-f1', offset_ms: 0,
            }, { timeout: 120000 });
        }

        act(() => {
            clearRuntimeEnvironment();
            deactivateProjectEnvironment();
        });
        expect(screen.queryByPlaceholderText(/输入该镜头/)).not.toBeInTheDocument();
        expect(screen.queryAllByTestId('shot')).toHaveLength(0);
        await act(async () => {
            const nextEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
            await activateProjectEnvironment(nextEnvironment.key, nextEnvironment.generation);
            const next = audioProject('a', 7);
            useProjectStore.setState({ projects: [next], currentProject: next, readWatermarks: {}, runningOps: {} });
        });
        expect(getRuntimeEnvironment()?.key).toBe(firstEnvironment.key);
        expect(getRuntimeEnvironment()?.generation).not.toBe(firstEnvironment.generation);
        await startOperation(newKind);
        await waitFor(() => expect(requestKinds).toEqual([oldKind, newKind]));
        const newOperationKey = dialogueAudioOperationKey('a');
        expect(useProjectStore.getState().runningOps[newOperationKey]).toBe(true);
        await expectPendingOperation(newKind);

        await act(async () => {
            if (oldKind === 'dub') oldRequest.reject(new Error('late old dub transport failure'));
            else oldRequest.resolve(json(audioAck('a', 4)));
        });

        expect(axios.get).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.revision).toBe(7);
        expect(useProjectStore.getState().runningOps[newOperationKey]).toBe(true);
        const blockedAction = await expectPendingOperation(newKind);
        expect(useToastStore.getState().toasts).toHaveLength(0);
        expect(screen.queryByText('late old dub transport failure')).not.toBeInTheDocument();
        fireEvent.click(blockedAction);
        expect(requestKinds).toEqual([oldKind, newKind]);
    } finally {
        await act(async () => {
            oldRequest.resolve(oldKind === 'dub' ? { data: audioAck('a', 4) } : json(audioAck('a', 4)));
            newRequest.resolve(json(audioAck('a', 8)));
        });
    }
});


type DubOwnershipAction = 'preview' | 'apply' | 'revert';

function dubOwnershipProject(action: DubOwnershipAction, revision = 3, completed = false) {
    const base = audioProject('a', revision);
    const source = { schema_version: 1, task_id: 'task-f1', video_url: '/video/f1.mp4', audio_url: '/audio/f1.mp3', offset_ms: 0 };
    const preview = completed ? action === 'preview' : action === 'apply';
    const applied = completed ? action === 'apply' : action === 'revert';
    return {
        ...base,
        frames: base.frames.map(value => value.id === 'f1' ? {
            ...value, audio_url: '/audio/f1.mp3', selected_video_id: 'task-f1',
            preview_video_url: preview ? '/dub/preview.mp4' : null, dub_preview_source: preview ? source : null,
            dubbed_video_url: applied ? '/dub/preview.mp4' : null, dub_applied_source: applied ? source : null,
            dubbed_video_task_id: applied ? 'task-f1' : null, dub_offset_ms: 0,
        } : value),
        video_tasks: [{ id: 'task-f1', project_id: 'a', frame_id: 'f1', status: 'completed' as const,
            video_url: '/video/f1.mp4', image_url: '', prompt: '', created_at: 1 }],
    };
}

function dubOwnershipAck(action: DubOwnershipAction, revision = 4) {
    const result = dubOwnershipProject(action, revision, true);
    return { ...ack(result.frames, 'a', revision), characters: result.characters, video_tasks: result.video_tasks };
}

function stubDubAudioState() {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
        if (String(url).endsWith('/frames/f1/audio/state')) {
            const current = useProjectStore.getState().currentProject;
            return json({ ...state, audio_url: '/audio/f1.mp3', freshness: 'current', reason: 'parameters_match',
                read_versions: { ...state.read_versions, project: current?.revision ?? 3 } });
        }
        throw new Error(`unexpected fetch ${url}`);
    }));
}

it.each([
    { action: 'preview', button: '预听' },
    { action: 'apply', button: '应用覆盖' },
    { action: 'revert', button: '撤销覆盖' },
] as const)('does not start a new-session readback when the old dub $action ACK arrives after reconnect', async ({ action, button }) => {
    const storageId = '11111111-1111-4111-8111-111111111111';
    const oldWrite = deferred();
    const oldAck = dubOwnershipAck(action);
    if (action === 'revert') vi.mocked(axios.delete).mockReturnValueOnce(oldWrite.promise);
    else vi.mocked(axios.post).mockReturnValueOnce(oldWrite.promise);
    const writeTransport = action === 'revert' ? axios.delete : axios.post;
    vi.mocked(axios.get).mockResolvedValue({ data: enriched({ ...dubOwnershipAck(action, 9), title: 'Old dub readback' }, 1) });
    stubDubAudioState();
    const firstEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    await activateProjectEnvironment(firstEnvironment.key, firstEnvironment.generation);
    const first = dubOwnershipProject(action);
    useProjectStore.setState({ projects: [first], currentProject: first, readWatermarks: {}, runningOps: {} });
    renderPage();
    try {
        fireEvent.click(screen.getAllByRole('button', { name: /对白音频/ })[0]);
        await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', { name: button }));
        await waitFor(() => expect(writeTransport).toHaveBeenCalledOnce());
        if (action === 'preview') {
            expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/dub/preview`, {
                video_task_id: 'task-f1', offset_ms: 0,
            }, { timeout: 120000 });
        } else if (action === 'apply') expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/dub/apply`);
        else expect(axios.delete).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/dub`);
        expect(synthOrBusyButton()).toBeDisabled();

        act(() => {
            clearRuntimeEnvironment();
            deactivateProjectEnvironment();
        });
        expect(screen.queryAllByTestId('shot')).toHaveLength(0);
        expect(screen.queryByPlaceholderText(/输入该镜头/)).not.toBeInTheDocument();
        await act(async () => {
            const nextEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
            await activateProjectEnvironment(nextEnvironment.key, nextEnvironment.generation);
            const next = audioProject('a', 7);
            next.frames[0].action_description = 'fresh session prompt';
            useProjectStore.setState({ projects: [next], currentProject: next, readWatermarks: {}, runningOps: {} });
        });
        expect(getRuntimeEnvironment()?.key).toBe(firstEnvironment.key);
        expect(getRuntimeEnvironment()?.generation).not.toBe(firstEnvironment.generation);
        const reconnectedProject = useProjectStore.getState().currentProject;
        expect(shot()).toHaveValue('fresh session prompt');

        await act(async () => oldWrite.resolve({ data: oldAck }));

        expect(axios.get).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject).toBe(reconnectedProject);
        expect(useProjectStore.getState().currentProject?.revision).toBe(7);
        expect(shot()).toHaveValue('fresh session prompt');
        expect(useToastStore.getState().toasts).toHaveLength(0);
        expect(writeTransport).toHaveBeenCalledOnce();
    } finally {
        await act(async () => oldWrite.resolve({ data: oldAck }));
    }
});

it('does not apply an old dub preview readback that returns after reconnect', async () => {
    const storageId = '11111111-1111-4111-8111-111111111111';
    const oldWrite = deferred();
    const oldRead = deferred();
    const oldAck = dubOwnershipAck('preview');
    // A high revision prevents the ordinary monotonic-version floor from
    // accidentally proving the environment-generation boundary by itself.
    const oldReadback = enriched({ ...dubOwnershipAck('preview', 9), title: 'Old preview readback' }, 1);
    vi.mocked(axios.post).mockReturnValueOnce(oldWrite.promise);
    vi.mocked(axios.get).mockReturnValueOnce(oldRead.promise);
    stubDubAudioState();
    const firstEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    await activateProjectEnvironment(firstEnvironment.key, firstEnvironment.generation);
    const first = dubOwnershipProject('preview');
    useProjectStore.setState({ projects: [first], currentProject: first, readWatermarks: {}, runningOps: {} });
    renderPage();
    try {
        fireEvent.click(screen.getAllByRole('button', { name: /对白音频/ })[0]);
        await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', { name: '预听' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledOnce());
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/dub/preview`, {
            video_task_id: 'task-f1', offset_ms: 0,
        }, { timeout: 120000 });
        await act(async () => oldWrite.resolve({ data: oldAck }));
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        expect(axios.get).toHaveBeenCalledWith(`${API_URL}/projects/a`);

        act(() => {
            clearRuntimeEnvironment();
            deactivateProjectEnvironment();
        });
        expect(screen.queryAllByTestId('shot')).toHaveLength(0);
        await act(async () => {
            const nextEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
            await activateProjectEnvironment(nextEnvironment.key, nextEnvironment.generation);
            const next = audioProject('a', 7);
            next.frames[0].action_description = 'fresh session prompt';
            useProjectStore.setState({ projects: [next], currentProject: next, readWatermarks: {}, runningOps: {} });
        });
        expect(getRuntimeEnvironment()?.key).toBe(firstEnvironment.key);
        expect(getRuntimeEnvironment()?.generation).not.toBe(firstEnvironment.generation);
        const reconnectedProject = useProjectStore.getState().currentProject;
        expect(shot()).toHaveValue('fresh session prompt');

        await act(async () => oldRead.resolve({ data: oldReadback }));

        expect(useProjectStore.getState().currentProject).toBe(reconnectedProject);
        expect(useProjectStore.getState().currentProject?.revision).toBe(7);
        expect(shot()).toHaveValue('fresh session prompt');
        expect(useToastStore.getState().toasts).toHaveLength(0);
        expect(axios.get).toHaveBeenCalledOnce();
        expect(axios.post).toHaveBeenCalledOnce();
    } finally {
        await act(async () => {
            oldWrite.resolve({ data: oldAck });
            oldRead.resolve({ data: oldReadback });
        });
    }
});
