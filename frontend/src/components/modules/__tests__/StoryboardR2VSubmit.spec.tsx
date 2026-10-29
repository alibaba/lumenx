import type { ComponentProps, ReactNode } from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { providerVideoRequestStorageKey } from '@/lib/providerVideoRequest';
import { ORDERED_MEDIA_CONTRACT_VERSION } from '@/lib/providerVideoTaskEntry';
import { useProjectStore, type Project } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { toast } from '@/store/toastStore';
import { VIDEO_I2V_MODELS } from '@/lib/modelCatalog';
import StoryboardR2V from '../StoryboardR2V';
import type ShotCard from '../storyboard-r2v/ShotCard';

import { ack, currentReadEnvironment, deferred, enriched, frame, project } from './frame-command-fixtures';

type MockShotCardProps = ComponentProps<typeof ShotCard> & { children?: ReactNode };

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
vi.mock('@/store/toastStore', () => ({ toast: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() } }));
vi.mock('@/lib/modelCatalog', async importOriginal => {
    const actual = await importOriginal<typeof import('@/lib/modelCatalog')>();
    const providerModels = [
        { id: 'wan2.6-i2v', name: 'Wan2.6 I2V', provider: 'wanx', family: 'wan2.6' },
        { id: 'wan2.6-i2v-flash', name: 'Wan2.6 I2V Flash', provider: 'wanx', family: 'wan2.6' },
        { id: 'wan3.0-video', name: 'Wan3.0 Video', provider: 'wanx', family: 'wan3.0', params: { watermark: true, promptExtend: true }, duration: { type: 'range', min: 1, max: 30, default: 5 } },
    ];
    const r2vProviderModels = [
        { id: 'wan3.0-video', name: 'Wan3.0 Video', provider: 'wanx', family: 'wan3.0', params: { watermark: true, promptExtend: true }, duration: { type: 'range', min: 1, max: 30, default: 5 } },
    ];
    return {
        ...actual,
        VIDEO_I2V_MODELS: [...providerModels, ...actual.VIDEO_I2V_MODELS.filter(model => !providerModels.some(provider => provider.id === model.id))],
        VIDEO_R2V_MODELS: [...r2vProviderModels, ...actual.VIDEO_R2V_MODELS.filter(model => !r2vProviderModels.some(provider => provider.id === model.id))],
    };
});
vi.mock('../storyboard-r2v/ShotCard', () => ({
    T2I_HISTORY_LIMIT: 10,
    default: (p: MockShotCardProps) => <section data-testid="shot" aria-label={p.shot.id}>
        <output data-testid="shot-data">{JSON.stringify(p.shot)}</output>
        <button aria-expanded={p.expanded} onClick={p.onToggleExpanded}>toggle shot</button>
        <button onClick={p.onGenerateVideo}>retry video</button>
        <button onClick={() => p.onSetGenerateCount?.(2)}>set count two</button>
        <button data-busy={p.generationBlockedBusy ? 'true' : 'false'} disabled={!!p.generationBlockedMessage} onClick={() => p.onGenerateBatch?.(2)}>{p.generationBlockedMessage ?? 'generate two'}</button>
        {p.onCancelVideo ? <button onClick={p.onCancelVideo}>cancel video</button> : null}
        {p.children}
    </section>,
}));
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/T2ISubsection', () => ({
    default: (p: {
        lastFrameUrl?: string;
        onLastFrameUpload?: (file: File) => Promise<unknown>;
        onLastFrameClear?: () => void;
    }) => <div>
        {p.onLastFrameUpload ? (
            <button onClick={() => void p.onLastFrameUpload?.(new File(['x'], 'last.png', { type: 'image/png' }))}>
                upload last frame
            </button>
        ) : null}
        {p.lastFrameUrl && p.onLastFrameClear ? <button onClick={p.onLastFrameClear}>clear last frame</button> : null}
    </div>,
}));
vi.mock('../storyboard-r2v/DialogueAudioRow', () => ({ default: () => null }));


function i2vFrame(id = 'f1') {
    return {
        ...frame(id),
        action_description: 'camera move',
        visual_description: 'camera move',
        assembled_prompt: 'camera move',
        image_url: 'uploads/source.png',
        t2i_image_urls: ['uploads/source.png'],
        t2i_selected_index: 0,
        workbench_tab_mode: 't2i_i2v',
    };
}

function providerI2vTask(id: string, frameId = 'f1', projectId = 'a') {
    return {
        ...videoTask(id, frameId, projectId),
        prompt: 'camera move',
        image_url: 'uploads/source.png',
        workbench_tab: 't2i_i2v',
        generation_mode: 'i2v',
    };
}

const PROVIDER_FINGERPRINT = 'a'.repeat(64);

function providerExecution(overrides: Record<string, unknown> = {}) {
    return {
        schema_version: 1,
        attempt_id: 'attempt-original',
        phase: 'accepted',
        provider_model_id: 'wan2.7-i2v',
        intent: {},
        media: [],
        scope: {},
        credential_ref: 'credential-ref',
        fingerprint: PROVIDER_FINGERPRINT,
        handle: null,
        observation: null,
        prepared_artifact: null,
        artifact: null,
        ...overrides,
    };
}

function providerNeedsConfirmationTask(id = 'task-continue', overrides: Record<string, unknown> = {}) {
    return {
        ...providerI2vTask(id),
        status: 'needs_confirmation',
        provider_execution: providerExecution(),
        ...overrides,
    } as ReturnType<typeof providerI2vTask> & Record<string, unknown>;
}


type OrderedMode = 'i2v' | 'r2v';
type OrderedMediaRole = 'first_frame' | 'last_frame' | 'reference_image' | 'reference_video' | 'reference_audio';

function orderedMedia(role: OrderedMediaRole, ordinal = 0) {
    const declared_media_type = role === 'reference_video' ? 'video/mp4' : role === 'reference_audio' ? 'audio/wav' : 'image/png';
    return {
        schema_version: 1,
        role,
        ordinal,
        sha256: `${ordinal + 1}`.repeat(64).slice(0, 64),
        size_bytes: 1024 + ordinal,
        declared_media_type,
    };
}

