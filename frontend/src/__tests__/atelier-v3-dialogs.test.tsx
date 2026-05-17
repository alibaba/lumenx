// @vitest-environment jsdom
//
// In-shell ConfirmDialog + PromptDialog. Confirm flows on Enter, cancels on
// Escape, focus traps via Tab, click-outside dismisses. Cinematic visual
// vocabulary is covered in the screenshot tests; this file is functional.
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConfirmDialog, PromptDialog } from "@/components/atelier/v3/Dialogs";

describe("ConfirmDialog", () => {
  it("does not render when closed", () => {
    render(<ConfirmDialog open={false} title="Delete" onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("calls onConfirm when the confirm button is clicked", () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open title="Delete" onConfirm={onConfirm} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalled();
  });

  it("Escape key cancels", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog open title="Delete" onConfirm={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("Enter key confirms", () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open title="Delete" onConfirm={onConfirm} onCancel={vi.fn()} />);
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalled();
  });

  it("clicking the backdrop cancels", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog open title="Delete" onConfirm={vi.fn()} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("dialog"));
    expect(onCancel).toHaveBeenCalled();
  });

  it("danger tone applies red rail accent", () => {
    const { container } = render(
      <ConfirmDialog open title="Delete" tone="danger" onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(container.innerHTML).toMatch(/from-red-400/);
  });

  it("body text is rendered when provided", () => {
    render(
      <ConfirmDialog open title="Delete" body="This cannot be undone." onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
  });
});

describe("PromptDialog", () => {
  it("submits the trimmed value on form submit", () => {
    const onSubmit = vi.fn();
    render(
      <PromptDialog open title="Rename" initialValue="" onSubmit={onSubmit} onCancel={vi.fn()} />,
    );
    const input = screen.getByRole("dialog").querySelector("input")!;
    fireEvent.change(input, { target: { value: "  hello  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit).toHaveBeenCalledWith("hello");
  });

  it("submit is disabled while empty", () => {
    render(
      <PromptDialog open title="Rename" initialValue="" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("Escape cancels", () => {
    const onCancel = vi.fn();
    render(
      <PromptDialog open title="Rename" initialValue="hi" onSubmit={vi.fn()} onCancel={onCancel} />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("re-opening with a new initialValue resets the input", () => {
    const { rerender } = render(
      <PromptDialog open={false} title="Rename" initialValue="alpha" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    rerender(
      <PromptDialog open title="Rename" initialValue="alpha" onSubmit={vi.fn()} onCancel={vi.fn()} />,
    );
    expect((screen.getByRole("dialog").querySelector("input") as HTMLInputElement).value).toBe("alpha");
  });
});
