// @vitest-environment happy-dom
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { BUILT_IN_WORKFLOW_TEMPLATES, WORKFLOW_TEMPLATE_MIME, getBuiltInWorkflowTemplate, validateBuiltInWorkflowTemplate } from "@/components/atelier/v3/workflowTemplates";
import { WorkflowsPanel } from "@/components/atelier/v3/WorkflowsPanel";

describe("Atelier built-in workflow templates", () => {
  it("ships six safe built-ins with unique ids, safe node fields, and valid reference edges", () => {
    expect(BUILT_IN_WORKFLOW_TEMPLATES).toHaveLength(6);
    const ids = new Set<string>();
    for (const template of BUILT_IN_WORKFLOW_TEMPLATES) {
      expect(ids.has(template.id)).toBe(false);
      ids.add(template.id);
      expect(validateBuiltInWorkflowTemplate(template)).toEqual({ ok: true });
      expect(JSON.stringify(template)).not.toMatch(/Wan 2\.7|provider_execution|credential|fingerprint|generation_attempt|candidate|handle|authority/i);
      const localIds = new Set(template.nodes.map(node => node.localId));
      expect(localIds.size).toBe(template.nodes.length);
      for (const node of template.nodes) {
        expect(Object.keys(node).sort()).toEqual(expect.arrayContaining(["height", "localId", "title", "type", "width", "x", "y"]));
        expect("data" in node).toBe(false);
      }
      for (const edge of template.edges) {
        expect(edge.kind).toBe("reference");
        expect(localIds.has(edge.source)).toBe(true);
        expect(localIds.has(edge.target)).toBe(true);
      }
    }
  });

  it("resolves only known built-in ids", () => {
    expect(getBuiltInWorkflowTemplate(BUILT_IN_WORKFLOW_TEMPLATES[0].id)?.id).toBe(BUILT_IN_WORKFLOW_TEMPLATES[0].id);
    expect(getBuiltInWorkflowTemplate("missing-template")).toBeNull();
  });

  it("filters cards, inserts by click, and drags only the template id", () => {
    const onInsert = vi.fn();
    render(<WorkflowsPanel labels={{ title: "Workflows", all: "All", insert: "Insert", drag: "Drag" }} disabled={false} onInsert={onInsert} />);
    expect(screen.getAllByRole("button", { name: /Insert/ })).toHaveLength(6);
    fireEvent.click(screen.getByRole("tab", { name: "Image" }));
    expect(screen.getAllByRole("button", { name: /Insert/ }).length).toBeLessThan(6);
    fireEvent.click(screen.getAllByRole("button", { name: /Insert/ })[0]);
    expect(onInsert).toHaveBeenCalledWith(expect.objectContaining({ category: "image" }));

    const card = screen.getAllByTestId("workflow-template-card")[0];
    const setData = vi.fn();
    fireEvent.dragStart(card, { dataTransfer: { setData, effectAllowed: "" } });
    expect(setData).toHaveBeenCalledWith(WORKFLOW_TEMPLATE_MIME, expect.any(String));
    expect(setData.mock.calls[0][1]).not.toContain("{");
  });

  it("does not call insert for disabled panels", () => {
    const onInsert = vi.fn();
    render(<WorkflowsPanel labels={{ title: "Workflows", all: "All", insert: "Insert", drag: "Drag" }} disabled onInsert={onInsert} />);
    fireEvent.click(within(screen.getAllByTestId("workflow-template-card")[0]).getByRole("button", { name: /Insert/ }));
    expect(onInsert).not.toHaveBeenCalled();
  });
});
