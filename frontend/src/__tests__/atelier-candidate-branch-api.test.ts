import { afterEach, describe, expect, it, vi } from "vitest";
import { API_URL } from "@/lib/apiUrl";
import { parseAtelierProject } from "@/lib/atelierApi";
import { atelierCandidateBranchApi } from "@/lib/atelierCandidateBranchApi";
import selectedFixture from "./fixtures/atelier-generation-selected.json";
import type { AtelierNode, AtelierProject } from "@/lib/atelierTypes";

const PROJECT_ID = "project-1";
const NODE_ID = "video";
const CANDIDATE_ID = "candidate-1";
const HASH = "a".repeat(64);

const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
const beforeProject = (): AtelierProject => parseAtelierProject(structuredClone(selectedFixture.project));
const branchSource = (patch: Record<string, unknown> = {}) => ({
    schema_version: 1,
    origin_project_id: PROJECT_ID,
    origin_node_id: NODE_ID,
    origin_candidate_id: CANDIDATE_ID,
    media_kind: "video",
    media_url: "uploads/branch-candidate-1.mp4",
    media_sha256: HASH,
    media_size_bytes: 1234,
    recipe_status: "available",
    ...patch,
});
const branchNode = (patch: Partial<AtelierNode> = {}): AtelierNode => ({
    id: "branch-1", project_id: PROJECT_ID, type: "video", title: "Branch take", prompt: "Frozen prompt", status: "draft",
    x: 420, y: 0, width: 320, height: 180, source_project_id: null, frame_id: null, asset_id: null, video_task_id: null,
    media_urls: [], data: { branch_source: branchSource(), generation_draft: { schema_version: 1, model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: {} } },
    created_by: "user", created_at: 30, updated_at: 30, ...patch,
});
const referenceNode = (patch: Partial<AtelierNode> = {}): AtelierNode => ({
    id: "ref-1", project_id: PROJECT_ID, type: "image", title: "Materialized ref", prompt: "", status: "completed",
    x: 380, y: 220, width: 160, height: 120, source_project_id: null, frame_id: null, asset_id: null, video_task_id: null,
    media_urls: ["uploads/ref-1.png"], data: {}, created_by: "user", created_at: 31, updated_at: 31, ...patch,
});
const ack = (projectPatch: Partial<AtelierProject> = {}, nodeIds: string[] = ["branch-1"]) => {
    const before = beforeProject();
    const project = { ...before, revision: 3, nodes: [...before.nodes, branchNode()], ...projectPatch };
    return { project, node_ids: nodeIds };
};

afterEach(() => vi.unstubAllGlobals());

describe("Atelier candidate branch API", () => {
    it("posts only expected_revision and accepts a strict branch ACK", async () => {
        const fetch = vi.fn().mockResolvedValue(response(ack()));
        vi.stubGlobal("fetch", fetch);

        const result = await atelierCandidateBranchApi.branchCandidate(PROJECT_ID, NODE_ID, CANDIDATE_ID, { expected_revision: 2 }, beforeProject());

        expect(result.node_ids).toEqual(["branch-1"]);
        expect(result.project.nodes.at(-1)?.data.branch_source).toEqual(branchSource());
        expect(fetch).toHaveBeenCalledWith(`${API_URL}/atelier/projects/project-1/nodes/video/candidates/candidate-1/branch`, expect.objectContaining({
            method: "POST",
            body: JSON.stringify({ expected_revision: 2 }),
        }));
    });

    it("rejects loose request bodies and malformed branch ACKs", async () => {
        const before = beforeProject();
        await expect(atelierCandidateBranchApi.branchCandidate(PROJECT_ID, NODE_ID, CANDIDATE_ID, { expected_revision: 2, extra: true } as never, before))
            .rejects.toMatchObject({ code: "invalid_atelier_candidate_branch_request" });

        vi.stubGlobal("fetch", vi.fn()
            .mockResolvedValueOnce(response(ack({ revision: 2 })))
            .mockResolvedValueOnce(response(ack({}, ["missing"])))
            .mockResolvedValueOnce(response(ack({}, [NODE_ID])))
            .mockResolvedValueOnce(response(ack({ nodes: [...before.nodes, branchNode(), referenceNode()] }, ["branch-1"])))
            .mockResolvedValueOnce(response(ack({ nodes: [...before.nodes.map(node => node.id === "image" ? { ...node, title: "Changed" } : node), branchNode()] })))
            .mockResolvedValueOnce(response(ack({ generation_attempts: [] })))
            .mockResolvedValueOnce(response(ack({ exports: [{ ...(before.exports[0] ?? {}), id: "extra-export", project_id: PROJECT_ID }] as never })))
            .mockResolvedValueOnce(response(ack({ nodes: [...before.nodes, branchNode({ data: { branch_source: branchSource({ origin_node_id: "other" }) } })] })))
            .mockResolvedValueOnce(response(ack({ nodes: [...before.nodes, { ...branchNode(), media_urls: ["video/selected.mp4"] }] })))
            .mockResolvedValueOnce(response(ack({ nodes: [...before.nodes, branchNode({ data: { branch_source: branchSource({ recipe_status: "unavailable_legacy" }), generation_draft: { schema_version: 1 } } })] })))
            .mockResolvedValueOnce(response(ack({ nodes: [...before.nodes, branchNode(), referenceNode({ status: "draft" })] }, ["branch-1", "ref-1"]))));

        for (let index = 0; index < 11; index += 1) {
            await expect(atelierCandidateBranchApi.branchCandidate(PROJECT_ID, NODE_ID, CANDIDATE_ID, { expected_revision: 2 }, before)).rejects.toThrow();
        }
    });

    it("parses source-only branch_source but rejects authority and unsafe media paths", () => {
        const raw = structuredClone(selectedFixture.project) as Record<string, unknown>;
        const node = (raw.nodes as Record<string, unknown>[]).find(item => item.id === NODE_ID)!;
        const originalData = structuredClone(node.data) as Record<string, unknown>;
        node.data = { ...originalData, branch_source: branchSource({ recipe_status: "unavailable_imported" }) };
        expect(parseAtelierProject(raw).nodes.find(item => item.id === NODE_ID)?.data.branch_source).toEqual(branchSource({ recipe_status: "unavailable_imported" }));
        node.data = { ...originalData, branch_source: branchSource({ authority: "provider" }) };
        expect(() => parseAtelierProject(raw)).toThrow();
        node.data = { ...originalData, branch_source: branchSource({ media_url: "video/secret.mp4" }) };
        expect(() => parseAtelierProject(raw)).toThrow();
    });
});
