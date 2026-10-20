'use client';

import { useState, useEffect, useCallback } from 'react';
import { X, RotateCcw, Clock, FileText, AlertTriangle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useTranslations } from 'next-intl';
import { scriptEditorApi, SnapshotResponse } from '@/lib/scriptEditorApi';

export interface SnapshotListDialogProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  onRestore: (content: any) => void;
}

function formatRelativeTime(dateStr: string, t: (key: string, values?: Record<string, any>) => string): string {
  const date = new Date(dateStr);
  const now = Date.now();
  const diff = now - date.getTime();

  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (seconds < 60) return t('dialogs.snapshots.justNow');
  if (minutes < 60) return t('dialogs.snapshots.minutesAgo', { count: minutes });
  if (hours < 24) return t('dialogs.snapshots.hoursAgo', { count: hours });
  if (days < 30) return t('dialogs.snapshots.daysAgo', { count: days });
  return date.toLocaleDateString();
}

function formatTimestamp(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export default function SnapshotListDialog({
  open,
  onClose,
  projectId,
  onRestore,
}: SnapshotListDialogProps) {
  const t = useTranslations('scriptEditor');
  const [snapshots, setSnapshots] = useState<SnapshotResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [confirmingTimestamp, setConfirmingTimestamp] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);

  // Fetch snapshots when dialog opens
  useEffect(() => {
    if (!open || !projectId) return;
    setLoading(true);
    scriptEditorApi
      .listSnapshots(projectId)
      .then((list) => {
        // Sort by created_at descending (newest first)
        const sorted = [...list].sort(
          (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        );
        setSnapshots(sorted);
      })
      .catch((err) => {
        console.error('[SnapshotListDialog] Failed to load snapshots:', err);
      })
      .finally(() => setLoading(false));
  }, [open, projectId]);

  const handleRestore = useCallback(
    async (timestamp: string) => {
      setRestoring(true);
      try {
        const result = await scriptEditorApi.restoreSnapshot(projectId, timestamp);
        onRestore(result.content);
        onClose();
      } catch (err) {
        console.error('[SnapshotListDialog] Restore failed:', err);
      } finally {
        setRestoring(false);
        setConfirmingTimestamp(null);
      }
    },
    [projectId, onRestore, onClose]
  );

  if (!open) return null;

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            className="relative w-full max-w-md rounded-xl border border-white/10 bg-zinc-900 shadow-2xl"
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
              <div className="flex items-center gap-2">
                <Clock size={18} className="text-[var(--color-primary)]" />
                <h2 className="text-base font-semibold text-zinc-100">{t('snapshots.title')}</h2>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
              >
                <X size={18} />
              </button>
            </div>

            {/* Content */}
            <div className="max-h-[400px] overflow-y-auto px-5 py-3">
              {loading ? (
                <div className="flex items-center justify-center py-12 text-sm text-zinc-500">
                  {t('dialogs.snapshots.loading')}
                </div>
              ) : snapshots.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-sm text-zinc-500">
                  <FileText size={32} className="mb-2 opacity-40" />
                  <span>{t('snapshots.empty')}</span>
                </div>
              ) : (
                <div className="space-y-1">
                  {snapshots.map((snap, index) => (
                    <motion.div
                      key={snap.timestamp}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: index * 0.03 }}
                      className="group flex items-center justify-between rounded-lg px-3 py-2.5 transition-colors hover:bg-white/5"
                    >
                      {/* Timeline dot + info */}
                      <div className="flex items-center gap-3">
                        <div className="relative flex flex-col items-center">
                          <div className="h-2.5 w-2.5 rounded-full bg-[var(--color-primary)] ring-2 ring-[var(--color-primary)]/20" />
                          {index < snapshots.length - 1 && (
                            <div className="absolute top-3 h-6 w-px bg-white/10" />
                          )}
                        </div>
                        <div>
                          <p className="text-sm font-medium text-zinc-200">
                            {formatTimestamp(snap.created_at)}
                          </p>
                          <p className="text-xs text-zinc-500">
                            {formatRelativeTime(snap.created_at, t)}
                          </p>
                        </div>
                      </div>

                      {/* Restore button */}
                      {confirmingTimestamp === snap.timestamp ? (
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => handleRestore(snap.timestamp)}
                            disabled={restoring}
                            className="rounded-md bg-amber-600/80 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-amber-600 disabled:opacity-50"
                          >
                            {restoring ? t('dialogs.snapshots.restoring') : t('dialogs.snapshots.confirmBtn')}
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmingTimestamp(null)}
                            className="rounded-md border border-white/10 px-2 py-1 text-xs text-zinc-400 transition-colors hover:text-zinc-200"
                          >
                            {t('dialogs.snapshots.cancel')}
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirmingTimestamp(snap.timestamp)}
                          className="flex items-center gap-1 rounded-md border border-white/10 px-2.5 py-1 text-xs text-zinc-400 opacity-0 transition-all hover:border-white/20 hover:text-zinc-200 group-hover:opacity-100"
                        >
                          <RotateCcw size={12} />
                          <span>{t('dialogs.snapshots.restore')}</span>
                        </button>
                      )}
                    </motion.div>
                  ))}
                </div>
              )}
            </div>

            {/* Confirmation warning */}
            {confirmingTimestamp && (
              <div className="border-t border-white/10 px-5 py-3">
                <div className="flex items-start gap-2 rounded-md bg-amber-900/20 px-3 py-2 text-xs text-amber-300">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>{t('snapshots.confirmRestore')}</span>
                </div>
              </div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
