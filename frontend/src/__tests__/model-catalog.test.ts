import rawCatalog from '@/generated/modelCatalog.json';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
    DEFAULT_MODEL_SETTINGS,
    GLOBAL_I2I_MODELS,
    GLOBAL_I2V_MODELS,
    GLOBAL_IMAGE_MODELS,
    GLOBAL_T2I_MODELS,
    PROJECT_I2V_MODELS,
    SERIES_I2V_MODELS,
    VIDEO_I2V_MODELS,
    VIDEO_R2V_MODELS,
    DEFAULT_R2V_MODEL_ID,
    R2V_ROUTE_MODEL_ID,
    R2V_SELECTION_MODEL_ID,
    getR2vRouteModelId,
    getCanonicalDefaults,
    getCanonicalModeEntry,
    getCanonicalModeId,
    getLegacyModelId,
    getMaxReferenceImages,
    getModelLineEntry,
    getModeGateway,
    resolveModelSettings,
} from '@/lib/modelCatalog';

type MockCatalog = Omit<typeof rawCatalog, 'model_lines' | 'modes' | 'compat'> & {
    model_lines?: Record<string, { id: string; family: string }>;
    modes?: Record<string, { id: string; model_line_id: string; mode: string }>;
    compat?: {
        legacy_model_ids?: Record<string, string>;
    };
};

afterEach(() => {
    vi.doUnmock('@/generated/modelCatalog.json');
    vi.resetModules();
});

describe('model catalog selectors', () => {
    it('derives visible model selectors from catalog defaults', () => {
        // Defaults follow the catalog upgrade to wan2.7 (Phase 2, 2026-Q1).
        // The unified `image_model` surface replaces the per-mode t2i/i2i
        // settings at the consumer layer.
        expect(DEFAULT_MODEL_SETTINGS).toMatchObject({
            t2i_model: 'wan2.7-image-pro',
            i2i_model: 'wan2.7-image-pro',
            i2v_model: 'happyhorse-1.1-i2v',
            image_model: 'wan2.7-image-pro',
        });

        // The 't2i' and 'i2i' selection_group surfaces moved to 'image'
        // in Phase 2. The resolver now falls through to visible image-group
        // models so user picks (e.g. Wan 2.7 Image Pro) persist through
        // resolveModelId() instead of silently reverting to the default.
        expect(GLOBAL_T2I_MODELS.map((model) => model.id)).toEqual(GLOBAL_IMAGE_MODELS.map((m) => m.id));
        expect(GLOBAL_I2I_MODELS.map((model) => model.id)).toEqual(GLOBAL_IMAGE_MODELS.map((m) => m.id));

        // Ordered DESC by ui.order; ties broken by display_name asc.
        expect(GLOBAL_I2V_MODELS.map((model) => model.id)).toEqual([
            'happyhorse-1.1-i2v',
            'kling-v3-i2v',
            'pixverse/pixverse-v6-video',
            'seedance-2.0-i2v',
            'pixverse-c1-i2v',
            'wan2.7-i2v',
            'viduq3-pro-i2v',
            'viduq3-turbo-i2v',
        ]);
    });

    it('keeps hidden and planned catalog entries out of visible selectors', () => {
        expect(GLOBAL_I2V_MODELS.some((model) => model.id === 'wan2.6-r2v')).toBe(false);
        expect(GLOBAL_I2V_MODELS.some((model) => model.id === 'pixverse-v4-i2v')).toBe(false);
    });

    it('activates Wan 3.0 omni video only for the video sidebar surfaces', () => {
        expect(getCanonicalModeId('wan3.0-video')).toBe('wan/wan3.0-video#video');
        expect(getCanonicalModeId('wan3.0-video-prime')).toBe('wan/wan3.0-video-prime#video');
        expect(getCanonicalModeEntry('wan/wan3.0-video#video')).toMatchObject({
            legacy_model_id: 'wan3.0-video',
            mode: 'video',
            status: 'active',
            ui: { selection_group: 'video', visible_in: ['video_sidebar'] },
        });
        expect(getCanonicalModeEntry('wan/wan3.0-video#video')?.runtime.dashscope).toMatchObject({
            gateway: 'dashscope_workspace',
            api_model_id: 'wan3.0-video',
        });
        expect(getCanonicalModeEntry('wan/wan3.0-video#video')?.docs).toMatchObject({
            context_hub_doc_ids: [],
        });
        expect(getCanonicalModeEntry('wan/wan3.0-video#video')?.duration).toMatchObject({
            type: 'slider',
            default: 5,
            max: 30,
            supports_auto: true,
        });
        expect(getCanonicalModeEntry('wan/wan3.0-video#video')?.params).toMatchObject({
            audio: { default: true },
            resolution: { options: ['480P', '720P', '1080P'], default: '1080P' },
            ratio: { options: ['adaptive', '21:9', '16:9', '4:3', '1:1', '3:4', '9:16'], default: 'adaptive' },
        });
        expect(GLOBAL_I2V_MODELS.some((model) => model.id.startsWith('wan3.0'))).toBe(false);
        expect(PROJECT_I2V_MODELS.some((model) => model.id.startsWith('wan3.0'))).toBe(false);
        expect(SERIES_I2V_MODELS.some((model) => model.id.startsWith('wan3.0'))).toBe(false);
        expect(VIDEO_I2V_MODELS.filter((model) => model.id.startsWith('wan3.0')).map((model) => model.id)).toEqual([
            'wan3.0-video-prime',
            'wan3.0-video',
        ]);
        expect(VIDEO_R2V_MODELS.filter((model) => model.id.startsWith('wan3.0')).map((model) => model.id)).toEqual([
            'wan3.0-video-prime',
            'wan3.0-video',
        ]);
        expect(DEFAULT_R2V_MODEL_ID).toBe('happyhorse-1.1-r2v');
        expect(getR2vRouteModelId('wan3.0-video')).toBe('wan3.0-video');
        expect(getR2vRouteModelId('wan/wan3.0-video#video')).toBe('wan3.0-video');
    });
});

