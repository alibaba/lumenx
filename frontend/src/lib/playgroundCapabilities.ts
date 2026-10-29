import { getCanonicalModeEntry, getCanonicalModeId } from './modelCatalog';
import { isCurrentRuntimeEnvironment, type RuntimeEnvironment } from './runtimeEnvironment';

const GATE_REASONS = ['seedance_http_key_required', 'seedance_http_endpoint_unsupported', 'seedance_cli_submission_unsafe'] as const;
export type SeedanceGateReason = typeof GATE_REASONS[number];
export type PlaygroundBlockReason = SeedanceGateReason | 'seedance_images_only' | 'seedance_image_count' | 'seedance_mode_mismatch' | 'seedance_capability_check_failed';
export type PlaygroundCapabilities = {
  schema_version: 1;
  seedance: { available: boolean; reason: SeedanceGateReason | null; channel: 'mulerouter_http' | null; input_kinds: ['image'] };
};
const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const keys = (v: Record<string, unknown>, expected: string[]) => Object.keys(v).length === expected.length && expected.every(k => Object.hasOwn(v, k));
const gateReason = (v: unknown): v is SeedanceGateReason => GATE_REASONS.some(reason => reason === v);

export function parsePlaygroundCapabilities(raw: unknown): PlaygroundCapabilities {
  if (!record(raw) || !keys(raw, ['schema_version', 'seedance']) || raw.schema_version !== 1 || !record(raw.seedance)) throw new Error('Invalid Playground capabilities');
  const s = raw.seedance;
  if (!keys(s, ['available', 'reason', 'channel', 'input_kinds']) || !Array.isArray(s.input_kinds) || s.input_kinds.length !== 1 || s.input_kinds[0] !== 'image'
    || !(s.available === true && s.reason === null && s.channel === 'mulerouter_http'
      || s.available === false && gateReason(s.reason) && s.channel === null)) throw new Error('Invalid Seedance capabilities');
  return { schema_version: 1, seedance: { available: s.available, reason: s.reason as SeedanceGateReason | null, channel: s.channel as 'mulerouter_http' | null, input_kinds: ['image'] } };
}

function seedanceEntry(modelId: string) {
  const entry = getCanonicalModeEntry(getCanonicalModeId(modelId) ?? modelId);
  return entry?.family === 'seedance' ? entry : null;
}
export const isSeedanceModel = (modelId: string): boolean => seedanceEntry(modelId) !== null;

export function seedanceInputBlockReason(modelId: string, mode: string, media: readonly string[]): PlaygroundBlockReason | null {
  const entry = seedanceEntry(modelId);
  if (!entry) return null;
  if (entry.mode !== mode) return 'seedance_mode_mismatch';
  if (media.some(path => !/\.(png|jpe?g|webp)$/i.test(path))) return 'seedance_images_only';
  const inputs = entry.inputs as { reference_images?: { max?: number } } | undefined;
  const max = inputs?.reference_images?.max ?? 0;
  if (media.length > max || (mode !== 't2v' && media.length === 0)) return 'seedance_image_count';
  return null;
}

export class PlaygroundCapabilityError extends Error {
  constructor(public readonly reason: PlaygroundBlockReason) { super(reason); }
}

/** A fresh read before a new journal or dispatch; never invokes a generation route. */
export async function requireSeedanceCapability(
  input: { modelId: string; mode: string; inputMedia: readonly string[] },
  environment: RuntimeEnvironment,
  load: () => Promise<unknown>,
): Promise<void> {
  if (!isSeedanceModel(input.modelId)) return;
  const inputReason = seedanceInputBlockReason(input.modelId, input.mode, input.inputMedia);
  if (inputReason) throw new PlaygroundCapabilityError(inputReason);
  let capability: PlaygroundCapabilities;
  try { capability = parsePlaygroundCapabilities(await load()); }
  catch { throw new PlaygroundCapabilityError('seedance_capability_check_failed'); }
  if (!isCurrentRuntimeEnvironment(environment.key, environment.generation)) throw new PlaygroundCapabilityError('seedance_capability_check_failed');
  if (!capability.seedance.available) throw new PlaygroundCapabilityError(capability.seedance.reason!);
}

/** Only this exact backend contract proves no admission occurred. */
export function seedanceNotAdmittedReason(error: unknown): SeedanceGateReason | null {
  if (!record(error) || !record(error.response) || error.response.status !== 503 || !record(error.response.data) || !record(error.response.data.detail)) return null;
  const detail = error.response.data.detail;
  return keys(detail, ['code', 'reason']) && detail.code === 'playground_generation_not_admitted' && gateReason(detail.reason) ? detail.reason : null;
}
