import rawCatalog from '@/generated/modelCatalog.json';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
    DEFAULT_MODEL_SETTINGS,
    GLOBAL_I2I_MODELS,
    GLOBAL_I2V_MODELS,
    GLOBAL_IMAGE_MODELS,
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
            i2v_model: 'happyhorse-1.0-i2v',
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

    it('exposes Seedance 2.0 480p across video modes', () => {
        for (const modelId of ['seedance-2.0-t2v', 'seedance-2.0-i2v', 'seedance-2.0-r2v']) {
            expect((rawCatalog as any).models[modelId].params.resolution.options).toEqual([
                '480p',
                '720p',
                '1080p',
            ]);
        }
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
            i2v_model: 'happyhorse-1.0-i2v',
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

        // i2v_model: 'wan2.6-i2v' remains visible in global_settings, so
        // the canonical → legacy normalization survives the visibility
        // filter. The i2i_model assertion was dropped: 'wan2.6-image'
        // migrated to selection_group='image' in Phase 2 and is no longer
        // visible under the 'i2i' surface, so the resolver correctly
        // falls back to the current default (wan2.7-image). The Phase 2
        // canonical helpers below cover the normalization contract
        // directly without the visibility filter.
        expect(
            resolveCompatModelSettings(
                {
                    i2v_model: 'wan/wan2.6-video#i2v',
                },
                'global_settings'
            ).i2v_model
        ).toBe('wan2.6-i2v');

        // An r2v canonical id normalizes to the matching legacy id
        // (wan2.6-r2v), which is hidden in the i2v surface — so the
        // resolver falls back to the current i2v default (happyhorse-1.0-i2v
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
        ).toBe('happyhorse-1.0-i2v');

        expect(compatI2vModels.map((model) => model.id)).toContain('wan2.6-i2v');
        expect(compatI2vModels.some((model) => model.id === 'wan/wan2.6-video#i2v')).toBe(false);
        // R2V selection/route ids are derived globally from catalog
        // ordering, not from the mocked compat slice.
        // - Selection (visible I2V picker target): happyhorse-1.0-i2v
        //   wins because its order=120 > everyone else's.
        // - Route (hidden R2V dispatch target): wan2.7-r2v wins
        //   because its order=20 > all other hidden R2V models
        //   (after the 2026-05-21 bump to prefer wan2.7 over wan2.6).
        expect(compatR2vSelectionModelId).toBe('happyhorse-1.0-i2v');
        expect(compatR2vRouteModelId).toBe('wan2.7-r2v');
    });
});

describe('model catalog runtime helpers', () => {
    it('derives the current R2V selection and route ids from catalog data', () => {
        // Selection target = highest-ordered visible R2V-capable
        // I2V model (happyhorse-1.0-i2v at order 120).
        // Route target = highest-ordered hidden R2V model, which is
        // wan2.7-r2v at order 20 (deliberately bumped above
        // wan2.6-r2v's 10 so Wan 2.7 wins the default routing).
        expect(R2V_SELECTION_MODEL_ID).toBe('happyhorse-1.0-i2v');
        expect(R2V_ROUTE_MODEL_ID).toBe('wan2.7-r2v');
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
