"use client";
//
// NodePort — the Flova-grade I/O port primitive (v0.5 target spec §2).
// A small colored dot (functional color code) with a same-hue halo, optionally
// labelled. Inputs sit on the LEFT edge, the output on the RIGHT. Color code:
//   model = amber · positive = green · negative = red · output = blue
// The dot is meant to sit ON the node's border (consumers nudge it half-outside
// with -ml/-mr) so connection beams visibly plug into it.
//
// v0.6.3 — TWO-MODE contract:
//   DECORATIVE (default, interactive=false): a quiet 5px indicator. NO
//     data-port attribute, NO .atelier-port-handle class (no hover scale /
//     grab cursor / tooltip), NO onPointerDown wiring even if a handler is
//     passed. The kind-tinted halo box-shadow stays so the color still
//     signals "output" semantics, but the dot is purely visual — pointer-
//     down "near" it is NOT eaten by the canvas-level
//     `closest('[data-port]')` bail-out, so the parent node still selects
//     and drags normally.
//   INTERACTIVE (interactive=true): the drag-to-connect affordance — adds
//     data-port + the .atelier-port-handle class (hover scale, amplified
//     halo, grab cursor, ~22px invisible hit target via ::before), the
//     "Drag to connect" tooltip, and the onPointerDown handler that owns
//     the gesture so the surrounding card never claims it. The actual
//     drag wiring still lives in AtelierShellV3 (handlePortDragOut);
//     the interactive primitive just exposes the visible dot AS the
//     drag source.
// Pass `interactive` only when handlePortDragOut accepts this kind of
// source (image MediaNode with media, completed candidate take with
// video_url). Every other output port stays decorative.
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
  /** Dot diameter in px. Defaults differ by mode: decorative dots default
   *  to 5px (quiet indicator); interactive dots default to 7px (visible
   *  drag handle). Explicit `size` always wins. */
  size?: number;
  className?: string;
  /** When true the dot gains drag-to-connect affordance: hover scale,
   *  amplified halo, grab cursor, tooltip, an invisible enlarged hit
   *  target via ::before, AND the data-port attribute the canvas
   *  node-drag bail-out keys off. When false (default) the dot is purely
   *  decorative — no data-port, no class, no pointer handlers — so the
   *  parent node can still claim pointer-downs that land on/near it. */
  interactive?: boolean;
  /** Drop-target state: when a connection drag is in flight and this
   *  port is the hovered landing zone, the halo flips to green. Only
   *  meaningful for interactive ports. */
  dropTarget?: boolean;
  /** Custom tooltip text. Defaults to a sensible per-kind hint when
   *  interactive (output → "Drag to connect"). Ignored when decorative. */
  tip?: string;
  /** Drag-from handler. Wired ONLY when `interactive` is true — passing
   *  a handler without `interactive` is a no-op (the dot stays decorative
   *  and the gesture falls through to the parent node). */
  onPointerDown?: (e: React.PointerEvent) => void;
}

/** The bare dot (no label) — for placing exactly on an edge.
 *
 *  Two modes, governed solely by the `interactive` flag:
 *    - DECORATIVE (default): a quiet 5px tinted indicator. No data-port,
 *      no .atelier-port-handle class, no onPointerDown wiring, no tooltip.
 *      The kind-tinted halo box-shadow stays so the color still carries
 *      semantics, but the dot is non-interactive — pointer-down near it
 *      does NOT trip the canvas-level [data-port] bail-out, so the parent
 *      node still selects/drags correctly.
 *    - INTERACTIVE: drag-to-connect affordance — data-port + hover scale +
 *      amplified halo + grab cursor + ~22px invisible hit target +
 *      tooltip + onPointerDown owned by the dot so node-drag never claims
 *      the gesture.
 *  Pass `interactive` ONLY when handlePortDragOut in AtelierShellV3 accepts
 *  this kind of source (image MediaNode with media, completed candidate
 *  take with video_url). Every other output port stays decorative.
 */
export function PortDot({
  kind,
  size,
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

  if (interactive) {
    const dotSize = size ?? 7;
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
          width: dotSize,
          height: dotSize,
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

  // Decorative branch — quiet visual indicator only. No data-port (so the
  // canvas node-drag bail-out doesn't trigger), no .atelier-port-handle
  // (no hover scale, no grab cursor), no onPointerDown (gesture passes
  // through to the parent node). Defaults to 5px + 0.85 opacity so it
  // reads as a non-actionable marker; the kind-tinted halo stays.
  const dotSize = size ?? 5;
  return (
    <span
      aria-hidden="true"
      className={`shrink-0 rounded-full ${className}`}
      style={{
        width: dotSize,
        height: dotSize,
        background: PORT_VAR[kind],
        boxShadow: restShadow,
        opacity: 0.85,
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
