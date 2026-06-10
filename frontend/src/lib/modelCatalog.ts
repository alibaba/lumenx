import rawCatalog from '@/generated/modelCatalog.json';

export type DurationConfig =
    | { type: 'slider'; min: number; max: number; step: number; default: number }
    | { type: 'buttons'; options: number[]; default: number }
    | { type: 'fixed'; value: number };

export interface ModelParamSupport {
    resolution?: { options: string[]; default: string };
    ratio?: { options: string[]; default: string };
    prompt?: { maxLength?: number };
    seed?: boolean;
    negativePrompt?: boolean;
    promptExtend?: boolean;
    shotType?: boolean | { options: string[]; default: string };
    audio?: boolean;
    mode?: { options: string[]; default: string };
    sound?: boolean;
    cfgScale?: { min: number; max: number; step: number; default: number };
    viduAudio?: boolean;
    movementAmplitude?: { options: string[]; default: string };
    watermark?: boolean;
}

export interface I2VModelConfig {
    id: string;
    name: string;
    description: string;
    duration: DurationConfig;
    params: ModelParamSupport;
    badges?: string[];
    recommended?: boolean;
    family?: string;
    status?: string;
}

export interface SelectableModelOption {
    id: string;
    name: string;
    description: string;
    badges?: string[];
    recommended?: boolean;
    family?: string;
    status?: string;
}

export type ModelOption = SelectableModelOption;

export interface FrontendModelSettings {
    t2i_model: string;
    i2i_model: string;
    image_model: string;
    i2v_model: string;
    /** Project-level R2V default. Storyboard's R2V tab uses it as
     *  initial value; per-storyboard localStorage override still wins.
     *  Optional on the wire so older project files still parse. */
    r2v_model?: string;
    character_aspect_ratio: string;
    scene_aspect_ratio: string;
    prop_aspect_ratio: string;
    storyboard_aspect_ratio: string;
}

type SelectionGroup = 't2i' | 'i2i' | 'image' | 'i2v' | 'r2v';
type ModelStatus = 'active' | 'planned' | 'deprecated' | 'hidden';
type SettingsSurface = 'project_settings' | 'series_settings' | 'global_settings';
type VisibilitySurface = SettingsSurface | 'video_sidebar';

interface CatalogModel {
    id: string;
    display_name: string;
    description: string;
    family: string;
    status: ModelStatus;
    capabilities: string[];
    duration?: DurationConfig | null;
    params?: ModelParamSupport;
    inputs?: {
        reference_images?: {
            max?: number;
        };
        [key: string]: unknown;
    };
    ui: {
        selection_group: SelectionGroup;
        visible_in: VisibilitySurface[];
        recommended?: boolean;
        order?: number;
        badges?: string[];
    };
}

interface ModelCatalog {
    defaults: {
        model_settings: {
            t2i_model: string;
            i2i_model: string;
            image_model: string;
            i2v_model: string;
        };
        canonical_model_settings?: {
            t2i_model?: string;
            i2i_model?: string;
            image_model?: string;
            i2v_model?: string;
        };
    };
    models: Record<string, CatalogModel>;
    model_lines: Record<
        string,
        {
            id: string;
            family: string;
            modes: string[];
            legacy_model_ids: string[];
            runtime?: Record<string, Record<string, unknown>>;
            [key: string]: unknown;
        }
    >;
    modes: Record<
        string,
        {
            id: string;
            model_line_id: string;
            legacy_model_id: string;
            mode: string;
            family: string;
            status: ModelStatus;
            capabilities: string[];
            runtime: Record<string, Record<string, unknown>>;
            ui: {
                selection_group: SelectionGroup;
                visible_in: VisibilitySurface[];
                recommended?: boolean;
                order?: number;
                badges?: string[];
            };
            [key: string]: unknown;
        }
    >;
    compat: {
        legacy_model_ids: Record<string, string>;
    };
}

const MODEL_CATALOG = rawCatalog as ModelCatalog;
const CATALOG_MODELS = Object.values(MODEL_CATALOG.models);
const LEGACY_MODEL_ID_ALIASES = MODEL_CATALOG.compat.legacy_model_ids;
const CANONICAL_MODEL_ID_ALIASES = Object.freeze(
    Object.fromEntries(
        Object.entries(LEGACY_MODEL_ID_ALIASES).map(([legacyModelId, canonicalModeId]) => [
            canonicalModeId,
            legacyModelId,
        ])
    ) as Record<string, string>
);

