"use client";
import axios from "axios";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAtelierStore } from "@/store/atelierStore";
import { buildReferenceLinks } from "@/lib/atelierCanvas";
import { getAssetUrl } from "@/lib/utils";
import { Check, ChevronDown, CloudUpload, FolderOpen, Link2, Pencil, Play, Plus, Trash2, X } from "lucide-react";
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
  type AtelierNodePayload,
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
        onTitleCommit={(next) => {
          void useAtelierStore.getState().updateNode(node.id, {
            title: next,
            data: { ...(node.data ?? {}), title: next },
          }).catch(() => {/* save chip surfaces failures */});
        }}
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
          onIntentCommit={(next) => {
            void useAtelierStore.getState().updateNode(node.id, {
              data: { ...(node.data ?? {}), intent: next },
            }).catch(() => {/* save chip surfaces failures */});
          }}
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
  onRetry: (parentId: string, candidateId: string) => void,
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
        errorMessage={c.error ?? undefined}
        selectedAsTake={selectedTakeId === c.id}
        selected={selectedIds.has(candKey)}
        x={x}
        y={y}
        onSelect={() => select(candKey)}
        onRetry={c.status === "failed" ? () => onRetry(node.id, c.id) : undefined}
      />
    );
  });
}

interface EdgeLabel {
  key: string;
  midX: number;
  midY: number;
  text: string;
  tone: "neutral" | "primary" | "success" | "warning" | "error";
}

