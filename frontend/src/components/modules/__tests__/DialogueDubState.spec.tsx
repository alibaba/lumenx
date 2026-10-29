import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import DialogueAudioRow from '../storyboard-r2v/DialogueAudioRow';
import { api } from '@/lib/api';
import { useProjectStore } from '@/store/projectStore';
import { ack, deferred, enriched, frame } from './frame-command-fixtures';
import { renderWithIntl } from '../../series/__tests__/test-utils';

vi.mock('@/lib/api', () => ({ API_URL: 'http://test.invalid', api: { getDialogueAudioState: vi.fn() } }));

const source = { schema_version: 1, task_id: 'A', video_url: 'video/A.mp4', audio_url: 'audio/line.mp3', offset_ms: 150 };
const props = {
    scriptId: 'p', frameId: 'f1', dialogue: 'line', voiceId: 'voice', audioUrl: 'audio/line.mp3', audioError: null,
    videoUrl: 'video/A.mp4', videoTaskId: 'A', previewVideoUrl: 'video/preview.mp4', dubbedVideoUrl: 'video/applied.mp4',
    dubOffsetMs: 25, onPreviewDub: vi.fn(), onApplyDub: vi.fn(), onRevertDub: vi.fn(),
};

beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear();
    useProjectStore.setState({ currentProject: null, projects: [], readWatermarks: {}, runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('p', enriched(ack([frame('f1')], 'p', 4), 1), true);
    vi.mocked(api.getDialogueAudioState).mockResolvedValue({ project_id: 'p', frame_id: 'f1',
        read_versions: { project: 4, series: null, library: 1 }, options: {}, freshness: 'current',
        voice_id: 'voice', dialogue: 'line', audio_url: 'audio/line.mp3' });
});

async function open() {
    fireEvent.click(screen.getByRole('button', { name: /对白音频/ }));
    await waitFor(() => expect(api.getDialogueAudioState).toHaveBeenCalled());
}

it('keeps unverified historical dub URLs out of the current player and apply action', async () => {
    const view = renderWithIntl(<DialogueAudioRow {...props} />);
    await open();
    expect(view.container.querySelector('video')).toHaveAttribute('src', expect.stringContaining('video/A.mp4'));
    expect(screen.queryByRole('button', { name: '应用覆盖' })).not.toBeInTheDocument();
    expect(screen.queryByText('已覆盖')).not.toBeInTheDocument();
});

it('uses the preview artifact offset and requires regeneration after changing it', async () => {
    const view = renderWithIntl(<DialogueAudioRow {...{ ...props, dubPreviewSource: source,
        dubAppliedSource: { ...source, offset_ms: 25 } }} />);
    await open();
    expect(view.container.querySelector('video')).toHaveAttribute('src', expect.stringContaining('video/preview.mp4'));
    const offset = screen.getByRole('spinbutton', { name: '音频位置' });
    expect(offset).toHaveValue(150);
    expect(screen.getByRole('button', { name: '应用覆盖' })).not.toBeDisabled();
    fireEvent.change(offset, { target: { value: '300' } });
    expect(screen.getByRole('button', { name: '应用覆盖' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '应用覆盖' }));
    expect(props.onApplyDub).not.toHaveBeenCalled();
});

it('does not preview or apply a dub whose audio source changed', async () => {
    const view = renderWithIntl(<DialogueAudioRow {...{ ...props, dubPreviewSource: source,
        dubAppliedSource: source, audioUrl: 'audio/new.mp3' }} />);
    await open();
    expect(view.container.querySelector('video')).toHaveAttribute('src', expect.stringContaining('video/A.mp4'));
    expect(screen.queryByRole('button', { name: '应用覆盖' })).not.toBeInTheDocument();
});


it('uses media_refs to verify signed preview URLs without stripping signatures', async () => {
    const view = renderWithIntl(<DialogueAudioRow {...{
        ...props,
        audioUrl: 'https://signed.invalid/lumenx/audio/line.mp3?Expires=1',
        videoUrl: 'https://signed.invalid/lumenx/video/A.mp4?Expires=1',
        previewVideoUrl: 'https://signed.invalid/lumenx/video/preview.mp4?Expires=1',
        frameMediaRefs: { audio_url: 'audio/line.mp3' },
        videoTaskMediaRefs: { video_url: 'video/A.mp4' },
        dubPreviewSource: source,
    }} />);
    await open();
    expect(view.container.querySelector('video')).toHaveAttribute('src', expect.stringContaining('signed.invalid/lumenx/video/preview.mp4'));
});

it('treats signed preview as stale when media_refs are absent', async () => {
    const view = renderWithIntl(<DialogueAudioRow {...{
        ...props,
        audioUrl: 'https://signed.invalid/lumenx/audio/line.mp3?Expires=1',
        videoUrl: 'https://signed.invalid/lumenx/video/A.mp4?Expires=1',
        previewVideoUrl: 'https://signed.invalid/lumenx/video/preview.mp4?Expires=1',
        dubPreviewSource: source,
    }} />);
    await open();
    expect(view.container.querySelector('video')).toHaveAttribute('src', expect.stringContaining('signed.invalid/lumenx/video/A.mp4'));
    expect(screen.queryByRole('button', { name: '应用覆盖' })).not.toBeInTheDocument();
});

it('blocks duplicate preview generation across close and reopen and drops late feedback', async () => {
    const pending = deferred();
    props.onPreviewDub.mockReturnValue(pending.promise);
    renderWithIntl(<DialogueAudioRow {...{ ...props, previewVideoUrl: undefined, dubbedVideoUrl: undefined }} />);
    await open();
    fireEvent.click(screen.getByRole('button', { name: '预听' }));
    await waitFor(() => expect(props.onPreviewDub).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    await open();
    expect(screen.getByRole('button', { name: '预听' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '预听' }));
    expect(props.onPreviewDub).toHaveBeenCalledOnce();
    await act(async () => pending.reject(new Error('late old failure')));
    expect(screen.queryByText('late old failure')).not.toBeInTheDocument();
});
