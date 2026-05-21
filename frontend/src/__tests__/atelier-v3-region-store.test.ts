// Region-related store actions: createRegion, attachToRegion,
// detachFromRegion, moveRegion. Each action is exercised against a
// mocked api client; we assert (1) the right HTTP-shape goes to the
// server and (2) local state stays consistent.
//
// Conventions match atelier-store.test.ts — same module-mock,
// same project fixture style, same import-after-clear pattern.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AtelierNode, AtelierProject } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    getAtelierProject: vi.fn(),
    listAtelierProjects: vi.fn(),
    createAtelierProject: vi.fn(),
    createAtelierNode: vi.fn(),
    updateAtelierNode: vi.fn(),
    deleteAtelierNode: vi.fn(),
  },
}));

const baseNode = (
  id: string,
  type: string,
  x: number,
  y: number,
  w = 200,
  h = 120,
  data: Record<string, unknown> = {},
): AtelierNode => ({
  id,
  project_id: "atelier-1",
  type,
  title: id,
  prompt: "",
  status: "draft",
  x,
  y,
  width: w,
  height: h,
  media_urls: [],
  data,
  created_by: "user",
  created_at: 1,
  updated_at: 1,
});

const project: AtelierProject = {
  id: "atelier-1",
  title: "Board",
  description: "",
  nodes: [
    baseNode("video-1", "video", 100, 100, 240, 110),
    baseNode("image-1", "image", 360, 100, 200, 120),
  ],
  agent_policy: {
    approval_mode: "untrusted",
    allowed_tools: [],
    max_nodes_per_action: 8,
    updated_at: 1,
  },
  created_at: 1,
  updated_at: 1,
  agent_turns: [],
};

function cloneProject(): AtelierProject {
  return JSON.parse(JSON.stringify(project)) as AtelierProject;
}

