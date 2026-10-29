import { persistenceFailure } from '@/lib/persistenceFeedback';

/** A successful write needs a fresh enriched view before the picker can confirm it. */
export class VoiceBindingReadbackError extends Error {}

export function voiceBindingFailure(cause: unknown) {
    return cause instanceof VoiceBindingReadbackError ? 'savedReadFailed' : persistenceFailure(cause);
}
