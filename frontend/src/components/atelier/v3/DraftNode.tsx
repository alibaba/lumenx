"use client";

import { useEffect, useRef, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { StatusDot, STATUS_TOKEN } from "./ornaments";
import { PortDot } from "./NodePort";

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
    ? "ring-1 ring-white/25 border-white/20"
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
      style={{ transform: `translate(${x}px, ${y}px)` }}
      // v0.5 Flova: frosted glass node body (target spec §2) — the dotted grid
      // shows through faintly; replaces the flat #141416 card.
      className={`group absolute w-[280px] atelier-node-shell transition-[box-shadow,border-color] duration-200 ease-out ${borderClass}`}
    >
      {/* output port — decorative blue dot on the right edge. v0.6.3:
          compact draft video nodes are not a valid handlePortDragOut source
          (only image media + completed candidate takes are), so this dot
          is a non-interactive indicator only. No data-port, no hover
          affordance, no pointer handler — pointer-down "near" the dot still
          selects/drags the parent draft card correctly. */}
      <PortDot
        kind="output"
        className="absolute right-[-3px] top-1/2 -translate-y-1/2 z-10"
      />
      <div className="px-[18px] pt-4 pb-3.5">
        {/* Title row — muted Sparkles glyph + sentence-case intent title.
            The take counter sits right-aligned as a quiet neutral chip. */}
        <div className="flex items-center gap-1.5 text-foreground">
          <Sparkles size={11} className="shrink-0 text-white/40" aria-hidden="true" />
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
              className="min-w-0 flex-1 rounded border border-atelier-brand-400/55 bg-input-bg px-1 font-sans text-[15px] font-medium tracking-[-0.01em] text-white/90 outline-none"
              aria-label="Rename draft"
            />
          ) : (
            <span
              className={`truncate font-sans text-[15px] font-medium tracking-[-0.01em] text-white/90 ${onIntentCommit ? "cursor-text" : ""}`}
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
            // Take counter — muted neutral chip (no saturated border/hue), e.g.
            // "Take 2/4". Reads as quiet metadata, not a status signal.
            <span
              className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-[4px] border border-white/10 bg-white/[0.03] px-1.5 py-[2px] text-[10px] leading-none text-white/45"
              aria-label={`${candidatesReady ?? 0} of ${candidatesTotal} candidates ready`}
            >
              <span>Take</span>
              <span className="tabular-nums text-white/60">
                {candidatesReady ?? 0}/{candidatesTotal}
              </span>
            </span>
          ) : null}
        </div>

        {/* Meta row — sentence-case Inter (model · config), grayscale-calm.
            No mono-caps, no spinner: a running draft already carries the
            bloom + the footer caption. One calm signal per state. */}
        <div className="mt-2.5 flex items-center gap-1.5 text-[11.5px] font-normal leading-[1.4] text-white/45">
          <span className="font-medium text-white/60">{modelLabel}</span>
          <span aria-hidden="true" className="text-white/25">·</span>
          <span className="truncate text-white/45">{configSummary}</span>
        </div>

        {refs && refs.length > 0 ? (
          <div className="mt-3 flex items-center gap-1.5">
            {visibleRefs.map((r, i) => (
              <div
                key={i}
                className="group/ref relative h-6 w-6 overflow-hidden rounded-[3px] border border-white/8"
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
              <span className="grid h-6 min-w-6 place-items-center rounded-[4px] border border-white/8 bg-white/[0.03] px-1 text-[10px] tabular-nums text-white/45">
                +{overflowCount}
              </span>
            ) : (
              <span className="ml-1 text-[10px] text-white/45">
                {refs.length} ref
              </span>
            )}
          </div>
        ) : null}
      </div>

      {/* Footer — a quiet sentence-case status caption with a tiny neutral
          dot, under a hairline divider. The lifecycle hue lives only in the
          top-right StatusDot, so the footer stays grayscale-calm (spec §9.6). */}
      <div className="mx-[18px] h-px bg-white/[0.08]" />
      <div className="flex items-center gap-1.5 px-[18px] pb-3.5 pt-3">
        <span aria-hidden="true" className="h-[4px] w-[4px] rounded-full bg-white/30" />
        <span className="text-[11px] leading-none text-white/40">
          {STATUS_TOKEN[status].caption}
        </span>
      </div>

      {/* Status dot — one 6px token-colored dot for EVERY status, identical to
          the expanded workbench's dot, so the object's status reads the same
          collapsed and expanded. */}
      <StatusDot status={status} className="absolute right-2.5 top-2.5" />
    </div>
  );
}
