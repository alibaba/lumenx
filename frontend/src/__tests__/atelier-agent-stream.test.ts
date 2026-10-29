// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API_URL } from "@/lib/apiUrl";
import { clearRuntimeEnvironment, activateRuntimeEnvironment } from "@/lib/runtimeEnvironment";
import { continueTurnStream, startTurnStream, type AtelierAgentStreamErrorEvent, type AtelierAgentStreamEvent } from "@/lib/atelierAgentStream";
import streamErrorWire from "./fixtures/atelier-agent-stream-error-wire.json";
import streamWire from "./fixtures/atelier-agent-stream-wire.json";

const STORAGE_A = "11111111-1111-4111-8111-111111111111";
const STORAGE_B = "22222222-2222-4222-8222-222222222222";
const AUTH_HASH = "c678edca72a86fa8beb0e62ce98ed59ae2f68d0ffc95a0457c665c7d11e5323d";

const encoder = new TextEncoder();

type WireFixture = {
    start_request: Record<string, unknown>;
    continue_request: Record<string, unknown>;
    start: string;
    continue: string;
    replay: string;
};

type ErrorWireFixture = {
    request: Record<string, unknown>;
    stream: string;
};

const errorWire = streamErrorWire as ErrorWireFixture;
const wire = streamWire as WireFixture;

const response = (body: ReadableStream<Uint8Array>, status = 200, contentType = "text/event-stream"): Response => new Response(body, {
    status,
    headers: { "Content-Type": contentType },
});

const jsonResponse = (value: unknown, status = 500): Response => new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
});

const streamFromChunks = (
    chunks: Array<string | Uint8Array>,
    cancel?: () => void,
    close = true,
): ReadableStream<Uint8Array> => new ReadableStream<Uint8Array>({
    start(controller) {
        for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
        if (close) controller.close();
    },
    cancel,
});

const sse = (value: unknown, eventType?: string, delimiter = "\n\n"): string => `${eventType ? `event: ${eventType}\n` : ""}data: ${JSON.stringify(value)}${delimiter}`;

const terminalAck = async (): Promise<Record<string, unknown>> => {
    const events: Array<AtelierAgentStreamEvent | AtelierAgentStreamErrorEvent> = [];
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([wire.start])));
    const result = await startTurnStream("p", wire.start_request as never, { onEvent: event => events.push(event) });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.turn.id).toBe(wire.start_request.client_turn_id);
    expect(events.at(-1)?.type).toBe("turn_terminal");
    return events.at(-1) as unknown as Record<string, unknown>;
};

beforeEach(() => {
    clearRuntimeEnvironment();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_A });
    vi.restoreAllMocks();
});

