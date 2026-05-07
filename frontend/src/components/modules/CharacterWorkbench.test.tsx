// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode, HTMLAttributes } from "react";

import CharacterWorkbench from "./CharacterWorkbench";

const { mockState, mockApi } = vi.hoisted(() => ({
  mockState: {
    currentProject: {
      id: "project-1",
      frames: [],
    },
    updateProject: vi.fn(),
  },
  mockApi: {
    uploadFile: vi.fn(),
    selectAssetVariant: vi.fn(),
    deleteAssetVariant: vi.fn(),
    favoriteAssetVariant: vi.fn(),
  },
}));

vi.mock("framer-motion", () => ({
  AnimatePresence: ({ children }: { children: ReactNode }) => <>{children}</>,
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
      触发生成
    </button>
  ),
}));

vi.mock("../common/VideoVariantSelector", () => ({
  VideoVariantSelector: () => <div>VideoVariantSelector</div>,
}));

const baseAsset = {
  id: "char-1",
  name: "林雾",
  description: "22岁，真人感女演员，黑色短发，灰色风衣。",
  full_body_asset: { variants: [], selected_id: null },
  three_view_asset: { variants: [], selected_id: null },
  headshot_asset: { variants: [], selected_id: null },
  full_body: { video_variants: [] },
  head_shot: { video_variants: [] },
};

describe("CharacterWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "alert").mockImplementation(() => undefined);
  });

  it("支持一键骨架回填全身主参考 prompt", () => {
    render(
      <CharacterWorkbench
        asset={baseAsset}
        onClose={() => undefined}
        onUpdateDescription={() => undefined}
        onGenerate={() => undefined}
        generatingTypes={[]}
        stylePrompt="写实电影感"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "全身主参考骨架" }));

    const promptTextarea = screen.getAllByPlaceholderText("输入提示词描述...")[0] as HTMLTextAreaElement;
    expect(promptTextarea.value).toContain("Full-body hero reference of [character name].");
  });

  it("在存在阻断项时拦截生成", () => {
    const onGenerate = vi.fn();

    render(
      <CharacterWorkbench
        asset={baseAsset}
        onClose={() => undefined}
        onUpdateDescription={() => undefined}
        onGenerate={onGenerate}
        generatingTypes={[]}
        stylePrompt="写实电影感"
      />,
    );

    const promptTextarea = screen.getAllByPlaceholderText("输入提示词描述...")[0] as HTMLTextAreaElement;
    fireEvent.change(promptTextarea, {
      target: { value: "dramatic lighting, shallow depth of field, ultra detail" },
    });

    fireEvent.click(screen.getAllByRole("button", { name: "触发生成" })[0]);

    expect(window.alert).toHaveBeenCalled();
    expect(onGenerate).not.toHaveBeenCalled();
  });
});
