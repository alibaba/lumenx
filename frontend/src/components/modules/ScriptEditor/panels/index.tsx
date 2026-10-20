'use client';

import { useEffect, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import { motion, AnimatePresence } from 'framer-motion';
import { Users, Camera, Workflow, MapPin, Package, StickyNote, Sparkles, Lock, Unlock } from 'lucide-react';
import type { Editor } from '@tiptap/react';
import { useEditorStore } from '@/store/editorStore';
import CharacterPanel from './CharacterPanel';
import ShotPanel from './ShotPanel';
import PipelinePanel from './PipelinePanel';
import LocationPanel from './LocationPanel';
import PropsPanel from './PropsPanel';
import NotesPanel from './NotesPanel';
import L3CompletionPanel from './L3CompletionPanel';

export interface RightPanelContainerProps {
  editor: Editor | null;
  mode?: 'full' | 'embedded' | 'focus';
  projectId?: string;
  onEnterPipeline?: () => void;
}

type PanelTab = 'characters' | 'shots' | 'pipeline' | 'locations' | 'props' | 'notes' | 'ai';

interface TabDef {
  id: PanelTab;
  label: string;
  icon: React.ReactNode;
  group: 'primary' | 'secondary';
}

export default function RightPanelContainer({
  editor,
  mode = 'full',
  projectId,
  onEnterPipeline,
}: RightPanelContainerProps) {
  const t = useTranslations('scriptEditor');
  const activePanel = useEditorStore((s) => s.activeRightPanel);
  const setActivePanel = useEditorStore((s) => s.setActiveRightPanel);
  const panelLocked = useEditorStore((s) => s.rightPanelLocked);
  const setRightPanelLocked = useEditorStore((s) => s.setRightPanelLocked);

  const ALL_TABS: TabDef[] = [
    { id: 'characters', label: t('panels.characters'), icon: <Users size={14} />, group: 'primary' },
    { id: 'shots', label: t('panels.shots'), icon: <Camera size={14} />, group: 'primary' },
    { id: 'pipeline', label: t('panels.pipeline'), icon: <Workflow size={14} />, group: 'primary' },
    { id: 'locations', label: t('panels.locations'), icon: <MapPin size={14} />, group: 'secondary' },
    { id: 'props', label: t('panels.props'), icon: <Package size={14} />, group: 'secondary' },
    { id: 'notes', label: t('panels.notes'), icon: <StickyNote size={14} />, group: 'secondary' },
    { id: 'ai', label: t('panels.aiCompletion'), icon: <Sparkles size={14} />, group: 'secondary' },
  ];

  const TABS_EMBEDDED: TabDef[] = [
    { id: 'shots', label: t('panels.shots'), icon: <Camera size={14} />, group: 'primary' },
    { id: 'pipeline', label: t('panels.pipeline'), icon: <Workflow size={14} />, group: 'primary' },
  ];

  const togglePanelLock = useCallback(() => {
    setRightPanelLocked(!panelLocked);
  }, [panelLocked, setRightPanelLocked]);

  const isEmbedded = mode === 'embedded';
  const tabs = isEmbedded ? TABS_EMBEDDED : ALL_TABS;

  // Map editorStore panel names to our tab IDs
  const currentTab: PanelTab = (() => {
    const valid = tabs.find((t) => t.id === activePanel);
    if (valid) return valid.id;
    // Default fallback
    return isEmbedded ? 'shots' : 'characters';
  })();

  const handleTabChange = useCallback(
    (tab: PanelTab) => {
      setActivePanel(tab as typeof activePanel);
    },
    [setActivePanel]
  );

  // Smart auto-switch: listen to editor selection changes
  useEffect(() => {
    if (!editor || panelLocked) return;

    const handleSelectionUpdate = () => {
      const { $from } = editor.state.selection;

      // Walk up the node tree to find context
      for (let depth = $from.depth; depth >= 0; depth--) {
        const node = $from.node(depth);
        if (node.type.name === 'characterCue') {
          if (!isEmbedded) {
            setActivePanel('characters');
          }
          return;
        }
        if (node.type.name === 'shotBlock') {
          setActivePanel('shots');
          return;
        }
      }
    };

    editor.on('selectionUpdate', handleSelectionUpdate);
    return () => {
      editor.off('selectionUpdate', handleSelectionUpdate);
    };
  }, [editor, panelLocked, isEmbedded, setActivePanel]);

  const primaryTabs = tabs.filter((t) => t.group === 'primary');
  const secondaryTabs = tabs.filter((t) => t.group === 'secondary');

  return (
    <div className="flex h-full flex-col">
      {/* Tab bar - Primary group */}
      <div className="flex shrink-0 border-b border-white/10">
        {primaryTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => handleTabChange(tab.id)}
            className={`relative flex flex-1 items-center justify-center gap-1.5 px-3 py-2.5 text-xs font-medium transition-colors ${
              currentTab === tab.id
                ? 'text-foreground'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            {tab.icon}
            {tab.label}
            {currentTab === tab.id && (
              <motion.div
                layoutId="panel-tab-indicator"
                className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full bg-indigo-500"
                transition={{ type: 'spring', stiffness: 400, damping: 30 }}
              />
            )}
          </button>
        ))}

        {/* Lock/Unlock toggle */}
        <button
          type="button"
          onClick={togglePanelLock}
          title={panelLocked ? t('panels.unlockPanel') : t('panels.lockPanel')}
          className={`flex items-center justify-center px-2.5 py-2.5 text-xs transition-colors border-l border-white/5 ${
            panelLocked
              ? 'text-amber-400 hover:text-amber-300'
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          {panelLocked ? <Lock size={13} /> : <Unlock size={13} />}
        </button>
      </div>

      {/* Tab bar - Secondary group */}
      {secondaryTabs.length > 0 && (
        <div className="flex shrink-0 border-b border-white/5 bg-zinc-900/30">
          {secondaryTabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => handleTabChange(tab.id)}
              className={`relative flex flex-1 items-center justify-center gap-1 px-2 py-2 text-xs font-medium transition-colors ${
                currentTab === tab.id
                  ? 'text-foreground'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              {tab.icon}
              {tab.label}
              {currentTab === tab.id && (
                <motion.div
                  layoutId="panel-tab-indicator-secondary"
                  className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full bg-teal-500"
                  transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                />
              )}
            </button>
          ))}
        </div>
      )}

      {/* Panel content */}
      <div className="flex-1 overflow-y-auto">
        <AnimatePresence mode="wait">
          <motion.div
            key={currentTab}
            initial={{ opacity: 0, x: 10 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -10 }}
            transition={{ duration: 0.15 }}
          >
            {currentTab === 'characters' && !isEmbedded && (
              <CharacterPanel editor={editor} />
            )}
            {currentTab === 'shots' && (
              <ShotPanel editor={editor} />
            )}
            {currentTab === 'pipeline' && (
              <PipelinePanel
                projectId={projectId}
                onEnterPipeline={onEnterPipeline}
              />
            )}
            {currentTab === 'locations' && !isEmbedded && (
              <LocationPanel editor={editor} />
            )}
            {currentTab === 'props' && !isEmbedded && (
              <PropsPanel editor={editor} />
            )}
            {currentTab === 'notes' && !isEmbedded && (
              <NotesPanel editor={editor} />
            )}
            {currentTab === 'ai' && !isEmbedded && (
              <L3CompletionPanel />
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
