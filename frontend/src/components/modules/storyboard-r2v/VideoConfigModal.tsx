"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Settings2, Check, Wand2, Timer, SlidersHorizontal } from "lucide-react";
import { useTranslations } from "next-intl";
import {
    DEFAULT_I2V_MODEL_ID,
    DEFAULT_R2V_MODEL_ID,
    type DurationConfig,
    type ModelParamSupport,
    VIDEO_I2V_MODELS,
    VIDEO_R2V_MODELS,
} from "@/lib/modelCatalog";
import GroupedModelGrid from "@/components/common/GroupedModelGrid";

export interface VideoConfig {
    /** Active I2V model id (used by t2i_i2v shots). */
    model: string;
    /** Active R2V model id (used by direct_r2v shots). The catalog
     *  hides individual R2V models from the I2V dropdown by design,
     *  but R2V projects need a real R2V picker — see VIDEO_R2V_MODELS. */
    r2vModel: string;
    duration: number;
    resolution: string;
    ratio?: string;
    promptExtend: boolean;
    negativePrompt: string;
    // Kling
    mode?: string;
    cfgScale?: number;
    sound?: boolean;
    // Vidu
    viduAudio?: boolean;
    movementAmplitude?: string;
    // Watermark toggle (wan / kling / vidu / pixverse / happyhorse video).
    // Lives in videoConfig so the user's choice survives shot switches +
    // page reloads, same lifecycle as negativePrompt / promptExtend.
    watermark?: boolean;
}

/** Which model dropdown the modal should drive: I2V models for the
 *  t2i_i2v workflow, R2V models for direct_r2v. The host page picks
 *  this based on the project's workflow_mode (or whatever shot is
 *  currently active). */
export type VideoConfigVariant = "i2v" | "r2v";

interface VideoConfigModalProps {
    isOpen: boolean;
    onClose: () => void;
    config: VideoConfig;
    onConfigChange: (config: VideoConfig) => void;
    /** Which model list to drive in the dropdown. Defaults to "i2v"
     *  for back-compat. R2V projects pass "r2v" so the dropdown
     *  surfaces actual R2V models (one per family) instead of the I2V
     *  parents that the catalog otherwise exposes. */
    variant?: VideoConfigVariant;
}

export const DEFAULT_VIDEO_CONFIG: VideoConfig = {
    model: DEFAULT_I2V_MODEL_ID,
    r2vModel: DEFAULT_R2V_MODEL_ID,
    duration: 5,
    resolution: "720p",
    promptExtend: true,
    negativePrompt: "",
    mode: "std",
    cfgScale: 0.5,
    sound: false,
    viduAudio: true,
    movementAmplitude: "auto",
};

const springFast = { type: "spring" as const, stiffness: 400, damping: 28 };
const springMed = { type: "spring" as const, stiffness: 300, damping: 30 };
const springOverlay = { type: "spring" as const, stiffness: 200, damping: 25 };

// Staggered section entrance variants
const sectionVariants = {
    hidden: { opacity: 0, y: 12 },
    visible: (i: number) => ({
        opacity: 1,
        y: 0,
        transition: { type: "spring" as const, stiffness: 200, damping: 22, delay: i * 0.06 },
    }),
};

// Provider accent color map
function getProviderAccent(modelId: string): string {
    if (modelId.includes("wan") || modelId.includes("wanx")) return "from-blue-500 to-cyan-400";
    if (modelId.includes("kling")) return "from-amber-400 to-orange-500";
    if (modelId.includes("vidu")) return "from-emerald-400 to-teal-500";
    if (modelId.includes("pixverse")) return "from-violet-400 to-purple-500";
    if (modelId.includes("happyhorse")) return "from-rose-400 to-pink-500";
    return "from-primary to-primary/60";
}

