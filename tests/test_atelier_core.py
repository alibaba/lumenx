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


def test_atelier_attach_reference_from_image_node_appends_url(pipeline):
    """Baseline / v0.6.x behaviour: dragging an image node onto a draft
    attaches its first media_url. The new pipeline helper must keep this
    path intact (no functional regression for the existing image-ref drop)."""
    project = pipeline.create_atelier_project("Board")
    target = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Draft A"})
    image = pipeline.create_atelier_node(
        project.id,
        {"type": "image", "title": "Ref", "media_urls": ["uploads/ref.png"]},
    )

    updated_target, updated_source = pipeline.attach_atelier_reference(
        project_id=project.id,
        target_node_id=target.id,
        source_node_id=image.id,
    )

    assert updated_target.data["reference_image_urls"] == ["uploads/ref.png"]
    assert updated_target.data["reference_node_ids"] == [image.id]
    assert updated_source.data["parent_node_id"] == target.id
    assert updated_source.data["reference_role"] == "video_reference_image"


def test_atelier_attach_reference_from_draft_uses_selected_take(pipeline):
    """v0.7 item H — dragging from a draft video with a selected completed
    take should attach that take's video_url to the target draft. The
    bucket name stays `reference_image_urls` (image-historical) but the
    shell infers per-URL kind at render time."""
    project = pipeline.create_atelier_project("Board")
    target = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Target Draft"})
    source = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Source Draft"})

    pipeline.create_atelier_video_candidates(
        project_id=project.id,
        node_id=source.id,
        prompt="A neon alley chase",
        model="wan2.7-i2v",
        reference_image_urls=["uploads/seed.png"],
        batch_size=2,
        params={"duration": 5},
    )
    candidates = source.data["candidates"]
    failed_cand_id = candidates[0]["id"]
    completed_cand_id = candidates[1]["id"]
    candidates[0]["status"] = "failed"
    candidates[0]["error"] = "provider timeout"
    candidates[1]["status"] = "completed"
    candidates[1]["video_url"] = "videos/atelier_completed_take.mp4"
    source.data["selected_candidate_id"] = completed_cand_id

    updated_target, updated_source = pipeline.attach_atelier_reference(
        project_id=project.id,
        target_node_id=target.id,
        source_node_id=source.id,
    )

    assert updated_target.data["reference_image_urls"] == [
        "videos/atelier_completed_take.mp4",
    ]
    assert updated_target.data["reference_node_ids"] == [source.id]
    assert updated_source.data["parent_node_id"] == target.id
    assert updated_source.data["reference_role"] == "video_reference_image"
    # The failed candidate must never be picked even by id collision.
    assert failed_cand_id != completed_cand_id


def test_atelier_attach_reference_from_draft_falls_back_to_first_completed(pipeline):
    """No selected_candidate_id → first completed candidate with a
    video_url wins; failed / pending candidates are skipped."""
    project = pipeline.create_atelier_project("Board")
    target = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Target"})
    source = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Source"})

    pipeline.create_atelier_video_candidates(
        project_id=project.id,
        node_id=source.id,
        prompt="A foggy harbour at dawn",
        model="wan2.7-i2v",
        reference_image_urls=["uploads/seed.png"],
        batch_size=3,
        params={},
    )
    cands = source.data["candidates"]
    cands[0]["status"] = "failed"
    cands[0]["error"] = "boom"
    cands[1]["status"] = "completed"
    cands[1]["video_url"] = "videos/first_completed.mp4"
    cands[2]["status"] = "completed"
    cands[2]["video_url"] = "videos/second_completed.mp4"
    # no selected_candidate_id

    updated_target, _ = pipeline.attach_atelier_reference(
        project_id=project.id,
        target_node_id=target.id,
        source_node_id=source.id,
    )

    assert updated_target.data["reference_image_urls"] == [
        "videos/first_completed.mp4",
    ]


def test_atelier_attach_reference_from_empty_draft_raises_no_completed_take(pipeline):
    """A draft with no candidates (or only failed / pending ones) must
    raise a `no completed take` ValueError. The API layer maps this to
    HTTP 422 so the frontend can surface the right toast."""
    project = pipeline.create_atelier_project("Board")
    target = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Target"})
    empty_source = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Empty"})

    with pytest.raises(ValueError, match="no completed take"):
        pipeline.attach_atelier_reference(
            project_id=project.id,
            target_node_id=target.id,
            source_node_id=empty_source.id,
        )

    # Same outcome when the only candidate is still pending / failed.
    pipeline.create_atelier_video_candidates(
        project_id=project.id,
        node_id=empty_source.id,
        prompt="A storm gathers",
        model="wan2.7-i2v",
        reference_image_urls=["uploads/seed.png"],
        batch_size=1,
        params={},
    )
    empty_source.data["candidates"][0]["status"] = "failed"
    with pytest.raises(ValueError, match="no completed take"):
        pipeline.attach_atelier_reference(
            project_id=project.id,
            target_node_id=target.id,
            source_node_id=empty_source.id,
        )


def test_atelier_attach_reference_skips_completed_candidate_without_video_url(pipeline):
    """A candidate marked completed but missing video_url is unusable as
    a reference. The helper must skip it and raise rather than attach a
    dangling pointer."""
    project = pipeline.create_atelier_project("Board")
    target = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Target"})
    source = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Source"})

    pipeline.create_atelier_video_candidates(
        project_id=project.id,
        node_id=source.id,
        prompt="A neon alley chase",
        model="wan2.7-i2v",
        reference_image_urls=["uploads/seed.png"],
        batch_size=1,
        params={},
    )
    source.data["candidates"][0]["status"] = "completed"
    source.data["candidates"][0]["video_url"] = None

    with pytest.raises(ValueError, match="no completed take"):
        pipeline.attach_atelier_reference(
            project_id=project.id,
            target_node_id=target.id,
            source_node_id=source.id,
        )


def test_atelier_attach_reference_dedupes_repeat_attaches(pipeline):
    """Re-attaching the same source must be a no-op rather than a
    duplicate (matches the v0.6.x N:M behaviour in
    `_execute_attach_reference_node`)."""
    project = pipeline.create_atelier_project("Board")
    target = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Target"})
    image = pipeline.create_atelier_node(
        project.id,
        {"type": "image", "title": "Ref", "media_urls": ["uploads/ref.png"]},
    )

    pipeline.attach_atelier_reference(project.id, target.id, image.id)
    updated_target, _ = pipeline.attach_atelier_reference(project.id, target.id, image.id)

    assert updated_target.data["reference_image_urls"] == ["uploads/ref.png"]
    assert updated_target.data["reference_node_ids"] == [image.id]