// ---------------------------------------------------------------------------
// Phase 2: Canonical mode internal helpers
// ---------------------------------------------------------------------------

/** Resolve a legacy flat ID to its canonical mode ID, or undefined. */
export function getCanonicalModeId(legacyId: string): string | undefined {
    return LEGACY_MODEL_ID_ALIASES[legacyId];
}

/** Resolve a canonical mode ID back to its legacy flat ID, or undefined. */
export function getLegacyModelId(canonicalModeId: string): string | undefined {
    return CANONICAL_MODEL_ID_ALIASES[canonicalModeId];
}

/** Get the canonical mode entry for a mode ID. */
export function getCanonicalModeEntry(canonicalModeId: string) {
    return MODEL_CATALOG.modes[canonicalModeId] ?? null;
}

/** Get the model line entry for a model line ID. */
export function getModelLineEntry(modelLineId: string) {
    return MODEL_CATALOG.model_lines[modelLineId] ?? null;
}

/** Get the gateway value for a canonical mode on a backend. */
export function getModeGateway(
    canonicalModeId: string,
    backend: string = 'dashscope'
): string | undefined {
    const mode = MODEL_CATALOG.modes[canonicalModeId];
    if (!mode) return undefined;
    const backendMeta = mode.runtime?.[backend];
    if (!backendMeta) return undefined;
    return backendMeta.gateway as string | undefined;
}

/** Get canonical default model settings. */
export function getCanonicalDefaults(): Record<string, string> {
    return { ...(MODEL_CATALOG.defaults.canonical_model_settings ?? {}) };
}

const DEFAULT_ASPECT_RATIOS = Object.freeze({
    character_aspect_ratio: '9:16',
    scene_aspect_ratio: '16:9',
    prop_aspect_ratio: '1:1',
    storyboard_aspect_ratio: '16:9',
});

export const DEFAULT_MODEL_SETTINGS: FrontendModelSettings = Object.freeze({
    ...MODEL_CATALOG.defaults.model_settings,
    ...DEFAULT_ASPECT_RATIOS,
});

const SORTED_MODEL_ENTRIES = [...CATALOG_MODELS].sort((left, right) => {
    const orderDelta = (right.ui.order ?? 0) - (left.ui.order ?? 0);
    if (orderDelta !== 0) {
        return orderDelta;
    }
    return left.display_name.localeCompare(right.display_name);
});

function isVisibleModel(model: CatalogModel, surface: VisibilitySurface): boolean {
    return (
        model.status !== 'planned' &&
        model.status !== 'deprecated' &&
        model.status !== 'hidden' &&
        model.ui.visible_in.includes(surface)
    );
}

function getVisibleModels(group: SelectionGroup, surface: VisibilitySurface): CatalogModel[] {
    // Strict match: model declared its primary selection_group as `group`.
    const direct = SORTED_MODEL_ENTRIES.filter(
        (model) => model.ui.selection_group === group && isVisibleModel(model, surface)
    );
    // Capability fallback: when the strict bucket is empty for t2i/i2i (the
    // current catalog ships only `image`-group models that can do both),
    // accept any visible image-group model that declares the matching
    // capability. Without this, resolveModelId() always falls through to
    // catalog defaults — meaning user-picked t2i/i2i selections silently
    // revert on the next render. (See PR-3* assembly model picker bug.)
    if (direct.length > 0 || (group !== 't2i' && group !== 'i2i')) {
        return direct;
    }
    const capability = group; // 't2i' | 'i2i'
    return SORTED_MODEL_ENTRIES.filter(
        (model) =>
            model.ui.selection_group === 'image' &&
            model.capabilities.includes(capability) &&
            isVisibleModel(model, surface)
    );
}

function toSelectableModel(model: CatalogModel): SelectableModelOption {
    return {
        id: model.id,
        name: model.display_name,
        description: model.description,
        badges: model.ui.badges ?? [],
        recommended: !!model.ui.recommended,
        family: model.family,
        status: model.status,
    };
}

function toI2VModel(model: CatalogModel): I2VModelConfig {
    return {
        id: model.id,
        name: model.display_name,
        description: model.description,
        duration: model.duration ?? { type: 'fixed', value: 5 },
        params: model.params ?? {},
        badges: model.ui.badges ?? [],
        recommended: !!model.ui.recommended,
        family: model.family,
        status: model.status,
    };
}

