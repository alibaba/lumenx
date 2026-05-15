// @vitest-environment happy-dom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MediaNode } from "@/components/atelier/v3/MediaNode";

describe("v3 MediaNode", () => {
  it("renders image with filename in DOM", () => {
    render(
      <MediaNode
        id="n1"
        kind="image"
        src="https://example.com/a.png"
        filename="A.JPG"
        status="completed"
        x={0}
        y={0}
      />,
    );
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://example.com/a.png");
    expect(screen.getByText("A.JPG")).toBeInTheDocument();
  });

  it("renders processing overlay with percent", () => {
    render(
      <MediaNode id="n2" kind="video" status="processing" progress={47} x={0} y={0} />,
    );
    expect(screen.getByText("47%")).toBeInTheDocument();
  });

  it("renders persistent Selected chip when selectedAsTake", () => {
    render(
      <MediaNode
        id="n3"
        kind="video"
        src="https://example.com/v.png"
        status="completed"
        selectedAsTake
        x={0}
        y={0}
      />,
    );
    expect(screen.getByText(/Selected/)).toBeInTheDocument();
  });

  it("clamps width to <= 240 even if larger requested", () => {
    const { container } = render(
      <MediaNode
        id="n4"
        kind="image"
        src="https://example.com/a.png"
        status="completed"
        width={400}
        height={400}
        x={0}
        y={0}
      />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(parseInt(root.style.width)).toBeLessThanOrEqual(240);
  });

  it("calls onSelect with id on pointerDown", () => {
    const onSelect = vi.fn();
    render(
      <MediaNode
        id="n5"
        kind="image"
        src="https://example.com/a.png"
        status="completed"
        x={0}
        y={0}
        onSelect={onSelect}
      />,
    );
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith("n5");
  });

  it("calls onSelect on Enter key", () => {
    const onSelect = vi.fn();
    render(
      <MediaNode
        id="n6"
        kind="image"
        src="https://example.com/a.png"
        status="completed"
        x={0}
        y={0}
        onSelect={onSelect}
      />,
    );
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("n6");
  });

  it("calls onSelect on Space key", () => {
    const onSelect = vi.fn();
    render(
      <MediaNode
        id="n7"
        kind="image"
        src="https://example.com/a.png"
        status="completed"
        x={0}
        y={0}
        onSelect={onSelect}
      />,
    );
    fireEvent.keyDown(screen.getByRole("button"), { key: " " });
    expect(onSelect).toHaveBeenCalledWith("n7");
  });

  it("stops propagation on pointerDown", () => {
    const parent = vi.fn();
    render(
      <div onPointerDown={parent}>
        <MediaNode
          id="n8"
          kind="image"
          src="https://example.com/a.png"
          status="completed"
          x={0}
          y={0}
        />
      </div>,
    );
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(parent).not.toHaveBeenCalled();
  });

  it("clamps negative width and height to a positive minimum", () => {
    const { container } = render(
      <MediaNode
        id="n9"
        kind="image"
        src="https://example.com/a.png"
        status="completed"
        x={0}
        y={0}
        width={-50}
        height={-50}
      />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(parseInt(root.style.width)).toBeGreaterThanOrEqual(40);
    expect(parseInt(root.style.height)).toBeGreaterThanOrEqual(24);
  });
});
