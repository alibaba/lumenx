// @vitest-environment happy-dom
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { AtelierShell } from "@/components/atelier/v3/AtelierShell";
import { beginAtelierAgentTurnRequest } from "@/lib/atelierAgentTurnRequest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierProject } from "@/lib/atelierTypes";
import fixture from "./fixtures/atelier-project.json";
import messages from "../../messages/en.json";

const STORAGE = "11111111-1111-4111-8111-111111111111";

function project(): AtelierProject {
    return {
        ...structuredClone(fixture),
        id: "p-a",
        title: "Project A",
        description: "",
        source_project_id: null,
        revision: 0,
        nodes: [],
        agent_turns: [],
        sequence: [],
        audio_tracks: [],
        exports: [],
        node_delete_receipts: [],
        node_upload_receipts: [],
        sequence_export_jobs: [],
        generation_attempts: [],
        generation_request_receipts: [],
        agent_compaction_summary: null,
        compacted_through_turn_id: null,
    } as unknown as AtelierProject;
}

function response(value: unknown): Response {
    return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

function renderShell() {
    return render(<NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={messages}>
        <AtelierShell />
    </NextIntlClientProvider>);
}

beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE });
});

describe("Atelier Agent shell integration", () => {
    it("shows project-level Agent recovery when no node is selected", async () => {
        beginAtelierAgentTurnRequest({
            environment: requireActiveRuntimeEnvironment(),
            projectId: "p-a",
            expectedRevision: 0,
            clientTurnId: "agent-turn-lost",
            userMessage: "Prepare a take",
            selectedNodeId: null,
        });
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([project()])));

        renderShell();

        expect(await screen.findByText("Project A")).toBeInTheDocument();
        expect(screen.getByText(/previous Agent request/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Read original turn" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Retry original request" })).toBeInTheDocument();
    });
});
