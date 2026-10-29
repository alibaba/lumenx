"""Portable built-in BGM stays bounded and becomes independent managed audio."""
import hashlib
import io
import json
import zipfile
import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app
from src.apps.comic_gen.audio import BGM_PRESETS
from src.apps.comic_gen.models import GlobalAssetLibrary, Scene
from src.apps.comic_gen.project_package import ProjectPackageError, build_package, prepare_import
from src.core.project_package_media import MediaRootPolicy, ProjectPackageMediaError, normalize_media_ref
from tests.test_merge_media_integration import _probe_json, _tool
from tests.test_project_package import _resolved_assets, _source_project
from tests.test_studio_media_browser_harness import bind, generate, load

PRESET = 'presets/bgm/calm_warm.mp3'


def package_source(tmp_path, ref=PRESET):
    root = tmp_path / 'source'
    script = _source_project(root)
    script.bgm_url = ref
    path = root / PRESET
    path.parent.mkdir(parents=True)
    path.write_bytes(b'builtin background bytes')
    return root, script


def build(root, script):
    return build_package(script, resolved_assets=_resolved_assets(), data_dir=root, source_storage_id='source')


def rewrite_package(package, change):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package)) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    change(entries)
    with zipfile.ZipFile(output, 'w') as target:
        for name, data in entries.items():
            target.writestr(name, data)
    return output.getvalue()


@pytest.mark.parametrize('preset', [row['url'] for row in BGM_PRESETS])
def test_builtin_bgm_roundtrip_is_managed_and_reexportable(tmp_path, preset):
    root, script = package_source(tmp_path, preset)
    selected = root / preset
    selected.write_bytes(preset.encode())
    snapshot = script.model_dump(mode='json')
    artifact = build(root, script)
    refs = {row['logical_ref'] for row in artifact.manifest['media']}
    assert {ref for ref in refs if ref.startswith('presets/')} == {preset}
    target = tmp_path / 'target'
    imported = prepare_import(artifact.data, data_dir=target)
    expected = f"audio/imported/{imported.script.id}/{preset}"
    assert imported.script.bgm_url == imported.media[preset] == expected
    assert (target / expected).read_bytes() == selected.read_bytes()
    assert not (target / 'presets').exists()
    assert script.model_dump(mode='json') == snapshot
    copied_assets = GlobalAssetLibrary.model_validate({
        'characters': imported.script.characters, 'scenes': imported.script.scenes, 'props': imported.script.props,
    })
    again = build_package(imported.script, resolved_assets=copied_assets, data_dir=target, source_storage_id='target')
    assert expected in {row['logical_ref'] for row in again.manifest['media']}


@pytest.mark.parametrize('ref', [
    'presets/private.json', 'presets/bgm/private.mp3', 'presets/bgm/calm_warm.wav',
    'presets/../config.json', 'presets/bgm/../../config.json',
    'presets/bgm/calm_warm.mp3/private', '/etc/passwd',
    'https://example.invalid/bgm.mp3', 'presets\\bgm\\calm_warm.mp3',
])
def test_unlisted_or_unsafe_preset_is_rejected(tmp_path, ref):
    root, script = package_source(tmp_path, ref)
    with pytest.raises(ProjectPackageError):
        build(root, script)


@pytest.mark.parametrize('damage', ['missing', 'leaf-symlink', 'parent-symlink', 'root-symlink'])
def test_preset_keeps_owned_open_checks(tmp_path, damage):
    root, script = package_source(tmp_path)
    selected = root / PRESET
    original = selected.read_bytes()
    if damage == 'missing':
        selected.unlink()
    elif damage == 'leaf-symlink':
        outside = tmp_path / 'outside.mp3'
        outside.write_bytes(original)
        selected.unlink()
        selected.symlink_to(outside)
    else:
        directory = root / ('presets/bgm' if damage == 'parent-symlink' else 'presets')
        moved = tmp_path / 'moved'
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ProjectPackageError, match='unavailable or unsafe'):
        build(root, script)


@pytest.mark.parametrize('damage', ['missing', 'changed', 'unlisted'])
def test_import_rejects_invalid_preset_before_project_or_media_creation(tmp_path, damage):
    root, script = package_source(tmp_path)
    artifact = build(root, script)
    def corrupt(entries):
        name = 'media/' + PRESET
        if damage == 'missing':
            entries.pop(name)
        elif damage == 'changed':
            entries[name] = b'x' * len(entries[name])
        else:
            changed = 'presets/bgm/private.mp3'
            entries['media/' + changed] = entries.pop(name)
            for metadata in ('manifest.json', 'project.json'):
                entries[metadata] = entries[metadata].replace(PRESET.encode(), changed.encode())
    payload = rewrite_package(artifact.data, corrupt)
    target = tmp_path / 'target'
    app = create_app(data_dir=target, user_data_dir=tmp_path / 'config', load_config=False,
                     setup_logs=False, recover_orphan_tasks=False, warmup_demucs=False)
    with TestClient(app) as http:
        response = http.post('/projects/package/import', content=payload, headers={'content-type': 'application/zip'})
        assert response.status_code == 422
        assert app.state.pipeline.scripts == {}
        assert not (target / 'audio/imported').exists()
        assert not (target / 'presets').exists()


