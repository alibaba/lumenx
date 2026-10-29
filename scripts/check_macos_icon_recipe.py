#!/usr/bin/env python3
"""Check four real-shell icon scenarios using only owned files and fake converters.

Run independently with Python -I -B -S and an explicit new --tmp-root.
This is a trusted build-recipe fixture, not an OS sandbox or a product test runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("src-tauri/icons/icon.png")
TARGET = Path("src-tauri/icons/icon.icns")
SIZES = {
    "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
}
FAKE_TOOL = r'''
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["ICON_TEST_ROOT"])
def owned(value):
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("Fake converter path escaped the owned case")
    return path

tool, *args = sys.argv[1:]
with owned(os.environ["ICON_TEST_LOG"]).open("a") as stream:
    stream.write(json.dumps({"tool": tool, "args": args}) + "\n")
if tool == "sips":
    assert len(args) == 6 and args[0] == "-z" and args[4] == "--out"
    source, target = owned(args[3]), owned(args[5])
    target.write_bytes(source.read_bytes() + str(args[1:3]).encode())
else:
    assert tool == "iconutil" and len(args) == 5 and args[:2] == ["-c", "icns"] and args[3] == "-o"
    images = {image.name: owned(image).read_bytes().hex() for image in owned(args[2]).glob("*.png")}
    owned(args[4]).write_bytes(b"fake-icns:" + json.dumps(images, sort_keys=True).encode())
if os.environ.get("ICON_TEST_FAIL") == tool:
    raise SystemExit(17)
'''


def _prepare_case(case: Path, script_bytes: bytes) -> dict:
    case.mkdir()
    source = case / "owned source with spaces"
    source.mkdir()
    script = source / "create_icon.sh"
    script.write_bytes(script_bytes)
    files = {
        SOURCE: b"current 1024px desktop brand",
        TARGET: b"previous complete canonical icon",
        Path("frontend/public/LumenX.png"): b"obsolete brand must not be read",
        Path("icon.icns"): b"legacy root icon must not be overwritten",
        Path("LumenX.iconset/sentinel"): b"unrelated pre-existing iconset",
    }
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    fake_bin = case / "fake bin"
    fake_bin.mkdir()
    helper = case / "fake_icon_tool.py"
    helper.write_text(FAKE_TOOL)
    for tool in ("sips", "iconutil"):
        command = fake_bin / tool
        command.write_text(
            '#!/bin/bash\nexec "$ICON_TEST_PYTHON" -I -B -S "$ICON_TEST_HELPER" '
            + tool + ' "$@"\n'
        )
        command.chmod(0o755)
    # No ambient PATH: image tools can only resolve to the fake converters.
    for tool in ("mkdir", "rm", "mv", "mktemp", "dirname"):
        executable = next((directory / tool for directory in (Path("/bin"), Path("/usr/bin"))
                           if (directory / tool).is_file()), None)
        if executable is None:
            raise ValueError(f"Required basic shell tool is missing: {tool}")
        (fake_bin / tool).symlink_to(executable)
    temporary, elsewhere, home = case / "temporary", case / "unrelated cwd", case / "home"
    for path in (temporary, elsewhere, home):
        path.mkdir()
    return {
        "case": case, "source": source, "script": script, "elsewhere": elsewhere,
        "temporary": temporary, "log": case / "tool-calls.jsonl",
        "env": {
            "PATH": str(fake_bin), "HOME": str(home), "TMPDIR": str(temporary), "LC_ALL": "C",
            "ICON_TEST_PYTHON": sys.executable, "ICON_TEST_HELPER": str(helper),
            "ICON_TEST_LOG": str(case / "tool-calls.jsonl"), "ICON_TEST_ROOT": str(case),
        },
    }


def _tree(root: Path) -> dict:
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Recipe created an unapproved symlink")
        result[path.relative_to(root).as_posix()] = path.read_bytes() if path.is_file() else None
    return result


def _run(recipe: dict, *, foreign_cwd: bool = False, failure: str | None = None):
    env = dict(recipe["env"])
    if failure:
        env["ICON_TEST_FAIL"] = failure
    command = ["/bin/bash", str(recipe["script"])]
    process = subprocess.Popen(
        command, cwd=recipe["elsewhere"] if foreign_cwd else recipe["source"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    timed_out = False
    interrupted = None
    try:
        stdout, stderr = process.communicate(timeout=15)
    except BaseException as exc:
        timed_out = isinstance(exc, subprocess.TimeoutExpired)
        if not timed_out:
            interrupted = exc
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate(timeout=5)
    (recipe["case"] / "stdout.log").write_text(stdout)
    (recipe["case"] / "stderr.log").write_text(stderr)
    (recipe["case"] / "process.json").write_text(json.dumps({
        "argv": command, "exit_code": process.returncode, "timed_out": timed_out,
        "owned_process_group": process.pid,
    }, indent=2) + "\n")
    if interrupted is not None:
        raise interrupted
    if timed_out:
        raise TimeoutError("Owned shell exceeded 15 seconds; its process group was terminated")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _current_brand_from_foreign_cwd(recipe: dict) -> None:
    source = recipe["source"]
    before = _tree(source)
    result = _run(recipe, foreign_cwd=True)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in recipe["log"].read_text().splitlines()]
    assert [call["tool"] for call in calls] == ["sips"] * 10 + ["iconutil"]
    assert {Path(call["args"][5]).name: int(call["args"][1]) for call in calls[:-1]} == SIZES
    assert all(call["args"][1] == call["args"][2] for call in calls[:-1])
    assert all(Path(call["args"][3]).resolve() == source / SOURCE for call in calls[:-1])
    target = (source / TARGET).read_bytes()
    assert target.startswith(b"fake-icns:")
    images = json.loads(target.removeprefix(b"fake-icns:"))
    assert set(images) == set(SIZES)
    assert all(bytes.fromhex(image).startswith(before[SOURCE.as_posix()]) for image in images.values())
    after = _tree(source)
    after[TARGET.as_posix()] = before[TARGET.as_posix()]
    assert after == before
    assert not any(recipe["temporary"].iterdir())
    assert not any(recipe["elsewhere"].iterdir())


def _missing_current_png(recipe: dict) -> None:
    (recipe["source"] / SOURCE).unlink()
    before = _tree(recipe["source"])
    result = _run(recipe)
    assert result.returncode != 0
    assert "Success" not in result.stdout
    assert not recipe["log"].exists()
    assert _tree(recipe["source"]) == before
    assert not any(recipe["temporary"].iterdir())


def _converter_failure(recipe: dict, failure: str) -> None:
    before = _tree(recipe["source"])
    result = _run(recipe, failure=failure)
    assert result.returncode != 0
    assert "Success" not in result.stdout
    calls = [json.loads(line) for line in recipe["log"].read_text().splitlines()]
    expected = ["sips"] if failure == "sips" else ["sips"] * 10 + ["iconutil"]
    assert [call["tool"] for call in calls] == expected
    assert _tree(recipe["source"]) == before
    assert not any(recipe["temporary"].iterdir())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tmp-root", required=True, type=Path)
    args = parser.parse_args(argv)
    if os.environ.get("LUMENX_TEST_ISOLATED") == "1":
        raise ValueError("Run this checker independently, never from the guarded product test runner")
    if sys.flags.optimize:
        raise ValueError("Assertions must be enabled for this checker")
    root = args.tmp_root
    if not root.is_absolute() or root.resolve() != root or root.exists() or not root.parent.is_dir():
        raise ValueError("--tmp-root must be a new absolute non-symlink directory with an existing parent")
    for protected in (ROOT, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()):
        if root.is_relative_to(protected) or protected.is_relative_to(root):
            raise ValueError("--tmp-root must not overlap the repository or Python environment")
    script = ROOT / "create_icon.sh"
    if script.resolve() != script or not script.is_file():
        raise ValueError("The recipe must be a regular repository file")
    script_bytes = script.read_bytes()
    script_text = script_bytes.decode("utf-8")
    if re.search(r"(?:^|[;\s])(?:export\s+)?PATH\s*\+?=", script_text):
        raise ValueError("Recipe PATH changes are not allowed in the fake-tool checker")
    if re.search(r"/(?:sips|iconutil)(?:\s|[\"'])", script_text):
        raise ValueError("Recipe must use PATH-resolved fake converters, not absolute converter paths")
    root.mkdir()
    cases = (
        ("foreign-cwd-current-brand", _current_brand_from_foreign_cwd),
        ("missing-current-source", _missing_current_png),
        ("sips-failure", lambda recipe: _converter_failure(recipe, "sips")),
        ("iconutil-failure", lambda recipe: _converter_failure(recipe, "iconutil")),
    )
    results = []
    for name, check in cases:
        try:
            check(_prepare_case(root / name, script_bytes))
            row = {"name": name, "passed": True}
        except Exception as exc:
            row = {"name": name, "passed": False, "error": f"{type(exc).__name__}: {exc}"}
        results.append(row)
        print(json.dumps(row), flush=True)
    stable = script.read_bytes() == script_bytes
    report = {
        "recipe_sha256": hashlib.sha256(script_bytes).hexdigest(), "recipe_unchanged": stable,
        "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "cases": results,
        "passed": sum(item["passed"] for item in results),
        "scope": "Real shell with synthetic files and fake converters; no application, SDK or real image conversion",
    }
    (root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if stable and all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f"Checker stopped: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
