import { describe, expect, it } from 'vitest';
import { frameToShotNode } from '@/components/modules/storyboard-r2v/shotNodeHelpers';

const task = (id: string, frameId = 'f1', videoUrl = `/video/${id}.mp4`) => ({
    id,
    frame_id: frameId,
    video_url: videoUrl,
    status: 'completed',
});

const source = (taskId = 'a', videoUrl = '/video/a.mp4', audioUrl = '/audio/f1.mp3', offsetMs = 120) => ({
    schema_version: 1,
    task_id: taskId,
    video_url: videoUrl,
    audio_url: audioUrl,
    offset_ms: offsetMs,
});

describe('frameToShotNode dub source mapping', () => {
    it('does not promote legacy dubbed_video_url without matching applied source proof', () => {
        const shot = frameToShotNode({
            id: 'f1',
            selected_video_id: 'b',
            audio_url: '/audio/f1.mp3',
            dubbed_video_url: '/dubbed/old-a.mp4',
            dub_applied_source: source('a'),
            dub_offset_ms: 120,
        }, [task('a'), task('b')]);
        expect(shot.videoUrl).toBe('/video/b.mp4');
        expect(shot.videoTaskId).toBe('b');
        expect(shot.videoStatus).toBe('completed');
    });

    it('maps verified applied source to the shot hero URL', () => {
        const shot = frameToShotNode({
            id: 'f1',
            selected_video_id: 'a',
            audio_url: '/audio/f1.mp3',
            dubbed_video_url: '/dubbed/a.mp4',
            dub_applied_source: source('a'),
            dub_offset_ms: 120,
        }, [task('a')]);
        expect(shot.videoUrl).toBe('/dubbed/a.mp4');
        expect(shot.videoTaskId).toBe('a');
    });

    it('does not map verified preview into shot hero because assembly/current display excludes preview', () => {
        const shot = frameToShotNode({
            id: 'f1',
            audio_url: '/audio/f1.mp3',
            preview_video_url: '/preview/a.mp4',
            dub_preview_source: source('a'),
        }, [task('a')]);
        expect(shot.videoUrl).toBe('/video/a.mp4');
    });

    it('does not silently fall back when selected_video_id points to a deleted task', () => {
        const shot = frameToShotNode({
            id: 'f1',
            selected_video_id: 'deleted',
            video_url: '/legacy/frame.mp4',
            dubbed_video_url: '/dubbed/a.mp4',
        }, [task('a')]);
        expect(shot.videoUrl).toBeUndefined();
        expect(shot.videoStatus).toBeUndefined();
        expect(shot.videoTaskId).toBeUndefined();
    });
});
