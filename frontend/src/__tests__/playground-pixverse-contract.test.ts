import { describe, expect, it } from 'vitest';
import {
  buildPlaygroundRequestParameters,
  hasInactivePlaygroundParameterDrafts,
  resolvePlaygroundParameterContract,
  supportsPlaygroundNegativePrompt,
} from '@/components/modules/playground/playgroundParameterContract';
import { getModelDuration, getModelOption, getModelsForMode } from '@/components/modules/playground/playgroundModels';
import { getCanonicalModeId, getModeGateway } from '@/lib/modelCatalog';

const models = [
  ['pixverse/pixverse-v6-video', 'pixverse/pixverse/pixverse-v6-video#i2v', 'i2v', 1],
  ['pixverse-c1-i2v', 'pixverse/pixverse-c1-video#i2v', 'i2v', 1],
  ['pixverse-c1-r2v', 'pixverse/pixverse-c1-video#r2v', 'r2v', 7],
  ['pixverse-v5.6-r2v', 'pixverse/pixverse-v5.6-r2v#r2v', 'r2v', 7],
] as const;

describe('Playground PixVerse workspace contract', () => {
  it.each(models)('keeps exact identity, gateway and image limits for %s', (id, canonical, mode, max) => {
    expect(getCanonicalModeId(id)).toBe(canonical);
    expect(getModeGateway(canonical)).toBe('dashscope_workspace');
    expect(resolvePlaygroundParameterContract(canonical)?.modelId).toBe(id);
    expect(resolvePlaygroundParameterContract(id)?.modelId).toBe(id);
    expect(getModelsForMode(mode).map(model => model.id)).toContain(id);
    expect(getModelOption(canonical)).toMatchObject({
      maxReferenceImages: max,
      referenceImageMimeTypes: ['image/jpeg', 'image/png', 'image/webp'],
    });
    expect(supportsPlaygroundNegativePrompt(canonical)).toBe(false);
  });

  it.each(models)('uses false defaults and seed zero without unsupported drafts for %s', (id, canonical, mode) => {
    const draft = { seed: 0, prompt_extend: true, negative_prompt: 'local draft', audio_setting: 'origin', shot_type: 'multi' };
    expect(buildPlaygroundRequestParameters(canonical, draft)).toEqual({
      duration: 5,
      resolution: '720P',
      seed: 0,
      audio: false,
      watermark: false,
      ...(mode === 'r2v' ? { aspect_ratio: '16:9' } : {}),
      ...(id === 'pixverse/pixverse-v6-video' ? { shot_type: 'multi' } : {}),
    });
    expect(hasInactivePlaygroundParameterDrafts(id, draft)).toBe(true);
    expect(draft.shot_type).toBe('multi');
    for (const seed of [-1, 0.5, Number.NaN, Number.POSITIVE_INFINITY, 2147483648]) {
      expect(buildPlaygroundRequestParameters(id, { seed }).seed).toBeUndefined();
    }
    expect(buildPlaygroundRequestParameters(id, { seed: 2147483647 }).seed).toBe(2147483647);
  });

  it('projects the V5.6 duration condition and restores the original 10-second draft', () => {
    expect(getModelDuration('pixverse-v5.6-r2v')).toEqual({
      type: 'buttons', options: [5, 8, 10], default: 5,
      options_by_resolution: { '720P': [5, 8, 10], '1080P': [5, 8] },
    });
    const draft = { resolution: '720P', duration: 10, aspect_ratio: '3:4', audio: false, watermark: false };
    expect(buildPlaygroundRequestParameters('pixverse-v5.6-r2v', draft).duration).toBe(10);
    draft.resolution = '1080P';
    expect(buildPlaygroundRequestParameters('pixverse-v5.6-r2v', draft)).toMatchObject({ resolution: '1080P', duration: 5, aspect_ratio: '3:4' });
    expect(draft.duration).toBe(10);
    expect(hasInactivePlaygroundParameterDrafts('pixverse-v5.6-r2v', draft)).toBe(true);
    draft.resolution = '720P';
    expect(buildPlaygroundRequestParameters('pixverse-v5.6-r2v', draft).duration).toBe(10);
    expect(hasInactivePlaygroundParameterDrafts('pixverse-v5.6-r2v', draft)).toBe(false);
  });

  it('uses valid V6 shot defaults and preserves a multi-shot draft across C1', () => {
    const draft = { shot_type: 'multi' };
    expect(buildPlaygroundRequestParameters('pixverse/pixverse-v6-video', {})).toMatchObject({ shot_type: 'single' });
    expect(buildPlaygroundRequestParameters('pixverse/pixverse-v6-video', { shot_type: 'invalid' })).toMatchObject({ shot_type: 'single' });
    expect(buildPlaygroundRequestParameters('pixverse-c1-i2v', draft)).not.toHaveProperty('shot_type');
    expect(buildPlaygroundRequestParameters('pixverse/pixverse-v6-video', draft)).toHaveProperty('shot_type', 'multi');
    for (const duration of [0, 1.5, 16, Number.NaN]) {
      expect(buildPlaygroundRequestParameters('pixverse-c1-r2v', { duration }).duration).toBe(5);
    }
    expect(buildPlaygroundRequestParameters('pixverse-c1-r2v', { duration: 15 }).duration).toBe(15);
  });
});
