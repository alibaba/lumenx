import { describe, expect, it } from 'vitest';

import {
    insertStoryboardR2VTag,
    resolveStoryboardR2VRefs,
    stripStoryboardR2VTags,
    validateStoryboardR2VRefs,
} from '@/lib/storyboardR2VAssets';

describe('storyboard R2V asset reference helpers', () => {
    it('strips asset tags from prompt text without collapsing surrounding content', () => {
        expect(
            stripStoryboardR2VTags(
                'A slow push-in on [character1:Mei] while [scene:Rain Alley] glows behind her.'
            )
        ).toBe('A slow push-in on Mei while Rain Alley glows behind her.');
    });

    it('inserts asset tags with word boundaries around adjacent prompt text', () => {
        expect(insertStoryboardR2VTag('Mei walks', '[scene:Rain Alley]', 3, 3)).toEqual({
            prompt: 'Mei [scene:Rain Alley] walks',
            cursor: 22,
        });

        expect(insertStoryboardR2VTag('', '[character1:Mei]', 0, 0)).toEqual({
            prompt: '[character1:Mei]',
            cursor: 16,
        });
    });

    it('resolves selected motion references for video-based R2V and image refs for image-based R2V', () => {
        const refs = resolveStoryboardR2VRefs(
            '[character1:Mei] turns toward [scene:Rain Alley] holding [prop:Umbrella]',
            [
                {
                    name: 'Mei',
                    full_body: {
                        selected_image_id: 'img-2',
                        image_variants: [
                            { id: 'img-1', url: 'mei-image-1.png' },
                            { id: 'img-2', url: 'mei-image-2.png' },
                        ],
                        selected_video_id: 'vid-2',
                        video_variants: [
                            { id: 'vid-1', url: 'mei-video-1.mp4' },
                            { id: 'vid-2', url: 'mei-video-2.mp4' },
                        ],
                    },
                },
            ],
            [
                {
                    name: 'Rain Alley',
                    image_asset: {
                        variants: [{ id: 'scene-img', url: 'rain-alley.png' }],
                    },
                    video_assets: [
                        { id: 'scene-video', status: 'completed', video_url: 'rain-alley.mp4' },
                    ],
                },
            ],
            [
                {
                    name: 'Umbrella',
                    image_asset: {
                        selected_id: 'umbrella-img',
                        variants: [{ id: 'umbrella-img', url: 'umbrella.png' }],
                    },
                    video_assets: [
                        { id: 'umbrella-draft', status: 'processing', video_url: 'ignore-processing.mp4' },
                        { id: 'umbrella-ready', status: 'completed', video_url: 'umbrella.mp4' },
                    ],
                },
            ]
        );

        expect(refs.imageUrls).toEqual(['mei-image-2.png', 'rain-alley.png', 'umbrella.png']);
        expect(refs.videoUrls).toEqual(['mei-video-2.mp4', 'rain-alley.mp4', 'umbrella.mp4']);
        expect(refs.missing).toEqual([]);
    });

    it('deduplicates refs and reports missing tagged assets', () => {
        const refs = resolveStoryboardR2VRefs(
            '[character1:Mei] [character2:Mei] [scene:Missing]',
            [
                {
                    name: 'Mei',
                    full_body_asset: {
                        selected_id: 'img-1',
                        variants: [{ id: 'img-1', url: 'mei.png' }],
                    },
                    video_assets: [
                        { id: 'video-1', status: 'completed', video_url: 'mei.mp4' },
                    ],
                },
            ],
            [],
            []
        );

        expect(refs.imageUrls).toEqual(['mei.png']);
        expect(refs.videoUrls).toEqual(['mei.mp4']);
        expect(refs.missing).toEqual(['scene:Missing']);
    });

    it('blocks image-based R2V when tagged refs resolve to no image urls', () => {
        const refs = resolveStoryboardR2VRefs(
            '[character1:Mei]',
            [
                {
                    name: 'Mei',
                    full_body: {
                        selected_video_id: 'vid-1',
                        video_variants: [{ id: 'vid-1', url: 'mei.mp4' }],
                    },
                },
            ],
            [],
            []
        );

        const validation = validateStoryboardR2VRefs(refs, 'image');

        expect(validation.requiredUrls).toEqual([]);
        expect(validation.canGenerate).toBe(false);
        expect(validation.issues).toEqual([{ type: 'missing_image_refs' }]);
    });

    it('blocks video-based R2V when tagged refs resolve to no motion reference videos', () => {
        const refs = resolveStoryboardR2VRefs(
            '[character1:Mei]',
            [
                {
                    name: 'Mei',
                    full_body: {
                        selected_image_id: 'img-1',
                        image_variants: [{ id: 'img-1', url: 'mei.png' }],
                    },
                },
            ],
            [],
            []
        );

        const validation = validateStoryboardR2VRefs(refs, 'video');

        expect(validation.requiredUrls).toEqual([]);
        expect(validation.canGenerate).toBe(false);
        expect(validation.issues).toEqual([{ type: 'missing_video_refs' }]);
    });

    it('reports missing asset tags before allowing either R2V reference mode', () => {
        const refs = resolveStoryboardR2VRefs('[scene:Missing]', [], [], []);

        expect(validateStoryboardR2VRefs(refs, 'image')).toEqual({
            mode: 'image',
            requiredUrls: [],
            canGenerate: false,
            issues: [
                { type: 'missing_assets', refs: ['scene:Missing'] },
                { type: 'missing_image_refs' },
            ],
        });
    });

    it('blocks R2V references above the concrete model limit', () => {
        const validation = validateStoryboardR2VRefs(
            {
                imageUrls: ['a.png', 'b.png', 'c.png'],
                videoUrls: [],
                missing: [],
            },
            'image',
            2
        );

        expect(validation.canGenerate).toBe(false);
        expect(validation.issues).toEqual([{ type: 'too_many_image_refs', count: 3, max: 2 }]);
    });
});
