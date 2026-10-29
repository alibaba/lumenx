"""Desktop imports stay inert; only explicit startup owns local services."""

import builtins
import importlib
import sys
import threading
import types
from pathlib import Path

import pytest


def install_static_shell(monkeypatch, tmp_path):
    app_root = tmp_path / "bundle"
    static = app_root / "static"
    static.mkdir(parents=True, exist_ok=True)
    (static / "index.html").write_text("<!doctype html><title>LumenX</title>", encoding="utf-8")
    main = importlib.import_module("main")
    monkeypatch.setattr(main, "get_application_path", lambda: app_root)
    return app_root


def test_import_does_not_change_process_or_start_backend(monkeypatch):
    cwd = Path.cwd()
    streams = (sys.stdout, sys.stderr)
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))
    sys.modules.pop("main", None)

    importlib.import_module("main")

    assert Path.cwd() == cwd
    assert (sys.stdout, sys.stderr) == streams
    assert started == []


def test_remote_window_does_not_import_or_start_local_backend(monkeypatch, tmp_path):
    main = importlib.import_module("main")
    windows, starts = [], []
    webview = types.SimpleNamespace(
        create_window=lambda **kwargs: windows.append(kwargs),
        start=lambda **kwargs: starts.append(kwargs),
    )
    monkeypatch.setitem(sys.modules, "webview", webview)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith(("uvicorn", "src.apps.comic_gen", "src.apps.playground"))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(threading.Thread, "start", lambda self: pytest.fail("local thread"))
    monkeypatch.setattr(main, "_check_backend_health", lambda *args, **kwargs: None)
    cwd = Path.cwd()
    streams = (sys.stdout, sys.stderr)

    main.run_desktop(backend_url="https://example.invalid/lumenx/", data_dir=tmp_path)

    assert windows[0]["url"] == "https://example.invalid/lumenx/"
    assert starts[0]["storage_path"] == str(tmp_path / "webview_storage")
    assert Path.cwd() == cwd
    assert (sys.stdout, sys.stderr) == streams
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("url", ["file:///tmp/x", "javascript:alert(1)", "example.com", "http://"])
def test_invalid_remote_url_is_rejected_before_initialization(tmp_path, url):
    main = importlib.import_module("main")
    target = tmp_path / "not-created"
    with pytest.raises(ValueError, match="URL"):
        main.run_desktop(backend_url=url, data_dir=target)
    assert not target.exists()


def test_local_window_uses_owned_server_and_stops_it(monkeypatch, tmp_path):
    install_static_shell(monkeypatch, tmp_path)
    main = importlib.import_module("main")
    stopped = threading.Event()
    calls = []

    class Server:
        started = False
        should_exit = False

        def run(self):
            self.started = True
            while not self.should_exit:
                stopped.wait(0.01)
            stopped.set()

    server = Server()

    def build_server(**kwargs):
        calls.append(kwargs)
        return server

    monkeypatch.setattr(main, "create_local_server", build_server)
    monkeypatch.setattr(main, "_check_backend_health", lambda *args, **kwargs: None)
    windows = []
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(
        create_window=lambda **kwargs: windows.append(kwargs),
        start=lambda **kwargs: None,
    ))
    main.run_desktop(data_dir=tmp_path, port=18177, startup_timeout=1)

    assert calls[0]["data_dir"] == tmp_path
    assert calls[0]["port"] == 18177
    assert windows[0]["url"].startswith("http://127.0.0.1:18177/static/index.html")
    assert server.should_exit
    assert stopped.is_set()


def test_failed_local_start_does_not_open_a_window(monkeypatch, tmp_path):
    install_static_shell(monkeypatch, tmp_path)
    main = importlib.import_module("main")
    server = types.SimpleNamespace(started=False, should_exit=False, run=lambda: None)
    monkeypatch.setattr(main, "create_local_server", lambda **kwargs: server)
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(
        create_window=lambda **kwargs: pytest.fail("window opened before readiness"),
        start=lambda **kwargs: None,
    ))
    with pytest.raises(RuntimeError, match="start"):
        main.run_desktop(data_dir=tmp_path, startup_timeout=0.1)
    assert server.should_exit


