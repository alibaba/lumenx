// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Mock the store with a minimal implementation.
vi.mock("@/store/atelierStore", () => {
  const project = {
    id: "p1",
    title: "Test",
    description: "",
    nodes: [
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
        data: {},
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
        status: "draft",
        x: 320,
        y: 180,
        width: 240,
        height: 110,
        media_urls: [],
        data: {
          intent: "Cinematic",
          model: "Wan 2.7",
          config_summary: "1280×720 · 5s · 4×",
        },
        created_by: "user",
        created_at: 0,
        updated_at: 0,
      },
    ],
    agent_policy: {
      approval_mode: "on_request",
      allowed_tools: [],
      max_nodes_per_action: 8,
      updated_at: 0,
    },
    agent_turns: [],
    created_at: 0,
    updated_at: 0,
  };
  const state = {
    currentProject: project,
    selectedNodeId: null as string | null,
    ensureProject: vi.fn().mockResolvedValue(project),
    selectNode: vi.fn(),
    createVideoNode: vi.fn(),
    updateAgentPolicy: vi.fn(),
    createVideoCandidates: vi.fn(),
    selectCandidate: vi.fn(),
  };
  return {
    useAtelierStore: Object.assign(
      vi.fn((selector?: (s: typeof state) => unknown) =>
        selector ? selector(state) : state,
      ),
      { getState: () => state },
    ),
  };
});

describe("AtelierShellV3", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the toolbar, right rail, and bottom nav rail", async () => {
    const { AtelierShellV3 } = await import(
      "@/components/atelier/v3/AtelierShellV3"
    );
    render(<AtelierShellV3 />);
    expect(
      screen.getByRole("toolbar", { name: /atelier toolbar/i }),
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
});
