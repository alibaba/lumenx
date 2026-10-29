// Synthetic transport sequences, NOT newly captured HTTP or provider evidence.
// Real ConsistencyVault/CharacterWorkbench, hook, journal, readback and store.
// The provider handle is intentionally absent from public wire; its proof needs
// the separately proposed backend capture before an end-to-end recovery claim.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import fixture from '@/__tests__/fixtures/studio-headshot-recovery-lifecycle.synthetic.json';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { project } from './frame-command-fixtures';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));

type Obj = Record<string, unknown>;
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid synthetic fixture object');
    return value as Obj;
}
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'p' });
const journals = () => listStudioAssetImageRequests(owner());
const currentActor = () => obj(useProjectStore.getState().currentProject?.characters.find(item => item.id === 'c'));
const panel = () => screen.getAllByRole('group', { name: '3. 头像（特写）' }).at(-1)!;
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/p'));
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
function authorFields(value: Obj) {
    const modernAuthor = { ...obj(value.head_shot) };
    delete modernAuthor.image_variants;
    return { name: value.name, description: value.description, source: value.source,
        reference_sheet: value.reference_sheet, full_body: value.full_body, full_body_asset: value.full_body_asset,
        full_body_prompt: value.full_body_prompt, three_views: value.three_views, three_view_asset: value.three_view_asset,
        three_view_prompt: value.three_view_prompt, headshot_prompt: value.headshot_prompt,
        headshot_updated_at: value.headshot_updated_at, headshot_image_url: value.headshot_image_url,
        avatar_url: value.avatar_url, voice_id: value.voice_id, is_consistent: value.is_consistent,
        selected_id: obj(value.headshot_asset).selected_id, modernAuthor };
}
function expectProjection(expected: Obj, before: Obj) {
    expect(currentActor().headshot_asset).toEqual(expected.headshot_asset);
    expect(currentActor().head_shot).toEqual(expected.head_shot);
    expect(currentActor().headshot_request_id).toBe(expected.headshot_request_id);
    expect(currentActor().status).toBe('completed');
    expect(authorFields(currentActor())).toEqual(authorFields(before));
}
function expectNoMutation() {
    expect(posts()).toHaveLength(0);
    expect(axios.post).not.toHaveBeenCalled();
    expect(axios.delete).not.toHaveBeenCalled();
}

beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment();
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}),
    } });
    vi.mocked(axios.post).mockRejectedValue(new Error('Receipt observation must not mutate'));
    vi.mocked(axios.delete).mockRejectedValue(new Error('Receipt observation must not delete'));
});
afterEach(() => { clearRuntimeEnvironment(); vi.restoreAllMocks(); });

