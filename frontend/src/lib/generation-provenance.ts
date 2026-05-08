export interface GenerationProvenance {
  generation_source?: string | null;
  generation_degraded?: boolean | null;
  generation_reason?: string | null;
}

interface ProjectLike {
  frames?: GenerationProvenance[] | null;
  generation_metadata?: Record<string, unknown> | null;
}

interface GenerationMetadataEntry {
  source?: string | null;
  degraded?: boolean | null;
  reason?: string | null;
}

const SOURCE_LABELS: Record<string, string> = {
  llm: "LLM",
  mock: "Mock",
  fallback: "Fallback",
  heuristic_fallback: "Heuristic",
  heuristic_draft: "Draft",
  draft: "Draft",
};

export function getGenerationSourceLabel(source?: string | null) {
  const normalized = (source || "").trim();
  return normalized ? SOURCE_LABELS[normalized] || normalized : "";
}

export function isGenerationDegraded(item?: GenerationProvenance | null) {
  if (!item) return false;
  const source = (item.generation_source || "").trim();
  return Boolean(item.generation_degraded) || (Boolean(source) && source !== "llm");
}

export function getGenerationBadgeText(item?: GenerationProvenance | null) {
  if (!item) return null;
  const sourceLabel = getGenerationSourceLabel(item.generation_source);
  const degraded = isGenerationDegraded(item);
  if (!sourceLabel && !degraded) return null;
  if (!sourceLabel) return "降级";
  return degraded ? `${sourceLabel} / 降级` : sourceLabel;
}

export function getGenerationTooltip(item?: GenerationProvenance | null) {
  if (!item) return "";
  const parts = [];
  const sourceLabel = getGenerationSourceLabel(item.generation_source);
  if (sourceLabel) {
    parts.push(`来源：${sourceLabel}`);
  }
  if (isGenerationDegraded(item)) {
    parts.push("当前结果来自 mock/fallback 路径");
  }
  if (item.generation_reason) {
    parts.push(`原因：${item.generation_reason}`);
  }
  return parts.join("；");
}

function isMetadataEntry(value: unknown): value is GenerationMetadataEntry {
  return Boolean(value && typeof value === "object");
}

export function getProjectGenerationProvenance(project?: ProjectLike | null): GenerationProvenance | null {
  if (!project) return null;

  const metadataEntries = Object.values(project.generation_metadata || {})
    .filter(isMetadataEntry);
  const degradedEntry = metadataEntries.find((entry) => {
    const source = (entry.source || "").trim();
    return Boolean(entry.degraded) || (Boolean(source) && source !== "llm");
  });
  const firstEntry = metadataEntries.find((entry) => entry.source);
  const degradedFrameCount = (project.frames || []).filter(isGenerationDegraded).length;

  if (degradedEntry) {
    return {
      generation_source: degradedEntry.source || "fallback",
      generation_degraded: true,
      generation_reason: degradedEntry.reason || `项目包含 ${degradedFrameCount} 个降级分镜帧`,
    };
  }

  if (degradedFrameCount > 0) {
    return {
      generation_source: "fallback",
      generation_degraded: true,
      generation_reason: `项目包含 ${degradedFrameCount} 个降级分镜帧`,
    };
  }

  if (firstEntry?.source) {
    return {
      generation_source: firstEntry.source,
      generation_degraded: false,
      generation_reason: firstEntry.reason || null,
    };
  }

  return null;
}
