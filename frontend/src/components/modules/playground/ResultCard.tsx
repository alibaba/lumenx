'use client';

import { useState, useCallback } from 'react';
import { Download, Video, Star, Copy, Check } from 'lucide-react';
import { API_URL, playgroundApi } from '@/lib/api';
import { usePlaygroundStore, type PlaygroundGeneration } from './usePlaygroundStore';

interface ResultCardProps {
  generation: PlaygroundGeneration;
  onGenerateVideo?: (imagePath: string) => void;
  onRetry?: (generation: PlaygroundGeneration) => void;
  onOpenDetail?: (generation: PlaygroundGeneration) => void;
  onDelete?: (generation: PlaygroundGeneration) => void;
}

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

function formatTime(dateStr: string): string {
  const date = new Date(dateStr);
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

function getElapsedProgress(createdAt: string): number {
  const elapsed = Date.now() - new Date(createdAt).getTime();
  // Estimate ~60s for generation, cap at 90%
  const progress = Math.min(elapsed / 60000, 0.9);
  return progress * 100;
}

function FailedCard({ generation, onRetry, onDelete }: { generation: PlaygroundGeneration; onRetry?: (g: PlaygroundGeneration) => void; onDelete?: (g: PlaygroundGeneration) => void }) {
  const { prompt, model_id, mode, created_at, error } = generation;
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!error) return;
    navigator.clipboard.writeText(error).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="rounded-xl border border-red-500/25 bg-red-500/[0.04] overflow-hidden">
      <div
        className="relative overflow-hidden bg-surface-inset flex flex-col items-center justify-center cursor-pointer"
        style={{ aspectRatio: expanded ? undefined : '16/9', minHeight: expanded ? 120 : undefined }}
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="absolute inset-0 bg-red-500/[0.05]" />
        <div className="relative text-center px-4 py-3 w-full">
          <p className="font-mono text-[10px] text-red-400/80 uppercase mb-2">生成失败</p>
          {error && (
            <p className={`text-[10px] text-text-secondary leading-relaxed break-all ${expanded ? '' : 'line-clamp-2'}`}>
              {error}
            </p>
          )}
        </div>

        {/* Action bar */}
        <div className="relative flex items-center gap-2 pb-2">
          {onRetry && (
            <button
              onClick={(e) => { e.stopPropagation(); onRetry(generation); }}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-medium text-primary bg-primary/10 hover:bg-primary/20 transition-colors"
            >
              ↻ 重试
            </button>
          )}
          {onDelete && (
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(generation); }}
              className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium text-red-400/60 hover:text-red-400 hover:bg-red-500/10 transition-colors"
            >
              × 删除
            </button>
          )}
          {error && (
            <button
              onClick={handleCopy}
              className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors"
            >
              {copied ? <Check className="w-3 h-3 text-green-400" /> : <Copy className="w-3 h-3" />}
              {copied ? '已复制' : '复制全文'}
            </button>
          )}
          <span className="text-[9px] text-text-muted ml-auto">
            {expanded ? '收起' : '展开'}
          </span>
        </div>
      </div>

      <div className="px-3 py-[10px]">
        <p className="text-[11px] text-text-secondary line-clamp-2 mb-1.5">{prompt}</p>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[9px] bg-glass text-text-secondary rounded px-[6px] py-[2px]">
            {model_id || mode}
          </span>
          <span className="font-mono text-[9px] text-text-muted">
            {formatTime(created_at)}
          </span>
        </div>
      </div>
    </div>
  );
}

