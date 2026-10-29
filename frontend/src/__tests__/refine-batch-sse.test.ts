import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, API_URL } from '@/lib/api';

const encoder = new TextEncoder();
const complete = { total: 2, success: 1, failed: 1 };
function event(type: string, data: object) { return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`; }
function response(chunks: (string | Uint8Array)[], close = true) {
    const cancelled = vi.fn();
    const body = new ReadableStream<Uint8Array>({
        start(controller) { chunks.forEach(chunk => controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk)); if (close) controller.close(); },
        cancel: cancelled,
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, body }));
    return { body, cancelled };
}

afterEach(() => vi.unstubAllGlobals());
describe('real batch refine transport and SSE protocol', () => {
    it('retains event names and multiline data across arbitrary chunks, CRLF and split UTF-8', async () => {
        const bytes = encoder.encode('event: frame_refine_start\r\ndata: {"frame_id":"一号",\r\ndata: "frame_index":0,"total":2}\r\n\r\n' + event('batch_complete', complete));
        const { body } = response(Array.from(bytes, byte => new Uint8Array([byte])));
        const received = vi.fn();
        await expect(api.refineBatchFrames('project', received)).resolves.toEqual({ type: 'batch_complete', ...complete });
        expect(received.mock.calls.map(([value]) => value.type)).toEqual(['frame_refine_start', 'batch_complete']);
        expect(received.mock.calls[0][0]).toMatchObject({ frame_id: '一号', frame_index: 0, total: 2 });
        expect(fetch).toHaveBeenCalledOnce();
        expect(fetch).toHaveBeenCalledWith(`${API_URL}/projects/project/storyboard/refine_batch`, expect.objectContaining({ method: 'POST', credentials: 'include' }));
        expect(body.locked).toBe(false);
    });

    it.each(['\n', '\r\n', '\r'])('supports %j line endings and the optional field space', async newline => {
        response([`: heartbeat${newline}event:batch_complete${newline}data:${JSON.stringify(complete)}${newline}${newline}`]);
        await expect(api.refineBatchFrames('a', vi.fn())).resolves.toMatchObject(complete);
    });

    it.each(['storage_conflict', 'storage_unavailable', 'target_missing'])('rejects %s abort without retrying or accepting a later success', async code => {
        const received = vi.fn(); const stats = { total: 3, success: 1, failed: 1 };
        const { cancelled, body } = response([event('frame_refine_complete', { frame_id: 'f1', frame_index: 0, total: 3 }),
            event('batch_aborted', { code, error: '已中止', ...stats }), event('batch_complete', complete)], false);
        await expect(api.refineBatchFrames('a', received)).rejects.toMatchObject({ code, message: '已中止', ...stats });
        expect(received.mock.calls.map(([value]) => value.type)).toEqual(['frame_refine_complete', 'batch_aborted']);
        expect(fetch).toHaveBeenCalledOnce(); expect(cancelled).toHaveBeenCalledOnce(); expect(body.locked).toBe(false);
    });

    it('keeps an upstream frame error nonterminal and returns the final partial-success counts', async () => {
        response([event('frame_refine_error', { frame_id: 'f1', frame_index: 0, error: 'upstream failed' }),
            event('frame_refine_complete', { frame_id: 'f2', frame_index: 1, total: 2 }), event('batch_complete', complete)]);
        const received = vi.fn();
        await expect(api.refineBatchFrames('a', received)).resolves.toMatchObject(complete);
        expect(received).toHaveBeenCalledTimes(3);
    });

    it.each(['', event('frame_refine_start', { frame_id: 'f1', frame_index: 0, total: 1 }),
        'event: batch_complete\ndata: {"total":0,"success":0,"failed":0}',
        'event: batch_complete\ndata: {"total":\n\n',
        event('batch_complete', { total: -1, success: 0, failed: 0 }),
        event('batch_aborted', { code: 'unknown', error: 'bad', ...complete }),
    ])('rejects missing, truncated or invalid terminal data: %j', async body => {
        const state = response([body]);
        await expect(api.refineBatchFrames('a', vi.fn())).rejects.toBeInstanceOf(Error);
        expect(state.body.locked).toBe(false);
    });

    it('does not let payload type impersonate a successful terminal', async () => {
        response([event('frame_refine_error', { type: 'batch_complete', frame_id: 'f1', frame_index: 0, error: 'failed' })]);
        const received = vi.fn();
        await expect(api.refineBatchFrames('a', received)).rejects.toThrow();
        expect(received.mock.calls[0][0].type).toBe('frame_refine_error');
    });

    it('propagates the original callback error and cancels/releases the stream', async () => {
        const cause = new Error('callback failed');
        const { body, cancelled } = response([event('frame_refine_start', { frame_id: 'f1', frame_index: 0, total: 1 })], false);
        await expect(api.refineBatchFrames('a', () => { throw cause; })).rejects.toBe(cause);
        expect(cancelled).toHaveBeenCalledOnce(); expect(body.locked).toBe(false);
    });

    it('propagates a reader failure and releases its lock', async () => {
        const cause = new Error('connection lost');
        const body = new ReadableStream<Uint8Array>({ start(controller) { controller.error(cause); } });
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body }));
        await expect(api.refineBatchFrames('a', vi.fn())).rejects.toBe(cause);
        expect(body.locked).toBe(false);
    });

    it.each([404, 409, 503])('rejects HTTP %s without reading an SSE body or retrying', async status => {
        const getReader = vi.fn();
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status, body: { getReader }, json: async () => ({ detail: 'service refused' }) }));
        await expect(api.refineBatchFrames('a', vi.fn())).rejects.toMatchObject({ status });
        expect(getReader).not.toHaveBeenCalled(); expect(fetch).toHaveBeenCalledOnce();
    });

    it('rejects a successful response without a body', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, body: null }));
        await expect(api.refineBatchFrames('a', vi.fn())).rejects.toThrow();
    });
});

describe('refinement stream cleanup edge cases', () => {
    it('rejects an incomplete UTF-8 sequence at EOF and releases the reader', async () => {
        const { body } = response([encoder.encode('event: frame_refine_start\ndata: {"frame_id":"'), new Uint8Array([0xe4, 0xb8])]);
        await expect(api.refineBatchFrames('a', vi.fn())).rejects.toThrow();
        expect(body.locked).toBe(false);
    });

    it('does not swallow a callback exception on the terminal event', async () => {
        const cause = new Error('terminal consumer failed');
        const { body, cancelled } = response([event('batch_complete', complete)], false);
        await expect(api.refineBatchFrames('a', () => { throw cause; })).rejects.toBe(cause);
        expect(body.locked).toBe(false); expect(cancelled).toHaveBeenCalledOnce();
    });

    it('rejects invalid JSON before reaching a later terminal and cancels unread data', async () => {
        const { body, cancelled } = response(['event: frame_refine_start\ndata: broken\n\n', event('batch_complete', complete)], false);
        const callback = vi.fn();
        await expect(api.refineBatchFrames('a', callback)).rejects.toThrow();
        expect(callback).not.toHaveBeenCalled(); expect(body.locked).toBe(false); expect(cancelled).toHaveBeenCalledOnce();
    });
});

describe('terminal dispatch does not depend on connection close', () => {
    it('dispatches a CR-only terminal immediately while the stream is still open', async () => {
        const { body, cancelled } = response([`event:batch_complete\rdata:${JSON.stringify(complete)}\r\r`], false);
        await expect(api.refineBatchFrames('a', vi.fn())).resolves.toEqual({ type: 'batch_complete', ...complete });
        expect(cancelled).toHaveBeenCalledOnce(); expect(body.locked).toBe(false);
    }, 500);
});
