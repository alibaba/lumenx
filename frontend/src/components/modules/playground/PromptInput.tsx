'use client';

import { useEffect, useState } from 'react';
import { Copy, Clock } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { usePlaygroundStore } from './usePlaygroundStore';
import PromptTemplateModal from './PromptTemplateModal';
import PromptHistoryDrawer from './PromptHistoryDrawer';
import { isWan27TextToImage, isQwen20TextToImage } from './playgroundImageContract';
import { supportsPlaygroundNegativePrompt } from './playgroundParameterContract';

const MAX_LENGTH = 2000;

export default function PromptInput() {
  const prompt = usePlaygroundStore((s) => s.prompt);
  const negativePrompt = usePlaygroundStore((s) => s.negativePrompt);
  const negativePromptProvided = usePlaygroundStore((s) => s.negativePromptProvided);
  const savedNegativePrompt = usePlaygroundStore((s) => s.parameters.negative_prompt);
  const modelId = usePlaygroundStore((s) => s.modelId);
  const mode = usePlaygroundStore((s) => s.mode);
  const inputMedia = usePlaygroundStore((s) => s.inputMedia);
  const setPrompt = usePlaygroundStore((s) => s.setPrompt);
  const setNegativePrompt = usePlaygroundStore((s) => s.setNegativePrompt);
  const setShowTemplateModal = usePlaygroundStore((s) => s.setShowTemplateModal);
  const setShowHistoryDrawer = usePlaygroundStore((s) => s.setShowHistoryDrawer);
  const t = useTranslations('playground');

  const [showNegPrompt, setShowNegPrompt] = useState(false);
  const strictImage = isWan27TextToImage(modelId, mode, inputMedia);
  const qwenImage = isQwen20TextToImage(modelId, mode, inputMedia);
  const negativePromptSupported = !strictImage && supportsPlaygroundNegativePrompt(modelId);
  const hasInactiveNegativePrompt = negativePrompt.trim().length > 0 && !negativePromptSupported;

  useEffect(() => {
    // Applying a saved value must reveal the editable text, including an explicit empty value.
    if (qwenImage && negativePromptProvided) setShowNegPrompt(true);
  }, [qwenImage, negativePromptProvided, negativePrompt, savedNegativePrompt]);

  return (
    <div>
      {/* Main prompt textarea */}
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value.slice(0, MAX_LENGTH))}
        placeholder={t('prompt.placeholder')}
        className="w-full min-h-[120px] max-h-[280px] resize-y bg-transparent border-0 rounded-none p-0 text-foreground text-[0.9375rem] leading-[1.65] placeholder-text-muted focus:ring-0"
      />

      {/* Toolbar — below the textarea, not overlapping */}
      <div className="flex items-center gap-[6px] border-t border-border-subtle pt-2.5 mt-3">
        <button
          type="button"
          onClick={() => setShowTemplateModal(true)}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[0.6875rem] font-medium text-text-muted hover:text-foreground hover:bg-hover-bg transition-colors"
        >
          <Copy size={12} />
          {t('prompt.templates')}
        </button>
        <button
          type="button"
          onClick={() => setShowHistoryDrawer(true)}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[0.6875rem] font-medium text-text-muted hover:text-foreground hover:bg-hover-bg transition-colors"
        >
          <Clock size={12} />
          {t('prompt.history')}
        </button>
        <span className="ml-auto font-mono text-[0.625rem] text-text-muted">
          {prompt.length} / {MAX_LENGTH}
        </span>
      </div>

      {/* Negative prompt toggle */}
      <div
        className={`flex items-center gap-[6px] py-[6px] text-[0.6875rem] text-text-muted mt-2 ${
          negativePromptSupported || hasInactiveNegativePrompt
            ? 'cursor-pointer hover:text-foreground'
            : 'cursor-not-allowed opacity-60'
        }`}
        onClick={() => {
          if (negativePromptSupported || hasInactiveNegativePrompt) setShowNegPrompt((v) => !v);
        }}
        aria-disabled={!negativePromptSupported}
      >
        <span
          className="inline-block transition-transform duration-150"
          style={{ transform: showNegPrompt ? 'rotate(90deg)' : 'rotate(0deg)' }}
        >
          &#9656;
        </span>
        <span>{t('prompt.negativeLabel')}</span>
        {hasInactiveNegativePrompt && (
          <span className="rounded-full border border-border-subtle px-1.5 py-0.5 text-[0.5625rem] uppercase tracking-[0.08em] text-text-muted">
            {t(strictImage ? 'imageContract.needsCorrection' : 'prompt.inactiveBadge')}
          </span>
        )}
      </div>

      {hasInactiveNegativePrompt && (
        <p className="text-[0.6875rem] leading-relaxed text-text-muted">
          {t(strictImage ? 'imageContract.negativeUnsupported' : 'prompt.negativeUnsupportedHint')}
        </p>
      )}

      {showNegPrompt && (negativePromptSupported || hasInactiveNegativePrompt) && (
        <div className="space-y-2">
          <textarea
            value={negativePrompt}
            onChange={(e) => setNegativePrompt(e.target.value)}
            placeholder={t('prompt.negativePlaceholder')}
            disabled={!negativePromptSupported}
            className="w-full min-h-[60px] resize-y bg-transparent border-0 rounded-none p-0 text-text-secondary text-xs placeholder-text-muted focus:ring-0 disabled:cursor-not-allowed disabled:opacity-70"
          />
          {qwenImage && <p className="text-[0.6875rem] text-text-muted">{t('imageContract.qwenNegativeLimit')}</p>}
        </div>
      )}

      <PromptTemplateModal />
      <PromptHistoryDrawer />
    </div>
  );
}
