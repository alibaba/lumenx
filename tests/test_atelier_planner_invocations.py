from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json

import pytest

from src.apps.atelier.agent_planner_invocations import (
    ClaimAgentPlannerInvocationCommand,
    SettleAgentPlannerInvocationCommand,
)
from src.apps.atelier.models import (
    AtelierAgentLoopStartContext,
    AtelierAgentPlannerConfigIdentity,
    AtelierAgentPlannerInvocationUsage,
    AtelierAgentPlannerMessage,
    AtelierAgentPolicy,
    AtelierAgentToolCall,
    AtelierAgentTurn,
    AtelierNode,
    AtelierGenerationAttempt,
    AtelierGenerationFirstFrameSource,
    AtelierGenerationRequestReceipt,
    AtelierProject,
)
from src.apps.atelier.storage import AtelierStorage
from src.models import provider_recovery
from src.models.provider_execution import ProviderExecutionRecord
from src.utils.generation_intent import normalize_generation_intent
from src.utils.json_storage import StorageConflictError, StorageError
from src.utils.media_versions import MediaVersion


def provider_execution(attempt_id="attempt-1"):
    intent = normalize_generation_intent(
        {
            "model_id": "wan2.7-i2v",
            "mode": "i2v",
            "prompt": "A slow sunrise",
            "params": {
                "duration": 5,
                "resolution": "720p",
                "seed": 0,
                "prompt_extend": False,
                "negative_prompt": "",
                "watermark": False,
            },
        },
        allowed_modes={"i2v"},
        allowed_parameters={"duration", "resolution", "seed", "prompt_extend", "negative_prompt", "watermark"},
    )
    media = [asdict(MediaVersion("d" * 64, 100, "first_frame", 0, "image/png"))]
    scope = {
        "provider": "dashscope",
        "backend": "dashscope",
        "gateway": "dashscope_workspace",
        "base_url": "https://dashscope.aliyuncs.com",
        "region": "cn-beijing",
        "workspace_id": None,
        "account_identity": None,
    }
    return ProviderExecutionRecord(
        attempt_id=attempt_id,
        phase="accepted",
        provider_model_id="wan2.7-i2v",
        intent=intent.to_dict(),
        media=media,
        scope=scope,
        credential_ref="credential-ref",
        fingerprint=provider_recovery._fingerprint({
            "attempt_id": attempt_id,
            "provider_model_id": "wan2.7-i2v",
            "intent": intent.to_dict(),
            "media": media,
            "scope": scope,
            "credential_ref": "credential-ref",
        }),
    )


def project(*, budget=128, turn_mutation=None, blocking_generation=False):
    context = AtelierAgentLoopStartContext(
        user_message="make a plan",
        planner="model_adapter",
        max_iterations=3,
    )
    turn = AtelierAgentTurn(
        id="turn-1",
        project_id="p1",
        user_message=context.user_message,
        status="pending",
        start_request_fingerprint="0" * 64,
        loop_start_context=context,
        max_iterations=context.max_iterations,
        created_at=1.0,
    )
    if turn_mutation is not None:
        turn_mutation(turn)
    nodes = []
    generation_attempts = []
    generation_receipts = []
    if blocking_generation:
        turn.tool_calls.append(
            AtelierAgentToolCall(
                call_id="generation-call-1",
                tool_name="generation.createVideoCandidates",
                status="completed",
                result_snapshot={"client_request_id": "request-1"},
            )
        )
        nodes.append(
            AtelierNode(
                id="node-1",
                project_id="p1",
                type="video",
                data={
                    "candidates": [
                        {
                            "execution_contract": "atelier-provider-video-v1",
                            "id": "candidate-1",
                            "attempt_id": "attempt-1",
                            "media_kind": "video",
                            "status": "processing",
                            "video_url": None,
                            "error_code": None,
                            "created_at": 1.0,
                            "updated_at": 1.0,
                        }
                    ]
                },
                created_at=1.0,
                updated_at=1.0,
            )
        )
        execution = provider_execution()
        generation_attempts.append(
            AtelierGenerationAttempt(
                project_id="p1",
                node_id="node-1",
                candidate_id="candidate-1",
                attempt_id="attempt-1",
                status="processing",
                first_frame_source=AtelierGenerationFirstFrameSource(node_id="source-1", media_url="uploads/source.png"),
                provider_execution=execution,
                created_at=1.0,
                updated_at=1.0,
            )
        )
        generation_receipts.append(
            AtelierGenerationRequestReceipt(
                project_id="p1",
                entrypoint="atelier.provider_video.v1",
                request_contract_version="request-json-v1",
                client_request_id="request-1",
                request_fingerprint="b" * 64,
                node_id="node-1",
                candidate_id="candidate-1",
                attempt_id="attempt-1",
                execution_fingerprint=execution.fingerprint,
                base_revision=0,
                committed_revision=0,
                created_at=1.0,
            )
        )
    return AtelierProject(
        id="p1",
        title="Planner",
        agent_policy=AtelierAgentPolicy(max_tokens_per_turn=budget),
        nodes=nodes,
        agent_turns=[turn],
        generation_attempts=generation_attempts,
        generation_request_receipts=generation_receipts,
        created_at=1.0,
        updated_at=1.0,
    )


