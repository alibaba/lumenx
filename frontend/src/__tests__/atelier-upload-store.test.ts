// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAtelierStore } from "@/store/atelierStore";
import { parseAtelierProject } from "@/lib/atelierApi";
import {
    readAtelierUploadRequest,
    atelierUploadProjectPendingKey,
} from "@/lib/atelierUploadRequest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project.json";
import type { AtelierMediaKind, AtelierNode, AtelierNodeUploadReceipt, AtelierProject } from "@/lib/atelierTypes";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const state = () => useAtelierStore.getState();
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(r => { resolve = r; });
    return { promise, resolve };
}

function baseProject(id = "p1", revision = 0): AtelierProject {
    const parsed = parseAtelierProject({
        ...structuredClone(fixture),
        id,
        revision,
        nodes: [],
        agent_turns: [],
        sequence: [],
        audio_tracks: [],
        exports: [],
        node_delete_receipts: [],
        node_upload_receipts: [],
        source_project_id: null,
    });
    return parsed;
}

function mediaNode(projectId: string, id = "upload-node"): AtelierNode {
    const base = parseAtelierProject(fixture).nodes[0];
    return {
        ...structuredClone(base),
        id,
        project_id: projectId,
        type: "image",
        title: "Hero",
        prompt: "",
        status: "completed",
        x: 12,
        y: 20,
        width: 240,
        height: 160,
        media_urls: ["uploads/hero.png"],
        data: { filename: "hero.png", size_bytes: 4, sha256: "a".repeat(64), width: 12, height: 8 },
    };
}

function uploadReceipt(projectId: string, requestId = "req-1", baseRevision = 0, nodeId = "upload-node"): AtelierNodeUploadReceipt {
    return {
        schema_version: 1,
        receipt_id: `receipt-${requestId}`,
        project_id: projectId,
        client_request_id: requestId,
        operation: "create_media_node",
        request_contract_version: "atelier-upload-request-v1",
        request_fingerprint: "b".repeat(64),
        base_revision: baseRevision,
        committed_revision: baseRevision + 1,
        created_at: 10,
        node_ids: [nodeId],
        media: {
            url: "uploads/hero.png",
            media_kind: "image",
            size_bytes: 4,
            sha256: "a".repeat(64),
            width: 12,
            height: 8,
            duration: null,
            poster_url: null,
            poster_size_bytes: null,
            poster_sha256: null,
        },
        target_node_id: null,
        source_node_id: nodeId,
        replaced_node_id: null,
        previous_media: null,
    };
}

function uploadedProject(id = "p1", requestId = "req-1", revision = 1): AtelierProject {
    const p = baseProject(id, revision);
    const node = mediaNode(id);
    const receipt = uploadReceipt(id, requestId, revision - 1, node.id);
    return { ...p, revision, nodes: [node], node_upload_receipts: [receipt] };
}

function uploadAck(project = uploadedProject()) {
    const receipt = project.node_upload_receipts[0];
    return { project, node_ids: [...receipt.node_ids], receipt_id: receipt.receipt_id, replayed: false };
}

function readback(project: AtelierProject) {
    const receipt = project.node_upload_receipts[0];
    return { status: "found", project, receipt };
}

function requestIdFromUrl(url: string): string {
    return new URL(url).searchParams.get("client_request_id")!;
}

function file(name = "hero.png") {
    return new File([new Uint8Array([1, 2, 3, 4])], name, { type: "image/png" });
}

async function setup(project = baseProject()) {
    const fetch = vi.fn().mockResolvedValueOnce(response([project]));
    vi.stubGlobal("fetch", fetch);
    await state().loadProjects();
    return fetch;
}

beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
});

