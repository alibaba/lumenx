"""Merged video provenance and response-only freshness."""

import json

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from src.utils.json_storage import StorageConflictError, StorageError
from tests.test_dub_source_provenance import applied_a, runtime as dub_runtime

runtime = dub_runtime


def source_for(ref="video/A.mp4", *, bgm_url=None, mix_settings=None):
    return {
        "schema_version": 1,
        "frames": [["f", ref]],
        "bgm_url": bgm_url,
        "mix_settings": mix_settings or {"dialogue": 100, "bgm": 35, "sfx": 60},
    }


def test_successful_merge_persists_source_and_reports_current_without_persisting_freshness(runtime):
    item, _ = runtime

    merged = item.merge_videos("p")
    saved = item.read_committed_script("p")
    payload = json.loads((item.data_dir / "projects.json").read_text())["p"]

    assert merged.merged_video_source == source_for()
    assert saved.merged_video_source == source_for()
    assert payload["merged_video_source"] == source_for()
    assert "merged_video_freshness" not in payload
    assert item.merge_video_freshness(saved) == {
        "state": "current",
        "reason": "current",
        "project_revision": saved.revision,
    }


@pytest.mark.parametrize("change", ["take", "mix", "bgm"])
def test_merge_freshness_becomes_stale_when_current_inputs_change(runtime, change):
    item, _ = runtime
    item.merge_videos("p")

    if change == "take":
        item.select_video_for_frame("p", "f", "B")
    else:
        script = item.scripts["p"]
        if change == "mix":
            script.mix_settings = {"dialogue": 50, "bgm": 35, "sfx": 60}
        else:
            script.bgm_url = "audio/new.mp3"
        item._save_data()

    saved = item.read_committed_script("p")
    assert item.merge_video_freshness(saved) == {
        "state": "stale",
        "reason": "inputs_changed",
        "project_revision": saved.revision,
    }


def test_merge_source_uses_applied_dub_ref_and_stales_after_selected_take_changes(runtime):
    item, _ = runtime
    applied = applied_a(runtime)

    merged = item.merge_videos("p")
    assert merged.merged_video_source == source_for(applied.dubbed_video_url)

    item.select_video_for_frame("p", "f", "B")
    saved = item.read_committed_script("p")
    assert item.merge_video_freshness(saved)["state"] == "stale"
    assert item.merge_video_freshness(saved)["reason"] == "inputs_changed"


@pytest.mark.parametrize("stored_source,reason", [
    (None, "missing_source"),
    ("not-a-dict", "invalid_source"),
    ({"schema_version": 99, "frames": []}, "unknown_source_schema"),
    ({"schema_version": True, "frames": [["f", "video/A.mp4"]], "bgm_url": None, "mix_settings": {"dialogue": 100, "bgm": 35, "sfx": 60}}, "invalid_source"),
    ({"frames": [["f", "video/A.mp4"]]}, "invalid_source"),
    ({"schema_version": 1, "frames": [["", "video/A.mp4"]], "bgm_url": None, "mix_settings": {"dialogue": 100, "bgm": 35, "sfx": 60}}, "invalid_source"),
    ({"schema_version": 1, "frames": [["f", ""]], "bgm_url": None, "mix_settings": {"dialogue": 100, "bgm": 35, "sfx": 60}}, "invalid_source"),
    ({"schema_version": 1, "frames": [["f", "video/A.mp4"]], "bgm_url": 12, "mix_settings": {"dialogue": 100, "bgm": 35, "sfx": 60}}, "invalid_source"),
    ({"schema_version": 1, "frames": [["f", "video/A.mp4"]], "bgm_url": None, "mix_settings": {"dialogue": True, "bgm": 35, "sfx": 60}}, "invalid_source"),
    ({"schema_version": 1, "frames": [["f", "video/A.mp4"]], "bgm_url": None, "mix_settings": {"dialogue": 100.0, "bgm": 35, "sfx": 60}}, "invalid_source"),
    ({"schema_version": 1, "frames": [["f", "video/A.mp4"]], "bgm_url": None, "mix_settings": {"dialogue": 100, "bgm": 35}}, "invalid_source"),
    ({"schema_version": 1, "frames": [["f", "video/A.mp4"]], "bgm_url": None, "mix_settings": {"dialogue": 101, "bgm": 35, "sfx": 60}}, "invalid_source"),
])
def test_legacy_malformed_or_future_source_is_unknown(runtime, stored_source, reason):
    item, _ = runtime
    script = item.scripts["p"]
    script.merged_video_url = "video/prior.mp4"
    script.merged_video_source = stored_source
    item._save_data()

    saved = item.read_committed_script("p")
    assert item.merge_video_freshness(saved) == {
        "state": "unknown",
        "reason": reason,
        "project_revision": saved.revision,
    }


