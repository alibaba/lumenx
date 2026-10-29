import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore, type Series, type Project } from '@/store/projectStore';

vi.mock('@/lib/api', async importOriginal => {
    const original = await importOriginal<typeof import('@/lib/api')>();
    return { ...original, api: { ...original.api, listSeries: vi.fn() } };
});
const series = (revision: number): Series => ({ id: 's', revision, title: `Series ${revision}`, description: '', characters: [], scenes: [], props: [], episode_ids: [], created_at: 1, updated_at: revision });
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; };
const envKey = () => getRuntimeEnvironment()!.key;
beforeEach(() => { vi.clearAllMocks(); useProjectStore.setState({ seriesList: [], readWatermarks: {}, currentProject: null, projects: [], currentSeries: null }); });

describe('workspace series list refresh', () => {
    it('does not let an older list completion replace a later refresh', async () => {
        const first = deferred<Series[]>();
        vi.mocked(api.listSeries).mockReturnValueOnce(first.promise).mockResolvedValueOnce([series(4)]);
        const pending = useProjectStore.getState().fetchSeriesList();
        await useProjectStore.getState().fetchSeriesList();
        first.resolve([series(2)]);
        await pending;
        expect(useProjectStore.getState().seriesList).toEqual([series(4)]);
    });
    it('does not regress a known committed series revision', async () => {
        useProjectStore.setState({ seriesList: [series(4)] });
        vi.mocked(api.listSeries).mockResolvedValueOnce([series(2)]);
        await useProjectStore.getState().fetchSeriesList();
        expect(useProjectStore.getState().seriesList).toEqual([series(4)]);
    });
    it('rejects a list older than a series revision observed in a project read', async () => {
        useProjectStore.setState({ seriesList: [series(4)], readWatermarks: { [envKey()]: { series: { s: 5 } } } });
        vi.mocked(api.listSeries).mockResolvedValueOnce([series(4)]);
        expect(await useProjectStore.getState().fetchSeriesList()).toBe(false);
        expect(useProjectStore.getState().seriesList).toEqual([series(4)]);
    });
    it('marks old shared project assets stale after a higher committed series list revision', async () => {
        const project = { id: 'p', revision: 1, series_id: 's', readEnvironment: envKey(),
            read_versions: { project: 1, library: 0, series: { id: 's', revision: 2 } }, sharedAssetsNeedRefresh: false } as Project;
        useProjectStore.setState({ seriesList: [series(2)], projects: [project], currentProject: project, currentSeries: series(2) });
        vi.mocked(api.listSeries).mockResolvedValueOnce([series(4)]);
        expect(await useProjectStore.getState().fetchSeriesList()).toBe(true);
        expect(useProjectStore.getState().currentProject?.sharedAssetsNeedRefresh).toBe(true);
        expect(useProjectStore.getState().currentSeries).toBeNull();
        expect(useProjectStore.getState().readWatermarks[envKey()].series.s).toBe(4);
    });
    it('preserves an omitted series when its source revision advances during the list request', async () => {
        const pending = deferred<Series[]>();
        useProjectStore.setState({ seriesList: [series(2)], readWatermarks: { [envKey()]: { series: { s: 2 } } } });
        vi.mocked(api.listSeries).mockReturnValueOnce(pending.promise);
        const reading = useProjectStore.getState().fetchSeriesList();
        useProjectStore.setState({ readWatermarks: { [envKey()]: { series: { s: 5 } } } });
        pending.resolve([]);
        await reading;
        expect(useProjectStore.getState().seriesList).toEqual([series(2)]);
    });
    it('clears an unchanged series when a new authoritative list is empty', async () => {
        useProjectStore.setState({ seriesList: [series(4)], readWatermarks: { [envKey()]: { series: { s: 4 } } } });
        vi.mocked(api.listSeries).mockResolvedValueOnce([]);
        expect(await useProjectStore.getState().fetchSeriesList()).toBe(true);
        expect(useProjectStore.getState().seriesList).toEqual([]);
    });
    it('reports malformed responses without discarding the visible list', async () => {
        useProjectStore.setState({ seriesList: [series(4)] });
        vi.mocked(api.listSeries).mockResolvedValueOnce(null as unknown as Series[]);
        expect(await useProjectStore.getState().fetchSeriesList()).toBe(false);
        expect(useProjectStore.getState().seriesList).toEqual([series(4)]);
    });
});
