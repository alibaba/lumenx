"use client";

import { useEffect, useRef, useState } from "react";

const VISIBLE_BULLETS = 5;

// v1.4 Batch 3 — agent.updatePlan optionally writes a structured
// `data.steps` array alongside the legacy `data.bullets`. When present,
// PlanNode renders one entry per step with a status glyph; when absent,
// the legacy bullet list renders unchanged so v1.0-v1.3 plan nodes load
// without a re-render diff.
export interface PlanStep {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "completed";
  notes?: string;
}

interface Props {
  id: string;
  title: string;
  bullets: string[];
  /** v1.4 Batch 3 — structured plan steps from agent.updatePlan. When
   *  present, supersedes `bullets` for rendering. */
  steps?: PlanStep[];
  selected?: boolean;
  x: number;
  y: number;
  onSelect?: (id: string) => void;
  /** Persist a renamed plan title. Wired by the shell to updateNode
   *  patching data.title. When omitted, the title is read-only. */
  onTitleCommit?: (next: string) => void;
}

export function PlanNode({ id, title, bullets, steps, selected, x, y, onSelect, onTitleCommit }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);
  const startEditing = () => {
    if (!onTitleCommit) return;
    setDraft(title);
    setEditing(true);
  };
  const commit = () => {
    setEditing(false);
    if (!onTitleCommit) return;
    const next = draft.trim();
    if (next && next !== title) onTitleCommit(next);
  };
  const borderClass = selected
    ? "ring-1 ring-white/25 border-white/20"
    : "border-glass-border";

  const useSteps = Array.isArray(steps) && steps.length > 0;
  const visibleSteps = useSteps ? steps!.slice(0, VISIBLE_BULLETS) : [];
  const overflowSteps = useSteps ? Math.max(0, steps!.length - VISIBLE_BULLETS) : 0;
  const visibleBullets = bullets.slice(0, VISIBLE_BULLETS);
  const overflowBullets = Math.max(0, bullets.length - VISIBLE_BULLETS);
  // Stamp index from id tail. Stable across re-renders without a real index.
  const stampNum = id.slice(-3).toUpperCase();

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
      // PX · demo n-plan (converged-v0.2 §136-139, §541-554): the plan card
      // is a regular glass node — title + right-edge `Plan N` badge,
      // tabular-numbered steps, and an at-node-foot status line. The old
      // opaque #141416 'agency receipt' recipe (Bot chip / tear line) is
      // retired.
      className={`group atelier-node-shell absolute w-[258px] overflow-hidden transition-[box-shadow,border-color] duration-200 ease-out ${borderClass}`}
    >
      <div className="px-[18px] pb-3 pt-3.5">
        <div className="flex items-center gap-2 text-foreground">
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
                  setDraft(title);
                }
              }}
              className="min-w-0 flex-1 rounded border border-atelier-brand-400/55 bg-input-bg px-1 font-display text-[13px] font-medium tracking-[-0.005em] text-foreground outline-none"
              aria-label="Rename plan"
            />
          ) : (
            <span
              className={`flex-1 truncate font-display text-[13px] font-medium tracking-[-0.005em] ${onTitleCommit ? "cursor-text" : ""}`}
              onDoubleClick={(e) => {
                e.stopPropagation();
                startEditing();
              }}
              title={onTitleCommit ? "Double-click to rename" : undefined}
            >
              {title}
            </span>
          )}
          {/* demo .plan-badge — quiet dark chip on the title's right edge */}
          <span className="ml-auto shrink-0 rounded-[6px] border border-white/[0.08] bg-black/30 px-[7px] py-[2px] text-[10.5px] text-white/50">
            Plan {stampNum}
          </span>
        </div>

        {/* demo .step rows — tabular two-digit index + 12px/1.7 titles.
            Structured steps keep their status legible through tone:
            completed dims + strikes, in-progress brightens. */}
        <div className="mt-3">
          {useSteps
            ? visibleSteps.map((step, i) => (
                <div key={step.id} className="flex items-baseline gap-2 text-[12px] leading-[1.7]">
                  <span aria-hidden="true" className="font-display text-[10.5px] tabular-nums text-white/40">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span
                    className={`line-clamp-2 ${
                      step.status === "completed"
                        ? "text-white/40 line-through decoration-white/25"
                        : step.status === "in_progress"
                        ? "text-white/90"
                        : "text-white/70"
                    }`}
                  >
                    {step.title}
                    {step.notes ? <span className="text-white/40"> — {step.notes}</span> : null}
                  </span>
                </div>
              ))
            : visibleBullets.map((b, i) => (
                <div key={i} className="flex items-baseline gap-2 text-[12px] leading-[1.7] text-white/70">
                  <span aria-hidden="true" className="font-display text-[10.5px] tabular-nums text-white/40">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="line-clamp-2">{b}</span>
                </div>
              ))}
          {(useSteps ? overflowSteps : overflowBullets) > 0 ? (
            <div className="mt-0.5 text-[11px] text-white/40">
              +{useSteps ? overflowSteps : overflowBullets} more
            </div>
          ) : null}
        </div>

        {/* demo .at-node-foot — status dot + progress caption over a hairline */}
        {(() => {
          const total = useSteps ? (steps?.length ?? 0) : bullets.length;
          const done = useSteps ? (steps?.filter((s) => s.status === "completed").length ?? 0) : 0;
          const runningIdx = useSteps ? (steps?.findIndex((s) => s.status === "in_progress") ?? -1) : -1;
          const caption =
            runningIdx >= 0
              ? `Running · step ${runningIdx + 1}`
              : total > 0 && done === total && useSteps
              ? "Completed"
              : `${total} step${total === 1 ? "" : "s"}`;
          const dotClass = runningIdx >= 0 ? "at-dot busy" : done === total && total > 0 && useSteps ? "at-dot ok" : "at-dot idle";
          return (
            <div className="mt-3 flex items-center gap-1.5 border-t border-white/[0.08] pt-2.5 text-[11px] text-white/40">
              <span aria-hidden="true" className={dotClass} />
              {caption}
            </div>
          );
        })()}
      </div>
    </div>
  );
}
