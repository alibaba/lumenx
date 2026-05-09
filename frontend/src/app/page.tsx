"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Plus, FolderOpen, RefreshCw, Library, Calendar, Play, Trash2, FileUp, X, ChevronDown, FileText,
  Zap, Film,
} from "lucide-react";
import { useProjectStore, Series, Project } from "@/store/projectStore";
import ProjectCard from "@/components/project/ProjectCard";
import CreateProjectDialog from "@/components/project/CreateProjectDialog";
import EnvConfigDialog from "@/components/project/EnvConfigDialog";
import CreativeCanvas from "@/components/canvas/CreativeCanvas";
import AppShell from "@/components/layout/AppShell";
import type { GlobalTab } from "@/components/layout/GlobalSidebar";
import dynamic from "next/dynamic";
import { api } from "@/lib/api";
import { useTranslations } from "next-intl";
import { useSettingsStore } from "@/store/settingsStore";

const ProjectClient = dynamic(() => import("@/components/project/ProjectClient"), { ssr: false });
const SeriesDetailPage = dynamic(() => import("@/components/series/SeriesDetailPage"), { ssr: false });
const ImportFileDialog = dynamic(() => import("@/components/series/ImportFileDialog"), { ssr: false });
const SettingsPage = dynamic(() => import("@/components/settings/SettingsPage"), { ssr: false });
const AssetLibraryPage = dynamic(() => import("@/components/library/AssetLibraryPage"), { ssr: false });
const AtelierShell = dynamic(() => import("@/components/atelier/AtelierShell"), { ssr: false });

