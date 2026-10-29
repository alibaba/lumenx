"""Desktop static app-shell readiness uses real ASGI static serving, no GUI/socket."""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def write_static_shell(root: Path) -> Path:
    static = root / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text('<!doctype html><script src="/static/app.js"></script>', encoding="utf-8")
    (static / "app.js").write_text("window.__lumenx = true;", encoding="utf-8")
    return static


def test_local_server_serves_configured_static_shell(monkeypatch, tmp_path):
    main = importlib.import_module("main")
    app_root = tmp_path / "bundle"
    write_static_shell(app_root)
    monkeypatch.setattr(main, "get_application_path", lambda: app_root)

    server = main.create_local_server(data_dir=tmp_path / "user-data", use_user_config=False)

    with TestClient(server.config.app) as client:
        index = client.get("/static/index.html")
        assert index.status_code == 200
        assert "doctype html" in index.text
        asset = client.get("/static/app.js")
        assert asset.status_code == 200
        assert "__lumenx" in asset.text


def test_local_server_rejects_missing_static_shell(monkeypatch, tmp_path):
    main = importlib.import_module("main")
    app_root = tmp_path / "bundle"
    (app_root / "static").mkdir(parents=True)
    monkeypatch.setattr(main, "get_application_path", lambda: app_root)

    with pytest.raises(RuntimeError, match="static app shell"):
        main.create_local_server(data_dir=tmp_path / "user-data", use_user_config=False)
