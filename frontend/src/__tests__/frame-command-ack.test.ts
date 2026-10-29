import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore, type Project } from '@/store/projectStore';

const envKey = () => getRuntimeEnvironment()!.key;

function project(id = 'a', revision = 3): Project {
    return { id, revision, readEnvironment: envKey(), title: id, originalText: 'old text', frames: [{ id: 'old' }],
        characters: [{ id: 'local', name: 'local', source: 'episode' }, { id: 'shared', name: 'shared', source: 'series' },
            { id: 'global', name: 'global', source: 'global' }, { id: 'unknown', name: 'unknown' }],
        scenes: [], props: [], status: 'draft', createdAt: 'old', updatedAt: 'old' };
}
function ack(id = 'a', revision = 4) {
    return { id, revision, title: 'saved title', original_text: 'saved text',
        frames: [{ id: 'f2' }, { id: 'f1' }], characters: [{ id: 'local' }, { id: 'shared' }],
        scenes: [], props: [], video_tasks: [], created_at: 1000, updated_at: 2000,
        art_direction: null, workflow_mode: 'r2v' };
}
function apply(value: unknown = ack()) {
    return useProjectStore.getState().applyFrameCommandSnapshot('a', value);
}
const read = (id = 'a', revision = 4) => ({ ...ack(id, revision), read_versions: { project: revision, series: null, library: 0 } });
const editKey = () => `frame-edit:${encodeURIComponent(envKey())}:a:1`;
beforeEach(() => useProjectStore.setState({ readWatermarks: {} }));
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(done => { resolve = done; });
    return { promise, resolve };
}

describe('frame command snapshots and project selection', () => {
    afterEach(() => vi.unstubAllGlobals());
    beforeEach(() => {
        vi.restoreAllMocks();
        useProjectStore.setState({ projects: [project()], currentProject: project(), currentSeries: null, runningOps: {} });
    });

    it('maps a complete ACK without associating old text or metadata with the new revision', () => {
        apply();
        const saved = useProjectStore.getState().currentProject!;
        expect(saved).toMatchObject({ revision: 4, title: 'saved title', originalText: 'saved text',
            createdAt: new Date(1000000).toISOString(), updatedAt: new Date(2000000).toISOString(), art_direction: null });
        expect(saved.characters.map(c => [c.id, c.source])).toEqual([
            ['local', 'episode'], ['shared', 'episode'], ['global', 'global'],
        ]);
        expect(saved.frames.map(f => f.id)).toEqual(['f2', 'f1']);
    });

    it.each(['currentProject', 'projects'] as const)('protects the newer %s independently of the other projection', projection => {
        const newer = project('a', 5);
        useProjectStore.setState(projection === 'currentProject' ? { currentProject: newer } : { projects: [newer] });
        apply();
        const state = useProjectStore.getState();
        expect(state.currentProject?.revision).toBe(projection === 'currentProject' ? 5 : 4);
        expect(state.projects[0].revision).toBe(projection === 'projects' ? 5 : 4);
    });

    it('updates cached A without navigating away from B', () => {
        useProjectStore.setState({ currentProject: project('b') });
        apply();
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(useProjectStore.getState().projects[0].revision).toBe(4);
    });

    it.each([null, {}, { ...ack(), id: 'b' }, { ...ack(), revision: -1 },
        { ...ack(), revision: 1.2 }, { ...ack(), frames: null }, { ...ack(), original_text: undefined }])(
        'rejects an invalid ACK without touching either projection: %j', value => {
            const before = useProjectStore.getState();
            expect(() => apply(value)).toThrow();
            expect(useProjectStore.getState()).toBe(before);
        },
    );

    it('keeps the latest selection when A and B reads finish in reverse order', async () => {
        const a = deferred<unknown>(); const b = deferred<unknown>();
        vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise));
        useProjectStore.setState({ projects: [project('a'), project('b')] });
        const selectA = useProjectStore.getState().selectProject('a');
        const selectB = useProjectStore.getState().selectProject('b');
        b.resolve({ ok: true, json: async () => ack('b') }); await selectB;
        a.resolve({ ok: true, json: async () => ack('a') }); await selectA;
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        expect(useProjectStore.getState().projects.find(p => p.id === 'a')?.revision).toBe(4);
    });

    it('does not regress an ACK with an older pending selection read or reopen a cleared project', async () => {
        const read = deferred<unknown>();
        vi.stubGlobal('fetch', vi.fn().mockReturnValue(read.promise));
        const selection = useProjectStore.getState().selectProject('a');
        apply(ack('a', 8));
        read.resolve({ ok: true, json: async () => ack('a', 4) }); await selection;
        expect(useProjectStore.getState().currentProject?.revision).toBe(8);
        expect(useProjectStore.getState().projects[0].revision).toBe(8);

        const late = deferred<unknown>();
        vi.stubGlobal('fetch', vi.fn().mockReturnValue(late.promise));
        const loading = useProjectStore.getState().selectProject('a');
        useProjectStore.getState().clearCurrentProject();
        late.resolve({ ok: true, json: async () => ack('a', 9) }); await loading;
        expect(useProjectStore.getState().currentProject).toBeNull();
    });

    it('clears a failed edit guard only after a confirmed project read with no outstanding edit', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => read() }));
        const failed = `frame-edit-failed:${encodeURIComponent(envKey())}:a:2`;
        const edit = editKey();
        useProjectStore.setState({ runningOps: { [failed]: true, [edit]: true } });
        await useProjectStore.getState().selectProject('a');
        expect(useProjectStore.getState().runningOps[failed]).toBe(true);
        useProjectStore.getState().setRunningOp(edit, false);
        await useProjectStore.getState().selectProject('a');
        expect(useProjectStore.getState().runningOps[failed]).toBeUndefined();
    });

    it('does not clear an edit failure that happened after a project read started', async () => {
        const read = deferred<unknown>();
        vi.stubGlobal('fetch', vi.fn().mockReturnValue(read.promise));
        const existing = `frame-edit-failed:${encodeURIComponent(envKey())}:a:1`;
        const later = `frame-edit-failed:${encodeURIComponent(envKey())}:a:2`;
        useProjectStore.setState({ runningOps: { [existing]: true } });
        const selecting = useProjectStore.getState().selectProject('a');
        useProjectStore.getState().setRunningOp(later, true);
        read.resolve({ ok: true, json: async () => ack() }); await selecting;
        expect(useProjectStore.getState().runningOps[later]).toBe(true);
    });
});

