"use client";
//
// BrandMark — LumenX iridescent orb wordmark icon. Replaces the generic
// Sparkles glyph used previously at the top-left brand position and in the
// LeftRail brand square. Pure inline SVG so it scales crisply at any size
// from the 14px rail collapsed state up to large displays, with no external
// asset dependency.
//
// Visual recipe (RHTV pixel-parity reference, v0.7 sweep — item C):
//   - Soft outer halo: radial gradient rgba(61,220,132,0.25) at the center
//     fading to transparent at the edge. Gives the orb its glow without
//     bloom shaders.
//   - Sharp inner sphere: linear gradient from atelier brand green (#3ddc84)
//     into a desaturated cyan-sage (#88aaa6). The gradient direction (top-
//     left → bottom-right) lets the highlight read as a lit sphere.
//   - Tiny highlight dot top-left at ~28% radius — sells the 3D specular.
//
// All gradient stops are inlined inside <defs> with stable ids prefixed by
// the component instance so multiple BrandMarks on the same page never
// collide. (React.useId is what keeps the ids stable for SSR + hydration.)
import * as React from "react";

interface BrandMarkProps {
  size?: number;
  className?: string;
}

export default function BrandMark({ size = 18, className }: BrandMarkProps) {
  const uid = React.useId().replace(/:/g, "");
  const haloId = `lumenx-brand-halo-${uid}`;
  const sphereId = `lumenx-brand-sphere-${uid}`;
  const highlightId = `lumenx-brand-highlight-${uid}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 18 18"
      role="img"
      aria-label="LumenX"
      className={className}
    >
      <defs>
        <radialGradient id={haloId} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="rgba(61,220,132,0.25)" />
          <stop offset="60%" stopColor="rgba(61,220,132,0.08)" />
          <stop offset="100%" stopColor="rgba(61,220,132,0)" />
        </radialGradient>
        <linearGradient id={sphereId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#3ddc84" />
          <stop offset="100%" stopColor="#88aaa6" />
        </linearGradient>
        <radialGradient id={highlightId} cx="30%" cy="28%" r="22%">
          <stop offset="0%" stopColor="rgba(255,255,255,0.85)" />
          <stop offset="100%" stopColor="rgba(255,255,255,0)" />
        </radialGradient>
      </defs>
      {/* Halo — full viewBox so the glow extends beyond the sphere edge. */}
      <circle cx="9" cy="9" r="9" fill={`url(#${haloId})`} />
      {/* Sphere — inner radius leaves room for the halo. */}
      <circle cx="9" cy="9" r="6" fill={`url(#${sphereId})`} />
      {/* Specular highlight — small, top-left, sells the 3D read. */}
      <circle cx="9" cy="9" r="6" fill={`url(#${highlightId})`} />
    </svg>
  );
}
