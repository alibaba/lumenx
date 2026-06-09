'use client';

import { useMemo, useState, useCallback, useEffect, useRef } from 'react';
import { X, Copy, BookmarkPlus, Search } from 'lucide-react';
import { usePlaygroundStore } from './usePlaygroundStore';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MODE_LABELS: Record<string, string> = {
  t2i: 'T2I',
  i2i: 'I2I',
  t2v: 'T2V',
  i2v: 'I2V',
  r2v: 'R2V',
  v2v: 'V2V',
};

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return '刚刚';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  const months = Math.floor(days / 30);
  return `${months} 个月前`;
}

// ---------------------------------------------------------------------------
// Deduplicated history entry
// ---------------------------------------------------------------------------

interface HistoryEntry {
  prompt: string;
  mode: string;
  model_id: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function PromptHistoryDrawer() {
  const showHistoryDrawer = usePlaygroundStore((s) => s.showHistoryDrawer);
  const setShowHistoryDrawer = usePlaygroundStore((s) => s.setShowHistoryDrawer);
  const history = usePlaygroundStore((s) => s.history);
  const setPrompt = usePlaygroundStore((s) => s.setPrompt);
  const setShowTemplateModal = usePlaygroundStore((s) => s.setShowTemplateModal);

  const [search, setSearch] = useState('');
  const [visible, setVisible] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);

  // Animate in after mount
  useEffect(() => {
    if (showHistoryDrawer) {
      // Allow a frame for the DOM to render before sliding in
      const raf = requestAnimationFrame(() => setVisible(true));
      return () => cancelAnimationFrame(raf);
    }
    setVisible(false);
  }, [showHistoryDrawer]);

  // Close with animation
  const handleClose = useCallback(() => {
    setVisible(false);
    // Wait for transition to finish before unmounting
    setTimeout(() => setShowHistoryDrawer(false), 250);
  }, [setShowHistoryDrawer]);

  // Deduplicate by prompt text, keep the most recent occurrence (history is newest-first)
  const deduplicated = useMemo<HistoryEntry[]>(() => {
    const seen = new Set<string>();
    const results: HistoryEntry[] = [];
    for (const gen of history) {
      const key = gen.prompt.trim();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      results.push({
        prompt: gen.prompt,
        mode: gen.mode,
        model_id: gen.model_id,
        created_at: gen.created_at,
      });
    }
    return results;
  }, [history]);

  // Filter by search query
  const filtered = useMemo(() => {
    if (!search.trim()) return deduplicated;
    const q = search.trim().toLowerCase();
    return deduplicated.filter((e) => e.prompt.toLowerCase().includes(q));
  }, [deduplicated, search]);

  const handleCopy = useCallback(
    (prompt: string) => {
      setPrompt(prompt);
      handleClose();
    },
    [setPrompt, handleClose],
  );

  const handleSaveAsTemplate = useCallback(
    (prompt: string) => {
      setPrompt(prompt);
      setShowTemplateModal(true);
      handleClose();
    },
    [setPrompt, setShowTemplateModal, handleClose],
  );

  if (!showHistoryDrawer) return null;

  return (
    // Overlay
    <div
      className="fixed inset-0 z-50 bg-black/60 transition-opacity duration-250"
      style={{ opacity: visible ? 1 : 0 }}
      onClick={handleClose}
    >
      {/* Drawer */}
      <div
        ref={drawerRef}
        className="fixed right-0 top-0 h-full w-[400px] bg-elevated border-l border-glass-border shadow-2xl flex flex-col transition-transform duration-250 ease-out"
        style={{ transform: visible ? 'translateX(0)' : 'translateX(100%)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Header ─────────────────────────────────────────────────── */}
        <div className="px-5 py-4 border-b border-glass-border flex items-center justify-between shrink-0">
          <h2 className="text-sm font-medium text-foreground">Prompt 历史</h2>
          <button
            type="button"
            onClick={handleClose}
            className="w-7 h-7 rounded-md flex items-center justify-center text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* ── Search ─────────────────────────────────────────────────── */}
        <div className="px-5 py-3 shrink-0">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted pointer-events-none" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索历史 prompt..."
              className="w-full bg-input-bg border border-glass-border rounded-lg pl-9 pr-3 py-2 text-xs text-foreground placeholder:text-text-muted outline-none focus:border-primary/40 transition-colors"
            />
          </div>
        </div>

        {/* ── List ───────────────────────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto px-5 py-2">
          {filtered.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-xs text-text-muted">
                {search.trim() ? '无匹配结果' : '暂无生成历史'}
              </p>
            </div>
          ) : (
            filtered.map((entry, idx) => (
              <div
                key={`${entry.created_at}-${idx}`}
                className={[
                  'py-3',
                  idx < filtered.length - 1
                    ? 'border-b border-glass-border'
                    : '',
                ].join(' ')}
              >
                {/* Prompt text */}
                <p className="text-[12px] text-foreground leading-relaxed line-clamp-3 mb-2">
                  {entry.prompt}
                </p>

                {/* Meta row */}
                <div className="flex items-center gap-1.5 mb-2">
                  <span className="font-mono text-[9px] bg-primary/15 text-primary rounded px-[6px] py-[2px] uppercase">
                    {MODE_LABELS[entry.mode] || entry.mode}
                  </span>
                  {entry.model_id && (
                    <span className="font-mono text-[9px] bg-glass text-text-secondary rounded px-[6px] py-[2px]">
                      {entry.model_id}
                    </span>
                  )}
                  <span className="font-mono text-[9px] text-text-muted ml-auto">
                    {relativeTime(entry.created_at)}
                  </span>
                </div>

                {/* Actions row */}
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleCopy(entry.prompt)}
                    className="flex items-center gap-1 text-[11px] text-text-secondary hover:text-foreground transition-colors cursor-pointer"
                  >
                    <Copy className="w-3 h-3" />
                    复制
                  </button>
                  <button
                    type="button"
                    onClick={() => handleSaveAsTemplate(entry.prompt)}
                    className="flex items-center gap-1 text-[11px] text-text-secondary hover:text-foreground transition-colors cursor-pointer"
                  >
                    <BookmarkPlus className="w-3 h-3" />
                    存为模板
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
