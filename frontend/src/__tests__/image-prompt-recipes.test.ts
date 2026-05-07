import { describe, expect, it } from "vitest";

import {
  applyImagePromptBlock,
  buildDefaultImagePrompt,
  detectImageStyleMode,
  getImagePromptTemplates,
} from "@/lib/image-prompt-recipes";

describe("image prompt recipes", () => {
  it("detects photoreal style mode from style prompt", () => {
    expect(detectImageStyleMode("真人电影感，写实摄影")).toBe("photoreal");
  });

  it("builds a live-action leaning default character prompt", () => {
    const prompt = buildDefaultImagePrompt({
      target: "full_body",
      name: "Lin",
      description: "Short black hair and a charcoal trench coat",
      stylePrompt: "写实电影感",
    });

    expect(prompt).toContain("Live-action human subject");
    expect(prompt).toContain("Avoid anime rendering");
    expect(prompt).toContain("Full-body hero reference of Lin.");
  });

  it("filters stylized headshot template when style is anime-like", () => {
    const items = getImagePromptTemplates({
      target: "headshot",
      stylePrompt: "动漫插画角色",
    });

    const ids = items.map((item) => item.id);
    expect(ids).toContain("headshot-stylized-key-visual");
  });

  it("appends image prompt blocks with spacing", () => {
    expect(applyImagePromptBlock("base prompt", "extra detail", "append")).toBe(
      "base prompt\n\nextra detail",
    );
  });
});
