import { create } from "zustand";
import { api } from "@/lib/api";
import { persistenceFailure, type PersistenceFailure } from "@/lib/persistenceFeedback";
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { useProjectStore } from "@/store/projectStore";

export interface ScriptDraft {
    text: string;
    savedText: string;
    revision: number | undefined;
    generation?: number;
    saving: boolean;
    error: PersistenceFailure | null;
}

interface ScriptDraftState {
    drafts: Record<string, ScriptDraft>;
    initialize: (key: string, text: string, revision: number | undefined) => void;
    changeText: (key: string, text: string) => void;
    markConflict: (key: string) => void;
    adoptReviewed: (key: string, projectId: string, savedText: string, revision: number, retainDraft: boolean) => boolean;
    acknowledgeExtraction: (key: string, projectId: string, baseline: number, text: string, revision: number) => void;
    save: (key: string, projectId: string, retry?: boolean) => Promise<void>;
}

// Session-only drafts survive React navigation, but never imply disk persistence.
// Include the backend identity so different environments cannot share a draft.
export const scriptDraftKey = (projectId: string) => JSON.stringify([requireActiveRuntimeEnvironment().key, projectId]);
const pendingBlurs = new Map<string, string>();
const saveRequests = new Map<string, object>();

export const useScriptDraftStore = create<ScriptDraftState>((set, get) => {
    const update = (key: string, patch: Partial<ScriptDraft>) => {
        set(state => ({ drafts: { ...state.drafts, [key]: { ...state.drafts[key], ...patch } } }));
    };

    return {
        drafts: {},
        initialize: (key, text, revision) => {
            const draft = get().drafts[key];
            const generation = getRuntimeEnvironment()?.generation;
            if (draft && draft.generation !== generation) {
                saveRequests.delete(key);
                pendingBlurs.delete(key);
                if (draft.saving || draft.error || draft.text !== draft.savedText) {
                    update(key, { generation, saving: false, error: draft.saving ? 'conflict' : draft.error });
                    return;
                }
            }
            if (draft && (draft.saving || draft.error || draft.text !== draft.savedText)) return;
            if (draft?.revision === revision && draft.savedText === text && draft.generation === generation) return;
            update(key, { text, savedText: text, revision, generation, saving: false, error: null });
        },
        changeText: (key, text) => update(key, { text }),
        markConflict: key => {
            if (get().drafts[key]) update(key, { error: 'conflict' });
        },
        adoptReviewed: (key, projectId, savedText, revision, retainDraft) => {
            const environment = getRuntimeEnvironment();
            const draft = get().drafts[key];
            const current = useProjectStore.getState().currentProject;
            if (!environment || key !== JSON.stringify([environment.key, projectId]) || !draft || draft.saving || draft.generation !== environment.generation
                || current?.id !== projectId || current.readEnvironment !== environment.key
                || current.sharedAssetsNeedRefresh || current.revision !== revision || current.originalText !== savedText) return false;
            // Only this explicit review choice may rebase a retained dirty draft.
            pendingBlurs.delete(key);
            update(key, { savedText, revision, error: null, ...(retainDraft ? {} : { text: savedText }) });
            return true;
        },
        acknowledgeExtraction: (key, projectId, baseline, text, revision) => {
            const environment = getRuntimeEnvironment();
            const draft = get().drafts[key];
            if (!environment || key !== JSON.stringify([environment.key, projectId]) || draft?.revision !== baseline) return;
            // A confirmed reparse saves this text too; retain any newer typing.
            update(key, { savedText: text, revision, error: null });
        },
        save: async (key, projectId, retry = false) => {
            const environment = getRuntimeEnvironment();
            if (!environment || key !== JSON.stringify([environment.key, projectId])) return;
            const initial = get().drafts[key];
            if (!initial || initial.error === "conflict" || (initial.error && !retry)) return;
            if (initial.generation !== environment.generation) return;
            if (!initial.saving && initial.text === initial.savedText) return;

            // Only blur (or explicit retry) enqueues a snapshot; typing alone does
            // not. While one write is pending, coalesce later blurs to the latest.
            pendingBlurs.set(key, initial.text);
            if (initial.saving) return;
            const request = {};
            saveRequests.set(key, request);
            const ownsDraft = () => saveRequests.get(key) === request && get().drafts[key]?.generation === environment.generation;
            const isCurrent = () => ownsDraft() && isCurrentRuntimeEnvironment(environment.key, environment.generation);
            update(key, { saving: true, error: null });

            try {
                while (pendingBlurs.has(key)) {
                    if (!isCurrent()) return;
                    const text = pendingBlurs.get(key)!;
                    pendingBlurs.delete(key);
                    const current = get().drafts[key];
                    if (text === current.savedText) continue;
                    const revision = current.revision;
                    if (revision === undefined || !Number.isSafeInteger(revision) || revision < 0) {
                        update(key, { error: "missingRevision" });
                        return;
                    }
                    const saved = await api.updateScriptText(projectId, text, revision);
                    if (!isCurrent()) return;
                    // Full raw Script ACKs confirm own-project state. Reuse the
                    // gate so the revision and retained shared-source proof agree.
                    useProjectStore.getState().applyFrameCommandSnapshot(projectId, saved);
                    update(key, { savedText: saved.original_text, revision: saved.revision });
                }
            } catch (error) {
                // A failed commit never retries the queued draft or borrows a
                // newer revision. Keep all user input available for inspection.
                if (isCurrent()) update(key, { error: persistenceFailure(error) });
            } finally {
                if (ownsDraft()) {
                    pendingBlurs.delete(key);
                    saveRequests.delete(key);
                    update(key, { saving: false, ...(!isCurrentRuntimeEnvironment(environment.key, environment.generation) ? { error: 'conflict' as const } : {}) });
                }
            }
        },
    };
});
