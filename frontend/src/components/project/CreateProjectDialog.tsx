"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Zap, Film } from "lucide-react";
import { useTranslations } from "next-intl";
import { useProjectStore } from "@/store/projectStore";


interface CreateProjectDialogProps {
    isOpen: boolean;
    onClose: () => void;
    seriesId?: string;
    seriesTitle?: string;
}

export default function CreateProjectDialog({ isOpen, onClose, seriesId, seriesTitle }: CreateProjectDialogProps) {
    const [title, setTitle] = useState("");
    const [text, setText] = useState("");
    const [workflowMode, setWorkflowMode] = useState<"r2v" | "i2v_legacy">("r2v");
    const [isCreating, setIsCreating] = useState(false);
    const createProject = useProjectStore((state) => state.createProject);
    const mountedRef = useRef(true);
    const openRef = useRef(isOpen);
    const sessionRef = useRef(0);
    const creatingRef = useRef(false);
    const t = useTranslations("project");
    const tc = useTranslations("common");

    useEffect(() => {
        openRef.current = isOpen;
        sessionRef.current += 1;
    }, [isOpen]);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            openRef.current = false;
            sessionRef.current += 1;
        };
    }, []);

    const handleCreate = async () => {
        if (!title) {
            alert(t("titleRequired"));
            return;
        }

        if (creatingRef.current) return;
        creatingRef.current = true;
        const session = sessionRef.current;
        setIsCreating(true);
        try {
            const project = await createProject(title, text, true, workflowMode, seriesId);
            if (mountedRef.current && openRef.current && sessionRef.current === session) {
                // Use hash-based routing to match the app's routing structure.
                window.location.hash = `#/project/${project.id}`;
                onClose();
            }
        } catch (error: any) {
            if (mountedRef.current && openRef.current && sessionRef.current === session) {
                const errorMessage = error?.response?.data?.detail || error?.message || t("checkBackend");
                alert(t("createFailed", { error: errorMessage }));
            }
        } finally {
            creatingRef.current = false;
            if (mountedRef.current) setIsCreating(false);
        }
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="fixed inset-0 bg-overlay backdrop-blur-sm z-50 flex items-center justify-center p-6"
                    onClick={onClose}
                >
                    <motion.div
                        initial={{ scale: 0.9, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        exit={{ scale: 0.9, opacity: 0 }}
                        className="bg-elevated border border-border shadow-2xl p-8 rounded-2xl w-full max-w-4xl"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-center justify-between mb-6">
                            <div>
                                <h2 className="text-2xl font-display font-bold text-foreground">{t("createTitle")}</h2>
                                {seriesId && (
                                    <div className="mt-1 font-mono text-[0.6875rem] uppercase tracking-wider text-primary">
                                        {t("series")} · {seriesTitle}
                                    </div>
                                )}
                            </div>
                            <button
                                onClick={onClose}
                                className="p-2 rounded-lg hover:bg-hover-bg text-text-secondary hover:text-foreground transition-colors"
                            >
                                <X size={20} />
                            </button>
                        </div>

                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-foreground mb-2">
                                    {t("projectTitle")}
                                </label>
                                <input
                                    type="text"
                                    value={title}
                                    onChange={(e) => setTitle(e.target.value)}
                                    placeholder={t("projectTitlePlaceholder")}
                                    className="glass-input w-full"
                                />
                            </div>

                            {/* Workflow Mode Selection */}
                            <div>
                                <label className="block text-sm font-medium text-foreground mb-2">
                                    {t("workflowMode")}
                                </label>
                                <div className="grid grid-cols-2 gap-3">
                                    <button
                                        type="button"
                                        onClick={() => setWorkflowMode("r2v")}
                                        className={`relative p-4 rounded-xl border-2 text-left transition-all ${
                                            workflowMode === "r2v"
                                                ? "border-primary bg-primary/10"
                                                : "border-border bg-surface hover:border-text-muted"
                                        }`}
                                    >
                                        <div className="flex items-center gap-2 mb-1.5">
                                            <Zap size={16} className={workflowMode === "r2v" ? "text-primary" : "text-text-secondary"} />
                                            <span className="font-semibold text-sm text-foreground">{t("workflowR2V")}</span>
                                        </div>
                                        <p className="text-xs text-text-secondary leading-relaxed">
                                            {t("workflowR2VDesc")}
                                        </p>
                                        {workflowMode === "r2v" && (
                                            <span className="absolute top-2 right-2 text-[0.625rem] font-medium text-primary bg-primary/20 px-1.5 py-0.5 rounded">
                                                {tc("recommended")}
                                            </span>
                                        )}
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setWorkflowMode("i2v_legacy")}
                                        className={`relative p-4 rounded-xl border-2 text-left transition-all ${
                                            workflowMode === "i2v_legacy"
                                                ? "border-primary bg-primary/10"
                                                : "border-border bg-surface hover:border-text-muted"
                                        }`}
                                    >
                                        <div className="flex items-center gap-2 mb-1.5">
                                            <Film size={16} className={workflowMode === "i2v_legacy" ? "text-primary" : "text-text-secondary"} />
                                            <span className="font-semibold text-sm text-foreground">{t("workflowI2V")}</span>
                                        </div>
                                        <p className="text-xs text-text-secondary leading-relaxed">
                                            {t("workflowI2VDesc")}
                                        </p>
                                    </button>
                                </div>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-foreground mb-2">
                                    {t("scriptContent")}
                                </label>
                                <textarea
                                    value={text}
                                    onChange={(e) => setText(e.target.value)}
                                    placeholder={t("scriptPlaceholder")}
                                    rows={8}
                                    className="glass-input w-full resize-none font-mono text-sm"
                                />
                            </div>

                            <div className="flex gap-3 pt-4">
                                <button
                                    onClick={onClose}
                                    className="flex-1 glass-button"
                                >
                                    {tc("cancel")}
                                </button>
                                <button
                                    onClick={handleCreate}
                                    disabled={isCreating || !title}
                                    className="flex-1 bg-primary hover:bg-primary/90 text-foreground px-6 py-3 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    {isCreating ? t("creating") : t("createProject")}
                                </button>
                            </div>
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
