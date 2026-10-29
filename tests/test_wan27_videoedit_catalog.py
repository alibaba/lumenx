from __future__ import annotations

from src.utils.model_catalog import get_catalog_accessor


def test_wan27_videoedit_catalog_uses_v2v_canonical_and_source_duration() -> None:
    catalog = get_catalog_accessor()

    assert catalog.resolve_legacy_to_canonical("wan2.7-videoedit") == "wan/wan2.7-video#v2v"

    entry = catalog.get_mode_entry("wan/wan2.7-video#v2v")
    assert entry is not None
    assert entry["legacy_model_id"] == "wan2.7-videoedit"
    assert "v2v" in entry["capabilities"]
    assert entry["provider"] == "aliyun"

    duration = entry["duration"]
    assert duration["default"] == 0
    assert duration["options"] == [0, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    params = entry["params"]
    assert params["resolution"]["default"] == "1080P"
    assert params["resolution"]["options"] == ["720P", "1080P"]
    assert params["audioSetting"]["default"] == "auto"
    assert params["audioSetting"]["options"] == ["auto", "origin"]
    assert params["seed"] is True
