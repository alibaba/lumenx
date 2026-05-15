"use client";
import { Check, X } from "lucide-react";

interface Props {
  on: boolean;
  label: string;       // e.g. "img-ref"
  sym: string;         // e.g. "🖼"
}

export function CapabilityIcon({ on, label, sym }: Props) {
  return (
    <span
      role="img"
      aria-label={`${label} ${on ? "supported" : "not supported"}`}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
        on ? "border-emerald-400/40 text-emerald-200/85" : "border-red-400/35 text-red-200/70"
      }`}
    >
      <span className="font-mono" aria-hidden="true">{sym}</span>
      <span aria-hidden="true">{label}</span>
      {on ? <Check size={10} aria-hidden="true" /> : <X size={10} aria-hidden="true" />}
    </span>
  );
}
