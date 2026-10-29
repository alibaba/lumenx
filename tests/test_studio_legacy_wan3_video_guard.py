"""Legacy Studio /video_tasks must not accept ordered Wan3 models."""

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen import api
from tests.test_studio_provider_recovery_dispatcher import _make_runtime


def _app(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path, monkeypatch)
    scheduled = []
    monkeypatch.setattr(api.BackgroundTasks, 'add_task', lambda self, func, *args, **kwargs: scheduled.append((func, args)))
    app = api.create_app(data_dir=runtime.data_dir, pipeline_instance=runtime, load_config=False, recover_orphan_tasks=False)
    return runtime, app, scheduled


def _legacy_payload(**changes):
    payload = {
        'image_url': 'uploads/source.png',
        'prompt': 'make a cinematic shot',
        'model': 'wan2.7-i2v',
        'frame_id': 'frame',
        'resolution': '720p',
        'duration': 5,
        'generate_audio': False,
        'prompt_extend': False,
        'batch_size': 1,
    }
    payload.update(changes)
    return payload


@pytest.mark.parametrize('model', ['wan3.0-video', 'wan3.0-video-prime', 'wan/wan3.0-video#video'])
def test_legacy_video_tasks_reject_ordered_wan3_without_mutation(tmp_path, monkeypatch, model):
    runtime, app, scheduled = _app(tmp_path, monkeypatch)
    before = (runtime.data_dir / 'projects.json').read_bytes()

    with TestClient(app) as client:
        response = client.post('/projects/p/video_tasks', json=_legacy_payload(model=model))
        assert response.status_code == 400, response.text
        assert 'ordered-media' in response.json()['detail']
        assert runtime.read_committed_script('p').video_tasks == []
        assert scheduled == []
    assert (runtime.data_dir / 'projects.json').read_bytes() == before
    runtime.close()


def test_legacy_video_tasks_still_accept_supported_i2v_model(tmp_path, monkeypatch):
    runtime, app, scheduled = _app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        response = client.post('/projects/p/video_tasks', json=_legacy_payload(model='wan2.7-i2v'))
        assert response.status_code == 200, response.text
        saved = runtime.read_committed_script('p')
        assert len(saved.video_tasks) == 1
        assert saved.video_tasks[0].model == 'wan2.7-i2v'
        assert len(scheduled) == 1
    runtime.close()
