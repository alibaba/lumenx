'use client';

import { useState, useRef, useEffect } from 'react';
import { useTranslations } from 'next-intl';
import { ListOrdered, X, Minus, Plus, RefreshCw } from 'lucide-react';
import { usePlaygroundStore } from './usePlaygroundStore';

/**
 * Queue indicator + popover for the client-side generation queue.
 * Shows waiting/dispatching requests, lets the user cancel pending ones and
 * adjust the concurrency limit. Running generations appear as skeletons in the
 * gallery; this panel only covers the not-yet-in-flight queue.
 */
export default function QueuePanel() {
  const t = useTranslations('playground');
  const queue = usePlaygroundStore((s) => s.queue);
  const activeCount = usePlaygroundStore((s) => s.activeGenerationIds.length);
  const maxConcurrent = usePlaygroundStore((s) => s.maxConcurrent);
  const setMaxConcurrent = usePlaygroundStore((s) => s.setMaxConcurrent);
  const cancelPendingRequest = usePlaygroundStore((s) => s.cancelPendingRequest);
  const requestReadback = usePlaygroundStore((s) => s.requestReadback);

  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  const waiting = queue.length;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-full border border-glass-border bg-surface-inset px-3.5 py-2 text-[0.8125rem] font-medium text-text-muted transition-colors cursor-pointer hover:bg-hover-bg hover:text-foreground"
        title={t('queue.label')}
      >
        <ListOrdered className="w-4 h-4" />
        {t('queue.label')}
        {waiting > 0 && <span className="font-mono text-[0.6875rem] text-accent">· {waiting}</span>}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-40 mt-2 w-[300px] rounded-[16px] border border-glass-border bg-elevated p-4 shadow-2xl">
          {/* Concurrency control */}
          <div className="mb-2 flex items-center justify-between">
            <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-text-muted">
              {t('queue.concurrency')}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setMaxConcurrent(maxConcurrent - 1)}
                disabled={maxConcurrent <= 1}
                className="flex h-6 w-6 items-center justify-center rounded-md border border-glass-border bg-surface-inset text-text-muted transition-colors hover:text-foreground disabled:opacity-30"
              >
                <Minus className="w-3 h-3" />
              </button>
              <span className="w-4 text-center font-mono text-sm text-foreground">{maxConcurrent}</span>
              <button
                type="button"
                onClick={() => setMaxConcurrent(maxConcurrent + 1)}
                disabled={maxConcurrent >= 8}
                className="flex h-6 w-6 items-center justify-center rounded-md border border-glass-border bg-surface-inset text-text-muted transition-colors hover:text-foreground disabled:opacity-30"
              >
                <Plus className="w-3 h-3" />
              </button>
            </div>
          </div>
          <p className="mb-3 text-[0.625rem] text-text-muted">{t('queue.runningHint', { count: activeCount })}</p>

          {/* Queued requests */}
          {queue.length === 0 ? (
            <p className="py-3 text-center text-[0.75rem] text-text-muted">{t('queue.empty')}</p>
          ) : (
            <div className="max-h-64 space-y-1.5 overflow-y-auto">
              {queue.map((q) => (
                <div
                  key={q.id}
                  className="flex items-center gap-2 rounded-lg border border-border-subtle bg-surface-inset px-2.5 py-2"
                >
                  <span
                    className={`font-mono text-[0.5625rem] uppercase px-1.5 py-[1px] rounded shrink-0 ${
                      q.status === 'dispatching'
                        ? 'bg-primary/15 text-primary'
                        : q.status === 'needs_readback'
                          ? 'bg-status-warning-bg text-status-warning-fg'
                          : 'bg-glass text-text-muted'
                    }`}
                  >
                    {q.status === 'dispatching'
                      ? t('queue.dispatching')
                      : q.status === 'needs_readback'
                        ? t('queue.needsReadback')
                        : t('queue.pending')}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[0.6875rem] text-text-secondary">
                    {q.body.prompt || '(empty)'}
                  </span>
                  {q.status === 'needs_readback' && (
                    <button
                      type="button"
                      onClick={() => requestReadback(q.id)}
                      title={t('queue.refreshReadback')}
                      className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
                    >
                      <RefreshCw className="w-3 h-3" />
                    </button>
                  )}
                  {q.status === 'pending' && (
                    <button
                      type="button"
                      onClick={() => cancelPendingRequest(q.id)}
                      title={t('queue.cancel')}
                      className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-text-muted transition-colors hover:bg-status-failed-bg hover:text-status-failed-fg"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
