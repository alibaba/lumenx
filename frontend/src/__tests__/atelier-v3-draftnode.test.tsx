// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DraftNode } from "@/components/atelier/v3/DraftNode";

describe("DraftNode", () => {
  it("renders intent, model, configSummary", () => {
    render(
      <DraftNode
        id="d1"
        status="draft"
        intent="Cinematic"
        modelLabel="Wan 2.7"
        configSummary="1280×720 · 5s · 4×"
        x={0}
        y={0}
      />,
    );
    expect(screen.getByText("Cinematic")).toBeInTheDocument();
    expect(screen.getByText("Wan 2.7")).toBeInTheDocument();
    expect(screen.getByText(/1280×720/)).toBeInTheDocument();
  });

  it("surfaces status=draft via the StatusDot, not a border tint", () => {
    // Per ornaments.tsx STATUS_TOKEN comment: lifecycle status is no longer
    // painted on the body/border/rail — it lives solely on the StatusDot
    // (top-right) + the muted footer caption, so the node reads identically
    // collapsed and expanded. Assert the StatusDot accessible name.
    render(
      <DraftNode
        id="d2"
        status="draft"
        intent="X"
        modelLabel="M"
        configSummary="—"
        x={0}
        y={0}
      />,
    );
    expect(
      screen.getByRole("status", { name: /awaiting approval/i }),
    ).toBeInTheDocument();
  });

  it("surfaces status=approved via the StatusDot, not a border tint", () => {
    // Same as above: approved is signaled by the StatusDot caption
    // ("Approved" per STATUS_TOKEN), not by tinting the card border.
    render(
      <DraftNode
        id="d3"
        status="approved"
        intent="X"
        modelLabel="M"
        configSummary="—"
        x={0}
        y={0}
      />,
    );
    expect(
      screen.getByRole("status", { name: /approved/i }),
    ).toBeInTheDocument();
  });

  it("renders ref thumbnails when refs provided", () => {
    render(
      <DraftNode
        id="d4"
        status="draft"
        intent="X"
        modelLabel="M"
        configSummary="—"
        x={0}
        y={0}
        refs={["a.jpg", "b.jpg", "c.jpg"]}
      />,
    );
    expect(screen.getAllByRole("img")).toHaveLength(3);
    expect(screen.getByText("3 ref")).toBeInTheDocument();
  });

  it("shows running pulse on the StatusDot when status=running", () => {
    // The old draft chrome painted a spinner inside the card body. The new
    // Flova design dropped the spinner: a running draft carries a soft
    // motion-safe pulse on the StatusDot (STATUS_TOKEN.running.pulse=true)
    // plus the "Generating takes" footer caption. Assert the pulse class
    // + the data-status attribute on the StatusDot.
    render(
      <DraftNode
        id="d5"
        status="running"
        intent="X"
        modelLabel="M"
        configSummary="—"
        x={0}
        y={0}
      />,
    );
    const dot = screen.getByRole("status", { name: /generating takes/i });
    expect(dot.getAttribute("data-status")).toBe("running");
    expect(dot.className).toMatch(/animate-pulse/);
  });

  it("calls onSelect on pointerDown", () => {
    const onSelect = vi.fn();
    render(
      <DraftNode
        id="d6"
        status="draft"
        intent="X"
        modelLabel="M"
        configSummary="—"
        x={0}
        y={0}
        onSelect={onSelect}
      />,
    );
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith("d6");
  });

  it("calls onSelect on Enter key", () => {
    const onSelect = vi.fn();
    render(
      <DraftNode
        id="d7"
        status="draft"
        intent="X"
        modelLabel="M"
        configSummary="—"
        x={0}
        y={0}
        onSelect={onSelect}
      />,
    );
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("d7");
  });

  it("calls onSelect on Space key", () => {
    const onSelect = vi.fn();
    render(
      <DraftNode
        id="d8"
        status="draft"
        intent="X"
        modelLabel="M"
        configSummary="—"
        x={0}
        y={0}
        onSelect={onSelect}
      />,
    );
    fireEvent.keyDown(screen.getByRole("button"), { key: " " });
    expect(onSelect).toHaveBeenCalledWith("d8");
  });

  it("stops propagation on pointerDown", () => {
    const parent = vi.fn();
    render(
      <div onPointerDown={parent}>
        <DraftNode
          id="d9"
          status="draft"
          intent="X"
          modelLabel="M"
          configSummary="—"
          x={0}
          y={0}
        />
      </div>,
    );
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(parent).not.toHaveBeenCalled();
  });

  it("caps visible refs at 4 and shows +N overflow", () => {
    render(
      <DraftNode
        id="d10"
        status="draft"
        intent="X"
        modelLabel="M"
        configSummary="—"
        refs={["a", "b", "c", "d", "e", "f", "g"]}
        x={0}
        y={0}
      />,
    );
    expect(screen.getAllByAltText(/^Reference/)).toHaveLength(4);
    expect(screen.getByText("+3")).toBeInTheDocument();
  });

  it("shows status dot with accessible label when status=draft", () => {
    render(
      <DraftNode
        id="d11"
        status="draft"
        intent="X"
        modelLabel="M"
        configSummary="—"
        x={0}
        y={0}
      />,
    );
    expect(
      screen.getByRole("status", { name: /awaiting approval/i }),
    ).toBeInTheDocument();
  });
});
