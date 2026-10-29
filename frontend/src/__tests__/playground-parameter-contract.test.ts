import { describe, expect, it } from 'vitest';

import {
  buildPlaygroundRequestParameters,
  getEffectivePlaygroundParameters,
  hasInactivePlaygroundParameterDrafts,
  resolvePlaygroundParameterContract,
  supportsPlaygroundNegativePrompt,
} from '@/components/modules/playground/playgroundParameterContract';

describe('playground migrated video parameter contract', () => {
  it('keeps Wan 2.7 VideoEdit exact controls and preserves explicit false and zero values', () => {
    expect(resolvePlaygroundParameterContract('wan/wan2.7-video#v2v')?.modelId).toBe('wan2.7-videoedit');

    const effective = getEffectivePlaygroundParameters('wan2.7-videoedit', {
      duration: 0,
      seed: 0,
      prompt_extend: false,
      watermark: false,
      audio_setting: 'origin',
    });

    expect(effective.duration).toBe(0);
    expect(effective.seed).toBe(0);
    expect(effective.prompt_extend).toBe(false);
    expect(effective.watermark).toBe(false);
    expect(effective.audio_setting).toBe('origin');

    expect(buildPlaygroundRequestParameters('wan2.7-videoedit', effective)).toMatchObject({
      duration: 0,
      seed: 0,
      prompt_extend: false,
      watermark: false,
      audio_setting: 'origin',
    });
  });

  it('does not materialize unsupported Wan I2V ratio from UI fallbacks', () => {
    expect(buildPlaygroundRequestParameters('wan2.7-i2v', {
      duration: 5,
      resolution: '1080p',
      aspect_ratio: '16:9',
      prompt_extend: true,
      watermark: false,
    })).toEqual({
      duration: 5,
      resolution: '1080p',
      prompt_extend: true,
      watermark: false,
    });
  });

  it('uses HappyHorse watermark default as ON and omits unsupported draft params', () => {
    expect(resolvePlaygroundParameterContract('happyhorse/happyhorse-1.1-video#r2v')?.modelId).toBe('happyhorse-1.1-r2v');
    expect(resolvePlaygroundParameterContract('happyhorse/happyhorse-1.1-video#i2v')?.modelId).toBe('happyhorse-1.1-i2v');

    const effective = getEffectivePlaygroundParameters('happyhorse-1.1-i2v', {
      duration: 5,
      resolution: '720P',
      aspect_ratio: '9:16',
      prompt_extend: false,
      audio_setting: 'origin',
      watermark: undefined,
      seed: 0,
    });

    expect(effective.watermark).toBe(true);
    expect(buildPlaygroundRequestParameters('happyhorse-1.1-i2v', effective)).toEqual({
      duration: 5,
      resolution: '720P',
      seed: 0,
      watermark: true,
    });

    expect(buildPlaygroundRequestParameters('happyhorse-1.1-i2v', { ...effective, watermark: false })).toMatchObject({
      watermark: false,
    });
  });

  it('keeps unsupported negative prompt drafts out of HappyHorse requests', () => {
    expect(supportsPlaygroundNegativePrompt('happyhorse-1.1-r2v')).toBe(false);
    expect(supportsPlaygroundNegativePrompt('wan2.7-r2v')).toBe(true);
  });

  it('keeps Vidu Q3 controls catalog-driven and preserves false and seed zero values', () => {
    expect(resolvePlaygroundParameterContract('vidu/viduq3-pro-video#i2v')?.modelId).toBe('viduq3-pro-i2v');
    expect(resolvePlaygroundParameterContract('vidu/viduq3-turbo-video#i2v')?.modelId).toBe('viduq3-turbo-i2v');
    expect(resolvePlaygroundParameterContract('vidu/viduq3-pro-video#r2v')?.modelId).toBe('viduq3-pro-r2v');
    expect(resolvePlaygroundParameterContract('vidu/viduq3-turbo-video#r2v')?.modelId).toBe('viduq3-turbo-r2v');

    const proI2v = getEffectivePlaygroundParameters('viduq3-pro-i2v', {
      duration: 99,
      resolution: '540P',
      aspect_ratio: '9:16',
      prompt_extend: true,
      watermark: false,
      audio: false,
      seed: 0,
    });
    expect(proI2v).toEqual({
      duration: 5,
      resolution: '540P',
      watermark: false,
      audio: false,
      seed: 0,
    });
    expect(buildPlaygroundRequestParameters('viduq3-pro-i2v', proI2v)).toEqual(proI2v);
    expect(buildPlaygroundRequestParameters('viduq3-pro-i2v', {
      duration: 5,
      resolution: '720P',
      seed: -1,
      watermark: false,
      audio: false,
    })).toEqual({
      duration: 5,
      resolution: '720P',
      watermark: false,
      audio: false,
    });

    const proR2v = getEffectivePlaygroundParameters('viduq3-pro-r2v', {
      duration: 16,
      resolution: '540P',
      aspect_ratio: '3:4',
      watermark: false,
      audio: false,
      seed: 0,
    });
    expect(proR2v).toEqual({
      duration: 16,
      resolution: '720P',
      aspect_ratio: '3:4',
      watermark: false,
      audio: false,
      seed: 0,
    });
    expect(supportsPlaygroundNegativePrompt('viduq3-pro-r2v')).toBe(false);
    for (const seed of [-1, 0.5, Number.NaN, Number.POSITIVE_INFINITY, 2147483648]) {
      const draft = { seed };
      expect(buildPlaygroundRequestParameters('viduq3-pro-i2v', draft).seed).toBeUndefined();
      expect(draft.seed).toBe(seed);
      expect(hasInactivePlaygroundParameterDrafts('viduq3-pro-i2v', draft)).toBe(true);
    }
    expect(buildPlaygroundRequestParameters('viduq3-pro-i2v', { seed: 2147483647 }).seed).toBe(2147483647);

  });

  it('filters HappyHorse 1.0 VideoEdit to source-video parameters only', () => {
    expect(resolvePlaygroundParameterContract('happyhorse/happyhorse-1.0-video-edit#v2v')?.modelId)
      .toBe('happyhorse-1.0-video-edit');
    expect(resolvePlaygroundParameterContract('happyhorse/happyhorse-1.0-video-edit#i2v')?.modelId)
      .toBe('happyhorse-1.0-video-edit');

    const effective = getEffectivePlaygroundParameters('happyhorse-1.0-video-edit', {
      duration: 0,
      resolution: '1080P',
      aspect_ratio: '16:9',
      audio_setting: 'origin',
      prompt_extend: false,
      watermark: false,
      seed: 0,
    });

    expect(effective).toEqual({
      resolution: '1080P',
      audio_setting: 'origin',
      watermark: false,
      seed: 0,
    });
    expect(supportsPlaygroundNegativePrompt('happyhorse-1.0-video-edit')).toBe(false);
  });

  it('normalizes migrated Seedance defaults without mutating inactive drafts', () => {
    const draft = { duration: 5, aspect_ratio: '16:9', prompt_extend: false, watermark: false };
    expect(buildPlaygroundRequestParameters('seedance-2.0-i2v', draft)).toEqual({ duration: 5, resolution: '1080p', aspect_ratio: '16:9', audio: true });
    expect(draft.watermark).toBe(false);
  });
});


it('enforces Seedance integer seed bounds without rewriting the draft', () => {
  for (const seed of [-1, 0, 4294967295]) {
    expect(buildPlaygroundRequestParameters('seedance-2.0-t2v', { seed }).seed).toBe(seed);
  }
  for (const seed of [-2, 4294967296, 1.5]) {
    const draft = { seed };
    expect(buildPlaygroundRequestParameters('seedance-2.0-t2v', draft)).not.toHaveProperty('seed');
    expect(draft.seed).toBe(seed);
  }
});
