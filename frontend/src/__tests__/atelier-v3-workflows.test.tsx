// @vitest-environment happy-dom
//
// WorkflowsPanel — local template browser with category filter and an
// Insert button that fires `onInsert(template)` to the shell. Tests
// lock the basic browse + filter + click flow; the actual canvas
// insertion logic lives in the shell and is exercised separately by
// the shell integration tests.
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WorkflowsPanel } from "@/components/atelier/v3/WorkflowsPanel";
import { WORKFLOW_TEMPLATES } from "@/components/atelier/v3/workflowTemplates";

describe("WorkflowsPanel", () => {
  it("lists every default template by name", () => {
    render(<WorkflowsPanel onInsert={vi.fn()} />);
    for (const t of WORKFLOW_TEMPLATES) {
      expect(screen.getByText(t.name)).toBeInTheDocument();
    }
  });

  it("category filter narrows the list", () => {
    render(<WorkflowsPanel onInsert={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Character" }));
    // Only character-category templates should remain visible.
    const characterTemplates = WORKFLOW_TEMPLATES.filter((t) => t.category === "character");
    for (const t of characterTemplates) {
      expect(screen.getByText(t.name)).toBeInTheDocument();
    }
    // A story-only template should disappear.
    const storyTemplate = WORKFLOW_TEMPLATES.find((t) => t.category === "story");
    if (storyTemplate) {
      expect(screen.queryByText(storyTemplate.name)).toBeNull();
    }
  });

  it("clicking Insert (the card itself) calls onInsert with the template", () => {
    const onInsert = vi.fn();
    render(<WorkflowsPanel onInsert={onInsert} />);
    const firstTemplate = WORKFLOW_TEMPLATES[0];
    // Each card is a <button> wrapping the entire row; locate by name.
    const card = screen.getByRole("button", { name: new RegExp(firstTemplate.name) });
    fireEvent.click(card);
    expect(onInsert).toHaveBeenCalledTimes(1);
    expect(onInsert.mock.calls[0][0]).toMatchObject({ id: firstTemplate.id });
  });

  it("default templates declare valid edge sources + targets", () => {
    // Schema integrity: every template edge must reference real local
    // node ids inside its own template. Catches the common 'rename a
    // localId but forget the edge' regression.
    for (const t of WORKFLOW_TEMPLATES) {
      const localIds = new Set(t.nodes.map((n) => n.localId));
      for (const e of t.edges) {
        expect(localIds.has(e.from)).toBe(true);
        expect(localIds.has(e.to)).toBe(true);
      }
    }
  });
});