def test_missing_current_merge_input_reports_stale_invalid_inputs(runtime):
    item, _ = runtime
    item.merge_videos("p")
    (item.data_dir / "video/A.mp4").unlink()

    saved = item.read_committed_script("p")
    assert item.merge_video_freshness(saved) == {
        "state": "stale",
        "reason": "inputs_invalid",
        "project_revision": saved.revision,
    }


def test_missing_merged_output_reports_stale_output_missing(runtime):
    item, _ = runtime
    merged = item.merge_videos("p")
    (item.data_dir / merged.merged_video_url).unlink()

    saved = item.read_committed_script("p")
    assert item.merge_video_freshness(saved) == {
        "state": "stale",
        "reason": "output_missing",
        "project_revision": saved.revision,
    }


def test_merge_storage_failure_preserves_previous_url_and_source(runtime, monkeypatch):
    item, state = runtime
    script = item.scripts["p"]
    script.merged_video_url = "video/prior.mp4"
    script.merged_video_source = source_for()
    item._save_data()
    before = item.scripts["p"].model_dump()

    def fail(payloads):
        raise StorageError("synthetic provenance save failure")

    state.hook = lambda: monkeypatch.setattr(item._storage, "commit", fail)

    with pytest.raises(StorageError):
        item.merge_videos("p")

    assert item.scripts["p"].model_dump() == before


def test_concurrent_merge_publication_preserves_newer_url_and_source(runtime):
    item, state = runtime
    other_source = source_for("video/B.mp4", mix_settings={"dialogue": 90, "bgm": 35, "sfx": 60})

    def publish_other_merge():
        current = item.scripts["p"]
        current.merged_video_url = "video/other.mp4"
        current.merged_video_source = other_source
        item._commit_storage("projects.json")

    state.hook = publish_other_merge

    with pytest.raises(StorageConflictError):
        item.merge_videos("p")

    assert item.scripts["p"].merged_video_url == "video/other.mp4"
    assert item.scripts["p"].merged_video_source == other_source


def test_merge_ack_and_get_project_include_consistent_response_only_freshness(runtime):
    item, _ = runtime
    app = api.create_app(data_dir=item.data_dir, load_config=False, pipeline_instance=item)

    with TestClient(app) as client:
        merge_response = client.post("/projects/p/merge")
        assert merge_response.status_code == 200, merge_response.text
        merged_payload = merge_response.json()

        get_response = client.get("/projects/p")
        assert get_response.status_code == 200, get_response.text
        get_payload = get_response.json()

        assert merged_payload["merged_video_freshness"] == get_payload["merged_video_freshness"]
        assert merged_payload["merged_video_freshness"] == {
            "state": "current",
            "reason": "current",
            "project_revision": get_payload["revision"],
        }

    persisted = json.loads((item.data_dir / "projects.json").read_text())["p"]
    assert "merged_video_source" in persisted
    assert "merged_video_freshness" not in persisted


def test_empty_frame_source_cannot_report_current(runtime):
    item, _ = runtime
    item.merge_videos("p")
    script = item.scripts["p"]
    script.frames = []
    script.merged_video_source = {**source_for(), "frames": []}
    item._save_data()
    assert item.merge_video_freshness(item.read_committed_script("p"))["state"] != "current"


@pytest.mark.parametrize("output_ref", ["video/A.mp4", "projects.json", "video/merged_other_fixture.mp4"])
def test_source_proof_cannot_relabel_another_managed_file_as_this_projects_output(runtime, output_ref):
    item, _ = runtime
    item.merge_videos("p")
    path = item.data_dir / output_ref
    if not path.exists():
        path.write_bytes(b"not the merge result")
    item.scripts["p"].merged_video_url = output_ref
    item._save_data()
    assert item.merge_video_freshness(item.read_committed_script("p"))["state"] != "current"
