"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
    Upload, X, Wand2, Plus, ChevronDown, ChevronUp, Loader2, Layout,
    Video,
    Eraser
} from "lucide-react";

import { useProjectStore } from "@/store/projectStore";
import { api, API_URL, VideoTask } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";
import PromptBuilder, { PromptSegment, PromptBuilderRef } from "./PromptBuilder";

interface VideoCreatorProps {
    onTaskCreated: (project: any) => void;
    remixData: Partial<VideoTask> | null;
    onRemixClear: () => void;
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
}

export default function VideoCreator({ onTaskCreated, remixData, onRemixClear, params }: VideoCreatorProps) {
    const currentProject = useProjectStore((state) => state.currentProject);

    // Helper function to generate motion description text
    const getMotionDescription = () => {
        const parts: string[] = [];

        if (params.cameraMovement && params.cameraMovement !== 'none') {
            const cameraDescriptions: Record<string, string> = {
                'pan_left_slow': 'camera slowly pans to the left',
                'pan_right_slow': 'camera slowly pans to the right',
                'pan_left_fast': 'camera quickly pans to the left',
                'pan_right_fast': 'camera quickly pans to the right',
                'tilt_up': 'camera tilts up',
                'tilt_down': 'camera tilts down',
                'zoom_in_slow': 'camera slowly zooms in',
                'zoom_out_slow': 'camera slowly zooms out',
                'zoom_in_fast': 'camera dramatically zooms in',
                'zoom_out_fast': 'camera dramatically zooms out',
                'dolly_in': 'camera dolly in',
                'dolly_out': 'camera dolly out',
                'orbit_left': 'camera orbits to the left',
                'orbit_right': 'camera orbits to the right',
                'crane_up': 'camera cranes up',
                'crane_down': 'camera cranes down'
            };
            parts.push(cameraDescriptions[params.cameraMovement] || '');
        }

        if (params.subjectMotion && params.subjectMotion !== 'still') {
            const subjectDescriptions: Record<string, string> = {
                'subtle': 'subtle movement',
                'natural': 'natural movement',
                'dynamic': 'dynamic action',
                'fast': 'fast-paced action'
            };
            parts.push(subjectDescriptions[params.subjectMotion] || '');
        }

        return parts.filter(p => p).join(', ');
    };

    const [selectedImages, setSelectedImages] = useState<string[]>([]);
    const [uploadingPaths, setUploadingPaths] = useState<Record<string, string>>({}); // Map blobUrl -> serverUrl
    const [activeTab, setActiveTab] = useState<"storyboard" | "upload">("storyboard");

    const handleFrameSelect = (frame: any) => {
        if (!frame.image_url) return;
        const url = frame.image_url;

        // If already selected, deselect
        if (selectedImages.includes(url)) {
            setSelectedImages([]);
            return;
        }

        // Select new image (replace existing)
        setSelectedImages([url]);

        // Auto-fill prompt (Replace existing prompt)
        let newPrompt = frame.image_prompt || frame.action_description || "";
        if (frame.dialogue) {
            newPrompt += ` . Dialogue: ${frame.dialogue}`;
        }
        setSegments([{ type: "text", value: newPrompt, id: "init" }]);
    };
    const [segments, setSegments] = useState<PromptSegment[]>([{ type: "text", value: "", id: "init" }]);
    const promptBuilderRef = useRef<PromptBuilderRef>(null);

    // Computed prompt for API
    const prompt = segments.map(s => s.value).join(" ");

    // negativePrompt moved to params
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [submitSuccess, setSubmitSuccess] = useState(false);
    const [showCameraDropdown, setShowCameraDropdown] = useState(false);
    const [polishedPrompt, setPolishedPrompt] = useState<string | null>(null);
    const [isPolishing, setIsPolishing] = useState(false);

    const handlePolish = async () => {
        if (!prompt) return;
        setIsPolishing(true);
        try {
            const res = await api.polishVideoPrompt(prompt);
            if (res.polished_prompt) {
                setPolishedPrompt(res.polished_prompt);
            }
        } catch (error) {
            console.error("Polish failed", error);
            alert("AI 润色失败");
        } finally {
            setIsPolishing(false);
        }
    };


    // Handle Remix Data
    useEffect(() => {
        if (remixData) {
            if (remixData.image_url) setSelectedImages([remixData.image_url]);
            if (remixData.prompt) setSegments([{ type: "text", value: remixData.prompt, id: "remix" }]);
            // negativePrompt handled by parent

            // Clear remix data after applying to avoid re-applying on every render
            onRemixClear();
        }
    }, [remixData, onRemixClear]);

    const handleImageSelect = (files: FileList | null) => {
        if (!files) return;

        const newImages: string[] = [];

        Array.from(files).forEach(async (file) => {
            const blobUrl = URL.createObjectURL(file);
            newImages.push(blobUrl);

            // Background Upload
            try {
                const res = await api.uploadFile(file);
                setUploadingPaths(prev => ({ ...prev, [blobUrl]: res.url }));
            } catch (error) {
                console.error("Upload failed", error);
                // Could remove from selectedImages or show error state on the specific image
            }
        });

        setSelectedImages(prev => [...prev, ...newImages]);
    };

    const handleAssetSelect = (url: string) => {
        if (!selectedImages.includes(url)) {
            setSelectedImages(prev => [...prev, url]);
        }
    };

    const removeImage = (index: number) => {
        setSelectedImages(prev => prev.filter((_, i) => i !== index));
    };

    const handleSubmit = async () => {
        if (selectedImages.length === 0 || !prompt || !currentProject) return;

        setIsSubmitting(true);
        try {
            // Add motion description to prompt
            const motionDesc = getMotionDescription();
            const finalPrompt = motionDesc ? `${prompt}, ${motionDesc}` : prompt;

            // Optimistic update - add pending tasks to queue immediately
            const optimisticTasks: VideoTask[] = [];

            selectedImages.forEach((img, idx) => {
                let displayUrl = img;
                if (img.startsWith("blob:")) {
                    displayUrl = uploadingPaths[img] || img;
                } else if (!img.startsWith("http")) {
                    displayUrl = img;
                }

                // Create batch_size tasks for each image
                for (let i = 0; i < params.batchSize; i++) {
                    optimisticTasks.push({
                        id: `temp-${Date.now()}-${idx}-${i}`,
                        project_id: currentProject.id,
                        image_url: displayUrl,
                        prompt: finalPrompt,
                        status: "pending",
                        video_url: undefined,
                        duration: params.duration,
                        seed: params.seed,
                        resolution: params.resolution,
                        generate_audio: params.generateAudio,
                        audio_url: params.audioUrl,
                        prompt_extend: params.promptExtend,
                        negative_prompt: params.negativePrompt,
                        model: params.model,
                        created_at: Date.now() / 1000
                    });
                }
            });

            // Immediately update UI with optimistic tasks
            const optimisticProject = {
                ...currentProject,
                video_tasks: [...(currentProject.video_tasks || []), ...optimisticTasks]
            };
            onTaskCreated(optimisticProject);

            // Batch submit for all images
            for (const img of selectedImages) {
                let finalImageUrl = img;
                if (img.startsWith("blob:")) {
                    if (uploadingPaths[img]) {
                        finalImageUrl = uploadingPaths[img];
                    } else {
                        console.warn("Image upload pending for", img);
                        continue;
                    }
                } else if (img.startsWith(`${API_URL}/files/`)) {
                    finalImageUrl = img.replace(`${API_URL}/files/`, "");
                }

                // Find frame ID
                const frame = currentProject?.frames?.find((f: any) => f.image_url === img || `${API_URL}/files/${f.image_url}` === img);
                const frameId = frame ? frame.id : undefined;

                await api.createVideoTask(
                    currentProject.id,
                    finalImageUrl,
                    finalPrompt,
                    params.duration,
                    params.seed,
                    params.resolution,
                    params.generateAudio,
                    params.audioUrl,
                    params.promptExtend,
                    params.negativePrompt,
                    params.batchSize,
                    params.model,
                    frameId,
                    params.shotType  // Pass shotType for wan2.6-i2v
                );
            }

            // Refresh with actual data from server
            const updatedProject = await api.getProject(currentProject.id);
            onTaskCreated(updatedProject);

            // Success feedback
            setSubmitSuccess(true);
            setTimeout(() => setSubmitSuccess(false), 1500);

            // Clear selection after successful submit
            // setSelectedImages([]); // Keep selection for iterative generation
        } catch (error) {
            console.error("Failed to submit task:", error);
            alert("提交失败");
            // Refresh to remove optimistic updates
            const updatedProject = await api.getProject(currentProject.id);
            onTaskCreated(updatedProject);
        } finally {
            setIsSubmitting(false);
        }
    };

    // Keyboard shortcut
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.ctrlKey && e.key === "Enter") {
                handleSubmit();
            }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [selectedImages, prompt, currentProject, params]); // Added params dependency

    // Available assets for drag/drop or selection
    const availableAssets = currentProject ? [
        ...currentProject.characters.map((c: any) => ({
            url: c.image_url ? `${API_URL}/files/${c.image_url}` : "",
            title: c.name
        })),
        ...currentProject.scenes.map((s: any) => ({
            url: s.image_url ? `${API_URL}/files/${s.image_url}` : "",
            title: s.name
        }))
    ].filter(a => a.url) : [];

    return (
        <div className="h-full flex flex-col relative min-h-0">
            {/* Scrollable Content Area */}
            <div className="flex-1 overflow-y-auto p-8 custom-scrollbar min-h-0">
                <h2 className="text-2xl font-display font-bold text-white mb-6 flex items-center gap-3">
                    <div className="w-2 h-8 bg-primary rounded-full" />
                    图生视频生成器
                    <span className="text-xs font-mono text-gray-500 bg-white/5 px-2 py-1 rounded">Img2Vid</span>
                </h2>

                <div className="flex flex-col gap-6 max-w-4xl mx-auto w-full pb-8">
                    {/* 1. Input Image Area */}
                    {/* 1. Source Selector */}
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <label className="text-sm font-medium text-gray-300">视频源 (Source)</label>
                            <div className="flex bg-white/5 rounded-lg p-1 gap-1">
                                <button
                                    onClick={() => setActiveTab("storyboard")}
                                    className={`px-3 py-1.5 text-xs rounded-md flex items-center gap-2 transition-all ${activeTab === "storyboard"
                                        ? "bg-primary text-white shadow-sm"
                                        : "text-gray-400 hover:text-white hover:bg-white/5"
                                        }`}
                                >
                                    <Layout size={14} /> Storyboard
                                </button>
                                <button
                                    onClick={() => setActiveTab("upload")}
                                    className={`px-3 py-1.5 text-xs rounded-md flex items-center gap-2 transition-all ${activeTab === "upload"
                                        ? "bg-primary text-white shadow-sm"
                                        : "text-gray-400 hover:text-white hover:bg-white/5"
                                        }`}
                                >
                                    <Upload size={14} /> Upload
                                </button>
                            </div>
                        </div>

                        {/* Tab Content */}
                        <div className="bg-black/20 border border-white/10 rounded-xl p-4 min-h-[200px]">
                            {activeTab === "storyboard" ? (
                                <div className="space-y-4">
                                    {currentProject?.frames && currentProject.frames.length > 0 ? (
                                        <div className="grid grid-cols-3 sm:grid-cols-4 gap-3 max-h-[300px] overflow-y-auto custom-scrollbar pr-2">
                                            {currentProject.frames.map((frame: any) => (
                                                <div
                                                    key={frame.id}
                                                    onClick={() => handleFrameSelect(frame)}
                                                    className={`group relative aspect-video rounded-lg overflow-hidden border cursor-pointer transition-all ${selectedImages.includes(frame.image_url)
                                                        ? "border-primary ring-2 ring-primary/50"
                                                        : "border-white/10 hover:border-white/30"
                                                        }`}
                                                >
                                                    {frame.image_url ? (
                                                        <img
                                                            src={getAssetUrl(frame.image_url) + `?t=${frame.updated_at || 0}`}
                                                            alt={`Frame ${frame.id}`}
                                                            className="w-full h-full object-cover"
                                                        />
                                                    ) : (
                                                        <div className="w-full h-full bg-white/5 flex items-center justify-center text-xs text-gray-500">
                                                            No Image
                                                        </div>
                                                    )}
                                                    <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                                                        <span className="text-xs text-white font-bold">Select</span>
                                                    </div>
                                                    {/* Frame Number Badge */}
                                                    <div className="absolute top-1 left-1 bg-black/60 px-1.5 rounded text-[10px] text-gray-300 backdrop-blur-sm">
                                                        #{frame.id.slice(0, 4)}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="flex flex-col items-center justify-center h-[200px] text-gray-500 gap-2">
                                            <Layout size={32} className="opacity-20" />
                                            <p className="text-xs">No storyboard frames found.</p>
                                        </div>
                                    )}

                                    {/* Selected Preview (Storyboard Mode) */}
                                    {selectedImages.length > 0 && (
                                        <div className="pt-4 border-t border-white/10">
                                            <p className="text-xs text-gray-500 mb-2">Selected for Generation:</p>
                                            <div className="flex gap-2 flex-wrap">
                                                {selectedImages.map((img, idx) => {
                                                    // Find frame to get updated_at for cache busting
                                                    const frame = currentProject?.frames?.find((f: any) => f.image_url === img);
                                                    const timestamp = frame?.updated_at || 0;
                                                    return (
                                                        <div key={idx} className="relative w-24 aspect-video rounded-lg overflow-hidden border border-white/20">
                                                            <img
                                                                src={getAssetUrl(img) + (timestamp ? `?t=${timestamp}` : "")}
                                                                alt="Selected"
                                                                className="w-full h-full object-cover"
                                                            />
                                                            <button
                                                                onClick={() => removeImage(idx)}
                                                                className="absolute top-1 right-1 p-0.5 bg-black/60 rounded-full text-white hover:bg-red-500"
                                                            >
                                                                <X size={10} />
                                                            </button>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                /* Upload Mode Content */
                                <div className="space-y-4">
                                    <div className="grid grid-cols-3 gap-4">
                                        {selectedImages.map((img, idx) => (
                                            <div key={idx} className="relative aspect-video bg-black/40 rounded-xl overflow-hidden border border-white/10 group">
                                                <img
                                                    src={img.startsWith("blob:") || img.startsWith("http") ? img : `${API_URL}/files/${img}`}
                                                    alt={`Input ${idx}`}
                                                    className="w-full h-full object-contain"
                                                />
                                                <button
                                                    onClick={() => removeImage(idx)}
                                                    className="absolute top-2 right-2 p-1 bg-black/60 rounded-full text-white opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500"
                                                >
                                                    <X size={12} />
                                                </button>
                                                {img.startsWith("blob:") && !uploadingPaths[img] && (
                                                    <div className="absolute inset-0 flex items-center justify-center bg-black/50">
                                                        <Loader2 className="animate-spin text-white" size={20} />
                                                    </div>
                                                )}
                                            </div>
                                        ))}

                                        {/* Add Button */}
                                        <div
                                            onClick={() => document.getElementById('image-upload')?.click()}
                                            className="aspect-video border-2 border-dashed border-white/10 rounded-xl flex flex-col items-center justify-center bg-white/5 hover:bg-white/10 transition-colors cursor-pointer relative min-h-[100px]"
                                        >
                                            <input
                                                id="image-upload"
                                                type="file"
                                                accept="image/*"
                                                multiple
                                                className="hidden"
                                                onChange={(e) => handleImageSelect(e.target.files)}
                                            />
                                            <Plus className="text-gray-400 mb-2" size={24} />
                                            <p className="text-gray-400 text-xs font-medium">Add Image</p>
                                        </div>
                                    </div>

                                    {/* Quick Select from Assets (Only in Upload Mode) */}
                                    {availableAssets.length > 0 && (
                                        <div className="mt-4 pt-4 border-t border-white/10">
                                            <p className="text-xs text-gray-500 mb-2">Quick Select from Assets:</p>
                                            <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-hide">
                                                {availableAssets.slice(0, 10).map((asset, i) => (
                                                    <div
                                                        key={i}
                                                        onClick={() => handleAssetSelect(asset.url)}
                                                        className="w-16 h-16 relative rounded-lg overflow-hidden flex-shrink-0 border border-white/10 hover:border-primary cursor-pointer"
                                                    >
                                                        <img src={asset.url} alt={asset.title} className="w-full h-full object-cover" />
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>

                    {/* 2. Prompt Input */}
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium text-gray-300">提示词 (Prompt)</label>
                            <div className="flex items-center gap-2">
                                <div className="relative">
                                    <button
                                        onClick={() => promptBuilderRef.current?.insertCamera()}
                                        className="text-xs flex items-center gap-1 px-2 py-1 rounded transition-colors text-gray-400 hover:text-white hover:bg-white/5"
                                    >
                                        <Video size={12} /> 运镜
                                    </button>
                                </div>
                                <button
                                    onClick={handlePolish}
                                    disabled={isPolishing || !prompt}
                                    className="text-xs text-primary hover:text-primary/80 flex items-center gap-1 disabled:opacity-50"
                                >
                                    {isPolishing ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
                                    AI 润色
                                </button>
                                <button
                                    onClick={() => setSegments([{ type: "text", value: "", id: "init" }])}
                                    className="text-xs text-gray-400 hover:text-white flex items-center gap-1 px-2 py-1 rounded hover:bg-white/5 transition-colors"
                                    title="Clear Prompt"
                                >
                                    <Eraser size={12} /> 清空
                                </button>
                            </div>
                        </div>
                        <div className="relative">
                            <PromptBuilder
                                ref={promptBuilderRef}
                                segments={segments}
                                onChange={setSegments}
                            />
                        </div>

                        {/* Polished Result Display */}
                        <AnimatePresence>
                            {polishedPrompt && (
                                <motion.div
                                    initial={{ opacity: 0, y: -10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={{ opacity: 0, y: -10 }}
                                    className="bg-purple-900/20 border border-purple-500/30 rounded-lg p-3 mt-2"
                                >
                                    <div className="flex justify-between items-start mb-2">
                                        <span className="text-xs font-bold text-purple-400 flex items-center gap-1">
                                            <Wand2 size={12} /> AI 建议 (AI Suggestion)
                                        </span>
                                        <div className="flex gap-2">
                                            <button
                                                onClick={() => {
                                                    navigator.clipboard.writeText(polishedPrompt);
                                                    alert("已复制 (Copied)");
                                                }}
                                                className="text-[10px] text-gray-400 hover:text-white bg-black/20 px-2 py-1 rounded"
                                            >
                                                复制 (Copy)
                                            </button>
                                            <button
                                                onClick={() => {
                                                    setSegments([{ type: "text", value: polishedPrompt, id: `polished-${Date.now()}` }]);
                                                    setPolishedPrompt(null); // Clear after applying
                                                }}
                                                className="text-[10px] text-white bg-purple-600 hover:bg-purple-500 px-2 py-1 rounded font-bold"
                                            >
                                                应用 (Apply)
                                            </button>
                                        </div>
                                    </div>
                                    <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap">
                                        {polishedPrompt}
                                    </p>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </div>
                </div>
            </div>

            {/* 4. Fixed Action Bar */}
            <div className="p-6 border-t border-white/10 bg-black/40 backdrop-blur-md z-10">
                <div className="max-w-4xl mx-auto w-full">
                    <button
                        onClick={handleSubmit}
                        disabled={selectedImages.length === 0 || !prompt || isSubmitting}
                        className={`w-full py-4 rounded-xl font-bold text-lg flex items-center justify-center gap-2 transition-all transform active:scale-[0.99] ${submitSuccess
                            ? "bg-green-500 text-white"
                            : "bg-primary hover:bg-primary/90 text-white"
                            } disabled:opacity-50 disabled:cursor-not-allowed`}
                    >
                        {isSubmitting ? (
                            <>
                                <Loader2 className="animate-spin" /> 提交中...
                            </>
                        ) : submitSuccess ? (
                            <>
                                <Plus /> 已加入队列
                            </>
                        ) : (
                            <>
                                <Plus /> 加入生成队列 (Ctrl+Enter)
                            </>
                        )}
                    </button>
                    <div className="flex justify-center mt-3">
                        <label className="flex items-center gap-2 text-xs text-gray-500 cursor-pointer hover:text-gray-400">
                            <input type="checkbox" className="rounded bg-white/10 border-white/20" />
                            提交后清空内容
                        </label>
                    </div>
                </div>
            </div>
        </div>
    );
}
