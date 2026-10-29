#!/usr/bin/env python3
"""Fresh-only Playground Wan2.7 browser fixture: real app/Core, fake HTTP only.

Use disposable data and separate evidence directories. No restart recovery or
HTTP fault controls are exposed. The CLI keeps its network guard until exit.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

sys.dont_write_bytecode = True
_SCRIPT = Path(__file__).resolve()
_spec = importlib.util.spec_from_file_location('playground_image_studio_transport', _SCRIPT.with_name('lumenx_studio_image_browser_smoke.py'))
assert _spec and _spec.loader
studio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(studio)
base = studio.base
KIND = 'lumenx.playground_image.offline_browser_smoke.v1'
MARKER = 'playground-image-smoke.json'
FIXTURE_IDS = ('legacy-wan27-unsupported', 'legacy-wan27-compatible')


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
    return base.Evidence(path, {'kind': KIND, 'transport_fixture': 'studio-image-http-v1',
        'fake_provider': {'counts': {'submit': 0, 'query': 0, 'fetch': 0}, 'events': []},
        'blocked_sockets': [], 'api_audit': [], 'controls': [], 'sessions': []})


def row_hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def seed_fixture(storage, root):
    from src.apps.playground.models import PlaygroundGeneration
    from tests.frozen_image_fixtures import rgb_png_bytes
    if storage.list_history(limit=1) or (root / 'fixture.json').exists():
        raise ValueError('Legacy fixture requires fresh empty history')
    fixtures = {}
    for index, identity in enumerate(FIXTURE_IDS):
        media_path = f'playground/images/{identity}.png'
        image = root / 'output' / media_path
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(rgb_png_bytes(identity, width=320, height=240))
        generation = PlaygroundGeneration.model_validate({'id': identity, 'mode': 't2i',
            'model_id': 'wan2.7-image-pro' if index == 0 else 'wan2.7-image',
            'prompt': '旧历史：需修改参数后再生成' if index == 0 else '旧历史：兼容参数可再生成',
            'negative_prompt': 'Synthetic legacy negative prompt' if index == 0 else None,
            'parameters': {'size': '1280*1280', 'seed': -1, 'prompt_extend': False, 'watermark': False} if index == 0 else
                          {'size': '1280*1280', 'seed': 0, 'watermark': False},
            # The real details view offers Retry for failed history, while the
            # retained output remains available for inspection and hash checks.
            'status': 'failed', 'batch_size': 1,
            'error': 'Synthetic legacy failure; retained PNG is an offline Retry fixture.',
            'created_at': f'2026-10-01T00:00:0{index}Z',
            'outputs': [{'id': identity + '-output', 'media_path': media_path, 'media_type': 'image'}]})
        storage.add_generation(generation)
        fixtures[identity] = {'row_sha256': row_hash(generation.model_dump(mode='json')),
                             'media_path': media_path, 'media_sha256': base.sha256_file(image)}
    (root / 'fixture.json').write_text(json.dumps({'kind': KIND, 'fixtures': fixtures}, ensure_ascii=False, indent=2) + '\n')
    return fixtures


def retained_fixtures(storage, root, fixtures):
    rows = {identity: row.model_dump(mode='json') for identity in fixtures
            if (row := storage.get_generation(identity)) is not None}
    return {identity: {**expected, 'unchanged': identity in rows and row_hash(rows[identity]) == expected['row_sha256']
             and base.sha256_file(root / 'output' / expected['media_path']) == expected['media_sha256']}
            for identity, expected in fixtures.items()}


def create_smoke_app(root: Path, static: Path, evidence, *, pause_seconds=120, restore_transport=True):
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app
    root, static = root.expanduser().resolve(), static.expanduser().resolve()
    marker = root / MARKER
    if (not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text()).get('kind') != KIND
            or static != root / 'static' or evidence.path.is_relative_to(root)):
        raise ValueError('Harness marker, frozen static and separate evidence directory are required')
    if (root / 'fixture.json').exists() or (root / 'output').exists():
        raise ValueError('Harness requires a fresh fixture; resume is unsupported')
    evidence.update(fixture_root=str(root), fixture_marker_sha256=base.sha256_file(marker))
    controls = studio.Controls(evidence, pause_seconds)
    app = create_app(data_dir=root / 'output', user_data_dir=root / 'user-config', load_config=False,
                     setup_logs=False, recover_orphan_tasks=True, warmup_demucs=False, access_mode='local')
    app.mount('/static', StaticFiles(directory=str(static), html=True), name='static')
    app.state.smoke_controls = controls  # Internal tests only; no HTTP write controls.
    lifecycle = app.router.lifespan_context
    fixtures = {}

    @asynccontextmanager
    async def lifespan(target):
        with studio.offline_transport(evidence, controls, restore=restore_transport):
            async with lifecycle(target):
                env = target.state.runtime_environment
                env.update({'DASHSCOPE_API_KEY': studio.SECRET},
                           remove_keys=tuple(k for k in env.snapshot() if k != 'DASHSCOPE_API_KEY'), persist=lambda *_: None)
                fixtures.update(seed_fixture(target.state.playground_storage, root))
                evidence.update(fixtures=fixtures)
                studio.event(evidence, 'sessions', pid=os.getpid(), started_at=time.time())
                try:
                    yield
                finally:
                    controls.interrupted = True
                    controls.release.set()
                    evidence.update(retained_fixtures=retained_fixtures(target.state.playground_storage, root, fixtures))
                    studio.event(evidence, 'sessions', pid=os.getpid(), stopped_at=time.time())
    app.router.lifespan_context = lifespan

    @app.get('/__smoke/state')
    def state():
        with evidence.lock:
            counts = dict(evidence.payload['fake_provider']['counts'])
        return {'kind': KIND, 'counts': counts, 'fixtures': retained_fixtures(app.state.playground_storage, root, fixtures)}

    @app.middleware('http')
    async def audit(request, call_next):
        try:
            response = await call_next(request)
        except BaseException:
            studio.event(evidence, 'api_audit', method=request.method, path=request.url.path, status=500, raised=True)
            raise
        if not request.url.path.startswith(('/static', '/files')):
            studio.event(evidence, 'api_audit', method=request.method, path=request.url.path, status=response.status_code)
        return response
    return app


def source_hashes():
    return {path.relative_to(base.REPO_ROOT).as_posix(): base.sha256_file(path) for path in
            (_SCRIPT, _SCRIPT.with_name('lumenx_studio_image_browser_smoke.py'), _SCRIPT.with_name('lumenx_generation_browser_smoke.py'),
             base.REPO_ROOT / 'tests/frozen_image_fixtures.py')}


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
