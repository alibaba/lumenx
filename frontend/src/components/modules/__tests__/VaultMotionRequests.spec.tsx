import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import { useProjectStore, type Character, type Scene } from '@/store/projectStore';
import type { StudioMotionReceipt } from '@/lib/api';
import { beginStudioMotionRequest } from '@/lib/studioMotionRequest';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, project } from './frame-command-fixtures';
import capture from '@/__tests__/fixtures/studio-motion-request-wire.json';
import duration5 from '@/__tests__/fixtures/studio-motion-duration5-wire.json';
import ConsistencyVault from '../ConsistencyVault';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
const SID = '11111111-1111-4111-8111-111111111111';
type CapturedEvent = { label: string; body: Record<string, unknown>; status_code: number };
const observed = capture as unknown as { cases: Array<{ case: string; events: CapturedEvent[] }> };
function actual(owner: string, label: string) {
    const found = observed.cases.find(item => item.case === `lifecycle_${owner}`)?.events.find(item => item.label === label);
    if (!found) throw new Error(`Missing real HTTP evidence: ${owner}/${label}`);
    return structuredClone(found);
}
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const strictPosts = () => vi.mocked(appAccessFetch).mock.calls.filter(([url, init]) => init?.method === 'POST' && String(url).endsWith('/motion_ref_requests'));
const legacyPosts = () => vi.mocked(axios.post).mock.calls.filter(([url]) => String(url).endsWith('/assets/generate_motion_ref'));
const panel = () => screen.getAllByRole('group', { name: '1. 主体资产（全身图）' }).at(-1)!;
const generate = () => within(panel()).getByRole('button', { name: 'Generate Motion Reference' });
let server: Record<string, unknown>;
let qualification: CapturedEvent;
let heldPost: ReturnType<typeof deferred<Response>>;
function install(owner = 'script') {
    const projection = actual(owner, 'completed_enriched_project_projection').body;
    // These project fields are the actual enriched read projection. Remaining
    // unrelated project fields come from the existing UI setup, not a fake receipt.
    server = { ...ack([], 'project', projection.revision as number), ...projection,
        model_settings: { t2i_model: 'wan2.6-t2i', i2v_model: 'kling-v1' } };
    qualification = actual(owner, 'eligibility');
    useProjectStore.setState({ currentProject: project('project', 0), projects: [project('project', 0)], currentSeries: null,
        readWatermarks: {}, generatingTasks: [], runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('project', server);
}
function page() {
    const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
    fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Hero' }));
    fireEvent.click(within(panel()).getByRole('button', { name: '动态' }));
    return view;
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); vi.spyOn(window, 'alert').mockImplementation(() => {});
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    heldPost = deferred<Response>(); install();
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : server }));
    vi.mocked(axios.post).mockResolvedValue({ data: server });
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        if (String(url).endsWith('/upload')) return response({ url: '/output/uploads/authored-audio.wav' });
        if (init?.method === 'POST') return heldPost.promise;
        if (new URL(String(url), 'http://localhost').pathname.endsWith('/motion_ref_requests')) return response(qualification.body, qualification.status_code);
        return response({ detail: 'No such request in this test' }, 404);
    });
});
afterEach(() => { clearRuntimeEnvironment(); });

