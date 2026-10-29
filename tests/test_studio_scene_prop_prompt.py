"""Authored scene/prop text reaches the real provider boundary and retained history."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.apps.comic_gen import assets
from tests import test_studio_core_image_api as old_http
from tests import test_studio_scene_prop_preparation as preparation
from tests import test_studio_scene_prop_service as scene

runtime = scene.runtime
bound_transport = scene.bound_transport
AUTHORED = 'L2 explicit prompt: a violet lantern on a silver table\n精确保留构图'
STYLE = 'soft painted light'


def provider_prompts(transport):
    return [json.loads(request.body)['input']['messages'][0]['content'][0]['text']
            for request, _ in transport['calls'] if request.method == 'POST']


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('route', scene.admission.ROUTES)
@pytest.mark.parametrize('apply_style', [False, True])
def test_http_edited_prompt_reaches_provider_and_persisted_variant(runtime, bound_transport, kind, route, apply_style):
    (root_kind, root_id), target = scene.setup(runtime, kind, route, selected=True)
    scene.queue_success(bound_transport, 2)
    body = {'client_request_id': 'authored-prompt', 'asset_id': 'c', 'asset_type': kind,
            'generation_type': 'all', 'batch_size': 2, 'prompt': AUTHORED,
            'apply_style': apply_style, 'style_prompt': STYLE,
            'model_name': 'wan2.7-image' if kind == 'scene' else 'wan2.7-image-pro'}
    expected = f'{AUTHORED}, {STYLE}' if apply_style else AUTHORED
    path = f"/{'projects' if root_kind == 'project' else 'series'}/{root_id}/asset_image_requests"
    app = old_http.real_app(runtime)
    with TestClient(app) as client:
        old_http.enable_fake_key(app)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        assert provider_prompts(bound_transport) == [expected, expected]
        response = client.get(f'{path}/authored-prompt')
        assert response.status_code == 200, response.text
        assert [member['status'] for member in response.json()['request']['members']] == ['completed', 'completed']
        record = scene.admission.owner(runtime, route).asset_image_requests[0]
        assert record.channel == 'image'  # all is the scene/prop single-image entry channel.
        assert [member.execution.intent['prompt'] for member in record.members] == [expected, expected]
        assert target.image_asset is not None and target.image_asset.selected_id == 'old'
        assert [variant.prompt_used for variant in target.image_asset.variants[:-1]] == [expected, expected]
        saved = scene.worker.disk_request(runtime, route)
        assert [member['execution']['intent']['prompt'] for member in saved['members']] == [expected, expected]
        filename = {'local': 'projects.json', 'series': 'series.json', 'global': 'library_assets.json', 'standalone': 'series.json'}[route]
        data = json.loads((runtime.data_dir / filename).read_text())
        persisted_owner = data if route == 'global' else data['p' if route == 'local' else 's']
        persisted_asset = persisted_owner[kind + 's'][0]['image_asset']
        assert [variant['prompt_used'] for variant in persisted_asset['variants'][:-1]] == [expected, expected]
        assert persisted_asset['selected_id'] == 'old'
        before = {file.name: file.read_bytes() for file in runtime.data_dir.glob('*.json')}
        replay = client.post(path, json=body)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        changed = client.post(path, json={**body, 'prompt': 'another authored prompt'})
        assert changed.status_code == 409
        assert provider_prompts(bound_transport) == [expected, expected]
        assert before == {file.name: file.read_bytes() for file in runtime.data_dir.glob('*.json')}


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('prompt_field', [{}, {'prompt': None}, {'prompt': ''}])
@pytest.mark.parametrize('apply_style', [False, True])
def test_absent_or_empty_prompt_retains_exact_default_provider_template(runtime, bound_transport, kind, prompt_field, apply_style):
    root, target = scene.setup(runtime, kind)
    scene.queue_success(bound_transport, 1)
    ack = runtime.asset_image_service.admit(*root, scene.command(kind, apply_style=apply_style, style_prompt=STYLE, **prompt_field))
    result = runtime.asset_image_service.run_original_request(ack.request.id)
    expected = (f'Scene Concept Art: {target.name}. {target.description}. High quality, detailed. ' if kind == 'scene'
                else f'Prop Design: {target.name}. {target.description}. Isolated on white background, high quality, detailed. ')
    expected += STYLE if apply_style else ''
    assert result is not None and provider_prompts(bound_transport) == [expected]
    assert target.image_asset is not None and target.image_asset.variants[0].prompt_used == expected


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('positive', [None, '', STYLE])
def test_custom_prompt_appends_effective_style_once_and_preserves_authored_text(kind, positive):
    authored = f'  {AUTHORED}, {STYLE}  '
    effective_style = preparation.DEFAULT_STYLE if positive is None else positive
    expected = f'{authored}, {effective_style}' if effective_style and effective_style not in authored else authored
    assert getattr(assets, f'prepare_{kind}_image')('name', 'description', prompt=authored, positive_prompt=positive) == (
        expected, preparation.DEFAULT_SIZES[kind],
    )


@pytest.mark.parametrize('kind', scene.KINDS)
@pytest.mark.parametrize('route', scene.admission.ROUTES)
def test_legacy_http_and_generator_preserve_edited_prompt(runtime, monkeypatch, kind, route):
    (root_kind, root_id), _ = scene.setup(runtime, kind, route, selected=True)
    calls = []

    def generate(self, prompt, output_path, **kwargs):
        calls.append((prompt, kwargs))
        Path(output_path).write_bytes(scene.PNG)
        return output_path, 0.0

    # Only the external legacy model is replaced; API, preparation, generator and storage run.
    monkeypatch.setattr(assets.WanxImageModel, 'generate', generate)
    path = f"/{'projects' if root_kind == 'project' else 'series'}/{root_id}/assets/generate"
    app = old_http.real_app(runtime)
    with TestClient(app) as client:
        old_http.enable_fake_key(app)
        response = client.post(path, json={'asset_id': 'c', 'asset_type': kind, 'generation_type': 'all',
                                          'prompt': AUTHORED, 'apply_style': False, 'model_name': 'wan2.6-t2i'})
        assert response.status_code == 200, response.text
        assert [prompt for prompt, _ in calls] == [AUTHORED]
        owner = scene.admission.owner(runtime, route)
        target = getattr(owner, kind + 's')[0]
        assert target.image_asset is not None and target.image_asset.variants[0].prompt_used == AUTHORED
        assert owner.asset_image_requests == []


@pytest.mark.parametrize('kind', scene.KINDS)
def test_accepted_prompt_stays_frozen_when_preparation_changes(runtime, bound_transport, monkeypatch, kind):
    root, target = scene.setup(runtime, kind)
    scene.queue_success(bound_transport, 2)
    command = scene.command(kind, prompt=AUTHORED, batch_size=2)
    ack = runtime.asset_image_service.admit(*root, command)
    def forbidden(*args, **kwargs):
        raise AssertionError('Accepted request must not re-prepare its prompt')
    monkeypatch.setattr(scene.module, f'prepare_{kind}_image', forbidden)
    result = runtime.asset_image_service.run_original_request(ack.request.id)
    assert result is not None and provider_prompts(bound_transport) == [AUTHORED, AUTHORED]
    assert target.image_asset is not None
    assert [variant.prompt_used for variant in target.image_asset.variants[:-1]] == [AUTHORED, AUTHORED]
    assert runtime.asset_image_service.admit(*root, command).replayed
