import { API_URL } from "@/lib/apiUrl";
import { appAccessFetch } from "@/lib/appAccess";
import { AtelierProjectApiError, isAtelierRecord } from "@/lib/atelierApi";
import { isCurrentRuntimeEnvironment, requireActiveRuntimeEnvironment, subscribeRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import type { AtelierAgentStreamErrorEvent } from "@/lib/atelierAgentStream";

const DEFAULT_MAX_EVENT_BYTES = 1024 * 1024;
const SIZE_ENCODER = new TextEncoder();
type AckEvent<Ack> = { type: string; ack: Ack | null };
export interface AtelierEventStreamOptions<Event> {
    signal?: AbortSignal;
    onEvent?: (event: Event | AtelierAgentStreamErrorEvent) => void;
    maxEventBytes?: number;
}
type DispatchResult<Ack> = { terminalAck: Ack | null };
type EventBuilder = { data: string[]; byteSize: number; eventType: string | null };
type SsePayload = { data: string; eventType: string | null };
const streamError = (message: string, code: string, status?: number): AtelierProjectApiError => new AtelierProjectApiError(message, code, status);

const parseErrorMessage = async (response: Response): Promise<string> => {
    try {
        const body = await response.clone().json();
        if (isAtelierRecord(body) && typeof body.detail === "string") return body.detail;
        if (isAtelierRecord(body) && typeof body.message === "string") return body.message;
    } catch {
        // Fall through to text parsing.
    }
    try {
        const text = await response.text();
        if (text.trim()) return text.trim();
    } catch {
        // Keep stable fallback below.
    }
    return "Atelier Agent stream request failed";
};

const abortError = (): AtelierProjectApiError => streamError("Atelier Agent stream was aborted", "atelier_agent_stream_aborted");

async function cancelReader(reader: ReadableStreamDefaultReader<Uint8Array> | null): Promise<void> {
    if (reader === null) return;
    try {
        await reader.cancel();
    } catch {
        // Reader cancellation is best-effort once the terminal ACK or an error path is known.
    }
}

const decodeChunk = (decoder: TextDecoder, chunk: Uint8Array, stream: boolean): string => {
    try {
        return decoder.decode(chunk, { stream });
    } catch {
        throw streamError("Invalid UTF-8 in Atelier Agent stream", "atelier_agent_stream_decode_failed");
    }
};

const parseSseBlock = (builder: EventBuilder): SsePayload | null => {
    if (builder.data.length === 0) return null;
    return { data: builder.data.join("\n"), eventType: builder.eventType };
};

const assertEventSize = (builder: EventBuilder, addedBytes: number, maxEventBytes: number): void => {
    if (builder.byteSize + addedBytes > maxEventBytes) {
        throw streamError("Atelier Agent stream event exceeded the maximum size", "atelier_agent_stream_event_too_large");
    }
    builder.byteSize += addedBytes;
};

const consumeLine = <Ack>(
    line: string,
    builder: EventBuilder,
    maxEventBytes: number,
    dispatch: (payload: SsePayload) => DispatchResult<Ack>,
): DispatchResult<Ack> => {
    if (line === "") {
        const payload = parseSseBlock(builder);
        builder.data = [];
        builder.byteSize = 0;
        builder.eventType = null;
        return payload === null ? { terminalAck: null } : dispatch(payload);
    }
    assertEventSize(builder, SIZE_ENCODER.encode(`${line}
`).byteLength, maxEventBytes);
    if (line.startsWith(":")) return { terminalAck: null };
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const rawValue = separator === -1 ? "" : line.slice(separator + 1);
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;
    if (field === "event") builder.eventType = value;
    if (field === "data") builder.data.push(value);
    return { terminalAck: null };
};

const drainLines = <Ack>(
    buffer: string,
    builder: EventBuilder,
    maxEventBytes: number,
    dispatch: (payload: SsePayload) => DispatchResult<Ack>,
): { buffer: string; terminalAck: Ack | null } => {
    let index = 0;
    while (index < buffer.length) {
        const nextLf = buffer.indexOf("\n", index);
        const nextCr = buffer.indexOf("\r", index);
        const candidates = [nextLf, nextCr].filter(item => item >= 0);
        if (candidates.length === 0) break;
        const lineEnd = Math.min(...candidates);
        const delimiterLength = buffer[lineEnd] === "\r" && buffer[lineEnd + 1] === "\n" ? 2 : 1;
        if (buffer[lineEnd] === "\r" && lineEnd === buffer.length - 1) break;
        const line = buffer.slice(index, lineEnd);
        const result = consumeLine(line, builder, maxEventBytes, dispatch);
        if (result.terminalAck !== null) return { buffer: buffer.slice(lineEnd + delimiterLength), terminalAck: result.terminalAck };
        index = lineEnd + delimiterLength;
    }
    return { buffer: buffer.slice(index), terminalAck: null };
};

const isAbortLike = (error: unknown): boolean => error instanceof DOMException && error.name === "AbortError";

async function readStream<Ack, Event extends AckEvent<Ack>>(
    response: Response,
    parseEvent: (raw: unknown) => Event | AtelierAgentStreamErrorEvent,
    options: AtelierEventStreamOptions<Event>,
    runtimeChanged: () => boolean,
    registerReaderCancel: (cancel: () => void) => void,
): Promise<Ack> {
    if (response.body === null) throw streamError("Atelier Agent stream response is not readable", "atelier_agent_stream_response_unavailable");
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8", { fatal: true });
    const maxEventBytes = options.maxEventBytes ?? DEFAULT_MAX_EVENT_BYTES;
    const builder: EventBuilder = { data: [], byteSize: 0, eventType: null };
    let buffer = "";
    const abortReader = () => { void cancelReader(reader); };
    registerReaderCancel(abortReader);
    const unsubscribeRuntime = subscribeRuntimeEnvironment(() => {
        if (runtimeChanged()) abortReader();
    });
    if (options.signal?.aborted) {
        await cancelReader(reader);
        throw abortError();
    }
    options.signal?.addEventListener("abort", abortReader, { once: true });
    const assertNotAborted = () => {
        if (options.signal?.aborted) throw abortError();
    };
    const assertCurrentEnvironment = () => {
        if (runtimeChanged()) {
            throw streamError("Runtime environment changed while Atelier Agent stream was in flight", "environment_changed");
        }
    };
    const dispatch = (payload: SsePayload): DispatchResult<Ack> => {
        assertNotAborted();
        let raw: unknown;
        try {
            raw = JSON.parse(payload.data);
        } catch {
            throw streamError("Invalid JSON in Atelier Agent stream event", "atelier_agent_stream_invalid_event");
        }
        if (isAtelierRecord(raw) && payload.eventType !== null && raw.type !== payload.eventType) {
            throw streamError("Atelier Agent stream event header does not match its payload", "atelier_agent_stream_event_mismatch");
        }
        const event = parseEvent(raw);
        assertNotAborted();
        assertCurrentEnvironment();
        options.onEvent?.(event);
        assertNotAborted();
        assertCurrentEnvironment();
        if ("detail" in event && "code" in event) {
            throw streamError(event.detail, event.code);
        }
        return { terminalAck: event.ack };
    };
    try {
        while (true) {
            assertNotAborted();
            assertCurrentEnvironment();
            const read = await reader.read();
            assertNotAborted();
            assertCurrentEnvironment();
            if (read.done) break;
            buffer += decodeChunk(decoder, read.value, true);
            assertEventSize({ data: [], byteSize: builder.byteSize, eventType: null }, SIZE_ENCODER.encode(buffer).byteLength, maxEventBytes);
            const drained = drainLines(buffer, builder, maxEventBytes, dispatch);
            buffer = drained.buffer;
            if (drained.terminalAck !== null) {
                await cancelReader(reader);
                return drained.terminalAck;
            }
        }
        const tail = decodeChunk(decoder, new Uint8Array(), false);
        if (tail) buffer += tail;
        if (buffer.endsWith("\r")) buffer += "\n";
        const drained = drainLines(buffer, builder, maxEventBytes, dispatch);
        buffer = drained.buffer;
        if (drained.terminalAck !== null) {
            await cancelReader(reader);
            return drained.terminalAck;
        }
        assertNotAborted();
        if (buffer.length > 0) assertEventSize(builder, SIZE_ENCODER.encode(buffer).byteLength, maxEventBytes);
        throw streamError("Atelier Agent stream ended before terminal ACK", "atelier_agent_stream_missing_terminal");
    } catch (error) {
        await cancelReader(reader);
        if (error instanceof AtelierProjectApiError) throw error;
        if (isAbortLike(error)) {
            if (runtimeChanged()) throw streamError("Runtime environment changed while Atelier Agent stream was in flight", "environment_changed");
            throw abortError();
        }
        throw error;
    } finally {
        options.signal?.removeEventListener("abort", abortReader);
        unsubscribeRuntime();
        registerReaderCancel(() => undefined);
    }
}

export async function postAtelierEventStream<Ack, Event extends AckEvent<Ack>>(
    path: string,
    body: string,
    parseEvent: (raw: unknown) => Event | AtelierAgentStreamErrorEvent,
    options: AtelierEventStreamOptions<Event> = {},
): Promise<Ack> {
    if (options.signal?.aborted) throw abortError();
    if (options.maxEventBytes !== undefined && (!Number.isSafeInteger(options.maxEventBytes) || options.maxEventBytes <= 0)) {
        throw streamError("Invalid Atelier Agent stream options", "invalid_atelier_agent_stream_options");
    }
    const environment = requireActiveRuntimeEnvironment();
    const controller = new AbortController();
    let cancelActiveReader: () => void = () => undefined;
    const runtimeChanged = () => !isCurrentRuntimeEnvironment(environment.key, environment.generation);
    const abort = () => controller.abort();
    const unsubscribeRuntime = subscribeRuntimeEnvironment(() => {
        if (runtimeChanged()) {
            controller.abort();
            cancelActiveReader();
        }
    });
    options.signal?.addEventListener("abort", abort, { once: true });
    try {
        const response = await appAccessFetch(`${API_URL}${path}`, {
            method: "POST",
            cache: "no-store",
            headers: {
                Accept: "text/event-stream",
                "Content-Type": "application/json",
            },
            body,
            signal: controller.signal,
        });
        if (runtimeChanged()) {
            controller.abort();
            throw streamError("Runtime environment changed while Atelier Agent stream was in flight", "environment_changed");
        }
        if (!response.ok) {
            const message = await parseErrorMessage(response);
            if (runtimeChanged()) {
                throw streamError("Runtime environment changed while Atelier Agent stream was in flight", "environment_changed");
            }
            throw streamError(message, "atelier_agent_stream_request_failed", response.status);
        }
        const contentType = response.headers.get("Content-Type") ?? "";
        if (contentType.split(";", 1)[0].trim().toLowerCase() !== "text/event-stream") {
            throw streamError("Atelier Agent stream response is not text/event-stream", "atelier_agent_stream_invalid_content_type", response.status);
        }
        return await readStream<Ack, Event>(response, parseEvent, options, runtimeChanged, cancel => { cancelActiveReader = cancel; });
    } catch (error) {
        if (error instanceof AtelierProjectApiError) throw error;
        if (isAbortLike(error)) {
            if (runtimeChanged()) throw streamError("Runtime environment changed while Atelier Agent stream was in flight", "environment_changed");
            throw abortError();
        }
        throw error;
    } finally {
        options.signal?.removeEventListener("abort", abort);
        unsubscribeRuntime();
    }
}
