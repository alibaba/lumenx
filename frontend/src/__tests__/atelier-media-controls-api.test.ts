// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { atelierMediaApi, parseAtelierMediaDraft, parseAtelierVoices, type AtelierMediaDraftInput } from "@/lib/atelierMediaApi";
import completed from "./fixtures/atelier-media-generation-completed.json";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";

const draft = { schema_version: 3, media_kind: "image", model_id: "wan/wan2.7-image-pro#image", mode: "i2i", params: { seed: 0, watermark: false }, media: [], batch_size: 2 } as const;
const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
const ack = () => ({ project: structuredClone(completed.project), node_ids: ["image"] });

beforeEach(() => {
    vi.restoreAllMocks(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
});

describe("media controls", () => {
    it("reads incomplete image drafts and preserves zero/false", () => {
        expect(parseAtelierMediaDraft(draft)).toEqual(draft);
        expect(parseAtelierMediaDraft({ schema_version: 3, media_kind: "audio", voice: "longanyang", params: { volume: 0 } }).params).toEqual({ volume: 0 });
    });
    it.each([
        { ...draft, batch_size: 7 }, { ...draft, params: { seed: Number.NaN } },
        { ...draft, params: { quality: "high" } },
        { ...draft, credential_ref: "forbidden" }, { ...draft, media: [{ role: "reference_image", ordinal: 1, node_id: "ref", media_url: "uploads/ref.png" }] },
        { schema_version: 3, media_kind: "audio", voice: "longanyang", params: { volume: false } },
    ])("rejects malformed draft %j", value => expect(() => parseAtelierMediaDraft(value)).toThrow());
    it("reads voice-specific controls and rejects duplicates/unknown controls", () => {
        const voice = { id: "Cherry", name: "Cherry", family: "qwen3", supported_parameters: ["instructions"] };
        expect(parseAtelierVoices([voice])).toEqual([voice]);
        expect(() => parseAtelierVoices([voice, voice])).toThrow();
        expect(() => parseAtelierVoices([{ ...voice, supported_parameters: ["api_key"] }])).toThrow();
    });
    it("saves a blank media prompt and verifies the returned draft", async () => {
        const saved = ack();
        saved.project.revision = 2;
        const node = saved.project.nodes.find(n => n.id === "image")!;
        node.prompt = "";
        Object.assign(node.data, { generation_draft: draft });
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(saved));
        const request = {
            media_kind: draft.media_kind,
            model_id: draft.model_id,
            mode: draft.mode,
            batch_size: draft.batch_size,
            media: [],
            params: { ...draft.params },
            expected_revision: 1,
            prompt: "",
        };
        await atelierMediaApi.saveDraft("media-project", "image", request);
        expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual(request);
        node.prompt = "changed by server";
        fetch.mockResolvedValue(response(saved));
        await expect(atelierMediaApi.saveDraft("media-project", "image", request)).rejects.toThrow();
    });
    it("accepts only explicit matching completed selection", async () => {
        const saved = ack(); saved.project.revision = 2;
        const node = saved.project.nodes.find(n => n.id === "image")!;
        const candidate = completed.candidate;
        Object.assign(node.data, { selected_candidate_id: candidate.id });
        (node as { media_urls: string[] }).media_urls = [candidate.media_url as string];
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(saved));
        await atelierMediaApi.selectCandidate("media-project", "image", candidate.id, { expected_revision: 1 });
        expect(String(fetch.mock.calls[0][0])).toContain(`/media_candidates/${candidate.id}/select`);
        (node as { media_urls: string[] }).media_urls = [];
        fetch.mockResolvedValue(response(saved));
        await expect(atelierMediaApi.selectCandidate("media-project", "image", candidate.id, { expected_revision: 1 })).rejects.toThrow();
    });
    it.each([false, true])("verifies visibility %s without selecting the candidate", async visible => {
        const saved = ack(); saved.project.revision = 2;
        const node = saved.project.nodes.find(n => n.id === "image")!;
        const candidate = completed.candidate;
        const original = (node.data as { candidates: { id: string }[] }).candidates;
        Object.assign(node.data, {
            candidates: visible ? original : original.filter(item => item.id !== candidate.id),
            deleted_candidates: visible ? [] : [candidate],
        });
        const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(saved));
        await atelierMediaApi.setCandidateVisibility("media-project", "image", candidate.id, { expected_revision: 1, visible });
        expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({ expected_revision: 1, visible });
        Object.assign(node.data, { selected_candidate_id: candidate.id });
        (node as { media_urls: string[] }).media_urls = [candidate.media_url as string];
        fetch.mockResolvedValue(response(saved));
        await expect(atelierMediaApi.setCandidateVisibility("media-project", "image", candidate.id, { expected_revision: 1, visible })).rejects.toThrow();
    });
    it("rejects an unexpected wire schema before sending draft data", async () => {
        const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("unexpected transport"));
        const input = { ...draft, expected_revision: 1, prompt: "draft" } as unknown as AtelierMediaDraftInput;
        await expect(atelierMediaApi.saveDraft("media-project", "image", input)).rejects.toThrow();
        expect(fetch).not.toHaveBeenCalled();
    });
});
