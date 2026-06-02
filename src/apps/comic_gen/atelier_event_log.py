"""Atelier agent structured event log (v1.4 Batch 4 / item 4j).

Append-only JSONL log of every conversation, API request, tool decision,
and tool result emitted by the agent loop. Designed to be cheap to produce
(one open + write + close per event, swallow IOError) so a full disk or
permission glitch can't kill an in-flight agent turn. Future eval/replay
tooling reads this file.

File path:
  - default: <repo_root>/logs/atelier-agent.jsonl
  - override: ATELIER_AGENT_LOG_PATH (absolute or repo-relative)

Disable entirely:
  ATELIER_AGENT_LOG_DISABLED=1   # short-circuit emit_event before any I/O

Event types (strings, not enums to keep the log human-readable):
  - atelier.conversation_starts
  - atelier.api_request
  - atelier.api_response
  - atelier.tool_decision
  - atelier.tool_result
  - atelier.conversation_ends
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Module-level lock — JSONL writes from concurrent agent turns will
# interleave whole lines, but never partial bytes within a line.
_LOG_LOCK = threading.Lock()


def _resolve_log_path() -> Optional[Path]:
    """Return the absolute path to write events to, or None if disabled.

    ATELIER_AGENT_LOG_DISABLED=1 → None (caller should short-circuit).
    ATELIER_AGENT_LOG_PATH=<path> → that path (resolved against repo root
        if relative).
    Otherwise → <repo_root>/logs/atelier-agent.jsonl.
    """
    if os.getenv("ATELIER_AGENT_LOG_DISABLED") == "1":
        return None
    override = os.getenv("ATELIER_AGENT_LOG_PATH")
    if override:
        p = Path(override)
        if not p.is_absolute():
            p = _repo_root() / p
        return p
    return _repo_root() / "logs" / "atelier-agent.jsonl"


def _repo_root() -> Path:
    """Walk up from this module to find the repo root (folder containing
    `src/`). Defensive — falls back to cwd if the marker is missing."""
    here = Path(__file__).resolve()
    for ancestor in (here, *here.parents):
        if (ancestor / "src").is_dir():
            return ancestor
    return Path.cwd()


def emit_event(event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Append one JSON line to the agent log.

    Best-effort: any IOError is logged and swallowed so a full disk doesn't
    crash an active agent turn. Callers should not catch — just let
    failures fall through.

    Each line shape:
        {"ts": <unix_seconds>, "type": "atelier.*", **payload}
    """
    target = _resolve_log_path()
    if target is None:
        return
    line: Dict[str, Any] = {"ts": time.time(), "type": event_type}
    if payload:
        # Drop None values so the log stays compact + filter-friendly.
        for k, v in payload.items():
            if v is None:
                continue
            line[k] = v
    serialized: str
    try:
        serialized = json.dumps(line, ensure_ascii=False, default=str)
    except Exception:  # pragma: no cover — defensive serializer guard
        logger.exception("atelier_event_log: failed to serialize event %s", event_type)
        return
    try:
        with _LOG_LOCK:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(serialized + "\n")
    except OSError:  # pragma: no cover — disk-full / permission errors
        logger.exception("atelier_event_log: failed to write to %s", target)
    except Exception:  # pragma: no cover — defensive catch-all
        logger.exception("atelier_event_log: unexpected error writing event")
