"use client";
/**
 * T2ISubsection — t2i_i2v 模式下"先生图、再生视频"的工作流容器
 * (Issue 10 / Task #2).
 *
 * 状态机（C 方案）：
 *   - hero 态：!hasActiveFrame  → 大号 Step 1-only CTA（生成 / 上传双口）
 *   - compact 态：hasActiveFrame → 单行折叠 + ▼/+ 控件，让位给下方 I2V Params
 *
 * 触发降级：用户删空所有 T2I 且无 storyboard frame → 自动回到 hero 态。
 *
 * 不维护"先 T2I 再 I2V"的强制阻断 — 已有 storyboard frame 时直接进入
 * compact 态、Step 2 立刻可用；只有真正空 shot 才看到 hero。
 */
import { useEffect, useRef, useState } from "react";
import {
    Loader2, Plus, X, Sparkles, Upload, Image as ImageIcon, Check, Pin, ChevronDown, ChevronUp,
    RefreshCw,
} from "lucide-react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { PendingTaskAffordance } from "@/components/shared/PendingTaskAffordance";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import { debugLog } from "@/lib/debugLog";

const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;
const ALLOWED_UPLOAD_TYPES = ["image/jpeg", "image/png", "image/webp"];

/** Render any T2IUploadError (legacy enum or rich {code, detail}) into a
 *  user-facing string. Always shows the backend detail if present so users
 *  can self-diagnose (e.g. "frame not saved yet" vs "permission denied"). */
function formatUploadError(err: T2IUploadError, t: ReturnType<typeof useTranslations>): string {
    const code = typeof err === "string" ? err : err.code;
    const detail = typeof err === "string" ? "" : err.detail;
    const base =
        code === "type" ? t("t2iHeroUploadInvalidType") :
        code === "size" ? t("t2iHeroUploadTooLarge") :
        code === "not_found" ? t("t2iUploadErrNotFound") :
        code === "server" ? t("t2iUploadErrServer") :
        t("t2iHeroUploadFailed");
    return detail ? `${base}（${detail}）` : base;
}

/** Upload failure surface. `code` drives default i18n message; `detail` is
 *  the actual backend reason (HTTP status text or response body) so the
 *  user / dev can diagnose instead of staring at a generic "请重试". */
export type T2IUploadError =
    | "type"
    | "size"
    | "network"
    | { code: "type" | "size" | "network" | "not_found" | "server"; detail: string };

interface T2ISubsectionProps {
    imageUrls: string[];
    selectedIndex: number;
    /** Whether shot.imageUrl (Storyboard stage render) exists — when true,
     *  the compact view marks the storyboard frame with a 📌 badge in the
     *  thumb at index 0. */
    storyboardFrameUrl?: string;
    /** Whether the user typed a prompt — gates the hero "Generate" CTA so
     *  we never silently early-return like the legacy +Gen tile did. */
    promptIsEmpty: boolean;
    generationDisabled?: boolean;
    /** True while a T2I generation is in flight. */
    generating: boolean;
    inFlightTaskId?: string;
    inFlightStatus?: "pending" | "processing" | "completed" | "failed";
    onSelect: (index: number) => void;
    onRemove: (index: number) => void;
    /** Generate via prompt (renderFrame). Host validates prompt-non-empty
     *  itself but we keep `promptIsEmpty` here for the disabled tooltip. */
    onGenerate: () => void;
    /** Upload an external image as a first-frame candidate. Returns the
     *  upload error code (or void if success). Host owns the actual
     *  api.uploadT2IFrame call + state mutation. */
    onUpload: (file: File) => Promise<T2IUploadError | void>;
    lastFrameEnabled?: boolean;
    lastFrameUrl?: string;
    onLastFrameUpload?: (file: File) => Promise<T2IUploadError | void>;
    onLastFrameClear?: () => void;
    resolveUrl?: (url: string) => string;
}

