"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Sparkles, Palette, Wand2, Plus, Check, Loader2, ChevronRight } from "lucide-react";
import { useProjectStore, type StyleConfig, type StylePreset } from "@/store/projectStore"; // Combined imports
import { api } from "@/lib/api";

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
    const [isSaving, setIsSaving] = useState(false);

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
            }

            setCustomStyles(currentProject.art_direction.custom_styles || []);

            // Load recommendations from project if available
            if (currentProject.art_direction.ai_recommendations && currentProject.art_direction.ai_recommendations.length > 0) {
                setAiRecommendations(currentProject.art_direction.ai_recommendations);
            }
        } else {
            console.log("No Art Direction found in currentProject");
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
            alert("风格分析失败");
        }
    };

    const handleSelectStyle = (style: StyleConfig) => {
        setSelectedStyle(style);
        setEditingName(style.name);
        setEditingPositive(style.positive_prompt);
        setEditingNegative(style.negative_prompt);
    };

    const handleSaveCustom = async () => {
        if (!editingName || !editingPositive) {
            alert("请填写风格名称和正向提示词");
            return;
        }

        const newCustomStyle: StyleConfig = {
            id: `custom-${Date.now()}`,
            name: editingName,
            positive_prompt: editingPositive,
            negative_prompt: editingNegative,
            is_custom: true
        };

        const updatedCustomStyles = [...customStyles, newCustomStyle];
        setCustomStyles(updatedCustomStyles);

        // Always try to save immediately
        if (currentProject && selectedStyle) {
            try {
                // Use the newly created style as the selected style if it's the one being edited
                // Or keep the currently selected style
                const updated = await api.saveArtDirection(
                    currentProject.id,
                    selectedStyle.id,
                    selectedStyle,
                    updatedCustomStyles,
                    aiRecommendations
                );
                updateProject(currentProject.id, updated);
                alert("自定义风格已保存！");
            } catch (error) {
                console.error("Failed to save custom style:", error);
                alert("保存失败，请重试");
            }
        }
    };

    const handleApply = async () => {
        if (!currentProject || !selectedStyle) {
            alert("请先选择一个风格");
            return;
        }

        const finalConfig: StyleConfig = {
            ...selectedStyle,
            name: editingName,
            positive_prompt: editingPositive,
            negative_prompt: editingNegative
        };

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
            alert("风格配置已应用！");
        } catch (error) {
            console.error("Failed to save art direction:", error);
            alert("保存失败");
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <div className="flex flex-col h-full bg-background overflow-hidden">
            {/* Header */}
            <div className="h-20 border-b border-white/10 bg-black/20 flex items-center px-8 justify-between">
                <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-purple-500 to-pink-600 flex items-center justify-center shadow-lg shadow-purple-500/20">
                        <Palette className="text-white" size={20} />
                    </div>
                    <div>
                        <h2 className="text-xl font-display font-bold text-white">Art Direction</h2>
                        <p className="text-xs text-gray-400">风格定调 - 建立全局视觉标准</p>
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
                            保存中...
                        </>
                    ) : (
                        <>
                            应用并继续
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
                                AI 智能推荐
                            </h3>
                            <button
                                onClick={handleAnalyze}
                                disabled={isAnalyzingArtStyle}
                                className="px-4 py-2 bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white text-sm rounded-lg font-medium transition-all disabled:opacity-50 flex items-center gap-2"
                            >
                                {isAnalyzingArtStyle ? (
                                    <>
                                        <Loader2 size={14} className="animate-spin" />
                                        分析中...
                                    </>
                                ) : (
                                    <>
                                        <Wand2 size={14} />
                                        分析剧本
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
                            内置风格预设
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
                                自定义风格
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
                        onNameChange={setEditingName}
                        onPositiveChange={setEditingPositive}
                        onNegativeChange={setEditingNegative}
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
                    <h4 className="font-bold text-white mb-1">{style.name}</h4>
                    <p className="text-xs text-gray-400 mb-3">{style.description}</p>
                    {style.reason && (
                        <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-3 mb-3">
                            <p className="text-xs text-yellow-300">
                                <span className="font-bold">推荐理由：</span>
                                {style.reason}
                            </p>
                        </div>
                    )}
                    <div className="flex flex-wrap gap-2">
                        {style.positive_prompt.split(",").slice(0, 3).map((keyword: string, i: number) => (
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
                <h4 className="font-bold text-white text-sm">{style.name}</h4>
            </div>
            {style.description && (
                <p className="text-xs text-gray-400 mb-2">{style.description}</p>
            )}
            <div className="text-[10px] text-gray-500 truncate">
                {style.positive_prompt.substring(0, 50)}...
            </div>
        </motion.div>
    );
}

function StyleEditor({ name, positivePrompt, negativePrompt, onNameChange, onPositiveChange, onNegativeChange, onSaveCustom, selectedStyle }: any) {
    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-lg font-bold text-white mb-4">风格编辑器</h3>
                {!selectedStyle && (
                    <div className="text-sm text-gray-500 italic mb-4">
                        请先从左侧选择一个风格
                    </div>
                )}
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                    风格名称
                </label>
                <input
                    type="text"
                    value={name}
                    onChange={(e) => onNameChange(e.target.value)}
                    placeholder="例如: Cyberpunk Neon"
                    className="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-sm text-white placeholder-gray-600 focus:border-primary focus:outline-none"
                />
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                    正向提示词 (Positive Prompt)
                </label>
                <textarea
                    value={positivePrompt}
                    onChange={(e) => onPositiveChange(e.target.value)}
                    placeholder="例如: cinematic, 8k, volumetric lighting..."
                    rows={6}
                    className="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-sm text-white placeholder-gray-600 focus:border-primary focus:outline-none resize-none"
                />
                <p className="text-xs text-gray-500 mt-1">
                    将自动应用到所有资产和分镜生成
                </p>
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                    负向提示词 (Negative Prompt)
                </label>
                <textarea
                    value={negativePrompt}
                    onChange={(e) => onNegativeChange(e.target.value)}
                    placeholder="例如: low quality, blurry, cartoon..."
                    rows={4}
                    className="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-sm text-white placeholder-gray-600 focus:border-primary focus:outline-none resize-none"
                />
                <p className="text-xs text-gray-500 mt-1">
                    避免的视觉元素
                </p>
            </div>

            <div className="pt-4 border-t border-white/10">
                <button
                    onClick={onSaveCustom}
                    disabled={!name || !positivePrompt}
                    className="w-full px-4 py-2 bg-white/10 hover:bg-white/20 text-white text-sm rounded-lg font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                    <Plus size={14} />
                    保存为自定义风格
                </button>
            </div>

            {/* Preview */}
            {positivePrompt && (
                <div className="bg-white/5 border border-white/10 rounded-lg p-4">
                    <p className="text-xs text-gray-500 mb-2">生成时的最终提示词预览：</p>
                    <p className="text-xs text-blue-400 font-mono">
                        "{positivePrompt}, [用户描述]"
                    </p>
                </div>
            )}
        </div>
    );
}
