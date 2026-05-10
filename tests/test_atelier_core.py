import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.apps.comic_gen.models import Script
from src.apps.comic_gen.pipeline import ComicGenPipeline


@pytest.fixture
def pipeline(tmp_path):
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


def _make_script() -> Script:
    now = time.time()
    return Script(
        id=str(uuid.uuid4()),
        title="Studio Source",
        original_text="source text",
        created_at=now,
        updated_at=now,
    )


def test_atelier_project_defaults_to_untrusted_agent_policy(pipeline):
    project = pipeline.create_atelier_project("Exploration Board", "free exploration")

    assert project.id in pipeline.atelier_projects
    assert project.title == "Exploration Board"
    assert project.nodes == []
    assert project.agent_policy.approval_mode == "untrusted"
    assert project.agent_policy.allowed_tools == []
    assert project.agent_policy.max_nodes_per_action == 8


def test_atelier_project_can_reference_existing_studio_project(pipeline):
    script = _make_script()
    pipeline.scripts[script.id] = script

    project = pipeline.create_atelier_project("Seeded Board", source_project_id=script.id)

    assert project.source_project_id == script.id


def test_atelier_project_rejects_missing_source_project(pipeline):
    with pytest.raises(ValueError, match="Source project not found"):
        pipeline.create_atelier_project("Broken Board", source_project_id="missing")


def test_atelier_nodes_hold_shared_core_references(pipeline):
    script = _make_script()
    pipeline.scripts[script.id] = script
    project = pipeline.create_atelier_project("Board", source_project_id=script.id)

    node = pipeline.create_atelier_node(
        project.id,
        {
            "type": "video",
            "title": "Branch A",
            "prompt": "A neon alley chase",
            "x": 120,
            "y": 240,
            "source_project_id": script.id,
            "frame_id": "frame-1",
            "asset_id": "asset-1",
            "video_task_id": "video-task-1",
            "media_urls": ["output/video/branch-a.mp4"],
            "data": {"model": "wan2.7-r2v"},
            "created_by": "agent",
        },
    )

    assert node.project_id == project.id
    assert node.type == "video"
    assert node.source_project_id == script.id
    assert node.video_task_id == "video-task-1"
    assert node.media_urls == ["output/video/branch-a.mp4"]
    assert project.nodes == [node]


def test_atelier_agent_policy_updates_like_codex_approval_modes(pipeline):
    project = pipeline.create_atelier_project("Board")

    updated = pipeline.update_atelier_agent_policy(
        project.id,
        {
            "approval_mode": "never",
            "allowed_tools": ["canvas.createNode", "video.createTask"],
            "max_nodes_per_action": 12,
        },
    )

    assert updated.agent_policy.approval_mode == "never"
    assert updated.agent_policy.allowed_tools == ["canvas.createNode", "video.createTask"]
    assert updated.agent_policy.max_nodes_per_action == 12


def test_atelier_node_updates_and_deletes(pipeline):
    project = pipeline.create_atelier_project("Board")
    node = pipeline.create_atelier_node(project.id, {"title": "Old", "x": 0})

    updated = pipeline.update_atelier_node(project.id, node.id, {"title": "New", "x": 48})
    assert updated.title == "New"
    assert updated.x == 48

    pipeline.delete_atelier_node(project.id, node.id)
    assert project.nodes == []


def test_atelier_projects_persist_to_json(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "on_request"})
    pipeline.create_atelier_node(project.id, {"type": "idea", "title": "Opening"})

    loaded = pipeline._load_atelier_data()

    assert project.id in loaded
    assert loaded[project.id].agent_policy.approval_mode == "on_request"
    assert loaded[project.id].nodes[0].title == "Opening"


def test_atelier_video_candidates_are_queued_on_node(pipeline):
    project = pipeline.create_atelier_project("Board")
    node = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})

    updated = pipeline.create_atelier_video_candidates(
        project_id=project.id,
        node_id=node.id,
        prompt="A cinematic rooftop reveal",
        model="wan2.7-i2v",
        reference_image_urls=["uploads/ref.png"],
        batch_size=3,
        params={"duration": 5, "resolution": "720p"},
    )

    assert updated.status == "processing"
    assert updated.prompt == "A cinematic rooftop reveal"
    assert updated.data["generation"]["model"] == "wan2.7-i2v"
    assert updated.data["reference_image_urls"] == ["uploads/ref.png"]
    assert len(updated.data["candidates"]) == 3
    assert {candidate["status"] for candidate in updated.data["candidates"]} == {"pending"}
    assert updated.data["candidates"][0]["attempt_count"] == 0
    assert updated.data["candidates"][0]["generation_snapshot"]["params"] == {"duration": 5, "resolution": "720p"}


