// @vitest-environment happy-dom
import React from "react";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import messages from "../../messages/en.json";
import { AtelierShell } from "@/components/atelier/v3/AtelierShell";
import {
  activateRuntimeEnvironment,
  clearRuntimeEnvironment,
} from "@/lib/runtimeEnvironment";
import type { AtelierNode, AtelierProject } from "@/lib/atelierTypes";

const store = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock("@/store/atelierStore", () => {
  const useAtelierStore = (
    selector: (state: Record<string, unknown>) => unknown,
  ) => selector(store.state);
  useAtelierStore.getState = () => store.state;
  return { useAtelierStore };
});
vi.mock("@/components/atelier/v3/GenerationPanel", () => ({
  GenerationPanel: ({ selectedNode }: { selectedNode: AtelierNode | null }) =>
    selectedNode?.type === "video" ? (
      <div data-testid="video-generation-panel" />
    ) : null,
}));
vi.mock("@/components/atelier/v3/MediaComposer", () => ({
  MediaComposer: ({ selectedNode }: { selectedNode: AtelierNode | null }) =>
    selectedNode?.type === "image" || selectedNode?.type === "audio" ? (
      <div data-testid="media-composer" />
    ) : null,
}));

vi.mock("@/components/atelier/v3/AgentTurnPanel", () => ({
  AgentTurnPanel: () => <div data-testid="agent-turn-panel" />,
}));
vi.mock("@/components/atelier/v3/SequenceWorkbench", () => ({
  SequenceWorkbench: () => <div data-testid="sequence-workbench" />,
}));
vi.mock("@/components/atelier/v3/ProjectPackageControls", () => ({
  ProjectPackageControls: () => <div data-testid="project-package-controls" />,
}));

function node(type: "image" | "audio" | "video", id = type): AtelierNode {
  return {
    id,
    project_id: "project-1",
    type,
    title: id,
    prompt: "",
    status: "draft",
    x: 0,
    y: 0,
    width: 240,
    height: 140,
    source_project_id: null,
    frame_id: null,
    asset_id: null,
    video_task_id: null,
    media_urls: [],
    data: {},
    created_by: "user",
    created_at: 1,
    updated_at: 1,
  };
}

function project(nodes: AtelierNode[]): AtelierProject {
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
    revision: 1,
    created_at: 1,
    updated_at: 1,
  };
}

function renderShell(selected: "image" | "audio" | "video") {
  const nodes = [node("image"), node("audio"), node("video")];
  store.state = {
    projects: [project(nodes)],
    currentProject: project(nodes),
    selectedNodeId: selected,
    isLoading: false,
    isSaving: false,
    needsRefresh: false,
    error: null,
    pendingUploads: [],
    pendingGenerations: [],
    ensureProject: vi.fn().mockResolvedValue(project(nodes)),
    createProject: vi.fn(),
    loadProjects: vi.fn(),
    switchProject: vi.fn(),
    refreshProject: vi.fn(),
    createNode: vi.fn(),
    updateNodeText: vi.fn(),
    updateNodeLayout: vi.fn(),
    updateRegionAppearance: vi.fn(),
    moveNodes: vi.fn(),
    wrapRegion: vi.fn(),
    deleteNodes: vi.fn(),
    restoreNodes: vi.fn(),
    attachPendingReferenceNode: vi.fn(),
    attachReferenceNode: vi.fn(),
    detachReferenceNode: vi.fn(),
    createMediaNode: vi.fn(),
    uploadReference: vi.fn(),
    loadPendingUploads: vi.fn().mockReturnValue([]),
    loadPendingGenerations: vi.fn().mockReturnValue([]),
    readbackPendingUpload: vi.fn(),
    readbackPendingGeneration: vi.fn(),
    createImageCandidate: vi.fn(),
    createAudioCandidate: vi.fn(),
    refreshGenerationAttempt: vi.fn(),
    continueGenerationAttempt: vi.fn(),
    cancelGenerationAttempt: vi.fn(),
    saveMediaGenerationDraft: vi.fn(),
    selectMediaGenerationCandidate: vi.fn(),
    setMediaGenerationCandidateVisibility: vi.fn(),
    selectNode: vi.fn(),
  };
  return render(
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <AtelierShell />
    </NextIntlClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  clearRuntimeEnvironment();
  activateRuntimeEnvironment({
    schema_version: 1,
    storage_id: "11111111-1111-4111-8111-111111111111",
  });
});

describe("Atelier shell media composer routing", () => {
  it("routes image and audio nodes to the media composer", () => {
    const imageView = renderShell("image");
    expect(screen.getByTestId("media-composer")).toBeInTheDocument();
    expect(
      screen.queryByTestId("video-generation-panel"),
    ).not.toBeInTheDocument();
    imageView.unmount();

    renderShell("audio");
    expect(screen.getByTestId("media-composer")).toBeInTheDocument();
    expect(
      screen.queryByTestId("video-generation-panel"),
    ).not.toBeInTheDocument();
  });

  it("keeps video nodes on the existing generation panel", () => {
    renderShell("video");
    expect(screen.getByTestId("video-generation-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("media-composer")).not.toBeInTheDocument();
  });
});
