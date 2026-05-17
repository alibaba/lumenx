import rawCatalog from '@/generated/modelCatalog.json';

export type DurationConfig =
    | { type: 'slider'; min: number; max: number; step: number; default: number }
    | { type: 'buttons'; options: number[]; default: number }
    | { type: 'fixed'; value: number };

export interface ModelParamSupport {
    resolution?: { options: string[]; default: string };
    ratio?: { options: string[]; default: string };
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
    character_aspect_ratio: string;
    scene_aspect_ratio: string;
    prop_aspect_ratio: string;
    storyboard_aspect_ratio: string;
}

type SelectionGroup = 't2i' | 'i2i' | 'image' | 'i2v';
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
            reference_type?: 'image' | 'video';
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
        model.status !== 'hidden' &&
        model.ui.visible_in.includes(surface)
    );
}

function getVisibleModels(group: SelectionGroup, surface: VisibilitySurface): CatalogModel[] {
    return SORTED_MODEL_ENTRIES.filter(
        (model) => model.ui.selection_group === group && isVisibleModel(model, surface)
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
    const normalizedModelId = normalizeRequestedModelId(modelId);
    const resolvedModelId = normalizedModelId && MODEL_CATALOG.models[normalizedModelId]
        ? normalizedModelId
        : resolveModelId('image', modelId, 'project_settings');
    const maxReferenceImages =
        MODEL_CATALOG.models[resolvedModelId]?.inputs?.reference_images?.max;

    return typeof maxReferenceImages === 'number' ? maxReferenceImages : 3;
}

export type R2VReferenceInputType = 'image' | 'video';

export type R2VReferenceInputConfig = {
    type: R2VReferenceInputType;
    max: number;
};

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
export const VIDEO_I2V_MODELS = getVisibleModels('i2v', 'video_sidebar').map(toI2VModel);

export const T2I_MODELS = PROJECT_T2I_MODELS;
export const I2I_MODELS = PROJECT_I2I_MODELS;
export const IMAGE_MODELS = PROJECT_IMAGE_MODELS;
export const I2V_MODELS = PROJECT_I2V_MODELS;
export const VIDEO_SIDEBAR_I2V_MODELS = VIDEO_I2V_MODELS;

const R2V_CANDIDATES = SORTED_MODEL_ENTRIES.filter((model) =>
    model.capabilities.includes('r2v')
);

export const DEFAULT_I2V_MODEL_ID = resolveModelId('i2v', undefined, 'video_sidebar');
export const R2V_SELECTION_MODEL_ID =
    R2V_CANDIDATES.find((model) => isVisibleModel(model, 'video_sidebar'))?.id ??
    DEFAULT_I2V_MODEL_ID;
export const R2V_ROUTE_MODEL_ID =
    R2V_CANDIDATES.find((model) => model.ui.visible_in.length === 0)?.id ??
    R2V_SELECTION_MODEL_ID;

export function isR2vSelectionModel(modelId: string): boolean {
    return modelId === R2V_SELECTION_MODEL_ID;
}

// ---------------------------------------------------------------------------
// Dynamic R2V routing: resolve the hidden R2V model per-family
// ---------------------------------------------------------------------------

/** Map from family name to hidden R2V route model ID. */
const R2V_ROUTE_MAP: Record<string, string> = {};
for (const model of SORTED_MODEL_ENTRIES) {
    if (model.capabilities.includes('r2v') && model.ui.visible_in.length === 0) {
        if (!R2V_ROUTE_MAP[model.family]) {
            R2V_ROUTE_MAP[model.family] = model.id;
        }
    }
}

/**
 * Given the currently selected I2V model, resolve the correct R2V route model.
 * Each family has its own hidden R2V model (e.g. wan -> wan2.6-r2v, happyhorse -> happyhorse-1.0-r2v).
 */
export function getR2vRouteModelId(selectedI2vModelId: string): string {
    const selectedFlatModelId = normalizeRequestedModelId(selectedI2vModelId) ?? selectedI2vModelId;
    const selectedCanonicalModeId = getCanonicalModeId(selectedFlatModelId);
    const selectedCanonicalMode = selectedCanonicalModeId
        ? getCanonicalModeEntry(selectedCanonicalModeId)
        : null;

    if (selectedCanonicalMode?.model_line_id) {
        const modelLine = getModelLineEntry(selectedCanonicalMode.model_line_id);
        const routeCanonicalModeId = modelLine?.modes?.find((modeId) => {
            const mode = getCanonicalModeEntry(modeId);
            return mode?.mode === 'r2v' && (mode.capabilities ?? []).includes('r2v');
        });
        const routeLegacyModelId = routeCanonicalModeId
            ? getLegacyModelId(routeCanonicalModeId)
            : undefined;
        if (routeLegacyModelId) {
            return routeLegacyModelId;
        }
    }

    const selectedModel = MODEL_CATALOG.models[selectedFlatModelId];
    if (!selectedModel) return R2V_ROUTE_MODEL_ID;
    return R2V_ROUTE_MAP[selectedModel.family] ?? R2V_ROUTE_MODEL_ID;
}

/**
 * Returns the concrete reference input contract for a routed R2V model.
 * The odd `reference_images.reference_type` catalog key is kept for backward
 * compatibility: reference_type decides whether those refs are image or video URLs.
 */
export function getR2vReferenceInputConfig(modelId: string): R2VReferenceInputConfig {
    const model = MODEL_CATALOG.models[normalizeRequestedModelId(modelId) ?? modelId];
    const referenceImages = model?.inputs?.reference_images;
    const type = referenceImages?.reference_type === 'video' ? 'video' : 'image';
    const max = typeof referenceImages?.max === 'number'
        ? referenceImages.max
        : type === 'video'
          ? 3
          : 9;

    return { type, max };
}

export function isR2vImageBased(modelId: string): boolean {
    return getR2vReferenceInputConfig(modelId).type === 'image';
}

// ---------------------------------------------------------------------------
// Atelier capability validation
// ---------------------------------------------------------------------------

/** Resolve a catalog model by its display_name (used by Atelier composer chips
 *  which surface user-readable labels rather than canonical ids). */
export function getModelByDisplayName(label: string):
    | {
          id: string;
          display_name: string;
          family: string;
          capabilities: string[];
          inputs?: CatalogModel['inputs'];
      }
    | undefined {
    if (!label) return undefined;
    const normalized = label.trim().toLowerCase();
    for (const model of CATALOG_MODELS) {
        if (model.display_name.trim().toLowerCase() === normalized) {
            return {
                id: model.id,
                display_name: model.display_name,
                family: model.family,
                capabilities: model.capabilities,
                inputs: model.inputs,
            };
        }
    }
    return undefined;
}

export type AtelierRefKind = 'image' | 'video' | 'audio';

export interface AtelierCapabilityCheck {
    ok: boolean;
    /** Short, user-facing reason. Composer banner concatenates it after the
     *  model display name, e.g. "Wan 2.7 doesn't accept video references." */
    reason?: string;
}

/** Validate that `refs` are acceptable inputs for the model identified by
 *  `displayLabel`. Returns `{ ok: true }` when:
 *    - the label doesn't resolve to a catalog entry (we only enforce when we
 *      have ground truth — never block on unknowns)
 *    - the model declares no `reference_images` constraint AND refs is empty
 *    - all constraints (max + reference_type) pass
 *
 *  Returns `{ ok: false, reason }` for the first violated constraint. */
export function validateAtelierRefs(
    displayLabel: string,
    refs: ReadonlyArray<{ kind?: AtelierRefKind | string }>,
): AtelierCapabilityCheck {
    const model = getModelByDisplayName(displayLabel);
    if (!model) return { ok: true };
    const constraint = model.inputs?.reference_images;
    if (!constraint) {
        return refs.length > 0
            ? { ok: false, reason: "doesn't accept references" }
            : { ok: true };
    }
    if (typeof constraint.max === 'number' && refs.length > constraint.max) {
        const noun = constraint.max === 1 ? 'reference' : 'references';
        return {
            ok: false,
            reason: `accepts at most ${constraint.max} ${noun}`,
        };
    }
    if (constraint.reference_type === 'image') {
        const offender = refs.find((r) => r.kind === 'video' || r.kind === 'audio');
        if (offender) {
            return {
                ok: false,
                reason: `doesn't accept ${offender.kind} references`,
            };
        }
    }
    if (constraint.reference_type === 'video') {
        const offender = refs.find((r) => r.kind === 'image' || r.kind === 'audio');
        if (offender) {
            return {
                ok: false,
                reason: `doesn't accept ${offender.kind} references`,
            };
        }
    }
    return { ok: true };
}
