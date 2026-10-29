// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { atelierUploadApi } from "@/lib/atelierUploadApi";
import { parseAtelierProject } from "@/lib/atelierApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project.json";
import uploadFixture from "./fixtures/atelier-upload-ack.json";
import type { AtelierNode } from "@/lib/atelierTypes";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const input = () => ({ expectedRevision: 0, clientRequestId: "upload-1", nodeType: "image" as const,
    file: new File([new Uint8Array([1, 2, 3, 4])], "hero.png", { type: "image/png" }), title: "Hero", x: 12 });
function ack(reference = false, replayed = false) {
    const project = parseAtelierProject(structuredClone(fixture));
    project.revision = replayed ? 3 : 1;
    const media = { url: "uploads/hero.png", media_kind: "image", size_bytes: 4, sha256: "a".repeat(64), width: 12, height: 8,
        duration: null, poster_url: null, poster_size_bytes: null, poster_sha256: null };
    const node: AtelierNode = { ...project.nodes[0], id: "uploaded", type: "image", status: "completed", title: "Hero", x: 12,
        media_urls: [media.url], data: { filename: "hero.png", size_bytes: 4, sha256: media.sha256, width: 12, height: 8,
            ...(reference ? { parent_node_id: "target", reference_role: "image" } : {}) } };
    project.nodes = replayed ? [] : [node];
    if (reference && !replayed) project.nodes.unshift({ ...node, id: "target", type: "video", data: {
        reference_node_ids: [node.id], reference_image_urls: [media.url],
    } });
    const receipt = { schema_version: 1, receipt_id: "receipt-1", project_id: project.id, client_request_id: "upload-1",
        operation: reference ? "upload_reference_attach" : "create_media_node", request_contract_version: "atelier-upload-request-v1",
        request_fingerprint: "b".repeat(64), base_revision: 0, committed_revision: 1, created_at: 123,
        node_ids: reference ? ["target", node.id] : [node.id], media, target_node_id: reference ? "target" : null,
        source_node_id: node.id, replaced_node_id: null, previous_media: null };
    return { project: { ...project, node_upload_receipts: [receipt] }, node_ids: receipt.node_ids, receipt_id: receipt.receipt_id, replayed };
}

beforeEach(() => { vi.restoreAllMocks(); clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: A }); });

