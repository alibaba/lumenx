"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useTranslations } from "next-intl";
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
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { ProjectReadbackError, readProjectOnce } from "@/lib/projectReadback";
import { frameCommandSnapshot } from "@/lib/frameCommandSnapshot";
import { R2V_SELECTION_MODEL_ID, getR2vRouteModelId, isR2vImageBased } from "@/lib/modelCatalog";
import { isOrderedWan3VideoModelId } from "@/lib/legacyVideoSidebarModels";
import { getAssetUrl, getAssetUrlWithTimestamp } from "@/lib/utils";
import PromptBuilder, { PromptSegment, PromptBuilderRef } from "./PromptBuilder";
import type { VideoParams } from "@/store/projectStore";

const videoCreatorOperationKey = (projectId: string, name: string) => `video-creator:${encodeURIComponent(requireActiveRuntimeEnvironment().key)}:${encodeURIComponent(projectId)}:${name}`;

interface VideoCreatorProps {
    onTaskCreated: (project: any) => void;
    remixData: Partial<VideoTask> | null;
    onRemixClear: () => void;
    params: VideoParams;
    onParamsChange: (params: Partial<VideoParams>) => void;
}

export default function VideoCreator({ onTaskCreated, remixData, onRemixClear, params, onParamsChange }: VideoCreatorProps) {
    const tc = useTranslations("creator");
    const tProject = useTranslations("project");
    const tFrameCommand = useTranslations("frameCommands");
    const currentProject = useProjectStore((state) => state.currentProject);
    const sessionMounted = useRef(true);
    const projectSessionRef = useRef(1);
    const submittedTaskIdsRef = useRef<string[]>([]);
    const retryReadRef = useRef(false);
    const submitInFlightRef = useRef<{ projectId: string; token: number } | null>(null);
    const submitOperation = currentProject?.id ? videoCreatorOperationKey(currentProject.id, "submit") : null;
    const submitBusy = useProjectStore((state) => submitOperation ? !!state.runningOps[submitOperation] : false);
    useEffect(() => {
        sessionMounted.current = true;
        return () => { sessionMounted.current = false; };
    }, []);
    const isProjectSessionCurrent = useCallback((expectedProjectId: string, token: number) => {
        const environment = getRuntimeEnvironment();
        const current = useProjectStore.getState().currentProject;
        return !!environment && sessionMounted.current && projectSessionRef.current === token && current?.id === expectedProjectId
            && current.readEnvironment === environment.key && isCurrentRuntimeEnvironment(environment.key, environment.generation);
    }, []);
    const currentProjectReadGuard = useCallback((expectedProjectId: string, token: number) => {
        const environment = getRuntimeEnvironment();
        return (snapshot?: { video_tasks?: { id: string }[] }) => {
            const current = useProjectStore.getState().currentProject;
            return !!environment && sessionMounted.current && projectSessionRef.current === token
                && current?.id === expectedProjectId && current.readEnvironment === environment.key
                && isCurrentRuntimeEnvironment(environment.key, environment.generation)
                && (!snapshot || submittedTaskIdsRef.current.every(id => snapshot.video_tasks?.some(task => task.id === id)));
        };
    }, []);

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
    const [extractingFrameId, setExtractingFrameId] = useState<string | null>(null);
    const [readbackMessage, setReadbackMessage] = useState<string | null>(null);
    const [readbackFloor, setReadbackFloor] = useState<number | undefined>(undefined);
    const [isRetryingReadback, setIsRetryingReadback] = useState(false);
    const hasPendingUpload = selectedImages.some(img => img.startsWith("blob:") && !uploadingPaths[img]);


    // Sync from parent params
    useEffect(() => {
        if (params.generationMode) {
            setGenerationMode(params.generationMode as "i2v" | "r2v");
        }
    }, [params.generationMode]);

    const handleExtractLastFrame = async (frameId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (readbackMessage || !currentProject?.frames || currentProject.readEnvironment !== getRuntimeEnvironment()?.key) return;

        const frameIndex = currentProject.frames.findIndex((f: any) => f.id === frameId);
        if (frameIndex <= 0) return;

        const prevFrame = currentProject.frames[frameIndex - 1];
        if (!prevFrame.selected_video_id) return;

        const prevVideo = currentProject.video_tasks?.find(
            (t: any) => t.id === prevFrame.selected_video_id && t.status === "completed"
        );
        if (!prevVideo) return;

        const projectId = currentProject.id;
        const opToken = projectSessionRef.current;
        setExtractingFrameId(frameId);
        setReadbackMessage(null);
        setReadbackFloor(undefined);
        let committedRevision: number | undefined;
        try {
            const response = await api.extractLastFrame(projectId, frameId, prevVideo.id);
            if (!isProjectSessionCurrent(projectId, opToken)) return;
            const parsed = frameCommandSnapshot(projectId, response);
            committedRevision = parsed.revision;
            await readProjectOnce(projectId, parsed.revision, { isCurrent: currentProjectReadGuard(projectId, opToken) });
        } catch (error: any) {
            console.error("Failed to extract last frame:", error);
            if (!isProjectSessionCurrent(projectId, opToken)) return;
            if (error instanceof ProjectReadbackError) {
                setReadbackMessage(tFrameCommand("savedReadFailed"));
                setReadbackFloor(committedRevision);
            } else {
                alert(error?.response?.data?.detail || tFrameCommand("unconfirmed"));
            }
        } finally {
            if (isProjectSessionCurrent(projectId, opToken)) setExtractingFrameId(null);
        }
    };

    const handleFrameSelect = (frame: any) => {
        // Prefer rendered_image_url (from extracted last frame / uploaded image), fallback to image_url
        const url = frame.rendered_image_url || frame.image_url;
        if (!url) return;

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

    useEffect(() => {
        projectSessionRef.current += 1;
        submitInFlightRef.current = null;
        submittedTaskIdsRef.current = [];
        retryReadRef.current = false;
        setIsRetryingReadback(false);
        setIsSubmitting(false);
        setReadbackMessage(null);
        setReadbackFloor(undefined);
    }, [currentProject?.id, currentProject?.readEnvironment]);
    const [showCameraDropdown, setShowCameraDropdown] = useState(false);
    const [polishedPrompt, setPolishedPrompt] = useState<{ cn: string; en: string } | null>(null);
    const [isPolishing, setIsPolishing] = useState(false);
    const [feedbackText, setFeedbackText] = useState("");

    const handlePolish = async (feedback: string = "") => {
        const draftPrompt = feedback ? (polishedPrompt?.en || prompt) : prompt;
        if (!draftPrompt) return;
        setIsPolishing(true);
        try {
            let res;
            const scriptId = currentProject?.id || "";
            if (generationMode === 'r2v') {
                // R2V mode: use R2V-specific polish with slot info
                const slotInfo = castSlots
                    .filter(slot => slot.url)
                    .map((slot) => ({
                        description: slot.name || 'Unknown character'
                    }));
                res = await api.polishR2VPrompt(draftPrompt, slotInfo, feedback, scriptId);
            } else {
                // I2V mode: use video polish
                res = await api.polishVideoPrompt(draftPrompt, feedback, scriptId);
            }
            if (res.prompt_cn && res.prompt_en) {
                setPolishedPrompt({ cn: res.prompt_cn, en: res.prompt_en });
                setFeedbackText("");
            }
        } catch (error) {
            console.error("Polish failed", error);
            alert(tc("aiPolishFailed"));
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
                alert(tc("maxRefVideos"));
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

    const handleRetryProjectRead = async () => {
        if (!currentProject || currentProject.readEnvironment !== getRuntimeEnvironment()?.key || retryReadRef.current) return;
        retryReadRef.current = true;
        const projectId = currentProject.id;
        const opToken = projectSessionRef.current;
        setIsRetryingReadback(true);
        try {
            const confirmed = await readProjectOnce(projectId, readbackFloor, { isCurrent: currentProjectReadGuard(projectId, opToken) });
            if (!isProjectSessionCurrent(projectId, opToken)) return;
            onTaskCreated(confirmed);
            setReadbackMessage(null);
            setReadbackFloor(undefined);
        } catch (error) {
            console.error("Failed to retry project read:", error);
            if (isProjectSessionCurrent(projectId, opToken)) setReadbackMessage(tFrameCommand("savedReadFailed"));
        } finally {
            if (isProjectSessionCurrent(projectId, opToken)) {
                retryReadRef.current = false;
                setIsRetryingReadback(false);
            }
        }
    };

    const handleSubmit = async () => {
        // Validation based on mode
        if (readbackMessage || submitBusy || submitInFlightRef.current || hasPendingUpload) return;
        if (generationMode === 'i2v') {
            if (selectedImages.length === 0 || !prompt || !currentProject) return;
        } else {
            // R2V mode: need at least one cast slot filled
            const filledSlots = castSlots.filter(s => s.url);
            if (filledSlots.length === 0) {
                alert(tc("r2vNeedSlot"));
                return;
            }
            if (!prompt || !currentProject) return;
        }

        if (isOrderedWan3VideoModelId(params.model)) {
            alert(tc("orderedWan3RequiresStoryboard"));
            return;
        }

        const projectId = currentProject.id;
        const opToken = projectSessionRef.current;
        const submitOp = videoCreatorOperationKey(projectId, "submit");
        const store = useProjectStore.getState();
        if (!isProjectSessionCurrent(projectId, opToken) || store.runningOps[submitOp]) return;
        submitInFlightRef.current = { projectId, token: opToken };
        store.setRunningOp(submitOp, true);
        submittedTaskIdsRef.current = [];
        setIsSubmitting(true);
        setReadbackMessage(null);
        try {
            // Add motion description to prompt
            const motionDesc = getMotionDescription();
            const finalPrompt = motionDesc ? `${prompt}, ${motionDesc}` : prompt;

            // Determine items to process
            // In I2V: process selected images
            // In R2V: process selected images OR a single task if no image selected
            let itemsToProcess = selectedImages;
            if (generationMode === 'r2v' && selectedImages.length === 0) {
                itemsToProcess = [""]; // Dummy item to trigger one iteration
            }

            // Batch submit for all images. A later readback failure must not
            // be reported as paid submission failure or trigger a second paid call.
            let submittedCount = 0;
            let submitFailure: unknown = null;
            for (const img of itemsToProcess) {
                if (!isProjectSessionCurrent(projectId, opToken)) return;
                try {
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
                        // I2V mode: find frame by matching image URL (check rendered_image_url first, then image_url)
                        const frame = currentProject?.frames?.find((f: any) =>
                            (f.rendered_image_url || f.image_url) === img ||
                            f.image_url === img ||
                            `${API_URL}/files/${f.image_url}` === img
                        );
                        frameId = frame ? frame.id : undefined;
                    }

                    // Determine model based on generation mode
                    // R2V mode uses the hidden route model, I2V uses the selected visible model.
                    const actualModel = generationMode === 'r2v' ? getR2vRouteModelId(params.model) : params.model;
                    if (isOrderedWan3VideoModelId(actualModel)) {
                        alert(tc("orderedWan3RequiresStoryboard"));
                        return;
                    }
                    const r2vImageBased = generationMode === 'r2v' && isR2vImageBased(actualModel);

                    // Get reference URLs from cast slots for R2V
                    const referenceVideos = generationMode === 'r2v' && !r2vImageBased
                        ? castSlots.filter(s => s.url).map(s => s.url)
                        : [];
                    const referenceImages = r2vImageBased
                        ? castSlots.filter(s => s.url).map(s => s.url)
                        : [];

                    const accepted = await api.createVideoTask(
                        projectId,
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
                        referenceVideos,  // Use cast slots (Wan R2V)
                        // Kling params
                        params.mode,
                        params.sound,
                        params.cfgScale,
                        // Vidu params
                        params.viduAudio,
                        params.movementAmplitude,
                        // HappyHorse params
                        referenceImages,  // Reference images for HappyHorse R2V
                        undefined  // ratio (use default)
                    );
                    if (!isProjectSessionCurrent(projectId, opToken)) return;
                    if (!Array.isArray(accepted) || !accepted.length || accepted.some(task =>
                        !task || typeof task.id !== 'string' || !task.id || task.project_id !== projectId)) {
                        setReadbackMessage(tFrameCommand("unconfirmed"));
                        return;
                    }
                    submittedTaskIdsRef.current.push(...accepted.map(task => task.id));
                    submittedCount += 1;
                } catch (error) {
                    submitFailure = error;
                    break;
                }
            }

            if (!isProjectSessionCurrent(projectId, opToken)) return;
            if (submitFailure) {
                if (submittedCount > 0) {
                    try {
                        const confirmed = await readProjectOnce(projectId, undefined, { isCurrent: currentProjectReadGuard(projectId, opToken) });
                        if (!isProjectSessionCurrent(projectId, opToken)) return;
                        onTaskCreated(confirmed);
                        alert(tc("partialSubmit", { count: submittedCount, total: itemsToProcess.length }));
                    } catch (readError) {
                        console.error("Failed to read partially submitted video tasks:", readError);
                        if (isProjectSessionCurrent(projectId, opToken)) { setReadbackMessage(tFrameCommand("savedReadFailed")); setReadbackFloor(undefined); }
                    }
                    return;
                }
                throw submitFailure;
            }

            try {
                const confirmed = await readProjectOnce(projectId, undefined, { isCurrent: currentProjectReadGuard(projectId, opToken) });
                if (!isProjectSessionCurrent(projectId, opToken)) return;
                onTaskCreated(confirmed);
            } catch (readError) {
                console.error("Failed to read submitted video tasks:", readError);
                if (readError instanceof ProjectReadbackError) {
                    if (isProjectSessionCurrent(projectId, opToken)) { setReadbackMessage(tFrameCommand("savedReadFailed")); setReadbackFloor(undefined); }
                    return;
                }
                throw readError;
            }

            if (!isProjectSessionCurrent(projectId, opToken)) return;
            // Success feedback
            setSubmitSuccess(true);
            setTimeout(() => {
                if (isProjectSessionCurrent(projectId, opToken)) setSubmitSuccess(false);
            }, 1500);

            // Clear selection after successful submit
            // setSelectedImages([]); // Keep selection for iterative generation
        } catch (error) {
            console.error("Failed to submit task:", error);
            if (isProjectSessionCurrent(projectId, opToken)) alert(tc("submitFailed"));
        } finally {
            if (submitInFlightRef.current?.projectId === projectId && submitInFlightRef.current.token === opToken) {
                submitInFlightRef.current = null;
            }
            store.setRunningOp(submitOp, false);
            if (isProjectSessionCurrent(projectId, opToken)) setIsSubmitting(false);
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

    // Whether the current R2V mode uses image references (HappyHorse) or video references (Wan)
    const r2vUsesImages = isR2vImageBased(getR2vRouteModelId(params.model));

    // Available Reference Images (for HappyHorse R2V - character images)
    const availableReferenceImages = currentProject ? [
        ...currentProject.characters.flatMap((c: any) => {
            const images: { url: string; thumbnail: string; title: string; assetName: string; type: string }[] = [];
            if (c.full_body_image_url) {
                images.push({
                    url: getAssetUrl(c.full_body_image_url),
                    thumbnail: getAssetUrl(c.full_body_image_url),
                    title: `${c.name} - Full Body`,
                    assetName: c.name,
                    type: 'character_full_body'
                });
            }
            if (c.full_body?.image_variants?.length) {
                images.push(...c.full_body.image_variants.map((v: any) => ({
                    url: getAssetUrl(v.url),
                    thumbnail: getAssetUrl(v.url),
                    title: `${c.name} - Full Body Variant`,
                    assetName: c.name,
                    type: 'character_full_body'
                })));
            }
            if (c.headshot_image_url) {
                images.push({
                    url: getAssetUrl(c.headshot_image_url),
                    thumbnail: getAssetUrl(c.headshot_image_url),
                    title: `${c.name} - Headshot`,
                    assetName: c.name,
                    type: 'character_headshot'
                });
            }
            if (c.head_shot?.image_variants?.length) {
                images.push(...c.head_shot.image_variants.map((v: any) => ({
                    url: getAssetUrl(v.url),
                    thumbnail: getAssetUrl(v.url),
                    title: `${c.name} - Headshot Variant`,
                    assetName: c.name,
                    type: 'character_headshot'
                })));
            }
            return images;
        })
    ].filter(img => img.url && img.url !== 'null' && img.url !== 'undefined') : [];

    return (
        <div className="h-full flex flex-col relative min-h-0">
            {/* Scrollable Content Area */}
            <div className="flex-1 overflow-y-auto p-8 custom-scrollbar min-h-0">
                <h2 className="text-2xl font-display font-bold text-foreground mb-6 flex items-center gap-3">
                    <div className="w-2 h-8 bg-primary rounded-full" />
                    {tc("title")}
                    <span className="text-xs font-mono text-text-muted bg-glass px-2 py-1 rounded">Motion</span>
                </h2>

                <div className="flex flex-col gap-6 max-w-4xl mx-auto w-full pb-8">
                    {/* Generation Mode Switcher */}
                    <div className="flex items-center justify-center">
                        <div className="flex bg-surface rounded-xl p-1.5 gap-1 border border-glass-border">
                            <button
                                onClick={() => {
                                    setGenerationMode("i2v");
                                    onParamsChange({ generationMode: "i2v" });
                                }}
                                className={`px-5 py-2.5 text-sm rounded-lg flex items-center gap-2 transition-all font-medium ${generationMode === "i2v"
                                    ? "bg-primary text-foreground shadow-lg"
                                    : "text-text-secondary hover:text-foreground hover:bg-glass"
                                    }`}
                            >
                                <ImageIcon size={16} />
                                {tc("i2vMode")}
                            </button>
                            <button
                                onClick={() => {
                                    setGenerationMode("r2v");
                                    onParamsChange({
                                        generationMode: "r2v",
                                        model: R2V_SELECTION_MODEL_ID
                                    });
                                }}
                                className={`px-5 py-2.5 text-sm rounded-lg flex items-center gap-2 transition-all font-medium ${generationMode === "r2v"
                                    ? "bg-primary text-foreground shadow-lg"
                                    : "text-text-secondary hover:text-foreground hover:bg-glass"
                                    }`}
                            >
                                <Film size={16} />
                                {tc("r2vMode")}
                            </button>
                        </div>
                    </div>
                    {/* === I2V MODE: Source Selector === */}
                    {generationMode === 'i2v' && (
                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <label className="text-sm font-medium text-text-secondary">{tc("firstFrame")}</label>
                                <div className="flex bg-glass rounded-lg p-1 gap-1">
                                    <button
                                        onClick={() => setActiveTab("storyboard")}
                                        className={`px-3 py-1.5 text-xs rounded-md flex items-center gap-2 transition-all ${activeTab === "storyboard"
                                            ? "bg-primary text-foreground shadow-sm"
                                            : "text-text-secondary hover:text-foreground hover:bg-glass"
                                            }`}
                                    >
                                        <Layout size={14} /> {tc("storyboardSource")}
                                    </button>
                                    <button
                                        onClick={() => setActiveTab("upload")}
                                        className={`px-3 py-1.5 text-xs rounded-md flex items-center gap-2 transition-all ${activeTab === "upload"
                                            ? "bg-primary text-foreground shadow-sm"
                                            : "text-text-secondary hover:text-foreground hover:bg-glass"
                                            }`}
                                    >
                                        <Upload size={14} /> {tc("uploadSource")}
                                    </button>
                                </div>
                            </div>

                            {/* Tab Content */}
                            <div className="bg-surface border border-glass-border rounded-xl p-4 min-h-[200px]">
                                {activeTab === "storyboard" ? (
                                    <div className="space-y-4">
                                        {currentProject?.frames && currentProject.frames.length > 0 ? (() => {
                                            const completedVideoIds = new Set(
                                                currentProject.video_tasks
                                                    ?.filter((t: any) => t.status === "completed")
                                                    .map((t: any) => t.id) ?? []
                                            );
                                            return (
                                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 max-h-[500px] overflow-y-auto custom-scrollbar pr-2 p-2">
                                                {currentProject.frames.map((frame: any, index: number) => {
                                                    const prevFrame = index > 0 ? currentProject.frames![index - 1] : null;
                                                    const prevVideoCompleted = prevFrame?.selected_video_id && completedVideoIds.has(prevFrame.selected_video_id);
                                                    const isExtracting = extractingFrameId === frame.id;
                                                    const hasExtracted = !!frame.rendered_image_url;

                                                    return (
                                                    <div
                                                        key={frame.id}
                                                        onClick={() => handleFrameSelect(frame)}
                                                        className={`group relative aspect-video rounded-lg overflow-hidden border cursor-pointer transition-all ${selectedImages.includes(frame.rendered_image_url || frame.image_url)
                                                            ? "border-primary ring-2 ring-primary/50"
                                                            : "border-glass-border hover:border-glass-border"
                                                            }`}
                                                    >
                                                        {(frame.rendered_image_url || frame.image_url) ? (
                                                            <img
                                                                src={getAssetUrlWithTimestamp(frame.rendered_image_url || frame.image_url, frame.updated_at)}
                                                                alt={`Frame ${frame.id}`}
                                                                className="w-full h-full object-cover"
                                                            />
                                                        ) : (
                                                            <div className="w-full h-full bg-glass flex items-center justify-center text-xs text-text-muted">
                                                                No Image
                                                            </div>
                                                        )}
                                                        <div className="absolute inset-0 bg-overlay opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                                                            <span className="text-xs text-foreground font-bold">Select</span>
                                                        </div>
                                                        {/* Frame Number Badge */}
                                                        <div className="absolute top-1 left-1 bg-surface px-1.5 rounded text-[0.625rem] text-text-secondary backdrop-blur-sm">
                                                            #{frame.id.slice(0, 4)}
                                                        </div>
                                                        {/* Extract Last Frame Button */}
                                                        {prevVideoCompleted && (
                                                            <button
                                                                onClick={(e) => handleExtractLastFrame(frame.id, e)}
                                                                disabled={isExtracting}
                                                                className={`absolute bottom-1 right-1 flex items-center gap-1 px-1.5 py-0.5 rounded text-[0.625rem] font-medium backdrop-blur-sm transition-colors ${
                                                                    hasExtracted
                                                                        ? "bg-green-500/20 text-green-400 border border-green-500/30 hover:bg-primary/20 hover:text-primary hover:border-primary/30"
                                                                        : "bg-primary/20 text-primary border border-primary/30 hover:bg-primary/40"
                                                                } disabled:opacity-50`}
                                                                title={hasExtracted ? "Re-extract previous video's last frame" : "Use previous video's last frame as input"}
                                                            >
                                                                {isExtracting ? (
                                                                    <Loader2 size={10} className="animate-spin" />
                                                                ) : hasExtracted ? (
                                                                    <><Check size={10} /> Applied</>
                                                                ) : (
                                                                    <><Film size={10} /> Prev End Frame</>
                                                                )}
                                                            </button>
                                                        )}
                                                    </div>
                                                    );
                                                })}
                                            </div>
                                            );
                                        })() : (
                                            <div className="flex flex-col items-center justify-center h-[200px] text-text-muted gap-2">
                                                <Layout size={32} className="opacity-20" />
                                                <p className="text-xs">No storyboard frames found.</p>
                                            </div>
                                        )}

                                        {/* Selected Preview (Storyboard Mode) */}
                                        {selectedImages.length > 0 && (
                                            <div className="pt-4 border-t border-glass-border">
                                                <p className="text-xs text-text-muted mb-2">Selected for Generation:</p>
                                                <div className="flex gap-2 flex-wrap">
                                                    {selectedImages.map((img, idx) => {
                                                        // Find frame to get updated_at for cache busting
                                                        const frame = currentProject?.frames?.find((f: any) => (f.rendered_image_url || f.image_url) === img);
                                                        const timestamp = frame?.updated_at || 0;
                                                        return (
                                                            <div key={idx} className="relative w-24 aspect-video rounded-lg overflow-hidden border border-glass-border">
                                                                <img
                                                                    src={timestamp ? getAssetUrlWithTimestamp(img, timestamp) : getAssetUrl(img)}
                                                                    alt="Selected"
                                                                    className="w-full h-full object-cover"
                                                                />
                                                                <button
                                                                    onClick={() => removeImage(idx)}
                                                                    className="absolute top-1 right-1 p-0.5 bg-surface rounded-full text-foreground hover:bg-red-500"
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
                                                <div key={idx} className="relative aspect-video bg-surface rounded-xl overflow-hidden border border-glass-border group">
                                                    <img
                                                        src={getAssetUrl(img)}
                                                        alt={`Input ${idx}`}
                                                        className="w-full h-full object-contain"
                                                    />
                                                    <button
                                                        onClick={() => removeImage(idx)}
                                                        className="absolute top-2 right-2 p-1 bg-surface rounded-full text-foreground opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500"
                                                    >
                                                        <X size={12} />
                                                    </button>
                                                    {img.startsWith("blob:") && !uploadingPaths[img] && (
                                                        <div className="absolute inset-0 flex items-center justify-center bg-overlay">
                                                            <Loader2 className="animate-spin text-foreground" size={20} />
                                                        </div>
                                                    )}
                                                </div>
                                            ))}

                                            {/* Add Button */}
                                            <div
                                                onClick={() => document.getElementById('image-upload')?.click()}
                                                className="aspect-video border-2 border-dashed border-glass-border rounded-xl flex flex-col items-center justify-center bg-glass hover:bg-hover-bg transition-colors cursor-pointer relative min-h-[100px]"
                                            >
                                                <input
                                                    id="image-upload"
                                                    type="file"
                                                    accept="image/*"
                                                    multiple
                                                    className="hidden"
                                                    onChange={(e) => handleImageSelect(e.target.files)}
                                                />
                                                <Plus className="text-text-secondary mb-2" size={24} />
                                                <p className="text-text-secondary text-xs font-medium">Add Image</p>
                                            </div>
                                        </div>

                                        {/* Quick Select from Assets (Only in Upload Mode) */}
                                        {availableAssets.length > 0 && (
                                            <div className="mt-4 pt-4 border-t border-glass-border">
                                                <p className="text-xs text-text-muted mb-2">Quick Select from Assets:</p>
                                                <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-hide">
                                                    {availableAssets.slice(0, 10).map((asset, i) => (
                                                        <div
                                                            key={i}
                                                            onClick={() => handleAssetSelect(asset.url)}
                                                            className="w-16 h-16 relative rounded-lg overflow-hidden flex-shrink-0 border border-glass-border hover:border-primary cursor-pointer"
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
                                <label className="text-sm font-medium text-text-secondary">{tc("noFrameSelected", { defaultMessage: "Select Frame" })}</label>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[200px] overflow-y-auto custom-scrollbar pr-2">
                                    {currentProject?.frames && currentProject.frames.length > 0 ? (
                                        currentProject.frames.map((frame: any) => (
                                            <div
                                                key={frame.id}
                                                onClick={() => handleR2VFrameSelect(frame)}
                                                className={`p-3 rounded-lg border cursor-pointer transition-all ${selectedFrameId === frame.id
                                                    ? "border-primary bg-primary/10 ring-2 ring-primary/30"
                                                    : "border-glass-border bg-surface hover:border-glass-border"
                                                    }`}
                                            >
                                                <div className="flex items-start gap-3">
                                                    {/* Frame thumbnail */}
                                                    <div className="w-16 h-10 rounded overflow-hidden flex-shrink-0 bg-surface">
                                                        {frame.image_url ? (
                                                            <img
                                                                src={getAssetUrlWithTimestamp(frame.image_url, frame.updated_at)}
                                                                alt=""
                                                                className="w-full h-full object-cover"
                                                            />
                                                        ) : (
                                                            <div className="w-full h-full flex items-center justify-center text-text-muted">
                                                                <Layout size={14} />
                                                            </div>
                                                        )}
                                                    </div>
                                                    {/* Frame description */}
                                                    <div className="flex-1 min-w-0">
                                                        <p className="text-xs text-text-secondary mb-1">#{frame.id.slice(0, 6)}</p>
                                                        <p className="text-xs text-text-secondary line-clamp-2">
                                                            {frame.action_description || frame.image_prompt || 'No description'}
                                                        </p>
                                                        {frame.dialogue && (
                                                            <p className="text-[0.625rem] text-primary mt-1 italic line-clamp-1">
                                                                “{frame.dialogue}”
                                                            </p>
                                                        )}
                                                    </div>
                                                    {/* Selected indicator */}
                                                    {selectedFrameId === frame.id && (
                                                        <div className="w-5 h-5 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
                                                            <Check size={12} className="text-white" />
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        ))
                                    ) : (
                                        <div className="col-span-2 flex flex-col items-center justify-center h-[100px] text-text-muted gap-2">
                                            <Layout size={24} className="opacity-20" />
                                            <p className="text-xs">{tc("noFrameSelected")}</p>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Cast Slots */}
                            <div className="space-y-3">
                                <label className="text-sm font-medium text-text-secondary">
                                    {r2vUsesImages ? tc('referenceImages') : 'Cast Slots'}
                                </label>
                                {r2vUsesImages ? (
                                    /* HappyHorse R2V: Image reference slots (1-9) */
                                    <>
                                        <div className="grid grid-cols-3 gap-3">
                                            {Array.from({ length: Math.min(Math.max(castSlots.filter(s => s.url).length + 1, 3), 9) }, (_, slotIndex) => {
                                                const slot = castSlots[slotIndex];
                                                const refImage = slot?.url ? availableReferenceImages.find(img => img.url === slot.url) : null;

                                                return (
                                                    <div
                                                        key={slotIndex}
                                                        className={`relative rounded-xl border-2 border-dashed transition-all ${slot?.url
                                                            ? "border-primary bg-primary/10"
                                                            : "border-glass-border bg-surface hover:border-glass-border"
                                                            }`}
                                                    >
                                                        {/* Slot Header */}
                                                        <div className="absolute top-2 left-2 z-10">
                                                            <span className="text-[0.625rem] px-2 py-0.5 rounded-full bg-primary text-white font-bold">
                                                                character{slotIndex + 1}
                                                            </span>
                                                        </div>

                                                        {slot?.url ? (
                                                            /* Filled Slot - show image */
                                                            <div className="aspect-square relative">
                                                                <img
                                                                    src={refImage?.thumbnail || slot.url}
                                                                    alt={slot.name}
                                                                    className="w-full h-full object-cover rounded-xl"
                                                                />
                                                                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-2 rounded-b-xl">
                                                                    <p className="text-xs text-foreground font-medium truncate">{slot.name}</p>
                                                                </div>
                                                                <button
                                                                    onClick={() => handleClearCastSlot(slotIndex)}
                                                                    className="absolute top-2 right-2 p-1 bg-surface rounded-full text-foreground hover:bg-red-500 transition-colors"
                                                                >
                                                                    <X size={12} />
                                                                </button>
                                                            </div>
                                                        ) : (
                                                            /* Empty Slot */
                                                            <div className="aspect-square flex flex-col items-center justify-center p-3">
                                                                <ImageIcon size={16} className="text-text-muted mb-1" />
                                                                <select
                                                                    className="w-full text-xs bg-input-bg border border-glass-border rounded-lg px-2 py-1.5 text-text-secondary focus:border-primary focus:outline-none"
                                                                    value=""
                                                                    onChange={(e) => {
                                                                        const selectedImg = availableReferenceImages.find(img => img.url === e.target.value);
                                                                        if (selectedImg) {
                                                                            handleCastSlotSelect(slotIndex, { url: selectedImg.url, name: selectedImg.assetName });
                                                                        }
                                                                    }}
                                                                >
                                                                    <option value="">{tc('selectImage')}</option>
                                                                    {availableReferenceImages.map((img, i) => (
                                                                        <option key={i} value={img.url}>{img.assetName} - {img.type}</option>
                                                                    ))}
                                                                </select>
                                                                {slotIndex === 0 && (
                                                                    <p className="text-[0.625rem] text-amber-400 mt-1">Required</p>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                        <p className="text-xs text-text-muted">{tc('refImagesHint')}</p>
                                        {availableReferenceImages.length === 0 && (
                                            <p className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
                                                {tc('noRefImagesAvailable')}
                                            </p>
                                        )}
                                    </>
                                ) : (
                                    /* Wan R2V: Video reference slots (3) */
                                    <>
                                        <div className="grid grid-cols-3 gap-4">
                                            {[0, 1, 2].map((slotIndex) => {
                                                const slot = castSlots[slotIndex];
                                                const slotTitle = slotIndex === 0 ? 'Protagonist' : 'Supporting';
                                                const video = slot?.url ? availableReferenceVideos.find(v => v.url === slot.url) : null;

                                                return (
                                                    <div
                                                        key={slotIndex}
                                                        className={`relative rounded-xl border-2 border-dashed transition-all ${slot?.url
                                                            ? "border-primary bg-primary/10"
                                                            : "border-glass-border bg-surface hover:border-glass-border"
                                                            }`}
                                                    >
                                                        {/* Slot Header */}
                                                        <div className="absolute top-2 left-2 z-10">
                                                            <span className="text-[0.625rem] px-2 py-0.5 rounded-full bg-primary text-white font-bold">
                                                                Character {slotIndex + 1}
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
                                                                    <p className="text-xs text-foreground font-medium truncate">{slot.name}</p>
                                                                </div>
                                                                <button
                                                                    onClick={() => handleClearCastSlot(slotIndex)}
                                                                    className="absolute top-2 right-2 p-1 bg-surface rounded-full text-foreground hover:bg-red-500 transition-colors"
                                                                >
                                                                    <X size={12} />
                                                                </button>
                                                            </div>
                                                        ) : (
                                                            /* Empty Slot */
                                                            <div className="aspect-video flex flex-col items-center justify-center p-4">
                                                                <p className="text-xs text-text-secondary mb-2">{slotTitle}</p>
                                                                <select
                                                                    className="w-full text-xs bg-input-bg border border-glass-border rounded-lg px-2 py-1.5 text-text-secondary focus:border-primary focus:outline-none"
                                                                    value=""
                                                                    onChange={(e) => {
                                                                        const selectedVideo = availableReferenceVideos.find(v => v.url === e.target.value);
                                                                        if (selectedVideo) {
                                                                            handleCastSlotSelect(slotIndex, { url: selectedVideo.url, name: selectedVideo.assetName });
                                                                        }
                                                                    }}
                                                                >
                                                                    <option value="">{tc('selectRefVideo')}</option>
                                                                    {availableReferenceVideos.map((v, i) => (
                                                                        <option key={i} value={v.url}>{v.assetName} - {v.type}</option>
                                                                    ))}
                                                                </select>
                                                                {slotIndex === 0 && (
                                                                    <p className="text-[0.625rem] text-amber-400 mt-2">Required</p>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                        {availableReferenceVideos.length === 0 && (
                                            <p className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3">
                                                {tc('noRefVideosAvailable')}
                                            </p>
                                        )}
                                    </>
                                )}
                            </div>
                        </div>
                    )}


                    {readbackMessage ? (
                        <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                            <span>{readbackMessage}</span>
                            <button
                                type="button"
                                onClick={handleRetryProjectRead}
                                disabled={isRetryingReadback}
                                className="shrink-0 underline disabled:opacity-60"
                            >
                                {isRetryingReadback ? tc("generatingVideo") : tProject("readAgain")}
                            </button>
                        </div>
                    ) : null}

                    {/* 2. Prompt Input */}
                    <div className="space-y-2">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium text-text-secondary">{tc("promptLabel")}</label>
                            <div className="flex items-center gap-2">
                                {generationMode === 'i2v' && (
                                    <div className="relative">
                                        <button
                                            onClick={() => promptBuilderRef.current?.insertCamera()}
                                            className="text-xs flex items-center gap-1 px-2 py-1 rounded transition-colors text-text-secondary hover:text-foreground hover:bg-glass"
                                        >
                                            <Video size={12} /> Camera
                                        </button>
                                    </div>
                                )}
                                <button
                                    onClick={() => handlePolish()}
                                    disabled={isPolishing || !prompt}
                                    className="text-xs text-primary hover:text-primary/80 flex items-center gap-1 disabled:opacity-50"
                                >
                                    {isPolishing ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
                                    {tc("aiPolish")}
                                </button>
                                <button
                                    onClick={() => setSegments([{ type: "text", value: "", id: "init" }])}
                                    className="text-xs text-text-secondary hover:text-foreground flex items-center gap-1 px-2 py-1 rounded hover:bg-glass transition-colors"
                                    title="Clear Prompt"
                                >
                                    <Eraser size={12} /> Clear
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
                                                ? "border-primary/50 bg-primary/10 text-primary hover:bg-primary/20"
                                                : "border-glass-border bg-glass text-text-muted cursor-not-allowed"
                                                }`}
                                        >
                                            {video?.thumbnail ? (
                                                <img src={getAssetUrl(video.thumbnail)} alt="" className="w-4 h-4 rounded-full object-cover" />
                                            ) : (
                                                <span className="w-4 h-4 rounded-full bg-primary/30 flex items-center justify-center text-[0.625rem]">+</span>
                                            )}
                                            <span>Insert {slot?.name || `Char ${idx + 1}`}</span>
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
                                    ? tc('promptPlaceholder')
                                    : tc("promptPlaceholder")
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
                                    className="bg-primary/10 border border-primary/30 rounded-lg p-3 mt-2 space-y-3"
                                >
                                    <div className="flex justify-between items-start">
                                        <span className="text-xs font-bold text-primary flex items-center gap-1">
                                            <Wand2 size={12} /> {tc("aiPolish")}
                                        </span>
                                        <button
                                            onClick={() => { setPolishedPrompt(null); setFeedbackText(""); }}
                                            className="text-[0.625rem] text-text-secondary hover:text-foreground"
                                        >
                                            ✕
                                        </button>
                                    </div>

                                    {/* Chinese Prompt */}
                                    <div className="space-y-1">
                                        <div className="flex justify-between items-center">
                                            <span className="text-[0.625rem] font-bold text-text-muted uppercase">CN (Preview)</span>
                                            <button
                                                onClick={() => {
                                                    navigator.clipboard.writeText(polishedPrompt.cn);
                                                    alert("CN prompt copied");
                                                }}
                                                className="text-[0.625rem] text-text-secondary hover:text-foreground bg-surface px-2 py-0.5 rounded"
                                            >
                                                复制
                                            </button>
                                        </div>
                                        <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap bg-surface p-2 rounded">
                                            {polishedPrompt.cn}
                                        </p>
                                    </div>

                                    {/* English Prompt */}
                                    <div className="space-y-1">
                                        <div className="flex justify-between items-center">
                                            <span className="text-[0.625rem] font-bold text-text-muted uppercase">EN (Generation)</span>
                                            <div className="flex gap-1">
                                                <button
                                                    onClick={() => {
                                                        navigator.clipboard.writeText(polishedPrompt.en);
                                                        alert("English prompt copied");
                                                    }}
                                                    className="text-[0.625rem] text-text-secondary hover:text-foreground bg-surface px-2 py-0.5 rounded"
                                                >
                                                    Copy
                                                </button>
                                                <button
                                                    onClick={() => {
                                                        setSegments([{ type: "text", value: polishedPrompt.en, id: `polished-${Date.now()}` }]);
                                                        setPolishedPrompt(null);
                                                    }}
                                                    className="text-[0.625rem] text-foreground bg-primary hover:bg-primary/90 px-2 py-0.5 rounded font-bold"
                                                >
                                                    应用
                                                </button>
                                            </div>
                                        </div>
                                        <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap bg-surface p-2 rounded font-mono">
                                            {polishedPrompt.en}
                                        </p>
                                    </div>

                                    {/* Feedback for iterative refinement */}
                                    <div className="space-y-2 pt-2 border-t border-primary/20">
                                        <div className="flex gap-2">
                                            <input
                                                type="text"
                                                value={feedbackText}
                                                onChange={(e) => setFeedbackText(e.target.value)}
                                                onKeyDown={(e) => {
                                                    if (e.key === "Enter" && feedbackText.trim() && !isPolishing) {
                                                        handlePolish(feedbackText.trim());
                                                    }
                                                }}
                                                placeholder="Feedback for refinement..."
                                                className="flex-1 text-xs bg-input-bg border border-primary/20 rounded px-2 py-1.5 text-foreground placeholder-text-muted focus:outline-none focus:border-primary/50"
                                            />
                                            <button
                                                onClick={() => handlePolish(feedbackText.trim())}
                                                disabled={isPolishing || !feedbackText.trim()}
                                                className="text-xs text-foreground bg-primary hover:bg-primary/90 px-3 py-1.5 rounded font-medium flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                                            >
                                                {isPolishing ? <Loader2 size={10} className="animate-spin" /> : <Wand2 size={10} />}
                                                再润色
                                            </button>
                                        </div>
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </div>
                </div>
            </div >

            {/* 4. Fixed Action Bar */}
            < div className="p-6 border-t border-glass-border bg-surface z-10" >
                <div className="max-w-4xl mx-auto w-full">
                    <button
                        onClick={handleSubmit}
                        disabled={Boolean(readbackMessage) || submitBusy || hasPendingUpload || (!prompt || isSubmitting) || (generationMode === 'i2v' && selectedImages.length === 0)}
                        className={`w-full py-4 rounded-xl font-bold text-lg flex items-center justify-center gap-2 transition-all transform active:scale-[0.99] ${submitSuccess
                            ? "bg-green-500 text-white"
                            : "bg-primary hover:bg-primary/90 text-white"
                            } disabled:opacity-50 disabled:cursor-not-allowed`}
                    >
                        {isSubmitting ? (
                            <>
                                <Loader2 className="animate-spin" /> {tc("generatingVideo")}
                            </>
                        ) : submitSuccess ? (
                            <>
                                <Plus /> Queued
                            </>
                        ) : (
                            <>
                                <Plus /> {tc("generateVideo")} (Ctrl+Enter)
                            </>
                        )}
                    </button>
                    <div className="flex justify-center mt-3">
                        <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer hover:text-text-secondary">
                            <input type="checkbox" className="rounded bg-glass border-glass-border" />
                            Clear after submit
                        </label>
                    </div>
                </div>
            </div >
        </div >
    );
}
