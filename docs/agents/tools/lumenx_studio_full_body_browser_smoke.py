"""Finite full-body browser fixture using real app/Core and external HTTP only.

Load through the existing pre-import isolation launcher. Reuse the proven exact
GET hold and controls; no listener, freezer or replacement product method here.
"""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager, contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace


_spec = importlib.util.spec_from_file_location(
    'full_body_headshot_helpers', Path(__file__).with_name('lumenx_studio_headshot_browser_smoke.py'))
assert _spec and _spec.loader
headshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(headshot)
images, helpers = headshot.images, headshot.helpers
base, event = headshot.base, headshot.event
prepare_root, MARKER = headshot.prepare_root, headshot.MARKER
owned_path, read_json = headshot.owned_path, headshot.read_json
Controls, ProjectGetHold, require_guard = headshot.Controls, headshot.ProjectGetHold, headshot.require_guard
KIND = 'lumenx.studio-full-body.browser-fixture.v1'
SECRET = 'synthetic-example-full-body-http'
EDIT = headshot.EDIT
RESULT = 'https://full-body-browser.invalid'
FIXTURE = {'project_id': 'p', 'series_id': 's', 'asset_id': 'c'}
SOURCES = ('uploads/view-first.png', 'uploads/view-second.png', 'uploads/face-first.png')
OLD_ID = 'body-old'
OLD_REF = 'uploads/body-old.png'
SIZE = '1024x1536'
REVERSE = (
    'STRICTLY MAINTAIN the SAME character appearance, face, hairstyle, skin tone, '
    'and clothing as the reference image. '
)


def open_evidence(path, *, resume):
    if resume and read_json(path).get('full_body_kind') != KIND:
        raise ValueError('Resume requires full-body-owned evidence')
    evidence = images.open_evidence(path, resume=resume)
    if not resume:
        evidence.update(full_body_kind=KIND, external_tasks={})
    return evidence


def validate_seed_media(root):
    fixture = read_json(owned_path(root, 'fixture.json'))
    if (fixture.get('kind') != KIND or fixture.get('owner') not in {'local', 'series', 'global'}
            or fixture.get('initial_selection') not in (None, OLD_ID)):
        raise ValueError('Invalid full-body fixture identity')
    expected = fixture.get('media_hashes')
    if not isinstance(expected, dict) or not {*SOURCES, OLD_REF}.issubset(expected):
        raise ValueError('Missing uploaded-reference PNG bindings')
    for relative, digest in expected.items():
        if base.sha256_file(owned_path(root / 'output', relative)) != digest:
            raise ValueError('Original fixture PNG changed')
    return fixture


def seed_fixture(pipeline, root, owner, initial_selection):
    from src.apps.comic_gen.models import Character, Script, Series
    from tests.frozen_image_fixtures import rgb_png_bytes

    refs = (*SOURCES, OLD_REF, 'uploads/body-legacy.png', 'uploads/body-modern.png', 'uploads/view-ordinary.png')
    media = {}
    for ref in refs:
        path = owned_path(root / 'output', ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rgb_png_bytes(ref))
        media[ref] = base.sha256_file(path)
    old = {'id': OLD_ID, 'url': OLD_REF, 'created_at': 1,
           'prompt_used': 'Old body prompt without reverse prefix', 'is_favorited': True}
    legacy = [old, {'id': 'body-legacy', 'url': refs[4], 'created_at': 2, 'prompt_used': 'Keep legacy body'}]
    modern = [old, {'id': 'body-modern', 'url': refs[5], 'created_at': 3, 'prompt_used': 'Keep modern body'}]
    views = [
        {'id': 'view-ordinary', 'url': refs[6], 'created_at': 1},
        {'id': 'view-first', 'url': SOURCES[0], 'created_at': 2,
         'is_uploaded_source': True, 'upload_type': 'three_views'},
        {'id': 'view-second', 'url': SOURCES[1], 'created_at': 3,
         'is_uploaded_source': True, 'upload_type': 'three_views', 'is_favorited': True},
    ]
    face = [{'id': 'face-first', 'url': SOURCES[2], 'created_at': 1,
             'is_uploaded_source': True, 'upload_type': 'head_shot'}]
    actor = Character.model_validate({
        'id': 'c', 'name': 'Alice', 'description': 'Synthetic full-body fixture, red coat and leather boots.',
        'status': 'completed', 'image_url': OLD_REF if initial_selection else SOURCES[1],
        'full_body_asset': {'selected_id': initial_selection, 'variants': legacy},
        'full_body': {'selected_image_id': initial_selection, 'image_variants': modern,
                      'image_prompt': 'Previous full-body author draft', 'image_updated_at': 1,
                      'video_prompt': 'Keep existing full-body motion'},
        'full_body_image_url': OLD_REF if initial_selection else None,
        'full_body_prompt': 'Previous full-body author draft', 'full_body_updated_at': 1,
        'three_view_asset': {'selected_id': 'view-second', 'variants': views},
        'three_views': {'selected_image_id': 'view-second', 'image_variants': views, 'image_prompt': 'Keep views'},
        'three_view_image_url': SOURCES[1], 'three_view_prompt': 'Keep views', 'three_view_updated_at': 2,
        'headshot_asset': {'selected_id': 'face-first', 'variants': face},
        'head_shot': {'selected_image_id': 'face-first', 'image_variants': face, 'image_prompt': 'Keep face'},
        'headshot_image_url': SOURCES[2], 'avatar_url': SOURCES[2], 'headshot_prompt': 'Keep face',
        'headshot_updated_at': 1,
    })
    project = Script.model_validate({
        'id': 'p', 'title': 'Full-body browser · ' + owner, 'original_text': 'Synthetic',
        'created_at': 1, 'updated_at': 1, 'workflow_mode': 'i2v_legacy',
    })
    project.model_settings.t2i_model = 'wan2.7-image'
    project.model_settings.i2i_model = 'gpt-image-2'
    project.model_settings.character_aspect_ratio = '9:16'
    if owner == 'local':
        project.characters = [actor]
    elif owner == 'series':
        series = Series.model_validate({
            'id': 's', 'title': 'Shared full-body fixture', 'created_at': 1, 'updated_at': 1,
            'episode_ids': ['p'], 'characters': [actor],
        })
        series.model_settings = project.model_settings.model_copy(deep=True)
        pipeline.series_store['s'], project.series_id = series, 's'
    else:
        pipeline.library_store.characters = [actor]
    pipeline.scripts['p'] = project
    pipeline._commit_storage('projects.json', 'series.json', 'library_assets.json')
    owned_path(root, 'fixture.json').write_text(json.dumps({
        'kind': KIND, 'owner': owner, 'fixture': FIXTURE, 'initial_selection': initial_selection,
        'media_hashes': media,
    }, indent=2) + '\n')


