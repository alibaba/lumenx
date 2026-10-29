"""Short settings commands merge the current state and commit under one lock."""

import json
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event, current_thread
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api, pipeline as module
from src.apps.comic_gen.models import ModelSettings, Script
from src.utils.json_storage import StorageError


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    for name in ("AssetGenerator", "StoryboardGenerator", "VideoGenerator", "AudioGenerator", "ExportManager"):
        monkeypatch.setattr(module, name, Mock())
    processor = Mock()
    processor.create_draft_script.side_effect = lambda title, text: Script(
        id=str(uuid.uuid4()), title=title, original_text=text, created_at=1, updated_at=1,
    )
    item = module.ComicGenPipeline(data_dir=tmp_path / "data", script_processor=processor)
    yield item
    item.close()


def _create(pipeline, kind):
    if kind == "series":
        return pipeline.create_series("series")
    return pipeline.create_project("project", "original", skip_analysis=True)


def _endpoint(kind, item_id):
    if kind == "series":
        return f"/series/{item_id}/model_settings"
    return f"/projects/{item_id}/audio_mix"


def _file(pipeline, kind):
    return pipeline.data_dir / ("series.json" if kind == "series" else "projects.json")


def _route(pipeline, kind, item_id, updates):
    if kind == "series":
        return api.update_series_model_settings(item_id, api.UpdateModelSettingsRequest(**updates), pipeline)
    return api.update_audio_mix(item_id, api.AudioMixRequest(**updates), pipeline)


def _command(pipeline, kind, item_id, updates):
    if kind == "series":
        return pipeline.update_series_model_settings(item_id, updates)
    return pipeline.update_audio_mix(item_id, updates)


def _run_overlapping_edits(first, second, paused, release):
    """The second command may finish or block, depending on the implementation."""
    second_started = Event()
    def run_second():
        second_started.set()
        return second()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        try:
            assert paused.wait(5), "first command did not reach the read/modify window"
            second_future = pool.submit(run_second)
            assert second_started.wait(5)
            try:
                second_future.result(timeout=0.2)
            except TimeoutError:
                pass  # Correct implementations hold the command lock here.
        finally:
            release.set()
        first_future.result(timeout=5)
        second_future.result(timeout=5)


def test_series_model_patches_merge_after_an_overlapping_read(pipeline, monkeypatch):
    series = _create(pipeline, "series")
    paused, release = Event(), Event()
    original_copy = ModelSettings.model_copy
    def pause_first_copy(self, *, update=None, deep=False):
        result = original_copy(self, update=update, deep=deep)
        if update and update.get("t2i_model") == "first-image-model":
            paused.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(ModelSettings, "model_copy", pause_first_copy)
    _run_overlapping_edits(
        lambda: _route(pipeline, "series", series.id, {"t2i_model": "first-image-model"}),
        lambda: _route(pipeline, "series", series.id, {"i2v_model": "second-video-model"}),
        paused, release,
    )
    saved = json.loads(_file(pipeline, "series").read_text())[series.id]
    assert saved["model_settings"]["t2i_model"] == "first-image-model"
    assert saved["model_settings"]["i2v_model"] == "second-video-model"
    assert pipeline.series_store[series.id].model_settings.i2v_model == "second-video-model"


def test_audio_patches_merge_after_an_overlapping_read(pipeline):
    script = _create(pipeline, "project")
    initial = dict(script.mix_settings)
    paused, release = Event(), Event()
    class PauseFirstRead(Mapping):
        did_pause = False
        def __iter__(self):
            return iter(initial)
        def __len__(self):
            return len(initial)
        def __getitem__(self, key):
            value = initial[key]
            if key == "sfx" and not self.did_pause:
                self.did_pause = True
                paused.set()
                assert release.wait(5)
            return value
    # Same committed values, with an observable copy boundary for the race.
    script.mix_settings = PauseFirstRead()
    _run_overlapping_edits(
        lambda: _route(pipeline, "project", script.id, {"dialogue_volume": 20}),
        lambda: _route(pipeline, "project", script.id, {"bgm_volume": 70}),
        paused, release,
    )
    saved = json.loads(_file(pipeline, "project").read_text())[script.id]
    assert saved["mix_settings"] == {"dialogue": 20, "bgm": 70, "sfx": 60}
    assert script.mix_settings == saved["mix_settings"]


@pytest.mark.parametrize("kind", ["series", "project"])
def test_close_while_command_waits_cannot_change_live_state(pipeline, monkeypatch, kind):
    item = _create(pipeline, kind)
    before = item.model_dump()
    disk_before = _file(pipeline, kind).read_bytes()
    lock, attempted = pipeline._save_lock, Event()
    owner = current_thread()
    class ObserveLock:
        def __enter__(self):
            if current_thread() is not owner:
                attempted.set()
            lock.acquire()
            return self
        def __exit__(self, *args):
            lock.release()
    monkeypatch.setattr(pipeline, "_save_lock", ObserveLock())
    updates = {"t2i_model": "new-model"} if kind == "series" else {"bgm_url": "new.mp3", "dialogue_volume": 0}
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lock:
            pending = pool.submit(_route, pipeline, kind, item.id, updates)
            assert attempted.wait(5)
            pipeline.close()
        with pytest.raises(StorageError):
            pending.result(timeout=5)
    assert item.model_dump() == before
    assert _file(pipeline, kind).read_bytes() == disk_before


