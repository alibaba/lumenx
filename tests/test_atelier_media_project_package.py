from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.apps.atelier.generation_selection import (
    AtelierGenerationSelectionService,
    GenerationCandidateNotFound,
    SelectVideoCandidateCommand,
    SetVideoCandidateVisibilityCommand,
)
from src.apps.atelier.project_package import build_package, prepare_import
from src.apps.atelier.storage import AtelierStorage
from src.apps.atelier.models import AtelierProject
from src.utils.json_storage import StorageError
from tests.frozen_image_fixtures import rgb_png_bytes
from tests.test_atelier_media_generation_ledger import _project_payload


def _media_bytes(media_kind: str) -> bytes:
    return rgb_png_bytes("portable-media") if media_kind == "image" else b"portable-audio"


def _source_project(tmp_path: Path, *, media_kind: str) -> AtelierProject:
    payload = _project_payload(media_kind=media_kind)
    payload["agent_policy"]["updated_at"] = 1.0
    output_ref = payload["nodes"][0]["data"]["candidates"][0]["media_url"]
    path = tmp_path / output_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_media_bytes(media_kind))
    return AtelierProject.model_validate(payload)


@pytest.mark.parametrize(
    ("media_kind", "expected_prefix"),
    [("image", "assets/imported/imported-project/atelier/"), ("audio", "audio/imported/imported-project/atelier/")],
)
def test_media_package_import_rebuilds_generated_candidate_as_inert_imported_copy(tmp_path, media_kind, expected_prefix):
    source = _source_project(tmp_path, media_kind=media_kind)

    artifact = build_package(source, data_dir=tmp_path, source_storage_id="source-storage")
    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-storage",
        id_factory=lambda kind: f"imported-{kind}",
    )

    imported = prepared.project
    node = imported.nodes[0]
    candidate = node.data["candidates"][0]
    assert imported.generation_attempts == []
    assert imported.generation_request_receipts == []
    assert candidate == {
        "schema_version": 2,
        "execution_contract": "atelier-provider-media-v2",
        "id": "imported-candidate",
        "media_kind": media_kind,
        "status": "completed",
        "media_url": candidate["media_url"],
        "error_code": None,
        "authority": "imported",
        "attempt_id": None,
        "execution_fingerprint": None,
    }
    assert candidate["media_url"].startswith(expected_prefix)
    assert (tmp_path / candidate["media_url"]).read_bytes() == _media_bytes(media_kind)

    store = AtelierStorage(tmp_path)
    try:
        saved = store.create_project(imported)
        selected = AtelierGenerationSelectionService(store).select_media_candidate(
            saved.id,
            node.id,
            candidate["id"],
            SelectVideoCandidateCommand(expected_revision=0),
        )
        assert selected.project.generation_attempts == []
        assert selected.project.nodes[0].data["selected_candidate_id"] == candidate["id"]
        assert selected.project.nodes[0].media_urls == [candidate["media_url"]]
    finally:
        store.close()


@pytest.mark.parametrize(
    ("media_kind", "expected_prefix"),
    [("image", "assets/imported/imported-project/atelier/"), ("audio", "audio/imported/imported-project/atelier/")],
)
def test_media_package_import_preserves_hidden_completed_imported_candidates(tmp_path, media_kind, expected_prefix):
    source = _source_project(tmp_path, media_kind=media_kind)
    candidate = source.nodes[0].data["candidates"].pop()
    source.nodes[0].data["deleted_candidates"] = [candidate]

    artifact = build_package(source, data_dir=tmp_path, source_storage_id="source-storage")
    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-storage",
        id_factory=lambda kind: f"imported-{kind}",
    )

    imported = prepared.project
    node = imported.nodes[0]
    assert node.data.get("candidates", []) == []
    hidden = node.data["deleted_candidates"][0]
    assert hidden == {
        "schema_version": 2,
        "execution_contract": "atelier-provider-media-v2",
        "id": "imported-candidate",
        "media_kind": media_kind,
        "status": "completed",
        "media_url": hidden["media_url"],
        "error_code": None,
        "authority": "imported",
        "attempt_id": None,
        "execution_fingerprint": None,
    }
    assert hidden["media_url"].startswith(expected_prefix)
    assert (tmp_path / hidden["media_url"]).read_bytes() == _media_bytes(media_kind)
    assert hidden["media_url"] in prepared.media.values()

    store = AtelierStorage(tmp_path)
    try:
        saved = store.create_project(imported)
        restored = AtelierGenerationSelectionService(store).set_media_candidate_visibility(
            saved.id,
            node.id,
            hidden["id"],
            SetVideoCandidateVisibilityCommand(expected_revision=0, visible=True),
        )
        assert restored.project.nodes[0].data["candidates"] == [hidden]
        assert restored.project.nodes[0].data["deleted_candidates"] == []
        assert restored.project.nodes[0].media_urls == []
    finally:
        store.close()


