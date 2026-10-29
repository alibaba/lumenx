import { create } from 'zustand';
import { effectivePlaygroundMode, isQwen20TextToImage, readPlaygroundParameters } from './playgroundImageContract';
import { resolveStoredPlaygroundGenerationRequest, type PlaygroundGenerateRequestBody, type StoredPlaygroundGenerationRequest } from '@/lib/playgroundGenerationRequest';

// ---------------------------------------------------------------------------
// Featured (best-of-batch) persistence — client-side localStorage only.
// Map of generationId -> the one outputId marked "featured" within that batch.
// ---------------------------------------------------------------------------

const FEATURED_LS_KEY = 'lumenx:playground:featured';

function loadFeatured(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  try {
    return JSON.parse(window.localStorage.getItem(FEATURED_LS_KEY) || '{}') as Record<string, string>;
  } catch {
    return {};
  }
}

function saveFeatured(map: Record<string, string>): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(FEATURED_LS_KEY, JSON.stringify(map));
  } catch {
    /* ignore quota / serialization errors */
  }
}

// ---------------------------------------------------------------------------
// Generation queue — client-side concurrency gate. Default concurrency is
// persisted to localStorage; queued ids use a simple module counter.
// ---------------------------------------------------------------------------

const CONCURRENCY_LS_KEY = 'lumenx:playground:concurrency';
const DEFAULT_CONCURRENCY = 3;

function loadConcurrency(): number {
  if (typeof window === 'undefined') return DEFAULT_CONCURRENCY;
  const raw = Number(window.localStorage.getItem(CONCURRENCY_LS_KEY));
  return Number.isFinite(raw) && raw >= 1 && raw <= 8 ? raw : DEFAULT_CONCURRENCY;
}

function saveConcurrency(n: number): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(CONCURRENCY_LS_KEY, String(n));
  } catch {
    /* ignore */
  }
}

let queueSeq = 0;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PlaygroundMode = 't2i' | 'i2i' | 't2v' | 'i2v' | 'r2v' | 'v2v';

export interface PlaygroundParameters {
  size?: string;
  resolution?: string;
  aspect_ratio?: string;
  quality?: string;
  shot_type?: string;
  duration?: number;
  seed?: number;
  prompt_extend?: boolean;
  watermark?: boolean;
  negative_prompt?: string;
  [key: string]: unknown;
}

export interface PlaygroundOutput {
  id: string;
  media_path: string;
  media_type: 'image' | 'video';
  thumbnail_path?: string;
  saved_to_library: boolean;
  attempt_id?: string;
}

export interface PlaygroundAttempt {
  attempt_id: string;
  ordinal: number;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'needs_confirmation' | 'cancelled';
  error?: string;
  provider_execution?: import('@/lib/api').ProviderExecutionRecord | null;
}

export interface PlaygroundGeneration {
  id: string;
  mode: PlaygroundMode;
  model_id: string;
  prompt: string;
  negative_prompt?: string;
  input_media: string[];
  parameters: PlaygroundParameters;
  batch_size: number;
  outputs: PlaygroundOutput[];
  attempts?: PlaygroundAttempt[];
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'needs_confirmation' | 'cancelled';
  error?: string;
  created_at: string;
}

export interface PlaygroundTemplate {
  id: string;
  name: string;
  category: string;
  prompt: string;
  negative_prompt?: string | null;
  default_mode?: PlaygroundMode | null;
  default_model_id?: string | null;
  default_parameters?: PlaygroundParameters | null;
  created_at: string;
  updated_at: string;
}

export interface QueuedRequest {
  id: string;
  clientRequestId: string;
  requestFingerprint: string;
  body: PlaygroundGenerateRequestBody;
  journal: StoredPlaygroundGenerationRequest;
  status: 'pending' | 'dispatching' | 'needs_readback';
  enqueuedAt: number;
  readbackRequestedAt?: number;
  error?: string;
}

// ---------------------------------------------------------------------------
// State & Actions
// ---------------------------------------------------------------------------

interface PlaygroundState {
  // Current input
  mode: PlaygroundMode;
  modelId: string;
  prompt: string;
  negativePrompt: string;
  negativePromptProvided: boolean;
  inputMedia: string[];
  parameters: PlaygroundParameters;
  batchSize: number;

