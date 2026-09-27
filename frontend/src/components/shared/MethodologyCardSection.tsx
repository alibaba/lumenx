"use client";

/**
 * T-B6 方法论三分离卡（persona / image / voice + 三锚点）可折叠编辑区。
 *
 * 方法论来源（只抄规范、不 vendor 原文）：
 * - eternityspring/shuohao-skills（Apache-2.0）cast.json：persona/image/voice 三分离，
 *   image.prompt 禁人名、必写族裔/年代/地域、negativePrompt 独立维护。
 * - Hao0321/ai-short-drama（MIT）production_pack：visual/performance/voice_anchor。
 *
 * 挂载点：CharacterWorkbench（character）与 ConsistencyVault 的 CharacterDetailModal
 * （scene）。prop 不渲染（后端 Prop 模型无此字段）。写入走通用
 * PATCH 语义接口 updateAssetAttributes（后端 update_asset_attributes）。
 */

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronRight, Check } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { api } from "@/lib/api";

const inputCls =
    "w-full bg-input-bg border border-glass-border rounded-lg px-3 py-2 text-sm text-text-secondary placeholder-text-muted focus:border-primary/50 focus:outline-none";
const labelCls = "text-[0.65rem] font-bold text-text-muted uppercase tracking-wider";
const groupCls = "text-xs font-bold text-text-secondary uppercase tracking-wider border-l-2 border-primary/40 pl-2";

