// @vitest-environment happy-dom
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PlaygroundPage from '@/components/modules/playground/PlaygroundPage';
import { usePlaygroundStore, type PlaygroundGeneration, type PlaygroundParameters } from '@/components/modules/playground/usePlaygroundStore';
import { resolvePlaygroundCatalogModelId } from '@/components/modules/playground/playgroundModelAliases';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginPlaygroundGenerationRequest, listPlaygroundGenerationRequests, type PlaygroundGenerateRequestBody } from '@/lib/playgroundGenerationRequest';
import type { PlaygroundRequestReceiptResponse } from '@/lib/api';
import en from '../../messages/en.json';
import zh from '../../messages/zh.json';
import capturedWire from './fixtures/playground-qwen20-wire.json';
import legacyWire from './fixtures/playground-qwen20-legacy-wire.json';

const api = vi.hoisted(() => ({ generate: vi.fn(), getHistory: vi.fn(), getTemplates: vi.fn(), getRequestReceipt: vi.fn(), getGeneration: vi.fn(), getCapabilities: vi.fn(), createTemplate: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'), playgroundApi: api }));
// All product components and locale providers are real. Only the API boundary is replaced.
const modelIds = ['qwen-image-2.0', 'qwen-image-2.0-pro', 'qwen/qwen-image-2.0#image', 'qwen/qwen-image-2.0-pro#image'];
const explicitParameters = { size: '768*1024', seed: 0, watermark: false, prompt_extend: false };
const storageA = '11111111-1111-4111-8111-111111111111';
const storageB = '22222222-2222-4222-8222-222222222222';
const wire = capturedWire as unknown as Record<string, { request: PlaygroundGenerateRequestBody; accepted: PlaygroundRequestReceiptResponse; completed: PlaygroundRequestReceiptResponse }>;
function journals() { return listPlaygroundGenerationRequests({ environment: getRuntimeEnvironment()! }); }
function renderPage(locale: 'en' | 'zh' = 'en') { return render(<NextIntlClientProvider locale={locale} messages={locale === 'en' ? en : zh} timeZone="UTC"><PlaygroundPage /></NextIntlClientProvider>); }
function generateButton() { return screen.getByRole('button', { name: /^Generate(?:\s|$)|^生成(?:\s|$)/ }); }
function openNegative() { fireEvent.click(screen.getByText(en.playground.prompt.negativeLabel)); return screen.getByPlaceholderText(en.playground.prompt.negativePlaceholder); }
function correction() { return screen.getByRole('button', { name: en.playground.imageContract.correct }); }
function history(overrides: Partial<PlaygroundGeneration> = {}): PlaygroundGeneration {
  return { id: 'old-qwen', mode: 't2i', model_id: modelIds[0], prompt: 'A glass moon', input_media: [], parameters: {}, batch_size: 2, outputs: [], status: 'failed', error: 'Retained historical attempt', created_at: '2026-10-20T00:00:00Z', ...overrides };
}
function receiptFor(body: PlaygroundGenerateRequestBody, explicit = false): PlaygroundRequestReceiptResponse {
  const source = wire[body.model_id + (explicit ? ':explicit' : ':default')];
  expect({ ...body, client_request_id: source.request.client_request_id, input_media: body.input_media?.length ? body.input_media : undefined }).toEqual({ ...source.request, input_media: undefined });
  const receipt = structuredClone(source.completed);
  receipt.generation!.client_request_id = body.client_request_id;
  // Only the test-generated journal id is rebound; frozen provider intent/proof stays verbatim.
  return receipt;
}
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA });
  usePlaygroundStore.getState().resetInput();
  usePlaygroundStore.setState({ mode: 't2i', modelId: modelIds[0], prompt: 'A glass moon', inputMedia: [], parameters: {}, batchSize: 2, modelPreferences: {}, history: [], templates: [], queue: [], activeGenerationIds: [], deletedGenerationIds: [], isGenerating: false, isHistoryLoading: false, historyError: undefined, historyHasMore: false, maxConcurrent: 3, showTemplateModal: false, showHistoryDrawer: false });
  api.getHistory.mockResolvedValue([]); api.getTemplates.mockResolvedValue([]);
  api.generate.mockImplementation(() => new Promise(() => {}));
});
afterEach(() => { clearRuntimeEnvironment(); localStorage.clear(); });