describe('shared asset parent identity', () => {
    it.each([null, 'new-series'])('drops old series assets when the committed parent becomes %s', seriesId => {
        const prior = { ...project(), series_id: 'old-series' };
        useProjectStore.setState({ projects: [prior], currentProject: prior });
        apply({ ...ack(), characters: [], series_id: seriesId });
        for (const saved of [useProjectStore.getState().currentProject!, useProjectStore.getState().projects[0]]) {
            expect(saved.characters.some(c => c.source === 'series')).toBe(false);
            expect(saved.characters.some(c => c.source === 'global')).toBe(true);
        }
    });
});

describe('enriched read snapshots', () => {
    it.each(['currentProject', 'projects'] as const)('preserves sources and independently protects a newer %s', projection => {
        const newer = { ...project('a', 6), series_id: 's', sharedAssetsNeedRefresh: true };
        useProjectStore.setState({ projects: [project()], currentProject: project() });
        useProjectStore.setState(projection === 'currentProject' ? { currentProject: newer } : { projects: [newer] });
        useProjectStore.getState().applyProjectReadSnapshot('a', { ...ack(), series_id: 'new-series',
            read_versions: { project: 4, series: { id: 'new-series', revision: 0 }, library: 0 },
            characters: [{ id: 's1', name: 'series character', source: 'series' }, { id: 'g1', name: 'global', source: 'global' }] });
        const state = useProjectStore.getState();
        const updated = projection === 'currentProject' ? state.projects[0] : state.currentProject!;
        const protectedProject = projection === 'currentProject' ? state.currentProject! : state.projects[0];
        expect(updated).toMatchObject({ revision: 4, series_id: 'new-series', sharedAssetsNeedRefresh: false });
        expect(updated.characters.map(c => c.source)).toEqual(['series', 'global']);
        expect(protectedProject).toBe(newer);
    });
});

describe('reopen acknowledgement races', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
        useProjectStore.setState({ projects: [project()], currentProject: project(), currentSeries: null, runningOps: {} });
    });
    afterEach(() => vi.unstubAllGlobals());

    it.each([null, 's2'])('does not restore the old parent after the project parent changed to %s', async seriesId => {
        const seriesRead = deferred<unknown>();
        vi.spyOn(axios, 'get').mockReturnValueOnce(seriesRead.promise);
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...ack(), series_id: 's1',
            read_versions: { project: 4, series: { id: 's1', revision: 0 }, library: 0 } }) }));
        const selecting = useProjectStore.getState().selectProject('a');
        // Let the project GET finish and leave its subsequent series GET pending.
        await vi.waitFor(() => expect(axios.get).toHaveBeenCalledWith(expect.stringContaining('/series/s1')));
        apply({ ...ack('a', 5), series_id: seriesId });
        seriesRead.resolve({ data: { id: 's1', title: 'old series', revision: 1 } });
        await selecting;
        expect(useProjectStore.getState().currentProject?.series_id).toBe(seriesId);
        expect(useProjectStore.getState().currentSeries).toBeNull();
    });

    it.each(['currentProject', 'projects'] as const)('keeps failure guards when a reopen read is older than %s', async projection => {
        const failed = `frame-edit-failed:${encodeURIComponent(envKey())}:a:1`;
        const commandError = `frame-command-error:${encodeURIComponent(envKey())}:a:savedReadFailed`;
        useProjectStore.setState({ runningOps: { [failed]: true, [commandError]: true } });
        const read = deferred<unknown>();
        vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(read.promise));
        const selecting = useProjectStore.getState().selectProject('a');
        useProjectStore.setState(projection === 'currentProject' ? { currentProject: project('a', 7) } : { projects: [project('a', 7)] });
        read.resolve({ ok: true, json: async () => ack('a', 4) }); await selecting;
        expect(useProjectStore.getState().runningOps[failed]).toBe(true);
        expect(useProjectStore.getState().runningOps[commandError]).toBe(true);
    });
});
