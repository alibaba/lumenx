import { describe, expect, it } from 'vitest';

import {
    buildSeedancePayloadPreview,
    buildSeedancePayloadPreviews,
    getSeedanceLowCostPreset,
    getSeedanceSubmissionState,
} from '@/lib/seedance';

describe('buildSeedancePayloadPreview', () => {
    it('标准工作流不会带 workflow_mode', () => {
        const payload = buildSeedancePayloadPreview({
            prompt: 'cinematic close-up',
            model: 'doubao-seedance-2-0-260128',
            duration: 5,
            resolution: '480p',
            aspectRatio: '16:9',
            watermark: true,
            cameraFixed: false,
            generateAudio: false,
            referenceMode: 'image',
            workflow: 'standard',
            extendMode: 'continue',
            editMode: 'subject_replace',
            imageUrls: ['https://example.com/hero.png'],
            referenceVideoUrls: ['https://example.com/ref.mp4'],
            referenceAudioUrl: 'https://example.com/ref.wav',
        });

        expect(payload.reference_mode).toBe('image');
        expect(payload.workflow).toBe('standard');
        expect(payload).not.toHaveProperty('workflow_mode');
        expect(payload.content).toEqual([
            { type: 'text', text: 'cinematic close-up' },
            {
                type: 'image_url',
                image_url: { url: 'https://example.com/hero.png' },
            },
        ]);
    });
});

describe('buildSeedancePayloadPreviews', () => {
    it('多图组合参考会展开成多份 payload，并保留视频/音频参考', () => {
        const previews = buildSeedancePayloadPreviews({
            prompt: 'hero keeps running through neon rain',
            model: 'doubao-seedance-2-0-260128',
            duration: 5,
            resolution: '480p',
            aspectRatio: '9:16',
            watermark: true,
            cameraFixed: true,
            generateAudio: false,
            seed: 42,
            referenceMode: 'combo',
            workflow: 'edit',
            extendMode: 'continue',
            editMode: 'object_edit',
            imageUrls: [
                'https://example.com/frame-1.png',
                'https://example.com/frame-2.png',
            ],
            referenceVideoUrls: ['https://example.com/source.mp4'],
            referenceAudioUrl: 'https://example.com/guide.wav',
        });

        expect(previews).toHaveLength(2);
        expect(previews[0].label).toBe('任务 1/2');
        expect(previews[1].label).toBe('任务 2/2');

        const firstPayload = previews[0].payload;
        const secondPayload = previews[1].payload;

        expect(firstPayload.workflow_mode).toBe('object_edit');
        expect(secondPayload.workflow_mode).toBe('object_edit');
        expect(firstPayload.content).toEqual([
            { type: 'text', text: 'hero keeps running through neon rain' },
            {
                type: 'image_url',
                image_url: { url: 'https://example.com/frame-1.png' },
            },
            {
                type: 'video_url',
                video_url: { url: 'https://example.com/source.mp4' },
            },
            {
                type: 'audio_url',
                audio_url: { url: 'https://example.com/guide.wav' },
            },
        ]);
        expect(secondPayload.content).toEqual([
            { type: 'text', text: 'hero keeps running through neon rain' },
            {
                type: 'image_url',
                image_url: { url: 'https://example.com/frame-2.png' },
            },
            {
                type: 'video_url',
                video_url: { url: 'https://example.com/source.mp4' },
            },
            {
                type: 'audio_url',
                audio_url: { url: 'https://example.com/guide.wav' },
            },
        ]);
    });

    it('视频参考模式会忽略图片和音频，只保留单份视频 payload', () => {
        const previews = buildSeedancePayloadPreviews({
            prompt: 'continue the action',
            model: 'doubao-seedance-2-0-260128',
            duration: 5,
            resolution: '720p',
            aspectRatio: '16:9',
            watermark: false,
            cameraFixed: false,
            generateAudio: true,
            referenceMode: 'video',
            workflow: 'extend',
            extendMode: 'prepend',
            editMode: 'subject_replace',
            imageUrls: ['https://example.com/ignored.png'],
            referenceVideoUrls: ['https://example.com/source.mp4'],
            referenceAudioUrl: 'https://example.com/ignored.wav',
        });

        expect(previews).toHaveLength(1);
        expect(previews[0].payload.content).toEqual([
            { type: 'text', text: 'continue the action' },
            {
                type: 'video_url',
                video_url: { url: 'https://example.com/source.mp4' },
            },
        ]);
    });
});

describe('getSeedanceSubmissionState', () => {
    it('手动开启仅预览时，优先锁到 preview', () => {
        const state = getSeedanceSubmissionState({
            previewOnly: true,
            workflow: 'standard',
            referenceMode: 'image',
            imageUrls: ['https://example.com/hero.png'],
            referenceVideoUrls: [],
            referenceAudioUrl: '',
        });

        expect(state).toEqual({
            mode: 'preview',
            reason: 'manual_preview',
        });
    });

    it('非标准工作流缺少有效参考视频时会自动锁预览', () => {
        const state = getSeedanceSubmissionState({
            previewOnly: false,
            workflow: 'edit',
            referenceMode: 'image',
            imageUrls: ['https://example.com/hero.png'],
            referenceVideoUrls: ['https://example.com/ignored.mp4'],
            referenceAudioUrl: '',
        });

        expect(state).toEqual({
            mode: 'preview',
            reason: 'workflow_missing_video',
        });
    });

    it('非标准工作流在视频参考就绪后可以真实提交', () => {
        const state = getSeedanceSubmissionState({
            previewOnly: false,
            workflow: 'extend',
            referenceMode: 'video',
            imageUrls: [],
            referenceVideoUrls: ['https://example.com/source.mp4'],
            referenceAudioUrl: '',
        });

        expect(state).toEqual({
            mode: 'submit',
            reason: 'ready',
        });
    });
});

describe('getSeedanceLowCostPreset', () => {
    it('返回最低成本测试组合', () => {
        expect(getSeedanceLowCostPreset()).toEqual({
            duration: 5,
            resolution: '480p',
            batchSize: 1,
            generateAudio: false,
            aspectRatio: 'adaptive',
            watermark: true,
            cameraFixed: false,
            seedanceWorkflow: 'standard',
            seedanceReferenceMode: 'image',
        });
    });
});
