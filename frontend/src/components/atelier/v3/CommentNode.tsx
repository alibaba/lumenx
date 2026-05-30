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
  /** When true, skip body + footer so the shell's overlaid textarea sits
   *  cleanly. Same fix as IdeaNode. */
  editing?: boolean;
}

// CommentNode — annotation pinned to the canvas. Reads as a tipped-in
// review slip: italic display body sits above a dashed perforation that
// optionally carries the author's mono-caps signature.
export function CommentNode({ id, body, author, selected, x, y, onSelect, editing }: Props) {
  const borderClass = selected
    ? "ring-2 ring-atelier-brand-400 border-atelier-brand-400/45"
    : "border-atelier-mauve/15";
  // Stamp index from id tail. Stable across renders without piping a real
  // index from the parent.
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
          "linear-gradient(155deg, rgba(181,154,190,0.06) 0%, rgba(181,154,190,0.02) 60%, rgba(0,0,0,0) 100%)",
      }}
      className={`group absolute w-[224px] overflow-hidden rounded-[10px] border bg-[#15141a] shadow-[0_14px_36px_-22px_rgba(0,0,0,0.7),0_2px_4px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(181,154,190,0.07)] transition-[box-shadow,border-color] duration-200 ${borderClass}`}
    >
      {editing ? (
        <div aria-hidden="true" className="min-h-[140px]" />
      ) : (
        <>
          <div className="px-3.5 pb-2 pt-3">
            <p className="line-clamp-5 whitespace-pre-wrap text-[13.5px] italic leading-[1.5] tracking-tight text-foreground/95">
              {body || (
                <span className="not-italic font-mono text-[10.5px] uppercase tracking-[0.22em] text-atelier-mauve/70">
                  empty · double-click
                </span>
              )}
            </p>
          </div>
          {/* Tear-stamp footer: dashed perforation flanking the author + index.
              When no author, just shows "NOTE · NO XXX". */}
          <div
            className="flex items-center gap-2 px-3 pb-2.5"
            aria-hidden="true"
          >
            <div className="flex-1 border-t border-dashed border-atelier-mauve/35" />
            <span className="shrink-0 font-mono text-[8.5px] font-medium uppercase tracking-[0.26em] text-atelier-mauve/90">
              {author ? `${author} · No ${stampNum}` : `Note · No ${stampNum}`}
            </span>
            <div className="flex-1 border-t border-dashed border-atelier-mauve/35" />
          </div>
        </>
      )}
    </div>
  );
}
