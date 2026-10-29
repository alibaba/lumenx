// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API_URL } from "@/lib/apiUrl";
import { atelierProjectApi } from "@/lib/atelierApi";
import atelierProjectFixture from "./fixtures/atelier-project.json";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const STORAGE_B = "22222222-2222-4222-8222-222222222222";
const SHARED_PROJECT = atelierProjectFixture as Record<string, unknown>;

function jsonResponse(body: unknown, init: ResponseInit = {}) {
    return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
        ...init,
    });
}

function project(overrides: Record<string, unknown> = {}) {
    return {
        id: "atelier-1",
        title: "Atelier One",
        description: "Draft space",
        source_project_id: "studio-1",
        nodes: [],
        agent_policy: {
            approval_mode: "untrusted",
            allowed_tools: [],
            max_nodes_per_action: 8,
            max_tokens_per_turn: 2048,
            enable_reviewer: false,
            max_generation_cost_per_turn: 5.0,
            updated_at: 10,
        },
        agent_turns: [],
        sequence: [],
        audio_tracks: [],
        exports: [],
        agent_compaction_summary: null,
        compacted_through_turn_id: null,
        revision: 0,
        created_at: 10,
        updated_at: 10,
        ...overrides,
    };
}

function backendShapedProject(overrides: Record<string, unknown> = {}) {
    return { ...SHARED_PROJECT, ...overrides };
}