export default function T2ISubsection({
    imageUrls,
    selectedIndex,
    storyboardFrameUrl,
    promptIsEmpty,
    generationDisabled = false,
    generating,
    inFlightTaskId,
    inFlightStatus,
    onSelect,
    onRemove,
    onGenerate,
    onUpload,
    lastFrameEnabled = false,
    lastFrameUrl,
    onLastFrameUpload,
    onLastFrameClear,
    resolveUrl,
}: T2ISubsectionProps) {
    const t = useTranslations("storyboardR2V");
    // resolveUrl is kept in the prop API (still passed by host for back-compat)
    // but PreviewImage handles URL resolution internally now (Issue 14), so we
    // don't thread `display` through any longer.
    void resolveUrl;
    // 是否有 storyboard frame 隐式占位（不进 imageUrls，但算 Step 1 完成）
    const hasStoryboardFrame = !!storyboardFrameUrl;
    const hasGeneratedOrUploaded = imageUrls.length > 0;
    // Step 1 完成的判据：有任何 active frame 来源（T2I or storyboard）
    const stepOneDone = hasGeneratedOrUploaded || hasStoryboardFrame;

    const renderLastFrameControl = (disabled: boolean) => lastFrameEnabled && onLastFrameUpload ? (
        <LastFrameControl
            t={t}
            url={lastFrameUrl}
            uploadingDisabled={disabled}
            onUpload={onLastFrameUpload}
            onClear={onLastFrameClear}
        />
    ) : null;

    if (!stepOneDone) {
        const uploadDisabled = generating;
        return (
            <>
                <Hero
                    t={t}
                    promptIsEmpty={promptIsEmpty}
                    generationDisabled={generationDisabled}
                    generating={generating}
                    inFlightTaskId={inFlightTaskId}
                    inFlightStatus={inFlightStatus}
                    onGenerate={onGenerate}
                    onUpload={onUpload}
                />
                {renderLastFrameControl(uploadDisabled)}
            </>
        );
    }

    return (
        <Compact
            t={t}
            imageUrls={imageUrls}
            selectedIndex={selectedIndex}
            storyboardFrameUrl={storyboardFrameUrl}
            generating={generating}
            inFlightTaskId={inFlightTaskId}
            inFlightStatus={inFlightStatus}
            promptIsEmpty={promptIsEmpty}
            generationDisabled={generationDisabled}
            onSelect={onSelect}
            onRemove={onRemove}
            onGenerate={onGenerate}
            onUpload={onUpload}
            lastFrameEnabled={lastFrameEnabled}
            lastFrameUrl={lastFrameUrl}
            onLastFrameUpload={onLastFrameUpload}
            onLastFrameClear={onLastFrameClear}
        />
    );
}

function LastFrameControl({
    t, url, uploadingDisabled, onUpload, onClear,
}: {
    t: ReturnType<typeof useTranslations>;
    url?: string;
    uploadingDisabled: boolean;
    onUpload: (file: File) => Promise<T2IUploadError | void>;
    onClear?: () => void;
}) {
    const inputRef = useRef<HTMLInputElement | null>(null);
    const [uploading, setUploading] = useState(false);
    const [uploadError, setUploadError] = useState<T2IUploadError | null>(null);

    const handleFile = async (file: File) => {
        setUploadError(null);
        if (!ALLOWED_UPLOAD_TYPES.includes(file.type)) {
            setUploadError("type");
            return;
        }
        if (file.size > MAX_UPLOAD_BYTES) {
            setUploadError("size");
            return;
        }
        setUploading(true);
        try {
            const result = await onUpload(file);
            if (result) setUploadError(result);
        } catch (error) {
            debugLog.error("Studio", "Last frame upload failed", error);
            setUploadError("network");
        } finally {
            setUploading(false);
        }
    };

    return (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-glass-border bg-black/20 px-3 py-2">
            <span className="font-mono text-chrome-sm uppercase tracking-tight text-text-muted">
                {t("lastFrameLabel")}
            </span>
            {url ? (
                <span className="max-w-[14rem] truncate font-mono text-chrome-sm text-text-secondary" title={url}>
                    {url}
                </span>
            ) : (
                <span className="font-sans text-body-sm text-text-muted">{t("lastFrameEmpty")}</span>
            )}
            <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={uploadingDisabled || uploading}
                className="ml-auto inline-flex min-h-[28px] items-center gap-1.5 rounded-md border border-glass-border bg-black/20 px-2.5 py-1 font-mono text-chrome-sm text-text-secondary transition-colors hover:border-primary/45 hover:text-primary disabled:opacity-50"
            >
                {uploading ? <Loader2 size={12} className="animate-spin" aria-hidden="true" /> : <Upload size={12} aria-hidden="true" />}
                {url ? t("lastFrameReplace") : t("lastFrameUpload")}
            </button>
            {url && onClear ? (
                <button
                    type="button"
                    onClick={onClear}
                    disabled={uploading}
                    className="inline-flex min-h-[28px] items-center rounded-md px-2.5 py-1 font-mono text-chrome-sm text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground disabled:opacity-50"
                >
                    {t("lastFrameClear")}
                </button>
            ) : null}
            <input
                ref={inputRef}
                type="file"
                accept={ALLOWED_UPLOAD_TYPES.join(",")}
                className="sr-only"
                onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void handleFile(file);
                    event.target.value = "";
                }}
            />
            {uploadError ? (
                <p role="alert" className="basis-full font-sans text-body-sm text-status-failed-fg">
                    {formatUploadError(uploadError, t)}
                </p>
            ) : null}
        </div>
    );
}

