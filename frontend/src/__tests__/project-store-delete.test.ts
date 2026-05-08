import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { deleteProjectMock } = vi.hoisted(() => ({
  deleteProjectMock: vi.fn(),
}));

const localStorageMock = {
  getItem: vi.fn(() => null),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
  key: vi.fn(() => null),
  length: 0,
};

Object.defineProperty(globalThis, "localStorage", {
  value: localStorageMock,
  configurable: true,
});

Object.defineProperty(globalThis, "window", {
  value: { localStorage: localStorageMock },
  configurable: true,
});

vi.mock("@/lib/api", () => ({
  api: {
    deleteProject: deleteProjectMock,
  },
}));

import { useProjectStore } from "@/store/projectStore";

describe("project store delete flow", () => {
  let errorSpy: ReturnType<typeof vi.spyOn> | null = null;
  let warnSpy: ReturnType<typeof vi.spyOn> | null = null;

  beforeEach(() => {
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    deleteProjectMock.mockReset();
    useProjectStore.setState({
      projects: [
        {
          id: "project-1",
          title: "测试项目",
          originalText: "",
          characters: [],
          scenes: [],
          props: [],
          frames: [],
          status: "completed",
          createdAt: "2026-05-07T00:00:00.000Z",
          updatedAt: "2026-05-07T00:00:00.000Z",
        },
      ],
      currentProject: {
        id: "project-1",
        title: "测试项目",
        originalText: "",
        characters: [],
        scenes: [],
        props: [],
        frames: [],
        status: "completed",
        createdAt: "2026-05-07T00:00:00.000Z",
        updatedAt: "2026-05-07T00:00:00.000Z",
      },
    });
  });

  afterEach(() => {
    errorSpy?.mockRestore();
    warnSpy?.mockRestore();
    errorSpy = null;
    warnSpy = null;
  });

  it("keeps local state intact when backend delete fails", async () => {
    deleteProjectMock.mockRejectedValue(new Error("boom"));

    await expect(useProjectStore.getState().deleteProject("project-1")).rejects.toThrow("boom");

    const state = useProjectStore.getState();
    expect(state.projects).toHaveLength(1);
    expect(state.currentProject?.id).toBe("project-1");
  });

  it("removes the project after a successful backend delete", async () => {
    deleteProjectMock.mockResolvedValue({ status: "deleted" });

    await useProjectStore.getState().deleteProject("project-1");

    const state = useProjectStore.getState();
    expect(state.projects).toHaveLength(0);
    expect(state.currentProject).toBeNull();
  });
});
