// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode, HTMLAttributes } from "react";

import StoryboardFrameEditor from "./StoryboardFrameEditor";

const { mockState, mockApi } = vi.hoisted(() => ({
  mockState: {
    currentProject: {
      id: "project-1",
      scenes: [{ id: "scene-1", name: "夜巷", image_url: "https://example.com/scene.png", locked: true }],
      characters: [
        {
          id: "char-1",
          name: "林夏",
          locked: true,
          three_view_asset: {
            selected_id: "char-ref",
            variants: [{ id: "char-ref", url: "https://example.com/linxia.png", created_at: 0 }],
          },
        },
      ],
      props: [{ id: "prop-1", name: "红色纸鹤", locked: true }],
      frames: [
        {
          id: "frame-1",
          scene_id: "scene-1",
          action_description: "上一帧镜头",
          rendered_image_asset: {
            selected_id: "prev-variant",
            variants: [{ id: "prev-variant", url: "https://example.com/prev-frame.png", created_at: 0 }],
          },
          rendered_image_url: "https://example.com/prev-frame.png",
        },
        {
          id: "frame-2",
          scene_id: "scene-1",
          action_description: "角色停在门口观察四周",
          dialogue: "先别出声。",
          character_ids: ["char-1"],
          prop_ids: ["prop-1"],
          rendered_image_asset: { selected_id: null, variants: [] },
          composition_data: null,
        },
      ],
    },
    updateProject: vi.fn(),
  },
  mockApi: {
    renderFrame: vi.fn(),
    selectAssetVariant: vi.fn(),
    deleteAssetVariant: vi.fn(),
  },
}));

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, {
    get: () => ({
      children,
      layout,
      initial,
      animate,
      exit,
      transition,
      ...props
    }: HTMLAttributes<HTMLDivElement> & Record<string, unknown>) => <div {...props}>{children}</div>,
  }),
}));

vi.mock("@/store/projectStore", () => ({
  useProjectStore: (selector: (state: typeof mockState) => unknown) => selector(mockState),
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
}));

vi.mock("../common/VariantSelector", () => ({
  VariantSelector: ({ onGenerate }: any) => (
    <button type="button" onClick={() => onGenerate(1)}>
      触发分镜生成
    </button>
  ),
}));

describe("StoryboardFrameEditor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "alert").mockImplementation(() => undefined);
    mockApi.renderFrame.mockResolvedValue(mockState.currentProject);
  });

  it("可视化上一帧默认参考，并把连续性参数透传给渲染接口", async () => {
    render(
      <StoryboardFrameEditor
        frame={mockState.currentProject.frames[1]}
        onClose={() => undefined}
      />,
    );

    expect(screen.getByAltText("上一帧选中图")).toBeInTheDocument();
    expect(screen.getByText("生成前引用预览")).toBeInTheDocument();
    expect(screen.getByText("林夏")).toBeInTheDocument();
    expect(screen.getByText("红色纸鹤")).toBeInTheDocument();
    expect(screen.getAllByText("已锁").length).toBeGreaterThan(0);
    expect(screen.getByText("缺主参考")).toBeInTheDocument();

    const promptTextarea = screen.getByPlaceholderText("输入要用于重绘的提示词...") as HTMLTextAreaElement;
    fireEvent.change(promptTextarea, {
      target: { value: "scene shot of the same character waiting by the doorway, keep same scene, preserve same lighting" },
    });

    fireEvent.click(screen.getByRole("button", { name: "触发分镜生成" }));

    await waitFor(() => {
      expect(mockApi.renderFrame).toHaveBeenCalled();
    });

    expect(mockApi.renderFrame.mock.calls[0][2]).toMatchObject({
      continuity_lock: true,
      continuity_source_frame_id: "frame-1",
    });
    expect(mockApi.renderFrame.mock.calls[0][2].reference_image_urls[0]).toBe("https://example.com/prev-frame.png");
  });

  it("在提示词存在阻断项时拦截分镜重绘", () => {
    render(
      <StoryboardFrameEditor
        frame={mockState.currentProject.frames[1]}
        onClose={() => undefined}
      />,
    );

    const promptTextarea = screen.getByPlaceholderText("输入要用于重绘的提示词...") as HTMLTextAreaElement;
    fireEvent.change(promptTextarea, {
      target: { value: "scene shot of character, static camera, pan left" },
    });

    fireEvent.click(screen.getByRole("button", { name: "触发分镜生成" }));

    expect(window.alert).toHaveBeenCalled();
    expect(mockApi.renderFrame).not.toHaveBeenCalled();
  });
});
