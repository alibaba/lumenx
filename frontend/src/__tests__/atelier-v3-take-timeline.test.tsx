// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TakeTimeline } from "@/components/atelier/v3/TakeTimeline";

describe("TakeTimeline", () => {
  const takes = [
    { id: "t1", thumbUrl: "a.jpg", status: "completed" as const, selected: false, createdAt: 100 },
    { id: "t2", thumbUrl: "b.jpg", status: "completed" as const, selected: true, createdAt: 200 },
    { id: "t3", thumbUrl: undefined, status: "processing" as const, selected: false, createdAt: 300 },
    { id: "t4", thumbUrl: undefined, status: "failed" as const, selected: false, createdAt: 400 },
  ];

  it("renders one tile per take", () => {
    render(<TakeTimeline takes={takes} onPickTake={vi.fn()} />);
    for (const t of takes) {
      expect(screen.getByLabelText(new RegExp(`take ${t.id}`, "i"))).toBeInTheDocument();
    }
  });

  it("marks the selected take with aria-pressed=true", () => {
    render(<TakeTimeline takes={takes} onPickTake={vi.fn()} />);
    const selected = screen.getByLabelText(/take t2/i);
    expect(selected.getAttribute("aria-pressed")).toBe("true");
    const other = screen.getByLabelText(/take t1/i);
    expect(other.getAttribute("aria-pressed")).toBe("false");
  });

  it("sorts takes oldest-first (chronological left → right)", () => {
    // Shuffle input — render order must still follow createdAt asc.
    const shuffled = [takes[2], takes[0], takes[3], takes[1]];
    const { container } = render(<TakeTimeline takes={shuffled} onPickTake={vi.fn()} />);
    const buttons = container.querySelectorAll("button[data-take-id]");
    const order = Array.from(buttons).map((b) => b.getAttribute("data-take-id"));
    expect(order).toEqual(["t1", "t2", "t3", "t4"]);
  });

  it("fires onPickTake with the take id on click", () => {
    const onPickTake = vi.fn();
    render(<TakeTimeline takes={takes} onPickTake={onPickTake} />);
    fireEvent.click(screen.getByLabelText(/take t3/i));
    expect(onPickTake).toHaveBeenCalledWith("t3");
  });

  it("renders nothing when takes is empty", () => {
    const { container } = render(<TakeTimeline takes={[]} onPickTake={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a status placeholder when thumbUrl missing (processing / failed)", () => {
    render(<TakeTimeline takes={takes} onPickTake={vi.fn()} />);
    // Both t3 (processing) and t4 (failed) lack a thumbUrl — they should
    // each carry a data-take-status attribute so the visual placeholder
    // can be styled by status tone.
    expect(screen.getByLabelText(/take t3/i).getAttribute("data-take-status")).toBe("processing");
    expect(screen.getByLabelText(/take t4/i).getAttribute("data-take-status")).toBe("failed");
  });

  it("stops propagation on pointerDown so canvas drag doesn't fire", () => {
    const parent = vi.fn();
    render(
      <div onPointerDown={parent}>
        <TakeTimeline takes={takes} onPickTake={vi.fn()} />
      </div>,
    );
    fireEvent.pointerDown(screen.getByLabelText(/take t1/i));
    expect(parent).not.toHaveBeenCalled();
  });
});
