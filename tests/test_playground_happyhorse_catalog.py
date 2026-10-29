"""HappyHorse 1.1 catalog facts required by Playground Core admission."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "config/model_catalog/generated/model_catalog.json"
FRONTEND = ROOT / "frontend/src/generated/modelCatalog.json"


def _catalog(path: Path) -> dict:
    return json.loads(path.read_text())


def _assert_happyhorse_catalog(data: dict) -> None:
    compat = data["compat"]["legacy_model_ids"]
    assert compat["happyhorse-1.1-t2v"] == "happyhorse/happyhorse-1.1-t2v#t2v"
    assert compat["happyhorse-1.1-i2v"] == "happyhorse/happyhorse-1.1-video#i2v"
    assert compat["happyhorse-1.1-r2v"] == "happyhorse/happyhorse-1.1-video#r2v"

    modes = data["modes"]
    t2v = modes["happyhorse/happyhorse-1.1-t2v#t2v"]
    i2v = modes["happyhorse/happyhorse-1.1-video#i2v"]
    r2v = modes["happyhorse/happyhorse-1.1-video#r2v"]

    assert t2v["legacy_model_id"] == "happyhorse-1.1-t2v"
    assert i2v["legacy_model_id"] == "happyhorse-1.1-i2v"
    assert r2v["legacy_model_id"] == "happyhorse-1.1-r2v"
    assert t2v["runtime"]["dashscope"]["gateway"] == "dashscope_workspace"
    assert i2v["runtime"]["dashscope"]["gateway"] == "dashscope_workspace"
    assert r2v["runtime"]["dashscope"]["gateway"] == "dashscope_workspace"

    assert t2v["duration"] == {"type": "slider", "min": 3, "max": 15, "step": 1, "default": 5}
    assert i2v["duration"] == {"type": "slider", "min": 3, "max": 15, "step": 1, "default": 5}
    assert r2v["duration"] == {"type": "slider", "min": 3, "max": 15, "step": 1, "default": 5}

    for mode in (t2v, i2v, r2v):
        params = mode["params"]
        assert params["resolution"] == {"options": ["480P", "720P", "1080P"], "default": "1080P"}
        assert params["seed"] is True
        assert params["watermark"] == {"default": True}
        assert "negativePrompt" not in params
        assert "promptExtend" not in params
        assert "audio" not in params

    ratio_options = ["16:9", "9:16", "1:1", "4:3", "3:4", "9:21", "21:9", "4:5", "5:4"]
    assert t2v["params"]["ratio"] == {"options": ratio_options, "default": "16:9"}
    assert r2v["params"]["ratio"] == {"options": ratio_options, "default": "16:9"}
    assert "ratio" not in i2v["params"]
    assert t2v["inputs"]["media"]["max"] == 0
    assert i2v["inputs"]["reference_images"]["max"] == 1
    assert r2v["inputs"]["reference_images"]["max"] == 9


def test_backend_happyhorse_11_catalog_facts_for_playground_admission() -> None:
    _assert_happyhorse_catalog(_catalog(BACKEND))


def test_frontend_generated_happyhorse_11_catalog_matches_backend_facts() -> None:
    _assert_happyhorse_catalog(_catalog(FRONTEND))
