"use client";
/**
 * LightboxProvider — singleton React Context that owns ONE portal-mounted
 * fullscreen viewer for image/video previews across the app (Issue 14).
 *
 * Why singleton:
 *   - Multiple lightboxes open at once is always wrong (z-index war + ESC
 *     ambiguity); a single context guarantees one-at-a-time.
 *   - Keyboard navigation (ESC, ← →) needs one handler scope.
 *   - Group-based prev/next (via LightboxGroup) needs a shared current-
 *     index pointer.
 *
 * Mount once at the app root (ProjectClient or layout). Consumer hooks:
 *   - `useLightbox()` — components call `open({src, alt, kind, group?})`
 *   - `LightboxGroup` — wraps children to register them as a navigable set
 *
 * Visual treatment matches CompareModal (full-screen backdrop, motion-safe
 * enter animation, click-outside / ESC to close).
 *
 * Stacking: the lightbox is opened from *inside* modal layers (Cast workbench
 * detail = z-100, voice dialogs = z-110/z-120), so it must sit above them —
 * otherwise the enlarged image renders behind the dialog and only becomes
 * visible after the dialog is closed. Kept below the toast layer (z-200).
 */
import React, {
    createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { createPortal } from "react-dom";
import { X, ChevronLeft, ChevronRight, Copy, Check, ExternalLink } from "lucide-react";
import { useTranslations } from "next-intl";
import { getAssetUrl } from "@/lib/utils";

export type LightboxItem = {
    /** Raw URL (relative or absolute) — resolved via getAssetUrl on render. */
    src: string;
    alt?: string;
    kind: "image" | "video";
};

interface LightboxContextValue {
    /** Open a single item (no group navigation). */
    open: (item: LightboxItem) => void;
    /** Open within a group at index. Prev/Next arrows navigate inside group. */
    openInGroup: (groupId: string, index: number) => void;
    /** Group registration — called by LightboxGroup. */
    registerGroup: (id: string, items: LightboxItem[]) => void;
    unregisterGroup: (id: string) => void;
}

const LightboxContext = createContext<LightboxContextValue | null>(null);

export function useLightbox(): LightboxContextValue {
    const ctx = useContext(LightboxContext);
    if (!ctx) {
        // Soft fallback so components don't crash if provider isn't mounted yet
        // (e.g. SSR or test). Open becomes a no-op + warns once.
        if (typeof window !== "undefined") {
            // eslint-disable-next-line no-console
            console.warn("[Lightbox] useLightbox called outside <LightboxProvider>; open is a no-op.");
        }
        return {
            open: () => {},
            openInGroup: () => {},
            registerGroup: () => {},
            unregisterGroup: () => {},
        };
    }
    return ctx;
}

interface LightboxProviderProps {
    children: React.ReactNode;
}

export function LightboxProvider({ children }: LightboxProviderProps) {
    const [groups, setGroups] = useState<Record<string, LightboxItem[]>>({});
    const [activeGroup, setActiveGroup] = useState<string | null>(null);
    const [activeIndex, setActiveIndex] = useState(0);
    /** Single-item open path stores in this slot (no group). */
    const [singleItem, setSingleItem] = useState<LightboxItem | null>(null);
    const [copied, setCopied] = useState(false);

    const isOpen = !!singleItem || !!activeGroup;
    const currentItem: LightboxItem | null = singleItem
        ? singleItem
        : (activeGroup ? (groups[activeGroup]?.[activeIndex] ?? null) : null);

    const close = useCallback(() => {
        setSingleItem(null);
        setActiveGroup(null);
        setActiveIndex(0);
        setCopied(false);
    }, []);

    const registerGroup = useCallback((id: string, items: LightboxItem[]) => {
        setGroups(prev => {
            const existing = prev[id];
            // Cheap reference dedup: if items array is identical-by-reference, skip
            // setState to avoid re-render storms when callers re-mount on every parent
            // render. Length + first/last src compare is good enough heuristic.
            if (existing && existing.length === items.length
                && existing[0]?.src === items[0]?.src
                && existing[existing.length - 1]?.src === items[items.length - 1]?.src) {
                return prev;
            }
            return { ...prev, [id]: items };
        });
    }, []);

    const unregisterGroup = useCallback((id: string) => {
        setGroups(prev => {
            if (!(id in prev)) return prev;
            const { [id]: _removed, ...rest } = prev;
            return rest;
        });
        // If the group being unregistered was active, close lightbox
        setActiveGroup(curr => (curr === id ? null : curr));
    }, []);

    const open = useCallback((item: LightboxItem) => {
        setSingleItem(item);
        setActiveGroup(null);
        setActiveIndex(0);
        setCopied(false);
    }, []);

    const openInGroup = useCallback((groupId: string, index: number) => {
        setSingleItem(null);
        setActiveGroup(groupId);
        setActiveIndex(Math.max(0, index));
        setCopied(false);
    }, []);

    // Keyboard handlers — ESC close, ← → navigate (when in group)
    useEffect(() => {
        if (!isOpen) return;
        const groupItems = activeGroup ? (groups[activeGroup] ?? []) : [];
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                e.preventDefault();
                close();
            } else if (e.key === "ArrowLeft" && activeGroup && groupItems.length > 1) {
                e.preventDefault();
                setActiveIndex(idx => (idx - 1 + groupItems.length) % groupItems.length);
            } else if (e.key === "ArrowRight" && activeGroup && groupItems.length > 1) {
                e.preventDefault();
                setActiveIndex(idx => (idx + 1) % groupItems.length);
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [isOpen, activeGroup, groups, close]);

    const handleCopyUrl = useCallback(async () => {
        if (!currentItem) return;
        try {
            await navigator.clipboard.writeText(getAssetUrl(currentItem.src));
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        } catch {
            /* clipboard blocked — silently fail */
        }
    }, [currentItem]);

    const value = useMemo<LightboxContextValue>(() => ({
        open, openInGroup, registerGroup, unregisterGroup,
    }), [open, openInGroup, registerGroup, unregisterGroup]);

    return (
        <LightboxContext.Provider value={value}>
            {children}
            {isOpen && currentItem ? (
                <LightboxPortal
                    item={currentItem}
                    groupCount={activeGroup ? (groups[activeGroup]?.length ?? 0) : 1}
                    groupIndex={activeGroup ? activeIndex : 0}
                    onClose={close}
                    onPrev={activeGroup ? () => {
                        const len = groups[activeGroup!]?.length ?? 0;
                        if (len > 1) setActiveIndex((idx) => (idx - 1 + len) % len);
                    } : null}
                    onNext={activeGroup ? () => {
                        const len = groups[activeGroup!]?.length ?? 0;
                        if (len > 1) setActiveIndex((idx) => (idx + 1) % len);
                    } : null}
                    copied={copied}
                    onCopyUrl={handleCopyUrl}
                />
            ) : null}
        </LightboxContext.Provider>
    );
}

interface LightboxPortalProps {
    item: LightboxItem;
    groupCount: number;
    groupIndex: number;
    onClose: () => void;
    onPrev: (() => void) | null;
    onNext: (() => void) | null;
    copied: boolean;
    onCopyUrl: () => void;
}

function LightboxPortal({
    item, groupCount, groupIndex, onClose, onPrev, onNext, copied, onCopyUrl,
}: LightboxPortalProps) {
    const t = useTranslations("preview");
    const resolved = getAssetUrl(item.src);
    const dialogRef = useRef<HTMLDivElement | null>(null);

    // Focus the dialog on mount so keyboard nav works immediately
    useEffect(() => {
        dialogRef.current?.focus();
    }, []);

    if (typeof document === "undefined") return null;

    const modal = (
        <>
            <div
                aria-hidden="true"
                onClick={onClose}
                className="fixed inset-0 z-[180] bg-black/85 backdrop-blur-sm motion-safe:animate-[lightboxFadeIn_200ms_ease-out_both]"
            />
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-label={item.alt || t("previewAlt")}
                tabIndex={-1}
                className="fixed inset-0 z-[181] flex items-center justify-center p-8 outline-none motion-safe:animate-[lightboxScaleIn_220ms_cubic-bezier(0.22,1,0.36,1)_both]"
            >
                {/* Top-right toolbar */}
                <div className="absolute right-4 top-4 z-[182] flex items-center gap-1">
                    <button
                        type="button"
                        onClick={onCopyUrl}
                        title={t("copyUrl")}
                        className="inline-flex h-9 items-center gap-1.5 rounded-md border border-foreground/15 bg-black/55 px-3 font-mono text-chrome-sm font-medium text-foreground backdrop-blur transition-colors duration-fast ease-out-quart hover:bg-black/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                    >
                        {copied ? <Check size={13} /> : <Copy size={13} />}
                        {copied ? t("copied") : t("copyUrl")}
                    </button>
                    <a
                        href={resolved}
                        target="_blank"
                        rel="noreferrer"
                        title={t("openInNewTab")}
                        className="grid h-9 w-9 place-items-center rounded-md border border-foreground/15 bg-black/55 text-foreground backdrop-blur transition-colors duration-fast ease-out-quart hover:bg-black/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                    >
                        <ExternalLink size={14} />
                    </a>
                    <button
                        type="button"
                        onClick={onClose}
                        title={t("closeEsc")}
                        aria-label={t("close")}
                        className="grid h-9 w-9 place-items-center rounded-md border border-foreground/15 bg-black/55 text-foreground backdrop-blur transition-colors duration-fast ease-out-quart hover:bg-black/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                    >
                        <X size={16} />
                    </button>
                </div>

                {/* Prev / Next chevrons (group nav) */}
                {onPrev && groupCount > 1 ? (
                    <button
                        type="button"
                        onClick={onPrev}
                        aria-label={t("prev")}
                        title={t("prevTitle")}
                        className="absolute left-4 top-1/2 z-[182] grid h-12 w-12 -translate-y-1/2 place-items-center rounded-full border border-foreground/15 bg-black/55 text-foreground backdrop-blur transition-colors duration-fast ease-out-quart hover:bg-black/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                    >
                        <ChevronLeft size={20} />
                    </button>
                ) : null}
                {onNext && groupCount > 1 ? (
                    <button
                        type="button"
                        onClick={onNext}
                        aria-label={t("next")}
                        title={t("nextTitle")}
                        className="absolute right-4 top-1/2 z-[182] grid h-12 w-12 -translate-y-1/2 place-items-center rounded-full border border-foreground/15 bg-black/55 text-foreground backdrop-blur transition-colors duration-fast ease-out-quart hover:bg-black/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                    >
                        <ChevronRight size={20} />
                    </button>
                ) : null}

                {/* Group counter */}
                {groupCount > 1 ? (
                    <div className="absolute bottom-4 left-1/2 z-[182] -translate-x-1/2 rounded-full border border-foreground/15 bg-black/55 px-3 py-1 font-mono text-chrome-sm text-foreground backdrop-blur">
                        {groupIndex + 1} / {groupCount}
                    </div>
                ) : null}

                {/* Centered media — onClick on the wrapper closes (click outside),
                    onClick on the media itself does NOT close (stopPropagation). */}
                <div
                    onClick={onClose}
                    className="grid h-full w-full place-items-center"
                >
                    {item.kind === "image" ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                            src={resolved}
                            alt={item.alt || t("previewAlt")}
                            onClick={(e) => e.stopPropagation()}
                            className="max-h-[90vh] max-w-[92vw] rounded-md shadow-[0_24px_64px_-20px_rgba(0,0,0,0.85)] object-contain"
                        />
                    ) : (
                        <video
                            src={resolved}
                            controls
                            autoPlay
                            loop
                            onClick={(e) => e.stopPropagation()}
                            className="max-h-[90vh] max-w-[92vw] rounded-md shadow-[0_24px_64px_-20px_rgba(0,0,0,0.85)] object-contain bg-black"
                        />
                    )}
                </div>
            </div>
        </>
    );

    return createPortal(modal, document.body);
}

/** Per-group registration helper — wraps children to register their items.
 *  Caller passes the FULL items array; provider stores it under groupId so
 *  prev/next can navigate without callers managing indices.
 *
 *  Note: when wrapping a dynamic list, items prop must be a stable reference
 *  (memoize with useMemo) or registration will re-fire on every parent render.
 */
interface LightboxGroupRegistrarProps {
    groupId: string;
    items: LightboxItem[];
    children: React.ReactNode;
}

export function LightboxGroupRegistrar({ groupId, items, children }: LightboxGroupRegistrarProps) {
    const { registerGroup, unregisterGroup } = useLightbox();
    useEffect(() => {
        registerGroup(groupId, items);
        return () => unregisterGroup(groupId);
    }, [groupId, items, registerGroup, unregisterGroup]);
    return <>{children}</>;
}
