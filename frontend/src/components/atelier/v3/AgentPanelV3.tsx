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
      <div className="max-w-[88%] rounded-2xl rounded-tr-md bg-primary/15 px-3 py-2 text-[13px] text-foreground">
        {children}
      </div>
    </div>
  );
}

function ConversationAgentBubble({ children, thinking = false }: { children?: React.ReactNode; thinking?: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-primary/20 text-primary">
        <Bot size={13} />
      </span>
      <div className="flex-1 rounded-2xl rounded-tl-md bg-glass px-3 py-2 text-[13px] text-foreground">
        {thinking ? (
          <span className="inline-flex items-center gap-1.5 text-text-secondary">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-blue-300" />
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-blue-300" style={{ animationDelay: "0.2s" }} />
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-blue-300" style={{ animationDelay: "0.4s" }} />
            <span className="ml-1 text-[12px]">Planning…</span>
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
    case "pending":           return { tone: "border-blue-400/50 bg-blue-400/10 text-blue-200",       label: "pending" };
    case "waiting_approval":  return { tone: "border-amber-300/40 bg-amber-400/[0.06] text-amber-100", label: "waiting" };
    case "completed":         return { tone: "border-emerald-400/50 bg-emerald-400/10 text-emerald-200", label: "done" };
    case "failed":            return { tone: "border-red-400/50 bg-red-400/10 text-red-200",          label: "failed" };
    default:                  return { tone: "border-glass-border bg-glass text-text-secondary",      label: status };
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

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Conversation scroll region */}
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {recentTurns.length === 0 && !pendingTurn ? (
          <div className="rounded-md border border-border-subtle bg-glass px-3 py-3 text-[12px] text-text-muted">
            <div className="mb-1 inline-flex items-center gap-1.5 text-text-secondary">
              <Sparkles size={12} className="text-primary" /> Try asking the Agent
            </div>
            <ul className="ml-1 mt-1 space-y-1 text-[11px] leading-relaxed">
              <li>· "Create three drafts for a rainy rooftop chase."</li>
              <li>· "Generate 4 candidates for the selected draft."</li>
              <li>· "Add the neon alley reference to the cinematic draft."</li>
            </ul>
          </div>
        ) : null}

        {recentTurns.map((turn) => (
          <div key={turn.id} className="space-y-2">
            {turn.user_message ? (
              <ConversationUserBubble>{turn.user_message}</ConversationUserBubble>
            ) : null}
            <ConversationAgentBubble>
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-2 text-[11px]">
                  <span className={`rounded border px-1.5 py-0.5 ${statusBadge(turn.status).tone}`}>{statusBadge(turn.status).label}</span>
                  <span className="font-mono text-[10px] text-text-muted">{new Date(turn.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                </div>
                {turn.tool_calls.length > 0 ? (
                  <ul className="mt-1 space-y-0.5 text-[12px]">
                    {turn.tool_calls.map((c) => (
                      <li key={c.call_id} className="flex items-center gap-1.5">
                        <span className={
                          c.status === "completed" ? "h-1.5 w-1.5 rounded-full bg-emerald-300" :
                          c.status === "failed" || c.status === "denied" ? "h-1.5 w-1.5 rounded-full bg-red-300" :
                          c.status === "approval_required" ? "h-1.5 w-1.5 rounded-full bg-amber-300" :
                          "h-1.5 w-1.5 rounded-full bg-text-muted/60"
                        } />
                        <span className="text-text-secondary">{summarizeToolCall(c)}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            </ConversationAgentBubble>
          </div>
        ))}

        {/* Pending approval card */}
        {pendingTurn ? (
          <div role="alertdialog" className="rounded-xl border border-amber-300/40 bg-amber-400/[0.06] p-3">
            <div className="mb-2 inline-flex items-center gap-1.5">
              <ShieldCheck size={13} className="text-amber-200" />
              <span className="font-mono text-[10px] uppercase tracking-wider text-amber-200/95">Action required</span>
            </div>
            {pendingTurn.user_message ? (
              <p className="mb-2 text-[12px] text-text-secondary italic">"{pendingTurn.user_message}"</p>
            ) : null}
            <ul className="mb-3 space-y-0.5 text-[13px] text-foreground">
              {pendingTurn.tool_calls.filter((c) => c.status === "approval_required" || c.status === "proposed").map((c) => (
                <li key={c.call_id} className="flex items-start gap-2">
                  <Sparkles size={11} className="mt-0.5 shrink-0 text-primary" />
                  <span>{summarizeToolCall(c)}</span>
                </li>
              ))}
            </ul>
            <div className="grid grid-cols-2 gap-2">
              <button
                disabled={isLocked}
                onClick={handleApprove}
                className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-white hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isLocked ? <span className="inline-flex items-center gap-1.5"><Loader2 size={12} className="animate-spin" /> Running</span> : "Approve & run"}
              </button>
              <button
                disabled={isLocked}
                onClick={handleReject}
                className="rounded-md border border-glass-border px-3 py-2 text-sm text-text-secondary hover:bg-hover-bg disabled:cursor-not-allowed disabled:opacity-60"
              >
                Reject
              </button>
            </div>
          </div>
        ) : null}

        {/* Currently planning — show a thinking bubble */}
        {previewing ? (
          <ConversationAgentBubble thinking />
        ) : null}

        {/* Plan preview before execute */}
        {plannedCalls.length > 0 && !pendingTurn ? (
          <div className="rounded-md border border-border-subtle bg-glass p-3">
            <div className="mb-1 inline-flex items-center gap-1.5 text-[12px] font-semibold text-foreground">
              <Sparkles size={12} className="text-primary" /> Plan preview ({plannedCalls.length})
            </div>
            <ul className="space-y-0.5 text-[12px]">
              {plannedCalls.map((c, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/80" />
                  <span className="text-text-secondary">{summarizeToolCall(c)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {planError ? (
          <div role="alert" className="rounded-md border border-red-400/40 bg-red-400/[0.08] px-3 py-2 text-[12px] text-red-100">
            {planError}
          </div>
        ) : null}
      </div>

      {/* Composer */}
      <div className="border-t border-border-subtle p-3">
        <div className="rounded-xl border border-glass-border bg-glass p-2">
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
            className="w-full resize-none bg-transparent px-1 py-1 text-[13px] text-foreground outline-none placeholder:text-text-muted disabled:cursor-not-allowed"
          />
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1">
              <button className="btn-tip rounded p-1 text-text-muted hover:bg-hover-bg hover:text-foreground" data-tip="Attach context" disabled>
                <Paperclip size={13} />
              </button>
              {policy?.approval_mode ? (
                <span className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
                  mode: {policy.approval_mode.replace("_", " ")}
                </span>
              ) : null}
            </div>
            <div className="flex items-center gap-1.5">
              <button
                disabled={isLocked || previewing || !draft.trim()}
                onClick={handlePreview}
                className="rounded-md border border-glass-border bg-glass px-3 py-1.5 text-[12px] font-semibold text-foreground hover:bg-hover-bg disabled:cursor-not-allowed disabled:opacity-50"
              >
                {previewing ? <Loader2 size={12} className="animate-spin" /> : "Preview"}
              </button>
              <button
                disabled={isLocked || (!draft.trim() && plannedCalls.length === 0)}
                onClick={() => handleExecute(false)}
                className="inline-flex items-center gap-1.5 rounded-md bg-foreground px-3 py-1.5 text-[12px] font-semibold text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {executing ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
                Execute
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
