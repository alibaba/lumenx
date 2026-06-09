'use client';

import { useState } from 'react';
import { Copy, Clock } from 'lucide-react';
import { usePlaygroundStore } from './usePlaygroundStore';
import PromptTemplateModal from './PromptTemplateModal';
import PromptHistoryDrawer from './PromptHistoryDrawer';

const MAX_LENGTH = 2000;

export default function PromptInput() {
  const prompt = usePlaygroundStore((s) => s.prompt);
  const negativePrompt = usePlaygroundStore((s) => s.negativePrompt);
  const setPrompt = usePlaygroundStore((s) => s.setPrompt);
  const setNegativePrompt = usePlaygroundStore((s) => s.setNegativePrompt);
  const setShowTemplateModal = usePlaygroundStore((s) => s.setShowTemplateModal);
  const setShowHistoryDrawer = usePlaygroundStore((s) => s.setShowHistoryDrawer);

  const [showNegPrompt, setShowNegPrompt] = useState(false);

  return (
    <div>
      {/* Main prompt textarea */}
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value.slice(0, MAX_LENGTH))}
        placeholder="描述你想生成的内容..."
        className="w-full min-h-[120px] max-h-[280px] resize-y p-[14px] border border-glass-border rounded-xl bg-input-bg text-foreground text-[13px] leading-relaxed placeholder:text-text-muted focus:border-primary/60 focus:ring-[3px] focus:ring-primary/15 outline-none"
      />

      {/* Toolbar — below the textarea, not overlapping */}
      <div className="flex items-center gap-[6px] mt-1.5 px-1">
        <button
          type="button"
          onClick={() => setShowTemplateModal(true)}
          className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors"
        >
          <Copy size={12} />
          模板
        </button>
        <button
          type="button"
          onClick={() => setShowHistoryDrawer(true)}
          className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors"
        >
          <Clock size={12} />
          历史
        </button>
        <span className="ml-auto font-mono text-[10px] text-text-muted">
          {prompt.length} / {MAX_LENGTH}
        </span>
      </div>

      {/* Negative prompt toggle */}
      <div
        className="flex items-center gap-[6px] py-[6px] text-[11px] text-text-secondary cursor-pointer hover:text-foreground mt-2"
        onClick={() => setShowNegPrompt((v) => !v)}
      >
        <span
          className="inline-block transition-transform duration-150"
          style={{ transform: showNegPrompt ? 'rotate(90deg)' : 'rotate(0deg)' }}
        >
          &#9656;
        </span>
        <span>负面提示词</span>
      </div>

      {showNegPrompt && (
        <textarea
          value={negativePrompt}
          onChange={(e) => setNegativePrompt(e.target.value)}
          placeholder="不希望出现的内容..."
          className="w-full min-h-[60px] resize-y p-[10px] border border-glass-border rounded-lg bg-input-bg text-text-secondary text-xs placeholder:text-text-muted focus:border-primary/60 focus:ring-[3px] focus:ring-primary/15 outline-none"
        />
      )}

      <PromptTemplateModal />
      <PromptHistoryDrawer />
    </div>
  );
}
