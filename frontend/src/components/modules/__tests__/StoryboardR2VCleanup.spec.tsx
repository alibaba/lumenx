import type { ComponentProps, ReactNode } from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/api';
import { activateProjectEnvironment, deactivateProjectEnvironment, useProjectStore, type Project } from '@/store/projectStore';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardR2V from '../StoryboardR2V';
import type ShotCard from '../storyboard-r2v/ShotCard';
import type T2ISubsection from '../storyboard-r2v/shot-panel/T2ISubsection';

import { ack, deferred, enriched, frame, project } from './frame-command-fixtures';

type MockShotCardProps = ComponentProps<typeof ShotCard> & { children?: ReactNode };

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
vi.mock('../storyboard-r2v/ShotCard', () => ({
    T2I_HISTORY_LIMIT: 10,
    default: (p: MockShotCardProps) => <section data-testid="shot" aria-label={p.shot.id}>
        <output data-testid="shot-data">{JSON.stringify(p.shot)}</output>
        <button onClick={p.onRefineFrame}>refine</button>
        <button onClick={() => p.onUpdateDialogue?.('dialogue draft')}>dialogue</button>
        <button disabled={p.structureDisabled} onClick={p.onDuplicate}>copy</button>
        {p.children}
    </section>,
}));
vi.mock('../storyboard-r2v/shot-panel/T2ISubsection', () => ({
    default: (p: ComponentProps<typeof T2ISubsection>) => <div>
        <output data-testid="t2i-images">{JSON.stringify(p.imageUrls)}</output>
        <button onClick={async () => {
            const error = await p.onUpload(new File(['image'], 'upload.png', { type: 'image/png' }));
            if (error && typeof error === 'object' && 'detail' in error) window.alert(String(error.detail));
        }}>upload image</button>
    </div>,
}));
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/TaskQueuePanel', () => ({ default: () => null }));

function renderPage(initial: Project = project()) {
    useProjectStore.setState({ projects: [initial], currentProject: initial, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
    return renderWithIntl(<StoryboardR2V />);
}
function shotIds() { return screen.queryAllByTestId('shot').map(node => node.getAttribute('aria-label')); }
function firstShot() { return screen.getAllByTestId('shot')[0]; }
function i2vProject(extraFrame: Record<string, unknown> = {}) {
    const currentFrame = { ...frame('f1'), workbench_tab_mode: 't2i_i2v', visual_description: 'prompt f1', ...extraFrame };
    return { ...project('a', 3), frames: [currentFrame] } as Project;
}

beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear();
    vi.spyOn(window, 'alert').mockImplementation(() => undefined);
});

afterEach(() => vi.unstubAllGlobals());