def test_media_package_imported_wire_fixture_export(tmp_path):
    source = _source_project(tmp_path, media_kind="image")

    artifact = build_package(source, data_dir=tmp_path, source_storage_id="source-storage")
    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-storage",
        now_fn=lambda: 99.0,
        id_factory=lambda kind: f"fixture-{kind}",
    )
    payload = {
        "manifest": artifact.manifest,
        "prepared_project": prepared.project.model_dump(mode="json"),
        "media_map": prepared.media,
        "id_map": prepared.id_map,
    }
    out = tmp_path / "atelier-media-generation-imported.json"
    wire_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    out.write_text(wire_json, encoding="utf-8")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"atelier_media_generation_imported_fixture={out} sha256={digest}")
    candidate = payload["prepared_project"]["nodes"][0]["data"]["candidates"][0]
    assert candidate["execution_contract"] == "atelier-provider-media-v2"
    assert candidate["authority"] == "imported"
    assert candidate["attempt_id"] is None


def test_media_package_imported_wire_fixture_matches_checked_fixture(tmp_path):
    source = _source_project(tmp_path, media_kind="image")

    artifact = build_package(source, data_dir=tmp_path, source_storage_id="source-storage")
    prepared = prepare_import(
        artifact.data,
        data_dir=tmp_path,
        target_storage_id="target-storage",
        now_fn=lambda: 99.0,
        id_factory=lambda kind: f"fixture-{kind}",
    )
    payload = {
        "manifest": artifact.manifest,
        "prepared_project": prepared.project.model_dump(mode="json"),
        "media_map": prepared.media,
        "id_map": prepared.id_map,
    }

    fixture = Path(__file__).parents[1] / "frontend/src/__tests__/fixtures/atelier-media-generation-imported.json"
    assert json.loads(fixture.read_text()) == payload


def test_media_package_imported_candidate_visibility_moves_without_provider_receipts(tmp_path):
    source = _source_project(tmp_path, media_kind="image")
    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-storage").data,
        data_dir=tmp_path,
        target_storage_id="target-storage",
        id_factory=lambda kind: f"imported-{kind}",
    )
    store = AtelierStorage(tmp_path)
    try:
        saved = store.create_project(prepared.project)
        service = AtelierGenerationSelectionService(store)
        node = saved.nodes[0]
        candidate = node.data["candidates"][0]

        hidden = service.set_media_candidate_visibility(
            saved.id,
            node.id,
            candidate["id"],
            SetVideoCandidateVisibilityCommand(expected_revision=0, visible=False),
        )
        assert hidden.project.nodes[0].data["candidates"] == []
        assert hidden.project.nodes[0].data["deleted_candidates"] == [candidate]
        assert hidden.project.generation_attempts == []
        assert hidden.project.generation_request_receipts == []

        restored = service.set_media_candidate_visibility(
            saved.id,
            node.id,
            candidate["id"],
            SetVideoCandidateVisibilityCommand(expected_revision=1, visible=True),
        )
        assert restored.project.nodes[0].data["candidates"] == [candidate]
        assert restored.project.nodes[0].data["deleted_candidates"] == []
    finally:
        store.close()


def test_media_package_hidden_imported_candidate_survives_reexport_as_new_inert_copy(tmp_path):
    source = _source_project(tmp_path, media_kind="image")
    candidate = source.nodes[0].data["candidates"].pop()
    source.nodes[0].data["deleted_candidates"] = [candidate]

    first = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-storage").data,
        data_dir=tmp_path,
        target_storage_id="target-storage",
        id_factory=lambda kind: f"first-{kind}",
    )
    second = prepare_import(
        build_package(first.project, data_dir=tmp_path, source_storage_id="target-storage").data,
        data_dir=tmp_path,
        target_storage_id="target-storage-2",
        id_factory=lambda kind: f"second-{kind}",
    )

    hidden = second.project.nodes[0].data["deleted_candidates"][0]
    assert hidden["id"] == "second-candidate"
    assert hidden["authority"] == "imported"
    assert hidden["attempt_id"] is None
    assert hidden["media_url"].startswith("assets/imported/second-project/")
    assert hidden["media_url"] != first.project.nodes[0].data["deleted_candidates"][0]["media_url"]
    assert (tmp_path / hidden["media_url"]).read_bytes() == _media_bytes("image")


