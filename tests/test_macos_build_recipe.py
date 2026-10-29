"""Real filesystem contracts for the packaging recipe; external builders are fakes."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
from types import SimpleNamespace

import pytest

from scripts import build_macos as build


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "docs/1-api-reference/catalog-snapshot.md"
BRAND_ICON = "src-tauri/icons/icon.icns"
DESKTOP_CONFIG = "src-tauri/tauri.conf.json"


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(build.metadata, "version", lambda _: "6.22.3")
    source = tmp_path / "fixed source"
    static = tmp_path / "verified static"
    files = {
        "main.py": "# not executed by recipe tests\n",
        "requirements.txt": "fastapi>=0.115.0\n# openai is required by existing runtime\n",
        ".pyinstaller-hooks/hook-dashscope.py": "",
        ".pyinstaller-hooks/hook-oss2.py": "",
        "src/apps/comic_gen/style_presets.json": "{}",
        "config/model_catalog/catalog.meta.yaml": "version: 1\n",
        "config/model_catalog/families/wan.yaml": "models: []\n",
        "config/model_catalog/schema/model-catalog.schema.json": "{}",
        "config/model_catalog/generated/model_catalog.json": json.dumps({"docs": {"repo_local_snapshot_paths": [SNAPSHOT]}}),
        SNAPSHOT: "# Frozen catalog reference\n",
        ".env": "PRIVATE_TOKEN=not-for-bundle",
        "output/projects.json": "private project",
        "config.json": "personal settings",
        BRAND_ICON: "current desktop brand icon",
        DESKTOP_CONFIG: json.dumps({"version": "1.2.1"}),
        "icon.icns": "legacy brand icon; not the packaging input",
    }
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    static.mkdir()
    (static / "index.html").write_text('<script src="/static/_next/app.js"></script>')
    (static / "_next").mkdir()
    (static / "_next/app.js").write_text("window.example = true;")
    for name in ("ffmpeg", "ffprobe"):
        path = tmp_path / name
        path.write_text("not executed")
        path.chmod(0o755)
    return argparse.Namespace(source=str(source), static=str(static), ffmpeg=str(tmp_path / "ffmpeg"),
                              ffprobe=str(tmp_path / "ffprobe"), output=str(tmp_path / "owned build"), dmg=True)


def test_plan_lists_only_runtime_resources_and_explicit_tools(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    resources = {Path(row["source"]).relative_to(inputs.source).as_posix() for row in plan["data_files"]
                 if Path(row["source"]).is_relative_to(inputs.source)}
    assert resources == {
        "config/model_catalog/catalog.meta.yaml", "config/model_catalog/families/wan.yaml",
        "config/model_catalog/schema/model-catalog.schema.json", "config/model_catalog/generated/model_catalog.json",
        SNAPSHOT, "src/apps/comic_gen/style_presets.json",
    }
    assert not Path(inputs.output).exists()
    command = plan["pyinstaller"]
    assert command[:4] == [build.sys.executable, "-B", "-m", "PyInstaller"]
    assert command[command.index("--workpath") + 1] == str(Path(inputs.output) / "work")
    assert Path(command[-1]).parent == Path(inputs.output) / "spec"
    assert "--clean" not in command
    records = []
    _fake_tools(monkeypatch, plan, spec_records=records)
    build.execute_plan(plan)
    analysis = records[0][0]["Analysis"]
    assert analysis.kwargs["binaries"][:2] == [(inputs.ffmpeg, "."), (inputs.ffprobe, ".")]
    assert analysis.kwargs["hookspath"] == [str(Path(inputs.source) / ".pyinstaller-hooks")]


def test_display_name_cannot_become_the_macos_bundle_identity(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    records = []
    _fake_tools(monkeypatch, plan, spec_records=records)
    build.execute_plan(plan)
    nodes = records[0][0]
    assert nodes["EXE"].kwargs["name"] == "LumenX Studio"
    assert nodes["BUNDLE"].kwargs["name"] == "LumenX Studio.app"
    identifier = nodes["BUNDLE"].kwargs["bundle_identifier"]
    assert re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", identifier)
    assert identifier == "com.lumenx.studio"


def test_packaging_uses_the_current_shared_brand_icon(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    icon = Path(inputs.source) / BRAND_ICON
    assert plan["input_sha256"][str(icon)] == hashlib.sha256(icon.read_bytes()).hexdigest()
    assert str(Path(inputs.source) / "icon.icns") not in plan["input_sha256"]
    assert not Path(inputs.output).exists()
    records = []
    _fake_tools(monkeypatch, plan, spec_records=records)
    build.execute_plan(plan)
    nodes = records[0][0]
    assert nodes["EXE"].kwargs["icon"] == [str(icon)]
    assert nodes["BUNDLE"].kwargs["icon"] == str(icon)


def test_missing_current_icon_does_not_fall_back_to_the_legacy_icon(inputs):
    (Path(inputs.source) / BRAND_ICON).unlink()
    assert (Path(inputs.source) / "icon.icns").is_file()
    with pytest.raises(ValueError, match="Required regular file"):
        build.build_plan(inputs)
    assert not Path(inputs.output).exists()


@pytest.mark.parametrize("location", ["file", "parent"])
def test_current_icon_cannot_be_an_external_resource(inputs, tmp_path, location):
    icon = Path(inputs.source) / BRAND_ICON
    if location == "file":
        external = tmp_path / "external.icns"
        icon.rename(external)
        icon.symlink_to(external)
    else:
        external = tmp_path / "external icons"
        icon.parent.rename(external)
        icon.parent.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="resource path|Required regular file"):
        build.build_plan(inputs)
    assert not Path(inputs.output).exists()


def test_changed_current_icon_cannot_use_an_earlier_plan(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    (Path(inputs.source) / BRAND_ICON).write_text("different current brand bytes")
    calls = _fake_tools(monkeypatch, plan)
    with pytest.raises(ValueError, match="Input changed"):
        build.execute_plan(plan)
    assert not calls
    assert not Path(inputs.output).exists()


@pytest.mark.parametrize("missing", ["main.py", "config/model_catalog/catalog.meta.yaml", SNAPSHOT,
                                      "src/apps/comic_gen/style_presets.json", ".pyinstaller-hooks/hook-oss2.py"])
def test_missing_required_resource_fails_before_output(inputs, missing):
    (Path(inputs.source) / missing).unlink()
    with pytest.raises(ValueError, match="Required regular file"):
        build.build_plan(inputs)
    assert not Path(inputs.output).exists()


@pytest.mark.parametrize("field", ["ffmpeg", "ffprobe"])
def test_missing_media_executable_fails_before_output(inputs, field):
    Path(getattr(inputs, field)).unlink()
    with pytest.raises(ValueError, match="executable"):
        build.build_plan(inputs)
    assert not Path(inputs.output).exists()


@pytest.mark.parametrize("relative", [".env", "config.json", "output/project.json", "private.py"])
def test_static_sensitive_or_unapproved_resources_are_rejected(inputs, relative):
    path = Path(inputs.static) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("never bundle")
    with pytest.raises(ValueError, match="Unapproved static"):
        build.build_plan(inputs)


def test_static_symlink_directory_is_not_silently_skipped(inputs, tmp_path):
    secret = tmp_path / "private"
    secret.mkdir()
    (Path(inputs.static) / "linked").symlink_to(secret, target_is_directory=True)
    with pytest.raises(ValueError, match="Unapproved static"):
        build.build_plan(inputs)


@pytest.mark.parametrize("reference", ["../outside.md", "/tmp/outside.md", "docs/other/file.md", "docs/1-api-reference/file.json"])
def test_catalog_cannot_add_arbitrary_local_files(inputs, reference):
    generated = Path(inputs.source) / "config/model_catalog/generated/model_catalog.json"
    generated.write_text(json.dumps({"docs": {"repo_local_snapshot_paths": [reference]}}))
    with pytest.raises(ValueError, match="Unapproved catalog snapshot"):
        build.build_plan(inputs)


@pytest.mark.parametrize("location", ["existing", "source", "static", "parent", "symlink"])
def test_output_collision_or_input_overlap_is_rejected_without_deleting(inputs, tmp_path, location):
    if location == "existing":
        target = Path(inputs.output)
        target.mkdir()
        (target / "sentinel").write_text("keep")
    elif location == "source":
        target = Path(inputs.source) / "new build"
    elif location == "static":
        target = Path(inputs.static) / "new build"
    elif location == "parent":
        target = tmp_path
    else:
        target = tmp_path / "alias"
        target.symlink_to(Path(inputs.source) / "new build")
    inputs.output = str(target)
    with pytest.raises(ValueError, match="Output"):
        build.build_plan(inputs)
    if location == "existing":
        assert (target / "sentinel").read_text() == "keep"


def test_plan_mode_runs_no_tools_or_writes(inputs, monkeypatch, capsys):
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **k: pytest.fail("plan must not run tools"))
    args = [part for field in ("source", "static", "ffmpeg", "ffprobe", "output")
            for part in (f"--{field}", getattr(inputs, field))]
    assert build.main([*args, "--plan", "--dmg"]) == 0
    assert json.loads(capsys.readouterr().out)["output"] == inputs.output
    assert not Path(inputs.output).exists()


def _record_generated_spec(command, output):
    """Evaluate the real recipe output with only builder constructors replaced."""
    spec = Path(command[-1])
    assert spec.parent == output / "spec" and spec.suffix == ".spec"
    assert spec.is_file() and not spec.is_symlink()
    assert command == [
        build.sys.executable, "-B", "-m", "PyInstaller", "--noconfirm",
        "--distpath", str(output / "dist"), "--workpath", str(output / "work"), str(spec),
    ]
    records, events, handles = {}, [], {}

    def collect_all(name):
        events.append(("collect_all", name))
        return [(f"{name}.data", name)], [(f"{name}.binary", name)], [f"{name}.hidden"]

    def import_hooks(name, _globals=None, _locals=None, fromlist=(), level=0):
        assert (name, tuple(fromlist), level) == ("PyInstaller.utils.hooks", ("collect_all",), 0)
        return SimpleNamespace(collect_all=collect_all)

    def constructor(name):
        def record(*args, **kwargs):
            assert name not in records, f"Duplicate {name} in generated spec"
            events.append(("construct", name))
            # Freeze mutable inputs now, while prior builder results remain opaque
            # identity-bearing handles. A fresh memo cannot retain mutable aliases
            # from a previous constructor's snapshot.
            saved_args, saved_kwargs = deepcopy((args, kwargs), memo=dict(handles))
            node = SimpleNamespace(args=saved_args, kwargs=saved_kwargs)
            if name == "Analysis":
                node.pure, node.scripts, node.binaries, node.datas = object(), object(), object(), object()
                for value in (node.pure, node.scripts, node.binaries, node.datas):
                    handles[id(value)] = value
            records[name] = node
            handles[id(node)] = node
            return node
        return record

    namespace: dict[str, object] = {name: constructor(name) for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")}
    namespace["__builtins__"] = {"__import__": import_hooks}
    exec(compile(spec.read_text(encoding="utf-8"), str(spec), "exec"), namespace)
    assert list(records) == ["Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE"]
    return records, events


def _fake_tools(monkeypatch, plan, *, failure=None, probe_version="8.1", spec_records=None):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "-version":
            name = Path(command[0]).name
            version = probe_version if name == "ffprobe" else "8.1"
            return subprocess.CompletedProcess(command, 0, f"{name} version {version}\n", "")
        if "PyInstaller" in command:
            if failure == "builder":
                raise subprocess.CalledProcessError(17, command)
            app = Path(plan["output"]) / "dist" / f"{build.APP_NAME}.app"
            app.mkdir(parents=True)
            (app / "fake-builder-artifact").write_text("test only")
            if Path(command[-1]).suffix == ".spec":
                records, events = _record_generated_spec(command, Path(plan["output"]))
                if spec_records is not None:
                    spec_records.append((records, events))
                bundle = records["BUNDLE"].kwargs
                assert bundle["name"] == app.name
                icon = Path(bundle["icon"])
                resources = app / "Contents/Resources"
                resources.mkdir(parents=True)
                (resources / icon.name).write_bytes(icon.read_bytes())
                # Mirrors only the relevant PyInstaller BUNDLE defaults/merge.
                # Identity comes from the actual spec, never from expected plan values.
                info = {
                    "CFBundleIdentifier": bundle.get("bundle_identifier", build.APP_NAME),
                    "CFBundleExecutable": records["EXE"].kwargs["name"],
                    "CFBundleIconFile": icon.name,
                    "CFBundleShortVersionString": bundle.get("version", "0.0.0"),
                    "NSHighResolutionCapable": not records["EXE"].kwargs["console"],
                }
                info.update(bundle.get("info_plist") or {})
                (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
                records["BUNDLE"].plist_bytes = (app / "Contents/Info.plist").read_bytes()
        else:
            assert command[0] == "/usr/bin/hdiutil"
            Path(command[-1]).write_text("fake dmg")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build.subprocess, "run", run)
    monkeypatch.setattr(build.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 20 * 1024**3})())
    monkeypatch.setattr(build.sys, "platform", "darwin")
    return calls


def test_build_moves_one_app_and_only_uses_owned_output(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    calls = _fake_tools(monkeypatch, plan)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-analysis")
    build.execute_plan(plan)
    assert [Path(c[0][0]).name for c in calls[:2]] == ["ffmpeg", "ffprobe"]
    assert len(calls) == 4
    assert Path(plan["app"]).is_dir()
    assert not (Path(inputs.output) / "dist" / f"{build.APP_NAME}.app").exists()
    assert calls[-1][0] == plan["dmg"]
    assert "detach" not in calls[-1][0] and "-ov" not in calls[-1][0]
    for _, kwargs in calls:
        assert kwargs["cwd"] == Path(inputs.output)
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert kwargs["env"]["PYINSTALLER_CONFIG_DIR"].startswith(inputs.output + "/")
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert (Path(inputs.source) / ".env").read_text() == "PRIVATE_TOKEN=not-for-bundle"


def test_signing_failure_is_not_downgraded_to_a_packaged_success(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    _fake_tools(monkeypatch, plan)
    run_tool = build.subprocess.run

    def fail_bundle_signing(command, **kwargs):
        if "PyInstaller" in command:
            # PyInstaller only turns a bundle-signing error into build failure
            # when this environment setting is enabled.
            if kwargs["env"].get("PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR") == "1":
                raise subprocess.CalledProcessError(1, command)
        return run_tool(command, **kwargs)

    monkeypatch.setattr(build.subprocess, "run", fail_bundle_signing)
    with pytest.raises(subprocess.CalledProcessError):
        build.execute_plan(plan)
    assert not Path(plan["app"]).exists()
    assert not Path(plan["dmg"][-1]).exists()


def test_tool_version_mismatch_stops_before_builder(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    calls = _fake_tools(monkeypatch, plan, probe_version="7.1")
    with pytest.raises(ValueError, match="versions must match"):
        build.execute_plan(plan)
    assert len(calls) == 2
    assert (Path(inputs.output) / "ffprobe-version.txt").exists()
    assert not Path(plan["app"]).exists()


def test_missing_runtime_dependency_is_visible_and_blocks_without_install(inputs, monkeypatch):
    def version(name):
        if name == "openai":
            raise build.metadata.PackageNotFoundError(name)
        return "6.22.3"

    monkeypatch.setattr(build.metadata, "version", version)
    plan = build.build_plan(inputs)
    assert plan["dependencies"]["openai"] is None
    calls = _fake_tools(monkeypatch, plan)
    with pytest.raises(ValueError, match="dependencies missing: openai"):
        build.execute_plan(plan)
    assert not calls
    assert not Path(inputs.output).exists()


def test_builder_failure_retains_evidence_and_skips_dmg(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    calls = _fake_tools(monkeypatch, plan, failure="builder")
    with pytest.raises(subprocess.CalledProcessError):
        build.execute_plan(plan)
    assert len(calls) == 3
    assert (Path(inputs.output) / "plan.json").exists()
    assert (Path(inputs.output) / "pyinstaller.log").exists()
    assert not Path(plan["app"]).exists()


def test_app_only_never_invokes_dmg_tool(inputs, monkeypatch):
    inputs.dmg = False
    plan = build.build_plan(inputs)
    calls = _fake_tools(monkeypatch, plan)
    build.execute_plan(plan)
    assert len(calls) == 3
    assert Path(plan["app"]).is_dir()
    assert not (Path(inputs.output) / "hdiutil.log").exists()


def test_disk_pressure_after_build_retains_app_and_skips_dmg(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    calls = _fake_tools(monkeypatch, plan)
    free_space = iter((20 * 1024**3, 1))
    monkeypatch.setattr(build.shutil, "disk_usage", lambda _: type("Usage", (), {"free": next(free_space)})())
    with pytest.raises(ValueError, match="application and logs are retained"):
        build.execute_plan(plan)
    assert len(calls) == 3
    assert Path(plan["app"]).is_dir()
    assert (Path(inputs.output) / "pyinstaller.log").exists()
    assert not (Path(inputs.output) / "hdiutil.log").exists()


def test_low_space_fails_before_creating_output(inputs, monkeypatch):
    plan = build.build_plan(inputs)
    calls = _fake_tools(monkeypatch, plan)
    monkeypatch.setattr(build.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 1})())
    with pytest.raises(ValueError, match="4 GiB"):
        build.execute_plan(plan)
    assert not Path(inputs.output).exists()
    assert not calls


def test_readonly_shared_dependency_environment_is_not_an_output_root(inputs, tmp_path, monkeypatch):
    shared = tmp_path / "shared venv"
    site = shared / "lib/python3.11/site-packages"
    site.mkdir(parents=True)
    monkeypatch.setattr(build.sys, "path", [*build.sys.path, str(site)])
    inputs.output = str(shared / "new build")
    with pytest.raises(ValueError, match="Output"):
        build.build_plan(inputs)
    assert not Path(inputs.output).exists()


def test_changed_python_source_cannot_use_an_earlier_plan(inputs, monkeypatch):
    module = Path(inputs.source) / "src/runtime.py"
    module.write_text("VERSION = 1\n")
    plan = build.build_plan(inputs)
    module.write_text("VERSION = 2\n")
    calls = _fake_tools(monkeypatch, plan)
    with pytest.raises(ValueError, match="Input changed"):
        build.execute_plan(plan)
    assert not calls
    assert not Path(inputs.output).exists()


def test_external_hook_directory_is_rejected(inputs, tmp_path):
    hooks = Path(inputs.source) / ".pyinstaller-hooks"
    moved = tmp_path / "unrelated hooks"
    hooks.rename(moved)
    hooks.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="resource path"):
        build.build_plan(inputs)


def test_real_runtime_catalog_snapshot_references_are_bundled(inputs):
    # The runtime builds YAML defaults, so generated JSON alone must not hide
    # a future stale reference. This is a real catalog load in the isolated runner.
    from src.utils.model_catalog import build_catalog_dict

    runtime = build_catalog_dict(ROOT / "config/model_catalog")
    resources = {str(path.relative_to(ROOT)) for path, _ in build.resource_files(ROOT, Path(inputs.static))
                 if path.is_relative_to(ROOT)}
    assert build._snapshot_paths(runtime) <= resources
    assert "docs/1-api-reference/aliyun-wan3-shared-execution-2026-10-12.md" in resources


def test_retired_template_cannot_collect_personal_environment():
    template = (ROOT / "build.spec.template").read_text()
    with pytest.raises(RuntimeError, match="Retired"):
        exec(compile(template, "build.spec.template", "exec"), {})


@pytest.mark.parametrize("version", ["1.2.1", "2.4.0"])
def test_plan_identity_uses_declared_desktop_version_and_separate_content_digests(inputs, version):
    declaration = Path(inputs.source) / DESKTOP_CONFIG
    declaration.write_text(json.dumps({"version": version}))
    plan = build.build_plan(inputs)
    assert plan["version"] == version
    for field in ("source_input_sha256", "static_shell_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", plan[field])
    assert plan["source_input_sha256"] != plan["static_shell_sha256"]
    assert plan["input_sha256"][str(declaration)] == hashlib.sha256(declaration.read_bytes()).hexdigest()
    assert str(declaration) not in {row["source"] for row in plan["data_files"]}
    assert not Path(inputs.output).exists()


@pytest.mark.parametrize("version", [None, True, 121, "", "1.2", "1.2.1-beta", "1.2.1.4", "missing"])
def test_invalid_desktop_version_cannot_fall_back_to_an_unidentified_app(inputs, version):
    declaration = Path(inputs.source) / DESKTOP_CONFIG
    if version == "missing":
        declaration.unlink()
    else:
        declaration.write_text(json.dumps({"version": version}))
    with pytest.raises(ValueError):
        build.build_plan(inputs)
    assert not Path(inputs.output).exists()


@pytest.mark.parametrize(("change", "source_changed", "static_changed"), [
    ("source_add", True, False), ("module_bytes", True, False), ("main_bytes", True, False),
    ("version_bytes", True, False), ("icon_bytes", True, False), ("catalog_bytes", True, False),
    ("style_bytes", True, False), ("source_rename", True, False),
    ("static_bytes", False, True), ("static_index_bytes", False, True), ("static_rename", False, True),
    ("nested_static", False, True), ("media", False, False), ("relocate", False, False),
])
def test_content_identity_tracks_only_its_own_declared_domain(inputs, change, source_changed, static_changed):
    module = Path(inputs.source) / "src/runtime.py"
    module.write_bytes(b"VALUE = 1\n")
    if change == "nested_static":
        nested = Path(inputs.source) / "frontend-static"
        Path(inputs.static).rename(nested)
        inputs.static = str(nested)
    original = build.build_plan(inputs)
    if change == "source_add":
        (Path(inputs.source) / "src/extra.py").write_bytes(b"VALUE = 2\n")
    elif change == "module_bytes":
        module.write_bytes(b"VALUE = 2\n")  # Same path, count and byte length.
    elif change == "main_bytes":
        main = Path(inputs.source) / "main.py"
        main.write_bytes(main.read_bytes().replace(b"not", b"NOT", 1))
    elif change == "version_bytes":
        (Path(inputs.source) / DESKTOP_CONFIG).write_text(json.dumps({"version": "2.4.0"}))
    elif change == "icon_bytes":
        icon = Path(inputs.source) / BRAND_ICON
        icon.write_bytes(icon.read_bytes().upper())
    elif change == "catalog_bytes":
        (Path(inputs.source) / "config/model_catalog/catalog.meta.yaml").write_bytes(b"version: 2\n")
    elif change == "style_bytes":
        (Path(inputs.source) / "src/apps/comic_gen/style_presets.json").write_bytes(b"{}\n")
    elif change in {"source_rename", "static_rename"}:
        old = module if change == "source_rename" else Path(inputs.static) / "_next/app.js"
        new = old.parent / "renamed" / old.name
        new.parent.mkdir()
        old.rename(new)  # Same leaf name, file count and bytes; only the relative path changes.
    elif change in {"static_bytes", "nested_static"}:
        (Path(inputs.static) / "_next/app.js").write_text("window.example = false;")
    elif change == "static_index_bytes":
        index = Path(inputs.static) / "index.html"
        index.write_bytes(index.read_bytes() + b"<!-- new shell bytes -->")
    elif change == "media":
        Path(inputs.ffmpeg).write_text("different tool bytes")
    else:
        for field in ("source", "static"):
            old = Path(getattr(inputs, field))
            new = old.with_name("relocated " + old.name)
            old.rename(new)
            setattr(inputs, field, str(new))
    changed = build.build_plan(inputs)
    assert (changed["source_input_sha256"] != original["source_input_sha256"]) is source_changed
    assert (changed["static_shell_sha256"] != original["static_shell_sha256"]) is static_changed
    if change in {"source_rename", "static_rename"}:
        assert original["input_sha256"].keys() != changed["input_sha256"].keys()
        assert sorted(original["input_sha256"].values()) == sorted(changed["input_sha256"].values())
    if change == "media":
        assert changed["input_sha256"][inputs.ffmpeg] != original["input_sha256"][inputs.ffmpeg]
    assert not Path(inputs.output).exists()


@pytest.mark.parametrize("stage", ["before_output", "after_probe", "after_builder"])
@pytest.mark.parametrize("domain", ["python", "static", "catalog"])
@pytest.mark.parametrize("operation", ["add", "delete"])
def test_reenumeration_rejects_changed_input_set_before_packaging(inputs, monkeypatch, stage, domain, operation):
    root, relative, content = {
        "python": (Path(inputs.source), "src/extra.py", "VALUE = 1\n"),
        "static": (Path(inputs.static), "_next/extra.js", "window.extra = true;"),
        "catalog": (Path(inputs.source), "config/model_catalog/families/extra.yaml", "models: []\n"),
    }[domain]
    target = root / relative
    if operation == "delete":
        target.write_text(content)
    plan = build.build_plan(inputs)
    calls = _fake_tools(monkeypatch, plan)
    run_tool = build.subprocess.run

    def mutate():
        if operation == "add":
            target.write_text(content)
        else:
            target.unlink()

    def run(command, **kwargs):
        result = run_tool(command, **kwargs)
        if ((stage == "after_probe" and command == [inputs.ffprobe, "-version"])
                or (stage == "after_builder" and "PyInstaller" in command)):
            mutate()
        return result

    monkeypatch.setattr(build.subprocess, "run", run)
    if stage == "before_output":
        mutate()
    with pytest.raises(ValueError):
        build.execute_plan(plan)
    assert len(calls) == {"before_output": 0, "after_probe": 2, "after_builder": 3}[stage]
    assert not Path(plan["app"]).exists() and not Path(plan["dmg"][-1]).exists()
    assert Path(inputs.output).exists() is (stage != "before_output")
    if stage == "after_builder":
        assert (Path(inputs.output) / "dist" / f"{build.APP_NAME}.app").is_dir()


@pytest.mark.parametrize(("unusual_paths", "version"), [(False, "1.2.1"), (True, "1.2.1"), (False, "2.4.0")])
def test_generated_spec_preserves_builder_dataflow_and_writes_identity_before_delivery(inputs, monkeypatch, unusual_paths, version):
    if unusual_paths:
        for field in ("source", "static"):
            old = Path(getattr(inputs, field))
            new = old.with_name(field + " 中文 'quote' \\ backslash")
            old.rename(new)
            setattr(inputs, field, str(new))
    (Path(inputs.source) / DESKTOP_CONFIG).write_text(json.dumps({"version": version}))
    plan = build.build_plan(inputs)
    records = []
    calls = _fake_tools(monkeypatch, plan, spec_records=records)
    build.execute_plan(plan)
    assert len(records) == 1, "The builder must consume the generated spec exactly once"
    nodes, events = records[0]
    packages = ["uvicorn", "fastapi", "starlette", "pydantic"]
    assert events == [("collect_all", name) for name in packages] + [
        ("construct", name) for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")
    ]
    hidden_imports = [
        "src.apps.comic_gen.api", "uvicorn.logging", "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
        "webview", "dashscope", "oss2", "alibabacloud_videoenhan20200320", "alibabacloud_tea_openapi",
        "alibabacloud_tea_util", "yaml", "dotenv", "httptools", "uvloop", "multipart",
    ]
    assert {(row["source"], row["destination"]) for row in plan["data_files"]
            if Path(row["source"]).is_relative_to(inputs.static)} == {
        (str(Path(inputs.static) / "index.html"), "static"),
        (str(Path(inputs.static) / "_next/app.js"), "static/_next"),
    }
    analysis, pyz, exe, coll, bundle = [nodes[name] for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")]
    assert analysis.args == ([str(Path(inputs.source) / "main.py")],)
    assert analysis.kwargs == {
        "pathex": [inputs.source],
        "datas": [(row["source"], row["destination"]) for row in plan["data_files"]]
        + [(f"{name}.data", name) for name in packages],
        "binaries": [(inputs.ffmpeg, "."), (inputs.ffprobe, ".")]
        + [(f"{name}.binary", name) for name in packages],
        "hiddenimports": hidden_imports + [f"{name}.hidden" for name in packages],
        "hookspath": [str(Path(inputs.source) / ".pyinstaller-hooks")],
        "hooksconfig": {}, "runtime_hooks": [], "excludes": [], "noarchive": False, "optimize": 0,
    }
    assert pyz.args == (analysis.pure,) and pyz.kwargs == {}
    assert exe.args == (pyz, analysis.scripts, [])
    assert exe.kwargs == {
        "exclude_binaries": True, "name": "LumenX Studio", "debug": False,
        "bootloader_ignore_signals": False, "strip": False, "upx": True, "console": False,
        "disable_windowed_traceback": False, "argv_emulation": False, "target_arch": None,
        "codesign_identity": None, "entitlements_file": None,
        "icon": [str(Path(inputs.source) / BRAND_ICON)],
    }
    assert coll.args == (exe, analysis.binaries, analysis.datas)
    assert coll.kwargs == {"strip": False, "upx": True, "upx_exclude": [], "name": "LumenX Studio"}
    assert bundle.args == (coll,)
    assert bundle.kwargs == {
        "name": "LumenX Studio.app", "icon": str(Path(inputs.source) / BRAND_ICON),
        "bundle_identifier": "com.lumenx.studio", "version": version,
        "info_plist": {
            "CFBundleVersion": version,
            "LumenXBuild": {"SourceInputSHA256": plan["source_input_sha256"], "StaticShellSHA256": plan["static_shell_sha256"]},
        },
    }
    app = Path(plan["app"])
    assert (app / "Contents/Info.plist").read_bytes() == bundle.plist_bytes
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert info["CFBundleShortVersionString"] == info["CFBundleVersion"] == version
    assert info["LumenXBuild"] == bundle.kwargs["info_plist"]["LumenXBuild"]
    assert info["CFBundleIdentifier"] == "com.lumenx.studio"
    assert (app / "Contents/Resources" / info["CFBundleIconFile"]).read_bytes() == (Path(inputs.source) / BRAND_ICON).read_bytes()
    assert inputs.source not in json.dumps(info) and inputs.static not in json.dumps(info)
    assert len(calls) == 4 and sum("PyInstaller" in command for command, _ in calls) == 1


@pytest.mark.parametrize("fault", ["missing_plist", "missing_identity", "source_digest", "static_digest",
                                  "short_version", "build_version", "bundle_id", "icon_bytes"])
def test_mismatching_built_identity_stays_in_dist_and_never_becomes_a_deliverable(inputs, monkeypatch, fault):
    plan = build.build_plan(inputs)
    calls = _fake_tools(monkeypatch, plan)
    run_tool = build.subprocess.run
    app = Path(inputs.output) / "dist" / f"{build.APP_NAME}.app"

    def run(command, **kwargs):
        result = run_tool(command, **kwargs)
        if "PyInstaller" in command:
            path = app / "Contents/Info.plist"
            if fault == "missing_plist":
                path.unlink(missing_ok=True)
            elif fault == "icon_bytes":
                resources = app / "Contents/Resources"
                resources.mkdir(parents=True, exist_ok=True)
                (resources / "icon.icns").write_bytes(b"different icon bytes")
            else:
                # On the pre-B recipe the old fake has no plist. Only introduce
                # the selected defect; never fabricate a correct expected identity.
                info = plistlib.loads(path.read_bytes()) if path.is_file() else {}
                if fault == "missing_identity":
                    info.pop("LumenXBuild", None)
                elif fault in {"source_digest", "static_digest"}:
                    key = "SourceInputSHA256" if fault == "source_digest" else "StaticShellSHA256"
                    info.setdefault("LumenXBuild", {})[key] = "0" * 64
                else:
                    key = {"short_version": "CFBundleShortVersionString", "build_version": "CFBundleVersion",
                           "bundle_id": "CFBundleIdentifier"}[fault]
                    info[key] = "incorrect"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(plistlib.dumps(info))
        return result

    monkeypatch.setattr(build.subprocess, "run", run)
    with pytest.raises(ValueError):
        build.execute_plan(plan)
    assert len(calls) == 3
    assert app.is_dir() and (Path(inputs.output) / "pyinstaller.log").is_file()
    assert not Path(plan["app"]).exists() and not Path(plan["dmg"][-1]).exists()
