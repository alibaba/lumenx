// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { IdeaNode } from "@/components/atelier/v3/IdeaNode";

describe("v3 IdeaNode", () => {
  it("renders body text", () => {
    render(<IdeaNode id="i1" body="The protagonist stops at the edge." x={0} y={0} />);
    expect(screen.getByText(/protagonist stops/)).toBeInTheDocument();
  });

  it("uses warm-ochre tinted edge by default", () => {
    // v0.5+ Flova skin: the IdeaNode category wash uses the muted
    // `atelier-ochre` token instead of raw tailwind `amber` (see
    // IdeaNode.tsx — `border-atelier-ochre/15`).
    const { container } = render(<IdeaNode id="i2" body="x" x={0} y={0} />);
    expect(container.firstElementChild?.className).toMatch(/ochre/);
  });

  it("switches to primary ring when selected", () => {
    const { container } = render(<IdeaNode id="i3" body="x" selected x={0} y={0} />);
    // v0.5+ Flova skin: cobalt is reserved for selection — but the soft
    // selection ring is rendered as `ring-1 ring-white/25 border-white/20`,
    // not the literal tailwind `ring-primary` token.
    expect(container.firstElementChild?.className).toMatch(/ring-1.*ring-white/);
  });

  it("calls onSelect on pointerDown", () => {
    const onSelect = vi.fn();
    render(<IdeaNode id="i4" body="x" x={0} y={0} onSelect={onSelect} />);
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith("i4");
  });

  it("calls onSelect on Enter key", () => {
    const onSelect = vi.fn();
    render(<IdeaNode id="i5" body="x" x={0} y={0} onSelect={onSelect} />);
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("i5");
  });

  it("calls onSelect on Space key", () => {
    const onSelect = vi.fn();
    render(<IdeaNode id="i6" body="x" x={0} y={0} onSelect={onSelect} />);
    fireEvent.keyDown(screen.getByRole("button"), { key: " " });
    expect(onSelect).toHaveBeenCalledWith("i6");
  });

  it("stops propagation on pointerDown", () => {
    const parent = vi.fn();
    render(
      <div onPointerDown={parent}>
        <IdeaNode id="i7" body="x" x={0} y={0} />
      </div>,
    );
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(parent).not.toHaveBeenCalled();
  });

  it("truncates very long body via line clamp", () => {
    const longBody = "Lorem ipsum dolor sit amet ".repeat(200);
    const { container } = render(<IdeaNode id="i8" body={longBody} x={0} y={0} />);
    const p = container.querySelector("p");
    expect(p?.className).toMatch(/line-clamp-5|line-clamp-6|overflow-hidden/);
  });
});
