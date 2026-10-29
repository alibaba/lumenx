import { Component, useState, type ComponentProps, type ReactNode } from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import axios, { AxiosError, type AxiosAdapter } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installAppAccessAxiosInterceptors } from '@/lib/appAccess';
import { activateRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { API_URL } from '@/lib/api';
import PolishPanel from '../storyboard-r2v/PolishPanel';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, frame, project } from './frame-command-fixtures';
import StoryboardR2V from '../StoryboardR2V';
import { useProjectStore, type Project } from '@/store/projectStore';

const bilingual = { prompt_cn: '镜头缓慢推近', prompt_en: 'A slow cinematic push towards the subject' };
const original = 'The character stands still';
const props: ComponentProps<typeof PolishPanel> = {
    prompt: original, scriptId: 'project-a', tabMode: 't2i_i2v', imageUrls: ['uploads/first.png'], onApply: () => undefined,
};
type Reply = { data: unknown; status?: number };
let replies: Array<Promise<Reply>>;
let requests: Array<{ url: string | undefined; body: Record<string, unknown> }>;
let uninstall: () => void;
let originalAdapter: typeof axios.defaults.adapter;

beforeEach(() => {
    requests = []; replies = []; originalAdapter = axios.defaults.adapter;
    const adapter: AxiosAdapter = async config => {
        requests.push({ url: config.url, body: config.data ? JSON.parse(config.data) : {} });
        const reply = replies.shift();
        if (!reply) throw new Error(`Unexpected HTTP request: ${config.url}`);
        const { data, status = 200 } = await reply;
        const response = { data, status, statusText: String(status), headers: {}, config };
        if (status >= 400) throw new AxiosError('Polish failed', undefined, config, undefined, response);
        return response;
    };
    axios.defaults.adapter = adapter; uninstall = installAppAccessAxiosInterceptors();
});
afterEach(() => { uninstall(); axios.defaults.adapter = originalAdapter; });
function respond(data: unknown = bilingual, status = 200) { replies.push(Promise.resolve({ data, status })); }
function start() { fireEvent.click(screen.getByRole('button', { name: /润色/ })); }
async function completeFirst() { start(); await screen.findByText(bilingual.prompt_en); }
function Editor() {
    const [prompt, setPrompt] = useState(original);
    return <><textarea aria-label="Original prompt" value={prompt} onChange={event => setPrompt(event.target.value)} />
        <PolishPanel {...props} prompt={prompt} onApply={setPrompt} /></>;
}

