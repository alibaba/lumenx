"use client";
import * as React from "react";

interface MinimapNode {
  id?: string;
  x: number;
  y: number;
}

interface MinimapViewport {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface MinimapProps {
  nodes: MinimapNode[];
  viewport: MinimapViewport;
  worldBounds?: { width: number; height: number };
  /** When provided, ids in this set get a brighter dot. */
  selectedIds?: Set<string>;
  /** Click anywhere on the minimap to recenter the canvas viewport on the
   *  clicked world coords. */
  onRecenter?: (worldX: number, worldY: number) => void;
}

export function Minimap({
  nodes,
  viewport,
  worldBounds = { width: 4000, height: 4000 },
  selectedIds,
  onRecenter,
}: MinimapProps) {
  // Click + drag both recenter the canvas — drag just keeps firing as the
  // pointer moves, so the user can scrub through the world by holding.
  const draggingRef = React.useRef(false);
  const fireRecenter = (clientX: number, clientY: number, rect: DOMRect) => {
    if (!onRecenter) return;
    const fx = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const fy = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
    onRecenter(fx * worldBounds.width, fy * worldBounds.height);
  };
  const handlePointerDown: React.PointerEventHandler<HTMLDivElement> = (event) => {
    if (!onRecenter) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    draggingRef.current = true;
    fireRecenter(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect());
  };
  const handlePointerMove: React.PointerEventHandler<HTMLDivElement> = (event) => {
    if (!draggingRef.current) return;
    fireRecenter(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect());
  };
  const handlePointerUp: React.PointerEventHandler<HTMLDivElement> = (event) => {
    draggingRef.current = false;
    try { event.currentTarget.releasePointerCapture(event.pointerId); } catch { /* ignore */ }
  };
  return (
    <div
      className={`absolute bottom-16 left-4 z-30 h-[132px] w-[200px] overflow-hidden rounded-md border border-glass-border bg-elevated/85 backdrop-blur-md select-none ${
        onRecenter ? "cursor-crosshair" : ""
      }`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      role={onRecenter ? "button" : undefined}
      aria-label={onRecenter ? "Drag to pan canvas" : undefined}
    >
      <div className="absolute inset-0 dotted-canvas opacity-40" />
      {nodes.map((n, i) => {
        const isSelected = !!(n.id && selectedIds?.has(n.id));
        return (
          <div
            key={n.id ?? i}
            data-testid="minimap-dot"
            className={`absolute rounded-full ${
              isSelected ? "h-1.5 w-1.5 bg-primary shadow-[0_0_0_1px_rgba(100,108,255,0.6)]" : "h-1 w-1 bg-primary/70"
            }`}
            style={{
              left: `${(n.x / worldBounds.width) * 100}%`,
              top: `${(n.y / worldBounds.height) * 100}%`,
            }}
          />
        );
      })}
      <div
        data-testid="minimap-viewport"
        className="absolute rounded border-2 border-primary/70 bg-primary/[0.06]"
        style={{
          left: `${(viewport.x / worldBounds.width) * 100}%`,
          top: `${(viewport.y / worldBounds.height) * 100}%`,
          width: `${(viewport.w / worldBounds.width) * 100}%`,
          height: `${(viewport.h / worldBounds.height) * 100}%`,
        }}
      />
      <span className="absolute right-1 bottom-1 font-mono text-[9px] text-text-muted">minimap</span>
    </div>
  );
}