function getConfiguredDefaultId(group: SelectionGroup): string {
    if (group === 't2i') {
        return MODEL_CATALOG.defaults.model_settings.t2i_model;
    }
    if (group === 'i2i') {
        return MODEL_CATALOG.defaults.model_settings.i2i_model;
    }
    if (group === 'image') {
        return MODEL_CATALOG.defaults.model_settings.image_model;
    }
    if (group === 'r2v') {
        return (MODEL_CATALOG.defaults.model_settings as Record<string, string>).r2v_model
            ?? MODEL_CATALOG.defaults.model_settings.i2v_model;
    }
    return MODEL_CATALOG.defaults.model_settings.i2v_model;
}

function getFallbackVisibleModelId(group: SelectionGroup, surface: VisibilitySurface): string {
    const visibleModels = getVisibleModels(group, surface);
    const configuredDefaultId = getConfiguredDefaultId(group);

    if (visibleModels.some((model) => model.id === configuredDefaultId)) {
        return configuredDefaultId;
    }

    return visibleModels[0]?.id ?? configuredDefaultId;
}

function warnModelFallback(
    group: SelectionGroup,
    requestedId: string,
    surface: VisibilitySurface,
    fallbackId: string
): void {
    console.warn(
        `[model_catalog] Falling back ${group} model "${requestedId}" to "${fallbackId}" for ${surface}.`
    );
}

function normalizeRequestedModelId(requestedId: string | null | undefined): string | undefined {
    if (!requestedId) {
        return undefined;
    }

    return CANONICAL_MODEL_ID_ALIASES[requestedId] ?? requestedId;
}

export function resolveModelId(
    group: SelectionGroup,
    requestedId: string | null | undefined,
    surface: VisibilitySurface
): string {
    const visibleModels = getVisibleModels(group, surface);
    const normalizedRequestedId = normalizeRequestedModelId(requestedId);

    if (normalizedRequestedId && visibleModels.some((model) => model.id === normalizedRequestedId)) {
        return normalizedRequestedId;
    }

    const fallbackId = getFallbackVisibleModelId(group, surface);
    if (requestedId && normalizedRequestedId !== fallbackId) {
        warnModelFallback(group, requestedId, surface, fallbackId);
    }
    return fallbackId;
}

export function resolveModelSettings(
    settings?: Partial<FrontendModelSettings> | null,
    surface: SettingsSurface = 'project_settings'
): FrontendModelSettings {
    return {
        ...DEFAULT_MODEL_SETTINGS,
        ...settings,
        t2i_model: resolveModelId('t2i', settings?.t2i_model, surface),
        i2i_model: resolveModelId('i2i', settings?.i2i_model, surface),
        image_model: resolveModelId('image', settings?.image_model, surface),
        i2v_model: resolveModelId('i2v', settings?.i2v_model, surface),
        r2v_model: resolveModelId('r2v', settings?.r2v_model, surface),
        character_aspect_ratio:
            settings?.character_aspect_ratio || DEFAULT_MODEL_SETTINGS.character_aspect_ratio,
        scene_aspect_ratio:
            settings?.scene_aspect_ratio || DEFAULT_MODEL_SETTINGS.scene_aspect_ratio,
        prop_aspect_ratio:
            settings?.prop_aspect_ratio || DEFAULT_MODEL_SETTINGS.prop_aspect_ratio,
        storyboard_aspect_ratio:
            settings?.storyboard_aspect_ratio || DEFAULT_MODEL_SETTINGS.storyboard_aspect_ratio,
    };
}

export const normalizeModelSettings = resolveModelSettings;
export const normalizeModelId = resolveModelId;

export function getMaxReferenceImages(modelId?: string | null): number {
    const resolvedModelId = resolveModelId('i2i', modelId, 'project_settings');
    const maxReferenceImages =
        MODEL_CATALOG.models[resolvedModelId]?.inputs?.reference_images?.max;

    return typeof maxReferenceImages === 'number' ? maxReferenceImages : 3;
}

export const PROJECT_T2I_MODELS = getVisibleModels('t2i', 'project_settings').map(toSelectableModel);
export const SERIES_T2I_MODELS = getVisibleModels('t2i', 'series_settings').map(toSelectableModel);
export const GLOBAL_T2I_MODELS = getVisibleModels('t2i', 'global_settings').map(toSelectableModel);

