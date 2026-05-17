export interface ComposerAnchor {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ComposerViewport {
  width: number;
  height: number;
  rightRailWidth: number;   // px taken by Right Rail at the right edge (e.g. 396 = 380 + 16 padding)
}

export interface ComposerSize {
  width: number;
  /** Approximate height used to decide whether the composer fits below the
   *  anchor before flipping above. Default 320 covers the common 7-tab +
   *  3-line textarea layout. */
  height?: number;
  gap?: number;     // px between anchor and composer; default 16
}

export interface ComposerPlacement {
  left: number;
  top: number;
}

export function composerPlacement(
  anchor: ComposerAnchor | null,
  viewport: ComposerViewport,
  composer: ComposerSize = { width: 520 }
): ComposerPlacement {
  const gap = composer.gap ?? 16;
  const height = composer.height ?? 320;
  if (!anchor) {
    return {
      left: Math.max(16, Math.round((viewport.width - composer.width) / 2)),
      top:  Math.max(16, Math.round(viewport.height / 3)),
    };
  }
  // Horizontal: prefer aligning with anchor.x but avoid the right rail and
  // the right edge of the viewport.
  const desiredLeft = anchor.x;
  const maxLeft     = viewport.width - viewport.rightRailWidth - composer.width - gap;
  const left = Math.max(16, Math.min(desiredLeft, maxLeft));
  // Vertical: try below the anchor first; if it would overflow the viewport
  // bottom, flip to above. Fall back to clamping inside the viewport when
  // neither side fits cleanly (very tall anchor / very short viewport).
  const belowTop = anchor.y + anchor.height + gap;
  const aboveTop = anchor.y - height - gap;
  let top: number;
  if (belowTop + height <= viewport.height - 16) top = belowTop;
  else if (aboveTop >= 16) top = aboveTop;
  else top = Math.max(16, Math.min(belowTop, viewport.height - height - 16));
  return { left, top };
}
