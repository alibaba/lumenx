"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Plus, FolderOpen, RefreshCw, Library, Calendar, Play, Trash2, FileUp, X, ChevronDown, FileText, Settings, Sparkles } from "lucide-react";
import { useProjectStore, Series, Project } from "@/store/projectStore";
import ProjectCard from "@/components/project/ProjectCard";
import CreateProjectDialog from "@/components/project/CreateProjectDialog";
import EnvConfigDialog from "@/components/project/EnvConfigDialog";
import CreativeCanvas from "@/components/canvas/CreativeCanvas";
import AppShell from "@/components/layout/AppShell";
import type { GlobalTab } from "@/components/layout/GlobalSidebar";
import dynamic from "next/dynamic";
import { api, type FixtureProjectSummary } from "@/lib/api";
import { defaultLocale, messages } from "@/lib/i18n";

const ProjectClient = dynamic(() => import("@/components/project/ProjectClient"), { ssr: false });
const SeriesDetailPage = dynamic(() => import("@/components/series/SeriesDetailPage"), { ssr: false });
const ImportFileDialog = dynamic(() => import("@/components/series/ImportFileDialog"), { ssr: false });
const SettingsPage = dynamic(() => import("@/components/settings/SettingsPage"), { ssr: false });
const AssetLibraryPage = dynamic(() => import("@/components/library/AssetLibraryPage"), { ssr: false });
const homeCopy = messages.homePage;
const commonActions = messages.common.actions;

// ── Create Series Dialog ──
function CreateSeriesDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const createSeries = useProjectStore((state) => state.createSeries);

  if (!isOpen) return null;

  const handleCreate = async () => {
    if (!title.trim()) return;
    setIsCreating(true);
    try {
      const series = await createSeries(title.trim(), description.trim() || undefined);
      setTitle("");
      setDescription("");
      onClose();
      window.location.hash = `#/series/${series.id}`;
    } catch (error) {
      console.error("Failed to create series:", error);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-md shadow-2xl"
      >
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-display font-bold text-white">{homeCopy.createSeriesDialog.title}</h2>
          <button onClick={onClose} className="p-1 hover:bg-white/10 rounded-lg transition-colors">
            <X size={20} className="text-gray-400" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">{homeCopy.createSeriesDialog.titleLabel}</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={homeCopy.createSeriesDialog.titlePlaceholder}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2.5 text-white placeholder-gray-500 focus:outline-none focus:border-primary transition-colors"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">{homeCopy.createSeriesDialog.descriptionLabel}</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={homeCopy.createSeriesDialog.descriptionPlaceholder}
              rows={3}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2.5 text-white placeholder-gray-500 focus:outline-none focus:border-primary transition-colors resize-none"
            />
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/10 transition-colors"
          >
            {commonActions.cancel}
          </button>
          <button
            onClick={handleCreate}
            disabled={!title.trim() || isCreating}
            className="px-4 py-2 rounded-lg bg-primary hover:bg-primary/90 text-white font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isCreating ? homeCopy.createSeriesDialog.creating : homeCopy.createSeriesDialog.create}
          </button>
        </div>
      </motion.div>
    </div>
  );
}