def test_webview_failure_still_stops_owned_server(monkeypatch, tmp_path):
    install_static_shell(monkeypatch, tmp_path)
    main = importlib.import_module("main")
    stopped = threading.Event()

    class Server:
        started = False
        should_exit = False

        def run(self):
            self.started = True
            while not self.should_exit:
                stopped.wait(0.01)
            stopped.set()

    server = Server()
    monkeypatch.setattr(main, "create_local_server", lambda **kwargs: server)
    monkeypatch.setattr(main, "_check_backend_health", lambda *args, **kwargs: None)

    def fail(**kwargs):
        raise RuntimeError("window failed")

    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(
        create_window=lambda **kwargs: None, start=fail,
    ))
    with pytest.raises(RuntimeError, match="window failed"):
        main.run_desktop(data_dir=tmp_path, startup_timeout=1)
    assert stopped.is_set()



class FakeHTTPResponse:
    def __init__(
        self,
        payload: str | bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ):
        self.payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit=-1):
        return self.payload


def install_webview(monkeypatch):
    windows, starts = [], []
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(
        create_window=lambda **kwargs: windows.append(kwargs),
        start=lambda **kwargs: starts.append(kwargs),
    ))
    return windows, starts


def valid_health_payload():
    return '{"ok": true, "service": "lumenx-core", "protocol_version": 1}'


def test_remote_backend_requires_lumenx_health_before_opening_window(monkeypatch, tmp_path):
    main = importlib.import_module("main")
    windows, _ = install_webview(monkeypatch)
    monkeypatch.setattr(main, "create_local_server", lambda **kwargs: pytest.fail("local backend"))
    monkeypatch.setattr(threading.Thread, "start", lambda self: pytest.fail("local thread"))
    calls = []

    def fake_open(url, *, timeout):
        calls.append((url, timeout))
        return FakeHTTPResponse('{"ok": true, "service": "not-lumenx", "protocol_version": 1}')

    monkeypatch.setattr(main, "_open_health_url", fake_open)

    with pytest.raises(RuntimeError, match="identity"):
        main.run_desktop(backend_url="https://remote.example/lumenx/", data_dir=tmp_path)

    assert calls == [("https://remote.example/lumenx/health", pytest.approx(5))]
    assert windows == []


@pytest.mark.parametrize(
    "entry_url, expected_health, expected_window",
    [
        ("https://remote.example/", "https://remote.example/health", "https://remote.example/"),
        (
            "https://remote.example/static",
            "https://remote.example/health",
            "https://remote.example/static",
        ),
        (
            "https://remote.example/static/",
            "https://remote.example/health",
            "https://remote.example/static/",
        ),
        (
            "https://remote.example/static/index.html",
            "https://remote.example/health",
            "https://remote.example/static/index.html",
        ),
        (
            "https://remote.example/lumenx/static",
            "https://remote.example/lumenx/health",
            "https://remote.example/lumenx/static",
        ),
        (
            "https://remote.example/lumenx/static/",
            "https://remote.example/lumenx/health",
            "https://remote.example/lumenx/static/",
        ),
        (
            "https://remote.example/lumenx/static/index.html?x=1#frag",
            "https://remote.example/lumenx/health",
            "https://remote.example/lumenx/static/index.html?x=1#frag",
        ),
        (
            "https://remote.example/lumenx/",
            "https://remote.example/lumenx/health",
            "https://remote.example/lumenx/",
        ),
    ],
)
def test_remote_backend_health_url_is_derived_without_changing_webview_entry(
    monkeypatch,
    tmp_path,
    entry_url,
    expected_health,
    expected_window,
):
    main = importlib.import_module("main")
    windows, _ = install_webview(monkeypatch)
    monkeypatch.setattr(threading.Thread, "start", lambda self: pytest.fail("local thread"))
    calls = []

    def fake_open(url, *, timeout):
        calls.append(url)
        return FakeHTTPResponse(valid_health_payload())

    monkeypatch.setattr(main, "_open_health_url", fake_open)

    main.run_desktop(backend_url=entry_url, data_dir=tmp_path)

    assert calls == [expected_health]
    assert windows[0]["url"] == expected_window


