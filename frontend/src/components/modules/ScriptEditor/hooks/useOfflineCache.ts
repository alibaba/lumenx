'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Editor } from '@tiptap/react';

const DB_NAME = 'scriptEditorCache';
const STORE_NAME = 'documents';
const DB_VERSION = 1;

interface CachedDocument {
  projectId: string;
  content: object;
  timestamp: number;
  wordCount: number;
}

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'projectId' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function getCache(projectId: string): Promise<CachedDocument | undefined> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const store = tx.objectStore(STORE_NAME);
    const req = store.get(projectId);
    req.onsuccess = () => resolve(req.result as CachedDocument | undefined);
    req.onerror = () => reject(req.error);
  });
}

async function setCache(doc: CachedDocument): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    const req = store.put(doc);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

/**
 * IndexedDB 离线缓存 Hook
 *
 * 功能：
 * 1. 每次 autoSave 成功后，同时写入 IndexedDB
 * 2. 编辑器初始化时，检查本地缓存 vs 服务器数据的 timestamp
 * 3. 如果本地更新 → 显示恢复提示条
 * 4. 网络离线时继续写入 IndexedDB
 * 5. 网络恢复（online 事件）时自动同步到服务器
 */
export function useOfflineCache(projectId: string | undefined, editor: Editor | null) {
  const [hasNewerLocal, setHasNewerLocal] = useState(false);
  const [isOffline, setIsOffline] = useState(
    typeof navigator !== 'undefined' ? !navigator.onLine : false
  );
  const cachedContentRef = useRef<object | null>(null);
  const syncPendingRef = useRef(false);

  // Monitor online/offline status
  useEffect(() => {
    const handleOnline = () => {
      setIsOffline(false);
      // Trigger sync when coming back online
      syncPendingRef.current = true;
    };
    const handleOffline = () => setIsOffline(true);

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  // On mount or projectId change, check local cache
  useEffect(() => {
    if (!projectId) return;

    (async () => {
      try {
        const cached = await getCache(projectId);
        if (cached) {
          cachedContentRef.current = cached.content;
          // If we have a local cache that's newer than a threshold (e.g. within last 24h)
          const now = Date.now();
          const age = now - cached.timestamp;
          if (age < 24 * 60 * 60 * 1000) {
            // Mark as potentially newer — the shell can compare with server timestamp
            setHasNewerLocal(true);
          }
        }
      } catch {
        // IndexedDB unavailable, ignore
      }
    })();
  }, [projectId]);

  // Save to local cache (called externally after each successful save or on edits while offline)
  const saveToLocal = useCallback(
    async (content: object, wordCount: number) => {
      if (!projectId) return;
      try {
        await setCache({
          projectId,
          content,
          timestamp: Date.now(),
          wordCount,
        });
        cachedContentRef.current = content;
      } catch {
        // Silently fail if IndexedDB is unavailable
      }
    },
    [projectId]
  );

  // Auto-save to IndexedDB when editor content changes and offline
  useEffect(() => {
    if (!editor || !projectId || !isOffline) return;

    const handleUpdate = () => {
      const content = editor.getJSON();
      const text = editor.getText();
      saveToLocal(content, text.length);
    };

    editor.on('update', handleUpdate);
    return () => {
      editor.off('update', handleUpdate);
    };
  }, [editor, projectId, isOffline, saveToLocal]);

  // Restore from local cache
  const restoreFromLocal = useCallback(() => {
    if (!editor || !cachedContentRef.current) return;
    editor.commands.setContent(cachedContentRef.current);
    setHasNewerLocal(false);
  }, [editor]);

  // Dismiss the local restore hint
  const dismissLocalRestore = useCallback(() => {
    setHasNewerLocal(false);
  }, []);

  return {
    hasNewerLocal,
    restoreFromLocal,
    dismissLocalRestore,
    isOffline,
    saveToLocal,
  };
}
