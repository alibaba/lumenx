// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HTMLAttributes, ReactNode } from "react";

import ScriptProcessor from "./ScriptProcessor";

const { mockState, mockApi, mockCrudApi } = vi.hoisted(() => ({
  mockState: {
    currentProject: {
      id: "project-1",
      title: "测试项目",
      originalText: "林夏穿着深色风衣走进废弃仓库，看见地上的红色纸鹤。",
      characters: [
        { id: "char-1", name: "林夏", description: "短发，深色风衣", visual_weight: 5 },
        { id: "char-2", name: "周沉", description: "黑色夹克，神情克制", visual_weight: 4 },
      ],
      scenes: [{ id: "scene-1", name: "废弃仓库", description: "昏暗潮湿的旧仓库", visual_weight: 4 }],
      props: [{ id: "prop-1", name: "红色纸鹤", description: "被雨水打湿的红色纸鹤" }],
      story_analysis: {
        summary: "林夏夜探废弃仓库，在现场发现关键线索纸鹤。",
        plot_points: ["林夏进入仓库调查。", "她发现了地上的红色纸鹤。"],
        scene_beats: [
          {
            id: "story_beat_001",
            order: 1,
            chapter_order: 1,
            chapter_title: "第1章 合租条例",
            title: "第1场 · 废弃仓库",
            summary: "林夏进入仓库调查，并注意到地上的红色纸鹤。",
            action_summary: "林夏进入仓库，先确认周围是否安全。",
            dialogue_excerpt: "林夏：这里不对劲。",
            storyboard_goal: "交代仓库空间、纸鹤线索和林夏的警觉状态。",
            scene_name: "废弃仓库",
            location_hint: "废弃仓库",
            time_hint: "夜晚",
            character_names: ["林夏", "周沉"],
            prop_names: ["红色纸鹤"],
            source_excerpt: "林夏穿着深色风衣走进废弃仓库，看见地上的红色纸鹤。",
            storyboard_focus: "仓库空间、林夏动作、纸鹤线索同时入镜。",
            quality_flags: [],
          },
          {
            id: "story_beat_002",
            order: 2,
            chapter_order: 2,
            chapter_title: "第2章 夜审",
            title: "第2场 · 走廊",
            summary: "这一场只留下了章节锚点，等待人工补足角色和场景。",
            action_summary: "",
            dialogue_excerpt: "",
            storyboard_goal: "",
            scene_name: "",
            location_hint: "",
            time_hint: "",
            character_names: [],
            prop_names: [],
            source_excerpt: "尾声",
            storyboard_focus: "",
            quality_flags: ["over_segmented", "no_characters", "no_scene"],
          },
        ],
        character_presence: [
          {
            character_id: "char-1",
            character_name: "林夏",
            scene_beat_ids: ["story_beat_001"],
            scene_titles: ["第1场 · 废弃仓库"],
            mention_count: 2,
            highlights: ["林夏进入仓库调查，并注意到地上的红色纸鹤。"],
          },
          {
            character_id: "char-2",
            character_name: "周沉",
            scene_beat_ids: ["story_beat_001"],
            scene_titles: ["第1场 · 废弃仓库"],
            mention_count: 1,
            highlights: ["周沉在仓库里与林夏对峙。"],
          },
        ],
        character_relationships: [
          {
            pair_id: "char-1::char-2",
            source_character_id: "char-1",
            source_character_name: "林夏",
            target_character_id: "char-2",
            target_character_name: "周沉",
            co_scene_count: 1,
            shared_scene_beat_ids: ["story_beat_001"],
            shared_scene_titles: ["第1场 · 废弃仓库"],
            relationship_hint: "林夏 与 周沉 在废弃仓库同场对峙，适合作为连续镜头锚点。",
          },
        ],
      },
      frames: [{ id: "frame-1", story_beat_id: "story_beat_001" }],
    },
    updateProject: vi.fn(),
    analyzeProject: vi.fn(),
    isAnalyzing: false,
  },
  mockApi: {
    getProject: vi.fn(),
    updateAssetAttributes: vi.fn(),
    updateStoryBeat: vi.fn(),
    analyzeStoryboardBeat: vi.fn(),
  },
  mockCrudApi: {
    deleteCharacter: vi.fn(),
    deleteScene: vi.fn(),
    deleteProp: vi.fn(),
    createCharacter: vi.fn(),
    createScene: vi.fn(),
    createProp: vi.fn(),
  },
}));

