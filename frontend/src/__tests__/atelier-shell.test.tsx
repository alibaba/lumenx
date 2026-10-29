// @vitest-environment happy-dom
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";

import { AtelierShell } from "@/components/atelier/v3/AtelierShell";
import type { AtelierNode, AtelierProject } from "@/lib/atelierTypes";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import fixture from "./fixtures/atelier-project.json";
import history from "./fixtures/atelier-node-history.json";
import messages from "../../messages/en.json";

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const STORAGE_B = "22222222-2222-4222-8222-222222222222";

type BackendProject = AtelierProject;
type BackendNode = AtelierNode;
type FetchCall = Parameters<ReturnType<typeof vi.fn>>[number];
const baseFixture = fixture as unknown as BackendProject;
const historyFixture = history as unknown as {
  deleted: { project: BackendProject; receipt_id: string; node_ids: string[] };
  restored: { project: BackendProject; receipt_id: string; restored_node_ids: string[]; skips: unknown[] };
};

const foundationMessages = messages;

function node(overrides: Partial<BackendNode>): BackendNode {
  const base = baseFixture.nodes[0];
  return {
    ...structuredClone(base),
    id: "idea-1",
    project_id: "p-a",
    type: "idea",
    title: "Idea 1",
    prompt: "Prompt 1",
    status: "draft",
    x: 10,
    y: 20,
    width: 260,
    height: 160,
    media_urls: [],
    data: { body: "Initial body" },
    ...overrides,
  } as BackendNode;
}

function project(id: string, revision: number, nodes: BackendNode[] = []): BackendProject {
  return {
    ...structuredClone(baseFixture),
    id,
    title: id === "p-a" ? "Project A" : id === "p-b" ? "Project B" : "Created Project",
    revision,
    nodes: nodes.map(n => ({ ...structuredClone(n), project_id: id })),
    agent_turns: baseFixture.agent_turns.map(turn => ({ ...turn, project_id: id })),
    exports: baseFixture.exports.map(record => ({ ...record, project_id: id })),
    node_delete_receipts: [],
    generation_attempts: [],
    generation_request_receipts: [],
  } as BackendProject;
}

function rehomeProject(p: BackendProject, id: string, revision = p.revision): BackendProject {
  return {
    ...structuredClone(p),
    id,
    revision,
    nodes: p.nodes.map(n => ({ ...n, project_id: id })),
    agent_turns: p.agent_turns.map(turn => ({ ...turn, project_id: id })),
    exports: p.exports.map(record => ({ ...record, project_id: id })),
    generation_attempts: p.generation_attempts ?? [],
    generation_request_receipts: p.generation_request_receipts ?? [],
    node_delete_receipts: p.node_delete_receipts.map(receipt => ({
      ...receipt,
      project_id: id,
      base_revision: revision - 1,
      restore_base_revision: receipt.restored_at === null ? receipt.restore_base_revision : revision - 1,
      node_snapshots: receipt.node_snapshots.map(snapshot => ({
        ...snapshot,
        node: { ...snapshot.node, project_id: id },
      })),
    })),
  } as BackendProject;
}

