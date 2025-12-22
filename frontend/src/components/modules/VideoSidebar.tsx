"use client";

import { useState, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Settings2, List, Info, RefreshCw, ChevronDown, ChevronUp, Mic, Music, VolumeX, Wand2 } from "lucide-react";
import VideoQueue from "./VideoQueue";
import { VideoTask, api } from "@/lib/api";
import { I2V_MODELS } from "@/store/projectStore";

interface VideoSidebarProps {
    tasks: VideoTask[];
    onRemix: (task: VideoTask) => void;
    // Generation Params
    params: {
        resolution: string;
        duration: number;
        seed: number | undefined;
        generateAudio: boolean;
        audioUrl: string;
        promptExtend: boolean;
        negativePrompt: string;
        batchSize: number;
        cameraMovement: string;
        subjectMotion: string;
        model: string;
        shotType: string;  // 'single' or 'multi' (only for wan2.6-i2v)
    };
    setParams: (params: any) => void;
}

export default function VideoSidebar({ tasks, onRemix, params, setParams }: VideoSidebarProps) {
    const [activeTab, setActiveTab] = useState<"settings" | "queue">("settings");
    const [isUploadingAudio, setIsUploadingAudio] = useState(false);
    const audioInputRef = useRef<HTMLInputElement>(null);
    const [showNegative, setShowNegative] = useState(false);

    const updateParam = (key: string, value: any) => {
        setParams({ ...params, [key]: value });
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

    const isAudioSupported = params.model === "wan2.5-i2v-preview" || params.model === "wan2.6-i2v";

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
                    Motion Params
                </button>
                <button
                    onClick={() => setActiveTab("queue")}
                    className={`flex-1 py-3 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${activeTab === "queue"
                        ? "text-white border-b-2 border-primary bg-white/5"
                        : "text-gray-500 hover:text-gray-300 hover:bg-white/5"
                        }`}
                >
                    <List size={16} />
                    Queue
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
                                    Basic Settings
                                </h3>

                                {/* Model Selection */}
                                <div>
                                    <label className="block text-xs text-gray-400 mb-2">Model (模型)</label>
                                    <div className="space-y-2">
                                        {I2V_MODELS.map((model) => (
                                            <button
                                                key={model.id}
                                                onClick={() => updateParam("model", model.id)}
                                                className={`w-full flex items-center justify-between p-2.5 rounded-lg border transition-all text-left ${params.model === model.id
                                                    ? 'border-primary/50 bg-primary/10'
                                                    : 'border-white/10 hover:border-white/20 bg-white/5'
                                                    }`}
                                            >
                                                <div>
                                                    <span className="text-xs font-medium text-white">{model.name}</span>
                                                    <p className="text-[10px] text-gray-500">{model.description}</p>
                                                </div>
                                                {params.model === model.id && (
                                                    <div className="w-2 h-2 bg-primary rounded-full" />
                                                )}
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Duration */}
                                <div>
                                    <label className="block text-xs text-gray-400 mb-2">Duration (生成时长)</label>
                                    <div className="grid grid-cols-2 gap-2">
                                        {[5, 10].map(dur => (
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

                                {/* Shot Type - Only for wan2.6-i2v when promptExtend is enabled */}
                                {params.model === 'wan2.6-i2v' && (
                                    <div>
                                        <label className="block text-xs text-gray-400 mb-2">
                                            Shot Type (镜头类型)
                                            {!params.promptExtend && (
                                                <span className="text-yellow-500 ml-2">(需开启智能扩写)</span>
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
                                                <span className="font-medium">Single</span>
                                                <span className="text-[10px] text-gray-500">单镜头</span>
                                            </button>
                                            <button
                                                onClick={() => updateParam("shotType", "multi")}
                                                disabled={!params.promptExtend}
                                                className={`py-2 text-xs rounded-lg border transition-all flex flex-col items-center gap-1 ${params.shotType === "multi"
                                                    ? "bg-primary/20 border-primary text-primary"
                                                    : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                    } ${!params.promptExtend ? 'opacity-50 cursor-not-allowed' : ''}`}
                                            >
                                                <span className="font-medium">Multi</span>
                                                <span className="text-[10px] text-gray-500">多镜头叙事</span>
                                            </button>
                                        </div>
                                        <p className="text-[10px] text-gray-600 mt-1.5">
                                            多镜头模式会生成包含多个切换镜头的叙事视频
                                        </p>
                                    </div>
                                )}
                            </section>

                            <div className="w-full h-px bg-white/5" />

                            {/* 2. Quality & Specs */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-blue-500 rounded-full" />
                                    Quality & Specs
                                </h3>

                                {/* Resolution */}
                                <div>
                                    <label className="block text-xs text-gray-400 mb-2">Resolution (画质)</label>
                                    <div className="grid grid-cols-3 gap-2">
                                        {["480p", "720p", "1080p"].map(res => (
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

                                {/* Batch Size */}
                                <div>
                                    <label className="block text-xs text-gray-400 mb-2">Batch Size (生成数量)</label>
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
                            </section>

                            <div className="w-full h-px bg-white/5" />

                            {/* 3. Creative & Audio */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-purple-500 rounded-full" />
                                    Creative & Audio
                                </h3>

                                {/* Prompt Enhancer */}
                                <div className="flex items-center justify-between">
                                    <label className="text-xs text-gray-400 flex items-center gap-2">
                                        <Wand2 size={12} />
                                        Prompt Enhancer (智能扩写)
                                    </label>
                                    <button
                                        onClick={() => updateParam("promptExtend", !params.promptExtend)}
                                        className={`w-10 h-5 rounded-full relative transition-colors ${params.promptExtend ? "bg-purple-500" : "bg-white/10"}`}
                                    >
                                        <div className={`absolute top-1 w-3 h-3 rounded-full bg-white transition-all ${params.promptExtend ? "left-6" : "left-1"}`} />
                                    </button>
                                </div>

                                {/* Audio Settings */}
                                <div className={!isAudioSupported ? "opacity-50 pointer-events-none" : ""}>
                                    <label className="block text-xs text-gray-400 mb-2 flex items-center justify-between">
                                        Audio Settings (音频)
                                        {!isAudioSupported && <span className="text-[10px] text-red-400">Only supported in Wan 2.5</span>}
                                    </label>
                                    <div className="grid grid-cols-3 gap-2 mb-2">
                                        <button
                                            onClick={() => setAudioMode("mute")}
                                            className={`py-1.5 text-xs rounded-lg border flex items-center justify-center gap-1 transition-all ${audioMode === "mute"
                                                ? "bg-purple-500/20 border-purple-500 text-purple-500"
                                                : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                }`}
                                        >
                                            <VolumeX size={12} /> Mute
                                        </button>
                                        <button
                                            onClick={() => setAudioMode("ai")}
                                            className={`py-1.5 text-xs rounded-lg border flex items-center justify-center gap-1 transition-all ${audioMode === "ai"
                                                ? "bg-purple-500/20 border-purple-500 text-purple-500"
                                                : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
                                                }`}
                                        >
                                            <Mic size={12} /> AI Sound
                                        </button>
                                        <button
                                            onClick={() => setAudioMode("custom")}
                                            className={`py-1.5 text-xs rounded-lg border flex items-center justify-center gap-1 transition-all ${audioMode === "custom"
                                                ? "bg-purple-500/20 border-purple-500 text-purple-500"
                                                : "bg-white/5 border-transparent text-gray-400 hover:bg-white/10"
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

                                {/* Negative Prompt */}
                                <div>
                                    <button
                                        onClick={() => setShowNegative(!showNegative)}
                                        className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1 mb-2"
                                    >
                                        {showNegative ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
                                        Negative Prompt (负向提示词)
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
                                                    className="w-full h-20 bg-white/5 border border-white/10 rounded-lg p-2 text-xs text-white focus:border-purple-500 focus:outline-none resize-none"
                                                />
                                            </motion.div>
                                        )}
                                    </AnimatePresence>
                                </div>
                            </section>

                            <div className="w-full h-px bg-white/5" />

                            {/* 4. Advanced / Effects */}
                            <section className="space-y-4">
                                <h3 className="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center gap-2">
                                    <div className="w-1 h-3 bg-orange-500 rounded-full" />
                                    Advanced
                                </h3>

                                {/* Seed */}
                                <div>
                                    <label className="block text-xs text-gray-400 mb-2">Seed (随机种子)</label>
                                    <div className="relative">
                                        <input
                                            type="number"
                                            value={params.seed ?? ""}
                                            onChange={(e) => updateParam("seed", e.target.value ? parseInt(e.target.value) : undefined)}
                                            placeholder="Random (-1)"
                                            className="w-full bg-white/5 border border-white/10 rounded-lg py-1.5 pl-2 pr-8 text-xs text-white focus:border-orange-500 focus:outline-none [&::-webkit-inner-spin-button]:appearance-none"
                                        />
                                        <button
                                            onClick={() => updateParam("seed", Math.floor(Math.random() * 2147483647))}
                                            className="absolute right-2 top-1.5 text-gray-500 hover:text-white"
                                            title="Randomize"
                                        >
                                            <RefreshCw size={12} />
                                        </button>
                                    </div>
                                </div>


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
