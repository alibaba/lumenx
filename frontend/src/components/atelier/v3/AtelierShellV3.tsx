"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAtelierStore } from "@/store/atelierStore";
import { buildReferenceLinks } from "@/lib/atelierCanvas";
import { getAssetUrl } from "@/lib/utils";
import { Link2, Play, X } from "lucide-react";
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
  selectedIds: Set<string>,
  select: (id: string | null) => void,
): React.ReactNode {
  const isSelected = selectedIds.has(node.id);
  const onSelect = () => select(node.id);

  if (node.type === "image") {
    const view = toMediaNodeView(node, { selectedNodeId: null });
    if (!view) return null;
    return (
      <MediaNode
        key={node.id}
        id={view.id}
        kind="image"
        src={view.src}
        filename={view.filename ?? node.title}
        status={view.status}
        selected={isSelected}
        x={view.x}
        y={view.y}
        width={view.width}
        height={view.height}
        onSelect={onSelect}
      />
    );
  }

  if (node.type === "audio") {
    const view = toMediaNodeView(node, { selectedNodeId: null });
    if (!view) return null;
    return (
      <MediaNode
        key={node.id}
        id={view.id}
        kind="audio"
        filename={view.filename ?? node.title}
        duration={view.duration}
        status={view.status}
        selected={isSelected}
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
      const cands = readCandidates(node);
      const candidatesReady = cands.filter((c) => c.status === "completed").length;
      const candidatesTotal = cands.length;
      return (
        <DraftNode
          key={node.id}
          id={node.id}
          status="draft"
          intent={intent}
          modelLabel={modelLabel}
          configSummary={configSummary}
          refs={refs}
          candidatesReady={candidatesTotal > 0 ? candidatesReady : undefined}
          candidatesTotal={candidatesTotal > 0 ? candidatesTotal : undefined}
          selected={isSelected}
          x={node.x}
          y={node.y}
          onSelect={onSelect}
        />
      );
    }
    const view = toMediaNodeView(node, { selectedNodeId: null });
    const candidateCount = readCandidates(node).length;
    // Empty-video fallback: a video node without media (no `media_urls`,
    // no candidates), and not recognized as a draft, would otherwise render
    // as a giant black MediaNode. Show a clearer placeholder card.
    if (!view?.src && candidateCount === 0) {
      return (
        <div
          key={node.id}
          className={`absolute w-[240px] rounded-md border bg-elevated/85 backdrop-blur-md ${
            isSelected ? "ring-2 ring-primary border-primary/50" : "border-glass-border"
          }`}
          style={{ transform: `translate(${node.x}px, ${node.y}px)` }}
          role="button"
          tabIndex={0}
          onPointerDown={(e) => {
            e.stopPropagation();
            onSelect();
          }}
        >
          <div className="px-3 py-3">
            <div className="mb-1 flex items-center gap-1.5 text-[12px] font-semibold text-foreground">
              <Play size={11} className="text-primary" />
              <span className="truncate">{node.title || "Video Node"}</span>
            </div>
            <div className="text-[11px] text-text-muted leading-relaxed">
              No media yet. Use the Composer below to generate, or attach a reference image.
            </div>
          </div>
        </div>
      );
    }
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
          // Clamp legacy nodes' bloated default 420x560 to v0.3 sizes.
          width={view.width ? Math.min(view.width, 240) : undefined}
          height={view.height ? Math.min(view.height, 136) : undefined}
          onSelect={onSelect}
        />
      );
    }
  }

  return null;
}

