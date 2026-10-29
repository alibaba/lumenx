"use client";

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Settings, X, Image, Video, Film, Check, Layout, User, Building, Box } from 'lucide-react';
import { useProjectStore, IMAGE_MODELS, I2V_MODELS, ASPECT_RATIOS } from '@/store/projectStore';
import { resolveModelSettings, VIDEO_R2V_MODELS, DEFAULT_R2V_MODEL_ID } from '@/lib/modelCatalog';
import { api } from '@/lib/api';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { ProjectReadbackError, readProjectOnce } from '@/lib/projectReadback';
import { frameCommandSnapshot } from '@/lib/frameCommandSnapshot';
import { useTranslations } from "next-intl";
import GroupedModelGrid from '@/components/common/GroupedModelGrid';

interface ModelSettingsModalProps {
    isOpen: boolean;
    onClose: () => void;
}

export default function ModelSettingsModal({ isOpen, onClose }: ModelSettingsModalProps) {
    const currentProject = useProjectStore((state) => state.currentProject);
    const t = useTranslations("models");
    const tc = useTranslations("common");
    const tp = useTranslations("project");
    const tf = useTranslations("frameCommands");
    const resolvedSettings = resolveModelSettings(currentProject?.model_settings, 'project_settings');

    const [t2iModel, setT2iModel] = useState(resolvedSettings.t2i_model);
    const [i2iModel, setI2iModel] = useState(resolvedSettings.i2i_model);
    const [i2vModel, setI2vModel] = useState(resolvedSettings.i2v_model);
    // R2V default for the project. Storyboard's R2V tab seeds from
    // here on first mount; per-storyboard localStorage override still
    // wins. Falls back to catalog's DEFAULT_R2V_MODEL_ID when the
    // project has no r2v_model set yet (older projects).
    const [r2vModel, setR2vModel] = useState(resolvedSettings.r2v_model || DEFAULT_R2V_MODEL_ID);
    const [characterAspectRatio, setCharacterAspectRatio] = useState(resolvedSettings.character_aspect_ratio);
    const [sceneAspectRatio, setSceneAspectRatio] = useState(resolvedSettings.scene_aspect_ratio);
    const [propAspectRatio, setPropAspectRatio] = useState(resolvedSettings.prop_aspect_ratio);
    const [storyboardAspectRatio, setStoryboardAspectRatio] = useState(resolvedSettings.storyboard_aspect_ratio);
    const [isSaving, setIsSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const sessionRef = useRef(0);
    const savingRef = useRef(false);
    const draftDirtyRef = useRef(false);
    const pendingReadRevisionRef = useRef<number | undefined>(undefined);
    const initialRuntime = getRuntimeEnvironment();
    const latestRef = useRef({
        isOpen,
        projectId: currentProject?.id,
        environment: currentProject?.readEnvironment,
        generation: initialRuntime && initialRuntime.key === currentProject?.readEnvironment ? initialRuntime.generation : undefined,
        modelSettings: currentProject?.model_settings,
    });

    const hydrateSettings = useCallback((settings: typeof resolvedSettings) => {
        setT2iModel(settings.t2i_model);
        setI2iModel(settings.i2i_model);
        setI2vModel(settings.i2v_model);
        setR2vModel(settings.r2v_model || DEFAULT_R2V_MODEL_ID);
        setCharacterAspectRatio(settings.character_aspect_ratio);
        setSceneAspectRatio(settings.scene_aspect_ratio);
        setPropAspectRatio(settings.prop_aspect_ratio);
        setStoryboardAspectRatio(settings.storyboard_aspect_ratio);
    }, []);

    useEffect(() => {
        const runtime = getRuntimeEnvironment();
        latestRef.current = {
            isOpen,
            projectId: currentProject?.id,
            environment: currentProject?.readEnvironment,
            generation: runtime && runtime.key === currentProject?.readEnvironment ? runtime.generation : undefined,
            modelSettings: currentProject?.model_settings,
        };
    });

    const isCurrentSession = (token: number, projectId: string) => (
        sessionRef.current === token
        && latestRef.current.isOpen
        && latestRef.current.projectId === projectId
        && !!latestRef.current.environment
        && isCurrentRuntimeEnvironment(latestRef.current.environment, latestRef.current.generation)
    );

    const markDirty = () => { draftDirtyRef.current = true; };

    // A modal session is scoped to open/project/environment, not ordinary project snapshots.
    useEffect(() => {
        sessionRef.current += 1;
        savingRef.current = false;
        draftDirtyRef.current = false;
        pendingReadRevisionRef.current = undefined;
        setIsSaving(false);
        setSaveError(null);
        hydrateSettings(resolveModelSettings(latestRef.current.modelSettings, 'project_settings'));
        return () => {
            sessionRef.current += 1;
            savingRef.current = false;
        };
    }, [isOpen, currentProject?.id, currentProject?.readEnvironment, hydrateSettings]);

    // Same-project refreshes may update the form only while the user has no draft or in-flight save.
    useEffect(() => {
        if (!isOpen || draftDirtyRef.current || savingRef.current) return;
        hydrateSettings(resolveModelSettings(currentProject?.model_settings, 'project_settings'));
    }, [isOpen, currentProject?.id, currentProject?.model_settings, hydrateSettings]);

    const handleReadAgain = async () => {
        if (!currentProject || savingRef.current) return;
        savingRef.current = true;
        setIsSaving(true);
        const projectId = currentProject.id;
        const token = sessionRef.current;
        setSaveError(null);
        try {
            await readProjectOnce(projectId, pendingReadRevisionRef.current, { isCurrent: () => isCurrentSession(token, projectId) });
            if (isCurrentSession(token, projectId)) {
                pendingReadRevisionRef.current = undefined;
                draftDirtyRef.current = false;
                onClose();
            }
        } catch {
            if (isCurrentSession(token, projectId)) setSaveError(tf("savedReadFailed"));
        } finally {
            if (isCurrentSession(token, projectId)) setIsSaving(false);
            if (sessionRef.current === token) savingRef.current = false;
        }
    };

    const handleSave = async () => {
        if (!currentProject || savingRef.current || pendingReadRevisionRef.current !== undefined) return;
        const projectId = currentProject.id;
        const token = sessionRef.current;
        savingRef.current = true;
        setIsSaving(true);
        setSaveError(null);
        try {
            const updated = await api.updateModelSettings(
                projectId,
                t2iModel,
                i2iModel,
                i2vModel,
                characterAspectRatio,
                sceneAspectRatio,
                propAspectRatio,
                storyboardAspectRatio,
                undefined, // imageModel — managed via t2i/i2i for now
                r2vModel,
            );
            if (!isCurrentSession(token, projectId)) return;
            const ack = frameCommandSnapshot(projectId, updated);
            pendingReadRevisionRef.current = ack.revision;
            if (!isCurrentSession(token, projectId)) return;
            await readProjectOnce(projectId, ack.revision, { isCurrent: () => isCurrentSession(token, projectId) });
            if (isCurrentSession(token, projectId)) {
                pendingReadRevisionRef.current = undefined;
                draftDirtyRef.current = false;
                onClose();
            }
        } catch (error) {
            console.error("Failed to save model settings:", error);
            if (isCurrentSession(token, projectId)) {
                setSaveError(error instanceof ProjectReadbackError ? tf("savedReadFailed") : t("saveSettingsFailed"));
            }
        } finally {
            if (isCurrentSession(token, projectId)) setIsSaving(false);
            if (sessionRef.current === token) savingRef.current = false;
        }
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 bg-overlay backdrop-blur-sm flex items-center justify-center p-4"
                onClick={onClose}
            >
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-elevated rounded-2xl border border-glass-border w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col"
                    onClick={(e) => e.stopPropagation()}
                >
                    {/* Header */}
                    <div className="flex items-center justify-between p-5 border-b border-glass-border">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-gradient-to-br from-blue-500/20 to-purple-500/20 rounded-lg">
                                <Settings size={20} className="text-blue-400" />
                            </div>
                            <div>
                                <h2 className="text-lg font-bold text-foreground">{t("genSettings")}</h2>
                                <p className="text-xs text-text-muted">{t("genSettingsDesc")}</p>
                            </div>
                        </div>
                        <button
                            onClick={onClose}
                            className="p-2 hover:bg-hover-bg rounded-lg transition-colors"
                        >
                            <X size={20} className="text-text-secondary" />
                        </button>
                    </div>

                    {/* Content */}
                    <div className="p-5 space-y-6 overflow-y-auto">
                        {/* Assets Section */}
                        <div className="space-y-5">
                            <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                <Image size={16} className="text-green-400" />
                                <span>{t("assetsT2I")}</span>
                            </div>

                            {/* T2I Model */}
                            <div className="space-y-2">
                                <label className="text-xs text-text-secondary">{t("model")}</label>
                                <GroupedModelGrid
                                    models={IMAGE_MODELS}
                                    selectedId={t2iModel}
                                    onSelect={(id) => { markDirty(); setT2iModel(id); }}
                                />
                            </div>

                            {/* Asset Aspect Ratios */}
                            <div className="grid grid-cols-3 gap-4">
                                {/* Character Aspect Ratio */}
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1 text-xs text-text-secondary">
                                        <User size={12} />
                                        <label>{t("character")}</label>
                                    </div>
                                    <div className="space-y-1">
                                        {ASPECT_RATIOS.map((ratio) => (
                                            <button
                                                key={ratio.id}
                                                onClick={() => { markDirty(); setCharacterAspectRatio(ratio.id); }}
                                                className={`w-full flex flex-col items-center py-1.5 px-2 rounded border transition-all ${characterAspectRatio === ratio.id
                                                        ? 'border-green-500/50 bg-green-500/10'
                                                        : 'border-glass-border hover:border-glass-border bg-glass'
                                                    }`}
                                            >
                                                <span className="text-xs font-medium text-foreground">{ratio.name}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Scene Aspect Ratio */}
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1 text-xs text-text-secondary">
                                        <Building size={12} />
                                        <label>{t("scene")}</label>
                                    </div>
                                    <div className="space-y-1">
                                        {ASPECT_RATIOS.map((ratio) => (
                                            <button
                                                key={ratio.id}
                                                onClick={() => { markDirty(); setSceneAspectRatio(ratio.id); }}
                                                className={`w-full flex flex-col items-center py-1.5 px-2 rounded border transition-all ${sceneAspectRatio === ratio.id
                                                        ? 'border-green-500/50 bg-green-500/10'
                                                        : 'border-glass-border hover:border-glass-border bg-glass'
                                                    }`}
                                            >
                                                <span className="text-xs font-medium text-foreground">{ratio.name}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Prop Aspect Ratio */}
                                <div className="space-y-2">
                                    <div className="flex items-center gap-1 text-xs text-text-secondary">
                                        <Box size={12} />
                                        <label>{t("prop")}</label>
                                    </div>
                                    <div className="space-y-1">
                                        {ASPECT_RATIOS.map((ratio) => (
                                            <button
                                                key={ratio.id}
                                                onClick={() => { markDirty(); setPropAspectRatio(ratio.id); }}
                                                className={`w-full flex flex-col items-center py-1.5 px-2 rounded border transition-all ${propAspectRatio === ratio.id
                                                        ? 'border-green-500/50 bg-green-500/10'
                                                        : 'border-glass-border hover:border-glass-border bg-glass'
                                                    }`}
                                            >
                                                <span className="text-xs font-medium text-foreground">{ratio.name}</span>
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="border-t border-glass-border" />

                        {/* Storyboard Section */}
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                <Layout size={16} className="text-blue-400" />
                                <span>{t("storyboardI2I")}</span>
                            </div>

                            {/* I2I Model */}
                            <div className="space-y-2">
                                <label className="text-xs text-text-secondary">{t("model")}</label>
                                <GroupedModelGrid
                                    models={IMAGE_MODELS}
                                    selectedId={i2iModel}
                                    onSelect={(id) => { markDirty(); setI2iModel(id); }}
                                />
                            </div>

                            {/* Storyboard Aspect Ratio */}
                            <div className="space-y-2">
                                <label className="text-xs text-text-secondary">{t("aspectRatio")}</label>
                                <div className="grid grid-cols-3 gap-2">
                                    {ASPECT_RATIOS.map((ratio) => (
                                        <button
                                            key={ratio.id}
                                            onClick={() => { markDirty(); setStoryboardAspectRatio(ratio.id); }}
                                            className={`flex flex-col items-center p-3 rounded-lg border transition-all ${storyboardAspectRatio === ratio.id
                                                    ? 'border-blue-500/50 bg-blue-500/10'
                                                    : 'border-glass-border hover:border-glass-border bg-glass'
                                                }`}
                                        >
                                            <span className="text-sm font-medium text-foreground">{ratio.name}</span>
                                            <span className="text-[0.625rem] text-text-muted">{ratio.description}</span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </div>

                        <div className="border-t border-glass-border" />

                        {/* Motion Section */}
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                <Video size={16} className="text-purple-400" />
                                <span>{t("motionI2V")}</span>
                            </div>
                            <p className="text-xs text-text-muted">{t("motionFollowsAR")}</p>

                            {/* I2V Model */}
                            <div className="space-y-2">
                                <label className="text-xs text-text-secondary">{t("model")}</label>
                                <GroupedModelGrid
                                    models={I2V_MODELS}
                                    selectedId={i2vModel}
                                    onSelect={(id) => { markDirty(); setI2vModel(id); }}
                                />
                            </div>
                        </div>

                        <div className="border-t border-glass-border" />

                        {/* R2V Section — Reference-to-Video. Project default;
                            Storyboard R2V tab seeds from here on first mount,
                            per-storyboard localStorage override still wins. */}
                        <div className="space-y-4">
                            <div className="flex items-center gap-2 text-sm font-bold text-foreground">
                                <Film size={16} className="text-pink-400" />
                                <span>R2V · 参考生视频</span>
                            </div>
                            <p className="text-xs text-text-muted">
                                项目级 R2V 模型默认值。Storyboard 的 R2V tab 进入时按此初始化；用户在 storyboard 内的临时切换会保存在本地、不影响这里。
                            </p>

                            <div className="space-y-2">
                                <label className="text-xs text-text-secondary">{t("model")}</label>
                                <GroupedModelGrid
                                    models={VIDEO_R2V_MODELS}
                                    selectedId={r2vModel}
                                    onSelect={(id) => { markDirty(); setR2vModel(id); }}
                                />
                            </div>
                        </div>
                    </div>

                    {/* Footer */}
                    {saveError && (
                        <div role="alert" className="mx-5 mb-3 bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-sm text-red-300 flex items-center justify-between gap-3">
                            <span>{saveError}</span>
                            {saveError === tf("savedReadFailed") && (
                                <button type="button" onClick={handleReadAgain} className="text-xs text-red-100 underline whitespace-nowrap">
                                    {tp("readAgain")}
                                </button>
                            )}
                        </div>
                    )}
                    <div className="flex justify-end gap-3 p-5 border-t border-glass-border bg-surface">
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-sm text-text-secondary hover:text-foreground transition-colors"
                        >
                            {tc("cancel")}
                        </button>
                        <button
                            onClick={handleSave}
                            disabled={isSaving || pendingReadRevisionRef.current !== undefined}
                            className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50"
                        >
                            {isSaving ? (
                                <>
                                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />
                                    {t("saving")}
                                </>
                            ) : (
                                <>
                                    <Check size={16} />
                                    {t("saveSettings")}
                                </>
                            )}
                        </button>
                    </div>
                </motion.div>
            </motion.div>
        </AnimatePresence>
    );
}
