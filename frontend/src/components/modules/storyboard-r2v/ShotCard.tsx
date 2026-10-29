"use client";

import { useRef, useCallback, useEffect, useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
    Trash2,
    ChevronUp,
    ChevronDown,
    Copy,
    Video,
    ImageIcon,
    AtSign,
    Maximize2,
    Sparkles,
    Loader2,
    Code2,
    ChevronRight,
    Pin,
    PinOff,
    Play,
    Star,
    AlertCircle,
} from "lucide-react";
import { useTranslations } from "next-intl";
import AssetChipBar from "./AssetChipBar";
import PromptExpandModal from "./PromptExpandModal";
import PolishPanel from "./PolishPanel";
import FieldTagChip, { AddFieldButton, type FieldType } from "./FieldTagChip";
import { buildAssembledPrompt } from "./buildAssembledPrompt";
import { PendingTaskAffordance } from "@/components/shared/PendingTaskAffordance";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import PreviewVideo from "@/components/shared/preview/PreviewVideo";
import { useProjectStore, type Character } from "@/store/projectStore";
import { selectedVariantUrl } from "@/lib/characterImage";

type AssetChipReference = {
    id: string;
    name: string;
};

export interface ShotNode {
    id: string;
    prompt: string;
    tabMode: "t2i_i2v" | "direct_r2v";

    // T2I stage (only for t2i_i2v mode). Single-task fields stay here
    // for backward compat with existing shot drafts and the legacy
    // single-image preview. New: a history of generated T2I images per
    // shot + an index for the currently-active one (the one used as
    // first-frame for I2V). Persisted in localStorage with the rest of
    // the shot state. See Storyboard R2V redesign discussion.
    t2iImageUrl?: string;
    t2iTaskId?: string;
    t2iStatus?: "pending" | "processing" | "completed" | "failed";
    /** Ordered list of every T2I image URL this shot has produced.
     *  Newest at the end. Active one is at t2iSelectedIndex (defaults
     *  to last). Bounded to T2I_HISTORY_LIMIT FIFO to keep
     *  localStorage from growing without bound. */
    t2iImageUrls?: string[];
    t2iSelectedIndex?: number;

    // Video stage (shared). The single-task fields stay for "the most
    // recent attempt" but the candidates panel reads from the shot's
    // full videoTaskIds history (cross-referenced against the script's
    // video_tasks list which is persisted server-side).
    videoUrl?: string;
    videoTaskId?: string;
    videoStatus?: "pending" | "processing" | "completed" | "failed" | "needs_confirmation";
    /** Issue 16 — final take selection (Z plan). Set in Assembly stage; read
     *  by Storyboard's ShotCard top preview as the canonical "this is the
     *  shipped output". Falls back to latest starred / latest completed /
     *  first frame when null. */
    finalTakeId?: string | null;
    /** Every video task this shot has spawned, oldest first. Each tab
     *  (t2i_i2v / direct_r2v) gets its own list — see videoTaskIdsByTab.
     *  Empty / missing → no history (e.g. legacy shots). */
    videoTaskIdsByTab?: {
        t2i_i2v?: string[];
        direct_r2v?: string[];
    };
    imageUrl?: string;
    /** Optional persisted closing frame for Wan3 first/last-frame I2V. */
    lastFrameUrl?: string;

    // ─── Storyboard Schema v2 fields ────────────────────────────────
    duration?: number | null;
    visualDescription?: string | null;
    assembledPrompt?: string | null;
    dialogueStructured?: {
        speaker: string;
        line: string;
        emotion?: string | null;
        delivery?: string | null;
    } | null;
    cameraMovementStructured?: {
        primary: string;
        secondary?: string | null;
        speed: string;
        description?: string | null;
    } | null;
    shotSize?: string | null;
    cameraAngle?: string | null;
    transitionHint?: string | null;

    /** When true, the user has manually pinned an active take. Hero
     *  shows a "Pinned" chip; autoSelectLatestVideo skips this frame on
     *  the backend, so new completed tasks stay in Candidates without
     *  overwriting the user's pick. Sourced from frame.is_video_pinned. */
    isVideoPinned?: boolean;
}

/** Cap on T2I image history per shot. Older drops off FIFO when adding. */
export const T2I_HISTORY_LIMIT = 10;

interface ShotCardProps {
    structureDisabled?: boolean;
    shot: ShotNode;
    index: number;
    totalShots: number;
    characters: Character[];
    scenes: AssetChipReference[];
    props: AssetChipReference[];
    onUpdatePrompt: (prompt: string) => void;
    onUpdateField: (field: string, value: string | number | null) => void;
    onGenerateT2I: () => void;
    onGenerateVideo: () => void;
    onDelete: () => void;
    onMoveUp: () => void;
    onMoveDown: () => void;
    onDuplicate: () => void;
    onSetTabMode: (mode: "t2i_i2v" | "direct_r2v") => void;
    onOpenDrawer: () => void;
    onInsertAsset: (type: string, name: string) => void;
    /** Duration editor config derived from model catalog */
    durationEditorConfig?: { min: number; max: number; step: number };
    /** Optional: Cancel CTA shown inside the pending-state affordance
     *  after the soft-stuck threshold (60 s by default). Caller should
     *  hit the backend cancel endpoint and refresh local state. */
    onCancelVideo?: () => Promise<void> | void;
    /** Issue 16 — per-shot expand state (P plan). When false, the
     *  Setup/Takes chips below the card are hidden entirely (zero chrome
     *  residue). When true, chips render. The chevron in the card's
     *  top-right corner toggles this. */
    expanded: boolean;
    onToggleExpanded: () => void;
    /** PR-3c · 闭环生成. Generation 移到 ShotCard 内的全宽行 (Action
     *  Bar 之后, disclosure bar 之前), 含 count selector 同行. Host
     *  传入 current count + handlers + canGenerate gate.
     *  Spec: r2v-workflow-v3-unified.md §4.3.1 / Q12. */
    generateCount?: number;
    /** At-a-glance "model · duration" summary shown in the generation row,
     *  visible even when the attached ShotPanel is collapsed (calm-default). */
    genSummary?: string;
    canGenerate?: boolean;
    onSetGenerateCount?: (count: number) => void;
    onGenerateBatch?: (count: number) => void;
    /** Generation is blocked by pending/failed frame edits; caller supplies the user-facing reason. */
    generationBlockedMessage?: string;
    /** Pending saves are busy; failed saves are blocked but should not look like work is still running. */
    generationBlockedBusy?: boolean;
    /** Active in-flight count for label flip (生成 ×N → 生成中 · N). */
    inFlightCount?: number;
    onRefineFrame?: () => void;
    onUpdateDialogue?: (text: string) => void;
    /** Active-take pin controls. When the user has manually pinned an
     *  active take (shot.isVideoPinned=true), the hero shows a "📌 Pinned"
     *  chip; clicking it fires onUnpinVideo to resume auto latest-wins. */
    onUnpinVideo?: () => void;
}

