import { useEffect, useRef, useCallback } from 'react';
import { Editor } from '@tiptap/react';
import { scriptEditorApi } from '@/lib/scriptEditorApi';
import { useEditorStore } from '@/store/editorStore';

const AUTOSAVE_INTERVAL_MS = 30_000; // 30 seconds

/**
 * 自动保存 Hook
 * - 30s 周期自动保存（仅当 isDirty 时）
 * - Cmd+S / Ctrl+S 手动保存 + 创建快照
 * - beforeunload 事件拦截（离开页面前提醒保存）
 */
export function useAutoSave(editor: Editor | null, projectId: string | null) {
  const { isDirty, setDirty, setLastSavedAt } = useEditorStore();
  const isSavingRef = useRef(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 核心保存逻辑
  const save = useCallback(
    async (createSnapshot = false) => {
      if (!editor || !projectId) return;
      if (isSavingRef.current) return;

      const content = editor.getJSON();
      isSavingRef.current = true;

      try {
        await scriptEditorApi.saveDocument(projectId, content, createSnapshot);
        setDirty(false);
        setLastSavedAt(new Date());
      } catch (err) {
        console.error('[useAutoSave] Save failed:', err);
      } finally {
        isSavingRef.current = false;
      }
    },
    [editor, projectId, setDirty, setLastSavedAt]
  );

  // 30s 周期自动保存
  useEffect(() => {
    intervalRef.current = setInterval(() => {
      if (useEditorStore.getState().isDirty) {
        save(false);
      }
    }, AUTOSAVE_INTERVAL_MS);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [save]);

  // Cmd+S / Ctrl+S 手动保存（创建快照）
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        save(true); // 手动保存时创建快照
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [save]);

  // beforeunload 拦截
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (useEditorStore.getState().isDirty) {
        e.preventDefault();
        // 尝试在卸载前保存
        if (editor && projectId) {
          const content = editor.getJSON();
          // 使用 sendBeacon 或同步请求不可靠，这里仅做拦截提醒
          navigator.sendBeacon?.(
            `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:17177'}/projects/${projectId}/document`,
            JSON.stringify({ content, create_snapshot: false })
          );
        }
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [editor, projectId]);

  return { save };
}
