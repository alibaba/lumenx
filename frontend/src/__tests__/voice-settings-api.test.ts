import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';

afterEach(() => vi.unstubAllGlobals());

describe('existing voice settings transport contracts', () => {
    it.each(['bind', 'params'])('%s returns the complete enriched ACK without renaming source or dropping fields', async kind => {
        const data = { id: 'p', revision: 4, original_text: 'committed text', characters: [{ id: 'c', source: 'global', voice_volume: 0 }] };
        const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(data), { status: 200 }));
        vi.stubGlobal('fetch', fetchMock);
        const result = await (kind === 'bind' ? api.bindVoice('p', 'c', 'voice', 'Name') : api.updateVoiceParams('p', 'c', 0.5, 2, 0));
        expect(result).toEqual(data);
        expect(fetchMock).toHaveBeenCalledOnce();
        expect(fetchMock.mock.calls[0][0]).toMatch(new RegExp(`/projects/p/characters/c/${kind === 'bind' ? 'voice' : 'voice_params'}$`));
        expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: kind === 'bind' ? 'POST' : 'PUT',
            body: JSON.stringify(kind === 'bind' ? { voice_id: 'voice', voice_name: 'Name' } : { speed: 0.5, pitch: 2, volume: 0 }) });
    });

    it.each(['bind', 'params'])('%s preserves HTTP status/detail for visible persistence failure feedback', async kind => {
        const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'storage unavailable' }), { status: 503 }));
        vi.stubGlobal('fetch', fetchMock);
        await expect(kind === 'bind' ? api.bindVoice('p', 'c', 'v', 'Name') : api.updateVoiceParams('p', 'c', 1, 1, 50))
            .rejects.toMatchObject({ message: 'storage unavailable', response: { status: 503 } });
        expect(fetchMock).toHaveBeenCalledOnce();
    });

    it.each(['bind', 'params'])('%s keeps HTTP status when an upstream error body is not JSON', async kind => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>proxy down</html>', { status: 502 })));
        await expect(kind === 'bind' ? api.bindVoice('p', 'c', 'v', 'Name') : api.updateVoiceParams('p', 'c', 1, 1, 50))
            .rejects.toMatchObject({ response: { status: 502 } });
    });
});
