import { getModelDuration, getModelParams, type PlaygroundModelOption } from './playgroundModels';
import { resolveMigratedPlaygroundVideoModelId } from './playgroundModelAliases';
import type { PlaygroundParameters } from './usePlaygroundStore';

type ParamKey = keyof PlaygroundParameters;
type BooleanParamKey = 'seed' | 'promptExtend' | 'watermark' | 'audio' | 'negativePrompt';

const BOOLEAN_DEFAULTS: Record<Extract<BooleanParamKey, 'promptExtend' | 'watermark' | 'audio'>, boolean> = {
  promptExtend: true,
  watermark: false,
  audio: false,
};

export interface PlaygroundParameterContract {
  modelId: string;
  params: PlaygroundModelOption['params'];
  duration: PlaygroundModelOption['duration'];
}

export function resolvePlaygroundParameterContract(modelId: string): PlaygroundParameterContract | null {
  const resolvedModelId = resolveMigratedPlaygroundVideoModelId(modelId);
  if (!resolvedModelId) return null;
  return {
    modelId: resolvedModelId,
    params: getModelParams(resolvedModelId) ?? {},
    duration: getModelDuration(resolvedModelId),
  };
}

export function isMigratedPlaygroundModel(modelId: string): boolean {
  return resolvePlaygroundParameterContract(modelId) !== null;
}

export function supportsPlaygroundParameter(
  modelId: string,
  key: BooleanParamKey | 'duration' | 'resolution' | 'aspect_ratio' | 'size' | 'quality' | 'audio_setting' | 'shot_type',
): boolean {
  const contract = resolvePlaygroundParameterContract(modelId);
  if (!contract) return true;
  const { params, duration } = contract;
  if (key === 'duration') return duration !== null;
  if (key === 'resolution') return params.resolution !== undefined;
  if (key === 'aspect_ratio') return params.ratio !== undefined;
  if (key === 'size') return params.size !== undefined;
  if (key === 'quality') return params.quality !== undefined;
  if (key === 'audio_setting') return params.audioSetting !== undefined;
  if (key === 'shot_type') return params.shotType !== undefined;
  if (key === 'seed') return seedParamSupported(params.seed);
  return booleanParamSupported(params[key]);
}

export function supportsPlaygroundNegativePrompt(modelId: string): boolean {
  return supportsPlaygroundParameter(modelId, 'negativePrompt');
}

export function getEffectivePlaygroundParameters(
  modelId: string,
  draft: PlaygroundParameters,
): PlaygroundParameters {
  const contract = resolvePlaygroundParameterContract(modelId);
  if (!contract) return draft;

  const effective: PlaygroundParameters = {};
  const { params } = contract;
  addOptionParam(effective, draft, 'resolution', params.resolution);
  const duration = getModelDuration(contract.modelId, effective.resolution);

  if (duration) {
    const value = draft.duration;
    if (duration.type === 'fixed') {
      effective.duration = duration.value;
    } else if (duration.type === 'buttons') {
      effective.duration = typeof value === 'number' && duration.options.includes(value) ? value : duration.default;
    } else if (duration.type === 'slider') {
      effective.duration = typeof value === 'number' && value >= duration.min && value <= duration.max
        && Number.isInteger((value - duration.min) / duration.step) ? value : duration.default;
    }
  }

  addOptionParam(effective, draft, 'shot_type', params.shotType);
  addOptionParam(effective, draft, 'aspect_ratio', params.ratio);
  addOptionParam(effective, draft, 'size', params.size);
  addOptionParam(effective, draft, 'quality', params.quality);
  addOptionParam(effective, draft, 'audio_setting', params.audioSetting);
  addOptionParam(effective, draft, 'mode', params.mode);
  if (booleanParamSupported(params.audio)) {
    effective.audio = typeof draft.audio === 'boolean' ? draft.audio : booleanParamDefault(params.audio, BOOLEAN_DEFAULTS.audio);
  }

  if (seedParamSupported(params.seed) && typeof draft.seed === 'number' && seedInRange(params.seed, draft.seed)) {
    effective.seed = draft.seed;
  }
  if (booleanParamSupported(params.promptExtend)) {
    effective.prompt_extend = typeof draft.prompt_extend === 'boolean'
      ? draft.prompt_extend
      : booleanParamDefault(params.promptExtend, BOOLEAN_DEFAULTS.promptExtend);
  }
  if (booleanParamSupported(params.watermark)) {
    effective.watermark = typeof draft.watermark === 'boolean'
      ? draft.watermark
      : booleanParamDefault(params.watermark, BOOLEAN_DEFAULTS.watermark);
  }

  return effective;
}

export function buildPlaygroundRequestParameters(
  modelId: string,
  draft: PlaygroundParameters,
): PlaygroundParameters {
  const contract = resolvePlaygroundParameterContract(modelId);
  if (!contract) return draft;
  return getEffectivePlaygroundParameters(modelId, draft);
}

export function hasInactivePlaygroundParameterDrafts(
  modelId: string,
  draft: PlaygroundParameters,
): boolean {
  const contract = resolvePlaygroundParameterContract(modelId);
  if (!contract) return false;
  const effective = getEffectivePlaygroundParameters(modelId, draft);
  return Object.entries(draft).some(([key, value]) => {
    if (value === undefined) return false;
    return effective[key] !== value;
  });
}

export function getPlaygroundBooleanDefault(
  modelId: string,
  key: Extract<BooleanParamKey, 'promptExtend' | 'watermark' | 'audio'>,
): boolean | undefined {
  const contract = resolvePlaygroundParameterContract(modelId);
  if (!contract) return undefined;
  const value = contract.params[key];
  if (!booleanParamSupported(value)) return undefined;
  return booleanParamDefault(value, BOOLEAN_DEFAULTS[key]);
}

function addOptionParam(
  effective: PlaygroundParameters,
  draft: PlaygroundParameters,
  requestKey: ParamKey,
  spec: { options: string[]; default: string } | undefined,
): void {
  if (!spec) return;
  const value = draft[requestKey];
  effective[requestKey] = typeof value === 'string' && spec.options.includes(value) ? value : spec.default;
}

function booleanParamSupported(value: boolean | { default: boolean } | undefined): boolean {
  return value !== undefined && value !== false;
}

function seedParamSupported(value: boolean | { min: number; max: number } | undefined): boolean {
  return value !== undefined && value !== false;
}

function seedInRange(value: boolean | { min: number; max: number } | undefined, seed: number): boolean {
  if (typeof value === 'object') return Number.isSafeInteger(seed) && seed >= value.min && seed <= value.max;
  return true;
}

function booleanParamDefault(value: boolean | { default: boolean } | undefined, fallback: boolean): boolean {
  return typeof value === 'object' ? value.default : fallback;
}
