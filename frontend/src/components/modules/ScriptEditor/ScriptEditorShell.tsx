'use client';

import { useCallback } from 'react';
import { useTranslations } from 'next-intl';
import { EditorContent } from '@tiptap/react';
import { PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, WifiOff, RotateCcw, X } from 'lucide-react';
import { useEditorStore } from '@/store/editorStore';
import { useEditorSetup } from './hooks/useEditorSetup';
import FormatToolbar from './toolbar/FormatToolbar';
import { usePasteHandler } from './hooks/usePasteHandler';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { useContinuityCheck } from './hooks/useContinuityCheck';
import { useSceneFolding } from './hooks/useSceneFolding';
import { useViewMode } from './hooks/useViewMode';
import { useOfflineCache } from './hooks/useOfflineCache';
import { useL3Completion } from './hooks/useL3Completion';
import { PasteHintBar } from './components/PasteHintBar';
import { ShortcutHelpPanel } from './components/ShortcutHelpPanel';
import { ContinuityIndicator } from './components/ContinuityIndicator';
import RightPanelContainer from './panels';
import LeftSidebar from './sidebar';
import StoryboardView from './views/StoryboardView';

export interface ScriptEditorShellProps {
  mode?: 'full' | 'embedded' | 'focus';
  projectId?: string;
  initialContent?: string | Record<string, unknown> | null;
}

