"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
    Upload, X, Wand2, Plus, ChevronDown, ChevronUp, Loader2, Layout,
    Video,
    Eraser,
    Check,
    Image as ImageIcon,
    Users,
    Film
} from "lucide-react";





import { useProjectStore } from "@/store/projectStore";
import { api, API_URL, VideoTask } from "@/lib/api";
import { getAssetUrl, getAssetUrlWithTimestamp } from "@/lib/utils";
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
        generationMode: string;  // 'i2v' or 'r2v'
        referenceVideoUrls: string[];  // Reference videos for R2V
    };
    onParamsChange: (params: Partial<VideoCreatorProps['params']>) => void;
}

export default function VideoCreator({ onTaskCreated, remixData, onRemixClear, params, onParamsChange }: VideoCreatorProps) {
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
    const [selectedReferenceVideos, setSelectedReferenceVideos] = useState<string[]>([]); // New state for R2V
    const [uploadingPaths, setUploadingPaths] = useState<Record<string, string>>({}); // Map blobUrl -> serverUrl
    const [activeTab, setActiveTab] = useState<"storyboard" | "upload">("storyboard");

    // R2V Cast Slots: 3 slots for reference videos
    const [castSlots, setCastSlots] = useState<{ url: string; name: string }[]>([]);
    const [selectedFrameId, setSelectedFrameId] = useState<string | null>(null); // Selected frame for R2V
    const [generationMode, setGenerationMode] = useState<"i2v" | "r2v">("i2v"); // Local mode state

    // Sync from parent params
    useEffect(() => {
        if (params.generationMode) {
            setGenerationMode(params.generationMode as "i2v" | "r2v");
        }
    }, [params.generationMode]);

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
    const [polishedPrompt, setPolishedPrompt] = useState<{ cn: string; en: string } | null>(null);
    const [isPolishing, setIsPolishing] = useState(false);

    const handlePolish = async () => {
        if (!prompt) return;
        setIsPolishing(true);
        try {
            let res;
            if (generationMode === 'r2v') {
                // R2V mode: use R2V-specific polish with slot info
                const slotInfo = castSlots
                    .filter(slot => slot.url)
                    .map((slot) => ({
                        description: slot.name || 'Unknown character'
                    }));
                res = await api.polishR2VPrompt(prompt, slotInfo);
            } else {
                // I2V mode: use video polish
                res = await api.polishVideoPrompt(prompt);
            }
            if (res.prompt_cn && res.prompt_en) {
                setPolishedPrompt({ cn: res.prompt_cn, en: res.prompt_en });
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

    // R2V: Handle Reference Video Selection
    const handleReferenceVideoSelect = (videoUrl: string) => {
        if (selectedReferenceVideos.includes(videoUrl)) {
            setSelectedReferenceVideos(prev => prev.filter(v => v !== videoUrl));
        } else {
            if (selectedReferenceVideos.length >= 3) {
                alert("最多选择 3 个参考视频");
                return;
            }
            setSelectedReferenceVideos(prev => [...prev, videoUrl]);
        }
    };

    // R2V: Handle Cast Slot Selection
    const handleCastSlotSelect = (slotIndex: number, video: { url: string; name: string }) => {
        setCastSlots(prev => {
            const newSlots = [...prev];
            // Ensure array is long enough
            while (newSlots.length <= slotIndex) {
                newSlots.push({ url: '', name: '' });
            }
            newSlots[slotIndex] = video;
            return newSlots;
        });
    };

    // R2V: Clear Cast Slot
    const handleClearCastSlot = (slotIndex: number) => {
        setCastSlots(prev => {
            const newSlots = [...prev];
            if (newSlots[slotIndex]) {
                newSlots[slotIndex] = { url: '', name: '' };
            }
            return newSlots;
        });
    };

    // R2V: Handle Frame Selection (for description)
    const handleR2VFrameSelect = (frame: any) => {
        setSelectedFrameId(frame.id);
        // Auto-fill prompt with frame description
        let newPrompt = frame.action_description || frame.image_prompt || "";
        if (frame.dialogue) {
            newPrompt += ` Dialogue: ${frame.dialogue}`;
        }
        setSegments([{ type: "text", value: newPrompt, id: `frame-${frame.id}` }]);
    };

    // Insert character into prompt at cursor position
    const insertCharacter = (slotIndex: number) => {
        const slot = castSlots[slotIndex];
        if (!slot?.url) return;

        // Find video to get thumbnail
        const video = availableReferenceVideos.find(v => v.url === slot.url);
        const thumbnail = video?.thumbnail ? getAssetUrl(video.thumbnail) : undefined;

        promptBuilderRef.current?.insertCharacter(slotIndex, slot.name, thumbnail);
    };

    const handleSubmit = async () => {
        // Validation based on mode
        if (generationMode === 'i2v') {
            if (selectedImages.length === 0 || !prompt || !currentProject) return;
        } else {
            // R2V mode: need at least one cast slot filled
            const filledSlots = castSlots.filter(s => s.url);
            if (filledSlots.length === 0) {
                alert("R2V 模式请至少填充一个角色槽位 (@Ref_A)");
                return;
            }
            if (!prompt || !currentProject) return;
        }

        setIsSubmitting(true);
        try {
            // Add motion description to prompt
            const motionDesc = getMotionDescription();
            const finalPrompt = motionDesc ? `${prompt}, ${motionDesc}` : prompt;

            // Optimistic update - add pending tasks to queue immediately
            const optimisticTasks: VideoTask[] = [];

            // Determine items to process
            // In I2V: process selected images
            // In R2V: process selected images OR a single task if no image selected
            let itemsToProcess = selectedImages;
            if (generationMode === 'r2v' && selectedImages.length === 0) {
                itemsToProcess = [""]; // Dummy item to trigger one iteration
            }

            itemsToProcess.forEach((img, idx) => {
                let displayUrl = img;
                if (img && img.startsWith("blob:")) {
                    displayUrl = uploadingPaths[img] || img;
                } else if (img && !img.startsWith("http")) {
                    displayUrl = img;
                }

                // Determine model based on generation mode
                const actualModel = generationMode === 'r2v' ? 'wan2.6-r2v' : params.model;
                const referenceVideos = generationMode === 'r2v'
                    ? castSlots.filter(s => s.url).map(s => s.url)
                    : undefined;

                // Create batch_size tasks for each image
                for (let i = 0; i < params.batchSize; i++) {
                    optimisticTasks.push({
                        id: `temp-${Date.now()}-${idx}-${i}`,
                        project_id: currentProject.id,
                        image_url: displayUrl, // Might be empty string for R2V
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
                        model: actualModel,
                        created_at: Date.now() / 1000,
                        generation_mode: generationMode,
                        reference_video_urls: referenceVideos
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
            for (const img of itemsToProcess) {
                let finalImageUrl = img;
                if (img && img.startsWith("blob:")) {
                    if (uploadingPaths[img]) {
                        finalImageUrl = uploadingPaths[img];
                    } else {
                        console.warn("Image upload pending for", img);
                        continue;
                    }
                } else if (img && img.startsWith(`${API_URL}/files/`)) {
                    finalImageUrl = img.replace(`${API_URL}/files/`, "");
                }

                // Find frame ID - use selectedFrameId directly for R2V mode
                let frameId: string | undefined;
                if (generationMode === 'r2v') {
                    // R2V mode: use the explicitly selected frame
                    frameId = selectedFrameId || undefined;
                } else {
                    // I2V mode: find frame by matching image URL
                    const frame = currentProject?.frames?.find((f: any) => f.image_url === img || `${API_URL}/files/${f.image_url}` === img);
                    frameId = frame ? frame.id : undefined;
                }

                // Determine model based on generation mode
                // R2V mode uses wan2.6-r2v, I2V uses selected model
                const actualModel = generationMode === 'r2v' ? 'wan2.6-r2v' : params.model;

                // Get reference video URLs from cast slots for R2V
                const referenceVideos = generationMode === 'r2v'
                    ? castSlots.filter(s => s.url).map(s => s.url)
                    : [];

                await api.createVideoTask(
                    currentProject.id,
                    finalImageUrl, // Can be empty string
                    finalPrompt,
                    params.duration,
                    params.seed,
                    params.resolution,
                    params.generateAudio,
                    params.audioUrl,
                    params.promptExtend,
                    params.negativePrompt,
                    params.batchSize,
                    actualModel,  // Use computed model
                    frameId,
                    params.shotType,
                    generationMode,  // Use local state
                    referenceVideos  // Use cast slots
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
    }, [selectedImages, prompt, currentProject, params, selectedReferenceVideos]); // Added selectedReferenceVideos dependency

    // Available assets for drag/drop or selection
    const availableAssets = currentProject ? [
        ...currentProject.characters.map((c: any) => ({
            url: getAssetUrl(c.image_url),
            title: c.name
        })),
        ...currentProject.scenes.map((s: any) => ({
            url: getAssetUrl(s.image_url),
            title: s.name
        }))
    ].filter(a => a.url) : [];

    // Available Reference Videos (for R2V)
    const availableReferenceVideos = currentProject ? [
        // Character asset video variants (full_body and headshot)
        ...currentProject.characters.flatMap((c: any) => {
            const variants = [];
            // Full body video variants
            if (c.full_body?.video_variants?.length) {
                variants.push(...c.full_body.video_variants.map((v: any) => ({
                    url: v.url,
                    thumbnail: c.full_body?.selected_image_id
                        ? (c.full_body.image_variants?.find((img: any) => img.id === c.full_body.selected_image_id)?.url || c.full_body_image_url)
                        : c.full_body_image_url,
                    title: `${c.name} - Full Body Motion Reference`,
                    assetName: c.name,
                    type: 'character_full_body'
                })));
            }
            // Headshot video variants
            if (c.head_shot?.video_variants?.length) {
                variants.push(...c.head_shot.video_variants.map((v: any) => ({
                    url: v.url,
                    thumbnail: c.head_shot?.selected_image_id
                        ? (c.head_shot.image_variants?.find((img: any) => img.id === c.head_shot.selected_image_id)?.url || c.headshot_image_url)
                        : c.headshot_image_url,
                    title: `${c.name} - Headshot Motion Reference`,
                    assetName: c.name,
                    type: 'character_headshot'
                })));
            }
            return variants;
        }),
        // Character legacy video assets
        ...currentProject.characters.flatMap((c: any) =>
            (c.video_assets || []).map((v: any) => ({
                url: v.video_url,
                thumbnail: v.image_url,
                title: `${c.name} - Video`,
                assetName: c.name,
                type: 'character_legacy'
            }))
        ),
        // Scene video assets
        ...currentProject.scenes.flatMap((s: any) =>
            (s.video_assets || []).map((v: any) => ({
                url: v.video_url,
                thumbnail: v.image_url,
                title: `${s.name} - Video`,
                assetName: s.name,
                type: 'scene'
            }))
        ),
        // Prop video assets
        ...currentProject.props.flatMap((p: any) =>
            (p.video_assets || []).map((v: any) => ({
                url: v.video_url,
                thumbnail: v.image_url,
                title: `${p.name} - Video`,
                assetName: p.name,
                type: 'prop'
            }))
        )
    ].filter(v => v.url && v.url !== 'null' && v.url !== 'undefined') : [];

    return (
        <div className="h-full flex flex-col relative min-h-0">
            {/* Scrollable Content Area */}
            <div className="flex-1 overflow-y-auto p-8 custom-scrollbar min-h-0">
                <h2 className="text-2xl font-display font-bold text-white mb-6 flex items-center gap-3">
                    <div className="w-2 h-8 bg-primary rounded-full" />
                    动态演译
                    <span className="text-xs font-mono text-gray-500 bg-white/5 px-2 py-1 rounded">Motion</span>
                </h2>

                <div className="flex flex-col gap-6 max-w-4xl mx-auto w-full pb-8">
                    {/* Generation Mode Switcher */}
                    <div className="flex items-center justify-center">
                        <div className="flex bg-black/40 rounded-xl p-1.5 gap-1 border border-white/10">
                            <button
                                onClick={() => {
                                    setGenerationMode("i2v");
                                    onParamsChange({ generationMode: "i2v" });
                                }}
                                className={`px-5 py-2.5 text-sm rounded-lg flex items-center gap-2 transition-all font-medium ${generationMode === "i2v"
                                    ? "bg-primary text-white shadow-lg"
                                    : "text-gray-400 hover:text-white hover:bg-white/5"
                                    }`}
                            >
                                <ImageIcon size={16} />
                                🖼️ 首帧驱动 (I2V)
                            </button>
                            <button
                                onClick={() => {
                                    setGenerationMode("r2v");
                                    onParamsChange({
                                        generationMode: "r2v",
                                        model: "wan2.6-i2v" // Force Wan 2.6 when switching to R2V
                                    });
                                }}
                                className={`px-5 py-2.5 text-sm rounded-lg flex items-center gap-2 transition-all font-medium ${generationMode === "r2v"
                                    ? "bg-purple-600 text-white shadow-lg"
                                    : "text-gray-400 hover:text-white hover:bg-white/5"
                                    }`}
                            >
                                <Film size={16} />
                                🎬 角色驱动 (R2V)
                            </button>
                        </div>
                    </div>
                    {/* === I2V MODE: Source Selector === */}
                    {generationMode === 'i2v' && (
                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <label className="text-sm font-medium text-gray-300">首帧图片 (First Frame)</label>
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
                                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 max-h-[500px] overflow-y-auto custom-scrollbar pr-2 p-2">
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
                                                                src={getAssetUrlWithTimestamp(frame.image_url, frame.updated_at)}
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
                                                                    src={timestamp ? getAssetUrlWithTimestamp(img, timestamp) : getAssetUrl(img)}
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
                                                        src={getAssetUrl(img)}
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
                    )}

                    {/* === R2V MODE: Cast Slots + Frame Description === */}
                    {generationMode === 'r2v' && (
                        <div className="space-y-6">
                            {/* Frame Description Cards */}
                            <div className="space-y-3">
                                <label className="text-sm font-medium text-gray-300">选择分镜 (Select Frame)</label>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[200px] overflow-y-auto custom-scrollbar pr-2">
                                    {currentProject?.frames && currentProject.frames.length > 0 ? (
                                        currentProject.frames.map((frame: any) => (
                                            <div
                                                key={frame.id}
                                                onClick={() => handleR2VFrameSelect(frame)}
                                                className={`p-3 rounded-lg border cursor-pointer transition-all ${selectedFrameId === frame.id
                                                    ? "border-purple-500 bg-purple-500/10 ring-2 ring-purple-500/30"
                                                    : "border-white/10 bg-black/20 hover:border-white/30"
                                                    }`}
                                            >
                                                <div className="flex items-start gap-3">
                                                    {/* Frame thumbnail */}
                                                    <div className="w-16 h-10 rounded overflow-hidden flex-shrink-0 bg-black/40">
                                                        {frame.image_url ? (
                                                            <img
                                                                src={getAssetUrlWithTimestamp(frame.image_url, frame.updated_at)}
                                                                alt=""
                                                                className="w-full h-full object-cover"
                                                            />
                                                        ) : (
                                                            <div className="w-full h-full flex items-center justify-center text-gray-600">
                                                                <Layout size={14} />
                                                            </div>
                                                        )}
                                                    </div>
                                                    {/* Frame description */}
                                                    <div className="flex-1 min-w-0">
                                                        <p className="text-xs text-gray-400 mb-1">#{frame.id.slice(0, 6)}</p>
                                                        <p className="text-xs text-gray-300 line-clamp-2">
                                                            {frame.action_description || frame.image_prompt || '暂无描述'}
                                                        </p>
                                                        {frame.dialogue && (
                                                            <p className="text-[10px] text-purple-400 mt-1 italic line-clamp-1">
                                                                “{frame.dialogue}”
                                                            </p>
                                                        )}
                                                    </div>
                                                    {/* Selected indicator */}
                                                    {selectedFrameId === frame.id && (
                                                        <div className="w-5 h-5 rounded-full bg-purple-500 flex items-center justify-center flex-shrink-0">
                                                            <Check size={12} className="text-white" />
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        ))
                                    ) : (
                                        <div className="col-span-2 flex flex-col items-center justify-center h-[100px] text-gray-500 gap-2">
                                            <Layout size={24} className="opacity-20" />
                                            <p className="text-xs">无分镜数据，请先在 Storyboard 阶段生成分镜</p>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Cast Slots (卡司槽位) */}
                            <div className="space-y-3">
                                <label className="text-sm font-medium text-gray-300">卡司槽位 (Cast Slots)</label>
                                <div className="grid grid-cols-3 gap-4">
                                    {[0, 1, 2].map((slotIndex) => {
                                        const slot = castSlots[slotIndex];
                                        const slotLabel = `@Ref_${String.fromCharCode(65 + slotIndex)}`; // @Ref_A, @Ref_B, @Ref_C
                                        const slotTitle = slotIndex === 0 ? '主角' : '配角';
                                        const video = slot?.url ? availableReferenceVideos.find(v => v.url === slot.url) : null;

                                        return (
                                            <div
                                                key={slotIndex}
                                                className={`relative rounded-xl border-2 border-dashed transition-all ${slot?.url
                                                    ? "border-purple-500 bg-purple-500/10"
                                                    : "border-white/20 bg-black/20 hover:border-white/40"
                                                    }`}
                                            >
                                                {/* Slot Header */}
                                                <div className="absolute top-2 left-2 z-10">
                                                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-600 text-white font-bold">
                                                        角色{slotIndex + 1}
                                                    </span>
                                                </div>

                                                {slot?.url ? (
                                                    /* Filled Slot */
                                                    <div className="aspect-video relative">
                                                        <img
                                                            src={getAssetUrl(video?.thumbnail || '')}
                                                            alt={slot.name}
                                                            className="w-full h-full object-cover rounded-xl"
                                                        />
                                                        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-2 rounded-b-xl">
                                                            <p className="text-xs text-white font-medium truncate">{slot.name}</p>
                                                        </div>
                                                        <button
                                                            onClick={() => handleClearCastSlot(slotIndex)}
                                                            className="absolute top-2 right-2 p-1 bg-black/60 rounded-full text-white hover:bg-red-500 transition-colors"
                                                        >
                                                            <X size={12} />
                                                        </button>
                                                    </div>
                                                ) : (
                                                    /* Empty Slot */
                                                    <div className="aspect-video flex flex-col items-center justify-center p-4">
                                                        <p className="text-xs text-gray-400 mb-2">{slotTitle}</p>
                                                        <select
                                                            className="w-full text-xs bg-black/40 border border-white/20 rounded-lg px-2 py-1.5 text-gray-300 focus:border-purple-500 focus:outline-none"
                                                            value=""
                                                            onChange={(e) => {
                                                                const selectedVideo = availableReferenceVideos.find(v => v.url === e.target.value);
                                                                if (selectedVideo) {
                                                                    handleCastSlotSelect(slotIndex, { url: selectedVideo.url, name: selectedVideo.assetName });
                                                                }
                                                            }}
                                                        >
                                                            <option value="">选择参考视频...</option>
                                                            {availableReferenceVideos.map((v, i) => (
                                                                <option key={i} value={v.url}>{v.assetName} - {v.type}</option>
                                                            ))}
                                                        </select>
                                                        {slotIndex === 0 && (
                                                            <p className="text-[10px] text-amber-400 mt-2">必填</p>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                                {availableReferenceVideos.length === 0 && (
                                    <p className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
                                        ⚠️ 无可用的参考视频。请先在 Assets 阶段为角色/场景生成 Motion Reference 视频。
                                    </p>
                                )}
                            </div>
                        </div>
                    )}


                    {/* 2. Prompt Input */}
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium text-gray-300">提示词 (Prompt)</label>
                            <div className="flex items-center gap-2">
                                {generationMode === 'i2v' && (
                                    <div className="relative">
                                        <button
                                            onClick={() => promptBuilderRef.current?.insertCamera()}
                                            className="text-xs flex items-center gap-1 px-2 py-1 rounded transition-colors text-gray-400 hover:text-white hover:bg-white/5"
                                        >
                                            <Video size={12} /> 运镜
                                        </button>
                                    </div>
                                )}
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

                        {/* Character Insert Shortcuts (R2V Mode Only) */}
                        {generationMode === 'r2v' && (
                            <div className="flex gap-2 flex-wrap">
                                {[0, 1, 2].map((idx) => {
                                    const slot = castSlots[idx];
                                    const isActive = slot?.url;
                                    const video = isActive ? availableReferenceVideos.find(v => v.url === slot.url) : null;
                                    return (
                                        <button
                                            key={idx}
                                            onClick={() => insertCharacter(idx)}
                                            disabled={!isActive}
                                            className={`text-xs px-2 py-1 rounded-lg border transition-all flex items-center gap-1.5 ${isActive
                                                ? "border-purple-500/50 bg-purple-500/10 text-purple-400 hover:bg-purple-500/20"
                                                : "border-white/10 bg-white/5 text-gray-500 cursor-not-allowed"
                                                }`}
                                        >
                                            {video?.thumbnail ? (
                                                <img src={getAssetUrl(video.thumbnail)} alt="" className="w-4 h-4 rounded-full object-cover" />
                                            ) : (
                                                <span className="w-4 h-4 rounded-full bg-purple-500/30 flex items-center justify-center text-[10px]">+</span>
                                            )}
                                            <span>插入 {slot?.name || `角色${idx + 1}`}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        )}

                        <div className="relative">
                            <PromptBuilder
                                ref={promptBuilderRef}
                                segments={segments}
                                onChange={setSegments}
                                placeholder={generationMode === 'r2v'
                                    ? "输入提示词... \n插入角色格式: [character1:名称]\n插入运镜格式: (camera: 运镜指令)"
                                    : "输入提示词，描述画面内容...\n插入运镜格式: (camera: 运镜指令)"
                                }
                            />
                        </div>

                        {/* Polished Result Display - Bilingual */}
                        <AnimatePresence>
                            {polishedPrompt && (
                                <motion.div
                                    initial={{ opacity: 0, y: -10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={{ opacity: 0, y: -10 }}
                                    className="bg-purple-900/20 border border-purple-500/30 rounded-lg p-3 mt-2 space-y-3"
                                >
                                    <div className="flex justify-between items-start">
                                        <span className="text-xs font-bold text-purple-400 flex items-center gap-1">
                                            <Wand2 size={12} /> AI 双语润色
                                        </span>
                                        <button
                                            onClick={() => setPolishedPrompt(null)}
                                            className="text-[10px] text-gray-400 hover:text-white"
                                        >
                                            ✕
                                        </button>
                                    </div>

                                    {/* Chinese Prompt */}
                                    <div className="space-y-1">
                                        <div className="flex justify-between items-center">
                                            <span className="text-[10px] font-bold text-gray-500 uppercase">中文 (预览)</span>
                                            <button
                                                onClick={() => {
                                                    navigator.clipboard.writeText(polishedPrompt.cn);
                                                    alert("中文提示词已复制");
                                                }}
                                                className="text-[10px] text-gray-400 hover:text-white bg-black/20 px-2 py-0.5 rounded"
                                            >
                                                复制
                                            </button>
                                        </div>
                                        <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap bg-black/20 p-2 rounded">
                                            {polishedPrompt.cn}
                                        </p>
                                    </div>

                                    {/* English Prompt */}
                                    <div className="space-y-1">
                                        <div className="flex justify-between items-center">
                                            <span className="text-[10px] font-bold text-gray-500 uppercase">English (生成用)</span>
                                            <div className="flex gap-1">
                                                <button
                                                    onClick={() => {
                                                        navigator.clipboard.writeText(polishedPrompt.en);
                                                        alert("English prompt copied");
                                                    }}
                                                    className="text-[10px] text-gray-400 hover:text-white bg-black/20 px-2 py-0.5 rounded"
                                                >
                                                    Copy
                                                </button>
                                                <button
                                                    onClick={() => {
                                                        setSegments([{ type: "text", value: polishedPrompt.en, id: `polished-${Date.now()}` }]);
                                                        setPolishedPrompt(null);
                                                    }}
                                                    className="text-[10px] text-white bg-purple-600 hover:bg-purple-500 px-2 py-0.5 rounded font-bold"
                                                >
                                                    应用
                                                </button>
                                            </div>
                                        </div>
                                        <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap bg-black/20 p-2 rounded font-mono">
                                            {polishedPrompt.en}
                                        </p>
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </div>
                </div>
            </div >

            {/* 4. Fixed Action Bar */}
            < div className="p-6 border-t border-white/10 bg-black/40 backdrop-blur-md z-10" >
                <div className="max-w-4xl mx-auto w-full">
                    <button
                        onClick={handleSubmit}
                        disabled={(!prompt || isSubmitting) || (generationMode === 'i2v' && selectedImages.length === 0)}
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
            </div >
        </div >
    );
}
