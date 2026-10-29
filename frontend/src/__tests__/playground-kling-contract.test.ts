import { describe, expect, it } from 'vitest';
import { buildPlaygroundRequestParameters, hasInactivePlaygroundParameterDrafts } from '@/components/modules/playground/playgroundParameterContract';
import { getModelOption, getModelsForMode } from '@/components/modules/playground/playgroundModels';
import { resolveMigratedPlaygroundVideoModelId } from '@/components/modules/playground/playgroundModelAliases';

describe('Playground Kling workspace contract', () => {
  it('keeps exact workspace aliases visible with the catalog reference limit', () => {
    expect(resolveMigratedPlaygroundVideoModelId('kling/kling-v3-video#i2v')).toBe('kling-v3-i2v');
    expect(resolveMigratedPlaygroundVideoModelId('kling/kling-v3-video#r2v')).toBe('kling-v3-r2v');
    expect(getModelsForMode('i2v').some(m => m.id === 'kling-v3-i2v')).toBe(true);
    expect(getModelsForMode('r2v').some(m => m.id === 'kling-v3-r2v')).toBe(true);
    expect(getModelOption('kling/kling-v3-video#r2v')?.maxReferenceImages).toBe(7);
  });
  it('materializes the actual I2V defaults and excludes legacy parameters without deleting drafts', () => {
    const draft = { resolution: '1080p', aspect_ratio: '9:16', seed: 0, sound: true, cfg_scale: 0.5, prompt_extend: true };
    expect(buildPlaygroundRequestParameters('kling-v3-i2v', draft)).toEqual({ duration: 5, mode: 'pro', audio: false, watermark: false });
    expect(hasInactivePlaygroundParameterDrafts('kling-v3-i2v', draft)).toBe(true);
    expect(draft.seed).toBe(0);
  });
  it('preserves R2V quality/audio choices and false while materializing only the R2V ratio', () => {
    expect(buildPlaygroundRequestParameters('kling/kling-v3-video#r2v', { mode: '4k', audio: true, watermark: false, duration: 3, aspect_ratio: '9:16' })).toEqual({mode: '4k', audio: true, watermark: false, duration: 3, aspect_ratio: '9:16'});
    expect(buildPlaygroundRequestParameters('kling-v3-r2v', {})).toEqual({mode: 'pro', audio: false, watermark: false, duration: 5, aspect_ratio: '16:9'});
  });
});
