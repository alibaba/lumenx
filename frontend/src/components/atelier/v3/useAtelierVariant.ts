"use client";
import { useEffect, useState } from "react";

export type AtelierVariant = "v3" | "legacy";

// v0.4.x canvas-uplift branch: v3 is the default shell. This branch exists to
// ship the v3 (graph-first, bloom/editorial) canvas, so visiting #/atelier should
// land on it without a query param — the old `?atelier=v3` trap (where the `?` had
// to precede the `#`) silently fell back to legacy and made the new design invisible.
// Legacy stays reachable as an explicit opt-out via `?atelier=legacy`.
export function resolveAtelierVariant(search: string): AtelierVariant {
  try {
    const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
    return params.get("atelier") === "legacy" ? "legacy" : "v3";
  } catch {
    return "v3";
  }
}

export function useAtelierVariant(): AtelierVariant {
  const [variant, setVariant] = useState<AtelierVariant>("v3");
  useEffect(() => {
    if (typeof window === "undefined") return;
    setVariant(resolveAtelierVariant(window.location.search));
    const onChange = () => setVariant(resolveAtelierVariant(window.location.search));
    window.addEventListener("popstate", onChange);
    return () => window.removeEventListener("popstate", onChange);
  }, []);
  return variant;
}
