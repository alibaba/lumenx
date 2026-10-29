import { describe, expect, it } from "vitest";
import { atelierImageDraftIssues, correctAtelierImageDraft, isAtelierQwen20T2I, isAtelierStrictImageT2I, validAtelierQwen20Prompt } from "@/lib/atelierWan27ImageDraft";
import { getCanonicalModeEntry, getCanonicalModeId } from "@/lib/modelCatalog";

const ids = ["qwen-image-2.0", "qwen-image-2.0-pro", "qwen/qwen-image-2.0#image", "qwen/qwen-image-2.0-pro#image"];
describe("Qwen editor strict draft boundary", () => {
  it.each(ids)("uses exact supported T2I ID %s, preserving default omission and supported fields", id => {
    expect(isAtelierQwen20T2I(id, [])).toBe(true); expect(isAtelierStrictImageT2I(id, [])).toBe(true);
    expect(isAtelierQwen20T2I(id, [{}])).toBe(false); expect(isAtelierStrictImageT2I(id, [{}])).toBe(false);
    expect(atelierImageDraftIssues(id, [], {})).toEqual([]);
    const spec = getCanonicalModeEntry(getCanonicalModeId(id) ?? id)!.params as { size: { options: string[] } };
    for (const size of spec.size.options) expect(atelierImageDraftIssues(id, [], { size, negative_prompt: "", seed: 0, watermark: false, prompt_extend: false })).toEqual([]);
    expect(atelierImageDraftIssues(id, [], { seed: 2147483647, negative_prompt: "🌕".repeat(500), prompt_extend: true })).toEqual([]);
  });
  it.each(["qwen-image", "qwen-image-2.0-fast", "qwen-image-2.0-pro-latest", "Qwen-image-2.0", "wan2.6-image", "__proto__"])("does not expand the migration to %s", id => {
    expect(isAtelierQwen20T2I(id, [])).toBe(false); expect(atelierImageDraftIssues(id, [], { seed: -1 })).toEqual([]);
  });
  it.each([{ seed: -1 }, { seed: 0.5 }, { seed: 2147483648 }, { seed: null }, { seed: Number.NaN }, { watermark: "false" }, { prompt_extend: null }, { negative_prompt: null }, { negative_prompt: "x".repeat(501) }, { negative_prompt: "\ud800" }, { negative_prompt: "\udfff" }, { size: "" }, { size: "1*1" }, { custom: 0 }])("reports untouched invalid input %j and only explicitly removes it", params => {
    const before = structuredClone(params);
    expect(atelierImageDraftIssues(ids[0], [], params)).toEqual(Object.entries(params).map(([key, value]) => ({ key, value })));
    expect(correctAtelierImageDraft(ids[0], [], { prompt_extend: false, negative_prompt: "", ...params })).toEqual(Object.fromEntries(Object.entries({ prompt_extend: false, negative_prompt: "" }).filter(([key]) => !(key in params))));
    expect(params).toEqual(before); expect(atelierImageDraftIssues(ids[0], [{}], params)).toEqual([]);
  });
  it("keeps Wan-specific unsupported fields distinct from the Qwen contract", () => {
    const params = { negative_prompt: "", prompt_extend: false };
    expect(atelierImageDraftIssues("wan2.7-image", [], params)).toHaveLength(2);
    expect(atelierImageDraftIssues(ids[0], [], params)).toEqual([]);
  });
  it("checks Unicode and byte length without altering a prompt", () => {
    expect(validAtelierQwen20Prompt(" ")).toBe(false);
    expect(validAtelierQwen20Prompt("\ud800")).toBe(false);
    expect(validAtelierQwen20Prompt("🌕".repeat(4096))).toBe(true);
    expect(validAtelierQwen20Prompt("🌕".repeat(4096) + "a")).toBe(false);
    expect(validAtelierQwen20Prompt("月".repeat(5461) + "a")).toBe(true);
    expect(validAtelierQwen20Prompt("月".repeat(5461) + "ab")).toBe(false);
  });
});
