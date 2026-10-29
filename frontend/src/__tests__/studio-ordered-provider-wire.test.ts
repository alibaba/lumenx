import { describe, expect, it } from 'vitest';

import i2vBatchWire from './fixtures/studio-ordered-provider-i2v-batch.json';
import r2vWire from './fixtures/studio-ordered-provider-r2v.json';
import t2vWire from './fixtures/studio-ordered-provider-t2v.json';
import {
  ORDERED_MEDIA_CONTRACT_VERSION,
  ProviderVideoTaskEntryError,
  assertProviderVideoTasksMatchPayload,
  buildProviderVideoTaskPayload,
  parseProviderVideoTaskReceipt,
  providerVideoTaskExpectationFromPayload,
} from '@/lib/providerVideoTaskEntry';

type WireFixture = {
  request: Record<string, unknown>;
  accepted: unknown;
  replay: unknown;
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function expectedFromWire(wire: WireFixture) {
  const request = wire.request;
  return providerVideoTaskExpectationFromPayload(request, {
    clientRequestId: request.client_request_id as string,
    projectId: 'p',
    frameId: request.frame_id as string,
  });
}


function mediaRoles(task: unknown): string[] {
  const record = task as { provider_execution?: { media?: unknown } | null };
  const media = record.provider_execution?.media;
  if (!Array.isArray(media)) return [];
  return media.map(item => (item as { role: string }).role);
}

function parseBoth(wire: WireFixture) {
  const expected = expectedFromWire(wire);
  const accepted = parseProviderVideoTaskReceipt(wire.accepted, expected);
  const replay = parseProviderVideoTaskReceipt(wire.replay, expected);
  assertProviderVideoTasksMatchPayload(accepted.tasks, expected, wire.request);
  assertProviderVideoTasksMatchPayload(replay.tasks, expected, wire.request);
  expect(accepted.replayed).toBe(false);
  expect(replay.replayed).toBe(true);
  expect(replay.taskIds).toEqual(accepted.taskIds);
  return accepted;
}

describe('Studio ordered provider backend wire fixtures', () => {
  it('parses real backend T2V, I2V batch, and R2V admission plus replay responses', () => {
    const t2v = parseBoth(t2vWire as WireFixture);
    expect(t2v.tasks[0].provider_execution?.media).toEqual([]);

    const i2v = parseBoth(i2vBatchWire as WireFixture);
    expect(i2v.taskIds).toHaveLength(2);
    expect(i2v.tasks.map(task => mediaRoles(task))).toEqual([
      ['first_frame', 'last_frame'],
      ['first_frame', 'last_frame'],
    ]);

    const r2v = parseBoth(r2vWire as WireFixture);
    expect(mediaRoles(r2v.tasks[0])).toEqual(['reference_image', 'reference_audio']);
  });

  it('keeps ordered payload guard aligned with the real T2V prompt boundary', () => {
    const request = t2vWire.request;
    expect(() => buildProviderVideoTaskPayload({
      clientRequestId: request.client_request_id as string,
      contractVersion: ORDERED_MEDIA_CONTRACT_VERSION,
      prompt: '',
      model: request.model as string,
      frameId: request.frame_id as string,
      generationMode: 't2v',
      workbenchTab: request.workbench_tab as 't2i_i2v',
      batchSize: request.batch_size as number,
      duration: request.duration as number,
      resolution: request.resolution as string,
      ratio: request.ratio as string,
      seed: request.seed as number,
      promptExtend: request.prompt_extend as boolean,
      watermark: request.watermark as boolean,
      generateAudio: request.generate_audio as boolean,
    })).toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));

    expect(() => buildProviderVideoTaskPayload({
      clientRequestId: request.client_request_id as string,
      contractVersion: ORDERED_MEDIA_CONTRACT_VERSION,
      prompt: 'x'.repeat(20001),
      model: request.model as string,
      frameId: request.frame_id as string,
      generationMode: 't2v',
      workbenchTab: request.workbench_tab as 't2i_i2v',
      batchSize: request.batch_size as number,
      duration: request.duration as number,
      resolution: request.resolution as string,
      ratio: request.ratio as string,
      seed: request.seed as number,
      promptExtend: request.prompt_extend as boolean,
      watermark: request.watermark as boolean,
      generateAudio: request.generate_audio as boolean,
    })).toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
  });

  it('rejects malformed ordered provider execution mutations against real wire', () => {
    const expected = expectedFromWire(i2vBatchWire as WireFixture);

    const badHash = clone(i2vBatchWire);
    badHash.accepted.tasks[0].provider_execution.media[0].sha256 = 'z'.repeat(64);
    expect(() => parseProviderVideoTaskReceipt(badHash.accepted, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));

    const badSize = clone(i2vBatchWire);
    badSize.accepted.tasks[0].provider_execution.media[0].size_bytes = 0;
    expect(() => parseProviderVideoTaskReceipt(badSize.accepted, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));

    const badScope = clone(i2vBatchWire);
    badScope.accepted.tasks[0].provider_execution.scope.gateway = 'dashscope';
    expect(() => parseProviderVideoTaskReceipt(badScope.accepted, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));

    const badPhase = clone(i2vBatchWire);
    badPhase.accepted.tasks[0].provider_execution.phase = 'unknown';
    expect(() => parseProviderVideoTaskReceipt(badPhase.accepted, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
  });

  it('rejects ordered receipts whose provider intent drifts from the frozen request payload', () => {
    const expected = expectedFromWire(r2vWire as WireFixture);

    const badPrompt = clone(r2vWire);
    badPrompt.accepted.tasks[0].provider_execution.intent.prompt = 'changed';
    expect(() => {
      const parsed = parseProviderVideoTaskReceipt(badPrompt.accepted, expected);
      assertProviderVideoTasksMatchPayload(parsed.tasks, expected, r2vWire.request);
    }).toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));

    const badDuration = clone(r2vWire);
    badDuration.accepted.tasks[0].provider_execution.intent.params.duration = 6;
    expect(() => {
      const parsed = parseProviderVideoTaskReceipt(badDuration.accepted, expected);
      assertProviderVideoTasksMatchPayload(parsed.tasks, expected, r2vWire.request);
    }).toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));

    const badFalse = clone(r2vWire);
    badFalse.accepted.tasks[0].provider_execution.intent.params.prompt_extend = true;
    expect(() => {
      const parsed = parseProviderVideoTaskReceipt(badFalse.accepted, expected);
      assertProviderVideoTasksMatchPayload(parsed.tasks, expected, r2vWire.request);
    }).toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));

    const badZero = clone(r2vWire);
    badZero.accepted.tasks[0].provider_execution.intent.params.seed = 1;
    expect(() => {
      const parsed = parseProviderVideoTaskReceipt(badZero.accepted, expected);
      assertProviderVideoTasksMatchPayload(parsed.tasks, expected, r2vWire.request);
    }).toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
  });
});
