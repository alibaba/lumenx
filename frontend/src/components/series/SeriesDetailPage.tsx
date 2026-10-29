"use client";

import { useState, useEffect, useRef, useMemo, useCallback, useSyncExternalStore } from "react";
import dynamic from "next/dynamic";
import { motion, AnimatePresence } from "framer-motion";
import { Image as ImageIcon, Play, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";
import type { Series, Character, Scene, Prop, Project } from "@/store/projectStore";
import AssetCard from "@/components/common/AssetCard";
import { useTranslations } from "next-intl";
import SeriesSidebar, { type SidebarItem } from "./SeriesSidebar";
import { persistenceFailure, type PersistenceFailure } from "@/lib/persistenceFeedback";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import {
  beginSeriesEpisodeRequest, readSeriesEpisodeRequest, resolveSeriesEpisodeRequest, SeriesEpisodeJournalError,
  type CreateSeriesEpisodeRequest, type SeriesEpisodeCreationReceipt,
} from "@/lib/seriesEpisodeRequest";

const SeriesModelSettingsModal = dynamic(() => import("./SeriesModelSettingsModal"), { ssr: false });
const SeriesPromptConfigModal = dynamic(() => import("./SeriesPromptConfigModal"), { ssr: false });
const ImportAssetsDialog = dynamic(() => import("./ImportAssetsDialog"), { ssr: false });
const SeriesArtDirectionPanel = dynamic(() => import("./SeriesArtDirectionPanel"), { ssr: false });

interface SeriesDetailPageProps {
  seriesId: string;
}

type AssetTab = "characters" | "scenes" | "props";
const responseStatus = (error: unknown) => (error as { response?: { status?: number } } | null)?.response?.status;

export default function SeriesDetailPage({ seriesId }: SeriesDetailPageProps) {
  const [series, setSeries] = useState<Series | null>(null);
  const [episodes, setEpisodes] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeItem, setActiveItem] = useState<SidebarItem>({ kind: "asset", tab: "characters" });
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [titleRevision, setTitleRevision] = useState<number>();
  const [titleError, setTitleError] = useState<PersistenceFailure | null>(null);
  const [titleSaving, setTitleSaving] = useState(false);
  const titleRequests = useRef(new Set<object>());
  const environment = useSyncExternalStore(subscribeRuntimeEnvironment, getRuntimeEnvironment, () => null);
  const view = useMemo(() => ({ seriesId, environment }), [seriesId, environment]);
  const episodeOwner = useMemo(() => environment ? { environment, seriesId } : null, [environment, seriesId]);
  const activeView = useRef<typeof view | null>(view);
  activeView.current = view;
  const latestSeries = useRef<Series | null>(null);
  const [showAddEpisode, setShowAddEpisode] = useState(false);
  const [newEpisodeTitle, setNewEpisodeTitle] = useState("");
  const [isCreatingEpisode, setIsCreatingEpisode] = useState(false);
  const [episodeError, setEpisodeError] = useState<string | null>(null);
  const [episodePending, setEpisodePending] = useState(false);
  const [episodeCanRetry, setEpisodeCanRetry] = useState(false);
  const [episodeConfirmed, setEpisodeConfirmed] = useState(false);
  const episodeOperation = useRef<object | null>(null);
  const pendingEpisode = useRef<Readonly<CreateSeriesEpisodeRequest> | null>(null);
  const confirmedEpisode = useRef<SeriesEpisodeCreationReceipt | null>(null);
  const [showModelSettings, setShowModelSettings] = useState(false);
  const [showPromptConfig, setShowPromptConfig] = useState(false);
  const [showImportAssets, setShowImportAssets] = useState(false);

  const t = useTranslations("series");
  const tc = useTranslations("common");
  const tp = useTranslations("persistence");

  const ASSET_LABELS: Record<AssetTab, string> = {
    characters: t("characterLabel"),
    scenes: t("sceneLabel"),
    props: t("propLabel"),
  };

  const acceptSeriesSnapshot = useCallback((snapshot: Series) => {
    if (activeView.current !== view || snapshot.id !== seriesId) return false;
    if (latestSeries.current?.id === seriesId && latestSeries.current.revision > snapshot.revision) return false;
    latestSeries.current = snapshot;
    setSeries(snapshot);
    return true;
  }, [seriesId, view]);

  const runEpisodeRequest = useCallback(async (allowPost: boolean, draft?: { title: string; revision: number }) => {
    if (!episodeOwner || activeView.current !== view || episodeOperation.current
      || !isCurrentRuntimeEnvironment(episodeOwner.environment.key, episodeOwner.environment.generation)) return;
    const operation = {};
    episodeOperation.current = operation;
    const stillCurrent = () => activeView.current === view && episodeOperation.current === operation
      && isCurrentRuntimeEnvironment(episodeOwner.environment.key, episodeOwner.environment.generation);
    const reload = async () => {
      const [saved, savedEpisodes] = await Promise.all([api.getSeries(seriesId), api.getSeriesEpisodes(seriesId)]);
      if (!stillCurrent()) return null;
      if (!Array.isArray(savedEpisodes) || !acceptSeriesSnapshot(saved)) throw new Error('Invalid series snapshot');
      setEpisodes(savedEpisodes);
      return savedEpisodes as Project[];
    };
    setEpisodeError(null);
    try {
      const existing = pendingEpisode.current ?? readSeriesEpisodeRequest(episodeOwner);
      if (!existing && !draft) return;
      const request = existing ?? beginSeriesEpisodeRequest(episodeOwner, draft!.title, draft!.revision);
      pendingEpisode.current = request;
      setEpisodePending(true);
      setShowAddEpisode(true);
      setNewEpisodeTitle(request.title);
      setIsCreatingEpisode(true);
      let receipt = confirmedEpisode.current;
      if (!receipt && existing) {
        try {
          receipt = await api.getSeriesEpisodeRequest(seriesId, request.client_request_id);
          if (!stillCurrent()) return;
        } catch (error) {
          if (!stillCurrent()) return;
          if (responseStatus(error) !== 404) throw error;
          if (!allowPost) {
            setEpisodeCanRetry(true);
            setEpisodeError(t('episodeNotConfirmed'));
            return;
          }
        }
      }
      if (!receipt) {
        try {
          receipt = await api.createEpisodeForSeries(seriesId, request);
          if (!stillCurrent()) return;
        } catch (error) {
          if (!stillCurrent()) return;
          try {
            receipt = await api.getSeriesEpisodeRequest(seriesId, request.client_request_id);
            if (!stillCurrent()) return;
          } catch (readError) {
            if (!stillCurrent()) return;
            const absent = responseStatus(readError) === 404;
            if (absent && [409, 422].includes(responseStatus(error) ?? 0)) {
              // The server explicitly rejected this identity and a read found no receipt.
              resolveSeriesEpisodeRequest(episodeOwner, request.client_request_id);
              pendingEpisode.current = null;
              setEpisodePending(false);
              setEpisodeCanRetry(false);
              await reload();
              if (stillCurrent()) setEpisodeError(t('episodeChanged'));
            } else {
              setEpisodeCanRetry(absent);
              setEpisodeError(t('episodeNotConfirmed'));
            }
            return;
          }
        }
      }
      if (receipt.title !== request.title) throw new Error('Episode receipt does not match the original request');
      confirmedEpisode.current = receipt;
      setEpisodeConfirmed(true);
      const savedEpisodes = await reload();
      if (!stillCurrent() || !savedEpisodes) return;
      resolveSeriesEpisodeRequest(episodeOwner, request.client_request_id);
      pendingEpisode.current = null;
      confirmedEpisode.current = null;
      setEpisodePending(false);
      setEpisodeConfirmed(false);
      setEpisodeCanRetry(false);
      if (!savedEpisodes.some(episode => episode.id === receipt.episode_id && episode.series_id === seriesId)) {
        setEpisodeError(t('episodeNoLongerLinked'));
      } else {
        setNewEpisodeTitle('');
        setShowAddEpisode(false);
      }
    } catch (error) {
      if (!stillCurrent()) return;
      setShowAddEpisode(true);
      setEpisodeError(error instanceof SeriesEpisodeJournalError
        ? t(error.code === 'corrupt' ? 'episodeJournalCorrupt' : 'episodeJournalUnavailable')
        : t(confirmedEpisode.current ? 'episodeCreatedRefreshFailed' : 'episodeNotConfirmed'));
    } finally {
      if (stillCurrent()) {
        episodeOperation.current = null;
        setIsCreatingEpisode(false);
      }
    }
  }, [episodeOwner, view, seriesId, acceptSeriesSnapshot, t]);

  useEffect(() => {
    let cancelled = false;
    activeView.current = view;
    latestSeries.current = null;
    setSeries(null);
    setEpisodes([]);
    setIsEditingTitle(false);
    setTitleError(null);
    setTitleSaving(titleRequests.current.has(view));
    episodeOperation.current = null;
    pendingEpisode.current = null;
    confirmedEpisode.current = null;
    setShowAddEpisode(false);
    setNewEpisodeTitle('');
    setIsCreatingEpisode(false);
    setEpisodeError(null);
    setEpisodePending(false);
    setEpisodeCanRetry(false);
    setEpisodeConfirmed(false);
    const fetchData = async () => {
      setLoading(true);
      try {
        const [seriesData, episodesData] = await Promise.all([
          api.getSeries(seriesId),
          api.getSeriesEpisodes(seriesId),
        ]);
        if (cancelled) return;
        if (!acceptSeriesSnapshot(seriesData)) return;
        setEpisodes(episodesData);
        setEditTitle(seriesData.title);
        void runEpisodeRequest(false);
      } catch (error) {
        console.error("Failed to fetch series data:", error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchData();
    return () => {
      cancelled = true;
      if (activeView.current === view) activeView.current = null;
    };
  }, [seriesId, view, acceptSeriesSnapshot, runEpisodeRequest]);

  const handleBackToHome = () => {
    window.location.hash = "";
  };

  const handleTitleSave = async (retry = false) => {
    if (!editTitle.trim() || !series || titleRequests.current.has(view) || titleError === "conflict" || (titleError && !retry)) return;
    if (editTitle.trim() === series.title) { setIsEditingTitle(false); return; }
    if (!Number.isSafeInteger(titleRevision) || titleRevision! < 0) { setTitleError("missingRevision"); return; }
    titleRequests.current.add(view);
    setTitleSaving(true);
    setTitleError(null);
    try {
      const saved = await api.updateSeries(seriesId, { title: editTitle.trim(), expected_revision: titleRevision! });
      if (activeView.current !== view) return;
      acceptSeriesSnapshot(saved);
      setTitleRevision(saved.revision);
      setEditTitle(saved.title);
      setIsEditingTitle(false);
    } catch (error) {
      if (activeView.current === view) setTitleError(persistenceFailure(error));
    } finally {
      titleRequests.current.delete(view);
      if (activeView.current === view) setTitleSaving(false);
    }
  };

  const handleTitleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") void handleTitleSave(true);
    if (e.key === "Escape") {
      setEditTitle(series?.title || "");
      setIsEditingTitle(false);
      setTitleError(null);
    }
  };

  const handleAddEpisode = async () => {
    if (!newEpisodeTitle.trim() || !series) return;
    if (!Number.isSafeInteger(series.revision) || series.revision < 0) {
      setEpisodeError(tp('missingRevision'));
      return;
    }
    await runEpisodeRequest(!episodePending || episodeCanRetry, { title: newEpisodeTitle, revision: series.revision });
  };

  const handleAddEpisodeKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleAddEpisode();
    if (e.key === "Escape" && !episodePending && !isCreatingEpisode) setShowAddEpisode(false);
  };

  const handleOpenEpisode = (episodeId: string) => {
    window.location.hash = `#/series/${seriesId}/episode/${episodeId}`;
  };

  const refreshSeriesData = async () => {
    try {
      const [seriesData, episodesData] = await Promise.all([
        api.getSeries(seriesId),
        api.getSeriesEpisodes(seriesId),
      ]);
      if (!acceptSeriesSnapshot(seriesData)) return;
      setEpisodes(episodesData);
    } catch (error) {
      console.error("Failed to refresh series data:", error);
    }
  };

  // ── Loading ──
  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-background">
        <div className="text-text-secondary">{tc("loading")}</div>
      </div>
    );
  }

  // ── Error ──
  if (!series) {
    return (
      <div className="flex items-center justify-center h-screen bg-background">
        <div className="text-center">
          <p className="text-text-secondary mb-4">{t("notFound")}</p>
          <a href="#/" className="text-primary hover:underline">{t("backToHome")}</a>
        </div>
      </div>
    );
  }

  // ── Derive content ──
  const getAssets = (tab: AssetTab): (Character | Scene | Prop)[] => {
    if (tab === "characters") return series.characters || [];
    if (tab === "scenes") return series.scenes || [];
    return series.props || [];
  };

  const selectedEpisode =
    activeItem.kind === "episode"
      ? episodes.find((ep) => ep.id === activeItem.episodeId)
      : null;

  return (
    <main className="flex h-screen w-screen bg-background overflow-hidden">
      {/* ── Sidebar ── */}
      <SeriesSidebar
        series={series}
        episodes={episodes}
        activeItem={activeItem}
        onItemChange={setActiveItem}
        onBack={handleBackToHome}
        isEditingTitle={isEditingTitle}
        editTitle={editTitle}
        titleSaving={titleSaving}
        titleError={titleError ? tp(titleError) : undefined}
        onEditTitleChange={setEditTitle}
        onTitleDoubleClick={() => { setEditTitle(series.title); setTitleRevision(series.revision); setTitleError(null); setIsEditingTitle(true); }}
        onTitleSave={() => void handleTitleSave()}
        onTitleKeyDown={handleTitleKeyDown}
        showAddEpisode={showAddEpisode}
        newEpisodeTitle={newEpisodeTitle}
        isCreatingEpisode={isCreatingEpisode}
        episodeError={episodeError ?? undefined}
        episodePending={episodePending}
        episodeActionLabel={episodeConfirmed ? t('episodeRefreshResult') : episodePending ? t(episodeCanRetry ? 'episodeRetryCreation' : 'episodeCheckResult') : undefined}
        onShowAddEpisode={setShowAddEpisode}
        onNewEpisodeTitleChange={setNewEpisodeTitle}
        onAddEpisode={handleAddEpisode}
        onAddEpisodeKeyDown={handleAddEpisodeKeyDown}
        onOpenModelSettings={() => setShowModelSettings(true)}
        onOpenPromptConfig={() => setShowPromptConfig(true)}
        onOpenImportAssets={() => setShowImportAssets(true)}
      />

      {/* ── Content Area ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <AnimatePresence mode="wait">
          {activeItem.kind === "art_direction" ? (
            <SeriesArtDirectionPanel
              key="art-direction"
              seriesId={seriesId}
              onSaved={refreshSeriesData}
            />
          ) : activeItem.kind === "asset" ? (
            <AssetContentPanel
              key={`asset-${activeItem.tab}`}
              tab={activeItem.tab}
              assets={getAssets(activeItem.tab)}
              label={ASSET_LABELS[activeItem.tab]}
            />
          ) : selectedEpisode ? (
            <EpisodeContentPanel
              key={`episode-${selectedEpisode.id}`}
              episode={selectedEpisode}
              seriesId={seriesId}
              onOpenEditor={() => handleOpenEpisode(selectedEpisode.id)}
            />
          ) : null}
        </AnimatePresence>
      </div>

      {/* ── Modals ── */}
      <SeriesModelSettingsModal
        isOpen={showModelSettings}
        onClose={() => setShowModelSettings(false)}
        seriesId={seriesId}
        onSaved={refreshSeriesData}
      />
      <SeriesPromptConfigModal
        isOpen={showPromptConfig}
        onClose={() => setShowPromptConfig(false)}
        seriesId={seriesId}
        onSaved={refreshSeriesData}
      />
      <ImportAssetsDialog
        isOpen={showImportAssets}
        onClose={() => setShowImportAssets(false)}
        seriesId={seriesId}
        onImported={refreshSeriesData}
      />
    </main>
  );
}