describe('actual PolishPanel with real API and Axios transport boundary', () => {
    for (const mode of ['t2i_i2v', 'direct_r2v'] as const) {
        it(`sends ${mode} first/feedback bodies and applies only the chosen language`, async () => {
            const apply = vi.fn(); respond(); respond({ prompt_cn: '调整后的中文', prompt_en: 'Updated composition' });
            renderWithIntl(<PolishPanel {...props} tabMode={mode} slots={[{ description: 'Hero' }]} onApply={apply} />);
            await completeFirst(); expect(apply).not.toHaveBeenCalled();
            expect(requests[0]).toEqual({
                url: `${API_URL}/video/${mode === 'direct_r2v' ? 'polish_r2v_prompt' : 'polish_prompt'}`,
                body: { draft_prompt: original, feedback: '', script_id: 'project-a', prev_cn: '', image_urls: ['uploads/first.png'], polish_model: '',
                    ...(mode === 'direct_r2v' ? { slots: [{ description: 'Hero' }] } : {}) },
            });
            fireEvent.change(screen.getByPlaceholderText(/想再调整/), { target: { value: '更近一些' } });
            fireEvent.click(screen.getByRole('button', { name: '再优化' })); await screen.findByText('Updated composition');
            expect(requests[1].body).toEqual({ ...requests[0].body, draft_prompt: bilingual.prompt_en, prev_cn: bilingual.prompt_cn, feedback: '更近一些' });
            fireEvent.click(screen.getAllByRole('button', { name: '应用' })[mode === 'direct_r2v' ? 1 : 0]);
            expect(apply).toHaveBeenCalledExactlyOnceWith(mode === 'direct_r2v' ? 'Updated composition' : '调整后的中文');
            expect(requests).toHaveLength(2);
        });
    }
    it('retains a candidate if the controlled editor does not accept Apply', async () => {
        const apply = vi.fn(); respond(); renderWithIntl(<PolishPanel {...props} onApply={apply} />); await completeFirst();
        fireEvent.click(screen.getAllByRole('button', { name: '应用' })[0]);
        expect(apply).toHaveBeenCalledWith(bilingual.prompt_cn);
        expect(screen.getByText(bilingual.prompt_en)).toBeInTheDocument();
    });
    it('clears an acknowledged candidate without automatically requesting another polish', async () => {
        respond(); renderWithIntl(<Editor />); await completeFirst();
        fireEvent.click(screen.getAllByRole('button', { name: '应用' })[1]);
        expect(screen.getByRole('textbox', { name: 'Original prompt' })).toHaveValue(bilingual.prompt_en);
        expect(screen.queryByRole('button', { name: '应用' })).not.toBeInTheDocument(); expect(requests).toHaveLength(1);
    });
    for (const settlement of ['success', 'error'] as const) {
        it(`discards a pending ${settlement} without resurrecting the panel`, async () => {
            const pending = deferred<Reply>(); replies.push(pending.promise);
            renderWithIntl(<PolishPanel {...props} />); start(); await waitFor(() => expect(requests).toHaveLength(1));
            fireEvent.click(screen.getByRole('button', { name: '丢弃' }));
            expect(screen.getByRole('button', { name: /润色/ })).toBeEnabled();
            await act(async () => { pending.resolve(settlement === 'success' ? { data: bilingual } : { status: 502, data: { detail: { reason: 'api_error' } } }); });
            expect(screen.getByRole('button', { name: /润色/ })).toBeEnabled();
            expect(screen.queryByText(bilingual.prompt_en)).not.toBeInTheDocument();
            expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
        });
    }
    for (const change of ['prompt', 'mode', 'reference', 'script'] as const) {
        it(`ignores a result after its ${change} changed`, async () => {
            const pending = deferred<Reply>(); replies.push(pending.promise);
            const view = renderWithIntl(<PolishPanel {...props} />); start(); await waitFor(() => expect(requests).toHaveLength(1));
            const next = { ...props, ...(change === 'prompt' ? { prompt: 'New authored draft' } : change === 'mode' ? { tabMode: 'direct_r2v' as const }
                : change === 'reference' ? { imageUrls: ['uploads/other.png'] } : { scriptId: 'project-b' }) };
            view.rerender(<PolishPanel {...next} />);
            await act(async () => { pending.resolve({ data: bilingual }); });
            expect(screen.queryByText(bilingual.prompt_en)).not.toBeInTheDocument();
            expect(screen.getByRole('button', { name: /润色/ })).toBeEnabled();
        });
    }
    it('keeps a current request when unchanged reference arrays are recreated', async () => {
        const pending = deferred<Reply>(); replies.push(pending.promise);
        const view = renderWithIntl(<PolishPanel {...props} slots={[{ description: 'Hero' }]} />); start();
        await waitFor(() => expect(requests).toHaveLength(1));
        view.rerender(<PolishPanel {...props} slots={[{ description: 'Hero' }]} imageUrls={[...props.imageUrls!]} />);
        await act(async () => { pending.resolve({ data: bilingual }); }); expect(screen.getByText(bilingual.prompt_en)).toBeInTheDocument();
    });
    it('releases a new activation and prevents A→B→A old failure from unlocking its pending request', async () => {
        const old = deferred<Reply>(); const current = deferred<Reply>(); replies.push(old.promise, current.promise);
        renderWithIntl(<PolishPanel {...props} />); start(); await waitFor(() => expect(requests).toHaveLength(1));
        act(() => {
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '22222222-2222-4222-8222-222222222222' });
            activateRuntimeEnvironment({ schema_version: 1, storage_id: '11111111-1111-4111-8111-111111111111' });
        });
        expect(screen.getByRole('button', { name: /润色/ })).toBeEnabled(); start(); await waitFor(() => expect(requests).toHaveLength(2));
        await act(async () => { old.resolve({ status: 502, data: { detail: { reason: 'api_error' } } }); });
        expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /润色/ })).not.toBeInTheDocument();
        await act(async () => { current.resolve({ data: bilingual }); }); expect(screen.getByText(bilingual.prompt_en)).toBeInTheDocument();
    });
    it('invalidates in-flight refinement after applying an earlier candidate', async () => {
        const pending = deferred<Reply>(); respond(); replies.push(pending.promise);
        renderWithIntl(<Editor />); await completeFirst();
        fireEvent.change(screen.getByPlaceholderText(/想再调整/), { target: { value: 'closer' } });
        fireEvent.click(screen.getByRole('button', { name: '再优化' })); await waitFor(() => expect(requests).toHaveLength(2));
        fireEvent.click(screen.getAllByRole('button', { name: '应用' })[0]);
        await act(async () => { pending.resolve({ data: { prompt_cn: '迟到的新稿', prompt_en: 'Late refinement' } }); });
        expect(screen.getByRole('textbox', { name: 'Original prompt' })).toHaveValue(bilingual.prompt_cn);
        expect(screen.queryByText('Late refinement')).not.toBeInTheDocument();
    });
    for (const [reason, message] of [
        ['is_configured_false', 'LLM 未配置'], ['api_error', '模型调用失败'],
        ['json_parse_error', '模型返回了无效响应'], ['missing_keys', '模型返回了不完整的双语结果'],
    ] as const) {
        it(`shows the real Axios 502 ${reason} without applying or resubmitting`, async () => {
            const apply = vi.fn(); respond({ detail: { reason, message_zh: 'server message', message_en: 'server message' } }, 502);
            renderWithIntl(<PolishPanel {...props} onApply={apply} />); start();
            expect(await screen.findByText(new RegExp(message))).toBeInTheDocument();
            expect(screen.getByRole('button', { name: '重试' })).toBeEnabled();
            expect(apply).not.toHaveBeenCalled(); expect(requests).toHaveLength(1);
        });
    }
    it('keeps 502 model_echo as a warning and forwards both languages for explicit feedback', async () => {
        respond({ detail: { reason: 'model_echo', ...bilingual } }, 502); respond({ prompt_cn: '更新', prompt_en: 'New iteration' });
        renderWithIntl(<PolishPanel {...props} />); start();
        expect(await screen.findByText(/模型未做明显修改/)).toBeInTheDocument();
        fireEvent.change(screen.getByPlaceholderText(/想再调整/), { target: { value: 'more detail' } });
        fireEvent.keyDown(screen.getByPlaceholderText(/想再调整/), { key: 'Enter' }); await screen.findByText('New iteration');
        expect(requests[1].body).toMatchObject({ draft_prompt: bilingual.prompt_en, prev_cn: bilingual.prompt_cn, feedback: 'more detail' });
    });
    it('preserves the previous candidate after an ordinary same-input refinement failure', async () => {
        respond(); respond({ detail: { reason: 'api_error' } }, 502); renderWithIntl(<PolishPanel {...props} />); await completeFirst();
        fireEvent.change(screen.getByPlaceholderText(/想再调整/), { target: { value: 'closer' } }); fireEvent.click(screen.getByRole('button', { name: '再优化' }));
        await screen.findByRole('button', { name: '重试' }); expect(screen.getByText(bilingual.prompt_en)).toBeInTheDocument();
        expect(screen.getByPlaceholderText(/想再调整/)).toHaveValue('closer');
    });
});

class RenderBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    render() { return this.state.failed ? <p>Polish render failed</p> : this.props.children; }
}
for (const status of [200, 502]) {
    it(`handles the actual processor's object CN at ${status} as incomplete bilingual output`, async () => {
        const data = { prompt_cn: { unexpected: 'object' }, prompt_en: original };
        respond(status === 502 ? { detail: { reason: 'model_echo', ...data } } : data, status);
        renderWithIntl(<RenderBoundary><PolishPanel {...props} /></RenderBoundary>); start();
        expect(await screen.findByText(/模型返回了不完整的双语结果/)).toBeInTheDocument();
        expect(screen.queryByText('Polish render failed')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: '应用' })).not.toBeInTheDocument();
    });
}

function renderMountedStoryboard() {
    const initial: Project = { ...project(), characters: [], frames: [{ ...frame('f1'), visual_description: original, workbench_tab_mode: 't2i_i2v' }] };
    localStorage.setItem('storyboard-r2v-expanded-a', JSON.stringify(['f1']));
    useProjectStore.setState({ projects: [initial], currentProject: initial, currentSeries: null, readWatermarks: {}, runningOps: {}, renderingFrames: new Set() });
    return renderWithIntl(<StoryboardR2V />);
}
it('uses the actual mounted Storyboard→ShotCard→PolishPanel apply and existing frame save path', async () => {
    respond(); respond(ack([{ ...frame('f1'), visual_description: bilingual.prompt_cn, workbench_tab_mode: 't2i_i2v' }], 'a', 4));
    renderMountedStoryboard(); await completeFirst();
    expect(screen.getByDisplayValue(original)).toBeInTheDocument(); expect(requests).toHaveLength(1);
    fireEvent.click(screen.getAllByRole('button', { name: '应用' })[0]);
    expect(screen.getByDisplayValue(bilingual.prompt_cn)).toBeInTheDocument();
    await waitFor(() => expect(requests).toHaveLength(2), { timeout: 2000 });
    expect(requests[1]).toEqual({ url: `${API_URL}/projects/a/frames/update`, body: { frame_id: 'f1', expected_revision: 3, visual_description: bilingual.prompt_cn } });
    await waitFor(() => expect(useProjectStore.getState().currentProject?.frames[0].visual_description).toBe(bilingual.prompt_cn));
});
it('retains the actual parent fieldset protection during a structural command', async () => {
    const copy = deferred<Reply>(); respond(); replies.push(copy.promise);
    renderMountedStoryboard(); await completeFirst();
    fireEvent.click(screen.getByRole('button', { name: '复制镜头' }));
    await waitFor(() => expect(requests).toHaveLength(2));
    for (const button of screen.getAllByRole('button', { name: '应用' })) expect(button).toBeDisabled();
    expect(screen.getByText(bilingual.prompt_en)).toBeInTheDocument();
    await act(async () => { copy.resolve({ data: ack([{ ...frame('f1'), visual_description: original, workbench_tab_mode: 't2i_i2v' }, frame('f2')]) }); });
    expect(screen.getAllByRole('button', { name: '应用' })[0]).toBeEnabled();
    expect(screen.getByText(bilingual.prompt_en)).toBeInTheDocument();
});

