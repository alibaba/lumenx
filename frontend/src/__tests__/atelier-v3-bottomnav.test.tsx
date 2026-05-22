// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BottomNavRail } from "@/components/atelier/v3/BottomNavRail";
import { Minimap } from "@/components/atelier/v3/Minimap";

describe("BottomNavRail", () => {
  it("renders zoom value as a 'ZOOM · 85' typewriter readout", () => {
    render(<BottomNavRail zoom={85} onZoomChange={() => {}} onFit={() => {}} onToggleMinimap={() => {}} />);
    // "ZOOM" tag + "85" number live in adjacent spans inside the reset
    // button — assert the button name.
    expect(screen.getByLabelText(/Reset zoom/i).textContent).toMatch(/Zoom/i);
    expect(screen.getByLabelText(/Reset zoom/i).textContent).toContain("85");
  });

  it("calls onFit when Fit view clicked", () => {
    const onFit = vi.fn();
    render(<BottomNavRail zoom={100} onZoomChange={() => {}} onFit={onFit} onToggleMinimap={() => {}} />);
    fireEvent.click(screen.getByLabelText("Fit view"));
    expect(onFit).toHaveBeenCalled();
  });

  it("calls onToggleMinimap when minimap button clicked", () => {
    const onToggle = vi.fn();
    render(<BottomNavRail zoom={100} onZoomChange={() => {}} onFit={() => {}} onToggleMinimap={onToggle} />);
    fireEvent.click(screen.getByLabelText("Toggle minimap"));
    expect(onToggle).toHaveBeenCalled();
  });

  it("zoom-in steps by 25 and clamps at 300", () => {
    const onZoomChange = vi.fn();
    render(<BottomNavRail zoom={290} onZoomChange={onZoomChange} onFit={() => {}} onToggleMinimap={() => {}} />);
    fireEvent.click(screen.getByLabelText("Zoom in"));
    expect(onZoomChange).toHaveBeenCalledWith(300);
  });

  it("zoom-out steps by 25 and clamps at 25", () => {
    const onZoomChange = vi.fn();
    render(<BottomNavRail zoom={30} onZoomChange={onZoomChange} onFit={() => {}} onToggleMinimap={() => {}} />);
    fireEvent.click(screen.getByLabelText("Zoom out"));
    expect(onZoomChange).toHaveBeenCalledWith(25);
  });

  it("slider onChange forwards numeric value", () => {
    const onZoomChange = vi.fn();
    render(<BottomNavRail zoom={50} onZoomChange={onZoomChange} onFit={() => {}} onToggleMinimap={() => {}} />);
    fireEvent.change(screen.getByLabelText("Zoom level"), { target: { value: "150" } });
    expect(onZoomChange).toHaveBeenCalledWith(150);
  });

  it("highlights minimap toggle when minimapOpen=true", () => {
    render(<BottomNavRail zoom={100} minimapOpen onZoomChange={() => {}} onFit={() => {}} onToggleMinimap={() => {}} />);
    expect(screen.getByLabelText("Toggle minimap").className).toMatch(/bg-hover-bg/);
  });

  it("minimap toggle exposes aria-pressed reflecting state", () => {
    const { rerender } = render(<BottomNavRail zoom={100} onZoomChange={() => {}} onFit={() => {}} onToggleMinimap={() => {}} />);
    expect(screen.getByLabelText("Toggle minimap")).toHaveAttribute("aria-pressed", "false");
    rerender(<BottomNavRail zoom={100} minimapOpen onZoomChange={() => {}} onFit={() => {}} onToggleMinimap={() => {}} />);
    expect(screen.getByLabelText("Toggle minimap")).toHaveAttribute("aria-pressed", "true");
  });

  it("zoom-button no-op when already at clamp boundary", () => {
    const onZoomChange = vi.fn();
    render(<BottomNavRail zoom={25} onZoomChange={onZoomChange} onFit={() => {}} onToggleMinimap={() => {}} />);
    fireEvent.click(screen.getByLabelText("Zoom out"));
    expect(onZoomChange).toHaveBeenCalledWith(25);
  });

  // ── P2 (E') viewport polish ─────────────────────────────────────────

  it("hides grid-snap and auto-arrange when callbacks not provided", () => {
    render(<BottomNavRail zoom={100} onZoomChange={() => {}} onFit={() => {}} onToggleMinimap={() => {}} />);
    expect(screen.queryByLabelText(/grid snap/i)).toBeNull();
    expect(screen.queryByLabelText(/auto-arrange/i)).toBeNull();
  });

  it("shows grid-snap toggle when onToggleGridSnap provided + reflects state via aria-pressed", () => {
    const onToggleGridSnap = vi.fn();
    const { rerender } = render(
      <BottomNavRail
        zoom={100}
        onZoomChange={() => {}}
        onFit={() => {}}
        onToggleMinimap={() => {}}
        gridSnap={false}
        onToggleGridSnap={onToggleGridSnap}
      />,
    );
    const btn = screen.getByLabelText(/toggle grid snap/i);
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn);
    expect(onToggleGridSnap).toHaveBeenCalledTimes(1);
    rerender(
      <BottomNavRail
        zoom={100}
        onZoomChange={() => {}}
        onFit={() => {}}
        onToggleMinimap={() => {}}
        gridSnap={true}
        onToggleGridSnap={onToggleGridSnap}
      />,
    );
    expect(screen.getByLabelText(/toggle grid snap/i).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows auto-arrange button when onAutoArrange provided + fires on click", () => {
    const onAutoArrange = vi.fn();
    render(
      <BottomNavRail
        zoom={100}
        onZoomChange={() => {}}
        onFit={() => {}}
        onToggleMinimap={() => {}}
        onAutoArrange={onAutoArrange}
      />,
    );
    fireEvent.click(screen.getByLabelText(/auto-arrange/i));
    expect(onAutoArrange).toHaveBeenCalledTimes(1);
  });
});

describe("Minimap", () => {
  it("renders one dot per node", () => {
    render(<Minimap nodes={[{x:0,y:0},{x:50,y:50}]} viewport={{x:0,y:0,w:100,h:100}} />);
    const dots = document.querySelectorAll('[data-testid="minimap-dot"]');
    expect(dots).toHaveLength(2);
  });

  it("renders one viewport rectangle", () => {
    render(<Minimap nodes={[]} viewport={{x:0,y:0,w:100,h:100}} />);
    const vp = document.querySelectorAll('[data-testid="minimap-viewport"]');
    expect(vp).toHaveLength(1);
  });

  it("normalizes node positions against worldBounds", () => {
    render(<Minimap nodes={[{x:1000,y:2000}]} viewport={{x:0,y:0,w:100,h:100}} worldBounds={{width:2000,height:4000}} />);
    const dot = document.querySelector('[data-testid="minimap-dot"]') as HTMLElement;
    expect(dot.style.left).toBe("50%"); // 1000/2000
    expect(dot.style.top).toBe("50%");  // 2000/4000
  });
});
