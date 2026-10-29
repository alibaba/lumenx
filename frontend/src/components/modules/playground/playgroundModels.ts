import rawCatalog from '@/generated/modelCatalog.json';
import type { PlaygroundMode } from './usePlaygroundStore';
import { allowsPlaygroundWorkspaceGateway, resolvePlaygroundCatalogModelId } from './playgroundModelAliases';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface PlaygroundModelOption {
  id: string;
  displayName: string;
  family: string;
  description: string;
  recommended: boolean;
  badges: string[];
  capabilities: string[];
  duration:
    | { type: 'slider'; min: number; max: number; step: number; default: number }
    | { type: 'buttons'; options: number[]; default: number; options_by_resolution?: Record<string, number[]> }
    | { type: 'fixed'; value: number }
    | null;
  params: {
    resolution?: { options: string[]; default: string };
    ratio?: { options: string[]; default: string };
    size?: { options: string[]; default: string };
    quality?: { options: string[]; default: string };
    seed?: boolean | { min: number; max: number };
    negativePrompt?: boolean | { default: boolean };
    promptExtend?: boolean | { default: boolean };
    watermark?: boolean | { default: boolean };
    audioSetting?: { options: string[]; default: string };
    shotType?: { options: string[]; default: string };
    mode?: { options: string[]; default: string };
    audio?: boolean | { default: boolean };
  };
  maxReferenceImages: number;
  referenceImageMimeTypes?: string[];
  maxReferenceImageBytes?: number;
}

// ---------------------------------------------------------------------------
// Internal catalog typing
// ---------------------------------------------------------------------------

interface CatalogDuration {
  type: string;
  min?: number;
  max?: number;
  step?: number;
  default?: number;
  value?: number;
  options?: number[];
  options_by_resolution?: Record<string, number[]>;
}

interface CatalogModel {
  id: string;
  display_name: string;
  description: string;
  family: string;
  status: string;
  capabilities: string[];
  duration?: CatalogDuration | null;
  params?: Record<string, unknown>;
  inputs?: {
    reference_images?: { max?: number; mime_types?: string[]; max_bytes?: number };
  };
  runtime?: Record<string, { gateway?: string }>;
  ui: {
    selection_group: string;
    visible_in: string[];
    recommended?: boolean;
    order?: number;
    badges?: string[];
  };
}

const catalog = rawCatalog as {
  models: Record<string, CatalogModel>;
  modes?: Record<string, { runtime?: Record<string, { gateway?: string }> }>;
  compat?: { legacy_model_ids?: Record<string, string> };
};
const allModels = Object.entries(catalog.models);

// ---------------------------------------------------------------------------
// Family priority maps (lower number = higher priority)
// ---------------------------------------------------------------------------

const VIDEO_MODES = new Set<string>(['t2v', 'i2v', 'r2v', 'v2v']);

const VIDEO_FAMILY_PRIORITY: Record<string, number> = {
  happyhorse: 1,
  seedance: 2,
  kling: 3,
  pixverse: 4,
  wan: 5,
  vidu: 6,
};

const IMAGE_FAMILY_PRIORITY: Record<string, number> = {
  'gpt-image': 1,
  wan: 2,
  qwen: 3,
};

const FALLBACK_PRIORITY = 999;

function getFamilyPriority(family: string, mode: PlaygroundMode): number {
  const map = VIDEO_MODES.has(mode) ? VIDEO_FAMILY_PRIORITY : IMAGE_FAMILY_PRIORITY;
  return map[family] ?? FALLBACK_PRIORITY;
}

function getDashscopeGateway(modelId: string, model: CatalogModel): string | undefined {
  return model.runtime?.dashscope?.gateway
    ?? catalog.modes?.[catalog.compat?.legacy_model_ids?.[modelId] ?? '']?.runtime?.dashscope?.gateway;
}

// ---------------------------------------------------------------------------
// Duration normalizer
// ---------------------------------------------------------------------------