describe("atelier store — region actions", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const { useAtelierStore } = await import("@/store/atelierStore");
    const fresh = cloneProject();
    useAtelierStore.setState({
      projects: [fresh],
      currentProject: fresh,
      selectedNodeId: null,
      agentTools: [],
      agentTurns: [],
      pendingAgentTurn: null,
      isLoading: false,
      isAgentRunning: false,
      error: null,
    });
  });

  it("createRegion posts a region node with default size and inserts it locally", async () => {
    const { api } = await import("@/lib/api");
    const { useAtelierStore } = await import("@/store/atelierStore");
    const created = baseNode("region-1", "region", 200, 200, 600, 400, {
      color: "default",
    });
    vi.mocked(api.createAtelierNode).mockResolvedValueOnce(created);

    const node = await useAtelierStore
      .getState()
      .createRegion({ title: "Character study" });

    expect(api.createAtelierNode).toHaveBeenCalledWith(
      "atelier-1",
      expect.objectContaining({ type: "region", title: "Character study" }),
    );
    const payload = vi.mocked(api.createAtelierNode).mock.calls[0][1];
    expect(payload.width).toBeGreaterThan(0);
    expect(payload.height).toBeGreaterThan(0);
    expect(node.id).toBe("region-1");
    const state = useAtelierStore.getState();
    expect(state.currentProject?.nodes.find((n) => n.id === "region-1")).toBeTruthy();
  });

  it("createRegion with wrap derives bounds from given nodes' union bbox + padding", async () => {
    const { api } = await import("@/lib/api");
    const { useAtelierStore } = await import("@/store/atelierStore");
    vi.mocked(api.createAtelierNode).mockImplementationOnce(async (_pid, payload) => {
      return baseNode("region-1", "region", payload.x ?? 0, payload.y ?? 0, payload.width ?? 0, payload.height ?? 0);
    });
    vi.mocked(api.updateAtelierNode).mockImplementation(async (_pid, nodeId, patch) => {
      const fresh = useAtelierStore.getState().currentProject!.nodes.find((n) => n.id === nodeId)!;
      return { ...fresh, ...patch, data: { ...fresh.data, ...(patch.data ?? {}) } };
    });

    await useAtelierStore.getState().createRegion({
      title: "Group",
      wrap: ["video-1", "image-1"],
    });

    const payload = vi.mocked(api.createAtelierNode).mock.calls[0][1];
    // video-1: x=100..340 y=100..210; image-1: x=360..560 y=100..220
    // union: x=100..560 y=100..220 → w=460, h=120; with padding=32 each side
    expect(payload.x).toBe(100 - 32);
    expect(payload.y).toBe(100 - 32);
    expect(payload.width).toBe(460 + 64);
    expect(payload.height).toBe(120 + 64);
  });

  it("createRegion with wrap calls attachToRegion on each wrapped node", async () => {
    const { api } = await import("@/lib/api");
    const { useAtelierStore } = await import("@/store/atelierStore");
    vi.mocked(api.createAtelierNode).mockResolvedValueOnce(
      baseNode("region-1", "region", 0, 0, 600, 400),
    );
    vi.mocked(api.updateAtelierNode).mockImplementation(async (_pid, nodeId, patch) => {
      const fresh = useAtelierStore.getState().currentProject!.nodes.find((n) => n.id === nodeId)!;
      return { ...fresh, ...patch, data: { ...fresh.data, ...(patch.data ?? {}) } };
    });

    await useAtelierStore.getState().createRegion({
      title: "Group",
      wrap: ["video-1", "image-1"],
    });

    // Each wrapped node should have been patched with data.region_id.
    const calls = vi.mocked(api.updateAtelierNode).mock.calls;
    const ids = calls.map((c) => c[1]).sort();
    expect(ids).toEqual(["image-1", "video-1"]);
    for (const c of calls) {
      const patch = c[2];
      expect((patch.data as { region_id?: string }).region_id).toBe("region-1");
    }
  });

  it("attachToRegion patches data.region_id and updates local state", async () => {
    const { api } = await import("@/lib/api");
    const { useAtelierStore } = await import("@/store/atelierStore");
    const updated = { ...cloneProject().nodes[0], data: { region_id: "region-1" } };
    vi.mocked(api.updateAtelierNode).mockResolvedValueOnce(updated);

    await useAtelierStore.getState().attachToRegion("video-1", "region-1");

    expect(api.updateAtelierNode).toHaveBeenCalledWith(
      "atelier-1",
      "video-1",
      expect.objectContaining({ data: expect.objectContaining({ region_id: "region-1" }) }),
    );
    const node = useAtelierStore
      .getState()
      .currentProject!.nodes.find((n) => n.id === "video-1");
    expect((node?.data as { region_id?: string }).region_id).toBe("region-1");
  });

  it("attachToRegion preserves existing data fields (does not clobber)", async () => {
    const { api } = await import("@/lib/api");
    const { useAtelierStore } = await import("@/store/atelierStore");
    // Seed video-1 with existing reference_image_urls.
    useAtelierStore.setState((state) => ({
      ...state,
      currentProject: state.currentProject
        ? {
            ...state.currentProject,
            nodes: state.currentProject.nodes.map((n) =>
              n.id === "video-1"
                ? { ...n, data: { ...n.data, reference_image_urls: ["a.png"] } }
                : n,
            ),
          }
        : null,
    }));
    vi.mocked(api.updateAtelierNode).mockImplementation(async (_pid, nodeId, patch) => {
      const fresh = useAtelierStore.getState().currentProject!.nodes.find((n) => n.id === nodeId)!;
      return { ...fresh, ...patch, data: { ...fresh.data, ...(patch.data ?? {}) } };
    });

    await useAtelierStore.getState().attachToRegion("video-1", "region-1");

    const patch = vi.mocked(api.updateAtelierNode).mock.calls[0][2];
    expect(patch.data).toEqual(
      expect.objectContaining({
        region_id: "region-1",
        reference_image_urls: ["a.png"],
      }),
    );
  });

  it("detachFromRegion clears data.region_id only, preserves rest", async () => {
    const { api } = await import("@/lib/api");
    const { useAtelierStore } = await import("@/store/atelierStore");
    // Seed: video-1 attached to region-1 with other data.
    useAtelierStore.setState((state) => ({
      ...state,
      currentProject: state.currentProject
        ? {
            ...state.currentProject,
            nodes: state.currentProject.nodes.map((n) =>
              n.id === "video-1"
                ? { ...n, data: { region_id: "region-1", intent: "shot" } }
                : n,
            ),
          }
        : null,
    }));
    // Real backend (pipeline.update_atelier_node) whole-replaces the
    // `data` field via setattr, so the mock mirrors that — whatever
    // the patch.data dict is, that's the new data.
    vi.mocked(api.updateAtelierNode).mockImplementation(async (_pid, nodeId, patch) => {
      const fresh = useAtelierStore.getState().currentProject!.nodes.find((n) => n.id === nodeId)!;
      const nextData: Record<string, unknown> = patch.data
        ? { ...(patch.data as Record<string, unknown>) }
        : { ...(fresh.data as Record<string, unknown>) };
      return { ...fresh, ...patch, data: nextData };
    });

    await useAtelierStore.getState().detachFromRegion("video-1");

    const patch = vi.mocked(api.updateAtelierNode).mock.calls[0][2];
    expect((patch.data as Record<string, unknown>).region_id).toBeUndefined();
    const node = useAtelierStore
      .getState()
      .currentProject!.nodes.find((n) => n.id === "video-1");
    expect((node?.data as { region_id?: string }).region_id).toBeUndefined();
    expect((node?.data as { intent?: string }).intent).toBe("shot");
  });

  it("moveRegion moves the region node AND all its attached children by dx/dy", async () => {
    const { api } = await import("@/lib/api");
    const { useAtelierStore } = await import("@/store/atelierStore");

    // Seed: region-1 at (100,100), video-1 attached to it at (150,150),
    // image-1 NOT attached at (360,100).
    useAtelierStore.setState((state) => ({
      ...state,
      currentProject: state.currentProject
        ? {
            ...state.currentProject,
            nodes: [
              ...state.currentProject.nodes,
              baseNode("region-1", "region", 100, 100, 600, 400),
            ].map((n) =>
              n.id === "video-1"
                ? { ...n, x: 150, y: 150, data: { region_id: "region-1" } }
                : n,
            ),
          }
        : null,
    }));
    vi.mocked(api.updateAtelierNode).mockImplementation(async (_pid, nodeId, patch) => {
      const fresh = useAtelierStore.getState().currentProject!.nodes.find((n) => n.id === nodeId)!;
      return { ...fresh, ...patch, data: { ...fresh.data, ...(patch.data ?? {}) } };
    });

    await useAtelierStore.getState().moveRegion("region-1", 40, -20);

    // Two updates: region itself + video-1 (the only child); image-1 stays.
    const calls = vi.mocked(api.updateAtelierNode).mock.calls;
    const updatedIds = calls.map((c) => c[1]).sort();
    expect(updatedIds).toEqual(["region-1", "video-1"]);
    const region = useAtelierStore
      .getState()
      .currentProject!.nodes.find((n) => n.id === "region-1");
    const child = useAtelierStore
      .getState()
      .currentProject!.nodes.find((n) => n.id === "video-1");
    const sibling = useAtelierStore
      .getState()
      .currentProject!.nodes.find((n) => n.id === "image-1");
    expect(region?.x).toBe(140);
    expect(region?.y).toBe(80);
    expect(child?.x).toBe(190);
    expect(child?.y).toBe(130);
    expect(sibling?.x).toBe(360);
    expect(sibling?.y).toBe(100);
  });

  it("deleting a region detaches its children before delete (cascade detach, children survive)", async () => {
    const { api } = await import("@/lib/api");
    const { useAtelierStore } = await import("@/store/atelierStore");

    useAtelierStore.setState((state) => ({
      ...state,
      currentProject: state.currentProject
        ? {
            ...state.currentProject,
            nodes: [
              ...state.currentProject.nodes.map((n) =>
                n.id === "video-1" || n.id === "image-1"
                  ? { ...n, data: { ...n.data, region_id: "region-1" } }
                  : n,
              ),
              baseNode("region-1", "region", 100, 100, 600, 400),
            ],
          }
        : null,
    }));
    vi.mocked(api.updateAtelierNode).mockImplementation(async (_pid, nodeId, patch) => {
      const fresh = useAtelierStore.getState().currentProject!.nodes.find((n) => n.id === nodeId)!;
      // Whole-replace data semantics matching backend.
      const nextData: Record<string, unknown> = patch.data
        ? { ...(patch.data as Record<string, unknown>) }
        : { ...(fresh.data as Record<string, unknown>) };
      return { ...fresh, ...patch, data: nextData };
    });
    vi.mocked(api.deleteAtelierNode).mockResolvedValueOnce(undefined);

    await useAtelierStore.getState().deleteAtelierNode("region-1");

    // Both children patched first to clear region_id.
    const updateCalls = vi.mocked(api.updateAtelierNode).mock.calls.map((c) => c[1]).sort();
    expect(updateCalls).toEqual(["image-1", "video-1"]);
    expect(api.deleteAtelierNode).toHaveBeenCalledWith("atelier-1", "region-1");
    const state = useAtelierStore.getState();
    expect(state.currentProject?.nodes.find((n) => n.id === "region-1")).toBeUndefined();
    expect(state.currentProject?.nodes.find((n) => n.id === "video-1")).toBeTruthy();
    expect(state.currentProject?.nodes.find((n) => n.id === "image-1")).toBeTruthy();
  });
});
