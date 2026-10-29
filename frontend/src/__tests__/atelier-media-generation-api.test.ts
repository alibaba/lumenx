// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { parseAtelierProject } from "@/lib/atelierApi";
import { atelierGenerationApi, assertAtelierGenerationAdmissionMatchesRequest, parseAtelierGenerationAdmissionAck, parseAtelierGenerationAttemptAck } from "@/lib/atelierGenerationApi";
import { beginAtelierGenerationRequest, listAtelierGenerationRequests, resolveAtelierGenerationRequest } from "@/lib/atelierGenerationRequest";
import { mergeAtelierGenerationState } from "@/lib/atelierGenerationMerge";
import { activateRuntimeEnvironment, clearRuntimeEnvironment, type RuntimeEnvironment } from "@/lib/runtimeEnvironment";

import imageAck from "./fixtures/atelier-media-generation-image.json";
import audioAck from "./fixtures/atelier-media-generation-audio.json";
import completedAck from "./fixtures/atelier-media-generation-completed.json";
import importedMedia from "./fixtures/atelier-media-generation-imported.json";
import unknownAck from "./fixtures/atelier-media-generation-unknown.json";
import replayedImageAck from "./fixtures/atelier-media-generation-image-replayed.json";
import requests from "./fixtures/atelier-media-generation-requests.json";

const env: RuntimeEnvironment = {
    apiUrl: "http://atelier.local/api",
    storageId: "media-storage",
    key: JSON.stringify(["http://atelier.local/api", "media-storage"]),
    generation: 1,
};

const owner = { environment: env, projectId: "media-project", nodeId: "image" };
const mutableFixture = <T,>(value: T): T => structuredClone(value);

const imageRequest = {
    entrypoint: "atelier.provider_image.v1" as const,
    ...requests.image,
    mode: requests.image.mode as "t2i",
    media: [],
};
const audioRequest = {
    entrypoint: "atelier.provider_audio.v1" as const,
    ...requests.audio,
};

beforeEach(() => {
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
    localStorage.clear();
});

