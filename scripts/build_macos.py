#!/usr/bin/env python3
"""Build prepared desktop inputs without installing or cleaning shared files."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys


APP_NAME = "LumenX Studio"
APP_BUNDLE_ID = "com.lumenx.studio"
MIN_FREE_BYTES = 4 * 1024**3
STATIC_SUFFIXES = {
    ".html", ".js", ".css", ".json", ".map", ".txt", ".png", ".jpg", ".jpeg",
    ".webp", ".gif", ".svg", ".ico", ".avif", ".mp4", ".webm", ".mp3", ".wav",
    ".woff", ".woff2", ".ttf", ".otf",
}
HIDDEN_IMPORTS = (
    "src.apps.comic_gen.api", "uvicorn.logging", "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on", "webview", "dashscope", "oss2",
    "alibabacloud_videoenhan20200320", "alibabacloud_tea_openapi",
    "alibabacloud_tea_util", "yaml", "dotenv", "httptools", "uvloop", "multipart",
)


def _absolute(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"Input must be an absolute path: {value}")
    if ":" in value:
        raise ValueError("Paths must not contain PyInstaller's ':' data separator")
    return path.resolve()


def _file(path: Path) -> Path:
    if path.resolve() != path:
        raise ValueError(f"Unapproved resource path: {path}")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Required regular file is missing or a symlink: {path}")
    return path


def _snapshot_paths(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "repo_local_snapshot_paths":
                if not isinstance(child, list) or not all(isinstance(p, str) for p in child):
                    raise ValueError("Invalid catalog snapshot paths")
                result.update(child)
            else:
                result.update(_snapshot_paths(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_snapshot_paths(child))
    return result


def dependency_versions(source: Path) -> dict[str, str | None]:
    # openai is already used by BOTH LLM providers, despite the legacy comment
    # in requirements.txt. Detect its absence; never silently install it.
    names = {"pyinstaller", "openai"}
    for line in _file(source / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~]+[A-Za-z0-9.*+-]+)?", line)
        if not match:
            raise ValueError(f"Review unsupported dependency declaration before packaging: {line}")
        names.add(match.group(1))
    result: dict[str, str | None] = {}
    for name in sorted(names):
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def resource_files(source: Path, static: Path) -> list[tuple[Path, str]]:
    """Enumerate exact data files; never include src, output or a user's config wholesale."""
    index = _file(static / "index.html")
    if "/static/" not in index.read_text(encoding="utf-8"):
        raise ValueError("Static shell must use the desktop /static/ base path")
    files: list[tuple[Path, str]] = []
    for path in sorted(static.rglob("*")):
        relative = path.relative_to(static)
        if path.is_symlink() or any(
            part.startswith(".") or part.lower() in {"output", "config.json", "credentials.json"}
            for part in relative.parts
        ):
            raise ValueError(f"Unapproved static resource: {relative}")
        if path.is_dir():
            continue
        if path.suffix.lower() not in STATIC_SUFFIXES:
            raise ValueError(f"Unapproved static resource: {relative}")
        files.append((_file(path), str(Path("static") / relative.parent)))

    catalog = source / "config/model_catalog"
    required = [catalog / "catalog.meta.yaml", catalog / "schema/model-catalog.schema.json",
                catalog / "generated/model_catalog.json"]
    families = sorted((catalog / "families").glob("*.yaml"))
    if not families:
        raise ValueError("Catalog families are missing")
    required.extend(families)
    generated = json.loads(_file(required[2]).read_text(encoding="utf-8"))
    for snapshot in sorted(_snapshot_paths(generated)):
        rel = Path(snapshot)
        if rel.is_absolute() or ".." in rel.parts or rel.parts[:2] != ("docs", "1-api-reference") or rel.suffix != ".md":
            raise ValueError(f"Unapproved catalog snapshot: {snapshot}")
        required.append(source / rel)
    required.append(source / "src/apps/comic_gen/style_presets.json")
    for path in required:
        # Reject symlinked parents too, not just the leaf file.
        if path.resolve() != path or not path.is_relative_to(source):
            raise ValueError(f"Unapproved resource path: {path}")
        files.append((_file(path), str(path.parent.relative_to(source))))
    return files


def _input_hashes(source: Path, files: list[tuple[Path, str]], media: list[Path]) -> dict[str, str]:
    inputs = [source / "main.py", source / "requirements.txt", Path(__file__).resolve(), *media,
              *(path for path, _ in files), *sorted((source / "src").rglob("*.py")),
              *sorted((source / ".pyinstaller-hooks").glob("*.py")),
              source / "src-tauri/icons/icon.icns", source / "src-tauri/tauri.conf.json"]
    return {str(path): hashlib.sha256(_file(path).read_bytes()).hexdigest() for path in inputs}


