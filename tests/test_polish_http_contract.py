"""F14: actual HTTP/processor contracts with only the LLM call replaced."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.apps.comic_gen.llm_adapter import LLMAdapter
from tests import test_studio_core_image_admission as admission


ROUTES = ("/video/polish_prompt", "/video/polish_r2v_prompt")
RESULT = {"prompt_cn": "暮色中的银色飞船掠过海面", "prompt_en": "A silver spacecraft sweeps over the ocean at twilight"}
runtime = admission.runtime


def payload(**changes):
    return {"draft_prompt": "a quiet lighthouse", "script_id": "p", "slots": [{"description": "Mira"}], **changes}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("outcome", ["success", "is_configured_false", "api_error", "json_parse_error", "missing_keys", "model_echo"])
def test_real_polish_http_keeps_success_and_declared_failure_distinct(runtime, monkeypatch, route, outcome):
    result = {**RESULT, "prompt_en": payload()["draft_prompt"]} if outcome == "model_echo" else RESULT
    answer = "not json" if outcome == "json_parse_error" else json.dumps({"prompt_cn": "only one key"} if outcome == "missing_keys" else result)
    chat = Mock(return_value=answer)
    if outcome == "api_error":
        chat.side_effect = RuntimeError("synthetic upstream timeout")
    monkeypatch.setattr(LLMAdapter, "chat", chat)
    before = runtime.scripts["p"].model_dump(mode="json")
    app = create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        app.state.runtime_environment.update(
            {"DASHSCOPE_API_KEY": "" if outcome == "is_configured_false" else "synthetic-polish-key"},
            persist=lambda *_: None,
        )
        response = client.post(route, json=payload())
    assert runtime.scripts["p"].model_dump(mode="json") == before
    if outcome == "success":
        assert response.status_code == 200, response.text
        assert response.json() == RESULT
    else:
        assert response.status_code == 502, response.text
        detail = response.json()["detail"]
        assert detail["reason"] == outcome
        assert isinstance(detail["message_zh"], str) and detail["message_zh"]
        assert isinstance(detail["message_en"], str) and detail["message_en"]
        if outcome == "model_echo":
            assert {key: detail[key] for key in RESULT} == result
        else:
            assert "prompt_cn" not in detail and "prompt_en" not in detail
    assert chat.call_count == (0 if outcome == "is_configured_false" else 1)


@pytest.mark.parametrize("route", ROUTES)
def test_real_polish_http_passes_bilingual_feedback_and_project_configuration(runtime, monkeypatch, route):
    field = "r2v_polish" if "r2v" in route else "video_polish"
    setattr(runtime.scripts["p"].prompt_config, field, "PROJECT POLISH RULE {SLOTS}")
    runtime.scripts["p"].prompt_config.polish_model = "project-polish-model"
    runtime._commit_storage("projects.json")
    chat = Mock(return_value=json.dumps(RESULT))
    monkeypatch.setattr(LLMAdapter, "chat", chat)
    app = create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        app.state.runtime_environment.update({"DASHSCOPE_API_KEY": "synthetic-polish-key"}, persist=lambda *_: None)
        response = client.post(route, json=payload(prev_cn="上一版中文锚点", feedback="保留灯塔，改成黄昏"))
    assert response.status_code == 200 and response.json() == RESULT
    chat.assert_called_once()
    sent = chat.call_args.kwargs
    assert sent["model"] == "project-polish-model"
    assert "PROJECT POLISH RULE" in sent["messages"][0]["content"]
    message = sent["messages"][-1]["content"]
    assert all(text in message for text in ("上一版中文锚点", "保留灯塔，改成黄昏", "a quiet lighthouse"))
    if "r2v" in route:
        assert "Mira" in sent["messages"][0]["content"]


@pytest.mark.parametrize("route", ROUTES)
def test_invalid_polish_http_input_never_reaches_llm(runtime, monkeypatch, route):
    chat = Mock(side_effect=AssertionError("invalid HTTP input reached LLM"))
    monkeypatch.setattr(LLMAdapter, "chat", chat)
    app = create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)
    with TestClient(app) as client:
        response = client.post(route, json=payload(feedback="x" * 2001))
    assert response.status_code == 422
    chat.assert_not_called()
