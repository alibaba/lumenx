'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Video, AlertCircle } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { API_URL } from '@/lib/api';
import type { PlaygroundGeneration } from './usePlaygroundStore';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface GalleryViewProps {
  generations: PlaygroundGeneration[];
  onOpenDetail: (gen: PlaygroundGeneration) => void;
  onRetry?: (gen: PlaygroundGeneration) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getMediaUrl(path: string): string {
  return API_URL + '/files/' + path.replace(/^output\//, '');
}

function formatTime(dateStr: string): string {
  const date = new Date(dateStr);
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

const VIDEO_MODES = new Set(['t2v', 'i2v', 'r2v', 'v2v']);
const TERMINAL_ISSUE_STATUSES = new Set(['failed', 'needs_confirmation', 'cancelled']);

function isTerminalIssue(status: PlaygroundGeneration['status']): boolean {
  return TERMINAL_ISSUE_STATUSES.has(status);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function GalleryView({
  generations,
  onOpenDetail,
  onRetry,
}: GalleryViewProps) {
  const t = useTranslations('playground');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const thumbnailStripRef = useRef<HTMLDivElement>(null);

  // Clamp selectedIndex when generations change
  useEffect(() => {
    if (selectedIndex >= generations.length) {
      setSelectedIndex(Math.max(0, generations.length - 1));
    }
  }, [generations.length, selectedIndex]);

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        setSelectedIndex((prev) => Math.max(0, prev - 1));
      } else if (e.key === 'ArrowRight') {
        setSelectedIndex((prev) =>
          Math.min(generations.length - 1, prev + 1)
        );
      } else if (e.key === 'Enter') {
        if (generations[selectedIndex]) {
          onOpenDetail(generations[selectedIndex]);
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [generations, selectedIndex, onOpenDetail]);

  // Scroll selected thumbnail into view
  useEffect(() => {
    const strip = thumbnailStripRef.current;
    if (!strip) return;
    const thumb = strip.children[selectedIndex] as HTMLElement | undefined;
    if (thumb) {
      thumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }
  }, [selectedIndex]);

  const handleClick = useCallback(() => {
    if (generations[selectedIndex]) {
      onOpenDetail(generations[selectedIndex]);
    }
  }, [generations, selectedIndex, onOpenDetail]);

  if (generations.length === 0) {
    return (
      <div className="flex flex-col h-full items-center justify-center">
        <p className="text-sm text-text-muted">No results to display</p>
      </div>
    );
  }

  const current = generations[selectedIndex];
  if (!current) return null;

  const output = current.outputs[0];
  const isVideo =
    output?.media_type === 'video' || VIDEO_MODES.has(current.mode);
  const mediaUrl = output?.media_path ? getMediaUrl(output.media_path) : null;
  const terminalIssue = isTerminalIssue(current.status);
  const failedAttempts = current.attempts?.filter((attempt) => isTerminalIssue(attempt.status)).length ?? 0;
  const issueLabel = current.status === 'needs_confirmation'
    ? t('card.needsConfirmation')
    : current.status === 'cancelled'
      ? t('card.cancelled')
      : failedAttempts > 0
        ? t('card.partialAttempts', { count: failedAttempts })
        : t('card.failed');
  const errorText = current.error ?? current.attempts?.map((attempt) => attempt.error).filter(Boolean).join('\n');
  const canRenderMedia = Boolean(mediaUrl) && (current.status === 'completed' || terminalIssue);

  return (
    <div className="flex flex-col h-full">
      {/* Main media area */}
      <div
        className="flex-1 min-h-0 overflow-hidden flex items-center justify-center p-6 bg-background"
        onClick={handleClick}
      >
        {canRenderMedia ? (
          isVideo ? (
            <video
              key={current.id}
              src={mediaUrl ?? undefined}
              controls
              className="max-w-full max-h-full object-contain rounded-lg cursor-pointer hover:ring-2 hover:ring-primary/30 transition-all duration-200"
            />
          ) : (
            <img
              key={current.id}
              src={mediaUrl ?? undefined}
              alt={current.prompt}
              className="max-w-full max-h-full object-contain rounded-lg cursor-pointer hover:scale-[1.01] hover:ring-2 hover:ring-primary/30 transition-all duration-200"
            />
          )
        ) : terminalIssue ? (
          <div className="flex flex-col items-center gap-3 text-status-failed-fg">
            <AlertCircle className="w-10 h-10" />
            <p className="font-mono text-xs">{issueLabel}</p>
            {errorText && (
              <p className="text-[0.625rem] text-text-muted max-w-xs text-center line-clamp-3">
                {errorText}
              </p>
            )}
            {onRetry && (
              <button
                onClick={() => onRetry(current)}
                className="mt-2 px-3 py-1.5 rounded text-xs font-medium text-primary bg-primary/10 hover:bg-primary/20 transition-colors"
              >
                {t('card.retry')}
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3 text-text-muted">
            <div className="w-8 h-8 border-2 border-glass-border border-t-primary rounded-full animate-spin" />
            <p className="font-mono text-xs">
              {current.status === 'pending' ? 'Queued...' : 'Generating...'}
            </p>
          </div>
        )}
      </div>

      {/* Info bar */}
      <div className="px-6 py-3 bg-surface space-y-1.5">
        <p className="text-xs text-text-secondary line-clamp-2 leading-relaxed cursor-pointer hover:text-foreground transition-colors" onClick={handleClick} title={t('gallery.viewDetail')}>
          {current.prompt || '(no prompt)'}
        </p>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[0.5625rem] bg-elevated text-text-muted rounded px-[6px] py-[2px]">
            {current.model_id || current.mode}
          </span>
          {terminalIssue && (
            <span className="font-mono text-[0.5625rem] bg-status-failed-bg text-status-failed-fg border border-status-failed-border rounded px-[6px] py-[2px]">
              {issueLabel}
            </span>
          )}
          {typeof current.parameters.size === 'string' && current.parameters.size && (
            <span className="font-mono text-[0.5625rem] bg-glass text-text-muted rounded px-[6px] py-[2px]">
              {(current.parameters.size as string).replace(/[*x]/g, '×')}
            </span>
          )}
          {typeof current.parameters.resolution === 'string' && current.parameters.resolution && !current.parameters.size && (
            <span className="font-mono text-[0.5625rem] bg-glass text-text-muted rounded px-[6px] py-[2px]">
              {current.parameters.resolution as string}
            </span>
          )}
          <span className="font-mono text-[0.5625rem] text-text-muted ml-auto">
            {formatTime(current.created_at)}
          </span>
        </div>
      </div>

      {/* Thumbnail strip */}
      <div className="h-20 shrink-0 px-4 py-2 border-t border-glass-border overflow-x-auto">
        <div
          ref={thumbnailStripRef}
          className="flex gap-2 h-full items-center"
        >
          {generations.map((gen, idx) => {
            const genOutput = gen.outputs[0];
            const genIsVideo =
              genOutput?.media_type === 'video' || VIDEO_MODES.has(gen.mode);
            const genMediaUrl = genOutput?.media_path
              ? getMediaUrl(genOutput.media_path)
              : null;
            const isSelected = idx === selectedIndex;
            const genTerminalIssue = isTerminalIssue(gen.status);
            const isFailed = genTerminalIssue && !genMediaUrl;

            return (
              <button
                key={gen.id}
                onClick={() => setSelectedIndex(idx)}
                className={`relative w-14 h-14 rounded-md overflow-hidden border-2 cursor-pointer shrink-0 transition-colors ${
                  isSelected
                    ? 'border-primary'
                    : 'border-transparent hover:border-foreground/30'
                }`}
              >
                {isFailed ? (
                  <div className="w-full h-full bg-status-failed-bg flex items-center justify-center">
                    <AlertCircle className="w-4 h-4 text-status-failed-fg" />
                  </div>
                ) : genIsVideo ? (
                  <div className="w-full h-full bg-gradient-to-br from-elevated to-surface flex items-center justify-center">
                    <Video className="w-4 h-4 text-text-muted" />
                  </div>
                ) : genMediaUrl ? (
                  <img
                    src={genMediaUrl}
                    alt=""
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full bg-glass flex items-center justify-center">
                    <div className="w-3 h-3 border border-glass-border border-t-primary rounded-full animate-spin" />
                  </div>
                )}
                {genTerminalIssue && genMediaUrl && (
                  <span className="absolute right-0.5 top-0.5 rounded-full bg-status-failed-bg p-0.5 text-status-failed-fg">
                    <AlertCircle className="w-3 h-3" />
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
