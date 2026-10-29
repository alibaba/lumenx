// @vitest-environment happy-dom
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";

import BrandMark from "@/components/atelier/v3/BrandMark";
import { MiniMarkdown } from "@/components/atelier/v3/MiniMarkdown";
import { ConfirmDialog, PromptDialog } from "@/components/atelier/v3/Dialogs";
import { BottomNavRail } from "@/components/atelier/v3/BottomNavRail";
import { ToolbarV3 } from "@/components/atelier/v3/ToolbarV3";
import { LeftRailV3 } from "@/components/atelier/v3/LeftRailV3";
import { RightControlStack } from "@/components/atelier/v3/RightControlStack";
import { PortDot } from "@/components/atelier/v3/NodePort";
import { IdeaNode } from "@/components/atelier/v3/IdeaNode";
import { CommentNode } from "@/components/atelier/v3/CommentNode";
import { PlanNode } from "@/components/atelier/v3/PlanNode";
import { RegionFrame } from "@/components/atelier/v3/RegionFrame";
import { toMediaNodeView } from "@/components/atelier/v3/types";
import type { AtelierNode } from "@/lib/atelierTypes";

function renderWithIntl(ui: React.ReactElement) {
  return render(
    <NextIntlClientProvider
      locale="en"
      timeZone="Asia/Shanghai"
      messages={{
        atelier: {
          node: {
            expand: "Expand",
            collapse: "Collapse",
            ideaChars: "{count} chars",
            ideaStamp: "Idea {num}",
            ideaEmpty: "Empty idea",
            edit: "Open editor",
          },
          dialog: {
            cancel: "Cancel",
            confirm: "Confirm",
            save: "Save",
          },
          status: {
            draft: "Draft",
            pending: "Pending",
            processing: "Processing",
            completed: "Completed",
            failed: "Failed",
          },
        },
      }}
    >
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("Atelier V3 primitives", () => {
  it("renders markdown without raw HTML and opens links safely", () => {
    render(<MiniMarkdown source={"**Bold** *em* `code` [site](https://example.com) <script>"} />);

    expect(screen.getByText("Bold").tagName).toBe("STRONG");
    expect(screen.getByText("em").tagName).toBe("EM");
    expect(screen.getByText("code").tagName).toBe("CODE");
    const link = screen.getByRole("link", { name: "site" });
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.getByText("<script>", { exact: false })).toBeInTheDocument();
  });

  it.each([
    '<script>alert(1)</script>',
    '<SCRIPT>alert(1)</SCRIPT>',
    '<ScRiPt>alert(1)</sCrIpT>',
    '<script>alert(1)</script ignored="value">',
    '<img src=x onerror="alert(1)">',
    '<svg onload="alert(1)"></svg>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
  ])("renders HTML-looking input as literal text: %s", (source) => {
    const { container } = render(<MiniMarkdown source={source} />);

    expect(container.textContent).toBe(source);
    expect(container.querySelector("script, img, svg, iframe")).toBeNull();
  });

  it("invokes toolbar and nav callbacks while respecting disabled undo and redo", () => {
    const onCreate = vi.fn();
    const onAskAgent = vi.fn();
    const onUndo = vi.fn();
    const onRedo = vi.fn();
    const onZoomChange = vi.fn();
    const onFit = vi.fn();
    const onToggleMinimap = vi.fn();
    const onToggleGridSnap = vi.fn();
    const onAutoArrange = vi.fn();

    render(
      <>
        <ToolbarV3
          onCreate={onCreate}
          onAskAgent={onAskAgent}
          onUndo={onUndo}
          onRedo={onRedo}
          canUndo={false}
          canRedo={false}
        />
        <BottomNavRail
          zoom={1}
          onZoomChange={onZoomChange}
          onFit={onFit}
          onToggleMinimap={onToggleMinimap}
          minimapOpen={false}
          gridSnap={false}
          onToggleGridSnap={onToggleGridSnap}
          onAutoArrange={onAutoArrange}
        />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "New Video Node" }));
    fireEvent.click(screen.getByRole("button", { name: "New Image Node" }));
    fireEvent.click(screen.getByRole("button", { name: "New Idea Node" }));
    fireEvent.click(screen.getByRole("button", { name: "Ask Agent" }));
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    fireEvent.click(screen.getByRole("button", { name: "Redo" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    fireEvent.click(screen.getByRole("button", { name: "Fit view" }));
    fireEvent.click(screen.getByRole("button", { name: "Toggle minimap" }));
    fireEvent.click(screen.getByRole("button", { name: "Toggle grid snap" }));
    fireEvent.click(screen.getByRole("button", { name: "Auto-arrange" }));

    expect(onCreate.mock.calls.map(([kind]) => kind)).toEqual(["video", "image", "idea"]);
    expect(onAskAgent).toHaveBeenCalledTimes(1);
    expect(onUndo).not.toHaveBeenCalled();
    expect(onRedo).not.toHaveBeenCalled();
    expect(onZoomChange.mock.calls.map(([z]) => z)).toEqual([26, 25]);
    expect(onFit).toHaveBeenCalledTimes(1);
    expect(onToggleMinimap).toHaveBeenCalledTimes(1);
    expect(onToggleGridSnap).toHaveBeenCalledTimes(1);
    expect(onAutoArrange).toHaveBeenCalledTimes(1);
  });

  it("selects text nodes from pointer and keyboard, hides while editing, and commits plan title edits", () => {
    const onSelect = vi.fn();
    const onTitleCommit = vi.fn();
    const onOpenEditor = vi.fn();

    const { rerender } = renderWithIntl(
      <>
        <IdeaNode id="idea123" body={"long ".repeat(40)} x={10} y={20} onSelect={onSelect} onOpenEditor={onOpenEditor} />
        <CommentNode id="comment456" body="Review this" author="Ada" x={30} y={40} onSelect={onSelect} />
        <PlanNode
          id="plan789"
          title="Original plan"
          bullets={["One", "Two"]}
          x={50}
          y={60}
          onSelect={onSelect}
          onTitleCommit={onTitleCommit}
        />
      </>,
    );

    fireEvent.pointerDown(screen.getByText(/long long/));
    fireEvent.keyDown(screen.getByText("Review this").closest('[role="button"]')!, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("idea123");
    expect(onSelect).toHaveBeenCalledWith("comment456");

    fireEvent.click(screen.getByRole("button", { name: "Open editor" }));
    expect(onOpenEditor).toHaveBeenCalledWith("idea123");

    fireEvent.doubleClick(screen.getByText("Original plan"));
    const input = screen.getByDisplayValue("Original plan");
    fireEvent.change(input, { target: { value: "Updated plan" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onTitleCommit).toHaveBeenCalledWith("Updated plan");

    rerender(
      <NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={{ atelier: { node: {} } }}>
        <IdeaNode id="idea123" body="hidden" x={10} y={20} editing />
      </NextIntlClientProvider>,
    );
    expect(screen.queryByText("hidden")).not.toBeInTheDocument();
  });

  it("handles dialogs, rail modes, ports and region interactions through explicit callbacks", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const onPromptSubmit = vi.fn();
    const onModeChange = vi.fn();
    const onSelect = vi.fn();
    const onResizeStart = vi.fn();
    const onMoveStart = vi.fn();
    const onToggleCollapse = vi.fn();
    const onPortDown = vi.fn();

    renderWithIntl(
      <>
        <ConfirmDialog open title="Delete node" body="Really?" confirmLabel="Delete" onConfirm={onConfirm} onCancel={onCancel} />
        <PromptDialog open title="Rename" initialValue="Old" onSubmit={onPromptSubmit} onCancel={onCancel} />
        <LeftRailV3 activeMode={null} onModeToggle={onModeChange} onUndo={vi.fn()} onRedo={vi.fn()} canUndo canRedo />
        <PortDot kind="output" interactive onPointerDown={onPortDown} />
        <RegionFrame
          id="region1"
          x={1}
          y={2}
          width={300}
          height={180}
          title="Act 1"
          selected
          childCount={2}
          onSelect={onSelect}
          onMoveStart={onMoveStart}
          onResizeStart={onResizeStart}
          onToggleCollapse={onToggleCollapse}
        />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    const rename = screen.getByDisplayValue("Old");
    fireEvent.change(rename, { target: { value: "New" } });
    fireEvent.submit(rename.closest("form")!);
    expect(onPromptSubmit).toHaveBeenCalledWith("New");

    fireEvent.click(screen.getByRole("tab", { name: "Assets · A" }));
    expect(onModeChange).toHaveBeenCalledWith("assets");

    fireEvent.pointerDown(screen.getByRole("button", { name: "Output port — drag to connect" }));
    expect(onPortDown).toHaveBeenCalledTimes(1);

    fireEvent.pointerDown(screen.getByText("Act 1"));
    expect(onMoveStart).toHaveBeenCalledWith("region1", expect.any(Object));
    fireEvent.pointerDown(screen.getByTestId("region-handle-nw"));
    expect(onResizeStart).toHaveBeenCalledWith("region1", expect.any(String), expect.any(Object));
    fireEvent.click(screen.getByRole("button", { name: /collapse/i }));
    expect(onToggleCollapse).toHaveBeenCalledWith("region1");
  });

  it("projects foundation Atelier media nodes through resolved asset URLs", () => {
    const node: AtelierNode = {
      id: "n-video",
      project_id: "p1",
      type: "video",
      title: "Video",
      prompt: "",
      status: "completed",
      x: 12,
      y: 34,
      width: 640,
      height: 360,
      source_project_id: null,
      frame_id: null,
      asset_id: null,
      video_task_id: null,
      media_urls: ["uploads/a.mp4"],
      data: { poster_url: "uploads/a.jpg", filename: "a.mp4", duration: 61, selected_as_take: true },
      created_by: "user",
      created_at: 1,
      updated_at: 2,
    };

    expect(toMediaNodeView(node, { selectedNodeId: "n-video" })).toMatchObject({
      id: "n-video",
      kind: "video",
      src: expect.stringContaining("/files/uploads/a.mp4"),
      poster: expect.stringContaining("/files/uploads/a.jpg"),
      filename: "a.mp4",
      duration: "1:01",
      selected: true,
      selectedAsTake: true,
      x: 12,
      y: 34,
      width: 640,
      height: 360,
    });
  });

  it("renders BrandMark and right control stack actions", () => {
    const onZoomIn = vi.fn();
    const onZoomOut = vi.fn();
    const onFit = vi.fn();
    const onToggleMinimap = vi.fn();
    render(
      <>
        <BrandMark size={24} />
        <RightControlStack zoom={100} onZoomChange={(z) => (z > 100 ? onZoomIn() : onZoomOut())} onFit={onFit} onToggleMinimap={onToggleMinimap} minimapOpen rightOffset={12} />
      </>,
    );

    expect(screen.getByRole("img", { name: "LumenX" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    fireEvent.click(screen.getByRole("button", { name: "Fit to view" }));
    fireEvent.click(screen.getByRole("button", { name: "Toggle minimap" }));
    expect(onZoomIn).toHaveBeenCalledTimes(1);
    expect(onZoomOut).toHaveBeenCalledTimes(1);
    expect(onFit).toHaveBeenCalledTimes(1);
    expect(onToggleMinimap).toHaveBeenCalledTimes(1);
  });
});
