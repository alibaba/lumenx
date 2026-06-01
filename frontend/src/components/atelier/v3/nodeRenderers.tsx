"use client";
//
// Node-rendering helpers extracted out of AtelierShellV3.tsx so the shell
// stops being a 4900-line file. Pure rendering — these functions take a
// node and the minimal props they need to dispatch to the right v3 leaf
// component, plus a couple of shared geometry / data helpers used both
// here and in the shell.
//
// Behavior is identical to the previous inline definitions; this is a
// move-only refactor (no logic changes).
import * as React from "react";
import { Play } from "lucide-react";
import {
  MediaNode,
  DraftNode,
  IdeaNode,
  CommentNode,
  PlanNode,
  toMediaNodeView,
} from "@/components/atelier/v3";
import { useAtelierStore } from "@/store/atelierStore";
import type { AtelierNode, AtelierVideoCandidate } from "@/lib/api";

// ── Geometry constants ────────────────────────────────────────────────
//
// Candidate takes (the small video thumbs spawned next to each draft)
// are laid out in a 2-column grid to the right of the parent draft.
// Same numbers the shell used to inline.
export const PARENT_TO_CAND_GAP = 32;
export const CAND_WIDTH = 200;
export const CAND_HEIGHT = 113;
export const CAND_GAP = 16;

// ── Pure data helpers ─────────────────────────────────────────────────

export function selectionKindOf(
  node: AtelierNode,
): "image" | "video" | "audio" | "draft" | "idea" | "comment" {
  if (node.type === "image") return "image";
  if (node.type === "audio") return "audio";
  if (node.type === "idea") return "idea";
  if (node.type === "comment") return "comment";
  if (node.type === "video" && node.status === "draft") return "draft";
  return "video";
}

export function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

export function readStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

export function readCandidates(node: AtelierNode): AtelierVideoCandidate[] {
  const raw = (node.data as { candidates?: unknown })?.candidates;
  if (!Array.isArray(raw)) return [];
  return raw as AtelierVideoCandidate[];
}

export function isDraftVideo(node: AtelierNode): boolean {
  // Any draft-status video is a DraftNode candidate. The intent string is
  // pulled from data.intent → title → "Untitled" further down so a node
  // missing data.intent still renders as a draft (instead of falling to
  // the wall-of-text empty-video card).
  return node.type === "video" && node.status === "draft";
}

export function candidateNodeId(parentId: string, candidateId: string): string {
  return `${parentId}::cand::${candidateId}`;
}

export function parseCandidateNodeId(
  id: string,
): { parentId: string; candidateId: string } | null {
  const m = id.match(/^(.+)::cand::(.+)$/);
  if (!m) return null;
  return { parentId: m[1], candidateId: m[2] };
}

export function candidatePosition(
  parent: AtelierNode,
  index: number,
): { x: number; y: number } {
  const parentRight = parent.x + (parent.width || 240) + PARENT_TO_CAND_GAP;
  const x = parentRight + (index % 2) * (CAND_WIDTH + CAND_GAP);
  const y = parent.y + Math.floor(index / 2) * (CAND_HEIGHT + 15);
  return { x, y };
}

// ── Node renderers ────────────────────────────────────────────────────

