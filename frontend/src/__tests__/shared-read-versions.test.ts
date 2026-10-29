import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore } from '@/store/projectStore';

function read(id = 'a', project = 3, series = 2, library = 4) {
    return { id, revision: project, title: id, original_text: 'committed', created_at: 1, updated_at: 2,
        series_id: 's', frames: [], video_tasks: [], scenes: [], props: [],
        characters: [{ id: 'shared', name: `voice-${series}-${library}`, source: 'series' as const }],
        read_versions: { project, series: { id: 's', revision: series }, library } };
}
function raw(id = 'a', project = 3) {
    const value: Partial<ReturnType<typeof read>> = read(id, project);
    delete value.read_versions;
    return value;
}
const state = () => useProjectStore.getState();
const apply = (value: unknown, id = 'a') => state().applyProjectReadSnapshot(id, value);
const envKey = () => getRuntimeEnvironment()!.key;
function seed(id = 'a') {
    const wire = read(id);
    return { ...wire, originalText: wire.original_text, createdAt: 'old', updatedAt: 'old', status: 'draft' };
}
beforeEach(() => {
    vi.restoreAllMocks();
    useProjectStore.setState({ projects: [seed(), seed('b')], currentProject: seed(), readWatermarks: {}, runningOps: {} });
});
afterEach(() => vi.unstubAllGlobals());