def test_atelier_video_candidate_selection_and_deletion(pipeline):
    project = pipeline.create_atelier_project("Board")
    node = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    updated = pipeline.create_atelier_video_candidates(
        project_id=project.id,
        node_id=node.id,
        prompt="A cinematic rooftop reveal",
        model="wan2.7-i2v",
        reference_image_urls=["uploads/ref.png"],
        batch_size=2,
        params={},
    )
    selected_id = updated.data["candidates"][0]["id"]
    discarded_id = updated.data["candidates"][1]["id"]
    updated.data["candidates"][0]["status"] = "completed"
    updated.data["candidates"][0]["video_url"] = "video/selected.mp4"

    selected = pipeline.select_atelier_video_candidate(project.id, node.id, selected_id)

    assert selected.status == "completed"
    assert selected.video_task_id == selected_id
    assert selected.media_urls == ["video/selected.mp4"]
    assert selected.data["selected_candidate_id"] == selected_id

    after_delete = pipeline.delete_atelier_video_candidate(project.id, node.id, discarded_id)

    assert [candidate["id"] for candidate in after_delete.data["candidates"]] == [selected_id]


def test_atelier_video_candidate_retry_resets_failed_candidate(pipeline):
    project = pipeline.create_atelier_project("Board")
    node = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    updated = pipeline.create_atelier_video_candidates(
        project_id=project.id,
        node_id=node.id,
        prompt="A cinematic rooftop reveal",
        model="wan2.7-i2v",
        reference_image_urls=["uploads/ref.png"],
        batch_size=1,
        params={"duration": 5},
    )
    candidate_id = updated.data["candidates"][0]["id"]
    updated.data["candidates"][0]["status"] = "failed"
    updated.data["candidates"][0]["error"] = "provider timeout"
    updated.data["candidates"][0]["completed_at"] = time.time()
    updated.status = "failed"

    retried = pipeline.retry_atelier_video_candidate(project.id, node.id, candidate_id)
    candidate = retried.data["candidates"][0]

    assert retried.status == "processing"
    assert candidate["id"] == candidate_id
    assert candidate["status"] == "pending"
    assert candidate["video_url"] is None
    assert candidate["error"] is None
    assert candidate["retry_count"] == 1
    assert candidate["generation_snapshot"]["model"] == "wan2.7-i2v"


def test_atelier_video_candidate_regenerate_replaces_current_round(pipeline):
    project = pipeline.create_atelier_project("Board")
    node = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    updated = pipeline.create_atelier_video_candidates(
        project_id=project.id,
        node_id=node.id,
        prompt="A cinematic rooftop reveal",
        model="wan2.7-i2v",
        reference_image_urls=["uploads/ref.png"],
        batch_size=2,
        params={"duration": 5},
    )
    old_candidate_id = updated.data["candidates"][0]["id"]
    updated.data["candidates"][0]["status"] = "completed"
    updated.data["candidates"][0]["video_url"] = "video/selected.mp4"
    pipeline.select_atelier_video_candidate(project.id, node.id, old_candidate_id)

    regenerated = pipeline.regenerate_atelier_video_candidates(
        project.id,
        node.id,
        batch_size=1,
        params={"duration": 8, "resolution": "1080p"},
    )

    assert regenerated.status == "processing"
    assert regenerated.video_task_id is None
    assert regenerated.media_urls == []
    assert regenerated.data["selected_candidate_id"] is None
    assert len(regenerated.data["candidates"]) == 1
    assert regenerated.data["candidates"][0]["id"] != old_candidate_id
    assert regenerated.data["candidates"][0]["params"] == {"duration": 8, "resolution": "1080p"}


