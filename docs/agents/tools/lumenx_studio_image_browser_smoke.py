#!/usr/bin/env python3
"""Offline Studio image browser fixture: real API/Core, fake HTTP transport only.

Never launch against user data. Resume requires this harness's marker and fixed
static hashes. Counters/audit survive restarts in a separate evidence directory.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager, contextmanager
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time

sys.dont_write_bytecode = True
_SCRIPT = Path(__file__).resolve()
_spec = importlib.util.spec_from_file_location('studio_smoke_base', _SCRIPT.with_name('lumenx_generation_browser_smoke.py'))
assert _spec and _spec.loader
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
KIND = 'lumenx.studio_image.offline_browser_smoke.v1'
SECRET = 'synthetic-example-studio-image'
ORIGIN = 'https://dashscope.aliyuncs.com'
RESULT_ORIGIN = 'https://studio-image-smoke.invalid'
MARKER = 'studio-image-smoke.json'
FIXTURES = {
    'local': {'project_id': 'image-local', 'asset_id': 'local-character', 'scene_id': 'local-scene', 'prop_id': 'local-prop', 'owner_kind': 'script', 'owner_id': 'image-local'},
    'series': {'project_id': 'image-episode', 'asset_id': 'series-character', 'scene_id': 'series-scene', 'prop_id': 'series-prop', 'owner_kind': 'series', 'owner_id': 'image-series'},
    'global': {'project_id': 'image-global', 'asset_id': 'global-character', 'scene_id': 'global-scene', 'prop_id': 'global-prop', 'owner_kind': 'global', 'owner_id': 'library'},
}


def prepare_root(root: Path, static: Path, *, frontend_commit: str, backend_commit: str, resume: bool) -> Path:
    root = root.expanduser().resolve()
    marker = root / MARKER
    if resume:
        if not marker.is_file() or marker.is_symlink():
            raise ValueError('Resume requires this harness marker')
        saved = json.loads(marker.read_text())
        if (saved.get('kind'), saved.get('frontend_commit'), saved.get('backend_commit')) != (KIND, frontend_commit, backend_commit):
            raise ValueError('Resume source identity changed')
        mounted = root / 'static'
        files = saved.get('static', {}).get('files')
        if not isinstance(files, list) or not files or mounted.is_symlink():
            raise ValueError('Resume static manifest is invalid')
        expected = set()
        for item in files:
            relative = item.get('path') if isinstance(item, dict) else None
            if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or any(part in {'..', '.'} for part in relative.split('/')):
                raise ValueError('Resume static path is invalid')
            path = mounted / relative
            if relative in expected or not path.resolve().is_relative_to(mounted) or base.sha256_file(path) != item.get('sha256'):
                raise ValueError('Resume static content changed')
            expected.add(relative)
        actual = set()
        for path in mounted.rglob('*'):
            if path.is_symlink():
                raise ValueError('Resume static refuses symlinks')
            if path.is_file():
                actual.add(path.relative_to(mounted).as_posix())
        if actual != expected or 'index.html' not in expected:
            raise ValueError('Resume static file set changed')
        if not (root / 'fixture.json').is_file():
            raise ValueError('Resume fixture was not initialized')
        return mounted
    base.ensure_owned_empty_data_root(root)
    mounted, manifest = base.freeze_static_snapshot(base.validate_static_source(static), root, frontend_commit)
    marker.write_text(json.dumps({'kind': KIND, 'frontend_commit': frontend_commit, 'backend_commit': backend_commit,
                                  'static': manifest}, indent=2) + '\n')
    return mounted


def open_evidence(path: Path, *, resume: bool):
    path = path.expanduser().resolve()
    if resume:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError('Resume evidence is missing or invalid')
        provider = payload.get('fake_provider', {})
        counts, events = provider.get('counts'), provider.get('events')
        if (payload.get('kind') != KIND or not isinstance(counts, dict) or set(counts) != {'submit', 'query', 'fetch'}
                or any(type(v) is not int or v < 0 for v in counts.values()) or not isinstance(events, list)
                or any(not isinstance(row, dict) or row.get('kind') not in counts for row in events)
                or any(sum(row['kind'] == kind for row in events) != value for kind, value in counts.items())):
            raise ValueError('Resume evidence is missing or invalid')
    else:
        if path.exists():
            raise ValueError('Refuse to overwrite existing evidence')
        payload = {'kind': KIND, 'fake_provider': {'counts': {'submit': 0, 'query': 0, 'fetch': 0}, 'events': []},
                   'blocked_sockets': [], 'api_audit': [], 'controls': [], 'sessions': []}
    return base.Evidence(path, payload)


def event(evidence, field, **values):
    with evidence.lock:
        evidence.payload.setdefault(field, []).append({'time': time.time(), **values})
    evidence.write()


class PollingClock:
    """Advance only owned provider polling; UI holds keep real time."""
    def __init__(self):
        self._lock = threading.Lock()
        self._monotonic = time.monotonic()
        self._wall = time.time()
        self._elapsed = 0.0

    def monotonic(self):
        with self._lock:
            return self._monotonic + self._elapsed

    def time(self):
        with self._lock:
            return self._wall + self._elapsed

    def sleep(self, seconds):
        if seconds < 0:
            raise ValueError('sleep length must be non-negative')
        with self._lock:
            self._elapsed += seconds


class Controls:
    def __init__(self, evidence, pause_seconds):
        if not 0 < pause_seconds <= 300:
            raise ValueError('pause_seconds must be between 0 and 300')
        self.evidence = evidence
        self.pause_seconds = pause_seconds
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.pause_number = None
        self.paused = False
        self.interrupted = False
        self.fail_completion = False

    def state(self):
        with self.lock:
            return {'paused': self.paused, 'pause_number': self.pause_number, 'fail_completion': self.fail_completion,
                    'counts': dict(self.evidence.payload['fake_provider']['counts']), 'fixtures': FIXTURES}

    def control(self, value):
        if not isinstance(value, dict):
            raise ValueError('Control requires an object')
        action = value.get('action')
        with self.lock:
            if action == 'arm' and set(value) == {'action', 'mode'} and value['mode'] in {'pause_second_submit', 'fail_completion'}:
                if self.paused or self.pause_number is not None or self.fail_completion:
                    raise ValueError('A fault is already armed')
                if value['mode'] == 'pause_second_submit':
                    self.pause_number = self.evidence.payload['fake_provider']['counts']['submit'] + 2
                    self.release.clear()
                    self.interrupted = False
                else:
                    self.fail_completion = True
            elif action in {'release_submit', 'interrupt_submit'} and set(value) == {'action'}:
                self.interrupted = action == 'interrupt_submit'
                self.release.set()
            else:
                raise ValueError('Unknown or malformed harness control')
        event(self.evidence, 'controls', **value)
        return self.state()

    def wait_for_submission(self, number):
        import requests
        with self.lock:
            if number != self.pause_number:
                return
            self.paused = True
        event(self.evidence, 'controls', action='second_submit_paused', number=number)
        released = self.release.wait(self.pause_seconds)
        with self.lock:
            interrupted = self.interrupted or not released
            self.paused = False
            self.pause_number = None
        if interrupted:
            raise requests.Timeout('Synthetic interrupted provider response')

    def commit(self, pipeline, original, payload):
        from src.utils.json_storage import StorageError
        with self.lock:
            armed = self.fail_completion
        if armed:
            for name, contents in payload.items():
                if name not in {'projects.json', 'series.json', 'library_assets.json'}:
                    continue
                owners = [contents] if name == 'library_assets.json' else contents.values()
                for owner in owners:
                    for request in owner.get('asset_image_requests', []):
                        for member in request['members']:
                            record = member['execution']
                            if record['phase'] == 'completed' and record['prepared_artifact']:
                                saved = pipeline.asset_image_service._find_request(request_id=request['id'])
                                old = next((m.execution for m in saved.members if m.variant_id == member['variant_id']), None) if saved else None
                                if old is not None and old.schema_version == 3 and old.phase == 'observed' and old.prepared_artifact is not None:
                                    with self.lock:
                                        self.fail_completion = False
                                    final = pipeline.data_dir / record['output_ref']
                                    event(self.evidence, 'controls', action='completion_checkpoint_failed',
                                          attempt_id=record['attempt_id'], final_exists=final.is_file())
                                    raise StorageError('Synthetic completion checkpoint interruption')
        return original(payload)


@contextmanager
def offline_transport(evidence, controls, *, restore=True):
    import requests
    from requests.adapters import BaseAdapter
    from src.models import wan27_image_http
    from src.apps.comic_gen import asset_image_execution
    from tests.frozen_image_fixtures import rgb_png_bytes

    if getattr(requests, '_studio_image_smoke_active', False):
        raise RuntimeError('Studio image smoke transport is already active')
    original_session = requests.Session
    original_interval = wan27_image_http._POLL_INTERVAL_SECONDS
    original_clock = asset_image_execution.time
    transport_lock = threading.Lock()
    original_connect, original_connect_ex, original_dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    tasks = {}
    class Stream(io.BytesIO):
        def release_conn(self):
            self.close()
    class Adapter(BaseAdapter):
        def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
            url, method = request.url, request.method
            if not isinstance(url, str) or not isinstance(method, str):
                raise RuntimeError('Unexpected incomplete HTTP request')
            if method == 'POST' and url == ORIGIN + '/api/v1/services/aigc/image-generation/generation':
                if request.headers.get('Authorization') != 'Bearer ' + SECRET:
                    raise RuntimeError('Unexpected non-synthetic provider credential')
                if not isinstance(request.body, (str, bytes, bytearray)):
                    raise RuntimeError('Unexpected provider request body')
                payload = json.loads(request.body)
                if payload['model'] not in {'wan2.7-image', 'wan2.7-image-pro'} or payload['parameters'].get('n') != 1:
                    raise RuntimeError('Unexpected provider payload')
                with transport_lock:
                    with evidence.lock:
                        number = evidence.payload['fake_provider']['counts']['submit'] + 1
                    identity = f'studio-image-{number}'
                    tasks[identity] = rgb_png_bytes(identity, width=320, height=240)
                    evidence.append_provider_event('submit', {'method': method, 'url': url, 'task_id': identity, 'payload': payload,
                                                              'authorization': 'Bearer ' + SECRET})
                controls.wait_for_submission(number)
                body = {'output': {'task_id': identity, 'task_status': 'PENDING'}}
            elif method == 'GET' and url.startswith(ORIGIN + '/api/v1/tasks/'):
                identity = url.rsplit('/', 1)[-1]
                if identity not in tasks or request.headers.get('Authorization') != 'Bearer ' + SECRET:
                    raise RuntimeError('Unexpected task query')
                evidence.append_provider_event('query', {'method': method, 'url': url, 'task_id': identity})
                body = {'output': {'task_id': identity, 'task_status': 'SUCCEEDED', 'choices': [{'message': {'role': 'assistant',
                        'content': [{'type': 'image', 'image': f'{RESULT_ORIGIN}/{identity}.png'}]}}]}}
            elif method == 'GET' and url.startswith(RESULT_ORIGIN + '/'):
                identity = url.rsplit('/', 1)[-1].removesuffix('.png')
                if url != f'{RESULT_ORIGIN}/{identity}.png' or identity not in tasks or 'Authorization' in request.headers or 'Cookie' in request.headers:
                    raise RuntimeError('Unexpected result download')
                evidence.append_provider_event('fetch', {'method': method, 'url': url, 'task_id': identity})
                body = tasks[identity]
            else:
                raise RuntimeError('Unexpected HTTP request blocked by Studio image smoke')
            response = requests.Response()
            response.status_code = 200
            response.headers['Content-Type'] = 'image/png' if isinstance(body, bytes) else 'application/json'
            response.url, response.request = url, request
            response.raw = Stream(body if isinstance(body, bytes) else json.dumps(body).encode())
            return response
        def close(self):
            pass
    def session():
        instance = original_session()
        instance.trust_env = False
        instance.mount('https://', Adapter())
        instance.mount('http://', Adapter())
        return instance
    def allowed(host):
        try:
            return ipaddress.ip_address(str(host)).is_loopback
        except ValueError:
            return False
    def reject(address):
        evidence.append_blocked_socket({'address': repr(address), 'time': time.time()})
        raise RuntimeError('Studio image smoke blocks outbound socket or DNS')
    def connect(sock, address):
        if not isinstance(address, tuple) or not allowed(address[0]):
            reject(address)
        return original_connect(sock, address)
    def connect_ex(sock, address):
        if not isinstance(address, tuple) or not allowed(address[0]):
            reject(address)
        return original_connect_ex(sock, address)
    def dns(host, *args, **kwargs):
        if not allowed(host):
            reject(host)
        return original_dns(host, *args, **kwargs)
    # Synthetic completion is immediate; retain the real HTTP query/parser path.
    wan27_image_http._POLL_INTERVAL_SECONDS = 0
    asset_image_execution.time = PollingClock()
    setattr(requests, '_studio_image_smoke_active', True)
    requests.Session = session
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, dns
    try:
        yield
    finally:
        controls.interrupted = True
        controls.release.set()
        if restore:
            requests.Session = original_session
            wan27_image_http._POLL_INTERVAL_SECONDS = original_interval
            asset_image_execution.time = original_clock
            socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = original_connect, original_connect_ex, original_dns
            delattr(requests, '_studio_image_smoke_active')
        # CLI process retains the guard through shutdown: a cancelled async task
        # can leave its provider thread alive until process exit.


def seed_fixture(pipeline, root):
    from src.apps.comic_gen.models import Character, Prop, Scene, Script, Series
    from tests.frozen_image_fixtures import rgb_png_bytes
    uploads = pipeline.data_dir / 'uploads'
    uploads.mkdir(exist_ok=True)
    hashes = {}
    for label in ('choice', 'alternate'):
        path = uploads / f'retained-{label}.png'
        path.write_bytes(rgb_png_bytes(label, width=320, height=240))
        hashes[path.name] = base.sha256_file(path)
    def character(identity, title):
        return Character.model_validate({'id': identity, 'name': title, 'description': 'Synthetic browser fixture, red coat.',
            'image_url': 'uploads/retained-choice.png', 'status': 'completed', 'reference_sheet': {
                'selected_image_id': 'choice', 'image_prompt': 'Preserve this original prompt', 'image_updated_at': 1,
                'image_variants': [{'id': label, 'url': f'uploads/retained-{label}.png', 'created_at': i + 1, 'is_favorited': i == 0}
                                   for i, label in enumerate(('choice', 'alternate'))]}})
    for route, fixture in FIXTURES.items():
        project = Script.model_validate({'id': fixture['project_id'], 'title': f'Studio image · {route}', 'original_text': 'Offline fixture',
                                         'created_at': 1, 'updated_at': 1, 'workflow_mode': 'r2v'})
        project.model_settings.t2i_model = 'wan2.7-image'
        project.model_settings.character_aspect_ratio = '1:1'
        target = character(fixture['asset_id'], {'local': '本地角色', 'series': '系列角色', 'global': '全局角色'}[route])
        if route == 'local':
            project.characters = [target]
        elif route == 'series':
            series = Series.model_validate({'id': fixture['owner_id'], 'title': 'Studio image · shared series', 'created_at': 1, 'updated_at': 1,
                                            'episode_ids': [project.id], 'characters': [target]})
            series.model_settings = project.model_settings.model_copy(deep=True)
            pipeline.series_store[series.id] = series
            project.series_id = series.id
        else:
            pipeline.library_store.characters = [target]
        owner = project if route == 'local' else pipeline.series_store[fixture['owner_id']] if route == 'series' else pipeline.library_store
        for kind, model, label in (('scene', Scene, '场景'), ('prop', Prop, '道具')):
            image = model.model_validate({
                'id': fixture[f'{kind}_id'], 'name': f"{route} · {label}",
                'description': f'Synthetic {kind} browser fixture.', 'status': 'completed',
                'image_url': 'uploads/retained-choice.png', 'video_prompt': 'Preserve independent motion',
                'image_asset': {'selected_id': 'choice', 'variants': [
                    {'id': choice, 'url': f'uploads/retained-{choice}.png', 'created_at': i + 1, 'is_favorited': i == 0}
                    for i, choice in enumerate(('choice', 'alternate'))]},
            })
            getattr(owner, f'{kind}s').append(image)
        pipeline.scripts[project.id] = project
    pipeline._commit_storage('projects.json', 'series.json', 'library_assets.json')
    (root / 'fixture.json').write_text(json.dumps({'kind': KIND, 'fixtures': FIXTURES, 'media_hashes': hashes}, ensure_ascii=False, indent=2) + '\n')


def create_smoke_app(root: Path, static: Path, evidence, *, resume=False, pause_seconds=120, restore_transport=True):
    from fastapi import Body, HTTPException
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app
    root = root.resolve()
    if not (root / MARKER).is_file() or evidence.path.is_relative_to(root):
        raise ValueError('Harness marker and separate evidence directory are required')
    if (root / 'fixture.json').exists() != resume:
        raise ValueError('Fixture can only be seeded once; use explicit resume')
    marker_hash = base.sha256_file(root / MARKER)
    if resume:
        if evidence.payload.get('fixture_root') != str(root) or evidence.payload.get('fixture_marker_sha256') != marker_hash:
            raise ValueError('Resume evidence does not belong to this fixture')
        saved = json.loads((root / MARKER).read_text())
        static = prepare_root(root, static, frontend_commit=saved['frontend_commit'], backend_commit=saved['backend_commit'], resume=True)
    else:
        evidence.update(fixture_root=str(root), fixture_marker_sha256=marker_hash)
    controls = Controls(evidence, pause_seconds)
    app = create_app(data_dir=root / 'output', user_data_dir=root / 'user-config', load_config=False,
                     setup_logs=False, recover_orphan_tasks=True, warmup_demucs=False, access_mode='local')
    app.mount('/static', StaticFiles(directory=str(static), html=True), name='static')
    lifecycle = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(target):
        with offline_transport(evidence, controls, restore=restore_transport):
            async with lifecycle(target):
                env = target.state.runtime_environment
                env.update({'DASHSCOPE_API_KEY': SECRET}, remove_keys=tuple(k for k in env.snapshot() if k != 'DASHSCOPE_API_KEY'), persist=lambda *_: None)
                if not resume:
                    seed_fixture(target.state.pipeline, root)
                commit = target.state.pipeline._storage.commit
                target.state.pipeline._storage.commit = lambda payload: controls.commit(target.state.pipeline, commit, payload)
                event(evidence, 'sessions', pid=os.getpid(), resume=resume, started_at=time.time(), counts=dict(evidence.payload['fake_provider']['counts']))
                try:
                    yield
                finally:
                    controls.interrupted = True
                    controls.release.set()
                    event(evidence, 'sessions', pid=os.getpid(), stopped_at=time.time(), counts=dict(evidence.payload['fake_provider']['counts']))
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
    frontend_commit = base.git_output(['rev-parse', '--verify', args.frontend_freeze_commit + '^{commit}'])
    backend_commit = base.git_output(['rev-parse', '--verify', args.backend_source_commit + '^{commit}'])
    if not frontend_commit or not backend_commit:
        raise SystemExit('Both source references must resolve to fixed commits')
    backend_hashes = base.validate_source_reference(backend_commit)
    root, evidence_path = args.data_root.expanduser().resolve(), args.evidence.expanduser().resolve()
    static = prepare_root(root, args.static_root, frontend_commit=frontend_commit, backend_commit=backend_commit, resume=args.resume)
    base.sanitize_environment(root)
    os.chdir(root)
    base.bootstrap_paths(args.python)
    evidence = open_evidence(evidence_path, resume=args.resume)
    harness_hashes = {str(path.relative_to(base.REPO_ROOT)): base.sha256_file(path) for path in (
        _SCRIPT, _SCRIPT.with_name('lumenx_generation_browser_smoke.py'), base.REPO_ROOT / 'tests/frozen_image_fixtures.py')}
    if args.resume and evidence.payload.get('harness_source_hashes') != harness_hashes:
        raise SystemExit('Resume harness source changed')
    evidence.update(backend_source_hashes=backend_hashes, backend_source_commit=backend_commit,
                    frontend_source_commit=frontend_commit, harness_source_hashes=harness_hashes)
    app = create_smoke_app(root, static, evidence, resume=args.resume, restore_transport=False)
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
        health = base.wait_for_health(host, port)
        check = base.check_static(host, port)
        evidence.update(ready=True, pid=os.getpid(), port=port, url=f'http://{host}:{port}/static/index.html', checks={'health': health, 'static': check})
        print(json.dumps({'url': f'http://{host}:{port}/static/index.html', 'pid': os.getpid(), 'evidence': str(evidence.path)}), flush=True)
        while thread.is_alive() and not server.should_exit:
            time.sleep(0.2)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        evidence.update(stopped=not thread.is_alive(), stopped_at=time.time())


if __name__ == '__main__':
    main()
