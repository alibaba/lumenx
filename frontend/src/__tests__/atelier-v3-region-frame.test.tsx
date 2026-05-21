// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RegionFrame } from "@/components/atelier/v3/RegionFrame";

describe("RegionFrame", () => {
  it("renders the title text", () => {
    render(
      <RegionFrame id="r1" x={0} y={0} width={400} height={300} title="Character study" />,
    );
    expect(screen.getByText("Character study")).toBeInTheDocument();
  });

  it("falls back to a default title when the prop is empty", () => {
    render(
      <RegionFrame id="r1" x={0} y={0} width={400} height={300} title="" />,
    );
    // Empty title shouldn't render an empty pill — show a placeholder.
    expect(screen.getByLabelText(/region/i)).toBeInTheDocument();
  });

  it("positions itself with x/y as a CSS translate", () => {
    const { container } = render(
      <RegionFrame id="r1" x={120} y={80} width={400} height={300} title="X" />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.transform).toContain("translate(120px, 80px)");
  });

  it("renders width/height as inline style", () => {
    const { container } = render(
      <RegionFrame id="r1" x={0} y={0} width={520} height={360} title="X" />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.width).toBe("520px");
    expect(root.style.height).toBe("360px");
  });

  it("uses a primary ring when selected", () => {
    const { container } = render(
      <RegionFrame id="r1" x={0} y={0} width={400} height={300} title="X" selected />,
    );
    expect(container.firstElementChild?.className).toMatch(/ring-primary|ring-2/);
  });

  it("calls onSelect on body pointerDown", () => {
    const onSelect = vi.fn();
    render(
      <RegionFrame
        id="r1"
        x={0}
        y={0}
        width={400}
        height={300}
        title="X"
        onSelect={onSelect}
      />,
    );
    fireEvent.pointerDown(screen.getByRole("group", { name: /region/i }));
    expect(onSelect).toHaveBeenCalledWith("r1");
  });

  it("calls onMoveStart when title bar is pressed", () => {
    const onMoveStart = vi.fn();
    render(
      <RegionFrame
        id="r1"
        x={0}
        y={0}
        width={400}
        height={300}
        title="X"
        onMoveStart={onMoveStart}
      />,
    );
    const titleBar = screen.getByTestId("region-title-bar");
    fireEvent.pointerDown(titleBar, { clientX: 50, clientY: 12 });
    expect(onMoveStart).toHaveBeenCalledTimes(1);
    expect(onMoveStart.mock.calls[0][0]).toBe("r1");
  });

  it("renders 4 resize handles when selected, each firing onResizeStart with its corner key", () => {
    const onResizeStart = vi.fn();
    render(
      <RegionFrame
        id="r1"
        x={0}
        y={0}
        width={400}
        height={300}
        title="X"
        selected
        onResizeStart={onResizeStart}
      />,
    );
    const corners: Array<"nw" | "ne" | "sw" | "se"> = ["nw", "ne", "sw", "se"];
    for (const c of corners) {
      const h = screen.getByTestId(`region-handle-${c}`);
      fireEvent.pointerDown(h, { clientX: 0, clientY: 0 });
    }
    expect(onResizeStart).toHaveBeenCalledTimes(4);
    const cornerArgs = onResizeStart.mock.calls.map((call) => call[1]);
    expect(cornerArgs.sort()).toEqual(["ne", "nw", "se", "sw"]);
  });

  it("fires onContextMenu with id + clientX/clientY", () => {
    const onContextMenu = vi.fn();
    render(
      <RegionFrame
        id="r1"
        x={0}
        y={0}
        width={400}
        height={300}
        title="X"
        onContextMenu={onContextMenu}
      />,
    );
    const root = screen.getByRole("group", { name: /region/i });
    fireEvent.contextMenu(root, { clientX: 70, clientY: 90 });
    expect(onContextMenu).toHaveBeenCalledTimes(1);
    expect(onContextMenu.mock.calls[0][0]).toBe("r1");
  });

  it("title becomes editable on double-click and commits on Enter", () => {
    const onTitleCommit = vi.fn();
    render(
      <RegionFrame
        id="r1"
        x={0}
        y={0}
        width={400}
        height={300}
        title="Original"
        onTitleCommit={onTitleCommit}
      />,
    );
    const titleEl = screen.getByText("Original");
    fireEvent.doubleClick(titleEl);
    const input = screen.getByRole("textbox", { name: /region title/i });
    fireEvent.change(input, { target: { value: "Renamed" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onTitleCommit).toHaveBeenCalledWith("Renamed");
  });

  it("title edit cancels on Escape (no commit)", () => {
    const onTitleCommit = vi.fn();
    render(
      <RegionFrame
        id="r1"
        x={0}
        y={0}
        width={400}
        height={300}
        title="Original"
        onTitleCommit={onTitleCommit}
      />,
    );
    fireEvent.doubleClick(screen.getByText("Original"));
    const input = screen.getByRole("textbox", { name: /region title/i });
    fireEvent.change(input, { target: { value: "Renamed" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onTitleCommit).not.toHaveBeenCalled();
    // After cancel, original title is back in DOM.
    expect(screen.getByText("Original")).toBeInTheDocument();
  });

  it("shows child count when childCount > 0", () => {
    render(
      <RegionFrame
        id="r1"
        x={0}
        y={0}
        width={400}
        height={300}
        title="X"
        childCount={5}
      />,
    );
    expect(screen.getByText(/5/)).toBeInTheDocument();
  });

  it("hides child count when 0 or undefined", () => {
    const { rerender } = render(
      <RegionFrame
        id="r1"
        x={0}
        y={0}
        width={400}
        height={300}
        title="X"
        childCount={0}
      />,
    );
    expect(screen.queryByTestId("region-child-count")).toBeNull();
    rerender(
      <RegionFrame id="r1" x={0} y={0} width={400} height={300} title="X" />,
    );
    expect(screen.queryByTestId("region-child-count")).toBeNull();
  });

  it("applies a color accent class when color prop given", () => {
    const { container } = render(
      <RegionFrame
        id="r1"
        x={0}
        y={0}
        width={400}
        height={300}
        title="X"
        color="cyan"
      />,
    );
    // Color affects either the title accent or the dot — assert that the
    // wrapper carries some marker indicating non-default.
    const root = container.firstElementChild as HTMLElement;
    expect(root.dataset.regionColor).toBe("cyan");
  });

  it("body pointerDown stops propagation", () => {
    const parent = vi.fn();
    render(
      <div onPointerDown={parent}>
        <RegionFrame id="r1" x={0} y={0} width={400} height={300} title="X" />
      </div>,
    );
    fireEvent.pointerDown(screen.getByRole("group", { name: /region/i }));
    expect(parent).not.toHaveBeenCalled();
  });
});