function CompletedCard({ generation, onGenerateVideo, onOpenDetail }: { generation: PlaygroundGeneration; onGenerateVideo?: (path: string) => void; onOpenDetail?: (generation: PlaygroundGeneration) => void }) {
  const { prompt, model_id, mode, outputs, created_at } = generation;
  const output = outputs[0];
  const isVideo = output?.media_type === 'video' || ['t2v', 'i2v', 'r2v', 'v2v'].includes(mode);
  const [saving, setSaving] = useState(false);

  const saved = output?.saved_to_library ?? false;
  const mediaUrl = output?.media_path ? getMediaUrl(output.media_path) : null;
  const updateGeneration = usePlaygroundStore((s) => s.updateGeneration);

  const handleDownload = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!mediaUrl) return;
    try {
      const resp = await fetch(mediaUrl);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = output?.media_path?.split('/').pop() || 'download';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      window.open(mediaUrl, '_blank');
    }
  }, [mediaUrl, output]);

  const handleSaveToLibrary = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
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
      console.error('[Playground] Save to library failed:', err);
    } finally {
      setSaving(false);
    }
  }, [generation, output, saved, saving, updateGeneration]);

  return (
    <div
      className="group rounded-xl border border-glass-border bg-glass overflow-hidden hover:border-primary/35 transition cursor-pointer"
      onClick={() => onOpenDetail?.(generation)}
    >
      {/* Media area */}
      <div className="relative overflow-hidden bg-surface-inset" style={{ aspectRatio: '16/9' }}>
        {mediaUrl ? (
          isVideo ? (
            <div className="w-full h-full bg-surface-inset flex items-center justify-center">
              <Video className="w-8 h-8 text-text-muted" />
            </div>
          ) : (
            <img src={mediaUrl} alt={prompt} className="w-full h-full object-cover" />
          )
        ) : (
          <div className="w-full h-full bg-surface-inset" />
        )}

        {/* Video badge top-left */}
        {isVideo && (
          <span className="absolute top-2 left-2 font-mono text-[9px] bg-black/60 text-white/80 backdrop-blur-sm rounded px-[6px] py-[2px] uppercase">
            {MODE_LABELS[mode] || mode}
          </span>
        )}

        {/* Bottom gradient toolbar — appears on hover */}
        <div className="absolute bottom-0 left-0 right-0 h-12 bg-gradient-to-t from-black/70 to-transparent flex items-end justify-end gap-1.5 px-3 pb-2.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={handleDownload}
            className="w-7 h-7 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center hover:bg-white/25 transition"
            title="下载"
          >
            <Download className="w-3.5 h-3.5 text-white" />
          </button>
          {output?.media_type === 'image' && onGenerateVideo && (
            <button
              onClick={(e) => { e.stopPropagation(); onGenerateVideo(output.media_path); }}
              className="w-7 h-7 rounded-full bg-white/10 backdrop-blur-sm flex items-center justify-center hover:bg-white/25 transition"
              title="生成视频"
            >
              <Video className="w-3.5 h-3.5 text-white" />
            </button>
          )}
          <button
            onClick={handleSaveToLibrary}
            className={`w-7 h-7 rounded-full backdrop-blur-sm flex items-center justify-center transition ${saved ? 'bg-green-500/20' : 'bg-white/10 hover:bg-white/25'}`}
            title={saved ? '已收藏' : '收藏'}
          >
            <Star className={`w-3.5 h-3.5 ${saved ? 'text-green-400 fill-green-400' : 'text-white'}`} />
          </button>
        </div>
      </div>

      {/* Info area */}
      <div className="px-3 py-[10px]">
        <p className="text-[11px] text-text-secondary line-clamp-2 mb-1.5">{prompt}</p>
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="font-mono text-[9px] bg-glass text-text-secondary rounded px-[6px] py-[2px]">
            {model_id || mode}
          </span>
          {/* Size or resolution tag */}
          {generation.parameters.size && (
            <span className="font-mono text-[9px] bg-glass text-text-muted rounded px-[6px] py-[2px]">
              {(generation.parameters.size as string).replace('*', '×').replace('x', '×')}
            </span>
          )}
          {generation.parameters.resolution && !generation.parameters.size && (
            <span className="font-mono text-[9px] bg-glass text-text-muted rounded px-[6px] py-[2px]">
              {generation.parameters.resolution as string}
            </span>
          )}
          {/* Mode badge */}
          <span className="font-mono text-[9px] bg-primary/10 text-primary rounded px-[6px] py-[2px] uppercase">
            {MODE_LABELS[mode] || mode}
          </span>
          <span className="font-mono text-[9px] text-text-muted ml-auto">{formatTime(created_at)}</span>
          {saved && (
            <span className="flex items-center gap-0.5 text-[9px] text-green-400/70">
              <Star className="w-2.5 h-2.5 fill-current" />
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ResultCard({ generation, onGenerateVideo, onRetry, onOpenDetail, onDelete }: ResultCardProps) {
  const { status, prompt, model_id, mode, created_at } = generation;

  // ─── PROCESSING STATE ───────────────────────────────────────────────────────
  if (status === 'pending' || status === 'processing') {
    return (
      <div className="rounded-xl border border-glass-border bg-glass overflow-hidden">
        {/* Media area */}
        <div className="relative overflow-hidden bg-surface-inset" style={{ aspectRatio: '16/9' }}>
          {/* Skeleton shimmer */}
          <div className="absolute inset-0 overflow-hidden">
            <div
              className="absolute inset-0 animate-shimmer"
              style={{
                background:
                  'linear-gradient(90deg, transparent 0%, var(--color-glass) 50%, transparent 100%)',
                backgroundSize: '200% 100%',
              }}
            />
          </div>

          {/* Centered spinner + text */}
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
            <div className="w-6 h-6 border-2 border-glass-border border-t-primary rounded-full animate-spin" />
            <span className="font-mono text-[10px] text-text-secondary uppercase">
              {status === 'pending' ? '排队中...' : '生成中...'}
            </span>
          </div>

          {/* Progress bar */}
          <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-glass">
            <div
              className="h-full bg-primary transition-all duration-1000 ease-out"
              style={{ width: `${getElapsedProgress(created_at)}%` }}
            />
          </div>
        </div>

        {/* Info area */}
        <div className="px-3 py-[10px]">
          <p className="text-[11px] text-text-secondary line-clamp-2 mb-1.5">{prompt}</p>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[9px] bg-glass text-text-secondary rounded px-[6px] py-[2px]">
              {model_id || mode}
            </span>
            <span className="font-mono text-[9px] text-text-muted">
              {formatTime(created_at)}
            </span>
          </div>
        </div>
      </div>
    );
  }

  // ─── FAILED STATE ───────────────────────────────────────────────────────────
  if (status === 'failed') {
    return <FailedCard generation={generation} onRetry={onRetry} onDelete={onDelete} />;
  }

  // ─── COMPLETED STATE ────────────────────────────────────────────────────────
  return <CompletedCard generation={generation} onGenerateVideo={onGenerateVideo} onOpenDetail={onOpenDetail} />;
}
