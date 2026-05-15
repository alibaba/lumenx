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
  gap?: number;     // px between anchor bottom and composer top; default 16
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
  if (!anchor) {
    return {
      left: Math.max(16, Math.round((viewport.width - composer.width) / 2)),
      top:  Math.max(16, Math.round(viewport.height / 3)),
    };
  }
  const desiredLeft = anchor.x;
  const maxLeft     = viewport.width - viewport.rightRailWidth - composer.width - gap;
  const left = Math.max(16, Math.min(desiredLeft, maxLeft));
  const top  = anchor.y + anchor.height + gap;
  return { left, top };
}
