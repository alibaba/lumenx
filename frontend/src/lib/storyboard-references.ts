interface ReferenceImageVariant {
  id: string;
  url?: string | null;
}

interface ReferenceImageAsset {
  selected_id?: string | null;
  variants?: ReferenceImageVariant[] | null;
}

interface ReferenceCompositionBindings {
  prefer_previous_frame?: boolean;
  required?: boolean;
  lock?: boolean;
}

interface ReferenceCompositionData {
  reference_binding_version?: unknown;
  reference_image_url?: unknown;
  reference_image_urls?: unknown;
  continuity_lock?: boolean;
  continuity?: ReferenceCompositionBindings;
  scene?: ReferenceCompositionBindings;
  style?: ReferenceCompositionBindings;
  [key: string]: unknown;
}

interface ReferenceFrame {
  id?: string;
  scene_id?: string | null;
  rendered_image_asset?: ReferenceImageAsset | null;
  rendered_image_url?: string | null;
  image_url?: string | null;
  composition_data?: ReferenceCompositionData | null;
  character_ids?: string[] | null;
  prop_ids?: string[] | null;
}

interface ReferenceScene {
  id: string;
  name?: string | null;
  image_asset?: ReferenceImageAsset | null;
  image_url?: string | null;
  locked?: boolean;
}

interface ReferenceCharacter {
  id: string;
  name?: string | null;
  three_view_asset?: ReferenceImageAsset | null;
  full_body_asset?: ReferenceImageAsset | null;
  headshot_asset?: ReferenceImageAsset | null;
  three_view_image_url?: string | null;
  full_body_image_url?: string | null;
  headshot_image_url?: string | null;
  avatar_url?: string | null;
  image_url?: string | null;
  locked?: boolean;
}

interface ReferenceProp {
  id: string;
  name?: string | null;
  image_asset?: ReferenceImageAsset | null;
  image_url?: string | null;
  locked?: boolean;
}

interface ReferenceStyleConfig {
  name?: string | null;
  positive_prompt?: string | null;
  moodboard_notes?: string | null;
  reference_images?: unknown;
}

interface ReferenceProject {
  frames?: ReferenceFrame[] | null;
  scenes?: ReferenceScene[] | null;
  characters?: ReferenceCharacter[] | null;
  props?: ReferenceProp[] | null;
  art_direction?: {
    style_config?: ReferenceStyleConfig | null;
  } | null;
}

export function getSelectedVariantUrl(asset?: ReferenceImageAsset | null): string | null {
  if (!asset || !asset.variants || asset.variants.length === 0) {
    return null;
  }

  if (asset.selected_id) {
    const selectedVariant = asset.variants.find((variant) => variant.id === asset.selected_id);
    if (selectedVariant?.url) {
      return selectedVariant.url;
    }
  }

  return asset.variants[0]?.url || null;
}

export function getSelectedFrameReference(frame?: ReferenceFrame | null): string | null {
  if (frame?.rendered_image_asset?.selected_id) {
    const selectedVariant = frame.rendered_image_asset.variants?.find(
      (variant) => variant.id === frame.rendered_image_asset?.selected_id,
    );
    if (selectedVariant?.url) {
      return selectedVariant.url;
    }
  }

  return frame?.rendered_image_url || frame?.image_url || null;
}

export function getPreviousSameSceneFrame(project?: ReferenceProject | null, frame?: ReferenceFrame | null) {
  if (!project?.frames || !frame?.id) {
    return null;
  }

  const frameIndex = project.frames.findIndex((item) => item.id === frame.id);
  if (frameIndex <= 0) {
    return null;
  }

  const previousFrame = project.frames?.[frameIndex - 1];
  return previousFrame?.scene_id === frame?.scene_id ? previousFrame : null;
}

