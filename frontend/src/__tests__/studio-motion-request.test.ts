// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { appAccessFetch } from '@/lib/appAccess';
import type { StudioMotionReceipt, StudioMotionRequestBody } from '@/lib/api';
import { beginStudioMotionRequest, listStudioMotionRequests, readStudioMotionRequestReceipt,
    submitStudioMotionRequest, validateStudioMotionReceipt, type StudioMotionRequestOwner } from '@/lib/studioMotionRequest';
import duration5 from './fixtures/studio-motion-duration5-wire.json';
import original from './fixtures/studio-motion-request-wire.json';
import batch1Rejection from './fixtures/studio-motion-batch1-rejection-wire.json';
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));

type Event = { label: string; body: Record<string, unknown>; request_body?: StudioMotionRequestBody; status_code: number };
type Capture = { cases: Array<{ case: string; events: Event[] }> };
function event(name: string, label: string): Event {
    const all = [...(duration5 as unknown as Capture).cases, ...(original as unknown as Capture).cases, ...(batch1Rejection as unknown as Capture).cases];
    const found = all.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual HTTP wire: ${name}/${label}`);
    return structuredClone(found);
}
const sample = (label: string) => event('lifecycle_script_duration5', label);
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const net = vi.mocked(appAccessFetch);
let owner: StudioMotionRequestOwner;
function prepared(name = 'lifecycle_script_duration5', label = 'accepted_post') {
    const body = event(name, label).request_body!;
    const { client_request_id: clientId, ...input } = body;
    return { input, clientId, selectedImageId: 'selected-image' };
}
function begin(name?: string, label?: string) {
    const p = prepared(name, label);
    return beginStudioMotionRequest({ ...owner, ...p, idFactory: () => p.clientId, now: () => 7 }).journal;
}
function submit(name?: string, label?: string, isCurrent?: () => boolean) {
    const p = prepared(name, label);
    return submitStudioMotionRequest({ ...owner, prepare: async () => p, idFactory: () => p.clientId, now: () => 7, isCurrent });
}
const journalBytes = () => {
    const key = Object.keys(localStorage).find(key => key.startsWith('lumenx.studioMotionRequest.v1:'))!;
    return localStorage.getItem(key);
};
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    owner = { environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' }),
        projectId: 'project', assetId: 'hero', assetOwner: { kind: 'script', id: 'project' }, storage: localStorage };
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, cb: (lock: object) => unknown) => cb({}),
    } });
});
afterEach(() => { clearRuntimeEnvironment(); });

it.each(['accepted_post', 'submitted_get', 'observed_running_get', 'completed_get'])('accepts the unchanged actual five-second %s wire', async label => {
    const journal = begin(); net.mockResolvedValue(response(sample(label).body));
    const result = await readStudioMotionRequestReceipt({ ...owner, journal });
    expect(result.status).toBe('confirmed');
    expect(result.receipt).toEqual(sample(label).body);
    expect(journalBytes()).not.toMatch(/provider_execution|credential_ref|source_handle|media\.invalid/);
    expect(net.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true);
});
it.each([
    ['unknown_submit_ack', 'accepted_before_lost_ack', 'unknown_original_get'],
    ['completed_unprojected', 'accepted_post', 'completed_unprojected_get'],
    ['prepared_local_repair', 'accepted_before_final_commit_fault', 'prepared_reopened_get'],
])('retains actual %s state without upgrading it to a different phase', async (name, admission, read) => {
    const journal = begin(name, admission), original = event(name, read).body;
    net.mockResolvedValue(response(original));
    const result = await readStudioMotionRequestReceipt({ ...owner, journal });
    expect(result.status).toBe('confirmed'); expect(result.receipt).toEqual(original);
    expect(net).toHaveBeenCalledTimes(1); expect(net.mock.calls[0][1]?.method).toBe('GET');
});

// Only negative cases mutate actual captured wire; positive fixtures stay byte-identical.
it.each([
    ['missing artifact', (value: StudioMotionReceipt) => { value.task!.provider_execution!.artifact = null; }],
    ['different artifact attempt', (value: StudioMotionReceipt) => { value.task!.provider_execution!.artifact!.attempt_id = 'another-attempt'; }],
    ['noncanonical path', (value: StudioMotionReceipt) => { value.task!.provider_execution!.artifact!.relative_path = 'video/../other.mp4'; }],
    ['invalid artifact digest', (value: StudioMotionReceipt) => { value.task!.provider_execution!.artifact!.sha256 = 'not-a-digest'; }],
    ['zero-size artifact', (value: StudioMotionReceipt) => { value.task!.provider_execution!.artifact!.size_bytes = 0; }],
    ['different artifact handle', (value: StudioMotionReceipt) => { (value.task!.provider_execution!.artifact!.handle as Record<string, unknown>).task_id = 'another-provider-task'; }],
    ['different observation handle', (value: StudioMotionReceipt) => { (value.task!.provider_execution!.observation!.source_handle as Record<string, unknown>).task_id = 'another-provider-task'; }],
    ['different prepared proof', (value: StudioMotionReceipt) => { value.task!.provider_execution!.prepared_artifact!.sha256 = 'a'.repeat(64); }],
    ['different execution scope', (value: StudioMotionReceipt) => { (value.task!.provider_execution!.scope as Record<string, unknown>).region = 'another-region'; }],
] as const)('rejects completion with %s before trusting or persisting it', (_label, mutate) => {
    const journal = begin(), value = sample('completed_get').body as unknown as StudioMotionReceipt;
    mutate(value);
    expect(() => validateStudioMotionReceipt(value, journal)).toThrow();
    expect(listStudioMotionRequests(owner)[0].receiptIdentity).toBeNull();
});
it('rejects an accepted phase carrying a provider handle', () => {
    const journal = begin(), value = sample('accepted_post').body as unknown as StudioMotionReceipt;
    value.task!.provider_execution!.handle = (sample('submitted_get').body as unknown as StudioMotionReceipt).task!.provider_execution!.handle;
    expect(() => validateStudioMotionReceipt(value, journal)).toThrow();
});
it('rejects submitted wire without its original provider handle', () => {
    const journal = begin(), value = sample('submitted_get').body as unknown as StudioMotionReceipt;
    value.task!.provider_execution!.handle = null;
    expect(() => validateStudioMotionReceipt(value, journal)).toThrow();
});

it.each([
    ['zero_admission', 'zero_admission_target_changed', 'target_rejection_original_key_404'],
    ['batch1_model_change_zero_admission', 'zero_admission_batch1_model_changed', 'batch1_rejection_original_key_404'],
])('persists the actual %s rejection beside unchanged journal bytes and reopens through original GET404', async (name, rejected, missingLabel) => {
    const rejection = event(name, rejected);
    let beforePost = '';
    net.mockImplementationOnce(async () => { beforePost = journalBytes()!; return response(rejection.body, rejection.status_code); });
    const sent = await submit(name, rejected);
    expect(sent?.status).toBe('not_accepted'); expect(journalBytes()).toBe(beforePost);
    clearRuntimeEnvironment(); owner = { ...owner, environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: owner.environment.storageId }) };
    const missing = event(name, missingLabel);
    net.mockResolvedValueOnce(response(missing.body, missing.status_code));
    const read = await readStudioMotionRequestReceipt({ ...owner, journal: listStudioMotionRequests(owner)[0] });
    expect(read.status).toBe('not_accepted'); expect(journalBytes()).toBe(beforePost);
    expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET']);
    expect(String(net.mock.calls[1][0])).toMatch(/\/motion_ref_requests\/motion-request$/);
});
it('does not use a stored target rejection to hide a failed GET', async () => {
    const rejection = event('zero_admission', 'zero_admission_target_changed');
    net.mockResolvedValueOnce(response(rejection.body, 409));
    const sent = await submit('zero_admission', 'zero_admission_target_changed');
    expect(sent?.status).toBe('not_accepted');
    net.mockResolvedValueOnce(response({ detail: 'unavailable' }, 503));
    const read = await readStudioMotionRequestReceipt({ ...owner, journal: sent!.journal });
    expect(read.status).toBe('unknown'); expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET']);
});
it('gives a real receipt precedence over a stored rejection of the same raw command', async () => {
    const rejection = event('zero_admission', 'zero_admission_target_changed');
    net.mockResolvedValueOnce(response(rejection.body, 409));
    const sent = await submit('zero_admission', 'zero_admission_target_changed');
    expect(sent?.status).toBe('not_accepted');
    // expected_target is a precondition, not part of the original request fingerprint.
    // This is unchanged actual wire for the same key/raw ten-second author command.
    const accepted = event('lifecycle_script', 'accepted_post');
    net.mockResolvedValueOnce(response(accepted.body));
    const read = await readStudioMotionRequestReceipt({ ...owner, journal: sent!.journal });
    expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(accepted.body);
    expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET']);
});
it('does not accept a late rejection callback after the original session is gone', async () => {
    let resolve!: (response: Response) => void;
    let live = true; net.mockImplementationOnce(() => new Promise<Response>(done => { resolve = done; }));
    const pending = submit('zero_admission', 'zero_admission_target_changed', () => live);
    await vi.waitFor(() => expect(net).toHaveBeenCalledTimes(1)); const original = journalBytes(); live = false;
    const rejection = event('zero_admission', 'zero_admission_target_changed');
    resolve(response(rejection.body, 409));
    expect((await pending)?.status).toBe('unknown'); expect(journalBytes()).toBe(original);
    expect(Object.keys(localStorage)).toHaveLength(1);
});
