import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ProjectClient from '../ProjectClient';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from '../../modules/__tests__/frame-command-fixtures';

vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('@/components/layout/PipelineSidebar', () => ({ default: () => null }));
vi.mock('@/components/layout/EpisodeMiniList', () => ({ default: () => null }));
vi.mock('@/components/modules/ScriptProcessor', () => ({ default: () => <p>Cached project content</p> }));
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

beforeEach(() => {
    localStorage.clear(); vi.restoreAllMocks();
    useProjectStore.setState({ projects: [project()], currentProject: project(), runningOps: {}, readWatermarks: {}, currentSeries: null });
});
afterEach(() => vi.unstubAllGlobals());

describe('project read confirmation in the actual project shell', () => {
    it('keeps cached content with a visible warning until a user-requested read confirms all sources', async () => {
        useProjectStore.getState().applyProjectReadSnapshot('a', enriched(ack([], 'a', 3), 8));
        const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => enriched(ack([], 'a', 4), 7) })
            .mockResolvedValueOnce({ ok: true, json: async () => enriched(ack([], 'a', 4), 8) });
        vi.stubGlobal('fetch', fetchMock);
        renderWithIntl(<ProjectClient id="a" />);
        expect(await screen.findByRole('alert')).toHaveTextContent(/未能确认最新项目与共享素材/);
        expect(screen.getByText('Cached project content')).toBeInTheDocument();
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
        expect(fetchMock).toHaveBeenCalledOnce();
        fireEvent.click(screen.getByRole('button', { name: '重新读取' }));
        await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(useProjectStore.getState().currentProject?.revision).toBe(4);
    });

    it('labels a legacy HTTP response unconfirmed without starting extra reads', async () => {
        const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ack() });
        vi.stubGlobal('fetch', fetchMock);
        renderWithIntl(<ProjectClient id="a" />);
        expect(await screen.findByRole('alert')).toHaveTextContent(/未能确认最新项目与共享素材/);
        expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('shows a failed network read even when the displayed cache previously had confirmed sources', async () => {
        useProjectStore.getState().applyProjectReadSnapshot('a', enriched(ack([], 'a', 3), 8));
        const fetchMock = vi.fn().mockRejectedValue(new Error('offline'));
        vi.stubGlobal('fetch', fetchMock);
        renderWithIntl(<ProjectClient id="a" />);
        expect(await screen.findByRole('alert')).toHaveTextContent(/未能确认/);
        expect(useProjectStore.getState().currentProject?.sharedAssetsNeedRefresh).toBe(false);
        expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('shows a projection mismatch even when both copies meet the source floor', async () => {
        useProjectStore.getState().applyProjectReadSnapshot('a', enriched(ack([], 'a', 4), 8));
        const read = deferred();
        vi.stubGlobal('fetch', vi.fn().mockReturnValue(read.promise));
        renderWithIntl(<ProjectClient id="a" />);
        const cache = useProjectStore.getState().projects[0];
        act(() => useProjectStore.setState({ projects: [{ ...cache, revision: 5,
            read_versions: { ...cache.read_versions!, project: 5 } }] }));
        await act(async () => read.resolve({ ok: true, json: async () => enriched(ack([], 'a', 3), 8) }));
        expect(screen.getByRole('alert')).toHaveTextContent(/未能确认/);
        expect(useProjectStore.getState().currentProject?.sharedAssetsNeedRefresh).toBe(false);
        expect(useProjectStore.getState().currentProject?.revision).toBe(4);
        expect(useProjectStore.getState().projects[0].revision).toBe(5);
        expect(fetch).toHaveBeenCalledOnce();
    });
});