@pytest.mark.parametrize("updates,expected_bgm,expected_mix", [
    ({}, "old.mp3", {"dialogue": 80, "bgm": 35, "sfx": 60}),
    ({"bgm_url": None}, None, {"dialogue": 80, "bgm": 35, "sfx": 60}),
    ({"bgm_url": ""}, None, {"dialogue": 80, "bgm": 35, "sfx": 60}),
    ({"bgm_url": "new.mp3", "dialogue_volume": None}, "new.mp3", {"dialogue": 80, "bgm": 35, "sfx": 60}),
    ({"dialogue_volume": 0, "bgm_volume": None}, "old.mp3", {"dialogue": 0, "bgm": 35, "sfx": 60}),
    ({"bgm_volume": -2, "sfx_volume": 150}, "old.mp3", {"dialogue": 80, "bgm": 0, "sfx": 100}),
])
def test_audio_http_patch_preserves_omitted_null_and_zero_semantics(pipeline, updates, expected_bgm, expected_mix):
    script = _create(pipeline, "project")
    script.bgm_url = "old.mp3"
    script.mix_settings["dialogue"] = 80
    pipeline._save_data()
    updated_at = script.updated_at
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.put(_endpoint("project", script.id), json=updates)
        assert response.status_code == 200, response.text
    saved = json.loads(_file(pipeline, "project").read_text())[script.id]
    assert response.json()["bgm_url"] == saved["bgm_url"] == expected_bgm
    assert response.json()["mix_settings"] == saved["mix_settings"] == expected_mix
    assert response.json()["updated_at"] == updated_at
    assert response.json()["revision"] == saved["revision"]


def test_empty_audio_mix_still_restores_defaults(pipeline):
    script = _create(pipeline, "project")
    script.mix_settings = {}
    pipeline._save_data()
    response = _route(pipeline, "project", script.id, {})
    assert json.loads(response.body)["mix_settings"] == {"dialogue": 100, "bgm": 35, "sfx": 60}
    assert json.loads(_file(pipeline, "project").read_text())[script.id]["mix_settings"] == script.mix_settings


@pytest.mark.parametrize("updates", [{}, {"t2i_model": None}, {"t2i_model": ""}, {"i2v_model": "custom-video"}])
def test_series_http_patch_preserves_omitted_null_and_empty_string(pipeline, updates):
    series = _create(pipeline, "series")
    expected = series.model_settings.model_dump()
    expected.update({key: value for key, value in updates.items() if value is not None})
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.put(_endpoint("series", series.id), json=updates)
        assert response.status_code == 200, response.text
    saved = json.loads(_file(pipeline, "series").read_text())[series.id]
    assert response.json()["model_settings"] == saved["model_settings"] == expected
    assert response.json()["revision"] == saved["revision"]


@pytest.mark.parametrize("kind", ["series", "project"])
def test_settings_missing_resource_is_404(pipeline, kind):
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        assert client.put(_endpoint(kind, "missing"), json={}).status_code == 404


@pytest.mark.parametrize("kind", ["series", "project"])
def test_failed_settings_save_is_503_without_a_partial_commit(pipeline, monkeypatch, kind):
    item = _create(pipeline, kind)
    before = item.model_dump()
    disk_before = _file(pipeline, kind).read_bytes()
    commit = Mock(side_effect=StorageError("disk unavailable"))
    monkeypatch.setattr(pipeline._storage, "commit", commit)
    updates = {"t2i_model": "new-model"} if kind == "series" else {"bgm_url": "new.mp3", "dialogue_volume": 0}
    app = api.create_app(data_dir=pipeline.data_dir, load_config=False, pipeline_instance=pipeline)
    with TestClient(app) as client:
        response = client.put(_endpoint(kind, item.id), json=updates)
        assert response.status_code == 503, response.text
        assert client.put(_endpoint(kind, item.id), json=updates).status_code == 503
        current = pipeline.series_store[item.id] if kind == "series" else pipeline.scripts[item.id]
        assert current.model_dump() == before
    assert _file(pipeline, kind).read_bytes() == disk_before
    assert commit.call_count == 1


@pytest.mark.parametrize("kind", ["series", "project"])
def test_command_returns_detached_snapshot_and_noop_does_not_save(pipeline, monkeypatch, kind):
    item = _create(pipeline, kind)
    updates = {"t2i_model": "first-model"} if kind == "series" else {"dialogue_volume": 20}
    first = _command(pipeline, kind, item.id, updates)
    expected = first.model_dump()
    commit = Mock(wraps=pipeline._storage.commit)
    monkeypatch.setattr(pipeline._storage, "commit", commit)
    unchanged = _command(pipeline, kind, item.id, {})
    assert unchanged.model_dump() == expected
    commit.assert_not_called()
    second = {"t2i_model": "second-model"} if kind == "series" else {"dialogue_volume": 40}
    latest = _command(pipeline, kind, item.id, second)
    assert latest.revision == first.revision + 1
    assert first.model_dump() == expected
    assert unchanged.model_dump() == expected
    if kind == "series":
        first.model_settings.t2i_model = "local edit"
        assert pipeline.series_store[item.id].model_settings.t2i_model == "second-model"
    else:
        first.mix_settings["dialogue"] = 99
        assert item.mix_settings["dialogue"] == 40
