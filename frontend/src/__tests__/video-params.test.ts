/**
 * Tests for model-adaptive video parameter configs.
 *
 * Covers:
 * - I2V_MODELS 配置完整性
 * - ModelParamSupport 各模型参数正确性
 * - GRID_COLS_CLASS 工具映射
 * - VideoParams 类型实例化
 */
import { describe, it, expect } from 'vitest';
import {
    I2V_MODELS,
    GRID_COLS_CLASS,
    type ModelParamSupport,
} from '@/store/projectStore';
import rawCatalog from '@/generated/modelCatalog.json';

type RawModelCatalog = {
    models: Record<string, { params?: ModelParamSupport }>;
};

const catalogModels = (rawCatalog as RawModelCatalog).models;

// ── I2V_MODELS 配置完整性 ─────────────────────────────────────────────

describe('I2V_MODELS 配置', () => {
    it('每个模型都包含 params 字段', () => {
        for (const model of I2V_MODELS) {
            expect(model.params).toBeDefined();
            expect(typeof model.params).toBe('object');
        }
    });

    it('每个模型都有唯一 id', () => {
        const ids = I2V_MODELS.map(m => m.id);
        expect(new Set(ids).size).toBe(ids.length);
    });

    it('每个模型都有 duration 配置', () => {
        for (const model of I2V_MODELS) {
            expect(model.duration).toBeDefined();
            expect(['slider', 'buttons', 'fixed']).toContain(model.duration.type);
        }
    });
});

// ── Wan 2.6 参数 ───────────────────────────────────────────────────────

// wan2.6-i2v is now hidden in the catalog (deprecated in 524f3a1, visible_in:
// []), so it no longer appears in I2V_MODELS. Read its params directly from the
// generated catalog to keep the wan2.6 contract documented and tested.
describe('Wan 2.6 模型参数', () => {
    const p = catalogModels['wan2.6-i2v']?.params as ModelParamSupport;

    it('支持所有 Wan 系列参数', () => {
        expect(p.resolution).toBeDefined();
        expect(p.seed).toBe(true);
        expect(p.negativePrompt).toBe(true);
        expect(p.promptExtend).toBe(true);
        expect(p.shotType).toBe(true);
        expect(p.audio).toBe(true);
    });

    it('resolution 包含 480p/720p/1080p', () => {
        expect(p.resolution!.options).toEqual(['480p', '720p', '1080p']);
        expect(p.resolution!.default).toBe('720p');
    });

    it('不支持 Kling/Vidu 独有参数', () => {
        expect(p.mode).toBeUndefined();
        expect(p.sound).toBeUndefined();
        expect(p.cfgScale).toBeUndefined();
        expect(p.viduAudio).toBeUndefined();
        expect(p.movementAmplitude).toBeUndefined();
    });
});

// ── Wan 2.5 参数 ───────────────────────────────────────────────────────

// wan2.5-i2v-preview is now hidden in the catalog (visible_in: []), so
// it doesn't appear in I2V_MODELS. Read its params directly from the
describe('Wan 2.5 模型参数', () => {
    const wan25Params = catalogModels['wan2.5-i2v-preview']?.params;
    const p = wan25Params as ModelParamSupport;

    it('支持 resolution, seed, negativePrompt, audio', () => {
        expect(p.resolution).toBeDefined();
        expect(p.seed).toBe(true);
        expect(p.negativePrompt).toBe(true);
        expect(p.audio).toBe(true);
    });

    it('不支持 promptExtend 和 shotType', () => {
        expect(p.promptExtend).toBeUndefined();
        expect(p.shotType).toBeUndefined();
    });
});

// ── Wan 2.2 参数 ───────────────────────────────────────────────────────

describe('Wan 2.2 模型参数', () => {
    // wan2.2-i2v-plus is now hidden in the catalog. Read params directly
    // from the raw catalog so the legacy contract stays documented.
    const wan22Params = catalogModels['wan2.2-i2v-plus']?.params;
    const p = wan22Params as ModelParamSupport;

    it('支持 resolution, seed, negativePrompt', () => {
        expect(p.resolution).toBeDefined();
        expect(p.seed).toBe(true);
        expect(p.negativePrompt).toBe(true);
    });

    it('不支持 promptExtend, shotType, audio', () => {
        expect(p.promptExtend).toBeUndefined();
        expect(p.shotType).toBeUndefined();
        expect(p.audio).toBeUndefined();
    });
});

// ── Kling v3 参数 ──────────────────────────────────────────────────────

describe('Kling v3 模型参数', () => {
    // Phase 2 split kling-v3 → kling-v3-i2v / kling-v3-r2v.
    const kling = I2V_MODELS.find(m => m.id === 'kling-v3-i2v')!;
    const p = kling.params;

    it('支持 negativePrompt, mode, audio, watermark', () => {
        expect(p.negativePrompt).toBe(true);
        expect(p.mode).toBeDefined();
        expect(p.audio).toEqual({ default: false });
        expect(p.watermark).toEqual({ default: false });
    });

    it('mode 选项为 std/pro/4k，默认 pro', () => {
        expect(p.mode!.options).toEqual(['std', 'pro', '4k']);
        expect(p.mode!.default).toBe('pro');
    });

    it('不支持 Wan 独有参数', () => {
        // Phase 2 per-model catalog params list unsupported flags explicitly as
        // `false` instead of omitting them, so assert falsy (false | undefined)
        // for "not supported".
        expect(p.resolution).toBeFalsy();
        expect(p.seed).toBeFalsy();
        expect(p.promptExtend).toBeFalsy();
        expect(p.shotType).toBeFalsy();
        expect(p.cfgScale).toBeFalsy();
        expect(p.sound).toBeFalsy();
    });

    it('不支持 Vidu 独有参数', () => {
        expect(p.viduAudio).toBeFalsy();
        expect(p.movementAmplitude).toBeFalsy();
    });
});

