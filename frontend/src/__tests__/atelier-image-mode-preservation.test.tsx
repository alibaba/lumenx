// @vitest-environment happy-dom
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../messages/en.json";
import zh from "../../messages/zh.json";
import wire from "./fixtures/atelier-qwen20-media-generation-wire.json";
import { MediaComposer } from "@/components/atelier/v3/MediaComposer";
import { parseAtelierProject } from "@/lib/atelierApi";
import { listProjectAtelierGenerationRequests } from "@/lib/atelierGenerationRequest";
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { useAtelierStore } from "@/store/atelierStore";
import type { AtelierImageGenerationParams, AtelierImageReferenceSelection } from "@/lib/atelierTypes";

const modelId = "qwen/qwen-image-2.0-pro#image";
const media: AtelierImageReferenceSelection[] = [{ role: "reference_image", ordinal: 0, node_id: "reference", media_url: "uploads/reference.png" }];
function fixture(mode: "i2i" | "t2i" = "i2i", params: AtelierImageGenerationParams = {}) {
  const p = parseAtelierProject(wire.initial_project);
  const image = p.nodes.find(node => node.id === "image")!;
  image.prompt = "A glass moon";
  image.data.generation_draft = { schema_version: 3, media_kind: "image", model_id: modelId, mode, params, media: [], batch_size: 2 };
  return { p, image };
}
type Fixture = ReturnType<typeof fixture>;
function attach(input: Fixture, includeSavedMedia = true): Fixture {
  const p = structuredClone(input.p);
  const image = p.nodes.find(node => node.id === input.image.id)!;
  const ref = { ...structuredClone(image), id: "reference", data: {}, media_urls: [media[0].media_url] };
  p.nodes.push(ref);
  image.data.reference_node_ids = [ref.id];
  image.data.reference_image_urls = ref.media_urls;
  if (includeSavedMedia) image.data.generation_draft = { ...(image.data.generation_draft as object), media: media.map(item => ({ ...item })) };
  return { p, image };
}
function mount(input = fixture(), extra: Partial<React.ComponentProps<typeof MediaComposer>> = {}, locale: "en" | "zh" = "en") {
  const calls = { saveDraft: vi.fn().mockResolvedValue({}), createImageCandidate: vi.fn().mockResolvedValue({}), createAudioCandidate: vi.fn(), readbackPendingGeneration: vi.fn(), refreshGenerationAttempt: vi.fn(), continueGenerationAttempt: vi.fn(), cancelGenerationAttempt: vi.fn() };
  const props = { project: input.p, selectedNode: input.image, ownerKey: "owner:p:image", disabled: false, pendingGenerations: [], ...calls, ...extra };
  const view = render(<NextIntlClientProvider locale={locale} messages={locale === "en" ? en : zh} timeZone="UTC"><MediaComposer {...props} /></NextIntlClientProvider>);
  return { ...view, calls, props };
}
const generate = () => screen.getByRole("button", { name: "Generate" });
const switchMode = () => screen.getByRole("button", { name: "Use a text-to-image draft" });
beforeEach(() => {
  vi.restoreAllMocks(); clearRuntimeEnvironment(); localStorage.clear();
  useAtelierStore.setState({ projects: [], currentProject: null, selectedNodeId: null, pendingGenerations: [], isLoading: false, isSaving: false, needsRefresh: false, error: null });
});

