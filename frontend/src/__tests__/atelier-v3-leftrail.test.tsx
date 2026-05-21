// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LeftRailV3 } from "@/components/atelier/v3/LeftRailV3";

describe("LeftRailV3", () => {
  const noopProps = {
    onModeToggle: vi.fn(),
    onUndo: vi.fn(),
    onRedo: vi.fn(),
  };

  it("renders every canonical mode button", () => {
    render(<LeftRailV3 activeMode={null} {...noopProps} />);
    for (const label of ["Add", "Assets", "Workflows", "History", "Director", "Agent", "Sequence"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });

  it("calls onModeToggle with the mode key when a button is clicked", () => {
    const onModeToggle = vi.fn();
    render(<LeftRailV3 activeMode={null} {...noopProps} onModeToggle={onModeToggle} />);
    fireEvent.click(screen.getByLabelText("Director"));
    expect(onModeToggle).toHaveBeenCalledWith("director");
    fireEvent.click(screen.getByLabelText("Workflows"));
    expect(onModeToggle).toHaveBeenCalledWith("workflows");
  });

  it("highlights the active mode via aria-selected", () => {
    render(<LeftRailV3 activeMode="director" {...noopProps} />);
    const directorBtn = screen.getByLabelText("Director");
    expect(directorBtn.getAttribute("aria-selected")).toBe("true");
    const workflowsBtn = screen.getByLabelText("Workflows");
    expect(workflowsBtn.getAttribute("aria-selected")).toBe("false");
  });
});