describe("atelier project API client", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
    });

    it("lists and gets committed projects through the protected app-access transport", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse([project(), project({ id: "atelier-2", revision: 3 })]))
            .mockResolvedValueOnce(jsonResponse(project({ id: "atelier-2", revision: 3 })));
        vi.stubGlobal("fetch", fetchMock);

        const listed = await atelierProjectApi.listProjects();
        const fetched = await atelierProjectApi.getProject("atelier-2");

        expect(listed.map(p => [p.id, p.revision])).toEqual([["atelier-1", 0], ["atelier-2", 3]]);
        expect(fetched.id).toBe("atelier-2");
        expect(fetchMock).toHaveBeenNthCalledWith(1, `${API_URL}/atelier/projects`, expect.objectContaining({
            method: "GET",
            credentials: "include",
            cache: "no-store",
        }));
        expect(fetchMock).toHaveBeenNthCalledWith(2, `${API_URL}/atelier/projects/atelier-2`, expect.objectContaining({
            method: "GET",
            credentials: "include",
            cache: "no-store",
        }));
        expect((fetchMock.mock.calls[0][1].headers as Headers).get("Accept")).toBe("application/json");
        expect((fetchMock.mock.calls[0][1].headers as Headers).get("X-LumenX-Environment")).toBe(STORAGE_A);
    });

    it("preserves backend-shaped project policy, graph, timeline, export, and opaque agent turn fields", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(backendShapedProject()));
        vi.stubGlobal("fetch", fetchMock);

        const fetched = await atelierProjectApi.getProject("atelier-old-1");

        expect(SHARED_PROJECT).not.toHaveProperty("candidate_judgments");
        expect(fetched).toEqual({
            ...SHARED_PROJECT,
            candidate_judgments: [],
            node_upload_receipts: [],
            sequence_export_jobs: [],
            generation_attempts: [],
            generation_request_receipts: [],
            exports: atelierProjectFixture.exports.map(record => ({
                ...record,
                job_id: null,
                publication_proof: null,
                snapshot_digest: null,
                deletion: null,
            })),
        });
        expect(fetched.agent_policy.max_tokens_per_turn).toBe(4096);
        expect(fetched.agent_policy.enable_reviewer).toBe(true);
        expect(fetched.agent_policy.max_generation_cost_per_turn).toBe(3.5);
        expect(fetched.agent_policy.updated_at).toBe(1002);
        expect(fetched.agent_turns[0]).toEqual((SHARED_PROJECT.agent_turns as unknown[])[0]);
    });

    it("rejects unsafe revisions, empty project IDs, and invalid update inputs before fetch", async () => {
        const fetchMock = vi.fn();
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierProjectApi.getProject("")).rejects.toMatchObject({ code: "invalid_atelier_project_id" });
        await expect(atelierProjectApi.updateProject("atelier-1", { expectedRevision: Number.MAX_SAFE_INTEGER + 1, title: "Unsafe" }))
            .rejects.toMatchObject({ code: "invalid_atelier_project_revision" });
        await expect(atelierProjectApi.deleteProject("atelier-1", { expectedRevision: -1 }))
            .rejects.toMatchObject({ code: "invalid_atelier_project_revision" });
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it("rejects unsafe ACK revisions before accepting a project", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(project({ revision: Number.MAX_SAFE_INTEGER + 1 })));
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "invalid_atelier_project" });
    });

    it("rejects cross-project graph owners plus duplicate and empty node ids", async () => {
        const malformed = vi.fn()
            .mockResolvedValueOnce(jsonResponse(backendShapedProject({ nodes: [{ ...(backendShapedProject().nodes as Record<string, unknown>[])[0], project_id: "other" }] })))
            .mockResolvedValueOnce(jsonResponse(backendShapedProject({ exports: [{ ...(backendShapedProject().exports as Record<string, unknown>[])[0], project_id: "other" }] })))
            .mockResolvedValueOnce(jsonResponse(backendShapedProject({ nodes: [
                (backendShapedProject().nodes as Record<string, unknown>[])[0],
                { ...(backendShapedProject().nodes as Record<string, unknown>[])[0] },
            ] })))
            .mockResolvedValueOnce(jsonResponse(backendShapedProject({ nodes: [{ ...(backendShapedProject().nodes as Record<string, unknown>[])[0], id: "" }] })));
        vi.stubGlobal("fetch", malformed);

        await expect(atelierProjectApi.getProject("atelier-old-1")).rejects.toMatchObject({ code: "atelier_project_graph_owner_mismatch" });
        await expect(atelierProjectApi.getProject("atelier-old-1")).rejects.toMatchObject({ code: "atelier_project_graph_owner_mismatch" });
        await expect(atelierProjectApi.getProject("atelier-old-1")).rejects.toMatchObject({ code: "atelier_project_duplicate_node_id" });
        await expect(atelierProjectApi.getProject("atelier-old-1")).rejects.toMatchObject({ code: "atelier_project_empty_node_id" });
    });



    it("rejects duplicate project ids in lists and invalid opaque agent turn ownership", async () => {
        const malformed = vi.fn()
            .mockResolvedValueOnce(jsonResponse([backendShapedProject(), backendShapedProject()]))
            .mockResolvedValueOnce(jsonResponse(backendShapedProject({ agent_turns: [{ ...(SHARED_PROJECT.agent_turns as Record<string, unknown>[])[0], project_id: "other" }] })))
            .mockResolvedValueOnce(jsonResponse(backendShapedProject({ agent_turns: [
                (SHARED_PROJECT.agent_turns as Record<string, unknown>[])[0],
                { ...(SHARED_PROJECT.agent_turns as Record<string, unknown>[])[0] },
            ] })))
            .mockResolvedValueOnce(jsonResponse(backendShapedProject({ agent_turns: [{ ...(SHARED_PROJECT.agent_turns as Record<string, unknown>[])[0], id: "" }] })));
        vi.stubGlobal("fetch", malformed);

        await expect(atelierProjectApi.listProjects()).rejects.toMatchObject({ code: "atelier_project_duplicate_project_id" });
        await expect(atelierProjectApi.getProject("atelier-old-1")).rejects.toMatchObject({ code: "atelier_project_agent_turn_owner_mismatch" });
        await expect(atelierProjectApi.getProject("atelier-old-1")).rejects.toMatchObject({ code: "atelier_project_duplicate_agent_turn_id" });
        await expect(atelierProjectApi.getProject("atelier-old-1")).rejects.toMatchObject({ code: "atelier_project_empty_agent_turn_id" });
    });

    it("creates a project without inventing source_project_id when the source is omitted", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(project({ revision: 0, source_project_id: null })));
        vi.stubGlobal("fetch", fetchMock);

        const created = await atelierProjectApi.createProject({ title: "Atelier One", description: "Draft space" });

        expect(created.source_project_id).toBeNull();
        expect(created.revision).toBe(0);
        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
            title: "Atelier One",
            description: "Draft space",
        });
    });

    it("updates with expected_revision and preserves explicit null source", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(project({
            title: "Renamed",
            source_project_id: null,
            revision: 2,
        })));
        vi.stubGlobal("fetch", fetchMock);

        const updated = await atelierProjectApi.updateProject("atelier-1", {
            expectedRevision: 1,
            title: "Renamed",
            sourceProjectId: null,
        });

        expect(updated.revision).toBe(2);
        expect(updated.source_project_id).toBeNull();
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/atelier/projects/atelier-1`, expect.objectContaining({
            method: "PUT",
            credentials: "include",
        }));
        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
            expected_revision: 1,
            title: "Renamed",
            source_project_id: null,
        });
    });

    it("omits patch fields that were not provided", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse(project({ revision: 1 })));
        vi.stubGlobal("fetch", fetchMock);

        await atelierProjectApi.updateProject("atelier-1", { expectedRevision: 0, description: "Only desc" });

        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
            expected_revision: 0,
            description: "Only desc",
        });
    });

    it("deletes only when the backend confirms strict ok true", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
        vi.stubGlobal("fetch", fetchMock);

        const result = await atelierProjectApi.deleteProject("atelier-1", { expectedRevision: 2 });

        expect(result).toEqual({ ok: true });
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/atelier/projects/atelier-1`, expect.objectContaining({
            method: "DELETE",
            credentials: "include",
        }));
        expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ expected_revision: 2 });
    });

    it("rejects malformed ACKs, id mismatches, revision rollbacks, and weak delete confirms", async () => {
        const malformed = vi.fn()
            .mockResolvedValueOnce(jsonResponse(project({ revision: undefined })))
            .mockResolvedValueOnce(jsonResponse(project({ id: "other", revision: 1 })))
            .mockResolvedValueOnce(jsonResponse(project({ revision: 1 })))
            .mockResolvedValueOnce(jsonResponse({ ok: "true" }));
        vi.stubGlobal("fetch", malformed);

        await expect(atelierProjectApi.createProject({ title: "Bad" })).rejects.toMatchObject({ code: "invalid_atelier_project" });
        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "atelier_project_identity_mismatch" });
        await expect(atelierProjectApi.updateProject("atelier-1", { expectedRevision: 2, title: "Rollback" })).rejects.toMatchObject({ code: "atelier_project_revision_rollback" });
        await expect(atelierProjectApi.deleteProject("atelier-1", { expectedRevision: 1 })).rejects.toMatchObject({ code: "invalid_atelier_project_delete_ack" });
    });

    it("surfaces service errors without fabricating a project", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "Atelier project changed; reload before saving" }, { status: 409 }));
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierProjectApi.updateProject("atelier-1", { expectedRevision: 0, title: "Late" }))
            .rejects.toMatchObject({ status: 409, message: "Atelier project changed; reload before saving" });
    });


    it("rejects an ACK if the environment changes while parsing the response body", async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            status: 200,
            json: async () => {
                activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
                return project();
            },
        } as Response);
        vi.stubGlobal("fetch", fetchMock);

        await expect(atelierProjectApi.getProject("atelier-1")).rejects.toMatchObject({ code: "environment_changed" });
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("rejects stale A-B-A project ACKs and never replays the request", async () => {
        let releaseFetch!: (response: Response) => void;
        const fetchMock = vi.fn().mockReturnValue(new Promise<Response>(resolve => {
            releaseFetch = resolve;
        }));
        vi.stubGlobal("fetch", fetchMock);

        const pending = atelierProjectApi.getProject("atelier-1");
        activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
        releaseFetch(jsonResponse(project()));

        await expect(pending).rejects.toMatchObject({ code: "environment_changed" });
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });
});