def store(tmp_path, *, budget=128, turn_mutation=None, blocking_generation=False):
    storage = AtelierStorage(tmp_path)
    storage.create_project(project(budget=budget, turn_mutation=turn_mutation, blocking_generation=blocking_generation))
    return storage


def claim_command(**overrides):
    data = {
        "expected_revision": 0,
        "turn_id": "turn-1",
        "iteration_index": 1,
        "claim_request_id": "claim-1",
        "provider_id": "dashscope",
        "model_id": "qwen-plus",
        "config_identity": AtelierAgentPlannerConfigIdentity(
            provider_scope="dashscope-compatible",
            runtime_config_id="local-config-v1",
            model_config_version="catalog-v1",
        ),
        "input_messages": (
            AtelierAgentPlannerMessage(role="system", content="planner"),
            AtelierAgentPlannerMessage(role="user", content="make a plan"),
        ),
        "max_completion_tokens": 4,
        "timeout_seconds": 10.0,
    }
    data.update(overrides)
    return ClaimAgentPlannerInvocationCommand(**data)


def settle_command(invocation_id, **overrides):
    data = {
        "invocation_id": invocation_id,
        "outcome": "success",
        "result_content": "ok",
        "provider_model": "qwen-plus",
        "usage": AtelierAgentPlannerInvocationUsage(
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
        ),
    }
    data.update(overrides)
    return SettleAgentPlannerInvocationCommand(**data)


def turn_of(storage):
    return storage.get_project("p1").agent_turns[0]


def test_claim_reserves_budget_and_freezes_input_config_identity(tmp_path):
    storage = store(tmp_path, budget=128)

    project_after, ack = storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    turn = project_after.agent_turns[0]
    invocation = ack.invocation
    assert ack.claimed is True
    assert turn.completion_tokens_reserved == 4
    assert turn.completion_tokens_used == 0
    assert invocation.project_id == "p1"
    assert invocation.turn_id == "turn-1"
    assert invocation.iteration_index == 1
    assert invocation.provider_id == "dashscope"
    assert invocation.model_id == "qwen-plus"
    assert invocation.config_identity.provider_scope == "dashscope-compatible"
    assert invocation.input_messages[1].content == "make a plan"
    assert invocation.budget_completion_tokens == 128
    assert invocation.reserved_completion_tokens == 4
    assert invocation.status == "claimed"


def test_duplicate_claim_same_content_is_readonly_and_does_not_reserve_again(tmp_path):
    storage = store(tmp_path, budget=128)
    _project_after, first = storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    project_after, second = storage.claim_agent_planner_invocation(
        "p1",
        claim_command(expected_revision=99),
        now=3.0,
    )

    turn = project_after.agent_turns[0]
    assert first.claimed is True
    assert second.claimed is False
    assert len(turn.planner_invocations) == 1
    assert turn.completion_tokens_reserved == 4


