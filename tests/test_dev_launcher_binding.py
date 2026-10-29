"""Developer backend launchers must default to loopback-only binding."""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _js_spawn_argv(script: str) -> list[str]:
    match = re.search(r"spawn\(pythonPath,\s*(\[[\s\S]*?\])\s*,", script)
    assert match, "start-backend.js must spawn pythonPath with a literal argv array"
    return ast.literal_eval(match.group(1))


def _shell_python_argv(script: str) -> list[str]:
    commands = [line.strip() for line in script.splitlines() if line.strip().startswith("python ")]
    assert commands, "start_backend.sh must launch python explicitly"
    assert len(commands) == 1, "start_backend.sh should have one backend launch command"
    return shlex.split(commands[0])


def _host_after_flag(argv: list[str]) -> str:
    assert "--host" in argv, f"launcher argv missing --host: {argv!r}"
    index = argv.index("--host")
    assert index + 1 < len(argv), f"launcher argv has --host without value: {argv!r}"
    return argv[index + 1]


def test_node_dev_launcher_binds_backend_to_loopback_by_default():
    script = (ROOT / "scripts" / "start-backend.js").read_text(encoding="utf-8")
    argv = _js_spawn_argv(script)

    assert argv[:3] == ["-m", "uvicorn", "src.apps.comic_gen.api:app"]
    assert "--reload" in argv
    assert argv[argv.index("--port") + 1] == "17177"
    assert _host_after_flag(argv) == "127.0.0.1"
    assert "0.0.0.0" not in argv
    assert "NO_PROXY: '*.aliyuncs.com,localhost,127.0.0.1'" in script


def test_shell_dev_launcher_binds_backend_to_loopback_by_default():
    script = (ROOT / "start_backend.sh").read_text(encoding="utf-8")
    argv = _shell_python_argv(script)

    assert argv[:3] == ["python", "-m", "uvicorn"]
    assert "src.apps.comic_gen.api:app" in argv
    assert "--reload" in argv
    assert argv[argv.index("--port") + 1] == "17177"
    assert _host_after_flag(argv) == "127.0.0.1"
    assert "0.0.0.0" not in argv
    assert 'NO_PROXY="*.aliyuncs.com,localhost,127.0.0.1"' in script
