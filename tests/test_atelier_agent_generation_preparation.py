"""Preparation is shared by Agent entries without granting execution authority."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.apps.atelier.agent_generation_preparation import prepare_agent_generation_call
from src.apps.atelier.agent_turn_commands import StartAgentTurnCommand

pytest_plugins = ("tests.test_atelier_agent_turns",)


def _prepare(executor, store, proposal, **overrides):
    options = dict(
        turn_id="turn-1", call_id="call-1", approval_id="approval-1",
        client_request_id="request-1", expected_revision=0,
        media_scope=store._media_write_scope(), credential=executor._credential_provider,
        server_storage_identity=executor._resolve_server_storage_identity(), clock=lambda: 10.0,
    )
    options.update(overrides)
    return prepare_agent_generation_call(store.get_project("p"), proposal, **options)


def _proposal(executor):
    return executor._readonly.plan("p", user_message="生成视频候选", selected_node_id="video").tool_calls[0]["arguments"]


def test_shared_preparation_matches_saved_entry_call_without_admitting_or_mutating(app):
    executor, store, _bind, scheduled, _, root = app
    proposal = _proposal(executor)
    before_proposal = deepcopy(proposal)
    before_project = store.get_project("p").model_dump(mode="json")
    before_bytes = (root / "atelier_projects.json").read_bytes()

    call = _prepare(executor, store, proposal)
    assert proposal == before_proposal
    assert store.get_project("p").model_dump(mode="json") == before_project
    assert (root / "atelier_projects.json").read_bytes() == before_bytes
    assert scheduled == []
    assert call.approval_granted is False
    assert call.approval_record is None
    assert call.status.value == "approval_required"
    assert call.preview_binding.preview_public_record["raw_request"]["client_request_id"] == "request-1"
    assert "client_request_id" not in call.arguments
    assert "test-only-secret" not in call.model_dump_json()

    ack = executor.start_turn("p", StartAgentTurnCommand(
        expected_revision=0, client_turn_id="turn-1", user_message="生成视频候选", selected_node_id="video",
    ))
    assert ack.call.model_dump(mode="json") == call.model_dump(mode="json")
    assert ack.project.generation_attempts == []
    assert ack.project.generation_request_receipts == []
    assert scheduled == []


def test_invalid_preparation_cannot_bind_credentials_or_write_project(app):
    executor, store, bind, scheduled, _, root = app
    proposal = _proposal(executor)
    proposal["mode"] = "unsupported"
    before = (root / "atelier_projects.json").read_bytes()
    with pytest.raises(ValueError):
        _prepare(executor, store, proposal)
    bind.assert_not_called()
    assert (root / "atelier_projects.json").read_bytes() == before
    assert scheduled == []


def test_preparation_uses_the_supplied_project_snapshot_without_storage_reread(app, monkeypatch):
    executor, store, _bind, scheduled, _, _ = app
    proposal = _proposal(executor)
    project = store.get_project("p")
    storage_identity = executor._resolve_server_storage_identity()
    monkeypatch.setattr(store, "get_project", Mock(side_effect=AssertionError("must use supplied snapshot")))
    call = prepare_agent_generation_call(
        project, proposal, turn_id="turn", call_id="call", approval_id="approval",
        client_request_id="request", expected_revision=0, media_scope=store._media_write_scope(),
        credential=executor._credential_provider, server_storage_identity=storage_identity, clock=lambda: 10.0,
    )
    assert call.arguments["node_id"] == "video"
    assert project.agent_turns == []
    assert project.generation_attempts == []
    assert scheduled == []