def test_atelier_attach_reference_rejects_non_video_target(pipeline):
    """The drop target must be a video / draft node; attaching onto an
    idea / image / audio node is not a supported gesture."""
    project = pipeline.create_atelier_project("Board")
    image_target = pipeline.create_atelier_node(
        project.id,
        {"type": "image", "title": "Img", "media_urls": ["uploads/a.png"]},
    )
    source = pipeline.create_atelier_node(
        project.id,
        {"type": "image", "title": "Src", "media_urls": ["uploads/b.png"]},
    )

    with pytest.raises(ValueError, match="video / draft"):
        pipeline.attach_atelier_reference(project.id, image_target.id, source.id)


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


def test_atelier_agent_model_adapter_planner_calls_llm_when_no_draft_calls(
    pipeline, monkeypatch
):
    """v0.8 item L: with no pre-validated tool_calls, the model_adapter
    planner builds a planner_package + dispatches to the LLM. We monkeypatch
    the LLM seam so the test stays offline / deterministic."""
    project = pipeline.create_atelier_project("Board")

    captured: Dict[str, Any] = {}

    def fake_call(package, user_message, model=None):
        captured["package"] = package
        captured["user_message"] = user_message
        captured["model"] = model
        return {
            "ok": True,
            "response": "Sure — I'll draft a moonlit chase shot for you.",
            "tool_calls": [
                {
                    "tool_name": "canvas.createVideoNode",
                    "arguments": {
                        "title": "Moonlit chase",
                        "prompt": "A moonlit chase across rooftops",
                    },
                }
            ],
            "error": None,
        }

    monkeypatch.setattr(
        "src.apps.comic_gen.atelier_agent.call_atelier_llm_planner", fake_call
    )

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "Draft a moonlit chase shot",
        planner="model_adapter",
    )

    assert plan.status == "ready"
    assert plan.planner == "model_adapter"
    assert plan.response == "Sure — I'll draft a moonlit chase shot for you."
    assert plan.tool_calls == [
        {
            "tool_name": "canvas.createVideoNode",
            "arguments": {
                "title": "Moonlit chase",
                "prompt": "A moonlit chase across rooftops",
            },
        }
    ]
    # Planner package was built and passed to the LLM seam.
    assert captured["user_message"] == "Draft a moonlit chase shot"
    assert captured["package"].project_id == project.id
    assert any(
        tool["name"] == "canvas.createVideoNode"
        for tool in captured["package"].tool_schemas
    )

    # End-to-end: forwarding assistant_response into run_turn overrides the
    # deterministic English summary on the resulting AtelierAgentTurn.
    turn = pipeline.run_atelier_agent_turn(
        project.id,
        plan.tool_calls,
        user_message=plan.user_message,
        assistant_response=plan.response,
    )
    assert turn.response == "Sure — I'll draft a moonlit chase shot for you."


def test_atelier_agent_model_adapter_planner_blocks_when_llm_fails(
    pipeline, monkeypatch
):
    project = pipeline.create_atelier_project("Board")
    monkeypatch.setattr(
        "src.apps.comic_gen.atelier_agent.call_atelier_llm_planner",
        lambda package, user_message, model=None: {
            "ok": False,
            "response": None,
            "tool_calls": [],
            "error": "LLM call failed: simulated provider 429",
        },
    )

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "Render the next beat",
        planner="model_adapter",
    )

    assert plan.status == "blocked"
    assert plan.planner == "model_adapter"
    assert plan.tool_calls == []
    assert "simulated provider 429" in plan.reason
    assert project.nodes == []


def test_atelier_agent_model_adapter_planner_blocks_when_llm_returns_no_actions(
    pipeline, monkeypatch
):
    project = pipeline.create_atelier_project("Board")
    monkeypatch.setattr(
        "src.apps.comic_gen.atelier_agent.call_atelier_llm_planner",
        lambda package, user_message, model=None: {
            "ok": True,
            "response": "I need more context — which node should I update?",
            "tool_calls": [],
            "error": None,
        },
    )

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "Just change it",
        planner="model_adapter",
    )

    assert plan.status == "blocked"
    assert plan.tool_calls == []
    # The LLM's own explanation should ride on the plan so the UI can show it.
    assert plan.response == "I need more context — which node should I update?"
    assert plan.reason == "I need more context — which node should I update?"


def test_atelier_agent_harness_uses_assistant_response_over_summary(pipeline):
    """run_turn prefers caller-supplied assistant_response over _build_turn_response."""
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [
            {
                "tool_name": "canvas.createVideoNode",
                "arguments": {"title": "Test", "prompt": "hello"},
            }
        ],
        user_message="Make a shot",
        assistant_response="Spinning up your hero shot now.",
    )

    assert turn.status == "completed"
    assert turn.response == "Spinning up your hero shot now."

    # Without assistant_response, falls back to the deterministic summary.
    turn2 = pipeline.run_atelier_agent_turn(
        project.id,
        [
            {
                "tool_name": "canvas.createVideoNode",
                "arguments": {"title": "Test 2", "prompt": "hello"},
            }
        ],
        user_message="Make another",
    )
    assert turn2.status == "completed"
    assert turn2.response is not None
    # Deterministic summary uses the "Created N video draft" phrasing.
    assert "Created" in turn2.response


def test_atelier_agent_model_adapter_llm_path_blocked_without_planner_package(pipeline):
    """If the model_adapter planner is invoked WITHOUT pre-supplied tool_calls
    AND without _planner_package (e.g. direct call to the planner bypassing
    the harness), it returns a blocked plan rather than calling the LLM with
    empty context."""
    from src.apps.comic_gen.atelier_agent import (
        ModelAdapterPlanner,
        build_default_atelier_tool_registry,
    )

    project = pipeline.create_atelier_project("Board")
    planner = ModelAdapterPlanner(build_default_atelier_tool_registry())
    plan = planner.plan(
        project=project,
        user_message="hi",
        planner_input={},
    )
    assert plan.status == "blocked"
    assert "planner_package" in plan.reason or "tool_calls" in plan.reason


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


