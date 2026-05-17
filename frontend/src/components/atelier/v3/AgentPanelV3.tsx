"use client";
import { useMemo, useState } from "react";
import { Bot, Loader2, Paperclip, Send, ShieldCheck, Sparkles } from "lucide-react";
import { useAtelierStore } from "@/store/atelierStore";
import type {
  AtelierAgentTurn,
  AtelierAgentToolCallPayload,
} from "@/lib/api";

interface Toast { kind: "info" | "success" | "error"; text: string }

interface Props {
  pushToast?: (kind: Toast["kind"], text: string) => void;
}

function ConversationUserBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[88%] rounded-[14px] rounded-tr-[6px] border border-primary/20 bg-primary/[0.1] px-3 py-2 text-[13px] leading-[1.55] text-foreground/95 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]">
        {children}
      </div>
    </div>
  );
}

function ConversationAgentBubble({ children, thinking = false }: { children?: React.ReactNode; thinking?: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-primary/15 text-primary ring-1 ring-inset ring-primary/25">
        <Bot size={13} aria-hidden="true" />
      </span>
      <div className="flex-1 rounded-[14px] rounded-tl-[6px] border border-white/6 bg-black/25 px-3 py-2 text-[13px] leading-[1.55] text-foreground/95 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)]">
        {thinking ? (
          <span className="inline-flex items-center gap-1.5 text-text-secondary/95">
            <span className="inline-block h-[5px] w-[5px] animate-pulse rounded-full bg-blue-300 shadow-[0_0_0_3px_rgba(96,165,250,0.18)]" />
            <span className="inline-block h-[5px] w-[5px] animate-pulse rounded-full bg-blue-300/85" style={{ animationDelay: "0.15s" }} />
            <span className="inline-block h-[5px] w-[5px] animate-pulse rounded-full bg-blue-300/65" style={{ animationDelay: "0.3s" }} />
            <span className="ml-1 font-mono text-[10px] uppercase tracking-[0.2em] text-blue-200/85">Planning</span>
          </span>
        ) : children}
      </div>
    </div>
  );
}

function summarizeToolCall(call: AtelierAgentToolCallPayload | { tool_name: string; arguments: Record<string, unknown> }): string {
  const name = call.tool_name;
  const args = call.arguments ?? {};
  switch (name) {
    case "canvas.createVideoNode":
      return `Create video node "${(args as { intent?: string }).intent || (args as { title?: string }).title || "draft"}"`;
    case "canvas.updateNodePrompt":
      return `Update prompt on node ${(args as { node_id?: string }).node_id ?? "?"}`;
    case "canvas.createReferenceImageNode":
      return `Create reference image node`;
    case "canvas.attachReferenceNode":
      return `Attach reference to video node`;
    case "generation.createVideoCandidates":
      return `Generate ${(args as { batch_size?: number }).batch_size ?? "?"} video candidate(s)`;
    case "canvas.readProject":
      return "Read canvas state";
    default:
      return name;
  }
}

function statusBadge(status: AtelierAgentTurn["status"]): { tone: string; label: string } {
  switch (status) {
    case "pending":
      return {
        tone: "border-blue-400/40 bg-blue-400/12 text-blue-200/95",
        label: "Pending",
      };
    case "waiting_approval":
      return {
        tone: "border-amber-300/40 bg-amber-400/12 text-amber-200/95",
        label: "Waiting",
      };
    case "completed":
      return {
        tone: "border-emerald-400/40 bg-emerald-400/12 text-emerald-200/95",
        label: "Done",
      };
    case "failed":
      return {
        tone: "border-red-400/40 bg-red-400/12 text-red-200/95",
        label: "Failed",
      };
    default:
      return {
        tone: "border-white/8 bg-black/25 text-text-secondary",
        label: status,
      };
  }
}

function toolCallDot(status: string): string {
  switch (status) {
    case "completed":
      return "bg-emerald-300 shadow-[0_0_0_2px_rgba(110,231,183,0.18)]";
    case "failed":
    case "denied":
      return "bg-red-300 shadow-[0_0_0_2px_rgba(252,165,165,0.18)]";
    case "approval_required":
      return "bg-amber-300 shadow-[0_0_0_2px_rgba(252,211,77,0.18)]";
    default:
      return "bg-text-muted/60";
  }
}

