import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { NextIntlClientProvider } from 'next-intl';
import zhMessages from '../../../../messages/zh.json';
import enMessages from '../../../../messages/en.json';
import { useStudioAssetImageRequests, usesStudioImageCore } from '@/lib/useStudioAssetImageRequests';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests } from '@/lib/studioAssetImageRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore } from '@/store/projectStore';
import { useToastStore } from '@/store/toastStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from './frame-command-fixtures';
import CastWorkbenchModal, { activePolls, type CastKind } from '../cast/CastWorkbenchModal';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
vi.mock('@/components/common/GroupedModelGrid', () => ({ default: () => null }));
const SID = '11111111-1111-4111-8111-111111111111';
const models = ['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'];
const kinds = ['scene', 'prop'] as const;
const owner = (rootId = 'a') => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId });
const body = (kind: 'scene' | 'prop', assetId = 'shared') => ({ asset_id: assetId, asset_type: kind, generation_type: 'all' as const, batch_size: 2 });
function snapshot({ id = 'a', revision = 5, source = 'global', seriesId = 'series-a', negative = '', model = 'wan2.7-image', selected = 'old', library = 9 } = {}) {
    const asset = (kind: string) => ({ id: 'shared', name: `${kind} ${id}`, description: `${kind} description`, source,
        image_asset: { selected_id: selected, variants: [{ id: 'old', url: '/old.png', is_favorited: true }, { id: 'other', url: '/other.png' }] },
        video_url: '/keep.mp4', video_prompt: 'keep video', voice_id: 'keep voice' });
    return enriched({ ...ack([], id, revision), series_id: source === 'series' ? seriesId : null,
        model_settings: { t2i_model: model },
        art_direction: negative ? { style_config: { name: 'Saved style', positive_prompt: 'soft light', negative_prompt: negative } } : null,
        characters: [{ ...asset('character'), reference_sheet: { selected_image_id: 'old', image_variants: [{ id: 'old', url: '/old.png' }] } }],
        scenes: [asset('scene')], props: [asset('prop')] }, library, 1);
}
function install(options: Parameters<typeof snapshot>[0] = {}) {
    const value = snapshot({ ...options, revision: options.revision ?? (useProjectStore.getState().currentProject?.revision ?? 3) + 1 });
    useProjectStore.getState().applyProjectReadSnapshot(value.id, value);
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : value }));
    return value;
}
// Synthetic strict public wire; actual backend capture parity is a separate L2c check.
function wire(kind: CastKind, key: string, statuses = ['needs_confirmation', 'needs_confirmation'], root = 'a') {
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: true, reason: null, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: kind === 'character' ? 'studio.asset.reference_sheet.v1' : 'studio.asset.scene_prop.v1', request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: root,
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: null,
        owner_kind: 'global', owner_id: 'library', asset_type: kind, asset_id: 'shared', channel: kind === 'character' ? 'reference_sheet' : 'image',
        members: statuses.map((status, i) => ({ variant_id: `${key}-${i}`, attempt_id: `${key}-${i}`, status, reason: null,
            ...(status === 'completed' ? { output_ref: `assets/studio/${key}-${i}.png` } : {}) })),
    } };
}
// Only explicit successful reads acquire completed-member proof; negative snapshots stay unchanged.
function projectedSnapshot(kind: 'scene' | 'prop', key: string = kind, statuses = ['completed', 'completed'], options: Parameters<typeof snapshot>[0] = {}) {
    const receipt = wire(kind, key, statuses), value = snapshot(options);
    const asset = value[kind === 'scene' ? 'scenes' : 'props'][0];
    Object.assign(asset, { image_request_id: receipt.request.id,
        image_url: asset.image_asset.variants.find(variant => variant.id === asset.image_asset.selected_id)?.url });
    asset.image_asset.variants.unshift(...receipt.request.members.flatMap(member => member.status === 'completed' && member.output_ref
        ? [{ id: member.variant_id, url: member.output_ref, is_favorited: false }] : []));
    return value;
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const seed = (kind: CastKind, key: string = kind, rootId = 'a') => beginStudioAssetImageRequest({ ...owner(rootId), input: kind === 'character' ? { asset_id: 'shared', batch_size: 2 } : body(kind), idFactory: () => key }).journal;
const element = (kind: CastKind, isOpen = true) => <LightboxProvider><CastWorkbenchModal isOpen={isOpen} kind={kind} entityId="shared" onClose={() => {}} /></LightboxProvider>;
const page = (kind: CastKind) => renderWithIntl(element(kind));
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const hookProps = (kind: CastKind, projectId = 'a', source = 'global', seriesId: string | null = null) => ({ open: true, kind, projectId, assetId: 'shared', source, seriesId });

beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); useToastStore.getState().clear();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ projects: [project(), project('b')], currentProject: project(), readWatermarks: {}, currentSeries: null, generatingTasks: [] });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        const request = init?.method === 'POST' ? JSON.parse(String(init.body)) : null;
        const key = request?.client_request_id ?? String(url).split('/').at(-1)!;
        return response(wire(request?.asset_type ?? key, key));
    });
    install();
});
afterEach(() => { activePolls.forEach(clearInterval); activePolls.clear(); vi.useRealTimers(); clearRuntimeEnvironment(); });