def test_duplicate_claim_different_payload_conflicts_without_second_reservation(tmp_path):
    storage = store(tmp_path, budget=128)
    storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    with pytest.raises(StorageConflictError):
        storage.claim_agent_planner_invocation(
            "p1",
            claim_command(input_messages=(AtelierAgentPlannerMessage(role="user", content="changed"),)),
            now=3.0,
        )

    turn = turn_of(storage)
    assert len(turn.planner_invocations) == 1
    assert turn.completion_tokens_reserved == 4


def test_claim_uses_current_policy_budget_and_blocks_bool_negative_or_over_budget(tmp_path):
    storage = store(tmp_path, budget=128)

    with pytest.raises(StorageConflictError):
        storage.claim_agent_planner_invocation("p1", claim_command(max_completion_tokens=129), now=2.0)

    assert turn_of(storage).planner_invocations == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_revision": True},
        {"iteration_index": 0},
        {"max_completion_tokens": True},
        {"max_completion_tokens": -1},
        {"timeout_seconds": 0},
    ],
)
def test_claim_command_rejects_invalid_bool_negative_or_zero_inputs(overrides):
    with pytest.raises(ValueError):
        claim_command(**overrides)


@pytest.mark.parametrize(
    "identity_factory",
    [
        lambda: {"headers": {"Authorization": "Bearer SECRET"}},
        lambda: AtelierAgentPlannerConfigIdentity(provider_scope="dashscope-compatible", credential_ref="sk-secret-token"),
        lambda: AtelierAgentPlannerConfigIdentity(provider_scope="https://dashscope.aliyuncs.com"),
    ],
)
def test_config_identity_rejects_secret_shaped_raw_config(identity_factory):
    with pytest.raises(ValueError):
        claim_command(config_identity=identity_factory())


def test_claim_requires_model_adapter_planner(tmp_path):
    storage = store(
        tmp_path,
        budget=128,
        turn_mutation=lambda turn: setattr(
            turn,
            "loop_start_context",
            turn.loop_start_context.model_copy(update={"planner": "deterministic_core"}),
        ),
    )

    with pytest.raises(StorageConflictError):
        storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)


@pytest.mark.parametrize(
    "mutation,command_overrides",
    [
        (lambda turn: setattr(turn, "iteration_count", 1), {}),
        (lambda turn: setattr(turn, "iteration_count", 0), {"iteration_index": 2, "claim_request_id": "claim-2"}),
        (lambda turn: setattr(turn, "iteration_count", 3), {}),
        (lambda turn: setattr(turn, "status", "waiting_approval"), {}),
        (lambda turn: turn.pending_tool_calls.append({"call_id": "pending"}), {}),
    ],
)
def test_claim_rejects_skip_future_max_waiting_or_pending_states(tmp_path, mutation, command_overrides):
    storage = store(tmp_path, budget=128, turn_mutation=mutation)

    with pytest.raises(StorageConflictError):
        storage.claim_agent_planner_invocation("p1", claim_command(**command_overrides), now=2.0)


def test_claim_rejects_blocking_generation_and_unsettled_prior_claim(tmp_path):
    blocked_storage = AtelierStorage(tmp_path / "blocked")
    blocked_storage._projects["p1"] = project(budget=128, blocking_generation=True).model_copy(update={"revision": 0})
    with pytest.raises(StorageConflictError):
        blocked_storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    unsettled_storage = store(tmp_path / "unsettled", budget=128)
    unsettled_storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    with pytest.raises(StorageConflictError):
        unsettled_storage.claim_agent_planner_invocation(
            "p1",
            claim_command(expected_revision=1, iteration_index=2, claim_request_id="claim-2"),
            now=3.0,
        )


def test_late_policy_change_does_not_reprice_existing_claim(tmp_path):
    storage = store(tmp_path, budget=256)
    _project_after, claim = storage.claim_agent_planner_invocation(
        "p1",
        claim_command(max_completion_tokens=200),
        now=2.0,
    )

    def lower_policy(project):
        project.agent_policy.max_tokens_per_turn = 128

    storage.apply_project_command("p1", expected_revision=1, command=lower_policy)
    project_after, ack = storage.settle_agent_planner_invocation(
        "p1",
        settle_command(
            claim.invocation.invocation_id,
            usage=None,
            outcome="failed",
            result_content=None,
            provider_model=None,
            error_code="provider_error",
        ),
        now=3.0,
    )

    turn = project_after.agent_turns[0]
    assert ack.invocation.budget_completion_tokens == 256
    assert ack.invocation.charged_completion_tokens == 200
    assert turn.completion_tokens_reserved == 0
    assert turn.completion_tokens_used == 200
    with pytest.raises(StorageConflictError):
        storage.claim_agent_planner_invocation(
            "p1",
            claim_command(expected_revision=project_after.revision, iteration_index=2, claim_request_id="claim-2"),
            now=4.0,
        )


