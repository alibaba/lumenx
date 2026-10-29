'use client';

import { useEffect, useCallback, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Sparkles } from 'lucide-react';
import { isSeedanceModel, PlaygroundCapabilityError, requireSeedanceCapability, seedanceInputBlockReason, seedanceNotAdmittedReason, type PlaygroundBlockReason } from '@/lib/playgroundCapabilities';
import { useSeedanceCapability } from './useSeedanceCapability';
import ModeSelector from './ModeSelector';
import ModelSelector from './ModelSelector';
import MediaInput from './MediaInput';
import PromptInput from './PromptInput';
import ParameterBar from './ParameterBar';
import { buildWan27ImageRequestParameters, correctWan27ImageDraft, correctQwen20ImageDraft, effectivePlaygroundMode, imageDraftValueText, isWan27TextToImage, isQwen20TextToImage, readPlaygroundParameters, wan27ImageDraftIssues, qwen20ImageDraftIssues } from './playgroundImageContract';

import ResultGallery from './ResultGallery';
import { usePlaygroundStore, type PlaygroundMode, type PlaygroundGeneration, type PlaygroundParameters, type QueuedRequest } from './usePlaygroundStore';
import { playgroundApi, type PlaygroundGenerationResponse, type PlaygroundRequestReceiptResponse } from '@/lib/api';
import { parsePlaygroundRequestReceipt } from '@/lib/playgroundRequestReceipt';
import {
  beginPlaygroundGenerationRequest,
  listPlaygroundGenerationRequests,
  resolveStoredPlaygroundGenerationRequest,
  type PlaygroundGenerateRequestBody,
  type StoredPlaygroundGenerationRequest,
} from '@/lib/playgroundGenerationRequest';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment, subscribeRuntimeEnvironment, type RuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { getModelOption } from './playgroundModels';
import { buildPlaygroundRequestParameters, supportsPlaygroundNegativePrompt } from './playgroundParameterContract';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MODE_LABELS: Record<PlaygroundMode, string> = {
  t2i: 'T2I',
  i2i: 'I2I',
  t2v: 'T2V',
  i2v: 'I2V',
  r2v: 'R2V',
  v2v: 'V2V',
};

/** Modes that require media input (image or video source).
 *  t2i also shows optional media input — when provided, it auto-becomes i2i. */
const MODES_WITH_MEDIA: PlaygroundMode[] = ['i2i', 'i2v', 'r2v', 'v2v'];
const MODES_WITH_OPTIONAL_MEDIA: PlaygroundMode[] = ['t2i'];

/** Polling interval for generation status (ms) */
const POLL_INTERVAL = 2000;

