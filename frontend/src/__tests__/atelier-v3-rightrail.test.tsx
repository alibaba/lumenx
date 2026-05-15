// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RightRailV3 } from "@/components/atelier/v3/RightRailV3";

describe("RightRailV3", () => {
  it("renders Creative Agent header with Active status", () => {
    render(<RightRailV3 mode="on_request" agentStatus="active"><div>body</div></RightRailV3>);
    expect(screen.getByText("Creative Agent")).toBeInTheDocument();
    expect(screen.getByText(/active/i)).toBeInTheDocument();
  });

  it("does NOT render a Node tab", () => {
    render(<RightRailV3 mode="on_request" agentStatus="active"><div /></RightRailV3>);
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByText(/^node$/i)).toBeNull();
  });

  it("renders all 4 permission modes with one checked", () => {
    render(<RightRailV3 mode="on_request" agentStatus="active"><div /></RightRailV3>);
    expect(screen.getByLabelText("Untrusted")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByLabelText("On failure")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByLabelText("On request")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText("Never")).toHaveAttribute("aria-checked", "false");
  });

  it("calls onModeChange when a permission mode is clicked", () => {
    const onModeChange = vi.fn();
    render(<RightRailV3 mode="on_request" agentStatus="active" onModeChange={onModeChange}><div /></RightRailV3>);
    fireEvent.click(screen.getByLabelText("Never"));
    expect(onModeChange).toHaveBeenCalledWith("never");
  });

  it("renders the children body content", () => {
    render(<RightRailV3 mode="on_request" agentStatus="active"><div>my-conversation</div></RightRailV3>);
    expect(screen.getByText("my-conversation")).toBeInTheDocument();
  });

  it("status dot reflects agent status — pulsing for thinking", () => {
    const { rerender } = render(<RightRailV3 mode="on_request" agentStatus="thinking"><div /></RightRailV3>);
    const dot = screen.getByTestId("agent-status-dot");
    expect(dot.className).toMatch(/animate-pulse/);
    expect(dot.className).toMatch(/blue/);
    rerender(<RightRailV3 mode="on_request" agentStatus="active"><div /></RightRailV3>);
    expect(screen.getByTestId("agent-status-dot").className).toMatch(/emerald/);
  });

  it("status dot pulses red for failed", () => {
    render(<RightRailV3 mode="on_request" agentStatus="failed"><div /></RightRailV3>);
    expect(screen.getByTestId("agent-status-dot").className).toMatch(/red/);
  });

  it("collapse button calls onCollapse", () => {
    const onCollapse = vi.fn();
    render(<RightRailV3 mode="on_request" agentStatus="active" onCollapse={onCollapse}><div /></RightRailV3>);
    fireEvent.click(screen.getByLabelText("Collapse panel"));
    expect(onCollapse).toHaveBeenCalled();
  });

  it("renders 56w handle when collapsed=true", () => {
    const onCollapse = vi.fn();
    const { container } = render(<RightRailV3 collapsed mode="on_request" agentStatus="active" onCollapse={onCollapse}><div /></RightRailV3>);
    const aside = container.firstElementChild as HTMLElement;
    expect(aside.className).toMatch(/w-\[56px\]/);
    // body content not rendered in collapsed state
    expect(screen.queryByText("Creative Agent")).toBeNull();
    fireEvent.click(screen.getByLabelText("Expand panel"));
    expect(onCollapse).toHaveBeenCalled();
  });

  it("permission hint reflects the active mode", () => {
    render(<RightRailV3 mode="never" agentStatus="active"><div /></RightRailV3>);
    expect(screen.getByText(/Run allowed tools within hard limits/i)).toBeInTheDocument();
  });

  it("permission radio group: only the checked radio is tabIndex=0", () => {
    render(<RightRailV3 mode="on_request" agentStatus="active"><div /></RightRailV3>);
    expect(screen.getByLabelText("On request")).toHaveAttribute("tabIndex", "0");
    expect(screen.getByLabelText("Untrusted")).toHaveAttribute("tabIndex", "-1");
    expect(screen.getByLabelText("On failure")).toHaveAttribute("tabIndex", "-1");
    expect(screen.getByLabelText("Never")).toHaveAttribute("tabIndex", "-1");
  });

  it("ArrowRight in radiogroup moves selection forward and wraps", () => {
    const onModeChange = vi.fn();
    render(<RightRailV3 mode="never" agentStatus="active" onModeChange={onModeChange}><div /></RightRailV3>);
    fireEvent.keyDown(screen.getByLabelText("Never"), { key: "ArrowRight" });
    expect(onModeChange).toHaveBeenCalledWith("untrusted");
  });

  it("ArrowLeft in radiogroup moves selection backward and wraps", () => {
    const onModeChange = vi.fn();
    render(<RightRailV3 mode="untrusted" agentStatus="active" onModeChange={onModeChange}><div /></RightRailV3>);
    fireEvent.keyDown(screen.getByLabelText("Untrusted"), { key: "ArrowLeft" });
    expect(onModeChange).toHaveBeenCalledWith("never");
  });

  it("Home/End jump to first/last permission mode", () => {
    const onModeChange = vi.fn();
    render(<RightRailV3 mode="on_failure" agentStatus="active" onModeChange={onModeChange}><div /></RightRailV3>);
    fireEvent.keyDown(screen.getByLabelText("On failure"), { key: "End" });
    expect(onModeChange).toHaveBeenCalledWith("never");
    onModeChange.mockClear();
    fireEvent.keyDown(screen.getByLabelText("On failure"), { key: "Home" });
    expect(onModeChange).toHaveBeenCalledWith("untrusted");
  });
});
