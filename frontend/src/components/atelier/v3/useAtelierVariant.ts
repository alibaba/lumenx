"use client";
import { useEffect, useState } from "react";

export type AtelierVariant = "v3" | "legacy";

export function resolveAtelierVariant(search: string): AtelierVariant {
  try {
    const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
    return params.get("atelier") === "v3" ? "v3" : "legacy";
  } catch {
    return "legacy";
  }
}

export function useAtelierVariant(): AtelierVariant {
  const [variant, setVariant] = useState<AtelierVariant>("legacy");
  useEffect(() => {
    if (typeof window === "undefined") return;
    setVariant(resolveAtelierVariant(window.location.search));
    const onChange = () => setVariant(resolveAtelierVariant(window.location.search));
    window.addEventListener("popstate", onChange);
    return () => window.removeEventListener("popstate", onChange);
  }, []);
  return variant;
}
