// @vitest-environment happy-dom
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";

import { AtelierShell } from "@/components/atelier/v3/AtelierShell";
import type { AtelierNode, AtelierProject } from "@/lib/atelierTypes";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project.json";
import messages from "../../messages/en.json";

const captures = vi.hoisted(() => ({
  mediaComposer: [] as Array<{ targetAvailableRect: DOMRect | null; targetRect: DOMRect | null }>,
  generationPanel: [] as Array<{ targetAvailableRect: DOMRect | null; targetRect: DOMRect | null }>,
}));

vi.mock("@/components/atelier/v3/MediaComposer", () => ({
  MediaComposer: (props: { targetAvailableRect: DOMRect | null; targetRect: DOMRect | null }) => {
    captures.mediaComposer.push({ targetAvailableRect: props.targetAvailableRect, targetRect: props.targetRect });
    return null;
  },
}));

vi.mock("@/components/atelier/v3/GenerationPanel", () => ({
  GenerationPanel: (props: { targetAvailableRect: DOMRect | null; targetRect: DOMRect | null }) => {
    captures.generationPanel.push({ targetAvailableRect: props.targetAvailableRect, targetRect: props.targetRect });
    return null;
  },
}));

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const baseFixture = fixture as unknown as AtelierProject;
const foundationMessages = messages;

function node(overrides: Partial<AtelierNode>): AtelierNode {
  const base = baseFixture.nodes[0];
  return {
    ...structuredClone(base),
    id: "node-1",
    project_id: "p-a",
    type: "image",
    title: "Image One",
    prompt: "",
    status: "completed",
    x: 10,
    y: 20,
    width: 260,
    height: 180,
    media_urls: ["uploads/image.png"],
    data: { filename: "Image One" },
    source_project_id: null,
    frame_id: null,
    asset_id: null,
    video_task_id: null,
    ...overrides,
  } as AtelierNode;
}

function project(id: string, revision: number, nodes: AtelierNode[] = []): AtelierProject {
  return {
    ...structuredClone(baseFixture),
    id,
    title: "Project A",
    revision,
    nodes: nodes.map(item => ({ ...structuredClone(item), project_id: id })),
    agent_turns: [],
    exports: [],
    sequence: [],
    audio_tracks: [],
    node_delete_receipts: [],
    node_upload_receipts: [],
    sequence_export_jobs: [],
    generation_attempts: [],
    generation_request_receipts: [],
  } as AtelierProject;
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function renderShell() {
  return render(
    <NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={foundationMessages}>
      <AtelierShell />
    </NextIntlClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  captures.mediaComposer.length = 0;
  captures.generationPanel.length = 0;
  clearRuntimeEnvironment();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
});

describe("Atelier composer layout shell integration", () => {
  it("reserves measured toolbar space in composer availableRect on a 1024x600 viewport", async () => {
    const current = project("p-a", 0, [node({ id: "image-1" })]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(jsonResponse([current])));

    renderShell();
    fireEvent.pointerDown(await screen.findByAltText("Image One"));
    const viewport = screen.getByTestId("atelier-canvas-viewport");
    const toolbar = screen.getByTestId("atelier-canvas-toolbar");
    vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, right: 1024, bottom: 600, width: 1024, height: 600, x: 0, y: 0, toJSON: () => ({}) });
    vi.spyOn(toolbar, "getBoundingClientRect").mockReturnValue({ left: 20, top: 20, right: 1000, bottom: 118, width: 980, height: 98, x: 20, y: 20, toJSON: () => ({}) });
    fireEvent.resize(window);

    await waitFor(() => expect(captures.mediaComposer.at(-1)?.targetAvailableRect?.top).toBe(130));
    expect(captures.mediaComposer.at(-1)?.targetAvailableRect?.height).toBe(470);
    expect(captures.generationPanel.at(-1)?.targetAvailableRect?.top).toBe(130);
  });
});
