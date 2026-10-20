'use client';

import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { useTranslations } from 'next-intl';

interface ShortcutHelpPanelProps {
  open: boolean;
  onClose: () => void;
}

interface ShortcutItem {
  keys: string;
  descKey: string;
}

interface ShortcutGroup {
  titleKey: string;
  items: ShortcutItem[];
}

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    titleKey: 'shortcuts.groupEdit',
    items: [
      { keys: 'Tab', descKey: 'shortcuts.cycleNodeType' },
      { keys: 'Shift+Tab', descKey: 'shortcuts.reverseNodeType' },
      { keys: 'Enter', descKey: 'shortcuts.createNextNode' },
      { keys: '⌘+Enter', descKey: 'shortcuts.newScene' },
      { keys: '⌘+D', descKey: 'shortcuts.dualDialogue' },
      { keys: '@', descKey: 'shortcuts.mentionCharacter' },
    ],
  },
  {
    titleKey: 'shortcuts.groupNavigation',
    items: [
      { keys: '⌘+Shift+F', descKey: 'shortcuts.search' },
      { keys: '⌘+Shift+E', descKey: 'shortcuts.toggleFolding' },
      { keys: 'Escape', descKey: 'shortcuts.exitFocus' },
    ],
  },
  {
    titleKey: 'shortcuts.groupFormat',
    items: [
      { keys: '⌘+Z', descKey: 'shortcuts.undo' },
      { keys: '⌘+Shift+Z', descKey: 'shortcuts.redo' },
    ],
  },
  {
    titleKey: 'shortcuts.groupView',
    items: [
      { keys: '⌘+S', descKey: 'shortcuts.save' },
      { keys: '⌘+/', descKey: 'shortcuts.showHelp' },
    ],
  },
];

/**
 * 快捷键帮助面板组件
 * - 模态面板，显示所有可用快捷键
 * - 分组显示：编辑 | 导航 | 格式 | 视图
 * - 暗色主题，支持 Escape 关闭
 */
export function ShortcutHelpPanel({ open, onClose }: ShortcutHelpPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const t = useTranslations('scriptEditor');

  // Escape 关闭
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', handleKey, true);
    return () => document.removeEventListener('keydown', handleKey, true);
  }, [open, onClose]);

  // 点击外部关闭
  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div
        ref={panelRef}
        className="relative w-[520px] max-h-[80vh] overflow-y-auto rounded-xl border border-white/10 bg-[#0c0c12] shadow-2xl"
      >
        {/* Header */}
        <div className="sticky top-0 flex items-center justify-between border-b border-white/10 bg-[#0c0c12] px-6 py-4">
          <h2 className="text-base font-semibold text-foreground">{t('shortcuts.title')}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-text-muted hover:text-foreground hover:bg-white/5 transition-colors"
            aria-label={t('shortcuts.close')}
          >
            <X size={16} />
          </button>
        </div>

        {/* Shortcut Groups */}
        <div className="px-6 py-4 space-y-6">
          {SHORTCUT_GROUPS.map((group) => (
            <div key={group.titleKey}>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">
                {t(group.titleKey)}
              </h3>
              <div className="space-y-1.5">
                {group.items.map((item) => (
                  <div
                    key={item.keys}
                    className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-white/[0.03] transition-colors"
                  >
                    <span className="text-sm text-text-secondary">{t(item.descKey)}</span>
                    <kbd className="inline-flex items-center gap-0.5 rounded border border-white/10 bg-white/5 px-2 py-0.5 text-xs font-mono text-text-muted">
                      {item.keys}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="border-t border-white/10 px-6 py-3">
          <p className="text-xs text-text-muted/60 text-center">
            {t('shortcuts.footer')}
          </p>
        </div>
      </div>
    </div>
  );
}
