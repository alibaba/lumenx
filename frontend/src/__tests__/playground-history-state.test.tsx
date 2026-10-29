// @vitest-environment happy-dom
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PlaygroundPage from "@/components/modules/playground/PlaygroundPage";
import { usePlaygroundStore, type PlaygroundGeneration, type PlaygroundParameters } from "@/components/modules/playground/usePlaygroundStore";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { type PlaygroundGenerateRequestBody } from "@/lib/playgroundGenerationRequest";

const playgroundApiMock = vi.hoisted(() => ({
    generate: vi.fn(),
    getRequestReceipt: vi.fn(),
    getHistory: vi.fn(),
    getTemplates: vi.fn(),
    getGeneration: vi.fn(),
    deleteGeneration: vi.fn(),
    saveToLibrary: vi.fn(),
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
vi.mock("@/components/modules/playground/DetailPanel", () => ({ default: () => <div data-testid="detail-panel" /> }));

const storageId = "00000000-0000-4000-8000-000000000001";

function generation(overrides: Partial<PlaygroundGeneration> = {}): PlaygroundGeneration {
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
    };
}

function apiGeneration(overrides: Partial<PlaygroundGeneration> = {}) {
    const gen = generation(overrides);
    return {
        ...gen,
        parameters: gen.parameters as PlaygroundParameters,
    };
}

function acceptedReceipt(gen: Partial<PlaygroundGeneration>, request: PlaygroundGenerateRequestBody) {
    return {
        schema_version: 1,
        replayed: false,
        request_status: "accepted",
        generation: {
            ...apiGeneration({
                id: "gen-accepted",
                mode: request.mode,
                model_id: request.model_id,
                prompt: request.prompt,
                input_media: request.input_media ?? [],
                parameters: (request.parameters ?? {}) as PlaygroundParameters,
                batch_size: request.batch_size ?? 1,
                ...gen,
            }),
            client_request_id: request.client_request_id,
            request_contract_version: "request-json-v1",
            request_fingerprint: "a".repeat(64),
        },
        tombstone: null,
    };
}

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
        historyNextOffset: 0,
        historyHasMore: false,
        isHistoryLoading: false,
        historyError: undefined,
        deletedGenerationIds: [],
    });
}

beforeEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    resetStore();
    playgroundApiMock.getHistory.mockResolvedValue([]);
    playgroundApiMock.getTemplates.mockResolvedValue([]);
    playgroundApiMock.deleteGeneration.mockResolvedValue(undefined);
    playgroundApiMock.saveToLibrary.mockResolvedValue(undefined);
});

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    localStorage.clear();
});

