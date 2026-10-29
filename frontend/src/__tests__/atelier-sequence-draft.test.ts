import { describe, expect, it } from "vitest";
import { clearSequenceDraft, readSequenceDraft, saveSequenceDraft } from "@/lib/atelierSequenceDraft";
import type { AtelierSequenceEntry } from "@/lib/atelierTypes";

const owner = { apiBase: "http://localhost:17177", storageId: "storage-a", projectId: "p1" };
const entry: AtelierSequenceEntry = { id: "clip", parentId: "video", candidateId: null, gain: 1, transition: { kind: "cut", duration: 0 } };
const storage = (): Storage => {
    const values = new Map<string, string>();
    return { get length() { return values.size; }, key: index => Array.from(values.keys())[index] ?? null,
        getItem: key => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); },
        removeItem: key => { values.delete(key); }, clear: () => values.clear() };
};

describe("sequence draft durability", () => {
    it("keeps exact empty and repeated entries across reload and isolates each owner", () => {
        const disk = storage();
        const draft = saveSequenceDraft(owner, 3, [entry, { ...entry, id: "clip2" }], disk);
        expect(readSequenceDraft(owner, disk)).toEqual(draft);
        for (const other of [{ ...owner, storageId: "storage-b" }, { ...owner, apiBase: "http://remote" }, { ...owner, projectId: "p2" }]) {
            expect(readSequenceDraft(other, disk)).toBeNull();
        }
        expect(saveSequenceDraft(owner, 3, [], disk).entries).toEqual([]);
        expect(readSequenceDraft(owner, disk)?.entries).toEqual([]);
    });

    it("snapshots mutable input and never clears a newer edit with an older save ACK", () => {
        const disk = storage();
        const input = [{ ...entry, transition: { kind: "cut" as const, duration: 0 } }];
        const older = saveSequenceDraft(owner, 3, input, disk);
        input[0].transition.duration = 9;
        expect(readSequenceDraft(owner, disk)?.entries[0].transition.duration).toBe(0);
        const newer = saveSequenceDraft(owner, 3, [{ ...entry, trimEnd: 0.4 }], disk);
        expect(clearSequenceDraft(owner, older, disk)).toBe(false);
        expect(readSequenceDraft(owner, disk)).toEqual(newer);
        expect(clearSequenceDraft(owner, newer, disk)).toBe(true);
        expect(readSequenceDraft(owner, disk)).toBeNull();
    });

    it("fails closed on malformed records, unavailable storage, and invalid revision", () => {
        const disk = storage();
        saveSequenceDraft(owner, 3, [entry], disk);
        disk.setItem(disk.key(0)!, "{broken");
        expect(() => readSequenceDraft(owner, disk)).toThrow(/draft/);
        expect(() => saveSequenceDraft(owner, -1, [entry], storage())).toThrow(/draft/);
        const broken = { ...storage(), setItem: () => { throw new Error("QuotaExceeded"); } };
        expect(() => saveSequenceDraft(owner, 0, [entry], broken)).toThrow(/draft/);
    });
});
