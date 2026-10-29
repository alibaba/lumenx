import { parseAtelierSequenceEntry } from "@/lib/atelierApi";
import type { AtelierSequenceEntry } from "@/lib/atelierTypes";

export interface SequenceDraftOwner {
    apiBase: string;
    storageId: string;
    projectId: string;
}

export interface SequenceDraft extends SequenceDraftOwner {
    schema: 1;
    baseRevision: number;
    entries: AtelierSequenceEntry[];
}

const failure = () => new Error("Sequence draft storage is unavailable or contains an invalid draft");
const keyOf = (owner: SequenceDraftOwner) => {
    if (![owner.apiBase, owner.storageId, owner.projectId].every(value => typeof value === "string" && value.length > 0)) throw failure();
    return `lumenx.atelier.sequence-draft.v1:${JSON.stringify([owner.apiBase, owner.storageId, owner.projectId])}`;
};
const storageOf = (storage?: Storage): Storage => {
    try { return storage ?? window.localStorage; } catch { throw failure(); }
};

export function readSequenceDraft(owner: SequenceDraftOwner, storage?: Storage): SequenceDraft | null {
    try {
        const raw = storageOf(storage).getItem(keyOf(owner));
        if (raw === null) return null;
        const value = JSON.parse(raw);
        if (!value || value.schema !== 1 || value.apiBase !== owner.apiBase || value.storageId !== owner.storageId
            || value.projectId !== owner.projectId || !Number.isSafeInteger(value.baseRevision) || value.baseRevision < 0
            || !Array.isArray(value.entries)) throw failure();
        return { schema: 1, ...owner, baseRevision: value.baseRevision, entries: value.entries.map(parseAtelierSequenceEntry) };
    } catch { throw failure(); }
}

export function saveSequenceDraft(owner: SequenceDraftOwner, baseRevision: number, entries: AtelierSequenceEntry[], storage?: Storage): SequenceDraft {
    try {
        if (!Number.isSafeInteger(baseRevision) || baseRevision < 0 || !Array.isArray(entries)) throw failure();
        const draft: SequenceDraft = { schema: 1, ...owner, baseRevision, entries: entries.map(parseAtelierSequenceEntry) };
        const raw = JSON.stringify(draft);
        storageOf(storage).setItem(keyOf(owner), raw);
        return JSON.parse(raw) as SequenceDraft;
    } catch { throw failure(); }
}

/** A delayed save receipt must not erase edits made while the save was in flight. */
export function clearSequenceDraft(owner: SequenceDraftOwner, saved: SequenceDraft, storage?: Storage): boolean {
    const disk = storageOf(storage);
    const current = readSequenceDraft(owner, disk);
    if (JSON.stringify(current) !== JSON.stringify(saved)) return false;
    try { disk.removeItem(keyOf(owner)); } catch { throw failure(); }
    return true;
}
