"use client";
//
// NodePort — the Flova-grade I/O port primitive (v0.5 target spec §2).
// A small colored dot (functional color code) with a same-hue halo, optionally
// labelled. Inputs sit on the LEFT edge, the output on the RIGHT. Color code:
//   model = amber · positive = green · negative = red · output = blue
// The dot is meant to sit ON the node's border (consumers nudge it half-outside
// with -ml/-mr) so connection beams visibly plug into it.
import * as React from "react";

export type PortKind = "model" | "positive" | "negative" | "output";

const PORT_VAR: Record<PortKind, string> = {
  model: "var(--atelier-port-model)",
  positive: "var(--atelier-port-positive)",
  negative: "var(--atelier-port-negative)",
  output: "var(--atelier-port-output)",
};

// rgb triplets (match the tokens) for the halo box-shadow alpha math.
const PORT_RGB: Record<PortKind, string> = {
  model: "224,185,78",
  positive: "61,220,132",
  negative: "240,97,109",
  output: "91,157,255",
};

interface Props {
  kind: PortKind;
  side: "left" | "right";
  label?: string;
  /** dot diameter in px (default 8) */
  size?: number;
  className?: string;
}

/** The bare dot (no label) — for placing exactly on an edge. */
export function PortDot({ kind, size = 7, className = "" }: { kind: PortKind; size?: number; className?: string }) {
  const rgb = PORT_RGB[kind];
  return (
    <span
      aria-hidden="true"
      data-port={kind}
      className={`shrink-0 rounded-full ${className}`}
      style={{
        width: size,
        height: size,
        background: PORT_VAR[kind],
        // v0.5.1 premium: softer, more restrained halo (was a loud glow).
        boxShadow: `0 0 0 2px rgba(${rgb},0.13), 0 0 5px rgba(${rgb},0.35)`,
      }}
    />
  );
}

/** A port row: dot + optional muted label, ordered by side. */
export function NodePort({ kind, side, label, size = 8, className = "" }: Props) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 ${side === "right" ? "flex-row-reverse" : ""} ${className}`}
    >
      <PortDot kind={kind} size={size} />
      {label ? (
        <span className="text-[10px] tracking-[0.01em] text-white/45">{label}</span>
      ) : null}
    </span>
  );
}
