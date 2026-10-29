'use client';

import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { useTranslations } from 'next-intl';
import { Sparkles, Grid3x3, GalleryHorizontal } from 'lucide-react';
import { usePlaygroundStore, type PlaygroundGeneration } from './usePlaygroundStore';
import { playgroundApi } from '@/lib/api';
import { beginPlaygroundGenerationRequest } from '@/lib/playgroundGenerationRequest';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { isSeedanceModel, PlaygroundCapabilityError, requireSeedanceCapability } from '@/lib/playgroundCapabilities';
import { buildPlaygroundRequestParameters, supportsPlaygroundNegativePrompt } from './playgroundParameterContract';
import { buildWan27ImageRequestParameters, isWan27TextToImage, isQwen20TextToImage, wan27ImageRetryNeedsEditing, qwen20ImageRetryNeedsEditing } from './playgroundImageContract';
import ResultCard from './ResultCard';
import GalleryView from './GalleryView';
import DetailPanel from './DetailPanel';
import QueuePanel from './QueuePanel';

type FilterType = 'all' | 'image' | 'video';

const VIDEO_MODES = new Set(['t2v', 'i2v', 'r2v', 'v2v']);

function formatSessionLabel(
  dateStr: string,
  todayLabel: string,
  yesterdayLabel: string,
): string {
  const date = new Date(dateStr);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const itemDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());

  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');

  if (itemDay.getTime() === today.getTime()) {
    return `${todayLabel} · ${hh}:${mm}`;
  }
  if (itemDay.getTime() === yesterday.getTime()) {
    return `${yesterdayLabel} · ${hh}:${mm}`;
  }
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return `${month}/${day} · ${hh}:${mm}`;
}

interface ResultGalleryProps {
  onLoadMore?: () => void;
}

