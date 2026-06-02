"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AtSign,
  Bot,
  Clapperboard,
  ImagePlus,
  Loader2,
  Mic2,
  Music,
  Palette,
  Paperclip,
  ScrollText,
  ShieldCheck,
  Sparkles,
  Square,
  Wand2,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import { useAtelierStore } from "@/store/atelierStore";
import type {
  AtelierAgentTurn,
  AtelierAgentToolCallPayload,
} from "@/lib/api";

interface Toast { kind: "info" | "success" | "error"; text: string }

// v0.7 §B — empty-state skill catalog. 8 cards arranged 2×4 below the orb
// greeting, each a soft prompt onto the agent's intended capabilities. Keep
// this list synced with the planner-side seed-graph registry; titles double
// as toast copy until the corresponding planner intents land.
export type SkillCardId =
  | "compose_shortfilm"
  | "storyboard_script"
  | "generate_hero_shot"
  | "animate_still"
  | "stylize_footage"
  | "replace_voice"
  | "mix_soundtrack"
  | "try_workflow";

interface SkillCardSpec {
  id: SkillCardId;
  title: string;
  subtitle: string;
  Icon: typeof Clapperboard;
}

const SKILL_CARDS: SkillCardSpec[] = [
  {
    id: "compose_shortfilm",
    title: "Compose a short film",
    subtitle: "Plan, board, and cut a full piece with the director agent.",
    Icon: Clapperboard,
  },
  {
    id: "storyboard_script",
    title: "Storyboard a script",
    subtitle: "Turn a script into shot-by-shot panels.",
    Icon: ScrollText,
  },
  {
    id: "generate_hero_shot",
    title: "Generate hero shot",
    subtitle: "Make a single keyframe from a prompt or reference.",
    Icon: ImagePlus,
  },
  {
    id: "animate_still",
    title: "Animate a still",
    subtitle: "Bring an image to life as a short clip.",
    Icon: Sparkles,
  },
  {
    id: "stylize_footage",
    title: "Stylize footage",
    subtitle: "Restyle existing clips with a look reference.",
    Icon: Palette,
  },
  {
    id: "replace_voice",
    title: "Replace a voice",
    subtitle: "Swap dialogue with a new voice and keep timing.",
    Icon: Mic2,
  },
  {
    id: "mix_soundtrack",
    title: "Mix a soundtrack",
    subtitle: "Generate score and SFX layered to your cut.",
    Icon: Music,
  },
  {
    id: "try_workflow",
    title: "Try a workflow",
    subtitle: "Browse saved templates and recipes to start from.",
    Icon: Workflow,
  },
];

