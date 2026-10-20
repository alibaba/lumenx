import { useEffect, useRef, useMemo } from 'react';
import { Editor } from '@tiptap/react';
import { useTranslations } from 'next-intl';
import { useEditorStore, DerivedScene, DerivedCharacter } from '@/store/editorStore';

const DEBOUNCE_MS = 1000;

// 消失警告阈值
const MIN_APPEARANCES_FOR_WARNING = 3; // 角色至少出场 3 次
const GAP_THRESHOLD = 5; // 连续 5 个场景未出现触发警告

export interface ContinuityWarning {
  type: 'character_disappeared' | 'location_reuse' | 'character_stats';
  severity: 'info' | 'warning';
  message: string;
  sceneIndex: number;
  relatedEntity: string;
}

export interface CharacterStat {
  name: string;
  appearances: number;
  firstScene: number;
  lastScene: number;
  scenes: number[];
}

export interface LocationStat {
  location: string;
  appearances: number;
  scenes: number[];
}

export interface ContinuityReport {
  characterStats: CharacterStat[];
  locationStats: LocationStat[];
  warnings: ContinuityWarning[];
}

const EMPTY_REPORT: ContinuityReport = {
  characterStats: [],
  locationStats: [],
  warnings: [],
};

/**
 * 从编辑器文档中提取角色在每个场景的出现信息
 * 返回 Map<characterName, sceneNumbers[]>
 */
function extractCharacterSceneMap(editor: Editor): Map<string, number[]> {
  const doc = editor.state.doc;
  const characterScenes = new Map<string, number[]>();
  let currentScene = 0;

  doc.descendants((node) => {
    if (node.type.name === 'sceneHeading') {
      currentScene++;
    }
    if (node.type.name === 'characterCue') {
      const name = node.textContent.trim().toUpperCase();
      if (name) {
        const existing = characterScenes.get(name) || [];
        // 避免同一场景重复记录
        if (existing[existing.length - 1] !== currentScene) {
          existing.push(currentScene);
        }
        characterScenes.set(name, existing);
      }
    }
    return true;
  });

  return characterScenes;
}

/**
 * 从场景数据中提取地点统计
 */
function extractLocationStats(scenes: DerivedScene[]): LocationStat[] {
  const locationMap = new Map<string, number[]>();

  scenes.forEach((scene) => {
    const loc = scene.location;
    if (loc) {
      const normalizedLoc = loc.trim();
      const existing = locationMap.get(normalizedLoc) || [];
      existing.push(scene.number ?? 0);
      locationMap.set(normalizedLoc, existing);
    }
  });

  return Array.from(locationMap.entries()).map(([location, sceneNumbers]) => ({
    location,
    appearances: sceneNumbers.length,
    scenes: sceneNumbers,
  }));
}

/**
 * 检测角色消失警告
 * 规则：角色出场 ≥ MIN_APPEARANCES_FOR_WARNING 次后，
 *       连续 GAP_THRESHOLD 个场景未出现 → 生成 warning
 */
function detectDisappearanceWarnings(
  characterScenes: Map<string, number[]>,
  totalScenes: number,
  t: (key: string, params?: Record<string, string | number | Date>) => string
): ContinuityWarning[] {
  const warnings: ContinuityWarning[] = [];

  characterScenes.forEach((scenes, name) => {
    if (scenes.length < MIN_APPEARANCES_FOR_WARNING) return;

    const lastAppearance = scenes[scenes.length - 1];
    const gap = totalScenes - lastAppearance;

    if (gap >= GAP_THRESHOLD) {
      warnings.push({
        type: 'character_disappeared',
        severity: 'warning',
        message: t('continuity.disappeared', { name, count: gap, total: scenes.length }),
        sceneIndex: lastAppearance,
        relatedEntity: name,
      });
    }

    // 检测中间的大间隔
    for (let i = 1; i < scenes.length; i++) {
      const midGap = scenes[i] - scenes[i - 1];
      if (midGap >= GAP_THRESHOLD) {
        warnings.push({
          type: 'character_disappeared',
          severity: 'info',
          message: t('continuity.midGap', { name, from: scenes[i - 1], to: scenes[i], count: midGap }),
          sceneIndex: scenes[i - 1],
          relatedEntity: name,
        });
      }
    }
  });

  return warnings;
}

/**
 * 故事连贯性检测 Hook（零 LLM 成本，纯前端计算）
 *
 * 从 editorStore.derivedScenes 和 Editor AST 中读取数据：
 * 1. 计算每个角色出现的场景列表
 * 2. 计算每个地点出现的场景列表
 * 3. 检测"消失警告"：角色出场 ≥ 3 次后，连续 5 个场景未出现
 * 4. debounce 1000ms，避免频繁计算
 */
export function useContinuityCheck(editor: Editor | null): ContinuityReport {
  const derivedScenes = useEditorStore((s) => s.derivedScenes);
  const t = useTranslations('scriptEditor');
  const reportRef = useRef<ContinuityReport>(EMPTY_REPORT);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const updateCountRef = useRef(0);

  // 计算连贯性报告
  const computeReport = useMemo(() => {
    return () => {
      if (!editor || derivedScenes.length === 0) {
        return EMPTY_REPORT;
      }

      const characterScenes = extractCharacterSceneMap(editor);
      const totalScenes = derivedScenes.length;

      // 角色统计
      const characterStats: CharacterStat[] = Array.from(characterScenes.entries()).map(
        ([name, scenes]) => ({
          name,
          appearances: scenes.length,
          firstScene: scenes[0] ?? 0,
          lastScene: scenes[scenes.length - 1] ?? 0,
          scenes,
        })
      );

      // 地点统计
      const locationStats = extractLocationStats(derivedScenes);

      // 检测警告
      const warnings = detectDisappearanceWarnings(characterScenes, totalScenes, t);

      return { characterStats, locationStats, warnings };
    };
  }, [editor, derivedScenes, t]);

  // Debounced update on editor changes
  useEffect(() => {
    if (!editor) return;

    const handleUpdate = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      timerRef.current = setTimeout(() => {
        const report = computeReport();
        reportRef.current = report;
        updateCountRef.current++;
      }, DEBOUNCE_MS);
    };

    // 初始计算
    reportRef.current = computeReport();

    editor.on('update', handleUpdate);

    return () => {
      editor.off('update', handleUpdate);
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, [editor, computeReport]);

  // 当 derivedScenes 变化时重新计算
  useEffect(() => {
    reportRef.current = computeReport();
  }, [derivedScenes, computeReport]);

  return reportRef.current;
}
