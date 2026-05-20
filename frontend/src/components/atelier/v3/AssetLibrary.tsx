"use client";
//
// Asset Library — left-edge collapsible drawer listing every media node
// in the project. The user's primary path to "find an old asset and
// reuse it" was previously: scroll the canvas with eyes. With 30+ assets
// that breaks. This drawer makes it: type 2 chars in search, drag.
//
// Scope (per Sprint 13 office-hours triage):
//   - One tab (project-scoped). Cross-project + 虚拟人像库 deferred until
//     usage data justifies the extra surface area.
//   - 4 kind filters (All / Image / Video / Audio).
//   - Image-only secondary filter: 角色 / 场景 / 道具 (Character / Scene /
//     Prop). Stored on the node as data.category. Click the pill on a
//     card to cycle.
//   - Search by title + filename substring.
//   - Drag a card onto the canvas to either:
//       * drop on a draft → attach as reference (matches port-drop flow)
//       * drop on empty canvas → no-op for v1 (defer "create new node from
//         existing asset" until we have a clear use case)
//
// All chrome stays out of the way at rest — the drawer collapses to a
// 36×36 button on the left edge. Toggle via the same button or the `A`
// keyboard shortcut.
import { useMemo, useState } from "react";
import { Check, CheckSquare, ChevronLeft, ChevronRight, Image as ImageIcon, Link2, Music, Search, Square, Video, X } from "lucide-react";
import type { AtelierNode } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";

export type AssetKind = "all" | "image" | "video" | "audio";
export type AssetCategory = "character" | "scene" | "prop" | null;

const CATEGORY_LABELS: Record<Exclude<AssetCategory, null>, string> = {
  character: "Character",
  scene: "Scene",
  prop: "Prop",
};

const CATEGORY_CYCLE: Array<AssetCategory> = [null, "character", "scene", "prop"];

interface AssetCard {
  nodeId: string;
  kind: "image" | "video" | "audio";
  title: string;
  thumbUrl?: string;
  category?: AssetCategory;
}

function readCategory(node: AtelierNode): AssetCategory {
  const raw = (node.data as { category?: unknown })?.category;
  if (raw === "character" || raw === "scene" || raw === "prop") return raw;
  return null;
}

function readMediaKind(node: AtelierNode): "image" | "video" | "audio" | null {
  if (node.type === "image") return "image";
  if (node.type === "audio") return "audio";
  // For video, only top-level video nodes with media count as assets;
  // candidate takes are sub-objects of drafts and shouldn't appear in
  // the library on their own (they'd be addable later via "completed
  // takes" view).
  if (node.type === "video" && (node.media_urls?.length ?? 0) > 0 && node.status !== "draft") {
    return "video";
  }
  return null;
}

function nodeToCard(node: AtelierNode): AssetCard | null {
  const kind = readMediaKind(node);
  if (!kind) return null;
  const url = node.media_urls?.[0];
  if (!url && kind !== "audio") return null;
  return {
    nodeId: node.id,
    kind,
    title: node.title || node.id.slice(-6),
    thumbUrl: url ? getAssetUrl(url) : undefined,
    category: kind === "image" ? readCategory(node) : null,
  };
}

interface Props {
  nodes: AtelierNode[];
  open: boolean;
  onToggle: () => void;
  /** Cycle the category of an image asset. Wired to updateNode in shell. */
  onCycleCategory?: (nodeId: string, next: AssetCategory) => void;
  /** When true, the closed-state collapsed-handle is suppressed. Used
   *  when a parent (e.g. LeftRailV3) is the canonical entry point and
   *  rendering our own handle would create two affordances for the same
   *  action. */
  hideCollapsedHandle?: boolean;
  /** Pixels to offset the panel from the left edge. Defaults to 16
   *  (matches the original solo-drawer position). LeftRailV3 passes 80
   *  (rail width 56 + 24 gap) so the panel docks against the rail. */
  leftOffsetPx?: number;
  /** Bulk-attach handler. When the user toggles into multi-select mode
   *  and picks one or more image cards, the bottom CTA fires this with
   *  the list of selected image node ids. Shell decides "attach to
   *  what" (typically the currently-selected draft, with a fallback
   *  toast). When omitted, multi-select mode is hidden. */
  onBulkAttach?: (imageNodeIds: string[]) => void;
}