interface GenerationPoll {
  timer?: ReturnType<typeof setTimeout>;
  journal?: StoredPlaygroundGenerationRequest;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert API response to store-compatible PlaygroundGeneration */
function toGeneration(resp: PlaygroundGenerationResponse): PlaygroundGeneration {
  return {
    id: resp.id,
    mode: resp.mode as PlaygroundMode,
    model_id: resp.model_id,
    prompt: resp.prompt,
    negative_prompt: resp.negative_prompt,
    input_media: resp.input_media,
    parameters: readPlaygroundParameters(resp.parameters, resp.model_id, resp.mode),
    batch_size: resp.batch_size,
    outputs: resp.outputs.map((o) => ({
      id: o.id,
      media_path: o.media_path,
      media_type: o.media_type as 'image' | 'video',
      thumbnail_path: o.thumbnail_path,
      saved_to_library: o.saved_to_library,
      attempt_id: o.attempt_id,
    })),
    attempts: resp.attempts?.map((a) => ({
      attempt_id: a.attempt_id,
      ordinal: a.ordinal,
      status: a.status as NonNullable<PlaygroundGeneration['attempts']>[number]['status'],
      error: a.error ?? undefined,
      provider_execution: a.provider_execution,
    })),
    status: resp.status as PlaygroundGeneration['status'],
    error: resp.error ?? undefined,
    created_at: resp.created_at,
  };
}

function getR2vImageLimit(modelId: string): number | null {
  const limit = getModelOption(modelId)?.maxReferenceImages ?? 0;
  return limit > 0 ? limit : null;
}

function isTerminal(gen: PlaygroundGeneration): boolean {
  return gen.status === 'completed'
    || gen.status === 'failed'
    || gen.status === 'needs_confirmation'
    || gen.status === 'cancelled';
}

function requestFromCurrentInput(input: {
  mode: PlaygroundMode;
  modelId: string;
  prompt: string;
  negativePrompt: string;
  negativePromptProvided: boolean;
  inputMedia: string[];
  parameters: PlaygroundParameters;
  batchSize: number;
}): StoredPlaygroundGenerationRequest {
  const effectiveMode = effectivePlaygroundMode(input.mode, input.inputMedia);
  if (wan27ImageDraftIssues(input).length > 0 || qwen20ImageDraftIssues(input).length > 0) throw new Error('Incompatible image draft');
  const wanImage = isWan27TextToImage(input.modelId, input.mode, input.inputMedia);
  const strictImage = wanImage || isQwen20TextToImage(input.modelId, input.mode, input.inputMedia);
  const requestParameters = strictImage ? buildWan27ImageRequestParameters(input.parameters) : buildPlaygroundRequestParameters(input.modelId, input.parameters);
  const requestNegativePrompt = !wanImage && supportsPlaygroundNegativePrompt(input.modelId)
    && (input.negativePromptProvided || input.negativePrompt !== '') ? input.negativePrompt : undefined;
  return beginPlaygroundGenerationRequest({
    environment: requireActiveRuntimeEnvironment(),
    mode: effectiveMode,
    modelId: input.modelId,
    prompt: input.prompt.trim(),
    negativePrompt: requestNegativePrompt,
    inputMedia: input.inputMedia,
    parameters: requestParameters,
    batchSize: input.batchSize,
  });
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function PlaygroundPage() {
  const t = useTranslations('playground');

  const mode = usePlaygroundStore((s) => s.mode);
  const modelId = usePlaygroundStore((s) => s.modelId);
  const prompt = usePlaygroundStore((s) => s.prompt);
  const negativePrompt = usePlaygroundStore((s) => s.negativePrompt);
  const negativePromptProvided = usePlaygroundStore((s) => s.negativePromptProvided);
  const inputMedia = usePlaygroundStore((s) => s.inputMedia);
  const parameters = usePlaygroundStore((s) => s.parameters);
  const batchSize = usePlaygroundStore((s) => s.batchSize);
  const history = usePlaygroundStore((s) => s.history);
  const mergeHistoryPage = usePlaygroundStore((s) => s.mergeHistoryPage);
  const setHistoryLoading = usePlaygroundStore((s) => s.setHistoryLoading);
  const setHistoryError = usePlaygroundStore((s) => s.setHistoryError);
  const setTemplates = usePlaygroundStore((s) => s.setTemplates);
  const startGeneration = usePlaygroundStore((s) => s.startGeneration);
  const updateGeneration = usePlaygroundStore((s) => s.updateGeneration);
  const enqueueRequest = usePlaygroundStore((s) => s.enqueueRequest);
  const markDispatching = usePlaygroundStore((s) => s.markDispatching);
  const removeFromQueue = usePlaygroundStore((s) => s.removeFromQueue);
  const markNeedsReadback = usePlaygroundStore((s) => s.markNeedsReadback);
  const clearRuntimeState = usePlaygroundStore((s) => s.clearRuntimeState);
  const queue = usePlaygroundStore((s) => s.queue);
  const activeCount = usePlaygroundStore((s) => s.activeGenerationIds.length);
  const maxConcurrent = usePlaygroundStore((s) => s.maxConcurrent);

  const polls = useRef<Map<string, GenerationPoll>>(new Map());
  const readbackRequests = useRef<Map<string, number | undefined>>(new Map());
  const serverRequestFingerprints = useRef<Map<string, string>>(new Map());
  const activationRef = useRef(0);
  const submittingRef = useRef(false);
  const preparationRevision = useRef(0);
  const [preparing, setPreparing] = useState(false);
  const [submissionError, setSubmissionError] = useState<PlaygroundBlockReason | null>(null);
  const capability = useSeedanceCapability(modelId);
  const inputBlockReason = seedanceInputBlockReason(modelId, mode, inputMedia);
  const imageDraft = { modelId, mode, inputMedia, parameters, negativePrompt, prompt };
  const qwenImage = isQwen20TextToImage(modelId, mode, inputMedia);
  const imageDraftIssues = [...wan27ImageDraftIssues(imageDraft), ...qwen20ImageDraftIssues(imageDraft)];
  const hasNestedNegative = qwenImage && parameters.negative_prompt !== undefined;
  useEffect(() => {
    preparationRevision.current += 1;
    submittingRef.current = false;
    setPreparing(false);
    setSubmissionError(null);
  }, [modelId, mode, inputMedia, parameters, prompt, negativePrompt, negativePromptProvided, batchSize]);

  const r2vImageLimit = mode === 'r2v' ? getR2vImageLimit(modelId) : null;
  const mediaLimitExceeded = r2vImageLimit !== null && inputMedia.length > r2vImageLimit;

  // ─── Cleanup poll timers ───────────────────────────────────────────────────

  const clearRuntimeLocalState = useCallback(() => {
    polls.current.forEach((poll) => clearTimeout(poll.timer));
    polls.current.clear();
    readbackRequests.current.clear();
    clearRuntimeState();
    preparationRevision.current += 1;
    setSubmissionError(null);
    submittingRef.current = false;
    setPreparing(false);
  }, [clearRuntimeState]);

  useEffect(() => {
    const activePolls = polls.current;
    const readbacks = readbackRequests.current;
    const serverFingerprints = serverRequestFingerprints.current;
    return () => {
      activationRef.current += 1;
      activePolls.forEach((poll) => clearTimeout(poll.timer));
      activePolls.clear();
      readbacks.clear();
      serverFingerprints.clear();
    };
  }, []);

  // ─── Status poller ─────────────────────────────────────────────────────────

  const adoptGeneration = useCallback((gen: PlaygroundGeneration) => {
    const exists = usePlaygroundStore.getState().history.some((item) => item.id === gen.id);
    if (exists) updateGeneration(gen);
    else startGeneration(gen);
  }, [startGeneration, updateGeneration]);

  const finishJournal = useCallback((journal: StoredPlaygroundGenerationRequest | undefined) => {
    if (!journal) return;
    try {
      resolveStoredPlaygroundGenerationRequest(journal);
    } catch (err) {
      console.error('[Playground] Failed to resolve request journal:', err);
    }
  }, []);

  const sameJournalEnvironment = useCallback((
    journal: StoredPlaygroundGenerationRequest,
    environment: RuntimeEnvironment,
  ): boolean => (
    journal.apiBase === environment.apiUrl
      && journal.storageId === environment.storageId
      && isCurrentRuntimeEnvironment(environment.key, environment.generation)
  ), []);

  const sameLiveJournalEnvironment = useCallback((
    journal: StoredPlaygroundGenerationRequest,
    environment: RuntimeEnvironment,
    activation: number,
  ): boolean => activationRef.current === activation && sameJournalEnvironment(journal, environment), [sameJournalEnvironment]);

  const serverFingerprintKey = useCallback((
    journal: StoredPlaygroundGenerationRequest,
    environment: RuntimeEnvironment,
  ): string => `${environment.key}:${journal.clientRequestId}`, []);

  const stopPolling = useCallback((generationId: string, poll: GenerationPoll) => {
    // A late read may share an id with a newer poll after A → B → A.
    if (polls.current.get(generationId) !== poll) return;
    clearTimeout(poll.timer);
    polls.current.delete(generationId);
  }, []);

  const startPolling = useCallback((
    generationId: string,
    journal: StoredPlaygroundGenerationRequest | undefined,
    environment: RuntimeEnvironment,
    activation: number,
  ) => {
    // Prevent duplicate timers and keep each generation at one in-flight read.
    const existing = polls.current.get(generationId);
    if (existing) {
      // History may start observation before its persisted request is read back.
      if (!existing.journal && journal) existing.journal = journal;
      return;
    }
    const poll: GenerationPoll = { journal };
    polls.current.set(generationId, poll);

    const shouldContinue = () => polls.current.get(generationId) === poll
      && activationRef.current === activation
      && isCurrentRuntimeEnvironment(environment.key, environment.generation)
      && !usePlaygroundStore.getState().deletedGenerationIds.includes(generationId);

    const schedule = () => {
      poll.timer = setTimeout(async () => {
        if (!shouldContinue()) {
          stopPolling(generationId, poll);
          return;
        }
        try {
          const fullResp = await playgroundApi.getGeneration(generationId);
          if (!shouldContinue()) {
            stopPolling(generationId, poll);
            return;
          }
          const gen = toGeneration(fullResp);
          updateGeneration(gen);

          if (isTerminal(gen)) {
            const stored = poll.journal;
            stopPolling(generationId, poll);
            finishJournal(stored);
            if (stored) serverRequestFingerprints.current.delete(serverFingerprintKey(stored, environment));
            return;
          }
        } catch (err) {
          if (shouldContinue()) console.error('[Playground] Poll failed for', generationId, err);
        }
        if (shouldContinue()) schedule();
        else stopPolling(generationId, poll);
      }, POLL_INTERVAL);
    };

    schedule();
  }, [finishJournal, serverFingerprintKey, stopPolling, updateGeneration]);

  // ─── Generate handler — enqueue a request; the dispatcher runs it ──────────

  const handleGenerate = useCallback(async () => {
    if (!prompt.trim() || mediaLimitExceeded || inputBlockReason || imageDraftIssues.length > 0 || submittingRef.current) return;
    const environment = getRuntimeEnvironment();
    const activation = activationRef.current;
    if (!environment) return;
    const revision = ++preparationRevision.current;
    submittingRef.current = true;
    setPreparing(true);
    setSubmissionError(null);
    try {
      if (isSeedanceModel(modelId)) {
        await requireSeedanceCapability({ modelId, mode, inputMedia }, environment, playgroundApi.getCapabilities);
      }
      const current = usePlaygroundStore.getState();
      if (revision !== preparationRevision.current || activation !== activationRef.current || !isCurrentRuntimeEnvironment(environment.key, environment.generation)
        || current.modelId !== modelId || current.mode !== mode || current.prompt !== prompt
        || current.inputMedia !== inputMedia || current.parameters !== parameters || current.negativePrompt !== negativePrompt
        || current.negativePromptProvided !== negativePromptProvided || current.batchSize !== batchSize) return;
      const journal = requestFromCurrentInput({ mode, modelId, prompt, negativePrompt, negativePromptProvided, inputMedia, parameters, batchSize });
      enqueueRequest({
        clientRequestId: journal.clientRequestId,
        requestFingerprint: journal.requestFingerprint,
        body: journal.request,
        journal,
      });
    } catch (err) {
      if (revision !== preparationRevision.current || activation !== activationRef.current || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      if (err instanceof PlaygroundCapabilityError) setSubmissionError(err.reason);
      else console.error('[Playground] Failed to prepare request:', err);
    } finally {
      if (revision === preparationRevision.current && activation === activationRef.current) { submittingRef.current = false; setPreparing(false); }
    }
  }, [mode, modelId, prompt, negativePrompt, negativePromptProvided, inputMedia, parameters, batchSize, enqueueRequest, mediaLimitExceeded, inputBlockReason, imageDraftIssues.length]);

  // ─── Queue dispatcher — POST a queued request, then poll for status ────────

  const enqueueReadbackRequest = useCallback((journal: StoredPlaygroundGenerationRequest, error?: string) => {
    enqueueRequest({
      clientRequestId: journal.clientRequestId,
      requestFingerprint: journal.requestFingerprint,
      body: journal.request,
      journal,
      status: 'needs_readback',
      error,
    });
  }, [enqueueRequest]);

  const handleReceipt = useCallback((
    receipt: PlaygroundRequestReceiptResponse,
    journal: StoredPlaygroundGenerationRequest,
    environment: RuntimeEnvironment,
    activation: number,
    queueId?: string,
  ) => {
    if (!sameLiveJournalEnvironment(journal, environment, activation)) return;
    const fingerprintKey = serverFingerprintKey(journal, environment);
    const parsed = parsePlaygroundRequestReceipt(
      receipt,
      journal,
      serverRequestFingerprints.current.get(fingerprintKey),
    );
    serverRequestFingerprints.current.set(fingerprintKey, parsed.serverRequestFingerprint);
    if (parsed.requestStatus === 'deleted') {
      if (queueId) removeFromQueue(queueId);
      finishJournal(journal);
      serverRequestFingerprints.current.delete(fingerprintKey);
      return;
    }
    const gen = toGeneration(parsed.generation);
    adoptGeneration(gen);
    if (queueId) removeFromQueue(queueId);
    if (isTerminal(gen)) {
      finishJournal(journal);
      serverRequestFingerprints.current.delete(fingerprintKey);
    } else {
      startPolling(gen.id, journal, environment, activation);
    }
  }, [adoptGeneration, finishJournal, removeFromQueue, sameLiveJournalEnvironment, serverFingerprintKey, startPolling]);

  const readBackJournal = useCallback(async (
    journal: StoredPlaygroundGenerationRequest,
    environment: RuntimeEnvironment,
    activation: number,
    options: { queueId?: string; keepVisibleOnFailure?: boolean } = {},
  ) => {
    if (!sameLiveJournalEnvironment(journal, environment, activation)) return;
    try {
      const receipt = await playgroundApi.getRequestReceipt(journal.clientRequestId);
      if (!sameLiveJournalEnvironment(journal, environment, activation)) return;
      handleReceipt(receipt, journal, environment, activation, options.queueId);
    } catch (err) {
      if (!sameLiveJournalEnvironment(journal, environment, activation)) return;
      console.error('[Playground] Request readback failed:', err);
      const message = err instanceof Error ? err.message : 'readback_failed';
      if (options.queueId) {
        markNeedsReadback(options.queueId, message);
      } else if (options.keepVisibleOnFailure) {
        enqueueReadbackRequest(journal, message);
      }
    }
  }, [enqueueReadbackRequest, handleReceipt, markNeedsReadback, sameLiveJournalEnvironment]);

  const dispatchRequest = useCallback(async (req: QueuedRequest) => {
    const environment = getRuntimeEnvironment();
    const activation = activationRef.current;
    if (!environment || !sameLiveJournalEnvironment(req.journal, environment, activation)) {
      markNeedsReadback(req.id, 'environment_changed');
      return;
    }
    const rejectBeforeAcceptance = (reason: PlaygroundBlockReason) => {
      // This request has either never been posted, or has the exact non-admission receipt.
      try {
        resolveStoredPlaygroundGenerationRequest(req.journal);
        removeFromQueue(req.id);
      } catch {
        markNeedsReadback(req.id, 'journal_cleanup_failed');
      }
      setSubmissionError(reason);
    };
    if (isSeedanceModel(req.body.model_id)) {
      try {
        await requireSeedanceCapability({ modelId: req.body.model_id, mode: req.body.mode, inputMedia: req.body.input_media ?? [] }, environment, playgroundApi.getCapabilities);
      } catch (err) {
        if (!sameLiveJournalEnvironment(req.journal, environment, activation)) return;
        rejectBeforeAcceptance(err instanceof PlaygroundCapabilityError ? err.reason : 'seedance_capability_check_failed');
        return;
      }
      if (!sameLiveJournalEnvironment(req.journal, environment, activation)) return;
    }
    try {
      const resp = await playgroundApi.generate(req.body as PlaygroundGenerateRequestBody);
      if (!sameLiveJournalEnvironment(req.journal, environment, activation)) return;
      handleReceipt(resp, req.journal, environment, activation, req.id);
    } catch (err) {
      if (!sameLiveJournalEnvironment(req.journal, environment, activation)) return;
      const notAdmitted = isSeedanceModel(req.body.model_id) ? seedanceNotAdmittedReason(err) : null;
      if (notAdmitted) { rejectBeforeAcceptance(notAdmitted); return; }
      console.error('[Playground] Dispatch failed:', err);
      readBackJournal(req.journal, environment, activation, { queueId: req.id, keepVisibleOnFailure: true });
    }
  }, [handleReceipt, markNeedsReadback, readBackJournal, sameLiveJournalEnvironment, removeFromQueue]);

  // ─── Fetch history/templates and recover local request journals ────────────

  const loadHistoryPage = useCallback(async (
    environment: RuntimeEnvironment,
    activation: number,
    offset: number,
  ) => {
    const { historyPageSize } = usePlaygroundStore.getState();
    setHistoryLoading(true);
    setHistoryError(undefined);
    try {
      const items = await playgroundApi.getHistory(historyPageSize, offset);
      if (activationRef.current !== activation || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      const generations = items.map(toGeneration);
      mergeHistoryPage(generations, { offset, limit: historyPageSize, reset: offset === 0 });
      for (const gen of generations) {
        if (!isTerminal(gen)) {
          updateGeneration(gen);
          startPolling(gen.id, undefined, environment, activation);
        }
      }
    } catch (err) {
      if (activationRef.current !== activation || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      console.error('[Playground] Failed to fetch history:', err);
      setHistoryError(err instanceof Error ? err.message : 'history_failed');
    } finally {
      if (activationRef.current === activation && isCurrentRuntimeEnvironment(environment.key, environment.generation)) {
        setHistoryLoading(false);
      }
    }
  }, [mergeHistoryPage, setHistoryError, setHistoryLoading, startPolling, updateGeneration]);

  const handleLoadMoreHistory = useCallback(() => {
    const environment = getRuntimeEnvironment();
    const activation = activationRef.current;
    if (!environment || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
    const { historyNextOffset, isHistoryLoading } = usePlaygroundStore.getState();
    if (isHistoryLoading) return;
    loadHistoryPage(environment, activation, historyNextOffset);
  }, [loadHistoryPage]);

  const loadRuntimeData = useCallback((environment: RuntimeEnvironment, activation: number) => {
    loadHistoryPage(environment, activation, 0);

    playgroundApi.getTemplates().then((items) => {
      if (activationRef.current !== activation || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      setTemplates(
        items.map((t) => ({
          id: t.id,
          name: t.name,
          category: t.category,
          prompt: t.prompt,
          negative_prompt: t.negative_prompt,
          default_mode: t.default_mode as PlaygroundMode | undefined,
          default_model_id: t.default_model_id,
          // A template may inherit both model and mode; decide projection when it is applied.
          default_parameters: t.default_parameters,
          created_at: t.created_at,
          updated_at: t.updated_at,
        }))
      );
    }).catch((err) => {
      if (activationRef.current !== activation || !isCurrentRuntimeEnvironment(environment.key, environment.generation)) return;
      console.error('[Playground] Failed to fetch templates:', err);
    });

    try {
      for (const journal of listPlaygroundGenerationRequests({ environment })) {
        readBackJournal(journal, environment, activation, { keepVisibleOnFailure: true });
      }
    } catch (err) {
      console.error('[Playground] Failed to restore request journals:', err);
    }
  }, [loadHistoryPage, readBackJournal, setTemplates]);

  useEffect(() => {
    const activation = activationRef.current + 1;
    activationRef.current = activation;
    const environment = getRuntimeEnvironment();
    if (environment) loadRuntimeData(environment, activation);
    const unsubscribe = subscribeRuntimeEnvironment(() => {
      const nextActivation = activationRef.current + 1;
      activationRef.current = nextActivation;
      clearRuntimeLocalState();
      const nextEnvironment = getRuntimeEnvironment();
      if (nextEnvironment) loadRuntimeData(nextEnvironment, nextActivation);
    });
    return () => {
      activationRef.current += 1;
      unsubscribe();
      clearRuntimeLocalState();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearRuntimeLocalState, loadRuntimeData]);

  // Pump: dispatch pending requests up to the concurrency limit.
  const pump = useCallback(() => {
    const s = usePlaygroundStore.getState();
    const dispatching = s.queue.filter((q) => q.status === 'dispatching').length;
    let slots = s.maxConcurrent - s.activeGenerationIds.length - dispatching;
    if (slots <= 0) return;
    for (const req of s.queue) {
      if (slots <= 0) break;
      if (req.status !== 'pending') continue;
      slots -= 1;
      markDispatching(req.id);
      dispatchRequest(req);
    }
  }, [markDispatching, dispatchRequest]);

  // Run the pump whenever the queue, in-flight count, or concurrency changes.
  useEffect(() => {
    pump();
  }, [queue, activeCount, maxConcurrent, pump]);

  useEffect(() => {
    for (const req of queue) {
      if (req.status !== 'needs_readback' || req.readbackRequestedAt === undefined) continue;
      if (readbackRequests.current.get(req.id) === req.readbackRequestedAt) continue;
      readbackRequests.current.set(req.id, req.readbackRequestedAt);
      const environment = getRuntimeEnvironment();
      const activation = activationRef.current;
      if (!environment) continue;
      readBackJournal(req.journal, environment, activation, { queueId: req.id, keepVisibleOnFailure: true });
    }
  }, [queue, readBackJournal]);

  // ─── Derived values ────────────────────────────────────────────────────────

  const resultCount = history.reduce((n, g) => n + g.outputs.length, 0);
  const showMediaInput = MODES_WITH_MEDIA.includes(mode) || MODES_WITH_OPTIONAL_MEDIA.includes(mode);
  const canGenerate = prompt.trim().length > 0 && !mediaLimitExceeded && !inputBlockReason && capability.available && imageDraftIssues.length === 0 && !preparing;
  const blockReason = inputBlockReason ?? submissionError ?? capability.reason;

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full flex-col overflow-hidden text-foreground">
      {/* ═══ PAGE HEADER ═══ */}
      <header className="flex shrink-0 items-center justify-between border-b border-border-subtle px-7 py-5">
        <div className="flex flex-col gap-1">
          <span className="font-mono text-[0.625rem] font-medium uppercase tracking-[0.2em] text-text-muted">
            FREEFORM STUDIO
            <span className="text-primary font-semibold"> · {t('header.eyebrowAccent')}</span>
          </span>
          <div className="flex items-baseline gap-[10px]">
            <h1 className="font-display text-[1.625rem] md:text-[2.125rem] font-semibold tracking-tight text-foreground atelier-display">
              {t('header.title')}
            </h1>
            <span className="font-mono text-[0.6875rem] uppercase tracking-[0.1em] text-text-muted">
              {t('header.resultsCount', { count: resultCount })}
            </span>
          </div>
          <p className="font-mono text-text-muted text-[0.6875rem] tracking-[0.06em]">
            {t('header.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="atelier-badge rounded border border-glass-border bg-glass px-2 py-1 text-[0.625rem] uppercase tracking-[0.18em] text-text-muted">
            {MODE_LABELS[mode]}
          </span>
        </div>
      </header>

      {/* ═══ SPLIT LAYOUT ═══ */}
      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* ─── LEFT: INPUT PANEL ─── */}
        <aside className="flex w-[420px] shrink-0 flex-col gap-3 overflow-y-auto border-r border-glass-border px-4 py-4 scrollbar-thin">
          {/* Mode */}
          <section className="glass-panel atelier-card rounded-[20px] px-5 py-5">
            <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
              {t('compose.modeLabel')}
            </div>
            <ModeSelector />
          </section>

          {/* Prompt — first, the primary input */}
          <section className="glass-panel atelier-card rounded-[20px] px-5 py-5">
            <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
              {t('compose.promptLabel')}
            </div>
            <PromptInput />
          </section>

          {/* Media Input (conditional) */}
          {showMediaInput && (
            <section className="glass-panel atelier-card rounded-[20px] px-5 py-5">
              <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
                {t(
                  mode === 'v2v'
                    ? 'compose.mediaSourceVideo'
                    : mode === 'r2v'
                      ? 'compose.mediaRefMaterial'
                      : mode === 'i2v'
                        ? 'compose.mediaFirstFrame'
                        : 'compose.mediaReference'
                )}
              </div>
              <MediaInput />
            </section>
          )}

          {/* Model & Parameters — merged into one card (mockup) */}
          <section className="glass-panel atelier-card rounded-[20px] px-5 py-5 relative z-30">
            <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
              {t('compose.modelLabel')}
            </div>
            <ModelSelector />
            <div className="my-4 h-px bg-border-subtle" />
            <div className="mb-3 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.16em] text-text-secondary">
              {t('compose.parametersLabel')}
            </div>
            <ParameterBar />
          </section>

          {/* Spacer to push generate button to bottom */}
          <div className="flex-1" />

          {/* Generate CTA (sticky) */}
          <div className={`sticky bottom-0 -mx-4 -mb-4 border-t border-glass-border bg-transparent backdrop-blur-md px-4 pb-4 pt-4 ${imageDraftIssues.length > 0 ? 'z-40' : ''}`}>
            {imageDraftIssues.length > 0 && (
              <div role="alert" className="mb-3 rounded-[12px] border border-glass-border bg-glass px-3 py-2 text-xs text-text-secondary">
                <p>{t('imageContract.blocked')}</p>
                <ul className="my-2 max-h-32 overflow-y-auto break-words pl-4 list-disc">
                  {imageDraftIssues.map((issue) => {
                    const labels: Record<string, string> = {
                      size: t('parameters.imageSize'), seed: 'Seed', watermark: t('parameters.watermark'),
                      prompt_extend: t('parameters.promptExtend'), negative_prompt: t(qwenImage ? 'imageContract.qwenParameterNegative' : 'prompt.negativeLabel'),
                      negativePrompt: t('prompt.negativeLabel'), quality: t('parameters.quality'),
                      duration: t('parameters.duration'), resolution: t('parameters.resolution'),
                      aspect_ratio: t('parameters.aspectRatio'), audio: t('parameters.generateAudio'),
                    };
                    return <li key={`${issue.source}:${issue.key}`}>{Object.prototype.hasOwnProperty.call(labels, issue.key) ? labels[issue.key] : issue.key}: {imageDraftValueText(issue.value)}</li>;
                  })}
                </ul>
                <p>{t(qwenImage ? 'imageContract.qwenCorrectionHint' : 'imageContract.correctionHint')}</p>
                {qwenImage && imageDraftIssues.some((issue) => issue.source === 'negativePrompt') && <p className="mt-2">{t('imageContract.qwenNegativeLimit')}</p>}
                {qwenImage && imageDraftIssues.some((issue) => issue.source === 'prompt') && <p className="mt-2">{t('imageContract.qwenPromptInvalid')}</p>}
                {hasNestedNegative && (
                  <div className="mt-2 space-y-2 break-words">
                    <p>{t('prompt.negativeLabel')}: {imageDraftValueText(negativePrompt)}</p>
                    {typeof parameters.negative_prompt === 'string' && <button type="button" className="block underline" onClick={() => {
                      const current = usePlaygroundStore.getState();
                      if (typeof current.parameters.negative_prompt !== 'string') return;
                      const parameters = { ...current.parameters };
                      const negativePrompt = parameters.negative_prompt!;
                      delete parameters.negative_prompt;
                      usePlaygroundStore.setState({ parameters, negativePrompt, negativePromptProvided: true });
                    }}>{t('imageContract.qwenUseParameterNegative')}</button>}
                    <button type="button" className="block underline" onClick={() => {
                      const parameters = { ...usePlaygroundStore.getState().parameters };
                      delete parameters.negative_prompt;
                      usePlaygroundStore.setState({ parameters });
                    }}>{t('imageContract.qwenUseVisibleNegative')}</button>
                  </div>
                )}
                {(!qwenImage || imageDraftIssues.some((issue) => issue.source === 'parameter' && issue.key !== 'negative_prompt')) && <button type="button" className="mt-2 underline" onClick={() => {
                  const current = usePlaygroundStore.getState();
                  usePlaygroundStore.setState(qwenImage ? correctQwen20ImageDraft(current) : correctWan27ImageDraft(current));
                }}>{t('imageContract.correct')}</button>}
              </div>
            )}
            {(capability.loading || blockReason) && (
              <div role="alert" className="mb-3 rounded-[12px] border border-glass-border bg-glass px-3 py-2 text-xs text-text-secondary">
                {t(`capabilities.${blockReason ?? 'checking'}`)}
                {mode === 't2v' && inputMedia.length > 0 && isSeedanceModel(modelId) && (
                  <button type="button" className="ml-2 underline" onClick={() => usePlaygroundStore.getState().setInputMedia([])}>
                    {t('capabilities.clearReferences')}
                  </button>
                )}
                {!capability.loading && !inputBlockReason && (
                  <button type="button" className="ml-2 underline" onClick={() => { setSubmissionError(null); void capability.recheck(); }}>
                    {t('capabilities.recheck')}
                  </button>
                )}
              </div>
            )}
            {mediaLimitExceeded && (
              <div role="alert" className="mb-3 rounded-[12px] border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
                {t('media.limitExceeded', { max: r2vImageLimit, current: inputMedia.length })}
              </div>
            )}
            <button
              type="button"
              onClick={handleGenerate}
              disabled={!canGenerate}
              className={[
                'inline-flex w-full items-center justify-center gap-[7px] rounded-full px-6 py-[13px]',
                "font-['Space_Grotesk',sans-serif] text-sm font-semibold",
                'bg-primary text-on-accent shadow-[var(--glow-primary)] transition-all duration-150 disabled:opacity-40 disabled:shadow-none',
                canGenerate
                  ? 'hover:bg-primary-hover hover:-translate-y-px cursor-pointer'
                  : 'cursor-not-allowed',
              ].join(' ')}
            >
              <Sparkles size={16} aria-hidden="true" />
              <span>
                {batchSize > 1
                  ? t('compose.generateBatch', { count: batchSize })
                  : t('compose.generate')}
              </span>
            </button>
          </div>
        </aside>

        {/* ─── RIGHT: RESULT GALLERY ─── */}
        <main className="flex flex-1 flex-col overflow-hidden min-w-0">
          <ResultGallery onLoadMore={handleLoadMoreHistory} />
        </main>
      </div>
    </div>
  );
}
