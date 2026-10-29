import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { deriveStatus } from '../ProjectCard';
import type { Project } from '@/store/projectStore';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';
const currentEnvKey = () => getRuntimeEnvironment()?.key ?? activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID }).key;

function project(extra: Partial<Project> = {}): Project {
    return {
        id: 'p', revision: 7, readEnvironment: currentEnvKey(), title: 'Project', originalText: '',
        characters: [], scenes: [], props: [], frames: [], status: 'draft', createdAt: '', updatedAt: '', ...extra,
    };
}

beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
});

afterEach(() => {
    clearRuntimeEnvironment();
});

describe('ProjectCard merge freshness status', () => {
    it('does not mark a project completed from a merged URL without a current backend proof', () => {
        expect(deriveStatus(project({ merged_video_url: '/video/old.mp4' }))).toBe('pending');
        expect(deriveStatus(project({ merged_video_url: '/video/old.mp4',
            merged_video_freshness: { state: 'stale', reason: 'inputs_changed', project_revision: 7 } }))).toBe('pending');
    });

    it('marks a project completed only when the merge proof matches the displayed revision', () => {
        expect(deriveStatus(project({ merged_video_url: '/video/final.mp4',
            merged_video_freshness: { state: 'current', reason: 'current', project_revision: 7 } }))).toBe('completed');
        expect(deriveStatus(project({ merged_video_url: '/video/final.mp4', revision: 8,
            merged_video_freshness: { state: 'current', reason: 'current', project_revision: 7 } }))).toBe('pending');
    });
});
