import { AtelierProjectApiError, isAtelierRecord, requestAtelierJson, requireAtelierProjectIdInput, requireAtelierRevisionInput } from "@/lib/atelierApi";
import { parseAtelierNodeCommandAck } from "@/lib/atelierNodeApi";
import type { AtelierImageReferenceSelection, AtelierNode, AtelierNodeCommandAck, JsonRecord } from "@/lib/atelierTypes";

type MediaParams = Record<string, string | number | boolean>;
export type AtelierMediaDraft =
    | { schema_version: 3; media_kind: "image"; model_id: string; mode: "t2i" | "i2i"; params: MediaParams; media: AtelierImageReferenceSelection[]; batch_size: number }
    | { schema_version: 3; media_kind: "audio"; voice: string; params: MediaParams };
export type AtelierMediaDraftInput = AtelierMediaDraft extends infer D ? D extends AtelierMediaDraft ? Omit<D, "schema_version"> & { expected_revision: number; prompt: string } : never : never;
export type AtelierBuiltinVoice = { id: string; name: string; family: "cosyvoice" | "qwen3"; supported_parameters: string[] };

const fail = (): never => { throw new AtelierProjectApiError("Invalid Atelier media control response", "atelier_media_control_mismatch"); };
const text = (v: unknown): string => typeof v === "string" && v.trim() ? v : fail();
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
const canonical = (value: unknown): string => JSON.stringify(value, (_key, v) => isAtelierRecord(v) ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);
const AUDIO_FIELDS = ["instructions", "speech_rate", "pitch_rate", "volume"];
const IMAGE_FIELDS = ["size", "seed", "negative_prompt", "prompt_extend", "watermark"];

function params(value: unknown, kind: "image" | "audio"): MediaParams {
    if (!isAtelierRecord(value)) return fail();
    const allowed = kind === "image" ? IMAGE_FIELDS : AUDIO_FIELDS;
    for (const [key, item] of Object.entries(value)) {
        if (!allowed.includes(key) || !["string", "number", "boolean"].includes(typeof item)) return fail();
        if (typeof item === "number" && !Number.isFinite(item)) return fail();
        if (kind === "audio") {
            if (key === "instructions") { if (typeof item !== "string") return fail(); }
            else if (typeof item !== "number" || item < (key === "volume" ? 0 : 0.5) || item > (key === "volume" ? 100 : 2)) return fail();
        } else if (key === "seed") { if (!Number.isSafeInteger(item)) return fail(); }
        else if (key === "prompt_extend" || key === "watermark") { if (typeof item !== "boolean") return fail(); }
        else if (typeof item !== "string") return fail();
    }
    return { ...value } as MediaParams;
}

export function parseAtelierMediaDraft(raw: unknown): AtelierMediaDraft {
    if (!isAtelierRecord(raw) || raw.schema_version !== 3) return fail();
    if (raw.media_kind === "audio") {
        if (!exact(raw, ["schema_version", "media_kind", "voice", "params"])) return fail();
        return { schema_version: 3, media_kind: "audio", voice: text(raw.voice), params: params(raw.params, "audio") };
    }
    if (raw.media_kind !== "image" || !exact(raw, ["schema_version", "media_kind", "model_id", "mode", "params", "media", "batch_size"])
        || (raw.mode !== "t2i" && raw.mode !== "i2i") || !Array.isArray(raw.media) || raw.media.length > 10
        || !Number.isSafeInteger(raw.batch_size) || (raw.batch_size as number) < 1 || (raw.batch_size as number) > 6) return fail();
    const media = raw.media.map((item, index) => {
        if (!isAtelierRecord(item) || !exact(item, ["role", "ordinal", "node_id", "media_url"]) || item.role !== "reference_image" || item.ordinal !== index) return fail();
        return { role: "reference_image" as const, ordinal: index, node_id: text(item.node_id), media_url: text(item.media_url) };
    });
    if (raw.mode === "t2i" && media.length) return fail();
    return { schema_version: 3, media_kind: "image", model_id: text(raw.model_id), mode: raw.mode, params: params(raw.params, "image"), media, batch_size: raw.batch_size as number };
}

export function parseAtelierVoices(raw: unknown): AtelierBuiltinVoice[] {
    if (!Array.isArray(raw)) return fail();
    const seen = new Set<string>();
    return raw.map(item => {
        if (!isAtelierRecord(item) || !exact(item, ["id", "name", "family", "supported_parameters"])
            || (item.family !== "cosyvoice" && item.family !== "qwen3") || !Array.isArray(item.supported_parameters)) return fail();
        const id = text(item.id);
        if (seen.has(id) || new Set(item.supported_parameters).size !== item.supported_parameters.length
            || item.supported_parameters.some(v => typeof v !== "string" || !AUDIO_FIELDS.includes(v))) return fail();
        seen.add(id);
        return { id, name: text(item.name), family: item.family, supported_parameters: [...item.supported_parameters] as string[] };
    });
}

