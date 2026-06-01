// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SelectionActionBar } from "@/components/atelier/v3/SelectionActionBar";

// v0.7 spec: SelectionActionBar is now a chip toolbar (icon + sentence-
// case label) with a trailing "More" overflow menu. Destructive Delete
// is ALWAYS in the overflow — even for short layouts (idea / comment) —
// so any test that wants to find Delete must open the More menu first.

describe("SelectionActionBar", () => {
  it("renders the take-judgment chip row for kind=video", () => {
    render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.getByLabelText("Pick this take")).toBeInTheDocument();
    expect(screen.getByLabelText("Reroll")).toBeInTheDocument();
    expect(screen.getByLabelText("Add to sequence")).toBeInTheDocument();
    expect(screen.getByLabelText("Compare takes")).toBeInTheDocument();
    expect(screen.getByLabelText("Use as ref")).toBeInTheDocument();
    expect(screen.getByLabelText("Download")).toBeInTheDocument();
    expect(screen.getByLabelText("More")).toBeInTheDocument();
    // Delete and Branch live in overflow, not the visible row.
    expect(screen.queryByLabelText("Delete")).toBeNull();
    expect(screen.queryByLabelText("Branch")).toBeNull();
  });

  it("opens the More menu and shows overflow actions for kind=video", () => {
    render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={() => {}} />);
    fireEvent.click(screen.getByLabelText("More"));
    // Overflow menu items.
    expect(screen.getByRole("menuitem", { name: "Fullscreen" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Send to agent" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Capture frame" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Branch" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeInTheDocument();
  });

  it("renders the audio chip row for kind=audio (Use in sequence, not Add to sequence)", () => {
    render(<SelectionActionBar kind="audio" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.getByLabelText("Preview")).toBeInTheDocument();
    expect(screen.getByLabelText("Replace voice")).toBeInTheDocument();
    expect(screen.getByLabelText("Trim")).toBeInTheDocument();
    expect(screen.getByLabelText("Use in sequence")).toBeInTheDocument();
    // Audio uses "Use in sequence" not "Add to sequence" — they are
    // distinct chip keys even though they ultimately land in the same
    // Sequence list.
    expect(screen.queryByLabelText("Add to sequence")).toBeNull();
  });

  it("hides sequence chips for kind=image / draft / idea", () => {
    const { unmount: u1 } = render(
      <SelectionActionBar kind="image" hasMedia x={0} y={100} width={200} onAct={() => {}} />,
    );
    expect(screen.queryByLabelText("Add to sequence")).toBeNull();
    expect(screen.queryByLabelText("Use in sequence")).toBeNull();
    u1();
    const { unmount: u2 } = render(
      <SelectionActionBar kind="draft" x={0} y={100} width={200} onAct={() => {}} />,
    );
    expect(screen.queryByLabelText("Add to sequence")).toBeNull();
    u2();
    render(<SelectionActionBar kind="idea" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.queryByLabelText("Add to sequence")).toBeNull();
  });

  it("calls onAct with the correct ActionKey when a main-row chip is clicked", () => {
    const onAct = vi.fn();
    render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={onAct} />);
    fireEvent.click(screen.getByLabelText("Pick this take"));
    expect(onAct).toHaveBeenCalledWith("selectTake");
    fireEvent.click(screen.getByLabelText("Reroll"));
    expect(onAct).toHaveBeenCalledWith("regenerate");
  });

  it("Delete in the overflow menu dispatches onAct('delete') for kind=video", () => {
    const onAct = vi.fn();
    render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={onAct} />);
    fireEvent.click(screen.getByLabelText("More"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(onAct).toHaveBeenCalledWith("delete");
  });

  it("Branch in the overflow menu dispatches onAct('branch') for kind=video", () => {
    const onAct = vi.fn();
    render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={onAct} />);
    fireEvent.click(screen.getByLabelText("More"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Branch" }));
    expect(onAct).toHaveBeenCalledWith("branch");
  });

  // P2 (A'): frame-capture lives only on video and routes to the
  // shell's frameCapture handler. In v0.7 it moved into the overflow.
  it("Capture frame lives in the overflow on video kind only", () => {
    const { unmount: u1 } = render(
      <SelectionActionBar kind="video" x={0} y={100} width={200} onAct={() => {}} />,
    );
    fireEvent.click(screen.getByLabelText("More"));
    expect(screen.getByRole("menuitem", { name: "Capture frame" })).toBeInTheDocument();
    u1();
    const { unmount: u2 } = render(
      <SelectionActionBar kind="image" hasMedia x={0} y={100} width={200} onAct={() => {}} />,
    );
    fireEvent.click(screen.getByLabelText("More"));
    expect(screen.queryByRole("menuitem", { name: "Capture frame" })).toBeNull();
    u2();
    render(<SelectionActionBar kind="audio" x={0} y={100} width={200} onAct={() => {}} />);
    fireEvent.click(screen.getByLabelText("More"));
    expect(screen.queryByRole("menuitem", { name: "Capture frame" })).toBeNull();
  });

  it("Capture frame click fires onAct('frameCapture')", () => {
    const onAct = vi.fn();
    render(<SelectionActionBar kind="video" x={0} y={100} width={200} onAct={onAct} />);
    fireEvent.click(screen.getByLabelText("More"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Capture frame" }));
    expect(onAct).toHaveBeenCalledWith("frameCapture");
  });

  it("stops propagation on click so canvas doesn't deselect", () => {
    const parent = vi.fn();
    render(
      <div onClick={parent}>
        <SelectionActionBar kind="video" x={0} y={100} width={200} onAct={() => {}} />
      </div>,
    );
    fireEvent.click(screen.getByLabelText("More"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(parent).not.toHaveBeenCalled();
  });

  it("positions itself centered above the node bounding box", () => {
    const { container } = render(
      <SelectionActionBar kind="video" x={100} y={200} width={200} onAct={() => {}} />,
    );
    const root = container.firstElementChild as HTMLElement;
    // expected: left = x + width/2 = 200, top = y - 36 = 164
    // (Tighter pairing than the legacy 40px gap — bar sits 4px above
    // the node top so the eye reads them as one selection unit.)
    expect(root.style.left).toBe("200px");
    expect(root.style.top).toBe("164px");
  });

  it("draft kind shows the new draft chip row (Generate + settings + More)", () => {
    render(<SelectionActionBar kind="draft" x={0} y={100} width={200} onAct={() => {}} />);
    // Generate is the emerald CTA — sentence-case label, not the old icon-
    // only Play button. Draft no longer shows Branch / Pick this take.
    expect(screen.getByLabelText("Generate")).toBeInTheDocument();
    expect(screen.getByLabelText("Edit prompt")).toBeInTheDocument();
    expect(screen.getByLabelText("Reroll seed")).toBeInTheDocument();
    expect(screen.getByLabelText("Pick model")).toBeInTheDocument();
    expect(screen.getByLabelText("Aspect")).toBeInTheDocument();
    expect(screen.getByLabelText("Duration")).toBeInTheDocument();
    expect(screen.getByLabelText("More")).toBeInTheDocument();
    expect(screen.queryByLabelText("Pick this take")).toBeNull();
    expect(screen.queryByLabelText("Branch")).toBeNull();
  });

  it("draft overflow contains Negative prompt + Convert to idea + Delete", () => {
    render(<SelectionActionBar kind="draft" x={0} y={100} width={200} onAct={() => {}} />);
    fireEvent.click(screen.getByLabelText("More"));
    expect(screen.getByRole("menuitem", { name: "Negative prompt" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Convert to idea" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeInTheDocument();
  });

  it("clamps top to >= 12 when node is near canvas top", () => {
    const { container } = render(
      <SelectionActionBar kind="video" x={0} y={20} width={200} onAct={() => {}} />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.top).toBe("12px");
  });

  it("idea kind: Convert to draft + Pin in main; Delete in overflow", () => {
    render(<SelectionActionBar kind="idea" x={0} y={100} width={200} onAct={() => {}} />);
    expect(screen.getByLabelText("Convert to draft")).toBeInTheDocument();
    expect(screen.getByLabelText("Pin")).toBeInTheDocument();
    expect(screen.getByLabelText("More")).toBeInTheDocument();
    // Delete is NOT in the main row.
    expect(screen.queryByLabelText("Delete")).toBeNull();
    fireEvent.click(screen.getByLabelText("More"));
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeInTheDocument();
  });

  it("empty image (hasMedia=false) collapses to Upload + Use as ref + More→Delete", () => {
    render(
      <SelectionActionBar kind="image" hasMedia={false} x={0} y={100} width={200} onAct={() => {}} />,
    );
    expect(screen.getByLabelText("Upload")).toBeInTheDocument();
    expect(screen.getByLabelText("Use as ref")).toBeInTheDocument();
    expect(screen.queryByLabelText("Variations")).toBeNull();
    expect(screen.queryByLabelText("Crop")).toBeNull();
    fireEvent.click(screen.getByLabelText("More"));
    expect(screen.getByRole("menuitem", { name: "Delete" })).toBeInTheDocument();
  });

  it("image (hasMedia=true) shows the full image-shaping chip row", () => {
    render(
      <SelectionActionBar kind="image" hasMedia x={0} y={100} width={200} onAct={() => {}} />,
    );
    expect(screen.getByLabelText("Variations")).toBeInTheDocument();
    expect(screen.getByLabelText("Edit subject")).toBeInTheDocument();
    expect(screen.getByLabelText("Crop")).toBeInTheDocument();
    expect(screen.getByLabelText("Upscale")).toBeInTheDocument();
    expect(screen.getByLabelText("Style transfer")).toBeInTheDocument();
    expect(screen.getByLabelText("Use as ref")).toBeInTheDocument();
    expect(screen.queryByLabelText("Upload")).toBeNull();
  });
});
