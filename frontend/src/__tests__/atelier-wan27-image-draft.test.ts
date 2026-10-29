import { describe, expect, it } from "vitest";
import { atelierWan27ImageDraftIssues, correctAtelierWan27ImageDraft, isAtelierWan27T2I } from "@/lib/atelierWan27ImageDraft";
import { getCanonicalModeEntry, getCanonicalModeId } from "@/lib/modelCatalog";
import type { AtelierImageGenerationParams } from "@/lib/atelierTypes";

const ids = ["wan2.7-image", "wan2.7-image-pro", "wan/wan2.7-image#image", "wan/wan2.7-image-pro#image"];
describe("Atelier Wan2.7 new-image draft gate", () => {
  it.each(ids)("accepts only exact T2I and catalog dimensions for %s", model => {
    expect(isAtelierWan27T2I(model, [])).toBe(true);
    expect(isAtelierWan27T2I(model, [{}])).toBe(false);
    expect(atelierWan27ImageDraftIssues(model, [], {})).toEqual([]);
    const spec = getCanonicalModeEntry(getCanonicalModeId(model) ?? model)!.params as { size: { options: string[] } };
    const size = spec.size;
    for (const option of size.options) expect(atelierWan27ImageDraftIssues(model, [], { size: option, seed: 0, watermark: false })).toEqual([]);
    expect(atelierWan27ImageDraftIssues(model, [], { seed: 2147483647, watermark: true })).toEqual([]);
  });
  it.each(["wan2.7-image-fast", "wan2.6-image", "wanx2.1-t2i", "gpt-image/gpt-image-2#image", "__proto__"])("leaves %s outside this product gate", model => {
    expect(isAtelierWan27T2I(model, [])).toBe(false);
    expect(atelierWan27ImageDraftIssues(model, [], { prompt_extend: false, seed: -1 })).toEqual([]);
  });
  it.each([{ negative_prompt: "" }, { negative_prompt: " " }, { prompt_extend: false }, { seed: -1 }, { seed: 0.5 }, { seed: 2147483648 }, { seed: undefined }, { seed: null }, { watermark: "false" }, { size: "" }, { size: "1024x1024" }, { custom: false }])("reports original invalid values without normalization: %j", raw => {
    const params = raw as AtelierImageGenerationParams;
    const before = { ...params };
    expect(atelierWan27ImageDraftIssues(ids[0], [], params)).toEqual(Object.entries(raw).map(([key, value]) => ({ key, value })));
    expect(params).toEqual(before);
    expect(atelierWan27ImageDraftIssues(ids[0], [{}], params)).toEqual([]);
  });
  it("explicitly removes only invalid fields, preserving valid zero/false and original params", () => {
    const params = { size: "1024*768", seed: 0, watermark: false, negative_prompt: "", prompt_extend: false };
    expect(correctAtelierWan27ImageDraft(ids[0], [], params)).toEqual({ size: "1024*768", seed: 0, watermark: false });
    expect(params).toHaveProperty("negative_prompt", "");
    expect(correctAtelierWan27ImageDraft(ids[0], [{}], params)).toEqual(params);
  });
});
