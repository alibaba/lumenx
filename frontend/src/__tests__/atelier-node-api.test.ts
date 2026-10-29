// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API_URL } from "@/lib/apiUrl";
import { atelierNodeApi } from "@/lib/atelierNodeApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import atelierProjectFixture from "./fixtures/atelier-project.json";

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

function project(overrides: Record<string, unknown> = {}) {
    const projectId = typeof overrides.id === "string" ? overrides.id : "atelier-1";
    const baseNodes = (overrides.nodes as Record<string, unknown>[] | undefined) ?? [node({ project_id: projectId })];
    const restOverrides = { ...overrides };
    delete restOverrides.id;
    delete restOverrides.nodes;
    delete restOverrides.agent_turns;
    delete restOverrides.exports;
    return {
        ...BASE_PROJECT,
        id: projectId,
        title: "Canvas",
        nodes: baseNodes.map(item => ({ ...item, project_id: projectId })),
        agent_turns: (BASE_PROJECT.agent_turns as Record<string, unknown>[]).map(item => ({ ...item, project_id: projectId })),
        exports: (BASE_PROJECT.exports as Record<string, unknown>[]).map(item => ({ ...item, project_id: projectId })),
        revision: 1,
        ...restOverrides,
    };
}

function ack(overrides: Record<string, unknown> = {}) {
    return {
        project: project(),
        node_ids: ["node-1"],
        ...overrides,
    };
}