describe('scene/prop hook identity and authoritative readback', () => {
    it('allows only the closed kind/channel tuples and exact model IDs', () => {
        for (const model of models) {
            expect(usesStudioImageCore('character', model, 'reference_sheet')).toBe(true);
            for (const kind of kinds) expect(usesStudioImageCore(kind, model, 'image')).toBe(true);
            for (const [kind, channel] of [['character', 'image'], ['scene', 'reference_sheet'], ['prop', 'all'], ['unknown', 'image']]) {
                expect(usesStudioImageCore(kind, model, channel)).toBe(false);
            }
        }
        expect(usesStudioImageCore('scene', 'qwen-image-2.0', 'image')).toBe(true);
    });
    it.each(['character', ...kinds] as const)('reads only the %s journal for a shared asset ID without rewriting omitted legacy keys', async kind => {
        seed('character'); seed('scene'); seed('prop');
        const hook = renderHook(() => useStudioAssetImageRequests(hookProps(kind)));
        await waitFor(() => expect(hook.result.current.results).toHaveLength(1));
        expect(hook.result.current.results[0].journal.request.client_request_id).toBe(kind);
        expect(gets()).toHaveLength(1); expect(String(gets()[0][0])).toMatch(new RegExp(`/${kind}$`));
        const old = listStudioAssetImageRequests(owner()).find(item => item.request.client_request_id === 'character')!;
        expect(old.request).not.toHaveProperty('asset_type'); expect(old.request).not.toHaveProperty('generation_type');
    });
    it.each([body('prop'), { asset_id: 'shared', batch_size: 2 }, body('scene', 'other')])('rejects wrong hook target before a journal or POST: %j', async input => {
        const hook = renderHook(() => useStudioAssetImageRequests(hookProps('scene')));
        await act(async () => { await hook.result.current.submit(input); });
        expect(hook.result.current.submitFailed).toBe(true); expect(posts()).toHaveLength(0);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
    });
    it.each(kinds)('reads the %s collection and preserves server choice, favorite and independent media', async kind => {
        seed(kind); const value = projectedSnapshot(kind, kind, ['completed', 'cancelled'], { revision: 6 });
        const asset = value[kind === 'scene' ? 'scenes' : 'props'][0];
        vi.mocked(axios.get).mockResolvedValue({ data: { ...value, characters: [] } });
        vi.mocked(appAccessFetch).mockResolvedValue(response(wire(kind, kind, ['completed', 'cancelled'])));
        const hook = renderHook(() => useStudioAssetImageRequests(hookProps(kind)));
        await waitFor(() => expect(hook.result.current.results).toHaveLength(1)); await act(async () => {});
        expect(hook.result.current.readbackFailed).toBe(false); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
        expect(useProjectStore.getState().currentProject?.[kind === 'scene' ? 'scenes' : 'props'][0]).toMatchObject(asset);
        expect(posts()).toHaveLength(0);
    });
    it.each(['failure', 'older-version'] as const)('keeps the terminal journal after %s readback until an original GET succeeds', async issue => {
        seed('scene'); vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('scene', 'scene', ['completed', 'cancelled'])));
        if (issue === 'failure') vi.mocked(axios.get).mockRejectedValue(Error('offline'));
        else vi.mocked(axios.get).mockResolvedValue({ data: snapshot({ revision: 6, selected: 'other', library: 8 }) });
        const hook = renderHook(() => useStudioAssetImageRequests(hookProps('scene')));
        await waitFor(() => expect(hook.result.current.readbackFailed).toBe(true));
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(useProjectStore.getState().currentProject?.scenes[0].image_asset?.selected_id).toBe('old');
        vi.mocked(axios.get).mockResolvedValue({ data: projectedSnapshot('scene', 'scene', ['completed', 'cancelled'], { revision: 7, library: 10 }) });
        await act(async () => { await hook.result.current.refresh(); });
        expect(hook.result.current.readbackFailed).toBe(false); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
        expect(posts()).toHaveLength(0); expect(gets()).toHaveLength(2);
    });
    it('drops a delayed scene result across scene to prop to scene instead of clearing either journal', async () => {
        seed('scene'); seed('prop'); const old = deferred<Response>();
        vi.mocked(appAccessFetch).mockReturnValueOnce(old.promise).mockImplementation(async url => response(wire(String(url).endsWith('/prop') ? 'prop' : 'scene', String(url).split('/').at(-1)!)));
        const hook = renderHook(props => useStudioAssetImageRequests(props), { initialProps: hookProps('scene') });
        await waitFor(() => expect(gets()).toHaveLength(1)); hook.rerender(hookProps('prop'));
        await waitFor(() => expect(hook.result.current.results[0]?.journal.request.client_request_id).toBe('prop'));
        hook.rerender(hookProps('scene')); await waitFor(() => expect(gets()).toHaveLength(3));
        await act(async () => old.resolve(response(wire('scene', 'scene', ['completed', 'completed']))));
        expect(hook.result.current.results).toHaveLength(1);
        expect(hook.result.current.results[0].receipt?.request.members[0].status).toBe('needs_confirmation');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(2); expect(axios.get).not.toHaveBeenCalled();
    });
    it.each((['character', ...kinds] as const).flatMap(kind => ['global-to-episode', 'episode-to-global', 'series-to-series'].map(move => [kind, move] as const)))(
        'retains a terminal %s receipt after its original owner changes: %s', async (kind, move) => {
            seed(kind);
            const source = move === 'global-to-episode' ? 'episode' : move === 'episode-to-global' ? 'global' : 'series';
            install({ source, seriesId: 'series-b' });
            const receipt = wire(kind, kind, ['completed', 'cancelled']);
            if (move === 'episode-to-global') Object.assign(receipt.request, { owner_kind: 'script', owner_id: 'a' });
            if (move === 'series-to-series') Object.assign(receipt.request, { owner_kind: 'series', owner_id: 'series-a', request_root_series_id: 'series-a' });
            vi.mocked(appAccessFetch).mockImplementation(async () => response(receipt));
            const hook = renderHook(() => useStudioAssetImageRequests(hookProps(kind, 'a', source, source === 'series' ? 'series-b' : null)));
            await waitFor(() => expect(hook.result.current.results).toHaveLength(1)); await act(async () => {});
            expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
            expect(hook.result.current.results[0].receipt?.request.owner_id).toBe(receipt.request.owner_id);
            expect(axios.get).not.toHaveBeenCalled(); expect(posts()).toHaveLength(0);
            expect(useProjectStore.getState().currentProject?.[kind === 'character' ? 'characters' : kind === 'scene' ? 'scenes' : 'props'][0].source).toBe(source);
        },
    );
    it('resolves only current-owner receipts when old and current owners share kind and ID', async () => {
        seed('scene', 'old-owner'); seed('scene', 'current-owner'); install({ source: 'episode' });
        vi.mocked(appAccessFetch).mockImplementation(async url => {
            const key = String(url).split('/').at(-1)!;
            const receipt = wire('scene', key, ['completed', 'cancelled']);
            if (key === 'current-owner') Object.assign(receipt.request, { owner_kind: 'script', owner_id: 'a' });
            return response(receipt);
        });
        vi.mocked(axios.get).mockResolvedValue({ data: projectedSnapshot('scene', 'current-owner', ['completed', 'cancelled'], { source: 'episode', revision: 6 }) });
        const hook = renderHook(() => useStudioAssetImageRequests(hookProps('scene', 'a', 'episode')));
        await waitFor(() => expect(hook.result.current.results).toHaveLength(2)); await act(async () => {});
        expect(listStudioAssetImageRequests(owner()).map(item => item.request.client_request_id)).toEqual(['old-owner']);
        expect(axios.get).toHaveBeenCalledOnce(); expect(posts()).toHaveLength(0);
    });
    it('keeps a newer chosen image when a late terminal project read is older', async () => {
        seed('scene'); const delayed = deferred<{ data: ReturnType<typeof snapshot> }>();
        vi.mocked(axios.get).mockReturnValue(delayed.promise);
        vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('scene', 'scene', ['completed', 'completed'])));
        const hook = renderHook(() => useStudioAssetImageRequests(hookProps('scene')));
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        act(() => { useProjectStore.getState().applyProjectReadSnapshot('a', projectedSnapshot('scene', 'scene', ['completed', 'completed'], { revision: 7, library: 10, selected: 'other' })); });
        await act(async () => delayed.resolve({ data: projectedSnapshot('scene', 'scene', ['completed', 'completed'], { revision: 6, library: 9 }) }));
        // The newer confirmed project dominates this response; it is valid readback without overwriting the choice.
        expect(hook.result.current.readbackFailed).toBe(false);
        expect(useProjectStore.getState().currentProject?.scenes[0].image_asset?.selected_id).toBe('other');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(0); expect(posts()).toHaveLength(0);
    });
    it('drops a late receipt after project A to B to A and reads only the original current key', async () => {
        seed('scene'); const delayed = deferred<Response>();
        vi.mocked(appAccessFetch).mockReturnValueOnce(delayed.promise).mockImplementation(async () => response(wire('scene', 'scene')));
        const hook = renderHook(props => useStudioAssetImageRequests(props), { initialProps: hookProps('scene') });
        await waitFor(() => expect(gets()).toHaveLength(1)); hook.rerender(hookProps('scene', 'b'));
        await act(async () => {}); hook.rerender(hookProps('scene'));
        await waitFor(() => expect(hook.result.current.results).toHaveLength(1));
        await act(async () => delayed.resolve(response(wire('scene', 'scene', ['completed', 'completed']))));
        expect(hook.result.current.results[0].receipt?.request.members[0].status).toBe('needs_confirmation');
        expect(gets()).toHaveLength(2); expect(axios.get).not.toHaveBeenCalled(); expect(posts()).toHaveLength(0);
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
    });
    it.each(['root', 'series', 'activation'] as const)('drops a late terminal readback across %s changes', async change => {
        if (change === 'series') install({ source: 'series' });
        seed('scene'); const readback = deferred<{ data: ReturnType<typeof snapshot> }>();
        vi.mocked(axios.get).mockReturnValue(readback.promise);
        const original = wire('scene', 'scene', ['completed', 'completed']);
        if (change === 'series') Object.assign(original.request, { owner_kind: 'series', owner_id: 'series-a', request_root_series_id: 'series-a' });
        vi.mocked(appAccessFetch).mockResolvedValueOnce(response(original)).mockImplementation(async () => response(wire('scene', 'scene')));
        const hook = renderHook(props => useStudioAssetImageRequests(props), { initialProps: hookProps('scene', 'a', change === 'series' ? 'series' : 'global', change === 'series' ? 'series-a' : null) });
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        if (change === 'root') hook.rerender(hookProps('scene', 'b'));
        else if (change === 'series') hook.rerender(hookProps('scene', 'a', 'series', 'series-b'));
        else act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        // New view cannot resolve the old operation; keep its separate reads unresolved.
        await act(async () => readback.resolve({ data: snapshot({ revision: 7, source: change === 'series' ? 'series' : 'global', selected: 'other' }) }));
        expect(useProjectStore.getState().currentProject?.scenes[0].image_asset?.selected_id).toBe('old');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); expect(posts()).toHaveLength(0);
    });
});

