"""Region tools (B-α) — backend agent tool tests.

The frontend store derives bounds, dispatches multiple tool calls, etc.
At this level we just verify each individual tool does its atomic job:
create a region node, attach a node to a region, detach a node from a
region. The harness/permission flow is already covered by
test_atelier_core.py — we don't re-prove it here.
"""

from unittest.mock import patch

import pytest

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


def _approve(pipeline, project_id: str, calls):
    """Helper: send calls untrusted-mode, then approve. Mirrors the
    approved-untrusted-call pattern in test_atelier_core.py."""
    pending = pipeline.run_atelier_agent_turn(project_id, calls)
    return pipeline.run_atelier_agent_turn(
        project_id, calls, approve=True, turn_id=pending.id,
    )


def test_canvas_create_region_tool_is_registered(pipeline):
    project = pipeline.create_atelier_project("Board")
    tool_specs = pipeline.list_atelier_agent_tools()
    names = [spec["name"] for spec in tool_specs]
    assert "canvas.createRegion" in names
    assert "canvas.attachToRegion" in names
    assert "canvas.detachFromRegion" in names


def test_create_region_tool_creates_region_node(pipeline):
    project = pipeline.create_atelier_project("Board")

    turn = _approve(
        pipeline,
        project.id,
        [
            {
                "tool_name": "canvas.createRegion",
                "arguments": {
                    "title": "Character study",
                    "color": "cyan",
                    "x": 100.0,
                    "y": 200.0,
                    "width": 600.0,
                    "height": 400.0,
                },
            }
        ],
    )

    assert turn.status == "completed"
    region_nodes = [n for n in pipeline.atelier_projects[project.id].nodes if n.type == "region"]
    assert len(region_nodes) == 1
    region = region_nodes[0]
    assert region.title == "Character study"
    assert region.x == 100.0
    assert region.y == 200.0
    assert region.width == 600.0
    assert region.height == 400.0
    assert region.data.get("color") == "cyan"
    assert region.created_by == "agent"


def test_create_region_with_no_size_uses_defaults(pipeline):
    project = pipeline.create_atelier_project("Board")

    _approve(
        pipeline,
        project.id,
        [
            {
                "tool_name": "canvas.createRegion",
                "arguments": {"title": "Empty"},
            }
        ],
    )

    region = next(n for n in pipeline.atelier_projects[project.id].nodes if n.type == "region")
    assert region.width > 0
    assert region.height > 0


def test_attach_to_region_sets_region_id_on_target_node(pipeline):
    project = pipeline.create_atelier_project("Board")
    # Seed: create a video node and a region node directly via pipeline.
    video = pipeline.create_atelier_node(
        project.id,
        {
            "type": "video",
            "title": "Shot",
            "prompt": "A chase",
            "x": 200,
            "y": 200,
            "width": 240,
            "height": 110,
        },
    )
    region = pipeline.create_atelier_node(
        project.id,
        {
            "type": "region",
            "title": "Group",
            "x": 100,
            "y": 100,
            "width": 600,
            "height": 400,
            "data": {},
        },
    )

    _approve(
        pipeline,
        project.id,
        [
            {
                "tool_name": "canvas.attachToRegion",
                "arguments": {"node_id": video.id, "region_id": region.id},
            }
        ],
    )

    fresh = next(n for n in pipeline.atelier_projects[project.id].nodes if n.id == video.id)
    assert fresh.data.get("region_id") == region.id


def test_attach_to_region_preserves_other_data_fields(pipeline):
    project = pipeline.create_atelier_project("Board")
    video = pipeline.create_atelier_node(
        project.id,
        {
            "type": "video",
            "title": "Shot",
            "prompt": "x",
            "x": 200,
            "y": 200,
            "width": 240,
            "height": 110,
            "data": {"intent": "chase", "reference_image_urls": ["a.png"]},
        },
    )
    region = pipeline.create_atelier_node(
        project.id,
        {"type": "region", "title": "G", "x": 0, "y": 0, "width": 600, "height": 400, "data": {}},
    )

    _approve(
        pipeline,
        project.id,
        [
            {
                "tool_name": "canvas.attachToRegion",
                "arguments": {"node_id": video.id, "region_id": region.id},
            }
        ],
    )

    fresh = next(n for n in pipeline.atelier_projects[project.id].nodes if n.id == video.id)
    assert fresh.data.get("region_id") == region.id
    assert fresh.data.get("intent") == "chase"
    assert fresh.data.get("reference_image_urls") == ["a.png"]


def test_attach_to_region_rejects_non_region_target(pipeline):
    project = pipeline.create_atelier_project("Board")
    video1 = pipeline.create_atelier_node(
        project.id,
        {"type": "video", "title": "A", "x": 0, "y": 0, "width": 240, "height": 110},
    )
    video2 = pipeline.create_atelier_node(
        project.id,
        {"type": "video", "title": "B", "x": 0, "y": 0, "width": 240, "height": 110},
    )

    pending = pipeline.run_atelier_agent_turn(
        project.id,
        [
            {
                "tool_name": "canvas.attachToRegion",
                "arguments": {"node_id": video1.id, "region_id": video2.id},
            }
        ],
    )
    final = pipeline.run_atelier_agent_turn(
        project.id,
        [
            {
                "tool_name": "canvas.attachToRegion",
                "arguments": {"node_id": video1.id, "region_id": video2.id},
            }
        ],
        approve=True,
        turn_id=pending.id,
    )

    # The call should have been rejected by the executor — turn ends in
    # failed state. Children are not modified.
    assert final.tool_calls[0].status == "failed"
    fresh = next(n for n in pipeline.atelier_projects[project.id].nodes if n.id == video1.id)
    assert "region_id" not in (fresh.data or {})


def test_detach_from_region_clears_region_id_only(pipeline):
    project = pipeline.create_atelier_project("Board")
    region = pipeline.create_atelier_node(
        project.id,
        {"type": "region", "title": "G", "x": 0, "y": 0, "width": 600, "height": 400, "data": {}},
    )
    video = pipeline.create_atelier_node(
        project.id,
        {
            "type": "video",
            "title": "A",
            "x": 0,
            "y": 0,
            "width": 240,
            "height": 110,
            "data": {"region_id": region.id, "intent": "chase"},
        },
    )

    _approve(
        pipeline,
        project.id,
        [
            {
                "tool_name": "canvas.detachFromRegion",
                "arguments": {"node_id": video.id},
            }
        ],
    )

    fresh = next(n for n in pipeline.atelier_projects[project.id].nodes if n.id == video.id)
    assert "region_id" not in (fresh.data or {})
    assert fresh.data.get("intent") == "chase"


def test_detach_from_region_is_noop_when_not_attached(pipeline):
    project = pipeline.create_atelier_project("Board")
    video = pipeline.create_atelier_node(
        project.id,
        {
            "type": "video",
            "title": "A",
            "x": 0,
            "y": 0,
            "width": 240,
            "height": 110,
            "data": {"intent": "chase"},
        },
    )

    _approve(
        pipeline,
        project.id,
        [
            {
                "tool_name": "canvas.detachFromRegion",
                "arguments": {"node_id": video.id},
            }
        ],
    )

    fresh = next(n for n in pipeline.atelier_projects[project.id].nodes if n.id == video.id)
    assert "region_id" not in (fresh.data or {})
    assert fresh.data.get("intent") == "chase"
