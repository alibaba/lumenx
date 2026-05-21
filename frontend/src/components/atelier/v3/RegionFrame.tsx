"use client";
//
// RegionFrame — the visual container for an Atelier region. Renders a
// translucent dashed-border frame with a 28px title bar at top and
// 4-corner resize handles. The frame is the container; child nodes are
// rendered separately by the shell on top of (z-index higher than) the
// region itself.
//
// This component is presentation-only: it surfaces drag-start /
// resize-start callbacks but doesn't track pointer movement itself.
// AtelierShellV3 owns the drag math (so it shares the same pointer
// loop used for normal node moves and can apply the world transform).
//
// Visual language follows DESIGN.md §6.x — quiet glass border at rest,
// primary ring on selection, dashed perforation on the body to read as
// "container, not ordinary node".

import * as React from "react";
import { useEffect, useRef, useState } from "react";

export type RegionColor =
  | "default"
  | "cyan"
  | "rose"
  | "amber"
  | "violet"
  | "emerald"
  | "slate";

interface Props {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  title: string;
  color?: RegionColor;
  selected?: boolean;
  /** Number of child nodes (data.region_id === this.id). Hidden when 0. */
  childCount?: number;
  onSelect?: (id: string) => void;
  onTitleCommit?: (next: string) => void;
  /** Fired when user presses on the title bar. Caller wires the move
   *  loop and calls store.moveRegion on commit. Args echo the event so
   *  the caller can compute world coords. */
  onMoveStart?: (id: string, event: React.PointerEvent) => void;
  /** Fired when user presses on a corner handle. Args identify which
   *  corner so the caller can apply the right resize direction. */
  onResizeStart?: (
    id: string,
    handle: "nw" | "ne" | "sw" | "se",
    event: React.PointerEvent,
  ) => void;
  /** Right-click on the frame body. Caller surfaces the menu. */
  onContextMenu?: (id: string, clientX: number, clientY: number) => void;
}

/** Map of region color → tailwind accent classes. Default is the quiet
 *  white-on-glass look used by every other v3 chrome surface. */
const COLOR_ACCENT: Record<RegionColor, string> = {
  default: "bg-white/[0.04] text-text-secondary",
  cyan: "bg-cyan-400/12 text-cyan-100",
  rose: "bg-rose-400/12 text-rose-100",
  amber: "bg-amber-400/14 text-amber-100",
  violet: "bg-violet-400/12 text-violet-100",
  emerald: "bg-emerald-400/12 text-emerald-100",
  slate: "bg-slate-400/14 text-slate-100",
};

const COLOR_BORDER: Record<RegionColor, string> = {
  default: "border-white/14",
  cyan: "border-cyan-400/30",
  rose: "border-rose-400/30",
  amber: "border-amber-400/30",
  violet: "border-violet-400/30",
  emerald: "border-emerald-400/30",
  slate: "border-slate-400/30",
};

const HANDLE_BASE =
  "absolute h-3 w-3 cursor-se-resize rounded-sm border border-white/30 bg-white/15 hover:bg-primary/40 hover:border-primary";

const HANDLE_POSITIONS: Record<
  "nw" | "ne" | "sw" | "se",
  { className: string; cursor: string }
> = {
  nw: { className: "left-[-6px] top-[-6px]", cursor: "cursor-nwse-resize" },
  ne: { className: "right-[-6px] top-[-6px]", cursor: "cursor-nesw-resize" },
  sw: { className: "left-[-6px] bottom-[-6px]", cursor: "cursor-nesw-resize" },
  se: { className: "right-[-6px] bottom-[-6px]", cursor: "cursor-nwse-resize" },
};

