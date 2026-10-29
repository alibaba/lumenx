import { useEffect, useRef, useCallback } from 'react';
import { Editor, type EditorEvents } from '@tiptap/react';
import { useEditorStore } from '@/store/editorStore';
import { scriptEditorApi, type DeriveGapsRequest } from '@/lib/scriptEditorApi';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment, type RuntimeEnvironment } from '@/lib/runtimeEnvironment';

/** 用户停止输入后触发 L3 的延迟（ms） */
const DEBOUNCE_MS = 2000;

/** L3 调用缓存有效期（ms）：5 分钟 */
const CACHE_TTL_MS = 5 * 60 * 1000;

/** confidence 阈值，低于此值时触发 L3 */
const CONFIDENCE_THRESHOLD = 0.7;

interface L3RequestSnapshot {
  key: string;
  params: DeriveGapsRequest;
  environment: RuntimeEnvironment;
}

function extractRawTextBlocks(editor: Editor): string[] {
  const text = editor.getText?.() ?? '';
  return text
    .split(/\n+/)
    .map((block) => block.trim())
    .filter(Boolean);
}

function buildRequestKey(environment: RuntimeEnvironment, projectId: string, params: DeriveGapsRequest): string {
  return JSON.stringify({
    environment: environment.key,
    projectId,
    rawTextBlocks: params.raw_text_blocks ?? [],
    alreadyExtracted: params.already_extracted ?? { scenes: [], characters: [] },
    gaps: params.gaps ?? [],
  });
}

function isAbortError(error: unknown): boolean {
  return (
    error instanceof DOMException && error.name === 'AbortError'
  ) || (
    error instanceof Error && (error.name === 'CanceledError' || error.name === 'AbortError')
  ) || (
    typeof error === 'object' && error !== null && 'code' in error && (error as { code?: unknown }).code === 'ERR_CANCELED'
  );
}

/**
 * L3 LLM 兜底补全 Hook
 *
 * 当 L1 结构化率（confidenceScore）低于阈值时，
 * 在用户停止输入 2 秒后异步调用后端 /derive_gaps API，
 * 获取 AI 辅助补全结果（角色、道具、节拍、地点）。
 *
 * 设计原则：
 * - L1 结果始终优先，L3 仅补充缺失部分
 * - L3 失败不阻塞编辑流
 * - 结果缓存 5 分钟，避免频繁调用
 */
