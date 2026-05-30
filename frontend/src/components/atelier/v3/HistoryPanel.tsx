"use client";
//
// HistoryPanel — project-level "process gallery" body for the LeftRail
// History mode. Renders the agent_turns timeline with each turn
// expandable into its tool-call breakdown. Affected-node chips inside
// an expanded turn jump the canvas to that node when clicked.
//
// Pulled out of AtelierShellV3 to keep the shell from growing
// unboundedly. The shell wires `onJumpToNode` (Cmd+P style center +
// select), passes `turns` from project.agent_turns, and otherwise
// stays out of the way.

import { useState } from "react";
import { ChevronDown, ChevronRight, ArrowUpRight } from "lucide-react";
import type { AtelierAgentTurn, AtelierAgentToolCall } from "@/lib/api";

interface Props {
  turns: AtelierAgentTurn[];
  /** Called when the user clicks an affected-node chip. Caller centers
   *  the canvas on the node and selects it. */
  onJumpToNode: (nodeId: string) => void;
}

function statusToneClass(status: AtelierAgentTurn["status"]): string {
  if (status === "completed") return "border-emerald-300/35 text-emerald-200/95";
  if (status === "failed") return "border-red-300/35 text-red-200/95";
  if (status === "waiting_approval") return "border-amber-300/35 text-amber-200/95";
  return "border-blue-300/35 text-blue-200/95";
}

function affectedNodeIds(call: AtelierAgentToolCall): string[] {
  // The Atelier executors return one of:
  //   { node: { id: "..." } }      — most create / update tools
  //   { region: {...}, node: {...} } — canvas.createRegion
  //   { video_node, image_node }    — canvas.attachReferenceNode
  // We collect every id that points at a real persisted canvas node.
  // Defensive: result_snapshot is unknown until inspected.
  const ids: string[] = [];
  const snap = call.result_snapshot;
  if (!snap || typeof snap !== "object") return ids;
  const obj = snap as Record<string, unknown>;
  for (const key of ["node", "region", "video_node", "image_node"]) {
    const candidate = obj[key];
    if (
      candidate &&
      typeof candidate === "object" &&
      "id" in (candidate as Record<string, unknown>) &&
      typeof (candidate as { id?: unknown }).id === "string"
    ) {
      ids.push((candidate as { id: string }).id);
    }
  }
  return Array.from(new Set(ids));
}

function formatTime(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface TurnItemProps {
  turn: AtelierAgentTurn;
  onJumpToNode: (nodeId: string) => void;
}

function TurnItem({ turn, onJumpToNode }: TurnItemProps) {
  const [expanded, setExpanded] = useState(false);
  const messageDisplay = turn.user_message.trim();
  const accessibleLabel = messageDisplay || "System turn";
  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <li className="rounded-md border border-white/8 bg-black/20">
      <button
        type="button"
        aria-label={accessibleLabel}
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-start gap-2 px-3 py-2 text-left transition-colors hover:bg-white/[0.03]"
      >
        <Chevron
          size={11}
          aria-hidden="true"
          className="mt-1 shrink-0 text-text-muted/85"
        />
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span
              className={`inline-flex items-center gap-1 rounded-[3px] border border-dashed px-1.5 py-[1px] font-mono text-[8.5px] font-medium uppercase tracking-[0.22em] ${statusToneClass(turn.status)}`}
            >
              {turn.status.replace("_", " ")}
            </span>
            <span className="font-mono text-[9px] tracking-tight text-text-muted/85">
              {formatTime(turn.created_at)}
            </span>
          </div>
          {messageDisplay ? (
            <p className="line-clamp-3 font-sans text-[12px] italic leading-[1.45] tracking-tight text-foreground/92">
              {messageDisplay}
            </p>
          ) : (
            <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-text-muted/70">
              System turn
            </p>
          )}
          {turn.tool_calls.length > 0 ? (
            <div className="mt-1.5 font-mono text-[9px] uppercase tracking-[0.22em] text-text-muted/85">
              {turn.tool_calls.length} tool call
              {turn.tool_calls.length === 1 ? "" : "s"}
            </div>
          ) : null}
        </div>
      </button>

      {expanded ? (
        <div className="border-t border-white/8 px-3 py-2">
          {turn.tool_calls.length === 0 ? (
            <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-text-muted/70">
              No tool calls recorded.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {turn.tool_calls.map((call, idx) => {
                const ids = affectedNodeIds(call);
                return (
                  <li
                    key={`${call.call_id ?? idx}`}
                    className="rounded-sm bg-black/30 px-2 py-1.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[10px] tracking-tight text-foreground/95">
                        {call.tool_name}
                      </span>
                      <span
                        className={`font-mono text-[9px] uppercase tracking-[0.22em] ${
                          call.status === "completed"
                            ? "text-emerald-200/95"
                            : call.status === "failed"
                              ? "text-red-200/95"
                              : "text-text-muted/85"
                        }`}
                      >
                        {(call.status ?? "pending").replace("_", " ")}
                      </span>
                    </div>
                    {ids.length > 0 ? (
                      <div className="mt-1 flex flex-wrap items-center gap-1">
                        {ids.map((id) => (
                          <button
                            key={id}
                            type="button"
                            aria-label={`Jump to ${id}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              onJumpToNode(id);
                            }}
                            className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-1.5 py-[2px] font-mono text-[9px] uppercase tracking-[0.18em] text-text-secondary transition-colors hover:border-primary/40 hover:bg-primary/10 hover:text-primary/95"
                          >
                            <ArrowUpRight size={9} aria-hidden="true" />
                            {id.slice(-8)}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </li>
  );
}

export function HistoryPanel({ turns, onJumpToNode }: Props) {
  if (turns.length === 0) {
    return (
      <div className="px-4 py-6 text-center">
        <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.28em] text-text-muted/85">
          No agent turns yet
        </div>
        <p className="text-[12px] leading-[1.55] text-text-secondary/95">
          Ask the Agent to do something — the timeline will fill in as it
          plans, asks for approval, and executes.
        </p>
      </div>
    );
  }

  // Most recent first reads as "what just happened?", which is what
  // the user opens this panel to find. Backend stores chronologically.
  const ordered = [...turns].sort((a, b) => b.created_at - a.created_at);

  return (
    <ul className="space-y-1.5 p-2.5">
      {ordered.map((turn) => (
        <TurnItem key={turn.id} turn={turn} onJumpToNode={onJumpToNode} />
      ))}
    </ul>
  );
}
