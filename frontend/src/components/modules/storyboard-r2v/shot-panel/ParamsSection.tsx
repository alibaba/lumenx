"use client";
/**
 * ParamsSection — the upper half of the attached ShotPanel. Contains:
 *   - model picker (pills, wraps to next line if many)
 *   - basic params (duration / count / resolution / ratio)
 *   - advanced params (collapsible: negative / seed / cfg / mode /
 *     movement / sound / vidu audio / prompt extend / shot type)
 *   - Generate ×N button (bottom-right)
 *
 * Param visibility is fully driven by the active model's
 * `modelParams: ModelParamSupport` from the catalog. Switching models
 * resets per-param defaults (except NegativePrompt, which is
 * preserved across model swaps because users hate losing typed
 * prompts).
 *
 * This component is parameter-list rendering only. Actual generation
 * is delegated via onGenerate(payload) so the host (StoryboardR2V)
 * can manage tasks, queue, and shot-state updates.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Dices, X, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";
import type { I2VModelConfig, DurationConfig, ModelParamSupport } from "@/lib/modelCatalog";
import { usePanelSectionState } from "./usePanelSectionState";
import SectionShell from "./SectionShell";
// PR-3c · Loader2/Sparkles/WorkflowActionButton removed with the Generate
// CTA — generation lives in ShotCard's inline row now.

/** Snapshot of every configurable param this panel can collect.
 *  Sub-fields are kept Optional so a model that doesn't expose a
 *  given param simply leaves the field as undefined / default. */
export interface ParamsState {
    model: string;
    duration: number;
    count: number;
    resolution?: string;
    ratio?: string;
    // Advanced
    negativePrompt?: string;
    seed?: number;
    promptExtend?: boolean;
    cfgScale?: number;
    mode?: string;
    movementAmplitude?: string;
    sound?: boolean;
    viduAudio?: boolean;
    shotType?: string;
    /** Embed provider watermark in output. undefined = use provider default
     *  (typically off); explicit false/true is user choice. */
    watermark?: boolean;
}


const MODEL_MENU_GAP = 4;
const MODEL_MENU_MARGIN = 8;
const MODEL_MENU_MIN_WIDTH = 192;
const MODEL_MENU_MAX_HEIGHT = 240;

interface ModelMenuPlacement {
    top: number;
    left: number;
    width: number;
    maxHeight: number;
}

function clamp(value: number, min: number, max: number) {
    return Math.min(Math.max(value, min), max);
}

function getModelMenuPlacement(rect: DOMRect, viewportWidth: number, viewportHeight: number): ModelMenuPlacement {
    const availableWidth = Math.max(1, viewportWidth - MODEL_MENU_MARGIN * 2);
    const width = Math.min(Math.max(rect.width, MODEL_MENU_MIN_WIDTH), availableWidth);
    const maxHeight = Math.max(1, Math.min(MODEL_MENU_MAX_HEIGHT, viewportHeight - MODEL_MENU_MARGIN * 2));
    const bottomLimit = Math.max(MODEL_MENU_MARGIN, viewportHeight - MODEL_MENU_MARGIN - maxHeight);
    const belowTop = rect.bottom + MODEL_MENU_GAP;
    const belowFits = belowTop + maxHeight <= viewportHeight - MODEL_MENU_MARGIN;
    const top = clamp(belowFits ? belowTop : rect.top - MODEL_MENU_GAP - maxHeight, MODEL_MENU_MARGIN, bottomLimit);
    const rightLimit = Math.max(MODEL_MENU_MARGIN, viewportWidth - MODEL_MENU_MARGIN - width);
    const left = clamp(rect.left, MODEL_MENU_MARGIN, rightLimit);
    return { top, left, width, maxHeight };
}

