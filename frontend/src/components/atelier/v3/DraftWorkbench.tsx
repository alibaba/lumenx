"use client";
//
// DraftWorkbench — RHTV / LibTV pattern. Selecting a video draft
// expands its node *in place* into a full generation workbench instead
// of summoning a floating Composer below it. The user no longer hunts
// for "where did the panel pop up?" because there's nothing to hunt:
// the node IS the panel.
//
// Layout (top to bottom):
//   - Title row: sparkle + intent (rename on dblclick), trailing TAKE
//     status pill, awaiting-approval halo dot
//   - Inline Composer (mode="inline") providing prompt / refs / params /
//     advanced popover / generate button. The Composer keeps every one
//     of its features; the only difference from floating mode is no
//     absolute positioning, no shadow, no animation, no fixed 520 width.
//
// Implementation notes:
//   - Width fixed at 480 — wider than the compact DraftNode (244) so
//     prompt + ref slots breathe, narrower than the old floating
//     Composer (520) so two workbenches fit side-by-side at moderate
//     zoom levels.
//   - Sits inside the canvas world transform, so it scales with zoom.
//   - The shell renders this when (selectedNode is a draft); otherwise
//     the compact DraftNode renders.
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Sparkles } from "lucide-react";
import {
  Composer,
  type ComposerMentionable,
  type ComposerRef,
  type ComposerSubmitPayload,
  type ComposerTab,
} from "./Composer";
import type { DraftNodeStatus } from "./DraftNode";
import { TearLine } from "./ornaments";
import { TakeTimeline, type TakeTimelineEntry } from "./TakeTimeline";

interface Props {
  id: string;
  status: DraftNodeStatus;
  intent: string;
  modelLabel: string;
  configSummary: string;
  candidatesReady?: number;
  candidatesTotal?: number;
  selected?: boolean;
  x: number;
  y: number;
  // Composer-specific props (mirrored from the old floating Composer
  // call site so the shell wiring is mostly unchanged — just point it
  // at this component).
  activeTab?: ComposerTab;
  onTabChange?: (tab: ComposerTab) => void;
  prompt?: string;
  refs?: ComposerRef[];
  modelOptions?: string[];
  aspectOptions?: string[];
  durationOptions?: string[];
  countOptions?: string[];
  aspect?: string;
  duration?: string;
  count?: string;
  onSelect?: (id: string) => void;
  onIntentCommit?: (next: string) => void;
  onSubmit?: (payload: ComposerSubmitPayload) => void;
  onAddRef?: () => void;
  onRemoveRef?: (idx: number) => void;
  onPromptCommit?: (next: string) => void;
  mentionables?: ComposerMentionable[];
  /** Set when the shell detects an upstream image reference whose
   *  updated_at is newer than this draft's last successful run. v1
   *  surfaces this as a small amber chip so the user knows their
   *  current take may not reflect the latest reference state. */
  staleRefCount?: number;
  /** I (B-α / take version timeline): every candidate take generated
   *  from this draft, in any order — the timeline sorts internally.
   *  When non-empty, a horizontal take strip renders between the
   *  Composer and the status footer. */
  takes?: TakeTimelineEntry[];
  /** Callback when the user clicks a take in the strip. Caller should
   *  promote that take to "primary" (e.g. store.selectCandidate). */
  onPickTake?: (takeId: string) => void;
}

