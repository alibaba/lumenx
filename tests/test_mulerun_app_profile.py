"""MuleRun browser authentication belongs to one application environment."""

from pathlib import Path

from tests.test_app_environment_isolation import application, settings


def install_session(monkeypatch):
    sessions = []

    class Session:
        def __init__(self, *, profile_home, env, **kwargs):
            self.home = Path(profile_home)
            self.env = dict(env)
            self.started = 0
            self.closed = False
            self.logged_in = False
            sessions.append(self)

        def start(self):
            self.started += 1
            return True

        def status(self):
            return self.logged_in

        def close(self):
            self.closed = True

    monkeypatch.setattr("src.utils.mulerun_profile.MuleRunLoginSession", Session)
    monkeypatch.setattr("shutil.which", lambda name: "/synthetic/mulerun" if name == "mulerun" else None)
    # A regression to the old ambient Popen must fail before any real process.
    def forbidden(*args, **kwargs):
        raise AssertionError("ambient login subprocess")
    monkeypatch.setattr("subprocess.Popen", forbidden)
    return sessions


def test_login_and_status_use_one_owned_session_and_close_it(tmp_path, monkeypatch):
    sessions = install_session(monkeypatch)
    config = {**settings("first"), "LUMENX_MULERUN_HOME": str(tmp_path / "untrusted")}
    monkeypatch.setenv("LUMENX_MULERUN_HOME", str(tmp_path / "ambient"))
    with application(tmp_path, "first", config) as (app, client):
        result = client.post("/config/mulerun-login")
        assert result.status_code == 200, result.text
        assert len(sessions) == 1
        session = sessions[0]
        assert session.home.is_relative_to(tmp_path / "first" / "user" / ".runtime")
        assert session.env["LUMENX_MULERUN_HOME"] == str(session.home)
        assert session.started == 1
        assert client.get("/config/env").json()["MULERUN_CLI_LOGGED_IN"] is False
        session.logged_in = True
        response = client.get("/config/env")
        assert response.json()["MULERUN_CLI_LOGGED_IN"] is True
        assert "LUMENX_MULERUN_HOME" not in response.json()
        assert str(session.home) not in response.text
        assert not session.closed
        assert client.post("/config/mulerun-login").status_code == 200
        assert len(sessions) == 1
    assert session.closed
    assert not hasattr(app.state, "mulerun_login_session")


def test_login_a_never_marks_b_logged_in(tmp_path, monkeypatch):
    sessions = install_session(monkeypatch)
    with application(tmp_path, "first", settings("first")) as (_, first):
        with application(tmp_path, "second", settings("second")) as (_, second):
            assert first.post("/config/mulerun-login").status_code == 200
            sessions[0].logged_in = True
            assert first.get("/config/env").json()["MULERUN_CLI_LOGGED_IN"] is True
            assert second.get("/config/env").json()["MULERUN_CLI_LOGGED_IN"] is False
            assert len(sessions) == 2
            assert sessions[0].home != sessions[1].home
            assert sessions[0].env["DASHSCOPE_API_KEY"] == "example-first"
            assert sessions[1].env["DASHSCOPE_API_KEY"] == "example-second"
        assert sessions[1].closed
        assert not sessions[0].closed


def test_profile_scope_cannot_be_changed_by_provider_settings(tmp_path, monkeypatch):
    sessions = install_session(monkeypatch)
    with application(tmp_path, "first", settings("first")) as (app, client):
        assert client.post("/config/mulerun-login").status_code == 200
        original_session = sessions[0]
        original = app.state.runtime_environment.snapshot()["LUMENX_MULERUN_HOME"]
        assert client.post("/config/env", json={"LUMENX_MULERUN_HOME": str(tmp_path / "outside"), "DASHSCOPE_API_KEY": "changed"}).status_code == 200
        assert app.state.runtime_environment.snapshot()["LUMENX_MULERUN_HOME"] == original
        assert client.post("/config/mulerun-login").status_code == 200
        assert app.state.mulerun_login_session is original_session
        assert len(sessions) == 1
        saved = (tmp_path / "first" / "user" / "config.json").read_text()
        assert "LUMENX_MULERUN_HOME" not in saved


def test_login_cleanup_failure_still_closes_owned_pipeline(tmp_path, monkeypatch):
    install_session(monkeypatch)
    with application(tmp_path, "first", settings("first")) as (app, _):
        pipeline = app.state.pipeline
        def fail():
            raise OSError("synthetic process cleanup failure")
        monkeypatch.setattr(app.state.mulerun_login_session, "close", fail)
    assert pipeline._closed
    assert not hasattr(app.state, "mulerun_login_session")
