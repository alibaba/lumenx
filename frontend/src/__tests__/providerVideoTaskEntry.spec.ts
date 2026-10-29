import { describe, expect, it } from 'vitest';
import {
  buildProviderVideoTaskPayload,
  parseProviderVideoTaskReceipt,
  ProviderVideoTaskEntryError,
  isProviderVideoModel,
} from '@/lib/providerVideoTaskEntry';

const baseInput = () => ({
  clientRequestId: 'request-1',
  imageUrl: 'uploads/source.png',
  prompt: 'cinematic shot',
  model: 'wan2.6-i2v-flash',
  frameId: 'frame-a',
  batchSize: 2,
  duration: 5,
  resolution: '720p',
  promptExtend: false,
  negativePrompt: '',
  seed: 0,
  workbenchTab: 't2i_i2v' as const,
});

describe('provider video task entry projection', () => {
  it('builds a thin Wan2.6 provider payload without legacy empty defaults', () => {
    const payload = buildProviderVideoTaskPayload(baseInput());

    expect(payload).toEqual({
      client_request_id: 'request-1',
      image_url: 'uploads/source.png',
      prompt: 'cinematic shot',
      model: 'wan2.6-i2v-flash',
      frame_id: 'frame-a',
      generation_mode: 'i2v',
      batch_size: 2,
      duration: 5,
      resolution: '720p',
      prompt_extend: false,
      negative_prompt: '',
      seed: 0,
      generate_audio: false,
      workbench_tab: 't2i_i2v',
    });
    expect(payload).not.toHaveProperty('audio_url');
    expect(payload).not.toHaveProperty('reference_video_urls');
    expect(payload).not.toHaveProperty('reference_image_urls');
    expect(payload).not.toHaveProperty('watermark');
    expect(payload).not.toHaveProperty('mode');
    expect(payload).not.toHaveProperty('vidu_audio');
  });

  it('builds a Wan2.7 first-frame provider payload with explicit watermark and strict bounds', () => {
    const payload = buildProviderVideoTaskPayload({
      ...baseInput(),
      model: 'wan2.7-i2v',
      prompt: 'p'.repeat(5000),
      negativePrompt: 'n'.repeat(500),
      duration: 15,
      resolution: '1080p',
      seed: 2147483647,
      watermark: false,
    });

    expect(payload).toMatchObject({
      client_request_id: 'request-1',
      model: 'wan2.7-i2v',
      duration: 15,
      resolution: '1080p',
      seed: 2147483647,
      watermark: false,
      prompt: 'p'.repeat(5000),
      negative_prompt: 'n'.repeat(500),
    });
    expect(payload).not.toHaveProperty('generate_audio');
    expect(payload).not.toHaveProperty('shot_type');
    expect(payload).not.toHaveProperty('audio_url');
    expect(payload).not.toHaveProperty('reference_video_urls');
    expect(payload).not.toHaveProperty('reference_image_urls');

    expect(buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', watermark: true })).toHaveProperty('watermark', true);
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', duration: 1 }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', resolution: '480p' }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', seed: 2147483648 }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', prompt: 'p'.repeat(5001) }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', negativePrompt: 'n'.repeat(501) }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', shotType: 'multi' }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_shot_type_incompatible'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', ratio: '16:9' } as Parameters<typeof buildProviderVideoTaskPayload>[0] & { ratio: string }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', generate_audio: false } as Parameters<typeof buildProviderVideoTaskPayload>[0] & { generate_audio: boolean }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
  });

  it('counts Wan2.7 prompt limits by Unicode code points like the backend', () => {
    const astral = '😀';

    expect(buildProviderVideoTaskPayload({
      ...baseInput(),
      model: 'wan2.7-i2v',
      prompt: astral.repeat(5000),
      negativePrompt: astral.repeat(500),
    })).toMatchObject({
      prompt: astral.repeat(5000),
      negative_prompt: astral.repeat(500),
    });
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', prompt: astral.repeat(5001) }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.7-i2v', negativePrompt: astral.repeat(501) }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
  });

  it('omits generate_audio for plain wan2.6-i2v and rejects unsupported or explicit incompatible values', () => {
    expect(buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan2.6-i2v' })).not.toHaveProperty('generate_audio');
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), model: 'wan3.0-i2v' }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_model_unsupported'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), watermark: false }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_watermark_unsupported'));
    expect(() => buildProviderVideoTaskPayload({ ...baseInput(), promptExtend: false, shotType: 'multi' }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_shot_type_incompatible'));
  });

  it('parses the new receipt response and rejects malformed or mismatched task acknowledgements', () => {
    const receipt = parseProviderVideoTaskReceipt({
      client_request_id: 'request-1',
      replayed: false,
      task_ids: ['task-1', 'task-2'],
      reason: null,
      tasks: [
        { id: 'task-1', project_id: 'p', frame_id: 'f', generation_mode: 'i2v', workbench_tab: 't2i_i2v' },
        { id: 'task-2', project_id: 'p', frame_id: 'f', generation_mode: 'i2v', workbench_tab: 't2i_i2v' },
      ],
    }, { clientRequestId: 'request-1', projectId: 'p', frameId: 'f', generationMode: 'i2v', workbenchTab: 't2i_i2v', batchSize: 2 });

    expect(receipt.taskIds).toEqual(['task-1', 'task-2']);
    expect(receipt.replayed).toBe(false);
    expect(receipt.reason).toBeNull();
    expect(receipt.tasks).toHaveLength(2);

    expect(() => parseProviderVideoTaskReceipt({ ...receipt, client_request_id: 'wrong' }, { clientRequestId: 'request-1', projectId: 'p', frameId: 'f', generationMode: 'i2v', workbenchTab: 't2i_i2v', batchSize: 2 }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'request-1', replayed: false, task_ids: ['task-1'], tasks: [{ id: 'task-1', project_id: 'p', frame_id: 'other', generation_mode: 'i2v', workbench_tab: 't2i_i2v' }], reason: null }, { clientRequestId: 'request-1', projectId: 'p', frameId: 'f', generationMode: 'i2v', workbenchTab: 't2i_i2v', batchSize: 2 }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
  });

  it('requires receipt ids and tasks to be unique, complete, and match the pending batch size', () => {
    const expected = { clientRequestId: 'request-1', projectId: 'p', frameId: 'f', generationMode: 'i2v' as const, workbenchTab: 't2i_i2v' as const, batchSize: 2 };
    const task = (id: string) => ({ id, project_id: 'p', frame_id: 'f', generation_mode: 'i2v', workbench_tab: 't2i_i2v' });

    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'request-1', replayed: false, task_ids: ['task-1', 'task-1'], tasks: [task('task-1')], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'request-1', replayed: false, task_ids: ['task-1', 'task-2'], tasks: [task('task-1')], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'request-1', replayed: false, task_ids: ['task-1'], tasks: [task('task-1')], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'request-1', replayed: true, task_ids: ['task-1', 'task-2'], tasks: [task('task-1')], reason: 'tombstone' }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
  });

  it('accepts only replay tombstones with a complete original id set and valid task subset', () => {
    const expected = { clientRequestId: 'request-1', projectId: 'p', frameId: 'f', generationMode: 'i2v' as const, workbenchTab: 't2i_i2v' as const, batchSize: 2 };
    const task = (id: string) => ({ id, project_id: 'p', frame_id: 'f', generation_mode: 'i2v', workbench_tab: 't2i_i2v' });

    const parsed = parseProviderVideoTaskReceipt({
      client_request_id: 'request-1',
      replayed: true,
      task_ids: ['task-1', 'task-2'],
      tasks: [task('task-1')],
      reason: 'provider_request_deleted',
    }, expected);

    expect(parsed.reason).toBe('provider_request_deleted');
    expect(parsed.taskIds).toEqual(['task-1', 'task-2']);
    expect(parsed.tasks).toEqual([task('task-1')]);

    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'request-1', replayed: false, task_ids: ['task-1', 'task-2'], tasks: [task('task-1')], reason: 'provider_request_deleted' }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'request-1', replayed: true, task_ids: ['task-1'], tasks: [task('task-1')], reason: 'provider_request_deleted' }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'request-1', replayed: true, task_ids: ['task-1', 'task-2'], tasks: [task('task-1')], reason: 'provider_request_timeout' }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'request-1', replayed: true, task_ids: ['task-1', 'task-2'], tasks: [task('task-x')], reason: 'provider_request_owner_deleted' }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
  });

  it('keeps provider model detection scoped to the legacy v1 Studio entry path', () => {
    expect(isProviderVideoModel('wan2.7-i2v')).toBe(true);
    expect(isProviderVideoModel('wan3.0-video')).toBe(false);
  });

  it('builds ordered Wan3 T2V payloads with explicit zero media and preserved params', () => {
    const payload = buildProviderVideoTaskPayload({
      clientRequestId: 'ordered-t2v',
      contractVersion: 'ordered-media-v1',
      prompt: 'text only scene',
      model: 'wan3.0-video',
      frameId: 'frame-a',
      generationMode: 't2v',
      workbenchTab: 't2i_i2v',
      batchSize: 1,
      duration: -1,
      resolution: '1080P',
      ratio: '16:9',
      seed: 0,
      promptExtend: false,
      watermark: false,
      generateAudio: false,
    });

    expect(payload).toEqual({
      client_request_id: 'ordered-t2v',
      contract_version: 'ordered-media-v1',
      prompt: 'text only scene',
      model: 'wan3.0-video',
      frame_id: 'frame-a',
      generation_mode: 't2v',
      workbench_tab: 't2i_i2v',
      batch_size: 1,
      duration: -1,
      resolution: '1080P',
      ratio: '16:9',
      seed: 0,
      prompt_extend: false,
      watermark: false,
      generate_audio: false,
    });
    expect(payload).not.toHaveProperty('image_url');
    expect(payload).not.toHaveProperty('last_frame_url');
    expect(payload).not.toHaveProperty('reference_image_urls');
    expect(payload).not.toHaveProperty('reference_video_urls');
    expect(payload).not.toHaveProperty('reference_audio_urls');
  });

  it('builds ordered Wan3 I2V and R2V payloads without reordering media refs', () => {
    expect(buildProviderVideoTaskPayload({
      clientRequestId: 'ordered-i2v',
      contractVersion: 'ordered-media-v1',
      prompt: 'first and last',
      model: 'wan3.0-video-prime',
      frameId: 'frame-a',
      generationMode: 'i2v',
      imageUrl: 'uploads/first.png',
      lastFrameUrl: 'uploads/last.png',
      workbenchTab: 't2i_i2v',
      batchSize: 1,
    })).toMatchObject({
      contract_version: 'ordered-media-v1',
      generation_mode: 'i2v',
      model: 'wan3.0-video-prime',
      image_url: 'uploads/first.png',
      last_frame_url: 'uploads/last.png',
    });

    const r2v = buildProviderVideoTaskPayload({
      clientRequestId: 'ordered-r2v',
      contractVersion: 'ordered-media-v1',
      prompt: 'ordered refs',
      model: 'wan3.0-video',
      frameId: 'frame-a',
      generationMode: 'r2v',
      referenceImageUrls: ['uploads/img-b.png', 'uploads/img-a.png'],
      referenceVideoUrls: ['uploads/video-b.mp4', 'uploads/video-a.mp4'],
      referenceAudioUrls: ['uploads/audio-b.wav', 'uploads/audio-a.wav'],
      workbenchTab: 'direct_r2v',
      batchSize: 2,
    });

    expect(r2v).toMatchObject({
      contract_version: 'ordered-media-v1',
      generation_mode: 'r2v',
      workbench_tab: 'direct_r2v',
      batch_size: 2,
      reference_image_urls: ['uploads/img-b.png', 'uploads/img-a.png'],
      reference_video_urls: ['uploads/video-b.mp4', 'uploads/video-a.mp4'],
      reference_audio_urls: ['uploads/audio-b.wav', 'uploads/audio-a.wav'],
    });
    expect(r2v).not.toHaveProperty('image_url');
    expect(r2v).not.toHaveProperty('last_frame_url');
  });

  it('rejects ordered Wan3 media conflicts, bounds, unsupported models, and unsupported params', () => {
    const orderedBase = {
      clientRequestId: 'ordered-bad',
      contractVersion: 'ordered-media-v1' as const,
      prompt: 'p',
      model: 'wan3.0-video',
      frameId: 'frame-a',
      generationMode: 't2v' as const,
      workbenchTab: 't2i_i2v' as const,
      batchSize: 1,
    };

    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, imageUrl: 'uploads/source.png' }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, generationMode: 'i2v', referenceImageUrls: ['uploads/ref.png'] }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, generationMode: 'r2v', imageUrl: 'uploads/source.png', referenceImageUrls: ['uploads/ref.png'] }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, generationMode: 'r2v', referenceImageUrls: Array.from({ length: 11 }, (_v, i) => `uploads/${i}.png`) }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, model: 'wan2.7-i2v' }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_model_unsupported'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, duration: 0 }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, resolution: '8K' }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, seed: -2 }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, negativePrompt: 'unsupported' } as Parameters<typeof buildProviderVideoTaskPayload>[0] & { negativePrompt: string }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, contractVersion: 'future' as 'ordered-media-v1' }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
  });

  it('enforces ordered Wan3 prompt length by code point and t2v nonblank policy', () => {
    const orderedBase = {
      clientRequestId: 'ordered-prompt',
      contractVersion: 'ordered-media-v1' as const,
      model: 'wan3.0-video',
      frameId: 'frame-a',
      generationMode: 't2v' as const,
      workbenchTab: 't2i_i2v' as const,
      batchSize: 1,
    };
    const astral = '😀';

    expect(buildProviderVideoTaskPayload({ ...orderedBase, prompt: astral.repeat(20000) })).toHaveProperty('prompt', astral.repeat(20000));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, prompt: astral.repeat(20001) }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(() => buildProviderVideoTaskPayload({ ...orderedBase, prompt: ' \t\n\u3000' }))
      .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    expect(buildProviderVideoTaskPayload({ ...orderedBase, generationMode: 'i2v', prompt: '', imageUrl: 'uploads/first.png' })).toHaveProperty('prompt', '');
  });

  it('rejects blank ordered media identifiers without rewriting valid source URLs', () => {
    const base = { clientRequestId: 'ordered-source', contractVersion: 'ordered-media-v1' as const,
      prompt: 'p', model: 'wan3.0-video', frameId: 'frame-a', generationMode: 'i2v' as const,
      workbenchTab: 't2i_i2v' as const, batchSize: 1 };
    for (const fields of [
      { imageUrl: '  ' },
      { imageUrl: 'uploads/first.png', lastFrameUrl: '  ' },
      { generationMode: 'r2v' as const, referenceImageUrls: ['  '] },
      { generationMode: 'r2v' as const, referenceVideoUrls: ['  '] },
      { generationMode: 'r2v' as const, referenceAudioUrls: ['  '] },
    ]) {
      expect(() => buildProviderVideoTaskPayload({ ...base, ...fields }))
        .toThrowError(new ProviderVideoTaskEntryError('provider_payload_invalid'));
    }
    expect(buildProviderVideoTaskPayload({ ...base, imageUrl: 'uploads/my frame.png' }).image_url)
      .toBe('uploads/my frame.png');
  });

  it('parses ordered Wan3 receipts against source fields and provider execution proof', () => {
    const mediaProof = (role: string, ordinal: number, declaredMediaType = 'image/png') => ({ schema_version: 1, role, ordinal, sha256: 'a'.repeat(64), size_bytes: 123, declared_media_type: declaredMediaType });
    const task = {
      id: 'task-ordered', project_id: 'p', frame_id: 'f', generation_mode: 'r2v', workbench_tab: 'direct_r2v',
      image_url: '', model: 'wan3.0-video', reference_image_urls: ['uploads/a.png'], reference_video_urls: ['uploads/b.mp4'], reference_audio_urls: ['uploads/c.wav'],
      provider_execution: {
        schema_version: 1, attempt_id: 'attempt', phase: 'completed', fingerprint: 'f'.repeat(64), provider_model_id: 'wan3.0-video',
        intent: { catalog_mode: 'video', mode: 'r2v', model_id: 'wan/wan3.0-video#video', prompt: 'ordered refs', params: {} },
        media: [mediaProof('reference_image', 0), mediaProof('reference_video', 0, 'video/mp4'), mediaProof('reference_audio', 0, 'audio/wav')],
        scope: { provider: 'dashscope', backend: 'dashscope', gateway: 'dashscope_workspace', base_url: 'https://workspace.cn-beijing.maas.aliyuncs.com', region: 'cn-beijing', workspace_id: 'workspace', account_identity: null },
        credential_ref: 'credential-ref', handle: null, observation: null, prepared_artifact: null, artifact: null,
      },
    };
    const expected = {
      clientRequestId: 'ordered-request', projectId: 'p', frameId: 'f', generationMode: 'r2v' as const,
      workbenchTab: 'direct_r2v' as const, batchSize: 1, contractVersion: 'ordered-media-v1' as const,
      model: 'wan3.0-video', referenceImageUrls: ['uploads/a.png'], referenceVideoUrls: ['uploads/b.mp4'], referenceAudioUrls: ['uploads/c.wav'],
    };

    const parsed = parseProviderVideoTaskReceipt({
      client_request_id: 'ordered-request', replayed: false, task_ids: ['task-ordered'], tasks: [task], reason: null,
    }, expected);
    expect(parsed.tasks[0]).toEqual(task);

    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'ordered-request', replayed: false, task_ids: ['task-ordered'], tasks: [{ ...task, model: 'wan3.0-video-prime' }], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'ordered-request', replayed: false, task_ids: ['task-ordered'], tasks: [{ ...task, reference_image_urls: ['uploads/other.png'] }], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'ordered-request', replayed: false, task_ids: ['task-ordered'], tasks: [{ ...task, provider_execution: { ...task.provider_execution, media: [mediaProof('reference_audio', 0, 'audio/wav'), mediaProof('reference_image', 0), mediaProof('reference_video', 0, 'video/mp4')] } }], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'ordered-request', replayed: false, task_ids: ['task-ordered'], tasks: [{ ...task, provider_execution: { ...task.provider_execution, schema_version: 2 } }], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'ordered-request', replayed: false, task_ids: ['task-ordered'], tasks: [{ ...task, provider_execution: { ...task.provider_execution, fingerprint: 'not-hex' } }], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'ordered-request', replayed: false, task_ids: ['task-ordered'], tasks: [{ ...task, provider_execution: { ...task.provider_execution, scope: { ...(task.provider_execution.scope), gateway: 'dashscope' } } }], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
    expect(() => parseProviderVideoTaskReceipt({ client_request_id: 'ordered-request', replayed: false, task_ids: ['task-ordered'], tasks: [{ ...task, provider_execution: { ...task.provider_execution, media: [mediaProof('reference_image', 0), mediaProof('reference_video', 0, 'image/png'), mediaProof('reference_audio', 0, 'audio/wav')] } }], reason: null }, expected))
      .toThrowError(new ProviderVideoTaskEntryError('provider_receipt_invalid'));
  });


});