def test_atelier_agent_allows_shared_reference_across_video_nodes(pipeline):
    """One image ref → many video drafts (N:M). The original 1:1 lock has
    been intentionally loosened to support shared character / scene refs
    (motion_study, character_ref structure plans). The image keeps its
    parent_node_id pointing at the first attacher for back-pointer
    purposes; subsequent edges live on each video's reference_node_ids."""
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

    assert turn.status == "completed"
    state = pipeline.get_atelier_project(project.id)
    second = next(n for n in state.nodes if n.id == second_video.id)
    img = next(n for n in state.nodes if n.id == image.id)
    assert image.id in (second.data or {}).get("reference_node_ids", [])
    # parent_node_id stays at the first attacher for back-pointer continuity.
    assert (img.data or {}).get("parent_node_id") == first_video.id


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


# ---------------------------------------------------------------------------
# StructurePlanner — "Director's Console" multi-step plans.
# ---------------------------------------------------------------------------


def test_atelier_structure_planner_three_shot_story_emits_three_video_drafts(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "3-shot story about a rain-soaked rooftop chase",
        planner="structure",
        planner_input={"drop_world_x": 200, "drop_world_y": 300},
    )

    assert plan.status == "ready"
    assert plan.planner == "structure"
    assert plan.skill_name == "director-story-beats"
    assert plan.reason.startswith("Structured plan: 3-beat story")
    assert len(plan.tool_calls) == 3
    titles = [call["arguments"]["title"] for call in plan.tool_calls]
    assert any("Setup" in t for t in titles)
    assert any("Turn" in t for t in titles)
    assert any("Payoff" in t for t in titles)
    assert all(call["tool_name"] == "canvas.createVideoNode" for call in plan.tool_calls)
    # Beat coords march downward at the drop anchor.
    xs = [call["arguments"]["x"] for call in plan.tool_calls]
    ys = [call["arguments"]["y"] for call in plan.tool_calls]
    assert xs == [200, 200, 200]
    assert ys[0] < ys[1] < ys[2]


def test_atelier_structure_planner_numeric_n_shot_caps_at_eight(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"max_nodes_per_action": 12})

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "10 shots from a rooftop heist",
        planner="structure",
    )

    assert plan.status == "ready"
    # Classifier caps at 8 even though the policy allows more.
    assert len(plan.tool_calls) == 8


def test_atelier_structure_planner_blocks_when_request_exceeds_policy(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"max_nodes_per_action": 2})

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "5-shot story",
        planner="structure",
    )

    assert plan.status == "blocked"
    assert "max_nodes_per_action" in plan.reason
    assert plan.tool_calls == []


def test_atelier_structure_planner_variants_default_to_four(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "give me parallel candidates of a moonlit chase",
        planner="structure",
    )

    assert plan.status == "ready"
    assert plan.skill_name == "director-variants"
    assert len(plan.tool_calls) == 4
    titles = [call["arguments"]["title"] for call in plan.tool_calls]
    assert all("v" in title for title in titles)


def test_atelier_structure_planner_motion_study_requires_image_ref(pipeline):
    project = pipeline.create_atelier_project("Board")

    # No selected node — blocked.
    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "motion study",
        planner="structure",
    )
    assert plan.status == "blocked"
    assert "image reference" in plan.reason.lower()

    # Selected image node without media — still blocked (need bytes to ref).
    image = pipeline.create_atelier_node(
        project.id,
        {"type": "image", "title": "Hero ref", "x": 80.0, "y": 80.0},
    )
    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "motion study",
        planner="structure",
        selected_node_id=image.id,
    )
    assert plan.status == "blocked"
    assert "no media" in plan.reason.lower()

    # Patch in a media url and try again — now it should succeed.
    pipeline.update_atelier_node(project.id, image.id, {"media_urls": ["uploads/hero.png"]})
    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "motion study",
        planner="structure",
        selected_node_id=image.id,
    )
    assert plan.status == "ready"
    assert plan.skill_name == "director-motion-study"
    # 4 variants by default × (createVideoNode + attachReferenceNode pair) = 8 calls.
    assert len(plan.tool_calls) == 8
    create_calls = [c for c in plan.tool_calls if c["tool_name"] == "canvas.createVideoNode"]
    attach_calls = [c for c in plan.tool_calls if c["tool_name"] == "canvas.attachReferenceNode"]
    assert len(create_calls) == 4
    assert len(attach_calls) == 4
    # Every create call binds an alias; every attach call references it.
    create_aliases = [c["arguments"]["_alias"] for c in create_calls]
    attach_alias_refs = [c["arguments"]["video_node_id_alias"] for c in attach_calls]
    assert sorted(create_aliases) == sorted(attach_alias_refs)
    # All attach calls point at the real selected image id.
    for call in attach_calls:
        assert call["arguments"]["image_node_id"] == image.id


def test_atelier_structure_planner_character_ref_creates_one_draft(pipeline):
    project = pipeline.create_atelier_project("Board")
    image = pipeline.create_atelier_node(
        project.id,
        {"type": "image", "title": "Hero", "media_urls": ["uploads/hero.png"], "x": 100.0, "y": 100.0},
    )

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "character ref → video",
        planner="structure",
        selected_node_id=image.id,
    )

    assert plan.status == "ready"
    assert plan.skill_name == "director-character-ref"
    # createVideoNode + attachReferenceNode pair (cross-call ref binding).
    assert len(plan.tool_calls) == 2
    create_call, attach_call = plan.tool_calls
    assert create_call["tool_name"] == "canvas.createVideoNode"
    assert create_call["arguments"]["title"] == "Character shot"
    assert create_call["arguments"]["_alias"] == "draft"
    assert attach_call["tool_name"] == "canvas.attachReferenceNode"
    assert attach_call["arguments"]["video_node_id_alias"] == "draft"
    assert attach_call["arguments"]["image_node_id"] == image.id


def test_atelier_structure_planner_motion_study_executes_with_real_attachments(pipeline):
    """End-to-end: build a motion-study plan, switch policy to never, run
    the plan, and verify each new draft has the original image attached
    as a reference (alias resolution happened in the harness)."""
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})
    image = pipeline.create_atelier_node(
        project.id,
        {"type": "image", "title": "Hero ref", "media_urls": ["uploads/hero.png"], "x": 0.0, "y": 0.0},
    )

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "anything",
        planner="structure",
        selected_node_id=image.id,
        planner_input={"intent_kind": "motion_study", "count": 2},
    )
    assert plan.status == "ready"
    # 2 variants × 2 calls each = 4 calls.
    assert len(plan.tool_calls) == 4

    turn = pipeline.run_atelier_agent_turn(project.id, plan.tool_calls)
    assert turn.status == "completed", turn.tool_calls
    # All four calls completed.
    statuses = [c.status for c in turn.tool_calls]
    assert statuses.count("completed") == 4

    # Each new draft now carries the image as a reference.
    project_state = pipeline.get_atelier_project(project.id)
    drafts = [n for n in project_state.nodes if n.type == "video"]
    assert len(drafts) == 2
    for draft in drafts:
        ref_ids = (draft.data or {}).get("reference_node_ids") or []
        assert image.id in ref_ids, f"draft {draft.id} missing ref {image.id}"


