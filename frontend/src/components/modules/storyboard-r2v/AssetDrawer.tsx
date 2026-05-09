"use client";

import { motion, AnimatePresence } from "framer-motion";
import { X, User, MapPin, Package } from "lucide-react";
import { useTranslations } from "next-intl";
import {
    getStoryboardR2VAssetReadiness,
    type StoryboardR2VAssetLike,
    type StoryboardR2VCharacterLike,
} from "@/lib/storyboardR2VAssets";

interface AssetDrawerProps {
    isOpen: boolean;
    onClose: () => void;
    characters: StoryboardR2VCharacterLike[];
    scenes: StoryboardR2VAssetLike[];
    props: StoryboardR2VAssetLike[];
    onSelectAsset: (type: string, name: string) => void;
}

function getImageVariantUrl(asset?: {
    selected_id?: string | null;
    selected_image_id?: string | null;
    variants?: Array<{ id?: string; url?: string }>;
    image_variants?: Array<{ id?: string; url?: string }>;
}): string | null {
    if (!asset) return null;
    const selectedId = asset.selected_image_id || asset.selected_id;
    const variants = asset.image_variants || asset.variants || [];
    const selected = selectedId ? variants.find((variant) => variant.id === selectedId) : undefined;
    return selected?.url || variants[0]?.url || null;
}

function getAssetThumbnail(
    item: StoryboardR2VCharacterLike | StoryboardR2VAssetLike,
    type: "character" | "scene" | "prop"
): string | null {
    if (type === "character") {
        const character = item as StoryboardR2VCharacterLike;
        return (
            getImageVariantUrl(character.full_body) ||
            getImageVariantUrl(character.full_body_asset) ||
            getImageVariantUrl(character.head_shot) ||
            getImageVariantUrl(character.headshot_asset) ||
            character.full_body_image_url ||
            character.avatar_url ||
            null
        );
    }

    const asset = item as StoryboardR2VAssetLike;
    return getImageVariantUrl(asset.image_asset) || asset.image_url || null;
}

function AssetReadinessBadges({ hasImageRef, hasVideoRef }: { hasImageRef: boolean; hasVideoRef: boolean }) {
    return (
        <div className="flex items-center gap-1">
            <span
                className={`h-1.5 w-1.5 rounded-full ${hasImageRef ? "bg-emerald-400" : "bg-white/20"}`}
                title={hasImageRef ? "Image ref ready" : "Missing image ref"}
            />
            <span
                className={`h-1.5 w-1.5 rounded-full ${hasVideoRef ? "bg-sky-400" : "bg-white/20"}`}
                title={hasVideoRef ? "Motion ref ready" : "Missing motion ref"}
            />
        </div>
    );
}

