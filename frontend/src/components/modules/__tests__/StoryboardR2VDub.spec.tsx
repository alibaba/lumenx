import type { ComponentProps } from 'react';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { useProjectStore, type Project } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardR2V from '../StoryboardR2V';
import type ShotCard from '../storyboard-r2v/ShotCard';
import { ack, frame, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
vi.mock('../storyboard-r2v/ShotCard', () => ({
    T2I_HISTORY_LIMIT: 10,
    default: (p: ComponentProps<typeof ShotCard>) => <section data-testid="shot" aria-label={p.shot.id}>
        <textarea aria-label="prompt" value={p.shot.prompt} disabled={p.structureDisabled} onChange={event => p.onUpdatePrompt(event.target.value)} />
        <output data-testid={`hero-${p.shot.id}`}>{p.shot.videoUrl ?? ''}</output>
    </section>,
}));
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/TaskQueuePanel', () => ({ default: () => null }));

function json(data: unknown, ok = true, status = ok ? 200 : 500) {
    return { ok, status, json: async () => data };
}

const source = (taskId = 'task-f1', videoUrl = '/video/f1.mp4', audioUrl = '/audio/f1.mp3', offsetMs = 0) => ({
    schema_version: 1,
    task_id: taskId,
    video_url: videoUrl,
    audio_url: audioUrl,
    offset_ms: offsetMs,
});

function dubProject(): Project {
    const f1 = { ...frame('f1'), dialogue: 'line one', character_ids: ['c1'], audio_url: '/audio/f1.mp3', audio_error: null,
        selected_video_id: 'task-f1', preview_video_url: null, dubbed_video_url: null, dub_preview_source: null, dub_applied_source: null };
    const f2 = { ...frame('f2'), dialogue: '', character_ids: [], audio_url: null, selected_video_id: null };
    return { ...project('a', 3), frames: [f1, f2], characters: [{ id: 'c1', name: 'Hero', source: 'episode' as const, voice_id: 'voice-ui' }],
        video_tasks: [{ id: 'task-f1', project_id: 'a', frame_id: 'f1', status: 'completed', video_url: '/video/f1.mp4', image_url: '', prompt: '', created_at: 1 }] };
}

function audioState(projectRevision = 3) {
    return { project_id: 'a', frame_id: 'f1', read_versions: { project: projectRevision, series: null, library: 1 },
        audio_url: '/audio/f1.mp3', audio_error: null, historical_instructions: null, dialogue: 'line one', voice_id: 'voice-backend',
        options: {}, resolved_request: null, sources: null, prepared: null, freshness: 'current', reason: 'parameters_match' };
}

function previewAck(revision = 4) {
    const current = dubProject();
    const frames = current.frames.map(frame => frame.id === 'f1'
        ? { ...frame, preview_video_url: '/dub/preview.mp4', dub_preview_source: source('task-f1', '/video/f1.mp4', '/audio/f1.mp3', 0) }
        : frame);
    return { ...ack(frames, 'a', revision), characters: current.characters, video_tasks: current.video_tasks };
}

function renderPage(current = dubProject()) {
    useProjectStore.setState({ projects: [current], currentProject: current, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
    return renderWithIntl(<StoryboardR2V />);
}

beforeEach(() => {
    vi.resetAllMocks(); vi.useRealTimers(); localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
        if (String(url).endsWith('/frames/f1/audio/state')) return json(audioState(useProjectStore.getState().currentProject?.revision ?? 3));
        throw new Error(`unexpected fetch ${url}`);
    }));
});

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('StoryboardR2V dub source-safe handlers', () => {
    it('does not apply preview ACK frames locally when the required readback GET fails', async () => {
        vi.mocked(axios.post).mockResolvedValueOnce({ data: previewAck(4) });
        vi.mocked(axios.get).mockRejectedValueOnce(new Error('offline'));
        renderPage();

        fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
        await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', { name: '预听' }));

        await waitFor(() => expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/dub/preview`, {
            video_task_id: 'task-f1',
            offset_ms: 0,
        }, { timeout: 120000 }));
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        expect(await screen.findByText(/读取最新状态失败/)).toBeInTheDocument();
        expect(useProjectStore.getState().currentProject?.frames.find(frame => frame.id === 'f1')?.preview_video_url).toBeNull();
        expect(screen.queryByText('应用覆盖')).not.toBeInTheDocument();
    });


    it('opens the original signed take when candidate click matches task id but applied dub proof is stale', async () => {
        const open = vi.fn();
        vi.stubGlobal('open', open);
        const current = dubProject();
        current.frames = current.frames.map(frame => frame.id === 'f1' ? {
            ...frame,
            audio_url: 'https://signed.invalid/lumenx/audio/current.mp3?Expires=1',
            media_refs: { audio_url: '/audio/current.mp3' },
            dubbed_video_task_id: 'task-f1',
            dubbed_video_url: 'https://signed.invalid/lumenx/dubbed/stale.mp4?Expires=1',
            dub_applied_source: source('task-f1', '/video/f1.mp4', '/audio/old.mp3', 0),
        } : frame);
        current.video_tasks = current.video_tasks?.map(task => task.id === 'task-f1' ? {
            ...task,
            generation_mode: 'r2v',
            video_url: 'https://signed.invalid/lumenx/video/f1.mp4?Expires=1',
            media_refs: { video_url: '/video/f1.mp4' },
        } : task);
        renderPage(current);
        fireEvent.click(screen.getByRole('button', { name: '全部展开' }));

        fireEvent.click(screen.getByRole('button', { name: /Candidate task-f/ }));

        expect(open).toHaveBeenCalledWith(expect.stringContaining('signed.invalid/lumenx/video/f1.mp4'), '_blank', 'noopener');
        expect(open).not.toHaveBeenCalledWith(expect.stringContaining('signed.invalid/lumenx/dubbed/stale.mp4'), '_blank', 'noopener');
    });

    it('opens the signed dubbed video on candidate click when raw media_refs prove the applied source', async () => {
        const open = vi.fn();
        vi.stubGlobal('open', open);
        const current = dubProject();
        current.frames = current.frames.map(frame => frame.id === 'f1' ? {
            ...frame,
            audio_url: 'https://signed.invalid/lumenx/audio/f1.mp3?Expires=1',
            media_refs: { audio_url: '/audio/f1.mp3' },
            dubbed_video_task_id: 'task-f1',
            dubbed_video_url: 'https://signed.invalid/lumenx/dubbed/f1.mp4?Expires=1',
            dub_applied_source: source('task-f1', '/video/f1.mp4', '/audio/f1.mp3', 0),
        } : frame);
        current.video_tasks = current.video_tasks?.map(task => task.id === 'task-f1' ? {
            ...task,
            generation_mode: 'r2v',
            video_url: 'https://signed.invalid/lumenx/video/f1.mp4?Expires=1',
            media_refs: { video_url: '/video/f1.mp4' },
        } : task);
        renderPage(current);
        fireEvent.click(screen.getByRole('button', { name: '全部展开' }));

        fireEvent.click(screen.getByRole('button', { name: /Candidate task-f/ }));

        expect(open).toHaveBeenCalledWith(expect.stringContaining('signed.invalid/lumenx/dubbed/f1.mp4'), '_blank', 'noopener');
    });

    it('passes media_refs through R2V so signed display URLs can match raw dub proof', async () => {
        const current = dubProject();
        current.frames = current.frames.map(frame => frame.id === 'f1' ? {
            ...frame,
            audio_url: 'https://signed.invalid/lumenx/audio/f1.mp3?Expires=1',
            media_refs: { audio_url: '/audio/f1.mp3' },
            dubbed_video_url: 'https://signed.invalid/lumenx/dubbed/f1.mp4?Expires=1',
            dub_applied_source: source('task-f1', '/video/f1.mp4', '/audio/f1.mp3', 44),
            dub_offset_ms: 44,
        } : frame);
        current.video_tasks = current.video_tasks?.map(task => task.id === 'task-f1' ? {
            ...task,
            video_url: 'https://signed.invalid/lumenx/video/f1.mp4?Expires=1',
            media_refs: { video_url: '/video/f1.mp4' },
        } : task);
        renderPage(current);

        expect(screen.getByTestId('hero-f1')).toHaveTextContent('https://signed.invalid/lumenx/dubbed/f1.mp4?Expires=1');
        fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
        await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
        expect(document.querySelector('video')?.getAttribute('src')).toContain('signed.invalid/lumenx/dubbed/f1.mp4');
    });

    it('does not expose preview controls for a selected video task that belongs to another frame', async () => {
        const current = dubProject();
        current.frames = current.frames.map(frame => frame.id === 'f1' ? { ...frame, selected_video_id: 'task-f2' } : frame);
        current.video_tasks = [{ id: 'task-f2', project_id: 'a', frame_id: 'f2', status: 'completed', video_url: '/video/f2.mp4', image_url: '', prompt: '', created_at: 1 }];
        renderPage(current);

        fireEvent.click(screen.getAllByRole('button', { name: /对白音频/ })[0]);
        await waitFor(() => expect(screen.getByText(/voice-backend/)).toBeInTheDocument());
        expect(screen.queryByRole('button', { name: '预听' })).not.toBeInTheDocument();
        expect(screen.getByTestId('hero-f1')).not.toHaveTextContent('/video/f2.mp4');
        expect(axios.post).not.toHaveBeenCalled();
    });
});