// ────────────────────────────────────────────────────────────────────
// Hero — Step 1 only, no active frame yet.
// 双 CTA（生成 + 上传），含拖拽到面板的支持；按 grill Q2 推荐。
// ────────────────────────────────────────────────────────────────────

interface HeroProps {
    t: ReturnType<typeof useTranslations>;
    promptIsEmpty: boolean;
    generationDisabled?: boolean;
    generating: boolean;
    inFlightTaskId?: string;
    inFlightStatus?: "pending" | "processing" | "completed" | "failed";
    onGenerate: () => void;
    onUpload: (file: File) => Promise<T2IUploadError | void>;
}

function Hero({
    t, promptIsEmpty, generationDisabled = false, generating, inFlightTaskId, inFlightStatus, onGenerate, onUpload,
}: HeroProps) {
    const [uploading, setUploading] = useState(false);
    const [uploadError, setUploadError] = useState<T2IUploadError | null>(null);
    const [dragHot, setDragHot] = useState(false);
    const inputRef = useRef<HTMLInputElement | null>(null);

    const handleFile = async (file: File) => {
        setUploadError(null);
        if (!ALLOWED_UPLOAD_TYPES.includes(file.type)) {
            setUploadError("type");
            return;
        }
        if (file.size > MAX_UPLOAD_BYTES) {
            setUploadError("size");
            return;
        }
        setUploading(true);
        try {
            const result = await onUpload(file);
            if (result) setUploadError(result);
        } catch (e) {
            debugLog.error("Studio", "T2I upload failed", e);
            setUploadError("network");
        } finally {
            setUploading(false);
        }
    };

    const uploadDisabled = uploading || generating;
    const generateDisabled = generationDisabled || promptIsEmpty || generating || uploading;

    return (
        <div
            // Hero 容器：玻璃边 + 内阴影 + 拖拽时高亮边。
            // 用 padding 而不是固定高度，长短文案都能呼吸。
            // motion-safe 兼容 reduced-motion 用户。
            onDragOver={(e) => {
                e.preventDefault();
                if (!uploadDisabled) setDragHot(true);
            }}
            onDragLeave={() => setDragHot(false)}
            onDrop={(e) => {
                e.preventDefault();
                setDragHot(false);
                if (uploadDisabled) return;
                const file = e.dataTransfer.files?.[0];
                if (file) void handleFile(file);
            }}
            className={clsx(
                "relative rounded-lg border px-5 py-6 transition-colors duration-fast ease-out-quart motion-safe:animate-[shotPanelIn_220ms_cubic-bezier(0.22,1,0.36,1)_both]",
                dragHot
                    ? "border-primary/55 bg-primary/[0.08]"
                    : "border-glass-border bg-black/30",
            )}
        >
            <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-5">
                {/* 视觉锚点 — 不是 hero metric template，是一个"占位帧"
                    缩略，提示"这里应该有一张图"。生成中时叠加 spinner。 */}
                <div className="relative grid h-[88px] w-[120px] shrink-0 place-items-center overflow-hidden rounded-md border border-glass-border bg-black/40">
                    {generating ? (
                        <PendingTaskAffordance
                            statusLabel={inFlightStatus === "pending" ? "Queued" : "Generating"}
                            taskId={inFlightTaskId}
                            compact
                        />
                    ) : (
                        <ImageIcon size={26} aria-hidden="true" className="text-text-muted/55" />
                    )}
                </div>

                <div className="min-w-0 flex-1 space-y-1.5">
                    {/* Step eyebrow — chrome tier，不是 banner */}
                    <div className="font-mono text-chrome-sm font-medium uppercase tracking-tight text-primary/90">
                        {t("t2iHeroEyebrow")}
                    </div>
                    {/* Title — display tier，是 hero 的视觉焦点 */}
                    <div className="font-display text-display-sm font-semibold text-foreground">
                        {t("t2iHeroTitle")}
                    </div>
                    <p className="max-w-[58ch] font-sans text-body-sm leading-relaxed text-text-secondary">
                        {t("t2iHeroBody")}
                    </p>

                    {/* CTA row — 主生成 + 次上传 + 拖拽 hint */}
                    <div className="flex flex-wrap items-center gap-2 pt-2">
                        <button
                            type="button"
                            onClick={onGenerate}
                            disabled={generateDisabled}
                            title={promptIsEmpty ? t("t2iHeroGenerateDisabledTooltip") : t("t2iHeroGenerateEnabledTooltip")}
                            className="btn-tip inline-flex min-h-[36px] items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 font-display text-display-sm font-semibold text-white shadow-[var(--btn-pri-glow),inset_0_1px_0_rgba(255,255,255,0.22)] transition-all duration-fast ease-out-quart hover:bg-primary/92 active:scale-[0.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                            {generating ? (
                                <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                            ) : (
                                <Sparkles size={14} aria-hidden="true" />
                            )}
                            {t("t2iHeroGenerateLabel")}
                        </button>

                        <button
                            type="button"
                            onClick={() => inputRef.current?.click()}
                            disabled={uploadDisabled}
                            className="inline-flex min-h-[36px] items-center gap-1.5 rounded-md border border-glass-border bg-black/20 px-3 py-1.5 font-mono text-chrome font-medium text-text-secondary transition-colors duration-fast ease-out-quart hover:border-primary/45 hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                            {uploading ? (
                                <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                            ) : (
                                <Upload size={13} aria-hidden="true" />
                            )}
                            {uploading ? t("t2iHeroUploadingLabel") : t("t2iHeroUploadLabel")}
                        </button>

                        <span className="font-mono text-chrome-sm tracking-tight text-text-muted">
                            {t("t2iHeroDropHint")}
                        </span>

                        <input
                            ref={inputRef}
                            type="file"
                            accept={ALLOWED_UPLOAD_TYPES.join(",")}
                            className="sr-only"
                            onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) void handleFile(file);
                                // Allow re-picking the same file
                                e.target.value = "";
                            }}
                        />
                    </div>

                    {uploadError ? (
                        <p
                            role="alert"
                            className="pt-1 font-sans text-body-sm text-status-failed-fg"
                        >
                            {formatUploadError(uploadError, t)}
                        </p>
                    ) : null}
                </div>
            </div>
        </div>
    );
}

