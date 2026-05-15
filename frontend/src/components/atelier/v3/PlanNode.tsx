"use client";

import { Bot } from "lucide-react";

interface Props {
  id: string;
  title: string;
  bullets: string[];
  selected?: boolean;
  x: number;
  y: number;
  onSelect?: (id: string) => void;
}

export function PlanNode({ id, title, bullets, selected, x, y, onSelect }: Props) {
  const borderClass = selected
    ? "ring-2 ring-primary border-primary/50"
    : "border-primary/30";

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
      className={`group absolute w-[260px] rounded-md border bg-elevated/85 backdrop-blur-md ${borderClass} px-3 py-2.5`}
    >
      <div className="mb-1.5 inline-flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
        <span className="grid h-5 w-5 place-items-center rounded bg-primary/20 text-primary">
          <Bot size={11} />
        </span>
        <span className="truncate">{title}</span>
      </div>
      <ul className="space-y-0.5 text-[11px] text-text-secondary">
        {bullets.map((b, i) => (
          <li key={i}>
            <span className="mr-1 text-text-muted">·</span>
            {b}
          </li>
        ))}
      </ul>
      <div className="mt-1.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        PLAN · by Agent
      </div>
    </div>
  );
}
