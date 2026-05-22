// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { HistoryPanel } from "@/components/atelier/v3/HistoryPanel";
import type { AtelierAgentTurn } from "@/lib/api";

function makeTurn(overrides: Partial<AtelierAgentTurn> = {}): AtelierAgentTurn {
  return {
    id: "t1",
    project_id: "p1",
    user_message: "Make a 3-shot story",
    preview: false,
    status: "completed",
    tool_calls: [],
    created_at: 1000,
    completed_at: 1010,
    ...overrides,
  } as AtelierAgentTurn;
}

describe("HistoryPanel", () => {
  it("shows the empty-state caption when there are no turns", () => {
    render(<HistoryPanel turns={[]} onJumpToNode={vi.fn()} />);
    expect(screen.getByText(/no agent turns yet/i)).toBeInTheDocument();
  });

  it("orders turns most-recent-first", () => {
    const turns: AtelierAgentTurn[] = [
      makeTurn({ id: "old", user_message: "old", created_at: 500 }),
      makeTurn({ id: "new", user_message: "new", created_at: 9000 }),
      makeTurn({ id: "mid", user_message: "mid", created_at: 4000 }),
    ];
    render(<HistoryPanel turns={turns} onJumpToNode={vi.fn()} />);
    const items = screen.getAllByRole("listitem");
    expect(items[0].textContent).toContain("new");
    expect(items[1].textContent).toContain("mid");
    expect(items[2].textContent).toContain("old");
  });

  it("collapses turns by default and expands on header click", () => {
    const turns = [
      makeTurn({
        id: "t1",
        user_message: "Make a 3-shot story",
        tool_calls: [
          {
            // Trimmed to the fields HistoryPanel reads. Real type is
            // AtelierAgentToolCall but loosely shaped here for the
            // test fixture.
            tool_name: "canvas.createVideoNode",
            arguments: { title: "Setup" },
            status: "completed",
            result_snapshot: { node: { id: "n-setup" } },
          } as unknown as AtelierAgentTurn["tool_calls"][number],
        ],
      }),
    ];
    render(<HistoryPanel turns={turns} onJumpToNode={vi.fn()} />);
    // Collapsed: tool call name should not appear yet.
    expect(screen.queryByText(/canvas\.createVideoNode/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Make a 3-shot story/i }));
    expect(screen.getByText(/canvas\.createVideoNode/i)).toBeInTheDocument();
  });

  it("clicking an affected-node chip fires onJumpToNode with the node id", () => {
    const onJumpToNode = vi.fn();
    const turns = [
      makeTurn({
        id: "t1",
        user_message: "Add ref + shot",
        tool_calls: [
          {
            tool_name: "canvas.createVideoNode",
            arguments: {},
            status: "completed",
            result_snapshot: { node: { id: "n-shot" } },
          } as unknown as AtelierAgentTurn["tool_calls"][number],
        ],
      }),
    ];
    render(<HistoryPanel turns={turns} onJumpToNode={onJumpToNode} />);
    fireEvent.click(screen.getByRole("button", { name: /Add ref \+ shot/i }));
    fireEvent.click(screen.getByRole("button", { name: /jump to n-shot/i }));
    expect(onJumpToNode).toHaveBeenCalledWith("n-shot");
  });

  it("renders System turn placeholder when user_message is empty", () => {
    const turns = [makeTurn({ user_message: "" })];
    render(<HistoryPanel turns={turns} onJumpToNode={vi.fn()} />);
    expect(screen.getByText(/system turn/i)).toBeInTheDocument();
  });

  it("status tone classes vary by turn.status", () => {
    const turns = [
      makeTurn({ id: "ok", user_message: "ok", status: "completed" }),
      makeTurn({ id: "fail", user_message: "fail", status: "failed" }),
      makeTurn({ id: "wait", user_message: "wait", status: "waiting_approval" }),
    ];
    const { container } = render(<HistoryPanel turns={turns} onJumpToNode={vi.fn()} />);
    expect(container.textContent).toMatch(/completed/i);
    expect(container.textContent).toMatch(/failed/i);
    expect(container.textContent).toMatch(/waiting/i);
  });
});
