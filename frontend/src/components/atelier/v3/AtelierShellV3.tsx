"use client";
import { useEffect, useMemo, useState } from "react";
import { useAtelierStore } from "@/store/atelierStore";
import { buildReferenceLinks } from "@/lib/atelierCanvas";
import {
  MediaNode,
  DraftNode,
  IdeaNode,
  PlanNode,
  SelectionActionBar,
  BottomNavRail,
  Minimap,
  ToolbarV3,
  RightRailV3,
  Composer,
  toMediaNodeView,
  type ComposerSubmitPayload,
} from "@/components/atelier/v3";
import type {
  AtelierNode,
  AtelierProject,
  AtelierVideoCandidate,
  AtelierApprovalMode,
} from "@/lib/api";

// ── Helpers ──────────────────────────────────────────────────────────────────

const PARENT_TO_CAND_GAP = 32;
const CAND_WIDTH = 200;
const CAND_HEIGHT = 113;
const CAND_GAP = 16;

function selectionKindOf(
  node: AtelierNode,
): "image" | "video" | "audio" | "draft" | "idea" {
  if (node.type === "image") return "image";
  if (node.type === "audio") return "audio";
  if (node.type === "idea") return "idea";
  if (node.type === "video" && node.status === "draft") return "draft";
  return "video";
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function readStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function readCandidates(node: AtelierNode): AtelierVideoCandidate[] {
  const raw = (node.data as { candidates?: unknown })?.candidates;
  if (!Array.isArray(raw)) return [];
  return raw as AtelierVideoCandidate[];
}

function isDraftVideo(node: AtelierNode): boolean {
  return (
    node.type === "video" &&
    node.status === "draft" &&
    typeof (node.data as { intent?: unknown })?.intent === "string"
  );
}

function candidateNodeId(parentId: string, candidateId: string): string {
  return `${parentId}::cand::${candidateId}`;
}

function parseCandidateNodeId(
  id: string,
): { parentId: string; candidateId: string } | null {
  const m = id.match(/^(.+)::cand::(.+)$/);
  if (!m) return null;
  return { parentId: m[1], candidateId: m[2] };
}

function candidatePosition(
  parent: AtelierNode,
  index: number,
): { x: number; y: number } {
  const parentRight = parent.x + (parent.width || 240) + PARENT_TO_CAND_GAP;
  const x = parentRight + (index % 2) * (CAND_WIDTH + CAND_GAP);
  const y = parent.y + Math.floor(index / 2) * (CAND_HEIGHT + 15);
  return { x, y };
}

// ── Node renderers ───────────────────────────────────────────────────────────

function renderNode(
  node: AtelierNode,
  selectedId: string | null,
  select: (id: string | null) => void,
): React.ReactNode {
  const isSelected = node.id === selectedId;
  const onSelect = () => select(node.id);

  if (node.type === "image") {
    const view = toMediaNodeView(node, { selectedNodeId: selectedId });
    if (!view) return null;
    return (
      <MediaNode
        key={node.id}
        id={view.id}
        kind="image"
        src={view.src}
        filename={view.filename ?? node.title}
        status={view.status}
        selected={view.selected}
        x={view.x}
        y={view.y}
        width={view.width}
        height={view.height}
        onSelect={onSelect}
      />
    );
  }

  if (node.type === "audio") {
    const view = toMediaNodeView(node, { selectedNodeId: selectedId });
    if (!view) return null;
    return (
      <MediaNode
        key={node.id}
        id={view.id}
        kind="audio"
        filename={view.filename ?? node.title}
        duration={view.duration}
        status={view.status}
        selected={view.selected}
        x={view.x}
        y={view.y}
        width={view.width}
        height={view.height}
        onSelect={onSelect}
      />
    );
  }

  if (node.type === "idea") {
    const body = readString(node.data?.body) ?? node.prompt ?? "";
    return (
      <IdeaNode
        key={node.id}
        id={node.id}
        body={body}
        selected={isSelected}
        x={node.x}
        y={node.y}
        onSelect={onSelect}
      />
    );
  }

  if (node.type === "plan") {
    const title = readString(node.data?.title) ?? node.title ?? "Plan";
    const bullets = readStringArray(node.data?.bullets);
    return (
      <PlanNode
        key={node.id}
        id={node.id}
        title={title}
        bullets={bullets}
        selected={isSelected}
        x={node.x}
        y={node.y}
        onSelect={onSelect}
      />
    );
  }

  if (node.type === "video") {
    if (isDraftVideo(node)) {
      const intent = readString(node.data?.intent) ?? "Video";
      const modelLabel = readString(node.data?.model) ?? "Wan 2.7";
      const configSummary =
        readString(node.data?.config_summary) ?? "1280×720 · 5s · 4×";
      const refs = readStringArray(node.data?.reference_image_urls);
      return (
        <DraftNode
          key={node.id}
          id={node.id}
          status="draft"
          intent={intent}
          modelLabel={modelLabel}
          configSummary={configSummary}
          refs={refs}
          selected={isSelected}
          x={node.x}
          y={node.y}
          onSelect={onSelect}
        />
      );
    }
    const view = toMediaNodeView(node, { selectedNodeId: selectedId });
    if (view) {
      return (
        <MediaNode
          key={node.id}
          id={view.id}
          kind="video"
          src={view.src}
          filename={view.filename ?? node.title}
          duration={view.duration}
          status={view.status}
          progress={view.progress}
          selected={view.selected}
          selectedAsTake={view.selectedAsTake}
          x={view.x}
          y={view.y}
          width={view.width}
          height={view.height}
          onSelect={onSelect}
        />
      );
    }
  }

  return null;
}

function renderCandidatesAsMediaNodes(
  node: AtelierNode,
  selectedId: string | null,
  select: (id: string | null) => void,
): React.ReactNode[] {
  if (node.type !== "video") return [];
  const candidates = readCandidates(node);
  if (candidates.length === 0) return [];
  const selectedTakeId = readString(
    (node.data as { selected_candidate_id?: unknown })?.selected_candidate_id,
  );

  return candidates.map((c, i) => {
    const { x, y } = candidatePosition(node, i);
    const candKey = candidateNodeId(node.id, c.id);
    const progress =
      typeof (c.params as { progress?: unknown })?.progress === "number"
        ? ((c.params as { progress: number }).progress)
        : undefined;
    return (
      <MediaNode
        key={candKey}
        id={candKey}
        kind="video"
        src={c.video_url ?? undefined}
        filename={c.label || c.id.slice(0, 8)}
        status={c.status}
        progress={progress}
        selectedAsTake={selectedTakeId === c.id}
        selected={selectedId === candKey}
        x={x}
        y={y}
        onSelect={() => select(candKey)}
      />
    );
  });
}

function renderEdges(project: AtelierProject | null): React.ReactNode {
  if (!project) return null;
  const edges: React.ReactNode[] = [];

  // Reference-image → video edges (muted, dotted).
  const refLinks = buildReferenceLinks(project.nodes);
  for (const link of refLinks) {
    const x1 = link.from.x + (link.from.width || 180);
    const y1 = link.from.y + (link.from.height || 180) / 2;
    const x2 = link.to.x;
    const y2 = link.to.y + (link.to.height || 110) / 2;
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.3);
    edges.push(
      <path
        key={`ref-${link.from.id}-${link.to.id}-${link.url.slice(-12)}`}
        d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
        fill="none"
        stroke="rgba(156,163,175,0.35)"
        strokeWidth="1.5"
        strokeDasharray="2 4"
      />,
    );
  }

  // Parent-video → candidate edges (primary).
  for (const node of project.nodes) {
    if (node.type !== "video") continue;
    const candidates = readCandidates(node);
    if (candidates.length === 0) continue;
    const x1 = node.x + (node.width || 240);
    const y1 = node.y + (node.height || 110) / 2;
    candidates.forEach((c, i) => {
      const { x: cx, y: cy } = candidatePosition(node, i);
      const x2 = cx;
      const y2 = cy + CAND_HEIGHT / 2;
      const dx = Math.max(40, Math.abs(x2 - x1) * 0.3);
      edges.push(
        <path
          key={`${node.id}-${c.id}`}
          d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
          fill="none"
          stroke="rgba(100,108,255,0.55)"
          strokeWidth="1.5"
          strokeDasharray={c.status === "completed" ? undefined : "6 4"}
        />,
      );
    });
  }
  return edges;
}

