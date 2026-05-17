"use client";
import * as React from "react";

interface Props {
  id: string;
  body: string;
  selected?: boolean;
  x: number;
  y: number;
  onSelect?: (id: string) => void;
}

export function IdeaNode({ id, body, selected, x, y, onSelect }: Props) {
  const borderClass = selected
    ? "ring-2 ring-primary border-primary/50"
    : "border-amber-300/20";
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
      className={`group absolute w-[220px] rounded-md border bg-amber-950/40 shadow-2xl shadow-black/40 backdrop-blur-md transition-shadow hover:shadow-[0_0_0_1px_rgba(252,211,77,0.18)] ${borderClass}`}
    >
      <span className="absolute left-1.5 top-1.5 hidden rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-amber-200/85 group-hover:block">
        💡 idea
      </span>
      <div className="px-3 py-2.5">
        <p className="line-clamp-6 whitespace-pre-wrap text-[13px] leading-relaxed text-foreground/90">
          {body || (
            <span className="italic text-text-muted">Empty idea — double-click to edit.</span>
          )}
        </p>
      </div>
    </div>
  );
}
