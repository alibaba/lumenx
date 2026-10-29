import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextIntlClientProvider } from "next-intl";
import { AtelierShell } from "../AtelierShell";
import { LightboxProvider } from "@/components/shared/preview/LightboxProvider";
import { useAtelierStore } from "@/store/atelierStore";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierNode, AtelierProject } from "@/lib/atelierTypes";
import fixture from "@/__tests__/fixtures/atelier-project.json";
import generationAccepted from "@/__tests__/fixtures/atelier-generation-accepted.json";
import generationCompleted from "@/__tests__/fixtures/atelier-generation-completed.json";
import messages from "../../../../../messages/en.json";

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const STORAGE_B = "22222222-2222-4222-8222-222222222222";

function node(id: string, type = "idea", data: AtelierNode["data"] = {}): AtelierNode {
  return {
    ...structuredClone(fixture.nodes[0]), id, project_id: "p-a", type, title: id,
    prompt: "Original prompt", status: "completed", media_urls: [],
    video_task_id: null, data,
  } as AtelierNode;
}

function project(nodes: AtelierNode[] = [
  node("Artwork", "idea", {
    body: "Keep this text", candidates: [{ id: "take-1", url: "uploads/take.png" }],
    generation: { seed: 0, prompt_extend: false }, reference_image_urls: ["uploads/ref.png"],
  }),
  node("Other", "idea", { body: "Other text", region_id: "Region B" }),
  node("Region A", "region", { color: "default", collapsed: false }),
  node("Region B", "region", { color: "cyan", collapsed: false }),
]): AtelierProject {
  return {
    ...structuredClone(fixture), id: "p-a", title: "Project A", revision: 0,
    nodes, agent_turns: [], exports: [], node_delete_receipts: [],
    generation_attempts: [], generation_request_receipts: [],
  } as unknown as AtelierProject;
}

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function renderShell() {
  return render(<NextIntlClientProvider locale="en" timeZone="Asia/Shanghai" messages={messages}>
    <LightboxProvider><AtelierShell /></LightboxProvider>
  </NextIntlClientProvider>);
}

function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>(r => { resolve = r; });
  return { promise, resolve };
}

function changedRegion(current: AtelierProject, regionId: string | null): AtelierProject {
  return {
    ...structuredClone(current), revision: current.revision + 1,
    nodes: current.nodes.map(item => {
      if (item.id !== "Artwork") return structuredClone(item);
      const data = { ...structuredClone(item.data) };
      if (regionId === null) delete data.region_id;
      else data.region_id = regionId;
      return { ...structuredClone(item), data };
    }),
  };
}