// ────────────────────────────────────────────────────────────────────
// Compact — Step 1 done, T2I subsection collapsed to a single row.
// 默认折叠（节省垂直空间）；processing 时自动展开。
// ────────────────────────────────────────────────────────────────────

interface CompactProps {
    t: ReturnType<typeof useTranslations>;
    imageUrls: string[];
    selectedIndex: number;
    storyboardFrameUrl?: string;
    generating: boolean;
    inFlightTaskId?: string;
    inFlightStatus?: "pending" | "processing" | "completed" | "failed";
    promptIsEmpty: boolean;
    generationDisabled?: boolean;
    onSelect: (index: number) => void;
    onRemove: (index: number) => void;
    onGenerate: () => void;
    onUpload: (file: File) => Promise<T2IUploadError | void>;
    lastFrameEnabled?: boolean;
    lastFrameUrl?: string;
    onLastFrameUpload?: (file: File) => Promise<T2IUploadError | void>;
    onLastFrameClear?: () => void;
}

function Compact({
    t, imageUrls, selectedIndex, storyboardFrameUrl, generating,
    inFlightTaskId, inFlightStatus, promptIsEmpty, generationDisabled = false,
    onSelect, onRemove, onGenerate, onUpload, lastFrameEnabled = false, lastFrameUrl, onLastFrameUpload, onLastFrameClear,
}: CompactProps) {
    // 数据模型：active frame 来源优先级 = getActiveT2IImageUrl 然后 fallback shot.imageUrl。
    // 简化 v1 决定：storyboard frame thumb 只在 T2I 历史为空时显示（自动成为 active）；
    // 用户一旦生成/上传 T2I，storyboard thumb 让位给 T2I 列表。若用户想"切回 storyboard
    // frame"，删空 T2I 即可 — 数据模型不需要新增 useStoryboardFrame 字段。
    const safeIndex = imageUrls.length === 0
        ? -1
        : Math.max(0, Math.min(selectedIndex, imageUrls.length - 1));
    const showStoryboardThumb = imageUrls.length === 0 && !!storyboardFrameUrl;
    const storyboardActive = showStoryboardThumb;

    // PR-3 grill Q14 (C) — candidates row default 展开:
    // 让用户立刻看到"已有 N 张可选 / 当前正在用第几张"，建立"first
    // frame 是多变体"的 mental model. 之前 default collapsed 导致
    // candidates 在 hover popover 后才被发现.
    const [expanded, setExpanded] = useState(true);
    // processing 时强制展开，让用户看见 spinner
    const forcedOpen = generating && (inFlightStatus === "pending" || inFlightStatus === "processing");
    const open = expanded || forcedOpen;
    const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
    const [menuOpen, setMenuOpen] = useState(false);
    const [uploadError, setUploadError] = useState<T2IUploadError | null>(null);
    const [uploading, setUploading] = useState(false);
    const inputRef = useRef<HTMLInputElement | null>(null);
    const closeTimer = useRef<number | null>(null);

    // 自动展开后用户主动收起的意图保留（避免 inFlightStatus 再次变 processing
    // 又强制覆盖用户的折叠选择）— 不实现，因为 forcedOpen 是单向的"安全网"。

    // 计数：T2I 历史 + （仅当其为唯一来源时）storyboard frame 隐式占 1 张
    const total = imageUrls.length + (showStoryboardThumb ? 1 : 0);
    const currentLabel = storyboardActive
        ? "📌"
        : safeIndex >= 0 ? `${safeIndex + 1}` : "—";
    const countLabel = total > 0
        ? t("t2iStepOneCount", { count: total, current: currentLabel })
        : t("t2iStepOneEmptyMeta");

    const openMenu = () => {
        if (closeTimer.current) {
            window.clearTimeout(closeTimer.current);
            closeTimer.current = null;
        }
        setMenuOpen(true);
    };
    const scheduleCloseMenu = () => {
        if (closeTimer.current) window.clearTimeout(closeTimer.current);
        closeTimer.current = window.setTimeout(() => setMenuOpen(false), 300);
    };
    useEffect(() => () => {
        if (closeTimer.current) window.clearTimeout(closeTimer.current);
    }, []);

    const handleFile = async (file: File) => {
        setMenuOpen(false);
        setUploadError(null);
        if (!ALLOWED_UPLOAD_TYPES.includes(file.type)) {
            setUploadError("type");
            return;
        }
        if (file.size > MAX_UPLOAD_BYTES) {
            setUploadError("size");
            return;
        }
        setUploading(true);
        try {
            const result = await onUpload(file);
            if (result) setUploadError(result);
        } catch (e) {
            debugLog.error("Studio", "T2I upload failed", e);
            setUploadError("network");
        } finally {
            setUploading(false);
        }
    };

    return (
        <div
            className={clsx(
                // Sibling section inside the ShotPanel container — match the
                // flat SectionShell rhythm (px-3 py-2) instead of nesting an
                // extra rounded card. Outer ShotPanel already provides chrome.
                "px-3 py-2",
            )}
        >
            {/* Header row — Step indicator + count + ▼/+ controls. */}
            <div className="flex items-center gap-2">
                <StepBadge num={1} done label={t("t2iStepOneDone")} />
                <span className="font-mono text-chrome-sm tracking-tight text-text-muted">
                    · {countLabel}
                </span>
                <div className="ml-auto flex items-center gap-0.5">
                    {/* PR-3 grill Q14 (D) — Reroll button: explicit "再抽一张"
                        action, surfaces the "regenerate first frame with current
                        prompt" capability that was previously hidden in the
                        hover popover behind "+". Direct onGenerate call (no
                        popover step), disabled when prompt is empty. */}
                    <button
                        type="button"
                        onClick={onGenerate}
                        disabled={generationDisabled || promptIsEmpty || generating}
                        aria-label={t("t2iCompactReroll")}
                        title={promptIsEmpty ? t("t2iCompactRerollDisabled") : t("t2iCompactRerollTooltip")}
                        className="btn-tip inline-flex items-center gap-1 px-1.5 h-7 rounded font-mono text-[0.65625rem] uppercase tracking-tight text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-text-muted"
                    >
                        <RefreshCw size={11} strokeWidth={1.8} aria-hidden="true" className={generating ? "animate-spin" : undefined} />
                        <span>{t("t2iCompactReroll")}</span>
                    </button>
                    <button
                        type="button"
                        onClick={() => setExpanded((v) => !v)}
                        aria-label={open ? t("t2iCollapse") : t("t2iExpand")}
                        title={open ? t("t2iCollapse") : t("t2iExpand")}
                        className="btn-tip -m-1 grid h-7 w-7 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                    >
                        {open ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}
                    </button>

                    {/* + 按钮 + hover popover (新增候选) */}
                    <div
                        onMouseEnter={openMenu}
                        onMouseLeave={scheduleCloseMenu}
                        className="relative"
                    >
                        <button
                            type="button"
                            onClick={openMenu}
                            onFocus={openMenu}
                            onBlur={scheduleCloseMenu}
                            aria-label={t("t2iAddTooltip")}
                            aria-haspopup="menu"
                            aria-expanded={menuOpen}
                            title={t("t2iAddTooltip")}
                            className="btn-tip -m-1 grid h-7 w-7 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            {uploading ? (
                                <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                            ) : (
                                <Plus size={13} aria-hidden="true" />
                            )}
                        </button>
                        {menuOpen ? (
                            <div
                                role="menu"
                                className="absolute right-0 top-full z-10 mt-1 w-44 overflow-hidden rounded-md border border-glass-border bg-surface/96 shadow-[0_8px_28px_-6px_rgba(0,0,0,0.7)] backdrop-blur-md motion-safe:animate-[shotPanelIn_180ms_cubic-bezier(0.22,1,0.36,1)_both]"
                            >
                                <button
                                    type="button"
                                    role="menuitem"
                                    onClick={() => {
                                        setMenuOpen(false);
                                        onGenerate();
                                    }}
                                    disabled={generationDisabled || promptIsEmpty || generating}
                                    title={promptIsEmpty ? t("t2iHeroGenerateDisabledTooltip") : undefined}
                                    className="btn-tip flex w-full min-h-[36px] items-center gap-2 px-3 py-2 text-left font-sans text-body-sm text-foreground transition-colors duration-fast ease-out-quart hover:bg-primary/12 hover:text-primary focus-visible:outline-none focus-visible:bg-primary/12 disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                    <Sparkles size={12} aria-hidden="true" />
                                    {t("t2iAddMenuGenerate")}
                                </button>
                                <button
                                    type="button"
                                    role="menuitem"
                                    onClick={() => {
                                        setMenuOpen(false);
                                        inputRef.current?.click();
                                    }}
                                    disabled={uploading}
                                    className="flex w-full min-h-[36px] items-center gap-2 px-3 py-2 text-left font-sans text-body-sm text-foreground transition-colors duration-fast ease-out-quart hover:bg-primary/12 hover:text-primary focus-visible:outline-none focus-visible:bg-primary/12 disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                    <Upload size={12} aria-hidden="true" />
                                    {t("t2iAddMenuUpload")}
                                </button>
                            </div>
                        ) : null}
                        <input
                            ref={inputRef}
                            type="file"
                            accept={ALLOWED_UPLOAD_TYPES.join(",")}
                            className="sr-only"
                            onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) void handleFile(file);
                                e.target.value = "";
                            }}
                        />
                    </div>
                </div>
            </div>

            {/* Q4 C: single-frame inline preview (160×90, 16:9) when there's
                only one option — most common after upload — so the user can
                actually SEE the image. Multi-frame goes to compact strip. */}
            {total === 1 ? (
                <div className="mt-1.5">
                    <SinglePreview
                        url={showStoryboardThumb ? storyboardFrameUrl! : imageUrls[0]}
                        isStoryboard={showStoryboardThumb}
                        onRemove={showStoryboardThumb ? null : () => onRemove(0)}
                        storyboardBadgeText={t("t2iStoryboardBadgeTooltip")}
                    />
                </div>
            ) : total >= 2 ? (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {showStoryboardThumb ? (
                        <ThumbButton
                            active
                            url={storyboardFrameUrl!}
                            onClick={() => { /* already active; no-op */ }}
                            showRemove={false}
                            onRemove={() => { /* noop */ }}
                            hovered={false}
                            onHover={() => { /* noop */ }}
                            badge="storyboard"
                            ariaLabel="Storyboard frame (active)"
                            title={t("t2iStoryboardBadgeTooltip")}
                        />
                    ) : null}

                    {imageUrls.map((url, idx) => {
                        const active = idx === safeIndex && !storyboardActive;
                        return (
                            <ThumbButton
                                key={`${url}-${idx}`}
                                active={active}
                                url={url}
                                onClick={() => onSelect(idx)}
                                showRemove
                                onRemove={() => onRemove(idx)}
                                hovered={hoveredIdx === idx}
                                onHover={(v) => setHoveredIdx(v ? idx : (cur) => (cur === idx ? null : cur))}
                                ariaLabel={`T2I candidate ${idx + 1}${active ? " (active)" : ""}`}
                                title={active ? "Active · click ✕ to remove" : "Click to make active"}
                            />
                        );
                    })}
                </div>
            ) : null}

            {/* Expanded mode — large active preview above the strip for
                comfortable inspection (multi-frame only; single-frame mode
                already shows the image inline at 160×90, no need to repeat). */}
            {open && total >= 2 ? (
                <ActivePreview
                    activeUrl={
                        storyboardActive
                            ? storyboardFrameUrl
                            : safeIndex >= 0 ? imageUrls[safeIndex] : undefined
                    }
                    isStoryboard={storyboardActive}
                    generating={generating}
                    inFlightTaskId={inFlightTaskId}
                    inFlightStatus={inFlightStatus}
                />
            ) : null}

            {lastFrameEnabled && onLastFrameUpload ? (
                <LastFrameControl
                    t={t}
                    url={lastFrameUrl}
                    uploadingDisabled={uploading || generating}
                    onUpload={onLastFrameUpload}
                    onClear={onLastFrameClear}
                />
            ) : null}

            {uploadError ? (
                <p
                    role="alert"
                    className="pt-1.5 font-sans text-body-sm text-status-failed-fg"
                >
                    {formatUploadError(uploadError, t)}
                </p>
            ) : null}
        </div>
    );
}

