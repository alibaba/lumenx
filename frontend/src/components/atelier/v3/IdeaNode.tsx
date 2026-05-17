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
  // Borders disappear when not selected — DESIGN.md §6.1 "default = content
  // itself, no chrome". Selected state takes the primary ring; otherwise we
  // rely on the warm tint + the small uppercase corner tag for identity.
  const borderClass = selected
    ? "ring-2 ring-primary border-primary/45"
    : "border-amber-200/12";
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
          "linear-gradient(155deg, rgba(252,211,77,0.06) 0%, rgba(252,211,77,0.02) 60%, rgba(0,0,0,0) 100%)",
      }}
      className={`group absolute w-[224px] overflow-hidden rounded-[10px] border bg-[#1a1611] shadow-[0_14px_36px_-22px_rgba(0,0,0,0.7),0_2px_4px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(252,211,77,0.06)] transition-shadow duration-200 ${borderClass}`}
    >
      {/* Corner tag — uppercase mono, no emoji. Hover-only chrome per
          §6.1; visible enough on hover to anchor identity, invisible at
          rest so a wall of ideas reads as text not chrome. */}
      <span className="absolute right-2.5 top-2 hidden font-mono text-[9px] uppercase tracking-[0.22em] text-amber-200/65 group-hover:block">
        Idea
      </span>
      <div className="px-3.5 pb-3 pt-3">
        <p className="line-clamp-6 whitespace-pre-wrap text-[13px] leading-[1.55] text-foreground/92">
          {body || (
            <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-amber-200/55">
              empty · double-click
            </span>
          )}
        </p>
      </div>
    </div>
  );
}
