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
    type VideoParams,
    type I2VModelConfig,
} from '@/store/projectStore';

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

// ── Seedance 2.0 参数 ──────────────────────────────────────────────────

describe('Seedance 2.0 模型参数', () => {
    const seedance = I2V_MODELS.find(m => m.id === 'doubao-seedance-2-0-260128')!;
    const p = seedance.params;

    it('支持 resolution, seed, audio', () => {
        expect(p.resolution).toBeDefined();
        expect(p.seed).toBe(true);
        expect(p.audio).toBe(true);
    });

    it('resolution 仅包含 480p/720p', () => {
        expect(p.resolution!.options).toEqual(['480p', '720p']);
        expect(p.resolution!.default).toBe('720p');
    });

    it('不支持 Wan/Kling/Vidu 专属参数', () => {
        expect(p.negativePrompt).toBeUndefined();
        expect(p.promptExtend).toBeUndefined();
        expect(p.shotType).toBeUndefined();
        expect(p.mode).toBeUndefined();
        expect(p.sound).toBeUndefined();
        expect(p.cfgScale).toBeUndefined();
        expect(p.viduAudio).toBeUndefined();
        expect(p.movementAmplitude).toBeUndefined();
    });
});

// ── Wan 2.6 参数 ───────────────────────────────────────────────────────

describe('Wan 2.6 模型参数', () => {
    const wan26 = I2V_MODELS.find(m => m.id === 'wan2.6-i2v')!;
    const p = wan26.params;

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

describe('Wan 2.5 模型参数', () => {
    const wan25 = I2V_MODELS.find(m => m.id === 'wan2.5-i2v-preview')!;
    const p = wan25.params;

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
    const wan22 = I2V_MODELS.find(m => m.id === 'wan2.2-i2v-plus')!;
    const p = wan22.params;

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
    const kling = I2V_MODELS.find(m => m.id === 'kling-v3')!;
    const p = kling.params;

    it('支持 negativePrompt, mode, sound, cfgScale', () => {
        expect(p.negativePrompt).toBe(true);
        expect(p.mode).toBeDefined();
        expect(p.sound).toBe(true);
        expect(p.cfgScale).toBeDefined();
    });

    it('mode 选项为 std/pro，默认 std', () => {
        expect(p.mode!.options).toEqual(['std', 'pro']);
        expect(p.mode!.default).toBe('std');
    });

    it('cfgScale 范围 0-1，步长 0.1', () => {
        expect(p.cfgScale!.min).toBe(0);
        expect(p.cfgScale!.max).toBe(1);
        expect(p.cfgScale!.step).toBe(0.1);
        expect(p.cfgScale!.default).toBe(0.5);
    });

    it('不支持 Wan 独有参数', () => {
        expect(p.resolution).toBeUndefined();
        expect(p.seed).toBeUndefined();
        expect(p.promptExtend).toBeUndefined();
        expect(p.shotType).toBeUndefined();
        expect(p.audio).toBeUndefined();
    });

    it('不支持 Vidu 独有参数', () => {
        expect(p.viduAudio).toBeUndefined();
        expect(p.movementAmplitude).toBeUndefined();
    });
});

// ── Vidu Q3 参数 ───────────────────────────────────────────────────────

describe('Vidu Q3 模型参数', () => {
    const viduPro = I2V_MODELS.find(m => m.id === 'viduq3-pro')!;
    const viduTurbo = I2V_MODELS.find(m => m.id === 'viduq3-turbo')!;

    it('Pro 和 Turbo 使用相同的参数配置', () => {
        expect(viduPro.params).toEqual(viduTurbo.params);
    });

    const p = viduPro.params;

    it('支持 resolution, seed, viduAudio, movementAmplitude', () => {
        expect(p.resolution).toBeDefined();
        expect(p.seed).toBe(true);
        expect(p.viduAudio).toBe(true);
        expect(p.movementAmplitude).toBeDefined();
    });

    it('resolution 包含 540p/720p/1080p', () => {
        expect(p.resolution!.options).toEqual(['540p', '720p', '1080p']);
    });

    it('movementAmplitude 选项为 auto/small/medium/large', () => {
        expect(p.movementAmplitude!.options).toEqual(['auto', 'small', 'medium', 'large']);
        expect(p.movementAmplitude!.default).toBe('auto');
    });

    it('不支持 Kling/Wan 独有参数', () => {
        expect(p.negativePrompt).toBeUndefined();
        expect(p.promptExtend).toBeUndefined();
        expect(p.shotType).toBeUndefined();
        expect(p.audio).toBeUndefined();
        expect(p.mode).toBeUndefined();
        expect(p.sound).toBeUndefined();
        expect(p.cfgScale).toBeUndefined();
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
    function simulateModelSwitch(targetModelId: string): Record<string, any> {
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
            viduAudio: true,
            movementAmplitude: np.movementAmplitude?.default ?? "auto",
        };
    }

    it('切换到 Kling → mode 默认 std', () => {
        const result = simulateModelSwitch('kling-v3');
        expect(result.mode).toBe('std');
        expect(result.cfgScale).toBe(0.5);
        expect(result.promptExtend).toBe(false);
    });

    it('切换到 Vidu → movementAmplitude 默认 auto', () => {
        const result = simulateModelSwitch('viduq3-pro');
        expect(result.movementAmplitude).toBe('auto');
        expect(result.viduAudio).toBe(true);
    });

    it('切换到 Wan 2.6 → promptExtend 默认 true', () => {
        const result = simulateModelSwitch('wan2.6-i2v');
        expect(result.promptExtend).toBe(true);
        expect(result.resolution).toBe('720p');
    });

    it('切换到 Seedance 2.0 → 分辨率默认 720p，且无 promptExtend', () => {
        const result = simulateModelSwitch('doubao-seedance-2-0-260128');
        expect(result.resolution).toBe('720p');
        expect(result.promptExtend).toBe(false);
    });

    it('切换到 Wan 2.2 → 无 promptExtend', () => {
        const result = simulateModelSwitch('wan2.2-i2v-plus');
        expect(result.promptExtend).toBe(false);
    });
});
