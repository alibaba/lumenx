// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAtelierStore } from "@/store/atelierStore";
import { parseAtelierProject } from "@/lib/atelierApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project.json";
import history from "./fixtures/atelier-node-history.json";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const state = () => useAtelierStore.getState();
const nodeId = fixture.nodes[0].id;
const regionId = "region";
function project(id = fixture.id, revision = 0) {
    const p = structuredClone(fixture);
    return { ...p, id, revision, nodes: [
        ...p.nodes.map(n => ({ ...n, project_id: id })),
        { ...p.nodes[0], id: regionId, project_id: id, type: "region", data: { color: "#646cff", collapsed: false } },
    ], agent_turns: p.agent_turns.map(t => ({ ...t, project_id: id })),
    exports: p.exports.map(e => ({ ...e, project_id: id })) };
}
const ack = (revision: number, ids = [nodeId]) => ({ project: project(fixture.id, revision), node_ids: ids });
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const projectWithCandidateJudgments = <T extends Record<string, unknown>>(project: T) => ({ ...project, candidate_judgments: [] });
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(r => { resolve = r; });
    return { promise, resolve };
}
async function setup() {
    const fetch = vi.fn().mockResolvedValueOnce(response([project(), project("b")]));
    vi.stubGlobal("fetch", fetch);
    await state().loadProjects();
    return fetch;
}
beforeEach(() => {
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
});

