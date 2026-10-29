#!/usr/bin/env python3
"""Fresh-only Atelier Wan2.7 browser fixture: real app/Core, fake HTTP only.

Use disposable data and separate evidence directories. No restart recovery or
HTTP fault controls are exposed. The CLI keeps its network guard until exit.
"""
from __future__ import annotations

import argparse
import base64
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
_spec = importlib.util.spec_from_file_location('atelier_image_studio_transport', _SCRIPT.with_name('lumenx_studio_image_browser_smoke.py'))
assert _spec and _spec.loader
studio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(studio)
base = studio.base
KIND = 'lumenx.atelier_image.offline_browser_smoke.v1'
MARKER = 'atelier-image-smoke.json'
PROJECT_ID = 'atelier-image-smoke'
IMAGE_NODE = 'wan-image'
PRO_NODE = 'wan-image-pro'
LEGACY_SOURCE = base.REPO_ROOT / 'tests/fixtures/atelier_wan27_legacy_wire.json'


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


def legacy_records(project, fixture):
    payload = project.model_dump(mode='json')
    node = next(n for n in payload['nodes'] if n['id'] == fixture['node_id'])
    return {
        'candidate': next(c for c in node['data']['candidates'] if c['id'] == fixture['candidate_id']),
        'attempt': next(a for a in payload['generation_attempts'] if a['attempt_id'] == fixture['attempt_id']),
        'receipt': next(r for r in payload['generation_request_receipts'] if r['client_request_id'] == fixture['request_id']),
    }


def seed_fixture(root):
    """Restore genuine pre-migration wire; never construct or relabel its proof."""
    from src.apps.atelier.models import AtelierNode, AtelierProject
    captured = json.loads(LEGACY_SOURCE.read_text())
    old = captured['completed']['project']
    member = captured['completed']['members'][0]
    attempt = member['attempt']
    execution = attempt['provider_execution']
    if execution['phase'] != 'completed' or 'intent_contract_version' in execution['intent']:
        raise ValueError('Expected completed untagged legacy fixture')
    relative = attempt['output_ref']
    output_root = root / 'output'
    image = (output_root / relative).resolve()
    if not image.is_relative_to(output_root) or image.suffix != '.png':
        raise ValueError('Fixture media must be an owned PNG')
    content = base64.b64decode(captured['files'][relative], validate=True)
    if (hashlib.sha256(content).hexdigest() != execution['artifact']['sha256']
            or len(content) != execution['artifact']['size_bytes']):
        raise ValueError('Legacy fixture bytes do not match its original proof')
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(content)
    nodes = [AtelierNode.model_validate({'id': node_id, 'project_id': PROJECT_ID, 'type': 'image', 'title': title,
        'prompt': 'Synthetic glass moon: ' + title, 'x': index * 420, 'y': 0,
        'data': {'generation_draft': {'schema_version': 3, 'media_kind': 'image', 'model_id': model,
              'mode': 't2i', 'params': {}, 'media': [], 'batch_size': 2}}})
        for index, (node_id, title, model) in enumerate([
            (IMAGE_NODE, 'Wan Image batch 2', 'wan/wan2.7-image#image'),
            (PRO_NODE, 'Wan Pro batch 2', 'wan/wan2.7-image-pro#image')])]
    fresh = AtelierProject.model_validate({'id': PROJECT_ID, 'title': 'Wan2.7 strict image smoke',
                                           'created_at': 1, 'updated_at': 1, 'nodes': nodes})
    # Setup only, before a live store owns the directory. Preserve legacy revision
    # and receipt identity; create_project would reset its revision to zero.
    (output_root / 'atelier_projects.json').write_text(json.dumps(
        {old['id']: old, PROJECT_ID: fresh.model_dump(mode='json')}, ensure_ascii=False, indent=2) + '\n')
    fixture = {'project_id': old['id'], 'node_id': attempt['node_id'], 'candidate_id': member['candidate']['id'],
               'attempt_id': attempt['attempt_id'], 'request_id': captured['request']['client_request_id'],
               'media_path': relative, 'media_sha256': base.sha256_file(image),
               'source_sha256': base.sha256_file(LEGACY_SOURCE)}
    fixture['records_sha256'] = row_hash(legacy_records(AtelierProject.model_validate(old), fixture))
    (root / 'fixture.json').write_text(json.dumps({'kind': KIND, 'legacy': fixture}, indent=2) + '\n')
    return fixture


def retained_legacy(storage, root, fixture):
    try:
        project = storage.get_project(fixture['project_id'])
        unchanged = (row_hash(legacy_records(project, fixture)) == fixture['records_sha256']
                     and base.sha256_file(root / 'output' / fixture['media_path']) == fixture['media_sha256'])
    except (KeyError, StopIteration, FileNotFoundError):
        unchanged = False
    return {**fixture, 'unchanged': unchanged}


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
    fixture = seed_fixture(root)
    evidence.update(fixture=fixture)
    controls = studio.Controls(evidence, pause_seconds)
    app = create_app(data_dir=root / 'output', user_data_dir=root / 'user-config', load_config=False,
                     setup_logs=False, recover_orphan_tasks=True, warmup_demucs=False, access_mode='local')
    app.mount('/static', StaticFiles(directory=str(static), html=True), name='static')
    app.state.smoke_controls = controls  # Internal tests only; no HTTP write controls.
    lifecycle = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(target):
        with studio.offline_transport(evidence, controls, restore=restore_transport):
            async with lifecycle(target):
                env = target.state.runtime_environment
                env.update({'DASHSCOPE_API_KEY': studio.SECRET},
                           remove_keys=tuple(k for k in env.snapshot() if k != 'DASHSCOPE_API_KEY'), persist=lambda *_: None)
                studio.event(evidence, 'sessions', pid=os.getpid(), started_at=time.time())
                try:
                    yield
                finally:
                    controls.interrupted = True
                    controls.release.set()
                    evidence.update(retained_legacy=retained_legacy(target.state.atelier_storage, root, fixture))
                    studio.event(evidence, 'sessions', pid=os.getpid(), stopped_at=time.time())
    app.router.lifespan_context = lifespan

    @app.get('/__smoke/state')
    def state():
        with evidence.lock:
            counts = dict(evidence.payload['fake_provider']['counts'])
        return {'kind': KIND, 'counts': counts, 'project_id': PROJECT_ID, 'image_node_id': IMAGE_NODE,
                'pro_node_id': PRO_NODE, 'legacy': retained_legacy(app.state.atelier_storage, root, fixture)}

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
             base.REPO_ROOT / 'tests/frozen_image_fixtures.py', LEGACY_SOURCE)}


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
