"use client";

import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronLeft,
  ChevronRight,
  Users,
  MapPin,
  Package,
  Plus,
  Settings,
  MessageSquareCode,
  Download,
  Palette,
} from "lucide-react";
import clsx from "clsx";
import type { Series, Project } from "@/store/projectStore";
import { useTranslations } from "next-intl";

// ── Types ──

export type SidebarItem =
  | { kind: "art_direction" }   // R2V v2 — series-level style baseline
  | { kind: "asset"; tab: "characters" | "scenes" | "props" }
  | { kind: "episode"; episodeId: string };

interface SeriesSidebarProps {
  series: Series;
  episodes: Project[];
  activeItem: SidebarItem;
  onItemChange: (item: SidebarItem) => void;
  onBack: () => void;
  // Title editing
  isEditingTitle: boolean;
  editTitle: string;
  titleSaving?: boolean;
  titleError?: string;
  onEditTitleChange: (val: string) => void;
  onTitleDoubleClick: () => void;
  onTitleSave: () => void;
  onTitleKeyDown: (e: React.KeyboardEvent) => void;
  // Add episode
  showAddEpisode: boolean;
  newEpisodeTitle: string;
  isCreatingEpisode: boolean;
  episodeError?: string;
  episodePending?: boolean;
  episodeActionLabel?: string;
  onShowAddEpisode: (show: boolean) => void;
  onNewEpisodeTitleChange: (val: string) => void;
  onAddEpisode: () => void;
  onAddEpisodeKeyDown: (e: React.KeyboardEvent) => void;
  // Actions
  onOpenModelSettings: () => void;
  onOpenPromptConfig: () => void;
  onOpenImportAssets: () => void;
}

// ── Asset nav config ──

const ASSET_TABS = [
  { tab: "characters" as const, labelKey: "characterLabel" as const, icon: Users },
  { tab: "scenes" as const, labelKey: "sceneLabel" as const, icon: MapPin },
  { tab: "props" as const, labelKey: "propLabel" as const, icon: Package },
] as const;

// ── Component ──