  // Model preferences (mode -> last used modelId)
  modelPreferences: Partial<Record<PlaygroundMode, string>>;

  // History
  history: PlaygroundGeneration[];
  historyPageSize: number;
  historyNextOffset: number;
  historyHasMore: boolean;
  isHistoryLoading: boolean;
  historyError?: string;
  deletedGenerationIds: string[];

  // Templates
  templates: PlaygroundTemplate[];

  // UI
  isGenerating: boolean;
  activeGenerationIds: string[];
  showAdvancedParams: boolean;
  showTemplateModal: boolean;
  showHistoryDrawer: boolean;

  // Template favorites (local, not persisted to backend)
  favoriteTemplateIds: string[];
  toggleTemplateFavorite: (id: string) => void;
  isTemplateFavorited: (id: string) => boolean;

  // Featured output per generation (best-of-batch); one per batch, localStorage-persisted
  featuredByGen: Record<string, string>;
  toggleFeatured: (genId: string, outputId: string) => void;
  isFeatured: (genId: string, outputId: string) => boolean;

  // Generation queue (client-side concurrency gate)
  queue: QueuedRequest[];
  maxConcurrent: number;
  enqueueRequest: (req: Omit<QueuedRequest, 'id' | 'status' | 'enqueuedAt'> & { status?: QueuedRequest['status']; error?: string }) => void;
  markDispatching: (id: string) => void;
  removeFromQueue: (id: string) => void;
  cancelPendingRequest: (id: string) => void;
  markNeedsReadback: (id: string, error?: string) => void;
  requestReadback: (id: string) => void;
  clearRuntimeState: () => void;
  setMaxConcurrent: (n: number) => void;

  // Actions — input setters
  setMode: (mode: PlaygroundMode) => void;
  setModelId: (modelId: string) => void;
  setPrompt: (prompt: string) => void;
  setNegativePrompt: (neg: string) => void;
  setInputMedia: (media: string[]) => void;
  /** Push a generated result back into the compose panel as reference input,
   *  switching to the appropriate mode. Image → i2i (default) or i2v when an
   *  explicit targetMode is given; video → v2v. Respects per-mode model
   *  preference (same behavior as setMode). */
  useResultAsReference: (
    mediaPath: string,
    mediaType: 'image' | 'video',
    targetMode?: PlaygroundMode,
  ) => void;
  setParameters: (params: PlaygroundParameters) => void;
  setBatchSize: (size: number) => void;
  setShowAdvancedParams: (show: boolean) => void;
  setShowTemplateModal: (show: boolean) => void;
  setShowHistoryDrawer: (show: boolean) => void;

  // Actions — generation lifecycle
  startGeneration: (gen: PlaygroundGeneration) => void;
  updateGeneration: (gen: PlaygroundGeneration) => void;
  patchGenerationOutput: (generationId: string, outputId: string, patch: Partial<PlaygroundOutput>) => void;
  removeGeneration: (id: string) => void;

  // Actions — history
  setHistory: (history: PlaygroundGeneration[]) => void;
  mergeHistoryPage: (history: PlaygroundGeneration[], options: { offset: number; limit: number; reset?: boolean }) => void;
  setHistoryLoading: (isHistoryLoading: boolean) => void;
  setHistoryError: (historyError?: string) => void;
  appendToHistory: (gen: PlaygroundGeneration) => void;

  // Actions — templates
  setTemplates: (templates: PlaygroundTemplate[]) => void;
  addTemplate: (template: PlaygroundTemplate) => void;
  updateTemplate: (template: PlaygroundTemplate) => void;
  removeTemplate: (id: string) => void;
  applyTemplate: (template: PlaygroundTemplate) => void;