export function useL3Completion(editor: Editor | null, projectId: string | null) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeRequestRef = useRef<{ key: string; seq: number } | null>(null);
  const requestSeqRef = useRef(0);
  const mountedRef = useRef(false);
  const lastSuccessRef = useRef<{ key: string; time: number } | null>(null);

  const confidenceScore = useEditorStore((s) => s.confidenceScore);
  const derivedScenes = useEditorStore((s) => s.derivedScenes);
  const derivedCharacters = useEditorStore((s) => s.derivedCharacters);
  const l3Status = useEditorStore((s) => s.l3Status);
  const l3StatusRef = useRef(l3Status);
  const setL3Status = useEditorStore((s) => s.setL3Status);
  const setL3Results = useEditorStore((s) => s.setL3Results);
  const setL3LastFetchTime = useEditorStore((s) => s.setL3LastFetchTime);

  useEffect(() => {
    l3StatusRef.current = l3Status;
  }, [l3Status]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const clearL3State = useCallback(() => {
    setL3Results(null);
    setL3LastFetchTime(null);
    setL3Status('idle');
  }, [setL3LastFetchTime, setL3Results, setL3Status]);

  const cancelPending = useCallback(() => {
    activeRequestRef.current = null;
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    const resetOwner = () => {
      cancelPending();
      lastSuccessRef.current = null;
      clearL3State();
    };
    resetOwner();
    const unsubscribe = subscribeRuntimeEnvironment(resetOwner);
    return () => {
      unsubscribe();
      cancelPending();
    };
  }, [cancelPending, clearL3State, editor, projectId]);

  const createRequestSnapshot = useCallback((): L3RequestSnapshot | null => {
    const environment = getRuntimeEnvironment();
    if (!environment || !projectId || !editor || editor.isEditable === false || editor.isDestroyed) return null;

    const rawTextBlocks = extractRawTextBlocks(editor);
    if (rawTextBlocks.length === 0) return null;

    const params: DeriveGapsRequest = {
      already_extracted: {
        scenes: derivedScenes.map((s) => s.title),
        characters: derivedCharacters.map((c) => c.name),
      },
      gaps: ['props', 'beats', 'locations'],
      raw_text_blocks: rawTextBlocks,
    };

    return {
      key: buildRequestKey(environment, projectId, params),
      params,
      environment,
    };
  }, [derivedCharacters, derivedScenes, editor, projectId]);

  /**
   * 判断是否应该触发 L3 请求
   */
  const shouldFetch = useCallback((snapshot: L3RequestSnapshot | null): snapshot is L3RequestSnapshot => {
    if (!snapshot || !projectId) return false;

    // 同一个输入已经在 loading → 不重复触发
    if (l3StatusRef.current === 'loading' && activeRequestRef.current?.key === snapshot.key) return false;

    // 同一输入缓存未过期 → 不重复调用
    if (
      lastSuccessRef.current?.key === snapshot.key &&
      Date.now() - lastSuccessRef.current.time < CACHE_TTL_MS
    ) {
      return false;
    }

    return true;
  }, [projectId]);

  /**
   * 执行 L3 请求
   */
  const fetchL3 = useCallback(async () => {
    const snapshot = createRequestSnapshot();
    if (!snapshot || confidenceScore >= CONFIDENCE_THRESHOLD) {
      // The current document no longer needs completion. Discard the previous
      // request/results rather than leaving an obsolete loading or cache state.
      cancelPending();
      lastSuccessRef.current = null;
      clearL3State();
      return;
    }
    if (!shouldFetch(snapshot) || !projectId) return;

    // 取消之前的不同输入请求；同输入 loading 已在 shouldFetch 中挡掉。
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const seq = requestSeqRef.current + 1;
    requestSeqRef.current = seq;
    activeRequestRef.current = { key: snapshot.key, seq };

    setL3Status('loading');

    try {
      const response = await scriptEditorApi.deriveGaps(projectId, snapshot.params, { signal: controller.signal });
      const currentSnapshot = createRequestSnapshot();
      const isCurrent =
        mountedRef.current &&
        !controller.signal.aborted &&
        isCurrentRuntimeEnvironment(snapshot.environment.key, snapshot.environment.generation) &&
        activeRequestRef.current?.seq === seq &&
        activeRequestRef.current?.key === snapshot.key &&
        currentSnapshot?.key === snapshot.key;
      if (!isCurrent) return;

      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      activeRequestRef.current = null;
      const now = Date.now();
      lastSuccessRef.current = { key: snapshot.key, time: now };
      setL3Results(response.results);
      setL3Status('success');
      setL3LastFetchTime(now);
    } catch (error: unknown) {
      if (isAbortError(error)) return;
      const currentSnapshot = createRequestSnapshot();
      const isCurrent =
        mountedRef.current &&
        !controller.signal.aborted &&
        isCurrentRuntimeEnvironment(snapshot.environment.key, snapshot.environment.generation) &&
        activeRequestRef.current?.seq === seq &&
        activeRequestRef.current?.key === snapshot.key &&
        currentSnapshot?.key === snapshot.key;
      if (!isCurrent) return;

      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      activeRequestRef.current = null;
      // L3 失败不阻塞编辑流，静默降级
      setL3Status('error');
      console.warn('[L3Completion] derive_gaps failed:', error);
    }
  }, [cancelPending, clearL3State, confidenceScore, createRequestSnapshot, projectId, setL3LastFetchTime, setL3Results, setL3Status, shouldFetch]);

  // L1 refresh changes fetchL3's inputs without authorizing a new request or
  // cancelling the debounce created by the user's last document edit.
  const fetchL3Ref = useRef(fetchL3);
  fetchL3Ref.current = fetchL3;

  useEffect(() => {
    if (!editor) return;

    const handleUpdate = ({ transaction }: EditorEvents['update']) => {
      const environment = getRuntimeEnvironment();
      if (!transaction.docChanged || !editor.isEditable || !environment) return;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        if (isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
          void fetchL3Ref.current();
        }
      }, DEBOUNCE_MS);
    };
    const handleTransaction = ({ transaction }: EditorEvents['transaction']) => {
      if (transaction.docChanged && transaction.getMeta('preventUpdate')) {
        cancelPending();
        lastSuccessRef.current = null;
        clearL3State();
      }
    };

    editor.on('update', handleUpdate);
    editor.on('transaction', handleTransaction);
    return () => {
      editor.off('update', handleUpdate);
      editor.off('transaction', handleTransaction);
      cancelPending();
    };
  }, [cancelPending, clearL3State, editor, projectId]);
}
