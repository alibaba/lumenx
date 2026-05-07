// @vitest-environment jsdom

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode, HTMLAttributes } from "react";

import ConsistencyVault from "./ConsistencyVault";

const { mockState, mockApi } = vi.hoisted(() => ({
  mockState: {
    currentProject: {
      id: "project-1",
      characters: [],
      scenes: [
        {
          id: "scene-1",
          name: "旧仓库",
          description: "雨夜里的旧仓库，顶部冷灯，地面有积水反光。",
          image_asset: { variants: [], selected_id: null },
          image_url: "",
        },
      ],
      props: [],
    },
    updateProject: vi.fn(),
    generatingTasks: [],
    addGeneratingTask: vi.fn(),
    removeGeneratingTask: vi.fn(),
  },
  mockApi: {
    updateAssetDescription: vi.fn(),
    generateAsset: vi.fn(),
    getTaskStatus: vi.fn(),
    getProject: vi.fn(),
    generateMotionRef: vi.fn(),
    deleteAssetVideo: vi.fn(),
    syncDescriptions: vi.fn(),
    toggleAssetLock: vi.fn(),
    uploadFile: vi.fn(),
    updateAssetImage: vi.fn(),
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
  API_URL: "http://127.0.0.1:18177",
  crudApi: {
    deleteCharacter: vi.fn(),
    deleteScene: vi.fn(),
    deleteProp: vi.fn(),
    createCharacter: vi.fn(),
    createScene: vi.fn(),
    createProp: vi.fn(),
  },
}));

vi.mock("./CharacterWorkbench", () => ({
  default: () => <div>CharacterWorkbench</div>,
}));

vi.mock("../common/VariantSelector", () => ({
  VariantSelector: ({ onGenerate }: any) => (
    <button type="button" onClick={() => onGenerate(1)}>
      触发资产生成
    </button>
  ),
}));

vi.mock("../common/VideoVariantSelector", () => ({
  VideoVariantSelector: () => <div>VideoVariantSelector</div>,
}));

describe("ConsistencyVault", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "alert").mockImplementation(() => undefined);
    vi.spyOn(window, "confirm").mockImplementation(() => true);
  });

  it("支持在场景详情里追加模板 prompt", () => {
    render(<ConsistencyVault />);

    fireEvent.click(screen.getByRole("button", { name: /场景/ }));
    fireEvent.click(screen.getByText("旧仓库"));

    fireEvent.click(screen.getByRole("button", { name: "追加 连续场景版" }));

    const promptTextarea = screen.getByPlaceholderText("描述这个素材的静态画面应该如何呈现...") as HTMLTextAreaElement;
    expect(promptTextarea.value).toContain("Treat this as a reusable master location.");
  });
});