// ── Shell ────────────────────────────────────────────────────────────────────

export function AtelierShellV3() {
  // Store (selectors)
  const project = useAtelierStore((s) => s.currentProject);
  const selectedNodeId = useAtelierStore((s) => s.selectedNodeId);
  const ensureProject = useAtelierStore((s) => s.ensureProject);
  const selectNode = useAtelierStore((s) => s.selectNode);
  const createVideoNode = useAtelierStore((s) => s.createVideoNode);
  const updateAgentPolicy = useAtelierStore((s) => s.updateAgentPolicy);

  const policy = project?.agent_policy;

  // Bootstrap
  useEffect(() => {
    void ensureProject().catch(() => {
      // TODO: surface ensureProject errors via store.error toast surface.
    });
  }, [ensureProject]);

  // Local view state
  const [zoom, setZoom] = useState(100);
  const [minimapOpen, setMinimapOpen] = useState(false);
  const [agentCollapsed, setAgentCollapsed] = useState(false);

  // Selection lookup. selectedNodeId may be a real node id OR a virtual
  // candidate id (parent::cand::cid). For virtual ids we synthesize a
  // stand-in AtelierNode so SelectionActionBar / action wiring can anchor
  // to the candidate's bounding box and operate on its candidate id.
  const selectedNode = useMemo<AtelierNode | undefined>(() => {
    if (!selectedNodeId || !project) return undefined;
    const real = project.nodes.find((n) => n.id === selectedNodeId);
    if (real) return real;
    const parsed = parseCandidateNodeId(selectedNodeId);
    if (!parsed) return undefined;
    const parent = project.nodes.find((n) => n.id === parsed.parentId);
    if (!parent) return undefined;
    const cands = readCandidates(parent);
    const idx = cands.findIndex((c) => c.id === parsed.candidateId);
    if (idx < 0) return undefined;
    const { x, y } = candidatePosition(parent, idx);
    // Synthesize a virtual node — we spread the parent's required scalar
    // fields then override identity, geometry, and status. The `as
    // AtelierNode` cast is intentional: this object only ever flows into
    // SelectionActionBar / Composer / handleActionBar, none of which read
    // server-side fields like project_id beyond what we set explicitly.
    return {
      ...parent,
      id: selectedNodeId,
      type: "video",
      x,
      y,
      width: CAND_WIDTH,
      height: CAND_HEIGHT,
      status: cands[idx].status,
    } as AtelierNode;
  }, [project, selectedNodeId]);

  // Composer anchor: only show below a selected draft. Re-generation from
  // a completed take or candidate is invoked via SelectionActionBar.
  const composerAnchor =
    selectedNode && isDraftVideo(selectedNode)
      ? {
          x: selectedNode.x,
          y: selectedNode.y,
          width: selectedNode.width || 240,
          height: selectedNode.height || 110,
        }
      : null;

  const viewport = {
    width: 1440,
    height: 900,
    rightRailWidth: agentCollapsed ? 56 + 16 : 380 + 16,
  };

  // Action wiring
  const handleComposerSubmit = (
    payload: ComposerSubmitPayload,
    node: AtelierNode,
  ) => {
    if (node.type === "video" && node.status === "draft") {
      const refs = readStringArray(
        (node.data as { reference_image_urls?: unknown })?.reference_image_urls,
      );
      const batch = parseInt(payload.count, 10);
      void useAtelierStore.getState().createVideoCandidates(node.id, {
        prompt: payload.prompt,
        model: payload.modelLabel,
        reference_image_urls: refs,
        batch_size: Number.isFinite(batch) && batch > 0 ? batch : 4,
        params: {},
      });
    }
    // TODO: image/audio submit paths require store actions for image/audio
    // candidate generation that don't exist yet.
  };

  const handleActionBar = (action: string, node: AtelierNode) => {
    const store = useAtelierStore.getState();
    if (action === "selectTake") {
      const parsed = parseCandidateNodeId(node.id);
      if (parsed) void store.selectCandidate(parsed.parentId, parsed.candidateId);
      return;
    }
    if (action === "delete") {
      const parsed = parseCandidateNodeId(node.id);
      if (parsed) {
        void store.deleteCandidate(parsed.parentId, parsed.candidateId);
        return;
      }
      // TODO: deleting a top-level AtelierNode requires a store action that
      // does not exist yet (no `deleteNode` on the store). Punt until added.
      return;
    }
    if (action === "regenerate") {
      // For drafts: re-run the candidate batch with current data. For
      // candidate media: retry the single candidate.
      const parsed = parseCandidateNodeId(node.id);
      if (parsed) {
        void store.retryCandidate(parsed.parentId, parsed.candidateId);
        return;
      }
      if (node.type === "video" && node.status === "draft") {
        void store.regenerateVideoCandidates(node.id);
      }
      return;
    }
    // TODO: play / useAsRef / branch / addToSequence wiring requires either
    // a media-preview surface or store actions that don't exist yet.
  };

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-background text-foreground">
      <ToolbarV3
        onCreate={(kind) => {
          if (kind === "video") {
            void createVideoNode();
          }
          // TODO: image/idea creation requires store actions
          // (`createImageNode`, `createIdeaNode`) that don't exist yet.
        }}
        onAskAgent={() => {
          // TODO: focus right-rail conversation composer when wired.
        }}
        onUndo={() => {
          // TODO: undo/redo requires a history store; not implemented.
        }}
        onRedo={() => {
          // TODO: undo/redo requires a history store; not implemented.
        }}
      />

      {/* Canvas surface */}
      <main className="absolute inset-0">
        {/* edges layer */}
        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          style={{ zIndex: 5 }}
        >
          {renderEdges(project ?? null)}
        </svg>

        {/* nodes */}
        {project?.nodes.map((node) =>
          renderNode(node, selectedNodeId, selectNode),
        )}

        {/* virtual candidate media nodes for each draft's data.candidates */}
        {project?.nodes.flatMap((node) =>
          renderCandidatesAsMediaNodes(node, selectedNodeId, selectNode),
        )}

        {/* selection action bar */}
        {selectedNode ? (
          <SelectionActionBar
            kind={selectionKindOf(selectedNode)}
            x={selectedNode.x}
            y={selectedNode.y}
            width={selectedNode.width || 240}
            onAct={(action) => handleActionBar(action, selectedNode)}
          />
        ) : null}

        {/* composer below selected draft / video */}
        {composerAnchor && selectedNode ? (
          <Composer
            anchor={composerAnchor}
            viewport={viewport}
            prompt={selectedNode.prompt || ""}
            onClose={() => selectNode(null)}
            onSubmit={(payload) => handleComposerSubmit(payload, selectedNode)}
          />
        ) : null}
      </main>

      {/* right rail (Agent-only) */}
      <RightRailV3
        agentStatus="active"
        mode={(policy?.approval_mode as AtelierApprovalMode) ?? "untrusted"}
        onModeChange={(m) => {
          void updateAgentPolicy({ approval_mode: m });
        }}
        collapsed={agentCollapsed}
        onCollapse={() => setAgentCollapsed((c) => !c)}
      >
        <div className="flex-1 overflow-y-auto p-3 text-[12px] text-text-muted">
          <p>Agent conversation goes here. (Full wiring lands in a follow-up.)</p>
        </div>
      </RightRailV3>

      {/* bottom nav rail */}
      <BottomNavRail
        zoom={zoom}
        minimapOpen={minimapOpen}
        onZoomChange={setZoom}
        onFit={() => setZoom(100)}
        onToggleMinimap={() => setMinimapOpen((o) => !o)}
      />

      {/* minimap floating widget */}
      {minimapOpen && project ? (
        <Minimap
          nodes={project.nodes.map((n) => ({ x: n.x, y: n.y }))}
          viewport={{ x: 0, y: 0, w: 1440, h: 900 }}
        />
      ) : null}
    </div>
  );
}
