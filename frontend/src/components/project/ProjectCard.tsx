"use client";

import { motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { Download, Play, Trash2, Film, Clock, MoreVertical, ExternalLink, Star } from "lucide-react";
import { useTranslations } from "next-intl";
import { Project } from "@/store/projectStore";
import { useSettingsStore } from "@/store/settingsStore";
import { getAssetUrl } from "@/lib/utils";
import { coverGradient, GRAIN_URL } from "@/lib/cover";
import { api } from "@/lib/api";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { isMergeVideoCurrent } from "@/lib/mergeFreshness";
import { safeProjectPackageFilename } from "@/lib/projectPackage";

interface ProjectCardProps {
    project: Project;
    onDelete: (id: string) => Promise<void> | void;
}

export type DerivedStatus = "completed" | "processing" | "pending";

// Derive a cover image from the project's frames / scenes. The backend has no
// dedicated cover field, so we fall back through the richest available source.
export function deriveCover(project: Project): string | undefined {
    const frames = (project.frames || []) as Array<Record<string, any>>;
    for (const f of frames) {
        const direct = f?.rendered_image_url || f?.image_url;
        if (direct) return getAssetUrl(direct);
        const variants = f?.rendered_image_asset?.variants || f?.image_asset?.variants;
        if (variants?.length) {
            const sel = variants.find((v: any) => v.id === (f?.rendered_image_asset?.selected_id || f?.image_asset?.selected_id));
            const url = sel?.url || variants[0]?.url;
            if (url) return getAssetUrl(url);
        }
    }
    for (const s of (project.scenes || []) as Array<Record<string, any>>) {
        if (s?.image_url) return getAssetUrl(s.image_url);
        const variants = s?.image_asset?.variants;
        if (variants?.length) {
            const url = variants[0]?.url;
            if (url) return getAssetUrl(url);
        }
    }
    return undefined;
}

// Status is absent on the data model, so derive a coarse lifecycle state:
// a merged video => completed; rendered frames present => processing; else draft.
export function deriveStatus(project: Project): DerivedStatus {
    if (isMergeVideoCurrent(project, getRuntimeEnvironment()?.key ?? "")) return "completed";
    const frames = (project.frames || []) as Array<Record<string, any>>;
    const rendered = frames.some((f) => f?.rendered_image_url || f?.image_url);
    if (rendered) return "processing";
    return "pending";
}

export default function ProjectCard({ project, onDelete }: ProjectCardProps) {
    const t = useTranslations("project");
    const tCommon = useTranslations("common");
    const tPersistence = useTranslations("persistence");
    const locale = useSettingsStore((s) => s.locale);

    const [menuOpen, setMenuOpen] = useState(false);
    const [deletePending, setDeletePending] = useState(false);
    const [exportPending, setExportPending] = useState(false);
    const [deleteError, setDeleteError] = useState<string | null>(null);
    const [exportError, setExportError] = useState<string | null>(null);
    const menuWrapRef = useRef<HTMLDivElement>(null);
    const firstItemRef = useRef<HTMLButtonElement>(null);
    const mountedRef = useRef(false);
    const latestProjectRef = useRef(project);
    const exportInFlightRef = useRef(false);
    const exportOperationRef = useRef(0);
    const [runtimeRevision, setRuntimeRevision] = useState(0);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            exportOperationRef.current += 1;
        };
    }, []);

    useEffect(() => {
        latestProjectRef.current = project;
    }, [project]);

    useEffect(() => {
        exportOperationRef.current += 1;
        exportInFlightRef.current = false;
        setExportPending(false);
        setExportError(null);
    }, [project.id]);

    useEffect(() => subscribeRuntimeEnvironment(() => {
        exportOperationRef.current += 1;
        exportInFlightRef.current = false;
        setExportPending(false);
        setExportError(null);
        setRuntimeRevision(value => value + 1);
    }), []);

    // Close the actions menu on outside click / Escape, and move focus to the
    // first item when it opens (keyboard + a11y parity with the rest of Studio).
    useEffect(() => {
        if (!menuOpen) return;
        firstItemRef.current?.focus();
        const onDocDown = (e: MouseEvent) => {
            if (menuWrapRef.current && !menuWrapRef.current.contains(e.target as Node)) {
                setMenuOpen(false);
            }
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") setMenuOpen(false);
        };
        document.addEventListener("mousedown", onDocDown);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("mousedown", onDocDown);
            document.removeEventListener("keydown", onKey);
        };
    }, [menuOpen]);

    void runtimeRevision;
    const cover = deriveCover(project);
    const status = deriveStatus(project);
    const frameCount = project.frames?.length || 0;

    // Featured == user-starred (amber-halation signature). Optimistic local
    // state, initialised from the server flag; rolls back if the toggle fails.
    const [starred, setStarred] = useState<boolean>(!!project.starred);
    const isFeatured = starred;
    // Cover image can 404 at runtime; on error fall back to the typographic
    // gradient cover (same as no-image) instead of the ugly broken-img glyph.
    const [coverError, setCoverError] = useState(false);

    const handleToggleStar = async (e: React.MouseEvent) => {
        e.stopPropagation();
        const prev = starred;
        setStarred(!prev); // optimistic
        try {
            await api.toggleProjectStarred(project.id);
        } catch {
            setStarred(prev); // rollback on failure
        }
    };

    const handleOpen = () => {
        // Series episodes open through the series → episode route so the
        // series/episode breadcrumb context is preserved; standalone
        // projects fall back to the flat project route.
        window.location.hash = project.series_id
            ? `#/series/${project.series_id}/episode/${project.id}`
            : `#/project/${project.id}`;
    };

    const handleDelete = async (e: React.MouseEvent) => {
        e.stopPropagation();
        if (deletePending) return;
        setDeleteError(null);
        if (!confirm(t("confirmDelete", { title: project.title }))) return;

        setDeletePending(true);
        try {
            await onDelete(project.id);
            setMenuOpen(false);
        } catch (error) {
            console.error('Failed to delete project:', error);
            setDeleteError(tPersistence("failed"));
            setMenuOpen(true);
        } finally {
            setDeletePending(false);
        }
    };

    const handleExportPackage = async (e: React.MouseEvent) => {
        e.stopPropagation();
        if (exportInFlightRef.current) return;
        const environment = requireActiveRuntimeEnvironment();
        const capturedProject = project;
        const operation = ++exportOperationRef.current;
        const isCurrentExportOperation = () => operation === exportOperationRef.current && mountedRef.current
            && latestProjectRef.current.id === capturedProject.id
            && isCurrentRuntimeEnvironment(environment.key, environment.generation);
        let objectUrl: string | null = null;
        setExportError(null);
        exportInFlightRef.current = true;
        setExportPending(true);
        try {
            const result = await api.exportProjectPackage(capturedProject.id);
            if (!isCurrentExportOperation()) return;
            objectUrl = URL.createObjectURL(result.blob);
            const anchor = document.createElement("a");
            anchor.href = objectUrl;
            anchor.download = safeProjectPackageFilename(capturedProject.title, capturedProject.id, result.filename);
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(objectUrl);
            objectUrl = null;
            setMenuOpen(false);
        } catch (error) {
            if (!isCurrentExportOperation()) return;
            console.error("Failed to export project package:", error);
            setExportError(error instanceof Error ? error.message : t("exportPackageFailed"));
            setMenuOpen(true);
        } finally {
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            if (isCurrentExportOperation()) {
                exportInFlightRef.current = false;
                setExportPending(false);
            }
        }
    };

    const badge = {
        completed: { label: t("statusCompleted"), cls: "text-status-completed-fg bg-status-completed-bg border-status-completed-border" },
        processing: { label: t("statusProcessing"), cls: "text-status-processing-fg bg-status-processing-bg border-status-processing-border" },
        pending: { label: t("statusDraft"), cls: "text-status-pending-fg bg-status-pending-bg border-status-pending-border" },
    }[status];

    const rawCreated = (project as any).created_at;
    const dateMs = project.createdAt
        ? new Date(project.createdAt).getTime()
        : typeof rawCreated === "number"
            ? rawCreated * 1000
            : NaN;
    const dateStr = Number.isFinite(dateMs)
        ? new Date(dateMs).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US")
        : "";

    return (
        <motion.article
            className={`glass-panel atelier-proj-card ${isFeatured ? "atelier-proj-featured" : ""} group relative rounded-2xl overflow-hidden cursor-pointer border border-glass-border`}
            onClick={handleOpen}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
                // Only activate when the keydown originates on the card itself,
                // not on a nested control (delete/more), whose Enter/Space would
                // otherwise bubble here and trigger navigation.
                if (e.target !== e.currentTarget) return;
                if (e.key === "Enter" || e.key === " ") {
                    if (e.key === " ") e.preventDefault(); // avoid page scroll on Space
                    handleOpen();
                }
            }}
        >
            {/* Thumbnail */}
            <div className="relative aspect-[16/10] overflow-hidden bg-surface-inset">
                {cover && !coverError ? (
                    <img
                        src={cover}
                        alt={project.title}
                        onError={() => setCoverError(true)}
                        className="w-full h-full object-cover transition-transform duration-500 ease-out group-hover:scale-[1.04]"
                    />
                ) : (
                    // Typographic cover — a deterministic graphite/teal gradient stands in
                    // for the image; the serif project name (bottom overlay) reads over it.
                    <div
                        className="absolute inset-0"
                        style={{ background: coverGradient(project.id || project.title) }}
                        aria-hidden="true"
                    >
                        {/* fine film grain — tactile texture over the flat gradient */}
                        <div
                            className="absolute inset-0 pointer-events-none mix-blend-overlay"
                            style={{ backgroundImage: GRAIN_URL, opacity: 0.07 }}
                        />
                        {/* vignette — darkens the edges, lifts the title zone */}
                        <div
                            className="absolute inset-0 pointer-events-none"
                            style={{ background: "radial-gradient(120% 120% at 50% 38%, transparent 50%, rgb(0 0 0 / 0.55))" }}
                        />
                    </div>
                )}
                {/* Gradient legibility scrim */}
                <div className="absolute inset-0 pointer-events-none bg-gradient-to-b from-transparent from-40% to-black/60" />

                {/* Featured amber halation — warm inset glow laid over the
                    thumbnail (starred signature; replaces the old teal ring). */}
                {isFeatured ? (
                    <div className="atelier-proj-halation absolute inset-0 pointer-events-none z-[1]" aria-hidden="true" />
                ) : null}

                {/* Star toggle — top-right; always shown when starred, hover/focus-revealed otherwise */}
                <div className="absolute top-3 right-3 z-[3]">
                    <button
                        type="button"
                        onClick={handleToggleStar}
                        aria-label={starred ? t("unstar") : t("star")}
                        aria-pressed={starred}
                        className={`w-8 h-8 rounded-full grid place-items-center backdrop-blur-md transition-all bg-black/35 hover:bg-black/55 ${starred ? "text-status-starred-solid opacity-100" : "text-foreground/70 hover:text-foreground opacity-0 group-hover:opacity-100 focus-visible:opacity-100"}`}
                    >
                        <Star size={15} fill={starred ? "currentColor" : "none"} aria-hidden="true" />
                    </button>
                </div>

                {/* Status badge — top-left */}
                <div className="absolute top-3 left-3 z-[2]">
                    <span className={`atelier-badge inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[0.59375rem] font-mono font-semibold uppercase tracking-wider ${badge.cls}`}>
                        <span className="w-[5px] h-[5px] rounded-full bg-current" />
                        {badge.label}
                    </span>
                </div>

                {/* Hover-reveal play */}
                <div className="absolute inset-0 z-[2] grid place-items-center opacity-0 group-hover:opacity-100 transition-opacity duration-200">
                    <span className="w-12 h-12 rounded-full grid place-items-center shadow-lg bg-foreground/90">
                        <Play size={18} className="text-on-accent ml-0.5" fill="currentColor" />
                    </span>
                </div>

                {/* Title + meta overlay — bottom-left */}
                <div className="absolute bottom-3 left-4 right-4 z-[2]">
                    <h3 className="font-display atelier-display text-[1.375rem] font-semibold leading-[1.05] tracking-tight text-white drop-shadow-[0_2px_12px_rgba(0,0,0,0.6)] truncate">
                        {project.title}
                    </h3>
                    <div className="font-mono text-[0.5625rem] uppercase tracking-wider mt-1 truncate text-foreground/75">
                        {project.episode_number ? `EP.${String(project.episode_number).padStart(2, "0")} · ` : ""}
                        {t("shotCount", { count: frameCount })}
                    </div>
                </div>
            </div>

            {/* Meta footer */}
            <div className="flex items-center justify-between px-4 py-3.5">
                <div className="flex flex-col gap-1.5 min-w-0">
                    <span className="font-mono text-[0.5625rem] uppercase tracking-wider text-text-muted truncate">
                        {badge.label}{dateStr ? ` · ${dateStr}` : ""}
                    </span>
                    <div className="flex items-center gap-2.5 font-mono text-[0.625rem] text-text-secondary">
                        <span className="inline-flex items-center gap-1">
                            <Film size={11} className="text-text-muted" />
                            {t("shotCount", { count: frameCount })}
                        </span>
                        <span className="inline-flex items-center gap-1">
                            <Clock size={11} className="text-text-muted" />
                            {project.scenes?.length || 0}
                        </span>
                    </div>
                </div>
                <div className="relative" ref={menuWrapRef} onClick={(e) => e.stopPropagation()}>
                    <button
                        type="button"
                        onClick={(e) => {
                            e.stopPropagation();
                            setMenuOpen((v) => !v);
                        }}
                        className={`w-8 h-8 rounded-lg grid place-items-center transition-colors ${menuOpen ? "text-foreground bg-hover-bg" : "text-text-muted hover:text-foreground hover:bg-hover-bg"}`}
                        aria-label={t("moreActions")}
                        aria-haspopup="menu"
                        aria-expanded={menuOpen}
                    >
                        <MoreVertical size={15} />
                    </button>
                    {menuOpen ? (
                        <div
                            role="menu"
                            aria-label={t("moreActions")}
                            className="absolute right-0 bottom-full z-20 mb-2 w-40 overflow-hidden rounded-md border border-glass-border bg-surface/96 shadow-[0_8px_28px_-6px_rgba(0,0,0,0.7)] backdrop-blur-md"
                        >
                            <button
                                type="button"
                                role="menuitem"
                                ref={firstItemRef}
                                onClick={(e) => {
                                    e.stopPropagation();
                                    setMenuOpen(false);
                                    handleOpen();
                                }}
                                className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left font-sans text-body-sm text-foreground transition-colors hover:bg-primary/12 hover:text-primary focus-visible:outline-none focus-visible:bg-primary/12"
                            >
                                <ExternalLink size={14} aria-hidden="true" />
                                {tCommon("open")}
                            </button>
                            <button
                                type="button"
                                role="menuitem"
                                onClick={(e) => {
                                    void handleExportPackage(e);
                                }}
                                disabled={exportPending}
                                aria-busy={exportPending}
                                className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left font-sans text-body-sm text-foreground transition-colors hover:bg-primary/12 hover:text-primary focus-visible:outline-none focus-visible:bg-primary/12 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                                <Download size={14} aria-hidden="true" />
                                {exportPending ? t("exportPackageBusy") : t("exportPackage")}
                            </button>
                            <button
                                type="button"
                                role="menuitem"
                                onClick={(e) => {
                                    void handleDelete(e);
                                }}
                                disabled={deletePending}
                                aria-busy={deletePending}
                                className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left font-sans text-body-sm text-foreground transition-colors hover:bg-red-500/10 hover:text-red-400 focus-visible:outline-none focus-visible:bg-red-500/10 focus-visible:text-red-400 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                                <Trash2 size={14} aria-hidden="true" />
                                {tCommon("delete")}
                            </button>
                            {deleteError ? (
                                <p role="alert" className="px-3 pb-2 pt-1 text-xs text-status-error-fg">
                                    {deleteError}
                                </p>
                            ) : null}
                            {exportError ? (
                                <p role="alert" className="px-3 pb-2 pt-1 text-xs text-status-error-fg">
                                    {exportError}
                                </p>
                            ) : null}
                        </div>
                    ) : null}
                </div>
            </div>
        </motion.article>
    );
}
