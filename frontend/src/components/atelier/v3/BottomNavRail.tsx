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
  const minimapClass = minimapOpen
    ? "btn-tip inline-flex items-center justify-center rounded-md p-1.5 transition bg-hover-bg text-foreground"
    : "btn-tip inline-flex items-center justify-center rounded-md p-1.5 text-text-secondary transition hover:bg-hover-bg hover:text-foreground";

  return (
    <div
      role="toolbar"
      aria-label="Canvas navigation"
      className="absolute left-4 bottom-4 z-30 flex items-center gap-1 rounded-full border border-glass-border bg-glass p-1 backdrop-blur-md"
    >
      <button
        type="button"
        aria-label="Toggle minimap"
        aria-pressed={!!minimapOpen}
        data-tip="Toggle minimap"
        onClick={onToggleMinimap}
        className={minimapClass}
      >
        <Map size={14} />
      </button>
      <span className="mx-1 h-5 w-px bg-glass-border" />
      <button
        type="button"
        aria-label="Fit view"
        data-tip="Fit view (F)"
        onClick={onFit}
        className="btn-tip inline-flex items-center justify-center rounded-md p-1.5 text-text-secondary transition hover:bg-hover-bg hover:text-foreground"
      >
        <Maximize size={14} />
      </button>
      <button
        type="button"
        aria-label="Zoom out"
        data-tip="Zoom out"
        onClick={() => onZoomChange(Math.max(25, zoom - 25))}
        className="btn-tip inline-flex items-center justify-center rounded-md p-1.5 text-text-secondary transition hover:bg-hover-bg hover:text-foreground"
      >
        <ZoomOut size={14} />
      </button>
      <input
        type="range"
        min={25}
        max={300}
        step={5}
        value={zoom}
        aria-label="Zoom level"
        onChange={(e) => onZoomChange(Number(e.target.value))}
        className="h-1 w-24 cursor-pointer appearance-none rounded-full bg-white/10 accent-primary"
      />
      <button
        type="button"
        aria-label="Zoom in"
        data-tip="Zoom in"
        onClick={() => onZoomChange(Math.min(300, zoom + 25))}
        className="btn-tip inline-flex items-center justify-center rounded-md p-1.5 text-text-secondary transition hover:bg-hover-bg hover:text-foreground"
      >
        <ZoomIn size={14} />
      </button>
      <button
        type="button"
        aria-label="Reset zoom to 100%"
        data-tip="Reset to 100%"
        onClick={() => onZoomChange(100)}
        className="btn-tip font-mono text-[10px] text-text-muted hover:text-foreground w-9 text-right"
        disabled={zoom === 100}
      >
        {zoom}%
      </button>
    </div>
  );
}
