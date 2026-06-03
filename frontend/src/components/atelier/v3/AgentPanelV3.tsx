"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AtSign,
  Bot,
  Check,
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
import { useAtelierStore, type ToolProgress } from "@/store/atelierStore";
import type {
  AtelierAgentIterationRecord,
  AtelierAgentModelOption,
  AtelierAgentTurn,
  AtelierAgentToolCallPayload,
} from "@/lib/api";
import { estimateCostUSD, formatCostUSD } from "@/lib/agentPricing";

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
//
// v1.0 track T — appends a tool-progress timeline below the bubble text
// once the harness starts emitting tool_start frames. Each chip flips
// from a spinner to a check / x when the matching tool_done lands.
function StreamingAgentBubble({
  text,
  done,
  toolProgress,
}: {
  text: string;
  done: boolean;
  toolProgress?: ToolProgress[];
}) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-atelier-brand-soft/15 text-atelier-brand-soft ring-1 ring-inset ring-atelier-brand-soft/25">
        <Bot size={13} aria-hidden="true" />
      </span>
      <div className="flex-1">
        <div
          className="rounded-[14px] rounded-tl-[6px] border border-white/6 bg-black/25 px-3 py-2 text-[13px] leading-[1.55] text-foreground/95 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.03)]"
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
        {toolProgress && toolProgress.length > 0 ? (
          <ToolProgressTimeline progress={toolProgress} />
        ) : null}
      </div>
    </div>
  );
}

// v1.0 track T — tool-progress chip rail rendered under the streaming
// bubble. Maps the harness tool_name to a human verb (Updating prompt,
// Generating takes, …) so the user reads a sentence instead of an api
// id. Status icon follows the harness's terminal status: running shows
// a spinner, completed flips to a green check, failed flips to a red X
// with the executor's error message exposed via a hover tooltip.
function toolProgressVerb(toolName: string): string {
  switch (toolName) {
    case "canvas.createVideoNode":
      return "Creating video draft";
    case "canvas.attachReferenceNode":
      return "Attaching reference";
    case "canvas.updateNodePrompt":
      return "Updating prompt";
    case "canvas.createReferenceImageNode":
      return "Adding reference image";
    case "canvas.readProject":
      return "Reading canvas";
    case "generation.createVideoCandidates":
      return "Generating takes";
    default:
      return toolName;
  }
}