// ── Create Series Dialog ──
function CreateSeriesDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [workflowMode, setWorkflowMode] = useState<"r2v" | "i2v_legacy">("r2v");
  const [isCreating, setIsCreating] = useState(false);
  const createSeries = useProjectStore((state) => state.createSeries);
  const t = useTranslations("workspace");
  const tc = useTranslations("common");
  const tp = useTranslations("project");

  if (!isOpen) return null;

  const handleCreate = async () => {
    if (!title.trim()) return;
    setIsCreating(true);
    try {
      const series = await createSeries(title.trim(), description.trim() || undefined, workflowMode);
      setTitle("");
      setDescription("");
      setWorkflowMode("r2v");
      onClose();
      window.location.hash = `#/series/${series.id}`;
    } catch (error) {
      console.error("Failed to create series:", error);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-overlay backdrop-blur-sm" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="bg-elevated border border-border rounded-2xl p-8 w-full max-w-4xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-2xl font-display font-bold text-foreground">{t("newSeries")}</h2>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-hover-bg transition-colors">
            <X size={20} className="text-text-secondary" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-foreground mb-2">{t("seriesTitle")} *</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("seriesTitlePlaceholder")}
              className="glass-input w-full"
              autoFocus
            />
          </div>

          {/* Workflow Mode */}
          <div>
            <label className="block text-sm font-medium text-foreground mb-2">{tp("workflowMode")}</label>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setWorkflowMode("r2v")}
                className={`relative p-4 rounded-xl border-2 text-left transition-all ${
                  workflowMode === "r2v"
                    ? "border-primary bg-primary/10"
                    : "border-border bg-surface hover:border-text-muted"
                }`}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <Zap size={16} className={workflowMode === "r2v" ? "text-primary" : "text-text-secondary"} />
                  <span className="font-semibold text-sm text-foreground">{tp("workflowR2V")}</span>
                </div>
                <p className="text-xs text-text-secondary leading-relaxed">{tp("workflowR2VDesc")}</p>
                {workflowMode === "r2v" && (
                  <span className="absolute top-2 right-2 text-[10px] font-medium text-primary bg-primary/20 px-1.5 py-0.5 rounded">
                    {tc("recommended")}
                  </span>
                )}
              </button>
              <button
                type="button"
                onClick={() => setWorkflowMode("i2v_legacy")}
                className={`relative p-4 rounded-xl border-2 text-left transition-all ${
                  workflowMode === "i2v_legacy"
                    ? "border-primary bg-primary/10"
                    : "border-border bg-surface hover:border-text-muted"
                }`}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <Film size={16} className={workflowMode === "i2v_legacy" ? "text-primary" : "text-text-secondary"} />
                  <span className="font-semibold text-sm text-foreground">{tp("workflowI2V")}</span>
                </div>
                <p className="text-xs text-text-secondary leading-relaxed">{tp("workflowI2VDesc")}</p>
              </button>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-2">{t("description")}</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("descriptionPlaceholder")}
              rows={4}
              className="glass-input w-full resize-none"
            />
          </div>
        </div>

        <div className="flex gap-3 pt-6">
          <button
            onClick={onClose}
            className="flex-1 glass-button"
          >
            {tc("cancel")}
          </button>
          <button
            onClick={handleCreate}
            disabled={!title.trim() || isCreating}
            className="flex-1 bg-primary hover:bg-primary/90 text-foreground px-6 py-3 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isCreating ? t("creating") : t("createSeries")}
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
  onDelete: (id: string) => void;
  episodes: Project[] | undefined;
  episodesLoading: boolean;
  onEpisodesChange: (seriesId: string) => void;
}) {
  const [inlineTitle, setInlineTitle] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [showInlineInput, setShowInlineInput] = useState(false);
  const t = useTranslations("workspace");
  const tc = useTranslations("common");
  const locale = useSettingsStore((s) => s.locale);

  const handleOpen = () => {
    window.location.hash = `#/series/${series.id}`;
  };

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirm(t("confirmDeleteSeries", { title: series.title }))) {
      onDelete(series.id);
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
              {t("series")}
            </span>
            <h3 className="text-lg font-display font-bold text-foreground">
              {series.title}
            </h3>
          </div>
          {series.description && (
            <p className="text-sm text-text-secondary mb-2 line-clamp-1">{series.description}</p>
          )}
          <div className="flex items-center gap-3 text-xs text-text-secondary">
            <span>{t("episodeLabel")} <span className="text-foreground font-medium">{series.episode_ids?.length || 0}</span></span>
            <span className="text-text-muted">·</span>
            <span>{t("characterLabel")} <span className="text-foreground font-medium">{series.characters?.length || 0}</span></span>
            <span className="text-text-muted">·</span>
            <span>{t("sceneLabel")} <span className="text-foreground font-medium">{series.scenes?.length || 0}</span></span>
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
                <div key={i} className="flex-shrink-0 w-28 h-16 rounded-lg bg-glass animate-pulse" />
              ))}
            </>
          ) : (
            <>
              {sortedEpisodes.map((ep) => (
                <button
                  key={ep.id}
                  onClick={() => { window.location.hash = `#/series/${series.id}/episode/${ep.id}`; }}
                  className="flex-shrink-0 w-28 p-2 rounded-lg bg-glass hover:bg-hover-bg border border-glass-border hover:border-glass-border transition-colors text-left"
                >
                  <span className="text-[10px] text-primary font-mono font-bold block">EP{ep.episode_number || "?"}</span>
                  <span className="text-xs text-foreground truncate block mt-0.5">{ep.title}</span>
                  <span className="text-[10px] text-text-muted block mt-0.5">{t("frames", { count: ep.frames?.length || 0 })}</span>
                </button>
              ))}

              {/* Inline add episode */}
              {showInlineInput ? (
                <div className="flex-shrink-0 w-36 p-2 rounded-lg bg-glass border border-primary/30 flex flex-col gap-1">
                  <input
                    type="text"
                    value={inlineTitle}
                    onChange={(e) => setInlineTitle(e.target.value)}
                    placeholder={t("episodeTitlePlaceholder")}
                    className="w-full bg-transparent border-none text-xs text-foreground placeholder-text-muted focus:outline-none"
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
                      {isAdding ? "..." : tc("confirm")}
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); setShowInlineInput(false); setInlineTitle(""); }}
                      className="text-[10px] text-text-muted hover:text-foreground transition-colors"
                    >
                      {tc("cancel")}
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={(e) => { e.stopPropagation(); setShowInlineInput(true); }}
                  className="flex-shrink-0 w-28 p-2 rounded-lg border border-dashed border-glass-border hover:border-text-muted bg-surface-inset hover:bg-glass transition-colors flex flex-col items-center justify-center gap-1"
                >
                  <Plus size={14} className="text-text-muted" />
                  <span className="text-[10px] text-text-muted">{t("addEpisode")}</span>
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between mt-3 pt-3 border-t border-glass-border">
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <Calendar size={12} />
          <span>{new Date(series.created_at * 1000).toLocaleDateString(locale === 'zh' ? 'zh-CN' : 'en-US')}</span>
        </div>
        <div className="flex items-center gap-1 text-primary text-xs font-medium">
          <Play size={14} />
          <span>{t("openSeries")}</span>
        </div>
      </div>
    </motion.div>
  );
}

// ── Episode Breadcrumb Wrapper ──
function EpisodeBreadcrumbWrapper({ seriesId, episodeId }: { seriesId: string; episodeId: string }) {
  const [seriesTitle, setSeriesTitle] = useState<string>("");
  const [episodeNumber, setEpisodeNumber] = useState<number | null>(null);
  const t = useTranslations("workspace");

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
    { label: "LumenX", hash: "#/" },
    { label: seriesTitle || t("series"), hash: `#/series/${seriesId}` },
    { label: episodeNumber != null ? t("episodeNum", { number: episodeNumber }) : t("episodeLabel") },
  ];

  return (
    <ProjectClient id={episodeId} breadcrumbSegments={segments} />
  );
}