// ── Shared animation config ──

const contentTransition = {
  duration: 0.25,
  ease: [0.25, 1, 0.5, 1] as const, // ease-out-quart
};

// ── Asset Content Panel ──

function AssetContentPanel({
  tab,
  assets,
  label,
}: {
  tab: AssetTab;
  assets: (Character | Scene | Prop)[];
  label: string;
}) {
  const t = useTranslations("series");

  return (
    <motion.div
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -16 }}
      transition={contentTransition}
      className="flex-1 flex flex-col overflow-hidden"
    >
      {/* Header */}
      <div className="px-8 pt-6 pb-4">
        <h2 className="text-xl font-display font-bold text-foreground">
          {label}
          <span className="text-sm font-normal text-text-secondary ml-2">
            {t("itemCount", { count: assets.length })}
          </span>
        </h2>
        <p className="text-xs text-text-muted mt-1">
          {t("sharedAssetsEditHint")}
        </p>
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {assets.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-text-secondary">
            <motion.div
              animate={{ y: [0, -6, 0] }}
              transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
              className="w-16 h-16 rounded-2xl bg-glass border border-glass-border flex items-center justify-center mb-4"
            >
              <ImageIcon size={28} className="text-text-muted" />
            </motion.div>
            <p className="text-sm font-medium">{t("noAssets", { label })}</p>
            <p className="text-xs text-text-muted mt-1">{t("assetsSharedHint")}</p>
          </div>
        ) : (
          <motion.div
            className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4"
            initial="hidden"
            animate="visible"
            variants={{
              visible: { transition: { staggerChildren: 0.04 } },
            }}
          >
            {assets.map((asset) => (
              <motion.div
                key={asset.id}
                variants={{
                  hidden: { opacity: 0, y: 16, scale: 0.97 },
                  visible: {
                    opacity: 1,
                    y: 0,
                    scale: 1,
                    transition: { duration: 0.3, ease: [0.25, 1, 0.5, 1] },
                  },
                }}
              >
                <AssetCard asset={asset} type={tab} />
              </motion.div>
            ))}
          </motion.div>
        )}
      </div>
    </motion.div>
  );
}