describe("Atelier image draft declared mode preservation", () => {
  it.each([-1, 0])("blocks incomplete I2I generation before any real store PUT, POST or journal for seed %s", async seed => {
    const input = fixture("i2i", { seed });
    const original = structuredClone(input.image.data);
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
    useAtelierStore.setState({ projects: [input.p], currentProject: input.p, selectedNodeId: input.image.id });
    const state = useAtelierStore.getState();
    const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("No transport expected"));
    mount(input, { saveDraft: state.saveMediaGenerationDraft, createImageCandidate: state.createImageCandidate });
    expect(generate()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("This image-to-image draft needs reference images");
    expect(switchMode()).toBeEnabled();
    fireEvent.click(generate());
    fireEvent.keyDown(screen.getByLabelText("Prompt"), { key: "Enter", ctrlKey: true });
    await act(async () => {});
    expect(fetch).not.toHaveBeenCalled();
    expect(listProjectAtelierGenerationRequests({ environment: requireActiveRuntimeEnvironment(), projectId: input.p.id })).toEqual([]);
    expect(input.image.data).toEqual(original);
  });

  it.each([-1, 0])("still explicitly saves an incomplete I2I draft as I2I with seed %s", async seed => {
    const { calls } = mount(fixture("i2i", { seed, prompt_extend: false, watermark: false, negative_prompt: "old negative" }));
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(calls.saveDraft).toHaveBeenCalledOnce());
    expect(calls.saveDraft).toHaveBeenCalledWith("image", expect.objectContaining({ mode: "i2i", media: [], params: { seed, size: "1280*1280", prompt_extend: false, watermark: false, negative_prompt: "old negative" } }));
    expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });

  it.each([-1, 0])("requires explicit local T2I conversion and preserves seed %s and supported values", async seed => {
    const input = fixture("i2i", { seed, prompt_extend: false, watermark: false, negative_prompt: "old negative" });
    const original = structuredClone(input.image.data);
    const { calls } = mount(input);
    fireEvent.click(switchMode());
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
    expect(input.image.data).toEqual(original);
    expect(screen.getByLabelText("Seed")).toHaveValue(seed);
    expect(screen.getByLabelText("Negative prompt")).toHaveValue("old negative");
    expect(screen.getByLabelText("Prompt extend")).not.toBeChecked();
    expect(screen.getByLabelText("Watermark")).not.toBeChecked();
    if (seed === -1) {
      expect(generate()).toBeDisabled();
      expect(screen.getByRole("button", { name: "Remove incompatible settings" })).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "0" } });
    }
    expect(generate()).toBeEnabled();
    fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(calls.saveDraft).toHaveBeenCalledWith("image", expect.objectContaining({ mode: "t2i", media: [] }));
    expect(calls.createImageCandidate).toHaveBeenCalledWith("image", expect.objectContaining({ mode: "t2i", media: [], params: { seed: 0, size: "1280*1280", prompt_extend: false, watermark: false, negative_prompt: "old negative" } }));
  });

  it("keeps valid referenced I2I on the legacy contract without offering implicit reference removal", async () => {
    const input = attach(fixture("i2i", { seed: -1, negative_prompt: "old negative", prompt_extend: false }));
    const { calls } = mount(input);
    expect(screen.queryByRole("button", { name: "Use a text-to-image draft" })).toBeNull();
    expect(generate()).toBeEnabled(); fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(calls.saveDraft).toHaveBeenCalledWith("image", expect.objectContaining({ mode: "i2i", media }));
    expect(calls.createImageCandidate).toHaveBeenCalledWith("image", expect.objectContaining({ mode: "i2i", media, params: expect.objectContaining({ seed: -1, negative_prompt: "old negative", prompt_extend: false }) }));
  });

  it("can restore references to an incomplete mounted I2I draft and keep its original parameters", async () => {
    const input = fixture("i2i", { seed: -1, prompt_extend: false });
    const { calls, props, rerender } = mount(input);
    expect(generate()).toBeDisabled();
    const attached = attach(input, false);
    rerender(<NextIntlClientProvider locale="en" messages={en}><MediaComposer {...props} project={attached.p} selectedNode={attached.image} /></NextIntlClientProvider>);
    await waitFor(() => expect(generate()).toBeEnabled()); fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(calls.createImageCandidate).toHaveBeenCalledWith("image", expect.objectContaining({ mode: "i2i", media, params: expect.objectContaining({ seed: -1, prompt_extend: false }) }));
  });

  it("lets explicit reference removal be followed by explicit T2I conversion without auto-save or generation", async () => {
    const input = attach(fixture("i2i", { seed: 0, prompt_extend: false }));
    const { calls, props, rerender } = mount(input);
    const detached = structuredClone(input.p);
    const image = detached.nodes.find(node => node.id === "image")!;
    image.data.reference_node_ids = []; image.data.reference_image_urls = [];
    const original = structuredClone(image.data);
    rerender(<NextIntlClientProvider locale="en" messages={en}><MediaComposer {...props} project={detached} selectedNode={image} /></NextIntlClientProvider>);
    expect(generate()).toBeDisabled();
    fireEvent.click(switchMode());
    expect(image.data).toEqual(original);
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
    expect(generate()).toBeEnabled(); fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(calls.createImageCandidate).toHaveBeenCalledWith("image", expect.objectContaining({ mode: "t2i", media: [], params: expect.objectContaining({ seed: 0, prompt_extend: false }) }));
  });

  it.each([{}, { seed: 0, prompt_extend: false, watermark: false, negative_prompt: "" }])("retains pure Qwen T2I provenance: %j", async params => {
    const { calls } = mount(fixture("t2i", params));
    expect(screen.queryByRole("button", { name: "Use a text-to-image draft" })).toBeNull();
    expect(generate()).toBeEnabled(); fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(calls.createImageCandidate).toHaveBeenCalledWith("image", expect.objectContaining({ mode: "t2i", media: [], params }));
  });

  it("uses the actual Chinese diagnostic and explicit mode action", () => {
    const { calls } = mount(fixture("i2i", { seed: 0 }), {}, "zh");
    expect(screen.getByRole("alert")).toHaveTextContent("此图生图草稿缺少参考图");
    fireEvent.click(screen.getByRole("button", { name: "改为文生图草稿" }));
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });
});
