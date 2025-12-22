"use client";

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Settings, X, Image, Video, Film, Check, Layout, User, Building, Box } from 'lucide-react';
import { useProjectStore, T2I_MODELS, I2I_MODELS, I2V_MODELS, ASPECT_RATIOS } from '@/store/projectStore';
import { api } from '@/lib/api';

interface ModelSettingsModalProps {
    isOpen: boolean;
    onClose: () => void;
}

export default function ModelSettingsModal({ isOpen, onClose }: ModelSettingsModalProps) {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    const [t2iModel, setT2iModel] = useState(currentProject?.model_settings?.t2i_model || 'wan2.5-t2i-preview');
    const [i2iModel, setI2iModel] = useState(currentProject?.model_settings?.i2i_model || 'wan2.5-i2i-preview');
    const [i2vModel, setI2vModel] = useState(currentProject?.model_settings?.i2v_model || 'wan2.5-i2v-preview');
    const [characterAspectRatio, setCharacterAspectRatio] = useState(currentProject?.model_settings?.character_aspect_ratio || '9:16');
    const [sceneAspectRatio, setSceneAspectRatio] = useState(currentProject?.model_settings?.scene_aspect_ratio || '16:9');
    const [propAspectRatio, setPropAspectRatio] = useState(currentProject?.model_settings?.prop_aspect_ratio || '1:1');
    const [storyboardAspectRatio, setStoryboardAspectRatio] = useState(currentProject?.model_settings?.storyboard_aspect_ratio || '16:9');
    const [isSaving, setIsSaving] = useState(false);

    // Sync state when project changes
    useEffect(() => {
        if (currentProject?.model_settings) {
            setT2iModel(currentProject.model_settings.t2i_model || 'wan2.5-t2i-preview');
            setI2iModel(currentProject.model_settings.i2i_model || 'wan2.5-i2i-preview');
            setI2vModel(currentProject.model_settings.i2v_model || 'wan2.5-i2v-preview');
            setCharacterAspectRatio(currentProject.model_settings.character_aspect_ratio || '9:16');
            setSceneAspectRatio(currentProject.model_settings.scene_aspect_ratio || '16:9');
            setPropAspectRatio(currentProject.model_settings.prop_aspect_ratio || '1:1');
            setStoryboardAspectRatio(currentProject.model_settings.storyboard_aspect_ratio || '16:9');
        }
    }, [currentProject?.model_settings]);

    const handleSave = async () => {
        if (!currentProject) return;
        setIsSaving(true);
        try {
            const updated = await api.updateModelSettings(
                currentProject.id,
                t2iModel,
                i2iModel,
                i2vModel,
                characterAspectRatio,
                sceneAspectRatio,
                propAspectRatio,
                storyboardAspectRatio
            );
            updateProject(currentProject.id, updated);
            onClose();
        } catch (error) {
            console.error("Failed to save model settings:", error);
            alert("Failed to save settings");
        } finally {
            setIsSaving(false);
        }
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
                onClick={onClose}
            >
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-[#1a1a1a] rounded-2xl border border-white/10 w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col"
                    onClick={(e) => e.stopPropagation()}
                >
                    {/* Header */}
                    <div className="flex items-center justify-between p-5 border-b border-white/10">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-gradient-to-br from-blue-500/20 to-purple-500/20 rounded-lg">
                                <Settings size={20} className="text-blue-400" />
                            </div>
                            <div>
                                <h2 className="text-lg font-bold text-white">Generation Settings</h2>
                                <p className="text-xs text-gray-500">Configure models and aspect ratios</p>
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
                    <div className="p-5 space-y-6 overflow-y-auto">
                        {/* Assets Section */}
                        <div className="space-y-5">
                            <div className="flex items-center gap-2 text-sm font-bold text-white">
                                <Image size={16} className="text-green-400" />
                                <span>Assets (Text-to-Image)</span>
                            </div>

                            {/* T2I Model */}
                            <div className="space-y-2">
                                <label className="text-xs text-gray-400">Model</label>
                                <div className="grid grid-cols-2 gap-2">
                                    {T2I_MODELS.map((model) => (
                                        <button
                                            key={model.id}
                                            onClick={() => setT2iModel(model.id)}
                                            className={`relative flex flex-col items-start p-3 rounded-lg border transition-all text-left ${t2iModel === model.id
                                                    ? 'border-green-500/50 bg-green-500/10'
                                                    : 'border-white/10 hover:border-white/20 bg-white/5'
                                                }`}
                                        >
                                            {t2iModel === model.id && (
                                                <div className="absolute top-2 right-2">
                                                    <Check size={14} className="text-green-400" />
                                                </div>
                                            )}
                                            <span className="text-sm font-medium text-white">{model.name}</span>
                                            <span className="text-xs text-gray-500">{model.description}</span>
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {/* Asset Aspect Ratios */}
                            <div className="grid grid-cols-3 gap-4">
                                {/* Character Aspect Ratio */}
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1 text-xs text-gray-400">
                                        <User size={12} />
                                        <label>Character</label>
                                    </div>
                                    <div className="space-y-1">
                                        {ASPECT_RATIOS.map((ratio) => (
                                            <button
                                                key={ratio.id}
                                                onClick={() => setCharacterAspectRatio(ratio.id)}
                                                className={`w-full flex flex-col items-center py-2 px-2 rounded border transition-all ${characterAspectRatio === ratio.id
                                                        ? 'border-green-500/50 bg-green-500/10'
                                                        : 'border-white/10 hover:border-white/20 bg-white/5'
                                                    }`}
                                            >
                                                <span className="text-xs font-medium text-white">{ratio.name}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Scene Aspect Ratio */}
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1 text-xs text-gray-400">
                                        <Building size={12} />
                                        <label>Scene</label>
                                    </div>
                                    <div className="space-y-1">
                                        {ASPECT_RATIOS.map((ratio) => (
                                            <button
                                                key={ratio.id}
                                                onClick={() => setSceneAspectRatio(ratio.id)}
                                                className={`w-full flex flex-col items-center py-2 px-2 rounded border transition-all ${sceneAspectRatio === ratio.id
                                                        ? 'border-green-500/50 bg-green-500/10'
                                                        : 'border-white/10 hover:border-white/20 bg-white/5'
                                                    }`}
                                            >
                                                <span className="text-xs font-medium text-white">{ratio.name}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Prop Aspect Ratio */}
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1 text-xs text-gray-400">
                                        <Box size={12} />
                                        <label>Prop</label>
                                    </div>
                                    <div className="space-y-1">
                                        {ASPECT_RATIOS.map((ratio) => (
                                            <button
                                                key={ratio.id}
                                                onClick={() => setPropAspectRatio(ratio.id)}
                                                className={`w-full flex flex-col items-center py-2 px-2 rounded border transition-all ${propAspectRatio === ratio.id
                                                        ? 'border-green-500/50 bg-green-500/10'
                                                        : 'border-white/10 hover:border-white/20 bg-white/5'
                                                    }`}
                                            >
                                                <span className="text-xs font-medium text-white">{ratio.name}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="border-t border-white/10" />

                        {/* Storyboard Section */}
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 text-sm font-bold text-white">
                                <Layout size={16} className="text-blue-400" />
                                <span>Storyboard (Image-to-Image)</span>
                            </div>

                            {/* I2I Model */}
                            <div className="space-y-2">
                                <label className="text-xs text-gray-400">Model</label>
                                <div className="grid grid-cols-2 gap-2">
                                    {I2I_MODELS.map((model) => (
                                        <button
                                            key={model.id}
                                            onClick={() => setI2iModel(model.id)}
                                            className={`relative flex flex-col items-start p-3 rounded-lg border transition-all text-left ${i2iModel === model.id
                                                    ? 'border-blue-500/50 bg-blue-500/10'
                                                    : 'border-white/10 hover:border-white/20 bg-white/5'
                                                }`}
                                        >
                                            {i2iModel === model.id && (
                                                <div className="absolute top-2 right-2">
                                                    <Check size={14} className="text-blue-400" />
                                                </div>
                                            )}
                                            <span className="text-sm font-medium text-white">{model.name}</span>
                                            <span className="text-xs text-gray-500">{model.description}</span>
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {/* Storyboard Aspect Ratio */}
                            <div className="space-y-2">
                                <label className="text-xs text-gray-400">Aspect Ratio</label>
                                <div className="grid grid-cols-3 gap-2">
                                    {ASPECT_RATIOS.map((ratio) => (
                                        <button
                                            key={ratio.id}
                                            onClick={() => setStoryboardAspectRatio(ratio.id)}
                                            className={`flex flex-col items-center p-3 rounded-lg border transition-all ${storyboardAspectRatio === ratio.id
                                                    ? 'border-blue-500/50 bg-blue-500/10'
                                                    : 'border-white/10 hover:border-white/20 bg-white/5'
                                                }`}
                                        >
                                            <span className="text-sm font-medium text-white">{ratio.name}</span>
                                            <span className="text-[10px] text-gray-500">{ratio.description}</span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </div>

                        <div className="border-t border-white/10" />

                        {/* Motion Section */}
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 text-sm font-bold text-white">
                                <Video size={16} className="text-purple-400" />
                                <span>Motion (Image-to-Video)</span>
                            </div>
                            <p className="text-xs text-gray-500">Motion follows storyboard aspect ratio automatically.</p>

                            {/* I2V Model */}
                            <div className="space-y-2">
                                <label className="text-xs text-gray-400">Model</label>
                                <div className="grid grid-cols-2 gap-2">
                                    {I2V_MODELS.map((model) => (
                                        <button
                                            key={model.id}
                                            onClick={() => setI2vModel(model.id)}
                                            className={`relative flex flex-col items-start p-3 rounded-lg border transition-all text-left ${i2vModel === model.id
                                                    ? 'border-purple-500/50 bg-purple-500/10'
                                                    : 'border-white/10 hover:border-white/20 bg-white/5'
                                                }`}
                                        >
                                            {i2vModel === model.id && (
                                                <div className="absolute top-2 right-2">
                                                    <Check size={14} className="text-purple-400" />
                                                </div>
                                            )}
                                            <span className="text-sm font-medium text-white">{model.name}</span>
                                            <span className="text-xs text-gray-500">{model.description}</span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Footer */}
                    <div className="flex justify-end gap-3 p-5 border-t border-white/10 bg-black/20">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleSave}
                            disabled={isSaving}
                            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50"
                        >
                            {isSaving ? (
                                <>
                                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />
                                    Saving...
                                </>
                            ) : (
                                <>
                                    <Check size={16} />
                                    Save Settings
                                </>
                            )}
                        </button>
                    </div>
                </motion.div>
            </motion.div>
        </AnimatePresence>
    );
}
