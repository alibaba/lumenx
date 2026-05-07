import { describe, expect, it } from "vitest";

import {
  applySeedancePromptBlock,
  getSeedancePromptScaffolds,
  getSeedancePromptTemplates,
} from "@/lib/seedance-prompts";

describe("seedance prompt library", () => {
  it("filters workflow scaffolds for extend mode", () => {
    const items = getSeedancePromptScaffolds({
      generationMode: "i2v",
      workflow: "extend",
    });

    const ids = items.map((item) => item.id);
    expect(ids).toContain("workflow-extend");
    expect(ids).not.toContain("workflow-edit");
  });

  it("filters workflow-mode specific templates for object_edit", () => {
    const items = getSeedancePromptTemplates({
      generationMode: "i2v",
      workflow: "edit",
      workflowMode: "object_edit",
    });

    const ids = items.map((item) => item.id);
    expect(ids).toContain("workflow-edit-object-cleanup");
    expect(ids).not.toContain("workflow-edit-subject-swap-clean");
  });

  it("filters templates for r2v context", () => {
    const items = getSeedancePromptTemplates({
      generationMode: "r2v",
      workflow: "standard",
    });

    const ids = items.map((item) => item.id);
    expect(ids).toContain("r2v-dialogue-closeup");
    expect(ids).toContain("short-drama-emotional-turn");
    expect(ids).not.toContain("cinematic-neon-reveal");
  });

  it("replaces current prompt when mode is replace", () => {
    expect(applySeedancePromptBlock("old prompt", "new template", "replace")).toBe(
      "new template",
    );
  });

  it("appends current prompt with spacing when mode is append", () => {
    expect(applySeedancePromptBlock("old prompt", "new template", "append")).toBe(
      "old prompt\n\nnew template",
    );
  });
});
