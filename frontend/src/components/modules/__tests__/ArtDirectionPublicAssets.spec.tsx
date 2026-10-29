import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import { getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import SeriesArtDirectionPanel from '../../series/SeriesArtDirectionPanel';
import ArtDirection from '../ArtDirection';
import shippedPresets from '../../../../../src/apps/comic_gen/style_presets.json';

// Both editors and the bundled-asset helper stay real. The preset API supplies
// the shipped backend catalog, so every actual thumbnail is covered.
vi.mock('@/lib/api', () => ({ API_URL: 'http://test.invalid', api: {
    getSeries: vi.fn(), getStylePresets: vi.fn(), updateSeries: vi.fn(),
    analyzeScriptForStyles: vi.fn(), saveArtDirection: vi.fn(),
} }));

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getStylePresets).mockResolvedValue(structuredClone(shippedPresets));
    vi.mocked(api.getSeries).mockResolvedValue({
        id: 's', revision: 4, title: 'Series', description: '', created_at: 1, updated_at: 1,
        characters: [], scenes: [], props: [], episode_ids: [],
    });
    useProjectStore.setState({ currentSeries: null, seriesList: [], readWatermarks: {}, runningOps: {},
        currentProject: {
            id: 'p', revision: 7, title: 'Project', originalText: 'Saved text',
            readEnvironment: getRuntimeEnvironment()!.key,
            characters: [], scenes: [], props: [], frames: [], status: 'draft', createdAt: '', updatedAt: '',
        },
    });
});

afterEach(() => vi.unstubAllEnvs());

describe.each(['project', 'series'] as const)('%s bundled style thumbnails', owner => {
    const show = () => renderWithIntl(owner === 'project'
        ? <ArtDirection /> : <SeriesArtDirectionPanel seriesId="s" onSaved={vi.fn()} />);

    it.each(['', '/static'])('resolves every shipped grid, detail and comparison image under base %s', async base => {
        vi.stubEnv('NEXT_PUBLIC_APP_BASE_PATH', base);
        show();
        await waitFor(() => expect(screen.getAllByRole('img')).toHaveLength(shippedPresets.presets.length));
        expect(shippedPresets.presets).toHaveLength(15);
        for (const preset of shippedPresets.presets) {
            expect(screen.getByRole('img', { name: preset.name_zh })).toHaveAttribute('src', `${base}${preset.thumbnail}`);
        }

        const first = shippedPresets.presets[0];
        fireEvent.click(screen.getByRole('img', { name: first.name_zh }));
        await screen.findByRole('button', { name: '使用此风格' });
        expect(screen.getAllByRole('img', { name: first.name_zh })).toHaveLength(2);
        const related = shippedPresets.presets.filter(p => p.category === first.category && p.id !== first.id).slice(0, 5);
        for (const preset of related) expect(screen.getAllByRole('img', { name: preset.name_zh })).toHaveLength(2);
        for (const image of screen.getAllByRole('img')) {
            const preset = shippedPresets.presets.find(p => p.name_zh === image.getAttribute('alt'))!;
            expect(image).toHaveAttribute('src', `${base}${preset.thumbnail}`);
        }
        expect(api.saveArtDirection).not.toHaveBeenCalled();
        expect(api.updateSeries).not.toHaveBeenCalled();
    });

    it.each([
        '/static/assets/styles/already-prefixed.png',
        'https://images.example.invalid/style.png',
        'data:image/png;base64,c3R5bGU=',
    ])('does not rewrite an already resolved or external source: %s', async thumbnail => {
        vi.stubEnv('NEXT_PUBLIC_APP_BASE_PATH', '/static');
        const preset = { ...shippedPresets.presets[0], thumbnail };
        vi.mocked(api.getStylePresets).mockResolvedValue({ ...shippedPresets, presets: [preset] });
        show();
        const gridImage = await screen.findByRole('img', { name: preset.name_zh });
        expect(gridImage).toHaveAttribute('src', thumbnail);
        fireEvent.click(gridImage);
        await screen.findByRole('button', { name: '使用此风格' });
        expect(screen.getAllByRole('img', { name: preset.name_zh })).toHaveLength(2);
        for (const image of screen.getAllByRole('img')) expect(image).toHaveAttribute('src', thumbnail);
    });
});
