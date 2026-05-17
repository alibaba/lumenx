"use client";
import * as React from "react";
import { MessageSquare } from "lucide-react";

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
 * CommentNode — a sticky-note style annotation. Distinct color (violet) from
 * IdeaNode's amber so the canvas reads "remark / pin" vs "raw idea seed".
 * Body text is read-only here; the shell renders an inline textarea overlay
 * when a comment is selected, mirroring the IdeaNode editing pattern.
 */
export function CommentNode({ id, body, author, selected, x, y, onSelect }: Props) {
  const borderClass = selected
    ? "ring-2 ring-primary border-primary/50"
    : "border-violet-300/30";
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
      className={`group absolute w-[220px] rounded-md border bg-violet-950/40 shadow-2xl shadow-black/40 backdrop-blur-md transition-shadow hover:shadow-[0_0_0_1px_rgba(167,139,250,0.22)] ${borderClass}`}
    >
      <span className="absolute left-1.5 top-1.5 hidden rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-violet-200/85 group-hover:flex items-center gap-1">
        <MessageSquare size={9} aria-hidden="true" /> comment
      </span>
      <div className="px-3 py-2.5">
        <p className="line-clamp-6 whitespace-pre-wrap text-[13px] leading-relaxed text-foreground/90">
          {body || (
            <span className="italic text-text-muted">Empty comment — double-click to edit.</span>
          )}
        </p>
        {author ? (
          <div className="mt-1.5 font-mono text-[10px] uppercase tracking-wider text-violet-200/75">
            {author}
          </div>
        ) : null}
      </div>
    </div>
  );
}
