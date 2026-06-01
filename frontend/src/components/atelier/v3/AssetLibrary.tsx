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
import { Check, ChevronLeft, ChevronRight, Image as ImageIcon, Link2, Music, Search, Square, Video, X } from "lucide-react";
import type { AtelierNode } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";
import { ATELIER_ASSET_SEEDS, type AssetSeed } from "./assetSeeds";

export type AssetKind = "all" | "image" | "video" | "audio";
// P2 (D'): added "style" so a creator can stamp a reference as
// look/aesthetic. Keeps the v0 image-only scope — audio uses its own
// SFX sub-classification (audioRoleOf below).
export type AssetCategory =
  | "character"
  | "scene"
  | "prop"
  | "style"
  | null;

const CATEGORY_LABELS: Record<Exclude<AssetCategory, null>, string> = {
  character: "Character",
  scene: "Scene",
  prop: "Prop",
  style: "Style",
};

const CATEGORY_CYCLE: Array<AssetCategory> = [null, "character", "scene", "prop", "style"];

// P2 (D'): audio sub-role lives on data.audio_role. v1 surfaces it as
// a non-cycling read-only badge inside the audio card so user-edited
// values (whether typed manually or set via a future TTS workflow)
// still show up; future PR can add a cycling pill if creators want to
// tag audio quickly inside the library.
export type AudioRole = "music" | "sfx" | "voice" | null;

const AUDIO_ROLE_LABELS: Record<Exclude<AudioRole, null>, string> = {
  music: "Music",
  sfx: "SFX",
  voice: "Voice",
};

interface AssetCard {
  /** For project cards this is the AtelierNode.id (used for selection,
   *  drag payload, bulk attach). For Browse seed cards this is the
   *  AssetSeed.id — purely a React key + selection identity. */
  nodeId: string;
  kind: "image" | "video" | "audio";
  title: string;
  thumbUrl?: string;
  category?: AssetCategory;
  audioRole?: AudioRole;
  /** v0.8 (M): one-line affordance hint rendered under the title.
   *  Project cards leave this undefined. Seed cards carry it from the
   *  AssetSeed roster so each curated entry can justify itself. */
  subtitle?: string;
  /** v0.8 (M): when set, this card represents a Browse seed (no
   *  AtelierNode exists yet). Drag flow uses a separate mime so the
   *  shell drop handler can branch (create + persist vs attach). */
  seed?: AssetSeed;
}

function seedToCard(seed: AssetSeed): AssetCard {
  return {
    nodeId: seed.id,
    kind: seed.kind,
    title: seed.title,
    subtitle: seed.subtitle,
    // Audio seeds intentionally leave thumbUrl undefined — the card
    // falls back to a Music icon. Image / video resolve through the
    // same getAssetUrl prefix path that project cards use, so a seed
    // file under output/ resolves to ${API_URL}/files/<url>.
    thumbUrl: seed.kind === "audio" ? undefined : getAssetUrl(seed.url),
    category: seed.kind === "image" ? (seed.category ?? null) : null,
    audioRole: seed.kind === "audio" ? (seed.audioRole ?? null) : null,
    seed,
  };
}

function readCategory(node: AtelierNode): AssetCategory {
  const raw = (node.data as { category?: unknown })?.category;
  if (raw === "character" || raw === "scene" || raw === "prop" || raw === "style") return raw;
  return null;
}

