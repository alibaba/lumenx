import { StrictMode } from 'react';
import { act, fireEvent, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { useStudioAssetImageRequests } from '@/lib/useStudioAssetImageRequests';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests } from '@/lib/studioAssetImageRequest';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import ToastContainer from '@/components/shared/ToastContainer';
import { useProjectStore } from '@/store/projectStore';
import { useToastStore } from '@/store/toastStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from './frame-command-fixtures';
import CastWorkbenchModal, { activePolls } from '../cast/CastWorkbenchModal';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
vi.mock('@/components/common/GroupedModelGrid', () => ({ default: () => null }));
const SID = '11111111-1111-4111-8111-111111111111';
const options = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'a' });
function snapshot(model = 'wan2.7-image', source = 'global', negative = '', assetId = 'shared', revision = 4) {
    return enriched({ ...ack([], 'a', revision), series_id: source === 'series' ? 's' : null,
        model_settings: { t2i_model: model },
        art_direction: negative ? { style_config: { name: 'Custom style', positive_prompt: 'soft light', negative_prompt: negative } } : null,
        characters: [{ id: assetId, name: assetId, source, reference_sheet: { selected_image_id: 'v1', image_variants: [{ id: 'v1', url: '/v1.png' }] } }] }, 9, 1);
}
// Synthetic successful projection: the unchanged receipt must match real artifact fields.
function completedSnapshot() {
    const value = snapshot('wan2.7-image', 'global', '', 'shared', 6);
    const receipt = wire('saved-request', ['completed', 'completed']);
    return { ...value, characters: [{ ...value.characters[0], image_url: '/v1.png',
        reference_sheet_request_id: receipt.request.id,
        reference_sheet: { selected_image_id: 'v1', image_variants: [
            ...value.characters[0].reference_sheet.image_variants,
            ...receipt.request.members.map(member => ({ id: member.variant_id, url: member.output_ref! })),
        ] },
    }] };
}
function completedRead() {
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : completedSnapshot() }));
}
function install(model = 'wan2.7-image', source = 'global', negative = '') {
    const value = snapshot(model, source, negative, 'shared', (useProjectStore.getState().currentProject?.revision ?? 3) + 1);
    useProjectStore.getState().applyProjectReadSnapshot('a', value);
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : value }));
    return value;
}
function wire(key: string, statuses = ['needs_confirmation', 'needs_confirmation']) {
    return { schema_version: 1, contract_version: 'studio-asset-image-request-v1', replayed: true, reason: null, request: {
        id: '22222222-2222-4222-8222-222222222222', entrypoint: 'studio.asset.reference_sheet.v1', request_contract_version: 'request-json-v1',
        client_request_id: key, request_fingerprint: 'a'.repeat(64), request_root_kind: 'project', request_root_id: 'a',
        request_root_scope_id: '33333333-3333-4333-8333-333333333333', request_root_series_id: null,
        owner_kind: 'global', owner_id: 'library', asset_type: 'character', asset_id: 'shared', channel: 'reference_sheet',
        members: statuses.map((status, i) => ({ variant_id: `attempt-${i}`, attempt_id: `attempt-${i}`, status, reason: null,
            ...(status === 'completed' ? { output_ref: `assets/studio/attempt-${i}.png` } : {}) })),
    } };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
function seed(key = 'saved-request') {
    return beginStudioAssetImageRequest({ ...options(), input: { asset_id: 'shared', batch_size: 2 }, idFactory: () => key }).journal;
}
function page(entityId = 'shared') {
    return renderWithIntl(<LightboxProvider><CastWorkbenchModal isOpen kind="character" entityId={entityId} onClose={() => {}} /><ToastContainer /></LightboxProvider>);
}
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');

beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); useToastStore.getState().clear();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ projects: [project()], currentProject: project(), readWatermarks: {}, currentSeries: null, generatingTasks: [] });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => {
        const key = init?.method === 'POST' ? JSON.parse(String(init.body)).client_request_id : 'saved-request';
        return response(wire(key));
    });
    install();
});
afterEach(() => { activePolls.forEach(clearInterval); activePolls.clear(); vi.useRealTimers(); clearRuntimeEnvironment(); });

