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
import { ack, project } from './frame-command-fixtures';
import capture from '@/__tests__/fixtures/studio-motion-request-wire.json';
import duration5 from '@/__tests__/fixtures/studio-motion-duration5-wire.json';
import terminal from '@/__tests__/fixtures/studio-motion-terminal-followup-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Obj = Record<string, unknown>;
type Event = { label: string; body: Obj; request_body?: StudioMotionRequestBody; status_code: number };
type Capture = { cases: Array<{ case: string; events: Event[] }> };
function actual(name: string, label: string): Event {
    const all = [...(capture as unknown as Capture).cases, ...(duration5 as unknown as Capture).cases,
        ...(terminal as unknown as Capture).cases];
    const found = all.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing actual HTTP evidence: ${name}/${label}`);
    return structuredClone(found);
}
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected an object in the actual fixture');
    return value as Obj;
}
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const panel = () => screen.getAllByRole('group', { name: '1. 主体资产（全身图）' }).at(-1)!;
const generate = () => within(panel()).getByRole('button', { name: 'Generate Motion Reference' });
const journalBytes = () => Object.keys(localStorage).filter(key => key.startsWith('lumenx.studioMotionRequest.v1:'))
    .sort().map(key => ({ key, raw: localStorage.getItem(key) }));
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/project'));
let server: Obj;
function install(name = 'lifecycle_script_duration5', seed = true, projection = 'completed_enriched_project_projection') {
    const source = actual(name, projection).body;
    server = { ...ack([], 'project', source.revision as number), ...source,
        model_settings: { t2i_model: 'wan2.6-t2i', i2v_model: 'kling-v1' } };
    useProjectStore.setState({ currentProject: project('project', 0), projects: [project('project', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('project', server);
    if (seed) seedJournal(name);
}
function seedJournal(name: string, key?: string) {
    const { client_request_id, ...input } = actual(name, 'accepted_post').request_body!;
    const identity = obj(actual(name, 'accepted_post').body.request);
    return beginStudioMotionRequest({
        environment: activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' }),
        projectId: 'project', assetId: 'hero', assetOwner: { kind: identity.owner_kind, id: identity.owner_id } as StudioMotionAssetOwner,
        input, selectedImageId: 'selected-image', idFactory: () => key ?? client_request_id, now: () => key ? 8 : 7,
    });
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
    vi.mocked(axios.post).mockRejectedValue(new Error('Legacy generation is outside this completion flow'));
});
afterEach(() => { clearRuntimeEnvironment(); });

it.each(['provider_fetch_artifact_missing', 'provider_fetch_artifact_invalid'])(
    'does not resolve or retain a completed-looking receipt whose matched error and reason are %s', async reason => {
        install();
        // Explicit unavailable negative: preserve the actual complete proof while
        // making both public error fields agree, as a failed local file recheck can.
        const unavailable = actual('lifecycle_script_duration5', 'completed_get').body;
        unavailable.reason = reason; obj(unavailable.task).error = reason;
        vi.mocked(appAccessFetch).mockImplementation(async () => response(unavailable));
        page(); await waitFor(() => expect(vi.mocked(appAccessFetch)).toHaveBeenCalledTimes(1));
        await act(async () => {});
        expect(journalBytes()).toHaveLength(1); expect(generate()).toBeDisabled();
        expect(projectReads()).toHaveLength(0); expect(posts()).toHaveLength(0);
        expect(screen.queryByRole('button', { name: '保留为历史' })).not.toBeInTheDocument();
        expect(screen.queryByText('已保留为历史；当前编辑不变。')).not.toBeInTheDocument();
        expect(vi.mocked(axios.post)).not.toHaveBeenCalled();
    });
