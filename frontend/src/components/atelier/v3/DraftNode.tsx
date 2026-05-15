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

  return (
    <div
      role="button"
      tabIndex={0}
      onPointerDown={() => onSelect?.(id)}
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
            {refs.map((r, i) => (
              <img
                key={i}
                src={r}
                alt=""
                role="img"
                className="h-6 w-6 rounded border border-white/10 object-cover"
              />
            ))}
            <span className="ml-1 font-mono text-[10px] text-text-muted">
              {refs.length} ref
            </span>
          </div>
        ) : null}
      </div>
      {status === "draft" ? (
        <span
          className="btn-tip absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-amber-300"
          data-tip="Awaiting approval"
        />
      ) : null}
    </div>
  );
}
