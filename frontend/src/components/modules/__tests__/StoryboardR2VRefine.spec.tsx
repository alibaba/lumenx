import type { ComponentProps } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { API_URL } from '@/lib/apiUrl';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, getRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { activateProjectEnvironment, deactivateProjectEnvironment, useProjectStore } from '@/store/projectStore';
import { useToastStore } from '@/store/toastStore';
import ToastContainer from '@/components/shared/ToastContainer';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import StoryboardR2V from '../StoryboardR2V';
import type ShotCard from '../storyboard-r2v/ShotCard';
import type StoryboardGenerateDialog from '../storyboard-r2v/StoryboardGenerateDialog';
import { ack, currentReadEnvironment, deferred, enriched, frame, project } from './frame-command-fixtures';

vi.mock('axios', () => ({ default: { post: vi.fn(), put: vi.fn(), delete: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
vi.mock('../storyboard-r2v/ShotCard', () => ({ T2I_HISTORY_LIMIT: 10,
    default: (p: ComponentProps<typeof ShotCard>) => <section aria-label={p.shot.id}>
        <output data-testid="tab-mode">{p.shot.tabMode}</output>
        <textarea aria-label="prompt" value={p.shot.prompt} onChange={e => p.onUpdatePrompt(e.target.value)} />
        <button disabled={p.structureDisabled} onClick={p.onDuplicate}>copy</button>
    </section>,
}));
vi.mock('../storyboard-r2v/StoryboardGenerateDialog', () => ({
    default: (p: ComponentProps<typeof StoryboardGenerateDialog>) => p.isOpen
        ? <button onClick={() => { p.onClose(); p.onConfirm(); }}>confirm generation</button> : null,
}));
vi.mock('../storyboard-r2v/AssetDrawer', () => ({ default: () => null }));
vi.mock('../storyboard-r2v/shot-panel/TaskQueuePanel', () => ({ default: () => null }));

const encoder = new TextEncoder();
const finished = enriched(ack([{ ...frame('f1'), visual_description: '已精修的第一帧' }, frame('f2')], 'a', 5));
const codes = [
    ['storage_conflict', '项目或共享输入已变化，请重新加载后重试'],
    ['storage_unavailable', '存储不可用，请重新启动后重试'],
    ['target_missing', '项目或分镜已删除，批量精修已停止'],
] as const;
function stream() {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, body }));
    return {
        event(type: string, data: object) { controller.enqueue(encoder.encode(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`)); },
        close() { controller.close(); },
    };
}
function start() {
    fireEvent.click(screen.getByRole('button', { name: /生成分镜/ }));
    fireEvent.click(screen.getByRole('button', { name: 'confirm generation' }));
}
function renderPage() { return renderWithIntl(<><StoryboardR2V /><ToastContainer /></>); }
const frameEditKey = (kind: 'inflight' | 'failed') => {
    const prefix = kind === 'inflight' ? 'frame-edit' : 'frame-edit-failed';
    return `${prefix}:${encodeURIComponent(currentReadEnvironment())}:a:1`;
};

beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); useToastStore.getState().clear();
    useProjectStore.setState({ projects: [project()], currentProject: project(), currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
    vi.mocked(axios.post).mockResolvedValue({ data: ack([frame('f1'), frame('f2')]) });
    vi.mocked(axios.get).mockResolvedValue({ data: finished });
});
afterEach(() => vi.unstubAllGlobals());

describe('batch refinement outcome in the real R2V page', () => {
    it('does not claim a confirmed read when the final GET is behind a shared-library watermark', async () => {
        useProjectStore.getState().applyProjectReadSnapshot('a', enriched(ack([frame('f1'), frame('f2')], 'a', 3), 8));
        vi.mocked(axios.get).mockResolvedValue({ data: enriched(finished, 7) });
        const wire = stream(); renderPage(); start();
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        await act(async () => { wire.event('batch_complete', { total: 2, success: 2, failed: 0 }); wire.close(); });
        expect(useToastStore.getState().toasts.some(t => t.kind === 'success')).toBe(false);
        expect(screen.getAllByText(/读取已提交精修结果失败/).length).toBeGreaterThan(0);
        expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(8);
        expect(axios.get).toHaveBeenCalledOnce(); expect(fetch).toHaveBeenCalledOnce();
    });

    it.each(codes)('shows %s abort, reads committed progress and releases the refining UI without retrying', async (code, error) => {
        const wire = stream(); renderPage();
        start();
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        await act(async () => {
            wire.event('frame_refine_complete', { frame_id: 'f1', frame_index: 0, total: 2 });
            wire.event('batch_aborted', { code, error, total: 2, success: 1, failed: 1 }); wire.close();
        });
        expect(await screen.findByText(new RegExp(error))).toBeInTheDocument();
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('已精修的第一帧');
        expect(useProjectStore.getState().currentProject?.revision).toBe(5);
        expect(useToastStore.getState().toasts.some(t => t.kind === 'success')).toBe(false);
        expect(screen.getByRole('button', { name: /生成分镜/ })).toBeEnabled();
        expect(screen.queryByText(/正在精修/)).not.toBeInTheDocument();
        expect(fetch).toHaveBeenCalledOnce(); expect(axios.post).toHaveBeenCalledOnce(); expect(axios.get).toHaveBeenCalledOnce();
    });

    it.each([0, 1])('uses the explicit terminal counts when failed=%s', async failed => {
        const wire = stream(); renderPage(); start();
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        await act(async () => { wire.event('batch_complete', { total: 2, success: 2 - failed, failed }); wire.close(); });
        const notice = useToastStore.getState().toasts.at(-1)!;
        expect(notice.kind).toBe(failed ? 'warning' : 'success');
        expect(notice.projectId).toBe('a');
        if (failed) expect(notice.title).toMatch(/1.*成功.*1.*失败/);
        expect(screen.getByRole('button', { name: /生成分镜/ })).toBeEnabled();
    });

    it('does not report success on EOF without terminal or discard the current frames when readback also fails', async () => {
        const wire = stream(); vi.mocked(axios.get).mockRejectedValueOnce(new Error('read failed'));
        renderPage(); start();
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        await act(async () => wire.close());
        expect(useToastStore.getState().toasts.some(t => t.kind === 'success')).toBe(false);
        expect(useToastStore.getState().toasts.at(-1)?.kind).toBe('error');
        expect(screen.getAllByRole('textbox', { name: 'prompt' })).toHaveLength(2);
        expect(screen.getByRole('button', { name: /生成分镜/ })).toBeEnabled();
        expect(fetch).toHaveBeenCalledOnce(); expect(axios.get).toHaveBeenCalledOnce();
    });

    it.each(['pending', 'inflight', 'failed'] as const)('refuses generation while an existing edit is %s', async kind => {
        const wire = stream(); wire.event('batch_complete', { total: 2, success: 2, failed: 0 }); wire.close();
        renderPage();
        if (kind === 'pending') fireEvent.change(screen.getAllByRole('textbox', { name: 'prompt' })[0], { target: { value: 'unsaved draft' } });
        else act(() => useProjectStore.getState().setRunningOp(frameEditKey(kind), true));
        start();
        await act(async () => {});
        expect(axios.post).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
        expect(screen.getByRole('alert')).toHaveTextContent(/待保存|正在保存|保存失败/);
        if (kind === 'pending') expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('unsaved draft');
    });

    it('keeps a shared refining gate across remount and prevents edits, structure and another generation', async () => {
        const wire = stream(); const view = renderPage(); start();
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        view.unmount(); renderPage();
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toBeDisabled();
        expect(screen.getAllByRole('button', { name: 'copy' })[0]).toBeDisabled();
        expect(screen.getByRole('button', { name: /正在生成|生成分镜/ })).toBeDisabled();
        await act(async () => { wire.event('batch_complete', { total: 2, success: 2, failed: 0 }); wire.close(); });
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('已精修的第一帧');
        expect(screen.getByRole('button', { name: /生成分镜/ })).toBeEnabled();
        expect(axios.post).toHaveBeenCalledOnce(); expect(fetch).toHaveBeenCalledOnce();
    });

    it.each(['switch', 'newer'])('keeps a late final GET from overwriting %s project state', async mode => {
        const read = deferred(); const wire = stream(); vi.mocked(axios.get).mockReturnValueOnce(read.promise);
        renderPage(); start();
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        await act(async () => { wire.event('batch_complete', { total: 2, success: 2, failed: 0 }); wire.close(); });
        await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
        act(() => {
            if (mode === 'switch') useProjectStore.setState({ currentProject: { ...project('b'), frames: [frame('B')] } });
            else useProjectStore.getState().applyProjectReadSnapshot('a', enriched(ack([{ ...frame('newer'), visual_description: '更新内容' }], 'a', 8)));
        });
        await act(async () => read.resolve({ data: finished }));
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue(mode === 'switch' ? 'prompt B' : '更新内容');
        expect(useProjectStore.getState().currentProject?.id).toBe(mode === 'switch' ? 'b' : 'a');
        expect(useToastStore.getState().toasts.at(-1)?.projectId).toBe('a');
    });
});

describe('refinement confirmation and cleanup', () => {
    it('does not turn a successful terminal followed by a failed GET into a success toast or another POST', async () => {
        const wire = stream(); vi.mocked(axios.get).mockRejectedValueOnce(new Error('unavailable'));
        renderPage(); start(); await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        await act(async () => { wire.event('batch_complete', { total: 2, success: 2, failed: 0 }); wire.close(); });
        expect(useToastStore.getState().toasts.some(t => t.kind === 'success')).toBe(false);
        expect(screen.getAllByText(/读取已提交精修结果失败/).length).toBeGreaterThan(0);
        expect(screen.getAllByRole('textbox', { name: 'prompt' })).toHaveLength(2);
        expect(screen.getByRole('button', { name: /生成分镜/ })).toBeEnabled();
        expect(fetch).toHaveBeenCalledOnce(); expect(axios.post).toHaveBeenCalledOnce();
    });

    it('takes the shared gate synchronously before two confirmation clicks', async () => {
        const wire = stream(); renderPage();
        fireEvent.click(screen.getByRole('button', { name: /生成分镜/ }));
        const confirm = screen.getByRole('button', { name: 'confirm generation' });
        act(() => { fireEvent.click(confirm); fireEvent.click(confirm); });
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        expect(axios.post).toHaveBeenCalledOnce();
        await act(async () => { wire.event('batch_complete', { total: 2, success: 2, failed: 0 }); wire.close(); });
    });

    it('releases the project gate after Phase 1 fails, preserving previously committed shots', async () => {
        vi.mocked(axios.post).mockRejectedValueOnce(new Error('coarse generation failed'));
        vi.stubGlobal('fetch', vi.fn()); renderPage(); start();
        await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1));
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('prompt f1');
        await waitFor(() => expect(screen.getByRole('button', { name: /生成分镜/ })).toBeEnabled());
        expect(fetch).not.toHaveBeenCalled(); expect(axios.get).not.toHaveBeenCalled();
    });
});

describe('project generation mode inheritance', () => {
    it.each(['i2v', 'r2v'] as const)('uses the committed %s project default for null frame modes during generation and remount', async mode => {
        const unset = { ...frame('f1'), workbench_tab_mode: null };
        const initial = { ...project(), default_generation_mode: mode, frames: [unset] };
        useProjectStore.setState({ projects: [initial], currentProject: initial });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: { ...ack([unset]), default_generation_mode: mode } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched({ ...ack([unset], 'a', 5), default_generation_mode: mode }) });
        const wire = stream(); const view = renderPage(); start();
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        const expected = mode === 'i2v' ? 't2i_i2v' : 'direct_r2v';
        expect(screen.getByTestId('tab-mode')).toHaveTextContent(expected);
        await act(async () => { wire.event('batch_complete', { total: 1, success: 1, failed: 0 }); wire.close(); });
        expect(screen.getByTestId('tab-mode')).toHaveTextContent(expected);
        view.unmount(); renderPage();
        expect(screen.getByTestId('tab-mode')).toHaveTextContent(expected);
    });

    it('uses a changed parent default from the committed GET while preserving explicit frame modes', async () => {
        const unset = { ...frame('f1'), workbench_tab_mode: null };
        vi.mocked(axios.post).mockResolvedValueOnce({ data: { ...ack([unset, frame('f2')]), series_id: 'old-series', default_generation_mode: 'r2v' } });
        vi.mocked(axios.get).mockResolvedValueOnce({ data: enriched({ ...ack([unset, frame('f2')], 'a', 5), series_id: 'new-series', default_generation_mode: 'i2v',
            characters: [{ id: 'new-shared', name: 'New parent asset', source: 'series' }] }) });
        const wire = stream(); renderPage(); start(); await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        await act(async () => { wire.event('batch_complete', { total: 2, success: 2, failed: 0 }); wire.close(); });
        expect(screen.getAllByTestId('tab-mode').map(node => node.textContent)).toEqual(['t2i_i2v', 'direct_r2v']);
        expect(useProjectStore.getState().currentProject?.characters.map(asset => asset.id)).toEqual(['new-shared']);
    });
});

describe('empty project refinement lifecycle', () => {
    it('replaces an empty-project remount with committed frames after Phase 1 and refinement finish', async () => {
        const empty = { ...project(), frames: [] };
        useProjectStore.setState({ projects: [empty], currentProject: empty });
        const coarse = deferred(); vi.mocked(axios.post).mockReturnValueOnce(coarse.promise);
        const wire = stream(); const view = renderPage(); start();
        expect(axios.post).toHaveBeenCalledOnce(); expect(fetch).not.toHaveBeenCalled();
        view.unmount(); renderPage();
        await act(async () => coarse.resolve({ data: ack([frame('f1'), frame('f2')]) }));
        await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
        await act(async () => { wire.event('batch_complete', { total: 2, success: 2, failed: 0 }); wire.close(); });
        expect(useProjectStore.getState().currentProject?.frames.map(frame => frame.id)).toEqual(['f1', 'f2']);
        expect(screen.getAllByRole('textbox', { name: 'prompt' })).toHaveLength(2);
        expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('已精修的第一帧');
        expect(screen.getByRole('button', { name: /生成分镜/ })).toBeEnabled();
    });
});


describe('batch refinement runtime ownership', () => {
    function heldStream() {
        let controller!: ReadableStreamDefaultController<Uint8Array>;
        let ended = false;
        const body = new ReadableStream<Uint8Array>({
            start(value) { controller = value; },
            cancel() { ended = true; },
        });
        const event = (type: string, data: object) => {
            if (!ended) controller.enqueue(encoder.encode(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`));
        };
        return {
            body,
            response: new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
            event,
            finish() {
                if (ended) return;
                event('batch_complete', { total: 2, success: 2, failed: 0 });
                controller.close(); ended = true;
            },
        };
    }

    it.each(['headers', 'complete', 'aborted'] as const)(
        'keeps the reconnected batch owned by its new runtime after old %s', async boundary => {
            const storageId = '11111111-1111-4111-8111-111111111111';
            const oldHeaders = deferred<Response>();
            const oldWire = heldStream(); const newWire = heldStream();
            const newScript = 'new runtime source';
            const newCoarse = { ...frame('f1'), visual_description: 'new runtime coarse frame' };
            const newFinal = { ...frame('f1'), visual_description: 'new runtime confirmed frame' };
            // Analyze finishes normally in each owning runtime; this mock does not test Axios interceptors.
            vi.mocked(axios.post).mockReset()
                .mockResolvedValueOnce({ data: ack([frame('f1'), frame('f2')], 'a', 4) })
                .mockResolvedValueOnce({ data: { ...ack([newCoarse, frame('f2')], 'a', 8), original_text: newScript } });
            // A continuation GET must not be hidden by a stale-revision rejection.
            vi.mocked(axios.get).mockReset().mockResolvedValue({ data: enriched({
                ...ack([{ ...frame('f1'), visual_description: 'unowned late read' }, frame('f2')], 'a', 12),
                original_text: 'unowned late script',
            }) });
            const transport = vi.fn<typeof fetch>().mockRejectedValue(new Error('Unexpected extra batch request'))
                .mockReturnValueOnce(boundary === 'headers' ? oldHeaders.promise : Promise.resolve(oldWire.response))
                .mockResolvedValueOnce(newWire.response);
            vi.stubGlobal('fetch', transport);
            const firstEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
            const operation = `frame-refine:${encodeURIComponent(firstEnvironment.key)}:a`;
            const errorPrefix = `frame-command-error:${encodeURIComponent(firstEnvironment.key)}:a:`;
            try {
                await activateProjectEnvironment(firstEnvironment.key, firstEnvironment.generation);
                const first = project();
                useProjectStore.setState({ projects: [first], currentProject: first, readWatermarks: {} });
                renderPage(); start();
                await waitFor(() => expect(transport).toHaveBeenCalledTimes(1));
                expect(axios.post).toHaveBeenCalledTimes(1);
                expect(useProjectStore.getState().currentProject?.revision).toBe(4);
                expect(useProjectStore.getState().runningOps[operation]).toBe(true);
                if (boundary !== 'headers') {
                    await act(async () => oldWire.event('frame_refine_start', { frame_id: 'f1', frame_index: 0, total: 2 }));
                    // The real parser must own the body before the runtime changes.
                    await waitFor(() => expect(oldWire.body.locked).toBe(true));
                }

                act(() => { clearRuntimeEnvironment(); deactivateProjectEnvironment(); });
                expect(screen.queryAllByRole('textbox', { name: 'prompt' })).toHaveLength(0);
                await act(async () => {
                    const nextEnvironment = activateRuntimeEnvironment({ schema_version: 1, storage_id: storageId });
                    await activateProjectEnvironment(nextEnvironment.key, nextEnvironment.generation);
                    const next = { ...project('a', 7), originalText: newScript };
                    useProjectStore.setState({ projects: [next], currentProject: next, readWatermarks: {} });
                });
                expect(getRuntimeEnvironment()?.key).toBe(firstEnvironment.key);
                expect(getRuntimeEnvironment()?.generation).not.toBe(firstEnvironment.generation);
                expect(useProjectStore.getState().runningOps).toEqual({});
                start();
                await waitFor(() => expect(transport).toHaveBeenCalledTimes(2));
                await act(async () => newWire.event('frame_refine_start', { frame_id: 'f1', frame_index: 0, total: 2 }));
                await waitFor(() => expect(newWire.body.locked).toBe(true));
                expect(useProjectStore.getState().currentProject?.revision).toBe(8);
                expect(useProjectStore.getState().runningOps[operation]).toBe(true);
                expect(useToastStore.getState().toasts).toEqual([]);

                await act(async () => {
                    if (boundary === 'headers') {
                        // appAccessFetch must reject this old response itself; the handler must not start a GET afterward.
                        oldHeaders.resolve(oldWire.response);
                        await oldHeaders.promise;
                    } else {
                        if (boundary === 'complete') oldWire.event('batch_complete', { total: 2, success: 2, failed: 0 });
                        else oldWire.event('batch_aborted', {
                            code: 'storage_conflict', error: 'old runtime abort', total: 2, success: 1, failed: 1,
                        });
                    }
                });
                // The accepted body was locked above; wait for the real parser's finally to release it.
                if (boundary !== 'headers') await waitFor(() => expect(oldWire.body.locked).toBe(false));

                expect(axios.get).not.toHaveBeenCalled();
                expect(useProjectStore.getState().currentProject?.revision).toBe(8);
                expect(useProjectStore.getState().projects.find(item => item.id === 'a')?.revision).toBe(8);
                expect(useProjectStore.getState().currentProject?.originalText).toBe(newScript);
                expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('new runtime coarse frame');
                expect(useProjectStore.getState().runningOps[operation]).toBe(true);
                expect(Object.keys(useProjectStore.getState().runningOps).filter(key => key.startsWith(errorPrefix))).toEqual([]);
                expect(screen.getAllByRole('button', { name: 'copy' })[0]).toBeDisabled();
                expect(screen.getByRole('button', { name: /正在生成|生成分镜/ })).toBeDisabled();
                expect(screen.queryByRole('alert')).not.toBeInTheDocument();
                expect(useToastStore.getState().toasts).toEqual([]);
                fireEvent.click(screen.getByRole('button', { name: /正在生成|生成分镜/ }));
                expect(screen.queryByRole('button', { name: 'confirm generation' })).not.toBeInTheDocument();
                expect(axios.post).toHaveBeenCalledTimes(2);
                expect(transport).toHaveBeenCalledTimes(2);
                for (const [url, init] of transport.mock.calls) {
                    expect(url).toBe(`${API_URL}/projects/a/storyboard/refine_batch`);
                    expect(init).toEqual(expect.objectContaining({ method: 'POST', credentials: 'include' }));
                    expect(new Headers(init?.headers).get('X-LumenX-Environment')).toBe(storageId);
                }
                for (const [url] of vi.mocked(axios.post).mock.calls) {
                    expect(url).toBe(`${API_URL}/projects/a/storyboard/analyze`);
                }
            } finally {
                // Release every created response/stream even when a boundary assertion fails.
                vi.mocked(axios.get).mockResolvedValue({ data: enriched({
                    ...ack([newFinal, frame('f2')], 'a', 9), original_text: newScript,
                }) });
                await act(async () => {
                    oldHeaders.resolve(oldWire.response);
                    oldWire.finish(); newWire.finish();
                });
            }
            await waitFor(() => expect(useProjectStore.getState().runningOps[operation]).toBeUndefined());
            expect(useProjectStore.getState().currentProject?.revision).toBe(9);
            expect(useProjectStore.getState().projects.find(item => item.id === 'a')?.revision).toBe(9);
            expect(screen.getAllByRole('textbox', { name: 'prompt' })[0]).toHaveValue('new runtime confirmed frame');
            expect(screen.getAllByRole('button', { name: 'copy' })[0]).toBeEnabled();
            expect(screen.getByRole('button', { name: /生成分镜/ })).toBeEnabled();
            expect(axios.get).toHaveBeenCalledExactlyOnceWith(`${API_URL}/projects/a`);
            expect(axios.post).toHaveBeenCalledTimes(2); expect(transport).toHaveBeenCalledTimes(2);
            expect(useToastStore.getState().toasts).toEqual([expect.objectContaining({ kind: 'success', projectId: 'a' })]);
            expect(oldWire.body.locked).toBe(false); expect(newWire.body.locked).toBe(false);
        },
    );
});
