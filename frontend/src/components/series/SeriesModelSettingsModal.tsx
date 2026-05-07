"use client";

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Settings, X, Image, Video, Layout, Check, User, Building, Box, Loader2 } from 'lucide-react';
import { DEFAULT_I2V_MODEL, DEFAULT_I2I_MODEL, DEFAULT_T2I_MODEL, T2I_MODELS, I2I_MODELS, I2V_MODELS, ASPECT_RATIOS } from '@/store/projectStore';
import { api } from '@/lib/api';
import { messages } from '@/lib/i18n';

interface SeriesModelSettingsModalProps {
    isOpen: boolean;
    onClose: () => void;
    seriesId: string;
    onSaved?: () => void;
}

export default function SeriesModelSettingsModal({ isOpen, onClose, seriesId, onSaved }: SeriesModelSettingsModalProps) {
    const copy = messages.seriesPage.modelSettingsModal;
    const defaultCopy = messages.settingsPage.defaultModelSettings;
    const commonActions = messages.common.actions;
    const [t2iModel, setT2iModel] = useState(DEFAULT_T2I_MODEL);
    const [i2iModel, setI2iModel] = useState(DEFAULT_I2I_MODEL);
    const [i2vModel, setI2vModel] = useState(DEFAULT_I2V_MODEL);
    const [characterAspectRatio, setCharacterAspectRatio] = useState('9:16');
    const [sceneAspectRatio, setSceneAspectRatio] = useState('16:9');
    const [propAspectRatio, setPropAspectRatio] = useState('1:1');
    const [storyboardAspectRatio, setStoryboardAspectRatio] = useState('16:9');
    const [isLoading, setIsLoading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);

    useEffect(() => {
        if (isOpen && seriesId) {
            setIsLoading(true);
            setLoadError(null);
            api.getSeriesModelSettings(seriesId)
                .then((data) => {
                    if (data) {
                        setT2iModel(data.t2i_model || DEFAULT_T2I_MODEL);
                        setI2iModel(data.i2i_model || DEFAULT_I2I_MODEL);
                        setI2vModel(data.i2v_model || DEFAULT_I2V_MODEL);
                        setCharacterAspectRatio(data.character_aspect_ratio || '9:16');
                        setSceneAspectRatio(data.scene_aspect_ratio || '16:9');
                        setPropAspectRatio(data.prop_aspect_ratio || '1:1');
                        setStoryboardAspectRatio(data.storyboard_aspect_ratio || '16:9');
                    }
                })
                .catch((err) => {
                    console.error("Failed to load series model settings:", err);
                    setLoadError(copy.loadError);
                })
                .finally(() => setIsLoading(false));
        }
    }, [isOpen, seriesId]);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await api.updateSeriesModelSettings(seriesId, {
                t2i_model: t2iModel,
                i2i_model: i2iModel,
                i2v_model: i2vModel,
                character_aspect_ratio: characterAspectRatio,
                scene_aspect_ratio: sceneAspectRatio,
                prop_aspect_ratio: propAspectRatio,
                storyboard_aspect_ratio: storyboardAspectRatio,
            });
            onSaved?.();
            onClose();
        } catch (error) {
            console.error("Failed to save series model settings:", error);
            alert(copy.saveError);
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
                                <h2 className="text-lg font-bold text-white">{copy.title}</h2>
                                <p className="text-xs text-gray-500">{copy.subtitle}</p>
                            </div>
                        </div>
                        <button onClick={onClose} className="p-2 hover:bg-white/10 rounded-lg transition-colors">
                            <X size={20} className="text-gray-400" />
                        </button>
                    </div>

                    {/* Content */}
                    <div className="p-5 space-y-6 overflow-y-auto">
                        {isLoading ? (
                            <div className="flex items-center justify-center py-12">
                                <Loader2 size={24} className="animate-spin text-blue-400" />
                                <span className="ml-2 text-gray-400">{copy.loading}</span>
                            </div>
                        ) : loadError ? (
                            <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-4 text-sm text-red-300">
                                {loadError}
                            </div>
                        ) : (
                            <>
                                {/* Assets Section */}
                                <div className="space-y-5">
                                    <div className="flex items-center gap-2 text-sm font-bold text-white">
                                        <Image size={16} className="text-green-400" />
                                        <span>{defaultCopy.textToImage}</span>
                                    </div>

                                    <div className="space-y-2">
                                        <label className="text-xs text-gray-400">{copy.modelLabel}</label>
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

                                    <div className="grid grid-cols-3 gap-4">
                                        {([
                                            { key: 'character', label: defaultCopy.character, icon: User, value: characterAspectRatio, setter: setCharacterAspectRatio },
                                            { key: 'scene', label: defaultCopy.scene, icon: Building, value: sceneAspectRatio, setter: setSceneAspectRatio },
                                            { key: 'prop', label: defaultCopy.prop, icon: Box, value: propAspectRatio, setter: setPropAspectRatio },
                                        ] as const).map(({ key, label, icon: Icon, value, setter }) => (
                                            <div key={key} className="space-y-2">
                                                <div className="flex items-center gap-1 text-xs text-gray-400">
                                                    <Icon size={12} />
                                                    <label>{label}</label>
                                                </div>
                                                <div className="space-y-1">
                                                    {ASPECT_RATIOS.map((ratio) => (
                                                        <button
                                                            key={ratio.id}
                                                            onClick={() => setter(ratio.id)}
                                                            className={`w-full flex flex-col items-center py-2 px-2 rounded border transition-all ${value === ratio.id
                                                                ? 'border-green-500/50 bg-green-500/10'
                                                                : 'border-white/10 hover:border-white/20 bg-white/5'
                                                            }`}
                                                        >
                                                            <span className="text-xs font-medium text-white">{ratio.name}</span>
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                <div className="border-t border-white/10" />

                                {/* Storyboard Section */}
                                <div className="space-y-4">
                                    <div className="flex items-center gap-2 text-sm font-bold text-white">
                                        <Layout size={16} className="text-blue-400" />
                                        <span>{defaultCopy.storyboard}</span>
                                    </div>

                                    <div className="space-y-2">
                                        <label className="text-xs text-gray-400">{copy.modelLabel}</label>
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

                                    <div className="space-y-2">
                                        <label className="text-xs text-gray-400">{defaultCopy.storyboardAspectRatio}</label>
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
                                        <span>{defaultCopy.motion}</span>
                                    </div>
                                    <p className="text-xs text-gray-500">{copy.motionAspectRatioHint}</p>

                                    <div className="space-y-2">
                                        <label className="text-xs text-gray-400">{copy.modelLabel}</label>
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
                            </>
                        )}
                    </div>

                    {/* Footer */}
                    <div className="flex justify-end gap-3 p-5 border-t border-white/10 bg-black/20">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
                        >
                            {commonActions.cancel}
                        </button>
                        <button
                            onClick={handleSave}
                            disabled={isSaving || isLoading || !!loadError}
                            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50"
                        >
                            {isSaving ? (
                                <>
                                    <Loader2 size={16} className="animate-spin" />
                                    {copy.saving}
                                </>
                            ) : (
                                <>
                                    <Check size={16} />
                                    {copy.save}
                                </>
                            )}
                        </button>
                    </div>
                </motion.div>
            </motion.div>
        </AnimatePresence>
    );
}
