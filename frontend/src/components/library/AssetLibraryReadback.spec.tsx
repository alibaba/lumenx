import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { renderWithIntl } from '../series/__tests__/test-utils';
import { api } from '@/lib/api';
import { toast } from '@/store/toastStore';
import { useProjectStore, type Project } from '@/store/projectStore';
import AssetLibraryPage from './AssetLibraryPage';
import AssetInspector from './AssetInspector';
import NewLibraryAssetDialog from './NewLibraryAssetDialog';
import images from '@/__tests__/fixtures/inline-raster-display.json';
import { clearLibraryRecoveries } from '@/lib/libraryReadback';
const TEST_STORAGE_ID = '11111111-1111-4111-8111-111111111111';
const currentEnvKey = () => getRuntimeEnvironment()?.key ?? activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID }).key;

vi.mock('@/lib/api', () => ({ API_URL: 'http://test.invalid', api: {
  listSeries: vi.fn(), getProjects: vi.fn(), listLibraryAssets: vi.fn(),
  createLibraryAsset: vi.fn(), promoteAssetToLibrary: vi.fn(),
  generateAsset: vi.fn(), getTaskStatus: vi.fn(), getProject: vi.fn(),
  toggleSeriesAssetStarred: vi.fn(), toggleAssetStarred: vi.fn(), updateLibraryAsset: vi.fn(),
} }));

vi.mock('@/store/toastStore', () => ({ toast: {
  success: vi.fn(), error: vi.fn(), progress: vi.fn(() => 'toast-1'), update: vi.fn(),
} }));

type Asset = { status?: string; id: string; name: string; description?: string; image_url?: string; image_asset?: { selected_id?: string; variants: { id: string; url: string; created_at?: number }[] }; reference_sheet?: { selected_image_id?: string; image_variants: { id: string; url: string; created_at?: number }[] } };

function deferred<T = unknown>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function library(revision: number, characters: Asset[] = [], scenes: Asset[] = [], props: Asset[] = []) {
  return { revision, characters, scenes, props };
}

function project(id = 'p', characters: Asset[] = []): Project {
  return {
    id, revision: 3, title: 'Standalone Project', originalText: 'script', readEnvironment: currentEnvKey(),
    read_versions: { project: 3, series: null, library: 1 }, characters: characters as Project['characters'],
    scenes: [], props: [], frames: [], video_tasks: [], status: 'draft', createdAt: '', updatedAt: '',
  };
}


function rawProject(id = 'p', characters: Asset[] = []) {
  return {
    id, revision: 4, title: 'Standalone Project', original_text: 'script', series_id: null,
    read_versions: { project: 4, series: null, library: 1 }, characters, scenes: [], props: [], frames: [], video_tasks: [],
    status: 'draft', created_at: 1, updated_at: 2,
  };
}

const projectAsset = { id: 'asset-1', name: 'Hero', description: 'brave', image_asset: { selected_id: 'v1', variants: [{ id: 'v1', url: '/v1.png' }] } } satisfies Asset;
const promotedAsset: Asset = { id: 'global-1', name: 'Hero Global', description: 'brave', image_url: '/global.png' };
const otherProjectAsset: Asset = { id: 'asset-1', name: 'Villain', description: 'cunning', image_asset: { selected_id: 'b1', variants: [{ id: 'b1', url: '/b1.png' }] } };

function mockInitialLibrary(projects: Project[] = [project('p', [projectAsset])], global = library(1)) {
  vi.mocked(api.listSeries).mockResolvedValue([]);
  vi.mocked(api.getProjects).mockResolvedValue(projects);
  vi.mocked(api.listLibraryAssets).mockResolvedValue(global);
}