export function AssetLibrary({
  nodes,
  open,
  onToggle,
  onCycleCategory,
  hideCollapsedHandle = false,
  leftOffsetPx = 16,
  onBulkAttach,
}: Props) {
  const [kindFilter, setKindFilter] = useState<AssetKind>("all");
  const [imageCategoryFilter, setImageCategoryFilter] = useState<"all" | Exclude<AssetCategory, null>>("all");
  const [search, setSearch] = useState("");
  // Multi-select mode: when on, image cards become click-to-toggle
  // checkboxes and the bottom of the panel surfaces an "Attach N" CTA.
  // Off-mode click still drag-and-drop / opens (existing behavior).
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const toggleSelected = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const exitSelectMode = () => {
    setSelectMode(false);
    setSelectedIds(new Set());
  };

  const allCards = useMemo(
    () => nodes.map(nodeToCard).filter((c): c is AssetCard => c !== null),
    [nodes],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allCards.filter((c) => {
      if (kindFilter !== "all" && c.kind !== kindFilter) return false;
      if (kindFilter === "image" && imageCategoryFilter !== "all") {
        if (c.category !== imageCategoryFilter) return false;
      }
      if (q && !c.title.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [allCards, kindFilter, imageCategoryFilter, search]);

  const counts = useMemo(() => {
    const map: Record<AssetKind, number> = { all: 0, image: 0, video: 0, audio: 0 };
    for (const c of allCards) {
      map.all += 1;
      map[c.kind] += 1;
    }
    return map;
  }, [allCards]);

  const cycleCategory = (card: AssetCard) => {
    if (card.kind !== "image" || !onCycleCategory) return;
    const idx = CATEGORY_CYCLE.indexOf(card.category ?? null);
    const next = CATEGORY_CYCLE[(idx + 1) % CATEGORY_CYCLE.length];
    onCycleCategory(card.nodeId, next);
  };

  // Collapsed state — small handle button on the left edge that hover-
  // tooltips. Press `A` or click to expand. Suppressed when a parent
  // (LeftRailV3) is the canonical entry point.
  if (!open) {
    if (hideCollapsedHandle) return null;
    return (
      <button
        type="button"
        aria-label="Open asset library"
        data-tip="Asset library (A)"
        onClick={onToggle}
        style={{ left: leftOffsetPx }}
        className="btn-tip absolute top-1/2 z-30 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-full border border-white/8 bg-[#141416]/96 text-text-muted shadow-[0_14px_30px_-18px_rgba(0,0,0,0.7),0_2px_6px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl transition-colors hover:bg-[#1a1a1d] hover:text-foreground"
      >
        <ChevronRight size={14} aria-hidden="true" />
      </button>
    );
  }

  return (
    <aside
      role="region"
      aria-label="Asset library"
      style={{ left: leftOffsetPx }}
      className="absolute top-4 bottom-4 z-30 flex w-[300px] flex-col overflow-hidden rounded-2xl border border-white/8 bg-[#0c0c10]/92 shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl"
    >
      <div aria-hidden="true" className="h-[2px] shrink-0 bg-gradient-to-r from-primary/85 via-primary/35 to-transparent" />

      {/* Editorial slip + collapse */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-dashed border-white/8 px-3.5 py-1.5">
        <span aria-hidden="true" className="font-mono text-[8.5px] font-medium uppercase tracking-[0.32em] text-text-muted/85">
          Atelier · Library · No 001
        </span>
        <button
          type="button"
          aria-label="Collapse asset library"
          data-tip="Collapse"
          onClick={onToggle}
          className="btn-tip inline-flex h-6 w-6 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
        >
          <ChevronLeft size={12} aria-hidden="true" />
        </button>
      </div>

      {/* Brand row + count + select-mode toggle */}
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-white/6 px-3.5 py-2.5">
        <div className="leading-tight">
          <div className="font-display text-[14px] font-medium tracking-[-0.005em] text-foreground">
            Project <span className="italic">assets</span>
          </div>
          <div className="mt-[2px] font-mono text-[9px] uppercase tracking-[0.28em] text-text-muted/85">
            {counts.all} item{counts.all === 1 ? "" : "s"}
          </div>
        </div>
        {onBulkAttach ? (
          <button
            type="button"
            onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
            data-tip={selectMode ? "Cancel selection" : "Multi-select"}
            className={`btn-tip rounded-full border px-2 py-[3px] font-mono text-[8.5px] font-medium uppercase tracking-[0.22em] transition-colors ${
              selectMode
                ? "border-primary/45 bg-primary/15 text-primary"
                : "border-dashed border-white/15 text-text-muted/85 hover:border-white/25 hover:text-foreground"
            }`}
          >
            {selectMode ? "Cancel" : "Select"}
          </button>
        ) : null}
      </header>

      {/* Search */}
      <div className="shrink-0 border-b border-white/6 px-3 py-2">
        <div className="relative">
          <Search size={11} aria-hidden="true" className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-muted/70" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by title…"
            className="w-full rounded-md border border-white/8 bg-black/35 py-1.5 pl-7 pr-7 text-[12px] leading-[1.4] text-foreground placeholder:text-text-muted/85 outline-none transition-colors focus:border-primary/55 focus:bg-black/45"
          />
          {search ? (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setSearch("")}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 grid h-5 w-5 place-items-center rounded text-text-muted hover:bg-hover-bg hover:text-foreground"
            >
              <X size={10} aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </div>

      {/* Kind filter row */}
      <div className="shrink-0 border-b border-white/6 px-3 py-2">
        <div role="tablist" aria-label="Asset kind" className="flex items-center gap-1">
          {(["all", "image", "video", "audio"] as const).map((k) => {
            const Icon = k === "image" ? ImageIcon : k === "video" ? Video : k === "audio" ? Music : null;
            const label = k === "all" ? "All" : k === "image" ? "Image" : k === "video" ? "Video" : "Audio";
            const isActive = kindFilter === k;
            return (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setKindFilter(k)}
                className={`inline-flex items-center gap-1 rounded-md px-2 py-[5px] font-mono text-[9.5px] font-medium uppercase tracking-[0.22em] transition-colors ${
                  isActive
                    ? "bg-primary/15 text-primary shadow-[inset_0_0_0_1px_rgba(100,108,255,0.3)]"
                    : "text-text-muted hover:bg-white/[0.04] hover:text-foreground/90"
                }`}
              >
                {Icon ? <Icon size={10} aria-hidden="true" /> : null}
                <span>{label}</span>
                <span aria-hidden="true" className="font-display text-[10px] tracking-tight text-text-muted/65">
                  {counts[k]}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Image-only secondary filter — only renders when Image is the
          selected kind, so the chrome stays minimal otherwise. */}
      {kindFilter === "image" ? (
        <div className="shrink-0 border-b border-white/6 px-3 py-1.5">
          <div role="tablist" aria-label="Image category" className="flex items-center gap-1">
            {(["all", "character", "scene", "prop"] as const).map((cat) => {
              const isActive = imageCategoryFilter === cat;
              const label =
                cat === "all"
                  ? "All"
                  : cat === "character"
                    ? "Character"
                    : cat === "scene"
                      ? "Scene"
                      : "Prop";
              return (
                <button
                  key={cat}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => setImageCategoryFilter(cat)}
                  className={`rounded-full border px-2 py-[3px] font-mono text-[8.5px] font-medium uppercase tracking-[0.24em] transition-colors ${
                    isActive
                      ? "border-primary/45 bg-primary/15 text-primary"
                      : "border-dashed border-white/15 text-text-muted/85 hover:border-white/25 hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* Grid */}
      <div className="flex-1 overflow-y-auto px-2.5 py-2.5">
        {filtered.length === 0 ? (
          <div className="grid h-full place-items-center px-3 text-center">
            <div className="space-y-1.5 text-text-muted/85">
              <div className="font-mono text-[9.5px] uppercase tracking-[0.28em]">
                {allCards.length === 0 ? "No assets yet" : "No matches"}
              </div>
              <div className="text-[11px] leading-[1.45]">
                {allCards.length === 0
                  ? "Drop image / video files anywhere on the canvas to add them."
                  : "Try clearing the search or kind filter."}
              </div>
            </div>
          </div>
        ) : (
          <ul className="grid grid-cols-2 gap-2">
            {filtered.map((card) => {
              const checked = selectedIds.has(card.nodeId);
              const selectableInThisMode = selectMode && card.kind === "image";
              return (
              <li key={card.nodeId}>
                <div
                  draggable={!selectMode}
                  onDragStart={(e) => {
                    if (selectMode) return;
                    // Carry a custom mime so canvas drop targets (drafts /
                    // composer ref slots) can recognize a library drag and
                    // distinguish it from take drags or external file drops.
                    e.dataTransfer.effectAllowed = "copyLink";
                    e.dataTransfer.setData(
                      "application/x-atelier-asset",
                      JSON.stringify({ nodeId: card.nodeId, kind: card.kind }),
                    );
                    e.dataTransfer.setData("text/plain", `@${card.title}`);
                  }}
                  onClick={() => {
                    if (!selectableInThisMode) return;
                    toggleSelected(card.nodeId);
                  }}
                  className={`group relative overflow-hidden rounded-md border bg-black/30 transition-shadow ${
                    selectableInThisMode
                      ? "cursor-pointer"
                      : selectMode
                        ? "cursor-not-allowed opacity-55"
                        : "cursor-grab active:cursor-grabbing"
                  } ${
                    checked
                      ? "border-primary/65 shadow-[0_0_0_2px_rgba(100,108,255,0.35)]"
                      : "border-white/8 hover:border-primary/45 hover:shadow-[0_0_0_1px_rgba(100,108,255,0.22)]"
                  }`}
                >
                  <div className="relative aspect-[4/3] bg-black/40">
                    {card.thumbUrl && card.kind === "image" ? (
                      <img
                        src={card.thumbUrl}
                        alt={card.title}
                        loading="lazy"
                        decoding="async"
                        className="absolute inset-0 h-full w-full object-cover"
                      />
                    ) : card.thumbUrl && card.kind === "video" ? (
                      <video
                        src={card.thumbUrl}
                        muted
                        playsInline
                        preload="metadata"
                        className="absolute inset-0 h-full w-full object-cover"
                      />
                    ) : (
                      <div className="absolute inset-0 grid place-items-center text-text-muted/65">
                        <Music size={18} aria-hidden="true" />
                      </div>
                    )}
                    {/* Kind tag — top-left */}
                    <span aria-hidden="true" className="absolute left-1 top-1 rounded-[3px] border border-dashed border-white/22 bg-black/70 px-1 py-[1px] font-mono text-[8px] font-medium uppercase tracking-[0.2em] text-white/85">
                      {card.kind}
                    </span>
                    {/* Category pill — image only, click to cycle */}
                    {card.kind === "image" && onCycleCategory ? (
                      <button
                        type="button"
                        aria-label={`Set category — current ${card.category ?? "none"}`}
                        data-tip={`Click to cycle category${card.category ? ` (${CATEGORY_LABELS[card.category]})` : ""}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          cycleCategory(card);
                        }}
                        onPointerDown={(e) => e.stopPropagation()}
                        onDragStart={(e) => e.preventDefault()}
                        className={`btn-tip absolute right-1 top-1 rounded-[3px] border px-1 py-[1px] font-mono text-[8px] font-medium uppercase tracking-[0.2em] transition-colors ${
                          card.category
                            ? "border-primary/55 bg-primary/20 text-primary"
                            : "border-dashed border-white/22 bg-black/55 text-text-muted/85 opacity-0 group-hover:opacity-100"
                        }`}
                      >
                        {card.category ? CATEGORY_LABELS[card.category] : "Tag"}
                      </button>
                    ) : null}
                  </div>
                  <div className="px-1.5 py-1">
                    <div className="truncate text-[11px] leading-[1.3] text-foreground/95">
                      {card.title}
                    </div>
                  </div>
                  {/* Multi-select corner checkbox — image cards only,
                      visible only in select mode. */}
                  {selectableInThisMode ? (
                    <span
                      aria-hidden="true"
                      className={`pointer-events-none absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-[4px] border ${
                        checked
                          ? "border-primary/70 bg-primary/30 text-primary"
                          : "border-white/30 bg-black/55 text-white/70"
                      }`}
                    >
                      {checked ? <Check size={11} aria-hidden="true" /> : <Square size={11} aria-hidden="true" />}
                    </span>
                  ) : null}
                </div>
              </li>
              );
            })}
          </ul>
        )}
      </div>
      {/* Bulk-attach CTA — only renders when multi-select mode is on
          AND the user has picked ≥1 image. The shell decides which
          draft to attach to. */}
      {selectMode && selectedIds.size > 0 && onBulkAttach ? (
        <div className="shrink-0 border-t border-white/8 px-3 py-2">
          <button
            type="button"
            onClick={() => {
              const ids = Array.from(selectedIds);
              onBulkAttach(ids);
              exitSelectMode();
            }}
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-[0.2em] text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_12px_-4px_rgba(100,108,255,0.5)] transition-all hover:bg-primary/92 active:scale-[0.97]"
          >
            <Link2 size={11} aria-hidden="true" />
            Attach {selectedIds.size} ref{selectedIds.size === 1 ? "" : "s"}
          </button>
        </div>
      ) : null}
    </aside>
  );
}