// ────────────────────────────────────────────────────────────────────
// Step badge — numbered chip with completion check
// ────────────────────────────────────────────────────────────────────

function StepBadge({
    num, done, label,
}: {
    num: number;
    done: boolean;
    label: string;
}) {
    return (
        <span className="inline-flex items-center gap-1.5">
            <span
                aria-hidden="true"
                className={clsx(
                    "grid h-5 w-5 place-items-center rounded-full font-mono text-[0.625rem] font-semibold transition-colors duration-fast ease-out-quart",
                    done
                        ? "bg-status-completed-bg text-status-completed-fg ring-1 ring-status-completed-border"
                        : "bg-primary/20 text-primary ring-1 ring-primary/40",
                )}
            >
                {done ? <Check size={10} strokeWidth={3} /> : num}
            </span>
            <span className="font-mono text-chrome-sm font-medium uppercase tracking-tight text-foreground">
                {label}
            </span>
        </span>
    );
}

// ────────────────────────────────────────────────────────────────────
// Thumbnail tile — used by both storyboard-frame slot and T2I history.
// ────────────────────────────────────────────────────────────────────

interface ThumbButtonProps {
    active: boolean;
    url: string;
    onClick: () => void;
    showRemove: boolean;
    onRemove: () => void;
    hovered: boolean;
    onHover: (v: boolean) => void;
    badge?: "storyboard";
    ariaLabel: string;
    title: string;
}

