import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.apps.comic_gen import api
from src.apps.comic_gen.pipeline import ComicGenPipeline
from src.apps.comic_gen.project_package import build_package
from src.utils.json_storage import StorageError
from tests.test_project_package import _resolved_assets, _source_project


def _runtime(tmp_path: Path) -> ComicGenPipeline:
    runtime = ComicGenPipeline(data_dir=tmp_path / "data", recover_orphan_tasks=False)
    return runtime


def _seed_project(runtime: ComicGenPipeline):
    script = _source_project(runtime.data_dir)
    assets = _resolved_assets()
    script.characters = assets.characters
    script.scenes = assets.scenes
    script.props = assets.props
    script.series_id = None
    runtime.scripts[script.id] = script
    runtime._commit_storage("projects.json")
    return runtime.read_committed_script(script.id)


def _app(runtime: ComicGenPipeline):
    return api.create_app(data_dir=runtime.data_dir, load_config=False, pipeline_instance=runtime)


def _export(client: TestClient, script_id: str) -> bytes:
    response = client.get(f"/projects/{script_id}/package/export")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment" in response.headers["content-disposition"]
    assert zipfile.is_zipfile(io.BytesIO(response.content))
    return response.content


def test_http_export_import_roundtrip_and_reimport_are_independent(tmp_path):
    runtime = _runtime(tmp_path)
    source = _seed_project(runtime)
    app = _app(runtime)
    with TestClient(app) as client:
        package = _export(client, source.id)
        first = client.post("/projects/package/import", content=package, headers={"content-type": "application/zip"})
        assert first.status_code == 200, first.text
        second = client.post("/projects/package/import", content=package, headers={"content-type": "application/octet-stream"})
        assert second.status_code == 200, second.text
        first_project = first.json()
        second_project = second.json()
        assert first_project["id"] != source.id
        assert second_project["id"] not in {source.id, first_project["id"]}
        assert first_project["provider_request_receipts"] == []
        assert first_project["video_tasks"][0]["project_id"] == first_project["id"]
        assert client.get(f"/projects/{first_project['id']}").status_code == 200
        reexported = _export(client, first_project["id"])
        assert zipfile.is_zipfile(io.BytesIO(reexported))
        assert runtime.read_committed_script(source.id).model_dump(mode="json") == source.model_dump(mode="json")


def test_pipeline_export_uses_committed_view_not_live_dirty_state(tmp_path):
    runtime = _runtime(tmp_path)
    source = _seed_project(runtime)
    runtime.scripts[source.id].frames[0].image_url = "https://example.invalid/live-only.png"

    package = runtime.export_project_package(source.id).data
    imported = runtime.import_project_package(package)

    assert imported.frames[0].image_url.startswith("storyboard/imported/")
    assert runtime.scripts[source.id].frames[0].image_url == "https://example.invalid/live-only.png"


def test_export_missing_project_returns_404(tmp_path):
    runtime = _runtime(tmp_path)
    app = _app(runtime)
    with TestClient(app) as client:
        response = client.get("/projects/missing/package/export")
    assert response.status_code == 404