describe('shared read versions through the actual store', () => {
    it('accepts a newer shared source without changing the project revision and rejects a late older source', () => {
        apply(read()); apply(read('a', 3, 3, 5));
        expect(apply(read())).toMatchObject({ status: 'superseded' });
        expect(state().currentProject?.characters[0].name).toBe('voice-3-5');
        expect(state().currentProject?.revision).toBe(3);
    });

    it('marks other project projections stale and protects B against a library version older than seen in A', () => {
        apply(read('b'), 'b'); apply(read('a', 3, 2, 8));
        expect(state().projects[1].sharedAssetsNeedRefresh).toBe(true);
        expect(apply(read('b', 3, 2, 6), 'b')).toMatchObject({ status: 'rejected' });
        expect(state().projects[1].read_versions?.library).toBe(4);
        expect(state().projects[1].characters[0].name).toBe('voice-2-4');
    });

    it('retains the source watermark after its observing project is removed from the cache', () => {
        apply(read('a', 3, 2, 8));
        useProjectStore.setState({ projects: [seed('b')], currentProject: seed('b') });
        expect(apply(read('b', 3, 2, 7), 'b')).toMatchObject({ status: 'rejected' });
        expect(state().currentProject?.readEnvironment).toBeUndefined();
    });

    it('rejects incomparable project/shared vectors but records the valid newer source observation', () => {
        apply(read('a', 5, 2, 4));
        expect(apply(read('a', 4, 3, 4))).toMatchObject({ status: 'rejected', reason: 'incomparable' });
        expect(state().currentProject).toMatchObject({ revision: 5, sharedAssetsNeedRefresh: true });
        expect(apply(read('b', 9, 2, 4), 'b')).toMatchObject({ status: 'rejected' });
        expect(apply(read('a', 5, 3, 4))).toMatchObject({ status: 'applied' });
    });

    it.each(['currentProject', 'projects'] as const)('never reports success while %s contains an incompatible vector', projection => {
        apply(read());
        const newer = { ...seed(), revision: 5, read_versions: { project: 5, series: { id: 's', revision: 2 }, library: 4 }, readEnvironment: envKey() };
        useProjectStore.setState(projection === 'currentProject' ? { currentProject: newer } : { projects: [newer, seed('b')] });
        expect(apply(read('a', 4, 3, 4))).toMatchObject({ status: 'rejected' });
        expect(state()[projection === 'currentProject' ? 'currentProject' : 'projects']).toBeTruthy();
    });

    it('can supersede an old reply only with two consistent confirmed projections', () => {
        apply(read('a', 6, 5, 9));
        expect(apply(read('a', 4, 3, 7))).toMatchObject({ status: 'superseded' });
        useProjectStore.setState({ projects: [{ ...state().projects[0], revision: 7,
            read_versions: { project: 7, series: { id: 's', revision: 5 }, library: 9 } }] });
        expect(apply(read('a', 4, 3, 7))).toMatchObject({ status: 'rejected', reason: 'projection_mismatch' });
    });

    it('uses the known target cache when a selection response is older than B while A is current', () => {
        apply(read('b', 8), 'b');
        const result = state().applyProjectReadSnapshot('b', read('b', 7), true);
        expect(result).toMatchObject({ status: 'superseded' });
        expect(state().currentProject).toMatchObject({ id: 'b', revision: 8 });
        expect(state().projects.find(project => project.id === 'b')?.revision).toBe(8);
    });


    it('reads selected projects through the app access fetch channel', async () => {
        const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => read('a', 3, 2) });
        vi.stubGlobal('fetch', fetchMock);
        vi.spyOn(axios, 'get').mockResolvedValue({ data: { id: 's', revision: 2, title: 'series', description: '', characters: [], scenes: [], props: [], episode_ids: [], created_at: 1, updated_at: 1 } });

        await state().selectProject('a');

        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/projects/a`, expect.objectContaining({ credentials: 'include' }));
    });

    it('does not clear failure guards on a reopen whose shared source is stale', async () => {
        apply(read('a', 3, 5));
        state().setRunningOp('frame-edit-failed:a:1:savedReadFailed', true);
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => read('a', 4, 4) }));
        vi.spyOn(axios, 'get').mockResolvedValue({ data: { id: 's', revision: 5 } });
        await state().selectProject('a');
        expect(state().runningOps['frame-edit-failed:a:1:savedReadFailed']).toBe(true);
        expect(state().currentProject?.revision).toBe(3);
    });

    it('does not expose a cached or delayed parent older than the confirmed series source', async () => {
        apply(read('a', 3, 5));
        let resolve!: (response: unknown) => void;
        vi.spyOn(axios, 'get').mockReturnValue(new Promise(done => { resolve = done; }));
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => read('a', 3, 5) }));
        const old = { id: 's', revision: 2, title: 'old', description: '', characters: [], scenes: [], props: [], episode_ids: [], created_at: 1, updated_at: 1 };
        useProjectStore.setState({ seriesList: [old] });
        const request = state().selectProject('a');
        await vi.waitFor(() => expect(axios.get).toHaveBeenCalled());
        expect(state().currentSeries).toBeNull();
        resolve({ data: old }); await request;
        expect(state().currentSeries).toBeNull();
    });

    it('marks an observed newer parent stale until an explicit project reread captures that revision', async () => {
        apply(read('a', 3, 2));
        const parent = { id: 's', revision: 3, title: 'new', description: '', characters: [], scenes: [], props: [], episode_ids: [], created_at: 1, updated_at: 1 };
        vi.spyOn(axios, 'get').mockResolvedValue({ data: parent });
        const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => read('a', 3, 2) })
            .mockResolvedValueOnce({ ok: true, json: async () => read('a', 3, 3) });
        vi.stubGlobal('fetch', fetchMock);
        await state().selectProject('a');
        expect(state().currentSeries).toBeNull();
        expect(state().currentProject?.sharedAssetsNeedRefresh).toBe(true);
        expect(state().readWatermarks[envKey()].series.s).toBe(3);
        expect(fetchMock).toHaveBeenCalledOnce();
        expect(apply(read('b', 3, 2), 'b')).toMatchObject({ status: 'rejected' });
        await state().selectProject('a');
        expect(state().currentSeries?.revision).toBe(3);
        expect(state().currentProject?.sharedAssetsNeedRefresh).toBe(false);
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('invalidates an old parent when an enriched read advances the same series source', () => {
        apply(read('a', 3, 2));
        useProjectStore.setState({ currentSeries: { id: 's', revision: 2, title: 'old', description: '', characters: [], scenes: [], props: [], episode_ids: [], created_at: 1, updated_at: 1 } });
        apply(read('a', 3, 3));
        expect(state().currentSeries).toBeNull();
    });

    it.each([7, '', true])('rejects a non-string or empty series identity even when metadata repeats it: %j', series_id => {
        const before = state();
        expect(() => apply({ ...read(), series_id, read_versions: { project: 3, series: { id: series_id, revision: 2 }, library: 4 } })).toThrow();
        expect(state()).toBe(before);
    });

    it('accepts legacy HTTP only as unconfirmed until a relevant source floor is established', () => {
        const legacy = raw();
        expect(apply(legacy)).toMatchObject({ status: 'unconfirmed' });
        expect(state().currentProject).toMatchObject({ sharedAssetsNeedRefresh: true });
        expect(state().currentProject?.read_versions).toBeUndefined();
        apply(read());
        expect(apply({ ...legacy, revision: 99 })).toMatchObject({ status: 'rejected', reason: 'unknown_sources' });
        expect(state().currentProject?.revision).toBe(3);
    });


    it('rejects raw and enriched reads older than the observed project command floor', () => {
        useProjectStore.setState({ readWatermarks: { [envKey()]: { series: {}, project: { a: 5 } } } });

        expect(apply(read('a', 4))).toMatchObject({ status: 'rejected' });
        expect(apply(raw('a', 4))).toMatchObject({ status: 'rejected', reason: 'unknown_sources' });
        expect(apply(read('a', 5))).toMatchObject({ status: 'applied' });
    });

    it('does not let a projection below the project command floor dominate an older read', () => {
        const belowFloor = { ...seed(), revision: 4, read_versions: { project: 4, series: { id: 's', revision: 2 }, library: 4 }, readEnvironment: envKey() };
        useProjectStore.setState({ projects: [belowFloor, seed('b')], currentProject: belowFloor,
            readWatermarks: { [envKey()]: { series: {}, project: { a: 5 } } } });

        expect(apply(read('a', 3))).toMatchObject({ status: 'rejected' });
        expect(state().currentProject?.revision).toBe(4);
    });

    it('lets a projection at the project command floor supersede an older read', () => {
        const confirmed = { ...seed(), revision: 5, read_versions: { project: 5, series: { id: 's', revision: 2 }, library: 4 }, readEnvironment: envKey() };
        useProjectStore.setState({ projects: [confirmed, seed('b')], currentProject: confirmed,
            readWatermarks: { [envKey()]: { series: {}, project: { a: 5 } } } });

        expect(apply(read('a', 4))).toMatchObject({ status: 'superseded' });
        expect(state().currentProject?.revision).toBe(5);
    });

    it.each([undefined, 'another-environment'])('does not seed watermarks from cache without this environment: %s', readEnvironment => {
        const prior = { ...seed('b'), readEnvironment, read_versions: { project: 3, series: { id: 's', revision: 200 }, library: 200 } };
        useProjectStore.setState({ projects: [seed(), prior] });
        expect(apply(read())).toMatchObject({ status: 'applied' });
        expect(state().currentProject?.readEnvironment).toBe(envKey());
    });

    it.each([undefined, 'another-environment'])('does not let an untrusted scalar revision block the first real read: %s', readEnvironment => {
        const prior = { ...seed(), revision: 100, readEnvironment };
        useProjectStore.setState({ projects: [prior], currentProject: prior });
        expect(apply(read('a', 1))).toMatchObject({ status: 'applied' });
        expect(state().currentProject?.revision).toBe(1);
    });

    it('seeds watermarks from validated persisted metadata belonging to this environment', () => {
        useProjectStore.setState({ projects: [seed(), { ...seed('b'), readEnvironment: envKey(),
            read_versions: { project: 3, series: { id: 's', revision: 10 }, library: 10 } }] });
        expect(apply(read())).toMatchObject({ status: 'rejected' });
    });

    it.each(['constructor', '__proto__'])('compares an opaque series identity without treating it as an object property: %s', id => {
        const response = { ...read(), series_id: id, read_versions: { project: 3, series: { id, revision: 2 }, library: 4 } };
        expect(apply(response)).toMatchObject({ status: 'applied' });
        expect(state().readWatermarks[envKey()].series[id]).toBe(2);
        expect(apply({ ...response, read_versions: { ...response.read_versions, series: { id, revision: 1 } } })).toMatchObject({ status: 'superseded' });
    });

    it.each([
        { project: 2, series: { id: 's', revision: 2 }, library: 4 },
        { project: 3, series: { id: 'other', revision: 2 }, library: 4 },
        { project: 3, series: null, library: 4 },
        { project: 3, series: { id: 's', revision: -1 }, library: 4 },
        { project: 3, series: { id: 's', revision: 2 }, library: 1.5 },
        { project: 3, series: { id: 's', revision: 2 } },
    ])('rejects malformed source proof atomically: %j', read_versions => {
        const before = state(); expect(() => apply({ ...read(), read_versions })).toThrow(); expect(state()).toBe(before);
    });

    it('treats dangling and previously numeric versions of the same series as incomparable', () => {
        apply(read());
        const dangling = { ...read('a', 4), characters: [], read_versions: { project: 4, series: { id: 's', revision: null }, library: 4 } };
        expect(apply(dangling)).toMatchObject({ status: 'rejected' });
        expect(state().currentProject?.characters).toHaveLength(1);
        expect(apply(read('a', 4, 1))).toMatchObject({ status: 'rejected' });
    });

    it('does not order a prior missing series against a numeric revision without a lineage proof', () => {
        const missing = { ...read(), characters: [], read_versions: { project: 3, series: { id: 's', revision: null }, library: 4 } };
        expect(apply(missing)).toMatchObject({ status: 'applied' });
        expect(apply(read('a', 4, 0))).toMatchObject({ status: 'rejected', reason: 'incomparable' });
        expect(state().currentProject?.read_versions?.series?.revision).toBeNull();
        expect(state().currentProject?.characters).toEqual([]);
    });

    it('retains known shared provenance on raw ACKs and drops only the changed parent component', () => {
        apply(read());
        const command = raw('a', 4);
        state().applyFrameCommandSnapshot('a', { ...command, characters: [] });
        expect(state().currentProject).toMatchObject({ readEnvironment: envKey(),
            read_versions: { project: 4, series: { id: 's', revision: 2 }, library: 4 } });
        state().applyFrameCommandSnapshot('a', { ...command, revision: 5, series_id: 'new-series', characters: [] });
        expect(state().currentProject?.read_versions?.series).toBeUndefined();
        expect(state().currentProject?.read_versions?.library).toBe(4);
        expect(state().currentProject?.sharedAssetsNeedRefresh).toBe(true);
        expect(state().currentProject?.characters).toEqual([]);
    });

    it('requires readback when removing an episode override could reveal a hidden global asset', () => {
        apply({ ...read(), series_id: null, characters: [{ id: 'collision', name: 'local', source: 'episode' }],
            read_versions: { project: 3, series: null, library: 4 } });
        state().applyFrameCommandSnapshot('a', { ...raw('a', 4), series_id: null, characters: [] });
        expect(state().currentProject?.sharedAssetsNeedRefresh).toBe(true);
        expect(state().currentProject?.characters).toEqual([]);
    });
});