function orderedProviderExecution(mode: OrderedMode, media: ReturnType<typeof orderedMedia>[], overrides: Record<string, unknown> = {}) {
    return {
        schema_version: 1,
        attempt_id: 'attempt-ordered',
        phase: 'accepted',
        provider_model_id: 'wan3.0-video',
        intent: {
            mode,
            model_id: 'wan/wan3.0-video#video',
            catalog_mode: 'video',
            prompt: 'camera move',
            params: { duration: 5, resolution: '720P', prompt_extend: true },
        },
        media,
        scope: {
            provider: 'dashscope',
            backend: 'dashscope',
            gateway: 'dashscope_workspace',
            workspace_id: 'workspace-a',
            region: 'cn-hangzhou',
            base_url: 'https://workspace-a.cn-hangzhou.maas.aliyuncs.com',
            account_identity: null,
        },
        credential_ref: 'credential-ref',
        fingerprint: PROVIDER_FINGERPRINT,
        handle: null,
        observation: null,
        prepared_artifact: null,
        artifact: null,
        ...overrides,
    };
}

function orderedI2vTask(id: string, overrides: Record<string, unknown> = {}) {
    const prompt = typeof overrides.prompt === 'string' ? overrides.prompt : 'camera move';
    return {
        ...providerI2vTask(id),
        model: 'wan3.0-video',
        prompt,
        image_url: 'uploads/source.png',
        last_frame_url: 'uploads/closing.png',
        duration: 5,
        resolution: '720P',
        prompt_extend: true,
        provider_execution: orderedProviderExecution('i2v', [orderedMedia('first_frame'), orderedMedia('last_frame')], {
            intent: { mode: 'i2v', model_id: 'wan/wan3.0-video#video', catalog_mode: 'video', prompt, params: { duration: 5, resolution: '720P', prompt_extend: true } },
        }),
        ...overrides,
    } as ReturnType<typeof providerI2vTask> & Record<string, unknown>;
}

function orderedR2vTask(id: string, overrides: Record<string, unknown> = {}) {
    const prompt = typeof overrides.prompt === 'string' ? overrides.prompt : 'camera move';
    return {
        ...videoTask(id),
        model: 'wan3.0-video',
        prompt,
        image_url: '',
        reference_image_urls: ['/asset.png'],
        reference_video_urls: [],
        reference_audio_urls: [],
        duration: 5,
        resolution: '720P',
        prompt_extend: true,
        provider_execution: orderedProviderExecution('r2v', [orderedMedia('reference_image')], {
            intent: { mode: 'r2v', model_id: 'wan/wan3.0-video#video', catalog_mode: 'video', prompt, params: { duration: 5, resolution: '720P', prompt_extend: true } },
        }),
        ...overrides,
    } as ReturnType<typeof videoTask> & Record<string, unknown>;
}

function projectWithWan3I2V(tasks: ReturnType<typeof providerI2vTask>[] = [], id = 'a', revision = 3): Project {
    return {
        ...projectWithProviderI2VTasks(tasks, id, revision),
        model_settings: { ...(projectWithProviderI2VTasks(tasks, id, revision) as Project).model_settings, i2v_model: 'wan3.0-video' },
        frames: [{ ...i2vFrame('f1'), last_frame_url: 'uploads/closing.png' }],
        video_tasks: tasks,
    } as Project;
}

function wan3I2VServerProject(tasks: ReturnType<typeof providerI2vTask>[] = [], revision = 4) {
    return {
        ...providerServerProject(tasks, revision),
        model_settings: { ...(providerServerProject(tasks, revision) as unknown as Project).model_settings, i2v_model: 'wan3.0-video' },
        frames: [{ ...i2vFrame('f1'), last_frame_url: 'uploads/closing.png' }],
        video_tasks: tasks,
    };
}

function projectWithWan3R2V(tasks: ReturnType<typeof videoTask>[] = [], id = 'a', revision = 3): Project {
    return {
        ...projectWithVideoTasks(tasks, id, revision),
        model_settings: { ...(projectWithVideoTasks(tasks, id, revision) as Project).model_settings, r2v_model: 'wan3.0-video' },
    } as Project;
}

function wan3R2VServerProject(tasks: ReturnType<typeof videoTask>[] = [], revision = 4) {
    return {
        ...serverProject(tasks, revision),
        model_settings: { ...(serverProject(tasks, revision) as unknown as Project).model_settings, r2v_model: 'wan3.0-video' },
        video_tasks: tasks,
    };
}

async function openReviewQueue() {
    fireEvent.click(screen.getByRole('button', { name: /Task queue/i }));
    await screen.findByRole('region', { name: /Task queue/i });
    fireEvent.click(screen.getByRole('tab', { name: /Review|待确认/i }));
}

function projectWithProviderI2VTasks(tasks: ReturnType<typeof providerI2vTask>[] = [], id = 'a', revision = 3): Project {
    return {
        ...project(id, revision),
        model_settings: { ...(project(id, revision) as Project).model_settings, i2v_model: 'wan2.6-i2v-flash' },
        frames: [i2vFrame('f1')],
        video_tasks: tasks,
    } as Project;
}

function projectWithTwoProviderI2VFrames(tasks: ReturnType<typeof providerI2vTask>[] = [], id = 'a', revision = 3): Project {
    return {
        ...projectWithProviderI2VTasks(tasks, id, revision),
        frames: [i2vFrame('f1'), i2vFrame('f2')],
    } as Project;
}


function projectWithWan27I2V(tasks: ReturnType<typeof providerI2vTask>[] = [], id = 'a', revision = 3): Project {
    return {
        ...projectWithProviderI2VTasks(tasks, id, revision),
        model_settings: { ...(projectWithProviderI2VTasks(tasks, id, revision) as Project).model_settings, i2v_model: 'wan2.7-i2v' },
    } as Project;
}

function providerServerProject(tasks: ReturnType<typeof providerI2vTask>[] = [], revision = 4) {
    return {
        ...ack([i2vFrame('f1')], 'a', revision),
        model_settings: { ...(ack([i2vFrame('f1')], 'a', revision) as unknown as Project).model_settings, i2v_model: 'wan2.6-i2v-flash' },
        video_tasks: tasks,
    };
}

function providerReceipt(tasks: ReturnType<typeof providerI2vTask>[], clientRequestId = 'request-fixed') {
    return {
        client_request_id: clientRequestId,
        replayed: false,
        task_ids: tasks.map(task => task.id),
        tasks,
        reason: null,
    };
}

