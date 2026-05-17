"use client";
import * as React from "react";

interface Props {
  id: string;
  body: string;
  author?: string;
  selected?: boolean;
  x: number;
  y: number;
  onSelect?: (id: string) => void;
}

/**
 * CommentNode — annotation pinned to the canvas. Distinct color story
 * (cool violet/indigo) from IdeaNode (warm amber) so the two text-card
 * types read at a glance. Same chrome vocabulary: invisible-by-default
 * border, corner mono tag on hover, primary ring when selected.
 */
export function CommentNode({ id, body, author, selected, x, y, onSelect }: Props) {
  const borderClass = selected
    ? "ring-2 ring-primary border-primary/45"
    : "border-violet-200/12";
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
          "linear-gradient(155deg, rgba(167,139,250,0.06) 0%, rgba(167,139,250,0.02) 60%, rgba(0,0,0,0) 100%)",
      }}
      className={`group absolute w-[224px] overflow-hidden rounded-[10px] border bg-[#15141a] shadow-[0_14px_36px_-22px_rgba(0,0,0,0.7),0_2px_4px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(167,139,250,0.07)] transition-shadow duration-200 ${borderClass}`}
    >
      <span className="absolute right-2.5 top-2 hidden font-mono text-[9px] uppercase tracking-[0.22em] text-violet-200/65 group-hover:block">
        Note
      </span>
      <div className="px-3.5 pb-3 pt-3">
        <p className="line-clamp-6 whitespace-pre-wrap text-[13px] leading-[1.55] text-foreground/92">
          {body || (
            <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-violet-200/55">
              empty · double-click
            </span>
          )}
        </p>
        {author ? (
          <div className="mt-2 font-mono text-[9px] uppercase tracking-[0.2em] text-violet-200/65">
            {author}
          </div>
        ) : null}
      </div>
    </div>
  );
}