describe("Atelier node commands share one confirmed project write queue", () => {
    it.each([
        ["create", () => state().createNode({ type: "draft", title: "new" }), "/nodes", [nodeId]],
        ["text", () => state().updateNodeText(nodeId, { prompt: "new" }), `/nodes/${nodeId}/text`, [nodeId]],
        ["body", () => state().updateNodeBody(nodeId, { body: "new" }), `/nodes/${nodeId}/body`, [nodeId]],
        ["layout", () => state().updateNodeLayout(nodeId, { x: 20 }), `/nodes/${nodeId}/layout`, [nodeId]],
        ["layouts", () => state().updateNodesLayout({ updates: [{ nodeId, y: 20 }] }), "/nodes/layout", [nodeId]],
        ["move", () => state().moveNodes({ nodeIds: [nodeId], dx: 20 }), "/nodes/move", [nodeId]],
        ["wrap", () => state().wrapRegion({ memberNodeIds: [nodeId] }), "/regions/wrap", [regionId]],
        ["members", () => state().setRegionMembers(regionId, { memberNodeIds: [] }), `/regions/${regionId}/members`, [regionId]],
        ["appearance", () => state().updateRegionAppearance(regionId, { collapsed: true }), `/regions/${regionId}/appearance`, [regionId]],
    ] as const)("adopts the entire confirmed %s acknowledgement", async (_name, send, path, ids) => {
        const fetch = await setup();
        fetch.mockResolvedValueOnce(response(ack(1, [...ids])));
        await send();
        expect(String(fetch.mock.calls[1][0])).toContain(path);
        expect(JSON.parse(fetch.mock.calls[1][1].body).expected_revision).toBe(0);
        expect(state().currentProject?.revision).toBe(1);
        expect(state().currentProject?.nodes[0].data).toEqual(fixture.nodes[0].data);
        expect(state().currentProject?.agent_turns).toEqual(fixture.agent_turns);
        expect(state().isSaving).toBe(false);
    });

    it("serializes node and metadata edits with each confirmed revision", async () => {
        const fetch = await setup();
        const first = deferred<Response>();
        fetch.mockReturnValueOnce(first.promise).mockResolvedValueOnce(response(project(fixture.id, 2)))
            .mockResolvedValueOnce(response(ack(3)));
        const node = state().updateNodeText(nodeId, { prompt: "first" });
        const metadata = state().updateProject({ title: "second" });
        const layout = state().updateNodeLayout(nodeId, { x: 30 });
        expect(fetch).toHaveBeenCalledTimes(2);
        expect(state().currentProject?.revision).toBe(0);
        first.resolve(response(ack(1)));
        await Promise.all([node, metadata, layout]);
        expect(fetch.mock.calls.slice(1).map(call => JSON.parse(call[1].body).expected_revision)).toEqual([0, 1, 2]);
        expect(state().currentProject?.revision).toBe(3);
    });

    it("rejects all queued edits after a failure without extra network requests", async () => {
        const fetch = await setup();
        const first = deferred<Response>();
        fetch.mockReturnValueOnce(first.promise);
        const results = Promise.allSettled([
            state().updateNodeText(nodeId, { prompt: "first" }),
            state().moveNodes({ nodeIds: [nodeId], dx: 30 }),
            state().updateProject({ title: "third" }),
        ]);
        first.resolve(response({ detail: "Unknown commit" }, 503));
        expect((await results).every(r => r.status === "rejected")).toBe(true);
        expect(fetch).toHaveBeenCalledTimes(2);
        expect(state().needsRefresh).toBe(true);
        expect(state().currentProject?.revision).toBe(0);
        fetch.mockResolvedValueOnce(response(project(fixture.id, 1))).mockResolvedValueOnce(response(ack(2)));
        await state().refreshProject();
        await state().moveNodes({ nodeIds: [nodeId], dx: 5 });
        expect(JSON.parse(fetch.mock.calls[3][1].body).expected_revision).toBe(1);
    });

    it("does not dispatch old queued edits after navigating away and back", async () => {
        const fetch = await setup();
        const first = deferred<Response>();
        fetch.mockReturnValueOnce(first.promise).mockResolvedValueOnce(response(project("b")))
            .mockResolvedValueOnce(response(project()));
        const saving = state().updateNodeText(nodeId, { title: "first" });
        const queued = Promise.allSettled([state().updateNodeLayout(nodeId, { x: 99 })]);
        await state().switchProject("b");
        await state().switchProject(fixture.id);
        first.resolve(response(ack(1)));
        await saving;
        expect((await queued)[0].status).toBe("rejected");
        expect(fetch).toHaveBeenCalledTimes(4);
        expect(state().currentProject?.revision).toBe(1);
        expect(state().needsRefresh).toBe(false);
        expect(state().isSaving).toBe(false);
    });

    it("clears queued ownership on an environment change", async () => {
        const fetch = await setup();
        const first = deferred<Response>();
        fetch.mockReturnValueOnce(first.promise);
        const results = Promise.allSettled([
            state().updateNodeBody(nodeId, { body: "first" }),
            state().updateNodeText(nodeId, { title: "second" }),
        ]);
        activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
        first.resolve(response(ack(1)));
        expect((await results).every(r => r.status === "rejected")).toBe(true);
        expect(fetch).toHaveBeenCalledTimes(2);
        expect(state().projects).toEqual([]);
        expect(state().error).toBeNull();
    });

    it("captures queued intent without accepting later caller mutation", async () => {
        const fetch = await setup();
        const first = deferred<Response>();
        fetch.mockReturnValueOnce(first.promise).mockResolvedValueOnce(response(ack(2)));
        const saving = state().updateNodeText(nodeId, { title: "first" });
        const patch = { updates: [{ nodeId, x: 10 }] };
        const queued = state().updateNodesLayout(patch);
        patch.updates[0].x = 99;
        first.resolve(response(ack(1)));
        await Promise.all([saving, queued]);
        expect(JSON.parse(fetch.mock.calls[2][1].body).updates).toEqual([{ node_id: nodeId, x: 10 }]);
    });

    it("queues project deletion after the last confirmed node command", async () => {
        const fetch = await setup();
        const first = deferred<Response>();
        fetch.mockReturnValueOnce(first.promise).mockResolvedValueOnce(response({ ok: true }));
        const saving = state().updateNodeLayout(nodeId, { x: 10 });
        const deleting = state().deleteProject();
        first.resolve(response(ack(1)));
        await Promise.all([saving, deleting]);
        expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ expected_revision: 1 });
        expect(state().currentProject).toBeNull();
        expect(state().projects.map(p => p.id)).toEqual(["b"]);
    });

    it("queues pending reference attachments with strict ACK ownership and aborts after unknown predecessors", async () => {
        const original = parseAtelierProject({ ...structuredClone(history.restored.project), revision: 0, node_delete_receipts: [] });
        const target = original.nodes.find(n => n.id === "target")!;
        const image = original.nodes.find(n => n.id === "image")!;
        target.data = {};
        image.media_urls = [];
        const pending = { ...structuredClone(original), revision: 1, nodes: original.nodes.map(n => n.id === "target"
            ? { ...n, data: { reference_node_ids: ["image"], pending_reference_nodes: [{ schema_version: 1, source_node_id: "image", source_kind: "image", status: "pending_media" }] } }
            : n) };
        const fetch = vi.fn().mockResolvedValueOnce(response([original]));
        vi.stubGlobal("fetch", fetch);
        await state().loadProjects();
        fetch.mockResolvedValueOnce(response({ project: pending, node_ids: ["target", "image"] }));
        await state().attachPendingReferenceNode("target", { sourceNodeId: "image" });
        expect(String(fetch.mock.calls[1][0])).toContain("/references/attach_pending");
        expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ expected_revision: 0, source_node_id: "image" });
        expect(state().currentProject?.nodes.find(n => n.id === "target")?.data.pending_reference_nodes).toEqual([
            { schema_version: 1, source_node_id: "image", source_kind: "image", status: "pending_media" },
        ]);

        fetch.mockResolvedValueOnce(response({ detail: "Unknown commit" }, 503));
        const failed = state().updateNodeText("target", { title: "blocked" });
        const queued = state().attachPendingReferenceNode("target", { sourceNodeId: "image" });
        await expect(failed).rejects.toThrow("Unknown commit");
        await expect(queued).rejects.toThrow("Unknown commit");
        expect(fetch.mock.calls.filter(call => String(call[0]).includes("attach_pending"))).toHaveLength(1);
        expect(state().needsRefresh).toBe(true);
    });

    it("rejects a late pending reference acknowledgement after an A-to-B-to-A environment switch", async () => {
        const original = parseAtelierProject({ ...structuredClone(history.restored.project), revision: 0, node_delete_receipts: [] });
        const target = original.nodes.find(n => n.id === "target")!;
        const image = original.nodes.find(n => n.id === "image")!;
        target.data = {};
        image.media_urls = [];
        const pending = { ...structuredClone(original), revision: 1, nodes: original.nodes.map(n => n.id === "target"
            ? { ...n, data: { reference_node_ids: ["image"], pending_reference_nodes: [{ schema_version: 1, source_node_id: "image", source_kind: "image", status: "pending_media" }] } }
            : n) };
        const fetch = vi.fn().mockResolvedValueOnce(response([original]));
        vi.stubGlobal("fetch", fetch);
        await state().loadProjects();
        fetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
            activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
            return { project: pending, node_ids: ["target", "image"] };
        } });
        await expect(state().attachPendingReferenceNode("target", { sourceNodeId: "image" })).rejects.toThrow();
        expect(state().currentProject).toBeNull();
    });
});