describe("Atelier managed upload client", () => {
    it("accepts the real backend serialization fixture for upload and single-snapshot readback", async () => {
        const bytes = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="), char => char.charCodeAt(0));
        const receipt = uploadFixture.project.node_upload_receipts[0];
        const expectedProject = { ...uploadFixture.project, candidate_judgments: [] };
        vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(uploadFixture)).mockResolvedValueOnce(response({ status: "found", project: uploadFixture.project, receipt })));
        const result = await atelierUploadApi.createMediaNode("p1", { expectedRevision: 0, clientRequestId: "fixture-req", nodeType: "image",
            file: new File([bytes], "pixel.png", { type: "image/png" }), title: "Pixel", x: 4, y: 5 });
        expect(uploadFixture.project).not.toHaveProperty("candidate_judgments");
        expect(result).toEqual({ ...uploadFixture, project: expectedProject });
        expect(await atelierUploadApi.getReceipt("p1", "fixture-req")).toEqual({ status: "found", project: expectedProject, receipt });
    });

    it("sends the immutable file directly with only supported request fields", async () => {
        const fetch = vi.fn().mockResolvedValue(response(ack())); vi.stubGlobal("fetch", fetch);
        const request = input();
        const result = await atelierUploadApi.createMediaNode(fixture.id, request);
        expect(result.project.node_upload_receipts[0].media.sha256).toBe("a".repeat(64));
        const url = new URL(String(fetch.mock.calls[0][0]));
        expect(url.pathname).toBe(`/atelier/projects/${fixture.id}/uploads/nodes`);
        expect(Object.fromEntries(url.searchParams)).toEqual({ expected_revision: "0", client_request_id: "upload-1", filename: "hero.png", node_type: "image", title: "Hero", x: "12" });
        expect(fetch.mock.calls[0][1].body).toBe(request.file);
        expect(new Headers(fetch.mock.calls[0][1].headers).get("Content-Type")).toBe("image/png");
    });

    it("validates atomic reference attach and accepts historical replay after deletion", async () => {
        const fetch = vi.fn().mockResolvedValueOnce(response(ack(true))).mockResolvedValueOnce(response(ack(false, true))); vi.stubGlobal("fetch", fetch);
        const { nodeType, ...request } = input();
        const attached = await atelierUploadApi.uploadReference(fixture.id, "target", { ...request, sourceNodeType: nodeType });
        expect(attached.node_ids).toEqual(["target", "uploaded"]);
        const replay = await atelierUploadApi.createMediaNode(fixture.id, input());
        expect(replay.replayed).toBe(true); expect(replay.project.nodes).toEqual([]);
    });

    it.each(["owner", "request", "operation", "revision", "media", "node", "receipt", "replayed", "source", "hash", "path", "poster"])("rejects a mismatched %s upload proof", async kind => {
        const value = ack(); const receipt = value.project.node_upload_receipts[0];
        if (kind === "owner") receipt.project_id = "other";
        if (kind === "request") receipt.client_request_id = "other";
        if (kind === "operation") receipt.operation = "upload_reference_attach";
        if (kind === "revision") receipt.committed_revision = 0;
        if (kind === "media") receipt.media.size_bytes = 5;
        if (kind === "node") value.project.nodes[0].media_urls = ["uploads/wrong.png"];
        if (kind === "receipt") value.receipt_id = "other";
        if (kind === "replayed") Object.assign(value, { replayed: "false" });
        if (kind === "source") receipt.source_node_id = "other";
        if (kind === "hash") receipt.media.sha256 = "not-a-hash";
        if (kind === "path") receipt.media.url = "uploads/../secret.png";
        if (kind === "poster") Object.assign(receipt.media, { poster_url: "uploads/poster.jpg" });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(value)));
        await expect(atelierUploadApi.createMediaNode(fixture.id, input())).rejects.toThrow();
    });

    it("rejects a reference ACK without the saved relationship", async () => {
        const value = ack(true); value.project.nodes[0].data.reference_node_ids = [];
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(value)));
        const { nodeType, ...request } = input();
        await expect(atelierUploadApi.uploadReference(fixture.id, "target", { ...request, sourceNodeType: nodeType })).rejects.toThrow();
    });

    it("checks the captured request even if its caller changes input while awaiting the ACK", async () => {
        const request = input();
        vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => {
            request.title = "Changed";
            request.x = 99;
            return response(ack());
        }));
        await expect(atelierUploadApi.createMediaNode(fixture.id, request)).resolves.toMatchObject({ replayed: false });
    });

    it("reads only the matching receipt and leaves 404 and unknown results explicit", async () => {
        const project = ack().project;
        const receipt = project.node_upload_receipts[0];
        const readback = { status: "found", project, receipt };
        const fetch = vi.fn().mockResolvedValueOnce(response(readback)).mockResolvedValueOnce(response({ ...readback, receipt: { ...receipt, client_request_id: "other" } }))
            .mockResolvedValueOnce(response({ detail: "No receipt" }, 404)).mockResolvedValueOnce(response({ detail: "Unknown commit" }, 503));
        vi.stubGlobal("fetch", fetch);
        expect((await atelierUploadApi.getReceipt(fixture.id, "upload-1")).receipt.receipt_id).toBe("receipt-1");
        await expect(atelierUploadApi.getReceipt(fixture.id, "upload-1")).rejects.toThrow();
        await expect(atelierUploadApi.getReceipt(fixture.id, "upload-1")).rejects.toMatchObject({ status: 404 });
        await expect(atelierUploadApi.createMediaNode(fixture.id, input())).rejects.toMatchObject({ status: 503 });
        expect(fetch).toHaveBeenCalledTimes(4);
    });

    it.each(["missing", "different", "owner", "status"])("rejects %s receipt/project readback snapshots", async kind => {
        const project = ack().project;
        const receipt = structuredClone(project.node_upload_receipts[0]);
        const readback = { status: "found", project, receipt };
        if (kind === "missing") project.node_upload_receipts = [];
        if (kind === "different") project.node_upload_receipts[0].media.sha256 = "c".repeat(64);
        if (kind === "owner") project.id = "other";
        if (kind === "status") readback.status = "not_found";
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(readback)));
        await expect(atelierUploadApi.getReceipt(fixture.id, "upload-1")).rejects.toThrow();
    });

    it("rejects invalid input before network and drops an environment ABA acknowledgement", async () => {
        const fetch = vi.fn().mockImplementation(async () => { activateRuntimeEnvironment({ schema_version: 1, storage_id: B }); activateRuntimeEnvironment({ schema_version: 1, storage_id: A }); return response(ack()); });
        vi.stubGlobal("fetch", fetch);
        await expect(atelierUploadApi.createMediaNode(fixture.id, { ...input(), clientRequestId: "bad id" })).rejects.toThrow();
        await expect(atelierUploadApi.createMediaNode(fixture.id, { ...input(), file: new File([], "empty.png") })).rejects.toThrow();
        expect(fetch).not.toHaveBeenCalled();
        await expect(atelierUploadApi.createMediaNode(fixture.id, input())).rejects.toMatchObject({ code: "environment_changed" });
        expect(fetch).toHaveBeenCalledTimes(1);
    });
});