describe('actual Vault full-body Motion admission', () => {
    it.each(['script', 'series', 'global'])('uses strict admission for the actual %s owner and preserves the original five seconds', async owner => {
        install(owner); page();
        const prompt = within(panel()).getByPlaceholderText('Describe the motion you want...');
        fireEvent.change(prompt, { target: { value: 'My authored motion, exactly.' } });
        await waitFor(() => expect(generate()).not.toBeDisabled());
        fireEvent.click(generate());
        await waitFor(() => expect(strictPosts().length + legacyPosts().length).toBeGreaterThan(0));
        expect(strictPosts()).toHaveLength(1); expect(legacyPosts()).toHaveLength(0);
        const [url, init] = strictPosts()[0];
        expect(String(url)).toMatch(/\/projects\/project\/motion_ref_requests$/);
        expect(JSON.parse(String(init?.body))).toEqual({ asset_id: 'hero', asset_type: 'full_body', duration: 5, batch_size: 1,
            prompt: 'My authored motion, exactly.', expected_target: qualification.body.expected_target,
            client_request_id: expect.any(String) });
        expect(generate()).toBeDisabled();
        // This POST stays unresolved: the captured historical receipts used ten
        // seconds, and must never be rewritten into a fabricated five-second ACK.
        const journals = Object.keys(localStorage).filter(key => key.startsWith('lumenx.studioMotionRequest.v1:'));
        expect(journals).toHaveLength(1);
        expect(localStorage.getItem(journals[0])).not.toMatch(/credential_ref|provider_execution|frameId/);
    });
    it('reads the original key after a lost ACK and reopening without another submission', async () => {
        const first = page();
        await waitFor(() => expect(generate()).not.toBeDisabled());
        fireEvent.click(generate());
        await waitFor(() => expect(strictPosts().length + legacyPosts().length).toBeGreaterThan(0));
        expect(strictPosts()).toHaveLength(1); expect(legacyPosts()).toHaveLength(0);
        const original = JSON.parse(String(strictPosts()[0][1]?.body)) as { client_request_id: string };
        await act(async () => { heldPost.reject(new Error('Response lost after acceptance')); });
        const storageKeys = Object.keys(localStorage).filter(key => key.startsWith('lumenx.studioMotionRequest.v1:'));
        expect(storageKeys).toHaveLength(1);
        const originalBytes = localStorage.getItem(storageKeys[0]);
        first.unmount(); page();
        await waitFor(() => expect(vi.mocked(appAccessFetch).mock.calls.some(([url, init]) =>
            init?.method === 'GET' && String(url).endsWith(`/motion_ref_requests/${original.client_request_id}`))).toBe(true));
        expect(strictPosts()).toHaveLength(1); expect(legacyPosts()).toHaveLength(0);
        expect(localStorage.getItem(storageKeys[0])).toBe(originalBytes);
        expect(generate()).toBeDisabled();
    });
    it('submits headshot Motion and recovers its original journal without replacing the selected video', async () => {
        // Headshot unit variants of the existing five-second capture. These
        // converted headshot responses are not new captured backend wire evidence.
        const captured = (label: string) => {
            const event = (duration5 as unknown as typeof observed).cases[0].events.find(item => item.label === label);
            if (!event) throw new Error(`Missing five-second evidence: ${label}`);
            return structuredClone(event.body);
        };
        const projection = captured('completed_enriched_project_projection');
        server = { ...ack([], 'project', projection.revision as number), ...projection,
            model_settings: { t2i_model: 'wan2.6-t2i', i2v_model: 'kling-v1' } };
        const character = (server.characters as Character[])[0];
        const completedHead = structuredClone(character.full_body!);
        character.headshot_image_url = completedHead.image_variants![0].url;
        character.head_shot = { ...structuredClone(completedHead), video_variants: completedHead.video_variants!.slice(0, 1) };
        character.head_shot_motion_request_id = null;
        const fullBody = structuredClone(character.full_body);
        qualification = { label: 'headshot unit eligibility', status_code: 200,
            body: { ...captured('eligibility'), asset_type: 'head_shot' } };
        useProjectStore.setState({ currentProject: project('project', 0), projects: [project('project', 0)],
            readWatermarks: {}, generatingTasks: [], runningOps: {} });
        useProjectStore.getState().applyProjectReadSnapshot('project', server);
        let requestKey = '', recovered = false;
        const receipt = (label: string) => {
            const value = captured(label) as unknown as StudioMotionReceipt;
            value.request.asset_type = 'head_shot'; value.request.client_request_id = requestKey;
            value.task!.motion_reference.channel = 'head_shot'; value.task!.motion_reference.client_request_id = requestKey;
            return value;
        };
        vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
            const path = new URL(String(url), 'http://localhost').pathname;
            if (path.endsWith('/motion_ref_requests')) {
                if (init?.method === 'POST') {
                    requestKey = JSON.parse(String(init.body)).client_request_id;
                    return heldPost.promise;
                }
                return response(qualification.body);
            }
            if (init?.method === 'POST' && path.endsWith('/recover')) {
                recovered = true;
                const completed = receipt('completed_get');
                character.head_shot = completedHead; character.head_shot_motion_request_id = completed.task_id;
                server.revision = Number(server.revision) + 1;
                server.read_versions = { ...(server.read_versions as object), project: server.revision };
                return response(completed);
            }
            return response(receipt(recovered ? 'completed_get' : 'submitted_get'));
        });
        const head = () => screen.getAllByRole('group', { name: '3. 头像（特写）' }).at(-1)!;
        const headGenerate = () => within(head()).getByRole('button', { name: 'Generate Motion Reference' });
        const openHead = () => { const view = page(); fireEvent.click(within(head()).getByRole('button', { name: '动态' })); return view; };
        const first = openHead();
        fireEvent.change(within(head()).getByPlaceholderText('Describe the motion you want...'), { target: { value: 'One slow rotation' } });
        await waitFor(() => expect(headGenerate()).not.toBeDisabled()); fireEvent.click(headGenerate());
        await waitFor(() => expect(strictPosts()).toHaveLength(1));
        expect(JSON.parse(String(strictPosts()[0][1]?.body))).toEqual({ asset_id: 'hero', asset_type: 'head_shot',
            prompt: 'One slow rotation', duration: 5, batch_size: 1, expected_target: qualification.body.expected_target,
            client_request_id: requestKey });
        expect(vi.mocked(appAccessFetch).mock.calls.some(([url, init]) => init?.method === 'GET'
            && new URL(String(url), 'http://localhost').searchParams.get('asset_type') === 'head_shot')).toBe(true);
        const storageKeys = Object.keys(localStorage).filter(key => key.startsWith('lumenx.studioMotionRequest.v1:'));
        expect(storageKeys).toHaveLength(1);
        expect(JSON.parse(localStorage.getItem(storageKeys[0])!).request.asset_type).toBe('head_shot');
        await act(async () => { heldPost.reject(new Error('Response lost after acceptance')); });
        first.unmount(); openHead();
        const recover = await within(head()).findByRole('button', { name: '恢复原任务' });
        expect(within(panel()).queryByRole('button', { name: '恢复原任务' })).not.toBeInTheDocument();
        expect(headGenerate()).toBeDisabled(); fireEvent.click(recover);
        await waitFor(() => expect(localStorage.getItem(storageKeys[0])).toBeNull());
        expect(strictPosts()).toHaveLength(1); expect(legacyPosts()).toHaveLength(0);
        const recoveries = vi.mocked(appAccessFetch).mock.calls.filter(([url, init]) => init?.method === 'POST' && String(url).endsWith('/recover'));
        expect(recoveries).toHaveLength(1);
        expect(String(recoveries[0][0])).toContain(`/motion_ref_requests/${requestKey}/recover`);
        expect(head().querySelector('video')).toHaveAttribute('src', expect.stringContaining('video/history.mp4'));
        expect(useProjectStore.getState().currentProject?.characters[0].head_shot?.selected_video_id).toBe('selected-video');
        expect(useProjectStore.getState().currentProject?.characters[0].full_body).toEqual(fullBody);
    });
    it('recovers scene Motion through its original journal without sharing a same-ID character or prop request', async () => {
        // Scene responses are unit variants of the existing five-second capture,
        // not newly captured backend wire or evidence that synthetic video decodes.
        const captured = (label: string) => {
            const event = (duration5 as unknown as typeof observed).cases[0].events.find(item => item.label === label);
            if (!event) throw new Error(`Missing five-second evidence: ${label}`);
            return structuredClone(event.body);
        };
        const projection = captured('completed_enriched_project_projection');
        server = { ...ack([], 'project', projection.revision as number), ...projection,
            model_settings: { t2i_model: 'wan2.6-t2i', i2v_model: 'kling-v1' } };
        const character = (server.characters as Character[])[0];
        const selectedImage = structuredClone(character.full_body!.image_variants[0]);
        const scene: Scene = { id: character.id, name: 'Room', description: 'A quiet room', source: 'episode',
            image_url: selectedImage.url, image_asset: { selected_id: selectedImage.id, variants: [selectedImage] },
            video_prompt: 'Earlier scene prompt', video_assets: [], motion_request_id: null };
        server.scenes = [scene]; server.props = [{ ...structuredClone(scene), name: 'Clock' }];
        const characterBefore = structuredClone(character), propBefore = structuredClone(server.props);
        qualification = { label: 'scene unit eligibility', status_code: 200, body: { ...captured('eligibility'), asset_type: 'scene' } };
        useProjectStore.setState({ currentProject: project('project', 0), projects: [project('project', 0)], currentSeries: null,
            readWatermarks: {}, generatingTasks: [], runningOps: {} });
        useProjectStore.getState().applyProjectReadSnapshot('project', server);
        beginStudioMotionRequest({ environment: requireActiveRuntimeEnvironment(), projectId: 'project', assetId: character.id,
            assetOwner: { kind: 'script', id: 'project' }, selectedImageId: selectedImage.id, idFactory: () => 'existing-character',
            input: { asset_id: character.id, asset_type: 'full_body', expected_target: String(qualification.body.expected_target), duration: 5 } });
        const prefix = 'lumenx.studioMotionRequest.v1:';
        const characterKey = Object.keys(localStorage).find(key => key.startsWith(prefix))!;
        const characterBytes = localStorage.getItem(characterKey)!;
        // Seed the prop namespace directly so this regression reaches the actual
        // scene button even on the old product that cannot begin a prop request.
        const propJournal = JSON.parse(characterBytes);
        propJournal.request = { ...propJournal.request, asset_type: 'prop', client_request_id: 'existing-prop' };
        const propKey = `${prefix}prop:${characterKey.slice(prefix.length, characterKey.lastIndexOf(':') + 1)}existing-prop`;
        const propBytes = JSON.stringify(propJournal); localStorage.setItem(propKey, propBytes);
        const locks: string[] = [];
        Object.defineProperty(navigator, 'locks', { configurable: true, value: {
            request: async (name: string, _options: unknown, callback: (lock: object | null) => unknown) => {
                locks.push(name);
                return callback(name.startsWith(`${prefix}scene:`) ? {} : null);
            } } });
        let requestKey = '', completeOnRead = false;
        vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
            const path = new URL(String(url), 'http://localhost').pathname;
            if (path.endsWith('/motion_ref_requests')) {
                if (init?.method === 'POST') { requestKey = JSON.parse(String(init.body)).client_request_id; return heldPost.promise; }
                return response(qualification.body);
            }
            if (!requestKey || !path.endsWith(`/motion_ref_requests/${requestKey}`) || !completeOnRead) return response({}, 404);
            const completed = captured('completed_get') as unknown as StudioMotionReceipt;
            completed.request.asset_type = 'scene'; completed.request.client_request_id = requestKey;
            completed.task!.motion_reference.channel = 'scene'; completed.task!.motion_reference.client_request_id = requestKey;
            scene.motion_request_id = completed.task_id;
            const task = completed.task!;
            const history = { id: task.id, project_id: task.project_id, asset_id: task.asset_id, image_url: task.image_url,
                prompt: task.prompt, status: task.status, video_url: task.video_url, duration: task.duration, created_at: task.created_at,
                provider_name: null, provider_task_id: null, provider_request_id: null, provider_execution: null, motion_reference: null };
            scene.video_assets = [history]; server.video_tasks = [structuredClone(task)];
            server.revision = Number(server.revision) + 1;
            server.read_versions = { ...(server.read_versions as object), project: server.revision };
            return response(completed);
        });
        const openScene = () => {
            const view = renderWithIntl(<LightboxProvider><ConsistencyVault /></LightboxProvider>);
            fireEvent.click(screen.getByRole('button', { name: /Scenes/ }));
            fireEvent.click(screen.getByRole('heading', { level: 3, name: 'Room' }));
            fireEvent.click(screen.getByRole('button', { name: 'Video Reference' }));
            return view;
        };
        const sceneGenerate = () => screen.getByRole('button', { name: '生成视频' });
        const first = openScene();
        fireEvent.change(screen.getByDisplayValue('Earlier scene prompt'), { target: { value: 'One slow rotation' } });
        await waitFor(() => expect(sceneGenerate()).not.toBeDisabled()); fireEvent.click(sceneGenerate());
        await waitFor(() => expect(strictPosts().length + legacyPosts().length).toBeGreaterThan(0));
        expect(strictPosts()).toHaveLength(1); expect(legacyPosts()).toHaveLength(0);
        expect(JSON.parse(String(strictPosts()[0][1]?.body))).toEqual({ asset_id: character.id, asset_type: 'scene',
            prompt: 'One slow rotation', duration: 5, batch_size: 1, expected_target: qualification.body.expected_target,
            client_request_id: requestKey });
        expect(screen.getByDisplayValue('One slow rotation')).toBeInTheDocument();
        expect(vi.mocked(appAccessFetch).mock.calls.some(([url, init]) => init?.method === 'GET'
            && new URL(String(url), 'http://localhost').searchParams.get('asset_type') === 'scene')).toBe(true);
        const sceneKey = Object.keys(localStorage).find(key => key.startsWith(`${prefix}scene:`))!;
        expect(sceneKey).toBeTruthy();
        expect(locks.length).toBeGreaterThan(0);
        expect(locks.every(name => name.startsWith(`${prefix}scene:`))).toBe(true);
        await act(async () => { heldPost.reject(new Error('Scene response lost after acceptance')); });
        expect(sceneGenerate()).toBeDisabled(); first.unmount(); completeOnRead = true; openScene();
        await waitFor(() => expect(localStorage.getItem(sceneKey)).toBeNull());
        expect(strictPosts()).toHaveLength(1); expect(legacyPosts()).toHaveLength(0);
        expect(vi.mocked(appAccessFetch).mock.calls.some(([url]) => /existing-character|existing-prop/.test(String(url)))).toBe(false);
        expect(localStorage.getItem(characterKey)).toBe(characterBytes); expect(localStorage.getItem(propKey)).toBe(propBytes);
        const saved = useProjectStore.getState().currentProject!;
        expect(saved.scenes[0].image_asset).toEqual(scene.image_asset);
        expect(saved.scenes[0].video_assets).toHaveLength(1);
        expect(saved.scenes[0].video_assets![0].id).toBe(saved.scenes[0].motion_request_id);
        expect(saved.scenes[0].video_assets![0]).toMatchObject({ provider_execution: null, motion_reference: null });
        expect(saved.characters[0]).toEqual(characterBefore); expect(saved.props).toEqual(propBefore);
    });
    it('keeps authored audio on the existing legacy route without a strict fallback', async () => {
        page();
        const input = within(panel()).getByLabelText('Upload Audio File');
        fireEvent.change(input, { target: { files: [new File(['audio'], 'voice.wav', { type: 'audio/wav' })] } });
        await within(panel()).findByText('Audio Uploaded');
        const prompt = within(panel()).getByPlaceholderText('Describe the motion you want...');
        fireEvent.change(prompt, { target: { value: 'Keep my speaking prompt.' } });
        fireEvent.click(generate()); await waitFor(() => expect(legacyPosts()).toHaveLength(1));
        expect(legacyPosts()[0][1]).toMatchObject({ asset_id: 'hero', asset_type: 'full_body', prompt: 'Keep my speaking prompt.',
            audio_url: '/output/uploads/authored-audio.wav', duration: 5, batch_size: 1 });
        expect(strictPosts()).toHaveLength(0);
        await act(async () => {});
    });
});
