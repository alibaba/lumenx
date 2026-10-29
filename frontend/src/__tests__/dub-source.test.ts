import { describe, expect, it } from 'vitest';
import {
    normalizeDubSource,
    selectAppliedDubForTask,
    selectCurrentVideoTask,
    selectFrameAssemblyVideo,
    selectPreviewDubForTask,
} from '@/lib/dubSource';

const task = (id: string, frameId = 'f1', videoUrl = `/video/${id}.mp4`, status = 'completed') => ({
    id,
    frame_id: frameId,
    video_url: videoUrl,
    status,
});

const source = (taskId = 'a', videoUrl = '/video/a.mp4', audioUrl = '/audio/f1.mp3', offsetMs = 120) => ({
    schema_version: 1,
    task_id: taskId,
    video_url: videoUrl,
    audio_url: audioUrl,
    offset_ms: offsetMs,
});

describe('dub source proof helpers', () => {
    it('accepts only schema v1 proof with strict scalar fields', () => {
        expect(normalizeDubSource(source())).toEqual(source());
        expect(normalizeDubSource({ ...source(), schema_version: '1' })).toBeNull();
        expect(normalizeDubSource({ ...source(), task_id: '' })).toBeNull();
        expect(normalizeDubSource({ ...source(), offset_ms: '120' })).toBeNull();
        expect(normalizeDubSource({ ...source(), offset_ms: Number.NaN })).toBeNull();
        expect(normalizeDubSource({ ...source(), offset_ms: -1 })).toBeNull();
        expect(normalizeDubSource({ ...source(), offset_ms: 1.5 })).toBeNull();
        expect(normalizeDubSource({ ...source(), extra: true })).toBeNull();
        expect(normalizeDubSource([source()])).toBeNull();
    });

    it('uses the explicit selected task only when it is completed, same-frame, and has a URL', () => {
        const tasks = [task('a'), task('b')];
        expect(selectCurrentVideoTask({ id: 'f1', selected_video_id: 'b' }, tasks)).toMatchObject({
            kind: 'selected',
            task: tasks[1],
        });
        expect(selectCurrentVideoTask({ id: 'f1', selected_video_id: 'missing' }, tasks)).toMatchObject({
            kind: 'selected-mismatch',
            task: null,
        });
        expect(selectCurrentVideoTask({ id: 'f1', selected_video_id: 'x' }, [task('x', 'other')])).toMatchObject({
            kind: 'selected-mismatch',
            task: null,
        });
        expect(selectCurrentVideoTask({ id: 'f1' }, tasks)).toMatchObject({
            kind: 'default',
            task: tasks[0],
        });
    });

    it('does not treat a stored dubbed URL as current assembly media without matching applied source proof', () => {
        const frame = {
            id: 'f1',
            selected_video_id: 'b',
            audio_url: '/audio/f1.mp3',
            dubbed_video_url: '/dubbed/old-a.mp4',
            dub_applied_source: source('a'),
            dub_offset_ms: 120,
        };
        const result = selectFrameAssemblyVideo(frame, [task('a'), task('b')]);
        expect(result).toMatchObject({ kind: 'original', taskId: 'b', url: '/video/b.mp4' });
        expect(result.history?.[0]).toMatchObject({ kind: 'stored-history', url: '/dubbed/old-a.mp4' });
    });

    it('requires applied source to match task, frame, source video, and audio; source offset is authoritative', () => {
        const frame = {
            id: 'f1',
            audio_url: '/audio/f1.mp3',
            dubbed_video_url: '/dubbed/a.mp4',
            dub_applied_source: source(),
            dub_offset_ms: 120,
        };
        expect(selectAppliedDubForTask(frame, task('a'))).toMatchObject({ kind: 'current', url: '/dubbed/a.mp4' });
        const changedLegacyOffset = { ...frame, dub_offset_ms: 121 };
        expect(selectAppliedDubForTask(changedLegacyOffset, task('a'))).toMatchObject({ kind: 'current', url: '/dubbed/a.mp4' });
        expect(selectAppliedDubForTask({ ...frame, audio_url: '/audio/new.mp3' }, task('a'))).toMatchObject({ kind: 'stale' });
        expect(selectAppliedDubForTask(frame, task('a', 'other'))).toMatchObject({ kind: 'stale' });
        expect(selectAppliedDubForTask(frame, task('a', 'f1', '/video/replaced.mp4'))).toMatchObject({ kind: 'stale' });
    });

    it('validates preview proof independently from the applied offset compatibility field', () => {
        const frame = {
            id: 'f1',
            audio_url: '/audio/f1.mp3',
            preview_video_url: '/preview/a.mp4',
            dub_preview_source: source('a', '/video/a.mp4', '/audio/f1.mp3', 777),
            dub_offset_ms: 120,
        };
        expect(selectPreviewDubForTask(frame, task('a'))).toMatchObject({ kind: 'current', url: '/preview/a.mp4' });
        expect(selectPreviewDubForTask({ ...frame, audio_url: '/audio/new.mp3' }, task('a'))).toMatchObject({ kind: 'stale' });
    });


    it('uses raw media_refs for signed display URLs without stripping query strings', () => {
        const signedTask = {
            ...task('a', 'f1', 'https://signed.invalid/lumenx/video/a.mp4?Expires=1'),
            media_refs: { video_url: '/video/a.mp4' },
        };
        const frame = {
            id: 'f1',
            audio_url: 'https://signed.invalid/lumenx/audio/f1.mp3?Expires=1',
            media_refs: { audio_url: '/audio/f1.mp3' },
            dubbed_video_url: 'https://signed.invalid/lumenx/dubbed/a.mp4?Expires=1',
            dub_applied_source: source('a', '/video/a.mp4', '/audio/f1.mp3'),
        };
        expect(selectAppliedDubForTask(frame, signedTask)).toMatchObject({
            kind: 'current',
            url: 'https://signed.invalid/lumenx/dubbed/a.mp4?Expires=1',
        });

        const refreshedSignature = { ...signedTask, video_url: 'https://signed.invalid/lumenx/video/a.mp4?Expires=2&Signature=changed' };
        expect(selectAppliedDubForTask(frame, refreshedSignature)).toMatchObject({ kind: 'current' });
        expect(selectAppliedDubForTask(frame, { ...signedTask, media_refs: { video_url: '/video/replaced.mp4' } })).toMatchObject({ kind: 'stale' });
        expect(selectAppliedDubForTask({ ...frame, media_refs: { audio_url: '/audio/replaced.mp3' } }, signedTask)).toMatchObject({ kind: 'stale' });
    });

    it('does not prove signed media with absent or malformed media_refs', () => {
        const signedTask = task('a', 'f1', 'https://signed.invalid/lumenx/video/a.mp4?Expires=1');
        const frame = {
            id: 'f1',
            audio_url: 'https://signed.invalid/lumenx/audio/f1.mp3?Expires=1',
            dubbed_video_url: 'https://signed.invalid/lumenx/dubbed/a.mp4?Expires=1',
            dub_applied_source: source('a', '/video/a.mp4', '/audio/f1.mp3'),
        };
        expect(selectAppliedDubForTask(frame, signedTask)).toMatchObject({ kind: 'stale' });
        expect(selectAppliedDubForTask(
            { ...frame, media_refs: { audio_url: ['/audio/f1.mp3'] } },
            { ...signedTask, media_refs: { video_url: { raw: '/video/a.mp4' } } },
        )).toMatchObject({ kind: 'stale' });
    });


    it('fails closed when explicit media_refs fields are malformed even if display values equal the source', () => {
        const frame = {
            id: 'f1',
            audio_url: '/audio/f1.mp3',
            media_refs: { audio_url: null },
            dubbed_video_url: '/dubbed/a.mp4',
            dub_applied_source: source('a', '/video/a.mp4', '/audio/f1.mp3'),
        };
        expect(selectAppliedDubForTask(frame, { ...task('a'), media_refs: { video_url: 0 } })).toMatchObject({ kind: 'stale' });
        expect(selectAppliedDubForTask({ ...frame, media_refs: { audio_url: '' } }, { ...task('a'), media_refs: { video_url: '/video/a.mp4' } })).toMatchObject({ kind: 'stale' });
        expect(selectAppliedDubForTask({ ...frame, media_refs: { audio_url: { raw: '/audio/f1.mp3' } } }, { ...task('a'), media_refs: { video_url: '/video/a.mp4' } })).toMatchObject({ kind: 'stale' });
    });

    it('keeps verified preview out of assembly selection unless explicitly allowed', () => {
        const frame = {
            id: 'f1',
            audio_url: '/audio/f1.mp3',
            preview_video_url: '/preview/b.mp4',
            dub_preview_source: source('b', '/video/b.mp4', '/audio/f1.mp3', 88),
        };
        expect(selectFrameAssemblyVideo(frame, [task('b')])).toMatchObject({ kind: 'original', url: '/video/b.mp4' });
        expect(selectFrameAssemblyVideo(frame, [task('b')], { allowPreview: true })).toMatchObject({ kind: 'preview', url: '/preview/b.mp4' });
    });

    it('does not silently alternate to first completed task when selected id is invalid', () => {
        const frame = {
            id: 'f1',
            selected_video_id: 'deleted',
            audio_url: '/audio/f1.mp3',
            dubbed_video_url: '/dubbed/a.mp4',
            dub_applied_source: source('a'),
            dub_offset_ms: 120,
        };
        expect(selectFrameAssemblyVideo(frame, [task('a')])).toMatchObject({ kind: 'none', reason: 'selected-mismatch' });
    });
});
