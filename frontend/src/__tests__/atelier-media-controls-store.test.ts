// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAtelierStore } from "@/store/atelierStore";
import { atelierProjectApi, parseAtelierProject } from "@/lib/atelierApi";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierNodeCommandAck, AtelierProject, JsonRecord } from "@/lib/atelierTypes";
import fixture from "./fixtures/atelier-project.json";

const api = vi.hoisted(() => ({
    saveDraft: vi.fn(),
    selectCandidate: vi.fn(),
    setCandidateVisibility: vi.fn(),
}));
vi.mock("@/lib/atelierMediaApi", async importOriginal => ({
    ...(await importOriginal<typeof import("@/lib/atelierMediaApi")>()),
    atelierMediaApi: api,
}));

const state = () => useAtelierStore.getState();
const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (error: unknown) => void;
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
};

function project(id = "p", revision = 0): AtelierProject {
    return parseAtelierProject({
        ...structuredClone(fixture),
        id,
        revision,
        nodes: [{
            ...structuredClone(fixture.nodes[0]),
            id: "image",
            project_id: id,
            type: "image",
            prompt: "",
            data: {},
            media_urls: [],
        }],
        agent_turns: [],
        sequence: [],
        audio_tracks: [],
        exports: [],
        node_delete_receipts: [],
        node_upload_receipts: [],
        sequence_export_jobs: [],
        generation_attempts: [],
        generation_request_receipts: [],
    });
}

function mediaAck(projectId: string, revision: number, patch: (node: AtelierProject["nodes"][number]) => void): AtelierNodeCommandAck {
    const p = project(projectId, revision);
    patch(p.nodes[0]);
    return { project: p, node_ids: ["image"] };
}

function seed(p = project()) {
    useAtelierStore.setState({ projects: [p], currentProject: p, selectedNodeId: null });
}

beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
    seed();
});

describe("Atelier media control store", () => {
    it("queues media draft after an edit and sends the latest revision with a snapshotted input", async () => {
        const updated = deferred<AtelierProject>();
        vi.spyOn(atelierProjectApi, "updateProject").mockReturnValue(updated.promise);
        const edit = state().updateProject({ title: "edited" });
        const input = {
            media_kind: "image" as const,
            prompt: "glass city",
            model_id: "wan/wan2.7-image-pro#image",
            mode: "t2i" as const,
            params: { seed: -1, prompt_extend: false, watermark: false },
            media: [],
            batch_size: 2,
        };
        api.saveDraft.mockImplementation(async (_projectId, _nodeId, request) =>
            mediaAck("p", request.expected_revision + 1, node => {
                node.prompt = request.prompt;
                node.data = { generation_draft: { schema_version: 3, ...request, expected_revision: undefined } as unknown as JsonRecord };
            }));

        const saving = state().saveMediaGenerationDraft("image", input);
        input.prompt = "late mutation";
        input.params.seed = 99;
        updated.resolve(project("p", 4));
        await edit;
        await saving;

        expect(api.saveDraft).toHaveBeenCalledWith("p", "image", {
            expected_revision: 4,
            media_kind: "image",
            prompt: "glass city",
            model_id: "wan/wan2.7-image-pro#image",
            mode: "t2i",
            params: { seed: -1, prompt_extend: false, watermark: false },
            media: [],
            batch_size: 2,
        });
    });

    it("does not replace the selected project when a deferred media selection resolves after navigation", async () => {
        const pending = deferred<AtelierNodeCommandAck>();
        api.selectCandidate.mockReturnValue(pending.promise);
        vi.spyOn(atelierProjectApi, "getProject").mockResolvedValue(project("q", 3));
        const selecting = state().selectMediaGenerationCandidate("image", "candidate-1");
        await Promise.resolve();

        const switched = await state().switchProject("q");
        pending.resolve(mediaAck("p", 1, node => {
            node.media_urls = ["assets/imported/p/atelier/out.png"];
            node.data = { selected_candidate_id: "candidate-1" };
        }));
        await selecting;

        expect(switched.id).toBe("q");
        expect(state().currentProject?.id).toBe("q");
        expect(state().currentProject?.revision).toBe(3);
    });

    it("rejects a deferred media visibility ack after runtime environment changes", async () => {
        const pending = deferred<AtelierNodeCommandAck>();
        api.setCandidateVisibility.mockReturnValue(pending.promise);
        const hiding = state().setMediaGenerationCandidateVisibility("image", "candidate-1", false);
        await Promise.resolve();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
        pending.resolve(mediaAck("p", 1, node => {
            node.data = { deleted_candidates: [{ id: "candidate-1" }] };
        }));

        await expect(hiding).rejects.toThrow("Atelier environment or project changed during request");
        expect(state().currentProject).toBeNull();
    });

    it("marks a failed media control write for refresh and resumes with the readback revision", async () => {
        api.saveDraft.mockRejectedValueOnce(Object.assign(new Error("conflict"), { status: 409 }));

        await expect(state().saveMediaGenerationDraft("image", {
            media_kind: "audio",
            prompt: "voice",
            voice: "longanyang",
            params: { volume: 50 },
        })).rejects.toThrow("conflict");

        expect(state().needsRefresh).toBe(true);
        await expect(state().selectMediaGenerationCandidate("image", "candidate-1")).rejects.toThrow("Refresh the Atelier project before saving again");

        vi.spyOn(atelierProjectApi, "getProject").mockResolvedValue(project("p", 7));
        await state().refreshProject();
        api.selectCandidate.mockResolvedValue(mediaAck("p", 8, node => {
            node.media_urls = ["assets/imported/p/atelier/out.png"];
            node.data = { selected_candidate_id: "candidate-1" };
        }));

        await state().selectMediaGenerationCandidate("image", "candidate-1");
        expect(api.selectCandidate).toHaveBeenCalledWith("p", "image", "candidate-1", { expected_revision: 7 });
        expect(state().needsRefresh).toBe(false);
    });
});