def test_import_rejects_invalid_or_missing_media_package_with_typed_422(tmp_path):
    runtime = _runtime(tmp_path)
    source = _seed_project(runtime)
    package = build_package(
        source,
        resolved_assets=_resolved_assets(),
        data_dir=runtime.data_dir,
        source_storage_id=runtime.storage_identity["storage_id"],
        writer_lock=runtime._writer_lock,
    ).data
    buffer = io.BytesIO(package)
    broken = io.BytesIO()
    with zipfile.ZipFile(buffer) as zin, zipfile.ZipFile(broken, "w") as zout:
        for info in zin.infolist():
            if info.filename != "media/video/take.mp4":
                zout.writestr(info.filename, zin.read(info.filename))
    app = _app(runtime)
    with TestClient(app) as client:
        invalid = client.post("/projects/package/import", content=b"not a zip", headers={"content-type": "application/zip"})
        missing = client.post("/projects/package/import", content=broken.getvalue(), headers={"content-type": "application/zip"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "project_package_invalid"
    assert missing.status_code == 422
    assert missing.json()["code"] == "project_package_invalid"


def test_import_rejects_oversize_body_before_pipeline_import(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    called = False

    def unexpected_import(_data):
        nonlocal called
        called = True
        raise AssertionError("import should not run")

    monkeypatch.setattr(runtime, "import_project_package", unexpected_import)
    monkeypatch.setattr(api, "DEFAULT_MAX_PACKAGE_BYTES", 16)
    app = _app(runtime)
    with TestClient(app) as client:
        response = client.post(
            "/projects/package/import",
            content=b"x" * 17,
            headers={"content-type": "application/octet-stream"},
        )
    assert response.status_code == 413
    assert response.json()["code"] == "project_package_too_large"
    assert called is False


def test_import_rejects_unknown_length_chunked_oversize_before_pipeline_import(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    called = False

    def unexpected_import(_data):
        nonlocal called
        called = True
        raise AssertionError("import should not run")

    def chunked_body():
        yield b"x" * 8
        yield b"y" * 9

    monkeypatch.setattr(runtime, "import_project_package", unexpected_import)
    monkeypatch.setattr(api, "DEFAULT_MAX_PACKAGE_BYTES", 16)
    app = _app(runtime)
    with TestClient(app) as client:
        response = client.post(
            "/projects/package/import",
            content=chunked_body(),
            headers={"content-type": "application/octet-stream"},
        )
    assert response.status_code == 413
    assert response.json()["code"] == "project_package_too_large"
    assert called is False


def test_import_rejects_wrong_content_type_before_pipeline_import(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    called = False

    def unexpected_import(_data):
        nonlocal called
        called = True
        raise AssertionError("import should not run")

    monkeypatch.setattr(runtime, "import_project_package", unexpected_import)
    app = _app(runtime)
    with TestClient(app) as client:
        response = client.post(
            "/projects/package/import",
            content=b"not a package",
            headers={"content-type": "text/plain"},
        )
    assert response.status_code == 415
    assert called is False


def test_definite_import_commit_failure_cleans_staged_media_and_leaves_no_project(tmp_path, monkeypatch):
    source_runtime = _runtime(tmp_path / "source-runtime")
    source = _seed_project(source_runtime)
    package = source_runtime.export_project_package(source.id).data
    target = _runtime(tmp_path / "target-runtime")

    def fail_commit(_payloads):
        raise StorageError("synthetic package commit failure", commit_uncertain=False)

    monkeypatch.setattr(target._storage, "commit", fail_commit)
    with pytest.raises(StorageError):
        target.import_project_package(package)
    assert target._committed["projects.json"] == {}
    assert target._storage_payloads(("projects.json",))["projects.json"] == {}
    imported_root = target.data_dir / "video" / "imported"
    assert not imported_root.exists() or not any(path.is_file() for path in imported_root.rglob("*"))



def test_postcommit_response_read_failure_preserves_committed_import_media(tmp_path, monkeypatch):
    source_runtime = _runtime(tmp_path / "source-runtime")
    source = _seed_project(source_runtime)
    package = source_runtime.export_project_package(source.id).data
    target = _runtime(tmp_path / "target-runtime")

    def fail_response_read(_script_id):
        raise RuntimeError("synthetic response read failure")

    monkeypatch.setattr(target, "read_committed_script", fail_response_read)
    with pytest.raises(RuntimeError, match="response read"):
        target.import_project_package(package)

    assert len(target._committed["projects.json"]) == 1
    imported_project_id = next(iter(target._committed["projects.json"]))
    imported_root = target.data_dir / "video" / "imported" / imported_project_id
    assert imported_root.exists()
    assert any(path.is_file() for path in imported_root.rglob("*"))

def test_uncertain_import_commit_failure_preserves_staged_media_and_http_503(tmp_path, monkeypatch):
    source_runtime = _runtime(tmp_path / "source-runtime")
    source = _seed_project(source_runtime)
    package = source_runtime.export_project_package(source.id).data
    target = _runtime(tmp_path / "target-runtime")

    def fail_commit(_payloads):
        raise StorageError("synthetic uncertain package commit failure", commit_uncertain=True)

    monkeypatch.setattr(target._storage, "commit", fail_commit)
    app = _app(target)
    with TestClient(app) as client:
        response = client.post("/projects/package/import", content=package, headers={"content-type": "application/zip"})
    assert response.status_code == 503
    assert response.json()["commit_uncertain"] is True
    imported_root = target.data_dir / "video" / "imported"
    assert imported_root.exists()
    assert any(path.is_file() for path in imported_root.rglob("*"))


def test_export_ignores_unreferenced_shared_assets_but_keeps_referenced_ancestors(tmp_path):
    from src.apps.comic_gen.models import Character, Prop

    runtime = _runtime(tmp_path)
    source = _seed_project(runtime)
    runtime.library_store.characters.extend([
        Character(id="unrelated", name="Unrelated", description="", image_url="assets/missing-unrelated.png"),
        Character(id="parent", name="Parent", description=""),
        Character(id="composition-only", name="Composition", description="", base_character_id="parent"),
    ])
    runtime.library_store.props.append(Prop(id="task-only", name="Task prop", description=""))
    runtime.scripts[source.id].frames[0].composition_data = {"character_ids": ["composition-only"]}
    runtime.scripts[source.id].video_tasks[1].asset_id = "task-only"
    runtime._commit_storage("projects.json", "library_assets.json")

    package = runtime.export_project_package(source.id)
    with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
        import json
        payload = json.loads(archive.read("project.json"))
    characters = payload["resolved_assets"]["characters"]
    assert {asset["id"] for asset in characters} == {"series-hero", "composition-only", "parent"}
    assert {asset["id"] for asset in payload["resolved_assets"]["props"]} == {"task-only"}
    imported = runtime.import_project_package(package.data)
    by_name = {asset.name: asset for asset in imported.characters}
    assert by_name["Composition"].base_character_id == by_name["Parent"].id
    assert imported.frames[0].composition_data["character_ids"] == [by_name["Composition"].id]
    assert imported.video_tasks[1].asset_id == imported.props[0].id