def test_atelier_video_candidate_processing_records_runtime_snapshot(pipeline):
    project = pipeline.create_atelier_project("Board")
    node = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    updated = pipeline.create_atelier_video_candidates(
        project_id=project.id,
        node_id=node.id,
        prompt="A cinematic rooftop reveal",
        model="wan2.7-i2v",
        reference_image_urls=["uploads/ref.png"],
        batch_size=1,
        params={"duration": 5, "resolution": "720p"},
    )
    candidate_id = updated.data["candidates"][0]["id"]
    pipeline._download_temp_image = MagicMock(return_value=None)

    pipeline.process_atelier_video_candidate(project.id, node.id, candidate_id)
    candidate = node.data["candidates"][0]

    assert candidate["status"] == "completed"
    assert candidate["attempt_count"] == 1
    assert candidate["runtime_snapshot"]["model"] == "wan2.7-i2v"
    assert candidate["runtime_snapshot"]["params"] == {"duration": 5, "resolution": "720p"}
    assert candidate["attempts"][0]["status"] == "completed"
    assert candidate["attempts"][0]["video_url"].startswith("video/atelier_")


def test_atelier_candidate_generation_requires_reference_images(pipeline):
    project = pipeline.create_atelier_project("Board")
    node = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})

    with pytest.raises(ValueError, match="reference image"):
        pipeline.create_atelier_video_candidates(
            project_id=project.id,
            node_id=node.id,
            prompt="A cinematic rooftop reveal",
            model="wan2.7-i2v",
            reference_image_urls=[],
            batch_size=1,
            params={},
        )


def test_atelier_agent_lists_bounded_canvas_tools(pipeline):
    project = pipeline.create_atelier_project("Board")

    tools = pipeline.list_atelier_agent_tools()

    tool_names = {tool["name"] for tool in tools}
    assert "canvas.readProject" in tool_names
    assert "canvas.createVideoNode" in tool_names
    assert "generation.createVideoCandidates" in tool_names
    assert pipeline.get_atelier_project(project.id).agent_turns == []


def test_atelier_agent_untrusted_requires_approval_before_mutation(pipeline):
    project = pipeline.create_atelier_project("Board")

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    assert turn.status == "waiting_approval"
    assert turn.tool_calls[0].status == "approval_required"
    assert turn.tool_calls[0].approval_required is True
    assert project.nodes == []
    assert project.agent_turns[-1].id == turn.id


def test_atelier_agent_approved_untrusted_call_mutates_canvas(pipeline):
    project = pipeline.create_atelier_project("Board")

    pending_turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )
    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
        approve=True,
        turn_id=pending_turn.id,
    )

    assert turn.status == "completed"
    assert turn.tool_calls[0].status == "completed"
    assert turn.tool_calls[0].approval_granted is True
    assert len(project.nodes) == 1
    assert project.nodes[0].created_by == "agent"
    assert project.nodes[0].prompt == "A chase"
    assert len(project.agent_turns) == 1
    assert project.agent_turns[0].status == "completed"
    assert project.agent_turns[0].id == pending_turn.id