// ── Series Card (col-span-2 + episode preview strip) ──
function SeriesCard({
  series,
  onDelete,
  episodes,
  episodesLoading,
  onEpisodesChange,
}: {
  series: Series;
  onDelete: (id: string) => Promise<void> | void;
  episodes: Project[] | undefined;
  episodesLoading: boolean;
  onEpisodesChange: (seriesId: string) => void;
}) {
  const [inlineTitle, setInlineTitle] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [showInlineInput, setShowInlineInput] = useState(false);

  const handleOpen = () => {
    window.location.hash = `#/series/${series.id}`;
  };

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirm(homeCopy.seriesCard.deleteConfirm(series.title))) {
      Promise.resolve(onDelete(series.id)).catch((error) => {
        console.error("Failed to delete series:", error);
        alert(homeCopy.seriesCard.deleteFailed);
      });
    }
  };

  const handleInlineAddEpisode = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!inlineTitle.trim()) return;
    setIsAdding(true);
    try {
      const nextEpNum = (episodes?.length || 0) + 1;
      await api.createEpisodeForSeries(series.id, inlineTitle.trim(), nextEpNum);
      setInlineTitle("");
      setShowInlineInput(false);
      onEpisodesChange(series.id);
    } catch (error) {
      console.error("Failed to add episode inline:", error);
    } finally {
      setIsAdding(false);
    }
  };

  const sortedEpisodes = episodes
    ? [...episodes].sort((a, b) => (a.episode_number || 0) - (b.episode_number || 0))
    : [];

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ scale: 1.01 }}
      className="glass-panel p-6 rounded-xl cursor-pointer group relative border-l-2 border-l-blue-500"
      onClick={handleOpen}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs px-2 py-0.5 rounded bg-blue-500/20 text-blue-400 font-medium">
              {homeCopy.seriesCard.badge}
            </span>
            <h3 className="text-lg font-display font-bold text-white">
              {series.title}
            </h3>
          </div>
          {series.description && (
            <p className="text-sm text-gray-400 mb-2 line-clamp-1">{series.description}</p>
          )}
          <div className="flex items-center gap-3 text-xs text-gray-400">
            <span>{homeCopy.seriesCard.episodes} <span className="text-white font-medium">{series.episode_ids?.length || 0}</span></span>
            <span className="text-gray-600">·</span>
            <span>{homeCopy.seriesCard.characters} <span className="text-white font-medium">{series.characters?.length || 0}</span></span>
            <span className="text-gray-600">·</span>
            <span>{homeCopy.seriesCard.scenes} <span className="text-white font-medium">{series.scenes?.length || 0}</span></span>
          </div>
        </div>

        <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={handleDelete}
            className="p-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 transition-colors"
          >
            <Trash2 size={16} />
          </button>
        </div>
      </div>

      {/* Episode preview strip */}
      <div className="mt-4 -mx-1">
        <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-thin" onClick={(e) => e.stopPropagation()}>
          {episodesLoading ? (
            <>
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex-shrink-0 w-28 h-16 rounded-lg bg-white/5 animate-pulse" />
              ))}
            </>
          ) : (
            <>
              {sortedEpisodes.map((ep) => (
                <button
                  key={ep.id}
                  onClick={() => { window.location.hash = `#/series/${series.id}/episode/${ep.id}`; }}
                  className="flex-shrink-0 w-28 p-2 rounded-lg bg-white/5 hover:bg-white/10 border border-gray-700/50 hover:border-gray-500/50 transition-colors text-left"
                >
                  <span className="text-[10px] text-primary font-mono font-bold block">EP{ep.episode_number || "?"}</span>
                  <span className="text-xs text-white truncate block mt-0.5">{ep.title}</span>
                  <span className="text-[10px] text-gray-500 block mt-0.5">{homeCopy.seriesCard.storyboardCount(ep.frames?.length || 0)}</span>
                </button>
              ))}

              {/* Inline add episode */}
              {showInlineInput ? (
                <div className="flex-shrink-0 w-36 p-2 rounded-lg bg-white/5 border border-primary/30 flex flex-col gap-1">
                  <input
                    type="text"
                    value={inlineTitle}
                    onChange={(e) => setInlineTitle(e.target.value)}
                    placeholder={homeCopy.seriesCard.addEpisodeTitlePlaceholder}
                    className="w-full bg-transparent border-none text-xs text-white placeholder-gray-500 focus:outline-none"
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleInlineAddEpisode(e as unknown as React.MouseEvent);
                      if (e.key === "Escape") { setShowInlineInput(false); setInlineTitle(""); }
                    }}
                  />
                  <div className="flex gap-1">
                    <button
                      onClick={handleInlineAddEpisode}
                      disabled={!inlineTitle.trim() || isAdding}
                      className="flex-1 text-[10px] text-primary hover:text-white transition-colors disabled:opacity-50"
                    >
                      {isAdding ? "..." : homeCopy.seriesCard.confirmAddEpisode}
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); setShowInlineInput(false); setInlineTitle(""); }}
                      className="text-[10px] text-gray-500 hover:text-white transition-colors"
                    >
                      {commonActions.cancel}
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={(e) => { e.stopPropagation(); setShowInlineInput(true); }}
                  className="flex-shrink-0 w-28 p-2 rounded-lg border border-dashed border-gray-600 hover:border-gray-400 bg-white/[0.02] hover:bg-white/5 transition-colors flex flex-col items-center justify-center gap-1"
                >
                  <Plus size={14} className="text-gray-500" />
                  <span className="text-[10px] text-gray-500">{homeCopy.seriesCard.addEpisode}</span>
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between mt-3 pt-3 border-t border-gray-700/30">
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <Calendar size={12} />
          <span>{new Date(series.created_at * 1000).toLocaleDateString(defaultLocale)}</span>
        </div>
        <div className="flex items-center gap-1 text-primary text-xs font-medium">
          <Play size={14} />
          <span>{homeCopy.seriesCard.openSeries}</span>
        </div>
      </div>
    </motion.div>
  );
}

