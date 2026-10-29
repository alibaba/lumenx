import { type DialogueAudioState } from "@/lib/api";
import { canApplyRead, observeReadVersions, validateReadVersions } from "@/lib/frameCommandSnapshot";
import { requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { useProjectStore } from "@/store/projectStore";

export const dialogueAudioOperationKey = (projectId: string) =>
    `dialogue-audio:${JSON.stringify([requireActiveRuntimeEnvironment().key, projectId])}`;

export function validateDialogueAudioState(projectId: string, frameId: string, value: unknown): DialogueAudioState {
    const state = value as Partial<DialogueAudioState> | null;
    const versions = state?.read_versions;
    if (!state || state.project_id !== projectId || state.frame_id !== frameId || !versions
        || !Number.isSafeInteger(versions.project) || versions.project < 0
        || !state.options || typeof state.options !== "object" || Array.isArray(state.options)
        || !["missing", "current", "stale", "unknown"].includes(String(state.freshness))) {
        throw new Error("Dialogue audio state did not match the current frame");
    }
    validateReadVersions({ revision: versions.project, series_id: versions.series?.id, read_versions: versions });
    for (const [key, option] of Object.entries(state.options)) {
        if (!["speed", "pitch", "volume", "instructions"].includes(key)
            || (option !== null && (key === "instructions" ? typeof option !== "string"
                : typeof option !== "number" || !Number.isFinite(option) || (key === "volume" && !Number.isInteger(option))))) {
            throw new Error("Invalid saved audio options");
        }
    }
    for (const key of ["audio_url", "audio_error", "historical_instructions", "dialogue", "voice_id", "reason"] as const) {
        if (state[key] != null && typeof state[key] !== "string") throw new Error("Invalid audio state field");
    }
    return state as DialogueAudioState;
}

/** A state read cannot predate project/source versions already seen in this environment. */
function projectForDialogueAudioState(state: DialogueAudioState) {
    const store = useProjectStore.getState();
    const project = store.currentProject?.id === state.project_id ? store.currentProject
        : store.projects.find(value => value.id === state.project_id);
    return { store, project };
}

function dialogueAudioReadCandidate(state: DialogueAudioState) {
    const { project } = projectForDialogueAudioState(state);
    if (!project) return null;
    const versions = state.read_versions;
    if ((project.series_id ?? null) !== (versions.series?.id ?? null)) return null;
    return { ...project, revision: versions.project, read_versions: versions, readEnvironment: requireActiveRuntimeEnvironment().key,
        series_id: versions.series?.id ?? undefined };
}

/** A state read cannot predate project/source versions already seen in this environment. */
export function audioStateMeetsKnownVersions(state: DialogueAudioState): boolean {
    const { store, project } = projectForDialogueAudioState(state);
    if (!project) return false;
    const candidate = dialogueAudioReadCandidate(state);
    if (!candidate) return false;
    const environment = requireActiveRuntimeEnvironment().key;
    return canApplyRead(project, candidate, store.readWatermarks[environment] ?? { series: {} }, environment);
}

/** Records source floors observed by an accepted audio/state read without replacing project content. */
export function recordDialogueAudioRead(state: DialogueAudioState): boolean {
    try {
        validateReadVersions({ revision: state.read_versions.project, series_id: state.read_versions.series?.id, read_versions: state.read_versions });
    } catch {
        return false;
    }
    if (!audioStateMeetsKnownVersions(state)) return false;
    const { store } = projectForDialogueAudioState(state);
    const candidate = dialogueAudioReadCandidate(state);
    if (!candidate) return false;
    const environment = requireActiveRuntimeEnvironment().key;
    useProjectStore.setState({
        readWatermarks: observeReadVersions(store.readWatermarks, [
            ...store.projects,
            ...(store.currentProject ? [store.currentProject] : []),
            candidate,
        ], environment),
    });
    return true;
}

export function audioStateLabelKey(state: DialogueAudioState | null | undefined): "empty" | "ready" | "stale" | "unknown" {
    if (!state) return "unknown";
    if (state.freshness === "current") return "ready";
    if (state.freshness === "stale") return "stale";
    if (state.freshness === "missing") return "empty";
    return "unknown";
}
