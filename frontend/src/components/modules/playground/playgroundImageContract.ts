import { resolveQwen20PlaygroundImageModelId, resolveWan27PlaygroundImageModelId } from './playgroundModelAliases';
import { getModelParams } from './playgroundModels';
import type { PlaygroundMode, PlaygroundParameters } from './usePlaygroundStore';

interface ImageDraft {
  modelId: string;
  mode: PlaygroundMode;
  inputMedia: readonly string[];
  parameters: PlaygroundParameters;
  negativePrompt: string;
  prompt?: string;
}

export interface ImageDraftIssue {
  source: 'parameter' | 'negativePrompt' | 'prompt';
  key: string;
  value: unknown;
}

export function effectivePlaygroundMode(mode: PlaygroundMode, inputMedia: readonly string[]): PlaygroundMode {
  return mode === 't2i' && inputMedia.length > 0 ? 'i2i' : mode;
}

export function isWan27TextToImage(modelId: string, mode: PlaygroundMode, inputMedia: readonly string[]): boolean {
  return resolveWan27PlaygroundImageModelId(modelId) !== null && effectivePlaygroundMode(mode, inputMedia) === 't2i';
}

export function isQwen20TextToImage(modelId: string, mode: PlaygroundMode, inputMedia: readonly string[]): boolean {
  return resolveQwen20PlaygroundImageModelId(modelId) !== null && effectivePlaygroundMode(mode, inputMedia) === 't2i';
}

function isUnicodeText(value: string): boolean {
  return !Array.from(value).some((character) => character.length === 1 && character.charCodeAt(0) >= 0xd800 && character.charCodeAt(0) <= 0xdfff);
}

/** New Qwen drafts use the visible negative field; retained nested values need a choice. */
export function qwen20ImageDraftIssues(draft: ImageDraft): ImageDraftIssue[] {
  if (!isQwen20TextToImage(draft.modelId, draft.mode, draft.inputMedia)) return [];
  const issues: ImageDraftIssue[] = [];
  const sizes = getModelParams(draft.modelId)?.size?.options ?? [];
  for (const [key, value] of Object.entries(draft.parameters)) {
    if (value === undefined) continue;
    if (key === 'size' && typeof value === 'string' && sizes.includes(value)) continue;
    if (key === 'seed' && typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 2147483647) continue;
    if ((key === 'watermark' || key === 'prompt_extend') && typeof value === 'boolean') continue;
    issues.push({ source: 'parameter', key, value });
  }
  if (Array.from(draft.negativePrompt).length > 500 || !isUnicodeText(draft.negativePrompt)) {
    issues.push({ source: 'negativePrompt', key: 'negativePrompt', value: draft.negativePrompt });
  }
  if (draft.prompt !== undefined && (!draft.prompt.trim() || !isUnicodeText(draft.prompt) || new TextEncoder().encode(draft.prompt.trim()).length > 16384)) {
    issues.push({ source: 'prompt', key: 'prompt', value: draft.prompt });
  }
  return issues;
}

export function qwen20ImageRetryNeedsEditing(draft: ImageDraft): boolean {
  return isQwen20TextToImage(draft.modelId, draft.mode, [])
    && (draft.inputMedia.length > 0 || qwen20ImageDraftIssues(draft).length > 0);
}

/** Keep author text and both negative sources intact; only remove invalid settings. */
export function correctQwen20ImageDraft(draft: ImageDraft): Pick<ImageDraft, 'parameters'> {
  const parameters = { ...draft.parameters };
  for (const issue of qwen20ImageDraftIssues(draft)) {
    if (issue.source === 'parameter' && issue.key !== 'negative_prompt') delete parameters[issue.key];
  }
  return { parameters };
}

/** Validate the saved draft without normalizing or dropping explicit values. */
export function wan27ImageDraftIssues(draft: ImageDraft): ImageDraftIssue[] {
  if (!isWan27TextToImage(draft.modelId, draft.mode, draft.inputMedia)) return [];
  const issues: ImageDraftIssue[] = [];
  const sizes = getModelParams(draft.modelId)?.size?.options ?? [];
  for (const [key, value] of Object.entries(draft.parameters)) {
    // Undefined is the existing control's representation of an omitted value.
    if (value === undefined) continue;
    if (key === 'size' && typeof value === 'string' && sizes.includes(value)) continue;
    if (key === 'seed' && typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 2147483647) continue;
    if (key === 'watermark' && typeof value === 'boolean') continue;
    issues.push({ source: 'parameter', key, value });
  }
  if (draft.negativePrompt.trim()) issues.push({ source: 'negativePrompt', key: 'negativePrompt', value: draft.negativePrompt });
  return issues;
}

/** A historical request keeps its declared mode; media requires a return to editing. */
export function wan27ImageRetryNeedsEditing(draft: ImageDraft): boolean {
  return isWan27TextToImage(draft.modelId, draft.mode, [])
    && (draft.inputMedia.length > 0 || wan27ImageDraftIssues(draft).length > 0);
}

/** Called only by the explicit correction button; this never submits a request. */
export function correctWan27ImageDraft(draft: ImageDraft): Pick<ImageDraft, 'parameters' | 'negativePrompt'> {
  const parameters = { ...draft.parameters };
  let negativePrompt = draft.negativePrompt;
  for (const issue of wan27ImageDraftIssues(draft)) {
    if (issue.source === 'negativePrompt') negativePrompt = '';
    else delete parameters[issue.key];
  }
  return { parameters, negativePrompt };
}

export function imageDraftValueText(value: unknown): string {
  return JSON.stringify(value) ?? String(value);
}

/** Keep strict T2I history/drafts intact; retain the existing primitive projection elsewhere. */
export function readPlaygroundParameters(
  parameters: Record<string, unknown> | null | undefined,
  modelId: string,
  mode: string,
): PlaygroundParameters {
  if ((resolveWan27PlaygroundImageModelId(modelId) || resolveQwen20PlaygroundImageModelId(modelId)) && mode === 't2i') return { ...parameters };
  return Object.fromEntries(Object.entries(parameters ?? {}).filter(([, value]) => (
    typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
  )));
}

/** Undefined is an editor-only omission marker, not a JSON value. Keep every other value. */
export function buildWan27ImageRequestParameters(parameters: PlaygroundParameters): PlaygroundParameters {
  return Object.fromEntries(Object.entries(parameters).filter(([, value]) => value !== undefined));
}
