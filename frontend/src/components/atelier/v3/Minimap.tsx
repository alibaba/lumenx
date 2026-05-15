"use client";
import * as React from "react";

interface MinimapNode {
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
}

export function Minimap({
  nodes,
  viewport,
  worldBounds = { width: 4000, height: 4000 },
}: MinimapProps) {
  return (
    <div className="absolute bottom-16 left-4 z-30 h-[132px] w-[200px] overflow-hidden rounded-md border border-glass-border bg-elevated/85 backdrop-blur-md">
      <div className="absolute inset-0 dotted-canvas opacity-40" />
      {nodes.map((n, i) => (
        <div
          key={i}
          data-testid="minimap-dot"
          className="absolute h-1 w-1 rounded-full bg-primary/80"
          style={{
            left: `${(n.x / worldBounds.width) * 100}%`,
            top: `${(n.y / worldBounds.height) * 100}%`,
          }}
        />
      ))}
      <div
        data-testid="minimap-viewport"
        className="absolute rounded border-2 border-primary/70"
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