describe("Atelier deletion state adopts server-owned receipts", () => {
    async function setupHistory() {
        const original = { ...structuredClone(history.restored.project), revision: 0, node_delete_receipts: [] };
        const fetch = vi.fn().mockResolvedValueOnce(response([original]));
        vi.stubGlobal("fetch", fetch);
        await state().loadProjects();
        return fetch;
    }

    it("only removes nodes after delete ACK and restores by receipt with the latest revision", async () => {
        const fetch = await setupHistory();
        const deleting = deferred<Response>();
        fetch.mockReturnValueOnce(deleting.promise).mockResolvedValueOnce(response(history.restored));
        state().selectNode("clip");
        const saved = state().deleteNodes([...history.deleted.node_ids]);
        const restored = state().restoreNodes(history.deleted.receipt_id);
        expect(state().currentProject?.nodes.some(n => n.id === "clip")).toBe(true);
        expect(fetch).toHaveBeenCalledTimes(2);
        deleting.resolve(response(history.deleted));
        await saved;
        await restored;
        expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ expected_revision: 1, receipt_id: history.deleted.receipt_id });
        expect(history.restored.project).not.toHaveProperty("candidate_judgments");
        expect(state().currentProject).toEqual(projectWithCandidateJudgments(history.restored.project));
        expect(state().selectedNodeId).toBeNull();
    });

    it("recovers a lost delete ACK by readback without replaying deletion", async () => {
        const fetch = await setupHistory();
        fetch.mockResolvedValueOnce(response({ detail: "Lost acknowledgement" }, 503));
        await expect(state().deleteNodes([...history.deleted.node_ids])).rejects.toThrow("Lost acknowledgement");
        expect(state().currentProject?.nodes.some(n => n.id === "clip")).toBe(true);
        expect(state().currentProject?.node_delete_receipts).toEqual([]);
        await expect(state().restoreNodes(history.deleted.receipt_id)).rejects.toThrow("Refresh");
        expect(fetch).toHaveBeenCalledTimes(2);
        fetch.mockResolvedValueOnce(response(history.deleted.project)).mockResolvedValueOnce(response(history.restored));
        await state().refreshProject();
        expect(state().currentProject?.node_delete_receipts[0].receipt_id).toBe(history.deleted.receipt_id);
        await state().restoreNodes(history.deleted.receipt_id);
        expect(fetch.mock.calls.filter(call => String(call[0]).endsWith("/nodes/delete"))).toHaveLength(1);
        expect(state().currentProject?.node_delete_receipts[0].restored_at).not.toBeNull();
    });

    it("keeps deleted state and the unconsumed receipt after a failed restore", async () => {
        const fetch = await setupHistory();
        fetch.mockResolvedValueOnce(response(history.deleted)).mockResolvedValueOnce(response({ detail: "Revision conflict" }, 409));
        await state().deleteNodes([...history.deleted.node_ids]);
        await expect(state().restoreNodes(history.deleted.receipt_id)).rejects.toThrow("Revision conflict");
        expect(history.deleted.project).not.toHaveProperty("candidate_judgments");
        expect(state().currentProject).toEqual(projectWithCandidateJudgments(history.deleted.project));
        expect(state().needsRefresh).toBe(true);
    });
});

