export type RefineAbortCode = 'storage_conflict' | 'storage_unavailable' | 'target_missing';
interface Counts { total: number; success: number; failed: number }
export interface RefineBatchComplete extends Counts { type: 'batch_complete' }
export interface RefineBatchAborted extends Counts {
    type: 'batch_aborted'; code: RefineAbortCode; error: string; frame_id?: string; frame_index?: number;
}
export type RefineSSEEvent =
    | { type: 'frame_refine_start' | 'frame_refine_complete'; frame_id: string; frame_index: number; total: number; label?: string }
    | { type: 'frame_refine_error'; frame_id: string; frame_index: number; error: string }
    | RefineBatchComplete | RefineBatchAborted;

export class RefineBatchAbortedError extends Error {
    readonly code: RefineAbortCode;
    readonly total: number;
    readonly success: number;
    readonly failed: number;
    constructor(event: RefineBatchAborted) {
        super(event.error);
        this.name = 'RefineBatchAbortedError';
        this.code = event.code; this.total = event.total; this.success = event.success; this.failed = event.failed;
    }
}

const eventTypes = new Set(['frame_refine_start', 'frame_refine_complete', 'frame_refine_error', 'batch_complete', 'batch_aborted']);
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
function parseEvent(type: string, text: string): RefineSSEEvent {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid refinement event');
    const data = value as Record<string, unknown>;
    if (type === 'batch_complete' || type === 'batch_aborted') {
        if (!count(data.total) || !count(data.success) || !count(data.failed) || data.success + data.failed > data.total) {
            throw new Error('Invalid refinement counts');
        }
        const counts = { total: data.total, success: data.success, failed: data.failed };
        if (type === 'batch_complete') {
            if (data.success + data.failed !== data.total) throw new Error('Incomplete refinement counts');
            return { type, ...counts };
        }
        if (!['storage_conflict', 'storage_unavailable', 'target_missing'].includes(String(data.code)) || typeof data.error !== 'string'
            || (data.frame_id !== undefined && typeof data.frame_id !== 'string')
            || (data.frame_index !== undefined && !count(data.frame_index))) throw new Error('Invalid refinement abort');
        return { type, ...counts, code: data.code as RefineAbortCode, error: data.error,
            frame_id: data.frame_id as string | undefined, frame_index: data.frame_index as number | undefined };
    }
    if (typeof data.frame_id !== 'string' || !count(data.frame_index)) throw new Error('Invalid refinement frame');
    if (type === 'frame_refine_error') {
        if (typeof data.error !== 'string') throw new Error('Invalid refinement frame error');
        return { type, frame_id: data.frame_id, frame_index: data.frame_index, error: data.error };
    }
    if (!count(data.total) || data.frame_index >= data.total) throw new Error('Invalid refinement frame progress');
    return { type: type as 'frame_refine_start' | 'frame_refine_complete', frame_id: data.frame_id, frame_index: data.frame_index,
        total: data.total, ...(typeof data.label === 'string' ? { label: data.label } : {}) };
}

/** Consume complete SSE records, never infer success from an HTTP 200 or EOF. */
export async function consumeRefineStream(body: ReadableStream<Uint8Array>, onEvent: (event: RefineSSEEvent) => void): Promise<RefineBatchComplete> {
    const reader = body.getReader();
    const decoder = new TextDecoder('utf-8', { fatal: true });
    let buffer = '';
    let skipLF = false;
    let eventName = '';
    let dataLines: string[] = [];
    const line = (text: string): RefineBatchComplete | undefined => {
        if (text === '') {
            const name = eventName; const data = dataLines.join('\n');
            eventName = ''; dataLines = [];
            if (!eventTypes.has(name)) return;
            const parsed = parseEvent(name, data);
            // Consumer errors must escape, exactly like transport/protocol errors.
            onEvent(parsed);
            if (parsed.type === 'batch_aborted') throw new RefineBatchAbortedError(parsed);
            if (parsed.type === 'batch_complete') return parsed;
            return;
        }
        if (text.startsWith(':')) return;
        const colon = text.indexOf(':');
        const field = colon < 0 ? text : text.slice(0, colon);
        const raw = colon < 0 ? '' : text.slice(colon + 1);
        const value = raw.startsWith(' ') ? raw.slice(1) : raw;
        if (field === 'event') eventName = value;
        if (field === 'data') dataLines.push(value);
    };
    try {
        while (true) {
            const { done, value } = await reader.read();
            buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
            while (true) {
                if (skipLF && buffer.length) {
                    if (buffer.startsWith('\n')) buffer = buffer.slice(1);
                    skipLF = false;
                }
                const index = buffer.search(/[\r\n]/);
                if (index < 0) break;
                const text = buffer.slice(0, index);
                // CR is already a delimiter. Consume a following LF (even in
                // another chunk) without delaying an otherwise complete event.
                skipLF = buffer[index] === '\r';
                buffer = buffer.slice(index + 1);
                const terminal = line(text);
                if (terminal) return terminal;
            }
            // An unterminated record is truncated, even if its text looks like
            // a terminal event. Only a complete blank-line delimiter commits it.
            if (done) throw new Error('Refinement stream ended without a terminal event');
        }
    } finally {
        try { await reader.cancel(); } catch { /* Keep the original stream/consumer error. */ }
        reader.releaseLock();
    }
}
