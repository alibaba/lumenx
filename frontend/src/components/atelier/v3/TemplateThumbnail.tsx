"use client";
//
// TemplateThumbnail — tiny SVG sketch of a WorkflowTemplate's node + edge
// graph. Used by WorkflowsPanel both as an inline hover-reveal preview
// (sm = 64×40) and as the HTML5 drag image (lg = 128×80) when the user
// starts dragging a card onto the canvas (Track N of v0.8 spec).
//
// Pure presentational. No interactions, no store access — we just take a
// WorkflowTemplate, compute the bbox of its nodes, and draw colored rects
// per node kind with thin lines for reference edges.
//
// Coordinate algorithm:
//   1. Bbox = (min x, min y, max x+W, max y+H) where W/H are an *assumed*
//      node footprint. Image/video/idea/comment all share roughly
//      244×140 pre-content, which is close enough at this scale.
//   2. Normalize into the viewBox with a small padding inset, picking a
//      uniform scale so the cluster never overflows nor stretches.
//   3. Center the bbox in the viewBox so single-row templates don't drift
//      to the top edge.
//
// Why SVG and not Canvas/HTML? SVG renders crisp at any DPR, animates
// nicely when the wrapping card hover-expands, and serializes well as a
// drag image. Footprint is negligible.

import type { WorkflowTemplate, TemplateNode } from "./workflowTemplates";
import { TEMPLATE_GEOMETRY } from "./workflowTemplates";

// Node footprint used purely for bbox math. Image/video/idea/comment all
// land within a ~244×140 rect on the canvas pre-content. Audio is
// included for forward-compat with future template kinds.
const NODE_W = TEMPLATE_GEOMETRY.IMG_W; // 244 — same as DRAFT_W
const NODE_H = 140;

// Color palette by node type. Sky-blue for refs, brand violet for video
// drafts (primary product surface), amber for ideation, muted slate for
// comments, teal for audio. Stays readable on the rail's near-black bg.
const NODE_FILL: Record<TemplateNode["type"] | "audio", string> = {
  image: "rgba(96,165,250,0.85)",
  video: "rgba(167,139,250,0.85)",
  idea: "rgba(251,191,36,0.85)",
  comment: "rgba(148,163,184,0.7)",
  // Reserved for future template kinds. TemplateNode["type"] doesn't
  // include "audio" yet; the cast on render keeps TS happy without
  // widening the template schema.
  audio: "rgba(136,170,166,0.85)",
};

interface Props {
  template: WorkflowTemplate;
  /** "sm" → 64×40 hover preview; "lg" → 128×80 drag image. */
  size?: "sm" | "lg";
  /** Optional override; falls back to the size preset. */
  width?: number;
  height?: number;
  /** Tailwind classes layered on the root <svg>. */
  className?: string;
}

const SIZE_PRESET = {
  sm: { w: 64, h: 40, px: 4, py: 3, edge: 0.6, stroke: 0.4, rx: 1.2 },
  lg: { w: 128, h: 80, px: 8, py: 6, edge: 1.0, stroke: 0.6, rx: 2.4 },
} as const;

export function TemplateThumbnail({
  template,
  size = "sm",
  width,
  height,
  className,
}: Props) {
  const preset = SIZE_PRESET[size];
  const W_svg = width ?? preset.w;
  const H_svg = height ?? preset.h;

  // Empty / degenerate template — render an empty frame rather than
  // crash on NaN bbox. Shouldn't happen for ships templates but keeps
  // user-saved selections safe.
  if (!template.nodes || template.nodes.length === 0) {
    return (
      <svg
        viewBox={`0 0 ${W_svg} ${H_svg}`}
        width={W_svg}
        height={H_svg}
        className={`rounded-[3px] border border-white/8 bg-black/40 ${className ?? ""}`}
        aria-hidden="true"
      />
    );
  }

  // Bbox in template-local coords.
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of template.nodes) {
    if (n.x < minX) minX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.x + NODE_W > maxX) maxX = n.x + NODE_W;
    if (n.y + NODE_H > maxY) maxY = n.y + NODE_H;
  }
  const bboxW = Math.max(1, maxX - minX);
  const bboxH = Math.max(1, maxY - minY);

  // Uniform scale so we never warp the layout. Center the result by
  // splitting leftover viewBox space evenly on each axis.
  const innerW = W_svg - 2 * preset.px;
  const innerH = H_svg - 2 * preset.py;
  const scale = Math.min(innerW / bboxW, innerH / bboxH);
  const drawnW = bboxW * scale;
  const drawnH = bboxH * scale;
  const offsetX = preset.px + (innerW - drawnW) / 2;
  const offsetY = preset.py + (innerH - drawnH) / 2;

  // Project a single point from template-local → SVG coords.
  const tx = (x: number) => offsetX + (x - minX) * scale;
  const ty = (y: number) => offsetY + (y - minY) * scale;

  // Per-node rect geometry, indexed by localId so edges can look up
  // their endpoints without scanning the node list twice.
  type Rect = { x: number; y: number; w: number; h: number; cx: number; cy: number };
  const rects = new Map<string, Rect>();
  for (const n of template.nodes) {
    const x = tx(n.x);
    const y = ty(n.y);
    const w = NODE_W * scale;
    const h = NODE_H * scale;
    rects.set(n.localId, { x, y, w, h, cx: x + w / 2, cy: y + h / 2 });
  }

  return (
    <svg
      viewBox={`0 0 ${W_svg} ${H_svg}`}
      width={W_svg}
      height={H_svg}
      className={`rounded-[3px] border border-white/8 bg-black/40 ${className ?? ""}`}
      aria-hidden="true"
    >
      {/* Edges first so node rects sit on top. Dashed for reference
          edges to hint at "this is a soft attach, not a flow". */}
      <g>
        {template.edges.map((e, i) => {
          const a = rects.get(e.from);
          const b = rects.get(e.to);
          if (!a || !b) return null;
          return (
            <line
              key={`e-${i}`}
              x1={a.cx}
              y1={a.cy}
              x2={b.cx}
              y2={b.cy}
              stroke="rgba(255,255,255,0.28)"
              strokeWidth={preset.edge}
              strokeDasharray={`${preset.edge * 1.6} ${preset.edge * 2.6}`}
              strokeLinecap="round"
            />
          );
        })}
      </g>

      {/* Node rects. Slightly translucent fills + a hairline white
          stroke keep them legible against the dark thumbnail bg even
          when they cluster. */}
      <g>
        {template.nodes.map((n) => {
          const r = rects.get(n.localId);
          if (!r) return null;
          const fill = NODE_FILL[n.type] ?? "rgba(148,163,184,0.7)";
          return (
            <rect
              key={n.localId}
              x={r.x}
              y={r.y}
              width={r.w}
              height={r.h}
              rx={preset.rx}
              ry={preset.rx}
              fill={fill}
              stroke="rgba(255,255,255,0.15)"
              strokeWidth={preset.stroke}
            />
          );
        })}
      </g>
    </svg>
  );
}

export default TemplateThumbnail;
