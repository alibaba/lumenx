'use client';

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import type { Editor } from '@tiptap/react';

/** Folding activation threshold */
const SCENE_THRESHOLD = 30;

/** Debounce delay for selection-based folding recalculation (ms) */
const SELECTION_DEBOUNCE_MS = 100;

export interface SceneFoldingState {
  /** Whether folding is enabled (totalScenes >= threshold) */
  enabled: boolean;
  /** Set of currently folded scene IDs */
  foldedScenes: Set<string>;
  /** Total number of scenes in the document */
  totalScenes: number;
  /** Toggle between all-expanded and smart-folding */
  toggleAll: () => void;
  /** Unfold a specific scene */
  unfoldScene: (sceneId: string) => void;
  /** Fold a specific scene */
  foldScene: (sceneId: string) => void;
  /** Whether currently in "all expanded" mode */
  isAllExpanded: boolean;
}

interface SceneInfo {
  id: string;
  pos: number;
  text: string;
}

/**
 * Scene folding hook for large documents (≥30 scenes).
 *
 * Strategy: Current scene ± 1 are expanded, rest are folded.
 * Implementation: CSS-only display control via data-scene-collapsed attribute.
 * Does NOT break ProseMirror position mapping, cursor navigation, or Cmd+F.
 */
export function useSceneFolding(editor: Editor | null): SceneFoldingState {
  const [foldedScenes, setFoldedScenes] = useState<Set<string>>(new Set());
  const [isAllExpanded, setIsAllExpanded] = useState(false);
  const [totalScenes, setTotalScenes] = useState(0);

  // Cache scene info to avoid repeated traversals
  const scenesRef = useRef<SceneInfo[]>([]);
  const rafRef = useRef<number | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const enabled = totalScenes >= SCENE_THRESHOLD;

  /**
   * Collect all scene heading nodes from the document.
   * Uses editor.state.doc.descendants for efficiency.
   */
  const collectScenes = useCallback((): SceneInfo[] => {
    if (!editor) return [];
    const scenes: SceneInfo[] = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === 'sceneHeading') {
        const id = (node.attrs.id as string) || `scene-pos-${pos}`;
        scenes.push({ id, pos, text: node.textContent });
      }
    });
    return scenes;
  }, [editor]);

  /**
   * Determine which scene the cursor is currently in.
   * Returns the index in the scenes array.
   */
  const getCurrentSceneIndex = useCallback(
    (scenes: SceneInfo[]): number => {
      if (!editor || scenes.length === 0) return 0;
      const { from } = editor.state.selection;
      // Find the last scene heading that is before or at cursor position
      let idx = 0;
      for (let i = scenes.length - 1; i >= 0; i--) {
        if (scenes[i].pos <= from) {
          idx = i;
          break;
        }
      }
      return idx;
    },
    [editor]
  );

  /**
   * Compute the smart folding set: fold all except current ± 1.
   */
  const computeSmartFolding = useCallback(
    (scenes: SceneInfo[], currentIdx: number): Set<string> => {
      const folded = new Set<string>();
      for (let i = 0; i < scenes.length; i++) {
        // Keep current scene and neighbors (±1) expanded
        if (Math.abs(i - currentIdx) > 1) {
          folded.add(scenes[i].id);
        }
      }
      return folded;
    },
    []
  );

  /**
   * Apply folded state to DOM via data-scene-collapsed attribute.
   * Uses requestAnimationFrame for batched updates.
   */
  const syncDom = useCallback(
    (folded: Set<string>) => {
      if (!editor) return;

      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
      }

      rafRef.current = requestAnimationFrame(() => {
        const dom = editor.view.dom;
        const headings = dom.querySelectorAll<HTMLElement>(
          'h3[data-type="scene-heading"]'
        );

        headings.forEach((heading) => {
          const sceneId =
            heading.getAttribute('data-id') ||
            `scene-pos-${heading.getAttribute('data-pos') || '0'}`;
          const parent = heading.parentElement;
          if (!parent) return;

          // Walk up to find the direct child of ProseMirror container if needed
          // In flat doc structure, scene content follows the heading as siblings
          // We need to find the "scene block" — in our case, the heading itself
          // acts as the marker. We'll mark a conceptual "scene range" using
          // a wrapper approach: mark sibling elements between this heading and the next.

          if (folded.has(sceneId)) {
            heading.setAttribute('data-scene-collapsed', 'true');
          } else {
            heading.removeAttribute('data-scene-collapsed');
          }
        });

        // Also handle the sibling content between scene headings
        applyCollapsedSiblings(dom, folded);
        rafRef.current = null;
      });
    },
    [editor]
  );

  /**
   * Toggle all expanded <-> smart folding mode.
   */
  const toggleAll = useCallback(() => {
    if (!enabled) return;

    setIsAllExpanded((prev) => {
      const next = !prev;
      if (next) {
        // Expand all
        setFoldedScenes(new Set());
        syncDom(new Set());
      } else {
        // Re-enable smart folding
        const scenes = collectScenes();
        const currentIdx = getCurrentSceneIndex(scenes);
        const folded = computeSmartFolding(scenes, currentIdx);
        setFoldedScenes(folded);
        syncDom(folded);
      }
      return next;
    });
  }, [enabled, collectScenes, getCurrentSceneIndex, computeSmartFolding, syncDom]);

  /**
   * Unfold a specific scene (e.g., when navigating to it).
   */
  const unfoldScene = useCallback(
    (sceneId: string) => {
      setFoldedScenes((prev) => {
        if (!prev.has(sceneId)) return prev;
        const next = new Set(prev);
        next.delete(sceneId);
        syncDom(next);
        return next;
      });
    },
    [syncDom]
  );

  /**
   * Fold a specific scene.
   */
  const foldScene = useCallback(
    (sceneId: string) => {
      setFoldedScenes((prev) => {
        if (prev.has(sceneId)) return prev;
        const next = new Set(prev);
        next.add(sceneId);
        syncDom(next);
        return next;
      });
    },
    [syncDom]
  );

  /**
   * Recalculate folding on selection update (debounced).
   */
  const handleSelectionUpdate = useCallback(() => {
    if (!enabled || isAllExpanded) return;

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }

    debounceTimerRef.current = setTimeout(() => {
      const scenes = collectScenes();
      scenesRef.current = scenes;
      setTotalScenes(scenes.length);

      if (scenes.length < SCENE_THRESHOLD) return;

      const currentIdx = getCurrentSceneIndex(scenes);
      const folded = computeSmartFolding(scenes, currentIdx);
      setFoldedScenes(folded);
      syncDom(folded);
    }, SELECTION_DEBOUNCE_MS);
  }, [enabled, isAllExpanded, collectScenes, getCurrentSceneIndex, computeSmartFolding, syncDom]);

  /**
   * Initial scene count and setup editor listeners.
   */
  useEffect(() => {
    if (!editor) return;

    // Initial scene count
    const scenes = collectScenes();
    scenesRef.current = scenes;
    setTotalScenes(scenes.length);

    // If threshold met and not all-expanded, apply initial smart folding
    if (scenes.length >= SCENE_THRESHOLD && !isAllExpanded) {
      const currentIdx = getCurrentSceneIndex(scenes);
      const folded = computeSmartFolding(scenes, currentIdx);
      setFoldedScenes(folded);
      // Defer DOM sync to next frame to ensure rendering is complete
      requestAnimationFrame(() => syncDom(folded));
    }

    // Listen for document changes to update scene count
    const handleUpdate = () => {
      const updatedScenes = collectScenes();
      scenesRef.current = updatedScenes;
      setTotalScenes(updatedScenes.length);
    };

    editor.on('update', handleUpdate);
    editor.on('selectionUpdate', handleSelectionUpdate);

    return () => {
      editor.off('update', handleUpdate);
      editor.off('selectionUpdate', handleSelectionUpdate);
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [editor, isAllExpanded, collectScenes, getCurrentSceneIndex, computeSmartFolding, syncDom, handleSelectionUpdate]);

  /**
   * Listen for Cmd+Shift+E custom event from keyboard shortcuts.
   */
  useEffect(() => {
    const handler = () => toggleAll();
    document.addEventListener('script-editor:toggle-scene-folding', handler);
    return () => {
      document.removeEventListener('script-editor:toggle-scene-folding', handler);
    };
  }, [toggleAll]);

  /**
   * Listen for scene navigation event (auto-unfold target scene).
   */
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ sceneId: string }>).detail;
      if (detail?.sceneId) {
        unfoldScene(detail.sceneId);
      }
    };
    document.addEventListener('script-editor:navigate-to-scene', handler);
    return () => {
      document.removeEventListener('script-editor:navigate-to-scene', handler);
    };
  }, [unfoldScene]);

  /**
   * Handle click on collapsed scene heading to expand it.
   */
  useEffect(() => {
    if (!editor) return;

    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      // Check if clicked on a collapsed scene heading
      const heading = target.closest<HTMLElement>(
        'h3[data-type="scene-heading"][data-scene-collapsed="true"]'
      );
      if (heading) {
        const sceneId = heading.getAttribute('data-id');
        if (sceneId) {
          unfoldScene(sceneId);
        }
      }
    };

    const dom = editor.view.dom;
    dom.addEventListener('click', handleClick);
    return () => dom.removeEventListener('click', handleClick);
  }, [editor, unfoldScene]);

  return useMemo(
    () => ({
      enabled,
      foldedScenes,
      totalScenes,
      toggleAll,
      unfoldScene,
      foldScene,
      isAllExpanded,
    }),
    [enabled, foldedScenes, totalScenes, toggleAll, unfoldScene, foldScene, isAllExpanded]
  );
}

