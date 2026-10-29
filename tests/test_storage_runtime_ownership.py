"""Actual application wiring across Studio and Playground storage owners."""

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.apps.playground.models import PlaygroundGeneration, PlaygroundOutput, PlaygroundTemplate
from src.utils.json_storage import CorruptStorageError, DirectoryWriterLock, StorageConflictError, StorageError


def test_real_app_shares_one_lease_and_closes_playground_first(tmp_path):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app):
        pipeline = app.state.pipeline
        storage = app.state.playground_storage
        with pytest.raises(StorageConflictError):
            DirectoryWriterLock(tmp_path).acquire()
        assert pipeline._writer_lock is storage._writer_lock
    with pytest.raises(StorageError):
        storage.add_template(PlaygroundTemplate(id='late', name='late', prompt='late', created_at='now', updated_at='now'))
    assert not (tmp_path / 'playground_templates.json').exists()
    with DirectoryWriterLock(tmp_path):
        pass


def test_failed_playground_load_releases_pipeline_lease(tmp_path):
    (tmp_path / 'playground_history.json').write_text('{')
    with pytest.raises(CorruptStorageError):
        with TestClient(create_app(data_dir=tmp_path, load_config=False)):
            pytest.fail('corrupt history should stop startup')
    with DirectoryWriterLock(tmp_path):
        pass


def test_failed_playground_import_releases_created_pipeline(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == 'playground.service':
            raise ImportError('injected service import failure')
        return original(name, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(builtins, '__import__', failing_import)
        with pytest.raises(ImportError, match='injected service') as error:
            with TestClient(create_app(data_dir=tmp_path, load_config=False)):
                pytest.fail('startup should fail')
    # Retain the exception/traceback: ownership cannot depend on GC timing.
    assert error.value is not None
    with DirectoryWriterLock(tmp_path):
        pass


def test_studio_failed_save_prevents_next_playground_payment(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    with TestClient(app) as client:
        def fail(*args):
            raise StorageError('full disk')
        monkeypatch.setattr(app.state.pipeline._storage, 'commit', fail)
        assert client.post('/series', json={'title': 'not saved'}).status_code == 503
        calls = []
        monkeypatch.setattr(app.state.playground_service, 'process_generation', lambda *_: calls.append('paid'))
        response = client.post('/playground/generate', json={'client_request_id': 'storage-fault-request', 'mode': 't2i', 'model_id': 'fake', 'prompt': 'new fee'})
        assert response.status_code == 503, response.text
        assert calls == []


def test_library_retry_after_history_fault_uses_same_real_asset_after_restart(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, load_config=False)
    source = tmp_path / 'playground/images/result.png'
    with TestClient(app):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'already paid image')
        app.state.playground_storage.add_generation(PlaygroundGeneration(
            id='gen', mode='t2i', model_id='fake', prompt='image', status='completed', created_at='now',
            outputs=[PlaygroundOutput(id='out', media_path='playground/images/result.png', media_type='image')],
        ))
        with monkeypatch.context() as fault:
            fault.setattr(app.state.playground_storage, 'mark_output_saved', lambda *_: (_ for _ in ()).throw(StorageError('history full')))
            with pytest.raises(StorageError):
                app.state.playground_service.save_to_library('gen', 'out', 'character')
        original = app.state.pipeline.library_store.characters[0]
        asset_id = original.id
        assert (tmp_path / original.reference_sheet.image_variants[0].url).read_bytes() == source.read_bytes()
    with TestClient(create_app(data_dir=tmp_path, load_config=False)) as client:
        response = client.post('/playground/history/gen/outputs/out/save-to-library', json={'category': 'scene'})
        assert response.status_code == 200, response.text
        assets = client.get('/library/assets').json()
        assert [asset['id'] for asset in assets['characters']] == [asset_id]
        assert assets['scenes'] == []
        assert client.get('/playground/history/gen').json()['outputs'][0]['saved_to_library'] is True
        assert source.read_bytes() == b'already paid image'
