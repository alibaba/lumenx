"""v1.4 BATCH 4 — Provider + Observability tests.

Four cases:
  A. test_atelier_runtime_provider_resolves_dashscope_default
  B. test_atelier_agent_loop_records_per_iteration_usage
  C. test_atelier_agent_loop_honors_planner_package_model_override
  D. test_atelier_agent_writes_structured_events_to_jsonl
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List
from unittest.mock import patch

import pytest

from src.apps.comic_gen import atelier_runtime_provider as rp
from src.apps.comic_gen.models import AtelierAgentTurn
from src.apps.comic_gen.pipeline import ComicGenPipeline


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """Fresh pipeline with tmp persistence + structured event log routed
    into the test tmp dir so the parent worktree's logs/ stays clean."""
    monkeypatch.delenv("ATELIER_AGENT_LOG_DISABLED", raising=False)
    monkeypatch.setenv("ATELIER_AGENT_LOG_PATH", str(tmp_path / "events.jsonl"))
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        p = ComicGenPipeline()
    p.data_file = str(tmp_path / "projects.json")
    p.series_data_file = str(tmp_path / "series.json")
    p.atelier_data_file = str(tmp_path / "atelier_projects.json")
    p.scripts = {}
    p.series_store = {}
    p.atelier_projects = {}
    return p


# ---------------------------------------------------------------------------
# CASE A — runtime_provider resolution
# ---------------------------------------------------------------------------


