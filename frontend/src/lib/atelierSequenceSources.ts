import type { AtelierNode, AtelierSequenceEntry, JsonRecord, JsonValue } from "@/lib/atelierTypes";

type UnavailableSource = "missing_node" | "wrong_media_kind" | "missing_candidate" | "candidate_not_ready" | "missing_media";
const record = (value: JsonValue): value is JsonRecord => value !== null && typeof value === "object" && !Array.isArray(value);
export const createSequenceClipId = () => `clip-${globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}`;

export interface SequenceCandidateOption {
    candidateId: string;
    ordinal: number;
    url: string;
}

/** Show the exact sequence reference. Core preflight remains the file/trim authority. */
export function getSequenceSource(nodes: AtelierNode[], entry: AtelierSequenceEntry): {
    node: AtelierNode | null; url: string | null; unavailable: UnavailableSource | null;
} {
    const node = nodes.find(item => item.id === entry.parentId) ?? null;
    const missing = (unavailable: UnavailableSource) => ({ node, url: null, unavailable });
    if (!node) return missing("missing_node");
    if (node.type !== "video" && node.type !== "draft") return missing("wrong_media_kind");
    let url: JsonValue | undefined;
    if (entry.candidateId !== null) {
        const candidates = Array.isArray(node.data.candidates) ? node.data.candidates.filter(record) : [];
        const candidate = candidates.find(item => item.id === entry.candidateId);
        if (!candidate) return missing("missing_candidate");
        if (candidate.status !== "completed" || (candidate.media_kind !== undefined && candidate.media_kind !== "video")) return missing("candidate_not_ready");
        url = candidate.video_url || candidate.audio_url || candidate.media_url;
    } else {
        if (node.type !== "video") return missing("wrong_media_kind");
        url = node.media_urls[0];
    }
    return typeof url === "string" && url.length > 0 ? { node, url, unavailable: null } : missing("missing_media");
}

export function sequenceEntryForVideo(node: AtelierNode): AtelierSequenceEntry | null {
    const candidates = node.data.candidates;
    const selected = node.data.selected_candidate_id;
    if (Array.isArray(candidates) && candidates.length > 0 && (typeof selected !== "string" || !selected)) return null;
    const entry: AtelierSequenceEntry = {
        id: createSequenceClipId(),
        parentId: node.id,
        candidateId: typeof selected === "string" && selected ? selected : null,
        trimStart: null, trimEnd: null, gain: 1, transition: { kind: "cut", duration: 0 },
    };
    return getSequenceSource([node], entry).unavailable ? null : entry;
}

export function candidateSequenceOptions(node: AtelierNode): SequenceCandidateOption[] {
    if (node.type !== "video" && node.type !== "draft") return [];
    const candidates = Array.isArray(node.data.candidates) ? node.data.candidates.filter(record) : [];
    return candidates.flatMap((candidate, index) => {
        const candidateId = candidate.id;
        const url = candidate.video_url || (candidate.media_kind === undefined || candidate.media_kind === "video" ? candidate.media_url : undefined);
        if (typeof candidateId !== "string" || !candidateId) return [];
        if (candidate.status !== "completed" || (candidate.media_kind !== undefined && candidate.media_kind !== "video")) return [];
        if (typeof url !== "string" || !url) return [];
        return [{ candidateId, ordinal: index + 1, url }];
    });
}

export function sequenceEntryForCandidate(node: AtelierNode, candidateId: string): AtelierSequenceEntry | null {
    if (!candidateSequenceOptions(node).some(option => option.candidateId === candidateId)) return null;
    const entry: AtelierSequenceEntry = {
        id: createSequenceClipId(),
        parentId: node.id,
        candidateId,
        trimStart: null, trimEnd: null, gain: 1, transition: { kind: "cut", duration: 0 },
    };
    return getSequenceSource([node], entry).unavailable ? null : entry;
}
