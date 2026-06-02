"""Atelier agent context compactor (v1.3 BATCH 2 — item 2b).

Token-budget-aware history compaction for the Atelier agent's
multi-turn memory. Sits between `build_prior_messages_from_history`
(serializer) and the LLM call: when the OpenAI-shaped messages list
exceeds ATELIER_AGENT_COMPACT_LIMIT (default 8000), the oldest turns
are folded into a single `{role:"system", content:"<compaction summary>: ..."}`
message and the rolling summary is persisted onto the project so
resume paths see it without re-summarizing.

Hooks (OpenClaw-style module-level register, no plugin loader):
    register_before_compaction(fn)
    register_after_compaction(fn)

Hooks fire only when compaction actually runs (token estimate > limit
AND there is enough history to fold). They are best-effort — exceptions
in a hook do not fail the compaction itself.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────────────────
# Soft cap on `system_prefix + sum(prior_messages content)` total tokens
# before compaction fires. The 4-chars-per-token heuristic over-estimates
# for English (~3.5) and under-estimates for CJK (~1.5–2). Either error
# direction is acceptable here because compaction is idempotent — the
# only real risk is under-firing, and the default 8k cap leaves a 4k
# safety margin under DashScope's 32k context window.
MAX_PROMPT_TOKENS: int = int(os.getenv("ATELIER_AGENT_COMPACT_LIMIT", "8000"))
# Target token budget for the produced summary itself (passed to the
# summarize LLM call as instruction; not strictly enforced).
SUMMARY_TOKEN_BUDGET: int = int(os.getenv("ATELIER_AGENT_COMPACT_SUMMARY_BUDGET", "500"))
# How many of the most recent messages to keep verbatim after the fold;
# anything older is absorbed into the summary.
KEEP_RECENT_TURNS: int = int(os.getenv("ATELIER_AGENT_COMPACT_KEEP", "3"))
# Default max prior turns to serialize before any compaction runs.
DEFAULT_PRIOR_TURNS: int = int(os.getenv("ATELIER_AGENT_PRIOR_TURNS", "3"))


# ── Hook register (OpenClaw pattern) ──────────────────────────────────────
_BEFORE_HOOKS: List[Callable[[List[Dict[str, Any]], Any], None]] = []
_AFTER_HOOKS: List[Callable[[str, Any], None]] = []


def register_before_compaction(fn: Callable[[List[Dict[str, Any]], Any], None]) -> None:
    """Register a hook fired with `(messages, project)` immediately
    before the summarize call. Hook may inspect/log; mutating
    `messages` is undefined behavior. Idempotent — duplicate
    registrations are de-duped by identity.
    """
    if fn not in _BEFORE_HOOKS:
        _BEFORE_HOOKS.append(fn)


def register_after_compaction(fn: Callable[[str, Any], None]) -> None:
    """Register a hook fired with `(summary, project)` after the
    compaction summary is persisted onto the project. Hook may
    inspect/log; mutating `project` is undefined behavior. Idempotent.
    """
    if fn not in _AFTER_HOOKS:
        _AFTER_HOOKS.append(fn)


def _clear_hooks_for_tests() -> None:
    """Test-only: drop all registered hooks. Not for production use."""
    _BEFORE_HOOKS.clear()
    _AFTER_HOOKS.clear()


def _fire_before(messages: List[Dict[str, Any]], project: Any) -> None:
    for fn in list(_BEFORE_HOOKS):
        try:
            fn(messages, project)
        except Exception:  # pragma: no cover — hook errors must not break compaction
            logger.exception("before_compaction hook raised")


def _fire_after(summary: str, project: Any) -> None:
    for fn in list(_AFTER_HOOKS):
        try:
            fn(summary, project)
        except Exception:  # pragma: no cover — hook errors must not break compaction
            logger.exception("after_compaction hook raised")


# ── Token estimator ───────────────────────────────────────────────────────
def estimate_tokens(messages: List[Dict[str, Any]], system_prompt: str = "") -> int:
    """Estimate prompt tokens for `system_prompt + flatten(messages)`.

    Tries tiktoken cl100k_base first (matches the GPT-4 family well and
    is a reasonable upper bound for DashScope's tokenizer). Falls back
    to `len(text)//4` so the module imports without tiktoken installed.
    """
    pieces: List[str] = []
    if system_prompt:
        pieces.append(system_prompt)
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            pieces.append(content)
        elif content is not None:
            try:
                pieces.append(json.dumps(content, ensure_ascii=False, default=str))
            except Exception:  # pragma: no cover — defensive
                pieces.append(str(content))
        # tool_calls payloads are also part of the wire — count them too.
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            try:
                pieces.append(json.dumps(tool_calls, ensure_ascii=False, default=str))
            except Exception:  # pragma: no cover — defensive
                pieces.append(str(tool_calls))
    blob = "\n".join(pieces)

    try:  # pragma: no cover — exercised when tiktoken is installed
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(blob))
    except Exception:
        # 4-chars/token heuristic. For English this over-estimates
        # (~3.5 chars/token); for CJK it under-estimates (~1.5–2). The
        # over-fire direction is acceptable; the under-fire risk is
        # bounded by the 4k headroom under the 32k context.
        return max(0, len(blob) // 4)


# ── History serializer ────────────────────────────────────────────────────
def build_prior_messages_from_history(
    project: Any,
    n_turns: Optional[int] = None,
    compaction_summary: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Serialize the last N completed agent turns of `project` into the
    OpenAI Chat Completions message shape consumed by the LLM.

    If `compaction_summary` is provided, prepend a synthetic
    `{role:"system", content:"<compacted history>: ..."}` message so the
    LLM sees folded older context before the verbatim recent turns.
    Used by both first-call (build_planner_package) and resume paths.
    """
    n = int(n_turns if n_turns is not None else DEFAULT_PRIOR_TURNS)
    if n <= 0:
        n = 0
    messages: List[Dict[str, Any]] = []

    if compaction_summary:
        messages.append(
            {
                "role": "system",
                "content": f"<compacted history>: {compaction_summary}",
            }
        )

    turns = list(getattr(project, "agent_turns", None) or [])
    if not turns or n == 0:
        return messages

    # Walk the most recent n turns; only include reaching-terminal turns
    # so we don't leak in-flight pending state into a sibling turn's
    # context.
    recent = [t for t in turns if getattr(t, "status", None) in ("completed", "failed", "canceled")]
    recent = recent[-n:]

    for turn in recent:
        user_content = getattr(turn, "user_message", "") or ""
        if user_content:
            messages.append({"role": "user", "content": user_content})

        assistant_text = getattr(turn, "response", "") or ""
        tool_calls_payload: List[Dict[str, Any]] = []
        for tc in list(getattr(turn, "tool_calls", []) or []):
            tc_id = getattr(tc, "id", None) or ""
            tool_name = getattr(tc, "tool_name", None) or ""
            args = getattr(tc, "arguments", None) or {}
            try:
                args_str = json.dumps(args, ensure_ascii=False, default=str)
            except Exception:  # pragma: no cover — defensive
                args_str = "{}"
            tool_calls_payload.append(
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": args_str},
                }
            )

        assistant_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_text}
        if tool_calls_payload:
            assistant_msg["tool_calls"] = tool_calls_payload
        messages.append(assistant_msg)

        # tool result messages — one per call that has a snapshot/error.
        for tc in list(getattr(turn, "tool_calls", []) or []):
            tc_id = getattr(tc, "id", None) or ""
            tool_name = getattr(tc, "tool_name", None) or ""
            snapshot = getattr(tc, "result_snapshot", None)
            err = getattr(tc, "error", None)
            if snapshot is None and not err:
                continue
            content_obj: Any = snapshot if snapshot is not None else {"error": err}
            try:
                serialized = json.dumps(content_obj, ensure_ascii=False, default=str)
            except Exception:  # pragma: no cover — defensive
                serialized = str(content_obj)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": tool_name,
                    "content": serialized,
                }
            )
    return messages


