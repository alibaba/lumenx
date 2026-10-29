import { describe, expect, it } from "vitest";
import { candidateSequenceOptions, getSequenceSource, sequenceEntryForCandidate, sequenceEntryForVideo } from "@/lib/atelierSequenceSources";
import type { AtelierNode, AtelierSequenceEntry } from "@/lib/atelierTypes";

const node = (changes: Partial<AtelierNode> = {}): AtelierNode => ({
    id: "video", project_id: "p", type: "video", title: "Clip", prompt: "", status: "completed",
    x: 0, y: 0, width: 260, height: 146, source_project_id: null, frame_id: null, asset_id: null,
    video_task_id: null, media_urls: ["uploads/clip.mp4"], data: {}, created_by: "user", created_at: 0, updated_at: 0, ...changes,
});
const entry: AtelierSequenceEntry = { parentId: "video", candidateId: null, id: "clip", gain: 1, transition: { kind: "cut", duration: 0 } };

describe("sequence source presentation", () => {
    it("keeps the exact candidate instead of falling back to another completed take", () => {
        const source = node({ data: { candidates: [{ id: "new", status: "completed", video_url: "video/new.mp4" }] } });
        expect(getSequenceSource([source], { ...entry, candidateId: "old" })).toMatchObject({ unavailable: "missing_candidate", url: null });
        expect(getSequenceSource([source], { ...entry, candidateId: "new" })).toMatchObject({ unavailable: null, url: "video/new.mp4" });
    });

    it("does not use stale node media for a failed candidate or a deleted node", () => {
        const source = node({ data: { candidates: [{ id: "bad", status: "failed", video_url: "video/old.mp4" }] } });
        expect(getSequenceSource([source], { ...entry, candidateId: "bad" }).url).toBeNull();
        expect(getSequenceSource([], entry).unavailable).toBe("missing_node");
        expect(getSequenceSource([node({ type: "image" })], entry).unavailable).toBe("wrong_media_kind");
    });

    it("allows uploaded videos and gives repeated insertions independent clip ids", () => {
        const source = node();
        expect(getSequenceSource([source], entry).url).toBe("uploads/clip.mp4");
        const first = sequenceEntryForVideo(source);
        const second = sequenceEntryForVideo(source);
        expect(first?.candidateId).toBeNull();
        expect(first?.id).not.toBe(second?.id);
    });

    it("inserts only a selected ready candidate when candidates exist", () => {
        const source = node({ data: { selected_candidate_id: "chosen", candidates: [{ id: "chosen", status: "completed", video_url: "video/chosen.mp4" }] } });
        expect(sequenceEntryForVideo(source)?.candidateId).toBe("chosen");
        expect(sequenceEntryForVideo(node({ data: { candidates: [{ id: "other", status: "completed", video_url: "video/other.mp4" }] } }))).toBeNull();
    });

    it("builds a sequence entry for an unselected completed video candidate without changing the selected take", () => {
        const source = node({ data: { selected_candidate_id: "take-a", candidates: [
            { id: "take-a", status: "completed", media_kind: "video", video_url: "video/a.mp4" },
            { id: "take-b", status: "completed", media_kind: "video", video_url: "video/b.mp4" },
        ] } });

        const entryForB = sequenceEntryForCandidate(source, "take-b");

        expect(sequenceEntryForVideo(source)?.candidateId).toBe("take-a");
        expect(entryForB?.candidateId).toBe("take-b");
        expect(getSequenceSource([source], entryForB!)).toMatchObject({ unavailable: null, url: "video/b.mp4" });
    });

    it("offers only live completed video candidates for sequence insertion", () => {
        const source = node({ data: {
            selected_candidate_id: "selected",
            candidates: [
                { id: "selected", status: "completed", media_kind: "video", video_url: "video/selected.mp4" },
                { id: "implicit-video", status: "completed", video_url: "video/implicit.mp4" },
                { id: "legacy-media-url", status: "completed", media_url: "video/legacy.mp4" },
                { id: "failed", status: "failed", media_kind: "video", video_url: "video/failed.mp4" },
                { id: "image", status: "completed", media_kind: "image", media_url: "image.png" },
                { id: "audio", status: "completed", media_kind: "audio", media_url: "audio/tone.mp3" },
                { id: "missing-url", status: "completed", media_kind: "video" },
            ],
            deleted_candidates: [
                { id: "hidden", status: "completed", media_kind: "video", video_url: "video/hidden.mp4" },
            ],
        } });

        expect(candidateSequenceOptions(source).map(option => option.candidateId)).toEqual(["selected", "implicit-video", "legacy-media-url"]);
        expect(sequenceEntryForCandidate(source, "failed")).toBeNull();
        expect(sequenceEntryForCandidate(source, "image")).toBeNull();
        expect(sequenceEntryForCandidate(source, "audio")).toBeNull();
        expect(sequenceEntryForCandidate(source, "hidden")).toBeNull();
    });
});
