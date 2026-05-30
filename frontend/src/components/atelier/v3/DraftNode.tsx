"use client";

import { useEffect, useRef, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { TearLine, StatusDot, STATUS_TOKEN } from "./ornaments";

export type DraftNodeStatus = "draft" | "approved" | "running" | "completed";

interface Props {
  id: string;
  status: DraftNodeStatus;
  intent: string;
  modelLabel: string;
  configSummary: string;
  refs?: string[];
  candidatesReady?: number;
  candidatesTotal?: number;
  selected?: boolean;
  x: number;
  y: number;
  onSelect?: (id: string) => void;
  /** Persist a renamed intent. Wired by the shell to updateNode patching
   *  data.intent. When omitted, the title becomes read-only. */
  onIntentCommit?: (next: string) => void;
  /** Detach a single reference URL (called when the user clicks the ×
   *  badge that hovers over a ref thumbnail). Wired by the shell to
   *  store.detachReferenceNode. When omitted, refs stay read-only. */
  onDetachRef?: (url: string) => void;
}

// Lifecycle status is NOT painted on the body/border/rail any more — it lives
// solely in the StatusDot (top-right) + the muted footer caption, so the node
// reads identically collapsed and expanded. See ornaments.tsx STATUS_TOKEN.

export function DraftNode({
  id,
  status,
  intent,
  modelLabel,
  configSummary,
  refs,
  candidatesReady,
  candidatesTotal,
  selected,
  x,
  y,
  onSelect,
  onIntentCommit,
  onDetachRef,
}: Props) {
  // Body is neutral-cool in every status; cobalt ring only when selected.
  // Status is carried by the StatusDot + footer caption, NOT the border.
  const borderClass = selected
    ? "ring-2 ring-atelier-brand-400 border-atelier-brand-400/50"
    : "border-glass-border";

  const VISIBLE_REFS = 4;
  const visibleRefs = refs ? refs.slice(0, VISIBLE_REFS) : [];
  const overflowCount = refs ? Math.max(0, refs.length - VISIBLE_REFS) : 0;

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

  // Cinematic surface vocabulary:
  //   - Deeper bg (#141416 mix) keeps the card from melting into draft borders
  //   - 1px white/[0.06] inset top edge highlight reads as "this card is
  //     a real surface lit from above" — the signature detail, not a
  //     decoration
  //   - Status rail is a 2px hairline pinned to the left edge (via ::before),
  //     replaces the border-color status hack on the whole box
  return (
    <div
      role="button"
      tabIndex={0}
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
          "linear-gradient(to bottom, rgba(255,255,255,0.018) 0%, rgba(255,255,255,0) 32%)",
      }}
      className={`group absolute w-[244px] overflow-hidden rounded-lg border bg-[#141416] shadow-[0_18px_40px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.6),inset_0_1px_0_0_rgba(255,255,255,0.06)] transition-[box-shadow,border-color] duration-200 ease-out ${borderClass}`}
    >
      <div className="px-3.5 pb-2.5 pt-3">
        {/* Title row — Sparkles 11px primary, intent in display font tighter
            tracking, ready badge mono caps */}
        <div className="flex items-center gap-1.5 text-foreground">
          <Sparkles size={11} className="shrink-0 text-atelier-brand-soft" aria-hidden="true" />
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
              className="min-w-0 flex-1 rounded border border-atelier-brand-400/55 bg-input-bg px-1 font-display text-[13px] font-medium tracking-[-0.005em] text-foreground outline-none"
              aria-label="Rename draft"
            />
          ) : (
            <span
              className={`truncate font-display text-[13px] font-medium tracking-[-0.005em] ${onIntentCommit ? "cursor-text" : ""}`}
              onDoubleClick={(e) => {
                e.stopPropagation();
                startEditing();
              }}
              title={onIntentCommit ? "Double-click to rename" : undefined}
            >
              {intent}
            </span>
          )}
          {typeof candidatesTotal === "number" && candidatesTotal > 0 ? (
            // Take counter — stamped feel via dashed inset border, e.g.
            // "TAKE · 02/04". Tone shifts to emerald when fully ready.
            <span
              className={`ml-auto inline-flex shrink-0 items-center gap-1 rounded-[3px] border border-dashed px-1.5 py-[2px] font-mono text-[8.5px] font-medium uppercase tracking-[0.22em] ${
                (candidatesReady ?? 0) >= candidatesTotal
                  ? "border-atelier-sage/45 text-atelier-sage"
                  : "border-atelier-brand-soft/45 text-atelier-brand-soft"
              }`}
              aria-label={`${candidatesReady ?? 0} of ${candidatesTotal} candidates ready`}
            >
              <span>Take</span>
              <span className="font-display text-[10px] tracking-tight">
                {candidatesReady ?? 0}/{candidatesTotal}
              </span>
            </span>
          ) : null}
        </div>

        {/* Meta row — model name in mono caps (signature detail), thin
            divider dot, config summary in muted secondary, optional spinner */}
        <div className="mt-1.5 flex items-center gap-1.5 text-[11px] leading-none text-text-secondary">
          <span className="font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-atelier-brand-soft">
            {modelLabel}
          </span>
          <span aria-hidden="true" className="text-text-muted/60">·</span>
          <span className="truncate font-mono text-[10px] tracking-[0.04em] text-text-secondary/85">
            {configSummary}
          </span>
          {/* No spinner here: a running draft already carries the breath-generating
              bloom + the "Generating takes" footer. One calm signal per state —
              breath + spin on the same small card was redundant motion. */}
        </div>

        {refs && refs.length > 0 ? (
          <div className="mt-2 flex items-center gap-1">
            {visibleRefs.map((r, i) => (
              <div
                key={i}
                className="group/ref relative h-[22px] w-[22px] overflow-hidden rounded-[3px] border border-white/8"
              >
                <img
                  src={r}
                  alt={`Reference ${i + 1}`}
                  loading="lazy"
                  decoding="async"
                  className="h-full w-full object-cover"
                />
                {onDetachRef ? (
                  <button
                    type="button"
                    aria-label={`Detach reference ${i + 1}`}
                    data-tip="Detach"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      onDetachRef(r);
                    }}
                    className="btn-tip absolute inset-0 grid place-items-center bg-black/65 text-atelier-failed opacity-0 transition-opacity hover:opacity-100 group-hover/ref:opacity-100"
                  >
                    <X size={11} aria-hidden="true" />
                  </button>
                ) : null}
              </div>
            ))}
            {overflowCount > 0 ? (
              <span className="grid h-[22px] min-w-[22px] place-items-center rounded-[3px] border border-white/8 bg-white/[0.03] px-1 font-mono text-[9px] tracking-tight text-text-muted">
                +{overflowCount}
              </span>
            ) : (
              <span className="ml-1 font-mono text-[9px] uppercase tracking-[0.16em] text-text-muted">
                {refs.length} ref
              </span>
            )}
          </div>
        ) : null}
      </div>

      {/* Receipt footer — dashed perforation + muted status caption. The hue
          lives in the StatusDot (top-right), never in the caption text, so the
          footer reads the same in every status. */}
      <div className="px-3.5 pb-2.5">
        <TearLine tone="muted" label={STATUS_TOKEN[status].caption} />
      </div>

      {/* Status dot — one 6px token-colored dot for EVERY status, identical to
          the expanded workbench's dot, so the object's status reads the same
          collapsed and expanded. */}
      <StatusDot status={status} className="absolute right-2.5 top-2.5" />
    </div>
  );
}
