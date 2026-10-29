// @vitest-environment happy-dom
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { usePlaygroundStore, type PlaygroundGeneration, type PlaygroundParameters } from "@/components/modules/playground/usePlaygroundStore";
import { beginPlaygroundGenerationRequest, listPlaygroundGenerationRequests, type PlaygroundGenerateRequestBody } from "@/lib/playgroundGenerationRequest";
import PlaygroundPage from "@/components/modules/playground/PlaygroundPage";

const playgroundApiMock = vi.hoisted(() => ({
    generate: vi.fn(),
    getCapabilities: vi.fn(),
    getRequestReceipt: vi.fn(),
    getHistory: vi.fn(),
    getTemplates: vi.fn(),
    getGenerationStatus: vi.fn(),
    getGeneration: vi.fn(),
    deleteGeneration: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
    const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    return {
        ...actual,
        playgroundApi: playgroundApiMock,
    };
});

vi.mock("next-intl", () => ({
    useTranslations: () => (key: string, params?: Record<string, unknown>) => (
        params?.count !== undefined ? `${key}:${params.count}` : key
    ),
}));

vi.mock("@/components/modules/playground/ModeSelector", () => ({ default: () => <div data-testid="mode-selector" /> }));
vi.mock("@/components/modules/playground/ModelSelector", () => ({ default: () => <div data-testid="model-selector" /> }));
vi.mock("@/components/modules/playground/MediaInput", () => ({ default: () => <div data-testid="media-input" /> }));
vi.mock("@/components/modules/playground/PromptInput", () => ({ default: () => <div data-testid="prompt-input" /> }));
vi.mock("@/components/modules/playground/ParameterBar", () => ({ default: () => <div data-testid="parameter-bar" /> }));
vi.mock("@/components/modules/playground/ResultGallery", async () => {
    const ReactActual = await vi.importActual<typeof React>("react");
    return { default: () => ReactActual.createElement("div", { "data-testid": "page-gallery" }) };
});

vi.mock("@/components/modules/playground/ResultCard", () => ({
    default: ({ generation, onRetry }: { generation: PlaygroundGeneration; onRetry: (gen: PlaygroundGeneration) => void }) => (
        <button type="button" onClick={() => onRetry(generation)}>retry {generation.id}</button>
    ),
}));
vi.mock("@/components/modules/playground/GalleryView", () => ({ default: () => <div data-testid="gallery-view" /> }));
vi.mock("@/components/modules/playground/DetailPanel", () => ({ default: () => <div data-testid="detail-panel" /> }));
vi.mock("@/components/modules/playground/QueuePanel", () => ({ default: () => <div data-testid="queue-panel" /> }));

const storageId = "00000000-0000-4000-8000-000000000001";

function resetStore() {
    usePlaygroundStore.setState({
        mode: "t2i",
        modelId: "wanx2.1-t2i",
        prompt: "glass city",
        negativePrompt: "",
        inputMedia: [],
        parameters: {},
        batchSize: 1,
        history: [],
        templates: [],
        isGenerating: false,
        activeGenerationIds: [],
        queue: [],
        maxConcurrent: 3,
    });
}

function generation(overrides: Partial<PlaygroundGeneration> = {}) {
    return {
        id: "gen-1",
        mode: "t2i",
        model_id: "wanx2.1-t2i",
        prompt: "glass city",
        input_media: [],
        parameters: {},
        batch_size: 1,
        outputs: [],
        status: "completed",
        created_at: "2026-10-13T00:00:00.000Z",
        ...overrides,
    } satisfies PlaygroundGeneration;
}

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function acceptedReceipt(
    gen: Partial<PlaygroundGeneration>,
    request: PlaygroundGenerateRequestBody,
    replayed = false,
    requestFingerprint = HASH_A,
) {
    return {
        schema_version: 1,
        replayed,
        request_status: "accepted",
        generation: {
            ...generation({
                id: "gen-1",
                mode: request.mode,
                model_id: request.model_id,
                prompt: request.prompt,
                negative_prompt: request.negative_prompt,
                input_media: request.input_media ?? [],
                parameters: (request.parameters ?? {}) as PlaygroundParameters,
                batch_size: request.batch_size ?? 1,
                ...gen,
            }),
            client_request_id: request.client_request_id,
            request_contract_version: "request-json-v1",
            request_fingerprint: requestFingerprint,
        },
        tombstone: null,
    };
}

function deletedReceipt(request: PlaygroundGenerateRequestBody, requestFingerprint = HASH_A) {
    return {
        schema_version: 1,
        replayed: true,
        request_status: "deleted",
        generation: null,
        tombstone: {
            record_type: "tombstone",
            schema_version: 1,
            client_request_id: request.client_request_id,
            request_contract_version: "request-json-v1",
            request_fingerprint: requestFingerprint,
            generation_id: "gen-deleted",
            deleted_at: "2026-10-13T00:00:00.000Z",
            deleted_status: "deleted",
        },
    };
}

beforeEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    resetStore();
    playgroundApiMock.getCapabilities.mockReset().mockResolvedValue(seedanceReady);
    playgroundApiMock.getHistory.mockResolvedValue([]);
    playgroundApiMock.getTemplates.mockResolvedValue([]);
});

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    localStorage.clear();
});

