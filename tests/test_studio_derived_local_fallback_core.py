"""Local fallback regression: real app/Core/owner storage, external transport only.

Source discriminators are not synthetic
variant IDs: old full_body_image_url has no variant ID. Prompt/style snapshots,
raw source selection, durable replay and projection are observed independently.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient

from src.apps.comic_gen.asset_image_execution import AssetImageRequestCommand, image_input_guard
from src.apps.comic_gen import assets
from src.apps.comic_gen.models import AssetUnit, ImageVariant
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.utils.runtime_environment import RuntimeEnvironment
from src.utils.json_storage import StorageError
from tests import test_studio_core_image_admission as admission
from tests import test_studio_gpt_reference_sheet_core as gpt_sheet
from tests import test_studio_headshot_core as headshot
from tests import test_studio_three_view_core as first
from tests import test_studio_three_view_boundaries as boundary
from tests.frozen_image_fixtures import rgb_png_bytes

runtime = first.runtime
transport = gpt_sheet.transport
CHANNELS = ('three_view', 'headshot')
SOURCES = ('legacy-url', 'other-upload', 'own-upload')
SOURCE_FIELDS = {
    'three_view': ('three_view_asset', 'three_views'),
    'headshot': ('headshot_asset', 'head_shot'),
}


def setup(item, channel, source, route='local'):
    # The qualified and short GPT IDs both remain exercised across the owners.
    headshot.setup_headshot(item, route, first.MODELS[1 if route == 'series' else 0], source='legacy-url')
    target = admission.owner(item, route).characters[0]
    other = 'headshot' if channel == 'three_view' else 'three_view'
    target.full_body_image_url = 'uploads/legacy-a.png' if source == 'legacy-url' else None
    for name in CHANNELS:
        asset_field, unit_field = SOURCE_FIELDS[name]
        images = getattr(target, asset_field)
        assert images is not None
        # Own source is tested with a nonempty OTHER channel that has no upload.
        for variant in images.variants:
            variant.is_uploaded_source = variant.is_uploaded_source and (source != 'own-upload' or name == channel)
        getattr(target, unit_field).image_variants = [v.model_copy(deep=True) for v in images.variants]
    if source == 'legacy-url':
        discriminator, variant_id, ref = 'full_body_image_url', None, target.full_body_image_url
    else:
        name = other if source == 'other-upload' else channel
        images = getattr(target, SOURCE_FIELDS[name][0])
        variant = next(v for v in images.variants if v.is_uploaded_source)
        discriminator, variant_id, ref = name + '_upload', variant.id, variant.url
    assert ref is not None
    item._commit_storage('projects.json', 'series.json', 'library_assets.json')
    return target, {'reference_source': discriminator, 'reference_image_url': ref,
                    **({'reference_variant_id': variant_id} if variant_id is not None else {})}


def body(channel, reference, **changes):
    return {'client_request_id': 'fallback-original', 'asset_id': 'c', 'asset_type': 'character',
            'generation_type': channel, 'prompt': 'Keep authored local-reference draft',
            'apply_style': True, 'style_prompt': 'one style suffix', 'batch_size': 1,
            **reference, **changes}


def image_state(target, channel):
    asset_field, unit_field = SOURCE_FIELDS[channel]
    return target.model_dump(mode='json', include={asset_field, unit_field, channel + '_image_url',
        channel + '_prompt', channel + '_updated_at', *({'avatar_url'} if channel == 'headshot' else set())})


@pytest.mark.parametrize('channel', CHANNELS)
@pytest.mark.parametrize('source', SOURCES)
@pytest.mark.parametrize('route', ['local', 'series', 'global'])
def test_strict_fallback_freezes_first_raw_source_at_actual_owner_and_keeps_dual_history(runtime, transport, channel, source, route):
    target, reference = setup(runtime, channel, source, route)
    protected = target.model_dump(mode='json')
    old = image_state(target, channel)
    ref_bytes = (runtime.data_dir / reference['reference_image_url']).read_bytes()
    raw = body(channel, reference)
    gpt_sheet.queue_images(transport, 'http', count=1)
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        accepted = client.post(first.PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()['request']['client_request_id'] == raw['client_request_id']
        headshot.assert_wire(transport, ref_bytes, raw['prompt'] + ', one style suffix', strict=True)
        saved = headshot.saved_owner(runtime, route)
        assert len(saved.asset_image_requests) == 1
        record = saved.asset_image_requests[0]
        assert (record.request_root_kind, record.request_root_id) == ('project', 'p')
        assert (record.owner_kind, record.owner_id) == {'local': ('script', 'p'), 'series': ('series', 's'), 'global': ('global', 'library')}[route]
        assert record.channel == channel and record.base_prompt == raw['prompt']
        execution = record.members[0].execution
        assert execution.phase == 'completed' and execution.intent['intent_contract_version'] == first.CONTRACT
        assert execution.media[0]['sha256'] == hashlib.sha256(ref_bytes).hexdigest()
        assert execution.media[0]['size_bytes'] == len(ref_bytes)
        target = saved.characters[0]
        asset_field, unit_field = SOURCE_FIELDS[channel]
        images, unit = getattr(target, asset_field), getattr(target, unit_field)
        assert images is not None and unit is not None
        assert images.selected_id == unit.selected_image_id == record.members[0].variant_id
        variants = {v.id: v.model_dump(mode='json') for v in images.variants}
        assert variants == {v.id: v.model_dump(mode='json') for v in unit.image_variants}
        for value in old[asset_field]['variants']:
            assert variants[value['id']] == value
        assert getattr(target, channel + '_image_url') == execution.output_ref
        assert getattr(target, channel + '_prompt') == unit.image_prompt == raw['prompt']
        assert variants[images.selected_id]['prompt_used'] == raw['prompt'] + ', one style suffix'
        assert target.is_consistent is protected['is_consistent']
        if channel == 'headshot':
            assert target.avatar_url == execution.output_ref
        changed = {asset_field, unit_field, channel + '_image_url', channel + '_prompt', channel + '_updated_at',
                   channel + '_request_id', 'status'} | ({'avatar_url'} if channel == 'headshot' else set())
        assert target.model_dump(mode='json', exclude=changed) == {k: v for k, v in protected.items() if k not in changed}
        for field in headshot.MOTION_FIELDS:
            assert getattr(unit, field) == getattr(AssetUnit.model_validate(old[unit_field]), field)
        before = gpt_sheet.persisted(runtime)
        read = client.get(first.PUBLIC + '/' + raw['client_request_id'])
        replay = client.post(first.PUBLIC, json=raw)
        assert read.status_code == replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['request'] == read.json()['request']
        assert [m['status'] for m in read.json()['request']['members']] == ['completed']
        assert gpt_sheet.persisted(runtime) == before and len(transport['calls']) == 3


@pytest.mark.parametrize('channel', CHANNELS)
def test_base_character_uses_its_own_local_fallback_through_core(runtime, transport, channel):
    target, reference = setup(runtime, channel, 'legacy-url')
    target.base_character_id = 'parent'
    boundary.commit_owners(runtime)
    ref_bytes = (runtime.data_dir / reference['reference_image_url']).read_bytes()
    raw = body(channel, reference, apply_style=False)
    assert 'reference_variant_id' not in raw
    gpt_sheet.queue_images(transport, 'http', count=1)
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        accepted = client.post(first.PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        headshot.assert_wire(transport, ref_bytes, raw['prompt'], strict=True)
        saved = runtime._load_data()['p']
        record = saved.asset_image_requests[0]
        assert record.channel == channel and record.members[0].execution.phase == 'completed'
        assert record.members[0].execution.media[0]['sha256'] == hashlib.sha256(ref_bytes).hexdigest()
        assert saved.characters[0].base_character_id == 'parent'
        assert saved.characters[0].full_body_image_url == reference['reference_image_url']
        before = gpt_sheet.persisted(runtime)
        read = client.get(first.PUBLIC + '/' + raw['client_request_id'])
        replay = client.post(first.PUBLIC, json=raw)
        assert read.status_code == replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['request'] == read.json()['request']
        assert gpt_sheet.persisted(runtime) == before and len(transport['calls']) == 3


@pytest.mark.parametrize('channel', CHANNELS)
@pytest.mark.parametrize('source', SOURCES)
def test_existing_adapter_uses_same_core_fallback_contract_without_losing_task_shape(runtime, transport, channel, source):
    target, reference = setup(runtime, channel, source)
    if source == 'legacy-url':
        # Derived channels use this character's own source even when it has a parent.
        target.base_character_id = 'parent'
        boundary.commit_owners(runtime)
    ref_bytes = (runtime.data_dir / reference['reference_image_url']).read_bytes()
    gpt_sheet.queue_images(transport, 'http', count=1)
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        accepted = client.post('/projects/p/assets/generate', json={
            'asset_id': 'c', 'asset_type': 'character', 'generation_type': channel,
            'prompt': 'Explicit adapter prompt', 'apply_style': False, 'batch_size': 1,
        })
        assert accepted.status_code == 200, accepted.text
        headshot.assert_wire(transport, ref_bytes, 'Explicit adapter prompt', strict=True)
        record = runtime._load_data()['p'].asset_image_requests[0]
        assert accepted.json()['_task_id'] == record.id
        assert runtime.asset_generation_tasks[record.id]['_core_image_request_id'] == record.id
        assert '_context' not in runtime.asset_generation_tasks[record.id]
        status = client.get('/tasks/' + record.id)
        assert status.status_code == 200 and status.json()['status'] == 'completed'


@pytest.mark.parametrize('channel', CHANNELS)
@pytest.mark.parametrize('case', ['wrong-source', 'wrong-id', 'wrong-url', 'dangling-selected', 'duplicate-upload', 'nonlocal'])
def test_fallback_rejects_changed_or_ambiguous_original_source_before_paid_effects(runtime, transport, channel, case):
    target, reference = setup(runtime, channel, 'other-upload')
    raw = body(channel, reference)
    if case == 'wrong-source':
        raw['reference_source'] = channel + '_upload'
    elif case == 'wrong-id':
        raw['reference_variant_id'] = 'second-not-first'
    elif case == 'wrong-url':
        raw['reference_image_url'] = 'uploads/legacy-a.png'
    elif case == 'dangling-selected':
        target.full_body_asset.selected_id = 'missing-selected'
    elif case == 'duplicate-upload':
        images = target.headshot_asset if channel == 'three_view' else target.three_view_asset
        reference_variant = next(v for v in images.variants if v.is_uploaded_source)
        images.variants.append(reference_variant.model_copy(deep=True))
    else:
        images = target.headshot_asset if channel == 'three_view' else target.three_view_asset
        source = next(v for v in images.variants if v.is_uploaded_source)
        source.url = raw['reference_image_url'] = 'https://example.invalid/out-of-scope.png'
    boundary.commit_owners(runtime)
    before, media = gpt_sheet.persisted(runtime), boundary.saved_files(runtime)
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        rejected = client.post(first.PUBLIC, json=raw)
        assert rejected.status_code == 422, rejected.text
    assert gpt_sheet.persisted(runtime) == before and boundary.saved_files(runtime) == media
    assert not transport['calls'] and not transport['cli']
    assert not runtime.asset_generation_tasks


@pytest.mark.parametrize('channel', CHANNELS)
@pytest.mark.parametrize('source', SOURCES)
def test_unknown_fallback_ack_replays_original_raw_after_source_change_and_reopen_without_paying(runtime, transport, channel, source):
    target, reference = setup(runtime, channel, source)
    old = image_state(target, channel)
    raw = body(channel, reference, batch_size=2)
    transport['queue'].append(requests.ConnectionError('synthetic response lost after paid submit'))
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        accepted = client.post(first.PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        record = runtime._load_data()['p'].asset_image_requests[0]
        assert [m.execution.phase for m in record.members] == ['submitting', 'cancelled']
        assert image_state(runtime.scripts['p'].characters[0], channel) == old
        target = runtime.scripts['p'].characters[0]
        target.full_body_asset.selected_id = first.SELECTED_ID
        boundary.commit_owners(runtime)
        original_id = record.id
        before = gpt_sheet.persisted(runtime)
        replay = client.post(first.PUBLIC, json=raw)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['request']['id'] == original_id
        changed = client.post(first.PUBLIC, json={**raw, 'reference_source': 'full_body_image_url'})
        # Even if the source changed on disk, the same original key cannot be repurposed.
        if raw['reference_source'] == 'full_body_image_url':
            changed = client.post(first.PUBLIC, json={**raw, 'reference_variant_id': None})
        assert changed.status_code == 409, changed.text
        assert gpt_sheet.persisted(runtime) == before and len(transport['calls']) == 1
    runtime.close()
    reopened = ComicGenPipeline(data_dir=runtime.data_dir, runtime_environment=RuntimeEnvironment({}))
    try:
        with TestClient(first.app_for(reopened)) as client:
            before = gpt_sheet.persisted(reopened)
            read = client.get(first.PUBLIC + '/' + raw['client_request_id'])
            replay = client.post(first.PUBLIC, json=raw)
            assert read.status_code == replay.status_code == 200 and replay.json()['replayed'] is True
            assert read.json()['request']['id'] == original_id
            assert reopened.asset_image_service.run_original_request(original_id) is None
            assert gpt_sheet.persisted(reopened) == before and len(transport['calls']) == 1
            assert not transport['cli']
    finally:
        reopened.close()


def persisted_record(item, route='local'):
    return headshot.saved_owner(item, route).asset_image_requests[0]


def assert_bound_proof(item, execution):
    assert execution.output_ref is not None and execution.prepared_artifact is not None
    proof = execution.prepared_artifact
    content = (item.data_dir / execution.output_ref).read_bytes()
    assert (hashlib.sha256(content).hexdigest(), len(content)) == (proof['sha256'], proof['size_bytes'])
    assert proof['output_ref'] == execution.output_ref and proof['attempt_id'] == execution.attempt_id
    if execution.schema_version == 3:
        assert execution.handle is not None and proof['handle'] == execution.handle
        assert execution.handle['task_id'] == gpt_sheet.http_task_id()
        assert execution.observation is not None and execution.observation['status'] == 'succeeded'
        assert execution.observation['source_handle'] == execution.handle
    return content


def channel_history(target, channel):
    asset_field, unit_field = SOURCE_FIELDS[channel]
    images, unit = getattr(target, asset_field), getattr(target, unit_field)
    assert images is not None and unit is not None
    return images, unit


@pytest.mark.parametrize('channel', CHANNELS)
@pytest.mark.parametrize('source', SOURCES)
def test_fallback_acceptance_freezes_bytes_before_same_path_edit_and_paid_source_removal(runtime, transport, channel, source):
    _, reference = setup(runtime, channel, source)
    path = runtime.data_dir / reference['reference_image_url']
    original = path.read_bytes()
    replacement = rgb_png_bytes('fallback later bytes', width=16, height=16)
    assert original != replacement
    raw = body(channel, reference)
    gpt_sheet.queue_images(transport, 'http', count=1)
    removed = []

    def remove_paid_source(request):
        if request.method == 'GET' and request.url != gpt_sheet.IMAGE_URL:
            transport['on_send'] = None
            assert len(boundary.paid_posts(transport)) == 1
            assert path.read_bytes() == replacement
            path.unlink()
            removed.append(reference['reference_image_url'])

    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        admitted = runtime.asset_image_service.admit('project', 'p', AssetImageRequestCommand.model_validate(raw))
        saved = persisted_record(runtime)
        assert saved.id == admitted.request.id and saved.members[0].execution.phase == 'accepted'
        assert saved.members[0].execution.media[0]['sha256'] == hashlib.sha256(original).hexdigest()
        assert not transport['calls']
        path.write_bytes(replacement)
        transport['on_send'] = remove_paid_source
        runtime.asset_image_service.run_original_request(saved.id)
        final = persisted_record(runtime)
        execution = final.members[0].execution
        assert execution.phase == 'completed' and execution.media == saved.members[0].execution.media
        assert_bound_proof(runtime, execution)
        headshot.assert_wire(transport, original, raw['prompt'] + ', one style suffix', strict=True)
        assert removed == [reference['reference_image_url']] and not path.exists()
        before = gpt_sheet.persisted(runtime)
        replay = client.post(first.PUBLIC, json=raw)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['request']['id'] == saved.id and final.request_fingerprint == saved.request_fingerprint
        assert gpt_sheet.persisted(runtime) == before and len(transport['calls']) == 3
        assert len(boundary.paid_posts(transport)) == 1


@pytest.mark.parametrize('channel', CHANNELS)
@pytest.mark.parametrize('effect', ['source', 'select', 'clear'])
@pytest.mark.parametrize('edge', ['query', 'fetch'])
def test_fallback_paid_late_result_preserves_source_choice_author_and_receipt(runtime, transport, channel, effect, edge):
    route = 'series' if channel == 'three_view' else 'local'
    _, reference = setup(runtime, channel, 'other-upload', route)
    original = (runtime.data_dir / reference['reference_image_url']).read_bytes()
    raw = body(channel, reference)
    captured: dict[str, Any] = {}
    gpt_sheet.queue_images(transport, 'http', count=1)

    def author_changes(request):
        matches = request.method == 'GET' and ((request.url == gpt_sheet.IMAGE_URL) == (edge == 'fetch'))
        if not matches:
            return
        transport['on_send'] = None
        assert len(boundary.paid_posts(transport)) == 1
        record = persisted_record(runtime, route)
        execution = record.members[0].execution
        assert execution.phase == ('submitted' if edge == 'query' else 'observed')
        assert execution.handle is not None and execution.handle['task_id'] == gpt_sheet.http_task_id()
        with runtime._save_lock:
            target = admission.owner(runtime, route).characters[0]
            images, unit = channel_history(target, channel)
            if effect == 'source':
                other = 'headshot' if channel == 'three_view' else 'three_view'
                other_images, other_unit = channel_history(target, other)
                first_upload = next(v for v in other_images.variants if v.is_uploaded_source)
                moved = first_upload.model_copy(deep=True)
                assert (moved.id, moved.url) == (reference['reference_variant_id'], reference['reference_image_url'])
                for variant in other_images.variants:
                    variant.is_uploaded_source = False
                other_unit.image_variants = [v.model_copy(deep=True) for v in other_images.variants]
                assert all(v.id != moved.id for v in images.variants)
                images.variants.insert(0, moved)
                unit.image_variants = [v.model_copy(deep=True) for v in images.variants]
                assert getattr(assets, 'derived_image_fallback_reference')(target, channel) == (channel + '_upload', moved.id, moved.url)
                # Same raw URL and variant ID, but a different source channel
                # must change the input guard even without editing the prompt.
                assert image_input_guard(target, channel) != record.input_guard
            else:
                selected = next(v for v in images.variants if v.id != images.selected_id) if effect == 'select' else None
                images.selected_id = unit.selected_image_id = selected.id if selected else None
                setattr(target, channel + '_image_url', selected.url if selected else None)
            # The single source/selection edit must suffice; leave prompt,
            # timestamps, avatar and other author fields exactly as they were.
            boundary.commit_owners(runtime)
            captured.update(target=target.model_dump(mode='json'), media=boundary.saved_files(runtime),
                            input_bytes=(runtime.data_dir / reference['reference_image_url']).read_bytes(),
                            attempt=execution.attempt_id, fingerprint=execution.fingerprint, handle=deepcopy(execution.handle))

    transport['on_send'] = author_changes
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        accepted = client.post(first.PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        assert captured
        record = persisted_record(runtime, route)
        execution = record.members[0].execution
        assert execution.schema_version == 3 and execution.phase == 'observed' and execution.artifact is None
        assert (execution.attempt_id, execution.fingerprint, execution.handle) == (captured['attempt'], captured['fingerprint'], captured['handle'])
        paid_bytes = assert_bound_proof(runtime, execution)
        assert admission.owner(runtime, route).characters[0].model_dump(mode='json') == captured['target']
        assert boundary.saved_files(runtime) == captured['media']
        assert (runtime.data_dir / reference['reference_image_url']).read_bytes() == captured['input_bytes'] == original
        assert record.members[0].variant_id not in [v['id'] for v in captured['target'][SOURCE_FIELDS[channel][0]]['variants']]
        headshot.assert_wire(transport, original, raw['prompt'] + ', one style suffix', strict=True)
        before = gpt_sheet.persisted(runtime)
        replay = client.post(first.PUBLIC, json=raw)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['request']['members'][0]['status'] == 'needs_confirmation'
        assert runtime.asset_image_service.run_original_request(record.id) is None
        runtime.asset_image_service.repair_request('project', 'p', raw['client_request_id'])
        assert gpt_sheet.persisted(runtime) == before
        assert (runtime.data_dir / execution.output_ref).read_bytes() == paid_bytes
        assert len(transport['calls']) == 3 and len(boundary.paid_posts(transport)) == 1
        assert not transport['queue'] and not transport['cli'] and not transport['cli_queue']


@pytest.mark.parametrize('channel', CHANNELS)
@pytest.mark.parametrize('selected', [True, False], ids=['keep-old', 'keep-empty'])
def test_fallback_real_prepared_checkpoint_repairs_without_selecting_or_resubmitting(runtime, transport, monkeypatch, channel, selected):
    target, reference = setup(runtime, channel, 'other-upload')
    images, unit = channel_history(target, channel)
    if not selected:
        images.selected_id = unit.selected_image_id = None
        setattr(target, channel + '_image_url', None)
    target.avatar_url = 'uploads/legacy-a.png'
    boundary.commit_owners(runtime)
    asset_field, unit_field = SOURCE_FIELDS[channel]
    before_images, before_unit = images.model_dump(mode='json'), unit.model_dump(mode='json')
    author = target.model_dump(mode='json', exclude={asset_field, unit_field, channel + '_request_id', 'status'})
    old_bytes = {v['url']: (runtime.data_dir / v['url']).read_bytes() for v in before_images['variants'] + before_unit['image_variants']}
    source_bytes = (runtime.data_dir / reference['reference_image_url']).read_bytes()
    raw = body(channel, reference)
    gpt_sheet.queue_images(transport, 'http', count=1)
    original_commit = runtime._storage.commit
    failures = []

    def fail_only_completed_checkpoint(payload):
        records = payload.get('projects.json', {}).get('p', {}).get('asset_image_requests', [])
        if records and records[0]['members'][0]['execution']['phase'] == 'completed':
            failures.append('completed')
            raise StorageError('synthetic 1a completed checkpoint failure')
        return original_commit(payload)

    with TestClient(first.app_for(runtime), raise_server_exceptions=False) as client:
        gpt_sheet.configure(client.app, 'http')
        with monkeypatch.context() as patch:
            patch.setattr(runtime._storage, 'commit', fail_only_completed_checkpoint)
            accepted = client.post(first.PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        assert failures == ['completed']
        record = persisted_record(runtime)
        execution = record.members[0].execution
        assert execution.schema_version == 3 and execution.phase == 'observed' and execution.artifact is None
        paid_bytes = assert_bound_proof(runtime, execution)
        frozen_execution = execution.model_dump(mode='json')
        proof = deepcopy(execution.prepared_artifact)
        assert proof is not None
        headshot.assert_wire(transport, source_bytes, raw['prompt'] + ', one style suffix', strict=True)
    calls = len(transport['calls'])
    runtime.close()
    reopened = ComicGenPipeline(data_dir=runtime.data_dir, runtime_environment=RuntimeEnvironment({}), recover_orphan_tasks=True)
    try:
        with TestClient(first.app_for(reopened)) as client:
            restored = persisted_record(reopened)
            repaired = restored.members[0].execution
            assert restored.id == record.id and restored.request_fingerprint == record.request_fingerprint
            assert repaired.phase == 'completed' and repaired.artifact == repaired.prepared_artifact == proof
            for field in ['handle', 'observation', 'attempt_id', 'fingerprint', 'scope', 'credential_ref', 'intent', 'media', 'output_ref']:
                assert getattr(repaired, field) == frozen_execution[field]
            assert (reopened.data_dir / repaired.output_ref).read_bytes() == paid_bytes
            target = reopened.scripts['p'].characters[0]
            assert target.model_dump(mode='json', exclude={asset_field, unit_field, channel + '_request_id', 'status'}) == author
            images, unit = channel_history(target, channel)
            expected = ImageVariant.model_validate({'id': restored.members[0].variant_id, 'url': repaired.output_ref,
                'created_at': restored.created_at, 'prompt_used': repaired.intent['prompt']}).model_dump(mode='json')
            for actual, previous, key in [(images.model_dump(mode='json'), before_images, 'variants'),
                                          (unit.model_dump(mode='json'), before_unit, 'image_variants')]:
                assert {k: v for k, v in actual.items() if k != key} == {k: v for k, v in previous.items() if k != key}
                assert actual[key] == previous[key] + [expected]
            assert images.selected_id == unit.selected_image_id == before_images['selected_id']
            assert {ref: (reopened.data_dir / ref).read_bytes() for ref in old_bytes} == old_bytes
            before = gpt_sheet.persisted(reopened)
            replay = client.post(first.PUBLIC, json=raw)
            assert replay.status_code == 200 and replay.json()['replayed'] is True
            assert replay.json()['request']['members'][0]['status'] == 'completed'
            assert reopened.asset_image_service.run_original_request(restored.id) is None
            reopened.asset_image_service.repair_request('project', 'p', raw['client_request_id'])
            assert gpt_sheet.persisted(reopened) == before and len(transport['calls']) == calls == 3
            assert not transport['queue'] and not transport['cli'] and not transport['cli_queue']
    finally:
        reopened.close()


@pytest.mark.parametrize('channel', CHANNELS)
@pytest.mark.parametrize('entry', ['strict', 'legacy'])
@pytest.mark.parametrize('bad', ['missing', 'symlink'])
def test_fallback_first_declared_bad_upload_never_uses_later_valid_upload(runtime, transport, channel, entry, bad):
    target, reference = setup(runtime, channel, 'other-upload')
    other = 'headshot' if channel == 'three_view' else 'three_view'
    images, unit = channel_history(target, other)
    uploads = [v for v in images.variants if v.is_uploaded_source]
    assert len(uploads) >= 2 and (runtime.data_dir / uploads[1].url).is_file()
    bad_ref = 'uploads/first-declared-invalid.png'
    uploads[0].url = bad_ref
    unit.image_variants = [v.model_copy(deep=True) for v in images.variants]
    if bad == 'symlink':
        (runtime.data_dir / bad_ref).symlink_to(runtime.data_dir / uploads[1].url)
    reference['reference_image_url'] = bad_ref
    boundary.commit_owners(runtime)
    gpt_sheet.queue_images(transport, 'http', count=1)
    pending = list(transport['queue'])
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        before, frozen = gpt_sheet.persisted(runtime), boundary.saved_files(runtime)
        raw = body(channel, reference) if entry == 'strict' else {
            'asset_id': 'c', 'asset_type': 'character', 'generation_type': channel, 'apply_style': False, 'batch_size': 1,
        }
        response = client.post(first.PUBLIC if entry == 'strict' else '/projects/p/assets/generate', json=raw)
        # The existing legacy adapter maps missing/invalid media ValueError to 404.
        assert response.status_code == (422 if entry == 'strict' else 404), response.text
        assert gpt_sheet.persisted(runtime) == before and boundary.saved_files(runtime) == frozen
        assert transport['queue'] == pending and not transport['calls'] and not transport['cli']
        assert not runtime.asset_generation_tasks and not getattr(runtime, '_asset_image_runs', {})


@pytest.mark.parametrize('channel', CHANNELS)
@pytest.mark.parametrize('bad', ['dangling-selected', 'duplicate-selected'])
def test_existing_adapter_bad_saved_selection_cannot_switch_to_local_fallback(runtime, transport, channel, bad):
    target, _ = setup(runtime, channel, 'legacy-url')
    assert target.full_body_asset is not None and target.full_body is not None
    if bad == 'dangling-selected':
        target.full_body_asset.selected_id = target.full_body.selected_image_id = 'missing-choice'
    else:
        variant = next(v for v in target.full_body_asset.variants if v.id == first.SELECTED_ID)
        target.full_body_asset.selected_id = target.full_body.selected_image_id = variant.id
        target.full_body_asset.variants.append(variant.model_copy(deep=True))
    boundary.commit_owners(runtime)
    gpt_sheet.queue_images(transport, 'http', count=1)
    pending = list(transport['queue'])
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        before, frozen = gpt_sheet.persisted(runtime), boundary.saved_files(runtime)
        rejected = client.post('/projects/p/assets/generate', json={
            'asset_id': 'c', 'asset_type': 'character', 'generation_type': channel, 'apply_style': False, 'batch_size': 1,
        })
        assert rejected.status_code == 422, rejected.text
        assert gpt_sheet.persisted(runtime) == before and boundary.saved_files(runtime) == frozen
        assert not transport['calls'] and not transport['cli'] and transport['queue'] == pending
        assert not runtime.asset_generation_tasks


@pytest.mark.parametrize('channel', CHANNELS)
def test_fallback_keeps_prior_true_consistency_despite_stale_other_channel_timestamp(runtime, transport, channel):
    target, reference = setup(runtime, channel, 'legacy-url')
    target.is_consistent = True
    target.full_body_updated_at = 9
    target.three_view_updated_at = target.headshot_updated_at = 1
    boundary.commit_owners(runtime)
    gpt_sheet.queue_images(transport, 'http', count=1)
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'http')
        accepted = client.post(first.PUBLIC, json=body(channel, reference))
        assert accepted.status_code == 200, accepted.text
        saved = headshot.saved_owner(runtime, 'local').characters[0]
        other = 'headshot' if channel == 'three_view' else 'three_view'
        assert getattr(saved, other + '_updated_at') == 1 < saved.full_body_updated_at == 9
        assert saved.is_consistent is True
        assert persisted_record(runtime).members[0].execution.phase == 'completed'
        assert len(boundary.paid_posts(transport)) == 1 and not transport['queue']


def test_fallback_cli_uses_frozen_reference_and_real_global_owner(runtime, transport, monkeypatch):
    _, reference = setup(runtime, 'headshot', 'own-upload', 'global')
    original = (runtime.data_dir / reference['reference_image_url']).read_bytes()
    gpt_sheet.queue_images(transport, 'token', count=1)
    external_run = gpt_sheet.mulerouter.subprocess.run
    observed = []

    def inspect_cli(argv, **options):
        refs = json.loads(argv[argv.index('--images') + 1])
        observed.append((list(argv), list(refs), [Path(ref).read_bytes() for ref in refs]))
        return external_run(argv, **options)

    monkeypatch.setattr(gpt_sheet.mulerouter, 'shutil', SimpleNamespace(which=lambda _name: '/synthetic-test-bin/mulerun'))
    monkeypatch.setattr(gpt_sheet.mulerouter.subprocess, 'run', inspect_cli)
    raw = body('headshot', reference)
    with TestClient(first.app_for(runtime)) as client:
        gpt_sheet.configure(client.app, 'token')
        accepted = client.post(first.PUBLIC, json=raw)
        assert accepted.status_code == 200, accepted.text
        record = persisted_record(runtime, 'global')
        execution = record.members[0].execution
        assert (record.owner_kind, record.owner_id) == ('global', 'library')
        assert execution.phase == 'completed' and execution.schema_version == 2
        assert execution.scope['gateway'] == 'mulerun_token'
        assert execution.media[0]['sha256'] == hashlib.sha256(original).hexdigest()
        assert_bound_proof(runtime, execution)
        assert len(observed) == len(transport['cli']) == 1
        argv, refs, contents = observed[0]
        assert 'openai/gpt-image-2/edit' in argv and argv[argv.index('--size') + 1] == '1024x1024'
        assert '--quality' not in argv and '--token' in argv and contents == [original]
        assert all(not Path(ref).exists() for ref in refs)
        assert [(r.method, r.url) for r, _ in transport['calls']] == [('GET', gpt_sheet.IMAGE_URL)]
        assert not runtime.scripts['p'].asset_image_requests and not runtime.series_store['s'].asset_image_requests
        before = gpt_sheet.persisted(runtime)
        replay = client.post(first.PUBLIC, json=raw)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert gpt_sheet.persisted(runtime) == before and len(transport['calls']) == len(transport['cli']) == 1
        assert not transport['queue'] and not transport['cli_queue']
