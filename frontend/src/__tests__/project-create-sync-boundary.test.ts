import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { environmentLocalStorageKey, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { projectReadFailureKey, useProjectStore, type Project } from '@/store/projectStore';

vi.mock('@/lib/api', async importOriginal => {
    const actual = await importOriginal<typeof import('@/lib/api')>();
    return {
        ...actual,
        api: {
            ...actual.api,
            createProject: vi.fn(),
            getProject: vi.fn(),
            updateModelSettings: vi.fn(),
            updatePromptConfig: vi.fn(),
        },
    };
});

function rawProject(id = 'p1', revision = 1, text = 'raw text') {
    return {
        id, revision, title: `Project ${id}`, original_text: text,
        frames: [], characters: [], scenes: [], props: [], video_tasks: [], status: 'draft',
        created_at: 1000 + revision, updated_at: 2000 + revision,
        workflow_mode: 'r2v',
    };
}

function readProject(id = 'p1', revision = 1, text = 'read text') {
    return { ...rawProject(id, revision, text), read_versions: { project: revision, series: null, library: 0 } };
}

function confirmedProject(id = 'p1', revision = 1, text = 'read text'): Project {
    const raw = readProject(id, revision, text);
    return {
        ...raw,
        originalText: raw.original_text,
        createdAt: new Date(raw.created_at * 1000).toISOString(),
        updatedAt: new Date(raw.updated_at * 1000).toISOString(),
        readEnvironment: getRuntimeEnvironment()!.key,
        read_versions: raw.read_versions,
        sharedAssetsNeedRefresh: false,
    } as Project;
}

function state() {
    return useProjectStore.getState();
}

const envKey = () => getRuntimeEnvironment()!.key;
const defaultModelKey = () => environmentLocalStorageKey('lumenx_default_model_settings', envKey());
const defaultPromptKey = () => environmentLocalStorageKey('lumenx_default_prompt_config', envKey());

function syncList(projects: unknown, capture?: unknown) {
    return (state() as unknown as { syncProjectList: (projects: unknown[], capture?: unknown) => void }).syncProjectList(projects as unknown[], capture);
}

function captureListStart() {
    return (state() as unknown as { captureProjectListSync?: () => unknown }).captureProjectListSync?.();
}

beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    useProjectStore.setState({
        projects: [],
        currentProject: null,
        currentSeries: null,
        isLoading: false,
        readWatermarks: {},
        runningOps: {},
    } as Partial<ReturnType<typeof useProjectStore.getState>>);
});

