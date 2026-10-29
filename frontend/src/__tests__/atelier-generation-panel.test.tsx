// @vitest-environment happy-dom
import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import messages from "../../messages/en.json";
import packageFixture from "./fixtures/atelier-project-package-wire.json";
import { AtelierProjectApiError, parseAtelierProject } from "@/lib/atelierApi";
import { GenerationPanel } from "@/components/atelier/v3/GenerationPanel";
import { pinnedComposerPlacement } from "@/components/atelier/v3/PinnedComposer";
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierComposerModelOption } from "@/lib/atelierGenParams";
import type { AtelierGenerationAttempt, AtelierNode, AtelierProject } from "@/lib/atelierTypes";

const store = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock("@/store/atelierStore", () => {
    const useAtelierStore = (selector: (state: Record<string, unknown>) => unknown) => selector(store.state);
    useAtelierStore.getState = () => store.state;
    return { useAtelierStore };
});
vi.mock("@/components/shared/preview/PreviewVideo", () => ({ default: ({ src, alt }: { src?: string; alt?: string }) => <div data-testid="preview-video">{alt}:{src}</div> }));

const HASH = "a".repeat(64);
const owner = "env-a:1:project-1:0";

const imageNode = (overrides: Partial<AtelierNode> = {}): AtelierNode => ({
    id: "image-1", project_id: "project-1", type: "image", title: "Hero still", prompt: "", status: "draft",
    x: 0, y: 0, width: 240, height: 160, source_project_id: null, frame_id: null, asset_id: null, video_task_id: null,
    media_urls: ["uploads/hero.png"], data: {}, created_by: "user", created_at: 1, updated_at: 1, ...overrides,
});
const videoNode = (overrides: Partial<AtelierNode> = {}): AtelierNode => ({
    id: "video-1", project_id: "project-1", type: "video", title: "Video take", prompt: "Original prompt", status: "draft",
    x: 0, y: 0, width: 320, height: 180, source_project_id: null, frame_id: null, asset_id: null, video_task_id: null,
    media_urls: [], data: { reference_node_ids: ["image-1"] }, created_by: "user", created_at: 1, updated_at: 1, ...overrides,
});
const providerExecution = (phase = "accepted") => ({
    schema_version: 1, attempt_id: "attempt-1", phase, provider_model_id: "wan2.7-i2v", intent: {}, media: [], scope: {},
    credential_ref: "credential", fingerprint: HASH, handle: null, observation: null, prepared_artifact: null, artifact: null,
});
const attempt = (overrides: Partial<AtelierGenerationAttempt> = {}): AtelierGenerationAttempt => ({
    schema_version: 1, project_id: "project-1", node_id: "video-1", candidate_id: "candidate-1", attempt_id: "attempt-1", attempt_revision: 0,
    status: "pending", error_code: null, first_frame_source: { node_id: "image-1", media_url: "uploads/hero.png" }, output_ref: null,
    provider_execution: providerExecution() as never, created_at: 1, updated_at: 1, ...overrides,
});
const candidate = (overrides: Record<string, unknown> = {}) => ({
    execution_contract: "atelier-provider-video-v1", id: "candidate-1", attempt_id: "attempt-1", media_kind: "video", status: "pending",
    video_url: null, error_code: null, created_at: 1, updated_at: 1, ...overrides,
});

const importedPackageRawProject = (): Record<string, unknown> => {
    const raw = structuredClone(packageFixture.prepared_project) as Record<string, unknown>;
    const node = (raw.nodes as Array<{ id: string; data: Record<string, unknown> }>).find(item => item.id === "fixture-node-3");
    const candidates = node?.data.candidates as Array<Record<string, unknown>> | undefined;
    if (candidates?.[0]) candidates[0].media_kind = "video";
    return raw;
};
const importedPackageProject = (): AtelierProject => parseAtelierProject(importedPackageRawProject());
const importedPackageVideoNode = (p: AtelierProject): AtelierNode => p.nodes.find(node => node.id === "fixture-node-3") as AtelierNode;

const project = (nodes: AtelierNode[] = [imageNode(), videoNode()]): AtelierProject => ({
    id: "project-1", title: "Canvas", description: "", source_project_id: null, nodes,
    agent_policy: { approval_mode: "untrusted", allowed_tools: [], max_nodes_per_action: 8, max_tokens_per_turn: 2048, enable_reviewer: false, max_generation_cost_per_turn: 5, updated_at: 1 },
    agent_turns: [], sequence: [], audio_tracks: [], exports: [], node_delete_receipts: [], node_upload_receipts: [], sequence_export_jobs: [],
    generation_attempts: [attempt()], generation_request_receipts: [], agent_compaction_summary: null, compacted_through_turn_id: null,
    revision: 3, created_at: 1, updated_at: 1,
});

function mount(p = project(), selected = p.nodes.find(node => node.id === "video-1") ?? null, ownerKey = owner, disabled = false, models?: AtelierComposerModelOption[], targetRect?: { left: number; top: number; width: number; height: number }) {
    store.state.currentProject = p;
    store.state.selectedNodeId = selected?.id ?? null;
    return render(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <GenerationPanel project={p} selectedNode={selected} ownerKey={ownerKey} disabled={disabled} models={models} targetRect={targetRect} />
    </NextIntlClientProvider>);
}

function wan3Models(): AtelierComposerModelOption[] {
    return [
        { id: "wan/wan2.7-video#i2v", name: "Wan 2.7 I2V", params: { resolution: { options: ["480p", "720p"], default: "720p" }, promptExtend: true, watermark: true }, duration: { type: "slider", default: 5, min: 2, max: 15, step: 1 } },
        { id: "wan/wan3.0-video#video", name: "Wan 3.0 Video", params: { resolution: { options: ["1080P", "720P", "480P"], default: "720P" }, ratio: { options: ["adaptive", "16:9", "9:16"], default: "adaptive" }, audio: { default: true }, promptExtend: { default: true }, watermark: { default: false } }, duration: { type: "slider", default: 5, min: 2, max: 30, step: 1, supports_auto: true } },
    ];
}

function selectChip(label: string, optionName: string | RegExp) {
    fireEvent.click(screen.getByRole("button", { name: label }));
    fireEvent.click(screen.getByRole("option", { name: optionName }));
}

function setDuration(value: number) {
    const chip = screen.getByRole("button", { name: "Duration" });
    fireEvent.click(chip);
    fireEvent.change(within(screen.getByRole("dialog", { name: "Duration" })).getByRole("slider", { name: "Duration" }), { target: { value: String(value) } });
    fireEvent.click(chip);
}

function setSmartDuration() {
    const chip = screen.getByRole("button", { name: "Duration" });
    fireEvent.click(chip);
    fireEvent.click(within(screen.getByRole("dialog", { name: "Duration" })).getByRole("button", { name: /Smart duration/ }));
    fireEvent.click(chip);
}

beforeEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: "11111111-1111-4111-8111-111111111111" });
    store.state = {
        pendingGenerations: [], isSaving: false, needsRefresh: false,
        createVideoCandidate: vi.fn().mockResolvedValue({}), loadPendingGenerations: vi.fn().mockReturnValue([]), readbackPendingGeneration: vi.fn().mockResolvedValue(null),
        refreshGenerationAttempt: vi.fn().mockResolvedValue({}), continueGenerationAttempt: vi.fn().mockResolvedValue({}), cancelGenerationAttempt: vi.fn().mockResolvedValue({}),
        selectGenerationCandidate: vi.fn().mockResolvedValue({ project: project(), node_ids: ["video-1"] }),
        setGenerationCandidateVisibility: vi.fn().mockResolvedValue({ project: project(), node_ids: ["video-1"] }),
        setCandidateJudgment: vi.fn().mockResolvedValue({ project: project(), node_ids: ["video-1"] }),
        branchFromCandidate: vi.fn().mockResolvedValue({ project: project(), node_ids: ["branch-1"] }),
        saveGenerationDraft: vi.fn().mockResolvedValue({ project: project(), node_ids: ["video-1"] }),
    };
});

describe("Atelier generation panel", () => {
    it("only enables Wan 2.7 I2V creation for video nodes with an attached image first frame", async () => {
        mount(project([videoNode()]));
        expect(screen.getByRole("button", { name: "Create Wan 2.7 take" })).toBeDisabled();
        expect(screen.getByText("Attach an image node with media before creating an I2V take.")).toBeInTheDocument();
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("submits explicit prompt and catalog-backed common params without revision or request id", async () => {
        mount();
        fireEvent.change(screen.getByLabelText("Generation prompt"), { target: { value: "New cinematic prompt" } });
        setDuration(7);
        selectChip("Resolution", "720p");
        fireEvent.click(screen.getByRole("button", { name: "Advanced parameters" }));
        fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "42" } });
        fireEvent.change(screen.getByLabelText("Negative prompt"), { target: { value: "blur" } });
        fireEvent.click(screen.getByLabelText("Watermark"));
        fireEvent.click(screen.getByRole("button", { name: "Create Wan 2.7 take" }));

        await waitFor(() => expect(store.state.createVideoCandidate).toHaveBeenCalledTimes(1));
        expect(store.state.createVideoCandidate).toHaveBeenCalledWith("video-1", {
            model_id: "wan/wan2.7-video#i2v", mode: "i2v", prompt: "New cinematic prompt",
            first_frame: { node_id: "image-1", media_url: "uploads/hero.png" },
            params: { duration: 7, resolution: "720p", seed: 42, negative_prompt: "blur", prompt_extend: true, watermark: true },
        });
    });

    it("preserves explicit false prompt_extend and watermark values", async () => {
        mount();
        fireEvent.click(screen.getByRole("button", { name: "Advanced parameters" }));
        fireEvent.click(screen.getByLabelText("Prompt extend"));
        fireEvent.click(screen.getByRole("button", { name: "Create Wan 2.7 take" }));

        await waitFor(() => expect(store.state.createVideoCandidate).toHaveBeenCalledTimes(1));
        expect(store.state.createVideoCandidate).toHaveBeenCalledWith("video-1", expect.objectContaining({
            params: expect.objectContaining({ prompt_extend: false, watermark: false }),
        }));
    });

    it("blocks create when store ownership is stale before save or changes after save", async () => {
        const p = project();
        const staleSave = vi.fn().mockResolvedValue({});
        store.state.saveGenerationDraft = staleSave;
        const view = mount(p);
        store.state.currentProject = { ...p, id: "project-2" };

        fireEvent.click(screen.getByRole("button", { name: "Create Wan 2.7 take" }));

        expect(staleSave).not.toHaveBeenCalled();
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
        view.unmount();

        const saveThenSwitch = vi.fn().mockImplementation(async () => {
            store.state.currentProject = { ...p, id: "project-2", nodes: p.nodes.map(node => ({ ...node, project_id: "project-2" })) };
            store.state.selectedNodeId = "video-1";
            return {};
        });
        store.state.saveGenerationDraft = saveThenSwitch;
        store.state.createVideoCandidate = vi.fn().mockResolvedValue({});
        mount(p);

        fireEvent.click(screen.getByRole("button", { name: "Create Wan 2.7 take" }));

        await waitFor(() => expect(saveThenSwitch).toHaveBeenCalledTimes(1));
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("loads saved generation draft settings and saves exact current params", async () => {
        const draftNode = videoNode({ prompt: "Saved prompt", data: { reference_node_ids: ["image-1"], generation_draft: {
            schema_version: 1, model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: { duration: 7, resolution: "720p", seed: -1, prompt_extend: false, watermark: false },
        } } });
        mount(project([imageNode(), draftNode]));

        expect(screen.getByLabelText("Generation prompt")).toHaveValue("Saved prompt");
        expect(screen.getByRole("button", { name: "Duration" })).toHaveTextContent("7s");
        expect(screen.getByLabelText("Resolution")).toHaveValue("720p");
        fireEvent.click(screen.getByRole("button", { name: "Advanced parameters" }));
        expect(screen.getByLabelText("Seed")).toHaveValue("-1");
        expect(screen.getByLabelText("Prompt extend")).not.toBeChecked();
        expect(screen.getByLabelText("Watermark")).not.toBeChecked();

        fireEvent.click(screen.getByRole("button", { name: "Save generation settings" }));

        await waitFor(() => expect(store.state.saveGenerationDraft).toHaveBeenCalledWith("video-1", {
            prompt: "Saved prompt",
            model_id: "wan/wan2.7-video#i2v",
            mode: "i2v",
            params: { duration: 7, resolution: "720p", seed: -1, prompt_extend: false, watermark: false },
        }));
    });

    it("restores generation defaults by saving an empty params override", async () => {
        mount();
        fireEvent.change(screen.getByLabelText("Generation prompt"), { target: { value: "" } });
        fireEvent.click(screen.getByRole("button", { name: "Advanced parameters" }));
        fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "-1" } });
        fireEvent.click(screen.getByLabelText("Prompt extend"));

        fireEvent.click(screen.getByRole("button", { name: "Restore generation defaults" }));

        await waitFor(() => expect(store.state.saveGenerationDraft).toHaveBeenCalledWith("video-1", {
            prompt: "",
            model_id: "wan/wan2.7-video#i2v",
            mode: "i2v",
            params: {},
        }));
    });

    it("does not apply stale restore-default resets after local edits or node changes", async () => {
        let finish!: () => void;
        store.state.saveGenerationDraft = vi.fn(() => new Promise(resolve => { finish = () => resolve({}); }));
        const second = videoNode({ id: "video-2", title: "Second take", prompt: "Second prompt", data: { reference_node_ids: ["image-1"], generation_draft: {
            schema_version: 1, model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: { duration: 7, resolution: "720p" },
        } } });
        const p = project([imageNode(), videoNode(), second]);
        const view = mount(p, p.nodes[1], owner);

        fireEvent.click(screen.getByRole("button", { name: "Restore generation defaults" }));
        fireEvent.change(screen.getByLabelText("Generation prompt"), { target: { value: "edited during restore" } });
        await act(async () => { finish(); });
        expect(screen.getByLabelText("Generation prompt")).toHaveValue("edited during restore");

        store.state.saveGenerationDraft = vi.fn(() => new Promise(resolve => { finish = () => resolve({}); }));
        fireEvent.click(screen.getByRole("button", { name: "Restore generation defaults" }));
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={p} selectedNode={p.nodes[2]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.getByLabelText("Generation prompt")).toHaveValue("Second prompt");
        expect(screen.getByRole("button", { name: "Duration" })).toHaveTextContent("7s");
        await act(async () => { finish(); });
        expect(screen.getByLabelText("Generation prompt")).toHaveValue("Second prompt");
        expect(screen.getByRole("button", { name: "Duration" })).toHaveTextContent("7s");
    });

    it("requires an explicit reset before using defaults over a malformed saved draft", async () => {
        const malformed = videoNode({ data: { reference_node_ids: ["image-1"], generation_draft: 42 as never } });
        const view = mount(project([imageNode(), malformed]));

        expect(screen.getByText("Saved generation settings use an unsupported format. Restore defaults before saving or creating a new take.")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Save generation settings" })).toBeDisabled();
        expect(screen.getByRole("button", { name: "Create Wan 2.7 take" })).toBeDisabled();

        const badParams = videoNode({ data: { reference_node_ids: ["image-1"], generation_draft: {
            schema_version: 1, model_id: "wan/wan2.7-video#i2v", mode: "i2v", params: { unknown_param: true },
        } } });
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={project([imageNode(), badParams])} selectedNode={badParams} ownerKey={`${owner}:bad-params`} disabled={false} />
        </NextIntlClientProvider>);

        expect(screen.getByText("Saved generation settings use an unsupported format. Restore defaults before saving or creating a new take.")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Create Wan 2.7 take" })).toBeDisabled();

        fireEvent.click(screen.getByRole("button", { name: "Restore generation defaults" }));

        await waitFor(() => expect(store.state.saveGenerationDraft).toHaveBeenCalledWith("video-1", {
            prompt: "Original prompt",
            model_id: "wan/wan2.7-video#i2v",
            mode: "i2v",
            params: {},
        }));
        await waitFor(() => expect(screen.getByRole("button", { name: "Create Wan 2.7 take" })).not.toBeDisabled());
    });

    it("keeps local input while saving generation draft and while polling completes", async () => {
        let finish!: () => void;
        store.state.saveGenerationDraft = vi.fn(() => new Promise(resolve => { finish = () => resolve({}); }));
        mount();
        fireEvent.change(screen.getByLabelText("Generation prompt"), { target: { value: "before save" } });
        fireEvent.click(screen.getByRole("button", { name: "Save generation settings" }));
        fireEvent.change(screen.getByLabelText("Generation prompt"), { target: { value: "kept local input" } });
        await act(async () => { finish(); });
        expect(screen.getByLabelText("Generation prompt")).toHaveValue("kept local input");
    });

    it("reads a lost ACK journal entry without creating a replacement", async () => {
        store.state.pendingGenerations = [{ clientRequestId: "generation-lost", nodeId: "video-1", request: { prompt: "Lost", first_frame: { node_id: "image-1", media_url: "uploads/hero.png" } } }];
        mount();
        fireEvent.click(screen.getByRole("button", { name: "Read original request" }));
        await waitFor(() => expect(store.state.readbackPendingGeneration).toHaveBeenCalledWith("generation-lost"));
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
        expect(await screen.findByText("Original request is still unconfirmed. No replacement was submitted.")).toBeInTheDocument();
    });

    it("keeps project-level readback available when edits are blocked or the source node is gone", async () => {
        store.state.needsRefresh = true;
        store.state.pendingGenerations = [{ clientRequestId: "generation-deleted", nodeId: "deleted-video", request: { prompt: "Lost" } }];
        mount(project(), null, owner, true);

        const button = screen.getByRole("button", { name: "Read original request" });
        expect(button).not.toBeDisabled();
        fireEvent.click(button);

        await waitFor(() => expect(store.state.readbackPendingGeneration).toHaveBeenCalledWith("generation-deleted"));
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("does not silently replace an explicit first frame selection when that media disappears", async () => {
        const secondImage = imageNode({ id: "image-2", title: "Second still", media_urls: ["uploads/second.png"] });
        const p = project([imageNode(), secondImage, videoNode({ data: { reference_node_ids: ["image-1", "image-2"] } })]);
        const view = mount(p);
        selectChip("First frame", /Second still/);

        const changed = project([imageNode(), videoNode({ data: { reference_node_ids: ["image-1", "image-2"] } })]);
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={changed} selectedNode={changed.nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);

        expect(screen.getByText("Selected first frame is no longer available. Choose a valid first frame before creating.")).toBeInTheDocument();
        const firstFrameSelect = screen.getByLabelText("First frame");
        fireEvent.click(screen.getByRole("button", { name: "First frame" }));
        expect(screen.getByRole("option", { name: "Missing selected first frame" })).toBeDisabled();
        expect(firstFrameSelect).toHaveValue("image-2:uploads/second.png");
        expect(screen.getByRole("button", { name: "Create Wan 2.7 take" })).toBeDisabled();
        fireEvent.click(screen.getByRole("button", { name: "Create Wan 2.7 take" }));
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("polls accepted and submitted attempts with serial GET while preserving a local prompt draft", async () => {
        vi.useFakeTimers();
        const releases: Array<() => void> = [];
        store.state.refreshGenerationAttempt = vi.fn(() => new Promise(resolve => { releases.push(() => resolve({})); }));
        const p = project();
        p.generation_attempts = [
            attempt({ attempt_id: "attempt-1", provider_execution: providerExecution("accepted") as never }),
            attempt({ attempt_id: "attempt-2", candidate_id: "candidate-2", provider_execution: { ...providerExecution("submitted"), attempt_id: "attempt-2" } as never }),
        ];
        mount(p);
        fireEvent.change(screen.getByLabelText("Generation prompt"), { target: { value: "Draft survives polling" } });
        await act(async () => { vi.advanceTimersByTime(1100); });
        expect(store.state.refreshGenerationAttempt).toHaveBeenCalledTimes(1);
        await act(async () => { vi.advanceTimersByTime(3000); });
        expect(store.state.refreshGenerationAttempt).toHaveBeenCalledTimes(1);
        await act(async () => { releases[0](); await Promise.resolve(); });
        expect(store.state.refreshGenerationAttempt).toHaveBeenCalledTimes(2);
        await act(async () => { releases[1](); await Promise.resolve(); });
        expect(screen.getByLabelText("Generation prompt")).toHaveValue("Draft survives polling");
        vi.useRealTimers();
    });


    it("continues needs-confirmation and provider progress attempts without exposing terminal retries", async () => {
        const p = project();
        p.generation_attempts = [
            attempt({ attempt_id: "attempt-needs", candidate_id: "candidate-needs", status: "needs_confirmation", updated_at: 4, provider_execution: { ...providerExecution("submitted"), attempt_id: "attempt-needs" } as never }),
            attempt({ attempt_id: "attempt-observed", candidate_id: "candidate-observed", updated_at: 3, provider_execution: { ...providerExecution("observed"), attempt_id: "attempt-observed" } as never }),
            attempt({ attempt_id: "attempt-accepted", candidate_id: "candidate-accepted", updated_at: 2, provider_execution: { ...providerExecution("accepted"), attempt_id: "attempt-accepted" } as never }),
            attempt({ attempt_id: "attempt-failed", candidate_id: "candidate-failed", status: "failed", updated_at: 1, provider_execution: { ...providerExecution("observed"), attempt_id: "attempt-failed" } as never }),
        ];
        mount(p);

        const continueButtons = screen.getAllByRole("button", { name: "Continue original task" });
        expect(continueButtons).toHaveLength(3);
        expect(screen.getByText("attempt-failed").closest("div")?.parentElement).not.toHaveTextContent("Continue original task");

        fireEvent.click(continueButtons[0]);
        await waitFor(() => expect(store.state.continueGenerationAttempt).toHaveBeenCalledWith("attempt-needs"));
        fireEvent.click(continueButtons[1]);
        await waitFor(() => expect(store.state.continueGenerationAttempt).toHaveBeenCalledWith("attempt-observed"));
        fireEvent.click(continueButtons[2]);
        await waitFor(() => expect(store.state.continueGenerationAttempt).toHaveBeenCalledWith("attempt-accepted"));
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("continues submitted provider work with POST instead of only exposing GET refresh", async () => {
        const p = project();
        p.generation_attempts = [attempt({ provider_execution: providerExecution("observed") as never })];
        mount(p);

        fireEvent.click(screen.getByRole("button", { name: "Continue original task" }));

        await waitFor(() => expect(store.state.continueGenerationAttempt).toHaveBeenCalledWith("attempt-1"));
        expect(store.state.refreshGenerationAttempt).not.toHaveBeenCalled();
    });

    it("clears a stale continue conflict by code only after a newer read proves the same attempt advanced", async () => {
        store.state.continueGenerationAttempt = vi.fn().mockRejectedValue(new AtelierProjectApiError("Remote changed", "storage_conflict", 409));
        const p = project();
        p.generation_attempts = [attempt({ attempt_revision: 0, provider_execution: providerExecution("observed") as never })];
        const view = mount(p);

        fireEvent.click(screen.getByRole("button", { name: "Continue original task" }));

        expect(await screen.findByRole("alert")).toHaveTextContent("Remote changed");

        const unrelated = project();
        unrelated.generation_attempts = [attempt({ attempt_id: "attempt-other", attempt_revision: 4, provider_execution: { ...providerExecution("observed"), attempt_id: "attempt-other" } as never })];
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={unrelated} selectedNode={unrelated.nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.getByRole("alert")).toHaveTextContent("Remote changed");

        const advanced = project();
        advanced.generation_attempts = [attempt({ attempt_revision: 1, status: "completed", provider_execution: providerExecution("observed") as never })];
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={advanced} selectedNode={advanced.nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);

        await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    });

    it("does not clear a non-API continue error whose message looks like a storage conflict", async () => {
        store.state.continueGenerationAttempt = vi.fn().mockRejectedValue(new Error("reload and retry after local guard"));
        const p = project();
        p.generation_attempts = [attempt({ attempt_revision: 0, provider_execution: providerExecution("observed") as never })];
        const view = mount(p);

        fireEvent.click(screen.getByRole("button", { name: "Continue original task" }));

        expect(await screen.findByRole("alert")).toHaveTextContent("reload and retry after local guard");
        const advanced = project();
        advanced.generation_attempts = [attempt({ attempt_revision: 1, status: "completed", provider_execution: providerExecution("observed") as never })];
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={advanced} selectedNode={advanced.nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.getByRole("alert")).toHaveTextContent("reload and retry after local guard");
    });

    it("does not let an older conflict token clear a newer polling error", async () => {
        store.state.continueGenerationAttempt = vi.fn().mockRejectedValue(new AtelierProjectApiError("Remote changed", "storage_conflict", 409));
        store.state.refreshGenerationAttempt = vi.fn().mockRejectedValue(new Error("poll network down"));
        const p = project();
        p.generation_attempts = [attempt({ attempt_revision: 0, provider_execution: providerExecution("observed") as never })];
        const view = mount(p);

        fireEvent.click(screen.getByRole("button", { name: "Continue original task" }));
        expect(await screen.findByRole("alert")).toHaveTextContent("Remote changed");
        await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("poll network down"));

        const advanced = project();
        advanced.generation_attempts = [attempt({ attempt_revision: 1, status: "completed", provider_execution: providerExecution("observed") as never })];
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={advanced} selectedNode={advanced.nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.getByRole("alert")).toHaveTextContent("poll network down");
    });

    it("never auto-selects completed candidates and requires explicit adoption", async () => {
        const completed = candidate({ status: "completed", video_url: "video/take.mp4" });
        mount(project([imageNode(), videoNode({ data: { candidates: [completed] } })]));
        expect(await screen.findByTestId("preview-video")).toHaveTextContent("Video take candidate:video/take.mp4");
        expect(store.state.selectGenerationCandidate).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "Use this take" }));
        await waitFor(() => expect(store.state.selectGenerationCandidate).toHaveBeenCalledWith("video-1", "candidate-1"));
    });


    it("creates a video branch draft without selecting, generating, reviewing, or hiding the take", async () => {
        const completed = candidate({ status: "completed", video_url: "video/take.mp4" });
        mount(project([imageNode(), videoNode({ data: { candidates: [completed] } })]));

        fireEvent.click(screen.getByRole("button", { name: "Create branch draft" }));

        await waitFor(() => expect(store.state.branchFromCandidate).toHaveBeenCalledWith("video-1", "candidate-1"));
        expect(store.state.selectGenerationCandidate).not.toHaveBeenCalled();
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
        expect(store.state.setGenerationCandidateVisibility).not.toHaveBeenCalled();
        expect(store.state.setCandidateJudgment).not.toHaveBeenCalled();
        expect(await screen.findByText("Branch draft created. Edit the draft, then generate explicitly.")).toBeInTheDocument();
    });

    it("clears a branch lost-ACK error only after an explicit authoritative refresh advances the project", async () => {
        store.state.branchFromCandidate = vi.fn().mockImplementation(async () => {
            store.state.needsRefresh = true;
            throw new Error("Failed to fetch");
        });
        const completed = candidate({ status: "completed", video_url: "video/take.mp4" });
        const base = project([imageNode(), videoNode({ data: { candidates: [completed] } })]);
        const view = mount(base);

        fireEvent.click(screen.getByRole("button", { name: "Create branch draft" }));

        expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={base} selectedNode={base.nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.getByRole("alert")).toHaveTextContent("Failed to fetch");

        store.state.needsRefresh = false;
        const refreshed = { ...base, revision: base.revision + 1, nodes: [...base.nodes, videoNode({ id: "branch-1", prompt: "Branch", data: { branch_source: { schema_version: 1, origin_project_id: "project-1", origin_node_id: "video-1", origin_candidate_id: "candidate-1", media_kind: "video", media_url: "uploads/branch.mp4", media_sha256: HASH, media_size_bytes: 123, recipe_status: "available" } } })] };
        store.state.currentProject = refreshed;
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={refreshed} selectedNode={refreshed.nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);

        await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
        expect(store.state.branchFromCandidate).toHaveBeenCalledTimes(1);
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("keeps unrelated branch errors through project updates that were not refresh recoveries", async () => {
        store.state.branchFromCandidate = vi.fn().mockRejectedValue(new Error("permission denied"));
        const completed = candidate({ status: "completed", video_url: "video/take.mp4" });
        const base = project([imageNode(), videoNode({ data: { candidates: [completed] } })]);
        const view = mount(base);

        fireEvent.click(screen.getByRole("button", { name: "Create branch draft" }));

        expect(await screen.findByRole("alert")).toHaveTextContent("permission denied");
        const changed = { ...base, revision: base.revision + 1 };
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={changed} selectedNode={changed.nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.getByRole("alert")).toHaveTextContent("permission denied");
    });

    it("does not clear API branch errors even when the store requires a refresh", async () => {
        store.state.branchFromCandidate = vi.fn().mockImplementation(async () => {
            store.state.needsRefresh = true;
            throw new AtelierProjectApiError("permission denied", "atelier_project_request_failed", 403);
        });
        const completed = candidate({ status: "completed", video_url: "video/take.mp4" });
        const base = project([imageNode(), videoNode({ data: { candidates: [completed] } })]);
        const view = mount(base);

        fireEvent.click(screen.getByRole("button", { name: "Create branch draft" }));

        expect(await screen.findByRole("alert")).toHaveTextContent("permission denied");
        store.state.needsRefresh = false;
        const changed = { ...base, revision: base.revision + 1 };
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={changed} selectedNode={changed.nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.getByRole("alert")).toHaveTextContent("permission denied");
    });

    it("does not expose branch creation for hidden video candidates and explains source-only branch drafts", () => {
        const hidden = candidate({ status: "completed", video_url: "video/take.mp4" });
        const sourceDraft = videoNode({ data: {
            deleted_candidates: [hidden],
            branch_source: { schema_version: 1, origin_project_id: "project-1", origin_node_id: "video-old", origin_candidate_id: "candidate-old", media_kind: "video", media_url: "uploads/branch-old.mp4", media_sha256: "a".repeat(64), media_size_bytes: 123, recipe_status: "unavailable_imported" },
        } });
        mount(project([imageNode(), sourceDraft]), sourceDraft);

        const card = screen.getByTestId("branch-source-card");
        expect(screen.queryByRole("button", { name: "Create branch draft" })).not.toBeInTheDocument();
        expect(card).toHaveTextContent("Only the source media was preserved");
        const sourceVideo = within(card).getByLabelText("Branch source media") as HTMLVideoElement;
        expect(sourceVideo).toHaveAttribute("controls");
        expect(sourceVideo).toHaveAttribute("playsinline");
        expect(sourceVideo).toHaveAttribute("src", expect.stringContaining("/files/uploads/branch-old.mp4"));
        expect(within(card).queryByRole("button")).not.toBeInTheDocument();
        expect(within(card).queryByRole("link")).not.toBeInTheDocument();
    });

    it("records video candidate judgment without selecting generating or changing visibility", async () => {
        const completed = candidate({ status: "completed", video_url: "video/take.mp4" });
        mount(project([imageNode(), videoNode({ data: { candidates: [completed] } })]));

        fireEvent.click(screen.getByRole("button", { name: "Review" }));
        fireEvent.click(screen.getByLabelText("Keep"));
        fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Strong pacing" } });
        fireEvent.click(screen.getByRole("button", { name: "Save review" }));

        await waitFor(() => expect(store.state.setCandidateJudgment).toHaveBeenCalledWith("video-1", "candidate-1", {
            verdict: "keep",
            reason: "Strong pacing",
        }));
        expect(store.state.selectGenerationCandidate).not.toHaveBeenCalled();
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
        expect(store.state.setGenerationCandidateVisibility).not.toHaveBeenCalled();
        expect(await screen.findByText("Review saved.")).toBeInTheDocument();
    });

    it("opens hidden video candidate judgment history as read-only", async () => {
        const hidden = candidate({ status: "completed", video_url: "video/take.mp4" });
        mount(project([imageNode(), videoNode({ data: { candidates: [], deleted_candidates: [hidden] } })]));

        fireEvent.click(screen.getByRole("button", { name: "Review" }));

        expect(screen.getByRole("dialog", { name: "Candidate review" })).toBeInTheDocument();
        expect(screen.getByText(/Hidden candidates keep their review history/)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Save review" })).not.toBeInTheDocument();
        expect(store.state.setCandidateJudgment).not.toHaveBeenCalled();
        expect(store.state.selectGenerationCandidate).not.toHaveBeenCalled();
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
        expect(store.state.setGenerationCandidateVisibility).not.toHaveBeenCalled();
    });


    it("hides completed visible candidates without selecting them", async () => {
        const completed = candidate({ status: "completed", video_url: "video/take.mp4" });
        mount(project([imageNode(), videoNode({ data: { candidates: [completed] } })]));

        fireEvent.click(screen.getByRole("button", { name: "Hide take" }));

        await waitFor(() => expect(store.state.setGenerationCandidateVisibility).toHaveBeenCalledWith("video-1", "candidate-1", false));
        expect(store.state.selectGenerationCandidate).not.toHaveBeenCalled();
        expect(await screen.findByText("Take hidden. Existing sequence entries are not changed.")).toBeInTheDocument();
    });

    it("does not expose hide or restore for active candidates", async () => {
        mount(project([imageNode(), videoNode({ data: { candidates: [candidate({ status: "pending", video_url: null })] } })]));

        expect(screen.queryByRole("button", { name: "Hide take" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Restore take" })).not.toBeInTheDocument();
    });

    it("does not offer visibility commands for an unsupported legacy status spelling", () => {
        mount(project([imageNode(), videoNode({ data: { candidates: [{ id: "old", status: "canceled" }] } })]));
        expect(screen.queryByRole("button", { name: "Hide take" })).not.toBeInTheDocument();
    });

    it("restores hidden terminal candidates without auto-selecting them", async () => {
        const hidden = candidate({ status: "completed", video_url: "video/take.mp4" });
        mount(project([imageNode(), videoNode({ data: { candidates: [], deleted_candidates: [hidden] } })]));

        expect(screen.getByText("Hidden")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Use this take" })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Restore take" }));

        await waitFor(() => expect(store.state.setGenerationCandidateVisibility).toHaveBeenCalledWith("video-1", "candidate-1", true));
        expect(store.state.selectGenerationCandidate).not.toHaveBeenCalled();
        expect(await screen.findByText("Take restored. Select it explicitly when you want to use it.")).toBeInTheDocument();
    });

    it("lets imported terminal legacy takes hide and restore without generation authority", async () => {
        const legacy = { id: "legacy-1", title: "Imported take", status: "completed", video_url: "video/imported.mp4" };
        const view = mount(project([imageNode(), videoNode({ data: { candidates: [legacy] } })]));

        fireEvent.click(screen.getByRole("button", { name: "Hide take" }));
        await waitFor(() => expect(store.state.setGenerationCandidateVisibility).toHaveBeenCalledWith("video-1", "legacy-1", false));
        expect(screen.queryByRole("button", { name: "Use this take" })).not.toBeInTheDocument();

        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={project([imageNode(), videoNode({ data: { candidates: [], deleted_candidates: [legacy] } })])} selectedNode={project([imageNode(), videoNode({ data: { candidates: [], deleted_candidates: [legacy] } })]).nodes[1]} ownerKey={owner} disabled={false} />
        </NextIntlClientProvider>);

        fireEvent.click(screen.getByRole("button", { name: "Restore take" }));
        await waitFor(() => expect(store.state.setGenerationCandidateVisibility).toHaveBeenCalledWith("video-1", "legacy-1", true));
    });

    it("lets safe imported package candidates be explicitly selected without auto-selecting", async () => {
        const p = importedPackageProject();
        mount(p, importedPackageVideoNode(p), `${owner}:fixture-node-3`);

        expect(await screen.findByTestId("preview-video")).toHaveTextContent("A neon alley take:video/imported/fixture-project/take.mp4");
        expect(screen.getByText("Imported take: preview it, then explicitly use it when it belongs in this node.")).toBeInTheDocument();
        expect(store.state.selectGenerationCandidate).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole("button", { name: "Use this take" }));

        await waitFor(() => expect(store.state.selectGenerationCandidate).toHaveBeenCalledWith("fixture-node-3", "fixture-candidate"));
    });

    it("keeps hidden or authority-bearing imported package candidates readonly", () => {
        const rawHidden = importedPackageRawProject();
        const hiddenNode = (rawHidden.nodes as Array<{ id: string; data: Record<string, unknown> }>).find(node => node.id === "fixture-node-3");
        expect(hiddenNode).toBeDefined();
        hiddenNode!.data.deleted_candidates = hiddenNode!.data.candidates;
        hiddenNode!.data.candidates = [];
        const hiddenProject = parseAtelierProject(rawHidden);
        const view = mount(hiddenProject, importedPackageVideoNode(hiddenProject), `${owner}:fixture-node-3`);
        expect(screen.queryByRole("button", { name: "Use this take" })).not.toBeInTheDocument();
        expect(screen.getByText("Hidden imported take: restore it when you want to review or use it. It will not be selected automatically.")).toBeInTheDocument();

        const rawAuthority = importedPackageRawProject();
        const authorityNode = (rawAuthority.nodes as Array<{ id: string; data: Record<string, unknown> }>).find(node => node.id === "fixture-node-3");
        expect(authorityNode).toBeDefined();
        const authorityCandidates = authorityNode!.data.candidates as Array<Record<string, unknown>>;
        authorityCandidates[0].attempt_id = "attempt-1";
        const authorityProject = parseAtelierProject(rawAuthority);
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={authorityProject} selectedNode={importedPackageVideoNode(authorityProject)} ownerKey={`${owner}:fixture-node-3`} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.queryByRole("button", { name: "Use this take" })).not.toBeInTheDocument();

        const rawImage = importedPackageRawProject();
        const imageNode = (rawImage.nodes as Array<{ id: string; data: Record<string, unknown> }>).find(node => node.id === "fixture-node-3");
        expect(imageNode).toBeDefined();
        const imageCandidates = imageNode!.data.candidates as Array<Record<string, unknown>>;
        imageCandidates[0].media_kind = "image";
        const imageProject = parseAtelierProject(rawImage);
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={imageProject} selectedNode={importedPackageVideoNode(imageProject)} ownerKey={`${owner}:fixture-node-3`} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.queryByRole("button", { name: "Use this take" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Hide take" })).not.toBeInTheDocument();

        const rawHiddenImage = importedPackageRawProject();
        const hiddenImageNode = (rawHiddenImage.nodes as Array<{ id: string; data: Record<string, unknown> }>).find(node => node.id === "fixture-node-3");
        expect(hiddenImageNode).toBeDefined();
        const hiddenImageCandidates = hiddenImageNode!.data.candidates as Array<Record<string, unknown>>;
        hiddenImageCandidates[0].media_kind = "image";
        hiddenImageNode!.data.deleted_candidates = hiddenImageNode!.data.candidates;
        hiddenImageNode!.data.candidates = [];
        const hiddenImageProject = parseAtelierProject(rawHiddenImage);
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={hiddenImageProject} selectedNode={importedPackageVideoNode(hiddenImageProject)} ownerKey={`${owner}:fixture-node-3`} disabled={false} />
        </NextIntlClientProvider>);
        expect(screen.queryByRole("button", { name: "Restore take" })).not.toBeInTheDocument();
    });

    it("ignores late local notices after owner changes", async () => {
        let finish!: () => void;
        store.state.saveGenerationDraft = vi.fn(() => new Promise(resolve => { finish = () => resolve({}); }));
        const view = mount();
        fireEvent.click(screen.getByRole("button", { name: "Create Wan 2.7 take" }));
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={project()} selectedNode={project().nodes[1]} ownerKey="env-b:2:project-1:1" disabled={false} />
        </NextIntlClientProvider>);
        await act(async () => { finish(); });
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
        expect(screen.queryByText("Generation request submitted. Watch the take list below.")).not.toBeInTheDocument();
    });


    it("saves a Wan 3 ordered first/last draft before creating the candidate request", async () => {
        const lastImage = imageNode({ id: "image-2", title: "Last still", media_urls: ["uploads/last.png"] });
        const video = videoNode({ data: { reference_node_ids: ["image-1", "image-2"] } });
        const p = project([imageNode(), lastImage, video]);
        mount(p, video, owner, false, wan3Models());

        selectChip("Model", "Wan 3.0 Video");
        selectChip("Mode", "I2V");
        fireEvent.change(screen.getByLabelText("Generation prompt"), { target: { value: "Wan 3 first last motion" } });
        selectChip("First frame", /Hero still/);
        selectChip("Last frame", /Last still/);
        setSmartDuration();
        selectChip("Resolution", "480P");
        selectChip("Aspect", "16:9");
        fireEvent.click(screen.getByRole("button", { name: "Advanced parameters" }));
        fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "0" } });
        fireEvent.click(screen.getByLabelText("Audio"));
        selectChip("Candidates", "6");
        fireEvent.click(screen.getByRole("button", { name: "Generate" }));

        const expected = {
            contract_version: "ordered-media-v1",
            model_id: "wan/wan3.0-video#video",
            mode: "i2v",
            prompt: "Wan 3 first last motion",
            params: { duration: -1, resolution: "480P", prompt_extend: true, watermark: false, seed: 0, ratio: "16:9", audio: false },
            media: [
                { role: "first_frame", ordinal: 0, node_id: "image-1", media_url: "uploads/hero.png" },
                { role: "last_frame", ordinal: 0, node_id: "image-2", media_url: "uploads/last.png" },
            ],
            batch_size: 6,
        };
        await waitFor(() => expect(store.state.createVideoCandidate).toHaveBeenCalledTimes(1));
        expect(store.state.saveGenerationDraft).toHaveBeenCalledWith("video-1", expected);
        expect(store.state.createVideoCandidate).toHaveBeenCalledWith("video-1", expected);
        const saveOrder = (store.state.saveGenerationDraft as { mock: { invocationCallOrder: number[] } }).mock.invocationCallOrder[0];
        const createOrder = (store.state.createVideoCandidate as { mock: { invocationCallOrder: number[] } }).mock.invocationCallOrder[0];
        expect(saveOrder).toBeLessThan(createOrder);
    });


    it("canonicalizes new Wan 3 R2V references by role without using project node order", async () => {
        const videoRef = imageNode({ id: "video-ref", type: "video", title: "Video ref", media_urls: ["uploads/ref.mp4"] });
        const audioRef = imageNode({ id: "audio-ref", type: "audio", title: "Audio ref", media_urls: ["uploads/ref.wav"] });
        const stillRef = imageNode({ id: "image-ref", title: "Image ref", media_urls: ["uploads/ref.png"] });
        const video = videoNode({ data: { reference_node_ids: ["video-ref", "audio-ref", "image-ref"] } });
        const p = project([videoRef, audioRef, stillRef, video]);
        mount(p, video, owner, false, wan3Models());

        selectChip("Model", "Wan 3.0 Video");
        selectChip("Mode", "R2V");
        fireEvent.click(screen.getByRole("button", { name: "Generate" }));

        await waitFor(() => expect(store.state.createVideoCandidate).toHaveBeenCalledTimes(1));
        expect(store.state.createVideoCandidate).toHaveBeenCalledWith("video-1", expect.objectContaining({
            mode: "r2v",
            media: [
                { role: "reference_image", ordinal: 0, node_id: "image-ref", media_url: "uploads/ref.png" },
                { role: "reference_video", ordinal: 0, node_id: "video-ref", media_url: "uploads/ref.mp4" },
                { role: "reference_audio", ordinal: 0, node_id: "audio-ref", media_url: "uploads/ref.wav" },
            ],
        }));
    });

    it("preserves saved Wan 3 R2V media order and blocks missing saved references", async () => {
        const first = imageNode({ id: "image-1", title: "First saved", media_urls: ["uploads/first.png"] });
        const second = imageNode({ id: "image-2", title: "Second saved", media_urls: ["uploads/second.png"] });
        const draft = {
            schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video#video", mode: "r2v",
            params: { duration: 5, resolution: "720P", ratio: "adaptive", prompt_extend: false, watermark: false, audio: true }, batch_size: 1,
            media: [
                { role: "reference_image", ordinal: 0, node_id: "image-2", media_url: "uploads/second.png" },
                { role: "reference_image", ordinal: 1, node_id: "image-1", media_url: "uploads/first.png" },
            ],
        };
        const video = videoNode({ data: { reference_node_ids: ["image-1", "image-2"], generation_draft: draft } });
        const p = project([first, second, video]);
        const view = mount(p, video, owner, false, wan3Models());
        fireEvent.click(screen.getByRole("button", { name: "Generate" }));
        await waitFor(() => expect(store.state.createVideoCandidate).toHaveBeenCalledWith("video-1", expect.objectContaining({ media: draft.media })));

        const missing = videoNode({ data: { reference_node_ids: ["image-1", "image-2"], generation_draft: draft } });
        const changed = project([first, missing]);
        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={changed} selectedNode={missing} ownerKey={`${owner}:missing-r2v`} disabled={false} models={wan3Models()} />
        </NextIntlClientProvider>);
        expect(screen.getByText(/image-2/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    });

    it("blocks a saved R2V role whose source node has the wrong media kind", () => {
        const draft = {
            schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video#video", mode: "r2v",
            params: {}, batch_size: 1,
            media: [{ role: "reference_audio", ordinal: 0, node_id: "image-1", media_url: "uploads/hero.png" }],
        };
        const video = videoNode({ data: { reference_node_ids: ["image-1"], generation_draft: draft } });
        mount(project([imageNode(), video]), video, owner, false, wan3Models());
        expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
        expect(screen.getByText(/Reference audio.*Hero still/)).toBeInTheDocument();
        fireEvent.keyDown(screen.getByLabelText("Generation prompt"), { key: "Enter", ctrlKey: true });
        expect(store.state.saveGenerationDraft).not.toHaveBeenCalled();
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("preserves a missing Wan 3 last frame and blocks create instead of downgrading to first-frame I2V", async () => {
        const draft = {
            schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video#video", mode: "i2v",
            params: { duration: 5, resolution: "720P", ratio: "adaptive", prompt_extend: false, watermark: false, audio: true }, batch_size: 1,
            media: [
                { role: "first_frame", ordinal: 0, node_id: "image-1", media_url: "uploads/hero.png" },
                { role: "last_frame", ordinal: 0, node_id: "image-2", media_url: "uploads/last.png" },
            ],
        };
        const video = videoNode({ data: { reference_node_ids: ["image-1", "image-2"], generation_draft: draft } });
        mount(project([imageNode(), video]), video, owner, false, wan3Models());
        expect(screen.getByText("Selected first frame is no longer available. Choose a valid first frame before creating.")).toBeInTheDocument();
        expect(screen.getByLabelText("Last frame")).toHaveValue("image-2:uploads/last.png");
        expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
        fireEvent.click(screen.getByRole("button", { name: "Generate" }));
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("restores Wan 3 defaults without forcing the draft back to Wan 2", async () => {
        const draft = {
            schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video#video", mode: "t2v",
            params: { duration: -1, resolution: "480P", ratio: "16:9", seed: 0, prompt_extend: true, watermark: true, audio: false }, batch_size: 6,
            media: [],
        };
        const video = videoNode({ data: { generation_draft: draft } });
        mount(project([imageNode(), video]), video, owner, false, wan3Models());
        fireEvent.click(screen.getByRole("button", { name: "Restore generation defaults" }));
        await waitFor(() => expect(store.state.saveGenerationDraft).toHaveBeenCalledWith("video-1", {
            contract_version: "ordered-media-v1",
            model_id: "wan/wan3.0-video#video",
            mode: "t2v",
            prompt: "Original prompt",
            params: {},
            media: [],
            batch_size: 6,
        }));
    });

    it("guards keyboard generation when create is disabled or IME composition is active", async () => {
        store.state.needsRefresh = true;
        mount();
        fireEvent.keyDown(screen.getByLabelText("Generation prompt"), { key: "Enter", metaKey: true });
        expect(store.state.saveGenerationDraft).not.toHaveBeenCalled();
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();

        store.state.needsRefresh = false;
        const view = mount();
        const input = screen.getAllByLabelText("Generation prompt").at(-1) as HTMLElement;
        fireEvent.compositionStart(input);
        fireEvent.keyDown(input, { key: "Enter", metaKey: true });
        fireEvent.compositionEnd(input);
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
        view.unmount();
    });

    it("rechecks current store ownership after saving before creating", async () => {
        store.state.saveGenerationDraft = vi.fn(async () => { store.state.needsRefresh = true; return {}; });
        mount();
        fireEvent.click(screen.getByRole("button", { name: "Create Wan 2.7 take" }));
        await waitFor(() => expect(store.state.saveGenerationDraft).toHaveBeenCalledTimes(1));
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("preserves typed missing Wan 3 first and last frame media when saving draft", async () => {
        const draft = {
            schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video#video", mode: "i2v",
            params: { duration: 5, resolution: "720P", ratio: "adaptive", prompt_extend: false, watermark: false, audio: true }, batch_size: 1,
            media: [
                { role: "first_frame", ordinal: 0, node_id: "image-missing", media_url: "uploads/missing.png" },
                { role: "last_frame", ordinal: 0, node_id: "image-2", media_url: "uploads/last.png" },
            ],
        };
        const last = imageNode({ id: "image-2", title: "Last still", media_urls: ["uploads/last.png"] });
        const video = videoNode({ data: { reference_node_ids: ["image-missing", "image-2"], generation_draft: draft } });
        mount(project([last, video]), video, owner, false, wan3Models());
        await screen.findByText("Selected first frame is no longer available. Choose a valid first frame before creating.");
        fireEvent.click(screen.getByRole("button", { name: "Save generation settings" }));
        await waitFor(() => expect(store.state.saveGenerationDraft).toHaveBeenCalledWith("video-1", expect.objectContaining({ media: draft.media })));
    });

    it("keeps an explicitly empty Wan 3 R2V draft empty instead of falling back to all references", async () => {
        const videoRef = imageNode({ id: "video-ref", type: "video", title: "Video ref", media_urls: ["uploads/ref.mp4"] });
        const stillRef = imageNode({ id: "image-ref", title: "Image ref", media_urls: ["uploads/ref.png"] });
        const draft = {
            schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video#video", mode: "r2v",
            params: { duration: 5, resolution: "720P", ratio: "adaptive", prompt_extend: false, watermark: false, audio: true }, batch_size: 1, media: [],
        };
        const video = videoNode({ data: { reference_node_ids: ["video-ref", "image-ref"], generation_draft: draft } });
        mount(project([videoRef, stillRef, video]), video, owner, false, wan3Models());
        expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
        fireEvent.click(screen.getByRole("button", { name: "Save generation settings" }));
        await waitFor(() => expect(store.state.saveGenerationDraft).toHaveBeenCalledWith("video-1", expect.objectContaining({ media: [] })));
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });


    it("uses production catalog composer defaults with active Wan 3 options and legacy Wan2 duration bounds", () => {
        mount();
        expect(screen.getByRole("button", { name: "Duration" })).toHaveTextContent("5s");
        fireEvent.click(screen.getByRole("button", { name: "Duration" }));
        const slider = within(screen.getByRole("dialog", { name: "Duration" })).getByRole("slider", { name: "Duration" });
        expect(slider).toHaveAttribute("min", "2");
        expect(slider).toHaveAttribute("max", "15");
        expect(slider).toHaveAttribute("step", "1");
        fireEvent.click(screen.getByRole("button", { name: "Duration" }));

        fireEvent.click(screen.getByRole("button", { name: "Model" }));
        expect(screen.getByRole("option", { name: /^Wan 3.0 Video$/ })).not.toBeDisabled();
        expect(screen.getByRole("option", { name: /^Wan 3.0 Video Prime$/ })).not.toBeDisabled();
    });

    it("does not create from a saved draft whose model is unavailable in the composer", () => {
        const draft = {
            schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video#video", mode: "t2v",
            params: {}, batch_size: 1, media: [],
        };
        const video = videoNode({ data: { generation_draft: draft } });
        const models = wan3Models().map(model => ({ ...model, disabled: model.id !== "wan/wan2.7-video#i2v" }));
        mount(project([video]), video, owner, false, models);
        expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
        fireEvent.keyDown(screen.getByLabelText("Generation prompt"), { key: "Enter", ctrlKey: true });
        expect(store.state.saveGenerationDraft).not.toHaveBeenCalled();
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("does not continue creating while project navigation begins during draft save", async () => {
        let finishSave!: () => void;
        store.state.saveGenerationDraft = vi.fn().mockImplementation(() => new Promise<void>(resolve => { finishSave = resolve; }));
        mount();
        fireEvent.click(screen.getByRole("button", { name: "Create Wan 2.7 take" }));
        expect(store.state.saveGenerationDraft).toHaveBeenCalledTimes(1);
        store.state.isLoading = true;
        await act(async () => finishSave());
        expect(store.state.createVideoCandidate).not.toHaveBeenCalled();
    });

    it("uses catalog boolean defaults for Wan 3 draft initialization and restore UI without stealing explicit false", async () => {
        const draft = {
            schema_version: 2, input_contract_version: "ordered-media-v1", model_id: "wan/wan3.0-video#video", mode: "t2v",
            params: { duration: 5, resolution: "1080P", ratio: "adaptive", audio: true }, batch_size: 1, media: [],
        };
        const video = videoNode({ data: { generation_draft: draft } });
        mount(project([imageNode(), video]), video, owner, false, wan3Models());
        fireEvent.click(screen.getByRole("button", { name: "Advanced parameters" }));
        expect(screen.getByLabelText("Prompt extend")).toHaveAttribute("aria-pressed", "true");
        expect(screen.getByLabelText("Watermark")).toHaveAttribute("aria-pressed", "false");
        fireEvent.click(screen.getByLabelText("Prompt extend"));
        fireEvent.click(screen.getByRole("button", { name: "Save generation settings" }));
        await waitFor(() => expect(store.state.saveGenerationDraft).toHaveBeenCalledWith("video-1", expect.objectContaining({
            params: expect.objectContaining({ prompt_extend: false, watermark: false, audio: true }),
        })));

        fireEvent.click(screen.getByRole("button", { name: "Restore generation defaults" }));
        await waitFor(() => expect(store.state.saveGenerationDraft).toHaveBeenCalledWith("video-1", {
            contract_version: "ordered-media-v1",
            model_id: "wan/wan3.0-video#video",
            mode: "t2v",
            prompt: "Original prompt",
            params: {},
            media: [],
            batch_size: 1,
        }));
        expect(screen.getByLabelText("Prompt extend")).toHaveAttribute("aria-pressed", "true");
    });

    it("portals a node-docked composer to body and constrains placement to the canvas available rect", () => {
        const placement = pinnedComposerPlacement(
            { left: 680, top: 200, width: 240, height: 150 },
            { width: 1200, height: 800 },
            { height: 150, availableRect: { left: 0, top: 0, width: 800, height: 600 }, obstacles: [{ left: 0, top: 560, width: 800, height: 40 }] },
        );
        expect(placement.left + placement.width).toBeLessThanOrEqual(800);
        expect(placement.top + Math.min(150, placement.maxHeight)).toBeLessThanOrEqual(548);

        const p = project();
        mount(p, p.nodes[1], owner, false, wan3Models(), { left: 10, top: 20, width: 200, height: 120 });
        const composer = screen.getByRole("search", { name: "Shot parameters" });
        expect(composer.parentElement).toBe(document.body);
    });

    it("remounts isolated local state when the selected node id changes", async () => {
        const p = project([imageNode(), videoNode(), videoNode({ id: "video-2", title: "Second take", prompt: "Second prompt", data: { reference_node_ids: ["image-1"] } })]);
        const view = mount(p, p.nodes[1], `${owner}:video-1`);
        fireEvent.change(screen.getByLabelText("Generation prompt"), { target: { value: "Node one draft" } });

        view.rerender(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
            <GenerationPanel project={p} selectedNode={p.nodes[2]} ownerKey={`${owner}:video-2`} disabled={false} />
        </NextIntlClientProvider>);

        expect(screen.getByLabelText("Generation prompt")).toHaveValue("Second prompt");
    });
});
