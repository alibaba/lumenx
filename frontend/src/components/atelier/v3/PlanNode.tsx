"use client";

import { Bot } from "lucide-react";

const VISIBLE_BULLETS = 5;

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

  const visibleBullets = bullets.slice(0, VISIBLE_BULLETS);
  const overflowBullets = Math.max(0, bullets.length - VISIBLE_BULLETS);

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
        {visibleBullets.map((b, i) => (
          <li key={i} className="line-clamp-2">
            <span aria-hidden="true" className="mr-1 text-text-muted">·</span>
            {b}
          </li>
        ))}
        {overflowBullets > 0 && (
          <li className="text-text-muted">
            <span aria-hidden="true" className="mr-1">·</span>+{overflowBullets} more
          </li>
        )}
      </ul>
      <div className="mt-1.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        PLAN · by Agent
      </div>
    </div>
  );
}