/**
 * Apply collapsed state to sibling content blocks between scene headings.
 *
 * In a flat ProseMirror doc, scene content follows the heading as sibling blocks.
 * We mark all sibling blocks between a collapsed heading and the next heading
 * with a data attribute for CSS to hide them.
 */
function applyCollapsedSiblings(dom: HTMLElement, folded: Set<string>) {
  const prosemirror = dom;
  const children = Array.from(prosemirror.children) as HTMLElement[];

  let currentSceneId: string | null = null;
  let isCurrentCollapsed = false;

  for (const child of children) {
    if (
      child.tagName === 'H3' &&
      child.getAttribute('data-type') === 'scene-heading'
    ) {
      // This is a scene heading
      currentSceneId =
        child.getAttribute('data-id') || null;
      isCurrentCollapsed = currentSceneId ? folded.has(currentSceneId) : false;

      // The heading itself: always visible but styled differently when collapsed
      if (isCurrentCollapsed) {
        child.setAttribute('data-scene-collapsed', 'true');
      } else {
        child.removeAttribute('data-scene-collapsed');
      }
    } else if (currentSceneId !== null) {
      // Content block belonging to current scene
      if (isCurrentCollapsed) {
        child.setAttribute('data-scene-content-collapsed', 'true');
      } else {
        child.removeAttribute('data-scene-content-collapsed');
      }
    }
  }
}
