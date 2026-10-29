// Actual GPT T2I HTTP captures; components, request client, hook and store stay real.
// Explicit negative aggregates and newer-store controls do not alter captured receipts.
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import { projectReadSnapshot } from '@/lib/frameCommandSnapshot';
import { clearLibraryRecoveries } from '@/lib/libraryReadback';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import AssetInspector from '@/components/library/AssetInspector';
import { useProjectStore } from '@/store/projectStore';
import { useToastStore } from '@/store/toastStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { deferred } from './frame-command-fixtures';
import ConsistencyVault from '../ConsistencyVault';
import CastWorkbenchModal, { activePolls } from '../cast/CastWorkbenchModal';
import capture from '@/__tests__/fixtures/studio-gpt-t2i-wire.json';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
type Obj = Record<string, unknown>;
type Event = { label: string; method: string; path: string; body: Obj; request_body?: Obj; status_code: number };
type CaseName = 'full_body_local_single' | 'scene_series_batch' | 'prop_global_single' | 'prop_global_partial';
type Capture = { cases: { case: string; kind: string; events: Event[] }[] };
const SID = '11111111-1111-4111-8111-111111111111';
const warning = '结果已保存，但最新素材读取失败，请再次查看。';
let activeCase: CaseName;
let server: Obj;
let receiptLabel: 'result_get' | 'reopen_get';
function obj(value: unknown): Obj {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid captured object');
    return value as Obj;
}
function rows(value: unknown): Obj[] {
    if (!Array.isArray(value)) throw new Error('Invalid captured collection');
    return value.map(obj);
}
function actual(label: string, name = activeCase): Event {
    const event = (capture as unknown as Capture).cases.find(item => item.case === name)?.events.find(item => item.label === label);
    if (!event) throw new Error(`Missing actual GPT HTTP sample: ${name}/${label}`);
    return structuredClone(event);
}
const command = () => obj(actual('accepted_post').request_body);
const rootId = () => String(actual('before_project').body.id);
const assetId = () => String(command().asset_id);
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: rootId() });
const pool = () => activeCase === 'full_body_local_single' ? 'characters' : activeCase === 'scene_series_batch' ? 'scenes' : 'props';
const asset = (snapshot: Obj) => obj(rows(snapshot[pool()]).find(item => item.id === assetId()));
const currentAsset = () => asset(obj(useProjectStore.getState().currentProject));
const members = () => rows(obj(actual('result_get').body.request).members);
const journals = () => listStudioAssetImageRequests(owner());
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const projectReads = () => vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith(actual('before_project').path));
const resultRegion = () => screen.getByRole('region', { name: '生成请求结果' });
function variantMap(value: unknown) {
    const variants = rows(value);
    expect(new Set(variants.map(item => item.id)).size).toBe(variants.length);
    return Object.fromEntries(variants.map(item => [String(item.id), item]));
}
function expectCompletedMembers(value: unknown) {
    const variants = variantMap(value);
    for (const member of members().filter(item => item.status === 'completed')) {
        expect(variants[String(member.variant_id)]).toMatchObject({ id: member.variant_id,
            url: member.output_ref, prompt_used: command().prompt });
    }
}
function expectOriginalReadOnly(count: number) {
    expect(gets()).toHaveLength(count);
    expect(gets().every(([url]) => String(url) === owner().environment.apiUrl + actual('result_get').path)).toBe(true);
    expect(posts()).toHaveLength(0);
    expect(axios.post).not.toHaveBeenCalled();
    expect(axios.delete).not.toHaveBeenCalled();
}
function install(name: CaseName, cached = true) {
    activeCase = name; receiptLabel = 'result_get'; server = actual('before_project').body;
    useProjectStore.setState({ currentProject: null, projects: [], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    if (cached) expect(useProjectStore.getState().applyProjectReadSnapshot(rootId(), structuredClone(server), true).status).toBe('applied');
    const { client_request_id, ...input } = command();
    const journal = beginStudioAssetImageRequest({ ...owner(), input: input as StudioAssetImageRequestInput,
        idFactory: () => String(client_request_id), now: () => 7 }).journal;
    expect(journal.request).toEqual(command());
    return journal;
}
function workbench() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('heading', { level: 3, name: String(asset(actual('before_project').body).name) }));
    return view;
}
function cast() {
    return renderWithIntl(<LightboxProvider><CastWorkbenchModal isOpen kind="scene" entityId={assetId()} onClose={() => {}} /></LightboxProvider>);
}
function propCard() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('button', { name: /Props/ }));
    return view;
}
function inspector() {
    const before = actual('before_project').body;
    const project = projectReadSnapshot(rootId(), before, owner().environment.key);
    const target = project.props.find(item => item.id === assetId())!;
    return renderWithIntl(<LightboxProvider><AssetInspector asset={target} type="props" sourceKind="project"
        sourceName={String(before.title)} sourceId={project.id} sourceProject={project} starred={!!target.starred}
        onClose={() => {}} onToggleStar={() => {}} /></LightboxProvider>);
}
function expectPartialInspector() {
    const panel = screen.getByRole('complementary');
    const expected = asset(server);
    expect(within(panel).getAllByAltText('变体')).toHaveLength(rows(obj(expected.image_asset).variants).length);
    expect(within(panel).getByAltText(String(expected.name))).toHaveAttribute('src',
        owner().environment.apiUrl + '/files/' + String(expected.image_url));
    const region = resultRegion();
    expect(within(region).getByText('已完成')).toBeInTheDocument();
    expect(within(region).getByText('待确认')).toBeInTheDocument();
    expect(within(region).getByText('本次未生成')).toBeInTheDocument();
    expect(within(region).getByAltText('结果 1')).toHaveAttribute('src',
        owner().environment.apiUrl + '/files/' + String(members()[0].output_ref));
    expect(screen.queryByText(warning)).not.toBeInTheDocument();
    expect(useProjectStore.getState().currentProject).toBeNull();
    expect(useProjectStore.getState().projects).toHaveLength(0);
}

beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); clearRuntimeEnvironment(); clearLibraryRecoveries();
    useToastStore.getState().clear(); vi.spyOn(window, 'alert').mockImplementation(() => {});
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
        request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}),
    } });
    vi.mocked(axios.get).mockImplementation(async url => {
        const value = String(url), environment = owner().environment;
        if (value.endsWith('/art_direction/presets')) return { data: [] };
        if (value === environment.apiUrl + actual('before_project').path) return { data: structuredClone(server) };
        const series = (capture as unknown as Capture).cases.find(item => item.case === activeCase)?.events.find(item => item.label === 'before_series');
        if (series && value === environment.apiUrl + series.path) return { data: structuredClone(series.body) };
        throw new Error(`Unexpected project/style route: ${value}`);
    });
    vi.mocked(axios.post).mockRejectedValue(new Error('Recovery must not mutate the project'));
    vi.mocked(axios.delete).mockRejectedValue(new Error('Recovery must not delete'));
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        if (init?.method !== 'GET') throw new Error('Recovery must only GET the original request');
        const event = actual(receiptLabel);
        expect(String(url)).toBe(owner().environment.apiUrl + event.path);
        return new Response(JSON.stringify(event.body), { status: event.status_code });
    });
});
afterEach(() => {
    cleanup(); activePolls.forEach(clearInterval); activePolls.clear();
    clearRuntimeEnvironment(); clearLibraryRecoveries(); vi.restoreAllMocks();
});

