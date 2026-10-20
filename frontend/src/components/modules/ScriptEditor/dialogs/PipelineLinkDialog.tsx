'use client';

import { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, FolderPlus, Link2, Search, Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useProjectStore } from '@/store/projectStore';
import { useEditorStore } from '@/store/editorStore';

export interface PipelineLinkDialogProps {
  open: boolean;
  onClose: () => void;
  onLink: (projectId: string) => void;
}

type LinkMode = 'create' | 'existing';

export default function PipelineLinkDialog({ open, onClose, onLink }: PipelineLinkDialogProps) {
  const t = useTranslations('scriptEditor');
  const [mode, setMode] = useState<LinkMode>('existing');
  const [newProjectName, setNewProjectName] = useState('');
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  const projects = useProjectStore((s) => s.projects);
  const createProject = useProjectStore((s) => s.createProject);
  const setProjectId = useEditorStore((s) => s.setProjectId);

  const filteredProjects = useMemo(() => {
    if (!searchQuery.trim()) return projects;
    const q = searchQuery.toLowerCase();
    return projects.filter((p) => p.title.toLowerCase().includes(q));
  }, [projects, searchQuery]);

  const handleConfirm = async () => {
    if (mode === 'create') {
      if (!newProjectName.trim()) return;
      setIsCreating(true);
      try {
        await createProject(newProjectName.trim(), '', true);
        // After creation, the new project will be currentProject
        const current = useProjectStore.getState().currentProject;
        if (current) {
          setProjectId(current.id);
          onLink(current.id);
        }
      } finally {
        setIsCreating(false);
      }
    } else {
      if (!selectedProjectId) return;
      setProjectId(selectedProjectId);
      onLink(selectedProjectId);
    }
    onClose();
  };

  const canConfirm = mode === 'create' ? newProjectName.trim().length > 0 : selectedProjectId !== null;

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Dialog */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ type: 'spring', stiffness: 300, damping: 25 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
          >
            <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-zinc-900 shadow-2xl">
              {/* Header */}
              <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
                <h2 className="text-base font-semibold text-foreground">{t('dialogs.pipeline.title')}</h2>
                <button
                  type="button"
                  onClick={onClose}
                  className="text-text-muted hover:text-foreground transition-colors"
                >
                  <X size={18} />
                </button>
              </div>

              {/* Mode switch */}
              <div className="px-6 pt-4">
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setMode('existing')}
                    className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                      mode === 'existing'
                        ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                        : 'bg-zinc-800 text-text-muted hover:text-text-secondary border border-white/5'
                    }`}
                  >
                    <Link2 size={14} />
                    {t('dialogs.pipeline.linkExisting')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setMode('create')}
                    className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                      mode === 'create'
                        ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                        : 'bg-zinc-800 text-text-muted hover:text-text-secondary border border-white/5'
                    }`}
                  >
                    <FolderPlus size={14} />
                    {t('dialogs.pipeline.createNew')}
                  </button>
                </div>
              </div>

              {/* Content */}
              <div className="px-6 py-4">
                <AnimatePresence mode="wait">
                  {mode === 'existing' ? (
                    <motion.div
                      key="existing"
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0, x: 10 }}
                      transition={{ duration: 0.15 }}
                    >
                      {/* Search */}
                      <div className="relative mb-3">
                        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                        <input
                          type="text"
                          value={searchQuery}
                          onChange={(e) => setSearchQuery(e.target.value)}
                          placeholder={t('dialogs.pipeline.searchPlaceholder')}
                          className="w-full rounded-lg border border-white/10 bg-zinc-800 pl-9 pr-3 py-2 text-sm text-foreground placeholder:text-text-muted focus:border-indigo-500/50 focus:outline-none transition-colors"
                        />
                      </div>

                      {/* Project list */}
                      <div className="max-h-[240px] overflow-y-auto space-y-1 rounded-lg border border-white/5 bg-zinc-800/30 p-2">
                        {filteredProjects.length > 0 ? (
                          filteredProjects.map((project) => (
                            <button
                              key={project.id}
                              type="button"
                              onClick={() => setSelectedProjectId(project.id)}
                              className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors ${
                                selectedProjectId === project.id
                                  ? 'bg-indigo-600/20 text-indigo-200 border border-indigo-500/30'
                                  : 'text-text-secondary hover:bg-zinc-700/50 border border-transparent'
                              }`}
                            >
                              <div className="flex-1 min-w-0">
                                <p className="truncate font-medium">{project.title}</p>
                                <p className="text-xs text-text-muted truncate">
                                  {t('dialogs.pipeline.projectStats', { scenes: project.scenes?.length || 0, characters: project.characters?.length || 0 })}
                                </p>
                              </div>
                            </button>
                          ))
                        ) : (
                          <p className="py-6 text-center text-xs text-text-muted italic">
                            {searchQuery ? t('dialogs.pipeline.noMatch') : t('dialogs.pipeline.noProjects')}
                          </p>
                        )}
                      </div>
                    </motion.div>
                  ) : (
                    <motion.div
                      key="create"
                      initial={{ opacity: 0, x: 10 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0, x: -10 }}
                      transition={{ duration: 0.15 }}
                    >
                      <label className="block text-sm text-text-secondary mb-2">
                        {t('dialogs.pipeline.projectName')}
                      </label>
                      <input
                        type="text"
                        value={newProjectName}
                        onChange={(e) => setNewProjectName(e.target.value)}
                        placeholder={t('dialogs.pipeline.projectNamePlaceholder')}
                        className="w-full rounded-lg border border-white/10 bg-zinc-800 px-3 py-2.5 text-sm text-foreground placeholder:text-text-muted focus:border-indigo-500/50 focus:outline-none transition-colors"
                        autoFocus
                      />
                      <p className="mt-2 text-xs text-text-muted">
                        {t('dialogs.pipeline.createHint')}
                      </p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              {/* Footer */}
              <div className="flex items-center justify-end gap-3 border-t border-white/10 px-6 py-4">
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-lg px-4 py-2 text-sm text-text-muted hover:text-foreground transition-colors"
                >
                  {t('dialogs.pipeline.cancel')}
                </button>
                <button
                  type="button"
                  onClick={handleConfirm}
                  disabled={!canConfirm || isCreating}
                  className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {isCreating && <Loader2 size={14} className="animate-spin" />}
                  {mode === 'create' ? t('dialogs.pipeline.createAndLink') : t('dialogs.pipeline.confirmLink')}
                </button>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
