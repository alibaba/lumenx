"""Disconnected Wan HTTP fixture; fresh schema3 always runs the actual Core.

Only Session.send supplies provider responses. The optional private-result hook
calls through the real Core download/normalization, then injects a named fault.
No fresh record, handle, observation, proof, or owner projection is manufactured.
"""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace

import requests

from src.apps.comic_gen import asset_image_execution as module
from src.utils.media_probe import probe_image_file
from src.utils.system_check import get_ffmpeg_path
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_wan27_bound_image import response

PNG = rgb_png_bytes()
IMAGE_BASE = 'https://images.example.invalid/wan-neighbor/'


def assert_png(path):
    """Normalization may change compression; decoded provider pixels must not."""
    metadata = probe_image_file(path)
    assert (metadata.width, metadata.height) == (240, 240)
    # A unique ordinary file keeps decoding within the test-owned media root.
    with tempfile.NamedTemporaryFile(prefix='wan-rgb-', suffix='.rgb', dir=Path(path).parent, delete=False) as output:
        pixels_path = Path(output.name)
    checked = False
    try:
        subprocess.run([
            get_ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(path),
            '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', str(pixels_path),
        ], capture_output=True, check=True, timeout=10)
        assert pixels_path.is_file() and not pixels_path.is_symlink()
        assert pixels_path.read_bytes() == hashlib.sha256(b'frozen image').digest()[:3] * (240 * 240)
        checked = True
    finally:
        try:
            pixels_path.unlink()
        except OSError:
            # A cleanup error must not replace the original decode/assertion failure.
            if checked:
                raise
    return Path(path).read_bytes()


class WanImageHttpHarness:
    def __init__(self, monkeypatch, *, private_root=None):
        self.calls, self.records, self.paths, self.http_calls = [], [], [], []
        self.tasks = {}
        self.before_http = None
        self.callback = self.execute
        self.real_fetch = module.fetch_async_image_to_private_file
        self.original_mkdtemp = tempfile.mkdtemp
        self.clock = {'now': 0.0}
        if private_root is not None:
            private_root.mkdir()
            monkeypatch.setattr(tempfile, 'mkdtemp', lambda **kwargs: self.original_mkdtemp(dir=private_root, **kwargs))
        monkeypatch.setattr(module, 'time', SimpleNamespace(
            time=time.time, monotonic=lambda: self.clock['now'], sleep=self.sleep,
        ))
        monkeypatch.setattr(requests.sessions.Session, 'send', lambda _session, request, **kwargs: self.send(request, **kwargs))
        monkeypatch.setattr(module, 'fetch_async_image_to_private_file', self.fetch)

    def sleep(self, seconds):
        self.clock['now'] += seconds

    @property
    def posts(self):
        return [request for request, _ in self.http_calls if request.method == 'POST']

    @property
    def queries(self):
        return [request for request, _ in self.http_calls if request.method == 'GET' and '/api/v1/tasks/' in request.url]

    @property
    def downloads(self):
        return [request for request, _ in self.http_calls if request.url.startswith(IMAGE_BASE)]

    def __iter__(self):
        return iter((self.execute, self.calls, self.paths))

    def __getitem__(self, index):
        return (self.execute, self.calls, self.paths)[index]

    def send(self, request, **kwargs):
        self.http_calls.append((request, kwargs))
        if self.before_http is not None:
            self.before_http(request)
        if request.method == 'POST' and request.url.endswith('/api/v1/services/aigc/image-generation/generation'):
            payload = json.loads(request.body)
            assert payload['model'] in {'wan2.7-image', 'wan2.7-image-pro'}
            assert payload['parameters']['n'] == 1
            identity = f'wan-neighbor-{len(self.posts)}'
            self.tasks[identity] = payload
            result = response({'output': {'task_id': identity, 'task_status': 'PENDING'}})
        elif request.method == 'GET' and '/api/v1/tasks/' in request.url:
            identity = request.url.rsplit('/', 1)[-1]
            assert identity in self.tasks
            result = response({'output': {'task_id': identity, 'task_status': 'SUCCEEDED',
                'choices': [{'message': {'role': 'assistant', 'content': [
                    {'type': 'image', 'image': IMAGE_BASE + identity + '.png'},
                ]}}]}})
        elif request.method == 'GET' and request.url.startswith(IMAGE_BASE):
            identity = request.url.removeprefix(IMAGE_BASE).removesuffix('.png')
            assert identity in self.tasks
            result = response(PNG)
        else:
            raise AssertionError(f'Unexpected external HTTP boundary: {request.method} {request.url}')
        result.request, result.url = request, request.url
        return result

    def execute(self, record, authority, *, output_root, scratch_dir, max_bytes):
        assert record.schema_version == 3 and record.phase == 'observed'
        assert record.handle is not None and record.observation is not None
        self.calls.append((record.model_copy(deep=True), authority))
        self.records.append(record.model_copy(deep=True))
        result = self.real_fetch(record, authority, scratch_dir=scratch_dir, max_bytes=max_bytes)
        self.paths.append(Path(result.path))
        assert Path(result.path).read_bytes().startswith(b'\x89PNG')
        assert hashlib.sha256(Path(result.path).read_bytes()).hexdigest() == result.sha256
        return result

    def fetch(self, record, authority, *, scratch_dir, max_bytes):
        return self.callback(record, authority, output_root=None, scratch_dir=scratch_dir, max_bytes=max_bytes)
