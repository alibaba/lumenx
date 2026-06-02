// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { TakeTimeline } from "@/components/atelier/v3/TakeTimeline";

// v4 ④ "celebrate output" pile design: each take tile carries an aria-label of
// `Take {i+1}` (1-based position), not `Take {id}`. The take id lives on the
// `data-take-id` attribute. The lookup helpers below resolve a take from the
// rendered tree by id via the data attribute so the tests stay decoupled from
// the visible label format.
function getTakeById(container: HTMLElement, id: string): HTMLElement {
  const el = container.querySelector(`[data-take-id="${id}"]`) as HTMLElement | null;
  if (!el) throw new Error(`Take with data-take-id="${id}" not found`);
  return el;
}

describe("TakeTimeline", () => {
  const takes = [
    { id: "t1", thumbUrl: "a.jpg", status: "completed" as const, selected: false, createdAt: 100 },
    { id: "t2", thumbUrl: "b.jpg", status: "completed" as const, selected: true, createdAt: 200 },
    { id: "t3", thumbUrl: undefined, status: "processing" as const, selected: false, createdAt: 300 },
    { id: "t4", thumbUrl: undefined, status: "failed" as const, selected: false, createdAt: 400 },
  ];

  it("renders one tile per take", () => {
    const { container } = render(<TakeTimeline takes={takes} onPickTake={vi.fn()} />);
    for (const t of takes) {
      expect(getTakeById(container, t.id)).toBeInTheDocument();
    }
  });

  it("marks the selected take with aria-pressed=true", () => {
    const { container } = render(<TakeTimeline takes={takes} onPickTake={vi.fn()} />);
    const selected = getTakeById(container, "t2");
    expect(selected.getAttribute("aria-pressed")).toBe("true");
    const other = getTakeById(container, "t1");
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
    const { container } = render(<TakeTimeline takes={takes} onPickTake={onPickTake} />);
    fireEvent.click(getTakeById(container, "t3"));
    expect(onPickTake).toHaveBeenCalledWith("t3");
  });

  it("renders nothing when takes is empty", () => {
    const { container } = render(<TakeTimeline takes={[]} onPickTake={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a status placeholder when thumbUrl missing (processing / failed)", () => {
    const { container } = render(<TakeTimeline takes={takes} onPickTake={vi.fn()} />);
    // Both t3 (processing) and t4 (failed) lack a thumbUrl — they should
    // each carry a data-take-status attribute so the visual placeholder
    // can be styled by status tone.
    expect(getTakeById(container, "t3").getAttribute("data-take-status")).toBe("processing");
    expect(getTakeById(container, "t4").getAttribute("data-take-status")).toBe("failed");
  });

  it("stops propagation on pointerDown so canvas drag doesn't fire", () => {
    const parent = vi.fn();
    const { container } = render(
      <div onPointerDown={parent}>
        <TakeTimeline takes={takes} onPickTake={vi.fn()} />
      </div>,
    );
    fireEvent.pointerDown(getTakeById(container, "t1"));
    expect(parent).not.toHaveBeenCalled();
  });
});