  // Actions — reset
  resetInput: () => void;
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_MODE: PlaygroundMode = 't2i';
const DEFAULT_MODEL_ID = '';
const DEFAULT_PROMPT = '';
const DEFAULT_BATCH_SIZE = 1;
const DEFAULT_HISTORY_PAGE_SIZE = 50;

const TERMINAL_GENERATION_STATUSES = new Set<PlaygroundGeneration['status']>([
  'completed',
  'failed',
  'needs_confirmation',
  'cancelled',
]);

function isTerminalGenerationStatus(status: PlaygroundGeneration['status']): boolean {
  return TERMINAL_GENERATION_STATUSES.has(status);
}

function timestampOf(gen: PlaygroundGeneration): number {
  const value = new Date(gen.created_at).getTime();
  return Number.isFinite(value) ? value : 0;
}

function sortHistoryItems(items: PlaygroundGeneration[]): PlaygroundGeneration[] {
  return [...items].sort((a, b) => timestampOf(b) - timestampOf(a));
}

function mergeGenerationForHistory(
  existing: PlaygroundGeneration | undefined,
  incoming: PlaygroundGeneration,
  activeGenerationIds: string[],
): PlaygroundGeneration {
  if (!existing) return incoming;
  if (isTerminalGenerationStatus(existing.status) && !isTerminalGenerationStatus(incoming.status)) {
    return existing;
  }
  if (activeGenerationIds.includes(existing.id) && !isTerminalGenerationStatus(incoming.status)) {
    return existing;
  }

  const existingOutputsById = new Map(existing.outputs.map((output) => [output.id, output]));
  const incomingOutputs = incoming.outputs.length > 0 ? incoming.outputs : existing.outputs;
  const outputs = incomingOutputs.map((output) => {
    const previous = existingOutputsById.get(output.id);
    return previous?.saved_to_library ? { ...output, saved_to_library: true } : output;
  });

  return {
    ...incoming,
    outputs,
    attempts: incoming.attempts ?? existing.attempts,
    error: incoming.error ?? existing.error,
  };
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const usePlaygroundStore = create<PlaygroundState>((set, get) => ({
  // -- Current input --------------------------------------------------------
  mode: DEFAULT_MODE,
  modelId: DEFAULT_MODEL_ID,
  prompt: DEFAULT_PROMPT,
  negativePrompt: '',
  negativePromptProvided: false,
  inputMedia: [],
  parameters: {},
  batchSize: DEFAULT_BATCH_SIZE,

  // -- Model preferences ----------------------------------------------------
  modelPreferences: {},

  // -- History ---------------------------------------------------------------
  history: [],
  historyPageSize: DEFAULT_HISTORY_PAGE_SIZE,
  historyNextOffset: 0,
  historyHasMore: false,
  isHistoryLoading: false,
  historyError: undefined,
  deletedGenerationIds: [],

  // -- Templates -------------------------------------------------------------
  templates: [],

  // -- UI --------------------------------------------------------------------
  isGenerating: false,
  activeGenerationIds: [],
  showAdvancedParams: false,
  showTemplateModal: false,
  showHistoryDrawer: false,

  // -- Template favorites ----------------------------------------------------
  favoriteTemplateIds: [],
  toggleTemplateFavorite: (id) => {
    const { favoriteTemplateIds } = get();
    if (favoriteTemplateIds.includes(id)) {
      set({ favoriteTemplateIds: favoriteTemplateIds.filter((fid) => fid !== id) });
    } else {
      set({ favoriteTemplateIds: [...favoriteTemplateIds, id] });
    }
  },
  isTemplateFavorited: (id) => get().favoriteTemplateIds.includes(id),

  // -- Featured output (best-of-batch, one per generation) -------------------
  featuredByGen: loadFeatured(),
  toggleFeatured: (genId, outputId) => {
    const next = { ...get().featuredByGen };
    if (next[genId] === outputId) delete next[genId];
    else next[genId] = outputId;
    saveFeatured(next);
    set({ featuredByGen: next });
  },
  isFeatured: (genId, outputId) => get().featuredByGen[genId] === outputId,

  // -- Generation queue (client-side concurrency gate) -----------------------
  queue: [],
  maxConcurrent: loadConcurrency(),
  enqueueRequest: (req) =>
    set((s) => ({
      queue: s.queue.some((item) => item.clientRequestId === req.clientRequestId)
        ? s.queue
        : [
            ...s.queue,
            { ...req, id: `q${++queueSeq}`, status: req.status ?? 'pending' as const, enqueuedAt: Date.now() },
          ],
    })),
  markDispatching: (id) =>
    set((s) => ({
      queue: s.queue.map((q) => (q.id === id && q.status === 'pending' ? { ...q, status: 'dispatching' as const } : q)),
    })),
  removeFromQueue: (id) => set((s) => ({ queue: s.queue.filter((q) => q.id !== id) })),
  cancelPendingRequest: (id) =>
    set((s) => {
      const target = s.queue.find((q) => q.id === id);
      if (!target || target.status !== 'pending') return {};
      resolveStoredPlaygroundGenerationRequest(target.journal);
      return { queue: s.queue.filter((q) => q.id !== id) };
    }),
  markNeedsReadback: (id, error) =>
    set((s) => ({
      queue: s.queue.map((q) => (q.id === id ? { ...q, status: 'needs_readback' as const, error } : q)),
    })),
  requestReadback: (id) =>
    set((s) => ({
      queue: s.queue.map((q) => (
        q.id === id && q.status === 'needs_readback'
          ? { ...q, readbackRequestedAt: Date.now(), error: undefined }
          : q
      )),
    })),
  clearRuntimeState: () => set({
    history: [],
    templates: [],
    activeGenerationIds: [],
    isGenerating: false,
    queue: [],
    historyNextOffset: 0,
    historyHasMore: false,
    isHistoryLoading: false,
    historyError: undefined,
    deletedGenerationIds: [],
    showTemplateModal: false,
    showHistoryDrawer: false,
  }),
  setMaxConcurrent: (n) => {
    const clamped = Math.max(1, Math.min(8, Math.round(n)));
    saveConcurrency(clamped);
    set({ maxConcurrent: clamped });
  },

  // =========================================================================
  // Actions
  // =========================================================================

  // -- Input setters ---------------------------------------------------------

  setMode: (mode) => {
    const { modelPreferences } = get();
    const preferredModel = modelPreferences[mode];
    set({
      mode,
      ...(preferredModel !== undefined ? { modelId: preferredModel } : {}),
    });
  },

  setModelId: (modelId) => {
    const { mode, modelPreferences } = get();
    set({
      modelId,
      modelPreferences: { ...modelPreferences, [mode]: modelId },
    });
  },

  setPrompt: (prompt) => set({ prompt }),

  setNegativePrompt: (negativePrompt) => set({ negativePrompt, negativePromptProvided: true }),

  setInputMedia: (inputMedia) => set({ inputMedia }),

  useResultAsReference: (mediaPath, mediaType, targetMode) => {
    const { modelPreferences } = get();
    const mode: PlaygroundMode =
      targetMode ?? (mediaType === 'video' ? 'v2v' : 'i2i');
    const preferredModel = modelPreferences[mode];
    set({
      mode,
      inputMedia: [mediaPath],
      ...(preferredModel !== undefined ? { modelId: preferredModel } : {}),
    });
  },

  setParameters: (parameters) => set({ parameters }),

  setBatchSize: (batchSize) => set({ batchSize }),

  setShowAdvancedParams: (showAdvancedParams) => set({ showAdvancedParams }),

  setShowTemplateModal: (showTemplateModal) =>
    set(showTemplateModal ? { showTemplateModal, showHistoryDrawer: false } : { showTemplateModal }),

  setShowHistoryDrawer: (showHistoryDrawer) =>
    set(showHistoryDrawer ? { showHistoryDrawer, showTemplateModal: false } : { showHistoryDrawer }),

  // -- Generation lifecycle --------------------------------------------------

  startGeneration: (gen) => {
    const { activeGenerationIds, history, deletedGenerationIds } = get();
    if (deletedGenerationIds.includes(gen.id)) return;
    const nextActive = isTerminalGenerationStatus(gen.status) || activeGenerationIds.includes(gen.id)
      ? activeGenerationIds
      : [...activeGenerationIds, gen.id];
    const existing = history.find((item) => item.id === gen.id);
    const nextHistory = existing
      ? history.map((item) => (item.id === gen.id ? mergeGenerationForHistory(item, gen, nextActive) : item))
      : [gen, ...history];
    set({
      activeGenerationIds: nextActive,
      history: sortHistoryItems(nextHistory),
      isGenerating: nextActive.length > 0,
    });
  },

  updateGeneration: (gen) => {
    const { history, activeGenerationIds, deletedGenerationIds } = get();
    if (deletedGenerationIds.includes(gen.id)) return;
    const isTerminal = isTerminalGenerationStatus(gen.status);
    const updatedActive = isTerminal
      ? activeGenerationIds.filter((id) => id !== gen.id)
      : (activeGenerationIds.includes(gen.id) ? activeGenerationIds : [...activeGenerationIds, gen.id]);
    const exists = history.some((h) => h.id === gen.id);
    const updatedHistory = exists
      ? history.map((h) => (h.id === gen.id ? mergeGenerationForHistory(h, gen, updatedActive) : h))
      : [gen, ...history];

    set({
      history: sortHistoryItems(updatedHistory),
      activeGenerationIds: updatedActive,
      isGenerating: updatedActive.length > 0,
    });
  },

  patchGenerationOutput: (generationId, outputId, patch) => {
    set((s) => ({
      history: s.history.map((gen) => (gen.id === generationId
        ? {
            ...gen,
            outputs: gen.outputs.map((output) => (
              output.id === outputId ? { ...output, ...patch } : output
            )),
          }
        : gen)),
    }));
  },

  removeGeneration: (id) => {
    const { history, activeGenerationIds, deletedGenerationIds } = get();
    const updatedActive = activeGenerationIds.filter((gid) => gid !== id);
    set({
      history: history.filter((h) => h.id !== id),
      activeGenerationIds: updatedActive,
      isGenerating: updatedActive.length > 0,
      deletedGenerationIds: deletedGenerationIds.includes(id) ? deletedGenerationIds : [...deletedGenerationIds, id],
    });
  },

  // -- History ---------------------------------------------------------------

  setHistory: (history) => set({ history: sortHistoryItems(history) }),

  mergeHistoryPage: (history, options) => set((s) => {
    const deleted = new Set(s.deletedGenerationIds);
    const merged = new Map<string, PlaygroundGeneration>();
    for (const item of s.history) {
      if (!deleted.has(item.id)) merged.set(item.id, item);
    }
    for (const item of history) {
      if (deleted.has(item.id)) continue;
      merged.set(item.id, mergeGenerationForHistory(merged.get(item.id), item, s.activeGenerationIds));
    }
    return {
      history: sortHistoryItems(Array.from(merged.values())),
      historyNextOffset: options.offset + history.length,
      historyHasMore: history.length >= options.limit,
      historyError: undefined,
    };
  }),

  setHistoryLoading: (isHistoryLoading) => set({ isHistoryLoading }),

  setHistoryError: (historyError) => set({ historyError }),

  appendToHistory: (gen) => set((s) => ({ history: sortHistoryItems([gen, ...s.history]) })),

  // -- Templates -------------------------------------------------------------

  setTemplates: (templates) => set({ templates }),

  addTemplate: (template) =>
    set((s) => ({ templates: [...s.templates, template] })),

  updateTemplate: (template) =>
    set((s) => ({
      templates: s.templates.map((t) => (t.id === template.id ? template : t)),
    })),

  removeTemplate: (id) =>
    set((s) => ({ templates: s.templates.filter((t) => t.id !== id) })),

  applyTemplate: (template) => {
    const patch: Partial<PlaygroundState> = {
      prompt: template.prompt,
    };
    patch.negativePrompt = template.negative_prompt ?? '';
    patch.negativePromptProvided = typeof template.negative_prompt === 'string';
    if (template.default_mode != null) {
      patch.mode = template.default_mode;
    }
    if (template.default_model_id != null) {
      patch.modelId = template.default_model_id;
    }
    const current = get();
    const modelId = patch.modelId ?? current.modelId;
    const mode = patch.mode ?? current.mode;
    patch.parameters = readPlaygroundParameters(
      template.default_parameters,
      modelId,
      isQwen20TextToImage(modelId, mode, []) ? mode : effectivePlaygroundMode(mode, current.inputMedia),
    );
    set(patch);
  },

  // -- Reset -----------------------------------------------------------------

  resetInput: () =>
    set({
      prompt: DEFAULT_PROMPT,
      negativePrompt: '',
      negativePromptProvided: false,
      inputMedia: [],
      parameters: {},
      batchSize: DEFAULT_BATCH_SIZE,
    }),
}));
