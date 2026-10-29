// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL } from "@/lib/api";
import {
    activateRuntimeEnvironment,
    clearRuntimeEnvironment,
    environmentLocalStorageKey,
    getRuntimeEnvironment,
    isCurrentRuntimeEnvironment,
    runtimeEnvironmentKey,
    validateRuntimeStorageIdentity,
} from "@/lib/runtimeEnvironment";
import { activateProjectEnvironment, projectReadFailureKey, resetProjectStoreHydrationForTests, useProjectStore } from "@/store/projectStore";
import { scriptDraftKey, useScriptDraftStore } from "@/store/scriptDraftStore";

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const STORAGE_B = "22222222-2222-4222-8222-222222222222";
const STORAGE_C = "abcdef12-3456-4abc-8def-abcdef123456";
const ENV_A = JSON.stringify([API_URL, STORAGE_A]);
const ENV_B = JSON.stringify([API_URL, STORAGE_B]);

function project(id = "p", revision = 1) {
    return {
        id,
        revision,
        title: id,
        original_text: "text",
        created_at: 1,
        updated_at: 1,
        frames: [],
        video_tasks: [],
        scenes: [],
        props: [],
        characters: [],
        read_versions: { project: revision, series: null, library: 0 },
    };
}

async function activate(storageId: string) {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    const environment = getRuntimeEnvironment()!;
    await activateProjectEnvironment(environment.key, environment.generation);
}

beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    clearRuntimeEnvironment();
    resetProjectStoreHydrationForTests();
    useProjectStore.setState({
        projects: [],
        currentProject: null,
        activeEnvironmentKey: null,
        environments: {},
        readWatermarks: {},
        generatingTasks: [],
        runningOps: {},
        seriesList: [],
        currentSeries: null,
    });
    useScriptDraftStore.setState({ drafts: {} });
});

describe("runtime environment identity", () => {
    it("validates only canonical schema 1 UUIDv4 storage identities", () => {
        expect(validateRuntimeStorageIdentity({ schema_version: 1, storage_id: STORAGE_A })).toEqual({
            schema_version: 1,
            storage_id: STORAGE_A,
        });
        expect(() => validateRuntimeStorageIdentity({ schema_version: 1, storage_id: STORAGE_C.toUpperCase() })).toThrow();
        expect(() => validateRuntimeStorageIdentity({ schema_version: 1, storage_id: "11111111-1111-1111-1111-111111111111" })).toThrow();
        expect(() => validateRuntimeStorageIdentity({ schema_version: 2, storage_id: STORAGE_A })).toThrow();
    });

    it("keys browser state by API URL and storage identity without URL fallback", () => {
        expect(runtimeEnvironmentKey(API_URL, STORAGE_A)).toBe(ENV_A);
        activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
        const captured = getRuntimeEnvironment()!;
        expect(captured.key).toBe(ENV_A);
        expect(isCurrentRuntimeEnvironment(captured.key, captured.generation)).toBe(true);
        clearRuntimeEnvironment();
        expect(() => environmentLocalStorageKey("defaults")).toThrow(/runtime environment/i);
    });
});

