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
// Implementation notes (v0.4.5):
//   - Width 520 — bumped from 480 to absorb the double-frame padding
//     overhead (atelier-opaque-shell padding 10 + atelier-opaque-inner
//     padding 18-20) without shrinking the prompt + ref slot content.
//   - Sits inside the canvas world transform, so it scales with zoom.
//   - The shell renders this when (selectedNode is a draft); otherwise
//     the compact DraftNode renders.
//   - Outer .atelier-opaque-shell is the visible atmospheric frame
//     (background + border + bloom internal via ::before in header zone).
//     Inner .atelier-opaque-inner hosts the operating area (prompt,
//     refs, controls, take strip). See globals.css §12.2 / §12.7.4.
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

  // v0.4.5 §12.2 + §12.7.4: selected workbench wears HERO bloom + attending
  // breath. opaque-shell already provides the visible outer frame; ring +
  // border-cobalt only when selected to reinforce the "focused" state.
  const selectedClass = selected
    ? "atelier-bloom atelier-bloom-hero atelier-breath-attending ring-2 ring-atelier-brand-400/55"
    : "";

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
      style={{ transform: `translate(${x}px, ${y}px)` }}
      // v0.4.5 §12.2 + §12.7.4: outer shell is the atmospheric frame
      // (carries bloom + visible border); inner card is the operating
      // area. Width bumped 480 → 520 to absorb the double-frame padding
      // overhead without shrinking the content area.
      className={`group absolute w-[520px] origin-top-left atelier-opaque-shell transition-shadow duration-200 motion-safe:animate-atelier-workbench-in ${selectedClass}`}
    >
      {/* Header zone — sparkle + intent (rename on dblclick) + take pill.
          Sits in the outer shell's top band (above the inner card), like
          the "Image Generator" title in the reference. Bloom paints
          behind this via the shell's ::before. */}
      <div className="atelier-shell-header text-foreground">
        <Sparkles
          size={12}
          className="shrink-0 text-atelier-brand-soft"
          aria-hidden="true"
        />
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
            className="min-w-0 flex-1 rounded border border-atelier-brand-400/55 bg-input-bg px-1 font-display text-[15px] font-medium tracking-[-0.005em] text-foreground outline-none"
            aria-label="Rename draft"
          />
        ) : (
          <span
            className={`min-w-0 flex-1 truncate font-display text-[15px] font-medium tracking-[-0.005em] ${onIntentCommit ? "cursor-text" : ""}`}
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
            className="btn-tip ml-auto inline-flex shrink-0 items-center gap-1 rounded-[3px] border border-dashed border-atelier-ochre/45 bg-atelier-ochre/10 px-1.5 py-[2px] font-mono text-[8.5px] font-medium uppercase tracking-[0.22em] text-atelier-ochre"
          >
            <AlertTriangle size={9} aria-hidden="true" />
            Stale ref · {staleRefCount}
          </span>
        ) : null}
        {typeof candidatesTotal === "number" && candidatesTotal > 0 ? (
          <span
            className={`ml-auto inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-[2px] text-[10px] font-medium ${
              (candidatesReady ?? 0) >= candidatesTotal
                ? "border-atelier-sage/35 bg-atelier-sage/8 text-atelier-sage"
                : "border-atelier-brand-soft/35 bg-atelier-brand-soft/8 text-atelier-brand-soft"
            }`}
            aria-label={`${candidatesReady ?? 0} of ${candidatesTotal} candidates ready`}
          >
            <span className="font-display tracking-tight">
              {candidatesReady ?? 0}
            </span>
            <span className="opacity-60">of</span>
            <span className="font-display tracking-tight">{candidatesTotal}</span>
          </span>
        ) : null}
        {status === "running" ? (
          <Loader2 size={11} className="shrink-0 animate-spin text-atelier-brand-soft" />
        ) : null}
      </div>

      {/* Inner content card — the operating area. Meta + Composer +
          TakeTimeline + TearLine all live in here. Staggered ~140ms behind the
          shell frame's grow-in so the body "drops in" after the drawer opens,
          instead of the whole 520px workbench appearing at once. The header
          (above) rides the frame's own ramp — it existed in the compact card,
          so animating it with the frame preserves identity continuity. */}
      <div className="atelier-opaque-inner motion-safe:animate-atelier-workbench-content-in">
        {/* Meta row — model + config. v0.4.5 §13.4: model name uses
            --brand-soft (muted slate) not saturated cobalt. */}
        <div className="mb-3 flex items-center gap-1.5 text-[11px] leading-none text-text-secondary">
          <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-atelier-brand-soft">
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
          <div className="mt-3">
            <TakeTimeline takes={takes} onPickTake={onPickTake} />
          </div>
        ) : null}

        {/* Status footer — same tear-stamp vocabulary as the compact
            DraftNode so the lifecycle reads identically. */}
        <div className="mt-3">
          <TearLine tone={m.tone} label={m.label} />
        </div>
      </div>

      {status === "draft" ? (
        <span
          role="status"
          aria-label="Awaiting approval"
          className="btn-tip absolute right-3 top-3 z-10 h-[5px] w-[5px] rounded-full bg-atelier-ochre shadow-[0_0_0_3px_rgba(201,168,126,0.18)]"
          data-tip="Awaiting approval"
        >
          <span className="sr-only">Awaiting approval</span>
        </span>
      ) : null}
    </div>
  );
}
