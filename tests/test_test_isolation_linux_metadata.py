"""Linux SDK metadata reads must not weaken the isolated runner's file boundary."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


def _probe(tmp_path, source):
    script = tmp_path / "linux_metadata_probe.py"
    script.write_text(dedent(source), encoding="utf-8")
    helper = Path(os.environ["LUMENX_REPO_ROOT"]) / "scripts/test_support/isolated_child.py"
    return subprocess.run(
        [sys.executable, "-S", str(helper), str(script)],
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("candidate", ["/etc/os-release", "/usr/lib/os-release"])
def test_linux_sdk_headers_can_read_public_os_metadata_with_a_cold_cache(tmp_path, candidate):
    # Run the real SDK and stdlib parser on every host. Only the Linux metadata
    # bytes and Ubuntu's /etc/os-release symlink are simulated; the guard is live.
    result = _probe(tmp_path, f"""
        import io
        import platform
        import sys
        from pathlib import Path
        from openai._base_client import platform_headers
        import isolation_guard

        real_open = isolation_guard._REAL_OPEN
        real_resolve = isolation_guard._resolve_path
        reads = []

        def linux_resolve(path):
            if str(path) == "/etc/os-release":
                return Path("/usr/lib/os-release")
            return real_resolve(path)

        def metadata_open(path, *args, **kwargs):
            if str(path) == {candidate!r}:
                reads.append(str(path))
                return io.StringIO('NAME="Ubuntu"\\nID=ubuntu\\nVERSION_ID="24.04"\\n')
            return real_open(path, *args, **kwargs)

        isolation_guard._resolve_path = linux_resolve
        isolation_guard._REAL_OPEN = metadata_open
        sys.platform = "linux"
        platform.system = lambda: "Linux"
        platform.platform = lambda: "Linux-test"
        platform._os_release_candidates = ({candidate!r},)
        platform._os_release_cache = None
        platform_headers.cache_clear()
        assert platform_headers("test", platform=None)["X-Stainless-OS"] == "Linux"
        assert platform.freedesktop_os_release()["ID"] == "ubuntu"
        assert reads == [{candidate!r}]
        assert isolation_guard.violations() == ()
        print("linux-sdk-metadata-ok")
    """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "linux-sdk-metadata-ok"


@pytest.mark.parametrize("path,mode", [
    ("/usr/lib/os-release", "w"),
    ("/usr/lib/os-release", "a"),
    ("/usr/lib/os-release", "r+"),
    ("/usr/lib/os-release.backup", "r"),
    ("/usr/lib/os-release/private.json", "r"),
    ("/etc/private-settings.json", "r"),
])
def test_linux_metadata_exception_does_not_allow_writes_or_other_files(tmp_path, path, mode):
    result = _probe(tmp_path, f"""
        import sys
        import isolation_guard

        def unexpected_open(*args, **kwargs):
            raise AssertionError("forbidden file reached the OS")

        isolation_guard._REAL_OPEN = unexpected_open
        sys.platform = "linux"
        try:
            open({path!r}, {mode!r})
        except RuntimeError:
            pass
        # A caught violation must still fail the process; never clear its ledger.
    """)
    assert result.returncode == 97, result.stderr
    assert "LUMENX_TEST_ISOLATION_VIOLATION" in result.stderr
    assert "outside" in result.stderr
    assert "forbidden file reached the OS" not in result.stderr


def test_linux_metadata_symlink_cannot_authorize_a_private_target(tmp_path):
    result = _probe(tmp_path, """
        import os
        import sys
        from pathlib import Path
        import isolation_guard

        real_resolve = isolation_guard._resolve_path
        def private_target(path):
            if str(path) == "/usr/lib/os-release":
                return Path(os.environ["LUMENX_TEST_SENTINEL_FILE"])
            return real_resolve(path)

        isolation_guard._resolve_path = private_target
        sys.platform = "linux"
        try:
            open("/usr/lib/os-release")
        except RuntimeError:
            pass
    """)
    assert result.returncode == 97, result.stderr
    assert "blocked read outside allowed roots" in result.stderr


def test_linux_metadata_exception_is_not_enabled_on_other_platforms(tmp_path):
    result = _probe(tmp_path, """
        import sys
        import isolation_guard

        sys.platform = "darwin"
        try:
            open("/usr/lib/os-release")
        except RuntimeError:
            pass
    """)
    assert result.returncode == 97, result.stderr
    assert "blocked read outside allowed roots" in result.stderr
