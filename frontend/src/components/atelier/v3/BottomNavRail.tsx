"use client";
import * as React from "react";
import { Map, Maximize, ZoomIn, ZoomOut } from "lucide-react";

interface BottomNavRailProps {
  zoom: number;
  onZoomChange: (z: number) => void;
  onFit: () => void;
  onToggleMinimap: () => void;
  minimapOpen?: boolean;
}

export function BottomNavRail({
  zoom,
  onZoomChange,
  onFit,
  onToggleMinimap,
  minimapOpen,
}: BottomNavRailProps) {
  const HIT =
    "btn-tip inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors";
  const HIT_QUIET = `${HIT} text-text-secondary hover:bg-hover-bg hover:text-foreground`;
  const minimapClass = minimapOpen
    ? `${HIT} bg-hover-bg text-foreground`
    : HIT_QUIET;

  return (
    <div
      role="toolbar"
      aria-label="Canvas navigation"
      className="absolute left-4 bottom-4 z-30 flex h-9 items-center gap-0.5 rounded-full border border-white/8 bg-[#141416]/96 px-1 shadow-[0_14px_30px_-18px_rgba(0,0,0,0.7),0_2px_6px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl"
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
      <span aria-hidden="true" className="mx-1 h-4 w-px bg-white/8" />
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
      <input
        type="range"
        min={25}
        max={300}
        step={5}
        value={zoom}
        aria-label="Zoom level"
        onChange={(e) => onZoomChange(Number(e.target.value))}
        className="mx-0.5 h-1 w-24 cursor-pointer appearance-none rounded-full bg-white/8 accent-primary"
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
        className="btn-tip ml-0.5 inline-flex h-6 items-center gap-1 rounded px-1.5 font-mono text-[9.5px] font-medium uppercase tracking-[0.22em] text-text-muted/85 transition-colors hover:bg-white/[0.06] hover:text-foreground disabled:hover:bg-transparent disabled:hover:text-text-muted/85"
        disabled={zoom === 100}
      >
        <span aria-hidden="true">Zoom</span>
        <span aria-hidden="true" className="text-text-muted/55">·</span>
        <span className="font-display text-[11px] tracking-tight">{zoom}</span>
      </button>
    </div>
  );
}
