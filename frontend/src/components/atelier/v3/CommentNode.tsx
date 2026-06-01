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
  /** When true, the AtelierShellV3 overlay textarea owns the visual frame —
   *  render nothing here to avoid a double-shell halo around the editor.
   *  Same fix as IdeaNode. */
  editing?: boolean;
}

// CommentNode — a calm editorial review slip pinned to the canvas. Shares the
// frosted glass shell (.atelier-node-shell) with the Idea slip; here the soft
// CATEGORY wash is mauve instead of ochre. The body sits above a single
// muted sentence-case signature line carrying the author + index.
export function CommentNode({ id, body, author, selected, x, y, onSelect, editing }: Props) {
  // Editing: skip rendering so only the overlay textarea paints a frame.
  if (editing) return null;
  const borderClass = selected
    ? "ring-1 ring-white/25 border-white/20"
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
      style={{ transform: `translate(${x}px, ${y}px)` }}
      className={`group absolute w-[260px] overflow-hidden atelier-node-shell transition-[box-shadow,border-color] duration-200 ease-out ${borderClass}`}
    >
      {/* Mauve category wash — a soft frosted tint over the glass shell (never
          replacing its depth gradient). Whisper alpha, pointer-events-none. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            "linear-gradient(155deg, rgba(181,154,190,0.055) 0%, rgba(181,154,190,0.015) 55%, rgba(0,0,0,0) 100%)",
        }}
      />
      <div className="relative">
        <div className="px-[18px] pb-3.5 pt-4">
          <p className="line-clamp-5 whitespace-pre-wrap text-[13px] leading-[1.6] text-white/80">
            {body || (
              <span className="text-[12px] italic text-white/35">
                Empty · double-click to write
              </span>
            )}
          </p>
        </div>
        {/* Quiet signature line — a tiny soft-mauve category dot + a
            sentence-case Inter label (author + index, or "Note"). Replaces
            the old mono-caps dashed signature. */}
        <div className="flex items-center gap-1.5 px-[18px] pb-3.5 pt-0.5" aria-hidden="true">
          <span
            aria-hidden="true"
            className="h-[5px] w-[5px] shrink-0 rounded-full bg-atelier-mauve/45"
          />
          <span className="text-[11px] text-white/40">
            {author ? `${author} · No ${stampNum}` : `Note · No ${stampNum}`}
          </span>
        </div>
      </div>
    </div>
  );
}