function renderEdges(
  project: AtelierProject | null,
  hoveredNodeId: string | null,
  labelsOut?: EdgeLabel[],
): React.ReactNode {
  if (!project) return null;
  const edges: React.ReactNode[] = [];
  // When the user hovers a node, edges *connected* to that node stay full
  // strength; everything else dims. Reads "relationship spotlight".
  const dimUnrelated = !!hoveredNodeId;
  const isRelated = (fromId: string, toId: string) =>
    !hoveredNodeId || fromId === hoveredNodeId || toId === hoveredNodeId;

  // Reference-image → video edges (muted, dotted).
  const refLinks = buildReferenceLinks(project.nodes);
  for (const link of refLinks) {
    const x1 = link.from.x + (link.from.width || 180);
    const y1 = link.from.y + (link.from.height || 180) / 2;
    const x2 = link.to.x;
    const y2 = link.to.y + (link.to.height || 110) / 2;
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.3);
    const related = isRelated(link.from.id, link.to.id);
    const opacity = dimUnrelated && !related ? 0.12 : 1;
    edges.push(
      <path
        key={`ref-${link.from.id}-${link.to.id}-${link.url.slice(-12)}`}
        d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
        fill="none"
        stroke="rgba(156,163,175,0.35)"
        strokeWidth={related && hoveredNodeId ? 2 : 1.5}
        strokeDasharray="2 4"
        style={{ opacity, transition: "opacity 180ms ease-out, stroke-width 180ms" }}
      />,
    );
    if (labelsOut && hoveredNodeId && related) {
      labelsOut.push({
        key: `ref-label-${link.from.id}-${link.to.id}-${link.url.slice(-12)}`,
        midX: (x1 + x2) / 2,
        midY: (y1 + y2) / 2,
        text: "ref",
        tone: "neutral",
      });
    }
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
      const inflight = c.status === "pending" || c.status === "processing";
      const failed = c.status === "failed";
      const stroke = failed
        ? "rgba(248,113,113,0.6)"   // red-400
        : "rgba(100,108,255,0.55)";  // primary
      const candKey = candidateNodeId(node.id, c.id);
      const related = isRelated(node.id, candKey);
      const opacity = dimUnrelated && !related ? 0.12 : 1;
      edges.push(
        <path
          key={`${node.id}-${c.id}`}
          d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
          fill="none"
          stroke={stroke}
          strokeWidth={related && hoveredNodeId ? 2 : 1.5}
          strokeDasharray={c.status === "completed" ? undefined : "6 4"}
          className={inflight ? "animate-atelier-dash motion-reduce:animate-none" : undefined}
          style={{ opacity, transition: "opacity 180ms ease-out, stroke-width 180ms" }}
        />,
      );
      if (labelsOut && hoveredNodeId && related) {
        const text = c.status === "completed"
          ? "take"
          : c.status === "failed"
          ? "failed"
          : c.status === "processing"
          ? "rendering"
          : "queued";
        const tone: EdgeLabel["tone"] = c.status === "completed"
          ? "success"
          : c.status === "failed"
          ? "error"
          : c.status === "processing"
          ? "primary"
          : "warning";
        labelsOut.push({
          key: `cand-label-${node.id}-${c.id}`,
          midX: (x1 + x2) / 2,
          midY: (y1 + y2) / 2,
          text,
          tone,
        });
      }
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
  const projects = useAtelierStore((s) => s.projects);
  const selectedNodeId = useAtelierStore((s) => s.selectedNodeId);
  const ensureProject = useAtelierStore((s) => s.ensureProject);
  const switchProject = useAtelierStore((s) => s.switchProject);
  const createProject = useAtelierStore((s) => s.createProject);
  const selectNode = useAtelierStore((s) => s.selectNode);
  const createImageNode = useAtelierStore((s) => s.createImageNode);
  const createIdeaNode = useAtelierStore((s) => s.createIdeaNode);
  const deleteAtelierNode = useAtelierStore((s) => s.deleteAtelierNode);
  const branchFromCandidate = useAtelierStore((s) => s.branchFromCandidate);
  const updateAgentPolicy = useAtelierStore((s) => s.updateAgentPolicy);
  const refreshCurrentProject = useAtelierStore((s) => s.refreshCurrentProject);

  const policy = project?.agent_policy;

  // Live save status — counts in-flight /atelier/* requests via axios
  // interceptors and tracks the timestamp of the last successful response
  // and the last failure. Surfaces in a small chip so the user knows
  // their work is persisted, and flips to a red error state when the
  // most recent round-trip didn't succeed.
  const [saveState, setSaveState] = useState<{
    inflight: number;
    savedAt: number | null;
    failedAt: number | null;
  }>({ inflight: 0, savedAt: null, failedAt: null });
  useEffect(() => {
    let inflight = 0;
    let savedAt: number | null = null;
    let failedAt: number | null = null;
    const isAtelier = (url: string | undefined) => !!url && url.includes("/atelier/");
    const reqId = axios.interceptors.request.use((cfg) => {
      if (isAtelier(cfg.url)) {
        inflight += 1;
        setSaveState({ inflight, savedAt, failedAt });
      }
      return cfg;
    });
    const resId = axios.interceptors.response.use(
      (res) => {
        if (isAtelier(res.config.url)) {
          inflight = Math.max(0, inflight - 1);
          // Treat reads (GET) and writes (POST/PUT/DELETE) the same — any
          // round-trip success is evidence the canvas state is in sync.
          savedAt = Date.now();
          // A success after a failure clears the error state — recovery is
          // implicit and we shouldn't keep nagging.
          failedAt = null;
          setSaveState({ inflight, savedAt, failedAt });
        }
        return res;
      },
      (err: unknown) => {
        const cfg = (err as { config?: { url?: string } } | null)?.config;
        if (isAtelier(cfg?.url)) {
          inflight = Math.max(0, inflight - 1);
          failedAt = Date.now();
          setSaveState({ inflight, savedAt, failedAt });
        }
        return Promise.reject(err);
      },
    );
    return () => {
      axios.interceptors.request.eject(reqId);
      axios.interceptors.response.eject(resId);
    };
  }, []);

  // Tick once a minute so the "saved 2 min ago" label stays fresh without
  // each save event tripping a re-render.
  const [, setSaveLabelTick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => setSaveLabelTick((v) => v + 1), 60_000);
    return () => window.clearInterval(t);
  }, []);

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

  // Adaptive polling for in-flight candidates. Cadence shifts based on
  // how recently the youngest pending/processing candidate started, so a
  // user who just hit Generate sees fast updates while a long-running
  // batch backs off and stops hammering the backend.
  //
  //   <  30s old  → 1500 ms  (perceived realtime)
  //   <  120s old → 3000 ms  (default)
  //   >= 120s old → 6000 ms  (deep generation)
  useEffect(() => {
    const inflight = (project?.nodes ?? []).flatMap((node) =>
      readCandidates(node).filter((c) => c.status === "pending" || c.status === "processing"),
    );
    if (inflight.length === 0) return;
    const nowSec = Date.now() / 1000;
    const youngestAgeSec = inflight.reduce((min, c) => {
      const start = c.attempt_started_at ?? c.created_at ?? nowSec;
      const age = nowSec - start;
      return age < min ? age : min;
    }, Number.POSITIVE_INFINITY);
    const interval = youngestAgeSec < 30 ? 1500 : youngestAgeSec < 120 ? 3000 : 6000;
    const t = window.setInterval(() => {
      void refreshCurrentProject().catch(() => {});
    }, interval);
    return () => window.clearInterval(t);
  }, [project, refreshCurrentProject]);

  // Local view state
  const [zoom, setZoom] = useState(100);          // percent, 25..300
  const [panX, setPanX] = useState(0);            // world translate x (px in CSS)
  const [panY, setPanY] = useState(0);            // world translate y

  // Hovered node id for the edge-spotlight effect — when set, only edges
  // touching this node stay full-strength; the rest fade.
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

  // Sequence drag-to-reorder: which clip index is currently being dragged
  // (source) and which index the drop indicator is shown at (target). Both
  // stored in shell because they only matter while a drag is in progress.
  const [seqDragFromIndex, setSeqDragFromIndex] = useState<number | null>(null);
  const [seqDragOverIndex, setSeqDragOverIndex] = useState<number | null>(null);

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
  // Preview modal state. Beyond the url, we carry the parent/candidate ids
  // when the source was a take so the modal can offer take-level actions
  // (select / branch / delete) inline. URL-only previews (Sequence Strip
  // clicks, generic media node Play button) skip the action row.
  const [preview, setPreview] = useState<{
    url: string;
    parentId?: string;
    candidateId?: string;
  } | null>(null);
  const setPreviewVideoUrl = (url: string | null) => setPreview(url ? { url } : null);

  // Preview modal arrow-key nav: ← / → step through completed takes from
  // the same parent. Wraps at edges (so the user can keep going). No-op
  // when the preview was opened without parent/candidate context (sequence
  // strip clicks, generic Play).
  useEffect(() => {
    if (!preview || !preview.parentId || !preview.candidateId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const proj = useAtelierStore.getState().currentProject;
      const parent = proj?.nodes.find((n) => n.id === preview.parentId);
      if (!parent) return;
      const cands = readCandidates(parent).filter((c) => c.video_url);
      if (cands.length < 2) return;
      e.preventDefault();
      const idx = cands.findIndex((c) => c.id === preview.candidateId);
      if (idx < 0) return;
      const next = e.key === "ArrowRight"
        ? cands[(idx + 1) % cands.length]
        : cands[(idx - 1 + cands.length) % cands.length];
      setPreview({ url: next.video_url!, parentId: parent.id, candidateId: next.id });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview]);
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

  // Snapshot the currently selected real nodes into the clipboard. Virtual
  // candidates (parent::cand::cid) are excluded because their lifecycle is
  // owned by their parent. Video drafts have their `candidates` stripped so
  // a paste starts a fresh iteration rather than carrying over takes.
  const copySelection = (): number => {
    const proj = useAtelierStore.getState().currentProject;
    if (!proj) return 0;
    const ids = Array.from(allSelectedIds).filter((id) => !parseCandidateNodeId(id));
    if (ids.length === 0) return 0;
    const entries: Array<{ payload: AtelierNodePayload; originX: number; originY: number }> = [];
    for (const id of ids) {
      const n = proj.nodes.find((x) => x.id === id);
      if (!n) continue;
      const data: Record<string, unknown> = { ...(n.data ?? {}) };
      if (n.type === "video") {
        // Don't carry takes into the copy — paste should be an empty draft
        // ready to iterate.
        delete (data as { candidates?: unknown }).candidates;
        delete (data as { selected_candidate_id?: unknown }).selected_candidate_id;
      }
      entries.push({
        payload: {
          type: n.type,
          title: n.title,
          prompt: n.prompt,
          // Reset draft-ish nodes to draft so paste is editable.
          status: n.type === "video" ? "draft" : n.status,
          width: n.width,
          height: n.height,
          source_project_id: n.source_project_id ?? null,
          frame_id: n.frame_id ?? null,
          asset_id: n.asset_id ?? null,
          video_task_id: n.video_task_id ?? null,
          media_urls: [...(n.media_urls ?? [])],
          data,
        },
        originX: n.x,
        originY: n.y,
      });
    }
    clipboardRef.current = entries;
    pasteOffsetRef.current = { x: 24, y: 24 };
    return entries.length;
  };

  // Paste the clipboard at originX/Y + offset, preserving relative spacing.
  // Each successive paste increments the offset so stacks don't overlap.
  const pasteClipboard = async (): Promise<number> => {
    const entries = clipboardRef.current;
    if (entries.length === 0) return 0;
    const proj = await ensureProject();
    const { x: offX, y: offY } = pasteOffsetRef.current;
    const created: AtelierNode[] = [];
    let failed = 0;
    for (const entry of entries) {
      try {
        const node = await api.createAtelierNode(proj.id, {
          ...entry.payload,
          x: Math.round(entry.originX + offX),
          y: Math.round(entry.originY + offY),
        });
        created.push(node);
      } catch {
        failed += 1;
      }
    }
    pasteOffsetRef.current = { x: offX + 24, y: offY + 24 };
    if (created.length > 0) {
      await refreshCurrentProject();
      const [first, ...rest] = created;
      selectNode(first.id);
      setExtraSelectedIds(new Set(rest.map((n) => n.id)));
    }
    if (failed > 0) {
      pushToast("error", `${failed} of ${entries.length} pastes failed`);
    } else if (created.length > 0) {
      pushToast(
        "success",
        created.length === 1 ? "Pasted 1 node" : `Pasted ${created.length} nodes`,
      );
    }
    return created.length;
  };

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

      // Cmd/Ctrl + Z = undo. Cmd/Ctrl + Shift + Z (or Cmd/Ctrl + Y) = redo.
      // Handled before the no-modifier guard. Keep the alt check loose so
      // OS-level chords don't get hijacked.
      if ((e.metaKey || e.ctrlKey) && !e.altKey) {
        const key = e.key.toLowerCase();
        if (key === "z") {
          e.preventDefault();
          if (e.shiftKey) redo();
          else undo();
          return;
        }
        if (key === "y" && !e.shiftKey) {
          e.preventDefault();
          redo();
          return;
        }
      }

      // Modifier-bearing shortcuts come *before* the no-modifier guard.
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey) {
        const key = e.key.toLowerCase();
        if (key === "a") {
          e.preventDefault();
          const proj = useAtelierStore.getState().currentProject;
          const nodes = proj?.nodes ?? [];
          if (nodes.length === 0) return;
          const [first, ...rest] = nodes;
          selectNode(first.id);
          setExtraSelectedIds(new Set(rest.map((n) => n.id)));
          return;
        }
        if (key === "c") {
          // Copy to private clipboard (not OS clipboard — node payloads
          // aren't useful there). Quiet on success: the next paste is the
          // signal users want.
          e.preventDefault();
          const count = copySelection();
          if (count === 0) {
            pushToast("info", "Nothing to copy.");
          } else {
            pushToast("info", count === 1 ? "1 node copied" : `${count} nodes copied`);
          }
          return;
        }
        if (key === "v") {
          e.preventDefault();
          if (clipboardRef.current.length === 0) {
            pushToast("info", "Clipboard is empty.");
            return;
          }
          void pasteClipboard().catch((err: unknown) => {
            pushToast("error", `Paste failed: ${err instanceof Error ? err.message : String(err)}`);
          });
          return;
        }
        if (key === "d") {
          // Cmd+D = duplicate. Snapshot now (overwriting the visible
          // clipboard is intentional — that's the cost of reusing copy
          // for duplicate; users who do explicit Cmd+C / Cmd+V can stack
          // separate buffers per-session).
          e.preventDefault();
          const count = copySelection();
          if (count === 0) {
            pushToast("info", "Select a node to duplicate.");
            return;
          }
          void pasteClipboard().catch((err: unknown) => {
            pushToast("error", `Duplicate failed: ${err instanceof Error ? err.message : String(err)}`);
          });
          return;
        }
      }

      if (e.metaKey || e.ctrlKey || e.altKey) return;
      // Arrow keys → navigate to the nearest node in that direction.
      // Shift+arrow extends the multi-selection (adds the new pick to extras).
      // Match Figma's pattern: pick the closest non-selected node whose center
      // is "primarily" in that direction relative to the current primary's
      // center. Falls back to no-op when the canvas is empty. Skipped when
      // the preview modal is open with take context (it has its own ←/→
      // handler for stepping between takes).
      if (preview && preview.parentId && preview.candidateId && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
      const arrowKey = (() => {
        switch (e.key) {
          case "ArrowRight": return "right" as const;
          case "ArrowLeft":  return "left"  as const;
          case "ArrowUp":    return "up"    as const;
          case "ArrowDown":  return "down"  as const;
          default: return null;
        }
      })();
      if (arrowKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const proj = useAtelierStore.getState().currentProject;
        const nodes = proj?.nodes ?? [];
        if (nodes.length === 0) return;
        e.preventDefault();
        const center = (n: { x: number; y: number; width?: number; height?: number }) => ({
          cx: n.x + (n.width || 240) / 2,
          cy: n.y + (n.height || 110) / 2,
        });
        const anchor = nodes.find((n) => n.id === selectedNodeId) ?? nodes[0];
        const ac = center(anchor);
        let best: { node: typeof nodes[number]; score: number } | null = null;
        for (const n of nodes) {
          if (n.id === anchor.id) continue;
          const c = center(n);
          const dx = c.cx - ac.cx;
          const dy = c.cy - ac.cy;
          // Direction predicate + distance-with-perpendicular-penalty so we
          // pick a *primarily* aligned neighbor (Figma's choice). Penalty
          // factor 2 means a node 100px sideways needs to be ~200px closer
          // along-axis to win over a more-aligned candidate.
          let inDir = false; let primary = 0; let perp = 0;
          if (arrowKey === "right") { inDir = dx > 0; primary = dx; perp = Math.abs(dy); }
          else if (arrowKey === "left") { inDir = dx < 0; primary = -dx; perp = Math.abs(dy); }
          else if (arrowKey === "up") { inDir = dy < 0; primary = -dy; perp = Math.abs(dx); }
          else if (arrowKey === "down") { inDir = dy > 0; primary = dy; perp = Math.abs(dx); }
          if (!inDir) continue;
          const score = primary + perp * 2;
          if (best === null || score < best.score) best = { node: n, score };
        }
        if (!best) return;
        if (e.shiftKey) {
          // Extend selection.
          setExtraSelectedIds((prev) => {
            const next = new Set(prev);
            next.add(best!.node.id);
            return next;
          });
        } else {
          selectNode(best.node.id);
          setExtraSelectedIds(new Set());
        }
        return;
      }

      switch (e.key.toLowerCase()) {
        case "?":
          e.preventDefault();
          setShowHelp((s) => !s);
          dismissOnboarding();
          break;
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
          if (showHelp) {
            e.preventDefault();
            setShowHelp(false);
            break;
          }
          if (contextMenu) {
            e.preventDefault();
            setContextMenu(null);
            break;
          }
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

  // Clipboard for copy / paste / duplicate. Each entry is a paste-ready
  // payload + the source's world coords so we can preserve relative layout
  // when pasting more than one node.
  const clipboardRef = useRef<Array<{ payload: AtelierNodePayload; originX: number; originY: number }>>([]);
  // Stacking offset so consecutive pastes don't sit on top of each other.
  const pasteOffsetRef = useRef<{ x: number; y: number }>({ x: 24, y: 24 });

  // Undo / redo history — position-move scope only for v1. Each entry is a
  // batch of (nodeId, prevX, prevY, nextX, nextY) tuples, so a group drag
  // and a single drag both round-trip through the same path. Capped at 50
  // entries to bound memory; undoing past that is rare and probably wrong.
  const HISTORY_CAP = 50;
  type MoveEntry = { nodeId: string; prevX: number; prevY: number; nextX: number; nextY: number };
  type HistoryItem = { kind: "move"; entries: MoveEntry[] };
  const undoStackRef = useRef<HistoryItem[]>([]);
  const redoStackRef = useRef<HistoryItem[]>([]);
  // bump to repaint the toolbar's undo/redo enabled state.
  const [, setHistoryTick] = useState(0);
  const pushHistory = (item: HistoryItem) => {
    undoStackRef.current.push(item);
    if (undoStackRef.current.length > HISTORY_CAP) undoStackRef.current.shift();
    // A new user action invalidates the redo lineage.
    redoStackRef.current = [];
    setHistoryTick((v) => v + 1);
  };
  const applyMoveEntries = async (entries: MoveEntry[], dir: "undo" | "redo") => {
    const store = useAtelierStore.getState();
    const proj = store.currentProject;
    if (!proj) return;
    const tasks: Array<Promise<unknown>> = [];
    for (const e of entries) {
      const x = dir === "undo" ? e.prevX : e.nextX;
      const y = dir === "undo" ? e.prevY : e.nextY;
      const exists = proj.nodes.some((n) => n.id === e.nodeId);
      if (!exists) continue;
      store.moveNodeLocal(e.nodeId, x, y);
      tasks.push(store.commitNodePosition(e.nodeId, x, y).catch(() => {}));
    }
    await Promise.all(tasks);
  };
  const undo = () => {
    const item = undoStackRef.current.pop();
    if (!item) return;
    void applyMoveEntries(item.entries, "undo");
    redoStackRef.current.push(item);
    setHistoryTick((v) => v + 1);
    pushToast("info", "Undid last move");
  };
  const redo = () => {
    const item = redoStackRef.current.pop();
    if (!item) return;
    void applyMoveEntries(item.entries, "redo");
    undoStackRef.current.push(item);
    setHistoryTick((v) => v + 1);
    pushToast("info", "Redid last move");
  };

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
    // Snap-to-grid helper. When Shift is held during a drag the resulting
    // world coords lock to the nearest GRID multiple — useful for tidy
    // alignment on the fly without manual nudging.
    const GRID = 8;
    const snap = (v: number) => (event.shiftKey ? Math.round(v / GRID) * GRID : Math.round(v));

    // Group drag (multi-selection)
    if (groupDragRef.current) {
      const dx = event.clientX - groupDragRef.current.startPointerX;
      const dy = event.clientY - groupDragRef.current.startPointerY;
      if (Math.abs(dx) + Math.abs(dy) > 3) groupDragRef.current.moved = true;
      const wd = screenDeltaToWorld(dx, dy);
      const store = useAtelierStore.getState();
      for (const m of groupDragRef.current.members) {
        store.moveNodeLocal(m.nodeId, snap(m.startWorldX + wd.x), snap(m.startWorldY + wd.y));
      }
      return;
    }
    // Node drag
    if (nodeDragRef.current) {
      const dx = event.clientX - nodeDragRef.current.startPointerX;
      const dy = event.clientY - nodeDragRef.current.startPointerY;
      const wd = screenDeltaToWorld(dx, dy);
      const newX = snap(nodeDragRef.current.startWorldX + wd.x);
      const newY = snap(nodeDragRef.current.startWorldY + wd.y);
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
        const entries: MoveEntry[] = [];
        for (const m of drag.members) {
          const real = proj?.nodes.find((n) => n.id === m.nodeId);
          if (real) {
            entries.push({ nodeId: m.nodeId, prevX: m.startWorldX, prevY: m.startWorldY, nextX: real.x, nextY: real.y });
            void store.commitNodePosition(m.nodeId, real.x, real.y).catch(() => {});
          }
        }
        if (entries.length > 0) pushHistory({ kind: "move", entries });
      }
      return;
    }
    if (nodeDragRef.current) {
      const drag = nodeDragRef.current;
      nodeDragRef.current = null;
      if (drag.moved) {
        const real = useAtelierStore.getState().currentProject?.nodes.find((n) => n.id === drag.nodeId);
        if (real) {
          pushHistory({
            kind: "move",
            entries: [{ nodeId: drag.nodeId, prevX: drag.startWorldX, prevY: drag.startWorldY, nextX: real.x, nextY: real.y }],
          });
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

  // Real viewport size, derived from the canvas main element. Falls back
  // to 1440x900 in environments where the rect isn't available yet (first
  // render, jsdom). Right-rail width is the only inset we need to subtract
  // since toolbar/save chip sit at top edges and don't crowd the composer.
  const viewport = useMemo(() => {
    const rect = mainRef.current?.getBoundingClientRect();
    return {
      width: rect?.width ?? 1440,
      height: rect?.height ?? 900,
      rightRailWidth: agentCollapsed ? 56 + 16 : 380 + 16,
    };
    // Re-derive when the right rail collapses or when zoom/pan changes,
    // which is a proxy for "user moved something" — the rect itself is
    // stable but the dependencies cover the cases where we want a fresh
    // measurement after layout settles.
  }, [agentCollapsed, zoom, panX, panY]);

  // Remember last-used model per project so the next new draft picks it up
  // instead of always defaulting to "Wan 2.7". Stored in localStorage with
  // the same key shape as the Sequence persistence — keeps user-side
  // preferences scoped to each project.
  const lastModelStorageKey = (projectId: string) => `atelier-v3-last-model:${projectId}`;
  const getRememberedModel = (projectId: string | undefined): string | null => {
    if (!projectId || typeof window === "undefined") return null;
    try {
      return window.localStorage.getItem(lastModelStorageKey(projectId));
    } catch {
      return null;
    }
  };
  const rememberModel = (projectId: string | undefined, model: string) => {
    if (!projectId || typeof window === "undefined") return;
    try { window.localStorage.setItem(lastModelStorageKey(projectId), model); } catch { /* ignore */ }
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
      // Persist the chosen model before kicking off generation — even if
      // the request fails, the user clearly intended this model and we
      // shouldn't re-default to Wan 2.7 on the next draft.
      rememberModel(project?.id, payload.modelLabel);
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
          // Pass parent/candidate into the modal so it can offer
          // take-level inline actions (select / branch / + sequence /
          // delete) instead of being a bare video popup.
          setPreview({ url: cand.video_url, parentId: parsed.parentId, candidateId: parsed.candidateId });
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

  // Keyboard shortcut help overlay — press '?' to open.
  const [showHelp, setShowHelp] = useState(false);

  // Project picker popover.
  const [showProjectPicker, setShowProjectPicker] = useState(false);

  // A11y: focus return-to-trigger across overlay open/close transitions.
  // We capture activeElement when *any* tracked modal/overlay opens, and
  // restore it when *all* are closed. Keyboard users land back on the
  // element that invoked the modal instead of dropping at body.
  const lastFocusedBeforeOverlayRef = useRef<HTMLElement | null>(null);
  // Effect lives further below — declared after all overlay states.

  // First-run onboarding hint pointing to '?'. Persists "seen" across
  // refreshes via localStorage so we don't nag returning users.
  const [showOnboarding, setShowOnboarding] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const seen = window.localStorage.getItem("atelier-v3-onboarding-seen");
      if (!seen) {
        // Wait for project to load + auto-fit settle, then surface.
        const t = window.setTimeout(() => setShowOnboarding(true), 1200);
        return () => window.clearTimeout(t);
      }
    } catch {
      /* localStorage may be unavailable; fall back to no hint */
    }
  }, []);
  const dismissOnboarding = () => {
    setShowOnboarding(false);
    try { window.localStorage.setItem("atelier-v3-onboarding-seen", "1"); } catch { /* ignore */ }
  };

  // Right-click context menu. Holds the cursor position (screen coords)
  // plus an optional node it was opened on. Closed by outside click,
  // Esc, or selecting a menu item. Node-less entries are canvas menus.
  const [contextMenu, setContextMenu] = useState<{
    screenX: number;
    screenY: number;
    node: AtelierNode | null;
  } | null>(null);

  // A11y focus-restore effect (companion to lastFocusedBeforeOverlayRef
  // declared near the top of the component). Captures focus on first open
  // of any tracked modal/popover; restores it once all are closed.
  useEffect(() => {
    const anyOpen = !!(showHelp || preview || useAsRefSourceId || contextMenu || showProjectPicker);
    if (anyOpen) {
      if (!lastFocusedBeforeOverlayRef.current) {
        const active = typeof document !== "undefined" ? document.activeElement : null;
        lastFocusedBeforeOverlayRef.current = (active && active !== document.body)
          ? (active as HTMLElement)
          : null;
      }
      return;
    }
    const prev = lastFocusedBeforeOverlayRef.current;
    lastFocusedBeforeOverlayRef.current = null;
    if (prev && typeof prev.focus === "function" && document.contains(prev)) {
      // RAF so React finishes unmounting the modal first; otherwise focus
      // can land on the disappearing element and immediately fall to body.
      requestAnimationFrame(() => prev.focus());
    }
  }, [showHelp, preview, useAsRefSourceId, contextMenu, showProjectPicker]);

  // Sequence ordering is stored in localStorage keyed on project id so it
  // survives refresh + back-nav. Server-side persistence (a dedicated
  // backend field on AtelierProject) is the right v1.1 — for now this
  // gives prod-grade durability without a schema change. Multi-device
  // sync ships with the migration.
  const sequenceStorageKey = (projectId: string) => `atelier-v3-seq:${projectId}`;
  const [sequence, setSequence] = useState<Array<{ parentId: string; candidateId: string }>>([]);
  // Hydrate from localStorage when project id changes.
  useEffect(() => {
    if (!project?.id || typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(sequenceStorageKey(project.id));
      if (!raw) {
        setSequence([]);
        return;
      }
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        // Defensively validate shape so a corrupted payload can't crash render.
        const valid = parsed.filter(
          (e): e is { parentId: string; candidateId: string } =>
            !!e && typeof e === "object" && typeof (e as { parentId?: unknown }).parentId === "string" && typeof (e as { candidateId?: unknown }).candidateId === "string",
        );
        setSequence(valid);
      } else {
        setSequence([]);
      }
    } catch {
      setSequence([]);
    }
  }, [project?.id]);
  // Persist on every change. Coalesce to localStorage; the storage write is
  // synchronous but cheap (<1ms for a few dozen entries).
  useEffect(() => {
    if (!project?.id || typeof window === "undefined") return;
    try {
      window.localStorage.setItem(sequenceStorageKey(project.id), JSON.stringify(sequence));
    } catch {
      /* quota exceeded / Safari private mode etc. — fall back to in-memory */
    }
  }, [project?.id, sequence]);

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

  // Double-click empty canvas → create a video draft at the clicked world
  // coords. RHTV-style "think it, create it" gesture. Skipped when the
  // user double-clicked a node, dialog, toolbar, or right rail.
  const handleMainDoubleClick = (event: React.MouseEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget && (event.target as HTMLElement).closest('[data-atelier-node],[role="dialog"],[role="toolbar"],[role="region"],[role="menu"],[role="status"]')) return;
    const rect = mainRef.current?.getBoundingClientRect();
    if (!rect) return;
    const wx = (event.clientX - rect.left - panX) / zoomFactor;
    const wy = (event.clientY - rect.top - panY) / zoomFactor;
    void (async () => {
      try {
        const proj = await ensureProject();
        const variant = draftVariations[(proj.nodes.length) % draftVariations.length];
        const remembered = getRememberedModel(proj.id);
        const node = await api.createAtelierNode(proj.id, {
          type: "video",
          title: `Video Node ${proj.nodes.length + 1}`,
          prompt: "",
          status: "draft",
          x: Math.round(wx - 120),
          y: Math.round(wy - 55),
          width: 240,
          height: 110,
          data: {
            intent: variant.intent,
            model: remembered ?? variant.model,
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
    })();
  };

  const handleCreateVideo = async () => {
    try {
      const proj = await ensureProject();
      const before = proj.nodes.length;
      const variant = draftVariations[before % draftVariations.length];
      const remembered = getRememberedModel(proj.id);
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
          model: remembered ?? variant.model,
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
        onUndo={undo}
        onRedo={redo}
        canUndo={undoStackRef.current.length > 0}
        canRedo={redoStackRef.current.length > 0}
      />

      {/* Project picker — sits below Toolbar (top-16 left-4). Pill shows
          current project; click opens a popover with the project list +
          a "New" CTA. Hidden when there are no projects loaded yet. */}
      {project ? (
        <div className="absolute left-4 top-16 z-30">
          <button
            type="button"
            aria-label="Switch project"
            aria-expanded={showProjectPicker}
            onClick={() => setShowProjectPicker((v) => !v)}
            className="btn-tip inline-flex items-center gap-1.5 rounded-full border border-glass-border bg-glass px-2.5 py-1 text-[12px] text-foreground hover:bg-hover-bg"
            data-tip="Switch project"
          >
            <FolderOpen size={12} className="text-text-muted" aria-hidden="true" />
            <span className="max-w-[160px] truncate">{project.title || "Untitled"}</span>
            <ChevronDown size={12} className="text-text-muted" aria-hidden="true" />
          </button>
          {showProjectPicker ? (
            <>
              <div
                aria-hidden="true"
                className="fixed inset-0 z-[34]"
                onClick={() => setShowProjectPicker(false)}
              />
              <div
                role="menu"
                aria-label="Atelier projects"
                className="absolute left-0 top-9 z-[35] w-[280px] rounded-md border border-glass-border bg-elevated p-1 shadow-2xl shadow-black/50 backdrop-blur-md"
              >
                <div className="px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-text-muted">
                  Projects · {projects.length}
                </div>
                <ul className="max-h-[260px] overflow-y-auto">
                  {projects.map((p) => {
                    const isCurrent = p.id === project.id;
                    return (
                      <li key={p.id} role="none" className="group/proj relative">
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setShowProjectPicker(false);
                            if (isCurrent) return;
                            void switchProject(p.id)
                              .then(() => pushToast("info", `Switched to "${p.title || "Untitled"}"`))
                              .catch((err: unknown) => pushToast("error", `Switch failed: ${err instanceof Error ? err.message : String(err)}`));
                          }}
                          className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left transition-colors ${
                            isCurrent ? "bg-primary/15 text-foreground" : "text-text-secondary hover:bg-hover-bg hover:text-foreground"
                          }`}
                        >
                          <div className="min-w-0">
                            <div className="truncate text-[13px] font-medium">{p.title || "Untitled"}</div>
                            <div className="font-mono text-[10px] text-text-muted">
                              {p.nodes.length} node{p.nodes.length === 1 ? "" : "s"}
                            </div>
                          </div>
                          {isCurrent ? (
                            <Check size={12} className="shrink-0 text-primary" aria-label="Current" />
                          ) : null}
                        </button>
                        {/* Hover-revealed rename + delete affordances. Only
                            actionable on the current project (renaming a
                            non-current project would surprise — we don't
                            switch there, just rename). For non-current we
                            still show but they'll switchProject first. */}
                        <div className="pointer-events-none absolute right-1 top-1 hidden gap-0.5 group-hover/proj:flex">
                          <button
                            type="button"
                            aria-label={`Rename ${p.title || "Untitled"}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              const next = window.prompt("Rename project:", p.title || "");
                              if (!next || next === p.title) return;
                              void api
                                .updateAtelierProject(p.id, { title: next })
                                .then(async () => {
                                  await useAtelierStore.getState().loadProjects();
                                  pushToast("success", `Renamed to "${next}"`);
                                })
                                .catch((err: unknown) => pushToast("error", `Rename failed: ${err instanceof Error ? err.message : String(err)}`));
                            }}
                            className="pointer-events-auto rounded p-1 text-text-muted hover:bg-hover-bg hover:text-foreground"
                          >
                            <Pencil size={11} />
                          </button>
                          {projects.length > 1 ? (
                            <button
                              type="button"
                              aria-label={`Delete ${p.title || "Untitled"}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                const ok = window.confirm(`Delete "${p.title || "Untitled"}" and all its nodes?\nThis cannot be undone.`);
                                if (!ok) return;
                                setShowProjectPicker(false);
                                void api
                                  .deleteAtelierProject(p.id)
                                  .then(async () => {
                                    await useAtelierStore.getState().loadProjects();
                                    pushToast("info", `Deleted "${p.title || "Untitled"}"`);
                                  })
                                  .catch((err: unknown) => pushToast("error", `Delete failed: ${err instanceof Error ? err.message : String(err)}`));
                              }}
                              className="pointer-events-auto rounded p-1 text-text-muted hover:bg-red-400/20 hover:text-red-200"
                            >
                              <Trash2 size={11} />
                            </button>
                          ) : null}
                        </div>
                      </li>
                    );
                  })}
                </ul>
                <div className="mt-1 border-t border-border-subtle pt-1">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      const title = window.prompt("Project name?");
                      if (!title) return;
                      setShowProjectPicker(false);
                      void createProject(title)
                        .then(() => pushToast("success", `Created "${title}"`))
                        .catch((err: unknown) => pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`));
                    }}
                    className="flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-[12px] font-medium text-primary hover:bg-primary/10"
                  >
                    <Plus size={12} aria-hidden="true" />
                    New project
                  </button>
                </div>
              </div>
            </>
          ) : null}
        </div>
      ) : null}

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
        onDoubleClick={handleMainDoubleClick}
        onContextMenu={(e) => {
          // Empty canvas right-click → canvas-level menu (Paste / Select all).
          // The node-wrapper handler stops propagation so this only fires when
          // the user clicked truly empty space.
          if ((e.target as HTMLElement).closest("[data-atelier-node],[role=\"dialog\"],[role=\"toolbar\"],[role=\"region\"]")) return;
          e.preventDefault();
          setContextMenu({ screenX: e.clientX, screenY: e.clientY, node: null });
        }}
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

        {/* Empty-canvas welcome (DESIGN.md §11.1). Three clickable seed
            cards beat a one-liner — production users land here cold and
            need an obvious first action. */}
        {projectIsEmpty ? (
          <div className="pointer-events-none absolute inset-0 grid place-items-center">
            <div className="pointer-events-auto flex flex-col items-center gap-6 text-center animate-atelier-node-in motion-reduce:animate-none">
              <div className="space-y-1">
                <div className="font-display text-[20px] font-semibold text-foreground">Drop a seed</div>
                <div className="text-[13px] text-text-secondary">
                  Pick a starting point. Everything you make connects from here.
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3">
                {([
                  { kind: "video", key: "V", title: "Video Node", desc: "Compose with a model + refs.", primary: true },
                  { kind: "image", key: "I", title: "Image Node", desc: "Upload or generate a reference.", primary: false },
                  { kind: "idea",  key: "T", title: "Idea Note",  desc: "Capture a beat or a vibe.",     primary: false },
                ] as const).map((card) => (
                  <button
                    key={card.kind}
                    type="button"
                    onClick={() => {
                      if (card.kind === "video") void handleCreateVideo();
                      else if (card.kind === "image") {
                        void createEmptyImageDraft().catch((err: unknown) => pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`));
                      } else {
                        void createIdeaNode()
                          .then((node) => {
                            setEditingIdeaId(node.id);
                            setEditingIdeaBody((node.data as { body?: string })?.body ?? "");
                          })
                          .catch((err: unknown) => pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`));
                      }
                    }}
                    className={`group flex w-[180px] flex-col items-start gap-1 rounded-xl border bg-elevated p-3 text-left shadow-2xl shadow-black/30 transition-all hover:-translate-y-0.5 ${
                      card.primary
                        ? "border-primary/40 hover:border-primary/70 hover:shadow-[0_0_0_1px_rgba(100,108,255,0.4)]"
                        : "border-glass-border hover:border-primary/40"
                    }`}
                  >
                    <div className="flex items-center justify-between w-full">
                      <span className={`text-[13px] font-semibold ${card.primary ? "text-primary" : "text-foreground"}`}>
                        {card.title}
                      </span>
                      <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-text-muted">
                        {card.key}
                      </kbd>
                    </div>
                    <span className="text-[11px] text-text-muted leading-snug">{card.desc}</span>
                  </button>
                ))}
              </div>
              <div className="text-[11px] text-text-muted">
                Press <kbd className="rounded border border-primary/40 bg-primary/10 px-1 font-mono text-[10px] text-primary">?</kbd> for shortcuts ·
                Drop image files anywhere on the canvas
              </div>
            </div>
          </div>
        ) : null}

        {/* World — everything in canvas space lives here. Transformed by zoom + pan. */}
        <div
          className="absolute left-0 top-0 origin-top-left"
          style={{ transform: `translate(${panX}px, ${panY}px) scale(${zoomFactor})` }}
        >
          {/* edges layer (in world coords). Labels are collected during the
              same pass so the geometry math doesn't have to be duplicated;
              we render the path SVG and the chip overlay separately. */}
          {(() => {
            const labels: EdgeLabel[] = [];
            const paths = renderEdges(project ?? null, hoveredNodeId, labels);
            return (
              <>
                <svg
                  className="pointer-events-none absolute"
                  style={{ left: -10000, top: -10000, width: 20000, height: 20000, zIndex: 5 }}
                  viewBox="-10000 -10000 20000 20000"
                >
                  {paths}
                </svg>
                {labels.map((l) => {
                  const tone =
                    l.tone === "primary" ? "border-primary/40 bg-primary/15 text-primary" :
                    l.tone === "success" ? "border-emerald-400/40 bg-emerald-400/15 text-emerald-200" :
                    l.tone === "error"   ? "border-red-400/40 bg-red-400/15 text-red-200" :
                    l.tone === "warning" ? "border-amber-300/40 bg-amber-400/15 text-amber-200" :
                    "border-glass-border bg-elevated/85 text-text-secondary";
                  return (
                    <div
                      key={l.key}
                      aria-hidden="true"
                      className={`pointer-events-none absolute z-[6] -translate-x-1/2 -translate-y-1/2 rounded-full border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider backdrop-blur-md animate-atelier-node-in motion-reduce:animate-none ${tone}`}
                      style={{ left: l.midX, top: l.midY }}
                    >
                      {l.text}
                    </div>
                  );
                })}
              </>
            );
          })()}

          {/* nodes — each wrapped in a drag-aware div. Use *Capture* phase
              because v3 leaf nodes stopPropagation in their own onPointerDown
              (Wave A polish), so a normal bubble-phase parent handler never
              fires. Capture lets us start the drag tracking before the child
              consumes the event; the child still selects the node on click. */}
          {(() => {
            // Viewport-cull invisible nodes — keeps the React tree small on
            // big canvases. Always include selected, hovered, and currently-
            // dragged nodes so a node won't pop out from under the user.
            // Bypass culling when the canvas hasn't laid out yet (jsdom or
            // very first render with rect dimensions still 0) — otherwise
            // the empty viewport filters every node out.
            const rect = mainRef.current?.getBoundingClientRect();
            const screenW = rect?.width ?? 0;
            const screenH = rect?.height ?? 0;
            if (screenW < 32 || screenH < 32) return project?.nodes ?? [];
            const PAD = 200;
            const visMinX = -panX / zoomFactor - PAD;
            const visMinY = -panY / zoomFactor - PAD;
            const visMaxX = (screenW - panX) / zoomFactor + PAD;
            const visMaxY = (screenH - panY) / zoomFactor + PAD;
            const draggingIds = new Set<string>();
            if (nodeDragRef.current) draggingIds.add(nodeDragRef.current.nodeId);
            if (groupDragRef.current) for (const m of groupDragRef.current.members) draggingIds.add(m.nodeId);
            return (project?.nodes ?? []).filter((n) => {
              if (allSelectedIds.has(n.id) || hoveredNodeId === n.id || draggingIds.has(n.id)) return true;
              const nx2 = n.x + (n.width || 240);
              const ny2 = n.y + (n.height || 110);
              return !(nx2 < visMinX || n.x > visMaxX || ny2 < visMinY || n.y > visMaxY);
            });
          })().map((node) => {
            const isSelected = allSelectedIds.has(node.id);
            const isBeingDragged =
              nodeDragRef.current?.nodeId === node.id ||
              groupDragRef.current?.members.some((m) => m.nodeId === node.id);
            return (
              <div
                key={node.id}
                data-atelier-node={node.id}
                onPointerDownCapture={(e) => handleNodePointerDown(e, node)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  // Right-click also selects the node so the menu actions
                  // anchor on a stable selection.
                  if (!allSelectedIds.has(node.id)) {
                    selectNode(node.id);
                    if (extraSelectedIds.size > 0) setExtraSelectedIds(new Set());
                  }
                  setContextMenu({ screenX: e.clientX, screenY: e.clientY, node });
                }}
                onMouseEnter={() => setHoveredNodeId(node.id)}
                onMouseLeave={() => setHoveredNodeId((prev) => (prev === node.id ? null : prev))}
                className={`group/node animate-atelier-node-in motion-reduce:animate-none ${isSelected ? "" : "cursor-pointer"}`}
                style={{
                  touchAction: "none",
                  cursor: isBeingDragged ? "grabbing" : undefined,
                  // Selected nodes lift above unselected so they don't get
                  // visually overlapped by neighbors. Stays below the screen-
                  // coord overlays (z-30+) which sit outside the world.
                  // Mid-drag we go higher still so the moving card visually
                  // clears anything it crosses over.
                  zIndex: isBeingDragged ? 25 : isSelected ? 20 : 10,
                  // Subtle drag ghost: 85% opacity while moving. Transition
                  // makes hover-grab and release feel intentional rather
                  // than snappy.
                  opacity: isBeingDragged ? 0.88 : undefined,
                  transition: "opacity 140ms ease-out",
                }}
              >
                {renderNode(node, allSelectedIds, selectNode)}
              </div>
            );
          })}

          {/* virtual candidate media nodes (no drag — derived) */}
          {project?.nodes.flatMap((node) =>
            renderCandidatesAsMediaNodes(node, allSelectedIds, selectNode, (parentId, candidateId) => {
              void useAtelierStore.getState()
                .retryCandidate(parentId, candidateId)
                .then(() => pushToast("info", "Retrying take…"))
                .catch((err: unknown) => pushToast("error", `Retry failed: ${err instanceof Error ? err.message : String(err)}`));
            }),
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

      {/* Inspector pill — single-selection, non-draft. Shows kind-specific
          one-line facts below the node so the user has context without a
          dedicated side panel. Drafts skip this because the Composer
          already exposes model + refs. Multi-selection skips so the
          batch chip can claim that space. */}
      {selectedNode && !isMultiSelect && !isDraftVideo(selectedNode) && !parseCandidateNodeId(selectedNode.id) ? (() => {
        const facts: Array<{ label: string; value: string }> = [];
        const node = selectedNode;
        if (node.type === "image") {
          const filename = readString((node.data as { filename?: unknown })?.filename) ?? node.title;
          if (filename) facts.push({ label: "file", value: filename });
          const refCount = (project?.nodes ?? []).filter((n) => {
            const refs = readStringArray((n.data as { reference_image_urls?: unknown })?.reference_image_urls);
            return n.type === "video" && (node.media_urls ?? []).some((u) => refs.includes(u));
          }).length;
          if (refCount > 0) facts.push({ label: "used by", value: `${refCount} draft${refCount === 1 ? "" : "s"}` });
        } else if (node.type === "video") {
          const cands = readCandidates(node);
          const completed = cands.filter((c) => c.status === "completed").length;
          if (cands.length > 0) facts.push({ label: "takes", value: `${completed}/${cands.length}` });
          const url = node.media_urls?.[0];
          if (url) facts.push({ label: "media", value: "ready" });
        } else if (node.type === "idea") {
          const body = readString(node.data?.body) ?? node.prompt ?? "";
          const chars = body.length;
          facts.push({ label: "length", value: `${chars} char${chars === 1 ? "" : "s"}` });
        } else if (node.type === "plan") {
          const bullets = readStringArray(node.data?.bullets);
          facts.push({ label: "steps", value: `${bullets.length}` });
        } else if (node.type === "audio") {
          const dur = readString((node.data as { duration?: unknown })?.duration);
          if (dur) facts.push({ label: "duration", value: dur });
        }
        const updated = node.updated_at ? Math.floor((Date.now() / 1000 - node.updated_at)) : null;
        if (updated !== null && updated >= 0) {
          let agoLabel: string;
          if (updated < 60) agoLabel = `${updated}s`;
          else if (updated < 3600) agoLabel = `${Math.floor(updated / 60)}m`;
          else if (updated < 86400) agoLabel = `${Math.floor(updated / 3600)}h`;
          else agoLabel = `${Math.floor(updated / 86400)}d`;
          facts.push({ label: "edited", value: `${agoLabel} ago` });
        }
        if (facts.length === 0) return null;
        const screenLeft = panX + node.x * zoomFactor;
        const screenTop = panY + (node.y + (node.height || 180)) * zoomFactor + 8;
        return (
          <div
            role="status"
            aria-label="Selected node details"
            className="absolute z-30 inline-flex items-center gap-2 rounded-full border border-glass-border bg-elevated/85 px-2.5 py-1 font-mono text-[10px] backdrop-blur-md shadow-2xl shadow-black/40 animate-atelier-node-in motion-reduce:animate-none"
            style={{ left: screenLeft, top: screenTop }}
          >
            {facts.map((f, i) => (
              <span key={f.label} className="inline-flex items-center gap-1">
                <span className="uppercase tracking-wider text-text-muted">{f.label}</span>
                <span className="text-foreground">{f.value}</span>
                {i < facts.length - 1 ? <span aria-hidden="true" className="text-text-muted">·</span> : null}
              </span>
            ))}
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
              onPromptCommit={(next) => {
                if (!isDraftVideo(selectedNode)) return;
                void useAtelierStore.getState()
                  .updateNode(selectedNode.id, {
                    data: { ...(selectedNode.data ?? {}), prompt: next },
                  })
                  .catch(() => {/* save chip surfaces failures */});
              }}
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

      {/* minimap floating widget — viewport rect is the actual visible
          world rect (derived from pan + zoom + main element size); world
          bounds expand to fit all nodes + a 400px margin so nodes never
          fall outside the minimap on small/large canvases. */}
      {minimapOpen && project ? (() => {
        const rect = mainRef.current?.getBoundingClientRect();
        const screenW = rect?.width ?? 1440;
        const screenH = rect?.height ?? 900;
        // Visible world rect: top-left in world = (-pan)/zoom, size = screen/zoom.
        const visibleX = -panX / zoomFactor;
        const visibleY = -panY / zoomFactor;
        const visibleW = screenW / zoomFactor;
        const visibleH = screenH / zoomFactor;
        // World bounds — encompass all nodes + the visible viewport so the
        // user always sees their current view inside the minimap, even if
        // they panned out into empty space.
        let minX = visibleX, minY = visibleY;
        let maxX = visibleX + visibleW, maxY = visibleY + visibleH;
        for (const n of project.nodes) {
          minX = Math.min(minX, n.x);
          minY = Math.min(minY, n.y);
          maxX = Math.max(maxX, n.x + (n.width || 240));
          maxY = Math.max(maxY, n.y + (n.height || 110));
        }
        const PAD = 400;
        const worldX = minX - PAD;
        const worldY = minY - PAD;
        const worldWidth = (maxX - minX) + PAD * 2;
        const worldHeight = (maxY - minY) + PAD * 2;
        return (
          <Minimap
            nodes={project.nodes.map((n) => ({
              id: n.id,
              x: n.x - worldX,
              y: n.y - worldY,
            }))}
            viewport={{
              x: visibleX - worldX,
              y: visibleY - worldY,
              w: visibleW,
              h: visibleH,
            }}
            worldBounds={{ width: worldWidth, height: worldHeight }}
            selectedIds={allSelectedIds}
            onRecenter={(wx, wy) => {
              // wx/wy are in *minimap-translated* world coords (already
              // offset by worldX/Y because we passed translated nodes).
              // Add back the offset to land in real world coords, then
              // recenter screen on that world point.
              const targetWorldX = wx + worldX;
              const targetWorldY = wy + worldY;
              setPanX(screenW / 2 - targetWorldX * zoomFactor);
              setPanY(screenH / 2 - targetWorldY * zoomFactor);
            }}
          />
        );
      })() : null}

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
                draggable
                onDragStart={(e) => {
                  setSeqDragFromIndex(i);
                  e.dataTransfer.effectAllowed = "move";
                  // Required by Firefox to actually start the drag.
                  e.dataTransfer.setData("text/plain", String(i));
                }}
                onDragOver={(e) => {
                  if (seqDragFromIndex === null) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  if (seqDragOverIndex !== i) setSeqDragOverIndex(i);
                }}
                onDragLeave={(e) => {
                  // Only clear if leaving for somewhere outside this clip.
                  if (e.currentTarget.contains(e.relatedTarget as Node)) return;
                  setSeqDragOverIndex((prev) => (prev === i ? null : prev));
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const from = seqDragFromIndex;
                  const to = i;
                  setSeqDragFromIndex(null);
                  setSeqDragOverIndex(null);
                  if (from === null || from === to) return;
                  setSequence((prev) => {
                    const next = prev.slice();
                    const [moved] = next.splice(from, 1);
                    next.splice(to, 0, moved);
                    return next;
                  });
                }}
                onDragEnd={() => {
                  setSeqDragFromIndex(null);
                  setSeqDragOverIndex(null);
                }}
                onClick={() => cand.video_url && setPreviewVideoUrl(cand.video_url)}
                className={`group relative h-[68px] w-[140px] shrink-0 cursor-grab overflow-hidden rounded-md border bg-elevated/80 transition-shadow hover:border-primary/50 hover:shadow-[0_0_0_1px_rgba(100,108,255,0.18)] active:cursor-grabbing ${
                  seqDragFromIndex === i
                    ? "opacity-50 border-primary/60"
                    : seqDragOverIndex === i && seqDragFromIndex !== null && seqDragFromIndex !== i
                    ? "border-primary ring-2 ring-primary/40"
                    : "border-glass-border"
                }`}
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

      {/* Right-click context menu — closes on outside click + Esc. */}
      {contextMenu ? (() => {
        const node = contextMenu.node;
        const items: Array<{ label: string; onClick: () => void; danger?: boolean; disabled?: boolean }> = [];
        const close = () => setContextMenu(null);
        if (!node) {
          // Canvas (empty) menu.
          items.push({
            label: "Paste",
            onClick: () => {
              void pasteClipboard().catch((err: unknown) => {
                pushToast("error", `Paste failed: ${err instanceof Error ? err.message : String(err)}`);
              });
              close();
            },
            disabled: clipboardRef.current.length === 0,
          });
          items.push({
            label: "Select all",
            onClick: () => {
              const proj = useAtelierStore.getState().currentProject;
              const nodes = proj?.nodes ?? [];
              if (nodes.length > 0) {
                const [first, ...rest] = nodes;
                selectNode(first.id);
                setExtraSelectedIds(new Set(rest.map((n) => n.id)));
              }
              close();
            },
            disabled: (project?.nodes.length ?? 0) === 0,
          });
        } else {
        const kind = selectionKindOf(node);
        if (kind === "draft") {
          items.push({ label: "Edit prompt", onClick: () => { selectNode(node.id); close(); } });
          items.push({ label: "Duplicate", onClick: () => { copySelection(); void pasteClipboard(); close(); } });
          items.push({ label: "Delete", onClick: () => { void deleteSelection(); close(); }, danger: true });
        } else if (kind === "image") {
          const hasMedia = (node.media_urls?.length ?? 0) > 0;
          items.push({ label: "Use as reference…", onClick: () => { handleActionBar("useAsRef", node); close(); }, disabled: !hasMedia });
          items.push({ label: "Duplicate", onClick: () => { copySelection(); void pasteClipboard(); close(); } });
          items.push({ label: "Delete", onClick: () => { void deleteSelection(); close(); }, danger: true });
        } else if (kind === "idea") {
          items.push({ label: "Edit", onClick: () => {
            selectNode(node.id);
            const body = (node.data as { body?: string })?.body ?? node.prompt ?? "";
            setEditingIdeaId(node.id);
            setEditingIdeaBody(body);
            close();
          } });
          items.push({ label: "Duplicate", onClick: () => { copySelection(); void pasteClipboard(); close(); } });
          items.push({ label: "Delete", onClick: () => { void deleteSelection(); close(); }, danger: true });
        } else if (kind === "video") {
          const url = node.media_urls?.[0];
          items.push({ label: "Play", onClick: () => { if (url) setPreviewVideoUrl(url); else pushToast("info", "Nothing to play yet."); close(); }, disabled: !url });
          items.push({ label: "Duplicate", onClick: () => { copySelection(); void pasteClipboard(); close(); } });
          items.push({ label: "Delete", onClick: () => { void deleteSelection(); close(); }, danger: true });
        } else if (kind === "audio") {
          items.push({ label: "Duplicate", onClick: () => { copySelection(); void pasteClipboard(); close(); } });
          items.push({ label: "Delete", onClick: () => { void deleteSelection(); close(); }, danger: true });
        }
        }
        return (
          <>
            <div
              aria-hidden="true"
              className="fixed inset-0 z-[55]"
              onClick={close}
              onContextMenu={(e) => { e.preventDefault(); close(); }}
            />
            <ul
              role="menu"
              aria-label="Node context menu"
              onContextMenu={(e) => e.preventDefault()}
              className="fixed z-[56] min-w-[180px] rounded-md border border-glass-border bg-elevated p-1 text-[12px] shadow-2xl shadow-black/50 backdrop-blur-md"
              style={{
                left: Math.min(contextMenu.screenX, window.innerWidth - 200),
                top: Math.min(contextMenu.screenY, window.innerHeight - 200),
              }}
            >
              {items.map((item) => (
                <li key={item.label} role="none">
                  <button
                    type="button"
                    role="menuitem"
                    disabled={item.disabled}
                    onClick={item.disabled ? undefined : item.onClick}
                    className={`block w-full rounded px-2.5 py-1.5 text-left transition-colors ${
                      item.disabled
                        ? "text-text-muted/60 cursor-not-allowed"
                        : item.danger
                        ? "text-red-200 hover:bg-red-400/15"
                        : "text-foreground hover:bg-hover-bg"
                    }`}
                  >
                    {item.label}
                  </button>
                </li>
              ))}
            </ul>
          </>
        );
      })() : null}

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

      {/* Preview modal — wraps the video, plus inline take actions when
          the preview was launched from a candidate (select / branch /
          delete / add to sequence). URL-only previews (sequence strip /
          generic Play) get just the close button. */}
      {preview ? (() => {
        const ctx = preview;
        const close = () => setPreview(null);
        const parent = ctx.parentId ? project?.nodes.find((n) => n.id === ctx.parentId) : undefined;
        const cand = parent && ctx.candidateId
          ? readCandidates(parent).find((c) => c.id === ctx.candidateId)
          : undefined;
        const isTake = !!cand;
        const isSelectedTake =
          parent && cand && (parent.data as { selected_candidate_id?: string })?.selected_candidate_id === cand.id;
        return (
          <div
            className="fixed inset-0 z-50 grid place-items-center bg-black/80 backdrop-blur-sm"
            onClick={close}
            role="dialog"
            aria-label="Video preview"
          >
            <div className="relative max-h-[88vh] max-w-[80vw] overflow-hidden rounded-xl border border-glass-border bg-elevated shadow-2xl shadow-black/40" onClick={(e) => e.stopPropagation()}>
              <video
                src={getAssetUrl(ctx.url)}
                controls
                autoPlay
                className="block max-h-[80vh] max-w-[80vw]"
              />
              {isTake && parent && cand ? (
                <div className="flex items-center justify-between gap-2 border-t border-border-subtle bg-elevated px-3 py-2">
                  <div className="min-w-0 text-[11px]">
                    <div className="truncate font-medium text-foreground">{parent.title}</div>
                    <div className="font-mono text-[10px] text-text-muted">
                      {cand.model} · {cand.label || cand.id.slice(0, 8)}
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 text-[11px]">
                    {!isSelectedTake ? (
                      <button
                        type="button"
                        onClick={() => {
                          void useAtelierStore.getState().selectCandidate(parent.id, cand.id)
                            .then(() => pushToast("success", "Selected as take"))
                            .catch((err: unknown) => pushToast("error", `Select failed: ${err instanceof Error ? err.message : String(err)}`));
                          close();
                        }}
                        className="rounded-full bg-primary px-2 py-0.5 font-medium text-white hover:bg-primary/90"
                      >
                        Select as take
                      </button>
                    ) : (
                      <span className="rounded-full bg-emerald-400/15 px-2 py-0.5 font-medium text-emerald-200">Selected</span>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        void branchFromCandidate(parent.id, cand.id)
                          .then(() => pushToast("success", "Branched · new draft created"))
                          .catch((err: unknown) => pushToast("error", `Branch failed: ${err instanceof Error ? err.message : String(err)}`));
                        close();
                      }}
                      className="rounded-full bg-glass px-2 py-0.5 text-text-secondary hover:bg-hover-bg hover:text-foreground"
                    >
                      Branch
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setSequence((prev) => {
                          if (prev.some((s) => s.parentId === parent.id && s.candidateId === cand.id)) return prev;
                          return [...prev, { parentId: parent.id, candidateId: cand.id }];
                        });
                        pushToast("success", "Added to Sequence");
                      }}
                      className="rounded-full bg-glass px-2 py-0.5 text-text-secondary hover:bg-hover-bg hover:text-foreground"
                    >
                      + Sequence
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        void useAtelierStore.getState().deleteCandidate(parent.id, cand.id)
                          .then(() => pushToast("info", "Take deleted"))
                          .catch((err: unknown) => pushToast("error", `Delete failed: ${err instanceof Error ? err.message : String(err)}`));
                        close();
                      }}
                      className="rounded-full bg-red-400/15 px-2 py-0.5 text-red-200 hover:bg-red-400/25"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ) : null}
              <button
                onClick={close}
                className="absolute right-2 top-2 rounded-full bg-black/55 p-1.5 text-white/90 hover:bg-black/75"
                aria-label="Close preview"
              >
                <X size={14} />
              </button>
            </div>
          </div>
        );
      })() : null}

      {/* First-run onboarding hint — slides in once, points to '?' help. */}
      {showOnboarding && !showHelp ? (
        <div
          role="status"
          aria-live="polite"
          className="absolute bottom-[120px] left-4 z-30 max-w-[280px] animate-atelier-node-in motion-reduce:animate-none rounded-xl border border-primary/40 bg-elevated px-3 py-2.5 shadow-2xl shadow-black/40 backdrop-blur-md"
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="font-mono text-[10px] uppercase tracking-wider text-primary/85">Welcome</span>
            <button
              type="button"
              onClick={dismissOnboarding}
              aria-label="Dismiss tip"
              className="rounded p-0.5 text-text-muted hover:bg-hover-bg hover:text-foreground"
            >
              <X size={11} />
            </button>
          </div>
          <p className="text-[12px] leading-relaxed text-text-secondary">
            Drop a seed with{" "}
            <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-foreground">V</kbd>,{" "}
            <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-foreground">I</kbd>, or{" "}
            <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-foreground">T</kbd>.
            Press{" "}
            <kbd className="rounded border border-primary/40 bg-primary/10 px-1 font-mono text-[10px] text-primary">?</kbd>{" "}
            anytime to see every shortcut.
          </p>
          <button
            type="button"
            onClick={() => { setShowHelp(true); dismissOnboarding(); }}
            className="mt-2 inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-[11px] font-medium text-primary hover:bg-primary/25"
          >
            View shortcuts
          </button>
        </div>
      ) : null}

      {/* Keyboard shortcut help overlay (press '?'). Outside-click + Esc
          to close. Production-grade learning surface — a glance is enough. */}
      {showHelp ? (
        <div
          role="dialog"
          aria-label="Keyboard shortcuts"
          className="fixed inset-0 z-50 grid place-items-center bg-black/70 backdrop-blur-sm"
          onClick={() => setShowHelp(false)}
        >
          <div
            className="w-[520px] max-w-[92vw] rounded-xl border border-glass-border bg-elevated p-4 shadow-2xl shadow-black/40"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <div className="font-display text-base font-semibold text-foreground">Shortcuts</div>
              <button
                type="button"
                onClick={() => setShowHelp(false)}
                aria-label="Close"
                className="rounded p-1 text-text-muted hover:bg-hover-bg hover:text-foreground"
              >
                <X size={14} />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12px]">
              {[
                ["V", "New Video Node"],
                ["I", "New Image Node"],
                ["T", "New Idea Node"],
                ["F", "Fit view"],
                ["/", "Focus Agent composer"],
                ["?", "Toggle this help"],
                ["Esc", "Clear selection / close menus"],
                ["Del / Backspace", "Delete selected"],
                ["Shift + Click", "Add to selection"],
                ["⌘ / Ctrl + Click", "Toggle in selection"],
                ["Shift + Drag empty", "Box-select (marquee)"],
                ["Drag empty", "Pan canvas"],
                ["Shift + Drag node", "Snap to 8px grid"],
                ["⌘ / Ctrl + A", "Select all"],
                ["⌘ / Ctrl + C", "Copy selection"],
                ["⌘ / Ctrl + V", "Paste"],
                ["⌘ / Ctrl + D", "Duplicate"],
                ["⌘ / Ctrl + Z", "Undo move"],
                ["⌘ / Ctrl + Shift + Z", "Redo move"],
                ["⌘ / Ctrl + Wheel", "Zoom"],
                ["← ↑ → ↓", "Navigate to nearest node"],
                ["Shift + ← ↑ → ↓", "Extend selection"],
                ["← / → in Preview", "Prev / next take"],
                ["Drag image handle → draft", "Attach as reference"],
                ["Right-click node", "Context menu"],
                ["Double-click empty canvas", "Quick-add video draft"],
              ].map(([keys, label]) => (
                <div key={keys} className="flex items-center justify-between gap-3 py-0.5">
                  <span className="text-text-secondary">{label}</span>
                  <kbd className="shrink-0 rounded border border-glass-border bg-glass px-1.5 py-0.5 font-mono text-[10px] text-foreground">
                    {keys}
                  </kbd>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {/* Save-state indicator — sits top-right, just inside the right rail.
          Three faces: blue saving / emerald saved-Ns-ago / red failed +
          retry. The chip stays hidden until the first round-trip so an
          empty session doesn't show a noisy default. */}
      {(() => {
        const { inflight, savedAt, failedAt } = saveState;
        if (inflight === 0 && savedAt === null && failedAt === null) return null;
        const isSaving = inflight > 0;
        const hasUnrecoveredFailure = failedAt !== null && (savedAt === null || failedAt > savedAt);
        let label: string;
        if (isSaving) {
          label = "Saving…";
        } else if (hasUnrecoveredFailure) {
          label = "Save failed";
        } else if (savedAt) {
          const ago = Math.max(0, Math.floor((Date.now() - savedAt) / 1000));
          if (ago < 5) label = "Saved";
          else if (ago < 60) label = `Saved ${ago}s ago`;
          else if (ago < 3600) label = `Saved ${Math.floor(ago / 60)}m ago`;
          else label = `Saved ${Math.floor(ago / 3600)}h ago`;
        } else {
          label = "Saved";
        }
        const tone = isSaving
          ? "border-blue-400/40 text-blue-200"
          : hasUnrecoveredFailure
          ? "border-red-400/50 text-red-200"
          : "border-emerald-400/30 text-emerald-200";
        return (
          <div
            role="status"
            aria-live="polite"
            className={`absolute top-4 z-30 inline-flex items-center gap-1.5 rounded-full border bg-elevated/85 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider backdrop-blur-md shadow-2xl shadow-black/40 transition-opacity duration-300 ${tone}`}
            style={{ right: agentCollapsed ? 88 : 412 }}
          >
            {isSaving ? (
              <CloudUpload size={10} className="animate-pulse" aria-hidden="true" />
            ) : hasUnrecoveredFailure ? (
              <CloudUpload size={10} aria-hidden="true" />
            ) : (
              <Check size={10} aria-hidden="true" />
            )}
            <span>{label}</span>
            {hasUnrecoveredFailure && !isSaving ? (
              <button
                type="button"
                onClick={() => {
                  // Re-fetch the project — gives the user a way to force-sync
                  // and verify whether the canvas matches the server.
                  void refreshCurrentProject().catch(() => {
                    // failedAt will refresh from the next response error.
                  });
                }}
                className="ml-1 rounded-full bg-red-400/15 px-1.5 py-0.5 text-[10px] font-semibold text-red-100 hover:bg-red-400/30"
                aria-label="Retry sync"
              >
                Retry
              </button>
            ) : null}
          </div>
        );
      })()}

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