// ── Vidu Q3 参数 ───────────────────────────────────────────────────────

describe('Vidu Q3 模型参数', () => {
    // Phase 2 split viduq3-pro / viduq3-turbo by modality suffix.
    const viduPro = I2V_MODELS.find(m => m.id === 'viduq3-pro-i2v')!;
    const viduTurbo = I2V_MODELS.find(m => m.id === 'viduq3-turbo-i2v')!;

    it('Pro 和 Turbo 使用相同的参数配置', () => {
        expect(viduPro.params).toEqual(viduTurbo.params);
    });

    const p = viduPro.params;

    it('支持 resolution, seed bounds, generic audio, and watermark defaults', () => {
        expect(p.resolution).toBeDefined();
        expect(p.seed).toEqual({ min: 0, max: 2147483647 });
        expect(p.audio).toEqual({ default: false });
        expect(p.watermark).toEqual({ default: false });
        expect(p.viduAudio).toBeUndefined();
        expect(p.movementAmplitude).toBeUndefined();
    });

    it('resolution 使用官方大写枚举', () => {
        expect(p.resolution!.options).toEqual(['540P', '720P', '1080P']);
        expect(p.resolution!.default).toBe('720P');
    });

    it('不支持 Kling/Wan 独有参数', () => {
        // Per-model catalog params list unsupported flags explicitly as
        // `false`; assert falsy (false | undefined) for "not supported".
        expect(p.negativePrompt).toBeFalsy();
        expect(p.promptExtend).toBeFalsy();
        expect(p.shotType).toBeFalsy();
        expect(p.mode).toBeFalsy();
        expect(p.sound).toBeFalsy();
        expect(p.cfgScale).toBeFalsy();
    });
});

// ── GRID_COLS_CLASS 映射 ───────────────────────────────────────────────

describe('GRID_COLS_CLASS', () => {
    it('2 列映射为 grid-cols-2', () => {
        expect(GRID_COLS_CLASS[2]).toBe('grid-cols-2');
    });

    it('3 列映射为 grid-cols-3', () => {
        expect(GRID_COLS_CLASS[3]).toBe('grid-cols-3');
    });

    it('4 列映射为 grid-cols-4', () => {
        expect(GRID_COLS_CLASS[4]).toBe('grid-cols-4');
    });

    it('覆盖所有 I2V_MODELS 中实际使用的列数', () => {
        // resolution: 3 cols, mode: 2 cols, movementAmplitude: 4 cols, duration buttons: 2 cols
        const usedCounts = new Set<number>();
        for (const model of I2V_MODELS) {
            const p = model.params;
            if (p.resolution) usedCounts.add(p.resolution.options.length);
            if (p.mode) usedCounts.add(p.mode.options.length);
            if (p.movementAmplitude) usedCounts.add(p.movementAmplitude.options.length);
            if (model.duration.type === 'buttons') {
                usedCounts.add(model.duration.options.length);
            }
        }
        usedCounts.forEach((count) => {
            expect(GRID_COLS_CLASS[count]).toBeDefined();
        });
    });
});

// ── 参数默认值重置逻辑（纯逻辑测试） ──────────────────────────────────

describe('模型切换参数重置逻辑', () => {
    /** 模拟 VideoSidebar 中 updateParam("model", ...) 的重置逻辑 */
    function simulateModelSwitch(targetModelId: string): Record<string, string | number | boolean> {
        const newModelConfig = I2V_MODELS.find(m => m.id === targetModelId);
        const np = newModelConfig?.params ?? {};
        return {
            resolution: np.resolution?.default ?? "720p",
            promptExtend: !!np.promptExtend,
            negativePrompt: "",
            shotType: "single",
            generateAudio: false,
            audioUrl: "",
            mode: np.mode?.default ?? "std",
            sound: false,
            cfgScale: np.cfgScale?.default ?? 0.5,
            audio: typeof np.audio === "object" ? np.audio.default : !!np.audio,
            viduAudio: !!np.viduAudio,
            movementAmplitude: np.movementAmplitude?.default ?? "",
        };
    }

    it('切换到 Kling → mode 默认 pro', () => {
        const result = simulateModelSwitch('kling-v3-i2v');
        expect(result.mode).toBe('pro');
        expect(result.cfgScale).toBe(0.5);
        expect(result.promptExtend).toBe(false);
    });

    it('切换到 Vidu → generic audio 默认 false 且不启用旧 Vidu 专属字段', () => {
        const result = simulateModelSwitch('viduq3-pro-i2v');
        expect(result.audio).toBe(false);
        expect(result.movementAmplitude).toBe('');
        expect(result.viduAudio).toBe(false);
    });

    it('切换到 Wan 2.7 → promptExtend 默认 true', () => {
        // wan2.6 was deprecated/hidden (524f3a1); wan2.7-i2v is the current
        // visible Wan I2V model. Its resolution default is 1080p.
        const result = simulateModelSwitch('wan2.7-i2v');
        expect(result.promptExtend).toBe(true);
        expect(result.resolution).toBe('1080p');
    });

    it('切换到 Wan 2.2 → 无 promptExtend', () => {
        const result = simulateModelSwitch('wan2.2-i2v-plus');
        expect(result.promptExtend).toBe(false);
    });
});
