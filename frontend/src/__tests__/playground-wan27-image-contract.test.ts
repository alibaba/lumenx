import { describe, expect, it } from 'vitest';
import { buildWan27ImageRequestParameters, correctWan27ImageDraft, isWan27TextToImage, wan27ImageDraftIssues } from '@/components/modules/playground/playgroundImageContract';
import { getModelParams } from '@/components/modules/playground/playgroundModels';
import { resolveMigratedPlaygroundVideoModelId } from '@/components/modules/playground/playgroundModelAliases';
import type { PlaygroundParameters } from '@/components/modules/playground/usePlaygroundStore';

const ids = ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'];
describe('strict image draft boundaries', () => {
  it.each(ids)('supports every catalog size without inventing video migration for %s', (modelId) => {
    expect(isWan27TextToImage(modelId, 't2i', [])).toBe(true);
    expect(resolveMigratedPlaygroundVideoModelId(modelId)).toBeNull();
    expect(isWan27TextToImage(modelId, 't2i', ['ref.png'])).toBe(false);
    expect(isWan27TextToImage(modelId, 'i2i', [])).toBe(false);
    const sizes = getModelParams(modelId)?.size;
    expect(sizes?.default).toBe('1280*1280');
    expect(sizes?.options.length).toBeGreaterThan(0);
    for (const size of sizes!.options) expect(wan27ImageDraftIssues({ modelId, mode: 't2i', inputMedia: [], parameters: { size, seed: 2147483647, watermark: true }, negativePrompt: '' })).toEqual([]);
  });
  it.each(['wan2.7-image-fast', 'wan2.7-image-pro#t2i', 'wan/wan2.7-image#t2i', 'wan2.7-t2v', 'constructor', '__proto__', 'toString'])('does not treat %s as one of the four image IDs', (modelId) => {
    expect(isWan27TextToImage(modelId, 't2i', [])).toBe(false);
  });
  it('only omits undefined during serialization, leaving invalid null and unknown fields for the gate', () => {
    const parameters = JSON.parse('{"__proto__":false,"constructor":null,"toString":false,"seed":0,"watermark":false}');
    parameters.missing = undefined;
    const serialized = buildWan27ImageRequestParameters(parameters);
    expect(Object.keys(serialized)).toEqual(['__proto__', 'constructor', 'toString', 'seed', 'watermark']);
    expect(Object.getPrototypeOf(serialized)).toBe(Object.prototype);
    const draft = { modelId: ids[0], mode: 't2i' as const, inputMedia: [], parameters, negativePrompt: '' };
    expect(wan27ImageDraftIssues(draft).map((issue) => issue.key)).toEqual(['__proto__', 'constructor', 'toString']);
    expect(correctWan27ImageDraft(draft).parameters).toEqual({ seed: 0, watermark: false, missing: undefined });
    expect(Object.keys(parameters)).toContain('__proto__');
  });
  it('explicit correction distinguishes top-level negative text from an unsupported parameter with the same name', () => {
    const draft = { modelId: ids[0], mode: 't2i' as const, inputMedia: [], parameters: { negativePrompt: { saved: true }, prompt_extend: null, seed: 0, watermark: false } as unknown as PlaygroundParameters, negativePrompt: 'original' };
    const corrected = correctWan27ImageDraft(draft);
    expect(corrected).toEqual({ parameters: { seed: 0, watermark: false }, negativePrompt: '' });
    expect(draft.parameters).toHaveProperty('prompt_extend', null);
    expect(wan27ImageDraftIssues({ ...draft, ...corrected })).toEqual([]);
  });
});
