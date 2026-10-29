"""Model-backed loop planner must use durable claim/settle before provider calls."""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import json
from threading import Event

import pytest

from src.apps.atelier.agent_loop import AgentLoopPlannerFailed, AtelierAgentLoopService, LoopPlannerContext
from src.apps.atelier.agent_loop_commands import StartAgentLoopCommand
from src.apps.atelier.agent_loop_model_planner import ModelBackedLoopPlanner, ModelPlannerConfig
from src.apps.atelier.agent_planner_invocations import ClaimAgentPlannerInvocationCommand
from src.apps.atelier.models import AtelierAgentPlannerConfigIdentity, AtelierAgentPlannerMessage
from src.apps.atelier.storage import AtelierStorage
from src.utils.json_storage import StorageConflictError
from src.utils.storage_identity import initialize_storage_identity

pytest_plugins = ("tests.test_atelier_agent_turns",)


@dataclass(frozen=True)
class FakeChatResult:
    content: str
    requested_model: str = "qwen-plus"
    provider_model: str | None = "qwen-plus"
    prompt_tokens: int | None = 3
    completion_tokens: int | None = 2
    total_tokens: int | None = 5


class FakeBoundedChat:
    def __init__(self, store: AtelierStorage, *, content: str | None = None, fail: Exception | None = None, on_call=None):
        self.store = store
        self.content = content or json.dumps({
            "assistant_message": "Inspect the project.",
            "tool_calls": [{"tool_name": "canvas.readProject", "arguments": {}}],
        })
        self.fail = fail
        self.on_call = on_call
        self.calls: list[dict] = []

    def __call__(self, messages, *, model, max_completion_tokens, timeout, response_format):
        project = self.store.get_project("p")
        turn = project.agent_turns[0]
        assert turn.planner_invocations[0].status == "claimed"
        assert [message.model_dump(mode="json") for message in turn.planner_invocations[0].input_messages] == messages
        assert response_format == {"type": "json_object"}
        self.calls.append({
            "messages": messages,
            "model": model,
            "max_completion_tokens": max_completion_tokens,
            "timeout": timeout,
        })
        if self.on_call is not None:
            self.on_call()
        if self.fail is not None:
            raise self.fail
        return FakeChatResult(content=self.content, requested_model=model)


def _config(max_completion_tokens=64) -> ModelPlannerConfig:
    return ModelPlannerConfig(
        provider_id="dashscope",
        model_id="qwen-plus",
        config_identity=AtelierAgentPlannerConfigIdentity(
            provider_scope="dashscope-compatible",
            runtime_config_id="test-runtime-v1",
            model_config_version="catalog-test-v1",
            credential_ref="credential-binding-1",
            storage_identity="storage-test",
        ),
        max_completion_tokens=max_completion_tokens,
        timeout_seconds=7.5,
    )


def _start(*, expected_revision=0, turn_id="model-loop", max_iterations=1, skill_name=None):
    return StartAgentLoopCommand(
        expected_revision=expected_revision,
        client_turn_id=turn_id,
        user_message="Inspect then propose.",
        selected_node_id="video",
        planner="model_adapter",
        max_iterations=max_iterations,
        skill_name=skill_name,
    )


def _loop(store: AtelierStorage, chat: FakeBoundedChat, root, *, max_completion_tokens=64) -> AtelierAgentLoopService:
    identity = initialize_storage_identity(root, writer_lock=store._writer_lock)["storage_id"]
    planner = ModelBackedLoopPlanner(config_provider=lambda: _config(max_completion_tokens), bounded_chat=chat)
    return AtelierAgentLoopService(
        store,
        planner=lambda _ctx: (_ for _ in ()).throw(AssertionError("deterministic planner must not run")),
        model_planner=planner,
        clock=itertools.count(10.0).__next__,
        id_factory=(f"model-id-{idx}" for idx in itertools.count(1)).__next__,
        server_storage_identity=identity,
    )


