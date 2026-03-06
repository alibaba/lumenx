"use client";

import { motion } from "framer-motion";
import { Settings, Sliders, Image as ImageIcon, Type, FileText, Users, Layout, Video, Mic, Music, Film, Info, StickyNote, Paintbrush, Wand2, Sparkles } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { useState, useEffect } from "react";
import { api, API_URL } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";

interface PropertiesPanelProps {
    activeStep: string;
}

export default function PropertiesPanel({ activeStep }: PropertiesPanelProps) {
    const currentProject = useProjectStore((state) => state.currentProject);

    // Hide panel for Motion step as it has its own sidebar
    if (activeStep === "motion" || activeStep === "assembly") return null;

    const renderContent = () => {
        switch (activeStep) {
            case "script":
                return <ScriptInspector project={currentProject} />;
            case "assets":
                return <AssetsInspector project={currentProject} />;
            case "storyboard":
                return <StoryboardInspector />;
            case "motion":
                return <MotionInspector />;
            case "audio":
                return <AudioInspector project={currentProject} />;
            case "mix":
                return <MixInspector />;
            case "export":
                return <ExportInspector />;
            default:
                return <div className="p-4 text-gray-500">Select a step to view properties.</div>;
        }
    };

    return (
        <motion.aside
            initial={{ x: 100, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            className="w-80 h-screen border-l border-glass-border bg-black/40 backdrop-blur-xl flex flex-col z-50"
        >
            <div className="p-4 border-b border-glass-border flex items-center justify-between">
                <h2 className="font-display font-bold text-white flex items-center gap-2">
                    <Info size={16} className="text-primary" /> Context
                </h2>
                <span className="text-xs font-mono text-gray-500 uppercase">{activeStep}</span>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-6">
                {renderContent()}
            </div>
        </motion.aside>
    );
}

// --- Sub-Inspectors ---

function ScriptInspector({ project }: { project: any }) {
    if (!project) return null;
    const wordCount = project.originalText?.length || 0;
    const charCount = project.characters?.length || 0;
    const sceneCount = project.scenes?.length || 0;

    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <FileText size={14} /> Project Stats
                </h3>
                <div className="grid grid-cols-2 gap-2">
                    <StatBox label="Words" value={wordCount} />
                    <StatBox label="Chars" value={charCount} />
                    <StatBox label="Scenes" value={sceneCount} />
                    <StatBox label="Est. Dur" value="~2m" />
                </div>
            </div>

            <div className="space-y-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <StickyNote size={14} /> Quick Notes
                </h3>
                <textarea
                    className="w-full h-32 bg-white/5 border border-white/10 rounded-lg p-3 text-xs text-gray-300 resize-none focus:outline-none focus:border-primary/50"
                    placeholder="Jot down ideas here..."
                />
            </div>

            <div className="pt-4 border-t border-white/10">
                <ArtDirectionStyleDisplay project={project} />
            </div>
        </div>
    );
}

function AssetsInspector({ project }: { project: any }) {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    // Get art direction style from Step 2
    const artDirectionStyle = currentProject?.art_direction?.style_config;

    // Get aspect ratios from model settings
    const characterAspectRatio = currentProject?.model_settings?.character_aspect_ratio || '9:16';
    const sceneAspectRatio = currentProject?.model_settings?.scene_aspect_ratio || '16:9';
    const propAspectRatio = currentProject?.model_settings?.prop_aspect_ratio || '1:1';

    const handleUpdateAspectRatio = async (type: 'character' | 'scene' | 'prop', ratio: string) => {
        if (!currentProject) return;

        try {
            const updatePayload: any = {};
            if (type === 'character') updatePayload.character_aspect_ratio = ratio;
            else if (type === 'scene') updatePayload.scene_aspect_ratio = ratio;
            else if (type === 'prop') updatePayload.prop_aspect_ratio = ratio;

            const updated = await api.updateModelSettings(
                currentProject.id,
                undefined, undefined, undefined,
                type === 'character' ? ratio : undefined,
                type === 'scene' ? ratio : undefined,
                type === 'prop' ? ratio : undefined,
                undefined
            );
            updateProject(currentProject.id, updated);
        } catch (error) {
            console.error('Failed to update aspect ratio:', error);
        }
    };

    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Users size={14} /> Asset Overview
                </h3>
                <div className="text-xs text-gray-400">
                    Manage aspect ratios and view global style settings.
                </div>
            </div>

            {/* Aspect Ratio Controls */}
            <div className="space-y-4 pt-4 border-t border-white/10">
                <div className="flex items-center gap-2 mb-2">
                    <Layout className="text-primary" size={14} />
                    <h3 className="font-bold text-white text-xs">Aspect Ratios</h3>
                </div>

                {/* Character Aspect Ratio */}
                <div className="space-y-2">
                    <label className="text-[10px] font-bold text-gray-500 uppercase">Character</label>
                    <div className="grid grid-cols-3 gap-1.5">
                        {['9:16', '16:9', '1:1'].map((ratio) => (
                            <button
                                key={ratio}
                                onClick={() => handleUpdateAspectRatio('character', ratio)}
                                className={`px-2 py-1.5 rounded text-[10px] border transition-all font-medium ${characterAspectRatio === ratio
                                    ? 'bg-primary/20 text-primary border-primary/30'
                                    : 'bg-white/5 text-gray-400 border-white/10 hover:bg-white/10'
                                    }`}
                            >
                                {ratio}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Scene Aspect Ratio */}
                <div className="space-y-2">
                    <label className="text-[10px] font-bold text-gray-500 uppercase">Scene</label>
                    <div className="grid grid-cols-3 gap-1.5">
                        {['9:16', '16:9', '1:1'].map((ratio) => (
                            <button
                                key={ratio}
                                onClick={() => handleUpdateAspectRatio('scene', ratio)}
                                className={`px-2 py-1.5 rounded text-[10px] border transition-all font-medium ${sceneAspectRatio === ratio
                                    ? 'bg-primary/20 text-primary border-primary/30'
                                    : 'bg-white/5 text-gray-400 border-white/10 hover:bg-white/10'
                                    }`}
                            >
                                {ratio}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Prop Aspect Ratio */}
                <div className="space-y-2">
                    <label className="text-[10px] font-bold text-gray-500 uppercase">Prop</label>
                    <div className="grid grid-cols-3 gap-1.5">
                        {['9:16', '16:9', '1:1'].map((ratio) => (
                            <button
                                key={ratio}
                                onClick={() => handleUpdateAspectRatio('prop', ratio)}
                                className={`px-2 py-1.5 rounded text-[10px] border transition-all font-medium ${propAspectRatio === ratio
                                    ? 'bg-primary/20 text-primary border-primary/30'
                                    : 'bg-white/5 text-gray-400 border-white/10 hover:bg-white/10'
                                    }`}
                            >
                                {ratio}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {/* Art Direction Style Display (Read-only) */}
            <div className="pt-4 border-t border-white/10">
                <ArtDirectionStyleDisplay project={currentProject} />
            </div>
        </div>
    );
}

function ArtDirectionStyleDisplay({ project }: { project: any }) {
    const artDirectionStyle = project?.art_direction?.style_config;

    return (
        <div className="space-y-4">
            <div className="flex items-center gap-2 mb-2">
                <Paintbrush className="text-primary" size={14} />
                <h3 className="font-bold text-white text-xs">Art Direction Style</h3>
            </div>

            {artDirectionStyle ? (
                <div className="space-y-3">
                    <div>
                        <label className="text-[10px] font-bold text-gray-500 uppercase mb-1.5 block">Style Name</label>
                        <div className="text-xs font-bold text-white bg-gradient-to-r from-blue-500/20 to-purple-500/20 p-2.5 rounded-lg border border-white/10">
                            {artDirectionStyle.name}
                        </div>
                    </div>

                    <div>
                        <label className="text-[10px] font-bold text-gray-500 uppercase mb-1.5 block">Positive Prompt</label>
                        <div className="bg-black/40 border border-white/5 rounded-lg p-2.5 text-[10px] text-gray-400 leading-relaxed max-h-20 overflow-y-auto">
                            {artDirectionStyle.positive_prompt || 'No positive prompt defined'}
                        </div>
                    </div>

                    {artDirectionStyle.negative_prompt && (
                        <div>
                            <label className="text-[10px] font-bold text-gray-500 uppercase mb-1.5 block">Negative Prompt</label>
                            <div className="bg-black/40 border border-white/5 rounded-lg p-2.5 text-[10px] text-gray-400 leading-relaxed max-h-16 overflow-y-auto">
                                {artDirectionStyle.negative_prompt}
                            </div>
                        </div>
                    )}

                    <div className="pt-2">
                        <p className="text-[9px] text-gray-500 leading-relaxed">
                            💡 Tip: Edit style in Step 2 (Art Direction)
                        </p>
                    </div>
                </div>
            ) : (
                <div className="bg-white/5 border border-white/10 rounded-lg p-3 text-center">
                    <p className="text-xs text-gray-500 mb-2">No style configured</p>
                    <p className="text-[9px] text-gray-600">
                        Go to Step 2 (Art Direction) to set up your project's visual style
                    </p>
                </div>
            )}
        </div>
    );
}

function StoryboardInspector() {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);
    const selectedFrameId = useProjectStore((state) => state.selectedFrameId);

    if (!currentProject) return null;

    const selectedFrame = currentProject?.frames?.find((f: any) => f.id === selectedFrameId);

    const updateFrame = async (data: any) => {
        if (!currentProject || !selectedFrame) return;

        // Optimistically update local state first
        const updatedFrames = currentProject.frames.map((f: any) =>
            f.id === selectedFrameId ? { ...f, ...data } : f
        );
        updateProject(currentProject.id, { frames: updatedFrames });

        // Sync to backend (fire and forget for speed, but log errors)
        try {
            await api.updateFrame(currentProject.id, selectedFrame.id, data);
        } catch (error) {
            console.error("Failed to sync frame to backend:", error);
            // Note: We don't revert optimistic update to keep UI responsive
        }
    };

    const handleComposePrompt = () => {
        if (!selectedFrame || !currentProject) return;

        const scene = currentProject.scenes?.find((s: any) => s.id === selectedFrame.scene_id);
        const characters = currentProject.characters?.filter((c: any) => selectedFrame.character_ids?.includes(c.id));

        // Construct prompt based on User Guide: Motion + Camera (+ Context)
        const promptParts = [];

        // 1. Motion / Action (Subject + Action)
        let motionPart = "";
        if (characters && characters.length > 0) {
            const charDescriptions = characters.map((c: any) => {
                let desc = `${c.name} (${c.description}`;
                if (c.clothing) desc += `, wearing ${c.clothing}`;
                desc += `)`;
                return desc;
            }).join(", ");
            motionPart += `Characters: ${charDescriptions}. `;
        }
        motionPart += `${selectedFrame.action_description || ""}`;
        if (selectedFrame.facial_expression) motionPart += `, ${selectedFrame.facial_expression}`;
        if (motionPart.trim()) promptParts.push(motionPart.trim());

        // 2. Camera (Movement + Angle)
        let cameraPart = "";
        if (selectedFrame.camera_angle) cameraPart += `${selectedFrame.camera_angle}`;
        if (selectedFrame.camera_movement) {
            if (cameraPart) cameraPart += ", ";
            cameraPart += `${selectedFrame.camera_movement}`;
        }
        if (selectedFrame.composition) {
            if (cameraPart) cameraPart += ", ";
            cameraPart += `${selectedFrame.composition}`;
        }
        if (cameraPart.trim()) promptParts.push(cameraPart.trim());

        // 3. Scene / Context (Environment + Atmosphere)
        let scenePart = "";
        if (scene) {
            scenePart += `${scene.description || scene.name}`;
            if (scene.time_of_day) scenePart += `, ${scene.time_of_day}`;
            if (scene.lighting_mood) scenePart += `, ${scene.lighting_mood}`;
        }
        if (selectedFrame.atmosphere) {
            if (scenePart) scenePart += ", ";
            scenePart += `${selectedFrame.atmosphere}`;
        }
        if (scenePart.trim()) promptParts.push(scenePart.trim());

        // Join with periods for clear separation
        const finalPrompt = promptParts.join(" . ");
        updateFrame({ image_prompt: finalPrompt });
    };

    const toggleCharacter = (charId: string) => {
        const currentIds = selectedFrame.character_ids || [];
        const newIds = currentIds.includes(charId)
            ? currentIds.filter((id: string) => id !== charId)
            : [...currentIds, charId];
        updateFrame({ character_ids: newIds });
    };

    // State for bilingual polish results
    const [polishedPrompts, setPolishedPrompts] = useState<Record<string, { cn: string; en: string }>>({});
    const [isPolishing, setIsPolishing] = useState(false);

    const polishedPrompt = selectedFrame ? polishedPrompts[selectedFrame.id] : null;

    const handlePolish = async () => {
        if (!selectedFrame || !currentProject) return;
        setIsPolishing(true);

        // Construct assets list for context
        const assets = [];
        if (selectedFrame.scene_id) {
            const scene = currentProject.scenes?.find((s: any) => s.id === selectedFrame.scene_id);
            if (scene) assets.push({ type: 'Scene', name: scene.name, description: scene.description });
        }
        if (selectedFrame.character_ids) {
            selectedFrame.character_ids.forEach((cid: string) => {
                const char = currentProject.characters?.find((c: any) => c.id === cid);
                if (char) assets.push({ type: 'Character', name: char.name, description: char.description });
            });
        }
        if (selectedFrame.prop_ids) {
            selectedFrame.prop_ids.forEach((pid: string) => {
                const prop = currentProject.props?.find((p: any) => p.id === pid);
                if (prop) assets.push({ type: 'Prop', name: prop.name, description: prop.description });
            });
        }

        const draft = selectedFrame.image_prompt || selectedFrame.action_description;

        try {
            // Use new bilingual refine API
            const res = await api.refineFramePrompt(currentProject.id, selectedFrame.id, draft, assets);
            if (res.prompt_cn && res.prompt_en) {
                setPolishedPrompts(prev => ({
                    ...prev,
                    [selectedFrame.id]: { cn: res.prompt_cn, en: res.prompt_en }
                }));
            }
        } catch (err) {
            console.error("Polish failed", err);
            alert("Prompt polishing failed");
        } finally {
            setIsPolishing(false);
        }
    };

    if (!selectedFrame) {
        return (
            <div className="space-y-6">
                <div className="p-4 bg-white/5 rounded-lg border border-white/10 text-center text-gray-500 text-xs">
                    Select a frame to edit its details.
                </div>
                <p className="text-xs text-gray-500 text-center">
                    Tip: Use the ⚙️ icon in the sidebar to configure aspect ratios.
                </p>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Layout size={14} /> Frame Editor
                </h3>
                <div className="text-xs text-gray-400">
                    Editing Frame {currentProject?.frames?.findIndex((f: any) => f.id === selectedFrameId) + 1}
                </div>
            </div>

            {/* Action Description */}
            <div className="space-y-2">
                <label className="text-xs font-bold text-gray-500 uppercase">Action / Visuals</label>
                <textarea
                    className="w-full h-24 bg-black/20 border border-white/10 rounded-lg p-3 text-xs text-gray-300 resize-none focus:outline-none focus:border-primary/50"
                    value={selectedFrame.action_description || ""}
                    onChange={(e) => updateFrame({ action_description: e.target.value })}
                    placeholder="Describe the action..."
                />
            </div>

            {/* Dialogue */}
            <div className="space-y-2">
                <label className="text-xs font-bold text-gray-500 uppercase">Dialogue</label>
                <textarea
                    className="w-full h-16 bg-black/20 border border-white/10 rounded-lg p-3 text-xs text-gray-300 resize-none focus:outline-none focus:border-primary/50"
                    value={selectedFrame.dialogue || ""}
                    onChange={(e) => updateFrame({ dialogue: e.target.value })}
                    placeholder="Speaker: Content"
                />
            </div>

            {/* Reference Assets */}
            <div className="space-y-2">
                {(() => {
                    // Calculate current reference count
                    const selectedScene = currentProject?.scenes?.find((s: any) => s.id === selectedFrame.scene_id);
                    const sceneHasImage = selectedScene?.image_url;

                    const selectedChars = currentProject?.characters?.filter((c: any) => selectedFrame.character_ids?.includes(c.id));
                    const charImageCount = selectedChars?.filter((c: any) => c.image_url || c.avatar_url).length || 0;

                    const selectedProps = currentProject?.props?.filter((p: any) => selectedFrame.prop_ids?.includes(p.id));
                    const propImageCount = selectedProps?.filter((p: any) => p.image_url).length || 0;

                    const referenceCount = (sceneHasImage ? 1 : 0) + charImageCount + propImageCount;

                    // Dynamic limit based on model
                    const i2iModel = currentProject?.model_settings?.i2i_model;
                    const referenceLimit = i2iModel === 'wan2.6-image' ? 4 : 3;
                    const isLimitReached = referenceCount >= referenceLimit;

                    return (
                        <>
                            <div className="flex justify-between items-center">
                                <label className="text-xs font-bold text-gray-500 uppercase">Reference Assets</label>
                                <span className={`text-[10px] ${isLimitReached ? "text-yellow-500 font-bold" : "text-gray-500"}`}>
                                    {referenceCount}/{referenceLimit} Images
                                </span>
                            </div>

                            {/* Scene Selector */}
                            <div className="mb-2 space-y-2">
                                <label className="text-[10px] font-bold text-gray-500 uppercase">Scene</label>
                                <select
                                    className="w-full bg-black/20 border border-white/10 rounded p-2 text-xs text-gray-300 focus:outline-none"
                                    value={selectedFrame.scene_id || ""}
                                    onChange={(e) => {
                                        // Check if selecting this scene would exceed limit
                                        // Actually, replacing a scene is always fine unless we treat scene as optional toggle.
                                        // Here it's a dropdown, so we always have 0 or 1 scene. 
                                        // If we switch to a scene with image from one without, we might exceed limit.
                                        const newSceneId = e.target.value;
                                        const newScene = currentProject?.scenes?.find((s: any) => s.id === newSceneId);
                                        const newSceneHasImage = newScene?.image_url;

                                        // Predicted count: (newScene ? 1 : 0) + charCount + propCount
                                        // If current scene had image, we lose 1, gain 1 (net 0).
                                        // If current didn't, we gain 1.
                                        const predictedCount = (newSceneHasImage ? 1 : 0) + charImageCount + propImageCount;

                                        if (predictedCount > referenceLimit) {
                                            alert(`Cannot select this scene: Reference image limit (${referenceLimit}) would be exceeded. Deselect some characters or props first.`);
                                            return;
                                        }
                                        updateFrame({ scene_id: newSceneId });
                                    }}
                                >
                                    <option value="">Select Scene...</option>
                                    {currentProject?.scenes?.map((scene: any) => (
                                        <option key={scene.id} value={scene.id}>{scene.name}</option>
                                    ))}
                                </select>

                                {/* Show Scene Description if selected */}
                                {selectedScene?.description && (
                                    <div className="bg-white/5 p-2 rounded text-[10px] text-gray-400 italic border border-white/5">
                                        <span className="font-bold not-italic text-gray-500">Scene: </span>
                                        {selectedScene.description}
                                    </div>
                                )}
                            </div>

                            {/* Character Toggles */}
                            <div className="space-y-2">
                                <label className="text-[10px] font-bold text-gray-500 uppercase">Characters</label>
                                <div className="grid grid-cols-2 gap-2">
                                    {currentProject?.characters?.map((char: any) => {
                                        const isSelected = selectedFrame.character_ids?.includes(char.id);
                                        const hasImage = char.image_url || char.avatar_url;
                                        // Disable if not selected, has image, and limit reached
                                        const isDisabled = !isSelected && hasImage && isLimitReached;

                                        return (
                                            <button
                                                key={char.id}
                                                disabled={isDisabled}
                                                onClick={() => {
                                                    if (isDisabled) return;
                                                    toggleCharacter(char.id);
                                                }}
                                                className={`flex items-center gap-2 p-2 rounded border text-xs transition-all ${isSelected
                                                    ? "bg-primary/20 border-primary text-white"
                                                    : isDisabled
                                                        ? "bg-black/10 border-white/5 text-gray-600 cursor-not-allowed opacity-50"
                                                        : "bg-black/20 border-white/10 text-gray-400 hover:bg-white/5"
                                                    }`}
                                            >
                                                <div className="w-4 h-4 rounded-full bg-gray-700 overflow-hidden">
                                                    {char.avatar_url && <img src={getAssetUrl(char.avatar_url)} className="w-full h-full object-cover" />}
                                                </div>
                                                <span className="truncate">{char.name}</span>
                                            </button>
                                        );
                                    })}
                                </div>

                                {/* Show Selected Characters Descriptions */}
                                {selectedChars && selectedChars.length > 0 && (
                                    <div className="space-y-1">
                                        {selectedChars.map((char: any) => (
                                            <div key={char.id} className="bg-white/5 p-2 rounded text-[10px] text-gray-400 italic border border-white/5">
                                                <span className="font-bold not-italic text-gray-500">{char.name}: </span>
                                                {char.description}
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Prop Toggles */}
                            {currentProject?.props && currentProject.props.length > 0 && (
                                <div className="space-y-2">
                                    <label className="text-[10px] font-bold text-gray-500 uppercase">Props</label>
                                    <div className="grid grid-cols-2 gap-2">
                                        {currentProject.props.map((prop: any) => {
                                            const isSelected = selectedFrame.prop_ids?.includes(prop.id);
                                            const hasImage = prop.image_url;
                                            const isDisabled = !isSelected && hasImage && isLimitReached;

                                            return (
                                                <button
                                                    key={prop.id}
                                                    disabled={isDisabled}
                                                    onClick={() => {
                                                        if (isDisabled) return;
                                                        // Toggle Prop Logic
                                                        const currentProps = selectedFrame.prop_ids || [];
                                                        const newProps = currentProps.includes(prop.id)
                                                            ? currentProps.filter((id: string) => id !== prop.id)
                                                            : [...currentProps, prop.id];
                                                        updateFrame({ prop_ids: newProps });
                                                    }}
                                                    className={`flex items-center gap-2 p-2 rounded border text-xs transition-all ${isSelected
                                                        ? "bg-primary/20 border-primary text-white"
                                                        : isDisabled
                                                            ? "bg-black/10 border-white/5 text-gray-600 cursor-not-allowed opacity-50"
                                                            : "bg-black/20 border-white/10 text-gray-400 hover:bg-white/5"
                                                        }`}
                                                >
                                                    <div className="w-4 h-4 rounded bg-gray-700 overflow-hidden flex-shrink-0">
                                                        {prop.image_url && <img src={getAssetUrl(prop.image_url)} className="w-full h-full object-cover" />}
                                                    </div>

                                                    <span className="truncate">{prop.name}</span>
                                                </button>
                                            );
                                        })}
                                    </div>

                                    {/* Show Selected Props Descriptions */}
                                    {(() => {
                                        const selectedProps = currentProject.props.filter((p: any) => selectedFrame.prop_ids?.includes(p.id));
                                        if (selectedProps && selectedProps.length > 0) {
                                            return (
                                                <div className="space-y-1">
                                                    {selectedProps.map((prop: any) => (
                                                        <div key={prop.id} className="bg-white/5 p-2 rounded text-[10px] text-gray-400 italic border border-white/5">
                                                            <span className="font-bold not-italic text-gray-500">{prop.name}: </span>
                                                            {prop.description}
                                                        </div>
                                                    ))}
                                                </div>
                                            );
                                        }
                                        return null;
                                    })()}
                                </div>
                            )}
                        </>
                    );
                })()}
            </div>

            {/* Camera Controls */}
            <div className="space-y-2">
                <label className="text-xs font-bold text-gray-500 uppercase">Camera</label>
                <div className="grid grid-cols-1 gap-2">
                    <select
                        className="bg-black/20 border border-white/10 rounded p-2 text-xs text-gray-300 focus:outline-none"
                        value={selectedFrame.camera_angle || ""}
                        onChange={(e) => updateFrame({ camera_angle: e.target.value })}
                    >
                        <option value="">Angle...</option>
                        <option value="Wide Shot">Wide Shot</option>
                        <option value="Medium Shot">Medium Shot</option>
                        <option value="Close Up">Close Up</option>
                        <option value="Low Angle">Low Angle</option>
                        <option value="High Angle">High Angle</option>
                        <option value="Over the Shoulder">Over the Shoulder</option>
                    </select>
                </div>
            </div>

            {/* Prompt */}
            <div className="space-y-2">
                <div className="flex justify-between items-center">
                    <label className="text-xs font-bold text-gray-500 uppercase">Image Prompt</label>
                    <button
                        onClick={handleComposePrompt}
                        className="flex items-center gap-1 text-[10px] bg-white/10 hover:bg-white/20 px-2 py-1 rounded text-white transition-colors"
                        title="Auto-generate prompt from metadata"
                    >
                        <Wand2 size={10} /> Auto-Compose
                    </button>
                    <button
                        onClick={handlePolish}
                        disabled={isPolishing}
                        className="flex items-center gap-1 text-[10px] bg-purple-600 hover:bg-purple-700 px-2 py-1 rounded text-white transition-colors ml-2 disabled:opacity-50"
                        title="AI Polish Prompt"
                    >
                        {isPolishing ? <Sparkles size={10} className="animate-spin" /> : <Sparkles size={10} />} Polish
                    </button>
                </div>
                <textarea
                    className="w-full h-32 bg-black/20 border border-white/10 rounded-lg p-3 text-xs text-gray-300 resize-none focus:outline-none focus:border-primary/50"
                    value={selectedFrame.image_prompt || ""}
                    onChange={(e) => updateFrame({ image_prompt: e.target.value })}
                    placeholder="Full image generation prompt..."
                />

                {/* Polished Result Display - Bilingual */}
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
                                onClick={() => {
                                    setPolishedPrompts(prev => {
                                        const newState = { ...prev };
                                        delete newState[selectedFrame.id];
                                        return newState;
                                    });
                                }}
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
                                <span className="text-[10px] font-bold text-gray-500 uppercase">English (生图用)</span>
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
                                            updateFrame({
                                                image_prompt: polishedPrompt.en,
                                                image_prompt_cn: polishedPrompt.cn,
                                                image_prompt_en: polishedPrompt.en
                                            });
                                            setPolishedPrompts(prev => {
                                                const newState = { ...prev };
                                                delete newState[selectedFrame.id];
                                                return newState;
                                            });
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
            </div>
        </div >
    );
}

function MotionInspector() {
    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Video size={14} /> Motion Params
                </h3>
                <div className="space-y-4">
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-gray-400">
                            <span>Motion Bucket</span>
                            <span>127</span>
                        </div>
                        <input type="range" className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer" />
                    </div>
                    <div className="space-y-1">
                        <div className="flex justify-between text-xs text-gray-400">
                            <span>FPS</span>
                            <span>24</span>
                        </div>
                        <input type="range" className="w-full h-1 bg-white/10 rounded-lg appearance-none cursor-pointer" />
                    </div>
                </div>
            </div>
        </div>
    );
}

function AudioInspector({ project }: { project: any }) {
    const assignedCount = project?.characters?.filter((c: any) => c.voice_id).length || 0;
    const totalCount = project?.characters?.length || 0;

    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Mic size={14} /> Casting Status
                </h3>
                <div className="flex items-center gap-2">
                    <div className="flex-1 h-2 bg-white/10 rounded-full overflow-hidden">
                        <div
                            className="h-full bg-green-500 transition-all duration-500"
                            style={{ width: `${(assignedCount / totalCount) * 100}%` }}
                        />
                    </div>
                    <span className="text-xs font-mono text-gray-400">{assignedCount}/{totalCount}</span>
                </div>
                <p className="text-xs text-gray-500">
                    {assignedCount === totalCount ? "All characters casted." : "Some characters need voices."}
                </p>
            </div>
        </div>
    );
}

function MixInspector() {
    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Music size={14} /> Track Inspector
                </h3>
                <div className="p-4 bg-white/5 rounded-lg border border-white/10 text-center text-xs text-gray-500">
                    Select a clip on the timeline to view details.
                </div>
            </div>
        </div>
    );
}

function ExportInspector() {
    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Film size={14} /> Export History
                </h3>
                <div className="space-y-2">
                    <div className="p-2 bg-white/5 rounded border border-white/10 flex justify-between items-center">
                        <span className="text-xs text-gray-300">Project_v1.mp4</span>
                        <span className="text-[10px] text-gray-500">2h ago</span>
                    </div>
                </div>
            </div>
        </div>
    );
}

function StatBox({ label, value }: { label: string, value: string | number }) {
    return (
        <div className="bg-white/5 border border-white/10 rounded p-2 text-center">
            <div className="text-lg font-bold text-white">{value}</div>
            <div className="text-[10px] text-gray-500 uppercase">{label}</div>
        </div>
    );
}
