import { getCanonicalModeEntry, getCanonicalModeId } from "./modelCatalog";
import type { AtelierImageGenerationParams } from "./atelierTypes";

const WAN27_IMAGE_IDS = new Set([
  "wan2.7-image", "wan2.7-image-pro",
  "wan/wan2.7-image#image", "wan/wan2.7-image-pro#image",
]);

export function isAtelierWan27T2I(modelId: string, media: readonly unknown[]): boolean {
  return WAN27_IMAGE_IDS.has(modelId) && media.length === 0;
}

const QWEN20_IMAGE_IDS = new Set([
  "qwen-image-2.0", "qwen-image-2.0-pro",
  "qwen/qwen-image-2.0#image", "qwen/qwen-image-2.0-pro#image",
]);

export function isAtelierQwen20T2I(modelId: string, media: readonly unknown[]): boolean {
  return QWEN20_IMAGE_IDS.has(modelId) && media.length === 0;
}

export function isAtelierStrictImageT2I(modelId: string, media: readonly unknown[]): boolean {
  return isAtelierWan27T2I(modelId, media) || isAtelierQwen20T2I(modelId, media);
}

function validUnicode(value: string): boolean {
  return !Array.from(value).some(char => char.length === 1 && char.charCodeAt(0) >= 0xd800 && char.charCodeAt(0) <= 0xdfff);
}

/** Same local UTF-8 byte bound as the Qwen Core admission contract. */
export function validAtelierQwen20Prompt(prompt: string): boolean {
  return prompt.trim().length > 0 && validUnicode(prompt) && new TextEncoder().encode(prompt).length <= 16 * 1024;
}

export type AtelierWan27ImageDraftIssue = { key: string; value: unknown };

/** Inspect the original editor values before any legacy parameter projection. */
export function atelierWan27ImageDraftIssues(
  modelId: string, media: readonly unknown[], params: AtelierImageGenerationParams,
): AtelierWan27ImageDraftIssue[] {
  if (!isAtelierWan27T2I(modelId, media)) return [];
  return atelierImageDraftIssues(modelId, media, params);
}

export function atelierImageDraftIssues(
  modelId: string, media: readonly unknown[], params: Record<string, unknown>,
): AtelierWan27ImageDraftIssue[] {
  if (!isAtelierStrictImageT2I(modelId, media)) return [];
  const qwen = isAtelierQwen20T2I(modelId, media);
  const spec = getCanonicalModeEntry(getCanonicalModeId(modelId) ?? modelId)?.params;
  const sizeSpec = spec && typeof spec === "object" && "size" in spec ? spec.size : null;
  const sizes = sizeSpec && typeof sizeSpec === "object" && "options" in sizeSpec && Array.isArray(sizeSpec.options)
    ? sizeSpec.options : [];
  return Object.entries(params).flatMap(([key, value]) => {
    if (key === "size" && typeof value === "string" && sizes.includes(value)) return [];
    if (key === "seed" && typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 2147483647) return [];
    if (key === "watermark" && typeof value === "boolean") return [];
    if (qwen && key === "prompt_extend" && typeof value === "boolean") return [];
    if (qwen && key === "negative_prompt" && typeof value === "string" && validUnicode(value) && Array.from(value).length <= 500) return [];
    return [{ key, value }];
  });
}

/** Explicit editor correction only: this never saves a draft or starts generation. */
export function correctAtelierWan27ImageDraft(
  modelId: string, media: readonly unknown[], params: AtelierImageGenerationParams,
): AtelierImageGenerationParams {
  const invalid = new Set(atelierWan27ImageDraftIssues(modelId, media, params).map(issue => issue.key));
  return Object.fromEntries(Object.entries(params).filter(([key]) => !invalid.has(key)));
}

/** Shared editor correction; old receipts are never passed through this gate. */
export function correctAtelierImageDraft(
  modelId: string, media: readonly unknown[], params: Record<string, unknown>,
): AtelierImageGenerationParams {
  const invalid = new Set(atelierImageDraftIssues(modelId, media, params).map(issue => issue.key));
  return Object.fromEntries(Object.entries(params).filter(([key]) => !invalid.has(key))) as AtelierImageGenerationParams;
}