function ToolProgressTimeline({ progress }: { progress: ToolProgress[] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {progress.map((entry) => {
        const verb = toolProgressVerb(entry.tool_name);
        const isRunning = entry.status === "running";
        const isCompleted = entry.status === "completed";
        const isFailed = entry.status === "failed";
        // v1.1 X — attempt badge. `attempt ≥ 2` means the chip is showing
        // a retry; we surface it as a small "·2/3" suffix so the user
        // knows the harness already retried once. `retriable: true`
        // means this is an interim failure that another attempt will
        // follow — render as a yellow "Retrying" pill instead of a
        // hard X.
        const attempt = entry.attempt ?? 1;
        const showAttempt = attempt > 1;
        const isRetriableFailure = isFailed && entry.retriable === true;
        return (
          <span
            key={entry.call_id}
            className="inline-flex items-center gap-1.5 rounded-full bg-white/[0.04] px-2 py-1 text-[11px] text-foreground/80 animate-atelier-popover-in"
            title={
              isFailed
                ? `${entry.error || "Tool call failed"}${
                    showAttempt ? ` (attempt ${attempt})` : ""
                  }`
                : showAttempt
                  ? `Attempt ${attempt}`
                  : undefined
            }
          >
            {isRunning ? (
              <Loader2
                size={11}
                className="animate-spin text-atelier-port-positive/85"
                aria-hidden="true"
              />
            ) : isCompleted ? (
              <Check
                size={11}
                className="text-atelier-port-positive"
                aria-hidden="true"
              />
            ) : isRetriableFailure ? (
              <Loader2
                size={11}
                className="animate-spin text-amber-300/85"
                aria-hidden="true"
              />
            ) : isFailed ? (
              <X size={11} className="text-red-300" aria-hidden="true" />
            ) : (
              <Sparkles
                size={11}
                className="text-foreground/55"
                aria-hidden="true"
              />
            )}
            <span>{isRetriableFailure ? `Retrying ${verb.toLowerCase()}` : verb}</span>
            {showAttempt ? (
              <span
                aria-label={`attempt ${attempt} of 3`}
                className="ml-0.5 rounded-sm bg-white/[0.08] px-1 font-mono text-[9.5px] tracking-tight text-foreground/65"
              >
                {attempt}/3
              </span>
            ) : null}
          </span>
        );
      })}
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

// v1.4 Batch 4 — humanize an IterationRecord into a single caption line:
//   "qwen-plus · 1240 in / 380 out · 1.4s · ≈ $0.001"
// Tokens fall back to "—" when usage didn't arrive (legacy LLMAdapter
// path on the streaming planner doesn't surface it). Cost uses the
// hard-coded V1_4 price table; unknown models render "—".
function formatIterationCaption(rec: AtelierAgentIterationRecord): string {
  const tokensCaption =
    rec.prompt_tokens || rec.completion_tokens
      ? `${rec.prompt_tokens} in / ${rec.completion_tokens} out`
      : "tokens —";
  const latencyCaption =
    rec.latency_ms > 0
      ? `${(rec.latency_ms / 1000).toFixed(1)}s`
      : "—";
  const cost = estimateCostUSD(
    rec.model_id,
    rec.prompt_tokens,
    rec.completion_tokens,
  );
  return `${rec.model_id || "model —"} · ${tokensCaption} · ${latencyCaption} · ${formatCostUSD(cost)}`;
}

function aggregateTurnUsage(rows: AtelierAgentIterationRecord[]) {
  let prompt = 0;
  let completion = 0;
  let latency = 0;
  let cost = 0;
  for (const r of rows) {
    prompt += r.prompt_tokens || 0;
    completion += r.completion_tokens || 0;
    latency += r.latency_ms || 0;
    cost += estimateCostUSD(r.model_id, r.prompt_tokens, r.completion_tokens);
  }
  return { prompt, completion, latency, cost };
}

// v1.4 Batch 4 — model-pill subcomponent. Renders a compact "<provider> ·
// <short_model>" pill in the composer footer; click opens an inline
// dropdown listing available ProviderConfigs grouped by provider with a
// check icon on the active row. Click outside closes; Esc cancels.
// Disabled rows render dimmed with the missing key_env name in the title
// attribute (acts as a tooltip without pulling in an extra Radix import).
//
// The pill width is unconstrained but caps via tabular-num + max-w so a
// long model id like "claude-3-5-sonnet-20241022" doesn't push the AUTO
// toggle off-screen. Width matches the height-7 sibling cluster so it
// reads as a peer of AUTO/PLAN, not a header band.
function shortModelLabel(modelId: string): string {
  // Drop common provider prefixes for compactness on the pill face.
  // Matches the GET /atelier/agent/models `model_id` slugs.
  if (modelId.startsWith("qwen-")) return modelId.slice(5);
  if (modelId.startsWith("gpt-")) return modelId.slice(4);
  if (modelId.startsWith("claude-3-5-")) return modelId.slice(11).split("-")[0];
  return modelId;
}

interface ModelPillProps {
  provider: string;
  modelId: string;
  options: AtelierAgentModelOption[];
  onSelect: (provider: string, modelId: string) => void;
  disabled?: boolean;
  openSignal?: number;
}

function ModelPill({
  provider,
  modelId,
  options,
  onSelect,
  disabled,
  openSignal,
}: ModelPillProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // External open trigger (for /model with no arg). Listening to a number
  // signal lets the parent force-open without exposing imperative refs.
  useEffect(() => {
    if (openSignal && openSignal > 0) {
      setOpen(true);
    }
  }, [openSignal]);
  useEffect(() => {
    if (!open) return;
    const onDocClick = (ev: MouseEvent) => {
      if (
        containerRef.current
        && !containerRef.current.contains(ev.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDocClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDocClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  // Group options by provider preserving registry order.
  const grouped = useMemo(() => {
    const groups = new Map<string, AtelierAgentModelOption[]>();
    for (const opt of options) {
      const arr = groups.get(opt.provider) ?? [];
      arr.push(opt);
      groups.set(opt.provider, arr);
    }
    return Array.from(groups.entries());
  }, [options]);
  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Active model: ${provider} ${modelId}. Click to change.`}
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className="btn-tip inline-flex h-7 max-w-[140px] items-center gap-1 rounded-md bg-white/[0.04] px-2 text-[10.5px] font-medium tracking-tight text-text-muted ring-1 ring-inset ring-white/8 transition-colors hover:bg-white/[0.06] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        data-tip={`${provider} · ${modelId}`}
      >
        <span className="truncate font-mono text-[10.5px]">
          {provider} · {shortModelLabel(modelId)}
        </span>
      </button>
      {open ? (
        <div
          role="listbox"
          aria-label="Atelier agent models"
          className="absolute bottom-[calc(100%+6px)] right-0 z-30 w-[260px] overflow-hidden rounded-md border border-white/10 bg-[rgba(10,12,18,0.95)] shadow-xl backdrop-blur"
        >
          <div className="max-h-72 overflow-y-auto py-1.5">
            {grouped.length === 0 ? (
              <div className="px-3 py-2 text-[11px] text-white/45">
                No models available.
              </div>
            ) : (
              grouped.map(([providerName, rows]) => (
                <div key={providerName}>
                  <div className="px-3 py-1 text-[9.5px] font-semibold uppercase tracking-[0.08em] text-white/35">
                    {providerName}
                  </div>
                  {rows.map((opt) => {
                    const isActive =
                      opt.provider === provider && opt.model_id === modelId;
                    const tooltip = opt.configured
                      ? opt.fallback_model
                        ? `Falls back to ${opt.fallback_model} on rate-limit/timeout`
                        : opt.label
                      : `Set ${opt.key_env} to enable`;
                    return (
                      <button
                        type="button"
                        role="option"
                        aria-selected={isActive}
                        key={`${opt.provider}-${opt.model_id}`}
                        disabled={!opt.configured}
                        title={tooltip}
                        onClick={() => {
                          if (!opt.configured) return;
                          onSelect(opt.provider, opt.model_id);
                          setOpen(false);
                        }}
                        className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-[11.5px] transition-colors ${
                          isActive
                            ? "bg-white/[0.06] text-foreground"
                            : "text-text-secondary hover:bg-white/[0.04] hover:text-foreground"
                        } ${opt.configured ? "" : "cursor-not-allowed opacity-50"}`}
                      >
                        <span className="truncate">{opt.label}</span>
                        {isActive ? (
                          <Check size={11} aria-hidden="true" className="shrink-0 text-atelier-port-positive" />
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
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
  const iterations = turn.iterations ?? [];
  const aggregate = iterations.length > 0 ? aggregateTurnUsage(iterations) : null;
  const aggregateTooltip = aggregate
    ? `${iterations.length} round${iterations.length === 1 ? "" : "s"} · ${
        aggregate.prompt + aggregate.completion
      } tokens · ${formatCostUSD(aggregate.cost)} · ${(aggregate.latency / 1000).toFixed(1)}s`
    : "";
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
          <div
            className="text-[11px] text-white/40"
            title={aggregateTooltip || undefined}
          >
            {statusCaption} · {time}
            {aggregate ? (
              <span className="ml-1.5 text-white/35">
                · {iterations.length} round{iterations.length === 1 ? "" : "s"} · {formatCostUSD(aggregate.cost)}
              </span>
            ) : null}
          </div>
          {iterations.length > 0 ? (
            <ul
              className="space-y-0.5 text-[10.5px] leading-[1.4] text-white/35"
              aria-label="Per-iteration LLM usage"
            >
              {iterations.map((rec) => (
                <li key={`${turn.id}-iter-${rec.idx}`} className="font-mono tracking-tight">
                  #{rec.idx} {formatIterationCaption(rec)}
                </li>
              ))}
            </ul>
          ) : null}
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
  // v1.1 X — preview-then-execute slice + actions. Populated when the
  // multi-step loop emits a `tool_plan` event under untrusted policy;
  // resolved by the user via Approve (continueAgentTurn_X) or Reject
  // (cancelAgentTurn_X). When non-null, the Preview card replaces the
  // existing approval card in the panel layout.
  const agentPreview_X     = useAtelierStore((s) => s.agentPreview_X);
  const agentPreviewBusy_X = useAtelierStore((s) => s.agentPreviewBusy_X);
  const continueAgentTurn_X = useAtelierStore((s) => s.continueAgentTurn_X);
  const cancelAgentTurn_X   = useAtelierStore((s) => s.cancelAgentTurn_X);
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

  // v1.4 Batch 3 — warm the skill registry on first mount so
  // dispatchSkillTurn_B3 has data ready before the user clicks. Soft-fail:
  // if the catalog endpoint is unavailable, the empty-state still renders
  // the legacy hardcoded card list (the store falls back gracefully).
  useEffect(() => {
    void useAtelierStore.getState().loadSkills_B3().catch(() => {
      /* swallow — empty-state cards still render */
    });
    // v1.4 Batch 4 — also warm the model catalog so the model-pill popover
    // has rows ready before the user clicks. Same soft-fail pattern: if the
    // route is unavailable, the popover renders only the active row.
    void useAtelierStore.getState().loadAgentModels_B4().catch(() => {
      /* swallow — pill still renders the active selection */
    });
  }, []);
  // v1.4 Batch 4 — model-pill state. Reads the persisted active model from
  // the store and writes selections back via setAgentModel_B4.
  const agentModel = useAtelierStore((s) => s.agentModel_B4);
  const agentModelOptions = useAtelierStore((s) => s.agentModelOptions_B4);
  const setAgentModel = useAtelierStore((s) => s.setAgentModel_B4);
  const [modelPillOpenSignal, setModelPillOpenSignal] = useState(0);
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
    // v1.4 Batch 4 — `/model <provider>:<model_id>` or `/model <model_id>`
    // slash command. Parsed BEFORE dispatch so the next manually-typed turn
    // picks up the new model. Bare `/model` opens the pill popover.
    const draftTrim = draft.trim();
    if (draftTrim.startsWith("/model")) {
      const arg = draftTrim.slice("/model".length).trim();
      if (!arg) {
        // No arg — open the popover (Cursor-like behavior).
        setModelPillOpenSignal(Date.now());
        setDraft("");
        return;
      }
      let nextProvider: string | null = null;
      let nextModel: string | null = null;
      if (arg.includes(":")) {
        const [p, m] = arg.split(":", 2);
        nextProvider = p.trim() || null;
        nextModel = m.trim() || null;
      } else {
        nextModel = arg;
      }
      // Resolve via the loaded options when present so unknown models
      // (typos) don't silently set bad state. Fall back to {dashscope, arg}
      // when the catalog hasn't loaded yet.
      const matched = agentModelOptions.find((o) => {
        if (nextProvider && nextModel) {
          return o.provider === nextProvider && o.model_id === nextModel;
        }
        return o.model_id === nextModel;
      });
      if (matched) {
        setAgentModel(matched.provider, matched.model_id);
        pushToast?.(
          "info",
          `Model set to ${matched.provider} · ${matched.model_id}`,
        );
      } else if (nextModel) {
        const provider = nextProvider || agentModel.provider;
        setAgentModel(provider, nextModel);
        pushToast?.("info", `Model set to ${provider} · ${nextModel}`);
      } else {
        pushToast?.("error", "Usage: /model <provider>:<model_id> or /model <model_id>");
      }
      setDraft("");
      return;
    }
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
                    onClick={async () => {
                      // v1.4 Batch 3 — empty-state card now dispatches a
                      // real agent turn via dispatchSkillTurn_B3. The
                      // store resolves the SkillSpec, validates
                      // `requires_inputs` (toasts a nudge when a
                      // selected_node is required but missing), and
                      // kicks runAgentTurnLoop_X with skill_name set so
                      // the backend splices the brief into the prompt.
                      // The legacy onSkillCardClick path still wins when
                      // a parent passes one, so storybook / standalone
                      // contexts can opt out without dispatching real
                      // turns.
                      if (onSkillCardClick) {
                        onSkillCardClick(card.id);
                        return;
                      }
                      try {
                        await useAtelierStore
                          .getState()
                          .dispatchSkillTurn_B3({
                            skill_id: card.id,
                            selected_node_id: selectedNodeId ?? null,
                          });
                      } catch (err) {
                        pushToast?.(
                          "error",
                          err instanceof Error
                            ? err.message
                            : `${card.title} failed to start`,
                        );
                      }
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

        {/* v1.3 BATCH 2 (2b) — small caption surfaced above the turn
            timeline when the backend has folded older turns into a
            rolling compaction summary. Non-load-bearing UI cue: the
            actual summary is system-side and consumed by the LLM. */}
        {project?.agent_compaction_summary ? (
          <div
            className="flex items-center gap-1.5 px-1 pb-1 text-[11px] text-white/40 animate-atelier-popover-in"
            title={project.agent_compaction_summary}
            aria-label="Earlier turns compacted into a rolling summary"
          >
            <span
              aria-hidden="true"
              className="h-1 w-1 rounded-full bg-white/30"
            />
            Earlier turns compacted
          </div>
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
              toolProgress={streamingTurn.tool_progress}
            />
          </div>
        ) : null}

        {/* v1.1 X — Preview-then-execute card. Rendered when the
            multi-step loop emitted a `tool_plan` event under untrusted
            policy and paused at `awaiting_approval`. Approve dispatches
            continueAgentTurn_X (resumes via the existing approval flow);
            Reject dispatches cancelAgentTurn_X (terminal `canceled`).
            Distinct from the pendingTurn approval card below — that one
            lists per-call approval chips; this one is the single planned
            iteration summary the spec asks for. */}
        {agentPreview_X && project ? (
          <div
            role="alertdialog"
            aria-label="Agent plan preview — Approve or Reject"
            className="overflow-hidden rounded-[12px] border border-amber-300/35 bg-amber-300/[0.04] shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(245,200,80,0.08)] motion-safe:animate-atelier-popover-in"
          >
            <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-amber-300/85 via-amber-300/30 to-transparent" />
            <div className="px-3.5 pb-3 pt-3">
              <div className="mb-2 flex items-center gap-1.5">
                <Sparkles size={12} className="text-amber-300" aria-hidden="true" />
                <span className="text-[11px] text-amber-300">
                  Preview — step {agentPreview_X.iteration}
                </span>
              </div>
              <p className="mb-2.5 text-[12px] leading-[1.5] text-text-secondary/95">
                The agent wants to run the following before continuing.
                Approve to execute, or Reject to dismiss this turn.
              </p>
              <ul className="mb-3 space-y-1 border-l border-amber-300/20 pl-2.5 text-[13px] leading-[1.5] text-foreground/95">
                {agentPreview_X.toolCalls.map((c, i) => (
                  <li key={`${c.tool_name}-${i}`} className="flex items-start gap-1.5">
                    <Sparkles
                      size={10}
                      className="mt-[5px] shrink-0 text-amber-300/80"
                      aria-hidden="true"
                    />
                    <div className="min-w-0 flex-1">
                      <span className="text-foreground/95">{summarizeToolCall(c)}</span>
                      <ToolCallParams call={c} />
                    </div>
                  </li>
                ))}
              </ul>
              <div className="grid grid-cols-2 gap-2">
                <button
                  disabled={agentPreviewBusy_X}
                  onClick={async () => {
                    try {
                      await continueAgentTurn_X(project.id, agentPreview_X.turnId);
                      pushToast?.("success", "Agent resumed.");
                    } catch (err) {
                      pushToast?.(
                        "error",
                        err instanceof Error ? err.message : "Continue failed",
                      );
                    }
                  }}
                  className="inline-flex items-center justify-center gap-1.5 rounded-md bg-amber-400/85 px-3 py-2 text-[11px] font-medium tracking-tight text-black/85 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_12px_-4px_rgba(245,200,80,0.5)] transition-all duration-200 hover:bg-amber-400 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {agentPreviewBusy_X ? (
                    <>
                      <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                      Resuming
                    </>
                  ) : (
                    "Approve & continue"
                  )}
                </button>
                <button
                  disabled={agentPreviewBusy_X}
                  onClick={async () => {
                    try {
                      await cancelAgentTurn_X(project.id, agentPreview_X.turnId);
                      pushToast?.("info", "Agent turn canceled.");
                    } catch (err) {
                      pushToast?.(
                        "error",
                        err instanceof Error ? err.message : "Cancel failed",
                      );
                    }
                  }}
                  className="rounded-md border border-white/10 bg-black/25 px-3 py-2 text-[11px] font-medium tracking-tight text-text-secondary/95 transition-all duration-150 hover:border-white/15 hover:bg-white/[0.06] hover:text-foreground active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Reject
                </button>
              </div>
            </div>
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
              {/* v1.4 Batch 4 — model-pill. Shows the active provider+model
                  and opens a grouped popover on click. Always rendered
                  (even on empty-state) so the user can pick a model before
                  typing the first message. */}
              <ModelPill
                provider={agentModel.provider}
                modelId={agentModel.model_id}
                options={agentModelOptions}
                onSelect={(p, m) => setAgentModel(p, m)}
                disabled={isLocked}
                openSignal={modelPillOpenSignal}
              />
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