describe("Playground admission flow", () => {
    it.each([
        { mode: "i2v" as const, modelId: "pixverse/pixverse/pixverse-v6-video#i2v", duration: 10, extra: { shot_type: "multi" } },
        { mode: "i2v" as const, modelId: "pixverse-c1-i2v", duration: 10, extra: {} },
        { mode: "r2v" as const, modelId: "pixverse/pixverse-c1-video#r2v", duration: 10, extra: { aspect_ratio: "3:4" } },
        { mode: "r2v" as const, modelId: "pixverse-v5.6-r2v", duration: 5, extra: { aspect_ratio: "3:4" } },
    ])("submits valid PixVerse parameters and original ordered inputs for $modelId", async ({ mode, modelId, duration, extra }) => {
        const refs = mode === "r2v" ? ["/uploads/second.webp", "/uploads/first.png"] : ["/uploads/first.webp"];
        usePlaygroundStore.setState({
            mode, modelId, prompt: "Original PixVerse prompt", negativePrompt: "keep this local draft",
            inputMedia: refs,
            parameters: { duration: 10, resolution: "1080P", aspect_ratio: "3:4", seed: 0, audio: false, watermark: false, shot_type: "multi", prompt_extend: true },
        });
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-pixverse-params", status: "completed" }, request)
        ));
        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));
        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        const body = playgroundApiMock.generate.mock.calls[0][0] as PlaygroundGenerateRequestBody;
        expect(body.model_id).toBe(modelId);
        expect(body.mode).toBe(mode);
        expect(body.prompt).toBe("Original PixVerse prompt");
        expect(body.negative_prompt).toBeUndefined();
        expect(body.input_media).toEqual(refs);
        expect(body.parameters).toEqual({ duration, resolution: "1080P", seed: 0, audio: false, watermark: false, ...extra });
        expect(usePlaygroundStore.getState().parameters).toMatchObject({ duration: 10, shot_type: "multi", prompt_extend: true });
        expect(usePlaygroundStore.getState().negativePrompt).toBe("keep this local draft");
    });

    it.each([
        { mode: "i2v" as const, modelId: "kling-v3-i2v", refs: ["/uploads/first.png"], ratio: {} },
        { mode: "r2v" as const, modelId: "kling/kling-v3-video#r2v", refs: ["/uploads/second.png", "/uploads/first.png"], ratio: { aspect_ratio: "9:16" } },
    ])("submits Kling $mode parameters and ordered inputs without legacy draft fields", async ({ mode, modelId, refs, ratio }) => {
        usePlaygroundStore.setState({
            mode, modelId, prompt: "Kling contract", negativePrompt: "avoid blur",
            inputMedia: refs,
            parameters: { mode: "4k", duration: 7, audio: false, watermark: false, aspect_ratio: "9:16", resolution: "720P", seed: 0, prompt_extend: true },
        });
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-kling-params", status: "completed" }, request)
        ));
        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));
        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        const body = playgroundApiMock.generate.mock.calls[0][0] as PlaygroundGenerateRequestBody;
        expect(body.model_id).toBe(modelId);
        expect(body.mode).toBe(mode);
        expect(body.negative_prompt).toBe("avoid blur");
        expect(body.input_media).toEqual(refs);
        expect(body.parameters).toEqual({ mode: "4k", duration: 7, audio: false, watermark: false, ...ratio });
        expect(usePlaygroundStore.getState().parameters).toMatchObject({ resolution: "720P", seed: 0, prompt_extend: true });
    });

    it("persists the request before POST and reads back the original id after an unknown dispatch result", async () => {
        playgroundApiMock.generate.mockRejectedValueOnce(new Error("network lost after commit"));
        playgroundApiMock.getRequestReceipt.mockImplementationOnce(async (clientRequestId: string) => (
            acceptedReceipt({ id: "gen-readback", status: "completed" }, {
                client_request_id: clientRequestId,
                mode: "t2i",
                model_id: "wanx2.1-t2i",
                prompt: "glass city",
            }, true)
        ));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));

        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        const body = playgroundApiMock.generate.mock.calls[0][0];
        expect(body.client_request_id).toMatch(/^playground-/);
        expect(body.prompt).toBe("glass city");
        await waitFor(() => expect(playgroundApiMock.getRequestReceipt).toHaveBeenCalledWith(body.client_request_id));
        await waitFor(() => expect(usePlaygroundStore.getState().history[0]?.id).toBe("gen-readback"));
        expect(usePlaygroundStore.getState().queue).toEqual([]);
        expect(listPlaygroundGenerationRequests({ environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId }), storage: localStorage }))
            .toEqual([]);
    });

    it("releases the queue and clears the journal when the server returns a deleted ACK", async () => {
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => deletedReceipt(request));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));

        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(usePlaygroundStore.getState().queue).toEqual([]));
        expect(usePlaygroundStore.getState().history).toEqual([]);
        expect(listPlaygroundGenerationRequests({ environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId }), storage: localStorage }))
            .toEqual([]);
    });

    it("submits the same migrated parameter contract that the UI displays for HappyHorse defaults", async () => {
        usePlaygroundStore.setState({
            mode: "i2v",
            modelId: "happyhorse-1.1-i2v",
            prompt: "horse lens test",
            negativePrompt: "draft should remain local",
            inputMedia: ["/uploads/frame.png"],
            parameters: {
                duration: 5,
                resolution: "720P",
                aspect_ratio: "16:9",
                prompt_extend: false,
                audio_setting: "origin",
                seed: 0,
            },
        });
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-hh-params", status: "completed" }, request)
        ));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));

        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        const body = playgroundApiMock.generate.mock.calls[0][0] as PlaygroundGenerateRequestBody;
        expect(body.negative_prompt).toBeUndefined();
        expect(body.parameters).toEqual({
            duration: 5,
            resolution: "720P",
            seed: 0,
            watermark: true,
        });
        expect(usePlaygroundStore.getState().negativePrompt).toBe("draft should remain local");
        expect(usePlaygroundStore.getState().parameters.prompt_extend).toBe(false);
    });

    it("does not submit a fallback ratio for migrated Wan I2V requests", async () => {
        usePlaygroundStore.setState({
            mode: "i2v",
            modelId: "wan2.7-i2v",
            prompt: "wan i2v no ratio",
            negativePrompt: "avoid blur",
            inputMedia: ["/uploads/frame.png"],
            parameters: {
                duration: 5,
                resolution: "1080p",
                aspect_ratio: "16:9",
                prompt_extend: true,
                watermark: false,
            },
        });
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-wan-i2v-params", status: "completed" }, request)
        ));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));

        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        const body = playgroundApiMock.generate.mock.calls[0][0] as PlaygroundGenerateRequestBody;
        expect(body.negative_prompt).toBe("avoid blur");
        expect(body.parameters).toEqual({
            duration: 5,
            resolution: "1080p",
            prompt_extend: true,
            watermark: false,
        });
    });

    it("submits HappyHorse VideoEdit source-video parameters without stale duration or prompt fields", async () => {
        usePlaygroundStore.setState({
            mode: "v2v",
            modelId: "happyhorse-1.0-video-edit",
            prompt: "source video edit",
            negativePrompt: "draft should not submit",
            inputMedia: ["/uploads/source.mp4"],
            parameters: {
                duration: 0,
                resolution: "1080P",
                aspect_ratio: "16:9",
                audio_setting: "origin",
                prompt_extend: false,
                watermark: false,
                seed: 0,
            },
        });
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-hh-videoedit-params", status: "completed" }, request)
        ));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));

        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        const body = playgroundApiMock.generate.mock.calls[0][0] as PlaygroundGenerateRequestBody;
        expect(body.negative_prompt).toBeUndefined();
        expect(body.parameters).toEqual({
            resolution: "1080P",
            audio_setting: "origin",
            watermark: false,
            seed: 0,
        });
        expect(usePlaygroundStore.getState().negativePrompt).toBe("draft should not submit");
        expect(usePlaygroundStore.getState().parameters.duration).toBe(0);
    });

    it("keeps a single timer for processing ACKs and clears it on unmount", async () => {
        vi.useFakeTimers();
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-processing", status: "processing" }, request)
        ));

        const view = render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));
        await act(async () => {
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(usePlaygroundStore.getState().activeGenerationIds).toEqual(["gen-processing"]);
        expect(vi.getTimerCount()).toBe(1);

        view.unmount();
        expect(vi.getTimerCount()).toBe(0);
    });

    it("deduplicates queued entries by client request id", () => {
        const environment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        const request = {
            schema_version: 1 as const,
            apiBase: environment.apiUrl,
            storageId: environment.storageId,
            clientRequestId: "playground-duplicate",
            requestFingerprint: "same",
            request: {
                client_request_id: "playground-duplicate",
                mode: "t2i" as const,
                model_id: "wanx2.1-t2i",
                prompt: "same",
            },
            createdAt: 1,
        };
        act(() => {
            usePlaygroundStore.getState().enqueueRequest({
                clientRequestId: request.clientRequestId,
                requestFingerprint: request.requestFingerprint,
                body: request.request,
                journal: request,
            });
            usePlaygroundStore.getState().enqueueRequest({
                clientRequestId: request.clientRequestId,
                requestFingerprint: request.requestFingerprint,
                body: request.request,
                journal: request,
            });
        });

        expect(usePlaygroundStore.getState().queue).toHaveLength(1);
    });

    it("reads local journals on reload, adopts accepted work, and observes non-terminal generations", async () => {
        vi.useFakeTimers();
        const environment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        const journal = beginPlaygroundGenerationRequest({
            environment,
            mode: "t2i",
            modelId: "wanx2.1-t2i",
            prompt: "reload recovery",
            storage: localStorage,
            idFactory: () => "playground-reload",
        });
        playgroundApiMock.getRequestReceipt.mockResolvedValueOnce(acceptedReceipt(
            { id: "gen-reload", status: "processing" },
            journal.request,
            true,
        ));

        const view = render(<PlaygroundPage />);
        await act(async () => {
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
        expect(playgroundApiMock.getRequestReceipt).toHaveBeenCalledWith("playground-reload");
        expect(usePlaygroundStore.getState().activeGenerationIds).toEqual(["gen-reload"]);
        expect(vi.getTimerCount()).toBe(1);
        view.unmount();
        expect(vi.getTimerCount()).toBe(0);
    });

    it("keeps failed journal readback visible and later refreshes the same id without POST", async () => {
        const environment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        beginPlaygroundGenerationRequest({
            environment,
            mode: "t2i",
            modelId: "wanx2.1-t2i",
            prompt: "lost ack",
            storage: localStorage,
            idFactory: () => "playground-lost-ack",
        });
        playgroundApiMock.getRequestReceipt
            .mockRejectedValueOnce(new Error("404"))
            .mockResolvedValueOnce(acceptedReceipt({ id: "gen-lost", status: "completed" }, {
                client_request_id: "playground-lost-ack",
                mode: "t2i",
                model_id: "wanx2.1-t2i",
                prompt: "lost ack",
            }, true));

        render(<PlaygroundPage />);
        await waitFor(() => expect(usePlaygroundStore.getState().queue[0]?.status).toBe("needs_readback"));
        expect(usePlaygroundStore.getState().queue[0]?.clientRequestId).toBe("playground-lost-ack");
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();

        act(() => {
            usePlaygroundStore.getState().requestReadback(usePlaygroundStore.getState().queue[0].id);
        });

        await waitFor(() => expect(playgroundApiMock.getRequestReceipt).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(usePlaygroundStore.getState().history[0]?.id).toBe("gen-lost"));
        expect(usePlaygroundStore.getState().queue).toEqual([]);
        expect(listPlaygroundGenerationRequests({ environment, storage: localStorage })).toEqual([]);
    });



    it("keeps invalid accepted ACK visible for readback instead of clearing the journal", async () => {
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-wrong-intent", prompt: "different prompt" }, request)
        ));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));

        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(usePlaygroundStore.getState().queue[0]?.status).toBe("needs_readback"));
        expect(usePlaygroundStore.getState().history).toEqual([]);
        expect(listPlaygroundGenerationRequests({ environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId }), storage: localStorage }))
            .toHaveLength(1);
    });

    it("ignores a late POST ACK after the runtime owner changes", async () => {
        let resolveGenerate!: (value: ReturnType<typeof acceptedReceipt>) => void;
        playgroundApiMock.generate.mockImplementationOnce((request: PlaygroundGenerateRequestBody) => new Promise((resolve) => {
            resolveGenerate = resolve;
            (globalThis as typeof globalThis & { __lateRequest?: PlaygroundGenerateRequestBody }).__lateRequest = request;
        }));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));
        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        const staleRequest = (globalThis as typeof globalThis & { __lateRequest: PlaygroundGenerateRequestBody }).__lateRequest;

        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000002" });
        });
        expect(usePlaygroundStore.getState().queue).toEqual([]);

        await act(async () => {
            resolveGenerate(acceptedReceipt({ id: "gen-stale-post" }, staleRequest));
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(usePlaygroundStore.getState().history).toEqual([]);
        expect(usePlaygroundStore.getState().queue).toEqual([]);
        expect(playgroundApiMock.getRequestReceipt).not.toHaveBeenCalled();
    });



    it("ignores an in-flight POST from an unmounted instance after same-runtime remount", async () => {
        const environment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        let resolveGenerate!: (value: ReturnType<typeof acceptedReceipt>) => void;
        let staleRequest!: PlaygroundGenerateRequestBody;
        playgroundApiMock.generate.mockImplementationOnce((request: PlaygroundGenerateRequestBody) => new Promise((resolve) => {
            staleRequest = request;
            resolveGenerate = resolve;
        }));
        playgroundApiMock.getRequestReceipt.mockImplementation(() => new Promise(() => undefined));

        const firstView = render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));
        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        firstView.unmount();

        beginPlaygroundGenerationRequest({
            environment,
            mode: "t2i",
            modelId: "wanx2.1-t2i",
            prompt: "fresh journal after remount",
            storage: localStorage,
            idFactory: () => "playground-fresh-after-remount",
        });
        render(<PlaygroundPage />);
        await waitFor(() => expect(playgroundApiMock.getRequestReceipt).toHaveBeenCalled());

        await act(async () => {
            resolveGenerate(acceptedReceipt({ id: "gen-stale-unmounted" }, staleRequest));
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(usePlaygroundStore.getState().history.some((item) => item.id === "gen-stale-unmounted")).toBe(false);
        expect(listPlaygroundGenerationRequests({ environment, storage: localStorage }).map(item => item.clientRequestId))
            .toContain("playground-fresh-after-remount");
    });

    it("rejects stale readback results across A to B to A runtime switches", async () => {
        const ownerA = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        const journal = beginPlaygroundGenerationRequest({
            environment: ownerA,
            mode: "t2i",
            modelId: "wanx2.1-t2i",
            prompt: "late readback",
            storage: localStorage,
            idFactory: () => "playground-late-readback",
        });
        const deferreds: Array<(value: ReturnType<typeof acceptedReceipt>) => void> = [];
        playgroundApiMock.getRequestReceipt.mockImplementation(() => new Promise((resolve) => {
            deferreds.push(resolve);
        }));

        render(<PlaygroundPage />);
        await waitFor(() => expect(playgroundApiMock.getRequestReceipt).toHaveBeenCalledTimes(1));

        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000002" });
        });
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        });
        await waitFor(() => expect(playgroundApiMock.getRequestReceipt).toHaveBeenCalledTimes(2));

        await act(async () => {
            deferreds[0](acceptedReceipt({ id: "gen-stale-readback" }, journal.request, true));
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(usePlaygroundStore.getState().history).toEqual([]);
        expect(listPlaygroundGenerationRequests({ environment: ownerA, storage: localStorage }).map(item => item.clientRequestId))
            .toEqual(["playground-late-readback"]);

        await act(async () => {
            deferreds[1](acceptedReceipt({ id: "gen-current-readback" }, journal.request, true, HASH_B));
            await Promise.resolve();
            await Promise.resolve();
        });
        await waitFor(() => expect(usePlaygroundStore.getState().history[0]?.id).toBe("gen-current-readback"));
        expect(listPlaygroundGenerationRequests({ environment: ownerA, storage: localStorage })).toEqual([]);
    });

    it("does not restore journals from another runtime storage owner", async () => {
        const ownerA = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        beginPlaygroundGenerationRequest({
            environment: ownerA,
            mode: "t2i",
            modelId: "wanx2.1-t2i",
            prompt: "owner A",
            storage: localStorage,
            idFactory: () => "playground-owner-a",
        });
        const ownerB = activateRuntimeEnvironment({
            schema_version: 1,
            storage_id: "00000000-0000-4000-8000-000000000002",
        });

        render(<PlaygroundPage />);
        await act(async () => {
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(playgroundApiMock.getRequestReceipt).not.toHaveBeenCalled();
        expect(listPlaygroundGenerationRequests({ environment: ownerA, storage: localStorage }).map(item => item.clientRequestId))
            .toEqual(["playground-owner-a"]);
        expect(listPlaygroundGenerationRequests({ environment: ownerB, storage: localStorage })).toEqual([]);
    });
});



