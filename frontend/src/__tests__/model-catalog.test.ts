import rawCatalog from '@/generated/modelCatalog.json';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
    DEFAULT_MODEL_SETTINGS,
    GLOBAL_I2I_MODELS,
    GLOBAL_I2V_MODELS,
    GLOBAL_T2I_MODELS,
    R2V_ROUTE_MODEL_ID,
    R2V_SELECTION_MODEL_ID,
    getCanonicalDefaults,
    getCanonicalModeEntry,
    getCanonicalModeId,
    getLegacyModelId,
    getMaxReferenceImages,
    getModelLineEntry,
    getModeGateway,
    getR2vReferenceInputConfig,
    getR2vRouteModelId,
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
        expect(DEFAULT_MODEL_SETTINGS).toMatchObject({
            t2i_model: 'wan2.7-image-pro',
            i2i_model: 'wan2.7-image',
            image_model: 'wan2.7-image-pro',
            i2v_model: 'wan2.7-i2v',
        });

        expect(GLOBAL_T2I_MODELS.map((model) => model.id)).toEqual([]);

        expect(GLOBAL_I2I_MODELS.map((model) => model.id)).toEqual([]);

        expect(GLOBAL_I2V_MODELS.map((model) => model.id)).toEqual([
            'happyhorse-1.0-i2v',
            'kling-v3-i2v',
            'pixverse/pixverse-v6-video',
            'pixverse-c1-i2v',
            'wan2.7-i2v',
            'wan2.6-i2v',
            'wan2.6-i2v-flash',
            'viduq3-pro-i2v',
            'viduq3-turbo-i2v',
        ]);
    });

    it('keeps hidden and planned catalog entries out of visible selectors', () => {
        expect(GLOBAL_I2V_MODELS.some((model) => model.id === 'wan2.6-r2v')).toBe(false);
        expect(GLOBAL_I2V_MODELS.some((model) => model.id === 'pixverse-v4-i2v')).toBe(false);
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
            i2i_model: 'wan2.7-image',
            i2v_model: 'wan2.7-i2v',
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

        const { GLOBAL_I2V_MODELS: compatI2vModels, getR2vRouteModelId: getCompatR2vRouteModelId, resolveModelSettings: resolveCompatModelSettings } = await import('@/lib/modelCatalog');

        expect(
            resolveCompatModelSettings(
                {
                    i2i_model: 'wan/wan2.6-image#i2i',
                    i2v_model: 'wan/wan2.6-video#i2v',
                },
                'global_settings'
            )
        ).toMatchObject({ i2v_model: 'wan2.6-i2v' });

        expect(
            resolveCompatModelSettings(
                {
                    i2v_model: 'wan/wan2.6-video#r2v',
                },
                'global_settings'
            ).i2v_model
        ).toBe('wan2.7-i2v');

        expect(compatI2vModels.map((model) => model.id)).toContain('wan2.6-i2v');
        expect(compatI2vModels.some((model) => model.id === 'wan/wan2.6-video#i2v')).toBe(false);
        expect(getCompatR2vRouteModelId('wan2.6-i2v')).toBe('wan2.6-r2v');
    });
});

describe('model catalog runtime helpers', () => {
    it('derives the current R2V selection and route ids from catalog data', () => {
        expect(R2V_SELECTION_MODEL_ID).toBe('happyhorse-1.0-i2v');
        expect(R2V_ROUTE_MODEL_ID).toBe('happyhorse-1.0-r2v');
        expect(getR2vRouteModelId('happyhorse-1.0-i2v')).toBe('happyhorse-1.0-r2v');
        expect(getR2vRouteModelId('wan2.7-i2v')).toBe('wan2.7-r2v');
        expect(getR2vRouteModelId('wan2.6-i2v')).toBe('wan2.6-r2v');
    });

    it('reads per-model reference image limits from catalog metadata', () => {
        expect(getMaxReferenceImages('wan2.7-image')).toBe(9);
        expect(getMaxReferenceImages('qwen-image-2.0')).toBe(3);
    });

    it('reads concrete R2V reference input contracts per routed model', () => {
        expect(getR2vReferenceInputConfig('happyhorse-1.0-r2v')).toEqual({ type: 'image', max: 9 });
        expect(getR2vReferenceInputConfig('wan2.7-r2v')).toEqual({ type: 'video', max: 5 });
        expect(getR2vReferenceInputConfig('wan2.6-r2v')).toEqual({ type: 'video', max: 3 });
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
