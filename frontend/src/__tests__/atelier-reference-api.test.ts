// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { atelierReferenceApi } from "@/lib/atelierReferenceApi";
import { parseAtelierProject } from "@/lib/atelierApi";
import type { AtelierNodeCommandAck, JsonRecord } from "@/lib/atelierTypes";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project.json";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
function ack({ attached = true, materialized = false, noop = false } = {}): AtelierNodeCommandAck {
    const project = parseAtelierProject(structuredClone(fixture));
    project.revision = noop ? 0 : 1;
    const original = project.nodes[0];
    const sourceId = materialized ? "materialized" : "source";
    const target = { ...original, id: "target", type: "video", data: attached ? {
        reference_node_ids: [sourceId], reference_image_urls: ["uploads/ref.png"],
    } : { reference_node_ids: [], reference_image_urls: [] } };
    const source = { ...original, id: sourceId, type: "image", media_urls: ["uploads/ref.png"], data: {
        parent_node_id: attached ? "target" : null,
        ...(materialized ? { reference_origin: { node_id: "source", candidate_id: "take" } } : {}),
    } };
    return { project: { ...project, nodes: [target, source, ...(materialized ? [{ ...source, id: "source", data: {} }] : [])] }, node_ids: noop ? ["target"] : ["target", sourceId] };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

function pendingAck({ withUrl = false, missingPending = false, wrongKind = false, historicalSource = false } = {}): AtelierNodeCommandAck {
    const project = parseAtelierProject(structuredClone(fixture));
    project.revision = 1;
    const original = project.nodes[0];
    const target = { ...original, id: "target", type: "video", media_urls: [], data: {
        reference_node_ids: ["source"],
        pending_reference_nodes: missingPending ? [] : [{ schema_version: 1, source_node_id: "source", source_kind: wrongKind ? "audio" : "image", status: "pending_media" }],
        ...(withUrl ? { reference_image_urls: ["uploads/fake.png"] } : {}),
    } };
    const sourceData: JsonRecord = historicalSource ? { deleted_candidates: [{ id: "old", status: "completed", media_url: "uploads/old.png" }] } : {};
    const source = { ...original, id: "source", type: "image", media_urls: [], data: sourceData };
    return { project: { ...project, nodes: [target, source] }, node_ids: ["target", "source"] };
}
beforeEach(() => {
    vi.restoreAllMocks(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
});

describe("Atelier reference command acknowledgements", () => {
    it("sends only typed fields and accepts both ordinary and materialized sources", async () => {
        const fetch = vi.fn().mockResolvedValueOnce(response(ack())).mockResolvedValueOnce(response(ack({ materialized: true })));
        vi.stubGlobal("fetch", fetch);
        const first = await atelierReferenceApi.attachReference(fixture.id, "target", { expectedRevision: 0, sourceNodeId: "source" });
        const second = await atelierReferenceApi.attachReference(fixture.id, "target", { expectedRevision: 0, sourceNodeId: "source", sourceCandidateId: "take" });
        expect(first.project.nodes[0].data.reference_node_ids).toEqual(["source"]);
        expect(second.node_ids).toEqual(["target", "materialized"]);
        expect(String(fetch.mock.calls[0][0])).toContain("/nodes/target/references/attach");
        expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ expected_revision: 0, source_node_id: "source" });
        expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ expected_revision: 0, source_node_id: "source", source_candidate_id: "take" });
    });


    it("attaches pending references as identity-only placeholders without materializing media URLs", async () => {
        const fetch = vi.fn().mockResolvedValue(response(pendingAck()));
        vi.stubGlobal("fetch", fetch);
        const result = await atelierReferenceApi.attachPendingReference(fixture.id, "target", { expectedRevision: 0, sourceNodeId: "source" });
        expect(String(fetch.mock.calls[0][0])).toContain("/nodes/target/references/attach_pending");
        expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ expected_revision: 0, source_node_id: "source" });
        expect(result.project.nodes[0].data.reference_node_ids).toEqual(["source"]);
        expect(result.project.nodes[0].data.pending_reference_nodes).toEqual([{ schema_version: 1, source_node_id: "source", source_kind: "image", status: "pending_media" }]);
        expect(result.project.nodes[0].data.reference_image_urls).toBeUndefined();
    });

    it.each(["url", "pending", "kind", "historical-source"])('rejects a pending attach ACK with mismatched %s proof', async mismatch => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(pendingAck({
            withUrl: mismatch === "url",
            missingPending: mismatch === "pending",
            wrongKind: mismatch === "kind",
            historicalSource: mismatch === "historical-source",
        }))));
        await expect(atelierReferenceApi.attachPendingReference(fixture.id, "target", { expectedRevision: 0, sourceNodeId: "source" })).rejects.toThrow();
    });

    it("accepts detach and no-op without reconstructing a partial project", async () => {
        const fetch = vi.fn().mockResolvedValueOnce(response(ack({ attached: false }))).mockResolvedValueOnce(response(ack({ attached: false, noop: true })));
        vi.stubGlobal("fetch", fetch);
        const result = await atelierReferenceApi.detachReference(fixture.id, "target", { expectedRevision: 0, referenceUrl: "uploads/ref.png" });
        expect(result.project.nodes[0].data.reference_node_ids).toEqual([]);
        const noop = await atelierReferenceApi.detachReference(fixture.id, "target", { expectedRevision: 0, referenceUrl: "uploads/ref.png", sourceNodeId: "source" });
        expect(noop.node_ids).toEqual(["target"]);
        expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ expected_revision: 0, reference_url: "uploads/ref.png", source_node_id: "source" });
    });

    it.each(["owner", "target", "source", "origin", "edge", "url"])("rejects a mismatched %s proof", async mismatch => {
        const value = ack({ materialized: mismatch === "origin" });
        if (mismatch === "owner") value.project.id = "other";
        if (mismatch === "target") value.node_ids.reverse();
        if (mismatch === "source") value.node_ids = ["target"];
        if (mismatch === "origin") value.project.nodes[1].data = { reference_origin: { node_id: "wrong", candidate_id: "take" } };
        if (mismatch === "edge") value.project.nodes[0].data = { reference_node_ids: [], reference_image_urls: ["uploads/ref.png"] };
        if (mismatch === "url") value.project.nodes[0].data = { reference_node_ids: ["source"], reference_image_urls: [] };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(value)));
        await expect(atelierReferenceApi.attachReference(fixture.id, "target", { expectedRevision: 0, sourceNodeId: "source", ...(mismatch === "origin" ? { sourceCandidateId: "take" } : {}) })).rejects.toThrow();
    });

    it("rejects a detach ACK that retains the supposedly removed relationship", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(ack())));
        await expect(atelierReferenceApi.detachReference(fixture.id, "target", { expectedRevision: 0, referenceUrl: "uploads/ref.png", sourceNodeId: "source" })).rejects.toThrow();
    });

    it("detaches a source with missing media by identity without fabricating a URL", async () => {
        const value = ack({ attached: false });
        value.project.nodes[1].media_urls = [];
        const fetch = vi.fn().mockResolvedValue(response(value));
        vi.stubGlobal("fetch", fetch);
        const result = await atelierReferenceApi.detachReference(fixture.id, "target", { expectedRevision: 0, sourceNodeId: "source" });
        expect(result.project.nodes[0].data.reference_node_ids).toEqual([]);
        expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ expected_revision: 0, source_node_id: "source" });
        await expect(atelierReferenceApi.detachReference(fixture.id, "target", { expectedRevision: 0 })).rejects.toThrow();
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("rejects a target-only ACK that still contains the legacy URL being removed", async () => {
        const value = ack({ attached: false });
        value.node_ids = ["target"];
        value.project.nodes[0].data.reference_audio_urls = ["uploads/legacy.wav"];
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(value)));
        await expect(atelierReferenceApi.detachReference(fixture.id, "target", {
            expectedRevision: 0, referenceUrl: "uploads/legacy.wav",
        })).rejects.toMatchObject({ code: "atelier_reference_ack_mismatch" });
    });

    it("rejects invalid inputs before fetch, and never retries a failed command", async () => {
        const fetch = vi.fn().mockResolvedValue(response({ detail: "Unknown commit" }, 503));
        vi.stubGlobal("fetch", fetch);
        await expect(atelierReferenceApi.attachReference(fixture.id, "target", { expectedRevision: 0, sourceNodeId: "" })).rejects.toThrow();
        await expect(atelierReferenceApi.detachReference(fixture.id, "target", { expectedRevision: 0, referenceUrl: " " })).rejects.toThrow();
        expect(fetch).not.toHaveBeenCalled();
        await expect(atelierReferenceApi.attachReference(fixture.id, "target", { expectedRevision: 0, sourceNodeId: "source" })).rejects.toThrow("Unknown commit");
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("rejects a late reference acknowledgement after environment replacement", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
            activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
            return ack();
        } }));
        await expect(atelierReferenceApi.attachReference(fixture.id, "target", { expectedRevision: 0, sourceNodeId: "source" })).rejects.toMatchObject({ code: "environment_changed" });
    });
});
