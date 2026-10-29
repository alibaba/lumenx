"""HappyHorse 1.0 VideoEdit catalog facts required by Playground admission."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "config/model_catalog/generated/model_catalog.json"
FRONTEND = ROOT / "frontend/src/generated/modelCatalog.json"
CANONICAL = "happyhorse/happyhorse-1.0-video-edit#v2v"
LEGACY = "happyhorse-1.0-video-edit"


def _catalog(path: Path) -> dict:
    return json.loads(path.read_text())


def _assert_happyhorse_videoedit_catalog(data: dict) -> None:
    assert data["compat"]["legacy_model_ids"][LEGACY] == CANONICAL

    entry = data["modes"][CANONICAL]
    assert entry["legacy_model_id"] == LEGACY
    assert entry["model_line_id"] == "happyhorse/happyhorse-1.0-video-edit"
    assert entry["mode"] == "v2v"
    assert entry["capabilities"] == ["v2v"]
    assert entry["status"] == "hidden"
    assert entry["runtime"]["dashscope"]["gateway"] == "dashscope_workspace"
    assert entry["ui"]["visible_in"] == []

    assert entry["duration"] is None
    params = entry["params"]
    assert params["resolution"] == {"options": ["720P", "1080P"], "default": "1080P"}
    assert params["audioSetting"] == {"options": ["auto", "origin"], "default": "auto"}
    assert params["watermark"] == {"default": True}
    assert params["seed"] is True
    assert "ratio" not in params
    assert "duration" not in params
    assert "negativePrompt" not in params
    assert "promptExtend" not in params

    assert entry["inputs"]["media"] == {"max": 1, "reference_type": "video"}
    assert "reference_images" not in entry["inputs"]


def test_backend_happyhorse_videoedit_catalog_facts_for_playground_admission() -> None:
    _assert_happyhorse_videoedit_catalog(_catalog(BACKEND))


def test_frontend_generated_happyhorse_videoedit_catalog_matches_backend_facts() -> None:
    _assert_happyhorse_videoedit_catalog(_catalog(FRONTEND))
