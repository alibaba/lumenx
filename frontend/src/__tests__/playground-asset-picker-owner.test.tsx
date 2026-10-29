// @vitest-environment happy-dom
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AssetPickerModal from "@/components/modules/playground/AssetPickerModal";
import MediaInput from "@/components/modules/playground/MediaInput";
import { usePlaygroundStore } from "@/components/modules/playground/usePlaygroundStore";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";

const playgroundApiMock = vi.hoisted(() => ({
    getHistory: vi.fn(),
    uploadMedia: vi.fn(),
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
        params ? `${key}:${JSON.stringify(params)}` : key
    ),
}));

const storageA = "00000000-0000-4000-8000-000000000101";
const storageB = "00000000-0000-4000-8000-000000000102";

function resetStore() {
    usePlaygroundStore.setState({
        mode: "r2v",
        modelId: "wan2.7-r2v",
        prompt: "make it move",
        negativePrompt: "",
        inputMedia: [],
        parameters: {},
        batchSize: 1,
        modelPreferences: {},
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
        showTemplateModal: false,
        showHistoryDrawer: false,
    });
}

describe("Playground asset picker owner guard", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        vi.spyOn(console, "error").mockImplementation(() => undefined);
        localStorage.clear();
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA });
        resetStore();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        clearRuntimeEnvironment();
        localStorage.clear();
    });

    it("filters Kling history formats and keeps ordered references when picking a compatible asset", async () => {
        usePlaygroundStore.setState({ mode: "r2v", modelId: "kling-v3-r2v", inputMedia: ["output/original.png"] });
        playgroundApiMock.getHistory.mockResolvedValueOnce([{
            id: "gen-formats", status: "completed", input_media: ["output/source.webp"],
            outputs: [
                { id: "webp", media_path: "output/incompatible.webp", media_type: "image" },
                { id: "png", media_path: "output/compatible.PNG", media_type: "image" },
            ],
        }]);
        render(<MediaInput />);
        fireEvent.click(screen.getByRole("button", { name: "media.pickFromLibrary" }));
        expect(await screen.findByText("compatible.PNG")).toBeTruthy();
        expect(screen.queryByText("incompatible.webp")).toBeNull();
        expect(screen.queryByText("source.webp")).toBeNull();
        fireEvent.click(screen.getByText("compatible.PNG"));
        fireEvent.click(screen.getByRole("button", { name: "assetPicker.select" }));
        expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/original.png", "output/compatible.PNG"]);
    });

    it("keeps WebP history assets selectable for Vidu reference inputs", async () => {
        usePlaygroundStore.setState({ mode: "r2v", modelId: "viduq3-pro-r2v", inputMedia: ["output/original.png"] });
        playgroundApiMock.getHistory.mockResolvedValueOnce([{
            id: "gen-vidu-formats", status: "completed", input_media: ["output/source.webp"],
            outputs: [
                { id: "webp", media_path: "output/compatible.webp", media_type: "image" },
            ],
        }]);
        render(<MediaInput />);
        fireEvent.click(screen.getByRole("button", { name: "media.pickFromLibrary" }));
        expect(await screen.findByText("compatible.webp")).toBeTruthy();
        expect(screen.getByText("source.webp")).toBeTruthy();
        fireEvent.click(screen.getByText("compatible.webp"));
        fireEvent.click(screen.getByRole("button", { name: "assetPicker.select" }));
        expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/original.png", "output/compatible.webp"]);
    });

    it("ignores stale history from a previous open on the same modal instance", async () => {
        let resolveFirst!: (value: unknown[]) => void;
        let resolveSecond!: (value: unknown[]) => void;
        playgroundApiMock.getHistory
            .mockImplementationOnce(() => new Promise((resolve) => {
                resolveFirst = resolve;
            }))
            .mockImplementationOnce(() => new Promise((resolve) => {
                resolveSecond = resolve;
            }));

        const view = render(
            <AssetPickerModal isOpen onClose={vi.fn()} onSelect={vi.fn()} accept="image" />,
        );
        view.rerender(<AssetPickerModal isOpen={false} onClose={vi.fn()} onSelect={vi.fn()} accept="image" />);
        view.rerender(<AssetPickerModal isOpen onClose={vi.fn()} onSelect={vi.fn()} accept="image" />);

        await act(async () => {
            resolveSecond([
                {
                    id: "gen-new",
                    status: "completed",
                    input_media: [],
                    outputs: [
                        { id: "out-new", media_path: "output/new-owner.png", media_type: "image", saved_to_library: false },
                    ],
                },
            ]);
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(screen.getByText("new-owner.png")).toBeTruthy();

        await act(async () => {
            resolveFirst([
                {
                    id: "gen-old",
                    status: "completed",
                    input_media: [],
                    outputs: [
                        { id: "out-old", media_path: "output/old-owner.png", media_type: "image", saved_to_library: false },
                    ],
                },
            ]);
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(screen.queryByText("old-owner.png")).toBeNull();
        expect(screen.getByText("new-owner.png")).toBeTruthy();
    });

    it("does not show or add stale assets after picker owner changes before history resolves", async () => {
        let resolveHistory!: (value: unknown[]) => void;
        playgroundApiMock.getHistory.mockImplementationOnce(() => new Promise((resolve) => {
            resolveHistory = resolve;
        }));

        render(<MediaInput />);
        fireEvent.click(screen.getByRole("button", { name: "media.pickFromLibrary" }));
        expect(playgroundApiMock.getHistory).toHaveBeenCalledTimes(1);

        usePlaygroundStore.setState({ mode: "i2v", modelId: "wan2.7-i2v" });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: storageB });

        await act(async () => {
            resolveHistory([
                {
                    id: "gen-old",
                    status: "completed",
                    input_media: [],
                    outputs: [
                        { id: "out-old", media_path: "output/old-owner.png", media_type: "image", saved_to_library: false },
                    ],
                },
            ]);
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(screen.queryByText("old-owner.png")).toBeNull();
        expect(usePlaygroundStore.getState().inputMedia).toEqual([]);
    });
});
