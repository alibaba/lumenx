import { describe, expect, it, vi } from 'vitest';
import { mergeVideoDisplayState, normalizeMergeFreshness, withoutMergeFreshness } from '@/lib/mergeFreshness';
import type { Project } from '@/store/projectStore';

describe('merge freshness proof normalization', () => {
    it('accepts only exact current proof for the displayed project revision and environment', () => {
        const project = { revision: 3, readEnvironment: 'http://api.test', merged_video_url: '/video/final.mp4',
            merged_video_freshness: { state: 'current', reason: 'current', project_revision: 3 } };
        expect(mergeVideoDisplayState(project, 'http://api.test')).toMatchObject({ kind: 'current', url: '/video/final.mp4' });
        expect(mergeVideoDisplayState({ ...project, readEnvironment: 'http://other.test' }, 'http://api.test').kind).toBe('unknown');
        expect(mergeVideoDisplayState({ ...project, revision: 4 }, 'http://api.test').kind).toBe('unknown');
    });

    it('treats malformed proof as unknown without rejecting the project', () => {
        expect(normalizeMergeFreshness({ state: 'current', reason: '', project_revision: 3 }, 3)).toEqual({
            state: 'unknown', reason: 'invalid_proof', project_revision: 3,
        });
        expect(mergeVideoDisplayState({ revision: 3, readEnvironment: 'http://api.test', merged_video_url: '/video/final.mp4',
            merged_video_freshness: { state: 'ready', project_revision: 3 } }, 'http://api.test').kind).toBe('unknown');
    });



    it('drops merge freshness from the persisted store projection while keeping the merged URL', async () => {
        vi.resetModules();
        vi.stubGlobal('localStorage', {
            getItem: vi.fn(() => null),
            setItem: vi.fn(),
            removeItem: vi.fn(),
        });
        const { useProjectStore } = await import('@/store/projectStore');
        const project = { id: 'p', revision: 3, merged_video_url: '/video/final.mp4',
            merged_video_freshness: { state: 'current', reason: 'current', project_revision: 3 } } as Project;
        const partialize = useProjectStore.persist.getOptions().partialize;
        expect(partialize).toBeTypeOf('function');
        if (!partialize) throw new Error('persist partialize is missing');

        const persisted = partialize({ ...useProjectStore.getState(), activeEnvironmentKey: 'env-a', projects: [project] }) as unknown as {
            environments: Record<string, { projects: Project[] }>;
        };

        expect(persisted.environments['env-a'].projects[0].merged_video_url).toBe('/video/final.mp4');
        expect(persisted.environments['env-a'].projects[0]).not.toHaveProperty('merged_video_freshness');
        vi.unstubAllGlobals();
    });

    it('strips response-only proof from cached project projection without dropping the URL', () => {
        const project = { id: 'p', revision: 3, merged_video_url: '/video/final.mp4',
            merged_video_freshness: { state: 'current', reason: 'current', project_revision: 3 } };
        expect(withoutMergeFreshness(project)).toEqual({ id: 'p', revision: 3, merged_video_url: '/video/final.mp4' });
    });
});
