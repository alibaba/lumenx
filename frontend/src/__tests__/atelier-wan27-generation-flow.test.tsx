// @vitest-environment happy-dom
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import messages from "../../messages/en.json";
import wire from "./fixtures/atelier-wan27-media-generation-wire.json";
import { MediaComposer } from "@/components/atelier/v3/MediaComposer";
import { useAtelierStore } from "@/store/atelierStore";
import { parseAtelierProject } from "@/lib/atelierApi";
import { beginAtelierGenerationRequest, listProjectAtelierGenerationRequests } from "@/lib/atelierGenerationRequest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierImageCandidateRequest, AtelierProject } from "@/lib/atelierTypes";

const state = () => useAtelierStore.getState();
const respond = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const journals = (projectId: string) => listProjectAtelierGenerationRequests({ environment: requireActiveRuntimeEnvironment(), projectId });
function seed(p: AtelierProject, nodeId: string) {
  useAtelierStore.setState({ projects: [p], currentProject: p, selectedNodeId: nodeId, pendingGenerations: [], isLoading: false, isSaving: false, needsRefresh: false, error: null });
}
function ConnectedComposer() {
  const s = useAtelierStore();
  if (!s.currentProject) return null;
  const environment = requireActiveRuntimeEnvironment();
  return <MediaComposer project={s.currentProject} selectedNode={s.currentProject.nodes.find(n => n.id === s.selectedNodeId) ?? null}
    ownerKey={`${environment.key}:${environment.generation}:${s.currentProject.id}:${s.selectedNodeId}`}
    disabled={s.isLoading || s.isSaving || s.needsRefresh} pendingGenerations={s.pendingGenerations}
    saveDraft={s.saveMediaGenerationDraft} createImageCandidate={s.createImageCandidate} createAudioCandidate={s.createAudioCandidate}
    readbackPendingGeneration={s.readbackPendingGeneration} refreshGenerationAttempt={s.refreshGenerationAttempt}
    continueGenerationAttempt={s.continueGenerationAttempt} cancelGenerationAttempt={s.cancelGenerationAttempt}
    selectCandidate={s.selectMediaGenerationCandidate} setCandidateVisibility={s.setMediaGenerationCandidateVisibility} />;
}
function mount() { return render(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC"><ConnectedComposer /></NextIntlClientProvider>); }
function pendingLegacy() {
  const request = { ...wire.legacy.request, entrypoint: "atelier.provider_image.v1" } as AtelierImageCandidateRequest;
  const project = parseAtelierProject(wire.legacy.draft_ack.project);
  const nodeId = wire.legacy.draft_ack.node_ids[0];
  seed(project, nodeId);
  beginAtelierGenerationRequest({ environment: requireActiveRuntimeEnvironment(), projectId: project.id, nodeId,
    entrypoint: request.entrypoint, expectedRevision: request.expected_revision, modelId: request.model_id,
    mode: request.mode, prompt: request.prompt, media: request.media, params: request.params, batchSize: request.batch_size,
    idFactory: () => request.client_request_id });
  state().loadPendingGenerations();
  return { project, request };
}
beforeEach(() => {
  vi.restoreAllMocks(); clearRuntimeEnvironment(); localStorage.clear();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Unexpected transport"));
});

describe("Atelier actual image journal and parser recovery", () => {
  it("reads an old untagged request despite the new draft gate without generating or selecting", async () => {
    const { project, request } = pendingLegacy();
    const transport = vi.mocked(globalThis.fetch).mockResolvedValue(respond(wire.legacy.replayed));
    mount();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(screen.getByText(/Negative prompt:/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Read original request" }));
    await waitFor(() => expect(journals(project.id)).toEqual([]));
    expect(transport).toHaveBeenCalledOnce();
    expect(transport.mock.calls[0][0]).toContain(`/generation/requests/${request.client_request_id}`);
    expect(transport.mock.calls[0][1]?.method).toBe("GET");
    expect(state().currentProject?.generation_attempts[0].provider_execution.intent).not.toHaveProperty("intent_contract_version");
    expect(state().currentProject?.nodes.find(n => n.id === state().selectedNodeId)?.media_urls).toEqual([]);
  });
  it.each([404, 503])("keeps the original image request after a readback %s", async status => {
    const { project } = pendingLegacy(); const original = journals(project.id)[0];
    vi.mocked(globalThis.fetch).mockResolvedValue(respond({ detail: "readback unavailable" }, status));
    mount(); fireEvent.click(screen.getByRole("button", { name: "Read original request" }));
    await screen.findByText("readback unavailable");
    expect(journals(project.id)).toEqual([original]);
    expect(globalThis.fetch).toHaveBeenCalledOnce();
    expect(vi.mocked(globalThis.fetch).mock.calls[0][1]?.method).toBe("GET");
  });
  it("does not consume the original journal when a GET returns after environment A to B to A", async () => {
    const { project } = pendingLegacy(); const original = journals(project.id)[0];
    let finish!: (response: Response) => void;
    vi.mocked(globalThis.fetch).mockReturnValue(new Promise<Response>(resolve => { finish = resolve; }));
    mount(); fireEvent.click(screen.getByRole("button", { name: "Read original request" }));
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledOnce());
    act(() => {
      activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
      activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
      seed(project, wire.legacy.draft_ack.node_ids[0]); state().loadPendingGenerations();
    });
    await act(async () => finish(respond(wire.legacy.replayed)));
    expect(journals(project.id)).toEqual([original]);
    expect(state().currentProject?.generation_attempts).toEqual(project.generation_attempts);
    expect(globalThis.fetch).toHaveBeenCalledOnce();
  });
});


function strictEditor() {
  const project = parseAtelierProject(wire.strict.initial_project);
  seed(project, "image");
  vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue("11111111-1111-4111-8111-111111111111");
  mount();
  fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "A glass moon" } });
  fireEvent.change(screen.getByLabelText("Size"), { target: { value: "1280*1280" } });
  fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "0" } });
  fireEvent.click(screen.getByLabelText("Watermark"));
  fireEvent.click(screen.getByLabelText("Watermark"));
  fireEvent.change(screen.getByLabelText("Candidates"), { target: { value: "2" } });
  return project;
}
function strictTransport(submit: () => Promise<Response>) {
  return vi.mocked(globalThis.fetch).mockImplementation(async (url, init) => {
    if (init?.method === "PUT" && String(url).endsWith("/generation_draft")) {
      expect(JSON.parse(String(init.body))).toEqual(wire.strict.draft_request);
      return respond(wire.strict.draft_ack);
    }
    if (init?.method === "POST" && String(url).endsWith("/image_candidates")) {
      expect(JSON.parse(String(init.body))).toEqual(wire.strict.request);
      expect(journals("p")).toHaveLength(1);
      expect(journals("p")[0].request).toEqual({ ...wire.strict.request, entrypoint: "atelier.provider_image.v1" });
      return submit();
    }
    if (init?.method === "GET" && String(url).endsWith(`/generation/requests/${wire.strict.request.client_request_id}`)) {
      return respond(wire.strict.replayed);
    }
    throw new Error(`Unexpected transport: ${init?.method} ${String(url)}`);
  });
}

