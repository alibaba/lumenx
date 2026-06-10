'use client';

import { useEffect, useCallback, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  X,
  Download,
  Star,
  Video,
  RotateCcw,
  Trash2,
  Copy,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { API_URL, playgroundApi } from '@/lib/api';
import { usePlaygroundStore, type PlaygroundGeneration } from './usePlaygroundStore';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface DetailPanelProps {
  generation: PlaygroundGeneration;
  allGenerations: PlaygroundGeneration[];
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
  onClose,
  onNavigate,
  onRetry,
  onGenerateVideo,
}: DetailPanelProps) {
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const updateGeneration = usePlaygroundStore((s) => s.updateGeneration);
  const history = usePlaygroundStore((s) => s.history);

  // Always read the latest generation from store (so saved_to_library stays in sync)
  const generation = history.find((g) => g.id === generationProp.id) ?? generationProp;
  const saved = generation.outputs[0]?.saved_to_library ?? false;

  // Determine media
  const output = generation.outputs[0];
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
    if (!output || saving) return;
    setSaving(true);
    try {
      const newSaved = !saved;
      if (newSaved) {
        await playgroundApi.saveToLibrary(generation.id, output.id);
      }
      const updatedOutputs = generation.outputs.map((o) =>
        o.id === output.id ? { ...o, saved_to_library: newSaved } : o
      );
      updateGeneration({ ...generation, outputs: updatedOutputs });
    } catch (err) {
      console.error('[DetailPanel] Save to library failed:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (deleting) return;
    setDeleting(true);
    try {
      await playgroundApi.deleteGeneration(generation.id);
      onClose();
    } catch (err) {
      console.error('[DetailPanel] Delete failed:', err);
    } finally {
      setDeleting(false);
    }
  };

  // Build parameter entries
  const paramEntries: [string, string][] = [];
  const params = generation.parameters || {};
  if (params.size) paramEntries.push(['Size', params.size]);
  if (params.resolution) paramEntries.push(['Resolution', params.resolution]);
  if (params.aspect_ratio) paramEntries.push(['Aspect Ratio', params.aspect_ratio]);
  if (params.duration) paramEntries.push(['Duration', `${params.duration}s`]);
  if (generation.batch_size > 1)
    paramEntries.push(['Batch Size', String(generation.batch_size)]);
  if (params.seed !== undefined && params.seed !== null)
    paramEntries.push(['Seed', String(params.seed)]);
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
      paramEntries.push([key, String(value)]);
    }
  });

  const modal = (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-50 bg-black/90 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Container */}
      <div className="fixed inset-4 md:inset-8 z-50 bg-elevated border border-glass-border rounded-2xl shadow-2xl flex overflow-hidden">
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

          {/* Navigation arrows */}
          {hasPrev && (
            <button
              onClick={navigatePrev}
              className="absolute left-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-white/[0.06] backdrop-blur-sm border border-white/[0.08] flex items-center justify-center hover:bg-white/[0.12] transition-colors"
            >
              <ChevronLeft className="w-5 h-5 text-white/70" />
            </button>
          )}
          {hasNext && (
            <button
              onClick={navigateNext}
              className="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-white/[0.06] backdrop-blur-sm border border-white/[0.08] flex items-center justify-center hover:bg-white/[0.12] transition-colors"
            >
              <ChevronRight className="w-5 h-5 text-white/70" />
            </button>
          )}
        </div>

        {/* ─── RIGHT SIDE (Details) ──────────────────────────────────────── */}
        <div className="relative w-[40%] h-full overflow-y-auto p-6 border-l border-glass-border">
          {/* Close button */}
          <button
            onClick={onClose}
            className="absolute top-4 right-4 w-8 h-8 rounded-lg bg-glass border border-glass-border flex items-center justify-center hover:bg-hover-bg transition-colors"
          >
            <X className="w-4 h-4 text-text-secondary" />
          </button>

          {/* Section 1: Title */}
          <div className="mb-6 pr-10">
            <h2 className="text-lg font-semibold text-foreground mb-1">
              {generation.model_id}
            </h2>
            <div className="flex items-center gap-2 mb-1">
              <span className="font-mono text-[10px] bg-glass text-text-secondary rounded px-[6px] py-[2px] uppercase">
                {MODE_LABELS[generation.mode] || generation.mode}
              </span>
              <span className="font-mono text-[10px] text-text-muted">
                {generation.id.slice(0, 8)}
              </span>
            </div>
            <p className="font-mono text-[11px] text-text-muted">
              {formatTimestamp(generation.created_at)}
            </p>
          </div>

          {/* Section 2: Actions */}
          <div className="flex flex-col gap-2 mb-6">
            {mediaUrl && (
              <button
                onClick={handleDownload}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-glass border border-glass-border text-text-secondary text-sm font-medium hover:bg-hover-bg hover:text-foreground transition-colors"
              >
                <Download className="w-4 h-4" />
                Download
              </button>
            )}
            {output && (
              <button
                onClick={handleSaveToLibrary}
                disabled={saving}
                className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border text-sm font-medium transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-wait ${
                  saved
                    ? 'bg-green-500/10 border-green-500/25 text-green-500 hover:bg-green-500/15'
                    : 'bg-glass border-glass-border text-text-secondary hover:bg-hover-bg hover:text-foreground'
                }`}
              >
                <Star
                  className={`w-4 h-4 ${saved ? 'fill-green-400 text-green-400' : ''}`}
                />
                {saving ? '处理中...' : saved ? '已收藏（点击取消）' : '收藏到资产库'}
              </button>
            )}
            {!isVideo && output?.media_path && onGenerateVideo && (
              <button
                onClick={() => onGenerateVideo(output.media_path)}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary/10 border border-primary/20 text-primary text-sm font-medium hover:bg-primary/20 transition-colors"
              >
                <Video className="w-4 h-4" />
                Generate Video
              </button>
            )}
            {generation.status === 'failed' && onRetry && (
              <button
                onClick={() => onRetry(generation)}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 text-sm font-medium hover:bg-amber-500/20 transition-colors"
              >
                <RotateCcw className="w-4 h-4" />
                Retry
              </button>
            )}
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-red-500/[0.06] border border-red-500/[0.12] text-red-400/80 text-sm font-medium hover:bg-red-500/[0.12] transition-colors"
            >
              <Trash2 className="w-4 h-4" />
              {deleting ? 'Deleting...' : 'Delete'}
            </button>
          </div>

          {/* Section 3: Prompt */}
          <div className="mb-6">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-amber-500/80">
                PROMPT
              </h3>
              <button
                onClick={handleCopyPrompt}
                className="flex items-center gap-1 px-2 py-1 rounded text-[10px] text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors"
              >
                <Copy className="w-3 h-3" />
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <div className="max-h-40 overflow-y-auto rounded-lg bg-input-bg border border-glass-border p-3">
              <p className="text-[12px] text-text-secondary leading-relaxed whitespace-pre-wrap break-words">
                {generation.prompt || '(empty)'}
              </p>
            </div>
          </div>

          {/* Section 4: Parameters */}
          {paramEntries.length > 0 && (
            <div className="mb-6">
              <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-amber-500/80 mb-2">
                参数
              </h3>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {paramEntries.map(([label, value]) => (
                  <div key={label}>
                    <p className="text-[10px] text-text-secondary mb-0.5">{label}</p>
                    <p className="text-[12px] text-foreground font-mono truncate">
                      {value}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Section 5: Negative prompt */}
          {generation.negative_prompt && (
            <div className="mb-6">
              <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-amber-500/80 mb-2">
                NEGATIVE PROMPT
              </h3>
              <div className="max-h-28 overflow-y-auto rounded-lg bg-input-bg border border-glass-border p-3">
                <p className="text-[12px] text-text-secondary leading-relaxed whitespace-pre-wrap break-words">
                  {generation.negative_prompt}
                </p>
              </div>
            </div>
          )}

          {/* Error display for failed generations */}
          {generation.status === 'failed' && generation.error && (
            <div className="mb-6">
              <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-red-400/80 mb-2">
                ERROR
              </h3>
              <div className="max-h-28 overflow-y-auto rounded-lg bg-red-500/[0.04] border border-red-500/[0.12] p-3">
                <p className="text-[11px] text-red-300/80 leading-relaxed break-all font-mono">
                  {generation.error}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );

  return createPortal(modal, document.body);
}