export function AgentPanelV3({ pushToast }: Props) {
  const project        = useAtelierStore((s) => s.currentProject);
  const selectedNodeId = useAtelierStore((s) => s.selectedNodeId);
  const agentTurns     = useAtelierStore((s) => s.agentTurns);
  const pendingTurn    = useAtelierStore((s) => s.pendingAgentTurn);
  const isAgentRunning = useAtelierStore((s) => s.isAgentRunning);
  const planAgentTurn  = useAtelierStore((s) => s.planAgentTurn);
  const runAgentTurn   = useAtelierStore((s) => s.runAgentTurn);

  const [draft, setDraft] = useState("");
  const [plannedCalls, setPlannedCalls] = useState<AtelierAgentToolCallPayload[]>([]);
  const [planError, setPlanError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);

  const policy = project?.agent_policy;
  const recentTurns = useMemo<AtelierAgentTurn[]>(
    () => (agentTurns ?? []).slice(-6),
    [agentTurns],
  );

  const handlePreview = async () => {
    if (!draft.trim()) {
      pushToast?.("info", "Type a message before previewing.");
      return;
    }
    setPreviewing(true);
    setPlanError(null);
    try {
      const plan = await planAgentTurn({
        user_message: draft,
        selected_node_id: selectedNodeId ?? null,
      });
      if (plan.status === "blocked") {
        setPlanError(plan.reason || "Agent planner refused this request.");
        setPlannedCalls([]);
      } else {
        setPlannedCalls(plan.tool_calls as AtelierAgentToolCallPayload[]);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Agent planning failed";
      setPlanError(msg);
      pushToast?.("error", msg);
    } finally {
      setPreviewing(false);
    }
  };

  const handleExecute = async (preview: boolean) => {
    if (!draft.trim() && plannedCalls.length === 0) {
      pushToast?.("info", "Nothing to execute.");
      return;
    }
    setExecuting(true);
    try {
      await runAgentTurn({
        user_message: draft,
        preview,
        tool_calls: plannedCalls,
      });
      if (!preview) {
        pushToast?.("success", "Agent turn executed.");
        setDraft("");
        setPlannedCalls([]);
        setPlanError(null);
      } else {
        pushToast?.("info", "Preview submitted — see history below.");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Agent run failed";
      pushToast?.("error", msg);
    } finally {
      setExecuting(false);
    }
  };

  const handleApprove = async () => {
    if (!pendingTurn) return;
    setExecuting(true);
    try {
      const tool_calls = pendingTurn.tool_calls
        .filter((c) => c.status === "approval_required" || c.status === "proposed")
        .map((c) => ({ tool_name: c.tool_name, arguments: c.arguments }));
      await runAgentTurn({
        user_message: pendingTurn.user_message,
        approve: true,
        turn_id: pendingTurn.id,
        tool_calls,
      });
      pushToast?.("success", "Approved & executed.");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Approval execution failed";
      pushToast?.("error", msg);
    } finally {
      setExecuting(false);
    }
  };

  const handleReject = async () => {
    if (!pendingTurn) return;
    setExecuting(true);
    try {
      await runAgentTurn({
        user_message: pendingTurn.user_message,
        deny: true,
        turn_id: pendingTurn.id,
        tool_calls: [],
      });
      pushToast?.("info", "Rejected.");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Rejection failed";
      pushToast?.("error", msg);
    } finally {
      setExecuting(false);
    }
  };

  const isLocked = isAgentRunning || executing;
  const hasDraft = draft.trim().length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Conversation scroll region */}
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {recentTurns.length === 0 && !pendingTurn ? (
          <div className="overflow-hidden rounded-[10px] border border-white/8 bg-black/20 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]">
            <div aria-hidden="true" className="h-[1px] bg-gradient-to-r from-primary/45 via-primary/15 to-transparent" />
            <div className="px-3 py-2.5">
              <div className="mb-1.5 flex items-center gap-1.5 font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-primary/85">
                <Sparkles size={10} aria-hidden="true" />
                Try asking
              </div>
              <ul className="space-y-1 border-l border-white/6 pl-3 text-[12px] leading-[1.5] text-text-secondary/95">
                <li>Create three drafts for a rainy rooftop chase.</li>
                <li>Generate 4 candidates for the selected draft.</li>
                <li>Add the neon alley reference to the cinematic draft.</li>
              </ul>
            </div>
          </div>
        ) : null}

        {recentTurns.map((turn) => {
          const badge = statusBadge(turn.status);
          return (
            <div key={turn.id} className="space-y-2">
              {turn.user_message ? (
                <ConversationUserBubble>{turn.user_message}</ConversationUserBubble>
              ) : null}
              <ConversationAgentBubble>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className={`rounded-full border px-1.5 py-[2px] font-mono text-[9px] font-medium uppercase tracking-[0.18em] ${badge.tone}`}>
                      {badge.label}
                    </span>
                    <span className="font-mono text-[9px] tracking-tight text-text-muted/85">
                      {new Date(turn.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </span>
                  </div>
                  {turn.tool_calls.length > 0 ? (
                    <ul className="space-y-1 border-l border-white/6 pl-2.5 text-[12px] leading-[1.5]">
                      {turn.tool_calls.map((c) => (
                        <li key={c.call_id} className="flex items-start gap-1.5">
                          <span aria-hidden="true" className={`mt-[7px] h-[5px] w-[5px] shrink-0 rounded-full ${toolCallDot(c.status)}`} />
                          <span className="text-text-secondary/95">{summarizeToolCall(c)}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              </ConversationAgentBubble>
            </div>
          );
        })}

        {/* Pending approval card */}
        {pendingTurn ? (
          <div
            role="alertdialog"
            className="overflow-hidden rounded-[12px] border border-amber-300/35 bg-amber-400/[0.04] shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(252,211,77,0.08)]"
          >
            <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-amber-300 via-amber-300/45 to-transparent" />
            <div className="px-3.5 pb-3 pt-3">
              <div className="mb-2 flex items-center gap-1.5">
                <ShieldCheck size={12} className="text-amber-200" aria-hidden="true" />
                <span className="font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-amber-200/95">
                  Action required
                </span>
              </div>
              {pendingTurn.user_message ? (
                <p className="mb-2.5 text-[12px] italic leading-[1.5] text-text-secondary/95">
                  &ldquo;{pendingTurn.user_message}&rdquo;
                </p>
              ) : null}
              <ul className="mb-3 space-y-1 border-l border-amber-300/15 pl-2.5 text-[13px] leading-[1.5] text-foreground/95">
                {pendingTurn.tool_calls.filter((c) => c.status === "approval_required" || c.status === "proposed").map((c) => (
                  <li key={c.call_id} className="flex items-start gap-1.5">
                    <Sparkles size={10} className="mt-[5px] shrink-0 text-amber-200/85" aria-hidden="true" />
                    <span>{summarizeToolCall(c)}</span>
                  </li>
                ))}
              </ul>
              <div className="grid grid-cols-2 gap-2">
                <button
                  disabled={isLocked}
                  onClick={handleApprove}
                  className="inline-flex items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_12px_-4px_rgba(100,108,255,0.5)] transition-all duration-200 hover:scale-[1.02] hover:bg-primary/92 hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.22),0_6px_16px_-4px_rgba(100,108,255,0.6)] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:scale-100"
                >
                  {isLocked ? (
                    <>
                      <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                      Running
                    </>
                  ) : (
                    "Approve & run"
                  )}
                </button>
                <button
                  disabled={isLocked}
                  onClick={handleReject}
                  className="rounded-md border border-white/10 bg-black/25 px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-text-secondary/95 transition-all duration-150 hover:border-white/15 hover:bg-white/[0.06] hover:text-foreground active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Reject
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {/* Currently planning — show a thinking bubble */}
        {previewing ? (
          <ConversationAgentBubble thinking />
        ) : null}

        {/* Plan preview before execute */}
        {plannedCalls.length > 0 && !pendingTurn ? (
          <div className="overflow-hidden rounded-[10px] border border-white/8 bg-black/20 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]">
            <div aria-hidden="true" className="h-[1px] bg-gradient-to-r from-primary/45 via-primary/15 to-transparent" />
            <div className="px-3 py-2.5">
              <div className="mb-1.5 flex items-center justify-between gap-2 font-mono text-[9px] font-medium uppercase tracking-[0.22em]">
                <span className="flex items-center gap-1.5 text-primary/85">
                  <Sparkles size={10} aria-hidden="true" />
                  Plan preview
                </span>
                <span className="text-text-muted/85">{plannedCalls.length} step{plannedCalls.length === 1 ? "" : "s"}</span>
              </div>
              <ul className="space-y-1 border-l border-white/6 pl-2.5 text-[12px] leading-[1.5]">
                {plannedCalls.map((c, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span aria-hidden="true" className="mt-[7px] h-[5px] w-[5px] shrink-0 rounded-full bg-primary/85 shadow-[0_0_0_2px_rgba(100,108,255,0.18)]" />
                    <span className="text-text-secondary/95">{summarizeToolCall(c)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        {planError ? (
          <div role="alert" className="rounded-md border border-red-400/35 bg-red-400/[0.06] px-3 py-2 text-[12px] leading-[1.5] text-red-100/95">
            <div className="mb-1 font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-red-200/85">
              Planner blocked
            </div>
            {planError}
          </div>
        ) : null}
      </div>

      {/* Composer */}
      <div className="border-t border-white/6 px-3 pb-3 pt-2.5">
        <div className="overflow-hidden rounded-[10px] border border-white/8 bg-black/25 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]">
          <textarea
            rows={2}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              if (plannedCalls.length > 0) setPlannedCalls([]);
            }}
            placeholder={selectedNodeId ? "Ask Agent about the selected node…" : "Ask Agent…"}
            disabled={isLocked}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                void handleExecute(false);
              }
            }}
            className="block w-full resize-none bg-transparent px-3 pt-2.5 pb-1 text-[13px] leading-[1.55] text-foreground/95 outline-none placeholder:text-text-muted/85 disabled:cursor-not-allowed"
          />
          <div className="flex items-center justify-between gap-2 border-t border-white/5 px-2 py-1.5">
            <div className="flex items-center gap-2">
              <button
                className="btn-tip inline-flex h-6 w-6 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                data-tip="Attach context"
                disabled
              >
                <Paperclip size={11} aria-hidden="true" />
              </button>
              {policy?.approval_mode ? (
                <span className="font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-text-muted/80">
                  Mode <span aria-hidden="true" className="text-text-muted/50">·</span>{" "}
                  <span className="text-text-secondary/95">{policy.approval_mode.replace("_", " ")}</span>
                </span>
              ) : null}
            </div>
            <div className="flex items-center gap-1.5">
              <button
                disabled={isLocked || previewing || !hasDraft}
                onClick={handlePreview}
                className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-black/25 px-2.5 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-text-secondary/95 transition-all duration-150 hover:border-white/15 hover:bg-white/[0.05] hover:text-foreground active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {previewing ? (
                  <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                ) : null}
                Preview
              </button>
              <button
                disabled={isLocked || (!hasDraft && plannedCalls.length === 0)}
                onClick={() => handleExecute(false)}
                className={`inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_12px_-4px_rgba(100,108,255,0.5)] transition-all duration-200 hover:scale-[1.04] hover:bg-primary/92 hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.22),0_6px_16px_-4px_rgba(100,108,255,0.6)] active:scale-[0.94] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:scale-100 ${
                  hasDraft && !isLocked && !executing ? "motion-safe:animate-atelier-pulse-soft" : ""
                }`}
              >
                {executing ? (
                  <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                ) : (
                  <Send size={11} aria-hidden="true" />
                )}
                Execute
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