describe("Atelier strict image actual editor, store, journal and wire", () => {
  it.each(["lost ACK", "503"])("keeps one original request after %s and recovers both members with GET", async failure => {
    const transport = strictTransport(async () => {
      if (failure === "lost ACK") throw new TypeError("response lost");
      return respond({ detail: "response unavailable" }, 503);
    });
    const project = strictEditor();
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await screen.findByText(failure === "lost ACK" ? "response lost" : "response unavailable");
    expect(journals(project.id)).toHaveLength(1);
    expect(transport.mock.calls.map(call => call[1]?.method)).toEqual(["PUT", "POST"]);
    fireEvent.click(screen.getByRole("button", { name: "Read original request" }));
    await waitFor(() => expect(journals(project.id)).toEqual([]));
    expect(transport.mock.calls.map(call => call[1]?.method)).toEqual(["PUT", "POST", "GET"]);
    const attempts = state().currentProject?.generation_attempts ?? [];
    expect(attempts).toHaveLength(2);
    expect(attempts.map(attempt => attempt.status)).toEqual(["completed", "completed"]);
    for (const attempt of attempts) {
      expect(attempt.provider_execution.intent).toMatchObject({
        intent_contract_version: "provider-wan27-t2i-bound-http-v1", params: { size: "1280*1280", seed: 0, watermark: false },
      });
    }
    expect(state().currentProject?.nodes[0].media_urls).toEqual([]);
    expect(state().currentProject?.nodes[0].data.selected_candidate_id).toBeUndefined();
  });

  it("clears only the definitely rejected new request after an explicit 422", async () => {
    const transport = strictTransport(async () => respond({ detail: "unsupported strict image parameter" }, 422));
    strictEditor(); fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await screen.findByText("unsupported strict image parameter");
    expect(journals("p")).toEqual([]);
    expect(transport.mock.calls.map(call => call[1]?.method)).toEqual(["PUT", "POST"]);
    expect(state().currentProject?.generation_attempts).toEqual([]);
  });

  it("retains the journal if the returned receipt belongs to another actual request", async () => {
    const transport = strictTransport(async () => respond(wire.strict.unknown));
    strictEditor(); fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await waitFor(() => expect(state().isSaving).toBe(false));
    await waitFor(() => expect(screen.getByRole("button", { name: "Read original request" })).toBeEnabled());
    expect(journals("p")).toHaveLength(1);
    expect(journals("p")[0].request.client_request_id).toBe(wire.strict.request.client_request_id);
    expect(transport.mock.calls.map(call => call[1]?.method)).toEqual(["PUT", "POST"]);
    expect(state().currentProject?.generation_attempts).toEqual([]);
  });

  it("reads partial completion without retrying an unknown member or selecting an image", async () => {
    const project = parseAtelierProject(wire.strict.completed.project);
    const request = { ...wire.strict.unknown_request, entrypoint: "atelier.provider_image.v1" } as AtelierImageCandidateRequest;
    seed(project, "image");
    beginAtelierGenerationRequest({ environment: requireActiveRuntimeEnvironment(), projectId: project.id, nodeId: "image",
      entrypoint: request.entrypoint, expectedRevision: request.expected_revision, modelId: request.model_id,
      mode: request.mode, prompt: request.prompt, media: request.media, params: request.params, batchSize: request.batch_size,
      idFactory: () => request.client_request_id });
    state().loadPendingGenerations();
    const transport = vi.mocked(globalThis.fetch).mockResolvedValue(respond(wire.strict.unknown));
    mount(); fireEvent.click(screen.getByRole("button", { name: "Read original request" }));
    await waitFor(() => expect(journals(project.id)).toEqual([]));
    expect(transport.mock.calls.map(call => call[1]?.method)).toEqual(["GET"]);
    const memberIds = wire.strict.unknown.members.map(member => member.attempt.attempt_id);
    const attempts = state().currentProject?.generation_attempts.filter(attempt => memberIds.includes(attempt.attempt_id)) ?? [];
    expect(attempts.map(attempt => attempt.status)).toEqual(["completed", "needs_confirmation"]);
    expect(state().currentProject?.nodes[0].media_urls).toEqual([]);
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("retains a strict request after a GET 404 without repeating its POST", async () => {
    const transport = strictTransport(async () => { throw new TypeError("response lost"); });
    strictEditor(); fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await screen.findByText("response lost");
    const original = journals("p")[0];
    transport.mockResolvedValueOnce(respond({ detail: "original request unavailable" }, 404));
    fireEvent.click(screen.getByRole("button", { name: "Read original request" }));
    await screen.findByText("original request unavailable");
    expect(journals("p")).toEqual([original]);
    expect(transport.mock.calls.map(call => call[1]?.method)).toEqual(["PUT", "POST", "GET"]);
  });

  it("adopts only the original project's late receipt after navigating to another project", async () => {
    let finish!: (response: Response) => void;
    const transport = strictTransport(() => new Promise<Response>(resolve => { finish = resolve; }));
    strictEditor(); fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await waitFor(() => expect(transport).toHaveBeenCalledTimes(2));
    const other = parseAtelierProject({ ...wire.strict.initial_project, id: "other-project", nodes: [] });
    transport.mockResolvedValueOnce(respond(other));
    await act(async () => {
      await state().switchProject(other.id);
      // AtelierShell refreshes pending journals whenever its project owner changes.
      state().loadPendingGenerations();
    });
    expect(state().currentProject?.id).toBe(other.id);
    await act(async () => finish(respond(wire.strict.completed)));
    expect(state().currentProject).toEqual(other);
    expect(state().selectedNodeId).toBeNull();
    expect(state().pendingGenerations).toEqual([]);
    expect(journals("p")).toEqual([]);
    expect(state().projects.find(p => p.id === "p")?.generation_attempts.map(attempt => attempt.status)).toEqual(["completed", "completed"]);
    expect(transport.mock.calls.map(call => call[1]?.method)).toEqual(["PUT", "POST", "GET"]);
    expect(transport.mock.calls[2][0]).toContain(`/atelier/projects/${other.id}`);
  });

  it("does not consume a POST receipt that arrives after environment A to B to A", async () => {
    let finish!: (response: Response) => void;
    const transport = strictTransport(() => new Promise<Response>(resolve => { finish = resolve; }));
    strictEditor(); fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await waitFor(() => expect(transport).toHaveBeenCalledTimes(2));
    const original = journals("p")[0];
    const saved = parseAtelierProject(wire.strict.draft_ack.project);
    act(() => {
      activateRuntimeEnvironment({ schema_version: 1, storage_id: "22222222-2222-4222-8222-222222222222" });
      activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
      seed(saved, "image"); state().loadPendingGenerations();
    });
    await act(async () => finish(respond(wire.strict.completed)));
    expect(journals("p")).toEqual([original]);
    expect(state().currentProject?.generation_attempts).toEqual([]);
    expect(transport.mock.calls.map(call => call[1]?.method)).toEqual(["PUT", "POST"]);
  });
});
