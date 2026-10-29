"use client";
/**
 * PreviewImage — drop-in replacement for raw `<img>` across the workbench
 * (Issue 14). Three layers of value over bare `<img>`:
 *
 *   1. URL resolution — auto-routes raw paths through getAssetUrl() so
 *      callers never accidentally render a relative URL the browser will
 *      resolve to the dev server origin (the original ShotCard:267 bug).
 *
 *   2. Error fallback — onError swaps to a sized-adaptive panel:
 *        - micro (≤ 40px any dim): just ⚠ icon, click to retry
 *        - mid  (≤ 120px): ⚠ + small Retry link
 *        - large (> 120px): ⚠ + label + Retry + Copy URL buttons
 *      One automatic retry first (cache-bust ?retry=1) — most "broken"
 *      images are transient cache / OSS-signature blips that retry fixes.
 *      Second failure stays on the panel, user does diagnose.
 *
 *   3. Click-to-lightbox — hover reveals a 🔍 button (top-right) that
 *      opens the singleton LightboxProvider. If wrapped in a
 *      LightboxGroupRegistrar, lightbox shows ← → prev/next.
 *
 * FUTURE SCANNING POINTS (other modules still using raw <img>, replace
 * opportunistically next time they're touched):
 *   - frontend/src/components/modules/VideoCreator.tsx
 *   - frontend/src/components/modules/CharacterWorkbench.tsx
 *   - frontend/src/components/modules/ConsistencyVault.tsx
 *   - frontend/src/components/canvas/* (storyboard frame thumbnails)
 */
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, RefreshCw, Maximize2, Copy, Check } from "lucide-react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { getAssetUrl } from "@/lib/utils";
import { useLightbox, type LightboxItem } from "./LightboxProvider";

export interface PreviewImageProps {
    /** Raw URL — relative paths auto-resolve via getAssetUrl. Empty/undefined
     *  renders the placeholder (no fallback panel, just empty state). */
    src?: string;
    alt?: string;
    className?: string;
    /** Disable click-to-lightbox + 🔍 button (e.g. cast avatars in chip bar
     *  where lightbox is overkill for tiny 16px chips). */
    noLightbox?: boolean;
    /** Optional: when the parent has wrapped multiple PreviewImages in a
     *  LightboxGroupRegistrar, supply this so click opens the group at the
     *  right index instead of as a singleton. */
    groupId?: string;
    groupIndex?: number;
    /** Force the magnify button visible even without hover (e.g. on touch
     *  devices). Default: hover-only. */
    alwaysShowMagnify?: boolean;
    /** Whole-thumb click opens lightbox (in addition to the 🔍 button).
     *  Default false — most callers own the click for "select" / "play".
     *  Turn this on for read-only thumbs (e.g. TaskQueuePanel queue rows)
     *  where there's no other click semantic. */
    clickToLightbox?: boolean;
    /** Render the placeholder slot (no src) without the error styling —
     *  e.g. "no T2I yet" empty state should be neutral, not red. */
    placeholder?: React.ReactNode;
}

export default function PreviewImage({
    src, alt, className, noLightbox = false,
    groupId, groupIndex, alwaysShowMagnify = false,
    clickToLightbox = false, placeholder,
}: PreviewImageProps) {
    const t = useTranslations("preview");
    const { open, openInGroup } = useLightbox();
    const [errored, setErrored] = useState(false);
    const [retryNonce, setRetryNonce] = useState(0);
    const [hasRetriedOnce, setHasRetriedOnce] = useState(false);
    const [copied, setCopied] = useState(false);
    const wrapperRef = useRef<HTMLDivElement | null>(null);
    /** Sized-adaptive bucket; recomputed on resize via ResizeObserver. */
    const [sizeBucket, setSizeBucket] = useState<"micro" | "mid" | "large">("large");

    useEffect(() => {
        setErrored(false);
        setHasRetriedOnce(false);
        setRetryNonce(0);
    }, [src]);

    useEffect(() => {
        const el = wrapperRef.current;
        if (!el) return;
        const measure = () => {
            const { width, height } = el.getBoundingClientRect();
            const min = Math.min(width, height);
            if (min <= 40) setSizeBucket("micro");
            else if (min <= 120) setSizeBucket("mid");
            else setSizeBucket("large");
        };
        measure();
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    const resolved = getAssetUrl(src);
    /** Append retry-bust to bypass browser/OSS cache on retry. */
    const displaySrc = resolved
        ? (retryNonce > 0 ? `${resolved}${resolved.includes("?") ? "&" : "?"}__r=${retryNonce}` : resolved)
        : "";

    const handleError = () => {
        // First failure → silent automatic retry once (cache hiccup / signature blip).
        // Second failure → surface fallback panel.
        if (!hasRetriedOnce) {
            setHasRetriedOnce(true);
            setRetryNonce(n => n + 1);
        } else {
            setErrored(true);
        }
    };

    const handleManualRetry = () => {
        setErrored(false);
        setRetryNonce(n => n + 1);
    };

    const handleCopyUrl = async () => {
        if (!resolved) return;
        try {
            await navigator.clipboard.writeText(resolved);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked */
        }
    };

    const handleOpenLightbox = (e?: React.MouseEvent) => {
        if (e) e.stopPropagation();
        if (noLightbox || !src) return;
        const item: LightboxItem = { src, alt, kind: "image" };
        if (groupId && typeof groupIndex === "number") openInGroup(groupId, groupIndex);
        else open(item);
    };

    if (!src) {
        return (
            <div ref={wrapperRef} className={clsx("relative overflow-hidden", className)}>
                {placeholder ?? null}
            </div>
        );
    }

    const clickable = clickToLightbox && !noLightbox && !errored && !!src;

    return (
        <div
            ref={wrapperRef}
            className={clsx(
                "group/preview relative overflow-hidden",
                clickable && "cursor-zoom-in",
                className,
            )}
            onClick={clickable ? handleOpenLightbox : undefined}
            role={clickable ? "button" : undefined}
            tabIndex={clickable ? 0 : undefined}
            onKeyDown={clickable ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleOpenLightbox();
                }
            } : undefined}
            aria-label={clickable ? t("zoom") : undefined}
        >
            {!errored ? (
                <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                        src={displaySrc}
                        alt={alt ?? ""}
                        loading="lazy"
                        onError={handleError}
                        className="h-full w-full object-cover"
                    />
                    {!noLightbox && sizeBucket !== "micro" ? (
                        <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); handleOpenLightbox(); }}
                            aria-label={t("zoom")}
                            title={t("zoom")}
                            className={clsx(
                                "absolute right-1 top-1 grid h-6 w-6 place-items-center rounded bg-black/55 text-foreground backdrop-blur transition-opacity duration-fast ease-out-quart hover:bg-black/75 focus-visible:outline-none focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-primary/55",
                                alwaysShowMagnify
                                    ? "opacity-100"
                                    : "opacity-0 group-hover/preview:opacity-100",
                            )}
                        >
                            <Maximize2 size={11} aria-hidden="true" />
                        </button>
                    ) : null}
                </>
            ) : (
                <FallbackPanel
                    sizeBucket={sizeBucket}
                    url={resolved}
                    onRetry={handleManualRetry}
                    onCopyUrl={handleCopyUrl}
                    copied={copied}
                />
            )}
        </div>
    );
}