export function renderNode(
  node: AtelierNode,
  selectedIds: Set<string>,
  select: (id: string | null) => void,
  imageActions?: {
    onUpload: (id: string) => void;
    onGenerate: (id: string) => void;
  },
  /** Id of the idea/comment node currently being edited inline by the
   *  shell. The matching node renders in editing-mode (chrome only) so the
   *  overlaid textarea isn't doubled with the underlying body. */
  editingTextNodeId?: string | null,
  /** v0.6.2/v0.6.3/v0.7 — connection drag wiring. The shell binds the
   *  AtelierNode and fires handlePortDragOut. We forward this to the
   *  leaf for sources handlePortDragOut actually accepts:
   *    - image MediaNode with src (isImageSource)
   *    - compact draft node WITH ≥1 completed take + video_url
   *      (v0.7 item H — backend resolves to the draft's selected /
   *      first-completed take URL via attachReferenceNode)
   *  Audio, top-level (non-candidate) non-draft videos, empty drafts,
   *  ideas, comments, plans — all get NO onPortDown, which keeps their
   *  output PortDot decorative (no data-port, no hover affordance,
   *  gesture falls through to parent node select/drag). Candidate takes
   *  are wired separately via renderCandidatesAsMediaNodes; the selected
   *  draft's expanded workbench is wired separately by the shell. */
  onPortDown?: (node: AtelierNode, event: React.PointerEvent) => void,
): React.ReactNode {
  const isSelected = selectedIds.has(node.id);
  const onSelect = () => select(node.id);
  const isEditingThis = editingTextNodeId === node.id;
  const portHandler = onPortDown ? (event: React.PointerEvent) => onPortDown(node, event) : undefined;

  if (node.type === "image") {
    const view = toMediaNodeView(node, { selectedNodeId: null });
    if (!view) return null;
    // Wire upload / generate actions only for empty image drafts — that's
    // the only state where the actionable card replaces the placeholder.
    const isEmptyDraft = !view.src;
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
        onUpload={isEmptyDraft && imageActions ? imageActions.onUpload : undefined}
        onGenerate={isEmptyDraft && imageActions ? imageActions.onGenerate : undefined}
        // v0.6.3 — image is a valid handlePortDragOut source ONLY when it
        // has media (isImageSource = type==="image" && media_urls>0). For
        // empty image drafts we leave the port decorative.
        onPortDown={!isEmptyDraft ? portHandler : undefined}
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
        // v0.6.3 — handlePortDragOut has no audio path; leave the dot
        // decorative so pointer-downs on it still drag the parent node.
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
        editing={isEditingThis}
      />
    );
  }

  if (node.type === "comment") {
    const body = readString(node.data?.body) ?? node.prompt ?? "";
    const author = readString((node.data as { author?: unknown })?.author);
    return (
      <CommentNode
        key={node.id}
        id={node.id}
        body={body}
        author={author}
        selected={isSelected}
        x={node.x}
        y={node.y}
        onSelect={onSelect}
        editing={isEditingThis}
      />
    );
  }

  if (node.type === "plan") {
    const title = node.title || "Plan";
    const bullets = readStringArray((node.data as { bullets?: unknown })?.bullets);
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
          void useAtelierStore
            .getState()
            .updateNode(node.id, { title: next })
            .catch(() => {/* save chip surfaces failures */});
        }}
      />
    );
  }

  if (node.type === "video") {
    if (isDraftVideo(node)) {
      // Selected drafts render as a DraftWorkbench overlay in the
      // shell (RHTV/LibTV pattern: selected node IS the workbench).
      // Skip the compact card here so the two don't double up.
      if (isSelected) return null;
      const intent =
        readString(node.data?.intent) ?? node.title ?? "Untitled draft";
      const modelLabel = readString(node.data?.model) ?? "Wan 2.7";
      const configSummary =
        readString(node.data?.config_summary) ?? "1280×720 · 5s · 4×";
      const refs = readStringArray(node.data?.reference_image_urls);
      const cands = readCandidates(node);
      const candidatesReady = cands.filter((c) => c.status === "completed").length;
      const candidatesTotal = cands.length;
      // v0.7 (item H) — compact draft is a valid handlePortDragOut source
      // ONLY when it has at least one completed take with a video URL.
      // The backend / store resolves the source draft to its selected (or
      // first completed) take's video_url when attachReferenceNode runs.
      // Empty drafts stay decorative — pointer-down on the dot falls
      // through to parent node select/drag.
      const hasCompletedTake = cands.some(
        (c) => c.status === "completed" && !!c.video_url,
      );
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
          onDetachRef={(url) => {
            void useAtelierStore.getState().detachReferenceNode(node.id, url).catch(() => {});
          }}
          onPortDown={hasCompletedTake ? portHandler : undefined}
        />
      );
    }
    const view = toMediaNodeView(node, { selectedNodeId: null });
    const candidateCount = readCandidates(node).length;
    // Empty-video fallback: a non-draft video node with no media + no
    // candidates. With the broadened isDraftVideo this is rare — only
    // triggered by orphaned legacy nodes whose status was bumped past
    // 'draft' before any media was attached.
    if (!view?.src && candidateCount === 0) {
      return (
        <div
          key={node.id}
          className={`absolute w-[200px] rounded-md border bg-elevated transition-shadow shadow-2xl shadow-black/40 hover:shadow-[0_0_0_1px_rgba(59,107,255,0.18)] ${
            isSelected ? "ring-1 ring-white/25 border-white/20" : "border-glass-border"
          }`}
          style={{ transform: `translate(${node.x}px, ${node.y}px)` }}
          role="button"
          tabIndex={0}
          onPointerDown={(e) => {
            e.stopPropagation();
            onSelect();
          }}
        >
          <div className="flex flex-col items-center gap-1 px-3 py-3 text-center">
            <Play size={14} className="text-text-muted" aria-hidden="true" />
            <div className="text-[11px] text-text-muted">
              {node.title || "Empty video"}
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
          // v0.6.3 — top-level (non-candidate) videos are not a valid
          // handlePortDragOut source (isTakeSource only matches the
          // parent::cand::id form). Leave the dot decorative.
        />
      );
    }
  }

  return null;
}