interface Props {
  pushToast?: (kind: Toast["kind"], text: string) => void;
  // v0.7 §B — shell wires this to its rail-mode dispatcher / seed-graph
  // helper. If omitted the panel falls back to a "<title> coming soon"
  // toast so the catalog still feels alive in storybook / standalone
  // contexts (and so this patch stays presentational).
  onSkillCardClick?: (skillId: SkillCardId) => void;
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

// P (v0.9) — incremental streaming bubble. Renders the partial response
// text plus a blinking caret while `done` is false; once done flips true
// the same row reads as a finished agent reply (no caret) until the
// store reconciles with the persisted turn and clears
// streamingAgentTurn.
function StreamingAgentBubble({ text, done }: { text: string; done: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-atelier-brand-soft/15 text-atelier-brand-soft ring-1 ring-inset ring-atelier-brand-soft/25">
        <Bot size={13} aria-hidden="true" />
      </span>
      <div
        className="flex-1 rounded-[14px] rounded-tl-[6px] border border-white/6 bg-black/25 px-3 py-2 text-[13px] leading-[1.55] text-foreground/95 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)]"
        style={{ fontFamily: "'Inter', sans-serif" }}
      >
        {text ? (
          <span className="whitespace-pre-wrap">{text}</span>
        ) : (
          <span className="text-text-secondary/85">Thinking…</span>
        )}
        {done ? null : (
          <span
            aria-hidden="true"
            className="ml-[2px] inline-block h-[12px] w-[6px] translate-y-[2px] bg-atelier-brand-soft/85 motion-safe:animate-pulse"
          />
        )}
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
            <span className="ml-1 text-[11px] text-atelier-processing/85">Planning</span>
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
              className="rounded-[3px] border border-white/8 bg-white/[0.03] px-1.5 py-[1px] font-mono text-[10px] tracking-[0.01em] text-text-muted/90"
            >
              {c}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function turnStatusCaption(status: AtelierAgentTurn["status"]): string {
  switch (status) {
    case "pending":
      return "Pending";
    case "waiting_approval":
      return "Waiting";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    default:
      return status;
  }
}

function turnResponseText(turn: AtelierAgentTurn): string {
  // Prefer the backend-derived `response` summary when present (populated
  // by AtelierAgentHarness._build_turn_response at every terminal site).
  // Fall back to the legacy status-derived caption for persisted pre-v0.7
  // turns and the brief pre-save window where response is still null.
  if (turn.response && turn.response.trim()) {
    return turn.response.trim();
  }
  const toolCount = turn.tool_calls.length;
  switch (turn.status) {
    case "completed":
      return toolCount > 0
        ? `Turn complete · ${toolCount} action${toolCount === 1 ? "" : "s"} ran.`
        : "Turn complete.";
    case "failed":
      return "This turn could not complete.";
    case "waiting_approval":
      return "Awaiting your approval.";
    case "pending":
      return "Working…";
    default:
      return "Turn complete.";
  }
}

function AgentTurnRow({ turn }: { turn: AtelierAgentTurn }) {
  const [showTools, setShowTools] = useState(false);
  const toolCount = turn.tool_calls.length;
  const time = new Date(turn.created_at * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const statusCaption = turnStatusCaption(turn.status);
  const responseText = turnResponseText(turn);
  return (
    <div className="space-y-2">
      {turn.user_message ? (
        <ConversationUserBubble>{turn.user_message}</ConversationUserBubble>
      ) : null}
      <div className="flex items-start gap-2">
        <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-atelier-brand-soft/15 text-atelier-brand-soft ring-1 ring-inset ring-atelier-brand-soft/25">
          <Bot size={13} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1 space-y-1.5">
          <p
            className="text-[13px] leading-[1.55] text-foreground/75"
            style={{ fontFamily: "'Inter', sans-serif" }}
          >
            {responseText}
          </p>
          {toolCount > 0 ? (
            <div>
              <button
                type="button"
                onClick={() => setShowTools((v) => !v)}
                aria-expanded={showTools}
                className="text-[11px] text-atelier-brand-soft/75 transition-colors hover:text-atelier-brand-soft"
              >
                {showTools ? "Hide" : "Show"} {toolCount} tool call{toolCount === 1 ? "" : "s"}
              </button>
              {showTools ? (
                <ul className="mt-1 space-y-1 border-l border-white/6 pl-2.5 text-[12px] leading-[1.5]">
                  {turn.tool_calls.map((c) => (
                    <li key={c.call_id} className="flex items-start gap-1.5">
                      <span
                        aria-hidden="true"
                        className={`mt-[7px] h-[5px] w-[5px] shrink-0 rounded-full ${toolCallDot(c.status)}`}
                      />
                      <span className="text-text-secondary/95">{summarizeToolCall(c)}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
          <div className="text-[11px] text-white/40">
            {statusCaption} · {time}
          </div>
        </div>
      </div>
    </div>
  );
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

export function AgentPanelV3({ pushToast, onSkillCardClick }: Props) {
  const project        = useAtelierStore((s) => s.currentProject);
  const selectedNodeId = useAtelierStore((s) => s.selectedNodeId);
  const agentTurns     = useAtelierStore((s) => s.agentTurns);
  const pendingTurn    = useAtelierStore((s) => s.pendingAgentTurn);
  const isAgentRunning = useAtelierStore((s) => s.isAgentRunning);
  const planAgentTurn  = useAtelierStore((s) => s.planAgentTurn);
  const runAgentTurn   = useAtelierStore((s) => s.runAgentTurn);
  // P (v0.9) — incremental streaming response slice + stream executor.
  // streamingAgentTurn is null when no stream is in flight; while non-null
  // the bubble renders with a blinking caret until done flips true. Reads
  // are scoped to the panel so other parts of the canvas don't re-render
  // on every delta.
  const streamingTurn  = useAtelierStore((s) => s.streamingAgentTurn);
  const runAgentTurnStreaming_P = useAtelierStore((s) => s.runAgentTurnStreaming_P);
  // AbortController for the in-flight stream. Replaced on each new run
  // so a Stop click cancels exactly the request the user is watching.
  const streamAbortRef = useRef<AbortController | null>(null);

  const [draft, setDraft] = useState("");
  const [plannedCalls, setPlannedCalls] = useState<AtelierAgentToolCallPayload[]>([]);
  // v0.8 item L — when the LLM-backed planner emits a `response` string we
  // park it alongside plannedCalls so handleExecute can forward it to the
  // backend as `assistant_response` (the harness uses it verbatim for
  // turn.response, replacing the deterministic English summary).
  const [plannedResponse, setPlannedResponse] = useState<string | null>(null);
  const [plannedPlannerName, setPlannedPlannerName] = useState<string | null>(null);
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
    setPlannedResponse(null);
    setPlannedPlannerName(null);
    setPlanError(null);
  };

  // v0.8 item L — which planner name to send for a given mode. "free" routes
  // to the LLM-backed model_adapter so the agent actually reasons over the
  // canvas instead of replaying deterministic single-action plans; "director"
  // stays on the deterministic structure planner for replayable multi-step
  // patterns (3-shot story / motion study / etc).
  const resolvePlannerName = (): string => {
    return plannerMode === "director" ? "structure" : "model_adapter";
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

  // v0.7 §D: approval-mode pill moved out of the composer; the Permission
  // segmented control in RightRailV3 is now the single source of truth, so
  // `policy` is no longer read here. (Was `const policy = project?.agent_policy;`.)
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
        planner: resolvePlannerName(),
      });
      if (plan.status === "blocked") {
        setPlanError(plan.reason || "Agent planner refused this request.");
        setPlannedCalls([]);
        // Carry the model's own explanation forward even on a blocked plan
        // so the chat surface can show it; deterministic planners leave
        // this null and the UI just shows the reason banner.
        setPlannedResponse(plan.response ?? null);
        setPlannedPlannerName(plan.planner ?? null);
      } else {
        setPlannedCalls(plan.tool_calls as AtelierAgentToolCallPayload[]);
        setPlannedResponse(plan.response ?? null);
        setPlannedPlannerName(plan.planner ?? null);
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
    setPlanError(null);
    try {
      // P (v0.9) — streaming fast-path for the LLM-backed planner. Only
      // triggers when the user is on Auto/Free mode (model_adapter) AND
      // has a fresh draft (no plannedCalls preview to replay AND not
      // preview-mode). All other paths (Director / preview / re-execute
      // of a previewed plan) fall through to the existing sync flow.
      const useStreamingFastPath =
        !preview
        && draft.trim().length > 0
        && plannedCalls.length === 0
        && resolvePlannerName() === "model_adapter";
      if (useStreamingFastPath) {
        const controller = new AbortController();
        streamAbortRef.current = controller;
        try {
          const turn = await runAgentTurnStreaming_P({
            user_message: draft,
            selected_node_id: selectedNodeId ?? null,
            signal: controller.signal,
          });
          if (turn) {
            pushToast?.("success", "Agent turn executed.");
            setDraft("");
            setPlannedCalls([]);
            setPlannedResponse(null);
            setPlannedPlannerName(null);
            setPlanError(null);
            void maybeWrapDirectorOutput(turn, draft);
          } else {
            // null = blocked / aborted / failed. Surface store error if any.
            const storeError = useAtelierStore.getState().error;
            if (storeError) {
              setPlanError(storeError);
            }
          }
        } finally {
          if (streamAbortRef.current === controller) {
            streamAbortRef.current = null;
          }
        }
        return;
      }

      let toolCallsToRun: AtelierAgentToolCallPayload[] = plannedCalls;
      let assistantResponse: string | null = plannedResponse;
      // v0.8 item L — plan-and-run shortcut. If the user typed and hit Run
      // without previewing, fire the planner now so we capture both the
      // tool_calls and (for the LLM-backed planner) the assistant response.
      // For preview-mode we skip this: preview turns are meant to render
      // the plan as a static row, not execute it.
      if (toolCallsToRun.length === 0 && draft.trim()) {
        const plan = await planAgentTurn({
          user_message: draft,
          selected_node_id: selectedNodeId ?? null,
          planner: resolvePlannerName(),
        });
        if (plan.status === "blocked") {
          const reason = plan.reason || "Agent planner refused this request.";
          setPlanError(reason);
          // Surface the LLM's own explanation in chat even when blocked,
          // so the user sees what the model said.
          if (plan.response && plan.response.trim()) {
            setPlannedResponse(plan.response);
            setPlannedPlannerName(plan.planner ?? null);
          }
          pushToast?.("error", reason);
          return;
        }
        toolCallsToRun = plan.tool_calls as AtelierAgentToolCallPayload[];
        assistantResponse = plan.response ?? null;
        setPlannedPlannerName(plan.planner ?? null);
      }
      const turn = await runAgentTurn({
        user_message: draft,
        preview,
        tool_calls: toolCallsToRun,
        assistant_response: assistantResponse ?? undefined,
      });
      if (!preview) {
        pushToast?.("success", "Agent turn executed.");
        setDraft("");
        setPlannedCalls([]);
        setPlannedResponse(null);
        setPlannedPlannerName(null);
        setPlanError(null);
        // G: trusted-policy direct execute path — wrap if director run
        // produced 2+ nodes.
        void maybeWrapDirectorOutput(turn, draft);
      } else {
        pushToast?.("info", "Preview submitted — see history below.");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Agent run failed";
      setPlanError(msg);
      pushToast?.("error", msg);
    } finally {
      setExecuting(false);
    }
  };

  // P (v0.9) — Stop the in-flight streaming agent turn. Aborts the fetch,
  // which propagates AbortError up through the store action so it can
  // clear streamingAgentTurn without persisting a half-applied turn.
  const handleStopStreaming = () => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
  };

  // P (v0.9) — clean up an in-flight stream on unmount so a panel
  // collapse / page navigation doesn't leak the fetch.
  useEffect(() => {
    return () => {
      streamAbortRef.current?.abort();
      streamAbortRef.current = null;
    };
  }, []);

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
          <>
            {/* Empty-state orb header (RHTV reference image #8). Big sage-green
                avatar + friendly greeting + caption — only shown before any
                agent_turns exist, so it never pushes the conversation down. */}
            <div className="flex flex-col items-center pt-1 pb-2 text-center">
              <div
                aria-hidden="true"
                className="relative grid h-14 w-14 place-items-center rounded-full"
                style={{
                  background:
                    "radial-gradient(circle at 50% 50%, rgba(61,220,132,0.55) 0%, rgba(61,220,132,0.32) 30%, rgba(61,220,132,0.08) 60%, transparent 70%)",
                  boxShadow:
                    "0 0 22px -4px rgba(61,220,132,0.35), inset 0 1px 0 0 rgba(255,255,255,0.12)",
                }}
              >
                <span
                  aria-hidden="true"
                  className="h-3 w-3 rounded-full bg-white/85 shadow-[0_0_10px_2px_rgba(255,255,255,0.45)]"
                />
              </div>
              <div className="mt-2.5 font-display text-[18px] text-foreground tracking-[-0.005em]">
                Hey — what should we make?
              </div>
              <div className="mt-1 text-[12px] text-white/45">
                Pick a node, ask anything, or drop a seed.
              </div>
            </div>

            {/* v0.7 §B — empty-state skill catalog. 2×4 grid of ghost buttons,
                each a soft entry point onto the agent's intended capabilities
                (matches the RHTV agent panel skill grid, scoped to video
                creation rather than RHTV's e-commerce slots). Cards are
                presentational; click dispatches through onSkillCardClick so
                seed-graph logic stays at the shell. */}
            <div
              role="list"
              aria-label="Agent skill catalog"
              className="grid grid-cols-2 gap-2"
            >
              {SKILL_CARDS.map((card) => {
                const Icon = card.Icon;
                return (
                  <button
                    key={card.id}
                    type="button"
                    role="listitem"
                    onClick={() => {
                      if (onSkillCardClick) onSkillCardClick(card.id);
                      else pushToast?.("info", `${card.title} coming soon`);
                    }}
                    className="group flex flex-col items-start gap-1.5 rounded-md px-3 py-2.5 text-left transition-colors hover:bg-white/[0.04] active:bg-white/[0.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-atelier-brand-400/45"
                  >
                    <div className="flex items-center gap-1.5">
                      <Icon
                        size={14}
                        aria-hidden="true"
                        className="text-atelier-port-positive/65 transition-colors group-hover:text-atelier-port-positive/85"
                      />
                      <span className="text-[12.5px] font-medium tracking-[-0.005em] text-foreground">
                        {card.title}
                      </span>
                    </div>
                    <span className="line-clamp-2 text-[11px] leading-[1.4] text-white/45">
                      {card.subtitle}
                    </span>
                  </button>
                );
              })}
            </div>
          </>
        ) : null}

        {recentTurns.map((turn) => (
          <AgentTurnRow key={turn.id} turn={turn} />
        ))}

        {/* P (v0.9) — live streaming agent bubble. Shows the user message
            the stream was kicked off with above the incremental response
            so the conversation reads coherently while tokens arrive.
            Cleared by the store once the persisted turn lands in
            agentTurns above. */}
        {streamingTurn ? (
          <div className="space-y-2">
            {draft.trim() ? (
              <ConversationUserBubble>{draft.trim()}</ConversationUserBubble>
            ) : null}
            <StreamingAgentBubble
              text={streamingTurn.response}
              done={streamingTurn.done}
            />
          </div>
        ) : null}

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
                <span className="text-[11px] text-atelier-brand-300">
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
                      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[11px] font-medium tracking-tight transition-all duration-200 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:scale-100 ${
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
                  className="rounded-md border border-white/10 bg-black/25 px-3 py-2 text-[11px] font-medium tracking-tight text-text-secondary/95 transition-all duration-150 hover:border-white/15 hover:bg-white/[0.06] hover:text-foreground active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Reject
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {/* Currently planning — show a thinking bubble. v0.8 item L expands
            this from "preview-only" to also cover the plan-and-run shortcut
            so the user sees an "agent is thinking" bubble while the LLM
            call is in-flight (Qwen typically 5-15s).
            P (v0.9) — suppressed when streamingTurn is active because the
            StreamingAgentBubble above already plays the "Thinking…" placeholder
            until the first delta arrives. */}
        {!streamingTurn
          && (previewing || (executing && plannedCalls.length === 0 && draft.trim())) ? (
          <ConversationAgentBubble thinking />
        ) : null}

        {/* Plan preview before execute */}
        {plannedCalls.length > 0 && !pendingTurn ? (
          <div className="overflow-hidden rounded-[10px] border border-white/8 bg-black/20 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)] motion-safe:animate-atelier-popover-in">
            <div aria-hidden="true" className="h-[1px] bg-gradient-to-r from-atelier-brand-soft/40 via-atelier-brand-soft/12 to-transparent" />
            <div className="px-3 py-2.5">
              <div className="mb-1.5 flex items-center justify-between gap-2 text-[11px]">
                <span className="flex items-center gap-1.5 text-atelier-brand-soft/85">
                  <Sparkles size={10} aria-hidden="true" />
                  {plannedPlannerName === "model_adapter" ? "Agent plan" : "Plan preview"}
                </span>
                <span className="text-text-muted/85">{plannedCalls.length} step{plannedCalls.length === 1 ? "" : "s"}</span>
              </div>
              {/* v0.8 item L — when the LLM-backed planner emits a `response`
                  string, render it above the tool-call list so the user
                  sees the assistant's own framing before the action chips.
                  Empty / deterministic plans render the chips alone. */}
              {plannedResponse && plannedResponse.trim() ? (
                <p
                  className="mb-1.5 border-l border-atelier-brand-soft/25 pl-2 text-[12px] leading-[1.5] text-foreground/85"
                  style={{ fontFamily: "'Inter', sans-serif" }}
                >
                  {plannedResponse.trim()}
                </p>
              ) : null}
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

        {/* v0.8 item L — when the LLM-backed planner returned a `response`
            but no tool_calls (model declined or asked for more context),
            show its explanation as a standalone agent bubble so the user
            isn't left staring at an empty inline planner banner. */}
        {plannedResponse && plannedCalls.length === 0 && !pendingTurn && !previewing ? (
          <ConversationAgentBubble>
            <span style={{ fontFamily: "'Inter', sans-serif" }}>{plannedResponse.trim()}</span>
          </ConversationAgentBubble>
        ) : null}

        {planError ? (
          <div role="alert" className="rounded-md border border-atelier-failed/35 bg-atelier-failed/[0.06] px-3 py-2 text-[12px] leading-[1.5] text-foreground/95 motion-safe:animate-atelier-popover-in">
            <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-atelier-failed">
              <span>Agent blocked</span>
              {/* v0.8 item L — retry chip when the LLM failed (network /
                  rate-limit / parse error). Re-runs the same draft against
                  the current planner mode so the user doesn't have to
                  retype. Hidden when no draft remains. */}
              {draft.trim() && !isLocked && !executing && !previewing ? (
                <button
                  type="button"
                  onClick={() => void handleExecute(false)}
                  className="rounded border border-atelier-failed/35 bg-atelier-failed/[0.08] px-1.5 py-[1px] text-[10.5px] font-medium text-atelier-failed/95 transition-colors hover:bg-atelier-failed/15"
                >
                  Retry
                </button>
              ) : null}
            </div>
            {planError}
          </div>
        ) : null}
      </div>

      {/* Composer — v0.6.2: dropped the heavy top hairline separator;
          the composer flows directly out of the message history without
          a chrome boundary. */}
      <div className="px-3 pb-3 pt-2.5">
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
            <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-atelier-brand-soft/85">
              <span aria-hidden="true" className="h-[5px] w-[5px] shrink-0 rounded-full bg-atelier-brand-soft/70" />
              <span className="truncate">Reading · {label}</span>
            </div>
          );
        })()}
        {/* v0.6.2 — dropped the bordered card recipe around the composer
            (the bottom-right card the user red-boxed). The textarea now
            sits directly on the agent panel's frosted bg; only the
            internal toolbar carries a hairline separator since textarea +
            tools are different affordance groups. */}
        <div className="overflow-hidden">
          <textarea
            rows={2}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              if (plannedCalls.length > 0) setPlannedCalls([]);
              if (plannedResponse) setPlannedResponse(null);
              if (plannedPlannerName) setPlannedPlannerName(null);
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
          {/* v0.7 §D — composer toolbar cleanup. Internal-feeling chip row
              replaced with a polished cluster: + (attach) / @ (mention)
              ghost icon buttons on the left; AUTO/PLAN toggle + Preview +
              green Run pill on the right. The approval-mode status pill
              moved out — it's already visible in the Permission segmented
              control at the top of the rail, so duplicating it here read
              as receipt-vocabulary. */}
          <div className="flex items-center justify-between gap-2 px-2 py-1.5">
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="btn-tip inline-flex h-7 w-7 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-white/[0.05] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                data-tip="Attach context"
                aria-label="Attach context"
                disabled
              >
                <Paperclip size={12} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="btn-tip inline-flex h-7 w-7 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-white/[0.05] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                data-tip="Mention a node"
                aria-label="Mention a node"
                disabled
              >
                <AtSign size={12} aria-hidden="true" />
              </button>
            </div>
            <div className="flex items-center gap-1.5">
              {/* AUTO / PLAN toggle. Single button cycles between the
                  existing planner modes — Free (= Auto, deterministic
                  single-action) and Director (= Plan, structured
                  multi-step). LeftRail's Director chip and this control
                  stay synced through the existing `atelier-v3-planner-
                  mode-changed` custom event + localStorage. */}
              <button
                type="button"
                aria-pressed={plannerMode === "director"}
                aria-label={
                  plannerMode === "director"
                    ? "Plan mode — deterministic structured templates. Click to switch to Auto (LLM)."
                    : "Auto mode — model-backed agent reasoning. Click to switch to Plan (structured templates)."
                }
                data-tip={
                  plannerMode === "director"
                    ? "Plan · structured templates"
                    : "Auto · model-backed agent"
                }
                onClick={() =>
                  setPlannerModePersist(plannerMode === "director" ? "free" : "director")
                }
                disabled={isLocked}
                className={`btn-tip inline-flex h-7 items-center gap-1 rounded-md px-2 text-[10.5px] font-medium uppercase tracking-[0.06em] transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                  plannerMode === "director"
                    ? "bg-atelier-brand-400/15 text-[#6e8fff] hover:bg-atelier-brand-400/22"
                    : "text-text-muted hover:bg-white/[0.05] hover:text-foreground"
                }`}
              >
                {plannerMode === "director" ? (
                  <Wand2 size={10} aria-hidden="true" />
                ) : (
                  <Zap size={10} aria-hidden="true" />
                )}
                {plannerMode === "director" ? "Plan" : "Auto"}
              </button>
              <button
                type="button"
                disabled={isLocked || previewing || !hasDraft}
                onClick={handlePreview}
                className="inline-flex h-7 items-center gap-1 rounded-md px-2.5 text-[11px] font-medium tracking-tight text-text-muted transition-colors hover:bg-white/[0.05] hover:text-foreground active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {previewing ? (
                  <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                ) : null}
                Preview
              </button>
              {/* Primary green Run pill (was "Execute" with the editorial
                  italic + → recipe). v0.7 §D pivots the affordance to the
                  Flova Generate-CTA green so primary action carries the
                  same chroma as the canvas's primary-port + generate
                  buttons — one green for "commit & spend" across the
                  product. */}
              {/* P (v0.9) — Stop pill replaces Run while a stream is in
                  flight (streamingTurn?.done === false). Click aborts the
                  underlying fetch; the store catches AbortError and clears
                  streamingAgentTurn without persisting a half-applied turn. */}
              {streamingTurn && !streamingTurn.done ? (
                <button
                  type="button"
                  onClick={handleStopStreaming}
                  aria-label="Stop streaming response"
                  className="inline-flex h-7 items-center gap-1.5 rounded-full border border-atelier-failed/40 bg-atelier-failed/10 px-3.5 text-[11.5px] font-medium text-atelier-failed shadow-[inset_0_1px_0_0_rgba(248,113,113,0.18)] transition-all duration-150 hover:bg-atelier-failed/15 active:scale-[0.97]"
                >
                  <Square size={10} aria-hidden="true" />
                  Stop
                </button>
              ) : (
                <button
                  type="button"
                  disabled={isLocked || (!hasDraft && plannedCalls.length === 0)}
                  onClick={() => handleExecute(false)}
                  className={`inline-flex h-7 items-center gap-1.5 rounded-full bg-atelier-port-positive/95 px-3.5 text-[11.5px] font-medium text-[#0c1a10] shadow-[inset_0_1px_0_0_rgba(255,255,255,0.25),0_4px_14px_-6px_rgba(61,220,132,0.55)] transition-all duration-150 hover:bg-atelier-port-positive hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.30),0_6px_18px_-6px_rgba(61,220,132,0.7)] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:scale-100 ${
                    hasDraft && !isLocked && !executing
                      ? "motion-safe:animate-atelier-pulse-soft"
                      : ""
                  }`}
                >
                  {executing ? (
                    <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                  ) : null}
                  Run
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