def test_atelier_planner_package_full_scope_when_no_pinned_nodes(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.create_atelier_node(project.id, {"type": "video", "title": "A"})
    pipeline.create_atelier_node(project.id, {"type": "video", "title": "B"})

    package = pipeline.build_atelier_agent_planner_package(project.id, user_message="hi")
    assert package.project_snapshot["scope"] == "full"
    assert len(package.project_snapshot["nodes"]) == 2


def test_atelier_planner_package_narrows_to_pinned_nodes(pipeline):
    """Selective context: when any node is agent_pinned, the snapshot
    drops down to just the pinned set + the selected node + any refs
    the kept nodes depend on. Everything else is canvas noise."""
    project = pipeline.create_atelier_project("Board")
    a = pipeline.create_atelier_node(project.id, {"type": "video", "title": "A"})
    b = pipeline.create_atelier_node(
        project.id,
        {"type": "video", "title": "B (pinned)", "data": {"agent_pinned": True}},
    )
    image = pipeline.create_atelier_node(
        project.id,
        {"type": "image", "title": "Ref", "media_urls": ["uploads/ref.png"]},
    )
    # B references the image — that ref should be kept too.
    pipeline.update_atelier_node(
        project.id,
        b.id,
        {"data": {**(b.data or {}), "reference_node_ids": [image.id]}},
    )
    # A noise node not pinned, not selected, not referenced — should drop.
    pipeline.create_atelier_node(project.id, {"type": "video", "title": "C"})

    package = pipeline.build_atelier_agent_planner_package(
        project.id,
        user_message="advance B",
        selected_node_id=a.id,  # selected stays in scope too
    )
    assert package.project_snapshot["scope"] == "pinned"
    kept_ids = {n["id"] for n in package.project_snapshot["nodes"]}
    # B (pinned), A (selected), image (ref of B) — but NOT the noise C.
    assert b.id in kept_ids
    assert a.id in kept_ids
    assert image.id in kept_ids
    assert len(kept_ids) == 3


def test_atelier_structure_planner_unresolved_alias_fails_cleanly(pipeline):
    """If a consumer alias references something that wasn't bound (because
    its producer call failed or was skipped), the harness must fail the
    consumer with a clean reason rather than passing the literal alias
    string to the executor."""
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [
            {
                "tool_name": "canvas.attachReferenceNode",
                "arguments": {
                    "video_node_id_alias": "ghost",
                    "image_node_id": "also-ghost",
                },
            },
        ],
    )

    assert turn.status == "failed"
    assert turn.tool_calls[0].status == "failed"
    assert "Unresolved planner aliases" in (turn.tool_calls[0].error or "")
    assert "ghost" in (turn.tool_calls[0].error or "")


def test_atelier_structure_planner_blocks_unrecognized_intent(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "do the thing with the stuff",
        planner="structure",
    )

    assert plan.status == "blocked"
    assert "structure pattern" in plan.reason.lower()
    assert plan.tool_calls == []


def test_atelier_orphan_candidate_marked_failed_on_recover(pipeline, tmp_path):
    """The user reports: video generation stuck on 排队中. Often it's
    backend restart eating the in-memory BG task. Recovery sweeps the
    persisted state on boot and marks orphan candidates failed so the
    UI shows a Retry affordance instead of an eternal spinner."""
    # Build a project with a stuck pending candidate persisted to disk.
    project = pipeline.create_atelier_project("Board")
    video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    pipeline.update_atelier_node(project.id, video.id, {
        "data": {
            "candidates": [
                {"id": "c1", "status": "pending", "video_url": None},
                {"id": "c2", "status": "processing", "video_url": None},
                {"id": "c3", "status": "completed", "video_url": "videos/x.mp4"},
            ],
        },
    })

    # Force a fresh ComicGenPipeline that re-reads from disk and runs
    # _recover_orphan_tasks in __init__.
    from unittest.mock import patch
    from src.apps.comic_gen.pipeline import ComicGenPipeline
    with patch("src.apps.comic_gen.pipeline.ScriptProcessor"), \
         patch("src.apps.comic_gen.pipeline.AssetGenerator"), \
         patch("src.apps.comic_gen.pipeline.StoryboardGenerator"), \
         patch("src.apps.comic_gen.pipeline.VideoGenerator"), \
         patch("src.apps.comic_gen.pipeline.AudioGenerator"), \
         patch("src.apps.comic_gen.pipeline.ExportManager"):
        # Build a new pipeline that loads from the same on-disk state.
        fresh = ComicGenPipeline()
        fresh.atelier_data_file = pipeline.atelier_data_file
        fresh.data_file = pipeline.data_file
        fresh.series_data_file = pipeline.series_data_file
        # Re-load + recover.
        fresh.atelier_projects = fresh._load_atelier_data()
        fresh.scripts = fresh._load_data()
        fresh.series_store = fresh._load_series_data()
        fresh._recover_orphan_tasks()

    state = fresh.get_atelier_project(project.id)
    candidates = (state.nodes[0].data or {}).get("candidates") or []
    by_id = {c["id"]: c for c in candidates}
    assert by_id["c1"]["status"] == "failed"
    assert by_id["c2"]["status"] == "failed"
    assert "Backend was restarted" in by_id["c1"]["error"]
    # Completed candidate untouched.
    assert by_id["c3"]["status"] == "completed"
    assert "error" not in by_id["c3"] or not by_id["c3"].get("error")