export default function ScriptEditorShell({
  mode = 'full',
  projectId,
  initialContent,
}: ScriptEditorShellProps) {
  const t = useTranslations('scriptEditor');
  const { editor, isReady } = useEditorSetup({ content: initialContent });
  const { showHint, analysis, applyFormatting, dismissHint } = usePasteHandler(editor);
  const { showShortcutHelp, closeShortcutHelp } = useKeyboardShortcuts(editor);
  const continuityReport = useContinuityCheck(editor);
  const { enabled: foldingEnabled, isAllExpanded, totalScenes: foldingTotal } = useSceneFolding(editor);
  const { mode: viewMode, setMode: setViewMode, isReadOnly, showToolbar, showSidebars } = useViewMode();
  const { hasNewerLocal, restoreFromLocal, dismissLocalRestore, isOffline } = useOfflineCache(projectId, editor);
  useL3Completion(editor, projectId ?? null);

  const isDirty = useEditorStore((s) => s.isDirty);
  const lastSavedAt = useEditorStore((s) => s.lastSavedAt);
  const wordCount = useEditorStore((s) => s.wordCount);
  const derivedScenes = useEditorStore((s) => s.derivedScenes);
  const currentFormat = useEditorStore((s) => s.currentFormat);
  const currentRendering = useEditorStore((s) => s.currentRendering);
  const leftCollapsed = useEditorStore((s) => s.leftSidebarCollapsed);
  const rightCollapsed = useEditorStore((s) => s.rightSidebarCollapsed);
  const toggleLeft = useEditorStore((s) => s.toggleLeftSidebar);
  const toggleRight = useEditorStore((s) => s.toggleRightSidebar);

  const showLeft = mode === 'full' && !leftCollapsed && showSidebars;
  const showRight = mode === 'full' && !rightCollapsed && showSidebars;
  const hideAllSidebars = mode === 'focus' || viewMode === 'focus';
  const hideLeftOnly = mode === 'embedded';

  const handleShotClick = useCallback((shotId: string) => {
    setViewMode('edit');
    if (editor) {
      const { doc } = editor.state;
      let targetPos: number | null = null;
      doc.descendants((node, pos) => {
        if (node.type.name === 'shotBlock' && node.attrs?.id === shotId) {
          targetPos = pos;
          return false;
        }
      });
      if (targetPos !== null) {
        editor.commands.setTextSelection(targetPos);
        editor.commands.scrollIntoView();
      }
    }
  }, [editor, setViewMode]);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-[#050508]">
      {/* Format Toolbar */}
      {!hideAllSidebars && showToolbar && (
        <FormatToolbar editor={editor} viewMode={viewMode} onViewModeChange={setViewMode} />
      )}

      {/* Top Toolbar */}
      {!hideAllSidebars && showToolbar && (
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/10 px-4">
          <div className="flex items-center gap-3">
            {mode === 'full' && (
              <button
                type="button"
                onClick={toggleLeft}
                className="text-text-muted hover:text-foreground transition-colors"
                aria-label="Toggle left sidebar"
              >
                {leftCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
              </button>
            )}
            <span className="text-sm font-medium text-foreground">
              {t('shell.title')}
            </span>
            {projectId && (
              <span className="text-xs text-text-muted">{projectId}</span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-text-muted">
              {isDirty ? t('status.unsaved') : lastSavedAt ? t('status.savedAt', { time: lastSavedAt.toLocaleTimeString() }) : ''}
            </span>
            {mode === 'full' && (
              <button
                type="button"
                onClick={toggleRight}
                className="text-text-muted hover:text-foreground transition-colors"
                aria-label="Toggle right sidebar"
              >
                {rightCollapsed ? <PanelRightOpen size={16} /> : <PanelRightClose size={16} />}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Main content area: Three-column layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Sidebar */}
        {!hideAllSidebars && !hideLeftOnly && showLeft && (
          <aside className="w-[260px] shrink-0 border-r border-white/10 bg-white/[0.02] backdrop-blur-xl overflow-hidden">
            <LeftSidebar editor={editor} />
          </aside>
        )}

        {/* Editor Content Area / Storyboard View */}
        {viewMode === 'storyboard' ? (
          <main className="relative flex-1 min-w-0 overflow-hidden">
            <StoryboardView editor={editor} onShotClick={handleShotClick} />
          </main>
        ) : (
          <main className={`relative flex-1 min-w-0 overflow-y-auto ${
            viewMode === 'focus' ? 'flex items-start justify-center' : ''
          }`}>
            {/* Offline / local restore banner */}
            {isOffline && (
              <div className="sticky top-0 z-10 flex items-center gap-2 bg-amber-900/30 px-4 py-2 text-xs text-amber-200 border-b border-amber-700/30">
                <WifiOff size={14} />
                <span>{t('status.offlineBanner')}</span>
              </div>
            )}
            {hasNewerLocal && (
              <div className="sticky top-0 z-10 flex items-center justify-between bg-blue-900/30 px-4 py-2 text-xs text-blue-200 border-b border-blue-700/30">
                <div className="flex items-center gap-2">
                  <RotateCcw size={14} />
                  <span>{t('status.localCacheFound')}</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={restoreFromLocal}
                    className="rounded bg-blue-700/60 px-2 py-0.5 text-xs hover:bg-blue-700/80 transition-colors"
                  >
                    {t('status.restore')}
                  </button>
                  <button
                    type="button"
                    onClick={dismissLocalRestore}
                    className="text-blue-300/60 hover:text-blue-200 transition-colors"
                  >
                    <X size={14} />
                  </button>
                </div>
              </div>
            )}
            {/* Paste Hint Bar */}
            <PasteHintBar
              visible={showHint}
              analysis={analysis}
              onApply={applyFormatting}
              onDismiss={dismissHint}
            />
            <div
              className={`script-editor script-editor-content mx-auto px-8 py-10 ${
                viewMode === 'focus' ? 'max-w-[860px]' : 'max-w-[720px]'
              }`}
              data-format={currentFormat}
              data-rendering={currentRendering}
            >
              {isReady ? (
                <EditorContent
                  editor={editor}
                  className={`prose prose-invert max-w-none focus:outline-none min-h-[60vh] ${
                    isReadOnly ? 'pointer-events-none opacity-90' : ''
                  }`}
                />
              ) : (
                <div className="flex items-center justify-center h-40 text-text-muted text-sm">
                  {t('shell.loading')}
                </div>
              )}
            </div>
          </main>
        )}

        {/* Right Sidebar - Panel */}
        {!hideAllSidebars && (showRight || mode === 'embedded') && (
          <aside className="w-[320px] shrink-0 border-l border-white/10 bg-white/[0.02] backdrop-blur-xl overflow-hidden">
            <RightPanelContainer
              editor={editor}
              mode={mode}
              projectId={projectId}
            />
          </aside>
        )}
      </div>

      {/* Status Bar */}
      {!hideAllSidebars && showToolbar && (
        <div className="flex h-8 shrink-0 items-center gap-4 border-t border-white/10 px-4 text-xs text-text-muted">
          <span>{t('status.wordCount', { count: wordCount })}</span>
          <span className="text-white/20">|</span>
          <span>
            {t('status.sceneCount', { count: derivedScenes.length })}
            {foldingEnabled && (
              <span className="ml-1 text-text-muted/60">
                ({isAllExpanded ? t('status.allExpanded') : t('status.smartFolding')})
              </span>
            )}
          </span>
          <span className="text-white/20">|</span>
          <span>
            {isOffline
              ? t('status.offlineShort')
              : isDirty
                ? t('status.unsavedDot')
                : lastSavedAt
                  ? t('status.savedAt', { time: lastSavedAt.toLocaleTimeString() })
                  : t('status.ready')}
          </span>
          <span className="text-white/20">|</span>
          <ContinuityIndicator report={continuityReport} />
          <span className="ml-auto text-text-muted/60">
            {currentFormat} / {currentRendering}
          </span>
        </div>
      )}

      {/* Shortcut Help Panel */}
      <ShortcutHelpPanel open={showShortcutHelp} onClose={closeShortcutHelp} />
    </div>
  );
}