@pytest.mark.parametrize(
    "response, error_match",
    [
        (FakeHTTPResponse("<html>wrong</html>", content_type="text/html"), "JSON"),
        (
            FakeHTTPResponse('{"ok": true, "service": "lumenx-core", "protocol_version": 2}'),
            "protocol",
        ),
        (
            FakeHTTPResponse('{"ok": true, "service": "lumenx-core", "protocol_version": true}'),
            "protocol",
        ),
        (FakeHTTPResponse(b"\xff"), "valid JSON"),
    ],
)
def test_remote_backend_health_rejects_malformed_identity(
    monkeypatch,
    tmp_path,
    response,
    error_match,
):
    main = importlib.import_module("main")
    install_webview(monkeypatch)
    monkeypatch.setattr(threading.Thread, "start", lambda self: pytest.fail("local thread"))

    def fake_open(url, *, timeout):
        return response

    monkeypatch.setattr(main, "_open_health_url", fake_open)

    with pytest.raises(RuntimeError, match=error_match):
        main.run_desktop(backend_url="https://remote.example/", data_dir=tmp_path)


@pytest.mark.parametrize("exc, error_match", [
    (TimeoutError("slow"), "timed out"),
    (None, "HTTP 302"),
])
def test_remote_backend_health_rejects_timeout_and_redirect(
    monkeypatch,
    tmp_path,
    exc,
    error_match,
):
    main = importlib.import_module("main")
    install_webview(monkeypatch)
    monkeypatch.setattr(threading.Thread, "start", lambda self: pytest.fail("local thread"))

    def fake_open(url, *, timeout):
        if exc is not None:
            raise exc
        raise main.urllib.error.HTTPError(url, 302, "Found", {}, None)

    monkeypatch.setattr(main, "_open_health_url", fake_open)

    with pytest.raises(RuntimeError, match=error_match):
        main.run_desktop(backend_url="https://remote.example/", data_dir=tmp_path)


def test_local_desktop_refuses_missing_static_shell_before_opening_window(monkeypatch, tmp_path):
    main = importlib.import_module("main")
    monkeypatch.setattr(main, "get_application_path", lambda: tmp_path / "bundle")
    windows, _ = install_webview(monkeypatch)
    monkeypatch.setattr(main, "_check_backend_health", lambda *args, **kwargs: None)

    class Server:
        started = False
        should_exit = False

        def run(self):
            self.started = True

    server = Server()
    monkeypatch.setattr(main, "create_local_server", lambda **kwargs: server)

    with pytest.raises(RuntimeError, match="static app shell"):
        main.run_desktop(data_dir=tmp_path, startup_timeout=0.1)

    assert windows == []


def test_remote_desktop_does_not_require_local_static_shell(monkeypatch, tmp_path):
    main = importlib.import_module("main")
    monkeypatch.setattr(main, "get_application_path", lambda: tmp_path / "bundle")
    windows, _ = install_webview(monkeypatch)
    monkeypatch.setattr(main, "_open_health_url", lambda url, *, timeout: FakeHTTPResponse(valid_health_payload()))
    monkeypatch.setattr(threading.Thread, "start", lambda self: pytest.fail("local thread"))

    main.run_desktop(backend_url="https://remote.example/lumenx/", data_dir=tmp_path)

    assert windows[0]["url"] == "https://remote.example/lumenx/"