export default function VideoConfigModal({ isOpen, onClose, config, onConfigChange, variant = "i2v" }: VideoConfigModalProps) {
    const [draft, setDraft] = useState<VideoConfig>(config);
    const t = useTranslations("storyboardR2V");
    const tm = useTranslations("motion");

    // Variant chooses which model list drives the dropdown. The "active
    // model id" referenced by the rest of the modal is `draft.model` for
    // i2v, `draft.r2vModel` for r2v — both VideoConfig fields live
    // side-by-side so the user's I2V choice survives a trip through the
    // R2V flow and vice versa.
    const isR2V = variant === "r2v";
    const modelList = isR2V ? VIDEO_R2V_MODELS : VIDEO_I2V_MODELS;
    const fallbackId = isR2V ? DEFAULT_R2V_MODEL_ID : DEFAULT_I2V_MODEL_ID;
    const activeModelId = isR2V ? draft.r2vModel : draft.model;
    const modelKey = isR2V ? ("r2vModel" as const) : ("model" as const);

    const currentModelConfig =
        modelList.find((m) => m.id === activeModelId) ??
        modelList.find((m) => m.id === fallbackId) ??
        modelList[0];
    const modelParams: ModelParamSupport = currentModelConfig?.params ?? {};

    const updateDraft = useCallback(
        (key: string, value: any) => {
            const newDraft = { ...draft, [key]: value };
            if (key === "model" || key === "r2vModel") {
                const list = key === "r2vModel" ? VIDEO_R2V_MODELS : VIDEO_I2V_MODELS;
                const newModelConfig = list.find((m) => m.id === value);
                if (newModelConfig?.duration) {
                    const dc = newModelConfig.duration;
                    if (dc.type === "fixed") {
                        newDraft.duration = dc.value;
                    } else if (dc.type === "slider") {
                        if (newDraft.duration < dc.min || newDraft.duration > dc.max) {
                            newDraft.duration = dc.default;
                        }
                    } else if (dc.type === "buttons") {
                        if (!dc.options.includes(newDraft.duration)) {
                            newDraft.duration = dc.default;
                        }
                    }
                }
                const np = newModelConfig?.params ?? {};
                newDraft.resolution = np.resolution?.default ?? "720p";
                newDraft.promptExtend = !!np.promptExtend;
                newDraft.negativePrompt = "";
                newDraft.mode = np.mode?.default ?? "std";
                newDraft.sound = false;
                newDraft.cfgScale = np.cfgScale?.default ?? 0.5;
                newDraft.viduAudio = true;
                newDraft.movementAmplitude = np.movementAmplitude?.default ?? "auto";
            }
            setDraft(newDraft);
        },
        [draft]
    );

    const handleApply = () => {
        onConfigChange(draft);
        onClose();
    };

    const handleOpen = () => {
        setDraft(config);
    };

    const hasAdvancedParams =
        modelParams.resolution ||
        modelParams.promptExtend ||
        modelParams.negativePrompt ||
        modelParams.cfgScale ||
        modelParams.movementAmplitude ||
        modelParams.mode;

    const activeAccent = getProviderAccent(activeModelId);

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={springOverlay}
                    className="fixed inset-0 bg-black/70 backdrop-blur-lg z-50 flex items-center justify-center p-4 md:p-8"
                    onClick={onClose}
                    onAnimationStart={handleOpen}
                >
                    <motion.div
                        initial={{ scale: 0.9, opacity: 0, y: 24 }}
                        animate={{ scale: 1, opacity: 1, y: 0 }}
                        exit={{ scale: 0.9, opacity: 0, y: 24 }}
                        transition={springMed}
                        className="relative bg-surface border border-glass-border shadow-[0_0_80px_rgba(0,0,0,0.6),inset_0_1px_0_rgba(255,255,255,0.04)] rounded-2xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Header — Cinematic glass bar with accent */}
                        <div className="relative flex items-center justify-between px-7 py-5 border-b border-glass-border shrink-0 bg-glass">
                            {/* Accent gradient line at top */}
                            <div className={`absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r ${activeAccent} opacity-40`} />
                            <div className="flex items-center gap-3.5">
                                <div className="relative w-9 h-9 rounded-xl bg-glass border border-glass-border flex items-center justify-center">
                                    <Settings2 size={16} className="text-foreground/80" strokeWidth={1.5} />
                                    <div className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-gradient-to-br ${activeAccent} border border-surface`} />
                                </div>
                                <div>
                                    <h2 className="text-[0.9375rem] font-semibold text-foreground tracking-tight">
                                        {t("videoSettings")}
                                    </h2>
                                    <p className="text-[0.6875rem] text-text-muted mt-0.5 tracking-wide font-medium">
                                        {currentModelConfig?.name}
                                    </p>
                                </div>
                            </div>
                            <motion.button
                                whileHover={{ scale: 1.1, rotate: 90 }}
                                whileTap={{ scale: 0.9 }}
                                onClick={onClose}
                                transition={springFast}
                                className="p-2 rounded-xl hover:bg-hover-bg text-text-muted hover:text-foreground transition-colors"
                            >
                                <X size={18} strokeWidth={1.5} />
                            </motion.button>
                        </div>

                        {/* Content — Staggered sections */}
                        <div className="flex-1 overflow-y-auto px-7 py-7 space-y-9">
                            {/* Model Selection */}
                            <motion.section
                                custom={0}
                                variants={sectionVariants}
                                initial="hidden"
                                animate="visible"
                                className="space-y-4"
                            >
                                <div className="flex items-center gap-2.5">
                                    <Wand2 size={13} className="text-text-muted" strokeWidth={1.5} />
                                    <h3 className="text-[0.625rem] font-bold text-text-muted uppercase tracking-[0.2em]">
                                        {t("modelSelection")}
                                    </h3>
                                </div>
                                <GroupedModelGrid
                                    models={modelList}
                                    selectedId={activeModelId}
                                    onSelect={(id) => updateDraft(modelKey, id)}
                                />
                            </motion.section>

                            {/* Duration */}
                            <motion.section
                                custom={1}
                                variants={sectionVariants}
                                initial="hidden"
                                animate="visible"
                                className="space-y-4"
                            >
                                <div className="flex items-center gap-2.5">
                                    <Timer size={13} className="text-text-muted" strokeWidth={1.5} />
                                    <h3 className="text-[0.625rem] font-bold text-text-muted uppercase tracking-[0.2em]">
                                        {t("durationLabel")}
                                    </h3>
                                </div>
                                {(() => {
                                    const dc: DurationConfig = currentModelConfig?.duration ?? {
                                        type: "buttons",
                                        options: [5, 10],
                                        default: 5,
                                    };
                                    if (dc.type === "fixed") {
                                        return (
                                            <div className="flex items-baseline gap-2 px-5 py-4 rounded-xl bg-glass border border-glass-border">
                                                <span className="text-3xl font-bold text-foreground tabular-nums tracking-tight">
                                                    {dc.value}
                                                </span>
                                                <span className="text-sm text-text-muted font-medium">sec</span>
                                            </div>
                                        );
                                    }
                                    if (dc.type === "slider") {
                                        const pct = ((draft.duration - dc.min) / (dc.max - dc.min)) * 100;
                                        return (
                                            <div className="space-y-3 px-1">
                                                <div className="flex items-center justify-between">
                                                    <span className="text-[0.6875rem] text-text-muted font-medium tracking-wide">
                                                        {t("durationLabel")}
                                                    </span>
                                                    <span className="text-sm font-bold text-foreground tabular-nums">
                                                        {draft.duration}<span className="text-text-muted text-xs font-medium ml-0.5">s</span>
                                                    </span>
                                                </div>
                                                <div className="relative h-[6px] bg-glass rounded-full">
                                                    {/* Gradient fill track */}
                                                    <div
                                                        className={`absolute top-0 left-0 h-full rounded-full bg-gradient-to-r ${activeAccent} opacity-60 transition-all duration-150`}
                                                        style={{ width: `${pct}%` }}
                                                    />
                                                    {/* Glow on track end */}
                                                    <div
                                                        className="absolute top-1/2 -translate-y-1/2 w-8 h-8 rounded-full opacity-20 pointer-events-none"
                                                        style={{
                                                            left: `${pct}%`,
                                                            transform: `translate(-50%, -50%)`,
                                                            background: `radial-gradient(circle, currentColor 0%, transparent 70%)`,
                                                        }}
                                                    />
                                                    <input
                                                        type="range"
                                                        min={dc.min}
                                                        max={dc.max}
                                                        step={dc.step}
                                                        value={draft.duration}
                                                        onChange={(e) => updateDraft("duration", parseInt(e.target.value))}
                                                        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
                                                    />
                                                    {/* Thumb */}
                                                    <motion.div
                                                        className="absolute top-1/2 -translate-y-1/2 w-[18px] h-[18px] rounded-full bg-white border-2 border-surface shadow-[0_0_12px_rgba(255,255,255,0.15)] pointer-events-none"
                                                        animate={{ left: `calc(${pct}% - 9px)` }}
                                                        transition={{ type: "spring", stiffness: 300, damping: 25 }}
                                                    />
                                                </div>
                                                <div className="flex justify-between text-[0.625rem] text-text-muted font-semibold tabular-nums">
                                                    <span>{dc.min}s</span>
                                                    <span>{dc.max}s</span>
                                                </div>
                                            </div>
                                        );
                                    }
                                    // Buttons type
                                    return (
                                        <div className="flex gap-2">
                                            {dc.options.map((dur) => {
                                                const isActive = draft.duration === dur;
                                                return (
                                                    <motion.button
                                                        key={dur}
                                                        whileHover={{ scale: 1.05 }}
                                                        whileTap={{ scale: 0.95 }}
                                                        onClick={() => updateDraft("duration", dur)}
                                                        className={`relative flex-1 py-3 text-sm font-bold rounded-xl border transition-all duration-200 overflow-hidden ${
                                                            isActive
                                                                ? "bg-elevated border-foreground/[0.12] text-foreground shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
                                                                : "bg-glass border-border-subtle text-text-muted hover:border-foreground/30 hover:text-foreground"
                                                        }`}
                                                    >
                                                        {isActive && (
                                                            <div className={`absolute inset-0 bg-gradient-to-r ${activeAccent} opacity-[0.06]`} />
                                                        )}
                                                        <span className="relative z-10">{dur}s</span>
                                                    </motion.button>
                                                );
                                            })}
                                        </div>
                                    );
                                })()}
                            </motion.section>

                            {/* Advanced Parameters */}
                            {hasAdvancedParams && (
                                <>
                                    <motion.div
                                        custom={2}
                                        variants={sectionVariants}
                                        initial="hidden"
                                        animate="visible"
                                        className="w-full h-px bg-gradient-to-r from-transparent via-glass-border to-transparent"
                                    />
                                    <motion.section
                                        custom={2}
                                        variants={sectionVariants}
                                        initial="hidden"
                                        animate="visible"
                                        className="space-y-5"
                                    >
                                        <div className="flex items-center gap-2.5">
                                            <SlidersHorizontal size={13} className="text-text-muted" strokeWidth={1.5} />
                                            <h3 className="text-[0.625rem] font-bold text-text-muted uppercase tracking-[0.2em]">
                                                {t("advancedParams")}
                                            </h3>
                                        </div>

                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-5 auto-flow-dense">
                                            {/* Resolution */}
                                            {modelParams.resolution && (
                                                <div className="space-y-2.5">
                                                    <label className="block text-[0.6875rem] text-text-muted font-semibold tracking-wide">
                                                        {tm("resolutionLabel")}
                                                    </label>
                                                    <div className="flex flex-wrap gap-1.5">
                                                        {modelParams.resolution.options.map((res) => {
                                                            const isActive = draft.resolution === res;
                                                            return (
                                                                <motion.button
                                                                    key={res}
                                                                    whileHover={{ scale: 1.06 }}
                                                                    whileTap={{ scale: 0.94 }}
                                                                    onClick={() => updateDraft("resolution", res)}
                                                                    className={`px-3 py-1.5 text-[0.6875rem] font-bold rounded-lg border transition-all duration-200 ${
                                                                        isActive
                                                                            ? `bg-gradient-to-r ${activeAccent} bg-opacity-10 border-foreground/[0.12] text-foreground`
                                                                            : "bg-glass border-border-subtle text-text-muted hover:border-foreground/30 hover:text-foreground"
                                                                    }`}
                                                                    style={isActive ? { background: `linear-gradient(to right, var(--tw-gradient-stops))`, opacity: 0.9 } : {}}
                                                                >
                                                                    {res}
                                                                </motion.button>
                                                            );
                                                        })}
                                                    </div>
                                                </div>
                                            )}

                                            {/* Prompt Extend */}
                                            {modelParams.promptExtend && (
                                                <div className="flex items-center justify-between py-1">
                                                    <label className="text-[0.6875rem] text-text-muted font-semibold tracking-wide">
                                                        {tm("promptEnhancer")}
                                                    </label>
                                                    <button
                                                        onClick={() => updateDraft("promptExtend", !draft.promptExtend)}
                                                        className={`relative w-11 h-[22px] rounded-full transition-colors duration-300 ${
                                                            draft.promptExtend
                                                                ? `bg-gradient-to-r ${activeAccent}`
                                                                : "bg-elevated"
                                                        }`}
                                                    >
                                                        <motion.div
                                                            className="absolute top-[3px] w-4 h-4 rounded-full bg-white shadow-[0_0_8px_rgba(255,255,255,0.2)]"
                                                            animate={{
                                                                left: draft.promptExtend ? "22px" : "4px",
                                                            }}
                                                            transition={springFast}
                                                        />
                                                    </button>
                                                </div>
                                            )}

                                            {/* Negative Prompt */}
                                            {modelParams.negativePrompt && (
                                                <div className="md:col-span-2 space-y-2.5">
                                                    <label className="block text-[0.6875rem] text-text-muted font-semibold tracking-wide">
                                                        {tm("negativePrompt")}
                                                    </label>
                                                    <input
                                                        type="text"
                                                        value={draft.negativePrompt}
                                                        onChange={(e) => updateDraft("negativePrompt", e.target.value)}
                                                        placeholder={tm("negativePromptPlaceholder")}
                                                        className="w-full text-sm bg-glass border border-glass-border rounded-xl px-4 py-3 text-foreground placeholder:text-text-muted focus:outline-none focus:border-foreground/[0.14] focus:bg-glass transition-all duration-200"
                                                    />
                                                </div>
                                            )}

                                            {/* Kling: Mode */}
                                            {modelParams.mode && (
                                                <div className="space-y-2.5">
                                                    <label className="block text-[0.6875rem] text-text-muted font-semibold tracking-wide">
                                                        {tm("modeLabel")}
                                                    </label>
                                                    <div className="flex gap-1.5">
                                                        {modelParams.mode.options.map((opt) => {
                                                            const isActive = draft.mode === opt;
                                                            return (
                                                                <motion.button
                                                                    key={opt}
                                                                    whileHover={{ scale: 1.04 }}
                                                                    whileTap={{ scale: 0.96 }}
                                                                    onClick={() => updateDraft("mode", opt)}
                                                                    className={`flex-1 py-2.5 text-[0.6875rem] font-bold rounded-lg border transition-all duration-200 uppercase tracking-wider ${
                                                                        isActive
                                                                            ? "bg-elevated border-foreground/[0.12] text-foreground"
                                                                            : "bg-glass border-border-subtle text-text-muted hover:border-foreground/30 hover:text-foreground"
                                                                    }`}
                                                                >
                                                                    {opt}
                                                                </motion.button>
                                                            );
                                                        })}
                                                    </div>
                                                </div>
                                            )}

                                            {/* Kling: CFG Scale */}
                                            {modelParams.cfgScale && (
                                                <div className="space-y-2.5">
                                                    <div className="flex items-center justify-between">
                                                        <label className="text-[0.6875rem] text-text-muted font-semibold tracking-wide">
                                                            {tm("cfgScale")}
                                                        </label>
                                                        <span className="text-[0.6875rem] font-bold text-text-secondary tabular-nums">
                                                            {(draft.cfgScale ?? 0.5).toFixed(1)}
                                                        </span>
                                                    </div>
                                                    <div className="relative h-[6px] bg-glass rounded-full">
                                                        {(() => {
                                                            const cfgVal = draft.cfgScale ?? 0.5;
                                                            const cfgMin = modelParams.cfgScale.min ?? 0;
                                                            const cfgMax = modelParams.cfgScale.max ?? 1;
                                                            const pct = ((cfgVal - cfgMin) / (cfgMax - cfgMin)) * 100;
                                                            return (
                                                                <>
                                                                    <div
                                                                        className={`absolute top-0 left-0 h-full rounded-full bg-gradient-to-r ${activeAccent} opacity-50 transition-all duration-150`}
                                                                        style={{ width: `${pct}%` }}
                                                                    />
                                                                    <input
                                                                        type="range"
                                                                        min={cfgMin}
                                                                        max={cfgMax}
                                                                        step={modelParams.cfgScale.step}
                                                                        value={cfgVal}
                                                                        onChange={(e) => updateDraft("cfgScale", parseFloat(e.target.value))}
                                                                        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10"
                                                                    />
                                                                    <motion.div
                                                                        className="absolute top-1/2 -translate-y-1/2 w-[16px] h-[16px] rounded-full bg-white border-2 border-surface shadow-[0_0_8px_rgba(255,255,255,0.12)] pointer-events-none"
                                                                        animate={{ left: `calc(${pct}% - 8px)` }}
                                                                        transition={{ type: "spring", stiffness: 300, damping: 25 }}
                                                                    />
                                                                </>
                                                            );
                                                        })()}
                                                    </div>
                                                </div>
                                            )}

                                            {/* Vidu: Movement Amplitude */}
                                            {modelParams.movementAmplitude && (
                                                <div className="space-y-2.5">
                                                    <label className="block text-[0.6875rem] text-text-muted font-semibold tracking-wide">
                                                        {tm("movementAmplitude")}
                                                    </label>
                                                    <div className="flex gap-1.5">
                                                        {modelParams.movementAmplitude.options.map((opt) => {
                                                            const isActive = draft.movementAmplitude === opt;
                                                            return (
                                                                <motion.button
                                                                    key={opt}
                                                                    whileHover={{ scale: 1.06 }}
                                                                    whileTap={{ scale: 0.94 }}
                                                                    onClick={() => updateDraft("movementAmplitude", opt)}
                                                                    className={`flex-1 py-2 text-[0.6875rem] font-bold rounded-lg border transition-all duration-200 capitalize ${
                                                                        isActive
                                                                            ? "bg-elevated border-foreground/[0.12] text-foreground"
                                                                            : "bg-glass border-border-subtle text-text-muted hover:border-foreground/30 hover:text-foreground"
                                                                    }`}
                                                                >
                                                                    {opt === "auto" ? "Auto" : opt === "small" ? "S" : opt === "medium" ? "M" : "L"}
                                                                </motion.button>
                                                            );
                                                        })}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    </motion.section>
                                </>
                            )}
                        </div>

                        {/* Footer — Glass bar with accent glow CTA */}
                        <div className="flex gap-3 px-7 py-5 border-t border-glass-border shrink-0 bg-glass">
                            <motion.button
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.98 }}
                                onClick={onClose}
                                className="flex-1 px-4 py-3 rounded-xl bg-glass hover:bg-hover-bg border border-glass-border text-text-muted hover:text-foreground text-sm font-semibold tracking-wide transition-all duration-200"
                            >
                                Cancel
                            </motion.button>
                            <motion.button
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.98 }}
                                onClick={handleApply}
                                className={`relative flex-1 px-4 py-3 rounded-xl bg-gradient-to-r ${activeAccent} text-foreground text-sm font-bold tracking-wide transition-all duration-200 shadow-[0_0_32px_rgba(100,108,255,0.2)] hover:shadow-[0_0_48px_rgba(100,108,255,0.3)]`}
                            >
                                {t("applySettings")}
                            </motion.button>
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