export default function SeriesSidebar({
  series,
  episodes,
  activeItem,
  onItemChange,
  onBack,
  isEditingTitle,
  editTitle,
  titleSaving,
  titleError,
  onEditTitleChange,
  onTitleDoubleClick,
  onTitleSave,
  onTitleKeyDown,
  showAddEpisode,
  newEpisodeTitle,
  isCreatingEpisode,
  episodeError,
  episodePending,
  episodeActionLabel,
  onShowAddEpisode,
  onNewEpisodeTitleChange,
  onAddEpisode,
  onAddEpisodeKeyDown,
  onOpenModelSettings,
  onOpenPromptConfig,
  onOpenImportAssets,
}: SeriesSidebarProps) {
  const t = useTranslations("series");
  const tc = useTranslations("common");

  const getAssetCount = (tab: "characters" | "scenes" | "props") => {
    if (tab === "characters") return series.characters?.length || 0;
    if (tab === "scenes") return series.scenes?.length || 0;
    return series.props?.length || 0;
  };

  const sortedEpisodes = [...episodes].sort(
    (a, b) => (a.episode_number || 0) - (b.episode_number || 0)
  );

  return (
    <motion.aside
      initial={{ x: -100, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ duration: 0.4, ease: [0.25, 1, 0.5, 1] }}
      className="w-64 flex-shrink-0 h-full border-r border-glass-border bg-surface backdrop-blur-xl flex flex-col"
    >
      {/* ── Header: breadcrumb + editable title ── */}
      <div className="p-5 border-b border-glass-border">
        <div className="space-y-2">
          {/* Back row */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={onBack}
              className="flex-shrink-0 text-text-secondary hover:text-foreground transition-colors"
              title={t("backToHome")}
            >
              <ChevronLeft size={16} />
            </button>
            <span className="text-xs text-text-secondary truncate">LumenX</span>
          </div>

          {/* Editable title */}
          {isEditingTitle ? (
            <input
              type="text"
              aria-label={t("editTitleHint")}
              disabled={titleSaving}
              value={editTitle}
              onChange={(e) => onEditTitleChange(e.target.value)}
              onBlur={onTitleSave}
              onKeyDown={onTitleKeyDown}
              className="text-base font-display font-bold text-foreground bg-transparent border-b-2 border-primary outline-none w-full"
              autoFocus
            />
          ) : (
            <h1
              className="text-base font-display font-bold text-foreground cursor-pointer hover:text-primary transition-colors truncate"
              onDoubleClick={onTitleDoubleClick}
              title={t("editTitleHint")}
            >
              {series.title}
            </h1>
          )}
          {titleError && <p role="alert" className="mt-2 text-xs text-status-failed-fg">{titleError}</p>}

          {series.description && (
            <p className="text-xs text-text-secondary truncate">{series.description}</p>
          )}
        </div>
      </div>

      {/* ── Art Direction (R2V v2 series-level style baseline) ── */}
      <div className="p-3 pb-1 space-y-1">
        <div className="px-3 py-1.5">
          <span className="text-[0.625rem] font-mono text-text-muted uppercase tracking-wider">
            {t("styleSection")}
          </span>
        </div>
        {(() => {
          const isActive = activeItem.kind === "art_direction";
          const hasStyle = !!series.art_direction?.style_config;
          return (
            <button
              onClick={() => onItemChange({ kind: "art_direction" })}
              className={clsx(
                "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group relative overflow-hidden",
                isActive
                  ? "bg-primary/10 text-foreground"
                  : "text-text-secondary hover:text-foreground hover:bg-hover-bg"
              )}
            >
              {isActive && (
                <motion.div
                  layoutId="series-active-pill"
                  className="absolute left-0 w-1 h-full bg-primary"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                />
              )}
              <Palette
                size={18}
                className={clsx(
                  "transition-colors",
                  isActive ? "text-primary" : "group-hover:text-foreground"
                )}
              />
              <span className="text-sm font-medium flex-1 text-left truncate">
                {t("artDirectionNav")}
              </span>
              <span
                className={clsx(
                  "text-[0.625rem] px-1.5 py-0.5 rounded-md font-mono uppercase tracking-wider",
                  hasStyle
                    ? isActive
                      ? "bg-primary/20 text-primary"
                      : "bg-glass text-text-secondary"
                    : "bg-glass text-text-muted"
                )}
              >
                {hasStyle ? t("styleSet") : t("styleEmpty")}
              </span>
            </button>
          );
        })()}
      </div>

      {/* ── Asset navigation ── */}
      <div className="px-3 pt-2 pb-3 space-y-1 border-t border-glass-border">
        <div className="px-3 py-1.5">
          <span className="text-[0.625rem] font-mono text-text-muted uppercase tracking-wider">
            {t("sharedAssets")}
          </span>
        </div>
        {ASSET_TABS.map(({ tab, labelKey, icon: Icon }) => {
          const isActive =
            activeItem.kind === "asset" && activeItem.tab === tab;
          const count = getAssetCount(tab);

          return (
            <button
              key={tab}
              onClick={() => onItemChange({ kind: "asset", tab })}
              className={clsx(
                "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group relative overflow-hidden",
                isActive
                  ? "bg-primary/10 text-foreground"
                  : "text-text-secondary hover:text-foreground hover:bg-hover-bg"
              )}
            >
              {isActive && (
                <motion.div
                  layoutId="series-active-pill"
                  className="absolute left-0 w-1 h-full bg-primary"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                />
              )}
              <Icon
                size={18}
                className={clsx(
                  "transition-colors",
                  isActive ? "text-primary" : "group-hover:text-foreground"
                )}
              />
              <span className="text-sm font-medium flex-1 text-left">
                {t(labelKey)}
              </span>
              <span
                className={clsx(
                  "text-xs px-1.5 py-0.5 rounded-md font-mono",
                  isActive
                    ? "bg-primary/20 text-primary"
                    : "bg-glass text-text-secondary"
                )}
              >
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* ── Episode list ── */}
      <div className="flex-1 flex flex-col min-h-0 border-t border-glass-border">
        <div className="px-6 py-2.5 flex items-center justify-between">
          <span className="text-[0.625rem] font-mono text-text-muted uppercase tracking-wider">
            {t("episodesCount", { count: episodes.length })}
          </span>
        </div>

        <div className="flex-1 overflow-y-auto px-3 space-y-1">
          {sortedEpisodes.map((ep) => {
            const isActive =
              activeItem.kind === "episode" &&
              activeItem.episodeId === ep.id;

            return (
              <button
                key={ep.id}
                onClick={() =>
                  onItemChange({ kind: "episode", episodeId: ep.id })
                }
                className={clsx(
                  "w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg transition-all duration-200 group relative overflow-hidden",
                  isActive
                    ? "bg-primary/10 text-foreground"
                    : "text-text-secondary hover:text-foreground hover:bg-hover-bg"
                )}
              >
                {isActive && (
                  <motion.div
                    layoutId="series-active-pill"
                    className="absolute left-0 w-1 h-full bg-primary"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                  />
                )}
                <span
                  className={clsx(
                    "text-[0.625rem] font-mono font-bold px-1.5 py-0.5 rounded",
                    isActive
                      ? "bg-primary/20 text-primary"
                      : "bg-glass text-text-secondary"
                  )}
                >
                  EP{ep.episode_number || "?"}
                </span>
                <span className="text-sm font-medium flex-1 text-left truncate">
                  {ep.title}
                </span>
                <span className="text-[0.625rem] text-text-muted font-mono">
                  {ep.frames?.length || 0}
                </span>
                {isActive && (
                  <ChevronRight size={14} className="opacity-40" />
                )}
              </button>
            );
          })}

          {episodes.length === 0 && !showAddEpisode && (
            <div className="text-center py-6">
              <p className="text-xs text-text-muted">{t("noEpisodes")}</p>
            </div>
          )}
        </div>

        {/* Add episode area */}
        <div className="px-3 pb-3 pt-1">
          <AnimatePresence mode="wait">
            {showAddEpisode ? (
              <motion.div
                key="add-form"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.2, ease: [0.25, 1, 0.5, 1] }}
                className="overflow-hidden"
              >
                <div className="space-y-2">
                  <input
                    type="text"
                    value={newEpisodeTitle}
                    disabled={isCreatingEpisode || episodePending}
                    onChange={(e) => onNewEpisodeTitleChange(e.target.value)}
                    placeholder={t("episodeTitlePlaceholder")}
                    className="w-full bg-input-bg border border-glass-border rounded-lg px-3 py-2 text-sm text-foreground placeholder-text-muted focus:outline-none focus:border-primary transition-colors"
                    autoFocus
                    onKeyDown={onAddEpisodeKeyDown}
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={onAddEpisode}
                      disabled={!newEpisodeTitle.trim() || isCreatingEpisode}
                      className="flex-1 bg-primary hover:bg-primary/90 text-white px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50 active:scale-[0.97]"
                    >
                      {isCreatingEpisode ? t("creating") : episodeActionLabel ?? tc("confirm")}
                    </button>
                    <button
                      disabled={isCreatingEpisode || episodePending}
                      onClick={() => {
                        onShowAddEpisode(false);
                        onNewEpisodeTitleChange("");
                      }}
                      className="px-3 py-1.5 rounded-lg text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors text-xs active:scale-[0.97]"
                    >
                      {tc("cancel")}
                    </button>
                  </div>
                  {episodeError && <p role="alert" className="text-xs leading-relaxed text-red-300">{episodeError}</p>}
                </div>
              </motion.div>
            ) : (
              <motion.button
                key="add-btn"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                onClick={() => onShowAddEpisode(true)}
                className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors text-xs border border-dashed border-glass-border hover:border-glass-border active:scale-[0.97]"
              >
                <Plus size={14} />
                {t("addEpisode")}
              </motion.button>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* ── Bottom tools ── */}
      <div className="border-t border-glass-border p-3 space-y-1">
        <button
          onClick={onOpenImportAssets}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors group"
        >
          <Download size={16} className="group-hover:text-green-400 transition-colors" />
          <span className="text-sm">{t("importAssets")}</span>
        </button>
        <button
          onClick={onOpenPromptConfig}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors group"
        >
          <MessageSquareCode size={16} className="group-hover:text-purple-400 transition-colors" />
          <span className="text-sm">{t("promptConfig")}</span>
        </button>
        <button
          onClick={onOpenModelSettings}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-text-secondary hover:text-foreground hover:bg-hover-bg transition-colors group"
        >
          <Settings size={16} className="group-hover:text-foreground transition-colors" />
          <span className="text-sm">{t("genSettings")}</span>
        </button>
      </div>
    </motion.aside>
  );
}
