// @vitest-environment happy-dom
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MediaInput from "@/components/modules/playground/MediaInput";
import PlaygroundPage from "@/components/modules/playground/PlaygroundPage";
import { usePlaygroundStore, type PlaygroundMode } from "@/components/modules/playground/usePlaygroundStore";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";

const playgroundApiMock = vi.hoisted(() => ({
    uploadMedia: vi.fn(),
    getCapabilities: vi.fn(),
    generate: vi.fn(),
    getRequestReceipt: vi.fn(),
    getHistory: vi.fn(),
    getTemplates: vi.fn(),
    getGeneration: vi.fn(),
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

vi.mock("@/components/modules/playground/AssetPickerModal", () => ({
    default: ({ isOpen, onSelect }: { isOpen: boolean; onSelect: (path: string) => void }) => (
        isOpen ? <><button type="button" onClick={() => onSelect("output/asset-picked.png")}>select asset</button><button type="button" onClick={() => onSelect("output/incompatible.webp")}>select incompatible asset</button></> : null
    ),
}));

vi.mock("@/components/modules/playground/ModeSelector", () => ({ default: () => <div data-testid="mode-selector" /> }));
vi.mock("@/components/modules/playground/ModelSelector", () => ({ default: () => <div data-testid="model-selector" /> }));
vi.mock("@/components/modules/playground/PromptInput", () => ({ default: () => <div data-testid="prompt-input" /> }));
vi.mock("@/components/modules/playground/ParameterBar", () => ({ default: () => <div data-testid="parameter-bar" /> }));
vi.mock("@/components/modules/playground/ResultGallery", () => ({ default: () => <div data-testid="result-gallery" /> }));

const storageId = "00000000-0000-4000-8000-000000000001";

function resetStore(overrides: Partial<{
    mode: PlaygroundMode;
    modelId: string;
    prompt: string;
    inputMedia: string[];
}> = {}) {
    usePlaygroundStore.setState({
        mode: overrides.mode ?? "r2v",
        modelId: overrides.modelId ?? "wan2.7-r2v",
        prompt: overrides.prompt ?? "make it move",
        negativePrompt: "",
        inputMedia: overrides.inputMedia ?? [],
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

function fileInput(container: HTMLElement): HTMLInputElement {
    const input = container.querySelector('input[type="file"]');
    if (!(input instanceof HTMLInputElement)) throw new Error("file input not found");
    return input;
}

function file(name: string, type = "image/png") {
    return new File(["x"], name, { type });
}

describe("Playground media limits", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        vi.spyOn(console, "error").mockImplementation(() => undefined);
        clearRuntimeEnvironment();
        activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        localStorage.clear();
        playgroundApiMock.getCapabilities.mockResolvedValue({ schema_version: 1, seedance: { available: true, reason: null, channel: 'mulerouter_http', input_kinds: ['image'] } });
        playgroundApiMock.getHistory.mockResolvedValue([]);
        playgroundApiMock.getTemplates.mockResolvedValue([]);
        playgroundApiMock.generate.mockResolvedValue({});
        playgroundApiMock.uploadMedia.mockResolvedValue({ path: "output/uploaded.png" });
        resetStore();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        clearRuntimeEnvironment();
        localStorage.clear();
    });

    it("rejects unsupported Kling uploads before transfer and keeps the existing reference", async () => {
        resetStore({ mode: "i2v", modelId: "kling-v3-i2v", inputMedia: ["output/original.png"] });
        const view = render(<MediaInput />);
        expect(fileInput(view.container).accept).toBe("image/jpeg,image/png");
        fireEvent.change(fileInput(view.container), { target: { files: [file("bad.webp", "image/webp")] } });
        await waitFor(() => expect(screen.getByText(/media.unsupportedImageType/)).toBeTruthy());
        expect(playgroundApiMock.uploadMedia).not.toHaveBeenCalled();
        expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/original.png"]);
    });

    it("uses the extension for a Kling file whose browser MIME is empty", async () => {
        resetStore({ mode: "i2v", modelId: "kling-v3-i2v", inputMedia: ["output/original.png"] });
        const view = render(<MediaInput />);
        fireEvent.change(fileInput(view.container), { target: { files: [file("bad.webp", "")] } });
        await waitFor(() => expect(screen.getByText(/media.unsupportedImageType/)).toBeTruthy());
        expect(playgroundApiMock.uploadMedia).not.toHaveBeenCalled();
        fireEvent.change(fileInput(view.container), { target: { files: [file("accepted.PNG", "")] } });
        await waitFor(() => expect(playgroundApiMock.uploadMedia).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/uploaded.png"]));
    });

    it.each(["viduq3-pro-r2v", "pixverse-c1-r2v", "pixverse-v5.6-r2v", "seedance-2.0-r2v"])("accepts WebP uploads and asset picks for %s catalog image inputs", async (modelId) => {
        resetStore({ mode: "r2v", modelId, inputMedia: ["output/original.png"] });
        const view = render(<MediaInput />);
        expect(fileInput(view.container).accept).toBe("image/jpeg,image/png,image/webp");

        fireEvent.change(fileInput(view.container), { target: { files: [file("accepted.webp", "")] } });
        await waitFor(() => expect(playgroundApiMock.uploadMedia).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/original.png", "output/uploaded.png"]));

        fireEvent.click(screen.getByRole("button", { name: "media.pickFromLibrary" }));
        fireEvent.click(screen.getByRole("button", { name: "select incompatible asset" }));
        expect(usePlaygroundStore.getState().inputMedia).toEqual([
            "output/original.png",
            "output/uploaded.png",
            "output/incompatible.webp",
        ]);
    });


    it('enforces the Seedance product upload byte boundary before HTTP', async () => {
        resetStore({ mode: 'i2v', modelId: 'seedance-2.0-i2v' });
        const view = render(<MediaInput />);
        const tooLarge = file('oversized.png');
        Object.defineProperty(tooLarge, 'size', { value: 20 * 1024 * 1024 + 1 });
        fireEvent.change(fileInput(view.container), { target: { files: [tooLarge] } });
        expect(playgroundApiMock.uploadMedia).not.toHaveBeenCalled();
        expect(screen.getByText(/media.imageTooLarge/)).toBeTruthy();
        const boundary = file('boundary.png');
        Object.defineProperty(boundary, 'size', { value: 20 * 1024 * 1024 });
        fireEvent.change(fileInput(view.container), { target: { files: [boundary] } });
        await waitFor(() => expect(playgroundApiMock.uploadMedia).toHaveBeenCalledTimes(1));
    });

    it('retains all ten Seedance references while explaining the nine-image limit', async () => {
        const refs = Array.from({ length: 10 }, (_, n) => `output/${n}.png`);
        resetStore({ mode: 'r2v', modelId: 'seedance-2.0-r2v', inputMedia: refs });
        render(<PlaygroundPage />);
        await screen.findByText('capabilities.seedance_image_count');
        expect(screen.getByRole('button', { name: /compose.generate/ })).toBeDisabled();
        expect(usePlaygroundStore.getState().inputMedia).toEqual(refs);
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
    });

    it("guards Kling library callbacks even when a picker returns an incompatible image", () => {
        resetStore({ modelId: "kling-v3-r2v", inputMedia: ["output/original.png"] });
        render(<MediaInput />);
        fireEvent.click(screen.getByRole("button", { name: "media.pickFromLibrary" }));
        fireEvent.click(screen.getByRole("button", { name: "select incompatible asset" }));
        expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/original.png"]);
        expect(screen.getByText(/media.unsupportedImageType/)).toBeTruthy();
    });

    it("blocks Kling reference overflow without dropping or reordering the user's images", () => {
        const refs = Array.from({ length: 8 }, (_, index) => `output/${index}.png`);
        resetStore({ modelId: "kling-v3-r2v", inputMedia: refs });
        render(<PlaygroundPage />);
        const generateButton = screen.getByRole("button", { name: /compose.generate/ });
        expect(generateButton).toHaveAttribute("disabled");
        fireEvent.click(generateButton);
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
        expect(usePlaygroundStore.getState().inputMedia).toEqual(refs);
        expect(screen.getAllByText(/media.limitExceeded/).length).toBeGreaterThan(0);
    });

    it("blocks Wan 2.7 R2V generation when references exceed catalog maxReferenceImages", async () => {
        resetStore({ inputMedia: ["a.png", "b.png", "c.png", "d.png", "e.png", "f.png"] });

        render(<PlaygroundPage />);
        await act(async () => {
            await Promise.resolve();
        });

        expect(screen.getAllByText(/media.limitExceeded/).length).toBeGreaterThan(0);
        const generateButton = screen.getByRole("button", { name: /compose.generate/ });
        expect(generateButton).toHaveAttribute("disabled");
        fireEvent.click(generateButton);
        expect(playgroundApiMock.generate).not.toHaveBeenCalled();
        expect(usePlaygroundStore.getState().inputMedia).toHaveLength(6);
    });

    it("blocks Seedance mixed AV drafts without dropping their references", () => {
        resetStore({
            modelId: "seedance-2.0-r2v",
            inputMedia: ["a.png", "b.mp4", "c.mp3", "d.png", "e.mov", "f.wav"],
        });

        render(<PlaygroundPage />);

        expect(screen.queryByText(/media.limitExceeded/)).toBeNull();
        expect(screen.getByRole("button", { name: /compose.generate/ })).toHaveAttribute("disabled");
        expect(usePlaygroundStore.getState().inputMedia).toHaveLength(6);
    });

    it("caps Wan 2.7 R2V uploads and asset picks at the catalog image limit", async () => {
        resetStore({ inputMedia: ["a.png", "b.png", "c.png", "d.png"] });
        playgroundApiMock.uploadMedia.mockResolvedValueOnce({ path: "output/e.png" });
        const view = render(<MediaInput />);

        fireEvent.change(fileInput(view.container), { target: { files: [file("e.png"), file("f.png")] } });

        await waitFor(() => expect(playgroundApiMock.uploadMedia).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(usePlaygroundStore.getState().inputMedia).toEqual(["a.png", "b.png", "c.png", "d.png", "output/e.png"]));
        expect(screen.getByText(/media.limitReached/)).toBeTruthy();

        fireEvent.click(screen.getByRole("button", { name: "media.pickFromLibrary" }));
        fireEvent.click(screen.getByRole("button", { name: "select asset" }));

        expect(usePlaygroundStore.getState().inputMedia).toEqual(["a.png", "b.png", "c.png", "d.png", "output/e.png"]);
        expect(screen.getByText(/media.limitReached/)).toBeTruthy();
    });

    it("replaces an existing single-reference file instead of clearing it", async () => {
        resetStore({ mode: "i2v", modelId: "wan2.7-i2v", inputMedia: ["output/old.png"] });
        playgroundApiMock.uploadMedia.mockResolvedValueOnce({ path: "output/new.png" });
        const view = render(<MediaInput />);

        fireEvent.change(fileInput(view.container), { target: { files: [file("new.png")] } });

        await waitFor(() => expect(playgroundApiMock.uploadMedia).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/new.png"]));
    });

    it("shows upload errors and preserves existing media", async () => {
        resetStore({ inputMedia: ["output/original.png"] });
        playgroundApiMock.uploadMedia.mockRejectedValueOnce(new Error("network"));
        const view = render(<MediaInput />);

        fireEvent.change(fileInput(view.container), { target: { files: [file("new.png")] } });

        await waitFor(() => expect(screen.getByText("media.uploadFailed")).toBeTruthy());
        expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/original.png"]);
    });


    it("releases busy state and ignores a same-runtime upload when mode or model changes", async () => {
        resetStore({ inputMedia: ["output/original.png"] });
        let resolveUpload!: (value: { path: string }) => void;
        playgroundApiMock.uploadMedia.mockImplementationOnce(() => new Promise((resolve) => {
            resolveUpload = resolve;
        }));
        const view = render(<MediaInput />);

        fireEvent.change(fileInput(view.container), { target: { files: [file("late.png")] } });
        expect(screen.getByRole("button", { name: "media.uploading" })).toBeTruthy();

        act(() => {
            usePlaygroundStore.setState({ mode: "i2v", modelId: "wan2.7-i2v" });
        });

        expect(screen.queryByRole("button", { name: "media.uploading" })).toBeNull();
        await act(async () => {
            resolveUpload({ path: "output/late.png" });
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/original.png"]);
    });

    it("rejects a late upload after switching away and back to the same mode and model", async () => {
        resetStore({ inputMedia: ["output/original.png"] });
        let resolveUpload!: (value: { path: string }) => void;
        playgroundApiMock.uploadMedia.mockImplementationOnce(() => new Promise((resolve) => {
            resolveUpload = resolve;
        }));
        const view = render(<MediaInput />);

        fireEvent.change(fileInput(view.container), { target: { files: [file("late.png")] } });
        act(() => {
            usePlaygroundStore.setState({ mode: "i2v", modelId: "wan2.7-i2v" });
            usePlaygroundStore.setState({ mode: "r2v", modelId: "wan2.7-r2v" });
        });
        await act(async () => {
            resolveUpload({ path: "output/late.png" });
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/original.png"]);
    });

    it("ignores late upload responses after mode, model, runtime, or mount ownership changes", async () => {
        resetStore({ inputMedia: ["output/original.png"] });
        let resolveUpload!: (value: { path: string }) => void;
        playgroundApiMock.uploadMedia.mockImplementationOnce(() => new Promise((resolve) => {
            resolveUpload = resolve;
        }));
        const view = render(<MediaInput />);

        fireEvent.change(fileInput(view.container), { target: { files: [file("late.png")] } });
        usePlaygroundStore.setState({ mode: "i2v", modelId: "wan2.7-i2v" });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000002" });
        view.unmount();
        resolveUpload({ path: "output/late.png" });

        await waitFor(() => expect(playgroundApiMock.uploadMedia).toHaveBeenCalledTimes(1));
        expect(usePlaygroundStore.getState().inputMedia).toEqual(["output/original.png"]);
    });
});