describe("Playground history state", () => {
    it("treats needs_confirmation and cancelled as terminal active states", () => {
        const active = generation({ id: "gen-active", status: "processing" });
        act(() => {
            usePlaygroundStore.getState().startGeneration(active);
            usePlaygroundStore.getState().updateGeneration({
                ...active,
                status: "needs_confirmation",
                attempts: [{ attempt_id: "attempt-a", ordinal: 1, status: "needs_confirmation", error: "confirm cost" }],
            });
        });

        expect(usePlaygroundStore.getState().activeGenerationIds).toEqual([]);
        expect(usePlaygroundStore.getState().isGenerating).toBe(false);
        expect(usePlaygroundStore.getState().history[0].status).toBe("needs_confirmation");

        act(() => {
            usePlaygroundStore.getState().startGeneration(generation({ id: "gen-cancel", status: "processing" }));
            usePlaygroundStore.getState().updateGeneration(generation({ id: "gen-cancel", status: "cancelled" }));
        });
        expect(usePlaygroundStore.getState().activeGenerationIds).toEqual([]);
    });

    it("merges slow history pages without overwriting active work or resurrecting local deletes", () => {
        const active = generation({ id: "gen-active", status: "processing", error: "live" });
        const deleted = generation({ id: "gen-deleted" });
        act(() => {
            usePlaygroundStore.getState().startGeneration(active);
            usePlaygroundStore.getState().setHistory([active, deleted]);
            usePlaygroundStore.getState().removeGeneration("gen-deleted");
            usePlaygroundStore.getState().mergeHistoryPage([
                generation({ id: "gen-active", status: "pending", error: "stale" }),
                generation({ id: "gen-deleted" }),
                generation({ id: "gen-old", created_at: "2026-10-12T00:00:00.000Z" }),
            ], { offset: 0, limit: 50, reset: true });
        });

        const state = usePlaygroundStore.getState();
        expect(state.history.map((item) => item.id)).toEqual(["gen-active", "gen-old"]);
        expect(state.history.find((item) => item.id === "gen-active")?.status).toBe("processing");
        expect(state.history.find((item) => item.id === "gen-active")?.error).toBe("live");
    });

    it("observes non-terminal generations returned by initial history with read-only polling", async () => {
        vi.useFakeTimers();
        playgroundApiMock.getHistory.mockResolvedValueOnce([
            apiGeneration({ id: "gen-history-processing", status: "processing" }),
        ]);
        playgroundApiMock.getGeneration.mockResolvedValue(apiGeneration({ id: "gen-history-processing", status: "processing" }));

        const view = render(<PlaygroundPage />);
        await act(async () => {
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
        expect(usePlaygroundStore.getState().activeGenerationIds).toEqual(["gen-history-processing"]);
        expect(vi.getTimerCount()).toBe(1);
        view.unmount();
        expect(vi.getTimerCount()).toBe(0);
    });

    it("serializes poll reads and resolves terminal confirmation without a status endpoint", async () => {
        vi.useFakeTimers();
        let resolveGeneration!: (value: ReturnType<typeof apiGeneration>) => void;
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-poll", status: "processing" }, request)
        ));
        playgroundApiMock.getGeneration.mockImplementationOnce(() => new Promise((resolve) => {
            resolveGeneration = resolve;
        }));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));
        await act(async () => {
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(usePlaygroundStore.getState().activeGenerationIds).toEqual(["gen-poll"]);

        await act(async () => {
            vi.advanceTimersByTime(2000);
            await Promise.resolve();
            vi.advanceTimersByTime(2000);
            await Promise.resolve();
        });
        expect(playgroundApiMock.getGeneration).toHaveBeenCalledTimes(1);

        await act(async () => {
            resolveGeneration(apiGeneration({ id: "gen-poll", status: "needs_confirmation" }));
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(usePlaygroundStore.getState().activeGenerationIds).toEqual([]);
        expect(usePlaygroundStore.getState().history[0].status).toBe("needs_confirmation");
        expect(vi.getTimerCount()).toBe(0);
    });

    it("does not resurrect a deleted generation from a late in-flight poll", async () => {
        vi.useFakeTimers();
        let resolveGeneration!: (value: ReturnType<typeof apiGeneration>) => void;
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-delete-race", status: "processing" }, request)
        ));
        playgroundApiMock.getGeneration.mockImplementationOnce(() => new Promise((resolve) => {
            resolveGeneration = resolve;
        }));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));
        await act(async () => {
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(usePlaygroundStore.getState().activeGenerationIds).toEqual(["gen-delete-race"]);

        await act(async () => {
            vi.advanceTimersByTime(2000);
            await Promise.resolve();
        });
        expect(playgroundApiMock.getGeneration).toHaveBeenCalledTimes(1);

        act(() => {
            usePlaygroundStore.getState().removeGeneration("gen-delete-race");
        });
        await act(async () => {
            resolveGeneration(apiGeneration({ id: "gen-delete-race", status: "completed" }));
            await Promise.resolve();
            await Promise.resolve();
            vi.advanceTimersByTime(2000);
            await Promise.resolve();
        });

        expect(usePlaygroundStore.getState().history.some((item) => item.id === "gen-delete-race")).toBe(false);
        expect(usePlaygroundStore.getState().activeGenerationIds).toEqual([]);
        expect(playgroundApiMock.getGeneration).toHaveBeenCalledTimes(1);
        expect(vi.getTimerCount()).toBe(0);
    });

    it("loads additional history through the existing limit and offset API", async () => {
        const firstPage = Array.from({ length: 50 }, (_, index) => apiGeneration({
            id: `gen-page-1-${index}`,
            created_at: `2026-10-13T00:00:${String(index).padStart(2, "0")}.000Z`,
        }));
        playgroundApiMock.getHistory
            .mockResolvedValueOnce(firstPage)
            .mockResolvedValueOnce([apiGeneration({ id: "gen-page-2", created_at: "2026-10-12T00:00:00.000Z" })]);

        render(<PlaygroundPage />);
        await waitFor(() => expect(usePlaygroundStore.getState().historyHasMore).toBe(true));

        fireEvent.click(screen.getByRole("button", { name: /results.loadMore/ }));

        await waitFor(() => expect(playgroundApiMock.getHistory).toHaveBeenLastCalledWith(50, 50));
        await waitFor(() => expect(usePlaygroundStore.getState().history.some((item) => item.id === "gen-page-2")).toBe(true));
        expect(usePlaygroundStore.getState().historyHasMore).toBe(false);
    });

    it("keeps a generated result when the initial history request resolves late", async () => {
        let resolveHistory!: (value: ReturnType<typeof apiGeneration>[]) => void;
        playgroundApiMock.getHistory.mockImplementationOnce(() => new Promise((resolve) => {
            resolveHistory = resolve;
        }));
        playgroundApiMock.generate.mockImplementationOnce(async (request: PlaygroundGenerateRequestBody) => (
            acceptedReceipt({ id: "gen-new", status: "completed" }, request)
        ));

        render(<PlaygroundPage />);
        fireEvent.click(screen.getByRole("button", { name: /compose.generate/ }));
        await waitFor(() => expect(usePlaygroundStore.getState().history[0]?.id).toBe("gen-new"));

        await act(async () => {
            resolveHistory([apiGeneration({ id: "gen-old", created_at: "2026-10-12T00:00:00.000Z" })]);
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(usePlaygroundStore.getState().history.map((item) => item.id)).toContain("gen-new");
        expect(usePlaygroundStore.getState().history.map((item) => item.id)).toContain("gen-old");
    });

    it("clears templates and detail visibility when the runtime owner changes", async () => {
        playgroundApiMock.getHistory.mockResolvedValueOnce([apiGeneration({ id: "gen-detail" })]);
        render(<PlaygroundPage />);
        await waitFor(() => expect(screen.getByText("glass city")).toBeTruthy());
        act(() => {
            usePlaygroundStore.getState().setTemplates([{
                id: "template-a",
                name: "Template A",
                category: "general",
                prompt: "old",
                created_at: "2026-10-13T00:00:00.000Z",
                updated_at: "2026-10-13T00:00:00.000Z",
            }]);
        });

        fireEvent.click(screen.getByText("glass city"));
        expect(screen.getByTestId("detail-panel")).toBeTruthy();
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000002" });
        });

        await waitFor(() => expect(usePlaygroundStore.getState().templates).toEqual([]));
        expect(screen.queryByTestId("detail-panel")).toBeNull();
    });
});
