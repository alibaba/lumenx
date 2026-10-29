"use client";

import { useTranslations } from "next-intl";
import PreviewImage from "@/components/shared/preview/PreviewImage";
import { getAssetUrl } from "@/lib/utils";
import type { AtelierCandidateBranchSource, AtelierNode, JsonRecord, JsonValue } from "@/lib/atelierTypes";

function isRecord(value: JsonValue | undefined): value is JsonRecord {
    return !!value && typeof value === "object" && !Array.isArray(value);
}

export function branchSourceFromNode(node: AtelierNode | null): AtelierCandidateBranchSource | null {
    const value = node?.data.branch_source;
    if (!isRecord(value) || value.schema_version !== 1) return null;
    if ((value.media_kind !== "image" && value.media_kind !== "video" && value.media_kind !== "audio")
        || (value.recipe_status !== "available" && value.recipe_status !== "unavailable_imported" && value.recipe_status !== "unavailable_legacy"
            && value.recipe_status !== "unavailable_inputs" && value.recipe_status !== "unsupported")
        || typeof value.origin_project_id !== "string" || typeof value.origin_node_id !== "string" || typeof value.origin_candidate_id !== "string"
        || typeof value.media_url !== "string" || typeof value.media_sha256 !== "string" || typeof value.media_size_bytes !== "number") return null;
    return value as unknown as AtelierCandidateBranchSource;
}

export function BranchSourceCard({ node }: { node: AtelierNode | null }) {
    const t = useTranslations("atelier.branchSource");
    const source = branchSourceFromNode(node);
    if (!source) return null;
    const sourceOnly = source.recipe_status !== "available";
    const previewAlt = t("previewAlt");
    return <div className="rounded-xl border border-cyan-300/15 bg-cyan-300/10 p-3 text-xs text-cyan-50/80" data-testid="branch-source-card">
        <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-cyan-50">{t("title")}</span>
            <span className="rounded-lg border border-white/[0.08] bg-black/20 px-2 py-1 font-mono text-[10px] text-cyan-50/60">{source.media_kind}</span>
        </div>
        <div className="mt-3 overflow-hidden rounded-xl border border-white/[0.08] bg-black/20">
            {source.media_kind === "video" ? <video aria-label={previewAlt} controls playsInline preload="metadata" src={getAssetUrl(source.media_url)} className="aspect-video w-full" />
                : source.media_kind === "image" ? <PreviewImage src={source.media_url} alt={previewAlt} noLightbox className="max-h-44 w-full object-contain" />
                    : <audio aria-label={previewAlt} controls preload="metadata" src={getAssetUrl(source.media_url)} className="w-full p-2" />}
        </div>
        <p className="mt-2 text-cyan-50/70">{sourceOnly ? t("sourceOnly") : t("available")}</p>
        <p className="mt-2 break-all font-mono text-[10px] text-cyan-50/45">{source.origin_node_id} · {source.origin_candidate_id}</p>
    </div>;
}
