import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import type { StudioMotionAssetOwner, StudioMotionRequestBody } from '@/lib/api';
import { beginStudioMotionRequest } from '@/lib/studioMotionRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred, project } from './frame-command-fixtures';
import legacy from '@/__tests__/fixtures/studio-motion-legacy-eligibility-wire.json';
import unknown from '@/__tests__/fixtures/studio-motion-request-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Obj = Record<string, unknown>;
type Event = { label: string; body: Obj; request_body?: StudioMotionRequestBody; status_code: number };
type Capture = { cases: Array<{ case: string; events: Event[] }> };
function actual(name: string, label: string): Event {
    const cases = [...(legacy as unknown as Capture).cases, ...(unknown as unknown as Capture).cases];
    const event = cases.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!event) throw new Error(`Missing actual HTTP evidence: ${name}/${label}`);
    return structuredClone(event);
}
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected actual HTTP object');
    return value as Obj;
}
const SID = '11111111-1111-4111-8111-111111111111';
const AUTHOR_PROMPT = 'Keep this authored movement on the allowed legacy route.';
const response = (event: Event) => new Response(JSON.stringify(event.body), { status: event.status_code });
const panel = () => screen.getAllByRole('group', { name: '1. 主体资产（全身图）' }).at(-1)!;
const generate = () => within(panel()).getByRole('button', { name: 'Generate Motion Reference' });
const prompt = () => within(panel()).getByPlaceholderText('Describe the motion you want...');
const legacyPosts = () => vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/assets/generate_motion_ref'));
const strictPosts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/project'));
const eligibilityReads = () => vi.mocked(appAccessFetch).mock.calls.filter(([url, init]) => init?.method === 'GET'
    && new URL(String(url), 'http://localhost').pathname.endsWith('/motion_ref_requests'));
const journalBytes = () => Object.keys(localStorage).filter(key => key.startsWith('lumenx.studioMotionRequest.v1:'))
    .sort().map(key => ({ key, raw: localStorage.getItem(key) }));
let server: Obj;
let heldLegacy: ReturnType<typeof deferred<{ data: Obj }>>;
let lockFinished: ReturnType<typeof deferred<void>>;
function seedOriginalUnknown() {
    const accepted = actual('unknown_submit_ack', 'accepted_before_lost_ack');
    const { client_request_id, ...input } = accepted.request_body!;
    const identity = obj(accepted.body.request);
    return beginStudioMotionRequest({
        environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }),
        projectId: 'project', assetId: 'hero', assetOwner: { kind: identity.owner_kind, id: identity.owner_id } as StudioMotionAssetOwner,
        input, selectedImageId: 'selected-image', idFactory: () => client_request_id, now: () => 7,
    }).journal;
}
function page() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Hero' }));
    fireEvent.click(within(panel()).getByRole('button', { name: '动态' }));
    fireEvent.change(prompt(), { target: { value: AUTHOR_PROMPT } });
    return view;
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    server = actual('legacy_model_eligibility_read_only', 'legacy_enriched_project').body;
    useProjectStore.setState({ currentProject: project('project', 0), projects: [project('project', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('project', structuredClone(server));
    heldLegacy = deferred<{ data: Obj }>(); lockFinished = deferred<void>();
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: vi.fn(async (_name: string, _options: unknown, callback: (lock: object) => unknown) => {
            try { return await callback({}); } finally { lockFinished.resolve(); }
        }),
    } });
    vi.mocked(axios.get).mockImplementation(async url => {
        if (String(url).endsWith('/presets')) return { data: [] };
        if (String(url).endsWith('/projects/project')) return { data: structuredClone(server) };
        throw new Error(`Unexpected project transport: ${String(url)}`);
    });
    // Only observe dispatch. A held legacy POST makes no claim about a provider ACK or completion.
    vi.mocked(axios.post).mockImplementation(async url => {
        if (!String(url).endsWith('/assets/generate_motion_ref')) throw new Error(`Unexpected legacy mutation: ${String(url)}`);
        return heldLegacy.promise;
    });
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        if (init?.method !== 'GET') throw new Error('This legacy case must never submit through the strict route');
        const path = new URL(String(url), 'http://localhost').pathname;
        if (path.endsWith('/motion_ref_requests')) return response(actual('legacy_model_eligibility_read_only', 'legacy_model_eligibility'));
        if (path.endsWith('/motion_ref_requests/motion-request')) return response(actual('unknown_submit_ack', 'unknown_original_get'));
        throw new Error(`Unexpected strict transport: ${path}`);
    });
});
afterEach(async () => {
    clearRuntimeEnvironment();
    if (legacyPosts().length) await act(async () => { heldLegacy.reject(new Error('Test ended with legacy transport still held')); });
    vi.restoreAllMocks();
});

it('dispatches exactly one allowed legacy request after the actual model-unsupported eligibility read', async () => {
    page(); await waitFor(() => expect(generate()).not.toBeDisabled());
    fireEvent.click(generate());
    await act(async () => { await lockFinished.promise; });
    expect(legacyPosts()).toHaveLength(1);
    expect(vi.mocked(axios.post)).toHaveBeenCalledTimes(1);
    expect(legacyPosts()[0][1]).toEqual({ asset_id: 'hero', asset_type: 'full_body', prompt: AUTHOR_PROMPT,
        audio_url: '', duration: 5, batch_size: 1 });
    expect(projectReads()).toHaveLength(1); expect(eligibilityReads()).toHaveLength(1);
    expect(strictPosts()).toHaveLength(0); expect(journalBytes()).toHaveLength(0);
    expect(prompt()).toHaveValue(AUTHOR_PROMPT); expect(generate()).toBeDisabled();
    expect(useProjectStore.getState().currentProject?.characters[0].full_body?.selected_video_id).toBe('selected-video');
});

it('blocks allowed legacy dispatch when the original unknown journal arrives during project preparation', async () => {
    const heldRead = deferred<{ data: Obj }>();
    vi.mocked(axios.get).mockImplementation(async url => {
        if (String(url).endsWith('/presets')) return { data: [] };
        if (String(url).endsWith('/projects/project')) return heldRead.promise;
        throw new Error(`Unexpected project transport: ${String(url)}`);
    });
    page(); await waitFor(() => expect(generate()).not.toBeDisabled());
    fireEvent.click(generate()); await waitFor(() => expect(projectReads()).toHaveLength(1));
    expect(eligibilityReads()).toHaveLength(0); expect(legacyPosts()).toHaveLength(0);
    const original = seedOriginalUnknown(), originalBytes = journalBytes();
    expect(original.request).toEqual(actual('unknown_submit_ack', 'accepted_before_lost_ack').request_body);
    expect(originalBytes).toHaveLength(1);
    await act(async () => { heldRead.resolve({ data: structuredClone(server) }); await lockFinished.promise; });
    // These are the primary product assertions: no second paid operation and no loss of the old key.
    expect(legacyPosts()).toHaveLength(0); expect(strictPosts()).toHaveLength(0);
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled(); expect(journalBytes()).toEqual(originalBytes);
    expect(eligibilityReads()).toHaveLength(1);
    await within(panel()).findByText('原任务待确认，请先查看结果，避免重复生成。');
    expect(generate()).toBeDisabled(); expect(prompt()).toHaveValue(AUTHOR_PROMPT);
    expect(useProjectStore.getState().currentProject?.characters[0].full_body?.selected_video_id).toBe('selected-video');
});
