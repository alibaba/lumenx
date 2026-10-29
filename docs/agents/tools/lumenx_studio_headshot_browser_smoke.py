"""Finite headshot browser fixture, external HTTP only.

Load through the existing pre-import isolation launcher. This module deliberately
has no listener, CLI, source freezer, or replacement Core/request/store methods.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager, contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace


def _helper(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


images = _helper('lumenx_studio_image_browser_smoke')
helpers = _helper('lumenx_studio_media_browser_smoke')
base, event = images.base, images.event
prepare_root, MARKER = images.prepare_root, images.MARKER
owned_path, read_json = helpers._owned_path, helpers._read_json
KIND = 'lumenx.studio-headshot.browser-fixture.v1'
SECRET = 'synthetic-example-headshot-http'
EDIT = 'https://api.mulerouter.ai/vendors/openai/v1/gpt-image-2/edit'
RESULT = 'https://headshot-browser.invalid'
FIXTURE = {'project_id': 'p', 'series_id': 's', 'asset_id': 'c'}
SOURCES = ('uploads/first-b.png', 'uploads/selected-c.png')


def require_guard():
    guard = sys.modules.get('isolation_guard')
    if not getattr(guard, '_INSTALLED', False) or os.environ.get('PYTHON_DOTENV_DISABLED') != '1':
        raise ValueError('Use the existing pre-import isolation launcher with dotenv disabled')


def open_evidence(path, *, resume):
    if resume and read_json(path).get('headshot_kind') != KIND:
        raise ValueError('Resume requires headshot-owned evidence')
    evidence = images.open_evidence(path, resume=resume)
    if not resume:
        evidence.update(headshot_kind=KIND, external_tasks={})
    return evidence


def validate_seed_media(root):
    fixture = read_json(owned_path(root, 'fixture.json'))
    if fixture.get('kind') != KIND or fixture.get('owner') not in {'local', 'series', 'global'}:
        raise ValueError('Invalid headshot fixture identity')
    expected = fixture.get('media_hashes')
    if not isinstance(expected, dict) or not set(SOURCES).issubset(expected):
        raise ValueError('Missing selected-reference PNG bindings')
    for relative, digest in expected.items():
        if base.sha256_file(owned_path(root / 'output', relative)) != digest:
            raise ValueError('Original fixture PNG changed')
    return fixture


def seed_fixture(pipeline, root, owner):
    from src.apps.comic_gen.models import Character, Script, Series
    from tests.frozen_image_fixtures import rgb_png_bytes

    refs = (*SOURCES, 'uploads/legacy-a.png', 'uploads/portrait-old.png', 'uploads/views-old.png')
    media = {}
    for ref in refs:
        path = owned_path(root / 'output', ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rgb_png_bytes(ref))
        media[ref] = base.sha256_file(path)
    body = [
        {'id': 'first-b', 'url': SOURCES[0], 'created_at': 1},
        {'id': 'selected-c', 'url': SOURCES[1], 'created_at': 2, 'is_favorited': True,
         'is_uploaded_source': True, 'upload_type': 'full_body'},
    ]
    portrait = [{'id': 'portrait-old', 'url': refs[3], 'created_at': 1, 'is_favorited': True}]
    views = [{'id': 'views-old', 'url': refs[4], 'created_at': 1}]
    actor = Character.model_validate({
        'id': 'c', 'name': 'Alice', 'description': 'Synthetic portrait fixture, red collar.',
        'status': 'completed', 'image_url': refs[3],
        'full_body_asset': {'selected_id': 'selected-c', 'variants': body},
        'full_body': {'selected_image_id': 'selected-c', 'image_variants': body, 'image_prompt': 'Existing body'},
        'full_body_image_url': refs[2], 'full_body_prompt': 'Existing body', 'full_body_updated_at': 1,
        'three_view_asset': {'selected_id': 'views-old', 'variants': views},
        'three_views': {'selected_image_id': 'views-old', 'image_variants': views, 'image_prompt': 'Existing views'},
        'three_view_image_url': refs[4], 'three_view_prompt': 'Existing views', 'three_view_updated_at': 2,
        'headshot_asset': {'selected_id': 'portrait-old', 'variants': portrait},
        'head_shot': {'selected_image_id': 'portrait-old', 'image_variants': portrait, 'image_prompt': 'Previous portrait'},
        'headshot_image_url': refs[3], 'avatar_url': refs[3], 'headshot_prompt': 'Previous portrait',
        'headshot_updated_at': 1,
    })
    project = Script.model_validate({
        'id': 'p', 'title': 'Headshot browser · ' + owner, 'original_text': 'Synthetic',
        'created_at': 1, 'updated_at': 1, 'workflow_mode': 'i2v_legacy',
    })
    project.model_settings.t2i_model = 'wan2.7-image'
    project.model_settings.i2i_model = 'gpt-image-2'
    project.model_settings.character_aspect_ratio = '9:16'
    if owner == 'local':
        project.characters = [actor]
    elif owner == 'series':
        series = Series.model_validate({
            'id': 's', 'title': 'Shared headshot fixture', 'created_at': 1, 'updated_at': 1,
            'episode_ids': ['p'], 'characters': [actor],
        })
        series.model_settings = project.model_settings.model_copy(deep=True)
        pipeline.series_store['s'], project.series_id = series, 's'
    else:
        pipeline.library_store.characters = [actor]
    pipeline.scripts['p'] = project
    pipeline._commit_storage('projects.json', 'series.json', 'library_assets.json')
    owned_path(root, 'fixture.json').write_text(json.dumps({
        'kind': KIND, 'owner': owner, 'fixture': FIXTURE, 'media_hashes': media,
    }, indent=2) + '\n')


def external_task_id(number):
    return f'00000000-0000-4000-8000-{number:012x}'


class Controls:
    """Only external tasks and one original GET response; never product state."""
    def __init__(self, evidence, owner, hold_seconds):
        if owner not in {'local', 'series', 'global'} or not 0 < hold_seconds <= 60:
            raise ValueError('Require a known owner and a hold of at most 60 seconds')
        self.evidence, self.owner, self.hold_seconds = evidence, owner, hold_seconds
        self.lock = threading.RLock()
        self.tasks = json.loads(json.dumps(evidence.payload.get('external_tasks', {})))
        count = evidence.payload['fake_provider']['counts']['submit']
        if set(self.tasks) != {external_task_id(i + 1) for i in range(count)}:
            raise ValueError('External task evidence is incomplete')
        self.lose_next, self.hold, self.closing = False, None, False

    def state(self):
        with self.lock:
            return {'fixture': FIXTURE, 'owner': self.owner,
                    'counts': dict(self.evidence.payload['fake_provider']['counts']),
                    'lose_next': self.lose_next, 'get_armed': self.hold is not None and not self.hold['taken'],
                    'get_held': self.hold is not None and self.hold['entered']}

    def control(self, value):
        if not isinstance(value, dict) or set(value) != {'action'}:
            raise ValueError('Only exact headshot controls are allowed')
        with self.lock:
            if self.closing:
                raise ValueError('Fixture is closing')
            if value['action'] == 'lose_next_provider_ack' and not self.lose_next:
                self.lose_next = True
            elif value['action'] == 'hold_next_project_get' and self.hold is None:
                self.hold = {'gate': threading.Event(), 'taken': False, 'entered': False}
            elif value['action'] == 'release_project_get' and self.hold is not None:
                self.hold['gate'].set()
            else:
                raise ValueError('Unknown or already armed headshot control')
        event(self.evidence, 'controls', **value)
        return self.state()

    def submit(self, payload, reference):
        with self.lock:
            identity = external_task_id(len(self.tasks) + 1)
            lost = self.lose_next
            self.lose_next = False
            self.tasks[identity] = {'reference': reference, 'lost_ack': lost}
            self.evidence.append_provider_event('submit', {
                'task_id': identity, 'payload': payload, 'reference': reference,
            })
            self.evidence.update(external_tasks=json.loads(json.dumps(self.tasks)))
        return identity, lost

    def take_hold(self):
        with self.lock:
            if self.hold is None or self.hold['taken']:
                return None
            self.hold['taken'] = True
            return self.hold

    def finish_hold(self, hold):
        hold['gate'].set()
        with self.lock:
            if self.hold is hold:
                self.hold = None

    def shutdown(self):
        with self.lock:
            self.closing = True
            if self.hold is not None:
                self.hold['gate'].set()


@contextmanager
def offline_transport(evidence, controls, root, *, restore=True):
    import requests
    from requests.adapters import BaseAdapter
    from src.models import mulerouter
    from src.apps.comic_gen import asset_image_execution
    from tests.frozen_image_fixtures import rgb_png_bytes

    require_guard()
    if getattr(requests, '_studio_headshot_smoke_active', False):
        raise RuntimeError('Headshot transport is already active')
    originals = requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess
    original_clock = asset_image_execution.time

    class Stream(io.BytesIO):
        def release_conn(self):
            self.close()

    class Adapter(BaseAdapter):
        def send(self, request, stream=False, timeout=None, verify: bool | str = True, cert=None, proxies=None, **_kwargs):
            if controls.closing:
                raise requests.Timeout('Owned headshot transport is closing')
            url, method = request.url, request.method
            status = 200
            auth = request.headers.get('Authorization')
            if request.headers.get('Cookie'):
                raise RuntimeError('Cookies are not part of the synthetic image transport')
            if method == 'POST' and url == EDIT and auth == 'Bearer ' + SECRET:
                payload = json.loads(request.body)
                inputs = payload.get('images')
                if (set(payload) != {'prompt', 'size', 'n', 'images'}
                        or payload['size'] != '1024x1024' or type(payload['n']) is not int or payload['n'] != 1
                        or not isinstance(payload['prompt'], str) or not payload['prompt'].strip()
                        or not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], str)
                        or not inputs[0].startswith('data:image/png;base64,') or len(inputs[0]) > 1024 * 1024):
                    raise RuntimeError('Unexpected GPT Edit payload')
                content = base64.b64decode(inputs[0].split(',', 1)[1], validate=True)
                allowed = {owned_path(root / 'output', ref).read_bytes(): ref for ref in SOURCES}
                if content not in allowed:
                    raise RuntimeError('Only the actual selectable fixture PNG bytes are allowed')
                identity, lost = controls.submit(payload, allowed[content])
                if lost:
                    event(evidence, 'controls', action='provider_submit_ack_lost', task_id=identity)
                    raise requests.Timeout('Synthetic external submit acknowledgement lost')
                status = 202
                body = {'task_info': {'id': identity, 'status': 'pending'}}
            elif method == 'GET' and isinstance(url, str) and url.startswith(EDIT + '/') and auth == 'Bearer ' + SECRET:
                identity = url.removeprefix(EDIT + '/')
                if identity not in controls.tasks:
                    raise RuntimeError('Unknown external GPT task')
                evidence.append_provider_event('query', {'task_id': identity})
                body = {'task_info': {'id': identity, 'status': 'completed'},
                        'images': [RESULT + '/' + identity + '.png']}
            elif method == 'GET' and isinstance(url, str) and url.startswith(RESULT + '/') and auth is None:
                identity = url.removeprefix(RESULT + '/').removesuffix('.png')
                if identity not in controls.tasks or url != RESULT + '/' + identity + '.png':
                    raise RuntimeError('Unknown external PNG')
                evidence.append_provider_event('fetch', {'task_id': identity})
                body = rgb_png_bytes(identity)
            else:
                raise RuntimeError('Unexpected HTTP blocked by the headshot fixture')
            encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
            result = requests.Response()
            result.status_code, result.url, result.request = status, url, request
            result.headers.update({'Content-Type': 'image/png' if isinstance(body, bytes) else 'application/json',
                                   'Content-Length': str(len(encoded))})
            result.raw = Stream(encoded)
            return result

        def close(self):
            pass

    def session():
        value = originals[1]()
        value.trust_env = False
        value.mount('https://', Adapter())
        value.mount('http://', Adapter())
        return value

    def reject_cli(*_args, **_kwargs):
        event(evidence, 'controls', action='unexpected_mulerun_cli')
        raise RuntimeError('Headshot browser uses HTTP authority only')

    setattr(requests, '_studio_headshot_smoke_active', True)
    requests.Session = requests.sessions.Session = session
    # Advance module-local polling while response holds keep standard time.
    mulerouter.time = asset_image_execution.time = images.PollingClock()
    mulerouter.subprocess = SimpleNamespace(run=reject_cli, TimeoutExpired=originals[3].TimeoutExpired)
    try:
        yield
    finally:
        controls.shutdown()
        if restore:
            requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess = originals
            asset_image_execution.time = original_clock
            delattr(requests, '_studio_headshot_smoke_active')


class ProjectGetHold:
    def __init__(self, app, controls, evidence):
        self.app, self.controls, self.evidence, self.state = app, controls, evidence, app.state

    async def __call__(self, scope, receive, send):
        hold = (self.controls.take_hold() if scope['type'] == 'http'
                and scope['method'] == 'GET' and scope['path'] == '/projects/p' else None)
        if hold is None:
            return await self.app(scope, receive, send)
        messages, body = [], bytearray()

        async def held_send(message):
            messages.append(message)
            if message['type'] != 'http.response.body':
                return
            body.extend(message.get('body', b''))
            if len(body) > 2 * 1024 * 1024:
                raise RuntimeError('Project GET exceeded the finite transport hold')
            if message.get('more_body', False):
                return
            with self.controls.lock:
                hold['entered'] = True
            digest = hashlib.sha256(body).hexdigest()
            event(self.evidence, 'controls', action='project_get_held',
                  path=scope['path'], response_sha256=digest, response_bytes=len(body))
            released = await asyncio.to_thread(hold['gate'].wait, self.controls.hold_seconds)
            event(self.evidence, 'controls', action='project_get_released',
                  response_sha256=digest, released=released, closing=self.controls.closing)
            for original in messages:
                await send(original)

        try:
            await self.app(scope, receive, held_send)
        finally:
            self.controls.finish_hold(hold)


def create_smoke_app(root, static, evidence, *, owner='series', resume=False, hold_seconds=60, restore_transport=True):
    require_guard()
    from fastapi import Body, HTTPException
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app

    root = Path(root).resolve()
    marker = read_json(owned_path(root, MARKER))
    static = helpers._validate_static(root, marker)
    if (marker.get('kind') != images.KIND or evidence.path.is_relative_to(root)
            or owned_path(root, 'fixture.json').exists() != resume or evidence.payload.get('headshot_kind') != KIND):
        raise ValueError('Require a fresh owned image root or explicit headshot resume')
    marker_hash = base.sha256_file(root / MARKER)
    if resume:
        fixture = validate_seed_media(root)
        if (fixture['owner'] != owner or evidence.payload.get('fixture_root') != str(root)
                or evidence.payload.get('fixture_marker_sha256') != marker_hash
                or evidence.payload.get('fixture_sha256') != base.sha256_file(root / 'fixture.json')):
            raise ValueError('Resume source, owner, fixture or evidence changed')
    else:
        evidence.update(fixture_root=str(root), fixture_marker_sha256=marker_hash)
    controls = Controls(evidence, owner, hold_seconds)
    app = create_app(data_dir=root / 'output', user_data_dir=root / 'user-config', load_config=False,
                     setup_logs=False, recover_orphan_tasks=True, warmup_demucs=False, access_mode='local')
    app.mount('/static', StaticFiles(directory=str(static), html=True), name='static')
    lifecycle = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(target):
        with offline_transport(evidence, controls, root, restore=restore_transport):
            async with lifecycle(target):
                target.state.runtime_environment.update({
                    'MULEROUTER_API_KEY': SECRET, 'MULERUN_SITE': 'sg', 'MULERUN_STUDIO_SITE': 'mulerouter',
                }, remove_keys=('DASHSCOPE_API_KEY', 'MULERUN_TOKEN', 'MULEROUTER_SITE'), persist=lambda *_: None)
                if not resume:
                    seed_fixture(target.state.pipeline, root, owner)
                    evidence.update(fixture_sha256=base.sha256_file(root / 'fixture.json'))
                validate_seed_media(root)
                event(evidence, 'sessions', pid=os.getpid(), resume=resume, owner=owner, counts=controls.state()['counts'])
                try:
                    yield
                finally:
                    controls.shutdown()
                    event(evidence, 'sessions', pid=os.getpid(), stopped=True, counts=controls.state()['counts'])

    app.router.lifespan_context = lifespan

    @app.get('/__smoke/state')
    def state():
        return controls.state()

    @app.post('/__smoke/control')
    def control(value: dict = Body(...)):
        try:
            return controls.control(value)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return ProjectGetHold(app, controls, evidence)
