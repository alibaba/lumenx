// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API_URL } from "@/lib/apiUrl";
import { atelierProjectApi } from "@/lib/atelierApi";
import { atelierNodeHistoryApi } from "@/lib/atelierNodeHistoryApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import atelierProjectFixture from "./fixtures/atelier-project.json";
import atelierNodeHistoryFixture from "./fixtures/atelier-node-history.json";

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const STORAGE_B = "22222222-2222-4222-8222-222222222222";
const BASE_PROJECT = atelierProjectFixture as Record<string, unknown>;
const BASE_NODE = (BASE_PROJECT.nodes as Record<string, unknown>[])[0];

function jsonResponse(body: unknown, init: ResponseInit = {}) {
    return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
        ...init,
    });
}

function node(overrides: Record<string, unknown> = {}) {
    return {
        ...BASE_NODE,
        id: "node-1",
        project_id: "atelier-1",
        type: "idea",
        title: "Node",
        prompt: "Prompt",
        data: { body: "Prompt" },
        ...overrides,
    };
}

function sequenceEntry(overrides: Record<string, unknown> = {}) {
    return {
        parentId: "node-1",
        candidateId: null,
        id: "clip-1",
        trimStart: 0,
        trimEnd: 2,
        gain: 1,
        transition: { kind: "cut", duration: 0 },
        ...overrides,
    };
}

function audioTrack(overrides: Record<string, unknown> = {}) {
    return {
        id: "audio-1",
        node_id: "node-1",
        start: 0,
        trim_start: null,
        trim_end: null,
        gain: 1,
        loop: false,
        ...overrides,
    };
}

function receipt(overrides: Record<string, unknown> = {}) {
    return {
        schema_version: 1,
        receipt_id: "receipt-1",
        project_id: "atelier-1",
        base_revision: 1,
        restore_base_revision: null,
        deleted_at: 10,
        restored_at: null,
        node_ids: ["node-1"],
        node_snapshots: [{
            node: node({ media_urls: ["video/a.mp4"], data: { reference_node_ids: ["source-1"], marker: { keep: true } } }),
            original_index: 0,
            reference_media_urls: ["video/a.mp4"],
            referenced_source_ids: ["source-1"],
            source_proofs: [{ target_node_id: "node-1", source_node_id: "source-1", source_type: "video", urls: ["video/a.mp4"] }],
        }],
        data_change_groups: [{
            node_id: "target-1",
            group: "references",
            before: {
                reference_node_ids: { exists: true, value: ["node-1"] },
                reference_image_urls: { exists: false, value: null },
                reference_video_urls: { exists: false, value: null },
                reference_audio_urls: { exists: false, value: null },
            },
            after: {
                reference_node_ids: { exists: true, value: [] },
                reference_image_urls: { exists: false, value: null },
                reference_video_urls: { exists: false, value: null },
                reference_audio_urls: { exists: false, value: null },
            },
            source_proofs: [{ target_node_id: "target-1", source_node_id: "node-1", source_type: "idea", urls: [] }],
        }],
        sequence_change: {
            before: [sequenceEntry()],
            after: [],
            removed_node_ids: ["node-1"],
        },
        audio_tracks_change: {
            before: [audioTrack()],
            after: [],
            removed_node_ids: ["node-1"],
        },
        last_restore_skips: [{ scope: "data_group", reason: "current value changed", node_id: "target-1", group: "references" }],
        ...overrides,
    };
}

function receiptWith(overrides: Record<string, unknown>) {
    return { ...receipt(), ...overrides };
}

function project(overrides: Record<string, unknown> = {}) {
    const projectId = typeof overrides.id === "string" ? overrides.id : "atelier-1";
    const rawNodes = (overrides.nodes as Record<string, unknown>[] | undefined) ?? [node({ project_id: projectId })];
    const rawReceipts = (overrides.node_delete_receipts as Record<string, unknown>[] | undefined) ?? [];
    const rest = { ...overrides };
    delete rest.id;
    delete rest.nodes;
    delete rest.agent_turns;
    delete rest.exports;
    delete rest.node_delete_receipts;
    return {
        ...BASE_PROJECT,
        id: projectId,
        title: "Canvas",
        nodes: rawNodes.map(item => ({ ...item, project_id: projectId })),
        agent_turns: (BASE_PROJECT.agent_turns as Record<string, unknown>[]).map(item => ({ ...item, project_id: projectId })),
        exports: (BASE_PROJECT.exports as Record<string, unknown>[]).map(item => ({ ...item, project_id: projectId })),
        node_delete_receipts: rawReceipts,
        revision: 1,
        ...rest,
    };
}

function deleteAck(overrides: Record<string, unknown> = {}) {
    return {
        project: project({ nodes: [], node_delete_receipts: [receipt()], revision: 2 }),
        receipt_id: "receipt-1",
        node_ids: ["node-1"],
        ...overrides,
    };
}

function restoreAck(overrides: Record<string, unknown> = {}) {
    const restoredReceipt = receipt({ restore_base_revision: 2, restored_at: 20 });
    return {
        project: project({ nodes: [node()], node_delete_receipts: [restoredReceipt], revision: 3 }),
        receipt_id: "receipt-1",
        restored_node_ids: ["node-1"],
        skips: [{ scope: "data_group", reason: "current value changed", node_id: "target-1", group: "references" }],
        ...overrides,
    };
}

