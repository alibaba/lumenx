"use client";
import * as React from "react";
import { TearLine } from "./ornaments";

interface Props {
  id: string;
  body: string;
  selected?: boolean;
  x: number;
  y: number;
  onSelect?: (id: string) => void;
  /** When true, the shell is overlaying an inline textarea editor — render
   *  only the chrome shell so the editor sits cleanly on top. Skipping the
   *  body + tear footer prevents the duplicated text the user reported. */
  editing?: boolean;
}

// Idea node — read as a torn slip of paper from a notebook. The body sits
// in the upper register in a near-handwritten italic display tone, then a
// dashed perforation + "IDEA · NO" tear stamp anchors the bottom edge so
// the wall of ideas in a brainstorm session feels tactile, not like a
// stack of UI cards.
export function IdeaNode({ id, body, selected, x, y, onSelect, editing }: Props) {
  const borderClass = selected
    ? "ring-2 ring-atelier-brand-400 border-atelier-brand-400/45"
    : "border-atelier-ochre/15";
  // Take the first 3 chars of the node id and display them as a stamped
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
      style={{
        transform: `translate(${x}px, ${y}px)`,
        backgroundImage:
          "linear-gradient(155deg, rgba(201,168,126,0.06) 0%, rgba(201,168,126,0.02) 60%, rgba(0,0,0,0) 100%)",
      }}
      className={`group absolute w-[224px] overflow-hidden rounded-[10px] border bg-[#1a1611] shadow-[0_14px_36px_-22px_rgba(0,0,0,0.7),0_2px_4px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(201,168,126,0.06)] transition-[box-shadow,border-color] duration-200 ease-out ${borderClass}`}
    >
      {editing ? (
        // Editing mode: keep the chrome (size + border + bg) but skip the
        // body + tear-stamp so the shell's inline textarea sits cleanly
        // on top. Min height matches a body of ~5 lines so the card doesn't
        // collapse while the user types.
        <div aria-hidden="true" className="min-h-[140px]" />
      ) : (
        <>
          <div className="px-3.5 pb-2 pt-3">
            <p className="line-clamp-5 whitespace-pre-wrap text-[13.5px] italic leading-[1.5] tracking-tight text-foreground/95">
              {body || (
                <span className="not-italic font-mono text-[10.5px] uppercase tracking-[0.22em] text-atelier-ochre/70">
                  empty · double-click
                </span>
              )}
            </p>
          </div>
          {/* Tear-stamp footer: dashed perforation flanking a "IDEA · NO XXX"
              centered cap. Reads as the bottom of a torn-off receipt slip. */}
          <div className="px-3 pb-2.5">
            <TearLine tone="amber" label={`Idea · No ${stampNum}`} />
          </div>
        </>
      )}
    </div>
  );
}
