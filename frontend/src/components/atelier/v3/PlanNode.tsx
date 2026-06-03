"use client";

import { useEffect, useRef, useState } from "react";
import { Bot } from "lucide-react";
import { TearLine, StampBadge } from "./ornaments";

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

const STEP_GLYPH: Record<PlanStep["status"], string> = {
  pending: "○",
  in_progress: "◐",
  completed: "●",
};

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
      style={{
        transform: `translate(${x}px, ${y}px)`,
        backgroundImage:
          "linear-gradient(to bottom, rgba(255,255,255,0.02) 0%, rgba(255,255,255,0) 35%)",
      }}
      className={`group absolute w-[268px] overflow-hidden rounded-lg border bg-[#141416] shadow-[0_18px_40px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.6),inset_0_1px_0_0_rgba(255,255,255,0.06)] transition-[box-shadow,border-color] duration-200 ease-out ${borderClass}`}
    >
      <div className="px-4 pb-3 pt-3.5">
        {/* Header — Bot avatar in primary tint, display-font title with
            tighter tracking, plus a stamped index badge in the trailing
            corner for that "agency receipt" weight. */}
        <div className="mb-2.5 flex items-center gap-2 text-foreground">
          <span className="grid h-[20px] w-[20px] shrink-0 place-items-center rounded-[5px] bg-atelier-brand-soft/15 text-atelier-brand-soft ring-1 ring-inset ring-atelier-brand-soft/25">
            <Bot size={11} aria-hidden="true" />
          </span>
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
          <StampBadge label="Plan No" number={stampNum} tone="muted" />
        </div>

        {/* Bullets — vertical hairline indent in lieu of dots. Reads as a
            quiet typographic device, not a list-item marker.
            v1.4 Batch 3 — when structured `steps` are supplied (agent
            wrote them via agent.updatePlan), render the status glyph
            ahead of each title. Otherwise fall back to the legacy
            bullet list (the strings already carry the glyph since the
            executor derives bullets from steps with the same glyph
            map). */}
        <ul className="space-y-1 border-l border-border-subtle pl-3 text-[11.5px] leading-[1.5] text-text-secondary/90">
          {useSteps
            ? visibleSteps.map((step) => (
                <li key={step.id} className="line-clamp-2">
                  <span aria-hidden="true" className="mr-1.5 text-text-muted">
                    {STEP_GLYPH[step.status] ?? "○"}
                  </span>
                  <span>{step.title}</span>
                  {step.notes ? (
                    <span className="text-text-muted"> — {step.notes}</span>
                  ) : null}
                </li>
              ))
            : visibleBullets.map((b, i) => (
                <li key={i} className="line-clamp-2">
                  {b}
                </li>
              ))}
          {useSteps
            ? overflowSteps > 0 && (
                <li className="text-text-muted">+{overflowSteps} more</li>
              )
            : overflowBullets > 0 && (
                <li className="text-text-muted">+{overflowBullets} more</li>
              )}
        </ul>

        {/* Tear-stamp footer: dashed perforation flanking the agent
            attribution. Reads as the bottom of an agency receipt. */}
        <div className="mt-3">
          <TearLine label="Plan · by Agent" />
        </div>
      </div>
    </div>
  );
}
