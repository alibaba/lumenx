'use client';

import { useState, useMemo, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronRight, TreePine, Film } from 'lucide-react';
import type { Editor } from '@tiptap/react';

export interface OutlineViewProps {
  editor: Editor | null;
}

interface OutlineSection {
  id: string;
  title: string;
  pos: number;
  scenes: OutlineScene[];
}

interface OutlineScene {
  id: string;
  title: string;
  number: number | null;
  pos: number;
}

export default function OutlineView({ editor }: OutlineViewProps) {
  const t = useTranslations('scriptEditor');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const outline = useMemo(() => {
    if (!editor) return { sections: [] as OutlineSection[], flatScenes: [] as OutlineScene[] };

    const sections: OutlineSection[] = [];
    const flatScenes: OutlineScene[] = [];
    let currentSection: OutlineSection | null = null;
    let sceneIndex = 0;

    const doc = editor.state.doc;
    doc.descendants((node, pos) => {
      if (node.type.name === 'section') {
        currentSection = {
          id: `section-${pos}`,
          title: '',
          pos,
          scenes: [],
        };
        sections.push(currentSection);
        return true; // traverse children
      }
      if (node.type.name === 'sectionHeading' && currentSection) {
        currentSection.title = node.textContent || t('sidebar.actLabel', { number: sections.length });
        return false;
      }
      if (node.type.name === 'sceneHeading') {
        sceneIndex++;
        const scene: OutlineScene = {
          id: `scene-${pos}`,
          title: node.textContent || t('sidebar.sceneLabel', { number: sceneIndex }),
          number: sceneIndex,
          pos,
        };
        if (currentSection) {
          currentSection.scenes.push(scene);
        } else {
          flatScenes.push(scene);
        }
        return false;
      }
      return true;
    });

    return { sections, flatScenes };
  }, [editor, editor?.state.doc]);

  const handleJump = useCallback(
    (pos: number) => {
      if (!editor) return;
      editor.commands.setTextSelection(pos + 1);
      editor.commands.scrollIntoView();
    },
    [editor]
  );

  const toggleSection = useCallback((id: string) => {
    setCollapsed((prev) => ({ ...prev, [id]: !prev[id] }));
  }, []);

  const hasSections = outline.sections.length > 0;
  const hasAny = hasSections || outline.flatScenes.length > 0;

  if (!hasAny) {
    return (
      <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-zinc-800 mb-2">
          <TreePine size={16} className="text-zinc-500" />
        </div>
        <p className="text-xs text-text-muted">{t('sidebar.noOutline')}</p>
      </div>
    );
  }

  return (
    <div className="p-2">
      {/* Flat scenes (no section) */}
      {outline.flatScenes.map((scene) => (
        <button
          key={scene.id}
          type="button"
          onClick={() => handleJump(scene.pos)}
          className="flex w-full items-center gap-2 px-2 py-1.5 text-left rounded hover:bg-white/5 transition-colors"
        >
          <Film size={12} className="shrink-0 text-zinc-400" />
          <span className="text-sm text-foreground truncate">
            {scene.number != null && (
              <span className="text-xs font-mono text-text-muted mr-1">#{scene.number}</span>
            )}
            {scene.title}
          </span>
        </button>
      ))}

      {/* Sections with nested scenes */}
      {outline.sections.map((section) => (
        <div key={section.id} className="mb-1">
          <button
            type="button"
            onClick={() => toggleSection(section.id)}
            className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left rounded hover:bg-white/5 transition-colors"
          >
            <motion.div
              animate={{ rotate: collapsed[section.id] ? 0 : 90 }}
              transition={{ duration: 0.15 }}
            >
              <ChevronRight size={12} className="text-text-muted" />
            </motion.div>
            <TreePine size={12} className="shrink-0 text-indigo-400" />
            <span className="text-sm font-medium text-foreground truncate">
              {section.title}
            </span>
            <span className="ml-auto text-[10px] text-text-muted">
              {section.scenes.length}
            </span>
          </button>

          <AnimatePresence>
            {!collapsed[section.id] && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="overflow-hidden"
              >
                <div className="ml-4 border-l border-white/5 pl-2">
                  {section.scenes.map((scene) => (
                    <button
                      key={scene.id}
                      type="button"
                      onClick={() => handleJump(scene.pos)}
                      className="flex w-full items-center gap-2 px-2 py-1 text-left rounded hover:bg-white/5 transition-colors"
                    >
                      <Film size={10} className="shrink-0 text-zinc-500" />
                      <span className="text-xs text-text-secondary truncate">
                        {scene.number != null && (
                          <span className="font-mono text-text-muted mr-1">#{scene.number}</span>
                        )}
                        {scene.title}
                      </span>
                    </button>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      ))}
    </div>
  );
}
