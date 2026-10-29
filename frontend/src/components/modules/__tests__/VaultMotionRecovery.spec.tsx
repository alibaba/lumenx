import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import type { StudioMotionRequestBody, StudioMotionReceipt } from '@/lib/api';
import { beginStudioMotionRequest } from '@/lib/studioMotionRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, project } from './frame-command-fixtures';
import capture from '@/__tests__/fixtures/studio-motion-request-wire.json';
import duration5 from '@/__tests__/fixtures/studio-motion-duration5-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Event = { label: string; body: Record<string, unknown>; request_body?: StudioMotionRequestBody; status_code: number };
type Capture = { cases: Array<{ case: string; events: Event[] }> };
function actual(name: string, label: string): Event {
    const all = [...(capture as unknown as Capture).cases, ...(duration5 as unknown as Capture).cases];
    const found = all.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual HTTP evidence: ${name}/${label}`);
    return structuredClone(found);
}
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const panel = () => screen.getAllByRole('group', { name: '1. 主体资产（全身图）' }).at(-1)!;
let server: Record<string, unknown>;
function install(name: string, admission = 'accepted_post', projection = 'completed_enriched_project_projection') {
    const source = actual(name.startsWith('lifecycle_') ? name : 'lifecycle_script', projection).body;
    server = { ...ack([], 'project', source.revision as number), ...source,
        model_settings: { t2i_model: 'wan2.6-t2i', i2v_model: 'kling-v1' } };
    useProjectStore.setState({ currentProject: project('project', 0), projects: [project('project', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('project', server);
    const { client_request_id, ...input } = actual(name, admission).request_body!;
    beginStudioMotionRequest({ environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' }),
        projectId: 'project', assetId: 'hero', assetOwner: { kind: 'script', id: 'project' }, input,
        selectedImageId: 'selected-image', idFactory: () => client_request_id, now: () => 7 });
}
function page() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Hero' }));
    fireEvent.click(within(panel()).getByRole('button', { name: '动态' }));
    return view;
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}),
    } });
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : server }));
    vi.mocked(axios.post).mockRejectedValue(new Error('Legacy generation is outside recovery'));
});
afterEach(() => { clearRuntimeEnvironment(); });

it('continues an accepted original request only on explicit click and reads its original key afterward', async () => {
    const name = 'lifecycle_script_duration5'; install(name);
    const accepted = actual(name, 'accepted_post').body as unknown as StudioMotionReceipt;
    const submitted = actual(name, 'submitted_get').body as unknown as StudioMotionReceipt;
    let continued = false;
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        if (init?.method === 'POST') { continued = true; return response(submitted.task); }
        expect(String(url)).toMatch(/\/motion_ref_requests\/motion-request$/);
        return response(continued ? submitted : accepted);
    });
    page(); const button = await screen.findByRole('button', { name: '继续原任务' });
    expect(posts()).toHaveLength(0); fireEvent.click(button);
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(String(posts()[0][0])).toMatch(new RegExp(`/provider_video_tasks/${accepted.task_id}/continue$`));
    expect(JSON.parse(String(posts()[0][1]?.body))).toEqual({ expected_attempt_id: accepted.request.attempt_id,
        expected_fingerprint: accepted.request.execution_fingerprint });
    await waitFor(() => expect(vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET').length).toBeGreaterThanOrEqual(3));
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    expect(within(panel()).getByRole('button', { name: 'Generate Motion Reference' })).toBeDisabled();
});
it('recovers a credential-blocked original handle through the public key command without submitting again', async () => {
    const name = 'lifecycle_script'; install(name);
    const pending = actual(name, 'restored_credential_read_only_get').body as unknown as StudioMotionReceipt;
    const completed = actual(name, 'completed_recover').body as unknown as StudioMotionReceipt;
    let recovered = false;
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        if (init?.method === 'POST') { recovered = true; return response(completed); }
        expect(String(url)).toMatch(/\/motion_ref_requests\/motion-request$/);
        return response(recovered ? completed : pending);
    });
    page(); const button = await screen.findByRole('button', { name: '恢复原任务' });
    expect(posts()).toHaveLength(0); fireEvent.click(button);
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(String(posts()[0][0])).toMatch(/\/motion_ref_requests\/motion-request\/recover$/);
    expect(JSON.parse(String(posts()[0][1]?.body))).toEqual({ expected_attempt_id: pending.request.attempt_id,
        expected_fingerprint: pending.request.execution_fingerprint });
    await waitFor(() => expect(vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET').length).toBeGreaterThanOrEqual(3));
    expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    expect(posts().some(([url]) => String(url).endsWith('/motion_ref_requests'))).toBe(false);
});
it('keeps a no-handle submission outcome unknown with neither continue nor recover', async () => {
    install('unknown_submit_ack', 'accepted_before_lost_ack');
    const unknown = actual('unknown_submit_ack', 'unknown_original_get');
    vi.mocked(appAccessFetch).mockResolvedValue(response(unknown.body)); page();
    await waitFor(() => expect(vi.mocked(appAccessFetch)).toHaveBeenCalled()); await act(async () => {});
    expect(screen.queryByRole('button', { name: '继续原任务' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '恢复原任务' })).not.toBeInTheDocument();
    expect(within(panel()).getByRole('button', { name: 'Generate Motion Reference' })).toBeDisabled();
    expect(posts()).toHaveLength(0); expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
});
