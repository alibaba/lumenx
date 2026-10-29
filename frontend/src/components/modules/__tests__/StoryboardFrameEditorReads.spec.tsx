import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import StoryboardFrameEditor from '../StoryboardFrameEditor';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, frame, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
function read(library = 8, revision = 3, selected = 'v1', id = 'a') {
    return enriched({ ...ack([{ ...frame('f1'), rendered_image_url: `/${selected}.png`, rendered_image_asset: {
        selected_id: selected, variants: [{ id: 'v1', url: '/v1.png' }, { id: 'v2', url: '/v2.png' }],
    } }], id, revision), characters: [{ id: 'shared', name: 'Alice', source: 'global' }] }, library);
}
function page() { return renderWithIntl(<StoryboardFrameEditor frame={useProjectStore.getState().currentProject!.frames[0]} onClose={() => {}} />); }
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); vi.spyOn(window, 'alert').mockImplementation(() => {});
    useProjectStore.setState({ currentProject: project(), projects: [project()], currentSeries: null, readWatermarks: {} });
    useProjectStore.getState().applyProjectReadSnapshot('a', read());
});
describe('StoryboardFrameEditor confirmed project reads', () => {
    it('uses one enriched read after raw rendering and preserves shared provenance', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: ack(read(9, 4, 'v2').frames) });
        vi.mocked(axios.get).mockResolvedValue({ data: read(9, 4, 'v2') }); page();
        fireEvent.click(screen.getByRole('button', { name: /生成/ }));
        await waitFor(() => expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(9));
        expect(useProjectStore.getState().currentProject?.characters[0].source).toBe('global');
        expect(useProjectStore.getState().projects[0].frames[0].rendered_image_url).toBe('/v2.png');
        expect(axios.get).toHaveBeenCalledOnce(); expect(axios.post).toHaveBeenCalledOnce();
    });
    it('reports saved readback failure without another render or discarded shared assets', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: ack() });
        vi.mocked(axios.get).mockRejectedValue(new Error('offline')); page();
        fireEvent.click(screen.getByRole('button', { name: /生成/ }));
        await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/)));
        expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(8);
        expect(axios.post).toHaveBeenCalledOnce(); expect(axios.get).toHaveBeenCalledOnce();
    });
    it('does not fetch or claim saved for a malformed render acknowledgement', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: { id: 'a' } }); page();
        fireEvent.click(screen.getByRole('button', { name: /生成/ }));
        await waitFor(() => expect(window.alert).toHaveBeenCalled());
        expect(window.alert).not.toHaveBeenCalledWith(expect.stringMatching(/已保存/));
        expect(axios.get).not.toHaveBeenCalled();
    });
    it.each(['raw render', 'enriched selection'])('does not confirm %s with a GET older than its acknowledged project revision', async operation => {
        vi.mocked(axios.post).mockResolvedValue({ data: operation === 'raw render' ? ack() : read(7, 4, 'v2') });
        vi.mocked(axios.get).mockResolvedValue({ data: read(8, 3) }); page();
        if (operation === 'raw render') fireEvent.click(screen.getByRole('button', { name: /生成/ }));
        else fireEvent.click(screen.getAllByAltText('Variant')[1]);
        await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/)));
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
        expect(axios.get).toHaveBeenCalledOnce(); expect(axios.post).toHaveBeenCalledOnce();
    });
    it.each(['select', 'delete'])('rejects incomparable %s ACK and stops after one read', async operation => {
        vi.mocked(axios.post).mockResolvedValue({ data: read(7, 4, 'v2') });
        vi.mocked(axios.get).mockResolvedValue({ data: read(7, 4, 'v2') }); page();
        if (operation === 'select') fireEvent.click(screen.getAllByAltText('Variant')[1]);
        else fireEvent.click(screen.getAllByTitle(/删除/)[0]);
        await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/)));
        expect(useProjectStore.getState().currentProject?.frames[0].rendered_image_url).toBe('/v1.png');
        expect(useProjectStore.getState().projects[0].read_versions?.library).toBe(8);
        expect(axios.get).toHaveBeenCalledOnce(); expect(axios.post).toHaveBeenCalledOnce();
    });
    it('keeps a late raw render read on its original project after navigation', async () => {
        const pending = deferred<{ data: ReturnType<typeof read> }>();
        vi.mocked(axios.post).mockResolvedValue({ data: ack() }); vi.mocked(axios.get).mockReturnValue(pending.promise);
        page(); fireEvent.click(screen.getByRole('button', { name: /生成/ }));
        await waitFor(() => expect(axios.post).toHaveBeenCalled());
        await act(async () => {
            useProjectStore.setState({ currentProject: project('b'), projects: [...useProjectStore.getState().projects, project('b')] });
            pending.resolve({ data: read(9, 4, 'v2') });
        });
        expect(useProjectStore.getState().currentProject?.id).toBe('b');
        await waitFor(() => expect(useProjectStore.getState().projects.find(p => p.id === 'a')?.read_versions?.library).toBe(9));
    });
});
