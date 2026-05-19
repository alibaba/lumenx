// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type {
  AtelierNode,
  AtelierProject,
  AtelierVideoCandidate,
} from "@/lib/api";

// ── Project fixture factory ──────────────────────────────────────────────────
function makeProject(opts?: {
  videoStatus?: "draft" | "completed";
  candidates?: AtelierVideoCandidate[];
  imageReferencesVideo?: boolean;
}): AtelierProject {
  const status = opts?.videoStatus ?? "draft";
  const cands = opts?.candidates;
  const imageData: Record<string, unknown> = {};
  if (opts?.imageReferencesVideo) {
    imageData.parent_node_id = "n2";
  }
  const videoData: Record<string, unknown> =
    status === "draft"
      ? {
          intent: "Cinematic",
          model: "Wan 2.7",
          config_summary: "1280×720 · 5s · 4×",
        }
      : {};
  if (cands) videoData.candidates = cands;

  const nodes: AtelierNode[] = [
    {
      id: "n1",
      project_id: "p1",
      type: "image",
      title: "ref",
      prompt: "",
      status: "completed",
      x: 60,
      y: 80,
      width: 180,
      height: 180,
      media_urls: ["https://example.com/a.png"],
      data: imageData,
      created_by: "user",
      created_at: 0,
      updated_at: 0,
    },
    {
      id: "n2",
      project_id: "p1",
      type: "video",
      title: "Cinematic",
      prompt: "",
      status,
      x: 320,
      y: 180,
      width: 240,
      height: 110,
      media_urls:
        status === "completed" ? ["https://example.com/v.mp4"] : [],
      data: videoData,
      created_by: "user",
      created_at: 0,
      updated_at: 0,
    },
  ];

  return {
    id: "p1",
    title: "Test",
    description: "",
    nodes,
    agent_policy: {
      approval_mode: "on_request",
      allowed_tools: [],
      max_nodes_per_action: 8,
      updated_at: 0,
    },
    agent_turns: [],
    created_at: 0,
    updated_at: 0,
  } as AtelierProject;
}