interface ParamsSectionProps {
    shotId: string;
    /** Pickable models for this section (already filtered for the
     *  active tab — I2V models for t2i_i2v, R2V models for direct_r2v). */
    modelList: I2VModelConfig[];
    /** Section title shown in the SectionShell header. */
    title: string;
    params: ParamsState;
    onChange: (next: ParamsState) => void;
    /** Active in-flight count for this shot — shown as a small badge
     *  on the SectionShell title bar so users see ongoing tasks even
     *  when the params section is collapsed. */
    inFlightCount?: number;
    /** Inline error to show under params section (e.g.
     *  "happyhorse-1.0-r2v needs reference images"). Host owns the
     *  validation; this component just renders the message.
     *  PR-3c · Generate-related props (onGenerate / generateDisabled /
     *  generateDisabledReason) removed: generation triggered from
     *  ShotCard's inline row. */
    errorMessage?: string | null;
}

// COUNT_OPTIONS removed in PR-3c — count selector relocated to ShotCard's
// inline generation row. ShotCard owns the canonical [1,2,4,6] list now.

export default function ParamsSection({
    shotId,
    modelList,
    title,
    params,
    onChange,
    inFlightCount = 0,
    errorMessage,
}: ParamsSectionProps) {
    const t = useTranslations("storyboardR2V");
    const [open, setOpen] = usePanelSectionState(shotId, "params", true);
    const [advOpen, setAdvOpen] = usePanelSectionState(shotId, "params-advanced", false);
    const [modelOpen, setModelOpen] = useState(false);
    const [menuPos, setMenuPos] = useState<ModelMenuPlacement | null>(null);
    const trigRef = useRef<HTMLButtonElement>(null);
    const menuRef = useRef<HTMLDivElement>(null);
    // Position the dropdown via a portal (escapes the shot card's stacking
    // context so it isn't covered by the next shot's frame) + close on
    // outside scroll/resize so it never floats away from its trigger.
    useEffect(() => {
        if (!modelOpen) { setMenuPos(null); return; }
        const trig = trigRef.current;
        if (trig) {
            setMenuPos(getModelMenuPlacement(trig.getBoundingClientRect(), window.innerWidth, window.innerHeight));
        }
        const close = () => setModelOpen(false);
        const handleScroll = (event: Event) => {
            const target = event.target;
            if (target instanceof Node && menuRef.current?.contains(target)) return;
            close();
        };
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") close();
        };
        window.addEventListener("scroll", handleScroll, true);
        window.addEventListener("resize", close);
        window.addEventListener("keydown", handleKeyDown);
        return () => {
            window.removeEventListener("scroll", handleScroll, true);
            window.removeEventListener("resize", close);
            window.removeEventListener("keydown", handleKeyDown);
        };
    }, [modelOpen]);

    const activeModel: I2VModelConfig | undefined = useMemo(
        () => modelList.find((m) => m.id === params.model) ?? modelList[0],
        [modelList, params.model],
    );
    const modelParams: ModelParamSupport = activeModel?.params ?? {};
    const durationCfg: DurationConfig = activeModel?.duration ?? { type: "fixed", value: 5 };

    const set = useCallback(<K extends keyof ParamsState>(key: K, value: ParamsState[K]) => {
        onChange({ ...params, [key]: value });
    }, [params, onChange]);

    const handleModelChange = useCallback((nextModelId: string) => {
        const next = modelList.find((m) => m.id === nextModelId);
        if (!next) return;
        // Reset per-model defaults but PRESERVE the user's negative
        // prompt across model swaps — losing typed text on a model
        // switch is a frequent papercut.
        const dc = next.duration;
        const safeDuration = (() => {
            if (dc.type === "fixed") return dc.value;
            if (dc.type === "slider") {
                if (params.duration >= dc.min && params.duration <= dc.max) return params.duration;
                return dc.default;
            }
            if (dc.options.includes(params.duration)) return params.duration;
            return dc.default;
        })();
        const np = next.params ?? {};
        onChange({
            ...params,
            model: nextModelId,
            duration: safeDuration,
            resolution: np.resolution?.default ?? params.resolution,
            ratio: np.ratio?.default ?? params.ratio,
            promptExtend: typeof np.promptExtend === "boolean" ? np.promptExtend : params.promptExtend,
            cfgScale: np.cfgScale?.default ?? params.cfgScale,
            mode: np.mode?.default ?? params.mode,
            movementAmplitude: np.movementAmplitude?.default ?? params.movementAmplitude,
            sound: typeof np.sound === "boolean" ? np.sound : params.sound,
            viduAudio: typeof np.viduAudio === "boolean" ? np.viduAudio : params.viduAudio,
            // Watermark: new model exposes the capability → reset to off (false);
            // new model doesn't expose → drop (undefined). Preserving across swap
            // would silently send a watermark flag the new model rejects.
            watermark: np.watermark ? (typeof params.watermark === "boolean" ? params.watermark : false) : undefined,
            // negativePrompt intentionally preserved
        });
    }, [modelList, params, onChange]);

    const hasAdvanced =
        !!modelParams.negativePrompt ||
        !!modelParams.seed ||
        !!modelParams.promptExtend ||
        !!modelParams.cfgScale ||
        !!modelParams.mode ||
        !!modelParams.movementAmplitude ||
        !!modelParams.sound ||
        !!modelParams.viduAudio ||
        !!modelParams.shotType ||
        !!modelParams.watermark;

    return (
        <SectionShell
            title={title}
            open={open}
            onToggle={() => setOpen(!open)}
            subtitle={activeModel ? `${activeModel.name}` : undefined}
            trailing={inFlightCount > 0 ? (
                <span className="rounded-full border border-status-processing-border bg-status-processing-bg px-1.5 py-0.5 text-[0.5625rem] font-semibold leading-none text-status-processing-fg">
                    {`${inFlightCount} ${t("inFlightShort")}`}
                </span>
            ) : undefined}
        >
            <div className="space-y-3">
                {/* Model picker — dropdown (scales past a pill wall). */}
                <ParamRow label="Model">
                    <div className="relative">
                        <button
                            ref={trigRef}
                            type="button"
                            onClick={() => setModelOpen(v => !v)}
                            aria-expanded={modelOpen}
                            aria-haspopup="listbox"
                            title={activeModel?.description}
                            className="inline-flex min-h-[28px] items-center gap-2 rounded-[14px] border border-glass-border bg-surface-inset px-3 py-1.5 font-mono text-[0.6875rem] font-medium text-foreground transition-colors duration-fast ease-out-quart hover:border-foreground/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            <span className="h-1.5 w-1.5 rounded-full bg-primary shadow-[var(--glow-primary)]" />
                            <span className="truncate">{activeModel?.name ?? params.model}</span>
                            <svg className={`h-3.5 w-3.5 shrink-0 text-text-muted transition-transform duration-fast ${modelOpen ? "rotate-180" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9}>
                                <path d="m6 9 6 6 6-6" />
                            </svg>
                        </button>
                        {modelOpen && menuPos && createPortal(
                            <>
                                <div className="fixed inset-0 z-[60]" onClick={() => setModelOpen(false)} aria-hidden="true" />
                                <div
                                    ref={menuRef}
                                    role="listbox"
                                    aria-label="Model"
                                    className="fixed z-[70] overflow-y-auto rounded-[14px] border border-border-subtle bg-elevated p-1 shadow-[var(--shadow-lift)]"
                                    style={{
                                        top: menuPos.top,
                                        left: menuPos.left,
                                        width: menuPos.width,
                                        maxHeight: menuPos.maxHeight,
                                        maxWidth: `calc(100vw - ${MODEL_MENU_MARGIN * 2}px)`,
                                    }}
                                >
                                    {modelList.map((m) => {
                                        const active = params.model === m.id;
                                        return (
                                            <button
                                                key={m.id}
                                                type="button"
                                                role="option"
                                                onClick={() => { handleModelChange(m.id); setModelOpen(false); }}
                                                title={m.description}
                                                aria-selected={active}
                                                className={`flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left font-mono text-chrome-sm transition-colors duration-fast ease-out-quart hover:bg-hover-bg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55 ${
                                                    active ? "bg-primary/10 text-primary" : "text-text-secondary hover:text-foreground"
                                                }`}
                                            >
                                                <span className={`h-1 w-1 shrink-0 rounded-full ${active ? "bg-primary" : "bg-transparent"}`} />
                                                <span className="truncate">{m.name}</span>
                                            </button>
                                        );
                                    })}
                                </div>
                            </>,
                            document.body
                        )}
                    </div>
                </ParamRow>

                {/* Duration */}
                <ParamRow label="Duration">
                    <DurationControl
                        cfg={durationCfg}
                        value={params.duration}
                        onChange={(v) => set("duration", v)}
                    />
                </ParamRow>

                {/* Count row removed in PR-3c — moved into ShotCard's
                    inline generation row (count selector + 生成 ×N).
                    params.count state is still owned here (via ParamsState)
                    and synced from ShotCard via onSetGenerateCount →
                    handleShotParamsChange. ParamsSection no longer renders
                    count UI to avoid two competing surfaces for the same
                    state. */}

                {/* Resolution */}
                {modelParams.resolution ? (
                    <ParamRow label="Resolution">
                        <PillCluster
                            options={modelParams.resolution.options}
                            value={params.resolution ?? modelParams.resolution.default}
                            onChange={(v) => set("resolution", v)}
                        />
                    </ParamRow>
                ) : null}

                {/* Ratio */}
                {modelParams.ratio ? (
                    <ParamRow label="Ratio">
                        <PillCluster
                            options={modelParams.ratio.options}
                            value={params.ratio ?? modelParams.ratio.default}
                            onChange={(v) => set("ratio", v)}
                        />
                    </ParamRow>
                ) : null}

                {/* Advanced fold — slight indent (pl-4) to read as a sub-group
                    of Params, no border/box (keeps it clean). Candidates below
                    is a sibling section. */}
                {hasAdvanced ? (
                    <div className="pt-1 pl-4">
                        <button
                            type="button"
                            onClick={() => setAdvOpen(!advOpen)}
                            aria-expanded={advOpen}
                            className="group inline-flex items-center gap-1.5 rounded-md py-1 font-mono text-[0.6875rem] font-medium text-primary transition-colors duration-fast ease-out-quart hover:text-primary-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                        >
                            <ChevronRight
                                size={12}
                                strokeWidth={2}
                                className={`transition-transform duration-fast ${advOpen ? "rotate-90" : ""}`}
                                aria-hidden="true"
                            />
                            <span>{t("advancedParams")}</span>
                            <span className="ml-0.5 rounded-md bg-primary/10 px-1.5 py-0.5 text-[0.5625rem] text-primary">
                                {countAdvancedParams(modelParams)}
                            </span>
                        </button>
                        {advOpen ? (
                            <div className="space-y-3 mt-2">
                                {modelParams.negativePrompt ? (
                                    <ParamRow label="Negative">
                                        <input
                                            type="text"
                                            value={params.negativePrompt ?? ""}
                                            onChange={(e) => set("negativePrompt", e.target.value)}
                                            placeholder="things to avoid…"
                                            className="w-full rounded-lg border border-glass-border bg-surface-inset px-2.5 py-1.5 font-sans text-body-sm text-foreground placeholder:text-text-muted outline-none transition-colors duration-fast ease-out-quart focus:border-primary/55 focus-visible:ring-2 focus-visible:ring-primary/45"
                                        />
                                    </ParamRow>
                                ) : null}
                                {modelParams.seed ? (
                                    <ParamRow label="Seed">
                                        <div className="flex items-center gap-1.5">
                                            <input
                                                type="number"
                                                min={0}
                                                max={2_147_483_647}
                                                value={params.seed ?? ""}
                                                onChange={(e) => {
                                                    // Browser may emit value="" when user
                                                    // clears OR when native spinner click
                                                    // on empty input fails on Safari.
                                                    // parseInt of non-numeric ("abc") is
                                                    // NaN — treat both as "no explicit
                                                    // seed" (provider will randomize).
                                                    const v = e.target.value;
                                                    if (v === "") {
                                                        set("seed", undefined);
                                                        return;
                                                    }
                                                    const parsed = parseInt(v, 10);
                                                    set("seed", Number.isNaN(parsed) ? undefined : parsed);
                                                }}
                                                placeholder="random"
                                                aria-label="Random seed (leave blank for provider default)"
                                                className="w-32 rounded-lg border border-glass-border bg-surface-inset px-2 py-1.5 font-mono text-body-sm text-foreground placeholder:text-text-muted outline-none transition-colors duration-fast ease-out-quart focus:border-primary/55 focus-visible:ring-2 focus-visible:ring-primary/45"
                                            />
                                            {/* Dice = randomize. Lucide icon for
                                                visual cohesion with the rest of
                                                the panel; the emoji 🎲 rendered
                                                inconsistently across platforms. */}
                                            <button
                                                type="button"
                                                onClick={() => set("seed", Math.floor(Math.random() * 1_000_000_000))}
                                                aria-label="Generate random seed"
                                                className="grid h-8 w-8 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                                title="New random seed"
                                            >
                                                <Dices size={15} aria-hidden="true" />
                                            </button>
                                            {/* Clear → back to "random" (provider
                                                picks). Native spinners on an empty
                                                number input behave inconsistently;
                                                this is the explicit reset. */}
                                            {params.seed !== undefined ? (
                                                <button
                                                    type="button"
                                                    onClick={() => set("seed", undefined)}
                                                    aria-label="Clear seed"
                                                    title="Clear (random)"
                                                    className="grid h-8 w-8 place-items-center rounded text-text-muted transition-colors duration-fast ease-out-quart hover:bg-hover-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
                                                >
                                                    <X size={13} aria-hidden="true" />
                                                </button>
                                            ) : null}
                                        </div>
                                    </ParamRow>
                                ) : null}
                                {modelParams.cfgScale ? (
                                    <ParamRow label="CFG">
                                        <SliderControl
                                            min={modelParams.cfgScale.min}
                                            max={modelParams.cfgScale.max}
                                            step={modelParams.cfgScale.step}
                                            value={params.cfgScale ?? modelParams.cfgScale.default}
                                            onChange={(v) => set("cfgScale", v)}
                                        />
                                    </ParamRow>
                                ) : null}
                                {modelParams.mode ? (
                                    <ParamRow label="Mode">
                                        <PillCluster
                                            options={modelParams.mode.options}
                                            value={params.mode ?? modelParams.mode.default}
                                            onChange={(v) => set("mode", v)}
                                        />
                                    </ParamRow>
                                ) : null}
                                {modelParams.movementAmplitude ? (
                                    <ParamRow label="Motion">
                                        <PillCluster
                                            options={modelParams.movementAmplitude.options}
                                            value={params.movementAmplitude ?? modelParams.movementAmplitude.default}
                                            onChange={(v) => set("movementAmplitude", v)}
                                        />
                                    </ParamRow>
                                ) : null}
                                {modelParams.sound ? (
                                    <ParamRow label="Sound">
                                        <ToggleControl
                                            value={!!params.sound}
                                            onChange={(v) => set("sound", v)}
                                        />
                                    </ParamRow>
                                ) : null}
                                {modelParams.viduAudio ? (
                                    <ParamRow label="Vidu audio">
                                        <ToggleControl
                                            value={!!params.viduAudio}
                                            onChange={(v) => set("viduAudio", v)}
                                        />
                                    </ParamRow>
                                ) : null}
                                {modelParams.promptExtend ? (
                                    <ParamRow label="Prompt extend">
                                        <ToggleControl
                                            value={!!params.promptExtend}
                                            onChange={(v) => set("promptExtend", v)}
                                        />
                                    </ParamRow>
                                ) : null}
                                {modelParams.watermark ? (
                                    <ParamRow label="Watermark">
                                        <ToggleControl
                                            value={!!params.watermark}
                                            onChange={(v) => set("watermark", v)}
                                        />
                                    </ParamRow>
                                ) : null}
                                {modelParams.shotType ? (
                                    <ParamRow label="Shot type">
                                        <PillCluster
                                            options={typeof modelParams.shotType === "boolean"
                                                ? ["single", "multi"]
                                                : modelParams.shotType.options}
                                            value={params.shotType ?? (typeof modelParams.shotType === "object" ? modelParams.shotType.default : "single")}
                                            onChange={(v) => set("shotType", v)}
                                        />
                                    </ParamRow>
                                ) : null}
                            </div>
                        ) : null}
                    </div>
                ) : null}

                {/* Inline validation error (e.g. R2V model with no
                    references attached). Pops above the Generate CTA
                    so the user sees why their click would fail BEFORE
                    they click, and stays visible until they fix the
                    issue or successfully generate. */}
                {errorMessage ? (
                    <div
                        role="alert"
                        className="rounded-lg border border-status-failed-border bg-status-failed-bg px-3 py-2 font-sans text-body-sm text-status-failed-fg"
                    >
                        {errorMessage}
                    </div>
                ) : null}

                {/* Generate CTA removed in PR-3c — generation is now
                    triggered from ShotCard's inline generation row
                    (Action Bar 下方全宽行 with count selector + 主按钮).
                    ParamsSection focuses purely on parameter config now;
                    no action button surface here. errorMessage above is
                    still useful when host validation fails (e.g., R2V
                    model with no references). */}
            </div>
        </SectionShell>
    );
}

// ---------- Sub-components ----------

function ParamRow({ label, children }: { label: string; children: React.ReactNode }) {
    // Mock-aligned: label is a mono uppercase label, control flexes.
    return (
        <div className="flex flex-col items-start gap-1.5 sm:flex-row sm:gap-4">
            <span className="font-mono text-[0.6875rem] font-medium uppercase tracking-[0.1em] text-text-muted w-20 shrink-0 pt-1.5">
                {label}
            </span>
            <div className="min-w-0 w-full flex-1 sm:w-auto">{children}</div>
        </div>
    );
}

function PillCluster({
    options,
    value,
    onChange,
}: {
    options: ReadonlyArray<string | number>;
    value: string | number;
    onChange: (v: string) => void;
}) {
    return (
        <div className="flex flex-wrap gap-2">
            {options.map((opt) => {
                const active = String(value) === String(opt);
                return (
                    <button
                        key={String(opt)}
                        type="button"
                        onClick={() => onChange(String(opt))}
                        aria-pressed={active}
                        className={`min-h-[28px] rounded-full border px-2.5 py-1 font-mono text-[0.59375rem] font-medium transition-colors duration-fast ease-out-quart focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55 ${
                            active
                                ? "border-primary/45 bg-primary/14 text-primary"
                                : "border-glass-border bg-surface-inset text-text-secondary hover:border-foreground/20 hover:text-foreground"
                        }`}
                    >
                        {opt}
                    </button>
                );
            })}
        </div>
    );
}

function DurationControl({
    cfg,
    value,
    onChange,
}: {
    cfg: DurationConfig;
    value: number;
    onChange: (v: number) => void;
}) {
    if (cfg.type === "fixed") {
        return (
            <span className="font-mono text-body-sm tabular-nums text-text-secondary">
                {cfg.value}s <span className="text-text-muted">(fixed)</span>
            </span>
        );
    }
    if (cfg.type === "buttons") {
        return (
            <PillCluster
                options={cfg.options.map((n) => String(n))}
                value={String(value)}
                onChange={(v) => onChange(parseInt(v, 10))}
            />
        );
    }
    // slider — pair with a small editable number input so users can type
    // a precise duration (e.g. 7 with step=1) instead of dragging. Both
    // controls stay in sync; the input clamps to cfg.min/cfg.max on blur
    // so typing 999 doesn't silently send an over-range value.
    const clamp = (n: number) => {
        if (Number.isNaN(n)) return value;
        return Math.min(cfg.max, Math.max(cfg.min, Math.round(n / cfg.step) * cfg.step));
    };
    return (
        <div className="flex items-center gap-2">
            <input
                type="range"
                min={cfg.min}
                max={cfg.max}
                step={cfg.step}
                value={value}
                onChange={(e) => onChange(parseInt(e.target.value, 10))}
                aria-label="Duration in seconds (drag to adjust)"
                className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-elevated accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
            />
            <div className="flex shrink-0 items-center gap-0.5">
                <input
                    type="number"
                    min={cfg.min}
                    max={cfg.max}
                    step={cfg.step}
                    value={value}
                    onChange={(e) => {
                        // Allow free typing; only apply when result is a number in range.
                        // Empty string is treated as "no change yet" — wait for blur.
                        const raw = e.target.value;
                        if (raw === "") return;
                        const parsed = parseInt(raw, 10);
                        if (!Number.isNaN(parsed)) onChange(clamp(parsed));
                    }}
                    onBlur={(e) => {
                        // Final clamp on blur covers the "user typed 999 and clicked away"
                        // case where onChange's mid-typing clamp would have looked jumpy.
                        const parsed = parseInt(e.target.value, 10);
                        const clamped = clamp(parsed);
                        if (clamped !== value) onChange(clamped);
                    }}
                    aria-label={`Duration in seconds (type a value between ${cfg.min} and ${cfg.max})`}
                    className="w-12 rounded border border-glass-border bg-surface-inset px-1.5 py-0.5 text-right font-mono text-body-sm tabular-nums text-foreground outline-none transition-colors duration-fast ease-out-quart focus:border-primary/55 focus-visible:ring-1 focus-visible:ring-primary/45 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                />
                <span className="font-mono text-body-sm text-text-muted">s</span>
            </div>
        </div>
    );
}

function SliderControl({
    min,
    max,
    step,
    value,
    onChange,
}: {
    min: number;
    max: number;
    step: number;
    value: number;
    onChange: (v: number) => void;
}) {
    return (
        <div className="flex items-center gap-2">
            <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={value}
                onChange={(e) => onChange(parseFloat(e.target.value))}
                className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-elevated accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
            />
            <span className="w-10 shrink-0 text-right font-mono text-body-sm tabular-nums text-foreground">
                {value}
            </span>
        </div>
    );
}

function ToggleControl({
    value,
    onChange,
}: {
    value: boolean;
    onChange: (v: boolean) => void;
}) {
    // 28x28 hit area via -m-1 p-1, 36x20 visual track preserved.
    return (
        <button
            type="button"
            onClick={() => onChange(!value)}
            aria-pressed={value}
            className="-m-1 inline-flex h-7 items-center rounded-full p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/55"
        >
            <span
                aria-hidden="true"
                className={`relative h-5 w-9 rounded-full transition-colors duration-fast ease-out-quart ${
                    value ? "bg-primary" : "bg-elevated"
                }`}
            >
                <span
                    className={`absolute top-[2px] h-4 w-4 rounded-full bg-white shadow transition-all duration-base ease-out-quart ${
                        value ? "left-[18px]" : "left-[2px]"
                    }`}
                />
            </span>
        </button>
    );
}

function countAdvancedParams(mp: ModelParamSupport): number {
    let n = 0;
    if (mp.negativePrompt) n++;
    if (mp.seed) n++;
    if (mp.cfgScale) n++;
    if (mp.mode) n++;
    if (mp.movementAmplitude) n++;
    if (mp.sound) n++;
    if (mp.viduAudio) n++;
    if (mp.promptExtend) n++;
    if (mp.shotType) n++;
    if (mp.watermark) n++;
    return n;
}
