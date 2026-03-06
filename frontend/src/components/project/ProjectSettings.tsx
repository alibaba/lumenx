"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Palette, Info } from "lucide-react";
import { api } from "@/lib/api";

interface ProjectSettingsProps {
    project: any;
    isOpen: boolean;
    onClose: () => void;
    onUpdate: (updatedProject: any) => void;
}

const STYLE_PRESETS = [
    { value: "realistic", label: "Realistic (写实)", description: "Photorealistic, detailed imagery" },
    { value: "cartoon", label: "Cartoon (卡通)", description: "Animated, colorful style" },
    { value: "anime", label: "Anime (动漫)", description: "Japanese animation style" },
    { value: "cyberpunk", label: "Cyberpunk (赛博朋克)", description: "Futuristic, neon-lit aesthetic" },
    { value: "watercolor", label: "Watercolor (水彩)", description: "Soft, painterly look" },
    { value: "sketch", label: "Sketch (素描)", description: "Hand-drawn pencil style" },
    { value: "comic", label: "Comic Book (漫画)", description: "Bold outlines, halftone shading" },
    { value: "cinematic", label: "Cinematic (电影)", description: "Film-like, dramatic lighting" },
];

export default function ProjectSettings({ project, isOpen, onClose, onUpdate }: ProjectSettingsProps) {
    const [stylePreset, setStylePreset] = useState(project?.style_preset || "realistic");
    const [stylePrompt, setStylePrompt] = useState(project?.style_prompt || "");
    const [isSaving, setIsSaving] = useState(false);

    useEffect(() => {
        if (project) {
            setStylePreset(project.style_preset || "realistic");
            setStylePrompt(project.style_prompt || "");
        }
    }, [project]);

    const handleSave = async () => {
        if (!project) return;

        setIsSaving(true);
        try {
            // Add timeout protection
            const timeoutPromise = new Promise((_, reject) =>
                setTimeout(() => reject(new Error('Request timeout')), 10000)
            );

            const updatePromise = api.updateProjectStyle(project.id, stylePreset, stylePrompt || undefined);

            const updated = await Promise.race([updatePromise, timeoutPromise]) as any;
            onUpdate(updated);
            onClose();
        } catch (error: any) {
            console.error("Failed to update style:", error);
            const errorMessage = error?.response?.data?.detail || error?.message || "更新风格失败";
            alert(`更新风格失败: ${errorMessage}`);
        } finally {
            setIsSaving(false);
        }
    };

    const selectedStyle = STYLE_PRESETS.find(s => s.value === stylePreset);

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
                    onClick={onClose}
                >
                    <motion.div
                        initial={{ scale: 0.9, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        exit={{ scale: 0.9, opacity: 0 }}
                        className="bg-[#1a1a1a] border border-white/10 rounded-2xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Header */}
                        <div className="flex items-center justify-between p-6 border-b border-white/10">
                            <div className="flex items-center gap-3">
                                <div className="w-10 h-10 rounded-xl bg-primary/20 flex items-center justify-center">
                                    <Palette className="text-primary" size={20} />
                                </div>
                                <div>
                                    <h2 className="text-xl font-bold text-white">项目风格设置</h2>
                                    <p className="text-xs text-gray-500">Project Style Settings</p>
                                </div>
                            </div>
                            <button
                                onClick={onClose}
                                className="p-2 hover:bg-white/10 rounded-lg transition-colors"
                            >
                                <X size={20} className="text-gray-400" />
                            </button>
                        </div>

                        {/* Content */}
                        <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
                            {/* Info Banner */}
                            <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-4 flex gap-3">
                                <Info size={18} className="text-blue-400 flex-shrink-0 mt-0.5" />
                                <div className="text-sm text-gray-300">
                                    <p className="font-semibold mb-1">全局风格设置</p>
                                    <p className="text-xs text-gray-400">
                                        此风格将自动应用到所有资产生成和故事板渲染。您仍可在生成时单独覆盖特定项目的风格。
                                    </p>
                                </div>
                            </div>

                            {/* Style Preset Selector */}
                            <div>
                                <label className="block text-sm font-medium text-gray-300 mb-3">
                                    风格预设 <span className="text-gray-600">(Style Preset)</span>
                                </label>
                                <div className="grid grid-cols-2 gap-3">
                                    {STYLE_PRESETS.map((style) => (
                                        <button
                                            key={style.value}
                                            onClick={() => setStylePreset(style.value)}
                                            className={`p-4 rounded-xl border-2 text-left transition-all ${stylePreset === style.value
                                                ? "bg-primary/20 border-primary shadow-lg shadow-primary/20"
                                                : "bg-white/5 border-white/10 hover:border-white/20 hover:bg-white/10"
                                                }`}
                                        >
                                            <div className="font-semibold text-sm mb-1">{style.label}</div>
                                            <div className="text-xs text-gray-500">{style.description}</div>
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {/* Custom Style Prompt */}
                            <div>
                                <label className="block text-sm font-medium text-gray-300 mb-2">
                                    自定义风格描述 <span className="text-gray-600">(Optional)</span>
                                </label>
                                <textarea
                                    value={stylePrompt}
                                    onChange={(e) => setStylePrompt(e.target.value)}
                                    placeholder="例如: vibrant colors, soft lighting, dreamlike atmosphere"
                                    className="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-sm text-white placeholder-gray-600 focus:border-primary focus:outline-none resize-none"
                                    rows={3}
                                />
                                <p className="text-xs text-gray-600 mt-1">
                                    此描述将与风格预设一起应用（可选）
                                </p>
                            </div>

                            {/* Preview */}
                            {selectedStyle && (
                                <div className="bg-white/5 border border-white/10 rounded-lg p-4">
                                    <p className="text-xs text-gray-500 mb-2">生成时将应用的风格：</p>
                                    <p className="text-sm text-blue-400 italic">
                                        &quot;{selectedStyle.value} style{stylePrompt ? `, ${stylePrompt}` : ""}&quot;
                                    </p>
                                </div>
                            )}
                        </div>

                        {/* Footer */}
                        <div className="flex items-center justify-end gap-3 p-6 border-t border-white/10">
                            <button
                                onClick={onClose}
                                className="px-4 py-2 text-sm text-gray-400 hover:text-white hover:bg-white/10 rounded-lg transition-colors"
                            >
                                取消
                            </button>
                            <button
                                onClick={handleSave}
                                disabled={isSaving}
                                className="px-6 py-2 text-sm bg-primary hover:bg-primary/90 text-white rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                            >
                                {isSaving ? "保存中..." : "保存设置"}
                            </button>
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
