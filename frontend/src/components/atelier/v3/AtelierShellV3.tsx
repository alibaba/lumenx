"use client";
import axios from "axios";
import * as React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAtelierStore } from "@/store/atelierStore";
import { buildReferenceLinks } from "@/lib/atelierCanvas";
import { getAssetUrl } from "@/lib/utils";
import { Check, ChevronDown, FolderOpen, Pencil, Play, Plus, Trash2, X } from "lucide-react";
import {
  SelectionActionBar,
  BottomNavRail,
  Minimap,
  RightRailV3,
  AgentPanelV3,
  DraftWorkbench,
  LeftRailV3,
  RailPanel,
  type LeftRailMode,
  type ComposerSubmitPayload,
} from "@/components/atelier/v3";
import { ConfirmDialog, PromptDialog } from "@/components/atelier/v3/Dialogs";
import { MiniMarkdown } from "@/components/atelier/v3/MiniMarkdown";
import { AssetLibrary } from "@/components/atelier/v3/AssetLibrary";
import { VIDEO_I2V_MODELS } from "@/lib/modelCatalog";
import {
  // Pure helpers + node renderers extracted to keep this file under
  // control. Behavior is identical; this is a move-only refactor.
  CAND_WIDTH,
  CAND_HEIGHT,
  candidateNodeId,
  candidatePosition,
  isDraftVideo,
  parseCandidateNodeId,
  readCandidates,
  readString,
  readStringArray,
  renderCandidatesAsMediaNodes,
  renderNode,
  selectionKindOf,
} from "@/components/atelier/v3/nodeRenderers";
import {
  api,
  type AtelierNode,
  type AtelierNodePayload,
  type AtelierProject,
  type AtelierApprovalMode,
} from "@/lib/api";

interface EdgeLabel {
  key: string;
  midX: number;
  midY: number;
  text: string;
  tone: "neutral" | "primary" | "success" | "warning" | "error";
}

/** A reference edge is identified by `${fromId}::${toId}::${url}`. */
function refEdgeId(fromId: string, toId: string, url: string): string {
  return `${fromId}::${toId}::${url}`;
}
function parseRefEdgeId(id: string): { fromId: string; toId: string; url: string } | null {
  const parts = id.split("::");
  if (parts.length < 3) return null;
  return { fromId: parts[0], toId: parts[1], url: parts.slice(2).join("::") };
}

interface RefEdgeMidpoint {
  id: string;
  midX: number;
  midY: number;
}

