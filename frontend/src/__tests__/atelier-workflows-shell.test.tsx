// @vitest-environment happy-dom
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";

import { AtelierShell } from "@/components/atelier/v3/AtelierShell";
import { BUILT_IN_WORKFLOW_TEMPLATES, WORKFLOW_TEMPLATE_MIME } from "@/components/atelier/v3/workflowTemplates";
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
const STORAGE_B = "22222222-2222-4222-8222-222222222222";
const baseFixture = fixture as unknown as AtelierProject;
const foundationMessages = messages;

type FetchCall = Parameters<ReturnType<typeof vi.fn>>[number];

function node(overrides: Partial<AtelierNode>): AtelierNode {
  const base = baseFixture.nodes[0];
  return {
    ...structuredClone(base),
    id: "node-1",
    project_id: "p-a",
    type: "idea",
    title: "Node 1",
    prompt: "",
    status: "draft",
    x: 10,
    y: 20,
    width: 260,
    height: 160,
    media_urls: [],
    data: { body: "Body" },
    ...overrides,
  } as AtelierNode;
}

function project(id: string, revision: number, nodes: AtelierNode[] = []): AtelierProject {
  return {
    ...structuredClone(baseFixture),
    id,
    title: id === "p-a" ? "Project A" : "Project B",
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}

function renderShell() {
  return render(
    <NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={foundationMessages}>
      <AtelierShell />
    </NextIntlClientProvider>,
  );
}

function requestPath(call: FetchCall): string {
  return new URL(String(call[0])).pathname;
}

function requestBody(call: FetchCall): Record<string, unknown> {
  return JSON.parse(String(call[1]?.body ?? "{}"));
}

function productSkillsResponse(): Response {
  return jsonResponse([]);
}

function isProductSkillsPath(path: string): boolean {
  return path === "/atelier/agent/product_skills";
}

function nodeFromCreate(projectId: string, id: string, body: Record<string, unknown>): AtelierNode {
  return node({
    id,
    project_id: projectId,
    type: body.type as string,
    title: body.title as string,
    prompt: body.prompt as string,
    data: typeof body.body === "string" ? { body: body.body } : {},
    x: body.x as number,
    y: body.y as number,
    width: body.width as number,
    height: body.height as number,
    source_project_id: null,
    frame_id: null,
    asset_id: null,
    video_task_id: null,
  });
}

function createTemplateBackend(startProject: AtelierProject, options: { failAfterCreates?: number } = {}) {
  let current = startProject;
  let serial = 0;
  const fetch = vi.fn().mockImplementation(async (url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    const body = requestBody([url, init]);
    if (isProductSkillsPath(path)) return productSkillsResponse();
    if (path === "/atelier/projects" && (init.method ?? "GET") === "GET") return jsonResponse([current]);
    if (path.endsWith("/nodes") && init.method === "POST") {
      if (options.failAfterCreates !== undefined && serial >= options.failAfterCreates) return jsonResponse({ detail: "template create failed" }, 503);
      serial += 1;
      const next = nodeFromCreate(current.id, `created-${serial}`, body);
      current = { ...current, revision: (body.expected_revision as number) + 1, nodes: [...current.nodes, next] };
      return jsonResponse({ project: current, node_ids: [next.id] });
    }
    if (path.endsWith("/references/attach_pending")) {
      const targetId = path.match(/nodes\/([^/]+)\/references/)?.[1];
      const sourceId = body.source_node_id as string;
      current = {
        ...current,
        revision: (body.expected_revision as number) + 1,
        nodes: current.nodes.map(item => item.id === targetId ? {
          ...item,
          data: {
            ...item.data,
            reference_node_ids: [...((item.data.reference_node_ids as string[] | undefined) ?? []), sourceId],
            pending_reference_nodes: [
              ...((item.data.pending_reference_nodes as unknown[] | undefined) ?? []),
              { schema_version: 1, source_node_id: sourceId, source_kind: current.nodes.find(source => source.id === sourceId)?.type, status: "pending_media" },
            ],
          },
        } : item),
      } as AtelierProject;
      return jsonResponse({ project: current, node_ids: [targetId, sourceId] });
    }
    throw new Error(`unexpected ${path}`);
  });
  return { fetch, current: () => current };
}

beforeEach(() => {
  vi.restoreAllMocks();
  captures.mediaComposer.length = 0;
  captures.generationPanel.length = 0;
  clearRuntimeEnvironment();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
});

describe("Atelier workflow template shell integration", () => {
  it("inserts a built-in from the panel through create commands and pending references", async () => {
    const backend = createTemplateBackend(project("p-a", 0, []));
    vi.stubGlobal("fetch", backend.fetch);

    renderShell();
    await screen.findByText("Create a node to start the canvas.");
    fireEvent.click(screen.getByRole("button", { name: "Workflows" }));
    const panel = screen.getByRole("region", { name: "Workflows" });
    fireEvent.click(within(panel).getAllByRole("button", { name: "Insert" })[0]);

    const template = BUILT_IN_WORKFLOW_TEMPLATES[0];
    await waitFor(() => expect(backend.fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes"))).toHaveLength(template.nodes.length));
    await waitFor(() => expect(screen.getAllByTestId("atelier-pending-reference-edge")).toHaveLength(template.edges.length));
    expect(backend.fetch.mock.calls.some(call => requestPath(call).endsWith("/references/attach_pending"))).toBe(true);
    expect(requestBody(backend.fetch.mock.calls.find(call => requestPath(call).endsWith("/references/attach_pending"))!)).toMatchObject({ source_node_id: "created-3" });
    expect(screen.getByRole("alert")).toHaveTextContent("Workflow template inserted");
  });

  it("uses drag data id only, maps drop to canvas coordinates, and ignores unknown template ids", async () => {
    const backend = createTemplateBackend(project("p-a", 0, []));
    vi.stubGlobal("fetch", backend.fetch);

    renderShell();
    await screen.findByText("Create a node to start the canvas.");
    const viewport = screen.getByTestId("atelier-canvas-viewport");
    vi.spyOn(viewport, "getBoundingClientRect").mockReturnValue({ left: 10, top: 20, right: 1010, bottom: 620, width: 1000, height: 600, x: 10, y: 20, toJSON: () => ({}) });
    const drop = createEvent.drop(viewport);
    Object.defineProperty(drop, "clientX", { value: 110 });
    Object.defineProperty(drop, "clientY", { value: 220 });
    Object.defineProperty(drop, "dataTransfer", {
      value: { types: [WORKFLOW_TEMPLATE_MIME], getData: () => "story-seed-to-shot", dropEffect: "" },
    });
    fireEvent(viewport, drop);

    await waitFor(() => expect(backend.fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes"))).toHaveLength(BUILT_IN_WORKFLOW_TEMPLATES[0].nodes.length));
    expect(requestBody(backend.fetch.mock.calls.find(call => requestPath(call).endsWith("/nodes") && call[1]?.method === "POST")!)).toMatchObject({ x: 100, y: 200, type: "idea" });
    const callsAfterKnownDrop = backend.fetch.mock.calls.length;
    fireEvent.drop(viewport, {
      clientX: 130,
      clientY: 260,
      dataTransfer: { types: [WORKFLOW_TEMPLATE_MIME], getData: () => "missing-template", dropEffect: "" },
    });
    expect(backend.fetch.mock.calls).toHaveLength(callsAfterKnownDrop);
  });

  it("locks duplicate insert clicks and preserves partial nodes when a later create fails", async () => {
    const backend = createTemplateBackend(project("p-a", 0, []), { failAfterCreates: 1 });
    vi.stubGlobal("fetch", backend.fetch);

    renderShell();
    await screen.findByText("Create a node to start the canvas.");
    fireEvent.click(screen.getByRole("button", { name: "Workflows" }));
    const firstInsert = within(screen.getByRole("region", { name: "Workflows" })).getAllByRole("button", { name: "Insert" })[0];
    fireEvent.click(firstInsert);
    fireEvent.click(firstInsert);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Workflow template partially inserted"));
    expect(screen.getByText("Write the one-sentence emotional hook here.")).toBeInTheDocument();
    expect(backend.fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes"))).toHaveLength(2);
    expect(backend.fetch.mock.calls.some(call => requestPath(call).endsWith("/references/attach_pending"))).toBe(false);
  });

  it("does not continue template insertion after an A-B-A owner switch", async () => {
    const a = project("p-a", 0, []);
    const b = project("p-b", 0, []);
    const slowCreate = deferred<Response>();
    const fetch = vi.fn().mockImplementation(async (url: string, init: RequestInit = {}) => {
      const path = new URL(url).pathname;
      if (isProductSkillsPath(path)) return productSkillsResponse();
      if (path === "/atelier/projects" && (init.method ?? "GET") === "GET") return jsonResponse([a, b]);
      if (path.endsWith("/nodes") && init.method === "POST") return slowCreate.promise;
      if (path === "/atelier/projects/p-b") return jsonResponse(b);
      if (path === "/atelier/projects/p-a") return jsonResponse(a);
      throw new Error(`unexpected ${path}`);
    });
    vi.stubGlobal("fetch", fetch);

    renderShell();
    await screen.findByText("Project A");
    fireEvent.click(screen.getByRole("button", { name: "Workflows" }));
    fireEvent.click(within(screen.getByRole("region", { name: "Workflows" })).getAllByRole("button", { name: "Insert" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Project B" }));
    await screen.findByText("Project B");
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
    fireEvent.click(screen.getByRole("button", { name: "Project A" }));
    slowCreate.resolve(jsonResponse({ project: { ...a, revision: 1, nodes: [node({ id: "late", data: { body: "Late template node" } })] }, node_ids: ["late"] }));

    await waitFor(() => expect(screen.queryByText("Late template node")).not.toBeInTheDocument());
    expect(fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes"))).toHaveLength(1);
    expect(fetch.mock.calls.some(call => requestPath(call).endsWith("/references/attach_pending"))).toBe(false);
  });
});
