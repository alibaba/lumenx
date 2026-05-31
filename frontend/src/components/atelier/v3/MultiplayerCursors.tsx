"use client";
//
// MultiplayerCursors — decorative collaborative-presence layer (target spec §4 /
// §9.9). A couple of remote-cursor pills (a tiny pointer arrow + a rounded name
// chip) pinned in world coordinates, evoking a live multiplayer session. Purely
// presentational: no state, no realtime back-end. The shell mounts this inside
// the transformed world layer so each item's translate(x,y) reads as a canvas
// coordinate, matching the node-positioning convention.
//
// Craft notes (spec §9): sentence-case Inter at 11px (NO mono-caps), a soft
// colour-tinted shadow, a rounded-full chip, and a subtle white-outlined arrow.
// Restrained — the only colour is the per-cursor hue.
import * as React from "react";

export interface CursorPresence {
  /** Display name shown in the chip (sentence case, e.g. "Kate"). */
  name: string;
  /** CSS colour for the arrow + chip fill (e.g. "#5b9dff"). */
  color: string;
  /** World-space x of the pointer tip. */
  x: number;
  /** World-space y of the pointer tip. */
  y: number;
}

interface Props {
  items?: CursorPresence[];
}

// Sensible default presence — Kate (blue) + Mario (pink), placed a little apart
// in world space so they read as two different collaborators on the board.
const DEFAULT_CURSORS: CursorPresence[] = [
  { name: "Kate", color: "#5b9dff", x: 520, y: 360 },
  { name: "Mario", color: "#f06fb0", x: 780, y: 540 },
];

export function MultiplayerCursors({ items = DEFAULT_CURSORS }: Props) {
  return (
    <>
      {items.map((cursor) => (
        <div
          key={`${cursor.name}-${cursor.x}-${cursor.y}`}
          aria-hidden="true"
          className="pointer-events-none absolute left-0 top-0 z-40 select-none"
          style={{ transform: `translate(${cursor.x}px, ${cursor.y}px)` }}
        >
          {/* Pointer arrow — filled with the collaborator hue, hairline white
              outline for crispness on the dark canvas, soft drop shadow. */}
          <svg
            width="20"
            height="20"
            viewBox="0 0 20 20"
            fill="none"
            className="block"
            style={{ filter: "drop-shadow(0 2px 3px rgba(0,0,0,0.45))" }}
          >
            <path
              d="M4 3 L4 17.4 L8.1 13.5 L10.8 18.9 L13.2 17.8 L10.5 12.5 L16.3 12.1 Z"
              fill={cursor.color}
              stroke="rgba(255,255,255,0.92)"
              strokeWidth="1.1"
              strokeLinejoin="round"
            />
          </svg>
          {/* Name chip — sits just below-right of the arrow tip. */}
          <span
            className="absolute left-[13px] top-[15px] inline-block whitespace-nowrap rounded-full px-2 py-[3px] font-sans text-[11px] font-medium leading-none text-white"
            style={{
              backgroundColor: cursor.color,
              boxShadow: `0 4px 12px -2px ${cursor.color}66, 0 2px 5px -2px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.22)`,
            }}
          >
            {cursor.name}
          </span>
        </div>
      ))}
    </>
  );
}
