"""Bound MuleRun CLI authority for GPT image execution uses persisted identity."""

import json
import subprocess
import tempfile
from pathlib import Path
from threading import RLock

import pytest

from src.models import mulerouter
from src.models.mulerouter import MuleRouterImageModel
from src.models.provider_binding import MuleRunCliRouteIdentity, ProviderCredentialStore
from src.models.provider_tasks import ProviderScope
from src.utils.json_storage import DirectoryWriterLock

TOKEN = "mrtok_" + "s" * 40


def routes():
    return MuleRunCliRouteIdentity(
        schema_version=1,
        login_site="cn",
        login_base_url="https://login.example",
        studio_site="mulerouter",
        studio_base_url="https://studio.example",
    )


def token_scope(route):
    return ProviderScope(
        provider="mulerouter",
        backend="mulerun_cli",
        gateway="mulerun_token",
        base_url=route.studio_base_url,
        region=None,
        workspace_id=None,
        account_identity=None,
    )


def profile_scope(route):
    return ProviderScope(
        provider="mulerouter",
        backend="mulerun_cli",
        gateway="mulerun_profile",
        base_url=route.studio_base_url,
        region=None,
        workspace_id=None,
        account_identity=None,
    )


def bind_token(tmp_path, route):
    with DirectoryWriterLock(tmp_path / "store") as lease:
        store = ProviderCredentialStore(tmp_path / "store", writer_lock=lease, runtime_lock=RLock())
        return store.bind_mulerun_token(token_scope(route), TOKEN, routes=route)


def bind_profile(tmp_path, route):
    source = tmp_path / "profile-source"
    (source / ".mulerun").mkdir(parents=True)
    (source / ".mulerun" / "session.json").write_text("opaque-profile", encoding="utf-8")
    lease = DirectoryWriterLock(tmp_path / "profile-store").acquire()
    store = ProviderCredentialStore(tmp_path / "profile-store", writer_lock=lease, runtime_lock=RLock())
    context = store.bind_mulerun_profile(profile_scope(route), source, routes=route)
    credential = context.__enter__()
    return lease, context, credential


def fake_completed(url="https://files.example/result.png"):
    return subprocess.CompletedProcess(
        ["mulerun"],
        0,
        stdout=json.dumps({"output": {"image_url": url}}),
        stderr="",
    )


@pytest.mark.parametrize(
    "custom_temp_root", [False, True], ids=["default-temp-root", "custom-temp-root"]
)
def test_bound_token_image_uses_original_routes_private_home_and_no_dynamic_backend(
    monkeypatch, tmp_path, custom_temp_root
):
    if custom_temp_root:
        temporary_root = tmp_path / "token-tmp"
        temporary_root.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(temporary_root))

    route = routes()
    credential = bind_token(tmp_path, route)
    calls = []
    downloads = []

    def fake_run(cmd, **kwargs):
        cli_home = Path(kwargs["env"]["HOME"]).resolve()
        assert cli_home.is_relative_to(Path(tempfile.gettempdir()).resolve())
        assert cli_home != Path.home().resolve()
        assert cli_home.is_dir()
        calls.append((cmd, kwargs))
        return fake_completed()

    def fake_download(url, output_path):
        downloads.append((url, output_path))
        return output_path

    model = MuleRouterImageModel(
        {"output_root": str(tmp_path / "output")},
        env={
            "MULERUN_TOKEN": "ambient-token-must-not-win",
            "MULEROUTER_BASE_URL": "https://ambient-router.example",
            "MULEROUTER_SITE": "mulerun",
        },
    )
    monkeypatch.setattr(model, "_use_cli_backend", lambda: pytest.fail("dynamic backend selection must not run"))
    monkeypatch.setattr(mulerouter.subprocess, "run", fake_run)
    monkeypatch.setattr(mulerouter, "_download_file", fake_download)

    output, elapsed = model.generate_with_bound_cli("draw a cat", str(tmp_path / "out.png"), credential, size="2048*2048")

    assert output == str(tmp_path / "out.png")
    assert elapsed >= 0
    assert downloads == [("https://files.example/result.png", str(tmp_path / "out.png"))]
    cmd, kwargs = calls[0]
    assert cmd[:9] == [
        "mulerun",
        "--token",
        TOKEN,
        "--base-url",
        "https://login.example",
        "--site",
        "cn",
        "studio",
        "run",
    ]
    assert "openai/gpt-image-2/generation" in cmd
    assert cmd[cmd.index("--base-url", 7) + 1] == "https://studio.example"
    assert cmd[cmd.index("--site", 7) + 1] == "mulerouter"
    assert "ambient-token-must-not-win" not in cmd
    assert kwargs["env"]["MULERUN_TOKEN"] == TOKEN
    cli_home = Path(kwargs["env"]["HOME"])
    assert not cli_home.exists()
    assert not cli_home.parent.exists()
    assert kwargs["env"].get("MULEROUTER_API_KEY") is None


def test_bound_profile_image_uses_exact_snapshot_home_without_copy_or_token(monkeypatch, tmp_path):
    route = routes()
    lease, context, credential = bind_profile(tmp_path, route)
    calls = []
    try:
        def fail_copy(*_args, **_kwargs):
            raise AssertionError("bound profile execution must not copy mutable source profile")

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return fake_completed("https://files.example/profile.png")

        model = MuleRouterImageModel({"output_root": str(tmp_path / "output")}, env={"MULERUN_TOKEN": "ambient-token"})
        monkeypatch.setattr(mulerouter, "snapshot_mulerun_profile_to_temp_home", fail_copy)
        monkeypatch.setattr(mulerouter.subprocess, "run", fake_run)
        monkeypatch.setattr(mulerouter, "_download_file", lambda url, output_path: output_path)

        model.generate_with_bound_cli("edit", str(tmp_path / "profile.png"), credential, ref_image_paths=[])

        cmd, kwargs = calls[0]
        assert "--token" not in cmd
        assert TOKEN not in cmd
        assert kwargs["env"]["HOME"] == str(credential.profile_home)
        assert "MULERUN_TOKEN" not in kwargs["env"]
        assert (credential.profile_home / ".mulerun" / "session.json").read_text(encoding="utf-8") == "opaque-profile"
        assert "openai/gpt-image-2/generation" in cmd
    finally:
        context.__exit__(None, None, None)
        lease.close()



