"use client";
/**
 * EpisodeMiniList — compact episode switcher rendered at the top of
 * PipelineSidebar when the current project belongs to a Series.
 *
 * Solves the friction L3 raised in the 火山剧创 comparison: switching
 * between episodes of the same series used to require Series detail
 * page → pick another episode → re-open Studio (3 clicks). Now it's
 * one click while staying on the same module (e.g. Storyboard).
 *
 * UX:
 *   - Header row "📺 本系列 · {N} 集"
 *   - Vertical scrollable list of episodes, sorted by episode_number
 *   - Each item: small chip "Ep N" + truncated title, active highlighted
 *   - Click → router push to /project/{id}#step (preserves activeStep)
 *   - Hidden entirely when project has no series_id (standalone projects)
 *
 * Backend hit is one-shot per session (cached by series id). Falls back
 * to silent hide on fetch error — non-blocking.
 */
import { useEffect, useState } from "react";
import { Tv, Plus } from "lucide-react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { debugLog } from "@/lib/debugLog";

interface EpisodeListItem {
    id: string;
    title: string;
    episode_number?: number;
}

interface EpisodeMiniListProps {
    seriesId: string;
    currentProjectId: string;
    /** Active pipeline step; preserved when switching episodes so the
     *  user lands on the same module (storyboard → storyboard) in the
     *  new episode. */
    activeStep: string;
}

function navigateToSeriesAddEpisode(seriesId: string): void {
    // Send the user to the SeriesDetailPage where they can add a
    // new episode. The page handles import / create flows.
    window.location.hash = `/series/${seriesId}`;
}

export default function EpisodeMiniList({
    seriesId,
    currentProjectId,
    activeStep,
}: EpisodeMiniListProps) {
    const t = useTranslations("episodeMiniList");
    const [episodes, setEpisodes] = useState<EpisodeListItem[] | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        api.getSeriesEpisodes(seriesId)
            .then((eps: any[]) => {
                if (cancelled) return;
                // Sort by episode_number asc; episodes without a
                // number fall to the end in stable insertion order.
                const sorted = [...eps].sort((a, b) => {
                    const an = typeof a.episode_number === "number" ? a.episode_number : 999;
                    const bn = typeof b.episode_number === "number" ? b.episode_number : 999;
                    return an - bn;
                });
                setEpisodes(sorted.map((e) => ({
                    id: e.id,
                    title: e.title || `Episode ${e.episode_number ?? "?"}`,
                    episode_number: e.episode_number,
                })));
            })
            .catch((err) => {
                debugLog.warn("Studio", "EpisodeMiniList fetch failed:", err);
                if (!cancelled) setEpisodes([]);
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => { cancelled = true; };
    }, [seriesId]);

    // Always render when this project is part of a series — even
    // if the series only has 1 episode today. The previous
    // "<=1 hide" gate made the affordance undiscoverable: users
    // never saw the widget so they never realized they could add
    // more episodes from inside Studio. Now they get the badge
    // plus a "+ Add" CTA that jumps to SeriesDetailPage's add-
    // episode flow.
    if (loading || !episodes || episodes.length === 0) return null;

    const handleSwitch = (epId: string) => {
        if (epId === currentProjectId) return;
        // The workspace router separates this step fragment from the ID
        // and hands it to ProjectClient when opening the destination.
        const stepFragment = activeStep ? `#${activeStep}` : "";
        window.location.hash = `/project/${epId}${stepFragment}`;
    };

    return (
        <div className="border-b border-glass-border px-4 py-3">
            <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 font-mono text-chrome-sm font-medium uppercase text-text-muted">
                    <Tv size={11} aria-hidden="true" />
                    {t("title", { count: episodes.length })}
                </div>
                {/* Add-episode CTA — always available so growing the
                    series doesn't require leaving Studio. */}
                <button
                    type="button"
                    onClick={() => navigateToSeriesAddEpisode(seriesId)}
                    aria-label={t("addEpisode")}
                    title={t("addEpisode")}
                    className="btn-tip -m-1 grid h-6 w-6 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                >
                    <Plus size={11} aria-hidden="true" />
                </button>
            </div>
            <div className="max-h-[180px] space-y-1 overflow-y-auto pr-1">
                {episodes.map((ep) => {
                    const isActive = ep.id === currentProjectId;
                    return (
                        <button
                            key={ep.id}
                            type="button"
                            onClick={() => handleSwitch(ep.id)}
                            title={ep.title}
                            className={clsx(
                                "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55",
                                isActive
                                    ? "bg-primary/15 text-primary"
                                    : "text-text-secondary hover:bg-hover-bg hover:text-foreground",
                            )}
                        >
                            <span className={clsx(
                                "grid h-5 w-7 shrink-0 place-items-center rounded font-mono text-chrome-sm font-medium tabular-nums",
                                isActive
                                    ? "bg-primary/25 text-primary"
                                    : "bg-black/30 text-text-muted group-hover:text-foreground",
                            )}>
                                {ep.episode_number ?? "—"}
                            </span>
                            <span className="truncate font-sans text-body-sm">
                                {ep.title}
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