// ── Mock store ───────────────────────────────────────────────────────────────
// The factory exposes a single mutable `state` object. Tests reach it via
// `useAtelierStore.getState()` and mutate before render.
vi.mock("@/store/atelierStore", () => {
  const state: {
    currentProject: AtelierProject | null;
    selectedNodeId: string | null;
    ensureProject: ReturnType<typeof vi.fn>;
    selectNode: ReturnType<typeof vi.fn>;
    createVideoNode: ReturnType<typeof vi.fn>;
    createImageNode: ReturnType<typeof vi.fn>;
    createIdeaNode: ReturnType<typeof vi.fn>;
    deleteAtelierNode: ReturnType<typeof vi.fn>;
    branchFromCandidate: ReturnType<typeof vi.fn>;
    updateNode: ReturnType<typeof vi.fn>;
    refreshCurrentProject: ReturnType<typeof vi.fn>;
    updateAgentPolicy: ReturnType<typeof vi.fn>;
    createVideoCandidates: ReturnType<typeof vi.fn>;
    selectCandidate: ReturnType<typeof vi.fn>;
    regenerateVideoCandidates: ReturnType<typeof vi.fn>;
    retryCandidate: ReturnType<typeof vi.fn>;
    deleteCandidate: ReturnType<typeof vi.fn>;
  } = {
    currentProject: null,
    selectedNodeId: null,
    ensureProject: vi.fn(),
    selectNode: vi.fn(),
    createVideoNode: vi.fn(),
    createImageNode: vi.fn(),
    createIdeaNode: vi.fn(),
    deleteAtelierNode: vi.fn(),
    branchFromCandidate: vi.fn(),
    updateNode: vi.fn(),
    refreshCurrentProject: vi.fn(),
    updateAgentPolicy: vi.fn(),
    createVideoCandidates: vi.fn(),
    selectCandidate: vi.fn(),
    regenerateVideoCandidates: vi.fn(),
    retryCandidate: vi.fn(),
    deleteCandidate: vi.fn(),
  };
  state.ensureProject.mockImplementation(async () => state.currentProject);
  // All store actions return resolved Promises by default so chain `.then()` /
  // `.catch()` calls inside AtelierShellV3 don't throw on undefined.
  for (const key of [
    "createVideoNode", "createImageNode", "createIdeaNode", "deleteAtelierNode",
    "branchFromCandidate", "updateNode", "refreshCurrentProject", "updateAgentPolicy",
    "createVideoCandidates", "selectCandidate", "regenerateVideoCandidates",
    "retryCandidate", "deleteCandidate",
  ] as const) {
    (state[key] as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
  }
  return {
    useAtelierStore: Object.assign(
      vi.fn((selector?: (s: typeof state) => unknown) =>
        selector ? selector(state) : state,
      ),
      { getState: () => state },
    ),
  };
});

async function getStoreState() {
  const mod = await import("@/store/atelierStore");
  // The mocked useAtelierStore exposes getState() over our fake state object.
  return (mod.useAtelierStore as unknown as { getState: () => {
    currentProject: AtelierProject | null;
    selectedNodeId: string | null;
    ensureProject: ReturnType<typeof vi.fn>;
    selectNode: ReturnType<typeof vi.fn>;
    createVideoNode: ReturnType<typeof vi.fn>;
    createImageNode: ReturnType<typeof vi.fn>;
    createIdeaNode: ReturnType<typeof vi.fn>;
    deleteAtelierNode: ReturnType<typeof vi.fn>;
    branchFromCandidate: ReturnType<typeof vi.fn>;
    updateNode: ReturnType<typeof vi.fn>;
    refreshCurrentProject: ReturnType<typeof vi.fn>;
    updateAgentPolicy: ReturnType<typeof vi.fn>;
    createVideoCandidates: ReturnType<typeof vi.fn>;
    selectCandidate: ReturnType<typeof vi.fn>;
    regenerateVideoCandidates: ReturnType<typeof vi.fn>;
    retryCandidate: ReturnType<typeof vi.fn>;
    deleteCandidate: ReturnType<typeof vi.fn>;
  } }).getState();
}

describe("AtelierShellV3", () => {
  beforeEach(async () => {
    const store = await getStoreState();
    store.currentProject = makeProject();
    store.selectedNodeId = null;
    store.ensureProject.mockClear();
    store.selectNode.mockClear();
    store.createVideoNode.mockClear();
    store.updateAgentPolicy.mockClear();
    store.createVideoCandidates.mockClear();
    store.selectCandidate.mockClear();
    store.regenerateVideoCandidates.mockClear();
    store.retryCandidate.mockClear();
    store.deleteCandidate.mockClear();
  });

  it("renders the left rail, right rail, and bottom nav rail", async () => {
    // Sprint B: top horizontal Toolbar replaced with vertical
    // LeftRailV3 (Activity-Bar pattern). Look for the new mode rail
    // by aria-label.
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    expect(
      screen.getByRole("toolbar", { name: /atelier mode rail/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: /^Atelier Agent$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("toolbar", { name: /canvas navigation/i }),
    ).toBeInTheDocument();
  });

  it("does NOT render Generate / Regenerate inside any node card on canvas", async () => {
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    expect(
      screen.queryByRole("button", { name: /generate candidates/i }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /regenerate all/i }),
    ).toBeNull();
  });

  it("renders the seeded image and draft nodes from the mocked project", async () => {
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    expect(screen.getByAltText("ref")).toBeInTheDocument();
    expect(screen.getByText("Cinematic")).toBeInTheDocument();
  });

  it("renders SelectionActionBar above a selected draft", async () => {
    const store = await getStoreState();
    store.selectedNodeId = "n2";
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    // draft layout (v0.3.1+): only Delete — Composer is the editor for drafts.
    expect(screen.getByLabelText("Delete")).toBeInTheDocument();
    expect(screen.queryByLabelText("Branch")).toBeNull();
    expect(screen.queryByLabelText("Re-generate")).toBeNull();
  });

  it("renders inline DraftWorkbench when a draft is selected (no floating Composer)", async () => {
    // Sprint A: the floating Composer was retired. A selected draft now
    // expands in place into a DraftWorkbench (RHTV / LibTV pattern). The
    // workbench is identified by its aria-label "Draft workbench: ...".
    const store = await getStoreState();
    store.selectedNodeId = "n2";
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    expect(
      screen.getByRole("button", { name: /draft workbench/i }),
    ).toBeInTheDocument();
    // Floating popup dialog should NOT exist anymore.
    expect(
      screen.queryByRole("dialog", { name: /generation composer/i }),
    ).toBeNull();
  });

  it("does NOT render Composer when a completed video is selected (only drafts get Composer)", async () => {
    const store = await getStoreState();
    store.currentProject = makeProject({ videoStatus: "completed" });
    store.selectedNodeId = "n2";
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    expect(
      screen.queryByRole("dialog", { name: /generation composer/i }),
    ).toBeNull();
  });

  it("Add panel create-video posts a draft node via api.createAtelierNode", async () => {
    // We bypass store.createVideoNode and call api directly to land a draft
    // in one network round-trip (status='draft' + data.intent set so the
    // DraftNode renderer wins immediately, no MediaNode flicker).
    const apiMod = await import("@/lib/api");
    const spy = vi.spyOn(apiMod.api, "createAtelierNode").mockResolvedValue({
      id: "n-new",
      project_id: "p1",
      type: "video",
      title: "Video Node 3",
      prompt: "",
      status: "draft",
      x: 100,
      y: 100,
      width: 240,
      height: 110,
      media_urls: [],
      data: { intent: "Cinematic interpretation" },
      created_by: "user",
      created_at: 0,
      updated_at: 0,
    });
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    // Sprint B: the old top "New Video Node" pill is gone. Now: click
    // the left rail's Add mode → its panel slides open → click the
    // "Video draft" entry in the panel.
    fireEvent.click(screen.getByRole("tab", { name: "Add" }));
    fireEvent.click(screen.getByRole("button", { name: /Video draft/i }));
    // Allow the async promise chain in handleCreateVideo to schedule.
    await new Promise((r) => setTimeout(r, 0));
    expect(spy).toHaveBeenCalledTimes(1);
    const [, payload] = spy.mock.calls[0];
    expect(payload).toMatchObject({
      type: "video",
      status: "draft",
    });
    expect((payload.data as { intent: string }).intent).toMatch(/.+/);
    spy.mockRestore();
  });

  it("Permission mode change calls store.updateAgentPolicy", async () => {
    const store = await getStoreState();
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    fireEvent.click(screen.getByRole("radio", { name: /^Never$/ }));
    expect(store.updateAgentPolicy).toHaveBeenCalledWith({
      approval_mode: "never",
    });
  });

  it("clicking a virtual candidate brings up SelectionActionBar with Select as take", async () => {
    const candidate: AtelierVideoCandidate = {
      id: "c1",
      status: "completed",
      video_url: "https://example.com/c1.mp4",
      prompt: "",
      model: "Wan 2.7",
      reference_image_urls: [],
      params: {},
      created_at: 0,
      label: "Take 1",
    };
    const store = await getStoreState();
    store.currentProject = makeProject({ candidates: [candidate] });
    store.selectedNodeId = "n2::cand::c1";
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    expect(screen.getByLabelText("Select as take")).toBeInTheDocument();
  });
});
