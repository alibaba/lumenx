"use client";

import * as React from "react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";
import { CommentNode } from "./CommentNode";
import { IdeaNode } from "./IdeaNode";
import { MediaNode } from "./MediaNode";
import { PlanNode, type PlanStep } from "./PlanNode";
import { RegionFrame, type RegionColor } from "./RegionFrame";
import { RegionMembershipControls } from "./RegionMembershipControls";
import { ReferenceControls } from "./ReferenceControls";
import { MediaUploadButton } from "./MediaUploadButton";
import { GenerationPanel } from "./GenerationPanel";
import { MediaComposer } from "./MediaComposer";
import { AgentTurnPanel } from "./AgentTurnPanel";
import { SequenceWorkbench } from "./SequenceWorkbench";
import { ProjectPackageControls } from "./ProjectPackageControls";
import { WorkflowsPanel } from "./WorkflowsPanel";
import {
  WORKFLOW_TEMPLATE_MIME,
  getBuiltInWorkflowTemplate,
  validateBuiltInWorkflowTemplate,
  type WorkflowTemplate,
} from "./workflowTemplates";
import { toMediaNodeView } from "./types";
import { useAtelierStore } from "@/store/atelierStore";
import { getRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import {
  ATELIER_MEDIA_RENDERED_BOUNDS,
  buildReferenceLinks,
  canAttachAtelierReference,
  findOpenAtelierCanvasPosition,
  getAtelierCanvasPlacementRects,
  getAtelierPendingReferenceNodes,
  getAtelierReferenceMediaUrls,
  hasAtelierNodeActiveExecution,
} from "@/lib/atelierCanvas";
import { ATELIER_IMAGE_UPLOAD_ACCEPT, ATELIER_UPLOAD_ACCEPT, getAtelierUploadKind } from "@/lib/atelierUploadApi";
import type { AtelierNode, JsonValue } from "@/lib/atelierTypes";
import "./atelier-v3.css";

type ShellOwner = {
  key: string;
  generation: number;
  projectId: string | null;
  session: number;
};

type Draft = {
  owner: string | null;
  title: string;
  prompt: string;
  body: string;
};

type DragState = {
  owner: ShellOwner;
  nodeId: string;
  startClientX: number;
  startClientY: number;
  startX: number;
  startY: number;
  width: number;
  height: number;
  mode: "move" | "resize";
  handle?: "nw" | "ne" | "sw" | "se";
};

type PanState = {
  startClientX: number;
  startClientY: number;
  startX: number;
  startY: number;
};

type Preview = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
};

type WorkflowNotice = {
  owner: string;
  kind: "success" | "partial" | "error";
  text: string;
};

type CanvasLink = {
  key: string;
  from: AtelierNode;
  to: AtelierNode;
  pending: boolean;
  stale?: boolean;
};

const REGION_COLORS = new Set<RegionColor>(["default", "cyan", "rose", "amber", "violet", "emerald", "slate"]);

function stringValue(value: JsonValue | undefined): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function boolValue(value: JsonValue | undefined): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function bodyOf(node: AtelierNode | null): string {
  return stringValue(node?.data?.body) ?? node?.prompt ?? "";
}

function authorOf(node: AtelierNode): string | undefined {
  return stringValue(node.data?.author);
}

function regionColorOf(node: AtelierNode): RegionColor {
  const color = stringValue(node.data?.color);
  return color && REGION_COLORS.has(color as RegionColor) ? color as RegionColor : "default";
}

function collapsedOf(node: AtelierNode | null | undefined): boolean {
  return boolValue(node?.data?.collapsed) ?? false;
}

function draftOwner(owner: ShellOwner, nodeId: string | null | undefined): string | null {
  return owner.projectId && nodeId ? `${ownerKey(owner)}:${nodeId}` : null;
}

function ownerKey(owner: ShellOwner): string {
  return `${owner.key}:${owner.generation}:${owner.projectId ?? ""}:${owner.session}`;
}

function shellOwnerFromSnapshot(environment: ReturnType<typeof getRuntimeEnvironment>, projectId: string | null | undefined, session: number): ShellOwner | null {
  if (!environment) return null;
  return { key: environment.key, generation: environment.generation, projectId: projectId ?? null, session };
}

function commandButtonClass(disabled: boolean): string {
  return `rounded-xl border px-3 py-2 text-sm transition ${disabled
    ? "cursor-not-allowed border-white/[0.06] bg-white/[0.03] text-white/30"
    : "border-white/[0.1] bg-white/[0.06] text-white/75 hover:border-white/[0.18] hover:bg-white/[0.1] hover:text-white"}`;
}

function fieldClass(): string {
  return "w-full rounded-xl border border-white/[0.1] bg-black/35 px-3 py-2 text-sm text-white/80 outline-none transition focus:border-white/25";
}

function nextCanvasPosition(nodes: AtelierNode[] | undefined, width: number, height: number, startX = 80, startY = 96): { x: number; y: number; width: number; height: number } {
  return {
    ...findOpenAtelierCanvasPosition(getAtelierCanvasPlacementRects(nodes ?? []), { width, height }, { startX, startY }),
    width,
    height,
  };
}

function isBodyNodeType(type: string | null | undefined): boolean {
  return type === "idea" || type === "comment";
}

function isBodyNode(node: AtelierNode | null): boolean {
  return isBodyNodeType(node?.type);
}

function stringArrayValue(value: JsonValue | undefined): string[] {
  return Array.isArray(value) && value.every(item => typeof item === "string") ? value : [];
}

function planStepsValue(value: JsonValue | undefined): PlanStep[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const steps: PlanStep[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return undefined;
    const record = item as Record<string, JsonValue | undefined>;
    if (typeof record.id !== "string" || typeof record.title !== "string") return undefined;
    if (record.status !== "pending" && record.status !== "in_progress" && record.status !== "completed") return undefined;
    const step: PlanStep = { id: record.id, title: record.title, status: record.status };
    if (typeof record.notes === "string") step.notes = record.notes;
    steps.push(step);
  }
  return steps;
}

