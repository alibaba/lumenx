// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SelectionActionBar } from "@/components/atelier/v3/SelectionActionBar";

describe("SelectionActionBar", () => {
  it("renders Play, Branch, Delete icons for kind=video", () => {
    render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.getByLabelText("Play")).toBeInTheDocument();
    expect(screen.getByLabelText("Branch")).toBeInTheDocument();
    expect(screen.getByLabelText("Delete")).toBeInTheDocument();
  });

  it("renders Add to Sequence for kind=video and audio", () => {
    const { unmount } = render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.getByLabelText("Add to Sequence")).toBeInTheDocument();
    unmount();
    render(<SelectionActionBar kind="audio" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.getByLabelText("Add to Sequence")).toBeInTheDocument();
  });

  it("hides Add to Sequence for kind=image and draft and idea", () => {
    const { unmount: u1 } = render(<SelectionActionBar kind="image" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.queryByLabelText("Add to Sequence")).toBeNull();
    u1();
    const { unmount: u2 } = render(<SelectionActionBar kind="draft" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.queryByLabelText("Add to Sequence")).toBeNull();
    u2();
    render(<SelectionActionBar kind="idea" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.queryByLabelText("Add to Sequence")).toBeNull();
  });

  it("calls onAct with the correct ActionKey when clicked", () => {
    const onAct = vi.fn();
    render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={onAct} />);
    fireEvent.click(screen.getByLabelText("Delete"));
    expect(onAct).toHaveBeenCalledWith("delete");
    fireEvent.click(screen.getByLabelText("Branch"));
    expect(onAct).toHaveBeenCalledWith("branch");
  });

  it("stops propagation on click so canvas doesn't deselect", () => {
    const parent = vi.fn();
    render(
      <div onClick={parent}>
        <SelectionActionBar kind="video" x={0} y={100} width={200} onAct={() => {}} />
      </div>
    );
    fireEvent.click(screen.getByLabelText("Delete"));
    expect(parent).not.toHaveBeenCalled();
  });

  it("positions itself centered above the node bounding box", () => {
    const { container } = render(<SelectionActionBar kind="video" x={100} y={200} width={200} onAct={() => {}} />);
    const root = container.firstElementChild as HTMLElement;
    // expected: left = x + width/2 = 200, top = y - 40 = 160
    expect(root.style.left).toBe("200px");
    expect(root.style.top).toBe("160px");
  });

  it("renders fewer icons for kind=draft (no Play, no SelectTake)", () => {
    render(<SelectionActionBar kind="draft" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.queryByLabelText("Play")).toBeNull();
    expect(screen.queryByLabelText("Select as take")).toBeNull();
    expect(screen.getByLabelText("Branch")).toBeInTheDocument();
    expect(screen.getByLabelText("Re-generate")).toBeInTheDocument();
  });

  it("clamps top to >= 8 when node is near canvas top", () => {
    const { container } = render(<SelectionActionBar kind="video" x={0} y={20} width={200} onAct={() => {}} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.top).toBe("8px");
  });

  it("idea kind shows only Delete (no leading divider)", () => {
    const { container } = render(<SelectionActionBar kind="idea" x={0} y={100} width={200} onAct={() => {}} />);
    const buttons = container.querySelectorAll("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("aria-label")).toBe("Delete");
    const dividers = container.querySelectorAll(".bg-glass-border");
    expect(dividers).toHaveLength(0);
  });
});
