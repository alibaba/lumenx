#!/usr/bin/env python3
"""Fresh-only Agent browser harness: real WanxModel, external HTTP/LLM fixtures.

No provider class, scheduler, storage record or artifact proof is fabricated.
Only GET /__smoke/state is added. CLI network protection remains until exit.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import asynccontextmanager, contextmanager
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True
_SCRIPT = Path(__file__).resolve()
_spec = importlib.util.spec_from_file_location('atelier_agent_smoke_base', _SCRIPT.with_name('lumenx_generation_browser_smoke.py'))
assert _spec and _spec.loader
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
KIND = 'lumenx.atelier_agent.http_browser_smoke.v1'
MARKER = 'atelier-agent-smoke.json'
ORIGIN = 'https://dashscope.aliyuncs.com'
RESULT_ORIGIN = 'https://agent-result.invalid'
SECRET = 'synthetic-example-agent-browser'
SCENARIOS = [
    {'project_id': 'agent-single', 'title': 'Agent single turn', 'family': 'turn', 'planner': 'deterministic_core'},
    {'project_id': 'agent-loop', 'title': 'Agent guided loop', 'family': 'loop', 'planner': 'deterministic_core'},
    {'project_id': 'agent-model', 'title': 'Agent model guided loop', 'family': 'loop', 'planner': 'model_adapter'},
]


def prepare_root(root: Path, static: Path, *, frontend_commit: str, backend_commit: str) -> Path:
    root = base.ensure_owned_empty_data_root(root)
    mounted, manifest = base.freeze_static_snapshot(base.validate_static_source(static), root, frontend_commit)
    (root / MARKER).write_text(json.dumps({'kind': KIND, 'frontend_commit': frontend_commit,
        'backend_commit': backend_commit, 'static': manifest}, indent=2) + '\n')
    return mounted


def open_evidence(path: Path):
    path = path.expanduser().resolve()
    if path.exists():
        raise ValueError('Refuse to overwrite existing evidence')
    return base.Evidence(path, {'kind': KIND, 'transport_fixture': 'wan27-i2v-http-v1',
        'fake_provider': {'counts': {'submit': 0, 'query': 0, 'fetch': 0}, 'events': []},
        'fake_model_planner': {'enabled': True, 'plan': 'generation', 'boundary': 'external chat_result only',
                               'counts': {'init': 0, 'chat_result': 0}, 'events': []},
        'blocked_sockets': [], 'api_audit': [], 'sessions': []})


def event(evidence, field, **values):
    with evidence.lock:
        evidence.payload.setdefault(field, []).append(values)
    evidence.write()


def seed_fixture(root):
    from src.apps.atelier.models import AtelierProject
    from src.utils.media_probe import probe_media_file
    from src.utils.system_check import get_ffmpeg_path
    from tests.frozen_image_fixtures import rgb_png_bytes

    output = root / 'output'
    uploads = output / 'uploads'
    uploads.mkdir(parents=True)
    projects = {}
    media = {}
    for scenario in SCENARIOS:
        project_id = scenario['project_id']
        relative = f'uploads/{project_id}.png'
        content = rgb_png_bytes(project_id)
        (output / relative).write_bytes(content)
        media[relative] = hashlib.sha256(content).hexdigest()
        project = AtelierProject.model_validate({'id': project_id, 'title': scenario['title'], 'created_at': 1, 'updated_at': 1,
            'nodes': [{'id': 'image', 'project_id': project_id, 'type': 'image', 'title': 'First frame',
                       'media_urls': [relative], 'x': 0, 'y': 0},
                      {'id': 'video', 'project_id': project_id, 'type': 'video', 'title': scenario['title'],
                       'prompt': 'Slow sunrise above a blue sea', 'x': 400, 'y': 0,
                       'data': {'reference_node_ids': ['image']}}]})
        projects[project_id] = project.model_dump(mode='json')
    (output / 'atelier_projects.json').write_text(json.dumps(projects, indent=2) + '\n')
    provider_dir = root / 'provider-fixture'
    provider_dir.mkdir()
    video = provider_dir / 'result.mp4'
    subprocess.run([get_ffmpeg_path() or 'ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi',
                    '-i', 'color=c=blue:s=320x240:r=24:d=1', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(video)],
                   capture_output=True, check=True, timeout=20)
    probe_media_file(video, 'video')
    fixture = {'scenarios': SCENARIOS, 'images': media, 'video_sha256': base.sha256_file(video), 'video_size': video.stat().st_size}
    (root / 'fixture.json').write_text(json.dumps(fixture, indent=2) + '\n')
    config = root / 'user-config'
    config.mkdir()
    (config / 'config.json').write_text(json.dumps({'DASHSCOPE_API_KEY': SECRET,
        'DASHSCOPE_BASE_URL': ORIGIN, 'DASHSCOPE_REGION': 'cn-beijing'}) + '\n')
    return video, fixture


@contextmanager
def offline_transport(evidence, video: Path, image_hashes, *, restore=True):
    import requests
    from requests.adapters import HTTPAdapter
    from src.apps.comic_gen import llm_adapter

    if getattr(HTTPAdapter, '_agent_browser_smoke_active', False):
        raise RuntimeError('Agent smoke transport is already active')
    original_send = HTTPAdapter.send
    original_llm = llm_adapter.LLMAdapter
    original_connect, original_connect_ex, original_dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    tasks = set()
    lock = threading.Lock()
    video_bytes = video.read_bytes()

    class Stream(io.BytesIO):
        def release_conn(self):
            self.close()

    def send(self, request, stream=False, timeout=None, verify: bool | str = True, cert=None, proxies=None):
        url, method = request.url, request.method
        body = None
        if method == 'POST' and url == ORIGIN + '/api/v1/services/aigc/video-generation/video-synthesis':
            if request.headers.get('Authorization') != 'Bearer ' + SECRET:
                raise RuntimeError('Unexpected provider credential')
            payload = json.loads(request.body)
            media = payload.get('input', {}).get('media', [])
            if payload.get('model') != 'wan2.7-i2v' or len(media) != 1 or media[0].get('type') != 'first_frame':
                raise RuntimeError('Unexpected Wan I2V HTTP payload')
            url_value = media[0].get('url', '')
            if not url_value.startswith('data:image/png;base64,'):
                raise RuntimeError('Expected frozen PNG data URI')
            image_sha = hashlib.sha256(base64.b64decode(url_value.split(',', 1)[1], validate=True)).hexdigest()
            if image_sha not in image_hashes:
                raise RuntimeError('Unexpected frozen image')
            with lock:
                task_id = f'agent-task-{len(tasks) + 1}'
                tasks.add(task_id)
                evidence.append_provider_event('submit', {'method': method, 'url': url, 'task_id': task_id,
                    'model': payload['model'], 'parameters': payload.get('parameters'), 'first_frame_sha256': image_sha})
            body = {'request_id': task_id + '-request', 'output': {'task_id': task_id}}
        elif method == 'GET' and isinstance(url, str) and url.startswith(ORIGIN + '/api/v1/tasks/'):
            task_id = url.rsplit('/', 1)[-1]
            if task_id not in tasks or url != ORIGIN + '/api/v1/tasks/' + task_id or request.headers.get('Authorization') != 'Bearer ' + SECRET:
                raise RuntimeError('Unexpected task HTTP query')
            evidence.append_provider_event('query', {'method': method, 'url': url, 'task_id': task_id})
            body = {'output': {'task_id': task_id, 'task_status': 'SUCCEEDED', 'video_url': f'{RESULT_ORIGIN}/{task_id}.mp4'}}
        elif method == 'GET' and isinstance(url, str) and url.startswith(RESULT_ORIGIN + '/'):
            task_id = url.rsplit('/', 1)[-1].removesuffix('.mp4')
            if task_id not in tasks or url != f'{RESULT_ORIGIN}/{task_id}.mp4' or 'Authorization' in request.headers or 'Cookie' in request.headers:
                raise RuntimeError('Unexpected anonymous result HTTP download')
            evidence.append_provider_event('fetch', {'method': method, 'url': url, 'task_id': task_id})
            body = video_bytes
        else:
            raise RuntimeError('Unexpected HTTP blocked by Agent smoke')
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        response = requests.Response()
        response.status_code = 200
        response.url, response.request = url, request
        response.headers.update({'Content-Type': 'video/mp4' if isinstance(body, bytes) else 'application/json',
                                 'Content-Length': str(len(data))})
        response.raw = Stream(data)
        return response

    def dns(host, *args, **kwargs):
        try:
            allowed = ipaddress.ip_address(str(host)).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            evidence.append_blocked_socket({'address': str(host), 'time': time.time()})
            raise RuntimeError('Agent smoke blocks external DNS')
        return original_dns(host, *args, **kwargs)

    setattr(HTTPAdapter, '_agent_browser_smoke_active', True)
    HTTPAdapter.send = send
    base.install_socket_guard(evidence)
    socket.getaddrinfo = dns
    try:
        base.install_fake_model_planner('generation', evidence)
        yield
    finally:
        if restore:
            HTTPAdapter.send = original_send
            llm_adapter.LLMAdapter = original_llm
            socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = original_connect, original_connect_ex, original_dns
            delattr(HTTPAdapter, '_agent_browser_smoke_active')
        # The standalone process intentionally retains all guards until exit.


def create_smoke_app(root: Path, static: Path, evidence, *, restore_transport=True):
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app
    root, static = root.expanduser().resolve(), static.expanduser().resolve()
    marker = root / MARKER
    if (not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text()).get('kind') != KIND
            or static != root / 'static' or evidence.path.is_relative_to(root)):
        raise ValueError('Harness marker, frozen static and separate evidence directory are required')
    if (root / 'fixture.json').exists() or (root / 'output').exists():
        raise ValueError('Harness requires a fresh fixture; resume is unsupported')
    video, fixture = seed_fixture(root)
    evidence.update(fixture_root=str(root), fixture_marker_sha256=base.sha256_file(marker), fixture=fixture,
                    dispatcher_interval_seconds=0.05)
    app = create_app(data_dir=root / 'output', user_data_dir=root / 'user-config', load_config=True,
                     setup_logs=False, recover_orphan_tasks=True, warmup_demucs=False, access_mode='local')
    app.mount('/static', StaticFiles(directory=str(static), html=True), name='static')
    lifecycle = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(target):
        with offline_transport(evidence, video, set(fixture['images'].values()), restore=restore_transport):
            async with lifecycle(target):
                # Only shorten this real instance's recovery scan in the harness.
                target.state.atelier_generation_dispatcher._interval_seconds = 0.05
                target.state.atelier_generation_dispatcher.wake()
                event(evidence, 'sessions', pid=os.getpid(), started_at=time.time())
                try:
                    yield
                finally:
                    event(evidence, 'sessions', pid=os.getpid(), stopped_at=time.time())
    app.router.lifespan_context = lifespan

    @app.get('/__smoke/state')
    def state():
        with evidence.lock:
            counts = dict(evidence.payload['fake_provider']['counts'])
            model_counts = dict(evidence.payload['fake_model_planner']['counts'])
        return {'kind': KIND, 'counts': counts, 'scenarios': SCENARIOS, 'model_counts': model_counts}

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


def source_hashes():
    return {path.relative_to(base.REPO_ROOT).as_posix(): base.sha256_file(path) for path in
            (_SCRIPT, _SCRIPT.with_name('lumenx_generation_browser_smoke.py'), base.REPO_ROOT / 'tests/frozen_image_fixtures.py')}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--static-root', required=True, type=Path)
    parser.add_argument('--frontend-freeze-commit', required=True)
    parser.add_argument('--backend-source-commit', required=True)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    return parser.parse_args(argv)


def main():
    args = parse_args()
    host = base.validate_loopback_literal(args.host)
    frontend_commit = base.git_output(['rev-parse', '--verify', args.frontend_freeze_commit + '^{commit}'])
    backend_commit = base.git_output(['rev-parse', '--verify', args.backend_source_commit + '^{commit}'])
    if not frontend_commit or not backend_commit:
        raise SystemExit('Both source references must resolve to fixed commits')
    backend_hashes = base.validate_source_reference(backend_commit)
    root, evidence_path = args.data_root.expanduser().resolve(), args.evidence.expanduser().resolve()
    if evidence_path.is_relative_to(root) or evidence_path.exists():
        raise SystemExit('Fresh evidence must be separate from the fixture root')
    static = prepare_root(root, args.static_root, frontend_commit=frontend_commit, backend_commit=backend_commit)
    base.sanitize_environment(root)
    os.chdir(root)
    base.bootstrap_paths(args.python)
    evidence = open_evidence(evidence_path)
    evidence.update(backend_source_hashes=backend_hashes, backend_source_commit=backend_commit,
                    frontend_source_commit=frontend_commit, harness_source_hashes=source_hashes())
    app = create_smoke_app(root, static, evidence, restore_transport=False)
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
        url = f'http://{host}:{port}/static/index.html'
        evidence.update(ready=True, pid=os.getpid(), port=port, url=url, checks={'health': health, 'static': static_check})
        print(json.dumps({'url': url, 'pid': os.getpid(), 'evidence': str(evidence.path)}), flush=True)
        while thread.is_alive() and not server.should_exit:
            time.sleep(0.2)
    except BaseException as exc:
        evidence.update(failure=type(exc).__name__)
        raise
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        evidence.update(stopped=not thread.is_alive(), stopped_at=time.time())


if __name__ == '__main__':
    main()
