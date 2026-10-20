'use client';

import { motion } from 'framer-motion';
import { Clapperboard, Users, Clock, FileText, ArrowRight, Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useEditorStore } from '@/store/editorStore';

export interface PipelinePanelProps {
  projectId?: string;
  onEnterPipeline?: () => void;
}

function StatCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: string | number }) {
  return (
    <div className="flex items-center gap-2.5 rounded-lg bg-zinc-800/60 border border-white/5 px-3 py-2.5">
      <div className="flex h-7 w-7 items-center justify-center rounded bg-zinc-700/50">
        {icon}
      </div>
      <div>
        <p className="text-xs text-text-muted">{label}</p>
        <p className="text-sm font-medium text-foreground">{value}</p>
      </div>
    </div>
  );
}

function formatDuration(seconds: number, t: (key: string, values?: Record<string, any>) => string): string {
  if (seconds <= 0) return '—';
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (mins === 0) return t('panels.durationSeconds', { count: secs });
  if (secs > 0) return t('panels.durationMinSec', { min: mins, sec: secs });
  return t('panels.durationMin', { min: mins });
}

export default function PipelinePanel({ projectId, onEnterPipeline }: PipelinePanelProps) {
  const t = useTranslations('scriptEditor');
  const derivedScenes = useEditorStore((s) => s.derivedScenes);
  const derivedCharacters = useEditorStore((s) => s.derivedCharacters);
  const estimatedDuration = useEditorStore((s) => s.estimatedDuration);
  const wordCount = useEditorStore((s) => s.wordCount);
  const editorMode = useEditorStore((s) => s.editorMode);

  const isEmbedded = editorMode === 'embedded';

  return (
    <div className="p-3 space-y-4">
      {/* Overview stats */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <Clapperboard size={14} className="text-text-muted" />
          <span className="text-xs font-medium text-text-muted uppercase tracking-wider">
            {t('panels.statsOverview')}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <StatCard
            icon={<Clapperboard size={13} className="text-blue-400" />}
            label={t('panels.statScenes')}
            value={derivedScenes.length}
          />
          <StatCard
            icon={<Users size={13} className="text-purple-400" />}
            label={t('panels.statCharacters')}
            value={derivedCharacters.length}
          />
          <StatCard
            icon={<Clock size={13} className="text-amber-400" />}
            label={t('panels.statDuration')}
            value={formatDuration(estimatedDuration, t)}
          />
          <StatCard
            icon={<FileText size={13} className="text-green-400" />}
            label={t('panels.statWordCount')}
            value={wordCount.toLocaleString()}
          />
        </div>
      </div>

      {/* Enter pipeline CTA - hidden in embedded mode */}
      {!isEmbedded && (
        <motion.button
          type="button"
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={onEnterPipeline}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 via-purple-600 to-pink-600 px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-purple-900/30 hover:shadow-purple-900/50 transition-shadow"
        >
          {t('panels.enterPipeline')}
          <ArrowRight size={16} />
        </motion.button>
      )}

      {/* Progress dashboard placeholder */}
      <div className="rounded-lg border border-white/5 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 mb-2">
          <Loader2 size={14} className="text-text-muted" />
          <span className="text-xs font-medium text-text-muted">{t('panels.generationProgress')}</span>
        </div>
        <div className="flex items-center justify-center py-4">
          <p className="text-xs text-text-muted/60 italic">{t('panels.noActiveTasks')}</p>
        </div>
      </div>

      {/* Recent history placeholder */}
      <div className="rounded-lg border border-white/5 bg-zinc-900/50 p-4">
        <div className="flex items-center gap-2 mb-2">
          <Clock size={14} className="text-text-muted" />
          <span className="text-xs font-medium text-text-muted">{t('panels.recentGeneration')}</span>
        </div>
        <div className="flex items-center justify-center py-4">
          <p className="text-xs text-text-muted/60 italic">{t('panels.noHistory')}</p>
        </div>
      </div>
    </div>
  );
}