// ── Episode Content Panel ──

function EpisodeContentPanel({
  episode,
  seriesId,
  onOpenEditor,
}: {
  episode: Project;
  seriesId: string;
  onOpenEditor: () => void;
}) {
  const t = useTranslations("series");

  const frames = episode.frames || [];
  const characters = episode.characters || [];
  const scenes = episode.scenes || [];
  const originalText = episode.originalText || "";

  return (
    <motion.div
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -16 }}
      transition={contentTransition}
      className="flex-1 flex flex-col overflow-hidden"
    >
      {/* Header */}
      <div className="px-8 pt-6 pb-4 flex items-start justify-between border-b border-glass-border">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <span className="text-xs bg-primary/20 text-primary px-2.5 py-1 rounded-lg font-mono font-bold">
              EP{episode.episode_number || "?"}
            </span>
            <h2 className="text-xl font-display font-bold text-foreground">
              {episode.title}
            </h2>
          </div>
          <p className="text-xs text-text-secondary">
            {episode.workflow_mode === "r2v" ? "R2V" : "I2V Legacy"} · {t("frameCount", { count: frames.length })}
          </p>
        </div>
        <motion.button
          whileHover={{ scale: 1.03 }}
          whileTap={{ scale: 0.97 }}
          onClick={onOpenEditor}
          className="flex items-center gap-2 bg-primary hover:bg-primary/90 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors shadow-lg shadow-primary/20 hover:shadow-primary/30"
        >
          <Play size={14} />
          {t("enterEditor")}
          <ChevronRight size={14} />
        </motion.button>
      </div>

      {/* Episode Overview */}
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
        {/* Script Summary */}
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-foreground">{t("scriptSummary")}</h3>
          {originalText ? (
            <p className="text-xs text-text-secondary leading-relaxed line-clamp-4 bg-surface rounded-lg p-3 border border-glass-border">
              {originalText.slice(0, 300)}{originalText.length > 300 ? "..." : ""}
            </p>
          ) : (
            <p className="text-xs text-text-muted italic">{t("noScript")}</p>
          )}
        </div>

        {/* Storyboard Overview */}
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-foreground">{t("storyboardOverview")}</h3>
          {frames.length === 0 ? (
            <div className="flex items-center gap-3 bg-surface rounded-lg p-4 border border-glass-border">
              <div className="w-10 h-10 rounded-lg bg-glass border border-glass-border flex items-center justify-center">
                <Play size={16} className="text-text-muted" />
              </div>
              <div>
                <p className="text-xs font-medium text-text-secondary">{t("noFrames")}</p>
                <p className="text-[0.6875rem] text-text-muted">{t("startCreating")}</p>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-4 lg:grid-cols-6 gap-2">
              {frames.slice(0, 12).map((frame, i) => (
                <div
                  key={frame.id}
                  className="aspect-video bg-surface rounded-lg border border-glass-border overflow-hidden cursor-pointer hover:border-primary/50 transition-colors"
                  onClick={onOpenEditor}
                >
                  {frame.rendered_image_url ? (
                    <img
                      src={/^(?:data:|blob:)/i.test(frame.rendered_image_url) ? frame.rendered_image_url : getAssetUrl(frame.rendered_image_url)}
                      alt={`#${i + 1}`}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-[0.625rem] text-text-muted font-mono">
                      #{i + 1}
                    </div>
                  )}
                </div>
              ))}
              {frames.length > 12 && (
                <div className="aspect-video bg-surface rounded-lg border border-glass-border flex items-center justify-center text-xs text-text-muted">
                  +{frames.length - 12}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Characters & Scenes count */}
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-surface rounded-lg p-3 border border-glass-border text-center">
            <p className="text-lg font-bold text-foreground">{characters.length}</p>
            <p className="text-[0.6875rem] text-text-muted">{t("characters")}</p>
          </div>
          <div className="bg-surface rounded-lg p-3 border border-glass-border text-center">
            <p className="text-lg font-bold text-foreground">{scenes.length}</p>
            <p className="text-[0.6875rem] text-text-muted">{t("scenes")}</p>
          </div>
          <div className="bg-surface rounded-lg p-3 border border-glass-border text-center">
            <p className="text-lg font-bold text-foreground">{frames.length}</p>
            <p className="text-[0.6875rem] text-text-muted">{t("storyboardFrames")}</p>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