def test_model_loop_claims_before_one_bounded_chat_then_saves_existing_projection(app):
    _executor, store, _bind, _scheduled, _image, root = app
    chat = FakeBoundedChat(store)
    loop = _loop(store, chat, root)

    ack = loop.start_loop("p", _start())

    turn = ack.turn
    assert len(chat.calls) == 1
    assert chat.calls[0]["model"] == "qwen-plus"
    assert chat.calls[0]["max_completion_tokens"] == 64
    assert chat.calls[0]["timeout"] == 7.5
    assert turn.loop_start_context.planner == "model_adapter"
    assert turn.planner_invocations[0].status == "settled"
    assert turn.planner_invocations[0].provider_model == "qwen-plus"
    assert turn.iteration_count == 1
    assert turn.tool_calls[0].tool_name == "canvas.readProject"
    assert turn.tool_calls[0].result_snapshot["node_count"] == 2
    assert turn.status == "completed"


def test_model_loop_unsettled_restart_never_calls_bounded_chat(app):
    _executor, store, _bind, _scheduled, _image, root = app
    started, _ = store.start_agent_loop_turn("p", _start(), now=10.0)
    store.claim_agent_planner_invocation(
        "p",
        ClaimAgentPlannerInvocationCommand(
            expected_revision=started.revision,
            turn_id="model-loop",
            iteration_index=1,
            claim_request_id="claim-existing",
            provider_id="dashscope",
            model_id="qwen-plus",
            config_identity=_config().config_identity,
            input_messages=(AtelierAgentPlannerMessage(role="user", content="old frozen input"),),
            max_completion_tokens=64,
            timeout_seconds=7.5,
        ),
        now=11.0,
    )
    chat = FakeBoundedChat(store)
    loop = _loop(store, chat, root)

    with pytest.raises(StorageConflictError, match="unsettled"):
        loop.resume_loop("p", "model-loop")

    assert chat.calls == []
    assert store.get_project("p").agent_turns[0].planner_invocations[0].status == "claimed"


def test_model_loop_settled_success_restart_projects_without_requery(app):
    _executor, store, _bind, _scheduled, _image, root = app
    cancel = Event()
    chat = FakeBoundedChat(store, on_call=cancel.set)
    loop = _loop(store, chat, root)

    closed = list(loop.iter_start_loop("p", _start(), cancel_event=cancel))[-1]
    assert closed.type == "loop_closed"
    assert len(chat.calls) == 1
    saved = store.get_project("p").agent_turns[0]
    assert saved.planner_invocations[0].status == "settled"
    assert saved.iteration_count == 0

    replay_chat = FakeBoundedChat(store)
    replay_loop = _loop(store, replay_chat, root)
    ack = replay_loop.resume_loop("p", "model-loop")

    assert replay_chat.calls == []
    assert ack.turn.iteration_count == 1
    assert ack.turn.tool_calls[0].tool_name == "canvas.readProject"
    assert ack.turn.status == "completed"


def test_model_loop_malformed_settled_success_records_failed_loop_without_tool_rights(app):
    _executor, store, _bind, _scheduled, _image, root = app
    chat = FakeBoundedChat(store, content=json.dumps({"assistant_message": "bad", "tool_calls": [{"tool_name": "shell.exec", "arguments": {}}]}))
    loop = _loop(store, chat, root)

    ack = loop.start_loop("p", _start())

    assert len(chat.calls) == 1
    assert ack.turn.status == "failed"
    assert ack.turn.response == "planner_parse_failed"
    assert ack.turn.tool_calls == []
    assert ack.turn.planner_invocations[0].status == "settled"
    assert ack.turn.planner_invocations[0].outcome == "success"


def test_model_loop_provider_failure_settles_and_records_failed_loop_without_tools(app):
    _executor, store, _bind, _scheduled, _image, root = app
    chat = FakeBoundedChat(store, fail=RuntimeError("provider leaked detail"))
    loop = _loop(store, chat, root)

    ack = loop.start_loop("p", _start())

    assert len(chat.calls) == 1
    assert ack.turn.status == "failed"
    assert ack.turn.response == "planner_failed"
    assert ack.turn.tool_calls == []
    invocation = ack.turn.planner_invocations[0]
    assert invocation.outcome == "failed"
    assert invocation.result_content is None
    assert invocation.charged_completion_tokens == 64


def test_model_loop_claim_failure_never_calls_bounded_chat(app):
    _executor, store, _bind, _scheduled, _image, root = app
    chat = FakeBoundedChat(store)
    loop = _loop(store, chat, root, max_completion_tokens=4096)

    with pytest.raises(StorageConflictError, match="budget"):
        loop.start_loop("p", _start())

    assert chat.calls == []


