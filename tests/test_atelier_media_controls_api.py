"""Public media editing controls stay separate from paid execution."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apps.atelier.models import AtelierNode, AtelierProject
from src.apps.atelier.tts_commands import list_builtin_tts_voices
from src.apps.comic_gen.api import create_app


@pytest.mark.parametrize('kind', ['image', 'audio'])
def test_public_media_draft_preserves_incomplete_inputs_without_generation(tmp_path, monkeypatch, kind):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        app.state.atelier_storage.create_project(AtelierProject(id='p', title='Draft controls', created_at=1, updated_at=1,
            nodes=[AtelierNode(id='target', project_id='p', type=kind)]))
        schedule = Mock(side_effect=AssertionError('Draft must not schedule generation'))
        monkeypatch.setattr(app.state.atelier_generation_dispatcher, 'schedule', schedule)
        request = dict(expected_revision=0, media_kind=kind, prompt='', params={})
        if kind == 'image':
            request.update(model_id='wan/wan2.7-image-pro#image', mode='i2i', media=[], batch_size=2)
        else:
            request.update(voice='longanyang', params={'volume': 0})
        response = client.put('/atelier/projects/p/nodes/target/generation_draft', json=request)
        assert response.status_code == 200, response.text
        project = response.json()['project']
        assert project['nodes'][0]['prompt'] == ''
        assert project['nodes'][0]['data']['generation_draft']['media_kind'] == kind
        assert project['generation_attempts'] == project['generation_request_receipts'] == []
        assert client.put('/atelier/projects/p/nodes/target/generation_draft', json=request).status_code == 409
        assert not schedule.called


def test_builtin_voice_endpoint_is_readonly_and_credential_free(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False, recover_orphan_tasks=False)
    with TestClient(app) as client:
        response = client.get('/atelier/voices')
        assert response.status_code == 200, response.text
        assert response.json() == list_builtin_tts_voices()
        assert app.state.atelier_storage.list_projects() == []