describe('project create and workspace sync boundaries', () => {
    it('confirms a raw create ACK with exactly one enriched project readback', async () => {
        vi.mocked(api.createProject).mockResolvedValueOnce({ ...rawProject('created', 1, 'draft'), originalText: 'draft' });
        vi.mocked(api.getProject).mockResolvedValueOnce(readProject('created', 1, 'committed'));

        const created = await state().createProject('Created', 'draft', true, 'r2v');

        expect(created.id).toBe('created');
        expect(api.createProject).toHaveBeenCalledTimes(1);
        expect(api.getProject).toHaveBeenCalledExactlyOnceWith('created');
        expect(state().currentProject).toMatchObject({
            id: 'created',
            originalText: 'committed',
            readEnvironment: envKey(),
            read_versions: { project: 1, series: null, library: 0 },
            sharedAssetsNeedRefresh: false,
        });
    });

    it('keeps a raw created project and marks read failed when enriched readback fails', async () => {
        vi.mocked(api.createProject).mockResolvedValueOnce({ ...rawProject('created', 1, 'draft'), originalText: 'draft' });
        vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));

        const created = await state().createProject('Created', 'draft', true, 'r2v');

        expect(created.id).toBe('created');
        expect(api.createProject).toHaveBeenCalledTimes(1);
        expect(api.getProject).toHaveBeenCalledExactlyOnceWith('created');
        expect(state().currentProject).toMatchObject({ id: 'created', originalText: 'draft' });
        expect(state().runningOps[projectReadFailureKey('created')]).toBe(true);
    });

    it('does not let a late raw list sync delete a project created after that list began', () => {
        useProjectStore.setState({ projects: [confirmedProject('old', 2)], currentProject: confirmedProject('old', 2) });
        const capture = captureListStart();
        useProjectStore.setState({ projects: [confirmedProject('old', 2), confirmedProject('created', 1)], currentProject: confirmedProject('created', 1) });

        syncList([rawProject('old', 2, 'old')], capture);

        expect(state().projects.map(project => project.id)).toEqual(['old', 'created']);
        expect(state().currentProject?.id).toBe('created');
    });

    it('clears an unchanged stale card when the backend list is empty', () => {
        useProjectStore.setState({ projects: [confirmedProject('stale', 2)], currentProject: null });
        const capture = captureListStart();

        syncList([], capture);

        expect(state().projects).toEqual([]);
    });

    it('rejects malformed project-list responses instead of treating them as an authoritative empty list', () => {
        useProjectStore.setState({ projects: [confirmedProject('kept', 2)], currentProject: confirmedProject('kept', 2) });
        const capture = captureListStart();

        expect(() => syncList(null, capture)).toThrow();
        expect(state().projects.map(project => project.id)).toEqual(['kept']);
    });

    it('does not downgrade an already confirmed project with a raw list summary', () => {
        useProjectStore.setState({ projects: [confirmedProject('a', 5, 'confirmed')], currentProject: confirmedProject('a', 5, 'confirmed') });
        const capture = captureListStart();

        syncList([rawProject('a', 5, 'raw summary')], capture);

        expect(state().currentProject).toMatchObject({
            id: 'a',
            originalText: 'confirmed',
            readEnvironment: envKey(),
            read_versions: { project: 5, series: null, library: 0 },
            sharedAssetsNeedRefresh: false,
        });
    });

    it('does not downgrade a known raw project with an older late list summary', () => {
        const knownRaw = {
            ...confirmedProject('created', 8, 'new raw project'),
            read_versions: undefined,
            sharedAssetsNeedRefresh: true,
        } as Project;
        useProjectStore.setState({ projects: [knownRaw], currentProject: knownRaw });
        const capture = captureListStart();

        syncList([rawProject('created', 7, 'older list summary')], capture);

        expect(state().currentProject).toMatchObject({
            id: 'created',
            revision: 8,
            originalText: 'new raw project',
            readEnvironment: envKey(),
        });
    });

    it('does not let create readback reclaim current after the user selects another project', async () => {
        const readback = new Promise<unknown>(resolve => setTimeout(() => resolve(readProject('created', 1, 'committed')), 0));
        vi.mocked(api.createProject).mockResolvedValueOnce({ ...rawProject('created', 1, 'draft'), originalText: 'draft' });
        vi.mocked(api.getProject).mockReturnValueOnce(readback as ReturnType<typeof api.getProject>);
        useProjectStore.setState({ projects: [confirmedProject('b', 4, 'B')], currentProject: null });

        const creating = state().createProject('Created', 'draft', true, 'r2v');
        await vi.waitFor(() => expect(state().currentProject?.id).toBe('created'));
        useProjectStore.setState({ currentProject: confirmedProject('b', 4, 'B') });
        await creating;

        expect(state().currentProject?.id).toBe('b');
        expect(state().projects.find(project => project.id === 'created')).toMatchObject({
            id: 'created',
            readEnvironment: envKey(),
            read_versions: { project: 1, series: null, library: 0 },
        });
    });

    it('does not select a created project when the user clears while the create POST is pending', async () => {
        let resolveCreate!: (value: unknown) => void;
        vi.mocked(api.createProject).mockReturnValueOnce(new Promise(resolve => { resolveCreate = resolve; }) as ReturnType<typeof api.createProject>);
        vi.mocked(api.getProject).mockResolvedValueOnce(readProject('created', 1, 'committed'));
        useProjectStore.setState({ projects: [confirmedProject('b', 4, 'B')], currentProject: confirmedProject('b', 4, 'B') });

        const creating = state().createProject('Created', 'draft', true, 'r2v');
        state().clearCurrentProject();
        resolveCreate({ ...rawProject('created', 1, 'draft'), originalText: 'draft' });
        await creating;

        expect(state().currentProject).toBeNull();
        expect(state().projects.find(project => project.id === 'created')).toMatchObject({
            id: 'created',
            readEnvironment: envKey(),
            read_versions: { project: 1, series: null, library: 0 },
        });
    });

    it('uses the default-injection readback text and revision together with one enriched read', async () => {
        const storage: Record<string, string> = {
            [defaultPromptKey()]: JSON.stringify({ entity_extraction: 'custom extraction prompt' }),
        };
        const localStorage = { getItem: (key: string) => storage[key] ?? null };
        vi.stubGlobal('window', { localStorage });
        vi.stubGlobal('localStorage', localStorage);
        vi.mocked(api.createProject).mockResolvedValueOnce({ ...rawProject('created', 1, 'original create text'), originalText: 'original create text' });
        vi.mocked(api.updatePromptConfig).mockResolvedValueOnce({ revision: 2, prompt_config: { entity_extraction: 'custom extraction prompt' } });
        vi.mocked(api.getProject).mockResolvedValueOnce(readProject('created', 2, 'server text after defaults'));

        await state().createProject('Created', 'original create text', true, 'r2v');

        expect(api.updatePromptConfig).toHaveBeenCalledExactlyOnceWith('created', { entity_extraction: 'custom extraction prompt' });
        expect(api.getProject).toHaveBeenCalledExactlyOnceWith('created');
        expect(state().currentProject).toMatchObject({
            id: 'created',
            revision: 2,
            originalText: 'server text after defaults',
            read_versions: { project: 2, series: null, library: 0 },
        });
    });

    it('rejects a default-write readback that is older than the default command acknowledgement', async () => {
        const storage: Record<string, string> = {
            [defaultPromptKey()]: JSON.stringify({ entity_extraction: 'custom extraction prompt' }),
        };
        const localStorage = { getItem: (key: string) => storage[key] ?? null };
        vi.stubGlobal('window', { localStorage });
        vi.stubGlobal('localStorage', localStorage);
        vi.mocked(api.createProject).mockResolvedValueOnce({ ...rawProject('created', 1, 'original create text'), originalText: 'original create text' });
        vi.mocked(api.updatePromptConfig).mockResolvedValueOnce({ revision: 3, prompt_config: { entity_extraction: 'custom extraction prompt' } });
        vi.mocked(api.getProject).mockResolvedValueOnce(readProject('created', 2, 'stale readback'));

        await state().createProject('Created', 'original create text', true, 'r2v');

        expect(api.getProject).toHaveBeenCalledTimes(1);
        expect(state().projects.find(project => project.id === 'created')).toMatchObject({
            id: 'created',
            revision: 1,
            originalText: 'original create text',
        });
        expect(state().runningOps[projectReadFailureKey('created')]).toBe(true);
    });


    it('persists the first successful default write floor when a later default write fails', async () => {
        const storage: Record<string, string> = {
            [defaultModelKey()]: JSON.stringify({ t2i_model: 'custom-t2i' }),
            [defaultPromptKey()]: JSON.stringify({ entity_extraction: 'custom extraction prompt' }),
        };
        const localStorage = { getItem: (key: string) => storage[key] ?? null };
        vi.stubGlobal('window', { localStorage });
        vi.stubGlobal('localStorage', localStorage);
        vi.mocked(api.createProject).mockResolvedValueOnce({ ...rawProject('created', 1, 'original create text'), originalText: 'original create text' });
        vi.mocked(api.updateModelSettings).mockResolvedValueOnce(rawProject('created', 3, 'after model defaults'));
        vi.mocked(api.updatePromptConfig).mockRejectedValueOnce(new Error('prompt default failed'));
        vi.mocked(api.getProject).mockResolvedValueOnce(readProject('created', 2, 'stale readback'));

        await state().createProject('Created', 'original create text', true, 'r2v');

        expect(api.updateModelSettings).toHaveBeenCalledTimes(1);
        expect(api.updatePromptConfig).toHaveBeenCalledTimes(1);
        expect(api.getProject).toHaveBeenCalledTimes(1);
        expect(state().currentProject).toMatchObject({
            id: 'created',
            revision: 1,
            originalText: 'original create text',
        });
        expect(state().readWatermarks[envKey()].project?.created).toBe(3);
        expect(state().runningOps[projectReadFailureKey('created')]).toBe(true);
    });


    it('does not accept a readback after a default write resolves without a revision floor', async () => {
        const storage: Record<string, string> = {
            [defaultPromptKey()]: JSON.stringify({ entity_extraction: 'custom extraction prompt' }),
        };
        const localStorage = { getItem: (key: string) => storage[key] ?? null };
        vi.stubGlobal('window', { localStorage });
        vi.stubGlobal('localStorage', localStorage);
        vi.mocked(api.createProject).mockResolvedValueOnce({ ...rawProject('created', 1, 'original create text'), originalText: 'original create text' });
        vi.mocked(api.updatePromptConfig).mockResolvedValueOnce({ prompt_config: { entity_extraction: 'custom extraction prompt' } });
        vi.mocked(api.getProject).mockResolvedValueOnce(readProject('created', 2, 'readback without write proof'));

        await state().createProject('Created', 'original create text', true, 'r2v');

        expect(api.getProject).toHaveBeenCalledTimes(1);
        expect(state().currentProject).toMatchObject({
            id: 'created',
            revision: 1,
            originalText: 'original create text',
        });
        expect(state().runningOps[projectReadFailureKey('created')]).toBe(true);
    });
});