const seedanceReady = { schema_version: 1, seedance: { available: true, reason: null, channel: 'mulerouter_http', input_kinds: ['image'] } };
const seedanceUnavailable = (reason = 'seedance_cli_submission_unsafe') => ({ schema_version: 1, seedance: { available: false, reason, channel: null, input_kinds: ['image'] } });
function selectSeedance(modelId = 'seedance-2.0-t2v', mode: 't2v' | 'i2v' | 'r2v' = 't2v', inputMedia: string[] = []) {
    usePlaygroundStore.setState({ modelId, mode, inputMedia, parameters: { prompt_extend: true, watermark: false, shot_type: 'multi' }, negativePrompt: 'retain draft' });
}
const clickGenerate = () => fireEvent.click(screen.getByRole('button', { name: /compose.generate/ }));
const journalCount = () => listPlaygroundGenerationRequests({ environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId }), storage: localStorage }).length;

describe('Seedance capability admission controls', () => {
    it.each(['seedance_http_key_required', 'seedance_http_endpoint_unsupported', 'seedance_cli_submission_unsafe'])('keeps the chosen model and drafts when %s', async reason => {
        selectSeedance();
        playgroundApiMock.getCapabilities.mockResolvedValue(seedanceUnavailable(reason));
        render(<PlaygroundPage />);
        await screen.findByText(`capabilities.${reason}`);
        expect(screen.getByRole('button', { name: /compose.generate/ })).toBeDisabled();
        expect(usePlaygroundStore.getState().modelId).toBe('seedance-2.0-t2v');
        expect(usePlaygroundStore.getState().parameters.shot_type).toBe('multi');
        expect(journalCount()).toBe(0);
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
    });
    it('fails closed while loading or failed, then permits an explicit fresh check', async () => {
        selectSeedance();
        let reject!: (error: Error) => void;
        playgroundApiMock.getCapabilities.mockImplementationOnce(() => new Promise((_, no) => { reject = no; }));
        render(<PlaygroundPage />);
        expect(screen.getByText('capabilities.checking')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /compose.generate/ })).toBeDisabled();
        await act(async () => reject(new Error('private transport details')));
        await screen.findByText('capabilities.seedance_capability_check_failed');
        expect(screen.queryByText('private transport details')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'capabilities.recheck' }));
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        expect(journalCount()).toBe(0);
    });
    it.each(['t2v', 'i2v', 'r2v'] as const)('submits normalized %s defaults and keeps hidden drafts', async mode => {
        const refs = mode === 't2v' ? [] : ['output/first.webp'];
        selectSeedance(`seedance/seedance-2.0-video#${mode}`, mode, refs);
        playgroundApiMock.generate.mockImplementationOnce(async request => acceptedReceipt({ id: 'seedance-defaults' }, request));
        render(<PlaygroundPage />);
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        clickGenerate();
        await waitFor(() => expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1));
        const request = playgroundApiMock.generate.mock.calls[0][0];
        expect(request.parameters).toEqual({ duration: 5, resolution: '1080p', aspect_ratio: '16:9', audio: true, ...(mode === 't2v' ? { watermark: false } : {}) });
        expect(request.input_media ?? []).toEqual(refs);
        expect(request.negative_prompt).toBeUndefined();
        expect(playgroundApiMock.getCapabilities).toHaveBeenCalledTimes(3); // display, pre-journal, dispatch
        expect(usePlaygroundStore.getState().negativePrompt).toBe('retain draft');
        expect(usePlaygroundStore.getState().parameters.shot_type).toBe('multi');
    });
    it('checks again before the journal and does not persist after a new gate', async () => {
        selectSeedance();
        playgroundApiMock.getCapabilities.mockResolvedValueOnce(seedanceReady).mockResolvedValue(seedanceUnavailable());
        render(<PlaygroundPage />);
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        clickGenerate();
        await screen.findByText('capabilities.seedance_cli_submission_unsafe');
        expect(journalCount()).toBe(0);
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
    });
    it('drops a never-posted journal when the dispatch check becomes unavailable', async () => {
        selectSeedance();
        playgroundApiMock.getCapabilities.mockResolvedValueOnce(seedanceReady).mockResolvedValueOnce(seedanceReady).mockResolvedValue(seedanceUnavailable());
        render(<PlaygroundPage />);
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        clickGenerate();
        await screen.findByText('capabilities.seedance_cli_submission_unsafe');
        expect(journalCount()).toBe(0);
        expect(usePlaygroundStore.getState().queue).toHaveLength(0);
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
    });
    it.each([503, 404])('keeps generic HTTP %s in the unknown acceptance readback flow', async status => {
        selectSeedance();
        playgroundApiMock.generate.mockRejectedValueOnce({ response: { status, data: { detail: { code: 'other', reason: 'seedance_cli_submission_unsafe' } } } });
        playgroundApiMock.getRequestReceipt.mockRejectedValueOnce(new Error('still unknown'));
        render(<PlaygroundPage />);
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        clickGenerate();
        await waitFor(() => expect(usePlaygroundStore.getState().queue[0]?.status).toBe('needs_readback'));
        expect(playgroundApiMock.getRequestReceipt).toHaveBeenCalledTimes(1);
        expect(journalCount()).toBe(1);
    });
    it('cleans up only the explicit definite non-admission 503 receipt', async () => {
        selectSeedance();
        playgroundApiMock.generate.mockRejectedValueOnce({ response: { status: 503, data: { detail: { code: 'playground_generation_not_admitted', reason: 'seedance_cli_submission_unsafe' } } } });
        render(<PlaygroundPage />);
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        clickGenerate();
        await screen.findByText('capabilities.seedance_cli_submission_unsafe');
        expect(playgroundApiMock.generate).toHaveBeenCalledTimes(1);
        expect(playgroundApiMock.getRequestReceipt).not.toHaveBeenCalled();
        expect(journalCount()).toBe(0);
        expect(usePlaygroundStore.getState().queue).toHaveLength(0);
    });
    it('ignores late availability across A to B to A', async () => {
        selectSeedance();
        let resolve!: (value: unknown) => void;
        playgroundApiMock.getCapabilities.mockImplementationOnce(() => new Promise(yes => { resolve = yes; })).mockResolvedValue(seedanceUnavailable());
        render(<PlaygroundPage />);
        act(() => activateRuntimeEnvironment({ schema_version: 1, storage_id: '00000000-0000-4000-8000-000000000002' }));
        act(() => activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId }));
        await screen.findByText('capabilities.seedance_cli_submission_unsafe');
        await act(async () => resolve(seedanceReady));
        expect(screen.getByRole('button', { name: /compose.generate/ })).toBeDisabled();
        expect(journalCount()).toBe(0);
    });
    it('does not journal a late preflight after changing a draft', async () => {
        selectSeedance();
        let resolve!: (value: unknown) => void;
        playgroundApiMock.getCapabilities.mockResolvedValueOnce(seedanceReady).mockImplementationOnce(() => new Promise(yes => { resolve = yes; }));
        render(<PlaygroundPage />);
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        clickGenerate();
        act(() => usePlaygroundStore.setState({ prompt: 'new draft' }));
        await act(async () => resolve(seedanceReady));
        expect(journalCount()).toBe(0);
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
    });


    it.each(['unavailable', 'failed'])('releases a changed model immediately and ignores the old preflight %s', async outcome => {
        selectSeedance();
        let resolve!: (value: unknown) => void;
        let reject!: (error: Error) => void;
        playgroundApiMock.getCapabilities.mockResolvedValueOnce(seedanceReady).mockImplementationOnce(() => new Promise((yes, no) => { resolve = yes; reject = no; }));
        render(<PlaygroundPage />);
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        clickGenerate();
        act(() => usePlaygroundStore.setState({ mode: 't2i', modelId: 'wanx2.1-t2i', prompt: 'new model draft' }));
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        await act(async () => { if (outcome === 'failed') reject(new Error('old lookup')); else resolve(seedanceUnavailable()); });
        expect(screen.queryByText('capabilities.seedance_cli_submission_unsafe')).toBeNull();
        expect(screen.queryByText('capabilities.seedance_capability_check_failed')).toBeNull();
        expect(journalCount()).toBe(0);
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
    });

    it('never creates a journal for a stale preflight after A to B to A', async () => {
        selectSeedance();
        let resolve!: (value: unknown) => void;
        playgroundApiMock.getCapabilities.mockResolvedValueOnce(seedanceReady).mockImplementationOnce(() => new Promise(yes => { resolve = yes; })).mockResolvedValue(seedanceReady);
        render(<PlaygroundPage />);
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        clickGenerate();
        act(() => activateRuntimeEnvironment({ schema_version: 1, storage_id: '00000000-0000-4000-8000-000000000002' }));
        act(() => activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId }));
        await act(async () => resolve(seedanceReady));
        expect(journalCount()).toBe(0);
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
    });

    it('keeps T2V saved references until the user explicitly removes them', async () => {
        selectSeedance('seedance-2.0-t2v', 't2v', ['output/old.png']);
        render(<PlaygroundPage />);
        await screen.findByText('capabilities.seedance_image_count');
        expect(usePlaygroundStore.getState().inputMedia).toEqual(['output/old.png']);
        expect(screen.getByRole('button', { name: /compose.generate/ })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: 'capabilities.clearReferences' }));
        expect(usePlaygroundStore.getState().inputMedia).toEqual([]);
        await waitFor(() => expect(screen.getByRole('button', { name: /compose.generate/ })).toBeEnabled());
        expect(journalCount()).toBe(0);
    });


    it('allows a current historical retry across A to B to A without the old lock or response taking ownership', async () => {
        const { default: ActualResultGallery } = await vi.importActual<typeof import('@/components/modules/playground/ResultGallery')>('@/components/modules/playground/ResultGallery');
        const original = generation({ id: 'retry-owner', mode: 't2v', model_id: 'seedance-2.0-t2v' });
        usePlaygroundStore.setState({ history: [original] });
        const resolves: Array<(value: unknown) => void> = [];
        playgroundApiMock.getCapabilities.mockImplementation(() => new Promise(yes => { resolves.push(yes); }));
        render(<ActualResultGallery />);
        fireEvent.click(screen.getByRole('button', { name: 'retry retry-owner' }));
        expect(resolves).toHaveLength(1);
        act(() => activateRuntimeEnvironment({ schema_version: 1, storage_id: '00000000-0000-4000-8000-000000000002' }));
        act(() => activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId }));
        fireEvent.click(screen.getByRole('button', { name: 'retry retry-owner' }));
        expect(resolves).toHaveLength(2);
        await act(async () => resolves[0](seedanceReady));
        expect(journalCount()).toBe(0);
        fireEvent.click(screen.getByRole('button', { name: 'retry retry-owner' }));
        expect(resolves).toHaveLength(2); // the stale finally cannot unlock the current attempt
        await act(async () => resolves[1](seedanceReady));
        expect(journalCount()).toBe(1);
        expect(usePlaygroundStore.getState().queue).toHaveLength(1);
    });

    it.each(['unavailable', 'mixed-media', 'available'])('guards the real historical retry before journaling: %s', async scenario => {
        const { default: ActualResultGallery } = await vi.importActual<typeof import('@/components/modules/playground/ResultGallery')>('@/components/modules/playground/ResultGallery');
        const refs = scenario === 'mixed-media' ? ['output/a.png', 'output/b.mp4'] : ['output/a.png'];
        usePlaygroundStore.setState({ history: [generation({ id: 'old-seedance', mode: 'r2v', model_id: 'seedance-2.0-r2v', input_media: refs, parameters: { watermark: true, prompt_extend: true } })] });
        if (scenario === 'unavailable') playgroundApiMock.getCapabilities.mockResolvedValue(seedanceUnavailable());
        render(<ActualResultGallery />);
        fireEvent.click(screen.getByRole('button', { name: 'retry old-seedance' }));
        if (scenario === 'available') {
            await waitFor(() => expect(usePlaygroundStore.getState().queue).toHaveLength(1));
            expect(usePlaygroundStore.getState().queue[0].body.parameters).toEqual({ duration: 5, resolution: '1080p', aspect_ratio: '16:9', audio: true });
            expect(journalCount()).toBe(1);
        } else {
            await screen.findByText(`capabilities.${scenario === 'mixed-media' ? 'seedance_images_only' : 'seedance_cli_submission_unsafe'}`);
            expect(journalCount()).toBe(0);
            expect(usePlaygroundStore.getState().queue).toHaveLength(0);
        }
        expect(usePlaygroundStore.getState().history[0].input_media).toEqual(refs);
    });
});