function normalizeDuration(
  raw: CatalogDuration | null | undefined,
): PlaygroundModelOption['duration'] {
  if (!raw) return null;

  if (raw.type === 'slider') {
    return {
      type: 'slider',
      min: raw.min ?? 1,
      max: raw.max ?? 15,
      step: raw.step ?? 1,
      default: raw.default ?? 5,
    };
  }

  if (raw.type === 'buttons') {
    return {
      type: 'buttons',
      options: raw.options ?? [],
      default: raw.default ?? (raw.options?.[0] ?? 5),
      ...(raw.options_by_resolution ? { options_by_resolution: raw.options_by_resolution } : {}),
    };
  }

  if (raw.type === 'fixed') {
    return {
      type: 'fixed',
      value: raw.value ?? 5,
    };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Params normalizer
// ---------------------------------------------------------------------------

function normalizeParams(
  raw: Record<string, unknown> | undefined,
): PlaygroundModelOption['params'] {
  if (!raw) return {};

  const result: PlaygroundModelOption['params'] = {};

  // resolution
  const res = raw.resolution;
  if (res && typeof res === 'object' && 'options' in (res as object)) {
    const r = res as { options?: string[]; default?: string };
    if (r.options) {
      result.resolution = {
        options: r.options,
        default: r.default ?? r.options[0] ?? '',
      };
    }
  }

  // ratio — catalog uses both "ratio" and "aspectRatio"
  const ratio = raw.ratio ?? raw.aspectRatio;
  if (ratio && typeof ratio === 'object' && 'options' in (ratio as object)) {
    const r = ratio as { options?: string[]; default?: string };
    if (r.options) {
      result.ratio = {
        options: r.options,
        default: r.default ?? r.options[0] ?? '',
      };
    }
  }

  // size (image models use size instead of resolution)
  const size = raw.size;
  if (size && typeof size === 'object' && 'options' in (size as object)) {
    const s = size as { options?: string[]; default?: string };
    if (s.options) {
      result.size = {
        options: s.options,
        default: s.default ?? s.options[0] ?? '',
      };
    }
  }

  // quality (GPT-Image-2)
  const quality = raw.quality;
  if (quality && typeof quality === 'object' && 'options' in (quality as object)) {
    const q = quality as { options?: string[]; default?: string };
    if (q.options) {
      result.quality = {
        options: q.options,
        default: q.default ?? q.options[0] ?? '',
      };
    }
  }

  // audio setting (Wan VideoEdit)
  const audioSetting = raw.audioSetting;
  if (audioSetting && typeof audioSetting === 'object' && 'options' in (audioSetting as object)) {
    const a = audioSetting as { options?: string[]; default?: string };
    if (a.options) {
      result.audioSetting = {
        options: a.options,
        default: a.default ?? a.options[0] ?? '',
      };
    }
  }

  const shotType = raw.shotType;
  if (shotType && typeof shotType === 'object' && 'options' in shotType) {
    const option = shotType as { options?: string[]; default?: string };
    if (option.options) result.shotType = { options: option.options, default: option.default ?? option.options[0] ?? '' };
  }

  const videoMode = raw.mode;
  if (videoMode && typeof videoMode === 'object' && 'options' in videoMode) {
    const option = videoMode as { options?: string[]; default?: string };
    if (option.options) result.mode = { options: option.options, default: option.default ?? option.options[0] ?? '' };
  }

  // boolean flags
  result.seed = normalizeSeedParam(raw.seed);
  result.negativePrompt = normalizeBooleanParam(raw.negativePrompt);
  result.promptExtend = normalizeBooleanParam(raw.promptExtend);
  result.watermark = normalizeBooleanParam(raw.watermark);
  result.audio = normalizeBooleanParam(raw.audio);

  return result;
}

function normalizeBooleanParam(value: unknown): boolean | { default: boolean } | undefined {
  if (typeof value === 'boolean') return value;
  if (value && typeof value === 'object' && typeof (value as { default?: unknown }).default === 'boolean') {
    return { default: (value as { default: boolean }).default };
  }
  return undefined;
}

function normalizeSeedParam(value: unknown): boolean | { min: number; max: number } | undefined {
  if (typeof value === 'boolean') return value;
  if (
    value
    && typeof value === 'object'
    && typeof (value as { min?: unknown }).min === 'number'
    && typeof (value as { max?: unknown }).max === 'number'
  ) {
    return {
      min: (value as { min: number }).min,
      max: (value as { max: number }).max,
    };
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// Model builder
// ---------------------------------------------------------------------------

function toOption(model: CatalogModel): PlaygroundModelOption {
  return {
    id: model.id,
    displayName: model.display_name,
    family: model.family,
    description: model.description,
    recommended: model.ui.recommended ?? false,
    badges: model.ui.badges ?? [],
    capabilities: model.capabilities,
    duration: normalizeDuration(model.duration),
    params: normalizeParams(model.params),
    maxReferenceImages: model.inputs?.reference_images?.max ?? 0,
    referenceImageMimeTypes: model.inputs?.reference_images?.mime_types,
    maxReferenceImageBytes: model.inputs?.reference_images?.max_bytes,
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Return all models whose capabilities include the given mode, sorted by
 * family priority, then recommended-first, then ui.order descending (newer
 * versions first), then alphabetically by display name.
 *
 * Filtering: exclude deprecated/planned models. Include hidden models that
 * have the required capability (they are hidden from Studio but available in
 * Playground — e.g. HappyHorse T2V, Wan 2.7 VideoEdit).
 */
export function getModelsForMode(mode: PlaygroundMode): PlaygroundModelOption[] {
  return allModels
    .filter(([modelId, model]) => {
      if (model.status === 'deprecated' || model.status === 'planned') return false;
      if (getDashscopeGateway(modelId, model) === 'dashscope_workspace' && !allowsPlaygroundWorkspaceGateway(modelId)) return false;
      if (!model.capabilities.includes(mode)) return false;
      return true;
    })
    .sort(([, a], [, b]) => {
      // 1. Family priority
      const famA = getFamilyPriority(a.family, mode);
      const famB = getFamilyPriority(b.family, mode);
      if (famA !== famB) return famA - famB;

      // 2. Recommended first
      const recA = a.ui.recommended ? 0 : 1;
      const recB = b.ui.recommended ? 0 : 1;
      if (recA !== recB) return recA - recB;

      // 3. ui.order descending (higher order = newer version = comes first)
      const orderA = a.ui.order ?? 0;
      const orderB = b.ui.order ?? 0;
      if (orderA !== orderB) return orderB - orderA;

      // 4. Alphabetical fallback
      return a.display_name.localeCompare(b.display_name);
    })
    .map(([, model]) => toOption(model));
}

/**
 * Return the normalized catalog option for a model ID, or null when unknown.
 */
export function getModelOption(modelId: string): PlaygroundModelOption | null {
  const model = catalog.models[resolvePlaygroundCatalogModelId(modelId)];
  if (!model) return null;
  return toOption(model);
}

/**
 * Return the default (recommended or first) model ID for a mode.
 */
export function getDefaultModelForMode(mode: PlaygroundMode): string {
  const models = getModelsForMode(mode);
  const recommended = models.find((m) => m.recommended);
  return recommended?.id ?? models[0]?.id ?? '';
}

/**
 * Lightweight display-info lookup — works for any model ID, regardless of
 * mode or status.
 */
export function getModelDisplayInfo(
  modelId: string,
): { displayName: string; family: string } | null {
  const model = catalog.models[resolvePlaygroundCatalogModelId(modelId)];
  if (!model) return null;
  return { displayName: model.display_name, family: model.family };
}

/**
 * Return the normalized params for a model, or null if the model is unknown.
 */
export function getModelParams(
  modelId: string,
): PlaygroundModelOption['params'] | null {
  const model = catalog.models[resolvePlaygroundCatalogModelId(modelId)];
  if (!model) return null;
  return normalizeParams(model.params);
}

/**
 * Return the normalized duration config for a model (null when the model has
 * no duration knob, e.g. image models).
 */
export function getModelDuration(
  modelId: string,
  resolution?: string,
): PlaygroundModelOption['duration'] {
  const model = catalog.models[resolvePlaygroundCatalogModelId(modelId)];
  if (!model) return null;
  const duration = normalizeDuration(model.duration);
  if (duration?.type !== 'buttons' || !resolution) return duration;
  const options = duration.options_by_resolution?.[resolution];
  if (!options?.length) return duration;
  return {
    ...duration,
    options,
    default: options.includes(duration.default) ? duration.default : options[0],
  };
}

const IMAGE_EXTENSION_BY_MIME: Record<string, RegExp> = {
  'image/jpeg': /\.jpe?g$/i,
  'image/png': /\.png$/i,
  'image/webp': /\.webp$/i,
};

/** Picker eligibility only; Core still validates the actual image bytes. */
export function isReferenceImagePathAllowed(path: string, allowedTypes?: readonly string[]): boolean {
  if (!allowedTypes?.length) return true;
  const name = path.split(/[?#]/, 1)[0];
  return allowedTypes.some((type) => IMAGE_EXTENSION_BY_MIME[type]?.test(name));
}
