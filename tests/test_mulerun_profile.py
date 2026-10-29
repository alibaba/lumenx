import os
import shutil
import subprocess
from pathlib import Path

import pytest

import src.utils.mulerun_profile as mulerun_profile
from src.utils.mulerun_profile import (
    MuleRunLoginSession,
    MuleRunProfileError,
    build_mulerun_profile_env,
    copy_owned_mulerun_profile,
    fingerprint_owned_mulerun_profile,
    mulerun_global_flags,
    snapshot_mulerun_profile_to_temp_home_with_fingerprint,
    mulerun_profile_home,
    snapshot_mulerun_profile_to_temp_home,
)


def test_profile_env_is_sanitized_private_and_disables_auto_upgrade(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", "/ambient/home")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ambient-dashscope")
    monkeypatch.setenv("MULEROUTER_API_KEY", "ambient-router")
    monkeypatch.setenv("PATH", "/bin")

    env = build_mulerun_profile_env(
        {"MULERUN_TOKEN": "owned-token"},
        profile_home=tmp_path / "profile-home",
        token_required=True,
    )

    assert env["HOME"] == str(tmp_path / "profile-home")
    assert env["XDG_CONFIG_HOME"].startswith(env["HOME"])
    assert env["MULERUN_TOKEN"] == "owned-token"
    assert env["MULERUN_DISABLE_AUTO_UPGRADE"] == "1"
    assert env["MULERUN_SKIP_AUTO_UPGRADE"] == "1"
    assert env["NO_UPDATE_NOTIFIER"] == "1"
    assert env["PATH"] == "/bin"
    assert "DASHSCOPE_API_KEY" not in env
    assert "MULEROUTER_API_KEY" not in env


def test_global_flags_are_explicit_and_do_not_parse_profile():
    flags = mulerun_global_flags({"MULERUN_BASE_URL": "https://owned.example/", "MULERUN_SITE": "cn"})

    assert flags == ["--base-url", "https://owned.example", "--site", "cn"]


