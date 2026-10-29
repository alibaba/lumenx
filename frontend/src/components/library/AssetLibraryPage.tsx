"use client";

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useTranslations } from "next-intl";
import { Search, Star, ArrowDownUp, ChevronDown, Check, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { getLibraryAssetUrl } from "./libraryAssetUrl";
import { requireActiveRuntimeEnvironment, getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { useProjectStore, type Series, type Project, type Character, type Scene, type Prop, type ImageAsset } from "@/store/projectStore";
import { toast } from "@/store/toastStore";
import { characterImageUrl, characterVariants } from "@/lib/characterImage";
import { coverGradient, GRAIN_URL } from "@/lib/cover";
import { rovingKeyDown } from "@/lib/a11y";
import AssetInspector from "./AssetInspector";
import NewLibraryAssetDialog from "./NewLibraryAssetDialog";
import { libraryHasAsset, libraryRevision, type LibraryAssetType } from "@/lib/libraryReadback";
import { markSharedReadStale } from "@/lib/frameCommandSnapshot";

type AssetTab = "characters" | "scenes" | "props";
type TypeFilter = AssetTab | "all";
type SortMode = "default" | "name" | "recent" | "usage";
type ViewAxis = "type" | "source";

const SINGULAR: Record<AssetTab, LibraryAssetType> = { characters: "character", scenes: "scene", props: "prop" };

interface AssetSource {
  id: string; // `series-X` / `project-X`（列表 key）
  rawId: string; // 裸 series/project id（调 API 用）
  name: string;
  kind: "series" | "project" | "global";
  project?: Project;
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
}

/** 渲染条目：携带所属 source，使「按类型」视图也能按源显示/操作。 */
interface RenderItem {
  asset: Character | Scene | Prop;
  type: AssetTab;
  src: AssetSource;
}

/** 渲染分组：「按类型」按资产类型、「按项目」按源，统一结构（title + meta + items）。 */
interface RenderGroup {
  key: string;
  title: string;
  meta: string;
  items: RenderItem[];
}

/** 取图：character 走 characterImageUrl（reference_sheet→full_body→legacy）；scene/prop 用 image_asset。 */
function getImageUrl(asset: Character | Scene | Prop, type: AssetTab): string | undefined {
  if (type === "characters") return characterImageUrl(asset as Character);
  const a = asset as Scene | Prop;
  if (a.image_asset?.variants?.length) {
    const sel = a.image_asset.variants.find((v) => v.id === a.image_asset?.selected_id);
    return sel?.url || a.image_asset.variants[0]?.url;
  }
  return a.image_url;
}

function variantCount(asset: Character | Scene | Prop, type: AssetTab): number {
  if (type === "characters") return characterVariants(asset as Character).length;
  return (asset as Scene | Prop).image_asset?.variants?.length ?? 0;
}

/** 「最近」排序用：派生资产的最新图片时间戳（秒，time.time）。
 *  character 取 full_body/three_view/headshot updated_at + reference_sheet 变体 created_at 的最大值；
 *  scene/prop 取 image_asset 的 created_at/image_updated_at + 变体 created_at 的最大值。
 *  全无时间戳 → 0（降序时排最后）。纯前端派生。 */

function publishLibraryRevision(revision: number): void {
  const environment = requireActiveRuntimeEnvironment().key;
  useProjectStore.setState((state) => {
    const previousFloor = state.readWatermarks[environment] ?? { series: {} };
    const nextFloor = { ...previousFloor, library: Math.max(previousFloor.library ?? 0, revision), series: { ...previousFloor.series } };
    if ((previousFloor.library ?? 0) >= nextFloor.library) return state;
    return {
      readWatermarks: { ...state.readWatermarks, [environment]: nextFloor },
      currentProject: state.currentProject ? markSharedReadStale(state.currentProject, nextFloor, environment) : null,
      projects: state.projects.map((project) => markSharedReadStale(project, nextFloor, environment)),
    };
  });
}

function recencyOf(asset: Character | Scene | Prop, type: AssetTab): number {
  const ts: number[] = [];
  if (type === "characters") {
    const c = asset as Character;
    if (c.full_body_updated_at) ts.push(c.full_body_updated_at);
    if (c.three_view_updated_at) ts.push(c.three_view_updated_at);
    if (c.headshot_updated_at) ts.push(c.headshot_updated_at);
    for (const v of c.reference_sheet?.image_variants ?? []) if (v.created_at) ts.push(v.created_at);
  } else {
    const a = asset as Scene | Prop;
    // ImageAsset 的 TS 类型未声明 created_at/image_updated_at，但后端确实下发（time.time 秒）；
    // 防御性读取后端字段，并以变体 created_at 兜底。
    const ia = a.image_asset as (ImageAsset & { created_at?: number; image_updated_at?: number }) | undefined;
    if (ia?.created_at) ts.push(ia.created_at);
    if (ia?.image_updated_at) ts.push(ia.image_updated_at);
    for (const v of ia?.variants ?? []) if (v.created_at) ts.push(v.created_at);
  }
  return ts.length ? Math.max(...ts) : 0;
}

export default function AssetLibraryPage() {
  const t = useTranslations("library");
  const tc = useTranslations("common");
  const [sources, setSources] = useState<AssetSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeType, setActiveType] = useState<TypeFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("default");
  const [sortOpen, setSortOpen] = useState(false);
  const [viewAxis, setViewAxis] = useState<ViewAxis>("type");
  const [starredOnly, setStarredOnly] = useState(false);
  const [selected, setSelected] = useState<{ sourceId: string; assetId: string; type: AssetTab } | null>(null);
  const [newAssetOpen, setNewAssetOpen] = useState(false);
  const mountedRef = useRef(false);
  const loadRequestRef = useRef(0);
  const libraryRevisionFloorRef = useRef(0);
  const sourcesRef = useRef(sources); sourcesRef.current = sources;
  const selectedRef = useRef(selected); selectedRef.current = selected;

  // Keep confirmed uncached project output in this page; never select or cache another Studio project.
  const acceptProjectRead = useCallback((snapshot: Project, isCurrent: (snapshot: Project) => boolean): boolean => {
    const selection = selectedRef.current;
    const source = sourcesRef.current.find(item => item.id === selection?.sourceId);
    if (!mountedRef.current || !selection || source?.kind !== 'project' || source.rawId !== snapshot.id
      || snapshot.revision < (source.project?.revision ?? 0) || !isCurrent(snapshot)) return false;
    const pool = snapshot[selection.type];
    const previous = source[selection.type].find(item => item.id === selection.assetId);
    if (!pool.some(item => item.id === selection.assetId && (item.source ?? 'episode') === (previous?.source ?? 'episode'))) return false;
    // GET /projects/:id also contains implicit shared overlays. Retain original owner groups,
    // including shared entries that were explicitly present in the project's source row.
    const visible = <T extends Character | Scene | Prop,>(items: T[], prior: T[]) => items.filter(item => !item.source || item.source === 'episode'
      || prior.some(old => old.id === item.id && old.source === item.source));
    const next = sourcesRef.current.map(item => item === source ? { ...item, project: snapshot,
      characters: visible(snapshot.characters, item.characters), scenes: visible(snapshot.scenes, item.scenes), props: visible(snapshot.props, item.props) } : item);
    // A list read issued before this projection cannot replace it when it arrives later.
    loadRequestRef.current += 1;
    libraryRevisionFloorRef.current = Math.max(libraryRevisionFloorRef.current, snapshot.read_versions?.library ?? 0);
    sourcesRef.current = next;
    setSources(next); setLoading(false);
    return true;
  }, []);

  type LoadAssetsOptions = { expectGlobalAsset?: { assetType: LibraryAssetType; assetId: string } };
  type LoadAssetsResult = { applied: boolean; found: boolean };

  const loadAssets = useCallback(async (options: LoadAssetsOptions = {}): Promise<LoadAssetsResult> => {
    const requestId = ++loadRequestRef.current;
    const environment = getRuntimeEnvironment();
    if (!environment) { setLoading(false); return { applied: false, found: false }; }
    const live = () => mountedRef.current && requestId === loadRequestRef.current && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    setLoading(true);
    try {
      const [seriesList, projects, globalPool] = await Promise.all([
        api.listSeries(),
        api.getProjects(),
        api.listLibraryAssets(),
      ]);
      const revision = libraryRevision(globalPool);
      const expected = options.expectGlobalAsset;
      const found = expected ? libraryHasAsset(globalPool, expected.assetType, expected.assetId) : true;
      if (!live()) return { applied: false, found };
      const knownLibraryFloor = Math.max(
        libraryRevisionFloorRef.current,
        useProjectStore.getState().readWatermarks[environment.key]?.library ?? 0
      );
      if (revision === null || revision < knownLibraryFloor) return { applied: false, found };

      const result: AssetSource[] = [];

      for (const s of seriesList as Series[]) {
        if ((s.characters?.length || 0) + (s.scenes?.length || 0) + (s.props?.length || 0) > 0) {
          result.push({
            id: `series-${s.id}`,
            rawId: s.id,
            name: s.title,
            kind: "series",
            characters: s.characters || [],
            scenes: s.scenes || [],
            props: s.props || [],
          });
        }
      }

      const standaloneProjects = (projects as Project[]).filter((p) => !p.series_id);
      for (const p of standaloneProjects) {
        const confirmed = sourcesRef.current.find(source => source.kind === 'project' && source.rawId === p.id);
        if (confirmed?.project?.readEnvironment === environment.key && confirmed.project.read_versions
          && p.revision <= confirmed.project.revision) {
          result.push(confirmed);
          continue;
        }
        if ((p.characters?.length || 0) + (p.scenes?.length || 0) + (p.props?.length || 0) > 0) {
          result.push({
            id: `project-${p.id}`,
            rawId: p.id,
            name: p.title,
            kind: "project",
            project: p,
            characters: p.characters || [],
            scenes: p.scenes || [],
            props: p.props || [],
          });
        }
      }

      // 全局/共享池作为一个 kind:"global" 源（空池则不加）。名称在加载时取 i18n，
      // 与 series/project 的 data 名同样存进 source.name。
      const g = (globalPool || {}) as { characters?: Character[]; scenes?: Scene[]; props?: Prop[] };
      const gChars = g.characters ?? [];
      const gScenes = g.scenes ?? [];
      const gProps = g.props ?? [];
      if (gChars.length + gScenes.length + gProps.length > 0) {
        result.push({
          id: "global",
          rawId: "global",
          name: t("globalGroup"),
          kind: "global",
          characters: gChars,
          scenes: gScenes,
          props: gProps,
        });
      }

      libraryRevisionFloorRef.current = Math.max(libraryRevisionFloorRef.current, revision);
      publishLibraryRevision(revision);
      setSources(result);
      return { applied: true, found };
    } catch (error) {
      if (live()) {
        console.error("Failed to load asset library:", error);
        toast.error(t("loadFailed"), { body: t("loadFailedBody") });
      }
      return { applied: false, found: false };
    } finally {
      if (live()) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    mountedRef.current = true;
    void loadAssets();
    const unsubscribe = subscribeRuntimeEnvironment(() => {
      loadRequestRef.current += 1;
      libraryRevisionFloorRef.current = 0;
      setSelected(null); setSources([]);
      void loadAssets();
    });
    return () => {
      unsubscribe();
      mountedRef.current = false;
      loadRequestRef.current += 1;
    };
  }, [loadAssets]);

  const confirmGlobalAssetRead = useCallback(async (assetType: LibraryAssetType, assetId: string): Promise<boolean> => {
    const result = await loadAssets({ expectGlobalAsset: { assetType, assetId } });
    return result.applied && result.found;
  }, [loadAssets]);

  // 全局计数（facet 总览；不受搜索/星标过滤影响，与分组标题里的计数互补）。
  const counts = useMemo(() => {
    let ch = 0,
      sc = 0,
      pr = 0,
      st = 0;
    for (const s of sources) {
      ch += s.characters.length;
      sc += s.scenes.length;
      pr += s.props.length;
      st +=
        s.characters.filter((a) => a.starred).length +
        s.scenes.filter((a) => a.starred).length +
        s.props.filter((a) => a.starred).length;
    }
    return { characters: ch, scenes: sc, props: pr, all: ch + sc + pr, starred: st };
  }, [sources]);

  const typePills: { id: TypeFilter; label: string; count: number }[] = [
    { id: "all", label: t("allLabel"), count: counts.all },
    { id: "characters", label: t("characterLabel"), count: counts.characters },
    { id: "scenes", label: t("sceneLabel"), count: counts.scenes },
    { id: "props", label: t("propLabel"), count: counts.props },
  ];

  const TYPE_LABEL: Record<AssetTab, string> = {
    characters: t("characterLabel"),
    scenes: t("sceneLabel"),
    props: t("propLabel"),
  };

  // 排序选项（usage 禁用：需后端使用频次统计）+ 触发按钮当前态文案。
  const sortOptions: { id: SortMode; label: string; disabled?: boolean }[] = [
    { id: "default", label: t("sortDefault") },
    { id: "name", label: t("sortName") },
    { id: "recent", label: t("sortRecent") },
    { id: "usage", label: t("sortUsage"), disabled: true },
  ];
  const sortLabelMap: Record<SortMode, string> = {
    default: t("sortDefault"),
    name: t("sortName"),
    recent: t("sortRecent"),
    usage: t("sortUsage"),
  };

  // 渲染模型：两种轴。
  //  - "type"（默认）：按资产类型分 3 组（角色/场景/道具），每组含所有 source 的该类型资产，
  //    卡片副标题显示所属 source 名。
  //  - "source"：按 source 分组（系列/项目/全局），保持原行为。
  // 两者都受 activeType pill + 搜索 + 星标过滤，并按 sortMode 排序。
  const groups = useMemo<RenderGroup[]>(() => {
    const scopedTypes: AssetTab[] = activeType === "all" ? ["characters", "scenes", "props"] : [activeType];
    const q = searchQuery.trim().toLowerCase();
    const match = (a: Character | Scene | Prop) =>
      (!starredOnly || !!a.starred) &&
      (!q || a.name.toLowerCase().includes(q) || (a.description?.toLowerCase().includes(q) ?? false));
    const sortItems = (items: RenderItem[]) => {
      if (sortMode === "name") items.sort((x, y) => x.asset.name.localeCompare(y.asset.name, "zh"));
      else if (sortMode === "recent") items.sort((x, y) => recencyOf(y.asset, y.type) - recencyOf(x.asset, x.type));
      // "default" / "usage"（禁用）：保持插入顺序。
      return items;
    };
    const typeLabel = (ty: AssetTab) =>
      ty === "characters" ? t("characterLabel") : ty === "scenes" ? t("sceneLabel") : t("propLabel");

    if (viewAxis === "type") {
      return scopedTypes
        .map((ty): RenderGroup => {
          const items: RenderItem[] = [];
          for (const src of sources)
            for (const a of src[ty] as (Character | Scene | Prop)[]) if (match(a)) items.push({ asset: a, type: ty, src });
          sortItems(items);
          return { key: `type-${ty}`, title: typeLabel(ty), meta: String(items.length), items };
        })
        .filter((grp) => grp.items.length > 0);
    }

    const kindLabel = (k: AssetSource["kind"]) =>
      k === "series" ? t("series") : k === "global" ? t("globalGroup") : t("project");
    return sources
      .map((src): RenderGroup => {
        const items: RenderItem[] = [];
        for (const ty of scopedTypes)
          for (const a of src[ty] as (Character | Scene | Prop)[]) if (match(a)) items.push({ asset: a, type: ty, src });
        sortItems(items);
        return { key: src.id, title: src.name, meta: `${kindLabel(src.kind)} · ${items.length}`, items };
      })
      .filter((grp) => grp.items.length > 0);
  }, [sources, activeType, searchQuery, starredOnly, sortMode, viewAxis, t]);

  const visibleCount = groups.reduce((acc, g) => acc + g.items.length, 0);

  // 选中的资产被筛掉后自动关 inspector（避免残留指向已隐藏资产）。
  useEffect(() => {
    if (!selected) return;
    const stillVisible = groups.some((grp) =>
      grp.items.some(
        (it) => it.src.id === selected.sourceId && it.asset.id === selected.assetId && it.type === selected.type
      )
    );
    if (!stillVisible) setSelected(null);
  }, [groups, selected]);

  const toggleStar = async (sourceId: string, assetId: string, type: AssetTab) => {
    const src = sources.find((s) => s.id === sourceId);
    if (!src) return;
    const cur = (src[type] as (Character | Scene | Prop)[]).find((a) => a.id === assetId);
    const prevStarred = !!cur?.starred;
    const setStarredTo = (val: boolean) => (prev: AssetSource[]) =>
      prev.map((s) =>
        s.id !== sourceId
          ? s
          : { ...s, [type]: (s[type] as (Character | Scene | Prop)[]).map((a) => (a.id === assetId ? { ...a, starred: val } : a)) }
      );
    setSources(setStarredTo(!prevStarred)); // 乐观更新
    try {
      if (src.kind === "series") await api.toggleSeriesAssetStarred(src.rawId, assetId, SINGULAR[type]);
      else if (src.kind === "global") await api.updateLibraryAsset(SINGULAR[type], assetId, { starred: !prevStarred });
      else await api.toggleAssetStarred(src.rawId, assetId, SINGULAR[type]);
    } catch (e) {
      console.error("toggle star failed", e);
      setSources(setStarredTo(prevStarred)); // 失败:精确还原到原值（不靠再翻一次，避免并发下双翻 desync）
    }
  };

  // 选中资产的实时引用（以 sources 为单一数据源，保证星标等变更同步到 inspector）。
  const selectedSource = selected ? sources.find((s) => s.id === selected.sourceId) : undefined;
  const selectedAsset =
    selected && selectedSource
      ? (selectedSource[selected.type] as (Character | Scene | Prop)[]).find((a) => a.id === selected.assetId)
      : undefined;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <header className="px-4 md:px-7 pt-5 md:pt-6 pb-3 flex items-end gap-5">
        <div className="flex-1 min-w-0">
          <div className="font-mono text-[0.625rem] font-medium uppercase tracking-[0.2em] text-text-muted">
            ASSET LIBRARY · <span className="text-primary font-semibold">{t("gallery") || "画廊"}</span>
          </div>
          <h1 className="text-[1.625rem] md:text-[2.125rem] font-display atelier-display font-semibold text-foreground leading-tight tracking-tight mt-1">
            {t("title")}
          </h1>
        </div>
        <div className="flex items-center gap-2.5 pb-1">
          <span className="font-mono text-[0.6875rem] text-text-muted tracking-[0.1em] uppercase">
            {t("assetCount", { count: visibleCount })}
          </span>
          <button
            type="button"
            onClick={() => setNewAssetOpen(true)}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full bg-primary text-on-accent text-[0.875rem] font-semibold hover:bg-primary-hover transition-colors"
          >
            <Plus size={14} />
            {t("newAsset")}
          </button>
        </div>
      </header>

      {/* Toolbar: 视图切换 + 类型 pills（带计数）+ ★ + 搜索 + 排序 */}
      <div className="px-4 md:px-7 pb-2 flex flex-wrap items-center gap-3">
        {/* 视图切换：按类型 ↔ 按项目 */}
        <div
          className="inline-flex p-[3px] rounded-full bg-surface-inset atelier-pill-tabs"
          role="group"
          aria-label={t("viewLabel")}
        >
          {([
            { id: "type", label: t("viewByType") },
            { id: "source", label: t("viewByProject") },
          ] as { id: ViewAxis; label: string }[]).map((v) => {
            const on = viewAxis === v.id;
            return (
              <button
                key={v.id}
                type="button"
                aria-pressed={on}
                onClick={() => setViewAxis(v.id)}
                className={`px-3.5 py-1.5 rounded-full text-[0.6875rem] font-semibold transition-colors ${
                  on ? "text-foreground atelier-pill-tab-active bg-surface shadow-sm" : "text-text-muted hover:text-foreground"
                }`}
              >
                {v.label}
              </button>
            );
          })}
        </div>

        <div className="inline-flex p-[3px] rounded-full bg-surface-inset atelier-pill-tabs" role="tablist" aria-label={t("assetTypeAria")} onKeyDown={rovingKeyDown}>
          {typePills.map((pill) => {
            const on = activeType === pill.id;
            return (
              <button
                key={pill.id}
                role="tab"
                aria-selected={on}
                tabIndex={on ? 0 : -1}
                onClick={() => setActiveType(pill.id)}
                className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-[0.6875rem] font-semibold transition-colors ${
                  on ? "text-foreground atelier-pill-tab-active bg-surface shadow-sm" : "text-text-muted hover:text-foreground"
                }`}
              >
                {pill.label}
                <span className={`font-mono text-[0.59375rem] ${on ? "text-text-secondary" : "text-text-muted"}`}>{pill.count}</span>
              </button>
            );
          })}
        </div>

        {/* ★ 加星过滤 */}
        <button
          type="button"
          aria-pressed={starredOnly}
          aria-label={t("starredOnlyAria")}
          onClick={() => setStarredOnly((v) => !v)}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[0.6875rem] font-semibold border transition-colors ${
            starredOnly
              ? "text-status-starred-fg bg-status-starred-bg border-status-starred-border"
              : "text-text-muted border-glass-border hover:text-foreground"
          }`}
        >
          <Star size={12} className={starredOnly ? "fill-current" : ""} />
          {counts.starred}
        </button>

        <div className="relative flex-1 min-w-[200px] max-w-[340px] bg-surface-inset border border-glass-border rounded-full atelier-search-input">
          <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            aria-label={t("searchPlaceholder")}
            className="w-full bg-transparent border-0 rounded-full py-2 pl-9 pr-4 text-[0.8125rem] text-foreground placeholder-text-muted focus:outline-none"
          />
        </div>

        {/* 排序下拉：默认 / 名称 / 最近（真实）/ 使用频次（禁用，需后端） */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setSortOpen((v) => !v)}
            aria-haspopup="listbox"
            aria-expanded={sortOpen}
            aria-label={t("sortLabel")}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[0.6875rem] font-medium text-text-muted border border-glass-border hover:text-foreground transition-colors"
          >
            <ArrowDownUp size={12} />
            {sortLabelMap[sortMode]}
            <ChevronDown size={12} className={`transition-transform ${sortOpen ? "rotate-180" : ""}`} />
          </button>
          {sortOpen && (
            <>
              {/* 点外关闭遮罩 */}
              <button
                type="button"
                aria-hidden="true"
                tabIndex={-1}
                onClick={() => setSortOpen(false)}
                className="fixed inset-0 z-40 cursor-default"
              />
              <div
                role="listbox"
                aria-label={t("sortLabel")}
                className="absolute right-0 top-full mt-1.5 z-50 min-w-[180px] glass-panel border border-glass-border rounded-xl p-1.5 shadow-xl"
              >
                {sortOptions.map((opt) => {
                  const on = sortMode === opt.id;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      role="option"
                      aria-selected={on}
                      disabled={opt.disabled}
                      title={opt.disabled ? t("sortUsageHint") : undefined}
                      onClick={() => {
                        if (opt.disabled) return;
                        setSortMode(opt.id);
                        setSortOpen(false);
                      }}
                      className={`w-full flex items-center justify-between gap-3 px-3 py-1.5 rounded-lg text-[0.75rem] font-medium text-left transition-colors ${
                        opt.disabled
                          ? "text-text-muted opacity-60 cursor-not-allowed"
                          : on
                            ? "text-primary bg-surface-inset"
                            : "text-text-secondary hover:text-foreground hover:bg-surface-inset"
                      }`}
                    >
                      <span className="flex items-center gap-2">
                        {opt.label}
                        {opt.disabled && (
                          <span className="font-mono text-[0.53125rem] uppercase tracking-[0.06em] text-text-muted px-1.5 py-0.5 rounded-full bg-surface-inset border border-glass-border">
                            {t("sortUsageHint")}
                          </span>
                        )}
                      </span>
                      {on && !opt.disabled && <Check size={13} />}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Body: 网格（按系列分组）+ 右侧 inspector */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        <div className="flex-1 overflow-y-auto px-7 pb-10 pt-3">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <div className="text-text-secondary text-[0.8125rem]">{tc("loading")}</div>
            </div>
          ) : counts.all === 0 ? (
            <div className="flex flex-col items-center justify-center py-16">
              <div className="glass-panel atelier-card p-10 rounded-2xl border border-glass-border text-center max-w-[620px] w-full relative overflow-hidden">
                <div className="relative z-[1] flex flex-col items-center gap-4">
                  <div className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-text-muted">
                    CAST · SCENES · PROPS
                  </div>
                  <p className="text-[2.125rem] font-display atelier-display font-medium italic leading-[1.25] tracking-tight text-foreground">
                    {t("emptyQuote")}
                  </p>
                  <p className="text-[0.9375rem] text-text-secondary max-w-[440px]">{t("noAssetsHint")}</p>
                </div>
              </div>
            </div>
          ) : groups.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-text-muted">
              <Search size={48} className="mb-3 opacity-60" />
              <p className="text-[0.9375rem] font-display atelier-display text-foreground">{t("noMatchTitle")}</p>
              <p className="text-[0.75rem] text-text-muted mt-1">{tc("noMatchHint")}</p>
              <button
                type="button"
                onClick={() => { setActiveType("all"); setSearchQuery(""); setStarredOnly(false); }}
                className="mt-4 glass-button text-[0.8125rem] font-semibold"
              >
                {tc("clearFilters")}
              </button>
            </div>
          ) : (
            <div className="space-y-6">
              {groups.map((grp) => (
                <div key={grp.key}>
                  {/* 分组标题 + 尾线 + 计数 */}
                  <div className="flex items-baseline gap-3 mb-4">
                    <span className="text-[1.5rem] font-display atelier-display font-semibold text-foreground tracking-tight">{grp.title}</span>
                    <span className="font-mono text-[0.625rem] text-text-muted tracking-wide uppercase">{grp.meta}</span>
                    <span className="atelier-group-line flex-1 h-px bg-border-subtle" />
                  </div>

                  {/* 卡片网格（库专用富卡片） */}
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                    {grp.items.map(({ asset, type, src }, i) => {
                      const url = getLibraryAssetUrl(getImageUrl(asset, type));
                      const vc = variantCount(asset, type);
                      const isSel = selected?.sourceId === src.id && selected?.assetId === asset.id && selected?.type === type;
                      const isStar = !!asset.starred;
                      const isChar = type === "characters";
                      return (
                        <div
                          key={`${src.id}-${type}-${asset.id}`}
                          role="button"
                          tabIndex={0}
                          onClick={() => setSelected({ sourceId: src.id, assetId: asset.id, type })}
                          onKeyDown={(e) => {
                            // 仅当卡片自身获得焦点时才响应；避免嵌套的 star <button> 在 Enter/Space 时双触发
                            if (e.target !== e.currentTarget) return;
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              setSelected({ sourceId: src.id, assetId: asset.id, type });
                            }
                          }}
                          aria-current={isSel ? "true" : undefined}
                          className={`atelier-asset-card atelier-reveal group relative text-left rounded-xl overflow-hidden border transition-all cursor-pointer ${
                            isSel ? "border-primary/60 ring-1 ring-primary/40" : "border-glass-border hover:-translate-y-1"
                          }`}
                          style={{ animationDelay: `${Math.min(i * 50, 250)}ms` }}
                        >
                          <div className={`${isChar ? "aspect-[4/3]" : "aspect-square"} bg-surface-inset overflow-hidden relative`}>
                            {url ? (
                              isChar ? (
                                // 角色卡横竖混杂 → 磨砂铺底（模糊同图填满留白）+ object-contain 完整显示不裁切
                                <>
                                  <img
                                    src={url}
                                    alt=""
                                    aria-hidden="true"
                                    className="absolute inset-0 w-full h-full object-cover blur-xl scale-110 opacity-40"
                                  />
                                  <img
                                    src={url}
                                    alt={asset.name}
                                    className="relative w-full h-full object-contain transition-transform group-hover:scale-105"
                                  />
                                </>
                              ) : (
                                <img src={url} alt={asset.name} className="w-full h-full object-cover transition-transform group-hover:scale-105" />
                              )
                            ) : (
                              // 无图：atelier 文字/渐变封面（取代发灰占位图标）— 确定性渐变 + 颗粒 + 首字母
                              <div
                                className="absolute inset-0 grid place-items-center overflow-hidden"
                                style={{ background: coverGradient(asset.id || asset.name) }}
                                aria-hidden="true"
                              >
                                <div
                                  className="pointer-events-none absolute inset-0 mix-blend-overlay opacity-50"
                                  style={{ backgroundImage: GRAIN_URL }}
                                />
                                <span className="relative font-display atelier-display font-semibold leading-none select-none text-foreground/90 text-[clamp(1.75rem,4.5vw,2.75rem)]">
                                  {(Array.from(asset.name.trim())[0] || "?").toUpperCase()}
                                </span>
                              </div>
                            )}
                            {isStar && (
                              <div className="pointer-events-none absolute inset-0 shadow-[inset_0_0_44px_-8px_var(--color-status-starred-bg)]" aria-hidden="true" />
                            )}
                            {/* top row: star chip + variant chip */}
                            <div className="absolute top-2 left-2 right-2 flex items-center justify-between">
                              <button
                                type="button"
                                aria-label={isStar ? t("unstar") : t("star")}
                                aria-pressed={isStar}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleStar(src.id, asset.id, type);
                                }}
                                onKeyDown={(e) => e.stopPropagation()}
                                className={`w-7 h-7 rounded-full grid place-items-center backdrop-blur-md transition-colors cursor-pointer ${
                                  isStar ? "text-status-starred-fg bg-status-starred-bg" : "text-white bg-black/45 hover:text-status-starred-fg"
                                }`}
                              >
                                <Star size={13} className={isStar ? "fill-current" : ""} />
                              </button>
                              {vc > 0 && (
                                <span className="px-2 py-[3px] rounded-full font-mono text-[0.5625rem] font-semibold text-white bg-black/55 backdrop-blur-md tracking-wide">
                                  {t("variantCount", { count: vc })}
                                </span>
                              )}
                            </div>
                            {/* kind chip（仅「按项目」视图 + 「全部」类型下显示，告知卡片类型） */}
                            {viewAxis === "source" && activeType === "all" && (
                              <span className="absolute bottom-2 left-2 px-2 py-[3px] rounded-full font-mono text-[0.53125rem] font-semibold uppercase tracking-[0.06em] text-white bg-black/55 backdrop-blur-md">
                                {TYPE_LABEL[type]}
                              </span>
                            )}
                          </div>
                          <div className="p-3">
                            <div className="text-sm font-medium text-foreground truncate">{asset.name}</div>
                            {viewAxis === "type" ? (
                              <div className="text-[0.6875rem] text-text-muted truncate mt-0.5">{src.name}</div>
                            ) : (
                              asset.description && <div className="text-[0.6875rem] text-text-muted truncate mt-0.5">{asset.description}</div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 右侧 inspector（选中才出现） */}
        {selected && selectedAsset && selectedSource && (
          <AssetInspector
            asset={selectedAsset}
            type={selected.type}
            sourceName={selectedSource.name}
            sourceId={selected.sourceId}
            sourceKind={selectedSource.kind}
            sourceProject={selectedSource.project}
            onProjectRead={acceptProjectRead}
            starred={!!selectedAsset.starred}
            onClose={() => setSelected(null)}
            onToggleStar={() => toggleStar(selected.sourceId, selected.assetId, selected.type)}
            onPromoted={confirmGlobalAssetRead}
            onRefresh={async () => (await loadAssets()).applied}
          />
        )}
      </div>

      {/* 新建全局资产弹窗（T6-entries） */}
      {newAssetOpen && (
        <NewLibraryAssetDialog onClose={() => setNewAssetOpen(false)} onCreated={confirmGlobalAssetRead} />
      )}
    </div>
  );
}
