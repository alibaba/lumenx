// @vitest-environment happy-dom
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DetailPanel from "@/components/modules/playground/DetailPanel";
import ResultCard from "@/components/modules/playground/ResultCard";
import ResultGallery from "@/components/modules/playground/ResultGallery";
import { usePlaygroundStore, type PlaygroundGeneration } from "@/components/modules/playground/usePlaygroundStore";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";

const playgroundApiMock = vi.hoisted(() => ({
    saveToLibrary: vi.fn(),
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
    useTranslations: () => (key: string) => key,
}));

function generation(overrides: Partial<PlaygroundGeneration> = {}): PlaygroundGeneration {
    return {
        id: "gen-actions",
        mode: "t2i",
        model_id: "wanx2.1-t2i",
        prompt: "glass city",
        input_media: [],
        parameters: {},
        batch_size: 1,
        outputs: [
            { id: "out-a", media_path: "output/a.png", media_type: "image", saved_to_library: false },
            { id: "out-b", media_path: "output/b.png", media_type: "image", saved_to_library: true },
        ],
        status: "completed",
        created_at: "2026-10-13T00:00:00.000Z",
        ...overrides,
    };
}

function resetStore(gen = generation()) {
    usePlaygroundStore.setState({
        history: [gen],
        activeGenerationIds: [],
        isGenerating: false,
        queue: [],
        featuredByGen: {},
    });
}

