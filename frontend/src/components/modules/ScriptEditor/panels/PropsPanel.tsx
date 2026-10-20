'use client';

import { useMemo } from 'react';
import { Package } from 'lucide-react';
import type { Editor } from '@tiptap/react';
import { useTranslations } from 'next-intl';
import { useEditorStore } from '@/store/editorStore';

export interface PropsPanelProps {
  editor: Editor | null;
}

interface PropEntry {
  name: string;
  firstSceneTitle: string;
}

/**
 * 从动作文本中提取可能的道具名词（基础正则版本）
 * - 中文：提取引号（「」「""」）内的物品
 * - 英文：提取大写开头的名词短语
 */
function extractProps(text: string): string[] {
  const props = new Set<string>();

  // 中文引号内容：「xxx」或 "xxx" 或 'xxx'
  const zhRe = /[「"']([\u4e00-\u9fff\w]{1,10})[」"']/g;
  let m: RegExpExecArray | null;
  while ((m = zhRe.exec(text)) !== null) {
    props.add(m[1]);
  }

  // 英文：大写开头的名词（排除句首）
  const enRe = /(?<=\s)([A-Z][a-z]+(?:\s[A-Z][a-z]+)*)/g;
  while ((m = enRe.exec(text)) !== null) {
    props.add(m[0]);
  }

  return Array.from(props);
}

export default function PropsPanel({ editor }: PropsPanelProps) {
  const t = useTranslations('scriptEditor');
  const derivedScenes = useEditorStore((s) => s.derivedScenes);

  const propEntries = useMemo<PropEntry[]>(() => {
    if (!editor) return [];

    const propsMap = new Map<string, string>(); // propName -> first scene title
    const doc = editor.state.doc;
    let currentSceneTitle = '';

    doc.descendants((node) => {
      if (node.type.name === 'sceneHeading') {
        currentSceneTitle = node.textContent || t('sidebar.untitledScene');
        return true;
      }
      if (node.type.name === 'action') {
        const text = node.textContent;
        const found = extractProps(text);
        for (const prop of found) {
          if (!propsMap.has(prop)) {
            propsMap.set(prop, currentSceneTitle);
          }
        }
      }
      return true;
    });

    return Array.from(propsMap.entries()).map(([name, firstSceneTitle]) => ({
      name,
      firstSceneTitle,
    }));
  }, [editor, derivedScenes]);

  if (propEntries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-800 mb-3">
          <Package size={20} className="text-zinc-500" />
        </div>
        <p className="text-sm text-text-muted">{t('panels.propsEmpty')}</p>
        <p className="text-xs text-text-muted/60 mt-1">
          {t('panels.propsEmptyHint')}
        </p>
      </div>
    );
  }

  return (
    <div className="p-3">
      <div className="flex items-center gap-2 mb-3">
        <Package size={14} className="text-text-muted" />
        <span className="text-xs font-medium text-text-muted uppercase tracking-wider">
          {t('panels.propsCount', { count: propEntries.length })}
        </span>
      </div>
      <div className="space-y-1">
        {propEntries.map((entry) => (
          <div
            key={entry.name}
            className="flex items-center justify-between rounded-lg border border-white/10 bg-zinc-800/80 px-3 py-2 hover:border-white/20 transition-colors"
          >
            <div className="flex items-center gap-2 min-w-0">
              <Package size={12} className="shrink-0 text-zinc-400" />
              <span className="text-sm text-foreground truncate">{entry.name}</span>
            </div>
            <span className="shrink-0 text-xs text-text-muted ml-2 truncate max-w-[120px]">
              {entry.firstSceneTitle}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
