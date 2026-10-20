"use client";

import { useCallback } from "react";
import { isTauri } from "@/lib/transport";

/**
 * Invisible drag bar at the top of the window for Tauri desktop mode.
 * Uses Tauri JS API startDragging() for reliable window movement.
 * Only renders in Tauri environment.
 */
export default function TauriDragBar() {
  if (!isTauri()) return null;

  const handleMouseDown = useCallback(async (e: React.MouseEvent) => {
    // Only left click triggers drag
    if (e.button !== 0) return;
    e.preventDefault();
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    getCurrentWindow().startDragging();
  }, []);

  return (
    <div
      className="tauri-drag-bar"
      onMouseDown={handleMouseDown}
    />
  );
}