def test_model_prompt_includes_registry_tool_schemas_policy_and_output_contract(app):
    _executor, store, _bind, _scheduled, _image, root = app

    def add_pending(project):
        video = next(node for node in project.nodes if node.id == "video")
        video.data["pending_reference_nodes"] = [{"schema_version": 1, "source_node_id": "image", "source_kind": "image", "status": "pending_media", "provider_request_id": "SECRET"}]

    store.apply_project_command("p", expected_revision=0, command=add_pending)
    chat = FakeBoundedChat(store)
    loop = _loop(store, chat, root)

    loop.start_loop("p", _start(expected_revision=1))

    user_payload = json.loads(chat.calls[0]["messages"][-1]["content"])
    tool_names = {item["name"] for item in user_payload["allowed_tool_schemas"]}
    assert tool_names == {
        "canvas.readProject",
        "canvas.createVideoNode",
        "canvas.updateNodePrompt",
        "generation.createVideoCandidates",
    }
    assert user_payload["tool_schema_version"] == "atelier.tools.readonly.v1"
    assert user_payload["policy_snapshot"]["approval_mode"] == "untrusted"
    assert user_payload["output_contract"]["required"] == ["assistant_message", "tool_calls"]
    assert user_payload["selected_node_snapshot"]["id"] == "video"
    assert user_payload["selected_node_snapshot"]["reference_image_urls"] == ["uploads/frame.png"]
    assert user_payload["selected_node_snapshot"]["pending_reference_nodes"] == [{"schema_version": 1, "source_node_id": "image", "source_kind": "image", "status": "pending_media"}]
    assert "SECRET" not in json.dumps(user_payload, sort_keys=True)
    assert user_payload["output_contract"]["properties"]["tool_calls"]["items"]["properties"]["tool_name"]["enum"] == [
        "canvas.createVideoNode",
        "canvas.readProject",
        "canvas.updateNodePrompt",
        "generation.createVideoCandidates",
    ]



def test_model_prompt_includes_frozen_product_skill_and_narrows_allowed_tools(app):
    from src.apps.atelier.product_skills import resolve_product_skill_snapshot

    _executor, store, _bind, _scheduled, _image, root = app
    snapshot = resolve_product_skill_snapshot("story-scout")
    started, _ = store.start_agent_loop_turn("p", _start(turn_id="skill-loop", skill_name="story-scout"), now=10.0, resolved_skill_snapshot=snapshot)
    chat = FakeBoundedChat(store)
    planner = ModelBackedLoopPlanner(config_provider=lambda: _config(), bounded_chat=chat)

    planner(
        LoopPlannerContext(project=started, turn=started.agent_turns[0], messages_history=[]),
        storage=store,
        project_id="p",
        expected_revision=started.revision,
        iteration_index=1,
        clock=itertools.count(11.0).__next__,
    )

    payload = json.loads(chat.calls[0]["messages"][-1]["content"])
    assert payload["active_skill"] == {
        "name": snapshot.name,
        "version": snapshot.version,
        "title": snapshot.title,
        "description": snapshot.description,
        "category": snapshot.category,
        "prompt_template": snapshot.prompt_template,
        "expected_tools": list(snapshot.expected_tools),
        "content_hash": snapshot.content_hash,
    }
    assert [item["name"] for item in payload["allowed_tool_schemas"]] == ["canvas.readProject"]
    assert payload["output_contract"]["properties"]["tool_calls"]["items"]["properties"]["tool_name"]["enum"] == ["canvas.readProject"]


def test_model_loop_uses_config_bound_executor_without_second_runtime_executor(app):
    _executor, store, _bind, _scheduled, _image, root = app
    bound_chat = FakeBoundedChat(store)
    fallback_chat = FakeBoundedChat(store, fail=AssertionError("fallback chat must not run"))
    identity = initialize_storage_identity(root, writer_lock=store._writer_lock)["storage_id"]

    def config_provider():
        config = _config()
        return ModelPlannerConfig(
            provider_id=config.provider_id,
            model_id=config.model_id,
            config_identity=config.config_identity,
            max_completion_tokens=config.max_completion_tokens,
            timeout_seconds=config.timeout_seconds,
            bounded_chat=bound_chat,
        )

    planner = ModelBackedLoopPlanner(config_provider=config_provider, bounded_chat=fallback_chat)
    loop = AtelierAgentLoopService(
        store,
        planner=lambda _ctx: (_ for _ in ()).throw(AssertionError("deterministic planner must not run")),
        model_planner=planner,
        clock=itertools.count(10.0).__next__,
        id_factory=(f"model-id-{idx}" for idx in itertools.count(1)).__next__,
        server_storage_identity=identity,
    )

    loop.start_loop("p", _start())

    assert len(bound_chat.calls) == 1
    assert fallback_chat.calls == []