describe('AssetLibraryPage readback boundaries', () => {

  it.each(['completed', 'needs_confirmation'])('lets a new authoritative prop replace a readonly %s result', async status => {
    const asset = { ...projectAsset, description: 'brave', status: 'needs_confirmation' };
    vi.mocked(api.getProject).mockResolvedValue(rawProject('p', [{ ...asset, status }]));
    const drawer = (nextStatus: string) => <AssetInspector asset={{ ...asset, status: nextStatus }} type="characters" sourceName="Project" sourceId="p" sourceKind="project" starred={false} onClose={() => {}} onToggleStar={() => {}} />;
    const view = renderWithIntl(drawer('needs_confirmation'));
    fireEvent.click(screen.getByRole('button', { name: '重新读取资产' })); await act(async () => {});
    if (status === 'completed') {
      expect(screen.queryByText('资产生成结果待确认')).toBeNull();
      view.rerender(drawer('needs_confirmation'));
      expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
    } else {
      view.rerender(drawer('completed'));
      expect(screen.queryByText('资产生成结果待确认')).toBeNull();
    }
    expect(api.generateAsset).not.toHaveBeenCalled();
  });

  it('keeps a mounted inspector readonly after runtime deactivation', async () => {
    const asset = { ...projectAsset, description: 'brave', status: 'needs_confirmation' };
    renderWithIntl(<AssetInspector asset={asset} type="characters" sourceName="Project" sourceId="p" sourceKind="project" starred={false} onClose={() => {}} onToggleStar={() => {}} />);
    act(() => clearRuntimeEnvironment());
    fireEvent.click(screen.getByRole('button', { name: '重新读取资产' })); await act(async () => {});
    expect(api.getProject).not.toHaveBeenCalled(); expect(api.generateAsset).not.toHaveBeenCalled();
    expect(screen.getByText('资产状态读取失败，结果仍未知。')).toBeInTheDocument();
  });

  it('rejects an inspector recovery read after the same project changes series owner', async () => {
    const asset = { ...projectAsset, description: 'brave', source: 'series' as const, status: 'needs_confirmation' };
    const owned = (owner: string, revision: number) => ({ ...project('p', [asset]), revision, series_id: owner,
      read_versions: { project: revision, library: 1, series: { id: owner, revision: 1 } } });
    useProjectStore.setState({ currentProject: owned('series-a', 3), projects: [owned('series-a', 3)] });
    const pending = deferred<unknown>(); vi.mocked(api.getProject).mockReturnValue(pending.promise);
    renderWithIntl(<AssetInspector asset={asset} type="characters" sourceName="Project" sourceId="p" sourceKind="project" starred={false} onClose={() => {}} onToggleStar={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: '重新读取资产' }));
    act(() => useProjectStore.setState({ currentProject: owned('series-b', 4), projects: [owned('series-b', 4)] }));
    expect(screen.getByRole('button', { name: '重新读取资产' })).toBeEnabled();
    await act(async () => pending.resolve({ ...rawProject('p', [asset]), revision: 5, series_id: 'series-a',
      read_versions: { project: 5, library: 1, series: { id: 'series-a', revision: 2 } } }));
    expect(useProjectStore.getState().currentProject?.series_id).toBe('series-b');
    expect(toast.success).not.toHaveBeenCalled(); expect(api.generateAsset).not.toHaveBeenCalled();
  });
  beforeEach(() => {
    activateRuntimeEnvironment({ schema_version: 1, storage_id: TEST_STORAGE_ID });
    vi.useRealTimers();
    vi.clearAllMocks();
    clearLibraryRecoveries();
    useProjectStore.setState({ projects: [project('p', [projectAsset])], currentProject: null, readWatermarks: {}, runningOps: {} });
    mockInitialLibrary();
  });

  afterEach(() => {
    vi.useRealTimers();
    clearLibraryRecoveries();
    vi.restoreAllMocks();
  });

  it.each([
    ['inline JPEG', images.jpeg, images.jpeg],
    ['HTML data URL', 'data:text/html,<script>alert(1)</script>', ''],
    ['SVG data URL', 'data:image/svg+xml,<svg onload="alert(1)"></svg>', ''],
    ['attribute breakout', `${images.jpeg}" onerror="alert(1)`, ''],
  ])('keeps the new asset preview safe for %s entered in the URL field', (_kind, url, expected) => {
    const { container } = renderWithIntl(<NewLibraryAssetDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    const input = container.querySelector<HTMLInputElement>('#lib-asset-image');
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { value: url } });

    const preview = container.querySelector('img');
    expect(preview).toHaveAttribute('src', expected);
    expect(preview).not.toHaveAttribute('onerror');
    expect(container.querySelector('script, iframe')).toBeNull();
    expect(api.createLibraryAsset).not.toHaveBeenCalled();
  });



  it('rejects an initial library read from an earlier activation at the same API URL', async () => {
    const old = deferred<Project[]>();
    vi.mocked(api.getProjects).mockReturnValueOnce(old.promise).mockResolvedValueOnce([project('new-owner', [{ ...projectAsset, name: 'New owner asset' }])]);
    renderWithIntl(<AssetLibraryPage />);
    act(() => activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' }));
    expect(await screen.findByText('New owner asset')).toBeInTheDocument();
    await act(async () => old.resolve([project('p', [{ ...projectAsset, name: 'Stale old asset' }])]));
    expect(screen.queryByText('Stale old asset')).toBeNull();
    expect(screen.getByText('New owner asset')).toBeInTheDocument();
    expect(api.generateAsset).not.toHaveBeenCalled();
  });



  it('handles runtime deactivation without loading a replacement or leaking old assets', async () => {
    renderWithIntl(<AssetLibraryPage />);
    await screen.findByText('Hero');
    const reads = vi.mocked(api.getProjects).mock.calls.length;
    act(() => clearRuntimeEnvironment());
    await act(async () => {});
    expect(screen.queryByText('Hero')).toBeNull();
    expect(api.getProjects).toHaveBeenCalledTimes(reads);
  });

  it.each(['global', 'series'] as const)('reads the saved %s confirmation through the existing library source loader', async source => {
    const asset = { ...projectAsset, description: 'brave', name: 'Shared interrupted', status: 'needs_confirmation' };
    if (source === 'global') mockInitialLibrary([], library(1, [asset]));
    else { mockInitialLibrary([]); vi.mocked(api.listSeries).mockResolvedValue([{ id: 'series-a', title: 'Series A', characters: [asset], scenes: [], props: [] }]); }
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Shared interrupted'));
    expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新读取资产' }));
    await waitFor(() => expect(api.listLibraryAssets).toHaveBeenCalledTimes(2));
    expect(api.generateAsset).not.toHaveBeenCalled();
  });

  it('retains the captured task handle after the inspector closes before the generation acknowledgement', async () => {
    const accepted = deferred<unknown>();
    vi.mocked(api.generateAsset).mockReturnValueOnce(accepted.promise);
    vi.mocked(api.getTaskStatus).mockRejectedValue({ response: { status: 404 } });
    vi.mocked(api.getProject).mockResolvedValue(rawProject('p', [{ ...projectAsset, status: 'needs_confirmation' }]));
    const view = renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));
    view.unmount();
    await act(async () => accepted.resolve({ _task_id: 'accepted-after-close' }));
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '重新读取' }));
    await waitFor(() => expect(api.getTaskStatus).toHaveBeenCalledWith('accepted-after-close'));
    expect(api.generateAsset).toHaveBeenCalledOnce();
    expect(await screen.findByText('资产生成结果待确认')).toBeInTheDocument();
  });

  it('shows neutral confirmation for a saved project asset and preserves the original selected image', async () => {
    mockInitialLibrary([project('p', [{ ...projectAsset, status: 'needs_confirmation' }])]);
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
    expect(screen.getByText(/再次生成会发起新的请求/)).toBeInTheDocument();
    expect(api.generateAsset).not.toHaveBeenCalled();
  });

  it('reads a confirmation snapshot after task 404 without using old or unrelated images as completion', async () => {
    vi.mocked(api.generateAsset).mockResolvedValueOnce({ _task_id: 'lost' });
    vi.mocked(api.getTaskStatus).mockRejectedValue({ response: { status: 404 } });
    vi.mocked(api.getProject).mockResolvedValue(rawProject('p', [{ ...projectAsset, status: 'needs_confirmation' }]));
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));
    expect(await screen.findByText('资产生成结果待确认', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(api.getProject).toHaveBeenCalledOnce();
    expect(api.generateAsset).toHaveBeenCalledOnce();
    expect(toast.update).not.toHaveBeenCalledWith('toast-1', expect.objectContaining({ kind: 'success' }));
  });

  it('does not let an older initial library read remove a newly confirmed global asset', async () => {
    const initialSeries = deferred<unknown[]>();
    const initialProjects = deferred<Project[]>();
    const initialLibrary = deferred<unknown>();
    vi.mocked(api.listSeries).mockReturnValueOnce(initialSeries.promise).mockResolvedValueOnce([]);
    vi.mocked(api.getProjects).mockReturnValueOnce(initialProjects.promise).mockResolvedValueOnce([]);
    vi.mocked(api.listLibraryAssets).mockReturnValueOnce(initialLibrary.promise).mockResolvedValueOnce(library(2, [promotedAsset]));
    vi.mocked(api.createLibraryAsset).mockResolvedValueOnce(promotedAsset);

    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(screen.getByRole('button', { name: '新建资产' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Hero Global' } });
    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    expect(await screen.findByText('Hero Global')).toBeInTheDocument();

    await act(async () => {
      initialSeries.resolve([]);
      initialProjects.resolve([]);
      initialLibrary.resolve(library(1));
    });

    expect(screen.getByText('Hero Global')).toBeInTheDocument();
  });


  it('keeps create recovery across remount without submitting a second create', async () => {
    vi.mocked(api.createLibraryAsset).mockResolvedValueOnce(promotedAsset);
    vi.mocked(api.listSeries)
      .mockResolvedValueOnce([]).mockResolvedValueOnce([])
      .mockResolvedValueOnce([]).mockResolvedValueOnce([]);
    vi.mocked(api.getProjects)
      .mockResolvedValueOnce([project('p', [projectAsset])]).mockResolvedValueOnce([project('p', [projectAsset])])
      .mockResolvedValueOnce([project('p', [projectAsset])]).mockResolvedValueOnce([project('p', [projectAsset])]);
    vi.mocked(api.listLibraryAssets)
      .mockResolvedValueOnce(library(1)).mockResolvedValueOnce(library(1))
      .mockResolvedValueOnce(library(2, [promotedAsset]));

    const view = renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(screen.getByRole('button', { name: '新建资产' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Hero Global' } });
    fireEvent.click(screen.getByRole('button', { name: '创建' }));
    expect(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 })).toBeInTheDocument();

    view.unmount();
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(screen.getByRole('button', { name: '新建资产' }));
    fireEvent.click(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 }));

    expect(api.createLibraryAsset).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('Hero Global')).toBeInTheDocument();
  });

  it('prevents duplicate promote submissions across remount and exposes read-only recovery after the late write finishes', async () => {
    const promote = deferred<unknown>();
    vi.mocked(api.promoteAssetToLibrary).mockReturnValueOnce(promote.promise);
    vi.mocked(api.listSeries)
      .mockResolvedValueOnce([]).mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    vi.mocked(api.getProjects)
      .mockResolvedValueOnce([project('p', [projectAsset])]).mockResolvedValueOnce([project('p', [projectAsset])])
      .mockResolvedValueOnce([project('p', [projectAsset])]);
    vi.mocked(api.listLibraryAssets)
      .mockResolvedValueOnce(library(1)).mockResolvedValueOnce(library(1))
      .mockResolvedValueOnce(library(2, [promotedAsset]));

    const view = renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '提升到全局' }));
    expect(api.promoteAssetToLibrary).toHaveBeenCalledTimes(1);

    view.unmount();
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '提升到全局' }));
    expect(api.promoteAssetToLibrary).toHaveBeenCalledTimes(1);

    await act(async () => promote.resolve(promotedAsset));
    fireEvent.click(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 }));

    expect(api.promoteAssetToLibrary).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('已提升到全局资产库', expect.anything()));
    expect(await screen.findByText('Hero Global')).toBeInTheDocument();
  });

  it('keeps promote in a refresh-required state when the global asset readback fails and recovers after remount', async () => {
    vi.mocked(api.promoteAssetToLibrary).mockResolvedValueOnce(promotedAsset);
    vi.mocked(api.listSeries)
      .mockResolvedValueOnce([]).mockResolvedValueOnce([])
      .mockResolvedValueOnce([]).mockResolvedValueOnce([]);
    vi.mocked(api.getProjects)
      .mockResolvedValueOnce([project('p', [projectAsset])]).mockResolvedValueOnce([project('p', [projectAsset])])
      .mockResolvedValueOnce([project('p', [projectAsset])]).mockResolvedValueOnce([project('p', [projectAsset])]);
    vi.mocked(api.listLibraryAssets)
      .mockResolvedValueOnce(library(1)).mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(library(1)).mockResolvedValueOnce(library(2, [promotedAsset]));

    const view = renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '提升到全局' }));

    expect(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 })).toBeInTheDocument();
    expect(toast.success).not.toHaveBeenCalledWith('已提升到全局资产库', expect.anything());

    view.unmount();
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 }));

    expect(api.promoteAssetToLibrary).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('已提升到全局资产库', expect.anything()));
    expect(await screen.findByText('Hero Global')).toBeInTheDocument();
  });


  it('clears stale read-only recovery when switching to a different source with the same asset id', async () => {
    vi.mocked(api.generateAsset).mockResolvedValueOnce({ _task_id: 'task-1' });
    vi.mocked(api.getTaskStatus).mockResolvedValueOnce({ status: 'completed' });
    vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));
    mockInitialLibrary([project('p', [projectAsset]), project('q', [otherProjectAsset])], library(1));

    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));
    expect(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 })).toBeInTheDocument();

    fireEvent.click(await screen.findByText('Villain'));
    await waitFor(() => expect(screen.queryByRole('button', { name: '重新读取' })).not.toBeInTheDocument());
  });

  it('does not apply a variant readback after the inspector unmounts', async () => {
    const freshAsset: Asset = {
      ...projectAsset,
      reference_sheet: { selected_image_id: 'v2', image_variants: [{ id: 'v1', url: '/v1.png' }, { id: 'v2', url: '/v2.png' }] },
    };
    const projectRead = deferred<unknown>();
    vi.mocked(api.generateAsset).mockResolvedValueOnce({ _task_id: 'task-1' });
    vi.mocked(api.getTaskStatus).mockResolvedValueOnce({ status: 'completed' });
    vi.mocked(api.getProject).mockReturnValueOnce(projectRead.promise);

    const view = renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledWith('p'), { timeout: 3000 });

    view.unmount();
    await act(async () => projectRead.resolve(rawProject('p', [freshAsset])));

    expect(useProjectStore.getState().projects[0]?.characters?.[0]?.reference_sheet).toBeUndefined();
    expect(toast.update).not.toHaveBeenCalledWith('toast-1', expect.objectContaining({ kind: 'success' }));
  });

  it('keeps variant generation locked for read-only retry while the accepted task is still pending', async () => {
    vi.mocked(api.generateAsset).mockResolvedValueOnce({ _task_id: 'task-1' });
    vi.mocked(api.getTaskStatus)
      .mockResolvedValueOnce({ status: 'completed' })
      .mockResolvedValueOnce({ status: 'processing' })
      .mockResolvedValueOnce({ status: 'completed' });
    vi.mocked(api.getProject)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(rawProject('p', [{
        ...projectAsset,
        reference_sheet: { selected_image_id: 'v2', image_variants: [{ id: 'v1', url: '/v1.png' }, { id: 'v2', url: '/v2.png' }] },
      }]));

    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));
    const retry = await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 });
    await waitFor(() => expect(retry).not.toBeDisabled(), { timeout: 3000 });
    fireEvent.click(retry);

    expect(api.generateAsset).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '重新读取' })).not.toBeDisabled());

    fireEvent.click(screen.getByRole('button', { name: '重新读取' }));
    await waitFor(() => expect(toast.update).toHaveBeenCalledWith('toast-1', expect.objectContaining({ kind: 'success' })), { timeout: 3000 });
    expect(api.generateAsset).toHaveBeenCalledTimes(1);
  });

  it('keeps an unconfirmed variant submit locked for read-only recovery when the submit response is lost', async () => {
    vi.mocked(api.generateAsset).mockRejectedValueOnce(new Error('network lost'));

    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));

    expect(api.generateAsset).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }));
    expect(api.generateAsset).toHaveBeenCalledTimes(1);
    expect(Object.entries(useProjectStore.getState().runningOps).some(([key, value]) => key.includes('library-generate') && value)).toBe(true);
  });

  it('does not use an unrelated new variant as proof that a lost submit finished', async () => {
    vi.mocked(api.generateAsset).mockRejectedValueOnce(new Error('network lost'));
    vi.mocked(api.getProject).mockResolvedValue(rawProject('p', [{ ...projectAsset,
      reference_sheet: { selected_image_id: 'other', image_variants: [{ id: 'other', url: '/unrelated.png' }] },
    }]));
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));
    const retry = await screen.findByRole('button', { name: '重新读取' });
    await act(async () => fireEvent.click(retry));
    expect(api.generateAsset).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: '生成更多变体' })).not.toBeInTheDocument();
    expect(Object.keys(useProjectStore.getState().runningOps).some(key => key.includes('library-generate'))).toBe(true);
  });

  it('retains the variant task receipt after polling disconnects instead of unlocking paid resubmit', async () => {
    vi.mocked(api.generateAsset).mockResolvedValueOnce({ _task_id: 'task-1' });
    vi.mocked(api.getTaskStatus).mockRejectedValue(new Error('offline'));

    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));

    expect(api.generateAsset).toHaveBeenCalledTimes(1);
    expect(Object.entries(useProjectStore.getState().runningOps).some(([key, value]) => key.includes('library-generate') && value)).toBe(true);
    expect(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 })).toBeInTheDocument();
  });

  it('confirms generated variants from a fresh project read even when the project is not cached in the store', async () => {
    const freshAsset: Asset = {
      ...projectAsset,
      reference_sheet: { selected_image_id: 'v2', image_variants: [{ id: 'v1', url: '/v1.png' }, { id: 'v2', url: '/v2.png' }] },
    };
    useProjectStore.setState({ projects: [], currentProject: null, readWatermarks: {}, runningOps: {} });
    mockInitialLibrary([project('p', [projectAsset])], library(1));
    vi.mocked(api.generateAsset).mockResolvedValueOnce({ _task_id: 'task-1' });
    vi.mocked(api.getTaskStatus).mockResolvedValueOnce({ status: 'completed' });
    vi.mocked(api.getProject).mockResolvedValueOnce(rawProject('p', [freshAsset]));

    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));

    await waitFor(() => expect(toast.update).toHaveBeenCalledWith('toast-1', expect.objectContaining({ kind: 'success' })), { timeout: 3000 });
    expect(api.generateAsset).toHaveBeenCalledTimes(1);
    expect(useProjectStore.getState().projects).toEqual([]);
  });

  it('does not resubmit paid variant generation after task completion when project readback fails', async () => {
    vi.mocked(api.generateAsset).mockResolvedValueOnce({ _task_id: 'task-1' });
    vi.mocked(api.getTaskStatus).mockResolvedValueOnce({ status: 'completed' });
    vi.mocked(api.getProject).mockRejectedValueOnce(new Error('offline'));

    const view = renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '生成更多变体' }));
    expect(api.generateAsset).toHaveBeenCalledTimes(1);

    expect(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }));
    expect(api.generateAsset).toHaveBeenCalledTimes(1);

    view.unmount();
    renderWithIntl(<AssetLibraryPage />);
    fireEvent.click(await screen.findByText('Hero'));
    fireEvent.click(await screen.findByRole('button', { name: '重新读取' }, { timeout: 3000 }));
    expect(api.generateAsset).toHaveBeenCalledTimes(1);
  });
});