export const PROJECT_I2I_MODELS = getVisibleModels('i2i', 'project_settings').map(toSelectableModel);
export const SERIES_I2I_MODELS = getVisibleModels('i2i', 'series_settings').map(toSelectableModel);
export const GLOBAL_I2I_MODELS = getVisibleModels('i2i', 'global_settings').map(toSelectableModel);

export const PROJECT_IMAGE_MODELS = getVisibleModels('image', 'project_settings').map(toSelectableModel);
export const SERIES_IMAGE_MODELS = getVisibleModels('image', 'series_settings').map(toSelectableModel);
export const GLOBAL_IMAGE_MODELS = getVisibleModels('image', 'global_settings').map(toSelectableModel);

export const PROJECT_I2V_MODELS = getVisibleModels('i2v', 'project_settings').map(toI2VModel);
export const SERIES_I2V_MODELS = getVisibleModels('i2v', 'series_settings').map(toI2VModel);
export const GLOBAL_I2V_MODELS = getVisibleModels('i2v', 'global_settings').map(toI2VModel);
export const GLOBAL_R2V_MODELS = getVisibleModels('r2v', 'global_settings').map(toI2VModel);
export const VIDEO_I2V_MODELS = getVisibleModels('i2v', 'video_sidebar').map(toI2VModel);

export const T2I_MODELS = PROJECT_T2I_MODELS;
export const I2I_MODELS = PROJECT_I2I_MODELS;
export const IMAGE_MODELS = PROJECT_IMAGE_MODELS;
export const I2V_MODELS = PROJECT_I2V_MODELS;
export const VIDEO_SIDEBAR_I2V_MODELS = VIDEO_I2V_MODELS;

export const DEFAULT_I2V_MODEL_ID = resolveModelId('i2v', undefined, 'video_sidebar');

const R2V_CANDIDATES = SORTED_MODEL_ENTRIES.filter((model) =>
    model.capabilities.includes('r2v')
);
export const R2V_SELECTION_MODEL_ID = R2V_CANDIDATES.find(
    (model) => isVisibleModel(model, 'video_sidebar') && model.ui.selection_group === 'r2v'
)?.id ?? DEFAULT_I2V_MODEL_ID;
export const R2V_ROUTE_MODEL_ID = R2V_SELECTION_MODEL_ID;

export function isR2vSelectionModel(modelId: string): boolean {
    return modelId === R2V_SELECTION_MODEL_ID;
}

// ---------------------------------------------------------------------------
// Dynamic R2V routing: resolve the hidden R2V model per-family
// ---------------------------------------------------------------------------

/** Map from family name to R2V route model ID. */
const R2V_ROUTE_MAP: Record<string, string> = {};
for (const model of SORTED_MODEL_ENTRIES) {
    if (model.capabilities.includes('r2v') && model.ui.selection_group === 'r2v') {
        if (!R2V_ROUTE_MAP[model.family]) {
            R2V_ROUTE_MAP[model.family] = model.id;
        }
    }
}

export const VIDEO_R2V_MODELS: I2VModelConfig[] = SORTED_MODEL_ENTRIES
    .filter((model) => model.ui.selection_group === 'r2v' && isVisibleModel(model, 'video_sidebar'))
    .map(toI2VModel);
export const DEFAULT_R2V_MODEL_ID = VIDEO_R2V_MODELS[0]?.id ?? R2V_SELECTION_MODEL_ID;

/**
 * Given the currently selected I2V model, resolve the correct R2V route model.
 * Each family has its own hidden R2V model (e.g. wan -> wan2.6-r2v, happyhorse -> happyhorse-1.0-r2v).
 */
export function getR2vRouteModelId(selectedI2vModelId: string): string {
    const selectedModel = MODEL_CATALOG.models[selectedI2vModelId];
    if (!selectedModel) return R2V_ROUTE_MODEL_ID;
    return R2V_ROUTE_MAP[selectedModel.family] ?? R2V_ROUTE_MODEL_ID;
}

/**
 * Returns true if the given R2V model uses image references
 * instead of video references (Wan 2.5/2.6 legacy).
 */
export function isR2vImageBased(modelId: string): boolean {
    const model = MODEL_CATALOG.models[modelId];
    const family = model?.family;
    if (family === 'wan' && modelId === 'wan2.6-r2v') return false;
    return family === 'happyhorse' || family === 'wan' || family === 'kling'
        || family === 'pixverse' || family === 'vidu' || family === 'seedance';
}