export default function AssetDrawer({ isOpen, onClose, characters, scenes, props, onSelectAsset }: AssetDrawerProps) {
    const t = useTranslations("storyboardR2V");

    const hasAnyAssets = characters.length > 0 || scenes.length > 0 || props.length > 0;

    return (
        <AnimatePresence>
            {isOpen && (
                <>
                    {/* Backdrop */}
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-black/30 z-40"
                        onClick={onClose}
                    />
                    {/* Drawer */}
                    <motion.div
                        initial={{ x: "100%" }}
                        animate={{ x: 0 }}
                        exit={{ x: "100%" }}
                        transition={{ type: "spring", damping: 25, stiffness: 300 }}
                        className="fixed inset-y-0 right-0 w-80 z-50 bg-[#0f0f14] border-l border-white/[0.06] shadow-2xl flex flex-col"
                    >
                        {/* Header */}
                        <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] bg-white/[0.02] backdrop-blur-xl shrink-0 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                            <h3 className="text-sm font-semibold text-foreground">{t("assetLibrary")}</h3>
                            <button
                                onClick={onClose}
                                className="p-1.5 rounded-lg hover:bg-hover-bg text-text-secondary hover:text-foreground transition-colors"
                            >
                                <X size={16} />
                            </button>
                        </div>

                        {/* Content */}
                        <div className="flex-1 overflow-y-auto p-4 space-y-5">
                            {!hasAnyAssets ? (
                                <div className="text-center py-8">
                                    <p className="text-sm text-text-secondary">{t("noAssetsAvailable")}</p>
                                    <p className="text-xs text-text-secondary/60 mt-1">{t("noAssetsHint")}</p>
                                </div>
                            ) : (
                                <>
                                    {/* Characters */}
                                    {characters.length > 0 && (
                                        <div>
                                            <div className="flex items-center gap-1.5 mb-2">
                                                <User size={12} className="text-blue-400" />
                                                <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wide">{t("characters")}</span>
                                            </div>
                                            <div className="grid grid-cols-2 gap-2">
                                                {characters.map((c, i) => {
                                                    const thumb = getAssetThumbnail(c, "character");
                                                    const readiness = getStoryboardR2VAssetReadiness(c, "character");
                                                    return (
                                                        <button
                                                            key={c.id}
                                                            onClick={() => {
                                                                onSelectAsset(`character${i + 1}`, c.name);
                                                                onClose();
                                                            }}
                                                            className="flex flex-col items-center gap-1.5 p-2 rounded-xl border border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12] hover:bg-white/[0.04] transition-all duration-200 group"
                                                        >
                                                            <div className="w-12 h-12 rounded-lg bg-white/[0.03] overflow-hidden flex items-center justify-center">
                                                                {thumb ? (
                                                                    <img src={thumb} alt={c.name} className="w-full h-full object-cover" />
                                                                ) : (
                                                                    <User size={16} className="text-text-secondary/40" />
                                                                )}
                                                            </div>
                                                            <span className="text-[11px] text-foreground group-hover:text-primary truncate w-full text-center">{c.name}</span>
                                                            <AssetReadinessBadges {...readiness} />
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    )}

                                    {/* Scenes */}
                                    {scenes.length > 0 && (
                                        <div>
                                            <div className="flex items-center gap-1.5 mb-2">
                                                <MapPin size={12} className="text-green-400" />
                                                <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wide">{t("scenes")}</span>
                                            </div>
                                            <div className="grid grid-cols-2 gap-2">
                                                {scenes.map((s) => {
                                                    const thumb = getAssetThumbnail(s, "scene");
                                                    const readiness = getStoryboardR2VAssetReadiness(s, "scene");
                                                    return (
                                                        <button
                                                            key={s.id}
                                                            onClick={() => {
                                                                onSelectAsset("scene", s.name);
                                                                onClose();
                                                            }}
                                                            className="flex flex-col items-center gap-1.5 p-2 rounded-xl border border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12] hover:bg-white/[0.04] transition-all duration-200 group"
                                                        >
                                                            <div className="w-12 h-12 rounded-lg bg-white/[0.03] overflow-hidden flex items-center justify-center">
                                                                {thumb ? (
                                                                    <img src={thumb} alt={s.name} className="w-full h-full object-cover" />
                                                                ) : (
                                                                    <MapPin size={16} className="text-text-secondary/40" />
                                                                )}
                                                            </div>
                                                            <span className="text-[11px] text-foreground group-hover:text-primary truncate w-full text-center">{s.name}</span>
                                                            <AssetReadinessBadges {...readiness} />
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    )}

                                    {/* Props */}
                                    {props.length > 0 && (
                                        <div>
                                            <div className="flex items-center gap-1.5 mb-2">
                                                <Package size={12} className="text-orange-400" />
                                                <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wide">{t("props")}</span>
                                            </div>
                                            <div className="grid grid-cols-2 gap-2">
                                                {props.map((p) => {
                                                    const thumb = getAssetThumbnail(p, "prop");
                                                    const readiness = getStoryboardR2VAssetReadiness(p, "prop");
                                                    return (
                                                        <button
                                                            key={p.id}
                                                            onClick={() => {
                                                                onSelectAsset("prop", p.name);
                                                                onClose();
                                                            }}
                                                            className="flex flex-col items-center gap-1.5 p-2 rounded-xl border border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12] hover:bg-white/[0.04] transition-all duration-200 group"
                                                        >
                                                            <div className="w-12 h-12 rounded-lg bg-white/[0.03] overflow-hidden flex items-center justify-center">
                                                                {thumb ? (
                                                                    <img src={thumb} alt={p.name} className="w-full h-full object-cover" />
                                                                ) : (
                                                                    <Package size={16} className="text-text-secondary/40" />
                                                                )}
                                                            </div>
                                                            <span className="text-[11px] text-foreground group-hover:text-primary truncate w-full text-center">{p.name}</span>
                                                            <AssetReadinessBadges {...readiness} />
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    </motion.div>
                </>
            )}
        </AnimatePresence>
    );
}
