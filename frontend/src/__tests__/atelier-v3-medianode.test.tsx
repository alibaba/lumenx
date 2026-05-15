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
});
