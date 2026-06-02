"use client";
//
// OnboardingTour — v1.1 track V — anchored, spotlight-driven guided tour.
//
// Replaces the v0.x static 4-card welcome chip with a real walkthrough:
//   - each step ANCHORS to a real DOM element via a CSS selector,
//   - a full-screen scrim dims everything *except* the anchored region
//     (achieved with a 0-padding box that carries a huge `box-shadow`
//     — pointer-events: none so the underlying button is still clickable),
//   - the tooltip card is positioned adjacent to the anchor (auto top /
//     bottom based on viewport room),
//   - the Next button is DISABLED until the per-step criterion is met
//     (e.g. "click Add" → node count rises). Skip is always available,
//   - if a step's anchor element doesn't exist yet (e.g. Composer isn't
//     open for step "Generate"), we degrade gracefully to a centered
//     card with the same content — the tour never gets stuck on a
//     missing DOM target.
//
// The store-side actions (start / advance / dismiss / setStep) and the
// `atelier-onboarding-completed-v1.1` localStorage key live in
// atelierStore.ts under the `_V` action prefix. The Shell wires:
//   - initial mount → maybeStartOnboarding_V() (no-op if already seen),
//   - Help dialog → "Replay tour" button calls startOnboarding_V(true),
//   - ? key → dismissOnboarding_V() (preserves prior behavior),
//   - the criterion fns read the live project (nodes / sequence) so
//     advance auto-unlocks the moment the user does the requested action.
//
// File ownership note: V owns this file and the onboarding slice in
// atelierStore.ts (action prefix `_V`). The Shell hosts only the mount
// site + the "Replay tour" button inside the existing Help dialog.

import * as React from "react";
import { X } from "lucide-react";

/** A single tour step's spec. Authored in AtelierShellV3.tsx and passed
 *  in via props — the component is purely presentational + positional. */
export interface OnboardingStep {
    /** Short progress label, e.g. "Step 2 of 6". */
    tag: string;
    /** Card title. */
    title: string;
    /** Card body — may contain inline <kbd> etc. */
    body: React.ReactNode;
    /** CSS selector to anchor the spotlight to. `null` = centered (no
     *  spotlight cutout, just the dim scrim with the card in the middle).
     *  If the selector returns no element at probe time we fall back to
     *  the centered variant for this step. */
    selector: string | null;
    /** Has the user performed the step's prompted action? When true the
     *  Next button is enabled. When false the user can only Skip (or
     *  perform the action — the criterion is re-evaluated on every
     *  re-render via the parent's store subscription, so the moment the
     *  store flips, Next becomes interactive). */
    criterionMet: boolean;
    /** Optional one-liner hint shown under the body, e.g. "Click Add to
     *  continue". Pure visual nudge so the user sees WHY Next is greyed. */
    hint?: string;
    /** Tooltip placement preference relative to the anchor. Defaults to
     *  "auto" which picks bottom→top→right→left based on viewport room. */
    placement?: "auto" | "top" | "bottom" | "left" | "right";
}

interface Props {
    /** Active step index. The parent (Shell) drives this via the store so
     *  Replay-from-Help can reset to 0 without re-rendering all steps. */
    stepIndex: number;
    /** Authored step list. Order is significant — index 0 plays first. */
    steps: OnboardingStep[];
    /** Skip or final-Done collapses the tour. Persists the seen flag. */
    onDismiss: () => void;
    /** Advance to next step. Last step calls onDismiss instead. */
    onAdvance: () => void;
    /** Allow direct jumps from the dot pager. Used for back-navigation. */
    onJumpTo: (index: number) => void;
}

/** Probe frequency for re-reading the anchor rect. Fast enough that
 *  pan/zoom/resize feel smooth, cheap enough that we don't pay rAF cost
 *  when the user is just reading the card. */
const PROBE_INTERVAL_MS = 200;

/** Spotlight padding around the anchor bounding rect, in CSS pixels. */
const SPOTLIGHT_PAD = 8;

/** Card sizing — kept tight so it fits next to most anchors without
 *  overlapping the spotlight. */
const CARD_W = 320;
const CARD_GAP = 14;
const VIEWPORT_PAD = 12;

