'use client';

import { useTranslations } from 'next-intl';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Wand2, X } from 'lucide-react';
import type { PasteAnalysis } from '../hooks/usePasteHandler';

interface PasteHintBarProps {
  visible: boolean;
  analysis: PasteAnalysis | null;
  onApply: () => void;
  onDismiss: () => void;
}

export function PasteHintBar({ visible, analysis, onApply, onDismiss }: PasteHintBarProps) {
  const t = useTranslations('scriptEditor');
  const matchPercent = analysis ? Math.round(analysis.matchRate * 100) : 0;

  return (
    <AnimatePresence>
      {visible && analysis && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.2, ease: 'easeOut' }}
          className="absolute top-2 left-1/2 -translate-x-1/2 z-50 w-auto max-w-[560px]"
        >
          <div className="flex items-center gap-3 rounded-lg border border-blue-500/20 bg-blue-500/10 backdrop-blur-md px-4 py-2.5 shadow-lg shadow-blue-500/5">
            <Sparkles size={16} className="shrink-0 text-blue-400" />
            <span className="text-sm text-blue-200/90 whitespace-nowrap">
              {t('paste.detectedDetail', { percent: matchPercent, lines: analysis.suggestions.length })}
            </span>
            <div className="flex items-center gap-2 ml-2">
              <button
                type="button"
                onClick={onApply}
                className="inline-flex items-center gap-1.5 rounded-md bg-blue-500/20 hover:bg-blue-500/30 border border-blue-400/30 px-3 py-1 text-xs font-medium text-blue-300 transition-colors"
              >
                <Wand2 size={12} />
                {t('paste.format')}
              </button>
              <button
                type="button"
                onClick={onDismiss}
                className="inline-flex items-center justify-center rounded-md hover:bg-white/10 p-1 text-blue-300/60 hover:text-blue-300 transition-colors"
                aria-label={t('paste.dismiss')}
              >
                <X size={14} />
              </button>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export default PasteHintBar;
