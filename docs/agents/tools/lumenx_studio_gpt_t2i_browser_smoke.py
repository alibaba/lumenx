"""Finite GPT T2I browser fixture using real app/Core and external HTTP only.

Load through the existing pre-import isolation launcher. Reuse the proven exact
GET hold and controls; no listener, freezer or replacement product method here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace


_spec = importlib.util.spec_from_file_location(
    'gpt_t2i_headshot_helpers', Path(__file__).with_name('lumenx_studio_headshot_browser_smoke.py'))
assert _spec and _spec.loader
headshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(headshot)
images, helpers = headshot.images, headshot.helpers
base, event = headshot.base, headshot.event
prepare_root, MARKER = headshot.prepare_root, headshot.MARKER
owned_path, read_json = headshot.owned_path, headshot.read_json
Controls, ProjectGetHold, require_guard = headshot.Controls, headshot.ProjectGetHold, headshot.require_guard
KIND = 'lumenx.studio-gpt-t2i.browser-fixture.v1'
SECRET = 'synthetic-example-gpt-t2i-http'
GENERATION = 'https://api.mulerouter.ai/vendors/openai/v1/gpt-image-2/generation'
RESULT = 'https://gpt-t2i-browser.invalid'
FIXTURE = {'project_id': 'p', 'series_id': 's', 'asset_id': 'c'}
OWNER_ASSETS = {'series': 'scene', 'global': 'prop'}
SIZES = {'scene': '1536x1024', 'prop': '1024x1024'}
OLD_ID = 'old'
OLD_REF = 'uploads/old.png'
SYNTHETIC_TIMESTAMP = '2026-10-25T00:00:00Z'


def open_evidence(path, *, resume):
    if resume and read_json(path).get('gpt_t2i_kind') != KIND:
        raise ValueError('Resume requires GPT-T2I-owned evidence')
    evidence = images.open_evidence(path, resume=resume)
    if not resume:
        evidence.update(gpt_t2i_kind=KIND, external_tasks={})
    return evidence


def validate_seed_media(root):
    fixture = read_json(owned_path(root, 'fixture.json'))
    owner = fixture.get('owner')
    if (fixture.get('kind') != KIND or owner not in OWNER_ASSETS
            or fixture.get('asset_type') != OWNER_ASSETS[owner]
            or fixture.get('initial_selection') != OLD_ID):
        raise ValueError('Invalid GPT T2I fixture identity')
    expected = fixture.get('media_hashes')
    if not isinstance(expected, dict) or set(expected) != {OLD_REF}:
        raise ValueError('Require only the existing output PNG binding, never reference inputs')
    for relative, digest in expected.items():
        if base.sha256_file(owned_path(root / 'output', relative)) != digest:
            raise ValueError('Original fixture PNG changed')
    return fixture


def seed_fixture(pipeline, root, owner, initial_selection):
    from src.apps.comic_gen.models import Prop, Scene, Script, Series
    from tests.frozen_image_fixtures import rgb_png_bytes

    kind = OWNER_ASSETS[owner]
    path = owned_path(root / 'output', OLD_REF)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rgb_png_bytes(OLD_REF))
    asset_data = {
        'id': 'c', 'name': 'Series Scene' if kind == 'scene' else 'Global Prop',
        'description': 'Synthetic browser ' + kind + ', preserve this authored description.',
        'status': 'completed', 'image_url': OLD_REF, 'starred': True,
        'video_prompt': 'Keep independent motion',
        'image_asset': {'selected_id': initial_selection, 'variants': [{
            'id': OLD_ID, 'url': OLD_REF, 'created_at': 1,
            'prompt_used': 'Previous ' + kind + ' prompt', 'is_favorited': True,
        }]},
    }
    project = Script.model_validate({
        'id': 'p', 'title': 'GPT T2I browser · ' + owner, 'original_text': 'Synthetic',
        'created_at': 1, 'updated_at': 1, 'workflow_mode': 'i2v_legacy',
    })
    project.model_settings.t2i_model = 'gpt-image-2'
    project.model_settings.i2i_model = 'wan2.7-image'
    project.model_settings.scene_aspect_ratio = '16:9'
    project.model_settings.prop_aspect_ratio = '1:1'
    if owner == 'series':
        series = Series.model_validate({
            'id': 's', 'title': 'Shared GPT T2I fixture', 'created_at': 1, 'updated_at': 1,
            'episode_ids': ['p'], 'scenes': [Scene.model_validate(asset_data)],
        })
        series.model_settings = project.model_settings.model_copy(deep=True)
        pipeline.series_store['s'], project.series_id = series, 's'
    else:
        pipeline.library_store.props = [Prop.model_validate(asset_data)]
    pipeline.scripts['p'] = project
    pipeline._commit_storage('projects.json', 'series.json', 'library_assets.json')
    owned_path(root, 'fixture.json').write_text(json.dumps({
        'kind': KIND, 'owner': owner, 'asset_type': kind, 'fixture': FIXTURE,
        'initial_selection': initial_selection, 'media_hashes': {OLD_REF: base.sha256_file(path)},
    }, indent=2) + '\n')


@contextmanager
def offline_transport(evidence, controls, root, *, restore=True):
    import requests
    from requests.adapters import BaseAdapter
    from src.models import mulerouter
    from src.apps.comic_gen import asset_image_execution
    from tests.frozen_image_fixtures import rgb_png_bytes

    require_guard()
    if (getattr(requests, '_studio_gpt_t2i_smoke_active', False)
            or getattr(requests, '_studio_full_body_smoke_active', False)
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
                raise requests.Timeout('Owned GPT T2I transport is closing')
            url, method = request.url, request.method
            status = 200
            auth = request.headers.get('Authorization')
            if request.headers.get('Cookie'):
                raise RuntimeError('Cookies are not part of the synthetic image transport')
            if method == 'POST' and url == GENERATION and auth == 'Bearer ' + SECRET:
                payload = json.loads(request.body)
                if (not isinstance(payload, dict) or set(payload) != {'prompt', 'size', 'quality', 'n'}
                        or payload['size'] != SIZES[OWNER_ASSETS[controls.owner]] or payload['quality'] != 'high'
                        or type(payload['n']) is not int or payload['n'] != 1
                        or not isinstance(payload['prompt'], str) or not payload['prompt'].strip()):
                    raise RuntimeError('Unexpected GPT T2I generation payload')
                # Reuse opaque external task IDs only; the real Core intent must
                # remain unmarked T2I with empty media, verified by self-checks.
                identity, lost = controls.submit(payload, None)
                if lost:
                    event(evidence, 'controls', action='provider_submit_ack_lost', task_id=identity)
                    raise requests.Timeout('Synthetic external submit acknowledgement lost')
                status = 202
                body = {'task_info': {'id': identity, 'status': 'pending',
                                     'created_at': SYNTHETIC_TIMESTAMP, 'updated_at': SYNTHETIC_TIMESTAMP}}
            elif method == 'GET' and isinstance(url, str) and url.startswith(GENERATION + '/') and auth == 'Bearer ' + SECRET:
                identity = url.removeprefix(GENERATION + '/')
                if identity not in controls.tasks:
                    raise RuntimeError('Unknown external GPT task')
                evidence.append_provider_event('query', {'task_id': identity})
                body = {'task_info': {'id': identity, 'status': 'completed',
                                     'created_at': SYNTHETIC_TIMESTAMP, 'updated_at': SYNTHETIC_TIMESTAMP},
                        'images': [RESULT + '/' + identity + '.png']}
            elif method == 'GET' and isinstance(url, str) and url.startswith(RESULT + '/') and auth is None:
                identity = url.removeprefix(RESULT + '/').removesuffix('.png')
                if identity not in controls.tasks or url != RESULT + '/' + identity + '.png':
                    raise RuntimeError('Unknown external PNG')
                evidence.append_provider_event('fetch', {'task_id': identity})
                body = rgb_png_bytes(identity, width=160, height=160)
            else:
                raise RuntimeError('Unexpected HTTP blocked by the GPT T2I fixture')
            if not isinstance(body, bytes):
                event(evidence, 'external_http_responses', method=method, url=url, status=status, body=body)
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
        raise RuntimeError('GPT T2I browser uses HTTP authority only')

    setattr(requests, '_studio_gpt_t2i_smoke_active', True)
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
            delattr(requests, '_studio_gpt_t2i_smoke_active')


def create_smoke_app(root, static, evidence, *, owner='series', initial_selection=OLD_ID,
                     resume=False, hold_seconds=60, restore_transport=True):
    require_guard()
    from fastapi import Body, HTTPException
    from starlette.staticfiles import StaticFiles
    from src.apps.comic_gen.api import create_app

    if owner not in OWNER_ASSETS or initial_selection != OLD_ID:
        raise ValueError('Require series scene or global prop and the existing old selection')
    root = Path(root).resolve()
    marker = read_json(owned_path(root, MARKER))
    static = helpers._validate_static(root, marker)
    if (marker.get('kind') != images.KIND or evidence.path.is_relative_to(root)
            or owned_path(root, 'fixture.json').exists() != resume or evidence.payload.get('gpt_t2i_kind') != KIND):
        raise ValueError('Require a fresh owned image root or explicit GPT T2I resume')
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
