// @vitest-environment happy-dom
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../messages/en.json";
import zh from "../../messages/zh.json";
import wire from "./fixtures/atelier-wan27-media-generation-wire.json";
import { parseAtelierProject } from "@/lib/atelierApi";
import { parseAtelierMediaDraft } from "@/lib/atelierMediaApi";
import { MediaComposer } from "@/components/atelier/v3/MediaComposer";
import { clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { useAtelierStore } from "@/store/atelierStore";
import type { AtelierImageGenerationParams, AtelierNode } from "@/lib/atelierTypes";

const ids = ["qwen-image-2.0", "qwen-image-2.0-pro", "qwen/qwen-image-2.0#image", "qwen/qwen-image-2.0-pro#image"];
function saved(modelId = ids[0], params: AtelierImageGenerationParams = {}) {
  const p = parseAtelierProject(wire.strict.initial_project);
  const image = p.nodes.find(n => n.id === "image")!;
  image.prompt = "A glass moon";
  image.data.generation_draft = { schema_version: 3, media_kind: "image", model_id: modelId, mode: "t2i", params, media: [], batch_size: 2 };
  return { p, image };
}
function mount(fixture = saved(), locale: "en" | "zh" = "en", extra: Partial<React.ComponentProps<typeof MediaComposer>> = {}) {
  const calls = { saveDraft: vi.fn().mockResolvedValue({}), createImageCandidate: vi.fn().mockResolvedValue({}), createAudioCandidate: vi.fn(), readbackPendingGeneration: vi.fn(), refreshGenerationAttempt: vi.fn(), continueGenerationAttempt: vi.fn(), cancelGenerationAttempt: vi.fn(), ...extra };
  const props = { project: fixture.p, selectedNode: fixture.image, ownerKey: "env:p:image", disabled: false, pendingGenerations: [], ...calls };
  const view = render(<NextIntlClientProvider locale={locale} messages={locale === "en" ? en : zh} timeZone="UTC"><MediaComposer {...props} /></NextIntlClientProvider>);
  return { ...view, calls, props, ...fixture };
}
const generate = () => screen.getByRole("button", { name: "Generate" });
const correction = () => screen.getByRole("button", { name: "Remove incompatible settings" });
beforeEach(() => {
  vi.restoreAllMocks(); clearRuntimeEnvironment(); localStorage.clear();
  useAtelierStore.setState({ currentProject: null, selectedNodeId: null, pendingGenerations: [], isLoading: false, isSaving: false, needsRefresh: false });
});

describe("Qwen T2I real MediaComposer parameter provenance", () => {
  it.each(ids)("shows supported controls but omits untouched defaults for %s", async id => {
    const { calls } = mount(saved(id));
    expect(screen.getByLabelText("Negative prompt")).toHaveValue("");
    expect(screen.getByLabelText("Prompt extend")).toBeChecked();
    expect(screen.getByLabelText("Watermark")).not.toBeChecked();
    expect(screen.getByLabelText("Seed")).toHaveValue(null);
    expect(screen.getByLabelText("Size")).toHaveValue("");
    expect(screen.getByLabelText("Size")).toHaveAttribute("placeholder", "1280*1280");
    fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    const expected = { params: {}, mode: "t2i", batch_size: 2, model_id: id.includes("/") ? id : `qwen/${id}#image` };
    expect(calls.saveDraft).toHaveBeenCalledWith("image", expect.objectContaining(expected));
    expect(calls.createImageCandidate).toHaveBeenCalledWith("image", expect.objectContaining(expected));
  });
  it("preserves explicit zero, false and cleared negative; clearing seed restores omission", async () => {
    const { calls } = mount(saved(ids[1]));
    fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Negative prompt"), { target: { value: "blur" } });
    fireEvent.change(screen.getByLabelText("Negative prompt"), { target: { value: "" } });
    fireEvent.click(screen.getByLabelText("Prompt extend"));
    fireEvent.click(screen.getByLabelText("Watermark")); fireEvent.click(screen.getByLabelText("Watermark"));
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1].params).toEqual({ negative_prompt: "", prompt_extend: false, watermark: false });
    fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "0" } });
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledTimes(2));
    expect(vi.mocked(calls.createImageCandidate).mock.calls[1][1].params).toEqual({ seed: 0, negative_prompt: "", prompt_extend: false, watermark: false });
  });
  it.each([{ seed: -1 }, { seed: 2147483648 }, { size: "wrong" }, { size: "" }, { negative_prompt: "x".repeat(501) }, { negative_prompt: "\ud800" }])("keeps invalid saved business values visible until explicit correction: %j", async invalid => {
    const fixture = saved(ids[0], { watermark: false, prompt_extend: false, ...invalid });
    const before = structuredClone(fixture.image.data);
    const { calls } = mount(fixture);
    expect(generate()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(JSON.stringify(Object.values(invalid)[0]));
    fireEvent.click(generate()); fireEvent.keyDown(screen.getByLabelText("Prompt"), { key: "Enter", ctrlKey: true });
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
    fireEvent.click(correction());
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
    expect(fixture.image.data).toEqual(before);
    await waitFor(() => expect(generate()).toBeEnabled());
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1].params).toEqual({ watermark: false, prompt_extend: false });
  });
  it.each(["\ud800", "\udfff", "月".repeat(5462)])("blocks an invalid Qwen prompt before any save or generation: %j", async prompt => {
    const fixture = saved(); fixture.image.prompt = prompt;
    const { calls } = mount(fixture);
    expect(generate()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("16 KiB");
    fireEvent.keyDown(screen.getByLabelText("Prompt"), { key: "Enter", ctrlKey: true });
    await act(async () => {});
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "A valid prompt" } });
    expect(generate()).toBeEnabled();
  });
  it("counts negative Unicode characters rather than UTF-16 code units", async () => {
    const params = { negative_prompt: "🌕".repeat(500), prompt_extend: false, seed: 0 };
    const { calls } = mount(saved(ids[0], params));
    expect(generate()).toBeEnabled(); fireEvent.click(generate());
    await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1].params).toEqual(params);
  });
  it("keeps invalid values and supported fields during model roundtrip", () => {
    mount(saved(ids[0], { seed: -1, negative_prompt: "old", prompt_extend: false }));
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gpt-image/gpt-image-2#image" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: ids[3] } });
    expect(generate()).toBeDisabled(); expect(screen.getByLabelText("Negative prompt")).toHaveValue("old");
    expect(screen.getByLabelText("Prompt extend")).not.toBeChecked(); expect(screen.getByLabelText("Seed")).toHaveValue(-1);
  });
  it("preserves legacy I2I defaults and seed before missing references attach", () => {
    const fixture = saved(ids[1], { seed: -1 });
    (fixture.image.data.generation_draft as { mode: string }).mode = "i2i";
    mount(fixture);
    expect(screen.getByLabelText("Size")).toHaveValue("1280*1280");
    expect(screen.getByLabelText("Seed")).toHaveValue(-1);
    expect(screen.getByLabelText("Prompt extend")).toBeChecked();
    expect(screen.queryByRole("button", { name: "Remove incompatible settings" })).toBeNull();
  });
  it("keeps the new action busy when an old finally returns after owner A to B to A", async () => {
    let finishOld!: () => void; let finishCurrent!: () => void;
    const saveDraft = vi.fn()
      .mockReturnValueOnce(new Promise<void>(resolve => { finishOld = resolve; }))
      .mockReturnValueOnce(new Promise<void>(resolve => { finishCurrent = resolve; }));
    const { calls, props, rerender } = mount(saved(ids[0], { seed: 0 }), "en", { saveDraft });
    fireEvent.click(generate()); expect(saveDraft).toHaveBeenCalledTimes(1);
    rerender(<NextIntlClientProvider locale="en" messages={en}><MediaComposer {...props} ownerKey="owner-B" /></NextIntlClientProvider>);
    rerender(<NextIntlClientProvider locale="en" messages={en}><MediaComposer {...props} /></NextIntlClientProvider>);
    fireEvent.click(generate()); expect(saveDraft).toHaveBeenCalledTimes(2);
    await act(async () => finishOld());
    expect(screen.getByRole("button", { name: "Generating…" })).toBeDisabled();
    expect(calls.createImageCandidate).not.toHaveBeenCalled();
    await act(async () => finishCurrent());
    await waitFor(() => expect(generate()).toBeEnabled());
    expect(calls.createImageCandidate).toHaveBeenCalledOnce();
  });
  it("does not submit a stale draft after a correction during save", async () => {
    let finish!: () => void;
    const { calls } = mount(saved(ids[0], { seed: 0 }), "en", { saveDraft: vi.fn().mockReturnValue(new Promise<void>(resolve => { finish = resolve; })) });
    fireEvent.click(generate()); fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "-1" } }); fireEvent.click(correction());
    await act(async () => finish()); expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });
});

