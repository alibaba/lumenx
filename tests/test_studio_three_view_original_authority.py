"""Original three-view receipts stay inert and their execution authority stays bound."""
from pathlib import Path
from copy import deepcopy
import hashlib

import pytest
from fastapi.testclient import TestClient

from tests import test_studio_three_view_core as first
from tests import test_studio_three_view_boundaries as boundary

runtime = first.runtime
transport = first.transport


def fingerprint_files(runtime):
    return {str(path.relative_to(runtime.data_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in runtime.data_dir.rglob('*') if path.is_file()}


def test_original_three_view_read_and_replay_survive_current_model_source_and_credential_loss(runtime, transport):
    first.setup(runtime, 'local', first.MODELS[0])
    first.gpt_sheet.queue_images(transport, 'http', count=1)
    body = first.raw()
    app = first.app_for(runtime)
    with TestClient(app) as client:
        first.gpt_sheet.configure(app, 'http')
        admitted = client.post(first.PUBLIC, json=body)
        assert admitted.status_code == 200, admitted.text
        original = boundary.current_record(runtime, 'local').model_dump(mode='json')
        member = original['members'][0]['execution']
        assert member['phase'] == 'completed'
        output = runtime.data_dir / member['output_ref']
        output_bytes = output.read_bytes()
        assert hashlib.sha256(output_bytes).hexdigest() == member['artifact']['sha256']
        with runtime._save_lock:
            project = runtime.scripts['p']
            project.model_settings.i2i_model = 'wan2.7-image'
            target = project.characters[0]
            target.full_body_asset.selected_id = None
            target.full_body_asset.variants = []
            target.full_body.selected_image_id = None
            target.full_body.image_variants = []
            boundary.commit_owners(runtime)
        (runtime.data_dir / first.SELECTED_REF).unlink()
        app.state.runtime_environment.update({}, remove_keys={'MULEROUTER_API_KEY'}, persist=lambda *_: None)
        before_files = fingerprint_files(runtime)
        before_calls = (len(transport['calls']), len(transport['cli']))
        observed = client.get(first.PUBLIC + '/' + body['client_request_id'])
        replay = client.post(first.PUBLIC, json=body)
        assert observed.status_code == replay.status_code == 200
        assert observed.json()['reason'] == replay.json()['reason'] == 'studio_image_request_inputs_changed'
        assert replay.json()['replayed'] is True
        assert observed.json()['request'] == replay.json()['request']
        assert boundary.current_record(runtime, 'local').model_dump(mode='json') == original
        assert fingerprint_files(runtime) == before_files
        assert output.read_bytes() == output_bytes
        assert (len(transport['calls']), len(transport['cli'])) == before_calls


@pytest.mark.parametrize('change', [
    {'reference_variant_id': None}, {'negative_prompt': None}, {'negative_prompt': ''},
])
def test_original_three_view_key_rejects_changed_raw_presence_without_side_effects(runtime, transport, change):
    first.setup(runtime, 'local', first.MODELS[0])
    first.gpt_sheet.queue_images(transport, 'http', count=1)
    body = first.raw()
    app = first.app_for(runtime)
    with TestClient(app) as client:
        first.gpt_sheet.configure(app, 'http')
        admitted = client.post(first.PUBLIC, json=body)
        assert admitted.status_code == 200, admitted.text
        original = boundary.current_record(runtime, 'local').model_dump(mode='json')
        assert original['members'][0]['execution']['phase'] == 'completed'
        before = fingerprint_files(runtime)
        calls = (len(transport['calls']), len(transport['cli']))
        conflict = client.post(first.PUBLIC, json=body | change)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()['code'] == 'storage_conflict'
        assert conflict.json()['commit_uncertain'] is False
        assert boundary.current_record(runtime, 'local').model_dump(mode='json') == original
        assert fingerprint_files(runtime) == before
        replay = client.post(first.PUBLIC, json=body)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert fingerprint_files(runtime) == before
        assert (len(transport['calls']), len(transport['cli'])) == calls


@pytest.mark.parametrize('authority,changed', [('http', True), ('token', True), ('profile', True), ('token', False)])
def test_real_three_view_admission_cannot_replace_its_bound_authority_before_worker(runtime, transport, monkeypatch, authority, changed):
    selected, protected = first.setup(runtime, 'global', first.MODELS[1])
    if not changed:
        first.gpt_sheet.queue_images(transport, authority, count=1)
    app = first.app_for(runtime)
    with TestClient(app) as client:
        first.gpt_sheet.configure(app, authority)
        service = runtime.asset_image_service
        original_worker = service.run_original_request
        boundary_observations = []

        def change_environment_then_run(request_id):
            accepted = boundary.current_record(runtime, 'global')
            execution = accepted.members[0].execution
            assert accepted.id == request_id and execution.phase == 'accepted'
            assert not transport['calls'] and not transport['cli']
            original_identity = deepcopy((execution.attempt_id, execution.fingerprint, execution.scope, execution.credential_ref))
            environment = app.state.runtime_environment
            if changed:
                if authority == 'profile':
                    home = Path(environment.snapshot()['LUMENX_MULERUN_HOME'])
                    (home / '.mulerun/session.json').write_text('synthetic changed profile content')
                else:
                    key = 'MULEROUTER_API_KEY' if authority == 'http' else 'MULERUN_TOKEN'
                    environment.update({key: 'synthetic-' + authority + '-replacement'}, persist=lambda *_: None)
            if authority != 'http':
                environment.update({'MULEROUTER_API_KEY': first.gpt_sheet.HTTP_SECRET}, persist=lambda *_: None)
            result = original_worker(request_id)
            final = boundary.current_record(runtime, 'global').members[0].execution
            assert (final.attempt_id, final.fingerprint, final.scope, final.credential_ref) == original_identity
            boundary_observations.append(final.phase)
            return result

        monkeypatch.setattr(service, 'run_original_request', change_environment_then_run)
        accepted = client.post(first.PUBLIC, json=first.raw())
        assert accepted.status_code == 200, accepted.text
        assert boundary_observations == ['cancelled' if changed else 'completed']
        final = boundary.current_record(runtime, 'global').members[0].execution
        assert final.scope['gateway'] == {'http': 'mulerouter_http', 'token': 'mulerun_token', 'profile': 'mulerun_profile'}[authority]
        assert final.media[0]['sha256'] == hashlib.sha256(selected).hexdigest()
        if changed:
            assert not transport['calls'] and not transport['cli']
            assert final.artifact is None and final.prepared_artifact is None
            assert boundary.current_target(runtime, 'global').model_dump(mode='json', include=first.PROTECTED_FIELDS) == protected
        else:
            assert len(transport['cli']) == 1 and len(transport['calls']) == 1
            assert transport['calls'][0][0].method == 'GET'
            assert final.artifact is not None
            assert hashlib.sha256((runtime.data_dir / final.output_ref).read_bytes()).hexdigest() == final.artifact['sha256']
        assert not transport['queue'] and not transport['cli_queue']
