import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ResultCard from '../playground/ResultCard';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { API_URL } from '@/lib/api';
import type { PlaygroundGeneration } from '../playground/usePlaygroundStore';

vi.mock('lucide-react', () => ({
    Download: () => null,
    Video: () => null,
    Copy: () => null,
    Check: () => null,
    Replace: () => null,
    Crown: () => null,
    Bookmark: () => null,
}));

const generation: PlaygroundGeneration = {
    id: 'g1',
    mode: 't2i',
    model_id: 'model',
    prompt: 'prompt',
    input_media: [],
    parameters: {},
    batch_size: 1,
    outputs: [{ id: 'o1', media_path: 'output/assets/image.png', media_type: 'image', saved_to_library: false }],
    status: 'completed',
    created_at: '2026-10-10T00:00:00Z',
};

beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('Playground ResultCard protected download', () => {
    it('does not download or open a protected 401 response as media', async () => {
        const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'access_required' }), {
            status: 401,
            headers: { 'Content-Type': 'application/json' },
        }));
        vi.stubGlobal('fetch', fetchMock);
        const open = vi.spyOn(window, 'open').mockImplementation(() => null);
        const createObjectURL = vi.spyOn(URL, 'createObjectURL');

        renderWithIntl(<ResultCard generation={generation} outputIndex={0} />);
        fireEvent.click(screen.getByTitle('下载'));

        await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/files/assets/image.png`, expect.objectContaining({ credentials: 'include' })));
        expect(createObjectURL).not.toHaveBeenCalled();
        expect(open).not.toHaveBeenCalled();
    });
});
