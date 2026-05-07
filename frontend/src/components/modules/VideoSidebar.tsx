"use client";

import { useState, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Settings2, List, RefreshCw, ChevronDown, ChevronUp, Mic, Music, VolumeX, Wand2 } from "lucide-react";
import VideoQueue from "./VideoQueue";
import { VideoTask, api } from "@/lib/api";
import { I2V_MODELS, DurationConfig, ModelParamSupport, VideoParams, GRID_COLS_CLASS, isSeedanceI2VModel } from "@/store/projectStore";
import {
    getSeedanceLowCostPreset,
    getSeedanceSubmissionState,
    SEEDANCE_EDIT_MODE_OPTIONS,
    SEEDANCE_EXTEND_MODE_OPTIONS,
    SEEDANCE_REFERENCE_MODE_OPTIONS,
    SEEDANCE_WORKFLOW_OPTIONS,
} from "@/lib/seedance";
import { messages, shotTypeTerms } from "@/lib/i18n";

interface VideoSidebarProps {
    tasks: VideoTask[];
    onRemix: (task: VideoTask) => void;
    params: VideoParams;
    setParams: (params: VideoParams) => void;
}

const copy = messages.modules.videoSidebar;

function getSeedanceWorkflowPanelCopy(params: VideoParams) {
    if (params.seedanceWorkflow === "extend") {
        if (params.seedanceExtendMode === "prepend") return copy.seedanceWorkflowPanels.extendPrepend;
        if (params.seedanceExtendMode === "trajectory") return copy.seedanceWorkflowPanels.extendTrajectory;
        return copy.seedanceWorkflowPanels.extendContinue;
    }

    if (params.seedanceWorkflow === "edit") {
        if (params.seedanceEditMode === "object_edit") return copy.seedanceWorkflowPanels.editObjectEdit;
        if (params.seedanceEditMode === "inpaint") return copy.seedanceWorkflowPanels.editInpaint;
        return copy.seedanceWorkflowPanels.editSubjectReplace;
    }

    return copy.seedanceWorkflowPanels.standard;
}

