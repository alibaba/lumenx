import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore } from '@/store/projectStore';
import { clearLibraryRecoveries } from '@/lib/libraryReadback';
import { renderWithIntl } from '../series/__tests__/test-utils';
import AssetLibraryPage from './AssetLibraryPage';
import AssetInspector from './AssetInspector';

const backend = vi.hoisted(() => ({ url: 'http://backend-a.invalid' }));
vi.mock('@/lib/apiUrl', () => ({ get API_URL() { return backend.url; } }));
vi.mock('@/lib/api', () => ({ get API_URL() { return backend.url; }, api: {
  listSeries: vi.fn(), getProjects: vi.fn(), listLibraryAssets: vi.fn(), generateAsset: vi.fn(),
} }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn(), isAppAccessApiUrl: vi.fn(() => true) }));

const STORAGE_A = '11111111-1111-4111-8111-111111111111';
const STORAGE_B = '22222222-2222-4222-8222-222222222222';
const types = ['characters', 'scenes', 'props'] as const;
const addresses = [
  ['legacy output prefix', 'output/uploads/retained-choice.png', 'http://backend-a.invalid/files/uploads/retained-choice.png'],
  ['legacy files prefix', '/files/uploads/retained-choice.png', 'http://backend-a.invalid/files/uploads/retained-choice.png'],
  ['legacy files without slash', 'files/uploads/retained-choice.png', 'http://backend-a.invalid/files/uploads/retained-choice.png'],
  ['managed relative', 'uploads/retained-choice.png', 'http://backend-a.invalid/files/uploads/retained-choice.png'],
  ['absolute backend file', 'http://backend-a.invalid/files/uploads/retained-choice.png', 'http://backend-a.invalid/files/uploads/retained-choice.png'],
  ['remote image', 'https://media.example.test/retained-choice.png?signature=retained', 'https://media.example.test/retained-choice.png?signature=retained'],
] as const;

function asset(type: typeof types[number], url: string) {
  const variants = [{ id: 'old', url: 'uploads/retained-alternate.png' }, { id: 'chosen', url }];
  return {
    id: 'retained', name: 'Retained asset', description: '', status: 'needs_confirmation',
    ...(type === 'characters'
      ? { reference_sheet: { selected_image_id: 'chosen', image_variants: variants } }
      : { image_asset: { selected_id: 'chosen', variants } }),
  };
}

function pool(type: typeof types[number], saved: ReturnType<typeof asset>) {
  return { revision: 1, characters: [], scenes: [], props: [], [type]: [saved] };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  backend.url = 'http://backend-a.invalid';
  activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
  window.history.replaceState({}, '', '/static/library');
  clearLibraryRecoveries();
  useProjectStore.setState({ projects: [], currentProject: null, readWatermarks: {}, runningOps: {} });
  vi.mocked(api.listSeries).mockResolvedValue([]);
  vi.mocked(api.getProjects).mockResolvedValue([]);
});

describe.each(types)('%s Library image addresses', type => {
  it.each(addresses)('renders %s cards, selected heroes and alternate variants through the backend without changing selection', async (_kind, raw, expected) => {
    const saved = asset(type, raw);
    const before = JSON.stringify(saved);
    vi.mocked(api.listLibraryAssets).mockResolvedValue(pool(type, saved));
    const view = renderWithIntl(<AssetLibraryPage />);
    expect(await screen.findByRole('img', { name: saved.name })).toHaveAttribute('src', expected);
    fireEvent.click(screen.getByText(saved.name));
    const inspector = screen.getByRole('complementary');
    expect(within(inspector).getByRole('img', { name: saved.name })).toHaveAttribute('src', expected);
    const thumbnails = Array.from(inspector.querySelectorAll('button img'));
    expect(thumbnails.map(image => image.getAttribute('src'))).toEqual([
      'http://backend-a.invalid/files/uploads/retained-alternate.png', expected,
    ]);
    expect(thumbnails[1].closest('button')).toHaveAttribute('aria-current', 'true');
    fireEvent.click(thumbnails[0].closest('button')!);
    expect(within(inspector).getByRole('img', { name: saved.name })).toHaveAttribute('src', 'http://backend-a.invalid/files/uploads/retained-alternate.png');
    expect(thumbnails[0].closest('button')).toHaveAttribute('aria-current', 'true');
    expect(JSON.stringify(saved)).toBe(before);
    expect(api.generateAsset).not.toHaveBeenCalled();
    // Decorative backdrops must resolve through the same backend as visible images.
    expect(Array.from(view.container.querySelectorAll('img')).every(image => /^https?:\/\//.test(image.getAttribute('src') ?? ''))).toBe(true);
  });

  it('resolves the legacy image fallback in the card and Inspector', async () => {
    const saved = { id: 'legacy', name: 'Legacy image', description: '', image_url: 'uploads/legacy.png' };
    vi.mocked(api.listLibraryAssets).mockResolvedValue({ revision: 1, characters: [], scenes: [], props: [], [type]: [saved] });
    renderWithIntl(<AssetLibraryPage />);
    expect(await screen.findByRole('img', { name: saved.name })).toHaveAttribute('src', 'http://backend-a.invalid/files/uploads/legacy.png');
    fireEvent.click(screen.getByText(saved.name));
    expect(within(screen.getByRole('complementary')).getByRole('img', { name: saved.name })).toHaveAttribute('src', 'http://backend-a.invalid/files/uploads/legacy.png');
  });
});

it('clears the old cards and Inspector on runtime change, then resolves the replacement images against the current backend', async () => {
  vi.mocked(api.listLibraryAssets).mockResolvedValueOnce(pool('characters', asset('characters', 'uploads/runtime-a.png')));
  renderWithIntl(<AssetLibraryPage />);
  fireEvent.click(await screen.findByText('Retained asset'));
  const replacement = deferred<ReturnType<typeof pool>>();
  vi.mocked(api.listLibraryAssets).mockReturnValueOnce(replacement.promise);
  act(() => {
    backend.url = 'http://backend-b.invalid';
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
  });
  expect(screen.queryByRole('complementary')).toBeNull();
  expect(screen.queryAllByRole('img')).toHaveLength(0);
  await act(async () => replacement.resolve(pool('characters', asset('characters', 'uploads/runtime-b.png'))));
  expect(await screen.findByRole('img', { name: 'Retained asset' })).toHaveAttribute('src', 'http://backend-b.invalid/files/uploads/runtime-b.png');
  fireEvent.click(screen.getByText('Retained asset'));
  expect(within(screen.getByRole('complementary')).getByRole('img', { name: 'Retained asset' })).toHaveAttribute('src', 'http://backend-b.invalid/files/uploads/runtime-b.png');
  expect(api.generateAsset).not.toHaveBeenCalled();
});

it.each([
  ...addresses,
  ['legacy output prefix', 'output/uploads/retained-choice.png', 'http://backend-a.invalid/files/uploads/retained-choice.png'],
  ['legacy files prefix', '/files/uploads/retained-choice.png', 'http://backend-a.invalid/files/uploads/retained-choice.png'],
] as const)('preserves the existing %s download address', async (_kind, raw, expected) => {
  vi.mocked(appAccessFetch).mockResolvedValue({ ok: false, status: 404 } as Response);
  renderWithIntl(<AssetInspector asset={asset('characters', raw)} type="characters" sourceName="Global" sourceId="global" sourceKind="global" starred={false} onClose={() => {}} onToggleStar={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: '下载' }));
  await waitFor(() => expect(appAccessFetch).toHaveBeenCalledWith(expected));
  expect(api.generateAsset).not.toHaveBeenCalled();
});
