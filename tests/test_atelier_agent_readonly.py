from __future__ import annotations

import json

import pytest

from src.apps.atelier.agent_service import AtelierAgentReadOnlyService
from src.apps.atelier.generation_commands import ATELIER_VIDEO_MODEL
from src.apps.atelier.models import AtelierAgentPolicy, AtelierNode, AtelierProject
from src.apps.atelier.storage import AtelierStorage


def _storage_with_project(tmp_path, project: AtelierProject) -> AtelierStorage:
    storage = AtelierStorage(tmp_path)
    storage.create_project(project)
    return storage


def _project(
    *,
    project_id: str = "p1",
    image_url: str = "uploads/frame.png",
    allowed_tools: list[str] | None = None,
    include_absolute_media: bool = False,
) -> AtelierProject:
    media_urls = [image_url]
    if include_absolute_media:
        media_urls.append("/Users/hoshinoren/secret/frame.png")
    return AtelierProject(
        id=project_id,
        title="Canvas",
        description="Project description",
        created_at=100.0,
        updated_at=100.0,
        revision=0,
        agent_policy=AtelierAgentPolicy(allowed_tools=allowed_tools or []),
        nodes=[
            AtelierNode(
                id="img1",
                project_id=project_id,
                type="image",
                title="Hero frame",
                prompt="image prompt",
                media_urls=media_urls,
                data={
                    "api_key": "SECRET_NODE_DATA",
                    "result_snapshot": {"provider_text": "SECRET_RESULT"},
                },
            ),
            AtelierNode(
                id="video1",
                project_id=project_id,
                type="video",
                title="Video take",
                prompt="make this cinematic",
                data={
                    "reference_node_ids": ["img1"],
                    "reference_image_urls": ["https://signed.example/source.png?token=SECRET_QUERY"],
                    "candidates": [{"result_snapshot": {"provider": "SECRET_CANDIDATE"}}],
                },
            ),
        ],
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_tools_only_list_read_project_and_generation_proposal(tmp_path):
    storage = _storage_with_project(tmp_path, _project())
    service = AtelierAgentReadOnlyService(storage)

    tools = service.list_tools("p1")
    names = [tool["name"] for tool in tools]

    assert names == ["canvas.readProject", "generation.createVideoCandidates"]
    assert "canvas.createVideoNode" not in names
    assert "canvas.updateNodePrompt" not in names
    assert "canvas.attachReferenceNode" not in names
    generation_tool = next(tool for tool in tools if tool["name"] == "generation.createVideoCandidates")
    assert generation_tool["proposal_only"] is True
    assert generation_tool["input_schema"]["additionalProperties"] is False


def test_planner_package_is_allowlisted_and_does_not_write_state(tmp_path):
    storage = _storage_with_project(tmp_path, _project(include_absolute_media=True))
    service = AtelierAgentReadOnlyService(storage)
    before = storage.get_project("p1").model_dump(mode="json")

    package = service.build_planner_package(
        "p1", user_message="生成视频", selected_node_id="video1", skill_name="cinematic"
    )

    file_before = (tmp_path / "atelier_projects.json").read_bytes()
    after = storage.get_project("p1").model_dump(mode="json")
    file_after = (tmp_path / "atelier_projects.json").read_bytes()
    payload = package.model_dump(mode="json")
    encoded = _json(payload)
    assert after == before
    assert file_after == file_before
    assert payload["project_snapshot"]["id"] == "p1"
    assert payload["selected_node_snapshot"]["id"] == "video1"
    assert payload["project_snapshot"]["nodes"][0]["media_urls"] == ["uploads/frame.png"]
    assert "data" not in payload["project_snapshot"]["nodes"][0]
    assert "agent_turns" not in payload["project_snapshot"]
    assert "generation_attempts" not in payload["project_snapshot"]
    assert "SECRET_NODE_DATA" not in encoded
    assert "SECRET_RESULT" not in encoded
    assert "SECRET_CANDIDATE" not in encoded
    assert "SECRET_QUERY" not in encoded
    assert "/Users/hoshinoren" not in encoded


def test_planner_package_exposes_pending_reference_graph_state(tmp_path):
    project = _project()
    project.nodes.append(AtelierNode(id="placeholder", project_id="p1", type="image"))
    project.nodes[1].data = {
        "reference_node_ids": ["img1", "placeholder"],
        "pending_reference_nodes": [{"schema_version": 1, "source_node_id": "placeholder", "source_kind": "image", "status": "pending_media", "credential_ref": "SECRET"}],
    }
    storage = _storage_with_project(tmp_path, project)
    service = AtelierAgentReadOnlyService(storage)

    package = service.build_planner_package("p1", selected_node_id="video1")

    video = next(node for node in package.project_snapshot["nodes"] if node["id"] == "video1")
    assert video["reference_node_ids"] == ["img1", "placeholder"]
    assert video["pending_reference_nodes"] == [{"schema_version": 1, "source_node_id": "placeholder", "source_kind": "image", "status": "pending_media"}]
    assert "reference_image_urls" not in video
    assert "SECRET" not in _json(package.model_dump(mode="json"))


def test_plan_generation_uses_durable_proposal_without_provider_or_storage_work(tmp_path):
    storage = _storage_with_project(tmp_path, _project())
    service = AtelierAgentReadOnlyService(storage)
    before = storage.get_project("p1").model_dump(mode="json")
    file_before = (tmp_path / "atelier_projects.json").read_bytes()

    plan = service.plan("p1", user_message="生成一个视频候选", selected_node_id="video1")

    after = storage.get_project("p1").model_dump(mode="json")
    file_after = (tmp_path / "atelier_projects.json").read_bytes()
    assert after == before
    assert file_after == file_before
    assert plan.status == "ready"
    assert len(plan.tool_calls) == 1
    call = plan.tool_calls[0]
    assert call["tool_name"] == "generation.createVideoCandidates"
    assert call["contract_version"] == "atelier-agent-generation-proposal-v1"
    args = call["arguments"]
    assert args["node_id"] == "video1"
    assert args["model_id"] == ATELIER_VIDEO_MODEL
    assert args["mode"] == "i2v"
    assert args["prompt"] == "make this cinematic"
    assert args["first_frame"] == {"node_id": "img1", "media_url": "uploads/frame.png"}
    assert args["params"] == {}
    assert "batch_size" not in args
    assert "model" not in args
    assert "reference_image_urls" not in args


def test_plan_blocks_policy_disallowed_generation(tmp_path):
    storage = _storage_with_project(tmp_path, _project(allowed_tools=["canvas.readProject"]))
    service = AtelierAgentReadOnlyService(storage)

    plan = service.plan("p1", user_message="生成视频", selected_node_id="video1")

    assert plan.status == "blocked"
    assert "project policy" in plan.reason
    assert plan.tool_calls == []


def test_plan_blocks_unknown_planner_and_planner_input_without_echoing_secrets(tmp_path):
    storage = _storage_with_project(tmp_path, _project())
    service = AtelierAgentReadOnlyService(storage)

    unknown = service.plan("p1", user_message="生成视频", selected_node_id="video1", planner="model_adapter")
    forbidden_input = service.plan(
        "p1",
        user_message="生成视频",
        selected_node_id="video1",
        planner_input={"secret_prompt": "SECRET_PLANNER_INPUT"},
    )

    assert unknown.status == "blocked"
    assert "Unknown Atelier agent planner" in unknown.reason
    assert forbidden_input.status == "blocked"
    encoded = forbidden_input.model_dump_json()
    assert "SECRET_PLANNER_INPUT" not in encoded
    assert forbidden_input.context.planner_input == {"rejected": True}


def test_selected_node_must_belong_to_project(tmp_path):
    storage = _storage_with_project(tmp_path, _project())
    p2 = _project(project_id="p2")
    p2.nodes[0].id = "image-p2"
    p2.nodes[1].id = "video-p2"
    p2.nodes[1].data["reference_node_ids"] = ["image-p2"]
    storage.create_project(p2)
    service = AtelierAgentReadOnlyService(storage)

    plan = service.plan("p1", user_message="生成视频", selected_node_id="video-p2")

    assert plan.status == "blocked"
    assert "Selected Atelier node was not found" in plan.reason
    with pytest.raises(ValueError, match="Selected Atelier node was not found"):
        service.build_planner_package("p1", selected_node_id="video-p2")


def test_signed_remote_media_query_is_stripped_from_snapshot_and_plan(tmp_path):
    storage = _storage_with_project(
        tmp_path,
        _project(image_url="https://cdn.example/private/frame.png?X-Amz-Credential=SECRET&token=SECRET"),
    )
    service = AtelierAgentReadOnlyService(storage)

    package = service.build_planner_package("p1", selected_node_id="video1")
    plan = service.plan("p1", user_message="生成视频", selected_node_id="video1")

    assert package.project_snapshot["nodes"][0]["media_urls"] == ["https://cdn.example/private/frame.png"]
    assert plan.status == "blocked"
    assert "managed local" in plan.reason
    encoded = _json({"package": package.model_dump(mode="json"), "plan": plan.model_dump(mode="json")})
    assert "X-Amz-Credential" not in encoded
    assert "token=" not in encoded
    assert "SECRET" not in encoded


def test_plan_requires_explicit_same_project_attached_image_source(tmp_path):
    project = _project()
    project.nodes[1].data["reference_node_ids"] = ["missing", "video1"]
    storage = _storage_with_project(tmp_path, project)
    service = AtelierAgentReadOnlyService(storage)

    plan = service.plan("p1", user_message="生成视频", selected_node_id="video1")

    assert plan.status == "blocked"
    assert "attached image" in plan.reason


def test_url_userinfo_and_malformed_ipv6_are_not_leaked_or_500(tmp_path):
    storage = _storage_with_project(
        tmp_path,
        _project(image_url="https://user:SECRET_PASSWORD@cdn.example/frame.png?token=SECRET"),
    )
    service = AtelierAgentReadOnlyService(storage)

    package = service.build_planner_package("p1", selected_node_id="video1")
    plan = service.plan("p1", user_message="生成视频", selected_node_id="video1")

    encoded = _json({"package": package.model_dump(mode="json"), "plan": plan.model_dump(mode="json")})
    assert "SECRET_PASSWORD" not in encoded
    assert "user:" not in encoded
    assert plan.status == "blocked"

    bad = _project(project_id="bad", image_url="https://[::1")
    storage.create_project(bad)
    bad_package = service.build_planner_package("bad", selected_node_id="video1")
    bad_plan = service.plan("bad", user_message="生成视频", selected_node_id="video1")
    assert bad_package.project_snapshot["nodes"][0]["media_urls"] == []
    assert bad_plan.status == "blocked"


def test_tool_schemas_are_defensive_copies_and_follow_command_shape(tmp_path):
    storage = _storage_with_project(tmp_path, _project())
    service = AtelierAgentReadOnlyService(storage)

    first = service.list_tools("p1")
    generation = next(tool for tool in first if tool["name"] == "generation.createVideoCandidates")
    generation["input_schema"]["properties"]["node_id"] = {"type": "integer"}
    second = service.list_tools("p1")
    generation_again = next(tool for tool in second if tool["name"] == "generation.createVideoCandidates")
    schema = generation_again["input_schema"]

    assert schema["properties"]["node_id"]["type"] == "string"
    assert "expected_revision" not in schema["properties"]
    assert "client_request_id" not in schema["properties"]
    assert "node_id" in schema["required"]
    assert "$defs" in schema
    assert schema["properties"]["first_frame"] == {"$ref": "#/$defs/FirstFrameSelection"}
    assert "duration" not in schema["properties"]["params"].get("properties", {})


def test_generation_proposal_accepts_core_managed_local_prefixes_without_io(tmp_path):
    for media_url in [
        "assets/characters/frame.png",
        "storyboard/shot-001.png",
        "playground/uploads/frame.png",
        "video_inputs/source.png",
        "output/assets/characters/frame.png",
        "outputs/storyboard/shot-001.png",
    ]:
        storage = _storage_with_project(tmp_path / media_url.replace("/", "_"), _project(image_url=media_url))
        service = AtelierAgentReadOnlyService(storage)

        plan = service.plan("p1", user_message="生成视频", selected_node_id="video1")

        assert plan.status == "ready", media_url
        assert plan.tool_calls[0]["arguments"]["first_frame"] == {"node_id": "img1", "media_url": media_url}


def test_generation_proposal_rejects_unmanaged_or_ambiguous_local_refs(tmp_path):
    cases = [
        "tmp/frame.png",
        "output/tmp/frame.png",
        "outputs/tmp/frame.png",
        "assets//frame.png",
        "assets/../frame.png",
        "assets\\frame.png",
        "assets/frame.png?token=SECRET",
        "assets/frame.png#frag",
        "assets/frame.png",
    ]
    for media_url in cases:
        storage = _storage_with_project(tmp_path / str(abs(hash(media_url))), _project(image_url=media_url))
        service = AtelierAgentReadOnlyService(storage)

        plan = service.plan("p1", user_message="生成视频", selected_node_id="video1")

        assert plan.status == "blocked", media_url
        assert plan.tool_calls == []