/** Q4 C — bumped from 56×56 (1:1) to 96×54 (16:9) to match video aspect
 *  and give ~50% more visible image area. Hover-reveal 🔍 magnify comes from
 *  PreviewImage automatically. Click on the thumb body = select active
 *  (existing semantics preserved); click on magnify = open lightbox. */
function ThumbButton({
    active, url, onClick, showRemove, onRemove,
    hovered, onHover, badge, ariaLabel, title,
}: ThumbButtonProps) {
    return (
        <button
            type="button"
            onMouseEnter={() => onHover(true)}
            onMouseLeave={() => onHover(false)}
            onClick={onClick}
            aria-pressed={active}
            aria-label={ariaLabel}
            title={title}
            className={clsx(
                "group relative h-[54px] w-[96px] shrink-0 overflow-hidden rounded-md border transition-colors duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-1 focus-visible:ring-offset-black",
                active
                    ? "border-primary/70 ring-1 ring-primary/40"
                    : "border-glass-border hover:border-foreground/30",
            )}
        >
            <PreviewImage
                src={url}
                alt=""
                className={clsx(
                    "h-full w-full transition-opacity duration-fast ease-out-quart",
                    active ? "" : "opacity-70 group-hover:opacity-100",
                )}
            />
            <span
                aria-hidden="true"
                className={clsx(
                    "pointer-events-none absolute bottom-0 left-0 h-[2px] bg-primary transition-[width] duration-base ease-out-quart",
                    active ? "w-full" : "w-0",
                )}
            />
            {badge === "storyboard" ? (
                <span
                    aria-hidden="true"
                    className="pointer-events-none absolute left-0.5 top-0.5 grid h-4 w-4 place-items-center rounded bg-black/65 text-text-secondary"
                    title="From Storyboard stage"
                >
                    <Pin size={9} aria-hidden="true" />
                </span>
            ) : null}
            {showRemove && hovered ? (
                <span
                    role="button"
                    tabIndex={0}
                    aria-label="Delete candidate"
                    title="Delete"
                    onClick={(e) => { e.stopPropagation(); onRemove(); }}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            e.stopPropagation();
                            onRemove();
                        }
                    }}
                    className="absolute left-0 top-0 grid h-6 w-6 cursor-pointer place-items-center rounded-full text-foreground transition-colors duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
                >
                    <span className="grid h-4 w-4 place-items-center rounded-full bg-black/75 transition-colors duration-fast ease-out-quart hover:bg-status-failed-fg">
                        <X size={9} aria-hidden="true" />
                    </span>
                </span>
            ) : null}
        </button>
    );
}

