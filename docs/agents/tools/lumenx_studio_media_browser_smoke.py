#!/usr/bin/env python3
"""Finite offline Studio media rehearsal; real API/storage/FFmpeg, synthetic SDK audio.

Only launch on an empty owned root with separately retained evidence. Seeded
video takes/background cache are fixtures, not provider or Demucs acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager, contextmanager
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True
_SCRIPT = Path(__file__).resolve()
_spec = importlib.util.spec_from_file_location('studio_media_helpers', _SCRIPT.with_name('lumenx_studio_image_browser_smoke.py'))
assert _spec and _spec.loader
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
base, event = helpers.base, helpers.event
KIND = 'lumenx.studio_media.offline_browser_smoke.v1'
MARKER = 'studio-media-smoke.json'
SECRET = 'synthetic-example-studio-media'
F2 = 'video/media-f2.mp4'
SDK_AUDIO = 'audio/media-sdk.mp3'
_PATCH_LOCK = threading.Lock()


def _read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Owned manifest is missing or a symlink')
    return json.loads(path.read_text())


def _owned_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or any(p in {'', '.', '..'} for p in relative.split('/')):
        raise ValueError('Invalid owned relative path')
    path = root / relative
    if any(p.is_symlink() for p in (path, *path.parents) if p == root or root in p.parents):
        raise ValueError('Owned paths must not contain symlinks')
    return path


def prepare_root(root: Path, static: Path, *, frontend_commit: str, backend_commit: str, resume: bool) -> Path:
    if root.is_symlink():
        raise ValueError('Data root must not be a symlink')
    root = root.expanduser().resolve()
    if not resume:
        base.ensure_owned_empty_data_root(root)
        mounted, manifest = base.freeze_static_snapshot(base.validate_static_source(static), root, frontend_commit)
        (root / MARKER).write_text(json.dumps({'kind': KIND, 'frontend_commit': frontend_commit,
            'backend_commit': backend_commit, 'static': manifest}, indent=2) + '\n')
        return mounted
    saved = _read_json(root / MARKER)
    if (saved.get('kind'), saved.get('frontend_commit'), saved.get('backend_commit')) != (KIND, frontend_commit, backend_commit):
        raise ValueError('Resume source identity changed')
    _read_json(root / 'fixture.json')
    return _validate_static(root, saved)


def _validate_static(root, saved):
    mounted = _owned_path(root, 'static')
    rows = saved.get('static', {}).get('files')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Resume static manifest is invalid')
    expected = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Resume static manifest is invalid')
        path = _owned_path(mounted, row.get('path'))
        if row['path'] in expected or base.sha256_file(path) != row.get('sha256'):
            raise ValueError('Resume static content changed')
        expected[row['path']] = row['sha256']
    actual = set()
    for path in mounted.rglob('*'):
        if path.is_symlink():
            raise ValueError('Resume static refuses symlinks')
        if path.is_file():
            actual.add(path.relative_to(mounted).as_posix())
    if actual != set(expected) or 'index.html' not in actual:
        raise ValueError('Resume static file set changed')
    return mounted


def open_evidence(path: Path, *, resume: bool):
    if path.is_symlink():
        raise ValueError('Evidence must not be a symlink')
    path = path.expanduser().resolve()
    if resume:
        payload = _read_json(path)
        if (not isinstance(payload, dict) or payload.get('kind') != KIND
                or any(not isinstance(payload.get(key), list) for key in ('sdk_calls', 'blocked_sockets', 'api_audit', 'controls', 'sessions'))
                or any(not isinstance(row, dict) or row.get('number') != i + 1 for i, row in enumerate(payload['sdk_calls']))):
            raise ValueError('Resume evidence is invalid')
    else:
        if path.exists():
            raise ValueError('Refuse to overwrite existing evidence')
        payload = {'kind': KIND, 'sdk_calls': [], 'blocked_sockets': [], 'api_audit': [], 'controls': [], 'sessions': []}
    return base.Evidence(path, payload)


def _validate_fixture(root, evidence):
    for name, key in ((MARKER, 'fixture_marker_sha256'), ('fixture.json', 'fixture_sha256')):
        path = _owned_path(root, name)
        if not evidence.payload.get(key) or base.sha256_file(path) != evidence.payload[key]:
            raise ValueError('Fixture identity changed')
    if evidence.payload.get('fixture_root') != str(root):
        raise ValueError('Evidence belongs to another fixture')
    fixture = _read_json(root / 'fixture.json')
    if fixture.get('kind') != KIND:
        raise ValueError('Unexpected fixture kind')
    return fixture


def _validate_media(root, fixture):
    for row in fixture['media']:
        path = _owned_path(root, 'output/' + row['path'])
        if row['path'] == F2:
            retained = _owned_path(root, 'output/' + F2 + '.retained')
            if path.exists() == retained.exists():
                raise ValueError('The controlled input must exist in exactly one location')
            path = path if path.exists() else retained
        if base.sha256_file(path) != row['sha256']:
            raise ValueError('Original fixture media changed')


class Controls:
    def __init__(self, root, evidence):
        self.root, self.evidence = root, evidence
        self.lock = threading.Lock()

    def state(self):
        return {'f2_stashed': (self.root / 'output' / (F2 + '.retained')).is_file(),
                'sdk_calls': len(self.evidence.payload['sdk_calls']),
                'series_id': 'media-series', 'project_ids': ['media-episode-1', 'media-episode-2'],
                'untouched_project_id': 'media-untouched'}

    def control(self, value):
        if not isinstance(value, dict) or set(value) != {'action'} or value['action'] not in {'stash_f2', 'restore_f2'}:
            raise ValueError('Unknown or malformed harness control')
        with self.lock:
            fixture = _validate_fixture(self.root, self.evidence)
            expected = next(row['sha256'] for row in fixture['media'] if row['path'] == F2)
            original = _owned_path(self.root, 'output/' + F2)
            retained = _owned_path(self.root, 'output/' + F2 + '.retained')
            source, destination = (original, retained) if value['action'] == 'stash_f2' else (retained, original)
            if not source.is_file() or destination.exists() or base.sha256_file(source) != expected:
                raise ValueError('Controlled input changed or destination exists')
            # Hard-link then unlink is a same-directory move which cannot replace
            # a destination created between validation and the filesystem call.
            try:
                os.link(source, destination)
                source.unlink()
            except OSError as exc:
                raise ValueError('Cannot safely move controlled fixture input') from exc
            event(self.evidence, 'controls', **value, path=F2, sha256=expected)
            return self.state()


@contextmanager
def offline_transport(evidence, root, *, restore_socket=True):
    from dashscope.audio.tts_v2 import SpeechSynthesizer
    with _PATCH_LOCK:
        if getattr(SpeechSynthesizer, '_lumenx_media_smoke_active', False):
            raise RuntimeError('Studio media SDK transport is already active')
        setattr(SpeechSynthesizer, '_lumenx_media_smoke_active', True)
    original_call = SpeechSynthesizer.call
    originals = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def synthesize(synthesizer, text, timeout_millis=None):
        if synthesizer.apikey != SECRET:
            raise RuntimeError('Unexpected non-synthetic SDK credential')
        payload = json.loads(synthesizer.request.get_start_request())['payload']
        if payload['model'] != 'cosyvoice-v3-flash' or payload['parameters'].get('voice') not in {'longanyang', 'longanhuan'}:
            raise RuntimeError('Only the finite CosyVoice system-voice fixture is supported')
        fixture = _validate_fixture(root, evidence)
        path = _owned_path(root, 'output/' + SDK_AUDIO)
        expected = next(row['sha256'] for row in fixture['media'] if row['path'] == SDK_AUDIO)
        audio = path.read_bytes()
        if hashlib.sha256(audio).hexdigest() != expected:
            raise RuntimeError('Synthetic SDK audio changed')
        with evidence.lock:
            evidence.payload['sdk_calls'].append({'number': len(evidence.payload['sdk_calls']) + 1,
                'time': time.time(), 'text': text, 'model': payload['model'], **payload['parameters'],
                'audio_sha256': expected})
        evidence.write()
        return audio

    def allowed(host):
        try:
            return ipaddress.ip_address(str(host)).is_loopback
        except ValueError:
            return False

    def reject(address):
        evidence.append_blocked_socket({'address': repr(address), 'time': time.time()})
        raise RuntimeError('Studio media smoke blocks outbound socket or DNS')

    def connect(sock, address):
        if not isinstance(address, tuple) or not allowed(address[0]):
            reject(address)
        return originals[0](sock, address)

    def connect_ex(sock, address):
        if not isinstance(address, tuple) or not allowed(address[0]):
            reject(address)
        return originals[1](sock, address)

    def dns(host, *args, **kwargs):
        if not allowed(host):
            reject(host)
        return originals[2](host, *args, **kwargs)

    setattr(SpeechSynthesizer, 'call', synthesize)
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, dns
    try:
        yield
    finally:
        SpeechSynthesizer.call = original_call
        if restore_socket:
            socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = originals
        # CLI leaves the outbound guard in place until process exit: a worker
        # cancelled at shutdown can still finish inside an executor thread.
        delattr(SpeechSynthesizer, '_lumenx_media_smoke_active')


def seed_fixture(pipeline, root):
    from src.apps.comic_gen.models import Character, Script, Series, StoryboardFrame, VideoTask
    from tests.frozen_image_fixtures import rgb_png_bytes
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('Real FFmpeg is required for the media fixture')
    media = []

    def record(relative, **metadata):
        media.append({'path': relative, 'sha256': base.sha256_file(pipeline.data_dir / relative), **metadata})

    def render(relative, source, *options, **metadata):
        path = pipeline.data_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', source,
                        *options, str(path)], check=True, capture_output=True, timeout=30)
        record(relative, **metadata)

    clips = [('media-A', 'media-f1', 'red', 440), ('media-B', 'media-f1', 'blue', 880),
             ('media-f2-take', 'media-f2', 'green', 660), ('media-f3-take', 'media-f3', 'yellow', 1320)]
    tasks = []
    for identity, frame_id, color, frequency in clips:
        relative = f"video/{identity.removesuffix('-take')}.mp4"
        render(relative, f'color=c={color}:s=160x90:d=1:r=25[out0];sine=frequency={frequency}:duration=1:sample_rate=44100[out1]',
               '-shortest', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-c:a', 'aac', color=color, frequency=frequency, duration=1)
        tasks.append(VideoTask.model_validate({'id': identity, 'project_id': 'media-episode-1', 'frame_id': frame_id,
            'image_url': 'uploads/media-avatar.png', 'prompt': 'Synthetic pre-seeded take; no generation performed',
            'status': 'completed', 'video_url': relative, 'duration': 1}))
    for relative, frequency, codec in ((SDK_AUDIO, 777, 'libmp3lame'), ('audio/media-A-background.wav', 220, 'pcm_s16le'),
                                       ('presets/bgm/calm_warm.mp3', 1000, 'libmp3lame')):
        render(relative, f'sine=frequency={frequency}:duration=1:sample_rate=44100', '-c:a', codec, frequency=frequency, duration=1)
    avatar = pipeline.data_dir / 'uploads/media-avatar.png'
    avatar.write_bytes(rgb_png_bytes('media-hero', width=320, height=240))
    record('uploads/media-avatar.png')
    hero = Character.model_validate({'id': 'media-hero', 'name': '声音旅程主角', 'description': 'Synthetic shared character.',
        'image_url': 'uploads/media-avatar.png', 'status': 'completed', 'reference_sheet': {
            'selected_image_id': 'media-avatar', 'image_variants': [{'id': 'media-avatar', 'url': 'uploads/media-avatar.png', 'created_at': 1}]}})
    for episode in (1, 2):
        frames = [StoryboardFrame.model_validate({'id': f'media-f{i}', 'scene_id': '', 'character_ids': [hero.id], 'speaker': hero.name,
            'action_description': f'Synthetic ordered shot {i}', 'dialogue': f'第{episode}集，第{i}个镜头的合成对白。',
            'selected_video_id': tasks[i - 1 if i == 1 else i].id if episode == 1 else None})
            for i in ([1, 2, 3] if episode == 1 else [4])]
        if episode == 1:
            frames[0].bg_audio_url = 'audio/media-A-background.wav'
            frames[0].bg_audio_source_video = tasks[0].video_url
        project = Script.model_validate({'id': f'media-episode-{episode}', 'title': f'Studio media · 第{episode}集',
            'original_text': 'Synthetic offline media rehearsal.', 'created_at': 1, 'updated_at': 1,
            'series_id': 'media-series', 'episode_number': episode, 'workflow_mode': 'r2v',
            'frames': frames, 'video_tasks': tasks if episode == 1 else []})
        pipeline.scripts[project.id] = project
    pipeline.series_store['media-series'] = Series.model_validate({'id': 'media-series', 'title': 'Studio media · 共享声音',
        'created_at': 1, 'updated_at': 1, 'workflow_mode': 'r2v', 'characters': [hero], 'episode_ids': ['media-episode-1', 'media-episode-2']})
    untouched = Script.model_validate({'id': 'media-untouched', 'title': 'Untouched control',
        'original_text': 'Must stay unchanged', 'created_at': 1, 'updated_at': 1})
    pipeline.scripts[untouched.id] = untouched
    pipeline._commit_storage('projects.json', 'series.json', 'library_assets.json')
    (root / 'fixture.json').write_text(json.dumps({'kind': KIND, 'media': media,
        'untouched_project': untouched.model_dump(), 'scope': 'Seeded takes and cached background; SDK MP3 tone only, no real provider/Demucs.'},
        ensure_ascii=False, indent=2) + '\n')


def create_smoke_app(root: Path, static: Path, evidence, *, resume=False, restore_socket=True):
    from fastapi import Body, HTTPException
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app
    if root.is_symlink():
        raise ValueError('Data root must not be a symlink')
    root = root.resolve()
    saved = _read_json(root / MARKER)
    if saved.get('kind') != KIND or evidence.path.is_relative_to(root):
        raise ValueError('Harness marker and separate evidence directory are required')
    if (root / 'fixture.json').exists() != resume:
        raise ValueError('Fixture can only be seeded once; use explicit resume')
    marker_hash = base.sha256_file(root / MARKER)
    if resume:
        static = prepare_root(root, static, frontend_commit=saved['frontend_commit'], backend_commit=saved['backend_commit'], resume=True)
        _validate_media(root, _validate_fixture(root, evidence))
    else:
        if evidence.payload.get('kind') != KIND or evidence.payload.get('fixture_root') is not None:
            raise ValueError('Initial evidence must belong to a new media fixture')
        if static.resolve() != _validate_static(root, saved):
            raise ValueError('Only the frozen static snapshot can be mounted')
        evidence.update(fixture_root=str(root), fixture_marker_sha256=marker_hash)
    controls = Controls(root, evidence)
    app = create_app(data_dir=root / 'output', user_data_dir=root / 'user-config', load_config=False,
        setup_logs=False, recover_orphan_tasks=True, warmup_demucs=False, access_mode='local')
    app.mount('/static', StaticFiles(directory=str(static), html=True), name='static')
    lifecycle = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(target):
        with offline_transport(evidence, root, restore_socket=restore_socket):
            previous_environment = dict(os.environ)
            try:
                base.sanitize_environment(root)
                # Preserve only the runner's two executable allowlists. Removing
                # them would make its still-active guard refuse real FFmpeg.
                for key in ('LUMENX_TEST_FFMPEG', 'LUMENX_TEST_FFPROBE'):
                    if key in previous_environment:
                        os.environ[key] = previous_environment[key]
                async with lifecycle(target):
                    env = target.state.runtime_environment
                    env.update({'DASHSCOPE_API_KEY': SECRET}, remove_keys=tuple(k for k in env.snapshot() if k != 'DASHSCOPE_API_KEY'), persist=lambda *_: None)
                    if not resume:
                        seed_fixture(target.state.pipeline, root)
                        evidence.update(fixture_sha256=base.sha256_file(root / 'fixture.json'))
                    event(evidence, 'sessions', pid=os.getpid(), resume=resume, started_at=time.time())
                    try:
                        yield
                    finally:
                        event(evidence, 'sessions', pid=os.getpid(), stopped_at=time.time())
            finally:
                os.environ.clear()
                os.environ.update(previous_environment)

    app.router.lifespan_context = lifespan

    @app.get('/__smoke/state')
    def state():
        return controls.state()

    @app.post('/__smoke/control')
    def control(value: dict = Body(...)):
        try:
            return controls.control(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.middleware('http')
    async def audit(request, call_next):
        try:
            response = await call_next(request)
        except BaseException:
            event(evidence, 'api_audit', method=request.method, path=request.url.path, status=500, raised=True)
            raise
        if not request.url.path.startswith(('/static', '/files')):
            event(evidence, 'api_audit', method=request.method, path=request.url.path, status=response.status_code)
        return response
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--static-root', required=True, type=Path)
    parser.add_argument('--frontend-freeze-commit', required=True)
    parser.add_argument('--backend-source-commit', required=True)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    host = base.validate_loopback_literal(args.host)
    frontend = base.git_output(['rev-parse', '--verify', args.frontend_freeze_commit + '^{commit}'])
    backend = base.git_output(['rev-parse', '--verify', args.backend_source_commit + '^{commit}'])
    if not frontend or not backend:
        raise SystemExit('Both source references must resolve to fixed commits')
    backend_hashes = base.validate_source_reference(backend)
    root, evidence_path = args.data_root.expanduser().absolute(), args.evidence.expanduser().absolute()
    static = prepare_root(root, args.static_root, frontend_commit=frontend, backend_commit=backend, resume=args.resume)
    root = root.resolve()
    base.sanitize_environment(root)
    os.chdir(root)
    base.bootstrap_paths(args.python)
    evidence = open_evidence(evidence_path, resume=args.resume)
    harness_hashes = {str(path.relative_to(base.REPO_ROOT)): base.sha256_file(path) for path in (
        _SCRIPT, _SCRIPT.with_name('lumenx_generation_browser_smoke.py'), _SCRIPT.with_name('lumenx_studio_image_browser_smoke.py'),
        base.REPO_ROOT / 'tests/frozen_image_fixtures.py')}
    if args.resume and evidence.payload.get('harness_source_hashes') != harness_hashes:
        raise SystemExit('Resume harness source changed')
    evidence.update(backend_source_hashes=backend_hashes, backend_source_commit=backend,
        frontend_source_commit=frontend, harness_source_hashes=harness_hashes)
    app = create_smoke_app(root, static, evidence, resume=args.resume, restore_socket=False)
    import uvicorn
    port = base.choose_port(host, args.port)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, reload=False, timeout_graceful_shutdown=3))

    def stop(signum, _frame):
        evidence.update(stopping_signal=signum)
        server.should_exit = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        health, static_check = base.wait_for_health(host, port), base.check_static(host, port)
        evidence.update(ready=True, pid=os.getpid(), port=port, url=f'http://{host}:{port}/static/index.html',
                        checks={'health': health, 'static': static_check})
        print(json.dumps({'url': evidence.payload['url'], 'pid': os.getpid(), 'evidence': str(evidence.path)}), flush=True)
        while thread.is_alive() and not server.should_exit:
            time.sleep(0.2)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        evidence.update(stopped=not thread.is_alive(), stopped_at=time.time())


if __name__ == '__main__':
    main()
