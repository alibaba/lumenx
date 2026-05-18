// Composer placement.
//
// Goal: the Composer must always read as "attached to the selected node".
// Earlier rev tried to be clever (clamp behind right rail, flip above
// when overflowing) — and produced placements that drifted far from the
// anchor when the node was near the right edge or the rail. That broke
// the visual association the whole UX depends on.
//
// New rule: stay glued to the anchor. Two simple decisions:
//   1. Try to place below the anchor; flip above only if there's clearly
//      no room below AND there's room above.
//   2. Align horizontally with the anchor's left edge; only nudge inward
//      if the composer would otherwise extend past the viewport edge by
//      more than its own gap. The right rail is allowed to overlap — it
//      sits at z-30, the composer at z-40, so the user still reads them
//      as discrete surfaces and can click through to either.
//
// Keep this code obvious. Clever placement is what got us here.

export interface ComposerAnchor {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ComposerViewport {
  width: number;
  height: number;
  /** Width reserved at the right edge by the Agent rail (informational —
   *  not used as a hard clamp anymore; see file comment). */
  rightRailWidth: number;
}

export interface ComposerSize {
  width: number;
  height?: number;
  gap?: number;
}

export interface ComposerPlacement {
  left: number;
  top: number;
}

export function composerPlacement(
  anchor: ComposerAnchor | null,
  viewport: ComposerViewport,
  composer: ComposerSize = { width: 520 },
): ComposerPlacement {
  const gap = composer.gap ?? 14;
  const height = composer.height ?? 320;
  // No anchor — center on viewport. (Only happens for the no-selection
  // fallback path; in normal use `anchor` is the selected node's DOM
  // rect.)
  if (!anchor) {
    return {
      left: Math.max(16, Math.round((viewport.width - composer.width) / 2)),
      top: Math.max(16, Math.round(viewport.height / 3)),
    };
  }

  // Horizontal — pin to the anchor's left edge by default. If the
  // composer would extend past the viewport right edge, try right-
  // aligning it with the anchor's right edge instead. That keeps the
  // composer visually attached to the same node rather than sliding
  // far left to fit. Clamp as a last resort.
  let left = Math.round(anchor.x);
  if (left + composer.width > viewport.width - 12) {
    const rightAligned = Math.round(anchor.x + anchor.width - composer.width);
    if (rightAligned >= 12 && rightAligned + composer.width <= viewport.width - 12) {
      left = rightAligned;
    } else {
      left = Math.max(12, viewport.width - composer.width - 12);
    }
  }
  if (left < 12) left = 12;

  // Vertical — prefer below; flip above when there is clearly no room
  // below AND there is room above. Otherwise stay below and let the
  // composer overflow (the canvas main is the user's working area, not
  // a fixed-size dialog).
  const belowTop = Math.round(anchor.y + anchor.height + gap);
  const aboveTop = Math.round(anchor.y - height - gap);
  const fitsBelow = belowTop + height <= viewport.height - 12;
  const fitsAbove = aboveTop >= 12;
  let top: number;
  if (fitsBelow) {
    top = belowTop;
  } else if (fitsAbove) {
    top = aboveTop;
  } else {
    // Neither side fits cleanly. Stay below, clamped, and let the user
    // scroll the inner content if the composer is taller than the
    // remaining space.
    top = Math.max(12, Math.min(belowTop, viewport.height - 80));
  }

  return { left, top };
}
