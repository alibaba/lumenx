import { describe, expect, it } from "vitest";

import {
  hasBlockingPromptIssues,
  inspectImagePrompt,
  inspectStoryboardPrompt,
  inspectVideoPrompt,
} from "@/lib/prompt-quality";

describe("prompt quality inspector", () => {
  it("blocks fixed-camera and movement conflicts for video prompts", () => {
    const issues = inspectVideoPrompt({
      prompt: "Static camera, then camera pans left while the hero runs forward.",
      workflow: "standard",
      generationMode: "i2v",
    });

    expect(hasBlockingPromptIssues(issues)).toBe(true);
    expect(issues.some((issue) => issue.code === "static_vs_motion")).toBe(true);
  });

  it("warns when realistic image prompts lack human realism cues", () => {
    const issues = inspectImagePrompt({
      prompt: "Character portrait of Lin with a calm expression.",
      target: "headshot",
      stylePrompt: "真人写实电影感",
    });

    expect(issues.some((issue) => issue.code === "realism_signal_weak")).toBe(true);
  });

  it("warns about continuity risk for same-scene storyboard prompts", () => {
    const issues = inspectStoryboardPrompt({
      prompt: "Wide shot of the room as the hero walks to the table.",
      sameSceneContinuity: true,
    });

    expect(issues.some((issue) => issue.code === "storyboard_continuity_missing")).toBe(true);
  });
});
