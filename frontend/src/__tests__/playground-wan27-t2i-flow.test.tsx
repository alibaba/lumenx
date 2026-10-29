// @vitest-environment happy-dom
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginPlaygroundGenerationRequest, listPlaygroundGenerationRequests, type PlaygroundGenerateRequestBody } from '@/lib/playgroundGenerationRequest';
import { usePlaygroundStore, type PlaygroundGeneration, type PlaygroundParameters } from '@/components/modules/playground/usePlaygroundStore';
import PlaygroundPage from '@/components/modules/playground/PlaygroundPage';

const api = vi.hoisted(() => ({ generate: vi.fn(), getHistory: vi.fn(), getTemplates: vi.fn(), getRequestReceipt: vi.fn(), getGeneration: vi.fn(), getCapabilities: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'), playgroundApi: api }));
vi.mock('next-intl', () => ({ useTranslations: () => (key: string, values?: Record<string, unknown>) => values?.count !== undefined ? `${key}:${values.count}` : key }));
vi.mock('@/components/modules/playground/ModeSelector', () => ({ default: () => null }));
vi.mock('@/components/modules/playground/ModelSelector', () => ({ default: () => null }));
vi.mock('@/components/modules/playground/MediaInput', () => ({ default: () => null }));
vi.mock('@/components/modules/playground/PromptTemplateModal', () => ({ default: () => null }));
vi.mock('@/components/modules/playground/PromptHistoryDrawer', () => ({ default: () => null }));
vi.mock('@/components/modules/playground/ResultCard', () => ({ default: ({ generation, onRetry }: { generation: PlaygroundGeneration; onRetry: (gen: PlaygroundGeneration) => void }) => <button onClick={() => onRetry(generation)}>retry {generation.id}</button> }));
vi.mock('@/components/modules/playground/GalleryView', () => ({ default: () => null }));
vi.mock('@/components/modules/playground/DetailPanel', () => ({ default: () => null }));
vi.mock('@/components/modules/playground/QueuePanel', () => ({ default: () => null }));

const ids = ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'];
function generation(overrides: Partial<PlaygroundGeneration> = {}): PlaygroundGeneration {
  return { id: 'old-image', mode: 't2i', model_id: 'wan2.7-image', prompt: 'original image', negative_prompt: '', input_media: [], parameters: {}, batch_size: 2, outputs: [], status: 'completed', created_at: '2026-10-14T00:00:00Z', ...overrides };
}
function receipt(request: PlaygroundGenerateRequestBody) {
  return { schema_version: 1, replayed: false, request_status: 'accepted', tombstone: null, generation: { ...generation({ id: 'new-image', mode: request.mode, model_id: request.model_id, prompt: request.prompt, negative_prompt: request.negative_prompt, input_media: request.input_media ?? [], parameters: request.parameters as PlaygroundParameters, batch_size: request.batch_size ?? 1 }), client_request_id: request.client_request_id, request_contract_version: 'request-json-v1', request_fingerprint: 'a'.repeat(64) } };
}
function journals() { return listPlaygroundGenerationRequests({ environment: getRuntimeEnvironment()! }); }
function generateButton() { return screen.getByRole('button', { name: /compose.generate/ }); }
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
  usePlaygroundStore.setState({ mode: 't2i', modelId: ids[0], prompt: 'new image', negativePrompt: '', inputMedia: [], parameters: {}, batchSize: 1, history: [], templates: [], queue: [], activeGenerationIds: [], deletedGenerationIds: [], isGenerating: false, isHistoryLoading: false, historyError: undefined, historyHasMore: false, maxConcurrent: 3 });
  api.getHistory.mockResolvedValue([]); api.getTemplates.mockResolvedValue([]); api.generate.mockImplementation(async (body) => receipt(body));
});
afterEach(() => { clearRuntimeEnvironment(); localStorage.clear(); });

describe('Wan2.7 effective T2I controls and submission', () => {
  it.each(ids)('keeps explicit zero/false for %s and hides unsupported controls', async (modelId) => {
    usePlaygroundStore.setState({ modelId, parameters: { size: '768*1024', seed: 0, watermark: false } });
    render(<PlaygroundPage />);
    fireEvent.click(screen.getByRole('button', { name: 'parameters.advanced' }));
    expect(screen.queryByText('parameters.promptExtend')).toBeNull();
    expect(screen.getByRole('spinbutton')).toHaveValue(0);
    expect(screen.getByRole('spinbutton')).toHaveAttribute('min', '0');
    expect(screen.getByRole('spinbutton')).toHaveAttribute('max', '2147483647');
    fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).toMatchObject({ mode: 't2i', model_id: modelId, parameters: { size: '768*1024', seed: 0, watermark: false } });
    expect(api.generate.mock.calls[0][0]).not.toHaveProperty('negative_prompt');
  });

  it('keeps an untouched draft empty and omits whitespace-only negative', async () => {
    usePlaygroundStore.setState({ negativePrompt: '  \n ' });
    render(<PlaygroundPage />);
    expect(usePlaygroundStore.getState().parameters).toEqual({});
    fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).not.toHaveProperty('parameters');
    expect(api.generate.mock.calls[0][0]).not.toHaveProperty('negative_prompt');
    expect(usePlaygroundStore.getState().negativePrompt).toBe('  \n ');
  });

  it.each([
    { size: 'bad-size' }, { size: null }, { size: { width: 1024 } },
    { seed: -1 }, { seed: 0.5 }, { seed: 2147483648 }, { seed: null },
    { watermark: 'false' }, { watermark: null }, { prompt_extend: false },
    { prompt_extend: null }, { negative_prompt: '' }, { negative_prompt: null },
    { quality: 'high' }, { custom: { keep: true } }, { negativePrompt: 'unknown field' },
    JSON.parse('{"__proto__":false}'), JSON.parse('{"constructor":false}'), JSON.parse('{"toString":null}'),
  ])('blocks and preserves incompatible parameter draft %j before any journal', (parameters) => {
    usePlaygroundStore.setState({ parameters: parameters as unknown as PlaygroundParameters });
    render(<PlaygroundPage />);
    expect(generateButton()).toBeDisabled();
    fireEvent.click(generateButton());
    expect(usePlaygroundStore.getState().parameters).toEqual(parameters);
    expect(screen.getByRole('button', { name: 'imageContract.correct' })).toBeTruthy();
    expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'imageContract.correct' }));
    expect(usePlaygroundStore.getState().parameters).toEqual({});
    expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
  });

  it('blocks a nonempty negative prompt on its own and preserves it for review', () => {
    usePlaygroundStore.setState({ negativePrompt: 'avoid blur' });
    render(<PlaygroundPage />);
    expect(generateButton()).toBeDisabled();
    fireEvent.click(generateButton());
    expect(usePlaygroundStore.getState().negativePrompt).toBe('avoid blur');
    expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
  });

  it('can generate after entering and then clearing the seed without rewriting the draft', async () => {
    render(<PlaygroundPage />);
    fireEvent.click(screen.getByRole('button', { name: 'parameters.advanced' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Seed' }), { target: { value: '123' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Seed' }), { target: { value: '' } });
    expect(usePlaygroundStore.getState().parameters).toEqual({ seed: undefined });
    expect(generateButton()).toBeEnabled();
    fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).not.toHaveProperty('parameters');
    expect(usePlaygroundStore.getState().parameters).toEqual({ seed: undefined });
  });

  it('requires explicit correction then a separate generate, preserving valid values', async () => {
    usePlaygroundStore.setState({ negativePrompt: 'do not lose this', parameters: { prompt_extend: false, quality: 'high', negativePrompt: 'unknown field', size: '768*1024', seed: 0, watermark: false } });
    render(<PlaygroundPage />);
    expect(generateButton()).toBeDisabled();
    expect(usePlaygroundStore.getState().negativePrompt).toBe('do not lose this');
    fireEvent.click(screen.getByRole('button', { name: 'imageContract.correct' }));
    expect(usePlaygroundStore.getState().parameters).toEqual({ size: '768*1024', seed: 0, watermark: false });
    expect(usePlaygroundStore.getState().negativePrompt).toBe('');
    expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
    fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
  });

  it('restores invalid size/watermark/seed only after correction, without persisting preset defaults', () => {
    usePlaygroundStore.setState({ parameters: { size: 'bad-size', seed: -1, watermark: 'yes' } as unknown as PlaygroundParameters });
    render(<PlaygroundPage />);
    expect(usePlaygroundStore.getState().parameters.size).toBe('bad-size');
    fireEvent.click(screen.getByRole('button', { name: 'imageContract.correct' }));
    expect(usePlaygroundStore.getState().parameters).toEqual({});
    expect(generateButton()).toBeEnabled(); expect(api.generate).not.toHaveBeenCalled();
  });

  it('keeps the T2I-with-image I2I path and restores the blocked T2I draft after removing media', async () => {
    const parameters = { size: '1024*1024', prompt_extend: false, seed: -1 };
    usePlaygroundStore.setState({ inputMedia: ['uploads/reference.png'], negativePrompt: 'legacy negative', parameters });
    render(<PlaygroundPage />);
    fireEvent.click(screen.getByRole('button', { name: 'parameters.advanced' }));
    expect(screen.getByText('parameters.promptExtend')).toBeTruthy();
    fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).toMatchObject({ mode: 'i2i', parameters, negative_prompt: 'legacy negative', input_media: ['uploads/reference.png'] });
    act(() => usePlaygroundStore.setState({ inputMedia: [] }));
    expect(generateButton()).toBeDisabled(); expect(usePlaygroundStore.getState().parameters).toEqual(parameters);
  });

  it('does not tighten another image model', async () => {
    usePlaygroundStore.setState({ modelId: 'wanx2.1-t2i', negativePrompt: 'legacy', parameters: { prompt_extend: true, seed: -1 } });
    render(<PlaygroundPage />); fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).toMatchObject({ negative_prompt: 'legacy', parameters: { prompt_extend: true, seed: -1 } });
  });
});