// ── Episode Breadcrumb Wrapper ──
function EpisodeBreadcrumbWrapper({ seriesId, episodeId }: { seriesId: string; episodeId: string }) {
  const [seriesTitle, setSeriesTitle] = useState<string>("");
  const [episodeNumber, setEpisodeNumber] = useState<number | null>(null);

  useEffect(() => {
    const fetchInfo = async () => {
      try {
        const series = await api.getSeries(seriesId);
        setSeriesTitle(series.title || "");
        const episodes = await api.getSeriesEpisodes(seriesId);
        const ep = episodes.find((e: Project) => e.id === episodeId);
        if (ep) {
          setEpisodeNumber(ep.episode_number ?? null);
        }
      } catch (error) {
        console.error("Failed to fetch series info for breadcrumb:", error);
      }
    };
    fetchInfo();
  }, [seriesId, episodeId]);

  const segments = [
    { label: homeCopy.breadcrumb.root, hash: "#/" },
    { label: seriesTitle || homeCopy.breadcrumb.seriesFallback, hash: `#/series/${seriesId}` },
    { label: episodeNumber != null ? homeCopy.breadcrumb.episodeNumber(episodeNumber) : homeCopy.breadcrumb.episodeFallback },
  ];

  return (
    <ProjectClient id={episodeId} breadcrumbSegments={segments} />
  );
}

