"""Actual storyboard processors must expose transport/response failures."""
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, llm_adapter, pipeline as pipeline_module
from src.apps.comic_gen.llm import PolishError, ScriptProcessor
from src.apps.comic_gen.models import Script, StoryboardFrame


@pytest.fixture
def processor(monkeypatch, tmp_path):
    transport = Mock(is_configured=True)
    monkeypatch.setattr(llm_adapter, "LLMAdapter", lambda: transport)
    return ScriptProcessor(output_root=str(tmp_path / "media"))


def invoke(processor, kind):
    if kind == "analyze":
        return processor.analyze_to_storyboard("source text", {"characters": [], "scenes": [], "props": []})
    if kind == "rich":
        return processor.refine_frame_to_rich({"action_summary": "draft"}, [], [])
    return processor.polish_storyboard_prompt("draft", [])


def good_result(kind):
    if kind == "analyze":
        return {"frames": [{"action_summary": "a character walks", "duration": 4}]}
    if kind == "rich":
        return {"visual_description": "a character walks into soft light"}
    return {"prompt_cn": "角色走入柔光", "prompt_en": "A character steps into soft cinematic light"}


@pytest.mark.parametrize("kind", ["analyze", "rich", "prompt"])
@pytest.mark.parametrize("failure,reason", [
    ("unconfigured", "is_configured_false"), ("transport", "api_error"),
    ("invalid_json", "json_parse_error"), ("empty_content", "json_parse_error"),
    ("nontext", "json_parse_error"), ("wrong_root", "missing_keys"),
    ("empty_object", "missing_keys"),
])
def test_processor_failures_are_explicit_and_never_call_transport_twice(processor, kind, failure, reason):
    if failure == "unconfigured":
        processor.llm.is_configured = False
    elif failure == "transport":
        processor.llm.chat.side_effect = RuntimeError("https://provider.invalid/generate?token=synthetic-secret")
    else:
        processor.llm.chat.return_value = {
            "invalid_json": "not JSON", "empty_content": "", "nontext": None,
            "wrong_root": "[]", "empty_object": "{}",
        }[failure]
    with pytest.raises(PolishError) as caught:
        invoke(processor, kind)
    assert caught.value.reason == reason
    assert caught.value.message_zh and caught.value.message_en
    if failure == "transport":
        assert "synthetic-secret" not in caught.value.message_zh + caught.value.message_en
    assert processor.llm.chat.call_count == (0 if failure == "unconfigured" else 1)


@pytest.mark.parametrize("kind,payload", [
    ("prompt", {"prompt_cn": "CN"}),
    ("prompt", {"prompt_cn": "CN", "prompt_en": None}),
    ("prompt", {"prompt_cn": "CN", "prompt_en": []}),
    ("prompt", {"prompt_cn": " ", "prompt_en": "EN"}),
    ("analyze", {"frames": []}), ("analyze", {"frames": "invalid"}),
    ("analyze", {"frames": ["invalid"]}), ("analyze", {"frames": [{}]}),
    ("analyze", {"frames": [{"action_summary": " "}]}),
    ("rich", {"unknown": "not a supported patch"}),
])
def test_processor_rejects_incomplete_json_results(processor, kind, payload):
    processor.llm.chat.return_value = json.dumps(payload)
    with pytest.raises(PolishError) as caught:
        invoke(processor, kind)
    assert caught.value.reason == "missing_keys"
    processor.llm.chat.assert_called_once()


@pytest.mark.parametrize("kind", ["analyze", "rich", "prompt"])
@pytest.mark.parametrize("fenced", [False, True])
def test_processor_preserves_successful_results_and_code_fences(processor, kind, fenced):
    result = good_result(kind)
    content = json.dumps(result)
    processor.llm.chat.return_value = f"```json\n{content}\n```" if fenced else content
    assert invoke(processor, kind) == (result["frames"] if kind == "analyze" else result)
    processor.llm.chat.assert_called_once()


def test_storyboard_prompt_echo_preserves_bilingual_warning(processor):
    processor.llm.chat.return_value = json.dumps({"prompt_cn": "原稿", "prompt_en": "draft"})
    with pytest.raises(PolishError) as caught:
        invoke(processor, "prompt")
    assert caught.value.reason == "model_echo"
    assert caught.value.prompt_cn == "原稿" and caught.value.prompt_en == "draft"


def test_analyze_custom_template_and_action_description_compatibility(processor):
    frames = [{"action_description": "legacy compatible action"}]
    processor.llm.chat.return_value = json.dumps({"frames": frames})
    assert processor.analyze_to_storyboard("source", {"characters": [{"name": "hero"}]}, "{text}|{entities_str}") == frames
    messages = processor.llm.chat.call_args.kwargs["messages"]
    assert messages[0]["content"].startswith("source|") and "hero" in messages[0]["content"]