interface FallbackPanelProps {
    sizeBucket: "micro" | "mid" | "large";
    url: string;
    onRetry: () => void;
    onCopyUrl: () => void;
    copied: boolean;
}

function FallbackPanel({ sizeBucket, url, onRetry, onCopyUrl, copied }: FallbackPanelProps) {
    const t = useTranslations("preview");
    // Micro (≤ 40px) — just ⚠ icon, whole panel = retry on click
    if (sizeBucket === "micro") {
        return (
            <button
                type="button"
                onClick={onRetry}
                title={`${t("imgLoadFailedRetry")}\n${url}`}
                aria-label={t("imgLoadFailedRetry")}
                className="grid h-full w-full place-items-center bg-status-failed-bg text-status-failed-fg transition-colors duration-fast ease-out-quart hover:bg-status-failed-bg/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
            >
                <AlertTriangle size={12} aria-hidden="true" />
            </button>
        );
    }

    // Mid (≤ 120px) — ⚠ icon + small Retry link, URL in tooltip
    if (sizeBucket === "mid") {
        return (
            <div
                title={url}
                className="grid h-full w-full place-items-center gap-1 bg-status-failed-bg p-2 text-status-failed-fg"
            >
                <AlertTriangle size={16} aria-hidden="true" />
                <button
                    type="button"
                    onClick={onRetry}
                    className="inline-flex items-center gap-0.5 font-mono text-chrome-sm font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
                >
                    <RefreshCw size={10} aria-hidden="true" />
                    {t("retry")}
                </button>
            </div>
        );
    }

    // Large (> 120px) — full panel with retry + copy URL + visible URL preview
    return (
        <div
            role="alert"
            className="grid h-full w-full place-items-center gap-2 bg-status-failed-bg p-4 text-center text-status-failed-fg"
        >
            <AlertTriangle size={22} aria-hidden="true" />
            <div className="space-y-1">
                <p className="font-sans text-body-sm font-medium">{t("imgLoadFailed")}</p>
                <p
                    className="max-w-[26rem] truncate font-mono text-chrome-sm text-status-failed-fg/75"
                    title={url}
                >
                    {url}
                </p>
            </div>
            <div className="flex items-center gap-1.5 pt-1">
                <button
                    type="button"
                    onClick={onRetry}
                    className="inline-flex min-h-[28px] items-center gap-1 rounded border border-status-failed-border bg-status-failed-bg px-2.5 py-1 font-mono text-chrome font-medium text-status-failed-fg transition-colors duration-fast ease-out-quart hover:bg-status-failed-fg/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-failed-border"
                >
                    <RefreshCw size={11} aria-hidden="true" />
                    {t("retry")}
                </button>
                <button
                    type="button"
                    onClick={onCopyUrl}
                    className="inline-flex min-h-[28px] items-center gap-1 rounded border border-glass-border bg-black/30 px-2.5 py-1 font-mono text-chrome font-medium text-text-secondary transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                >
                    {copied ? <Check size={11} /> : <Copy size={11} />}
                    {copied ? t("copied") : t("copyUrl")}
                </button>
            </div>
        </div>
    );
}