def test_model_loop_cancel_after_claim_settles_zero_usage_without_provider_call(app):
    _executor, store, _bind, _scheduled, _image, root = app
    started, _ = store.start_agent_loop_turn("p", _start(), now=10.0)
    turn = started.agent_turns[0]
    cancel = Event()
    cancel.set()
    chat = FakeBoundedChat(store, fail=AssertionError("provider must not run after cancellation"))
    planner = ModelBackedLoopPlanner(config_provider=lambda: _config(), bounded_chat=chat)

    with pytest.raises(AgentLoopPlannerFailed, match="planner_cancelled_before_call"):
        planner(
            LoopPlannerContext(project=started, turn=turn, messages_history=[]),
            storage=store,
            project_id="p",
            expected_revision=started.revision,
            iteration_index=1,
            clock=itertools.count(11.0).__next__,
            cancel_event=cancel,
        )

    saved = store.get_project("p").agent_turns[0].planner_invocations[0]
    assert chat.calls == []
    assert saved.status == "settled"
    assert saved.outcome == "failed"
    assert saved.error_code == "planner_cancelled_before_call"
    assert saved.usage is not None and saved.usage.completion_tokens == 0
    assert saved.charged_completion_tokens == 0


@pytest.mark.parametrize(
    "content",
    [
        '{"assistant_message":"one","assistant_message":"two","tool_calls":[]}',
        '{"assistant_message":"bad","tool_calls":[{"tool_name":["canvas.readProject"],"arguments":{}}]}',
        '{"assistant_message":"bad","tool_calls":[{"tool_name":"canvas.readProject","arguments":{"x":NaN}}]}',
        '{"assistant_message":"missing tool calls"}',
    ],
)
def test_model_loop_strict_parser_malformed_json_records_failed_loop_without_typeerror(app, content):
    _executor, store, _bind, _scheduled, _image, root = app
    chat = FakeBoundedChat(store, content=content)
    loop = _loop(store, chat, root)

    ack = loop.start_loop("p", _start())

    assert len(chat.calls) == 1
    assert ack.turn.status == "failed"
    assert ack.turn.response == "planner_parse_failed"
    assert ack.turn.tool_calls == []
    assert ack.turn.planner_invocations[0].outcome == "success"


def test_model_loop_settled_success_with_invalid_tool_arguments_terminalizes_without_rewriting_settlement(app):
    _executor, store, _bind, _scheduled, _image, root = app
    chat = FakeBoundedChat(
        store,
        content=json.dumps({
            "assistant_message": "bad generation args",
            "tool_calls": [{"tool_name": "generation.createVideoCandidates", "arguments": {}}],
        }),
    )
    loop = _loop(store, chat, root)

    ack = loop.start_loop("p", _start())

    assert len(chat.calls) == 1
    assert ack.turn.status == "failed"
    assert ack.turn.response == "planner_projection_failed"
    assert ack.turn.tool_calls == []
    invocation = ack.turn.planner_invocations[0]
    assert invocation.status == "settled"
    assert invocation.outcome == "success"
    assert invocation.error_code is None

def test_model_loop_rejects_nonempty_read_project_arguments_without_tool_rights(app):
    _executor, store, _bind, _scheduled, _image, root = app
    chat = FakeBoundedChat(
        store,
        content=json.dumps({
            "assistant_message": "bad read args",
            "tool_calls": [{"tool_name": "canvas.readProject", "arguments": {"unexpected": True}}],
        }),
    )
    loop = _loop(store, chat, root)

    ack = loop.start_loop("p", _start())

    assert len(chat.calls) == 1
    assert ack.turn.status == "failed"
    assert ack.turn.response == "planner_parse_failed"
    assert ack.turn.tool_calls == []
    assert ack.turn.planner_invocations[0].outcome == "success"