describe('model catalog fallbacks', () => {
    it('falls back unknown and legacy-surface ids to catalog defaults', () => {
        expect(
            resolveModelSettings(
                {
                    t2i_model: 'missing-model',
                    i2i_model: 'wan2.6-r2v',
                    i2v_model: 'missing-video-model',
                },
                'global_settings'
            )
        ).toMatchObject({
            t2i_model: 'wan2.7-image-pro',
            i2i_model: 'wan2.7-image-pro',
            i2v_model: 'happyhorse-1.1-i2v',
        });
    });

    it('normalizes canonical mode ids back to legacy compatibility ids when compat metadata exists', async () => {
        const catalogWithCompat = structuredClone(rawCatalog) as MockCatalog;
        catalogWithCompat.model_lines = {
            'wan/wan2.6-image': {
                id: 'wan/wan2.6-image',
                family: 'wan',
            },
            'wan/wan2.6-video': {
                id: 'wan/wan2.6-video',
                family: 'wan',
            },
        };
        catalogWithCompat.modes = {
            'wan/wan2.6-image#i2i': {
                id: 'wan/wan2.6-image#i2i',
                model_line_id: 'wan/wan2.6-image',
                mode: 'i2i',
            },
            'wan/wan2.6-video#i2v': {
                id: 'wan/wan2.6-video#i2v',
                model_line_id: 'wan/wan2.6-video',
                mode: 'i2v',
            },
            'wan/wan2.6-video#r2v': {
                id: 'wan/wan2.6-video#r2v',
                model_line_id: 'wan/wan2.6-video',
                mode: 'r2v',
            },
        };
        catalogWithCompat.compat = {
            legacy_model_ids: {
                'wan2.6-image': 'wan/wan2.6-image#i2i',
                'wan2.6-i2v': 'wan/wan2.6-video#i2v',
                'wan2.6-r2v': 'wan/wan2.6-video#r2v',
            },
        };

        vi.doMock('@/generated/modelCatalog.json', () => ({
            default: catalogWithCompat,
        }));

        const {
            GLOBAL_I2V_MODELS: compatI2vModels,
            R2V_ROUTE_MODEL_ID: compatR2vRouteModelId,
            R2V_SELECTION_MODEL_ID: compatR2vSelectionModelId,
            resolveModelSettings: resolveCompatModelSettings,
        } = await import('@/lib/modelCatalog');

        // After 524f3a1 deprecated the wan2.6 series, 'wan2.6-i2v' is hidden
        // (visible_in: []), so the canonical → legacy normalization is filtered
        // out by the visibility check and the resolver falls back to the current
        // i2v default (happyhorse-1.1-i2v). The raw normalization contract is
        // covered directly by the Phase 2 canonical helpers below.
        expect(
            resolveCompatModelSettings(
                {
                    i2v_model: 'wan/wan2.6-video#i2v',
                },
                'global_settings'
            ).i2v_model
        ).toBe('happyhorse-1.1-i2v');

        // An r2v canonical id normalizes to the matching legacy id
        // (wan2.6-r2v), which is hidden in the i2v surface — so the
        // resolver falls back to the current i2v default (happyhorse-1.1-i2v
        // since the 2026-05-26 catalog meta switch). Previously this
        // assertion expected the resolver to remap r2v into the parent
        // i2v legacy id; that behavior was dropped when r2v ids gained
        // explicit modality suffixes.
        expect(
            resolveCompatModelSettings(
                {
                    i2v_model: 'wan/wan2.6-video#r2v',
                },
                'global_settings'
            ).i2v_model
        ).toBe('happyhorse-1.1-i2v');

        expect(compatI2vModels.map((model) => model.id)).not.toContain('wan2.6-i2v');
        expect(compatI2vModels.some((model) => model.id === 'wan/wan2.6-video#i2v')).toBe(false);
        // R2V selection/route ids follow the catalog meta default
        // (defaults.model_settings.r2v_model = happyhorse-1.1-r2v) via
        // getFallbackVisibleModelId, not raw ui.order. Several R2V models
        // share order=80, so anchoring to the explicit meta default keeps the
        // default route deterministic. Selection and route are unified
        // (R2V_ROUTE_MODEL_ID = R2V_SELECTION_MODEL_ID).
        expect(compatR2vSelectionModelId).toBe('happyhorse-1.1-r2v');
        expect(compatR2vRouteModelId).toBe('happyhorse-1.1-r2v');
    });
});

