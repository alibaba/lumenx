// @vitest-environment happy-dom
import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import messages from "../../messages/en.json";
import { MediaComposer } from "@/components/atelier/v3/MediaComposer";
import { AtelierProjectApiError } from "@/lib/atelierApi";
import { atelierMediaApi } from "@/lib/atelierMediaApi";
import {
  activateRuntimeEnvironment,
  clearRuntimeEnvironment,
} from "@/lib/runtimeEnvironment";
import { useAtelierStore } from "@/store/atelierStore";
import type { AtelierGenerationAttempt, AtelierNode, AtelierProject } from "@/lib/atelierTypes";

vi.mock("@/lib/atelierMediaApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/atelierMediaApi")>();
  return {
    ...actual,
    atelierMediaApi: {
      ...actual.atelierMediaApi,
      listVoices: vi.fn(),
    },
  };
});

const owner = "env-a:1:project-1:0";
const HASH = "a".repeat(64);

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

function mediaAttempt(
  kind: "image" | "audio",
  overrides: Partial<AtelierGenerationAttempt> = {},
): AtelierGenerationAttempt {
  return {
    schema_version: 1,
    execution_contract: "atelier-provider-media-v2",
    project_id: "project-1",
    node_id: kind,
    candidate_id: "candidate-1",
    attempt_id: "attempt-1",
    attempt_revision: 0,
    status: "pending",
    error_code: null,
    output_ref: null,
    media_kind: kind,
    provider_execution: {
      schema_version: 2,
      execution_kind: "sync_artifact",
      media_kind: kind,
      output_ref: "provider:image:attempt-1",
      attempt_id: "attempt-1",
      phase: "observed",
      provider_model_id: kind === "image" ? "wan2.7-image" : "cosyvoice-v2",
      intent: {},
      media: [],
      scope: {},
      credential_ref: "credential",
      fingerprint: HASH,
      handle: null,
      observation: null,
      prepared_artifact: null,
      artifact: null,
    },
    created_at: 1,
    updated_at: 1,
    ...overrides,
  } as AtelierGenerationAttempt;
}

function mount(
  p: AtelierProject,
  selected = p.nodes[0],
  props: Partial<React.ComponentProps<typeof MediaComposer>> = {},
) {
  const base = {
    saveDraft: vi.fn().mockResolvedValue({}),
    createImageCandidate: vi.fn().mockResolvedValue({}),
    createAudioCandidate: vi.fn().mockResolvedValue({}),
    readbackPendingGeneration: vi.fn().mockResolvedValue({}),
    refreshGenerationAttempt: vi.fn().mockResolvedValue({}),
    continueGenerationAttempt: vi.fn().mockResolvedValue({}),
    cancelGenerationAttempt: vi.fn().mockResolvedValue({}),
    selectCandidate: vi.fn().mockResolvedValue({}),
    setCandidateVisibility: vi.fn().mockResolvedValue({}),
  };
  const view = render(
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <MediaComposer
        project={p}
        selectedNode={selected}
        ownerKey={`${owner}:${selected.id}`}
        disabled={false}
        pendingGenerations={[]}
        {...base}
        {...props}
      />
    </NextIntlClientProvider>,
  );
  return { ...view, calls: { ...base, ...props } };
}

beforeEach(() => {
  vi.restoreAllMocks();
  clearRuntimeEnvironment();
  useAtelierStore.setState({
    branchFromCandidate: vi.fn().mockResolvedValue({}),
    currentProject: null,
    selectedNodeId: null,
    pendingGenerations: [],
    isLoading: false,
    isSaving: false,
    needsRefresh: false,
  });
  vi.mocked(atelierMediaApi.listVoices).mockResolvedValue([
    {
      id: "longxiaochun_v2",
      name: "龙小淳 (知性女)",
      family: "cosyvoice",
      supported_parameters: ["pitch_rate", "speech_rate", "volume"],
    },
  ]);
});

