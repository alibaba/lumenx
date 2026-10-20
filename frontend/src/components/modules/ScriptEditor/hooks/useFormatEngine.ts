'use client';

import { useEffect, useCallback } from 'react';
import { useEditorStore, type ScriptFormat, type TextRendering } from '@/store/editorStore';

/**
 * useFormatEngine — manages the two-dimensional format engine:
 * - ScriptFormat: hollywood | chinese_film | chinese_short | japanese_anime
 * - TextRendering: latin | cjk_zh | cjk_ja
 *
 * Syncs store state → DOM data-attributes on .script-editor container.
 */
export function useFormatEngine() {
  const currentFormat = useEditorStore((s) => s.currentFormat);
  const currentRendering = useEditorStore((s) => s.currentRendering);
  const setFormatStore = useEditorStore((s) => s.setFormat);
  const setRenderingStore = useEditorStore((s) => s.setRendering);

  // Sync DOM data-attributes whenever format/rendering changes
  useEffect(() => {
    const container = document.querySelector('.script-editor');
    if (!container) return;
    container.setAttribute('data-format', currentFormat);
    container.setAttribute('data-rendering', currentRendering);
  }, [currentFormat, currentRendering]);

  const setFormat = useCallback(
    (format: ScriptFormat) => {
      setFormatStore(format);
    },
    [setFormatStore]
  );

  const setRendering = useCallback(
    (rendering: TextRendering) => {
      setRenderingStore(rendering);
    },
    [setRenderingStore]
  );

  return {
    currentFormat,
    currentRendering,
    setFormat,
    setRendering,
  };
}