export function DraftWorkbench({
  id,
  status,
  intent,
  modelLabel,
  configSummary,
  candidatesReady,
  candidatesTotal,
  selected,
  x,
  y,
  activeTab,
  onTabChange,
  prompt,
  refs,
  modelOptions,
  aspectOptions,
  durationOptions,
  countOptions,
  aspect,
  duration,
  count,
  onSelect,
  onIntentCommit,
  onSubmit,
  onAddRef,
  onRemoveRef,
  onPromptCommit,
  mentionables,
  staleRefCount = 0,
  takes,
  onPickTake,
}: Props) {
  // Title row (rename) — same affordance as DraftNode so the muscle
  // memory carries over. Double-click to rename, Enter commits, Esc
  // reverts. Pointer-down stops propagation so the canvas marquee /
  // node drag don't fire while the user is typing in the field.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(intent);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);
  const startEditing = () => {
    if (!onIntentCommit) return;
    setDraft(intent);
    setEditing(true);
  };
  const commit = () => {
    setEditing(false);
    if (!onIntentCommit) return;
    const next = draft.trim();
    if (next && next !== intent) onIntentCommit(next);
  };

  const borderClass = selected
    ? "ring-2 ring-primary border-primary/55"
    : "border-glass-border";

  // Status footer caption — same vocabulary as the compact DraftNode so
  // the lifecycle reads identically whether the node is collapsed or
  // expanded.
  const statusMap: Record<DraftNodeStatus, { label: string; tone: "amber" | "blue" | "primary" | "emerald" }> = {
    draft:     { label: "Awaiting approval", tone: "amber" },
    running:   { label: "Generating takes",  tone: "blue" },
    approved:  { label: "Approved",          tone: "primary" },
    completed: { label: "Take selected",     tone: "emerald" },
  };
  const m = statusMap[status];

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Draft workbench: ${intent}`}
      onPointerDown={(event) => {
        event.stopPropagation();
        onSelect?.(id);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect?.(id);
        }
      }}
      style={{
        transform: `translate(${x}px, ${y}px)`,
        backgroundImage:
          "linear-gradient(to bottom, rgba(255,255,255,0.018) 0%, rgba(255,255,255,0) 12%)",
      }}
      className={`group absolute w-[480px] overflow-hidden rounded-[14px] border bg-[#141416] shadow-[0_24px_48px_-22px_rgba(0,0,0,0.8),0_4px_14px_-4px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.06)] transition-shadow duration-200 ${borderClass}`}
    >
      {/* Title row — sparkle + intent (rename on dblclick) + take pill */}
      <div className="flex items-center gap-1.5 px-4 pb-2 pt-3 text-foreground">
        <Sparkles size={11} className="shrink-0 text-primary" aria-hidden="true" />
        {editing ? (
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onPointerDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commit();
              } else if (e.key === "Escape") {
                e.preventDefault();
                setEditing(false);
                setDraft(intent);
              }
            }}
            className="min-w-0 flex-1 rounded border border-primary/60 bg-input-bg px-1 font-display text-[14px] font-medium tracking-[-0.005em] text-foreground outline-none"
            aria-label="Rename draft"
          />
        ) : (
          <span
            className={`min-w-0 flex-1 truncate font-display text-[14px] font-medium tracking-[-0.005em] ${onIntentCommit ? "cursor-text" : ""}`}
            onDoubleClick={(e) => {
              e.stopPropagation();
              startEditing();
            }}
            title={onIntentCommit ? "Double-click to rename" : undefined}
          >
            {intent}
          </span>
        )}
        {staleRefCount > 0 ? (
          <span
            role="status"
            aria-label={`${staleRefCount} reference${staleRefCount === 1 ? "" : "s"} updated since last run`}
            data-tip="Reference updated since last run"
            className="btn-tip ml-auto inline-flex shrink-0 items-center gap-1 rounded-[3px] border border-dashed border-amber-300/45 bg-amber-400/10 px-1.5 py-[2px] font-mono text-[8.5px] font-medium uppercase tracking-[0.22em] text-amber-100"
          >
            <AlertTriangle size={9} aria-hidden="true" />
            Stale ref · {staleRefCount}
          </span>
        ) : null}
        {typeof candidatesTotal === "number" && candidatesTotal > 0 ? (
          <span
            className={`ml-auto inline-flex shrink-0 items-center gap-1 rounded-[3px] border border-dashed px-1.5 py-[2px] font-mono text-[8.5px] font-medium uppercase tracking-[0.22em] ${
              (candidatesReady ?? 0) >= candidatesTotal
                ? "border-emerald-300/35 text-emerald-200/95"
                : "border-blue-300/35 text-blue-200/95"
            }`}
            aria-label={`${candidatesReady ?? 0} of ${candidatesTotal} candidates ready`}
          >
            <span>Take</span>
            <span className="font-display text-[10px] tracking-tight">
              {candidatesReady ?? 0}/{candidatesTotal}
            </span>
          </span>
        ) : null}
        {status === "running" ? (
          <Loader2 size={11} className="shrink-0 animate-spin text-blue-200" />
        ) : null}
      </div>

      {/* Meta row — model + config (same compact metadata vocabulary as
          the collapsed DraftNode, just sized up slightly). */}
      <div className="flex items-center gap-1.5 px-4 pb-2 text-[11px] leading-none text-text-secondary">
        <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-primary/95">
          {modelLabel}
        </span>
        <span aria-hidden="true" className="text-text-muted/60">·</span>
        <span className="truncate font-mono text-[10px] tracking-[0.04em] text-text-secondary/85">
          {configSummary}
        </span>
      </div>

      {/* Inline Composer — workbench body. Uses mode="inline" so it
          renders in-place (no absolute positioning, no fixed width, no
          floating shadow). All Composer features (mention picker,
          mismatch banner, advanced popover, chip dropdowns, generate)
          stay intact. */}
      <Composer
        inline
        activeTab={activeTab}
        onTabChange={onTabChange}
        prompt={prompt}
        modelLabel={modelLabel}
        aspect={aspect}
        duration={duration}
        count={count}
        modelOptions={modelOptions}
        aspectOptions={aspectOptions}
        durationOptions={durationOptions}
        countOptions={countOptions}
        refs={refs}
        onSubmit={onSubmit}
        onAddRef={onAddRef}
        onRemoveRef={onRemoveRef}
        onPromptCommit={onPromptCommit}
        mentionables={mentionables}
      />

      {/* I (Take version timeline) — horizontal strip of every take
          this draft has generated. Renders only when there is at least
          one take so empty drafts stay quiet. */}
      {takes && takes.length > 0 && onPickTake ? (
        <TakeTimeline takes={takes} onPickTake={onPickTake} />
      ) : null}

      {/* Status footer — same tear-stamp vocabulary as the compact
          DraftNode so the lifecycle reads identically. */}
      <div className="px-4 pb-3 pt-1.5">
        <TearLine tone={m.tone} label={m.label} />
      </div>

      {status === "draft" ? (
        <span
          role="status"
          aria-label="Awaiting approval"
          className="btn-tip absolute right-3 top-3 h-[5px] w-[5px] rounded-full bg-amber-300 shadow-[0_0_0_3px_rgba(252,211,77,0.18)]"
          data-tip="Awaiting approval"
        >
          <span className="sr-only">Awaiting approval</span>
        </span>
      ) : null}
    </div>
  );
}