describe('StoryboardR2V cleanup boundaries', () => {
    it('does not render a synthetic shot for an empty committed project', () => {
        renderPage({ ...project(), frames: [] });
        expect(shotIds()).toEqual([]);
        expect(screen.getAllByRole('button', { name: /添加镜头|Add Shot|新增镜头/ }).length).toBeGreaterThan(0);
    });

    it('does not leave a synthetic shot when create-frame fails', async () => {
        vi.mocked(axios.post).mockRejectedValueOnce({ response: { status: 503 } });
        renderPage();
        fireEvent.click(screen.getAllByRole('button', { name: /添加镜头|Add Shot|新增镜头/ }).at(-1)!);
        expect(await screen.findByRole('alert')).toHaveTextContent(/保存|服务|未确认/);
        expect(shotIds()).toEqual(['f1', 'f2']);
        expect(shotIds().some(id => id?.startsWith('shot_'))).toBe(false);
    });

    it('adds only the committed frame id after create ACK on an empty project', async () => {
        const empty = { ...project(), frames: [] };
        vi.mocked(axios.post).mockResolvedValueOnce({ data: ack([frame('f1')], 'a', 4) });
        renderPage(empty);
        fireEvent.click(screen.getAllByRole('button', { name: /添加镜头|Add Shot|新增镜头/ }).at(-1)!);
        await waitFor(() => expect(shotIds()).toEqual(['f1']));
        expect(shotIds().some(id => id?.startsWith('shot_'))).toBe(false);
    });

    it('confirms single-frame refine through guarded readback and blocks retry after stale read', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...frame('f1'), visual_description: 'refined', assembled_prompt: 'refined', updated_at: 100 }) }));
        useProjectStore.getState().applyProjectReadSnapshot('a', enriched(ack([frame('f1'), frame('f2')], 'a', 3), 8), true);
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(ack([{ ...frame('f1'), visual_description: 'refined' }, frame('f2')], 'a', 4), 7) });
        renderWithIntl(<StoryboardR2V />);
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/已保存.*读取失败|保存|读取/);
        expect(screen.queryByText('refined')).toBeNull();
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(axios.get).toHaveBeenCalledTimes(1);
        vi.unstubAllGlobals();
    });

    it('accepts the real single-frame refine response and confirms its frame receipt', async () => {
        const receipt = { ...frame('f1'), visual_description: 'refined', assembled_prompt: 'refined', updated_at: 100 };
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => receipt }));
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack([receipt, frame('f2')])) });
        renderPage();
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
        await waitFor(() => expect(useProjectStore.getState().currentProject?.frames[0].visual_description).toBe('refined'));
        expect(within(firstShot()).getByTestId('shot-data')).toHaveTextContent('refined');
        expect(screen.queryByRole('alert')).toBeNull();
        expect(axios.get).toHaveBeenCalledTimes(1);
    });

    it('keeps a reconnected single-frame refine busy when the old request settles', async () => {
        const storageId = '11111111-1111-4111-8111-111111111111';
        const oldRequest = deferred<Response>();
        const newRequest = deferred<Response>();
        const oldReceipt = { ...frame('f1'), assembled_prompt: 'old refinement', updated_at: 100 };
        const newReceipt = { ...frame('f1'), assembled_prompt: 'new refinement', updated_at: 200 };
        const response = (receipt: typeof oldReceipt) => new Response(JSON.stringify(receipt), {
            status: 200, headers: { 'Content-Type': 'application/json' },
        });
        vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(oldRequest.promise).mockReturnValueOnce(newRequest.promise));
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(ack([newReceipt, frame('f2')], 'a', 8)) });
        const firstEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
        await activateProjectEnvironment(firstEnvironment.key, firstEnvironment.generation);
        renderPage();
        const operation = `frame-refine:${encodeURIComponent(firstEnvironment.key)}:a`;
        try {
            fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
            await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
            expect(fetch).toHaveBeenLastCalledWith(`${API_URL}/projects/a/frames/f1/refine`, expect.objectContaining({ method: 'POST' }));
            expect(useProjectStore.getState().runningOps[operation]).toBe(true);
            expect(within(firstShot()).getByRole('button', { name: 'copy' })).toBeDisabled();

            act(() => { clearRuntimeEnvironment(); deactivateProjectEnvironment(); });
            expect(screen.queryAllByTestId('shot')).toHaveLength(0);
            await act(async () => {
                const nextEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
                await activateProjectEnvironment(nextEnvironment.key, nextEnvironment.generation);
                const next = project('a', 7);
                useProjectStore.setState({ projects: [next], currentProject: next, readWatermarks: {} });
            });
            expect(getRuntimeEnvironment()?.key).toBe(firstEnvironment.key);
            expect(getRuntimeEnvironment()?.generation).not.toBe(firstEnvironment.generation);
            expect(useProjectStore.getState().runningOps).toEqual({});
            fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
            await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
            expect(useProjectStore.getState().runningOps[operation]).toBe(true);

            await act(async () => oldRequest.resolve(response(oldReceipt)));

            expect(useProjectStore.getState().runningOps[operation]).toBe(true);
            expect(within(firstShot()).getByRole('button', { name: 'copy' })).toBeDisabled();
            expect(useProjectStore.getState().currentProject?.revision).toBe(7);
            expect(useProjectStore.getState().currentProject?.frames[0].assembled_prompt).not.toBe('old refinement');
            expect(axios.get).not.toHaveBeenCalled();
            expect(screen.queryByRole('alert')).not.toBeInTheDocument();
            fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
            expect(fetch).toHaveBeenCalledTimes(2);
        } finally {
            await act(async () => {
                oldRequest.resolve(response(oldReceipt));
                newRequest.resolve(response(newReceipt));
            });
        }
        await waitFor(() => expect(useProjectStore.getState().runningOps[operation]).toBeUndefined());
        expect(useProjectStore.getState().currentProject?.revision).toBe(8);
        expect(useProjectStore.getState().currentProject?.frames[0].assembled_prompt).toBe('new refinement');
        expect(axios.get).toHaveBeenCalledExactlyOnceWith(`${API_URL}/projects/a`);
        expect(within(firstShot()).getByRole('button', { name: 'copy' })).toBeEnabled();
    });

    it('releases a single-frame refine after an ordinary remount in the same runtime generation', async () => {
        const oldRequest = deferred<Response>();
        const newRequest = deferred<Response>();
        const oldReceipt = { ...frame('f1'), assembled_prompt: 'old mount refinement', updated_at: 100 };
        const newReceipt = { ...frame('f1'), assembled_prompt: 'new mount refinement', updated_at: 200 };
        const response = (receipt: typeof oldReceipt) => new Response(JSON.stringify(receipt), {
            status: 200, headers: { 'Content-Type': 'application/json' },
        });
        vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(oldRequest.promise).mockReturnValueOnce(newRequest.promise));
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(ack([newReceipt, frame('f2')], 'a', 4)) });
        const view = renderPage();
        const environment = getRuntimeEnvironment()!;
        const operation = `frame-refine:${encodeURIComponent(environment.key)}:a`;
        try {
            fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
            await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
            view.unmount(); renderWithIntl(<StoryboardR2V />);
            expect(getRuntimeEnvironment()).toBe(environment);
            expect(useProjectStore.getState().runningOps[operation]).toBe(true);
            expect(within(firstShot()).getByRole('button', { name: 'copy' })).toBeDisabled();
            fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
            expect(fetch).toHaveBeenCalledTimes(1);

            await act(async () => oldRequest.resolve(response(oldReceipt)));

            expect(useProjectStore.getState().runningOps[operation]).toBeUndefined();
            expect(within(firstShot()).getByRole('button', { name: 'copy' })).toBeEnabled();
            expect(axios.get).not.toHaveBeenCalled();
            expect(screen.queryByRole('alert')).not.toBeInTheDocument();
            fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
            await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
            expect(useProjectStore.getState().runningOps[operation]).toBe(true);
        } finally {
            await act(async () => {
                oldRequest.resolve(response(oldReceipt));
                newRequest.resolve(response(newReceipt));
            });
        }
        await waitFor(() => expect(useProjectStore.getState().runningOps[operation]).toBeUndefined());
        expect(useProjectStore.getState().currentProject?.frames[0].assembled_prompt).toBe('new mount refinement');
        expect(axios.get).toHaveBeenCalledExactlyOnceWith(`${API_URL}/projects/a`);
        expect(within(firstShot()).getByRole('button', { name: 'copy' })).toBeEnabled();
    });

    it('rejects an enriched read that has not observed the refined frame receipt', async () => {
        const receipt = { ...frame('f1'), visual_description: 'refined', assembled_prompt: 'refined', updated_at: 100 };
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => receipt }));
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack([{ ...frame('f1'), updated_at: 99 }, frame('f2')])) });
        renderPage();
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(/读取/);
        expect(useProjectStore.getState().currentProject?.revision).toBe(3);
        expect(axios.get).toHaveBeenCalledTimes(1);
    });

    it('keeps a pending dialogue draft from being replaced by single-frame refinement', async () => {
        vi.stubGlobal('fetch', vi.fn());
        const pending = deferred();
        vi.mocked(axios.post).mockReturnValueOnce(pending.promise);
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack()) });
        renderPage();
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'dialogue' }));
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'refine' }));
        expect(fetch).not.toHaveBeenCalled();
        expect(screen.getByRole('alert')).toHaveTextContent(/保存|等待/);
        pending.resolve({ data: ack() });
        await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    });

    it('rejects add-shot writes for a foreign API environment', () => {
        renderPage({ ...project(), readEnvironment: 'https://other.example' });
        fireEvent.click(screen.getAllByRole('button', { name: /添加镜头|Add Shot|新增镜头/ }).at(-1)!);
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('uses the frame edit queue for inline dialogue and confirms with one project read', async () => {
        const confirmed = ack([{ ...frame('f1'), dialogue: 'dialogue draft' }, frame('f2')], 'a', 4);
        vi.mocked(axios.post).mockResolvedValueOnce({ data: confirmed });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(confirmed, 1) });
        renderPage();
        fireEvent.click(within(firstShot()).getByRole('button', { name: 'dialogue' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/update`, { frame_id: 'f1', expected_revision: 3, dialogue: 'dialogue draft' }));
        await waitFor(() => expect(axios.get).toHaveBeenCalledExactlyOnceWith(`${API_URL}/projects/a`));
        expect(useProjectStore.getState().currentProject?.frames[0].dialogue).toBe('dialogue draft');
    });

    it('refuses T2I upload for synthetic frame ids without lazy create or media upload', async () => {
        localStorage.setItem('storyboard-r2v-expanded-a', JSON.stringify(['shot_pending']));
        renderPage({ ...project(), frames: [{ ...frame('shot_pending'), workbench_tab_mode: 't2i_i2v' }] });
        fireEvent.click(screen.getByRole('button', { name: 'upload image' }));
        await waitFor(() => expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/保存|创建|落库|committed/i)));
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('does not display uploaded T2I image until the committed project read confirms it', async () => {
        const uploadAck = { ...frame('f1'), workbench_tab_mode: 't2i_i2v', t2i_image_urls: ['/upload.png'], t2i_selected_index: 0, updated_at: 100 };
        vi.mocked(axios.post).mockResolvedValueOnce({ data: uploadAck });
        vi.mocked(axios.get).mockRejectedValueOnce(new Error('offline'));
        localStorage.setItem('storyboard-r2v-expanded-a', JSON.stringify(['f1']));
        renderPage(i2vProject());
        fireEvent.click(screen.getByRole('button', { name: 'upload image' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith(`${API_URL}/projects/a/frames/f1/upload_t2i`, expect.any(FormData), expect.any(Object)));
        expect(await screen.findByRole('alert')).toHaveTextContent(/已保存.*读取失败|保存|读取/);
        expect(screen.getByTestId('t2i-images')).toHaveTextContent('[]');
    });
    it('confirms upload using persisted frame identity when signed history URLs rotate', async () => {
        const receipt = { ...frame('f1'), workbench_tab_mode: 't2i_i2v', t2i_image_urls: ['https://oss.example/old?signature=one', '/new.png'], t2i_selected_index: 1, updated_at: 100 };
        const confirmed = { ...receipt, t2i_image_urls: ['https://oss.example/old?signature=two', '/new.png'] };
        vi.mocked(axios.post).mockResolvedValueOnce({ data: receipt });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched(ack([confirmed])) });
        localStorage.setItem('storyboard-r2v-expanded-a', JSON.stringify(['f1']));
        renderPage(i2vProject());
        fireEvent.click(screen.getByRole('button', { name: 'upload image' }));
        await waitFor(() => expect(screen.getByTestId('t2i-images')).toHaveTextContent('/new.png'));
        expect(screen.queryByRole('alert')).toBeNull();
    });

});
