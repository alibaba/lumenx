'use client';

import { useState, useCallback, useRef } from 'react';
import { useTranslations } from 'next-intl';
import { motion } from 'framer-motion';
import { Film } from 'lucide-react';
import type { Editor } from '@tiptap/react';
import { useEditorStore, type DerivedScene } from '@/store/editorStore';

export interface SceneNavigatorProps {
  editor: Editor | null;
}

const COLOR_OPTIONS = [
  '#6366f1', '#8b5cf6', '#ec4899', '#ef4444',
  '#f97316', '#eab308', '#22c55e', '#06b6d4',
];

interface SceneItemProps {
  scene: DerivedScene;
  isActive: boolean;
  color: string | null;
  onSelect: () => void;
  onColorChange: (color: string | null) => void;
  onDragStart: (e: React.DragEvent, id: string) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent, id: string) => void;
}

function SceneItem({
  scene,
  isActive,
  color,
  onSelect,
  onColorChange,
  onDragStart,
  onDragOver,
  onDrop,
}: SceneItemProps) {
  const t = useTranslations('scriptEditor');
  const [showColors, setShowColors] = useState(false);

  return (
    <motion.div
      layout
      draggable
      onDragStart={(e) => onDragStart(e as unknown as React.DragEvent, scene.id)}
      onDragOver={onDragOver}
      onDrop={(e) => onDrop(e as unknown as React.DragEvent, scene.id)}
      className={`group flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition-colors ${
        isActive
          ? 'bg-indigo-500/20 border border-indigo-500/30'
          : 'hover:bg-white/5 border border-transparent'
      }`}
      onClick={onSelect}
    >
      {/* Color dot */}
      <button
        type="button"
        className="shrink-0 w-3 h-3 rounded-full border border-white/20 transition-colors hover:border-white/40"
        style={{ backgroundColor: color || 'transparent' }}
        onClick={(e) => {
          e.stopPropagation();
          setShowColors(!showColors);
        }}
      />

      {/* Scene info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          {scene.number != null && (
            <span className="text-xs font-mono text-text-muted shrink-0">
              #{scene.number}
            </span>
          )}
          {scene.intExt && (
            <span className="text-[10px] font-medium uppercase px-1 py-0.5 rounded bg-zinc-700 text-zinc-300 shrink-0">
              {scene.intExt}
            </span>
          )}
          <span className="text-sm text-foreground truncate">
            {scene.location || scene.title || t('sidebar.untitled')}
          </span>
        </div>
      </div>

      {/* Drag handle indicator */}
      <div className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-text-muted">
        <svg width="8" height="14" viewBox="0 0 8 14" fill="currentColor">
          <circle cx="2" cy="2" r="1" />
          <circle cx="6" cy="2" r="1" />
          <circle cx="2" cy="7" r="1" />
          <circle cx="6" cy="7" r="1" />
          <circle cx="2" cy="12" r="1" />
          <circle cx="6" cy="12" r="1" />
        </svg>
      </div>

      {/* Color picker popup */}
      {showColors && (
        <div
          className="absolute left-8 mt-1 z-50 p-1.5 bg-zinc-800 border border-white/10 rounded-lg shadow-xl grid grid-cols-4 gap-1"
          onClick={(e) => e.stopPropagation()}
        >
          {COLOR_OPTIONS.map((c) => (
            <button
              key={c}
              type="button"
              className="w-5 h-5 rounded-full border border-white/20 hover:scale-110 transition-transform"
              style={{ backgroundColor: c }}
              onClick={() => {
                onColorChange(c);
                setShowColors(false);
              }}
            />
          ))}
          <button
            type="button"
            className="w-5 h-5 rounded-full border border-white/20 hover:scale-110 transition-transform bg-zinc-600 flex items-center justify-center text-[8px] text-zinc-300"
            onClick={() => {
              onColorChange(null);
              setShowColors(false);
            }}
          >
            ✕
          </button>
        </div>
      )}
    </motion.div>
  );
}

export default function SceneNavigator({ editor }: SceneNavigatorProps) {
  const t = useTranslations('scriptEditor');
  const derivedScenes = useEditorStore((s) => s.derivedScenes);
  const [sceneColors, setSceneColors] = useState<Record<string, string | null>>({});
  const [activeSceneId, setActiveSceneId] = useState<string | null>(null);
  const dragIdRef = useRef<string | null>(null);

  const handleSceneSelect = useCallback(
    (scene: DerivedScene) => {
      setActiveSceneId(scene.id);
      if (!editor) return;

      // Dispatch event to auto-unfold the target scene if it's collapsed
      document.dispatchEvent(
        new CustomEvent('script-editor:navigate-to-scene', {
          detail: { sceneId: scene.id },
        })
      );

      // Find the scene heading node position
      const doc = editor.state.doc;
      let targetPos: number | null = null;

      doc.descendants((node, pos) => {
        if (node.type.name === 'sceneHeading' && targetPos === null) {
          const nodeId = node.attrs.id as string | null;
          if (nodeId === scene.id) {
            targetPos = pos;
          } else {
            const text = node.textContent;
            if (scene.title && text.includes(scene.title)) {
              targetPos = pos;
            } else if (scene.location && text.includes(scene.location)) {
              targetPos = pos;
            }
          }
        }
        return targetPos === null;
      });

      if (targetPos !== null) {
        editor.commands.setTextSelection(targetPos + 1);
        editor.commands.scrollIntoView();
      }
    },
    [editor]
  );

  const handleColorChange = useCallback((sceneId: string, color: string | null) => {
    setSceneColors((prev) => ({ ...prev, [sceneId]: color }));
  }, []);

  const handleDragStart = useCallback((e: React.DragEvent, id: string) => {
    dragIdRef.current = id;
    e.dataTransfer.effectAllowed = 'move';
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const handleDrop = useCallback((_e: React.DragEvent, _targetId: string) => {
    // Reorder is visual-only placeholder; actual reorder requires editor transaction
    dragIdRef.current = null;
  }, []);

  if (derivedScenes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-zinc-800 mb-2">
          <Film size={16} className="text-zinc-500" />
        </div>
        <p className="text-xs text-text-muted">{t('sidebar.noScenes')}</p>
      </div>
    );
  }

  return (
    <div className="p-2 space-y-0.5">
      {derivedScenes.map((scene) => (
        <SceneItem
          key={scene.id}
          scene={scene}
          isActive={activeSceneId === scene.id}
          color={sceneColors[scene.id] || null}
          onSelect={() => handleSceneSelect(scene)}
          onColorChange={(c) => handleColorChange(scene.id, c)}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
        />
      ))}
    </div>
  );
}
