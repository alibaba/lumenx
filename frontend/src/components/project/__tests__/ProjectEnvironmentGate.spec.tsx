import { Component, type ReactNode } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import axios, { type AxiosAdapter } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AppAccessGate from '@/components/AppAccessGate';
import ProjectClient from '../ProjectClient';
import ScriptProcessor from '@/components/modules/ScriptProcessor';
import { clearRuntimeEnvironment, getRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { activateProjectEnvironment, deactivateProjectEnvironment, projectReadFailureKey, resetProjectStoreHydrationForTests, useProjectStore } from '@/store/projectStore';
import { scriptDraftKey, useScriptDraftStore } from '@/store/scriptDraftStore';
import { ack, deferred, enriched, project } from '../../modules/__tests__/frame-command-fixtures';
import { renderWithIntl } from '../../series/__tests__/test-utils';

// Only inactive panes / visual chrome are replaced. Gate, project shell, script
// editor, draft/project stores, API and installed interceptors are production.
vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('@/components/layout/PipelineSidebar', () => ({ default: () => null }));
vi.mock('@/components/layout/EpisodeMiniList', () => ({ default: () => null }));
vi.mock('@/components/modules/Cast', () => ({ default: () => null }));
vi.mock('@/components/modules/VideoGenerator', () => ({ default: () => null }));
vi.mock('@/components/modules/VideoAssembly', () => ({ default: () => null }));
vi.mock('@/components/modules/ConsistencyVault', () => ({ default: () => null }));
vi.mock('@/components/modules/ArtDirection', () => ({ default: () => null }));
vi.mock('@/components/modules/StoryboardComposer', () => ({ default: () => null }));
vi.mock('@/components/modules/StoryboardR2V', () => ({ default: () => null }));
vi.mock('@/components/modules/EntityConfirmModal', () => ({ default: () => null }));
vi.mock('@/components/common/ModelSettingsModal', () => ({ default: () => null }));
vi.mock('@/components/project/EnvConfigDialog', () => ({ default: () => null }));
vi.mock('@/components/project/PromptConfigModal', () => ({ default: () => null }));

const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
let storage: string;
let nextHealth: Promise<Response> | undefined;
let adapterBefore: typeof axios.defaults.adapter;
let writes: Array<{ body: unknown; environment: unknown }>;
let pendingSave: ReturnType<typeof deferred<{ data: unknown }>> | undefined;
const renderErrors: Error[] = [];

class RenderBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
    state = { error: null as Error | null };
    static getDerivedStateFromError(error: Error) { return { error }; }
    componentDidCatch(error: Error) { renderErrors.push(error); }
    render() { return this.state.error ? <p data-testid="render-crash">{this.state.error.message}</p> : this.props.children; }
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const health = () => json({ ok: true, service: 'lumenx-core', protocol_version: 1 });
const serverProject = () => enriched({ ...ack([], 'same', storage === A ? 3 : 7), original_text: storage === A ? 'A saved text' : 'B saved text' });
const editor = () => screen.getByRole('textbox', { name: '剧本编辑器' });

beforeEach(async () => {
    storage = A; nextHealth = undefined; writes = []; pendingSave = undefined; renderErrors.length = 0;
    localStorage.clear(); resetProjectStoreHydrationForTests();
    useProjectStore.setState({ activeEnvironmentKey: null, environments: {}, projects: [], currentProject: null,
        readWatermarks: {}, runningOps: {}, generatingTasks: [], currentSeries: null, pendingExtraction: null });
    useScriptDraftStore.setState({ drafts: {} });
    await activateProjectEnvironment(requireActiveRuntimeEnvironment().key);
    const initial = { ...project('same', 3), originalText: 'A saved text', frames: [], characters: [] };
    useProjectStore.setState({ projects: [initial], currentProject: initial });
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
        const url = new URL(input);
        if (url.pathname === '/health') { const held = nextHealth; nextHealth = undefined; return held ?? health(); }
        if (url.pathname === '/auth/session') return json({ auth_required: false, authenticated: false });
        if (url.pathname === '/runtime/environment') return json({ schema_version: 1, storage_id: storage });
        if (url.pathname === '/projects/same') {
            if (new Headers(init?.headers).get('X-LumenX-Environment') !== storage) return json({ code: 'environment_changed' }, 409);
            return json(serverProject());
        }
        throw new Error(`Unexpected fetch ${url.pathname}`);
    }));
    adapterBefore = axios.defaults.adapter;
    const adapter: AxiosAdapter = async config => {
        let data: unknown;
        if (config.method === 'put' && config.url?.endsWith('/projects/same/text')) {
            writes.push({ body: JSON.parse(config.data), environment: config.headers.get('X-LumenX-Environment') });
            if (!pendingSave) throw new Error('Unexpected script write');
            const response = await pendingSave.promise;
            return { ...response, status: 200, statusText: 'OK', headers: {}, config };
        }
        if (config.url?.endsWith('/next_hook')) data = { has_text: false, hook: null, stale: false };
        else if (config.url?.endsWith('/previous_episode')) data = { has_previous: false };
        else throw new Error(`Unexpected Axios request ${config.url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    axios.defaults.adapter = adapter;
});
afterEach(() => { axios.defaults.adapter = adapterBefore; vi.unstubAllGlobals(); });

async function ready() {
    await waitFor(() => expect(useProjectStore.getState().currentProject?.read_versions?.project).toBe(3));
    expect(editor()).toHaveValue('A saved text');
}
function mount(child = <ProjectClient id="same" />) {
    return renderWithIntl(<RenderBoundary><AppAccessGate>{child}</AppAccessGate></RenderBoundary>);
}
async function switchRoot(target: string) {
    const recheck = deferred<Response>(); nextHealth = recheck.promise; storage = target;
    await act(async () => { await useProjectStore.getState().selectProject('same'); });
    expect(renderErrors).toEqual([]);
    expect(screen.queryByTestId('render-crash')).not.toBeInTheDocument();
    expect(getRuntimeEnvironment()).toBeNull();
    expect(() => scriptDraftKey('same')).toThrow(/not active/);
    expect(() => projectReadFailureKey('same')).toThrow(/not active/);
    expect(screen.queryByRole('textbox', { name: '剧本编辑器' })).not.toBeInTheDocument();
    await act(async () => { recheck.resolve(health()); });
    await waitFor(() => expect(getRuntimeEnvironment()?.storageId).toBe(target));
    await waitFor(() => expect(editor()).toHaveValue(target === A ? 'A draft' : 'B saved text'));
}

describe('actual mounted project children while AppAccessGate clears runtime identity', () => {
    it('survives a real 409 and keeps same-ID drafts separate across A → B → A', async () => {
        mount(); await ready();
        const keyA = scriptDraftKey('same');
        fireEvent.change(editor(), { target: { value: 'A draft' } });
        await switchRoot(B);
        const keyB = scriptDraftKey('same');
        expect(keyB).not.toBe(keyA);
        fireEvent.change(editor(), { target: { value: 'B draft' } });
        await switchRoot(A);
        expect(useScriptDraftStore.getState().drafts[keyA].text).toBe('A draft');
        expect(useScriptDraftStore.getState().drafts[keyB].text).toBe('B draft');
        expect(Object.keys(useScriptDraftStore.getState().drafts).sort()).toEqual([keyA, keyB].sort());
        expect(writes).toEqual([]);
    });

    it('also keeps a directly mounted ScriptProcessor safe during the null-runtime gap', async () => {
        mount(<ScriptProcessor />); await waitFor(() => expect(editor()).toHaveValue('A saved text'));
        const key = scriptDraftKey('same'); fireEvent.change(editor(), { target: { value: 'A draft' } });
        const recheck = deferred<Response>(); nextHealth = recheck.promise; storage = B;
        await act(async () => { await useProjectStore.getState().selectProject('same'); });
        expect(renderErrors).toEqual([]);
        expect(getRuntimeEnvironment()).toBeNull();
        expect(useScriptDraftStore.getState().drafts[key].text).toBe('A draft');
        expect(screen.queryByRole('textbox', { name: '剧本编辑器' })).not.toBeInTheDocument();
        const hiddenEditor = screen.getByRole('textbox', { hidden: true });
        expect(hiddenEditor).toHaveValue('');
        expect(hiddenEditor).toBeDisabled();
        fireEvent.change(hiddenEditor, { target: { value: 'must not write through hidden editor' } });
        fireEvent.blur(hiddenEditor);
        expect(Object.keys(useScriptDraftStore.getState().drafts)).toEqual([key]);
        expect(useScriptDraftStore.getState().drafts[key].text).toBe('A draft');
        expect(writes).toEqual([]);
        await act(async () => { recheck.resolve(health()); });
    });

    it('rejects a stale blur without throwing or writing when there is no active environment', async () => {
        const key = scriptDraftKey('same');
        useScriptDraftStore.getState().initialize(key, 'A saved text', 3);
        useScriptDraftStore.getState().changeText(key, 'A draft');
        // The save closure can run after the gate invalidates the environment.
        clearRuntimeEnvironment(); deactivateProjectEnvironment();
        await expect(useScriptDraftStore.getState().save(key, 'same')).resolves.toBeUndefined();
        expect(writes).toEqual([]);
        expect(useScriptDraftStore.getState().drafts[key]).toMatchObject({ text: 'A draft', savedText: 'A saved text', saving: false });
    });

    it('does not let a delayed original save ACK confirm the reactivated A draft or overwrite B', async () => {
        mount(); await ready();
        const keyA = scriptDraftKey('same');
        fireEvent.change(editor(), { target: { value: 'A draft' } });
        pendingSave = deferred<{ data: unknown }>(); fireEvent.blur(editor());
        await waitFor(() => expect(writes).toHaveLength(1));
        await switchRoot(B);
        const keyB = scriptDraftKey('same'); fireEvent.change(editor(), { target: { value: 'B draft' } });
        await useScriptDraftStore.getState().save(keyA, 'same');
        expect(writes).toHaveLength(1);
        await switchRoot(A);
        await act(async () => { pendingSave!.resolve({ data: { ...ack([], 'same', 4), original_text: 'A draft' } }); });
        expect(editor()).toHaveValue('A draft');
        expect(useProjectStore.getState().currentProject).toMatchObject({ originalText: 'A saved text', revision: 3 });
        expect(useScriptDraftStore.getState().drafts[keyA]).toMatchObject({ text: 'A draft', savedText: 'A saved text', revision: 3, saving: false });
        expect(useScriptDraftStore.getState().drafts[keyB]).toMatchObject({ text: 'B draft', savedText: 'B saved text' });
        expect(writes).toEqual([{ body: { text: 'A draft', expected_revision: 3 }, environment: A }]);
    });
});