def test_settle_unknown_usage_charges_full_reservation_and_is_idempotent(tmp_path):
    storage = store(tmp_path, budget=128)
    _project_after, claim = storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    settle = settle_command(
        claim.invocation.invocation_id,
        usage=None,
        outcome="timeout_unknown",
        result_content=None,
        provider_model=None,
        error_code="timeout",
        error_message="unknown after provider boundary",
    )
    project_after, first = storage.settle_agent_planner_invocation("p1", settle, now=4.0)
    project_replay, second = storage.settle_agent_planner_invocation("p1", settle, now=5.0)

    turn = project_after.agent_turns[0]
    assert first.settled is True
    assert second.settled is False
    assert project_replay.agent_turns[0].completion_tokens_used == 4
    assert turn.completion_tokens_reserved == 0
    assert turn.completion_tokens_used == 4
    assert first.invocation.charged_completion_tokens == 4
    assert first.invocation.outcome == "timeout_unknown"


def test_settle_true_zero_usage_releases_reservation_without_fabricating_unknown(tmp_path):
    storage = store(tmp_path, budget=128)
    _project_after, claim = storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    project_after, ack = storage.settle_agent_planner_invocation(
        "p1",
        settle_command(
            claim.invocation.invocation_id,
            usage=AtelierAgentPlannerInvocationUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        ),
        now=3.0,
    )

    turn = project_after.agent_turns[0]
    assert turn.completion_tokens_reserved == 0
    assert turn.completion_tokens_used == 0
    assert ack.invocation.usage.completion_tokens == 0
    assert ack.invocation.charged_completion_tokens == 0


def test_usage_over_reservation_records_actual_and_blocks_future_budget_breakthrough(tmp_path):
    storage = store(tmp_path, budget=128)
    _project_after, claim = storage.claim_agent_planner_invocation(
        "p1",
        claim_command(max_completion_tokens=2),
        now=2.0,
    )

    storage.settle_agent_planner_invocation(
        "p1",
        settle_command(
            claim.invocation.invocation_id,
            usage=AtelierAgentPlannerInvocationUsage(prompt_tokens=1, completion_tokens=130, total_tokens=131),
        ),
        now=3.0,
    )

    turn = turn_of(storage)
    assert turn.completion_tokens_used == 130
    assert turn.planner_invocations[0].charged_completion_tokens == 130
    with pytest.raises(StorageConflictError):
        storage.claim_agent_planner_invocation(
            "p1",
            claim_command(expected_revision=storage.get_project("p1").revision, iteration_index=2, claim_request_id="claim-2", max_completion_tokens=1),
            now=4.0,
        )


def test_settle_different_result_conflicts(tmp_path):
    storage = store(tmp_path, budget=128)
    _project_after, claim = storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)
    storage.settle_agent_planner_invocation("p1", settle_command(claim.invocation.invocation_id), now=3.0)

    with pytest.raises(StorageConflictError):
        storage.settle_agent_planner_invocation(
            "p1",
            settle_command(claim.invocation.invocation_id, result_content="different"),
            now=4.0,
        )



def corrupt_payload_and_reload(tmp_path, setup, mutate):
    storage = store(tmp_path, budget=128)
    setup(storage)
    storage.close()
    path = tmp_path / "atelier_projects.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload["p1"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(StorageError):
        AtelierStorage(tmp_path)


def test_load_rejects_tampered_claim_fingerprints_and_claimed_results(tmp_path):
    def setup(storage):
        storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    corrupt_payload_and_reload(
        tmp_path / "input-fingerprint",
        setup,
        lambda project_payload: project_payload["agent_turns"][0]["planner_invocations"][0]["input_messages"][1].update(
            {"content": "tampered"}
        ),
    )
    corrupt_payload_and_reload(
        tmp_path / "claimed-result",
        setup,
        lambda project_payload: project_payload["agent_turns"][0]["planner_invocations"][0].update(
            {"result_content": "should-not-exist"}
        ),
    )


