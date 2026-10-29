"use client";

import { useState, useEffect, useRef, useSyncExternalStore, useCallback, useMemo } from "react";
import { useTranslations } from "next-intl";
import { X, Star, Download, Sparkles, Loader2, Globe } from "lucide-react";
import { useProjectStore, type Character, type Scene, type Prop, type ImageAsset, type ImageVariant, type Project, type Series } from "@/store/projectStore";
import { characterImageAsset } from "@/lib/characterImage";
import { api } from "@/lib/api";
import { getLibraryAssetUrl } from "./libraryAssetUrl";
import { requireActiveRuntimeEnvironment, getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { useStudioAssetImageRequests, usesStudioImageCore, usesStudioQwenImageCore } from "@/lib/useStudioAssetImageRequests";
import { isStudioGptImageModel } from "@/lib/studioAssetImageRequest";
import StudioImageRequestResults from "@/components/shared/StudioImageRequestResults";
import AssetRecoveryNotice from "@/components/shared/AssetRecoveryNotice";
import { toast } from "@/store/toastStore";
import { coverGradient, GRAIN_URL } from "@/lib/cover";
import { findLibraryRecovery, forgetLibraryRecovery, getLibraryRecoveryVersion, libraryOperationKey, rememberLibraryRecovery, subscribeLibraryRecoveries, type LibraryAssetType, type LibraryRecoveryRecord } from "@/lib/libraryReadback";
import { canApplyRead, observeReadVersions, projectReadSnapshot } from "@/lib/frameCommandSnapshot";
import { appAccessFetch, isAppAccessApiUrl } from "@/lib/appAccess";

type AssetTab = "characters" | "scenes" | "props";

// 资产类型 → 后端单数 type（生成端点用）。
const SINGULAR_TYPE: Record<AssetTab, LibraryAssetType> = {
  characters: "character",
  scenes: "scene",
  props: "prop",
};

// 「生成更多变体」一次追加的张数 + 任务轮询参数（~5 分钟上限）。
const VARIANT_BATCH = 3;
const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 150;

interface AssetInspectorProps {
  asset: Character | Scene | Prop;
  type: AssetTab;
  sourceName: string;
  /** 裸 series/project id（调生成/刷新 API 用）。 */
  sourceId: string;
  /** 资产归属：series/global 无生成端点 → 变体生成置灰。 */
  sourceKind: "series" | "project" | "global";
  sourceProject?: Project;
  onProjectRead?: (snapshot: Project, isCurrent: (snapshot: Project) => boolean) => boolean;
  starred: boolean;
  onClose: () => void;
  onToggleStar: () => void;
  /** 提升到全局成功后回调（父层刷新库以显示新入池资产）。可选。 */
  onRefresh?: () => Promise<boolean>;
  onPromoted?: (assetType: LibraryAssetType, assetId: string) => Promise<boolean>;
}

/** Character 走 characterImageAsset（reference_sheet→full_body，归一化成 ImageAsset 形状）；scene/prop 用 image_asset。 */
function primaryImageAsset(asset: Character | Scene | Prop, type: AssetTab): ImageAsset | undefined {
  if (type === "characters") return characterImageAsset(asset as Character);
  return (asset as Scene | Prop).image_asset;
}

function fallbackUrl(asset: Character | Scene | Prop, type: AssetTab): string | undefined {
  if (type === "characters") {
    const c = asset as Character;
    return c.image_url || c.full_body_image_url;
  }
  return (asset as Scene | Prop).image_url;
}

const MIME_EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
  "image/gif": "gif",
  "image/avif": "avif",
  "image/svg+xml": "svg",
};

/** 下载文件名扩展名：优先取 URL 路径后缀（剥掉 query/签名），否则回退到 blob content-type，默认 png。 */
function downloadExt(url: string, contentType?: string): string {
  try {
    const path = new URL(url, window.location.origin).pathname;
    const m = path.match(/\.([a-z0-9]+)$/i);
    if (m) return m[1].toLowerCase();
  } catch {
    // URL 解析失败时退回 content-type / 默认
  }
  const fromType = contentType?.split(";")[0].trim().toLowerCase();
  if (fromType && MIME_EXT[fromType]) return MIME_EXT[fromType];
  return "png";
}


function readPromoteRecovery(key: string): Extract<LibraryRecoveryRecord, { kind: "promote" }> | undefined {
  const record = findLibraryRecovery((candidate) => candidate.key === key && candidate.kind === "promote");
  return record?.kind === "promote" ? record : undefined;
}

function readVariantRecovery(key: string): Extract<LibraryRecoveryRecord, { kind: "variant" }> | undefined {
  const record = findLibraryRecovery((candidate) => candidate.key === key && candidate.kind === "variant");
  return record?.kind === "variant" ? record : undefined;
}

class VariantTaskPendingError extends Error {}
class VariantTaskFailedError extends Error {}

/**
 * 资产库右侧详情抽屉（Line B "Luminous Atelier"）。
 * 库专用，不复用共享 AssetCard。展示选中资产的 hero + 变体条 + 元数据 + prompt + 动作。
 * 元数据数据驱动（metaRows）：SEED/MODEL/SIZE 当前数据模型未存（变体仅
 * id/url/created_at/prompt_used），故读为 undefined → 不渲染；后端补字段后 UI 零改自动出现。
 * 动作区：「下载」实做；「生成更多变体」对 project 资产实做（series 置灰，无生成端点）。
 */