describe("Atelier references use the same project queue", () => {
    it("uses the actual revision after a no-op attach before detaching", async () => {
        const original = parseAtelierProject({ ...structuredClone(history.restored.project), revision: 0, node_delete_receipts: [] });
        const saved = { ...structuredClone(original), revision: 1, title: "Edited" };
        const detached = { ...structuredClone(saved), revision: 2 };
        const target = detached.nodes.find(n => n.id === "target")!;
        target.data.reference_node_ids = ["other", "audio"];
        target.data.reference_image_urls = ["uploads/other.png", "standalone.png"];
        const image = detached.nodes.find(n => n.id === "image")!;
        image.data.parent_node_id = null;
        image.data.reference_role = null;
        const first = deferred<Response>();
        const fetch = vi.fn().mockResolvedValueOnce(response([original])).mockReturnValueOnce(first.promise)
            .mockResolvedValueOnce(response({ project: saved, node_ids: ["target", "image"] }))
            .mockResolvedValueOnce(response({ project: detached, node_ids: ["target", "image"] }));
        vi.stubGlobal("fetch", fetch);
        await state().loadProjects();
        const metadata = state().updateProject({ title: "Edited" });
        const attached = state().attachReferenceNode("target", { sourceNodeId: "image" });
        const removed = state().detachReferenceNode("target", { sourceNodeId: "image", referenceUrl: "uploads/image.png" });
        expect(fetch).toHaveBeenCalledTimes(2);
        first.resolve(response(saved));
        await Promise.all([metadata, attached, removed]);
        expect(fetch.mock.calls.slice(1).map(call => JSON.parse(call[1].body).expected_revision)).toEqual([0, 1, 1]);
        expect(state().currentProject?.revision).toBe(2);
        expect(state().currentProject?.nodes.find(n => n.id === "target")?.data.reference_node_ids).toEqual(["other", "audio"]);
    });
});