describe('GPT T2I actual HTTP readback through Studio entry points', () => {
    it('reads local full-body output into both histories without rewriting independent media', async () => {
        install('full_body_local_single'); const before = structuredClone(currentAsset());
        server = actual('after_project').body; const expected = asset(server); workbench();
        await waitFor(() => expect(journals()).toHaveLength(0));
        for (const field of ['full_body_asset', 'full_body', 'full_body_request_id', 'full_body_image_url', 'full_body_prompt', 'source']) {
            expect(currentAsset()[field]).toEqual(expected[field]);
        }
        const legacy = obj(currentAsset().full_body_asset), modern = obj(currentAsset().full_body);
        expect(variantMap(legacy.variants)).toEqual(variantMap(modern.image_variants));
        expectCompletedMembers(legacy.variants); expectCompletedMembers(modern.image_variants);
        expect(legacy.selected_id).toBe(members()[0].variant_id); expect(modern.selected_image_id).toBe(legacy.selected_id);
        for (const field of ['reference_sheet', 'reference_sheet_request_id', 'headshot_asset', 'head_shot', 'three_view_asset', 'three_views', 'voice_id']) {
            expect(currentAsset()[field]).toEqual(before[field]);
        }
        for (const field of ['video_variants', 'selected_video_id', 'video_prompt']) expect(modern[field]).toEqual(obj(before.full_body)[field]);
        expect(screen.queryByText(warning)).not.toBeInTheDocument();
        expect(projectReads()).toHaveLength(1); expectOriginalReadOnly(1);
    });

    it('reads both series scene outputs while preserving the original choice and independent fields', async () => {
        install('scene_series_batch'); const before = structuredClone(currentAsset());
        server = actual('after_project').body; const expected = asset(server); cast();
        await waitFor(() => expect(journals()).toHaveLength(0));
        expect(currentAsset()).toEqual(expected); expectCompletedMembers(obj(currentAsset().image_asset).variants);
        expect(obj(currentAsset().image_asset).selected_id).toBe(obj(before.image_asset).selected_id);
        expect(variantMap(obj(currentAsset().image_asset).variants)[String(obj(before.image_asset).selected_id)])
            .toEqual(variantMap(obj(before.image_asset).variants)[String(obj(before.image_asset).selected_id)]);
        expect(currentAsset().video_prompt).toBe(before.video_prompt);
        expect(currentAsset().image_request_id).toBe(obj(actual('result_get').body.request).id);
        const region = resultRegion();
        expect(within(region).getAllByText('已完成')).toHaveLength(2);
        expect(within(region).getByAltText('结果 1')).toBeInTheDocument();
        expect(within(region).getByAltText('结果 2')).toBeInTheDocument();
        expect(screen.queryByText(warning)).not.toBeInTheDocument();
        expect(projectReads()).toHaveLength(1); expectOriginalReadOnly(1);
    });

    it('reads the actual global prop single result through the Vault card', async () => {
        install('prop_global_single'); server = actual('after_project').body; const expected = asset(server); propCard();
        await waitFor(() => expect(journals()).toHaveLength(0));
        expect(currentAsset()).toEqual(expected); expectCompletedMembers(obj(currentAsset().image_asset).variants);
        expect(currentAsset().source).toBe('global');
        expect(obj(currentAsset().image_asset).selected_id).toBe(members()[0].variant_id);
        expect(currentAsset().image_request_id).toBe(obj(actual('result_get').body.request).id);
        expect(within(resultRegion()).getByAltText('结果 1')).toBeInTheDocument();
        expect(screen.queryByText(warning)).not.toBeInTheDocument();
        expect(projectReads()).toHaveLength(1); expectOriginalReadOnly(1);
    });

    it('keeps partial global results and the original key through Library Inspector reopen and explicit GET', async () => {
        const original = install('prop_global_partial', false);
        const before = asset(actual('before_project').body);
        server = actual('after_project').body;
        expect(members().map(member => member.status)).toEqual(['completed', 'needs_confirmation', 'cancelled']);
        expect(obj(asset(server).image_asset).selected_id).toBe(obj(before.image_asset).selected_id);
        const first = inspector();
        await waitFor(() => expect(screen.getAllByAltText('变体')).toHaveLength(2));
        await waitFor(() => expect(within(resultRegion()).getByRole('button', { name: '查看生成结果' })).toBeEnabled());
        expectPartialInspector(); expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        first.unmount(); server = actual('reopen_project').body; receiptLabel = 'reopen_get'; inspector();
        await waitFor(() => expect(gets()).toHaveLength(2));
        await waitFor(() => expect(screen.getAllByAltText('变体')).toHaveLength(2));
        await waitFor(() => expect(within(resultRegion()).getByRole('button', { name: '查看生成结果' })).toBeEnabled());
        expectPartialInspector();
        fireEvent.click(within(resultRegion()).getByRole('button', { name: '查看生成结果' }));
        await waitFor(() => expect(gets()).toHaveLength(3));
        await waitFor(() => expect(within(resultRegion()).getByRole('button', { name: '查看生成结果' })).toBeEnabled());
        expectPartialInspector(); expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expect(projectReads()).toHaveLength(3); expectOriginalReadOnly(3);
    });
});