@contextmanager
def offline_transport(evidence, controls, root, *, restore=True):
    import requests
    from requests.adapters import BaseAdapter
    from src.models import mulerouter
    from src.apps.comic_gen import asset_image_execution
    from tests.frozen_image_fixtures import rgb_png_bytes

    require_guard()
    if (getattr(requests, '_studio_full_body_smoke_active', False)
            or getattr(requests, '_studio_headshot_smoke_active', False)):
        raise RuntimeError('An owned image transport is already active')
    originals = requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess
    original_clock = asset_image_execution.time

    class Stream(io.BytesIO):
        def release_conn(self):
            self.close()

    class Adapter(BaseAdapter):
        def send(self, request, stream=False, timeout=None, verify: bool | str = True, cert=None, proxies=None, **_kwargs):
            if controls.closing:
                raise requests.Timeout('Owned full-body transport is closing')
            url, method = request.url, request.method
            status = 200
            auth = request.headers.get('Authorization')
            if request.headers.get('Cookie'):
                raise RuntimeError('Cookies are not part of the synthetic image transport')
            if method == 'POST' and url == EDIT and auth == 'Bearer ' + SECRET:
                payload = json.loads(request.body)
                inputs = payload.get('images')
                if (set(payload) != {'prompt', 'size', 'n', 'images'}
                        or payload['size'] != SIZE or type(payload['n']) is not int or payload['n'] != 1
                        or not isinstance(payload['prompt'], str) or not payload['prompt'].startswith(REVERSE)
                        or payload['prompt'].count(REVERSE.strip()) != 1
                        or not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], str)
                        or not inputs[0].startswith('data:image/png;base64,') or len(inputs[0]) > 1024 * 1024):
                    raise RuntimeError('Unexpected full-body GPT Edit payload')
                content = base64.b64decode(inputs[0].split(',', 1)[1], validate=True)
                allowed = {owned_path(root / 'output', ref).read_bytes(): ref for ref in SOURCES}
                if content not in allowed:
                    raise RuntimeError('Only the actual uploaded fixture PNG bytes are allowed')
                # Reuse opaque external task IDs/counters; the product receipt
                # channel and v2 intent come only from actual Core execution.
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
                body = rgb_png_bytes(identity, width=160, height=240)
            else:
                raise RuntimeError('Unexpected HTTP blocked by the full-body fixture')
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
        raise RuntimeError('Full-body browser uses HTTP authority only')

    setattr(requests, '_studio_full_body_smoke_active', True)
    requests.Session = requests.sessions.Session = session
    mulerouter.time = asset_image_execution.time = images.PollingClock()
    mulerouter.subprocess = SimpleNamespace(run=reject_cli, TimeoutExpired=originals[3].TimeoutExpired)
    try:
        yield
    finally:
        controls.shutdown()
        if restore:
            requests.Session, requests.sessions.Session, mulerouter.time, mulerouter.subprocess = originals
            asset_image_execution.time = original_clock
            delattr(requests, '_studio_full_body_smoke_active')


def create_smoke_app(root, static, evidence, *, owner='series', initial_selection=OLD_ID,
                     resume=False, hold_seconds=60, restore_transport=True):
    require_guard()
    from fastapi import Body, HTTPException
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app

    if initial_selection not in (None, OLD_ID):
        raise ValueError('Require an explicit known initial full-body selection')
    root = Path(root).resolve()
    marker = read_json(owned_path(root, MARKER))
    static = helpers._validate_static(root, marker)
    if (marker.get('kind') != images.KIND or evidence.path.is_relative_to(root)
            or owned_path(root, 'fixture.json').exists() != resume or evidence.payload.get('full_body_kind') != KIND):
        raise ValueError('Require a fresh owned image root or explicit full-body resume')
    marker_hash = base.sha256_file(root / MARKER)
    if resume:
        fixture = validate_seed_media(root)
        if (fixture['owner'] != owner or fixture['initial_selection'] != initial_selection
                or evidence.payload.get('fixture_root') != str(root)
                or evidence.payload.get('fixture_marker_sha256') != marker_hash
                or evidence.payload.get('fixture_sha256') != base.sha256_file(root / 'fixture.json')):
            raise ValueError('Resume source, owner, initial selection, fixture or evidence changed')
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
                    seed_fixture(target.state.pipeline, root, owner, initial_selection)
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