def test_prompt_custom_template_asset_numbering_and_feedback_remain_intact(processor):
    processor.llm.chat.return_value = json.dumps(good_result("prompt"))
    processor.polish_storyboard_prompt("draft", [{"type": "character", "name": "hero", "description": "coat"}], "more light", "{DRAFT}|{ASSETS}")
    content = processor.llm.chat.call_args.kwargs["messages"][0]["content"]
    assert "draft|Image 1: character - hero (coat)" in content
    assert "[用户反馈]" in content and "more light" in content


@pytest.fixture
def runtime(processor, tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(pipeline_module, name, Mock())
    runtime = pipeline_module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=processor)
    runtime.scripts["p"] = Script(
        id="p", title="project", original_text="source", created_at=1, updated_at=1,
        frames=[StoryboardFrame(id="f", scene_id="", action_description="draft",
                                image_prompt="saved EN", image_prompt_cn="saved CN", image_prompt_en="saved EN")],
    )
    runtime._save_data()
    yield runtime
    runtime.close()


def call(client, kind):
    if kind == "analyze":
        return client.post("/projects/p/storyboard/analyze", json={"text": "source"})
    if kind == "rich":
        return client.post("/projects/p/frames/f/refine")
    return client.post("/projects/p/storyboard/refine_prompt", json={"frame_id": "f", "raw_prompt": "draft"})


@pytest.mark.parametrize("kind", ["analyze", "rich", "prompt"])
@pytest.mark.parametrize("failure,reason", [
    ("unconfigured", "is_configured_false"), ("transport", "api_error"),
    ("invalid_json", "json_parse_error"), ("empty_object", "missing_keys"),
])
def test_http_actual_processor_errors_preserve_saved_project(runtime, processor, kind, failure, reason):
    if failure == "unconfigured":
        processor.llm.is_configured = False
    elif failure == "transport":
        processor.llm.chat.side_effect = RuntimeError("https://provider.invalid/generate?token=synthetic-secret")
    else:
        processor.llm.chat.return_value = "not JSON" if failure == "invalid_json" else "{}"
    before = runtime.read_committed_script("p").model_dump()
    path = runtime.data_dir / "projects.json"
    disk = path.read_bytes()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, kind)
        assert response.status_code == 502, response.text
        assert response.json()["detail"]["reason"] == reason
        assert "synthetic-secret" not in response.text
        assert runtime.scripts["p"].model_dump() == before and path.read_bytes() == disk
        runtime.assert_storage_available()
    assert processor.llm.chat.call_count == (0 if failure == "unconfigured" else 1)


def test_http_actual_prompt_echo_is_warning_without_project_write(runtime, processor):
    processor.llm.chat.return_value = json.dumps({"prompt_cn": "原稿", "prompt_en": "draft"})
    before = runtime.read_committed_script("p").model_dump()
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, "prompt")
        assert response.status_code == 502
        detail = response.json()["detail"]
        assert detail["reason"] == "model_echo" and detail["prompt_cn"] == "原稿" and detail["prompt_en"] == "draft"
        assert runtime.read_committed_script("p").model_dump() == before


@pytest.mark.parametrize("kind", ["analyze", "rich", "prompt"])
def test_http_actual_processor_success_persists_result(runtime, processor, kind):
    processor.llm.chat.return_value = json.dumps(good_result(kind))
    app = api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = call(client, kind)
        assert response.status_code == 200, response.text
        frame = runtime.read_committed_script("p").frames[0]
        if kind == "analyze":
            assert frame.action_description == "a character walks"
        elif kind == "rich":
            assert frame.visual_description == good_result(kind)["visual_description"]
        else:
            assert frame.image_prompt == good_result(kind)["prompt_en"]
    processor.llm.chat.assert_called_once()


def test_batch_actual_processor_failure_has_reason_and_continues(runtime, processor):
    runtime.scripts["p"].frames.append(StoryboardFrame(id="f2", scene_id="", action_description="second"))
    runtime._save_data()
    processor.llm.chat.side_effect = ["not JSON", json.dumps(good_result("rich"))]
    events = list(runtime.refine_batch_generator("p"))
    assert [name for name, _ in events] == ["frame_refine_start", "frame_refine_error", "frame_refine_start", "frame_refine_complete", "batch_complete"]
    assert events[1][1]["reason"] == "json_parse_error"
    assert events[-1][1] == {"total": 2, "success": 1, "failed": 1}
    assert processor.llm.chat.call_count == 2