def _content_digest(domain: str, files: dict[str, str]) -> str:
    content = {"schema": 1, "domain": domain, "files": files}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _spec_text(plan: dict) -> str:
    """Preserve the existing onedir/windowed recipe; supply identity before BUNDLE signs."""
    source = Path(plan["source"])
    icon = str(source / "src-tauri/icons/icon.icns")
    data_files = [(row["source"], row["destination"]) for row in plan["data_files"]]
    binaries = [(path, ".") for path in plan["media"]]
    info = {
        "CFBundleVersion": plan["version"],
        "LumenXBuild": {
            "SourceInputSHA256": plan["source_input_sha256"],
            "StaticShellSHA256": plan["static_shell_sha256"],
        },
    }
    preamble = [
        "from PyInstaller.utils.hooks import collect_all",
        f"datas = {data_files!r}", f"binaries = {binaries!r}", f"hiddenimports = {list(HIDDEN_IMPORTS)!r}",
    ]
    for name in ("uvicorn", "fastapi", "starlette", "pydantic"):
        preamble.extend([f"collected = collect_all({name!r})", "datas += collected[0]",
                         "binaries += collected[1]", "hiddenimports += collected[2]"])
    return "\n".join(preamble) + f"""

a = Analysis(
    {[str(source / 'main.py')]!r},
    pathex={[str(source)]!r},
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath={[str(source / '.pyinstaller-hooks')]!r},
    hooksconfig={{}}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True, name={APP_NAME!r}, debug=False,
    bootloader_ignore_signals=False, strip=False, upx=True, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon={[icon]!r},
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=True, upx_exclude=[], name={APP_NAME!r},
)
app = BUNDLE(
    coll, name={f'{APP_NAME}.app'!r}, icon={icon!r},
    bundle_identifier={APP_BUNDLE_ID!r}, version={plan['version']!r},
    info_plist={info!r},
)
"""


def build_plan(args: argparse.Namespace) -> dict:
    if sys.version_info < (3, 11):
        raise ValueError("The selected Python must be 3.11 or newer")
    source, static = _absolute(args.source), _absolute(args.static)
    output = _absolute(args.output)
    if Path(args.output).is_symlink() or output.exists() or not output.parent.is_dir():
        raise ValueError("Output must be a new directory with an existing parent")
    dependency_roots = [Path(path).resolve().parents[2] for path in sys.path
                        if path and Path(path).name == "site-packages" and len(Path(path).parents) >= 3]
    protected = (source, static, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), *dependency_roots)
    if any(output.is_relative_to(path) or path.is_relative_to(output) for path in protected):
        raise ValueError("Output must be outside source, static and Python environments")
    for rel in ("main.py", "requirements.txt", ".pyinstaller-hooks/hook-dashscope.py", ".pyinstaller-hooks/hook-oss2.py"):
        _file(source / rel)
    media = [_absolute(args.ffmpeg), _absolute(args.ffprobe)]
    if media[0] == media[1] or any(not path.is_file() or not os.access(path, os.X_OK) for path in media):
        raise ValueError("FFmpeg and FFprobe must be distinct executable files")
    if media[0].name != "ffmpeg" or media[1].name != "ffprobe":
        raise ValueError("Media executables must retain the names ffmpeg and ffprobe")
    files = resource_files(source, static)
    inputs = _input_hashes(source, files, media)
    declaration = json.loads(_file(source / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    version = declaration.get("version") if isinstance(declaration, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Desktop version must be three numeric components in src-tauri/tauri.conf.json")
    source_files, static_files = {}, {}
    separate_inputs = {Path(__file__).resolve(), *media}
    for filename, digest in inputs.items():
        path = Path(filename)
        if path in separate_inputs:
            continue
        # Static inputs may live inside source, but still have their own identity.
        if path.is_relative_to(static):
            static_files[path.relative_to(static).as_posix()] = digest
        elif path.is_relative_to(source):
            source_files[path.relative_to(source).as_posix()] = digest
    spec = output / "spec" / f"{APP_NAME}.spec"
    command = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm",
               "--distpath", str(output / "dist"), "--workpath", str(output / "work"), str(spec)]
    plan = {
        "source": str(source), "static": str(static), "output": str(output),
        "python": sys.executable, "media": [str(path) for path in media],
        "dependencies": dependency_versions(source),
        "data_files": [{"source": str(path), "destination": dest} for path, dest in files],
        "input_sha256": inputs, "version": version,
        "source_input_sha256": _content_digest("source-inputs", source_files),
        "static_shell_sha256": _content_digest("static-shell", static_files),
        "spec": str(spec),
        "pyinstaller": command,
        "app": str(output / "package" / f"{APP_NAME}.app"),
        "dmg": ["/usr/bin/hdiutil", "create", "-volname", APP_NAME, "-srcfolder", str(output / "package"),
                "-format", "UDZO", str(output / f"{APP_NAME}.dmg")] if args.dmg else None,
        "limitations": "Plan is not a build, dependency-closure proof, signed release or desktop acceptance.",
    }
    plan["spec_text"] = _spec_text(plan)
    return plan


def _build_environment(output: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in {
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ", "SYSTEMROOT",
    }}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYINSTALLER_CONFIG_DIR": str(output / "cache"),
                "PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR": "1",
                "XDG_CACHE_HOME": str(output / "cache"), "MPLCONFIGDIR": str(output / "cache/matplotlib"),
                "TORCH_HOME": str(output / "cache/torch"), "HF_HOME": str(output / "cache/huggingface"),
                "LUMENX_DATA_DIR": str(output / "analysis-data")})
    return env


