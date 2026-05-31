"use client";
import * as React from "react";

interface Props {
  id: string;
  body: string;
  selected?: boolean;
  x: number;
  y: number;
  onSelect?: (id: string) => void;
  /** When true, the shell is overlaying an inline textarea editor — render
   *  only the chrome shell so the editor sits cleanly on top. Skipping the
   *  body + footer prevents the duplicated text the user reported. */
  editing?: boolean;
}

// Idea node — a calm editorial slip pinned to the wall of ideas. The frosted
// glass shell (.atelier-node-shell) supplies the depth, hairline highlight and
// float shadow; we lay only a whisper-soft warm-ochre CATEGORY wash over it so
// the slip reads premium and quiet, never garish. The body sits in a gentle
// near-handwritten italic, signed off by a single muted sentence-case line.
export function IdeaNode({ id, body, selected, x, y, onSelect, editing }: Props) {
  const borderClass = selected
    ? "ring-2 ring-atelier-brand-400 border-atelier-brand-400/45"
    : "border-atelier-ochre/15";
  // Take the last 3 chars of the node id and display them as a stamped
  // index — keeps the slip identifiable without piping a real index in.
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
      {/* Warm-ochre category wash — a soft frosted tint laid over the glass
          shell (never replacing its depth gradient). Whisper alpha so it reads
          luxe, not a sticker. pointer-events-none so it never eats clicks. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            "linear-gradient(155deg, rgba(201,168,126,0.055) 0%, rgba(201,168,126,0.015) 55%, rgba(0,0,0,0) 100%)",
        }}
      />
      {editing ? (
        // Editing mode: keep the chrome (size + border + glass) but skip the
        // body + footer so the shell's inline textarea sits cleanly on top.
        // Min height matches a body of ~5 lines so the card doesn't collapse
        // while the user types.
        <div aria-hidden="true" className="relative min-h-[150px]" />
      ) : (
        <div className="relative">
          <div className="px-4 pb-2.5 pt-3.5">
            <p className="line-clamp-5 whitespace-pre-wrap text-[13.5px] italic leading-[1.5] tracking-tight text-foreground/95">
              {body || (
                <span className="not-italic text-[11px] tracking-tight text-white/35">
                  Empty · double-click to write
                </span>
              )}
            </p>
          </div>
          {/* Quiet signature line — a tiny soft-ochre category dot + a
              sentence-case Inter label. Replaces the old mono-caps colored
              tear stamp that read "cheap". */}
          <div className="flex items-center gap-1.5 px-4 pb-3.5 pt-0.5">
            <span
              aria-hidden="true"
              className="h-[5px] w-[5px] shrink-0 rounded-full bg-atelier-ochre/45"
            />
            <span className="text-[10px] text-white/45">Idea · No {stampNum}</span>
          </div>
        </div>
      )}
    </div>
  );
}