export function OnboardingTour({
    stepIndex,
    steps,
    onDismiss,
    onAdvance,
    onJumpTo,
}: Props) {
    const step = steps[stepIndex];
    const isLast = stepIndex >= steps.length - 1;
    const [rect, setRect] = React.useState<DOMRect | null>(null);
    const [viewport, setViewport] = React.useState<{ w: number; h: number }>(() => ({
        w: typeof window === "undefined" ? 1280 : window.innerWidth,
        h: typeof window === "undefined" ? 800 : window.innerHeight,
    }));

    // Probe loop — re-reads getBoundingClientRect every PROBE_INTERVAL_MS
    // so we track scroll/pan/resize/anchor remount without wiring per-DOM
    // observers (which would be over-engineering for a 6-step tour). The
    // probe is cheap (one querySelector + one getBoundingClientRect) and
    // we bail out immediately when the step has no anchor.
    React.useEffect(() => {
        if (!step) return;
        if (!step.selector) {
            setRect(null);
            return;
        }
        const selector = step.selector;
        const tick = () => {
            try {
                const el = document.querySelector(selector) as HTMLElement | null;
                if (!el) {
                    setRect((cur) => (cur === null ? cur : null));
                    return;
                }
                const r = el.getBoundingClientRect();
                if (r.width === 0 && r.height === 0) {
                    setRect((cur) => (cur === null ? cur : null));
                    return;
                }
                setRect((cur) => {
                    if (
                        cur &&
                        cur.left === r.left &&
                        cur.top === r.top &&
                        cur.width === r.width &&
                        cur.height === r.height
                    ) {
                        return cur;
                    }
                    return r;
                });
            } catch {
                setRect(null);
            }
        };
        tick();
        const handle = window.setInterval(tick, PROBE_INTERVAL_MS);
        const onResize = () => {
            setViewport({ w: window.innerWidth, h: window.innerHeight });
            tick();
        };
        window.addEventListener("resize", onResize);
        window.addEventListener("scroll", tick, true);
        return () => {
            window.clearInterval(handle);
            window.removeEventListener("resize", onResize);
            window.removeEventListener("scroll", tick, true);
        };
    }, [step]);

    // Esc dismisses the tour (matches the Help overlay convention).
    React.useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                e.stopPropagation();
                onDismiss();
            }
        };
        window.addEventListener("keydown", onKey, true);
        return () => window.removeEventListener("keydown", onKey, true);
    }, [onDismiss]);

    if (!step) return null;

    // Spotlight rect (anchor expanded by SPOTLIGHT_PAD). When null we
    // render the centered fallback card with no cutout.
    const spotlight =
        rect !== null
            ? {
                  left: rect.left - SPOTLIGHT_PAD,
                  top: rect.top - SPOTLIGHT_PAD,
                  width: rect.width + SPOTLIGHT_PAD * 2,
                  height: rect.height + SPOTLIGHT_PAD * 2,
              }
            : null;

    // Tooltip placement — when anchored, prefer below; if below would
    // overflow, try above; then right; then left; otherwise centered.
    let cardStyle: React.CSSProperties;
    if (spotlight) {
        const placement = step.placement ?? "auto";
        const cardH = 220; // approximate; the card auto-fits so this is a
        //                     lower-bound for the placement heuristic.
        const room = {
            below: viewport.h - (spotlight.top + spotlight.height) - VIEWPORT_PAD,
            above: spotlight.top - VIEWPORT_PAD,
            right: viewport.w - (spotlight.left + spotlight.width) - VIEWPORT_PAD,
            left: spotlight.left - VIEWPORT_PAD,
        };
        const wantBelow = placement === "bottom" || (placement === "auto" && room.below >= cardH);
        const wantAbove = placement === "top" || (placement === "auto" && !wantBelow && room.above >= cardH);
        const wantRight = placement === "right" || (placement === "auto" && !wantBelow && !wantAbove && room.right >= CARD_W);
        const wantLeft = placement === "left" || (placement === "auto" && !wantBelow && !wantAbove && !wantRight && room.left >= CARD_W);

        if (wantBelow) {
            const left = clamp(
                spotlight.left + spotlight.width / 2 - CARD_W / 2,
                VIEWPORT_PAD,
                viewport.w - CARD_W - VIEWPORT_PAD,
            );
            cardStyle = { left, top: spotlight.top + spotlight.height + CARD_GAP, width: CARD_W };
        } else if (wantAbove) {
            const left = clamp(
                spotlight.left + spotlight.width / 2 - CARD_W / 2,
                VIEWPORT_PAD,
                viewport.w - CARD_W - VIEWPORT_PAD,
            );
            cardStyle = { left, bottom: viewport.h - spotlight.top + CARD_GAP, width: CARD_W };
        } else if (wantRight) {
            cardStyle = {
                left: spotlight.left + spotlight.width + CARD_GAP,
                top: clamp(
                    spotlight.top + spotlight.height / 2 - cardH / 2,
                    VIEWPORT_PAD,
                    viewport.h - cardH - VIEWPORT_PAD,
                ),
                width: CARD_W,
            };
        } else if (wantLeft) {
            cardStyle = {
                right: viewport.w - spotlight.left + CARD_GAP,
                top: clamp(
                    spotlight.top + spotlight.height / 2 - cardH / 2,
                    VIEWPORT_PAD,
                    viewport.h - cardH - VIEWPORT_PAD,
                ),
                width: CARD_W,
            };
        } else {
            cardStyle = {
                left: viewport.w / 2 - CARD_W / 2,
                top: viewport.h / 2 - cardH / 2,
                width: CARD_W,
            };
        }
    } else {
        cardStyle = {
            left: viewport.w / 2 - CARD_W / 2,
            top: Math.max(VIEWPORT_PAD, viewport.h / 2 - 140),
            width: CARD_W,
        };
    }

    const nextEnabled = step.criterionMet;
    const nextLabel = isLast ? "Done" : "Next";

    return (
        <>
            {/* Spotlight cutout when anchored. The big box-shadow makes
                the scrim. pointer-events: none keeps the anchored element
                clickable — that's how the user satisfies the criterion
                (e.g. clicks Add) and unlocks Next. The thin brand ring
                signs the anchored region as "this is what we mean". */}
            {spotlight ? (
                <div
                    aria-hidden="true"
                    className="pointer-events-none fixed z-[55] motion-safe:transition-all motion-safe:duration-200"
                    style={{
                        left: spotlight.left,
                        top: spotlight.top,
                        width: spotlight.width,
                        height: spotlight.height,
                        borderRadius: 12,
                        boxShadow:
                            "0 0 0 9999px rgba(0,0,0,0.55), inset 0 0 0 1.5px rgba(99,151,255,0.85), 0 0 22px rgba(99,151,255,0.35)",
                    }}
                />
            ) : (
                // Centered fallback: full-screen scrim (no cutout) at a
                // lower opacity so the canvas still reads. pointer-events
                // pass-through so the user can still interact with the
                // canvas to satisfy criteria for non-anchored steps.
                <div
                    aria-hidden="true"
                    className="pointer-events-none fixed inset-0 z-[55] bg-black/45"
                />
            )}

            {/* The card itself — interactive, brand chrome, dot pager,
                Skip + Next. Animated in via the existing node-in motion. */}
            <div
                role="dialog"
                aria-live="polite"
                aria-label={`Onboarding step ${stepIndex + 1} of ${steps.length}`}
                className="fixed z-[56] overflow-hidden rounded-[12px] border border-white/8 bg-[#141416]/96 shadow-[0_28px_60px_-26px_rgba(0,0,0,0.85),0_8px_18px_-6px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl animate-atelier-node-in motion-reduce:animate-none"
                style={cardStyle}
            >
                <div
                    aria-hidden="true"
                    className="h-[2px] bg-gradient-to-r from-atelier-brand-400 via-atelier-brand-400/45 to-transparent"
                />
                <div className="px-4 pb-3 pt-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                        <span className="text-[11px] font-medium text-atelier-brand-400/85">
                            {step.tag}
                        </span>
                        <button
                            type="button"
                            onClick={onDismiss}
                            aria-label="Dismiss tour"
                            className="inline-flex h-5 w-5 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
                        >
                            <X size={11} aria-hidden="true" />
                        </button>
                    </div>
                    <div className="mb-1 font-display text-[14px] font-medium tracking-[-0.01em] text-foreground">
                        {step.title}
                    </div>
                    <div className="text-[12px] leading-[1.55] text-text-secondary/95">{step.body}</div>
                    {step.hint && !nextEnabled ? (
                        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-atelier-brand-400/85">
                            <span
                                aria-hidden="true"
                                className="inline-block h-1.5 w-1.5 rounded-full bg-atelier-brand-400 motion-safe:animate-atelier-pulse-soft"
                            />
                            <span>{step.hint}</span>
                        </div>
                    ) : null}
                    <div className="mt-3 flex items-center justify-between gap-2 border-t border-white/6 pt-2.5">
                        <div className="flex items-center gap-1" role="tablist" aria-label="Tour progress">
                            {steps.map((_, i) => (
                                <button
                                    type="button"
                                    key={i}
                                    role="tab"
                                    aria-selected={i === stepIndex}
                                    aria-label={`Go to step ${i + 1}`}
                                    onClick={() => onJumpTo(i)}
                                    className={`h-[3px] rounded-full transition-all ${
                                        i === stepIndex
                                            ? "w-5 bg-atelier-brand-400"
                                            : i < stepIndex
                                              ? "w-1.5 bg-atelier-brand-400/40 hover:bg-atelier-brand-400/70"
                                              : "w-1.5 bg-white/12 hover:bg-white/30"
                                    }`}
                                />
                            ))}
                        </div>
                        <div className="flex items-center gap-1">
                            <button
                                type="button"
                                onClick={onDismiss}
                                className="rounded-full px-2 py-1 text-[11px] text-text-muted/85 transition-colors hover:bg-hover-bg hover:text-foreground"
                            >
                                Skip
                            </button>
                            <button
                                type="button"
                                onClick={onAdvance}
                                disabled={!nextEnabled}
                                aria-disabled={!nextEnabled}
                                title={!nextEnabled && step.hint ? step.hint : undefined}
                                className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-all duration-200 ${
                                    nextEnabled
                                        ? "bg-atelier-brand-400 text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.16),0_4px_10px_-3px_rgba(59,107,255,0.5)] hover:scale-[1.04] hover:bg-atelier-brand-400/92 hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.2),0_6px_14px_-3px_rgba(59,107,255,0.6)] active:scale-[0.96]"
                                        : "cursor-not-allowed bg-white/8 text-text-muted/55"
                                }`}
                            >
                                {nextLabel}
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </>
    );
}

function clamp(n: number, lo: number, hi: number): number {
    if (hi < lo) return lo;
    if (n < lo) return lo;
    if (n > hi) return hi;
    return n;
}
