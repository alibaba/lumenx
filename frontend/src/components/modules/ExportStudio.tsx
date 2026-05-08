import { useState } from "react";
import { Download, Film, CheckCircle, FileVideo, Monitor, Captions, FileText } from "lucide-react";
import clsx from "clsx";
import { messages } from "@/lib/i18n";
import { useProjectStore } from "@/store/projectStore";
import { api } from "@/lib/api";
import { getAssetUrl } from "@/lib/utils";
import { getGenerationBadgeText, getGenerationTooltip, getProjectGenerationProvenance, isGenerationDegraded } from "@/lib/generation-provenance";

export default function ExportStudio() {
    const copy = messages.modules.exportStudio;
    const currentProject = useProjectStore((state) => state.currentProject);

    const [isExporting, setIsExporting] = useState(false);
    const [exportUrl, setExportUrl] = useState<string | null>(null);
    const [subtitleUrl, setSubtitleUrl] = useState<string | null>(null);
    const [exportError, setExportError] = useState<string | null>(null);

    // Config State
    const [resolution, setResolution] = useState("1080p");
    const [format, setFormat] = useState("mp4");
    const [subtitles, setSubtitles] = useState("burn-in");
    const subtitleOptions = [
        { id: "burn-in", label: copy.subtitleOptions.burnIn },
        { id: "srt", label: copy.subtitleOptions.srt },
        { id: "none", label: copy.subtitleOptions.none },
    ];

    // If project already has a merged video, show it immediately
    const effectiveUrl = exportUrl || currentProject?.merged_video_url || null;
    const generationSummary = getProjectGenerationProvenance(currentProject);
    const generationBadge = getGenerationBadgeText(generationSummary);
    const generationBadgeDegraded = isGenerationDegraded(generationSummary);

    const handleExport = async () => {
        if (!currentProject) return;
        setIsExporting(true);
        setExportUrl(null);
        setSubtitleUrl(null);
        setExportError(null);

        try {
            const result = await api.exportProject(currentProject.id, { resolution, format, subtitles });
            setExportUrl(result.url);
            setSubtitleUrl(result.subtitle_url || null);
        } catch (error: unknown) {
            console.error("Export failed:", error);
            setExportError(error instanceof Error ? error.message : copy.exportFailedHint);
        } finally {
            setIsExporting(false);
        }
    };

    return (
        <div className="flex h-full text-white">
            {/* Left: Configuration */}
            <div className="w-96 border-r border-white/10 bg-black/20 p-8 flex flex-col">
                <h2 className="text-2xl font-display font-bold mb-8 flex items-center gap-3">
                    <Film className="text-primary" /> {copy.title}
                    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                        {copy.exportBadge}
                    </span>
                </h2>

                <div className="mb-6 rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-100">
                    <div className="flex items-center gap-2 font-semibold">
                        <CheckCircle size={14} className="text-emerald-300" />
                        {copy.exportNoticeTitle}
                    </div>
                    <p className="mt-2 text-emerald-100/80">{copy.exportNotice}</p>
                </div>

                {generationBadge && (
                    <div
                        className={clsx(
                            "mb-6 rounded-xl border px-4 py-3 text-sm",
                            generationBadgeDegraded
                                ? "border-amber-500/30 bg-amber-500/10 text-amber-100"
                                : "border-emerald-500/20 bg-emerald-500/10 text-emerald-100"
                        )}
                        title={getGenerationTooltip(generationSummary)}
                    >
                        <div className="font-semibold">{generationBadge}</div>
                        {generationSummary?.generation_reason && (
                            <p className="mt-1 text-xs opacity-80">{generationSummary.generation_reason}</p>
                        )}
                    </div>
                )}

                <div className="space-y-8 flex-1">
                    {/* Resolution */}
                    <div className="space-y-3">
                        <label className="text-sm font-bold text-gray-400 flex items-center gap-2">
                            <Monitor size={16} /> {copy.resolution}
                        </label>
                        <div className="grid grid-cols-2 gap-3">
                            {["1080p", "4K"].map(res => (
                                <button
                                    key={res}
                                    onClick={() => setResolution(res)}
                                    className={clsx(
                                        "py-3 px-4 rounded-xl border text-sm font-bold transition-all",
                                        resolution === res
                                            ? "bg-primary text-white border-primary shadow-lg shadow-primary/20"
                                            : "bg-white/5 border-white/10 text-gray-400 hover:bg-white/10"
                                    )}
                                >
                                    {res}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Format */}
                    <div className="space-y-3">
                        <label className="text-sm font-bold text-gray-400 flex items-center gap-2">
                            <FileVideo size={16} /> {copy.format}
                        </label>
                        <div className="grid grid-cols-3 gap-3">
                            {["mp4", "mov", "gif"].map(fmt => (
                                <button
                                    key={fmt}
                                    onClick={() => setFormat(fmt)}
                                    className={clsx(
                                        "py-3 px-4 rounded-xl border text-sm font-bold uppercase transition-all",
                                        format === fmt
                                            ? "bg-primary text-white border-primary shadow-lg shadow-primary/20"
                                            : "bg-white/5 border-white/10 text-gray-400 hover:bg-white/10"
                                    )}
                                >
                                    {fmt}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Subtitles */}
                    <div className="space-y-3">
                        <label className="text-sm font-bold text-gray-400 flex items-center gap-2">
                            <Captions size={16} /> {copy.subtitles}
                        </label>
                        <div className="space-y-2">
                            {subtitleOptions.map(opt => (
                                <button
                                    key={opt.id}
                                    onClick={() => setSubtitles(opt.id)}
                                    className={clsx(
                                        "w-full py-3 px-4 rounded-xl border text-sm font-medium text-left transition-all",
                                        subtitles === opt.id
                                            ? "bg-primary text-white border-primary shadow-lg shadow-primary/20"
                                            : "bg-white/5 border-white/10 text-gray-400 hover:bg-white/10"
                                    )}
                                >
                                    {opt.label}
                                </button>
                            ))}
                        </div>
                    </div>

                    <p className="text-xs leading-relaxed text-gray-500">
                        {copy.optionsHint}
                    </p>
                </div>

                <button
                    onClick={handleExport}
                    disabled={isExporting}
                    className="w-full bg-gradient-to-r from-primary to-purple-600 hover:from-primary/90 hover:to-purple-600/90 text-white py-4 rounded-xl font-bold text-lg shadow-xl shadow-primary/20 disabled:opacity-50 disabled:cursor-not-allowed transition-all mt-8"
                >
                    {isExporting ? copy.rendering : copy.startRender}
                </button>
            </div>

            {/* Right: Preview & Status */}
            <div className="flex-1 flex items-center justify-center relative overflow-hidden">
                {/* Background Glow */}
                <div className="absolute inset-0 bg-gradient-to-br from-primary/5 to-purple-900/10 pointer-events-none" />

                <div className="w-full max-w-2xl p-8 text-center space-y-8 relative z-10">
                    {isExporting ? (
                        <div className="bg-black/30 backdrop-blur-xl border border-white/10 rounded-2xl p-12 shadow-2xl">
                            <div className="w-24 h-24 border-4 border-white/10 border-t-primary rounded-full animate-spin mx-auto mb-8" />
                            <h3 className="text-2xl font-bold mb-2">{copy.renderingTitle}</h3>
                            <p className="text-gray-400">{copy.renderingHint}</p>
                        </div>
                    ) : exportError ? (
                        <div className="bg-black/30 backdrop-blur-xl border border-red-500/30 rounded-2xl p-12 shadow-2xl shadow-red-900/20">
                            <div className="w-20 h-20 bg-red-500/20 text-red-500 rounded-full flex items-center justify-center mx-auto mb-6">
                                <Film size={40} />
                            </div>
                            <h3 className="text-2xl font-bold mb-2 text-white">{copy.exportFailed}</h3>
                            <p className="text-gray-400 mb-4">{exportError}</p>
                            <button
                                onClick={handleExport}
                                className="inline-flex items-center gap-2 bg-white/10 hover:bg-white/20 text-white px-6 py-3 rounded-xl font-bold transition-colors"
                            >
                                {copy.retry}
                            </button>
                        </div>
                    ) : effectiveUrl ? (
                        <div className="bg-black/30 backdrop-blur-xl border border-green-500/30 rounded-2xl p-12 shadow-2xl shadow-green-900/20">
                            <div className="w-20 h-20 bg-green-500/20 text-green-500 rounded-full flex items-center justify-center mx-auto mb-6">
                                <CheckCircle size={40} />
                            </div>
                            <h3 className="text-2xl font-bold mb-2 text-white">{copy.exportComplete}</h3>
                            <p className="text-gray-400 mb-8">{copy.successHint}</p>
                            <p className="text-xs text-emerald-300/90 mb-6">{copy.successNote}</p>
                            {generationBadge && (
                                <div
                                    className={clsx(
                                        "mx-auto mb-6 inline-flex max-w-md flex-col rounded-lg border px-3 py-2 text-xs",
                                        generationBadgeDegraded
                                            ? "border-amber-400/30 bg-amber-400/10 text-amber-100"
                                            : "border-emerald-400/20 bg-emerald-400/10 text-emerald-100"
                                    )}
                                    title={getGenerationTooltip(generationSummary)}
                                >
                                    <span className="font-semibold">{generationBadge}</span>
                                    {generationSummary?.generation_reason && <span className="mt-1 opacity-80">{generationSummary.generation_reason}</span>}
                                </div>
                            )}

                            <a
                                href={getAssetUrl(effectiveUrl)}
                                target="_blank"
                                className="inline-flex items-center gap-2 bg-green-600 hover:bg-green-500 text-white px-8 py-4 rounded-xl font-bold text-lg transition-colors shadow-lg shadow-green-600/20"
                            >
                                <Download size={20} /> {copy.downloadVideo}
                            </a>
                            {subtitleUrl && (
                                <a
                                    href={getAssetUrl(subtitleUrl)}
                                    target="_blank"
                                    className="ml-3 inline-flex items-center gap-2 bg-white/10 hover:bg-white/15 text-white px-6 py-4 rounded-xl font-bold text-lg transition-colors"
                                >
                                    <FileText size={18} /> {copy.downloadSubtitles}
                                </a>
                            )}
                        </div>
                    ) : (
                        <div className="opacity-50">
                            <Film size={64} className="mx-auto mb-4 text-gray-600" />
                            <p className="text-gray-500">{copy.emptyHint}</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
