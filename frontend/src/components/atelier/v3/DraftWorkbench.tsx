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
import { NodePort, PortDot } from "./NodePort";
import { StatusDot, STATUS_TOKEN } from "./ornaments";
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
    ? "atelier-bloom atelier-bloom-hero atelier-breath-attending ring-1 ring-white/25"
    : "";

  // Status caption — single source of truth shared with the compact DraftNode
  // (ornaments STATUS_TOKEN), so the lifecycle reads identically collapsed and
  // expanded. Hue lives in the StatusDot, the caption stays muted.
  const caption = STATUS_TOKEN[status].caption;

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
        // v0.5 §2 — read the shell as ONE frosted glass card. opaque-shell's
        // base fill is near-solid rgba(28,32,52,0.94); override it with the
        // translucent node-fill so the dotted grid shows through faintly, and
        // bump the blur to 20px. overflow:visible lets the I/O port dots sit
        // half-outside the border (opaque-shell defaults to overflow:hidden,
        // which would clip them). The opaque-shell class is kept so the
        // internal bloom (::before), header layout, and grow-in are intact.
        background: "var(--atelier-node-fill)",
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
        overflow: "visible",
      }}
      // v0.5 §2: outer shell is the single frosted card; the inner card's
      // opaque fill is neutralized below so it reads as one surface. Width
      // 520 keeps the prompt + ref content roomy.
      className={`group absolute w-[560px] origin-top-left atelier-opaque-shell transition-shadow duration-200 motion-safe:animate-atelier-workbench-in ${selectedClass}`}
    >
      {/* §2 OUTPUT PORT — blue dot anchored to the right edge MIDLINE (no
          longer floats in the top-right corner like an accidental sticker).
          Wrapped in a hoverable 20px hit target with cursor:grab + tooltip so
          the user immediately reads "drag from here to connect" — the fix for
          the user's "节点之间如何连线？？" question. On hover the dot scales
          and a soft halo ring fades in so the affordance is unmistakable. */}
      <div
        role="button"
        aria-label="Output port — drag to connect"
        data-tip="Drag to connect"
        onPointerDown={(e) => e.stopPropagation()}
        className="btn-tip absolute -right-[5px] top-1/2 z-20 grid h-5 w-5 -translate-y-1/2 cursor-grab place-items-center rounded-full transition-all duration-150 hover:scale-125 hover:bg-[rgba(91,157,255,0.06)] hover:shadow-[0_0_0_3px_rgba(91,157,255,0.18),0_0_14px_rgba(91,157,255,0.5)] active:cursor-grabbing"
      >
        <PortDot kind="output" size={9} />
      </div>

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
            className="min-w-0 flex-1 rounded border border-atelier-brand-400/55 bg-input-bg px-1 font-display text-[15px] font-medium tracking-[-0.01em] text-foreground outline-none"
            aria-label="Rename draft"
          />
        ) : (
          <span
            className={`min-w-0 flex-1 truncate font-display text-[15px] font-medium tracking-[-0.01em] text-white/90 ${onIntentCommit ? "cursor-text" : ""}`}
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
            className="btn-tip ml-auto inline-flex shrink-0 items-center gap-1 rounded-md border border-atelier-ochre/30 bg-atelier-ochre/10 px-1.5 py-[2px] text-[10px] font-medium text-atelier-ochre/90"
          >
            <AlertTriangle size={9} aria-hidden="true" />
            Stale ref · {staleRefCount}
          </span>
        ) : null}
        {typeof candidatesTotal === "number" && candidatesTotal > 0 ? (
          <span
            className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-full border border-white/[0.08] bg-white/[0.04] px-2 py-[2px] text-[10px] font-medium text-white/55"
            aria-label={`${candidatesReady ?? 0} of ${candidatesTotal} candidates ready`}
          >
            <span className="font-display tracking-tight text-white/75">
              {candidatesReady ?? 0}
            </span>
            <span className="opacity-50">of</span>
            <span className="font-display tracking-tight text-white/75">{candidatesTotal}</span>
          </span>
        ) : null}
        {status === "running" ? (
          <Loader2 size={11} className="shrink-0 animate-spin text-atelier-processing" />
        ) : null}
      </div>

      {/* Inner content card — the operating area. Meta + Composer +
          TakeTimeline + status footer all live in here. Staggered ~140ms behind the
          shell frame's grow-in so the body "drops in" after the drawer opens,
          instead of the whole 520px workbench appearing at once. The header
          (above) rides the frame's own ramp — it existed in the compact card,
          so animating it with the frame preserves identity continuity. */}
      <div
        // v0.5 §2: neutralize the inner card's opaque fill + border so the
        // frosted outer shell is the single visible surface (no double-frame).
        style={{ background: "transparent", borderColor: "transparent" }}
        className="atelier-opaque-inner p-5 motion-safe:animate-atelier-workbench-content-in"
      >
        {/* v0.5.4 — two columns: a clean LEFT INPUT-PORT RAIL (model / positive
            / negative, RON I/O), and the BODY (Composer + takes + footer). The
            old absolute-overlapping ports + redundant Model/Config rows (which
            duplicated the Composer's own model/aspect chips) are gone — that
            collision was the "排版乱" the user hit. The rail dots straddle the
            card's left border (-ml) so connection beams plug in. */}
        <div className="flex gap-4">
          <div className="flex shrink-0 flex-col gap-3 pt-1 -ml-[14px]">
            <NodePort kind="model" side="left" label="model" size={7} />
            <NodePort kind="positive" side="left" label="positive" size={7} />
            <NodePort kind="negative" side="left" label="negative" size={7} />
          </div>

          <div className="min-w-0 flex-1">
            {/* Inline Composer — the generation body. mode="inline": no
                absolute positioning / fixed width / floating shadow. All
                Composer features intact. */}
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

            {/* Take version timeline — renders only when ≥1 take. */}
            {takes && takes.length > 0 && onPickTake ? (
              <div className="mt-3">
                <TakeTimeline takes={takes} onPickTake={onPickTake} />
              </div>
            ) : null}

            {/* Status footer — quiet sentence-case caption + tiny neutral dot. */}
            <div className="mt-3 flex items-center gap-2 border-t border-white/8 pt-3">
              <span
                aria-hidden="true"
                className="h-[5px] w-[5px] shrink-0 rounded-full bg-white/25"
              />
              <span className="text-[11px] text-white/40">{caption}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Status dot — same 6px token-colored dot as the collapsed card, for
          EVERY status. Selection (cobalt ring + hero bloom) is layered on top
          via selectedClass and is orthogonal to this status signal. */}
      <StatusDot status={status} className="absolute right-3 top-3 z-10" />
    </div>
  );
}