describe("atelier node API client", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
    });

    it("creates a node through protected transport and validates the created node ACK", async () => {
        const created = node({ id: "created-1", type: "comment", prompt: "Body", data: { body: "Body" }, x: 1, y: 0, width: 0.5, height: 2 });
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(ack({
            project: project({ nodes: [created], revision: 1 }),
            node_ids: ["created-1"],
        })));
        vi.stubGlobal("fetch", fetchMock);

        const result = await atelierNodeApi.createNode("atelier-1", {
            expectedRevision: 0,
            type: "comment",
            body: "Body",
            x: 1,
            y: 0,
            width: 0.5,
            height: 2,
        });

        expect(result.node_ids).toEqual(["created-1"]);
        expect(result.project.nodes[0].type).toBe("comment");
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/atelier/projects/atelier-1/nodes`, expect.objectContaining({
            method: "POST",
            credentials: "include",
            cache: "no-store",
        }));
        expect((fetchMock.mock.calls[0][1].headers as Headers).get("X-LumenX-Environment")).toBe(STORAGE_A);
        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
            expected_revision: 0,
            type: "comment",
            body: "Body",
            x: 1,
            y: 0,
            width: 0.5,
            height: 2,
        });
    });

    it("creates a region preserving explicit false and empty appearance fields", async () => {
        const region = node({ id: "region-1", type: "region", data: { color: "", collapsed: false } });
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(ack({
            project: project({ nodes: [node(), region], revision: 1 }),
            node_ids: ["region-1"],
        })));
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierNodeApi.createNode("atelier-1", {
            expectedRevision: 0,
            type: "region",
            color: "",
            collapsed: false,
        })).resolves.toMatchObject({ node_ids: ["region-1"] });

        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
            expected_revision: 0,
            type: "region",
            color: "",
            collapsed: false,
        });
    });

    it("updates text and body with exact node ACK identity", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ revision: 2, nodes: [node({ title: "Next", prompt: "P" })] }) })))
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ revision: 3, nodes: [node({ prompt: "Body", data: { body: "Body" } })] }) })));
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierNodeApi.updateNodeText("atelier-1", "node-1", { expectedRevision: 1, title: "Next", prompt: "P" }))
            .resolves.toMatchObject({ node_ids: ["node-1"] });
        await expect(atelierNodeApi.updateNodeBody("atelier-1", "node-1", { expectedRevision: 2, body: "Body" }))
            .resolves.toMatchObject({ node_ids: ["node-1"] });

        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ expected_revision: 1, title: "Next", prompt: "P" });
        expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual({ expected_revision: 2, body: "Body" });
    });

    it("updates layout preserving explicit null regionId and rejects stale A-B-A ACK", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ revision: 4, nodes: [node({ data: {} })] }) })))
            .mockImplementationOnce(() => new Promise<Response>(resolve => {
                activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
                activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
                resolve(jsonResponse(ack({ project: project({ revision: 5 }) })));
            }));
        vi.stubGlobal("fetch", fetchMock);

        await atelierNodeApi.updateNodeLayout("atelier-1", "node-1", { expectedRevision: 3, x: 0, y: 2, regionId: null });
        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ expected_revision: 3, x: 0, y: 2, region_id: null });

        await expect(atelierNodeApi.updateNodeLayout("atelier-1", "node-1", { expectedRevision: 4, x: 5 }))
            .rejects.toMatchObject({ code: "environment_changed" });
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it("updates batch layout and move with ordered target ACK validation", async () => {
        const nodes = [node({ id: "a" }), node({ id: "b" })];
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ revision: 2, nodes }), node_ids: ["a", "b"] })))
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ revision: 3, nodes }), node_ids: ["a", "b"] })));
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierNodeApi.updateNodesLayout("atelier-1", {
            expectedRevision: 1,
            updates: [{ nodeId: "a", x: 0 }, { nodeId: "b", regionId: null }],
        })).resolves.toMatchObject({ node_ids: ["a", "b"] });
        await expect(atelierNodeApi.moveNodes("atelier-1", { expectedRevision: 2, nodeIds: ["a", "b"], dx: 0, dy: -1 }))
            .resolves.toMatchObject({ node_ids: ["a", "b"] });

        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ expected_revision: 1, updates: [{ node_id: "a", x: 0 }, { node_id: "b", region_id: null }] });
        expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual({ expected_revision: 2, node_ids: ["a", "b"], dx: 0, dy: -1 });
    });

    it("supports region wrap, empty member clearing, and region appearance", async () => {
        const region = node({ id: "region-1", type: "region", data: { color: "#111", collapsed: false } });
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ revision: 2, nodes: [node(), region] }), node_ids: ["region-1"] })))
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ revision: 3, nodes: [node(), region] }), node_ids: ["region-1"] })))
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ revision: 4, nodes: [node(), region] }), node_ids: ["region-1"] })));
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierNodeApi.wrapRegion("atelier-1", { expectedRevision: 1, title: "Group", memberNodeIds: ["node-1"], collapsed: false }))
            .resolves.toMatchObject({ node_ids: ["region-1"] });
        await expect(atelierNodeApi.setRegionMembers("atelier-1", "region-1", { expectedRevision: 2, memberNodeIds: [] }))
            .resolves.toMatchObject({ node_ids: ["region-1"] });
        await expect(atelierNodeApi.updateRegionAppearance("atelier-1", "region-1", { expectedRevision: 3, color: "", collapsed: false }))
            .resolves.toMatchObject({ node_ids: ["region-1"] });

        expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual({ expected_revision: 2, member_node_ids: [] });
        expect(JSON.parse(fetchMock.mock.calls[2][1].body as string)).toEqual({ expected_revision: 3, color: "", collapsed: false });
    });

    it("rejects invalid local inputs before fetch", async () => {
        const fetchMock = vi.fn();
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierNodeApi.createNode("", { expectedRevision: 0 })).rejects.toMatchObject({ code: "invalid_atelier_project_id" });
        await expect(atelierNodeApi.updateNodeText("atelier-1", "", { expectedRevision: 0, title: "x" })).rejects.toMatchObject({ code: "invalid_atelier_node_id" });
        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: Number.MAX_SAFE_INTEGER + 1 })).rejects.toMatchObject({ code: "invalid_atelier_project_revision" });
        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0, type: "comment", collapsed: false })).rejects.toMatchObject({ code: "invalid_atelier_node_create_fields" });
        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0, type: "idea", color: "#fff" })).rejects.toMatchObject({ code: "invalid_atelier_node_create_fields" });
        await expect(atelierNodeApi.updateNodesLayout("atelier-1", { expectedRevision: 0, updates: [{ nodeId: "a" }, { nodeId: "a" }] })).rejects.toMatchObject({ code: "invalid_atelier_node_ids" });
        await expect(atelierNodeApi.moveNodes("atelier-1", { expectedRevision: 0, nodeIds: ["a", ""], dx: 0, dy: 0 })).rejects.toMatchObject({ code: "invalid_atelier_node_ids" });
        await expect(atelierNodeApi.setRegionMembers("atelier-1", "region-1", { expectedRevision: 0, memberNodeIds: [""] })).rejects.toMatchObject({ code: "invalid_atelier_node_ids" });
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("rejects malformed ACKs without fabricating a project", async () => {
        const malformed = vi.fn()
            .mockResolvedValueOnce(jsonResponse({ node_ids: ["node-1"] }))
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ id: "other" }) })))
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ revision: Number.MAX_SAFE_INTEGER + 1 }) })))
            .mockResolvedValueOnce(jsonResponse(ack({ node_ids: [] })))
            .mockResolvedValueOnce(jsonResponse(ack({ node_ids: ["node-1", "node-1"] })))
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ nodes: [] }), node_ids: ["missing"] })))
            .mockResolvedValueOnce(jsonResponse(ack({ project: project({ nodes: [node({ id: "other" })] }), node_ids: ["other"] })));
        vi.stubGlobal("fetch", malformed);

        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0 })).rejects.toMatchObject({ code: "invalid_atelier_node_ack" });
        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0 })).rejects.toMatchObject({ code: "atelier_project_identity_mismatch" });
        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0 })).rejects.toMatchObject({ code: "invalid_atelier_project" });
        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0 })).rejects.toMatchObject({ code: "atelier_node_ack_empty" });
        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0 })).rejects.toMatchObject({ code: "atelier_node_ack_duplicate" });
        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0 })).rejects.toMatchObject({ code: "atelier_node_ack_missing_target" });
        await expect(atelierNodeApi.updateNodeText("atelier-1", "node-1", { expectedRevision: 0, title: "x" })).rejects.toMatchObject({ code: "atelier_node_ack_target_mismatch" });
    });

    it("surfaces HTTP errors and rejects stale responses during body parsing", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse({ detail: "bad node" }, { status: 422 }))
            .mockResolvedValueOnce({
                ok: true,
                status: 200,
                json: async () => {
                    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
                    return ack({ project: project({ revision: 1 }) });
                },
            } as Response);
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0 })).rejects.toMatchObject({ status: 422, message: "bad node" });
        await expect(atelierNodeApi.createNode("atelier-1", { expectedRevision: 0 })).rejects.toMatchObject({ code: "environment_changed" });
    });
});