describe('GPT completed receipts reject explicitly corrupted aggregate projections', () => {
    it('retains the full-body journal when only the actual aggregate request pointer is stale', async () => {
        const original = install('full_body_local_single'); const before = structuredClone(currentAsset());
        const genuine = actual('after_project').body; server = structuredClone(genuine);
        asset(server).full_body_request_id = before.full_body_request_id;
        expect(server.read_versions).toEqual(genuine.read_versions);
        expect(asset(server).source).toBe(asset(genuine).source);
        expect(asset(server).id).toBe(asset(genuine).id);
        expect(asset(server).full_body_asset).toEqual(asset(genuine).full_body_asset);
        expect(asset(server).full_body).toEqual(asset(genuine).full_body);
        workbench(); await screen.findByText(warning);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expect(currentAsset()).toEqual(before); expect(projectReads()).toHaveLength(1); expectOriginalReadOnly(1);
    });

    it('retains the series scene journal when the same owner and pointer omit one actual completed member', async () => {
        const original = install('scene_series_batch'); const before = structuredClone(currentAsset());
        const genuine = actual('after_project').body; server = structuredClone(genuine);
        const missing = members()[1].variant_id;
        obj(asset(server).image_asset).variants = rows(obj(asset(server).image_asset).variants).filter(variant => variant.id !== missing);
        expect(server.read_versions).toEqual(genuine.read_versions);
        for (const field of ['id', 'source', 'image_request_id', 'image_url']) expect(asset(server)[field]).toEqual(asset(genuine)[field]);
        expect(obj(asset(server).image_asset).selected_id).toBe(obj(before.image_asset).selected_id);
        cast(); await screen.findByText(warning);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expect(currentAsset()).toEqual(before); expect(projectReads()).toHaveLength(1); expectOriginalReadOnly(1);
    });

    it.each([
        ['partial empty selection with nonempty alias', 'prop_global_partial'],
        ['explicit null raw URL with a plausible display URL', 'prop_global_single'],
        ['duplicate target ID in another source', 'prop_global_single'],
        ['missing completed member in modern full-body history', 'full_body_local_single'],
    ] as const)('retains the original journal for %s', async (change, name) => {
        const original = install(name); const before = structuredClone(currentAsset());
        const genuine = actual('after_project').body; server = structuredClone(genuine);
        const target = asset(server), completed = members().find(member => member.status === 'completed')!;
        // Keep the actual public receipt untouched; corrupt only this aggregate relation.
        if (change === 'partial empty selection with nonempty alias') {
            expect(obj(target.image_asset).selected_id).toBeTruthy();
            expect(target.image_url).toBeTruthy();
            obj(target.image_asset).selected_id = null;
        } else if (change === 'explicit null raw URL with a plausible display URL') {
            const variant = rows(obj(target.image_asset).variants).find(value => value.id === completed.variant_id)!;
            expect(variant.url).toBe(completed.output_ref);
            variant.media_refs = { url: null };
        } else if (change === 'duplicate target ID in another source') {
            expect(target.source).toBe('global');
            server[pool()] = [...rows(server[pool()]), { ...structuredClone(target), source: 'episode' }];
            expect(rows(server[pool()]).filter(value => value.id === assetId())).toHaveLength(2);
            expect(rows(server[pool()]).filter(value => value.id === assetId() && value.source === 'global')).toHaveLength(1);
        } else {
            const modern = obj(target.full_body);
            modern.image_variants = rows(modern.image_variants).filter(value => value.id !== completed.variant_id);
            expect(target.full_body_asset).toEqual(asset(genuine).full_body_asset);
        }
        expect(server.id).toBe(genuine.id); expect(server.read_versions).toEqual(genuine.read_versions);
        if (name === 'full_body_local_single') workbench(); else propCard();
        await screen.findByText(warning);
        expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
        expect(currentAsset()).toEqual(before); expect(projectReads()).toHaveLength(1); expectOriginalReadOnly(1);
    });
});

