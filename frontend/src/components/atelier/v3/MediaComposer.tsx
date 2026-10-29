"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, Music2, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { BranchSourceCard } from "./BranchSourceCard";
import { CandidateJudgmentDialog, latestCandidateJudgment } from "./CandidateJudgmentDialog";
import {
  pinnedComposerPlacement,
  type PinnedComposerTargetRect,
} from "./PinnedComposer";
import {
  atelierMediaApi,
  parseAtelierMediaDraft,
  type AtelierBuiltinVoice,
  type AtelierMediaDraftInput,
} from "@/lib/atelierMediaApi";
import { AtelierProjectApiError } from "@/lib/atelierApi";
import { atelierImageDraftIssues, correctAtelierImageDraft, isAtelierQwen20T2I, isAtelierStrictImageT2I, isAtelierWan27T2I, validAtelierQwen20Prompt } from "@/lib/atelierWan27ImageDraft";
import { getAssetUrl } from "@/lib/utils";
import { getRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { useAtelierStore } from "@/store/atelierStore";
import {
  DEFAULT_MODEL_SETTINGS,
  PROJECT_I2I_MODELS,
  PROJECT_T2I_MODELS,
  getCanonicalDefaults,
  getCanonicalModeEntry,
  getCanonicalModeId,
} from "@/lib/modelCatalog";
import type {
  AtelierAudioCandidateRequest,
  AtelierCandidateJudgment,
  AtelierGenerationAttempt,
  AtelierImageCandidateRequest,
  AtelierImageGenerationParams,
  AtelierImageReferenceSelection,
  AtelierMediaGenerationCandidateProjection,
  AtelierNode,
  AtelierProject,
  AtelierTTSGenerationParams,
  JsonRecord,
  JsonValue,
} from "@/lib/atelierTypes";
import type { StoredAtelierGenerationRequest } from "@/lib/atelierGenerationRequest";

type ImageCreateInput = Omit<
  AtelierImageCandidateRequest,
  "expected_revision" | "client_request_id"
>;
type AudioCreateInput = Omit<
  AtelierAudioCandidateRequest,
  "expected_revision" | "client_request_id"
>;
type MediaDraftInput = AtelierMediaDraftInput extends infer D
  ? D extends AtelierMediaDraftInput
    ? Omit<D, "expected_revision">
    : never
  : never;

type Props = {
  project: AtelierProject;
  selectedNode: AtelierNode | null;
  ownerKey: string;
  disabled: boolean;
  pendingGenerations: StoredAtelierGenerationRequest[];
  targetRect?: PinnedComposerTargetRect | null;
  targetAvailableRect?: PinnedComposerTargetRect | null;
  saveDraft?: (nodeId: string, input: MediaDraftInput) => Promise<unknown>;
  createImageCandidate: (
    nodeId: string,
    input: ImageCreateInput,
  ) => Promise<unknown>;
  createAudioCandidate: (
    nodeId: string,
    input: AudioCreateInput,
  ) => Promise<unknown>;
  readbackPendingGeneration: (clientRequestId?: string) => Promise<unknown>;
  refreshGenerationAttempt: (attemptId: string) => Promise<unknown>;
  continueGenerationAttempt: (attemptId: string) => Promise<unknown>;
  cancelGenerationAttempt: (attemptId: string) => Promise<unknown>;
  selectCandidate?: (nodeId: string, candidateId: string) => Promise<unknown>;
  setCandidateVisibility?: (
    nodeId: string,
    candidateId: string,
    visible: boolean,
  ) => Promise<unknown>;
};

type RecoverableLocalError =
  | {
      kind: "attempt-conflict";
      ownerKey: string;
      attemptId: string;
      attemptRevision: number;
    }
  | { kind: "refresh-required"; ownerKey: string; projectRevision: number };

type DraftState = {
  owner: string;
  prompt: string;
  modelId: string;
  mode: "t2i" | "i2i";
  media: AtelierImageReferenceSelection[];
  batchSize: number;
  imageParams: AtelierImageGenerationParams;
  voice: string;
  audioParams: AtelierTTSGenerationParams;
};

const buttonClass =
  "rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs text-white/75 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-35";
const inputClass =
  "rounded-xl border border-white/[0.1] bg-black/35 px-3 py-2 text-sm text-white/80 outline-none focus:border-white/25";
const CANONICAL_IMAGE_DEFAULTS = getCanonicalDefaults();
const DEFAULT_T2I_MODEL =
  CANONICAL_IMAGE_DEFAULTS.t2i_model ??
  getCanonicalModeId(DEFAULT_MODEL_SETTINGS.t2i_model) ??
  DEFAULT_MODEL_SETTINGS.t2i_model;
const DEFAULT_I2I_MODEL =
  CANONICAL_IMAGE_DEFAULTS.i2i_model ??
  getCanonicalModeId(DEFAULT_MODEL_SETTINGS.i2i_model) ??
  DEFAULT_MODEL_SETTINGS.i2i_model;
const IMAGE_MODEL_OPTIONS = [...PROJECT_T2I_MODELS, ...PROJECT_I2I_MODELS]
  .map((item) => ({ ...item, id: getCanonicalModeId(item.id) ?? item.id }))
  .filter(
    (item, index, all) =>
      all.findIndex((other) => other.id === item.id) === index,
  )
  .filter((item) => !!getCanonicalModeEntry(item.id));
const POLL_MS = 2000;

function isRecord(value: JsonValue | undefined): value is JsonRecord {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function numberValue(value: JsonValue | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stringValue(value: JsonValue | undefined, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function boolValue(value: JsonValue | undefined, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function candidatesFor(node: AtelierNode): Array<{
  candidate: AtelierMediaGenerationCandidateProjection;
  deleted: boolean;
}> {
  const collect = (
    field: "candidates" | "deleted_candidates",
    deleted: boolean,
  ) => {
    const values = node.data[field];
    if (!Array.isArray(values)) return [];
    return values.flatMap((item) => {
      if (
        !isRecord(item) ||
        item.execution_contract !== "atelier-provider-media-v2" ||
        item.media_kind !== node.type
      )
        return [];
      return [
        {
          candidate:
            item as unknown as AtelierMediaGenerationCandidateProjection,
          deleted,
        },
      ];
    });
  };
  return [
    ...collect("candidates", false),
    ...collect("deleted_candidates", true),
  ];
}

function attemptsFor(
  project: AtelierProject,
  node: AtelierNode,
): AtelierGenerationAttempt[] {
  return project.generation_attempts.filter(
    (attempt) =>
      attempt.node_id === node.id &&
      "execution_contract" in attempt &&
      attempt.execution_contract === "atelier-provider-media-v2" &&
      attempt.media_kind === node.type,
  );
}

function stringArray(value: JsonValue | undefined): string[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is string => typeof item === "string" && item.length > 0,
      )
    : [];
}

function completedCandidateUrls(node: AtelierNode): string[] {
  const collect = (field: "candidates" | "deleted_candidates") => {
    const values = node.data[field];
    return Array.isArray(values)
      ? values.flatMap((item) =>
          isRecord(item) &&
          item.status === "completed" &&
          typeof item.media_url === "string"
            ? [item.media_url]
            : [],
        )
      : [];
  };
  return [
    ...node.media_urls,
    ...collect("candidates"),
    ...collect("deleted_candidates"),
  ];
}

function referenceSelections(
  project: AtelierProject,
  node: AtelierNode,
): {
  media: AtelierImageReferenceSelection[];
  missing: boolean;
  hasReferences: boolean;
} {
  if (node.type !== "image")
    return { media: [], missing: false, hasReferences: false };
  const ids = stringArray(node.data.reference_node_ids);
  const urls = stringArray(node.data.reference_image_urls);
  if (!ids.length) return { media: [], missing: false, hasReferences: false };
  const media: AtelierImageReferenceSelection[] = [];
  for (const id of ids) {
    const source = project.nodes.find((item) => item.id === id);
    if (!source || source.type !== "image")
      return { media: [], missing: true, hasReferences: true };
    const sourceUrls = new Set(completedCandidateUrls(source));
    const exactUrls = urls.filter((url) => sourceUrls.has(url));
    if (!exactUrls.length)
      return { media: [], missing: true, hasReferences: true };
    for (const mediaUrl of exactUrls) {
      media.push({
        role: "reference_image",
        ordinal: media.length,
        node_id: id,
        media_url: mediaUrl,
      });
    }
  }
  return { media, missing: false, hasReferences: true };
}

function imageParamsSpec(modelId: string): JsonRecord {
  const entry =
    getCanonicalModeEntry(modelId) ??
    getCanonicalModeEntry(getCanonicalModeId(modelId) ?? "");
  return isRecord(entry?.params as JsonValue | undefined)
    ? (entry.params as JsonRecord)
    : {};
}

function imageParamEnabled(
  spec: JsonRecord,
  key: "seed" | "negativePrompt" | "promptExtend" | "watermark" | "size",
): boolean {
  const value = spec[key];
  return key === "size"
    ? isRecord(value) || value === true
    : value === true || isRecord(value);
}

function defaultImageParams(modelId: string, mode: "t2i" | "i2i" = "t2i"): AtelierImageGenerationParams {
  if (mode === "t2i" && isAtelierStrictImageT2I(modelId, [])) return {};
  const spec = imageParamsSpec(modelId);
  const params: AtelierImageGenerationParams = {};
  const sizeSpec = spec.size;
  const sizeRecord = isRecord(sizeSpec as JsonValue | undefined)
    ? (sizeSpec as JsonRecord)
    : null;
  if (
    imageParamEnabled(spec, "size") &&
    sizeRecord &&
    typeof sizeRecord.default === "string"
  )
    params.size = sizeRecord.default;
  if (imageParamEnabled(spec, "promptExtend")) params.prompt_extend = true;
  if (imageParamEnabled(spec, "watermark")) params.watermark = false;
  return params;
}

function sanitizeImageParams(
  params: AtelierImageGenerationParams,
  modelId: string,
): AtelierImageGenerationParams {
  const spec = imageParamsSpec(modelId);
  const next: AtelierImageGenerationParams = {};
  if (
    imageParamEnabled(spec, "size") &&
    typeof params.size === "string" &&
    params.size
  )
    next.size = params.size;
  if (
    imageParamEnabled(spec, "seed") &&
    typeof params.seed === "number" &&
    Number.isSafeInteger(params.seed)
  )
    next.seed = params.seed;
  if (
    imageParamEnabled(spec, "negativePrompt") &&
    typeof params.negative_prompt === "string" &&
    params.negative_prompt
  )
    next.negative_prompt = params.negative_prompt;
  if (
    imageParamEnabled(spec, "promptExtend") &&
    typeof params.prompt_extend === "boolean"
  )
    next.prompt_extend = params.prompt_extend;
  if (
    imageParamEnabled(spec, "watermark") &&
    typeof params.watermark === "boolean"
  )
    next.watermark = params.watermark;
  return next;
}

function sanitizeAudioParams(
  params: AtelierTTSGenerationParams,
  voice: AtelierBuiltinVoice | null,
): AtelierTTSGenerationParams {
  if (!voice) return { ...params };
  const supports = new Set(voice.supported_parameters);
  const next: AtelierTTSGenerationParams = {};
  if (supports.has("speech_rate") && typeof params.speech_rate === "number")
    next.speech_rate = params.speech_rate;
  if (supports.has("pitch_rate") && typeof params.pitch_rate === "number")
    next.pitch_rate = params.pitch_rate;
  if (supports.has("volume") && typeof params.volume === "number")
    next.volume = params.volume;
  if (
    supports.has("instructions") &&
    typeof params.instructions === "string" &&
    params.instructions
  )
    next.instructions = params.instructions;
  return next;
}

function imageMediaHasMissing(
  project: AtelierProject,
  node: AtelierNode,
  media: AtelierImageReferenceSelection[],
): boolean {
  if (node.type !== "image") return false;
  const refs = referenceSelections(project, node);
  if (!refs.hasReferences) return media.length > 0;
  if (refs.missing || media.length === 0) return true;
  return media.some(
    (item) =>
      !refs.media.some(
        (ref) =>
          ref.node_id === item.node_id &&
          ref.media_url === item.media_url &&
          ref.ordinal === item.ordinal,
      ),
  );
}

const DEFAULT_AUDIO_VOICE = "longxiaochun_v2";

type DraftLoad = { state: DraftState; unsupported: boolean };

function draftFromNode(
  project: AtelierProject,
  node: AtelierNode,
  owner: string,
): DraftLoad {
  const refs = referenceSelections(project, node);
  const mode = refs.hasReferences ? "i2i" : "t2i";
  const imageDefaultModel =
    mode === "i2i" ? DEFAULT_I2I_MODEL : DEFAULT_T2I_MODEL;
  const base: DraftState = {
    owner,
    prompt: node.prompt,
    modelId: imageDefaultModel,
    mode,
    media: refs.media,
    batchSize: 1,
    imageParams: defaultImageParams(imageDefaultModel, mode),
    voice: DEFAULT_AUDIO_VOICE,
    audioParams: { speech_rate: 1, pitch_rate: 1, volume: 50 },
  };
  if (!Object.prototype.hasOwnProperty.call(node.data, "generation_draft"))
    return { state: base, unsupported: false };
  try {
    const draft = parseAtelierMediaDraft(node.data.generation_draft);
    if (draft.media_kind === "image" && node.type === "image") {
      const modelId = getCanonicalModeId(draft.model_id) ?? draft.model_id;
      if (!getCanonicalModeEntry(modelId))
        return { state: base, unsupported: true };
      return {
        state: {
          ...base,
          modelId,
          mode: draft.mode,
          media: draft.media,
          batchSize: draft.batch_size,
          imageParams: isAtelierWan27T2I(modelId, draft.media) || (draft.mode === "t2i" && isAtelierQwen20T2I(modelId, draft.media))
            ? { ...draft.params }
            : {
                ...defaultImageParams(modelId, draft.mode),
                ...sanitizeImageParams(draft.params, modelId),
              },
        },
        unsupported: false,
      };
    }
    if (draft.media_kind === "audio" && node.type === "audio") {
      return {
        state: {
          ...base,
          voice: draft.voice,
          audioParams: { ...draft.params },
        },
        unsupported: false,
      };
    }
    return { state: base, unsupported: true };
  } catch {
    return { state: base, unsupported: true };
  }
}

/** Build a proposal only. Parsing the saved record remains strict and unchanged. */
function qwenDraftCorrection(project: AtelierProject, node: AtelierNode, owner: string): DraftState | null {
  const raw = node.data.generation_draft;
  if (node.type !== "image" || !isRecord(raw) || raw.media_kind !== "image"
      || raw.mode !== "t2i" || typeof raw.model_id !== "string"
      || !Array.isArray(raw.media) || raw.media.length !== 0
      || !isAtelierQwen20T2I(raw.model_id, raw.media)
      || referenceSelections(project, node).hasReferences) return null;
  const base = draftFromNode(project, { ...node, data: {} }, owner).state;
  const modelId = getCanonicalModeId(raw.model_id) ?? raw.model_id;
  return {
    ...base, modelId, mode: "t2i", media: [],
    batchSize: typeof raw.batch_size === "number" && Number.isInteger(raw.batch_size) && raw.batch_size >= 1 && raw.batch_size <= 6 ? raw.batch_size : 1,
    imageParams: correctAtelierImageDraft(modelId, [], isRecord(raw.params) ? raw.params : {}),
  };
}

function pendingForNode(
  pending: StoredAtelierGenerationRequest[],
  node: AtelierNode | null,
): StoredAtelierGenerationRequest | null {
  if (!node) return null;
  return (
    pending.find(
      (item) =>
        item.nodeId === node.id &&
        "entrypoint" in item.request &&
        (node.type === "image"
          ? item.request.entrypoint === "atelier.provider_image.v1"
          : item.request.entrypoint === "atelier.provider_audio.v1"),
    ) ?? null
  );
}

function isTerminal(attempt: AtelierGenerationAttempt): boolean {
  return (
    attempt.status === "completed" ||
    attempt.status === "failed" ||
    attempt.status === "cancelled"
  );
}

function canContinueAttempt(attempt: AtelierGenerationAttempt): boolean {
  return (
    !isTerminal(attempt) &&
    attempt.status !== "needs_confirmation" &&
    ["accepted", "submitted", "observed"].includes(
      attempt.provider_execution.phase,
    )
  );
}

function attemptLabelKey(attempt: AtelierGenerationAttempt): string {
  if (attempt.status === "completed") return "statusCompleted";
  if (attempt.status === "failed") return "statusFailed";
  if (attempt.status === "cancelled") return "statusCancelled";
  if (attempt.status === "needs_confirmation") return "statusNeedsConfirmation";
  if (attempt.provider_execution.phase === "accepted") return "phaseAccepted";
  if (
    attempt.provider_execution.phase === "submitted" ||
    attempt.provider_execution.phase === "observed"
  )
    return "phaseSubmitted";
  return "statusPending";
}

function isStorageConflict(cause: unknown): boolean {
  return (
    cause instanceof AtelierProjectApiError && cause.code === "storage_conflict"
  );
}

function isLostAckTransportError(cause: unknown): boolean {
  if (cause instanceof AtelierProjectApiError) return false;
  return cause instanceof TypeError || (cause instanceof Error && cause.message === "Failed to fetch");
}

type StoreCreateIdentity = {
  projectId: string;
  nodeId: string;
  runtimeKey: string;
  runtimeGeneration: number;
};

function currentStoreAllowsCreate(
  identity: StoreCreateIdentity | null,
): boolean {
  if (!identity) return true;
  const runtime = getRuntimeEnvironment();
  const state = useAtelierStore.getState();
  return (
    !!runtime &&
    runtime.key === identity.runtimeKey &&
    runtime.generation === identity.runtimeGeneration &&
    state.currentProject?.id === identity.projectId &&
    state.selectedNodeId === identity.nodeId &&
    !state.isLoading &&
    !state.isSaving &&
    !state.needsRefresh &&
    !state.pendingGenerations?.some(() => true)
  );
}

function useViewportMetrics(contentRef: React.RefObject<HTMLElement>) {
  const [metrics, setMetrics] = useState({
    width: 360,
    height: 240,
    contentHeight: 240,
  });
  useEffect(() => {
    const measure = () =>
      setMetrics({
        width: typeof window === "undefined" ? 1440 : window.innerWidth,
        height: typeof window === "undefined" ? 900 : window.innerHeight,
        contentHeight: contentRef.current?.scrollHeight ?? 240,
      });
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(measure);
    if (contentRef.current) observer?.observe(contentRef.current);
    measure();
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [contentRef]);
  return metrics;
}

export function MediaComposer({
  project,
  selectedNode,
  ownerKey,
  disabled,
  pendingGenerations,
  targetRect,
  targetAvailableRect,
  saveDraft,
  createImageCandidate,
  createAudioCandidate,
  readbackPendingGeneration,
  refreshGenerationAttempt,
  continueGenerationAttempt,
  cancelGenerationAttempt,
  selectCandidate,
  setCandidateVisibility,
}: Props) {
  const t = useTranslations("atelier.media");
  const setCandidateJudgment = useAtelierStore(state => state.setCandidateJudgment);
  const branchFromCandidate = useAtelierStore(state => state.branchFromCandidate);
  const node =
    selectedNode &&
    (selectedNode.type === "image" || selectedNode.type === "audio")
      ? selectedNode
      : null;
  const draftOwner = `${ownerKey}:media:${node?.id ?? "none"}`;
  const initialDraft = () =>
    node
      ? draftFromNode(project, node, draftOwner)
      : draftFromNode(
          project,
          {
            ...(project.nodes[0] ?? {}),
            type: "image",
            prompt: "",
            data: {},
            media_urls: [],
          } as AtelierNode,
          draftOwner,
        );
  const [draft, setDraft] = useState<DraftState>(() => initialDraft().state);
  const [draftUnsupported, setDraftUnsupported] = useState(
    () => initialDraft().unsupported,
  );
  const [voices, setVoices] = useState<AtelierBuiltinVoice[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recoverableError, setRecoverableError] =
    useState<RecoverableLocalError | null>(null);
  const [reviewingCandidateId, setReviewingCandidateId] = useState<string | null>(null);
  const composing = useRef(false);
  const actionLockRef = useRef<string | null>(null);
  const editVersionRef = useRef(0);
  const latestRef = useRef({
    ownerKey,
    projectId: project.id,
    nodeId: node?.id ?? null,
    epoch: 0,
  });
  const aliveRef = useRef(true);
  const refreshSeenRef = useRef({
    ownerKey,
    needsRefresh: useAtelierStore.getState().needsRefresh,
    revision: project.revision,
  });
  const contentRef = useRef<HTMLDivElement>(null);
  const metrics = useViewportMetrics(contentRef);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    const previous = latestRef.current;
    if (previous.ownerKey === ownerKey && previous.projectId === project.id && previous.nodeId === (node?.id ?? null)) return;
    latestRef.current = {
      ownerKey,
      projectId: project.id,
      nodeId: node?.id ?? null,
      epoch: previous.epoch + 1,
    };
    actionLockRef.current = null;
    setBusy(null);
    setError(null);
    setRecoverableError(null);
    setReviewingCandidateId(null);
  }, [node?.id, ownerKey, project.id]);

  useEffect(() => {
    if (!node || draft.owner === draftOwner) return;
    const loaded = draftFromNode(project, node, draftOwner);
    setDraft(loaded.state);
    setDraftUnsupported(loaded.unsupported);
    setError(null);
    setRecoverableError(null);
    editVersionRef.current += 1;
  }, [draft.owner, draftOwner, node, project]);

  const audioNodeId = node?.type === "audio" ? node.id : null;

  useEffect(() => {
    if (!audioNodeId) return;
    let active = true;
    const voiceOwner = latestRef.current.epoch;
    atelierMediaApi
      .listVoices()
      .then((value) => {
        if (!active || latestRef.current.epoch !== voiceOwner) return;
        setVoices(value);
        setDraft((current) => ({
          ...current,
          audioParams: sanitizeAudioParams(
            current.audioParams,
            value.find((voice) => voice.id === current.voice) ?? null,
          ),
        }));
      })
      .catch((cause) => {
        if (active && latestRef.current.epoch === voiceOwner) {
          setError(cause instanceof Error ? cause.message : String(cause));
          setRecoverableError(null);
        }
      });
    return () => {
      active = false;
    };
  }, [audioNodeId, ownerKey, project.id]);

  const reference = useMemo(
    () =>
      node
        ? referenceSelections(project, node)
        : { media: [], missing: false, hasReferences: false },
    [node, project],
  );
  const pending = pendingForNode(pendingGenerations, node);
  const projectPending = pendingGenerations[0] ?? null;
  const blockingPending = pending ?? projectPending;
  const attempts = useMemo(
    () => (node ? attemptsFor(project, node) : []),
    [node, project],
  );
  const pollIds = attempts
    .filter((attempt) => !isTerminal(attempt))
    .map((attempt) => attempt.attempt_id)
    .join(",");
  const candidates = node ? candidatesFor(node) : [];
  const candidateJudgments = project.candidate_judgments ?? [];
  const reviewingHistory: AtelierCandidateJudgment[] = node && reviewingCandidateId
    ? candidateJudgments.filter(item => item.node_id === node.id && item.candidate_id === reviewingCandidateId)
    : [];
  const currentVoice = voices.find((item) => item.id === draft.voice) ?? null;
  const supports = new Set(currentVoice?.supported_parameters ?? []);
  const unsupportedVoice = node?.type === "audio" && voices.length > 0 && currentVoice === null;
  const strictImage = node?.type === "image" && (isAtelierWan27T2I(draft.modelId, draft.media) || (draft.mode === "t2i" && isAtelierQwen20T2I(draft.modelId, draft.media)));
  const invalidQwenPrompt = strictImage && isAtelierQwen20T2I(draft.modelId, draft.media) && !validAtelierQwen20Prompt(draft.prompt);
  const wanImage = strictImage && isAtelierWan27T2I(draft.modelId, draft.media);
  const imageDraftIssues = strictImage
    ? atelierImageDraftIssues(draft.modelId, draft.media, draft.imageParams)
    : [];
  const currentImageParams = strictImage
    ? draft.imageParams
    : sanitizeImageParams(draft.imageParams, draft.modelId);
  const sizeSpec = imageParamsSpec(draft.modelId).size;
  const defaultSize = isRecord(sizeSpec) ? stringValue(sizeSpec.default) : "";
  const missingDraftMedia = node
    ? imageMediaHasMissing(project, node, draft.media)
    : false;
  const incompleteI2i = node?.type === "image" && draft.mode === "i2i" && draft.media.length === 0;
  const needsRefresh = useAtelierStore((state) => state.needsRefresh);

  useEffect(() => {
    if (recoverableError?.kind === "attempt-conflict" && recoverableError.ownerKey === ownerKey) {
      const current = attempts.find(
        (item) => item.attempt_id === recoverableError.attemptId,
      );
      if (current && current.attempt_revision > recoverableError.attemptRevision) {
        setError(null);
        setRecoverableError(null);
      }
    }
    if (
      recoverableError?.kind === "refresh-required" &&
      recoverableError.ownerKey === ownerKey &&
      !needsRefresh &&
      project.revision > recoverableError.projectRevision
    ) {
      setError(null);
      setRecoverableError(null);
    }
    refreshSeenRef.current = {
      ownerKey,
      needsRefresh,
      revision: project.revision,
    };
  }, [attempts, needsRefresh, ownerKey, project.revision, recoverableError]);

  const referenceSignature = JSON.stringify(reference.media);

  useEffect(() => {
    if (!node || node.type !== "image" || draft.owner !== draftOwner) return;
    if (reference.missing) return;
    setDraft((current) => {
      if (current.owner !== draftOwner) return current;
      if (!reference.hasReferences)
        return current.media.length ? current : current;
      if (current.media.length > 0) return current;
      const modelId =
        current.mode === "t2i" ? DEFAULT_I2I_MODEL : current.modelId;
      const fromStrictT2I = current.mode === "t2i" && isAtelierWan27T2I(current.modelId, current.media);
      return {
        ...current,
        mode: "i2i",
        modelId,
        media: reference.media,
        imageParams: modelId === current.modelId && !fromStrictT2I
          ? current.imageParams
          : {
              ...defaultImageParams(modelId, "i2i"),
              ...(fromStrictT2I ? current.imageParams : sanitizeImageParams(current.imageParams, modelId)),
            },
      };
    });
  }, [
    draft.owner,
    draftOwner,
    node,
    reference.hasReferences,
    reference.media,
    reference.missing,
    referenceSignature,
  ]);

  useEffect(() => {
    if (!pollIds) return;
    let stopped = false;
    let running = false;
    let timer: ReturnType<typeof setTimeout>;
    const ids = pollIds.split(",");
    const pollOwner = latestRef.current.epoch;
    const poll = async () => {
      if (running) return;
      running = true;
      try {
        for (const id of ids) {
          if (stopped) return;
          await refreshGenerationAttempt(id);
        }
      } catch (cause) {
        if (
          !stopped &&
          aliveRef.current &&
          latestRef.current.epoch === pollOwner
        ) {
          setError(cause instanceof Error ? cause.message : String(cause));
          setRecoverableError(null);
        }
      } finally {
        running = false;
        if (!stopped) timer = setTimeout(() => void poll(), POLL_MS);
      }
    };
    timer = setTimeout(() => void poll(), 1000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [pollIds, refreshGenerationAttempt, ownerKey, project.id, node?.id]);

  const markEdited = useCallback(() => {
    editVersionRef.current += 1;
  }, []);

  if (!node) return null;
  const correctedDraft = draftUnsupported ? qwenDraftCorrection(project, node, draftOwner) : null;
  const canPersist =
    !!saveDraft &&
    !draftUnsupported &&
    !unsupportedVoice &&
    !reference.missing &&
    !missingDraftMedia;
  const canGenerate =
    canPersist &&
    !incompleteI2i &&
    imageDraftIssues.length === 0 &&
    !invalidQwenPrompt &&
    !disabled &&
    !busy &&
    !blockingPending &&
    draft.prompt.trim().length > 0 &&
    (node.type === "image" || currentVoice !== null);
  const placement = targetRect
    ? pinnedComposerPlacement(targetRect, metrics, {
        height: metrics.contentHeight,
        availableRect: targetAvailableRect ?? undefined,
      })
    : null;
  const shellClass = targetRect
    ? "fixed z-[56] overflow-visible rounded-[18px] border border-white/[0.09] bg-black/72 p-3 shadow-[0_24px_80px_rgba(0,0,0,0.45)] backdrop-blur-xl"
    : "relative overflow-visible rounded-[18px] border border-white/[0.09] bg-black/25 p-3 shadow-[0_24px_80px_rgba(0,0,0,0.24)]";
  const shellStyle = placement
    ? {
        left: placement.left,
        top: placement.top,
        width: placement.width,
        maxHeight: placement.maxHeight,
        overflowY: "auto" as const,
        overscrollBehavior: "contain" as const,
      }
    : undefined;

  const saveCurrentDraft = async () => {
    if (!saveDraft) return;
    if (node.type === "image") {
      await saveDraft(node.id, {
        media_kind: "image",
        prompt: draft.prompt,
        model_id: draft.modelId,
        mode: draft.mode,
        media: draft.media,
        params: strictImage ? { ...draft.imageParams } : sanitizeImageParams(draft.imageParams, draft.modelId),
        batch_size: draft.batchSize,
      });
      return;
    }
    await saveDraft(node.id, {
      media_kind: "audio",
      voice: draft.voice,
      params: sanitizeAudioParams(draft.audioParams, currentVoice),
      prompt: draft.prompt,
    });
  };

  const runAction = async (
    name: string,
    action: () => Promise<unknown>,
    recover?: (cause: unknown, message: string) => RecoverableLocalError | null,
  ) => {
    if (actionLockRef.current) return;
    const actionOwner = latestRef.current.epoch;
    actionLockRef.current = name;
    setBusy(name);
    setError(null);
    setRecoverableError(null);
    try {
      await action();
    } catch (cause) {
      if (aliveRef.current && latestRef.current.epoch === actionOwner) {
        const message = cause instanceof Error ? cause.message : String(cause);
        setError(message);
        setRecoverableError(recover?.(cause, message) ?? null);
      }
    } finally {
      if (aliveRef.current && latestRef.current.epoch === actionOwner) {
        if (actionLockRef.current === name) actionLockRef.current = null;
        setBusy((current) => (current === name ? null : current));
      }
    }
  };

  const generate = () =>
    runAction("generate", async () => {
      if (!canGenerate) return;
      const runtime = getRuntimeEnvironment();
      const storeIdentity = runtime
        ? {
            projectId: project.id,
            nodeId: node.id,
            runtimeKey: runtime.key,
            runtimeGeneration: runtime.generation,
          }
        : null;
      if (!currentStoreAllowsCreate(storeIdentity)) return;
      const requestIdentity = { ...latestRef.current };
      const requestEditVersion = editVersionRef.current;
      const imageParams = strictImage ? { ...draft.imageParams } : sanitizeImageParams(draft.imageParams, draft.modelId);
      const audioParams = sanitizeAudioParams(draft.audioParams, currentVoice);
      await saveCurrentDraft();
      const latest = latestRef.current;
      if (
        latest.epoch !== requestIdentity.epoch ||
        latest.ownerKey !== requestIdentity.ownerKey ||
        latest.projectId !== requestIdentity.projectId ||
        latest.nodeId !== requestIdentity.nodeId ||
        editVersionRef.current !== requestEditVersion ||
        !aliveRef.current ||
        !currentStoreAllowsCreate(storeIdentity)
      )
        return;
      if (node.type === "image") {
        await createImageCandidate(node.id, {
          entrypoint: "atelier.provider_image.v1",
          model_id: draft.modelId,
          mode: draft.mode,
          prompt: draft.prompt,
          media: draft.media,
          params: imageParams,
          batch_size: draft.batchSize,
        });
        return;
      }
      await createAudioCandidate(node.id, {
        entrypoint: "atelier.provider_audio.v1",
        voice: draft.voice,
        text: draft.prompt,
        params: audioParams,
      });
    });

  const body = (
    <section
      role="region"
      aria-label={t(node.type)}
      data-composer-dock={targetRect ? "node" : "inspector"}
      data-composer-side={placement?.side ?? "inspector"}
      className={shellClass}
      style={shellStyle}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      <div ref={contentRef} className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium text-white/82">
            {node.type === "audio" ? (
              <Music2 size={15} aria-hidden="true" />
            ) : (
              <Sparkles size={15} aria-hidden="true" />
            )}
            <span>{t(node.type)}</span>
          </div>
          <span className="rounded-lg border border-white/[0.08] bg-white/[0.04] px-2 py-1 text-[10px] text-white/40">
            {node.title || node.id}
          </span>
        </div>
        <BranchSourceCard node={node} />

        {blockingPending ? (
          <div className="rounded-xl border border-amber-300/20 bg-amber-300/10 p-3 text-xs text-amber-100">
            <p>{pending ? t("pending") : t("pendingProject")}</p>
            <button
              type="button"
              className={`${buttonClass} mt-2`}
              disabled={!!busy}
              onClick={() =>
                void runAction("readback", () =>
                  readbackPendingGeneration(blockingPending.clientRequestId),
                )
              }
            >
              {t("readOriginal")}
            </button>
          </div>
        ) : null}
        {draftUnsupported || unsupportedVoice ? (
          <p
            role="alert"
            className="rounded-xl border border-amber-300/20 bg-amber-300/10 p-3 text-xs text-amber-100"
          >
            {t("draftUnsupported")}
          </p>
        ) : null}
        {draftUnsupported ? (
          <div className="space-y-2 text-xs text-amber-100">
            <details>
              <summary>{t("draftRawLabel")}</summary>
              <pre aria-label={t("draftRawLabel")} className="max-h-40 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(node.data.generation_draft, null, 2)}</pre>
            </details>
            {correctedDraft ? <>
              <p>{t("draftCorrectionHint")}</p>
              <button type="button" className={buttonClass} disabled={!!busy || disabled} onClick={() => {
                markEdited();
                setDraft({ ...correctedDraft, prompt: draft.prompt });
                setDraftUnsupported(false);
              }}>{t("draftCorrectCopy")}</button>
            </> : null}
          </div>
        ) : null}
        <textarea
          aria-label={node.type === "audio" ? t("text") : t("prompt")}
          rows={3}
          value={draft.prompt}
          onCompositionStart={() => {
            composing.current = true;
          }}
          onCompositionEnd={() => {
            composing.current = false;
          }}
          onChange={(event) => {
            markEdited();
            setDraft((current) => ({ ...current, prompt: event.target.value }));
          }}
          onKeyDown={(event) => {
            if (
              composing.current ||
              event.nativeEvent.isComposing ||
              event.keyCode === 229
            )
              return;
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void generate();
            }
          }}
          className={`${inputClass} min-h-[78px] w-full resize-y`}
        />
        {node.type === "image" ? (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="flex items-center gap-2">
              {t("model")}
              <select
                aria-label={t("model")}
                className={inputClass}
                value={draft.modelId}
                onChange={(event) => {
                  markEdited();
                  const modelId = event.target.value;
                  setDraft((current) => ({
                    ...current,
                    modelId,
                    imageParams: {
                      ...defaultImageParams(modelId, current.mode),
                      ...(isAtelierStrictImageT2I(current.modelId, current.media) || isAtelierStrictImageT2I(modelId, current.media)
                        ? current.imageParams
                        : sanitizeImageParams(current.imageParams, modelId)),
                    },
                  }));
                }}
              >
                {IMAGE_MODEL_OPTIONS.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2">
              {t("batch")}
              <select
                aria-label={t("batch")}
                className={inputClass}
                value={draft.batchSize}
                onChange={(event) => {
                  markEdited();
                  setDraft((current) => ({
                    ...current,
                    batchSize: Number(event.target.value),
                  }));
                }}
              >
                {[1, 2, 3, 4, 5, 6].map((count) => (
                  <option key={count} value={count}>
                    {count}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="flex items-center gap-2">
              {t("voice")}
              <select
                aria-label={t("voice")}
                className={inputClass}
                value={draft.voice}
                onChange={(event) => {
                  markEdited();
                  const voice =
                    voices.find((item) => item.id === event.target.value) ??
                    null;
                  setDraft((current) => ({
                    ...current,
                    voice: event.target.value,
                    audioParams: sanitizeAudioParams(
                      current.audioParams,
                      voice,
                    ),
                  }));
                }}
              >
                {unsupportedVoice ? (
                  <option value={draft.voice}>{draft.voice}</option>
                ) : null}
                {voices.map((voice) => (
                  <option key={voice.id} value={voice.id}>
                    {voice.name}
                  </option>
                ))}
              </select>
            </label>
            {supports.has("speech_rate") ? (
              <NumberField
                label={t("speed")}
                min={0.5}
                max={2}
                step={0.1}
                value={draft.audioParams.speech_rate ?? 1}
                onChange={(value) => {
                  markEdited();
                  setDraft((current) => ({
                    ...current,
                    audioParams: { ...current.audioParams, speech_rate: value },
                  }));
                }}
              />
            ) : null}
          </div>
        )}
        {node.type === "image" ? (
          <details>
            <summary className="cursor-pointer text-xs text-white/50">
              {t("advanced")}
            </summary>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
              {imageParamEnabled(imageParamsSpec(draft.modelId), "size") ? (
                <label className="flex items-center gap-2">
                  {t("size")}
                  <input
                    aria-label={t("size")}
                    className={inputClass}
                    value={stringValue(currentImageParams.size, "")}
                    placeholder={strictImage ? defaultSize : undefined}
                    onChange={(event) => {
                      markEdited();
                      setDraft((current) => ({
                        ...current,
                        imageParams: {
                          ...current.imageParams,
                          size: event.target.value,
                        },
                      }));
                    }}
                  />
                </label>
              ) : null}
              {imageParamEnabled(imageParamsSpec(draft.modelId), "seed") ? (
                strictImage ? <label className="flex items-center gap-2">
                  {t("seed")}
                  <input aria-label={t("seed")} className={`${inputClass} w-24`} type="number"
                    min={0} max={2147483647} step={1}
                    value={currentImageParams.seed ?? ""}
                    onChange={(event) => {
                      markEdited();
                      const value = event.target.value;
                      setDraft((current) => {
                        const imageParams = { ...current.imageParams };
                        if (value === "") delete imageParams.seed;
                        else imageParams.seed = Number(value);
                        return { ...current, imageParams };
                      });
                    }} />
                </label> : <NumberField
                  label={t("seed")}
                  step={1}
                  value={numberValue(currentImageParams.seed, -1)}
                  onChange={(value) => {
                    markEdited();
                    setDraft((current) => ({
                      ...current,
                      imageParams: { ...current.imageParams, seed: value },
                    }));
                  }}
                />
              ) : null}
              {!wanImage && imageParamEnabled(
                imageParamsSpec(draft.modelId),
                "negativePrompt",
              ) ? (
                <label className="flex items-center gap-2">
                  {t("negativePrompt")}
                  <input
                    aria-label={t("negativePrompt")}
                    className={inputClass}
                    value={stringValue(currentImageParams.negative_prompt, "")}
                    onChange={(event) => {
                      markEdited();
                      setDraft((current) => ({
                        ...current,
                        imageParams: {
                          ...current.imageParams,
                          negative_prompt: event.target.value,
                        },
                      }));
                    }}
                  />
                </label>
              ) : null}
              {!wanImage && imageParamEnabled(
                imageParamsSpec(draft.modelId),
                "promptExtend",
              ) ? (
                <label className="flex items-center gap-2">
                  <input
                    aria-label={t("promptExtend")}
                    type="checkbox"
                    checked={boolValue(currentImageParams.prompt_extend, true)}
                    onChange={(event) => {
                      markEdited();
                      setDraft((current) => ({
                        ...current,
                        imageParams: {
                          ...current.imageParams,
                          prompt_extend: event.target.checked,
                        },
                      }));
                    }}
                  />
                  {t("promptExtend")}
                </label>
              ) : null}
              {imageParamEnabled(
                imageParamsSpec(draft.modelId),
                "watermark",
              ) ? (
                <label className="flex items-center gap-2">
                  <input
                    aria-label={t("watermark")}
                    type="checkbox"
                    checked={boolValue(currentImageParams.watermark, false)}
                    onChange={(event) => {
                      markEdited();
                      setDraft((current) => ({
                        ...current,
                        imageParams: {
                          ...current.imageParams,
                          watermark: event.target.checked,
                        },
                      }));
                    }}
                  />
                  {t("watermark")}
                </label>
              ) : null}
            </div>
          </details>
        ) : (
          <details>
            <summary className="cursor-pointer text-xs text-white/50">
              {t("advanced")}
            </summary>
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              {supports.has("pitch_rate") ? (
                <NumberField
                  label={t("pitch")}
                  min={0.5}
                  max={2}
                  step={0.1}
                  value={draft.audioParams.pitch_rate ?? 1}
                  onChange={(value) => {
                    markEdited();
                    setDraft((current) => ({
                      ...current,
                      audioParams: {
                        ...current.audioParams,
                        pitch_rate: value,
                      },
                    }));
                  }}
                />
              ) : null}
              {supports.has("volume") ? (
                <NumberField
                  label={t("volume")}
                  min={0}
                  max={100}
                  step={1}
                  value={draft.audioParams.volume ?? 50}
                  onChange={(value) => {
                    markEdited();
                    setDraft((current) => ({
                      ...current,
                      audioParams: { ...current.audioParams, volume: value },
                    }));
                  }}
                />
              ) : null}
              {supports.has("instructions") ? (
                <label className="flex items-center gap-2">
                  {t("instructions")}
                  <input
                    aria-label={t("instructions")}
                    className={inputClass}
                    value={stringValue(draft.audioParams.instructions, "")}
                    onChange={(event) => {
                      markEdited();
                      setDraft((current) => ({
                        ...current,
                        audioParams: {
                          ...current.audioParams,
                          instructions: event.target.value,
                        },
                      }));
                    }}
                  />
                </label>
              ) : null}
            </div>
            <p className="mt-2 text-xs text-white/40">{t("uploadOnly")}</p>
          </details>
        )}
        {node.type === "image" ? (
          <div className="space-y-1 text-xs text-white/55">
            <p>{t("refs")}</p>
            {reference.missing || missingDraftMedia ? (
              <p role="alert" className="text-amber-200">
                {t("missingRefs")}
              </p>
            ) : null}
            {incompleteI2i && !reference.hasReferences ? (
              <p role="alert" className="text-amber-200">{t("i2iRefsRequired")}</p>
            ) : null}
            {!draftUnsupported && draft.mode === "i2i" && !reference.hasReferences ? (
              <button type="button" className={buttonClass} disabled={!!busy || disabled} onClick={() => {
                markEdited();
                setDraft((current) => ({ ...current, mode: "t2i", media: [] }));
              }}>{t("switchToT2i")}</button>
            ) : null}
            {draft.media.map((item) => (
              <div
                key={`${item.node_id}:${item.ordinal}`}
                className="flex items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.03] px-2 py-1"
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- Atelier canvas previews render managed local media URLs. */}
                <img
                  alt=""
                  src={getAssetUrl(item.media_url)}
                  className="h-8 w-8 rounded object-cover"
                />
                <span className="min-w-0 truncate">
                  {item.ordinal + 1}. {item.node_id}
                </span>
              </div>
            ))}
          </div>
        ) : null}
        {invalidQwenPrompt ? <p role="alert" className="text-xs text-amber-200">{t("qwenPromptInvalid")}</p> : null}
        {imageDraftIssues.length > 0 ? (
          <div role="alert" className="space-y-2 rounded-xl border border-amber-300/20 bg-amber-300/10 p-3 text-xs text-amber-100">
            <p>{t("wan27Blocked")}</p>
            <ul className="list-disc break-words pl-4">
              {imageDraftIssues.map((issue) => {
                const label = issue.key === "negative_prompt" ? t("negativePrompt")
                  : issue.key === "prompt_extend" ? t("promptExtend")
                    : issue.key === "size" ? t("size") : issue.key === "seed" ? t("seed")
                      : issue.key === "watermark" ? t("watermark") : issue.key;
                return <li key={issue.key}>{label}: {JSON.stringify(issue.value)}</li>;
              })}
            </ul>
            <p>{t("wan27CorrectionHint")}</p>
            <button type="button" className={buttonClass} onClick={() => {
              markEdited();
              setDraft((current) => ({ ...current, imageParams: correctAtelierImageDraft(current.modelId, current.media, current.imageParams) }));
            }}>{t("wan27Correct")}</button>
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="text-xs text-rose-200">
            {error}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={buttonClass}
            disabled={!canPersist || !!busy || disabled}
            onClick={() => void runAction("save", saveCurrentDraft)}
          >
            {t("saveDraft")}
          </button>
          <button
            type="button"
            className={buttonClass}
            disabled={!canGenerate}
            onClick={() => void generate()}
          >
            {busy === "generate" ? (
              <Loader2
                size={13}
                aria-hidden="true"
                className="inline animate-spin"
              />
            ) : null}{" "}
            {t(busy === "generate" ? "generating" : "generate")}
          </button>
        </div>
        {attempts.length ? (
          <div className="space-y-2">
            {attempts.map((attempt) => (
              <div
                key={attempt.attempt_id}
                className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-2 text-xs text-white/60"
              >
                <div>
                  {t(attemptLabelKey(attempt))}
                  {attempt.error_code ? ` · ${attempt.error_code}` : ""}
                </div>
                <div className="mt-2 flex gap-2">
                  <button
                    className={buttonClass}
                    disabled={!!busy}
                    onClick={() =>
                      void runAction(`read:${attempt.attempt_id}`, () =>
                        refreshGenerationAttempt(attempt.attempt_id),
                      )
                    }
                  >
                    {t("check")}
                  </button>
                  {canContinueAttempt(attempt) ? (
                    <button
                      className={buttonClass}
                      disabled={!!busy}
                      onClick={() =>
                        void runAction(`continue:${attempt.attempt_id}`, () =>
                          continueGenerationAttempt(attempt.attempt_id),
                          (cause) =>
                            isStorageConflict(cause)
                              ? {
                                  kind: "attempt-conflict",
                                  ownerKey,
                                  attemptId: attempt.attempt_id,
                                  attemptRevision: attempt.attempt_revision,
                                }
                              : null,
                        )
                      }
                    >
                      {t("continue")}
                    </button>
                  ) : null}
                  {attempt.provider_execution.phase === "accepted" &&
                  attempt.status !== "needs_confirmation" ? (
                    <button
                      className={buttonClass}
                      disabled={!!busy}
                      onClick={() =>
                        void runAction(`cancel:${attempt.attempt_id}`, () =>
                          cancelGenerationAttempt(attempt.attempt_id),
                        )
                      }
                    >
                      {t("cancel")}
                    </button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {candidates.length ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {candidates.map(({ candidate, deleted }) => {
              const judgmentHistory = candidateJudgments.filter(item => item.node_id === node.id && item.candidate_id === candidate.id);
              const latestJudgment = latestCandidateJudgment(judgmentHistory);
              return <div
                key={`${deleted ? "deleted" : "live"}:${candidate.id}`}
                className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-2 text-xs text-white/65"
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span>{candidate.status}</span>
                  {node.data.selected_candidate_id === candidate.id ? (
                    <span>{t("selected")}</span>
                  ) : null}
                </div>
                {latestJudgment ? (
                  <div className="mb-2 rounded-lg border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-white/60">
                    {t("judgment")}: {t(`judgment_${latestJudgment.verdict}`)}
                  </div>
                ) : null}
                {candidate.media_url ? (
                  node.type === "audio" ? (
                    <audio
                      controls
                      preload="metadata"
                      src={getAssetUrl(candidate.media_url)}
                      className="w-full"
                    />
                  ) : (
                    <>
                      {/* eslint-disable-next-line @next/next/no-img-element -- Candidate previews render managed local media URLs. */}
                      <img
                        alt=""
                        src={getAssetUrl(candidate.media_url)}
                        className="max-h-40 w-full rounded object-contain"
                      />
                    </>
                  )
                ) : null}
                <div className="mt-2 flex flex-wrap gap-2">
                  {candidate.status === "completed" ? (
                    <button
                      className={buttonClass}
                      disabled={!!busy}
                      onClick={() => setReviewingCandidateId(candidate.id)}
                    >
                      {t("review")}
                    </button>
                  ) : null}
                  {!deleted && candidate.status === "completed" ? (
                    <button
                      className={buttonClass}
                      disabled={!!busy}
                      onClick={() =>
                        void runAction(`branch:${candidate.id}`, () =>
                          branchFromCandidate(node.id, candidate.id),
                          (cause) =>
                            isLostAckTransportError(cause) &&
                            useAtelierStore.getState().needsRefresh
                              ? {
                                  kind: "refresh-required",
                                  ownerKey,
                                  projectRevision: project.revision,
                                }
                              : null,
                        )
                      }
                    >
                      {t("createBranch")}
                    </button>
                  ) : null}
                  {!deleted && candidate.status === "completed" ? (
                    <button
                      className={buttonClass}
                      disabled={
                        !!busy ||
                        node.data.selected_candidate_id === candidate.id ||
                        !selectCandidate
                      }
                      onClick={() =>
                        void runAction(`select:${candidate.id}`, () =>
                          selectCandidate!(node.id, candidate.id),
                        )
                      }
                    >
                      {t("use")}
                    </button>
                  ) : null}
                  <button
                    className={buttonClass}
                    disabled={
                      !!busy ||
                      !setCandidateVisibility ||
                      node.data.selected_candidate_id === candidate.id
                    }
                    onClick={() =>
                      void runAction(`visibility:${candidate.id}`, () =>
                        setCandidateVisibility!(node.id, candidate.id, deleted),
                      )
                    }
                  >
                    {deleted ? t("restore") : t("hide")}
                  </button>
                </div>
              </div>;
            })}
          </div>
        ) : null}
        {node && reviewingCandidateId ? (
          <CandidateJudgmentDialog
            candidateId={reviewingCandidateId}
            mediaKind={node.type as "image" | "audio"}
            history={reviewingHistory}
            open={true}
            readOnly={!!candidates.find(item => item.candidate.id === reviewingCandidateId)?.deleted}
            saving={busy === `judge:${reviewingCandidateId}`}
            onClose={() => setReviewingCandidateId(null)}
            onSave={async input => {
              const key = `judge:${reviewingCandidateId}`;
              actionLockRef.current = key;
              setBusy(key);
              setError(null);
              try {
                await setCandidateJudgment(node.id, reviewingCandidateId, input);
                if (aliveRef.current) setReviewingCandidateId(null);
              } finally {
                if (actionLockRef.current === key) actionLockRef.current = null;
                if (aliveRef.current) setBusy(current => current === key ? null : current);
              }
            }}
          />
        ) : null}
      </div>
    </section>
  );
  return targetRect && typeof document !== "undefined"
    ? createPortal(body, document.body)
    : body;
}

function NumberField({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="flex items-center gap-2">
      {label}
      <input
        aria-label={label}
        className={`${inputClass} w-24`}
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

export type { MediaDraftInput };
