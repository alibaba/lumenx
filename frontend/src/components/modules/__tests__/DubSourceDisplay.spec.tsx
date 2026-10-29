import { fireEvent, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CandidateThumb from '../storyboard-r2v/shot-panel/CandidateThumb';
import CandidatesSection from '../storyboard-r2v/shot-panel/CandidatesSection';
import VideoAssembly from '../VideoAssembly';
import { useProjectStore, type Project } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { selectVerifiedAppliedDubUrlForTask } from '@/lib/dubSource';

vi.mock('@/components/shared/preview/PreviewVideo', () => ({
    default: ({ src, alt }: { src?: string; alt?: string }) => <video data-testid="preview-video" src={src} aria-label={alt} />,
}));

const task = {
    id: 'task-a',
    project_id: 'p1',
    image_url: '',
    prompt: 'prompt',
    status: 'completed' as const,
    video_url: '/raw/a.mp4',
    duration: 5,
    resolution: '720p',
    generate_audio: false,
    prompt_extend: false,
    created_at: 1,
    frame_id: 'f1',
};

const noop = vi.fn();

const source = (taskId = 'a', videoUrl = '/video/a.mp4', audioUrl = '/audio/f1.mp3', offsetMs = 120) => ({
    schema_version: 1 as const,
    task_id: taskId,
    video_url: videoUrl,
    audio_url: audioUrl,
    offset_ms: offsetMs,
});

const project = (frame: Record<string, unknown>): Project => ({
    id: 'p1',
    revision: 1,
    title: 'Project',
    originalText: 'script',
    characters: [],
    scenes: [],
    props: [],
    status: 'draft',
    createdAt: 'old',
    updatedAt: 'old',
    frames: [{ id: 'f1', scene_id: 's1', ...frame }],
    video_tasks: [
        { ...task, id: 'a', video_url: '/video/a.mp4', frame_id: 'f1' },
        { ...task, id: 'b', video_url: '/video/b.mp4', frame_id: 'f1' },
    ],
});

describe('dub source-safe display components', () => {
    beforeEach(() => {
        useProjectStore.setState({ currentProject: null, projects: [] });
    });

    it('CandidateThumb ignores legacy dubbed URL unless caller supplies a verified display URL', () => {
        renderWithIntl(<CandidateThumb
            task={task}
            isCompareSelected={false}
            dubbedVideoUrl="/legacy/dubbed.mp4"
            onClick={noop}
            onToggleStar={noop}
            onSetLabel={noop}
        />);
        expect(screen.getByTestId('preview-video')).toHaveAttribute('src', '/raw/a.mp4');
    });

    it('CandidateThumb renders verified display URL when source helper selected it', () => {
        renderWithIntl(<CandidateThumb
            task={task}
            isCompareSelected={false}
            displayVideoUrl="/verified/dubbed.mp4"
            onClick={noop}
            onToggleStar={noop}
            onSetLabel={noop}
        />);
        expect(screen.getByTestId('preview-video')).toHaveAttribute('src', '/verified/dubbed.mp4');
    });

    it('CandidateThumb keeps needs-confirmation tasks read-only and unpinnable', () => {
        const onClick = vi.fn();
        const onSetActive = vi.fn();
        const onRetry = vi.fn();
        renderWithIntl(<CandidateThumb
            task={{ ...task, status: 'needs_confirmation' as const, video_url: '/historical.mp4', provider_task_id: 'provider-task-1' }}
            isCompareSelected={false}
            onClick={onClick}
            onToggleStar={noop}
            onSetLabel={noop}
            onSetActive={onSetActive}
            onRetry={onRetry}
        />);

        expect(screen.getByText('待确认')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Set as active take/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Retry/i })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /needs_confirmation/i }));
        expect(onClick).not.toHaveBeenCalled();
        expect(onSetActive).not.toHaveBeenCalled();
        expect(onRetry).not.toHaveBeenCalled();
    });

    it('CandidatesSection excludes needs-confirmation tasks from compare and starred action surfaces', () => {
        const onToggleStar = vi.fn();
        renderWithIntl(<CandidatesSection
            shotId="f1"
            tasks={[{ ...task, status: 'needs_confirmation' as const, id: 'recover', video_url: '/historical.mp4', is_starred: true }]}
            activeModel="wan"
            compareSelectedIds={new Set(['recover'])}
            activeTaskId="recover"
            onClickThumb={noop}
            onToggleStar={onToggleStar}
            onSetLabel={noop}
            onSetActive={noop}
        />);

        expect(screen.queryByText('已选 1 个候选对比')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /needs_confirmation/i }));
        expect(onToggleStar).not.toHaveBeenCalled();
    });

    it('VideoAssembly falls back to the selected original when stored applied dub proof belongs to another task', () => {
        useProjectStore.setState({
            currentProject: project({
                selected_video_id: 'b',
                audio_url: '/audio/f1.mp3',
                dubbed_video_url: '/dubbed/old-a.mp4',
                dub_applied_source: source('a'),
                dub_offset_ms: 120,
            }),
        });
        const { container } = renderWithIntl(<VideoAssembly />);
        expect(container.querySelector('video')?.getAttribute('src')).toContain('video/b.mp4');
        expect(container.querySelector('video')?.getAttribute('src')).not.toContain('dubbed');
    });

    it('VideoAssembly keeps signed display URL while matching raw media_refs proof', () => {
        useProjectStore.setState({
            currentProject: project({
                selected_video_id: 'a',
                audio_url: 'https://signed.invalid/lumenx/audio/f1.mp3?Expires=1',
                media_refs: { audio_url: '/audio/f1.mp3' },
                dubbed_video_url: 'https://signed.invalid/lumenx/dubbed/a.mp4?Expires=1',
                dub_applied_source: source('a', '/video/a.mp4', '/audio/f1.mp3'),
                dub_offset_ms: 120,
            }),
        });
        useProjectStore.setState(state => ({
            currentProject: state.currentProject ? {
                ...state.currentProject,
                video_tasks: state.currentProject.video_tasks?.map(task => task.id === 'a'
                    ? { ...task, video_url: 'https://signed.invalid/lumenx/video/a.mp4?Expires=1', media_refs: { video_url: '/video/a.mp4' } }
                    : task),
            } : null,
        }));
        const { container } = renderWithIntl(<VideoAssembly />);
        expect(container.querySelector('video')?.getAttribute('src')).toContain('signed.invalid/lumenx/dubbed/a.mp4');
    });

    it('CandidatesSection passes frame refs and task refs into source proof before rendering dub display', () => {
        expect(selectVerifiedAppliedDubUrlForTask({
            id: 'f1', audio_url: 'https://signed.invalid/lumenx/audio/f1.mp3?Expires=1', media_refs: { audio_url: '/audio/f1.mp3' },
            dubbed_video_url: 'https://signed.invalid/lumenx/dubbed/a.mp4?Expires=1', dub_applied_source: source('a', '/video/a.mp4', '/audio/f1.mp3'),
        }, { ...task, id: 'a', video_url: 'https://signed.invalid/lumenx/video/a.mp4?Expires=1', media_refs: { video_url: '/video/a.mp4' } })).toBe('https://signed.invalid/lumenx/dubbed/a.mp4?Expires=1');
        renderWithIntl(<CandidatesSection
            shotId="f1"
            tasks={[{ ...task, id: 'a', video_url: 'https://signed.invalid/lumenx/video/a.mp4?Expires=1', media_refs: { video_url: '/video/a.mp4' } }]}
            activeModel="wan"
            compareSelectedIds={new Set()}
            activeTaskId="a"
            dubbedVideoUrl="https://signed.invalid/lumenx/dubbed/a.mp4?Expires=1"
            dubbedVideoTaskId="a"
            frameAudioUrl="https://signed.invalid/lumenx/audio/f1.mp3?Expires=1"
            frameMediaRefs={{ audio_url: '/audio/f1.mp3' }}
            dubAppliedSource={source('a', '/video/a.mp4', '/audio/f1.mp3')}
            onClickThumb={noop}
            onToggleStar={noop}
            onSetLabel={noop}
        />);
        expect(screen.getByTestId('preview-video')).toHaveAttribute('src', 'https://signed.invalid/lumenx/dubbed/a.mp4?Expires=1');
    });

    it('CandidatesSection rejects signed dub display without raw media_refs proof', () => {
        renderWithIntl(<CandidatesSection
            shotId="f1"
            tasks={[{ ...task, id: 'a', video_url: 'https://signed.invalid/lumenx/video/a.mp4?Expires=1' }]}
            activeModel="wan"
            compareSelectedIds={new Set()}
            activeTaskId="a"
            dubbedVideoUrl="https://signed.invalid/lumenx/dubbed/a.mp4?Expires=1"
            dubbedVideoTaskId="a"
            frameAudioUrl="https://signed.invalid/lumenx/audio/f1.mp3?Expires=1"
            dubAppliedSource={source('a', '/video/a.mp4', '/audio/f1.mp3')}
            onClickThumb={noop}
            onToggleStar={noop}
            onSetLabel={noop}
        />);
        expect(screen.getByTestId('preview-video')).toHaveAttribute('src', 'https://signed.invalid/lumenx/video/a.mp4?Expires=1');
    });

    it('VideoAssembly displays applied dub only when proof matches the selected task inputs', () => {
        useProjectStore.setState({
            currentProject: project({
                selected_video_id: 'a',
                audio_url: '/audio/f1.mp3',
                dubbed_video_url: '/dubbed/a.mp4',
                dub_applied_source: source('a'),
                dub_offset_ms: 120,
            }),
        });
        const { container } = renderWithIntl(<VideoAssembly />);
        expect(container.querySelector('video')?.getAttribute('src')).toContain('dubbed/a.mp4');
    });
});