describe("Atelier Agent SSE stream client", () => {
    it("parses real backend start, continue, and replay stream fixtures", async () => {
        const events: Array<AtelierAgentStreamEvent | AtelierAgentStreamErrorEvent> = [];
        const fetchMock = vi.spyOn(globalThis, "fetch")
            .mockResolvedValueOnce(response(streamFromChunks([wire.start])))
            .mockResolvedValueOnce(response(streamFromChunks([wire.continue])))
            .mockResolvedValueOnce(response(streamFromChunks([wire.replay])));

        const started = await startTurnStream("p", wire.start_request as never, { onEvent: event => events.push(event) });
        expect(started.turn.id).toBe(wire.start_request.client_turn_id);
        expect(events.map(event => event.type)).toEqual([
            "turn_planning_started",
            "turn_plan_ready",
            "turn_preview_prepared",
            "turn_pending_saved",
            "turn_terminal",
        ]);
        expect(fetchMock.mock.calls[0][0]).toBe(`${API_URL}/atelier/projects/p/agent/turns/stream`);
        expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual(wire.start_request);

        events.length = 0;
        const continued = await continueTurnStream("p", wire.continue_request.turn_id as string, wire.continue_request as never, { onEvent: event => events.push(event) });
        expect(continued.generation?.receipt.client_request_id).toBe(wire.continue_request.client_request_id);
        expect(events.map(event => event.type)).toEqual([
            "generation_admitted",
            "generation_schedule_requested",
            "turn_terminal",
        ]);
        expect(fetchMock.mock.calls[1][0]).toBe(`${API_URL}/atelier/projects/p/agent/turns/${wire.continue_request.turn_id as string}/continue/stream`);
        expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual(wire.continue_request);

        events.length = 0;
        const replayed = await startTurnStream("p", wire.start_request as never, { onEvent: event => events.push(event) });
        expect(replayed.replayed).toBe(true);
        expect(events.map(event => event.type)).toEqual(["turn_terminal"]);
        expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    it("supports CRLF split lines, comments, half lines, and multi-data events", async () => {
        const terminal = await terminalAck();
        const eventJson = JSON.stringify({
            type: "turn_plan_ready",
            project_id: "p",
            turn_id: wire.start_request.client_turn_id,
            call_id: null,
            approval_id: null,
            attempt_id: null,
            candidate_id: null,
            message: null,
            ack: null,
        });
        const terminalJson = JSON.stringify(terminal);
        const events: Array<AtelierAgentStreamEvent | AtelierAgentStreamErrorEvent> = [];
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([
            ": ignored\r",
            "\n",
            "data: ",
            eventJson.slice(0, 18),
            eventJson.slice(18),
            "\r\n\r\n",
            "data: ",
            "{",
            "\n",
            "data: ",
            terminalJson.slice(1),
            "\n\n",
        ])));

        const result = await startTurnStream("p", wire.start_request as never, { onEvent: event => events.push(event) });

        expect(result.turn.id).toBe(wire.start_request.client_turn_id);
        expect(events.map(event => event.type)).toEqual(["turn_plan_ready", "turn_terminal"]);
    });

    it("fails closed for missing terminal and never retries or mints a new POST", async () => {
        const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([
            sse({
                type: "turn_plan_ready",
                project_id: "p",
                turn_id: wire.start_request.client_turn_id,
                call_id: null,
                approval_id: null,
                attempt_id: null,
                candidate_id: null,
                message: null,
                ack: null,
            }),
        ])));

        await expect(startTurnStream("p", wire.start_request as never)).rejects.toMatchObject({ code: "atelier_agent_stream_missing_terminal" });
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual(wire.start_request);
    });

    it("rejects unknown event types, identity mismatches, stream error events, mismatched event headers, and oversize events", async () => {
        vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(streamFromChunks([sse({
            type: "future_event",
            project_id: "p",
            turn_id: wire.start_request.client_turn_id,
            call_id: null,
            approval_id: null,
            attempt_id: null,
            candidate_id: null,
            message: null,
            ack: null,
        })])));
        await expect(startTurnStream("p", wire.start_request as never)).rejects.toMatchObject({ code: "atelier_agent_stream_unknown_event" });

        vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(streamFromChunks([sse({
            type: "turn_plan_ready",
            project_id: "other",
            turn_id: wire.start_request.client_turn_id,
            call_id: null,
            approval_id: null,
            attempt_id: null,
            candidate_id: null,
            message: null,
            ack: null,
        })])));
        await expect(startTurnStream("p", wire.start_request as never)).rejects.toMatchObject({ code: "atelier_agent_stream_identity_mismatch" });

        vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(streamFromChunks([errorWire.stream])));
        await expect(startTurnStream("p", errorWire.request as never)).rejects.toMatchObject({ code: "atelier_agent_start_rejected" });

        vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(streamFromChunks([sse({
            type: "turn_plan_ready",
            project_id: "p",
            turn_id: wire.start_request.client_turn_id,
            call_id: null,
            approval_id: null,
            attempt_id: null,
            candidate_id: null,
            message: null,
            ack: null,
        }, "turn_preview_prepared")])));
        await expect(startTurnStream("p", wire.start_request as never)).rejects.toMatchObject({ code: "atelier_agent_stream_event_mismatch" });

        vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(streamFromChunks([sse({
            type: "turn_plan_ready",
            project_id: "p",
            turn_id: wire.start_request.client_turn_id,
            call_id: null,
            approval_id: null,
            attempt_id: null,
            candidate_id: null,
            message: "x".repeat(128),
            ack: null,
        })])));
        await expect(startTurnStream("p", wire.start_request as never, { maxEventBytes: 80 })).rejects.toMatchObject({ code: "atelier_agent_stream_event_too_large" });

        vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(streamFromChunks(["data: ", "x".repeat(96)], undefined, false)));
        await expect(startTurnStream("p", wire.start_request as never, { maxEventBytes: 80 })).rejects.toMatchObject({ code: "atelier_agent_stream_event_too_large" });

        await expect(startTurnStream("p", wire.start_request as never, { maxEventBytes: 0 })).rejects.toMatchObject({ code: "invalid_atelier_agent_stream_options" });
    });

    it("uses fatal UTF-8 decoding and stable malformed response errors", async () => {
        vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response(streamFromChunks([new Uint8Array([0xc3, 0x28])])))
            .mockResolvedValueOnce(jsonResponse({ detail: "broken" }, 503))
            .mockResolvedValueOnce(response(streamFromChunks([wire.start]), 200, "application/json"));

        await expect(startTurnStream("p", wire.start_request as never)).rejects.toMatchObject({ code: "atelier_agent_stream_decode_failed" });
        await expect(startTurnStream("p", wire.start_request as never)).rejects.toMatchObject({ code: "atelier_agent_stream_request_failed", status: 503 });
        await expect(startTurnStream("p", wire.start_request as never)).rejects.toMatchObject({ code: "atelier_agent_stream_invalid_content_type" });
    });

    it("requires the exact SSE media type while allowing its charset parameter", async () => {
        vi.spyOn(globalThis, "fetch")
            .mockResolvedValueOnce(response(streamFromChunks([wire.start]), 200, "text/event-streaming"))
            .mockResolvedValueOnce(response(streamFromChunks([wire.start]), 200, "text/event-stream; charset=utf-8"));
        await expect(startTurnStream("p", wire.start_request as never)).rejects.toMatchObject({ code: "atelier_agent_stream_invalid_content_type" });
        expect((await startTurnStream("p", wire.start_request as never)).turn.id).toBe(wire.start_request.client_turn_id);
    });

    it("aborts and cancels readers on external abort or runtime environment changes", async () => {
        const externalAbort = new AbortController();
        externalAbort.abort();
        const fetchMock = vi.spyOn(globalThis, "fetch");
        await expect(startTurnStream("p", wire.start_request as never, { signal: externalAbort.signal })).rejects.toMatchObject({ code: "atelier_agent_stream_aborted" });
        expect(fetchMock).not.toHaveBeenCalled();

        let cancelled = false;
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([wire.start], () => { cancelled = true; }, false)));
        const events: Array<AtelierAgentStreamEvent | AtelierAgentStreamErrorEvent> = [];
        const promise = startTurnStream("p", wire.start_request as never, {
            onEvent: event => {
                events.push(event);
                if (event.type === "turn_planning_started") activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B });
            },
        });
        await expect(promise).rejects.toMatchObject({ code: "environment_changed" });
        expect(cancelled).toBe(true);
    });

    it("cancels stalled readers when the runtime environment changes", async () => {
        let cancelled = false;
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([sse({
            type: "turn_planning_started",
            project_id: "p",
            turn_id: wire.start_request.client_turn_id,
            call_id: null,
            approval_id: null,
            attempt_id: null,
            candidate_id: null,
            message: null,
            ack: null,
        }, "turn_planning_started")], () => { cancelled = true; }, false)));
        const promise = startTurnStream("p", wire.start_request as never, {
            onEvent: () => activateRuntimeEnvironment({ schema_version: 1, storage_id: STORAGE_B }),
        });

        await expect(promise).rejects.toMatchObject({ code: "environment_changed" });
        expect(cancelled).toBe(true);
    });

    it("cancels the reader after verified terminal ACK", async () => {
        let cancelled = false;
        const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([wire.start, sse({
            type: "turn_plan_ready",
            project_id: "p",
            turn_id: wire.start_request.client_turn_id,
            call_id: null,
            approval_id: null,
            attempt_id: null,
            candidate_id: null,
            message: null,
            ack: null,
        })], () => { cancelled = true; }, false)));

        const result = await startTurnStream("p", wire.start_request as never);

        expect(result.turn.id).toBe(wire.start_request.client_turn_id);
        expect(cancelled).toBe(true);
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("cancels the reader when the external signal aborts after streaming starts", async () => {
        let cancelled = false;
        const controller = new AbortController();
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([sse({
            type: "turn_plan_ready",
            project_id: "p",
            turn_id: wire.start_request.client_turn_id,
            call_id: null,
            approval_id: null,
            attempt_id: null,
            candidate_id: null,
            message: null,
            ack: null,
        })], () => { cancelled = true; }, false)));

        const promise = startTurnStream("p", wire.start_request as never, {
            signal: controller.signal,
            onEvent: () => controller.abort(),
        });

        await expect(promise).rejects.toMatchObject({ code: "atelier_agent_stream_aborted" });
        expect(cancelled).toBe(true);
    });

    it("rejects terminal success if the callback aborts during the terminal event", async () => {
        const controller = new AbortController();
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([wire.start])));

        await expect(startTurnStream("p", wire.start_request as never, {
            signal: controller.signal,
            onEvent: event => {
                if (event.type === "turn_terminal") controller.abort();
            },
        })).rejects.toMatchObject({ code: "atelier_agent_stream_aborted" });
    });

    it("accepts a final CR-only delimiter at EOF", async () => {
        const terminal = await terminalAck();
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([`event: turn_terminal\rdata: ${JSON.stringify(terminal)}\r\r`])));

        const result = await startTurnStream("p", wire.start_request as never);

        expect(result.turn.id).toBe(wire.start_request.client_turn_id);
    });

    it("rejects stream metadata mismatches before terminal ACK", async () => {
        vi.spyOn(globalThis, "fetch").mockResolvedValue(response(streamFromChunks([sse({
            type: "generation_admitted",
            project_id: "p",
            turn_id: wire.continue_request.turn_id,
            call_id: "wrong-call",
            approval_id: wire.continue_request.approval_id,
            attempt_id: null,
            candidate_id: null,
            message: null,
            ack: null,
        }, "generation_admitted")])));

        await expect(continueTurnStream("p", wire.continue_request.turn_id as string, wire.continue_request as never)).rejects.toMatchObject({ code: "atelier_agent_stream_identity_mismatch" });
    });

    it("rejects continue path/body mismatches before network I/O", async () => {
        const fetchMock = vi.spyOn(globalThis, "fetch");

        await expect(continueTurnStream("p", "turn-path", {
            ...(wire.continue_request as Record<string, unknown>),
            turn_id: "turn-body",
            authorization_hash: AUTH_HASH,
        } as never)).rejects.toMatchObject({ code: "atelier_agent_ack_mismatch" });
        expect(fetchMock).not.toHaveBeenCalled();
    });
});