def test_load_rejects_tampered_budget_totals_and_settlement_usage(tmp_path):
    def setup_claim(storage):
        storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    corrupt_payload_and_reload(
        tmp_path / "reservation-total",
        setup_claim,
        lambda project_payload: project_payload["agent_turns"][0].update({"completion_tokens_reserved": 0}),
    )

    def setup_settled(storage):
        _project_after, claim = storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)
        storage.settle_agent_planner_invocation("p1", settle_command(claim.invocation.invocation_id), now=3.0)

    corrupt_payload_and_reload(
        tmp_path / "usage-total",
        setup_settled,
        lambda project_payload: project_payload["agent_turns"][0].update({"completion_tokens_used": 0}),
    )
    corrupt_payload_and_reload(
        tmp_path / "settlement-content",
        setup_settled,
        lambda project_payload: project_payload["agent_turns"][0]["planner_invocations"][0].update({"result_content": ""}),
    )


def test_settle_command_rejects_empty_success_and_failed_content():
    with pytest.raises(ValueError):
        settle_command("turn-1:iteration:1", result_content="")
    with pytest.raises(ValueError):
        settle_command("turn-1:iteration:1", outcome="failed", error_code="provider_error")


def test_settle_advances_project_revision_to_prevent_same_revision_snapshot_conflict(tmp_path):
    storage = store(tmp_path, budget=128)
    project_after, claim = storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    settled_project, _ack = storage.settle_agent_planner_invocation(
        "p1",
        settle_command(claim.invocation.invocation_id),
        now=3.0,
    )

    assert project_after.revision == 1
    assert settled_project.revision == 2

def test_store_failure_before_claim_persists_no_call_authority(tmp_path, monkeypatch):
    storage = store(tmp_path, budget=128)

    def fail_write(*_args, **_kwargs):
        raise StorageError("disk full")

    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", fail_write)
    with pytest.raises(StorageError):
        storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    monkeypatch.undo()
    assert turn_of(storage).planner_invocations == []
    assert turn_of(storage).completion_tokens_reserved == 0


def test_settlement_save_failure_retains_claim_and_duplicate_claim_has_no_call_authority(tmp_path, monkeypatch):
    storage = store(tmp_path, budget=128)
    _project_after, claim = storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    def fail_write(*_args, **_kwargs):
        raise StorageError("disk full")

    monkeypatch.setattr("src.apps.atelier.storage.atomic_write_json", fail_write)
    with pytest.raises(StorageError):
        storage.settle_agent_planner_invocation("p1", settle_command(claim.invocation.invocation_id), now=3.0)

    monkeypatch.undo()
    turn = turn_of(storage)
    assert turn.planner_invocations[0].status == "claimed"
    assert turn.completion_tokens_reserved == 4
    _project_after, replay = storage.claim_agent_planner_invocation("p1", claim_command(expected_revision=99), now=4.0)
    assert replay.claimed is False


def test_generic_project_mutation_cannot_rewrite_planner_invocation_history(tmp_path):
    storage = store(tmp_path, budget=128)
    storage.claim_agent_planner_invocation("p1", claim_command(), now=2.0)

    def erase_history(project):
        project.agent_turns[0].planner_invocations = []

    with pytest.raises(StorageError):
        storage.apply_project_command("p1", expected_revision=1, command=erase_history)

    assert len(turn_of(storage).planner_invocations) == 1


def test_concurrent_claim_has_one_winner(tmp_path):
    storage = store(tmp_path, budget=128)
    command = claim_command()

    def run_claim():
        _project_after, ack = storage.claim_agent_planner_invocation("p1", command, now=2.0)
        return ack.claimed

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _idx: run_claim(), range(2)))

    assert sorted(results) == [False, True]
    turn = turn_of(storage)
    assert len(turn.planner_invocations) == 1
    assert turn.completion_tokens_reserved == 4
