"""Offline Studio motion fixtures with finite transport and artifact faults.

Only external HTTP responses, an ASGI response fault and owned output files are
controlled. Durable receipts/artifacts come from the actual application.
Importing this tool does not launch a browser or start a server.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import threading
import time

_SCRIPT = Path(__file__).resolve()
_spec = importlib.util.spec_from_file_location(
    'studio_motion_helpers', _SCRIPT.with_name('lumenx_studio_media_browser_smoke.py'))
assert _spec and _spec.loader
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
base, event = helpers.base, helpers.event
owned_path, read_json = helpers._owned_path, helpers._read_json
KIND = 'lumenx.studio_motion.offline_browser_smoke.v1'
MARKER = 'studio-motion-smoke.json'
SECRET = 'synthetic-example-studio-motion'
ORIGIN = 'https://dashscope.aliyuncs.com'
SUBMIT = ORIGIN + '/api/v1/services/aigc/video-generation/video-synthesis'
RESULT = 'https://studio-motion-smoke.invalid'
STATUSES = {'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED'}
HOLDS = {'none', 'submit', 'query', 'fetch'}
FIXTURES = {
    'local': {'project_id': 'motion-local', 'asset_id': 'hero', 'owner_kind': 'script', 'owner_id': 'motion-local'},
    'series': {'project_id': 'motion-episode', 'asset_id': 'hero', 'owner_kind': 'series', 'owner_id': 'motion-series'},
    'global': {'project_id': 'motion-global', 'asset_id': 'hero', 'owner_kind': 'global', 'owner_id': 'library'},
}
PROJECTS = {row['project_id'] for row in FIXTURES.values()}


def reject_symlink_chain(path):
    path = Path(path).expanduser().absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Fixture paths must not contain symlinks')


def prepare_root(root, static, *, frontend_commit, backend_commit, resume,
                 media_sources=None, media_manifest=None):
    """Use prepared/probed clips; never invoke a media executable here."""
    root = Path(root)
    reject_symlink_chain(root)
    root = root.expanduser().resolve()
    if resume:
        saved = read_json(owned_path(root, MARKER))
        if (saved.get('kind'), saved.get('frontend_commit'), saved.get('backend_commit')) != (KIND, frontend_commit, backend_commit):
            raise ValueError('Resume source identity changed')
        read_json(owned_path(root, 'fixture.json'))
        validate_seed_media(root, saved)
        return helpers._validate_static(root, saved)
    if not isinstance(media_sources, dict) or set(media_sources) != {'old', 'new'}:
        raise ValueError('Exactly two prepared clips are required')
    if not isinstance(media_manifest, dict) or set(media_manifest) != {'old', 'new'}:
        raise ValueError('Exact hash-bound probe results are required')
    copies = {}
    for role, source in media_sources.items():
        source, row = Path(source), media_manifest[role]
        reject_symlink_chain(source)
        if source.is_symlink() or not source.is_file() or not isinstance(row, dict):
            raise ValueError('Prepared clip is missing or a symlink')
        data = source.read_bytes()
        if (len(data) > 2 * 1024 * 1024 or len(data) < 1024 or len(data) != row.get('size_bytes')
                or hashlib.sha256(data).hexdigest() != row.get('sha256')
                or row.get('codec_name') != 'h264' or row.get('pix_fmt') != 'yuv420p'
                or (row.get('width'), row.get('height')) != (320, 180)
                or type(row.get('duration')) not in {int, float} or not 2.5 <= row['duration'] <= 3.5
                or row.get('has_audio') is not False or not isinstance(row.get('ffprobe'), dict)):
            raise ValueError('Prepared clip does not match the reviewed media contract')
        copies[role] = data
    if copies['old'] == copies['new']:
        raise ValueError('Old selection and provider output must differ')
    base.ensure_owned_empty_data_root(root)
    mounted, manifest = base.freeze_static_snapshot(base.validate_static_source(Path(static)), root, frontend_commit)
    (root / 'seed-media').mkdir()
    for role, data in copies.items():
        with (root / 'seed-media' / f'{role}.mp4').open('xb') as stream:
            stream.write(data)
    (root / MARKER).write_text(json.dumps({
        'kind': KIND, 'frontend_commit': frontend_commit, 'backend_commit': backend_commit,
        'static': manifest, 'media': media_manifest,
    }, indent=2) + '\n')
    return mounted


def validate_seed_media(root, marker):
    rows = marker.get('media')
    if not isinstance(rows, dict) or set(rows) != {'old', 'new'}:
        raise ValueError('Seed media manifest is invalid')
    for role, row in rows.items():
        path = owned_path(root, f'seed-media/{role}.mp4')
        if not path.is_file() or base.sha256_file(path) != row['sha256'] or path.stat().st_size != row['size_bytes']:
            raise ValueError('Immutable seed clip changed')


def open_evidence(path, *, resume):
    path = Path(path)
    reject_symlink_chain(path)
    path = path.expanduser().resolve()
    if resume:
        payload = read_json(path)
        provider = payload.get('fake_provider', {})
        counts, events, tasks = provider.get('counts'), provider.get('events'), payload.get('external_tasks')
        if (payload.get('kind') != KIND or not isinstance(counts, dict) or set(counts) != {'submit', 'query', 'fetch'}
                or any(type(n) is not int or n < 0 for n in counts.values()) or not isinstance(events, list)
                or any(not isinstance(row, dict) or row.get('kind') not in counts for row in events)
                or any(sum(row['kind'] == key for row in events) != count for key, count in counts.items())
                or not isinstance(tasks, dict) or set(tasks) != {row['task_id'] for row in events if row['kind'] == 'submit'}
                or len(tasks) != counts['submit']
                or any(not isinstance(row, dict) or row.get('status') not in STATUSES
                       or row.get('hold') not in HOLDS or type(row.get('lose_submit_ack')) is not bool
                       for row in tasks.values())
                or set(tasks) != {f'studio-motion-{i + 1}' for i in range(counts['submit'])}):
            raise ValueError('Resume simulator evidence is inconsistent')
    else:
        if path.exists():
            raise ValueError('Refuse to overwrite evidence')
        payload = {'kind': KIND, 'fake_provider': {'counts': {'submit': 0, 'query': 0, 'fetch': 0}, 'events': []},
                   'external_tasks': {}, 'blocked_sockets': [], 'api_audit': [], 'controls': [], 'sessions': []}
    return base.Evidence(path, payload)


@contextmanager
def isolated_environment(root):
    """Enclose app construction and its entire lifecycle; no HOME remapping."""
    original = dict(os.environ)
    try:
        base.sanitize_environment(root)
        os.environ['DASHSCOPE_API_KEY'] = SECRET
        os.environ['NETRC'] = str(owned_path(root, 'disabled-netrc'))
        if Path(os.environ['NETRC']).exists():
            raise ValueError('The disabled netrc path must remain absent')
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


class Controls:
    """External simulator state; never owns a product task or receipt."""
    def __init__(self, evidence, *, hold_seconds=60):
        if not 0 < hold_seconds <= 60:
            raise ValueError('A hold must be bounded to 60 seconds')
        self.evidence, self.hold_seconds = evidence, hold_seconds
        self.lock = threading.RLock()
        self.tasks = json.loads(json.dumps(evidence.payload['external_tasks']))
        self.gates, self.waiting = {}, set()
        self.next_plan, self.admission_loss, self.blocked_gets = None, None, set()
        self.closing = False

    def state(self):
        with self.lock, self.evidence.lock:
            return {'fixtures': FIXTURES, 'counts': dict(self.evidence.payload['fake_provider']['counts']),
                    'external_tasks': json.loads(json.dumps(self.tasks)), 'waiting': sorted(self.waiting),
                    'next_plan': dict(self.next_plan) if self.next_plan else None, 'admission_loss': self.admission_loss,
                    'blocked_gets': sorted(self.blocked_gets)}

    def control(self, value):
        if not isinstance(value, dict):
            raise ValueError('Control requires an object')
        action = value.get('action')
        with self.lock:
            if action == 'arm_next' and set(value) == {'action', 'status', 'hold', 'lose_submit_ack'}:
                if self.next_plan is not None or value['status'] not in STATUSES or value['hold'] not in HOLDS or type(value['lose_submit_ack']) is not bool:
                    raise ValueError('Invalid or already armed external submission')
                self.next_plan = {key: value[key] for key in ('status', 'hold', 'lose_submit_ack')}
            elif action == 'release' and set(value) == {'action', 'task_id', 'status'}:
                identity = value['task_id']
                if identity not in self.tasks or value['status'] not in STATUSES:
                    raise ValueError('Only a known external task can be released')
                self.tasks[identity]['status'] = value['status']
                self.evidence.update(external_tasks=json.loads(json.dumps(self.tasks)))
                if identity in self.gates:
                    self.gates[identity].set()
            elif action == 'lose_admission_ack' and set(value) == {'action', 'project_id'}:
                if value['project_id'] not in PROJECTS or self.admission_loss is not None:
                    raise ValueError('Invalid or already armed admission fault')
                self.admission_loss = value['project_id']
            elif action == 'block_get' and set(value) == {'action', 'path', 'enabled'}:
                if type(value['enabled']) is not bool or not fixture_get_path(value['path']):
                    raise ValueError('GET fault must name an exact fixture project/original path')
                if value['enabled']:
                    self.blocked_gets.add(value['path'])
                else:
                    self.blocked_gets.discard(value['path'])
            else:
                raise ValueError('Unknown or malformed control')
        event(self.evidence, 'controls', **value)
        return self.state()

    def submit(self, payload):
        with self.lock:
            number = len(self.tasks) + 1
            identity = f'studio-motion-{number}'
            plan = self.next_plan or {'status': 'SUCCEEDED', 'hold': 'none', 'lose_submit_ack': False}
            self.next_plan = None
            self.tasks[identity] = dict(plan)
            self.gates[identity] = threading.Event()
            self.evidence.append_provider_event('submit', {'task_id': identity, 'payload': payload})
            self.evidence.update(external_tasks=json.loads(json.dumps(self.tasks)))
        return identity

    def wait(self, identity, phase):
        import requests
        with self.lock:
            gate = self.gates.get(identity)
            held = gate is not None and self.tasks[identity]['hold'] == phase and not gate.is_set()
            if held:
                self.waiting.add(identity + ':' + phase)
        if held and gate is not None:
            event(self.evidence, 'controls', action='external_hold_entered', task_id=identity, phase=phase)
            released = gate.wait(self.hold_seconds)
            with self.lock:
                self.waiting.discard(identity + ':' + phase)
            if not released:
                event(self.evidence, 'controls', action='external_hold_timed_out', task_id=identity, phase=phase)
                raise requests.Timeout('Synthetic bounded provider hold expired')
        if self.closing:
            raise requests.Timeout('Synthetic provider interrupted by owned shutdown')

    def shutdown(self):
        with self.lock:
            self.closing = True
            for gate in self.gates.values():
                gate.set()


def fixture_get_path(path):
    if not isinstance(path, str):
        return False
    parts = path.split('/')
    return (len(parts) in {3, 5} and parts[:1] == [''] and parts[1] == 'projects' and parts[2] in PROJECTS
            and (len(parts) == 3 or (parts[3] == 'motion_ref_requests' and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', parts[4]) is not None)))


@contextmanager
def offline_transport(evidence, controls, root, *, restore=True):
    import requests
    from requests.adapters import BaseAdapter
    if getattr(requests, '_studio_motion_smoke_active', False):
        raise RuntimeError('Motion transport is already active')
    original_alias, original_session = requests.Session, requests.sessions.Session
    originals = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    result_path = owned_path(root, 'seed-media/new.mp4')
    expected_result = read_json(root / MARKER)['media']['new']

    class Stream(io.BytesIO):
        def release_conn(self):
            self.close()

    class Adapter(BaseAdapter):
        def send(self, request, stream=False, timeout=None, verify: bool | str = True, cert=None, proxies=None, **_kwargs):
            url, method = request.url, request.method
            authorization = request.headers.get('Authorization')
            if method == 'POST' and url == SUBMIT:
                if authorization != 'Bearer ' + SECRET or request.headers.get('Cookie'):
                    raise RuntimeError('Only synthetic credential authority is allowed')
                payload = json.loads(request.body)
                if (payload.get('model') != 'wan2.7-i2v' or payload.get('parameters', {}).get('duration') != 5
                        or payload.get('parameters', {}).get('resolution') != '720P'
                        or 'audio_url' in payload.get('input', {})):
                    raise RuntimeError('Unexpected motion transport payload')
                media = payload.get('input', {}).get('media')
                if not isinstance(media, list) or len(media) != 1 or media[0].get('type') != 'first_frame' or not media[0].get('url', '').startswith('data:image/png;base64,'):
                    raise RuntimeError('Only the selected managed PNG transport is allowed')
                identity = controls.submit(payload)
                controls.wait(identity, 'submit')
                if controls.tasks[identity]['lose_submit_ack']:
                    event(evidence, 'controls', action='provider_submit_ack_lost', task_id=identity)
                    raise requests.ConnectionError('Synthetic provider accepted but acknowledgement was lost')
                body = {'output': {'task_id': identity}}
            elif method == 'GET' and isinstance(url, str) and url.startswith(ORIGIN + '/api/v1/tasks/'):
                identity = url.rsplit('/', 1)[-1]
                if url != ORIGIN + '/api/v1/tasks/' + identity or identity not in controls.tasks or authorization != 'Bearer ' + SECRET:
                    raise RuntimeError('Unexpected external task query')
                evidence.append_provider_event('query', {'task_id': identity})
                controls.wait(identity, 'query')
                with controls.lock:
                    status = controls.tasks[identity]['status']
                body = {'output': {'task_id': identity, 'task_status': status}}
                if status == 'SUCCEEDED':
                    body['output']['video_url'] = RESULT + '/' + identity + '.mp4'
                event(evidence, 'controls', action='external_query_returned', task_id=identity, status=status)
            elif method == 'GET' and isinstance(url, str) and url.startswith(RESULT + '/'):
                identity = url.rsplit('/', 1)[-1].removesuffix('.mp4')
                if (url != RESULT + '/' + identity + '.mp4' or identity not in controls.tasks
                        or controls.tasks[identity]['status'] != 'SUCCEEDED'
                        or authorization is not None or request.headers.get('Cookie')):
                    raise RuntimeError('Unexpected anonymous result download')
                evidence.append_provider_event('fetch', {'task_id': identity})
                controls.wait(identity, 'fetch')
                body = result_path.read_bytes()
                if hashlib.sha256(body).hexdigest() != expected_result['sha256'] or len(body) != expected_result['size_bytes']:
                    raise RuntimeError('Synthetic result bytes changed')
            else:
                event(evidence, 'controls', action='unexpected_external_http', method=method, url=url)
                raise RuntimeError('Motion harness blocked an unexpected external HTTP request')
            encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
            response = requests.Response()
            response.status_code, response.url, response.request = 200, url, request
            response.headers.update({'Content-Type': 'video/mp4' if isinstance(body, bytes) else 'application/json',
                                     'Content-Length': str(len(encoded))})
            response.raw = Stream(encoded)
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
        raise RuntimeError('Motion harness blocks outbound sockets and DNS')

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

    setattr(requests, '_studio_motion_smoke_active', True)
    requests.Session = requests.sessions.Session = session
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, dns
    try:
        yield
    finally:
        controls.shutdown()
        if restore:
            requests.Session, requests.sessions.Session = original_alias, original_session
            socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = originals
            delattr(requests, '_studio_motion_smoke_active')


def seed_fixture(pipeline, root):
    from src.apps.comic_gen.models import Character, Script, Series
    from tests.frozen_image_fixtures import rgb_png_bytes
    uploads = pipeline.data_dir / 'uploads'
    hashes = {}
    for label in ('choice', 'alternate'):
        path = uploads / f'motion-{label}.png'
        with path.open('xb') as stream:
            stream.write(rgb_png_bytes(label, width=320, height=240))
        hashes[path.relative_to(pipeline.data_dir).as_posix()] = base.sha256_file(path)
    retained = uploads / 'motion-selected-old.mp4'
    with retained.open('xb') as stream:
        stream.write((root / 'seed-media/old.mp4').read_bytes())
    hashes['uploads/motion-selected-old.mp4'] = base.sha256_file(retained)
    for route, row in FIXTURES.items():
        project = Script.model_validate({'id': row['project_id'], 'title': 'Motion fixture · ' + route,
            'original_text': 'Synthetic offline motion fixture', 'created_at': 1, 'updated_at': 1, 'workflow_mode': 'i2v_legacy'})
        target = Character.model_validate({'id': row['asset_id'], 'name': 'Hero', 'description': 'Synthetic red coat character',
            'status': 'completed', 'full_body_image_url': 'uploads/motion-alternate.png', 'full_body': {
                'selected_image_id': 'choice', 'image_variants': [
                    {'id': label, 'url': f'uploads/motion-{label}.png', 'created_at': i + 1, 'is_favorited': i == 0}
                    for i, label in enumerate(('choice', 'alternate'))],
                'selected_video_id': 'old-selected', 'video_variants': [{'id': 'old-selected',
                    'url': 'uploads/motion-selected-old.mp4', 'created_at': 1, 'source_image_id': 'choice'}],
                'image_prompt': 'Preserve selected static source', 'video_prompt': 'One slow turn',
                'image_updated_at': 1, 'video_updated_at': 1}})
        if route == 'local':
            project.characters = [target]
        elif route == 'series':
            series = Series.model_validate({'id': row['owner_id'], 'title': 'Motion series fixture', 'created_at': 1,
                'updated_at': 1, 'episode_ids': [project.id], 'characters': [target]})
            pipeline.series_store[series.id] = series
            project.series_id = series.id
        else:
            pipeline.library_store.characters = [target]
        pipeline.scripts[project.id] = project
    pipeline._commit_storage('projects.json', 'series.json', 'library_assets.json')
    (root / 'fixture.json').write_text(json.dumps({'kind': KIND, 'fixtures': FIXTURES, 'media_hashes': hashes}, indent=2) + '\n')


def damage_artifact(pipeline, root, evidence, value):
    required = {'action', 'project_id', 'client_request_id', 'task_id', 'attempt_id', 'fingerprint', 'mode'}
    if set(value) != required or value['action'] != 'damage_artifact' or value['project_id'] not in PROJECTS or value['mode'] not in {'missing', 'invalid'}:
        raise ValueError('Invalid artifact fault')
    original = pipeline.motion_reference_service.read_request(value['project_id'], value['client_request_id'])
    task = original.get('task') if original else None
    record = task.get('provider_execution') if task else None
    fixture = next(row for row in FIXTURES.values() if row['project_id'] == value['project_id'])
    if (not original or not task or not record
            or (original['task_id'], record['attempt_id'], record['fingerprint']) != (value['task_id'], value['attempt_id'], value['fingerprint'])
            or task['asset_id'] != fixture['asset_id'] or record['phase'] != 'completed'
            or not record.get('artifact') or record.get('prepared_artifact') != record['artifact']):
        raise ValueError('Artifact fault requires the exact genuine completed attempt/proof')
    proof = record['artifact']
    path = owned_path(root / 'output', proof['relative_path'])
    if not path.is_file() or path.stat().st_size != proof['size_bytes'] or base.sha256_file(path) != proof['sha256']:
        raise ValueError('Completed artifact no longer matches its saved proof')
    stash_dir = owned_path(root, 'retained-artifacts')
    stash_dir.mkdir(exist_ok=True)
    stash = owned_path(stash_dir, hashlib.sha256(proof['relative_path'].encode()).hexdigest() + '.mp4')
    if stash.exists():
        raise ValueError('This artifact has already been faulted')
    os.link(path, stash)
    path.unlink()
    if value['mode'] == 'invalid':
        with path.open('xb') as stream:
            stream.write(b'synthetic corruption: original retained outside the output root')
    event(evidence, 'controls', **value, original_proof=proof, retained_path=stash.relative_to(root).as_posix())
    return {'fault': value['mode'], 'proof': proof, 'product_record_modified': False}


class AdmissionAckLost(Exception):
    """Injected ASGI response-transport failure; never a product exception."""


class FaultBoundary:
    def __init__(self, app, controls, evidence, *, admission_transport=False):
        self.app, self.controls, self.evidence = app, controls, evidence
        self.state = getattr(app, 'state', None)
        self.admission_transport = admission_transport

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        method, path = scope['method'], scope['path']
        with self.controls.lock:
            blocked = not self.admission_transport and method == 'GET' and path in self.controls.blocked_gets
            project = self.controls.admission_loss
            lose = bool(self.admission_transport and project and method == 'POST'
                        and path == f'/projects/{project}/motion_ref_requests')
            if lose:
                self.controls.admission_loss = None
        if self.admission_transport and not lose:
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse
        if blocked:
            event(self.evidence, 'api_audit', method=method, path=path, status=503, injected='exact_get_unavailable')
            return await JSONResponse({'detail': 'Synthetic original/project GET unavailable'}, status_code=503)(scope, receive, send)
        start, response_bytes, request_bytes = None, bytearray(), bytearray()
        response_hash, response_size, status = hashlib.sha256(), 0, None

        async def observed_receive():
            message = await receive()
            if message['type'] == 'http.request':
                request_bytes.extend(message.get('body', b''))
                if len(request_bytes) > 64 * 1024:
                    raise ValueError('Finite browser command exceeded the audit bound')
            return message

        async def observed_send(message):
            nonlocal start, status, response_size
            if message['type'] == 'http.response.start':
                status = message['status']
                if lose:
                    start = message
                    return
            if message['type'] == 'http.response.body':
                body = message.get('body', b'')
                response_hash.update(body)
                response_size += len(body)
                if lose:
                    response_bytes.extend(body)
                    if len(response_bytes) > 2 * 1024 * 1024:
                        raise ValueError('Finite admission response exceeded the fault bound')
                    if message.get('more_body', False):
                        return
                    original = json.loads(response_bytes)
                    record = (original.get('task') or {}).get('provider_execution') or {}
                    if status == 200 and record.get('phase') == 'accepted':
                        event(self.evidence, 'controls', action='admission_ack_lost', path=path,
                              original_response=original, original_response_sha256=response_hash.hexdigest())
                        raise AdmissionAckLost('Synthetic final admission response-body transport failure')
                    event(self.evidence, 'controls', action='admission_fault_not_injected', path=path, status=status)
                    await send(start)
                    return await send({'type': 'http.response.body', 'body': bytes(response_bytes)})
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        except AdmissionAckLost:
            await JSONResponse({'detail': 'Synthetic admission acknowledgement unavailable'}, status_code=503)(scope, receive, send)
            status = 503
        finally:
            if not self.admission_transport and not path.startswith(('/static', '/files', '/__smoke')):
                event(self.evidence, 'api_audit', method=method, path=path, status=status,
                      request_body=request_bytes.decode('utf-8', errors='replace'), response_size=response_size,
                      response_sha256=response_hash.hexdigest(), injected='admission_ack_loss' if lose and status == 503 else None)


def create_smoke_app(root, static, evidence, *, resume=False, restore_transport=True, hold_seconds=60):
    if os.environ.get('DASHSCOPE_API_KEY') != SECRET or os.environ.get('PYTHON_DOTENV_DISABLED') != '1':
        raise ValueError('Construct and run the app inside isolated_environment')
    from fastapi import Body, HTTPException
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app, router as product_router
    from src.apps.comic_gen.provider_recovery_dispatcher import StudioProviderRecoveryDispatcher
    reject_symlink_chain(root)
    root = Path(root).resolve()
    marker = read_json(owned_path(root, MARKER))
    if marker.get('kind') != KIND or evidence.path.is_relative_to(root) or (root / 'fixture.json').exists() != resume:
        raise ValueError('Owned marker, separate evidence and explicit resume are required')
    static = helpers._validate_static(root, marker)
    validate_seed_media(root, marker)
    marker_hash = base.sha256_file(root / MARKER)
    if resume:
        if evidence.payload.get('fixture_root') != str(root) or evidence.payload.get('fixture_marker_sha256') != marker_hash or evidence.payload.get('fixture_sha256') != base.sha256_file(owned_path(root, 'fixture.json')):
            raise ValueError('Resume fixture identity changed')
        fixture = read_json(root / 'fixture.json')
        if fixture.get('kind') != KIND or fixture.get('fixtures') != FIXTURES:
            raise ValueError('Resume fixture shape changed')
        for relative, digest in fixture['media_hashes'].items():
            if base.sha256_file(owned_path(root / 'output', relative)) != digest:
                raise ValueError('Original selected image/video changed')
    else:
        evidence.update(fixture_root=str(root), fixture_marker_sha256=marker_hash)
    controls = Controls(evidence, hold_seconds=hold_seconds)
    app = create_app(data_dir=root / 'output', user_data_dir=root / 'user-config', load_config=False,
                     setup_logs=False, recover_orphan_tasks=False, warmup_demucs=False, access_mode='local')
    app.mount('/static', StaticFiles(directory=str(static), html=True), name='static')
    lifecycle = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(target):
        with offline_transport(evidence, controls, root, restore=restore_transport):
            async with lifecycle(target):
                if not resume:
                    seed_fixture(target.state.pipeline, root)
                    evidence.update(fixture_sha256=base.sha256_file(root / 'fixture.json'))
                # Lifecycle registration only: this is the production worker,
                # with its actual candidate selection and storage operations.
                dispatcher = StudioProviderRecoveryDispatcher(target.state.pipeline, interval_seconds=2, transient_backoff_seconds=60)
                target.state.provider_recovery_dispatcher = dispatcher
                dispatcher.start()
                event(evidence, 'sessions', pid=os.getpid(), resume=resume, started_at=time.time(),
                      dispatcher_class=type(dispatcher).__name__, counts=controls.state()['counts'])
                try:
                    yield
                finally:
                    dispatcher.request_stop()
                    controls.shutdown()
                    event(evidence, 'sessions', pid=os.getpid(), stopped_at=time.time(), counts=controls.state()['counts'])
    app.router.lifespan_context = lifespan

    # Keep the fault inside buffering BaseHTTPMiddleware. Recent FastAPI uses
    # an app-owned include branch; older versions clone flat APIRoutes.
    # Wrap that branch's handle so lazy context dispatch, the endpoint and
    # BackgroundTasks stay intact. Never mutate the shared product router.
    admission_path = '/projects/{script_id}/motion_ref_requests'
    shared_admissions = [route for route in product_router.routes
                         if getattr(route, 'path', None) == admission_path
                         and 'POST' in (getattr(route, 'methods', None) or set())]
    admission_routes = [route for route in app.router.routes
                        if getattr(route, 'original_router', None) is product_router
                        or (getattr(route, 'path', None) == admission_path
                            and 'POST' in (getattr(route, 'methods', None) or set()))]
    if (len(shared_admissions) != 1 or len(admission_routes) != 1
            or any(admission_routes[0] is route for route in product_router.routes)):
        raise ValueError('Expected exactly one app-owned strict motion admission route')
    admission_routes[0].handle = FaultBoundary(admission_routes[0].handle, controls, evidence, admission_transport=True)

    @app.get('/__smoke/state')
    def state():
        return controls.state()

    @app.post('/__smoke/control')
    def control(value: dict = Body(...)):
        try:
            if value.get('action') == 'damage_artifact':
                return damage_artifact(app.state.pipeline, root, evidence, value)
            return controls.control(value)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return FaultBoundary(app, controls, evidence)
