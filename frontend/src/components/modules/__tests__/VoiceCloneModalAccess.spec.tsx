import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VoiceCloneModal from '../cast/VoiceCloneModal';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { API_URL, api } from '@/lib/api';

vi.mock('lucide-react', () => ({
    X: () => null,
    Upload: () => null,
    Loader2: () => null,
    Check: () => null,
    Users: () => null,
}));

beforeEach(() => {
    vi.restoreAllMocks();
});

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('VoiceCloneModal app access upload', () => {
    it('uploads clone samples through the protected app access fetch channel', async () => {
        const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ url: '/uploads/voice.wav' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        }));
        vi.stubGlobal('fetch', fetchMock);
        vi.spyOn(api, 'cloneVoice').mockResolvedValue({
            id: 'v1', label: 'Voice', source_audio_url: '/uploads/voice.wav', origin: 'clone',
            target_model: 'cosyvoice-v3.5-plus', family: 'cosyvoice', created_at: 1,
        });

        const onCreated = vi.fn();
        renderWithIntl(<VoiceCloneModal isOpen seriesId="s1" onClose={vi.fn()} onCreated={onCreated} />);

        const file = new File(['voice'], 'voice.wav', { type: 'audio/wav' });
        const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
        fireEvent.change(fileInput, { target: { files: [file] } });
        fireEvent.click(screen.getByRole('button', { name: '开始复刻' }));

        await waitFor(() => expect(api.cloneVoice).toHaveBeenCalledWith({
            series_id: 's1',
            audio_url: '/uploads/voice.wav',
            label: 'voice',
        }));
        expect(fetchMock).toHaveBeenCalledWith(`${API_URL}/upload`, expect.objectContaining({
            method: 'POST',
            credentials: 'include',
        }));
    });
});