describe('actual Cast scene/prop entry', () => {
    it.each(kinds.flatMap(kind => (['zh', 'en'] as const).map(locale => [kind, locale] as const)))('describes unset art direction truthfully for %s in %s without changing the request', async (kind, locale) => {
        const messages = locale === 'zh' ? zhMessages : enMessages;
        const expected = locale === 'zh'
            ? '未设置风格定调；生成仍可能应用项目或默认风格。'
            : 'No art direction set; generation may still apply the project or default style.';
        install();
        render(<NextIntlClientProvider locale={locale} messages={messages} timeZone="Asia/Shanghai">{element(kind)}</NextIntlClientProvider>);
        expect(screen.getByText(expected)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: messages.castWorkbench.styleAppliedFrom })).not.toBeInTheDocument();
        expect(posts()).toHaveLength(0);
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'An authored violet lantern' } });
        fireEvent.click(screen.getByRole('button', { name: messages.castWorkbench.generateMore.replace('{count}', '2') }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({
            ...body(kind), apply_style: true, style_preset: 'realistic', style_prompt: '', prompt: 'An authored violet lantern',
        });
        expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(kinds.flatMap(kind => models.map(model => [kind, model] as const)))('submits %s/%s through the strict endpoint without template negative', async (kind, model) => {
        install({ model }); page(kind); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ ...body(kind), model_name: model, apply_style: true });
        expect(JSON.parse(String(posts()[0][1]?.body))).not.toHaveProperty('negative_prompt');
        expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/);
        expect(axios.post).not.toHaveBeenCalled(); expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
    });
    it.each(kinds.flatMap(kind => ['episode', 'series', 'global'].map(source => [kind, source] as const)))('keeps the project root for %s owned by %s', async (kind, source) => {
        install({ source }); page(kind); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/);
    });
    it.each(kinds)('locks the entire %s double click before preflight returns', async kind => {
        const pending = deferred<{ data: ReturnType<typeof snapshot> }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : pending.promise);
        page(kind); const button = screen.getByRole('button', { name: /再生成/ }); fireEvent.click(button); fireEvent.click(button);
        await act(async () => pending.resolve({ data: snapshot({ revision: 6 }) }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'))).toHaveLength(1);
    });
    it.each(kinds)('retains a %s project negative until explicit correction then a separate generate click', async kind => {
        install({ negative: 'my negative words' }); page(kind);
        expect(screen.getByText('my negative words')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /再生成/ })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: '本次不使用风格提示词' })); await act(async () => {});
        expect(posts()).toHaveLength(0); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
        expect(useProjectStore.getState().currentProject?.art_direction?.style_config?.negative_prompt).toBe('my negative words');
        fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await waitFor(() => expect(posts()).toHaveLength(1)); expect(JSON.parse(String(posts()[0][1]?.body)).apply_style).toBe(false);
    });
    it.each(kinds)('blocks a %s negative discovered during fresh preflight with zero journal', async kind => {
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : snapshot({ negative: 'fresh negative', revision: 6 }) }));
        page(kind); fireEvent.click(screen.getByRole('button', { name: /再生成/ })); await act(async () => {});
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled(); expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
        expect(screen.getByRole('button', { name: '本次不使用风格提示词' })).toBeInTheDocument();
    });
    it.each(kinds)('shows partial %s success, unknown and ungenerated slots without changing the old selection', async kind => {
        beginStudioAssetImageRequest({ ...owner(), input: { ...body(kind), batch_size: 3 }, idFactory: () => kind });
        vi.mocked(appAccessFetch).mockResolvedValue(response(wire(kind, kind, ['completed', 'needs_confirmation', 'cancelled'])));
        page(kind); expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(3); await act(async () => {});
        expect(screen.getByText('已完成')).toBeInTheDocument(); expect(screen.getByText('待确认')).toBeInTheDocument(); expect(screen.getByText('本次未生成')).toBeInTheDocument();
        expect(useProjectStore.getState().currentProject?.[kind === 'scene' ? 'scenes' : 'props'][0].image_asset?.selected_id).toBe('old');
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1); expect(posts()).toHaveLength(0);
    });
    it('recovers original scene history after the selected model changes to a legacy model', async () => {
        seed('scene'); install({ model: 'qwen-image-2.0' }); page('scene');
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2); expect(gets()).toHaveLength(1); expect(posts()).toHaveLength(0);
    });
    it.each(['kind', 'root', 'owner', 'activation'] as const)('seeds a new draft for same-ID %s changes while preserving close/reopen edits', async change => {
        const view = page('scene'); fireEvent.change(screen.getByRole('textbox'), { target: { value: 'unsaved scene text' } });
        view.rerender(element('scene', false)); view.rerender(element('scene'));
        expect(screen.getByRole('textbox')).toHaveValue('unsaved scene text');
        if (change === 'kind') view.rerender(element('prop'));
        else if (change === 'root') act(() => { useProjectStore.setState({ currentProject: project('b') }); install({ id: 'b' }); });
        else if (change === 'owner') act(() => { install({ source: 'series', revision: 6 }); });
        else act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        await waitFor(() => expect(screen.getByRole('textbox')).not.toHaveValue('unsaved scene text'));
        expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toContain(change === 'kind' ? 'prop a' : change === 'root' ? 'scene b' : 'scene a');
        expect(posts()).toHaveLength(0);
    });
});