# ── Summarizer ────────────────────────────────────────────────────────────
_SUMMARIZE_PROMPT = (
    "Summarize the following Atelier agent turns into <=500 tokens "
    "preserving (a) which nodes were created with which intent, (b) "
    "what user has approved/rejected, (c) any standing constraints. "
    "Output prose, no JSON, no preamble."
)


def summarize_messages(messages_to_fold: List[Dict[str, Any]], project: Any) -> str:
    """Produce a rolling summary of `messages_to_fold`.

    Tries the LLMAdapter; on any failure (adapter unavailable, no API
    key, network error, empty completion) returns a deterministic
    fallback string so tests pass offline and the agent still gets a
    bounded context. The fallback enumerates the recent message
    role/content fragments — not pretty, but correct.
    """
    try:
        # Local import keeps test seams predictable and avoids a
        # circular import via atelier_agent (which imports this module).
        from .llm_adapter import LLMAdapter  # type: ignore
    except Exception:  # pragma: no cover — defensive
        return _fallback_summary(messages_to_fold)

    try:
        adapter = LLMAdapter()
    except Exception:  # pragma: no cover — defensive
        return _fallback_summary(messages_to_fold)

    if not getattr(adapter, "is_configured", False):
        return _fallback_summary(messages_to_fold)

    try:
        blob = json.dumps(messages_to_fold, ensure_ascii=False, default=str)
    except Exception:  # pragma: no cover — defensive
        blob = str(messages_to_fold)
    chat_messages = [
        {"role": "system", "content": _SUMMARIZE_PROMPT},
        {"role": "user", "content": blob},
    ]
    try:
        raw = adapter.chat(chat_messages)
    except Exception as exc:
        logger.warning("Atelier compaction summarize failed: %s", exc)
        return _fallback_summary(messages_to_fold)
    if not raw or not isinstance(raw, str):
        return _fallback_summary(messages_to_fold)
    text = raw.strip()
    return text or _fallback_summary(messages_to_fold)


