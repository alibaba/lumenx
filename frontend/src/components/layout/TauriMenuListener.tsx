"use client";

import { useEffect } from "react";
import { isTauri } from "@/lib/transport";

/**
 * Listens for native menu events dispatched via CustomEvent from Tauri Rust backend
 * (the Rust side uses webview.eval() to fire DOM events).
 *
 * Props:
 *   onNewProject – callback to open the Create Project dialog
 */
interface TauriMenuListenerProps {
  onNewProject?: () => void;
}

export default function TauriMenuListener({ onNewProject }: TauriMenuListenerProps) {
  useEffect(() => {
    if (!isTauri()) return;

    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail === "new_project") {
        onNewProject?.();
      }
    };

    window.addEventListener("tauri-menu", handler);
    return () => window.removeEventListener("tauri-menu", handler);
  }, [onNewProject]);

  return null;
}