def test_expired_bound_profile_snapshot_fails_without_subprocess_or_home_rebuild(monkeypatch, tmp_path):
    route = routes()
    lease, context, credential = bind_profile(tmp_path, route)
    home = credential.profile_home
    context.__exit__(None, None, None)
    lease.close()
    assert not home.exists()

    def fail_run(*_args, **_kwargs):
        raise AssertionError("expired bound profile snapshot must not launch subprocess")

    model = MuleRouterImageModel({"output_root": str(tmp_path / "output")}, env={"MULERUN_TOKEN": "ambient-token"})
    monkeypatch.setattr(mulerouter.subprocess, "run", fail_run)
    monkeypatch.setattr(mulerouter, "_download_file", lambda *_args, **_kwargs: pytest.fail("must not download"))

    with pytest.raises(RuntimeError, match="MuleRun profile snapshot"):
        model.generate_with_bound_cli("draw", str(tmp_path / "out.png"), credential)

    assert not home.exists()


def test_empty_bound_profile_snapshot_fails_before_subprocess(monkeypatch, tmp_path):
    route = routes()
    lease, context, credential = bind_profile(tmp_path, route)
    try:
        for path in sorted((credential.profile_home / ".mulerun").rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()

        def fail_run(*_args, **_kwargs):
            raise AssertionError("empty bound profile snapshot must not launch subprocess")

        model = MuleRouterImageModel({"output_root": str(tmp_path / "output")}, env={})
        monkeypatch.setattr(mulerouter.subprocess, "run", fail_run)
        with pytest.raises(RuntimeError, match="MuleRun profile snapshot"):
            model.generate_with_bound_cli("draw", str(tmp_path / "out.png"), credential)
    finally:
        context.__exit__(None, None, None)
        lease.close()


def test_symlinked_bound_profile_snapshot_fails_before_subprocess(monkeypatch, tmp_path):
    route = routes()
    lease, context, credential = bind_profile(tmp_path, route)
    try:
        session = credential.profile_home / ".mulerun" / "session.json"
        session.unlink()
        session.symlink_to(tmp_path / "outside-secret")

        def fail_run(*_args, **_kwargs):
            raise AssertionError("unsafe bound profile snapshot must not launch subprocess")

        model = MuleRouterImageModel({"output_root": str(tmp_path / "output")}, env={})
        monkeypatch.setattr(mulerouter.subprocess, "run", fail_run)
        with pytest.raises(RuntimeError, match="MuleRun profile snapshot"):
            model.generate_with_bound_cli("draw", str(tmp_path / "out.png"), credential)
    finally:
        context.__exit__(None, None, None)
        lease.close()

def test_bound_cli_failure_redacts_token(monkeypatch, tmp_path, caplog):
    route = routes()
    credential = bind_token(tmp_path, route)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"bad token {TOKEN}")

    model = MuleRouterImageModel({"output_root": str(tmp_path / "output")}, env={})
    monkeypatch.setattr(mulerouter.subprocess, "run", fake_run)

    with caplog.at_level("INFO"):
        with pytest.raises(RuntimeError) as raised:
            model.generate_with_bound_cli("draw", str(tmp_path / "out.png"), credential)

    assert TOKEN not in str(raised.value)
    assert TOKEN not in caplog.text
    assert "<redacted>" in caplog.text



def test_bound_token_non_json_stdout_redacts_token(monkeypatch, tmp_path):
    route = routes()
    credential = bind_token(tmp_path, route)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"not json leaked {TOKEN}", stderr="")

    model = MuleRouterImageModel({"output_root": str(tmp_path / "output")}, env={})
    monkeypatch.setattr(mulerouter.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as raised:
        model.generate_with_bound_cli("draw", str(tmp_path / "out.png"), credential)

    assert TOKEN not in str(raised.value)
    assert "<redacted>" in str(raised.value)


def test_legacy_token_non_json_stdout_redacts_token(monkeypatch, tmp_path):
    legacy_token = "mrtok_" + "l" * 40

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"legacy leaked {legacy_token}", stderr="")

    model = MuleRouterImageModel({"output_root": str(tmp_path / "output")}, env={"MULERUN_TOKEN": legacy_token})
    monkeypatch.setattr(mulerouter.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as raised:
        model._generate_via_cli("draw", str(tmp_path / "out.png"))

    assert legacy_token not in str(raised.value)
    assert "<redacted>" in str(raised.value)

def test_legacy_generate_path_still_uses_http_when_dynamic_backend_false(monkeypatch, tmp_path):
    model = MuleRouterImageModel({"output_root": str(tmp_path / "output")}, env={"MULEROUTER_API_KEY": "http-key"})
    monkeypatch.setattr(model, "_use_cli_backend", lambda: False)
    called = {"http": 0}

    def fake_http(prompt, output_path, **kwargs):
        called["http"] += 1
        return output_path, 0.0

    monkeypatch.setattr(model, "_generate_via_http", fake_http)

    assert model.generate("draw", str(tmp_path / "out.png")) == (str(tmp_path / "out.png"), 0.0)
    assert called == {"http": 1}