def test_atelier_mark_candidate_failed_writes_status_and_error(pipeline):
    """T1.4 #2: belt-and-suspenders writeback. mark_atelier_candidate_failed
    flips status + writes error so the UI never sees an eternal spinner."""
    project = pipeline.create_atelier_project("Board")
    video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    pipeline.update_atelier_node(project.id, video.id, {
        "data": {"candidates": [{"id": "c1", "status": "processing"}]},
    })

    ok = pipeline.mark_atelier_candidate_failed(
        project.id, video.id, "c1", "Background error: boom"
    )
    assert ok
    state = pipeline.get_atelier_project(project.id)
    cand = ((state.nodes[0].data or {}).get("candidates") or [])[0]
    assert cand["status"] == "failed"
    assert cand["error"] == "Background error: boom"

    # No-op on completed candidate (don't downgrade success).
    pipeline.update_atelier_node(project.id, video.id, {
        "data": {"candidates": [{"id": "c2", "status": "completed", "video_url": "videos/c2.mp4"}]},
    })
    ok2 = pipeline.mark_atelier_candidate_failed(project.id, video.id, "c2", "spurious")
    assert ok2 is False
    state = pipeline.get_atelier_project(project.id)
    cand2 = ((state.nodes[0].data or {}).get("candidates") or [])[0]
    assert cand2["status"] == "completed"

    # Unknown candidate id returns False.
    ok3 = pipeline.mark_atelier_candidate_failed(project.id, video.id, "ghost", "x")
    assert ok3 is False


def test_atelier_sequence_persists_on_project(pipeline):
    """T2.5: replace_atelier_sequence persists the cut on the project so
    it survives device / browser changes (no longer localStorage-only)."""
    project = pipeline.create_atelier_project("Board")
    video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    pipeline.update_atelier_node(project.id, video.id, {
        "data": {
            "candidates": [
                {"id": "c1", "status": "completed", "video_url": "videos/atelier_c1.mp4"},
                {"id": "c2", "status": "completed", "video_url": "videos/atelier_c2.mp4"},
            ],
        },
    })

    saved = pipeline.replace_atelier_sequence(project.id, [
        {"parentId": video.id, "candidateId": "c1", "trimStart": 0.5, "trimEnd": 4.0},
        {"parentId": video.id, "candidateId": "c2"},
    ])
    assert len(saved.sequence) == 2
    assert saved.sequence[0].trimStart == 0.5
    assert saved.sequence[0].trimEnd == 4.0
    assert saved.sequence[1].trimStart is None
    assert saved.sequence[1].trimEnd is None

    # Round-trip: persistence + reload from disk should preserve.
    fetched = pipeline.get_atelier_project(project.id)
    assert fetched is not None
    assert [e.candidateId for e in fetched.sequence] == ["c1", "c2"]


def test_atelier_sequence_replace_rejects_unknown_candidate(pipeline):
    project = pipeline.create_atelier_project("Board")
    video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    with pytest.raises(ValueError, match="candidate"):
        pipeline.replace_atelier_sequence(project.id, [
            {"parentId": video.id, "candidateId": "ghost"},
        ])


def test_atelier_sequence_export_validates_entries(pipeline):
    """Schema validation in pipeline.export_atelier_sequence — empty list,
    missing parent, missing candidate, invalid trim ranges."""
    project = pipeline.create_atelier_project("Board")

    # Empty list
    with pytest.raises(ValueError, match="empty"):
        pipeline.export_atelier_sequence(project.id, [])

    # Unknown parent
    with pytest.raises(ValueError, match="parent node"):
        pipeline.export_atelier_sequence(project.id, [
            {"parentId": "ghost", "candidateId": "x"},
        ])

    # Real video node but no candidate yet
    video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    with pytest.raises(ValueError, match="candidate"):
        pipeline.export_atelier_sequence(project.id, [
            {"parentId": video.id, "candidateId": "x"},
        ])

    # Add a fake candidate without video_url → still errors
    pipeline.update_atelier_node(project.id, video.id, {
        "data": {**(video.data or {}), "candidates": [{"id": "c1", "status": "completed"}]},
    })
    with pytest.raises(ValueError, match="no rendered video"):
        pipeline.export_atelier_sequence(project.id, [
            {"parentId": video.id, "candidateId": "c1"},
        ])

    # Trim end ≤ start should reject before touching ffmpeg.
    pipeline.update_atelier_node(project.id, video.id, {
        "data": {
            **(video.data or {}),
            "candidates": [
                {"id": "c1", "status": "completed", "video_url": "videos/atelier_c1.mp4"},
            ],
        },
    })
    with pytest.raises(ValueError, match="trimEnd"):
        pipeline.export_atelier_sequence(project.id, [
            {"parentId": video.id, "candidateId": "c1", "trimStart": 2.0, "trimEnd": 1.0},
        ])

    # Remote URLs explicitly out-of-scope for v1.
    pipeline.update_atelier_node(project.id, video.id, {
        "data": {
            **(video.data or {}),
            "candidates": [
                {"id": "c1", "status": "completed", "video_url": "https://cdn/x.mp4"},
            ],
        },
    })
    with pytest.raises(ValueError, match="remote candidate"):
        pipeline.export_atelier_sequence(project.id, [
            {"parentId": video.id, "candidateId": "c1"},
        ])


def test_atelier_sequence_export_async_job_lifecycle(pipeline, monkeypatch):
    """Track O (v0.9): start_atelier_sequence_export_job allocates a
    job_id without executing; process_atelier_sequence_export_job runs
    the work in-process, with progress monotonically reaching 100; the
    polling DTO surfaces the terminal video_url / filename. Cross-
    project job_ids return None (caller maps to 404)."""
    project = pipeline.create_atelier_project("Async board")
    video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    pipeline.update_atelier_node(project.id, video.id, {
        "data": {
            **(video.data or {}),
            "candidates": [
                {"id": "c1", "status": "completed", "video_url": "videos/atelier_c1.mp4"},
                {"id": "c2", "status": "completed", "video_url": "videos/atelier_c2.mp4"},
            ],
        },
    })

    # Skip the actual ffmpeg invocations + treat any source path as
    # "exists". We also fake the eventual output file's size so size_mb
    # comes back as a positive number.
    monkeypatch.setattr(
        "src.apps.comic_gen.pipeline.get_ffmpeg_path",
        lambda: "/usr/bin/true",
    )
    monkeypatch.setattr(
        "src.apps.comic_gen.pipeline.os.path.exists",
        lambda _p: True,
    )
    monkeypatch.setattr(
        "src.apps.comic_gen.pipeline.os.path.getsize",
        lambda _p: 1024 * 1024,
    )
    monkeypatch.setattr(
        "src.apps.comic_gen.pipeline.subprocess.run",
        lambda *args, **kwargs: MagicMock(returncode=0, stderr=b"", stdout=b""),
    )

    # Allocation. POST handler propagates the returned id verbatim.
    job_id = pipeline.start_atelier_sequence_export_job(project.id, [
        {"parentId": video.id, "candidateId": "c1"},
        {"parentId": video.id, "candidateId": "c2"},
    ])
    assert isinstance(job_id, str) and len(job_id) > 0

    # Pre-run polling DTO reflects "pending" / 0%.
    pre = pipeline.get_atelier_export_job_status(project.id, job_id)
    assert pre is not None
    assert pre["status"] == "pending"
    assert pre["progress"] == 0

    # Cross-project lookup must return None (POST handler maps → 404).
    other_project = pipeline.create_atelier_project("Other board")
    assert pipeline.get_atelier_export_job_status(other_project.id, job_id) is None

    # Run the worker synchronously (FastAPI BackgroundTasks normally
    # invokes this after the response is sent).
    pipeline.process_atelier_sequence_export_job(job_id)

    post = pipeline.get_atelier_export_job_status(project.id, job_id)
    assert post is not None
    assert post["status"] == "completed"
    assert post["progress"] == 100
    assert post["video_url"].startswith("videos/atelier_seq_")
    assert post["filename"].endswith(".mp4")
    assert post["clip_count"] == 2
    assert post["size_mb"] == pytest.approx(1.0, abs=0.01)
    # Internal resolved cache must be dropped on terminal so it can't
    # leak into a future status payload.
    raw = pipeline.atelier_export_tasks[job_id]
    assert "_resolved" not in raw