describe("Qwen malformed saved draft explicit local copy", () => {
  it.each([
    { params: null }, { params: { seed: { old: 0 }, negative_prompt: "", watermark: false } },
    { params: { custom: false, prompt_extend: false, seed: 0 } }, { schema_version: 900 }, { batch_size: 0 },
  ])("diagnoses raw %j without relaxing the shared parser or saving", async changes => {
    const fixture = saved();
    fixture.image.data.generation_draft = { ...(fixture.image.data.generation_draft as object), ...changes } as unknown as AtelierNode["data"][string];
    const raw = structuredClone(fixture.image.data.generation_draft);
    expect(() => parseAtelierMediaDraft(raw)).toThrow();
    const { calls } = mount(fixture);
    expect(generate()).toBeDisabled(); expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    expect(screen.getByLabelText("Original saved settings").textContent).toBe(JSON.stringify(raw, null, 2));
    fireEvent.click(screen.getByRole("button", { name: "Create corrected draft copy" }));
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
    expect(fixture.image.data.generation_draft).toEqual(raw); expect(() => parseAtelierMediaDraft(raw)).toThrow();
    fireEvent.click(generate()); await waitFor(() => expect(calls.createImageCandidate).toHaveBeenCalledOnce());
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1].model_id).toBe("qwen/qwen-image-2.0#image");
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1].params).not.toHaveProperty("custom");
    expect(vi.mocked(calls.createImageCandidate).mock.calls[0][1].params).not.toHaveProperty("seed", { old: 0 });
  });
  it("uses actual Chinese translations for raw diagnostic and correction", () => {
    const fixture = saved(); fixture.image.data.generation_draft = { ...(fixture.image.data.generation_draft as object), params: null } as unknown as AtelierNode["data"][string];
    const { calls } = mount(fixture, "zh");
    expect(screen.getByLabelText("原始保存设置")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建修正草稿副本" }));
    expect(calls.saveDraft).not.toHaveBeenCalled(); expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });
  it("does not infer T2I when a saved T2I record contains references", () => {
    const fixture = saved();
    fixture.image.data.generation_draft = { ...(fixture.image.data.generation_draft as object), media: [{ role: "reference_image", ordinal: 0, node_id: "ref", media_url: "uploads/ref.png" }] } as unknown as AtelierNode["data"][string];
    const { calls } = mount(fixture);
    expect(generate()).toBeDisabled(); expect(screen.queryByRole("button", { name: "Create corrected draft copy" })).toBeNull();
    expect(calls.saveDraft).not.toHaveBeenCalled();
  });
});