function nodePath(projectId: string, nodeId: string): string {
    return `/atelier/projects/${encodeURIComponent(requireAtelierProjectIdInput(projectId))}/nodes/${encodeURIComponent(text(nodeId))}`;
}

function candidateFrom(node: AtelierNode, candidateId: string, key: "candidates" | "deleted_candidates"): JsonRecord | null {
    const entries = node.data[key];
    if (entries === undefined) return null;
    if (!Array.isArray(entries)) return fail();
    const matches = entries.filter(v => isAtelierRecord(v) && v.id === candidateId);
    if (matches.length > 1) return fail();
    const result = matches[0];
    if (!result) return null;
    if (!isAtelierRecord(result) || result.execution_contract !== "atelier-provider-media-v2" || result.media_kind !== node.type) return fail();
    return result as JsonRecord;
}

async function command(projectId: string, nodeId: string, suffix: string, payload: Record<string, unknown>, method = "POST"): Promise<AtelierNodeCommandAck> {
    const expectedRevision = requireAtelierRevisionInput(payload.expected_revision as number);
    return parseAtelierNodeCommandAck(await requestAtelierJson(`${nodePath(projectId, nodeId)}/${suffix}`, { method, body: JSON.stringify(payload) }),
        { projectId, expectedRevision, expectedIds: [nodeId] });
}

export const atelierMediaApi = {
    async listVoices(): Promise<AtelierBuiltinVoice[]> {
        return parseAtelierVoices(await requestAtelierJson("/atelier/voices", { method: "GET" }));
    },
    async saveDraft(projectId: string, nodeId: string, input: AtelierMediaDraftInput): Promise<AtelierNodeCommandAck> {
        const { expected_revision, prompt, ...fields } = input;
        if (typeof prompt !== "string" || Object.hasOwn(fields, "schema_version")) return fail();
        const draft = parseAtelierMediaDraft({ ...fields, schema_version: 3 });
        const ack = await command(projectId, nodeId, "generation_draft", { ...fields, expected_revision, prompt }, "PUT");
        const node = ack.project.nodes.find(v => v.id === nodeId)!;
        if (node.type !== draft.media_kind || node.prompt !== prompt || canonical(parseAtelierMediaDraft(node.data.generation_draft)) !== canonical(draft)) return fail();
        return ack;
    },
    async selectCandidate(projectId: string, nodeId: string, candidateId: string, input: { expected_revision: number }): Promise<AtelierNodeCommandAck> {
        if (!exact(input, ["expected_revision"])) return fail();
        const ack = await command(projectId, nodeId, `media_candidates/${encodeURIComponent(text(candidateId))}/select`, input);
        const node = ack.project.nodes.find(v => v.id === nodeId)!;
        const candidate = candidateFrom(node, candidateId, "candidates");
        if (!candidate || candidate.status !== "completed" || typeof candidate.media_url !== "string"
            || node.data.selected_candidate_id !== candidateId || canonical(node.media_urls) !== canonical([candidate.media_url])) return fail();
        return ack;
    },
    async setCandidateVisibility(projectId: string, nodeId: string, candidateId: string, input: { expected_revision: number; visible: boolean }): Promise<AtelierNodeCommandAck> {
        if (!exact(input, ["expected_revision", "visible"]) || typeof input.visible !== "boolean") return fail();
        const ack = await command(projectId, nodeId, `media_candidates/${encodeURIComponent(text(candidateId))}/visibility`, input);
        const node = ack.project.nodes.find(v => v.id === nodeId)!;
        const candidate = candidateFrom(node, candidateId, input.visible ? "candidates" : "deleted_candidates");
        if (!candidate || !["completed", "failed", "cancelled"].includes(candidate.status as string)
            || candidateFrom(node, candidateId, input.visible ? "deleted_candidates" : "candidates") || node.data.selected_candidate_id === candidateId) return fail();
        if (typeof candidate.media_url === "string" && node.media_urls.includes(candidate.media_url)) {
            const selected = typeof node.data.selected_candidate_id === "string" ? candidateFrom(node, node.data.selected_candidate_id, "candidates") : null;
            if (!selected || selected.status !== "completed" || selected.media_url !== candidate.media_url || node.media_urls.length !== 1) return fail();
        }
        return ack;
    },
};
