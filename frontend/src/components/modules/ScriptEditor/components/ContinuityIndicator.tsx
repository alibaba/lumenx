'use client';

import { useState } from 'react';
import { AlertTriangle, CheckCircle, ChevronDown, ChevronUp, MapPin, User } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { ContinuityReport, ContinuityWarning } from '../hooks/useContinuityCheck';

interface ContinuityIndicatorProps {
  report: ContinuityReport;
}

function WarningIcon({ type }: { type: ContinuityWarning['type'] }) {
  switch (type) {
    case 'character_disappeared':
      return <User size={12} className="text-amber-400" />;
    case 'location_reuse':
      return <MapPin size={12} className="text-blue-400" />;
    case 'character_stats':
      return <User size={12} className="text-text-muted" />;
    default:
      return <AlertTriangle size={12} className="text-amber-400" />;
  }
}

/**
 * 连贯性指示器组件
 * - 显示连贯性警告计数徽章
 * - 点击展开警告列表
 * - 每条警告：图标 + 消息 + 点击跳转到相关场景
 * - 警告为空时显示绿色 ✓ "故事连贯"
 */
export function ContinuityIndicator({ report }: ContinuityIndicatorProps) {
  const [expanded, setExpanded] = useState(false);
  const t = useTranslations('scriptEditor');
  const { warnings, characterStats, locationStats } = report;

  const hasWarnings = warnings.length > 0;

  return (
    <div className="relative">
      {/* Badge / Indicator Button */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs transition-colors ${
          hasWarnings
            ? 'text-amber-400 hover:bg-amber-400/10'
            : 'text-emerald-400 hover:bg-emerald-400/10'
        }`}
        aria-label={hasWarnings ? t('continuity.warningsCount', { count: warnings.length }) : t('continuity.allGood')}
      >
        {hasWarnings ? (
          <>
            <AlertTriangle size={12} />
            <span>{t('continuity.warningsBadge', { count: warnings.length })}</span>
          </>
        ) : (
          <>
            <CheckCircle size={12} />
            <span>{t('continuity.allGood')}</span>
          </>
        )}
        {expanded ? <ChevronDown size={10} /> : <ChevronUp size={10} />}
      </button>

      {/* Expanded Panel */}
      {expanded && (
        <div className="absolute bottom-full left-0 mb-2 w-[360px] rounded-lg border border-white/10 bg-[#0c0c12] shadow-xl z-40 max-h-[320px] overflow-y-auto">
          {/* Stats Summary */}
          <div className="border-b border-white/10 px-4 py-3">
            <div className="flex items-center gap-4 text-xs text-text-muted">
              <span className="flex items-center gap-1">
                <User size={11} />
                {t('continuity.characterCount', { count: characterStats.length })}
              </span>
              <span className="flex items-center gap-1">
                <MapPin size={11} />
                {t('continuity.locationCount', { count: locationStats.length })}
              </span>
            </div>
          </div>

          {/* Warnings List */}
          {hasWarnings ? (
            <div className="divide-y divide-white/5">
              {warnings.map((warning, idx) => (
                <div
                  key={`${warning.relatedEntity}-${warning.sceneIndex}-${idx}`}
                  className="flex items-start gap-2 px-4 py-2.5 hover:bg-white/[0.03] transition-colors cursor-pointer"
                  title={t('continuity.jumpToScene', { index: warning.sceneIndex })}
                >
                  <div className="mt-0.5 shrink-0">
                    <WarningIcon type={warning.type} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-text-secondary leading-relaxed">
                      {warning.message}
                    </p>
                    <p className="text-[10px] text-text-muted mt-0.5">
                      {t('continuity.sceneLabel', { index: warning.sceneIndex })} · {warning.severity === 'warning' ? t('continuity.severityWarning') : t('continuity.severityHint')}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="px-4 py-6 text-center">
              <CheckCircle size={20} className="mx-auto text-emerald-400 mb-2" />
              <p className="text-xs text-text-muted">{t('continuity.allConsistent')}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
