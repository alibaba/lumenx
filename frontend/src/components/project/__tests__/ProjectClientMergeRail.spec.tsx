import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ProjectClient from '../ProjectClient';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore, type Project } from '@/store/projectStore';
const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';
const currentEnvKey = () => getRuntimeEnvironment()?.key ?? activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID }).key;

vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('@/components/layout/EpisodeMiniList', () => ({ default: () => null }));
vi.mock('@/components/modules/ScriptProcessor', () => ({ default: () => null }));
vi.mock('@/components/modules/Cast', () => ({ default: () => null }));
vi.mock('@/components/modules/VideoGenerator', () => ({ default: () => null }));
vi.mock('@/components/modules/VideoAssembly', () => ({ default: () => null }));
vi.mock('@/components/modules/ConsistencyVault', () => ({ default: () => null }));
vi.mock('@/components/modules/ArtDirection', () => ({ default: () => null }));
vi.mock('@/components/modules/StoryboardComposer', () => ({ default: () => null }));
vi.mock('@/components/modules/StoryboardR2V', () => ({ default: () => null }));
vi.mock('@/components/modules/EntityConfirmModal', () => ({ default: () => null }));
vi.mock('@/components/common/ModelSettingsModal', () => ({ default: () => null }));
vi.mock('@/components/project/EnvConfigDialog', () => ({ default: () => null }));
vi.mock('@/components/project/PromptConfigModal', () => ({ default: () => null }));

function project(extra: Partial<Project> = {}): Project {
    return {
        id: 'p', revision: 7, readEnvironment: currentEnvKey(), workflow_mode: 'r2v', title: 'Project', originalText: '',
        characters: [], scenes: [], props: [], frames: [{ id: 'f1' }], video_tasks: [], status: 'draft', createdAt: '', updatedAt: '', ...extra,
    };
}

function renderProject(current: Project) {
    useProjectStore.setState({ projects: [current], currentProject: current, runningOps: {}, readWatermarks: {}, currentSeries: null });
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)));
    renderWithIntl(<ProjectClient id={current.id} />);
}

beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

afterEach(() => {
    clearRuntimeEnvironment();
});

describe('ProjectClient assembly rail merge freshness', () => {
    it('warns when the cached merged MP4 is stale', () => {
        renderProject(project({ merged_video_url: '/video/old.mp4',
            merged_video_freshness: { state: 'stale', reason: 'inputs_changed', project_revision: 7 } }));
        expect(screen.getByText('合成已过期')).toBeInTheDocument();
    });

    it('warns when a merged MP4 has no current proof', () => {
        renderProject(project({ merged_video_url: '/video/old.mp4' }));
        expect(screen.getByText('合成待确认')).toBeInTheDocument();
    });
});
