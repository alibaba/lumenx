"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { RefreshCw, Download, Plus } from "lucide-react";

import { api } from "@/lib/api";
import { messages } from "@/lib/i18n";
import { useProjectStore } from "@/store/projectStore";
import { getAssetUrl } from "@/lib/utils";

interface Asset {
    id: string;
    type: "char" | "bg" | "prop";
    url: string;
    title: string;
}

interface AssetGridProps {
    projectId: string | null;
}

export default function AssetGrid({ projectId }: AssetGridProps) {
    const copy = messages.modules.assetGrid;
    const commonLabels = messages.common.labels;
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    const [isGenerating, setIsGenerating] = useState(false);

    // Initialize assets from current project
    const [assets, setAssets] = useState<Asset[]>(() => {
        if (!currentProject) return [];
        return [
            ...currentProject.characters.map((c: any) => ({
                id: c.id,
                type: "char" as const,
                url: getAssetUrl(c.image_url),
                title: c.name
            })),
            ...currentProject.scenes.map((s: any) => ({
                id: s.id,
                type: "bg" as const,
                url: getAssetUrl(s.image_url),
                title: s.name
            }))
        ];
    });

    // Update assets when project changes
    useEffect(() => {
        if (currentProject) {
            const newAssets: Asset[] = [
                ...currentProject.characters.map((c: any) => ({
                    id: c.id,
                    type: "char" as const,
                    url: getAssetUrl(c.image_url),
                    title: c.name
                })),
                ...currentProject.scenes.map((s: any) => ({
                    id: s.id,
                    type: "bg" as const,
                    url: getAssetUrl(s.image_url),
                    title: s.name
                }))
            ];
            setAssets(newAssets);
        }
    }, [currentProject?.id, currentProject?.characters, currentProject?.scenes]);

    const handleGenerate = async () => {
        if (!projectId) {
            // Try to find the latest project or alert user
            alert(copy.scriptRequired);
            return;
        }
        setIsGenerating(true);
        try {
            const project = await api.generateAssets(projectId);
            // Transform backend data
            const newAssets: Asset[] = [
                ...project.characters.map((c: any) => ({
                    id: c.id,
                    type: "char" as const,
                    url: getAssetUrl(c.image_url),
                    title: c.name
                })),
                ...project.scenes.map((s: any) => ({
                    id: s.id,
                    type: "bg" as const,
                    url: getAssetUrl(s.image_url),
                    title: s.name
                }))
            ];
            setAssets(newAssets);

            // Update project in store
            if (currentProject) {
                updateProject(currentProject.id, {
                    characters: project.characters,
                    scenes: project.scenes,
                });
            }
        } catch (error) {
            console.error("Failed to generate assets:", error);
        } finally {
            setIsGenerating(false);
        }
    };

    return (
        <div className="h-full p-6 overflow-y-auto">
            <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-display font-bold text-white">{copy.title}</h2>
                <div className="flex gap-2">
                    <button className="glass-button text-xs flex items-center gap-2">
                        <Plus size={14} /> {copy.upload}
                    </button>
                    <button
                        onClick={handleGenerate}
                        disabled={isGenerating}
                        className="glass-button text-xs flex items-center gap-2 text-primary border-primary/30"
                    >
                        <RefreshCw size={14} className={isGenerating ? "animate-spin" : ""} />
                        {isGenerating ? copy.generatingAll : copy.generateAll}
                    </button>
                </div>
            </div>

            <div className="columns-2 md:columns-3 lg:columns-4 gap-4 space-y-4">
                {assets.map((asset, i) => (
                    <motion.div
                        key={asset.id}
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: i * 0.05 }}
                        className="break-inside-avoid group relative rounded-xl overflow-hidden bg-white/5 border border-white/5 hover:border-primary/50 transition-all duration-300"
                    >
                        {asset.url ? (
                            <img
                                src={getAssetUrl(asset.url)}
                                alt={asset.title}
                                className="w-full h-auto object-cover transition-transform duration-500 group-hover:scale-105"
                            />
                        ) : (
                            <div className="w-full h-48 bg-white/5 flex items-center justify-center text-xs text-gray-500">
                                {copy.noImage}
                            </div>
                        )}

                        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex flex-col justify-end p-4">
                            <h3 className="text-sm font-bold text-white">{asset.title}</h3>
                            <div className="flex items-center justify-between mt-2">
                                <span className="text-[10px] font-mono bg-white/10 px-2 py-1 rounded">
                                    {asset.type === "char"
                                        ? commonLabels.character
                                        : asset.type === "bg"
                                            ? commonLabels.scene
                                            : commonLabels.prop}
                                </span>
                                <button className="p-1.5 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors">
                                    <Download size={12} />
                                </button>
                            </div>
                        </div>
                    </motion.div>
                ))}
            </div>
        </div>
    );
}