def test_media_package_hidden_failed_or_active_candidate_is_not_promoted_to_completed(tmp_path):
    source = _source_project(tmp_path, media_kind="image")
    candidate = source.nodes[0].data["candidates"].pop()
    candidate["status"] = "failed"
    candidate["error_code"] = "provider_sync_failed"
    source.nodes[0].data["deleted_candidates"] = [candidate]

    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-storage").data,
        data_dir=tmp_path,
        target_storage_id="target-storage",
        id_factory=lambda kind: f"imported-{kind}",
    )

    assert prepared.project.nodes[0].data.get("deleted_candidates", []) == []
    assert prepared.project.nodes[0].data.get("candidates", []) == []
    assert prepared.media == {}


def test_media_package_rejects_candidate_id_collision_between_live_and_hidden_buckets(tmp_path):
    from src.apps.atelier.project_package import AtelierProjectPackageError

    source = _source_project(tmp_path, media_kind="image")
    candidate = dict(source.nodes[0].data["candidates"][0])
    source.nodes[0].data["deleted_candidates"] = [candidate]

    with pytest.raises(AtelierProjectPackageError, match="Duplicate or invalid Atelier candidate id"):
        build_package(source, data_dir=tmp_path, source_storage_id="source-storage")


def test_media_package_rejects_selected_hidden_candidate(tmp_path):
    from src.apps.atelier.project_package import AtelierProjectPackageError

    source = _source_project(tmp_path, media_kind="image")
    candidate = source.nodes[0].data["candidates"].pop()
    source.nodes[0].data["selected_candidate_id"] = candidate["id"]
    source.nodes[0].data["deleted_candidates"] = [candidate]

    with pytest.raises(AtelierProjectPackageError, match="Selected Atelier candidate is missing"):
        build_package(source, data_dir=tmp_path, source_storage_id="source-storage")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda candidate, project_id: candidate.update({"media_url": f"assets/imported/{project_id}/atelier/missing.png"}), "not a safe copied artifact"),
        (lambda candidate, _project_id: candidate.update({"media_url": "assets/imported/other-project/atelier/out.png"}), "not a safe copied artifact"),
        (lambda candidate, project_id: candidate.update({"media_kind": "audio", "media_url": f"audio/imported/{project_id}/atelier/out.mp3"}), "owner node media kind"),
        (lambda candidate, _project_id: candidate.update({"authority": "provider", "attempt_id": "provider-attempt", "execution_fingerprint": "a" * 64, "media_url": "assets/atelier/out.png"}), "generation attempt"),
    ],
)
def test_select_imported_media_candidate_rejects_missing_cross_project_wrong_kind_or_authority(tmp_path, mutate, message):
    source = _source_project(tmp_path, media_kind="image")
    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-storage").data,
        data_dir=tmp_path,
        target_storage_id="target-storage",
        id_factory=lambda kind: f"imported-{kind}",
    )
    project = prepared.project.model_dump(mode="json")
    candidate = project["nodes"][0]["data"]["candidates"][0]
    mutate(candidate, project["id"])
    store = AtelierStorage(tmp_path)
    try:
        with pytest.raises((GenerationCandidateNotFound, StorageError), match=message):
            store.create_project(AtelierProject.model_validate(project))
            service = AtelierGenerationSelectionService(store)
            service.select_media_candidate(
                project["id"],
                project["nodes"][0]["id"],
                candidate["id"],
                SelectVideoCandidateCommand(expected_revision=0),
            )
    finally:
        store.close()


def test_select_imported_media_candidate_rejects_symlinked_imported_file(tmp_path):
    source = _source_project(tmp_path, media_kind="image")
    prepared = prepare_import(
        build_package(source, data_dir=tmp_path, source_storage_id="source-storage").data,
        data_dir=tmp_path,
        target_storage_id="target-storage",
        id_factory=lambda kind: f"imported-{kind}",
    )
    imported = prepared.project
    candidate = imported.nodes[0].data["candidates"][0]
    media_path = tmp_path / candidate["media_url"]
    media_path.unlink()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_media_bytes("image"))
    media_path.symlink_to(outside)
    store = AtelierStorage(tmp_path)
    try:
        saved = store.create_project(imported)
        with pytest.raises(GenerationCandidateNotFound, match="not a safe copied artifact"):
            AtelierGenerationSelectionService(store).select_media_candidate(
                saved.id,
                saved.nodes[0].id,
                candidate["id"],
                SelectVideoCandidateCommand(expected_revision=0),
            )
    finally:
        store.close()