describe("Atelier media generation public wire", () => {
    it.each([
        { speech_rate: 0.49 }, { speech_rate: 2.01 }, { pitch_rate: 0.49 },
        { pitch_rate: 2.01 }, { volume: -1 }, { volume: 101 },
    ])("rejects out-of-range speech params before journaling or transport: %j", async params => {
        expect(() => beginAtelierGenerationRequest({ ...owner, nodeId: "audio", expectedRevision: 1, entrypoint: "atelier.provider_audio.v1",
            voice: audioRequest.voice, text: audioRequest.text, params, storage: localStorage,
            idFactory: () => audioRequest.client_request_id })).toThrow();
        expect(listAtelierGenerationRequests({ ...owner, nodeId: "audio", storage: localStorage })).toEqual([]);
        const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("unexpected transport"));
        await expect(atelierGenerationApi.createAudioCandidate("media-project", "audio", { ...audioRequest, params })).rejects.toThrow();
        expect(fetch).not.toHaveBeenCalled();
    });
    it("parses real backend image batch and audio singleton member ACKs", () => {
        const parsedImage = parseAtelierGenerationAdmissionAck(imageAck, "media-project", { request: imageRequest, nodeId: "image", allowBatch: true, creating: true });
        expect(parsedImage.receipt.entrypoint).toBe("atelier.provider_image.v1");
        expect(parsedImage.members).toHaveLength(2);
        expect(parsedImage.members[0].attempt.provider_execution.schema_version).toBe(2);
        assertAtelierGenerationAdmissionMatchesRequest(parsedImage, imageRequest, { projectId: "media-project", nodeId: "image" });

        const parsedAudio = parseAtelierGenerationAdmissionAck(audioAck, "media-project", { request: audioRequest, nodeId: "audio", allowBatch: true, creating: true });
        expect(parsedAudio.receipt.schema_version).toBe(1);
        expect(parsedAudio.members).toHaveLength(1);
        expect("media_kind" in parsedAudio.members[0].attempt ? parsedAudio.members[0].attempt.media_kind : null).toBe("audio");
        assertAtelierGenerationAdmissionMatchesRequest(parsedAudio, audioRequest, { projectId: "media-project", nodeId: "audio" });
    });

    it("parses completed and unknown attempt readbacks without video aliases", () => {
        const completed = parseAtelierGenerationAttemptAck(completedAck, "media-project");
        expect(completed.candidate?.media_kind).toBe("image");
        expect(completed.candidate && "video_url" in completed.candidate).toBe(false);
        expect(completed.candidate?.media_url).toMatch(/^assets\/atelier\/.+\.png$/);
        expect(completed.attempt.provider_execution.artifact?.contract_version).toBe("provider-sync-artifact-v1");

        const unknown = parseAtelierGenerationAttemptAck(unknownAck, "media-project");
        expect(unknown.attempt.status).toBe("needs_confirmation");
        expect(unknown.candidate?.media_url).toBeNull();
    });

    it("rejects media graph mixing between receipt, attempt, candidate, and proof", () => {
        const mixed = structuredClone(completedAck) as typeof completedAck;
        mixed.candidate.media_kind = "audio";
        expect(() => parseAtelierGenerationAttemptAck(mixed, "media-project")).toThrow(/generation/i);

        const badProof = structuredClone(completedAck) as typeof completedAck;
        badProof.attempt.provider_execution.artifact.relative_path = "video/not-media.mp4";
        expect(() => parseAtelierGenerationAttemptAck(badProof, "media-project")).toThrow(/generation/i);
    });

    it("keeps replayed image request identity tied to the saved original request", () => {
        const parsed = parseAtelierGenerationAdmissionAck(replayedImageAck, "media-project", { request: imageRequest, nodeId: "image", allowBatch: true });
        expect(parsed.replayed).toBe(true);
        expect(parsed.new_candidate_ids).toEqual([]);
    });

    it("stores image and audio pending requests with explicit entrypoint discriminators", () => {
        const imageStored = beginAtelierGenerationRequest({ ...owner, expectedRevision: 0, entrypoint: "atelier.provider_image.v1", modelId: imageRequest.model_id,
            mode: imageRequest.mode, prompt: imageRequest.prompt, media: imageRequest.media, params: imageRequest.params, batchSize: imageRequest.batch_size,
            storage: localStorage, idFactory: () => imageRequest.client_request_id });
        const audioStored = beginAtelierGenerationRequest({ ...owner, nodeId: "audio", expectedRevision: 1, entrypoint: "atelier.provider_audio.v1",
            voice: audioRequest.voice, text: audioRequest.text, params: audioRequest.params, storage: localStorage,
            idFactory: () => audioRequest.client_request_id });

        expect("entrypoint" in imageStored.request.request ? imageStored.request.request.entrypoint : null).toBe("atelier.provider_image.v1");
        expect("entrypoint" in audioStored.request.request ? audioStored.request.request.entrypoint : null).toBe("atelier.provider_audio.v1");
        expect(listAtelierGenerationRequests({ ...owner, storage: localStorage }).map(item => item.clientRequestId)).toEqual([imageRequest.client_request_id]);
        expect(resolveAtelierGenerationRequest({ ...owner, clientRequestId: imageRequest.client_request_id, request: imageStored.request.request, storage: localStorage })).toBe(true);
    });


    it("rejects unsafe media paths, stale errors, and invalid sync proof states", () => {
        const badMediaPath = mutableFixture(completedAck);
        badMediaPath.candidate.media_url = "video/not-media.mp4";
        expect(() => parseAtelierGenerationAttemptAck(badMediaPath, "media-project")).toThrow(/generation/i);

        const staleCandidateError = mutableFixture(completedAck);
        Object.assign(staleCandidateError.candidate, { error_code: "provider_sync_execution_unknown" });
        expect(() => parseAtelierGenerationAttemptAck(staleCandidateError, "media-project")).toThrow(/generation/i);

        const staleAttemptError = mutableFixture(completedAck);
        Object.assign(staleAttemptError.attempt, { error_code: "provider_sync_execution_unknown" });
        Object.assign(staleAttemptError.project.generation_attempts[0], { error_code: "provider_sync_execution_unknown" });
        expect(() => parseAtelierGenerationAttemptAck(staleAttemptError, "media-project")).toThrow(/generation/i);

        const acceptedWithProof = mutableFixture(imageAck);
        Object.assign(acceptedWithProof.members[0].attempt.provider_execution, {
            prepared_artifact: structuredClone(completedAck.attempt.provider_execution.prepared_artifact),
            artifact: structuredClone(completedAck.attempt.provider_execution.artifact),
        });
        Object.assign(acceptedWithProof.project.generation_attempts[0].provider_execution, {
            prepared_artifact: structuredClone(completedAck.attempt.provider_execution.prepared_artifact),
            artifact: structuredClone(completedAck.attempt.provider_execution.artifact),
        });
        expect(() => parseAtelierGenerationAdmissionAck(acceptedWithProof, "media-project", { request: imageRequest, nodeId: "image", allowBatch: true })).toThrow(/generation/i);

        const completedWithoutProof = mutableFixture(completedAck);
        Object.assign(completedWithoutProof.attempt.provider_execution, { artifact: null });
        Object.assign(completedWithoutProof.project.generation_attempts[0].provider_execution, { artifact: null });
        expect(() => parseAtelierGenerationAttemptAck(completedWithoutProof, "media-project")).toThrow(/generation/i);
    });

    it("treats schema2 media output_ref as immutable merge identity", () => {
        const base = parseAtelierProject(imageAck.project);
        const completed = parseAtelierProject(completedAck.project);
        const changed = structuredClone(completed);
        const incoming = changed.generation_attempts[0];
        if (!("execution_contract" in incoming) || incoming.execution_contract !== "atelier-provider-media-v2") {
            throw new Error("expected media attempt fixture");
        }
        incoming.attempt_revision += 1;
        incoming.provider_execution.output_ref = "assets/atelier/changed-output.png";
        incoming.output_ref = "assets/atelier/changed-output.png";
        expect(() => mergeAtelierGenerationState(base, changed)).toThrow(/identity/i);
    });

    it("merges completed media candidates using media_url while preserving video_url for video", () => {
        const base = parseAtelierProject(imageAck.project);
        const completed = parseAtelierProject(completedAck.project);
        const merged = mergeAtelierGenerationState(base, completed);
        const node = merged.nodes.find(item => item.id === "image");
        const candidates = node?.data.candidates instanceof Array ? node.data.candidates : [];
        const candidate = candidates.find(item => item && typeof item === "object" && "media_url" in item && item.media_url !== null) ?? null;
        expect(candidate && typeof candidate === "object" && "media_url" in candidate ? candidate.media_url : null).toMatch(/^assets\/atelier\/.+\.png$/);
        expect(candidate && typeof candidate === "object" && "video_url" in candidate).toBe(false);
    });

    it("accepts only inert current-project imported media candidates", () => {
        const project = parseAtelierProject(importedMedia.prepared_project);
        const node = project.nodes[0];
        const candidate = Array.isArray(node.data.candidates) ? node.data.candidates[0] : null;
        expect(project.generation_attempts).toEqual([]);
        expect(project.generation_request_receipts).toEqual([]);
        expect(candidate).toMatchObject({
            execution_contract: "atelier-provider-media-v2",
            authority: "imported",
            attempt_id: null,
            execution_fingerprint: null,
            media_kind: "image",
            media_url: "assets/imported/fixture-project/atelier/out.png",
        });

        const withoutProvenance = mutableFixture(importedMedia.prepared_project);
        delete (withoutProvenance as { import_provenance?: unknown }).import_provenance;
        expect(() => parseAtelierProject(withoutProvenance)).toThrow(/generation/i);

        const foreignPath = mutableFixture(importedMedia.prepared_project);
        foreignPath.nodes[0].data.candidates[0].media_url = "assets/imported/other-project/atelier/out.png";
        expect(() => parseAtelierProject(foreignPath)).toThrow(/generation/i);

        const wrongNodeKind = mutableFixture(importedMedia.prepared_project);
        wrongNodeKind.nodes[0].type = "audio";
        expect(() => parseAtelierProject(wrongNodeKind)).toThrow(/generation/i);

        const authorityBearing = mutableFixture(importedMedia.prepared_project);
        (authorityBearing.nodes[0].data.candidates[0] as { attempt_id: string | null }).attempt_id = "attempt-1";
        expect(() => parseAtelierProject(authorityBearing)).toThrow(/generation/i);
    });
});
