import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, API_URL } from '@/lib/api';

vi.mock('axios', () => ({ default: { put: vi.fn(), get: vi.fn() } }));

describe('revisioned persistence API transport', () => {
    beforeEach(() => vi.clearAllMocks());

    it('sends the text baseline version and returns the acknowledged version', async () => {
        vi.mocked(axios.put).mockResolvedValue({ data: { id: 'p', original_text: 'New text', revision: 8 } });
        const saved = await api.updateScriptText('p', 'New text', 7);
        expect(axios.put).toHaveBeenCalledWith(`${API_URL}/projects/p/text`, { text: 'New text', expected_revision: 7 });
        expect(saved).toMatchObject({ originalText: 'New text', revision: 8 });
    });

    it('sends the confirmed extraction baseline without reading or replacing it', async () => {
        vi.mocked(axios.put).mockResolvedValue({ data: { id: 'p', original_text: 'Confirmed text', revision: 8 } });
        const saved = await api.reparseProject('p', 'Confirmed text', 7);
        expect(axios.put).toHaveBeenCalledWith(`${API_URL}/projects/p/reparse`, { text: 'Confirmed text', expected_revision: 7 });
        expect(saved).toMatchObject({ originalText: 'Confirmed text', revision: 8 });
        expect(axios.get).not.toHaveBeenCalled();
    });

    it.each([{ title: 'New title' }, { art_direction: null }])('sends only the series patch plus its baseline version: %j', async patch => {
        vi.mocked(axios.put).mockResolvedValue({ data: { id: 's', revision: 5 } });
        const saved = await api.updateSeries('s', { ...patch, expected_revision: 4 });
        expect(axios.put).toHaveBeenCalledWith(`${API_URL}/series/s`, { ...patch, expected_revision: 4 });
        expect(saved.revision).toBe(5);
    });

    it.each([409, 503])('propagates HTTP %s without fetching a new version or retrying', async status => {
        const error = { response: { status } };
        vi.mocked(axios.put).mockRejectedValue(error);
        await expect(api.updateScriptText('p', 'Draft', 7)).rejects.toBe(error);
        await expect(api.updateSeries('s', { title: 'Draft', expected_revision: 4 })).rejects.toBe(error);
        await expect(api.reparseProject('p', 'Draft', 7)).rejects.toBe(error);
        expect(axios.put).toHaveBeenCalledTimes(3);
        expect(axios.get).not.toHaveBeenCalled();
    });
});