export default function AssetInspector({
  asset,
  type,
  sourceName,
  sourceId,
  sourceKind,
  sourceProject,
  onProjectRead,
  starred,
  onClose,
  onToggleStar,
  onPromoted,
  onRefresh,
}: AssetInspectorProps) {
  const t = useTranslations("library");
  const tp = useTranslations("project");
  const recovery = useTranslations("assetRecovery");
  const imageFeedback = useTranslations("castWorkbench");
  const runningOps = useProjectStore((state) => state.runningOps);
  const TYPE_LABEL: Record<AssetTab, string> = {
    characters: t("characterLabel"),
    scenes: t("sceneLabel"),
    props: t("propLabel"),
  };
  // created_at 来自 time.time()（秒）；容错已是毫秒的情况。相对时间标签走 i18n。
  const timeAgo = (ts?: number): string => {
    if (!ts) return "—";
    const tsMs = ts > 1e12 ? ts : ts * 1000;
    const days = Math.floor((Date.now() - tsMs) / 86_400_000);
    if (days <= 0) return t("timeToday");
    if (days === 1) return t("timeYesterday");
    if (days < 30) return t("timeDaysAgo", { days });
    return t("timeMonthsAgo", { months: Math.floor(days / 30) });
  };
  const rawSourceId = sourceId.replace(/^(project|series)-/, "");
  const assetType = SINGULAR_TYPE[type];
  // Raw /projects/ rows omit source; within a project list that means local ownership.
  const assetSource = sourceKind === 'project' && type !== 'characters' ? asset.source ?? 'episode' : asset.source;
  const cachedProject = useProjectStore(state => sourceKind === 'project'
    ? state.currentProject?.id === rawSourceId ? state.currentProject : state.projects.find(project => project.id === rawSourceId)
    : undefined);
  const ownerSeriesId = (sourceProject ? sourceProject.series_id : cachedProject?.series_id) ?? null;
  const activeEnvironment = useSyncExternalStore(subscribeRuntimeEnvironment, getRuntimeEnvironment, () => null);
  const identity = useMemo(() => ({ sourceId, sourceKind, type, assetId: asset.id, source: assetSource, ownerSeriesId, activeEnvironment }),
    [sourceId, sourceKind, type, asset.id, assetSource, ownerSeriesId, activeEnvironment]);
  const latestIdentity = useRef(identity); latestIdentity.current = identity;
  const sourceProjectRef = useRef(sourceProject); sourceProjectRef.current = sourceProject;
  const projectReadRef = useRef(onProjectRead); projectReadRef.current = onProjectRead;
  const localRead = useRef<{ identity: object; project: Project } | null>(null);
  const [projection, setProjection] = useState<{ identity: object; project: Project } | null>(null);
  const projected = projection?.identity === identity && projection.project.revision >= (sourceProject?.revision ?? 0) ? projection.project : null;
  const displayAsset = (type === 'scenes' ? projected?.scenes : type === 'props' ? projected?.props : [])?.find(item => item.id === asset.id) ?? asset;
  const imageAsset = primaryImageAsset(displayAsset, type);
  const baseVariants = imageAsset?.variants ?? [];
  // Legacy task results keep their append/highlight behavior. Core reads replace the complete asset projection above.
  const [extraVariants, setExtraVariants] = useState<ImageVariant[]>([]);
  const baseIds = new Set(baseVariants.map((v) => v.id));
  const variants = projected ? baseVariants : [...baseVariants, ...extraVariants.filter((v) => !baseIds.has(v.id))];
  const originalImage = primaryImageAsset(asset, type);
  const defaultId = originalImage?.selected_id ?? originalImage?.variants[0]?.id ?? null;
  const [activeVariantId, setActiveVariantId] = useState<string | null>(defaultId);
  const [generating, setGenerating] = useState(false);
  const [confirmationRead, setConfirmationRead] = useState<boolean | null>(null);
  const [readingAsset, setReadingAsset] = useState(false);
  const [assetReadFailed, setAssetReadFailed] = useState(false);
  useEffect(() => setConfirmationRead(null), [asset]);
  const needsConfirmation = confirmationRead ?? displayAsset.status === 'needs_confirmation';
  const [promoting, setPromoting] = useState(false);
  const [variantReadbackTarget, setVariantReadbackTarget] = useState<(Extract<LibraryRecoveryRecord, { kind: "variant" }> & { toastId?: string }) | null>(null);
  const [promoteReadbackTarget, setPromoteReadbackTarget] = useState<Extract<LibraryRecoveryRecord, { kind: "promote" }> | null>(null);
  const promoteKey = activeEnvironment && sourceKind !== "global"
    ? libraryOperationKey("promote", activeEnvironment.key, [sourceKind, rawSourceId, assetType, asset.id])
    : "";
  const generateKey = activeEnvironment && sourceKind === "project"
    ? libraryOperationKey("generate", activeEnvironment.key, [rawSourceId, asset.id, assetType, ...(assetSource === "series" ? [ownerSeriesId ?? ""] : [])])
    : "";
  const recoveryVersion = useSyncExternalStore(subscribeLibraryRecoveries, getLibraryRecoveryVersion, getLibraryRecoveryVersion);

  // 切换选中资产时重置本地高亮的变体 + 丢弃上一个资产本地追加的变体。
  useEffect(() => {
    setActiveVariantId(defaultId);
    setExtraVariants([]);
  }, [asset.id, defaultId, type, sourceId, sourceKind, ownerSeriesId]);

  // 卸载/切换资产后避免异步轮询回写已失效的状态。
  const aliveRef = useRef(true);
  const currentAssetIdRef = useRef(asset.id);
  const sessionEpochRef = useRef(0);
  const variantRetryInFlightRef = useRef(false);
  useEffect(() => {
    currentAssetIdRef.current = asset.id;
    sessionEpochRef.current += 1;
    setConfirmationRead(null); setReadingAsset(false); setAssetReadFailed(false);
    setGenerating(false); variantRetryInFlightRef.current = false;
  }, [asset.id, assetSource, sourceId, sourceKind, type, ownerSeriesId]);
  useEffect(() => subscribeRuntimeEnvironment(() => {
    sessionEpochRef.current += 1;
    variantRetryInFlightRef.current = false;
    setGenerating(false); setReadingAsset(false); setAssetReadFailed(false); setConfirmationRead(null);
  }), []);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      sessionEpochRef.current += 1;
    };
  }, []);

  useEffect(() => {
    const promoteRecovery = promoteKey ? readPromoteRecovery(promoteKey) : undefined;
    setPromoteReadbackTarget(promoteRecovery ?? null);
    const variantRecovery = generateKey ? readVariantRecovery(generateKey) : undefined;
    setVariantReadbackTarget(variantRecovery ?? null);
  }, [promoteKey, generateKey, recoveryVersion]);

  // a11y：抽屉打开时把焦点移入面板、Escape 关闭、关闭后还原焦点（非模态，不做 focus trap）。
  const asideRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    asideRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus?.();
    };
  }, []);

  const activeVariant = variants.find((v) => v.id === activeVariantId)
    ?? (projected ? variants.find(v => v.id === imageAsset?.selected_id) : undefined) ?? variants[0];
  const heroUrl = activeVariant?.url ?? fallbackUrl(displayAsset, type);
  const prompt = activeVariant?.prompt_used ?? "";

  // 元数据行（数据驱动）：先放现有四项，再在字段存在时追加 SEED/MODEL/SIZE。
  // 后端 TODO：当前 ImageVariant 仅 id/url/created_at/prompt_used，资产无 seed/model/size，
  // 故 assetMeta.* 读为 undefined → 不 push → 不渲染。后端补字段后此处零改自动出现。
  const assetMeta = asset as Partial<{ seed: number | string; model: string; size: string }>;
  const metaRows: { label: string; value: string }[] = [
    { label: t("metaType"), value: TYPE_LABEL[type] },
    { label: t("metaSource"), value: sourceName },
    { label: t("metaVariant"), value: `${variants.length}` },
    { label: t("metaCreated"), value: timeAgo(activeVariant?.created_at) },
  ];
  if (assetMeta.seed != null) metaRows.push({ label: "SEED", value: String(assetMeta.seed) });
  if (assetMeta.model) metaRows.push({ label: "MODEL", value: assetMeta.model });
  if (assetMeta.size) metaRows.push({ label: "SIZE", value: assetMeta.size });

  const handleDownload = async () => {
    if (!heroUrl) return;
    const fileBase = asset.name || "asset";
    const downloadUrl = getLibraryAssetUrl(heroUrl);
    try {
      const res = await appAccessFetch(downloadUrl);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const ext = downloadExt(downloadUrl, blob.type);
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = `${fileBase}.${ext}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      if (!isAppAccessApiUrl(downloadUrl)) {
        // 跨域(CORS)/网络失败：download 属性对跨域 URL 无效，退回到新标签打开。
        window.open(heroUrl, "_blank", "noopener,noreferrer");
      }
    }
  };

  // 轮询生成任务直到完成（mirror ConsistencyVault 的 task 轮询）；失败/超时抛错。
  const readVariantTaskState = async (taskId: string): Promise<"completed" | "failed" | "pending" | "unknown"> => {
    try {
      const status = await api.getTaskStatus(taskId);
      if (status?.status === "completed") return "completed";
      if (status?.status === "failed") return "failed";
      return "pending";
    } catch {
      return "unknown";
    }
  };

  const pollUntilDone = async (taskId: string, live: () => boolean): Promise<boolean> => {
    for (let i = 0; i < POLL_MAX_ATTEMPTS; i++) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      if (!live()) return false;
      const status = await readVariantTaskState(taskId);
      if (!live()) return false;
      if (status === "completed") return true;
      if (status === "failed") throw new VariantTaskFailedError(t("genFailed"));
      if (status === "unknown") throw new VariantTaskPendingError(t("loadFailedBody"));
    }
    throw new VariantTaskPendingError(t("genTimeout"));
  };

  const readSourceProject = useCallback(async (projectId: string, targetAssetId: string, sessionEpoch: number, isCurrent?: (snapshot: Project) => boolean): Promise<Project> => {
    if (!aliveRef.current || sessionEpochRef.current !== sessionEpoch || latestIdentity.current !== identity) throw new Error(t("loadFailed"));
    const captured = requireActiveRuntimeEnvironment();
    const response = await api.getProject(projectId);
    if (!isCurrentRuntimeEnvironment(captured.key, captured.generation)) throw new Error(t("loadFailed"));
    if (!aliveRef.current || sessionEpochRef.current !== sessionEpoch || latestIdentity.current !== identity) throw new Error(t("loadFailed"));
    const environment = captured.key;
    const snapshot = projectReadSnapshot(projectId, response, environment);
    const snapshotPool = type === 'characters' ? snapshot.characters : type === 'scenes' ? snapshot.scenes : snapshot.props;
    if (assetSource === 'series' && snapshot.series_id !== ownerSeriesId) throw new Error(t('loadFailed'));
    const sourceMatches = (item: Character | Scene | Prop) => (sourceKind === 'project' && type !== 'characters' ? item.source ?? 'episode' : item.source) === assetSource;
    if (!snapshotPool.some(item => item.id === targetAssetId && sourceMatches(item))) throw new Error(t('loadFailed'));
    const storeBefore = useProjectStore.getState();
    const knownProject = storeBefore.currentProject?.id === projectId
      ? storeBefore.currentProject
      : storeBefore.projects.find((project) => project.id === projectId);
    const prior = localRead.current?.identity === identity ? localRead.current.project : undefined;
    const origin = sourceProjectRef.current;
    const ownerRevision = Math.max(origin?.revision ?? 0, prior?.revision ?? 0);
    const knownPool = type === 'characters' ? knownProject?.characters : type === 'scenes' ? knownProject?.scenes : knownProject?.props;
    // A newer Library source supersedes old cached ownership, but a new GET cannot erase an observed owner change.
    if (knownProject && (type === 'characters' || knownProject.revision >= ownerRevision)
      && (!knownPool?.some(item => item.id === targetAssetId && sourceMatches(item))
        || (assetSource === 'series' && knownProject.series_id !== ownerSeriesId))) throw new Error(t('loadFailed'));
    if (type !== 'characters' && knownProject && knownProject.revision >= ownerRevision
      && (knownProject.series_id ?? null) !== ownerSeriesId) throw new Error(t('loadFailed'));
    if (origin && (origin.id !== projectId || snapshot.revision < origin.revision || (snapshot.series_id ?? null) !== (origin.series_id ?? null))) throw new Error(t('loadFailed'));
    const readWatermarks = observeReadVersions(
      storeBefore.readWatermarks,
      [...storeBefore.projects, ...(storeBefore.currentProject ? [storeBefore.currentProject] : []), ...(prior ? [prior] : []), ...(origin ? [origin] : [])],
      environment
    );
    const floor = readWatermarks[environment] ?? { series: {} };
    if (!canApplyRead(knownProject, snapshot, floor, environment) || !canApplyRead(prior, snapshot, floor, environment)) throw new Error(t("loadFailed"));

    if (isCurrent && !isCurrent(snapshot)) throw new Error(t('loadFailed'));
    let confirmed: Project | undefined = snapshot;
    if (knownProject) {
      if (!aliveRef.current || sessionEpochRef.current !== sessionEpoch) throw new Error(t("loadFailed"));
      const result = useProjectStore.getState().applyProjectReadSnapshot(projectId, response, false);
      if (result.status !== "applied") throw new Error(t("loadFailed"));
      const storeAfter = useProjectStore.getState();
      confirmed = storeAfter.currentProject?.id === projectId
        ? storeAfter.currentProject
        : storeAfter.projects.find((project) => project.id === projectId);
    }
    if (!aliveRef.current || sessionEpochRef.current !== sessionEpoch) throw new Error(t("loadFailed"));
    if (!confirmed || isCurrent && !isCurrent(confirmed)) throw new Error(t('loadFailed'));
    localRead.current = { identity, project: confirmed };
    return confirmed;
  }, [assetSource, sourceKind, type, ownerSeriesId, identity, t]);

  const readProjectAsset = async (projectId: string, targetAssetId: string, sessionEpoch: number): Promise<Character | Scene | Prop> => {
    const confirmed = await readSourceProject(projectId, targetAssetId, sessionEpoch);
    const list = type === 'characters' ? confirmed.characters : type === 'scenes' ? confirmed.scenes : confirmed.props;
    const updated = list.find(item => item.id === targetAssetId);
    if (!updated) throw new Error(t('loadFailed'));
    return updated;
  };

  const readback = useCallback(async (isCurrent: (snapshot: Project) => boolean) => {
    const fresh = await readSourceProject(rawSourceId, asset.id, sessionEpochRef.current, isCurrent);
    if (latestIdentity.current !== identity || !isCurrent(fresh)
      || projectReadRef.current && !projectReadRef.current(fresh, isCurrent)) throw new Error('Asset read is no longer current');
    setProjection({ identity, project: fresh });
    setConfirmationRead(null);
  }, [readSourceProject, rawSourceId, asset.id, identity]);
  const requests = useStudioAssetImageRequests({ kind: sourceKind === 'project' && type !== 'characters' ? assetType : null,
    open: sourceKind === 'project' && type !== 'characters', projectId: rawSourceId, assetId: asset.id, source: assetSource,
    seriesId: ownerSeriesId, readback });
  const pendingPreflight = useRef<object | null>(null);
  const [preparing, setPreparing] = useState<object | null>(null);
  const [preflightFailed, setPreflightFailed] = useState<object | null>(null);
  const [styleDisabledFor, setStyleDisabledFor] = useState<object | null>(null);
  const applyStyle = styleDisabledFor !== identity;
  const generationDraftEpoch = useRef(0);
  const [seriesStyle, setSeriesStyle] = useState<{ identity: object; rootRevision: number; series: Series } | null>(null);
  const sourceSnapshot = projected ?? sourceProject ?? cachedProject;
  const sourceModel = sourceSnapshot?.model_settings?.t2i_model ?? '';
  const strict = usesStudioImageCore(assetType, sourceModel, 'image');
  const qwen = usesStudioQwenImageCore(assetType, sourceModel, 'image');
  const gpt = strict && isStudioGptImageModel(sourceModel);
  const inheritedStyle = seriesStyle?.identity === identity && seriesStyle.rootRevision === sourceSnapshot?.revision
    && seriesStyle.series.id === sourceSnapshot.series_id && seriesStyle.series.revision === sourceSnapshot.read_versions?.series?.revision
    ? seriesStyle.series.art_direction : null;
  const negative = (sourceSnapshot?.art_direction ?? (qwen || gpt ? inheritedStyle : null))?.style_config?.negative_prompt || '';
  const negativeBlocked = strict && applyStyle && (qwen ? Array.from(negative).length > 500 : !gpt && !!negative);
  const imageBusy = requests.busy || preparing === identity;

  const readConfirmedProjectVariants = async (projectId: string, targetAssetId: string, sessionEpoch: number, beforeVariantIds: string[]): Promise<ImageVariant[]> => {
    const updated = await readProjectAsset(projectId, targetAssetId, sessionEpoch);
    if (updated.status === 'needs_confirmation') { setConfirmationRead(true); throw new VariantTaskPendingError(recovery('title')); }
    const freshVariants = primaryImageAsset(updated, type)?.variants ?? [];
    if (!freshVariants.some((variant) => !beforeVariantIds.includes(variant.id))) throw new Error(t("loadFailed"));
    return freshVariants;
  };

  const readAssetAgain = async () => {
    const sessionEpoch = sessionEpochRef.current;
    const environment = getRuntimeEnvironment();
        if (!environment) { setAssetReadFailed(true); return; }
    const live = () => aliveRef.current && sessionEpochRef.current === sessionEpoch && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    setReadingAsset(true); setAssetReadFailed(false);
    try {
      if (sourceKind === 'project') {
        const updated = await readProjectAsset(rawSourceId, asset.id, sessionEpoch);
        if (live()) setConfirmationRead(updated.status === 'needs_confirmation');
      } else if (!onRefresh || !await onRefresh()) throw new Error(recovery('readFailed'));
    } catch { if (live()) setAssetReadFailed(true); }
    finally { if (live()) setReadingAsset(false); }
  };

  const retryVariantReadback = async () => {
    if (!variantReadbackTarget || variantRetryInFlightRef.current) return;
    const target = variantReadbackTarget;
    const sessionEpoch = sessionEpochRef.current;
    const environment = requireActiveRuntimeEnvironment();
    const live = () => aliveRef.current && sessionEpochRef.current === sessionEpoch && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    variantRetryInFlightRef.current = true;
    setGenerating(true);
    try {
      const taskState = target.taskId ? await readVariantTaskState(target.taskId) : 'unknown';
      if (!live()) return;
      if (taskState === 'failed') {
        useProjectStore.getState().setRunningOp(target.key, false);
        forgetLibraryRecovery(target.key);
        setVariantReadbackTarget(null);
        toast.error(t('variantsGenFailed'), { body: t('genFailed') });
        return;
      }
      if (taskState !== 'completed') {
        if (taskState === 'unknown') {
          const updated = await readProjectAsset(target.projectId, target.assetId, sessionEpoch);
          if (!live()) return;
          setConfirmationRead(updated.status === 'needs_confirmation');
        }
        // A fresh asset (including unrelated new variants) never proves this request completed.
        if (live()) toast.error(recovery('readFailed'));
        return;
      }
      const freshVariants = await readConfirmedProjectVariants(target.projectId, target.assetId, sessionEpoch, target.beforeVariantIds);
      if (!live()) return;
      const added = freshVariants.filter(v => !target.beforeVariantIds.includes(v.id));
      setExtraVariants(freshVariants);
      if (added[0]) setActiveVariantId(added[0].id);
      useProjectStore.getState().setRunningOp(target.key, false);
      forgetLibraryRecovery(target.key);
      setVariantReadbackTarget(null);
      if (target.toastId) toast.update(target.toastId, { kind: 'success', title: t('variantsGenerated'), body: added.length ? t('variantsAddedBody', { count: added.length }) : t('variantsRefreshed'), autoCloseMs: 5000 });
    } catch { if (live()) toast.error(recovery('readFailed')); }
    finally { if (live()) { variantRetryInFlightRef.current = false; setGenerating(false); } }
  };

  // 生成更多变体：仅 project 资产可用（series 无生成端点）。复用按项目 batch 生成管线，
  // 完成后 re-fetch 该项目，把新变体并入本地展示并高亮最新一张。
  const handleGenerateVariants = async () => {
    if (variantReadbackTarget) {
      await retryVariantReadback();
      return;
    }
    if (sourceKind !== "project" || generating || !generateKey || runningOps[generateKey]) return;
    const assetId = asset.id;
    const projectId = rawSourceId;
    const sessionEpoch = sessionEpochRef.current;
    const environment = getRuntimeEnvironment();
    if (!environment) { setPreflightFailed(identity); return; }
    const live = () => aliveRef.current && sessionEpochRef.current === sessionEpoch && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    if (type !== 'characters') {
      if (pendingPreflight.current === identity || requests.busy) return;
      pendingPreflight.current = identity; setPreparing(identity); setPreflightFailed(null);
      try {
        const fresh = await readSourceProject(projectId, assetId, sessionEpoch);
        if (!live() || latestIdentity.current !== identity) return;
        const model = fresh.model_settings?.t2i_model || 'wan2.1-t2i';
        if (usesStudioImageCore(assetType, model, 'image')) {
          const qwenRequest = usesStudioQwenImageCore(assetType, model, 'image');
          const gptRequest = isStudioGptImageModel(model);
          const freshInputs = qwenRequest || gptRequest;
          const draftEpoch = generationDraftEpoch.current;
          const requestCurrent = () => live() && latestIdentity.current === identity && generationDraftEpoch.current === draftEpoch;
          setProjection({ identity, project: fresh });
          let inherited: Series | null = null;
          if (freshInputs && applyStyle && !fresh.art_direction && fresh.series_id) {
            const received = await api.getSeries(fresh.series_id) as Series | null;
            if (!requestCurrent()) return;
            const captured = fresh.read_versions?.series;
            if (!received || received.id !== fresh.series_id || captured?.id !== received.id
              || !Number.isSafeInteger(received.revision) || received.revision < 0 || captured.revision !== received.revision) throw new Error('Series style is not current');
            inherited = received;
            // Library may display an uncached source. This proof belongs to its drawer, never Studio's current project.
            setSeriesStyle({ identity, rootRevision: fresh.revision, series: received });
          }
          const freshNegative = (fresh.art_direction ?? inherited?.art_direction)?.style_config?.negative_prompt || '';
          if (applyStyle && (qwenRequest ? Array.from(freshNegative).length > 500 : !gptRequest && !!freshNegative)) return;
          const submissionCurrent = () => {
            if (!requestCurrent() || localRead.current?.identity !== identity || localRead.current.project !== fresh
              || fresh.sharedAssetsNeedRefresh || fresh.readEnvironment !== environment.key) return false;
            const state = useProjectStore.getState();
            const known = state.currentProject?.id === projectId ? state.currentProject : state.projects.find(item => item.id === projectId);
            const origin = sourceProjectRef.current;
            const matches = (candidate?: Project) => {
              if (!candidate || candidate.revision < fresh.revision) return true;
              const pool = type === 'scenes' ? candidate.scenes : candidate.props;
              return candidate.id === fresh.id && candidate.revision === fresh.revision
                && (candidate.series_id ?? null) === (fresh.series_id ?? null)
                && pool.some(item => item.id === assetId && (item.source ?? 'episode') === assetSource)
                && (candidate.model_settings?.t2i_model || 'wan2.1-t2i') === model
                && (!applyStyle || JSON.stringify(candidate.art_direction ?? null) === JSON.stringify(fresh.art_direction ?? null));
            };
            if (!matches(known) || !matches(origin)) return false;
            const watermarks = observeReadVersions(state.readWatermarks,
              [...state.projects, ...(state.currentProject ? [state.currentProject] : []), ...(origin ? [origin] : [])], environment.key);
            const floor = watermarks[environment.key] ?? { series: {} };
            if (!canApplyRead(known, fresh, floor, environment.key) || !canApplyRead(origin, fresh, floor, environment.key)) return false;
            const cachedSeries = state.currentSeries;
            return !inherited || !cachedSeries || cachedSeries.id !== inherited.id || cachedSeries.revision < inherited.revision
              || cachedSeries.revision === inherited.revision && JSON.stringify(cachedSeries.art_direction ?? null) === JSON.stringify(inherited.art_direction ?? null);
          };
          if (freshInputs && !submissionCurrent()) return;
          await requests.submit({ asset_id: assetId, asset_type: type === 'scenes' ? 'scene' : 'prop', generation_type: 'all',
            prompt: '', apply_style: applyStyle, negative_prompt: '', batch_size: VARIANT_BATCH, model_name: model }, freshInputs ? submissionCurrent : undefined);
          return;
        }
      } catch { if (live()) setPreflightFailed(identity); return; }
      finally { if (pendingPreflight.current === identity) pendingPreflight.current = null; if (live()) setPreparing(null); }
    }
    const beforeVariantIds = variants.map((variant) => variant.id);
    useProjectStore.getState().setRunningOp(generateKey, true);
    setGenerating(true);
    const tid = toast.progress(t("generatingVariants"), {
      body: t("generatingVariantsBody", { name: asset.name, count: VARIANT_BATCH }),
    });
    let taskId: string | undefined;
    let ownedRecovery: Extract<LibraryRecoveryRecord, { kind: 'variant' }> = { kind: 'variant', key: generateKey, projectId, assetId, assetType, beforeVariantIds };
    rememberLibraryRecovery(ownedRecovery);
    const ownsRecovery = () => readVariantRecovery(generateKey) === ownedRecovery;
    const retainRecovery = () => {
      if (!ownsRecovery()) return false;
      ownedRecovery = { ...ownedRecovery, taskId };
      rememberLibraryRecovery(ownedRecovery);
      return true;
    };
    try {
      const resp = await api.generateAsset(
        projectId,
        assetId,
        assetType,
        "",
        undefined,
        "all",
        "",
        true,
        "",
        VARIANT_BATCH
      );
      if (!ownsRecovery()) return;
      const receivedTaskId = (resp as { _task_id?: unknown } | undefined)?._task_id;
      if (typeof receivedTaskId !== "string" || !receivedTaskId.trim()) throw new Error(t("loadFailedBody"));
      taskId = receivedTaskId;
      if (taskId) {
        retainRecovery();
        if (!live()) return;
        const done = await pollUntilDone(taskId, live);
        if (!done) return; // 已卸载
      }
      if (!live() || !ownsRecovery() || currentAssetIdRef.current !== assetId) return;
      let freshVariants: ImageVariant[];
      try {
        freshVariants = await readConfirmedProjectVariants(projectId, assetId, sessionEpoch, beforeVariantIds);
      } catch (error) {
        if (!live() || !ownsRecovery()) return;
        const msg = error instanceof Error ? error.message : t("loadFailedBody");
        const recovery = { kind: "variant" as const, key: generateKey, projectId, assetId, assetType, taskId, beforeVariantIds, toastId: tid };
        ownedRecovery = recovery;
        rememberLibraryRecovery(ownedRecovery);
        if (aliveRef.current && sessionEpochRef.current === sessionEpoch) {
          setGenerating(false);
          setVariantReadbackTarget(recovery);
          toast.update(tid, { kind: "error", title: t("loadFailed"), body: msg, autoCloseMs: 0 });
        }
        return;
      }
      if (!live() || !ownsRecovery() || currentAssetIdRef.current !== assetId) return;
      const added = freshVariants.filter((v) => !beforeVariantIds.includes(v.id));
      setExtraVariants(freshVariants);
      if (added[0]) setActiveVariantId(added[0].id);
      useProjectStore.getState().setRunningOp(generateKey, false);
      forgetLibraryRecovery(generateKey);
      toast.update(tid, {
        kind: "success",
        title: t("variantsGenerated"),
        body: added.length ? t("variantsAddedBody", { count: added.length }) : t("variantsRefreshed"),
        autoCloseMs: 5000,
      });
    } catch (e) {
      if (!ownsRecovery()) return;
      retainRecovery();
      if (!live()) return;
      if (taskId && e instanceof VariantTaskPendingError) {
        try {
          const updated = await readProjectAsset(projectId, assetId, sessionEpoch);
          if (!live()) return;
          setConfirmationRead(updated.status === 'needs_confirmation');
        } catch { if (!live()) return; }
      }
      const msg = e instanceof Error ? e.message : t("genFailed");
      if (!(e instanceof VariantTaskFailedError)) {
        const recovery = { kind: "variant" as const, key: generateKey, projectId, assetId, assetType, taskId, beforeVariantIds, toastId: tid };
        ownedRecovery = recovery;
        rememberLibraryRecovery(ownedRecovery);
        if (aliveRef.current && sessionEpochRef.current === sessionEpoch) {
          setGenerating(false);
          setVariantReadbackTarget(recovery);
          toast.update(tid, { kind: "error", title: t("loadFailed"), body: msg, autoCloseMs: 0 });
        }
        return;
      }
      useProjectStore.getState().setRunningOp(generateKey, false);
      forgetLibraryRecovery(generateKey);
      if (aliveRef.current && sessionEpochRef.current === sessionEpoch) toast.update(tid, { kind: "error", title: t("variantsGenFailed"), body: msg, autoCloseMs: 0 });
    } finally {
      if (live()) setGenerating(false);
    }
  };

  // 提升到全局：把 project/series 来源资产 deep-copy 进全局共享池（global 来源不显示该按钮）。
  // 成功后 toast 并回调父层刷新（新入池资产即出现在「全局 / 共享」分组）。
  const retryPromoteReadback = async () => {
    if (!promoteReadbackTarget || promoting) return;
    setPromoting(true);
    try {
      const sessionEpoch = sessionEpochRef.current;
      const confirmed = await onPromoted?.(promoteReadbackTarget.assetType, promoteReadbackTarget.assetId);
      if (sessionEpochRef.current !== sessionEpoch) return;
      if (!confirmed) {
        toast.error(t("loadFailed"), { body: t("loadFailedBody") });
        return;
      }
      useProjectStore.getState().setRunningOp(promoteReadbackTarget.key, false);
      forgetLibraryRecovery(promoteReadbackTarget.key);
      toast.success(t("promoteSuccess"), { body: t("promoteSuccessBody", { name: promoteReadbackTarget.name }) });
      setPromoteReadbackTarget(null);
    } finally {
      if (aliveRef.current) setPromoting(false);
    }
  };

  // 提升到全局：把 project/series 来源资产 deep-copy 进全局共享池（global 来源不显示该按钮）。
  // 成功后 toast 并回调父层刷新（新入池资产即出现在「全局 / 共享」分组）。
  const handlePromote = async () => {
    if (promoteReadbackTarget) {
      await retryPromoteReadback();
      return;
    }
    if (sourceKind === "global" || promoting || !promoteKey || runningOps[promoteKey]) return;
    const sessionEpoch = sessionEpochRef.current;
    useProjectStore.getState().setRunningOp(promoteKey, true);
    setPromoting(true);
    let acceptedPromoteRecovery: Extract<LibraryRecoveryRecord, { kind: "promote" }> | null = null;
    try {
      const promoted = await api.promoteAssetToLibrary(sourceKind, rawSourceId, assetType, asset.id);
      const promotedId = (promoted as { id?: unknown } | null)?.id;
      if (typeof promotedId !== "string" || !promotedId.trim()) throw new Error(t("promoteFailed"));
      const recovery = { kind: "promote" as const, key: promoteKey, assetType, assetId: promotedId, name: asset.name };
      acceptedPromoteRecovery = recovery;
      rememberLibraryRecovery(recovery);
      if (!aliveRef.current || currentAssetIdRef.current !== asset.id || sessionEpochRef.current !== sessionEpoch) return;
      const confirmed = await onPromoted?.(assetType, promotedId);
      if (!aliveRef.current || currentAssetIdRef.current !== asset.id || sessionEpochRef.current !== sessionEpoch) {
        rememberLibraryRecovery(recovery);
        return;
      }
      if (!confirmed) {
        rememberLibraryRecovery(recovery);
        setPromoteReadbackTarget(recovery);
        toast.error(t("loadFailed"), { body: t("loadFailedBody") });
        return;
      }
      useProjectStore.getState().setRunningOp(promoteKey, false);
      forgetLibraryRecovery(promoteKey);
      toast.success(t("promoteSuccess"), { body: t("promoteSuccessBody", { name: asset.name }) });
    } catch (e) {
      if (acceptedPromoteRecovery) {
        rememberLibraryRecovery(acceptedPromoteRecovery);
        if (aliveRef.current && currentAssetIdRef.current === asset.id && sessionEpochRef.current === sessionEpoch) {
          setPromoteReadbackTarget(acceptedPromoteRecovery);
          toast.error(t("loadFailed"), { body: t("loadFailedBody") });
        }
        return;
      }
      useProjectStore.getState().setRunningOp(promoteKey, false);
      forgetLibraryRecovery(promoteKey);
      const msg = e instanceof Error ? e.message : t("promoteFailed");
      toast.error(t("promoteFailed"), { body: msg });
    } finally {
      if (aliveRef.current) setPromoting(false);
    }
  };

  return (
    <aside
      ref={asideRef}
      tabIndex={-1}
      className="fixed inset-0 z-50 w-full md:static md:inset-auto md:z-auto md:w-[340px] flex-shrink-0 h-full flex flex-col overflow-y-auto bg-surface border-l border-glass-border shadow-2xl atelier-reveal focus:outline-none"
      aria-label={t("inspectorAria")}
    >
      {/* Hero — 磨砂铺底 + object-contain：三视图/横竖混杂的资产完整展示不裁切（避免裁头）。 */}
      <div className="relative aspect-[3/4] bg-surface-inset overflow-hidden flex-shrink-0">
        {heroUrl ? (
          <>
            <img
              src={getLibraryAssetUrl(heroUrl)}
              alt=""
              aria-hidden="true"
              className="absolute inset-0 w-full h-full object-cover blur-xl scale-110 opacity-40"
            />
            <img src={getLibraryAssetUrl(heroUrl)} alt={asset.name} className="relative w-full h-full object-contain" />
          </>
        ) : (
          // 无图像：确定性渐变封面 + 颗粒，替代发灰占位图标。
          <>
            <div className="absolute inset-0" style={{ background: coverGradient(asset.id) }} aria-hidden="true" />
            <div
              className="absolute inset-0 mix-blend-overlay opacity-60"
              style={{ backgroundImage: GRAIN_URL }}
              aria-hidden="true"
            />
            <div className="relative w-full h-full grid place-items-center p-6 text-center">
              <span className="font-display atelier-display text-2xl font-semibold text-foreground tracking-tight">
                {asset.name}
              </span>
            </div>
          </>
        )}
        {/* amber halation overlay — only on starred (atelier signature; amber = starred). */}
        {starred && (
          <div
            className="pointer-events-none absolute inset-0 shadow-[inset_0_0_60px_-10px_var(--color-status-starred-bg)]"
            aria-hidden="true"
          />
        )}
        <button
          type="button"
          onClick={onToggleStar}
          aria-pressed={starred}
          aria-label={starred ? t("unstar") : t("star")}
          className={`absolute top-3 left-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full font-mono text-[0.625rem] font-bold uppercase tracking-[0.1em] backdrop-blur-md border transition-colors ${
            starred
              ? "text-status-starred-fg bg-status-starred-bg border-status-starred-border"
              : "text-text-secondary bg-black/40 border-transparent hover:text-foreground"
          }`}
        >
          <Star size={12} className={starred ? "fill-current" : ""} />
          {starred ? t("starred") : t("star")}
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("closeInspector")}
          className="absolute top-3 right-3 w-8 h-8 rounded-full grid place-items-center bg-black/50 backdrop-blur-md text-foreground hover:bg-black/70 transition-colors"
        >
          <X size={15} />
        </button>
      </div>

      <div className="p-5 flex flex-col gap-5">
        <div>
          <div className="font-display atelier-display text-xl font-semibold text-foreground tracking-tight">
            {asset.name}
          </div>
          <div className="font-mono text-[0.59375rem] text-text-muted tracking-[0.06em] uppercase mt-1.5">
            {TYPE_LABEL[type]} · {sourceName} · {t("variantCount", { count: variants.length })}
          </div>
        </div>

        {/* Variant strip */}
        {variants.length > 1 && (
          <div>
            <div className="font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary mb-2.5">
              {t("variantsSection")}
            </div>
            <div className="grid grid-cols-4 gap-2">
              {variants.map((v) => {
                const on = v.id === activeVariant?.id;
                return (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => setActiveVariantId(v.id)}
                    aria-current={on ? "true" : undefined}
                    className={`relative aspect-square rounded-md overflow-hidden transition-transform hover:-translate-y-0.5 ${
                      on ? "ring-2 ring-primary" : "ring-1 ring-glass-border"
                    }`}
                  >
                    <img src={getLibraryAssetUrl(v.url)} alt={t("variantAlt")} className="w-full h-full object-cover" />
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Metadata */}
        <div>
          <div className="font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary mb-2.5">
            {t("metadataSection")}
          </div>
          <div className="flex flex-col">
            {metaRows.map((row) => (
              <div
                key={row.label}
                className="flex justify-between items-center py-2 border-b border-glass-border last:border-b-0 text-[0.8125rem]"
              >
                <span className="font-mono text-[0.625rem] text-text-muted tracking-[0.04em]">{row.label}</span>
                <span className="text-foreground font-medium">{row.value}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Prompt */}
        {prompt && (
          <div>
            <div className="font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary mb-2.5">
              {t("promptSection")}
            </div>
            <div className="bg-surface-inset rounded-lg p-3.5 text-[0.8125rem] leading-relaxed text-text-secondary border-l-2 border-status-starred-border">
              {prompt}
            </div>
          </div>
        )}

        {needsConfirmation && <AssetRecoveryNotice onRead={variantReadbackTarget ? undefined : readAssetAgain} reading={readingAsset} readFailed={assetReadFailed} />}
        {sourceKind === 'project' && type !== 'characters' && <StudioImageRequestResults requests={requests} />}
        {preflightFailed === identity && <p role="status" className="text-xs text-amber-300">{imageFeedback('requestNotSubmitted')}</p>}
        {!variantReadbackTarget && gpt && applyStyle && negative && <p role="status" className="text-xs text-text-secondary">{imageFeedback('gptNegativeNotApplied')}</p>}
        {!variantReadbackTarget && negativeBlocked && <div className="text-xs text-amber-300 space-y-2">
          <p role="status">{imageFeedback(qwen ? 'imageNegativeTooLong' : 'negativeUnsupported')}</p>
          <button onClick={() => { generationDraftEpoch.current += 1; setStyleDisabledFor(identity); }} className="underline">{imageFeedback('disableStyleForRequest')}</button>
        </div>}
        {/* Actions */}
        {/*
          生成更多变体：project 资产复用「按项目 batch 生成」管线，对当前 asset append 新变体
          （不替换），完成后并入本地展示并高亮最新一张；series 资产无生成端点（生成需在具体
          项目内进行），故置灰并提示在剧集内生成。「用于分镜」按钮已移除（占位、无落地路径）。
        */}
        <div className="flex flex-col gap-2">
          {sourceKind === "project" ? (
            <button
              type="button"
              onClick={handleGenerateVariants}
              disabled={generating || (!variantReadbackTarget && (imageBusy || negativeBlocked || !!runningOps[generateKey]))}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-on-accent text-sm font-semibold hover:bg-primary-hover transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {generating ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
              {variantReadbackTarget ? tp("readAgain") : generating ? t("generating") : needsConfirmation ? recovery("newGenerate") : t("generateMoreVariants")}
            </button>
          ) : (
            <button
              type="button"
              disabled
              title={t("genInEpisodeTooltip")}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-surface-inset border border-glass-border text-text-muted text-sm font-medium cursor-not-allowed disabled:opacity-60"
            >
              <Sparkles size={15} />
              {t("generateMoreVariants")}
              <span className="inline-flex items-center rounded-full px-1.5 py-0.5 font-mono text-[0.53125rem] font-semibold tracking-[0.06em] text-status-pending-fg bg-status-pending-bg border border-status-pending-border">
                {t("genInEpisodeBadge")}
              </span>
            </button>
          )}
          {/* 提升到全局：project/series 来源可用；global 来源隐藏（无需自我提升）。 */}
          {sourceKind !== "global" && (
            <button
              type="button"
              onClick={handlePromote}
              disabled={promoting || (!promoteReadbackTarget && !!runningOps[promoteKey])}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-surface-inset border border-glass-border text-foreground text-sm font-medium hover:bg-hover-bg transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {promoting ? <Loader2 size={15} className="animate-spin" /> : <Globe size={15} />}
              {promoteReadbackTarget ? tp("readAgain") : promoting ? t("promoting") : t("promoteToGlobal")}
            </button>
          )}
          {/* 下载：v1 实做 */}
          <button
            type="button"
            onClick={handleDownload}
            disabled={!heroUrl}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-surface-inset border border-glass-border text-foreground text-sm font-medium hover:bg-hover-bg transition-colors disabled:opacity-40"
          >
            <Download size={15} />
            {t("download")}
          </button>
        </div>
      </div>
    </aside>
  );
}
