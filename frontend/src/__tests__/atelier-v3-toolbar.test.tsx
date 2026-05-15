// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ToolbarV3 } from "@/components/atelier/v3/ToolbarV3";

const noop = () => {};

describe("ToolbarV3", () => {
  it("emits create('video') when New Video clicked", () => {
    const onCreate = vi.fn();
    render(<ToolbarV3 onCreate={onCreate} onAskAgent={noop} onUndo={noop} onRedo={noop} />);
    fireEvent.click(screen.getByRole("button", { name: /new video node/i }));
    expect(onCreate).toHaveBeenCalledWith("video");
  });

  it("emits create('image') and create('idea') for the icon-only buttons", () => {
    const onCreate = vi.fn();
    render(<ToolbarV3 onCreate={onCreate} onAskAgent={noop} onUndo={noop} onRedo={noop} />);
    fireEvent.click(screen.getByLabelText("New Image Node"));
    expect(onCreate).toHaveBeenLastCalledWith("image");
    fireEvent.click(screen.getByLabelText("New Idea Node"));
    expect(onCreate).toHaveBeenLastCalledWith("idea");
  });

  it("emits onAskAgent when Ask Agent clicked", () => {
    const onAskAgent = vi.fn();
    render(<ToolbarV3 onCreate={noop} onAskAgent={onAskAgent} onUndo={noop} onRedo={noop} />);
    fireEvent.click(screen.getByRole("button", { name: /ask agent/i }));
    expect(onAskAgent).toHaveBeenCalled();
  });

  it("does NOT render zoom or fit controls (moved to BottomNavRail)", () => {
    render(<ToolbarV3 onCreate={noop} onAskAgent={noop} onUndo={noop} onRedo={noop} />);
    expect(screen.queryByLabelText(/zoom/i)).toBeNull();
    expect(screen.queryByLabelText(/fit/i)).toBeNull();
  });

  it("highlights Ask Agent when askActive=true", () => {
    render(<ToolbarV3 askActive onCreate={noop} onAskAgent={noop} onUndo={noop} onRedo={noop} />);
    expect(screen.getByLabelText("Ask Agent").className).toMatch(/bg-hover-bg/);
  });

  it("disables Undo when canUndo=false", () => {
    const onUndo = vi.fn();
    render(<ToolbarV3 canUndo={false} onCreate={noop} onAskAgent={noop} onUndo={onUndo} onRedo={noop} />);
    const btn = screen.getByLabelText("Undo");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onUndo).not.toHaveBeenCalled();
  });

  it("disables Redo when canRedo=false", () => {
    render(<ToolbarV3 canRedo={false} onCreate={noop} onAskAgent={noop} onUndo={noop} onRedo={noop} />);
    expect(screen.getByLabelText("Redo")).toBeDisabled();
  });

  it("Undo and Redo are clickable when can=true (default)", () => {
    const onUndo = vi.fn();
    const onRedo = vi.fn();
    render(<ToolbarV3 canUndo canRedo onCreate={noop} onAskAgent={noop} onUndo={onUndo} onRedo={onRedo} />);
    fireEvent.click(screen.getByLabelText("Undo"));
    fireEvent.click(screen.getByLabelText("Redo"));
    expect(onUndo).toHaveBeenCalled();
    expect(onRedo).toHaveBeenCalled();
  });

  it("renders role=toolbar with aria-label", () => {
    render(<ToolbarV3 onCreate={noop} onAskAgent={noop} onUndo={noop} onRedo={noop} />);
    expect(screen.getByRole("toolbar", { name: /atelier toolbar/i })).toBeInTheDocument();
  });
});