def test_mulerun_profile_home_preserves_symlink_path_for_later_rejection(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    assert mulerun_profile_home({"LUMENX_MULERUN_HOME": str(linked)}) == linked


def test_snapshot_copies_only_mulerun_tree_and_never_writes_back(tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    (source / "opaque-token.json").write_text("v1")
    (profile_home / ".other").write_text("do not copy")

    with snapshot_mulerun_profile_to_temp_home(profile_home) as temp_home:
        assert (temp_home / ".mulerun" / "opaque-token.json").read_text() == "v1"
        assert not (temp_home / ".other").exists()
        (temp_home / ".mulerun" / "opaque-token.json").write_text("changed")

    assert (source / "opaque-token.json").read_text() == "v1"


def test_profile_copy_rejects_symlink_components_before_copy(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(MuleRunProfileError, match="symlink"):
        copy_owned_mulerun_profile(linked, tmp_path / "target")


def test_profile_copy_rejects_symlink_inside_mulerun_tree(tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    (source / "token").symlink_to(tmp_path / "outside-token")

    with pytest.raises(MuleRunProfileError, match="symlink"):
        copy_owned_mulerun_profile(profile_home, tmp_path / "target")


def test_profile_copy_error_preserves_original_failure_without_double_close(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    (source / "token").write_text("owned-token")

    def fail_copy(*args, **kwargs):
        raise OSError("synthetic copy failure")

    monkeypatch.setattr(mulerun_profile.shutil, "copyfileobj", fail_copy)

    with pytest.raises(MuleRunProfileError) as raised:
        copy_owned_mulerun_profile(profile_home, tmp_path / "target")

    assert "Bad file descriptor" not in str(raised.value)
    assert isinstance(raised.value.__cause__, OSError)
    assert "synthetic copy failure" in str(raised.value.__cause__)


def test_profile_copy_enforces_actual_bytes_after_validation(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    token = source / "token"
    token.write_text("ok")
    original_require = mulerun_profile._require_safe_profile_tree

    def grow_after_validation(home):
        checked_source = original_require(home)
        token.write_text("this grew after validation")
        return checked_source

    monkeypatch.setattr(mulerun_profile, "_MAX_PROFILE_BYTES", 4)
    monkeypatch.setattr(mulerun_profile, "_require_safe_profile_tree", grow_after_validation)

    with pytest.raises(MuleRunProfileError, match="too large"):
        copy_owned_mulerun_profile(profile_home, tmp_path / "target")


def test_profile_copy_enforces_actual_file_count_after_validation(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    (source / "one").write_text("1")
    original_require = mulerun_profile._require_safe_profile_tree

    def add_file_after_validation(home):
        checked_source = original_require(home)
        (source / "two").write_text("2")
        return checked_source

    monkeypatch.setattr(mulerun_profile, "_MAX_PROFILE_FILES", 1)
    monkeypatch.setattr(mulerun_profile, "_require_safe_profile_tree", add_file_after_validation)

    with pytest.raises(MuleRunProfileError, match="too many files"):
        copy_owned_mulerun_profile(profile_home, tmp_path / "target")


def test_profile_copy_rejects_parent_symlink_swap_during_file_open(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    nested = source / "nested"
    nested.mkdir(parents=True)
    token = nested / "token"
    token.write_text("owned-token")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "token").write_text("outside-token")
    original_open = mulerun_profile.os.open
    swapped = False

    def swap_parent_before_token_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        path_text = os.fspath(path)
        should_swap = (dir_fd is not None and path_text == "token") or Path(path_text) == token
        if should_swap and not swapped:
            swapped = True
            shutil.rmtree(nested)
            nested.symlink_to(outside, target_is_directory=True)
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(mulerun_profile.os, "open", swap_parent_before_token_open)

    with pytest.raises(MuleRunProfileError, match="symlink|changed|Cannot copy|disappeared"):
        copy_owned_mulerun_profile(profile_home, tmp_path / "target")


def test_profile_root_parent_swap_cannot_copy_another_profile(monkeypatch, tmp_path):
    profile = tmp_path / "owned"
    (profile / ".mulerun").mkdir(parents=True)
    (profile / ".mulerun/token").write_text("owned")
    other = tmp_path / "other"
    (other / ".mulerun").mkdir(parents=True)
    (other / ".mulerun/token").write_text("other-account")
    original = mulerun_profile._open_source_dir_no_follow
    def swap_before_open(path):
        profile.rename(tmp_path / "previous-owned")
        profile.symlink_to(other, target_is_directory=True)
        return original(path)
    monkeypatch.setattr(mulerun_profile, "_open_source_dir_no_follow", swap_before_open)
    with pytest.raises(MuleRunProfileError):
        copy_owned_mulerun_profile(profile, tmp_path / "target")
    assert not (tmp_path / "target/.mulerun/token").exists()


def test_profile_fingerprint_is_stable_sorted_and_secret_free(tmp_path, monkeypatch):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    (source / "nested").mkdir(parents=True)
    (source / "z-token").write_bytes(b"secret-z")
    (source / "nested" / "a-token").write_bytes(b"secret-a")
    monkeypatch.setenv("MULERUN_TOKEN", "ambient-token")
    monkeypatch.setenv("MULEROUTER_API_KEY", "ambient-router")
    monkeypatch.setenv("HOME", str(tmp_path / "ambient-home"))

    first = fingerprint_owned_mulerun_profile(profile_home)
    second = fingerprint_owned_mulerun_profile(profile_home)

    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)
    assert "secret" not in first
    assert str(profile_home) not in first


def test_profile_fingerprint_changes_with_profile_bytes_and_paths(tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    token = source / "token"
    token.write_bytes(b"v1")
    original = fingerprint_owned_mulerun_profile(profile_home)

    token.write_bytes(b"v2")
    changed_bytes = fingerprint_owned_mulerun_profile(profile_home)
    token.rename(source / "renamed-token")
    changed_path = fingerprint_owned_mulerun_profile(profile_home)

    assert changed_bytes != original
    assert changed_path != changed_bytes


def test_empty_profile_fingerprint_is_deterministic(tmp_path):
    profile_home = tmp_path / "profile"
    (profile_home / ".mulerun").mkdir(parents=True)

    assert fingerprint_owned_mulerun_profile(profile_home) == fingerprint_owned_mulerun_profile(profile_home)


def test_profile_fingerprint_reuses_copy_safety_checks(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    (source / "token").write_text("owned-token")

    monkeypatch.setattr(mulerun_profile, "_MAX_PROFILE_BYTES", 4)

    with pytest.raises(MuleRunProfileError, match="too large"):
        fingerprint_owned_mulerun_profile(profile_home)


def test_profile_fingerprint_rejects_symlinked_profile_tree(tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    (source / "token").symlink_to(tmp_path / "outside-token")

    with pytest.raises(MuleRunProfileError, match="symlink"):
        fingerprint_owned_mulerun_profile(profile_home)


def test_login_session_start_rejects_symlink_profile_home_before_popen(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    calls = []

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("popen must not run for a symlinked profile root")

    session = MuleRunLoginSession(linked, {}, popen_factory=fake_popen)

    with pytest.raises(MuleRunProfileError, match="symlink"):
        session.start()
    assert calls == []


def test_status_missing_profile_has_no_side_effects_or_cli_call(tmp_path):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return type("Result", (), {"returncode": 0})()

    session = MuleRunLoginSession(tmp_path / "missing", {}, run_factory=fake_run)

    assert session.status() is False
    assert calls == []
    assert not (tmp_path / "missing").exists()


def test_login_session_start_uses_devnull_and_close_owns_child(tmp_path):
    captured = {}

    class FakeProcess:
        def __init__(self):
            self.terminated = False
            self.killed = False
            self.waits = 0

        def poll(self):
            return None if not self.terminated else 0

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            self.waits += 1
            return 0

        def kill(self):
            self.killed = True

    process = FakeProcess()

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return process

    session = MuleRunLoginSession(
        tmp_path / "profile",
        {"MULERUN_BASE_URL": "https://owned.example", "MULERUN_SITE": "cn"},
        popen_factory=fake_popen,
    )

    assert session.start() is True
    assert session.start() is False
    assert captured["kwargs"]["stdout"] is subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is subprocess.DEVNULL
    assert captured["cmd"] == ["mulerun", "--base-url", "https://owned.example", "--site", "cn", "login"]
    session.close()
    assert process.terminated is True
    assert process.killed is False
    assert process.waits == 1
    session.close()


def test_login_session_copies_env_immutably(tmp_path):
    captured = {}
    env = {"MULERUN_BASE_URL": "https://before.example"}

    class DoneProcess:
        def poll(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return DoneProcess()

    session = MuleRunLoginSession(tmp_path / "profile", env, popen_factory=fake_popen)
    env["MULERUN_BASE_URL"] = "https://after.example"

    assert session.start() is True
    assert captured["cmd"][:3] == ["mulerun", "--base-url", "https://before.example"]


def test_global_flags_use_known_cn_site_base_and_explicit_api_base():
    assert mulerun_global_flags({"MULERUN_SITE": "cn"}) == [
        "--base-url",
        "https://api.xiaoluozi.cn",
        "--site",
        "cn",
    ]
    assert mulerun_global_flags({"MULERUN_SITE": "cn", "MULERUN_API_BASE_URL": "https://custom.example/"}) == [
        "--base-url",
        "https://custom.example",
        "--site",
        "cn",
    ]


def test_global_flags_reject_unknown_site_before_cli():
    with pytest.raises(MuleRunProfileError, match="Unsupported MuleRun site"):
        mulerun_global_flags({"MULERUN_SITE": "moon"})


def test_snapshot_with_fingerprint_reuses_copied_tree_and_returns_same_digest(tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    (source / "token").write_text("secret-v1", encoding="utf-8")

    with snapshot_mulerun_profile_to_temp_home_with_fingerprint(profile_home) as snapshot:
        (source / "token").write_text("secret-v2", encoding="utf-8")
        assert (snapshot.home / ".mulerun" / "token").read_text(encoding="utf-8") == "secret-v1"
        assert snapshot.fingerprint == fingerprint_owned_mulerun_profile(snapshot.home)


def test_snapshot_with_fingerprint_rejects_unsafe_profile_with_existing_checks(tmp_path):
    profile_home = tmp_path / "profile"
    source = profile_home / ".mulerun"
    source.mkdir(parents=True)
    (source / "token").symlink_to(tmp_path / "outside")

    with pytest.raises(MuleRunProfileError, match="symlink"):
        with snapshot_mulerun_profile_to_temp_home_with_fingerprint(profile_home):
            pass
