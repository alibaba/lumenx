"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Sparkles, Palette, Wand2, Plus, Check, Loader2, ChevronRight, Upload, X } from "lucide-react";
import { useProjectStore, type StyleConfig, type StylePreset } from "@/store/projectStore"; // Combined imports
import { api } from "@/lib/api";
import { getStylePresetCopy, getStyleTerm, messages } from "@/lib/i18n";
import { getAssetUrl } from "@/lib/utils";

const copy = messages.modules.artDirection;

function getStylePositivePrompt(style: StyleConfig | StylePreset) {
    return "positive_prompt" in style ? style.positive_prompt : style.prompt;
}

function getDisplayStyleLabel(style: StyleConfig | StylePreset) {
    return getStylePresetCopy(style.id)?.label || getStyleTerm(style.id)?.label || style.name;
}

function getDisplayStyleDescription(style: StyleConfig | StylePreset) {
    const fallbackDescription = "description" in style && typeof style.description === "string" ? style.description : "";
    return getStylePresetCopy(style.id)?.description || fallbackDescription;
}

function getStyleReferenceImages(style: StyleConfig | StylePreset | null | undefined) {
    if (!style || !("reference_images" in style) || !Array.isArray(style.reference_images)) {
        return [];
    }
    return style.reference_images.filter((value) => typeof value === "string" && value.trim().length > 0);
}

function getStyleMoodboardNotes(style: StyleConfig | StylePreset | null | undefined) {
    if (!style || !("moodboard_notes" in style) || typeof style.moodboard_notes !== "string") {
        return "";
    }
    return style.moodboard_notes;
}

