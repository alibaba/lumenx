"use client";

import { Loader2, Sparkles } from "lucide-react";

export type DraftNodeStatus = "draft" | "approved" | "running" | "completed";

interface Props {
  id: string;
  status: DraftNodeStatus;
  intent: string;
  modelLabel: string;
  configSummary: string;
  refs?: string[];
  selected?: boolean;
  x: number;
  y: number;
  onSelect?: (id: string) => void;
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
  selected,
  x,
  y,
  onSelect,
}: Props) {
  const borderClass = selected
    ? "ring-2 ring-primary border-primary/50"
    : STATUS_BORDER[status];

  const VISIBLE_REFS = 4;
  const visibleRefs = refs ? refs.slice(0, VISIBLE_REFS) : [];
  const overflowCount = refs ? Math.max(0, refs.length - VISIBLE_REFS) : 0;

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
      className={`group absolute w-[240px] rounded-md border bg-elevated/85 backdrop-blur-md ${borderClass}`}
    >
      <div className="px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <Sparkles size={12} className="text-primary shrink-0" />
          <span className="truncate">{intent}</span>
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
