'use client';

import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useTranslations } from 'next-intl';
import { motion, AnimatePresence } from 'framer-motion';
import { GripVertical, Film, Clock, CheckCircle2 } from 'lucide-react';
import type { Editor } from '@tiptap/react';

export interface StoryboardViewProps {
  editor: Editor | null;
  onShotClick: (shotId: string) => void;
}

interface ShotBlockData {
  id: string;
  shotNumber: number;
  description: string;
  duration: number; // seconds
  status: 'draft' | 'confirmed' | 'rendered';
  thumbnailUrl?: string;
}

/**
 * 从编辑器 AST 中提取所有 ShotBlock 节点
 */
function extractShotBlocks(editor: Editor | null): ShotBlockData[] {
  if (!editor) return [];

  const doc = editor.getJSON();
  const shots: ShotBlockData[] = [];
  let shotIndex = 0;

  function traverse(node: any) {
    if (node.type === 'shotBlock') {
      shotIndex++;
      shots.push({
        id: node.attrs?.id || `shot-${shotIndex}`,
        shotNumber: shotIndex,
        description: extractText(node),
        duration: node.attrs?.duration || 3,
        status: node.attrs?.status || 'draft',
        thumbnailUrl: node.attrs?.thumbnailUrl,
      });
    }
    if (node.content) {
      for (const child of node.content) {
        traverse(child);
      }
    }
  }

  traverse(doc);
  return shots;
}

function extractText(node: any): string {
  if (node.text) return node.text;
  if (!node.content) return '';
  return node.content.map(extractText).join('');
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s > 0 ? `${m}m${s}s` : `${m}m`;
}

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-zinc-700 text-zinc-300',
  confirmed: 'bg-blue-900/60 text-blue-300',
  rendered: 'bg-emerald-900/60 text-emerald-300',
};

export default function StoryboardView({ editor, onShotClick }: StoryboardViewProps) {
  const t = useTranslations('scriptEditor');
  const [shots, setShots] = useState<ShotBlockData[]>([]);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  // Extract shots from editor document
  useEffect(() => {
    setShots(extractShotBlocks(editor));
  }, [editor]);

  // Also listen for editor updates
  useEffect(() => {
    if (!editor) return;
    const handleUpdate = () => {
      setShots(extractShotBlocks(editor));
    };
    editor.on('update', handleUpdate);
    return () => {
      editor.off('update', handleUpdate);
    };
  }, [editor]);

  // Statistics
  const stats = useMemo(() => {
    const totalShots = shots.length;
    const totalDuration = shots.reduce((sum, s) => sum + s.duration, 0);
    const completedShots = shots.filter((s) => s.status === 'rendered').length;
    return { totalShots, totalDuration, completedShots };
  }, [shots]);

  // Drag handlers (HTML5 DnD)
  const handleDragStart = useCallback((index: number) => {
    setDraggedIndex(index);
  }, []);

  const handleDragOver = useCallback(
    (e: React.DragEvent, index: number) => {
      e.preventDefault();
      if (draggedIndex !== null && draggedIndex !== index) {
        setDragOverIndex(index);
      }
    },
    [draggedIndex]
  );

  const handleDrop = useCallback(
    (index: number) => {
      if (draggedIndex === null || draggedIndex === index) return;
      const newShots = [...shots];
      const [moved] = newShots.splice(draggedIndex, 1);
      newShots.splice(index, 0, moved);
      // Re-number
      const renumbered = newShots.map((s, i) => ({ ...s, shotNumber: i + 1 }));
      setShots(renumbered);
      setDraggedIndex(null);
      setDragOverIndex(null);
    },
    [draggedIndex, shots]
  );

  const handleDragEnd = useCallback(() => {
    setDraggedIndex(null);
    setDragOverIndex(null);
  }, []);

  if (shots.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-zinc-500">
        <Film size={48} className="mb-4 opacity-30" />
        <p className="text-sm">{t('views.storyboardEmpty')}</p>
        <p className="mt-1 text-xs text-zinc-600">{t('views.storyboardEmptyHint')}</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Grid content */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <AnimatePresence mode="popLayout">
            {shots.map((shot, index) => (
              <motion.div
                key={shot.id}
                layout
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{
                  opacity: 1,
                  scale: 1,
                  y: dragOverIndex === index ? -4 : 0,
                }}
                exit={{ opacity: 0, scale: 0.9 }}
                transition={{ duration: 0.2 }}
                draggable
                onDragStart={() => handleDragStart(index)}
                onDragOver={(e) => handleDragOver(e, index)}
                onDrop={() => handleDrop(index)}
                onDragEnd={handleDragEnd}
                onClick={() => onShotClick(shot.id)}
                className={`group cursor-pointer rounded-lg border transition-all ${
                  draggedIndex === index
                    ? 'border-[var(--color-primary)] opacity-50'
                    : dragOverIndex === index
                    ? 'border-[var(--color-primary)]/50 bg-white/5'
                    : 'border-white/10 hover:border-white/20 hover:bg-white/[0.03]'
                } bg-zinc-900/60`}
              >
                {/* Thumbnail placeholder */}
                <div className="relative aspect-video w-full rounded-t-lg bg-zinc-800/80">
                  {shot.thumbnailUrl ? (
                    <img
                      src={shot.thumbnailUrl}
                      alt={`Shot ${shot.shotNumber}`}
                      className="h-full w-full rounded-t-lg object-cover"
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center text-zinc-600">
                      <Film size={24} />
                    </div>
                  )}
                  {/* Shot number badge */}
                  <div className="absolute left-2 top-2 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-bold text-zinc-200 backdrop-blur-sm">
                    #{shot.shotNumber}
                  </div>
                  {/* Drag handle */}
                  <div className="absolute right-2 top-2 rounded bg-black/50 p-0.5 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100">
                    <GripVertical size={14} className="text-zinc-400" />
                  </div>
                </div>

                {/* Card body */}
                <div className="px-3 py-2.5">
                  <p className="line-clamp-2 text-xs text-zinc-300">
                    {shot.description || t('views.noDescription')}
                  </p>
                  <div className="mt-2 flex items-center gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${STATUS_COLORS[shot.status]}`}
                    >
                      {t(`views.status${shot.status.charAt(0).toUpperCase() + shot.status.slice(1)}` as any)}
                    </span>
                    <span className="flex items-center gap-0.5 text-[10px] text-zinc-500">
                      <Clock size={10} />
                      {formatDuration(shot.duration)}
                    </span>
                  </div>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>

      {/* Bottom stats bar */}
      <div className="flex shrink-0 items-center gap-4 border-t border-white/10 bg-zinc-900/80 px-6 py-2.5 text-xs text-zinc-400">
        <span className="flex items-center gap-1">
          <Film size={12} />
          {t('views.totalShots', { count: stats.totalShots })}
        </span>
        <span className="text-white/20">|</span>
        <span className="flex items-center gap-1">
          <Clock size={12} />
          {t('views.totalDuration', { time: formatDuration(stats.totalDuration) })}
        </span>
        <span className="text-white/20">|</span>
        <span className="flex items-center gap-1">
          <CheckCircle2 size={12} />
          {t('views.completedShots', { count: stats.completedShots })}
        </span>
      </div>
    </div>
  );
}
