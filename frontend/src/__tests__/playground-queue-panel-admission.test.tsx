// @vitest-environment happy-dom
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import QueuePanel from "@/components/modules/playground/QueuePanel";
import { usePlaygroundStore, type QueuedRequest } from "@/components/modules/playground/usePlaygroundStore";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import {
    beginPlaygroundGenerationRequest,
    listPlaygroundGenerationRequests,
} from "@/lib/playgroundGenerationRequest";

vi.mock("next-intl", () => ({
    useTranslations: () => {
        const messages: Record<string, string> = {
            "queue.cancel": "Cancel",
            "queue.label": "Queue",
            "queue.refreshReadback": "Check status again",
        };
        return (key: string, params?: Record<string, unknown>) => (
            params?.count !== undefined ? `${key}:${params.count}` : messages[key] ?? key
        );
    },
}));

const storageId = "00000000-0000-4000-8000-000000000001";

beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    clearRuntimeEnvironment();
    const environment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
    const journal = beginPlaygroundGenerationRequest({
        environment,
        mode: "t2i",
        modelId: "wanx2.1-t2i",
        prompt: "queued while saturated",
        storage: localStorage,
        idFactory: () => "playground-saturated",
    });
    usePlaygroundStore.setState({
        queue: [{
            id: "q-saturated",
            clientRequestId: journal.clientRequestId,
            requestFingerprint: journal.requestFingerprint,
            body: journal.request,
            journal,
            status: "pending",
            enqueuedAt: 1,
        } satisfies QueuedRequest],
        activeGenerationIds: ["gen-running"],
        maxConcurrent: 1,
    });
});

afterEach(() => {
    vi.restoreAllMocks();
    clearRuntimeEnvironment();
    localStorage.clear();
});

describe("Playground queue panel admission controls", () => {
    it("lets only unsubmitted pending queue entries cancel and clears their journal", () => {
        const environment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        expect(listPlaygroundGenerationRequests({ environment, storage: localStorage }).map(item => item.clientRequestId))
            .toEqual(["playground-saturated"]);

        render(<QueuePanel />);
        fireEvent.click(screen.getByRole("button", { name: /Queue/ }));
        fireEvent.click(screen.getByTitle("Cancel"));

        expect(usePlaygroundStore.getState().queue).toEqual([]);
        expect(listPlaygroundGenerationRequests({ environment, storage: localStorage })).toEqual([]);
    });

    it("does not expose ordinary cancel for unknown readback entries", () => {
        usePlaygroundStore.setState({
            queue: usePlaygroundStore.getState().queue.map(item => ({
                ...item,
                status: "needs_readback" as const,
            })),
        });

        render(<QueuePanel />);
        fireEvent.click(screen.getByRole("button", { name: /Queue/ }));

        expect(screen.queryByTitle("Cancel")).toBeNull();
        expect(screen.getByTitle("Check status again")).toBeInTheDocument();
    });
});
