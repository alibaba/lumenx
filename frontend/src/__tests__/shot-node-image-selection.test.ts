import { describe, expect, it } from 'vitest';
import { frameToShotNode, getActiveT2IImageUrl } from '@/components/modules/storyboard-r2v/shotNodeHelpers';

describe('committed frame first-image projection', () => {
    it('renders an existing saved image when no workbench history exists', () => {
        const node = frameToShotNode({ id: 'f', rendered_image_url: 'selected.png', image_url: 'old.png' }, [], 't2i_i2v');
        expect(node.t2iImageUrl).toBe('selected.png');
        expect(getActiveT2IImageUrl(node)).toBe('selected.png');
        expect(node.t2iImageUrls).toEqual([]);
    });

    it('keeps the selected workbench image even when another render exists', () => {
        const node = frameToShotNode({ id: 'f', rendered_image_url: 'another.png', t2i_image_urls: ['one.png', 'two.png'], t2i_selected_index: 1 }, [], 't2i_i2v');
        expect(node.t2iImageUrl).toBe('two.png');
        expect(getActiveT2IImageUrl(node)).toBe('two.png');
    });

    it('clamps historical selected indices to the same image used for generation', () => {
        const node = frameToShotNode({ id: 'f', t2i_image_urls: ['one.png', 'two.png'], t2i_selected_index: 100 }, [], 't2i_i2v');
        expect(node.t2iImageUrl).toBe(getActiveT2IImageUrl(node));
        expect(node.t2iImageUrl).toBe('two.png');
    });

    it('preserves a legacy frame video as completed when no video task competes', () => {
        const node = frameToShotNode({ id: 'f', video_url: '/legacy.mp4' }, [], 'direct_r2v');

        expect(node.videoStatus).toBe('completed');
        expect(node.videoTaskId).toBeUndefined();
        expect(node.videoUrl).toBe('/legacy.mp4');
    });

    it('projects recovered video tasks with media as unconfirmed history instead of completed output', () => {
        const node = frameToShotNode({ id: 'f', video_url: '/old-selected.mp4' }, [{
            id: 'recover-task',
            frame_id: 'f',
            status: 'needs_confirmation',
            video_url: '/historical.mp4',
        }], 'direct_r2v');

        expect(node.videoStatus).toBe('needs_confirmation');
        expect(node.videoTaskId).toBe('recover-task');
        expect(node.videoUrl).toBe('/historical.mp4');
    });

    it('keeps a valid selected completed video when another same-frame task needs confirmation', () => {
        const node = frameToShotNode({
            id: 'f',
            selected_video_id: 'selected-task',
        }, [{
            id: 'selected-task',
            frame_id: 'f',
            status: 'completed',
            video_url: '/selected.mp4',
        }, {
            id: 'recover-task',
            frame_id: 'f',
            status: 'needs_confirmation',
            video_url: '/historical.mp4',
        }], 'direct_r2v');

        expect(node.videoStatus).toBe('completed');
        expect(node.videoTaskId).toBe('selected-task');
        expect(node.videoUrl).toBe('/selected.mp4');
    });

    it('does not borrow another completed video when the explicit selected task needs confirmation', () => {
        const node = frameToShotNode({
            id: 'f',
            selected_video_id: 'recover-task',
        }, [{
            id: 'completed-task',
            frame_id: 'f',
            status: 'completed',
            video_url: '/completed.mp4',
        }, {
            id: 'recover-task',
            frame_id: 'f',
            status: 'needs_confirmation',
            video_url: '/historical.mp4',
        }], 'direct_r2v');

        expect(node.videoStatus).toBe('needs_confirmation');
        expect(node.videoTaskId).toBe('recover-task');
        expect(node.videoUrl).toBe('/historical.mp4');
    });
});
