// @vitest-environment happy-dom
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import messages from "../../messages/en.json";
import wire from "./fixtures/atelier-wan27-media-generation-wire.json";
import { parseAtelierProject } from "@/lib/atelierApi";
import { MediaComposer } from "@/components/atelier/v3/MediaComposer";
import { clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { useAtelierStore } from "@/store/atelierStore";
import type { AtelierImageGenerationParams, AtelierNode, AtelierProject } from "@/lib/atelierTypes";
const ids = ["wan2.7-image", "wan2.7-image-pro", "wan/wan2.7-image#image", "wan/wan2.7-image-pro#image"];
function node(overrides: Partial<AtelierNode>): AtelierNode {
  return {
    id: "image",
    project_id: "project-1",
    type: "image",
    title: "Image",
    prompt: "make a still",
    status: "draft",
    x: 0,
    y: 0,
    width: 320,
    height: 180,
    source_project_id: null,
    frame_id: null,
    asset_id: null,
    video_task_id: null,
    media_urls: [],
    data: {},
    created_by: "user",
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

function project(
  nodes: AtelierNode[] = [node({})],
  overrides: Partial<AtelierProject> = {},
): AtelierProject {
  return {
    id: "project-1",
    title: "Canvas",
    description: "",
    source_project_id: null,
    nodes,
    agent_policy: {
      approval_mode: "untrusted",
      allowed_tools: [],
      max_nodes_per_action: 8,
      max_tokens_per_turn: 2048,
      enable_reviewer: false,
      max_generation_cost_per_turn: 5,
      updated_at: 1,
    },
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
    revision: 3,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}


function saved(modelId = ids[0], params: AtelierImageGenerationParams = {}) {
  return node({ data: { generation_draft: { schema_version: 3, media_kind: "image", model_id: modelId, mode: "t2i", params, media: [], batch_size: 2 } } });
}
function mount(image = node({}), extra: Partial<React.ComponentProps<typeof MediaComposer>> = {}) {
  const p = project([image]);
  const calls = { saveDraft: vi.fn().mockResolvedValue({}), createImageCandidate: vi.fn().mockResolvedValue({}), createAudioCandidate: vi.fn(), readbackPendingGeneration: vi.fn(), refreshGenerationAttempt: vi.fn(), continueGenerationAttempt: vi.fn(), cancelGenerationAttempt: vi.fn(), ...extra };
  const props = { project: p, selectedNode: image, ownerKey: "env:project-1:image", disabled: false, pendingGenerations: [], ...calls };
  const view = render(<NextIntlClientProvider locale="en" messages={messages}><MediaComposer {...props} /></NextIntlClientProvider>);
  return { ...view, calls, props, p, image };
}
const generate = () => screen.getByRole("button", { name: "Generate" });
const correct = () => screen.getByRole("button", { name: /Remove incompatible settings/ });
beforeEach(() => {
  vi.restoreAllMocks(); clearRuntimeEnvironment(); localStorage.clear();
  useAtelierStore.setState({ currentProject: null, selectedNodeId: null, pendingGenerations: [], isLoading: false, isSaving: false, needsRefresh: false });
});
describe("Atelier strict Wan2.7 draft controls", () => {
  it.each(ids)("keeps untouched defaults omitted for %s", async modelId => {
    const { calls } = mount(saved(modelId));
    expect(screen.queryByLabelText("Prompt extend")).toBeNull();
    expect(screen.queryByLabelText("Negative prompt")).toBeNull();
    expect(screen.getByLabelText("Seed")).toHaveValue(null);
    expect(screen.getByLabelText("Watermark")).not.toBeChecked();
    fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(calls.saveDraft).toHaveBeenCalledWith("image", expect.objectContaining({ params: {}, batch_size: 2 }));
    expect(calls.createImageCandidate).toHaveBeenCalledWith("image", expect.objectContaining({ params: {}, mode: "t2i" }));
  });
  it("does not inject catalog defaults into a fresh image node", async () => {
    const { calls } = mount(); fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(calls.createImageCandidate).toHaveBeenCalledWith("image", expect.objectContaining({ params: {} }));
  });
  it("omits seed after clearing it and preserves explicit zero and false", async () => {
    const { calls } = mount(saved()); const seed = screen.getByLabelText("Seed");
    fireEvent.change(seed, { target: { value: "7" } }); fireEvent.change(seed, { target: { value: "" } });
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1].params).not.toHaveProperty("seed");
    fireEvent.change(seed, { target: { value: "0" } });
    fireEvent.click(screen.getByLabelText("Watermark")); fireEvent.click(screen.getByLabelText("Watermark"));
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledTimes(2));
    expect(vi.mocked(calls.createImageCandidate).mock.calls[1][1].params).toEqual({ seed: 0, watermark: false });
  });
  it.each([{ negative_prompt: "" }, { negative_prompt: "old negative" }, { prompt_extend: false }, { prompt_extend: true }, { seed: -1 }, { seed: 2147483648 }, { size: "bad" }, { size: "" }])("preserves and blocks old fields %j before save or create", async params => {
    const image = saved(ids[0], params); const before = structuredClone(image); const { calls } = mount(image);
    expect(generate()).toBeDisabled(); expect(screen.getByRole("alert")).toBeInTheDocument();
    fireEvent.click(generate()); fireEvent.keyDown(screen.getByLabelText("Prompt"), { key: "Enter", ctrlKey: true });
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Save settings" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(calls.saveDraft).toHaveBeenCalledOnce());
    expect(calls.saveDraft).toHaveBeenCalledWith("image", expect.objectContaining({ params })); expect(image).toEqual(before);
  });
  it("corrects only explicitly and still requires a separate generate", async () => {
    const params = { size: "768*1024", seed: 0, watermark: false, negative_prompt: "", prompt_extend: false };
    const { calls, image } = mount(saved(ids[1], params)); fireEvent.click(correct());
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
    expect(image.data.generation_draft).toMatchObject({ params }); fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1]).toMatchObject({ prompt: "make a still", batch_size: 2, params: { size: "768*1024", seed: 0, watermark: false } });
  });
  it("keeps incompatible fields across a model roundtrip", () => {
    mount(saved(ids[0], { negative_prompt: "", prompt_extend: false, seed: -1 }));
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gpt-image/gpt-image-2#image" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "wan/wan2.7-image#image" } });
    expect(generate()).toBeDisabled(); expect(screen.getByRole("alert")).toHaveTextContent("Negative prompt");
    expect(screen.getByLabelText("Seed")).toHaveValue(-1);
  });
  it("keeps the existing I2I model-switch projection when references are present", async () => {
    const ref = node({ id: "reference", media_urls: ["uploads/reference.png"] });
    const image = saved("wan/wan2.7-image-pro#image", { negative_prompt: "old negative", seed: 0 });
    image.data.reference_node_ids = [ref.id]; image.data.reference_image_urls = ref.media_urls;
    image.data.generation_draft = { schema_version: 3, media_kind: "image", model_id: "wan/wan2.7-image-pro#image", mode: "i2i",
      params: { negative_prompt: "old negative", seed: 0 }, batch_size: 2,
      media: [{ role: "reference_image", ordinal: 0, node_id: ref.id, media_url: ref.media_urls[0] }] };
    const { calls } = mount(image, { project: project([image, ref]) });
    expect(screen.getByLabelText("Negative prompt")).toHaveValue("old negative");
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gpt-image/gpt-image-2#image" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "wan/wan2.7-image-pro#image" } });
    expect(screen.getByLabelText("Negative prompt")).toHaveValue("");
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1]).toMatchObject({ mode: "i2i" });
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1].params).not.toHaveProperty("negative_prompt");
  });
  it("does not create after correcting an edit while draft save is pending", async () => {
    let finish!: () => void; const pending = new Promise<void>(resolve => { finish = resolve; });
    const { calls } = mount(saved(ids[0], { seed: 0 }), { saveDraft: vi.fn().mockReturnValue(pending) });
    fireEvent.click(generate()); fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "-1" } }); fireEvent.click(correct());
    await act(async () => finish()); expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });
  it("blocks the actual old untagged candidate branch without changing its original history", async () => {
    const original = structuredClone(wire.legacy);
    const p = parseAtelierProject(wire.legacy.branch_ack.project);
    const branch = p.nodes.find(n => n.id === wire.legacy.branch_ack.node_ids[0])!;
    const { calls } = mount(branch, { project: p });
    expect(generate()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent('Negative prompt: ""');
    expect(screen.getByRole("alert")).toHaveTextContent("Prompt extend: false");
    expect(screen.getByLabelText("Seed")).toHaveValue(-1);
    fireEvent.click(correct()); expect(calls.createImageCandidate).not.toHaveBeenCalled();
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(wire.legacy).toEqual(original);
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1].params).toEqual({ size: "1280*1280", watermark: false });
  });
  it.each(["wan/wan2.7-image-pro#image", "qwen/qwen-image-2.0-pro#image"])("keeps the existing I2I projection on reference attachment for %s", async modelId => {
    const image = saved(modelId, { seed: 0, watermark: false });
    const { calls, rerender, props } = mount(image);
    const ref = node({ id: "reference", media_urls: ["uploads/reference.png"] });
    const attached = { ...image, data: { ...image.data, reference_node_ids: [ref.id], reference_image_urls: ref.media_urls } };
    const p = project([attached, ref]);
    rerender(<NextIntlClientProvider locale="en" messages={messages}><MediaComposer {...props} project={p} selectedNode={attached} /></NextIntlClientProvider>);
    await waitFor(() => expect(screen.getByLabelText("Prompt extend")).toBeInTheDocument());
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1]).toMatchObject({ mode: "i2i", params: { seed: 0, watermark: false, prompt_extend: true }, media: [{ ordinal: 0, node_id: ref.id, media_url: ref.media_urls[0] }] });
  });

  it("keeps non-Wan same-model I2I parameters unchanged when references attach", async () => {
    const image = saved("qwen/qwen-image-2.0-pro#image", { seed: 0, prompt_extend: false });
    const draft = image.data.generation_draft as { mode: string };
    draft.mode = "i2i";
    const { calls, rerender, props } = mount(image);
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(calls.saveDraft).toHaveBeenCalledOnce());
    const before = vi.mocked(calls.saveDraft!).mock.calls[0][1];
    const ref = node({ id: "reference", media_urls: ["uploads/reference.png"] });
    const attached = { ...image, data: { ...image.data, reference_node_ids: [ref.id], reference_image_urls: ref.media_urls } };
    rerender(<NextIntlClientProvider locale="en" messages={messages}><MediaComposer {...props} project={project([attached, ref])} selectedNode={attached} /></NextIntlClientProvider>);
    await waitFor(() => expect(generate()).toBeEnabled());
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1]).toMatchObject({ model_id: "qwen/qwen-image-2.0-pro#image", mode: "i2i", params: before.params });
  });
  it.each([-1, 0.5, 2147483648])("keeps a manually entered invalid seed %s until explicit correction", value => {
    mount(saved()); fireEvent.change(screen.getByLabelText("Seed"), { target: { value: String(value) } });
    expect(screen.getByLabelText("Seed")).toHaveValue(value); expect(generate()).toBeDisabled();
    fireEvent.click(correct()); expect(screen.getByLabelText("Seed")).toHaveValue(null);
  });

});