vi.mock("framer-motion", () => ({
  AnimatePresence: ({ children }: { children: ReactNode }) => <>{children}</>,
  motion: new Proxy({}, {
    get: () => ({
      children,
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
  crudApi: mockCrudApi,
}));

describe("ScriptProcessor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("alert", vi.fn());
  });

  it("展示章节树、诊断标签、角色出场和共场关系", () => {
    render(<ScriptProcessor />);

    expect(screen.getByText("剧情摘要")).toBeTruthy();
    expect(screen.getByText("林夏夜探废弃仓库，在现场发现关键线索纸鹤。")).toBeTruthy();
    expect(screen.getByText("场次列表")).toBeTruthy();
    expect(screen.getByText("场次树")).toBeTruthy();
    expect(screen.getAllByText("第1章 合租条例").length).toBeGreaterThan(0);
    expect(screen.getAllByText("第2章 夜审").length).toBeGreaterThan(0);
    expect(screen.getByText("场次质量诊断")).toBeTruthy();
    expect(screen.getByText("疑似过切分 1")).toBeTruthy();
    expect(screen.getByRole("button", { name: "定位当前场次" })).toBeTruthy();
    expect(screen.getAllByText("第1场 · 废弃仓库").length).toBeGreaterThan(0);
    expect(screen.getByText("角色出场 / 共场关系")).toBeTruthy();
    expect(screen.getByText("角色关系 / 共场统计")).toBeTruthy();
    expect(screen.getByText("林夏 × 周沉")).toBeTruthy();
    expect(screen.getByText("实体工作台")).toBeTruthy();
  });

  it("支持切换角色关系图谱视图", async () => {
    render(<ScriptProcessor />);

    fireEvent.click(screen.getByRole("button", { name: "图谱视图" }));

    await waitFor(() => {
      expect(screen.getByTestId("relationship-graph")).toBeTruthy();
    });
  });

  it("支持保存场次编辑并触发按场次重算分镜", async () => {
    mockApi.updateStoryBeat.mockResolvedValue(mockState.currentProject);
    mockApi.analyzeStoryboardBeat.mockResolvedValue(mockState.currentProject);

    render(<ScriptProcessor />);

    fireEvent.change(screen.getByLabelText("动作摘要"), {
      target: { value: "林夏沿着水痕继续向仓库深处推进。" },
    });
    fireEvent.change(screen.getByLabelText("对白摘录"), {
      target: { value: "林夏：这里不对劲，先别出声。" },
    });
    fireEvent.change(screen.getByLabelText("分镜目标"), {
      target: { value: "锁定纸鹤线索与林夏的警觉状态，保持仓库空间连续。" },
    });

    fireEvent.click(screen.getByRole("button", { name: "保存场次" }));

    await waitFor(() => {
      expect(mockApi.updateStoryBeat).toHaveBeenCalledWith("project-1", "story_beat_001", {
        actionSummary: "林夏沿着水痕继续向仓库深处推进。",
        dialogueExcerpt: "林夏：这里不对劲，先别出声。",
        storyboardGoal: "锁定纸鹤线索与林夏的警觉状态，保持仓库空间连续。",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "按场次重算分镜" }));

    await waitFor(() => {
      expect(mockApi.analyzeStoryboardBeat).toHaveBeenCalledWith("project-1", "story_beat_001");
    });
  });
});
