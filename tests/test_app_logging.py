"""App-owned file logging is isolated by runtime context."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from src.apps.comic_gen import api
from src.apps.comic_gen.provider_recovery_dispatcher import StudioProviderRecoveryDispatcher
from src.utils.app_logging import app_log_context, create_app_file_handler


class StubPipeline:
    def __init__(self, *, data_dir, **kwargs):
        self.data_dir = Path(data_dir)
        self.scripts = {}
        self.closed = False

    def create_library_asset(self, *args, **kwargs):
        return None

    def read_committed_projects(self):
        return []

    def close(self):
        self.closed = True


def _app(root: Path):
    return api.create_app(
        data_dir=root / "media",
        user_data_dir=root / "user",
        load_config=False,
        setup_logs=True,
        pipeline_instance=StubPipeline(data_dir=root / "media"),
    )


def _log_text(root: Path) -> str:
    path = root / "user" / "logs" / "app.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_request_logs_are_routed_to_only_the_current_app_file(tmp_path):
    app_a = _app(tmp_path / "a")
    app_b = _app(tmp_path / "b")

    @app_a.get("/emit-log")
    def emit_a():
        logging.getLogger("lumenx-test").warning("request-log-a")
        return {"ok": True}

    @app_b.get("/emit-log")
    def emit_b():
        logging.getLogger("lumenx-test").warning("request-log-b")
        return {"ok": True}

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        assert client_a.get("/emit-log").status_code == 200
        assert client_b.get("/emit-log").status_code == 200
        logging.getLogger("lumenx-test").warning("unscoped-process-record")

    text_a = _log_text(tmp_path / "a")
    text_b = _log_text(tmp_path / "b")
    assert "STARTUP" in text_a
    assert "STARTUP" in text_b
    assert "request-log-a" in text_a
    assert "request-log-a" not in text_b
    assert "request-log-b" in text_b
    assert "request-log-b" not in text_a
    assert "unscoped-process-record" not in text_a
    assert "unscoped-process-record" not in text_b


def test_closing_one_app_keeps_other_app_logging_handler_active(tmp_path):
    app_a = _app(tmp_path / "a")
    app_b = _app(tmp_path / "b")

    @app_b.get("/emit-log")
    def emit_b():
        logging.getLogger("lumenx-test").warning("b-after-a-close")
        return {"ok": True}

    with TestClient(app_b) as client_b:
        with TestClient(app_a) as client_a:
            assert client_a.get("/health").status_code == 200
        assert client_b.get("/emit-log").status_code == 200

    assert "b-after-a-close" not in _log_text(tmp_path / "a")
    assert "b-after-a-close" in _log_text(tmp_path / "b")


def test_failed_startup_removes_owned_handler_and_blocks_unscoped_pollution(monkeypatch, tmp_path):
    before = list(logging.getLogger().handlers)

    def fail(**kwargs):
        raise ValueError("invalid config")

    monkeypatch.setattr(api, "load_user_config", fail)
    app = api.create_app(data_dir=tmp_path / "media", user_data_dir=tmp_path / "user", setup_logs=True)
    with pytest.raises(ValueError, match="invalid config"):
        with TestClient(app):
            raise AssertionError("startup should fail")

    logging.getLogger("lumenx-test").warning("after-failed-startup-unscoped")
    assert logging.getLogger().handlers == before
    assert "after-failed-startup-unscoped" not in _log_text(tmp_path)


def test_provider_recovery_worker_inherits_app_log_context(tmp_path):
    log_id, handler = create_app_file_handler(tmp_path / "worker.log")
    root = logging.getLogger()
    before_level = root.getEffectiveLevel()
    if before_level > logging.INFO:
        root.setLevel(logging.INFO)
    root.addHandler(handler)

    class FailingScanPipeline:
        # This test exercises an active recovery scan, not a paused runtime.
        provider_recovery_runtime = object()

        def __init__(self):
            self.calls = 0

        def read_committed_projects(self):
            self.calls += 1
            raise RuntimeError("worker scoped scan")

    pipeline = FailingScanPipeline()
    dispatcher = StudioProviderRecoveryDispatcher(pipeline, interval_seconds=60)
    try:
        with app_log_context(log_id):
            dispatcher.start()
        assert dispatcher.wait_for_idle(timeout=2)
        dispatcher.request_stop()
        dispatcher.join(timeout=2)
    finally:
        root.removeHandler(handler)
        handler.close()
        if before_level > logging.INFO:
            root.setLevel(before_level)

    assert pipeline.calls == 1
    assert "worker scoped scan" in (tmp_path / "worker.log").read_text(encoding="utf-8")


def test_real_lifespan_scopes_configuration_and_started_worker(monkeypatch, tmp_path):
    original_config = api._load_runtime_config

    def logged_config(**kwargs):
        logging.getLogger("lumenx-test").warning("runtime-config-owned")
        return original_config(**kwargs)

    class LoggingRecoveryPipeline(StubPipeline):
        provider_recovery_runtime = object()

        def _provider_runtime_or_none(self):
            return self.provider_recovery_runtime

        def read_committed_projects(self):
            logging.getLogger("lumenx-test").warning("started-worker-owned")
            return []

    monkeypatch.setattr(api, "_load_runtime_config", logged_config)
    app = api.create_app(
        data_dir=tmp_path / "media", user_data_dir=tmp_path / "user",
        load_config=False, setup_logs=True,
        pipeline_instance=LoggingRecoveryPipeline(data_dir=tmp_path / "media"),
        recover_orphan_tasks=True,
    )
    with TestClient(app):
        assert app.state.provider_recovery_dispatcher.wait_for_idle(timeout=2)

    text = _log_text(tmp_path)
    assert "runtime-config-owned" in text
    assert "started-worker-owned" in text


def test_stream_and_response_background_logs_retain_request_owner(tmp_path):
    app_a = _app(tmp_path / "a")
    app_b = _app(tmp_path / "b")

    @app_a.get("/stream-log")
    def stream():
        def chunks():
            yield b"first\n"
            logging.getLogger("lumenx-test").warning("a-stream-owned")
            yield b"last\n"

        return StreamingResponse(
            chunks(),
            background=BackgroundTask(logging.getLogger("lumenx-test").warning, "a-background-owned"),
        )

    with TestClient(app_a) as client, TestClient(app_b):
        assert client.get("/stream-log").text == "first\nlast\n"
    for marker in ("a-stream-owned", "a-background-owned"):
        assert marker in _log_text(tmp_path / "a")
        assert marker not in _log_text(tmp_path / "b")
