"use client";
//
// RegionFrame — visual container for an Atelier region.
//
// Two visual states:
//   - Expanded (default): a translucent dashed-border frame with a
//     28px title bar at top and 4-corner resize handles when selected.
//     Child nodes are rendered separately (z-order higher) so they
//     paint on top of the frame.
//   - Collapsed (B-β): the same frame compresses to a fixed 200×80
//     mini-tile at the same anchor position. Title + child-count chip
//     + status dot + first-N thumbnails are still visible so the user
//     can recognize the region at a glance. Resize handles disappear.
//
// This component stays presentation-only. AtelierShellV3 owns the
// pointer math for move/resize and the data flow for collapsed toggle
// (persists as `data.collapsed` on the region node).

import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export type RegionColor =
  | "default"
  | "cyan"
  | "rose"
  | "amber"
  | "violet"
  | "emerald"
  | "slate";

export type RegionStatusBadge = "idle" | "processing" | "completed" | "failed";

/** Compact card geometry used in collapsed state. Fixed so visually a
 *  collapsed region always reads the same size regardless of how big it
 *  was when expanded — that's the LibTV pattern. */
export const REGION_COLLAPSED_WIDTH = 200;
export const REGION_COLLAPSED_HEIGHT = 80;

interface Props {
  id: string;
  x: number;
  y: number;
  /** Stored full-size width/height. Used when expanded. When collapsed,
   *  the component renders at REGION_COLLAPSED_WIDTH × _HEIGHT and the
   *  stored values are preserved (the shell does not write them on
   *  collapse — re-expanding restores the original frame). */
  width: number;
  height: number;
  title: string;
  color?: RegionColor;
  selected?: boolean;
  /** Number of child nodes (data.region_id === this.id). Hidden when 0. */
  childCount?: number;
  /** B-β: whether the region is currently collapsed to a mini-tile. */
  collapsed?: boolean;
  /** B-β: aggregated child-status used to color the status dot. */
  statusBadge?: RegionStatusBadge;
  /** B-β: media URLs to render in the collapsed mini-tile thumbnail
   *  strip. Up to 3 are shown; pass already-resolved URLs (no further
   *  transformation happens inside the component). */
  thumbnails?: string[];
  onSelect?: (id: string) => void;
  onTitleCommit?: (next: string) => void;
  /** B-β: toggle the collapsed state. Caller writes back to data.collapsed. */
  onToggleCollapse?: (id: string) => void;
  /** Fired when user presses on the title bar. Caller wires the move
   *  loop and calls store.moveRegion on commit. */
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

const STATUS_DOT: Record<RegionStatusBadge, string> = {
  // Subtle but recognizable. Processing gets an animate-pulse so the
  // region's "I have work in flight" reads at a glance.
  idle: "bg-white/35",
  processing: "bg-blue-400/85 animate-pulse",
  completed: "bg-emerald-400/85",
  failed: "bg-red-400/85",
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
  collapsed = false,
  statusBadge,
  thumbnails,
  onSelect,
  onTitleCommit,
  onToggleCollapse,
  onMoveStart,
  onResizeStart,
  onContextMenu,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const inputRef = useRef<HTMLInputElement | null>(null);

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

  const ringClass = selected ? "ring-2 ring-primary" : "ring-1 ring-transparent";
  const accent = COLOR_ACCENT[color];
  const border = COLOR_BORDER[color];
  const displayedTitle = title.trim().length > 0 ? title : "Region";

  // Render geometry: collapsed mode locks to the compact card.
  const renderW = collapsed ? REGION_COLLAPSED_WIDTH : width;
  const renderH = collapsed ? REGION_COLLAPSED_HEIGHT : height;

  // Thumbnails strip in collapsed mode: cap at 3 to fit the 200px card
  // with the title row above. We don't decorate or fetch — the shell
  // resolves URLs (e.g. through getAssetUrl) before passing them in.
  const visibleThumbs = collapsed ? (thumbnails ?? []).slice(0, 3) : [];

  return (
    <div
      role="group"
      aria-label={`Region: ${displayedTitle}`}
      data-region-id={id}
      data-region-color={color}
      data-region-collapsed={collapsed ? "true" : "false"}
      className={`absolute select-none rounded-md border border-dashed ${border} bg-[#0c0c10]/30 backdrop-blur-[1px] transition-shadow ${ringClass}`}
      style={{
        transform: `translate(${x}px, ${y}px)`,
        width: `${renderW}px`,
        height: `${renderH}px`,
      }}
      onPointerDown={(e) => {
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
        className={`flex h-7 items-center gap-1.5 rounded-t-md border-b border-dashed ${border} ${accent} px-1.5 cursor-grab active:cursor-grabbing`}
        onPointerDown={(e) => {
          e.stopPropagation();
          onMoveStart?.(id, e);
        }}
        onDoubleClick={(e) => {
          e.stopPropagation();
          if (!editing) startEditing();
        }}
      >
        {/* B-β: collapse / expand toggle. Chevron-down when expanded
            (action = collapse), chevron-right when collapsed (action =
            expand). Aria-label flips so screen readers track state. */}
        {onToggleCollapse ? (
          <button
            type="button"
            aria-label={collapsed ? "Expand region" : "Collapse region"}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onToggleCollapse(id);
            }}
            className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-sm text-text-muted hover:bg-white/[0.08] hover:text-foreground"
          >
            {collapsed ? <ChevronRight size={11} aria-hidden="true" /> : <ChevronDown size={11} aria-hidden="true" />}
          </button>
        ) : null}
        {/* Color dot — quick visual id. The status badge (when set)
            replaces it so the user gets the most informative signal. */}
        {statusBadge ? (
          <span
            aria-hidden="true"
            data-region-status={statusBadge}
            className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[statusBadge]}`}
          />
        ) : (
          <span
            aria-hidden="true"
            className={`h-2 w-2 shrink-0 rounded-full ${color === "default" ? "bg-white/40" : ""}`}
            data-region-color-dot={color}
          />
        )}
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

      {/* B-β: collapsed-only thumbnail strip. Fills the body below the
          title bar with up to 3 tiny media previews so the user can
          recognize "which region is this" without re-expanding. */}
      {collapsed && visibleThumbs.length > 0 ? (
        <div className="flex h-[52px] items-center gap-1 px-2">
          {visibleThumbs.map((url, idx) => (
            <span
              key={`${url}-${idx}`}
              className="inline-flex h-9 w-12 shrink-0 overflow-hidden rounded-sm border border-white/8 bg-black/40"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={url}
                alt={`thumb ${idx + 1}`}
                className="h-full w-full object-cover"
                draggable={false}
              />
            </span>
          ))}
        </div>
      ) : null}

      {/* 4 corner resize handles. Hidden when collapsed (compact card has
          fixed geometry) or unselected. */}
      {!collapsed && selected && onResizeStart
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
