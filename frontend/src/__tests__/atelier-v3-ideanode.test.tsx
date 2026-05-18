// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { IdeaNode } from "@/components/atelier/v3/IdeaNode";

describe("v3 IdeaNode", () => {
  it("renders body text", () => {
    render(<IdeaNode id="i1" body="The protagonist stops at the edge." x={0} y={0} />);
    expect(screen.getByText(/protagonist stops/)).toBeInTheDocument();
  });

  it("uses amber tinted edge by default", () => {
    const { container } = render(<IdeaNode id="i2" body="x" x={0} y={0} />);
    expect(container.firstElementChild?.className).toMatch(/amber/);
  });

  it("switches to primary ring when selected", () => {
    const { container } = render(<IdeaNode id="i3" body="x" selected x={0} y={0} />);
    expect(container.firstElementChild?.className).toMatch(/ring-primary/);
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