function renderEdges(
  project: AtelierProject | null,
  hoveredNodeId: string | null,
  selectedRefEdgeId: string | null,
  onClickRefEdge: (id: string) => void,
  labelsOut?: EdgeLabel[],
  refEdgeMidpointsOut?: RefEdgeMidpoint[],
): React.ReactNode {
  if (!project) return null;
  const edges: React.ReactNode[] = [];
  const dimUnrelated = !!hoveredNodeId;
  const isRelated = (fromId: string, toId: string) =>
    !hoveredNodeId || fromId === hoveredNodeId || toId === hoveredNodeId;

  // Reference-image → video edges. Each rendered as a <g> with two paths:
  //   - invisible thick hit-area (12px stroke, pointer-events: stroke) so
  //     the edge is clickable without making it visually thick
  //   - visible thin styled path on top
  // Selected edge upgrades to primary tint; hovered/related thicken from
  // 1.5 → 2px. Dim factor on unrelated edges is 0.12.
  const refLinks = buildReferenceLinks(project.nodes);
  for (const link of refLinks) {
    const x1 = link.from.x + (link.from.width || 180);
    const y1 = link.from.y + (link.from.height || 180) / 2;
    const x2 = link.to.x;
    const y2 = link.to.y + (link.to.height || 110) / 2;
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.3);
    const related = isRelated(link.from.id, link.to.id);
    const opacity = dimUnrelated && !related ? 0.12 : 1;
    const eid = refEdgeId(link.from.id, link.to.id, link.url);
    const isSelected = selectedRefEdgeId === eid;
    const stroke = isSelected ? "rgba(100,108,255,0.85)" : "rgba(156,163,175,0.35)";
    const strokeWidth = isSelected ? 2.25 : (related && hoveredNodeId ? 2 : 1.5);
    const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
    edges.push(
      <g
        key={`ref-${eid}`}
        style={{ pointerEvents: "stroke", cursor: "pointer", opacity, transition: "opacity 180ms ease-out" }}
        onPointerDown={(event) => {
          event.stopPropagation();
          onClickRefEdge(eid);
        }}
      >
        {/* Invisible fat hit-area — stroke 12px so the click target is
            generous even at zoom=25%. */}
        <path d={d} fill="none" stroke="rgba(0,0,0,0)" strokeWidth={12} />
        <path
          d={d}
          fill="none"
          stroke={stroke}
          strokeWidth={strokeWidth}
          strokeDasharray={isSelected ? undefined : "2 4"}
          style={{ transition: "stroke 180ms ease-out, stroke-width 180ms" }}
        />
      </g>,
    );
    if (refEdgeMidpointsOut && (isSelected || (related && hoveredNodeId))) {
      refEdgeMidpointsOut.push({
        id: eid,
        midX: (x1 + x2) / 2,
        midY: (y1 + y2) / 2,
      });
    }
    if (labelsOut && hoveredNodeId && related && !isSelected) {
      labelsOut.push({
        key: `ref-label-${eid}`,
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
  const createCommentNode = useAtelierStore((s) => s.createCommentNode);
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

  // Status-transition notifications. When a candidate's status flips
  // from pending/processing to completed or failed, fire a toast so the
  // user gets a passive signal even when looking elsewhere on the canvas.
  // First snapshot is silent (no toast on initial mount); only diffs
  // produced by subsequent refreshes trigger notifications.
  const prevCandStatusRef = useRef<Map<string, string>>(new Map());
  const prevCandSeededRef = useRef(false);
  useEffect(() => {
    if (!project) return;
    const next = new Map<string, string>();
    for (const node of project.nodes) {
      for (const c of readCandidates(node)) next.set(`${node.id}::${c.id}`, c.status);
    }
    if (!prevCandSeededRef.current) {
      prevCandStatusRef.current = next;
      prevCandSeededRef.current = true;
      return;
    }
    const prev = prevCandStatusRef.current;
    let completed = 0;
    let failed = 0;
    next.forEach((status, key) => {
      const old = prev.get(key);
      if (old === status) return;
      if (status === "completed" && (old === "pending" || old === "processing")) completed += 1;
      else if (status === "failed" && (old === "pending" || old === "processing")) failed += 1;
    });
    if (completed > 0) {
      pushToast("success", completed === 1 ? "Take ready" : `${completed} takes ready`);
    }
    if (failed > 0) {
      pushToast("error", failed === 1 ? "Take failed" : `${failed} takes failed`);
    }
    prevCandStatusRef.current = next;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  // Adaptive polling for in-flight candidates. Cadence shifts based on
  // how recently the youngest pending/processing candidate started, so a
  // user who just hit Generate sees fast updates while a long-running
  // batch backs off and stops hammering the backend.
  //
  //   <  30s old  → 1500 ms  (perceived realtime)
  //   <  120s old → 3000 ms  (default)
  //   >= 120s old → 6000 ms  (deep generation)
  //
  // The previous implementation re-ran the effect on every `project`
  // change, which meant every refresh tore down + recreated the interval —
  // a 1500ms tick could be repeatedly cancelled before it ever fired.
  // Now: keep `currentInterval` in a ref, and only restart the timer when
  // the *cadence tier* changes (or in-flight count crosses zero).
  const currentIntervalRef = useRef<number | null>(null);
  const inflightCadenceTier = useMemo(() => {
    const inflight = (project?.nodes ?? []).flatMap((node) =>
      readCandidates(node).filter((c) => c.status === "pending" || c.status === "processing"),
    );
    if (inflight.length === 0) return null;
    const nowSec = Date.now() / 1000;
    const youngestAgeSec = inflight.reduce((min, c) => {
      const start = c.attempt_started_at ?? c.created_at ?? nowSec;
      const age = nowSec - start;
      return age < min ? age : min;
    }, Number.POSITIVE_INFINITY);
    return youngestAgeSec < 30 ? 1500 : youngestAgeSec < 120 ? 3000 : 6000;
  }, [project]);
  useEffect(() => {
    if (inflightCadenceTier === null) {
      // No in-flight candidates; clear any pending tick.
      if (currentIntervalRef.current !== null) {
        window.clearInterval(currentIntervalRef.current);
        currentIntervalRef.current = null;
      }
      return;
    }
    // (Re)start interval only when entering polling or when the cadence
    // tier changed. Effect re-runs whenever inflightCadenceTier changes
    // (memoized over `project`), so this fires once per tier transition.
    if (currentIntervalRef.current !== null) {
      window.clearInterval(currentIntervalRef.current);
    }
    currentIntervalRef.current = window.setInterval(() => {
      void refreshCurrentProject().catch(() => {});
    }, inflightCadenceTier);
    return () => {
      if (currentIntervalRef.current !== null) {
        window.clearInterval(currentIntervalRef.current);
        currentIntervalRef.current = null;
      }
    };
  }, [inflightCadenceTier, refreshCurrentProject]);

  // Local view state
  const [zoom, setZoom] = useState(100);          // percent, 25..300
  const [panX, setPanX] = useState(0);            // world translate x (px in CSS)
  const [panY, setPanY] = useState(0);            // world translate y

  // Hovered node id for the edge-spotlight effect — when set, only edges
  // touching this node stay full-strength; the rest fade.
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

  // Selected reference edge id (`${fromId}::${toId}::${url}`). Set when the
  // user clicks a ref edge on the canvas. Selecting an edge clears node
  // selection, surfaces an inline × delete button at the edge's midpoint,
  // and arms Delete/Backspace to detach the reference.
  const [selectedRefEdgeId, setSelectedRefEdgeId] = useState<string | null>(null);

  // Sequence drag-to-reorder: which clip index is currently being dragged
  // (source) and which index the drop indicator is shown at (target). Both
  // stored in shell because they only matter while a drag is in progress.
  const [seqDragFromIndex, setSeqDragFromIndex] = useState<number | null>(null);
  const [seqDragOverIndex, setSeqDragOverIndex] = useState<number | null>(null);
  // True while a take drag from the canvas is over the strip — drives the
  // primary-tinted drop-zone ring on the strip's outer surface.
  const [seqDropActive, setSeqDropActive] = useState(false);

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
  // Right Rail collapsed pref — remembered across reloads. Lazy initial
  // value so we don't read localStorage during SSR / first server render.
  const [agentCollapsed, setAgentCollapsedRaw] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem("atelier-v3-rail-collapsed") === "1";
    } catch {
      return false;
    }
  });
  const setAgentCollapsed: typeof setAgentCollapsedRaw = (next) => {
    setAgentCollapsedRaw((prev) => {
      const resolved = typeof next === "function"
        ? (next as (p: boolean) => boolean)(prev)
        : next;
      try {
        if (typeof window !== "undefined") {
          window.localStorage.setItem("atelier-v3-rail-collapsed", resolved ? "1" : "0");
        }
      } catch { /* ignore */ }
      return resolved;
    });
  };

  // Active left-rail mode — drives which slide-out panel is visible
  // beside the rail. Sprint B: 6 modes (Add / Assets / Workflows /
  // History / Agent / Sequence). null = no panel open. Toggling the
  // active mode again closes its panel. We don't persist this to
  // localStorage on purpose: opening a panel is a transient action,
  // a creator's "next thing to do", not their preferred default.
  const [activeRailMode, setActiveRailMode] = useState<LeftRailMode | null>(null);
  const toggleRailMode = (mode: LeftRailMode) => {
    setActiveRailMode((cur) => (cur === mode ? null : mode));
  };

  // (Sprint B: Asset Library open state moved into activeRailMode.
  //  The localStorage 'atelier-v3-library-open' key is no longer
  //  written; old values are harmlessly ignored.)
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

  // ── Confirm / prompt dialogs ──────────────────────────────────────────
  // Replaces window.confirm / window.prompt so destructive actions and
  // renames stay inside the cinematic frame. Each dialog state slot holds
  // both the visual props and the resolved-user-input callback. Helpers
  // `askConfirm` / `askPrompt` are pseudo-imperative — open the dialog,
  // resolve via the user's choice. Unlike browser dialogs they do not
  // block the event loop, so callers must continue work inside `onConfirm`
  // / `onSubmit` rather than reading a return value.
  const [confirmDialogState, setConfirmDialogState] = useState<{
    title: string;
    body?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    tone?: "danger" | "primary";
    onConfirm: () => void;
  } | null>(null);
  const [promptDialogState, setPromptDialogState] = useState<{
    title: string;
    description?: string;
    initialValue?: string;
    placeholder?: string;
    submitLabel?: string;
    multiline?: boolean;
    allowEmpty?: boolean;
    onSubmit: (value: string) => void;
  } | null>(null);
  const askConfirm = (opts: NonNullable<typeof confirmDialogState>) =>
    setConfirmDialogState(opts);
  const askPrompt = (opts: NonNullable<typeof promptDialogState>) =>
    setPromptDialogState(opts);

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
    askConfirm({
      title: ids.length === 1 ? "Delete this node?" : `Delete ${ids.length} nodes?`,
      body: "This cannot be undone.",
      confirmLabel: "Delete",
      tone: "danger",
      onConfirm: () => {
        void runDeleteSelection(ids);
      },
    });
  };

  const runDeleteSelection = async (ids: string[]) => {
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

      // Cmd/Ctrl + \ = collapse / expand the Right Rail. Matches VS Code's
      // sidebar toggle muscle memory.
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key === "\\") {
        e.preventDefault();
        setAgentCollapsed((c) => !c);
        return;
      }

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
          // If the user has text selected in the page (e.g., copying a
          // toast message, error, or any visible label), let the browser's
          // native copy run instead of intercepting with our node-clipboard
          // logic. Otherwise the user gets the wrong text plus a confusing
          // "Nothing to copy" toast.
          const sel = window.getSelection?.();
          if (sel && sel.toString().trim().length > 0) {
            return; // browser handles copy
          }
          // Copy to private clipboard (not OS clipboard — node payloads
          // aren't useful there). Quiet on success: the next paste is the
          // signal users want.
          e.preventDefault();
          const count = copySelection();
          if (count === 0) {
            // No text selection AND nothing on canvas to copy. Stay quiet
            // — the toast was just noise for a no-op shortcut press.
            return;
          }
          pushToast("info", count === 1 ? "1 node copied" : `${count} nodes copied`);
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
      // Cmd/Ctrl + Arrow: nudge every selected real node by 1px (or 8px
      // with Shift). Tiny precision tool for aligning nodes by hand
      // without dragging. Goes through the same move-history pipeline as
      // a real drag so undo/redo restore atomically.
      if (arrowKey && (e.metaKey || e.ctrlKey)) {
        const proj = useAtelierStore.getState().currentProject;
        if (!proj || allSelectedIds.size === 0) return;
        e.preventDefault();
        const step = e.shiftKey ? 8 : 1;
        const dx = arrowKey === "right" ? step : arrowKey === "left" ? -step : 0;
        const dy = arrowKey === "down" ? step : arrowKey === "up" ? -step : 0;
        const store = useAtelierStore.getState();
        const entries: MoveEntry[] = [];
        const ids = Array.from(allSelectedIds).filter((id) => !parseCandidateNodeId(id));
        for (const id of ids) {
          const n = proj.nodes.find((x) => x.id === id);
          if (!n) continue;
          const nextX = n.x + dx;
          const nextY = n.y + dy;
          entries.push({ nodeId: n.id, prevX: n.x, prevY: n.y, nextX, nextY });
          store.moveNodeLocal(n.id, nextX, nextY);
          void store.commitNodePosition(n.id, nextX, nextY).catch(() => {});
        }
        if (entries.length > 0) pushHistory({ kind: "move", entries });
        return;
      }
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
        case "c":
          e.preventDefault();
          void createCommentNode()
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
        case "a":
          // Toggle the Assets rail mode. Pure UX shortcut, no canvas
          // state mutated. Cmd/Ctrl+A is select-all and is handled in
          // the modifier branch above; bare A only fires unmodified.
          e.preventDefault();
          setActiveRailMode((cur) => (cur === "assets" ? null : "assets"));
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
          if (selectedRefEdgeId) {
            e.preventDefault();
            setSelectedRefEdgeId(null);
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
          // Edge selected → detach the reference and clear edge selection.
          // Otherwise fall through to node delete.
          if (selectedRefEdgeId) {
            if ((e.target as HTMLElement)?.tagName !== "BODY") return;
            e.preventDefault();
            const parsed = parseRefEdgeId(selectedRefEdgeId);
            if (parsed) {
              const { fromId, toId, url } = parsed;
              setSelectedRefEdgeId(null);
              void useAtelierStore.getState()
                .detachReferenceNode(toId, url, fromId)
                .then(() => pushToast("info", "Reference detached"))
                .catch((err: unknown) => pushToast("error", `Detach failed: ${err instanceof Error ? err.message : String(err)}`));
            }
            break;
          }
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

  // Auto-enter idea/comment editing whenever a sticky-text node becomes
  // selected — saves a click. The textarea overlay (rendered later in
  // JSX) takes focus.
  useEffect(() => {
    if (!project || !selectedNodeId) {
      // Selection cleared — close any open editor (and persist on close).
      if (editingIdeaId) setEditingIdeaId(null);
      return;
    }
    const sel = project.nodes.find((n) => n.id === selectedNodeId);
    if (!sel || (sel.type !== "idea" && sel.type !== "comment")) {
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

  // Multi-selection alignment + distribution. Operates on the real-node
  // members of allSelectedIds (virtual candidates skip — their bbox is
  // derived). Each op routes through commitNodePosition so changes
  // persist + share the move-history pipeline (Cmd+Z restores all at
  // once). Distribution requires ≥3 nodes; alignment requires ≥2.
  const [showAlignMenu, setShowAlignMenu] = useState(false);
  const applyAlign = (op:
    | "left" | "center-h" | "right"
    | "top" | "center-v" | "bottom"
    | "distribute-h" | "distribute-v"
  ) => {
    const proj = useAtelierStore.getState().currentProject;
    if (!proj) return;
    const ids = Array.from(allSelectedIds).filter((id) => !parseCandidateNodeId(id));
    const members = ids
      .map((id) => proj.nodes.find((n) => n.id === id))
      .filter((n): n is AtelierNode => !!n);
    if (members.length < 2) return;
    if ((op === "distribute-h" || op === "distribute-v") && members.length < 3) {
      pushToast("info", "Need at least 3 nodes to distribute.");
      return;
    }
    let minX = Infinity, minY = Infinity, maxRight = -Infinity, maxBottom = -Infinity;
    for (const n of members) {
      const w = n.width || 240;
      const h = n.height || 110;
      if (n.x < minX) minX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.x + w > maxRight) maxRight = n.x + w;
      if (n.y + h > maxBottom) maxBottom = n.y + h;
    }
    const centerX = (minX + maxRight) / 2;
    const centerY = (minY + maxBottom) / 2;
    const entries: MoveEntry[] = [];
    const targets: Array<{ id: string; x: number; y: number }> = [];

    if (op === "distribute-h") {
      // Sort by left edge, anchor the leftmost + rightmost, evenly space
      // the cumulative gaps between the rest.
      const sorted = [...members].sort((a, b) => a.x - b.x);
      const totalSpan = sorted[sorted.length - 1].x - sorted[0].x;
      const widthsSum = sorted.slice(1, -1).reduce((s, n) => s + (n.width || 240), 0);
      const gapsCount = sorted.length - 1;
      // Total content widths between first and last for distribution
      const innerSpan = totalSpan;
      // Equal-gap distribution: place each interior node so gaps between
      // *rights* are equal — simpler approach: just distribute the x
      // coordinates evenly between first.x and last.x.
      void widthsSum;
      void innerSpan;
      void gapsCount;
      const firstX = sorted[0].x;
      const lastX = sorted[sorted.length - 1].x;
      for (let i = 0; i < sorted.length; i++) {
        const t = i / (sorted.length - 1);
        const nx = Math.round(firstX + (lastX - firstX) * t);
        targets.push({ id: sorted[i].id, x: nx, y: sorted[i].y });
      }
    } else if (op === "distribute-v") {
      const sorted = [...members].sort((a, b) => a.y - b.y);
      const firstY = sorted[0].y;
      const lastY = sorted[sorted.length - 1].y;
      for (let i = 0; i < sorted.length; i++) {
        const t = i / (sorted.length - 1);
        const ny = Math.round(firstY + (lastY - firstY) * t);
        targets.push({ id: sorted[i].id, x: sorted[i].x, y: ny });
      }
    } else {
      for (const n of members) {
        const w = n.width || 240;
        const h = n.height || 110;
        let nx = n.x;
        let ny = n.y;
        switch (op) {
          case "left":     nx = minX; break;
          case "center-h": nx = Math.round(centerX - w / 2); break;
          case "right":    nx = Math.round(maxRight - w); break;
          case "top":      ny = minY; break;
          case "center-v": ny = Math.round(centerY - h / 2); break;
          case "bottom":   ny = Math.round(maxBottom - h); break;
        }
        targets.push({ id: n.id, x: nx, y: ny });
      }
    }

    const store = useAtelierStore.getState();
    for (const t of targets) {
      const orig = members.find((m) => m.id === t.id);
      if (!orig) continue;
      if (orig.x === t.x && orig.y === t.y) continue;
      entries.push({ nodeId: t.id, prevX: orig.x, prevY: orig.y, nextX: t.x, nextY: t.y });
      store.moveNodeLocal(t.id, t.x, t.y);
      void store.commitNodePosition(t.id, t.x, t.y).catch(() => {});
    }
    if (entries.length > 0) pushHistory({ kind: "move", entries });
    setShowAlignMenu(false);
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

  // Resize drag — selected media nodes (image / video, real not virtual)
  // get four corner handles. Pointer-down on a handle starts a resize
  // drag that updates width/height (and x/y when dragging from a top or
  // left handle, since the opposite edge is anchored). Mirrors the
  // {move,group}-drag patterns: optimistic local update + commit on up.
  const resizeDragRef = useRef<{
    nodeId: string;
    corner: "tl" | "tr" | "bl" | "br";
    startX: number;
    startY: number;
    startW: number;
    startH: number;
    startPointerX: number;
    startPointerY: number;
    moved: boolean;
  } | null>(null);
  const [, setResizeTick] = useState(0);

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

  // Branch drag — from a completed take's right-edge handle, drag a bezier
  // (Branch drag has been folded into the unified handlePortDragOut path —
  //  no separate ref/state needed.)

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

    // Plain empty-canvas click clears any selection (nodes + edge).
    selectNode(null);
    if (extraSelectedIds.size > 0) setExtraSelectedIds(new Set());
    if (selectedRefEdgeId) setSelectedRefEdgeId(null);
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

  // Unified hover-port outgoing drag. Replaces the two specialized
  // selection-state handles (image connect, take branch). Works on every
  // node type — the dispatch decides what the drop does:
  //   - Source = image w/ media → drop on draft attaches as ref;
  //                                drop on canvas creates a new draft pre-
  //                                attached to the image at the drop point.
  //   - Source = completed take → branches into a new draft, dropped at
  //                                the cursor (existing branch flow).
  //   - Source = anything else → toast "coming soon" (idea/comment/draft
  //     don't have well-defined outgoing semantics yet).
  const handlePortDragOut = (
    event: React.PointerEvent,
    source: AtelierNode,
    handleScreenX: number,
    handleScreenY: number,
  ) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.preventDefault();
    const parsedCand = parseCandidateNodeId(source.id);
    const isTakeSource = !!parsedCand;
    const isImageSource = source.type === "image" && (source.media_urls?.length ?? 0) > 0;

    if (!isTakeSource && !isImageSource) {
      // Should never fire — ports are only rendered on supported source
      // types. Belt-and-braces silent return rather than a toast: the
      // user clicked an affordance we shouldn't have shown.
      return;
    }

    connectDragRef.current = {
      sourceNodeId: source.id,
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
      // Highlight only meaningful drop targets — for image sources, that's
      // a draft video; takes don't snap onto a target (they create a new
      // draft at the cursor regardless).
      if (isImageSource) {
        const el = document.elementFromPoint(ev.clientX, ev.clientY) as HTMLElement | null;
        const nodeEl = el?.closest("[data-atelier-node]") as HTMLElement | null;
        const targetId = nodeEl?.dataset.atelierNode ?? null;
        const valid = (() => {
          if (!targetId || targetId === source.id) return null;
          const target = useAtelierStore.getState().currentProject?.nodes.find((n) => n.id === targetId);
          return target && isDraftVideo(target) ? target.id : null;
        })();
        setHoveredConnectTargetId(valid);
      }
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

      const dragged =
        Math.abs(ev.clientX - drag.startScreenX) + Math.abs(ev.clientY - drag.startScreenY);
      // Tiny movement = treat as a click. Don't fire any side-effect.
      if (dragged < 8) return;

      const el = document.elementFromPoint(ev.clientX, ev.clientY) as HTMLElement | null;
      const nodeEl = el?.closest("[data-atelier-node]") as HTMLElement | null;
      const targetId = nodeEl?.dataset.atelierNode ?? null;
      const target = targetId
        ? useAtelierStore.getState().currentProject?.nodes.find((n) => n.id === targetId)
        : null;
      const rect = mainRef.current?.getBoundingClientRect();
      const offX = rect?.left ?? 0;
      const offY = rect?.top ?? 0;
      const dropWorldX = (ev.clientX - offX - panX) / zoomFactor;
      const dropWorldY = (ev.clientY - offY - panY) / zoomFactor;

      // Source = take → always branch (existing behavior). Drop on canvas
      // anchors the new draft at the cursor; drop on a node still branches
      // but lets the auto-layout placement stand.
      if (isTakeSource && parsedCand) {
        void useAtelierStore.getState()
          .branchFromCandidate(parsedCand.parentId, parsedCand.candidateId)
          .then(async (newDraft) => {
            if (!target) {
              const w = newDraft.width || 240;
              const h = newDraft.height || 110;
              const tx = Math.round(dropWorldX - w / 2);
              const ty = Math.round(dropWorldY - h / 2);
              useAtelierStore.getState().moveNodeLocal(newDraft.id, tx, ty);
              await useAtelierStore.getState().commitNodePosition(newDraft.id, tx, ty).catch(() => {});
            }
            pushToast("success", "Branched · new draft created");
          })
          .catch((err: unknown) =>
            pushToast("error", `Branch failed: ${err instanceof Error ? err.message : String(err)}`),
          );
        return;
      }

      // Source = image
      if (isImageSource) {
        if (target && isDraftVideo(target)) {
          // Attach to an existing draft target (the typical "use as ref"
          // flow but performed via drag).
          void useAtelierStore.getState()
            .attachReferenceNode(target.id, source.id)
            .then(() => pushToast("success", "Reference attached"))
            .catch((err: unknown) =>
              pushToast("error", `Attach failed: ${err instanceof Error ? err.message : String(err)}`),
            );
          return;
        }
        if (!target) {
          // Drop on empty canvas → create a new draft pre-attached to this
          // image. The new draft lands centered on the cursor so the user
          // sees confirmation at the drop point.
          void useAtelierStore.getState()
            .createVideoNode()
            .then(async (newDraft) => {
              const w = newDraft.width || 240;
              const h = newDraft.height || 110;
              const tx = Math.round(dropWorldX - w / 2);
              const ty = Math.round(dropWorldY - h / 2);
              useAtelierStore.getState().moveNodeLocal(newDraft.id, tx, ty);
              await useAtelierStore.getState().commitNodePosition(newDraft.id, tx, ty).catch(() => {});
              await useAtelierStore.getState().attachReferenceNode(newDraft.id, source.id);
              pushToast("success", "New draft created and image attached");
            })
            .catch((err: unknown) =>
              pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`),
            );
          return;
        }
        pushToast("info", "Drop on a draft video, or on empty canvas to create one.");
        return;
      }
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
    if (selectedRefEdgeId) setSelectedRefEdgeId(null);

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
  //
  // The anchor is derived from the *DOM rect* of the selected node, not
  // recomputed from world coordinates. Pan / zoom / right-rail collapse
  // can leave the math out of sync with what's actually painted; reading
  // (Sprint A: floating Composer retired in favor of inline DraftWorkbench.
  //  The composerAnchor measurement + viewport calc that powered popup
  //  positioning are no longer needed — the workbench renders inside the
  //  world transform at the node's own coords.)

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
    // All video-output tabs share one backend path — what changes is the
    // ref configuration the user has set up on the draft. T2V is just
    // I2V with refs cleared; R2V is I2V with multi-ref; V2V is I2V with
    // a video ref. The composer's tab is therefore *advisory* (drives
    // copy + model filter) rather than dispatching different endpoints.
    const isVideoTab =
      payload.tab === "I2V" ||
      payload.tab === "T2V" ||
      payload.tab === "R2V" ||
      payload.tab === "V2V";
    const isImageTab = payload.tab === "T2I" || payload.tab === "I2I";

    if (isVideoTab && node.type === "video" && node.status === "draft") {
      const existingRefs = readStringArray(
        (node.data as { reference_image_urls?: unknown })?.reference_image_urls,
      );
      const batch = parseInt(payload.count, 10);

      // ── Parse @mentions from the prompt and auto-attach matching image
      // nodes. Mentions act as a typing-speed shortcut for the visual
      // attach flow — typing "@hero" implicitly adds the image node
      // labeled "hero" as a reference, no need to hunt the connect
      // handle. We only auto-attach IMAGE nodes; @-mentioning a draft
      // or an idea is informational (the prompt mentions it, but we
      // don't try to attach a draft as a ref to itself).
      // Mention resolution — try in priority: exact → case-insensitive
      // exact → prefix (case-insensitive) → contains. The first
      // priority that yields a unique image-with-media wins. This is
      // forgiving enough that the user typing `@hero` finds a node
      // titled `Hero shot` without case-matching, while still being
      // strict enough that `@a` doesn't accidentally pick the first
      // node alphabetically.
      const allNodes = project?.nodes ?? [];
      const candidatePool = allNodes.filter(
        (n) => n.type === "image" && (n.media_urls?.length ?? 0) > 0,
      );
      const labelsOf = (n: AtelierNode): string[] => {
        const title = n.title || "";
        const intent = readString((n.data as { intent?: unknown })?.intent) || "";
        const body = readString((n.data as { body?: unknown })?.body) || "";
        return [title, intent, body.slice(0, 40)].filter((s) => s.length > 0);
      };
      const resolveMention = (query: string): AtelierNode | null => {
        const q = query.trim();
        const ql = q.toLowerCase();
        const tries: Array<(labels: string[]) => boolean> = [
          (labels) => labels.some((l) => l === q),
          (labels) => labels.some((l) => l.toLowerCase() === ql),
          (labels) => labels.some((l) => l.toLowerCase().startsWith(ql)),
          (labels) => labels.some((l) => l.toLowerCase().includes(ql)),
        ];
        for (const test of tries) {
          const matches = candidatePool.filter((n) => test(labelsOf(n)));
          if (matches.length === 1) return matches[0];
          // Multiple hits at this priority → ambiguous, escalate to next
          // priority. If even contains-search gives multiple matches,
          // bail (better silence than auto-attaching the wrong one).
        }
        return null;
      };

      const mentionMatches = Array.from(payload.prompt.matchAll(/@([^\s@]+)/g));
      const newRefUrls: string[] = [];
      for (const m of mentionMatches) {
        const target = resolveMention(m[1]);
        if (!target || !target.media_urls) continue;
        for (const url of target.media_urls) {
          if (!existingRefs.includes(url) && !newRefUrls.includes(url)) {
            newRefUrls.push(url);
          }
        }
      }
      const finalRefs = [...existingRefs, ...newRefUrls];

      // Persist the chosen model before kicking off generation — even if
      // the request fails, the user clearly intended this model and we
      // shouldn't re-default to Wan 2.7 on the next draft.
      rememberModel(project?.id, payload.modelLabel);

      // Translate the Composer's `advanced` payload into the backend's
      // `params` shape. We use snake_case for the wire format (matches
      // existing model.runtime keys) and skip undefined/empty values so
      // the backend's defaults still apply when the user hasn't touched
      // a knob.
      const adv = payload.advanced ?? {};
      const advParams: Record<string, unknown> = {};
      if (adv.negativePrompt && adv.negativePrompt.trim().length > 0) {
        advParams.negative_prompt = adv.negativePrompt.trim();
      }
      if (typeof adv.seed === "number") advParams.seed = adv.seed;
      if (typeof adv.cfgScale === "number") advParams.cfg_scale = adv.cfgScale;
      if (adv.mode) advParams.mode = adv.mode;
      if (adv.movementAmplitude) advParams.movement_amplitude = adv.movementAmplitude;
      if (typeof adv.sound === "boolean") advParams.sound = adv.sound;

      void useAtelierStore.getState()
        .createVideoCandidates(node.id, {
          prompt: payload.prompt,
          model: payload.modelLabel,
          reference_image_urls: finalRefs,
          batch_size: Number.isFinite(batch) && batch > 0 ? batch : 4,
          params: advParams,
        })
        .then(() => {
          if (newRefUrls.length > 0) {
            pushToast(
              "success",
              `Generating ${batch || 4} candidates · auto-attached ${newRefUrls.length} mention${newRefUrls.length === 1 ? "" : "s"}`,
            );
          } else {
            pushToast("success", `Generating ${batch || 4} candidates…`);
          }
        })
        .catch((err: unknown) => pushToast("error", `Generate failed: ${err instanceof Error ? err.message : String(err)}`));
      return;
    }
    if (isImageTab) {
      pushToast(
        "info",
        "Image generation (T2I / I2I) needs an image draft node — drop a new image node from the toolbar, then run.",
      );
      return;
    }
    if (payload.tab === "Audio") {
      pushToast(
        "info",
        "Audio generation isn't wired yet — coming with the music / voice integration.",
      );
      return;
    }
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
      askConfirm({
        title: `Delete this ${node.type} node?`,
        body: "This cannot be undone.",
        confirmLabel: "Delete",
        tone: "danger",
        onConfirm: () => {
          void deleteAtelierNode(node.id)
            .then(() => pushToast("info", "Node deleted"))
            .catch((err: unknown) => pushToast("error", `Delete failed: ${err instanceof Error ? err.message : String(err)}`));
        },
      });
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
      // Two ways to attach:
      //   1. This action bar item → opens a picker modal listing every
      //      draft video node so the user clicks the target.
      //   2. The right-edge connect handle → drag onto a draft directly.
      // Both go through store.attachReferenceNode, so the result is the
      // same — the picker is the keyboard / mouse-only baseline.
      if (node.type !== "image") {
        pushToast("info", "Use-as-reference is for image nodes — pick an image first.");
        return;
      }
      if (!node.media_urls || node.media_urls.length === 0) {
        pushToast("info", "Upload an image into this node first, then attach it.");
        return;
      }
      const draftCount = (project?.nodes ?? []).filter(isDraftVideo).length;
      if (draftCount === 0) {
        pushToast(
          "info",
          "No draft video nodes yet — press V (or the toolbar Video button) to create one, then attach.",
        );
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

  // First-run onboarding tour — 3 sequential coachmark cards. Persists
  // "seen" across refreshes via localStorage so we don't nag returning
  // users. tourStep === null means inactive (returning user or finished).
  const [tourStep, setTourStep] = useState<number | null>(null);
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const seen = window.localStorage.getItem("atelier-v3-onboarding-seen");
      if (!seen) {
        const t = window.setTimeout(() => setTourStep(0), 1200);
        return () => window.clearTimeout(t);
      }
    } catch {
      /* localStorage may be unavailable; fall back to no tour */
    }
  }, []);
  const dismissOnboarding = () => {
    setTourStep(null);
    try { window.localStorage.setItem("atelier-v3-onboarding-seen", "1"); } catch { /* ignore */ }
  };
  const advanceOnboarding = () => {
    setTourStep((s) => {
      if (s === null) return null;
      if (s >= 2) {
        try { window.localStorage.setItem("atelier-v3-onboarding-seen", "1"); } catch { /* ignore */ }
        return null;
      }
      return s + 1;
    });
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
    // Accept either OS file drops OR atelier-asset drags from the library.
    const types = Array.from(event.dataTransfer.types);
    if (!types.includes("Files") && !types.includes("application/x-atelier-asset")) return;
    event.preventDefault();
    if (types.includes("Files") && !isDraggingFileOver) setIsDraggingFileOver(true);
  };
  const handleDragLeave = (event: React.DragEvent) => {
    if (event.target === event.currentTarget) setIsDraggingFileOver(false);
  };
  const handleDrop = async (event: React.DragEvent) => {
    const types = Array.from(event.dataTransfer.types);

    // ── Library asset drop ──────────────────────────────────────────
    // When the user drags an image asset from the AssetLibrary onto a
    // draft, attach the asset's image as a reference. Drops on empty
    // canvas no-op for v1 (the library asset already exists; cloning
    // it would just duplicate a node).
    if (types.includes("application/x-atelier-asset")) {
      event.preventDefault();
      const data = event.dataTransfer.getData("application/x-atelier-asset");
      if (!data) return;
      let parsed: { nodeId: string; kind: string };
      try {
        parsed = JSON.parse(data) as { nodeId: string; kind: string };
      } catch {
        return;
      }
      // Walk up from the drop target to find a draft node.
      const el = event.target as HTMLElement | null;
      const targetEl = el?.closest("[data-atelier-node]") as HTMLElement | null;
      const targetId = targetEl?.dataset.atelierNode ?? null;
      if (!targetId) return; // no-op on empty canvas
      const target = useAtelierStore.getState().currentProject?.nodes.find((n) => n.id === targetId);
      if (!target || !isDraftVideo(target)) {
        pushToast("info", "Drop on a draft video node to attach.");
        return;
      }
      if (parsed.kind !== "image") {
        pushToast("info", "Only image assets can be attached as references.");
        return;
      }
      try {
        await useAtelierStore.getState().attachReferenceNode(target.id, parsed.nodeId);
        pushToast("success", "Reference attached");
      } catch (err: unknown) {
        pushToast("error", `Attach failed: ${err instanceof Error ? err.message : String(err)}`);
      }
      return;
    }

    // ── OS file drop ───────────────────────────────────────────────
    if (!types.includes("Files")) return;
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
      {/* Asset Library — left-edge collapsible drawer. Toggle via the
          edge button or the `A` shortcut. Categorisation pill writes to
          node.data.category (character / scene / prop). */}
      {/* AssetLibrary — controlled by the left rail's Assets mode.
          hideCollapsedHandle drops the standalone edge handle (the rail
          is the canonical entry point now); leftOffsetPx=72 docks the
          panel against the rail (rail width 56 + 16 gap). The legacy
          libraryOpen / `A` shortcut still works because A opens it
          directly via setActiveRailMode below. */}
      <AssetLibrary
        nodes={project?.nodes ?? []}
        open={activeRailMode === "assets"}
        onToggle={() => setActiveRailMode((cur) => (cur === "assets" ? null : "assets"))}
        hideCollapsedHandle
        leftOffsetPx={72}
        onCycleCategory={(nodeId, next) => {
          const node = project?.nodes.find((n) => n.id === nodeId);
          if (!node) return;
          const data = { ...(node.data ?? {}), category: next };
          if (next === null) {
            delete (data as Record<string, unknown>).category;
          }
          void useAtelierStore.getState()
            .updateNode(nodeId, { data })
            .catch((err: unknown) =>
              pushToast("error", `Set category failed: ${err instanceof Error ? err.message : String(err)}`),
            );
        }}
      />

      {/* LeftRailV3 — vertical mode rail at the left edge (replaces the
          old top horizontal Toolbar). Six modes drive the slide-out
          panel; undo/redo/help live at the rail's bottom. */}
      <LeftRailV3
        activeMode={activeRailMode}
        onModeToggle={(mode) => {
          // Agent mode is special: it doesn't have its own slide-out
          // panel here — it just toggles the always-present right rail.
          // Same for Sequence (toggles the bottom strip). The rail's
          // active highlight still tracks the user's last pick so they
          // can see "I'm in Agent mode" at a glance.
          if (mode === "agent") {
            setAgentCollapsed((c) => !c);
            setActiveRailMode((cur) => (cur === "agent" ? null : "agent"));
            return;
          }
          toggleRailMode(mode);
        }}
        onUndo={undo}
        onRedo={redo}
        canUndo={undoStackRef.current.length > 0}
        canRedo={redoStackRef.current.length > 0}
        onHelp={() => setShowHelp(true)}
      />

      {/* Add panel — slide-out beside the rail with creator-facing
          shortcuts. Mirrors LibTV's add-node taxonomy (Codex doc §3.4):
          Image / Video / Idea / Comment / Upload / From Library. */}
      <RailPanel
        open={activeRailMode === "add"}
        title="Add to canvas"
        tag="Atelier · Add · No 001"
        onClose={() => setActiveRailMode(null)}
      >
        <ul className="space-y-1 p-2">
          {[
            {
              key: "video",
              title: "Video draft",
              shortcut: "V",
              desc: "Compose with a model + refs.",
              onPick: () => {
                void handleCreateVideo();
                setActiveRailMode(null);
              },
            },
            {
              key: "image",
              title: "Image",
              shortcut: "I",
              desc: "Upload or generate a reference.",
              onPick: () => {
                void createEmptyImageDraft()
                  .then(() => pushToast("info", "Image node added — select Upload from its action bar."))
                  .catch((err: unknown) =>
                    pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`),
                  );
                setActiveRailMode(null);
              },
            },
            {
              key: "idea",
              title: "Idea",
              shortcut: "T",
              desc: "Capture a beat or a vibe.",
              onPick: () => {
                void createIdeaNode()
                  .then((node) => {
                    setEditingIdeaId(node.id);
                    setEditingIdeaBody((node.data as { body?: string })?.body ?? "");
                  })
                  .catch((err: unknown) =>
                    pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`),
                  );
                setActiveRailMode(null);
              },
            },
            {
              key: "comment",
              title: "Comment",
              shortcut: "C",
              desc: "Pin a note on the canvas.",
              onPick: () => {
                void createCommentNode()
                  .then((node) => {
                    setEditingIdeaId(node.id);
                    setEditingIdeaBody((node.data as { body?: string })?.body ?? "");
                  })
                  .catch((err: unknown) =>
                    pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`),
                  );
                setActiveRailMode(null);
              },
            },
            {
              key: "upload",
              title: "Upload file",
              shortcut: "Drop",
              desc: "Drop image / video files anywhere.",
              onPick: () => {
                imageNodeIdForUploadRef.current = null;
                fileInputRef.current?.click();
                setActiveRailMode(null);
              },
            },
          ].map((opt) => (
            <li key={opt.key}>
              <button
                type="button"
                onClick={opt.onPick}
                className="group flex w-full items-center gap-3 rounded-md border border-white/6 bg-black/20 px-3 py-2 text-left transition-all hover:-translate-y-[1px] hover:border-primary/35 hover:bg-primary/[0.06]"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-display text-[13px] font-medium tracking-[-0.005em] text-foreground/95">
                    {opt.title}
                  </div>
                  <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-text-muted/85">
                    {opt.desc}
                  </div>
                </div>
                <kbd className="rounded border border-white/8 bg-black/35 px-1.5 py-[1px] font-mono text-[10px] tracking-tight text-text-muted/85 group-hover:border-primary/35 group-hover:text-primary/85">
                  {opt.shortcut}
                </kbd>
              </button>
            </li>
          ))}
        </ul>
      </RailPanel>

      {/* Workflows panel — placeholder for Sprint C. The slot exists so
          the rail's affordance isn't a dead button. Real workflow
          browser (curated local presets) lands next sprint per Codex
          doc §4.7 / §7.7. */}
      <RailPanel
        open={activeRailMode === "workflows"}
        title="Workflows"
        tag="Atelier · Workflows · No 001"
        onClose={() => setActiveRailMode(null)}
      >
        <div className="px-4 py-6 text-center">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.28em] text-text-muted/85">
            Coming next sprint
          </div>
          <p className="text-[12px] leading-[1.55] text-text-secondary/95">
            A curated browser of generation recipes — story video, character
            reference, product reveal, motion presets. Each template will
            instantiate a small group of nodes + reference wiring + model
            defaults so you can start with a working setup instead of a
            blank canvas.
          </p>
        </div>
      </RailPanel>

      {/* History panel — placeholder for Sprint D. Real timeline browser
          (project event log, saved versions, agent turns) lands later. */}
      <RailPanel
        open={activeRailMode === "history"}
        title="History"
        tag="Atelier · History · No 001"
        onClose={() => setActiveRailMode(null)}
      >
        <div className="px-4 py-6 text-center">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.28em] text-text-muted/85">
            Coming later
          </div>
          <p className="text-[12px] leading-[1.55] text-text-secondary/95">
            Project event log + saved versions — see every generation,
            every approval, every branch your project has gone through.
            Roll back to any point.
          </p>
        </div>
      </RailPanel>

      {/* Project picker — sits below Toolbar (top-16 left-4). Pill shows
          current project; click opens a popover with the project list +
          a "New" CTA. Hidden when there are no projects loaded yet. */}
      {project ? (
        <div className="absolute left-4 top-[60px] z-30">
          <button
            type="button"
            aria-label="Switch project"
            aria-expanded={showProjectPicker}
            onClick={() => setShowProjectPicker((v) => !v)}
            className="btn-tip inline-flex items-center gap-2 rounded-full border border-white/8 bg-[#141416]/92 px-2.5 py-[5px] text-foreground shadow-[inset_0_1px_0_0_rgba(255,255,255,0.05)] transition-colors hover:bg-[#1a1a1d]"
            data-tip="Switch project"
          >
            <FolderOpen size={11} className="text-text-muted/85" aria-hidden="true" />
            <span className="max-w-[160px] truncate font-display text-[12px] font-medium tracking-[-0.005em]">
              {project.title || "Untitled"}
            </span>
            <ChevronDown size={11} className="text-text-muted/70" aria-hidden="true" />
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
                className="absolute left-0 top-10 z-[35] w-[300px] origin-top rounded-md border border-white/8 bg-[#141416]/96 p-1 shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
              >
                <div className="flex items-center justify-between border-b border-dashed border-white/8 px-2.5 py-1.5 font-mono text-[9px] font-medium uppercase tracking-[0.28em] text-text-muted/85">
                  <span>Atelier · Projects</span>
                  <span className="font-display text-[11px] tracking-tight text-text-muted/65">
                    {String(projects.length).padStart(2, "0")}
                  </span>
                </div>
                {/* Current-project description card. Editable inline via the
                    pencil — opens the multiline PromptDialog. Reads italic
                    when present, mono caps placeholder when blank. */}
                <div className="group/desc relative border-b border-white/6 px-2.5 py-2">
                  {project.description ? (
                    <MiniMarkdown
                      source={project.description}
                      className="pr-6 font-display text-[12px] italic leading-[1.5] tracking-tight text-text-secondary/95"
                    />
                  ) : (
                    <p className="pr-6 font-mono text-[9.5px] uppercase tracking-[0.24em] text-text-muted/75">
                      No description · click pencil to add
                    </p>
                  )}
                  <button
                    type="button"
                    aria-label="Edit project description"
                    data-tip="Edit description"
                    onClick={(e) => {
                      e.stopPropagation();
                      askPrompt({
                        title: "Project description",
                        description: `For "${project.title || "Untitled"}". Markdown subset: **bold** *italic* \`code\` [link](url).`,
                        initialValue: project.description || "",
                        placeholder: "What is this project about?",
                        submitLabel: "Save",
                        multiline: true,
                        allowEmpty: true,
                        onSubmit: (next) => {
                          if (next === (project.description || "")) return;
                          void api
                            .updateAtelierProject(project.id, { description: next })
                            .then(async () => {
                              await useAtelierStore.getState().loadProjects();
                              pushToast("success", "Description saved");
                            })
                            .catch((err: unknown) =>
                              pushToast("error", `Save failed: ${err instanceof Error ? err.message : String(err)}`),
                            );
                        },
                      });
                    }}
                    className="btn-tip absolute right-1 top-1.5 rounded p-1 text-text-muted opacity-0 transition-opacity hover:bg-hover-bg hover:text-foreground group-hover/desc:opacity-100"
                  >
                    <Pencil size={10} aria-hidden="true" />
                  </button>
                </div>
                <ul className="max-h-[280px] overflow-y-auto">
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
                          className={`flex w-full items-center justify-between gap-2 rounded px-2.5 py-2 text-left transition-colors ${
                            isCurrent
                              ? "bg-primary/[0.08] text-foreground"
                              : "text-text-secondary hover:bg-white/[0.04] hover:text-foreground"
                          }`}
                        >
                          <div className="min-w-0">
                            <div className="truncate font-display text-[13px] font-medium tracking-[-0.005em]">
                              {p.title || "Untitled"}
                            </div>
                            <div className="mt-[2px] font-mono text-[9px] uppercase tracking-[0.18em] text-text-muted/85">
                              {p.nodes.length} node{p.nodes.length === 1 ? "" : "s"}
                            </div>
                          </div>
                          {isCurrent ? (
                            <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-1.5 py-[2px] font-mono text-[9px] font-medium uppercase tracking-[0.2em] text-primary/95">
                              <Check size={9} aria-hidden="true" /> Current
                            </span>
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
                              askPrompt({
                                title: "Rename project",
                                description: `Currently "${p.title || "Untitled"}".`,
                                initialValue: p.title || "",
                                placeholder: "Project name",
                                submitLabel: "Rename",
                                onSubmit: (next) => {
                                  if (next === p.title) return;
                                  void api
                                    .updateAtelierProject(p.id, { title: next })
                                    .then(async () => {
                                      await useAtelierStore.getState().loadProjects();
                                      pushToast("success", `Renamed to "${next}"`);
                                    })
                                    .catch((err: unknown) => pushToast("error", `Rename failed: ${err instanceof Error ? err.message : String(err)}`));
                                },
                              });
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
                                askConfirm({
                                  title: `Delete "${p.title || "Untitled"}"?`,
                                  body: "All of its nodes will be removed. This cannot be undone.",
                                  confirmLabel: "Delete project",
                                  tone: "danger",
                                  onConfirm: () => {
                                    setShowProjectPicker(false);
                                    void api
                                      .deleteAtelierProject(p.id)
                                      .then(async () => {
                                        await useAtelierStore.getState().loadProjects();
                                        pushToast("info", `Deleted "${p.title || "Untitled"}"`);
                                      })
                                      .catch((err: unknown) => pushToast("error", `Delete failed: ${err instanceof Error ? err.message : String(err)}`));
                                  },
                                });
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
                <div className="mt-1 border-t border-white/6 pt-1">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      askPrompt({
                        title: "New project",
                        description: "Give it a name. You can rename later.",
                        placeholder: "Project name",
                        submitLabel: "Create",
                        onSubmit: (title) => {
                          setShowProjectPicker(false);
                          void createProject(title)
                            .then(() => pushToast("success", `Created "${title}"`))
                            .catch((err: unknown) => pushToast("error", `Create failed: ${err instanceof Error ? err.message : String(err)}`));
                        },
                      });
                    }}
                    className="flex w-full items-center gap-1.5 rounded px-2.5 py-2 text-left font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-primary transition-colors hover:bg-primary/10"
                  >
                    <Plus size={11} aria-hidden="true" />
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
        {/* Loading skeleton — three node-shaped surfaces with a shimmer
            sweep crossing left to right. Replaces the bland animate-pulse
            with a real gradient sweep so it reads as Linear-grade waiting,
            not a broken page. */}
        {isBootingProject ? (
          <div className="absolute inset-0 grid place-items-center" aria-label="Loading Atelier">
            <div className="flex items-center gap-6">
              <div
                aria-hidden="true"
                className="h-[180px] w-[180px] rounded-md border border-white/8 bg-[linear-gradient(110deg,rgba(255,255,255,0.02)_0%,rgba(255,255,255,0.05)_45%,rgba(100,108,255,0.06)_50%,rgba(255,255,255,0.05)_55%,rgba(255,255,255,0.02)_100%)] bg-[length:200%_100%] motion-safe:animate-atelier-shimmer motion-reduce:bg-white/[0.03]"
                style={{ backgroundColor: "#141416" }}
              />
              <div className="space-y-2">
                <div
                  aria-hidden="true"
                  className="h-[110px] w-[240px] rounded-md border border-white/8 bg-[linear-gradient(110deg,rgba(255,255,255,0.02)_0%,rgba(255,255,255,0.05)_45%,rgba(100,108,255,0.06)_50%,rgba(255,255,255,0.05)_55%,rgba(255,255,255,0.02)_100%)] bg-[length:200%_100%] motion-safe:animate-atelier-shimmer motion-reduce:bg-white/[0.03]"
                  style={{ backgroundColor: "#141416", animationDelay: "0.15s" }}
                />
                <div
                  aria-hidden="true"
                  className="h-[68px] w-[200px] rounded-md border border-white/8 bg-[linear-gradient(110deg,rgba(255,255,255,0.02)_0%,rgba(255,255,255,0.05)_45%,rgba(100,108,255,0.06)_50%,rgba(255,255,255,0.05)_55%,rgba(255,255,255,0.02)_100%)] bg-[length:200%_100%] motion-safe:animate-atelier-shimmer motion-reduce:bg-white/[0.03]"
                  style={{ backgroundColor: "#141416", animationDelay: "0.3s" }}
                />
              </div>
              <div
                aria-hidden="true"
                className="h-[180px] w-[180px] rounded-md border border-white/8 bg-[linear-gradient(110deg,rgba(255,255,255,0.02)_0%,rgba(255,255,255,0.05)_45%,rgba(100,108,255,0.06)_50%,rgba(255,255,255,0.05)_55%,rgba(255,255,255,0.02)_100%)] bg-[length:200%_100%] motion-safe:animate-atelier-shimmer motion-reduce:bg-white/[0.03]"
                style={{ backgroundColor: "#141416", animationDelay: "0.45s" }}
              />
            </div>
            <div className="absolute bottom-12 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.22em] text-text-muted/85">
              <span aria-hidden="true" className="h-[5px] w-[5px] rounded-full bg-primary/80 shadow-[0_0_0_3px_rgba(100,108,255,0.18)] motion-safe:animate-pulse" />
              Loading Atelier
            </div>
          </div>
        ) : null}

        {/* Empty-canvas welcome (DESIGN.md §11.1). Three clickable seed
            cards beat a one-liner — production users land here cold and
            need an obvious first action. */}
        {projectIsEmpty ? (
          <div className="pointer-events-none absolute inset-0 grid place-items-center">
            <div className="pointer-events-auto flex flex-col items-center gap-7 text-center animate-atelier-node-in motion-reduce:animate-none">
              <div className="space-y-2.5">
                <span className="font-mono text-[10px] font-medium uppercase tracking-[0.36em] text-text-muted/85">
                  Atelier · No 001 · v0.3
                </span>
                <div className="font-display text-[30px] font-medium leading-[1.02] tracking-[-0.012em] text-foreground">
                  Drop a <span className="italic">seed</span>.
                </div>
                <div className="max-w-[420px] text-[13px] leading-[1.55] text-text-secondary/95">
                  Pick a starting point. Everything you make connects from here.
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2.5">
                {([
                  { kind: "video", key: "V", title: "Video", desc: "Compose with a model + refs", primary: true },
                  { kind: "image", key: "I", title: "Image", desc: "Upload or generate a reference", primary: false },
                  { kind: "idea",  key: "T", title: "Idea",  desc: "Capture a beat or a vibe",     primary: false },
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
                    className={`group flex w-[176px] flex-col items-start gap-1.5 overflow-hidden rounded-lg border bg-[#141416] p-3.5 text-left shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.06)] transition-all hover:-translate-y-[1px] ${
                      card.primary
                        ? "border-primary/35 hover:border-primary/55 hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06),0_18px_36px_-18px_rgba(100,108,255,0.4)]"
                        : "border-white/8 hover:border-primary/35"
                    }`}
                  >
                    <div className="flex w-full items-center justify-between">
                      <span className={`font-mono text-[10px] font-medium uppercase tracking-[0.22em] ${card.primary ? "text-primary" : "text-text-muted/85"}`}>
                        {card.title}
                      </span>
                      <kbd className={`rounded-[3px] border px-1 py-[1px] font-mono text-[10px] tracking-tight ${
                        card.primary
                          ? "border-primary/30 bg-primary/10 text-primary"
                          : "border-white/8 bg-black/35 text-text-muted/85"
                      }`}>
                        {card.key}
                      </kbd>
                    </div>
                    <span className="text-[12px] leading-[1.4] text-text-secondary/90">{card.desc}</span>
                  </button>
                ))}
              </div>
              {/* Two short discoverability hints — a flat keyboard cue
                  and a one-liner explaining how nodes get linked, since
                  the right-edge drag handle isn't visible until something
                  is selected. */}
              <div className="flex flex-col items-center gap-2 font-mono text-[10px] uppercase tracking-[0.22em] text-text-muted/75">
                <div className="flex items-center gap-2">
                  <kbd className="rounded-[3px] border border-primary/30 bg-primary/10 px-1.5 py-[1px] text-[10px] tracking-tight text-primary">?</kbd>
                  <span>shortcuts</span>
                  <span aria-hidden="true" className="h-3 w-px bg-white/8" />
                  <span>drop image files anywhere</span>
                </div>
                <div className="flex items-center gap-2">
                  <span aria-hidden="true" className="h-[5px] w-[5px] rounded-full bg-primary/65 shadow-[0_0_0_2px_rgba(100,108,255,0.16)]" />
                  <span className="text-text-muted/85">
                    Link · select an image, drag the right-edge handle onto a draft
                  </span>
                </div>
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
              we render the path SVG and the chip overlay separately.
              The SVG bbox is computed dynamically from project node bounds +
              a generous pad — the previous hardcoded ±10000 viewBox would
              clip edges once a node drifted past those world coordinates,
              which Cmd+Arrow nudges or long drags could trigger trivially. */}
          {(() => {
            const labels: EdgeLabel[] = [];
            const refEdgeMidpoints: RefEdgeMidpoint[] = [];
            const paths = renderEdges(
              project ?? null,
              hoveredNodeId,
              selectedRefEdgeId,
              (id) => {
                // Selecting an edge clears node selection so the SelectionActionBar
                // doesn't fight the inline edge × delete button for screen real estate.
                setSelectedRefEdgeId(id);
                selectNode(null);
                if (extraSelectedIds.size > 0) setExtraSelectedIds(new Set());
              },
              labels,
              refEdgeMidpoints,
            );
            // Compute bbox: every node's outer corners + a 4000px pad so
            // edges curving outside the node bbox (cubic control points)
            // never get clipped. Falls back to a 4000² box centered at 0,0
            // when project is empty.
            let edgeMinX = -2000, edgeMinY = -2000, edgeMaxX = 2000, edgeMaxY = 2000;
            for (const n of project?.nodes ?? []) {
              const w = n.width || 240;
              const h = n.height || 110;
              if (n.x < edgeMinX) edgeMinX = n.x;
              if (n.y < edgeMinY) edgeMinY = n.y;
              if (n.x + w > edgeMaxX) edgeMaxX = n.x + w;
              if (n.y + h > edgeMaxY) edgeMaxY = n.y + h;
            }
            const PAD = 4000;
            const svgX = edgeMinX - PAD;
            const svgY = edgeMinY - PAD;
            const svgW = (edgeMaxX - edgeMinX) + PAD * 2;
            const svgH = (edgeMaxY - edgeMinY) + PAD * 2;
            return (
              <>
                <svg
                  className="absolute"
                  style={{
                    left: svgX,
                    top: svgY,
                    width: svgW,
                    height: svgH,
                    zIndex: 5,
                    // Container itself is pass-through; individual edge <g>
                    // elements set pointer-events: stroke locally so only
                    // the visible/hit-area path lines capture clicks. This
                    // keeps the giant SVG bbox from swallowing pan / drag
                    // gestures over empty space.
                    pointerEvents: "none",
                  }}
                  viewBox={`${svgX} ${svgY} ${svgW} ${svgH}`}
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
                {/* Selected ref-edge × button at the edge midpoint. Click
                    detaches the reference. Delete key does the same. */}
                {refEdgeMidpoints
                  .filter((m) => m.id === selectedRefEdgeId)
                  .map((m) => (
                    <button
                      key={`refedge-del-${m.id}`}
                      type="button"
                      aria-label="Detach reference"
                      data-tip="Detach reference (Del)"
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation();
                        const parsed = parseRefEdgeId(m.id);
                        if (!parsed) return;
                        const { fromId, toId, url } = parsed;
                        setSelectedRefEdgeId(null);
                        void useAtelierStore.getState()
                          .detachReferenceNode(toId, url, fromId)
                          .then(() => pushToast("info", "Reference detached"))
                          .catch((err: unknown) => pushToast("error", `Detach failed: ${err instanceof Error ? err.message : String(err)}`));
                      }}
                      className="btn-tip absolute z-[7] grid h-5 w-5 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/30 bg-[#141416] text-text-secondary shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18),0_2px_8px_-2px_rgba(0,0,0,0.6)] transition-all duration-150 hover:scale-[1.15] hover:border-red-400/55 hover:bg-red-400/12 hover:text-red-200 active:scale-[0.94] motion-safe:animate-atelier-popover-in"
                      style={{ left: m.midX, top: m.midY }}
                    >
                      <X size={11} aria-hidden="true" />
                    </button>
                  ))}
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
                {renderNode(node, allSelectedIds, selectNode, {
                  onUpload: (nodeId) => {
                    imageNodeIdForUploadRef.current = nodeId;
                    fileInputRef.current?.click();
                  },
                  onGenerate: () => {
                    pushToast("info", "Generate from prompt (T2I) is coming next.");
                  },
                }, editingIdeaId)}
              </div>
            );
          })}

          {/* virtual candidate media nodes (no drag — derived) */}
          {project?.nodes.flatMap((node) =>
            renderCandidatesAsMediaNodes(
              node,
              allSelectedIds,
              selectNode,
              (parentId, candidateId) => {
                void useAtelierStore.getState()
                  .retryCandidate(parentId, candidateId)
                  .then(() => pushToast("info", "Retrying take…"))
                  .catch((err: unknown) => pushToast("error", `Retry failed: ${err instanceof Error ? err.message : String(err)}`));
              },
              // Retry-with-different-model options: every visible video
              // model from the catalog. The user picked the original
              // model when they created the draft; we deliberately
              // include it here so "retry same model with a different
              // seed" stays accessible from the same menu.
              VIDEO_I2V_MODELS.map((m) => m.name),
              (parentId, candidateId, modelLabel) => {
                // Retry-with-model: delete the failed candidate then
                // generate one new candidate with the chosen model.
                // The endpoint supports per-call model + batch_size,
                // so this composes from existing primitives — no
                // backend change needed.
                const store = useAtelierStore.getState();
                const parent = store.currentProject?.nodes.find((n) => n.id === parentId);
                if (!parent) return;
                const existingRefs = readStringArray(
                  (parent.data as { reference_image_urls?: unknown })?.reference_image_urls,
                );
                const prompt =
                  readString((parent.data as { prompt?: unknown })?.prompt) ??
                  parent.prompt ??
                  "";
                void store
                  .deleteCandidate(parentId, candidateId)
                  .then(() =>
                    store.createVideoCandidates(parentId, {
                      prompt,
                      model: modelLabel,
                      reference_image_urls: existingRefs,
                      batch_size: 1,
                      params: {},
                    }),
                  )
                  .then(() => pushToast("info", `Retrying with ${modelLabel}…`))
                  .catch((err: unknown) =>
                    pushToast("error", `Retry failed: ${err instanceof Error ? err.message : String(err)}`),
                  );
              },
            ),
          )}

          {/* Selected-draft workbench (RHTV/LibTV pattern). Render in
              world coords so it scales with zoom + lives at the same
              spot the compact card would occupy. The compact DraftNode
              for this id is suppressed in renderNode when isSelected,
              so the two never double up. */}
          {selectedNode && isDraftVideo(selectedNode) && !isMultiSelect && project ? (
            <div
              key={`workbench-${selectedNode.id}`}
              data-atelier-node={selectedNode.id}
              onPointerDownCapture={(e) => handleNodePointerDown(e, selectedNode)}
              onContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                if (!allSelectedIds.has(selectedNode.id)) {
                  selectNode(selectedNode.id);
                  if (extraSelectedIds.size > 0) setExtraSelectedIds(new Set());
                }
                setContextMenu({ screenX: e.clientX, screenY: e.clientY, node: selectedNode });
              }}
              onMouseEnter={() => setHoveredNodeId(selectedNode.id)}
              onMouseLeave={() => setHoveredNodeId((prev) => (prev === selectedNode.id ? null : prev))}
              className="group/node animate-atelier-node-in motion-reduce:animate-none"
              style={{ touchAction: "none", zIndex: 22 }}
            >
              <DraftWorkbench
                id={selectedNode.id}
                status="draft"
                intent={
                  readString(selectedNode.data?.intent) ?? selectedNode.title ?? "Untitled draft"
                }
                modelLabel={readString(selectedNode.data?.model) ?? "Wan 2.7"}
                configSummary={
                  readString(selectedNode.data?.config_summary) ?? "1280×720 · 5s · 4×"
                }
                candidatesReady={(() => {
                  const cands = readCandidates(selectedNode);
                  return cands.length > 0
                    ? cands.filter((c) => c.status === "completed").length
                    : undefined;
                })()}
                candidatesTotal={(() => {
                  const cands = readCandidates(selectedNode);
                  return cands.length > 0 ? cands.length : undefined;
                })()}
                selected
                x={selectedNode.x}
                y={selectedNode.y}
                prompt={readString(selectedNode.data?.prompt) ?? selectedNode.prompt ?? ""}
                refs={(() => {
                  const urls = readStringArray(
                    (selectedNode.data as { reference_image_urls?: unknown })?.reference_image_urls,
                  );
                  const idList = readStringArray(
                    (selectedNode.data as { reference_node_ids?: unknown })?.reference_node_ids,
                  );
                  const allNodes = project.nodes;
                  const findKind = (url: string, idx: number): "image" | "video" | "audio" | undefined => {
                    const idCandidate = idList[idx];
                    if (idCandidate) {
                      const byId = allNodes.find((n) => n.id === idCandidate);
                      if (byId && (byId.type === "image" || byId.type === "video" || byId.type === "audio")) {
                        return byId.type;
                      }
                    }
                    const byUrl = allNodes.find(
                      (n) => Array.isArray(n.media_urls) && n.media_urls.includes(url),
                    );
                    if (byUrl && (byUrl.type === "image" || byUrl.type === "video" || byUrl.type === "audio")) {
                      return byUrl.type;
                    }
                    return undefined;
                  };
                  return urls.map((src, idx) => ({
                    src: getAssetUrl(src),
                    role: "ref",
                    kind: findKind(src, idx),
                  }));
                })()}
                onSelect={(id) => selectNode(id)}
                onIntentCommit={(next) => {
                  void useAtelierStore.getState()
                    .updateNode(selectedNode.id, {
                      data: { ...(selectedNode.data ?? {}), intent: next },
                    })
                    .catch(() => {/* save chip surfaces failures */});
                }}
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
                    .catch((err: unknown) =>
                      pushToast("error", `Remove failed: ${err instanceof Error ? err.message : String(err)}`),
                    );
                }}
                onPromptCommit={(next) => {
                  void useAtelierStore.getState()
                    .updateNode(selectedNode.id, {
                      data: { ...(selectedNode.data ?? {}), prompt: next },
                    })
                    .catch(() => {/* save chip surfaces failures */});
                }}
                mentionables={(project.nodes ?? [])
                  .filter((n) => n.id !== selectedNode.id)
                  .map((n) => ({
                    id: n.id,
                    label:
                      n.title ||
                      readString((n.data as { intent?: unknown })?.intent) ||
                      readString((n.data as { body?: unknown })?.body)?.slice(0, 40) ||
                      n.type,
                    kind:
                      n.type === "video" && n.status === "draft"
                        ? ("draft" as const)
                        : (n.type as "image" | "video" | "audio" | "idea" | "plan"),
                  }))}
              />
            </div>
          ) : null}

          {/* Connect-drag target highlight: while dragging from an image's
              connect handle, glow the draft under the cursor (in world coords
              so the ring scales with zoom). */}
          {connectDragRef.current && hoveredConnectTargetId ? (() => {
            const target = project?.nodes.find((n) => n.id === hoveredConnectTargetId);
            if (!target) return null;
            return (
              <div
                key={`connect-target-ring-${connectDragTick}`}
                className="pointer-events-none absolute z-[36] rounded-lg ring-2 ring-primary shadow-[0_0_0_4px_rgba(100,108,255,0.18),0_0_24px_-2px_rgba(100,108,255,0.45)] motion-safe:animate-atelier-pulse-soft"
                style={{
                  left: target.x - 6,
                  top: target.y - 6,
                  width: (target.width || 240) + 12,
                  height: (target.height || 110) + 12,
                }}
              />
            );
          })() : null}

          {/* selection action bar + composer moved OUT of world (screen coords)
              so they stay readable at any zoom — see below. */}

          {/* Inline editor for sticky-text nodes (idea + comment). Same
              shape, different background tint by kind. */}
          {editingIdeaId && project ? (() => {
            const node = project.nodes.find((n) => n.id === editingIdeaId);
            if (!node || (node.type !== "idea" && node.type !== "comment")) return null;
            // Match the underlying node card: same width (224), same solid
            // bg, same rounded corners, same outer shadow. The card itself
            // renders editing-mode (chrome only) so this textarea owns the
            // entire visual frame while the user types.
            const isComment = node.type === "comment";
            const surfaceBg = isComment ? "bg-[#15141a]" : "bg-[#1a1611]";
            const ringTone = isComment
              ? "border-violet-300/55 ring-violet-300/40"
              : "border-primary/55 ring-primary/40";
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
                placeholder="Write the idea — Esc to cancel, ⌘⏎ to save"
                className={`absolute z-30 w-[224px] resize-none rounded-[10px] border px-3.5 py-3 font-display text-[13.5px] italic leading-[1.5] tracking-tight text-foreground/95 placeholder:not-italic placeholder:font-mono placeholder:text-[10.5px] placeholder:uppercase placeholder:tracking-[0.22em] placeholder:text-text-muted/60 shadow-[0_14px_36px_-22px_rgba(0,0,0,0.7),0_2px_4px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.05)] outline-none ring-2 ${surfaceBg} ${ringTone}`}
                style={{ left: node.x, top: node.y, minHeight: 140, height: Math.max(140, (node.height || 140)) }}
              />
            );
          })() : null}

          {/* Empty image upload affordance moved into MediaNode itself —
              one bordered box, one style language, no stacked overlays.
              See `onUpload` / `onGenerate` props on the renderNode call. */}
        </div>
      </main>

      {/* SelectionActionBar — rendered OUTSIDE the world transform so it
          stays a fixed screen size while still anchored above the selected
          node. Coordinates converted: screen = pan + world * zoom.
          Hidden in multi-select mode; the multi-select chip takes over.
          The bar is positioned directly under the shell root (relative)
          using its own absolute — no nested 0×0 wrapper, since the nested
          wrapper introduced an extra containing-block that desync'd the
          bar from the node when the user pinch-zoomed or pan-resized. */}
      {selectedNode && !isMultiSelect ? (
        <SelectionActionBar
          kind={selectionKindOf(selectedNode)}
          x={panX + selectedNode.x * zoomFactor}
          y={panY + selectedNode.y * zoomFactor}
          width={(selectedNode.width || 240) * zoomFactor}
          onAct={(action) => handleActionBar(action, selectedNode)}
        />
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
            className="pointer-events-auto absolute z-30 flex -translate-x-1/2 items-center gap-2 rounded-full border border-white/8 bg-[#141416]/96 px-3 py-[6px] text-[12px] shadow-[0_14px_30px_-16px_rgba(0,0,0,0.7),0_2px_6px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
            style={{ left: screenCx, top: Math.max(8, screenY) }}
          >
            <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-muted/85">
              <span className="text-foreground/95">{allSelectedIds.size}</span>{" "}
              <span>selected</span>
            </span>
            <span aria-hidden="true" className="h-3.5 w-px bg-white/8" />
            <div className="relative">
              <button
                type="button"
                onClick={() => setShowAlignMenu((v) => !v)}
                aria-expanded={showAlignMenu}
                aria-haspopup="menu"
                className="inline-flex items-center gap-1 rounded-full px-2 py-[3px] font-mono text-[10px] uppercase tracking-[0.18em] text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground"
              >
                Align
                <ChevronDown size={9} aria-hidden="true" />
              </button>
              {showAlignMenu ? (
                <>
                  <div
                    aria-hidden="true"
                    className="fixed inset-0 z-[31]"
                    onClick={() => setShowAlignMenu(false)}
                  />
                  <ul
                    role="menu"
                    aria-label="Align selection"
                    className="absolute left-0 top-8 z-[32] w-[200px] origin-top rounded-md border border-white/8 bg-[#141416] p-1 shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
                  >
                    <div className="px-2 pb-1 pt-1 font-mono text-[9px] uppercase tracking-[0.2em] text-text-muted/80">
                      Align
                    </div>
                    {([
                      { op: "left", label: "Align left" },
                      { op: "center-h", label: "Align center horizontal" },
                      { op: "right", label: "Align right" },
                      { op: "divider", label: "" },
                      { op: "top", label: "Align top" },
                      { op: "center-v", label: "Align center vertical" },
                      { op: "bottom", label: "Align bottom" },
                      { op: "divider", label: "" },
                      { op: "distribute-h", label: "Distribute horizontally" },
                      { op: "distribute-v", label: "Distribute vertically" },
                    ] as const).map((it, i) =>
                      it.op === "divider" ? (
                        <li key={`d-${i}`} role="none" className="my-1 mx-2 h-px bg-white/6" />
                      ) : (
                        <li key={it.op} role="none">
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => applyAlign(it.op)}
                            className="block w-full rounded px-2.5 py-[6px] text-left text-[12px] text-text-secondary transition-colors hover:bg-white/[0.05] hover:text-foreground"
                          >
                            {it.label}
                          </button>
                        </li>
                      ),
                    )}
                  </ul>
                </>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => void deleteSelection()}
              className="rounded-full bg-red-400/12 px-2 py-[3px] font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-red-200/95 transition-colors hover:bg-red-400/22"
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
              className="rounded-full px-2 py-[3px] font-mono text-[10px] uppercase tracking-[0.18em] text-text-muted/85 transition-colors hover:bg-hover-bg hover:text-foreground"
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
          batch chip can claim that space. Empty image drafts skip too
          because their actionable card already speaks for itself. */}
      {selectedNode && !isMultiSelect && !isDraftVideo(selectedNode) && !parseCandidateNodeId(selectedNode.id)
        && !(selectedNode.type === "image" && (selectedNode.media_urls?.length ?? 0) === 0) ? (() => {
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
        } else if (node.type === "idea" || node.type === "comment") {
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
            className="absolute z-30 inline-flex items-center gap-2.5 rounded-full border border-white/8 bg-[#141416]/92 px-3 py-[5px] font-mono text-[10px] shadow-[inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-node-in motion-reduce:animate-none"
            style={{ left: screenLeft, top: screenTop }}
          >
            {facts.map((f, i) => (
              <React.Fragment key={f.label}>
                <span className="inline-flex items-center gap-1.5">
                  <span className="uppercase tracking-[0.2em] text-text-muted/85">{f.label}</span>
                  <span className="text-foreground/95">{f.value}</span>
                </span>
                {i < facts.length - 1 ? (
                  <span aria-hidden="true" className="h-3 w-px bg-white/8" />
                ) : null}
              </React.Fragment>
            ))}
          </div>
        );
      })() : null}

      {/* Resize handles — four corners on a selected image / video node
          (real, not virtual, not draft). Drag a corner to resize the
          node's bounds; opposite corner stays anchored. Constrained to
          80×60 min and 800×600 max. Persists via commitNodeBounds on
          release. */}
      {selectedNode &&
       !isMultiSelect &&
       (selectedNode.type === "image" || (selectedNode.type === "video" && !isDraftVideo(selectedNode))) &&
       !parseCandidateNodeId(selectedNode.id) &&
       (selectedNode.media_urls?.length ?? 0) > 0 ? (() => {
        const w = selectedNode.width || 180;
        const h = selectedNode.height || 180;
        const corners = [
          { key: "tl" as const, sx: panX + selectedNode.x * zoomFactor, sy: panY + selectedNode.y * zoomFactor, cursor: "nwse-resize" },
          { key: "tr" as const, sx: panX + (selectedNode.x + w) * zoomFactor, sy: panY + selectedNode.y * zoomFactor, cursor: "nesw-resize" },
          { key: "bl" as const, sx: panX + selectedNode.x * zoomFactor, sy: panY + (selectedNode.y + h) * zoomFactor, cursor: "nesw-resize" },
          { key: "br" as const, sx: panX + (selectedNode.x + w) * zoomFactor, sy: panY + (selectedNode.y + h) * zoomFactor, cursor: "nwse-resize" },
        ];
        const startResize = (corner: "tl" | "tr" | "bl" | "br") => (event: React.PointerEvent) => {
          if (event.button !== 0) return;
          event.stopPropagation();
          event.preventDefault();
          const node = selectedNode;
          if (!node) return;
          resizeDragRef.current = {
            nodeId: node.id,
            corner,
            startX: node.x,
            startY: node.y,
            startW: node.width || 180,
            startH: node.height || 180,
            startPointerX: event.clientX,
            startPointerY: event.clientY,
            moved: false,
          };
          setResizeTick((v) => v + 1);
          const MIN_W = 80, MIN_H = 60, MAX_W = 800, MAX_H = 600;
          const onMove = (ev: PointerEvent) => {
            const drag = resizeDragRef.current;
            if (!drag) return;
            const dx = (ev.clientX - drag.startPointerX) / zoomFactor;
            const dy = (ev.clientY - drag.startPointerY) / zoomFactor;
            if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
            let nx = drag.startX, ny = drag.startY, nw = drag.startW, nh = drag.startH;
            if (drag.corner === "tl") {
              nw = drag.startW - dx;
              nh = drag.startH - dy;
              nx = drag.startX + dx;
              ny = drag.startY + dy;
            } else if (drag.corner === "tr") {
              nw = drag.startW + dx;
              nh = drag.startH - dy;
              ny = drag.startY + dy;
            } else if (drag.corner === "bl") {
              nw = drag.startW - dx;
              nh = drag.startH + dy;
              nx = drag.startX + dx;
            } else { // br
              nw = drag.startW + dx;
              nh = drag.startH + dy;
            }
            // Clamp to min/max while keeping the opposite corner anchored.
            if (nw < MIN_W) {
              if (drag.corner === "tl" || drag.corner === "bl") nx = drag.startX + (drag.startW - MIN_W);
              nw = MIN_W;
            }
            if (nw > MAX_W) {
              if (drag.corner === "tl" || drag.corner === "bl") nx = drag.startX + (drag.startW - MAX_W);
              nw = MAX_W;
            }
            if (nh < MIN_H) {
              if (drag.corner === "tl" || drag.corner === "tr") ny = drag.startY + (drag.startH - MIN_H);
              nh = MIN_H;
            }
            if (nh > MAX_H) {
              if (drag.corner === "tl" || drag.corner === "tr") ny = drag.startY + (drag.startH - MAX_H);
              nh = MAX_H;
            }
            useAtelierStore.getState().resizeNodeLocal(drag.nodeId, Math.round(nx), Math.round(ny), Math.round(nw), Math.round(nh));
            setResizeTick((v) => v + 1);
          };
          const onUp = () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
            const drag = resizeDragRef.current;
            resizeDragRef.current = null;
            setResizeTick((v) => v + 1);
            if (!drag || !drag.moved) return;
            const real = useAtelierStore.getState().currentProject?.nodes.find((n) => n.id === drag.nodeId);
            if (!real) return;
            void useAtelierStore.getState()
              .commitNodeBounds(drag.nodeId, real.x, real.y, real.width || drag.startW, real.height || drag.startH)
              .catch(() => {/* save chip surfaces failures */});
          };
          window.addEventListener("pointermove", onMove);
          window.addEventListener("pointerup", onUp);
        };
        return (
          <>
            {corners.map((c) => (
              <button
                key={c.key}
                type="button"
                aria-label={`Resize ${c.key}`}
                onPointerDown={startResize(c.key)}
                className="absolute z-40 h-[10px] w-[10px] -translate-x-1/2 -translate-y-1/2 rounded-[2px] border border-white/40 bg-primary shadow-[inset_0_1px_0_0_rgba(255,255,255,0.25),0_0_0_2px_rgba(100,108,255,0.18),0_2px_6px_-2px_rgba(100,108,255,0.55)] transition-all duration-200 hover:scale-[1.4] hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.3),0_0_0_3px_rgba(100,108,255,0.28),0_3px_8px_-2px_rgba(100,108,255,0.7)]"
                style={{ left: c.sx, top: c.sy, cursor: c.cursor }}
              />
            ))}
          </>
        );
      })() : null}

      {/* Connection ports — only show on nodes that *can* drag out (image
          with media, completed take). Showing them on idea/draft/comment
          nodes was a "coming soon" trap: the affordance promised an
          interaction the implementation didn't have. Visual: identical
          16×16 ports on left and right edges. The right port is the
          active drag-from. The left port is a passive drop indicator —
          when a connection drag is in flight and this node is the
          hovered target, both ports light primary to confirm the
          landing zone. */}
      {(() => {
        const candidate = selectedNode
          ? selectedNode
          : hoveredNodeId
            ? (project?.nodes.find((n) => n.id === hoveredNodeId) ?? null)
            : null;
        if (!candidate) return null;
        if (connectDragRef.current?.sourceNodeId === candidate.id) return null;
        // Only nodes with outgoing semantics earn ports. Image needs
        // uploaded media; takes must be a completed candidate.
        const parsedCand = parseCandidateNodeId(candidate.id);
        const candData = (() => {
          if (!parsedCand || !project) return null;
          const parent = project.nodes.find((n) => n.id === parsedCand.parentId);
          return parent ? readCandidates(parent).find((c) => c.id === parsedCand.candidateId) ?? null : null;
        })();
        const isImageWithMedia =
          candidate.type === "image" && (candidate.media_urls?.length ?? 0) > 0;
        const isCompletedTake = !!candData && candData.status === "completed";
        if (!isImageWithMedia && !isCompletedTake) return null;

        const w = candidate.width || (candidate.type === "image" ? 244 : 200);
        const h = candidate.height || (candidate.type === "image" ? 224 : 113);
        const cy = panY + (candidate.y + h / 2) * zoomFactor;
        const leftX = panX + candidate.x * zoomFactor;
        const rightX = panX + (candidate.x + w) * zoomFactor;
        const isDropTarget =
          !!connectDragRef.current && hoveredConnectTargetId === candidate.id;
        const isDragSourceCompatible = !!connectDragRef.current; // any drag in flight

        // Both ports share the same base shape (16×16 round, dashed inset
        // border, neutral fill) so they read as a coherent input/output
        // pair. Active states only adjust tone, not geometry.
        const baseShape =
          "absolute z-40 grid h-4 w-4 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border backdrop-blur-md transition-all duration-150";
        const idleTone =
          "border-white/22 bg-[#141416]/95 text-text-muted/80 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06),0_2px_6px_-2px_rgba(0,0,0,0.45)]";
        const activeTone =
          "border-primary/60 bg-primary/15 text-primary shadow-[0_0_0_3px_rgba(100,108,255,0.18),0_3px_10px_-2px_rgba(100,108,255,0.4)]";
        const dropTargetTone =
          "border-primary bg-primary text-white shadow-[0_0_0_4px_rgba(100,108,255,0.32),0_4px_12px_-2px_rgba(100,108,255,0.5)] motion-safe:animate-atelier-pulse-soft";
        return (
          <>
            <span
              aria-hidden="true"
              data-tip={isDropTarget ? "Drop to connect" : "Input"}
              className={`btn-tip ${baseShape} ${
                isDropTarget ? dropTargetTone : isDragSourceCompatible ? activeTone : idleTone
              }`}
              style={{ left: leftX, top: cy }}
            >
              <Plus size={9} aria-hidden="true" />
            </span>
            <button
              type="button"
              aria-label="Connection output — drag onto a node or empty canvas"
              data-tip="Drag onto a draft, or to empty canvas to spawn a connected draft"
              onPointerDown={(e) => handlePortDragOut(e, candidate, rightX, cy)}
              className={`btn-tip cursor-grab active:cursor-grabbing hover:scale-[1.18] hover:border-primary/60 hover:bg-primary/30 hover:text-white ${baseShape} ${
                isDropTarget ? dropTargetTone : activeTone
              }`}
              style={{ left: rightX, top: cy }}
            >
              <Plus size={9} aria-hidden="true" />
            </button>
          </>
        );
      })()}

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
            className="pointer-events-none fixed z-[44] rounded-[3px] border border-primary/55 bg-primary/[0.06] shadow-[0_0_0_1px_rgba(100,108,255,0.18),inset_0_0_24px_-8px_rgba(100,108,255,0.35)]"
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
              className="motion-safe:animate-atelier-dash"
            />
            <circle
              cx={x2}
              cy={y2}
              r={isOverTarget ? 5 : 3.5}
              fill="rgba(100,108,255,0.95)"
              style={{ filter: isOverTarget ? "drop-shadow(0 0 6px rgba(100,108,255,0.6))" : "drop-shadow(0 0 3px rgba(100,108,255,0.4))" }}
            />
          </svg>
        );
      })() : null}

      {/* Floating Composer retired in Sprint A — drafts now use the
          inline DraftWorkbench (rendered above inside the world wrapper).
          Per Codex competitive research §4.4 / §6.1 / §7.4: selected
          node IS the workbench, no popups to chase. */}

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

      {/* Sequence Strip — bottom rail. Outer surface uses the cinematic
          chrome vocabulary (1px white/8 hairline + inset top edge highlight
          + layered shadow) so it reads as a real surface, not a glass card.
          Drop target: HTML5-draggable completed takes from the canvas can
          be dropped here to append to the sequence (handler reads the
          custom application/x-atelier-take mime). */}
      <div
        className={`absolute bottom-4 left-[280px] z-20 rounded-2xl border bg-[#0c0c10]/92 p-2.5 shadow-[0_18px_36px_-22px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl transition-colors ${
          seqDropActive ? "border-primary/60 ring-2 ring-primary/35" : "border-white/8"
        }`}
        style={{ right: agentCollapsed ? 88 : 412 }}
        onDragEnter={(e) => {
          if (!Array.from(e.dataTransfer.types).includes("application/x-atelier-take")) return;
          e.preventDefault();
          setSeqDropActive(true);
        }}
        onDragOver={(e) => {
          if (!Array.from(e.dataTransfer.types).includes("application/x-atelier-take")) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDragLeave={(e) => {
          // Only clear when leaving for somewhere outside the strip.
          if (e.currentTarget.contains(e.relatedTarget as Node)) return;
          setSeqDropActive(false);
        }}
        onDrop={(e) => {
          const data = e.dataTransfer.getData("application/x-atelier-take");
          setSeqDropActive(false);
          if (!data) return;
          e.preventDefault();
          try {
            // Payload is always an array — single take is a 1-element
            // array. Multi-selected takes share order from the parent's
            // candidate list (see buildBatchPayload in nodeRenderers).
            const parsed = JSON.parse(data);
            const batch: Array<{ parentId: string; candidateId: string }> =
              Array.isArray(parsed)
                ? parsed
                : parsed && typeof parsed === "object"
                  ? [parsed]
                  : [];
            if (batch.length === 0) return;
            let added = 0;
            let skipped = 0;
            setSequence((prev) => {
              const next = [...prev];
              for (const item of batch) {
                if (!item || typeof item.parentId !== "string" || typeof item.candidateId !== "string") {
                  continue;
                }
                if (next.some((s) => s.parentId === item.parentId && s.candidateId === item.candidateId)) {
                  skipped += 1;
                  continue;
                }
                next.push({ parentId: item.parentId, candidateId: item.candidateId });
                added += 1;
              }
              return next;
            });
            if (added === 0 && skipped > 0) {
              pushToast("info", "Already in sequence");
            } else if (added > 0 && skipped > 0) {
              pushToast("success", `Added ${added} · skipped ${skipped} duplicate${skipped === 1 ? "" : "s"}`);
            } else if (added === 1) {
              pushToast("success", "Added to sequence");
            } else if (added > 1) {
              pushToast("success", `Added ${added} clips`);
            }
          } catch {
            // Malformed payload — silently ignore. Should never happen
            // since we serialize ourselves, but defensive parsing means
            // a foreign drop won't crash the strip.
          }
        }}
      >
        {/* Editorial header: stamped 'CUT · SEQUENCE · NO 001' identity +
            running clip count rendered as a typewriter readout. The bullet
            dot becomes part of the rhythm, not a status indicator. */}
        <div className="mb-2 flex items-center justify-between gap-2 border-b border-dashed border-white/8 px-1 pb-1.5 font-mono text-[9px] uppercase tracking-[0.28em] text-text-muted/85">
          <div className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-[5px] w-[5px] rounded-full bg-primary/70 shadow-[0_0_0_2px_rgba(100,108,255,0.16)]" />
            <span className="font-medium">Cut · Sequence · No 001</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span aria-hidden="true" className="text-text-muted/55">{sequenceEntries.length === 1 ? "Clip" : "Clips"}</span>
            <span className="font-display text-[11px] tracking-tight text-foreground/95">
              {String(sequenceEntries.length).padStart(2, "0")}
            </span>
            {sequenceEntries.length > 0 ? (
              <button
                onClick={() => setSequence([])}
                className="ml-1 rounded px-1.5 py-0.5 tracking-[0.24em] text-text-muted/70 transition-colors hover:bg-white/[0.06] hover:text-foreground"
              >
                Clear
              </button>
            ) : null}
          </div>
        </div>
        {sequenceEntries.length === 0 ? (
          <div className="px-2 py-2 text-[11px] text-text-muted/85">
            Drag a completed take here, or use{" "}
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-text-muted/70">Add to Sequence</span>{" "}
            from its action bar.
          </div>
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
                onMouseEnter={(e) => {
                  // Hover-to-preview, same vibe as the canvas take cards:
                  // the inner <video> autoplays muted+loop on a 250 ms
                  // dwell so a quick scrub through the strip doesn't
                  // trigger flicker.
                  const v = e.currentTarget.querySelector("video");
                  if (!v) return;
                  window.setTimeout(() => {
                    v.play().catch(() => {/* autoplay may be blocked */});
                  }, 250);
                }}
                onMouseLeave={(e) => {
                  const v = e.currentTarget.querySelector("video");
                  if (!v) return;
                  v.pause();
                  try { v.currentTime = 0; } catch { /* ignore */ }
                }}
                className={`group relative h-[68px] w-[124px] shrink-0 cursor-grab overflow-hidden rounded-[5px] border transition-shadow hover:border-primary/45 hover:shadow-[0_0_0_1px_rgba(100,108,255,0.22)] active:cursor-grabbing ${
                  seqDragFromIndex === i
                    ? "opacity-45 border-primary/55"
                    : seqDragOverIndex === i && seqDragFromIndex !== null && seqDragFromIndex !== i
                    ? "border-primary ring-2 ring-primary/35"
                    : "border-white/8 bg-[#141416]"
                }`}
                aria-label={`Play ${parent.title}, clip ${i + 1}`}
              >
                {cand.video_url ? (
                  <video
                    src={getAssetUrl(cand.video_url)}
                    muted
                    loop
                    playsInline
                    preload="metadata"
                    aria-label={`${parent.title} thumbnail`}
                    className="h-full w-full object-cover"
                  />
                ) : null}
                <span className="pointer-events-none absolute inset-0 m-auto grid h-7 w-7 place-items-center rounded-full bg-black/65 text-white/95 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100">
                  <Play size={11} aria-hidden="true" />
                </span>
                <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-gradient-to-t from-black/85 via-black/55 to-transparent px-1.5 pb-1 pt-2.5">
                  <span className="truncate text-[10px] text-foreground/95">{parent.title}</span>
                  <span className="font-mono text-[9px] tracking-tight text-text-muted">{String(i + 1).padStart(2, "0")}</span>
                </div>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSequence((prev) => prev.filter((s) => !(s.parentId === entry.parentId && s.candidateId === entry.candidateId)));
                  }}
                  className="absolute right-1 top-1 grid h-4 w-4 place-items-center rounded-full bg-black/65 text-white/85 opacity-0 transition-colors hover:bg-red-500/75 group-hover:opacity-100"
                  aria-label={`Remove ${parent.title} from sequence`}
                >
                  <X size={9} aria-hidden="true" />
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
        } else if (kind === "idea" || kind === "comment") {
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
              className="fixed z-[56] min-w-[200px] origin-top-left rounded-md border border-white/8 bg-[#141416]/96 p-1 text-[12px] shadow-[0_18px_36px_-20px_rgba(0,0,0,0.7),0_2px_8px_-2px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-popover-in motion-reduce:animate-none"
              style={{
                left: Math.min(contextMenu.screenX, window.innerWidth - 220),
                top: Math.min(contextMenu.screenY, window.innerHeight - 220),
              }}
            >
              {items.map((item) => (
                <li key={item.label} role="none">
                  <button
                    type="button"
                    role="menuitem"
                    disabled={item.disabled}
                    onClick={item.disabled ? undefined : item.onClick}
                    className={`block w-full rounded px-2.5 py-[6px] text-left transition-colors ${
                      item.disabled
                        ? "cursor-not-allowed text-text-muted/55"
                        : item.danger
                        ? "text-red-200 hover:bg-red-400/12"
                        : "text-foreground/95 hover:bg-white/[0.05]"
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
            className="fixed inset-0 z-50 grid place-items-center bg-black/75 backdrop-blur-md animate-atelier-modal-overlay-in motion-reduce:animate-none"
            onClick={() => setUseAsRefSourceId(null)}
            role="dialog"
            aria-label="Pick a target draft to attach this reference"
          >
            <div
              className="w-[440px] overflow-hidden rounded-[14px] border border-white/8 bg-[#141416] shadow-[0_32px_60px_-26px_rgba(0,0,0,0.85),0_8px_18px_-6px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.06)] animate-atelier-modal-content-in motion-reduce:animate-none"
              onClick={(e) => e.stopPropagation()}
            >
              <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-primary via-primary/45 to-transparent" />
              <div className="px-4 pb-3 pt-3.5">
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-baseline gap-2.5">
                    <span className="font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-primary/85">Reference</span>
                    <span className="font-display text-[14px] font-medium tracking-[-0.005em] text-foreground">Attach to a draft</span>
                  </div>
                  <button onClick={() => setUseAsRefSourceId(null)} className="inline-flex h-6 w-6 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground" aria-label="Close">
                    <X size={13} aria-hidden="true" />
                  </button>
                </div>
                <div className="mb-3 flex items-center gap-2.5 rounded-md border border-white/6 bg-black/25 p-2">
                  {source.media_urls?.[0] ? (
                    <img src={getAssetUrl(source.media_urls[0])} alt="" className="h-10 w-10 rounded-[5px] border border-white/8 object-cover" />
                  ) : null}
                  <div className="min-w-0 text-[12px] text-text-secondary">
                    <div className="truncate font-display text-[13px] font-medium text-foreground/95">{source.title || "Untitled image"}</div>
                    <div className="font-mono text-[9px] uppercase tracking-[0.2em] text-text-muted/80">Source</div>
                  </div>
                </div>
                {drafts.length === 0 ? (
                  <div className="rounded-md border border-dashed border-white/10 bg-black/20 p-4 text-center text-[12px] text-text-muted/85">
                    No draft video nodes yet. Press{" "}
                    <kbd className="rounded border border-white/8 bg-black/35 px-1 py-[1px] font-mono text-[10px] text-foreground/95">V</kbd>{" "}
                    to create one.
                  </div>
                ) : (
                  <ul className="max-h-[300px] space-y-[3px] overflow-y-auto">
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
                            className="group flex w-full items-center justify-between gap-2 rounded-md border border-white/6 bg-black/20 px-3 py-2 text-left transition-colors hover:border-primary/35 hover:bg-primary/[0.04]"
                          >
                            <div className="min-w-0">
                              <div className="truncate font-display text-[13px] font-medium tracking-[-0.005em] text-foreground/95">{intent}</div>
                              <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-text-muted/85">{model}</div>
                            </div>
                            <span className="rounded-full bg-primary/12 px-2 py-[3px] font-mono text-[9px] font-medium uppercase tracking-[0.2em] text-primary/95 transition-colors group-hover:bg-primary/20">
                              Attach
                            </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
              </div>
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
            className="fixed inset-0 z-50 grid place-items-center bg-black/85 backdrop-blur-md animate-atelier-modal-overlay-in motion-reduce:animate-none"
            onClick={close}
            role="dialog"
            aria-label="Video preview"
          >
            <div className="relative max-h-[88vh] max-w-[80vw] overflow-hidden rounded-[14px] border border-white/8 bg-[#0c0c10] shadow-[0_36px_70px_-30px_rgba(0,0,0,0.95),0_10px_24px_-8px_rgba(0,0,0,0.6),inset_0_1px_0_0_rgba(255,255,255,0.06)] animate-atelier-modal-content-in motion-reduce:animate-none" onClick={(e) => e.stopPropagation()}>
              <video
                src={getAssetUrl(ctx.url)}
                controls
                autoPlay
                className="block max-h-[80vh] max-w-[80vw] bg-black"
              />
              {isTake && parent && cand ? (
                <div className="flex items-center justify-between gap-3 border-t border-white/6 px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="truncate font-display text-[13px] font-medium tracking-[-0.005em] text-foreground/95">
                      {parent.title}
                    </div>
                    <div className="mt-[2px] flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.18em]">
                      <span className="text-primary/85">{cand.model}</span>
                      <span aria-hidden="true" className="text-text-muted/50">·</span>
                      <span className="text-text-muted/85">{cand.label || cand.id.slice(0, 8)}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    {!isSelectedTake ? (
                      <button
                        type="button"
                        onClick={() => {
                          void useAtelierStore.getState().selectCandidate(parent.id, cand.id)
                            .then(() => pushToast("success", "Selected as take"))
                            .catch((err: unknown) => pushToast("error", `Select failed: ${err instanceof Error ? err.message : String(err)}`));
                          close();
                        }}
                        className="rounded-full bg-primary px-2.5 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.16),0_4px_12px_-4px_rgba(100,108,255,0.5)] transition-colors hover:bg-primary/92"
                      >
                        Select as take
                      </button>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-400/12 px-2 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-emerald-200/95">
                        <Check size={10} aria-hidden="true" /> Selected
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        void branchFromCandidate(parent.id, cand.id)
                          .then(() => pushToast("success", "Branched · new draft created"))
                          .catch((err: unknown) => pushToast("error", `Branch failed: ${err instanceof Error ? err.message : String(err)}`));
                        close();
                      }}
                      className="rounded-full px-2 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground"
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
                      className="rounded-full px-2 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-text-secondary transition-colors hover:bg-hover-bg hover:text-foreground"
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
                      className="rounded-full bg-red-400/12 px-2 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-red-200/95 transition-colors hover:bg-red-400/22"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ) : null}
              <button
                onClick={close}
                className="absolute right-2.5 top-2.5 inline-flex h-7 w-7 items-center justify-center rounded-full bg-black/55 text-white/90 backdrop-blur transition-colors hover:bg-black/75"
                aria-label="Close preview"
              >
                <X size={14} />
              </button>
            </div>
          </div>
        );
      })() : null}

      {/* Multi-step onboarding tour — 3 sequential cards walking the user
          through the seed → compose → approve flow. Bottom-left, doesn't
          block canvas interaction. Each step has Skip + Next buttons. */}
      {tourStep !== null && !showHelp ? (() => {
        const steps = [
          {
            tag: "Step 1 of 4",
            title: "Welcome to Atelier",
            body: "Your AI video studio canvas. Drop seeds, link them with references, generate takes, judge, and stitch a sequence — all in one space.",
          },
          {
            tag: "Step 2 of 4",
            title: "Drop a seed",
            body: (
              <>
                Press{" "}
                <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-foreground">V</kbd>{" "}
                for video,{" "}
                <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-foreground">I</kbd>{" "}
                for image,{" "}
                <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-foreground">T</kbd>{" "}
                for idea, or{" "}
                <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-foreground">C</kbd>{" "}
                for comment. Or double-click anywhere on empty canvas to drop a video.
              </>
            ),
          },
          {
            tag: "Step 3 of 4",
            title: "Wire it up",
            body: (
              <>
                Hover an image or completed take — left + right{" "}
                <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-foreground">+</kbd>{" "}
                ports appear on its edges. Drag the right port onto a draft to attach as reference, onto another node to branch, or to empty canvas to spawn a new draft pre-connected.
              </>
            ),
          },
          {
            tag: "Step 4 of 4",
            title: "Generate & sequence",
            body: (
              <>
                Click a draft → Composer pops up below. Press{" "}
                <kbd className="rounded border border-primary/40 bg-primary/10 px-1 font-mono text-[10px] text-primary">⌘ Enter</kbd>{" "}
                to generate. Type{" "}
                <kbd className="rounded border border-glass-border bg-glass px-1 font-mono text-[10px] text-foreground">@</kbd>{" "}
                to mention nodes (auto-attaches matching images). Drag completed takes into the bottom Sequence Strip to stitch your cut. Press{" "}
                <kbd className="rounded border border-primary/40 bg-primary/10 px-1 font-mono text-[10px] text-primary">?</kbd>{" "}
                for the full shortcut list.
              </>
            ),
          },
        ];
        const step = steps[tourStep];
        const isLast = tourStep >= steps.length - 1;
        return (
          <div
            role="status"
            aria-live="polite"
            className="absolute bottom-[120px] left-4 z-30 max-w-[300px] overflow-hidden rounded-[12px] border border-white/8 bg-[#141416]/96 shadow-[0_28px_60px_-26px_rgba(0,0,0,0.85),0_8px_18px_-6px_rgba(0,0,0,0.55),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl animate-atelier-node-in motion-reduce:animate-none"
          >
            {/* Top accent rule — primary hairline gradient signs the card as
                'instructional', not generic info card */}
            <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-primary via-primary/45 to-transparent" />
            <div className="px-4 pb-3 pt-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-primary/85">
                  {step.tag.replace(/Step (\d+) of (\d+)/, "Step $1 / $2")}
                </span>
                <button
                  type="button"
                  onClick={dismissOnboarding}
                  aria-label="Dismiss tour"
                  className="inline-flex h-5 w-5 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
                >
                  <X size={11} aria-hidden="true" />
                </button>
              </div>
              <div className="mb-1 font-display text-[14px] font-medium tracking-[-0.01em] text-foreground">
                {step.title}
              </div>
              <div className="text-[12px] leading-[1.55] text-text-secondary/95">{step.body}</div>
              <div className="mt-3 flex items-center justify-between gap-2 border-t border-white/6 pt-2.5">
                <div className="flex items-center gap-1">
                  {steps.map((_, i) => (
                    <span
                      key={i}
                      aria-hidden="true"
                      className={`h-[3px] rounded-full transition-all ${
                        i === tourStep ? "w-5 bg-primary" : i < tourStep ? "w-1.5 bg-primary/40" : "w-1.5 bg-white/12"
                      }`}
                    />
                  ))}
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={dismissOnboarding}
                    className="rounded-full px-2 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-text-muted/85 transition-colors hover:bg-hover-bg hover:text-foreground"
                  >
                    Skip
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (isLast) {
                        setShowHelp(true);
                        dismissOnboarding();
                      } else {
                        advanceOnboarding();
                      }
                    }}
                    className="rounded-full bg-primary px-2.5 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-white shadow-[inset_0_1px_0_0_rgba(255,255,255,0.16),0_4px_10px_-3px_rgba(100,108,255,0.5)] transition-all duration-200 hover:scale-[1.04] hover:bg-primary/92 hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.2),0_6px_14px_-3px_rgba(100,108,255,0.6)] active:scale-[0.96]"
                  >
                    {isLast ? "Shortcuts" : "Next"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        );
      })() : null}

      {/* Keyboard shortcut help overlay (press '?'). Outside-click + Esc
          to close. Production-grade learning surface — a glance is enough. */}
      {showHelp ? (() => {
        // Grouped sections — turns the wall of bindings into a learnable
        // map. Sections are uppercase mono captions; rows are label / kbd
        // pairs at typographic minor scale.
        const groups: Array<{ heading: string; items: Array<[string, string]> }> = [
          {
            heading: "Create",
            items: [
              ["V", "New Video Node"],
              ["I", "New Image Node"],
              ["T", "New Idea Node"],
              ["C", "New Comment"],
              ["Double-click canvas", "Quick-add video draft"],
            ],
          },
          {
            heading: "Selection",
            items: [
              ["Shift + Click", "Add to selection"],
              ["⌘ / Ctrl + Click", "Toggle in selection"],
              ["Shift + Drag empty", "Marquee box-select"],
              ["⌘ / Ctrl + A", "Select all"],
              ["Esc", "Clear selection / close menus"],
            ],
          },
          {
            heading: "Move & edit",
            items: [
              ["Drag node", "Move"],
              ["Shift + Drag node", "Snap to 8px grid"],
              ["⌘ / Ctrl + ← ↑ → ↓", "Nudge by 1px"],
              ["⌘ + Shift + ← ↑ → ↓", "Nudge by 8px"],
              ["⌘ / Ctrl + C / V / D", "Copy / Paste / Duplicate"],
              ["⌘ / Ctrl + Z / ⇧Z", "Undo / Redo"],
              ["Del / Backspace", "Delete selected"],
            ],
          },
          {
            heading: "Navigate",
            items: [
              ["← ↑ → ↓", "Nearest node"],
              ["Shift + ← ↑ → ↓", "Extend selection"],
              ["F", "Fit view"],
              ["Drag empty", "Pan canvas"],
              ["⌘ / Ctrl + Wheel", "Zoom"],
              ["⌘ / Ctrl + \\", "Toggle right rail"],
            ],
          },
          {
            heading: "Library",
            items: [
              ["A", "Toggle asset library"],
              ["Drag from library → draft", "Attach as reference"],
              ["Click category pill on image card", "Cycle: Character / Scene / Prop"],
            ],
          },
          {
            heading: "Connect",
            items: [
              ["Hover image / take", "Reveals L+R ports"],
              ["Drag right port → draft", "Attach as reference"],
              ["Drag right port → take", "Branch a new draft"],
              ["Drag right port → empty canvas", "Spawn a draft connected to source"],
              ["Click ref edge midpoint", "Select the connection"],
              ["Del on selected ref edge", "Detach reference"],
            ],
          },
          {
            heading: "Generate",
            items: [
              ["Click draft node", "Open Composer"],
              ["⌘ + Enter (in Composer)", "Generate"],
              ["@ in prompt", "Mention canvas node"],
              ["← / → in Preview", "Prev / next take"],
              ["/", "Focus Agent composer"],
            ],
          },
          {
            heading: "Sequence",
            items: [
              ["Drag take → Sequence Strip", "Append to sequence"],
              ["Take action bar → Add", "Same, via menu"],
              ["Drag a clip in strip", "Reorder"],
              ["Hover clip + ×", "Remove from sequence"],
              ["Click strip clip", "Open in preview"],
            ],
          },
        ];
        return (
          <div
            role="dialog"
            aria-label="Keyboard shortcuts"
            className="fixed inset-0 z-50 grid place-items-center bg-black/75 backdrop-blur-md animate-atelier-modal-overlay-in motion-reduce:animate-none"
            onClick={() => setShowHelp(false)}
          >
            <div
              className="w-[640px] max-w-[92vw] overflow-hidden rounded-[14px] border border-white/8 bg-[#141416] shadow-[0_36px_70px_-30px_rgba(0,0,0,0.95),0_10px_22px_-8px_rgba(0,0,0,0.65),inset_0_1px_0_0_rgba(255,255,255,0.06)] backdrop-blur-xl animate-atelier-modal-content-in motion-reduce:animate-none"
              onClick={(e) => e.stopPropagation()}
            >
              <div aria-hidden="true" className="h-[2px] bg-gradient-to-r from-primary via-primary/45 to-transparent" />
              <div className="px-5 pb-4 pt-4">
                <div className="mb-4 flex items-center justify-between">
                  <div className="flex items-baseline gap-3">
                    <span className="font-mono text-[10px] font-medium uppercase tracking-[0.22em] text-primary/85">
                      Atelier
                    </span>
                    <span className="font-display text-[15px] font-medium tracking-[-0.005em] text-foreground">
                      Shortcuts
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowHelp(false)}
                    aria-label="Close"
                    className="inline-flex h-6 w-6 items-center justify-center rounded text-text-muted transition-colors hover:bg-hover-bg hover:text-foreground"
                  >
                    <X size={13} aria-hidden="true" />
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-x-7 gap-y-5">
                  {groups.map((g) => (
                    <div key={g.heading}>
                      <div className="mb-2 font-mono text-[9px] font-medium uppercase tracking-[0.22em] text-text-muted/80">
                        {g.heading}
                      </div>
                      <div className="space-y-1">
                        {g.items.map(([keys, label]) => (
                          <div key={keys} className="flex items-baseline justify-between gap-3 py-[2px]">
                            <span className="text-[12px] text-text-secondary/95">{label}</span>
                            <kbd className="shrink-0 rounded-[3px] border border-white/8 bg-black/35 px-1.5 py-[2px] font-mono text-[10px] tracking-tight text-foreground/95">
                              {keys}
                            </kbd>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        );
      })() : null}

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
        const dotTone = isSaving
          ? "bg-blue-300 shadow-[0_0_0_3px_rgba(96,165,250,0.18)] animate-pulse"
          : hasUnrecoveredFailure
          ? "bg-red-300 shadow-[0_0_0_3px_rgba(252,165,165,0.18)]"
          : "bg-emerald-300 shadow-[0_0_0_3px_rgba(110,231,183,0.18)]";
        const tone = isSaving
          ? "text-blue-200/95"
          : hasUnrecoveredFailure
          ? "text-red-200/95"
          : "text-emerald-200/95";
        return (
          <div
            role="status"
            aria-live="polite"
            className={`absolute top-4 z-30 inline-flex items-center gap-2 rounded-full border border-white/8 bg-[#141416]/92 px-2.5 py-[5px] font-mono text-[10px] uppercase tracking-[0.18em] shadow-[inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl transition-opacity duration-300 ${tone}`}
            style={{ right: agentCollapsed ? 88 : 412 }}
          >
            <span aria-hidden="true" className={`h-[5px] w-[5px] rounded-full ${dotTone}`} />
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

      {/* Toast queue — top-center stack with kind-tinted leading dot + chrome
          surface vocab. Reads as a status readout, not a marketing notification. */}
      {toasts.length > 0 ? (
        <div className="fixed left-1/2 top-4 z-[60] flex -translate-x-1/2 flex-col gap-2">
          {toasts.map((t) => {
            const dotTone =
              t.kind === "error"
                ? "bg-red-300 shadow-[0_0_0_3px_rgba(252,165,165,0.18)]"
                : t.kind === "success"
                ? "bg-emerald-300 shadow-[0_0_0_3px_rgba(110,231,183,0.18)]"
                : "bg-primary shadow-[0_0_0_3px_rgba(100,108,255,0.18)]";
            const textTone =
              t.kind === "error" ? "text-red-100/95" :
              t.kind === "success" ? "text-emerald-100/95" :
              "text-foreground/95";
            return (
              <div
                key={t.id}
                role="status"
                className="pointer-events-auto inline-flex items-center gap-2.5 rounded-full border border-white/8 bg-[#141416]/95 px-3 py-[7px] text-[12px] shadow-[0_14px_30px_-18px_rgba(0,0,0,0.7),0_2px_6px_-2px_rgba(0,0,0,0.5),inset_0_1px_0_0_rgba(255,255,255,0.05)] backdrop-blur-xl animate-atelier-toast-in motion-reduce:animate-none"
              >
                <span aria-hidden="true" className={`h-[5px] w-[5px] shrink-0 rounded-full ${dotTone}`} />
                <span className={textTone}>{t.text}</span>
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

      {/* In-shell confirm + prompt dialogs (replaces window.confirm /
          window.prompt). Each dispatch closes the dialog after running the
          user's chosen handler — cancel just clears state. */}
      <ConfirmDialog
        open={!!confirmDialogState}
        title={confirmDialogState?.title ?? ""}
        body={confirmDialogState?.body}
        confirmLabel={confirmDialogState?.confirmLabel}
        cancelLabel={confirmDialogState?.cancelLabel}
        tone={confirmDialogState?.tone}
        onConfirm={() => {
          const fn = confirmDialogState?.onConfirm;
          setConfirmDialogState(null);
          fn?.();
        }}
        onCancel={() => setConfirmDialogState(null)}
      />
      <PromptDialog
        open={!!promptDialogState}
        title={promptDialogState?.title ?? ""}
        description={promptDialogState?.description}
        initialValue={promptDialogState?.initialValue}
        placeholder={promptDialogState?.placeholder}
        submitLabel={promptDialogState?.submitLabel}
        multiline={promptDialogState?.multiline}
        allowEmpty={promptDialogState?.allowEmpty}
        onSubmit={(value) => {
          const fn = promptDialogState?.onSubmit;
          setPromptDialogState(null);
          fn?.(value);
        }}
        onCancel={() => setPromptDialogState(null)}
      />
    </div>
  );
}
