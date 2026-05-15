// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BottomNavRail } from "@/components/atelier/v3/BottomNavRail";
import { Minimap } from "@/components/atelier/v3/Minimap";

describe("BottomNavRail", () => {
  it("renders zoom value", () => {
    render(<BottomNavRail zoom={85} onZoomChange={() => {}} onFit={() => {}} onToggleMinimap={() => {}} />);
    expect(screen.getByText("85%")).toBeInTheDocument();
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