const providerStorageKey = (frameId = 'f1') => providerVideoRequestStorageKey({
    apiBase: API_URL,
    storageId: currentReadEnvironment().match(/\"([^\"]+)\"\]$/)?.[1] ?? '11111111-1111-4111-8111-111111111111',
    projectId: 'a',
    frameId,
});

function storeProviderPending(clientRequestId: string, overrides: Record<string, unknown> = {}, frameId = 'f1') {
    localStorage.setItem(providerStorageKey(frameId), JSON.stringify({
        schema_version: 1,
        owner: { apiBase: API_URL, storageId: '11111111-1111-4111-8111-111111111111', projectId: 'a', frameId },
        client_request_id: clientRequestId,
        payload: {
            client_request_id: clientRequestId,
            batch_size: 1,
            image_url: 'uploads/source.png',
            prompt: 'existing',
            model: 'wan2.6-i2v-flash',
            frame_id: frameId,
            generation_mode: 'i2v',
            workbench_tab: 't2i_i2v',
            ...overrides,
        },
    }));
}

function installProviderLock() {
    const request = vi.fn(async (_name: string, _options: LockOptions & { ifAvailable?: boolean }, callback: (lock: object | null) => Promise<unknown>) => callback({}));
    vi.stubGlobal('navigator', { locks: { request } });
    return request;
}

function r2vFrame(id = 'f1') {
    return {
        ...frame(id),
        action_description: '[character1:shared] camera move',
        visual_description: '[character1:shared] camera move',
        assembled_prompt: '[character1:shared] camera move',
        workbench_tab_mode: 'direct_r2v',
    };
}

function videoTask(id: string, frameId = 'f1', projectId = 'a') {
    return {
        id,
        project_id: projectId,
        frame_id: frameId,
        prompt: '[character1:shared] camera move',
        image_url: '',
        video_url: '',
        status: 'pending',
        workbench_tab: 'direct_r2v',
        generation_mode: 'r2v',
    };
}

function projectWithVideoTasks(tasks: ReturnType<typeof videoTask>[] = [], id = 'a', revision = 3): Project {
    return {
        ...project(id, revision),
        frames: [r2vFrame('f1')],
        characters: [{ id: 'shared', name: 'shared', source: 'series', reference_sheet: { selected_image_id: 'img1', image_variants: [{ id: 'img1', url: '/asset.png', created_at: 1 }] }, image_url: '/asset.png' }],
        video_tasks: tasks,
    } as Project;
}

function serverProject(tasks: ReturnType<typeof videoTask>[] = [], revision = 4) {
    return {
        ...ack([r2vFrame('f1')], 'a', revision),
        characters: [{ id: 'shared', name: 'shared', source: 'series', reference_sheet: { selected_image_id: 'img1', image_variants: [{ id: 'img1', url: '/asset.png', created_at: 1 }] }, image_url: '/asset.png' }],
        video_tasks: tasks,
    };
}

function renderPage(initial: Project = projectWithVideoTasks()) {
    useProjectStore.setState({
        projects: [initial],
        currentProject: initial,
        currentSeries: null,
        readWatermarks: {},
        runningOps: {},
        renderingFrames: new Set(),
    });
    return renderWithIntl(<StoryboardR2V />);
}

function firstShot() {
    return screen.getByTestId('shot');
}

async function expandFirstShotParams() {
    const shot = firstShot();
    const toggle = within(shot).getByRole('button', { name: 'toggle shot' });
    if (toggle.getAttribute('aria-expanded') !== 'true') {
        fireEvent.click(toggle);
    }
    await screen.findByText('I2V Params');
    const advanced = screen.getByRole('button', { name: /Advanced|高级参数/i });
    if (advanced.getAttribute('aria-expanded') !== 'true') {
        fireEvent.click(advanced);
    }
    await screen.findByText('Prompt extend');
    return shot;
}

function toggleParam(label: string) {
    const labelElement = screen.getByText(label);
    const row = labelElement.parentElement;
    if (!row) throw new Error(`Missing param row for ${label}`);
    fireEvent.click(within(row).getByRole('button'));
    return within(row).getByRole('button');
}

const videoSubmitKey = (projectId = 'a', frameId = 'f1') =>
    `video-submit:${encodeURIComponent(currentReadEnvironment())}:${encodeURIComponent(projectId)}:${encodeURIComponent(frameId)}`;

beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
});

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('StoryboardR2V video submit readback', () => {
    it('routes failed-state retry through batch submit and accepts only after committed task readback', async () => {
        const accepted = videoTask('task-1');
        vi.mocked(axios.post).mockResolvedValueOnce({ data: accepted });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(serverProject([accepted], 4)) });
        renderPage();

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        await waitFor(() => expect(axios.get).toHaveBeenCalledExactlyOnceWith(`${API_URL}/projects/a`));
        await waitFor(() => expect(within(firstShot()).getByTestId('shot-data')).toHaveTextContent('task-1'));
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ batch_size: 1, frame_id: 'f1', generation_mode: 'r2v' });
    });

    it('keeps the paid submit lock across same-project remount until readback finishes', async () => {
        const pending = deferred<{ data: ReturnType<typeof videoTask> }>();
        vi.mocked(axios.post).mockReturnValueOnce(pending.promise);
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(serverProject([videoTask('task-1')], 4)) });
        const rendered = renderPage();

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        rendered.unmount();
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(useProjectStore.getState().runningOps[videoSubmitKey()]).toBe(true);
        pending.resolve({ data: videoTask('task-1') });
        await waitFor(() => expect(useProjectStore.getState().runningOps[videoSubmitKey()]).not.toBe(true));
    });


    it('does not read back or write the local shot when an old project session returns after A-B-A switch', async () => {
        const pending = deferred<{ data: ReturnType<typeof videoTask> }>();
        const a = projectWithVideoTasks([], 'a', 3);
        const b = projectWithVideoTasks([], 'b', 3);
        vi.mocked(axios.post).mockReturnValueOnce(pending.promise);
        renderPage(a);

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        await act(async () => {
            useProjectStore.setState({ projects: [a, b], currentProject: b });
        });
        await act(async () => {
            useProjectStore.setState({ projects: [a, b], currentProject: a });
        });
        expect(useProjectStore.getState().runningOps[videoSubmitKey()]).toBe(true);
        pending.resolve({ data: videoTask('task-old') });
        await waitFor(() => expect(useProjectStore.getState().runningOps[videoSubmitKey()]).not.toBe(true));
        expect(axios.get).not.toHaveBeenCalled();
        expect(within(firstShot()).getByTestId('shot-data')).not.toHaveTextContent('task-old');
    });

    it('rejects video submit before paid POST when the project was read from a foreign environment', () => {
        renderPage({ ...projectWithVideoTasks(), readEnvironment: 'https://other.example' });

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('shows generation-specific pending save feedback and waits for an explicit second click', async () => {
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-save-gate');
        installProviderLock();
        const savedFrame = { ...i2vFrame('f1'), updated_at: 10, workbench_generate_count: 2 };
        const accepted = [providerI2vTask('task-1'), providerI2vTask('task-2')];
        vi.mocked(axios.patch).mockResolvedValueOnce({ data: savedFrame });
        vi.mocked(axios.get)
            .mockResolvedValueOnce({ data: enriched({ ...providerServerProject([], 4), frames: [savedFrame] }) })
            .mockResolvedValueOnce({ data: enriched(providerServerProject(accepted, 5)) });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-save-gate') });
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'set count two' }));
        const blockedGenerate = within(firstShot()).getByRole('button', { name: /saving generation settings|正在保存生成设置/i });
        expect(blockedGenerate).toBeDisabled();
        expect(blockedGenerate).toHaveAttribute('data-busy', 'true');
        fireEvent.click(blockedGenerate);
        expect(axios.post).not.toHaveBeenCalled();

        await waitFor(() => expect(axios.patch).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/workbench`, { workbench_generate_count: 2 }));
        await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'generate two' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/provider_video_tasks`, expect.objectContaining({
            client_request_id: 'request-save-gate',
            batch_size: 2,
            frame_id: 'f1',
            generation_mode: 'i2v',
            workbench_tab: 't2i_i2v',
        }));
    });

    it('keeps generation gated when the pending settings save fails', async () => {
        installProviderLock();
        vi.mocked(axios.patch).mockRejectedValueOnce({ response: { status: 503 } });
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'set count two' }));
        await waitFor(() => expect(axios.patch).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/workbench`, { workbench_generate_count: 2 }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/保存失败|could not be saved/i);

        const blockedGenerate = within(firstShot()).getByRole('button', { name: /resolve unsaved shot edits|处理未保存的分镜编辑/i });
        expect(blockedGenerate).toBeDisabled();
        expect(blockedGenerate).toHaveAttribute('data-busy', 'false');
        fireEvent.click(blockedGenerate);
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('keeps generation gated when settings save commits but project readback fails', async () => {
        installProviderLock();
        const savedFrame = { ...i2vFrame('f1'), updated_at: 10, workbench_generate_count: 2 };
        vi.mocked(axios.patch).mockResolvedValueOnce({ data: savedFrame });
        vi.mocked(axios.get).mockRejectedValueOnce({ response: { status: 503 } });
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'set count two' }));
        await waitFor(() => expect(axios.patch).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/workbench`, { workbench_generate_count: 2 }));
        await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
        expect(await screen.findByRole('alert')).toHaveTextContent(/读取失败|could not be read/i);

        const blockedGenerate = within(firstShot()).getByRole('button', { name: /resolve unsaved shot edits|处理未保存的分镜编辑/i });
        expect(blockedGenerate).toBeDisabled();
        expect(blockedGenerate).toHaveAttribute('data-busy', 'false');
        fireEvent.click(blockedGenerate);
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('keeps successful batch tasks when a sibling submit fails', async () => {
        const accepted = videoTask('task-1');
        vi.mocked(axios.post)
            .mockResolvedValueOnce({ data: accepted })
            .mockRejectedValueOnce({ response: { status: 503, data: { detail: 'provider unavailable' } } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(serverProject([accepted], 4)) });
        renderPage();

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'generate two' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledTimes(2);
        expect(within(firstShot()).getByTestId('shot-data')).toHaveTextContent('task-1');
        expect(toast.warning).toHaveBeenCalledWith(expect.stringMatching(/1\/2/));
    });



    it('accepts valid siblings but blocks continuation when another fulfilled ACK is malformed', async () => {
        const accepted = videoTask('task-1');
        vi.mocked(axios.post)
            .mockResolvedValueOnce({ data: accepted })
            .mockResolvedValueOnce({ data: { ...videoTask(''), id: '' } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(serverProject([accepted], 4)) });
        renderPage();

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'generate two' }));

        await waitFor(() => expect(within(firstShot()).getByTestId('shot-data')).toHaveTextContent('task-1'));
        expect(toast.warning).toHaveBeenCalledWith(expect.stringMatching(/1\/2/));
        expect(await screen.findByRole('alert')).toHaveTextContent(/读取|read/i);
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        expect(axios.post).toHaveBeenCalledTimes(2);
    });

    it.each([
        ['empty id', { ...videoTask(''), id: '' }],
        ['foreign project', videoTask('task-foreign', 'f1', 'other')],
        ['wrong workbench tab', { ...videoTask('task-tab'), workbench_tab: 't2i_i2v' }],
        ['wrong generation mode', { ...videoTask('task-mode'), generation_mode: 'i2v' }],
    ])('treats %s task acknowledgement as unknown outcome and blocks paid repeat', async (_label, badAck) => {
        vi.mocked(axios.post).mockResolvedValueOnce({ data: badAck });
        renderPage();

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/读取|read/i);
        expect(within(firstShot()).getByTestId('shot-data')).not.toHaveTextContent('failed');
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('blocks paid resubmit after create succeeds but committed readback misses the accepted task', async () => {
        vi.mocked(axios.post).mockResolvedValueOnce({ data: videoTask('task-1') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(serverProject([], 4)) });
        renderPage();

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/读取|read/i);
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it.each([
        ['network response loss', new Error('connection lost')],
        ['server response failure', { response: { status: 500, data: { detail: 'failed after admission' } } }],
    ])('keeps %s unconfirmed instead of allowing a paid repeat', async (_label, error) => {
        vi.mocked(axios.post).mockRejectedValueOnce(error);
        renderPage();
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/读取|read/i);
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(within(firstShot()).getByTestId('shot-data')).not.toHaveTextContent('failed');
    });

    it('requires a read after an accepted POST resolves to an obsolete mount', async () => {
        const pending = deferred<{ data: ReturnType<typeof videoTask> }>();
        vi.mocked(axios.post).mockReturnValueOnce(pending.promise);
        const view = renderPage();
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        view.unmount();
        renderWithIntl(<StoryboardR2V />);
        await act(async () => pending.resolve({ data: videoTask('paid-task') }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/读取|read/i);
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('blocks structural edits while a same-project video submit remains unresolved after remount', async () => {
        const pending = deferred<{ data: ReturnType<typeof videoTask> }>();
        vi.mocked(axios.post).mockReturnValueOnce(pending.promise);
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(serverProject([videoTask('task-1')], 4)) });
        const rendered = renderPage();

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        rendered.unmount();
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(screen.getAllByRole('button', { name: /Add Shot|添加镜头/ })[0]);

        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(await screen.findByRole('alert')).toHaveTextContent(/暂不能|temporarily unavailable/i);
        pending.resolve({ data: videoTask('task-1') });
        await waitFor(() => expect(useProjectStore.getState().runningOps[videoSubmitKey()]).not.toBe(true));
    });


    it('keeps a provider task when cancel returns provider_cancel_unconfirmed', async () => {
        const task = videoTask('task-cancel');
        vi.mocked(axios.post).mockRejectedValueOnce({ response: { status: 409, data: { code: 'provider_cancel_unconfirmed' } } });
        renderPage(projectWithVideoTasks([task]));

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'cancel video' }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/kept the task|保留该任务/i);
        expect(within(firstShot()).getByTestId('shot-data')).toHaveTextContent('task-cancel');
    });

    it('rejects readback tasks whose mode or workbench tab do not match the accepted submit', async () => {
        vi.mocked(axios.post).mockResolvedValueOnce({ data: videoTask('task-1') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(serverProject([{ ...videoTask('task-1'), workbench_tab: 't2i_i2v' }], 4)) });
        renderPage();

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/读取|read/i);
        expect(within(firstShot()).getByTestId('shot-data')).not.toHaveTextContent('task-1');
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        expect(axios.post).toHaveBeenCalledTimes(1);
    });
});


