"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAtelierStore } from "@/store/atelierStore";
import { buildReferenceLinks } from "@/lib/atelierCanvas";
import { getAssetUrl } from "@/lib/utils";
import { Play, X } from "lucide-react";
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
  AgentPanelV3,
  Composer,
  toMediaNodeView,
  type ComposerSubmitPayload,
} from "@/components/atelier/v3";
import {
  api,
  type AtelierNode,
  type AtelierProject,
  type AtelierVideoCandidate,
  type AtelierApprovalMode,
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

// Lightweight in-memory toast queue. Could move to a store later.
type Toast = { id: number; kind: "info" | "error" | "success"; text: string };
let toastSeq = 0;

export function AtelierShellV3() {
  // Store (selectors)
  const project = useAtelierStore((s) => s.currentProject);
  const selectedNodeId = useAtelierStore((s) => s.selectedNodeId);
  const ensureProject = useAtelierStore((s) => s.ensureProject);
  const selectNode = useAtelierStore((s) => s.selectNode);
  const createVideoNode = useAtelierStore((s) => s.createVideoNode);
  const createImageNode = useAtelierStore((s) => s.createImageNode);
  const createIdeaNode = useAtelierStore((s) => s.createIdeaNode);
  const deleteAtelierNode = useAtelierStore((s) => s.deleteAtelierNode);
  const branchFromCandidate = useAtelierStore((s) => s.branchFromCandidate);
  const updateAgentPolicy = useAtelierStore((s) => s.updateAgentPolicy);
  const refreshCurrentProject = useAtelierStore((s) => s.refreshCurrentProject);

  const policy = project?.agent_policy;

  // Toast queue
  const [toasts, setToasts] = useState<Toast[]>([]);
  const pushToast = (kind: Toast["kind"], text: string) => {
    const id = ++toastSeq;
    setToasts((t) => [...t, { id, kind, text }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  };

  // Bootstrap
  useEffect(() => {
    void ensureProject().catch((err: unknown) => {
      pushToast("error", `Failed to open Atelier: ${err instanceof Error ? err.message : String(err)}`);
    });
  }, [ensureProject]);

  // Poll for in-flight candidates so the canvas updates without manual refresh.
  useEffect(() => {
    const hasRunning = (project?.nodes ?? []).some((node) => {
      const cands = readCandidates(node);
      return cands.some((c) => c.status === "pending" || c.status === "processing");
    });
    if (!hasRunning) return;
    const t = window.setInterval(() => {
      void refreshCurrentProject().catch(() => {});
    }, 3000);
    return () => window.clearInterval(t);
  }, [project, refreshCurrentProject]);

  // Local view state
  const [zoom, setZoom] = useState(100);          // percent, 25..300
  const [panX, setPanX] = useState(0);            // world translate x (px in CSS)
  const [panY, setPanY] = useState(0);            // world translate y
  const [minimapOpen, setMinimapOpen] = useState(false);
  const [agentCollapsed, setAgentCollapsed] = useState(false);
  const [previewVideoUrl, setPreviewVideoUrl] = useState<string | null>(null);
  const [editingIdeaId, setEditingIdeaId] = useState<string | null>(null);
  const [editingIdeaBody, setEditingIdeaBody] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageNodeIdForUploadRef = useRef<string | null>(null); // when set, the upload writes to this node
  const mainRef = useRef<HTMLElement>(null);

  // Drag refs (for nodes) + pan ref (for background).
  const nodeDragRef = useRef<{
    nodeId: string;
    startWorldX: number;     // node's world coord at drag start
    startWorldY: number;
    startPointerX: number;
    startPointerY: number;
    moved: boolean;
  } | null>(null);
  const panDragRef = useRef<{ startX: number; startY: number; startPanX: number; startPanY: number } | null>(null);

  const zoomFactor = zoom / 100;

  // Helper: translate a screen-space delta to world-space (just divide by zoom).
  const screenDeltaToWorld = (dx: number, dy: number) => ({ x: dx / zoomFactor, y: dy / zoomFactor });

  // ── Pan + zoom handlers ────────────────────────────────────────────────────
  const handleMainPointerDown = (event: React.PointerEvent) => {
    // Start pan only when clicking the canvas background (not a node / overlay).
    if (event.target !== event.currentTarget && (event.target as HTMLElement).closest('[data-atelier-node],[role="dialog"],[role="toolbar"],[role="region"]')) return;
    if (event.button !== 0) return;
    selectNode(null);
    event.currentTarget.setPointerCapture(event.pointerId);
    panDragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      startPanX: panX,
      startPanY: panY,
    };
  };

  const handleMainPointerMove = (event: React.PointerEvent) => {
    // Pan
    if (panDragRef.current) {
      const dx = event.clientX - panDragRef.current.startX;
      const dy = event.clientY - panDragRef.current.startY;
      setPanX(panDragRef.current.startPanX + dx);
      setPanY(panDragRef.current.startPanY + dy);
      return;
    }
    // Node drag
    if (nodeDragRef.current) {
      const dx = event.clientX - nodeDragRef.current.startPointerX;
      const dy = event.clientY - nodeDragRef.current.startPointerY;
      const wd = screenDeltaToWorld(dx, dy);
      const newX = Math.round(nodeDragRef.current.startWorldX + wd.x);
      const newY = Math.round(nodeDragRef.current.startWorldY + wd.y);
      if (Math.abs(dx) + Math.abs(dy) > 3) nodeDragRef.current.moved = true;
      // Optimistic local update
      useAtelierStore.getState().moveNodeLocal(nodeDragRef.current.nodeId, newX, newY);
    }
  };

  const handleMainPointerUp = () => {
    if (panDragRef.current) {
      panDragRef.current = null;
      return;
    }
    if (nodeDragRef.current) {
      const drag = nodeDragRef.current;
      nodeDragRef.current = null;
      if (drag.moved) {
        const real = useAtelierStore.getState().currentProject?.nodes.find((n) => n.id === drag.nodeId);
        if (real) {
          void useAtelierStore.getState().commitNodePosition(drag.nodeId, real.x, real.y).catch(() => {});
        }
      }
    }
  };

  const handleNodePointerDown = (event: React.PointerEvent, node: AtelierNode) => {
    if (event.button !== 0) return;
    // Don't drag virtual candidates (they're derived); selection still fires.
    if (parseCandidateNodeId(node.id)) return;
    // Don't initiate drag on textareas / inputs / buttons inside the node.
    const tag = (event.target as HTMLElement).tagName;
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "BUTTON") return;
    nodeDragRef.current = {
      nodeId: node.id,
      startWorldX: node.x,
      startWorldY: node.y,
      startPointerX: event.clientX,
      startPointerY: event.clientY,
      moved: false,
    };
  };

  const handleWheel = (event: React.WheelEvent) => {
    if (!event.ctrlKey && !event.metaKey && Math.abs(event.deltaX) === 0 && Math.abs(event.deltaY) < 1) return;
    // Zoom only when Ctrl/Cmd held (matches Figma); otherwise trackpad two-finger pan.
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      const rect = mainRef.current?.getBoundingClientRect();
      const cx = event.clientX - (rect?.left ?? 0);
      const cy = event.clientY - (rect?.top ?? 0);
      // World coords under cursor before zoom
      const wxBefore = (cx - panX) / zoomFactor;
      const wyBefore = (cy - panY) / zoomFactor;
      const next = Math.max(25, Math.min(300, Math.round(zoom * (event.deltaY < 0 ? 1.1 : 0.9))));
      const nextFactor = next / 100;
      // Adjust pan so cursor stays anchored
      setZoom(next);
      setPanX(cx - wxBefore * nextFactor);
      setPanY(cy - wyBefore * nextFactor);
    } else {
      // Two-finger pan
      setPanX((p) => p - event.deltaX);
      setPanY((p) => p - event.deltaY);
    }
  };

  const handleFitView = () => {
    const nodes = project?.nodes ?? [];
    const rect = mainRef.current?.getBoundingClientRect();
    if (nodes.length === 0 || !rect) {
      setZoom(100);
      setPanX(0);
      setPanY(0);
      return;
    }
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of nodes) {
      minX = Math.min(minX, n.x);
      minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + (n.width || 240));
      maxY = Math.max(maxY, n.y + (n.height || 200));
    }
    const padding = 80;
    const worldW = (maxX - minX) + padding * 2;
    const worldH = (maxY - minY) + padding * 2;
    const fitZoom = Math.min(rect.width / worldW, rect.height / worldH, 1) * 100;
    const next = Math.max(25, Math.min(300, Math.round(fitZoom)));
    const nextFactor = next / 100;
    setZoom(next);
    setPanX((rect.width - (maxX - minX) * nextFactor) / 2 - minX * nextFactor);
    setPanY((rect.height - (maxY - minY) * nextFactor) / 2 - minY * nextFactor);
  };

  const handleZoomChange = (z: number) => {
    // Zoom around the canvas center.
    const rect = mainRef.current?.getBoundingClientRect();
    if (!rect) {
      setZoom(z);
      return;
    }
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const wxBefore = (cx - panX) / zoomFactor;
    const wyBefore = (cy - panY) / zoomFactor;
    const nextFactor = z / 100;
    setZoom(z);
    setPanX(cx - wxBefore * nextFactor);
    setPanY(cy - wyBefore * nextFactor);
  };

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
      void useAtelierStore.getState()
        .createVideoCandidates(node.id, {
          prompt: payload.prompt,
          model: payload.modelLabel,
          reference_image_urls: refs,
          batch_size: Number.isFinite(batch) && batch > 0 ? batch : 4,
          params: {},
        })
        .then(() => pushToast("success", `Generating ${batch || 4} candidates…`))
        .catch((err: unknown) => pushToast("error", `Generate failed: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    pushToast("info", `Tab "${payload.tab}" submission isn't wired yet — use I2V/R2V drafts for now.`);
  };

  const handleActionBar = (action: string, node: AtelierNode) => {
    const store = useAtelierStore.getState();

    if (action === "selectTake") {
      const parsed = parseCandidateNodeId(node.id);
      if (parsed) {
        void store.selectCandidate(parsed.parentId, parsed.candidateId)
          .then(() => pushToast("success", "Selected as take"))
          .catch((err: unknown) => pushToast("error", `Select failed: ${err instanceof Error ? err.message : String(err)}`));
      }
      return;
    }

    if (action === "delete") {
      const parsed = parseCandidateNodeId(node.id);
      if (parsed) {
        void store.deleteCandidate(parsed.parentId, parsed.candidateId)
          .then(() => pushToast("info", "Candidate deleted"))
          .catch((err: unknown) => pushToast("error", `Delete failed: ${err instanceof Error ? err.message : String(err)}`));
        return;
      }
      // Top-level node delete with confirmation
      const ok = window.confirm(`Delete this ${node.type} node?`);
      if (!ok) return;
      void deleteAtelierNode(node.id)
        .then(() => pushToast("info", "Node deleted"))
        .catch((err: unknown) => pushToast("error", `Delete failed: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }

    if (action === "regenerate") {
      // Only candidate-level retry is sensible without payload. Drafts go
      // through the floating Composer; non-candidate "regenerate" on top-
      // level video opens a confirm + re-runs with current node data.
      const parsed = parseCandidateNodeId(node.id);
      if (parsed) {
        void store.retryCandidate(parsed.parentId, parsed.candidateId)
          .then(() => pushToast("info", "Retrying take…"))
          .catch((err: unknown) => pushToast("error", `Retry failed: ${err instanceof Error ? err.message : String(err)}`));
        return;
      }
      pushToast("info", "Use the Composer below the draft to re-run generation.");
      return;
    }

    if (action === "play") {
      const parsed = parseCandidateNodeId(node.id);
      if (parsed && project) {
        const parent = project.nodes.find((n) => n.id === parsed.parentId);
        const cand = parent ? readCandidates(parent).find((c) => c.id === parsed.candidateId) : undefined;
        if (cand?.video_url) {
          setPreviewVideoUrl(cand.video_url);
          return;
        }
      }
      const url = node.media_urls?.[0];
      if (url) {
        setPreviewVideoUrl(url);
        return;
      }
      pushToast("info", "Nothing to play yet.");
      return;
    }

    if (action === "branch") {
      const parsed = parseCandidateNodeId(node.id);
      if (parsed) {
        void branchFromCandidate(parsed.parentId, parsed.candidateId)
          .then(() => pushToast("success", "Branched · new draft created"))
          .catch((err: unknown) => pushToast("error", `Branch failed: ${err instanceof Error ? err.message : String(err)}`));
        return;
      }
      pushToast("info", "Branch from a take (candidate) — select a candidate first.");
      return;
    }

    if (action === "addToSequence") {
      const parsed = parseCandidateNodeId(node.id);
      const parentId = parsed?.parentId ?? node.id;
      const cid = parsed?.candidateId;
      if (cid) {
        // Add to local sequence list (client-side state).
        setSequence((prev) => {
          if (prev.some((s) => s.parentId === parentId && s.candidateId === cid)) return prev;
          return [...prev, { parentId, candidateId: cid }];
        });
        pushToast("success", "Added to Sequence");
        return;
      }
      pushToast("info", "Add to Sequence works on a selected take.");
      return;
    }

    if (action === "useAsRef") {
      pushToast("info", "Use-as-reference: drag onto a draft (coming next).");
      return;
    }
  };

  // Sequence is a simple client-side list of {parentId, candidateId} for now.
  const [sequence, setSequence] = useState<Array<{ parentId: string; candidateId: string }>>([]);

  // Resolve sequence entries against current project candidates so we can
  // render thumbnails + handle stale entries (parent or candidate gone).
  const sequenceEntries = useMemo(() => {
    if (!project) return [];
    return sequence
      .map((entry) => {
        const parent = project.nodes.find((n) => n.id === entry.parentId);
        if (!parent) return null;
        const cand = readCandidates(parent).find((c) => c.id === entry.candidateId);
        if (!cand || cand.status !== "completed") return null;
        return { entry, parent, cand };
      })
      .filter(<T,>(x: T): x is NonNullable<T> => x != null);
  }, [project, sequence]);

  const handleFilePicked = (file: File | undefined) => {
    if (!file) return;
    const targetNodeId = imageNodeIdForUploadRef.current;
    imageNodeIdForUploadRef.current = null;
    if (targetNodeId) {
      // Upload into an existing image-draft node: replace its media_urls + status.
      const proj = useAtelierStore.getState().currentProject;
      const target = proj?.nodes.find((n) => n.id === targetNodeId);
      if (!proj || !target) {
        pushToast("error", "Target image node disappeared.");
        return;
      }
      void api
        .uploadFile(file)
        .then((r) => {
          const url = r.url as string;
          return useAtelierStore.getState().updateNode(targetNodeId, {
            status: "completed",
            media_urls: [url],
            data: { ...(target.data ?? {}), filename: file.name },
          });
        })
        .then(() => pushToast("success", "Image uploaded"))
        .catch((err: unknown) => pushToast("error", `Upload failed: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    void createImageNode(file)
      .then(() => pushToast("success", "Reference uploaded"))
      .catch((err: unknown) => pushToast("error", `Upload failed: ${err instanceof Error ? err.message : String(err)}`));
  };

  // Create an empty image-draft node first, then user selects "Upload" or
  // "Generate" via the action bar. Position around current viewport center.
  const createEmptyImageDraft = async () => {
    const proj = await ensureProject();
    const rect = mainRef.current?.getBoundingClientRect();
    const centerX = rect ? (rect.width / 2 - panX) / zoomFactor : 200;
    const centerY = rect ? (rect.height / 2 - panY) / zoomFactor : 200;
    const node = await api.createAtelierNode(proj.id, {
      type: "image",
      title: "Image (empty)",
      status: "draft",
      x: Math.round(centerX - 90),
      y: Math.round(centerY - 90),
      width: 180,
      height: 180,
      media_urls: [],
      data: {},
    });
    // Refresh project so the new node appears, then select it.
    await refreshCurrentProject();
    selectNode(node.id);
  };

  const isBootingProject = !project;
  const projectIsEmpty = !!project && project.nodes.length === 0;

  // Variation pool so each new draft has a different intent + model spread.
  const draftVariations = [
    { intent: "Cinematic interpretation", model: "Wan 2.7" },
    { intent: "Anime stylization", model: "HappyHorse R2V" },
    { intent: "Documentary handheld", model: "Wan 2.7" },
    { intent: "Wildcard direction", model: "Vidu Q3" },
  ];

  const handleCreateVideo = async () => {
    try {
      const node = await createVideoNode();
      const variant = draftVariations[(project?.nodes.length ?? 0) % draftVariations.length];
      const rect = mainRef.current?.getBoundingClientRect();
      const cx = rect ? (rect.width / 2 - panX) / zoomFactor - 120 : node.x;
      const cy = rect ? (rect.height / 2 - panY) / zoomFactor - 55 : node.y;
      await useAtelierStore.getState().updateNode(node.id, {
        status: "draft",
        x: Math.round(cx),
        y: Math.round(cy),
        width: 240,
        height: 110,
        data: {
          ...(node.data ?? {}),
          intent: variant.intent,
          model: variant.model,
          config_summary: "1280×720 · 5s · 4×",
          reference_image_urls: [],
          candidates: [],
        },
      });
    } catch (err: unknown) {
      pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  return (
    <div
      className="relative h-screen w-screen overflow-hidden bg-background text-foreground"
      onWheel={handleWheel as unknown as React.WheelEventHandler<HTMLDivElement>}
    >
      <ToolbarV3
        onCreate={(kind) => {
          if (kind === "video") {
            void handleCreateVideo();
            return;
          }
          if (kind === "image") {
            // v0.3.2+: create an empty image-draft node; user uploads or
            // generates from its action bar. (PRD §6 / user request.)
            void createEmptyImageDraft()
              .then(() => pushToast("info", "Image node added — select Upload from its action bar."))
              .catch((err: unknown) => pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`));
            return;
          }
          if (kind === "idea") {
            void createIdeaNode()
              .then((node) => {
                setEditingIdeaId(node.id);
                setEditingIdeaBody((node.data as { body?: string })?.body ?? "");
              })
              .catch((err: unknown) => pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`));
            return;
          }
        }}
        onAskAgent={() => setAgentCollapsed(false)}
        onUndo={() => pushToast("info", "Undo isn't wired yet.")}
        onRedo={() => pushToast("info", "Redo isn't wired yet.")}
      />

      {/* Hidden file input shared by Toolbar (legacy direct upload) and the
          per-node "Upload" action bar entry (via imageNodeIdForUploadRef). */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          handleFilePicked(e.target.files?.[0]);
          e.target.value = "";
        }}
      />

      {/* Canvas surface — receives pan + zoom + node-drag pointer events */}
      <main
        ref={mainRef}
        className="absolute inset-0 cursor-default touch-none select-none"
        style={{ cursor: panDragRef.current ? "grabbing" : "default" }}
        onPointerDown={handleMainPointerDown}
        onPointerMove={handleMainPointerMove}
        onPointerUp={handleMainPointerUp}
        onPointerCancel={() => {
          panDragRef.current = null;
          nodeDragRef.current = null;
        }}
      >
        {/* loading skeleton (in screen coords) */}
        {isBootingProject ? (
          <div className="absolute inset-0 grid place-items-center">
            <div className="rounded-md border border-glass-border bg-glass px-4 py-2 text-[12px] text-text-secondary backdrop-blur-md">
              Loading Atelier…
            </div>
          </div>
        ) : null}

        {/* empty canvas hint (DESIGN.md §11.1) */}
        {projectIsEmpty ? (
          <div className="pointer-events-none absolute inset-0 grid place-items-center">
            <div className="font-display text-[15px] text-text-muted">
              Drop a seed. Press <span className="font-mono text-text-secondary">V</span> for video,
              {" "}<span className="font-mono text-text-secondary">I</span> for image,
              {" "}<span className="font-mono text-text-secondary">T</span> for idea.
            </div>
          </div>
        ) : null}

        {/* World — everything in canvas space lives here. Transformed by zoom + pan. */}
        <div
          className="absolute left-0 top-0 origin-top-left"
          style={{ transform: `translate(${panX}px, ${panY}px) scale(${zoomFactor})` }}
        >
          {/* edges layer (in world coords) */}
          <svg
            className="pointer-events-none absolute"
            style={{ left: -10000, top: -10000, width: 20000, height: 20000, zIndex: 5 }}
            viewBox="-10000 -10000 20000 20000"
          >
            {renderEdges(project ?? null)}
          </svg>

          {/* nodes — each wrapped in a drag-aware div */}
          {project?.nodes.map((node) => (
            <div
              key={node.id}
              data-atelier-node={node.id}
              onPointerDown={(e) => handleNodePointerDown(e, node)}
              style={{ touchAction: "none" }}
            >
              {renderNode(node, selectedNodeId, selectNode)}
            </div>
          ))}

          {/* virtual candidate media nodes (no drag — derived) */}
          {project?.nodes.flatMap((node) =>
            renderCandidatesAsMediaNodes(node, selectedNodeId, selectNode),
          )}

          {/* selection action bar (world coords; scales with zoom) */}
          {selectedNode ? (
            <SelectionActionBar
              kind={selectionKindOf(selectedNode)}
              x={selectedNode.x}
              y={selectedNode.y}
              width={selectedNode.width || 240}
              onAct={(action) => handleActionBar(action, selectedNode)}
            />
          ) : null}

          {/* composer below selected draft (world coords) */}
          {composerAnchor && selectedNode ? (
            <Composer
              anchor={composerAnchor}
              viewport={viewport}
              prompt={selectedNode.prompt || ""}
              onClose={() => selectNode(null)}
              onSubmit={(payload) => handleComposerSubmit(payload, selectedNode)}
            />
          ) : null}

          {/* Inline IdeaNode editor: when an idea is being edited, overlay a
              textarea on top of it in world coords. */}
          {editingIdeaId && project ? (() => {
            const node = project.nodes.find((n) => n.id === editingIdeaId);
            if (!node || node.type !== "idea") return null;
            return (
              <textarea
                autoFocus
                value={editingIdeaBody}
                onChange={(e) => setEditingIdeaBody(e.target.value)}
                onBlur={() => {
                  void useAtelierStore.getState().updateNode(node.id, {
                    prompt: editingIdeaBody,
                    data: { ...(node.data ?? {}), body: editingIdeaBody },
                  }).catch((err: unknown) => pushToast("error", `Save failed: ${err instanceof Error ? err.message : String(err)}`));
                  setEditingIdeaId(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setEditingIdeaId(null);
                  }
                  if ((e.key === "Enter") && (e.metaKey || e.ctrlKey)) {
                    (e.target as HTMLTextAreaElement).blur();
                  }
                }}
                className="absolute z-30 w-[220px] resize-none rounded-md border border-primary/60 bg-amber-400/[0.06] px-3 py-2.5 text-[13px] leading-relaxed text-foreground outline-none ring-2 ring-primary/30"
                style={{ left: node.x, top: node.y, height: Math.max(80, (node.height || 120)) }}
              />
            );
          })() : null}

          {/* Upload affordance for empty image-draft nodes (selected) */}
          {selectedNode && selectedNode.type === "image" && selectedNode.status === "draft" && (selectedNode.media_urls?.length ?? 0) === 0 ? (
            <div
              className="absolute z-30 grid w-[180px] place-items-center rounded-md border border-dashed border-primary/60 bg-primary/[0.04] p-3 text-center"
              style={{ left: selectedNode.x, top: selectedNode.y, height: 180 }}
            >
              <div className="space-y-2">
                <div className="text-[11px] font-mono uppercase tracking-wider text-primary/85">Image draft</div>
                <button
                  type="button"
                  className="block w-full rounded-md bg-primary px-2 py-1.5 text-[12px] font-semibold text-white hover:bg-primary/90"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => {
                    imageNodeIdForUploadRef.current = selectedNode.id;
                    fileInputRef.current?.click();
                  }}
                >
                  Upload image
                </button>
                <button
                  type="button"
                  className="block w-full rounded-md border border-glass-border bg-glass px-2 py-1.5 text-[12px] text-text-secondary hover:bg-hover-bg hover:text-foreground"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => pushToast("info", "Generate from prompt (T2I) is coming next.")}
                >
                  Generate from prompt
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </main>

      {/* right rail (Agent-only) */}
      <RightRailV3
        agentStatus="active"
        mode={(policy?.approval_mode as AtelierApprovalMode) ?? "untrusted"}
        onModeChange={(m) => {
          void updateAgentPolicy({ approval_mode: m })
            .catch((err: unknown) => pushToast("error", `Policy save failed: ${err instanceof Error ? err.message : String(err)}`));
        }}
        collapsed={agentCollapsed}
        onCollapse={() => setAgentCollapsed((c) => !c)}
      >
        <AgentPanelV3 pushToast={pushToast} />
      </RightRailV3>

      {/* bottom nav rail */}
      <BottomNavRail
        zoom={zoom}
        minimapOpen={minimapOpen}
        onZoomChange={handleZoomChange}
        onFit={handleFitView}
        onToggleMinimap={() => setMinimapOpen((o) => !o)}
      />

      {/* minimap floating widget */}
      {minimapOpen && project ? (
        <Minimap
          nodes={project.nodes.map((n) => ({ x: n.x, y: n.y }))}
          viewport={{ x: 0, y: 0, w: 1440, h: 900 }}
        />
      ) : null}

      {/* sequence strip (bottom, between left edge and right rail) */}
      <div
        className="absolute bottom-4 left-[280px] z-20 rounded-2xl border border-glass-border bg-glass p-2 backdrop-blur-md"
        style={{ right: agentCollapsed ? 88 : 412 }}
      >
        <div className="mb-1.5 flex items-center justify-between text-[10px] font-mono uppercase tracking-wider text-text-muted">
          <span>Sequence Strip · {sequenceEntries.length} clip{sequenceEntries.length === 1 ? "" : "s"}</span>
          {sequenceEntries.length > 0 ? <button onClick={() => setSequence([])} className="text-text-muted hover:text-foreground">clear</button> : null}
        </div>
        {sequenceEntries.length === 0 ? (
          <div className="px-2 py-2 text-[11px] text-text-muted">Select a completed take, then "Add to Sequence" from its action bar.</div>
        ) : (
          <div className="flex items-center gap-2 overflow-x-auto">
            {sequenceEntries.map(({ entry, parent, cand }, i) => (
              <div key={`${entry.parentId}-${entry.candidateId}`} className="group relative h-[68px] w-[140px] shrink-0 overflow-hidden rounded-md border border-glass-border bg-elevated/80">
                {cand.video_url ? (
                  <img src={getAssetUrl(cand.video_url)} alt="" className="h-full w-full object-cover" />
                ) : null}
                <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-black/60 px-1.5 py-1 backdrop-blur-sm">
                  <span className="truncate text-[10px] text-foreground">{parent.title}</span>
                  <span className="font-mono text-[9px] text-text-muted">#{i + 1}</span>
                </div>
                <button
                  onClick={() => setSequence((prev) => prev.filter((s) => !(s.parentId === entry.parentId && s.candidateId === entry.candidateId)))}
                  className="absolute right-1 top-1 rounded bg-black/55 p-0.5 text-white/80 opacity-0 hover:bg-red-500/70 group-hover:opacity-100"
                  aria-label={`Remove ${parent.title} from sequence`}
                >
                  <X size={10} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* preview video modal */}
      {previewVideoUrl ? (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/80 backdrop-blur-sm"
          onClick={() => setPreviewVideoUrl(null)}
          role="dialog"
          aria-label="Video preview"
        >
          <div className="relative max-h-[80vh] max-w-[80vw] overflow-hidden rounded-xl border border-glass-border bg-elevated shadow-2xl shadow-black/40" onClick={(e) => e.stopPropagation()}>
            <video src={getAssetUrl(previewVideoUrl)} controls autoPlay className="block max-h-[80vh] max-w-[80vw]" />
            <button
              onClick={() => setPreviewVideoUrl(null)}
              className="absolute right-2 top-2 rounded-full bg-black/55 p-1.5 text-white/90 hover:bg-black/75"
              aria-label="Close preview"
            >
              <X size={14} />
            </button>
          </div>
        </div>
      ) : null}

      {/* toast queue (top-center) */}
      {toasts.length > 0 ? (
        <div className="fixed left-1/2 top-4 z-50 flex -translate-x-1/2 flex-col gap-2">
          {toasts.map((t) => {
            const tone =
              t.kind === "error" ? "border-red-400/60 bg-red-400/15 text-red-100" :
              t.kind === "success" ? "border-emerald-400/60 bg-emerald-400/10 text-emerald-100" :
              "border-glass-border bg-elevated text-foreground";
            return (
              <div
                key={t.id}
                role="status"
                className={`pointer-events-auto rounded-md border px-3 py-2 text-[12px] backdrop-blur-md shadow-2xl shadow-black/40 ${tone}`}
              >
                {t.text}
              </div>
            );
          })}
        </div>
      ) : null}

      {/* play / preview button affordance for selected video media (in addition
          to action bar's Play). Helps demonstrate clickable preview when nothing
          is selected. */}
      {selectedNode && selectedNode.type === "video" && !isDraftVideo(selectedNode) && selectedNode.media_urls?.[0] ? (
        <button
          onClick={() => setPreviewVideoUrl(selectedNode.media_urls[0])}
          className="absolute z-30 grid h-9 w-9 place-items-center rounded-full bg-primary text-white shadow-2xl shadow-black/40 hover:bg-primary/90"
          style={{ left: selectedNode.x + (selectedNode.width || 200) / 2 - 18, top: selectedNode.y + (selectedNode.height || 113) / 2 - 18 }}
          aria-label="Play preview"
        >
          <Play size={14} />
        </button>
      ) : null}
    </div>
  );
}