describe("Playground result actions", () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        vi.spyOn(console, "error").mockImplementation(() => undefined);
        playgroundApiMock.saveToLibrary.mockResolvedValue(undefined);
        playgroundApiMock.deleteGeneration.mockResolvedValue(undefined);
        clearRuntimeEnvironment();
        vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => undefined);
        resetStore();
    });

    it("uses the focused output saved state in DetailPanel", () => {
        const gen = generation();
        render(
            <DetailPanel
                generation={gen}
                allGenerations={[gen]}
                focusOutputId="out-b"
                onClose={vi.fn()}
                onNavigate={vi.fn()}
            />,
        );

        expect(screen.getByRole("button", { name: /detail.savedCancel/ })).toBeTruthy();
    });

    it("does not unsave locally when ResultCard save is clicked for an already saved output", async () => {
        const gen = generation();
        resetStore(gen);
        render(<ResultCard generation={gen} outputIndex={1} />);

        fireEvent.click(screen.getByTitle("card.saved"));

        await waitFor(() => expect(usePlaygroundStore.getState().history[0].outputs[1].saved_to_library).toBe(true));
        expect(playgroundApiMock.saveToLibrary).not.toHaveBeenCalled();
    });

    it("patches only the saved output against the latest generation after stale async save", async () => {
        const gen = generation({
            outputs: [
                { id: "out-a", media_path: "output/a.png", media_type: "image", saved_to_library: false },
                { id: "out-b", media_path: "output/b.png", media_type: "image", saved_to_library: false },
            ],
        });
        resetStore(gen);
        let resolveSave!: () => void;
        playgroundApiMock.saveToLibrary.mockImplementationOnce(() => new Promise<void>((resolve) => {
            resolveSave = resolve;
        }));
        render(<ResultCard generation={gen} outputIndex={0} />);

        fireEvent.click(screen.getByTitle("card.saveToLibrary"));
        usePlaygroundStore.getState().updateGeneration({
            ...gen,
            outputs: [
                gen.outputs[0],
                { ...gen.outputs[1], saved_to_library: true },
                { id: "out-c", media_path: "output/c.png", media_type: "image", saved_to_library: false },
            ],
        });
        resolveSave();

        await waitFor(() => expect(usePlaygroundStore.getState().history[0].outputs.map((o) => o.id)).toEqual(["out-a", "out-b", "out-c"]));
        expect(usePlaygroundStore.getState().history[0].outputs.find((o) => o.id === "out-a")?.saved_to_library).toBe(true);
        expect(usePlaygroundStore.getState().history[0].outputs.find((o) => o.id === "out-b")?.saved_to_library).toBe(true);
    });

    it("ignores a save ACK after the runtime owner changes", async () => {
        const gen = generation({
            outputs: [
                { id: "out-a", media_path: "output/a.png", media_type: "image", saved_to_library: false },
            ],
        });
        resetStore(gen);
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000001" });
        let resolveSave!: () => void;
        playgroundApiMock.saveToLibrary.mockImplementationOnce(() => new Promise<void>((resolve) => {
            resolveSave = resolve;
        }));
        render(<ResultCard generation={gen} outputIndex={0} />);

        fireEvent.click(screen.getByTitle("card.saveToLibrary"));
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000002" });
        resolveSave();

        await waitFor(() => expect(playgroundApiMock.saveToLibrary).toHaveBeenCalledTimes(1));
        expect(usePlaygroundStore.getState().history[0].outputs[0].saved_to_library).toBe(false);
    });

    it("keeps ResultCard save ACK active under React StrictMode effect replay", async () => {
        const gen = generation({
            outputs: [
                { id: "out-a", media_path: "output/a.png", media_type: "image", saved_to_library: false },
            ],
        });
        resetStore(gen);
        render(
            <React.StrictMode>
                <ResultCard generation={gen} outputIndex={0} />
            </React.StrictMode>,
        );

        fireEvent.click(screen.getByTitle("card.saveToLibrary"));

        await waitFor(() => expect(usePlaygroundStore.getState().history[0].outputs[0].saved_to_library).toBe(true));
    });

    it("keeps a new ResultCard save pending when an old runtime ACK arrives late", async () => {
        const genA = generation({
            id: "gen-a",
            outputs: [{ id: "out-a", media_path: "output/a.png", media_type: "image", saved_to_library: false }],
        });
        const genB = generation({
            id: "gen-b",
            outputs: [{ id: "out-b", media_path: "output/b.png", media_type: "image", saved_to_library: false }],
        });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000001" });
        resetStore(genA);
        usePlaygroundStore.setState({ history: [genA, genB] });
        let resolveOldSave!: () => void;
        let resolveNewSave!: () => void;
        playgroundApiMock.saveToLibrary
            .mockImplementationOnce(() => new Promise<void>((resolve) => {
                resolveOldSave = resolve;
            }))
            .mockImplementationOnce(() => new Promise<void>((resolve) => {
                resolveNewSave = resolve;
            }));
        const view = render(<ResultCard generation={genA} outputIndex={0} />);

        fireEvent.click(screen.getByTitle("card.saveToLibrary"));
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000002" });
        view.rerender(<ResultCard generation={genB} outputIndex={0} />);
        fireEvent.click(screen.getByTitle("card.saveToLibrary"));

        resolveOldSave();
        await Promise.resolve();
        expect(usePlaygroundStore.getState().history.find((item) => item.id === "gen-a")?.outputs[0].saved_to_library).toBe(false);
        expect(usePlaygroundStore.getState().history.find((item) => item.id === "gen-b")?.outputs[0].saved_to_library).toBe(false);

        resolveNewSave();
        await waitFor(() => expect(usePlaygroundStore.getState().history.find((item) => item.id === "gen-b")?.outputs[0].saved_to_library).toBe(true));
    });

    it("keeps a new DetailPanel delete pending when an old runtime ACK arrives late", async () => {
        const genA = generation({ id: "gen-a" });
        const genB = generation({ id: "gen-b", prompt: "new focus" });
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000001" });
        resetStore(genA);
        usePlaygroundStore.setState({ history: [genA, genB] });
        let resolveOldDelete!: () => void;
        let resolveNewDelete!: () => void;
        playgroundApiMock.deleteGeneration
            .mockImplementationOnce(() => new Promise<void>((resolve) => {
                resolveOldDelete = resolve;
            }))
            .mockImplementationOnce(() => new Promise<void>((resolve) => {
                resolveNewDelete = resolve;
            }));
        const onClose = vi.fn();
        const view = render(
            <DetailPanel generation={genA} allGenerations={[genA, genB]} onClose={onClose} onNavigate={vi.fn()} />,
        );

        fireEvent.click(screen.getByRole("button", { name: /Delete/ }));
        activateRuntimeEnvironment({ schema_version: 1, storage_id: "00000000-0000-4000-8000-000000000002" });
        view.rerender(
            <DetailPanel generation={genB} allGenerations={[genA, genB]} onClose={onClose} onNavigate={vi.fn()} />,
        );
        await waitFor(() => expect(screen.getByRole("button", { name: /Delete/ })).toBeTruthy());
        fireEvent.click(screen.getByRole("button", { name: /Delete/ }));

        resolveOldDelete();
        await Promise.resolve();
        expect(usePlaygroundStore.getState().history.map((item) => item.id)).toEqual(["gen-a", "gen-b"]);
        expect(screen.getByRole("button", { name: /Deleting/ })).toBeTruthy();

        resolveNewDelete();
        await waitFor(() => expect(usePlaygroundStore.getState().history.map((item) => item.id)).toEqual(["gen-a"]));
        expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("removes a generation from history after DetailPanel delete succeeds", async () => {
        const gen = generation();
        resetStore(gen);
        const onClose = vi.fn();
        render(
            <DetailPanel
                generation={gen}
                allGenerations={[gen]}
                onClose={onClose}
                onNavigate={vi.fn()}
            />,
        );

        fireEvent.click(screen.getByRole("button", { name: /Delete/ }));

        await waitFor(() => expect(usePlaygroundStore.getState().history).toEqual([]));
        expect(onClose).toHaveBeenCalled();
    });

    it("removes only the deleted generation and does not close after navigating during pending delete", async () => {
        const genA = generation({ id: "gen-a" });
        const genB = generation({ id: "gen-b", prompt: "new focus" });
        resetStore(genA);
        usePlaygroundStore.setState({ history: [genA, genB] });
        let resolveDelete!: () => void;
        playgroundApiMock.deleteGeneration.mockImplementationOnce(() => new Promise<void>((resolve) => {
            resolveDelete = resolve;
        }));
        const onClose = vi.fn();
        const view = render(
            <DetailPanel
                generation={genA}
                allGenerations={[genA, genB]}
                onClose={onClose}
                onNavigate={vi.fn()}
            />,
        );

        fireEvent.click(screen.getByRole("button", { name: /Delete/ }));
        view.rerender(
            <DetailPanel
                generation={genB}
                allGenerations={[genA, genB]}
                onClose={onClose}
                onNavigate={vi.fn()}
            />,
        );
        resolveDelete();

        await waitFor(() => expect(usePlaygroundStore.getState().history.map((item) => item.id)).toEqual(["gen-b"]));
        expect(onClose).not.toHaveBeenCalled();
    });

    it("does not show a stale delete failure after navigating to another generation", async () => {
        const genA = generation({ id: "gen-a" });
        const genB = generation({ id: "gen-b", prompt: "new focus" });
        resetStore(genA);
        usePlaygroundStore.setState({ history: [genA, genB] });
        let rejectDelete!: (error: Error) => void;
        playgroundApiMock.deleteGeneration.mockImplementationOnce(() => new Promise<void>((_resolve, reject) => {
            rejectDelete = reject;
        }));
        const view = render(
            <DetailPanel
                generation={genA}
                allGenerations={[genA, genB]}
                onClose={vi.fn()}
                onNavigate={vi.fn()}
            />,
        );

        fireEvent.click(screen.getByRole("button", { name: /Delete/ }));
        view.rerender(
            <DetailPanel
                generation={genB}
                allGenerations={[genA, genB]}
                onClose={vi.fn()}
                onNavigate={vi.fn()}
            />,
        );
        rejectDelete(new Error("delete failed"));

        await waitFor(() => expect(playgroundApiMock.deleteGeneration).toHaveBeenCalledTimes(1));
        expect(screen.queryByText("detail.deleteFailed")).toBeNull();
    });

    it("keeps successful outputs visible when a generation needs confirmation", () => {
        const gen = generation({
            status: "needs_confirmation",
            outputs: [
                { id: "out-a", media_path: "output/a.png", media_type: "image", saved_to_library: false },
            ],
            attempts: [{ attempt_id: "attempt-a", ordinal: 1, status: "needs_confirmation", error: "confirm cost" }],
        });

        render(<ResultCard generation={gen} outputIndex={0} />);

        expect(screen.getByText("card.needsConfirmation")).toBeTruthy();
        expect(screen.getByTitle("card.saveToLibrary")).toBeTruthy();
    });

    it("keeps terminal partial outputs visible in gallery mode", () => {
        const gen = generation({
            status: "needs_confirmation",
            outputs: [
                { id: "out-a", media_path: "output/a.png", media_type: "image", saved_to_library: false },
            ],
            attempts: [{ attempt_id: "attempt-a", ordinal: 1, status: "needs_confirmation", error: "confirm cost" }],
        });
        resetStore(gen);

        render(<ResultGallery />);
        fireEvent.click(screen.getByTitle("results.galleryView"));

        expect(screen.getByText("card.needsConfirmation")).toBeTruthy();
        expect(document.querySelector('img[src$=\"a.png\"]')).toBeTruthy();
        expect(screen.queryByText("card.processing")).toBeNull();
    });

    it("shows delete failure in DetailPanel and keeps the generation visible", async () => {
        const gen = generation();
        resetStore(gen);
        playgroundApiMock.deleteGeneration.mockRejectedValueOnce(new Error("delete failed"));
        render(
            <DetailPanel
                generation={gen}
                allGenerations={[gen]}
                onClose={vi.fn()}
                onNavigate={vi.fn()}
            />,
        );

        fireEvent.click(screen.getByRole("button", { name: /Delete/ }));

        await waitFor(() => expect(screen.getByText("detail.deleteFailed")).toBeTruthy());
        expect(usePlaygroundStore.getState().history).toHaveLength(1);
    });
});
