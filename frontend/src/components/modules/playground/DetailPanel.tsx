'use client';

import { useEffect, useCallback, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  X,
  Download,
  Crown,
  Bookmark,
  Video,
  RotateCcw,
  Trash2,
  Copy,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { API_URL, playgroundApi } from '@/lib/api';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useTranslations } from 'next-intl';
import { usePlaygroundStore, type PlaygroundGeneration } from './usePlaygroundStore';
import { imageDraftValueText } from './playgroundImageContract';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface DetailPanelProps {
  generation: PlaygroundGeneration;
  allGenerations: PlaygroundGeneration[];
  focusOutputId?: string;
  onClose: () => void;
  onNavigate: (generation: PlaygroundGeneration) => void;
  onRetry?: (generation: PlaygroundGeneration) => void;
  onGenerateVideo?: (imagePath: string) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MODE_LABELS: Record<string, string> = {
  t2v: 'T2V',
  i2v: 'I2V',
  r2v: 'R2V',
  v2v: 'V2V',
  t2i: 'T2I',
  i2i: 'I2I',
};

function getMediaUrl(path: string): string {
  const relativePath = path.replace(/^output\//, '');
  return `${API_URL}/files/${relativePath}`;
}

function formatTimestamp(dateStr: string): string {
  const date = new Date(dateStr);
  const yyyy = date.getFullYear();
  const MM = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  const ss = String(date.getSeconds()).padStart(2, '0');
  return `${yyyy}-${MM}-${dd} ${hh}:${mm}:${ss}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function DetailPanel({
  generation: generationProp,
  allGenerations,
  focusOutputId,
  onClose,
  onNavigate,
  onRetry,
  onGenerateVideo,
}: DetailPanelProps) {
  const t = useTranslations('playground');
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const patchGenerationOutput = usePlaygroundStore((s) => s.patchGenerationOutput);
  const removeGeneration = usePlaygroundStore((s) => s.removeGeneration);
  const history = usePlaygroundStore((s) => s.history);
  const featuredByGen = usePlaygroundStore((s) => s.featuredByGen);
  const toggleFeatured = usePlaygroundStore((s) => s.toggleFeatured);
  const activationRef = useRef(0);
  const currentGenerationIdRef = useRef(generationProp.id);

  useEffect(() => {
    activationRef.current += 1;
    const activation = activationRef.current;
    const unsubscribe = subscribeRuntimeEnvironment(() => {
      activationRef.current += 1;
      setSaving(false);
      setDeleting(false);
      setActionError(null);
    });
    return () => {
      unsubscribe();
      if (activationRef.current === activation) activationRef.current += 1;
    };
  }, []);

  // Always read the latest generation from store (so saved_to_library stays in sync)
  const generation = history.find((g) => g.id === generationProp.id) ?? generationProp;
  currentGenerationIdRef.current = generation.id;

  useEffect(() => {
    setSaving(false);
    setDeleting(false);
    setActionError(null);
  }, [generation.id, focusOutputId]);

  // Determine media — focus the clicked output of a batch, else the first.
  const output =
    generation.outputs.find((o) => o.id === focusOutputId) ?? generation.outputs[0];
  const saved = output?.saved_to_library ?? false;
  const featured = output ? featuredByGen[generation.id] === output.id : false;
  const isVideo =
    output?.media_type === 'video' ||
    ['t2v', 'i2v', 'r2v', 'v2v'].includes(generation.mode);
  const mediaUrl = output?.media_path ? getMediaUrl(output.media_path) : null;

  // Navigation
  const currentIndex = allGenerations.findIndex((g) => g.id === generation.id);
  const hasPrev = currentIndex < allGenerations.length - 1;
  const hasNext = currentIndex > 0;

  const navigatePrev = useCallback(() => {
    if (hasPrev) onNavigate(allGenerations[currentIndex + 1]);
  }, [hasPrev, currentIndex, allGenerations, onNavigate]);

  const navigateNext = useCallback(() => {
    if (hasNext) onNavigate(allGenerations[currentIndex - 1]);
  }, [hasNext, currentIndex, allGenerations, onNavigate]);

  // Keyboard
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowLeft') navigatePrev();
      if (e.key === 'ArrowRight') navigateNext();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose, navigatePrev, navigateNext]);

  // Lock body scroll
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  // Actions
  const handleCopyPrompt = () => {
    navigator.clipboard.writeText(generation.prompt).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleDownload = () => {
    if (!mediaUrl) return;
    const a = document.createElement('a');
    a.href = mediaUrl;
    a.download = output?.media_path?.split('/').pop() || 'download';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const handleSaveToLibrary = async () => {
    if (!output || saving || saved) return;
    const capturedRuntime = getRuntimeEnvironment();
    const activation = activationRef.current;
    const targetGenerationId = generation.id;
    const targetOutputId = output.id;
    setSaving(true);
    setActionError(null);
    try {
      await playgroundApi.saveToLibrary(targetGenerationId, targetOutputId);
      if (activationRef.current !== activation || (capturedRuntime && !isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) return;
      patchGenerationOutput(targetGenerationId, targetOutputId, { saved_to_library: true });
    } catch (err) {
      console.error('[DetailPanel] Save to library failed:', err);
      if (activationRef.current === activation && currentGenerationIdRef.current === targetGenerationId
        && (!capturedRuntime || isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) {
        setActionError(t('detail.saveFailed'));
      }
    } finally {
      if (activationRef.current === activation && currentGenerationIdRef.current === targetGenerationId
        && (!capturedRuntime || isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) {
        setSaving(false);
      }
    }
  };

  const handleDelete = async () => {
    if (deleting) return;
    const capturedRuntime = getRuntimeEnvironment();
    const activation = activationRef.current;
    const targetGenerationId = generation.id;
    setDeleting(true);
    setActionError(null);
    try {
      await playgroundApi.deleteGeneration(targetGenerationId);
      if (activationRef.current !== activation || (capturedRuntime && !isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) return;
      removeGeneration(targetGenerationId);
      if (currentGenerationIdRef.current === targetGenerationId) onClose();
    } catch (err) {
      console.error('[DetailPanel] Delete failed:', err);
      if (activationRef.current === activation && currentGenerationIdRef.current === targetGenerationId
        && (!capturedRuntime || isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) {
        setActionError(t('detail.deleteFailed'));
      }
    } finally {
      if (activationRef.current === activation && currentGenerationIdRef.current === targetGenerationId
        && (!capturedRuntime || isCurrentRuntimeEnvironment(capturedRuntime.key, capturedRuntime.generation))) {
        setDeleting(false);
      }
    }
  };

  // Build parameter entries
  const paramEntries: [string, string][] = [];
  const params = generation.parameters || {};
  const parameterText = (value: unknown) => typeof value === 'string' ? value : imageDraftValueText(value);
  if (params.size) paramEntries.push(['Size', parameterText(params.size)]);
  if (params.resolution) paramEntries.push(['Resolution', parameterText(params.resolution)]);
  if (params.aspect_ratio) paramEntries.push(['Aspect Ratio', parameterText(params.aspect_ratio)]);
  if (params.duration) paramEntries.push(['Duration', `${parameterText(params.duration)}s`]);
  if (generation.batch_size > 1)
    paramEntries.push(['Batch Size', String(generation.batch_size)]);
  if (params.seed !== undefined && params.seed !== null)
    paramEntries.push(['Seed', parameterText(params.seed)]);
  // Add remaining params
  const skipKeys = new Set([
    'size',
    'resolution',
    'aspect_ratio',
    'duration',
    'seed',
  ]);
  Object.entries(params).forEach(([key, value]) => {
    if (!skipKeys.has(key) && value !== undefined && value !== null && value !== '') {
      paramEntries.push([key, parameterText(value)]);
    }
  });

  const modal = (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-50 bg-overlay backdrop-blur-md"
        onClick={onClose}
      />

      {/* Container */}
      <div className="fixed inset-4 md:inset-8 z-50 bg-surface border border-glass-border rounded-[20px] shadow-2xl flex overflow-hidden">
        {/* ─── LEFT SIDE (Media) ─────────────────────────────────────────── */}
        <div className="relative w-[60%] h-full bg-surface-inset flex items-center justify-center">
          {mediaUrl ? (
            isVideo ? (
              <video
                src={mediaUrl}
                controls
                className="max-w-full max-h-full object-contain rounded"
              />
            ) : (
              <img
                src={mediaUrl}
                alt={generation.prompt}
                className="max-w-full max-h-full object-contain"
              />
            )
          ) : (
            <div className="flex flex-col items-center gap-2 text-text-muted">
              <Video className="w-12 h-12" />
              <span className="font-mono text-xs">No media</span>
            </div>
          )}

          {/* Amber halation overlay — only when saved to library (mirrors ResultCard) */}
          {saved && (
            <div className="atelier-proj-halation pointer-events-none absolute inset-0 z-[1]" />
          )}

          {/* Navigation arrows */}
          {hasPrev && (
            <button
              onClick={navigatePrev}
              className="absolute left-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-elevated backdrop-blur-sm border border-glass-border flex items-center justify-center hover:bg-hover-bg transition-colors"
            >
              <ChevronLeft className="w-5 h-5 text-foreground/80" />
            </button>
          )}
          {hasNext && (
            <button
              onClick={navigateNext}
              className="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-elevated backdrop-blur-sm border border-glass-border flex items-center justify-center hover:bg-hover-bg transition-colors"
            >
              <ChevronRight className="w-5 h-5 text-foreground/80" />
            </button>
          )}
        </div>

        {/* ─── RIGHT SIDE (Details) — 3 zones: header / scroll body / pinned footer ─── */}
        <div className="relative w-[40%] h-full overflow-y-auto border-l border-border-subtle">
          {/* Close button */}
          <button
            onClick={onClose}
            className="absolute top-4 right-4 z-10 w-8 h-8 rounded-lg bg-glass border border-glass-border flex items-center justify-center hover:bg-hover-bg transition-colors"
          >
            <X className="w-4 h-4 text-text-muted" />
          </button>

          {/* ── Header ── */}
          <div className="px-6 pt-6 pb-4 border-b border-border-subtle pr-14">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="font-mono text-[0.5625rem] bg-primary/15 text-primary rounded px-[6px] py-[2px] uppercase tracking-[0.1em]">
                {MODE_LABELS[generation.mode] || generation.mode}
              </span>
              <span className="font-mono text-[0.5625rem] text-text-muted uppercase tracking-[0.1em]">
                {generation.id.slice(0, 8)}
              </span>
            </div>
            <h2 className="font-display atelier-display text-xl font-semibold tracking-tight text-foreground leading-tight">
              {generation.model_id}
            </h2>
            <p className="font-mono text-[0.625rem] text-text-muted mt-1.5">
              {formatTimestamp(generation.created_at)}
            </p>
          </div>

          {/* ── Body ── */}
          <div className="px-6 py-5 space-y-5">
            {/* Prompt */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-mono text-[0.625rem] font-medium uppercase tracking-[0.18em] text-text-muted">
                  PROMPT
                </h3>
                <button
                  onClick={handleCopyPrompt}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[0.625rem] text-text-muted hover:text-foreground hover:bg-hover-bg transition-colors"
                >
                  <Copy className="w-3 h-3" />
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <div className="rounded-[14px] bg-surface-inset border border-border-subtle p-4 max-h-48 overflow-y-auto">
                <p className="font-display italic text-[0.9375rem] text-text-secondary leading-relaxed whitespace-pre-wrap break-words">
                  {generation.prompt ? `“${generation.prompt}”` : '(empty)'}
                </p>
              </div>
            </div>

            {/* Parameters — labeled spec grid; first entry (Size) is a hero row */}
            {paramEntries.length > 0 && (
              <div>
                <h3 className="font-mono text-[0.625rem] font-medium uppercase tracking-[0.18em] text-text-muted mb-2">
                  {t('detail.parameters')}
                </h3>
                <div className="rounded-[14px] bg-surface-inset border border-border-subtle p-4 grid grid-cols-2 gap-x-4 gap-y-3.5">
                  {paramEntries.map(([label, value], i) => (
                    <div key={label} className={i === 0 ? 'col-span-2' : ''}>
                      <div className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-text-muted mb-1">
                        {label}
                      </div>
                      <div
                        className={`font-mono text-foreground ${
                          i === 0 ? 'text-[1.0625rem]' : 'text-sm'
                        }`}
                      >
                        {value}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Negative prompt */}
            {generation.negative_prompt && (
              <div>
                <h3 className="font-mono text-[0.625rem] font-medium uppercase tracking-[0.18em] text-text-muted mb-2">
                  NEGATIVE PROMPT
                </h3>
                <div className="rounded-[14px] bg-surface-inset border border-border-subtle p-4 max-h-28 overflow-y-auto">
                  <p className="text-[0.8125rem] text-text-secondary leading-relaxed whitespace-pre-wrap break-words">
                    {generation.negative_prompt}
                  </p>
                </div>
              </div>
            )}

            {/* Error display for failed generations */}
            {generation.status === 'failed' && generation.error && (
              <div>
                <h3 className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-status-failed-fg mb-2">
                  ERROR
                </h3>
                <div className="max-h-28 overflow-y-auto rounded-[16px] bg-status-failed-bg border border-status-failed-border p-4">
                  <p className="text-[0.6875rem] text-status-failed-fg leading-relaxed break-all font-mono">
                    {generation.error}
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* ── Actions (flow after content; not pinned to bottom) ── */}
          <div className="border-t border-border-subtle px-6 py-4 space-y-2.5">
            {/* Primary: Retry (failed) or Save to library */}
            {generation.status === 'failed' && onRetry ? (
              <button
                onClick={() => onRetry(generation)}
                className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-full bg-primary text-on-accent text-sm font-medium shadow-[var(--glow-primary)] hover:bg-primary-hover transition"
              >
                <RotateCcw className="w-4 h-4" />
                Retry
              </button>
            ) : output ? (
              <button
                onClick={handleSaveToLibrary}
                disabled={saving}
                className={`w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-full text-sm font-medium transition cursor-pointer disabled:opacity-50 disabled:cursor-wait ${
                  saved
                    ? 'bg-primary text-on-accent hover:bg-primary-hover'
                    : 'bg-primary text-on-accent shadow-[var(--glow-primary)] hover:bg-primary-hover'
                }`}
              >
                <Bookmark className={`w-4 h-4 ${saved ? 'fill-current' : ''}`} />
                {saving ? t('detail.saving') : saved ? t('detail.savedCancel') : t('detail.saveToLibrary')}
              </button>
            ) : null}

            {/* Featured (best-of-batch) toggle — amber only when active */}
            {output && (
              <button
                onClick={() => toggleFeatured(generation.id, output.id)}
                className={`w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-full text-sm font-medium transition border ${
                  featured
                    ? 'bg-status-starred-bg border-status-starred-border text-status-starred-fg'
                    : 'bg-surface-inset border-glass-border text-text-secondary hover:text-foreground hover:bg-hover-bg'
                }`}
                title={t('card.featured')}
              >
                <Crown className={`w-4 h-4 ${featured ? 'fill-status-starred-solid' : ''}`} />
                {t('card.featured')}
              </button>
            )}

            {output && (
              <p className="text-[0.625rem] leading-relaxed text-text-muted">
                {t('detail.markHint')}
              </p>
            )}

            {actionError && (
              <p className="rounded-[12px] border border-status-failed-border bg-status-failed-bg px-3 py-2 text-[0.6875rem] text-status-failed-fg">
                {actionError}
              </p>
            )}

            {/* Secondary row: Download + Generate Video (neutral ghosts) */}
            {(mediaUrl || (!isVideo && output?.media_path && onGenerateVideo)) && (
              <div className="flex gap-2">
                {mediaUrl && (
                  <button
                    onClick={handleDownload}
                    className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2.5 rounded-full bg-surface-inset border border-glass-border text-text-secondary text-[0.8125rem] font-medium hover:text-foreground hover:bg-hover-bg transition"
                  >
                    <Download className="w-4 h-4" />
                    Download
                  </button>
                )}
                {!isVideo && output?.media_path && onGenerateVideo && (
                  <button
                    onClick={() => onGenerateVideo(output.media_path)}
                    className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2.5 rounded-full bg-surface-inset border border-glass-border text-text-secondary text-[0.8125rem] font-medium hover:text-foreground hover:bg-hover-bg transition"
                  >
                    <Video className="w-4 h-4" />
                    Generate Video
                  </button>
                )}
              </div>
            )}

            {/* Delete — subdued, red only on hover */}
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-full text-[0.8125rem] font-medium text-text-muted hover:text-status-failed-fg hover:bg-status-failed-bg transition disabled:opacity-40"
            >
              <Trash2 className="w-4 h-4" />
              {deleting ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>
      </div>
    </>
  );

  return createPortal(modal, document.body);
}