describe("atelier node history API", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
    });

    it("parses legacy projects with empty receipts and preserves typed non-empty receipt details", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse(project({ node_delete_receipts: undefined })))
            .mockResolvedValueOnce(jsonResponse(project({ nodes: [], node_delete_receipts: [receipt()] })));
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierProjectApi.getProject("atelier-1")).resolves.toMatchObject({ node_delete_receipts: [] });
        const parsed = await atelierProjectApi.getProject("atelier-1");

        expect(parsed.node_delete_receipts[0].node_snapshots[0].node.data.marker).toEqual({ keep: true });
        expect(parsed.node_delete_receipts[0].data_change_groups[0].before.reference_node_ids).toEqual({ exists: true, value: ["node-1"] });
        expect(parsed.node_delete_receipts[0].sequence_change?.before[0].parentId).toBe("node-1");
        expect(parsed.node_delete_receipts[0].audio_tracks_change?.before[0].node_id).toBe("node-1");
        expect(parsed.node_delete_receipts[0].last_restore_skips[0]).toMatchObject({ scope: "data_group", group: "references" });
    });

    it("accepts the shared backend-generated delete and restore ACK fixture", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse(atelierNodeHistoryFixture.deleted))
            .mockResolvedValueOnce(jsonResponse(atelierNodeHistoryFixture.restored));
        vi.stubGlobal("fetch", fetchMock);

        const deleted = await atelierNodeHistoryApi.deleteNodes("history-project", { expectedRevision: 0, nodeIds: atelierNodeHistoryFixture.deleted.node_ids });
        const restored = await atelierNodeHistoryApi.restoreNodes("history-project", { expectedRevision: 1, receiptId: atelierNodeHistoryFixture.deleted.receipt_id });

        expect(deleted.project.node_delete_receipts[0].data_change_groups.map(group => group.group)).toEqual(["references", "parent", "region"]);
        expect(deleted.project.node_delete_receipts[0].sequence_change?.removed_node_ids).toEqual(atelierNodeHistoryFixture.deleted.node_ids);
        expect(deleted.project.node_delete_receipts[0].audio_tracks_change?.removed_node_ids).toEqual(atelierNodeHistoryFixture.deleted.node_ids);
        expect(restored.project.node_delete_receipts[0].restored_at).not.toBeNull();
        expect(restored.restored_node_ids).toEqual(atelierNodeHistoryFixture.restored.restored_node_ids);
    });

    it("deletes nodes with ordered ACK receipt validation", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(deleteAck()));
        vi.stubGlobal("fetch", fetchMock);

        const result = await atelierNodeHistoryApi.deleteNodes("atelier-1", { expectedRevision: 1, nodeIds: ["node-1"] });

        expect(result.receipt_id).toBe("receipt-1");
        expect(result.project.node_delete_receipts[0].restored_at).toBeNull();
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/atelier/projects/atelier-1/nodes/delete`, expect.objectContaining({ method: "POST" }));
        expect((fetchMock.mock.calls[0][1].headers as Headers).get("X-LumenX-Environment")).toBe(STORAGE_A);
        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ expected_revision: 1, node_ids: ["node-1"] });
    });

    it("restores nodes by receipt id and validates consumed receipt/live restored nodes", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(restoreAck()));
        vi.stubGlobal("fetch", fetchMock);

        const result = await atelierNodeHistoryApi.restoreNodes("atelier-1", { expectedRevision: 2, receiptId: "receipt-1" });

        expect(result.restored_node_ids).toEqual(["node-1"]);
        expect(result.skips[0]).toMatchObject({ scope: "data_group", node_id: "target-1" });
        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ expected_revision: 2, receipt_id: "receipt-1" });
    });

    it("rejects delete and restore ACKs that do not prove the current command revision and committed skips", async () => {
        const consumed = receipt({ restore_base_revision: 2, restored_at: 20, last_restore_skips: [] });
        const wrongSkip = { scope: "sequence", reason: "current value changed", node_id: null, group: null };
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse(deleteAck({ project: project({ nodes: [], revision: 2, node_delete_receipts: [receipt({ base_revision: 0 })] }) })))
            .mockResolvedValueOnce(jsonResponse(restoreAck({ project: project({ revision: 3, nodes: [node()], node_delete_receipts: [receipt({ restore_base_revision: 1, restored_at: 20 })] }) })))
            .mockResolvedValueOnce(jsonResponse(restoreAck({ project: project({ revision: 3, nodes: [node()], node_delete_receipts: [consumed] }), skips: [wrongSkip] })));
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierNodeHistoryApi.deleteNodes("atelier-1", { expectedRevision: 1, nodeIds: ["node-1"] })).rejects.toMatchObject({ code: "atelier_delete_receipt_base_revision_mismatch" });
        await expect(atelierNodeHistoryApi.restoreNodes("atelier-1", { expectedRevision: 2, receiptId: "receipt-1" })).rejects.toMatchObject({ code: "atelier_restore_receipt_base_revision_mismatch" });
        await expect(atelierNodeHistoryApi.restoreNodes("atelier-1", { expectedRevision: 2, receiptId: "receipt-1" })).rejects.toMatchObject({ code: "atelier_restore_ack_skips_mismatch" });
    });

    it("rejects invalid local history inputs before fetch", async () => {
        const fetchMock = vi.fn();
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierNodeHistoryApi.deleteNodes("atelier-1", { expectedRevision: 0, nodeIds: [] })).rejects.toMatchObject({ code: "invalid_atelier_node_ids" });
        await expect(atelierNodeHistoryApi.deleteNodes("atelier-1", { expectedRevision: 0, nodeIds: ["node-1", "node-1"] })).rejects.toMatchObject({ code: "invalid_atelier_node_ids" });
        await expect(atelierNodeHistoryApi.deleteNodes("atelier-1", { expectedRevision: 0, nodeIds: [""] })).rejects.toMatchObject({ code: "invalid_atelier_node_ids" });
        await expect(atelierNodeHistoryApi.restoreNodes("atelier-1", { expectedRevision: 0, receiptId: "" })).rejects.toMatchObject({ code: "invalid_atelier_delete_receipt_id" });
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("rejects malformed receipts and ACK relationships", async () => {
        const malformed = vi.fn()
            .mockResolvedValueOnce(jsonResponse(project({ nodes: [], node_delete_receipts: [receipt({ project_id: "other" })] })))
            .mockResolvedValueOnce(jsonResponse(project({ nodes: [], node_delete_receipts: [receipt(), receipt()] })))
            .mockResolvedValueOnce(jsonResponse(project({ nodes: [], node_delete_receipts: [receiptWith({ restore_base_revision: 2, restored_at: null })] })))
            .mockResolvedValueOnce(jsonResponse(project({ nodes: [], node_delete_receipts: [receiptWith({ base_revision: 99 })] })))
            .mockResolvedValueOnce(jsonResponse(project({ nodes: [], node_delete_receipts: [receiptWith({ node_snapshots: [receipt().node_snapshots[0], { ...receipt().node_snapshots[0], node: node({ id: "node-2" }), original_index: 0 }] })] })))
            .mockResolvedValueOnce(jsonResponse(project({ nodes: [], node_delete_receipts: [receiptWith({ data_change_groups: [{ ...receipt().data_change_groups[0], before: { reference_node_ids: { exists: true, value: ["node-1"] } } }] })] })))
            .mockResolvedValueOnce(jsonResponse(project({ nodes: [], node_delete_receipts: [receiptWith({ node_snapshots: [{ ...receipt().node_snapshots[0], source_proofs: [{ target_node_id: "other", source_node_id: "source-1", source_type: "video", urls: [] }] }] })] })))
            .mockResolvedValueOnce(jsonResponse(deleteAck({ node_ids: ["other"] })))
            .mockResolvedValueOnce(jsonResponse(deleteAck({ project: project({ nodes: [], node_delete_receipts: [] }) })))
            .mockResolvedValueOnce(jsonResponse(deleteAck({ project: project({ nodes: [node()], node_delete_receipts: [receipt()] }) })))
            .mockResolvedValueOnce(jsonResponse(restoreAck({ project: project({ revision: 2, nodes: [], node_delete_receipts: [receipt()] }) })))
            .mockResolvedValueOnce({
                ok: true,
                status: 200,
                json: async () => {
                    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
                    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
                    return deleteAck();
                },
            } as Response);
        vi.stubGlobal("fetch", malformed);

        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "atelier_delete_receipt_owner_mismatch" });
        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "atelier_delete_receipt_duplicate" });
        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "atelier_delete_receipt_restore_fields_inconsistent" });
        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "atelier_delete_receipt_revision_invalid" });
        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "atelier_delete_receipt_snapshot_mismatch" });
        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "atelier_delete_receipt_group_fields_invalid" });
        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "atelier_delete_receipt_source_proof_owner_mismatch" });
        await expect(atelierNodeHistoryApi.deleteNodes("atelier-1", { expectedRevision: 1, nodeIds: ["node-1"] })).rejects.toMatchObject({ code: "atelier_delete_ack_target_mismatch" });
        await expect(atelierNodeHistoryApi.deleteNodes("atelier-1", { expectedRevision: 1, nodeIds: ["node-1"] })).rejects.toMatchObject({ code: "atelier_delete_receipt_missing" });
        await expect(atelierNodeHistoryApi.deleteNodes("atelier-1", { expectedRevision: 1, nodeIds: ["node-1"] })).rejects.toMatchObject({ code: "atelier_delete_receipt_live_overlap" });
        await expect(atelierNodeHistoryApi.restoreNodes("atelier-1", { expectedRevision: 2, receiptId: "receipt-1" })).rejects.toMatchObject({ code: "atelier_restore_receipt_unconsumed" });
        await expect(atelierNodeHistoryApi.deleteNodes("atelier-1", { expectedRevision: 1, nodeIds: ["node-1"] })).rejects.toMatchObject({ code: "environment_changed" });
    });
});
