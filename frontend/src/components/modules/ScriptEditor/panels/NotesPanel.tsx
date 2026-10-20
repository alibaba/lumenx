'use client';

import { useState, useMemo, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { StickyNote, Check, MessageSquare } from 'lucide-react';
import type { Editor } from '@tiptap/react';
import { useTranslations } from 'next-intl';

export interface NotesPanelProps {
  editor: Editor | null;
}

interface NoteEntry {
  id: string;
  content: string;
  author: string;
  timestamp: string;
  resolved: boolean;
  pos: number;
}

type FilterMode = 'all' | 'unresolved' | 'resolved';

export default function NotesPanel({ editor }: NotesPanelProps) {
  const t = useTranslations('scriptEditor');
  const [filter, setFilter] = useState<FilterMode>('all');

  const notes = useMemo<NoteEntry[]>(() => {
    if (!editor) return [];

    const entries: NoteEntry[] = [];
    const doc = editor.state.doc;

    doc.descendants((node, pos) => {
      if (node.type.name === 'note') {
        entries.push({
          id: `note-${pos}`,
          content: node.textContent || t('panels.emptyNote'),
          author: (node.attrs.author as string) || t('panels.unknownAuthor'),
          timestamp: (node.attrs.timestamp as string) || '',
          resolved: Boolean(node.attrs.resolved),
          pos,
        });
      }
      return true;
    });

    return entries;
  }, [editor, editor?.state.doc]);

  const filteredNotes = useMemo(() => {
    if (filter === 'all') return notes;
    if (filter === 'unresolved') return notes.filter((n) => !n.resolved);
    return notes.filter((n) => n.resolved);
  }, [notes, filter]);

  const handleNoteClick = useCallback(
    (pos: number) => {
      if (!editor) return;
      editor.commands.setTextSelection(pos);
      editor.commands.scrollIntoView();
    },
    [editor]
  );

  if (notes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-800 mb-3">
          <StickyNote size={20} className="text-zinc-500" />
        </div>
        <p className="text-sm text-text-muted">{t('panels.notesEmpty')}</p>
        <p className="text-xs text-text-muted/60 mt-1">{t('panels.notesEmptyHint')}</p>
      </div>
    );
  }

  return (
    <div className="p-3">
      <div className="flex items-center gap-2 mb-3">
        <StickyNote size={14} className="text-text-muted" />
        <span className="text-xs font-medium text-text-muted uppercase tracking-wider">
          {t('panels.notesCount', { count: notes.length })}
        </span>
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-1 mb-3 rounded-lg bg-zinc-900/60 p-0.5">
        {([
          { id: 'all', label: t('panels.filterAll') },
          { id: 'unresolved', label: t('panels.filterUnresolved') },
          { id: 'resolved', label: t('panels.filterResolved') },
        ] as const).map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setFilter(f.id)}
            className={`flex-1 rounded-md px-2 py-1 text-xs font-medium transition-colors ${
              filter === f.id
                ? 'bg-zinc-700 text-foreground'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Notes List */}
      <AnimatePresence mode="popLayout">
        <div className="space-y-2">
          {filteredNotes.map((note) => (
            <motion.div
              key={note.id}
              layout
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.15 }}
              className="rounded-lg border border-white/10 bg-zinc-800/80 p-3 cursor-pointer hover:border-white/20 hover:bg-zinc-800 transition-colors"
              onClick={() => handleNoteClick(note.pos)}
            >
              <div className="flex items-start gap-2">
                <MessageSquare size={12} className="shrink-0 mt-0.5 text-zinc-400" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-foreground line-clamp-2">{note.content}</p>
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="text-xs text-text-muted">{note.author}</span>
                    {note.timestamp && (
                      <>
                        <span className="text-white/20">·</span>
                        <span className="text-xs text-text-muted">{note.timestamp}</span>
                      </>
                    )}
                  </div>
                </div>
                {note.resolved && (
                  <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-900/40">
                    <Check size={10} className="text-green-400" />
                  </div>
                )}
              </div>
            </motion.div>
          ))}
        </div>
      </AnimatePresence>

      {filteredNotes.length === 0 && (
        <p className="text-xs text-text-muted text-center py-4 italic">
          {filter === 'unresolved' ? t('panels.noUnresolved') : t('panels.noResolved')}
        </p>
      )}
    </div>
  );
}
