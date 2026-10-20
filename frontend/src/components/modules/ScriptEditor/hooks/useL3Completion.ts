import { useEffect, useRef, useCallback } from 'react';
import { Editor } from '@tiptap/react';
import { useEditorStore } from '@/store/editorStore';
import { scriptEditorApi } from '@/lib/scriptEditorApi';

/** 用户停止输入后触发 L3 的延迟（ms） */
const DEBOUNCE_MS = 2000;

/** L3 调用缓存有效期（ms）：5 分钟 */
const CACHE_TTL_MS = 5 * 60 * 1000;

/** confidence 阈值，低于此值时触发 L3 */
const CONFIDENCE_THRESHOLD = 0.7;

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

  const confidenceScore = useEditorStore((s) => s.confidenceScore);
  const derivedScenes = useEditorStore((s) => s.derivedScenes);
  const derivedCharacters = useEditorStore((s) => s.derivedCharacters);
  const l3Status = useEditorStore((s) => s.l3Status);
  const l3LastFetchTime = useEditorStore((s) => s.l3LastFetchTime);
  const setL3Status = useEditorStore((s) => s.setL3Status);
  const setL3Results = useEditorStore((s) => s.setL3Results);
  const setL3LastFetchTime = useEditorStore((s) => s.setL3LastFetchTime);

  /**
   * 判断是否应该触发 L3 请求
   */
  const shouldFetch = useCallback((): boolean => {
    // 无 projectId → 不触发
    if (!projectId) return false;

    // 已经在 loading → 不重复触发
    if (l3Status === 'loading') return false;

    // confidence 高于阈值 → 不需要 L3
    if (confidenceScore >= CONFIDENCE_THRESHOLD) return false;

    // 缓存未过期 → 不重复调用
    if (l3LastFetchTime && Date.now() - l3LastFetchTime < CACHE_TTL_MS) return false;

    return true;
  }, [projectId, l3Status, confidenceScore, l3LastFetchTime]);

  /**
   * 执行 L3 请求
   */
  const fetchL3 = useCallback(async () => {
    if (!shouldFetch() || !projectId) return;

    // 取消之前的请求
    if (abortRef.current) {
      abortRef.current.abort();
    }
    abortRef.current = new AbortController();

    setL3Status('loading');

    try {
      const alreadyExtracted = {
        scenes: derivedScenes.map((s) => s.title),
        characters: derivedCharacters.map((c) => c.name),
      };

      const response = await scriptEditorApi.deriveGaps(projectId, {
        already_extracted: alreadyExtracted,
        gaps: ['props', 'beats', 'locations'],
      });

      // 请求成功
      setL3Results(response.results ?? []);
      setL3Status('success');
      setL3LastFetchTime(Date.now());
    } catch (error: unknown) {
      // 如果是主动取消，不更新状态
      if (error instanceof Error && error.name === 'CanceledError') return;

      // L3 失败不阻塞编辑流，静默降级
      setL3Status('error');
      console.warn('[L3Completion] derive_gaps failed:', error);
    }
  }, [
    shouldFetch,
    projectId,
    derivedScenes,
    derivedCharacters,
    setL3Status,
    setL3Results,
    setL3LastFetchTime,
  ]);

  /**
   * 监听编辑器更新，debounce 后触发 L3
   */
  useEffect(() => {
    if (!editor) return;

    const handleUpdate = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }

      timerRef.current = setTimeout(() => {
        fetchL3();
      }, DEBOUNCE_MS);
    };

    editor.on('update', handleUpdate);

    return () => {
      editor.off('update', handleUpdate);
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      if (abortRef.current) {
        abortRef.current.abort();
      }
    };
  }, [editor, fetchL3]);

  /**
   * 当 confidenceScore 变化且低于阈值时，也触发一次检查
   * （覆盖粘贴大段文本后 L1 重新计算但 confidence 仍低的场景）
   */
  useEffect(() => {
    if (confidenceScore < CONFIDENCE_THRESHOLD && confidenceScore > 0) {
      // 延迟一点以避免与 editor update debounce 冲突
      const timer = setTimeout(() => {
        fetchL3();
      }, DEBOUNCE_MS + 500);
      return () => clearTimeout(timer);
    }
  }, [confidenceScore, fetchL3]);
}