// ────────────────────────────────────────────────────────────────────
// SinglePreview — Q4 C inline 160×90 preview for the most common case
// (only one frame). Clicking the image opens lightbox; ✕ on hover removes
// (unless storyboard frame, which is read-only).
// ────────────────────────────────────────────────────────────────────

interface SinglePreviewProps {
    url: string;
    isStoryboard: boolean;
    /** null = read-only (storyboard frame); function = removable T2I candidate */
    onRemove: (() => void) | null;
    storyboardBadgeText: string;
}

function SinglePreview({ url, isStoryboard, onRemove, storyboardBadgeText }: SinglePreviewProps) {
    const [hovered, setHovered] = useState(false);
    return (
        <div
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
            className="relative h-[90px] w-[160px] overflow-hidden rounded-md border border-primary/60 ring-1 ring-primary/30"
        >
            <PreviewImage src={url} alt="" className="h-full w-full" />
            {isStoryboard ? (
                <span
                    className="pointer-events-none absolute left-1 top-1 inline-flex items-center gap-1 rounded bg-black/65 px-1.5 py-0.5 font-mono text-[0.625rem] font-medium uppercase text-text-secondary"
                    title={storyboardBadgeText}
                >
                    <Pin size={9} aria-hidden="true" />
                    storyboard
                </span>
            ) : null}
            {onRemove && hovered ? (
                <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onRemove(); }}
                    aria-label="Delete candidate"
                    title="Delete"
                    className="absolute left-1 top-1 grid h-6 w-6 cursor-pointer place-items-center rounded-full bg-black/75 text-foreground transition-colors duration-fast ease-out-quart hover:bg-status-failed-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
                >
                    <X size={11} aria-hidden="true" />
                </button>
            ) : null}
        </div>
    );
}

