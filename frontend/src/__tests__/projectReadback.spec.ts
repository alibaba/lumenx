import { beforeEach, describe, expect, it, vi } from 'vitest';
import { API_URL, api } from '@/lib/api';
import { projectReadSnapshot } from '@/lib/frameCommandSnapshot';
import { ProjectReadbackError, readProjectOnce } from '@/lib/projectReadback';
import { useProjectStore } from '@/store/projectStore';

vi.mock('@/lib/api', async importOriginal => {
    const actual = await importOriginal<typeof import('@/lib/api')>();
    return { ...actual, api: { ...actual.api, getProject: vi.fn() } };
});

function rawProject(revision: number) {
    return {
        id: 'a', revision, title: 'Project a', original_text: 'original text',
        frames: [{ id: 'f1', action_description: 'prompt', scene_id: '', character_ids: [], prop_ids: [] }],
        characters: [], scenes: [], props: [], video_tasks: [], status: 'draft',
        created_at: 1000, updated_at: 2000,
        read_versions: { project: revision, library: revision, series: null },
    };
}

beforeEach(() => {
    vi.resetAllMocks();
    const current = projectReadSnapshot('a', rawProject(9), API_URL);
    useProjectStore.setState({ projects: [current], currentProject: current, currentSeries: null, readWatermarks: {}, runningOps: {} });
});

describe('readProjectOnce', () => {
    it('rejects a guarded stale session before mutating the store', async () => {
        vi.mocked(api.getProject).mockResolvedValue(rawProject(10));

        await expect(readProjectOnce('a', undefined, { isCurrent: () => false })).rejects.toBeInstanceOf(ProjectReadbackError);

        expect(useProjectStore.getState().currentProject?.revision).toBe(9);
        expect(useProjectStore.getState().projects.find(project => project.id === 'a')?.revision).toBe(9);
    });
});
