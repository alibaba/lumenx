import { describe, expect, it } from 'vitest';

import { getModelOption, getModelsForMode, isReferenceImagePathAllowed } from '@/components/modules/playground/playgroundModels';

describe('playground model catalog filtering', () => {
  it('keeps active workspace-gateway Wan 3 video models out of legacy Playground lists', () => {
    for (const mode of ['t2v', 'i2v', 'r2v', 'v2v'] as const) {
      const ids = getModelsForMode(mode).map((model) => model.id);
      expect(ids).not.toContain('wan3.0-video');
      expect(ids).not.toContain('wan3.0-video-prime');
    }
  });

  it('preserves existing hidden legacy Playground exposure', () => {
    expect(getModelsForMode('t2v').map((model) => model.id)).toContain('happyhorse-1.1-t2v');
    expect(getModelsForMode('i2v').map((model) => model.id)).toContain('happyhorse-1.1-i2v');
    expect(getModelsForMode('r2v').map((model) => model.id)).toContain('happyhorse-1.1-r2v');
  });

  it('exposes Vidu Q3 workspace models with WebP reference support', () => {
    expect(getModelsForMode('i2v').map((model) => model.id)).toEqual(
      expect.arrayContaining(['viduq3-pro-i2v', 'viduq3-turbo-i2v']),
    );
    expect(getModelsForMode('r2v').map((model) => model.id)).toEqual(
      expect.arrayContaining(['viduq3-pro-r2v', 'viduq3-turbo-r2v']),
    );

    expect(getModelOption('vidu/viduq3-pro-video#i2v')?.referenceImageMimeTypes).toEqual([
      'image/jpeg',
      'image/png',
      'image/webp',
    ]);
    expect(isReferenceImagePathAllowed('output/history-frame.WEBP', ['image/jpeg', 'image/png', 'image/webp'])).toBe(true);
    expect(isReferenceImagePathAllowed('output/history-frame.webp?cache=1', ['image/jpeg', 'image/png', 'image/webp'])).toBe(true);
  });
});