describe("Atelier upload store transactions", () => {
    it("persists intent before POST, queues on the current revision, adopts ACK, then clears pending proof", async () => {
        const fetch = await setup(baseProject("p1", 4));
        fetch.mockImplementationOnce(async (url: string) => response(uploadAck(uploadedProject("p1", requestIdFromUrl(url), 5))));

        await state().createMediaNode({ file: file(), nodeType: "image", title: "Hero", x: 12, y: 20, width: 240, height: 160 });

        const call = fetch.mock.calls[1];
        expect(String(call[0])).toContain("/atelier/projects/p1/uploads/nodes");
        expect(String(call[0])).toContain("expected_revision=4");
        expect(call[1].body).toBeInstanceOf(File);
        expect(new Headers(call[1].headers).get("Content-Type")).toBe("image/png");
        expect(state().currentProject?.revision).toBe(5);
        expect(readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })).toBeNull();
    });

    it("keeps unknown outcomes for reload readback without replaying the upload", async () => {
        const fetch = await setup(baseProject("p1", 0));
        fetch.mockResolvedValueOnce(response({ detail: "Commit outcome unknown" }, 503));
        await expect(state().createMediaNode({ file: file(), nodeType: "image" })).rejects.toThrow("Commit outcome unknown");
        expect(fetch).toHaveBeenCalledTimes(2);
        const pendingId = readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })!.clientRequestId;
        expect(pendingId).toMatch(/^upload-/);
        expect(state().loadPendingUploads().map(item => item.clientRequestId)).toEqual([pendingId]);

        fetch.mockImplementationOnce(async () => {
            const requestId = readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })!.clientRequestId;
            return response(readback(uploadedProject("p1", requestId)));
        });
        await state().readbackPendingUpload(pendingId);
        expect(fetch.mock.calls.filter(call => String(call[0]).includes("/uploads/nodes"))).toHaveLength(1);
        expect(state().currentProject?.node_upload_receipts).toHaveLength(1);
        expect(readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })).toBeNull();
    });

    it("does not clear pending state or retry when receipt readback is 404", async () => {
        const fetch = await setup(baseProject("p1", 0));
        fetch.mockResolvedValueOnce(response({ detail: "Unknown" }, 503));
        await expect(state().createMediaNode({ file: file(), nodeType: "image" })).rejects.toThrow();
        fetch.mockResolvedValueOnce(response({ detail: "Missing receipt" }, 404));
        await expect(state().readbackPendingUpload()).resolves.toBeNull();
        expect(readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })).not.toBeNull();
        expect(fetch.mock.calls.filter(call => String(call[0]).includes("/uploads/nodes"))).toHaveLength(1);
    });

    it("fails closed on corrupt or unavailable storage before POST", async () => {
        const fetch = await setup(baseProject("p1", 0));
        localStorage.setItem(`${atelierUploadProjectPendingKey({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })}:bad`, "{broken");
        await expect(state().createMediaNode({ file: file(), nodeType: "image" })).rejects.toThrow();
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("fails closed when a current owner key stores a mismatched owner payload", async () => {
        const fetch = await setup(baseProject("p1", 0));
        const environment = requireActiveRuntimeEnvironment();
        localStorage.setItem(`${atelierUploadProjectPendingKey({ environment, projectId: "p1" })}:upload-mismatch`, JSON.stringify({
            schema_version: 1,
            apiBase: environment.apiUrl,
            storageId: B,
            projectId: "p1",
            clientRequestId: "upload-mismatch",
            operation: "create_media_node",
            targetNodeId: null,
            mediaKind: "image",
            fileName: "hero.png",
            fileSize: 4,
            baseRevision: 0,
        }));
        await expect(state().createMediaNode({ file: file(), nodeType: "image" })).rejects.toThrow();
        expect(fetch).toHaveBeenCalledTimes(1);
    });

    it("ignores corrupt request records from a different runtime environment", async () => {
        activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        localStorage.setItem(`${atelierUploadProjectPendingKey({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })}:bad`, "{broken");
        activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
        const fetch = await setup(baseProject("p1", 0));
        fetch.mockImplementationOnce(async (url: string) => response(uploadAck(uploadedProject("p1", requestIdFromUrl(url), 1))));
        await state().createMediaNode({ file: file(), nodeType: "image" });
        expect(state().currentProject?.revision).toBe(1);
    });

    it("clears journal for definite POST rejections but keeps unknown POST failures", async () => {
        const fetch = await setup(baseProject("p1", 0));
        fetch.mockResolvedValueOnce(response({ detail: "Bad image" }, 422));
        await expect(state().createMediaNode({ file: file(), nodeType: "image" })).rejects.toThrow("Bad image");
        expect(readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })).toBeNull();

        fetch.mockResolvedValueOnce(response(baseProject("p1", 0)));
        await state().refreshProject();
        fetch.mockResolvedValueOnce(response({ detail: "Commit uncertain" }, 503));
        await expect(state().createMediaNode({ file: file("hero2.png"), nodeType: "image" })).rejects.toThrow("Commit uncertain");
        expect(readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })).not.toBeNull();
    });

    it("clears the journal for structured active-target upload rejection but keeps generic 409 conflicts", async () => {
        const fetch = await setup(baseProject("p1", 0));
        fetch.mockResolvedValueOnce(response({
            detail: {
                code: "atelier_upload_target_active_execution",
                detail: "Target node has active execution; wait for it to finish before uploading a reference",
                commit_uncertain: false,
            },
        }, 409));
        await expect(state().uploadReference("target", { file: file(), sourceNodeType: "image" }))
            .rejects.toThrow("Target node has active execution");
        expect(readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })).toBeNull();

        fetch.mockResolvedValueOnce(response(baseProject("p1", 0)));
        await state().refreshProject();
        fetch.mockResolvedValueOnce(response({ code: "storage_conflict", detail: "The saved version changed; reload and retry", commit_uncertain: false }, 409));
        await expect(state().uploadReference("target", { file: file("hero2.png"), sourceNodeType: "image" }))
            .rejects.toThrow("The saved version changed");
        expect(readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })).not.toBeNull();
    });

    it("does not restore deleted historical nodes when readback finds a replayed receipt", async () => {
        const fetch = await setup(baseProject("p1", 0));
        fetch.mockResolvedValueOnce(response({ detail: "Unknown" }, 503));
        await expect(state().createMediaNode({ file: file(), nodeType: "image" })).rejects.toThrow();
        const requestId = readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })!.clientRequestId;
        const replayed = { ...baseProject("p1", 3), node_upload_receipts: [uploadReceipt("p1", requestId, 0)] };
        fetch.mockResolvedValueOnce(response(readback(replayed)));
        await state().readbackPendingUpload();
        expect(state().currentProject?.revision).toBe(3);
        expect(state().currentProject?.nodes).toEqual([]);
    });

    it("snapshots caller scalars and blocks a second upload while one project intent is pending", async () => {
        const fetch = await setup(baseProject("p1", 0));
        const pending = deferred<Response>();
        fetch.mockReturnValueOnce(pending.promise);
        const firstInput = { file: file(), nodeType: "image" as AtelierMediaKind, title: "Hero" };
        const first = state().createMediaNode(firstInput);
        firstInput.title = "Mutated";
        await expect(state().uploadReference("target", { file: file(), sourceNodeType: "image" })).rejects.toThrow();
        pending.resolve(response({ detail: "Lost" }, 503));
        await expect(first).rejects.toThrow("Lost");
        const stored = readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" });
        expect(stored?.title).toBe("Hero");
        expect(fetch.mock.calls.filter(call => String(call[0]).includes("/uploads/"))).toHaveLength(1);
    });

    it("caches cross-project late ACKs without switching the current project", async () => {
        const oldA = deferred<Response>();
        const fetch = vi.fn().mockResolvedValueOnce(response([baseProject("p1", 0), baseProject("p2", 0)]))
            .mockReturnValueOnce(oldA.promise)
            .mockResolvedValueOnce(response(baseProject("p2", 0)));
        vi.stubGlobal("fetch", fetch);
        await state().loadProjects();
        const saving = state().createMediaNode({ file: file(), nodeType: "image" });
        await state().switchProject("p2");
        oldA.resolve(response(uploadAck(uploadedProject("p1", requestIdFromUrl(String(fetch.mock.calls[1][0])), 1))));
        await saving;
        expect(state().currentProject?.id).toBe("p2");
        expect(state().projects.find(p => p.id === "p1")?.revision).toBe(1);
    });

    it("does not let a late readback overwrite the visible project pending list or error", async () => {
        const receiptRead = deferred<Response>();
        const fetch = vi.fn().mockResolvedValueOnce(response([baseProject("p1", 0), baseProject("p2", 0)]))
            .mockResolvedValueOnce(response({ detail: "Commit uncertain" }, 503))
            .mockReturnValueOnce(receiptRead.promise)
            .mockResolvedValueOnce(response(baseProject("p2", 0)))
            .mockResolvedValueOnce(response({ detail: "Project B failed" }, 503));
        vi.stubGlobal("fetch", fetch);
        await state().loadProjects();
        await expect(state().createMediaNode({ file: file(), nodeType: "image" })).rejects.toThrow("Commit uncertain");
        const pendingId = state().pendingUploads[0].clientRequestId;
        const readbackResult = state().readbackPendingUpload(pendingId);
        await state().switchProject("p2");
        await expect(state().refreshProject()).rejects.toThrow("Project B failed");

        receiptRead.resolve(response(readback(uploadedProject("p1", pendingId, 1))));
        await readbackResult;
        expect(state().currentProject?.id).toBe("p2");
        expect(state().pendingUploads).toEqual([]);
        expect(state().error).toBe("Project B failed");
        expect(state().projects.find(p => p.id === "p1")?.revision).toBe(1);
    });

    it("does not let an old environment upload failure publish pending uploads into the active environment", async () => {
        const delayed = deferred<Response>();
        const fetch = vi.fn().mockResolvedValueOnce(response([baseProject("p1", 0)]))
            .mockReturnValueOnce(delayed.promise)
            .mockResolvedValueOnce(response([baseProject("p1", 0)]));
        vi.stubGlobal("fetch", fetch);
        await state().loadProjects();
        const saving = state().createMediaNode({ file: file(), nodeType: "image" });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        await state().loadProjects();
        delayed.resolve(response({ detail: "Commit uncertain" }, 503));
        await expect(saving).rejects.toThrow();
        expect(state().currentProject?.id).toBe("p1");
        expect(state().pendingUploads).toEqual([]);
        expect(readAtelierUploadRequest({ environment: requireActiveRuntimeEnvironment(), projectId: "p1" })).toBeNull();
    });

    it("clears only the acknowledged request when another pending upload exists for the same project", async () => {
        const fetch = await setup(baseProject("p1", 0));
        const environment = requireActiveRuntimeEnvironment();
        const delayed = deferred<Response>();
        fetch.mockReturnValueOnce(delayed.promise);
        const saving = state().createMediaNode({ file: file(), nodeType: "image" });
        const ownRequestId = requestIdFromUrl(String(fetch.mock.calls[1][0]));
        localStorage.setItem(`${atelierUploadProjectPendingKey({ environment, projectId: "p1" })}:upload-000-other`, JSON.stringify({
            schema_version: 1,
            apiBase: environment.apiUrl,
            storageId: environment.storageId,
            projectId: "p1",
            clientRequestId: "upload-000-other",
            operation: "create_media_node",
            targetNodeId: null,
            mediaKind: "image",
            fileName: "other.png",
            fileSize: 4,
            baseRevision: 0,
        }));
        delayed.resolve(response(uploadAck(uploadedProject("p1", ownRequestId, 1))));
        await saving;
        expect(readAtelierUploadRequest({ environment, projectId: "p1" })?.clientRequestId).toBe("upload-000-other");
        expect(state().pendingUploads.map(item => item.clientRequestId)).toEqual(["upload-000-other"]);
    });

    it("rejects late ACKs after environment A to B to A and keeps the current session clean", async () => {
        const fetch = await setup(baseProject("p1", 0));
        const pending = deferred<Response>();
        fetch.mockReturnValueOnce(pending.promise);
        const saving = state().createMediaNode({ file: file(), nodeType: "image" });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
        pending.resolve(response(uploadAck(uploadedProject("p1", "req-1", 1))));
        await expect(saving).rejects.toThrow();
        expect(state().projects).toEqual([]);
    });
});
