"""P7 app access boundary regressions.

These tests use real FastAPI/TestClient paths and fake only the pipeline object so
no provider, socket, or GUI is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.apps.comic_gen.api import create_app


# Deliberately synthetic fixtures, never deployment credentials.
TOKEN = "test-access-" * 4
WRONG_TOKEN = "invalid-" * 5


class StubPipeline:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.scripts = {}
        self.closed = False

    def assert_storage_available(self):
        return None

    def create_library_asset(self, *args, **kwargs):
        return None

    def read_committed_projects(self):
        return []

    def read_committed_script(self, script_id):
        return {"id": script_id}

    def refine_batch_generator(self, script_id):
        yield "progress", {"script_id": script_id, "step": 1}
        yield "done", {"script_id": script_id}

    def close(self):
        self.closed = True


def session_cookie_value(response) -> str:
    cookie = response.headers["set-cookie"]
    first = cookie.split(";", 1)[0]
    return first.split("=", 1)[1]


def write_media(root: Path, relative: str, content: bytes = b"0123456789abcdef") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def make_app(tmp_path: Path, **kwargs):
    root = tmp_path / "media"
    return create_app(
        data_dir=root,
        user_data_dir=tmp_path / "user",
        load_config=False,
        pipeline_instance=StubPipeline(root),
        **kwargs,
    )


def make_remote_client(tmp_path: Path, **kwargs) -> TestClient:
    app = make_app(
        tmp_path,
        access_mode="remote",
        access_token=TOKEN,
        access_allowed_origins=["https://studio.example"],
        **kwargs,
    )
    return TestClient(app, base_url="https://studio.example")


def test_local_mode_default_keeps_existing_api_and_media_access(tmp_path):
    write_media(tmp_path / "media", "uploads/ref.png", b"image")
    with TestClient(make_app(tmp_path), base_url="http://testserver") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/projects/").status_code == 200
        assert client.get("/files/uploads/ref.png").content == b"image"
        assert client.get("/debug/config").status_code == 200




def test_local_auth_session_contract_is_available_without_remote_mode(tmp_path):
    with TestClient(make_app(tmp_path), base_url="http://testserver") as client:
        expected = {"auth_required": False, "authenticated": True}
        assert client.get("/auth/session").json() == expected
        assert client.post("/auth/session", json={"token": "ignored"}).json() == expected
        assert client.delete("/auth/session").json() == expected


def test_remote_non_ascii_authorization_is_rejected_not_500(tmp_path):
    with make_remote_client(tmp_path) as client:
        response = client.get("/projects/", headers=[(b"authorization", "Bearer 你好".encode("utf-8"))])
        assert response.status_code == 401
        assert response.json()["code"] == "access_required"


def test_remote_login_rejects_oversized_body_without_authenticating(tmp_path):
    with make_remote_client(tmp_path) as client:
        response = client.post(
            "/auth/session",
            content=b"{" + b"a" * 8192 + b"}",
            headers={"Origin": "https://studio.example", "Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert client.get("/projects/").status_code == 401


def test_remote_sessions_are_cleared_when_lifespan_closes(tmp_path):
    app = make_app(
        tmp_path,
        access_mode="remote",
        access_token=TOKEN,
        access_allowed_origins=["https://studio.example"],
    )
    with TestClient(app, base_url="https://studio.example") as client:
        login = client.post(
            "/auth/session",
            json={"token": TOKEN},
            headers={"Origin": "https://studio.example"},
        )
        assert login.status_code == 200
        assert app.state.app_access.sessions
    assert app.state.app_access.sessions == {}




def test_local_dev_origin_get_session_api_and_preflight_get_precise_cors(tmp_path):
    with TestClient(make_app(tmp_path), base_url="http://127.0.0.1:17177") as client:
        session = client.get("/auth/session", headers={"Origin": "http://127.0.0.1:3008"})
        assert session.status_code == 200
        assert session.json() == {"auth_required": False, "authenticated": True}
        assert session.headers["access-control-allow-origin"] == "http://127.0.0.1:3008"
        assert session.headers["access-control-allow-credentials"] == "true"
        assert session.headers["cache-control"] == "no-store"

        projects = client.get("/projects/", headers={"Origin": "http://127.0.0.1:3008"})
        assert projects.status_code == 200
        assert projects.headers["access-control-allow-origin"] == "http://127.0.0.1:3008"
        assert projects.headers["access-control-allow-credentials"] == "true"
        assert projects.headers.get("cache-control") != "no-store"

        preflight = client.options(
            "/projects/",
            headers={
                "Origin": "http://127.0.0.1:3008",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:3008"
        assert preflight.headers["access-control-allow-credentials"] == "true"


def test_local_disallowed_origin_does_not_reflect_cors_or_disable_media_cache(tmp_path):
    write_media(tmp_path / "media", "uploads/ref.png", b"image")
    with TestClient(make_app(tmp_path), base_url="http://127.0.0.1:17177") as client:
        evil = client.get("/projects/", headers={"Origin": "http://evil.example"})
        assert evil.status_code == 200
        assert "access-control-allow-origin" not in evil.headers

        media = client.get("/files/uploads/ref.png", headers={"Origin": "http://127.0.0.1:3008"})
        assert media.status_code == 200
        assert media.content == b"image"
        assert media.headers["cache-control"] == "public, max-age=86400"




def test_local_explicit_allowed_origin_can_differ_from_backend_hostname(tmp_path):
    app = make_app(tmp_path, access_allowed_origins=["http://studio.local:3008"])
    with TestClient(app, base_url="http://127.0.0.1:17177") as client:
        allowed = client.get("/projects/", headers={"Origin": "http://studio.local:3008"})
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "http://studio.local:3008"

        disallowed = client.get("/projects/", headers={"Origin": "http://other.local:3008"})
        assert disallowed.status_code == 200
        assert "access-control-allow-origin" not in disallowed.headers


def test_remote_login_rejects_lone_surrogate_and_deep_json_without_500(tmp_path):
    with make_remote_client(tmp_path) as client:
        surrogate = client.post(
            "/auth/session",
            content=b'{"token":"\\ud800"}',
            headers={"Origin": "https://studio.example", "Content-Type": "application/json"},
        )
        assert surrogate.status_code == 401

        deep_body = ("[" * 1500 + "]" * 1500).encode("utf-8")
        deep = client.post(
            "/auth/session",
            content=deep_body,
            headers={"Origin": "https://studio.example", "Content-Type": "application/json"},
        )
        assert deep.status_code == 401


def test_remote_rejects_fractional_session_limits_and_insecure_host_cookie(tmp_path):
    invalid_cases = [
        {"access_session_ttl_seconds": 1.5},
        {"access_max_sessions": 1.5},
        {"access_cookie_secure": False},
    ]
    for kwargs in invalid_cases:
        app = make_app(tmp_path / str(len(str(kwargs))), access_mode="remote", access_token=TOKEN, **kwargs)
        with pytest.raises(ValueError):
            with TestClient(app, base_url="https://studio.example"):
                pass


def test_remote_mode_requires_strong_token_at_startup(tmp_path):
    app = make_app(tmp_path, access_mode="remote", access_token="short")
    with pytest.raises(ValueError, match="LUMENX_ACCESS_TOKEN"):
        with TestClient(app, base_url="https://studio.example"):
            pass




def test_remote_rejects_nonpositive_session_limits_and_invalid_origin_allowlist(tmp_path):
    invalid_cases = [
        {"access_session_ttl_seconds": 0},
        {"access_session_ttl_seconds": -1},
        {"access_max_sessions": 0},
        {"access_max_sessions": -1},
        {"access_allowed_origins": ["*"]},
        {"access_allowed_origins": ["null"]},
        {"access_allowed_origins": ["https://studio.example/app"]},
        {"access_allowed_origins": ["https://studio.example?x=1"]},
    ]
    for kwargs in invalid_cases:
        app = make_app(tmp_path / str(len(str(kwargs))), access_mode="remote", access_token=TOKEN, **kwargs)
        with pytest.raises(ValueError):
            with TestClient(app, base_url="https://studio.example"):
                pass


def test_access_config_repr_does_not_include_token_value():
    from src.utils.app_access import resolve_app_access_config

    config = resolve_app_access_config(mode="remote", token=TOKEN)
    assert TOKEN not in repr(config)
    assert "token=" not in repr(config)


def test_remote_health_is_public_minimal_but_authenticated_health_keeps_diagnostics(tmp_path):
    with make_remote_client(tmp_path) as client:
        public = client.get("/health")
        assert public.status_code == 200
        assert public.json()["service"] == "lumenx-core"
        assert "protocol_version" in public.json()
        assert public.headers["cache-control"] == "no-store"
        assert "log_file" not in public.json()
        assert "log_dir" not in public.json()
        assert "studio_projects" not in public.json()

        authenticated = client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})
        assert authenticated.status_code == 200
        assert "log_file" in authenticated.json()
        assert "studio_projects" in authenticated.json()


def test_remote_rejects_sensitive_routes_docs_and_media_without_auth(tmp_path):
    write_media(tmp_path / "media", "uploads/ref.png", b"image")
    with make_remote_client(tmp_path) as client:
        for method, path in [
            ("GET", "/projects/"),
            ("GET", "/config/env"),
            ("POST", "/config/env"),
            ("GET", "/files/uploads/ref.png"),
            ("GET", "/debug/config"),
            ("GET", "/diagnose/log_tail"),
            ("GET", "/docs"),
            ("GET", "/openapi.json"),
        ]:
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code == 401, (method, path, response.status_code, response.text)
            assert response.headers["cache-control"] == "no-store"
            assert TOKEN not in response.text


def test_remote_accepts_bearer_token_and_rejects_wrong_token_without_leak(tmp_path):
    with make_remote_client(tmp_path) as client:
        ok = client.get("/projects/", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200

        wrong = client.get("/projects/", headers={"Authorization": f"Bearer {WRONG_TOKEN}"})
        assert wrong.status_code == 401
        assert WRONG_TOKEN not in wrong.text
        assert TOKEN not in wrong.text


def test_remote_login_sets_secure_http_only_cookie_for_media_range(tmp_path):
    write_media(tmp_path / "media", "video/out.mp4", b"0123456789abcdef")
    with make_remote_client(tmp_path) as client:
        login = client.post(
            "/auth/session",
            json={"token": TOKEN},
            headers={"Origin": "https://studio.example"},
        )
        assert login.status_code == 200, login.text
        assert login.json() == {"auth_required": True, "authenticated": True}
        assert login.headers["cache-control"] == "no-store"
        cookie = login.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "samesite=strict" in cookie.lower()
        assert "Secure" in cookie
        assert "Path=/" in cookie
        assert TOKEN not in cookie

        ranged = client.get("/files/video/out.mp4", headers={"Range": "bytes=0-3"})
        assert ranged.status_code == 206
        assert ranged.headers["cache-control"] == "no-store"
        assert ranged.content == b"0123"
        assert ranged.headers["content-range"].startswith("bytes 0-3/")


def test_cookie_mutation_requires_same_origin_but_bearer_can_omit_origin(tmp_path):
    with make_remote_client(tmp_path) as client:
        login = client.post(
            "/auth/session",
            json={"token": TOKEN},
            headers={"Origin": "https://studio.example"},
        )
        assert login.status_code == 200

        missing_origin = client.post("/config/env", json={})
        assert missing_origin.status_code == 403

        cross_origin = client.post(
            "/config/env",
            json={},
            headers={"Origin": "https://evil.example"},
        )
        assert cross_origin.status_code == 403

        same_origin = client.post(
            "/config/env",
            json={},
            headers={"Origin": "https://studio.example"},
        )
        assert same_origin.status_code == 200, same_origin.text

    with make_remote_client(tmp_path / "bearer") as client:
        bearer = client.post(
            "/config/env",
            json={},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert bearer.status_code == 200, bearer.text


def test_remote_rejects_cross_site_fetch_metadata_for_sensitive_get(tmp_path):
    write_media(tmp_path / "media", "uploads/ref.png", b"image")
    with make_remote_client(tmp_path) as client:
        response = client.get(
            "/files/uploads/ref.png",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert response.status_code == 403
        assert response.content != b"image"


def test_remote_true_cors_preflight_is_public_but_plain_options_is_protected(tmp_path):
    with make_remote_client(tmp_path) as client:
        preflight = client.options(
            "/projects/",
            headers={
                "Origin": "https://studio.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://studio.example"
        assert preflight.headers["access-control-allow-credentials"] == "true"
        assert preflight.headers["access-control-allow-origin"] != "*"

        plain = client.options("/projects/")
        assert plain.status_code == 401


def test_remote_sse_stays_streamable_with_bearer_auth(tmp_path):
    with make_remote_client(tmp_path) as client:
        with client.stream(
            "POST",
            "/projects/script-a/storyboard/refine_batch",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())
        assert "event: progress" in body
        assert "event: done" in body


def test_remote_session_discovery_logout_and_max_session_eviction(tmp_path):
    app = make_app(
        tmp_path,
        access_mode="remote",
        access_token=TOKEN,
        access_allowed_origins=["https://studio.example"],
        access_max_sessions=1,
    )
    with TestClient(app, base_url="https://studio.example") as client:
        anonymous = client.get("/auth/session")
        assert anonymous.status_code == 200
        assert anonymous.json() == {"auth_required": True, "authenticated": False}
        assert anonymous.headers["cache-control"] == "no-store"

        first_login = client.post(
            "/auth/session",
            json={"token": TOKEN},
            headers={"Origin": "https://studio.example"},
        )
        first_session = session_cookie_value(first_login)
        second_login = client.post(
            "/auth/session",
            json={"token": TOKEN},
            headers={"Origin": "https://studio.example"},
        )
        second_session = session_cookie_value(second_login)
        assert first_session != second_session

        client.cookies.clear()
        evicted = client.get(
            "/projects/",
            headers={"Cookie": f"__Host-lumenx_session={first_session}"},
        )
        assert evicted.status_code == 401

        active = client.get(
            "/projects/",
            headers={"Cookie": f"__Host-lumenx_session={second_session}"},
        )
        assert active.status_code == 200

        logout = client.delete(
            "/auth/session",
            headers={
                "Cookie": f"__Host-lumenx_session={second_session}",
                "Origin": "https://studio.example",
            },
        )
        assert logout.status_code == 200
        assert logout.json() == {"auth_required": True, "authenticated": False}
        assert logout.headers["cache-control"] == "no-store"
        assert "Secure" in logout.headers["set-cookie"]
        assert "samesite=strict" in logout.headers["set-cookie"].lower()
        after_logout = client.get(
            "/projects/",
            headers={"Cookie": f"__Host-lumenx_session={second_session}"},
        )
        assert after_logout.status_code == 401


def test_remote_mode_can_be_loaded_from_environment_after_runtime_config(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMENX_ACCESS_MODE", "remote")
    monkeypatch.setenv("LUMENX_ACCESS_TOKEN", TOKEN)
    app = make_app(tmp_path)
    with TestClient(app, base_url="https://studio.example") as client:
        assert client.get("/projects/").status_code == 401
        response = client.get("/projects/", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200


@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/projects/", 401),
    ("POST", "/auth/session", 403),
    ("OPTIONS", "/projects/", 403),
])
def test_malformed_origin_is_rejected_without_server_error(tmp_path, method, path, expected):
    with make_remote_client(tmp_path) as client:
        headers = {"Origin": "http://["}
        if method == "OPTIONS":
            headers["Access-Control-Request-Method"] = "GET"
        response = client.request(method, path, headers=headers)
        assert response.status_code == expected
        assert "access-control-allow-origin" not in response.headers


def test_local_malformed_host_does_not_crash_origin_check(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.get("/auth/session", headers={
            "Origin": "http://localhost:3008", "Host": "[",
        })
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
