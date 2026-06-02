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
    expect(screen.getByText(/selected/i)).toBeInTheDocument();
  });

  it("clamps width to <= 240 for video takes even if larger requested", () => {
    // Image+src and video+src+completed now render the unified preview-card
    // chrome (fixed w-[260px], no user-supplied dimensions). The clamp test
    // is therefore moved to a pending video take, which still flows through
    // the legacy width-prop branch (the preview-card branch only kicks in on
    // status="completed").
    const { container } = render(
      <MediaNode
        id="n4"
        kind="video"
        src="https://example.com/v.mp4"
        status="pending"
        width={400}
        height={400}
        x={0}
        y={0}
      />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(parseInt(root.style.width)).toBeLessThanOrEqual(240);
  });

  // For these onSelect / propagation tests we use status="pending" instead
  // of "completed". A completed image+src now renders the preview-card
  // chrome which contains 5 inner action buttons (expand/bookmark/copy/
  // refresh/download) in addition to the outer role="button", making
  // getByRole("button") ambiguous. The pending variant flows through the
  // image-card branch which has exactly one role="button" (the card itself).

  it("calls onSelect with id on pointerDown", () => {
    const onSelect = vi.fn();
    render(
      <MediaNode
        id="n5"
        kind="image"
        src="https://example.com/a.png"
        status="pending"
        x={0}
        y={0}
        onSelect={onSelect}
      />,
    );
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith("n5");
  });

  it("calls onSelect on Enter key", () => {
    const onSelect = vi.fn();
    render(
      <MediaNode
        id="n6"
        kind="image"
        src="https://example.com/a.png"
        status="pending"
        x={0}
        y={0}
        onSelect={onSelect}
      />,
    );
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("n6");
  });

  it("calls onSelect on Space key", () => {
    const onSelect = vi.fn();
    render(
      <MediaNode
        id="n7"
        kind="image"
        src="https://example.com/a.png"
        status="pending"
        x={0}
        y={0}
        onSelect={onSelect}
      />,
    );
    fireEvent.keyDown(screen.getByRole("button"), { key: " " });
    expect(onSelect).toHaveBeenCalledWith("n7");
  });

  it("stops propagation on pointerDown", () => {
    const parent = vi.fn();
    render(
      <div onPointerDown={parent}>
        <MediaNode
          id="n8"
          kind="image"
          src="https://example.com/a.png"
          status="pending"
          x={0}
          y={0}
        />
      </div>,
    );
    fireEvent.pointerDown(screen.getByRole("button"));
    expect(parent).not.toHaveBeenCalled();
  });

  it("clamps negative width and height to a positive minimum (video kind)", () => {
    // Same reason as the clamp-max test above: a completed video+src now
    // renders the preview-card chrome (fixed w-[260px], no inline width /
    // height), so the clamp test uses a pending video to flow through the
    // legacy width-prop branch.
    const { container } = render(
      <MediaNode
        id="n9"
        kind="video"
        src="https://example.com/v.mp4"
        status="pending"
        x={0}
        y={0}
        width={-50}
        height={-50}
      />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(parseInt(root.style.width)).toBeGreaterThanOrEqual(40);
    expect(parseInt(root.style.height)).toBeGreaterThanOrEqual(24);
  });

  it("renders an image card with stamped image caption + id suffix", () => {
    // v0.5+ Flova skin: the image card grew to w-[260px] and split the
    // single "Image · No 123" caption into a sentence-case "image" pill at
    // the header trailing edge and a separate "Image" + stampNum row in
    // the footer. We use status="pending" to hit the image-card branch
    // deterministically — the completed branch now renders the preview-
    // card chrome which doesn't expose the same caption row.
    const { container } = render(
      <MediaNode
        id="n10abc123"
        kind="image"
        src="https://example.com/a.png"
        filename="hero.png"
        status="pending"
        x={0}
        y={0}
      />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toMatch(/w-\[260px\]/);
    // Header trailing-edge type pill (lowercase "image" per §9.1) and the
    // footer caption ("Image") are different spans. Match case-sensitively
    // so the two assertions don't collapse onto each other.
    expect(screen.getByText(/^image$/)).toBeInTheDocument();
    expect(screen.getByText(/^Image$/)).toBeInTheDocument();
    expect(screen.getByText("123")).toBeInTheDocument();
  });
});
