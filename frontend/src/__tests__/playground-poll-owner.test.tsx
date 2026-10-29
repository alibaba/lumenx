// @vitest-environment happy-dom
import React from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PlaygroundPage from '@/components/modules/playground/PlaygroundPage';
import { usePlaygroundStore } from '@/components/modules/playground/usePlaygroundStore';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginPlaygroundGenerationRequest, listPlaygroundGenerationRequests } from '@/lib/playgroundGenerationRequest';
import type { PlaygroundGenerationResponse, PlaygroundRequestReceiptResponse } from '@/lib/api';
import en from '../../messages/en.json';
import capturedWire from '@/__tests__/fixtures/playground-qwen20-wire.json';

const api = vi.hoisted(() => ({ generate: vi.fn(), getHistory: vi.fn(), getTemplates: vi.fn(), getRequestReceipt: vi.fn(), getGeneration: vi.fn(), getCapabilities: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'), playgroundApi: api }));
const source = capturedWire['qwen-image-2.0:default'] as unknown as {
  request: { client_request_id: string; mode: 't2i'; model_id: string; prompt: string; batch_size: number };
  accepted: PlaygroundRequestReceiptResponse;
  completed: PlaygroundRequestReceiptResponse;
};
const storageA = '11111111-1111-4111-8111-111111111111';
const storageB = '22222222-2222-4222-8222-222222222222';
function journals() { return listPlaygroundGenerationRequests({ environment: getRuntimeEnvironment()! }); }
function renderPage() { return render(<NextIntlClientProvider locale="en" messages={en} timeZone="UTC"><PlaygroundPage /></NextIntlClientProvider>); }
async function flush() { await act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); }); }
async function tick() { await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); }

beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
  activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA });
  usePlaygroundStore.getState().resetInput();
  usePlaygroundStore.setState({ mode: 't2i', modelId: source.request.model_id, prompt: source.request.prompt, batchSize: 2, modelPreferences: {}, history: [], templates: [], queue: [], activeGenerationIds: [], deletedGenerationIds: [], isGenerating: false, isHistoryLoading: false, historyError: undefined, historyHasMore: false, maxConcurrent: 3, showTemplateModal: false, showHistoryDrawer: false });
  api.getHistory.mockResolvedValue([]); api.getTemplates.mockResolvedValue([]);
  api.getRequestReceipt.mockResolvedValue(source.accepted);
  beginPlaygroundGenerationRequest({ environment: getRuntimeEnvironment()!, modelId: source.request.model_id, mode: source.request.mode, prompt: source.request.prompt, batchSize: source.request.batch_size, idFactory: () => source.request.client_request_id });
});
afterEach(() => { cleanup(); clearRuntimeEnvironment(); localStorage.clear(); vi.useRealTimers(); });

describe('Playground polling ownership', () => {
  it.each(['control', 'aba-resolve', 'aba-reject', 'aba-resolve-during-new-read', 'aba-reject-during-new-read'] as const)('%s preserves the current poll and journal until its own completion', async scenario => {
    let resolveOld!: (value: PlaygroundGenerationResponse) => void;
    let rejectOld!: (reason: Error) => void;
    let resolveCurrent!: (value: PlaygroundGenerationResponse) => void;
    const duringNewRead = scenario.endsWith('during-new-read');
    api.getGeneration.mockImplementationOnce(() => new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; }));
    if (duringNewRead) api.getGeneration.mockImplementationOnce(() => new Promise(resolve => { resolveCurrent = resolve; }));
    else api.getGeneration.mockResolvedValue(source.completed.generation);
    renderPage();
    await flush();
    expect(api.getRequestReceipt).toHaveBeenCalledTimes(1);
    expect(usePlaygroundStore.getState().activeGenerationIds).toEqual([source.accepted.generation!.id]);
    await tick(); expect(api.getGeneration).toHaveBeenCalledTimes(1);
    if (scenario !== 'control') {
      act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: storageB }); activateRuntimeEnvironment({ schema_version: 1, storage_id: storageA }); });
      await flush();
      expect(api.getRequestReceipt).toHaveBeenCalledTimes(2);
      expect(usePlaygroundStore.getState().activeGenerationIds).toEqual([source.accepted.generation!.id]);
    }
    if (duringNewRead) { await tick(); expect(api.getGeneration).toHaveBeenCalledTimes(2); }
    await act(async () => {
      if (scenario.includes('reject')) rejectOld(new Error('old A polling response failed after return'));
      else resolveOld(source.accepted.generation!);
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
    });
    if (duringNewRead) {
      await act(async () => { resolveCurrent(source.completed.generation!); });
    }
    await tick();
    expect.soft(api.generate).not.toHaveBeenCalled();
    expect.soft(api.getGeneration).toHaveBeenCalledTimes(2);
    expect.soft(usePlaygroundStore.getState().history[0]?.status).toBe('completed');
    expect.soft(usePlaygroundStore.getState().activeGenerationIds).toEqual([]);
    expect(journals()).toEqual([]);
  });

  it('retains an unresolved journal on unmount and finishes the same generation after remount', async () => {
    let resolveOld!: (value: PlaygroundGenerationResponse) => void;
    api.getGeneration.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    api.getGeneration.mockResolvedValue(source.completed.generation);
    const first = renderPage(); await flush(); await tick();
    expect(api.getGeneration).toHaveBeenCalledTimes(1);
    first.unmount(); expect(journals()).toHaveLength(1);
    renderPage(); await flush();
    await act(async () => { resolveOld(source.completed.generation!); });
    expect(usePlaygroundStore.getState().history[0]?.status).toBe('pending');
    await tick();
    expect(api.getGeneration).toHaveBeenCalledTimes(2);
    expect(api.getRequestReceipt).toHaveBeenCalledTimes(2);
    expect(api.generate).not.toHaveBeenCalled();
    expect(usePlaygroundStore.getState().history[0]?.status).toBe('completed');
    expect(journals()).toEqual([]);
    await tick(); expect(api.getGeneration).toHaveBeenCalledTimes(2);
  });

  it('attaches a recovered journal when history already started the same generation poll', async () => {
    let resolveReceipt!: (value: PlaygroundRequestReceiptResponse) => void;
    api.getHistory.mockResolvedValueOnce([source.accepted.generation]);
    api.getRequestReceipt.mockImplementationOnce(() => new Promise(resolve => { resolveReceipt = resolve; }));
    api.getGeneration.mockResolvedValue(source.completed.generation);
    renderPage(); await flush();
    expect(usePlaygroundStore.getState().activeGenerationIds).toEqual([source.accepted.generation!.id]);
    await act(async () => { resolveReceipt(source.accepted); });
    await tick();
    expect(api.getGeneration).toHaveBeenCalledTimes(1);
    expect(api.generate).not.toHaveBeenCalled();
    expect(usePlaygroundStore.getState().history[0]?.status).toBe('completed');
    expect(journals()).toEqual([]);
  });
});