describe('Cast strict Studio image requests', () => {
    it.each(['wan2.7-image', 'wan2.7-image-pro', 'wan/wan2.7-image#image', 'wan/wan2.7-image-pro#image'])('uses one strict POST and no template negative for %s', async model => {
        install(model); page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        const [url, init] = posts()[0];
        expect(String(url)).toMatch(/\/projects\/a\/asset_image_requests$/);
        const body = JSON.parse(String(init?.body));
        expect(body).toMatchObject({ asset_id: 'shared', asset_type: 'character', generation_type: 'reference_sheet', batch_size: 2, model_name: model });
        expect(body).not.toHaveProperty('negative_prompt');
        expect(axios.post).not.toHaveBeenCalled();
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
    });
    it.each(['episode', 'series', 'global'])('preserves the project request root for %s assets', async source => {
        install('wan2.7-image', source); page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/);
    });
    it('guards the whole generation click while two preflight reads could finish at different times', async () => {
        const first = deferred<{ data: ReturnType<typeof snapshot> }>();
        const second = deferred<{ data: ReturnType<typeof snapshot> }>(); let reads = 0;
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : ++reads === 1 ? first.promise : second.promise);
        page(); const generate = screen.getByRole('button', { name: /再生成/ });
        fireEvent.click(generate); fireEvent.click(generate); await act(async () => {});
        await act(async () => first.resolve({ data: snapshot('wan2.7-image', 'global', '', 'shared', 6) }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        await act(async () => second.resolve({ data: snapshot('wan2.7-image', 'global', '', 'shared', 7) }));
        expect(posts()).toHaveLength(1); expect(reads).toBe(1);
    });
    it('keeps other image models on their existing legacy route and negative template', async () => {
        install('wan2.6-t2i'); vi.mocked(axios.post).mockResolvedValue({ data: { ...ack([], 'a', 4), _task_id: 'legacy' } });
        page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledOnce());
        expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post).mock.calls[0][1]).toMatchObject({ negative_prompt: expect.stringContaining('text, labels') });
    });
    it.each(['scene', 'prop'] as const)('preserves the legacy %s route for an unmigrated model', async kind => {
        const asset = { id: 'shared', name: kind, source: 'global', image_asset: { variants: [{ id: 'v1', url: '/v1.png' }] } };
        const value = { ...snapshot('wan2.6-t2i', 'global', '', 'shared', 6), [kind === 'scene' ? 'scenes' : 'props']: [asset] };
        useProjectStore.getState().applyProjectReadSnapshot('a', value);
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : value }));
        vi.mocked(axios.post).mockResolvedValue({ data: { ...ack([], 'a', 6), _task_id: 'legacy' } });
        renderWithIntl(<LightboxProvider><CastWorkbenchModal isOpen kind={kind} entityId="shared" onClose={() => {}} /></LightboxProvider>);
        fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledOnce()); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.post).mock.calls[0]).toEqual([
            expect.stringMatching(/\/projects\/a\/assets\/generate$/),
            expect.objectContaining({ asset_id: 'shared', asset_type: kind, generation_type: 'all', model_name: 'wan2.6-t2i' }),
        ]);
    });
    it('blocks effective negative style explicitly, and allows turning the style off', async () => {
        install('wan2.7-image', 'global', 'no text'); page();
        expect(screen.getByText('此模型不支持负向提示词，请关闭风格提示词或更换模型。')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /再生成/ })); await act(async () => {});
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: '风格基线' }));
        fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({ apply_style: false });
    });
    it('retains every member after a lost ACK; reopen and manual recovery only GET', async () => {
        vi.mocked(appAccessFetch).mockRejectedValueOnce(new Error('lost ACK'));
        const view = page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
        expect(screen.getAllByText('待确认')).toHaveLength(2);
        expect(posts()).toHaveLength(1);
        view.unmount();
        const key = listStudioAssetImageRequests(options())[0].request.client_request_id;
        vi.mocked(appAccessFetch).mockImplementation(async () => response(wire(key)));
        page(); await waitFor(() => expect(gets()).toHaveLength(1));
        fireEvent.click(await screen.findByRole('button', { name: '查看生成结果' }));
        await waitFor(() => expect(gets()).toHaveLength(2));
        expect(posts()).toHaveLength(1); expect(axios.post).not.toHaveBeenCalled();
        expect(useProjectStore.getState().currentProject?.characters[0].reference_sheet?.selected_image_id).toBe('v1');
    });
    it('keeps all 404 members unknown and explicit new generation uses a new request key', async () => {
        seed(); vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => init?.method === 'GET' ? response({}, 404) : response(wire(JSON.parse(String(init?.body)).client_request_id)));
        page(); expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
        expect(screen.getAllByText('待确认')).toHaveLength(2); expect(posts()).toHaveLength(0);
        fireEvent.click(screen.getByRole('button', { name: '发起新生成' }));
        await waitFor(() => expect(posts()).toHaveLength(1));
        expect(JSON.parse(String(posts()[0][1]?.body)).client_request_id).not.toBe('saved-request');
        expect(listStudioAssetImageRequests(options())).toHaveLength(2);
    });
    it.each([false, true])('resolves terminal journal only after successful project readback; failure=%s', async failRead => {
        seed(); vi.mocked(appAccessFetch).mockResolvedValue(response(wire('saved-request', ['completed', 'cancelled'])));
        if (failRead) vi.mocked(axios.get).mockImplementation(async url => { if (String(url).endsWith('/presets')) return { data: [] }; throw Error('readback offline'); });
        else completedRead();
        page(); expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2); await act(async () => {});
        expect(listStudioAssetImageRequests(options())).toHaveLength(failRead ? 1 : 0);
        expect(useProjectStore.getState().currentProject?.characters[0].reference_sheet?.selected_image_id).toBe('v1');
        expect(posts()).toHaveLength(0);
        if (failRead) expect(screen.getByText('结果已保存，但最新素材读取失败，请再次查看。')).toBeInTheDocument();
    });
    it('keeps a newer completed GET when the older POST ACK arrives after journal resolution', async () => {
        completedRead();
        const old = deferred<Response>(); let key = '';
        vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => {
            if (init?.method === 'POST') { key = JSON.parse(String(init.body)).client_request_id; return old.promise; }
            return response(wire(key, ['completed', 'completed']));
        });
        const hook = renderHook(() => useStudioAssetImageRequests({ kind: 'character', open: true, projectId: 'a', assetId: 'shared', source: 'global' }));
        let submitted!: Promise<void>;
        await act(async () => { submitted = hook.result.current.submit({ asset_id: 'shared', batch_size: 2 }); });
        await waitFor(() => expect(posts()).toHaveLength(1));
        await act(async () => { await hook.result.current.refresh(); });
        expect(listStudioAssetImageRequests(options())).toHaveLength(0);
        await act(async () => { old.resolve(response(wire(key, ['pending', 'processing']))); await submitted; });
        expect(hook.result.current.results[0].receipt?.request.members.map(member => member.status)).toEqual(['completed', 'completed']);
        expect(hook.result.current.unknown).toBe(false); expect(posts()).toHaveLength(1); expect(gets()).toHaveLength(1);
    });
    it('keeps local terminal resolution when an already in-flight GET finishes after readback', async () => {
        const readback = deferred<{ data: ReturnType<typeof snapshot> }>(); const later = deferred<Response>(); let key = '';
        vi.mocked(axios.get).mockReturnValue(readback.promise);
        vi.mocked(appAccessFetch).mockImplementation(async (_url, init) => {
            if (init?.method === 'POST') { key = JSON.parse(String(init.body)).client_request_id; return response(wire(key, ['completed', 'completed'])); }
            return later.promise;
        });
        const hook = renderHook(() => useStudioAssetImageRequests({ kind: 'character', open: true, projectId: 'a', assetId: 'shared', source: 'global' }));
        let submitted!: Promise<void>; let refreshed!: Promise<void>;
        await act(async () => { submitted = hook.result.current.submit({ asset_id: 'shared', batch_size: 2 }); });
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        await act(async () => { refreshed = hook.result.current.refresh(); });
        await waitFor(() => expect(gets()).toHaveLength(1));
        await act(async () => { readback.resolve({ data: completedSnapshot() }); await submitted; });
        expect(listStudioAssetImageRequests(options())).toHaveLength(0);
        await act(async () => { later.resolve(response(wire(key, ['completed', 'completed']))); await refreshed; });
        expect(hook.result.current.results[0].receipt?.request.members.map(member => member.status)).toEqual(['completed', 'completed']);
        expect(hook.result.current.unknown).toBe(false); expect(posts()).toHaveLength(1);
    });
    it('retains the visible receipt binding when storage is downgraded before refresh', async () => {
        const hook = renderHook(() => useStudioAssetImageRequests({ kind: 'character', open: true, projectId: 'a', assetId: 'shared', source: 'global' }));
        await act(async () => { await hook.result.current.submit({ asset_id: 'shared', batch_size: 2 }); });
        const known = hook.result.current.results[0].journal;
        expect(known.receiptIdentity).not.toBeNull();
        const name = Object.keys(localStorage).find(key => key.startsWith('lumenx.studioAssetImageRequest.v1:'))!;
        const saved = JSON.parse(localStorage.getItem(name)!); saved.receiptIdentity = null; localStorage.setItem(name, JSON.stringify(saved));
        const changed = wire(known.request.client_request_id, ['completed', 'completed']); changed.request.request_fingerprint = 'b'.repeat(64);
        vi.mocked(appAccessFetch).mockImplementation(async () => response(changed));
        await act(async () => { await hook.result.current.refresh(); });
        expect(hook.result.current.results[0].status).toBe('unknown');
        expect(hook.result.current.results[0].journal.receiptIdentity).toBe(known.receiptIdentity);
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'))).toHaveLength(0);
        expect(listStudioAssetImageRequests(options())).toHaveLength(1); expect(gets()).toHaveLength(0);
    });
    it('keeps partial completed and unconfirmed members visible and retains the journal', async () => {
        seed(); vi.mocked(appAccessFetch).mockResolvedValue(response(wire('saved-request', ['completed', 'needs_confirmation'])));
        page(); expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2); await act(async () => {});
        expect(screen.getByText('已完成')).toBeInTheDocument(); expect(screen.getByText('待确认')).toBeInTheDocument();
        expect(listStudioAssetImageRequests(options())).toHaveLength(1); expect(posts()).toHaveLength(0);
    });
    it('rejects an old receipt response across environment A to B to A', async () => {
        seed(); const old = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValueOnce(old.promise).mockImplementation(async () => response({}, 404));
        page(); await waitFor(() => expect(gets()).toHaveLength(1));
        act(() => { activateRuntimeEnvironment({ schema_version: 1, storage_id: '44444444-4444-4444-8444-444444444444' }); activateRuntimeEnvironment({ schema_version: 1, storage_id: SID }); });
        await act(async () => { old.resolve(response(wire('saved-request', ['completed', 'completed']))); });
        expect(screen.queryByText('已完成')).toBeNull(); expect(posts()).toHaveLength(0);
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'))).toHaveLength(0);
    });
    it('recovers retained requests under React StrictMode without POST', async () => {
        seed();
        renderWithIntl(<StrictMode><LightboxProvider><CastWorkbenchModal isOpen kind="character" entityId="shared" onClose={() => {}} /></LightboxProvider></StrictMode>);
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
        expect(gets().length).toBeGreaterThan(0); expect(posts()).toHaveLength(0);
    });
    it('isolates each StrictMode effect lease and drops the older completed response', async () => {
        completedRead();
        seed(); const old = deferred<Response>();
        vi.mocked(appAccessFetch).mockReturnValueOnce(old.promise).mockImplementation(async () => response(wire('saved-request', ['completed', 'completed'])));
        renderWithIntl(<StrictMode><LightboxProvider><CastWorkbenchModal isOpen kind="character" entityId="shared" onClose={() => {}} /></LightboxProvider></StrictMode>);
        await waitFor(() => expect(gets()).toHaveLength(2));
        await waitFor(() => expect(listStudioAssetImageRequests(options())).toHaveLength(0));
        await act(async () => old.resolve(response(wire('saved-request', ['completed', 'completed']))));
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'))).toHaveLength(1);
        expect(screen.queryByText('结果已保存，但最新素材读取失败，请再次查看。')).toBeNull();
        expect(posts()).toHaveLength(0);
        expect(document.querySelector('img[src="http://localhost:3000/files/assets/studio/attempt-0.png"]')).not.toBeNull();
    });
    it('polls original pending members with GET and resolves only after terminal readback', async () => {
        completedRead();
        vi.useFakeTimers(); seed(); let count = 0;
        vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('saved-request', ++count === 1 ? ['pending', 'processing'] : ['completed', 'completed'])));
        page(); await act(async () => {});
        expect(gets()).toHaveLength(1); expect(screen.getByRole('button', { name: /生成中/ })).toBeDisabled();
        await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
        expect(gets()).toHaveLength(2); expect(posts()).toHaveLength(0);
        expect(screen.getAllByText('已完成')).toHaveLength(2); expect(listStudioAssetImageRequests(options())).toHaveLength(0);
    });
    it('stops showing busy when another tab removes the retained pending journal without resubmitting', async () => {
        vi.useFakeTimers(); seed(); vi.mocked(appAccessFetch).mockImplementation(async () => response(wire('saved-request', ['pending', 'processing'])));
        page(); await act(async () => {}); expect(gets()).toHaveLength(1);
        for (const key of Object.keys(localStorage)) if (key.startsWith('lumenx.studioAssetImageRequest.v1:')) localStorage.removeItem(key);
        await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
        expect(screen.getByRole('button', { name: '发起新生成' })).toBeEnabled();
        expect(screen.getAllByText('待确认')).toHaveLength(2);
        await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
        expect(gets()).toHaveLength(1); expect(posts()).toHaveLength(0); expect(listStudioAssetImageRequests(options())).toHaveLength(0);
    });
    it('reports a pre-submit lock failure without claiming an unknown paid attempt', async () => {
        Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined });
        page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ }));
        expect(await screen.findByText('本次生成尚未提交，请检查连接或本机存储后重试。')).toBeInTheDocument();
        expect(posts()).toHaveLength(0); expect(screen.queryByTestId('studio-image-member')).toBeNull();
        expect(listStudioAssetImageRequests(options())).toHaveLength(0);
    });
    it('rejects a receipt response after switching to another entity', async () => {
        seed(); const old = deferred<Response>(); vi.mocked(appAccessFetch).mockReturnValue(old.promise);
        const view = page(); await waitFor(() => expect(gets()).toHaveLength(1));
        act(() => { useProjectStore.getState().applyProjectReadSnapshot('a', snapshot('wan2.7-image', 'global', '', 'other', 6)); });
        view.rerender(<LightboxProvider><CastWorkbenchModal isOpen kind="character" entityId="other" onClose={() => {}} /></LightboxProvider>);
        await act(async () => old.resolve(response(wire('saved-request', ['completed', 'completed']))));
        expect(screen.queryByTestId('studio-image-member')).toBeNull(); expect(posts()).toHaveLength(0);
        expect(useProjectStore.getState().currentProject?.characters[0].id).toBe('other');
        expect(vi.mocked(axios.get).mock.calls.filter(([url]) => String(url).endsWith('/projects/a'))).toHaveLength(0);
    });
    it('blocks a negative style discovered by the required fresh read', async () => {
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : snapshot('wan2.7-image', 'global', 'no text', 'shared', 6) }));
        page(); fireEvent.click(screen.getByRole('button', { name: /再生成/ })); await act(async () => {});
        expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
        expect(screen.getAllByText('此模型不支持负向提示词，请关闭风格提示词或更换模型。').length).toBeGreaterThan(0);
    });
    it.each(['owner', 'inputs'])('explains a retained %s mismatch without resubmitting or claiming an artifact', async changed => {
        seed(); vi.mocked(appAccessFetch).mockImplementation(async () => response({ ...wire('saved-request'), reason: `studio_image_request_${changed}_changed` }));
        page();
        expect(await screen.findByText('此请求与当前素材已不匹配，历史记录已保留。')).toBeInTheDocument();
        expect(gets()).toHaveLength(1); expect(posts()).toHaveLength(0);
        expect(screen.getAllByText('待确认')).toHaveLength(2); expect(listStudioAssetImageRequests(options())).toHaveLength(1);
    });
    it('reads original history after an owner change but does not apply a stale owner readback', async () => {
        seed(); install('wan2.7-image', 'episode');
        vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : snapshot('wan2.7-image', 'global', '', 'shared', 5) }));
        vi.mocked(appAccessFetch).mockResolvedValue(response(wire('saved-request', ['completed', 'completed'])));
        page(); expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2); await act(async () => {});
        expect(gets()).toHaveLength(1); expect(posts()).toHaveLength(0);
        expect(useProjectStore.getState().currentProject?.characters[0].source).toBe('episode');
        expect(listStudioAssetImageRequests(options())).toHaveLength(1);
    });
});
