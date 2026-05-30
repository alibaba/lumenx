"use client";
//
// RailPanel — generic slide-out panel that renders next to the
// LeftRailV3. Handles the chrome (header / close button / dashed
// editorial slip), positioning, and animation. Each mode supplies its
// own body content as `children`.
//
// Geometry: anchored at left:56 (rail width) so it docks against the
// rail's right edge. Width 320 — wide enough for a search input + a
// 2-column grid (asset library / workflow templates) but narrow enough
// that the canvas still owns most of the screen.
import * as React from "react";
import { X } from "lucide-react";

interface Props {
  open: boolean;
  title: string;
  /** Mono-caps tag shown above the title — keeps the panel chrome
   *  visually consistent with the rest of Atelier (Composer header,
   *  Asset Library, etc.). */
  tag?: string;
  onClose: () => void;
  children: React.ReactNode;
}

export function RailPanel({ open, title, tag, onClose, children }: Props) {
  if (!open) return null;
  return (
    <aside
      role="region"
      aria-label={title}
      className="absolute left-[56px] top-4 bottom-4 z-30 flex w-[320px] flex-col overflow-hidden rounded-2xl border border-white/8 atelier-chrome-opaque shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.05)] animate-atelier-popover-in motion-reduce:animate-none"
    >
      <div aria-hidden="true" className="h-[2px] shrink-0 bg-gradient-to-r from-atelier-brand-400/85 via-atelier-brand-400/35 to-transparent" />

      {tag ? (
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-dashed border-white/8 px-3.5 py-1.5">
          <span aria-hidden="true" className="font-mono text-[8.5px] font-medium uppercase tracking-[0.32em] text-text-muted/85">
            {tag}
          </span>
          <button
            type="button"
            aria-label="Close panel"
            data-tip="Close"
            onClick={onClose}
            className="btn-tip inline-flex h-6 w-6 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
          >
            <X size={11} aria-hidden="true" />
          </button>
        </div>
      ) : null}

      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-white/6 px-3.5 py-2.5">
        <div className="font-display text-[14px] font-medium tracking-[-0.005em] text-foreground">
          {title}
        </div>
        {!tag ? (
          <button
            type="button"
            aria-label="Close panel"
            data-tip="Close"
            onClick={onClose}
            className="btn-tip inline-flex h-6 w-6 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
          >
            <X size={11} aria-hidden="true" />
          </button>
        ) : null}
      </header>

      <div className="flex-1 overflow-y-auto">{children}</div>
    </aside>
  );
}
