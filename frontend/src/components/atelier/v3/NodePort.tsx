"use client";
//
// NodePort — the Flova-grade I/O port primitive (v0.5 target spec §2).
// A small colored dot (functional color code) with a same-hue halo, optionally
// labelled. Inputs sit on the LEFT edge, the output on the RIGHT. Color code:
//   model = amber · positive = green · negative = red · output = blue
// The dot is meant to sit ON the node's border (consumers nudge it half-outside
// with -ml/-mr) so connection beams visibly plug into it.
//
// v0.6.1 — `interactive` mode adds drag-to-connect affordance: hover scale,
// amplified halo, grab cursor, tooltip via .btn-tip, plus an invisible
// ~22px hit target via ::before so the user doesn't have to land on a 7px
// circle. The actual drag wiring still lives in AtelierShellV3
// (handlePortDragOut on the canvas-level 16×16 Plus button); the
// interactive primitive just makes the visible dot read as "this is the
// thing you drag from" so connection mechanics become discoverable.
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

interface PortDotProps {
  kind: PortKind;
  size?: number;
  className?: string;
  /** When true the dot gains drag-to-connect affordance: hover scale,
   *  amplified halo, grab cursor, tooltip, and an invisible enlarged
   *  hit target via ::before. Visual only — drag wiring is owned by the
   *  canvas-level connection handle in AtelierShellV3. */
  interactive?: boolean;
  /** Drop-target state: when a connection drag is in flight and this
   *  port is the hovered landing zone, the halo flips to green. */
  dropTarget?: boolean;
  /** Custom tooltip text. Defaults to a sensible per-kind hint when
   *  interactive (output → "Drag to connect"). */
  tip?: string;
  /** Optional drag-from handler. When present, the dot is rendered as
   *  a button so it can capture pointer events without being eaten by
   *  the parent node's drag handler. */
  onPointerDown?: (e: React.PointerEvent) => void;
}

/** The bare dot (no label) — for placing exactly on an edge. */
export function PortDot({
  kind,
  size = 7,
  className = "",
  interactive = false,
  dropTarget = false,
  tip,
  onPointerDown,
}: PortDotProps) {
  const rgb = PORT_RGB[kind];
  // Rest halo — softer, restrained (v0.5.1 premium).
  const restShadow = `0 0 0 2px rgba(${rgb},0.13), 0 0 5px rgba(${rgb},0.35)`;
  // Drop-target halo — saturated green so the landing zone reads as
  // "yes, drop here". Uses the positive-port green (61,220,132) regardless
  // of the port's own color so the cue is unambiguous.
  const dropShadow = `0 0 0 4px rgba(61,220,132,0.55), 0 0 14px rgba(61,220,132,0.6)`;
  const isInteractive = interactive || !!onPointerDown;

  if (isInteractive) {
    const tooltip = tip ?? (kind === "output" ? "Drag to connect" : undefined);
    const label =
      kind === "output" ? "Output port — drag to connect" : `${kind} port`;
    return (
      <span
        data-port={kind}
        data-drop-target={dropTarget ? "true" : undefined}
        data-tip={tooltip}
        aria-label={label}
        role={onPointerDown ? "button" : undefined}
        tabIndex={onPointerDown ? -1 : undefined}
        onPointerDown={onPointerDown}
        className={`atelier-port-handle shrink-0 rounded-full ${
          tooltip ? "btn-tip" : ""
        } ${className}`}
        style={{
          width: size,
          height: size,
          background: PORT_VAR[kind],
          boxShadow: dropTarget ? dropShadow : restShadow,
          // Per-instance CSS vars so the hover recipe in globals.css can
          // amplify the rest halo without losing the kind's color.
          ["--port-halo-rest" as string]: restShadow,
          ["--port-halo-hover" as string]:
            `0 0 0 4px rgba(${rgb},0.22), 0 0 12px rgba(${rgb},0.5), 0 0 22px rgba(${rgb},0.3)`,
          ["--port-halo-drop" as string]: dropShadow,
        }}
      />
    );
  }

  return (
    <span
      aria-hidden="true"
      data-port={kind}
      data-drop-target={dropTarget ? "true" : undefined}
      className={`shrink-0 rounded-full ${className}`}
      style={{
        width: size,
        height: size,
        background: PORT_VAR[kind],
        boxShadow: dropTarget ? dropShadow : restShadow,
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