describe('real Qwen composer', () => {
  it.each(modelIds)('retains the physical model and implicit defaults for %s', async (modelId) => {
    usePlaygroundStore.setState({ modelId }); renderPage();
    expect(usePlaygroundStore.getState().modelId).toBe(modelId.split('/').at(-1)!.split('#')[0]);
    expect(screen.getByRole('button', { name: /Qwen.*Image.*2\.0/i })).toBeTruthy();
    expect(usePlaygroundStore.getState().parameters).toEqual({});
    fireEvent.click(generateButton()); await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).toMatchObject({ model_id: resolvePlaygroundCatalogModelId(modelId), batch_size: 2 });
    expect(api.generate.mock.calls[0][0]).not.toHaveProperty('negative_prompt');
    expect(api.generate.mock.calls[0][0]).not.toHaveProperty('parameters');
  });

  it.each(['en', 'zh'] as const)('shows Qwen-supported controls with real %s locale', (locale) => {
    renderPage(locale); const messages = locale === 'en' ? en : zh;
    expect(screen.getByText(messages.playground.imageContract.qwenSupported)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: messages.playground.parameters.advanced }));
    expect(screen.getByText(messages.playground.parameters.promptExtend)).toBeTruthy();
    expect(screen.getByRole('spinbutton', { name: 'Seed' })).toHaveAttribute('min', '0');
    expect(screen.getByRole('spinbutton', { name: 'Seed' })).toHaveAttribute('max', '2147483647');
    expect(usePlaygroundStore.getState().parameters).toEqual({});
  });

  it('submits zero, explicit false and an edited empty negative without inserting defaults', async () => {
    renderPage(); fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Seed' }), { target: { value: '0' } });
    const promptToggle = screen.getByText(en.playground.parameters.promptExtend).parentElement!;
    fireEvent.click(within(promptToggle).getByRole('button', { name: 'OFF' }));
    const watermarkToggle = screen.getByText(en.playground.parameters.watermark).parentElement!;
    fireEvent.click(within(watermarkToggle).getByRole('button', { name: 'OFF' }));
    const negative = openNegative(); fireEvent.change(negative, { target: { value: 'avoid blur' } }); fireEvent.change(negative, { target: { value: '' } });
    fireEvent.click(generateButton()); await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).toMatchObject({ negative_prompt: '', parameters: { seed: 0, watermark: false, prompt_extend: false } });
  });

  it('preserves a fractional seed as invalid until edited and omits a cleared seed', async () => {
    renderPage(); fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
    const seed = screen.getByRole('spinbutton', { name: 'Seed' });
    fireEvent.change(seed, { target: { value: '0.5' } });
    expect(usePlaygroundStore.getState().parameters.seed).toBe(0.5); expect(generateButton()).toBeDisabled();
    fireEvent.change(seed, { target: { value: '' } }); fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce()); expect(api.generate.mock.calls[0][0]).not.toHaveProperty('parameters');
  });

  it.each([
    { size: 'old-size' }, { size: null }, { size: { width: 1024 } }, { seed: -1 }, { seed: 0.5 }, { seed: 2147483648 }, { seed: null },
    { watermark: 0 }, { prompt_extend: null }, { quality: 'high' }, { n: 1 }, { custom: { retained: true } }, JSON.parse('{"__proto__":false}'),
  ])('keeps invalid raw parameters %j and blocks before journal creation', (parameters) => {
    usePlaygroundStore.setState({ parameters: parameters as PlaygroundParameters }); renderPage();
    expect(usePlaygroundStore.getState().parameters).toEqual(parameters); expect(generateButton()).toBeDisabled();
    fireEvent.click(generateButton()); expect(journals()).toEqual([]); expect(api.generate).not.toHaveBeenCalled();
    fireEvent.click(correction()); expect(usePlaygroundStore.getState().parameters).toEqual({});
    expect(journals()).toEqual([]); expect(api.generate).not.toHaveBeenCalled();
  });

  it('preserves overlong negative text and the author prompt through generic correction', () => {
    usePlaygroundStore.getState().setNegativePrompt('🌙'.repeat(501));
    usePlaygroundStore.setState({ parameters: { seed: -1 }, prompt: 'original text' }); renderPage();
    expect(generateButton()).toBeDisabled(); fireEvent.click(correction());
    expect(usePlaygroundStore.getState().negativePrompt).toBe('🌙'.repeat(501)); expect(usePlaygroundStore.getState().prompt).toBe('original text');
    expect(screen.getAllByText(en.playground.imageContract.qwenNegativeLimit).length).toBeGreaterThan(0); expect(generateButton()).toBeDisabled(); expect(journals()).toEqual([]);
  });

  it.each(['parameter', 'visible'])('requires explicit %s negative choice, preserving both drafts until chosen', (choice) => {
    usePlaygroundStore.getState().setNegativePrompt('visible text');
    usePlaygroundStore.setState({ parameters: { negative_prompt: 'parameter text', seed: -1, watermark: false } }); renderPage();
    expect(generateButton()).toBeDisabled(); expect(within(screen.getByRole('alert')).getByText(/parameter text/)).toBeTruthy(); expect(within(screen.getByRole('alert')).getByText(/visible text/)).toBeTruthy();
    fireEvent.click(correction()); expect(usePlaygroundStore.getState().parameters).toEqual({ negative_prompt: 'parameter text', watermark: false });
    expect(usePlaygroundStore.getState().negativePrompt).toBe('visible text'); expect(generateButton()).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: choice === 'parameter' ? en.playground.imageContract.qwenUseParameterNegative : en.playground.imageContract.qwenUseVisibleNegative }));
    expect(usePlaygroundStore.getState().negativePrompt).toBe(choice === 'parameter' ? 'parameter text' : 'visible text');
    expect(usePlaygroundStore.getState().parameters).toEqual({ watermark: false }); expect(generateButton()).toBeEnabled(); expect(journals()).toEqual([]); expect(api.generate).not.toHaveBeenCalled();
  });

  it('keeps legal negative and prompt extension when switching Qwen models', () => {
    usePlaygroundStore.getState().setNegativePrompt('avoid blur'); usePlaygroundStore.setState({ parameters: { prompt_extend: false, size: 'old-size' } }); renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Qwen.*Image.*2\.0/i }));
    fireEvent.click(screen.getByRole('button', { name: /Qwen.*Image.*2\.0 Pro/i }));
    expect(usePlaygroundStore.getState()).toMatchObject({ modelId: 'qwen-image-2.0-pro', negativePrompt: 'avoid blur', parameters: { prompt_extend: false, size: 'old-size' } });
    expect(generateButton()).toBeDisabled(); expect(api.generate).not.toHaveBeenCalled();
  });

  it('reset clears negative presence before the next explicit generation', async () => {
    renderPage(); const negative = openNegative(); fireEvent.change(negative, { target: { value: 'avoid blur' } }); fireEvent.change(negative, { target: { value: '' } });
    act(() => usePlaygroundStore.getState().resetInput());
    fireEvent.change(screen.getByPlaceholderText(en.playground.prompt.placeholder), { target: { value: 'A glass moon' } });
    fireEvent.click(generateButton()); await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).not.toHaveProperty('negative_prompt');
  });

  it.each([null, { legacy: true }, false])('diagnoses nested negative %j without silently discarding it', negative => {
    usePlaygroundStore.setState({ parameters: { negative_prompt: negative } as unknown as PlaygroundParameters }); renderPage();
    expect(generateButton()).toBeDisabled(); expect(screen.getByText(new RegExp(JSON.stringify(negative).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))).toBeTruthy();
    expect(screen.queryByRole('button', { name: en.playground.imageContract.qwenUseParameterNegative })).toBeNull();
    expect(screen.queryByRole('button', { name: en.playground.imageContract.correct })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: en.playground.imageContract.qwenUseVisibleNegative }));
    expect(usePlaygroundStore.getState().parameters).toEqual({}); expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
  });

  it('moves even an empty saved negative explicitly and preserves it in the next request', async () => {
    usePlaygroundStore.setState({ parameters: { negative_prompt: '' } }); renderPage();
    fireEvent.click(screen.getByRole('button', { name: en.playground.imageContract.qwenUseParameterNegative }));
    expect(api.generate).not.toHaveBeenCalled(); fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce()); expect(api.generate.mock.calls[0][0]).toHaveProperty('negative_prompt', ''); expect(api.generate.mock.calls[0][0]).not.toHaveProperty('parameters');
  });

  it('opens the visible negative editor after choosing a retained parameter value', () => {
    usePlaygroundStore.setState({ parameters: { negative_prompt: 'saved author text' } }); renderPage();
    expect(screen.queryByPlaceholderText(en.playground.prompt.negativePlaceholder)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: en.playground.imageContract.qwenUseParameterNegative }));
    expect(screen.getByPlaceholderText(en.playground.prompt.negativePlaceholder)).toHaveValue('saved author text');
    expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
  });
});

