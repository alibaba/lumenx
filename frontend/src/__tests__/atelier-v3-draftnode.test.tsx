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

  it("uses amber border when status=draft", () => {
    const { container } = render(
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
    expect(container.firstElementChild?.className).toMatch(/amber/);
  });

  it("uses primary border when status=approved", () => {
    const { container } = render(
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
    expect(container.firstElementChild?.className).toMatch(/primary\/40/);
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

  it("shows running spinner when status=running", () => {
    const { container } = render(
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
    expect(container.querySelector(".animate-spin")).not.toBeNull();
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