def test_local_started_flag_is_not_ready_without_health_identity(monkeypatch, tmp_path):
    install_static_shell(monkeypatch, tmp_path)
    main = importlib.import_module("main")
    server = types.SimpleNamespace(started=True, should_exit=False, run=lambda: None)
    monkeypatch.setattr(main, "create_local_server", lambda **kwargs: server)
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(
        create_window=lambda **kwargs: pytest.fail("window opened before health readiness"),
        start=lambda **kwargs: None,
    ))
    def fail_health(*args, **kwargs):
        raise RuntimeError("wrong service identity")

    monkeypatch.setattr(main, "_check_backend_health", fail_health)

    with pytest.raises(RuntimeError, match="health"):
        main.run_desktop(data_dir=tmp_path, port=18177, startup_timeout=0.1)

    assert server.should_exit


def test_local_shutdown_reports_join_timeout(monkeypatch, tmp_path):
    install_static_shell(monkeypatch, tmp_path)
    main = importlib.import_module("main")
    server = types.SimpleNamespace(started=True, should_exit=False, run=lambda: None)
    monkeypatch.setattr(main, "create_local_server", lambda **kwargs: server)
    monkeypatch.setattr(main, "_check_backend_health", lambda *args, **kwargs: None)
    install_webview(monkeypatch)

    class Thread:
        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon
            self.join_calls = []

        def start(self):
            return None

        def is_alive(self):
            return True

        def join(self, timeout=None):
            self.join_calls.append(timeout)

    monkeypatch.setattr(threading, "Thread", Thread)

    with pytest.raises(RuntimeError, match="did not stop"):
        main.run_desktop(data_dir=tmp_path, startup_timeout=0.1)

    assert server.should_exit


def test_webview_failure_is_not_masked_by_join_timeout(monkeypatch, tmp_path):
    install_static_shell(monkeypatch, tmp_path)
    main = importlib.import_module("main")
    server = types.SimpleNamespace(started=True, should_exit=False, run=lambda: None)
    monkeypatch.setattr(main, "create_local_server", lambda **kwargs: server)
    monkeypatch.setattr(main, "_check_backend_health", lambda *args, **kwargs: None)

    def fail_start(**kwargs):
        raise RuntimeError("window failed")

    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(
        create_window=lambda **kwargs: None,
        start=fail_start,
    ))

    class Thread:
        def __init__(self, target, daemon):
            pass

        def start(self):
            return None

        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(threading, "Thread", Thread)

    with pytest.raises(RuntimeError, match="window failed"):
        main.run_desktop(data_dir=tmp_path, startup_timeout=0.1)

    assert server.should_exit


def test_local_server_construction_defers_directory_creation(monkeypatch, tmp_path):
    install_static_shell(monkeypatch, tmp_path)
    main = importlib.import_module('main')
    # Real app assembly and Uvicorn configuration; no thread, socket or GUI.
    monkeypatch.setattr(main, 'get_application_path', lambda: tmp_path / 'bundle')
    data = tmp_path / 'user-data'
    cwd = Path.cwd()
    server = main.create_local_server(data_dir=data, port=18177)
    assert not data.exists()
    assert not hasattr(server.config.app.state, 'pipeline')
    assert server.config.host == '127.0.0.1'
    assert server.config.port == 18177
    assert Path.cwd() == cwd


@pytest.mark.parametrize(
    'frozen,explicit,expected',
    [(False, False, False), (True, False, True), (False, True, True)],
)
def test_desktop_preserves_development_and_packaged_config_sources(
    monkeypatch,
    tmp_path,
    frozen,
    explicit,
    expected,
):
    install_static_shell(monkeypatch, tmp_path)
    main = importlib.import_module('main')
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.delenv('LUMENX_DATA_DIR', raising=False)
    monkeypatch.setattr(main, 'configure_packaged_paths', lambda: tmp_path)
    monkeypatch.setattr(main, 'default_user_data_dir', lambda: tmp_path)
    calls = []
    server = types.SimpleNamespace(started=False, should_exit=False, run=lambda: None)
    def build(**kwargs):
        calls.append(kwargs)
        return server
    monkeypatch.setattr(main, 'create_local_server', build)
    with pytest.raises(RuntimeError, match='start'):
        main.run_desktop(data_dir=tmp_path if explicit else None, startup_timeout=0.01)
    assert calls[0]['use_user_config'] is expected
