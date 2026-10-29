import { describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { playgroundApi } from '@/lib/api';
import { parsePlaygroundCapabilities, seedanceInputBlockReason, seedanceNotAdmittedReason } from '@/lib/playgroundCapabilities';

const ready = { schema_version: 1, seedance: { available: true, reason: null, channel: 'mulerouter_http', input_kinds: ['image'] } };
describe('Seedance capability contract', () => {
  it('accepts the exact HTTP and unavailable contracts', () => {
    expect(parsePlaygroundCapabilities(ready)).toEqual(ready);
    for (const reason of ['seedance_http_key_required', 'seedance_http_endpoint_unsupported', 'seedance_cli_submission_unsafe']) {
      expect(parsePlaygroundCapabilities({ schema_version: 1, seedance: { available: false, reason, channel: null, input_kinds: ['image'] } }).seedance.reason).toBe(reason);
    }
  });
  it.each([null, {}, { ...ready, schema_version: 2 }, { ...ready, extra: true },
    { ...ready, seedance: { ...ready.seedance, available: false } },
    { ...ready, seedance: { ...ready.seedance, reason: 'secret text' } },
    { ...ready, seedance: { ...ready.seedance, input_kinds: ['image', 'video'] } },
  ])('rejects malformed or contradictory capability data %j', raw => {
    expect(() => parsePlaygroundCapabilities(raw)).toThrow();
  });
  it('gates unsupported media without filtering or reordering it', () => {
    const refs = ['output/a.png', 'output/b.mp4', 'output/c.wav'];
    expect(seedanceInputBlockReason('seedance-2.0-r2v', 'r2v', refs)).toBe('seedance_images_only');
    expect(refs).toEqual(['output/a.png', 'output/b.mp4', 'output/c.wav']);
    expect(seedanceInputBlockReason('seedance/seedance-2.0-video#i2v', 'i2v', [])).toBe('seedance_image_count');
    expect(seedanceInputBlockReason('seedance-2.0-r2v', 'r2v', ['a.png', 'a.png'])).toBeNull();
    expect(seedanceInputBlockReason('seedance-2.0-t2v', 't2v', ['a.png'])).toBe('seedance_image_count');
  });
  it('recognizes only the explicit definite non-admission envelope', () => {
    const detail = { code: 'playground_generation_not_admitted', reason: 'seedance_cli_submission_unsafe' };
    expect(seedanceNotAdmittedReason({ response: { status: 503, data: { detail } } })).toBe(detail.reason);
    expect(seedanceNotAdmittedReason({ response: { status: 404, data: { detail } } })).toBeNull();
    expect(seedanceNotAdmittedReason({ response: { status: 503, data: { detail: { ...detail, code: 'playground_generation_unavailable' } } } })).toBeNull();
    expect(seedanceNotAdmittedReason({ response: { status: 503, data: { detail: { ...detail, reason: 'unknown' } } } })).toBeNull();
  });
});


it('uses the read-only capability endpoint and rejects malformed API data before callers see it', async () => {
  const get = vi.spyOn(axios, 'get');
  try {
    get.mockResolvedValueOnce({ data: ready });
    expect(await playgroundApi.getCapabilities()).toEqual(ready);
    expect(get.mock.calls[0][0]).toMatch(/\/playground\/capabilities$/);
    get.mockResolvedValueOnce({ data: { ...ready, seedance: { ...ready.seedance, channel: 'cli' } } });
    await expect(playgroundApi.getCapabilities()).rejects.toThrow('Invalid Seedance capabilities');
  } finally { get.mockRestore(); }
});