export default function MethodologyCardSection({ asset, type }: { asset: any; type: string }) {
    const currentProject = useProjectStore((state) => state.currentProject);
    const updateProject = useProjectStore((state) => state.updateProject);

    const [open, setOpen] = useState(false);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    // 三锚点（Hao0321 production_pack 语义）
    const [visualAnchor, setVisualAnchor] = useState<string>(asset?.visual_anchor || "");
    const [performanceAnchor, setPerformanceAnchor] = useState<string>(asset?.performance_anchor || "");
    const [voiceAnchor, setVoiceAnchor] = useState<string>(asset?.voice_anchor || "");
    // persona（shuohao：是谁——纯设定层，与图/声解耦）
    const [gender, setGender] = useState<string>(asset?.persona_profile?.gender || "");
    const [ageRange, setAgeRange] = useState<string>(asset?.persona_profile?.age_range || "");
    const [identity, setIdentity] = useState<string>(asset?.persona_profile?.identity || "");
    const [personality, setPersonality] = useState<string>(asset?.persona_profile?.personality || "");
    // image（shuohao：怎么画——prompt 禁人名、必写族裔/年代/地域）
    const [imagePrompt, setImagePrompt] = useState<string>(asset?.image_card?.prompt || "");
    const [imagePromptLocal, setImagePromptLocal] = useState<string>(asset?.image_card?.prompt_local || "");
    const [cardNegative, setCardNegative] = useState<string>(asset?.image_card?.negative_prompt || "");
    // voice（shuohao：怎么说话）
    const [timbre, setTimbre] = useState<string>(asset?.voice_card?.timbre || "");
    const [accent, setAccent] = useState<string>(asset?.voice_card?.accent || "");
    const [emotion, setEmotion] = useState<string>(asset?.voice_card?.emotion || "");

    const isCharacter = type === "character";
    const isProp = type === "prop";

    // asset 变更（保存后项目刷新）时回填
    useEffect(() => {
        setVisualAnchor(asset?.visual_anchor || "");
        setPerformanceAnchor(asset?.performance_anchor || "");
        setVoiceAnchor(asset?.voice_anchor || "");
        setGender(asset?.persona_profile?.gender || "");
        setAgeRange(asset?.persona_profile?.age_range || "");
        setIdentity(asset?.persona_profile?.identity || "");
        setPersonality(asset?.persona_profile?.personality || "");
        setImagePrompt(asset?.image_card?.prompt || "");
        setImagePromptLocal(asset?.image_card?.prompt_local || "");
        setCardNegative(asset?.image_card?.negative_prompt || "");
        setTimbre(asset?.voice_card?.timbre || "");
        setAccent(asset?.voice_card?.accent || "");
        setEmotion(asset?.voice_card?.emotion || "");
    }, [asset]);

    if (isProp) return null;

    const orNull = (v: string) => (v.trim() ? v.trim() : null);

    const buildPatch = (): Record<string, unknown> => {
        const patch: Record<string, unknown> = {
            visual_anchor: orNull(visualAnchor),
            image_card: {
                prompt: orNull(imagePrompt),
                prompt_local: orNull(imagePromptLocal),
                negative_prompt: orNull(cardNegative),
                tags: asset?.image_card?.tags || [],
            },
        };
        if (isCharacter) {
            patch.performance_anchor = orNull(performanceAnchor);
            patch.voice_anchor = orNull(voiceAnchor);
            patch.persona_profile = {
                gender: orNull(gender),
                age_range: orNull(ageRange),
                identity: orNull(identity),
                personality: orNull(personality),
            };
            patch.voice_card = {
                timbre: orNull(timbre),
                accent: orNull(accent),
                emotion: orNull(emotion),
            };
        }
        return patch;
    };

    const handleSave = async () => {
        if (!currentProject || saving) return;
        setSaving(true);
        setSaved(false);
        try {
            const updatedProject = await api.updateAssetAttributes(
                currentProject.id, asset.id, type, buildPatch(),
            );
            updateProject(currentProject.id, updatedProject);
            setSaved(true);
            setTimeout(() => setSaved(false), 2500);
        } catch (error) {
            console.error("Failed to save methodology card:", error);
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-2">
            <button
                onClick={() => setOpen(!open)}
                className="flex items-center gap-2 text-xs font-bold text-text-muted hover:text-foreground transition-colors uppercase"
            >
                <span>方法论卡（三分离 persona / image / voice）</span>
                <ChevronRight size={12} className={`transform transition-transform ${open ? "rotate-90" : ""}`} />
            </button>

            <AnimatePresence>
                {open && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="overflow-hidden"
                    >
                        <div className="bg-glass rounded-lg p-4 border border-border-subtle space-y-4">
                            {/* 三锚点 */}
                            <div className="space-y-2">
                                <div className={groupCls}>三锚点 · 跨镜逐字复述</div>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                                    <div className="space-y-1">
                                        <label className={labelCls}>视觉锚点</label>
                                        <input value={visualAnchor} onChange={(e) => setVisualAnchor(e.target.value)}
                                            placeholder="黑色长发 / 米色围裙 / 手腕红绳" className={inputCls} />
                                    </div>
                                    {isCharacter && (
                                        <>
                                            <div className="space-y-1">
                                                <label className={labelCls}>表演锚点</label>
                                                <input value={performanceAnchor} onChange={(e) => setPerformanceAnchor(e.target.value)}
                                                    placeholder="紧张时搓围裙边角" className={inputCls} />
                                            </div>
                                            <div className="space-y-1">
                                                <label className={labelCls}>声音锚点</label>
                                                <input value={voiceAnchor} onChange={(e) => setVoiceAnchor(e.target.value)}
                                                    placeholder="清亮偏暖的女声" className={inputCls} />
                                            </div>
                                        </>
                                    )}
                                </div>
                            </div>

                            {/* persona（仅角色） */}
                            {isCharacter && (
                                <div className="space-y-2">
                                    <div className={groupCls}>人物设定 · Persona</div>
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                                        <div className="space-y-1">
                                            <label className={labelCls}>性别</label>
                                            <input value={gender} onChange={(e) => setGender(e.target.value)} className={inputCls} />
                                        </div>
                                        <div className="space-y-1">
                                            <label className={labelCls}>年龄段</label>
                                            <input value={ageRange} onChange={(e) => setAgeRange(e.target.value)}
                                                placeholder="约 24 岁" className={inputCls} />
                                        </div>
                                        <div className="space-y-1">
                                            <label className={labelCls}>身份</label>
                                            <input value={identity} onChange={(e) => setIdentity(e.target.value)}
                                                placeholder="奶茶店店员" className={inputCls} />
                                        </div>
                                        <div className="space-y-1">
                                            <label className={labelCls}>性格（顿号分隔）</label>
                                            <input value={personality} onChange={(e) => setPersonality(e.target.value)}
                                                placeholder="倔强、温柔" className={inputCls} />
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* image 卡 */}
                            <div className="space-y-2">
                                <div className={groupCls}>出图卡 · Image</div>
                                <p className="text-[0.65rem] text-text-muted">
                                    规范：提示词禁止出现人名（用外观描述替代）· 必写族裔 / 年代 / 地域 · 负面词独立维护（缺失只记 warning 不拦截）
                                </p>
                                <div className="space-y-1">
                                    <label className={labelCls}>正向提示词（英文）</label>
                                    <textarea value={imagePrompt} onChange={(e) => setImagePrompt(e.target.value)} rows={2}
                                        placeholder="a young East Asian woman, 2020s, southern China city, …"
                                        className={`${inputCls} resize-none font-mono text-xs`} />
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                                    <div className="space-y-1">
                                        <label className={labelCls}>本地语言版</label>
                                        <textarea value={imagePromptLocal} onChange={(e) => setImagePromptLocal(e.target.value)} rows={2}
                                            placeholder="一位年轻的东亚女性，2020 年代，中国南方城市……"
                                            className={`${inputCls} resize-none text-xs`} />
                                    </div>
                                    <div className="space-y-1">
                                        <label className={labelCls}>负面提示词（独立维护）</label>
                                        <textarea value={cardNegative} onChange={(e) => setCardNegative(e.target.value)} rows={2}
                                            placeholder="blurry, extra fingers, …"
                                            className={`${inputCls} resize-none font-mono text-xs`} />
                                    </div>
                                </div>
                            </div>

                            {/* voice 卡（仅角色） */}
                            {isCharacter && (
                                <div className="space-y-2">
                                    <div className={groupCls}>声音卡 · Voice</div>
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                                        <div className="space-y-1">
                                            <label className={labelCls}>音色</label>
                                            <input value={timbre} onChange={(e) => setTimbre(e.target.value)}
                                                placeholder="清亮" className={inputCls} />
                                        </div>
                                        <div className="space-y-1">
                                            <label className={labelCls}>口音</label>
                                            <input value={accent} onChange={(e) => setAccent(e.target.value)}
                                                placeholder="南方口音" className={inputCls} />
                                        </div>
                                        <div className="space-y-1">
                                            <label className={labelCls}>基底情绪</label>
                                            <input value={emotion} onChange={(e) => setEmotion(e.target.value)}
                                                placeholder="平静偏倔强" className={inputCls} />
                                        </div>
                                    </div>
                                </div>
                            )}

                            <div className="flex justify-end items-center gap-3 pt-1">
                                {saved && (
                                    <span className="text-xs text-green-400 flex items-center gap-1">
                                        <Check size={12} /> 已保存
                                    </span>
                                )}
                                <button
                                    onClick={handleSave}
                                    disabled={saving}
                                    className="px-4 py-1.5 bg-primary text-white text-xs rounded hover:bg-primary/90 disabled:opacity-50"
                                >
                                    {saving ? "保存中…" : "保存方法论卡"}
                                </button>
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