describe('model catalog runtime helpers', () => {
    it('derives the current R2V selection and route ids from catalog data', () => {
        // Selection and route both resolve to the catalog meta default R2V
        // model (defaults.model_settings.r2v_model = happyhorse-1.1-r2v) via
        // getFallbackVisibleModelId — deterministic regardless of the order=80
        // tie among visible R2V models (happyhorse/kling/seedance/wan2.7).
        expect(R2V_SELECTION_MODEL_ID).toBe('happyhorse-1.1-r2v');
        expect(R2V_ROUTE_MODEL_ID).toBe('happyhorse-1.1-r2v');
    });

    it('reads per-model reference image limits from catalog metadata', () => {
        // getMaxReferenceImages routes the input through resolveModelId
        // for the 'i2i' surface — when the literal id isn't visible in
        // that surface (post-Phase 2 the wan2.6 ids moved to the
        // 'image' selection_group), the resolver falls back to the
        // current default (wan2.7-image, which advertises 9 refs).
        // The behavior is correct given how callers (PropertiesPanel)
        // use the project's i2i_model setting.
        expect(getMaxReferenceImages('wan2.6-image')).toBe(9);
        expect(getMaxReferenceImages('wan2.5-i2i-preview')).toBe(9);
    });
});

describe('model catalog phase 2 canonical helpers', () => {
    it('resolves legacy flat id to canonical mode id', () => {
        expect(getCanonicalModeId('wan2.6-i2v')).toBe('wan/wan2.6-video#i2v');
        expect(getCanonicalModeId('wan2.6-r2v')).toBe('wan/wan2.6-video#r2v');
        expect(getCanonicalModeId('nonexistent')).toBeUndefined();
    });

    it('resolves canonical mode id back to legacy flat id', () => {
        expect(getLegacyModelId('wan/wan2.6-video#i2v')).toBe('wan2.6-i2v');
        expect(getLegacyModelId('wan/wan2.6-video#r2v')).toBe('wan2.6-r2v');
        expect(getLegacyModelId('nonexistent')).toBeUndefined();
    });

    it('reads canonical mode entry with full metadata', () => {
        const entry = getCanonicalModeEntry('wan/wan2.6-video#i2v');
        expect(entry).not.toBeNull();
        expect(entry?.model_line_id).toBe('wan/wan2.6-video');
        expect(entry?.legacy_model_id).toBe('wan2.6-i2v');
        expect(entry?.mode).toBe('i2v');
        expect(entry?.family).toBe('wan');

        expect(getCanonicalModeEntry('nonexistent')).toBeNull();
    });

    it('reads model line entry', () => {
        const line = getModelLineEntry('wan/wan2.6-video');
        expect(line).not.toBeNull();
        expect(line?.family).toBe('wan');
        expect(line?.modes).toContain('wan/wan2.6-video#i2v');
        expect(line?.modes).toContain('wan/wan2.6-video#r2v');
        expect(line?.legacy_model_ids).toContain('wan2.6-i2v');

        expect(getModelLineEntry('nonexistent')).toBeNull();
    });

    it('reads gateway metadata from canonical mode runtime', () => {
        expect(getModeGateway('wan/wan2.6-video#r2v')).toBe('dashscope');
        expect(getModeGateway('wan/wan2.6-video#r2v', 'vendor')).toBeUndefined();
        expect(getModeGateway('nonexistent')).toBeUndefined();
    });

    it('reads canonical default model settings', () => {
        const defaults = getCanonicalDefaults();
        expect(defaults.t2i_model).toContain('#');
        expect(defaults.i2i_model).toContain('#');
        expect(defaults.i2v_model).toContain('#');
    });

    it('does not leak canonical ids into visible flat model selectors', () => {
        for (const model of GLOBAL_I2V_MODELS) {
            expect(model.id).not.toContain('#');
        }
        for (const model of GLOBAL_T2I_MODELS) {
            expect(model.id).not.toContain('#');
        }
        for (const model of GLOBAL_I2I_MODELS) {
            expect(model.id).not.toContain('#');
        }
    });
});


describe('Wan 2.7 Studio image size compatibility', () => {
    it.each(['wan2.7-image', 'wan2.7-image-pro'])('projects only the two missing sizes for %s', modelId => {
        const canonical = `wan/${modelId}#image`;
        expect(getCanonicalModeId(modelId)).toBe(canonical);
        const entry = getCanonicalModeEntry(canonical);
        expect(entry?.params).toMatchObject({ size: {
            options: ['1024*1024', '1280*1280', '1440*1440', '2048*2048',
                '1280*720', '720*1280', '1024*576', '576*1024', '768*1024', '1024*768'],
            default: '1280*1280',
        } });
        expect(entry?.runtime.dashscope).toEqual({ gateway: 'dashscope', api_model_id: modelId });
        expect(entry?.ui.visible_in).toEqual(['project_settings', 'series_settings', 'global_settings']);
        expect(entry?.params).toMatchObject({ negativePrompt: true, promptExtend: true, seed: true, watermark: true });
    });
});
