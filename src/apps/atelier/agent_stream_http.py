"""HTTP streaming adapter; executor stages retain all execution authority."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from threading import Event

import anyio
from starlette.responses import StreamingResponse

from ...models.provider_recovery import ProviderRecoveryError
from ...utils.json_storage import StorageConflictError, StorageError
from .agent_executor import AgentStartRejected, AgentTurnNotFound
from .agent_loop import AgentLoopNotFound
from .storage import AtelierProjectNotFound

logger = logging.getLogger(__name__)


def _error_code(exc: Exception) -> str:
    if isinstance(exc, (AtelierProjectNotFound, AgentTurnNotFound, AgentLoopNotFound)):
        return "atelier_agent_not_found"
    if isinstance(exc, AgentStartRejected):
        return "atelier_agent_start_rejected"
    if isinstance(exc, StorageConflictError):
        return "atelier_agent_conflict"
    if isinstance(exc, StorageError):
        return "atelier_agent_storage_unavailable"
    if isinstance(exc, ProviderRecoveryError):
        return "atelier_agent_runtime_unavailable" if exc.reason in {
            "provider_recovery_runtime_missing", "provider_secret_unavailable",
        } else "atelier_agent_request_invalid"
    if isinstance(exc, ValueError):
        return "atelier_agent_request_invalid"
    return "atelier_agent_stream_failed"


class AgentEventStreamResponse(StreamingResponse):
    """Observe disconnect on every ASGI version and close the owned iterator.

    Waiting for a thread's current next() is shielded by Starlette/AnyIO. The
    signal prevents subsequent stages; it cannot undo an already-started write
    or provider callback. No background job is created by this response.
    """

    def __init__(self, events: Iterator, cancel_event: Event, *, project_id: str, turn_id: str):
        self._events = events
        self._cancel_event = cancel_event
        self._project_id = project_id
        self._turn_id = turn_id
        self._encoded = self._encode_events()
        super().__init__(self._encoded, media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
        })

    def _encode_events(self):
        try:
            while not self._cancel_event.is_set():
                event = next(self._events, None)
                if event is None or self._cancel_event.is_set():
                    break
                payload = event.model_dump(mode="json")
                yield _encode(payload)
        except Exception as exc:  # stream headers are already committed
            logger.warning("Agent stream stopped (%s)", type(exc).__name__)
            if not self._cancel_event.is_set():
                yield _encode({
                    "type": "agent_error", "project_id": self._project_id,
                    "turn_id": self._turn_id, "code": _error_code(exc),
                    "detail": ("Agent start request could not be prepared; no turn was saved"
                               if isinstance(exc, AgentStartRejected) else
                               "Agent stream stopped; read the saved turn before retrying"),
                })
        finally:
            close = getattr(self._events, "close", None)
            if close is not None:
                close()

    async def listen_for_disconnect(self, receive):
        try:
            await super().listen_for_disconnect(receive)
        finally:
            self._cancel_event.set()

    async def __call__(self, scope, receive, send):
        # Starlette's ASGI >= 2.4 branch omits the receive listener. Preserve a
        # proactive signal for every version, with send OSError as a fallback.
        try:
            async with anyio.create_task_group() as group:
                async def stream():
                    try:
                        await self.stream_response(send)
                    except OSError:
                        pass  # transport closed; never convert it to success
                    finally:
                        self._cancel_event.set()
                        group.cancel_scope.cancel()

                group.start_soon(stream)
                await self.listen_for_disconnect(receive)
                group.cancel_scope.cancel()
        finally:
            # The task group has joined its shielded next() before close().
            self._cancel_event.set()
            self._encoded.close()
            close = getattr(self._events, "close", None)
            if close is not None:
                close()


def _encode(payload: dict) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