function FixtureProjectLibrary({
  fixtures,
  isLoading,
  importingSlug,
  onImport,
}: {
  fixtures: FixtureProjectSummary[];
  isLoading: boolean;
  importingSlug: string | null;
  onImport: (slug: string) => void;
}) {
  const copy = homeCopy.fixtureLibrary;

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {[1, 2, 3].map((item) => (
          <div key={item} className="h-44 rounded-xl border border-white/10 bg-white/[0.03] animate-pulse" />
        ))}
      </div>
    );
  }

  if (fixtures.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-700 bg-white/[0.02] p-5 text-sm text-gray-500">
        {copy.empty}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {fixtures.map((fixture) => {
        const isImporting = importingSlug === fixture.slug;
        const modelLabel =
          fixture.model_settings?.openai_image_model ||
          fixture.model_settings?.t2i_model ||
          copy.modelFallback;

        return (
          <motion.button
            key={fixture.slug}
            type="button"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            whileHover={{ scale: 1.01 }}
            onClick={() => onImport(fixture.slug)}
            disabled={Boolean(importingSlug)}
            className="group rounded-xl border border-amber-500/25 bg-amber-500/[0.05] p-5 text-left transition-all hover:border-amber-400/60 hover:bg-amber-500/[0.08] disabled:cursor-wait disabled:opacity-60"
            data-testid={`fixture-project-card-${fixture.slug}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-amber-400/15 px-2 py-0.5 text-[11px] font-medium text-amber-200">
                    {copy.badge}
                  </span>
                  {fixture.is_imported && (
                    <span className="rounded-full bg-green-400/10 px-2 py-0.5 text-[11px] font-medium text-green-300">
                      {copy.imported}
                    </span>
                  )}
                </div>
                <h4 className="text-lg font-display font-bold text-white transition-colors group-hover:text-amber-100">
                  {fixture.name}
                </h4>
              </div>
              <Sparkles size={22} className={isImporting ? "animate-pulse text-amber-200" : "text-amber-300"} />
            </div>

            <p className="mt-3 line-clamp-3 text-sm leading-6 text-gray-400">
              {fixture.description}
            </p>

            <div className="mt-4 flex flex-wrap gap-2 text-[11px] text-gray-300">
              <span className="rounded bg-white/5 px-2 py-1">{copy.frames(fixture.frame_count)}</span>
              <span className="rounded bg-white/5 px-2 py-1">{copy.references(fixture.reference_count)}</span>
              <span className="rounded bg-white/5 px-2 py-1">{modelLabel}</span>
            </div>

            <div className="mt-4 flex items-center gap-1 text-sm font-medium text-amber-100">
              <Play size={14} />
              <span>{isImporting ? copy.importing : fixture.is_imported ? copy.openImported : copy.importAndOpen}</span>
            </div>
          </motion.button>
        );
      })}
    </div>
  );
}

// ── Main Component ──
export default function Home() {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isSeriesDialogOpen, setIsSeriesDialogOpen] = useState(false);
  const [isImportDialogOpen, setIsImportDialogOpen] = useState(false);
  const [isEnvDialogOpen, setIsEnvDialogOpen] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [fixtures, setFixtures] = useState<FixtureProjectSummary[]>([]);
  const [isLoadingFixtures, setIsLoadingFixtures] = useState(false);
  const [importingFixtureSlug, setImportingFixtureSlug] = useState<string | null>(null);
  const [showCreateDropdown, setShowCreateDropdown] = useState(false);
  const [currentView, setCurrentView] = useState<'home' | 'project' | 'series' | 'series-episode' | 'library' | 'settings'>('home');
  const [activeTab, setActiveTab] = useState<GlobalTab>("workspace");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [seriesId, setSeriesId] = useState<string | null>(null);
  const [episodeId, setEpisodeId] = useState<string | null>(null);
  const [seriesEpisodes, setSeriesEpisodes] = useState<Record<string, Project[]>>({});
  const [episodesLoading, setEpisodesLoading] = useState(false);
  const projects = useProjectStore((state) => state.projects);
  const seriesList = useProjectStore((state) => state.seriesList);
  const deleteProject = useProjectStore((state) => state.deleteProject);
  const deleteSeries = useProjectStore((state) => state.deleteSeries);
  const setProjects = useProjectStore((state) => state.setProjects);
  const fetchSeriesList = useProjectStore((state) => state.fetchSeriesList);

  // Sync projects and series from backend on mount
  useEffect(() => {
    syncProjects();
    loadFixtureLibrary();
    fetchSeriesList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load episodes for all series when seriesList changes
  useEffect(() => {
    if (seriesList.length === 0) return;
    loadAllSeriesEpisodes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesList]);

  const loadAllSeriesEpisodes = async () => {
    setEpisodesLoading(true);
    try {
      const results = await Promise.all(
        seriesList.map(async (s) => {
          const eps = await api.getSeriesEpisodes(s.id);
          return [s.id, eps] as const;
        })
      );
      const map: Record<string, Project[]> = {};
      for (const [id, eps] of results) {
        map[id] = eps;
      }
      setSeriesEpisodes(map);
    } catch (error) {
      console.error("Failed to load series episodes:", error);
    } finally {
      setEpisodesLoading(false);
    }
  };

  const refreshSeriesEpisodes = async (sid: string) => {
    try {
      const eps = await api.getSeriesEpisodes(sid);
      setSeriesEpisodes((prev) => ({ ...prev, [sid]: eps }));
    } catch (error) {
      console.error("Failed to refresh series episodes:", error);
    }
  };

  const syncProjects = async () => {
    setIsSyncing(true);
    try {
      const backendProjects = await api.getProjects();
      const nextProjects = Array.isArray(backendProjects) ? backendProjects : [];
      setProjects(nextProjects);

      const currentProject = useProjectStore.getState().currentProject;
      if (currentProject) {
        const matchedProject = nextProjects.find((project) => project.id === currentProject.id) ?? null;
        useProjectStore.setState({ currentProject: matchedProject });
      }
    } catch (error) {
      console.error("Failed to sync projects from backend:", error);
    } finally {
      setIsSyncing(false);
    }
  };

  const loadFixtureLibrary = async () => {
    setIsLoadingFixtures(true);
    try {
      const items = await api.listFixtureProjects();
      setFixtures(items);
    } catch (error) {
      console.error("Failed to load fixture project library:", error);
      setFixtures([]);
    } finally {
      setIsLoadingFixtures(false);
    }
  };

  const syncAll = async () => {
    await Promise.all([syncProjects(), fetchSeriesList(), loadFixtureLibrary()]);
  };

  const openFixtureProject = async (fixtureSlug: string) => {
    setImportingFixtureSlug(fixtureSlug);
    try {
      const project = await api.importFixtureProject(fixtureSlug);
      await Promise.all([syncProjects(), loadFixtureLibrary()]);
      window.location.hash = `#/project/${project.id}`;
    } catch (error) {
      console.error("Failed to import fixture project:", error);
      alert(homeCopy.fixtureLibrary.importFailed);
    } finally {
      setImportingFixtureSlug(null);
    }
  };

  // Close dropdown when clicking outside
  useEffect(() => {
    if (!showCreateDropdown) return;
    const handleClick = () => setShowCreateDropdown(false);
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, [showCreateDropdown]);

  // 监听 hash 变化
  useEffect(() => {
    const handleHashChange = () => {
      const hash = window.location.hash;
      // Match #/series/{id}/episode/{eid} first (more specific)
      const seriesEpisodeMatch = hash.match(/^#\/series\/([^/]+)\/episode\/([^/]+)$/);
      if (seriesEpisodeMatch) {
        setSeriesId(seriesEpisodeMatch[1]);
        setEpisodeId(seriesEpisodeMatch[2]);
        setProjectId(null);
        setCurrentView('series-episode');
        return;
      }
      // Match #/series/{id}
      const seriesMatch = hash.match(/^#\/series\/([^/]+)$/);
      if (seriesMatch) {
        setSeriesId(seriesMatch[1]);
        setEpisodeId(null);
        setProjectId(null);
        setCurrentView('series');
        return;
      }
      if (hash.startsWith('#/project/')) {
        const id = hash.replace('#/project/', '');
        setProjectId(id);
        setSeriesId(null);
        setEpisodeId(null);
        setCurrentView('project');
        return;
      }
      if (hash === '#/library') {
        setCurrentView('library');
        setActiveTab('library');
        setProjectId(null);
        setSeriesId(null);
        setEpisodeId(null);
        return;
      }
      if (hash === '#/settings') {
        setCurrentView('settings');
        setActiveTab('settings');
        setProjectId(null);
        setSeriesId(null);
        setEpisodeId(null);
        return;
      }
      // Default: workspace
      setCurrentView('home');
      setActiveTab('workspace');
      setProjectId(null);
      setSeriesId(null);
      setEpisodeId(null);
    };

    handleHashChange();
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  // 项目详情页 — 全屏，无 GlobalSidebar
  if (currentView === 'project' && projectId) {
    return <ProjectClient id={projectId} />;
  }

  // 系列集数编辑 — 全屏，BreadcrumbBar 内嵌在 ProjectClient
  if (currentView === 'series-episode' && seriesId && episodeId) {
    return <EpisodeBreadcrumbWrapper seriesId={seriesId} episodeId={episodeId} />;
  }

  // 系列详情页 — 全屏，自带 BreadcrumbBar
  if (currentView === 'series' && seriesId) {
    return <SeriesDetailPage seriesId={seriesId} />;
  }

  // Filter standalone projects (not belonging to any series)
  const standaloneProjects = projects.filter((p) => !p.series_id);

  // Build mixed list: series + standalone projects, sorted by creation time descending
  type ListItem = { type: 'series'; data: Series; sortTime: number } | { type: 'project'; data: Project; sortTime: number };
  const mixedList: ListItem[] = [
    ...seriesList.map((s) => ({ type: 'series' as const, data: s, sortTime: s.created_at * 1000 })),
    ...standaloneProjects.map((p) => ({ type: 'project' as const, data: p, sortTime: new Date(p.createdAt).getTime() })),
  ].sort((a, b) => b.sortTime - a.sortTime);

  const totalCount = mixedList.length;

  const handleTabChange = (tab: GlobalTab) => {
    setActiveTab(tab);
  };

  // Determine content based on activeTab
  const renderContent = () => {
    if (currentView === 'library') {
      return <AssetLibraryPage />;
    }
    if (currentView === 'settings') {
      return <SettingsPage />;
    }

    // Workspace view
    return (
      <div className="container mx-auto px-6 py-8">
        {/* Content Section */}
        {totalCount === 0 ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex flex-col items-center justify-center py-20"
          >
            <FolderOpen size={64} className="text-gray-600 mb-4" />
            <h3 className="text-xl font-medium text-gray-400 mb-2">{homeCopy.workspace.emptyTitle}</h3>
            <p className="text-gray-500 mb-8">{homeCopy.workspace.emptySubtitle}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-3xl w-full">
              <button
                type="button"
                onClick={() => setIsSeriesDialogOpen(true)}
                className="glass-panel p-6 rounded-xl border border-blue-500/30 hover:border-blue-500/60 transition-all group text-left"
              >
                <Library size={32} className="text-blue-400 mb-3" />
                <h4 className="text-lg font-display font-bold text-white mb-1 group-hover:text-blue-400 transition-colors">{homeCopy.workspace.createSeriesTitle}</h4>
                <p className="text-sm text-gray-400">{homeCopy.workspace.createSeriesDescription}</p>
              </button>
              <button
                type="button"
                onClick={() => setIsDialogOpen(true)}
                className="glass-panel p-6 rounded-xl border border-gray-600/30 hover:border-gray-500/60 transition-all group text-left"
                data-testid="home-empty-create-project-card"
              >
                <FileText size={32} className="text-gray-400 mb-3" />
                <h4 className="text-lg font-display font-bold text-white mb-1 group-hover:text-primary transition-colors">{homeCopy.workspace.createProjectTitle}</h4>
                <p className="text-sm text-gray-400">{homeCopy.workspace.createProjectDescription}</p>
              </button>
            </div>
            <div className="mt-10 w-full max-w-5xl text-left">
              <div className="mb-4 flex items-center justify-between gap-4">
                <div>
                  <h3 className="text-lg font-display font-bold text-white">{homeCopy.fixtureLibrary.title}</h3>
                  <p className="mt-1 text-sm text-gray-500">{homeCopy.fixtureLibrary.subtitle}</p>
                </div>
                <button
                  type="button"
                  onClick={loadFixtureLibrary}
                  disabled={isLoadingFixtures}
                  className="rounded-lg bg-white/10 px-3 py-2 text-xs font-medium text-gray-200 transition-colors hover:bg-white/20 disabled:opacity-50"
                >
                  {homeCopy.fixtureLibrary.refresh}
                </button>
              </div>
              <FixtureProjectLibrary
                fixtures={fixtures}
                isLoading={isLoadingFixtures}
                importingSlug={importingFixtureSlug}
                onImport={openFixtureProject}
              />
            </div>
            <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
              <button
                type="button"
                onClick={setIsEnvDialogOpen.bind(null, true)}
                className="bg-white/10 hover:bg-white/20 text-white px-6 py-2.5 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm"
              >
                <Settings size={16} />
                {homeCopy.workspace.homeSettings}
              </button>
              <button
                type="button"
                onClick={syncAll}
                disabled={isSyncing}
                className="bg-white/10 hover:bg-white/20 text-white px-6 py-2.5 rounded-lg font-medium flex items-center gap-2 transition-colors disabled:opacity-50 text-sm"
              >
                <RefreshCw size={16} className={isSyncing ? "animate-spin" : ""} />
                {homeCopy.workspace.syncFromBackend}
              </button>
            </div>
          </motion.div>
        ) : (
          <>
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-2xl font-display font-bold text-white">
                {homeCopy.workspace.title(totalCount)}
              </h2>
              <div className="flex gap-3">
                <button
                  onClick={() => setIsEnvDialogOpen(true)}
                  className="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm"
                >
                  <Settings size={16} />
                  {homeCopy.workspace.settings}
                </button>
                <button
                  onClick={syncAll}
                  disabled={isSyncing}
                  className="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm disabled:opacity-50"
                >
                  <RefreshCw size={16} className={isSyncing ? "animate-spin" : ""} />
                  {homeCopy.workspace.sync}
                </button>
                <button
                  onClick={() => setIsImportDialogOpen(true)}
                  className="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm"
                >
                  <FileUp size={16} />
                  {homeCopy.workspace.importFile}
                </button>
                {/* Unified create dropdown */}
                <div className="relative">
                  <button
                    onClick={(e) => { e.stopPropagation(); setShowCreateDropdown((v) => !v); }}
                    className="bg-primary hover:bg-primary/90 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm"
                    data-testid="home-create-dropdown-toggle"
                  >
                    <Plus size={16} />
                    {homeCopy.workspace.create}
                    <ChevronDown size={14} />
                  </button>
                  {showCreateDropdown && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="absolute right-0 top-full mt-1 w-48 bg-gray-800 border border-gray-600 rounded-lg shadow-xl z-20 overflow-hidden"
                    >
                      <button
                        onClick={() => { setIsSeriesDialogOpen(true); setShowCreateDropdown(false); }}
                        className="w-full px-4 py-2.5 text-sm text-left text-white hover:bg-white/10 transition-colors flex items-center gap-2"
                      >
                        <Library size={16} className="text-blue-400" />
                        {homeCopy.workspace.createSeries}
                      </button>
                      <button
                        onClick={() => { setIsDialogOpen(true); setShowCreateDropdown(false); }}
                        className="w-full px-4 py-2.5 text-sm text-left text-white hover:bg-white/10 transition-colors flex items-center gap-2"
                        data-testid="home-create-project-option"
                      >
                        <FileText size={16} className="text-gray-400" />
                        {homeCopy.workspace.createProject}
                      </button>
                    </motion.div>
                  )}
                </div>
              </div>
            </div>

            <div className="mb-8">
              <div className="mb-4 flex items-center justify-between gap-4">
                <div>
                  <h3 className="text-lg font-display font-bold text-white">{homeCopy.fixtureLibrary.title}</h3>
                  <p className="mt-1 text-sm text-gray-500">{homeCopy.fixtureLibrary.subtitle}</p>
                </div>
                <button
                  onClick={loadFixtureLibrary}
                  disabled={isLoadingFixtures}
                  className="rounded-lg bg-white/10 px-3 py-2 text-xs font-medium text-gray-200 transition-colors hover:bg-white/20 disabled:opacity-50"
                >
                  {homeCopy.fixtureLibrary.refresh}
                </button>
              </div>
              <FixtureProjectLibrary
                fixtures={fixtures}
                isLoading={isLoadingFixtures}
                importingSlug={importingFixtureSlug}
                onImport={openFixtureProject}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6 pb-12">
              {mixedList.map((item, i) => (
                <motion.div
                  key={item.type === 'series' ? `s-${item.data.id}` : `p-${(item.data as Project).id}`}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: Math.min(i * 0.03, 0.3) }}
                  className={item.type === 'series' ? 'col-span-1 md:col-span-2' : ''}
                >
                  {item.type === 'series' ? (
                    <SeriesCard
                      series={item.data as Series}
                      onDelete={deleteSeries}
                      episodes={seriesEpisodes[(item.data as Series).id]}
                      episodesLoading={episodesLoading}
                      onEpisodesChange={refreshSeriesEpisodes}
                    />
                  ) : (
                    <ProjectCard project={item.data as Project} onDelete={deleteProject} />
                  )}
                </motion.div>
              ))}
            </div>
          </>
        )}
      </div>
    );
  };

  return (
    <main className="relative h-screen w-screen bg-background flex flex-col" data-testid="lumenx-home">
      {/* Background Canvas */}
      <div className="fixed inset-0 z-0 pointer-events-none">
        <CreativeCanvas />
      </div>

      {/* AppShell with GlobalSidebar + content */}
      <div className="relative z-10 flex-1 overflow-hidden">
        <AppShell activeTab={activeTab} onTabChange={handleTabChange}>
          {renderContent()}
        </AppShell>
      </div>

      {/* Create Project Dialog */}
      <CreateProjectDialog
        isOpen={isDialogOpen}
        onClose={() => setIsDialogOpen(false)}
      />

      {/* Create Series Dialog */}
      <CreateSeriesDialog
        isOpen={isSeriesDialogOpen}
        onClose={() => setIsSeriesDialogOpen(false)}
      />

      <EnvConfigDialog
        isOpen={isEnvDialogOpen}
        onClose={() => setIsEnvDialogOpen(false)}
      />

      {/* Import File Dialog */}
      <ImportFileDialog
        isOpen={isImportDialogOpen}
        onClose={() => setIsImportDialogOpen(false)}
        onSuccess={() => fetchSeriesList()}
      />
    </main>
  );
}
