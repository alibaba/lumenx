'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Editor } from '@tiptap/react';
import { isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';

const DB_NAME = 'scriptEditorCache';
const STORE_NAME = 'documents';
const DB_VERSION = 1;

interface CachedDocument {
  projectId: string;
  content: object;
  timestamp: number;
  wordCount: number;
}

function cacheKey(projectId: string): string {
  return `${requireActiveRuntimeEnvironment().key}::${projectId}`;
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
    const req = store.get(cacheKey(projectId));
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
 * 1. 将离线编辑写入 IndexedDB
 * 2. 编辑器初始化时检查当前项目是否存在近期本地缓存
 * 3. 如果存在近期本地缓存 → 显示可选恢复提示条
 * 4. 网络离线时继续写入 IndexedDB
 *
 * 注意：此 hook 只提供本地草稿提示，不证明本地内容比服务器更新，也不自动同步到服务器。
 */
export function useOfflineCache(projectId: string | undefined, editor: Editor | null) {
  const [hasNewerLocal, setHasNewerLocal] = useState(false);
  const [isOffline, setIsOffline] = useState(
    typeof navigator !== 'undefined' ? !navigator.onLine : false
  );
  const cachedContentRef = useRef<object | null>(null);
  const cachedOwnerRef = useRef<string | null>(null);
  const currentProjectRef = useRef<string | undefined>(projectId);
  useEffect(() => {
    currentProjectRef.current = projectId;
  }, [projectId]);

  // Monitor online/offline status
  useEffect(() => {
    const handleOnline = () => {
      setIsOffline(false);
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
    cachedContentRef.current = null;
    cachedOwnerRef.current = null;
    setHasNewerLocal(false);
    if (!projectId) return;
    let active = true;
    const environment = requireActiveRuntimeEnvironment();
    const key = `${environment.key}::${projectId}`;

    (async () => {
      try {
        const cached = await getCache(projectId);
        if (!active || currentProjectRef.current !== projectId
          || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
        if (cached) {
          cachedContentRef.current = cached.content;
          cachedOwnerRef.current = key;
          // If we have a recent local cache, offer manual recovery.
          const now = Date.now();
          const age = now - cached.timestamp;
          if (age < 24 * 60 * 60 * 1000) {
            // This is an unconfirmed local draft hint, not a server freshness proof.
            setHasNewerLocal(true);
          }
        }
      } catch {
        // IndexedDB unavailable, ignore
      }
    })();
    return () => { active = false; };
  }, [projectId]);

  // Save to local cache (called externally after each successful save or on edits while offline)
  const saveToLocal = useCallback(
    async (content: object, wordCount: number) => {
      if (!projectId) return;
      const environment = requireActiveRuntimeEnvironment();
      const key = `${environment.key}::${projectId}`;
      try {
        await setCache({
          projectId: key,
          content,
          timestamp: Date.now(),
          wordCount,
        });
        if (currentProjectRef.current === projectId && isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
          cachedContentRef.current = content;
          cachedOwnerRef.current = key;
        }
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
    if (!editor || !projectId || !cachedContentRef.current) return;
    const environment = requireActiveRuntimeEnvironment();
    if (cachedOwnerRef.current !== `${environment.key}::${projectId}`
      || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
    const editableEditor = editor as Editor & { isEditable?: boolean };
    if (editableEditor.isEditable === false) return;
    editor.commands.setContent(cachedContentRef.current);
    setHasNewerLocal(false);
  }, [editor, projectId]);

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