def test_atelier_runtime_provider_resolves_dashscope_default(monkeypatch):
    """resolve(provider, model_id) returns the right ProviderConfig.

    Covers exact-match, provider-only, model_id-only, and the default
    fallback (None, None). Also asserts the DashScope compatible-mode
    base URL + DASHSCOPE_API_KEY env name + qwen-turbo fallback chain
    are wired correctly.
    """
    cfg = rp.resolve("dashscope", "qwen-plus")
    assert cfg.provider == "dashscope"
    assert cfg.model_id == "qwen-plus"
    assert cfg.api_mode == "chat_completions"
    assert cfg.base_url is not None and cfg.base_url.endswith("/compatible-mode/v1")
    assert cfg.key_env == "DASHSCOPE_API_KEY"
    assert cfg.fallback_model == "qwen-turbo"

    # (None, None) → resolve_default(). Default env reads
    # ATELIER_AGENT_PROVIDER which we leave unset → 'dashscope', then
    # DASHSCOPE_AGENT_MODEL which we also leave unset → first dashscope
    # entry (qwen-plus).
    monkeypatch.delenv("ATELIER_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("DASHSCOPE_AGENT_MODEL", raising=False)
    cfg2 = rp.resolve(None, None)
    assert cfg2.provider == "dashscope"
    assert cfg2.model_id == "qwen-plus"

    # model_id-only → cross-provider lookup.
    cfg3 = rp.resolve(None, "claude-3-5-sonnet-20241022")
    assert cfg3.provider == "anthropic"
    assert cfg3.api_mode == "anthropic_messages"

    # Fallback resolution returns the matching ProviderConfig.
    fb = rp.fallback_for(cfg)
    assert fb is not None
    assert fb.model_id == "qwen-turbo"


# ---------------------------------------------------------------------------
# CASE B — per-iteration usage capture
# ---------------------------------------------------------------------------


def test_atelier_agent_loop_records_per_iteration_usage(pipeline, monkeypatch):
    """run_agent_loop_streaming populates AtelierAgentTurn.iterations with
    a per-iteration UsageRecord-shaped payload."""
    project = pipeline.create_atelier_project("Usage Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    def fake_stream(package, user_message, model=None, max_tokens=None):
        # Single round, no tool calls — loop terminates after this.
        yield {"type": "delta", "text": '{"response":"Done.","tool_calls":[]}'}
        yield {
            "type": "done",
            "response": "Done.",
            "tool_calls": [],
            "error": None,
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
                "model_id": "qwen-plus",
                "latency_ms": 850.0,
            },
        }

    monkeypatch.setattr(
        "src.apps.comic_gen.atelier_agent.stream_atelier_llm_planner", fake_stream
    )

    events = list(
        pipeline.run_atelier_agent_loop_streaming(
            project_id=project.id,
            user_message="Recap the canvas.",
            model="qwen-plus",
        )
    )

    # Final terminal frame carries the persisted turn.
    terminal = next(e for e in events if e.get("type") == "turn_done")
    turn = terminal["turn"]
    assert isinstance(turn, AtelierAgentTurn)
    assert len(turn.iterations) == 1
    rec = turn.iterations[0]
    assert rec.idx == 1
    assert rec.model_id == "qwen-plus"
    assert rec.provider == "dashscope"
    assert rec.prompt_tokens == 120
    assert rec.completion_tokens == 40
    assert rec.total_tokens == 160
    # Latency from the usage block is preserved verbatim.
    assert rec.latency_ms == 850.0
    assert rec.error is None

    # JSON round-trip preserves the iteration record (so persisted turns
    # survive a backend restart with their usage intact).
    blob = turn.model_dump_json()
    rehydrated = AtelierAgentTurn.model_validate_json(blob)
    assert len(rehydrated.iterations) == 1
    assert rehydrated.iterations[0].prompt_tokens == 120
    assert rehydrated.iterations[0].model_id == "qwen-plus"


# ---------------------------------------------------------------------------
# CASE C — model_override threading via runtime_provider.resolve
# ---------------------------------------------------------------------------


def test_atelier_agent_loop_honors_planner_package_model_override(pipeline, monkeypatch):
    """run_agent_loop_streaming with model='qwen-max' resolves to the
    qwen-max ProviderConfig and stamps IterationRecord with that model.

    Asserts atelier_runtime_provider.resolve was called with
    (None or "dashscope", "qwen-max") so the override flows from the
    loop kwarg into the runtime layer cleanly.
    """
    project = pipeline.create_atelier_project("Override Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    captured_resolves: List[Any] = []

    real_resolve = rp.resolve

    def spy_resolve(provider=None, model_id=None):
        captured_resolves.append((provider, model_id))
        return real_resolve(provider, model_id)

    monkeypatch.setattr(rp, "resolve", spy_resolve)
    # The agent module imports resolve via `from . import atelier_runtime_provider`,
    # so patching the attribute on the module re-routes both call sites.

    def fake_stream(package, user_message, model=None, max_tokens=None):
        # Assert the resolved model id actually flowed through to the
        # streaming planner via the package.
        assert package.model_id == "qwen-max"
        yield {
            "type": "done",
            "response": "OK",
            "tool_calls": [],
            "error": None,
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 10,
                "total_tokens": 60,
                "model_id": "qwen-max",
                "latency_ms": 300.0,
            },
        }

    monkeypatch.setattr(
        "src.apps.comic_gen.atelier_agent.stream_atelier_llm_planner", fake_stream
    )

    events = list(
        pipeline.run_atelier_agent_loop_streaming(
            project_id=project.id,
            user_message="Switch to qwen-max",
            model="qwen-max",
        )
    )

    # resolve() was called at least once with model_id="qwen-max" (the
    # package-build call). The conversation_starts probe also calls
    # resolve, so we accept any call carrying "qwen-max" as the model id.
    qwen_max_calls = [c for c in captured_resolves if c[1] == "qwen-max"]
    assert len(qwen_max_calls) >= 1, captured_resolves

    terminal = next(e for e in events if e.get("type") == "turn_done")
    turn = terminal["turn"]
    assert isinstance(turn, AtelierAgentTurn)
    assert len(turn.iterations) == 1
    assert turn.iterations[0].model_id == "qwen-max"
    assert turn.iterations[0].provider == "dashscope"


# ---------------------------------------------------------------------------
# CASE D — structured JSONL event log
# ---------------------------------------------------------------------------


def test_atelier_agent_writes_structured_events_to_jsonl(pipeline, monkeypatch, tmp_path):
    """run_agent_loop_streaming emits the full conversation_starts →
    api_request → api_response → tool_decision → tool_result →
    conversation_ends sequence to logs/atelier-agent.jsonl. Drives one
    round whose tool_calls produces a single canvas.readProject call so
    the tool_result event lands."""
    project = pipeline.create_atelier_project("Eventlog Board")
    pipeline.update_atelier_agent_policy(
        project.id, {"approval_mode": "never", "allowed_tools": ["canvas.readProject"]}
    )

    def fake_stream(package, user_message, model=None, max_tokens=None):
        yield {
            "type": "done",
            "response": "Reading canvas.",
            "tool_calls": [
                {"tool_name": "canvas.readProject", "arguments": {}},
            ],
            "error": None,
            "usage": {
                "prompt_tokens": 80,
                "completion_tokens": 20,
                "total_tokens": 100,
                "model_id": "qwen-plus",
                "latency_ms": 420.0,
            },
        }

    monkeypatch.setattr(
        "src.apps.comic_gen.atelier_agent.stream_atelier_llm_planner", fake_stream
    )

    log_path = tmp_path / "events.jsonl"
    # Sanity: the pipeline fixture pinned ATELIER_AGENT_LOG_PATH to this
    # path, so the helper writes here. Drive the loop with a one-round
    # conversation that completes after the tool fires.
    events = list(
        pipeline.run_atelier_agent_loop_streaming(
            project_id=project.id,
            user_message="Read the canvas.",
            max_iterations=1,
        )
    )

    # Read the JSONL file and assert every event_type appears.
    assert log_path.exists(), "structured event log was not created"
    lines = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    types = [e["type"] for e in lines]
    expected = {
        "atelier.conversation_starts",
        "atelier.api_request",
        "atelier.api_response",
        "atelier.tool_decision",
        "atelier.tool_result",
        "atelier.conversation_ends",
    }
    missing = expected - set(types)
    assert not missing, f"missing event types: {missing}; saw {types}"

    # conversation_ends carries non-zero token + latency totals.
    end_event = next(e for e in lines if e["type"] == "atelier.conversation_ends")
    assert end_event.get("total_tokens", 0) >= 100
    assert end_event.get("total_latency_ms", 0.0) > 0.0
    assert end_event.get("iteration_count", 0) >= 1

    # tool_decision payload doesn't leak full arguments — just argument
    # keys (here: empty list since canvas.readProject has no args).
    decision_event = next(e for e in lines if e["type"] == "atelier.tool_decision")
    assert decision_event["tool_calls"][0]["tool_name"] == "canvas.readProject"
    assert decision_event["tool_calls"][0]["argument_keys"] == []

    # The terminal turn carries one IterationRecord whose tool_call_ids
    # back-link the executed canvas.readProject call.
    terminal = next(e for e in events if e.get("type") == "turn_done")
    turn = terminal["turn"]
    assert len(turn.iterations) == 1
    assert turn.iterations[0].tool_call_ids