def test_atelier_sequence_export_async_job_marks_failed_on_ffmpeg_error(pipeline, monkeypatch):
    """If the worker's ffmpeg call raises, the job lands in
    status='failed' with the error message preserved — progress sticks
    at the last bump rather than jumping to 100."""
    import subprocess as _sp

    project = pipeline.create_atelier_project("Failing board")
    video = pipeline.create_atelier_node(project.id, {"type": "video", "title": "Shot"})
    pipeline.update_atelier_node(project.id, video.id, {
        "data": {
            **(video.data or {}),
            "candidates": [
                {"id": "c1", "status": "completed", "video_url": "videos/atelier_c1.mp4"},
            ],
        },
    })

    monkeypatch.setattr(
        "src.apps.comic_gen.pipeline.get_ffmpeg_path",
        lambda: "/usr/bin/true",
    )
    monkeypatch.setattr(
        "src.apps.comic_gen.pipeline.os.path.exists",
        lambda _p: True,
    )

    def _explode(*_args, **_kwargs):
        raise _sp.CalledProcessError(returncode=1, cmd="ffmpeg", stderr=b"boom")

    monkeypatch.setattr(
        "src.apps.comic_gen.pipeline.subprocess.run",
        _explode,
    )

    job_id = pipeline.start_atelier_sequence_export_job(project.id, [
        {"parentId": video.id, "candidateId": "c1", "trimStart": 0.5, "trimEnd": 2.5},
    ])
    pipeline.process_atelier_sequence_export_job(job_id)

    status = pipeline.get_atelier_export_job_status(project.id, job_id)
    assert status is not None
    assert status["status"] == "failed"
    assert "boom" in status.get("error", "")
    assert status.get("video_url") is None


def test_atelier_structure_planner_explicit_intent_kind_overrides_message(pipeline):
    project = pipeline.create_atelier_project("Board")

    plan = pipeline.plan_atelier_agent_turn(
        project.id,
        "anything goes",
        planner="structure",
        planner_input={"intent_kind": "story_beats", "count": 5},
    )

    assert plan.status == "ready"
    assert len(plan.tool_calls) == 5


def test_atelier_agent_turn_response_summarizes_completed_actions(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [
            {"tool_name": "canvas.createVideoNode", "arguments": {"title": "A", "prompt": "A"}},
            {"tool_name": "canvas.createVideoNode", "arguments": {"title": "B", "prompt": "B"}},
        ],
    )

    assert turn.status == "completed"
    assert turn.response is not None
    assert turn.response == "Created 2 video drafts."


def test_atelier_agent_turn_response_uses_singular_for_one_action(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Solo", "prompt": "Solo"}}],
    )

    assert turn.response == "Created 1 video draft."


def test_atelier_agent_turn_response_signals_awaiting_approval(pipeline):
    project = pipeline.create_atelier_project("Board")

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    assert turn.status == "waiting_approval"
    assert turn.response is not None
    assert "Awaiting your approval" in turn.response


def test_atelier_agent_turn_response_marks_denied_actions(pipeline):
    project = pipeline.create_atelier_project("Board")
    pending = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "Shot", "prompt": "A chase"}}],
    )

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [],
        deny=True,
        turn_id=pending.id,
    )

    assert turn.status == "failed"
    assert turn.response is not None
    assert "Denied" in turn.response


def test_atelier_agent_turn_response_reports_failed_first_error(pipeline):
    project = pipeline.create_atelier_project("Board")
    pipeline.update_atelier_agent_policy(
        project.id,
        {"approval_mode": "never", "allowed_tools": ["canvas.readProject"]},
    )

    turn = pipeline.run_atelier_agent_turn(
        project.id,
        [{"tool_name": "canvas.createVideoNode", "arguments": {"title": "X", "prompt": "X"}}],
    )

    # createVideoNode is denied (not in allowed_tools), so the turn completes with denied calls.
    assert turn.status == "completed"
    assert turn.response is not None
    assert "Denied" in turn.response


# ---------------------------------------------------------------------------
# v0.9 track P — SSE streaming agent turn route.
# ---------------------------------------------------------------------------


def _parse_sse_events(body_text: str):
    """Split a captured SSE response body into a list of (event, data) tuples.

    Reused across the streaming-route tests below; intentionally minimal — no
    multi-line `data:` reassembly is needed because the helper in api.py
    always serializes a single JSON line per frame.
    """
    import json as _j

    out = []
    for block in body_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = "message"
        data_lines = []
        for line in block.split("\n"):
            line = line.rstrip("\r")
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        if not data_lines:
            continue
        try:
            payload = _j.loads("\n".join(data_lines))
        except Exception:
            payload = {"raw": "\n".join(data_lines)}
        out.append((event_name, payload))
    return out


