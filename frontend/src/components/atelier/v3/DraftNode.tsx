"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";

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
}

const STATUS_BORDER: Record<DraftNodeStatus, string> = {
  draft: "border-amber-300/40",
  approved: "border-primary/40",
  running: "border-blue-400/50",
  completed: "border-glass-border opacity-80",
};

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
}: Props) {
  const borderClass = selected
    ? "ring-2 ring-primary border-primary/50"
    : STATUS_BORDER[status];

  const VISIBLE_REFS = 4;
  const visibleRefs = refs ? refs.slice(0, VISIBLE_REFS) : [];
  const overflowCount = refs ? Math.max(0, refs.length - VISIBLE_REFS) : 0;

  // Inline rename: dbl-click the intent label flips it to a text input.
  // Enter or blur commits, Esc reverts. Intent prop drives the input's
  // initial value each time editing starts so external updates don't get
  // overwritten by a stale draft.
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
      className={`group absolute w-[240px] rounded-md border bg-elevated shadow-2xl shadow-black/40 backdrop-blur-md transition-shadow hover:shadow-[0_0_0_1px_rgba(100,108,255,0.18)] ${borderClass}`}
    >
      <div className="px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <Sparkles size={12} className="text-primary shrink-0" />
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
              className="min-w-0 flex-1 rounded border border-primary/60 bg-input-bg px-1 text-[13px] font-semibold text-foreground outline-none"
              aria-label="Rename draft"
            />
          ) : (
            <span
              className={`truncate ${onIntentCommit ? "cursor-text" : ""}`}
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
            <span
              className={`ml-auto shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[9px] font-semibold ${
                (candidatesReady ?? 0) >= candidatesTotal
                  ? "bg-emerald-400/15 text-emerald-200"
                  : "bg-blue-400/15 text-blue-200"
              }`}
              aria-label={`${candidatesReady ?? 0} of ${candidatesTotal} candidates ready`}
            >
              {candidatesReady ?? 0}/{candidatesTotal} ready
            </span>
          ) : null}
        </div>
        <div className="mt-1 flex items-center gap-1.5 text-[11px] text-text-secondary">
          <span className="rounded bg-primary/15 px-1.5 py-0.5 font-semibold text-primary">
            {modelLabel}
          </span>
          <span className="truncate">{configSummary}</span>
          {status === "running" ? (
            <Loader2 size={11} className="ml-auto text-blue-200 animate-spin" />
          ) : null}
        </div>
        {refs && refs.length > 0 ? (
          <div className="mt-1.5 flex items-center gap-1">
            {visibleRefs.map((r, i) => (
              <img
                key={i}
                src={r}
                alt={`Reference ${i + 1}`}
                loading="lazy"
                decoding="async"
                className="h-6 w-6 rounded border border-white/10 object-cover"
              />
            ))}
            {overflowCount > 0 ? (
              <span className="grid h-6 min-w-[1.5rem] place-items-center rounded border border-white/10 bg-white/[0.04] px-1 text-[10px] font-mono text-text-muted">
                +{overflowCount}
              </span>
            ) : null}
            {overflowCount === 0 ? (
              <span className="ml-1 font-mono text-[10px] text-text-muted">
                {refs.length} ref
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
      {status === "draft" ? (
        <span
          role="status"
          aria-label="Awaiting approval"
          className="btn-tip absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-amber-300"
          data-tip="Awaiting approval"
        >
          <span className="sr-only">Awaiting approval</span>
        </span>
      ) : null}
    </div>
  );
}
