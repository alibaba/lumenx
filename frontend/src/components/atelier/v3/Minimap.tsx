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
  const handleClick: React.MouseEventHandler<HTMLDivElement> = (event) => {
    if (!onRecenter) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const fx = (event.clientX - rect.left) / rect.width;
    const fy = (event.clientY - rect.top) / rect.height;
    onRecenter(fx * worldBounds.width, fy * worldBounds.height);
  };
  return (
    <div
      className={`absolute bottom-16 left-4 z-30 h-[132px] w-[200px] overflow-hidden rounded-md border border-glass-border bg-elevated/85 backdrop-blur-md ${
        onRecenter ? "cursor-crosshair" : ""
      }`}
      onClick={handleClick}
      role={onRecenter ? "button" : undefined}
      aria-label={onRecenter ? "Recenter canvas — click any point" : undefined}
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
