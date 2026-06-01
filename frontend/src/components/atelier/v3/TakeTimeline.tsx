"use client";
//
// TakeTimeline — horizontal strip of every candidate (take) generated
// from a draft, in chronological order, click to set as primary.
//
// Rendered as an inline row inside DraftWorkbench (between the Composer
// and the status footer). Each take is a 36×36 button: thumbnail when
// available, otherwise a status-toned placeholder (processing / failed
// / completed without media). The selected take wears a primary ring +
// aria-pressed=true.
//
// This is the per-node "version timeline" sibling of the canvas-tile
// candidate spread — the spread is for spatial manipulation (drag,
// attach as ref, etc.), the timeline is for fast take navigation.

import { useState } from "react";
import { Loader2, AlertTriangle, Check } from "lucide-react";

export type TakeStatus = "completed" | "processing" | "pending" | "failed" | "draft";

export interface TakeTimelineEntry {
  id: string;
  thumbUrl?: string;
  status: TakeStatus;
  selected: boolean;
  createdAt: number;
}

interface Props {
  takes: TakeTimelineEntry[];
  onPickTake: (takeId: string) => void;
}

const STATUS_BG: Record<TakeStatus, string> = {
  completed: "bg-atelier-completed/15 text-atelier-completed",
  processing: "bg-atelier-processing/15 text-atelier-processing",
  pending: "bg-atelier-processing/15 text-atelier-processing",
  failed: "bg-atelier-failed/15 text-atelier-failed",
  draft: "bg-white/[0.05] text-text-muted",
};

function TakePlaceholder({ status }: { status: TakeStatus }) {
  if (status === "processing" || status === "pending") {
    return <Loader2 size={16} aria-hidden="true" className="animate-spin" />;
  }
  if (status === "failed") {
    return <AlertTriangle size={16} aria-hidden="true" />;
  }
  if (status === "completed") {
    return <Check size={16} aria-hidden="true" />;
  }
  // draft / unknown — quiet dot
  return <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-white/35" />;
}

// v4 ④ "celebrate output": takes render as a polaroid PILE by default — offset +
// gently rotated cards with the selected take on top (a count badge sits on the
// pile). Hover / keyboard-focus FANS the pile into a row of larger 16:9 tiles for
// navigation. The inspiration ("output deserves the biggest, most beautiful
// surface") flagged the old flat 36×36 strip as underselling the result; this
// gives the takes weight without restructuring the on-canvas candidate spread.
const TILE_W = 72; // 16:9 at h-10 (40px)

export function TakeTimeline({ takes, onPickTake }: Props) {
  const [open, setOpen] = useState(false);
  if (takes.length === 0) return null;
  // Stable chronological order, oldest first — the user reads
  // left-to-right as "earliest attempt → latest attempt". Caller order
  // is not trusted (regenerates may push to the front).
  const ordered = [...takes].sort((a, b) => a.createdAt - b.createdAt);

  return (
    <div
      role="toolbar"
      aria-label="Take version timeline"
      className="flex items-center gap-2 border-t border-dashed border-white/8 px-4 py-2.5"
      onPointerDown={(e) => e.stopPropagation()}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocusCapture={() => setOpen(true)}
      onBlurCapture={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false);
      }}
    >
      <span
        aria-hidden="true"
        className="shrink-0 text-[11px] text-white/45"
      >
        Takes · {ordered.length}
      </span>
      {/* The pile / fan. overflow-x-auto only matters when fanned (open). */}
      <div className={`flex min-w-0 items-center ${open ? "overflow-x-auto" : "overflow-visible"}`}>
        {ordered.map((take, i) => {
          const isSelected = take.selected;
          // Collapsed = polaroid pile: overlap (negative margin) + a gentle
          // alternating tilt; the selected take floats to the top (highest z)
          // so the current result is what you see on the pile. Open = flat fan.
          const collapsed = !open;
          const marginLeft = i === 0 ? 0 : collapsed ? -(TILE_W - 16) : 6;
          const tilt = collapsed ? (i % 2 === 0 ? -2 : 2) * Math.min(1.6, 0.6 + i * 0.25) : 0;
          const z = isSelected ? 60 : collapsed ? ordered.length - i : i;
          const ringClass = isSelected
            ? "ring-1 ring-white/25 border-white/20"
            : "border-white/10 hover:border-white/25";
          return (
            <button
              key={take.id}
              type="button"
              aria-label={`Take ${i + 1}${isSelected ? " (current)" : ""}`}
              aria-pressed={isSelected}
              data-take-id={take.id}
              data-take-status={take.status}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onPickTake(take.id);
              }}
              style={{
                marginLeft,
                transform: `rotate(${tilt}deg)`,
                zIndex: z,
                transition:
                  "margin-left 240ms cubic-bezier(0.22,1,0.36,1), transform 240ms cubic-bezier(0.22,1,0.36,1)",
              }}
              className={`relative grid h-10 w-[72px] shrink-0 origin-bottom place-items-center overflow-hidden rounded-[5px] border bg-black/40 shadow-[0_4px_12px_-6px_rgba(0,0,0,0.7)] ${ringClass}`}
            >
              {take.thumbUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={take.thumbUrl}
                  alt=""
                  className="h-full w-full object-cover"
                  draggable={false}
                />
              ) : (
                <span className={`grid h-full w-full place-items-center ${STATUS_BG[take.status]}`}>
                  <TakePlaceholder status={take.status} />
                </span>
              )}
              {/* current-take check badge — output you've chosen reads at a glance */}
              {isSelected ? (
                <span
                  aria-hidden="true"
                  className="absolute bottom-0.5 left-0.5 grid h-3.5 w-3.5 place-items-center rounded-full bg-atelier-brand-400 text-white shadow-[0_0_0_1.5px_rgba(0,0,0,0.5)]"
                >
                  <Check size={9} />
                </span>
              ) : null}
              {/* status dot for in-flight / failed takes that still show a thumb */}
              {take.thumbUrl && (take.status === "failed" || take.status === "processing" || take.status === "pending") ? (
                <span
                  aria-hidden="true"
                  className={`absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full ${
                    take.status === "failed" ? "bg-atelier-failed" : "bg-atelier-processing animate-pulse"
                  }`}
                />
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
