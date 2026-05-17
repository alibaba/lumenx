// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, createEvent } from "@testing-library/react";
import { PlanNode } from "@/components/atelier/v3/PlanNode";

describe("PlanNode", () => {
  it("renders title and bullet list", () => {
    render(<PlanNode id="p1" title="3-direction" bullets={["a", "b", "c"]} x={0} y={0} />);
    expect(screen.getByText("3-direction")).toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
    expect(screen.getByText("c")).toBeInTheDocument();
  });

  it("shows the 'Plan · by Agent' footer", () => {
    // Footer is split into two mono-caps tokens with a separator dot, so we
    // assert the two distinct labels rather than a single concatenated string.
    render(<PlanNode id="p2" title="x" bullets={[]} x={0} y={0} />);
    expect(screen.getByText(/^Plan$/i)).toBeInTheDocument();
    expect(screen.getByText(/^by Agent$/i)).toBeInTheDocument();
  });

  it("uses a quiet glass border by default and primary ring when selected", () => {
    const { container } = render(<PlanNode id="p3" title="x" bullets={[]} x={0} y={0} />);
    // DESIGN.md §6.1 default = no chrome shouting; Plan keeps a glass-border
    // hairline and earns its primary color only on selection.
    expect(container.firstElementChild?.className).toMatch(/glass-border/);
  });

  it("uses primary ring when selected", () => {
    const { container } = render(<PlanNode id="p4" title="x" bullets={[]} selected x={0} y={0} />);
    expect(container.firstElementChild?.className).toMatch(/ring-primary/);
  });

  it("calls onSelect on pointerDown", () => {
    const onSelect = vi.fn();
    render(<PlanNode id="p5" title="x" bullets={[]} x={0} y={0} onSelect={onSelect} />);
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith("p5");
  });

  it("stops propagation on pointerDown", () => {
    const parent = vi.fn();
    render(
      <div onPointerDown={parent}>
        <PlanNode id="p6" title="x" bullets={[]} x={0} y={0} />
      </div>
    );
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(parent).not.toHaveBeenCalled();
  });

  it("calls onSelect on Enter key", () => {
    const onSelect = vi.fn();
    render(<PlanNode id="p7" title="x" bullets={[]} x={0} y={0} onSelect={onSelect} />);
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("p7");
  });

  it("calls onSelect on Space key", () => {
    const onSelect = vi.fn();
    render(<PlanNode id="p8" title="x" bullets={[]} x={0} y={0} onSelect={onSelect} />);
    fireEvent.keyDown(screen.getByRole("button"), { key: " " });
    expect(onSelect).toHaveBeenCalledWith("p8");
  });

  it("caps visible bullets at 5 and shows '+N more' overflow", () => {
    render(<PlanNode id="p9" title="t" bullets={["a","b","c","d","e","f","g"]} x={0} y={0} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(6);
    expect(screen.getByText("+2 more")).toBeInTheDocument();
  });

  it("does not show overflow row when bullets exactly fit", () => {
    render(<PlanNode id="p10" title="t" bullets={["a","b","c","d","e"]} x={0} y={0} />);
    expect(screen.queryByText(/more/i)).toBeNull();
  });

  it("Space key prevents default", () => {
    render(<PlanNode id="p11" title="t" bullets={[]} x={0} y={0} onSelect={() => {}} />);
    const button = screen.getByRole("button");
    const event = createEvent.keyDown(button, { key: " " });
    fireEvent(button, event);
    expect(event.defaultPrevented).toBe(true);
  });
});
