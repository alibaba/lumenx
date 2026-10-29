"""Finite offline media fixture: real Studio API, SDK preparation and media decode."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket

import dashscope
import pytest
from dashscope.audio.tts_v2 import SpeechSynthesizer
from fastapi.testclient import TestClient

from tests.test_merge_media_integration import (
    _decode_pcm, _goertzel_power, _probe_json, _sample_rgb, _tool,
)

SCRIPT = Path(__file__).resolve().parents[1] / 'docs/agents/tools/lumenx_studio_media_browser_smoke.py'


def load():
    assert SCRIPT.is_file(), 'The finite Studio media companion has not been implemented'
    spec = importlib.util.spec_from_file_location('studio_media_smoke', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(tmp_path):
    smoke = load()
    static = tmp_path / 'build'
    static.mkdir()
    (static / 'index.html').write_text('<html>media fixture</html>')
    root = tmp_path / 'business'
    mounted = smoke.prepare_root(root, static, frontend_commit='front', backend_commit='back', resume=False)
    evidence = smoke.open_evidence(tmp_path / 'evidence/audit.json', resume=False)
    return smoke, root, mounted, evidence


def start(harness, *, resume=False):
    smoke, root, static, evidence = harness
    app = smoke.create_smoke_app(root, static, evidence, resume=resume)
    return app, TestClient(app)


def bind(http):
    result = http.post('/projects/media-episode-1/characters/media-hero/voice',
                       json={'voice_id': 'longanyang', 'voice_name': 'Offline system voice'})
    assert result.status_code == 200, result.text


def generate(http, episode=1):
    result = http.post(f'/projects/media-episode-{episode}/frames/media-f{1 if episode == 1 else 4}/audio',
                       json={'speed': 1.2, 'pitch': 0.8, 'volume': 37})
    assert result.status_code == 200, result.text
    project = http.get(f'/projects/media-episode-{episode}').json()
    assert project['frames'][0]['audio_url'], project['frames'][0]
    return project['frames'][0]


def test_fixture_has_true_shared_owner_distinct_takes_and_decodable_media(harness, tmp_path):
    smoke, root, _, evidence = harness
    app, client = start(harness)
    with client as http:
        pipeline = app.state.pipeline
        series = pipeline.series_store['media-series']
        assert series.episode_ids == ['media-episode-1', 'media-episode-2']
        assert [c.id for c in series.characters] == ['media-hero']
        assert not series.characters[0].voice_id
        for identity in series.episode_ids:
            owner = pipeline.read_committed_script(identity)
            assert owner.series_id == series.id and owner.characters == []
            assert http.get(f'/projects/{identity}').json()['characters'][0]['id'] == 'media-hero'
        first = pipeline.read_committed_script('media-episode-1')
        assert [f.id for f in first.frames] == ['media-f1', 'media-f2', 'media-f3']
        assert [f.selected_video_id for f in first.frames] == ['media-A', 'media-f2-take', 'media-f3-take']
        assert [(t.id, t.project_id, t.frame_id) for t in first.video_tasks] == [
            ('media-A', first.id, 'media-f1'), ('media-B', first.id, 'media-f1'),
            ('media-f2-take', first.id, 'media-f2'), ('media-f3-take', first.id, 'media-f3')]
        assert first.frames[0].bg_audio_source_video == first.video_tasks[0].video_url
        manifest = json.loads((root / 'fixture.json').read_text())
        assert manifest['kind'] == smoke.KIND
        for row in manifest['media']:
            path = root / 'output' / row['path']
            assert not Path(row['path']).is_absolute() and path.is_relative_to(root / 'output')
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        ffmpeg, ffprobe = _tool('ffmpeg'), _tool('ffprobe')
        for task, channel in zip(first.video_tasks, [0, 2, 1, 0]):
            path = root / 'output' / task.video_url
            probe = _probe_json(ffprobe, path)
            assert {s['codec_type'] for s in probe['streams']} == {'video', 'audio'}
            assert 0.9 <= float(probe['format']['duration']) <= 1.2
            assert _sample_rgb(ffmpeg, path, 0.4, tmp_path)[channel] > 90
        assert evidence.payload['sdk_calls'] == []


def test_real_voice_api_two_episode_sdk_and_persisted_mp3(harness, tmp_path, monkeypatch):
    _, root, _, evidence = harness
    monkeypatch.setattr(dashscope, 'api_key', 'ambient-sdk-sentinel')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'ambient-env-sentinel')
    app, client = start(harness)
    with client as http:
        pipeline = app.state.pipeline
        untouched = pipeline.read_committed_script('media-untouched').model_dump()
        bind(http)
        assert len(evidence.payload['sdk_calls']) == 0
        outputs = []
        for episode in (1, 2):
            assert http.get(f'/projects/media-episode-{episode}').json()['characters'][0]['voice_id'] == 'longanyang'
            frame = generate(http, episode)
            path = root / 'output' / frame['audio_url']
            assert path.suffix == '.mp3' and path.stat().st_size > 1000
            assert frame['dialogue_audio_snapshot']['prepared']['parameters']['voice'] == 'longanyang'
            samples = _decode_pcm(_tool('ffmpeg'), path, tmp_path)
            assert 0.7 * 44100 < len(samples) < 1.2 * 44100
            assert _goertzel_power(samples, 44100, 777) > 100 * _goertzel_power(samples, 44100, 440)
            assert http.get('/files/' + frame['audio_url']).content == path.read_bytes()
            outputs.append(frame['audio_url'])
        assert len(set(outputs)) == 2
        calls = evidence.payload['sdk_calls']
        assert len(calls) == 2 and calls[0]['text'] != calls[1]['text']
        assert all((c['voice'], c['rate'], c['pitch'], c['volume']) == ('longanyang', 1.2, 0.8, 37) for c in calls)
        assert all(c['model'].startswith('cosyvoice-') for c in calls)
        assert all(c['audio_sha256'] == hashlib.sha256((root / 'output' / outputs[0]).read_bytes()).hexdigest() for c in calls)
        rebound = http.post('/projects/media-episode-1/characters/media-hero/voice',
                            json={'voice_id': 'longanhuan', 'voice_name': '龙安欢 (活力女)'})
        assert rebound.status_code == 200, rebound.text
        for episode, url in zip((1, 2), outputs):
            current = http.get(f'/projects/media-episode-{episode}').json()
            assert current['characters'][0]['voice_id'] == 'longanhuan'
            assert current['frames'][0]['audio_url'] == url
        assert len(evidence.payload['sdk_calls']) == 2, 'Changing a shared binding must not generate audio'
        assert pipeline.read_committed_script('media-untouched').model_dump() == untouched
        assert not pipeline.read_committed_script('media-episode-1').characters
        assert evidence.payload['blocked_sockets'] == []
        serialized = json.dumps(evidence.payload)
        assert 'ambient-sdk-sentinel' not in serialized and 'ambient-env-sentinel' not in serialized
    assert dashscope.api_key == 'ambient-sdk-sentinel'


def test_cached_background_preview_and_controlled_real_merge_failure_recover(harness, tmp_path):
    smoke, root, _, evidence = harness
    app, client = start(harness)
    with client as http:
        bind(http)
        generate(http)
        preview = http.post('/projects/media-episode-1/frames/media-f1/dub/preview', json={'video_task_id': 'media-A'})
        assert preview.status_code == 200, preview.text
        assert http.post('/projects/media-episode-1/frames/media-f1/dub/apply').status_code == 200
        assert http.post('/projects/media-episode-1/frames/media-f1/select_video', json={'video_id': 'media-B'}).status_code == 200
        merged = http.post('/projects/media-episode-1/merge')
        assert merged.status_code == 200, merged.text
        old = app.state.pipeline.read_committed_script('media-episode-1').model_dump()
        output = root / 'output' / old['merged_video_url']
        old_bytes = output.read_bytes()
        ffmpeg, ffprobe = _tool('ffmpeg'), _tool('ffprobe')
        assert 2.9 <= float(_probe_json(ffprobe, output)['format']['duration']) <= 3.4
        samples = [_sample_rgb(ffmpeg, output, second, tmp_path) for second in (0.4, 1.4, 2.4)]
        assert samples[0][2] > samples[0][0] + 100
        assert samples[1][1] > samples[1][0] + 70
        assert samples[2][0] > 150 and samples[2][1] > 150 and samples[2][2] < 80
        audio = _decode_pcm(ffmpeg, output, tmp_path)
        assert _goertzel_power(audio, 44100, 880) > 100 * _goertzel_power(audio, 44100, 777)
        assert http.post('/__smoke/control', json={'action': 'stash_f2'}).status_code == 200
        failed = http.post('/projects/media-episode-1/merge')
        assert failed.status_code == 400 and 'missing' in failed.text.lower()
        retained = app.state.pipeline.read_committed_script('media-episode-1').model_dump()
        assert retained['merged_video_url'] == old['merged_video_url'] and output.read_bytes() == old_bytes
        assert http.post('/__smoke/control', json={'action': 'restore_f2'}).status_code == 200
        assert http.post('/projects/media-episode-1/merge').status_code == 200
        assert len(evidence.payload['sdk_calls']) == 1
        assert smoke.base.sha256_file(root / 'output/video/media-f2.mp4')


@pytest.mark.parametrize('body', [{}, {'action': 'delete'}, {'action': 'stash_f2', 'path': '../user.mp4'},
                                {'action': 'restore_f2', 'force': True}])
def test_control_rejects_unknown_or_extra_fields_without_mutation(harness, body):
    _, root, _, evidence = harness
    _, client = start(harness)
    with client as http:
        before = (root / 'output/video/media-f2.mp4').read_bytes()
        assert http.post('/__smoke/control', json=body).status_code == 422
        assert (root / 'output/video/media-f2.mp4').read_bytes() == before
        assert evidence.payload['controls'] == []


@pytest.mark.parametrize('damage', ['marker', 'fixture', 'content', 'destination', 'symlink'])
def test_control_refuses_tamper_without_overwrite(harness, tmp_path, damage):
    smoke, root, _, _ = harness
    _, client = start(harness)
    with client as http:
        source = root / 'output/video/media-f2.mp4'
        saved = source.with_name(source.name + '.retained')
        original = source.read_bytes()
        if damage == 'marker':
            (root / smoke.MARKER).write_text('{}')
        elif damage == 'fixture':
            (root / 'fixture.json').write_text('{}')
        elif damage == 'content':
            source.write_bytes(b'changed')
        elif damage == 'destination':
            saved.write_bytes(b'do not replace')
        else:
            external = tmp_path / 'external.mp4'
            external.write_bytes(original)
            source.unlink()
            source.symlink_to(external)
        before = source.read_bytes()
        assert http.post('/__smoke/control', json={'action': 'stash_f2'}).status_code == 422
        assert source.read_bytes() == before
        if damage == 'destination':
            assert saved.read_bytes() == b'do not replace'


def test_resume_keeps_binding_audio_stashed_file_and_cumulative_audit(harness):
    smoke, root, static, evidence = harness
    _, client = start(harness)
    with client as http:
        bind(http)
        frame = generate(http)
        assert http.post('/__smoke/control', json={'action': 'stash_f2'}).status_code == 200
    before = (root / 'output' / frame['audio_url']).read_bytes()
    resumed = smoke.open_evidence(evidence.path, resume=True)
    _, client = start((smoke, root, static, resumed), resume=True)
    with client as http:
        current = http.get('/projects/media-episode-1').json()
        assert current['frames'][0]['audio_url'] == frame['audio_url']
        assert current['characters'][0]['voice_id'] == 'longanyang'
        assert http.get('/__smoke/state').json()['f2_stashed'] is True
        assert len(resumed.payload['sdk_calls']) == 1
        assert http.post('/__smoke/control', json={'action': 'restore_f2'}).status_code == 200
    assert (root / 'output' / frame['audio_url']).read_bytes() == before
    assert len([s for s in resumed.payload['sessions'] if 'started_at' in s]) == 2


@pytest.mark.parametrize('damage', ['static', 'extra_static', 'source_commit', 'foreign_evidence', 'media'])
def test_resume_rejects_changed_source_or_fixture_identity(harness, damage):
    smoke, root, static, evidence = harness
    _, client = start(harness)
    with client:
        pass
    if damage == 'static':
        (static / 'index.html').write_text('changed')
    elif damage == 'extra_static':
        (static / 'injected.js').write_text('changed')
    elif damage == 'source_commit':
        with pytest.raises(ValueError, match='source'):
            smoke.prepare_root(root, static, frontend_commit='other', backend_commit='back', resume=True)
        return
    elif damage == 'foreign_evidence':
        evidence.payload['fixture_root'] = '/other'
    else:
        (root / 'output/video/media-A.mp4').write_bytes(b'changed')
    with pytest.raises(ValueError):
        app = smoke.create_smoke_app(root, static, evidence, resume=True)
        with TestClient(app):
            pass


@pytest.mark.parametrize('damage', ['static', 'extra_static', 'foreign_evidence'])
def test_first_start_rechecks_snapshot_and_unbound_evidence(harness, damage):
    smoke, root, static, evidence = harness
    if damage == 'static':
        (static / 'index.html').write_text('changed after snapshot')
    elif damage == 'extra_static':
        (static / 'injected.js').write_text('unfrozen script')
    else:
        evidence.payload['fixture_root'] = '/other'
    with pytest.raises(ValueError):
        smoke.create_smoke_app(root, static, evidence)
    assert not (root / 'fixture.json').exists()


def test_sdk_exclusive_credential_guard_and_restoration_on_exception(harness, monkeypatch):
    smoke, root, _, evidence = harness
    # The isolated test runner independently forbids all sockets, even local
    # ones. A recording resolver proves forwarding without weakening its guard.
    forwarded = []
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **_kw: forwarded.append(args) or ['local-result'])
    original = SpeechSynthesizer.call, socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    monkeypatch.setattr(dashscope, 'api_key', 'not-the-synthetic-credential')
    with pytest.raises(LookupError):
        with smoke.offline_transport(evidence, root):
            with pytest.raises(RuntimeError, match='already active'):
                with smoke.offline_transport(evidence, root):
                    pass
            synth = SpeechSynthesizer(model='cosyvoice-v3-flash', voice='longanyang')
            with pytest.raises(RuntimeError, match='credential'):
                synth.call('synthetic text')
            for call in (lambda: socket.getaddrinfo('external.invalid', 443),
                         lambda: socket.socket().connect(('192.0.2.1', 443)),
                         lambda: socket.socket().connect_ex(('192.0.2.1', 443))):
                with pytest.raises(RuntimeError, match='blocks outbound'):
                    call()
            assert socket.getaddrinfo('127.0.0.1', 80) == ['local-result']
            assert forwarded == [('127.0.0.1', 80)]
            raise LookupError('exercise exceptional teardown')
    assert (SpeechSynthesizer.call, socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo) == original
    assert len(evidence.payload['blocked_sockets']) == 3 and evidence.payload['sdk_calls'] == []


def test_no_control_endpoints_are_added_to_production_app():
    from src.apps.comic_gen.api import create_app
    assert all(not getattr(route, 'path', '').startswith('/__smoke') for route in create_app().routes)
