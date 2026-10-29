"""Headless entry contracts using the real app, persistence and access middleware."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest


def server_module():
    assert importlib.util.find_spec("src.lumenx_server") is not None, "headless entry is missing"
    return importlib.import_module("src.lumenx_server")


@pytest.fixture(autouse=True)
def local_access(monkeypatch):
    monkeypatch.setenv("LUMENX_ACCESS_MODE", "local")
    monkeypatch.delenv("LUMENX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LUMENX_ACCESS_ALLOWED_ORIGINS", raising=False)


def test_explicit_data_root_and_api_only_lifecycle(tmp_path):
    module = server_module()
    root = tmp_path / "service-data"
    before = Path.cwd()
    server = module.create_server(data_dir=root)
    assert not root.exists()
    with TestClient(server.config.app) as client:
        assert client.get("/health").json()["service"] == "lumenx-core"
        config = client.get("/config/info").json()
        assert config["config_path"] == str(root / "config.json")
        assert (root / "output/storage_identity.json").is_file()
        assert (root / "logs/app.log").is_file()
        assert client.get("/").status_code == 404
        assert client.get("/static/index.html").status_code == 404
    assert not hasattr(server.config.app.state, "pipeline")
    assert Path.cwd() == before


def test_config_and_project_data_survive_server_recreation(tmp_path):
    module = server_module()
    root = tmp_path / "persistent"
    with TestClient(module.create_server(data_dir=root).config.app) as client:
        response = client.post("/config/env", json={"OSS_ENABLE": False})
        assert response.status_code == 200, response.text
        created = client.post("/playground/templates", json={"name": "Keep me", "prompt": "lighthouse"})
        assert created.status_code == 200, created.text
        storage_id = client.get("/runtime/environment").json()["storage_id"]
    assert json.loads((root / "config.json").read_text())["OSS_ENABLE"] == "false"
    reopened = module.create_server(data_dir=root)
    with TestClient(reopened.config.app) as client:
        assert client.get("/runtime/environment").json()["storage_id"] == storage_id
        assert [x["name"] for x in client.get("/playground/templates").json()] == ["Keep me"]
        assert reopened.config.app.state.runtime_environment.snapshot()["OSS_ENABLE"] == "false"


def test_separate_roots_do_not_share_state(tmp_path):
    module = server_module()
    with TestClient(module.create_server(data_dir=tmp_path / "a").config.app) as a:
        with TestClient(module.create_server(data_dir=tmp_path / "b").config.app) as b:
            result = a.post("/playground/templates", json={"name": "Only A", "prompt": "lighthouse"})
            assert result.status_code == 200
            assert b.get("/playground/templates").json() == []
            assert a.get("/runtime/environment").json()["storage_id"] != b.get("/runtime/environment").json()["storage_id"]


def test_empty_private_root_does_not_inherit_process_provider_credentials(monkeypatch, tmp_path):
    module = server_module()
    ambient = {
        "DASHSCOPE_API_KEY": "synthetic-ambient-dashscope",
        "OPENAI_API_KEY": "synthetic-ambient-openai",
        "ALIBABA_CLOUD_ACCESS_KEY_ID": "synthetic-ambient-oss-id",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "synthetic-ambient-oss-secret",
        "OSS_BUCKET_NAME": "synthetic-ambient-bucket",
        "OSS_ENDPOINT": "https://oss.example.invalid",
    }
    for key, value in ambient.items():
        monkeypatch.setenv(key, value)
    server = module.create_server(data_dir=tmp_path / "private")
    with TestClient(server.config.app) as client:
        config = client.get("/config/env").json()
        for key in ambient:
            if key in config:
                assert config[key] == ""
            if key in config["secrets_configured"]:
                assert config["secrets_configured"][key] is False
        pipeline = server.config.app.state.pipeline
        assert not set(ambient).intersection(pipeline.environment_snapshot())
        assert pipeline.script_processor.is_configured is False
        with pytest.raises(RuntimeError, match="credentials are not configured"):
            pipeline.script_processor.llm.chat([{"role": "user", "content": "No provider call is allowed."}])
        assert pipeline.oss_uploader().is_configured is False
    assert {key: os.environ[key] for key in ambient} == ambient


def test_second_writer_is_rejected_and_exit_releases_root(tmp_path):
    module = server_module()
    from src.utils.json_storage import StorageConflictError

    root = tmp_path / "exclusive"
    with TestClient(module.create_server(data_dir=root).config.app) as first:
        with pytest.raises(StorageConflictError, match="already has a writer"):
            with TestClient(module.create_server(data_dir=root).config.app):
                pytest.fail("second writer was accepted")
        assert first.get("/health").status_code == 200
    with TestClient(module.create_server(data_dir=root).config.app) as reopened:
        assert reopened.get("/health").status_code == 200


def test_optional_static_shell_does_not_swallow_api_404(tmp_path):
    module = server_module()
    static = tmp_path / "shell"
    static.mkdir()
    (static / "index.html").write_text('<!doctype html><script src="/static/app.js"></script>')
    (static / "app.js").write_text("window.lumenx=true;")
    with TestClient(module.create_server(data_dir=tmp_path / "data", static_dir=static).config.app) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code in {302, 307}
        assert root.headers["location"] == "/static/index.html"
        assert "doctype html" in client.get("/static/index.html").text
        asset = client.get("/static/app.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
        assert client.get("/runtime/environment").status_code == 200
        missing = client.get("/missing-api")
        assert missing.status_code == 404
        assert "application/json" in missing.headers["content-type"]


def test_missing_static_shell_fails_before_data_writes(tmp_path):
    module = server_module()
    root = tmp_path / "untouched"
    with pytest.raises(ValueError, match="index.html"):
        module.create_server(data_dir=root, static_dir=tmp_path / "missing-shell")
    assert not root.exists()


@pytest.mark.parametrize("layout", ["same", "data-inside-static", "static-inside-data"])
def test_static_and_data_roots_cannot_overlap(tmp_path, layout):
    module = server_module()
    root = tmp_path / "data"
    static = root if layout == "same" else tmp_path if layout == "data-inside-static" else root / "shell"
    static.mkdir(parents=True, exist_ok=True)
    (static / "index.html").write_text("<!doctype html>")
    with pytest.raises(ValueError, match="overlap"):
        module.create_server(data_dir=root, static_dir=static)
    assert not (root / "output").exists()


def test_single_worker_and_explicit_proxy_ignore_process_defaults(monkeypatch, tmp_path):
    module = server_module()
    monkeypatch.setenv("WEB_CONCURRENCY", "8")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    config = module.create_server(data_dir=tmp_path / "data").config
    assert config.host == "127.0.0.1"
    assert config.port == 17177
    assert config.workers == 1
    assert config.reload is False
    assert config.proxy_headers is True
    assert config.forwarded_allow_ips == ["127.0.0.1"]


@pytest.mark.parametrize("trust", ["*", "127.0.0.1,*", "10.0.0.0/24", "localhost", ""])
def test_only_explicit_proxy_ip_addresses_are_accepted(tmp_path, trust):
    module = server_module()
    root = tmp_path / "not-started"
    with pytest.raises(ValueError, match="proxy"):
        module.create_server(data_dir=root, trusted_proxy_ips=trust)
    assert not root.exists()


def test_remote_mode_requires_a_token(monkeypatch, tmp_path):
    module = server_module()
    monkeypatch.setenv("LUMENX_ACCESS_MODE", "remote")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        with TestClient(module.create_server(data_dir=tmp_path / "data").config.app):
            pytest.fail("remote service started without an access token")


def test_remote_login_and_origin_boundary(monkeypatch, tmp_path):
    module = server_module()
    token = "synthetic-headless-access-" * 2
    monkeypatch.setenv("LUMENX_ACCESS_MODE", "remote")
    monkeypatch.setenv("LUMENX_ACCESS_TOKEN", token)
    with TestClient(module.create_server(data_dir=tmp_path / "data").config.app, base_url="https://studio.example") as client:
        assert client.get("/runtime/environment").status_code == 401
        logged_in = client.post("/auth/session", json={"token": token}, headers={"Origin": "https://studio.example"})
        assert logged_in.status_code == 200
        assert "Secure" in logged_in.headers["set-cookie"]
        assert client.get("/auth/session").json()["authenticated"] is True
        assert client.get("/runtime/environment").status_code == 200
        denied = client.post("/config/env", json={"OSS_ENABLE": False}, headers={"Origin": "https://other.example"})
        assert denied.status_code == 403
        accepted = client.post("/config/env", json={"OSS_ENABLE": False}, headers={"Origin": "https://studio.example"})
        assert accepted.status_code == 200


@pytest.mark.parametrize("peer,status", [("127.0.0.1", 200), ("203.0.113.8", 403)])
def test_forwarded_scheme_is_trusted_only_from_named_proxy(monkeypatch, tmp_path, peer, status):
    module = server_module()
    token = "synthetic-headless-access-" * 2
    monkeypatch.setenv("LUMENX_ACCESS_MODE", "remote")
    monkeypatch.setenv("LUMENX_ACCESS_TOKEN", token)
    server = module.create_server(data_dir=tmp_path / "data", trusted_proxy_ips="127.0.0.1,::1")
    server.config.load()
    with TestClient(server.config.loaded_app, base_url="http://studio.example", client=(peer, 5152)) as client:
        response = client.post("/auth/session", json={"token": token}, headers={
            "Origin": "https://studio.example", "X-Forwarded-Proto": "https",
        })
        assert response.status_code == status, response.text


def test_module_help_does_not_import_gui_or_application(tmp_path):
    server_module()
    probe = tmp_path / "headless-help.py"
    probe.write_text('''import builtins, runpy, sys
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    assert not name.startswith(("webview", "main", "uvicorn", "src.apps")), name
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
sys.argv = ["lumenx_server", "--help"]
runpy.run_module("src.lumenx_server", run_name="__main__")
''')
    helper = Path(os.environ["LUMENX_REPO_ROOT"]) / "scripts/test_support/isolated_child.py"
    result = subprocess.run([sys.executable, "-S", str(helper), str(probe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--data-dir" in result.stdout and "--static-dir" in result.stdout


def test_cli_requires_an_explicit_data_root():
    module = server_module()
    with pytest.raises(SystemExit) as exc:
        module.parse_args([])
    assert exc.value.code == 2


def test_cli_parameters_start_the_selected_server(monkeypatch, tmp_path):
    module = server_module()
    import uvicorn

    started = []

    def run(server):
        started.append(server.config)
        server.started = True

    monkeypatch.setattr(uvicorn.Server, "run", run)
    root = tmp_path / "selected-root"
    module.main(["--data-dir", str(root), "--port", "18178", "--trusted-proxy-ips", "127.0.0.1,::1"])
    assert len(started) == 1
    assert started[0].port == 18178
    assert started[0].forwarded_allow_ips == ["127.0.0.1", "::1"]
    assert not root.exists(), "the stubbed server must not enter the app lifespan"


@pytest.mark.parametrize("started", [False, True])
def test_cli_reports_whether_returned_server_started(monkeypatch, tmp_path, started):
    module = server_module()
    import uvicorn

    def returned(server):
        # Older supported Uvicorn releases return after failed lifespan startup.
        server.started = started

    monkeypatch.setattr(uvicorn.Server, "run", returned)
    if started:
        assert module.main(["--data-dir", str(tmp_path / "data")]) is None
    else:
        with pytest.raises(SystemExit) as exc:
            module.main(["--data-dir", str(tmp_path / "data")])
        assert exc.value.code == 3


def test_cli_server_error_is_not_swallowed(monkeypatch, tmp_path):
    module = server_module()
    import uvicorn

    def failed(_server):
        raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(uvicorn.Server, "run", failed)
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        module.main(["--data-dir", str(tmp_path / "data")])


def test_cli_ctrl_c_exits_without_a_traceback(monkeypatch, tmp_path):
    module = server_module()
    import uvicorn

    def interrupted(_server):
        raise KeyboardInterrupt

    monkeypatch.setattr(uvicorn.Server, "run", interrupted)
    try:
        module.main(["--data-dir", str(tmp_path / "data")])
    except KeyboardInterrupt:
        pytest.fail("Ctrl+C escaped the headless command boundary")