export default function VideoSidebar({ tasks, onRemix, params, setParams }: VideoSidebarProps) {
    const [activeTab, setActiveTab] = useState<"settings" | "queue">("settings");
    const [isUploadingAudio, setIsUploadingAudio] = useState(false);
    const audioInputRef = useRef<HTMLInputElement>(null);
    const [showNegative, setShowNegative] = useState(false);

    const currentModelConfig = I2V_MODELS.find(m => m.id === params.model);
    const modelParams: ModelParamSupport = currentModelConfig?.params ?? {};
    const isSeedanceModel = isSeedanceI2VModel(params.model);
    const seedanceSubmissionState = isSeedanceModel
        ? getSeedanceSubmissionState({
            previewOnly: params.seedancePreviewOnly,
            workflow: params.seedanceWorkflow as "standard" | "extend" | "edit",
            referenceMode: params.seedanceReferenceMode as "image" | "video" | "combo",
            imageUrls: [],
            referenceVideoUrls: params.referenceVideoUrls,
            referenceAudioUrl: params.referenceAudioUrl,
        })
        : null;
    const seedanceWorkflowPanel = isSeedanceModel ? getSeedanceWorkflowPanelCopy(params) : null;

    const updateParam = (key: string, value: any) => {
        const newParams = { ...params, [key]: value };
        // When model changes, clamp duration and reset model-specific params
        if (key === "model") {
            const newModelConfig = I2V_MODELS.find(m => m.id === value);
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
            newParams.aspectRatio = np.aspectRatio?.default ?? "adaptive";
            newParams.watermark = false;
            newParams.cameraFixed = false;
            newParams.referenceAudioUrl = "";
            newParams.referenceVideoUrls = [];
            newParams.seedanceReferenceMode = np.seedanceReferenceMode?.default ?? "image";
            newParams.seedanceWorkflow = np.seedanceWorkflow?.default ?? "standard";
            newParams.seedanceExtendMode = "continue";
            newParams.seedanceEditMode = "subject_replace";
            newParams.seedancePreviewOnly = false;
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
    const audioMode = isSeedanceModel
        ? (params.generateAudio ? "ai" : "mute")
        : params.audioUrl ? "custom" : params.generateAudio ? "ai" : "mute";
    const setAudioMode = (mode: "mute" | "ai" | "custom") => {
        if (mode === "custom" && isSeedanceModel) {
            return;
        }
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

    const handleApplySeedanceLowCostPreset = () => {
        setParams({
            ...params,
            ...getSeedanceLowCostPreset(),
        });
    };

    return (
        <div className="h-full flex flex-col bg-black/40 backdrop-blur-sm border-l border-white/5">
            <input
                type="file"
                ref={audioInputRef}
                className="hidden"
                accept="audio/*"
                onChange={handleFileUpload}
            />
            {/* Tab Navigation */}
            <div className="flex border-b border-white/5">
                <button
                    onClick={() => setActiveTab("settings")}
                    className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${activeTab === "settings"
                        ? "text-white border-b-2 border-primary bg-white/5"
                        : "text-gray-500 hover:text-gray-300 hover:bg-white/5"
                        }`}
                >
                    <Settings2 size={16} />
                    {copy.motionParams}
                </button>
                <button
                    onClick={() => setActiveTab("queue")}
                    className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${activeTab === "queue"
                        ? "text-white border-b-2 border-primary bg-white/5"
                        : "text-gray-500 hover:text-gray-300 hover:bg-white/5"
                        }`}
                >
                    <List size={16} />
                    {copy.queue}
                    {tasks.filter(t => t.status === "pending" || t.status === "processing").length > 0 && (
                        <span className="bg-primary text-white text-[10px] px-1.5 rounded-full">
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
                                <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-primary rounded-full" />
                                    {copy.sections.basic}
                                </h3>

                                {/* Model Selection - R2V mode: only Wan 2.6 is selectable */}
                                <div>
                                    <label className="block text-xs text-gray-400 mb-2">
                                        {copy.model}
                                        {params.generationMode === "r2v" && (
                                            <span className="text-purple-400 ml-2">({copy.r2vOnlyWan})</span>
                                        )}
                                    </label>
                                    <div className="space-y-2">
                                        {I2V_MODELS.map((model) => {
                                            const isR2VMode = params.generationMode === "r2v";
                                            const isWan26 = model.id === "wan2.6-i2v";
                                            const isDisabled = isR2VMode && !isWan26;
                                            const isSelected = isR2VMode ? isWan26 : params.model === model.id;

                                            return (
                                                <button
                                                    key={model.id}
                                                    onClick={() => !isDisabled && updateParam("model", model.id)}
                                                    disabled={isDisabled}
                                                    className={`w-full flex items-center justify-between p-2.5 rounded-lg border transition-all text-left ${isSelected
                                                        ? 'border-primary/50 bg-primary/10'
                                                        : 'border-white/10 hover:border-white/20 bg-white/5'
                                                        } ${isDisabled ? 'opacity-40 cursor-not-allowed' : ''}`}
                                                >
                                                    <div>
                                                        <span className="text-xs font-medium text-white">{model.name}</span>
                                                        <p className="text-[10px] text-gray-500">{model.description}</p>
                                                    </div>
                                                    {isSelected && (
                                                        <div className="w-2 h-2 bg-primary rounded-full" />
                                                    )}
                                                </button>
                                            );
                                        })}
                                    </div>
                                </div>

                                {/* Duration - Dynamic per model */}
                                {(() => {
                                    const durationConfig: DurationConfig = currentModelConfig?.duration ?? { type: 'buttons', options: [5, 10], default: 5 };

                                    if (durationConfig.type === 'fixed') {
                                        return (
                                            <div>
                                                <label className="block text-xs text-gray-400 mb-2">{copy.duration}</label>
                                                <div className="py-1.5 text-xs text-gray-500 bg-white/5 rounded-lg text-center border border-transparent">
                                                    {durationConfig.value}s（{copy.fixed}）
                                                </div>
                                            </div>
                                        );
                                    }

                                    if (durationConfig.type === 'slider') {
                                        return (
                                            <div>
                                                <label className="block text-xs text-gray-400 mb-2">
                                                    {copy.duration} <span className="text-primary font-medium">{params.duration}s</span>
                                                </label>
                                                <input
                                                    type="range"
                                                    min={durationConfig.min}
                                                    max={durationConfig.max}
                                                    step={durationConfig.step}
                                                    value={params.duration}
                                                    onChange={(e) => updateParam("duration", parseInt(e.target.value))}
                                                    className="w-full h-1.5 bg-white/10 rounded-full appearance-none cursor-pointer accent-primary [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:shadow-lg"
                                                />
                                                <div className="flex justify-between text-[10px] text-gray-600 mt-1">
                                                    <span>{durationConfig.min}s</span>
                                                    <span>{durationConfig.max}s</span>
                                                </div>
                                            </div>
                                        );
                                    }

                                    // buttons
                                    return (
                                        <div>
                                            <label className="block text-xs text-gray-400 mb-2">{copy.duration}</label>
                                            <div className={`grid ${GRID_COLS_CLASS[durationConfig.options.length] ?? 'grid-cols-3'} gap-2`}>
                                                {durationConfig.options.map(dur => (
                                                    <button
                                                        key={dur}
                                                        onClick={() => updateParam("duration", dur)}
                                                        className={`py-1.5 text-xs rounded-lg border transition-all ${params.duration === dur
                                                            ? "bg-primary/20 border-primary text-primary"
                                                            : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                            }`}
                                                    >
                                                        {dur}s
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                    );
                                })()}

                                {isSeedanceModel && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">{copy.seedanceWorkflow}</label>
                                        <div className="grid grid-cols-3 gap-2">
                                            {SEEDANCE_WORKFLOW_OPTIONS.map((workflow) => (
                                                <button
                                                    key={workflow.value}
                                                    onClick={() => updateParam("seedanceWorkflow", workflow.value)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all ${params.seedanceWorkflow === workflow.value
                                                        ? "bg-primary/20 border-primary text-primary"
                                                        : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                        }`}
                                                >
                                                    {workflow.label}
                                                </button>
                                            ))}
                                        </div>
                                        <p className="text-[10px] text-gray-600 mt-1.5">{copy.seedanceWorkflowHint}</p>
                                    </div>
                                )}

                                {seedanceWorkflowPanel && (
                                    <div className="space-y-3 rounded-xl border border-fuchsia-500/20 bg-fuchsia-500/10 p-3">
                                        <div>
                                            <p className="text-xs font-medium text-white">{copy.seedanceWorkflowPanelTitle}</p>
                                            <p className="mt-1 text-xs font-semibold text-fuchsia-100">{seedanceWorkflowPanel.title}</p>
                                            <p className="mt-1 text-[11px] leading-relaxed text-fuchsia-100/80">{seedanceWorkflowPanel.summary}</p>
                                        </div>
                                        <div className="flex flex-wrap gap-2">
                                            {seedanceWorkflowPanel.highlights.map((item) => (
                                                <span
                                                    key={item}
                                                    className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-fuchsia-50"
                                                >
                                                    {item}
                                                </span>
                                            ))}
                                        </div>
                                        <p className="text-[11px] leading-relaxed text-gray-300">{seedanceWorkflowPanel.requirement}</p>
                                    </div>
                                )}

                                {isSeedanceModel && params.seedanceWorkflow === "extend" && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">{copy.seedanceExtendMode}</label>
                                        <div className="grid grid-cols-3 gap-2">
                                            {SEEDANCE_EXTEND_MODE_OPTIONS.map((mode) => (
                                                <button
                                                    key={mode.value}
                                                    onClick={() => updateParam("seedanceExtendMode", mode.value)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all ${params.seedanceExtendMode === mode.value
                                                        ? "bg-primary/20 border-primary text-primary"
                                                        : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                        }`}
                                                >
                                                    {mode.label}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {isSeedanceModel && params.seedanceWorkflow === "edit" && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">{copy.seedanceEditMode}</label>
                                        <div className="grid grid-cols-3 gap-2">
                                            {SEEDANCE_EDIT_MODE_OPTIONS.map((mode) => (
                                                <button
                                                    key={mode.value}
                                                    onClick={() => updateParam("seedanceEditMode", mode.value)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all ${params.seedanceEditMode === mode.value
                                                        ? "bg-primary/20 border-primary text-primary"
                                                        : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                        }`}
                                                >
                                                    {mode.label}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {isSeedanceModel && seedanceSubmissionState && (
                                    <div
                                        data-testid="seedance-submit-status"
                                        className={`rounded-xl border px-3 py-3 ${seedanceSubmissionState.reason === "ready"
                                            ? "border-emerald-500/25 bg-emerald-500/10"
                                            : "border-amber-500/25 bg-amber-500/10"
                                            }`}
                                    >
                                        <p className="text-xs font-medium text-white">{copy.seedanceSubmitStatus}</p>
                                        <p className={`mt-1 text-[11px] leading-relaxed ${seedanceSubmissionState.reason === "ready"
                                            ? "text-emerald-100/90"
                                            : "text-amber-100/90"
                                            }`}>
                                            {seedanceSubmissionState.reason === "manual_preview"
                                                ? copy.seedanceSubmitManualPreview
                                                : seedanceSubmissionState.reason === "workflow_missing_video"
                                                    ? copy.seedanceSubmitWorkflowLocked
                                                    : copy.seedanceSubmitReady}
                                        </p>
                                    </div>
                                )}

                                {/* Shot Type - Only when model supports it and promptExtend is enabled */}
                                {modelParams.shotType && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">
                                            {copy.shotType}
                                            {!params.promptExtend && (
                                                <span className="text-yellow-500 ml-2">({copy.promptExtendRequired})</span>
                                            )}
                                        </label>
                                        <div className="grid grid-cols-2 gap-2">
                                            <button
                                                onClick={() => updateParam("shotType", "single")}
                                                disabled={!params.promptExtend}
                                                className={`py-2 text-xs rounded-lg border transition-all flex flex-col items-center gap-1 ${params.shotType === "single"
                                                    ? "bg-primary/20 border-primary text-primary"
                                                    : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                    } ${!params.promptExtend ? 'opacity-50 cursor-not-allowed' : ''}`}
                                            >
                                                <span className="font-medium">{shotTypeTerms.single.label}</span>
                                                <span className="text-[10px] text-gray-500">{shotTypeTerms.single.description}</span>
                                            </button>
                                            <button
                                                onClick={() => updateParam("shotType", "multi")}
                                                disabled={!params.promptExtend}
                                                className={`py-2 text-xs rounded-lg border transition-all flex flex-col items-center gap-1 ${params.shotType === "multi"
                                                    ? "bg-primary/20 border-primary text-primary"
                                                    : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                    } ${!params.promptExtend ? 'opacity-50 cursor-not-allowed' : ''}`}
                                            >
                                                <span className="font-medium">{shotTypeTerms.multi.label}</span>
                                                <span className="text-[10px] text-gray-500">{shotTypeTerms.multi.description}</span>
                                            </button>
                                        </div>
                                        <p className="text-[10px] text-gray-600 mt-1.5">
                                            {copy.shotTypeHint}
                                        </p>
                                    </div>
                                )}

                                {/* Kling: Mode (std/pro) */}
                                {modelParams.mode && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">{copy.mode}</label>
                                        <div className="grid grid-cols-2 gap-2">
                                            {modelParams.mode.options.map(opt => (
                                                <button
                                                    key={opt}
                                                    onClick={() => updateParam("mode", opt)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all ${params.mode === opt
                                                        ? "bg-primary/20 border-primary text-primary"
                                                        : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                        }`}
                                                >
                                                    {opt.toUpperCase()}
                                                </button>
                                            ))}
                                        </div>
                                        <p className="text-[10px] text-gray-600 mt-1.5">
                                            {copy.modeProHint}
                                        </p>
                                    </div>
                                )}
                            </section>

                            <div className="w-full h-px bg-white/5" />

                            {/* 2. Quality & Specs */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-blue-500 rounded-full" />
                                    {copy.sections.quality}
                                </h3>

                                {/* Resolution - only when model supports it */}
                                {modelParams.resolution && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">{copy.resolution}</label>
                                        <div className={`grid ${GRID_COLS_CLASS[modelParams.resolution.options.length] ?? 'grid-cols-3'} gap-2`}>
                                            {modelParams.resolution.options.map(res => (
                                                <button
                                                    key={res}
                                                    onClick={() => updateParam("resolution", res)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all ${params.resolution === res
                                                        ? "bg-blue-500/20 border-blue-500 text-blue-500"
                                                        : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                        }`}
                                                >
                                                    {res}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {isSeedanceModel && modelParams.aspectRatio && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">{copy.aspectRatio}</label>
                                        <div className={`grid ${GRID_COLS_CLASS[modelParams.aspectRatio.options.length] ?? 'grid-cols-4'} gap-2`}>
                                            {modelParams.aspectRatio.options.map((ratio) => (
                                                <button
                                                    key={ratio}
                                                    onClick={() => updateParam("aspectRatio", ratio)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all ${params.aspectRatio === ratio
                                                        ? "bg-blue-500/20 border-blue-500 text-blue-500"
                                                        : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                        }`}
                                                >
                                                    {ratio === "adaptive" ? copy.aspectRatioOptions.adaptive : ratio}
                                                </button>
                                            ))}
                                        </div>
                                        <p className="text-[10px] text-gray-600 mt-1.5">
                                            {copy.aspectRatioHint}
                                        </p>
                                    </div>
                                )}

                                {isSeedanceModel && modelParams.seedanceReferenceMode && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">{copy.seedanceReferenceMode}</label>
                                        <div className="grid grid-cols-3 gap-2">
                                            {SEEDANCE_REFERENCE_MODE_OPTIONS.map((mode) => (
                                                <button
                                                    key={mode.value}
                                                    onClick={() => updateParam("seedanceReferenceMode", mode.value)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all ${params.seedanceReferenceMode === mode.value
                                                        ? "bg-blue-500/20 border-blue-500 text-blue-500"
                                                        : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                        }`}
                                                >
                                                    {mode.label}
                                                </button>
                                            ))}
                                        </div>
                                        <p className="text-[10px] text-gray-600 mt-1.5">{copy.seedanceReferenceModeHint}</p>
                                    </div>
                                )}

                                {/* Batch Size */}
                                <div>
                                    <label className="block text-xs text-gray-400 mb-2">{copy.batchSize}</label>
                                    <div className="grid grid-cols-3 gap-2">
                                        {[1, 2, 4].map(size => (
                                            <button
                                                key={size}
                                                onClick={() => updateParam("batchSize", size)}
                                                className={`py-1.5 text-xs rounded-lg border transition-all ${params.batchSize === size
                                                    ? "bg-blue-500/20 border-blue-500 text-blue-500"
                                                    : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                    }`}
                                            >
                                                {size}x
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {isSeedanceModel && (
                                    <div className="space-y-3 rounded-xl border border-blue-500/20 bg-blue-500/10 p-3">
                                        <div>
                                            <p className="text-xs font-medium text-white">{copy.seedanceSpecTitle}</p>
                                            <p className="mt-1 text-[11px] leading-relaxed text-blue-100/80">{copy.seedanceSpecHint}</p>
                                        </div>
                                        <div className="flex flex-wrap gap-2">
                                            <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-blue-50">{copy.seedanceSpecFps}</span>
                                            <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-blue-50">{copy.seedanceSpecResolution}</span>
                                            <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-blue-50">{copy.seedanceSpecReference}</span>
                                        </div>
                                        <button
                                            type="button"
                                            data-testid="seedance-low-cost-preset"
                                            onClick={handleApplySeedanceLowCostPreset}
                                            className="w-full rounded-lg border border-blue-400/30 bg-blue-400/10 px-3 py-2 text-xs font-medium text-blue-50 transition-colors hover:bg-blue-400/20"
                                        >
                                            {copy.seedanceLowCostPreset}
                                        </button>
                                        <p className="text-[11px] leading-relaxed text-blue-100/75">{copy.seedanceLowCostPresetHint}</p>
                                    </div>
                                )}

                                {/* Kling: CFG Scale */}
                                {modelParams.cfgScale && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">
                                            {copy.cfgScale} <span className="text-blue-500 font-medium">{params.cfgScale.toFixed(1)}</span>
                                        </label>
                                        <input
                                            type="range"
                                            min={modelParams.cfgScale.min}
                                            max={modelParams.cfgScale.max}
                                            step={modelParams.cfgScale.step}
                                            value={params.cfgScale}
                                            onChange={(e) => updateParam("cfgScale", parseFloat(e.target.value))}
                                            className="w-full h-1.5 bg-white/10 rounded-full appearance-none cursor-pointer accent-blue-500 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-500 [&::-webkit-slider-thumb]:shadow-lg"
                                        />
                                        <div className="flex justify-between text-[10px] text-gray-600 mt-1">
                                            <span>{modelParams.cfgScale.min}（{copy.cfgFree}）</span>
                                            <span>{modelParams.cfgScale.max}（{copy.cfgStrict}）</span>
                                        </div>
                                    </div>
                                )}

                                {/* Vidu: Movement Amplitude */}
                                {modelParams.movementAmplitude && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">{copy.movementAmplitude}</label>
                                        <div className={`grid ${GRID_COLS_CLASS[modelParams.movementAmplitude.options.length] ?? 'grid-cols-4'} gap-2`}>
                                            {modelParams.movementAmplitude.options.map(opt => (
                                                <button
                                                    key={opt}
                                                    onClick={() => updateParam("movementAmplitude", opt)}
                                                    className={`py-1.5 text-xs rounded-lg border transition-all capitalize ${params.movementAmplitude === opt
                                                        ? "bg-blue-500/20 border-blue-500 text-blue-500"
                                                        : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                        }`}
                                                >
                                                    {opt === 'auto'
                                                        ? copy.movementAmplitudeOptions.auto
                                                        : opt === 'small'
                                                            ? copy.movementAmplitudeOptions.small
                                                            : opt === 'medium'
                                                                ? copy.movementAmplitudeOptions.medium
                                                                : copy.movementAmplitudeOptions.large}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </section>

                            <div className="w-full h-px bg-white/5" />

                            {/* 3. Creative & Audio */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-purple-500 rounded-full" />
                                    {copy.sections.creativeAudio}
                                </h3>

                                {/* Prompt Enhancer - only when model supports it */}
                                {modelParams.promptExtend && (
                                    <div className="flex items-center justify-between">
                                        <label className="text-xs text-gray-400 flex items-center gap-2">
                                            <Wand2 size={12} />
                                            {copy.promptEnhancer}
                                        </label>
                                        <button
                                            onClick={() => updateParam("promptExtend", !params.promptExtend)}
                                            className={`w-10 h-5 rounded-full relative transition-colors ${params.promptExtend ? "bg-purple-500" : "bg-white/10"}`}
                                        >
                                            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${params.promptExtend ? "left-6" : "left-1"}`} />
                                        </button>
                                    </div>
                                )}

                                {/* Wan Audio Settings (三模式) - only when model supports it */}
                                {modelParams.audio && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">
                                            {copy.audioSettings}
                                        </label>
                                        <div className={`grid ${isSeedanceModel ? 'grid-cols-2' : 'grid-cols-3'} gap-2 mb-2`}>
                                            <button
                                                onClick={() => setAudioMode("mute")}
                                                className={`py-1.5 text-xs rounded-lg border flex items-center justify-center gap-1 transition-all ${audioMode === "mute"
                                                    ? "bg-purple-500/20 border-purple-500 text-purple-500"
                                                    : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                    }`}
                                            >
                                                <VolumeX size={12} /> {copy.mute}
                                            </button>
                                            <button
                                                onClick={() => setAudioMode("ai")}
                                                className={`py-1.5 text-xs rounded-lg border flex items-center justify-center gap-1 transition-all ${audioMode === "ai"
                                                    ? "bg-purple-500/20 border-purple-500 text-purple-500"
                                                    : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                    }`}
                                            >
                                                <Mic size={12} /> {copy.aiSound}
                                            </button>
                                            {!isSeedanceModel && (
                                                <button
                                                    onClick={() => setAudioMode("custom")}
                                                    className={`py-1.5 text-xs rounded-lg border flex items-center justify-center gap-1 transition-all ${audioMode === "custom"
                                                        ? "bg-purple-500/20 border-purple-500 text-purple-500"
                                                        : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                        }`}
                                                >
                                                    <Music size={12} /> {copy.soundDriven}
                                                </button>
                                            )}
                                        </div>
                                        {isSeedanceModel && (
                                            <p className="text-[10px] text-gray-500 mb-2">
                                                {copy.seedanceAudioHint}
                                            </p>
                                        )}
                                        {!isSeedanceModel && audioMode === "custom" && (
                                            <div className="relative">
                                                <input
                                                    type="text"
                                                    value={params.audioUrl || ""}
                                                    readOnly
                                                    placeholder={isUploadingAudio ? copy.uploadingAudio : copy.clickToUploadAudio}
                                                    onClick={() => audioInputRef.current?.click()}
                                                    className="w-full bg-white/5 border border-white/10 rounded-lg py-1.5 px-2 text-xs text-white focus:border-purple-500 focus:outline-none cursor-pointer"
                                                />
                                                {params.audioUrl && (
                                                    <button
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            updateParam("audioUrl", "");
                                                            setAudioMode("mute");
                                                        }}
                                                        className="absolute right-2 top-1.5 text-gray-500 hover:text-white"
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
                                        <label className="text-xs text-gray-400 flex items-center gap-2">
                                            <Mic size={12} />
                                            {copy.sound}
                                        </label>
                                        <button
                                            onClick={() => updateParam("sound", !params.sound)}
                                            className={`w-10 h-5 rounded-full relative transition-colors ${params.sound ? "bg-purple-500" : "bg-white/10"}`}
                                        >
                                            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${params.sound ? "left-6" : "left-1"}`} />
                                        </button>
                                    </div>
                                )}

                                {/* Vidu: Audio on/off */}
                                {modelParams.viduAudio && (
                                    <div className="flex items-center justify-between">
                                        <label className="text-xs text-gray-400 flex items-center gap-2">
                                            <Mic size={12} />
                                            {copy.audioOutput}
                                        </label>
                                        <button
                                            onClick={() => updateParam("viduAudio", !params.viduAudio)}
                                            className={`w-10 h-5 rounded-full relative transition-colors ${params.viduAudio ? "bg-purple-500" : "bg-white/10"}`}
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
                                            className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1 mb-2"
                                        >
                                            {showNegative ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
                                            {copy.negativePrompt}
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
                                                        placeholder={copy.negativePromptPlaceholder}
                                                        className="w-full h-20 bg-white/5 border border-white/10 rounded-lg p-2 text-xs text-white focus:border-purple-500 focus:outline-none resize-none"
                                                    />
                                                </motion.div>
                                            )}
                                        </AnimatePresence>
                                    </div>
                                )}
                            </section>

                            <div className="w-full h-px bg-white/5" />

                            {/* 4. Advanced / Effects */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-orange-500 rounded-full" />
                                    {copy.sections.advanced}
                                </h3>

                                {/* Seed - only when model supports it */}
                                {modelParams.seed && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">{copy.seed}</label>
                                        <div className="relative">
                                            <input
                                                type="number"
                                                value={params.seed ?? ""}
                                                onChange={(e) => updateParam("seed", e.target.value ? parseInt(e.target.value) : undefined)}
                                                placeholder={copy.randomPlaceholder}
                                                className="w-full bg-white/5 border border-white/10 rounded-lg py-1.5 pl-2 pr-8 text-xs text-white focus:border-orange-500 focus:outline-none [&::-webkit-inner-spin-button]:appearance-none"
                                            />
                                            <button
                                                onClick={() => updateParam("seed", Math.floor(Math.random() * 2147483647))}
                                                className="absolute right-2 top-1.5 text-gray-500 hover:text-white"
                                                title={copy.randomize}
                                            >
                                                <RefreshCw size={12} />
                                            </button>
                                        </div>
                                    </div>
                                )}

                                {isSeedanceModel && (
                                    <div className="flex items-center justify-between gap-3">
                                        <div>
                                            <label className="text-xs text-gray-400">{copy.seedancePreviewOnly}</label>
                                            <p className="text-[10px] text-gray-600 mt-1">{copy.seedancePreviewOnlyHint}</p>
                                        </div>
                                        <button
                                            onClick={() => updateParam("seedancePreviewOnly", !params.seedancePreviewOnly)}
                                            aria-label={copy.seedancePreviewOnly}
                                            aria-pressed={params.seedancePreviewOnly}
                                            className={`w-10 h-5 rounded-full relative transition-colors ${params.seedancePreviewOnly ? "bg-orange-500" : "bg-white/10"}`}
                                        >
                                            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${params.seedancePreviewOnly ? "left-6" : "left-1"}`} />
                                        </button>
                                    </div>
                                )}

                                {isSeedanceModel && modelParams.watermark && (
                                    <div className="flex items-center justify-between gap-3">
                                        <div>
                                            <label className="text-xs text-gray-400">{copy.watermark}</label>
                                            <p className="text-[10px] text-gray-600 mt-1">{copy.watermarkHint}</p>
                                        </div>
                                        <button
                                            onClick={() => updateParam("watermark", !params.watermark)}
                                            aria-label={copy.watermark}
                                            aria-pressed={params.watermark}
                                            className={`w-10 h-5 rounded-full relative transition-colors ${params.watermark ? "bg-orange-500" : "bg-white/10"}`}
                                        >
                                            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${params.watermark ? "left-6" : "left-1"}`} />
                                        </button>
                                    </div>
                                )}

                                {isSeedanceModel && modelParams.cameraFixed && (
                                    <div className="flex items-center justify-between gap-3">
                                        <div>
                                            <label className="text-xs text-gray-400">{copy.cameraFixed}</label>
                                            <p className="text-[10px] text-gray-600 mt-1">{copy.cameraFixedHint}</p>
                                        </div>
                                        <button
                                            onClick={() => updateParam("cameraFixed", !params.cameraFixed)}
                                            aria-label={copy.cameraFixed}
                                            aria-pressed={params.cameraFixed}
                                            className={`w-10 h-5 rounded-full relative transition-colors ${params.cameraFixed ? "bg-orange-500" : "bg-white/10"}`}
                                        >
                                            <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${params.cameraFixed ? "left-6" : "left-1"}`} />
                                        </button>
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