def test_atelier_agent_approval_requires_turn_id(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    with pytest.raises(ValueError, match="turn_id is required"):
        pipeline.run_atelier_agent_turn(
            project.id,
            [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
            approve=True,
        )

    assert project.nodes == []
    assert len(project.agent_turns) == 1
    assert project.agent_turns[0].status == "waiting_approval"


def test_atelier_agent_pending_turn_blocks_new_turns_until_resolved(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    with pytest.raises(ValueError, match="Resolve the pending Atelier agent turn"):
        pipeline.run_atelier_agent_turn(
            project.id,
            [{"tool_name": "canvas.readProject", "arguments": {}}],
            preview=True,
        )

    assert project.nodes == []
    assert len(project.agent_turns) == 1


def test_atelier_agent_approval_rejects_mismatched_tool_calls(pipeline):
    project = pipeline.create_atelier_project("Board")
    pending_turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    with pytest.raises(ValueError, match="do not match"):
        pipeline.run_atelier_agent_turn(
            project.id,
            [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Different", "prompt": "Different"}}],
            approve=True,
            turn_id=pending_turn.id,
        )

    assert project.nodes == []
    assert len(project.agent_turns) == 1
    assert project.agent_turns[0].status == "waiting_approval"


def test_atelier_agent_approval_rejects_empty_tool_call_payload(pipeline):
    project = pipeline.create_atelier_project("Board")
    pending_turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    with pytest.raises(ValueError, match="do not match"):
        pipeline.run_atelier_agent_turn(
            project.id,
            [],
            approve=True,
            turn_id=pending_turn.id,
        )

    assert project.nodes == []
    assert len(project.agent_turns) == 1
    assert project.agent_turns[0].status == "waiting_approval"


def test_atelier_agent_approval_rejects_preview_mode(pipeline):
    project = pipeline.create_atelier_project("Board")
    pending_turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    with pytest.raises(ValueError, match="preview mode"):
        pipeline.run_atelier_agent_turn(
            project.id,
            [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
            approve=True,
            preview=True,
            turn_id=pending_turn.id,
        )

    assert project.nodes == []
    assert project.agent_turns[0].status == "waiting_approval"


def test_atelier_agent_denies_pending_approval_without_mutation(pipeline):
    project = pipeline.create_atelier_project("Board")
    pending_turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [],
        deny=True,
        turn_id=pending_turn.id,
    )

    assert turn.id == pending_turn.id
    assert turn.status == "failed"
    assert turn.tool_calls[0].status == "denied"
    assert turn.tool_calls[0].approval_granted is False
    assert "User denied approval" in turn.tool_calls[0].error
    assert pipeline.get_atelier_project(project.id).nodes == []


def test_atelier_agent_denial_requires_turn_id(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    with pytest.raises(ValueError, match="turn_id is required"):
        pipeline.run_atelier_agent_turn(project.id, [], deny=True)


def test_atelier_agent_cannot_approve_and_deny_same_turn(pipeline):
    project = pipeline.create_atelier_project("Board")
    pending_turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    with pytest.raises(ValueError, match="Cannot approve and deny"):
        pipeline.run_atelier_agent_turn(
            project.id,
            [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
            approve=True,
            deny=True,
            turn_id=pending_turn.id,
        )


def test_atelier_agent_approval_preserves_pending_call_identity(pipeline):
    project = pipeline.create_atelier_project("Board")
    pending_turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )
    pending_call_id = pending_turn.tool_calls[0].call_id
    pending_call_created_at = pending_turn.tool_calls[0].created_at

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
        approve=True,
        turn_id=pending_turn.id,
    )

    assert turn.tool_calls[0].call_id == pending_call_id
    assert turn.tool_calls[0].created_at == pending_call_created_at
    assert turn.tool_calls[0].status == "completed"
    assert turn.tool_calls[0].approval_granted is True
    assert len(project.nodes) == 1


def test_atelier_agent_never_executes_without_preapproval(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    assert turn.status == "completed"
    assert turn.tool_calls[0].status == "completed"
    assert len(project.nodes) == 1


def test_atelier_agent_allowed_tools_hard_denies_disallowed_tool_even_in_never(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(
        project.id,
        {"approval_mode": "never", "allowed_tools": ["canvas.readProject"]},
    )

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    assert turn.status == "completed"
    assert turn.tool_calls[0].status == "denied"
    assert "not allowed" in turn.tool_calls[0].error
    assert project.nodes == []


def test_atelier_agent_max_nodes_per_action_hard_denies_over_budget_turn(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never", "max_nodes_per_action": 1})

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [
            {"tool_name": "canvas.createVideoNode", "arguments": {"title": "A", "prompt": "A"}},
            {"tool_name": "canvas.createVideoNode", "arguments": {"title": "B", "prompt": "B"}},
        ],
    )

    assert turn.tool_calls[0].status == "completed"
    assert turn.tool_calls[1].status == "denied"
    assert "max_nodes_per_action" in turn.tool_calls[1].error
    assert [node.title for node in project.nodes] == ["A"]


def test_atelier_agent_preview_does_not_mutate_canvas(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
        preview=True,
    )

    assert turn.status == "completed"
    assert turn.tool_calls[0].status == "proposed"
    assert project.nodes == []


def test_atelier_agent_planner_creates_video_node_plan_from_freeform_intent(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "A rain-soaked rooftop chase",
        planner="deterministic_core",
    )

    assert plan.status == "ready"
    assert plan.planner == "deterministic_core"
    assert plan.skill_name == "idea-to-canvas"
    assert plan.reason == "Create a draft video node from the user intent."
    assert plan.tool_calls == [
        {
            "tool_name": "canvas.createVideoNode",
            "arguments": {
                "title": "A rain-soaked rooftop chase",
                "prompt": "A rain-soaked rooftop chase",
                "model": "wan2.7-i2v",
                "x": 160,
                "y": 160,
            },
        }
    ]


def test_atelier_agent_planner_blocks_unknown_planner_without_tool_calls(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "Create a moonlit chase shot",
        planner="model_planner_vNext",
        planner_input={
            "schema_version": "atelier.agent.planner.v1",
            "adapter_name": "unit-test-adapter",
            "raw_provider_payload": {"secret": "do-not-echo"},
        },
    )

    assert plan.status == "blocked"
    assert plan.planner == "model_planner_vNext"
    assert plan.tool_calls == []
    assert plan.context.planner_input["adapter_name"] == "unit-test-adapter"
    assert "raw_provider_payload" not in plan.context.planner_input
    assert "Unknown Atelier agent planner" in plan.reason
    assert project.nodes == []


def test_atelier_agent_model_adapter_planner_accepts_validated_tool_calls(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "Create a moonlit chase shot",
        planner="model_adapter",
        planner_input={
            "schema_version": "atelier.agent.planner.v1",
            "adapter_name": "unit-test-adapter",
            "tool_schema_version": "atelier.tools.v1",
            "model_trace_id": "trace-1",
            "skill_name": "idea-to-canvas",
            "raw_provider_payload": {"secret": "do-not-echo"},
            "tool_calls": [
                {
                    "tool_name": "canvas.createVideoNode",
                    "arguments": {
                        "title": "Moonlit chase",
                        "prompt": "A moonlit chase across rooftops",
                    },
                }
            ],
        },
    )

    assert plan.status == "ready"
    assert plan.planner == "model_adapter"
    assert plan.skill_name == "idea-to-canvas"
    assert plan.reason == "Model planner produced a validated Atelier tool-call plan."
    assert plan.context.planner_input["skill_name"] == "idea-to-canvas"
    assert "raw_provider_payload" not in plan.context.planner_input
    assert plan.context.planner_schema_version == "atelier.agent.planner.v1"
    assert plan.context.planner_adapter_name == "unit-test-adapter"
    assert plan.context.tool_schema_version == "atelier.tools.v1"
    assert plan.context.model_trace_id == "trace-1"
    assert plan.tool_calls == [
        {
            "tool_name": "canvas.createVideoNode",
            "arguments": {
                "title": "Moonlit chase",
                "prompt": "A moonlit chase across rooftops",
            },
        }
    ]
    assert project.nodes == []


def test_atelier_agent_planner_package_includes_core_snapshot_and_tool_schemas(pipeline):
    project = pipeline.create_atelier_project("Board")
    video = pipeline.create_atelier_node(
        project.id,
        {
            "type": "video",
            "title": "Shot",
            "prompt": "A rooftop chase",
            "data": {"model": "wan2.7-i2v"},
        },
    )

    package = pipeline.build_atelier_agent_planner_package(
        project.id,
        user_message="Adapt the shot for a reference image",
        selected_node_id=video.id,
        skill_name="idea-to-canvas",
    )

    assert package.project_id == project.id
    assert package.planner_schema_version == "atelier.agent.planner.v1"
    assert package.tool_schema_version == "atelier.tools.v1"
    assert package.output_contract["schema_version"] == "atelier.agent.planner.v1"
    assert package.output_contract["tool_schema_version"] == "atelier.tools.v1"
    assert package.project_snapshot["node_count"] == 1
    assert package.selected_node_snapshot["id"] == video.id
    assert package.selected_node_snapshot["prompt"] == "A rooftop chase"
    assert any(tool["name"] == "canvas.createVideoNode" for tool in package.tool_schemas)
    assert package.policy_snapshot["approval_mode"] == project.agent_policy.approval_mode.value
    assert project.nodes[0].title == "Shot"


def test_atelier_agent_model_adapter_planner_blocks_unsupported_schema_version(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "Create a moonlit chase shot",
        planner="model_adapter",
        planner_input={
            "schema_version": "atelier.agent.planner.v0",
            "tool_calls": [
                {
                    "tool_name": "canvas.createVideoNode",
                    "arguments": {"prompt": "A moonlit chase"},
                }
            ],
        },
    )

    assert plan.status == "blocked"
    assert plan.planner == "model_adapter"
    assert plan.tool_calls == []
    assert "Unsupported model planner schema_version" in plan.reason
    assert project.nodes == []


def test_atelier_agent_model_adapter_planner_blocks_unknown_tools(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "Delete everything",
        planner="model_adapter",
        planner_input={
            "tool_calls": [
                {
                    "tool_name": "canvas.deleteProject",
                    "arguments": {"project_id": project.id},
                }
            ],
        },
    )

    assert plan.status == "blocked"
    assert plan.planner == "model_adapter"
    assert plan.tool_calls == []
    assert "unknown Atelier tool" in plan.reason
    assert project.nodes == []


def test_atelier_agent_planner_updates_selected_video_prompt(pipeline):
    project = pipeline.create_atelier_project("Board")
    video = pipeline.create_atelier_node(
        project.id,
        {"type": "video", "title": "Shot", "data": {"model": "happyhorse-1.0-i2v"}},
    )

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "Make the character turn toward camera",
        selected_node_id=video.id,
    )

    assert plan.status == "ready"
    assert plan.skill_name == "shot-variant-maker"
    assert plan.tool_calls[0] == {
        "tool_name": "canvas.updateNodePrompt",
        "arguments": {
            "node_id": video.id,
            "prompt": "Make the character turn toward camera",
            "model": "happyhorse-1.0-i2v",
        },
    }


def test_atelier_agent_planner_blocks_generation_without_selected_reference_video_node(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(project.id, "生成 3 个候选视频")

    assert plan.status == "blocked"
    assert plan.tool_calls == []
    assert "selected video node" in plan.reason


def test_atelier_agent_planner_generates_candidates_for_selected_referenced_video(pipeline):
    project = pipeline.create_atelier_project("Board")
    video = pipeline.create_atelier_node(
        project.id,
        {
            "type": "video",
            "title": "Shot",
            "prompt": "A rooftop chase",
            "data": {
                "model": "wan2.7-i2v",
                "reference_image_urls": ["uploads/ref-a.png"],
            },
        },
    )

    plan = pipeline.plan_atelier_agent_turn(project.id, "生成 3 个候选视频", selected_node_id=video.id)

    assert plan.status == "ready"
    assert plan.skill_name == "candidate-brief"
    assert plan.tool_calls[0]["tool_name"] == "generation.createVideoCandidates"
    assert plan.tool_calls[0]["arguments"]["node_id"] == video.id
    assert plan.tool_calls[0]["arguments"]["prompt"] == "A rooftop chase"
    assert plan.tool_calls[0]["arguments"]["reference_image_urls"] == ["uploads/ref-a.png"]


def test_atelier_agent_generation_requires_reference_images(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})
    node = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{
            "tool_name": "generation.createVideoCandidates",
            "arguments": {
                "node_id": node.id,
                "prompt": "A cinematic rooftop reveal",
                "model": "wan2.7-i2v",
                "reference_image_urls": [],
                "batch_size": 1,
            },
        }],
    )

    assert turn.status == "failed"
    assert turn.tool_calls[0].status == "failed"
    assert "reference image" in turn.tool_calls[0].error


def test_atelier_agent_attach_reference_node_updates_both_nodes(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})
    video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    image = pipeline.create_atelier_node(
        project.id,
        {"type": "image", "title": "Ref", "media_urls": ["uploads/ref.png"]},
    )

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{
            "tool_name": "canvas.attachReferenceNode",
            "arguments": {"video_node_id": video.id, "image_node_id": image.id},
        }],
    )

    assert turn.status == "completed"
    assert video.data["reference_image_urls"] == ["uploads/ref.png"]
    assert video.data["reference_node_ids"] == [image.id]
    assert image.data["parent_node_id"] == video.id


def test_atelier_agent_rejects_cross_video_reference_attachment(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})
    first_video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "First"})
    second_video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Second"})
    image = pipeline.create_atelier_node(
        project.id,
        {
            "type": "image",
            "title": "Ref",
            "media_urls": ["uploads/ref.png"],
            "data": {"parent_node_id": first_video.id},
        },
    )

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{
            "tool_name": "canvas.attachReferenceNode",
            "arguments": {"video_node_id": second_video.id, "image_node_id": image.id},
        }],
    )

    assert turn.status == "failed"
    assert turn.tool_calls[0].status == "failed"
    assert "already attached" in turn.tool_calls[0].error


def test_atelier_agent_r2v_generation_uses_model_specific_reference_validation(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})
    node = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{
            "tool_name": "generation.createVideoCandidates",
            "arguments": {
                "node_id": node.id,
                "prompt": "A cinematic rooftop reveal",
                "model": "wan2.7-r2v",
                "reference_image_urls": ["uploads/ref.png"],
                "batch_size": 1,
                "params": {"generation_mode": "r2v"},
            },
        }],
    )

    assert turn.status == "failed"
    assert turn.tool_calls[0].status == "failed"
    assert "reference_video_urls" in turn.tool_calls[0].error
