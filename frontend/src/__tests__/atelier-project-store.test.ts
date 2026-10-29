// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAtelierStore } from "@/store/atelierStore";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project.json";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const state = () => useAtelierStore.getState();
function project(id = "a", revision = 0) {
    const p = structuredClone(fixture);
    return { ...p, id, revision, title: `${id}-${revision}`,
        nodes: p.nodes.map(n => ({ ...n, project_id: id })),
        agent_turns: p.agent_turns.map(t => ({ ...t, project_id: id })),
        exports: p.exports.map(e => ({ ...e, project_id: id })),
    };
}
function response(value: unknown, status = 200) {
    return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(r => { resolve = r; });
    return { promise, resolve };
}
function mockFetch() {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    return fetch;
}

beforeEach(() => {
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
});

describe("Atelier project state uses confirmed product snapshots", () => {
    it("does not create a project when the initial list fails", async () => {
        const fetch = mockFetch().mockResolvedValue(response({ detail: "Disk unavailable" }, 503));
        await expect(state().ensureProject()).rejects.toThrow("Disk unavailable");
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(fetch.mock.calls[0][1].method).toBe("GET");
        expect(state().currentProject).toBeNull();
        expect(state().error).toContain("Disk unavailable");
    });

    it("coalesces bootstrap and creates only after a confirmed empty list", async () => {
        const list = deferred<Response>();
        const fetch = mockFetch().mockReturnValueOnce(list.promise).mockResolvedValueOnce(response(project()));
        const first = state().ensureProject();
        const second = state().ensureProject();
        list.resolve(response([]));
        await expect(first).resolves.toMatchObject({ id: "a" });
        await expect(second).resolves.toMatchObject({ id: "a" });
        expect(fetch).toHaveBeenCalledTimes(2);
        expect(fetch.mock.calls[1][1].method).toBe("POST");
        expect(state().projects.map(p => p.id)).toEqual(["a"]);
    });

    it("retains confirmed state and rejects a failed list without manufacturing an empty project", async () => {
        const fetch = mockFetch().mockResolvedValueOnce(response([project()]));
        await state().loadProjects();
        fetch.mockResolvedValueOnce(response({ detail: "Read failed" }, 503));
        await expect(state().loadProjects()).rejects.toThrow("Read failed");
        expect(state().currentProject?.id).toBe("a");
        expect(state().projects).toHaveLength(1);
        expect(state().isLoading).toBe(false);
    });

    it("deduplicates one pending create and never retries a lost create response", async () => {
        const pending = deferred<Response>();
        const fetch = mockFetch().mockReturnValueOnce(pending.promise);
        const first = state().createProject("New");
        const second = state().createProject("New");
        const failures = Promise.allSettled([first, second]);
        pending.resolve(response({ detail: "Unknown commit" }, 503));
        expect((await failures).every(r => r.status === "rejected")).toBe(true);
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(state().currentProject).toBeNull();
        expect(state().projects).toEqual([]);
    });

    it("rejects old navigation results even after A to B to A", async () => {
        const oldA = deferred<Response>();
        const latestA = deferred<Response>();
        mockFetch().mockReturnValueOnce(oldA.promise)
            .mockResolvedValueOnce(response(project("b"))).mockReturnValueOnce(latestA.promise);
        const first = state().switchProject("a");
        const firstResult = Promise.allSettled([first]);
        await state().switchProject("b");
        const latest = state().switchProject("a");
        oldA.resolve(response(project("a", 9)));
        await firstResult;
        expect(state().isLoading).toBe(true);
        latestA.resolve(response(project("a", 2)));
        await latest;
        expect(state().currentProject?.revision).toBe(2);
        expect(state().isLoading).toBe(false);
    });

    it("clears state on environment change and rejects late responses across A to B to A", async () => {
        const old = deferred<Response>();
        const fetch = mockFetch().mockResolvedValueOnce(response([project()]));
        await state().loadProjects();
        fetch.mockReturnValueOnce(old.promise);
        const result = Promise.allSettled([state().refreshProject()]);
        activateRuntimeEnvironment({ schema_version: 1, storage_id: B });
        expect(state().projects).toEqual([]);
        expect(state().currentProject).toBeNull();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: A });
        old.resolve(response(project("a", 20)));
        expect((await result)[0].status).toBe("rejected");
        expect(state().projects).toEqual([]);
        expect(state().error).toBeNull();
    });

    it("does not let a slow list erase a newly confirmed create", async () => {
        const oldList = deferred<Response>();
        mockFetch().mockReturnValueOnce(oldList.promise).mockResolvedValueOnce(response(project("new")));
        const listing = state().loadProjects();
        await state().createProject("New");
        oldList.resolve(response([]));
        await listing;
        expect(state().projects.map(p => p.id)).toEqual(["new"]);
        expect(state().currentProject?.id).toBe("new");
    });

    it("uses saved revisions, accepts full update ACKs and rejects older readback", async () => {
        const oldRead = deferred<Response>();
        const fetch = mockFetch().mockResolvedValueOnce(response([project("a", 4)]));
        await state().loadProjects();
        state().selectNode(fixture.nodes[0].id);
        fetch.mockReturnValueOnce(oldRead.promise).mockResolvedValueOnce(response(project("a", 5)));
        const refreshing = state().refreshProject();
        await state().updateProject({ title: "Edited" });
        expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ expected_revision: 4, title: "Edited" });
        oldRead.resolve(response(project("a", 3)));
        await refreshing;
        expect(state().currentProject?.revision).toBe(5);
        expect(state().currentProject?.nodes[0].data).toEqual(fixture.nodes[0].data);
        expect(state().selectedNodeId).toBe(fixture.nodes[0].id);
    });

    it("blocks writes after failure until an explicit fresh read succeeds", async () => {
        const fetch = mockFetch().mockResolvedValueOnce(response([project("a", 3)]));
        await state().loadProjects();
        fetch.mockResolvedValueOnce(response({ detail: "Revision conflict" }, 409));
        await expect(state().updateProject({ title: "unsaved" })).rejects.toThrow("Revision conflict");
        expect(state().currentProject?.title).toBe("a-3");
        expect(state().needsRefresh).toBe(true);
        await expect(state().updateProject({ title: "not sent" })).rejects.toThrow();
        expect(fetch).toHaveBeenCalledTimes(2);
        fetch.mockResolvedValueOnce(response(project("a", 4))).mockResolvedValueOnce(response(project("a", 5)));
        await state().refreshProject();
        expect(state().needsRefresh).toBe(false);
        await state().updateProject({ title: "saved" });
        expect(state().currentProject?.revision).toBe(5);
    });

    it("does not clear a write failure with a read that started before it", async () => {
        const oldRead = deferred<Response>();
        const fetch = mockFetch().mockResolvedValueOnce(response([project()]));
        await state().loadProjects();
        fetch.mockReturnValueOnce(oldRead.promise).mockResolvedValueOnce(response({ detail: "Failed" }, 503));
        const reading = state().refreshProject();
        await expect(state().updateProject({ title: "unsaved" })).rejects.toThrow();
        oldRead.resolve(response(project()));
        await reading;
        expect(state().needsRefresh).toBe(true);
        expect(state().error).toContain("Failed");
    });

    it("removes a project only after confirmed deletion and ignores pre-delete lists", async () => {
        const oldList = deferred<Response>();
        const fetch = mockFetch().mockResolvedValueOnce(response([project()]));
        await state().loadProjects();
        fetch.mockResolvedValueOnce(response({ detail: "Delete failed" }, 503));
        await expect(state().deleteProject()).rejects.toThrow("Delete failed");
        expect(state().currentProject?.id).toBe("a");
        fetch.mockResolvedValueOnce(response(project()));
        await state().refreshProject();
        fetch.mockReturnValueOnce(oldList.promise).mockResolvedValueOnce(response({ ok: true }));
        const listing = state().loadProjects();
        await state().deleteProject();
        oldList.resolve(response([project()]));
        await listing;
        expect(state().currentProject).toBeNull();
        expect(state().projects).toEqual([]);
        expect(state().selectedNodeId).toBeNull();
    });

    it("records an offscreen confirmed write without changing the selected project", async () => {
        const saving = deferred<Response>();
        const fetch = mockFetch().mockResolvedValueOnce(response([project("a"), project("b")]));
        await state().loadProjects();
        fetch.mockReturnValueOnce(saving.promise).mockResolvedValueOnce(response(project("b")));
        const write = state().updateProject({ title: "Saved A" });
        await state().switchProject("b");
        saving.resolve(response(project("a", 1)));
        await write;
        expect(state().currentProject?.id).toBe("b");
        expect(state().projects.find(p => p.id === "a")?.revision).toBe(1);
        expect(state().isSaving).toBe(false);
    });

    it("settles an in-flight write after navigation away and back without leaving saving stuck", async () => {
        const saving = deferred<Response>();
        const fetch = mockFetch().mockResolvedValueOnce(response([project("a"), project("b")]));
        await state().loadProjects();
        fetch.mockReturnValueOnce(saving.promise).mockResolvedValueOnce(response(project("b")))
            .mockResolvedValueOnce(response(project("a")));
        const write = state().updateProject({ title: "Saved A" });
        await state().switchProject("b");
        await state().switchProject("a");
        expect(state().isSaving).toBe(true);
        saving.resolve(response(project("a", 1)));
        await write;
        expect(state().isSaving).toBe(false);
        expect(state().currentProject?.revision).toBe(1);
    });

    it("does not clear another project's selection when an offscreen delete finishes", async () => {
        const deleting = deferred<Response>();
        const fetch = mockFetch().mockResolvedValueOnce(response([project("a"), project("b")]));
        await state().loadProjects();
        fetch.mockReturnValueOnce(deleting.promise).mockResolvedValueOnce(response(project("b")));
        const write = state().deleteProject();
        await state().switchProject("b");
        deleting.resolve(response({ ok: true }));
        await write;
        expect(state().currentProject?.id).toBe("b");
        expect(state().projects.map(p => p.id)).toEqual(["b"]);
    });


    it("a refresh issued during creation cannot navigate back to the previous project", async () => {
        const creating = deferred<Response>();
        const oldRead = deferred<Response>();
        const fetch = mockFetch().mockResolvedValueOnce(response([project("a")]));
        await state().loadProjects();
        fetch.mockReturnValueOnce(creating.promise).mockReturnValueOnce(oldRead.promise);
        const create = state().createProject("New");
        const refresh = state().refreshProject();
        creating.resolve(response(project("new")));
        await create;
        oldRead.resolve(response(project("a")));
        await refresh;
        expect(state().currentProject?.id).toBe("new");
    });

    it("starts a fresh explicit list after navigation even if an old list is still pending", async () => {
        const oldList = deferred<Response>();
        const fetch = mockFetch().mockReturnValueOnce(oldList.promise)
            .mockResolvedValueOnce(response(project("b")))
            .mockResolvedValueOnce(response([project("b"), project("c")]));
        const old = state().loadProjects();
        await state().switchProject("b");
        const fresh = state().loadProjects();
        expect(fetch).toHaveBeenCalledTimes(3);
        await fresh;
        oldList.resolve(response([project("a")]));
        await old;
        expect(state().projects.map(p => p.id)).toEqual(["b", "c"]);
        expect(state().currentProject?.id).toBe("b");
    });

});