export default function ArtDirection() {
    const {
        currentProject,
        updateProject,
        isAnalyzingArtStyle,
        analyzeArtStyle
    } = useProjectStore();

    const [selectedStyle, setSelectedStyle] = useState<StyleConfig | null>(null);
    const [customStyles, setCustomStyles] = useState<StyleConfig[]>([]);
    const [aiRecommendations, setAiRecommendations] = useState<StyleConfig[]>([]);
    const [presets, setPresets] = useState<StylePreset[]>([]); // Changed type to StylePreset[]

    // Editor state
    const [editingName, setEditingName] = useState("");
    const [editingPositive, setEditingPositive] = useState("");
    const [editingNegative, setEditingNegative] = useState("");
    const [editingReferenceImages, setEditingReferenceImages] = useState<string[]>([]);
    const [editingMoodboardNotes, setEditingMoodboardNotes] = useState("");
    const [isSaving, setIsSaving] = useState(false);
    const [isUploadingReference, setIsUploadingReference] = useState(false);

    // Load presets only once on mount (separate from project-dependent state)
    useEffect(() => {
        loadPresets();
    }, []);  // Empty dependency - only run on mount

    // Load art direction from project when it changes
    useEffect(() => {
        // Load existing art direction if available
        if (currentProject?.art_direction) {
            console.log("Loading Art Direction:", currentProject.art_direction);
            if (currentProject.art_direction.style_config) {
                setSelectedStyle(currentProject.art_direction.style_config);
                setEditingName(currentProject.art_direction.style_config.name || "");
                setEditingPositive(currentProject.art_direction.style_config.positive_prompt || "");
                setEditingNegative(currentProject.art_direction.style_config.negative_prompt || "");
                setEditingReferenceImages(getStyleReferenceImages(currentProject.art_direction.style_config));
                setEditingMoodboardNotes(getStyleMoodboardNotes(currentProject.art_direction.style_config));
            }

            setCustomStyles(currentProject.art_direction.custom_styles || []);

            // Load recommendations from project if available
            if (currentProject.art_direction.ai_recommendations && currentProject.art_direction.ai_recommendations.length > 0) {
                setAiRecommendations(currentProject.art_direction.ai_recommendations);
            }
        } else {
            console.log("No Art Direction found in currentProject");
            setSelectedStyle(null);
            setEditingName("");
            setEditingPositive("");
            setEditingNegative("");
            setEditingReferenceImages([]);
            setEditingMoodboardNotes("");
            setCustomStyles([]);
            setAiRecommendations([]);
        }
    }, [currentProject?.id, currentProject?.art_direction]);  // More specific dependencies

    // Sync local aiRecommendations with store when it updates (e.g. after analysis finishes)
    useEffect(() => {
        if (currentProject?.art_direction?.ai_recommendations) {
            setAiRecommendations(currentProject.art_direction.ai_recommendations);
        }
    }, [currentProject?.art_direction?.ai_recommendations]);

    const loadPresets = async () => {
        try {
            const data = await api.getStylePresets();
            console.log("Loaded presets:", data.presets);
            setPresets(data.presets || []);
        } catch (error) {
            console.error("Failed to load presets:", error);
        }
    };

    const handleAnalyze = async () => {
        if (!currentProject) return;

        // Use global action
        try {
            await analyzeArtStyle(
                currentProject.id,
                currentProject.originalText || currentProject.title
            );
        } catch (error) {
            console.error("Failed to analyze script:", error);
            alert(copy.analyzeFailed);
        }
    };

    const toStyleConfig = (style: StyleConfig | StylePreset): StyleConfig => {
        if ("positive_prompt" in style) {
            return style;
        }

        return {
            id: style.id,
            name: style.name,
            positive_prompt: style.prompt,
            negative_prompt: style.negative_prompt || "",
            reference_images: [],
            moodboard_notes: "",
            is_custom: false,
        };
    };

    const buildEditingStyleConfig = (baseStyle: StyleConfig): StyleConfig => ({
        ...baseStyle,
        name: editingName,
        positive_prompt: editingPositive,
        negative_prompt: editingNegative,
        reference_images: editingReferenceImages.filter(Boolean),
        moodboard_notes: editingMoodboardNotes.trim(),
    });

    const handleSelectStyle = (style: StyleConfig | StylePreset) => {
        const normalizedStyle = toStyleConfig(style);
        setSelectedStyle(normalizedStyle);
        setEditingName(normalizedStyle.name);
        setEditingPositive(normalizedStyle.positive_prompt);
        setEditingNegative(normalizedStyle.negative_prompt);
        setEditingReferenceImages(getStyleReferenceImages(normalizedStyle));
        setEditingMoodboardNotes(getStyleMoodboardNotes(normalizedStyle));
    };

    const handleUploadReferenceImage = async (file: File | null) => {
        if (!file) return;

        setIsUploadingReference(true);
        try {
            const result = await api.uploadFile(file);
            const nextUrl = typeof result?.storage_path === "string" ? result.storage_path : result.url;
            if (typeof nextUrl === "string" && nextUrl.trim()) {
                setEditingReferenceImages((prev) => Array.from(new Set([...prev, nextUrl])));
            }
        } catch (error) {
            console.error("Failed to upload style reference image:", error);
            alert(copy.referenceUploadFailed);
        } finally {
            setIsUploadingReference(false);
        }
    };

    const handleRemoveReferenceImage = (targetUrl: string) => {
        setEditingReferenceImages((prev) => prev.filter((url) => url !== targetUrl));
    };

    const handleSaveCustom = async () => {
        if (!editingName || !editingPositive) {
            alert(copy.missingStyleFields);
            return;
        }

        const newCustomStyle: StyleConfig = {
            id: `custom-${Date.now()}`,
            name: editingName,
            positive_prompt: editingPositive,
            negative_prompt: editingNegative,
            reference_images: editingReferenceImages.filter(Boolean),
            moodboard_notes: editingMoodboardNotes.trim(),
            is_custom: true
        };

        const updatedCustomStyles = [...customStyles, newCustomStyle];
        setCustomStyles(updatedCustomStyles);

        // Always try to save immediately
        if (currentProject) {
            try {
                const styleToPersist = selectedStyle ? buildEditingStyleConfig(selectedStyle) : newCustomStyle;
                const selectedStyleId = selectedStyle?.id || newCustomStyle.id;
                const updated = await api.saveArtDirection(
                    currentProject.id,
                    selectedStyleId,
                    styleToPersist,
                    updatedCustomStyles,
                    aiRecommendations
                );
                updateProject(currentProject.id, updated);
                setSelectedStyle(styleToPersist);
                alert(copy.customStyleSaved);
            } catch (error) {
                console.error("Failed to save custom style:", error);
                alert(copy.saveFailed);
            }
        }
    };

    const handleApply = async () => {
        if (!currentProject || !selectedStyle) {
            alert(copy.selectStyleFirst);
            return;
        }

        const finalConfig = buildEditingStyleConfig(selectedStyle);

        setIsSaving(true);
        try {
            const updated = await api.saveArtDirection(
                currentProject.id,
                finalConfig.id,
                finalConfig,
                customStyles,
                aiRecommendations
            );
            updateProject(currentProject.id, updated);
            alert(copy.applied);
        } catch (error) {
            console.error("Failed to save art direction:", error);
            alert(copy.saveFailed);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <div className="flex flex-col h-full overflow-hidden">
            {/* Header */}
            <div className="h-20 border-b border-white/10 bg-black/20 flex items-center px-8 justify-between">
                <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-purple-500 to-pink-600 flex items-center justify-center shadow-lg shadow-purple-500/20">
                        <Palette className="text-white" size={20} />
                    </div>
                    <div>
                        <h2 className="text-xl font-display font-bold text-white">{copy.title}</h2>
                        <p className="text-xs text-gray-400">{copy.subtitle}</p>
                    </div>
                </div>

                <button
                    onClick={handleApply}
                    disabled={!selectedStyle || isSaving}
                    className="px-6 py-2 bg-primary hover:bg-primary/90 text-white rounded-lg font-bold transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                    {isSaving ? (
                        <>
                            <Loader2 size={16} className="animate-spin" />
                            {copy.saving}
                        </>
                    ) : (
                        <>
                            {copy.applyAndContinue}
                            <ChevronRight size={16} />
                        </>
                    )}
                </button>
            </div>

            <div className="flex-1 flex overflow-hidden">
                {/* Left Panel: AI + Presets */}
                <div className="w-2/3 flex flex-col p-8 overflow-y-auto gap-8 border-r border-white/10">
                    {/* AI Recommendations */}
                    <div>
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="text-lg font-bold text-white flex items-center gap-2">
                                <Sparkles size={20} className="text-yellow-400" />
                                {copy.sections.aiRecommendations}
                            </h3>
                            <button
                                onClick={handleAnalyze}
                                disabled={isAnalyzingArtStyle}
                                className="px-4 py-2 bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white text-sm rounded-lg font-medium transition-all disabled:opacity-50 flex items-center gap-2"
                            >
                                {isAnalyzingArtStyle ? (
                                    <>
                                        <Loader2 size={14} className="animate-spin" />
                                        {copy.analyzing}
                                    </>
                                ) : (
                                    <>
                                        <Wand2 size={14} />
                                        {copy.analyzeScript}
                                    </>
                                )}
                            </button>
                        </div>

                        <div className="grid grid-cols-1 gap-4">
                            {aiRecommendations.map((style) => (
                                <StyleRecommendationCard
                                    key={style.id}
                                    style={style}
                                    isSelected={selectedStyle?.id === style.id}
                                    onSelect={() => handleSelectStyle(style)}
                                />
                            ))}
                        </div>
                    </div>

                    {/* Built-in Presets */}
                    <div>
                        <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                            <Palette size={20} className="text-blue-400" />
                            {copy.sections.presets}
                        </h3>

                        <div className="grid grid-cols-2 gap-4">
                            {presets.map((style) => (
                                <StylePresetCard
                                    key={style.id}
                                    style={style}
                                    isSelected={selectedStyle?.id === style.id}
                                    onSelect={() => handleSelectStyle(style)}
                                />
                            ))}
                        </div>
                    </div>

                    {/* Custom Styles */}
                    {customStyles.length > 0 && (
                        <div>
                            <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                                <Plus size={20} className="text-green-400" />
                                {copy.sections.customStyles}
                            </h3>

                            <div className="grid grid-cols-2 gap-4">
                                {customStyles.map((style) => (
                                    <StylePresetCard
                                        key={style.id}
                                        style={style}
                                        isSelected={selectedStyle?.id === style.id}
                                        onSelect={() => handleSelectStyle(style)}
                                    />
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Right Panel: Editor */}
                <div className="w-1/3 flex flex-col p-8 overflow-y-auto bg-black/10">
                    <StyleEditor
                        name={editingName}
                        positivePrompt={editingPositive}
                        negativePrompt={editingNegative}
                        referenceImages={editingReferenceImages}
                        moodboardNotes={editingMoodboardNotes}
                        isUploadingReference={isUploadingReference}
                        onNameChange={setEditingName}
                        onPositiveChange={setEditingPositive}
                        onNegativeChange={setEditingNegative}
                        onMoodboardNotesChange={setEditingMoodboardNotes}
                        onUploadReferenceImage={handleUploadReferenceImage}
                        onRemoveReferenceImage={handleRemoveReferenceImage}
                        onSaveCustom={handleSaveCustom}
                        selectedStyle={selectedStyle}
                    />
                </div>
            </div>
        </div>
    );
}

// Sub-components
function StyleRecommendationCard({ style, isSelected, onSelect }: any) {
    const displayName = getDisplayStyleLabel(style);
    const displayDescription = getDisplayStyleDescription(style);
    const promptText = getStylePositivePrompt(style);

    return (
        <motion.div
            layout
            onClick={onSelect}
            className={`p-6 rounded-xl border-2 cursor-pointer transition-all ${isSelected
                ? "bg-purple-500/20 border-purple-500 shadow-lg shadow-purple-500/20"
                : "bg-white/5 border-white/10 hover:border-white/30 hover:bg-white/10"
                }`}
        >
            <div className="flex items-start gap-4">
                <div className={`w-8 h-8 rounded-full flex items-center justify-center ${isSelected ? 'bg-purple-500' : 'bg-white/10'}`}>
                    {isSelected ? <Check size={16} className="text-white" /> : <Sparkles size={16} className="text-gray-400" />}
                </div>
                <div className="flex-1">
                    <h4 className="font-bold text-white mb-1">{displayName}</h4>
                    {displayDescription && <p className="text-xs text-gray-400 mb-3">{displayDescription}</p>}
                    {style.reason && (
                        <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-3 mb-3">
                            <p className="text-xs text-yellow-300">
                                <span className="font-bold">{copy.recommendationReason}</span>
                                {style.reason}
                            </p>
                        </div>
                    )}
                    <div className="flex flex-wrap gap-2">
                        {promptText.split(",").slice(0, 3).map((keyword: string, i: number) => (
                            <span key={i} className="text-[10px] px-2 py-1 bg-primary/20 text-primary rounded border border-primary/30">
                                {keyword.trim()}
                            </span>
                        ))}
                    </div>
                </div>
            </div>
        </motion.div>
    );
}

function StylePresetCard({ style, isSelected, onSelect }: any) {
    const displayName = getDisplayStyleLabel(style);
    const displayDescription = getDisplayStyleDescription(style);
    const promptText = getStylePositivePrompt(style);

    return (
        <motion.div
            layout
            onClick={onSelect}
            className={`p-4 rounded-xl border-2 cursor-pointer transition-all ${isSelected
                ? "bg-blue-500/20 border-blue-500 shadow-lg shadow-blue-500/20"
                : "bg-white/5 border-white/10 hover:border-white/30 hover:bg-white/10"
                }`}
        >
            <div className="flex items-center gap-3 mb-2">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center ${isSelected ? 'bg-blue-500' : 'bg-white/10'}`}>
                    {isSelected && <Check size={12} className="text-white" />}
                </div>
                <h4 className="font-bold text-white text-sm">{displayName}</h4>
            </div>
            {displayDescription && (
                <p className="text-xs text-gray-400 mb-2">{displayDescription}</p>
            )}
            <div className="text-[10px] text-gray-500 truncate">
                {promptText.substring(0, 50)}...
            </div>
        </motion.div>
    );
}

function StyleEditor({
    name,
    positivePrompt,
    negativePrompt,
    referenceImages,
    moodboardNotes,
    isUploadingReference,
    onNameChange,
    onPositiveChange,
    onNegativeChange,
    onMoodboardNotesChange,
    onUploadReferenceImage,
    onRemoveReferenceImage,
    onSaveCustom,
    selectedStyle,
}: any) {
    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-lg font-bold text-white mb-4">{copy.editor.title}</h3>
                {!selectedStyle && (
                    <div className="text-sm text-gray-500 italic mb-4">
                        {copy.editor.selectHint}
                    </div>
                )}
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                    {copy.editor.name}
                </label>
                <input
                    type="text"
                    value={name}
                    onChange={(e) => onNameChange(e.target.value)}
                    placeholder={copy.editor.namePlaceholder}
                    className="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-sm text-white placeholder-gray-600 focus:border-primary focus:outline-none"
                />
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                    {copy.editor.positivePrompt}
                </label>
                <textarea
                    value={positivePrompt}
                    onChange={(e) => onPositiveChange(e.target.value)}
                    placeholder={copy.editor.positivePlaceholder}
                    rows={6}
                    className="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-sm text-white placeholder-gray-600 focus:border-primary focus:outline-none resize-none"
                />
                <p className="text-xs text-gray-500 mt-1">
                    {copy.editor.positiveHint}
                </p>
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                    {copy.editor.negativePrompt}
                </label>
                <textarea
                    value={negativePrompt}
                    onChange={(e) => onNegativeChange(e.target.value)}
                    placeholder={copy.editor.negativePlaceholder}
                    rows={4}
                    className="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-sm text-white placeholder-gray-600 focus:border-primary focus:outline-none resize-none"
                />
                <p className="text-xs text-gray-500 mt-1">
                    {copy.editor.negativeHint}
                </p>
            </div>

            <div>
                <div className="flex items-center justify-between mb-2">
                    <label className="block text-sm font-medium text-gray-300">
                        {copy.editor.referenceImages}
                    </label>
                    <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-white transition hover:bg-white/10">
                        <Upload size={14} />
                        <span>{isUploadingReference ? copy.editor.uploadingReference : copy.editor.uploadReference}</span>
                        <input
                            type="file"
                            accept="image/*"
                            className="hidden"
                            onChange={(event) => {
                                const file = event.target.files?.[0] || null;
                                onUploadReferenceImage(file);
                                event.target.value = "";
                            }}
                            disabled={isUploadingReference}
                        />
                    </label>
                </div>
                <p className="text-xs text-gray-500 mb-3">
                    {copy.editor.referenceHint}
                </p>
                {referenceImages.length > 0 ? (
                    <div className="grid grid-cols-2 gap-3">
                        {referenceImages.map((url: string) => (
                            <div key={url} className="rounded-xl border border-white/10 bg-black/20 p-2">
                                <div className="relative">
                                    <img
                                        src={getAssetUrl(url)}
                                        alt={copy.editor.referenceAlt}
                                        className="h-28 w-full rounded-lg object-cover"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => onRemoveReferenceImage(url)}
                                        className="absolute right-2 top-2 rounded-full bg-black/60 p-1 text-gray-200 transition hover:bg-black/80"
                                        title={copy.editor.removeReference}
                                    >
                                        <X size={12} />
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className="rounded-xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-5 text-xs text-gray-500">
                        {copy.editor.referenceEmpty}
                    </div>
                )}
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                    {copy.editor.moodboardNotes}
                </label>
                <textarea
                    value={moodboardNotes}
                    onChange={(e) => onMoodboardNotesChange(e.target.value)}
                    placeholder={copy.editor.moodboardPlaceholder}
                    rows={4}
                    className="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-sm text-white placeholder-gray-600 focus:border-primary focus:outline-none resize-none"
                />
                <p className="text-xs text-gray-500 mt-1">
                    {copy.editor.moodboardHint}
                </p>
            </div>

            <div className="pt-4 border-t border-white/10">
                <button
                    onClick={onSaveCustom}
                    disabled={!name || !positivePrompt}
                    className="w-full px-4 py-2 bg-white/10 hover:bg-white/20 text-white text-sm rounded-lg font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                    <Plus size={14} />
                    {copy.editor.saveAsCustom}
                </button>
            </div>

            {/* Preview */}
            {positivePrompt && (
                <div className="bg-white/5 border border-white/10 rounded-lg p-4">
                    <p className="text-xs text-gray-500 mb-2">{copy.editor.previewTitle}</p>
                    <p className="text-xs text-blue-400 font-mono">
                        {copy.editor.previewTemplate(positivePrompt, moodboardNotes || "", referenceImages.length)}
                    </p>
                </div>
            )}
        </div>
    );
}
