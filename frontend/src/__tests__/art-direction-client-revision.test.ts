import { afterEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { api, API_URL } from '@/lib/api';

describe('project art direction HTTP revision wire', () => {
    afterEach(() => vi.restoreAllMocks());

    it('sends the explicit save revision alongside the existing style fields', async () => {
        const post = vi.spyOn(axios, 'post').mockResolvedValue({ data: { revision: 8 } });
        const config = { id: 'style', positive_prompt: 'ink', negative_prompt: '' };
        expect(await api.saveArtDirection('p', 'style', config, [], [], 7)).toEqual({ revision: 8 });
        expect(post).toHaveBeenCalledExactlyOnceWith(`${API_URL}/projects/p/art_direction/save`, {
            selected_style_id: 'style', style_config: config, custom_styles: [], ai_recommendations: [], expected_revision: 7,
        });
    });

    it('sends the explicit clear revision in the POST body', async () => {
        const post = vi.spyOn(axios, 'post').mockResolvedValue({ data: { revision: 8, art_direction: null } });
        expect(await api.clearProjectArtDirection('p', 7)).toEqual({ revision: 8, art_direction: null });
        expect(post).toHaveBeenCalledExactlyOnceWith(`${API_URL}/projects/p/art_direction/clear`, { expected_revision: 7 });
    });
});
