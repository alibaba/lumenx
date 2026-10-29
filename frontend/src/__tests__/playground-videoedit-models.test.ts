import { describe, expect, it } from 'vitest';

import {
  getModelDisplayInfo,
  getModelDuration,
  getModelOption,
  getModelParams,
  getModelsForMode,
} from '@/components/modules/playground/playgroundModels';

describe('Wan 2.7 VideoEdit Playground catalog metadata', () => {
  it('exposes the legacy VideoEdit model for v2v with source-duration and audio controls', () => {
    const ids = getModelsForMode('v2v').map((model) => model.id);
    expect(ids).toContain('wan2.7-videoedit');

    expect(getModelDuration('wan2.7-videoedit')).toEqual({
      type: 'buttons',
      options: [0, 2, 3, 4, 5, 6, 7, 8, 9, 10],
      default: 0,
    });

    const params = getModelParams('wan2.7-videoedit');
    expect(params?.resolution).toEqual({ options: ['720P', '1080P'], default: '1080P' });
    expect(params?.audioSetting).toEqual({ options: ['auto', 'origin'], default: 'auto' });
  });

  it('exposes HappyHorse 1.0 VideoEdit for v2v with source-duration parameters', () => {
    const ids = getModelsForMode('v2v').map((model) => model.id);
    expect(ids).toContain('happyhorse-1.0-video-edit');

    expect(getModelOption('happyhorse/happyhorse-1.0-video-edit#v2v')?.id).toBe('happyhorse-1.0-video-edit');
    expect(getModelOption('happyhorse/happyhorse-1.0-video-edit#i2v')?.id).toBe('happyhorse-1.0-video-edit');
    expect(getModelDuration('happyhorse-1.0-video-edit')).toBeNull();

    const params = getModelParams('happyhorse-1.0-video-edit');
    expect(params?.resolution).toEqual({ options: ['720P', '1080P'], default: '1080P' });
    expect(params?.audioSetting).toEqual({ options: ['auto', 'origin'], default: 'auto' });
    expect(params?.watermark).toEqual({ default: true });
    expect(params?.negativePrompt).toBeUndefined();
    expect(params?.promptExtend).toBeUndefined();
  });

  it('resolves canonical aliases for lookup helpers without exposing Wan 3 workspace models', () => {
    expect(getModelOption('wan/wan2.7-video#v2v')?.id).toBe('wan2.7-videoedit');
    expect(getModelDisplayInfo('happyhorse/happyhorse-1.1-video#i2v')?.displayName).toBe('HappyHorse I2V');
    expect(getModelDuration('happyhorse/happyhorse-1.1-video#r2v')).toEqual({
      type: 'slider',
      min: 3,
      max: 15,
      step: 1,
      default: 5,
    });
    expect(getModelParams('happyhorse/happyhorse-1.1-video#i2v')?.watermark).toEqual({ default: true });

    for (const mode of ['t2v', 'i2v', 'r2v', 'v2v'] as const) {
      const ids = getModelsForMode(mode).map((model) => model.id);
      expect(ids).not.toContain('wan3.0-video');
      expect(ids).not.toContain('wan3.0-video-prime');
    }
  });
});
