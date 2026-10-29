import type { Project } from '@/store/projectStore';
import { activateRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';

const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';

export function currentReadEnvironment() {
    return getRuntimeEnvironment()?.key
        ?? activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID }).key;
}

export function frame(id: string) {
    return { id, action_description: `prompt ${id}`, scene_id: '', character_ids: [], prop_ids: [],
        dialogue: '', image_url: '', video_url: '', workbench_tab_mode: 'direct_r2v', locked: false };
}
export function project(id = 'a', revision = 3): Project {
    return { id, revision, readEnvironment: currentReadEnvironment(), title: `Project ${id}`, originalText: 'original text', frames: [frame('f1'), frame('f2')],
        characters: [{ id: 'shared', name: 'shared', source: 'series' }],
        scenes: [], props: [], video_tasks: [], status: 'draft', createdAt: 'old', updatedAt: 'old' };
}
export function ack(frames: Record<string, unknown>[] = [frame('f1'), frame('copy'), frame('f2')], id = 'a', revision = 4) {
    return { id, revision, title: `Project ${id}`, original_text: 'server text', frames,
        characters: [], scenes: [], props: [], video_tasks: [], created_at: 1000, updated_at: 2000,
        art_direction: null, workflow_mode: 'r2v' };
}
export function deferred<T = unknown>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
    return { promise, resolve, reject };
}

/** GET/enriched endpoints carry source proof; raw frame ACKs deliberately do not. */
export function enriched<T extends { revision: number; series_id?: string | null }>(value: T, library = 0, series = 0) {
    return { ...value, read_versions: { project: value.revision,
        series: value.series_id ? { id: value.series_id, revision: series } : null, library } };
}