export function getNextSameSceneFrame(project?: ReferenceProject | null, frame?: ReferenceFrame | null) {
  if (!project?.frames || !frame?.id) {
    return null;
  }

  const frameIndex = project.frames.findIndex((item) => item.id === frame.id);
  if (frameIndex < 0 || frameIndex >= project.frames.length - 1) {
    return null;
  }

  const nextFrame = project.frames?.[frameIndex + 1];
  return nextFrame?.scene_id === frame?.scene_id ? nextFrame : null;
}

export function getArtDirectionPromptPrefix(styleConfig?: ReferenceStyleConfig | null): string {
  return [styleConfig?.positive_prompt, styleConfig?.moodboard_notes]
    .filter((value) => typeof value === "string" && value.trim().length > 0)
    .join(", ");
}

export function getArtDirectionReferenceImages(styleConfig?: ReferenceStyleConfig | null): string[] {
  if (!Array.isArray(styleConfig?.reference_images)) {
    return [];
  }

  return styleConfig.reference_images.filter((value: unknown): value is string =>
    typeof value === "string" && value.trim().length > 0,
  );
}

function pushUnique(urls: string[], candidate?: string | null) {
  if (candidate && !urls.includes(candidate)) {
    urls.push(candidate);
  }
}

export interface StoryboardReferencePreviewItem {
  id: string;
  name: string;
  type: "continuity" | "scene" | "character" | "prop" | "style";
  url: string | null;
  required: boolean;
  locked: boolean;
  status: "ready" | "missing";
  source: string;
}

export type CodexImagegenRecommendedMode = "safe_refs_only" | "two_stage_high_consistency";

export interface CodexImagegenRecommendationMetrics {
  readyCount: number;
  totalCount: number;
  requiredReadyCount: number;
  missingRequiredCount: number;
  continuityCount: number;
  sceneCount: number;
  characterCount: number;
  propCount: number;
  styleCount: number;
  identityCount: number;
  environmentCount: number;
  lockedCount: number;
}

export interface CodexImagegenRecommendation {
  mode: CodexImagegenRecommendedMode;
  score: number;
  reason: string;
  metrics: CodexImagegenRecommendationMetrics;
  thresholds?: Record<string, number>;
  policy?: Record<string, unknown>;
  shot_type?: string | null;
  genre?: string | null;
}

function buildPreviewItem(
  item: Omit<StoryboardReferencePreviewItem, "status">,
): StoryboardReferencePreviewItem {
  return {
    ...item,
    status: item.url ? "ready" : "missing",
  };
}

