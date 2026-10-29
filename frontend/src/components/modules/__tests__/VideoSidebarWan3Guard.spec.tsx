import { describe, expect, it } from 'vitest';
import { LEGACY_VIDEO_I2V_MODELS, resolveLegacyVideoSidebarModelId } from '@/lib/legacyVideoSidebarModels';

describe('legacy VideoSidebar Wan3 guard', () => {
  it('keeps ordered Wan3 models out of the legacy motion generator model list', () => {
    const ids = LEGACY_VIDEO_I2V_MODELS.map(model => model.id);

    expect(ids).not.toContain('wan3.0-video');
    expect(ids).not.toContain('wan3.0-video-prime');
    expect(ids).toContain('wan2.7-i2v');
  });

  it('falls stale Wan3 defaults back to a legacy-visible model without changing existing legacy selections', () => {
    expect(resolveLegacyVideoSidebarModelId('wan2.7-i2v')).toBe('wan2.7-i2v');
    expect(resolveLegacyVideoSidebarModelId('wan3.0-video')).not.toMatch(/^wan3\.0-/);
    expect(resolveLegacyVideoSidebarModelId('wan/wan3.0-video#video')).not.toMatch(/^wan3\.0-/);
  });
});
