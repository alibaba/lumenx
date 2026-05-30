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
    return <Loader2 size={14} aria-hidden="true" className="animate-spin" />;
  }
  if (status === "failed") {
    return <AlertTriangle size={14} aria-hidden="true" />;
  }
  if (status === "completed") {
    return <Check size={14} aria-hidden="true" />;
  }
  // draft / unknown — quiet dot
  return <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-white/35" />;
}

export function TakeTimeline({ takes, onPickTake }: Props) {
  if (takes.length === 0) return null;
  // Stable chronological order, oldest first — the user reads
  // left-to-right as "earliest attempt → latest attempt". Caller order
  // is not trusted (regenerates may push to the front).
  const ordered = [...takes].sort((a, b) => a.createdAt - b.createdAt);

  return (
    <div
      role="toolbar"
      aria-label="Take version timeline"
      className="flex items-center gap-1.5 overflow-x-auto border-t border-dashed border-white/8 px-4 py-2"
      onPointerDown={(e) => e.stopPropagation()}
    >
      <span
        aria-hidden="true"
        className="shrink-0 font-mono text-[9px] uppercase tracking-[0.22em] text-text-muted/85"
      >
        Takes
      </span>
      {ordered.map((take) => {
        const isSelected = take.selected;
        const ringClass = isSelected
          ? "ring-2 ring-atelier-brand-400 border-atelier-brand-400/60"
          : "border-white/8 hover:border-white/22";
        return (
          <button
            key={take.id}
            type="button"
            aria-label={`Take ${take.id}`}
            aria-pressed={isSelected}
            data-take-id={take.id}
            data-take-status={take.status}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onPickTake(take.id);
            }}
            className={`relative grid h-9 w-9 shrink-0 place-items-center overflow-hidden rounded-sm border bg-black/40 transition-all ${ringClass}`}
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
              <span
                className={`grid h-full w-full place-items-center ${STATUS_BG[take.status]}`}
              >
                <TakePlaceholder status={take.status} />
              </span>
            )}
            {/* Status dot on top-right corner; only shown when there's
                a thumbnail (otherwise the placeholder icon already
                conveys status). */}
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
  );
}