export function RegionFrame({
  id,
  x,
  y,
  width,
  height,
  title,
  color = "default",
  selected = false,
  childCount,
  onSelect,
  onTitleCommit,
  onMoveStart,
  onResizeStart,
  onContextMenu,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Re-sync local draft if the title prop changes underneath us (e.g.
  // another tab updated the region).
  useEffect(() => {
    if (!editing) setDraftTitle(title);
  }, [title, editing]);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const startEditing = () => {
    setDraftTitle(title);
    setEditing(true);
  };

  const cancel = () => {
    setEditing(false);
    setDraftTitle(title);
  };

  const commit = () => {
    setEditing(false);
    if (draftTitle !== title) onTitleCommit?.(draftTitle);
  };

  const ringClass = selected
    ? "ring-2 ring-primary"
    : "ring-1 ring-transparent";

  const accent = COLOR_ACCENT[color];
  const border = COLOR_BORDER[color];

  // The displayed title text — empty falls back to "Region" so the chrome
  // never reads as a void rectangle in the wild.
  const displayedTitle = title.trim().length > 0 ? title : "Region";

  return (
    <div
      role="group"
      aria-label={`Region: ${displayedTitle}`}
      data-region-id={id}
      data-region-color={color}
      className={`absolute select-none rounded-md border border-dashed ${border} bg-[#0c0c10]/30 backdrop-blur-[1px] transition-shadow ${ringClass}`}
      style={{
        transform: `translate(${x}px, ${y}px)`,
        width: `${width}px`,
        height: `${height}px`,
      }}
      onPointerDown={(e) => {
        // Body pointerDown selects the region but DOES NOT initiate a
        // move — title bar handles drag. This separation lets users
        // click inside a region (e.g. on empty area) to select it
        // without accidentally dragging it.
        e.stopPropagation();
        onSelect?.(id);
      }}
      onContextMenu={(e) => {
        if (!onContextMenu) return;
        e.preventDefault();
        e.stopPropagation();
        onContextMenu(id, e.clientX, e.clientY);
      }}
    >
      {/* Title bar — draggable, inline-editable. h-7 (28px). */}
      <div
        data-testid="region-title-bar"
        className={`flex h-7 items-center gap-2 rounded-t-md border-b border-dashed ${border} ${accent} px-2 cursor-grab active:cursor-grabbing`}
        onPointerDown={(e) => {
          e.stopPropagation();
          onMoveStart?.(id, e);
        }}
        onDoubleClick={(e) => {
          e.stopPropagation();
          if (!editing) startEditing();
        }}
      >
        {/* Color dot — gives the user a quick visual id at glance even
            when the title is faded. Color comes from the accent palette
            so it matches the title bg. */}
        <span
          aria-hidden="true"
          className={`h-2 w-2 rounded-full ${color === "default" ? "bg-white/40" : ""}`}
          data-region-color-dot={color}
        />
        {editing ? (
          <input
            ref={inputRef}
            aria-label="Region title"
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onPointerDown={(e) => e.stopPropagation()}
            onBlur={commit}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") {
                e.preventDefault();
                commit();
              } else if (e.key === "Escape") {
                e.preventDefault();
                cancel();
              }
            }}
            className="flex-1 bg-transparent font-mono text-[11px] uppercase tracking-[0.18em] text-foreground outline-none"
          />
        ) : (
          <span className="flex-1 truncate font-mono text-[11px] uppercase tracking-[0.18em]">
            {displayedTitle}
          </span>
        )}
        {childCount && childCount > 0 ? (
          <span
            data-testid="region-child-count"
            aria-label={`${childCount} child node${childCount === 1 ? "" : "s"}`}
            className="rounded-full bg-white/10 px-1.5 py-px font-mono text-[9px] tracking-wider text-text-muted"
          >
            {childCount}
          </span>
        ) : null}
      </div>

      {/* 4 corner resize handles. Hidden until the region is selected to
          keep the resting visual quiet (DESIGN.md §6.1). */}
      {selected && onResizeStart
        ? (Object.keys(HANDLE_POSITIONS) as Array<keyof typeof HANDLE_POSITIONS>).map(
            (corner) => {
              const def = HANDLE_POSITIONS[corner];
              return (
                <span
                  key={corner}
                  data-testid={`region-handle-${corner}`}
                  className={`${HANDLE_BASE} ${def.className} ${def.cursor}`}
                  onPointerDown={(e) => {
                    e.stopPropagation();
                    onResizeStart(id, corner, e);
                  }}
                />
              );
            },
          )
        : null}
    </div>
  );
}