describe('Qwen history, templates and request ownership', () => {
  it('returns invalid history to editing as a copy, with the original qualified identity intact', async () => {
    const original = history({ model_id: modelIds[3], negative_prompt: '', parameters: { size: null, seed: 0, prompt_extend: null, custom: { original: true } } as unknown as PlaygroundParameters });
    api.getHistory.mockResolvedValue([original]); renderPage(); fireEvent.click(await screen.findByRole('button', { name: /Retry/ }));
    expect(usePlaygroundStore.getState()).toMatchObject({ modelId: 'qwen-image-2.0-pro', parameters: original.parameters, negativePrompt: '' });
    expect(usePlaygroundStore.getState().history[0]).toEqual(original); expect(journals()).toEqual([]); expect(api.generate).not.toHaveBeenCalled();
    fireEvent.click(correction()); expect(usePlaygroundStore.getState().parameters).toEqual({ seed: 0 }); expect(usePlaygroundStore.getState().history[0]).toEqual(original);
    fireEvent.click(generateButton()); await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).toHaveProperty('negative_prompt', '');
  });

  it('returns historical T2I references to editing before the user explicitly chooses I2I generation', async () => {
    const original = history({ input_media: ['uploads/old.png'], parameters: { seed: -1, prompt_extend: false } });
    api.getHistory.mockResolvedValue([original]); renderPage(); fireEvent.click(await screen.findByRole('button', { name: /Retry/ }));
    expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]); expect(screen.getByText(en.playground.imageContract.retryReferenceEdit)).toBeTruthy();
    expect(usePlaygroundStore.getState().history[0]).toEqual(original); fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce()); expect(api.generate.mock.calls[0][0]).toMatchObject({ mode: 'i2i', input_media: original.input_media, parameters: original.parameters });
  });

  it.each([undefined, ''] as const)('preserves template negative presence %j when saving through the real modal', async (negative) => {
    if (negative !== undefined) usePlaygroundStore.getState().setNegativePrompt(negative);
    api.createTemplate.mockImplementation(async (body) => ({ ...body, id: 'saved', created_at: 'now', updated_at: 'now' }));
    renderPage(); fireEvent.click(screen.getByRole('button', { name: en.playground.prompt.templates }));
    fireEvent.change(screen.getByPlaceholderText(en.playground.template.namePlaceholder), { target: { value: 'My moon' } });
    fireEvent.click(screen.getByRole('button', { name: en.playground.template.save }));
    await waitFor(() => expect(api.createTemplate).toHaveBeenCalledOnce()); expect(api.createTemplate.mock.calls[0][0].negative_prompt).toBe(negative);
    expect(api.generate).not.toHaveBeenCalled();
  });

  it('loads raw template conflicts through real apply controls without losing malformed values', async () => {
    const template = { id: 'saved', name: 'Legacy moon', category: 'image', prompt: 'A glass moon', negative_prompt: 'visible old', default_model_id: modelIds[2], default_mode: 't2i', default_parameters: { negative_prompt: 'nested old', size: null, custom: { retained: true } }, created_at: 'now', updated_at: 'now' };
    usePlaygroundStore.setState({ prompt: '' }); api.getTemplates.mockResolvedValue([template]); renderPage();
    await waitFor(() => expect(usePlaygroundStore.getState().templates).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: en.playground.prompt.templates }));
    fireEvent.click(screen.getByRole('button', { name: en.playground.template.apply }));
    expect(usePlaygroundStore.getState()).toMatchObject({ modelId: 'qwen-image-2.0', negativePrompt: 'visible old', parameters: template.default_parameters });
    expect(generateButton()).toBeDisabled(); expect(journals()).toEqual([]); expect(api.generate).not.toHaveBeenCalled();
  });

  it.each([null, { width: 1024 }, 'old-size'])('retains declared T2I template raw size %j even when the editor already has a reference', async size => {
    const template = { id: 'ref-template', name: 'Reference moon', category: 'image', prompt: 'A glass moon', default_model_id: modelIds[3], default_mode: 't2i', default_parameters: { size, custom: { kept: true }, prompt_extend: false }, created_at: 'now', updated_at: 'now' };
    usePlaygroundStore.setState({ prompt: '', inputMedia: ['uploads/reference.png'] }); api.getTemplates.mockResolvedValue([template]); renderPage();
    await waitFor(() => expect(usePlaygroundStore.getState().templates).toHaveLength(1)); fireEvent.click(screen.getByRole('button', { name: en.playground.prompt.templates }));
    fireEvent.click(screen.getByRole('button', { name: en.playground.template.apply }));
    expect(usePlaygroundStore.getState().parameters).toEqual(template.default_parameters); expect(api.generate).not.toHaveBeenCalled();
    act(() => usePlaygroundStore.getState().setInputMedia([])); expect(generateButton()).toBeDisabled();
  });

  it('retries valid old explicit empty negative under a new identity without editing the old receipt', async () => {
    const original = history({ model_id: modelIds[2], negative_prompt: '', parameters: explicitParameters });
    api.getHistory.mockResolvedValue([original]); renderPage(); fireEvent.click(await screen.findByRole('button', { name: /Retry/ }));
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).toMatchObject({ model_id: modelIds[2], negative_prompt: '', parameters: explicitParameters });
    expect(usePlaygroundStore.getState().history[0]).toEqual(original);
  });

  it('reads the frozen pre-migration untagged Qwen receipt without applying the new draft gate', async () => {
    const source = legacyWire.receipt;
    const old = source.generation;
    beginPlaygroundGenerationRequest({ environment: getRuntimeEnvironment()!, modelId: old.model_id, mode: 't2i', prompt: old.prompt, negativePrompt: old.negative_prompt ?? undefined, parameters: old.parameters, batchSize: old.batch_size, idFactory: () => old.client_request_id });
    api.getRequestReceipt.mockResolvedValue(source); renderPage();
    await waitFor(() => expect(usePlaygroundStore.getState().history[0]?.id).toBe(old.id));
    expect(api.generate).not.toHaveBeenCalled(); expect(api.getRequestReceipt).toHaveBeenCalledOnce();
    expect(usePlaygroundStore.getState().history[0].parameters).toEqual(old.parameters);
    expect(usePlaygroundStore.getState().history[0].attempts?.[0].provider_execution).toEqual(old.attempts[0].provider_execution);
    expect(old.attempts[0].provider_execution.intent).not.toHaveProperty('intent_contract_version');
  });

  it.each(modelIds.flatMap(modelId => [false, true].map(explicit => ({ modelId, explicit }))))('accepts actual strict backend batch2 wire ($modelId explicit=$explicit)', async ({ modelId, explicit }) => {
    usePlaygroundStore.setState({ modelId });
    if (explicit) { usePlaygroundStore.setState({ parameters: explicitParameters }); usePlaygroundStore.getState().setNegativePrompt(''); }
    api.generate.mockImplementation(async body => receiptFor(body, explicit)); renderPage(); fireEvent.click(generateButton());
    await waitFor(() => expect(usePlaygroundStore.getState().history[0]?.status).toBe('completed'));
    expect(usePlaygroundStore.getState().history[0].outputs).toHaveLength(2); expect(journals()).toEqual([]);
    expect(usePlaygroundStore.getState().history[0].attempts?.[0].provider_execution?.intent).toMatchObject({ intent_contract_version: 'provider-qwen20-t2i-bound-http-v1' });
  });

  it.each(['lost-ack', '503'])('recovers %s with GET only and leaves original qualified journal identity unchanged', async (reason) => {
    usePlaygroundStore.setState({ modelId: modelIds[3] });
    const source = wire[modelIds[3] + ':default'];
    const journal = beginPlaygroundGenerationRequest({ environment: getRuntimeEnvironment()!, modelId: modelIds[3], mode: 't2i', prompt: source.request.prompt, batchSize: 2, idFactory: () => source.request.client_request_id });
    api.getRequestReceipt.mockRejectedValueOnce(reason === '503' ? { response: { status: 503 } } : new Error('Lost ACK'));
    api.getRequestReceipt.mockResolvedValueOnce(source.completed);
    renderPage(); await waitFor(() => expect(api.getRequestReceipt).toHaveBeenCalledOnce());
    expect(journals()[0]).toEqual(journal); expect(usePlaygroundStore.getState().modelId).toBe('qwen-image-2.0-pro');
    fireEvent.click(screen.getByRole('button', { name: /^Queue/ }));
    fireEvent.click(await screen.findByRole('button', { name: en.playground.queue.refreshReadback }));
    await waitFor(() => expect(usePlaygroundStore.getState().history[0]?.status).toBe('completed'));
    expect(api.generate).not.toHaveBeenCalled(); expect(api.getRequestReceipt).toHaveBeenCalledTimes(2);
    expect(usePlaygroundStore.getState().history[0].model_id).toBe(modelIds[3]); expect(journals()).toEqual([]);
  });

  it.each(['lost-ack', '503'])('reads back an accepted first POST after %s without submitting again', async reason => {
    api.generate.mockRejectedValueOnce(reason === '503' ? { response: { status: 503 } } : new Error('Response lost after acceptance'));
    api.getRequestReceipt.mockImplementation(async id => {
      const body = api.generate.mock.calls[0][0] as PlaygroundGenerateRequestBody;
      expect(id).toBe(body.client_request_id); expect(journals()[0].request).toEqual(body);
      return receiptFor(body);
    });
    renderPage(); fireEvent.click(generateButton());
    await waitFor(() => expect(usePlaygroundStore.getState().history[0]?.status).toBe('completed'));
    expect(api.generate).toHaveBeenCalledOnce(); expect(api.getRequestReceipt).toHaveBeenCalledOnce(); expect(journals()).toEqual([]);
    expect(usePlaygroundStore.getState().history[0].attempts).toHaveLength(2);
  });

  it('does not project a late Qwen POST across environment A→B→A', async () => {
    let resolve!: (value: PlaygroundRequestReceiptResponse) => void;
    api.generate.mockImplementation(() => new Promise(r => { resolve = r; }));
    api.getRequestReceipt.mockImplementation(() => new Promise(() => {}));
    renderPage(); fireEvent.click(generateButton()); await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    const body = api.generate.mock.calls[0][0] as PlaygroundGenerateRequestBody;
    act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: storageB }); activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA }); });
    await act(async () => { resolve(receiptFor(body)); });
    expect(usePlaygroundStore.getState().history).toEqual([]); expect(journals()).toHaveLength(1); expect(api.generate).toHaveBeenCalledOnce();
  });
});
