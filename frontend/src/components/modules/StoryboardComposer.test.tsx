// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HTMLAttributes, ReactNode } from "react";

import StoryboardComposer from "./StoryboardComposer";

const { mockState, mockApi, mockCrudApi, useProjectStoreMock } = vi.hoisted(() => {
  const mockState = {
    currentProject: {
      id: "project-1",
      originalText: "测试文本",
      scenes: [
        { id: "scene-1", name: "废弃仓库" },
        { id: "scene-2", name: "走廊" },
      ],
      story_analysis: {
        scene_beats: [
          {
            id: "beat-1",
            order: 1,
            title: "第1场 · 仓库潜入",
            chapter_order: 1,
            chapter_title: "第1章 潜入夜",
            scene_name: "废弃仓库",
            summary: "林夏潜入仓库，注意到滴水声和地上的纸鹤。",
            quality_flags: [],
          },
          {
            id: "beat-2",
            order: 2,
            title: "第2场 · 走廊对峙",
            chapter_order: 1,
            chapter_title: "第1章 潜入夜",
            scene_name: "走廊",
            summary: "周沉现身，走廊气氛骤然紧张。",
            quality_flags: ["over_segmented"],
          },
        ],
      },
      frames: [
        {
          id: "frame-1",
          scene_id: "scene-1",
          story_beat_id: "beat-1",
          story_beat_title: "第1场 · 仓库潜入",
          story_beat_order: 1,
          action_description: "林夏推开仓库门，借着手电观察地面。",
        },
        {
          id: "frame-2",
          scene_id: "scene-1",
          story_beat_id: "beat-1",
          story_beat_title: "第1场 · 仓库潜入",
          story_beat_order: 1,
          action_description: "镜头切向地上的红色纸鹤。",
        },
        {
          id: "frame-3",
          scene_id: "scene-2",
          story_beat_id: "beat-2",
          story_beat_title: "第2场 · 走廊对峙",
          story_beat_order: 2,
          action_description: "周沉从走廊尽头出现。",
        },
      ],
      video_tasks: [],
    },
    selectedFrameId: "frame-1",
    setSelectedFrameId: vi.fn(),
    updateProject: vi.fn(),
    renderingFrames: new Set<string>(),
    addRenderingFrame: vi.fn(),
    removeRenderingFrame: vi.fn(),
    isAnalyzingStoryboard: false,
    setIsAnalyzingStoryboard: vi.fn(),
  };

  const mockApi = {
    analyzeToStoryboard: vi.fn(),
    getProject: vi.fn(),
    toggleFrameLock: vi.fn(),
    extractLastFrame: vi.fn(),
    uploadFrameImage: vi.fn(),
    renderFrame: vi.fn(),
  };

  const mockCrudApi = {
    deleteFrame: vi.fn(),
    copyFrame: vi.fn(),
    createFrame: vi.fn(),
    reorderFrames: vi.fn(),
  };

  const useProjectStoreMock = Object.assign(
    (selector: (state: typeof mockState) => unknown) => selector(mockState),
    {
      getState: () => mockState,
    },
  );

  return { mockState, mockApi, mockCrudApi, useProjectStoreMock };
});

vi.mock("framer-motion", () => ({
  AnimatePresence: ({ children }: { children: ReactNode }) => <>{children}</>,
  motion: new Proxy({}, {
    get: () => ({
      children,
      layout,
      layoutId,
      initial,
      animate,
      exit,
      transition,
      ...props
    }: HTMLAttributes<HTMLDivElement> & Record<string, unknown>) => <div {...props}>{children}</div>,
  }),
}));

vi.mock("@/store/projectStore", () => ({
  useProjectStore: useProjectStoreMock,
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  crudApi: mockCrudApi,
  API_URL: "http://127.0.0.1:18177",
}));

vi.mock("@/lib/storyboard-references", () => ({
  buildStoryboardCompositionData: vi.fn(() => ({})),
  buildStoryboardReferencePreview: vi.fn(() => []),
  getArtDirectionPromptPrefix: vi.fn(() => ""),
  normalizeCodexImagegenRecommendation: vi.fn(() => null),
  recommendCodexImagegenMode: vi.fn(() => ({
    mode: "safe_refs_only",
    score: 25,
    reason: "当前参考量较轻，安全直连已足够覆盖镜头一致性。",
    metrics: {
      readyCount: 0,
      totalCount: 0,
      requiredReadyCount: 0,
      missingRequiredCount: 0,
      continuityCount: 0,
      sceneCount: 0,
      characterCount: 0,
      propCount: 0,
      styleCount: 0,
      identityCount: 0,
      environmentCount: 0,
      lockedCount: 0,
    },
  })),
}));

vi.mock("@/lib/utils", () => ({
  appendAssetQueryParam: vi.fn((url: string) => url),
  getAssetUrl: vi.fn((url: string) => url),
  getAssetUrlWithTimestamp: vi.fn((url: string) => url),
  extractErrorDetail: vi.fn(() => ""),
}));

vi.mock("./StoryboardFrameEditor", () => ({
  default: () => null,
}));

describe("StoryboardComposer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("alert", vi.fn());
    vi.stubGlobal("confirm", vi.fn(() => true));
  });

  it("在顺序视图中展示每帧所属 StoryBeat 与本场帧数", () => {
    render(<StoryboardComposer />);

    expect(screen.getByTestId("frame-story-beat-frame-1")).toHaveTextContent("第1场 · 仓库潜入");
    expect(screen.getByTestId("frame-story-beat-frame-2")).toHaveTextContent("第1场 · 仓库潜入");
    expect(screen.getByTestId("frame-story-beat-frame-3")).toHaveTextContent("第2场 · 走廊对峙");
    expect(screen.getByTestId("frame-story-beat-meta-frame-1")).toHaveTextContent("章节 · 第1章 潜入夜");
    expect(screen.getByTestId("frame-story-beat-meta-frame-1")).toHaveTextContent("场景 · 废弃仓库");
    expect(screen.getByTestId("frame-story-beat-meta-frame-3")).toHaveTextContent("场景 · 走廊");
    expect(screen.getByTestId("frame-story-beat-meta-frame-3")).toHaveTextContent("疑似过切分");
    expect(screen.getAllByText("本场 2 帧").length).toBeGreaterThan(0);
  });

  it("支持切换到按场次查看并显示场次分组摘要", () => {
    render(<StoryboardComposer />);

    fireEvent.click(screen.getByRole("button", { name: "按场次查看" }));

    expect(screen.getByTestId("story-beat-group-beat-1")).toHaveTextContent("第1场 · 仓库潜入");
    expect(screen.getByTestId("story-beat-group-beat-1")).toHaveTextContent("林夏潜入仓库，注意到滴水声和地上的纸鹤。");
    expect(screen.getByTestId("story-beat-group-beat-2")).toHaveTextContent("第2场 · 走廊对峙");
    expect(screen.getByTestId("story-beat-group-meta-beat-1")).toHaveTextContent("章节 · 第1章 潜入夜");
    expect(screen.getByTestId("story-beat-group-meta-beat-1")).toHaveTextContent("场景 · 废弃仓库");
    expect(screen.getByTestId("story-beat-group-meta-beat-2")).toHaveTextContent("场景 · 走廊");
    expect(screen.getByTestId("story-beat-group-meta-beat-2")).toHaveTextContent("疑似过切分");
  });
});