function clampScore(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function metricNumber(
  metrics: Record<string, unknown> | null,
  camelKey: string,
  snakeKey: string,
): number {
  const value = metrics?.[camelKey] ?? metrics?.[snakeKey];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function normalizeRecommendationMode(value: unknown): CodexImagegenRecommendedMode | null {
  const raw = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (raw === "two_stage_high_consistency" || raw === "two_stage" || raw === "high_consistency") {
    return "two_stage_high_consistency";
  }
  if (raw === "safe_refs_only" || raw === "safe_direct" || raw === "direct") {
    return "safe_refs_only";
  }
  return null;
}

function normalizeThresholds(value: unknown): Record<string, number> | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  const normalized = Object.entries(record).reduce<Record<string, number>>((acc, [key, rawValue]) => {
    if (typeof rawValue === "number" && Number.isFinite(rawValue)) {
      acc[key] = rawValue;
    }
    return acc;
  }, {});
  return Object.keys(normalized).length > 0 ? normalized : undefined;
}

export function normalizeCodexImagegenRecommendation(
  value: unknown,
): CodexImagegenRecommendation | null {
  const record = asRecord(value);
  if (!record) return null;
  const mode = normalizeRecommendationMode(record.mode);
  if (!mode) return null;

  const metrics = asRecord(record.metrics);
  return {
    mode,
    score: clampScore(typeof record.score === "number" ? record.score : 0),
    reason: typeof record.reason === "string" ? record.reason : "",
    metrics: {
      readyCount: metricNumber(metrics, "readyCount", "ready_count"),
      totalCount: metricNumber(metrics, "totalCount", "total_count"),
      requiredReadyCount: metricNumber(metrics, "requiredReadyCount", "required_ready_count"),
      missingRequiredCount: metricNumber(metrics, "missingRequiredCount", "missing_required_count"),
      continuityCount: metricNumber(metrics, "continuityCount", "continuity_count"),
      sceneCount: metricNumber(metrics, "sceneCount", "scene_count"),
      characterCount: metricNumber(metrics, "characterCount", "character_count"),
      propCount: metricNumber(metrics, "propCount", "prop_count"),
      styleCount: metricNumber(metrics, "styleCount", "style_count"),
      identityCount: metricNumber(metrics, "identityCount", "identity_count"),
      environmentCount: metricNumber(metrics, "environmentCount", "environment_count"),
      lockedCount: metricNumber(metrics, "lockedCount", "locked_count"),
    },
    thresholds: normalizeThresholds(record.thresholds),
    policy: asRecord(record.policy) ?? undefined,
    shot_type: typeof record.shot_type === "string" ? record.shot_type : null,
    genre: typeof record.genre === "string" ? record.genre : null,
  };
}

export function recommendCodexImagegenMode(
  previewItems?: StoryboardReferencePreviewItem[] | null,
): CodexImagegenRecommendation {
  const items = Array.isArray(previewItems) ? previewItems : [];
  const readyItems = items.filter((item) => item.status === "ready" && Boolean(item.url));
  const requiredItems = items.filter((item) => item.required);
  const readyRequiredItems = requiredItems.filter((item) => item.status === "ready" && Boolean(item.url));

  const counts = readyItems.reduce(
    (acc, item) => {
      acc[item.type] += 1;
      if (item.locked) acc.lockedCount += 1;
      return acc;
    },
    {
      continuity: 0,
      scene: 0,
      character: 0,
      prop: 0,
      style: 0,
      lockedCount: 0,
    },
  );

  const identityCount = counts.character + counts.prop;
  const environmentCount = counts.continuity + counts.scene + counts.style;
  const missingRequiredCount = Math.max(0, requiredItems.length - readyRequiredItems.length);

  const shouldUseTwoStage =
    missingRequiredCount === 0
    && (
      readyItems.length >= 5
      || identityCount >= 3
      || (counts.character >= 2 && counts.prop >= 1)
      || (counts.character >= 2 && counts.scene >= 1)
      || (counts.prop >= 2 && counts.character >= 1)
    );

  let score = readyItems.length * 11
    + identityCount * 9
    + environmentCount * 7
    + counts.lockedCount * 3
    + (counts.character >= 2 ? 10 : 0)
    + (counts.prop >= 2 ? 6 : 0)
    + (counts.scene >= 1 && identityCount >= 2 ? 8 : 0)
    + (counts.continuity >= 1 && counts.scene >= 1 ? 4 : 0)
    - missingRequiredCount * 18;

  if (!shouldUseTwoStage) {
    score = Math.min(score, 59);
  } else {
    score = Math.max(score, 60);
  }

  const reason = missingRequiredCount > 0
    ? `当前有 ${missingRequiredCount} 个主参考缺失，先用安全直连稳住可用参考，补齐后再考虑两段式。`
    : shouldUseTwoStage
      ? "参考数量较多且身份锚点分散，建议先用两段式锁人物与关键道具，再细化场景和光影。"
      : "当前参考量较轻，安全直连已足够覆盖镜头一致性。";

  return {
    mode: shouldUseTwoStage ? "two_stage_high_consistency" : "safe_refs_only",
    score: clampScore(score),
    reason,
    metrics: {
      readyCount: readyItems.length,
      totalCount: items.length,
      requiredReadyCount: readyRequiredItems.length,
      missingRequiredCount,
      continuityCount: counts.continuity,
      sceneCount: counts.scene,
      characterCount: counts.character,
      propCount: counts.prop,
      styleCount: counts.style,
      identityCount,
      environmentCount,
      lockedCount: counts.lockedCount,
    },
  };
}

export function buildStoryboardReferencePreview(
  project?: ReferenceProject | null,
  frame?: ReferenceFrame | null,
  options?: {
    continuityLock?: boolean;
    includeStyleReferences?: boolean;
  },
): StoryboardReferencePreviewItem[] {
  const composition = frame?.composition_data || {};
  const bindings: ReferenceCompositionData = composition.reference_binding_version ? composition : {};
  const continuityLock = options?.continuityLock ?? composition.continuity_lock ?? true;
  const previewItems: StoryboardReferencePreviewItem[] = [];

  const previousSameSceneFrame = getPreviousSameSceneFrame(project, frame);
  const continuityUrl = continuityLock ? getSelectedFrameReference(previousSameSceneFrame) : null;
  if (continuityLock) {
    previewItems.push(buildPreviewItem({
      id: previousSameSceneFrame?.id || "previous-frame",
      name: previousSameSceneFrame ? `上一帧 #${previousSameSceneFrame.id?.slice(0, 8)}` : "上一帧连续参考",
      type: "continuity",
      url: continuityUrl,
      required: Boolean(bindings.continuity?.prefer_previous_frame),
      locked: true,
      source: "同场景连续镜头",
    }));
  }

  if (frame?.scene_id) {
    const scene = project?.scenes?.find((sceneItem) => sceneItem.id === frame.scene_id);
    previewItems.push(buildPreviewItem({
      id: scene?.id || frame.scene_id,
      name: scene?.name || "未知场景",
      type: "scene",
      url: getSelectedVariantUrl(scene?.image_asset) || scene?.image_url || null,
      required: bindings.scene?.required !== false,
      locked: Boolean(scene?.locked || bindings.scene?.lock),
      source: "场景主参考",
    }));
  }

  (frame?.character_ids || []).forEach((characterId: string) => {
    const character = project?.characters?.find((item) => item.id === characterId);
    previewItems.push(buildPreviewItem({
      id: character?.id || characterId,
      name: character?.name || "未知角色",
      type: "character",
      url:
        getSelectedVariantUrl(character?.three_view_asset)
        || getSelectedVariantUrl(character?.full_body_asset)
        || getSelectedVariantUrl(character?.headshot_asset)
        || character?.three_view_image_url
        || character?.full_body_image_url
        || character?.headshot_image_url
        || character?.avatar_url
        || character?.image_url
        || null,
      required: true,
      locked: Boolean(character?.locked),
      source: "角色主参考",
    }));
  });

  (frame?.prop_ids || []).forEach((propId: string) => {
    const prop = project?.props?.find((item) => item.id === propId);
    previewItems.push(buildPreviewItem({
      id: prop?.id || propId,
      name: prop?.name || "未知道具",
      type: "prop",
      url: getSelectedVariantUrl(prop?.image_asset) || prop?.image_url || null,
      required: true,
      locked: Boolean(prop?.locked),
      source: "道具主参考",
    }));
  });

  if (options?.includeStyleReferences !== false) {
    getArtDirectionReferenceImages(project?.art_direction?.style_config).forEach((url: string, index: number) => {
      previewItems.push(buildPreviewItem({
        id: `style-${index}`,
        name: project?.art_direction?.style_config?.name || "风格参考",
        type: "style",
        url,
        required: false,
        locked: Boolean(bindings.style?.lock),
        source: "美术指导参考",
      }));
    });
  }

  return previewItems;
}

export function buildStoryboardCompositionData(
  project?: ReferenceProject | null,
  frame?: ReferenceFrame | null,
  options?: {
    continuityLock?: boolean;
    includeStyleReferences?: boolean;
    codexRecommendationIncludeStyleReferences?: boolean;
  },
) {
  const continuityLock = options?.continuityLock ?? true;
  const baseComposition = frame?.composition_data || {};
  const preserveLegacyReferences = !baseComposition.reference_binding_version;
  const referenceImageUrls: string[] = [];

  // Managed bindings are rebuilt from current project state on every render.
  // Carrying old reference_image_urls forward can balloon the payload after repeated edits.
  if (preserveLegacyReferences) {
    if (typeof baseComposition.reference_image_url === "string") {
      pushUnique(referenceImageUrls, baseComposition.reference_image_url);
    }

    if (Array.isArray(baseComposition.reference_image_urls)) {
      baseComposition.reference_image_urls.forEach((url: unknown) => {
        if (typeof url === "string") {
          pushUnique(referenceImageUrls, url);
        }
      });
    }
  }

  if (frame?.scene_id) {
    const scene = project?.scenes?.find((item) => item.id === frame.scene_id);
    pushUnique(referenceImageUrls, getSelectedVariantUrl(scene?.image_asset) || scene?.image_url);
  }

  if (Array.isArray(frame?.character_ids)) {
    frame.character_ids.forEach((characterId: string) => {
      const character = project?.characters?.find((item) => item.id === characterId);
      pushUnique(
        referenceImageUrls,
        getSelectedVariantUrl(character?.three_view_asset)
          || getSelectedVariantUrl(character?.full_body_asset)
          || getSelectedVariantUrl(character?.headshot_asset)
          || character?.three_view_image_url
          || character?.full_body_image_url
          || character?.headshot_image_url
          || character?.avatar_url
          || character?.image_url,
      );
    });
  }

  if (Array.isArray(frame?.prop_ids)) {
    frame.prop_ids.forEach((propId: string) => {
      const prop = project?.props?.find((item) => item.id === propId);
      pushUnique(referenceImageUrls, getSelectedVariantUrl(prop?.image_asset) || prop?.image_url);
    });
  }

  const previousSameSceneFrame = getPreviousSameSceneFrame(project, frame);
  const continuitySourceUrl = continuityLock ? getSelectedFrameReference(previousSameSceneFrame) : null;
  if (continuitySourceUrl) {
    referenceImageUrls.unshift(continuitySourceUrl);
  }

  if (options?.includeStyleReferences !== false) {
    getArtDirectionReferenceImages(project?.art_direction?.style_config).forEach((url: string) => {
      pushUnique(referenceImageUrls, url);
    });
  }

  const dedupedReferenceUrls = referenceImageUrls.filter((value, index, all) => all.indexOf(value) === index);
  const referencePreview = buildStoryboardReferencePreview(project, frame, options);
  const codexRecommendationReferencePreview = buildStoryboardReferencePreview(project, frame, {
    ...options,
    includeStyleReferences: options?.codexRecommendationIncludeStyleReferences ?? options?.includeStyleReferences ?? true,
  });
  const existingCodexRecommendation = normalizeCodexImagegenRecommendation(baseComposition.codex_imagegen_recommendation);
  const codexImagegenRecommendation =
    existingCodexRecommendation
      ? existingCodexRecommendation
      : recommendCodexImagegenMode(codexRecommendationReferencePreview);

  return {
    ...baseComposition,
    character_ids: frame?.character_ids || [],
    prop_ids: frame?.prop_ids || [],
    scene_id: frame?.scene_id,
    continuity_lock: continuityLock,
    continuity_source_frame_id: continuitySourceUrl ? previousSameSceneFrame?.id : undefined,
    reference_image_url: dedupedReferenceUrls[0] || undefined,
    reference_image_urls: dedupedReferenceUrls,
    reference_preview: referencePreview,
    codex_imagegen_recommended_mode: codexImagegenRecommendation.mode,
    codex_imagegen_recommendation: codexImagegenRecommendation,
  };
}