describe('GPT actual project GET readback after a newer store projection wins', () => {
    it.each(['same request proof', 'old request pointer'] as const)(
        'preserves the newer store with %s and resolves only a still-proven receipt', async change => {
            const original = install('prop_global_single');
            const received = actual('after_project').body;
            const held = deferred<{ data: Obj }>();
            vi.mocked(axios.get).mockImplementation(url => {
                if (String(url).endsWith('/art_direction/presets')) return Promise.resolve({ data: [] });
                expect(String(url)).toBe(owner().environment.apiUrl + actual('after_project').path);
                return held.promise;
            });
            const view = propCard();
            try {
                await waitFor(() => expect(projectReads()).toHaveLength(1));
                expect(gets()).toHaveLength(1); expect(journals()).toHaveLength(1);
                expect(journals()[0].request).toEqual(original.request);
                // This is an explicit newer local-store control, not another captured HTTP response.
                const newer = structuredClone(received);
                newer.revision = Number(received.revision) + 1;
                obj(newer.read_versions).project = newer.revision;
                obj(newer.read_versions).library = Number(obj(received.read_versions).library) + 1;
                if (change === 'old request pointer') {
                    asset(newer).image_request_id = asset(actual('before_project').body).image_request_id;
                }
                act(() => {
                    expect(useProjectStore.getState().applyProjectReadSnapshot(rootId(), newer).status).toBe('applied');
                });
                const retained = useProjectStore.getState().currentProject;
                const cached = useProjectStore.getState().projects.find(project => project.id === rootId());
                expect(retained?.revision).toBe(newer.revision);
                expect(retained?.read_versions).toEqual(newer.read_versions);
                // Both rows release the same unchanged valid captured response and receipt.
                expect(received).toEqual(actual('after_project').body);
                await act(async () => { held.resolve({ data: structuredClone(received) }); });
                if (change === 'same request proof') {
                    await waitFor(() => expect(journals()).toHaveLength(0));
                    expect(screen.queryByText(warning)).not.toBeInTheDocument();
                } else {
                    await screen.findByText(warning);
                    expect(journals()).toHaveLength(1); expect(journals()[0].request).toEqual(original.request);
                }
                expect(useProjectStore.getState().currentProject).toBe(retained);
                expect(useProjectStore.getState().projects.find(project => project.id === rootId())).toBe(cached);
                expect(currentAsset()).toEqual(asset(newer));
                expect(useProjectStore.getState().currentProject?.read_versions).toEqual(newer.read_versions);
                expect(projectReads()).toHaveLength(1); expectOriginalReadOnly(1);
            } finally {
                view.unmount(); clearRuntimeEnvironment();
                await act(async () => { held.resolve({ data: structuredClone(received) }); });
            }
        },
    );
});