export function renderCandidatesAsMediaNodes(
  node: AtelierNode,
  selectedIds: Set<string>,
  select: (id: string | null) => void,
  onRetry: (parentId: string, candidateId: string) => void,
  retryModelOptions?: string[],
  onRetryWithModel?: (parentId: string, candidateId: string, modelLabel: string) => void,
  onCancel?: (parentId: string, candidateId: string) => Promise<void> | void,
  // v0.5.5 composition density (mode "a"): when the shell has a focal node
  // (hover OR sticky selection), dim every candidate not on the focal
  // constellation. Passed in from the shell so this renderer doesn't have
  // to know about hoveredNodeId / selectedNodeId / link graph.
  focalDim?: { focalNodeId: string | null; relatedKeys: Set<string> },
  /** v0.6.2 — connection drag wiring for candidate takes. The shell
   *  builds a virtual AtelierNode for the candidate id (parent::cand::id)
   *  so handlePortDragOut treats it as an isTakeSource. */
  onPortDown?: (candidateNodeKey: string, event: React.PointerEvent) => void,
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
    // Completed takes are draggable to the Sequence Strip via HTML5
    // drag-and-drop. We carry a custom mime type so the strip's drop
    // handler can distinguish take drags from regular pointer drags
    // (canvas pan, marquee, etc.). Payload is always an array — single
    // take is just an array of one. The strip iterates and appends in
    // order, so multi-selecting takes (Shift / Cmd click) and dragging
    // any one of them drops the whole batch in selection order.
    const isMultiSelected = selectedIds.has(candKey) && selectedIds.size > 1;
    const buildBatchPayload = () => {
      if (!isMultiSelected) {
        return JSON.stringify([{ parentId: node.id, candidateId: c.id }]);
      }
      // Walk the parent's candidate list (same lookup we already do)
      // and emit every selected one. This keeps order stable so the
      // strip mirrors the candidate-grid order, not selection order.
      const batch: Array<{ parentId: string; candidateId: string }> = [];
      for (const cc of candidates) {
        const k = candidateNodeId(node.id, cc.id);
        if (selectedIds.has(k) && cc.status === "completed" && !!cc.video_url) {
          batch.push({ parentId: node.id, candidateId: cc.id });
        }
      }
      // If somehow our own candKey wasn't included (shouldn't happen
      // since isMultiSelected required selectedIds.has(candKey)), drop
      // back to single-take payload.
      if (batch.length === 0) {
        return JSON.stringify([{ parentId: node.id, candidateId: c.id }]);
      }
      return JSON.stringify(batch);
    };
    // v0.5.5 — focal dim for virtual candidate nodes. Mirrors the same
    // logic the shell applies to real nodes so the dimming reads as a
    // single canvas-wide effect, not "real nodes dim, takes don't".
    // Selected (isMultiSelected OR primary) candidates stay bright.
    const isCandSelected = selectedIds.has(candKey);
    const isCandDimmed =
      !!focalDim?.focalNodeId &&
      !isCandSelected &&
      focalDim.focalNodeId !== candKey &&
      !focalDim.relatedKeys.has(candKey);
    return (
      // Wrap each candidate MediaNode with a positional shell that carries
      // data-atelier-node, so the Composer's DOM-rect anchor lookup can
      // find candidate takes the same way it finds top-level nodes.
      <div
        key={candKey}
        data-atelier-node={candKey}
        data-atelier-dim={isCandDimmed ? "true" : undefined}
        draggable={c.status === "completed" && !!c.video_url}
        onDragStart={(e) => {
          if (c.status !== "completed" || !c.video_url) return;
          e.dataTransfer.effectAllowed = "copyLink";
          e.dataTransfer.setData("application/x-atelier-take", buildBatchPayload());
          // Plain text fallback so dropping into other surfaces (e.g.,
          // the agent prompt) still gets a meaningful payload.
          e.dataTransfer.setData("text/plain", `@${c.label || c.id.slice(0, 8)}`);
        }}
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: 0,
          height: 0,
          opacity: isCandDimmed ? 0.28 : undefined,
          filter: isCandDimmed ? "saturate(0.55)" : undefined,
          transition: "opacity 220ms ease-out, filter 220ms ease-out",
        }}
      >
        <MediaNode
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
          retryModelOptions={c.status === "failed" ? retryModelOptions : undefined}
          onRetryWithModel={
            c.status === "failed" && onRetryWithModel
              ? (_, modelLabel) => onRetryWithModel(node.id, c.id, modelLabel)
              : undefined
          }
          onCancel={
            (c.status === "pending" || c.status === "processing") && onCancel
              ? () => onCancel(node.id, c.id)
              : undefined
          }
          onPortDown={onPortDown ? (event) => onPortDown(candKey, event) : undefined}
        />
      </div>
    );
  });
}