describe('Wan2.7 historical retry and readback', () => {
  it('returns invalid history to editing without dropping null/object parameters or changing the original', async () => {
    const old = generation({ negative_prompt: 'old negative', parameters: { size: '768*1024', seed: 0, watermark: false, prompt_extend: null, custom: { preserved: true } } as unknown as PlaygroundParameters });
    api.getHistory.mockResolvedValue([old]); render(<PlaygroundPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'retry old-image' }));
    expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
    expect(usePlaygroundStore.getState()).toMatchObject({ modelId: old.model_id, mode: old.mode, prompt: old.prompt, negativePrompt: old.negative_prompt, parameters: old.parameters, batchSize: 2 });
    expect(usePlaygroundStore.getState().history[0]).toEqual(old);
    expect(screen.getByText('imageContract.retryEdit')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'imageContract.correct' }));
    expect(usePlaygroundStore.getState().history[0]).toEqual(old); expect(api.generate).not.toHaveBeenCalled();
    fireEvent.click(generateButton()); await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
  });

  it('keeps the existing I2I retry payload shape, including its legacy filtering', async () => {
    api.getHistory.mockResolvedValue([generation({ mode: 'i2i', input_media: ['uploads/old.png'], negative_prompt: 'old negative', parameters: { size: '1024*1024', seed: -1, prompt_extend: false, extra_null: null, custom: { old: true } } as unknown as PlaygroundParameters })]);
    render(<PlaygroundPage />); fireEvent.click(await screen.findByRole('button', { name: 'retry old-image' }));
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).toMatchObject({ mode: 'i2i', negative_prompt: 'old negative', input_media: ['uploads/old.png'], parameters: { size: '1024*1024', seed: -1, prompt_extend: false } });
    expect(api.generate.mock.calls[0][0].parameters).not.toHaveProperty('custom');
    expect(api.generate.mock.calls[0][0].parameters).not.toHaveProperty('extra_null');
  });

  it('returns historical T2I with media to editing instead of silently retrying as I2I', async () => {
    const old = generation({ input_media: ['uploads/old.png'], parameters: { seed: 0, watermark: false, prompt_extend: null } as unknown as PlaygroundParameters });
    api.getHistory.mockResolvedValue([old]); render(<PlaygroundPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'retry old-image' }));
    expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
    expect(usePlaygroundStore.getState()).toMatchObject({ mode: 't2i', inputMedia: old.input_media, parameters: old.parameters });
    expect(screen.getByText('imageContract.retryReferenceEdit')).toBeTruthy();
    expect(usePlaygroundStore.getState().history[0]).toEqual(old);
    fireEvent.click(generateButton());
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0]).toMatchObject({ mode: 'i2i', input_media: old.input_media, parameters: old.parameters });
  });

  it.each([{}, { default_model_id: 'wan2.7-image' }, { default_mode: 't2i' }])('retains image template fields when applying inherited defaults %j', async (defaults) => {
    api.getTemplates.mockResolvedValue([{ id: 'template', name: 'legacy', category: 'image', prompt: 'template prompt', ...defaults, default_parameters: { seed: 0, watermark: false, prompt_extend: null, custom: { old: true } }, created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z' }]);
    render(<PlaygroundPage />);
    await waitFor(() => expect(usePlaygroundStore.getState().templates).toHaveLength(1));
    act(() => usePlaygroundStore.getState().applyTemplate(usePlaygroundStore.getState().templates[0]));
    expect(usePlaygroundStore.getState().parameters).toMatchObject({ prompt_extend: null, custom: { old: true } });
    expect(generateButton()).toBeDisabled(); expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
  });

  it.each([{ mode: 'i2i' as const, modelId: ids[0] }, { mode: 't2i' as const, modelId: 'wanx2.1-t2i' }])('keeps the old template projection outside strict T2I ($mode/$modelId)', async (target) => {
    api.getTemplates.mockResolvedValue([{ id: 'template', name: 'legacy', category: 'image', prompt: 'template prompt', default_parameters: { seed: -1, prompt_extend: false, custom: { old: true }, unknown: null }, created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z' }]);
    render(<PlaygroundPage />);
    await waitFor(() => expect(usePlaygroundStore.getState().templates).toHaveLength(1));
    act(() => {
      usePlaygroundStore.setState(target);
      usePlaygroundStore.getState().applyTemplate(usePlaygroundStore.getState().templates[0]);
    });
    expect(usePlaygroundStore.getState().parameters).toEqual({ seed: -1, prompt_extend: false });
    expect(generateButton()).toBeEnabled();
  });

  it('retries valid history with zero/false and omits blank negative', async () => {
    api.getHistory.mockResolvedValue([generation({ model_id: ids[3], negative_prompt: '  ', parameters: { seed: 0, watermark: false, size: '1024*768' } })]);
    render(<PlaygroundPage />); fireEvent.click(await screen.findByRole('button', { name: 'retry old-image' }));
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0].parameters).toEqual({ seed: 0, watermark: false, size: '1024*768' });
    expect(api.generate.mock.calls[0][0]).not.toHaveProperty('negative_prompt');
  });

  it('uses the same undefined omission when retrying a local history entry', async () => {
    render(<PlaygroundPage />);
    await waitFor(() => expect(api.getHistory).toHaveBeenCalledOnce());
    await act(async () => undefined);
    const old = generation({ parameters: { size: '1024*1024', seed: undefined, watermark: false } });
    act(() => usePlaygroundStore.setState({ history: [old] }));
    fireEvent.click(screen.getByRole('button', { name: 'retry old-image' }));
    await waitFor(() => expect(api.generate).toHaveBeenCalledOnce());
    expect(api.generate.mock.calls[0][0].parameters).toEqual({ size: '1024*1024', watermark: false });
    expect(old.parameters).toEqual({ size: '1024*1024', seed: undefined, watermark: false });
  });

  it('reads an old incompatible request receipt without a new POST or repairing its content', async () => {
    const saved = beginPlaygroundGenerationRequest({ environment: getRuntimeEnvironment()!, modelId: ids[0], mode: 't2i', prompt: 'old original', parameters: { prompt_extend: true, seed: -1 }, negativePrompt: 'legacy' });
    api.getRequestReceipt.mockResolvedValue(receipt(saved.request));
    usePlaygroundStore.setState({ parameters: { prompt_extend: false } }); render(<PlaygroundPage />);
    await waitFor(() => expect(api.getRequestReceipt).toHaveBeenCalledWith(saved.clientRequestId));
    await waitFor(() => expect(usePlaygroundStore.getState().history).toHaveLength(1));
    expect(api.generate).not.toHaveBeenCalled(); expect(journals()).toEqual([]);
    expect(usePlaygroundStore.getState().parameters).toEqual({ prompt_extend: false });
  });
});