export default function ResultGallery({ onLoadMore }: ResultGalleryProps = {}) {
  const {
    history,
    enqueueRequest,
    useResultAsReference: resultAsReference,
    historyHasMore,
    isHistoryLoading,
    historyError,
  } = usePlaygroundStore();
  const t = useTranslations('playground');
  const [activeFilter, setActiveFilter] = useState<FilterType>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'gallery'>('grid');
  const [detailGen, setDetailGen] = useState<PlaygroundGeneration | null>(null);
  const [detailOutputId, setDetailOutputId] = useState<string | undefined>(undefined);
  const [actionError, setActionError] = useState<string | null>(null);
  const retrying = useRef(new Map<string, symbol>());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => subscribeRuntimeEnvironment(() => {
    retrying.current.clear();
    setDetailGen(null);
    setDetailOutputId(undefined);
    setActionError(null);
  }), []);

  const handleOpenDetail = useCallback((gen: PlaygroundGeneration, outputId?: string) => {
    setActionError(null);
    setDetailGen(gen);
    setDetailOutputId(outputId);
  }, []);

  const handleRetry = useCallback(async (gen: PlaygroundGeneration) => {
    if (retrying.current.has(gen.id)) return;
    const environment = getRuntimeEnvironment();
    if (!environment) return;
    const draft = { modelId: gen.model_id, mode: gen.mode, inputMedia: gen.input_media, parameters: gen.parameters, negativePrompt: gen.negative_prompt ?? '', prompt: gen.prompt };
    if (wan27ImageRetryNeedsEditing(draft) || qwen20ImageRetryNeedsEditing(draft)) {
      // Retry is a new paid request. Preserve the original receipt and edit a copy.
      usePlaygroundStore.setState({ ...draft, negativePromptProvided: typeof gen.negative_prompt === 'string', parameters: { ...gen.parameters }, inputMedia: [...gen.input_media], batchSize: gen.batch_size });
      setDetailGen(null);
      setActionError(t(gen.input_media.length > 0 ? 'imageContract.retryReferenceEdit' : 'imageContract.retryEdit'));
      return;
    }
    const wanImage = isWan27TextToImage(gen.model_id, gen.mode, gen.input_media);
    const strictImage = wanImage || isQwen20TextToImage(gen.model_id, gen.mode, gen.input_media);
    const retryToken = Symbol('retry');
    retrying.current.set(gen.id, retryToken);
    setActionError(null);
    try {
      if (isSeedanceModel(gen.model_id)) {
        await requireSeedanceCapability({ modelId: gen.model_id, mode: gen.mode, inputMedia: gen.input_media }, environment, playgroundApi.getCapabilities);
      }
      if (!mounted.current || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      const journal = beginPlaygroundGenerationRequest({
        environment,
        mode: gen.mode,
        modelId: gen.model_id,
        prompt: gen.prompt,
        negativePrompt: !wanImage && (!isSeedanceModel(gen.model_id) || supportsPlaygroundNegativePrompt(gen.model_id)) ? gen.negative_prompt ?? undefined : undefined,
        inputMedia: gen.input_media,
        parameters: strictImage ? buildWan27ImageRequestParameters(gen.parameters) : isSeedanceModel(gen.model_id) ? buildPlaygroundRequestParameters(gen.model_id, gen.parameters) : gen.parameters,
        batchSize: gen.batch_size,
      });
      enqueueRequest({
        clientRequestId: journal.clientRequestId,
        requestFingerprint: journal.requestFingerprint,
        body: journal.request,
        journal,
      });
    } catch (err) {
      if (!mounted.current || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      console.error('[Playground] Retry failed:', err);
      setActionError(t(err instanceof PlaygroundCapabilityError ? `capabilities.${err.reason}` : 'results.retryFailed'));
    } finally {
      if (retrying.current.get(gen.id) === retryToken) retrying.current.delete(gen.id);
    }
  }, [enqueueRequest, t]);

  const handleDelete = useCallback(async (gen: PlaygroundGeneration) => {
    setActionError(null);
    const environment = getRuntimeEnvironment();
    try {
      await playgroundApi.deleteGeneration(gen.id);
      if (environment && !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      usePlaygroundStore.getState().removeGeneration(gen.id);
      if (detailGen?.id === gen.id) {
        setDetailGen(null);
        setDetailOutputId(undefined);
      }
    } catch (err) {
      if (environment && !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      console.error('[Playground] Delete failed:', err);
      setActionError(t('results.deleteFailed'));
    }
  }, [detailGen?.id, t]);

  // Image result → "Generate video": set the image as i2v reference and switch mode.
  const handleGenerateVideo = useCallback(
    (mediaPath: string) => resultAsReference(mediaPath, 'image', 'i2v'),
    [resultAsReference],
  );

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
            label: formatSessionLabel(
              sorted[i].created_at,
              t('results.today'),
              t('results.yesterday'),
            ),
            key: `divider-${sorted[i].id}`,
          });
        }
      }
      result.push({ type: 'generation', data: sorted[i] });
    }

    return result;
  }, [sorted, t]);

  // Flat list of generation data items (no dividers) for GalleryView and DetailPanel
  const dataItems = useMemo(
    () =>
      itemsWithDividers
        .filter((item): item is { type: 'generation'; data: PlaygroundGeneration } => item.type === 'generation')
        .map((item) => item.data),
    [itemsWithDividers],
  );

  // Grid items: expand each completed generation into one tile per output (so
  // multi-output batches show all N); keep pending/processing/failed as one card.
  const gridItems = useMemo(() => {
    const out: Array<
      | { kind: 'divider'; label: string; key: string }
      | { kind: 'output'; gen: PlaygroundGeneration; outputIndex: number }
      | { kind: 'gen'; gen: PlaygroundGeneration }
    > = [];
    for (const item of itemsWithDividers) {
      if (item.type === 'divider') {
        out.push({ kind: 'divider', label: item.label, key: item.key });
        continue;
      }
      const g = item.data;
      if (g.status === 'completed' && g.outputs.length > 0) {
        g.outputs.forEach((_, i) => out.push({ kind: 'output', gen: g, outputIndex: i }));
      } else {
        out.push({ kind: 'gen', gen: g });
      }
    }
    return out;
  }, [itemsWithDividers]);

  const filters: { key: FilterType; label: string }[] = [
    { key: 'all', label: t('results.filterAll') },
    { key: 'image', label: t('results.filterImage') },
    { key: 'video', label: t('results.filterVideo') },
  ];

  if (history.length === 0) {
    return (
      <div className="relative flex flex-col flex-1 overflow-hidden min-w-0 items-center justify-center px-7 text-center">
        <div className="absolute right-7 top-4 text-left"><QueuePanel /></div>
        <Sparkles className="w-12 h-12 text-text-muted opacity-40 mb-4" />
        <p className="font-display atelier-display text-base text-foreground mb-1">
          {t('results.emptyTitle')}
        </p>
        <p className="text-xs text-text-muted">{t('results.emptyBody')}</p>
        {(actionError || historyError) && (
          <div className="mt-4 rounded-lg border border-status-failed-border bg-status-failed-bg px-3 py-2 text-xs text-status-failed-fg">
            {actionError ?? t('results.historyFailed')}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden min-w-0">
      {/* Header */}
      <div className="px-7 py-4 flex items-center justify-between border-b border-border-subtle shrink-0">
        <div className="flex flex-col gap-1">
          <span className="font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-text-muted">
            RESULTS
          </span>
          <div className="flex items-center gap-2">
            <span className="text-[2.125rem] leading-[1.1] font-semibold tracking-[-0.02em] text-foreground font-display atelier-display">
              {t('results.title')}
            </span>
            <span className="font-mono text-[0.625rem] bg-elevated text-text-secondary rounded px-[6px] py-[1px]">
              {filtered.reduce((n, g) => n + g.outputs.length, 0)}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-[2px] bg-surface-inset rounded-full p-1 atelier-pill-tabs">
            {filters.map((f) => (
              <button
                key={f.key}
                onClick={() => setActiveFilter(f.key)}
                className={`rounded-full px-4 py-2 text-[0.8125rem] font-medium text-center transition-all cursor-pointer ${
                  activeFilter === f.key
                    ? 'bg-surface text-foreground atelier-pill-tab-active'
                    : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-[2px] bg-surface-inset rounded-full p-1 atelier-pill-tabs">
            <button
              onClick={() => setViewMode('grid')}
              className={`rounded-full p-2 transition-all cursor-pointer ${
                viewMode === 'grid'
                  ? 'bg-surface text-foreground atelier-pill-tab-active'
                  : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
              }`}
              title={t('results.gridView')}
            >
              <Grid3x3 className="w-4 h-4" />
            </button>
            <button
              onClick={() => setViewMode('gallery')}
              className={`rounded-full p-2 transition-all cursor-pointer ${
                viewMode === 'gallery'
                  ? 'bg-surface text-foreground atelier-pill-tab-active'
                  : 'text-text-muted hover:text-foreground hover:bg-hover-bg'
              }`}
              title={t('results.galleryView')}
            >
              <GalleryHorizontal className="w-4 h-4" />
            </button>
          </div>

          <QueuePanel />
        </div>
      </div>

      {(actionError || historyError) && (
        <div className="mx-7 mt-3 rounded-lg border border-status-failed-border bg-status-failed-bg px-3 py-2 text-xs text-status-failed-fg">
          {actionError ?? t('results.historyFailed')}
        </div>
      )}

      {/* Content area */}
      {viewMode === 'gallery' ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <GalleryView
            generations={dataItems}
            onOpenDetail={handleOpenDetail}
            onRetry={handleRetry}
          />
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto p-6">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4 content-start">
            {gridItems.map((it) => {
              if (it.kind === 'divider') {
                return (
                  <div
                    key={it.key}
                    className="col-span-full flex items-center gap-3 py-2"
                  >
                    <div className="flex-1 h-px bg-border-subtle" />
                    <span className="font-mono text-[0.5625rem] text-text-muted uppercase tracking-wider whitespace-nowrap">
                      {it.label}
                    </span>
                    <div className="flex-1 h-px bg-border-subtle" />
                  </div>
                );
              }
              if (it.kind === 'output') {
                return (
                  <ResultCard
                    key={`${it.gen.id}-${it.outputIndex}`}
                    generation={it.gen}
                    outputIndex={it.outputIndex}
                    onRetry={handleRetry}
                    onDelete={handleDelete}
                    onGenerateVideo={handleGenerateVideo}
                    onOpenDetail={handleOpenDetail}
                  />
                );
              }
              return (
                <ResultCard
                  key={it.gen.id}
                  generation={it.gen}
                  onRetry={handleRetry}
                  onDelete={handleDelete}
                  onGenerateVideo={handleGenerateVideo}
                  onOpenDetail={handleOpenDetail}
                />
              );
            })}
          </div>
          {(historyHasMore || isHistoryLoading) && (
            <div className="flex justify-center py-5">
              <button
                type="button"
                onClick={onLoadMore}
                disabled={isHistoryLoading || !onLoadMore}
                className="rounded-full border border-glass-border bg-glass px-4 py-2 text-xs font-medium text-text-secondary transition hover:text-foreground disabled:cursor-wait disabled:opacity-60"
              >
                {isHistoryLoading ? t('results.loadingMore') : t('results.loadMore')}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Detail Panel */}
      {detailGen && (
        <DetailPanel
          generation={detailGen}
          allGenerations={dataItems}
          focusOutputId={detailOutputId}
          onClose={() => { setDetailGen(null); setDetailOutputId(undefined); }}
          onNavigate={(g) => handleOpenDetail(g)}
          onRetry={handleRetry}
          onGenerateVideo={handleGenerateVideo}
        />
      )}
    </div>
  );
}
