// @vitest-environment jsdom
//
// Per-tool rejection on the approval card (PRD §14.4). The test mocks the
// Atelier store with a pendingTurn that has 2 proposed tool calls and asserts:
//   - each call gets a Skip toggle
//   - Approve runs only the un-skipped calls
//   - skipping all routes to deny instead of running an empty turn
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { AtelierAgentTurn, AtelierProject } from "@/lib/api";

function makePendingTurn(): AtelierAgentTurn {
  return {
    id: "turn-pending",
    project_id: "p1",
    user_message: "make me three drafts",
    preview: false,
    status: "waiting_approval",
    created_at: 1_700_000_000,
    completed_at: null,
    tool_calls: [
      {
        call_id: "call-1",
        tool_name: "canvas.createVideoNode",
        arguments: { intent: "Cinematic" },
        status: "approval_required",
        approval_required: true,
        approval_granted: false,
        error: null,
        result_snapshot: null,
        created_at: 1_700_000_000,
        completed_at: null,
      },
      {
        call_id: "call-2",
        tool_name: "canvas.createVideoNode",
        arguments: { intent: "Documentary" },
        status: "approval_required",
        approval_required: true,
        approval_granted: false,
        error: null,
        result_snapshot: null,
        created_at: 1_700_000_000,
        completed_at: null,
      },
    ],
  };
}

const project: AtelierProject = {
  id: "p1",
  title: "Test",
  description: "",
  nodes: [],
  agent_policy: {
    approval_mode: "untrusted",
    allowed_tools: [],
    max_nodes_per_action: 8,
    updated_at: 0,
  },
  agent_turns: [],
  created_at: 0,
  updated_at: 0,
};

vi.mock("@/store/atelierStore", () => {
  const state = {
    currentProject: project,
    selectedNodeId: null as string | null,
    agentTurns: [] as AtelierAgentTurn[],
    pendingAgentTurn: null as AtelierAgentTurn | null,
    isAgentRunning: false,
    planAgentTurn: vi.fn(),
    runAgentTurn: vi.fn().mockResolvedValue(undefined),
    // v1.4 Batch 3 — skill registry slice (warmed on AgentPanelV3 mount).
    skillRegistry_B3: [] as unknown[],
    skillRegistryLoaded_B3: true,
    loadSkills_B3: vi.fn().mockResolvedValue([]),
    dispatchSkillTurn_B3: vi.fn().mockResolvedValue(null),
    // v1.4 Batch 4 — model picker slice (warmed on AgentPanelV3 mount).
    agentModel_B4: { provider: "dashscope", model_id: "qwen-plus" },
    agentModelOptions_B4: [] as unknown[],
    agentModelOptionsLoaded_B4: true,
    setAgentModel_B4: vi.fn(),
    loadAgentModels_B4: vi.fn().mockResolvedValue([]),
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

async function loadModule() {
  const mod = await import("@/components/atelier/v3/AgentPanelV3");
  const store = await import("@/store/atelierStore");
  const state = (store.useAtelierStore as unknown as { getState: () => {
    currentProject: AtelierProject;
    pendingAgentTurn: AtelierAgentTurn | null;
    runAgentTurn: ReturnType<typeof vi.fn>;
  } }).getState();
  return { AgentPanelV3: mod.AgentPanelV3, state };
}

describe("AgentPanelV3 — per-tool rejection", () => {
  beforeEach(async () => {
    const { state } = await loadModule();
    state.pendingAgentTurn = makePendingTurn();
    state.runAgentTurn.mockClear();
  });

  it("shows a Skip toggle next to each pending tool call", async () => {
    const { AgentPanelV3 } = await loadModule();
    render(<AgentPanelV3 />);
    expect(screen.getAllByLabelText(/Skip this action/i)).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: /Skip this action|Restore this action/i })).toHaveLength(2);
  });

  it("Approve sends only un-skipped tool calls", async () => {
    const { AgentPanelV3, state } = await loadModule();
    render(<AgentPanelV3 />);
    // Skip the first call
    const skipButtons = screen.getAllByRole("button", { name: /Skip this action/i });
    fireEvent.click(skipButtons[0]);
    // Button label flips to "Approve 1"
    expect(screen.getByRole("button", { name: /Approve 1/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Approve 1/i }));
    await waitFor(() => expect(state.runAgentTurn).toHaveBeenCalled());
    const arg = state.runAgentTurn.mock.calls[0][0] as {
      approve?: boolean;
      tool_calls: Array<{ tool_name: string; arguments: Record<string, unknown> }>;
    };
    expect(arg.approve).toBe(true);
    expect(arg.tool_calls).toHaveLength(1);
    expect(arg.tool_calls[0].arguments).toEqual({ intent: "Documentary" });
  });

  it("skipping all calls routes to deny instead of running empty", async () => {
    const { AgentPanelV3, state } = await loadModule();
    render(<AgentPanelV3 />);
    const skipButtons = screen.getAllByRole("button", { name: /Skip this action/i });
    fireEvent.click(skipButtons[0]);
    fireEvent.click(skipButtons[1]);
    expect(screen.getByRole("button", { name: /Skip all/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Skip all/i }));
    await waitFor(() => expect(state.runAgentTurn).toHaveBeenCalled());
    const arg = state.runAgentTurn.mock.calls[0][0] as {
      approve?: boolean;
      deny?: boolean;
      tool_calls: unknown[];
    };
    expect(arg.deny).toBe(true);
    expect(arg.approve).toBeUndefined();
    expect(arg.tool_calls).toEqual([]);
  });

  it("Restore reverses a skip", async () => {
    const { AgentPanelV3 } = await loadModule();
    render(<AgentPanelV3 />);
    const skipButtons = screen.getAllByRole("button", { name: /Skip this action/i });
    fireEvent.click(skipButtons[0]);
    expect(screen.getByRole("button", { name: /Approve 1/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Restore this action/i }));
    expect(screen.getByRole("button", { name: /Approve & run/i })).toBeInTheDocument();
  });
});