// ────────────────────────────────────────────────────────────────────
// Expanded large preview (only when expanded or processing)
// ────────────────────────────────────────────────────────────────────

interface ActivePreviewProps {
    activeUrl?: string;
    isStoryboard: boolean;
    generating: boolean;
    inFlightTaskId?: string;
    inFlightStatus?: "pending" | "processing" | "completed" | "failed";
}

function ActivePreview({
    activeUrl, isStoryboard, generating, inFlightTaskId, inFlightStatus,
}: ActivePreviewProps) {
    return (
        <div className="mt-2 flex items-start gap-2.5 motion-safe:animate-[shotPanelIn_180ms_cubic-bezier(0.22,1,0.36,1)_both]">
            <div className="relative h-[100px] w-[140px] shrink-0 overflow-hidden rounded-md border border-glass-border bg-black/40">
                {activeUrl ? (
                    <PreviewImage
                        src={activeUrl}
                        alt="Active first frame"
                        className="h-full w-full"
                    />
                ) : (
                    <div className="grid h-full w-full place-items-center font-mono text-chrome-sm font-medium uppercase text-text-muted">
                        no frame yet
                    </div>
                )}
                {isStoryboard ? (
                    <span
                        className="pointer-events-none absolute left-1 top-1 inline-flex items-center gap-1 rounded bg-black/65 px-1.5 py-0.5 font-mono text-[0.625rem] font-medium uppercase text-text-secondary"
                        title="From Storyboard stage"
                    >
                        <Pin size={9} aria-hidden="true" />
                        storyboard
                    </span>
                ) : null}
                {generating && !activeUrl ? (
                    <div className="absolute inset-0 grid place-items-center bg-black/65 backdrop-blur-[1px]">
                        <PendingTaskAffordance
                            statusLabel={inFlightStatus === "pending" ? "Queued" : "Generating"}
                            taskId={inFlightTaskId}
                            compact
                        />
                    </div>
                ) : null}
            </div>
        </div>
    );
}
