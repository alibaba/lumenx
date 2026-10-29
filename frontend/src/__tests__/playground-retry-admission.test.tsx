// @vitest-environment happy-dom
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { usePlaygroundStore, type PlaygroundGeneration } from "@/components/modules/playground/usePlaygroundStore";
import ResultGallery from "@/components/modules/playground/ResultGallery";

vi.mock("next-intl", () => ({
    useTranslations: () => (key: string, params?: Record<string, unknown>) => (
        params?.count !== undefined ? `${key}:${params.count}` : key
    ),
}));

vi.mock("@/components/modules/playground/ResultCard", () => ({
    default: ({ generation, onRetry }: { generation: PlaygroundGeneration; onRetry: (gen: PlaygroundGeneration) => void }) => (
        <button type="button" onClick={() => onRetry(generation)}>retry {generation.id}</button>
    ),
}));
vi.mock("@/components/modules/playground/GalleryView", () => ({ default: () => <div data-testid="gallery-view" /> }));
vi.mock("@/components/modules/playground/DetailPanel", () => ({ default: () => <div data-testid="detail-panel" /> }));
vi.mock("@/components/modules/playground/QueuePanel", () => ({ default: () => <div data-testid="queue-panel" /> }));

const storageId = "00000000-0000-4000-8000-000000000001";

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

beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    usePlaygroundStore.setState({
        history: [],
        activeGenerationIds: [],
        isGenerating: false,
        queue: [],
        maxConcurrent: 3,
    });
});

afterEach(() => {
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    localStorage.clear();
});

describe("Playground retry admission", () => {
    it("queues an explicit retry with a new client request id through the shared queue", () => {
        const failed = generation({ id: "gen-failed", status: "failed", error: "provider failed" });
        usePlaygroundStore.setState({ history: [failed] });

        render(<ResultGallery />);
        fireEvent.click(screen.getByRole("button", { name: /retry gen-failed/ }));

        const queued = usePlaygroundStore.getState().queue[0];
        expect(queued.clientRequestId).toMatch(/^playground-/);
        expect(queued.body.client_request_id).toBe(queued.clientRequestId);
        expect(queued.body.prompt).toBe(failed.prompt);
        expect(queued.clientRequestId).not.toBe(failed.id);
    });
});
