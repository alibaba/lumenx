'use client';

import { useState, useMemo, useCallback } from 'react';
import { Sparkles, Grid3x3, GalleryHorizontal } from 'lucide-react';
import { usePlaygroundStore, type PlaygroundGeneration } from './usePlaygroundStore';
import { playgroundApi } from '@/lib/api';
import ResultCard from './ResultCard';
import GalleryView from './GalleryView';
import DetailPanel from './DetailPanel';

type FilterType = 'all' | 'image' | 'video';

const VIDEO_MODES = new Set(['t2v', 'i2v', 'r2v', 'v2v']);

function formatSessionLabel(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const itemDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());

  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');

  if (itemDay.getTime() === today.getTime()) {
    return `今天 · ${hh}:${mm}`;
  }
  if (itemDay.getTime() === yesterday.getTime()) {
    return `昨天 · ${hh}:${mm}`;
  }
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return `${month}/${day} · ${hh}:${mm}`;
}

export default function ResultGallery() {
  const { history, startGeneration, updateGeneration } = usePlaygroundStore();
  const [activeFilter, setActiveFilter] = useState<FilterType>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'gallery'>('grid');
  const [detailGen, setDetailGen] = useState<PlaygroundGeneration | null>(null);

  const handleRetry = useCallback(async (gen: PlaygroundGeneration) => {
    try {
      const resp = await playgroundApi.generate({
        mode: gen.mode,
        model_id: gen.model_id,
        prompt: gen.prompt,
        negative_prompt: gen.negative_prompt || undefined,
        input_media: gen.input_media.length > 0 ? gen.input_media : undefined,
        parameters: Object.keys(gen.parameters).length > 0 ? gen.parameters : undefined,
        batch_size: gen.batch_size > 1 ? gen.batch_size : undefined,
      });
      const newGen: PlaygroundGeneration = {
        id: resp.id,
        mode: resp.mode as PlaygroundGeneration['mode'],
        model_id: resp.model_id,
        prompt: resp.prompt,
        negative_prompt: resp.negative_prompt,
        input_media: resp.input_media,
        parameters: resp.parameters,
        batch_size: resp.batch_size,
        outputs: [],
        status: resp.status as PlaygroundGeneration['status'],
        error: resp.error,
        created_at: resp.created_at,
      };
      startGeneration(newGen);
      // Poll for status
      const poll = setInterval(async () => {
        try {
          const s = await playgroundApi.getGenerationStatus(newGen.id);
          if (s.status === 'completed' || s.status === 'failed') {
            clearInterval(poll);
            const full = await playgroundApi.getGeneration(newGen.id);
            updateGeneration({
              ...newGen,
              status: full.status as PlaygroundGeneration['status'],
              outputs: full.outputs.map((o) => ({ id: o.id, media_path: o.media_path, media_type: o.media_type as 'image' | 'video', thumbnail_path: o.thumbnail_path, saved_to_library: o.saved_to_library })),
              error: full.error,
            });
          }
        } catch { clearInterval(poll); }
      }, 2000);
    } catch (err) {
      console.error('[Playground] Retry failed:', err);
    }
  }, [startGeneration, updateGeneration]);

  const handleDelete = useCallback(async (gen: PlaygroundGeneration) => {
    try {
      await playgroundApi.deleteGeneration(gen.id);
      usePlaygroundStore.getState().removeGeneration(gen.id);
    } catch (err) {
      console.error('[Playground] Delete failed:', err);
    }
  }, []);

  const filtered = useMemo(() => {
    if (activeFilter === 'all') return history;
    if (activeFilter === 'image') {
      return history.filter((g) => !VIDEO_MODES.has(g.mode));
    }
    return history.filter((g) => VIDEO_MODES.has(g.mode));
  }, [history, activeFilter]);

  // Sort descending by created_at
  const sorted = useMemo(
    () =>
      [...filtered].sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [filtered],
  );

  // Build items with session dividers
  const itemsWithDividers = useMemo(() => {
    const result: Array<
      | { type: 'generation'; data: PlaygroundGeneration }
      | { type: 'divider'; label: string; key: string }
    > = [];

    for (let i = 0; i < sorted.length; i++) {
      if (i > 0) {
        const prevTime = new Date(sorted[i - 1].created_at).getTime();
        const currTime = new Date(sorted[i].created_at).getTime();
        const gap = prevTime - currTime; // prev is more recent (descending)
        if (gap > 30 * 60 * 1000) {
          result.push({
            type: 'divider',
            label: formatSessionLabel(sorted[i].created_at),
            key: `divider-${sorted[i].id}`,
          });
        }
      }
      result.push({ type: 'generation', data: sorted[i] });
    }

    return result;
  }, [sorted]);

  // Flat list of generation data items (no dividers) for GalleryView and DetailPanel
  const dataItems = useMemo(
    () =>
      itemsWithDividers
        .filter((item): item is { type: 'generation'; data: PlaygroundGeneration } => item.type === 'generation')
        .map((item) => item.data),
    [itemsWithDividers],
  );

  const filters: { key: FilterType; label: string }[] = [
    { key: 'all', label: '全部' },
    { key: 'image', label: '图片' },
    { key: 'video', label: '视频' },
  ];

  if (history.length === 0) {
    return (
      <div className="flex flex-col flex-1 overflow-hidden min-w-0 items-center justify-center">
        <Sparkles className="w-12 h-12 text-text-secondary opacity-40 mb-4" />
        <p className="text-sm text-text-secondary mb-1">暂无生成结果</p>
        <p className="text-xs text-text-muted">输入提示词并点击生成，结果将展示在这里</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden min-w-0">
      {/* Header */}
      <div className="px-7 py-4 flex items-center justify-between border-b border-glass-border shrink-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-text-secondary">
            生成结果
          </span>
          <span className="font-mono text-[10px] bg-glass text-text-secondary rounded px-[6px] py-[1px]">
            {filtered.length}
          </span>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 rounded-md bg-glass p-[3px]">
            {filters.map((f) => (
              <button
                key={f.key}
                onClick={() => setActiveFilter(f.key)}
                className={`rounded px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  activeFilter === f.key
                    ? 'bg-hover-bg text-foreground'
                    : 'text-text-secondary hover:text-foreground'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-0.5 rounded-md bg-glass p-[3px]">
            <button
              onClick={() => setViewMode('grid')}
              className={`rounded p-1.5 transition-colors ${
                viewMode === 'grid'
                  ? 'bg-hover-bg text-foreground'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
              title="Grid view"
            >
              <Grid3x3 className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setViewMode('gallery')}
              className={`rounded p-1.5 transition-colors ${
                viewMode === 'gallery'
                  ? 'bg-hover-bg text-foreground'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
              title="Gallery view"
            >
              <GalleryHorizontal className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* Content area */}
      {viewMode === 'gallery' ? (
        <GalleryView
          generations={dataItems}
          onOpenDetail={setDetailGen}
          onRetry={handleRetry}
        />
      ) : (
        <div className="flex-1 overflow-y-auto p-6">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4 content-start">
            {itemsWithDividers.map((item) => {
              if (item.type === 'divider') {
                return (
                  <div
                    key={item.key}
                    className="col-span-full flex items-center gap-3 py-2"
                  >
                    <div className="flex-1 h-px bg-border-subtle" />
                    <span className="font-mono text-[9px] text-text-secondary uppercase tracking-wider whitespace-nowrap">
                      {item.label}
                    </span>
                    <div className="flex-1 h-px bg-border-subtle" />
                  </div>
                );
              }
              return (
                <ResultCard
                  key={item.data.id}
                  generation={item.data}
                  onRetry={handleRetry}
          onDelete={handleDelete}
                  onOpenDetail={setDetailGen}
                />
              );
            })}
          </div>
        </div>
      )}

      {/* Detail Panel */}
      {detailGen && (
        <DetailPanel
          generation={detailGen}
          allGenerations={dataItems}
          onClose={() => setDetailGen(null)}
          onNavigate={setDetailGen}
          onRetry={handleRetry}
        />
      )}
    </div>
  );
}