// ── Main Component ──
export default function Home() {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isSeriesDialogOpen, setIsSeriesDialogOpen] = useState(false);
  const [isImportDialogOpen, setIsImportDialogOpen] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [showCreateDropdown, setShowCreateDropdown] = useState(false);
  const [currentView, setCurrentView] = useState<'home' | 'project' | 'series' | 'series-episode' | 'library' | 'settings' | 'atelier'>('home');
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
  const t = useTranslations("workspace");
  const tc = useTranslations("common");

  // Sync projects and series from backend on mount
  useEffect(() => {
    syncProjects();
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
      if (backendProjects && backendProjects.length > 0) {
        setProjects(backendProjects);
      }
    } catch (error) {
      console.error("Failed to sync projects from backend:", error);
    } finally {
      setIsSyncing(false);
    }
  };

  const syncAll = async () => {
    await Promise.all([syncProjects(), fetchSeriesList()]);
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
      if (hash === '#/atelier') {
        setCurrentView('atelier');
        setActiveTab('atelier');
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

  if (currentView === 'atelier') {
    return <AtelierShell />;
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
            <FolderOpen size={64} className="text-text-muted mb-4" />
            <h3 className="text-xl font-medium text-text-secondary mb-2">{t("empty")}</h3>
            <p className="text-text-muted mb-8">{t("emptyHint")}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-xl w-full">
              <button
                onClick={() => setIsSeriesDialogOpen(true)}
                className="glass-panel p-6 rounded-xl border border-blue-500/30 hover:border-blue-500/60 transition-all group text-left"
              >
                <Library size={32} className="text-blue-400 mb-3" />
                <h4 className="text-lg font-display font-bold text-foreground mb-1 group-hover:text-blue-400 transition-colors">{t("createSeries")}</h4>
                <p className="text-sm text-text-secondary">{t("createSeriesHint")}</p>
              </button>
              <button
                onClick={() => setIsDialogOpen(true)}
                className="glass-panel p-6 rounded-xl border border-glass-border hover:border-text-muted transition-all group text-left"
              >
                <FileText size={32} className="text-gray-400 mb-3" />
                <h4 className="text-lg font-display font-bold text-foreground mb-1 group-hover:text-primary transition-colors">{t("createProject")}</h4>
                <p className="text-sm text-text-secondary">{t("createProjectHint")}</p>
              </button>
            </div>
            <button
              onClick={syncAll}
              disabled={isSyncing}
              className="mt-6 bg-hover-bg hover:bg-glass text-foreground px-6 py-2.5 rounded-lg font-medium flex items-center gap-2 transition-colors disabled:opacity-50 text-sm"
            >
              <RefreshCw size={16} className={isSyncing ? "animate-spin" : ""} />
              {t("syncFromBackend")}
            </button>
          </motion.div>
        ) : (
          <>
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-2xl font-display font-bold text-foreground">
                {t("title")} ({totalCount})
              </h2>
              <div className="flex gap-3">
                <button
                  onClick={syncAll}
                  disabled={isSyncing}
                  className="bg-hover-bg hover:bg-glass text-foreground px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm disabled:opacity-50"
                >
                  <RefreshCw size={16} className={isSyncing ? "animate-spin" : ""} />
                  {tc("sync")}
                </button>
                <button
                  onClick={() => setIsImportDialogOpen(true)}
                  className="bg-hover-bg hover:bg-glass text-foreground px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm"
                >
                  <FileUp size={16} />
                  {t("importFile")}
                </button>
                {/* Unified create dropdown */}
                <div className="relative">
                  <button
                    onClick={(e) => { e.stopPropagation(); setShowCreateDropdown((v) => !v); }}
                    className="bg-primary hover:bg-primary/90 text-foreground px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm"
                  >
                    <Plus size={16} />
                    {t("new")}
                    <ChevronDown size={14} />
                  </button>
                  {showCreateDropdown && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }}
                      animate={{ opacity: 1, y: 0 }}
                      className="absolute right-0 top-full mt-1 w-48 bg-elevated border border-glass-border rounded-lg shadow-xl z-20 overflow-hidden"
                    >
                      <button
                        onClick={() => { setIsSeriesDialogOpen(true); setShowCreateDropdown(false); }}
                        className="w-full px-4 py-2.5 text-sm text-left text-foreground hover:bg-hover-bg transition-colors flex items-center gap-2"
                      >
                        <Library size={16} className="text-blue-400" />
                        {t("newSeries")}
                      </button>
                      <button
                        onClick={() => { setIsDialogOpen(true); setShowCreateDropdown(false); }}
                        className="w-full px-4 py-2.5 text-sm text-left text-foreground hover:bg-hover-bg transition-colors flex items-center gap-2"
                      >
                        <FileText size={16} className="text-gray-400" />
                        {t("newProject")}
                      </button>
                    </motion.div>
                  )}
                </div>
              </div>
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
    <main className="relative h-screen w-screen bg-background flex flex-col">
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

      {/* Environment Configuration Dialog (kept for EnvConfigChecker) */}
      <EnvConfigDialog
        isOpen={false}
        onClose={() => {}}
        isRequired={false}
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
