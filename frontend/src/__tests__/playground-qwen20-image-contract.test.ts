import { beforeEach, describe, expect, it } from 'vitest';
import { correctQwen20ImageDraft, qwen20ImageDraftIssues, qwen20ImageRetryNeedsEditing, readPlaygroundParameters } from '@/components/modules/playground/playgroundImageContract';
import { resolvePlaygroundCatalogModelId, resolveQwen20PlaygroundImageModelId, resolveMigratedPlaygroundVideoModelId } from '@/components/modules/playground/playgroundModelAliases';
import { usePlaygroundStore, type PlaygroundParameters } from '@/components/modules/playground/usePlaygroundStore';

const ids = ['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'];
function draft(parameters: Record<string, unknown> = {}) {
  return { modelId: ids[0], mode: 't2i' as const, prompt: 'A glass moon', negativePrompt: '', parameters: parameters as PlaygroundParameters, inputMedia: [] as string[] };
}
describe('Qwen product-local image contract', () => {
  it.each(ids)('resolves only the exact catalog alias %s', modelId => {
    const expected = modelId.split('/').at(-1)!.split('#')[0];
    expect(resolvePlaygroundCatalogModelId(modelId)).toBe(expected);
    expect(resolveQwen20PlaygroundImageModelId(modelId)).toBe(expected);
    expect(resolveMigratedPlaygroundVideoModelId(modelId)).toBeNull();
  });
  it.each([' qwen-image-2.0', 'qwen-image-2.0 ', 'qwen/qwen-image-2.0#video', 'Qwen-image-2.0', 'qwen-image-2', 'constructor', '__proto__', 'wan2.7-image'])('does not broaden Qwen aliases to %s', model => {
    expect(resolveQwen20PlaygroundImageModelId(model)).toBeNull();
  });
  it.each([{ size: '1280*1280' }, { size: '768*1024' }, { seed: 0 }, { seed: 2147483647 }, { seed: undefined }, { watermark: false, prompt_extend: false }, {}])('accepts explicit settings without inventing defaults: %j', parameters => {
    const original = structuredClone(parameters); expect(qwen20ImageDraftIssues(draft(parameters))).toEqual([]); expect(parameters).toEqual(original);
  });
  it('counts Unicode characters rather than UTF-16 units in negative text', () => {
    expect(qwen20ImageDraftIssues({ ...draft(), negativePrompt: '🌙'.repeat(500) })).toEqual([]);
    expect(qwen20ImageDraftIssues({ ...draft(), negativePrompt: '🌙'.repeat(501) })).toMatchObject([{ source: 'negativePrompt' }]);
  });
  it.each(['\ud800', '\udc00', 'a\ud800x', '🌙\udc00'])('rejects invalid Unicode before JSON coercion: %j', prompt => {
    expect(qwen20ImageDraftIssues({ ...draft(), prompt })).toMatchObject([{ source: 'prompt' }]);
    expect(qwen20ImageDraftIssues({ ...draft(), negativePrompt: prompt })).toMatchObject([{ source: 'negativePrompt' }]);
  });
  it('enforces the existing UTF-8 prompt byte limit while retaining original text', () => {
    expect(qwen20ImageDraftIssues({ ...draft(), prompt: '月'.repeat(5461) })).toEqual([]);
    const oversized = '月'.repeat(5462);
    expect(qwen20ImageDraftIssues({ ...draft(), prompt: oversized })).toEqual([{ source: 'prompt', key: 'prompt', value: oversized }]);
  });
  it.each([null, false, 0, { original: 'retained' }, 'saved', ''])('retains nested negative %j for an explicit choice', negative => {
    const input = draft({ negative_prompt: negative, seed: -1, watermark: false });
    expect(qwen20ImageDraftIssues(input)).toHaveLength(2);
    expect(correctQwen20ImageDraft(input)).toEqual({ parameters: { negative_prompt: negative, watermark: false } });
    expect(input.parameters).toEqual({ negative_prompt: negative, seed: -1, watermark: false });
  });
  it('preserves raw T2I drafts but retains the old I2I primitive projection', () => {
    const parameters = { size: null, prompt_extend: false, custom: { kept: true } };
    expect(readPlaygroundParameters(parameters, ids[2], 't2i')).toEqual(parameters);
    expect(readPlaygroundParameters(parameters, ids[2], 'i2i')).toEqual({ prompt_extend: false });
    expect(qwen20ImageDraftIssues({ ...draft({ seed: -1 }), inputMedia: ['uploads/ref.png'] })).toEqual([]);
    expect(qwen20ImageRetryNeedsEditing({ ...draft({ seed: -1 }), inputMedia: ['uploads/ref.png'] })).toBe(true);
  });
});

describe('negative prompt provenance in the existing store', () => {
  beforeEach(() => usePlaygroundStore.getState().resetInput());
  it('distinguishes untouched, explicitly cleared and reset negative values', () => {
    expect(usePlaygroundStore.getState().negativePromptProvided).toBe(false);
    usePlaygroundStore.getState().setNegativePrompt('old'); usePlaygroundStore.getState().setNegativePrompt('');
    expect(usePlaygroundStore.getState()).toMatchObject({ negativePrompt: '', negativePromptProvided: true });
    usePlaygroundStore.getState().resetInput(); expect(usePlaygroundStore.getState().negativePromptProvided).toBe(false);
  });
  it.each([undefined, null, '', 'text'])('restores template presence without truthy coercion: %j', negative => {
    usePlaygroundStore.getState().applyTemplate({ id: 'template', name: 'saved', category: 'image', prompt: 'moon', negative_prompt: negative, created_at: 'now', updated_at: 'now' });
    expect(usePlaygroundStore.getState().negativePromptProvided).toBe(typeof negative === 'string');
    expect(usePlaygroundStore.getState().negativePrompt).toBe(negative ?? '');
  });
});