def test_import_target_collision_rejects_without_overwriting_either_media(tmp_path):
    root, script = package_source(tmp_path)
    collision = 'audio/' + PRESET
    other = root / collision
    other.parent.mkdir(parents=True)
    other.write_bytes(b'distinct ordinary audio')
    script.frames[0].audio_url = collision
    artifact = build(root, script)
    target = tmp_path / 'target'
    app = create_app(data_dir=target, user_data_dir=tmp_path / 'config', load_config=False,
                     setup_logs=False, recover_orphan_tasks=False, warmup_demucs=False)
    with TestClient(app) as http:
        sentinel = target / 'audio/retained.mp3'
        sentinel.parent.mkdir(exist_ok=True)
        sentinel.write_bytes(b'retained target audio')
        response = http.post('/projects/package/import', content=artifact.data, headers={'content-type': 'application/zip'})
        assert response.status_code == 422 and 'collision' in response.text.lower()
        assert app.state.pipeline.scripts == {}
        assert sentinel.read_bytes() == b'retained target audio'
        assert not (target / 'audio/imported').exists()
    assert other.read_bytes() == b'distinct ordinary audio'
    assert (root / PRESET).read_bytes() == b'builtin background bytes'


def test_import_does_not_follow_target_audio_directory_symlink(tmp_path):
    root, script = package_source(tmp_path)
    artifact = build(root, script)
    target = tmp_path / 'target'
    target.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'retained'
    sentinel.write_bytes(b'untouched')
    (target / 'audio').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectPackageError, match='unavailable or unsafe'):
        prepare_import(artifact.data, data_dir=target)
    assert list(outside.iterdir()) == [sentinel] and sentinel.read_bytes() == b'untouched'


def test_exact_file_policy_does_not_widen_default_or_other_roots():
    ordinary = MediaRootPolicy(allowed_roots=frozenset({'audio'}))
    assert normalize_media_ref('audio/a.mp3', policy=ordinary) == 'audio/a.mp3'
    with pytest.raises(ProjectPackageMediaError, match='Unsupported'):
        normalize_media_ref(PRESET, policy=ordinary)
    exact = MediaRootPolicy(allowed_roots=frozenset({'audio'}), allowed_files=frozenset({PRESET}))
    assert normalize_media_ref(PRESET, policy=exact) == PRESET
    for ref in ('presets/private.json', 'presets/bgm/private.mp3', 'presets/bgm/../bgm/calm_warm.mp3'):
        with pytest.raises(ProjectPackageMediaError):
            normalize_media_ref(ref, policy=exact)


def test_real_http_preset_audio_video_merge_survives_two_root_package(tmp_path):
    smoke = load()
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text('<html>HTTP test only</html>')
    root = tmp_path / 'source'
    mounted = smoke.prepare_root(root, static, frontend_commit='test', backend_commit='test', resume=False)
    evidence = smoke.open_evidence(tmp_path / 'audit.json', resume=False)
    app = smoke.create_smoke_app(root, mounted, evidence)
    with TestClient(app) as http:
        pipeline = app.state.pipeline
        script = pipeline.scripts['media-episode-1']
        script.scenes = [Scene.model_validate({'id': 'scene', 'name': 'Scene', 'description': 'Finite package fixture'})]
        for frame in script.frames:
            frame.scene_id = 'scene'
        pipeline._commit_storage('projects.json')
        bind(http)
        generate(http)
        assert http.post('/projects/media-episode-1/frames/media-f1/dub/preview', json={'video_task_id': 'media-A'}).status_code == 200
        assert http.post('/projects/media-episode-1/frames/media-f1/dub/apply').status_code == 200
        assert http.post('/projects/media-episode-1/frames/media-f1/select_video', json={'video_id': 'media-B'}).status_code == 200
        assert http.put('/projects/media-episode-1/audio_mix', json={'bgm_url': PRESET, 'dialogue_volume': 50, 'bgm_volume': 35}).status_code == 200
        assert http.post('/projects/media-episode-1/merge').status_code == 200
        before = pipeline.read_committed_script('media-episode-1').model_dump(mode='json')
        response = http.get('/projects/media-episode-1/package/export')
        assert response.status_code == 200, response.text
        assert pipeline.read_committed_script('media-episode-1').model_dump(mode='json') == before
        assert len(evidence.payload['sdk_calls']) == 1 and evidence.payload['blocked_sockets'] == []
        package = response.content
    target = tmp_path / 'target'
    app = create_app(data_dir=target, user_data_dir=tmp_path / 'config', load_config=False,
                     setup_logs=False, recover_orphan_tasks=True, warmup_demucs=False)
    with TestClient(app) as http:
        response = http.post('/projects/package/import', content=package, headers={'content-type': 'application/zip'})
        assert response.status_code == 200, response.text
        copied = response.json()
        assert copied['bgm_url'].startswith(f"audio/imported/{copied['id']}/")
        assert copied['provider_request_receipts'] == [] and copied['asset_image_requests'] == []
        assert copied['merged_video_source'] is None and copied['frames'][0]['dialogue_audio_snapshot'] is None
        selected = next(t for t in copied['video_tasks'] if t['id'] == copied['frames'][0]['selected_video_id'])
        assert selected['video_url'].endswith('/media-B.mp4')
        assert copied['frames'][0]['dub_applied_source']['task_id'] != selected['id']
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            for entry in manifest['media']:
                logical = entry['logical_ref']
                kind, tail = logical.split('/', 1)
                relative = f"audio/imported/{copied['id']}/{logical}" if kind == 'presets' else f"{kind}/imported/{copied['id']}/{tail}"
                assert hashlib.sha256((target / relative).read_bytes()).hexdigest() == entry['sha256']
        merged = target / copied['merged_video_url']
        assert {s['codec_type'] for s in _probe_json(_tool('ffprobe'), merged)['streams']} == {'audio', 'video'}
        assert not (target / 'presets').exists()