function buildCanvasReferenceLinks(nodes: AtelierNode[]): CanvasLink[] {
  const byId = new Map(nodes.map(node => [node.id, node]));
  const resolved = buildReferenceLinks(nodes).map(link => ({
    key: `resolved:${link.key}`,
    from: link.from,
    to: link.to,
    pending: false,
    stale: link.stale,
  }));
  const pending: CanvasLink[] = [];
  for (const target of nodes) {
    for (const reference of getAtelierPendingReferenceNodes(target)) {
      const source = byId.get(reference.source_node_id);
      if (!source || source.type !== reference.source_kind || !canAttachAtelierReference(target, source)) continue;
      pending.push({ key: `pending:${source.id}:${target.id}`, from: source, to: target, pending: true });
    }
  }
  return [...resolved, ...pending];
}

function canvasPointFromDrop(event: React.DragEvent, viewport: HTMLDivElement | null, pan: { x: number; y: number }, zoom: number): { x: number; y: number } {
  const rect = viewport?.getBoundingClientRect();
  if (!rect || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return { x: 80, y: 96 };
  return { x: (event.clientX - rect.left - pan.x) / zoom, y: (event.clientY - rect.top - pan.y) / zoom };
}

function resizePreview(drag: DragState, dx: number, dy: number): Preview {
  const minWidth = 120;
  const minHeight = 80;
  if (drag.handle === "nw") {
    const width = Math.max(minWidth, drag.width - dx);
    const height = Math.max(minHeight, drag.height - dy);
    return { x: drag.startX + drag.width - width, y: drag.startY + drag.height - height, width, height };
  }
  if (drag.handle === "ne") {
    const width = Math.max(minWidth, drag.width + dx);
    const height = Math.max(minHeight, drag.height - dy);
    return { y: drag.startY + drag.height - height, width, height };
  }
  if (drag.handle === "sw") {
    const width = Math.max(minWidth, drag.width - dx);
    const height = Math.max(minHeight, drag.height + dy);
    return { x: drag.startX + drag.width - width, width, height };
  }
  return { width: Math.max(minWidth, drag.width + dx), height: Math.max(minHeight, drag.height + dy) };
}

export function AtelierShell() {
  const t = useTranslations("atelier.foundation");
  const projects = useAtelierStore(state => state.projects);
  const currentProject = useAtelierStore(state => state.currentProject);
  const selectedNodeId = useAtelierStore(state => state.selectedNodeId);
  const isLoading = useAtelierStore(state => state.isLoading);
  const isSaving = useAtelierStore(state => state.isSaving);
  const needsRefresh = useAtelierStore(state => state.needsRefresh);
  const error = useAtelierStore(state => state.error);
  const ensureProject = useAtelierStore(state => state.ensureProject);
  const createProject = useAtelierStore(state => state.createProject);
  const loadProjects = useAtelierStore(state => state.loadProjects);
  const switchProject = useAtelierStore(state => state.switchProject);
  const refreshProject = useAtelierStore(state => state.refreshProject);
  const createNode = useAtelierStore(state => state.createNode);
  const updateNodeText = useAtelierStore(state => state.updateNodeText);
  const updateNodeLayout = useAtelierStore(state => state.updateNodeLayout);
  const updateRegionAppearance = useAtelierStore(state => state.updateRegionAppearance);
  const moveNodes = useAtelierStore(state => state.moveNodes);
  const wrapRegion = useAtelierStore(state => state.wrapRegion);
  const deleteNodes = useAtelierStore(state => state.deleteNodes);
  const restoreNodes = useAtelierStore(state => state.restoreNodes);
  const attachPendingReferenceNode = useAtelierStore(state => state.attachPendingReferenceNode);
  const attachReferenceNode = useAtelierStore(state => state.attachReferenceNode);
  const detachReferenceNode = useAtelierStore(state => state.detachReferenceNode);
  const createMediaNode = useAtelierStore(state => state.createMediaNode);
  const uploadReference = useAtelierStore(state => state.uploadReference);
  const pendingUploads = useAtelierStore(state => state.pendingUploads);
  const pendingGenerations = useAtelierStore(state => state.pendingGenerations);
  const loadPendingUploads = useAtelierStore(state => state.loadPendingUploads);
  const loadPendingGenerations = useAtelierStore(state => state.loadPendingGenerations);
  const readbackPendingUpload = useAtelierStore(state => state.readbackPendingUpload);
  const readbackPendingGeneration = useAtelierStore(state => state.readbackPendingGeneration);
  const createImageCandidate = useAtelierStore(state => state.createImageCandidate);
  const createAudioCandidate = useAtelierStore(state => state.createAudioCandidate);
  const refreshGenerationAttempt = useAtelierStore(state => state.refreshGenerationAttempt);
  const continueGenerationAttempt = useAtelierStore(state => state.continueGenerationAttempt);
  const cancelGenerationAttempt = useAtelierStore(state => state.cancelGenerationAttempt);
  const saveMediaGenerationDraft = useAtelierStore(state => state.saveMediaGenerationDraft);
  const selectMediaGenerationCandidate = useAtelierStore(state => state.selectMediaGenerationCandidate);
  const setMediaGenerationCandidateVisibility = useAtelierStore(state => state.setMediaGenerationCandidateVisibility);
  const selectNode = useAtelierStore(state => state.selectNode);

  const [localError, setLocalError] = useState<string | null>(null);
  const [uploadJournalError, setUploadJournalError] = useState<{ owner: string; text: string } | null>(null);
  const [checkingUpload, setCheckingUpload] = useState<string | null>(null);
  const [uploadNotice, setUploadNotice] = useState<{ owner: string; text: string } | null>(null);
  const [workflowNotice, setWorkflowNotice] = useState<WorkflowNotice | null>(null);
  const [workflowsOpen, setWorkflowsOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>({ owner: null, title: "", prompt: "", body: "" });
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [preview, setPreview] = useState<Record<string, Preview>>({});
  const [interactionSession, setInteractionSession] = useState(0);
  const sessionRef = useRef(0);
  const dragRef = useRef<DragState | null>(null);
  const panRef = useRef<PanState | null>(null);
  const workflowInsertRef = useRef(false);
  const canvasViewportRef = useRef<HTMLDivElement>(null);
  const canvasToolbarRef = useRef<HTMLDivElement>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  const [layoutEpoch, setLayoutEpoch] = useState(0);
  const runtimeEnvironment = useSyncExternalStore(subscribeRuntimeEnvironment, getRuntimeEnvironment, getRuntimeEnvironment);

  useLayoutEffect(() => {
    let frame = 0;
    const bump = () => {
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; setLayoutEpoch(epoch => epoch + 1); });
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(bump);
    if (canvasViewportRef.current) observer?.observe(canvasViewportRef.current);
    if (canvasToolbarRef.current) observer?.observe(canvasToolbarRef.current);
    if (inspectorRef.current) observer?.observe(inspectorRef.current);
    window.addEventListener("resize", bump);
    return () => {
      observer?.disconnect();
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", bump);
    };
  }, []);

  const selectedNode = useMemo(
    () => currentProject?.nodes.find(node => node.id === selectedNodeId) ?? null,
    [currentProject, selectedNodeId],
  );
  const shellOwner = useMemo(() => shellOwnerFromSnapshot(runtimeEnvironment, currentProject?.id, interactionSession), [currentProject?.id, interactionSession, runtimeEnvironment]);
  const shellOwnerId = shellOwner ? ownerKey(shellOwner) : "no-runtime";
  const selectedOwner = shellOwner ? draftOwner(shellOwner, selectedNode?.id) : null;
  const selectedComposerLayout = useMemo(() => {
    void layoutEpoch;
    if (!selectedNode || (selectedNode.type !== "video" && selectedNode.type !== "image" && selectedNode.type !== "audio")) return { rect: null, availableRect: null };
    const viewport = canvasViewportRef.current?.getBoundingClientRect();
    if (!viewport) return { rect: null, availableRect: null };
    const toolbar = canvasToolbarRef.current?.getBoundingClientRect();
    const availableTop = Math.max(viewport.top, toolbar && toolbar.bottom > viewport.top ? toolbar.bottom + 12 : viewport.top);
    return {
      rect: {
        left: viewport.left + pan.x + selectedNode.x * zoom,
        top: viewport.top + pan.y + selectedNode.y * zoom,
        width: selectedNode.width * zoom,
        height: selectedNode.height * zoom,
      },
      availableRect: {
        left: viewport.left,
        top: availableTop,
        width: viewport.width,
        height: Math.max(0, viewport.top + viewport.height - availableTop),
      },
    };
  }, [layoutEpoch, pan.x, pan.y, selectedNode, zoom]);
  const writeDisabled = isSaving || isLoading || needsRefresh || !currentProject || !shellOwner;
  const uploadDisabled = writeDisabled || pendingUploads.length > 0;
  const selectedNodeUploadBlocked = !!selectedNode && hasAtelierNodeActiveExecution(selectedNode);
  const unconsumedReceipts = currentProject?.node_delete_receipts.filter(receipt => receipt.restored_at === null) ?? [];
  const canvasLinks = useMemo(() => buildCanvasReferenceLinks(currentProject?.nodes ?? []), [currentProject?.nodes]);
  const workflowNoticeText = workflowNotice?.owner === shellOwnerId ? workflowNotice.text : null;

  const bumpInteractionSession = useCallback(() => {
    sessionRef.current += 1;
    setInteractionSession(sessionRef.current);
  }, []);

  const isCurrentOwner = useCallback((owner: ShellOwner) => {
    try {
      const current = shellOwnerFromSnapshot(getRuntimeEnvironment(), useAtelierStore.getState().currentProject?.id ?? null, sessionRef.current);
      return !!current && current.key === owner.key && current.generation === owner.generation && current.projectId === owner.projectId && current.session === owner.session;
    } catch {
      return false;
    }
  }, []);

  useEffect(() => {
    return subscribeRuntimeEnvironment(() => {
      dragRef.current = null;
      panRef.current = null;
      setPreview({});
      setLocalError(null);
      bumpInteractionSession();
    });
  }, [bumpInteractionSession]);

  useEffect(() => {
    dragRef.current = null;
    setPreview({});
  }, [shellOwnerId]);

  useEffect(() => {
    if (!shellOwner?.projectId) return;
    const refresh = () => {
      try {
        loadPendingUploads();
        loadPendingGenerations();
        if (isCurrentOwner(shellOwner)) setUploadJournalError(null);
      } catch (cause) {
        if (isCurrentOwner(shellOwner)) setUploadJournalError({ owner: ownerKey(shellOwner), text: cause instanceof Error ? cause.message : t("uploadError") });
      }
    };
    refresh();
    window.addEventListener("storage", refresh);
    window.addEventListener("focus", refresh);
    return () => { window.removeEventListener("storage", refresh); window.removeEventListener("focus", refresh); };
  }, [isCurrentOwner, loadPendingGenerations, loadPendingUploads, shellOwner, t]);

  useEffect(() => {
    let alive = true;
    const owner = shellOwner;
    if (!owner) return () => { alive = false; };
    ensureProject().catch(err => {
      if (alive && isCurrentOwner(owner)) setLocalError(err instanceof Error ? err.message : String(err));
    });
    return () => { alive = false; };
  }, [ensureProject, isCurrentOwner, shellOwner]);

  useEffect(() => {
    if (draft.owner === selectedOwner) return;
    setDraft({
      owner: selectedOwner,
      title: selectedNode?.title ?? "",
      prompt: selectedNode?.prompt ?? "",
      body: bodyOf(selectedNode),
    });
  }, [draft.owner, selectedNode, selectedOwner]);

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const panDrag = panRef.current;
      if (panDrag) {
        setPan({ x: panDrag.startX + event.clientX - panDrag.startClientX, y: panDrag.startY + event.clientY - panDrag.startClientY });
        return;
      }
      const drag = dragRef.current;
      if (!drag) return;
      const dx = (event.clientX - drag.startClientX) / zoom;
      const dy = (event.clientY - drag.startClientY) / zoom;
      let next: Preview;
      if (drag.mode === "move") {
        next = { x: drag.startX + dx, y: drag.startY + dy };
        const project = useAtelierStore.getState().currentProject;
        const regionMembers = project?.nodes.filter(child => stringValue(child.data?.region_id) === drag.nodeId) ?? [];
        setPreview(prev => {
          const copy = { ...prev, [drag.nodeId]: next };
          for (const child of regionMembers) copy[child.id] = { x: child.x + dx, y: child.y + dy };
          return copy;
        });
        return;
      }
      next = resizePreview(drag, dx, dy);
      setPreview(prev => ({ ...prev, [drag.nodeId]: next }));
    };
    const onUp = () => {
      if (panRef.current) {
        panRef.current = null;
        return;
      }
      const drag = dragRef.current;
      if (!drag) return;
      dragRef.current = null;
      const next = preview[drag.nodeId];
      setPreview(prev => {
        const copy = { ...prev };
        delete copy[drag.nodeId];
        const project = useAtelierStore.getState().currentProject;
        for (const child of project?.nodes.filter(item => stringValue(item.data?.region_id) === drag.nodeId) ?? []) delete copy[child.id];
        return copy;
      });
      if (!next || writeDisabled || !isCurrentOwner(drag.owner)) return;
      const request = drag.mode === "move" && useAtelierStore.getState().currentProject?.nodes.find(node => node.id === drag.nodeId)?.type === "region"
        ? moveNodes({ nodeIds: [drag.nodeId], dx: (next.x ?? drag.startX) - drag.startX, dy: (next.y ?? drag.startY) - drag.startY })
        : updateNodeLayout(drag.nodeId, next);
      request.catch(err => {
        if (isCurrentOwner(drag.owner)) setLocalError(err instanceof Error ? `${t("layoutError")}: ${err.message}` : t("layoutError"));
      });
    };
    const onCancel = () => {
      dragRef.current = null;
      panRef.current = null;
      setPreview({});
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
    };
  }, [isCurrentOwner, moveNodes, preview, t, updateNodeLayout, writeDisabled, zoom]);

  const beginDrag = useCallback((node: AtelierNode, event: React.PointerEvent, mode: "move" | "resize" = "move", handle?: DragState["handle"]) => {
    const target = event.target as HTMLElement;
    if (target.closest("button,input,textarea,select,a")) return;
    selectNode(node.id);
    if (writeDisabled) return;
    const owner = shellOwnerFromSnapshot(getRuntimeEnvironment(), currentProject?.id, sessionRef.current);
    if (!owner) return;
    dragRef.current = {
      owner,
      nodeId: node.id,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: node.x,
      startY: node.y,
      width: node.width,
      height: node.height,
      mode,
      handle,
    };
  }, [currentProject?.id, selectNode, writeDisabled]);

  const run = useCallback(async (action: (owner: ShellOwner) => Promise<unknown>, fallback: string) => {
    const owner = shellOwnerFromSnapshot(getRuntimeEnvironment(), useAtelierStore.getState().currentProject?.id ?? null, sessionRef.current);
    if (!owner) {
      setLocalError(fallback);
      return;
    }
    setLocalError(null);
    try {
      await action(owner);
    } catch (err) {
      if (isCurrentOwner(owner)) setLocalError(err instanceof Error ? `${fallback}: ${err.message}` : fallback);
    }
  }, [isCurrentOwner]);

  const createNodeAndSelect = useCallback((type: "idea" | "comment" | "image" | "video" | "audio") => {
    void run(async owner => {
      const index = (currentProject?.nodes.length ?? 0) + 1;
      const layout = nextCanvasPosition(currentProject?.nodes, type === "video" ? 220 : 260, type === "video" ? 124 : type === "audio" ? 96 : 160);
      const titleKey = type === "idea" ? "createIdea" : type === "comment" ? "createComment" : type === "image" ? "createImage" : type === "video" ? "createVideo" : "createAudio";
      const ack = await createNode({
        type,
        title: `${t(titleKey)} ${index}`,
        prompt: "",
        body: type === "idea" || type === "comment" ? "" : null,
        ...layout,
      });
      if (isCurrentOwner(owner) && useAtelierStore.getState().currentProject?.id === ack.project.id) selectNode(ack.node_ids[0] ?? null);
    }, t("createError"));
  }, [createNode, currentProject?.nodes, isCurrentOwner, run, selectNode, t]);

  const createRegion = useCallback(() => {
    void run(async owner => {
      const index = (currentProject?.nodes.length ?? 0) + 1;
      const layout = nextCanvasPosition(currentProject?.nodes, 320, 180, 120, 130);
      const ack = await wrapRegion({
        title: `${t("createRegion")} ${index}`,
        memberNodeIds: [],
        ...layout,
        color: "default",
        collapsed: false,
      });
      if (isCurrentOwner(owner) && useAtelierStore.getState().currentProject?.id === ack.project.id) selectNode(ack.node_ids[0] ?? null);
    }, t("createError"));
  }, [currentProject?.nodes, isCurrentOwner, run, selectNode, t, wrapRegion]);

  const insertWorkflowTemplate = useCallback((template: WorkflowTemplate, anchor = { x: 80, y: 96 }) => {
    if (workflowInsertRef.current) return;
    const validation = validateBuiltInWorkflowTemplate(template);
    if (!validation.ok) {
      setLocalError(`${t("workflowInsertError")}: ${validation.reason}`);
      return;
    }
    workflowInsertRef.current = true;
    void run(async owner => {
      if (!isCurrentOwner(owner)) throw new Error("Atelier environment or project changed during request");
      const ownerId = ownerKey(owner);
      const localToReal = new Map<string, string>();
      let createdCount = 0;
      setWorkflowNotice(null);
      try {
        for (const item of template.nodes) {
          if (!isCurrentOwner(owner)) throw new Error("Atelier environment or project changed during request");
          const ack = await createNode({
            type: item.type,
            title: item.title,
            prompt: item.prompt ?? "",
            body: item.body ?? (isBodyNodeType(item.type) ? "" : null),
            x: anchor.x + item.x,
            y: anchor.y + item.y,
            width: item.width,
            height: item.height,
          });
          if (!isCurrentOwner(owner) || ack.project.id !== owner.projectId || useAtelierStore.getState().currentProject?.id !== owner.projectId) {
            throw new Error("Atelier environment or project changed during request");
          }
          const realId = ack.node_ids[0];
          if (!realId) throw new Error("Atelier create acknowledgement did not include a node id");
          localToReal.set(item.localId, realId);
          createdCount += 1;
        }
        for (const edge of template.edges) {
          if (!isCurrentOwner(owner)) throw new Error("Atelier environment or project changed during request");
          const sourceId = localToReal.get(edge.source);
          const targetId = localToReal.get(edge.target);
          if (!sourceId || !targetId) throw new Error("Workflow template edge target was not created");
          const project = useAtelierStore.getState().currentProject;
          const source = project?.nodes.find(node => node.id === sourceId);
          const target = project?.nodes.find(node => node.id === targetId);
          if (!project || project.id !== owner.projectId || !source || !target || !canAttachAtelierReference(target, source)) {
            throw new Error("Workflow template edge no longer matches the current project");
          }
          const ack = getAtelierReferenceMediaUrls(source).length > 0
            ? await attachReferenceNode(targetId, { sourceNodeId: sourceId })
            : await attachPendingReferenceNode(targetId, { sourceNodeId: sourceId });
          if (!isCurrentOwner(owner) || ack.project.id !== owner.projectId || useAtelierStore.getState().currentProject?.id !== owner.projectId) {
            throw new Error("Atelier environment or project changed during request");
          }
        }
        if (isCurrentOwner(owner)) {
          selectNode(localToReal.get(template.nodes[0]?.localId ?? "") ?? null);
          setWorkflowNotice({ owner: ownerId, kind: "success", text: t("workflowInsertSuccess") });
        }
      } catch (cause) {
        if (isCurrentOwner(owner)) {
          const message = cause instanceof Error ? cause.message : t("workflowInsertError");
          setWorkflowNotice({ owner: ownerId, kind: createdCount > 0 ? "partial" : "error", text: `${createdCount > 0 ? t("workflowInsertPartial") : t("workflowInsertError")}: ${message}` });
        }
        throw cause;
      }
    }, t("workflowInsertError")).finally(() => { workflowInsertRef.current = false; });
  }, [attachPendingReferenceNode, attachReferenceNode, createNode, isCurrentOwner, run, selectNode, t]);

  const dropWorkflowTemplate = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (writeDisabled || !event.dataTransfer.types.includes(WORKFLOW_TEMPLATE_MIME)) return;
    event.preventDefault();
    const templateId = event.dataTransfer.getData(WORKFLOW_TEMPLATE_MIME);
    const template = getBuiltInWorkflowTemplate(templateId);
    if (!template) {
      setLocalError(t("workflowInsertError"));
      return;
    }
    insertWorkflowTemplate(template, canvasPointFromDrop(event, canvasViewportRef.current, pan, zoom));
  }, [insertWorkflowTemplate, pan, t, writeDisabled, zoom]);

  const allowWorkflowDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (event.dataTransfer.types.includes(WORKFLOW_TEMPLATE_MIME) && !writeDisabled) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    }
  }, [writeDisabled]);

  const saveText = useCallback(() => {
    if (!selectedNode || !currentProject || draft.owner !== selectedOwner) return;
    void run(async owner => {
      if (!isCurrentOwner(owner)) throw new Error("Atelier environment or project changed during request");
      if (isBodyNode(selectedNode)) {
        if (draft.title !== selectedNode.title || draft.body !== bodyOf(selectedNode)) {
          await updateNodeText(selectedNode.id, { title: draft.title, prompt: draft.body });
        }
        return;
      }
      if (draft.title !== selectedNode.title || draft.prompt !== selectedNode.prompt) {
        await updateNodeText(selectedNode.id, { title: draft.title, prompt: draft.prompt });
      }
    }, t("saveError"));
  }, [currentProject, draft, isCurrentOwner, run, selectedNode, selectedOwner, t, updateNodeText]);

  const deleteSelected = useCallback(() => {
    if (!selectedNode) return;
    void run(async () => { await deleteNodes([selectedNode.id]); }, t("deleteError"));
  }, [deleteNodes, run, selectedNode, t]);

  const uploadMedia = async (file: File, targetId?: string) => {
    if (!shellOwner || !isCurrentOwner(shellOwner) || uploadDisabled) return;
    if (targetId !== undefined) {
      const target = useAtelierStore.getState().currentProject?.nodes.find(node => node.id === targetId) ?? null;
      if (!target || hasAtelierNodeActiveExecution(target)) return;
    }
    const owner = shellOwner;
    const kind = getAtelierUploadKind(file);
    const layout = nextCanvasPosition(currentProject?.nodes, ATELIER_MEDIA_RENDERED_BOUNDS[kind].width, ATELIER_MEDIA_RENDERED_BOUNDS[kind].height, 100, 130);
    const input = { file, title: file.name, ...layout };
    const ack = targetId === undefined
      ? await createMediaNode({ ...input, nodeType: kind })
      : await uploadReference(targetId, { ...input, sourceNodeType: kind });
    if (targetId === undefined && isCurrentOwner(owner)) selectNode(ack.node_ids[0] ?? null);
  };

  const checkUpload = async (clientRequestId: string) => {
    if (!shellOwner || !isCurrentOwner(shellOwner)) return;
    const owner = shellOwner;
    const key = `${shellOwnerId}:${clientRequestId}`;
    setCheckingUpload(key);
    try {
      const result = await readbackPendingUpload(clientRequestId);
      if (isCurrentOwner(owner)) setUploadNotice(result ? null : { owner: shellOwnerId, text: t("uploadUnconfirmed") });
    } catch (cause) {
      if (isCurrentOwner(owner)) setUploadNotice({ owner: shellOwnerId, text: cause instanceof Error ? cause.message : t("uploadError") });
    } finally {
      setCheckingUpload(current => current === key ? null : current);
    }
  };

  const renderNode = (node: AtelierNode) => {
    const p = preview[node.id] ?? {};
    const x = p.x ?? node.x;
    const y = p.y ?? node.y;
    const width = p.width ?? node.width;
    const height = p.height ?? node.height;
    const selected = node.id === selectedNodeId;
    if (collapsedOf(currentProject?.nodes.find(region => region.id === stringValue(node.data?.region_id)))) return null;
    if (node.type === "idea") {
      return (
        <div key={node.id} onPointerDownCapture={event => beginDrag(node, event)}>
          <IdeaNode id={node.id} body={bodyOf(node)} x={x} y={y} selected={selected} onSelect={selectNode} />
        </div>
      );
    }
    if (node.type === "comment") {
      return (
        <div key={node.id} onPointerDownCapture={event => beginDrag(node, event)}>
          <CommentNode id={node.id} body={bodyOf(node)} author={authorOf(node)} x={x} y={y} selected={selected} onSelect={selectNode} />
        </div>
      );
    }
    if (node.type === "region") {
      return (
        <div key={node.id} onPointerDownCapture={event => beginDrag(node, event)}>
          <RegionFrame
            id={node.id}
            title={node.title}
            x={x}
            y={y}
            width={width}
            height={height}
            selected={selected}
            collapsed={collapsedOf(node)}
            color={regionColorOf(node)}
            childCount={currentProject?.nodes.filter(child => stringValue(child.data?.region_id) === node.id).length ?? 0}
            onSelect={selectNode}
            onToggleCollapse={() => {
              if (writeDisabled) return;
              void run(async () => { await updateRegionAppearance(node.id, { collapsed: !collapsedOf(node) }); }, t("layoutError"));
            }}
            onResizeStart={(_id, corner, event) => beginDrag(node, event, "resize", corner)}
          />
        </div>
      );
    }
    if (node.type === "plan") {
      return (
        <div key={node.id} onPointerDownCapture={event => beginDrag(node, event)}>
          <PlanNode
            id={node.id}
            title={node.title}
            bullets={stringArrayValue(node.data?.bullets)}
            steps={planStepsValue(node.data?.steps)}
            x={x}
            y={y}
            selected={selected}
            onSelect={selectNode}
          />
        </div>
      );
    }
    const media = toMediaNodeView({ ...node, x, y, width, height }, { selectedNodeId });
    if (media) {
      return (
        <div key={node.id} onPointerDownCapture={event => beginDrag(node, event)}>
          <MediaNode {...media} onSelect={selectNode} />
        </div>
      );
    }
    return (
      <div
        key={node.id}
        role="button"
        tabIndex={0}
        onPointerDownCapture={event => beginDrag(node, event)}
        onPointerDown={event => { event.stopPropagation(); selectNode(node.id); }}
        onKeyDown={event => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            selectNode(node.id);
          }
        }}
        style={{ transform: `translate(${x}px, ${y}px)`, width, minHeight: Math.min(height, 180) }}
        className={`atelier-node-shell absolute overflow-hidden p-4 transition-[box-shadow,border-color] ${selected ? "ring-1 ring-white/25 border-white/20" : "border-white/[0.08]"}`}
      >
        <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">{node.type}</div>
        <div className="mt-2 truncate font-display text-[13px] font-medium text-white/80">{node.title || node.id}</div>
        {node.prompt ? <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs leading-5 text-white/50">{node.prompt}</p> : null}
        <div className="mt-3 text-[11px] text-white/35">{node.status}</div>
      </div>
    );
  };

  return (
    <section className="atelier-v3-root h-dvh max-h-dvh overflow-hidden bg-[#050508] text-white" aria-label={t("title")}>
      <div className="flex h-full min-h-0 flex-col">
        <header className="shrink-0 flex items-center justify-between border-b border-white/[0.08] bg-black/30 px-5 py-4 backdrop-blur-xl">
          <div>
            <h1 className="font-display text-xl font-semibold tracking-[-0.03em]">{t("title")}</h1>
            <p className="text-sm text-white/45">{t("subtitle")}</p>
          </div>
          <div className="flex items-center gap-2">
            <a className="rounded-xl border border-white/[0.1] px-3 py-2 text-sm text-white/70 hover:bg-white/[0.06]" href="#/workspace">
              {t("returnWorkspace")}
            </a>
            <button type="button" className={commandButtonClass(isLoading)} disabled={isLoading} onClick={() => { bumpInteractionSession(); void run(() => createProject(t("projectName")), t("createError")); }}>
              {t("createProject")}
            </button>
          </div>
        </header>

        <div className="shrink-0 flex border-b border-white/[0.08] bg-white/[0.025] px-4 py-2">
          <div className="flex flex-wrap gap-2">
            {projects.map(project => (
              <button
                key={project.id}
                type="button"
                className={`rounded-full border px-3 py-1.5 text-sm ${project.id === currentProject?.id ? "border-white/30 bg-white/15 text-white" : "border-white/[0.08] bg-black/20 text-white/55 hover:text-white"}`}
                onClick={() => { bumpInteractionSession(); void run(() => switchProject(project.id), t("switchError")); }}
              >
                {project.title}
              </button>
            ))}
          </div>
        </div>

        {(error || localError || needsRefresh || uploadJournalError?.owner === shellOwnerId || workflowNoticeText) ? (
          <div role="alert" className="shrink-0 flex items-center justify-between border-b border-amber-300/20 bg-amber-500/10 px-5 py-3 text-sm text-amber-100">
            <span>{workflowNoticeText ?? (needsRefresh ? t("needsRefresh") : null)} {error ?? localError ?? (uploadJournalError?.owner === shellOwnerId ? uploadJournalError.text : null)}</span>
            <button type="button" className="rounded-lg border border-amber-100/30 px-3 py-1 text-amber-50" onClick={() => void run(() => refreshProject(), t("switchError"))}>
              {t("refresh")}
            </button>
          </div>
        ) : null}

        {pendingUploads.length > 0 && <div className="max-h-[20dvh] shrink-0 space-y-2 overflow-y-auto border-b border-amber-300/20 bg-amber-500/10 px-5 py-3 text-sm text-amber-100">
          <p>{t("uploadPending")}</p>
          {pendingUploads.map(request => <div key={request.clientRequestId} className="flex items-center justify-between gap-3">
            <span className="truncate">{request.fileName}</span>
            <button type="button" className="rounded-lg border border-amber-100/30 px-3 py-1 disabled:opacity-40"
              disabled={isSaving || checkingUpload === `${shellOwnerId}:${request.clientRequestId}`}
              onClick={() => void checkUpload(request.clientRequestId)}>{t("checkUpload")}</button>
          </div>)}
          {uploadNotice?.owner === shellOwnerId && <p role="status">{uploadNotice.text}</p>}
        </div>}

        <main className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_320px] overflow-hidden">
          <div ref={canvasViewportRef} data-testid="atelier-canvas-viewport" className="relative min-h-0 overflow-hidden" onDragOver={allowWorkflowDrop} onDrop={dropWorkflowTemplate}>
            <div ref={canvasToolbarRef} data-testid="atelier-canvas-toolbar" className="absolute left-5 top-5 z-20 flex flex-wrap gap-2 rounded-2xl border border-white/[0.08] bg-black/35 p-2 backdrop-blur-xl">
              <button type="button" className={commandButtonClass(writeDisabled)} disabled={writeDisabled} onClick={() => createNodeAndSelect("idea")}>{t("createIdea")}</button>
              <button type="button" className={commandButtonClass(writeDisabled)} disabled={writeDisabled} onClick={() => createNodeAndSelect("comment")}>{t("createComment")}</button>
              <button type="button" className={commandButtonClass(writeDisabled)} disabled={writeDisabled} onClick={() => createNodeAndSelect("image")}>{t("createImage")}</button>
              <button type="button" className={commandButtonClass(writeDisabled)} disabled={writeDisabled} onClick={() => createNodeAndSelect("video")}>{t("createVideo")}</button>
              <button type="button" className={commandButtonClass(writeDisabled)} disabled={writeDisabled} onClick={() => createNodeAndSelect("audio")}>{t("createAudio")}</button>
              <button type="button" className={commandButtonClass(writeDisabled)} disabled={writeDisabled} onClick={createRegion}>{t("createRegion")}</button>
              <button type="button" className={commandButtonClass(writeDisabled)} disabled={writeDisabled} onClick={() => setWorkflowsOpen(value => !value)}>{t("workflows")}</button>
              <MediaUploadButton ownerKey={shellOwnerId} label={t("uploadMedia")} busyLabel={t("uploading")} errorLabel={t("uploadError")}
                accept={ATELIER_UPLOAD_ACCEPT} disabled={uploadDisabled} onUpload={file => uploadMedia(file)} />
              <button type="button" className={commandButtonClass(false)} onClick={() => setZoom(z => Math.min(2, z + 0.1))}>{t("zoomIn")}</button>
              <button type="button" className={commandButtonClass(false)} onClick={() => setZoom(z => Math.max(0.4, z - 0.1))}>{t("zoomOut")}</button>
            </div>
            {workflowsOpen ? <div className="absolute left-5 top-24 z-30">
              <WorkflowsPanel
                labels={{ title: t("workflowsTitle"), all: t("workflowsAll"), insert: t("workflowInsert"), drag: t("workflowDrag") }}
                disabled={writeDisabled}
                onInsert={template => insertWorkflowTemplate(template)}
              />
            </div> : null}
            {isLoading ? <p className="absolute left-6 top-24 z-20 text-sm text-white/45">{t("loading")}</p> : null}
            {currentProject && currentProject.nodes.length === 0 && !isLoading ? (
              <p className="absolute left-6 top-24 z-20 text-sm text-white/45">{t("emptyCanvas")}</p>
            ) : null}
            <div
              className="relative h-full w-full origin-top-left"
              style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
              aria-label="Atelier canvas"
              onPointerDown={event => {
                if (event.currentTarget !== event.target) return;
                selectNode(null);
                panRef.current = { startClientX: event.clientX, startClientY: event.clientY, startX: pan.x, startY: pan.y };
              }}
            >
              <svg aria-label={t("templateRelationships")} data-testid="atelier-reference-edges" className="pointer-events-none absolute left-0 top-0 h-[6000px] w-[6000px] overflow-visible">
                {canvasLinks.map(link => {
                  const x1 = link.from.x + link.from.width / 2;
                  const y1 = link.from.y + link.from.height / 2;
                  const x2 = link.to.x + link.to.width / 2;
                  const y2 = link.to.y + link.to.height / 2;
                  return <line key={link.key} data-testid={link.pending ? "atelier-pending-reference-edge" : "atelier-reference-edge"}
                    x1={x1} y1={y1} x2={x2} y2={y2}
                    stroke={link.pending ? "rgba(255,255,255,0.34)" : link.stale ? "rgba(251,191,36,0.42)" : "rgba(100,108,255,0.42)"}
                    strokeWidth="2" strokeDasharray={link.pending ? "8 7" : link.stale ? "4 5" : undefined} />;
                })}
              </svg>
              <div aria-hidden="true" className="atelier-bloom pointer-events-none absolute left-[22%] top-[10%]" />
              {/* Regions are backgrounds, including when created after their members. */}
              {currentProject?.nodes.filter(node => node.type === "region").map(renderNode)}
              {currentProject?.nodes.filter(node => node.type !== "region").map(renderNode)}
            </div>
          </div>

          <aside ref={inspectorRef} role="region" aria-label={t("inspectorTitle")} className="min-h-0 overflow-y-auto overscroll-contain border-l border-white/[0.08] bg-black/35 p-4 backdrop-blur-xl">
            <h2 className="font-display text-lg font-medium">{t("inspectorTitle")}</h2>
            {selectedNode ? (
              <div className="mt-4 space-y-3">
                <label className="block text-xs text-white/45">
                  {t("titleLabel")}
                  <input className={`${fieldClass()} mt-1`} aria-label={t("titleLabel")} value={draft.title} onChange={event => setDraft(prev => ({ ...prev, title: event.target.value }))} />
                </label>
                {!isBodyNode(selectedNode) ? (
                  <label className="block text-xs text-white/45">
                    {t("promptLabel")}
                    <textarea className={`${fieldClass()} mt-1 min-h-[72px]`} aria-label={t("promptLabel")} value={draft.prompt} onChange={event => setDraft(prev => ({ ...prev, prompt: event.target.value }))} />
                  </label>
                ) : null}
                {isBodyNode(selectedNode) ? (
                  <label className="block text-xs text-white/45">
                    {t("bodyLabel")}
                    <textarea className={`${fieldClass()} mt-1 min-h-[120px]`} aria-label={t("bodyLabel")} value={draft.body} onChange={event => setDraft(prev => ({ ...prev, body: event.target.value }))} />
                  </label>
                ) : null}
                <div className="flex gap-2">
                  <button type="button" className={commandButtonClass(writeDisabled)} disabled={writeDisabled} onClick={saveText}>{isSaving ? t("commandBusy") : t("saveText")}</button>
                  <button type="button" className={commandButtonClass(writeDisabled)} disabled={writeDisabled} onClick={deleteSelected}>{t("deleteNode")}</button>
                </div>
                {currentProject && selectedNode.type !== "region" ? <RegionMembershipControls
                  key={`${shellOwnerId}:${selectedNode.id}:membership`}
                  node={selectedNode}
                  nodes={currentProject.nodes}
                  disabled={writeDisabled}
                  onSave={regionId => updateNodeLayout(selectedNode.id, { regionId })}
                /> : null}
                {currentProject ? <ReferenceControls
                  project={currentProject}
                  targetId={selectedNode.id}
                  ownerKey={shellOwnerId}
                  disabled={writeDisabled}
                  labels={{ title: t("referencesTitle"), select: t("chooseReferenceSource"), empty: t("noReferenceSource"), attach: t("attachReference"), attachPending: t("attachPendingReference"), pending: t("pendingReference"), pendingReady: t("pendingReferenceReady"), confirmPending: t("confirmPendingReference"), detach: t("detachReference") }}
                  onAttach={input => void run(() => attachReferenceNode(selectedNode.id, input), t("referenceError"))}
                  onAttachPending={input => void run(() => attachPendingReferenceNode(selectedNode.id, input), t("referenceError"))}
                  onDetach={input => void run(() => detachReferenceNode(selectedNode.id, input), t("referenceError"))}
                /> : null}
                {(selectedNode.type === "image" || selectedNode.type === "video") && <MediaUploadButton
                  ownerKey={`${shellOwnerId}:${selectedNode.id}`} label={t("uploadReference")} busyLabel={t("uploading")} errorLabel={t("uploadError")}
                  accept={selectedNode.type === "image" ? ATELIER_IMAGE_UPLOAD_ACCEPT : ATELIER_UPLOAD_ACCEPT}
                  disabled={uploadDisabled || selectedNodeUploadBlocked} onUpload={file => uploadMedia(file, selectedNode.id)} />}
              </div>
            ) : (
              <p className="mt-4 text-sm text-white/45">{t("noSelection")}</p>
            )}
            {currentProject ? <div className="mt-4">
              <ProjectPackageControls
                project={currentProject}
                ownerKey={`${shellOwnerId}:package`}
                environmentKey={runtimeEnvironment?.key ?? null}
                disabled={writeDisabled}
                loadProjects={loadProjects}
                switchProject={switchProject}
              />
            </div> : null}
            {currentProject ? <div className="mt-4">
              <AgentTurnPanel
                key={`${shellOwnerId}:agent:${selectedNode?.id ?? "none"}`}
                project={currentProject}
                selectedNode={selectedNode}
                ownerKey={`${shellOwnerId}:agent:${selectedNode?.id ?? "none"}`}
                disabled={writeDisabled}
              />
            </div> : null}
            {currentProject ? <div className="mt-4">
              <GenerationPanel
                key={`${shellOwnerId}:generation:${selectedNode?.id ?? "none"}`}
                project={currentProject}
                selectedNode={selectedNode}
                ownerKey={`${shellOwnerId}:${selectedNode?.id ?? "none"}`}
                disabled={writeDisabled}
                targetRect={selectedComposerLayout.rect}
                targetAvailableRect={selectedComposerLayout.availableRect}
              />
            </div> : null}
            {currentProject ? <div className="mt-4">
              <MediaComposer
                key={`${shellOwnerId}:media:${selectedNode?.id ?? "none"}`}
                project={currentProject}
                selectedNode={selectedNode}
                ownerKey={`${shellOwnerId}:${selectedNode?.id ?? "none"}`}
                disabled={writeDisabled}
                pendingGenerations={pendingGenerations}
                targetRect={selectedComposerLayout.rect}
                targetAvailableRect={selectedComposerLayout.availableRect}
                saveDraft={saveMediaGenerationDraft}
                createImageCandidate={createImageCandidate}
                createAudioCandidate={createAudioCandidate}
                readbackPendingGeneration={readbackPendingGeneration}
                refreshGenerationAttempt={refreshGenerationAttempt}
                continueGenerationAttempt={continueGenerationAttempt}
                cancelGenerationAttempt={cancelGenerationAttempt}
                selectCandidate={selectMediaGenerationCandidate}
                setCandidateVisibility={setMediaGenerationCandidateVisibility}
              />
            </div> : null}
            {unconsumedReceipts.length ? (
              <div className="mt-6 space-y-2">
                {unconsumedReceipts.map(receipt => (
                  <div key={receipt.receipt_id} className="rounded-xl border border-white/[0.08] bg-white/[0.04] p-3">
                    <p className="text-sm text-white/65">{t("deletedReceipt")}</p>
                    <button type="button" className={`${commandButtonClass(writeDisabled)} mt-2`} disabled={writeDisabled} onClick={() => void run(() => restoreNodes(receipt.receipt_id), t("restoreError"))}>
                      {t("restoreReceipt")}
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
          </aside>
        </main>
        {currentProject && runtimeEnvironment ? <div className="max-h-[35dvh] shrink-0 overflow-y-auto overscroll-contain">
          <SequenceWorkbench key={shellOwnerId} project={currentProject} selectedNode={selectedNode}
            owner={{ apiBase: runtimeEnvironment.apiUrl, storageId: runtimeEnvironment.storageId, projectId: currentProject.id }} />
        </div> : null}
      </div>
    </section>
  );
}

export default AtelierShell;
