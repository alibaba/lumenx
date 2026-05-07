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
} from "lucide-react";
import clsx from "clsx";
import type { Series, Project } from "@/store/projectStore";
import { assetTypeTerms, messages } from "@/lib/i18n";

// ── Types ──

export type SidebarItem =
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
  onEditTitleChange: (val: string) => void;
  onTitleDoubleClick: () => void;
  onTitleSave: () => void;
  onTitleKeyDown: (e: React.KeyboardEvent) => void;
  // Add episode
  showAddEpisode: boolean;
  newEpisodeTitle: string;
  isCreatingEpisode: boolean;
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

// ── Component ──

export default function SeriesSidebar({
  series,
  episodes,
  activeItem,
  onItemChange,
  onBack,
  isEditingTitle,
  editTitle,
  onEditTitleChange,
  onTitleDoubleClick,
  onTitleSave,
  onTitleKeyDown,
  showAddEpisode,
  newEpisodeTitle,
  isCreatingEpisode,
  onShowAddEpisode,
  onNewEpisodeTitleChange,
  onAddEpisode,
  onAddEpisodeKeyDown,
  onOpenModelSettings,
  onOpenPromptConfig,
  onOpenImportAssets,
}: SeriesSidebarProps) {
  const copy = messages.seriesPage.sidebar;
  const homeCopy = messages.homePage;
  const commonActions = messages.common.actions;
  const ASSET_TABS = [
    { tab: "characters" as const, label: assetTypeTerms.character.label, icon: Users },
    { tab: "scenes" as const, label: assetTypeTerms.scene.label, icon: MapPin },
    { tab: "props" as const, label: assetTypeTerms.prop.label, icon: Package },
  ] as const;

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
      className="w-64 flex-shrink-0 h-full border-r border-glass-border bg-black/40 backdrop-blur-xl flex flex-col"
    >
      {/* ── Header: breadcrumb + editable title ── */}
      <div className="p-5 border-b border-glass-border">
        <div className="space-y-2">
          {/* Back row */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={onBack}
              className="flex-shrink-0 text-gray-400 hover:text-white transition-colors"
              title={copy.backHome}
            >
              <ChevronLeft size={16} />
            </button>
            <span className="text-xs text-gray-500 truncate">{homeCopy.breadcrumb.root}</span>
          </div>

          {/* Editable title */}
          {isEditingTitle ? (
            <input
              type="text"
              value={editTitle}
              onChange={(e) => onEditTitleChange(e.target.value)}
              onBlur={onTitleSave}
              onKeyDown={onTitleKeyDown}
              className="text-base font-display font-bold text-white bg-transparent border-b-2 border-primary outline-none w-full"
              autoFocus
            />
          ) : (
            <h1
              className="text-base font-display font-bold text-white cursor-pointer hover:text-primary transition-colors truncate"
              onDoubleClick={onTitleDoubleClick}
              title={copy.editTitleHint}
            >
              {series.title}
            </h1>
          )}

          {series.description && (
            <p className="text-xs text-gray-500 truncate">{series.description}</p>
          )}
        </div>
      </div>

      {/* ── Asset navigation ── */}
      <div className="p-3 space-y-1">
        <div className="px-3 py-1.5">
          <span className="text-[10px] font-mono text-gray-600 uppercase tracking-wider">
            {copy.sharedAssets}
          </span>
        </div>
        {ASSET_TABS.map(({ tab, label, icon: Icon }) => {
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
                  ? "bg-primary/10 text-white"
                  : "text-gray-400 hover:text-white hover:bg-white/5"
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
                  isActive ? "text-primary" : "group-hover:text-white"
                )}
              />
              <span className="text-sm font-medium flex-1 text-left">
                {label}
              </span>
              <span
                className={clsx(
                  "text-xs px-1.5 py-0.5 rounded-md font-mono",
                  isActive
                    ? "bg-primary/20 text-primary"
                    : "bg-white/5 text-gray-500"
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
          <span className="text-[10px] font-mono text-gray-600 uppercase tracking-wider">
            {copy.episodes(episodes.length)}
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
                    ? "bg-primary/10 text-white"
                    : "text-gray-400 hover:text-white hover:bg-white/5"
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
                    "text-[10px] font-mono font-bold px-1.5 py-0.5 rounded",
                    isActive
                      ? "bg-primary/20 text-primary"
                      : "bg-white/5 text-gray-500"
                  )}
                >
                  EP{ep.episode_number || "?"}
                </span>
                <span className="text-sm font-medium flex-1 text-left truncate">
                  {ep.title}
                </span>
                <span className="text-[10px] text-gray-600 font-mono">
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
              <p className="text-xs text-gray-600">{copy.emptyEpisodes}</p>
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
                    onChange={(e) => onNewEpisodeTitleChange(e.target.value)}
                    placeholder={homeCopy.seriesCard.addEpisodeTitlePlaceholder}
                    className="w-full bg-black/30 border border-glass-border rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-primary transition-colors"
                    autoFocus
                    onKeyDown={onAddEpisodeKeyDown}
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={onAddEpisode}
                      disabled={!newEpisodeTitle.trim() || isCreatingEpisode}
                      className="flex-1 bg-primary hover:bg-primary/90 text-white px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50 active:scale-[0.97]"
                    >
                      {isCreatingEpisode ? homeCopy.createSeriesDialog.creating : homeCopy.seriesCard.confirmAddEpisode}
                    </button>
                    <button
                      onClick={() => {
                        onShowAddEpisode(false);
                        onNewEpisodeTitleChange("");
                      }}
                      className="px-3 py-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-white/10 transition-colors text-xs active:scale-[0.97]"
                    >
                      {commonActions.cancel}
                    </button>
                  </div>
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
                className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-gray-500 hover:text-white hover:bg-white/5 transition-colors text-xs border border-dashed border-gray-700 hover:border-gray-500 active:scale-[0.97]"
              >
                <Plus size={14} />
                {homeCopy.seriesCard.addEpisode}
              </motion.button>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* ── Bottom tools ── */}
      <div className="border-t border-glass-border p-3 space-y-1">
        <button
          onClick={onOpenImportAssets}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-colors group"
        >
          <Download size={16} className="group-hover:text-green-400 transition-colors" />
          <span className="text-sm">{copy.importAssets}</span>
        </button>
        <button
          onClick={onOpenPromptConfig}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-colors group"
        >
          <MessageSquareCode size={16} className="group-hover:text-purple-400 transition-colors" />
          <span className="text-sm">{copy.promptConfig}</span>
        </button>
        <button
          onClick={onOpenModelSettings}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-colors group"
        >
          <Settings size={16} className="group-hover:text-white transition-colors" />
          <span className="text-sm">{copy.modelSettings}</span>
        </button>
      </div>
    </motion.aside>
  );
}