def _fallback_summary(messages_to_fold: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for msg in messages_to_fold:
        role = str(msg.get("role") or "?")
        content = msg.get("content")
        if isinstance(content, str):
            snippet = content[:240]
        elif content is not None:
            try:
                snippet = json.dumps(content, ensure_ascii=False, default=str)[:240]
            except Exception:  # pragma: no cover — defensive
                snippet = str(content)[:240]
        else:
            snippet = ""
        parts.append(f"[{role}] {snippet}")
    body = " | ".join(parts) if parts else "(no prior turns)"
    return f"Compacted earlier turns: {body}"


# ── Compaction entrypoint ─────────────────────────────────────────────────
def maybe_compact_messages(
    messages: List[Dict[str, Any]],
    system_prompt: str,
    project: Any,
    max_tokens: Optional[int] = None,
    keep_recent: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Inspect `messages`; if `(estimate_tokens > max_tokens)` AND there
    is enough history to fold, replace the oldest `(len(messages) -
    keep_recent)` entries with a single `{role:"system", content:
    "<compaction summary>: ..."}` message. Persists the rolling summary
    onto `project.agent_compaction_summary` and the most-recent folded
    turn id onto `project.compacted_through_turn_id` so resume paths
    don't re-fold.

    Returns `(messages_out, summary_or_None)`. `summary` is None when
    no compaction ran; callers can use that to decide whether to call
    pipeline._save_atelier_data().
    """
    cap = int(max_tokens if max_tokens is not None else MAX_PROMPT_TOKENS)
    keep = int(keep_recent if keep_recent is not None else KEEP_RECENT_TURNS)
    keep = max(0, keep)

    if not messages:
        return list(messages), None

    if estimate_tokens(messages, system_prompt) <= cap:
        return list(messages), None

    if len(messages) <= keep:
        # Already at-or-below the floor — no room to fold without
        # destroying the recent context the model relies on.
        return list(messages), None

    fold_count = len(messages) - keep
    to_fold = list(messages[:fold_count])
    keep_tail = list(messages[fold_count:])

    _fire_before(to_fold, project)

    summary = summarize_messages(to_fold, project)

    folded_msg: Dict[str, Any] = {
        "role": "system",
        "content": f"<compaction summary>: {summary}",
    }
    new_messages = [folded_msg] + keep_tail

    # Persist the rolling summary + most-recent folded turn id onto the
    # project so resume paths see it. compacted_through_turn_id is a
    # best-effort cursor — we cannot reliably attribute every wire-shape
    # message back to a turn id (tool messages don't carry one), so we
    # fall back to the most recent terminal turn currently on the
    # project under the assumption that the fold absorbed everything up
    # through it.
    try:
        project.agent_compaction_summary = summary
    except Exception:  # pragma: no cover — defensive
        logger.exception("could not persist agent_compaction_summary onto project")
    try:
        turns = list(getattr(project, "agent_turns", None) or [])
        terminal = [t for t in turns if getattr(t, "status", None) in ("completed", "failed", "canceled")]
        if terminal:
            project.compacted_through_turn_id = getattr(terminal[-1], "id", None)
    except Exception:  # pragma: no cover — defensive
        logger.exception("could not persist compacted_through_turn_id onto project")

    _fire_after(summary, project)

    return new_messages, summary


__all__ = [
    "MAX_PROMPT_TOKENS",
    "SUMMARY_TOKEN_BUDGET",
    "KEEP_RECENT_TURNS",
    "DEFAULT_PRIOR_TURNS",
    "estimate_tokens",
    "build_prior_messages_from_history",
    "summarize_messages",
    "maybe_compact_messages",
    "register_before_compaction",
    "register_after_compaction",
]
