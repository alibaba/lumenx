import { describe, expect, it, vi } from 'vitest';
import {
  ProviderVideoRequestError,
  beginProviderVideoRequest,
  withProviderVideoRequestLock,
  providerVideoRequestStorageKey,
  resolveProviderVideoRequest,
  type ProviderVideoRequestOwner,
} from '@/lib/providerVideoRequest';

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  clear(): void { this.values.clear(); }
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  key(index: number): string | null { return Array.from(this.values.keys())[index] ?? null; }
  removeItem(key: string): void { this.values.delete(key); }
  setItem(key: string, value: string): void { this.values.set(key, value); }
}

const owner = (overrides: Partial<ProviderVideoRequestOwner> = {}): ProviderVideoRequestOwner => ({
  apiBase: 'http://localhost:17177',
  storageId: '11111111-1111-4111-8111-111111111111',
  projectId: 'project-a',
  frameId: 'frame-a',
  ...overrides,
});

const baseInput = () => ({
  image_url: 'uploads/source.png',
  prompt: 'cinematic shot',
  model: 'wan2.6-i2v-flash',
  frame_id: 'frame-a',
  generation_mode: 'i2v',
  workbench_tab: 't2i_i2v',
  duration: 5,
  resolution: '720p',
});

const begin = (overrides: Partial<Parameters<typeof beginProviderVideoRequest>[0]> = {}) => beginProviderVideoRequest({
  owner: owner(),
  input: baseInput(),
  batchSize: 1,
  storage: new MemoryStorage(),
  idFactory: () => 'request-1',
  ...overrides,
});