function renderCandidatesAsMediaNodes(
  node: AtelierNode,
  selectedIds: Set<string>,
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
    const params = (c.params ?? {}) as Record<string, unknown>;
    const progress =
      typeof params.progress === "number" ? params.progress : undefined;
    // Prefer backend-supplied ETA; fall back to a heuristic from
    // attempt_started_at + progress so the user gets *some* signal.
    const etaSeconds = (() => {
      const explicit = [
        params.eta_seconds,
        params.estimated_seconds_remaining,
        params.eta,
      ].find((v): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0);
      if (explicit !== undefined) return Math.round(explicit);
      if (
        typeof progress === "number" &&
        progress > 5 &&
        typeof c.attempt_started_at === "number" &&
        c.attempt_started_at > 0
      ) {
        const elapsedMs = Date.now() - c.attempt_started_at * 1000;
        if (elapsedMs > 0) {
          const eta = (elapsedMs / 1000) * (100 - progress) / progress;
          if (eta > 0 && eta < 600) return Math.round(eta);
        }
      }
      return undefined;
    })();
    return (
      <MediaNode
        key={candKey}
        id={candKey}
        kind="video"
        src={c.video_url ?? undefined}
        filename={c.label || c.id.slice(0, 8)}
        status={c.status}
        progress={progress}
        etaSeconds={etaSeconds}
        selectedAsTake={selectedTakeId === c.id}
        selected={selectedIds.has(candKey)}
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

  // Multi-selection layer. Store keeps a single primary `selectedNodeId`
  // (which anchors the action bar / composer / inspector); the shell
  // tracks *additional* secondary selections in this set. The union is the
  // visual selection users see. Plain click clears extras; Shift/Cmd-click
  // toggles a node into the extras (a "promote second" gesture); marquee
  // (Sprint 4 next commit) writes both primary + extras at once.
  const [extraSelectedIds, setExtraSelectedIds] = useState<Set<string>>(() => new Set());

  // Computed union — passed to renderNode/renderCandidatesAsMediaNodes so
  // every node in the multi-selection paints its primary ring.
  const allSelectedIds = useMemo(() => {
    const set = new Set(extraSelectedIds);
    if (selectedNodeId) set.add(selectedNodeId);
    return set;
  }, [extraSelectedIds, selectedNodeId]);
  const isMultiSelect = allSelectedIds.size > 1;
  const [minimapOpen, setMinimapOpen] = useState(false);
  const [agentCollapsed, setAgentCollapsed] = useState(false);
  const [previewVideoUrl, setPreviewVideoUrl] = useState<string | null>(null);
  const [editingIdeaId, setEditingIdeaId] = useState<string | null>(null);
  const [editingIdeaBody, setEditingIdeaBody] = useState("");

  // First-load auto-fit: once a project loads with at least one node, fit
  // them in viewport. One-shot per browser session — user is in control of
  // pan/zoom after the first paint.
  const didInitialFitRef = useRef(false);
  useEffect(() => {
    if (didInitialFitRef.current) return;
    if (!project || project.nodes.length === 0) return;
    if (!mainRef.current) return;
    didInitialFitRef.current = true;
    const t = window.setTimeout(() => handleFitView(), 50);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id]);

  // Delete every node in the multi-selection. Real nodes go through the
  // store's deleteAtelierNode; virtual candidate ids (parent::cand::cid)
  // route to deleteCandidate. Confirm once for the whole batch.
  const deleteSelection = async () => {
    const ids = Array.from(allSelectedIds);
    if (ids.length === 0) return;
    const ok = window.confirm(
      ids.length === 1 ? "Delete this node?" : `Delete ${ids.length} nodes?`,
    );
    if (!ok) return;
    const store = useAtelierStore.getState();
    let ok_count = 0;
    let fail_count = 0;
    for (const id of ids) {
      const parsed = parseCandidateNodeId(id);
      try {
        if (parsed) {
          await store.deleteCandidate(parsed.parentId, parsed.candidateId);
        } else {
          await deleteAtelierNode(id);
        }
        ok_count += 1;
      } catch {
        fail_count += 1;
      }
    }
    setExtraSelectedIds(new Set());
    if (fail_count === 0) {
      pushToast("info", `${ok_count} node${ok_count === 1 ? "" : "s"} deleted`);
    } else {
      pushToast("error", `${fail_count} of ${ids.length} deletes failed`);
    }
  };

  // Keyboard shortcuts (PRD §12.5): V / I / T / F / Esc / Delete / / (focus agent).
  // Skip when typing in an input/textarea or contenteditable.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      switch (e.key.toLowerCase()) {
        case "v":
          e.preventDefault();
          void handleCreateVideo();
          break;
        case "i":
          e.preventDefault();
          void createEmptyImageDraft().catch((err: unknown) => pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`));
          break;
        case "t":
          e.preventDefault();
          void createIdeaNode()
            .then((node) => {
              setEditingIdeaId(node.id);
              setEditingIdeaBody((node.data as { body?: string })?.body ?? "");
            })
            .catch((err: unknown) => pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`));
          break;
        case "f":
          e.preventDefault();
          handleFitView();
          break;
        case "/":
          e.preventDefault();
          setAgentCollapsed(false);
          // Best-effort: focus the Agent composer textarea if visible.
          requestAnimationFrame(() => {
            const ta = document.querySelector('[role="region"][aria-label="Atelier Agent"] textarea') as HTMLTextAreaElement | null;
            ta?.focus();
          });
          break;
        case "escape":
          if (selectedNodeId || extraSelectedIds.size > 0) {
            e.preventDefault();
            selectNode(null);
            if (extraSelectedIds.size > 0) setExtraSelectedIds(new Set());
          }
          break;
        case "delete":
        case "backspace":
          if (allSelectedIds.size === 0) break;
          if ((e.target as HTMLElement)?.tagName !== "BODY") return;
          e.preventDefault();
          void deleteSelection();
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, project?.nodes]);

  // Auto-enter idea editing whenever an idea node becomes selected — saves
  // a click. The textarea overlay (rendered later in JSX) takes focus.
  useEffect(() => {
    if (!project || !selectedNodeId) {
      // Selection cleared — close any open editor (and persist on close).
      if (editingIdeaId) setEditingIdeaId(null);
      return;
    }
    const sel = project.nodes.find((n) => n.id === selectedNodeId);
    if (!sel || sel.type !== "idea") {
      if (editingIdeaId && editingIdeaId !== selectedNodeId) setEditingIdeaId(null);
      return;
    }
    if (editingIdeaId !== sel.id) {
      setEditingIdeaId(sel.id);
      const body = (sel.data as { body?: unknown })?.body;
      setEditingIdeaBody(typeof body === "string" ? body : sel.prompt ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNodeId, project?.nodes]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageNodeIdForUploadRef = useRef<string | null>(null); // when set, the upload writes to this node
  const composerRefDraftIdRef = useRef<string | null>(null);   // when set, upload attaches to this draft as reference
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

  // Group drag — when the user starts dragging a node that's part of a
  // multi-selection (size > 1), every other real node in the selection moves
  // with the same delta. Virtual candidates are excluded; their positions
  // are derived from their parent.
  const groupDragRef = useRef<{
    members: Array<{ nodeId: string; startWorldX: number; startWorldY: number }>;
    startPointerX: number;
    startPointerY: number;
    moved: boolean;
  } | null>(null);

  // Marquee box-select. Activated by Shift + drag on empty canvas.
  // Tracks both screen-coord rect (for the visible overlay) and uses
  // pan/zoom to map back to world coords on release.
  const marqueeDragRef = useRef<{
    startScreenX: number;
    startScreenY: number;
    currentScreenX: number;
    currentScreenY: number;
  } | null>(null);
  const [marqueeTick, setMarqueeTick] = useState(0);

  // Connect drag (image → draft attach-as-reference). Live updates via tick
  // since refs don't trigger re-render — we want the dashed bezier overlay
  // to follow the cursor and the hovered target ring to repaint.
  const connectDragRef = useRef<{
    sourceNodeId: string;
    startScreenX: number;
    startScreenY: number;
    currentScreenX: number;
    currentScreenY: number;
  } | null>(null);
  const [connectDragTick, setConnectDragTick] = useState(0);
  const [hoveredConnectTargetId, setHoveredConnectTargetId] = useState<string | null>(null);

  const zoomFactor = zoom / 100;

  // Helper: translate a screen-space delta to world-space (just divide by zoom).
  const screenDeltaToWorld = (dx: number, dy: number) => ({ x: dx / zoomFactor, y: dy / zoomFactor });

  // ── Pan + zoom handlers ────────────────────────────────────────────────────
  const handleMainPointerDown = (event: React.PointerEvent) => {
    // Start pan only when clicking the canvas background (not a node / overlay).
    if (event.target !== event.currentTarget && (event.target as HTMLElement).closest('[data-atelier-node],[role="dialog"],[role="toolbar"],[role="region"]')) return;
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);

    // Shift + drag on empty canvas = marquee box-select. Don't clear the
    // existing selection until the user actually drags (pure shift-click on
    // empty is a no-op, not a deselect).
    if (event.shiftKey) {
      marqueeDragRef.current = {
        startScreenX: event.clientX,
        startScreenY: event.clientY,
        currentScreenX: event.clientX,
        currentScreenY: event.clientY,
      };
      setMarqueeTick((v) => v + 1);
      return;
    }

    // Plain empty-canvas click clears any selection.
    selectNode(null);
    if (extraSelectedIds.size > 0) setExtraSelectedIds(new Set());
    panDragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      startPanX: panX,
      startPanY: panY,
    };
  };

  const handleMainPointerMove = (event: React.PointerEvent) => {
    // Marquee — update rect; don't pan or drag.
    if (marqueeDragRef.current) {
      marqueeDragRef.current.currentScreenX = event.clientX;
      marqueeDragRef.current.currentScreenY = event.clientY;
      setMarqueeTick((v) => v + 1);
      return;
    }
    // Pan
    if (panDragRef.current) {
      const dx = event.clientX - panDragRef.current.startX;
      const dy = event.clientY - panDragRef.current.startY;
      setPanX(panDragRef.current.startPanX + dx);
      setPanY(panDragRef.current.startPanY + dy);
      return;
    }
    // Group drag (multi-selection)
    if (groupDragRef.current) {
      const dx = event.clientX - groupDragRef.current.startPointerX;
      const dy = event.clientY - groupDragRef.current.startPointerY;
      if (Math.abs(dx) + Math.abs(dy) > 3) groupDragRef.current.moved = true;
      const wd = screenDeltaToWorld(dx, dy);
      const store = useAtelierStore.getState();
      for (const m of groupDragRef.current.members) {
        store.moveNodeLocal(m.nodeId, Math.round(m.startWorldX + wd.x), Math.round(m.startWorldY + wd.y));
      }
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
    if (marqueeDragRef.current) {
      const m = marqueeDragRef.current;
      marqueeDragRef.current = null;
      // Map screen-coord rect back to world coords.
      const x1 = Math.min(m.startScreenX, m.currentScreenX);
      const x2 = Math.max(m.startScreenX, m.currentScreenX);
      const y1 = Math.min(m.startScreenY, m.currentScreenY);
      const y2 = Math.max(m.startScreenY, m.currentScreenY);
      // Ignore dribbles — treat as a click (do nothing).
      if (x2 - x1 < 4 && y2 - y1 < 4) {
        setMarqueeTick((v) => v + 1);
        return;
      }
      const rect = mainRef.current?.getBoundingClientRect();
      const offX = rect?.left ?? 0;
      const offY = rect?.top ?? 0;
      const wx1 = (x1 - offX - panX) / zoomFactor;
      const wy1 = (y1 - offY - panY) / zoomFactor;
      const wx2 = (x2 - offX - panX) / zoomFactor;
      const wy2 = (y2 - offY - panY) / zoomFactor;
      // Hit-test real nodes (use bbox intersection, not strict containment,
      // which feels more permissive and matches Figma).
      const proj = useAtelierStore.getState().currentProject;
      const hits: string[] = [];
      if (proj) {
        for (const n of proj.nodes) {
          const nx2 = n.x + (n.width || 240);
          const ny2 = n.y + (n.height || 110);
          const intersects = !(nx2 < wx1 || n.x > wx2 || ny2 < wy1 || n.y > wy2);
          if (intersects) hits.push(n.id);
        }
      }
      if (hits.length === 0) {
        // Empty drag — clear selection like a plain canvas click would.
        selectNode(null);
        setExtraSelectedIds(new Set());
        setMarqueeTick((v) => v + 1);
        return;
      }
      const [first, ...rest] = hits;
      selectNode(first);
      setExtraSelectedIds(new Set(rest));
      setMarqueeTick((v) => v + 1);
      return;
    }
    if (panDragRef.current) {
      panDragRef.current = null;
      return;
    }
    if (groupDragRef.current) {
      const drag = groupDragRef.current;
      groupDragRef.current = null;
      if (drag.moved) {
        const store = useAtelierStore.getState();
        const proj = store.currentProject;
        for (const m of drag.members) {
          const real = proj?.nodes.find((n) => n.id === m.nodeId);
          if (real) {
            void store.commitNodePosition(m.nodeId, real.x, real.y).catch(() => {});
          }
        }
      }
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

  // Drag-to-connect: from a selected image's right-edge handle, draw a
  // dashed bezier following the cursor. Drop on a draft (orange) video node
  // to attach the image as a reference. Cancel drops elsewhere.
  // Uses window-level pointermove/up so the gesture survives leaving the
  // canvas bounds and doesn't fight main's pan handlers.
  const handleConnectHandlePointerDown = (
    event: React.PointerEvent,
    sourceNodeId: string,
    handleScreenX: number,
    handleScreenY: number,
  ) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.preventDefault();
    connectDragRef.current = {
      sourceNodeId,
      startScreenX: handleScreenX,
      startScreenY: handleScreenY,
      currentScreenX: event.clientX,
      currentScreenY: event.clientY,
    };
    setConnectDragTick((v) => v + 1);

    const onMove = (ev: PointerEvent) => {
      if (!connectDragRef.current) return;
      connectDragRef.current.currentScreenX = ev.clientX;
      connectDragRef.current.currentScreenY = ev.clientY;
      const el = document.elementFromPoint(ev.clientX, ev.clientY) as HTMLElement | null;
      const nodeEl = el?.closest("[data-atelier-node]") as HTMLElement | null;
      const targetId = nodeEl?.dataset.atelierNode ?? null;
      const valid = (() => {
        if (!targetId || targetId === sourceNodeId) return null;
        const target = useAtelierStore.getState().currentProject?.nodes.find((n) => n.id === targetId);
        if (!target || !isDraftVideo(target)) return null;
        return target.id;
      })();
      setHoveredConnectTargetId(valid);
      setConnectDragTick((v) => v + 1);
    };

    const onUp = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      const drag = connectDragRef.current;
      connectDragRef.current = null;
      setHoveredConnectTargetId(null);
      setConnectDragTick((v) => v + 1);
      if (!drag) return;
      const el = document.elementFromPoint(ev.clientX, ev.clientY) as HTMLElement | null;
      const nodeEl = el?.closest("[data-atelier-node]") as HTMLElement | null;
      const targetId = nodeEl?.dataset.atelierNode ?? null;
      if (!targetId || targetId === drag.sourceNodeId) return;
      const target = useAtelierStore.getState().currentProject?.nodes.find((n) => n.id === targetId);
      if (!target || !isDraftVideo(target)) {
        pushToast("info", "Drop on a draft video node to attach as reference.");
        return;
      }
      void useAtelierStore.getState()
        .attachReferenceNode(target.id, drag.sourceNodeId)
        .then(() => pushToast("success", "Reference attached"))
        .catch((err: unknown) => pushToast("error", `Attach failed: ${err instanceof Error ? err.message : String(err)}`));
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const handleNodePointerDown = (event: React.PointerEvent, node: AtelierNode) => {
    if (event.button !== 0) return;

    // Multi-select: Shift / Cmd / Ctrl click toggles the node into the extras
    // set *without* changing the primary. We consume the event in the
    // capture phase so the leaf's own onPointerDown — which would call
    // select(id) and reset the primary — never fires.
    if (event.shiftKey || event.metaKey || event.ctrlKey) {
      event.preventDefault();
      event.stopPropagation();
      // Virtual candidates aren't first-class nodes; allow them in extras
      // for now (a future commit can decide whether to filter them out
      // from group ops).
      setExtraSelectedIds((prev) => {
        const next = new Set(prev);
        if (node.id === selectedNodeId) {
          // Clicking the current primary with a modifier: demote it.
          // Promote first extra to primary, or clear entirely if no extras.
          const survivor = next.values().next().value;
          if (survivor !== undefined) {
            next.delete(survivor);
            window.setTimeout(() => selectNode(survivor), 0);
          } else {
            window.setTimeout(() => selectNode(null), 0);
          }
        } else if (next.has(node.id)) {
          next.delete(node.id);
        } else {
          next.add(node.id);
        }
        return next;
      });
      return;
    }

    // Don't initiate drag on textareas / inputs / buttons inside the node.
    const tag = (event.target as HTMLElement).tagName;
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "BUTTON") return;

    // Plain click on a node that's already in a multi-selection (size > 1):
    // preserve the selection and start a *group* drag. If the clicked node
    // wasn't the primary, promote it to primary (and demote the old primary
    // into extras) so the action bar would anchor here on click-release.
    if (allSelectedIds.size > 1 && allSelectedIds.has(node.id)) {
      event.preventDefault();
      event.stopPropagation();
      if (node.id !== selectedNodeId && selectedNodeId) {
        const oldPrimary = selectedNodeId;
        setExtraSelectedIds((prev) => {
          const next = new Set(prev);
          next.delete(node.id);
          next.add(oldPrimary);
          return next;
        });
        selectNode(node.id);
      }
      const proj = useAtelierStore.getState().currentProject;
      const members: Array<{ nodeId: string; startWorldX: number; startWorldY: number }> = [];
      for (const id of Array.from(allSelectedIds)) {
        if (parseCandidateNodeId(id)) continue; // skip virtual candidates
        const n = proj?.nodes.find((x) => x.id === id);
        if (!n) continue;
        members.push({ nodeId: n.id, startWorldX: n.x, startWorldY: n.y });
      }
      if (members.length === 0) return;
      groupDragRef.current = {
        members,
        startPointerX: event.clientX,
        startPointerY: event.clientY,
        moved: false,
      };
      return;
    }

    // Plain click on something else: drop extras for a fresh single-selection.
    if (extraSelectedIds.size > 0) setExtraSelectedIds(new Set());

    // Don't drag virtual candidates (they're derived); selection still fires.
    if (parseCandidateNodeId(node.id)) return;
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
      // Re-generate on a take = "use this take as the starting point for a
      // new iteration". Prefill the parent draft's Composer with the take's
      // prompt + model, then select the parent so the Composer pops up.
      // User tweaks → submit creates new candidates. (Plain retry of an
      // identical candidate is rarely what the user actually wants — the
      // floating Composer is the iteration affordance.)
      const parsed = parseCandidateNodeId(node.id);
      if (parsed) {
        const proj = store.currentProject;
        const parent = proj?.nodes.find((n) => n.id === parsed.parentId);
        const cand = parent
          ? readCandidates(parent).find((c) => c.id === parsed.candidateId)
          : undefined;
        if (!parent || !cand) return;
        void store.updateNode(parent.id, {
          data: {
            ...(parent.data ?? {}),
            prompt: cand.prompt,
            model: cand.model,
          },
        }).catch(() => {});
        selectNode(parent.id);
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
      // Open the per-action picker that lets the user choose which draft
      // to attach this image as a reference to. (Drag-to-attach is a v1.1
      // affordance; this dropdown is the v1 baseline.)
      if (node.type !== "image") {
        pushToast("info", "Use-as-reference is for image nodes.");
        return;
      }
      if (!node.media_urls || node.media_urls.length === 0) {
        pushToast("info", "Upload the image first, then attach it as reference.");
        return;
      }
      setUseAsRefSourceId(node.id);
      return;
    }
  };

  // Track which image node is choosing a target for "Use as reference".
  const [useAsRefSourceId, setUseAsRefSourceId] = useState<string | null>(null);

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
    const composerDraftId = composerRefDraftIdRef.current;
    composerRefDraftIdRef.current = null;
    if (composerDraftId) {
      // Upload as new reference attached to this draft. uploadReferenceImage
      // creates an image-node sibling and patches the draft's
      // reference_image_urls + reference_node_ids.
      void useAtelierStore.getState()
        .uploadReferenceImage(composerDraftId, file)
        .then(() => pushToast("success", `Reference "${file.name}" attached`))
        .catch((err: unknown) => pushToast("error", `Upload failed: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
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
      const proj = await ensureProject();
      const before = proj.nodes.length;
      const variant = draftVariations[before % draftVariations.length];
      const rect = mainRef.current?.getBoundingClientRect();
      // Offset consecutive creates by up to 6×28 px so repeated "New Video"
      // clicks don't stack on the same pixel.
      const jitter = (before % 6) * 28;
      const cx = rect ? (rect.width / 2 - panX) / zoomFactor - 120 + jitter : 200;
      const cy = rect ? (rect.height / 2 - panY) / zoomFactor - 55 + jitter : 200;
      // Single-shot create: full draft fields in one POST. Avoids the brief
      // black-MediaNode flicker (or terminal stuck-state if the second PATCH
      // fails) of the two-step pattern. Render path requires
      // status="draft" + data.intent (string) for DraftNode to win.
      const node = await api.createAtelierNode(proj.id, {
        type: "video",
        title: `Video Node ${before + 1}`,
        prompt: "",
        status: "draft",
        x: Math.round(cx),
        y: Math.round(cy),
        width: 240,
        height: 110,
        data: {
          intent: variant.intent,
          model: variant.model,
          config_summary: "1280×720 · 5s · 4×",
          reference_image_urls: [],
          candidates: [],
        },
      });
      await refreshCurrentProject();
      selectNode(node.id);
    } catch (err: unknown) {
      pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // Drag-and-drop image files onto the canvas → quick reference upload.
  // Drop on a draft → attach as ref to that draft (future: needs hit-test).
  // For v1: every drop creates a new image node at drop position.
  const [isDraggingFileOver, setIsDraggingFileOver] = useState(false);
  const handleDragOver = (event: React.DragEvent) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    if (!isDraggingFileOver) setIsDraggingFileOver(true);
  };
  const handleDragLeave = (event: React.DragEvent) => {
    if (event.target === event.currentTarget) setIsDraggingFileOver(false);
  };
  const handleDrop = async (event: React.DragEvent) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    setIsDraggingFileOver(false);
    const files = Array.from(event.dataTransfer.files).filter((f) => f.type.startsWith("image/"));
    if (files.length === 0) {
      pushToast("info", "Only image files can be dropped onto the canvas.");
      return;
    }
    for (const file of files) {
      try {
        await createImageNode(file);
        pushToast("success", `Added "${file.name}" as reference`);
      } catch (err: unknown) {
        pushToast("error", `Upload failed: ${err instanceof Error ? err.message : String(err)}`);
      }
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
        onUndo={() => {}}
        onRedo={() => {}}
        // Honest disabled state — better than fake-clickable buttons that
        // toast "not implemented" each time. Reads "Coming soon" via tooltip.
        canUndo={false}
        canRedo={false}
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
        className={`absolute inset-0 cursor-default select-none ${isDraggingFileOver ? "ring-4 ring-inset ring-primary/40" : ""}`}
        style={{ cursor: panDragRef.current ? "grabbing" : "default", touchAction: "none" }}
        onPointerDown={handleMainPointerDown}
        onPointerMove={handleMainPointerMove}
        onPointerUp={handleMainPointerUp}
        onPointerCancel={() => {
          panDragRef.current = null;
          nodeDragRef.current = null;
        }}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
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

          {/* nodes — each wrapped in a drag-aware div. Use *Capture* phase
              because v3 leaf nodes stopPropagation in their own onPointerDown
              (Wave A polish), so a normal bubble-phase parent handler never
              fires. Capture lets us start the drag tracking before the child
              consumes the event; the child still selects the node on click. */}
          {project?.nodes.map((node) => {
            const isSelected = allSelectedIds.has(node.id);
            return (
              <div
                key={node.id}
                data-atelier-node={node.id}
                onPointerDownCapture={(e) => handleNodePointerDown(e, node)}
                className={`group/node ${isSelected ? "" : "cursor-pointer"}`}
                style={{ touchAction: "none", cursor: nodeDragRef.current?.nodeId === node.id ? "grabbing" : undefined }}
              >
                {renderNode(node, allSelectedIds, selectNode)}
              </div>
            );
          })}

          {/* virtual candidate media nodes (no drag — derived) */}
          {project?.nodes.flatMap((node) =>
            renderCandidatesAsMediaNodes(node, allSelectedIds, selectNode),
          )}

          {/* Connect-drag target highlight: while dragging from an image's
              connect handle, glow the draft under the cursor (in world coords
              so the ring scales with zoom). */}
          {connectDragRef.current && hoveredConnectTargetId ? (() => {
            const target = project?.nodes.find((n) => n.id === hoveredConnectTargetId);
            if (!target) return null;
            return (
              <div
                key={`connect-target-ring-${connectDragTick}`}
                className="pointer-events-none absolute z-[36] rounded-md ring-2 ring-primary"
                style={{
                  left: target.x - 4,
                  top: target.y - 4,
                  width: (target.width || 240) + 8,
                  height: (target.height || 110) + 8,
                }}
              />
            );
          })() : null}

          {/* selection action bar + composer moved OUT of world (screen coords)
              so they stay readable at any zoom — see below. */}

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

      {/* SelectionActionBar — rendered OUTSIDE the world transform so it
          stays a fixed screen size while still anchored above the selected
          node. Coordinates converted: screen = pan + world * zoom.
          Hidden in multi-select mode; the multi-select chip takes over. */}
      {selectedNode && !isMultiSelect ? (
        <div className="pointer-events-none absolute inset-0 z-30">
          <div className="pointer-events-auto absolute" style={{ left: 0, top: 0 }}>
            <SelectionActionBar
              kind={selectionKindOf(selectedNode)}
              x={panX + selectedNode.x * zoomFactor}
              y={panY + selectedNode.y * zoomFactor}
              width={(selectedNode.width || 240) * zoomFactor}
              onAct={(action) => handleActionBar(action, selectedNode)}
            />
          </div>
        </div>
      ) : null}

      {/* Multi-select chip: shows count + group Delete + Clear. Centered
          above the bounding box of the selection (in screen coords). */}
      {isMultiSelect && project ? (() => {
        // Compute bounding rect of all selected nodes (real + virtual).
        let minX = Infinity, minY = Infinity, maxX = -Infinity;
        for (const id of Array.from(allSelectedIds)) {
          const parsed = parseCandidateNodeId(id);
          if (parsed) {
            const parent = project.nodes.find((n) => n.id === parsed.parentId);
            if (!parent) continue;
            const idx = readCandidates(parent).findIndex((c) => c.id === parsed.candidateId);
            if (idx < 0) continue;
            const { x, y } = candidatePosition(parent, idx);
            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            maxX = Math.max(maxX, x + CAND_WIDTH);
          } else {
            const n = project.nodes.find((nn) => nn.id === id);
            if (!n) continue;
            minX = Math.min(minX, n.x);
            minY = Math.min(minY, n.y);
            maxX = Math.max(maxX, n.x + (n.width || 240));
          }
        }
        if (!Number.isFinite(minX)) return null;
        const screenCx = panX + ((minX + maxX) / 2) * zoomFactor;
        const screenY = panY + minY * zoomFactor - 38;
        return (
          <div
            role="toolbar"
            aria-label={`${allSelectedIds.size} nodes selected`}
            className="pointer-events-auto absolute z-30 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-glass-border bg-elevated px-2.5 py-1 text-[12px] shadow-2xl shadow-black/40 backdrop-blur-md"
            style={{ left: screenCx, top: Math.max(8, screenY) }}
          >
            <span className="font-mono text-text-secondary">{allSelectedIds.size} selected</span>
            <button
              type="button"
              onClick={() => void deleteSelection()}
              className="rounded-full bg-red-400/15 px-2 py-0.5 font-medium text-red-200 hover:bg-red-400/25"
              aria-label="Delete selected nodes"
            >
              Delete
            </button>
            <button
              type="button"
              onClick={() => {
                setExtraSelectedIds(new Set());
                selectNode(null);
              }}
              className="rounded-full px-2 py-0.5 text-text-muted hover:bg-hover-bg hover:text-foreground"
              aria-label="Clear selection"
            >
              Clear
            </button>
          </div>
        );
      })() : null}

      {/* Connect handle: appears on right-middle of a selected image node
          that has media. Drag onto a draft to attach as reference. Sits in
          screen coords so it stays a fixed 16px button at any zoom. */}
      {selectedNode && selectedNode.type === "image" && (selectedNode.media_urls?.length ?? 0) > 0 ? (() => {
        const handleScreenX = panX + (selectedNode.x + (selectedNode.width || 180)) * zoomFactor;
        const handleScreenY = panY + (selectedNode.y + (selectedNode.height || 180) / 2) * zoomFactor;
        return (
          <button
            type="button"
            aria-label="Drag to attach this image as a reference to a draft"
            data-tip="Drag to a draft to attach as reference"
            onPointerDown={(e) => handleConnectHandlePointerDown(e, selectedNode.id, handleScreenX, handleScreenY)}
            className="btn-tip absolute z-40 grid h-4 w-4 -translate-x-1/2 -translate-y-1/2 cursor-grab place-items-center rounded-full border border-white/40 bg-primary text-white shadow-[0_0_0_3px_rgba(100,108,255,0.18)] hover:scale-110 active:cursor-grabbing"
            style={{ left: handleScreenX, top: handleScreenY }}
          >
            <Link2 size={9} aria-hidden="true" />
          </button>
        );
      })() : null}

      {/* Marquee box-select overlay (screen coords). */}
      {marqueeDragRef.current ? (() => {
        const m = marqueeDragRef.current!;
        const x = Math.min(m.startScreenX, m.currentScreenX);
        const y = Math.min(m.startScreenY, m.currentScreenY);
        const w = Math.abs(m.currentScreenX - m.startScreenX);
        const h = Math.abs(m.currentScreenY - m.startScreenY);
        return (
          <div
            key={`marquee-${marqueeTick}`}
            aria-hidden="true"
            className="pointer-events-none fixed z-[44] rounded-sm border border-primary/70 bg-primary/[0.08]"
            style={{ left: x, top: y, width: w, height: h }}
          />
        );
      })() : null}

      {/* Connect-drag bezier overlay (screen coords). Dashed primary line
          from the handle origin to the cursor. */}
      {connectDragRef.current ? (() => {
        const drag = connectDragRef.current!;
        const x1 = drag.startScreenX;
        const y1 = drag.startScreenY;
        const x2 = drag.currentScreenX;
        const y2 = drag.currentScreenY;
        const dx = Math.max(40, Math.abs(x2 - x1) * 0.4);
        const isOverTarget = !!hoveredConnectTargetId;
        return (
          <svg
            key={`connect-svg-${connectDragTick}`}
            aria-hidden="true"
            className="pointer-events-none fixed inset-0 z-[45]"
            width="100%"
            height="100%"
          >
            <path
              d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
              fill="none"
              stroke={isOverTarget ? "rgba(100,108,255,0.95)" : "rgba(100,108,255,0.7)"}
              strokeWidth={isOverTarget ? 2.5 : 2}
              strokeDasharray="6 4"
              strokeLinecap="round"
            />
            <circle cx={x2} cy={y2} r={isOverTarget ? 5 : 3.5} fill="rgba(100,108,255,0.95)" />
          </svg>
        );
      })() : null}

      {/* Composer — also OUTSIDE world, anchored to selected draft via
          screen-coord anchor + viewport. Hidden in multi-select. */}
      {composerAnchor && selectedNode && !isMultiSelect ? (
        <div className="pointer-events-none absolute inset-0 z-30">
          <div className="pointer-events-auto">
            <Composer
              anchor={{
                x: panX + composerAnchor.x * zoomFactor,
                y: panY + composerAnchor.y * zoomFactor,
                width: composerAnchor.width * zoomFactor,
                height: composerAnchor.height * zoomFactor,
              }}
              viewport={viewport}
              prompt={
                isDraftVideo(selectedNode)
                  ? readString(selectedNode.data?.prompt) ?? selectedNode.prompt ?? ""
                  : selectedNode.prompt ?? ""
              }
              modelLabel={readString(selectedNode.data?.model) ?? "Wan 2.7"}
              refs={
                readStringArray(
                  (selectedNode.data as { reference_image_urls?: unknown })?.reference_image_urls,
                ).map((src) => ({ src: getAssetUrl(src), role: "ref" }))
              }
              onClose={() => selectNode(null)}
              onSubmit={(payload) => handleComposerSubmit(payload, selectedNode)}
              onAddRef={() => {
                imageNodeIdForUploadRef.current = null;
                composerRefDraftIdRef.current = selectedNode.id;
                fileInputRef.current?.click();
              }}
              onRemoveRef={(idx) => {
                const refs = readStringArray(
                  (selectedNode.data as { reference_image_urls?: unknown })?.reference_image_urls,
                );
                const removed = refs[idx];
                if (!removed) return;
                void useAtelierStore.getState()
                  .detachReferenceNode(selectedNode.id, removed)
                  .then(() => pushToast("info", "Reference removed"))
                  .catch((err: unknown) => pushToast("error", `Remove failed: ${err instanceof Error ? err.message : String(err)}`));
              }}
              onAdvanced={() => pushToast("info", "Advanced params (seed / guidance / motion) coming next.")}
            />
          </div>
        </div>
      ) : null}

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
              <button
                key={`${entry.parentId}-${entry.candidateId}`}
                type="button"
                onClick={() => cand.video_url && setPreviewVideoUrl(cand.video_url)}
                className="group relative h-[68px] w-[140px] shrink-0 overflow-hidden rounded-md border border-glass-border bg-elevated/80 transition-shadow hover:border-primary/50 hover:shadow-[0_0_0_1px_rgba(100,108,255,0.18)]"
                aria-label={`Play ${parent.title}, clip ${i + 1}`}
              >
                {cand.video_url ? (
                  <video
                    src={getAssetUrl(cand.video_url)}
                    muted
                    playsInline
                    preload="metadata"
                    aria-label={`${parent.title} thumbnail`}
                    className="h-full w-full object-cover"
                  />
                ) : null}
                <span className="pointer-events-none absolute inset-0 m-auto grid h-7 w-7 place-items-center rounded-full bg-black/55 text-white/95 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100">
                  <Play size={12} />
                </span>
                <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-black/60 px-1.5 py-1 backdrop-blur-sm">
                  <span className="truncate text-[10px] text-foreground">{parent.title}</span>
                  <span className="font-mono text-[9px] text-text-muted">#{i + 1}</span>
                </div>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSequence((prev) => prev.filter((s) => !(s.parentId === entry.parentId && s.candidateId === entry.candidateId)));
                  }}
                  className="absolute right-1 top-1 rounded bg-black/55 p-0.5 text-white/80 opacity-0 hover:bg-red-500/70 group-hover:opacity-100"
                  aria-label={`Remove ${parent.title} from sequence`}
                >
                  <X size={10} />
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Use-as-reference picker modal */}
      {useAsRefSourceId ? (() => {
        const source = project?.nodes.find((n) => n.id === useAsRefSourceId);
        const drafts = (project?.nodes ?? []).filter((n) => n.type === "video" && n.status === "draft");
        if (!source) {
          // Source vanished — close.
          setTimeout(() => setUseAsRefSourceId(null), 0);
          return null;
        }
        return (
          <div
            className="fixed inset-0 z-50 grid place-items-center bg-black/70 backdrop-blur-sm"
            onClick={() => setUseAsRefSourceId(null)}
            role="dialog"
            aria-label="Pick a target draft to attach this reference"
          >
            <div
              className="w-[420px] rounded-xl border border-glass-border bg-elevated p-3 shadow-2xl shadow-black/40"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-2 flex items-center justify-between">
                <div className="font-display text-sm font-semibold text-foreground">Use as reference</div>
                <button onClick={() => setUseAsRefSourceId(null)} className="rounded p-1 text-text-muted hover:bg-hover-bg hover:text-foreground" aria-label="Close">
                  <X size={14} />
                </button>
              </div>
              <div className="mb-3 flex items-center gap-2 rounded-md border border-border-subtle bg-glass p-2">
                {source.media_urls?.[0] ? (
                  <img src={getAssetUrl(source.media_urls[0])} alt="" className="h-10 w-10 rounded object-cover" />
                ) : null}
                <div className="text-[12px] text-text-secondary truncate">
                  Attach <span className="text-foreground font-medium">{source.title || "this image"}</span> to a draft as reference
                </div>
              </div>
              {drafts.length === 0 ? (
                <div className="rounded-md border border-dashed border-glass-border bg-glass p-4 text-center text-[12px] text-text-muted">
                  No draft video nodes yet. Create one (press <span className="font-mono text-text-secondary">V</span>) and try again.
                </div>
              ) : (
                <ul className="max-h-[280px] space-y-1 overflow-y-auto">
                  {drafts.map((d) => {
                    const intent = (d.data as { intent?: string })?.intent ?? d.title ?? "Untitled draft";
                    const model = (d.data as { model?: string })?.model ?? "";
                    return (
                      <li key={d.id}>
                        <button
                          onClick={() => {
                            setUseAsRefSourceId(null);
                            void useAtelierStore.getState().attachReferenceNode(d.id, source.id)
                              .then(() => pushToast("success", `Attached to ${intent}`))
                              .catch((err: unknown) => pushToast("error", `Attach failed: ${err instanceof Error ? err.message : String(err)}`));
                          }}
                          className="flex w-full items-center justify-between gap-2 rounded-md border border-glass-border bg-glass px-3 py-2 text-left hover:bg-hover-bg"
                        >
                          <div className="min-w-0">
                            <div className="truncate text-[13px] font-medium text-foreground">{intent}</div>
                            <div className="font-mono text-[10px] text-text-muted">{model}</div>
                          </div>
                          <span className="rounded bg-primary/15 px-2 py-0.5 text-[11px] font-semibold text-primary">attach</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        );
      })() : null}

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
        <div className="fixed left-1/2 top-4 z-[60] flex -translate-x-1/2 flex-col gap-2">
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
