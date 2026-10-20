import { useEffect, useRef, useCallback } from 'react';
import { Editor } from '@tiptap/react';
import { useEditorStore, DerivedScene, DerivedCharacter } from '@/store/editorStore';

const DEBOUNCE_MS = 500;

// 英文场景标题正则: INT. OFFICE - DAY
const EN_SCENE_RE = /^(?:INT|EXT|INT\/EXT)\.?\s+(.+?)\s*[-–—]\s*(.+)$/i;
// 中文场景标题正则: 内景. 办公室 - 日
const ZH_SCENE_RE = /^(?:内景|外景|内\/外)\.?\s*(.+?)\s*[-–—·]\s*(.+)$/;

/**
 * 从场景标题文本提取地点
 * e.g. "INT. 办公室 - 日" → "办公室"
 */
function extractLocation(text: string): string | null {
  const enMatch = text.match(EN_SCENE_RE);
  if (enMatch) return enMatch[1].trim();

  const zhMatch = text.match(ZH_SCENE_RE);
  if (zhMatch) return zhMatch[1].trim();

  return null;
}

/**
 * 从场景标题文本提取时间
 * e.g. "INT. 办公室 - 日" → "日"
 */
function extractTimeOfDay(text: string): string | null {
  const enMatch = text.match(EN_SCENE_RE);
  if (enMatch) return enMatch[2].trim();

  const zhMatch = text.match(ZH_SCENE_RE);
  if (zhMatch) return zhMatch[2].trim();

  return null;
}

/**
 * 从 Editor 文档 AST 中派生结构化数据
 */
function deriveFromDocument(editor: Editor) {
  const doc = editor.state.doc;
  const scenes: DerivedScene[] = [];
  const characterMap = new Map<string, { name: string; count: number; firstScene: number }>();
  let totalChars = 0;
  let structuredChars = 0;
  let sceneIndex = 0;

  // 遍历文档节点
  doc.descendants((node) => {
    totalChars += node.textContent.length;

    if (node.type.name === 'sceneHeading') {
      sceneIndex++;
      const text = node.textContent;
      scenes.push({
        id: node.attrs.id || `scene-${sceneIndex}`,
        number: sceneIndex,
        intExt: node.attrs.intExt || null,
        location: node.attrs.location || extractLocation(text),
        timeOfDay: node.attrs.timeOfDay || extractTimeOfDay(text),
        title: text,
      });
      structuredChars += node.textContent.length;
    }

    if (node.type.name === 'characterCue') {
      const name = node.textContent.trim().toUpperCase();
      if (name) {
        const existing = characterMap.get(name);
        if (existing) {
          existing.count++;
        } else {
          characterMap.set(name, { name, count: 1, firstScene: sceneIndex });
        }
      }
      structuredChars += node.textContent.length;
    }

    if (node.type.name === 'dialogue' || node.type.name === 'transition') {
      structuredChars += node.textContent.length;
    }

    return true; // 继续遍历子节点
  });

  const characters: DerivedCharacter[] = Array.from(characterMap.entries()).map(
    ([key, val]) => ({
      id: key.toLowerCase().replace(/\s+/g, '-'),
      name: val.name,
      occurrences: val.count,
      firstAppearance: val.firstScene,
    })
  );

  const wordCount = totalChars; // 中文按字符数计算
  const estimatedDuration = scenes.length * 60; // 每场景约60秒
  const confidenceScore = totalChars > 0 ? structuredChars / totalChars : 0;

  return { scenes, characters, wordCount, estimatedDuration, confidenceScore };
}

/**
 * L1 实时派生引擎
 *
 * 监听 Editor 的 update 事件，debounce 500ms 后：
 * 1. 遍历 AST 提取 Scene 节点 → derivedScenes
 * 2. 遍历 AST 提取 CharacterCue 节点 → derivedCharacters（去重）
 * 3. 计算总字数
 * 4. 估计时长（每个场景约 60 秒基准）
 * 5. 计算 confidence_score（有结构化节点的字符数 / 总字符数）
 * 6. 更新 editorStore
 */
export function useDerivation(editor: Editor | null) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const updateDerivation = useEditorStore((state) => state.updateDerivation);

  const runDerivation = useCallback(() => {
    if (!editor) return;

    const { scenes, characters, wordCount, estimatedDuration, confidenceScore } =
      deriveFromDocument(editor);

    updateDerivation({
      scenes,
      characters,
      duration: estimatedDuration,
      wordCount,
      confidenceScore,
    });
  }, [editor, updateDerivation]);

  useEffect(() => {
    if (!editor) return;

    // 初始派生一次
    runDerivation();

    const handleUpdate = () => {
      // 清除旧的 debounce timer
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      // 设置新的 debounce timer
      timerRef.current = setTimeout(() => {
        runDerivation();
      }, DEBOUNCE_MS);
    };

    editor.on('update', handleUpdate);

    return () => {
      editor.off('update', handleUpdate);
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, [editor, runDerivation]);
}
