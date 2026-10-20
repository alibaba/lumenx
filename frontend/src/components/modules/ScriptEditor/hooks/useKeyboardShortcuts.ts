import { useEffect, useCallback, useState } from 'react';
import { Editor } from '@tiptap/react';
import { useTranslations } from 'next-intl';
import { useEditorStore } from '@/store/editorStore';

/**
 * 全局快捷键注册 Hook
 *
 * 已在其他地方实现的快捷键（不重复注册）：
 * - Tab/Shift+Tab (Keymap extension)
 * - Cmd+S (useAutoSave)
 * - Cmd+Enter (Keymap extension)
 * - Cmd+Z/Cmd+Shift+Z (Tiptap History)
 * - Enter (Keymap extension)
 *
 * 本 Hook 新增注册：
 * - Cmd+Shift+E: 切换场景折叠/展开（Phase 1.4 实现，此处占位 console.log）
 * - Cmd+Shift+F: 聚焦到左侧栏搜索面板（dispatch 自定义事件）
 * - Cmd+/: 显示/隐藏快捷键帮助面板
 * - Cmd+B: 加粗切换
 * - Cmd+I: 斜体切换
 * - Cmd+D: 插入 DualDialogue 结构
 * - Escape: 退出 focus 模式（如果在 focus 模式中）
 * - Cmd+?: 打开快捷键帮助面板（备用）
 */
export function useKeyboardShortcuts(editor: Editor | null) {
  const [showShortcutHelp, setShowShortcutHelp] = useState(false);
  const t = useTranslations('scriptEditor');
  const viewMode = useEditorStore((s) => s.viewMode);
  const setViewMode = useEditorStore((s) => s.setViewMode);

  const toggleShortcutHelp = useCallback(() => {
    setShowShortcutHelp((prev) => !prev);
  }, []);

  const closeShortcutHelp = useCallback(() => {
    setShowShortcutHelp(false);
  }, []);

  useEffect(() => {
    if (!editor) return;

    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;

      // Cmd+Shift+E: 切换场景折叠/展开
      if (mod && e.shiftKey && e.key === 'E') {
        e.preventDefault();
        document.dispatchEvent(new CustomEvent('script-editor:toggle-scene-folding'));
        return;
      }

      // Cmd+Shift+F: 聚焦到左侧栏搜索面板
      if (mod && e.shiftKey && e.key === 'F') {
        e.preventDefault();
        document.dispatchEvent(new CustomEvent('script-editor:focus-search'));
        return;
      }

      // Cmd+/: 显示/隐藏快捷键帮助面板
      if (mod && !e.shiftKey && e.key === '/') {
        e.preventDefault();
        toggleShortcutHelp();
        return;
      }

      // Cmd+B: 加粗切换
      if (mod && !e.shiftKey && e.key === 'b') {
        e.preventDefault();
        editor.chain().focus().toggleBold().run();
        return;
      }

      // Cmd+I: 斜体切换
      if (mod && !e.shiftKey && e.key === 'i') {
        e.preventDefault();
        editor.chain().focus().toggleItalic().run();
        return;
      }

      // Cmd+D: 插入 DualDialogue 结构
      if (mod && !e.shiftKey && e.key === 'd') {
        e.preventDefault();
        // 插入双人对话结构：两个连续的 characterCue + dialogue
        try {
          editor
            .chain()
            .focus()
            .insertContent([
              { type: 'characterCue', content: [{ type: 'text', text: t('components.characterAPlaceholder') }] },
              { type: 'dialogue', content: [{ type: 'text', text: '' }] },
              { type: 'characterCue', content: [{ type: 'text', text: t('components.characterBPlaceholder') }] },
              { type: 'dialogue', content: [{ type: 'text', text: '' }] },
            ])
            .run();
        } catch {
          // 如果节点类型不存在，使用 paragraph fallback
          editor
            .chain()
            .focus()
            .insertContent([
              { type: 'paragraph', content: [{ type: 'text', text: `【${t('components.characterAPlaceholder')}】` }] },
              { type: 'paragraph', content: [{ type: 'text', text: t('components.dialoguePlaceholder') }] },
              { type: 'paragraph', content: [{ type: 'text', text: `【${t('components.characterBPlaceholder')}】` }] },
              { type: 'paragraph', content: [{ type: 'text', text: t('components.dialoguePlaceholder') }] },
            ])
            .run();
        }
        return;
      }

      // Escape: 退出 focus 模式
      if (e.key === 'Escape' && !mod && !e.shiftKey) {
        if (viewMode === 'focus') {
          e.preventDefault();
          setViewMode('edit');
          return;
        }
        // 如果快捷键帮助面板打开，关闭它
        if (showShortcutHelp) {
          e.preventDefault();
          closeShortcutHelp();
          return;
        }
      }

      // Cmd+? (Cmd+Shift+/): 打开快捷键帮助面板
      if (mod && e.shiftKey && e.key === '?') {
        e.preventDefault();
        toggleShortcutHelp();
        return;
      }
    };

    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [editor, viewMode, setViewMode, showShortcutHelp, closeShortcutHelp, toggleShortcutHelp, t]);

  return { showShortcutHelp, toggleShortcutHelp, closeShortcutHelp };
}