def test_atelier_agent_stream_route_yields_deltas_and_executes(pipeline, monkeypatch):
    """v1.0 track T: POST /agent/turns/stream emits the renamed wire frames
    (turn / llm_delta / llm_done / tool_start / tool_done / turn_done) in
    order, and the final turn_done carries the persisted turn payload.

    The harness's per-call streaming generator is bridged through the
    route, so a single canvas.createVideoNode planner call surfaces as
    a tool_start / tool_done pair sandwiched between llm_done and the
    terminal turn_done.
    """
    from fastapi.testclient import TestClient
    from src.apps.comic_gen import api as api_module

    # Re-point the module-level pipeline at the test fixture so the route
    # reads/writes the same in-memory project the assertions inspect.
    monkeypatch.setattr(api_module, "pipeline", pipeline)

    project = pipeline.create_atelier_project("Stream Board")
    # never-mode policy so the executed canvas.createVideoNode tool call
    # actually completes (default untrusted would route to waiting_approval).
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    def fake_stream(package, user_message, model=None):
        # Yield several deltas that, concatenated, form the JSON payload
        # the route expects from the LLM.
        yield {"type": "delta", "text": '{"response":"Sure'}
        yield {"type": "delta", "text": " — drafting"}
        yield {"type": "delta", "text": ' a moon shot.",'}
        yield {"type": "delta", "text": '"tool_calls":[{"tool_name":'}
        yield {"type": "delta", "text": '"canvas.createVideoNode","arguments":{"title":"Moon","prompt":"moonlit chase"}}]}'}
        yield {
            "type": "done",
            "response": "Sure — drafting a moon shot.",
            "tool_calls": [
                {
                    "tool_name": "canvas.createVideoNode",
                    "arguments": {"title": "Moon", "prompt": "moonlit chase"},
                }
            ],
            "error": None,
        }

    monkeypatch.setattr(
        "src.apps.comic_gen.atelier_agent.stream_atelier_llm_planner", fake_stream
    )

    client = TestClient(api_module.app)
    with client.stream(
        "POST",
        f"/atelier/projects/{project.id}/agent/turns/stream",
        json={"user_message": "Draft a moonlit chase shot"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(chunk for chunk in response.iter_text())

    events = _parse_sse_events(body)
    event_names = [e for (e, _) in events]
    # Required v1.0 wire-frame ordering: turn -> llm_delta(s) -> llm_done
    # -> tool_start -> tool_done -> turn_done.
    assert event_names[0] == "turn"
    assert event_names[-1] == "turn_done"
    assert "llm_done" in event_names
    assert "tool_start" in event_names
    assert "tool_done" in event_names
    # llm_done must precede tool_start (the LLM phase is over before the
    # harness starts firing executor frames).
    assert event_names.index("llm_done") < event_names.index("tool_start")
    # And tool_done must precede the terminal turn_done.
    assert event_names.index("tool_done") < event_names.index("turn_done")

    # First event is `turn` (renamed from metadata), carrying planner +
    # turn_id so the panel can stamp the bubble before any tokens arrive.
    first_event, first_payload = events[0]
    assert first_event == "turn"
    assert first_payload["planner"] == "model_adapter"
    assert "turn_id" in first_payload

    # At least 3 llm_delta events whose concatenated text is the JSON the
    # planner emitted.
    delta_events = [p for (e, p) in events if e == "llm_delta"]
    assert len(delta_events) >= 3
    delta_concat = "".join(d.get("text", "") for d in delta_events)
    assert "canvas.createVideoNode" in delta_concat
    assert "moonlit chase" in delta_concat

    # Single llm_done frame carrying buffered text + raw tool_calls.
    llm_done = next(p for (e, p) in events if e == "llm_done")
    assert llm_done["response"] == "Sure — drafting a moon shot."
    assert llm_done["tool_calls"][0]["tool_name"] == "canvas.createVideoNode"

    # One tool_start / tool_done pair for the executor call.
    tool_start = next(p for (e, p) in events if e == "tool_start")
    assert tool_start["tool_name"] == "canvas.createVideoNode"
    assert tool_start["arguments"]["title"] == "Moon"
    assert tool_start["call_id"]
    tool_done = next(p for (e, p) in events if e == "tool_done")
    assert tool_done["tool_name"] == "canvas.createVideoNode"
    assert tool_done["status"] == "completed"
    assert tool_done["call_id"] == tool_start["call_id"]
    assert tool_done["error"] is None

    # Final turn_done — completed status, full_response, tool_calls, and
    # the persisted turn payload.
    last_event, last_payload = events[-1]
    assert last_event == "turn_done"
    assert last_payload["status"] == "completed"
    assert last_payload["full_response"] == "Sure — drafting a moon shot."
    assert last_payload["tool_calls"][0]["tool_name"] == "canvas.createVideoNode"
    assert last_payload["turn"] is not None
    assert "id" in last_payload["turn"]

    # Persistence side-effect: project now has exactly one node + one
    # AtelierAgentTurn whose response carries the streamed text.
    refreshed = pipeline.get_atelier_project(project.id)
    assert len(refreshed.nodes) == 1
    assert refreshed.nodes[0].title == "Moon"
    assert len(refreshed.agent_turns) == 1
    assert refreshed.agent_turns[0].response == "Sure — drafting a moon shot."


def test_atelier_agent_stream_route_blocks_on_invalid_json(pipeline, monkeypatch):
    """v1.0 track T: when the LLM emits non-JSON text the route still streams
    the deltas + an llm_done frame, but the terminal `turn_done` event is
    `blocked` and no turn is persisted (project.nodes stays empty)."""
    from fastapi.testclient import TestClient
    from src.apps.comic_gen import api as api_module

    monkeypatch.setattr(api_module, "pipeline", pipeline)

    project = pipeline.create_atelier_project("Bad Stream Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    def fake_stream(package, user_message, model=None):
        yield {"type": "delta", "text": "not json at all"}
        yield {
            "type": "done",
            "response": "not json at all",
            "tool_calls": [],
            "error": "parse_failed",
        }

    monkeypatch.setattr(
        "src.apps.comic_gen.atelier_agent.stream_atelier_llm_planner", fake_stream
    )

    client = TestClient(api_module.app)
    with client.stream(
        "POST",
        f"/atelier/projects/{project.id}/agent/turns/stream",
        json={"user_message": "go"},
    ) as response:
        assert response.status_code == 200
        body = "".join(chunk for chunk in response.iter_text())

    events = _parse_sse_events(body)
    assert events, "expected at least one SSE event"
    event_names = [e for (e, _) in events]
    # The llm_done frame fires even on parse failure so the panel can
    # render the model's raw output before the turn_done blocks the run.
    assert "llm_done" in event_names
    # No tool_start / tool_done should be emitted when no actionable
    # tool_calls were parsed.
    assert "tool_start" not in event_names
    assert "tool_done" not in event_names
    last_event, last_payload = events[-1]
    assert last_event == "turn_done"
    assert last_payload["status"] == "blocked"
    assert last_payload["turn"] is None
    assert last_payload["tool_calls"] == []
    # Error message mentions JSON parse failure so the panel can show why.
    assert "JSON" in (last_payload.get("error") or "")

    # No persistence on parse failure.
    refreshed = pipeline.get_atelier_project(project.id)
    assert refreshed.nodes == []
    assert refreshed.agent_turns == []


def test_atelier_agent_stream_route_forwards_harness_events_in_order(pipeline, monkeypatch):
    """v1.0 track T: when `AtelierAgentHarness.run_turn_streaming` is
    monkeypatched to a fixed event sequence, the SSE route forwards each
    yielded harness event to the wire in order and the terminal turn_done
    carries the harness's persisted turn payload (model_dumped).

    Exercises the bridge path end-to-end without depending on tool
    executor side effects, so the assertion is purely about the wire
    plumbing (event names + ordering + payload pass-through).
    """
    from fastapi.testclient import TestClient
    from src.apps.comic_gen import api as api_module
    from src.apps.comic_gen import atelier_agent as agent_module
    from src.apps.comic_gen.models import AtelierAgentTurn, AtelierAgentToolCall

    monkeypatch.setattr(api_module, "pipeline", pipeline)

    project = pipeline.create_atelier_project("Bridge Board")
    pipeline.update_atelier_agent_policy(project.id, {"approval_mode": "never"})

    def fake_stream(package, user_message, model=None):
        # Two-call planner output so we can assert the bridge forwards
        # multiple tool_start/tool_done pairs and that the discriminated
        #-union ordering survives the queue round-trip.
        yield {
            "type": "done",
            "response": "Drafting two takes.",
            "tool_calls": [
                {"tool_name": "canvas.createVideoNode", "arguments": {"title": "A", "prompt": "p1"}},
                {"tool_name": "canvas.createVideoNode", "arguments": {"title": "B", "prompt": "p2"}},
            ],
            "error": None,
        }

    monkeypatch.setattr(
        "src.apps.comic_gen.atelier_agent.stream_atelier_llm_planner", fake_stream
    )

    # Monkeypatch the harness's streaming generator to a fixed event
    # sequence so we don't depend on executor side effects. The terminal
    # turn_done carries a real AtelierAgentTurn so the api-layer can
    # model_dump it onto the wire.
    fixed_turn = AtelierAgentTurn(
        project_id=project.id,
        user_message="bridge",
        preview=False,
        status="completed",
        tool_calls=[
            AtelierAgentToolCall(tool_name="canvas.createVideoNode", arguments={"title": "A"}),
            AtelierAgentToolCall(tool_name="canvas.createVideoNode", arguments={"title": "B"}),
        ],
        response="Drafting two takes.",
    )

    def fake_run_turn_streaming(self, **kwargs):
        yield {
            "type": "tool_start",
            "call_id": "c1",
            "tool_name": "canvas.createVideoNode",
            "arguments": {"title": "A"},
        }
        yield {
            "type": "tool_done",
            "call_id": "c1",
            "tool_name": "canvas.createVideoNode",
            "status": "completed",
            "result_snapshot": {"node": {"id": "n1"}},
            "error": None,
        }
        yield {
            "type": "tool_start",
            "call_id": "c2",
            "tool_name": "canvas.createVideoNode",
            "arguments": {"title": "B"},
        }
        yield {
            "type": "tool_done",
            "call_id": "c2",
            "tool_name": "canvas.createVideoNode",
            "status": "failed",
            "result_snapshot": None,
            "error": "boom",
        }
        yield {
            "type": "turn_done",
            "turn": fixed_turn,
            "status": fixed_turn.status,
            "error": None,
        }

    monkeypatch.setattr(
        agent_module.AtelierAgentHarness,
        "run_turn_streaming",
        fake_run_turn_streaming,
    )

    client = TestClient(api_module.app)
    with client.stream(
        "POST",
        f"/atelier/projects/{project.id}/agent/turns/stream",
        json={"user_message": "bridge"},
    ) as response:
        assert response.status_code == 200
        body = "".join(chunk for chunk in response.iter_text())

    events = _parse_sse_events(body)
    event_names = [e for (e, _) in events]
    # Required ordering — each harness frame should appear once in
    # this exact relative order, sandwiched by the surrounding
    # turn / llm_done / turn_done wrappers.
    assert event_names[0] == "turn"
    assert event_names[-1] == "turn_done"
    expected_subsequence = [
        "llm_done",
        "tool_start",
        "tool_done",
        "tool_start",
        "tool_done",
        "turn_done",
    ]
    # Walk the wire events and confirm the expected sequence appears in
    # order (allowing other frames in between, e.g. the initial `turn`).
    iterator = iter(event_names)
    for expected in expected_subsequence:
        assert any(name == expected for name in iterator), (
            f"missing {expected} in wire order; got {event_names}"
        )

    # Bridge fidelity: tool_start payloads carry the harness's verbatim
    # call_id + arguments, and tool_done payloads carry the harness's
    # status + error pass-through.
    tool_starts = [p for (e, p) in events if e == "tool_start"]
    tool_dones = [p for (e, p) in events if e == "tool_done"]
    assert [t["call_id"] for t in tool_starts] == ["c1", "c2"]
    assert [t["arguments"] for t in tool_starts] == [{"title": "A"}, {"title": "B"}]
    assert [t["call_id"] for t in tool_dones] == ["c1", "c2"]
    assert [t["status"] for t in tool_dones] == ["completed", "failed"]
    assert tool_dones[1]["error"] == "boom"

    # Final turn_done carries the persisted turn (model_dumped) and the
    # api-layer status label.
    last_event, last_payload = events[-1]
    assert last_event == "turn_done"
    assert last_payload["status"] == "completed"
    assert last_payload["turn"] is not None
    assert last_payload["turn"]["status"] == "completed"
    assert last_payload["turn"]["response"] == "Drafting two takes."
    assert [c["tool_name"] for c in last_payload["tool_calls"]] == [
        "canvas.createVideoNode",
        "canvas.createVideoNode",
    ]
