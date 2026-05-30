"use client";
import { useEffect, useMemo, useState } from "react";
import { Bot, Loader2, Paperclip, ShieldCheck, Sparkles, Wand2, X } from "lucide-react";
import { useAtelierStore } from "@/store/atelierStore";
import type {
  AtelierAgentTurn,
  AtelierAgentToolCallPayload,
} from "@/lib/api";

interface Toast { kind: "info" | "success" | "error"; text: string }

interface Props {
  pushToast?: (kind: Toast["kind"], text: string) => void;
}

// v0.4.5 §13.4: user bubble shifts from saturated cobalt (primary/0.1
// + primary/0.2) to muted brand-soft. The cobalt was reading as "Slack
// UI"; muted slate reads as "designed correspondence."
function ConversationUserBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[88%] rounded-[14px] rounded-tr-[6px] border border-atelier-brand-soft/24 bg-atelier-brand-soft/[0.08] px-3 py-2 text-[13px] italic leading-[1.55] text-atelier-brand-soft shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]" style={{ fontFamily: "'Inter', sans-serif" }}>
        {children}
      </div>
    </div>
  );
}

function ConversationAgentBubble({ children, thinking = false }: { children?: React.ReactNode; thinking?: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-atelier-brand-soft/15 text-atelier-brand-soft ring-1 ring-inset ring-atelier-brand-soft/25">
        <Bot size={13} aria-hidden="true" />
      </span>
      <div className="flex-1 rounded-[14px] rounded-tl-[6px] border border-white/6 bg-black/25 px-3 py-2 text-[13px] leading-[1.55] text-foreground/95 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)]">
        {thinking ? (
          <span className="inline-flex items-center gap-1.5 text-text-secondary/95">
            <span className="inline-block h-[5px] w-[5px] animate-pulse rounded-full bg-atelier-processing shadow-[0_0_0_3px_rgba(96,165,250,0.18)]" />
            <span className="inline-block h-[5px] w-[5px] animate-pulse rounded-full bg-atelier-processing/85" style={{ animationDelay: "0.15s" }} />
            <span className="inline-block h-[5px] w-[5px] animate-pulse rounded-full bg-atelier-processing/65" style={{ animationDelay: "0.3s" }} />
            <span className="ml-1 font-mono text-[10px] uppercase tracking-[0.2em] text-atelier-processing/85">Planning</span>
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

// RHTV pattern: the agent's plan should let the user VERIFY the pre-filled
// params at a glance before confirming — not just "Create video node X" but the
// actual prompt + model/count/aspect it will apply. describeToolCallParams pulls
// the human-relevant args; ToolCallParams renders them as a muted sub-line under
// the verb (used in both the plan preview and the pending-approval card).
function describeToolCallParams(
  call: AtelierAgentToolCallPayload | { tool_name: string; arguments: Record<string, unknown> },
): { prompt?: string; chips: string[] } {
  const a = (call.arguments ?? {}) as Record<string, unknown>;
  const s = (...keys: string[]): string | undefined => {
    for (const k of keys) {
      const v = a[k];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    return undefined;
  };
  const n = (...keys: string[]): number | undefined => {
    for (const k of keys) {
      const v = a[k];
      if (typeof v === "number" && Number.isFinite(v)) return v;
    }
    return undefined;
  };
  const chips: string[] = [];
  const count = n("batch_size", "count", "candidates");
  if (count) chips.push(`${count}×`);
  const model = s("model", "model_label");
  if (model) chips.push(model);
  const aspect = s("aspect_ratio", "aspect", "resolution");
  if (aspect) chips.push(aspect);
  const duration = s("duration");
  if (duration) chips.push(/s$/.test(duration) ? duration : `${duration}s`);
  return { prompt: s("prompt", "intent", "description"), chips };
}

function ToolCallParams({
  call,
}: {
  call: AtelierAgentToolCallPayload | { tool_name: string; arguments: Record<string, unknown> };
}) {
  const { prompt, chips } = describeToolCallParams(call);
  if (!prompt && chips.length === 0) return null;
  return (
    <div className="mt-1 space-y-1">
      {prompt ? (
        <p
          className="line-clamp-2 border-l border-white/8 pl-2 font-sans text-[11.5px] italic leading-[1.45] text-text-secondary/85"
          title={prompt}
        >
          &ldquo;{prompt}&rdquo;
        </p>
      ) : null}
      {chips.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1">
          {chips.map((c, i) => (
            <span
              key={i}
              className="rounded-[3px] border border-white/8 bg-white/[0.03] px-1.5 py-[1px] font-mono text-[9px] uppercase tracking-[0.14em] text-text-muted/90"
            >
              {c}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function statusBadge(status: AtelierAgentTurn["status"]): { tone: string; label: string } {
  switch (status) {
    case "pending":
      return {
        tone: "border-atelier-processing/40 bg-atelier-processing/12 text-atelier-processing",
        label: "Pending",
      };
    case "waiting_approval":
      return {
        tone: "border-atelier-brand-300/40 bg-atelier-brand-300/12 text-atelier-brand-300",
        label: "Waiting",
      };
    case "completed":
      return {
        tone: "border-atelier-completed/40 bg-atelier-completed/12 text-atelier-completed",
        label: "Done",
      };
    case "failed":
      return {
        tone: "border-atelier-failed/40 bg-atelier-failed/12 text-atelier-failed",
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
      return "bg-atelier-completed shadow-[0_0_0_2px_rgba(52,211,153,0.18)]";
    case "failed":
    case "denied":
      return "bg-atelier-failed shadow-[0_0_0_2px_rgba(248,113,113,0.18)]";
    case "approval_required":
      return "bg-atelier-brand-300 shadow-[0_0_0_2px_rgba(110,143,255,0.18)]";
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
  // Mode: "free" routes to deterministic_core (single-action plans);
  // "director" routes to the new structure planner that emits multi-step
  // plans for "3-shot story" / "N variants" / "motion study" etc.
  // Persisted across renders so a power user can stay in director mode.
  const [plannerMode, setPlannerMode] = useState<"free" | "director">(() => {
    if (typeof window === "undefined") return "free";
    const v = window.localStorage.getItem("atelier-v3-planner-mode");
    return v === "director" ? "director" : "free";
  });
  const setPlannerModePersist = (next: "free" | "director") => {
    setPlannerMode(next);
    try {
      window.localStorage.setItem("atelier-v3-planner-mode", next);
    } catch {
      /* ignore quota / private mode */
    }
    // Stale plan from a previous mode would mismatch the new vocabulary,
    // so flush it whenever the mode flips.
    if (plannedCalls.length > 0) setPlannedCalls([]);
    setPlanError(null);
  };

  // G: react to LeftRail's Director toggle. The shell flips
  // localStorage + fires this custom event; we mirror it into our
  // local state so the segmented mode toggle visually agrees.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onModeChanged = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail === "free" || detail === "director") {
        setPlannerMode(detail);
        if (plannedCalls.length > 0) setPlannedCalls([]);
        setPlanError(null);
      }
    };
    window.addEventListener("atelier-planner-mode-changed", onModeChanged);
    return () => window.removeEventListener("atelier-planner-mode-changed", onModeChanged);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Per-tool rejection (PRD §14.4). The user can flip individual proposed
  // calls off before approving — what gets executed is `proposed ∖ rejected`.
  // Reset whenever the pending turn changes so each new approval card starts
  // with everything green-lit.
  const [rejectedCallIds, setRejectedCallIds] = useState<Set<string>>(() => new Set());
  const pendingTurnId = pendingTurn?.id;
  useEffect(() => {
    setRejectedCallIds(new Set());
  }, [pendingTurnId]);
  const toggleCallRejection = (callId: string) => {
    setRejectedCallIds((prev) => {
      const next = new Set(prev);
      if (next.has(callId)) next.delete(callId);
      else next.add(callId);
      return next;
    });
  };

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
        planner: plannerMode === "director" ? "structure" : null,
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
      const turn = await runAgentTurn({
        user_message: draft,
        preview,
        tool_calls: plannedCalls,
      });
      if (!preview) {
        pushToast?.("success", "Agent turn executed.");
        setDraft("");
        setPlannedCalls([]);
        setPlanError(null);
        // G: trusted-policy direct execute path — wrap if director run
        // produced 2+ nodes.
        void maybeWrapDirectorOutput(turn, draft);
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

  // G: collect node IDs created by a completed turn and (if 2+) wrap
  // them in a Director region. We key on plannerMode at call time
  // because AtelierAgentTurn doesn't carry the planner field directly.
  // Region title falls back to a short hash of the user message if no
  // skill_name-derived label is available.
  const maybeWrapDirectorOutput = async (turn: AtelierAgentTurn | null | undefined, userMessage: string) => {
    if (!turn || turn.status !== "completed") return;
    if (plannerMode !== "director") return;
    const createdIds: string[] = [];
    for (const call of turn.tool_calls) {
      if (
        call.tool_name !== "canvas.createVideoNode" &&
        call.tool_name !== "canvas.createReferenceImageNode"
      ) continue;
      const snap = call.result_snapshot as { node?: { id?: string } } | null | undefined;
      const id = snap?.node?.id;
      if (typeof id === "string") createdIds.push(id);
    }
    if (createdIds.length < 2) return;
    const trimmedMessage = userMessage.trim();
    const title = trimmedMessage
      ? trimmedMessage.length > 32 ? trimmedMessage.slice(0, 32) + "…" : trimmedMessage
      : "Director output";
    try {
      await useAtelierStore.getState().createRegion({ title, wrap: createdIds });
      pushToast?.("success", `Wrapped ${createdIds.length} Director nodes in a region.`);
    } catch (err) {
      pushToast?.(
        "error",
        `Region wrap failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  };

  const handleApprove = async () => {
    if (!pendingTurn) return;
    const proposedCalls = pendingTurn.tool_calls.filter(
      (c) => c.status === "approval_required" || c.status === "proposed",
    );
    const acceptedCalls = proposedCalls.filter((c) => !rejectedCallIds.has(c.call_id));
    // All rejected → route to global deny so the turn closes cleanly rather
    // than executing nothing.
    if (acceptedCalls.length === 0) {
      await handleReject();
      return;
    }
    setExecuting(true);
    try {
      const tool_calls = acceptedCalls.map((c) => ({
        tool_name: c.tool_name,
        arguments: c.arguments,
      }));
      const turn = await runAgentTurn({
        user_message: pendingTurn.user_message,
        approve: true,
        turn_id: pendingTurn.id,
        tool_calls,
      });
      const skipped = proposedCalls.length - acceptedCalls.length;
      pushToast?.(
        "success",
        skipped > 0
          ? `Approved ${acceptedCalls.length}, skipped ${skipped}.`
          : "Approved & executed.",
      );
      void maybeWrapDirectorOutput(turn, pendingTurn.user_message);
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
          // Editorial 'try asking' card. Reads as a thumbed-down menu of
          // possible openings — italic display body for each line, mono
          // caps tear-stamp footer caption.
          <div className="overflow-hidden rounded-[10px] border border-white/8 bg-black/20 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]">
            <div aria-hidden="true" className="h-[1px] bg-gradient-to-r from-atelier-brand-soft/40 via-atelier-brand-soft/12 to-transparent" />
            <div className="px-3.5 py-3">
              <div className="mb-2 flex items-center gap-1.5 font-mono text-[9px] font-medium uppercase tracking-[0.28em] text-atelier-brand-soft/85">
                <Sparkles size={10} aria-hidden="true" />
                Try asking
              </div>
              <ul className="space-y-1.5 border-l border-white/6 pl-3 font-sans text-[13px] italic leading-[1.5] tracking-tight text-foreground/92">
                {plannerMode === "director" ? (
                  <>
                    <li>3-shot story about a rooftop chase.</li>
                    <li>4 variants from this reference.</li>
                    <li>Motion study (select an image first).</li>
                    <li>Character ref → video.</li>
                  </>
                ) : (
                  <>
                    <li>Create three drafts for a rainy rooftop chase.</li>
                    <li>Generate 4 candidates for the selected draft.</li>
                    <li>Add the neon alley reference to the cinematic draft.</li>
                  </>
                )}
              </ul>
              <div
                aria-hidden="true"
                className="mt-3 flex items-center gap-2"
              >
                <div className="flex-1 border-t border-dashed border-white/10" />
                <span className="font-mono text-[8.5px] font-medium uppercase tracking-[0.28em] text-text-muted/75">
                  Prompt · Library
                </span>
                <div className="flex-1 border-t border-dashed border-white/10" />
              </div>
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
            className="overflow-hidden rounded-[12px] border border-atelier-brand-300/35 bg-atelier-brand-300/[0.06] shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(110,143,255,0.08)] motion-safe:animate-atelier-popover-in"
          >
            <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-atelier-brand-300 via-atelier-brand-300/45 to-transparent" />
            <div className="px-3.5 pb-3 pt-3">
              <div className="mb-2 flex items-center gap-1.5">
                <ShieldCheck size={12} className="text-atelier-brand-300" aria-hidden="true" />
                <span className="font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-atelier-brand-300">
                  Action required
                </span>
              </div>
              {pendingTurn.user_message ? (
                <p className="mb-2.5 text-[12px] italic leading-[1.5] text-text-secondary/95">
                  &ldquo;{pendingTurn.user_message}&rdquo;
                </p>
              ) : null}
              <ul className="mb-3 space-y-1 border-l border-atelier-brand-300/15 pl-2.5 text-[13px] leading-[1.5] text-foreground/95">
                {pendingTurn.tool_calls
                  .filter((c) => c.status === "approval_required" || c.status === "proposed")
                  .map((c) => {
                    const rejected = rejectedCallIds.has(c.call_id);
                    return (
                      <li key={c.call_id} className="group/call flex items-start gap-1.5">
                        <Sparkles
                          size={10}
                          className={`mt-[5px] shrink-0 transition-colors ${
                            rejected ? "text-text-muted/55" : "text-atelier-brand-300/85"
                          }`}
                          aria-hidden="true"
                        />
                        <div className="min-w-0 flex-1">
                          <span
                            className={`transition-colors ${
                              rejected ? "text-text-muted/65 line-through" : "text-foreground/95"
                            }`}
                          >
                            {summarizeToolCall(c)}
                          </span>
                          {rejected ? null : <ToolCallParams call={c} />}
                        </div>
                        <button
                          type="button"
                          aria-label={rejected ? "Restore this action" : "Skip this action"}
                          aria-pressed={rejected}
                          data-tip={rejected ? "Restore" : "Skip"}
                          onClick={() => toggleCallRejection(c.call_id)}
                          disabled={isLocked}
                          className={`btn-tip mt-[2px] grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
                            rejected
                              ? "border-atelier-brand-300/40 bg-atelier-brand-300/10 text-atelier-brand-300 hover:bg-atelier-brand-300/15"
                              : "border-white/10 bg-black/25 text-text-muted opacity-0 hover:border-atelier-failed/40 hover:bg-atelier-failed/10 hover:text-atelier-failed group-hover/call:opacity-100 focus:opacity-100"
                          }`}
                        >
                          {rejected ? (
                            <Sparkles size={9} aria-hidden="true" />
                          ) : (
                            <X size={10} aria-hidden="true" />
                          )}
                        </button>
                      </li>
                    );
                  })}
              </ul>
              <div className="grid grid-cols-2 gap-2">
                {(() => {
                  const proposedCount = pendingTurn.tool_calls.filter(
                    (c) => c.status === "approval_required" || c.status === "proposed",
                  ).length;
                  const skippedCount = pendingTurn.tool_calls.filter(
                    (c) =>
                      (c.status === "approval_required" || c.status === "proposed") &&
                      rejectedCallIds.has(c.call_id),
                  ).length;
                  const allSkipped = skippedCount === proposedCount && proposedCount > 0;
                  const partial = skippedCount > 0 && !allSkipped;
                  const label = allSkipped
                    ? "Skip all"
                    : partial
                      ? `Approve ${proposedCount - skippedCount}`
                      : "Approve & run";
                  return (
                    <button
                      disabled={isLocked}
                      onClick={handleApprove}
                      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-[0.18em] transition-all duration-200 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:scale-100 ${
                        allSkipped
                          ? "border border-atelier-failed/35 bg-atelier-failed/10 text-atelier-failed hover:bg-atelier-failed/15"
                          : "bg-atelier-brand-400 text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_12px_-4px_rgba(59,107,255,0.5)] hover:bg-atelier-brand-400/92 hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.22),0_6px_16px_-4px_rgba(59,107,255,0.6)]"
                      }`}
                    >
                      {isLocked ? (
                        <>
                          <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                          Running
                        </>
                      ) : (
                        label
                      )}
                    </button>
                  );
                })()}
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
          <div className="overflow-hidden rounded-[10px] border border-white/8 bg-black/20 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)] motion-safe:animate-atelier-popover-in">
            <div aria-hidden="true" className="h-[1px] bg-gradient-to-r from-atelier-brand-soft/40 via-atelier-brand-soft/12 to-transparent" />
            <div className="px-3 py-2.5">
              <div className="mb-1.5 flex items-center justify-between gap-2 font-mono text-[9px] font-medium uppercase tracking-[0.22em]">
                <span className="flex items-center gap-1.5 text-atelier-brand-soft/85">
                  <Sparkles size={10} aria-hidden="true" />
                  Plan preview
                </span>
                <span className="text-text-muted/85">{plannedCalls.length} step{plannedCalls.length === 1 ? "" : "s"}</span>
              </div>
              <ul className="space-y-1 border-l border-white/6 pl-2.5 text-[12px] leading-[1.5]">
                {plannedCalls.map((c, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span aria-hidden="true" className="mt-[7px] h-[5px] w-[5px] shrink-0 rounded-full bg-atelier-brand-soft/85 shadow-[0_0_0_2px_rgba(138,156,196,0.18)]" />
                    <div className="min-w-0 flex-1">
                      <span className="text-text-secondary/95">{summarizeToolCall(c)}</span>
                      <ToolCallParams call={c} />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        {planError ? (
          <div role="alert" className="rounded-md border border-atelier-failed/35 bg-atelier-failed/[0.06] px-3 py-2 text-[12px] leading-[1.5] text-foreground/95 motion-safe:animate-atelier-popover-in">
            <div className="mb-1 font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-atelier-failed">
              Planner blocked
            </div>
            {planError}
          </div>
        ) : null}
      </div>

      {/* Composer */}
      <div className="border-t border-white/6 px-3 pb-3 pt-2.5">
        {/* Canvas-context chip — makes the agent visibly "read" the canvas: when
            a node is selected the agent grounds its plan on it (selected_node_id
            is already passed to the planner). Shows WHICH node so the resident
            agent feels connected to the canvas, RHTV-style. */}
        {(() => {
          const ctxNode = selectedNodeId ? project?.nodes.find((n) => n.id === selectedNodeId) : undefined;
          if (!ctxNode) return null;
          const label =
            (typeof ctxNode.data?.intent === "string" && ctxNode.data.intent) ||
            ctxNode.title ||
            `${ctxNode.type} node`;
          return (
            <div className="mb-1.5 flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.18em] text-atelier-brand-soft/85">
              <span aria-hidden="true" className="h-[5px] w-[5px] shrink-0 rounded-full bg-atelier-brand-soft/70" />
              <span className="truncate">Reading · {label}</span>
            </div>
          );
        })()}
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
              {/* Planner mode toggle. Free = deterministic single-action
                  plans (existing). Director = structure planner: maps
                  "3-shot story" / "N variants" / "motion study" intents
                  into multi-step canvas plans.
                  v0.4.5 §12.3 + §12.7.1: editorial-toggle style — Space
                  Grotesk upright + weight 600 + cobalt underline on
                  active (no italic — Space Grotesk has no italic glyph,
                  so synthesized italic made Free vs Director read as
                  two fonts). */}
              <div role="tablist" aria-label="Planner mode" className="atelier-editorial-toggle">
                <button
                  role="tab"
                  type="button"
                  aria-selected={plannerMode === "free"}
                  data-tip="Free intent → single action"
                  onClick={() => setPlannerModePersist("free")}
                  disabled={isLocked}
                  className={`opt btn-tip disabled:cursor-not-allowed disabled:opacity-60 ${
                    plannerMode === "free" ? "on" : ""
                  }`}
                >
                  Free
                </button>
                <button
                  role="tab"
                  type="button"
                  aria-selected={plannerMode === "director"}
                  data-tip="Director · structured multi-step plans"
                  onClick={() => setPlannerModePersist("director")}
                  disabled={isLocked}
                  className={`opt btn-tip inline-flex items-center gap-1.5 disabled:cursor-not-allowed disabled:opacity-60 ${
                    plannerMode === "director" ? "on" : ""
                  }`}
                >
                  <Wand2 size={10} aria-hidden="true" />
                  Director
                </button>
              </div>
              {policy?.approval_mode ? (
                <span className="font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-text-muted/80">
                  <span aria-hidden="true" className="text-text-muted/50">·</span>{" "}
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
              {/* v0.4.5 §12.3: editorial primary — Inter italic verb + →.
                  Replaces the cobalt-pill submit. Same affordance, new register. */}
              <button
                disabled={isLocked || (!hasDraft && plannedCalls.length === 0)}
                onClick={() => handleExecute(false)}
                className={`atelier-btn-editorial primary ${
                  hasDraft && !isLocked && !executing ? "motion-safe:animate-atelier-pulse-soft" : ""
                }`}
              >
                {executing ? (
                  <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                ) : null}
                Execute
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
