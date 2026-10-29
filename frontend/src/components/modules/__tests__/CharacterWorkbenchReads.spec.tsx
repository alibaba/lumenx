import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import CharacterWorkbench from '../CharacterWorkbench';
import { useProjectStore } from '@/store/projectStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
function read(library = 8, selected = 'v1', revision = 3) {
    return enriched({ ...ack([], 'a', revision), characters: [{ id: 'shared', name: 'Alice', description: 'red coat', source: 'global',
        full_body_image_url: `/${selected}.png`, full_body_asset: { selected_id: selected,
            variants: [{ id: 'v1', url: '/v1.png' }, { id: 'v2', url: '/v2.png' }] } }] }, library);
}
function Page() {
    const current = useProjectStore(s => s.currentProject);
    return <CharacterWorkbench asset={current!.characters[0]} onClose={() => {}} onUpdateDescription={() => {}} onGenerate={() => {}} generatingTypes={[]} />;
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); vi.spyOn(window, 'alert').mockImplementation(() => {});
    useProjectStore.setState({ currentProject: project(), projects: [project()], currentSeries: null, readWatermarks: {} });
    useProjectStore.getState().applyProjectReadSnapshot('a', read());
});
describe('CharacterWorkbench source-confirmed variant commands', () => {

    it('rejects a pending recovery read after the same project changes series owner', async () => {
        const base = read(9);
        const owned = (owner: string, revision: number) => enriched({ ...base, revision, series_id: owner,
            characters: [{ ...base.characters[0], source: 'series', status: 'needs_confirmation' }] }, 9, 1);
        useProjectStore.getState().applyProjectReadSnapshot('a', owned('series-a', 4));
        const pending = deferred<{ data: ReturnType<typeof owned> }>();
        vi.mocked(axios.get).mockImplementation(url => String(url).endsWith('/presets') ? Promise.resolve({ data: [] }) : pending.promise);
        renderWithIntl(<Page />);
        fireEvent.click(screen.getByRole('button', { name: '重新读取资产' }));
        act(() => { useProjectStore.getState().applyProjectReadSnapshot('a', owned('series-b', 5)); });
        expect(screen.getByRole('button', { name: '重新读取资产' })).toBeEnabled();
        await act(async () => { pending.resolve({ data: owned('series-a', 6) }); });
        expect(useProjectStore.getState().currentProject?.series_id).toBe('series-b');
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('keeps the chosen legacy image visible when asset generation needs confirmation', () => {
        const snapshot = read(9); Object.assign(snapshot.characters[0], { status: 'needs_confirmation' });
        useProjectStore.getState().applyProjectReadSnapshot('a', snapshot);
        renderWithIntl(<Page />);
        expect(screen.getByText('资产生成结果待确认')).toBeInTheDocument();
        expect(screen.getAllByAltText('Selected Variant')[0]).toHaveAttribute('src', expect.stringContaining('/v1.png'));
        expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['select', 'delete', 'favorite'])('rejects an incomparable %s ACK without replacing current or cached shared assets', async operation => {
        vi.mocked(axios.post).mockResolvedValue({ data: read(7, 'v2', 4) });
        vi.mocked(axios.get).mockResolvedValue({ data: read(7, 'v2', 4) }); renderWithIntl(<Page />);
        if (operation === 'select') fireEvent.click(screen.getAllByAltText('Variant')[1]);
        else fireEvent.click(screen.getAllByTitle(operation === 'delete' ? /删除/ : /收藏/)[0]);
        await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/已保存.*读取失败/)));
        expect(useProjectStore.getState().currentProject?.characters[0].full_body_image_url).toBe('/v1.png');
        expect(useProjectStore.getState().projects[0].read_versions?.library).toBe(8);
        expect(axios.post).toHaveBeenCalledOnce(); expect(axios.get).toHaveBeenCalledOnce();
    });
    it('accepts a newer shared source with unchanged project revision', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: read(9, 'v2') }); renderWithIntl(<Page />);
        fireEvent.click(screen.getAllByAltText('Variant')[1]);
        await waitFor(() => expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(9));
        expect(screen.getAllByAltText('Selected Variant')[0]).toHaveAttribute('src', expect.stringContaining('/v2.png'));
        expect(axios.get).not.toHaveBeenCalled();
    });
    it('does not roll back a newer confirmed selection when an older ACK arrives late', async () => {
        const pending = deferred<{ data: ReturnType<typeof read> }>();
        vi.mocked(axios.post).mockReturnValue(pending.promise); renderWithIntl(<Page />);
        fireEvent.click(screen.getAllByAltText('Variant')[1]);
        await act(async () => {
            useProjectStore.getState().applyProjectReadSnapshot('a', read(10, 'v2'));
            pending.resolve({ data: read(9, 'v1') });
        });
        expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(10);
        expect(useProjectStore.getState().projects[0].characters[0].full_body_image_url).toBe('/v2.png');
        expect(axios.get).not.toHaveBeenCalled();
    });
    it('shows an ordinary failure for malformed ACK and never reads it back', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: { id: 'a' } }); renderWithIntl(<Page />);
        fireEvent.click(screen.getAllByAltText('Variant')[1]);
        await waitFor(() => expect(window.alert).toHaveBeenCalled());
        expect(window.alert).not.toHaveBeenCalledWith(expect.stringMatching(/已保存/));
        expect(axios.get).not.toHaveBeenCalled();
    });
});