function withNode(p: BackendProject, nextNode: BackendNode, revision: number): BackendProject {
  const nodes = p.nodes.some(n => n.id === nextNode.id)
    ? p.nodes.map(n => n.id === nextNode.id ? { ...structuredClone(nextNode), project_id: p.id } : n)
    : [...p.nodes, { ...structuredClone(nextNode), project_id: p.id }];
  return { ...structuredClone(p), revision, nodes } as BackendProject;
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

function bodyOfRequest(init: RequestInit): Record<string, unknown> {
  return JSON.parse(String(init.body ?? "{}"));
}

function productSkillsResponse(): Response {
  return jsonResponse([]);
}

function isProductSkillsPath(path: string): boolean {
  return path === "/atelier/agent/product_skills";
}

function callsFor(fetch: ReturnType<typeof vi.fn>, suffix: string): FetchCall[] {
  return fetch.mock.calls.filter(call => requestPath(call).endsWith(suffix));
}

beforeEach(() => {
  vi.restoreAllMocks();
  clearRuntimeEnvironment();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
});

describe("Atelier foundation shell", () => {
  it("connects reference controls through acknowledged commands and freezes unknown saves", async () => {
    let current = project("p-a", 0, [
      node({ id: "target", type: "image", title: "Target", media_urls: ["uploads/target.png"], data: { filename: "Target image" } }),
      node({ id: "source", type: "image", title: "Source", media_urls: ["uploads/source.png"], data: { filename: "Source image" } }),
    ]);
    const attach = deferred<Response>();
    let detachCount = 0;
    const fetch = vi.fn().mockResolvedValueOnce(jsonResponse([current])).mockImplementation(async (url: string) => {
      const path = new URL(url).pathname;
      if (isProductSkillsPath(path)) return productSkillsResponse();
      if (path.endsWith("/references/attach")) return attach.promise;
      if (path.endsWith("/references/detach")) {
        if (detachCount++ > 0) return jsonResponse({ detail: "Commit outcome unknown" }, 503);
        current = { ...current, revision: 2, nodes: current.nodes.map(n => n.id === "target"
          ? { ...n, data: { ...n.data, reference_node_ids: [], reference_image_urls: [] } }
          : { ...n, data: { ...n.data, parent_node_id: null } }) };
        return jsonResponse({ project: current, node_ids: ["target", "source"] });
      }
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal("fetch", fetch);
    renderShell();
    fireEvent.pointerDown(await screen.findByAltText("Target image"));
    fireEvent.change(screen.getByRole("combobox", { name: "Choose a source node" }), { target: { value: "source" } });
    fireEvent.click(screen.getByRole("button", { name: "Attach reference" }));
    expect(screen.getByRole("button", { name: "Attach reference" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Detach reference Source" })).not.toBeInTheDocument();
    current = { ...current, revision: 1, nodes: current.nodes.map(n => n.id === "target"
      ? { ...n, data: { ...n.data, reference_node_ids: ["source"], reference_image_urls: ["uploads/source.png"] } }
      : { ...n, data: { ...n.data, parent_node_id: "target" } }) };
    attach.resolve(jsonResponse({ project: current, node_ids: ["target", "source"] }));
    fireEvent.click(await screen.findByRole("button", { name: "Detach reference Source" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Detach reference Source" })).not.toBeInTheDocument());
    expect(requestBody(callsFor(fetch, "/references/attach")[0])).toEqual({ expected_revision: 0, source_node_id: "source" });
    expect(requestBody(callsFor(fetch, "/references/detach")[0])).toEqual({ expected_revision: 1, source_node_id: "source" });
    const { useAtelierStore } = await import("@/store/atelierStore");
    current = { ...current, nodes: current.nodes.map(n => n.id === "target" ? { ...n, data: { ...n.data, reference_image_urls: ["uploads/legacy.png"] } } : n) };
    useAtelierStore.setState({ currentProject: current });
    fireEvent.click(await screen.findByRole("button", { name: "Detach reference uploads/legacy.png" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Detach reference uploads/legacy.png" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save text" })).toBeDisabled();
    expect(callsFor(fetch, "/references/attach")).toHaveLength(1);
    expect(callsFor(fetch, "/references/detach")).toHaveLength(2);
  });

  it("blocks reference upload while a selected media node has active execution and re-enables after completion", async () => {
    let current = project("p-a", 0, [
      node({
        id: "target",
        type: "video",
        title: "Target",
        media_urls: ["uploads/target.mp4"],
        video_task_id: null,
        status: "completed",
        data: {
          filename: "Target video",
          candidates: [{ id: "candidate-1", status: "accepted" }],
        },
      }),
    ]);
    const fetch = vi.fn().mockResolvedValueOnce(jsonResponse([current])).mockImplementation(async (url: string) => {
      const path = new URL(url).pathname;
      if (isProductSkillsPath(path)) return productSkillsResponse();
      if (path.includes("/uploads/references/")) {
        current = { ...current, revision: current.revision + 1 };
        return jsonResponse({ project: current, node_ids: ["target", "source"], receipt_id: "receipt", replayed: false });
      }
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal("fetch", fetch);
    const { useAtelierStore } = await import("@/store/atelierStore");

    renderShell();
    await screen.findByText("Project A");
    fireEvent.pointerDown(screen.getByRole("button", { name: /Target video/ }));
    expect(screen.getByRole("button", { name: "Upload reference" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Upload reference" }));
    expect(fetch.mock.calls.filter(call => requestPath(call).includes("/uploads/references/"))).toHaveLength(0);

    current = {
      ...current,
      nodes: current.nodes.map(item => item.id === "target"
        ? { ...item, data: { ...item.data, candidates: [{ id: "candidate-1", status: "completed", video_url: "video/done.mp4" }] } }
        : item),
    };
    useAtelierStore.setState({ currentProject: current, projects: [current] });
    await waitFor(() => expect(screen.getByRole("button", { name: "Upload reference" })).not.toBeDisabled());
  });

  it("renders a disabled shell instead of throwing while runtime identity is absent", () => {
    clearRuntimeEnvironment();
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);

    renderShell();

    expect(screen.getByRole("link", { name: "Return to workspace" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Idea" })).toBeDisabled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("loads, ensures, creates and switches projects through the real store client", async () => {
    const a = project("p-a", 0, [
      node({ id: "idea-a", title: "Idea A", data: { body: "A body" } }),
      node({ id: "plan-a", type: "plan", title: "Plan A", data: { bullets: ["Beat one"] } }),
      node({ id: "draft-a", type: "draft", title: "Legacy draft", prompt: "Legacy prompt", data: {} }),
    ]);
    const b = project("p-b", 0, [node({ id: "comment-b", type: "comment", title: "Comment B", data: { body: "B body", author: "Bea" } })]);
    const created = project("p-new", 0, []);
    const fetch = vi.fn().mockImplementation(async (url: string, init: RequestInit = {}) => {
      const path = new URL(url).pathname;
      if (isProductSkillsPath(path)) return productSkillsResponse();
      if (path === "/atelier/projects" && (init.method ?? "GET") === "GET") return jsonResponse([a, b]);
      if (path === "/atelier/projects/p-b") return jsonResponse(b);
      if (path === "/atelier/projects" && init.method === "POST") return jsonResponse(created);
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal("fetch", fetch);

    renderShell();

    expect(await screen.findByText("Project A")).toBeInTheDocument();
    expect(screen.getByText("Plan A")).toBeInTheDocument();
    expect(screen.getByText("Legacy draft")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Project B" }));
    expect(await screen.findByText("B body")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(screen.getByText("Created Project")).toBeInTheDocument());

    expect(fetch.mock.calls.filter(call => !isProductSkillsPath(requestPath(call))).map(requestPath)).toEqual([
      "/atelier/projects",
      "/atelier/projects/p-b",
      "/atelier/projects",
    ]);
    expect(requestBody(fetch.mock.calls.find(call => requestPath(call) === "/atelier/projects" && call[1]?.method === "POST")!)).toEqual({ title: "Untitled Atelier" });
    expect(screen.getByRole("link", { name: "Return to workspace" })).toHaveAttribute("href", "#/workspace");
  });

  it("creates visible primitive nodes and saves text while preserving failed drafts", async () => {
    let current = project("p-a", 0, []);
    const idea = node({ id: "idea-1", type: "idea", title: "Idea", prompt: "", data: { body: "" } });
    const saved = node({ ...idea, title: "Renamed", prompt: "New prompt", data: { body: "Unsaved body" } });
    const fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse([current]))
      .mockImplementation(async (url: string, init: RequestInit) => {
        const path = new URL(url).pathname;
        if (isProductSkillsPath(path)) return productSkillsResponse();
        if (path.endsWith("/nodes") && init.method === "POST") {
          current = withNode(current, idea, 1);
          return jsonResponse({ project: current, node_ids: ["idea-1"] });
        }
        if (path.endsWith("/nodes/idea-1/text")) {
          const body = bodyOfRequest(init);
          if (current.revision === 1) return jsonResponse({ detail: "Revision conflict" }, 409);
          current = withNode(current, { ...saved, prompt: body.prompt as string, data: { body: body.prompt as string } }, current.revision + 1);
          return jsonResponse({ project: current, node_ids: ["idea-1"] });
        }
        if (path.endsWith("/atelier/projects/p-a")) {
          current = { ...current, revision: 2 };
          return jsonResponse(current);
        }
        throw new Error(`unexpected ${path}`);
      });
    vi.stubGlobal("fetch", fetch);

    renderShell();
    expect(await screen.findByText("Create a node to start the canvas.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Idea" }));
    fireEvent.pointerDown(await screen.findByText("Empty · double-click to write"));

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Renamed" } });
    expect(screen.queryByLabelText("Prompt")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Body"), { target: { value: "Unsaved body" } });
    fireEvent.click(screen.getByRole("button", { name: "Save text" }));

    await screen.findByText(/Revision conflict/);
    expect(screen.getByLabelText("Body")).toHaveValue("Unsaved body");
    expect(screen.getByRole("button", { name: "Comment" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.queryByText(/Revision conflict/)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Save text" }));
    await waitFor(() => expect(screen.getByText("Unsaved body")).toBeInTheDocument());

    const paths = fetch.mock.calls.map(requestPath);
    expect(paths.some(path => path.endsWith("/nodes/idea-1/body"))).toBe(false);
    const bodies = fetch.mock.calls.map(requestBody);
    expect(bodies.some(body => body.title === "Renamed" && body.prompt === "Unsaved body")).toBe(true);
  });



  it("creates an audio node from the toolbar", async () => {
    let current = project("p-a", 0, []);
    const audio = node({ id: "audio-1", type: "audio", title: "Audio 1", prompt: "", width: 260, height: 96, data: {}, media_urls: [] });
    const fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse([current]))
      .mockImplementation(async (url: string, init: RequestInit) => {
        const path = new URL(url).pathname;
        if (isProductSkillsPath(path)) return productSkillsResponse();
        if (path.endsWith("/nodes") && init.method === "POST") {
          current = withNode(current, audio, 1);
          return jsonResponse({ project: current, node_ids: ["audio-1"] });
        }
        throw new Error(`unexpected ${path}`);
      });
    vi.stubGlobal("fetch", fetch);

    renderShell();
    await screen.findByText("Create a node to start the canvas.");
    fireEvent.click(screen.getByRole("button", { name: "Audio" }));

    await waitFor(() => expect(requestBody(callsFor(fetch, "/nodes").find(call => call[1]?.method === "POST")!)).toMatchObject({
      expected_revision: 0,
      type: "audio",
      title: "Audio 1",
      width: 260,
      height: 96,
    }));
  });

  it("keeps later-created region backgrounds behind members without reordering persisted nodes", async () => {
    const current = project("p-a", 0, [
      node({ id: "member-1", type: "comment", data: { body: "First member", region_id: "region-1" } }),
      node({ id: "region-1", type: "region", title: "First region", data: { collapsed: false } }),
      node({ id: "member-2", type: "comment", data: { body: "Second member", region_id: "region-2" } }),
      node({ id: "member-3", type: "comment", data: { body: "Third member", region_id: "region-1" } }),
      node({ id: "region-2", type: "region", title: "Second region", data: { collapsed: false } }),
    ]);
    const before = structuredClone(current);
    const fetch = vi.fn().mockResolvedValueOnce(jsonResponse([current])).mockImplementation(async (url: string) => {
      const path = new URL(url).pathname;
      if (isProductSkillsPath(path)) return productSkillsResponse();
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal("fetch", fetch);

    renderShell();
    await screen.findByText("Third member");
    const canvas = screen.getByLabelText("Atelier canvas");
    const paintedNodes = Array.from(canvas.querySelectorAll('[data-region-id], [role="button"]'));
    expect(paintedNodes).toEqual([
      screen.getByRole("group", { name: "Region: First region" }),
      screen.getByRole("group", { name: "Region: Second region" }),
      screen.getByText("First member").closest('[role="button"]'),
      screen.getByText("Second member").closest('[role="button"]'),
      screen.getByText("Third member").closest('[role="button"]'),
    ]);
    const { useAtelierStore } = await import("@/store/atelierStore");
    expect(useAtelierStore.getState().currentProject?.nodes).toEqual(before.nodes);
    expect(current).toEqual(before);
    expect(fetch.mock.calls.every(call => !call[1]?.method || call[1].method === "GET")).toBe(true);
  });

  it("commits drag layout, region collapse, resize, delete and receipt restore through ACKs", async () => {
    let current = project("p-a", 0, [
      node({ id: "idea-1", type: "idea", title: "Idea", data: { body: "Drag me" }, x: 10, y: 20 }),
      node({ id: "region-1", type: "region", title: "Region One", x: 100, y: 120, width: 320, height: 180, data: { collapsed: false, color: "default" } }),
      node({ id: "member-1", type: "comment", title: "Member", data: { body: "Inside region", region_id: "region-1" }, x: 140, y: 160 }),
    ]);
    const fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse([current]))
      .mockImplementation(async (url: string, init: RequestInit) => {
        const path = new URL(url).pathname;
        if (isProductSkillsPath(path)) return productSkillsResponse();
        const body = requestBody([url, init]);
        if (path.endsWith("/nodes/idea-1/layout")) {
          current = withNode(current, { ...current.nodes[0], x: body.x as number, y: body.y as number }, (body.expected_revision as number) + 1);
          return jsonResponse({ project: current, node_ids: ["idea-1"] });
        }
        if (path.endsWith("/regions/region-1/appearance")) {
          current = withNode(current, { ...current.nodes[1], data: { ...current.nodes[1].data, collapsed: body.collapsed as boolean } }, (body.expected_revision as number) + 1);
          return jsonResponse({ project: current, node_ids: ["region-1"] });
        }
        if (path.endsWith("/nodes/move")) {
          current = withNode(current, { ...current.nodes[1], x: current.nodes[1].x + (body.dx as number), y: current.nodes[1].y + (body.dy as number) }, (body.expected_revision as number) + 1);
          current = withNode(current, { ...current.nodes[2], x: current.nodes[2].x + (body.dx as number), y: current.nodes[2].y + (body.dy as number) }, current.revision);
          return jsonResponse({ project: current, node_ids: body.node_ids as string[] });
        }
        if (path.endsWith("/nodes/region-1/layout")) {
          current = withNode(current, {
            ...current.nodes[1],
            x: typeof body.x === "number" ? body.x : current.nodes[1].x,
            y: typeof body.y === "number" ? body.y : current.nodes[1].y,
            width: typeof body.width === "number" ? body.width : current.nodes[1].width,
            height: typeof body.height === "number" ? body.height : current.nodes[1].height,
          }, (body.expected_revision as number) + 1);
          return jsonResponse({ project: current, node_ids: ["region-1"] });
        }
        if (path.endsWith("/nodes/delete")) {
          const expected = body.expected_revision as number;
          const requested = body.node_ids as string[];
          const deletedProject = rehomeProject(historyFixture.deleted.project as BackendProject, "p-a", expected + 1);
          deletedProject.node_delete_receipts = deletedProject.node_delete_receipts.map(receipt => ({ ...receipt, node_ids: requested }));
          return jsonResponse({
            ...historyFixture.deleted,
            node_ids: requested,
            project: deletedProject,
          });
        }
        if (path.endsWith("/nodes/restore")) {
          const expected = body.expected_revision as number;
          const restoredProject = rehomeProject(historyFixture.restored.project as BackendProject, "p-a", expected + 1);
          restoredProject.node_delete_receipts = restoredProject.node_delete_receipts.map(receipt => ({ ...receipt, node_ids: ["idea-1"] }));
          return jsonResponse({
            ...historyFixture.restored,
            restored_node_ids: ["idea-1"],
            project: restoredProject,
          });
        }
        throw new Error(`unexpected ${path}`);
      });
    vi.stubGlobal("fetch", fetch);

    renderShell();
    const canvas = await screen.findByLabelText("Atelier canvas");
    fireEvent.pointerDown(canvas, { clientX: 5, clientY: 5 });
    fireEvent.pointerMove(window, { clientX: 25, clientY: 35 });
    fireEvent.pointerUp(window);
    expect(canvas).toHaveStyle({ transform: "translate(20px, 30px) scale(1)" });

    const ideaCard = await screen.findByText("Drag me");
    fireEvent.pointerDown(ideaCard, { clientX: 10, clientY: 20 });
    fireEvent.pointerMove(window, { clientX: 40, clientY: 60 });
    fireEvent.pointerUp(window);
    await waitFor(() => expect(fetch.mock.calls.some(call => requestPath(call).endsWith("/nodes/idea-1/layout"))).toBe(true));

    fireEvent.pointerDown(within(canvas).getByText("Region One"), { clientX: 100, clientY: 120 });
    fireEvent.pointerMove(window, { clientX: 130, clientY: 150 });
    expect(screen.getByText("Inside region").closest('[role="button"]')).toHaveStyle({ transform: "translate(170px, 190px)" });
    fireEvent.pointerUp(window);
    await waitFor(() => expect(fetch.mock.calls.some(call => requestPath(call).endsWith("/nodes/move"))).toBe(true));
    expect(requestBody(fetch.mock.calls.find(call => requestPath(call).endsWith("/nodes/move"))!)).toMatchObject({ node_ids: ["region-1"], dx: 30, dy: 30 });

    fireEvent.click(screen.getByRole("button", { name: "Collapse region" }));
    await waitFor(() => expect(screen.queryByText("Inside region")).not.toBeInTheDocument());
    fireEvent.pointerDown(within(canvas).getByText("Region One"));
    fireEvent.click(screen.getByRole("button", { name: "Expand region" }));
    await screen.findByText("Inside region");
    fireEvent.pointerDown(within(canvas).getByText("Region One"));
    fireEvent.click(screen.getByRole("button", { name: "Collapse region" }));
    await waitFor(() => expect(fetch.mock.calls.some(call => requestPath(call).endsWith("/regions/region-1/appearance"))).toBe(true));
    fireEvent.pointerDown(within(canvas).getByText("Region One"));
    fireEvent.click(screen.getByRole("button", { name: "Expand region" }));
    await waitFor(() => expect(screen.getByTestId("region-handle-se")).toBeInTheDocument());
    fireEvent.pointerDown(screen.getByTestId("region-handle-se"), { clientX: 420, clientY: 300 });
    fireEvent.pointerMove(window, { clientX: 450, clientY: 330 });
    fireEvent.pointerUp(window);
    await waitFor(() => expect(fetch.mock.calls.some(call => requestPath(call).endsWith("/nodes/region-1/layout"))).toBe(true));
    const layoutCallsAfterSe = fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes/region-1/layout"));
    expect(requestBody(layoutCallsAfterSe.at(-1)!)).toMatchObject({ width: 350, height: 210 });

    fireEvent.pointerDown(within(canvas).getByText("Region One"));
    fireEvent.pointerDown(screen.getByTestId("region-handle-nw"), { clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 80, clientY: 70 });
    fireEvent.pointerUp(window);
    await waitFor(() => expect(fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes/region-1/layout"))).toHaveLength(2));
    const nwBody = requestBody(fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes/region-1/layout")).at(-1)!);
    expect(nwBody).toMatchObject({ x: 110, y: 120, width: 370, height: 240 });

    fireEvent.pointerDown(within(canvas).getByText("Region One"));
    fireEvent.pointerDown(screen.getByTestId("region-handle-nw"), { clientX: 80, clientY: 70 });
    fireEvent.pointerMove(window, { clientX: 500, clientY: 500 });
    fireEvent.pointerCancel(window);
    expect(fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes/region-1/layout"))).toHaveLength(2);

    fireEvent.pointerDown(screen.getByTestId("region-handle-nw"), { clientX: 80, clientY: 70 });
    fireEvent.pointerMove(window, { clientX: 500, clientY: 500 });
    fireEvent.pointerUp(window);
    await waitFor(() => expect(fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes/region-1/layout"))).toHaveLength(3));
    const clamped = requestBody(fetch.mock.calls.filter(call => requestPath(call).endsWith("/nodes/region-1/layout")).at(-1)!);
    expect(clamped).toMatchObject({ x: 360, y: 280, width: 120, height: 80 });
  });

  it("deletes a node only after ACK and restores an unconsumed server receipt", async () => {
    const original = rehomeProject(historyFixture.restored.project as BackendProject, "p-a");
    original.title = "Project A";
    const fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse([original]))
      .mockImplementation(async (url: string, init: RequestInit) => {
        const path = new URL(url).pathname;
        if (isProductSkillsPath(path)) return productSkillsResponse();
        const body = bodyOfRequest(init);
        if (path.endsWith("/nodes/delete")) {
          const expected = body.expected_revision as number;
          const requested = body.node_ids as string[];
          const deletedProject = rehomeProject(historyFixture.deleted.project as BackendProject, "p-a", expected + 1);
          deletedProject.node_delete_receipts = deletedProject.node_delete_receipts.map(receipt => ({
            ...receipt,
            node_ids: requested,
            node_snapshots: receipt.node_snapshots.filter(snapshot => requested.includes(snapshot.node.id)),
          }));
          return jsonResponse({ ...historyFixture.deleted, node_ids: requested, project: deletedProject });
        }
        if (path.endsWith("/nodes/restore")) {
          const expected = body.expected_revision as number;
          const restoredProject = rehomeProject(historyFixture.restored.project as BackendProject, "p-a", expected + 1);
          restoredProject.node_delete_receipts = restoredProject.node_delete_receipts.map(receipt => ({
            ...receipt,
            node_ids: ["region"],
            node_snapshots: receipt.node_snapshots.filter(snapshot => snapshot.node.id === "region"),
          }));
          return jsonResponse({ ...historyFixture.restored, restored_node_ids: ["region"], project: restoredProject });
        }
        throw new Error(`unexpected ${path}`);
      });
    vi.stubGlobal("fetch", fetch);

    renderShell();
    fireEvent.pointerDown(await screen.findByRole("group", { name: "Region: Region" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete node" }));
    expect(await screen.findByText("Deleted receipt available")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restore deleted nodes" }));
    await waitFor(() => expect(fetch.mock.calls.some(call => requestPath(call).endsWith("/nodes/restore"))).toBe(true));
  });


  it("does not let an old A create acknowledgement select after A to B to A navigation", async () => {
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
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal("fetch", fetch);

    renderShell();
    await screen.findByText("Project A");
    fireEvent.click(screen.getByRole("button", { name: "Idea" }));
    fireEvent.click(screen.getByRole("button", { name: "Project B" }));
    await screen.findByText("Project B");
    fireEvent.click(screen.getByRole("button", { name: "Project A" }));
    await screen.findByText("Create a node to start the canvas.");
    slowCreate.resolve(jsonResponse({
      project: withNode(a, node({ id: "late-idea", data: { body: "Late idea" } }), 1),
      node_ids: ["late-idea"],
    }));

    await waitFor(() => expect(screen.queryByDisplayValue("Late idea")).not.toBeInTheDocument());
    expect(screen.getByText("Select a node to edit it.")).toBeInTheDocument();
  });


  it("rejects stale environment edits and keeps the current project draft isolated", async () => {
    const a = project("p-a", 0, [node({ id: "shared", type: "idea", title: "A title", data: { body: "A body" } })]);
    const b = project("p-b", 0, [node({ id: "shared", type: "idea", title: "B title", data: { body: "B body" } })]);
    const slow = deferred<Response>();
    const fetch = vi.fn().mockImplementation(async (url: string, init: RequestInit = {}) => {
      const path = new URL(url).pathname;
      if (isProductSkillsPath(path)) return productSkillsResponse();
      if (path === "/atelier/projects" && (init.method ?? "GET") === "GET") return jsonResponse([a, b]);
      if (path.endsWith("/nodes/shared/text")) return slow.promise;
      if (path === "/atelier/projects/p-b") return jsonResponse(b);
      throw new Error(`Unexpected request ${path}`);
    });
    vi.stubGlobal("fetch", fetch);

    renderShell();
    fireEvent.pointerDown(await screen.findByText("A body"));
    fireEvent.change(screen.getByLabelText("Body"), { target: { value: "A edited late" } });
    fireEvent.click(screen.getByRole("button", { name: "Save text" }));
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
    fireEvent.click(screen.getByRole("button", { name: "Project B" }));
    fireEvent.pointerDown(await screen.findByText("B body"));
    slow.resolve(jsonResponse({ project: withNode(a, { ...a.nodes[0], data: { body: "A edited late" } }, 1), node_ids: ["shared"] }));
    await waitFor(() => expect(screen.queryByText("A edited late")).not.toBeInTheDocument());

    const inspector = screen.getByRole("region", { name: "Inspector" });
    expect(within(inspector).getByDisplayValue("B body")).toBeInTheDocument();
    expect(fetch.mock.calls.filter(call => !isProductSkillsPath(requestPath(call))).map(requestPath)).toEqual([
      "/atelier/projects",
      "/atelier/projects/p-a/nodes/shared/text",
      "/atelier/projects/p-b",
    ]);
  });
});