function readAudioRole(node: AtelierNode): AudioRole {
  const raw = (node.data as { audio_role?: unknown })?.audio_role;
  if (raw === "music" || raw === "sfx" || raw === "voice") return raw;
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
    audioRole: kind === "audio" ? readAudioRole(node) : null,
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
  // v0.8 (M): Project vs Browse view-mode. Project = the existing
  // projection over `nodes`. Browse = the curated seed roster. The two
  // share kind filter + image-secondary filter + search wiring; what
  // differs is the cards source (allCards), drag mime, and that seed
  // cards skip multi-select + the category-cycle pill.
  const [viewMode, setViewMode] = useState<"project" | "browse">("project");
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

  const projectCards = useMemo(
    () => nodes.map(nodeToCard).filter((c): c is AssetCard => c !== null),
    [nodes],
  );
  // Seeds are static — memoise once. Browse switching is just a card-
  // source swap; filters stay live.
  const seedCards = useMemo(() => ATELIER_ASSET_SEEDS.map(seedToCard), []);
  const allCards = viewMode === "browse" ? seedCards : projectCards;

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
      className="absolute top-4 bottom-4 z-30 flex w-[300px] flex-col overflow-hidden rounded-2xl border border-white/8 atelier-chrome-opaque shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.05)]"
    >
      <div aria-hidden="true" className="h-[2px] shrink-0 bg-gradient-to-r from-atelier-brand-400/85 via-atelier-brand-400/35 to-transparent" />

      {/* Editorial slip + collapse */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-white/8 px-3.5 py-1.5">
        <span aria-hidden="true" className="text-[11px] text-white/45">
          Library
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
            {viewMode === "browse" ? (
              <>Browse <span className="italic">samples</span></>
            ) : (
              <>Project <span className="italic">assets</span></>
            )}
          </div>
          <div className="mt-[2px] text-[11px] text-white/45">
            {counts.all} item{counts.all === 1 ? "" : "s"}
          </div>
        </div>
        {/* Multi-select only makes sense when each card has a real
            AtelierNode the shell can bulk-attach to a draft. Browse
            seeds don't have nodes yet, so the toggle hides in that mode. */}
        {onBulkAttach && viewMode === "project" ? (
          <button
            type="button"
            onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
            data-tip={selectMode ? "Cancel selection" : "Multi-select"}
            className={`btn-tip rounded-full border px-2 py-[3px] text-[10px] tracking-[0.01em] transition-colors ${
              selectMode
                ? "border-atelier-brand-400/45 bg-atelier-brand-400/15 text-atelier-brand-400"
                : "border-dashed border-white/15 text-text-muted/85 hover:border-white/25 hover:text-foreground"
            }`}
          >
            {selectMode ? "Cancel" : "Select"}
          </button>
        ) : null}
      </header>

      {/* v0.8 (M): Project | Browse view-mode toggle. Underlined
          segmented control sits just under the editorial header so it
          reads as a primary-axis switch (what's the source?) rather
          than a filter (which keeps the kind row beneath). Switching
          to Browse always exits multi-select so the panel state stays
          coherent. */}
      <div className="shrink-0 border-b border-white/6 px-3.5 pt-2">
        <div role="tablist" aria-label="Asset source" className="flex items-center gap-4">
          {(["project", "browse"] as const).map((mode) => {
            const isActive = viewMode === mode;
            const label = mode === "project" ? "Project" : "Browse";
            return (
              <button
                key={mode}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => {
                  if (mode === viewMode) return;
                  exitSelectMode();
                  setViewMode(mode);
                }}
                className={`relative pb-1.5 text-[11px] tracking-[0.01em] transition-colors ${
                  isActive ? "text-foreground" : "text-text-muted hover:text-foreground/85"
                }`}
              >
                {label}
                <span
                  aria-hidden="true"
                  className={`absolute inset-x-0 -bottom-px h-[1.5px] rounded-full transition-opacity ${
                    isActive ? "bg-atelier-brand-400/85 opacity-100" : "opacity-0"
                  }`}
                />
              </button>
            );
          })}
        </div>
      </div>

      {/* Search */}
      <div className="shrink-0 border-b border-white/6 px-3 py-2">
        <div className="relative">
          <Search size={11} aria-hidden="true" className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-muted/70" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by title…"
            className="w-full rounded-md border border-white/8 bg-black/35 py-1.5 pl-7 pr-7 text-[12px] leading-[1.4] text-foreground placeholder:text-text-muted/85 outline-none transition-colors focus:border-atelier-brand-400/55 focus:bg-black/45"
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
                className={`inline-flex items-center gap-1 rounded-md px-2 py-[5px] text-[11px] transition-colors ${
                  isActive
                    ? "bg-atelier-brand-400/15 text-atelier-brand-400 shadow-[inset_0_0_0_1px_rgba(59,107,255,0.3)]"
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
                  className={`rounded-full border px-2 py-[3px] text-[10px] tracking-[0.01em] transition-colors ${
                    isActive
                      ? "border-atelier-brand-400/45 bg-atelier-brand-400/15 text-atelier-brand-400"
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
              <div className="text-[11px] text-white/45">
                {allCards.length === 0 ? "No assets yet" : "No matches"}
              </div>
              <div className="text-[11px] leading-[1.45]">
                {allCards.length === 0 ? (
                  <>
                    Drop image / video files anywhere on the canvas to add them.
                    {viewMode === "project" ? (
                      <>
                        {" "}or{" "}
                        <button
                          type="button"
                          onClick={() => setViewMode("browse")}
                          className="text-atelier-brand-400 underline-offset-2 hover:underline"
                        >
                          switch to Browse
                        </button>
                        {" "}for sample assets.
                      </>
                    ) : null}
                  </>
                ) : (
                  "Try clearing the search or kind filter."
                )}
              </div>
            </div>
          </div>
        ) : (
          <ul className="grid grid-cols-2 gap-2">
            {filtered.map((card) => {
              const isSeed = !!card.seed;
              const checked = selectedIds.has(card.nodeId);
              // Browse seed cards are never selectable — no AtelierNode
              // exists yet for bulk-attach to target.
              const selectableInThisMode = selectMode && card.kind === "image" && !isSeed;
              return (
              <li key={card.nodeId}>
                <div
                  draggable={!selectMode}
                  onDragStart={(e) => {
                    if (selectMode) return;
                    e.dataTransfer.effectAllowed = "copyLink";
                    if (isSeed && card.seed) {
                      // v0.8 (M): seeds use a separate mime because the
                      // shell drop handler must CREATE a new node from
                      // a static url (and optionally chain attach) — not
                      // attach an existing node. Splitting the mime
                      // keeps the existing project-attach branch a clean
                      // single-purpose path.
                      e.dataTransfer.setData(
                        "application/x-atelier-asset-seed",
                        JSON.stringify({
                          id: card.seed.id,
                          kind: card.seed.kind,
                          title: card.seed.title,
                          url: card.seed.url,
                          category: card.seed.category,
                          audioRole: card.seed.audioRole,
                        }),
                      );
                    } else {
                      // Carry a custom mime so canvas drop targets
                      // (drafts / composer ref slots) can recognize a
                      // library drag and distinguish it from take drags
                      // or external file drops.
                      e.dataTransfer.setData(
                        "application/x-atelier-asset",
                        JSON.stringify({ nodeId: card.nodeId, kind: card.kind }),
                      );
                    }
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
                      ? "border-atelier-brand-400/65 shadow-[0_0_0_2px_rgba(59,107,255,0.35)]"
                      : "border-white/8 hover:border-atelier-brand-400/45 hover:shadow-[0_0_0_1px_rgba(59,107,255,0.22)]"
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
                    <span aria-hidden="true" className="absolute left-1 top-1 rounded-[3px] border border-dashed border-white/22 bg-black/70 px-1 py-[1px] text-[10px] tracking-[0.01em] text-white/85">
                      {card.kind}
                    </span>
                    {/* Category pill — image only.
                        Project cards: click-to-cycle (writes back via
                          onCycleCategory).
                        Browse seed cards: read-only badge — only renders
                          when the seed carries a category, since seeds
                          have no node to write back to. */}
                    {card.kind === "image" && !isSeed && onCycleCategory ? (
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
                        className={`btn-tip absolute right-1 top-1 rounded-[3px] border px-1 py-[1px] text-[10px] tracking-[0.01em] transition-colors ${
                          card.category
                            ? "border-atelier-brand-400/55 bg-atelier-brand-400/20 text-atelier-brand-400"
                            : "border-dashed border-white/22 bg-black/55 text-text-muted/85 opacity-0 group-hover:opacity-100"
                        }`}
                      >
                        {card.category ? CATEGORY_LABELS[card.category] : "Tag"}
                      </button>
                    ) : card.kind === "image" && isSeed && card.category ? (
                      <span
                        aria-label={`Category: ${CATEGORY_LABELS[card.category]}`}
                        className="absolute right-1 top-1 rounded-[3px] border border-atelier-brand-400/45 bg-atelier-brand-400/15 px-1 py-[1px] text-[10px] tracking-[0.01em] text-atelier-brand-400"
                      >
                        {CATEGORY_LABELS[card.category]}
                      </span>
                    ) : null}
                    {/* P2 (D'): audio-role badge — read-only in v1.
                        Lit only when an audio asset has data.audio_role
                        set so we don't spam an empty pill on the common
                        case. Future PR can add cycling like the image
                        category pill. */}
                    {card.kind === "audio" && card.audioRole ? (
                      <span
                        aria-label={`Audio role: ${AUDIO_ROLE_LABELS[card.audioRole]}`}
                        className="absolute right-1 top-1 rounded-[3px] border border-[#b59abe]/45 bg-[#b59abe]/18 px-1 py-[1px] text-[10px] tracking-[0.01em] text-[#d8cce0]"
                      >
                        {AUDIO_ROLE_LABELS[card.audioRole]}
                      </span>
                    ) : null}
                  </div>
                  <div className="px-1.5 py-1">
                    <div className="truncate text-[11px] leading-[1.3] text-foreground/95">
                      {card.title}
                    </div>
                    {card.subtitle ? (
                      <div className="mt-[1px] truncate text-[10px] leading-[1.3] text-text-muted/85">
                        {card.subtitle}
                      </div>
                    ) : null}
                  </div>
                  {/* Multi-select corner checkbox — image cards only,
                      visible only in select mode. */}
                  {selectableInThisMode ? (
                    <span
                      aria-hidden="true"
                      className={`pointer-events-none absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-[4px] border ${
                        checked
                          ? "border-atelier-brand-400/70 bg-atelier-brand-400/30 text-atelier-brand-400"
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
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-md bg-atelier-brand-400 px-3 py-2 text-[11px] text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_4px_12px_-4px_rgba(59,107,255,0.5)] transition-all hover:bg-atelier-brand-400/92 active:scale-[0.97]"
          >
            <Link2 size={11} aria-hidden="true" />
            Attach {selectedIds.size} ref{selectedIds.size === 1 ? "" : "s"}
          </button>
        </div>
      ) : null}
    </aside>
  );
}
