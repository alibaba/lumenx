// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PlanNode } from "@/components/atelier/v3/PlanNode";

describe("PlanNode", () => {
  it("renders title and bullet list", () => {
    render(<PlanNode id="p1" title="3-direction" bullets={["a", "b", "c"]} x={0} y={0} />);
    expect(screen.getByText("3-direction")).toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
    expect(screen.getByText("c")).toBeInTheDocument();
  });

  it("shows 'PLAN · by Agent' footer", () => {
    render(<PlanNode id="p2" title="x" bullets={[]} x={0} y={0} />);
    expect(screen.getByText(/PLAN · by Agent/i)).toBeInTheDocument();
  });

  it("uses primary/30 border by default", () => {
    const { container } = render(<PlanNode id="p3" title="x" bullets={[]} x={0} y={0} />);
    expect(container.firstElementChild?.className).toMatch(/primary\/30/);
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
});