function setup(initial = project(), save?: (body: Record<string, unknown>) => Response | Promise<Response>) {
  let current = initial;
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    if (path === "/atelier/agent/product_skills") return response([]);
    if (path === "/atelier/projects") return response([current]);
    if (path === "/atelier/projects/p-a") return response(current);
    if (path.endsWith("/nodes/Artwork/layout")) {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      if (save) return save(body);
      current = changedRegion(current, body.region_id as string | null);
      return response({ project: current, node_ids: ["Artwork"] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal("fetch", fetch);
  renderShell();
  return {
    fetch,
    setCurrent(value: AtelierProject) { current = value; },
    writes: () => fetch.mock.calls.filter(([url]) => new URL(url).pathname.endsWith("/layout")),
  };
}

async function selectArtwork() {
  await screen.findByText("Project A");
  fireEvent.pointerDown(screen.getByText("Keep this text"));
  return screen.getByRole("region", { name: "Inspector" });
}

function choose(regionId: string) {
  fireEvent.change(screen.getByRole("combobox", { name: "Region membership" }), { target: { value: regionId } });
}

beforeEach(() => {
  vi.restoreAllMocks();
  clearRuntimeEnvironment();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
});

describe("Atelier region membership through the real shell and client", () => {
  it("joins, moves between regions and removes membership with narrow acknowledged commands", async () => {
    const initial = project();
    const { writes } = setup(initial);
    const inspector = await selectArtwork();
    expect(within(inspector).getByText("Saved region: No region")).toBeInTheDocument();
    expect(within(inspector).getByRole("button", { name: "Save region" })).toBeDisabled();

    for (const [value, label] of [["Region A", "Region A"], ["Region B", "Region B"], ["", "No region"]]) {
      choose(value);
      expect(within(inspector).getByText("Region change not saved")).toBeInTheDocument();
      fireEvent.click(within(inspector).getByRole("button", { name: "Save region" }));
      await waitFor(() => expect(within(inspector).getByText(`Saved region: ${label}`)).toBeInTheDocument());
      await waitFor(() => expect(within(inspector).getByRole("button", { name: "Save region" })).toBeDisabled());
    }
    expect(writes().map(([, init]) => JSON.parse(String(init?.body)))).toEqual([
      { expected_revision: 0, region_id: "Region A" },
      { expected_revision: 1, region_id: "Region B" },
      { expected_revision: 2, region_id: null },
    ]);
    expect(useAtelierStore.getState().currentProject?.nodes).toEqual(initial.nodes);
  });

  it("keeps committed membership visible until ACK and prevents duplicate saves", async () => {
    const initial = project();
    const pending = deferred();
    const { writes } = setup(initial, () => pending.promise);
    const inspector = await selectArtwork();
    fireEvent.change(within(inspector).getByRole("textbox", { name: "Body" }), { target: { value: "Still editing this idea" } });
    choose("Region A");
    const saveButton = within(inspector).getByRole("button", { name: "Save region" });
    fireEvent.click(saveButton);
    expect(within(inspector).getByText("Saved region: No region")).toBeInTheDocument();
    expect(within(inspector).getByRole("combobox", { name: "Region membership" })).toBeDisabled();
    expect(saveButton).toBeDisabled();
    fireEvent.click(saveButton);
    expect(writes()).toHaveLength(1);
    pending.resolve(response({ project: changedRegion(initial, "Region A"), node_ids: ["Artwork"] }));
    await waitFor(() => expect(within(inspector).getByText("Saved region: Region A")).toBeInTheDocument());
    expect(within(inspector).getByRole("textbox", { name: "Body" })).toHaveValue("Still editing this idea");
    expect(useAtelierStore.getState().currentProject?.nodes[0].data.body).toBe("Keep this text");
  });

  it.each([409, 503])("preserves an unsaved region choice after HTTP %i and only retries after explicit refresh", async status => {
    const initial = project();
    let count = 0;
    const { writes } = setup(initial, () => ++count === 1
      ? response({ detail: "Save not confirmed" }, status)
      : response({ project: changedRegion(initial, "Region A"), node_ids: ["Artwork"] }));
    const inspector = await selectArtwork();
    choose("Region A");
    fireEvent.click(within(inspector).getByRole("button", { name: "Save region" }));
    await waitFor(() => expect(within(inspector).getByRole("alert")).toHaveTextContent("Save not confirmed"));
    expect(within(inspector).getByRole("combobox", { name: "Region membership" })).toHaveValue("Region A");
    expect(within(inspector).getByText("Saved region: No region")).toBeInTheDocument();
    expect(within(inspector).getByRole("button", { name: "Save region" })).toBeDisabled();
    expect(writes()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(within(inspector).getByRole("button", { name: "Save region" })).not.toBeDisabled());
    expect(writes()).toHaveLength(1);
    fireEvent.click(within(inspector).getByRole("button", { name: "Save region" }));
    await waitFor(() => expect(within(inspector).getByText("Saved region: Region A")).toBeInTheDocument());
    expect(writes()).toHaveLength(2);
  });

  it("does not replace the newly selected node's membership when an earlier save completes", async () => {
    const initial = project();
    const pending = deferred();
    setup(initial, () => pending.promise);
    const inspector = await selectArtwork();
    choose("Region A");
    fireEvent.click(within(inspector).getByRole("button", { name: "Save region" }));
    fireEvent.pointerDown(screen.getByText("Other text"));
    expect(within(inspector).getByRole("combobox", { name: "Region membership" })).toHaveValue("Region B");
    pending.resolve(response({ project: changedRegion(initial, "Region A"), node_ids: ["Artwork"] }));
    await waitFor(() => expect(within(inspector).getByRole("button", { name: "Save text" })).not.toBeDisabled());
    expect(within(inspector).getByRole("combobox", { name: "Region membership" })).toHaveValue("Region B");
    expect(within(inspector).getByText("Saved region: Region B")).toBeInTheDocument();
  });

  it("clears an uncertain save error when explicit readback confirms the chosen region without another write", async () => {
    const initial = project();
    const runtime = setup(initial, () => response({ detail: "Commit outcome unknown" }, 503));
    const inspector = await selectArtwork();
    choose("Region A");
    fireEvent.click(within(inspector).getByRole("button", { name: "Save region" }));
    await waitFor(() => expect(within(inspector).getByRole("alert")).toBeInTheDocument());
    runtime.setCurrent(changedRegion(initial, "Region A"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(within(inspector).getByText("Saved region: Region A")).toBeInTheDocument());
    expect(within(inspector).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(inspector).queryByText("Region change not saved")).not.toBeInTheDocument();
    expect(within(inspector).getByRole("button", { name: "Save region" })).toBeDisabled();
    expect(runtime.writes()).toHaveLength(1);
  });

  it("preserves completed generation state when an older membership ACK arrives after a fresh read", async () => {
    const decorate = (value: typeof generationAccepted.project | typeof generationCompleted.project): AtelierProject => ({
      ...structuredClone(value), title: "Project A",
      nodes: [
        ...value.nodes.map(item => ({ ...structuredClone(item), data: { ...structuredClone(item.data), filename: item.id === "video" ? "Generated video" : "Reference" } })),
        { ...node("Region A", "region", { color: "default", collapsed: false }), project_id: value.id },
      ],
    } as unknown as AtelierProject);
    const accepted = decorate(generationAccepted.project);
    const completed = decorate(generationCompleted.project);
    const acknowledged = {
      ...structuredClone(accepted), revision: accepted.revision + 1,
      nodes: accepted.nodes.map(item => item.id === "video" ? { ...item, data: { ...item.data, region_id: "Region A" } } : item),
    };
    const pending = deferred();
    const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
      const path = new URL(url).pathname;
      if (path === "/atelier/agent/product_skills") return response([]);
      if (path === "/atelier/projects") return response([accepted]);
      if (path === "/atelier/projects/project-1") return response(completed);
      if (path.endsWith("/nodes/video/layout")) return pending.promise;
      throw new Error(`Unexpected request: ${path} ${init.method}`);
    });
    vi.stubGlobal("fetch", fetch);
    renderShell();
    await screen.findByText("Project A");
    fireEvent.pointerDown(screen.getByRole("button", { name: /Generated video/ }));
    const inspector = screen.getByRole("region", { name: "Inspector" });
    choose("Region A");
    fireEvent.click(within(inspector).getByRole("button", { name: "Save region" }));
    await act(async () => { await useAtelierStore.getState().refreshProject(); });
    await act(async () => { pending.resolve(response({ project: acknowledged, node_ids: ["video"] })); });
    await waitFor(() => expect(within(inspector).getByText("Saved region: Region A")).toBeInTheDocument());
    expect(useAtelierStore.getState().currentProject?.generation_attempts).toEqual(completed.generation_attempts);
    expect(useAtelierStore.getState().currentProject?.nodes.find(item => item.id === "video")?.data).toMatchObject({
      region_id: "Region A", candidates: [{ id: generationCompleted.candidate.id, status: "completed" }],
    });
    const write = fetch.mock.calls.find(([url]) => new URL(url).pathname.endsWith("/layout"));
    expect(JSON.parse(String(write?.[1]?.body))).toEqual({ expected_revision: accepted.revision, region_id: "Region A" });
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(1);
  });

  it.each([false, true])("discards pending membership and late ACK across environment switch (return to A: %s)", async returnToA => {
    const initial = project();
    const pending = deferred();
    const runtime = setup(initial, () => pending.promise);
    let inspector = await selectArtwork();
    choose("Region A");
    fireEvent.click(within(inspector).getByRole("button", { name: "Save region" }));
    const otherEnvironment = changedRegion(initial, "Region B");
    runtime.setCurrent(otherEnvironment);
    act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }); });
    await waitFor(() => expect(useAtelierStore.getState().currentProject?.revision).toBe(1));
    inspector = await selectArtwork();
    expect(within(inspector).getByRole("combobox", { name: "Region membership" })).toHaveValue("Region B");
    if (returnToA) {
      runtime.setCurrent({ ...initial, revision: 2 });
      act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A }); });
      await waitFor(() => expect(useAtelierStore.getState().currentProject?.revision).toBe(2));
      inspector = await selectArtwork();
    }
    await act(async () => { pending.resolve(response({ project: changedRegion(initial, "Region A"), node_ids: ["Artwork"] })); });
    expect(within(inspector).getByRole("combobox", { name: "Region membership" })).toHaveValue(returnToA ? "" : "Region B");
    expect(within(inspector).queryByRole("alert")).not.toBeInTheDocument();
    expect(runtime.writes()).toHaveLength(1);
  });

  it("keeps a removed draft destination visible and prevents submitting it", async () => {
    const initial = project();
    const runtime = setup(initial);
    const inspector = await selectArtwork();
    choose("Region A");
    runtime.setCurrent({ ...initial, revision: 1, nodes: initial.nodes.filter(item => item.id !== "Region A") });
    await act(async () => { await useAtelierStore.getState().refreshProject(); });
    expect(within(inspector).getByRole("combobox", { name: "Region membership" })).toHaveValue("Region A");
    expect(within(inspector).getByRole("option", { name: "Unavailable region" })).toBeDisabled();
    expect(within(inspector).getByText("Saved region: No region")).toBeInTheDocument();
    expect(within(inspector).getByRole("button", { name: "Save region" })).toBeDisabled();
    expect(runtime.writes()).toHaveLength(0);
  });

  it("offers no membership command for region nodes themselves", async () => {
    setup();
    await screen.findByText("Project A");
    fireEvent.pointerDown(screen.getByText("Region A"));
    expect(screen.queryByRole("combobox", { name: "Region membership" })).not.toBeInTheDocument();
  });
});
