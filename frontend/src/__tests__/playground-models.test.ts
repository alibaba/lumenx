import { describe, expect, it } from 'vitest';

import { getModelParams } from '@/components/modules/playground/playgroundModels';

describe('playground model params', () => {
  it('exposes per-model prompt max length', () => {
    expect(getModelParams('seedance-2.0-t2v')?.prompt?.maxLength).toBe(2000);
    expect(getModelParams('pixverse/pixverse-v6-video')?.prompt?.maxLength).toBe(2000);
    expect(getModelParams('happyhorse-1.0-i2v')?.prompt?.maxLength).toBe(2000);
  });
});