describe('StoryboardR2V provider receipt video submit', () => {


    it('routes Wan3 I2V first/last through the ordered provider journal', async () => {
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-wan3-i2v');
        installProviderLock();
        const accepted = [orderedI2vTask('task-wan3-i2v')];
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-wan3-i2v') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(wan3I2VServerProject(accepted, 4)) });
        renderPage(projectWithWan3I2V());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/provider_video_tasks`, expect.objectContaining({
            client_request_id: 'request-wan3-i2v',
            contract_version: ORDERED_MEDIA_CONTRACT_VERSION,
            model: 'wan3.0-video',
            frame_id: 'f1',
            generation_mode: 'i2v',
            workbench_tab: 't2i_i2v',
            image_url: 'uploads/source.png',
            last_frame_url: 'uploads/closing.png',
        }));
        const payload = vi.mocked(axios.post).mock.calls[0][1] as Record<string, unknown>;
        expect(payload).not.toHaveProperty('negative_prompt');
        expect(payload).not.toHaveProperty('shot_type');
        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toBeNull());
    });

    it('routes Wan3 direct R2V asset references through the ordered provider journal', async () => {
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-wan3-r2v');
        installProviderLock();
        const accepted = [orderedR2vTask('task-wan3-r2v')];
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-wan3-r2v') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(wan3R2VServerProject(accepted, 4)) });
        renderPage(projectWithWan3R2V());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/provider_video_tasks`, expect.objectContaining({
            client_request_id: 'request-wan3-r2v',
            contract_version: ORDERED_MEDIA_CONTRACT_VERSION,
            model: 'wan3.0-video',
            frame_id: 'f1',
            generation_mode: 'r2v',
            workbench_tab: 'direct_r2v',
            reference_image_urls: ['/asset.png'],
        }));
        expect(`${vi.mocked(axios.post).mock.calls[0][0]}`).not.toContain('/video_tasks');
        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toBeNull());
    });

    it('recovers an ordered direct R2V pending request using its saved payload identity', async () => {
        storeProviderPending('request-wan3-existing', {
            contract_version: ORDERED_MEDIA_CONTRACT_VERSION,
            model: 'wan3.0-video',
            reference_image_urls: ['/asset.png'],
            generation_mode: 'r2v',
            workbench_tab: 'direct_r2v',
        });
        installProviderLock();
        const accepted = [orderedR2vTask('task-wan3-existing', { prompt: 'existing' })];
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-wan3-existing') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(wan3R2VServerProject(accepted, 4)) });
        renderPage(projectWithWan3R2V());

        fireEvent.click(await screen.findByRole('button', { name: /retry this submission|重试本次提交/i }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({
            client_request_id: 'request-wan3-existing',
            contract_version: ORDERED_MEDIA_CONTRACT_VERSION,
            generation_mode: 'r2v',
            workbench_tab: 'direct_r2v',
            reference_image_urls: ['/asset.png'],
        });
        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toBeNull());
    });

    it('keeps an ordered request pending when provider intent drifts from the frozen payload', async () => {
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-wan3-drift');
        installProviderLock();
        const accepted = [orderedR2vTask('task-wan3-drift', {
            provider_execution: orderedProviderExecution('r2v', [orderedMedia('reference_image')], {
                intent: {
                    mode: 'r2v',
                    model_id: 'wan/wan3.0-video#video',
                    catalog_mode: 'video',
                    prompt: 'camera move',
                    params: { duration: 6, resolution: '720P', prompt_extend: true },
                },
            }),
        })];
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-wan3-drift') });
        renderPage(projectWithWan3R2V());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toContain('request-wan3-drift'));
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('uploads Wan3 last frame with managed local_url and verifies the queued workbench save', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
            url: 'https://oss.example.com/signed.png?secret=1',
            local_url: 'uploads/last-local.png',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
        vi.mocked(axios.patch).mockResolvedValueOnce({ data: { ...i2vFrame('f1'), id: 'f1', updated_at: 10, last_frame_url: 'uploads/last-local.png' } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched({
            ...wan3I2VServerProject([], 4),
            frames: [{ ...i2vFrame('f1'), updated_at: 10, last_frame_url: 'uploads/last-local.png' }],
        }) });
        renderPage(projectWithWan3I2V());
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'toggle shot' }));

        fireEvent.click(screen.getByRole('button', { name: 'upload last frame' }));

        await waitFor(() => expect(axios.patch).toHaveBeenCalledWith(
            `${API_URL}/projects/a/frames/f1/workbench`,
            { last_frame_url: 'uploads/last-local.png' },
        ));
        expect(JSON.stringify(vi.mocked(axios.patch).mock.calls[0][1])).not.toContain('oss.example.com');
        await waitFor(() => expect(within(firstShot()).getByTestId('shot-data')).toHaveTextContent('uploads/last-local.png'));
    });

    it('does not patch a last frame upload after the project owner changes', async () => {
        const upload = deferred<Response>();
        vi.stubGlobal('fetch', vi.fn().mockReturnValue(upload.promise));
        renderPage(projectWithWan3I2V());
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'toggle shot' }));

        fireEvent.click(screen.getByRole('button', { name: 'upload last frame' }));
        act(() => {
            useProjectStore.setState({
                projects: [projectWithWan3I2V([], 'a'), project('b')],
                currentProject: project('b'),
            });
        });
        upload.resolve(new Response(JSON.stringify({ local_url: 'uploads/stale.png' }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

        await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(axios.patch).not.toHaveBeenCalled());
    });

    it('rejects remote-only last frame upload acknowledgements before workbench write', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
            url: 'https://oss.example.com/signed.png?secret=1',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
        renderPage(projectWithWan3I2V());
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'toggle shot' }));

        fireEvent.click(screen.getByRole('button', { name: 'upload last frame' }));

        await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
        expect(axios.patch).not.toHaveBeenCalled();
    });


    it('submits real visible Wan2.7 I2V through the provider receipt route without legacy fallback', async () => {
        expect(VIDEO_I2V_MODELS.some(model => model.id === 'wan2.7-i2v')).toBe(true);
        expect(VIDEO_I2V_MODELS.find(model => model.id === 'wan2.7-i2v')?.name).toBe('Wan 2.7 I2V');
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-wan27');
        installProviderLock();
        const accepted = [providerI2vTask('task-27')];
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-wan27') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject(accepted, 4)) });
        renderPage(projectWithWan27I2V());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/provider_video_tasks`, expect.objectContaining({
            client_request_id: 'request-wan27',
            model: 'wan2.7-i2v',
            frame_id: 'f1',
            generation_mode: 'i2v',
            workbench_tab: 't2i_i2v',
        }));
        expect(`${vi.mocked(axios.post).mock.calls[0][0]}`).not.toContain('/video_tasks');
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('shot_type');
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('generate_audio');
        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toBeNull());
    });

    it('uses real Wan2.7 params controls to submit explicit watermark on and prompt extension off', async () => {
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-wan27-watermark-on');
        installProviderLock();
        const accepted = [providerI2vTask('task-27')];
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-wan27-watermark-on') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject(accepted, 4)) });
        renderPage(projectWithWan27I2V());

        await expandFirstShotParams();
        const promptExtend = toggleParam('Prompt extend');
        await waitFor(() => expect(promptExtend).toHaveAttribute('aria-pressed', 'false'));
        const watermark = toggleParam('Watermark');
        await waitFor(() => expect(watermark).toHaveAttribute('aria-pressed', 'true'));
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/provider_video_tasks`, expect.objectContaining({
            client_request_id: 'request-wan27-watermark-on',
            model: 'wan2.7-i2v',
            watermark: true,
            prompt_extend: false,
        }));
        const payload = vi.mocked(axios.post).mock.calls[0][1] as Record<string, unknown>;
        expect(payload).not.toHaveProperty('shot_type');
        expect(payload).not.toHaveProperty('generate_audio');
        expect(`${vi.mocked(axios.post).mock.calls[0][0]}`).toBe(`${API_URL}/projects/a/provider_video_tasks`);
        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toBeNull());
    });

    it('uses real Wan2.7 params controls to submit explicit watermark off and prompt extension on', async () => {
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-wan27-watermark-off');
        installProviderLock();
        const accepted = [providerI2vTask('task-27')];
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-wan27-watermark-off') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject(accepted, 4)) });
        renderPage(projectWithWan27I2V());

        await expandFirstShotParams();
        const promptExtend = toggleParam('Prompt extend');
        await waitFor(() => expect(promptExtend).toHaveAttribute('aria-pressed', 'false'));
        toggleParam('Prompt extend');
        await waitFor(() => expect(promptExtend).toHaveAttribute('aria-pressed', 'true'));
        const watermark = toggleParam('Watermark');
        await waitFor(() => expect(watermark).toHaveAttribute('aria-pressed', 'true'));
        toggleParam('Watermark');
        await waitFor(() => expect(watermark).toHaveAttribute('aria-pressed', 'false'));
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/provider_video_tasks`, expect.objectContaining({
            client_request_id: 'request-wan27-watermark-off',
            model: 'wan2.7-i2v',
            watermark: false,
            prompt_extend: true,
        }));
        const payload = vi.mocked(axios.post).mock.calls[0][1] as Record<string, unknown>;
        expect(payload).not.toHaveProperty('shot_type');
        expect(payload).not.toHaveProperty('generate_audio');
        expect(`${vi.mocked(axios.post).mock.calls[0][0]}`).toBe(`${API_URL}/projects/a/provider_video_tasks`);
        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toBeNull());
    });

    it('does not queue a second provider submit into a new paid request after the first resolves', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValueOnce('request-first').mockReturnValueOnce('request-second');
        let locked = false;
        const request = vi.fn(async (_name: string, _options: LockOptions & { ifAvailable?: boolean }, callback: (lock: object | null) => Promise<unknown>) => {
            if (locked) return callback(null);
            locked = true;
            try { return await callback({}); } finally { locked = false; }
        });
        vi.stubGlobal('navigator', { locks: { request } });
        const accepted = [providerI2vTask('task-1')];
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-first') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject(accepted, 4)) });
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ client_request_id: 'request-first' });
        expect(globalThis.crypto.randomUUID).toHaveBeenCalledTimes(1);
    });

    it('fails closed when provider Web Locks are unavailable', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-fixed');
        vi.stubGlobal('navigator', {});
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/exclusive lock|互斥锁|确认/i);
        expect(axios.post).not.toHaveBeenCalled();
        expect(localStorage.getItem(providerStorageKey())).toBeNull();
    });

    it('re-reads pending inside the provider lock so stale retry state cannot post an old id', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-fixed');
        storeProviderPending('request-stale', { prompt: 'stale' });
        const request = vi.fn(async (_name: string, _options: LockOptions & { ifAvailable?: boolean }, callback: (lock: object | null) => Promise<unknown>) => {
            storeProviderPending('request-existing', { prompt: 'existing' });
            return callback({});
        });
        vi.stubGlobal('navigator', { locks: { request } });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt([providerI2vTask('task-1')], 'request-existing') });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject([providerI2vTask('task-1')], 4)) });
        renderPage(projectWithProviderI2VTasks());

        const retry = await screen.findByRole('button', { name: /retry this submission|重试本次提交/i });
        fireEvent.click(retry);

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ client_request_id: 'request-existing', prompt: 'existing' });
        expect(request).toHaveBeenCalledTimes(1);
    });

    it('keeps provider pending visible when resolve cannot remove the same stored request', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-fixed');
        installProviderLock();
        const accepted = [providerI2vTask('task-1')];
        const readback = deferred<{ data: ReturnType<typeof providerServerProject> }>();
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted) });
        vi.mocked(axios.get).mockReturnValueOnce(readback.promise);
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        storeProviderPending('request-other', { prompt: 'newer intent' });
        readback.resolve({ data: enriched(providerServerProject(accepted, 4)) });

        const retry = await screen.findByRole('button', { name: /retry this submission|重试本次提交/i });
        expect(retry).not.toBeDisabled();
        expect(localStorage.getItem(providerStorageKey())).toContain('request-other');
    });
    it('submits Wan2.6 I2V batch as one provider request and resolves only after committed readback', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        const accepted = [providerI2vTask('task-1'), providerI2vTask('task-2')];
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-fixed');
        installProviderLock();
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted) });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject(accepted, 4)) });
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'generate two' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/provider_video_tasks`, expect.objectContaining({
            client_request_id: 'request-fixed',
            batch_size: 2,
            model: 'wan2.6-i2v-flash',
            frame_id: 'f1',
            generation_mode: 'i2v',
            workbench_tab: 't2i_i2v',
        }));
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('audio_url');
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('reference_video_urls');
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('reference_image_urls');
        await waitFor(() => expect(within(firstShot()).getByTestId('shot-data')).toHaveTextContent('task-2'));
        expect(localStorage.getItem(providerStorageKey())).toBeNull();
    });


    it('clears an exact pending request for a confirmed provider tombstone without reposting', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        storeProviderPending('request-fixed', { batch_size: 2 });
        installProviderLock();
        vi.mocked(axios.post).mockResolvedValueOnce({
            data: {
                client_request_id: 'request-fixed',
                replayed: true,
                task_ids: ['task-1', 'task-2'],
                tasks: [providerI2vTask('task-1')],
                reason: 'provider_request_deleted',
            },
        });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject([], 4)) });
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(await screen.findByRole('button', { name: /retry this submission|重试本次提交/i }));

        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toBeNull());
        expect(screen.queryByRole('button', { name: /retry this submission|重试本次提交/i })).toBeNull();
        expect(await screen.findByRole('alert')).toHaveTextContent(/deleted|删除|不存在/i);
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('keeps provider pending when tombstone replay readback cannot confirm current project', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        storeProviderPending('request-fixed', { batch_size: 2 });
        installProviderLock();
        vi.mocked(axios.post).mockResolvedValueOnce({
            data: {
                client_request_id: 'request-fixed',
                replayed: true,
                task_ids: ['task-1', 'task-2'],
                tasks: [providerI2vTask('task-1')],
                reason: 'provider_request_owner_deleted',
            },
        });
        vi.mocked(axios.get).mockRejectedValueOnce(new Error('readback failed'));
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(await screen.findByRole('button', { name: /retry this submission|重试本次提交/i }));

        expect(await screen.findByRole('button', { name: /retry this submission|重试本次提交/i })).not.toBeDisabled();
        expect(localStorage.getItem(providerStorageKey())).toContain('request-fixed');
        expect(axios.post).toHaveBeenCalledTimes(1);
    });
    it('keeps unknown provider outcome pending and retries the same request id from an enabled recovery button', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-fixed');
        installProviderLock();
        vi.mocked(axios.post)
            .mockRejectedValueOnce({ response: { status: 503, data: { detail: 'saved after timeout' } } })
            .mockResolvedValueOnce({ data: providerReceipt([providerI2vTask('task-1')]) });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject([providerI2vTask('task-1')], 4)) });
        renderPage(projectWithProviderI2VTasks());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/unconfirmed|确认|读取/i);
        const retry = await screen.findByRole('button', { name: /retry this submission|重试本次提交/i });
        expect(retry).not.toBeDisabled();
        fireEvent.click(retry);

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
        expect(vi.mocked(axios.post).mock.calls[1][1]).toMatchObject(vi.mocked(axios.post).mock.calls[0][1] as Record<string, unknown>);
        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toBeNull());
        await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
        expect(within(firstShot()).getByRole('button', { name: 'retry video' })).not.toBeDisabled();
    });

    it('keeps the pending gate when another frame request remains unresolved after exact recovery', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-f1');
        installProviderLock();
        const accepted = [providerI2vTask('task-1')];
        const readback = deferred<{ data: ReturnType<typeof providerServerProject> & { frames: ReturnType<typeof i2vFrame>[] } }>();
        vi.mocked(axios.post).mockResolvedValueOnce({ data: providerReceipt(accepted, 'request-f1') });
        vi.mocked(axios.get).mockReturnValueOnce(readback.promise);
        renderPage(projectWithTwoProviderI2VFrames());

        fireEvent.click(within(screen.getAllByTestId('shot')[0]).getByRole('button', { name: 'retry video' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        storeProviderPending('request-f2', { prompt: 'second frame', frame_id: 'f2' }, 'f2');
        readback.resolve({
            data: {
                ...enriched(providerServerProject(accepted, 4)),
                frames: [i2vFrame('f1'), i2vFrame('f2')],
            },
        });

        await waitFor(() => expect(localStorage.getItem(providerStorageKey())).toBeNull());
        expect(localStorage.getItem(providerStorageKey('f2'))).toContain('request-f2');
        expect(await screen.findByRole('alert')).toHaveTextContent(/unconfirmed|确认|读取/i);
        expect(screen.getByRole('button', { name: /retry this submission|重试本次提交/i })).not.toBeDisabled();
        expect(within(screen.getAllByTestId('shot')[0]).getByRole('button', { name: 'retry video' })).toBeDisabled();
    });

    it('continues only the original accepted provider attempt from the review queue', async () => {
        const original = providerNeedsConfirmationTask();
        const submitted = providerNeedsConfirmationTask('task-continue', {
            status: 'processing',
            provider_execution: providerExecution({ phase: 'submitted' }),
        });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: submitted });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject([submitted], 4)) });
        renderPage(projectWithProviderI2VTasks([original]));

        await openReviewQueue();
        fireEvent.click(await screen.findByRole('button', { name: /Continue original submission|继续原提交/i }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/provider_video_tasks/task-continue/continue`, {
            expected_attempt_id: 'attempt-original',
            expected_fingerprint: PROVIDER_FINGERPRINT,
        });
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('prompt');
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('model');
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('client_request_id');
        expect(vi.mocked(axios.post).mock.calls[0][1]).not.toHaveProperty('batch_size');
        await waitFor(() => expect(axios.get).toHaveBeenCalledWith(`${API_URL}/projects/a`));
    });

    it('accepts a needs-confirmation provider continue acknowledgement and refreshes diagnostics', async () => {
        const original = providerNeedsConfirmationTask('task-continue', { error: 'old diagnostic' });
        const missingCredential = providerNeedsConfirmationTask('task-continue', { error: 'provider_secret_unavailable' });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: missingCredential });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject([missingCredential], 4)) });
        renderPage(projectWithProviderI2VTasks([original]));

        await openReviewQueue();
        fireEvent.click(await screen.findByRole('button', { name: /Continue original submission|继续原提交/i }));

        await screen.findByText(/provider_secret_unavailable/i);
        expect(screen.queryByRole('alert')).toBeNull();
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(`${vi.mocked(axios.post).mock.calls[0][0]}`).toContain('/provider_video_tasks/task-continue/continue');
        expect(`${vi.mocked(axios.post).mock.calls[0][0]}`).not.toMatch(/\/provider_video_tasks$/);
    });

    it('accepts monotonic provider readback advancement after continue acknowledgement', async () => {
        const original = providerNeedsConfirmationTask();
        const acknowledged = providerNeedsConfirmationTask('task-continue', {
            status: 'processing',
            provider_execution: providerExecution({ phase: 'submitted' }),
        });
        const observed = providerNeedsConfirmationTask('task-continue', {
            status: 'processing',
            provider_execution: providerExecution({ phase: 'observed', handle: { task_id: 'remote-task' } }),
        });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: acknowledged });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject([observed], 4)) });
        renderPage(projectWithProviderI2VTasks([original]));

        await openReviewQueue();
        fireEvent.click(await screen.findByRole('button', { name: /Continue original submission|继续原提交/i }));

        await waitFor(() => expect(screen.queryByRole('button', { name: /Continue original submission|继续原提交/i })).toBeNull());
        expect(screen.queryByRole('alert')).toBeNull();
        expect(axios.get).toHaveBeenCalledWith(`${API_URL}/projects/a`);
    });

    it('does not offer paid continue for submitted, handled, or untyped review tasks', async () => {
        renderPage(projectWithProviderI2VTasks([
            providerNeedsConfirmationTask('task-submitted', { provider_execution: providerExecution({ phase: 'submitted' }) }),
            providerNeedsConfirmationTask('task-handled', { provider_execution: providerExecution({ handle: { task_id: 'remote' } }) }),
            providerNeedsConfirmationTask('task-untyped', { provider_execution: null }),
        ]));

        await openReviewQueue();

        expect(screen.queryByRole('button', { name: /Continue original submission|继续原提交/i })).toBeNull();
        expect(screen.queryByRole('button', { name: /Retry task/i })).toBeNull();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('keeps review diagnostics on provider continue rejection without falling back to generation', async () => {
        const original = providerNeedsConfirmationTask();
        vi.mocked(axios.post).mockRejectedValueOnce({
            response: { status: 409, data: { code: 'provider_continue_not_allowed' } },
        });
        renderPage(projectWithProviderI2VTasks([original]));

        await openReviewQueue();
        fireEvent.click(await screen.findByRole('button', { name: /Continue original submission|继续原提交/i }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/original provider attempt|原始 provider 尝试/i);
        await waitFor(() => expect(screen.getByRole('button', { name: /Continue original submission|继续原提交/i })).not.toBeDisabled());
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(`${vi.mocked(axios.post).mock.calls[0][0]}`).toContain('/provider_video_tasks/task-continue/continue');
        expect(`${vi.mocked(axios.post).mock.calls[0][0]}`).not.toMatch(/\/provider_video_tasks$/);
        expect(`${vi.mocked(axios.post).mock.calls[0][0]}`).not.toContain('/video_tasks');
        expect(axios.get).not.toHaveBeenCalled();
    });

    it('requires committed readback before accepting a provider continue response', async () => {
        const original = providerNeedsConfirmationTask();
        const submitted = providerNeedsConfirmationTask('task-continue', {
            status: 'processing',
            provider_execution: providerExecution({ phase: 'submitted' }),
        });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: submitted });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(providerServerProject([providerNeedsConfirmationTask()], 4)) });
        renderPage(projectWithProviderI2VTasks([original]));

        await openReviewQueue();
        fireEvent.click(await screen.findByRole('button', { name: /Continue original submission|继续原提交/i }));

        expect(await screen.findByRole('alert')).toHaveTextContent(/read|读取|confirm/i);
        expect(screen.getByRole('button', { name: /Continue original submission|继续原提交/i })).toBeDisabled();
    });

    it('ignores a stale provider continue response after an A-B-A project switch', async () => {
        const original = providerNeedsConfirmationTask();
        const submitted = providerNeedsConfirmationTask('task-continue', {
            status: 'processing',
            provider_execution: providerExecution({ phase: 'submitted' }),
        });
        const pending = deferred<{ data: typeof submitted }>();
        vi.mocked(axios.post).mockReturnValueOnce(pending.promise);
        renderPage(projectWithProviderI2VTasks([original], 'a', 3));

        await openReviewQueue();
        fireEvent.click(await screen.findByRole('button', { name: /Continue original submission|继续原提交/i }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
        await act(async () => {
            useProjectStore.setState({ projects: [projectWithProviderI2VTasks([original], 'a', 3), projectWithProviderI2VTasks([], 'b', 3)], currentProject: projectWithProviderI2VTasks([], 'b', 3) });
        });
        await act(async () => {
            useProjectStore.setState({ projects: [projectWithProviderI2VTasks([original], 'a', 5), projectWithProviderI2VTasks([], 'b', 3)], currentProject: projectWithProviderI2VTasks([original], 'a', 5) });
        });
        pending.resolve({ data: submitted });

        await waitFor(() => expect(axios.get).not.toHaveBeenCalled());
        expect(within(firstShot()).getByTestId('shot-data')).not.toHaveTextContent('processing');
    });

    it('blocks legacy submit for the same frame while provider receipt is pending', async () => {
        localStorage.setItem('storyboard-r2v-model', 'wan2.6-i2v-flash');
        vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('request-fixed');
        installProviderLock();
        vi.mocked(axios.post).mockRejectedValueOnce(new Error('lost'));
        const rendered = renderPage(projectWithProviderI2VTasks());

        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));
        expect(await screen.findByRole('button', { name: /retry this submission|重试本次提交/i })).not.toBeDisabled();
        rendered.unmount();
        const direct = projectWithVideoTasks([], 'a', 3);
        localStorage.setItem('storyboard-r2v-model', 'happyhorse-1.1-i2v');
        renderPage(direct);
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'retry video' }));

        expect(axios.post).toHaveBeenCalledTimes(1);
    });
});