describe("Atelier media composer", () => {

  it.each(["image", "audio"] as const)("creates a %s branch draft without selecting, generating, or hiding", async (kind) => {
    const mediaNode = node({
      id: kind,
      type: kind,
      title: kind === "image" ? "Image" : "Audio",
      data: {
        candidates: [{
          schema_version: 2,
          execution_contract: "atelier-provider-media-v2",
          id: "candidate-1",
          attempt_id: "attempt-1",
          media_kind: kind,
          status: "completed",
          media_url: kind === "image" ? "assets/atelier/candidate-1.png" : "audio/atelier/candidate-1.mp3",
          error_code: null,
          authority: "provider",
          execution_fingerprint: "a".repeat(64),
        }],
      },
    });
    const branchFromCandidate = vi.fn().mockResolvedValue({});
    useAtelierStore.setState({ branchFromCandidate } as never);
    const { calls } = mount(project([mediaNode]), mediaNode);

    fireEvent.click(screen.getByRole("button", { name: "Create branch draft" }));

    await waitFor(() => expect(branchFromCandidate).toHaveBeenCalledWith(kind, "candidate-1"));
    expect(calls.selectCandidate).not.toHaveBeenCalled();
    expect(calls.setCandidateVisibility).not.toHaveBeenCalled();
    expect(calls.createImageCandidate).not.toHaveBeenCalled();
    expect(calls.createAudioCandidate).not.toHaveBeenCalled();
  });

  it.each(["image", "audio"] as const)("clears a %s continue conflict only when the same attempt revision advances", async (kind) => {
    const mediaNode = node({ id: kind, type: kind, title: kind });
    const baseProject = project([mediaNode], {
      generation_attempts: [mediaAttempt(kind, { attempt_revision: 0 })],
    });
    const continueGenerationAttempt = vi
      .fn()
      .mockRejectedValue(new AtelierProjectApiError("Remote changed", "storage_conflict", 409));
    const mounted = mount(baseProject, mediaNode, { continueGenerationAttempt });

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Remote changed");
    const unrelated = project([mediaNode], {
      generation_attempts: [
        mediaAttempt(kind, {
          attempt_id: "attempt-other",
          candidate_id: "candidate-other",
          attempt_revision: 2,
          provider_execution: {
            ...mediaAttempt(kind).provider_execution,
            attempt_id: "attempt-other",
          },
        }),
      ],
    });
    mounted.rerender(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MediaComposer
          project={unrelated}
          selectedNode={mediaNode}
          ownerKey={`${owner}:${kind}`}
          disabled={false}
          pendingGenerations={[]}
          saveDraft={mounted.calls.saveDraft}
          createImageCandidate={mounted.calls.createImageCandidate}
          createAudioCandidate={mounted.calls.createAudioCandidate}
          readbackPendingGeneration={mounted.calls.readbackPendingGeneration}
          refreshGenerationAttempt={mounted.calls.refreshGenerationAttempt}
          continueGenerationAttempt={continueGenerationAttempt}
          cancelGenerationAttempt={mounted.calls.cancelGenerationAttempt}
          selectCandidate={mounted.calls.selectCandidate}
          setCandidateVisibility={mounted.calls.setCandidateVisibility}
        />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Remote changed");

    const advanced = project([mediaNode], {
      generation_attempts: [
        mediaAttempt(kind, { attempt_revision: 1, status: "completed" }),
      ],
    });
    mounted.rerender(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MediaComposer
          project={advanced}
          selectedNode={mediaNode}
          ownerKey={`${owner}:${kind}`}
          disabled={false}
          pendingGenerations={[]}
          saveDraft={mounted.calls.saveDraft}
          createImageCandidate={mounted.calls.createImageCandidate}
          createAudioCandidate={mounted.calls.createAudioCandidate}
          readbackPendingGeneration={mounted.calls.readbackPendingGeneration}
          refreshGenerationAttempt={mounted.calls.refreshGenerationAttempt}
          continueGenerationAttempt={continueGenerationAttempt}
          cancelGenerationAttempt={mounted.calls.cancelGenerationAttempt}
          selectCandidate={mounted.calls.selectCandidate}
          setCandidateVisibility={mounted.calls.setCandidateVisibility}
        />
      </NextIntlClientProvider>,
    );
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it.each(["image", "audio"] as const)("clears a %s branch lost-ACK error only after authoritative refresh", async (kind) => {
    const mediaNode = node({
      id: kind,
      type: kind,
      title: kind,
      data: {
        candidates: [{
          schema_version: 2,
          execution_contract: "atelier-provider-media-v2",
          id: "candidate-1",
          attempt_id: "attempt-1",
          media_kind: kind,
          status: "completed",
          media_url: kind === "image" ? "assets/atelier/candidate-1.png" : "audio/atelier/candidate-1.mp3",
          error_code: null,
          authority: "provider",
          execution_fingerprint: HASH,
        }],
      },
    });
    const branchFromCandidate = vi.fn().mockImplementation(async () => {
      useAtelierStore.setState({ needsRefresh: true } as never);
      throw new Error("Failed to fetch");
    });
    useAtelierStore.setState({ branchFromCandidate } as never);
    const baseProject = project([mediaNode]);
    const mounted = mount(baseProject, mediaNode);

    fireEvent.click(screen.getByRole("button", { name: "Create branch draft" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
    mounted.rerender(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MediaComposer
          project={baseProject}
          selectedNode={mediaNode}
          ownerKey={`${owner}:${kind}`}
          disabled={false}
          pendingGenerations={[]}
          saveDraft={mounted.calls.saveDraft}
          createImageCandidate={mounted.calls.createImageCandidate}
          createAudioCandidate={mounted.calls.createAudioCandidate}
          readbackPendingGeneration={mounted.calls.readbackPendingGeneration}
          refreshGenerationAttempt={mounted.calls.refreshGenerationAttempt}
          continueGenerationAttempt={mounted.calls.continueGenerationAttempt}
          cancelGenerationAttempt={mounted.calls.cancelGenerationAttempt}
          selectCandidate={mounted.calls.selectCandidate}
          setCandidateVisibility={mounted.calls.setCandidateVisibility}
        />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Failed to fetch");

    useAtelierStore.setState({ needsRefresh: false } as never);
    const refreshed = project([
      mediaNode,
      node({
        id: `${kind}-branch`,
        type: kind,
        title: `${kind} branch`,
        data: {
          branch_source: {
            schema_version: 1,
            origin_project_id: "project-1",
            origin_node_id: kind,
            origin_candidate_id: "candidate-1",
            media_kind: kind,
            media_url: kind === "image" ? "uploads/branch.png" : "uploads/branch.mp3",
            media_sha256: HASH,
            media_size_bytes: 123,
            recipe_status: "available",
          },
        },
      }),
    ], { revision: baseProject.revision + 1 });
    mounted.rerender(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MediaComposer
          project={refreshed}
          selectedNode={mediaNode}
          ownerKey={`${owner}:${kind}`}
          disabled={false}
          pendingGenerations={[]}
          saveDraft={mounted.calls.saveDraft}
          createImageCandidate={mounted.calls.createImageCandidate}
          createAudioCandidate={mounted.calls.createAudioCandidate}
          readbackPendingGeneration={mounted.calls.readbackPendingGeneration}
          refreshGenerationAttempt={mounted.calls.refreshGenerationAttempt}
          continueGenerationAttempt={mounted.calls.continueGenerationAttempt}
          cancelGenerationAttempt={mounted.calls.cancelGenerationAttempt}
          selectCandidate={mounted.calls.selectCandidate}
          setCandidateVisibility={mounted.calls.setCandidateVisibility}
        />
      </NextIntlClientProvider>,
    );
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(branchFromCandidate).toHaveBeenCalledTimes(1);
    expect(mounted.calls.createImageCandidate).not.toHaveBeenCalled();
    expect(mounted.calls.createAudioCandidate).not.toHaveBeenCalled();
  });

  it.each(["image", "audio"] as const)("keeps a %s API branch error even when the store requires refresh", async (kind) => {
    const mediaNode = node({
      id: kind,
      type: kind,
      title: kind,
      data: {
        candidates: [{
          schema_version: 2,
          execution_contract: "atelier-provider-media-v2",
          id: "candidate-1",
          attempt_id: "attempt-1",
          media_kind: kind,
          status: "completed",
          media_url: kind === "image" ? "assets/atelier/candidate-1.png" : "audio/atelier/candidate-1.mp3",
          error_code: null,
          authority: "provider",
          execution_fingerprint: HASH,
        }],
      },
    });
    const branchFromCandidate = vi.fn().mockImplementation(async () => {
      useAtelierStore.setState({ needsRefresh: true } as never);
      throw new AtelierProjectApiError("permission denied", "atelier_project_request_failed", 403);
    });
    useAtelierStore.setState({ branchFromCandidate } as never);
    const baseProject = project([mediaNode]);
    const mounted = mount(baseProject, mediaNode);

    fireEvent.click(screen.getByRole("button", { name: "Create branch draft" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("permission denied");
    useAtelierStore.setState({ needsRefresh: false } as never);
    const changed = project([mediaNode], { revision: baseProject.revision + 1 });
    mounted.rerender(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MediaComposer
          project={changed}
          selectedNode={mediaNode}
          ownerKey={`${owner}:${kind}`}
          disabled={false}
          pendingGenerations={[]}
          saveDraft={mounted.calls.saveDraft}
          createImageCandidate={mounted.calls.createImageCandidate}
          createAudioCandidate={mounted.calls.createAudioCandidate}
          readbackPendingGeneration={mounted.calls.readbackPendingGeneration}
          refreshGenerationAttempt={mounted.calls.refreshGenerationAttempt}
          continueGenerationAttempt={mounted.calls.continueGenerationAttempt}
          cancelGenerationAttempt={mounted.calls.cancelGenerationAttempt}
          selectCandidate={mounted.calls.selectCandidate}
          setCandidateVisibility={mounted.calls.setCandidateVisibility}
        />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("permission denied");
  });

  it("keeps a late action error from a previous owner out of the new media composer", async () => {
    const image = node({ id: "image" });
    const audio = node({ id: "audio", type: "audio", title: "Audio" });
    let reject!: (cause: Error) => void;
    const saveDraft = vi.fn(() => new Promise((_, rejectPromise) => { reject = rejectPromise; }));
    const mounted = mount(project([image, audio]), image, { saveDraft });

    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    mounted.rerender(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MediaComposer
          project={project([image, audio])}
          selectedNode={audio}
          ownerKey={`${owner}:audio`}
          disabled={false}
          pendingGenerations={[]}
          saveDraft={saveDraft}
          createImageCandidate={mounted.calls.createImageCandidate}
          createAudioCandidate={mounted.calls.createAudioCandidate}
          readbackPendingGeneration={mounted.calls.readbackPendingGeneration}
          refreshGenerationAttempt={mounted.calls.refreshGenerationAttempt}
          continueGenerationAttempt={mounted.calls.continueGenerationAttempt}
          cancelGenerationAttempt={mounted.calls.cancelGenerationAttempt}
          selectCandidate={mounted.calls.selectCandidate}
          setCandidateVisibility={mounted.calls.setCandidateVisibility}
        />
      </NextIntlClientProvider>,
    );
    reject(new Error("late save failed"));
    await waitFor(() => expect(saveDraft).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows source-only branch provenance without offering an exact retry", () => {
    const image = node({
      id: "image",
      data: {
        branch_source: { schema_version: 1, origin_project_id: "project-1", origin_node_id: "old-image", origin_candidate_id: "old-candidate", media_kind: "image", media_url: "uploads/imported/pkg1/image.png", media_sha256: "a".repeat(64), media_size_bytes: 123, recipe_status: "unavailable_legacy" },
      },
    });

    mount(project([image]), image);

    const card = screen.getByTestId("branch-source-card");
    expect(card).toHaveTextContent("Only the source media was preserved");
    expect(screen.getByAltText("Branch source media")).toHaveAttribute("src", expect.stringContaining("/files/uploads/imported/pkg1/image.png"));
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/exact retry/i)).not.toBeInTheDocument();
  });

  it("shows audio branch source as readonly playback without source navigation", () => {
    const audio = node({
      id: "audio",
      type: "audio",
      title: "Audio",
      data: {
        branch_source: { schema_version: 1, origin_project_id: "project-1", origin_node_id: "old-audio", origin_candidate_id: "old-candidate", media_kind: "audio", media_url: "uploads/audio-source.mp3", media_sha256: "a".repeat(64), media_size_bytes: 123, recipe_status: "available" },
      },
    });

    mount(project([audio]), audio);

    const card = screen.getByTestId("branch-source-card");
    expect(card).toHaveTextContent("Historical settings were recovered when this branch was created");
    expect(screen.getByLabelText("Branch source media")).toHaveAttribute("src", expect.stringContaining("/files/uploads/audio-source.mp3"));
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
  });

  it("records image candidate judgment without selecting or generating and preserves draft on save failure", async () => {
    const image = node({
      id: "image",
      data: {
        candidates: [{
          schema_version: 2,
          execution_contract: "atelier-provider-media-v2",
          id: "candidate-1",
          attempt_id: "attempt-1",
          media_kind: "image",
          status: "completed",
          media_url: "assets/atelier/candidate-1.png",
          error_code: null,
          authority: "provider",
          execution_fingerprint: "a".repeat(64),
        }],
      },
    });
    const setCandidateJudgment = vi.fn().mockRejectedValue(new Error("save failed"));
    useAtelierStore.setState({ setCandidateJudgment } as never);
    const { calls } = mount(project([image]), image);

    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    fireEvent.click(screen.getByLabelText("Reject"));
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Weak silhouette" } });
    fireEvent.click(screen.getByRole("button", { name: "Save review" }));

    await waitFor(() => expect(setCandidateJudgment).toHaveBeenCalledWith("image", "candidate-1", {
      verdict: "reject",
      reason: "Weak silhouette",
    }));
    expect(await screen.findByRole("alert")).toHaveTextContent("save failed");
    expect(screen.getByLabelText("Reason")).toHaveValue("Weak silhouette");
    expect(calls.selectCandidate).not.toHaveBeenCalled();
    expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });

  it("opens hidden candidate review as read-only history", async () => {
    const image = node({
      id: "image",
      data: {
        deleted_candidates: [{
          schema_version: 2,
          execution_contract: "atelier-provider-media-v2",
          id: "candidate-1",
          attempt_id: "attempt-1",
          media_kind: "image",
          status: "completed",
          media_url: "assets/atelier/candidate-1.png",
          error_code: null,
          authority: "provider",
          execution_fingerprint: "a".repeat(64),
        }],
      },
    });
    const setCandidateJudgment = vi.fn();
    useAtelierStore.setState({ setCandidateJudgment } as never);

    mount(project([image], { candidate_judgments: [{
      schema_version: 1,
      id: "judgment-1",
      project_id: "project-1",
      node_id: "image",
      candidate_id: "candidate-1",
      media_kind: "image",
      verdict: "keep",
      reason: "Good shape",
      created_at: 10,
    }] }), image);

    fireEvent.click(screen.getByRole("button", { name: "Review" }));

    expect(screen.getByRole("dialog", { name: "Candidate review" })).toBeInTheDocument();
    expect(screen.getByText(/Hidden candidates keep their review history/)).toBeInTheDocument();
    expect(screen.getByLabelText("Reason")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save review" })).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Candidate review" })).not.toBeInTheDocument());
    expect(setCandidateJudgment).not.toHaveBeenCalled();
  });

  it("portals pinned fixed layout to document body so viewport coordinates are not scoped by the inspector", () => {
    const host = document.createElement("aside");
    host.style.backdropFilter = "blur(12px)";
    document.body.appendChild(host);
    const image = node({ id: "image" });

    const { container } = render(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <div data-testid="inspector-host">
          <MediaComposer
            project={project([image])}
            selectedNode={image}
            ownerKey={`${owner}:image`}
            disabled={false}
            pendingGenerations={[]}
            targetRect={{ left: 560, top: 120, width: 240, height: 160 }}
            targetAvailableRect={{ left: 0, top: 0, width: 1280, height: 720 }}
            saveDraft={vi.fn().mockResolvedValue({})}
            createImageCandidate={vi.fn().mockResolvedValue({})}
            createAudioCandidate={vi.fn().mockResolvedValue({})}
            readbackPendingGeneration={vi.fn().mockResolvedValue({})}
            refreshGenerationAttempt={vi.fn().mockResolvedValue({})}
            continueGenerationAttempt={vi.fn().mockResolvedValue({})}
            cancelGenerationAttempt={vi.fn().mockResolvedValue({})}
          />
        </div>
      </NextIntlClientProvider>,
      { container: host },
    );

    const composer = screen.getByRole("region", { name: "Image" });
    expect(container).not.toContainElement(composer);
    expect(composer.parentElement).toBe(document.body);
    expect(composer).toHaveAttribute("data-composer-dock", "node");
    expect(composer).toHaveClass("fixed");
    expect(composer.style.left).toMatch(/px$/);
    expect(Number.parseFloat(composer.style.left)).toBeGreaterThanOrEqual(8);
  });

  it("saves image draft before generating an ordered i2i request", async () => {
    const image = node({
      id: "image",
      data: {
        reference_node_ids: ["ref"],
        reference_image_urls: ["uploads/ref.png"],
      },
    });
    const ref = node({
      id: "ref",
      title: "Reference",
      media_urls: ["uploads/ref.png"],
    });
    const { calls } = mount(project([image, ref]), image);

    fireEvent.change(screen.getByLabelText("Prompt"), {
      target: { value: "paint it again" },
    });
    fireEvent.change(screen.getByLabelText("Candidates"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByText("Advanced"));
    fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "0" } });
    fireEvent.click(screen.getByLabelText("Prompt extend"));
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    await waitFor(() => expect(calls.saveDraft).toHaveBeenCalledTimes(1));
    expect(calls.saveDraft).toHaveBeenCalledWith(
      "image",
      expect.objectContaining({
        media_kind: "image",
        prompt: "paint it again",
        mode: "i2i",
        batch_size: 2,
        media: [
          {
            role: "reference_image",
            ordinal: 0,
            node_id: "ref",
            media_url: "uploads/ref.png",
          },
        ],
        model_id: "wan/wan2.7-image-pro#image",
        params: expect.objectContaining({ seed: 0, prompt_extend: false }),
      }),
    );
    expect(calls.createImageCandidate).toHaveBeenCalledWith(
      "image",
      expect.objectContaining({
        entrypoint: "atelier.provider_image.v1",
        mode: "i2i",
        model_id: "wan/wan2.7-image-pro#image",
        prompt: "paint it again",
      }),
    );
    const saveDraftMock = calls.saveDraft as ReturnType<typeof vi.fn>;
    const createImageMock = calls.createImageCandidate as ReturnType<
      typeof vi.fn
    >;
    expect(saveDraftMock.mock.invocationCallOrder[0]).toBeLessThan(
      createImageMock.mock.invocationCallOrder[0],
    );
  });

  it("maps shared reference URLs by media ownership instead of array index", async () => {
    const image = node({
      id: "image",
      data: {
        reference_node_ids: ["ref-a", "ref-b"],
        reference_image_urls: ["uploads/shared.png"],
      },
    });
    const refA = node({
      id: "ref-a",
      title: "A",
      media_urls: ["uploads/shared.png"],
    });
    const refB = node({
      id: "ref-b",
      title: "B",
      media_urls: ["uploads/shared.png"],
    });
    const { calls } = mount(project([image, refA, refB]), image);

    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    await waitFor(() =>
      expect(calls.createImageCandidate).toHaveBeenCalledTimes(1),
    );
    expect(calls.createImageCandidate).toHaveBeenCalledWith(
      "image",
      expect.objectContaining({
        mode: "i2i",
        media: [
          {
            role: "reference_image",
            ordinal: 0,
            node_id: "ref-a",
            media_url: "uploads/shared.png",
          },
          {
            role: "reference_image",
            ordinal: 1,
            node_id: "ref-b",
            media_url: "uploads/shared.png",
          },
        ],
      }),
    );
  });

  it("reconciles newly attached references after the composer is already mounted", async () => {
    const image = node({ id: "image" });
    const ref = node({
      id: "ref",
      title: "Reference",
      media_urls: ["uploads/ref.png"],
    });
    const p0 = project([image, ref]);
    const mounted = mount(p0, image);
    const nextImage = node({
      id: "image",
      data: {
        reference_node_ids: ["ref"],
        reference_image_urls: ["uploads/ref.png"],
      },
    });
    const p1 = project([nextImage, ref], { revision: 4 });

    mounted.rerender(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MediaComposer
          project={p1}
          selectedNode={nextImage}
          ownerKey={`${owner}:image`}
          disabled={false}
          pendingGenerations={[]}
          saveDraft={mounted.calls.saveDraft}
          createImageCandidate={mounted.calls.createImageCandidate}
          createAudioCandidate={mounted.calls.createAudioCandidate}
          readbackPendingGeneration={mounted.calls.readbackPendingGeneration}
          refreshGenerationAttempt={mounted.calls.refreshGenerationAttempt}
          continueGenerationAttempt={mounted.calls.continueGenerationAttempt}
          cancelGenerationAttempt={mounted.calls.cancelGenerationAttempt}
          selectCandidate={mounted.calls.selectCandidate}
          setCandidateVisibility={mounted.calls.setCandidateVisibility}
        />
      </NextIntlClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByText(/1\. ref/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    await waitFor(() =>
      expect(mounted.calls.createImageCandidate).toHaveBeenCalledTimes(1),
    );
    expect(mounted.calls.createImageCandidate).toHaveBeenCalledWith(
      "image",
      expect.objectContaining({
        mode: "i2i",
        media: [
          {
            role: "reference_image",
            ordinal: 0,
            node_id: "ref",
            media_url: "uploads/ref.png",
          },
        ],
      }),
    );
  });

  it("reconciles a saved T2I draft when references are attached later", async () => {
    const image = node({
      id: "image",
      data: {
        generation_draft: {
          schema_version: 3,
          media_kind: "image",
          model_id: "wan/wan2.7-image-pro#image",
          mode: "t2i",
          params: { size: "1280*1280" },
          media: [],
          batch_size: 1,
        },
      },
    });
    const ref = node({
      id: "ref",
      title: "Reference",
      media_urls: ["uploads/ref.png"],
    });
    const mounted = mount(project([image, ref]), image);
    const nextImage = node({
      id: "image",
      data: {
        generation_draft: image.data.generation_draft,
        reference_node_ids: ["ref"],
        reference_image_urls: ["uploads/ref.png"],
      },
    });
    const p1 = project([nextImage, ref], { revision: 4 });

    mounted.rerender(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MediaComposer
          project={p1}
          selectedNode={nextImage}
          ownerKey={`${owner}:image`}
          disabled={false}
          pendingGenerations={[]}
          saveDraft={mounted.calls.saveDraft}
          createImageCandidate={mounted.calls.createImageCandidate}
          createAudioCandidate={mounted.calls.createAudioCandidate}
          readbackPendingGeneration={mounted.calls.readbackPendingGeneration}
          refreshGenerationAttempt={mounted.calls.refreshGenerationAttempt}
          continueGenerationAttempt={mounted.calls.continueGenerationAttempt}
          cancelGenerationAttempt={mounted.calls.cancelGenerationAttempt}
          selectCandidate={mounted.calls.selectCandidate}
          setCandidateVisibility={mounted.calls.setCandidateVisibility}
        />
      </NextIntlClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByText(/1\. ref/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    await waitFor(() =>
      expect(mounted.calls.createImageCandidate).toHaveBeenCalledTimes(1),
    );
    expect(mounted.calls.createImageCandidate).toHaveBeenCalledWith(
      "image",
      expect.objectContaining({
        mode: "i2i",
        media: [
          {
            role: "reference_image",
            ordinal: 0,
            node_id: "ref",
            media_url: "uploads/ref.png",
          },
        ],
      }),
    );
  });

  it("does not create after save when runtime ownership switches on the same node id", async () => {
    activateRuntimeEnvironment({
      schema_version: 1,
      storage_id: "11111111-1111-4111-8111-111111111111",
    });
    const image = node({ id: "image" });
    const current = project([image]);
    useAtelierStore.setState({
      currentProject: current,
      selectedNodeId: "image",
      pendingGenerations: [],
      isLoading: false,
      isSaving: false,
      needsRefresh: false,
    });
    const saveDraft = vi.fn().mockImplementation(async () => {
      useAtelierStore.setState({
        currentProject: current,
        selectedNodeId: "other-image",
      });
    });
    const { calls } = mount(current, image, { saveDraft });

    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    await waitFor(() => expect(calls.saveDraft).toHaveBeenCalledTimes(1));
    expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });

  it("blocks new generation while an original media request is pending and reads back the original", async () => {
    const image = node({ id: "image" });
    const pending = {
      schema_version: 1 as const,
      apiBase: "http://local",
      storageId: "storage",
      projectId: "project-1",
      nodeId: "image",
      clientRequestId: "request-1",
      request: {
        entrypoint: "atelier.provider_image.v1" as const,
        expected_revision: 3,
        client_request_id: "request-1",
        model_id: "wan/wan2.7-image-pro#image",
        mode: "t2i" as const,
        prompt: "old",
        media: [],
      },
    };
    const { calls } = mount(project([image]), image, {
      pendingGenerations: [pending],
    });

    expect(screen.getByText(/previous generation request/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate/ })).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Read original request" }),
    );

    await waitFor(() =>
      expect(calls.readbackPendingGeneration).toHaveBeenCalledWith("request-1"),
    );
    expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });

  it("blocks generation for a different unresolved project media request", async () => {
    const image = node({ id: "image" });
    const pending = {
      schema_version: 1 as const,
      apiBase: "http://local",
      storageId: "storage",
      projectId: "project-1",
      nodeId: "other-image",
      clientRequestId: "request-other",
      request: {
        entrypoint: "atelier.provider_image.v1" as const,
        expected_revision: 3,
        client_request_id: "request-other",
        model_id: "wan/wan2.7-image-pro#image",
        mode: "t2i" as const,
        prompt: "old",
        media: [],
      },
    };
    const { calls } = mount(project([image]), image, {
      pendingGenerations: [pending],
    });

    expect(screen.getByText(/Another generation request/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate/ })).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Read original request" }),
    );

    await waitFor(() =>
      expect(calls.readbackPendingGeneration).toHaveBeenCalledWith(
        "request-other",
      ),
    );
    expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });

  it("does not silently overwrite an unsupported saved media draft", () => {
    const image = node({
      data: {
        generation_draft: {
          schema_version: 99,
          media_kind: "image",
        },
      },
    });
    const { calls } = mount(project([image]), image);

    expect(screen.getByText(/unsupported contract/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    expect(calls.saveDraft).not.toHaveBeenCalled();
    expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });

  it("filters unsupported image parameters for Mule image models", async () => {
    const image = node({
      data: {
        generation_draft: {
          schema_version: 3,
          media_kind: "image",
          model_id: "gpt-image/gpt-image-2#image",
          mode: "t2i",
          params: {
            size: "1024x1024",
            seed: 0,
            prompt_extend: false,
            watermark: false,
            negative_prompt: "unsupported",
          },
          media: [],
          batch_size: 1,
        },
      },
    });
    const { calls } = mount(project([image]), image);

    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    await waitFor(() =>
      expect(calls.createImageCandidate).toHaveBeenCalledTimes(1),
    );
    expect(calls.saveDraft).toHaveBeenCalledWith(
      "image",
      expect.objectContaining({
        model_id: "gpt-image/gpt-image-2#image",
        params: { size: "1024x1024" },
      }),
    );
    expect(calls.createImageCandidate).toHaveBeenCalledWith(
      "image",
      expect.objectContaining({
        model_id: "gpt-image/gpt-image-2#image",
        params: { size: "1024x1024" },
      }),
    );
  });

  it("honors voice-supported audio controls and preserves volume zero", async () => {
    const audio = node({
      id: "audio",
      type: "audio",
      title: "Narration",
      prompt: "hello",
    });
    const { calls } = mount(project([audio]), audio);
    await waitFor(() =>
      expect(screen.getByLabelText("Voice")).toHaveValue("longxiaochun_v2"),
    );

    fireEvent.change(screen.getByLabelText("Volume"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    await waitFor(() =>
      expect(calls.createAudioCandidate).toHaveBeenCalledTimes(1),
    );
    expect(calls.saveDraft).toHaveBeenCalledWith(
      "audio",
      expect.objectContaining({
        media_kind: "audio",
        voice: "longxiaochun_v2",
        params: expect.objectContaining({ volume: 0 }),
      }),
    );
    expect(calls.createAudioCandidate).toHaveBeenCalledWith(
      "audio",
      expect.objectContaining({
        entrypoint: "atelier.provider_audio.v1",
        voice: "longxiaochun_v2",
        text: "hello",
        params: expect.objectContaining({ volume: 0 }),
      }),
    );
  });

  it("blocks a saved audio draft with an unsupported voice instead of falling back silently", async () => {
    const audio = node({
      id: "audio",
      type: "audio",
      title: "Narration",
      prompt: "hello",
      data: {
        generation_draft: {
          schema_version: 3,
          media_kind: "audio",
          voice: "retired-voice",
          params: { speech_rate: 1.2 },
        },
      },
    });
    const { calls } = mount(project([audio]), audio);

    await waitFor(() =>
      expect(screen.getByText(/unsupported contract/)).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Voice")).toHaveValue("retired-voice");
    expect(screen.getByRole("button", { name: /Generate/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    expect(calls.saveDraft).not.toHaveBeenCalled();
    expect(calls.createAudioCandidate).not.toHaveBeenCalled();
  });

  it("removes unsupported voice params when switching voice families", async () => {
    vi.mocked(atelierMediaApi.listVoices).mockResolvedValue([
      {
        id: "longxiaochun_v2",
        name: "龙小淳 (知性女)",
        family: "cosyvoice",
        supported_parameters: [
          "speech_rate",
          "pitch_rate",
          "volume",
          "instructions",
        ],
      },
      {
        id: "qwen-narrator",
        name: "Qwen Narrator",
        family: "qwen3",
        supported_parameters: ["instructions"],
      },
    ]);
    const audio = node({
      id: "audio",
      type: "audio",
      title: "Narration",
      prompt: "hello",
      data: {
        generation_draft: {
          schema_version: 3,
          media_kind: "audio",
          voice: "longxiaochun_v2",
          params: {
            speech_rate: 1.2,
            pitch_rate: 0.8,
            volume: 0,
            instructions: "calm",
          },
        },
      },
    });
    const { calls } = mount(project([audio]), audio);
    await waitFor(() =>
      expect(screen.getByLabelText("Voice")).toHaveValue("longxiaochun_v2"),
    );

    fireEvent.change(screen.getByLabelText("Voice"), {
      target: { value: "qwen-narrator" },
    });
    expect(screen.queryByLabelText("Speed")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Pitch")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Volume")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    await waitFor(() =>
      expect(calls.createAudioCandidate).toHaveBeenCalledTimes(1),
    );
    expect(calls.createAudioCandidate).toHaveBeenCalledWith(
      "audio",
      expect.objectContaining({
        voice: "qwen-narrator",
        params: { instructions: "calm" },
      }),
    );
  });

  it("selects and hides imported inert media candidates only through explicit actions", async () => {
    const image = node({
      data: {
        candidates: [
          {
            schema_version: 2,
            execution_contract: "atelier-provider-media-v2",
            id: "imported",
            media_kind: "image",
            status: "completed",
            media_url: "assets/imported/project-1/atelier/out.png",
            error_code: null,
            authority: "imported",
            attempt_id: null,
            execution_fingerprint: null,
          },
        ],
      },
    });
    const { calls } = mount(project([image]), image);

    fireEvent.click(screen.getByRole("button", { name: "Use take" }));
    await waitFor(() =>
      expect(calls.selectCandidate).toHaveBeenCalledWith("image", "imported"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    await waitFor(() =>
      expect(calls.setCandidateVisibility).toHaveBeenCalledWith(
        "image",
        "imported",
        false,
      ),
    );
  });

  it("fails closed when ordered image references are missing media", () => {
    const image = node({ data: { reference_node_ids: ["missing"] } });
    const { calls } = mount(project([image]), image);

    expect(screen.getByText(/ordered image references/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));

    expect(calls.createImageCandidate).not.toHaveBeenCalled();
  });
});
