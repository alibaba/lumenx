import { describe, expect, it } from "vitest";

import { getPromptConfigPresets } from "@/lib/prompt-config-presets";

describe("prompt config presets", () => {
  it("returns storyboard presets with placeholders intact", () => {
    const items = getPromptConfigPresets("storyboard_polish");

    expect(items.length).toBeGreaterThan(0);
    expect(items[0].prompt).toContain("{ASSETS}");
    expect(items[0].prompt).toContain("{DRAFT}");
  });

  it("returns r2v presets for multi-character blocking", () => {
    const items = getPromptConfigPresets("r2v_polish");
    const ids = items.map((item) => item.id);

    expect(ids).toContain("r2v-dialogue-blocking");
    expect(ids).toContain("r2v-action-blocking");
  });
});