describe("project cache environment buckets", () => {
    it("hydrates a persisted environment bucket without replacing it with the default empty live slice", async () => {
        const restored = {
            ...project("persisted", 9),
            originalText: "persisted text",
            createdAt: "old",
            updatedAt: "old",
            status: "draft",
            readEnvironment: ENV_A,
        };
        localStorage.setItem("project-storage", JSON.stringify({
            state: {
                activeEnvironmentKey: ENV_A,
                environments: {
                    [ENV_A]: {
                        projects: [restored],
                        currentProject: restored,
                        readWatermarks: { [ENV_A]: { project: { persisted: 9 }, series: {}, library: 0 } },
                        generatingTasks: [{ assetId: "asset-a", generationType: "full_body", batchSize: 1 }],
                    },
                },
            },
            version: 0,
        }));

        await activate(STORAGE_A);

        expect(useProjectStore.getState().currentProject?.id).toBe("persisted");
        expect(useProjectStore.getState().currentProject?.revision).toBe(9);
        expect(useProjectStore.getState().generatingTasks).toEqual([{ assetId: "asset-a", generationType: "full_body", batchSize: 1 }]);
        const persisted = JSON.parse(localStorage.getItem("project-storage")!);
        expect(persisted.state.environments[ENV_A].currentProject.id).toBe("persisted");
    });

    it("does not let an older hydration activation overwrite the newer active environment", async () => {
        let resolveHydrate!: () => void;
        const pendingHydrate = new Promise<void>(resolve => {
            resolveHydrate = resolve;
        });
        const rehydrate = vi.spyOn(useProjectStore.persist, "rehydrate").mockImplementation(() => pendingHydrate);

        activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
        const first = activateProjectEnvironment(getRuntimeEnvironment()!.key, getRuntimeEnvironment()!.generation);
        activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
        const second = activateProjectEnvironment(getRuntimeEnvironment()!.key, getRuntimeEnvironment()!.generation);

        resolveHydrate();
        await Promise.all([first, second]);

        expect(useProjectStore.getState().activeEnvironmentKey).toBe(ENV_B);
        rehydrate.mockRestore();
    });

    it("does not expose same-URL project cache, watermarks, or generating tasks from another storage identity", async () => {
        await activate(STORAGE_A);
        useProjectStore.getState().applyProjectReadSnapshot("p", project("p", 5), true);
        useProjectStore.getState().addGeneratingTask("asset-a", "full_body", 1);
        expect(useProjectStore.getState().currentProject?.readEnvironment).toBe(ENV_A);
        expect(useProjectStore.getState().readWatermarks[ENV_A]?.library).toBe(0);

        await activate(STORAGE_B);
        expect(useProjectStore.getState().projects).toEqual([]);
        expect(useProjectStore.getState().currentProject).toBeNull();
        expect(useProjectStore.getState().readWatermarks).toEqual({});
        expect(useProjectStore.getState().generatingTasks).toEqual([]);

        useProjectStore.getState().applyProjectReadSnapshot("p", project("p", 2), true);
        expect(useProjectStore.getState().currentProject?.readEnvironment).toBe(ENV_B);

        await activate(STORAGE_A);
        expect(useProjectStore.getState().currentProject?.readEnvironment).toBe(ENV_A);
        expect(useProjectStore.getState().currentProject?.revision).toBe(5);
        expect(useProjectStore.getState().generatingTasks).toEqual([{ assetId: "asset-a", generationType: "full_body", batchSize: 1 }]);
    });

    it("keeps legacy URL-only persisted project cache inert after activation", async () => {
        useProjectStore.setState({
            projects: [{ ...project("legacy", 9), originalText: "legacy", createdAt: "old", updatedAt: "old", status: "draft", readEnvironment: API_URL }],
            currentProject: { ...project("legacy", 9), originalText: "legacy", createdAt: "old", updatedAt: "old", status: "draft", readEnvironment: API_URL },
            readWatermarks: { [API_URL]: { series: {}, project: { legacy: 9 } } },
            generatingTasks: [{ assetId: "legacy", generationType: "headshot", batchSize: 1 }],
        });

        await activate(STORAGE_A);

        expect(useProjectStore.getState().projects).toEqual([]);
        expect(useProjectStore.getState().currentProject).toBeNull();
        expect(useProjectStore.getState().readWatermarks).toEqual({});
        expect(useProjectStore.getState().generatingTasks).toEqual([]);
    });

    it("drops a delayed read that resolves after the active storage identity changed", async () => {
        await activate(STORAGE_A);
        let resolveRead!: (response: Response) => void;
        vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { resolveRead = resolve; })));

        const read = useProjectStore.getState().selectProject("p");
        await activate(STORAGE_B);
        resolveRead(new Response(JSON.stringify(project("p", 7)), { status: 200, headers: { "Content-Type": "application/json" } }));
        await read;

        expect(useProjectStore.getState().projects).toEqual([]);
        expect(useProjectStore.getState().runningOps[projectReadFailureKey("p")]).toBeUndefined();
    });
});

describe("draft and default cache namespaces", () => {
    it("uses active storage identity for script drafts and localStorage defaults", async () => {
        await activate(STORAGE_A);
        const keyA = scriptDraftKey("p");
        useScriptDraftStore.getState().initialize(keyA, "draft A", 1);
        localStorage.setItem(environmentLocalStorageKey("lumenx_default_model_settings"), JSON.stringify({ image_model: "wan-a" }));

        await activate(STORAGE_B);
        const keyB = scriptDraftKey("p");

        expect(keyB).not.toBe(keyA);
        expect(useScriptDraftStore.getState().drafts[keyB]).toBeUndefined();
        expect(localStorage.getItem(environmentLocalStorageKey("lumenx_default_model_settings"))).toBeNull();

        await activate(STORAGE_A);
        expect(scriptDraftKey("p")).toBe(keyA);
        expect(useScriptDraftStore.getState().drafts[keyA]?.text).toBe("draft A");
        expect(localStorage.getItem(environmentLocalStorageKey("lumenx_default_model_settings"))).toContain("wan-a");
    });
});