describe('provider video request identity helper', () => {
  it('creates and persists one frozen request id and payload for an owner', () => {
    const storage = new MemoryStorage();
    const input = baseInput();

    const result = begin({ input, batchSize: 3, storage });

    expect(result.status).toBe('created');
    expect(result.clientRequestId).toBe('request-1');
    expect(result.payload).toEqual({
      ...baseInput(),
      batch_size: 3,
      client_request_id: 'request-1',
    });
    expect(Object.isFrozen(result.payload)).toBe(true);

    input.prompt = 'mutated after begin';
    const replay = begin({
      input: { ...baseInput(), prompt: 'new paid intent' },
      batchSize: 1,
      storage,
      idFactory: () => 'request-2',
    });

    expect(replay.status).toBe('pending');
    expect(replay.clientRequestId).toBe('request-1');
    expect(replay.payload.prompt).toBe('cinematic shot');
    expect(replay.payload.batch_size).toBe(3);
  });

  it('reuses persisted pending state after a fresh call with the same storage', () => {
    const storage = new MemoryStorage();
    begin({ storage, idFactory: () => 'request-a' });

    const afterReload = begin({
      storage,
      input: { ...baseInput(), prompt: 'after reload changed intent' },
      idFactory: () => 'request-b',
    });

    expect(afterReload.status).toBe('pending');
    expect(afterReload.clientRequestId).toBe('request-a');
    expect(afterReload.payload.prompt).toBe('cinematic shot');
  });

  it.each([
    ['api base', { apiBase: 'https://ecs.example' }],
    ['storage id', { storageId: '22222222-2222-4222-8222-222222222222' }],
    ['project id', { projectId: 'project-b' }],
    ['frame id', { frameId: 'frame-b' }],
  ] satisfies Array<[string, Partial<ProviderVideoRequestOwner>]>)('keeps owners isolated by %s', (_label, changedOwner) => {
    const storage = new MemoryStorage();
    const ids = vi.fn().mockReturnValueOnce('request-a').mockReturnValueOnce('request-b');

    const first = begin({ storage, idFactory: ids });
    const differentOwner = begin({ owner: owner(changedOwner), storage, idFactory: ids });

    expect(first.clientRequestId).toBe('request-a');
    expect(differentOwner.clientRequestId).toBe('request-b');
    expect(ids).toHaveBeenCalledTimes(2);
  });

  it('does not use workbench tab as an owner namespace', () => {
    const storage = new MemoryStorage();
    begin({ storage, input: { ...baseInput(), workbench_tab: 't2i_i2v' }, idFactory: () => 'request-a' });

    const changedTab = begin({
      storage,
      input: { ...baseInput(), workbench_tab: 'direct_r2v' },
      idFactory: () => 'request-b',
    });

    expect(changedTab.status).toBe('pending');
    expect(changedTab.clientRequestId).toBe('request-a');
    expect(changedTab.payload.workbench_tab).toBe('t2i_i2v');
  });

  it('resolves only the matching owner and request id so stale responses cannot clear pending work', () => {
    const storage = new MemoryStorage();
    begin({ storage, idFactory: () => 'request-a' });

    expect(resolveProviderVideoRequest({ owner: owner(), clientRequestId: 'wrong-id', storage })).toBe(false);
    expect(begin({ input: { ...baseInput(), prompt: 'new intent' }, storage, idFactory: () => 'request-b' }).clientRequestId).toBe('request-a');

    expect(resolveProviderVideoRequest({ owner: owner({ projectId: 'project-b' }), clientRequestId: 'request-a', storage })).toBe(false);
    expect(resolveProviderVideoRequest({ owner: owner(), clientRequestId: 'request-a', storage })).toBe(true);

    const next = begin({ input: { ...baseInput(), prompt: 'after verified readback' }, storage, idFactory: () => 'request-b' });
    expect(next.status).toBe('created');
    expect(next.clientRequestId).toBe('request-b');
  });

  it('does not let a stale A resolve clear B after B was created', () => {
    const storage = new MemoryStorage();
    const ownerA = owner({ storageId: '11111111-1111-4111-8111-111111111111' });
    const ownerB = owner({ storageId: '22222222-2222-4222-8222-222222222222' });
    begin({ owner: ownerA, storage, idFactory: () => 'request-a' });
    begin({ owner: ownerB, storage, idFactory: () => 'request-b' });

    expect(resolveProviderVideoRequest({ owner: ownerB, clientRequestId: 'request-a', storage })).toBe(false);
    expect(begin({ owner: ownerB, storage, idFactory: () => 'request-c' }).clientRequestId).toBe('request-b');
  });

  it.each([
    ['Date instance', { created_at: new Date('2026-10-11T00:00:00Z') }],
    ['Map instance', { metadata: new Map([['a', 1]]) }],
    ['custom class instance', { metadata: new (class Custom { value = 1; })() }],
    ['cyclic object', (() => { const item: Record<string, unknown> = {}; item.self = item; return item; })()],
    ['sparse array', { refs: [, 'image.png'] as unknown[] }],
    ['non-finite number', { seed: Number.NaN }],
    ['undefined value', { watermark: undefined }],
    ['function value', { callback: () => undefined }],
  ])('rejects non-strict JSON payload values: %s', (_label, patch) => {
    expect(() => begin({ input: { ...baseInput(), ...patch } }))
      .toThrowError(new ProviderVideoRequestError('provider_video_request_payload_invalid'));
  });

  it('preserves legitimate __proto__ and empty keys as JSON data without prototype mutation', () => {
    const storage = new MemoryStorage();
    const input = Object.create(null) as Record<string, unknown>;
    Object.assign(input, baseInput());
    Object.defineProperty(input, '__proto__', { value: 'literal-proto', enumerable: true });
    input[''] = 'empty-key';

    const result = begin({ input, storage });

    expect(result.payload.__proto__).toBe('literal-proto');
    expect(result.payload['']).toBe('empty-key');
    expect(Object.getPrototypeOf(result.payload)).toBeNull();
  });

  it('fails closed for corrupt storage and corrupted stored payload validation', () => {
    const storage = new MemoryStorage();
    storage.setItem(providerVideoRequestStorageKey(owner()), '{broken');
    expect(() => begin({ storage })).toThrowError(new ProviderVideoRequestError('provider_video_request_store_corrupt'));

    const poisoned = new MemoryStorage();
    const key = providerVideoRequestStorageKey(owner());
    poisoned.setItem(key, JSON.stringify({
      schema_version: 1,
      owner: owner(),
      client_request_id: 'request-a',
      payload: { ...baseInput(), client_request_id: 'request-a', batch_size: 1, api_key: 'secret' },
    }));
    expect(() => begin({ storage: poisoned })).toThrowError(new ProviderVideoRequestError('provider_video_request_store_corrupt'));
  });

  it('fails closed for unavailable storage reads, writes, removes, and default localStorage getter errors', () => {
    const readFailure = {
      getItem: () => { throw new Error('disabled'); },
      setItem: () => undefined,
      removeItem: () => undefined,
      clear: () => undefined,
      key: () => null,
      length: 0,
    } satisfies Storage;
    expect(() => begin({ storage: readFailure })).toThrowError(new ProviderVideoRequestError('provider_video_request_storage_unavailable'));

    const writeFailure = {
      getItem: () => null,
      setItem: () => { throw new Error('quota'); },
      removeItem: () => undefined,
      clear: () => undefined,
      key: () => null,
      length: 0,
    } satisfies Storage;
    expect(() => begin({ storage: writeFailure })).toThrowError(new ProviderVideoRequestError('provider_video_request_storage_unavailable'));

    const removeFailure = {
      getItem: () => JSON.stringify({
        schema_version: 1,
        owner: owner(),
        client_request_id: 'request-a',
        payload: { ...baseInput(), client_request_id: 'request-a', batch_size: 1 },
      }),
      setItem: () => undefined,
      removeItem: () => { throw new Error('locked'); },
      clear: () => undefined,
      key: () => null,
      length: 1,
    } satisfies Storage;
    expect(() => resolveProviderVideoRequest({ owner: owner(), clientRequestId: 'request-a', storage: removeFailure }))
      .toThrowError(new ProviderVideoRequestError('provider_video_request_storage_unavailable'));

    vi.stubGlobal('window', Object.defineProperty({}, 'localStorage', { get: () => { throw new Error('blocked'); } }));
    expect(() => beginProviderVideoRequest({ owner: owner(), input: baseInput(), batchSize: 1, idFactory: () => 'request-a' }))
      .toThrowError(new ProviderVideoRequestError('provider_video_request_storage_unavailable'));
  });


  it('serializes an owner with a named Web Lock and fails closed when locks are unavailable', async () => {
    const ownerValue = owner();
    const calls: string[] = [];
    const request = vi.fn(async (name: string, options: LockOptions & { ifAvailable?: boolean }, callback: (lock: object | null) => Promise<string>) => {
      calls.push(name);
      expect(options).toMatchObject({ mode: 'exclusive', ifAvailable: true });
      expect(options).not.toHaveProperty('steal');
      return callback({});
    });
    vi.stubGlobal('navigator', { locks: { request } });

    await expect(withProviderVideoRequestLock(ownerValue, async () => 'ok')).resolves.toBe('ok');
    expect(request).toHaveBeenCalledTimes(1);
    expect(calls[0]).toContain('lumenx.providerVideoRequest.v1:');
    expect(calls[0]).toContain(encodeURIComponent(providerVideoRequestStorageKey(ownerValue)));

    request.mockImplementationOnce(async (_name: string, _options: LockOptions & { ifAvailable?: boolean }, callback: (lock: object | null) => Promise<string>) => callback(null));
    await expect(withProviderVideoRequestLock(ownerValue, async () => 'never'))
      .rejects.toThrowError(new ProviderVideoRequestError('provider_video_request_lock_unavailable'));

    vi.stubGlobal('navigator', {});
    await expect(withProviderVideoRequestLock(ownerValue, async () => 'never'))
      .rejects.toThrowError(new ProviderVideoRequestError('provider_video_request_lock_unavailable'));
  });

  it('fails closed for invalid batch size, invalid id, and secret-like new payload keys', () => {
    expect(() => begin({ batchSize: 0 })).toThrowError(new ProviderVideoRequestError('provider_video_request_batch_invalid'));
    expect(() => begin({ idFactory: () => 'bad id' })).toThrowError(new ProviderVideoRequestError('provider_video_request_id_invalid'));
    expect(() => begin({ input: { ...baseInput(), api_key: 'secret' } })).toThrowError(new ProviderVideoRequestError('provider_video_request_secret_field'));
  });
});
