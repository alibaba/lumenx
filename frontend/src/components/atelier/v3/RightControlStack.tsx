"use client";
//
// RightControlStack — Flova/RON-style vertical canvas-control stack pinned to
// the right edge (target spec §4). Dark rounded icon buttons: zoom in / out /
// fit / minimap. Sits just LEFT of the Agent rail (offset by its width), since
// Atelier's right edge is occupied by the agent panel. Reuses the shell's
// existing zoom/fit/minimap handlers — pure chrome relocation + restyle.
import * as React from "react";
import { Plus, Minus, Maximize2, Map } from "lucide-react";

interface Props {
  zoom: number;
  onZoomChange: (z: number) => void;
  onFit: () => void;
  onToggleMinimap: () => void;
  minimapOpen?: boolean;
  /** px from the right edge (dodges the agent rail). */
  rightOffset: number;
}

// G3 · demo .ctrls — the zoom cluster is ONE floating glass stack; the
// buttons inside are quiet borderless cells (hover = soft white plate),
// not four separate bordered boxes.
const BTN =
  "btn-tip grid h-[34px] w-[34px] place-items-center rounded-[9px] text-white/55 transition-colors duration-[var(--at-dur-fast)] hover:bg-white/[0.08] hover:text-white active:scale-[0.94]";

export function RightControlStack({
  zoom,
  onZoomChange,
  onFit,
  onToggleMinimap,
  minimapOpen,
  rightOffset,
}: Props) {
  return (
    <div
      role="toolbar"
      aria-label="Canvas zoom controls"
      className="at-glass absolute z-30 flex flex-col gap-[2px] p-[5px] transition-[right] duration-200 ease-out"
      style={{ right: rightOffset, top: "50%", transform: "translateY(-50%)" }}
    >
      <button type="button" className={BTN} data-tip="Zoom in" aria-label="Zoom in" onClick={() => onZoomChange(Math.min(300, zoom + 10))}>
        <Plus size={15} aria-hidden="true" />
      </button>
      <button type="button" className={BTN} data-tip="Zoom out" aria-label="Zoom out" onClick={() => onZoomChange(Math.max(25, zoom - 10))}>
        <Minus size={15} aria-hidden="true" />
      </button>
      <button type="button" className={BTN} data-tip="Fit to view" aria-label="Fit to view" onClick={onFit}>
        <Maximize2 size={14} aria-hidden="true" />
      </button>
      <button
        type="button"
        className={`${BTN} ${minimapOpen ? "bg-white/10 text-white/90" : ""}`}
        data-tip="Minimap"
        aria-label="Toggle minimap"
        aria-pressed={!!minimapOpen}
        onClick={onToggleMinimap}
      >
        <Map size={14} aria-hidden="true" />
      </button>
    </div>
  );
}
