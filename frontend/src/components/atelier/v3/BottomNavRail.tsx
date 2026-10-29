"use client";
import * as React from "react";
import { Grid3x3, LayoutGrid, Map, Maximize, ZoomIn, ZoomOut } from "lucide-react";

interface BottomNavRailProps {
  zoom: number;
  onZoomChange: (z: number) => void;
  onFit: () => void;
  onToggleMinimap: () => void;
  minimapOpen?: boolean;
  /** P2 (E'): persistent grid-snap toggle. When on, drags snap to the
   *  GRID multiple even without holding Shift. Defaults to false to
   *  match the original Shift-only behavior. */
  gridSnap?: boolean;
  onToggleGridSnap?: () => void;
  /** P2 (E'): one-click "tidy up" button. Caller decides what to
   *  arrange (selection vs whole canvas). */
  onAutoArrange?: () => void;
}

export function BottomNavRail({
  zoom,
  onZoomChange,
  onFit,
  onToggleMinimap,
  minimapOpen,
  gridSnap,
  onToggleGridSnap,
  onAutoArrange,
}: BottomNavRailProps) {
  const HIT =
    "btn-tip inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors";
  const HIT_QUIET = `${HIT} text-text-secondary hover:bg-hover-bg hover:text-foreground`;
  const minimapClass = minimapOpen
    ? `${HIT} bg-hover-bg text-foreground`
    : HIT_QUIET;
  const snapClass = gridSnap ? `${HIT} bg-hover-bg text-foreground` : HIT_QUIET;

  return (
    <div
      role="toolbar"
      aria-label="Canvas navigation"
      // v0.6.2 — RHTV bare-canvas: dropped the rounded-full border + bg
      // + shadow pill. The controls float directly on the canvas; each
      // already carries its own hover plate via HIT_QUIET / snap / minimap
      // classes, so the per-element hover affordance is intact while the
      // permanent pill chrome is gone.
      // left-[80px] clears the 64px-wide left icon rail + a 16px gutter
      // (mockup .bottom-rail sits at left:78px for the same reason).
      // max-width caps the rail to the lane LEFT of the centered composer
      // (composer half-width clamp mirrored, 80px start subtracted) — the
      // ghost hint used to run under the composer and read as an overlap.
      // The hint span shrinks first; 280px floor keeps every button alive
      // on narrow viewports.
      className="at-glass absolute left-[80px] bottom-4 z-30 flex h-[42px] max-w-[max(280px,calc(50vw-446px))] items-center gap-0.5 overflow-hidden px-2"
    >
      <button
        type="button"
        aria-label="Toggle minimap"
        aria-pressed={!!minimapOpen}
        data-tip="Toggle minimap"
        onClick={onToggleMinimap}
        className={minimapClass}
      >
        <Map size={13} aria-hidden="true" />
      </button>
      {onToggleGridSnap ? (
        <button
          type="button"
          aria-label="Toggle grid snap"
          aria-pressed={!!gridSnap}
          data-tip={gridSnap ? "Grid snap on (drags lock to grid)" : "Grid snap off (Shift to snap)"}
          onClick={onToggleGridSnap}
          className={snapClass}
        >
          <Grid3x3 size={13} aria-hidden="true" />
        </button>
      ) : null}
      {onAutoArrange ? (
        <button
          type="button"
          aria-label="Auto-arrange"
          data-tip="Auto-arrange (⌥⇧F)"
          onClick={onAutoArrange}
          className={HIT_QUIET}
        >
          <LayoutGrid size={13} aria-hidden="true" />
        </button>
      ) : null}
      <span aria-hidden="true" className="mx-1 h-4 w-px bg-white/[0.08]" />
      <button
        type="button"
        aria-label="Fit view"
        data-tip="Fit view (F)"
        onClick={onFit}
        className={HIT_QUIET}
      >
        <Maximize size={13} aria-hidden="true" />
      </button>
      <button
        type="button"
        aria-label="Zoom out"
        data-tip="Zoom out"
        onClick={() => onZoomChange(Math.max(25, zoom - 25))}
        className={HIT_QUIET}
      >
        <ZoomOut size={13} aria-hidden="true" />
      </button>
      {/* v0.4.5 §12.7 / round-6: custom-styled via vendor pseudo-elements
          in globals.css — 9px sky-soft thumb + 2px hairline track,
          hover-only brightening. Replaces OS-default chunky cobalt thumb. */}
      <input
        type="range"
        min={25}
        max={300}
        step={5}
        value={zoom}
        aria-label="Zoom level"
        onChange={(e) => onZoomChange(Number(e.target.value))}
        className="atelier-zoom-slider"
      />
      <button
        type="button"
        aria-label="Zoom in"
        data-tip="Zoom in"
        onClick={() => onZoomChange(Math.min(300, zoom + 25))}
        className={HIT_QUIET}
      >
        <ZoomIn size={13} aria-hidden="true" />
      </button>
      {/* Zoom readout — typewriter-style "ZOOM · 100" reads as a meter
          marking, not a percent sign you've seen on every SaaS app. */}
      <button
        type="button"
        aria-label="Reset zoom to 100%"
        data-tip="Reset to 100%"
        onClick={() => onZoomChange(100)}
        className="btn-tip ml-0.5 inline-flex h-6 items-center gap-1 rounded px-2 text-[11px] text-white/55 transition-colors hover:bg-white/[0.06] hover:text-foreground disabled:hover:bg-transparent disabled:hover:text-white/55"
        disabled={zoom === 100}
      >
        <span aria-hidden="true">Zoom</span>
        <span aria-hidden="true" className="text-text-muted/55">·</span>
        <span className="font-display text-[11px] tracking-tight">{zoom}</span>
      </button>
      {/* PX · demo zoomhud ghost hint — quiet interaction affordances that
          ride after the zoom cluster (demo: `90% · 双击建节点 · 右键菜单…`).
          Pure text, zero chrome; shrinks to nothing before the rail may
          touch the composer. */}
      <span
        aria-hidden="true"
        className="ml-2 hidden min-w-0 select-none overflow-hidden text-ellipsis whitespace-nowrap text-[11px] text-white/25 lg:inline-block"
      >
        Double-click to add · Right-click for menu
      </span>
    </div>
  );
}