describe('open headshot workbench observes retained recovery without another submission', () => {
    it.each(fixture.cases)('$label', async sample => {
        expect(fixture.provenance.kind).toBe('SYNTHETIC_TRANSPORT_SEQUENCE_NOT_HTTP_CAPTURE');
        const before = structuredClone(sample.before_project);
        const completed = structuredClone(sample.completed_project);
        const initialReceipt = structuredClone(sample.initial_receipt);
        const finalReceipt = structuredClone(sample.completed_receipt);
        const { client_request_id: key, ...input } = sample.request_body;
        const originalRequest = initialReceipt.request;
        const firstMember = originalRequest.members[0];
        // Both transport states retain the exact original request/attempt/member
        // identity; status/reason/output are the only public member differences.
        expect(finalReceipt.request).toEqual({ ...originalRequest, members: finalReceipt.request.members });
        expect(finalReceipt.request.members.map(({ attempt_id, variant_id }) => ({ attempt_id, variant_id })))
            .toEqual(originalRequest.members.map(({ attempt_id, variant_id }) => ({ attempt_id, variant_id })));
        expect(firstMember).toMatchObject({ status: 'needs_confirmation', reason: sample.initial_reason });
        expect(firstMember).not.toHaveProperty('output_ref');
        useProjectStore.setState({ currentProject: project('p', 0), projects: [project('p', 0)], currentSeries: null,
            readWatermarks: {}, generatingTasks: [], runningOps: {} });
        useProjectStore.getState().applyProjectReadSnapshot('p', before);
        const original = beginStudioAssetImageRequest({ ...owner(), input: input as StudioAssetImageRequestInput,
            idFactory: () => key, now: () => 7 }).journal;
        let server = before;
        let receipt: unknown = initialReceipt;
        vi.mocked(axios.get).mockImplementation(async url => {
            if (String(url).endsWith('/presets')) return { data: [] };
            if (String(url).endsWith('/projects/p')) return { data: structuredClone(server) };
            throw new Error(`Unexpected read URL: ${String(url)}`);
        });
        vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
            if (init?.method !== 'GET' || !String(url).endsWith('/asset_image_requests/' + key)) {
                throw new Error('Observation must GET the original request key');
            }
            return response(structuredClone(receipt));
        });
        const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
        fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Alice' }));
        try {
            await within(panel()).findByText('待确认');
            await waitFor(() => expect(gets()).toHaveLength(1));
            expect(projectReads()).toHaveLength(0);
            expect(journals()).toHaveLength(1);
            expect(journals()[0].request).toEqual(original.request);
            const retainedIdentity = journals()[0].receiptIdentity;
            expect(retainedIdentity).toBeTruthy();
            expect(currentActor().headshot_asset).toEqual(before.characters[0].headshot_asset);
            expect(authorFields(currentActor())).toEqual(authorFields(before.characters[0]));
            expectNoMutation();

            // Simulated backend completion becomes available only after the
            // first unresolved GET. No remount, click, prop/store update or POST
            // triggers the interval under test. Real timers preserve Workbench
            // animation and effects; the wait spans its existing 2500 ms timer.
            server = completed;
            receipt = finalReceipt;
            await act(async () => { await new Promise<void>(resolve => setTimeout(resolve, 3100)); });
            const automatic = { gets: gets().length, journalCount: journals().length,
                actor: structuredClone(currentActor()), projectReads: projectReads().length };

            // Diagnostic positive control: if automatic observation stalled,
            // explicit GET must accept the SAME completion. This never replaces
            // the automatic assertions below or turns their RED into a pass.
            if (automatic.journalCount) {
                expect(journals()[0].receiptIdentity).toEqual(retainedIdentity);
                expect(journals()[0].request).toEqual(original.request);
                fireEvent.click(within(panel()).getByRole('button', { name: '查看生成结果' }));
                await waitFor(() => expect(journals()).toHaveLength(0));
            }
            expectProjection(completed.characters[0], before.characters[0]);
            expect(gets().every(([url]) => String(url).endsWith('/asset_image_requests/' + key))).toBe(true);
            expectNoMutation();
            expect(obj(currentActor().headshot_asset).selected_id).toBe(sample.selection);
            expect(obj(currentActor().head_shot).selected_image_id).toBe(sample.selection);
            const variants = obj(currentActor().headshot_asset).variants as Obj[];
            expect(variants.filter(value => value.id === firstMember.variant_id)).toHaveLength(1);
            expect(variants.slice(0, -1)).toEqual(before.characters[0].headshot_asset.variants);
            expect(automatic.gets, 'an open workbench must GET the original known recovery after its first needs_confirmation receipt').toBe(2);
            expect(automatic.projectReads).toBe(1);
            expect(automatic.journalCount).toBe(0);
            expect(automatic.actor.headshot_asset).toEqual(completed.characters[0].headshot_asset);
            expect(automatic.actor.head_shot).toEqual(completed.characters[0].head_shot);
            expect(authorFields(automatic.actor)).toEqual(authorFields(before.characters[0]));
        } finally { view.unmount(); }
    }, 10000);
});