def execute_plan(plan: dict) -> None:
    if sys.platform != "darwin":
        raise ValueError("macOS packaging requires macOS")
    output = Path(plan["output"])
    dependencies = dependency_versions(Path(plan["source"]))
    missing = [name for name, version in dependencies.items() if version is None]
    if missing:
        raise ValueError(f"Required build/runtime dependencies missing: {', '.join(missing)}; no packages were installed")
    if dependencies != plan["dependencies"]:
        raise ValueError("Dependency versions changed since planning")
    if shutil.disk_usage(output.parent).free < MIN_FREE_BYTES:
        raise ValueError("Build requires at least 4 GiB free; this is a floor, not a size guarantee")
    _verify_inputs(plan)
    output.mkdir()  # Exclusive ownership; never overwrite or delete an existing build.
    env = _build_environment(output)
    (output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    versions = []
    for executable, name in zip(plan["media"], ("ffmpeg", "ffprobe")):
        result = subprocess.run([executable, "-version"], cwd=output, env=env,
                                capture_output=True, text=True, check=True, timeout=15)
        (output / f"{name}-version.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
        match = re.match(rf"{name} version (\S+)", result.stdout)
        if not match:
            raise ValueError(f"Cannot identify {name} version")
        versions.append(match.group(1))
    if versions[0] != versions[1]:
        raise ValueError("FFmpeg and FFprobe versions must match")
    _verify_inputs(plan)
    spec = Path(plan["spec"])
    spec.parent.mkdir()
    spec.write_text(plan["spec_text"], encoding="utf-8")
    with (output / "pyinstaller.log").open("w", encoding="utf-8") as log:
        subprocess.run(plan["pyinstaller"], cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    _verify_inputs(plan)
    app = output / "dist" / f"{APP_NAME}.app"
    if not app.is_dir() or app.is_symlink():
        raise ValueError("PyInstaller did not produce the expected application")
    _verify_app(plan, app)
    (output / "package").mkdir()
    app.rename(plan["app"])  # Move the app, do not copy it into multiple staging trees.
    if plan["dmg"]:
        app_bytes = sum(path.stat().st_size for path in Path(plan["app"]).rglob("*") if path.is_file())
        if shutil.disk_usage(output).free < app_bytes * 2 + MIN_FREE_BYTES:
            raise ValueError("Insufficient space for DMG; the application and logs are retained")
        with (output / "hdiutil.log").open("w", encoding="utf-8") as log:
            subprocess.run(plan["dmg"], cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)


def _verify_inputs(plan: dict) -> None:
    source, static = Path(plan["source"]), Path(plan["static"])
    actual = _input_hashes(source, resource_files(source, static), [Path(path) for path in plan["media"]])
    if actual.keys() != plan["input_sha256"].keys():
        raise ValueError("Input changed since planning: declared file set differs")
    for path, expected in plan["input_sha256"].items():
        if actual[path] != expected:
            raise ValueError(f"Input changed since planning: {path}")


def _verify_app(plan: dict, app: Path) -> None:
    """Read signed output only; a mismatching bundle stays in dist for diagnosis."""
    try:
        info = plistlib.loads(_file(app / "Contents/Info.plist").read_bytes())
    except Exception as exc:
        raise ValueError("Application Info.plist is missing or invalid") from exc
    expected = {
        "CFBundleIdentifier": APP_BUNDLE_ID,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIconFile": "icon.icns",
        "CFBundleShortVersionString": plan["version"],
        "CFBundleVersion": plan["version"],
        "LumenXBuild": {
            "SourceInputSHA256": plan["source_input_sha256"],
            "StaticShellSHA256": plan["static_shell_sha256"],
        },
    }
    if not isinstance(info, dict) or any(info.get(key) != value for key, value in expected.items()):
        raise ValueError("Application identity does not match the build plan")
    icon = _file(app / "Contents/Resources/icon.icns")
    expected_icon = plan["input_sha256"][str(Path(plan["source"]) / "src-tauri/icons/icon.icns")]
    if hashlib.sha256(icon.read_bytes()).hexdigest() != expected_icon:
        raise ValueError("Application icon does not match the build plan")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "static", "ffmpeg", "ffprobe", "output"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--plan", action="store_true", help="Validate and print inputs without running tools or writing outputs")
    parser.add_argument("--dmg", action="store_true", help="Also create a DMG after building the app")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args)
        if args.plan:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            execute_plan(plan)
            print(f"Application: {plan['app']}")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Build stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