export default function ShotCard({
    structureDisabled = false,
    shot,
    index,
    totalShots,
    characters,
    scenes,
    props,
    onUpdatePrompt,
    onUpdateField,
    onGenerateT2I,
    onGenerateVideo,
    onDelete,
    onMoveUp,
    onMoveDown,
    onDuplicate,
    onSetTabMode,
    onOpenDrawer,
    durationEditorConfig,
    onCancelVideo,
    expanded,
    onToggleExpanded,
    generateCount = 1,
    genSummary,
    canGenerate = true,
    onSetGenerateCount,
    onGenerateBatch,
    generationBlockedMessage,
    generationBlockedBusy = false,
    inFlightCount = 0,
    onRefineFrame,
    onUnpinVideo,
}: ShotCardProps) {
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const cardRef = useRef<HTMLDivElement>(null);
    const t = useTranslations("storyboardR2V");
    // Expand modal state (B5). Cmd/Ctrl+E in the small textarea
    // opens it; saving syncs back via onUpdatePrompt; cancel
    // discards the modal's draft without touching parent state.
    const [expandOpen, setExpandOpen] = useState(false);
    const [promptPreviewOpen, setPromptPreviewOpen] = useState(false);
    // currentProjectId — needed by PolishPanel to look up the
    // project's PromptConfig override server-side.
    const currentProjectId = useProjectStore((state) => state.currentProject?.id);
    // r2vSlots — when R2V tab is active, derive slot context from
    // @character references in the prompt so the polish system
    // prompt knows what character1/character2 ID maps to. Dedup by
    // slot number (first-seen wins) and sort ascending so the list
    // index aligns with HappyHorse's characterN positional mapping.
    // Backend polish_r2v_prompt re-numbers with enumerate(slots),
    // which only matches the prompt's characterN tags when slots
    // are unique and ordered.
    const r2vSlots = useCallback((): { description: string }[] => {
        if (shot.tabMode !== "direct_r2v") return [];
        const bySlot = new Map<number, string>();
        const tagPattern = /\[character(\d+):([^\]]+)\]/g;
        let match;
        while ((match = tagPattern.exec(shot.prompt)) !== null) {
            const slotN = parseInt(match[1], 10);
            if (bySlot.has(slotN)) continue;
            const name = match[2];
            const char = characters.find((c) => c.name === name);
            bySlot.set(slotN, char?.description ? `${name}: ${char.description}` : name);
        }
        return Array.from(bySlot.entries())
            .sort((a, b) => a[0] - b[0])
            .map(([, description]) => ({ description }));
    }, [shot.tabMode, shot.prompt, characters])();

    // polishImageUrls — feed vision-capable polish (Issue 13) with the
    // images the polish actually needs to "see":
    //   • i2v: the active first frame (T2I selection if any, else the
    //     Storyboard render). No frame yet → empty → text-only polish.
    //   • r2v: each referenced character's avatar/headshot/full body
    //     image, dedup'd by id. No references → empty → text-only.
    const generationBlocked = !!generationBlockedMessage;
    const generationActionable = canGenerate && inFlightCount === 0 && !generationBlocked;

    const polishImageUrls = useCallback((): string[] => {
        if (shot.tabMode === "direct_r2v") {
            const out: string[] = [];
            const seen = new Set<string>();
            const tagPattern = /\[character\d*:([^\]]+)\]/g;
            let m;
            while ((m = tagPattern.exec(shot.prompt)) !== null) {
                const [, name] = m;
                const char = characters.find((c) => c.name === name);
                if (!char || seen.has(char.id)) continue;
                seen.add(char.id);
                const url = char.headshot_image_url || char.image_url || char.full_body_image_url
                    || selectedVariantUrl(char.reference_sheet)
                    || (char.full_body_asset?.variants?.[0]?.url);
                if (url) out.push(url);
            }
            return out.slice(0, 4); // cap at 4 to keep payload reasonable
        }
        // i2v: prefer active T2I image; fall back to storyboard frame.
        const active = (shot.t2iImageUrls && shot.t2iImageUrls.length > 0)
            ? shot.t2iImageUrls[Math.max(0, Math.min(shot.t2iSelectedIndex ?? 0, shot.t2iImageUrls.length - 1))]
            : (shot.t2iImageUrl || shot.imageUrl);
        return active ? [active] : [];
    }, [shot.tabMode, shot.prompt, shot.t2iImageUrls, shot.t2iSelectedIndex, shot.t2iImageUrl, shot.imageUrl, characters])();

    // castAvatars — character avatar group for the "Cast:" row above
    // the prompt textarea (L5 borrow from 火山剧创's 出镜角色). De-
    // duped by id. We accept either [character:name] or [characterN:
    // name] patterns since the asset chip bar emits both formats.
    const castAvatars = useCallback((): Array<{ id: string; name: string; avatarUrl?: string }> => {
        const out: Array<{ id: string; name: string; avatarUrl?: string }> = [];
        const seen = new Set<string>();
        const tagPattern = /\[character\d*:([^\]]+)\]/g;
        let match;
        while ((match = tagPattern.exec(shot.prompt)) !== null) {
            const [, name] = match;
            const char = characters.find((c) => c.name === name);
            if (!char || seen.has(char.id)) continue;
            seen.add(char.id);
            const avatarUrl =
                char.avatar_url ||
                char.headshot_image_url ||
                char.image_url ||
                char.full_body_image_url ||
                selectedVariantUrl(char.reference_sheet) ||
                (char.full_body_asset?.variants?.[0]?.url);
            out.push({ id: char.id, name: char.name, avatarUrl });
        }
        return out;
    }, [shot.prompt, characters])();

    const assembledPromptPreview = useMemo(() => buildAssembledPrompt(shot), [
        shot.prompt, shot.shotSize, shot.cameraAngle, shot.cameraMovementStructured, shot.transitionHint,
    ]);

    useEffect(() => {
        const ta = textareaRef.current;
        if (!ta) return;
        // Reset before measuring so shrinking also works (delete text).
        ta.style.height = "auto";
        const next = Math.min(ta.scrollHeight, 260);
        ta.style.height = `${next}px`;
    }, [shot.prompt]);

    const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        const el = cardRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        el.style.setProperty("--spotlight-x", `${e.clientX - rect.left}px`);
        el.style.setProperty("--spotlight-y", `${e.clientY - rect.top}px`);
    }, []);

    const renderNeedsConfirmation = () => (
        <div className="w-full aspect-video relative flex flex-col items-center justify-center gap-2.5 border border-amber-500/25 bg-amber-500/10 px-5 text-center">
            {shot.videoUrl ? (
                <PreviewVideo
                    src={shot.videoUrl}
                    alt={t("historicalUnconfirmedVideo") || "Historical unconfirmed video"}
                    className="pointer-events-none absolute inset-0 h-full w-full opacity-45"
                    noLightbox
                    hoverPlay={false}
                />
            ) : null}
            <div className="relative z-10 flex flex-col items-center gap-2 rounded-xl border border-amber-500/25 bg-black/55 px-4 py-3 backdrop-blur-sm">
                <AlertCircle size={18} className="text-amber-300" aria-hidden="true" />
                <span className="text-[0.75rem] font-semibold text-amber-100">{t("needsConfirmation")}</span>
                <span className="max-w-[260px] text-[0.6875rem] leading-snug text-amber-100/75">{t("needsConfirmationDetail")}</span>
                {shot.videoTaskId ? (
                    <code className="rounded bg-black/45 px-1.5 py-0.5 font-mono text-[0.625rem] text-amber-100/70">{shot.videoTaskId}</code>
                ) : null}
            </div>
        </div>
    );

    const renderPreview = () => {
        if (shot.tabMode === "t2i_i2v") {
            if (shot.videoUrl && shot.videoStatus !== "needs_confirmation") {
                return (
                    <PreviewVideo
                        src={shot.videoUrl}
                        alt={t("generatedVideo") || "Generated video"}
                        className="w-full aspect-video"
                    />
                );
            }
            if (shot.videoStatus === "needs_confirmation") {
                return renderNeedsConfirmation();
            }
            if (shot.videoStatus === "processing" || shot.videoStatus === "pending") {
                return (
                    <div className="w-full aspect-video flex items-center justify-center">
                        <PendingTaskAffordance
                            statusLabel={shot.videoStatus === "pending" ? t("queued") : t("generatingVideo")}
                            taskId={shot.videoTaskId}
                            onCancel={onCancelVideo}
                        />
                    </div>
                );
            }
            if (shot.videoStatus === "failed") {
                return (
                    <div className="w-full aspect-video flex flex-col items-center justify-center gap-2">
                        <span className="text-[0.6875rem] text-status-failed-fg font-medium">{t("generationFailed")}</span>
                        <button
                            onClick={onGenerateVideo}
                            className="text-[0.6875rem] text-primary hover:text-primary/80 transition-colors font-medium"
                        >
                            {t("retry")}
                        </button>
                    </div>
                );
            }
            if (shot.t2iImageUrl) {
                // Fixed: was rendering raw `<img src={shot.t2iImageUrl}>` —
                // shot.t2iImageUrl is a relative path (e.g. "uploads/t2i_xxx.jpg")
                // which the browser resolved against the current origin → 404 →
                // broken icon + "Generated frame" alt fallback. PreviewImage
                // routes through getAssetUrl() (Issue 14).
                //
                // Issue 15: bottom badge label changed to "next: generate
                // video →" so the user knows the first frame is in place and
                // the next step is downstream, not another image gen.
                return (
                    <div className="w-full aspect-video relative">
                        <PreviewImage
                            src={shot.t2iImageUrl}
                            alt={t("t2iCompleted") || "First frame"}
                            className="w-full h-full"
                        />
                        <div className="absolute bottom-2 left-2 text-[0.625rem] px-1.5 py-0.5 rounded-full bg-status-completed-bg/90 text-white font-medium backdrop-blur-sm pointer-events-none">
                            {t("generateVideoNext")}
                        </div>
                    </div>
                );
            }
            if (shot.t2iStatus === "processing" || shot.t2iStatus === "pending") {
                return (
                    <div className="w-full aspect-video flex items-center justify-center">
                        <PendingTaskAffordance
                            statusLabel={shot.t2iStatus === "pending" ? t("queued") : t("t2iGenerating")}
                            taskId={shot.t2iTaskId}
                        />
                    </div>
                );
            }
            if (shot.t2iStatus === "failed") {
                return (
                    <div className="w-full aspect-video flex flex-col items-center justify-center gap-2">
                        <span className="text-[0.6875rem] text-status-failed-fg font-medium">{t("generationFailed")}</span>
                        <button
                            onClick={onGenerateT2I}
                            className="text-[0.6875rem] text-primary hover:text-primary/80 transition-colors font-medium"
                        >
                            {t("retry")}
                        </button>
                    </div>
                );
            }
            // I2V tab, no first frame yet — the active CTA is in the
            // Step 1 panel below (Hero state), not here. Just signal
            // "waiting for a first frame" so the user knows where to
            // act (Issue 15).
            return (
                <div className="w-full aspect-video flex flex-col items-center justify-center gap-2.5 text-text-muted">
                    <ImageIcon size={24} strokeWidth={1.6} className="opacity-50" />
                    <span className="font-mono text-[0.65625rem] uppercase tracking-[0.08em]">{t("generateImageOrUpload")}</span>
                </div>
            );
        }

        // Direct R2V mode
        if (shot.videoUrl && shot.videoStatus !== "needs_confirmation") {
            return (
                <PreviewVideo
                    src={shot.videoUrl}
                    alt={t("generatedVideo") || "Generated video"}
                    className="w-full aspect-video"
                />
            );
        }
        if (shot.videoStatus === "needs_confirmation") {
            return renderNeedsConfirmation();
        }
        if (shot.videoStatus === "processing" || shot.videoStatus === "pending") {
            return (
                <div className="w-full aspect-video flex items-center justify-center">
                    <PendingTaskAffordance
                        statusLabel={shot.videoStatus === "pending" ? t("queued") : t("generatingVideo")}
                        taskId={shot.videoTaskId}
                        onCancel={onCancelVideo}
                    />
                </div>
            );
        }
        if (shot.videoStatus === "failed") {
            return (
                <div className="w-full aspect-video flex flex-col items-center justify-center gap-2">
                    <span className="text-[0.6875rem] text-status-failed-fg font-medium">{t("generationFailed")}</span>
                    <button
                        onClick={onGenerateVideo}
                        className="text-[0.6875rem] text-primary hover:text-primary/80 transition-colors font-medium"
                    >
                        {t("retry")}
                    </button>
                </div>
            );
        }
        return (
            <div className="w-full aspect-video flex flex-col items-center justify-center gap-2.5 text-text-muted">
                <Video size={24} strokeWidth={1.6} className="opacity-50" />
                <span className="font-mono text-[0.65625rem] uppercase tracking-[0.08em]">{t("noVideoYet")}</span>
            </div>
        );
    };

    // Legacy renderGenerateButton was removed in the workbench
    // redesign (Sweep G, 2026-05-21): generation moved to the
    // ParamsSection's "Generate ×N" CTA inside the attached
    // ShotPanel, and T2I首帧 generation lives in T2ISubsection's
    // "+gen" tile. Keeping it on the ShotCard duplicated the action
    // with a different label (i18n vs English) and a different
    // batch-size semantics (×1 vs ×N) — confusing and the source of
    // the "two Generate buttons" bug report.
    // onGenerateVideo / onGenerateT2I are still wired for the inline
    // retry buttons inside renderPreview when a take fails.

    function ShotStatusBadge({ shot, t }: { shot: ShotNode; t: (key: string, values?: Record<string, number | string>) => string }) {
        const isNeedsConfirmation = shot.videoStatus === "needs_confirmation";
        const isProcessing = !isNeedsConfirmation && (shot.videoStatus === "processing" || shot.t2iStatus === "processing");
        const isPending = !isNeedsConfirmation && !isProcessing && (shot.videoStatus === "pending" || shot.t2iStatus === "pending");
        const isFailed = !isProcessing && !isPending && !isNeedsConfirmation && (shot.videoStatus === "failed" || shot.t2iStatus === "failed");
        const isStarred = !isNeedsConfirmation && (shot.isVideoPinned || shot.finalTakeId);
        if (isNeedsConfirmation) {
            return (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/35 bg-amber-500/12 px-2.5 py-1 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-amber-200">
                    <span className="h-[5px] w-[5px] rounded-full bg-amber-300" />
                    {t("statusNeedsConfirmation")}
                </span>
            );
        }
        if (isStarred) {
            return (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-status-starred-border bg-status-starred-bg px-2.5 py-1 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-status-starred-fg">
                    <span className="h-[5px] w-[5px] rounded-full bg-status-starred-solid" />
                    {t("statusStarred")}
                </span>
            );
        }
        if (isProcessing) {
            return (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-status-processing-border bg-status-processing-bg px-2.5 py-1 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-status-processing-fg">
                    <span className="h-[5px] w-[5px] rounded-full bg-status-processing-fg animate-pulse" />
                    {t("statusProcessing")}
                </span>
            );
        }
        if (isPending) {
            return (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-status-pending-border bg-status-pending-bg px-2.5 py-1 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-status-pending-fg">
                    <span className="h-[5px] w-[5px] rounded-full bg-status-pending-fg" />
                    {t("statusPending")}
                </span>
            );
        }
        if (isFailed) {
            return (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-status-failed-border bg-status-failed-bg px-2.5 py-1 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-status-failed-fg">
                    <span className="h-[5px] w-[5px] rounded-full bg-status-failed-fg" />
                    {t("statusFailed")}
                </span>
            );
        }
        return (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-glass-border bg-black/20 px-2.5 py-1 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-text-secondary">
                <span className="h-[5px] w-[5px] rounded-full bg-text-muted" />
                {t("statusReady")}
            </span>
        );
    }

    const handleInsertAssetFromChip = (_type: string, name: string) => {
        const currentPrompt = shot.prompt;
        // Each unique character gets one fixed slot number throughout this
        // prompt: slot N → reference_image_urls[N-1] in HappyHorse R2V, so
        // referencing the same actor twice must reuse the same slot —
        // otherwise the model would expect two separate reference images.
        // Examples:
        //   first @小兔子 → [character1:小兔子]
        //   then @小狗 → [character2:小狗]
        //   then @小兔子 again → [character1:小兔子]   (reuse, NOT [character3:…])
        const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const existingTagRe = new RegExp(`\\[character(\\d+):${escapedName}\\]`);
        const existingMatch = currentPrompt.match(existingTagRe);

        let slot: number;
        if (existingMatch) {
            slot = parseInt(existingMatch[1], 10);
        } else {
            // Map of (slot → name) already in the prompt; first-seen wins
            // per slot so accidental dup tags don't inflate the count.
            const usedSlotByName = new Map<number, string>();
            const slotRe = /\[character(\d+):([^\]]+)\]/g;
            let m;
            while ((m = slotRe.exec(currentPrompt)) !== null) {
                const slotN = parseInt(m[1], 10);
                if (!usedSlotByName.has(slotN)) {
                    usedSlotByName.set(slotN, m[2]);
                }
            }
            const usedSlots = Array.from(usedSlotByName.keys());
            slot = usedSlots.length > 0 ? Math.max(...usedSlots) + 1 : 1;
        }
        const tag = `[character${slot}:${name}]`;

        const textarea = textareaRef.current;
        if (textarea) {
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const newPrompt = currentPrompt.slice(0, start) + tag + currentPrompt.slice(end);
            onUpdatePrompt(newPrompt);
            setTimeout(() => {
                textarea.selectionStart = textarea.selectionEnd = start + tag.length;
                textarea.focus();
            }, 0);
        } else {
            onUpdatePrompt(currentPrompt + " " + tag);
        }
    };

    const isActiveT2I = shot.tabMode === "t2i_i2v";

    return (
        <div
            ref={cardRef}
            onMouseMove={handleMouseMove}
            className="relative group"
        >
            {/* Spotlight border glow */}
            <div
                className="absolute -inset-[1px] rounded-xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none z-0"
                style={{
                    background:
                        "radial-gradient(600px circle at var(--spotlight-x, 50%) var(--spotlight-y, 50%), rgba(255,255,255,0.07), transparent 40%)",
                }}
            />

            {/* Floating card body — mock-aligned glass surface */}
            <div className="relative overflow-hidden rounded-[20px] border border-glass-border bg-surface shadow-[0_8px_30px_-10px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.05)] transition-all duration-base ease-out-quart group-hover:-translate-y-1 group-hover:shadow-[0_16px_50px_-12px_rgba(0,0,0,0.65),inset_0_1px_0_rgba(255,255,255,0.07)] z-10">
                {/* Card top — shot no/cap + status badge + tab switcher */}
                <div className="flex items-center justify-between gap-4 px-5 pt-4 pb-3">
                    <div className="flex items-center gap-3 min-w-0">
                        <div className="font-display text-[1.875rem] font-semibold leading-none text-text-secondary tracking-tight">
                            {String(index + 1).padStart(2, "0")}
                        </div>
                        <div className="font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-text-muted leading-tight">
                            <span>SHOT</span>
                            {shot.shotSize ? (
                                <span className="ml-1.5 text-text-secondary font-medium">· {shot.shotSize}</span>
                            ) : null}
                            {shot.cameraMovementStructured?.primary ? (
                                <span className="ml-1.5 text-text-secondary">· {shot.cameraMovementStructured.primary}</span>
                            ) : null}
                        </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                        <ShotStatusBadge shot={shot} t={t} />
                        {/* Pill Tab Switcher */}
                        <div className="relative inline-flex items-center p-[3px] bg-surface-inset rounded-full">
                            <motion.div
                                className="absolute top-[3px] bottom-[3px] rounded-full bg-elevated shadow-[inset_0_1px_0_rgba(255,255,255,0.1)]"
                                initial={false}
                                animate={{
                                    left: isActiveT2I ? 3 : "calc(50% + 1.5px)",
                                    width: "calc(50% - 3px)",
                                }}
                                transition={{ type: "spring", stiffness: 350, damping: 32 }}
                            />
                            <button
                                onClick={() => onSetTabMode("t2i_i2v")}
                                className={`relative z-10 flex items-center gap-1.5 px-3.5 py-1.5 text-[13px] font-semibold rounded-full transition-colors duration-200 ${
                                    isActiveT2I ? "text-foreground" : "text-text-secondary hover:text-text-secondary/80"
                                }`}
                            >
                                <ImageIcon size={11} strokeWidth={1.6} />
                                {t("tabT2iI2v")}
                            </button>
                            <button
                                onClick={() => onSetTabMode("direct_r2v")}
                                className={`relative z-10 flex items-center gap-1.5 px-3.5 py-1.5 text-[13px] font-semibold rounded-full transition-colors duration-200 ${
                                    !isActiveT2I ? "text-foreground" : "text-text-secondary hover:text-text-secondary/80"
                                }`}
                            >
                                <Video size={11} strokeWidth={1.6} />
                                {t("tabDirectR2v")}
                            </button>
                        </div>
                    </div>
                </div>

                {/* Main content: Preview + Editor */}
                <div className="flex px-5">
                    {/* Left: Preview */}
                    <div className="group/preview relative w-72 shrink-0 bg-surface-inset flex flex-col items-center justify-center overflow-hidden rounded-[14px] border border-glass-border shadow-[inset_0_0_0_1px_rgba(255,255,255,0.04)]">
                        {renderPreview()}
                        {/* Selected-take amber halation */}
                        {(shot.isVideoPinned || shot.finalTakeId) && shot.videoStatus !== "needs_confirmation" && shot.videoUrl ? (
                            <div
                                className="pointer-events-none absolute inset-0 rounded-[14px]"
                                style={{ boxShadow: "inset 0 0 42px -8px rgba(255,169,77,0.28)" }}
                            />
                        ) : null}
                        {/* Hover play overlay — only on completed video */}
                        {shot.videoUrl && shot.videoStatus !== "needs_confirmation" ? (
                            <div className="absolute inset-0 grid place-items-center bg-overlay/20 opacity-0 transition-opacity duration-base group-hover/preview:opacity-100 pointer-events-none">
                                <div className="grid h-11 w-11 place-items-center rounded-full bg-foreground/90 text-on-accent">
                                    <Play size={17} fill="currentColor" className="ml-0.5" />
                                </div>
                            </div>
                        ) : null}
                        {/* Top-left selected chip */}
                        {(shot.isVideoPinned || shot.finalTakeId) && shot.videoStatus !== "needs_confirmation" && shot.videoUrl ? (
                            <div className="absolute top-2.5 left-2.5 z-10 flex items-center gap-1 rounded-full border border-status-starred-border bg-status-starred-bg/90 px-2 py-[3px] backdrop-blur-sm font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.08em] text-status-starred-fg">
                                <Star size={10} fill="currentColor" aria-hidden="true" />
                                {t("selectedTake")}
                            </div>
                        ) : null}
                        {/* Duration chip */}
                        {shot.duration ? (
                            <div className="absolute bottom-2.5 right-2.5 z-10 rounded-full bg-overlay/70 px-2 py-0.5 backdrop-blur-sm font-mono text-[0.5625rem] text-foreground">
                                {shot.duration}s
                            </div>
                        ) : null}
                        {/* Pinned chip — overlays the hero when the user has
                            manually pinned an active take. Group/peer makes
                            the "Unpin" CTA fade in on hover so the chip stays
                            calm in the resting state. Only shown when a
                            video is actually rendered (no point pinning a
                            "no video" placeholder). */}
                        {shot.isVideoPinned && shot.videoStatus !== "needs_confirmation" && shot.videoUrl && onUnpinVideo ? (
                            <div className="group/pin absolute top-2.5 right-2.5 z-20 flex items-center gap-1">
                                <span
                                    className="inline-flex items-center gap-1 rounded-full border border-primary/55 bg-primary/20 backdrop-blur-sm px-2 py-[2px] font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-primary shadow-[var(--glow-primary)]"
                                    title={t("activeTakePinnedTooltip")}
                                >
                                    <Pin size={9} aria-hidden="true" strokeWidth={2.2} fill="currentColor" />
                                    {t("activeTakePinned")}
                                </span>
                                <button
                                    type="button"
                                    onClick={(e) => { e.stopPropagation(); onUnpinVideo(); }}
                                    title={t("unpinActiveTakeTooltip")}
                                    aria-label={t("unpinActiveTake")}
                                    className="opacity-0 transition-opacity duration-fast ease-out-quart group-hover/pin:opacity-100 focus-visible:opacity-100 inline-flex items-center gap-1 rounded-full border border-foreground/15 bg-black/55 backdrop-blur-sm px-1.5 py-[2px] font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-foreground/80 hover:text-foreground hover:border-foreground/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                >
                                    <PinOff size={9} aria-hidden="true" strokeWidth={2.2} />
                                    {t("unpinActiveTakeShort")}
                                </button>
                            </div>
                        ) : null}
                    </div>

                    {/* Right: Prompt + Controls */}
                    <div className="flex-1 py-0.5 pl-5 pr-0 flex flex-col gap-3">
                        {/* Cast avatar group */}
                        {castAvatars.length > 0 ? (
                            <div className="flex items-center gap-2">
                                <span className="font-mono text-[0.625rem] uppercase tracking-[0.1em] text-text-muted">
                                    {t("shotCast")}
                                </span>
                                <div className="flex items-center -space-x-2">
                                    {castAvatars.slice(0, 3).map((c) => (
                                        <button
                                            key={c.id}
                                            type="button"
                                            onClick={() => {
                                                document.dispatchEvent(
                                                    new CustomEvent("lumenx:navigateStep", { detail: "cast" }),
                                                );
                                            }}
                                            title={c.name}
                                            className="grid h-[26px] w-[26px] place-items-center overflow-hidden rounded-full border-2 border-surface bg-elevated transition-all duration-fast ease-out-quart hover:z-10 hover:scale-110 focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                        >
                                            {c.avatarUrl ? (
                                                <PreviewImage
                                                    src={c.avatarUrl}
                                                    alt={c.name}
                                                    className="h-full w-full"
                                                    noLightbox
                                                />
                                            ) : (
                                                <span className="font-mono text-[0.5625rem] font-medium text-text-secondary">
                                                    {c.name.slice(0, 1)}
                                                </span>
                                            )}
                                        </button>
                                    ))}
                                    {castAvatars.length > 3 ? (
                                        <span className="grid h-[26px] w-[26px] place-items-center rounded-full border-2 border-surface bg-elevated font-mono text-[0.5625rem] font-medium text-text-secondary">
                                            +{castAvatars.length - 3}
                                        </span>
                                    ) : null}
                                </div>
                            </div>
                        ) : null}

                        {/* Prompt Editor wrapper — with left accent line */}
                        <div className="relative">
                            <textarea
                                ref={textareaRef}
                                value={shot.prompt}
                                onChange={(e) => onUpdatePrompt(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key.toLowerCase() === "e" && (e.metaKey || e.ctrlKey)) {
                                        e.preventDefault();
                                        setExpandOpen(true);
                                    }
                                }}
                                placeholder={t("promptPlaceholder")}
                                className="w-full resize-none bg-transparent border-l-2 border-glass-border pl-3.5 pr-8 py-1 text-[13px] leading-[1.7] text-foreground placeholder:text-text-muted focus:outline-none focus:border-l-primary/40 focus:bg-glass/30 transition-all duration-200 min-h-[80px] max-h-[260px] overflow-y-auto"
                                rows={5}
                            />
                            {/* Expand-to-modal icon — top-right,
                                always visible. 24×24 hit area on
                                a 14×14 visual via padding. */}
                            <button
                                type="button"
                                onClick={() => setExpandOpen(true)}
                                aria-label={t("promptExpand")}
                                title={`${t("promptExpand")} (⌘/Ctrl + E)`}
                                className="btn-tip absolute right-1.5 top-1.5 grid h-6 w-6 place-items-center rounded text-text-muted/70 transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                            >
                                <Maximize2 size={12} aria-hidden="true" />
                            </button>
                        </div>

                        {/* AI Polish — bilingual prompt rewrite using
                            the project's polish system prompt
                            (storyboard_polish / video_polish /
                            r2v_polish from PromptConfig). Routes to
                            the right API by tabMode. */}
                        <PolishPanel
                            prompt={shot.prompt}
                            tabMode={shot.tabMode}
                            scriptId={currentProjectId ?? ""}
                            slots={r2vSlots}
                            imageUrls={polishImageUrls}
                            onApply={onUpdatePrompt}
                        />

                        {/* Structured field tags — interactive Popover editors */}
                        <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
                            {/* Duration: always visible */}
                            <FieldTagChip
                                field="duration"
                                value={shot.duration}
                                editorConfig={durationEditorConfig
                                    ? { type: "duration", ...durationEditorConfig }
                                    : { type: "duration", min: 3, max: 15, step: 1 }
                                }
                                onChange={(v) => onUpdateField("duration", v)}
                            />
                            {/* Shot size: visible when has value */}
                            {shot.shotSize !== undefined && shot.shotSize !== null && (
                                <FieldTagChip
                                    field="shotSize"
                                    value={shot.shotSize}
                                    editorConfig={{ type: "preset", presets: ["特写", "近景", "中景", "全景", "远景", "大特写"] }}
                                    onChange={(v) => onUpdateField("shotSize", v)}
                                />
                            )}
                            {/* Camera angle: visible when has value */}
                            {shot.cameraAngle !== undefined && shot.cameraAngle !== null && (
                                <FieldTagChip
                                    field="cameraAngle"
                                    value={shot.cameraAngle}
                                    editorConfig={{ type: "preset", presets: ["平视", "俯视", "仰视", "鸟瞰", "低角度"] }}
                                    onChange={(v) => onUpdateField("cameraAngle", v)}
                                />
                            )}
                            {/* Camera movement: visible when has value */}
                            {shot.cameraMovementStructured && (
                                <FieldTagChip
                                    field="cameraMovement"
                                    value={shot.cameraMovementStructured.description || shot.cameraMovementStructured.primary}
                                    editorConfig={{ type: "preset", presets: ["固定镜头", "缓慢推进", "跟随平移", "环绕旋转", "快速拉远", "缓慢上升"] }}
                                    onChange={(v) => onUpdateField("cameraMovement", v)}
                                />
                            )}
                            {/* Transition hint: visible when has value */}
                            {shot.transitionHint !== undefined && shot.transitionHint !== null && (
                                <FieldTagChip
                                    field="transitionHint"
                                    value={shot.transitionHint}
                                    editorConfig={{ type: "preset", presets: ["硬切", "淡入淡出", "溶解", "闪白", "划像"], allowCustom: true }}
                                    onChange={(v) => onUpdateField("transitionHint", v)}
                                />
                            )}
                            {/* "+" button to add optional fields */}
                            <AddFieldButton
                                onAdd={(field: FieldType) => {
                                    if (field === "cameraMovement") {
                                        onUpdateField("cameraMovement", "固定镜头");
                                    } else {
                                        onUpdateField(field, "");
                                    }
                                }}
                            />
                        </div>

                        {/* Dialogue text display (read-only — editing via 配音工作台 modal) */}
                        {shot.dialogueStructured?.line && (
                            <div className="pl-3.5 border-l-2 border-accent/40">
                                <span className="block font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-text-muted">
                                    {shot.dialogueStructured.speaker}
                                </span>
                                <span className="block font-display text-[0.90625rem] italic leading-snug text-text-secondary">
                                    “{shot.dialogueStructured.line}”
                                </span>
                            </div>
                        )}

                        {/* Assembled prompt preview (read-only, collapsible) — uses buildAssembledPrompt for real-time computation */}
                        {(shot.prompt || shot.shotSize || shot.cameraMovementStructured) && (
                            <div className="mt-1">
                                <button
                                    type="button"
                                    onClick={() => setPromptPreviewOpen(v => !v)}
                                    className="inline-flex items-center gap-1 text-[0.6875rem] text-text-muted hover:text-text-secondary transition-colors"
                                >
                                    <Code2 size={12} strokeWidth={1.5} />
                                    <span>{t("viewFinalPrompt")}</span>
                                    <ChevronRight
                                        size={11}
                                        className={`transition-transform duration-200 ${promptPreviewOpen ? "rotate-90" : ""}`}
                                    />
                                </button>
                                <AnimatePresence>
                                    {promptPreviewOpen && (
                                        <motion.div
                                            initial={{ height: 0, opacity: 0 }}
                                            animate={{ height: "auto", opacity: 1 }}
                                            exit={{ height: 0, opacity: 0 }}
                                            transition={{ duration: 0.2 }}
                                            className="overflow-hidden"
                                        >
                                            <div className="mt-1.5 rounded-md border border-glass-border bg-black/20 px-3 py-2 text-[0.71875rem] leading-relaxed font-mono space-y-2">
                                                {/* Final prompt as model receives it (computed real-time) */}
                                                <p className="text-text-secondary whitespace-pre-wrap">
                                                    {assembledPromptPreview}
                                                </p>
                                                {/* Duration is the only field NOT in prompt — show as API param note */}
                                                {shot.duration && (
                                                    <p className="text-text-muted border-t border-border-subtle pt-1.5">
                                                        <span className="text-status-completed-fg/70">{t("durationLabel")}:</span> {shot.duration}s {t("durationApiNote")}
                                                    </p>
                                                )}
                                            </div>
                                        </motion.div>
                                    )}
                                </AnimatePresence>
                            </div>
                        )}

                        {/* Asset Chip Bar */}
                        <AssetChipBar
                            characters={characters}
                            scenes={scenes}
                            props={props}
                            onInsertAsset={handleInsertAssetFromChip}
                        />
                    </div>
                </div>

                {/* actions row — full-width per mock (lives outside editor/card-body) */}
                <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-t border-border-subtle">
                            <div className="flex items-center gap-1 shrink-0">
                                <motion.button
                                    whileHover={{ scale: 1.06 }}
                                    whileTap={{ scale: 0.94 }}
                                    onClick={onOpenDrawer}
                                    className="ico-btn flex h-8 w-8 items-center justify-center rounded-[14px] text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                    title={t("browseAssets")}
                                >
                                    <AtSign size={15} strokeWidth={1.8} />
                                </motion.button>
                                <motion.button
                                    whileHover={{ scale: 1.06 }}
                                    whileTap={{ scale: 0.94 }}
                                    onClick={onMoveUp}
                                    disabled={structureDisabled || index === 0}
                                    className="ico-btn flex h-8 w-8 items-center justify-center rounded-[14px] text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground disabled:opacity-25 disabled:hover:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                    title={t("moveUp")}
                                    aria-label={t("moveUp")}
                                >
                                    <ChevronUp size={15} strokeWidth={1.8} />
                                </motion.button>
                                <motion.button
                                    whileHover={{ scale: 1.06 }}
                                    whileTap={{ scale: 0.94 }}
                                    onClick={onMoveDown}
                                    disabled={structureDisabled || index === totalShots - 1}
                                    className="ico-btn flex h-8 w-8 items-center justify-center rounded-[14px] text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground disabled:opacity-25 disabled:hover:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                    title={t("moveDown")}
                                    aria-label={t("moveDown")}
                                >
                                    <ChevronDown size={15} strokeWidth={1.8} />
                                </motion.button>
                                <motion.button
                                    whileHover={{ scale: 1.06 }}
                                    whileTap={{ scale: 0.94 }}
                                    onClick={onDuplicate}
                                    disabled={structureDisabled}
                                    className="ico-btn flex h-8 w-8 items-center justify-center rounded-[14px] text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                    title={t("duplicateShot")}
                                    aria-label={t("duplicateShot")}
                                >
                                    <Copy size={15} strokeWidth={1.8} />
                                </motion.button>
                                {onRefineFrame && (
                                    <motion.button
                                        whileHover={{ scale: 1.06 }}
                                        whileTap={{ scale: 0.94 }}
                                        onClick={onRefineFrame}
                                        className="ico-btn flex h-8 w-8 items-center justify-center rounded-[14px] text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                        title={t("refineFrame")}
                                    >
                                        <Sparkles size={15} strokeWidth={1.8} />
                                    </motion.button>
                                )}
                                <motion.button
                                    whileHover={{ scale: 1.06 }}
                                    whileTap={{ scale: 0.94 }}
                                    onClick={onDelete}
                                    disabled={structureDisabled}
                                    className="ico-btn flex h-8 w-8 items-center justify-center rounded-[14px] text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-status-failed-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                    title={t("deleteShot")}
                                    aria-label={t("deleteShot")}
                                >
                                    <Trash2 size={15} strokeWidth={1.8} />
                                </motion.button>
                            </div>

                            <div className="flex items-center gap-3">
                                {genSummary && (
                                    <span className="inline-flex items-center gap-1.5 shrink-0 font-mono text-[0.6875rem] text-text-secondary">
                                        <span className="h-1.5 w-1.5 rounded-full bg-primary shadow-[var(--glow-primary)]" />
                                        {genSummary}
                                    </span>
                                )}
                                <span className="font-mono text-[0.59375rem] uppercase tracking-[0.1em] text-text-muted hidden sm:inline">
                                    {t("countLabel")}
                                </span>
                                <div className="flex items-center gap-0.5 shrink-0 p-[3px] rounded-full bg-surface-inset">
                                    {[1, 2, 4, 6].map((n) => {
                                        const active = generateCount === n;
                                        return (
                                            <button
                                                key={n}
                                                type="button"
                                                onClick={() => onSetGenerateCount?.(n)}
                                                aria-pressed={active}
                                                aria-label={`Generate ${n} at a time`}
                                                title={t("genCandidatesEachTooltip", { n })}
                                                className={`grid h-7 min-w-[28px] place-items-center rounded-full font-mono text-[0.625rem] font-semibold transition-colors duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55 ${
                                                    active
                                                        ? "bg-primary text-on-accent"
                                                        : "text-text-muted hover:text-foreground"
                                                }`}
                                            >
                                                ×{n}
                                            </button>
                                        );
                                    })}
                                </div>
                                <motion.button
                                    whileHover={generationActionable ? { scale: 1.02 } : undefined}
                                    whileTap={generationActionable ? { scale: 0.98 } : undefined}
                                    type="button"
                                    onClick={() => { if (generationActionable) onGenerateBatch?.(generateCount); }}
                                    disabled={!generationActionable}
                                    title={generationBlockedMessage ?? (!canGenerate
                                        ? (shot.tabMode === "t2i_i2v"
                                            ? t("needFirstFrameTooltip")
                                            : t("needPromptInputTooltip"))
                                        : t("genVideoCandidatesTooltip", { count: generateCount }))}
                                    className="inline-flex items-center justify-center gap-1.5 rounded-full px-[13px] py-[7px] font-sans text-[0.75rem] font-semibold tracking-tight transition-all duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55 disabled:cursor-not-allowed disabled:opacity-40 bg-primary text-on-accent shadow-[var(--btn-pri-glow),inset_0_1.5px_0_rgba(255,255,255,0.14)] hover:bg-primary-hover hover:-translate-y-px disabled:hover:translate-y-0"
                                >
                                    {generationBlockedMessage ? (
                                        <>
                                            {generationBlockedBusy ? <Loader2 size={14} className="animate-spin" strokeWidth={2} /> : null}
                                            <span>{generationBlockedMessage}</span>
                                        </>
                                    ) : inFlightCount > 0 ? (
                                        <>
                                            <Loader2 size={14} className="animate-spin" strokeWidth={2} />
                                            <span>{t("genClusterInFlight", { count: inFlightCount })}</span>
                                        </>
                                    ) : (
                                        <>
                                            <Sparkles size={14} strokeWidth={2} />
                                            <span>{t("generateBatch", { count: generateCount })}</span>
                                        </>
                                    )}
                                </motion.button>
                            </div>
                        </div>

                        {/* Disclosure bar — controls attached panel visibility */}
                        <button
                            type="button"
                            onClick={onToggleExpanded}
                            aria-expanded={expanded}
                            aria-label={expanded ? t("collapseShot") : t("expandShot")}
                            className="group/disc flex w-full items-center gap-2.5 border-t border-border-subtle px-5 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.14em] text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            {expanded ? (
                                <ChevronUp size={13} strokeWidth={2} className="text-text-muted transition-transform duration-fast group-hover/disc:text-text-secondary" aria-hidden="true" />
                            ) : (
                                <ChevronDown size={13} strokeWidth={2} className="text-text-muted transition-transform duration-fast group-hover/disc:text-text-secondary" aria-hidden="true" />
                            )}
                            <span className="text-foreground font-semibold">{expanded ? t("collapseShotShort") : t("expandShotShort")}</span>
                            <span className="text-text-muted/80">· {t("panelLabel")}</span>
                            {expanded ? (
                                <ChevronUp size={13} strokeWidth={2} className="ml-auto text-text-muted/60" aria-hidden="true" />
                            ) : (
                                <ChevronDown size={13} strokeWidth={2} className="ml-auto text-text-muted/60" aria-hidden="true" />
                            )}
                        </button>
            </div>
            {/* Focus-editor modal (B5 escape hatch) — opens via the
                expand icon or Cmd/Ctrl+E. Cancel discards; Save
                propagates back through the same onUpdatePrompt
                path the inline textarea uses. */}
            {expandOpen ? (
                <PromptExpandModal
                    initialValue={shot.prompt}
                    shotLabel={`Shot ${index + 1}`}
                    placeholder={t("promptPlaceholder")}
                    onSave={(next) => {
                        onUpdatePrompt(next);
                        setExpandOpen(false);
                    }}
                    onClose={() => setExpandOpen(false)}
                />
            ) : null}
        </div>
    );
}
