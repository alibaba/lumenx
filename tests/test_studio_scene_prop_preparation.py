"""Lock legacy scene/prop preparation before extracting reusable pure helpers."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.apps.comic_gen import assets
from src.apps.comic_gen.models import GenerationStatus, Prop, Scene


KINDS = ('scene', 'prop')
DEFAULT_STYLE = 'cinematic lighting, movie still, 8k, highly detailed, realistic'
PREFIXES = {
    'scene': 'Scene Concept Art: name. description. High quality, detailed. ',
    'prop': 'Prop Design: name. description. Isolated on white background, high quality, detailed. ',
}
DEFAULT_SIZES = {'scene': '1024*576', 'prop': '1024*1024'}
CASES = [
    (None, None), ('', ''), (' ', ' '), ('watercolor', '768*1024'),
    ('cinematic lighting', '1024*768'), ('\n细节  ', 'legacy-size'),
]


def target(kind, *, selected=True):
    model = Scene if kind == 'scene' else Prop
    return model.model_validate({
        'id': 'asset', 'name': 'name', 'description': 'description',
        'image_url': 'uploads/old.png' if selected else None,
        'image_asset': {
            'selected_id': 'old' if selected else None,
            'variants': [{'id': 'old', 'url': 'uploads/old.png', 'created_at': 1, 'is_favorited': True}],
        },
        'video_prompt': 'preserved motion', 'locked': True, 'starred': True,
    })


@pytest.fixture
def generator(tmp_path, monkeypatch):
    state = SimpleNamespace(calls=[], uploads=[], uploader_options=[], fail_slot=None, mirror='disabled')
    root = tmp_path / 'output'

    def generate(prompt, output_path, **kwargs):
        state.calls.append({'prompt': prompt, 'path': Path(output_path), 'kwargs': kwargs})
        Path(output_path).write_bytes(b'synthetic-image')
        if len(state.calls) == state.fail_slot:
            raise RuntimeError('synthetic provider failure')
        return output_path, 0.0

    def uploader(**kwargs):
        state.uploader_options.append(kwargs)

        def upload(path, *, sub_path):
            state.uploads.append((Path(path), sub_path))
            if state.mirror == 'error':
                raise OSError('optional mirror unavailable')
            return f'{sub_path}/mirror-{len(state.uploads)}.png' if state.mirror == 'success' else None

        return SimpleNamespace(is_configured=state.mirror != 'disabled', upload_file=upload)

    monkeypatch.setattr(assets, 'WanxImageModel', lambda *args, **kwargs: SimpleNamespace(generate=generate))
    monkeypatch.setattr('src.utils.oss_utils.OSSImageUploader', uploader)
    return assets.AssetGenerator({'output_dir': str(root / 'assets'), 'output_root': str(root)}, env={}), state


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('positive,size', CASES)
def test_legacy_prompt_size_and_negative_are_exact(generator, kind, positive, size):
    model, state = generator
    item = target(kind)
    result = getattr(model, f'generate_{kind}')(
        item, positive_prompt=positive, size=size, negative_prompt='negative unchanged',
        model_name='wan2.7-image-pro',
    )
    expected = PREFIXES[kind] + (DEFAULT_STYLE if positive is None else positive)
    assert result is item
    assert state.calls[0]['prompt'] == expected
    assert state.calls[0]['kwargs'] == {
        'negative_prompt': 'negative unchanged', 'model_name': 'wan2.7-image-pro',
        'size': size or DEFAULT_SIZES[kind],
    }
    assert item.image_asset is not None
    assert item.image_asset.variants[0].prompt_used == expected
    assert item.status == GenerationStatus.COMPLETED


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('selected', [False, True])
@pytest.mark.parametrize('batch_size', [1, 3])
def test_legacy_batch_prepend_and_selection_preserve_other_fields(generator, kind, selected, batch_size):
    model, state = generator
    item = target(kind, selected=selected)
    before = item.model_dump(mode='json')
    getattr(model, f'generate_{kind}')(item, batch_size=batch_size)
    assert len(state.calls) == batch_size
    assert item.image_asset is not None
    paths = [str(call['path'].relative_to(model.output_root)) for call in state.calls]
    assert [variant.url for variant in item.image_asset.variants] == [*reversed(paths), 'uploads/old.png']
    assert item.image_asset.variants[-1].is_favorited is True
    assert item.image_url == ('uploads/old.png' if selected and batch_size > 1 else paths[0])
    chosen = next(variant for variant in item.image_asset.variants if variant.id == item.image_asset.selected_id)
    assert chosen.url == item.image_url
    after = item.model_dump(mode='json')
    assert {k: v for k, v in after.items() if k not in {'image_asset', 'image_url', 'status'}} == {
        k: v for k, v in before.items() if k not in {'image_asset', 'image_url', 'status'}
    }


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('selected', [False, True])
def test_legacy_direct_generator_stops_at_first_exception_and_retains_paid_candidate(generator, kind, selected):
    model, state = generator
    item = target(kind, selected=selected)
    state.fail_slot = 2
    with pytest.raises(RuntimeError, match='synthetic provider failure'):
        getattr(model, f'generate_{kind}')(item, batch_size=3)
    assert len(state.calls) == 2 and item.status == GenerationStatus.FAILED
    assert item.image_asset is not None and len(item.image_asset.variants) == 2
    first_path = str(state.calls[0]['path'].relative_to(model.output_root))
    assert item.image_asset.variants[0].url == first_path
    # The outer pipeline restores original selection on partial failure; this is the generator boundary.
    assert item.image_url == ('uploads/old.png' if selected else first_path)
    assert all(call['path'].read_bytes() == b'synthetic-image' for call in state.calls)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('mirror', ['disabled', 'none', 'error', 'success'])
def test_legacy_optional_oss_mirror_and_local_output_are_unchanged(generator, kind, mirror):
    model, state = generator
    state.mirror = mirror
    item = target(kind, selected=False)
    getattr(model, f'generate_{kind}')(item, batch_size=2)
    assert item.image_asset is not None
    assert len(state.calls) == 2 and len(state.uploads) == (0 if mirror == 'disabled' else 2)
    paths = [str(call['path'].relative_to(model.output_root)) for call in state.calls]
    expected = [f'assets/{kind}s/mirror-{i}.png' for i in (1, 2)] if mirror == 'success' else paths
    assert [variant.url for variant in item.image_asset.variants] == [*reversed(expected), 'uploads/old.png']
    assert item.image_url == expected[0]
    assert all(call['path'].read_bytes() == b'synthetic-image' for call in state.calls)
    assert all(options == {'env': {}, 'output_root': model.output_root} for options in state.uploader_options)
    assert all(sub_path == f'assets/{kind}s' for _, sub_path in state.uploads)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('positive,size', CASES)
def test_pure_preparation_matches_exact_legacy_contract(kind, positive, size):
    prepare = getattr(assets, f'prepare_{kind}_image')
    assert prepare('name', 'description', positive_prompt=positive, size=size) == (
        PREFIXES[kind] + (DEFAULT_STYLE if positive is None else positive), size or DEFAULT_SIZES[kind],
    )


@pytest.mark.parametrize('kind', KINDS)
def test_legacy_generator_uses_pure_preparation_once_for_whole_batch(generator, monkeypatch, kind):
    model, state = generator
    calls = []

    def prepare(name, description, *, prompt, positive_prompt, size):
        calls.append((name, description, prompt, positive_prompt, size))
        return 'prepared exact prompt', 'prepared-size'

    monkeypatch.setattr(assets, f'prepare_{kind}_image', prepare)
    getattr(model, f'generate_{kind}')(target(kind), positive_prompt='', size=' ', batch_size=3)
    assert calls == [('name', 'description', None, '', ' ')]
    assert len(state.calls) == 3
    assert all(call['prompt'] == 'prepared exact prompt' and call['kwargs']['size'] == 'prepared-size' for call in state.calls)
