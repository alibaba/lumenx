"use client";

import { useState, useRef } from "react";
import { useTranslations } from "next-intl";
import { motion, AnimatePresence } from "framer-motion";
import { Settings2, List, RefreshCw, ChevronDown, ChevronUp, Mic, Music, VolumeX, Wand2 } from "lucide-react";
import VideoQueue from "./VideoQueue";
import { VideoTask, api } from "@/lib/api";
import { VideoParams, GRID_COLS_CLASS } from "@/store/projectStore";
import {
    DEFAULT_I2V_MODEL_ID,
    type DurationConfig,
    type ModelParamSupport,
    R2V_SELECTION_MODEL_ID,
} from "@/lib/modelCatalog";
import { LEGACY_VIDEO_I2V_MODELS, resolveLegacyVideoSidebarModelId } from "@/lib/legacyVideoSidebarModels";
import GroupedModelGrid from "@/components/common/GroupedModelGrid";

interface VideoSidebarProps {
    tasks: VideoTask[];
    onRemix: (task: VideoTask) => void;
    params: VideoParams;
    setParams: (params: VideoParams) => void;
}

export default function VideoSidebar({ tasks, onRemix, params, setParams }: VideoSidebarProps) {
    const tm = useTranslations("motion");
    const [activeTab, setActiveTab] = useState<"settings" | "queue">("settings");
    const [isUploadingAudio, setIsUploadingAudio] = useState(false);
    const audioInputRef = useRef<HTMLInputElement>(null);
    const [showNegative, setShowNegative] = useState(false);

    const currentModelConfig =
        LEGACY_VIDEO_I2V_MODELS.find(m => m.id === resolveLegacyVideoSidebarModelId(params.model)) ??
        LEGACY_VIDEO_I2V_MODELS.find(m => m.id === DEFAULT_I2V_MODEL_ID) ??
        LEGACY_VIDEO_I2V_MODELS[0];
    const r2vSelectionModelName =
        LEGACY_VIDEO_I2V_MODELS.find((model) => model.id === resolveLegacyVideoSidebarModelId(R2V_SELECTION_MODEL_ID))?.name ??
        tm("currentR2VModel");
    const modelParams: ModelParamSupport = currentModelConfig?.params ?? {};

    const updateParam = (key: string, value: any) => {
        const newParams = { ...params, [key]: value };
        // When model changes, clamp duration and reset model-specific params
        if (key === "model") {
            const newModelConfig = LEGACY_VIDEO_I2V_MODELS.find(m => m.id === value);
            if (newModelConfig?.duration) {
                const dc = newModelConfig.duration;
                if (dc.type === 'fixed') {
                    newParams.duration = dc.value;
                } else if (dc.type === 'slider') {
                    if (newParams.duration < dc.min || newParams.duration > dc.max) {
                        newParams.duration = dc.default;
                    }
                } else if (dc.type === 'buttons') {
                    if (!dc.options.includes(newParams.duration)) {
                        newParams.duration = dc.default;
                    }
                }
            }
            // Reset model-specific params to defaults
            const np = newModelConfig?.params ?? {};
            newParams.resolution = np.resolution?.default ?? "720p";
            newParams.promptExtend = !!np.promptExtend;
            newParams.negativePrompt = "";
            newParams.shotType = "single";
            newParams.generateAudio = false;
            newParams.audioUrl = "";
            // Kling defaults
            newParams.mode = np.mode?.default ?? "std";
            newParams.sound = false;
            newParams.cfgScale = np.cfgScale?.default ?? 0.5;
            // Vidu defaults
            newParams.viduAudio = true;
            newParams.movementAmplitude = np.movementAmplitude?.default ?? "auto";
        }
        setParams(newParams);
    };

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        setIsUploadingAudio(true);
        try {
            const res = await api.uploadFile(file);
            updateParam("audioUrl", res.url);
            setAudioMode("custom");
        } catch (error) {
            console.error("Audio upload failed:", error);
        } finally {
            setIsUploadingAudio(false);
            // Reset input
            if (audioInputRef.current) audioInputRef.current.value = "";
        }
    };

    // Audio Mode Logic
    const audioMode = params.audioUrl ? "custom" : params.generateAudio ? "ai" : "mute";
    const setAudioMode = (mode: "mute" | "ai" | "custom") => {
        if (mode === "mute") {
            setParams({ ...params, generateAudio: false, audioUrl: "" });
        } else if (mode === "ai") {
            setParams({ ...params, generateAudio: true, audioUrl: "" });
        } else {
            // Custom / Sound Driven
            setParams({ ...params, generateAudio: false });
            // Trigger upload if no URL exists
            if (!params.audioUrl && audioInputRef.current) {
                audioInputRef.current.click();
            }
        }
    };

    return (
        <div className="h-full flex flex-col bg-surface backdrop-blur-sm border-l border-border-subtle">
            <input
                type="file"
                ref={audioInputRef}
                className="hidden"
                accept="audio/*"
                onChange={handleFileUpload}
            />
            {/* Tab Navigation */}
            <div className="flex border-b border-border-subtle">
                <button
                    onClick={() => setActiveTab("settings")}
                    className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${activeTab === "settings"
                        ? "text-foreground border-b-2 border-primary bg-glass"
                        : "text-text-muted hover:text-text-secondary hover:bg-glass"
                        }`}
                >
                    <Settings2 size={16} />
                    {tm("motionParams")}
                </button>
                <button
                    onClick={() => setActiveTab("queue")}
                    className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${activeTab === "queue"
                        ? "text-foreground border-b-2 border-primary bg-glass"
                        : "text-text-muted hover:text-text-secondary hover:bg-glass"
                        }`}
                >
                    <List size={16} />
                    {tm("queue")}
                    {tasks.filter(t => t.status === "pending" || t.status === "processing").length > 0 && (
                        <span className="bg-primary text-white text-[0.625rem] px-1.5 rounded-full">
                            {tasks.filter(t => t.status === "pending" || t.status === "processing").length}
                        </span>
                    )}
                </button>
            </div>

            {/* Content Area */}
            <div className="flex-1 overflow-hidden relative">
                <AnimatePresence mode="wait">
                    {activeTab === "settings" ? (
                        <motion.div
                            key="settings"
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -20 }}
                            className="absolute inset-0 overflow-y-auto custom-scrollbar p-6 space-y-8"
                        >
                            {/* 1. Basic Settings */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-text-muted uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-primary rounded-full" />
                                    {tm("basicSettings")}
                                </h3>

                                {/* Model Selection - R2V mode: only the current catalog-backed R2V model is selectable */}
                                <div>
                                    <label className="block text-xs text-text-secondary mb-2">
                                        {tm("modelLabel")}
                                        {params.generationMode === "r2v" && (
                                            <span className="text-primary ml-2">{tm("r2vOnly", { name: r2vSelectionModelName })}</span>
                                        )}
                                    </label>
                                    <GroupedModelGrid
                                        models={LEGACY_VIDEO_I2V_MODELS}
                                        selectedId={params.generationMode === "r2v" ? resolveLegacyVideoSidebarModelId(R2V_SELECTION_MODEL_ID) : resolveLegacyVideoSidebarModelId(params.model)}
                                        onSelect={(id) => updateParam("model", id)}
                                    />
                                </div>

                                {/* Duration - Dynamic per model */}
                                {(() => {
                                    const durationConfig: DurationConfig = currentModelConfig?.duration ?? { type: 'buttons', options: [5, 10], default: 5 };

                                    if (durationConfig.type === 'fixed') {
                                        return (
                                            <div>
                                                <label className="block text-xs text-text-secondary mb-2">{tm("durationLabel")}</label>
                                                <div className="py-1.5 text-xs text-text-muted bg-glass rounded-lg text-center border border-transparent">
                                                    {durationConfig.value}s {tm("durationFixed")}
                                                </div>
                                            </div>
                                        );
                                    }

                                    if (durationConfig.type === 'slider') {
                                        return (
                                            <div>
                                                <label className="block text-xs text-text-secondary mb-2">
                                                    {tm("durationLabel")} <span className="text-primary font-medium">{params.duration}s</span>
                                                </label>
                                                <input
                                                    type="range"
                                                    min={durationConfig.min}
                                                    max={durationConfig.max}
                                                    step={durationConfig.step}
                                                    value={params.duration}
                                                    onChange={(e) => updateParam("duration", parseInt(e.target.value))}
                                                    className="w-full h-1.5 bg-hover-bg rounded-full appearance-none cursor-pointer accent-primary [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:shadow-lg"
                                                />
                                                <div className="flex justify-between text-[0.625rem] text-text-muted mt-1">
                                                    <span>{durationConfig.min}s</span>
                                                    <span>{durationConfig.max}s</span>
                                                </div>
                                            </div>
                                        );
                                    }

                                    // buttons
                                    return (
                                        <div>
                                            <label className="block text-xs text-text-secondary mb-2">{tm("durationLabel")}</label>
                                            <div className={`grid ${GRID_COLS_CLASS[durationConfig.options.length] ?? 'grid-cols-3'} gap-2`}>
                                                {durationConfig.options.map(dur => (
                                                    <button
                                                        key={dur}
                                                        onClick={() => updateParam("duration", dur)}
                                                        className={`py-1.5 text-xs rounded-lg border transition-all ${params.duration === dur
                                                            ? "bg-primary/20 border-primary text-primary"
                                                            : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                            }`}
                                                    >
                                                        {dur}s
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                    );
                                })()}

                                {/* Shot Type - Only when model supports it and promptExtend is enabled */}
                                {modelParams.shotType && (
                                    <div>
                                        <label className="block text-xs text-text-secondary mb-2">
                                            {tm("shotType")}
                                            {!params.promptExtend && (
                                                <span className="text-yellow-500 ml-2">{tm("needPromptExtend")}</span>
                                            )}
                                        </label>
                                        <div className="grid grid-cols-2 gap-2">
                                            <button
                                                onClick={() => updateParam("shotType", "single")}
                                                disabled={!params.promptExtend}
                                                className={`py-2 text-xs rounded-lg border transition-all flex flex-col items-center gap-1 ${params.shotType === "single"
                                                    ? "bg-primary/20 border-primary text-primary"
                                                    : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                    } ${!params.promptExtend ? 'opacity-50 cursor-not-allowed' : ''}`}
                                            >
                                                <span className="font-medium">Single</span>
                                                <span className="text-[0.625rem] text-text-muted">{tm("singleShot")}</span>
                                            </button>
                                            <button
                                                onClick={() => updateParam("shotType", "multi")}
                                                disabled={!params.promptExtend}
                                                className={`py-2 text-xs rounded-lg border transition-all flex flex-col items-center gap-1 ${params.shotType === "multi"
                                                    ? "bg-primary/20 border-primary text-primary"
                                                    : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                    } ${!params.promptExtend ? 'opacity-50 cursor-not-allowed' : ''}`}
                                            >
                                                <span className="font-medium">Multi</span>
                                                <span className="text-[0.625rem] text-text-muted">{tm("multiShot")}</span>
                                            </button>
                                        </div>
                                        <p className="text-[0.625rem] text-text-muted mt-1.5">
                                            {tm("multiShotHint")}
                                        </p>
                                    </div>
                                )}

                                {/* Kling: Mode (std/pro) */}
                                {modelParams.mode && (
                                    <div>
                                        <label className="block text-xs text-text-secondary mb-2">{tm("modeLabel")}</label>
                                        <div className="grid grid-cols-2 gap-2">
                                            {modelParams.mode.options.map(opt => (
                                                <button
                                                    key={opt}
                                                    onClick={() => updateParam("mode", opt)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all ${params.mode === opt
                                                        ? "bg-primary/20 border-primary text-primary"
                                                        : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                        }`}
                                                >
                                                    {opt.toUpperCase()}
                                                </button>
                                            ))}
                                        </div>
                                        <p className="text-[0.625rem] text-text-muted mt-1.5">
                                            {tm("proModeHint")}
                                        </p>
                                    </div>
                                )}
                            </section>

                            <div className="w-full h-px bg-glass" />

                            {/* 2. Quality & Specs */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-text-muted uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-blue-500 rounded-full" />
                                    {tm("qualitySpecs")}
                                </h3>

                                {/* Resolution - only when model supports it */}
                                {modelParams.resolution && (
                                    <div>
                                        <label className="block text-xs text-text-secondary mb-2">{tm("resolutionLabel")}</label>
                                        <div className={`grid ${GRID_COLS_CLASS[modelParams.resolution.options.length] ?? 'grid-cols-3'} gap-2`}>
                                            {modelParams.resolution.options.map(res => (
                                                <button
                                                    key={res}
                                                    onClick={() => updateParam("resolution", res)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all ${params.resolution === res
                                                        ? "bg-blue-500/20 border-blue-500 text-blue-500"
                                                        : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                        }`}
                                                >
                                                    {res}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* Batch Size */}
                                <div>
                                    <label className="block text-xs text-text-secondary mb-2">{tm("batchSize")}</label>
                                    <div className="grid grid-cols-3 gap-2">
                                        {[1, 2, 4].map(size => (
                                            <button
                                                key={size}
                                                onClick={() => updateParam("batchSize", size)}
                                                className={`py-1.5 text-xs rounded-lg border transition-all ${params.batchSize === size
                                                    ? "bg-blue-500/20 border-blue-500 text-blue-500"
                                                    : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                    }`}
                                            >
                                                {size}x
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Kling: CFG Scale */}
                                {modelParams.cfgScale && (
                                    <div>
                                        <label className="block text-xs text-text-secondary mb-2">
                                            {tm("cfgScale")} <span className="text-blue-500 font-medium">{params.cfgScale.toFixed(1)}</span>
                                        </label>
                                        <input
                                            type="range"
                                            min={modelParams.cfgScale.min}
                                            max={modelParams.cfgScale.max}
                                            step={modelParams.cfgScale.step}
                                            value={params.cfgScale}
                                            onChange={(e) => updateParam("cfgScale", parseFloat(e.target.value))}
                                            className="w-full h-1.5 bg-hover-bg rounded-full appearance-none cursor-pointer accent-blue-500 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-500 [&::-webkit-slider-thumb]:shadow-lg"
                                        />
                                        <div className="flex justify-between text-[0.625rem] text-text-muted mt-1">
                                            <span>{modelParams.cfgScale.min} {tm("cfgMin")}</span>
                                            <span>{modelParams.cfgScale.max} {tm("cfgMax")}</span>
                                        </div>
                                    </div>
                                )}

                                {/* Vidu: Movement Amplitude */}
                                {modelParams.movementAmplitude && (
                                    <div>
                                        <label className="block text-xs text-text-secondary mb-2">{tm("movementAmplitude")}</label>
                                        <div className={`grid ${GRID_COLS_CLASS[modelParams.movementAmplitude.options.length] ?? 'grid-cols-4'} gap-2`}>
                                            {modelParams.movementAmplitude.options.map(opt => (
                                                <button
                                                    key={opt}
                                                    onClick={() => updateParam("movementAmplitude", opt)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all capitalize ${params.movementAmplitude === opt
                                                        ? "bg-blue-500/20 border-blue-500 text-blue-500"
                                                        : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                        }`}
                                                >
                                                    {opt === 'auto' ? 'Auto' : opt === 'small' ? 'S' : opt === 'medium' ? 'M' : 'L'}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </section>

                            <div className="w-full h-px bg-glass" />

                            {/* 3. Creative & Audio */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-text-muted uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-purple-500 rounded-full" />
                                    {tm("creativeAudio")}
                                </h3>

                                {/* Prompt Enhancer - only when model supports it */}
                                {modelParams.promptExtend && (
                                    <div className="flex items-center justify-between">
                                        <label className="text-xs text-text-secondary flex items-center gap-2">
                                            <Wand2 size={12} />
                                            {tm("promptEnhancer")}
                                        </label>
                                        <button
                                            onClick={() => updateParam("promptExtend", !params.promptExtend)}
                                            className={`w-10 h-5 rounded-full relative transition-colors ${params.promptExtend ? "bg-purple-500" : "bg-hover-bg"}`}
                                        >
                                            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${params.promptExtend ? "left-6" : "left-1"}`} />
                                        </button>
                                    </div>
                                )}

                                {/* Wan Audio Settings (三模式) - only when model supports it */}
                                {modelParams.audio && (
                                    <div>
                                        <label className="block text-xs text-text-secondary mb-2">
                                            {tm("audioSettings")}
                                        </label>
                                        <div className="grid grid-cols-3 gap-2 mb-2">
                                            <button
                                                onClick={() => setAudioMode("mute")}
                                                className={`py-1.5 text-xs rounded-lg border flex items-center justify-center gap-1 transition-all ${audioMode === "mute"
                                                    ? "bg-purple-500/20 border-purple-500 text-purple-500"
                                                    : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                    }`}
                                            >
                                                <VolumeX size={12} /> Mute
                                            </button>
                                            <button
                                                onClick={() => setAudioMode("ai")}
                                                className={`py-1.5 text-xs rounded-lg border flex items-center justify-center gap-1 transition-all ${audioMode === "ai"
                                                    ? "bg-purple-500/20 border-purple-500 text-purple-500"
                                                    : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                    }`}
                                            >
                                                <Mic size={12} /> AI Sound
                                            </button>
                                            <button
                                                onClick={() => setAudioMode("custom")}
                                                className={`py-1.5 text-xs rounded-lg border flex items-center justify-center gap-1 transition-all ${audioMode === "custom"
                                                    ? "bg-purple-500/20 border-purple-500 text-purple-500"
                                                    : "bg-glass border-transparent text-text-secondary hover:bg-hover-bg"
                                                    }`}
                                            >
                                                <Music size={12} /> Sound Driven
                                            </button>
                                        </div>
                                        {audioMode === "custom" && (
                                            <div className="relative">
                                                <input
                                                    type="text"
                                                    value={params.audioUrl || ""}
                                                    readOnly
                                                    placeholder={isUploadingAudio ? "Uploading..." : "Click to upload audio"}
                                                    onClick={() => audioInputRef.current?.click()}
                                                    className="w-full bg-glass border border-glass-border rounded-lg py-1.5 px-2 text-xs text-foreground focus:border-purple-500 focus:outline-none cursor-pointer"
                                                />
                                                {params.audioUrl && (
                                                    <button
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            updateParam("audioUrl", "");
                                                            setAudioMode("mute");
                                                        }}
                                                        className="absolute right-2 top-1.5 text-text-muted hover:text-foreground"
                                                    >
                                                        <VolumeX size={12} />
                                                    </button>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                )}

                                {/* Kling: Sound on/off */}
                                {modelParams.sound && (
                                    <div className="flex items-center justify-between">
                                        <label className="text-xs text-text-secondary flex items-center gap-2">
                                            <Mic size={12} />
                                            {tm("soundAI")}
                                        </label>
                                        <button
                                            onClick={() => updateParam("sound", !params.sound)}
                                            className={`w-10 h-5 rounded-full relative transition-colors ${params.sound ? "bg-purple-500" : "bg-hover-bg"}`}
                                        >
                                            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${params.sound ? "left-6" : "left-1"}`} />
                                        </button>
                                    </div>
                                )}

                                {/* Vidu: Audio on/off */}
                                {modelParams.viduAudio && (
                                    <div className="flex items-center justify-between">
                                        <label className="text-xs text-text-secondary flex items-center gap-2">
                                            <Mic size={12} />
                                            {tm("audioOutput")}
                                        </label>
                                        <button
                                            onClick={() => updateParam("viduAudio", !params.viduAudio)}
                                            className={`w-10 h-5 rounded-full relative transition-colors ${params.viduAudio ? "bg-purple-500" : "bg-hover-bg"}`}
                                        >
                                            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${params.viduAudio ? "left-6" : "left-1"}`} />
                                        </button>
                                    </div>
                                )}

                                {/* Negative Prompt - only when model supports it */}
                                {modelParams.negativePrompt && (
                                    <div>
                                        <button
                                            onClick={() => setShowNegative(!showNegative)}
                                            className="text-xs text-text-muted hover:text-text-secondary flex items-center gap-1 mb-2"
                                        >
                                            {showNegative ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
                                            {tm("negativePrompt")}
                                        </button>
                                        <AnimatePresence>
                                            {showNegative && (
                                                <motion.div
                                                    initial={{ height: 0, opacity: 0 }}
                                                    animate={{ height: "auto", opacity: 1 }}
                                                    exit={{ height: 0, opacity: 0 }}
                                                    className="overflow-hidden"
                                                >
                                                    <textarea
                                                        value={params.negativePrompt || ""}
                                                        onChange={(e) => updateParam("negativePrompt", e.target.value)}
                                                        placeholder="Low quality, blurry, distorted..."
                                                        className="w-full h-20 bg-glass border border-glass-border rounded-lg p-2 text-xs text-foreground focus:border-purple-500 focus:outline-none resize-none"
                                                    />
                                                </motion.div>
                                            )}
                                        </AnimatePresence>
                                    </div>
                                )}
                            </section>

                            <div className="w-full h-px bg-glass" />

                            {/* 4. Advanced / Effects */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-text-muted uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-orange-500 rounded-full" />
                                    {tm("advancedLabel")}
                                </h3>

                                {/* Seed - only when model supports it */}
                                {modelParams.seed && (
                                    <div>
                                        <label className="block text-xs text-text-secondary mb-2">{tm("seedLabel")}</label>
                                        <div className="relative">
                                            <input
                                                type="number"
                                                value={params.seed ?? ""}
                                                onChange={(e) => updateParam("seed", e.target.value ? parseInt(e.target.value) : undefined)}
                                                placeholder="Random (-1)"
                                                className="w-full bg-glass border border-glass-border rounded-lg py-1.5 pl-2 pr-8 text-xs text-foreground focus:border-orange-500 focus:outline-none [&::-webkit-inner-spin-button]:appearance-none"
                                            />
                                            <button
                                                onClick={() => updateParam("seed", Math.floor(Math.random() * 2147483647))}
                                                className="absolute right-2 top-1.5 text-text-muted hover:text-foreground"
                                                title="Randomize"
                                            >
                                                <RefreshCw size={12} />
                                            </button>
                                        </div>
                                    </div>
                                )}


                            </section>
                        </motion.div>
                    ) : (
                        <motion.div
                            key="queue"
                            initial={{ opacity: 0, x: 20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: 20 }}
                            className="absolute inset-0"
                        >
                            <VideoQueue tasks={tasks} onRemix={onRemix} />
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}
