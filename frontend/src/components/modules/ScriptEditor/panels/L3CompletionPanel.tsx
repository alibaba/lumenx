'use client';

import { useMemo, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Check, X, RefreshCw, Loader2 } from 'lucide-react';
import { useEditorStore, type L3Result } from '@/store/editorStore';

type ResultGroup = {
  type: L3Result['type'];
  label: string;
  items: L3Result[];
};

function ConfidenceBadge({ value, label }: { value: number; label: string }) {
  const color =
    value > 0.8
      ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
      : value > 0.6
        ? 'bg-amber-500/20 text-amber-400 border-amber-500/30'
        : 'bg-red-500/20 text-red-400 border-red-500/30';

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${color}`}
    >
      {label} {Math.round(value * 100)}%
    </span>
  );
}

function ResultCard({
  item,
  onApply,
  onReject,
  confidenceLabel,
  applyLabel,
  rejectLabel,
  sceneLabel,
}: {
  item: L3Result;
  onApply: () => void;
  onReject: () => void;
  confidenceLabel: string;
  applyLabel: string;
  rejectLabel: string;
  sceneLabel: string;
}) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      className="group rounded-lg border border-white/10 bg-zinc-800/80 p-3 hover:border-white/20 hover:bg-zinc-800 transition-colors"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{item.name}</p>
          {item.description && (
            <p className="text-xs text-text-muted mt-0.5 line-clamp-2">{item.description}</p>
          )}
          <div className="flex items-center gap-2 mt-1.5 flex-wrap">
            <ConfidenceBadge value={item.confidence} label={confidenceLabel} />
            {item.sceneIndex !== undefined && (
              <span className="text-[10px] text-text-muted bg-zinc-700/50 rounded px-1.5 py-0.5">
                {sceneLabel} {item.sceneIndex + 1}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
          <button
            type="button"
            onClick={onApply}
            title={applyLabel}
            className="flex h-6 w-6 items-center justify-center rounded bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 transition-colors"
          >
            <Check size={12} />
          </button>
          <button
            type="button"
            onClick={onReject}
            title={rejectLabel}
            className="flex h-6 w-6 items-center justify-center rounded bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors"
          >
            <X size={12} />
          </button>
        </div>
      </div>
    </motion.div>
  );
}

export default function L3CompletionPanel() {
  const t = useTranslations('scriptEditor');
  const l3Status = useEditorStore((s) => s.l3Status);
  const l3Results = useEditorStore((s) => s.l3Results);
  const setL3Results = useEditorStore((s) => s.setL3Results);
  const setL3Status = useEditorStore((s) => s.setL3Status);
  const setL3LastFetchTime = useEditorStore((s) => s.setL3LastFetchTime);

  const groupedResults: ResultGroup[] = useMemo(() => {
    if (!l3Results || l3Results.length === 0) return [];

    const groupMap: Record<L3Result['type'], L3Result[]> = {
      character: [],
      prop: [],
      beat: [],
      location: [],
    };

    for (const item of l3Results) {
      groupMap[item.type].push(item);
    }

    const typeLabels: Record<L3Result['type'], string> = {
      character: t('panels.characters'),
      prop: t('panels.props'),
      beat: t('panels.shots'),
      location: t('panels.locations'),
    };

    return Object.entries(groupMap)
      .filter(([, items]) => items.length > 0)
      .map(([type, items]) => ({
        type: type as L3Result['type'],
        label: typeLabels[type as L3Result['type']],
        items,
      }));
  }, [l3Results, t]);

  const handleApply = useCallback(
    (item: L3Result) => {
      // Placeholder: apply entity to confirmed list
      console.log('[L3] Apply entity:', item);
    },
    []
  );

  const handleReject = useCallback(
    (item: L3Result) => {
      if (!l3Results) return;
      const updated = l3Results.filter(
        (r) => !(r.type === item.type && r.name === item.name)
      );
      setL3Results(updated.length > 0 ? updated : null);
    },
    [l3Results, setL3Results]
  );

  const handleRetry = useCallback(() => {
    // Reset status to trigger re-fetch
    setL3Status('idle');
    setL3LastFetchTime(null);
  }, [setL3Status, setL3LastFetchTime]);

  // Status: idle
  if (l3Status === 'idle' && (!l3Results || l3Results.length === 0)) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-800 mb-3">
          <Sparkles size={20} className="text-zinc-500" />
        </div>
        <p className="text-sm text-text-muted">{t('panels.aiIdle')}</p>
      </div>
    );
  }

  // Status: loading
  if (l3Status === 'loading') {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-800 mb-3">
          <Loader2 size={20} className="text-indigo-400 animate-spin" />
        </div>
        <p className="text-sm text-text-muted">{t('panels.aiLoading')}</p>
      </div>
    );
  }

  // Status: error
  if (l3Status === 'error') {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-900/30 mb-3">
          <X size={20} className="text-red-400" />
        </div>
        <p className="text-sm text-text-muted">{t('panels.aiError')}</p>
        <button
          type="button"
          onClick={handleRetry}
          className="mt-3 flex items-center gap-1.5 rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-medium text-text-secondary hover:bg-zinc-700 transition-colors"
        >
          <RefreshCw size={12} />
          {t('panels.aiRetry')}
        </button>
      </div>
    );
  }

  // Status: success but empty
  if (l3Status === 'success' && (!l3Results || l3Results.length === 0)) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-800 mb-3">
          <Check size={20} className="text-zinc-500" />
        </div>
        <p className="text-sm text-text-muted">{t('panels.aiEmpty')}</p>
      </div>
    );
  }

  // Results view
  return (
    <div className="p-3">
      <div className="flex items-center gap-2 mb-3">
        <Sparkles size={14} className="text-indigo-400" />
        <span className="text-xs font-medium text-text-muted uppercase tracking-wider">
          {t('panels.aiCompletion')}
        </span>
      </div>

      <div className="space-y-4">
        {groupedResults.map((group) => (
          <div key={group.type}>
            <p className="text-xs font-medium text-text-muted mb-2">
              {group.label} ({group.items.length})
            </p>
            <div className="space-y-2">
              <AnimatePresence mode="popLayout">
                {group.items.map((item) => (
                  <ResultCard
                    key={`${item.type}-${item.name}`}
                    item={item}
                    onApply={() => handleApply(item)}
                    onReject={() => handleReject(item)}
                    confidenceLabel={t('panels.aiConfidence')}
                    applyLabel={t('panels.aiApply')}
                    rejectLabel={t('panels.aiReject')}
                    sceneLabel={t('panels.sceneLabel', { number: '' }).trim()}
                  />
                ))}
              </AnimatePresence>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
