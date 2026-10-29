import { beforeEach, describe, expect, it } from 'vitest';
import type { DialogueAudioState } from '@/lib/api';
import { audioStateMeetsKnownVersions, recordDialogueAudioRead } from '@/lib/dialogueAudioState';
import { getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore } from '@/store/projectStore';

function read(project = 4, series = 2, library = 1) {
    return { id: 'p', revision: project, title: 'p', original_text: 'committed', created_at: 1, updated_at: 2,
        series_id: 's', frames: [{ id: 'f1', scene_id: 'scene' }], video_tasks: [], scenes: [], props: [],
        characters: [{ id: 'c1', name: `voice-${series}-${library}`, source: 'series' as const }],
        read_versions: { project, series: { id: 's', revision: series }, library } };
}

function audioState(project = 4, series = 2, library = 1): DialogueAudioState {
    return {
        project_id: 'p',
        frame_id: 'f1',
        read_versions: { project, series: { id: 's', revision: series }, library },
        options: {},
        freshness: 'current',
    };
}

const envKey = () => getRuntimeEnvironment()!.key;

beforeEach(() => {
    useProjectStore.setState({ currentProject: null, projects: [], readWatermarks: {}, runningOps: {} });
    useProjectStore.getState().applyProjectReadSnapshot('p', read(), true);
});

describe('dialogue audio state source floors', () => {
    it('records audio-only source floors without advancing the project snapshot', () => {
        const newer = audioState(4, 5, 10);
        const older = audioState(4, 4, 9);

        expect(recordDialogueAudioRead(newer)).toBe(true);
        expect(useProjectStore.getState().readWatermarks[envKey()].library).toBe(10);
        expect(useProjectStore.getState().readWatermarks[envKey()].series.s).toBe(5);
        expect(useProjectStore.getState().currentProject).toMatchObject({
            revision: 4,
            read_versions: { project: 4, series: { id: 's', revision: 2 }, library: 1 },
            characters: [{ id: 'c1', name: 'voice-2-1' }],
        });

        expect(audioStateMeetsKnownVersions(older)).toBe(false);
        expect(recordDialogueAudioRead(older)).toBe(false);
        expect(useProjectStore.getState().readWatermarks[envKey()].library).toBe(10);
        expect(useProjectStore.getState().readWatermarks[envKey()].series.s).toBe(5);
    });

    it('rejects an audio state from an older project revision before recording floors', () => {
        const olderProject = audioState(3, 8, 12);
        expect(recordDialogueAudioRead(olderProject)).toBe(false);
        expect(useProjectStore.getState().readWatermarks[envKey()].library).toBe(1);
        expect(useProjectStore.getState().readWatermarks[envKey()].series.s).toBe(2);
    });
});
