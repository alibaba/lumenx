// @vitest-environment happy-dom
// Consume unchanged actual headshot HTTP receipts and raw commands.
// Captures use real app/Core/storage with external transport fixtures, not paid providers.
// Client-local fetch/Web Locks boundaries are stubbed; UI projection is covered elsewhere.
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { validateStudioAssetImageReceipt } from '@/lib/studioAssetImageContract';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, submitStudioAssetImageRequest,
    readStudioAssetImageRequestReceipt, recoverStudioAssetImageRequests, resolveStudioAssetImageRequest,
    type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import capture from '@/__tests__/fixtures/studio-headshot-wire.json';

vi.mock('@/lib/appAccess', () => ({ appAccessFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
type Obj = Record<string, unknown>;
type Event = { label: string; method: string; path: string; status_code: number; body: Obj; request_body?: Obj };
type Capture = { cases: { case: string; events: Event[] }[] };
const SID = '11111111-1111-4111-8111-111111111111';
const CASES = [
    { name: 'local_single', ownerKind: 'script', ownerId: 'p', seriesId: null },
    { name: 'series_batch', ownerKind: 'series', ownerId: 's', seriesId: 's' },
    { name: 'global_single', ownerKind: 'global', ownerId: 'library', seriesId: null },
    { name: 'local_unknown', ownerKind: 'script', ownerId: 'p', seriesId: null },
    { name: 'global_partial', ownerKind: 'global', ownerId: 'library', seriesId: null },
    { name: 'restart_read_only', ownerKind: 'script', ownerId: 'p', seriesId: null },
];
function actual(name: string, label: string): Event {
    const found = (capture as unknown as Capture).cases.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual headshot HTTP event: ${name}/${label}`);
    return structuredClone(found);
}
function command(name = 'local_single') {
    const raw = actual(name, 'accepted_post').request_body;
    if (!raw || typeof raw.client_request_id !== 'string') throw new Error('Missing actual headshot author command');
    const { client_request_id, ...input } = raw;
    return { input: input as StudioAssetImageRequestInput, idFactory: () => client_request_id, now: () => 7 };
}
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p', storage: localStorage });
const journals = () => listStudioAssetImageRequests(owner());
const response = (event: Event) => new Response(JSON.stringify(event.body), { status: event.status_code });
let net: ReturnType<typeof vi.fn>;
beforeEach(() => {
    localStorage.clear(); clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    net = vi.fn(async () => { throw new Error('Unexpected headshot network request'); });
    vi.stubGlobal('fetch', net);
    vi.stubGlobal('navigator', { locks: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
});
afterEach(() => { vi.unstubAllGlobals(); clearRuntimeEnvironment(); });

it.each(CASES)('$name preserves actual public tuples, original identities and replay receipts', sample => {
    const accepted = validateStudioAssetImageReceipt(actual(sample.name, 'accepted_post').body);
    const labels = ['accepted_post', 'result_get', 'original_replay',
        ...(sample.name === 'local_unknown' ? ['reopen_get', 'reopen_replay'] : [])];
    for (const label of labels) {
        const event = actual(sample.name, label);
        expect(event.status_code).toBe(200);
        const parsed = validateStudioAssetImageReceipt(event.body);
        expect(parsed).toEqual(event.body);
        expect(parsed.request).toMatchObject({ entrypoint: 'studio.asset.headshot.v1', asset_type: 'character', channel: 'headshot',
            request_root_kind: 'project', request_root_id: 'p', request_root_series_id: sample.seriesId,
            owner_kind: sample.ownerKind, owner_id: sample.ownerId, asset_id: 'c',
            id: accepted.request.id, client_request_id: accepted.request.client_request_id,
            request_fingerprint: accepted.request.request_fingerprint, request_root_scope_id: accepted.request.request_root_scope_id });
        expect(parsed.request.members.map(({ variant_id, attempt_id }) => ({ variant_id, attempt_id })))
            .toEqual(accepted.request.members.map(({ variant_id, attempt_id }) => ({ variant_id, attempt_id })));
        expect(parsed.replayed).toBe(label !== 'accepted_post');
    }
    expect(net).not.toHaveBeenCalled();
});

it.each(['local_single', 'series_batch', 'global_single'])(
    '%s stores the actual raw command before one POST and reads only its original key without resubmission', async name => {
        const accepted = actual(name, 'accepted_post'), completed = actual(name, 'result_get');
        const original = command(name), raw = accepted.request_body!;
        net.mockImplementationOnce(async (url, init) => {
            expect(String(url)).toBe(owner().environment.apiUrl + accepted.path);
            expect(init?.method).toBe('POST'); expect(JSON.parse(String(init?.body))).toEqual(raw);
            expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(raw);
            return response(accepted);
        }).mockImplementationOnce(async (url, init) => {
            expect(String(url)).toBe(owner().environment.apiUrl + completed.path);
            expect(init?.method).toBe('GET'); expect(init).not.toHaveProperty('body');
            return response(completed);
        });
        const sent = await submitStudioAssetImageRequest({ ...owner(), ...original });
        expect(sent.status).toBe('confirmed'); expect(sent.receipt).toEqual(accepted.body);
        expect(sent.receipt?.request.members.every(member => member.status === 'pending')).toBe(true);
        const duplicate = await submitStudioAssetImageRequest({ ...owner(), ...original });
        expect(duplicate.reason).toBe('studio_image_request_already_recorded'); expect(net).toHaveBeenCalledTimes(1);
        const read = await readStudioAssetImageRequestReceipt({ ...owner(), journal: journals()[0] });
        expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(completed.body);
        expect(read.receipt?.request.members.every(member => member.status === 'completed')).toBe(true);
        expect(read.journal.receiptIdentity).toBe(sent.journal.receiptIdentity);
        expect((await submitStudioAssetImageRequest({ ...owner(), ...original })).reason).toBe('studio_image_request_already_recorded');
        expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET']);
        // Receipt consumption alone does not stand in for the UI's projection confirmation.
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(raw);
    });

it('recovers a lost ACK after the same storage reconnects with a new generation using only the original-key GET', async () => {
    const original = command(), raw = actual('local_single', 'accepted_post').request_body;
    const firstEnvironment = owner().environment;
    net.mockRejectedValueOnce(new Error('Deliberately lost transport acknowledgement'));
    const sent = await submitStudioAssetImageRequest({ ...owner(), ...original });
    expect(sent.status).toBe('unknown'); expect(journals()).toHaveLength(1);
    expect(JSON.parse(String(net.mock.calls[0][1]?.body))).toEqual(raw);
    clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    expect(owner().environment.key).toBe(firstEnvironment.key);
    expect(owner().environment.generation).not.toBe(firstEnvironment.generation);
    const completed = actual('local_single', 'result_get');
    net.mockImplementationOnce(async () => response(completed));
    const recovered = await recoverStudioAssetImageRequests(owner());
    expect(recovered).toHaveLength(1); expect(recovered[0].status).toBe('confirmed');
    expect(recovered[0].receipt).toEqual(completed.body);
    expect(String(net.mock.calls[1][0])).toBe(owner().environment.apiUrl + completed.path);
    expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET']);
    expect((await submitStudioAssetImageRequest({ ...owner(), ...original })).reason).toBe('studio_image_request_already_recorded');
    expect(net).toHaveBeenCalledTimes(2); expect(journals()[0].request).toEqual(raw);
});

it('accepts the actual replayed POST ACK for a newly recorded browser journal and never repeats that POST', async () => {
    const replay = actual('local_single', 'original_replay'), original = command();
    expect(journals()).toHaveLength(0);
    net.mockImplementationOnce(async (url, init) => {
        expect(String(url)).toBe(owner().environment.apiUrl + replay.path);
        expect(init?.method).toBe(replay.method);
        expect(JSON.parse(String(init?.body))).toEqual(replay.request_body);
        expect(journals()[0].request).toEqual(replay.request_body);
        return response(replay);
    });
    const sent = await submitStudioAssetImageRequest({ ...owner(), ...original });
    expect(sent.status).toBe('confirmed'); expect(sent.receipt).toEqual(replay.body);
    expect(sent.receipt?.replayed).toBe(true);
    expect((await submitStudioAssetImageRequest({ ...owner(), ...original })).reason).toBe('studio_image_request_already_recorded');
    expect(net).toHaveBeenCalledTimes(1); expect(journals()).toHaveLength(1);
});

it.each(['local_unknown', 'global_partial'])(
    '%s keeps its actual incomplete receipt journal and cannot resubmit the recorded raw command', async name => {
        const original = command(name), raw = actual(name, 'accepted_post').request_body;
        const pending = beginStudioAssetImageRequest({ ...owner(), ...original });
        const readEvent = actual(name, 'result_get');
        net.mockImplementationOnce(async () => response(readEvent));
        const read = await readStudioAssetImageRequestReceipt({ ...owner(), journal: pending.journal });
        expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(readEvent.body);
        expect(read.receipt?.request.members.map(member => member.status)).toEqual(name === 'local_unknown'
            ? ['needs_confirmation', 'cancelled', 'cancelled', 'cancelled']
            : ['completed', 'needs_confirmation', 'cancelled', 'cancelled']);
        const storageKey = localStorage.key(0)!, bytes = localStorage.getItem(storageKey);
        expect(resolveStudioAssetImageRequest({ ...owner(), journal: read.journal, receipt: read.receipt! })).toBe(false);
        expect(localStorage.getItem(storageKey)).toBe(bytes); expect(journals()[0].request).toEqual(raw);
        if (name === 'local_unknown') {
            clearRuntimeEnvironment(); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
        }
        const repeat = actual(name, name === 'local_unknown' ? 'reopen_get' : 'result_get');
        net.mockImplementationOnce(async () => response(repeat));
        const recovered = await recoverStudioAssetImageRequests(owner());
        expect(recovered).toHaveLength(1); expect(recovered[0].receipt).toEqual(repeat.body);
        expect((await submitStudioAssetImageRequest({ ...owner(), ...original })).reason).toBe('studio_image_request_already_recorded');
        expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['GET', 'GET']);
        expect(net.mock.calls.every(([url]) => String(url) === owner().environment.apiUrl + readEvent.path)).toBe(true);
        expect(journals()).toHaveLength(1); expect(localStorage.getItem(storageKey)).toBe(bytes);
    });

it('reads the actual repaired receipt from the reopened app without a project GET or another POST', async () => {
    const original = command('restart_read_only'), pending = beginStudioAssetImageRequest({ ...owner(), ...original });
    expect(actual('restart_read_only', 'blocked_project_get').status_code).toBe(503);
    const repaired = actual('restart_read_only', 'result_get');
    net.mockImplementationOnce(async () => response(repaired));
    const recovered = await recoverStudioAssetImageRequests(owner());
    expect(recovered).toHaveLength(1); expect(recovered[0].status).toBe('confirmed');
    expect(recovered[0].receipt).toEqual(repaired.body);
    expect(recovered[0].receipt?.request.members[0].status).toBe('completed');
    expect(net).toHaveBeenCalledTimes(1);
    expect(String(net.mock.calls[0][0])).toBe(owner().environment.apiUrl + repaired.path);
    expect(net.mock.calls[0][1]?.method).toBe('GET');
    expect((await submitStudioAssetImageRequest({ ...owner(), ...original })).reason).toBe('studio_image_request_already_recorded');
    expect(net).toHaveBeenCalledTimes(1); expect(journals()[0].request).toEqual(pending.journal.request);
});

it('refuses a different actual request receipt without rewriting either capture or the original journal', async () => {
    const pending = beginStudioAssetImageRequest({ ...owner(), ...command() });
    const storageKey = localStorage.key(0)!, bytes = localStorage.getItem(storageKey);
    // Intentional mismatched response: the entire actual global receipt remains unchanged.
    net.mockImplementationOnce(async () => response(actual('global_single', 'result_get')));
    const wrong = await readStudioAssetImageRequestReceipt({ ...owner(), journal: pending.journal });
    expect(wrong.status).toBe('unknown'); expect(wrong.reason).toBe('studio_image_receipt_mismatch');
    expect(wrong.receipt).toBeUndefined(); expect(localStorage.getItem(storageKey)).toBe(bytes);
    const correct = actual('local_single', 'result_get');
    net.mockImplementationOnce(async () => response(correct));
    const read = await readStudioAssetImageRequestReceipt({ ...owner(), journal: journals()[0] });
    expect(read.status).toBe('confirmed'); expect(read.receipt).toEqual(correct.body);
    expect(journals()[0].request).toEqual(pending.journal.request);
    expect(net.mock.calls.map(([, init]) => init?.method)).toEqual(['GET', 'GET']);
    expect(net.mock.calls.every(([url]) => String(url) === owner().environment.apiUrl + correct.path)).toBe(true);
});