it('keeps empty input and whitespace feedback inert and prevents Enter while a request is pending', async () => {
    const view = renderWithIntl(<PolishPanel {...props} prompt="  " />);
    expect(screen.getByRole('button', { name: /润色/ })).toBeDisabled(); expect(requests).toHaveLength(0);
    respond(); view.rerender(<PolishPanel {...props} />); await completeFirst();
    const feedback = screen.getByPlaceholderText(/想再调整/);
    fireEvent.change(feedback, { target: { value: '   ' } });
    expect(screen.getByRole('button', { name: '再优化' })).toBeDisabled(); fireEvent.keyDown(feedback, { key: 'Enter' });
    fireEvent.change(feedback, { target: { value: 'closer' } }); fireEvent.keyDown(feedback, { key: 'Escape' });
    expect(requests).toHaveLength(1);
    const pending = deferred<Reply>(); replies.push(pending.promise);
    fireEvent.keyDown(feedback, { key: 'Enter' }); await waitFor(() => expect(requests).toHaveLength(2));
    expect(feedback).toBeDisabled(); fireEvent.keyDown(feedback, { key: 'Enter' }); expect(requests).toHaveLength(2);
    await act(async () => { pending.resolve({ data: bilingual }); });
});
it('preserves text-only and empty project API defaults through the real client', async () => {
    respond(); renderWithIntl(<PolishPanel prompt={original} scriptId="" tabMode="direct_r2v" onApply={() => undefined} />); await completeFirst();
    expect(requests[0].body).toEqual({ draft_prompt: original, script_id: '', slots: [], image_urls: [], feedback: '', prev_cn: '', polish_model: '' });
});
it('dismisses an explicitly applied candidate already equal to the controlled prompt', async () => {
    respond({ prompt_cn: original, prompt_en: bilingual.prompt_en }); renderWithIntl(<PolishPanel {...props} />); await completeFirst();
    fireEvent.click(screen.getAllByRole('button', { name: '应用' })[0]);
    expect(screen.queryByRole('button', { name: '应用' })).not.toBeInTheDocument(); expect(requests).toHaveLength(1);
});
it('does not let a discarded request settle or unlock a newer request for the same input', async () => {
    const old = deferred<Reply>(); const current = deferred<Reply>(); replies.push(old.promise, current.promise);
    renderWithIntl(<PolishPanel {...props} />); start(); await waitFor(() => expect(requests).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: '丢弃' })); start(); await waitFor(() => expect(requests).toHaveLength(2));
    await act(async () => { old.resolve({ data: { prompt_cn: '旧的中文', prompt_en: 'Discarded result' } }); });
    expect(screen.queryByText('Discarded result')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /润色/ })).not.toBeInTheDocument();
    await act(async () => { current.resolve({ data: bilingual }); }); expect(screen.getByText(bilingual.prompt_en)).toBeInTheDocument();
});
